from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class _NearestPrice:
    price: float
    observed_at: datetime
    source: str
    delta_seconds: float


def build_settlement_report(store: SQLiteStore, run_id: str) -> str:
    run = store.run_by_id(run_id)
    lines = ["Settlement validation report", f"Run ID: {run_id}"]
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

    trade_rows = [
        row
        for row in store.trade_rows(run_id)
        if str(row["close_mode"] or "") == "approximate-expiry"
        or "approximate expiry" in str(row["settlement_note"] or "").lower()
    ]
    if not trade_rows:
        lines.append("No approximate-expiry trades found.")
        return "\n".join(lines)

    session_id = str(run["session_id"]) if run["session_id"] else None
    source_filter = str(run["data_source"]) if run["data_source"] in {"demo", "public"} else None
    confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    missing_start = 0
    missing_end = 0

    for trade in trade_rows:
        entry_time = _parse_iso(str(trade["opened_at"]))
        expiry_time = _parse_iso(str(trade["window_end"]))
        start_target = _parse_iso(str(trade["window_start"])) if trade["window_start"] else entry_time
        start_price = _nearest_price(store, str(trade["asset"]), start_target, source_filter=source_filter, session_id=session_id)
        end_price = _nearest_price(store, str(trade["asset"]), expiry_time, source_filter=source_filter, session_id=session_id)
        settlement_result = _settlement_result(start_price, end_price)
        confidence, reason = _confidence_for(start_price, end_price)
        confidence_counts[confidence] += 1
        if start_price is None:
            missing_start += 1
        if end_price is None:
            missing_end += 1
        lines.append(
            " | ".join(
                [
                    f"market={trade['market_slug']}",
                    f"side={trade['direction']}",
                    f"entry_ts={entry_time.isoformat()}",
                    f"expiry_ts={expiry_time.isoformat()}",
                    f"start_px={_fmt_price(start_price)}",
                    f"end_px={_fmt_price(end_price)}",
                    f"settlement={settlement_result}",
                    f"entry_price={float(trade['entry_price']):.4f}",
                    f"modeled_payout={_fmt_value(trade['exit_price'])}",
                    f"start_missing={start_price is None}",
                    f"end_missing={end_price is None}",
                    f"confidence={confidence}",
                    f"reason={reason}",
                ]
            )
        )

    lines.extend(
        [
            f"Approximate-expiry trades: {len(trade_rows)}",
            f"Missing start prices: {missing_start}",
            f"Missing end prices: {missing_end}",
            f"Confidence counts: HIGH={confidence_counts['HIGH']}, MEDIUM={confidence_counts['MEDIUM']}, LOW={confidence_counts['LOW']}",
        ]
    )
    return "\n".join(lines)


def _nearest_price(
    store: SQLiteStore,
    asset: str,
    target: datetime,
    *,
    source_filter: str | None,
    session_id: str | None,
    max_delta_seconds: int = 300,
) -> _NearestPrice | None:
    clauses = ["asset = ?"]
    params: list[object] = [asset]
    if source_filter == "demo":
        clauses.append("source LIKE ?")
        params.append("mock:%")
    elif source_filter == "public":
        clauses.append("source NOT LIKE ?")
        params.append("mock:%")
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    rows = store.rows(
        f"""
        SELECT asset, price, observed_at, source
        FROM price_snapshots
        WHERE {' AND '.join(clauses)}
        ORDER BY observed_at DESC, id DESC
        """,
        tuple(params),
    )
    candidates: list[_NearestPrice] = []
    for row in rows:
        try:
            observed_at = _parse_iso(str(row["observed_at"]))
        except ValueError:
            continue
        delta_seconds = abs((observed_at - target).total_seconds())
        if delta_seconds <= max_delta_seconds:
            candidates.append(
                _NearestPrice(
                    price=float(row["price"]),
                    observed_at=observed_at,
                    source=str(row["source"]),
                    delta_seconds=delta_seconds,
                )
            )
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.delta_seconds, -item.observed_at.timestamp()))


def _settlement_result(start_price: _NearestPrice | None, end_price: _NearestPrice | None) -> str:
    if start_price is None or end_price is None:
        return "UNKNOWN"
    if end_price.price > start_price.price:
        return "UP"
    if end_price.price < start_price.price:
        return "DOWN"
    return "FLAT"


def _confidence_for(start_price: _NearestPrice | None, end_price: _NearestPrice | None) -> tuple[str, str]:
    if start_price is None and end_price is None:
        return "LOW", "missing both start and expiry prices"
    if start_price is None:
        return "LOW", "missing start price near market start"
    if end_price is None:
        return "LOW", "missing expiry price near market end"
    max_delta = max(start_price.delta_seconds, end_price.delta_seconds)
    if max_delta <= 30:
        return "HIGH", "start and expiry prices were within 30 seconds"
    if max_delta <= 120:
        return "MEDIUM", "start and expiry prices were within 120 seconds"
    return "LOW", "start or expiry price was more than 120 seconds away"


def _config_value(notes: str, key: str) -> str | None:
    for part in notes.split(";"):
        candidate = part.strip()
        if not candidate or "=" not in candidate:
            continue
        name, value = candidate.split("=", 1)
        if name.strip() == key:
            return value.strip()
    return None


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt_price(value: _NearestPrice | None) -> str:
    if value is None:
        return "n/a"
    return f"{value.price:.2f}@{value.observed_at.isoformat()}({value.delta_seconds:.0f}s)"


def _fmt_value(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"
