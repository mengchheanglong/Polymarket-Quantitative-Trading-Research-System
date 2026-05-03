from __future__ import annotations

from src.storage.sqlite import SQLiteStore


def build_strategy_comparison(store: SQLiteStore) -> str:
    rows = store.rows(
        """
        SELECT
            r.strategy,
            COUNT(*) AS runs,
            COALESCE(SUM(r.realized_pnl), 0) AS realized_pnl,
            COALESCE(SUM(r.accepted_trade_count), 0) AS accepted_trades,
            COALESCE(SUM(r.skipped_opportunity_count), 0) AS skipped_opportunities,
            COALESCE(MAX(r.max_equity_drawdown), 0) AS max_equity_drawdown,
            COALESCE(MAX(r.max_position_exposure), 0) AS max_position_exposure
        FROM runs r
        GROUP BY r.strategy
        ORDER BY r.strategy
        """
    )
    lines = ["Strategy comparison"]
    if not rows:
        lines.append("No runs found.")
        return "\n".join(lines)

    for row in rows:
        strategy = str(row["strategy"])
        win_row = store.rows(
            """
            SELECT
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins,
                COUNT(*) AS closed
            FROM trades t
            JOIN runs r ON r.run_id = t.run_id
            WHERE r.strategy = ? AND t.status = 'CLOSED'
            """,
            (strategy,),
        )[0]
        edge_row = store.rows(
            """
            SELECT AVG(edge) AS average_edge
            FROM opportunities o
            JOIN runs r ON r.run_id = o.run_id
            WHERE r.strategy = ?
            """,
            (strategy,),
        )[0]
        closed = int(win_row["closed"] or 0)
        wins = int(win_row["wins"] or 0)
        win_rate = wins / closed if closed else 0.0
        avg_edge = float(edge_row["average_edge"] or 0.0)
        lines.append(
            " | ".join(
                [
                    strategy,
                    f"runs={int(row['runs'])}",
                    f"realized_pnl=${float(row['realized_pnl']):.2f}",
                    f"win_rate={win_rate:.2%}",
                    f"average_edge={avg_edge:.4f}",
                    f"max_equity_drawdown=${float(row['max_equity_drawdown']):.2f}",
                    f"max_position_exposure=${float(row['max_position_exposure']):.2f}",
                    f"accepted_trades={int(row['accepted_trades'])}",
                    f"skipped_opportunities={int(row['skipped_opportunities'])}",
                ]
            )
        )
    return "\n".join(lines)
