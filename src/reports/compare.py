from __future__ import annotations

from src.storage.sqlite import SQLiteStore


def build_strategy_comparison(store: SQLiteStore, source_filter: str | None = None) -> str:
    clauses = []
    params: list[str] = []
    if source_filter:
        clauses.append("r.data_source = ?")
        params.append(source_filter)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.rows(
        f"""
        SELECT
            r.strategy,
            COUNT(*) AS runs,
            COALESCE(SUM(r.realized_pnl), 0) AS realized_pnl,
            COALESCE(SUM(r.accepted_trade_count), 0) AS accepted_trades,
            COALESCE(SUM(r.skipped_opportunity_count), 0) AS skipped_opportunities,
            COALESCE(MAX(r.max_equity_drawdown), 0) AS max_equity_drawdown,
            COALESCE(MAX(r.max_position_exposure), 0) AS max_position_exposure
        FROM runs r
        {where}
        GROUP BY r.strategy
        ORDER BY r.strategy
        """,
        tuple(params),
    )
    lines = ["Strategy comparison", f"Source filter: {source_filter or 'all'}"]
    if not rows:
        lines.append("No runs found.")
        return "\n".join(lines)

    lines.append("Latest runs:")
    latest_rows = _latest_rows_by_strategy(store, source_filter)
    for row in latest_rows:
        lines.append(
            " | ".join(
                [
                    str(row["strategy"]),
                    f"run_id={row['run_id']}",
                    f"data_source={row['data_source']}",
                    f"realized_pnl=${float(row['realized_pnl'] or 0.0):.2f}",
                    f"accepted_trades={int(row['accepted_trade_count'] or 0)}",
                    f"skipped_opportunities={int(row['skipped_opportunity_count'] or 0)}",
                ]
            )
        )

    lines.append("Aggregate by strategy:")
    for row in rows:
        strategy = str(row["strategy"])
        filter_clauses = ["r.strategy = ?"]
        filter_params: list[str] = [strategy]
        if source_filter:
            filter_clauses.append("r.data_source = ?")
            filter_params.append(source_filter)
        filter_where = " AND ".join(filter_clauses)
        win_row = store.rows(
            f"""
            SELECT
                SUM(CASE WHEN t.result = 'WIN' THEN 1 ELSE 0 END) AS wins,
                COUNT(*) AS closed
            FROM trades t
            JOIN runs r ON r.run_id = t.run_id
            WHERE {filter_where} AND t.status = 'CLOSED'
            """,
            tuple(filter_params),
        )[0]
        edge_row = store.rows(
            f"""
            SELECT AVG(o.edge) AS average_edge
            FROM opportunities o
            JOIN runs r ON r.run_id = o.run_id
            WHERE {filter_where}
            """,
            tuple(filter_params),
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


def _latest_rows_by_strategy(store: SQLiteStore, source_filter: str | None) -> list:
    if source_filter:
        return store.rows(
            """
            SELECT r.*
            FROM runs r
            JOIN (
                SELECT strategy, MAX(started_at) AS latest_started_at
                FROM runs
                WHERE data_source = ?
                GROUP BY strategy
            ) latest
                ON latest.strategy = r.strategy
               AND latest.latest_started_at = r.started_at
            WHERE r.data_source = ?
            ORDER BY r.strategy, r.started_at DESC, r.rowid DESC
            """,
            (source_filter, source_filter),
        )
    return store.rows(
        """
        SELECT r.*
        FROM runs r
        JOIN (
            SELECT strategy, MAX(started_at) AS latest_started_at
            FROM runs
            GROUP BY strategy
        ) latest
            ON latest.strategy = r.strategy
           AND latest.latest_started_at = r.started_at
        ORDER BY r.strategy, r.started_at DESC, r.rowid DESC
        """
    )
