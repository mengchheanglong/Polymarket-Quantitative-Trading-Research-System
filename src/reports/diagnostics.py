from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from datetime import datetime
from typing import Any

from src.config import AgentConfig
from src.reports.active_markets import select_market_snapshots, summarize_liquidity, summarize_timing
from src.reports.config_view import format_config_view, merged_config_view, parse_config_notes
from src.simulator.engine import RISK_BLOCK_REASONS
from src.storage.sqlite import SQLiteStore


def build_diagnostics(
    store: SQLiteStore,
    config: AgentConfig,
    run_id: str | None = None,
    strategy: str | None = None,
    source_filter: str | None = None,
    session_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    active_only: bool = False,
    tiny_only: bool = False,
    min_seconds_to_expiry: int | None = None,
    max_seconds_to_expiry: int | None = None,
) -> str:
    run_rows = _matching_run_rows(
        store,
        run_id=run_id,
        strategy=strategy,
        source_filter=source_filter,
        session_id=session_id,
        active_only=active_only,
        tiny_only=tiny_only,
    )
    scope = _scope_label(run_rows, run_id=run_id, strategy=strategy, source_filter=source_filter, session_id=session_id)
    lines = ["Strategy diagnostics", f"Scope: {scope}"]
    if not run_rows:
        lines.append("No matching runs found.")
        return "\n".join(lines)

    lines.append(f"Runs matched: {len(run_rows)}")
    strategies = sorted({str(row['strategy']) for row in run_rows})
    lines.append(f"Strategies: {', '.join(strategies)}")
    lines.append(f"Config: {format_config_view(merged_config_view([row['notes'] for row in run_rows]))}")

    market_selection = select_market_snapshots(
        store,
        config,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        active_only=active_only,
        min_seconds_to_expiry=min_seconds_to_expiry,
        max_seconds_to_expiry=max_seconds_to_expiry,
    )
    overall = _aggregate_for_runs(store, run_rows, session_id=session_id, market_selection=market_selection, config=config)
    lines.extend(_section_lines("Overall", overall))

    if len(strategies) > 1:
        lines.append("By strategy:")
        for item in strategies:
            strategy_rows = [row for row in run_rows if str(row["strategy"]) == item]
            lines.extend(
                _section_lines(
                    item,
                    _aggregate_for_runs(store, strategy_rows, session_id=session_id, market_selection=market_selection, config=config),
                    indent="  ",
                )
            )

    lines.append("Market-level diagnostics:")
    market_rows = overall["market_rows"]
    if not market_rows:
        lines.append("none")
    else:
        for row in market_rows[:10]:
            lines.append(
                " | ".join(
                    [
                        str(row["market_slug"]),
                        f"asset={row['asset']}",
                        f"opportunities={row['opportunities']}",
                        f"accepted_trades={row['accepted_trades']}",
                        f"skipped={row['skipped']}",
                        f"avg_spread={_fmt_float(row['avg_spread'])}",
                        f"avg_edge={_fmt_float(row['avg_edge'])}",
                        f"orderbook={row['orderbook_status']}",
                        f"first_seen={row['first_seen']}",
                        f"latest_seen={row['latest_seen']}",
                    ]
                )
            )
    return "\n".join(lines)


def _aggregate_for_runs(
    store: SQLiteStore,
    run_rows: list[Any],
    session_id: str | None = None,
    market_selection: list[Any] | None = None,
    config: AgentConfig | None = None,
) -> dict[str, Any]:
    run_ids = [str(row["run_id"]) for row in run_rows]
    placeholders = ",".join("?" for _ in run_ids)
    opportunity_rows = store.rows(
        f"""
        SELECT o.*, r.strategy, r.data_source, r.notes
        FROM opportunities o
        JOIN runs r ON r.run_id = o.run_id
        WHERE o.run_id IN ({placeholders})
        ORDER BY o.observed_at, o.id
        """,
        tuple(run_ids),
    )
    trade_rows = store.rows(
        f"""
        SELECT t.*, r.strategy, r.data_source
        FROM trades t
        JOIN runs r ON r.run_id = t.run_id
        WHERE t.run_id IN ({placeholders})
        ORDER BY t.opened_at, t.trade_id
        """,
        tuple(run_ids),
    )
    market_map = {
        row["slug"]: row
        for row in store.market_audit_rows(source_filter=_single_value(run_rows, "data_source"), session_id=session_id)
    }
    skip_rows = [row for row in opportunity_rows if str(row["decision"]) == "SKIP"]
    accepted_rows = [row for row in opportunity_rows if str(row["decision"]) == "TRADE"]
    edge_values = [float(row["edge"] or 0.0) for row in opportunity_rows]
    accepted_edge_values = [float(row["edge"] or 0.0) for row in accepted_rows]
    skipped_edge_values = [float(row["edge"] or 0.0) for row in skip_rows]
    spread_values = [float(row["spread"]) for row in opportunity_rows if row["spread"] is not None]
    seconds_to_expiry_values = [float(row["seconds_to_expiry"]) for row in opportunity_rows if row["seconds_to_expiry"] is not None]
    pair_cost_values = [
        float(row["market_price"])
        for row in opportunity_rows
        if str(row["strategy"]) == "pair-cost" and row["market_price"] is not None
    ]
    skipped_by_reason = Counter(str(row["reason"]) for row in skip_rows)
    timing_buckets = Counter(str(row["timing_bucket"] or "unknown") for row in opportunity_rows)
    markets_with_skips = Counter(str(row["market_slug"]) for row in skip_rows)
    run_configs = {str(row["run_id"]): parse_config_notes(row["notes"]) for row in run_rows}
    near_threshold = sum(1 for row in opportunity_rows if _is_near_threshold(row, run_configs.get(str(row["run_id"]), {})))
    market_rows = _market_level_rows(opportunity_rows, trade_rows, market_map)
    liquidity = summarize_liquidity(market_selection or [], config) if config is not None else {}
    timing = summarize_timing(market_selection or [])
    timing["edge_below_threshold"] = sum(
        1 for row in opportunity_rows if str(row["strategy"]) == "momentum" and str(row["reason"]) == "edge below threshold"
    )
    timing["missing_reference_price"] = sum(
        1 for row in opportunity_rows if str(row["strategy"]) == "momentum" and str(row["reason"]) == "missing underlying price"
    )
    timing["missing_current_price"] = sum(
        1 for row in opportunity_rows if str(row["strategy"]) == "momentum" and str(row["reason"]) in {"missing market price", "public orderbook unavailable"}
    )
    strategy_skips = {reason: count for reason, count in skipped_by_reason.items() if reason not in RISK_BLOCK_REASONS}
    risk_skips = {reason: count for reason, count in skipped_by_reason.items() if reason in RISK_BLOCK_REASONS}
    trade_sizes = [float(row["total_cost"] or 0.0) for row in trade_rows]
    stuck_rows = [row for row in opportunity_rows if str(row["strategy"]) == "stuck-markov"]
    stuck_bucket_distribution = Counter(str(row["state_bucket"]) for row in stuck_rows if row["state_bucket"])
    stuck_cycles_values = [int(row["stuck_cycles"]) for row in stuck_rows if row["stuck_cycles"] is not None]
    transition_probabilities = [float(row["transition_probability"]) for row in stuck_rows if row["transition_probability"] is not None]
    closed_stuck_trades = [
        row for row in trade_rows if str(row["status"]).startswith("CLOSED") and row["state_bucket"] is not None
    ]
    result_by_bucket = Counter(str(row["state_bucket"]) for row in closed_stuck_trades)
    result_by_seconds_bucket = Counter(_seconds_bucket(_seconds_to_expiry_for_trade(row)) for row in closed_stuck_trades)
    result_by_entry_price_bucket = Counter(_entry_price_bucket(float(row["entry_price"])) for row in closed_stuck_trades)
    equity_rows = store.rows(
        f"""
        SELECT observed_at, total_equity, position_exposure
        FROM equity_snapshots
        WHERE run_id IN ({placeholders})
        ORDER BY observed_at, id
        """,
        tuple(run_ids),
    )
    exposure_values = [float(row["position_exposure"] or 0.0) for row in equity_rows]
    trades_per_market = Counter(str(row["market_slug"]) for row in trade_rows)
    accepted_by_side = Counter(str(row["direction"]) for row in trade_rows)
    accepted_by_asset = Counter(str(row["asset"]) for row in trade_rows)
    accepted_by_duration = Counter(_duration_label(row) for row in trade_rows)
    accepted_by_seconds_bucket = Counter(_seconds_bucket(_seconds_to_expiry_for_trade(row)) for row in trade_rows)
    accepted_by_entry_price_bucket = Counter(_entry_price_bucket(float(row["entry_price"])) for row in trade_rows)
    return {
        "config": format_config_view(merged_config_view([row["notes"] for row in run_rows])),
        "total_opportunities": len(opportunity_rows),
        "accepted_trades": len(trade_rows),
        "skipped_opportunities": len(skip_rows),
        "skipped_by_reason": dict(skipped_by_reason),
        "strategy_skips": strategy_skips,
        "risk_skips": risk_skips,
        "risk_blocked_trades": sum(risk_skips.values()),
        "average_edge": _safe_avg(edge_values),
        "average_edge_accepted": _safe_avg(accepted_edge_values),
        "average_edge_skipped": _safe_avg(skipped_edge_values),
        "accepted_edge_min": min(accepted_edge_values, default=None),
        "accepted_edge_median": median(accepted_edge_values) if accepted_edge_values else None,
        "accepted_edge_max": max(accepted_edge_values, default=None),
        "min_edge": min(edge_values, default=None),
        "max_edge": max(edge_values, default=None),
        "median_edge": median(edge_values) if edge_values else None,
        "average_spread": _safe_avg(spread_values),
        "min_spread": min(spread_values, default=None),
        "max_spread": max(spread_values, default=None),
        "average_pair_cost": _safe_avg(pair_cost_values),
        "min_pair_cost": min(pair_cost_values, default=None),
        "max_pair_cost": max(pair_cost_values, default=None),
        "average_seconds_to_expiry": _safe_avg(seconds_to_expiry_values),
        "min_seconds_to_expiry": min(seconds_to_expiry_values, default=None),
        "max_seconds_to_expiry": max(seconds_to_expiry_values, default=None),
        "timing_buckets": dict(timing_buckets),
        "liquidity": liquidity,
        "momentum_timing": timing,
        "exposure_avg": _safe_avg(exposure_values),
        "exposure_max": max(exposure_values, default=None),
        "max_simultaneous_positions": _max_simultaneous_positions(trade_rows),
        "largest_single_trade": max(trade_sizes, default=None),
        "average_trade_size": _safe_avg(trade_sizes),
        "trades_per_market_avg": _safe_avg([float(value) for value in trades_per_market.values()]),
        "trades_per_market_max": max(trades_per_market.values(), default=0),
        "accepted_by_side": dict(accepted_by_side),
        "accepted_by_asset": dict(accepted_by_asset),
        "accepted_by_duration": dict(accepted_by_duration),
        "accepted_by_seconds_bucket": dict(accepted_by_seconds_bucket),
        "accepted_by_entry_price_bucket": dict(accepted_by_entry_price_bucket),
        "cooldown_skips": skipped_by_reason.get("cooldown after loss", 0),
        "loss_limit_skips": skipped_by_reason.get("session loss limit reached", 0) + skipped_by_reason.get("daily loss limit reached", 0),
        "near_threshold": near_threshold,
        "markets_with_most_skips": markets_with_skips.most_common(5),
        "edge_distribution": _bucket_edges(edge_values),
        "pair_cost_distribution": _bucket_pair_costs(pair_cost_values),
        "stuck_signal_count": len(stuck_rows),
        "stuck_bucket_distribution": dict(stuck_bucket_distribution),
        "average_stuck_cycles": _safe_avg([float(value) for value in stuck_cycles_values]),
        "average_transition_probability": _safe_avg(transition_probabilities),
        "result_by_bucket": dict(result_by_bucket),
        "result_by_seconds_bucket": dict(result_by_seconds_bucket),
        "result_by_entry_price_bucket": dict(result_by_entry_price_bucket),
        "market_rows": market_rows,
    }


def _section_lines(label: str, values: dict[str, Any], indent: str = "") -> list[str]:
    lines = [f"{indent}{label}:"]
    lines.append(f"{indent}config={values['config']}")
    lines.append(f"{indent}total_opportunities={values['total_opportunities']}")
    lines.append(f"{indent}accepted_trades={values['accepted_trades']}")
    lines.append(f"{indent}skipped_opportunities={values['skipped_opportunities']}")
    lines.append(f"{indent}skipped_by_reason={_fmt_map(values['skipped_by_reason'])}")
    lines.append(f"{indent}strategy_skips={_fmt_map(values['strategy_skips'])}")
    lines.append(f"{indent}risk_skips={_fmt_map(values['risk_skips'])}")
    lines.append(f"{indent}risk_blocked_trades={values['risk_blocked_trades']}")
    lines.append(
        f"{indent}edge_stats=avg:{_fmt_float(values['average_edge'])}, min:{_fmt_float(values['min_edge'])}, "
        f"median:{_fmt_float(values['median_edge'])}, max:{_fmt_float(values['max_edge'])}"
    )
    lines.append(
        f"{indent}accepted_edge_stats=avg:{_fmt_float(values['average_edge_accepted'])}, skipped_avg:{_fmt_float(values['average_edge_skipped'])}, "
        f"min:{_fmt_float(values['accepted_edge_min'])}, median:{_fmt_float(values['accepted_edge_median'])}, max:{_fmt_float(values['accepted_edge_max'])}"
    )
    lines.append(
        f"{indent}spread_stats=avg:{_fmt_float(values['average_spread'])}, min:{_fmt_float(values['min_spread'])}, "
        f"max:{_fmt_float(values['max_spread'])}"
    )
    lines.append(
        f"{indent}pair_cost_stats=avg:{_fmt_float(values['average_pair_cost'])}, min:{_fmt_float(values['min_pair_cost'])}, "
        f"max:{_fmt_float(values['max_pair_cost'])}"
    )
    lines.append(
        f"{indent}seconds_to_expiry=avg:{_fmt_float(values['average_seconds_to_expiry'])}, min:{_fmt_float(values['min_seconds_to_expiry'])}, "
        f"max:{_fmt_float(values['max_seconds_to_expiry'])}"
    )
    lines.append(f"{indent}timing_buckets={_fmt_map(values['timing_buckets'])}")
    if values["liquidity"]:
        lines.append(f"{indent}liquidity={_fmt_map(values['liquidity'])}")
    if values["momentum_timing"]:
        lines.append(f"{indent}momentum_timing={_fmt_map(values['momentum_timing'])}")
    lines.append(
        f"{indent}exposure=avg:{_fmt_float(values['exposure_avg'])}, max:{_fmt_float(values['exposure_max'])}, "
        f"max_simultaneous_positions={values['max_simultaneous_positions']}"
    )
    lines.append(
        f"{indent}trade_size=avg:{_fmt_float(values['average_trade_size'])}, largest:{_fmt_float(values['largest_single_trade'])}, "
        f"trades_per_market_avg:{_fmt_float(values['trades_per_market_avg'])}, trades_per_market_max:{values['trades_per_market_max']}"
    )
    lines.append(f"{indent}accepted_trades_by_side={_fmt_map(values['accepted_by_side'])}")
    lines.append(f"{indent}accepted_trades_by_asset={_fmt_map(values['accepted_by_asset'])}")
    lines.append(f"{indent}accepted_trades_by_duration={_fmt_map(values['accepted_by_duration'])}")
    lines.append(f"{indent}accepted_trades_by_seconds_to_expiry_bucket={_fmt_map(values['accepted_by_seconds_bucket'])}")
    lines.append(f"{indent}accepted_trades_by_entry_price_bucket={_fmt_map(values['accepted_by_entry_price_bucket'])}")
    lines.append(f"{indent}cooldown_skips={values['cooldown_skips']}")
    lines.append(f"{indent}loss_limit_skips={values['loss_limit_skips']}")
    lines.append(f"{indent}near_threshold={values['near_threshold']}")
    lines.append(f"{indent}markets_with_most_skips={_fmt_pairs(values['markets_with_most_skips'])}")
    lines.append(f"{indent}edge_distribution={_fmt_map(values['edge_distribution'])}")
    if values["pair_cost_distribution"]:
        lines.append(f"{indent}pair_cost_distribution={_fmt_map(values['pair_cost_distribution'])}")
    if values["stuck_signal_count"]:
        lines.append(f"{indent}stuck_signals_found={values['stuck_signal_count']}")
        lines.append(f"{indent}stuck_bucket_distribution={_fmt_map(values['stuck_bucket_distribution'])}")
        lines.append(f"{indent}average_stuck_cycles={_fmt_float(values['average_stuck_cycles'])}")
        lines.append(f"{indent}average_transition_probability={_fmt_float(values['average_transition_probability'])}")
        lines.append(f"{indent}result_by_bucket={_fmt_map(values['result_by_bucket'])}")
        lines.append(f"{indent}result_by_seconds_to_expiry_bucket={_fmt_map(values['result_by_seconds_bucket'])}")
        lines.append(f"{indent}result_by_entry_price_bucket={_fmt_map(values['result_by_entry_price_bucket'])}")
    return lines


def _matching_run_rows(
    store: SQLiteStore,
    run_id: str | None = None,
    strategy: str | None = None,
    source_filter: str | None = None,
    session_id: str | None = None,
    active_only: bool = False,
    tiny_only: bool = False,
) -> list[Any]:
    if run_id:
        row = store.run_by_id(run_id)
        return [row] if row else []
    clauses = []
    params: list[Any] = []
    if strategy:
        clauses.append("strategy = ?")
        params.append(strategy)
    if source_filter:
        clauses.append("data_source = ?")
        params.append(source_filter)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if active_only:
        clauses.append("notes LIKE ?")
        params.append("%active_only=true%")
    if tiny_only:
        clauses.append("notes LIKE ?")
        params.append("%tiny_profile=true%")
    if clauses:
        where = " WHERE " + " AND ".join(clauses)
        return store.rows(f"SELECT * FROM runs{where} ORDER BY started_at, rowid", tuple(params))
    latest = store.latest_run()
    return [latest] if latest else []


def _scope_label(run_rows: list[Any], run_id: str | None, strategy: str | None, source_filter: str | None, session_id: str | None) -> str:
    if run_id:
        return f"run_id={run_id}"
    parts = []
    if strategy:
        parts.append(f"strategy={strategy}")
    if source_filter:
        parts.append(f"source={source_filter}")
    if session_id:
        parts.append(f"session_id={session_id}")
    if parts:
        return "; ".join(parts)
    if run_rows:
        return f"latest run ({run_rows[-1]['run_id']})"
    return "no matching runs"


def _is_near_threshold(row: Any, config: dict[str, str]) -> bool:
    edge = float(row["edge"] or 0.0)
    if str(row["strategy"]) == "pair-cost":
        return abs(edge) <= 0.01
    threshold = float(config.get("min_edge", "0.02"))
    return abs(edge - threshold) <= 0.01


def _market_level_rows(opportunity_rows: list[Any], trade_rows: list[Any], market_map: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "asset": "n/a",
            "opportunities": 0,
            "accepted_trades": 0,
            "skipped": 0,
            "spreads": [],
            "edges": [],
        }
    )
    for row in opportunity_rows:
        market = grouped[str(row["market_slug"])]
        market["asset"] = str(row["asset"])
        market["opportunities"] += 1
        if str(row["decision"]) == "SKIP":
            market["skipped"] += 1
        if row["spread"] is not None:
            market["spreads"].append(float(row["spread"]))
        market["edges"].append(float(row["edge"] or 0.0))
    for row in trade_rows:
        grouped[str(row["market_slug"])]["accepted_trades"] += 1

    output = []
    for slug, values in grouped.items():
        audit = market_map.get(slug, {})
        output.append(
            {
                "market_slug": slug,
                "asset": values["asset"],
                "opportunities": values["opportunities"],
                "accepted_trades": values["accepted_trades"],
                "skipped": values["skipped"],
                "avg_spread": _safe_avg(values["spreads"]),
                "avg_edge": _safe_avg(values["edges"]),
                "orderbook_status": audit.get("orderbook_status", "unknown"),
                "first_seen": audit.get("first_seen", "n/a"),
                "latest_seen": audit.get("latest_seen", "n/a"),
            }
        )
    return sorted(output, key=lambda item: (-item["skipped"], -item["opportunities"], item["market_slug"]))


def _bucket_edges(values: list[float]) -> dict[str, int]:
    buckets = Counter()
    for value in values:
        if value < -0.05:
            buckets["edge < -0.05"] += 1
        elif value < 0.0:
            buckets["-0.05 to 0"] += 1
        elif value < 0.01:
            buckets["0 to 0.01"] += 1
        elif value < 0.03:
            buckets["0.01 to 0.03"] += 1
        elif value < 0.05:
            buckets["0.03 to 0.05"] += 1
        else:
            buckets["> 0.05"] += 1
    return dict(buckets)


def _bucket_pair_costs(values: list[float]) -> dict[str, int]:
    buckets = Counter()
    for value in values:
        if value > 1.02:
            buckets["pair cost > 1.02"] += 1
        elif value >= 1.00:
            buckets["1.00 to 1.02"] += 1
        elif value >= 0.99:
            buckets["0.99 to 1.00"] += 1
        elif value >= 0.97:
            buckets["0.97 to 0.99"] += 1
        else:
            buckets["< 0.97"] += 1
    return dict(buckets)


def _safe_avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _seconds_to_expiry_for_trade(row: Any) -> float:
    opened_at = datetime.fromisoformat(str(row["opened_at"]).replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(str(row["window_end"]).replace("Z", "+00:00"))
    return max(0.0, (window_end - opened_at).total_seconds())


def _seconds_bucket(seconds_to_expiry: float) -> str:
    if seconds_to_expiry < 30:
        return "<30"
    if seconds_to_expiry < 60:
        return "30-60"
    if seconds_to_expiry < 120:
        return "60-120"
    if seconds_to_expiry < 180:
        return "120-180"
    if seconds_to_expiry < 240:
        return "180-240"
    return "240+"


def _entry_price_bucket(entry_price: float) -> str:
    if entry_price < 0.03:
        return "<0.03"
    if entry_price < 0.05:
        return "0.03-0.05"
    if entry_price < 0.10:
        return "0.05-0.10"
    if entry_price < 0.25:
        return "0.10-0.25"
    return ">=0.25"


def _duration_label(row: Any) -> str:
    if not row["window_start"] or not row["window_end"]:
        return "unknown"
    start = datetime.fromisoformat(str(row["window_start"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(row["window_end"]).replace("Z", "+00:00"))
    minutes = (end - start).total_seconds() / 60.0
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"


def _max_simultaneous_positions(trade_rows: list[Any]) -> int:
    events: list[tuple[str, int]] = []
    for row in trade_rows:
        opened_at = str(row["opened_at"])
        events.append((opened_at, 1))
        closed_at = row["closed_at"]
        if closed_at:
            events.append((str(closed_at), -1))
    current = 0
    maximum = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_map(values: dict[str, Any]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())


def _fmt_pairs(values: list[tuple[str, int]]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={count}" for key, count in values)


def _single_value(rows: list[Any], field: str) -> str | None:
    values = {str(row[field]) for row in rows}
    return next(iter(values)) if len(values) == 1 else None
