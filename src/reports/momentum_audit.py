from __future__ import annotations

from typing import Any


def build_momentum_audit_report(
    *,
    source_filter: str | None,
    session_id: str | None,
    tiny: bool,
    baseline_rows: list[dict[str, Any]],
    timing_rows: list[dict[str, Any]],
    experiment_rows: list[dict[str, Any]],
) -> str:
    lines = [
        "Momentum audit",
        "Research only: stored-snapshot replay. No external APIs and no execution.",
        f"Source filter: {source_filter or 'all'}",
        f"Session ID: {session_id or 'none'}",
        f"Tiny: {tiny}",
        "Baseline close modes:",
    ]
    for row in baseline_rows:
        lines.append(
            " | ".join(
                [
                    row["label"],
                    f"close_mode={row['close_mode']}",
                    f"accepted={row['accepted']}",
                    f"closed={row['closed']}",
                    f"realized_pnl={_fmt_money(row['realized_pnl'])}",
                    f"win_rate={row['win_rate']:.2%}",
                    f"avg_edge={row['average_edge']:.4f}",
                    f"accepted_edge={_fmt_float(row['average_edge_accepted'])}",
                    f"max_exposure={_fmt_money(row['max_exposure'])}",
                    f"warnings={', '.join(row['warnings']) if row['warnings'] else 'none'}",
                    f"verdicts={', '.join(row['verdicts'])}",
                ]
            )
        )
    lines.append("Timing buckets by close mode:")
    for row in timing_rows:
        lines.append(
            " | ".join(
                [
                    row["close_mode"],
                    row["bucket"],
                    f"closed={row['closed']}",
                    f"wins={row['wins']}",
                    f"win_rate={row['win_rate']:.2%}",
                    f"realized_pnl={_fmt_money(row['realized_pnl'])}",
                    f"avg_entry={_fmt_float(row['avg_entry_price'])}",
                ]
            )
        )
    lines.append("Filter and preset experiments (approximate-expiry):")
    for row in experiment_rows:
        lines.append(
            " | ".join(
                [
                    row["label"],
                    f"accepted={row['accepted']}",
                    f"closed={row['closed']}",
                    f"realized_pnl={_fmt_money(row['realized_pnl'])}",
                    f"win_rate={row['win_rate']:.2%}",
                    f"avg_edge={row['average_edge']:.4f}",
                    f"accepted_edge={_fmt_float(row['average_edge_accepted'])}",
                    f"risk_blocked={row['risk_blocked_trades']}",
                    f"max_exposure={_fmt_money(row['max_exposure'])}",
                    f"verdicts={', '.join(row['verdicts'])}",
                ]
            )
        )
    return "\n".join(lines)


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"
