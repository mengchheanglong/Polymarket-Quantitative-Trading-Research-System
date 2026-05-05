from __future__ import annotations

from typing import Any

from src.reports.conservative_report import ConservativeAggregateRow
from src.reports.signal_audit import SignalAuditRow


def build_side_audit_report(
    *,
    source_filter: str,
    ready_session_count: int,
    conservative_run_count: int,
    missing_session_ids: list[str],
    rows: list[SignalAuditRow],
    summary: dict[str, object],
    run_verdicts: dict[str, tuple[str, ...]],
    details: bool,
) -> str:
    lines = [
        "Side audit",
        "Research only: stored public snapshots, paper replay, no execution.",
        f"Source filter: {source_filter}",
        f"Ready public sessions: {ready_session_count}",
        f"Matched conservative runs: {conservative_run_count}",
        f"Accepted conservative trades: {len(rows)}",
        f"Matched side count: {summary['matched']}",
        f"Mismatched side count: {summary['mismatched']}",
        f"Unknown side count: {summary['unknown']}",
        f"Correctness rate: {_fmt_pct(summary['correctness_rate'])}",
        f"Correctness by asset: {_fmt_breakdown(summary['by_asset'])}",
        f"Correctness by side: {_fmt_breakdown(summary['by_side'])}",
        f"Correctness by duration: {_fmt_breakdown(summary['by_duration'])}",
        f"Correctness by entry price bucket: {_fmt_breakdown(summary['by_entry_price_bucket'])}",
        f"Correctness by seconds-to-expiry bucket: {_fmt_breakdown(summary['by_seconds_to_expiry_bucket'])}",
        f"Correctness by edge bucket: {_fmt_breakdown(summary['by_edge_bucket'])}",
        f"Correctness by spread bucket: {_fmt_breakdown(summary['by_spread_bucket'])}",
        f"Correctness by pre-entry exchange move: {_fmt_breakdown(summary['by_pre_entry_move_bucket'])}",
        f"Correctness by post-entry exchange move: {_fmt_breakdown(summary['by_post_entry_move_bucket'])}",
        f"Mismatch signal-lag flags: {_fmt_map(summary['lag_flags'])}",
    ]
    if missing_session_ids:
        lines.append("Ready sessions without a stored conservative run: " + ", ".join(missing_session_ids))
    if details:
        lines.append("Accepted-trade feature rows:")
        for row in rows:
            matched_label = "unknown"
            if row.side_matched is True:
                matched_label = "matched"
            elif row.side_matched is False:
                matched_label = "mismatched"
            lines.append(
                " | ".join(
                    [
                        f"session_id={row.session_id or 'n/a'}",
                        f"run_id={row.run_id or 'n/a'}",
                        row.market_slug,
                        f"asset={row.asset}",
                        f"duration={row.duration}",
                        f"side={row.side}",
                        f"actual_result={row.actual_result}",
                        f"match_status={matched_label}",
                        f"entry_ts={row.entry_timestamp.isoformat()}",
                        f"expiry_ts={row.expiry_timestamp.isoformat()}",
                        f"seconds_to_expiry={row.seconds_to_expiry:.1f}",
                        f"entry_price={row.entry_price:.4f}",
                        f"spread={_fmt_float(row.spread)}",
                        f"modeled_edge={_fmt_float(row.edge_at_entry)}",
                        f"start_price={_fmt_price(row.start_price)}",
                        f"entry_exchange_price={_fmt_price(row.entry_underlying_price)}",
                        f"expiry_exchange_price={_fmt_price(row.expiry_price)}",
                        f"pre_entry_move={_fmt_pct(row.pre_entry_exchange_move)}",
                        f"post_entry_move={_fmt_pct(row.post_entry_exchange_move)}",
                        f"pnl={_fmt_money(row.pnl)}",
                        f"close_mode={row.close_mode or 'n/a'}",
                        f"verdict_flags={','.join(run_verdicts.get(row.run_id or '', ())) or 'none'}",
                        f"lag_flags={','.join(_lag_flags(row)) or 'none'}",
                    ]
                )
            )
    return "\n".join(lines)


def build_side_sweep_report(
    *,
    source_filter: str,
    session_scope: str,
    rows: list[ConservativeAggregateRow],
) -> str:
    lines = [
        "Side sweep",
        "Research only: stored public snapshots, paper replay, no execution.",
        f"Source filter: {source_filter}",
        f"Session scope: {session_scope}",
    ]
    if not rows:
        lines.append("No side-sweep variants produced a replay result.")
        return "\n".join(lines)
    for row in rows:
        lines.append(_aggregate_line(row))
    return "\n".join(lines)


def build_candidate_ranking_report(
    *,
    source_filter: str,
    session_scope: str,
    rows: list[ConservativeAggregateRow],
) -> str:
    lines = [
        "Candidate ranking",
        "Research only: stored public snapshots, paper replay, no execution.",
        f"Source filter: {source_filter}",
        f"Session scope: {session_scope}",
    ]
    if not rows:
        lines.append("No candidate variants produced a replay result.")
        return "\n".join(lines)
    ranked = sorted(rows, key=_ranking_key, reverse=True)
    for index, row in enumerate(ranked, start=1):
        lines.append(f"{index}. {_aggregate_line(row)}")
    return "\n".join(lines)


def _aggregate_line(row: ConservativeAggregateRow) -> str:
    return " | ".join(
        [
            row.label,
            f"sessions={row.sessions_tested}",
            f"accepted={row.accepted_trades}",
            f"closed={row.closed_trades}",
            f"realized_pnl={_fmt_money(row.realized_pnl)}",
            f"win_rate={row.win_rate:.2%}",
            f"expectancy={_fmt_money(row.expectancy)}",
            f"top_1_pct={_fmt_pct(row.top_1_trade_pct)}",
            f"pnl_ex_top_1={_fmt_money(row.pnl_excluding_top_1)}",
            f"pnl_ex_top_3={_fmt_money(row.pnl_excluding_top_3)}",
            f"max_drawdown={_fmt_money(row.max_drawdown)}",
            f"max_exposure={_fmt_money(row.max_exposure)}",
            f"side_correctness={row.matched}/{row.matched + row.mismatched} ({_fmt_pct(row.side_correctness_rate)})",
            f"verdicts={', '.join(row.verdicts)}",
        ]
    )


def _ranking_key(row: ConservativeAggregateRow) -> tuple[float, ...]:
    has_paper_promising = 1.0 if "PAPER_PROMISING" in row.verdicts else 0.0
    side_rate = row.side_correctness_rate if row.side_correctness_rate is not None else -1.0
    expectancy = row.expectancy if row.expectancy is not None else float("-inf")
    pnl_ex_top_3 = row.pnl_excluding_top_3 if row.pnl_excluding_top_3 is not None else float("-inf")
    top_1_inverse = -(row.top_1_trade_pct if row.top_1_trade_pct is not None else 1.0)
    drawdown_inverse = -row.max_drawdown
    exposure_inverse = -row.max_exposure
    return (
        has_paper_promising,
        side_rate,
        expectancy,
        float(row.closed_trades),
        pnl_ex_top_3,
        top_1_inverse,
        drawdown_inverse,
        exposure_inverse,
    )


def _fmt_breakdown(values: dict[str, dict[str, float | int | None]]) -> str:
    if not values:
        return "none"
    return ", ".join(
        f"{key}={stats['matched']}/{int(stats['matched']) + int(stats['mismatched'])} ({_fmt_pct(stats['correctness_rate'])})"
        for key, stats in sorted(values.items())
    )


def _fmt_map(values: dict[str, Any]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _lag_flags(row: SignalAuditRow) -> list[str]:
    flags: list[str] = []
    post_move = row.post_entry_exchange_move
    if row.side_matched is not False:
        return flags
    if post_move is None:
        return ["unknown"]
    if abs(post_move) < 0.0005:
        flags.append("noisy")
    if row.seconds_to_expiry >= 120:
        flags.append("too_early")
    elif row.seconds_to_expiry <= 60:
        flags.append("too_late")
    if row.edge_at_entry is None or row.edge_at_entry < 0.05:
        flags.append("low_confidence")
    if (row.side == "UP" and post_move < 0) or (row.side == "DOWN" and post_move > 0):
        flags.append("reversed")
    return flags or ["unknown"]
