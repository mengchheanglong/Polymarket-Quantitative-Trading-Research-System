from __future__ import annotations

from dataclasses import dataclass

from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class Report:
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


def build_report(store: SQLiteStore, starting_balance: float) -> Report:
    trade_rows = store.rows("SELECT result, pnl FROM trades WHERE status = 'CLOSED'")
    open_rows = store.open_trades()
    closed = len(trade_rows)
    wins = sum(1 for row in trade_rows if row["result"] == "WIN")
    realized_pnl = sum(float(row["pnl"] or 0.0) for row in trade_rows)
    open_value = sum(float(row["shares"]) * float(row["entry_price"]) for row in open_rows)
    open_exposure = sum(float(row["total_cost"]) for row in open_rows)
    unrealized_pnl = open_value - open_exposure
    skipped = store.rows("SELECT COUNT(*) AS count FROM opportunities WHERE decision = 'SKIP'")[0][
        "count"
    ]
    edge_rows = store.rows("SELECT edge FROM opportunities")
    avg_edge = (
        sum(float(row["edge"] or 0.0) for row in edge_rows) / len(edge_rows)
        if edge_rows
        else 0.0
    )
    current_balance = store.current_balance(default=starting_balance)
    total_equity = current_balance + open_value
    equity_rows = store.rows("SELECT total_equity FROM equity_snapshots ORDER BY id")
    equity_values = [starting_balance, *[float(row["total_equity"]) for row in equity_rows]]
    if equity_values[-1] != total_equity:
        equity_values.append(total_equity)
    exposure_rows = store.rows("SELECT position_exposure FROM equity_snapshots ORDER BY id")
    max_exposure = max(
        [open_exposure, *[float(row["position_exposure"]) for row in exposure_rows]],
        default=0.0,
    )
    return Report(
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


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return max_dd
