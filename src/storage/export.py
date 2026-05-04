from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from src.storage.sqlite import SQLiteStore


EXPORT_QUERIES = {
    "raw_snapshots": "SELECT observed_at, source_name, asset, snapshot_type, status, error_message FROM raw_snapshots ORDER BY observed_at, id",
    "trades": "SELECT trade_id, run_id, opened_at, closed_at, market_slug, asset, direction, entry_price, shares, entry_fee, slippage_cost, exit_price, exit_fee, pnl, result, status, close_mode, settlement_note FROM trades ORDER BY opened_at, trade_id",
    "skipped_opportunities": "SELECT run_id, observed_at, market_slug, asset, direction, market_price, spread, edge, reason, seconds_to_expiry, lifecycle_status, timing_bucket FROM opportunities WHERE decision = 'SKIP' ORDER BY observed_at, id",
    "runs": "SELECT run_id, strategy, mode, data_source, started_at, ended_at, starting_balance, ending_balance, realized_pnl, max_equity_drawdown, max_position_exposure, accepted_trade_count, skipped_opportunity_count, notes FROM runs ORDER BY started_at, rowid",
    "equity_snapshots": "SELECT run_id, observed_at, cash_balance, open_position_value, total_equity, position_exposure FROM equity_snapshots ORDER BY observed_at, id",
}


def export_csv(
    store: SQLiteStore,
    out_dir: Path | str,
    source_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rows_by_name = _filtered_rows(store, source_filter=source_filter, since=since, until=until, session_id=session_id)
    for name in EXPORT_QUERIES:
        rows = rows_by_name[name]
        path = out / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if rows:
                headers = rows[0].keys()
                writer.writerow(headers)
                for row in rows:
                    writer.writerow([row[key] for key in headers])
            else:
                writer.writerow(_headers_for_empty(name))
        written.append(path)
    return written


def _filtered_rows(
    store: SQLiteStore,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> dict[str, list]:
    if source_filter is None and since is None and until is None and session_id is None:
        return {name: store.rows(query) for name, query in EXPORT_QUERIES.items()}

    raw_rows = [
        {
            "observed_at": row["observed_at"],
            "source_name": row["source_name"],
            "asset": row["asset"],
            "snapshot_type": row["snapshot_type"],
            "status": row["status"],
            "error_message": row["error_message"],
        }
        for row in store.raw_snapshot_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
    ]
    run_rows = _run_rows(store, source_filter, session_id)
    run_ids = [str(row["run_id"]) for row in run_rows]
    if not run_ids:
        return {
            "raw_snapshots": raw_rows,
            "trades": [],
            "skipped_opportunities": [],
            "runs": [],
            "equity_snapshots": [],
        }
    placeholders = ",".join("?" for _ in run_ids)
    return {
        "raw_snapshots": raw_rows,
        "trades": store.rows(
            f"""
            SELECT trade_id, run_id, opened_at, closed_at, market_slug, asset, direction, entry_price,
                   shares, entry_fee, slippage_cost, exit_price, exit_fee, pnl, result, status, close_mode, settlement_note
            FROM trades
            WHERE run_id IN ({placeholders})
            ORDER BY opened_at, trade_id
            """,
            tuple(run_ids),
        ),
        "skipped_opportunities": store.rows(
            f"""
            SELECT run_id, observed_at, market_slug, asset, direction, market_price, spread, edge, reason, seconds_to_expiry, lifecycle_status, timing_bucket
            FROM opportunities
            WHERE decision = 'SKIP' AND run_id IN ({placeholders})
            ORDER BY observed_at, id
            """,
            tuple(run_ids),
        ),
        "runs": run_rows,
        "equity_snapshots": store.rows(
            f"""
            SELECT run_id, observed_at, cash_balance, open_position_value, total_equity, position_exposure
            FROM equity_snapshots
            WHERE run_id IN ({placeholders})
            ORDER BY observed_at, id
            """,
            tuple(run_ids),
        ),
    }


def _run_rows(store: SQLiteStore, source_filter: str | None, session_id: str | None) -> list:
    if source_filter is None and session_id is None:
        return store.rows(EXPORT_QUERIES["runs"])
    clauses = []
    params: list[str] = []
    if source_filter is not None:
        clauses.append("data_source = ?")
        params.append(source_filter)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    return store.rows(
        f"""
        SELECT run_id, strategy, mode, data_source, started_at, ended_at, starting_balance,
               ending_balance, realized_pnl, max_equity_drawdown, max_position_exposure,
               accepted_trade_count, skipped_opportunity_count, notes
        FROM runs
        WHERE {' AND '.join(clauses)}
        ORDER BY started_at, rowid
        """,
        tuple(params),
    )


def _headers_for_empty(name: str) -> list[str]:
    return {
        "raw_snapshots": ["observed_at", "source_name", "asset", "snapshot_type", "status", "error_message"],
        "trades": ["trade_id", "run_id", "opened_at", "closed_at", "market_slug", "asset", "direction", "entry_price", "shares", "entry_fee", "slippage_cost", "exit_price", "exit_fee", "pnl", "result", "status", "close_mode", "settlement_note"],
        "skipped_opportunities": ["run_id", "observed_at", "market_slug", "asset", "direction", "market_price", "spread", "edge", "reason", "seconds_to_expiry", "lifecycle_status", "timing_bucket"],
        "runs": ["run_id", "strategy", "mode", "data_source", "started_at", "ended_at", "starting_balance", "ending_balance", "realized_pnl", "max_equity_drawdown", "max_position_exposure", "accepted_trade_count", "skipped_opportunity_count", "notes"],
        "equity_snapshots": ["run_id", "observed_at", "cash_balance", "open_position_value", "total_equity", "position_exposure"],
    }[name]
