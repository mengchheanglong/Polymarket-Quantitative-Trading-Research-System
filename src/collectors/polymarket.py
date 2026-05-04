from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from src.http_client import HttpError, JsonHttpClient
from src.models import Asset, DiscoveredMarket, Market, MarketClassification, OrderBook, OrderLevel, TimingWindow


ASSET_PATTERNS = {
    Asset.BTC: (re.compile(r"\bbitcoin\b", re.IGNORECASE), re.compile(r"\bbtc\b", re.IGNORECASE)),
    Asset.ETH: (
        re.compile(r"\bethereum\b", re.IGNORECASE),
        re.compile(r"\beth\b", re.IGNORECASE),
        re.compile(r"\bether\b", re.IGNORECASE),
    ),
}
UP_RE = re.compile(r"\b(up)\b", re.IGNORECASE)
DOWN_RE = re.compile(r"\b(down)\b", re.IGNORECASE)
HIGHER_RE = re.compile(r"\b(higher)\b", re.IGNORECASE)
LOWER_RE = re.compile(r"\b(lower)\b", re.IGNORECASE)


class PolymarketPublicCollector:
    """Reads only public Polymarket discovery and public CLOB market-data endpoints."""

    def __init__(
        self,
        gamma_base_url: str,
        clob_base_url: str,
        http: JsonHttpClient | None = None,
    ):
        self.gamma_base_url = gamma_base_url
        self.clob_base_url = clob_base_url
        self.http = http or JsonHttpClient()

    def discover_market_candidates(
        self,
        asset_filter: Asset | None = None,
        max_duration_minutes: int = 60,
    ) -> list[DiscoveredMarket]:
        candidates: dict[str, DiscoveredMarket] = {}
        for event in self._search_events(asset_filter):
            for market in _iter_event_markets(event):
                candidate = _candidate_from_market_payload(
                    event=event,
                    market=market,
                    max_duration_minutes=max_duration_minutes,
                )
                if candidate.classification not in (
                    MarketClassification.CRYPTO_UP_DOWN,
                    MarketClassification.CRYPTO_HIGHER_LOWER,
                ):
                    continue
                if asset_filter and candidate.asset_label != asset_filter.value:
                    continue
                _keep_best_candidate(candidates, candidate)
        for market in self._search_markets(asset_filter):
            candidate = _candidate_from_market_payload(
                event=None,
                market=market,
                max_duration_minutes=max_duration_minutes,
            )
            if candidate.classification not in (
                MarketClassification.CRYPTO_UP_DOWN,
                MarketClassification.CRYPTO_HIGHER_LOWER,
            ):
                continue
            if asset_filter and candidate.asset_label != asset_filter.value:
                continue
            _keep_best_candidate(candidates, candidate)
        return sorted(candidates.values(), key=lambda item: (item.window_end or datetime.max.replace(tzinfo=timezone.utc), item.slug))

    def capture_orderbooks(
        self,
        candidates: list[DiscoveredMarket],
    ) -> tuple[list[DiscoveredMarket], dict[str, OrderBook]]:
        requested = [
            token_id
            for candidate in candidates
            for token_id in (candidate.up_token_id, candidate.down_token_id)
            if token_id
        ]
        books, failures = self.batch_orderbooks(requested)
        output: list[DiscoveredMarket] = []
        for candidate in candidates:
            reasons = list(candidate.classification_reasons)
            if candidate.token_status == "MISSING":
                output.append(
                    _replace_candidate(
                        candidate,
                        orderbook_status="SKIPPED_NO_TOKEN_IDS",
                        reason=f"{candidate.reason}; no public token ids",
                    )
                )
                continue
            missing = [
                token_id
                for token_id in (candidate.up_token_id, candidate.down_token_id)
                if token_id and token_id not in books
            ]
            if missing:
                failure_reason = failures.get(missing[0], "empty public orderbook response")
                output.append(
                    _replace_candidate(
                        candidate,
                        orderbook_status="FAILED",
                        reason=f"{candidate.reason}; orderbook unavailable: {failure_reason}",
                    )
                )
                continue
            reasons.append("has public orderbook")
            output.append(
                _replace_candidate(
                    candidate,
                    classification_reasons=tuple(reasons),
                    orderbook_status="FOUND",
                    reason=f"{candidate.reason}; public orderbook captured",
                )
            )
        return output, books

    def discover_updown_markets(self, max_duration_minutes: int = 60) -> list[Market]:
        candidates = self.discover_market_candidates(max_duration_minutes=max_duration_minutes)
        output: list[Market] = []
        for candidate in candidates:
            market = _candidate_to_market(candidate)
            if market is not None:
                output.append(market)
        return sorted(output, key=lambda market: market.window.end)

    def batch_orderbooks(self, token_ids: Iterable[str]) -> tuple[dict[str, OrderBook], dict[str, str]]:
        requested = [token_id for token_id in dict.fromkeys(token_ids) if token_id]
        if not requested:
            return {}, {}
        books: dict[str, OrderBook] = {}
        failures: dict[str, str] = {}
        try:
            rows = self.http.post_json(
                self.clob_base_url,
                "/books",
                [{"token_id": token_id} for token_id in requested],
            )
        except HttpError as exc:
            for token_id in requested:
                failures[token_id] = str(exc)
            return {}, failures
        for row in rows if isinstance(rows, list) else []:
            book = _parse_book(row)
            if book is not None:
                books[book.token_id] = book
        for token_id in requested:
            if token_id in books:
                continue
            try:
                row = self.http.get_json(self.clob_base_url, "/book", {"token_id": token_id})
            except HttpError as exc:
                failures[token_id] = str(exc)
                continue
            book = _parse_book(row)
            if book is None:
                failures[token_id] = "empty public orderbook response"
                continue
            books[book.token_id] = book
        return books, failures

    def orderbook(self, token_id: str) -> OrderBook:
        books, failures = self.batch_orderbooks([token_id])
        if token_id in books:
            return books[token_id]
        raise HttpError(failures.get(token_id, f"no public orderbook for token {token_id}"))

    def _search_events(self, asset_filter: Asset | None) -> list[dict[str, Any]]:
        events: dict[str, dict[str, Any]] = {}
        for query in _search_queries(asset_filter):
            try:
                data = self.http.get_json(
                    self.gamma_base_url,
                    "/public-search",
                    {
                        "q": query,
                        "limit_per_type": 25,
                        "events_status": "open",
                        "keep_closed_markets": 0,
                        "search_profiles": "false",
                        "search_tags": "false",
                    },
                )
            except HttpError:
                continue
            for event in data.get("events") or []:
                slug = str(event.get("slug") or "")
                if slug and event.get("closed") is not True:
                    events[slug] = event
        try:
            data = self.http.get_json(
                self.gamma_base_url,
                "/events",
                {"limit": 250, "active": "true", "closed": "false"},
            )
        except HttpError:
            data = []
        for event in data if isinstance(data, list) else data.get("data", []):
            slug = str(event.get("slug") or "")
            if slug and event.get("closed") is not True:
                events[slug] = event
        for slug in _nearby_slug_candidates():
            event = self._event_by_slug(slug)
            if event:
                events[str(event.get("slug") or slug)] = event
        return list(events.values())

    def _search_markets(self, asset_filter: Asset | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            data = self.http.get_json(
                self.gamma_base_url,
                "/markets",
                {"limit": 500, "active": "true", "closed": "false"},
            )
        except HttpError:
            return rows
        for market in data if isinstance(data, list) else data.get("data", []):
            if asset_filter and _detect_asset(_market_text(market)) != asset_filter:
                continue
            rows.append(market)
        return rows

    def _event_by_slug(self, slug: str) -> dict[str, Any] | None:
        try:
            event = self.http.get_json(self.gamma_base_url, f"/events/slug/{slug}")
        except HttpError:
            return None
        if not isinstance(event, dict) or not event or event.get("closed") is True:
            return None
        return event


def _candidate_from_market_payload(
    event: dict[str, Any] | None,
    market: dict[str, Any],
    max_duration_minutes: int,
) -> DiscoveredMarket:
    market_id = str(market.get("id") or "")
    slug = str(market.get("slug") or (event or {}).get("slug") or market_id)
    title = str(market.get("question") or (event or {}).get("title") or slug)
    combined_text = " ".join(filter(None, [_market_text(event or {}), _market_text(market)]))
    asset = _detect_asset(combined_text)
    asset_label = asset.value if asset else "unknown"
    classification, reasons = _classify_market(combined_text, market, asset)
    window = _window_for(event or {}, market)
    token_ids = _token_ids(market)
    token_status = "FOUND" if token_ids[0] and token_ids[1] else "MISSING"
    reasons = list(reasons)
    if window and window.duration_minutes <= max_duration_minutes:
        reasons.append("short duration")
    elif window:
        reasons.append("long duration")
    else:
        reasons.append("missing timing window")
    if token_status == "FOUND":
        reasons.append("has token ids")
    else:
        reasons.append("missing token ids")
    accepted, reason = _acceptance_decision(
        classification=classification,
        asset=asset,
        market=market,
        window=window,
        max_duration_minutes=max_duration_minutes,
        token_status=token_status,
    )
    return DiscoveredMarket(
        market_id=market_id,
        slug=slug,
        title=title,
        asset_label=asset_label,
        classification=classification,
        classification_reasons=tuple(reasons),
        token_status=token_status,
        orderbook_status="PENDING" if token_status == "FOUND" else "SKIPPED_NO_TOKEN_IDS",
        active=bool((event or {}).get("active", market.get("active", False))),
        closed=bool((event or {}).get("closed", market.get("closed", False))),
        accepting_orders=_to_bool(market.get("acceptingOrders") if "acceptingOrders" in market else market.get("accepting_orders")),
        up_token_id=token_ids[0],
        down_token_id=token_ids[1],
        condition_id=_first_text(market.get("conditionId"), market.get("condition_id")),
        window_start=window.start if window else None,
        window_end=window.end if window else None,
        source_url=f"https://polymarket.com/event/{(event or {}).get('slug') or slug}",
        accepted=accepted,
        reason=reason,
    )


def _classify_market(
    text: str,
    market: dict[str, Any],
    asset: Asset | None,
) -> tuple[MarketClassification, list[str]]:
    lowered = text.lower()
    reasons: list[str] = []
    if asset is not None:
        reasons.append(f"contains {asset.value}")
    if UP_RE.search(lowered) and DOWN_RE.search(lowered):
        reasons.append("contains up/down language")
        return MarketClassification.CRYPTO_UP_DOWN if asset is not None else MarketClassification.UNKNOWN, reasons
    if HIGHER_RE.search(lowered) and LOWER_RE.search(lowered):
        reasons.append("contains higher/lower language")
        return MarketClassification.CRYPTO_HIGHER_LOWER if asset is not None else MarketClassification.UNKNOWN, reasons
    if asset is not None:
        reasons.append("contains crypto asset only")
        return MarketClassification.NON_CRYPTO, reasons
    reasons.append("asset or direction language missing")
    return MarketClassification.UNKNOWN, reasons


def _acceptance_decision(
    classification: MarketClassification,
    asset: Asset | None,
    market: dict[str, Any],
    window: TimingWindow | None,
    max_duration_minutes: int,
    token_status: str,
) -> tuple[bool, str]:
    if classification not in (
        MarketClassification.CRYPTO_UP_DOWN,
        MarketClassification.CRYPTO_HIGHER_LOWER,
    ):
        return False, f"rejected: classification={classification.value}"
    if asset is None:
        return False, "rejected: asset unknown"
    if window is None:
        return False, "rejected: missing timing window"
    if window.duration_minutes > max_duration_minutes:
        return False, f"rejected: duration {window.duration_minutes:.1f}m exceeds limit"
    if market.get("closed") is True:
        return False, "rejected: market closed"
    if market.get("archived") is True:
        return False, "rejected: market archived"
    if market.get("active") is False:
        return False, "rejected: market inactive"
    if token_status != "FOUND":
        return False, "rejected: missing yes/no token ids"
    return True, "accepted: directional crypto market with token ids"


def _candidate_to_market(candidate: DiscoveredMarket) -> Market | None:
    if (
        not candidate.accepted
        or candidate.up_token_id is None
        or candidate.down_token_id is None
        or candidate.window_start is None
        or candidate.window_end is None
        or candidate.asset_label not in {"BTC", "ETH"}
    ):
        return None
    return Market(
        market_id=candidate.market_id,
        slug=candidate.slug,
        title=candidate.title,
        asset=Asset(candidate.asset_label),
        window=TimingWindow(start=candidate.window_start, end=candidate.window_end),
        up_token_id=candidate.up_token_id,
        down_token_id=candidate.down_token_id,
        source_url=candidate.source_url,
        is_mock=False,
    )


def _iter_event_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [market for market in _json_or_value(event.get("markets"), []) if isinstance(market, dict)]


def _search_queries(asset_filter: Asset | None) -> list[str]:
    if asset_filter == Asset.BTC:
        assets = ("bitcoin", "btc")
    elif asset_filter == Asset.ETH:
        assets = ("ethereum", "eth")
    else:
        assets = ("bitcoin", "btc", "ethereum", "eth")
    suffixes = ("up or down", "higher or lower", "updown")
    return [f"{asset} {suffix}".strip() for asset in assets for suffix in suffixes]


def _nearby_slug_candidates(now_epoch: int | None = None) -> Iterable[str]:
    now_epoch = now_epoch or int(time.time())
    for minutes in (5, 15):
        step = minutes * 60
        base = now_epoch - (now_epoch % step)
        for offset in range(-12, 13):
            epoch = base + offset * step
            yield f"btc-updown-{minutes}m-{epoch}"
            yield f"eth-updown-{minutes}m-{epoch}"


def _window_for(event: dict[str, Any], market: dict[str, Any]) -> TimingWindow | None:
    slug = str(market.get("slug") or event.get("slug") or "")
    end = _parse_time(
        _best_time_value(
            market.get("endDate"),
            market.get("endDateIso"),
            market.get("end_date_iso"),
            event.get("endDate"),
            event.get("end_date_iso"),
        )
    )
    start = _parse_time(
        _best_time_value(
            market.get("startDate"),
            market.get("startDateIso"),
            market.get("start_date_iso"),
            event.get("startDate"),
            event.get("start_date_iso"),
        )
    )
    inferred_start = _infer_start_from_slug(slug)
    if inferred_start is not None and end is not None:
        inferred_minutes = (end - inferred_start).total_seconds() / 60.0
        if 0 < inferred_minutes <= 60:
            start = inferred_start
    if start is None:
        start = inferred_start
    if start is None or end is None or end <= start:
        return None
    return TimingWindow(start=start, end=end)


def _infer_start_from_slug(slug: str) -> datetime | None:
    match = re.search(r"updown-(?:5|15)m-(\d{10})", slug)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)


def _token_ids(market: dict[str, Any]) -> tuple[str | None, str | None]:
    tokens = _json_or_value(market.get("tokens"), [])
    if isinstance(tokens, list):
        pair = _token_ids_from_token_objects(tokens)
        if pair[0] and pair[1]:
            return pair
    outcomes = _json_or_value(market.get("outcomes"), [])
    token_ids = _json_or_value(market.get("clobTokenIds"), [])
    if len(outcomes) == len(token_ids) and outcomes:
        return _token_ids_from_parallel_lists(outcomes, token_ids)
    if isinstance(outcomes, list):
        pair = _token_ids_from_token_objects(outcomes)
        if pair[0] and pair[1]:
            return pair
    return None, None


def _token_ids_from_parallel_lists(outcomes: list[Any], token_ids: list[Any]) -> tuple[str | None, str | None]:
    up_token = None
    down_token = None
    for outcome, token_id in zip(outcomes, token_ids, strict=False):
        direction = _direction_label(str(outcome))
        if direction == "UP":
            up_token = str(token_id)
        elif direction == "DOWN":
            down_token = str(token_id)
    return up_token, down_token


def _token_ids_from_token_objects(items: list[Any]) -> tuple[str | None, str | None]:
    up_token = None
    down_token = None
    for item in items:
        if not isinstance(item, dict):
            continue
        label = _first_text(item.get("outcome"), item.get("name"), item.get("label"))
        token_id = _first_text(item.get("token_id"), item.get("tokenId"), item.get("asset_id"), item.get("id"))
        if not label or not token_id:
            continue
        direction = _direction_label(label)
        if direction == "UP":
            up_token = token_id
        elif direction == "DOWN":
            down_token = token_id
    return up_token, down_token


def _direction_label(label: str) -> str | None:
    lowered = label.strip().lower()
    if lowered in {"up", "yes", "higher"}:
        return "UP"
    if lowered in {"down", "no", "lower"}:
        return "DOWN"
    return None


def _parse_book(payload: Any) -> OrderBook | None:
    if not isinstance(payload, dict):
        return None
    token_id = _first_text(payload.get("asset_id"), payload.get("token_id"))
    if not token_id:
        return None
    return OrderBook(
        token_id=token_id,
        bids=tuple(_parse_levels(payload.get("bids") or ())),
        asks=tuple(_parse_levels(payload.get("asks") or ())),
        last_trade_price=_to_float(payload.get("last_trade_price")),
    )


def _parse_levels(rows: Iterable[dict[str, Any]]) -> list[OrderLevel]:
    levels: list[OrderLevel] = []
    for row in rows:
        price = _to_float(row.get("price"))
        size = _to_float(row.get("size"))
        if price is not None and size is not None:
            levels.append(OrderLevel(price=price, size=size))
    return levels


def _market_text(payload: dict[str, Any]) -> str:
    text_parts = [
        str(payload.get("slug") or ""),
        str(payload.get("title") or ""),
        str(payload.get("question") or ""),
        str(payload.get("description") or ""),
        str(payload.get("ticker") or ""),
    ]
    return " ".join(part for part in text_parts if part)


def _detect_asset(text: str) -> Asset | None:
    for asset, patterns in ASSET_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            return asset
    return None


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _json_or_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None or value == "":
            continue
        return str(value)
    return None


def _best_time_value(*values: Any) -> Any:
    precise = [value for value in values if isinstance(value, str) and "T" in value]
    if precise:
        return precise[0]
    return _first_text(*values)


def _replace_candidate(
    candidate: DiscoveredMarket,
    classification_reasons: tuple[str, ...] | None = None,
    orderbook_status: str | None = None,
    reason: str | None = None,
) -> DiscoveredMarket:
    return DiscoveredMarket(
        market_id=candidate.market_id,
        slug=candidate.slug,
        title=candidate.title,
        asset_label=candidate.asset_label,
        classification=candidate.classification,
        classification_reasons=classification_reasons or candidate.classification_reasons,
        token_status=candidate.token_status,
        orderbook_status=orderbook_status or candidate.orderbook_status,
        active=candidate.active,
        closed=candidate.closed,
        accepting_orders=candidate.accepting_orders,
        up_token_id=candidate.up_token_id,
        down_token_id=candidate.down_token_id,
        condition_id=candidate.condition_id,
        window_start=candidate.window_start,
        window_end=candidate.window_end,
        source_url=candidate.source_url,
        accepted=candidate.accepted,
        reason=reason or candidate.reason,
    )


def _keep_best_candidate(candidates: dict[str, DiscoveredMarket], candidate: DiscoveredMarket) -> None:
    current = candidates.get(candidate.slug)
    if current is None:
        candidates[candidate.slug] = candidate
        return
    current_score = _candidate_score(current)
    candidate_score = _candidate_score(candidate)
    if candidate_score > current_score:
        candidates[candidate.slug] = candidate


def _candidate_score(candidate: DiscoveredMarket) -> tuple[int, int, int, int]:
    return (
        int(candidate.accepted),
        int(candidate.token_status == "FOUND"),
        int(candidate.window_end is not None),
        len(candidate.classification_reasons),
    )
