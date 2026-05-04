from __future__ import annotations

from dataclasses import dataclass

from src.reports.config_view import format_config_view, merged_config_view
from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class Report:
    scope: str
    strategy: str
    mode: str
    data_source: str
    config_summary: str
    starting_balance: float
    current_balance: float
    realized_pnl: float
    unrealized_pnl: float
    total_equity: float
    max_equity_drawdown: float
    max_position_exposure: float
    closed_trades: int
    skipped_trades: int
    win_rate: float
    average_edge: float

    def as_text(self) -> str:
        return "\n".join(
            [
                "Paper trading report",
                f"Scope: {self.scope}",
                f"Strategy: {self.strategy}",
                f"Mode: {self.mode}",
                f"Data source: {self.data_source}",
                f"Config: {self.config_summary}",
                f"Starting balance: ${self.starting_balance:.2f}",
                f"Current cash balance: ${self.current_balance:.2f}",
                f"Realized fake PnL: ${self.realized_pnl:.2f}",
                f"Unrealized fake PnL: ${self.unrealized_pnl:.2f}",
                f"Total fake equity: ${self.total_equity:.2f}",
                f"Max equity drawdown: ${self.max_equity_drawdown:.2f}",
                f"Max position exposure: ${self.max_position_exposure:.2f}",
                f"Closed trades: {self.closed_trades}",
                f"Skipped trades: {self.skipped_trades}",
                f"Win rate: {self.win_rate:.2%}",
                f"Average edge: {self.average_edge:.4f}",
            ]
        )


def build_report(
    store: SQLiteStore,
    starting_balance: float,
    run_id: str | None = None,
    strategy: str | None = None,
    all_runs: bool = False,
) -> Report:
    run_rows = _resolve_run_rows(store, run_id, strategy, all_runs)
    run_ids = [str(row["run_id"]) for row in run_rows]
    scope = _scope_label(run_ids, run_id, strategy, all_runs)
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        trade_rows = store.rows(
            f"SELECT result, pnl FROM trades WHERE status = 'CLOSED' AND run_id IN ({placeholders})",
            tuple(run_ids),
        )
        open_rows = store.rows(
            f"SELECT * FROM trades WHERE status = 'OPEN' AND run_id IN ({placeholders})",
            tuple(run_ids),
        )
    else:
        trade_rows = store.rows("SELECT result, pnl FROM trades WHERE status = 'CLOSED'")
        open_rows = store.open_trades()
    closed = len(trade_rows)
    wins = sum(1 for row in trade_rows if row["result"] == "WIN")
    realized_pnl = sum(float(row["pnl"] or 0.0) for row in trade_rows)
    open_value = sum(float(row["shares"]) * float(row["entry_price"]) for row in open_rows)
    open_exposure = sum(float(row["total_cost"]) for row in open_rows)
    unrealized_pnl = open_value - open_exposure
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        skipped = store.rows(
            f"SELECT COUNT(*) AS count FROM opportunities WHERE decision = 'SKIP' AND run_id IN ({placeholders})",
            tuple(run_ids),
        )[0]["count"]
        edge_rows = store.rows(
            f"SELECT edge FROM opportunities WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        )
    else:
        skipped = store.rows("SELECT COUNT(*) AS count FROM opportunities WHERE decision = 'SKIP'")[0][
            "count"
        ]
        edge_rows = store.rows("SELECT edge FROM opportunities")
    avg_edge = (
        sum(float(row["edge"] or 0.0) for row in edge_rows) / len(edge_rows)
        if edge_rows
        else 0.0
    )
    if len(run_ids) == 1:
        current_balance = store.current_balance(default=starting_balance, run_id=run_ids[-1])
    elif run_ids:
        current_balance = starting_balance + realized_pnl
    else:
        current_balance = store.current_balance(default=starting_balance)
    total_equity = current_balance + open_value
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        equity_rows = store.rows(
            f"SELECT total_equity FROM equity_snapshots WHERE run_id IN ({placeholders}) ORDER BY id",
            tuple(run_ids),
        )
        exposure_rows = store.rows(
            f"SELECT position_exposure FROM equity_snapshots WHERE run_id IN ({placeholders}) ORDER BY id",
            tuple(run_ids),
        )
    else:
        equity_rows = store.rows("SELECT total_equity FROM equity_snapshots ORDER BY id")
        exposure_rows = store.rows("SELECT position_exposure FROM equity_snapshots ORDER BY id")
    equity_values = [starting_balance, *[float(row["total_equity"]) for row in equity_rows]]
    if equity_values[-1] != total_equity:
        equity_values.append(total_equity)
    max_exposure = max(
        [open_exposure, *[float(row["position_exposure"]) for row in exposure_rows]],
        default=0.0,
    )
    strategies = sorted({str(row["strategy"]) for row in run_rows}) if run_rows else []
    modes = sorted({str(row["mode"]) for row in run_rows}) if run_rows else []
    data_sources = sorted({str(row["data_source"]) for row in run_rows}) if run_rows else []
    return Report(
        scope=scope,
        strategy=_single_or_mixed(strategies),
        mode=_single_or_mixed(modes),
        data_source=_single_or_mixed(data_sources),
        config_summary=format_config_view(merged_config_view([row["notes"] for row in run_rows])),
        starting_balance=starting_balance,
        current_balance=current_balance,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_equity=total_equity,
        max_equity_drawdown=_max_drawdown(equity_values),
        max_position_exposure=max_exposure,
        closed_trades=closed,
        skipped_trades=int(skipped),
        win_rate=wins / closed if closed else 0.0,
        average_edge=avg_edge,
    )


def _resolve_run_rows(
    store: SQLiteStore,
    run_id: str | None,
    strategy: str | None,
    all_runs: bool,
) -> list:
    if run_id:
        row = store.run_by_id(run_id)
        return [row] if row else []
    if strategy:
        return store.rows(
            "SELECT * FROM runs WHERE strategy = ? ORDER BY started_at, rowid",
            (strategy,),
        )
    if all_runs:
        return store.run_rows()
    latest = store.latest_run_id()
    row = store.run_by_id(latest) if latest else None
    return [row] if row else []


def _scope_label(run_ids: list[str], run_id: str | None, strategy: str | None, all_runs: bool) -> str:
    if run_id:
        return f"run_id={run_id}"
    if strategy:
        return f"strategy={strategy}"
    if all_runs:
        return "all runs"
    if run_ids:
        return f"latest run ({run_ids[-1]})"
    return "unscoped legacy rows"


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return max_dd


def _single_or_mixed(values: list[str]) -> str:
    if not values:
        return "n/a"
    return values[0] if len(values) == 1 else "mixed"
