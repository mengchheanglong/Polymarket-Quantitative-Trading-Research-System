from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class SignalAuditRow:
    market_slug: str
    asset: str
    duration: str
    side: str
    entry_timestamp: datetime
    expiry_timestamp: datetime
    start_price: float | None
    expiry_price: float | None
    actual_result: str
    side_matched: bool | None
    entry_price: float
    edge_at_entry: float | None
    seconds_to_expiry: float
    pnl: float | None


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
    opportunity_map: dict[tuple[str, str, str], float],
) -> list[SignalAuditRow]:
    source_filter = str(run["data_source"]) if run["data_source"] in {"demo", "public"} else None
    session_id = str(run["session_id"]) if run["session_id"] else None
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
        edge = opportunity_map.get((str(trade["market_slug"]), side, str(trade["opened_at"])))
        rows.append(
            SignalAuditRow(
                market_slug=str(trade["market_slug"]),
                asset=str(trade["asset"]),
                duration=_duration_label(trade["window_start"], trade["window_end"]),
                side=side,
                entry_timestamp=opened_at,
                expiry_timestamp=window_end,
                start_price=start_price.price if start_price else None,
                expiry_price=expiry_price.price if expiry_price else None,
                actual_result=actual_result,
                side_matched=side_matched,
                entry_price=float(trade["entry_price"]),
                edge_at_entry=edge,
                seconds_to_expiry=max(0.0, (window_end - opened_at).total_seconds()),
                pnl=float(trade["pnl"]) if trade["pnl"] is not None else None,
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
    for row in rows:
        side_counts[row.side] += 1
        duration_counts[row.duration] += 1
        if row.side_matched is True:
            outcome_counts["matched"] += 1
        elif row.side_matched is False:
            outcome_counts["mismatched"] += 1
        else:
            outcome_counts["unknown"] += 1
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
                    f"expiry_price={_fmt_price(row.expiry_price)}",
                    f"actual_result={row.actual_result}",
                    f"side_matched={row.side_matched}",
                    f"entry_price={row.entry_price:.4f}",
                    f"edge_at_entry={_fmt_float(row.edge_at_entry)}",
                    f"seconds_to_expiry={row.seconds_to_expiry:.1f}",
                    f"pnl={_fmt_money(row.pnl)}",
                ]
            )
        )
    return "\n".join(lines)


def _accepted_opportunity_map(store: SQLiteStore, run_id: str) -> dict[tuple[str, str, str], float]:
    rows = store.rows(
        """
        SELECT market_slug, direction, observed_at, edge
        FROM opportunities
        WHERE run_id = ? AND decision = 'TRADE'
        """,
        (run_id,),
    )
    return {
        (str(row["market_slug"]), str(row["direction"]), str(row["observed_at"])): float(row["edge"] or 0.0)
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


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"
