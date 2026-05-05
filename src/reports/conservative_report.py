from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ConservativeSessionRow:
    session_id: str
    accepted_trades: int
    closed_trades: int
    realized_pnl: float
    win_rate: float
    expectancy: float | None
    max_drawdown: float
    max_exposure: float
    top_1_trade_pct: float | None
    pnl_excluding_top_1: float | None
    pnl_excluding_top_3: float | None
    settlement_unavailable: int
    matched: int
    mismatched: int
    unknown: int
    side_correctness_rate: float | None
    warnings: tuple[str, ...]
    verdicts: tuple[str, ...]


@dataclass(frozen=True)
class ConservativeAggregateRow:
    label: str
    sessions_tested: int
    accepted_trades: int
    closed_trades: int
    realized_pnl: float
    win_rate: float
    expectancy: float | None
    max_drawdown: float
    max_exposure: float
    top_1_trade_pct: float | None
    pnl_excluding_top_1: float | None
    pnl_excluding_top_3: float | None
    settlement_unavailable: int
    matched: int
    mismatched: int
    unknown: int
    side_correctness_rate: float | None
    warnings: tuple[str, ...]
    verdicts: tuple[str, ...]


def build_conservative_report(
    *,
    source_filter: str,
    preset_summary: str,
    session_rows: list[ConservativeSessionRow],
    aggregate_row: ConservativeAggregateRow,
    variant_rows: list[ConservativeAggregateRow],
    by_asset: dict[str, dict[str, float | int | None]],
    by_duration: dict[str, dict[str, float | int | None]],
    by_side: dict[str, dict[str, float | int | None]],
) -> str:
    lines = [
        "Conservative report",
        "Research only: stored public snapshots, paper replay, no execution.",
        f"Source filter: {source_filter}",
        f"Preset: {preset_summary}",
        f"Sessions tested: {len(session_rows)}",
    ]
    if not session_rows:
        lines.append("No public research sessions produced a conservative replay result.")
        return "\n".join(lines)

    lines.append("Per-session conservative results:")
    for row in session_rows:
        lines.append(
            " | ".join(
                [
                    row.session_id,
                    f"accepted={row.accepted_trades}",
                    f"closed={row.closed_trades}",
                    f"realized_pnl={_fmt_money(row.realized_pnl)}",
                    f"win_rate={row.win_rate:.2%}",
                    f"expectancy={_fmt_money(row.expectancy)}",
                    f"max_drawdown={_fmt_money(row.max_drawdown)}",
                    f"max_exposure={_fmt_money(row.max_exposure)}",
                    f"top_1_pct={_fmt_pct(row.top_1_trade_pct)}",
                    f"pnl_ex_top_1={_fmt_money(row.pnl_excluding_top_1)}",
                    f"pnl_ex_top_3={_fmt_money(row.pnl_excluding_top_3)}",
                    f"settlement_unavailable={row.settlement_unavailable}",
                    f"side_correctness={row.matched}/{row.matched + row.mismatched} ({_fmt_pct(row.side_correctness_rate)})",
                    f"verdicts={', '.join(row.verdicts)}",
                ]
            )
        )

    lines.append("Aggregate conservative result:")
    lines.append(_aggregate_line(aggregate_row))
    lines.append("BTC/ETH and duration comparisons:")
    for row in variant_rows:
        lines.append(_aggregate_line(row))

    lines.append("Side correctness by asset:")
    lines.extend(_breakdown_lines(by_asset))
    lines.append("Side correctness by duration:")
    lines.extend(_breakdown_lines(by_duration))
    lines.append("Side correctness by side:")
    lines.extend(_breakdown_lines(by_side))
    lines.append(f"Aggregate verdict: {', '.join(aggregate_row.verdicts)}")
    return "\n".join(lines)


def conservative_readiness_verdict(
    *,
    closed_trades: int,
    realized_pnl: float,
    expectancy: float | None,
    pnl_excluding_top_3: float | None,
    top_1_trade_pct: float | None,
    side_correctness_rate: float | None,
    max_drawdown: float,
    drawdown_limit: float | None,
    safety_violation: bool = False,
) -> tuple[str, ...]:
    if (
        not safety_violation
        and closed_trades >= 50
        and realized_pnl > 0
        and (expectancy or 0.0) > 0
        and (pnl_excluding_top_3 or 0.0) > 0
        and (top_1_trade_pct is None or top_1_trade_pct < 0.40)
        and (side_correctness_rate or 0.0) > 0.55
        and (drawdown_limit is None or max_drawdown <= drawdown_limit)
    ):
        return ("PAPER_PROMISING",)

    verdicts: list[str] = []
    if closed_trades < 10:
        verdicts.append("INSUFFICIENT_CLOSED_TRADES")
    if closed_trades < 50:
        verdicts.append("NEEDS_MORE_DATA")
    if realized_pnl <= 0 or (expectancy is not None and expectancy <= 0):
        verdicts.append("NEGATIVE_EXPECTANCY")
    if (top_1_trade_pct is not None and top_1_trade_pct >= 0.40) or (
        pnl_excluding_top_3 is not None and pnl_excluding_top_3 <= 0
    ):
        verdicts.append("TAIL_RISK_DOMINATED")
    if side_correctness_rate is not None and side_correctness_rate <= 0.55:
        verdicts.append("DIRECTIONAL_SIGNAL_FAILED")
    if drawdown_limit is not None and max_drawdown > drawdown_limit:
        verdicts.append("NEEDS_MORE_DATA")
    return tuple(dict.fromkeys(verdicts or ["NEEDS_MORE_DATA"]))


def aggregate_variant_row(
    *,
    label: str,
    session_rows: list[dict],
) -> ConservativeAggregateRow:
    trade_rows = [trade for session in session_rows for trade in session["trade_rows"]]
    closed_rows = [row for row in trade_rows if row["pnl"] is not None]
    win_rows = [row for row in closed_rows if float(row["pnl"] or 0.0) > 0.0]
    matched = sum(int(session["summary"]["matched"]) for session in session_rows)
    mismatched = sum(int(session["summary"]["mismatched"]) for session in session_rows)
    unknown = sum(int(session["summary"]["unknown"]) for session in session_rows)
    resolved = matched + mismatched
    concentration = _profit_concentration(closed_rows)
    realized_pnl = sum(float(row["pnl"] or 0.0) for row in closed_rows)
    expectancy = realized_pnl / len(closed_rows) if closed_rows else None
    warnings = _aggregate_warnings(session_rows)
    verdicts = conservative_readiness_verdict(
        closed_trades=len(closed_rows),
        realized_pnl=realized_pnl,
        expectancy=expectancy,
        pnl_excluding_top_3=concentration["pnl_excluding_top_3"],
        top_1_trade_pct=concentration["top_1_trade_pct"],
        side_correctness_rate=(matched / resolved) if resolved else None,
        max_drawdown=max((float(session["report"].max_equity_drawdown) for session in session_rows), default=0.0),
        drawdown_limit=min(
            (
                float(session["drawdown_limit"])
                for session in session_rows
                if session["drawdown_limit"] is not None
            ),
            default=None,
        ),
    )
    return ConservativeAggregateRow(
        label=label,
        sessions_tested=len(session_rows),
        accepted_trades=sum(int(session["accepted"]) for session in session_rows),
        closed_trades=len(closed_rows),
        realized_pnl=realized_pnl,
        win_rate=(len(win_rows) / len(closed_rows)) if closed_rows else 0.0,
        expectancy=expectancy,
        max_drawdown=max((float(session["report"].max_equity_drawdown) for session in session_rows), default=0.0),
        max_exposure=max((float(session["report"].max_position_exposure) for session in session_rows), default=0.0),
        top_1_trade_pct=concentration["top_1_trade_pct"],
        pnl_excluding_top_1=concentration["pnl_excluding_top_1"],
        pnl_excluding_top_3=concentration["pnl_excluding_top_3"],
        settlement_unavailable=sum(int(session["report"].settlement_unavailable) for session in session_rows),
        matched=matched,
        mismatched=mismatched,
        unknown=unknown,
        side_correctness_rate=(matched / resolved) if resolved else None,
        warnings=warnings,
        verdicts=verdicts,
    )


def _aggregate_warnings(session_rows: list[dict]) -> tuple[str, ...]:
    warnings: list[str] = []
    for session in session_rows:
        warnings.extend(str(value) for value in session["warnings"])
    return tuple(dict.fromkeys(warnings))


def _profit_concentration(closed_rows) -> dict[str, float | None]:
    pnls = sorted((float(row["pnl"] or 0.0) for row in closed_rows), reverse=True)
    realized_pnl = sum(pnls)
    if not pnls:
        return {
            "top_1_trade_pct": None,
            "pnl_excluding_top_1": None,
            "pnl_excluding_top_3": None,
        }
    top_1 = pnls[0]
    top_3 = sum(pnls[:3])
    return {
        "top_1_trade_pct": (top_1 / realized_pnl) if realized_pnl else None,
        "pnl_excluding_top_1": realized_pnl - top_1,
        "pnl_excluding_top_3": realized_pnl - top_3,
    }


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
            f"max_drawdown={_fmt_money(row.max_drawdown)}",
            f"max_exposure={_fmt_money(row.max_exposure)}",
            f"top_1_pct={_fmt_pct(row.top_1_trade_pct)}",
            f"pnl_ex_top_1={_fmt_money(row.pnl_excluding_top_1)}",
            f"pnl_ex_top_3={_fmt_money(row.pnl_excluding_top_3)}",
            f"settlement_unavailable={row.settlement_unavailable}",
            f"side_correctness={row.matched}/{row.matched + row.mismatched} ({_fmt_pct(row.side_correctness_rate)})",
            f"verdicts={', '.join(row.verdicts)}",
        ]
    )


def _breakdown_lines(values: dict[str, dict[str, float | int | None]]) -> list[str]:
    if not values:
        return ["none"]
    lines = []
    for key, stats in sorted(values.items()):
        matched = int(stats["matched"])
        mismatched = int(stats["mismatched"])
        unknown = int(stats["unknown"])
        rate = stats["correctness_rate"]
        lines.append(
            f"{key}: matched={matched}, mismatched={mismatched}, unknown={unknown}, correctness={_fmt_pct(rate)}"
        )
    return lines


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.2%}"
