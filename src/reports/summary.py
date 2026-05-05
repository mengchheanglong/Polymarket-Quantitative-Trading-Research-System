from __future__ import annotations

import math
from dataclasses import dataclass

from src.reports.config_view import format_config_view, merged_config_view
from src.simulator.engine import RISK_BLOCK_REASONS
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
    max_position_exposure_pct: float
    open_positions: int
    closed_trades: int
    unresolved_positions: int
    settlement_unavailable: int
    approximate_expiry_settlements: int
    mark_to_market_settlements: int
    skipped_trades: int
    risk_blocked_trades: int
    win_rate: float
    average_edge: float
    average_pnl_per_trade: float | None
    average_win: float | None
    average_loss: float | None
    profit_factor: float | None
    expectancy_per_trade: float | None
    session_loss_limit_status: str
    top_1_trade_pnl: float | None
    top_3_trades_pnl: float | None
    top_5_trades_pnl: float | None
    top_10pct_trades_pnl: float | None
    top_1_trade_pct_of_total_pnl: float | None
    top_3_trades_pct_of_total_pnl: float | None
    top_10pct_trades_pct_of_total_pnl: float | None
    pnl_excluding_top_1: float | None
    pnl_excluding_top_3: float | None
    pnl_excluding_top_10pct: float | None
    median_trade_pnl: float | None
    bottom_10pct_trades_pnl: float | None
    largest_loss: float | None
    largest_win: float | None
    win_loss_payout_ratio: float | None
    low_price_trade_count_below_0_05: int
    low_price_trade_count_below_0_03: int
    pnl_from_entry_price_below_0_05: float
    pnl_from_entry_price_below_0_03: float
    warnings: tuple[str, ...]
    verdicts: tuple[str, ...]

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
                f"Max position exposure pct: {self.max_position_exposure_pct:.2%}",
                f"Open positions: {self.open_positions}",
                f"Closed trades: {self.closed_trades}",
                f"Unresolved positions: {self.unresolved_positions}",
                f"Settlement unavailable: {self.settlement_unavailable}",
                f"Approximate expiry settlements: {self.approximate_expiry_settlements}",
                f"Mark-to-market settlements: {self.mark_to_market_settlements}",
                f"Skipped trades: {self.skipped_trades}",
                f"Risk-blocked trades: {self.risk_blocked_trades}",
                f"Win rate: {self.win_rate:.2%}",
                f"Average edge: {self.average_edge:.4f}",
                f"Average PnL per trade: {_fmt_money(self.average_pnl_per_trade)}",
                f"Average win: {_fmt_money(self.average_win)}",
                f"Average loss: {_fmt_money(self.average_loss)}",
                f"Profit factor: {_fmt_ratio(self.profit_factor)}",
                f"Expectancy per trade: {_fmt_money(self.expectancy_per_trade)}",
                f"Session loss limit status: {self.session_loss_limit_status}",
                f"Top 1 trade PnL: {_fmt_money(self.top_1_trade_pnl)}",
                f"Top 3 trades PnL: {_fmt_money(self.top_3_trades_pnl)}",
                f"Top 5 trades PnL: {_fmt_money(self.top_5_trades_pnl)}",
                f"Top 10% trades PnL: {_fmt_money(self.top_10pct_trades_pnl)}",
                f"Top 1 trade pct of total PnL: {_fmt_pct(self.top_1_trade_pct_of_total_pnl)}",
                f"Top 3 trades pct of total PnL: {_fmt_pct(self.top_3_trades_pct_of_total_pnl)}",
                f"Top 10% trades pct of total PnL: {_fmt_pct(self.top_10pct_trades_pct_of_total_pnl)}",
                f"PnL excluding top 1 trade: {_fmt_money(self.pnl_excluding_top_1)}",
                f"PnL excluding top 3 trades: {_fmt_money(self.pnl_excluding_top_3)}",
                f"PnL excluding top 10% trades: {_fmt_money(self.pnl_excluding_top_10pct)}",
                f"Median trade PnL: {_fmt_money(self.median_trade_pnl)}",
                f"Bottom 10% trades PnL: {_fmt_money(self.bottom_10pct_trades_pnl)}",
                f"Largest loss: {_fmt_money(self.largest_loss)}",
                f"Largest win: {_fmt_money(self.largest_win)}",
                f"Win/loss payout ratio: {_fmt_ratio(self.win_loss_payout_ratio)}",
                f"Trades with entry price < 0.05: {self.low_price_trade_count_below_0_05}",
                f"Trades with entry price < 0.03: {self.low_price_trade_count_below_0_03}",
                f"PnL from entry price < 0.05: {_fmt_money(self.pnl_from_entry_price_below_0_05)}",
                f"PnL from entry price < 0.03: {_fmt_money(self.pnl_from_entry_price_below_0_03)}",
                f"Warnings: {', '.join(self.warnings) if self.warnings else 'none'}",
                f"Verdicts: {', '.join(self.verdicts) if self.verdicts else 'RESEARCH_ONLY_VALID'}",
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
            f"SELECT * FROM trades WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        )
        open_rows = store.rows(
            f"SELECT * FROM trades WHERE status = 'OPEN' AND run_id IN ({placeholders})",
            tuple(run_ids),
        )
    else:
        trade_rows = store.rows("SELECT result, pnl, status, close_mode FROM trades")
        open_rows = store.open_trades()
    closed_rows = [row for row in trade_rows if str(row["status"]).startswith("CLOSED")]
    unresolved_rows = [row for row in trade_rows if row["status"] == "EXPIRED_UNRESOLVED"]
    settlement_unavailable_rows = [row for row in trade_rows if row["status"] == "SETTLEMENT_UNAVAILABLE"]
    closed = len(closed_rows)
    wins = sum(1 for row in closed_rows if row["result"] == "WIN")
    loss_rows = [row for row in closed_rows if float(row["pnl"] or 0.0) < 0.0]
    win_rows = [row for row in closed_rows if float(row["pnl"] or 0.0) > 0.0]
    realized_pnl = sum(float(row["pnl"] or 0.0) for row in closed_rows)
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
    average_pnl_per_trade = realized_pnl / closed if closed else None
    average_win = sum(float(row["pnl"] or 0.0) for row in win_rows) / len(win_rows) if win_rows else None
    average_loss = sum(float(row["pnl"] or 0.0) for row in loss_rows) / len(loss_rows) if loss_rows else None
    gross_wins = sum(float(row["pnl"] or 0.0) for row in win_rows)
    gross_losses = abs(sum(float(row["pnl"] or 0.0) for row in loss_rows))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (None if gross_wins == 0 else float("inf"))
    expectancy_per_trade = average_pnl_per_trade
    risk_blocked = sum(1 for row in skip_reason_rows(skip_source=run_ids, store=store) if row["reason"] in RISK_BLOCK_REASONS)
    config_values = merged_config_view([row["notes"] for row in run_rows])
    session_loss_limit = _float_or_none(config_values.get("session_loss_limit_usd"))
    close_mode_value = config_values.get("close_mode")
    session_loss_limit_status = (
        "TRIGGERED"
        if session_loss_limit is not None and realized_pnl <= -session_loss_limit
        else "CLEAR"
    )
    concentration = _profit_concentration(closed_rows, realized_pnl)
    low_price = _low_price_contribution(closed_rows)
    warnings, verdicts = _run_warnings_and_verdicts(
        realized_pnl=realized_pnl,
        closed_trades=closed,
        average_edge=avg_edge,
        expectancy_per_trade=expectancy_per_trade,
        average_win=average_win,
        average_loss=average_loss,
        profit_factor=profit_factor,
        concentration=concentration,
        low_price=low_price,
        close_mode=close_mode_value,
        approximate_expiry_settlements=sum(1 for row in closed_rows if row["close_mode"] == "approximate-expiry"),
    )
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
        max_position_exposure_pct=(max_exposure / starting_balance) if starting_balance else 0.0,
        open_positions=len(open_rows),
        closed_trades=closed,
        unresolved_positions=len(unresolved_rows),
        settlement_unavailable=len(settlement_unavailable_rows),
        approximate_expiry_settlements=sum(1 for row in closed_rows if row["close_mode"] == "approximate-expiry"),
        mark_to_market_settlements=sum(1 for row in closed_rows if row["status"] == "CLOSED_BY_MARK_TO_MARKET"),
        skipped_trades=int(skipped),
        risk_blocked_trades=risk_blocked,
        win_rate=wins / closed if closed else 0.0,
        average_edge=avg_edge,
        average_pnl_per_trade=average_pnl_per_trade,
        average_win=average_win,
        average_loss=average_loss,
        profit_factor=profit_factor,
        expectancy_per_trade=expectancy_per_trade,
        session_loss_limit_status=session_loss_limit_status,
        top_1_trade_pnl=concentration["top_1_trade_pnl"],
        top_3_trades_pnl=concentration["top_3_trades_pnl"],
        top_5_trades_pnl=concentration["top_5_trades_pnl"],
        top_10pct_trades_pnl=concentration["top_10pct_trades_pnl"],
        top_1_trade_pct_of_total_pnl=concentration["top_1_trade_pct_of_total_pnl"],
        top_3_trades_pct_of_total_pnl=concentration["top_3_trades_pct_of_total_pnl"],
        top_10pct_trades_pct_of_total_pnl=concentration["top_10pct_trades_pct_of_total_pnl"],
        pnl_excluding_top_1=concentration["pnl_excluding_top_1"],
        pnl_excluding_top_3=concentration["pnl_excluding_top_3"],
        pnl_excluding_top_10pct=concentration["pnl_excluding_top_10pct"],
        median_trade_pnl=concentration["median_trade_pnl"],
        bottom_10pct_trades_pnl=concentration["bottom_10pct_trades_pnl"],
        largest_loss=concentration["largest_loss"],
        largest_win=concentration["largest_win"],
        win_loss_payout_ratio=concentration["win_loss_payout_ratio"],
        low_price_trade_count_below_0_05=low_price["count_below_0_05"],
        low_price_trade_count_below_0_03=low_price["count_below_0_03"],
        pnl_from_entry_price_below_0_05=low_price["pnl_below_0_05"],
        pnl_from_entry_price_below_0_03=low_price["pnl_below_0_03"],
        warnings=warnings,
        verdicts=verdicts,
    )


def skip_reason_rows(*, skip_source: list[str], store: SQLiteStore):
    if skip_source:
        placeholders = ",".join("?" for _ in skip_source)
        return store.rows(
            f"SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id IN ({placeholders})",
            tuple(skip_source),
        )
    return store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP'")


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


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _float_or_none(value: str | None) -> float | None:
    if value in (None, "", "none", "n/a", "mixed"):
        return None
    return float(value)


def _profit_concentration(closed_rows, realized_pnl: float) -> dict[str, float | None]:
    pnl_values = sorted((float(row["pnl"] or 0.0) for row in closed_rows), reverse=True)
    if not pnl_values:
        return {
            "top_1_trade_pnl": None,
            "top_3_trades_pnl": None,
            "top_5_trades_pnl": None,
            "top_10pct_trades_pnl": None,
            "top_1_trade_pct_of_total_pnl": None,
            "top_3_trades_pct_of_total_pnl": None,
            "top_10pct_trades_pct_of_total_pnl": None,
            "pnl_excluding_top_1": None,
            "pnl_excluding_top_3": None,
            "pnl_excluding_top_10pct": None,
            "median_trade_pnl": None,
            "bottom_10pct_trades_pnl": None,
            "largest_loss": None,
            "largest_win": None,
            "win_loss_payout_ratio": None,
        }
    top10_count = max(1, math.ceil(len(pnl_values) * 0.1))
    bottom10_count = max(1, math.ceil(len(pnl_values) * 0.1))
    top_1 = sum(pnl_values[:1])
    top_3 = sum(pnl_values[:3])
    top_5 = sum(pnl_values[:5])
    top_10pct = sum(pnl_values[:top10_count])
    bottom_10pct = sum(sorted(pnl_values)[:bottom10_count])
    total_for_pct = realized_pnl if realized_pnl > 0 else None
    wins = [value for value in pnl_values if value > 0]
    losses = [value for value in pnl_values if value < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = abs(sum(losses) / len(losses)) if losses else None
    payout_ratio = (avg_win / avg_loss) if avg_win is not None and avg_loss not in (None, 0) else None
    return {
        "top_1_trade_pnl": top_1,
        "top_3_trades_pnl": top_3,
        "top_5_trades_pnl": top_5,
        "top_10pct_trades_pnl": top_10pct,
        "top_1_trade_pct_of_total_pnl": (top_1 / total_for_pct) if total_for_pct else None,
        "top_3_trades_pct_of_total_pnl": (top_3 / total_for_pct) if total_for_pct else None,
        "top_10pct_trades_pct_of_total_pnl": (top_10pct / total_for_pct) if total_for_pct else None,
        "pnl_excluding_top_1": realized_pnl - top_1,
        "pnl_excluding_top_3": realized_pnl - top_3,
        "pnl_excluding_top_10pct": realized_pnl - top_10pct,
        "median_trade_pnl": pnl_values[len(pnl_values) // 2] if len(pnl_values) % 2 == 1 else (pnl_values[len(pnl_values)//2 - 1] + pnl_values[len(pnl_values)//2]) / 2.0,
        "bottom_10pct_trades_pnl": bottom_10pct,
        "largest_loss": min(pnl_values),
        "largest_win": max(pnl_values),
        "win_loss_payout_ratio": payout_ratio,
    }


def _low_price_contribution(closed_rows) -> dict[str, float | int]:
    low_05 = [row for row in closed_rows if float(row["entry_price"] or 0.0) < 0.05]
    low_03 = [row for row in closed_rows if float(row["entry_price"] or 0.0) < 0.03]
    return {
        "count_below_0_05": len(low_05),
        "count_below_0_03": len(low_03),
        "pnl_below_0_05": sum(float(row["pnl"] or 0.0) for row in low_05),
        "pnl_below_0_03": sum(float(row["pnl"] or 0.0) for row in low_03),
    }


def _run_warnings_and_verdicts(
    *,
    realized_pnl: float,
    closed_trades: int,
    average_edge: float,
    expectancy_per_trade: float | None,
    average_win: float | None,
    average_loss: float | None,
    profit_factor: float | None,
    concentration: dict[str, float | None],
    low_price: dict[str, float | int],
    close_mode: str | None,
    approximate_expiry_settlements: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    warnings: list[str] = []
    verdicts: list[str] = []
    top1_pct = concentration["top_1_trade_pct_of_total_pnl"]
    top3_pct = concentration["top_3_trades_pct_of_total_pnl"]
    pnl_ex_top3 = concentration["pnl_excluding_top_3"]
    tail_risk = False
    if (
        (top1_pct is not None and top1_pct > 0.50)
        or (top3_pct is not None and top3_pct > 0.80)
        or (pnl_ex_top3 is not None and pnl_ex_top3 < 0)
        or (average_edge < 0 and realized_pnl > 0)
        or (profit_factor is not None and profit_factor != float("inf") and profit_factor > 10.0)
    ):
        warnings.append("TAIL_RISK_CONCENTRATED_PROFIT")
        tail_risk = True
    low_price_warning = False
    if realized_pnl > 0:
        if low_price["pnl_below_0_05"] > 0.5 * realized_pnl or low_price["pnl_below_0_03"] > 0.3 * realized_pnl:
            warnings.append("LOW_PRICE_BINARY_TAIL_STRATEGY")
            low_price_warning = True
    if tail_risk or low_price_warning:
        verdicts.append("TAIL_RISK_DOMINATED")
    if close_mode == "approximate-expiry" or approximate_expiry_settlements > 0:
        verdicts.append("SETTLEMENT_APPROXIMATION_UNCERTAIN")
    if closed_trades < 10:
        verdicts.append("INSUFFICIENT_CLOSED_TRADES")
    elif closed_trades < 30:
        verdicts.append("NEEDS_LONGER_SAMPLE")
    if expectancy_per_trade is not None and expectancy_per_trade < 0:
        verdicts.append("NEGATIVE_EXPECTANCY")
    if not verdicts:
        verdicts.append("RESEARCH_ONLY_VALID")
    return tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(verdicts))
