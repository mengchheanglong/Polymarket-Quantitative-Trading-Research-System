from __future__ import annotations

from dataclasses import dataclass

from src.reports.signal_audit import (
    SignalAuditRow,
    load_signal_audit_rows,
    settlement_direction,
    settlement_match,
    settlement_value_for_side,
)
from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class ConsistencyAuditRow:
    trade_id: str
    market_slug: str
    side: str
    token_id: str
    entry_price: float
    exit_price: float | None
    realized_pnl: float | None
    start_price: float | None
    expiry_price: float | None
    settlement_direction: str
    side_matched: bool | None
    pnl_sign: str
    consistency_status: str
    close_mode: str | None
    token_mapping_status: str
    settlement_note: str | None


def build_consistency_audit(store: SQLiteStore, run_id: str) -> str:
    run, rows = load_consistency_rows(store, run_id)
    lines = ["Consistency audit", f"Run ID: {run_id}"]
    if run is None:
        lines.append("Run not found.")
        return "\n".join(lines)
    lines.extend(
        [
            f"Strategy: {run['strategy']}",
            f"Mode: {run['mode']}",
            f"Data source: {run['data_source']}",
        ]
    )
    if not rows:
        lines.append("No closed trades found.")
        return "\n".join(lines)
    totals = summarize_consistency_rows(rows)
    lines.extend(
        [
            f"Closed trades checked: {totals['closed_trades_checked']}",
            f"Consistency OK: {totals['ok_count']}",
            f"Positive PnL but side mismatch: {totals['positive_pnl_but_side_mismatch']}",
            f"Negative PnL but side match: {totals['negative_pnl_but_side_match']}",
            f"Unknown settlement: {totals['unknown_settlement']}",
            f"Token mapping suspect: {totals['token_mapping_suspect']}",
            f"Close mode mismatch: {totals['close_mode_mismatch']}",
        ]
    )
    for row in rows:
        side_label = "unknown"
        if row.side_matched is True:
            side_label = "true"
        elif row.side_matched is False:
            side_label = "false"
        lines.append(
            " | ".join(
                [
                    f"trade_id={row.trade_id}",
                    f"market={row.market_slug}",
                    f"side={row.side}",
                    f"token={row.token_id or 'n/a'}",
                    f"entry_price={row.entry_price:.4f}",
                    f"exit_price={_fmt_value(row.exit_price)}",
                    f"realized_pnl={_fmt_money(row.realized_pnl)}",
                    f"start_btc={_fmt_price(row.start_price)}",
                    f"expiry_btc={_fmt_price(row.expiry_price)}",
                    f"settlement={row.settlement_direction}",
                    f"side_matched={side_label}",
                    f"pnl_sign={row.pnl_sign}",
                    f"token_mapping={row.token_mapping_status}",
                    f"status={row.consistency_status}",
                    f"close_mode={row.close_mode or 'n/a'}",
                    f"note={row.settlement_note or 'n/a'}",
                ]
            )
        )
    return "\n".join(lines)


def load_consistency_rows(store: SQLiteStore, run_id: str) -> tuple[object | None, list[ConsistencyAuditRow]]:
    run, signal_rows = load_signal_audit_rows(store, run_id)
    if run is None:
        return None, []
    trade_rows = {
        str(row["trade_id"]): row
        for row in store.trade_rows(run_id)
        if row["status"] and str(row["status"]).startswith("CLOSED")
    }
    market_tokens = _market_token_map(store, str(run["session_id"]) if run["session_id"] else None)
    output: list[ConsistencyAuditRow] = []
    for signal_row in signal_rows:
        trade = trade_rows.get(signal_row.trade_id)
        if trade is None:
            continue
        output.append(_consistency_row(signal_row, trade, market_tokens.get(signal_row.market_slug)))
    return run, output


def summarize_consistency_rows(rows: list[ConsistencyAuditRow]) -> dict[str, int]:
    summary = {
        "closed_trades_checked": len(rows),
        "ok_count": 0,
        "positive_pnl_but_side_mismatch": 0,
        "negative_pnl_but_side_match": 0,
        "unknown_settlement": 0,
        "token_mapping_suspect": 0,
        "close_mode_mismatch": 0,
    }
    for row in rows:
        if row.consistency_status == "OK":
            summary["ok_count"] += 1
        elif row.consistency_status == "PNL_POSITIVE_BUT_SIDE_MISMATCH":
            summary["positive_pnl_but_side_mismatch"] += 1
        elif row.consistency_status == "PNL_NEGATIVE_BUT_SIDE_MATCH":
            summary["negative_pnl_but_side_match"] += 1
        elif row.consistency_status == "UNKNOWN_SETTLEMENT":
            summary["unknown_settlement"] += 1
        elif row.consistency_status == "TOKEN_SIDE_MAPPING_SUSPECT":
            summary["token_mapping_suspect"] += 1
        elif row.consistency_status == "CLOSE_MODE_MISMATCH":
            summary["close_mode_mismatch"] += 1
    return summary


def _consistency_row(
    signal_row: SignalAuditRow,
    trade,
    market_tokens: tuple[str | None, str | None] | None,
) -> ConsistencyAuditRow:
    pnl = signal_row.pnl
    pnl_sign = "breakeven"
    if pnl is not None and pnl > 0:
        pnl_sign = "win"
    elif pnl is not None and pnl < 0:
        pnl_sign = "loss"
    expected_value = settlement_value_for_side(signal_row.side, signal_row.start_price, signal_row.expiry_price)
    actual_value = signal_row.exit_price
    token_mapping_status = "ok"
    if market_tokens is not None:
        up_token, down_token = market_tokens
        expected_token = up_token if signal_row.side == "UP" else down_token if signal_row.side == "DOWN" else None
        if expected_token and signal_row.token_id and signal_row.token_id != expected_token:
            token_mapping_status = "suspect"
    status = "OK"
    if token_mapping_status == "suspect":
        status = "TOKEN_SIDE_MAPPING_SUSPECT"
    elif signal_row.close_mode == "approximate-expiry" and expected_value is None:
        status = "UNKNOWN_SETTLEMENT"
    elif pnl is not None and pnl > 0 and signal_row.side_matched is False:
        status = "PNL_POSITIVE_BUT_SIDE_MISMATCH"
    elif pnl is not None and pnl < -1e-9 and signal_row.side_matched is True:
        status = "PNL_NEGATIVE_BUT_SIDE_MATCH"
    elif signal_row.close_mode == "approximate-expiry" and actual_value is not None and expected_value is not None and abs(actual_value - expected_value) > 1e-9:
        status = "CLOSE_MODE_MISMATCH"
    elif signal_row.side_matched is None:
        status = "UNKNOWN_SETTLEMENT"
    return ConsistencyAuditRow(
        trade_id=signal_row.trade_id,
        market_slug=signal_row.market_slug,
        side=signal_row.side,
        token_id=signal_row.token_id,
        entry_price=signal_row.entry_price,
        exit_price=signal_row.exit_price,
        realized_pnl=pnl,
        start_price=signal_row.start_price,
        expiry_price=signal_row.expiry_price,
        settlement_direction=settlement_direction(signal_row.start_price, signal_row.expiry_price),
        side_matched=signal_row.side_matched,
        pnl_sign=pnl_sign,
        consistency_status=status,
        close_mode=signal_row.close_mode,
        token_mapping_status=token_mapping_status,
        settlement_note=signal_row.settlement_note,
    )


def _market_token_map(store: SQLiteStore, session_id: str | None) -> dict[str, tuple[str | None, str | None]]:
    if session_id is None:
        return {}
    rows = store.rows(
        """
        SELECT market_slug, up_token_id, down_token_id
        FROM discovered_markets
        WHERE session_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (session_id,),
    )
    output: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        slug = str(row["market_slug"])
        if slug not in output:
            output[slug] = (
                str(row["up_token_id"]) if row["up_token_id"] is not None else None,
                str(row["down_token_id"]) if row["down_token_id"] is not None else None,
            )
    return output


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _fmt_value(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"
