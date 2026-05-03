from __future__ import annotations

import csv
from pathlib import Path

from src.storage.sqlite import SQLiteStore


EXPORT_QUERIES = {
    "raw_snapshots": "SELECT observed_at, source_name, asset, snapshot_type, status, error_message FROM raw_snapshots ORDER BY observed_at, id",
    "trades": "SELECT trade_id, run_id, opened_at, closed_at, market_slug, asset, direction, entry_price, shares, entry_fee, slippage_cost, exit_price, exit_fee, pnl, result, status FROM trades ORDER BY opened_at, trade_id",
    "skipped_opportunities": "SELECT run_id, observed_at, market_slug, asset, direction, market_price, spread, edge, reason FROM opportunities WHERE decision = 'SKIP' ORDER BY observed_at, id",
    "runs": "SELECT run_id, strategy, mode, data_source, started_at, ended_at, starting_balance, ending_balance, realized_pnl, max_equity_drawdown, max_position_exposure, accepted_trade_count, skipped_opportunity_count, notes FROM runs ORDER BY started_at, rowid",
    "equity_snapshots": "SELECT run_id, observed_at, cash_balance, open_position_value, total_equity, position_exposure FROM equity_snapshots ORDER BY observed_at, id",
}


def export_csv(store: SQLiteStore, out_dir: Path | str) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, query in EXPORT_QUERIES.items():
        rows = store.rows(query)
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


def _headers_for_empty(name: str) -> list[str]:
    return {
        "raw_snapshots": ["observed_at", "source_name", "asset", "snapshot_type", "status", "error_message"],
        "trades": ["trade_id", "run_id", "opened_at", "closed_at", "market_slug", "asset", "direction", "entry_price", "shares", "entry_fee", "slippage_cost", "exit_price", "exit_fee", "pnl", "result", "status"],
        "skipped_opportunities": ["run_id", "observed_at", "market_slug", "asset", "direction", "market_price", "spread", "edge", "reason"],
        "runs": ["run_id", "strategy", "mode", "data_source", "started_at", "ended_at", "starting_balance", "ending_balance", "realized_pnl", "max_equity_drawdown", "max_position_exposure", "accepted_trade_count", "skipped_opportunity_count", "notes"],
        "equity_snapshots": ["run_id", "observed_at", "cash_balance", "open_position_value", "total_equity", "position_exposure"],
    }[name]
