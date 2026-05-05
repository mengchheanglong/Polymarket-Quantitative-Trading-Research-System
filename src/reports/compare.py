from __future__ import annotations

from collections import defaultdict

from src.reports.config_view import parse_config_notes
from src.storage.sqlite import SQLiteStore


def build_strategy_comparison(
    store: SQLiteStore,
    source_filter: str | None = None,
    session_id: str | None = None,
    active_only: bool = False,
    tiny_only: bool = False,
) -> str:
    clauses = []
    params: list[str] = []
    if source_filter:
        clauses.append("r.data_source = ?")
        params.append(source_filter)
    if session_id:
        clauses.append("r.session_id = ?")
        params.append(session_id)
    if active_only:
        clauses.append("r.notes LIKE ?")
        params.append("%active_only=true%")
    if tiny_only:
        clauses.append("r.notes LIKE ?")
        params.append("%tiny_profile=true%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    run_rows = store.rows(
        f"SELECT * FROM runs r {where} ORDER BY r.started_at DESC, r.rowid DESC",
        tuple(params),
    )
    lines = [
        "Strategy comparison",
        f"Source filter: {source_filter or 'all'}",
        f"Session ID: {session_id or 'none'}",
        f"Active only: {active_only}",
        f"Tiny only: {tiny_only}",
    ]
    if not run_rows:
        lines.append("No runs found.")
        return "\n".join(lines)

    lines.append("Recent runs:")
    for row in run_rows[:10]:
        notes = parse_config_notes(row["notes"])
        lines.append(
            " | ".join(
                [
                    str(row["strategy"]),
                    f"run_id={row['run_id']}",
                    f"data_source={row['data_source']}",
                    f"tiny_profile={notes.get('tiny_profile', 'false')}",
                    f"close_mode={notes.get('close_mode', 'n/a')}",
                    f"realized_pnl=${float(row['realized_pnl'] or 0.0):.2f}",
                    f"accepted_trades={int(row['accepted_trade_count'] or 0)}",
                    f"skipped_opportunities={int(row['skipped_opportunity_count'] or 0)}",
                    f"max_position_exposure=${float(row['max_position_exposure'] or 0.0):.2f}",
                ]
            )
        )

    lines.append("Aggregate by strategy/profile:")
    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
    for row in run_rows:
        notes = parse_config_notes(row["notes"])
        grouped[
            (
                str(row["strategy"]),
                notes.get("tiny_profile", "false"),
                notes.get("close_mode", "n/a"),
            )
        ].append(row)
    for (strategy, tiny_profile, close_mode), rows_for_group in sorted(grouped.items()):
        run_ids = [str(row["run_id"]) for row in rows_for_group]
        placeholders = ",".join("?" for _ in run_ids)
        win_row = store.rows(
            f"""
            SELECT
                SUM(CASE WHEN t.result = 'WIN' THEN 1 ELSE 0 END) AS wins,
                COUNT(*) AS closed
            FROM trades t
            WHERE t.run_id IN ({placeholders}) AND t.status LIKE 'CLOSED%'
            """,
            tuple(run_ids),
        )[0]
        edge_row = store.rows(
            f"""
            SELECT AVG(o.edge) AS average_edge
            FROM opportunities o
            WHERE o.run_id IN ({placeholders})
            """,
            tuple(run_ids),
        )[0]
        realized_pnl = sum(float(row["realized_pnl"] or 0.0) for row in rows_for_group)
        accepted = sum(int(row["accepted_trade_count"] or 0) for row in rows_for_group)
        skipped = sum(int(row["skipped_opportunity_count"] or 0) for row in rows_for_group)
        max_drawdown = max(float(row["max_equity_drawdown"] or 0.0) for row in rows_for_group)
        max_exposure = max(float(row["max_position_exposure"] or 0.0) for row in rows_for_group)
        closed = int(win_row["closed"] or 0)
        wins = int(win_row["wins"] or 0)
        win_rate = wins / closed if closed else 0.0
        avg_edge = float(edge_row["average_edge"] or 0.0)
        lines.append(
            " | ".join(
                [
                    strategy,
                    f"tiny_profile={tiny_profile}",
                    f"close_mode={close_mode}",
                    f"runs={len(rows_for_group)}",
                    f"realized_pnl=${realized_pnl:.2f}",
                    f"win_rate={win_rate:.2%}",
                    f"average_edge={avg_edge:.4f}",
                    f"max_equity_drawdown=${max_drawdown:.2f}",
                    f"max_position_exposure=${max_exposure:.2f}",
                    f"accepted_trades={accepted}",
                    f"skipped_opportunities={skipped}",
                ]
            )
        )
    return "\n".join(lines)
