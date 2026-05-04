from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SweepRow:
    label: str
    accepted_trades: int
    skipped_opportunities: int
    realized_pnl: float
    win_rate: float
    max_equity_drawdown: float
    max_position_exposure: float
    average_edge: float


def build_sweep_report(strategy: str, source_filter: str | None, rows: list[SweepRow]) -> str:
    lines = [
        "Threshold sweep",
        "Research only: simulated stored-snapshot replay. This is not evidence of live profitability.",
        f"Strategy: {strategy}",
        f"Source filter: {source_filter or 'all'}",
    ]
    if not rows:
        lines.append("No sweep rows produced.")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            " | ".join(
                [
                    row.label,
                    f"accepted_trades={row.accepted_trades}",
                    f"skipped_opportunities={row.skipped_opportunities}",
                    f"realized_pnl=${row.realized_pnl:.2f}",
                    f"win_rate={row.win_rate:.2%}",
                    f"max_equity_drawdown=${row.max_equity_drawdown:.2f}",
                    f"max_position_exposure=${row.max_position_exposure:.2f}",
                    f"average_edge={row.average_edge:.4f}",
                ]
            )
        )
    best_pnl = max(rows, key=lambda item: item.realized_pnl)
    most_trades = max(rows, key=lambda item: item.accepted_trades)
    lines.append(
        f"Best realized PnL row: {best_pnl.label} | realized_pnl=${best_pnl.realized_pnl:.2f} | accepted_trades={best_pnl.accepted_trades}"
    )
    lines.append(
        f"Most active row: {most_trades.label} | accepted_trades={most_trades.accepted_trades} | realized_pnl=${most_trades.realized_pnl:.2f}"
    )
    return "\n".join(lines)
