from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from src.config import AgentConfig
from src.models import Asset, Market, OrderBook, TimingWindow
from src.simulator.fees import execution_price, fee_amount
from src.simulator.lifecycle import classify_market_lifecycle
from src.storage.sqlite import SQLiteStore, _from_iso, _iso


@dataclass(frozen=True)
class MarketSelection:
    market: Market
    observed_at: datetime
    lifecycle_status: str
    timing_bucket: str
    seconds_to_expiry: float | None
    duration_type: str
    up_book: OrderBook | None
    down_book: OrderBook | None
    has_complete_orderbooks: bool
    missing_yes_ask: bool
    missing_no_ask: bool
    missing_both_asks: bool
    missing_yes_bid: bool
    missing_no_bid: bool
    spread_values: tuple[float, ...]
    pair_cost: float | None
    pair_cost_after_costs: float | None
    liquidity_size: float


def select_market_snapshots(
    store: SQLiteStore,
    config: AgentConfig,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
    active_only: bool,
    min_seconds_to_expiry: int | None,
    max_seconds_to_expiry: int | None,
) -> list[MarketSelection]:
    rows = _market_history_rows(store, source_filter=source_filter, since=since, until=until, session_id=session_id)
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row["market_slug"]), []).append(row)
    selected: list[MarketSelection] = []
    for slug in sorted(grouped):
        chosen = _choose_market_row(
            store,
            config,
            grouped[slug],
            source_filter=source_filter,
            session_id=session_id,
            active_only=active_only,
            min_seconds_to_expiry=min_seconds_to_expiry,
            max_seconds_to_expiry=max_seconds_to_expiry,
        )
        if chosen is not None:
            selected.append(chosen)
    return selected


def build_active_market_report(
    store: SQLiteStore,
    config: AgentConfig,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
    min_seconds_to_expiry: int | None = None,
    max_seconds_to_expiry: int | None = None,
) -> str:
    selected = select_market_snapshots(
        store,
        config,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        active_only=False,
        min_seconds_to_expiry=min_seconds_to_expiry,
        max_seconds_to_expiry=max_seconds_to_expiry,
    )
    lines = ["Active market report", f"Source filter: {source_filter or 'all'}", f"Session ID: {session_id or 'none'}"]
    if not selected:
        lines.append("No markets found for the requested filters.")
        return "\n".join(lines)
    active = [item for item in selected if item.timing_bucket == "valid_window"]
    btc_active = [item for item in active if item.market.asset.value == "BTC"]
    eth_active = [item for item in active if item.market.asset.value == "ETH"]
    complete = [item for item in active if item.has_complete_orderbooks]
    missing_yes_ask = sum(1 for item in active if item.missing_yes_ask and not item.missing_both_asks)
    missing_no_ask = sum(1 for item in active if item.missing_no_ask and not item.missing_both_asks)
    missing_both_asks = sum(1 for item in active if item.missing_both_asks)
    missing_yes_bid = sum(1 for item in active if item.missing_yes_bid)
    missing_no_bid = sum(1 for item in active if item.missing_no_bid)
    wide_spreads = sum(1 for item in active if any(spread > config.max_spread for spread in item.spread_values))
    low_liquidity = sum(1 for item in active if item.liquidity_size < 10.0)
    duration_counts = {}
    lifecycle_counts = {}
    for item in selected:
        duration_counts[item.duration_type] = duration_counts.get(item.duration_type, 0) + 1
        lifecycle_counts[item.lifecycle_status] = lifecycle_counts.get(item.lifecycle_status, 0) + 1
    first_active = min((item.observed_at for item in active), default=None)
    latest_active = max((item.observed_at for item in active), default=None)
    lines.extend(
        [
            f"Active market count: {len(active)}",
            f"Active BTC markets: {len(btc_active)}",
            f"Active ETH markets: {len(eth_active)}",
            f"Duration counts: {_fmt_map(duration_counts)}",
            f"Lifecycle counts: {_fmt_map(lifecycle_counts)}",
            f"Markets with complete YES/NO orderbooks: {len(complete)}",
            f"Markets missing YES asks: {missing_yes_ask}",
            f"Markets missing NO asks: {missing_no_ask}",
            f"Markets missing both asks: {missing_both_asks}",
            f"Markets missing YES bids: {missing_yes_bid}",
            f"Markets missing NO bids: {missing_no_bid}",
            f"Markets with wide spreads: {wide_spreads}",
            f"Markets with low liquidity: {low_liquidity}",
            f"First active timestamp: {first_active.isoformat() if first_active else 'n/a'}",
            f"Latest active timestamp: {latest_active.isoformat() if latest_active else 'n/a'}",
            f"Median spread: {_fmt_float(_median([spread for item in active for spread in item.spread_values]))}",
            f"Max spread: {_fmt_float(max((spread for item in active for spread in item.spread_values), default=None))}",
            f"Median pair cost: {_fmt_float(_median([item.pair_cost for item in active if item.pair_cost is not None]))}",
            f"Min pair cost: {_fmt_float(min((item.pair_cost for item in active if item.pair_cost is not None), default=None))}",
            f"Pair cost below 1.00: {sum(1 for item in active if item.pair_cost is not None and item.pair_cost < 1.0)}",
            f"Pair cost below threshold: {sum(1 for item in active if item.pair_cost is not None and item.pair_cost < config.pair_cost_threshold)}",
            f"Fee/slippage-adjusted pair cost below threshold: {sum(1 for item in active if item.pair_cost_after_costs is not None and item.pair_cost_after_costs < config.pair_cost_threshold)}",
        ]
    )
    lines.append("Top active markets:")
    for item in active[:10]:
        lines.append(
            " | ".join(
                [
                    item.market.slug,
                    f"asset={item.market.asset.value}",
                    f"observed_at={item.observed_at.isoformat()}",
                    f"seconds_to_expiry={_fmt_float(item.seconds_to_expiry)}",
                    f"spread={_fmt_float(_median(list(item.spread_values)))}",
                    f"pair_cost={_fmt_float(item.pair_cost)}",
                    f"pair_cost_after_costs={_fmt_float(item.pair_cost_after_costs)}",
                    f"complete_orderbooks={item.has_complete_orderbooks}",
                ]
            )
        )
    return "\n".join(lines)


def summarize_liquidity(
    selected: list[MarketSelection],
    config: AgentConfig,
) -> dict[str, Any]:
    pair_costs = [item.pair_cost for item in selected if item.pair_cost is not None]
    pair_costs_after = [item.pair_cost_after_costs for item in selected if item.pair_cost_after_costs is not None]
    spreads = [spread for item in selected for spread in item.spread_values]
    return {
        "missing_yes_ask": sum(1 for item in selected if item.missing_yes_ask and not item.missing_both_asks),
        "missing_no_ask": sum(1 for item in selected if item.missing_no_ask and not item.missing_both_asks),
        "missing_both_asks": sum(1 for item in selected if item.missing_both_asks),
        "missing_yes_bid": sum(1 for item in selected if item.missing_yes_bid),
        "missing_no_bid": sum(1 for item in selected if item.missing_no_bid),
        "median_spread": _median(spreads),
        "max_spread": max(spreads, default=None),
        "median_pair_cost": _median(pair_costs),
        "min_pair_cost": min(pair_costs, default=None),
        "pair_cost_above_1_02": sum(1 for value in pair_costs if value > 1.02),
        "pair_cost_1_00_to_1_02": sum(1 for value in pair_costs if 1.00 <= value <= 1.02),
        "pair_cost_0_99_to_1_00": sum(1 for value in pair_costs if 0.99 <= value < 1.00),
        "pair_cost_below_threshold": sum(1 for value in pair_costs if value < config.pair_cost_threshold),
        "pair_cost_after_costs_below_threshold": sum(1 for value in pair_costs_after if value < config.pair_cost_threshold),
    }


def summarize_timing(selected: list[MarketSelection]) -> dict[str, int]:
    counts = {
        "not_started": 0,
        "active_and_valid": 0,
        "active_but_too_late": 0,
        "expired": 0,
        "missing_expiry": 0,
        "missing_reference_price": 0,
        "missing_current_price": 0,
        "edge_below_threshold": 0,
    }
    for item in selected:
        if item.timing_bucket == "too_early":
            counts["not_started"] += 1
        elif item.timing_bucket == "valid_window":
            counts["active_and_valid"] += 1
        elif item.timing_bucket == "too_late":
            counts["active_but_too_late"] += 1
        elif item.timing_bucket == "expired":
            counts["expired"] += 1
        elif item.timing_bucket == "missing_expiry":
            counts["missing_expiry"] += 1
    return counts


def _choose_market_row(
    store: SQLiteStore,
    config: AgentConfig,
    rows: list[Any],
    *,
    source_filter: str | None,
    session_id: str | None,
    active_only: bool,
    min_seconds_to_expiry: int | None,
    max_seconds_to_expiry: int | None,
) -> MarketSelection | None:
    fallback: MarketSelection | None = None
    for row in rows:
        item = _selection_for_row(store, config, row, source_filter=source_filter, session_id=session_id)
        if fallback is None:
            fallback = item
        if _matches_selection(item, active_only=active_only, min_seconds_to_expiry=min_seconds_to_expiry, max_seconds_to_expiry=max_seconds_to_expiry):
            return item
    if active_only or min_seconds_to_expiry is not None or max_seconds_to_expiry is not None:
        return None
    return fallback


def _matches_selection(
    item: MarketSelection,
    *,
    active_only: bool,
    min_seconds_to_expiry: int | None,
    max_seconds_to_expiry: int | None,
) -> bool:
    if active_only:
        if item.timing_bucket != "valid_window":
            return False
        if not item.has_complete_orderbooks:
            return False
    if item.seconds_to_expiry is None:
        return min_seconds_to_expiry is None and max_seconds_to_expiry is None and not active_only
    if min_seconds_to_expiry is not None and item.seconds_to_expiry < min_seconds_to_expiry:
        return False
    if max_seconds_to_expiry is not None and item.seconds_to_expiry > max_seconds_to_expiry:
        return False
    return True


def _selection_for_row(
    store: SQLiteStore,
    config: AgentConfig,
    row: Any,
    *,
    source_filter: str | None,
    session_id: str | None,
) -> MarketSelection:
    observed_at = _from_iso(str(row["observed_at"]))
    market = Market(
        market_id=str(row["market_id"]),
        slug=str(row["market_slug"]),
        title=str(row["title"]),
        asset=Asset(str(row["asset"])),
        window=TimingWindow(
            start=_from_iso(str(row["window_start"])),
            end=_from_iso(str(row["window_end"])),
        ),
        up_token_id=str(row["up_token_id"]),
        down_token_id=str(row["down_token_id"]),
        source_url=str(row["source_url"]),
        is_mock=bool(row["is_mock"]),
        observed_at=observed_at,
        latest_observed_at=observed_at,
    )
    lifecycle = classify_market_lifecycle(
        market,
        observed_at,
        min_seconds_before_end=config.min_seconds_before_end,
        max_seconds_after_start=config.max_seconds_after_start,
    )
    up_book = store.collected_orderbook(market.up_token_id, source_filter=source_filter, until=observed_at, session_id=session_id)
    down_book = store.collected_orderbook(market.down_token_id, source_filter=source_filter, until=observed_at, session_id=session_id)
    spreads = tuple(
        spread
        for spread in (
            up_book.spread if up_book else None,
            down_book.spread if down_book else None,
        )
        if spread is not None
    )
    up_exec = execution_price(up_book.best_ask, config.slippage_bps) if up_book and up_book.best_ask is not None else None
    down_exec = execution_price(down_book.best_ask, config.slippage_bps) if down_book and down_book.best_ask is not None else None
    pair_cost = (up_book.best_ask + down_book.best_ask) if up_book and up_book.best_ask is not None and down_book and down_book.best_ask is not None else None
    pair_cost_after_costs = None
    if up_exec is not None and down_exec is not None:
        pair_cost_after_costs = up_exec + down_exec
        pair_cost_after_costs += fee_amount(pair_cost_after_costs, config.fee_bps)
    liquidity_size = 0.0
    for book in (up_book, down_book):
        if book is None:
            continue
        liquidity_size += sum(level.size for level in book.bids[:1]) + sum(level.size for level in book.asks[:1])
    return MarketSelection(
        market=market,
        observed_at=observed_at,
        lifecycle_status=lifecycle.status,
        timing_bucket=lifecycle.timing_bucket,
        seconds_to_expiry=lifecycle.seconds_to_expiry,
        duration_type=lifecycle.duration_type,
        up_book=up_book,
        down_book=down_book,
        has_complete_orderbooks=up_book is not None and down_book is not None,
        missing_yes_ask=up_book is None or up_book.best_ask is None,
        missing_no_ask=down_book is None or down_book.best_ask is None,
        missing_both_asks=(up_book is None or up_book.best_ask is None) and (down_book is None or down_book.best_ask is None),
        missing_yes_bid=up_book is None or up_book.best_bid is None,
        missing_no_bid=down_book is None or down_book.best_bid is None,
        spread_values=spreads,
        pair_cost=pair_cost,
        pair_cost_after_costs=pair_cost_after_costs,
        liquidity_size=liquidity_size,
    )


def _market_history_rows(
    store: SQLiteStore,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> list[Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if source_filter == "demo":
        clauses.append("source_name LIKE ?")
        params.append("mock:%")
    elif source_filter == "public":
        clauses.append("source_name NOT LIKE ?")
        params.append("mock:%")
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if since is not None:
        clauses.append("observed_at >= ?")
        params.append(_iso(since))
    if until is not None:
        clauses.append("observed_at <= ?")
        params.append(_iso(until))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.rows(
        f"""
        SELECT session_id, observed_at, source_name, market_slug, market_id, title, asset, window_start,
               window_end, up_token_id, down_token_id, source_url, is_mock
        FROM collected_market_history
        {where}
        ORDER BY observed_at, id
        """,
        tuple(params),
    )
    if rows or session_id is not None:
        return rows
    collected_clauses: list[str] = []
    collected_params: list[Any] = []
    if source_filter == "demo":
        collected_clauses.append("source_name LIKE ?")
        collected_params.append("mock:%")
    elif source_filter == "public":
        collected_clauses.append("source_name NOT LIKE ?")
        collected_params.append("mock:%")
    if since is not None:
        collected_clauses.append("collected_at >= ?")
        collected_params.append(_iso(since))
    if until is not None:
        collected_clauses.append("collected_at <= ?")
        collected_params.append(_iso(until))
    collected_where = f"WHERE {' AND '.join(collected_clauses)}" if collected_clauses else ""
    return store.rows(
        f"""
        SELECT NULL AS session_id, collected_at AS observed_at, source_name, market_slug, market_id, title, asset,
               window_start, window_end, up_token_id, down_token_id, source_url, is_mock
        FROM collected_markets
        {collected_where}
        ORDER BY collected_at, market_slug
        """,
        tuple(collected_params),
    )

def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(median(values))


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_map(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())
