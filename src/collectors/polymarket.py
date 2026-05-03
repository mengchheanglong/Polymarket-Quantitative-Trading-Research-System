from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from src.http_client import HttpError, JsonHttpClient
from src.models import Asset, Market, OrderBook, OrderLevel, TimingWindow


ASSET_WORDS = {
    Asset.BTC: ("bitcoin", "btc"),
    Asset.ETH: ("ethereum", "eth", "ether"),
}
UPDOWN_RE = re.compile(r"\b(up|down)\b", re.IGNORECASE)


class PolymarketPublicCollector:
    """Reads only public Polymarket discovery and CLOB market-data endpoints."""

    def __init__(
        self,
        gamma_base_url: str,
        clob_base_url: str,
        http: JsonHttpClient | None = None,
    ):
        self.gamma_base_url = gamma_base_url
        self.clob_base_url = clob_base_url
        self.http = http or JsonHttpClient()

    def discover_updown_markets(self, max_duration_minutes: int = 60) -> list[Market]:
        events = self._search_events()
        for slug in _nearby_slug_candidates():
            event = self._event_by_slug(slug)
            if event:
                events[str(event.get("slug") or slug)] = event

        markets: dict[str, Market] = {}
        for event in events.values():
            for market in _event_to_markets(event, max_duration_minutes):
                markets[market.slug] = market
        return sorted(markets.values(), key=lambda market: market.window.end)

    def orderbook(self, token_id: str) -> OrderBook:
        data = self.http.get_json(self.clob_base_url, "/book", {"token_id": token_id})
        return OrderBook(
            token_id=token_id,
            bids=tuple(_parse_levels(data.get("bids") or ())),
            asks=tuple(_parse_levels(data.get("asks") or ())),
            last_trade_price=_to_float(data.get("last_trade_price")),
        )

    def _search_events(self) -> dict[str, dict[str, Any]]:
        events: dict[str, dict[str, Any]] = {}
        for query in ("bitcoin up or down", "ethereum up or down", "btc updown", "eth updown"):
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
                if slug and _looks_like_crypto_updown(event):
                    events[slug] = event

        try:
            data = self.http.get_json(
                self.gamma_base_url,
                "/events",
                {"limit": 100, "active": "true", "closed": "false"},
            )
        except HttpError:
            return events
        for event in data if isinstance(data, list) else data.get("data", []):
            slug = str(event.get("slug") or "")
            if slug and _looks_like_crypto_updown(event):
                events[slug] = event
        return events

    def _event_by_slug(self, slug: str) -> dict[str, Any] | None:
        try:
            event = self.http.get_json(self.gamma_base_url, f"/events/slug/{slug}")
        except HttpError:
            return None
        return event if isinstance(event, dict) and event else None


def _event_to_markets(event: dict[str, Any], max_duration_minutes: int) -> list[Market]:
    asset = _asset_for_event(event)
    if asset is None:
        return []
    output: list[Market] = []
    markets = _json_or_value(event.get("markets"), [])
    for raw_market in markets:
        if not isinstance(raw_market, dict) or not _is_open_orderbook_market(raw_market):
            continue
        title = str(raw_market.get("question") or event.get("title") or "")
        if not UPDOWN_RE.search(title):
            continue
        window = _window_for(event, raw_market)
        if window is None or window.duration_minutes > max_duration_minutes:
            continue
        up_token, down_token = _token_ids(raw_market)
        if not up_token or not down_token:
            continue
        slug = str(raw_market.get("slug") or event.get("slug") or raw_market.get("id"))
        output.append(
            Market(
                market_id=str(raw_market.get("id") or ""),
                slug=slug,
                title=title,
                asset=asset,
                window=window,
                up_token_id=up_token,
                down_token_id=down_token,
                source_url=f"https://polymarket.com/event/{event.get('slug')}",
                is_mock=False,
            )
        )
    return output


def _nearby_slug_candidates(now_epoch: int | None = None) -> Iterable[str]:
    now_epoch = now_epoch or int(time.time())
    for minutes in (5, 15):
        step = minutes * 60
        base = now_epoch - (now_epoch % step)
        for offset in range(-12, 13):
            epoch = base + offset * step
            yield f"btc-updown-{minutes}m-{epoch}"
            yield f"eth-updown-{minutes}m-{epoch}"


def _parse_levels(rows: Iterable[dict[str, Any]]) -> list[OrderLevel]:
    levels: list[OrderLevel] = []
    for row in rows:
        price = _to_float(row.get("price"))
        size = _to_float(row.get("size"))
        if price is not None and size is not None:
            levels.append(OrderLevel(price=price, size=size))
    return levels


def _is_open_orderbook_market(market: dict[str, Any]) -> bool:
    return not (
        market.get("closed") is True
        or market.get("archived") is True
        or market.get("active") is False
        or market.get("enableOrderBook") is False
        or market.get("acceptingOrders") is False
    )


def _looks_like_crypto_updown(event: dict[str, Any]) -> bool:
    text = " ".join(str(event.get(key) or "") for key in ("slug", "title", "description"))
    return _asset_for_event(event) is not None and UPDOWN_RE.search(text) is not None


def _asset_for_event(event: dict[str, Any]) -> Asset | None:
    text = " ".join(str(event.get(key) or "") for key in ("slug", "title", "description"))
    markets = _json_or_value(event.get("markets"), [])
    for market in markets:
        if isinstance(market, dict):
            text += " " + str(market.get("question") or market.get("slug") or "")
    text = text.lower()
    for asset, words in ASSET_WORDS.items():
        if any(word in text for word in words):
            return asset
    return None


def _window_for(event: dict[str, Any], market: dict[str, Any]) -> TimingWindow | None:
    end = _parse_time(
        market.get("endDateIso") or market.get("endDate") or event.get("endDate")
    )
    start = _parse_time(
        market.get("startDateIso") or market.get("startDate") or event.get("startDate")
    )
    if start is None:
        start = _infer_start_from_slug(str(market.get("slug") or event.get("slug") or ""))
    if start is None or end is None or end <= start:
        return None
    return TimingWindow(start=start, end=end)


def _infer_start_from_slug(slug: str) -> datetime | None:
    match = re.search(r"updown-(?:5|15)m-(\d{10})", slug)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)


def _token_ids(market: dict[str, Any]) -> tuple[str | None, str | None]:
    outcomes = _json_or_value(market.get("outcomes"), [])
    token_ids = _json_or_value(market.get("clobTokenIds"), [])
    if len(outcomes) != len(token_ids):
        return None, None
    up_token = None
    down_token = None
    for outcome, token_id in zip(outcomes, token_ids, strict=False):
        label = str(outcome).lower()
        if label in {"up", "yes"}:
            up_token = str(token_id)
        elif label in {"down", "no"}:
            down_token = str(token_id)
    return up_token, down_token


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

