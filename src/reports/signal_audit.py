from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class SignalAuditRow:
    session_id: str | None
    run_id: str | None
    market_slug: str
    asset: str
    duration: str
    side: str
    entry_timestamp: datetime
    expiry_timestamp: datetime
    start_price: float | None
    entry_underlying_price: float | None
    expiry_price: float | None
    actual_result: str
    side_matched: bool | None
    entry_price: float
    spread: float | None
    edge_at_entry: float | None
    seconds_to_expiry: float
    pnl: float | None
    close_mode: str | None
    pre_entry_exchange_move: float | None
    post_entry_exchange_move: float | None


def load_signal_audit_rows(store: SQLiteStore, run_id: str) -> tuple[object | None, list[SignalAuditRow]]:
    run = store.run_by_id(run_id)
    if run is None:
        return None, []
    trade_rows = store.trade_rows(run_id)
    if not trade_rows:
        return run, []
    opportunity_map = _accepted_opportunity_map(store, run_id)
    return run, _signal_rows_for_records(store, run, trade_rows, opportunity_map)


def load_signal_audit_rows_from_records(
    price_store: SQLiteStore,
    run,
    trade_rows,
    opportunity_map: dict[tuple[str, str, str], float] | None = None,
) -> list[SignalAuditRow]:
    return _signal_rows_for_records(price_store, run, trade_rows, opportunity_map or {})


def _signal_rows_for_records(
    price_store: SQLiteStore,
    run,
    trade_rows,
    opportunity_map: dict[tuple[str, str, str], dict[str, float | None]],
) -> list[SignalAuditRow]:
    source_filter = str(run["data_source"]) if run["data_source"] in {"demo", "public"} else None
    session_id = str(run["session_id"]) if run["session_id"] else None
    close_mode = _config_value(str(run["notes"] or ""), "close_mode")
    rows: list[SignalAuditRow] = []
    for trade in trade_rows:
        opened_at = _parse_iso(str(trade["opened_at"]))
        window_start = _parse_iso(str(trade["window_start"])) if trade["window_start"] else opened_at
        window_end = _parse_iso(str(trade["window_end"]))
        start_price = price_store.nearest_price(
            str(trade["asset"]),
            window_start,
            max_delta_seconds=60,
            source_filter=source_filter,
            session_id=session_id,
        )
        expiry_price = price_store.nearest_price(
            str(trade["asset"]),
            window_end,
            max_delta_seconds=60,
            source_filter=source_filter,
            session_id=session_id,
        )
        actual_result = _actual_result(start_price.price if start_price else None, expiry_price.price if expiry_price else None)
        side = str(trade["direction"])
        side_matched = None if actual_result == "UNKNOWN" else side == actual_result
        opportunity = opportunity_map.get((str(trade["market_slug"]), side, str(trade["opened_at"])), {})
        entry_underlying_price = float(trade["entry_underlying_price"]) if trade["entry_underlying_price"] is not None else None
        rows.append(
            SignalAuditRow(
                session_id=session_id,
                run_id=str(run["run_id"]) if run["run_id"] else None,
                market_slug=str(trade["market_slug"]),
                asset=str(trade["asset"]),
                duration=_duration_label(trade["window_start"], trade["window_end"]),
                side=side,
                entry_timestamp=opened_at,
                expiry_timestamp=window_end,
                start_price=start_price.price if start_price else None,
                entry_underlying_price=entry_underlying_price,
                expiry_price=expiry_price.price if expiry_price else None,
                actual_result=actual_result,
                side_matched=side_matched,
                entry_price=float(trade["entry_price"]),
                spread=_float_or_none(opportunity.get("spread")),
                edge_at_entry=_float_or_none(opportunity.get("edge")),
                seconds_to_expiry=max(0.0, (window_end - opened_at).total_seconds()),
                pnl=float(trade["pnl"]) if trade["pnl"] is not None else None,
                close_mode=close_mode,
                pre_entry_exchange_move=_pct_move(
                    start_price.price if start_price else None,
                    entry_underlying_price,
                ),
                post_entry_exchange_move=_pct_move(
                    entry_underlying_price,
                    expiry_price.price if expiry_price else None,
                ),
            )
        )
    return rows


def summarize_signal_audit_rows(rows: list[SignalAuditRow]) -> dict[str, object]:
    side_counts = Counter()
    duration_counts = Counter()
    outcome_counts = Counter()
    by_asset = _correctness_breakdown(rows, key=lambda row: row.asset)
    by_duration = _correctness_breakdown(rows, key=lambda row: row.duration)
    by_side = _correctness_breakdown(rows, key=lambda row: row.side)
    by_entry_price_bucket = _correctness_breakdown(rows, key=lambda row: _entry_price_bucket(row.entry_price))
    by_seconds_to_expiry_bucket = _correctness_breakdown(rows, key=lambda row: _seconds_bucket(row.seconds_to_expiry))
    by_edge_bucket = _correctness_breakdown(rows, key=lambda row: _edge_bucket(row.edge_at_entry))
    by_spread_bucket = _correctness_breakdown(rows, key=lambda row: _spread_bucket(row.spread))
    by_pre_entry_move_bucket = _correctness_breakdown(rows, key=lambda row: _move_bucket(row.pre_entry_exchange_move))
    by_post_entry_move_bucket = _correctness_breakdown(rows, key=lambda row: _move_bucket(row.post_entry_exchange_move))
    lag_flags = Counter()
    for row in rows:
        side_counts[row.side] += 1
        duration_counts[row.duration] += 1
        if row.side_matched is True:
            outcome_counts["matched"] += 1
        elif row.side_matched is False:
            outcome_counts["mismatched"] += 1
        else:
            outcome_counts["unknown"] += 1
        for flag in _lag_flags(row):
            lag_flags[flag] += 1
    resolved = outcome_counts["matched"] + outcome_counts["mismatched"]
    return {
        "matched": outcome_counts["matched"],
        "mismatched": outcome_counts["mismatched"],
        "unknown": outcome_counts["unknown"],
        "correctness_rate": (outcome_counts["matched"] / resolved) if resolved else None,
        "side_counts": dict(side_counts),
        "duration_counts": dict(duration_counts),
        "by_asset": by_asset,
        "by_duration": by_duration,
        "by_side": by_side,
        "by_entry_price_bucket": by_entry_price_bucket,
        "by_seconds_to_expiry_bucket": by_seconds_to_expiry_bucket,
        "by_edge_bucket": by_edge_bucket,
        "by_spread_bucket": by_spread_bucket,
        "by_pre_entry_move_bucket": by_pre_entry_move_bucket,
        "by_post_entry_move_bucket": by_post_entry_move_bucket,
        "lag_flags": dict(lag_flags),
    }


def build_signal_audit(store: SQLiteStore, run_id: str) -> str:
    run, rows = load_signal_audit_rows(store, run_id)
    lines = ["Signal audit", f"Run ID: {run_id}"]
    if run is None:
        lines.append("Run not found.")
        return "\n".join(lines)

    close_mode = _config_value(str(run["notes"] or ""), "close_mode")
    lines.extend(
        [
            f"Strategy: {run['strategy']}",
            f"Mode: {run['mode']}",
            f"Data source: {run['data_source']}",
            f"Close mode: {close_mode or 'n/a'}",
        ]
    )
    if not rows:
        lines.append("No accepted trades found.")
        return "\n".join(lines)

    summary = summarize_signal_audit_rows(rows)
    lines.extend(
        [
            f"Accepted trades: {len(rows)}",
            f"Side correctness: matched={summary['matched']}, mismatched={summary['mismatched']}, unknown={summary['unknown']}",
            f"Accepted trades by side: {_fmt_map(summary['side_counts'])}",
            f"Accepted trades by duration: {_fmt_map(summary['duration_counts'])}",
            f"Accepted trades by entry price bucket: {_fmt_breakdown(summary['by_entry_price_bucket'])}",
            f"Accepted trades by seconds-to-expiry bucket: {_fmt_breakdown(summary['by_seconds_to_expiry_bucket'])}",
            f"Accepted trades by edge bucket: {_fmt_breakdown(summary['by_edge_bucket'])}",
            f"Accepted trades by spread bucket: {_fmt_breakdown(summary['by_spread_bucket'])}",
            f"Accepted trades by pre-entry move bucket: {_fmt_breakdown(summary['by_pre_entry_move_bucket'])}",
            f"Accepted trades by post-entry move bucket: {_fmt_breakdown(summary['by_post_entry_move_bucket'])}",
            f"Mismatch lag flags: {_fmt_map(summary['lag_flags'])}",
        ]
    )
    for row in rows:
        lines.append(
            " | ".join(
                [
                    row.market_slug,
                    f"asset={row.asset}",
                    f"duration={row.duration}",
                    f"side={row.side}",
                    f"entry_ts={row.entry_timestamp.isoformat()}",
                    f"expiry_ts={row.expiry_timestamp.isoformat()}",
                    f"start_price={_fmt_price(row.start_price)}",
                    f"entry_underlying_price={_fmt_price(row.entry_underlying_price)}",
                    f"expiry_price={_fmt_price(row.expiry_price)}",
                    f"actual_result={row.actual_result}",
                    f"side_matched={row.side_matched}",
                    f"entry_price={row.entry_price:.4f}",
                    f"spread={_fmt_float(row.spread)}",
                    f"edge_at_entry={_fmt_float(row.edge_at_entry)}",
                    f"seconds_to_expiry={row.seconds_to_expiry:.1f}",
                    f"pre_entry_move={_fmt_pct(row.pre_entry_exchange_move)}",
                    f"post_entry_move={_fmt_pct(row.post_entry_exchange_move)}",
                    f"lag_flags={','.join(_lag_flags(row)) or 'none'}",
                    f"pnl={_fmt_money(row.pnl)}",
                ]
            )
        )
    return "\n".join(lines)


def _accepted_opportunity_map(store: SQLiteStore, run_id: str) -> dict[tuple[str, str, str], dict[str, float | None]]:
    rows = store.rows(
        """
        SELECT market_slug, direction, observed_at, edge, spread
        FROM opportunities
        WHERE run_id = ? AND decision = 'TRADE'
        """,
        (run_id,),
    )
    return {
        (str(row["market_slug"]), str(row["direction"]), str(row["observed_at"])): {
            "edge": float(row["edge"] or 0.0) if row["edge"] is not None else None,
            "spread": float(row["spread"]) if row["spread"] is not None else None,
        }
        for row in rows
    }


def _actual_result(start_price: float | None, expiry_price: float | None) -> str:
    if start_price is None or expiry_price is None:
        return "UNKNOWN"
    if expiry_price > start_price:
        return "UP"
    if expiry_price < start_price:
        return "DOWN"
    return "FLAT"


def _correctness_breakdown(
    rows: list[SignalAuditRow],
    *,
    key,
) -> dict[str, dict[str, float | int | None]]:
    grouped: dict[str, dict[str, int]] = {}
    for row in rows:
        name = key(row)
        stats = grouped.setdefault(name, {"matched": 0, "mismatched": 0, "unknown": 0})
        if row.side_matched is True:
            stats["matched"] += 1
        elif row.side_matched is False:
            stats["mismatched"] += 1
        else:
            stats["unknown"] += 1
    output: dict[str, dict[str, float | int | None]] = {}
    for name, stats in grouped.items():
        resolved = stats["matched"] + stats["mismatched"]
        output[name] = {
            **stats,
            "correctness_rate": (stats["matched"] / resolved) if resolved else None,
        }
    return output


def _duration_label(window_start, window_end) -> str:
    if not window_start or not window_end:
        return "unknown"
    start = _parse_iso(str(window_start))
    end = _parse_iso(str(window_end))
    minutes = (end - start).total_seconds() / 60.0
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"


def _config_value(notes: str, key: str) -> str | None:
    for part in notes.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip() == key:
            return value.strip()
    return None


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt_map(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())


def _fmt_breakdown(values: dict[str, dict[str, float | int | None]]) -> str:
    if not values:
        return "none"
    return ", ".join(
        f"{key}={stats['matched']}/{int(stats['matched']) + int(stats['mismatched'])} ({_fmt_pct(stats['correctness_rate'])})"
        for key, stats in sorted(values.items())
    )


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _pct_move(start: float | None, end: float | None) -> float | None:
    if start in (None, 0.0) or end is None:
        return None
    return (end - start) / start


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _entry_price_bucket(value: float) -> str:
    if value < 0.20:
        return "<0.20"
    if value < 0.30:
        return "0.20-0.30"
    if value < 0.40:
        return "0.30-0.40"
    if value < 0.50:
        return "0.40-0.50"
    if value < 0.70:
        return "0.50-0.70"
    if value < 0.85:
        return "0.70-0.85"
    return ">=0.85"


def _seconds_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 60:
        return "30-60"
    if value < 120:
        return "60-120"
    if value < 180:
        return "120-180"
    if value < 240:
        return "180-240"
    return ">=240"


def _edge_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.03:
        return "<0.03"
    if value < 0.05:
        return "0.03-0.05"
    if value < 0.10:
        return "0.05-0.10"
    return ">=0.10"


def _spread_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value <= 0.01:
        return "<=0.01"
    if value <= 0.02:
        return "0.01-0.02"
    if value <= 0.05:
        return "0.02-0.05"
    return ">0.05"


def _move_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value <= -0.002:
        return "<=-0.20%"
    if value < -0.0005:
        return "-0.20% to -0.05%"
    if value <= 0.0005:
        return "-0.05% to 0.05%"
    if value < 0.002:
        return "0.05% to 0.20%"
    return ">=0.20%"


def _lag_flags(row: SignalAuditRow) -> list[str]:
    if row.side_matched is not False:
        return []
    flags: list[str] = []
    post_move = row.post_entry_exchange_move
    if post_move is None:
        return ["unknown"]
    if abs(post_move) < 0.0005:
        flags.append("noisy")
    if row.seconds_to_expiry >= 120:
        flags.append("too_early")
    elif row.seconds_to_expiry <= 60:
        flags.append("too_late")
    if row.edge_at_entry is None or row.edge_at_entry < 0.05:
        flags.append("low_confidence")
    if (row.side == "UP" and post_move < 0) or (row.side == "DOWN" and post_move > 0):
        flags.append("reversed")
    return flags or ["unknown"]
