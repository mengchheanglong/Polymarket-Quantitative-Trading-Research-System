from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.reports.summary import Report, build_report
from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class BacktestReport:
    run_id: str | None
    strategy: str
    mode: str
    source_filter: str
    active_only: bool
    config_summary: str
    actual_sources: dict[str, int]
    demo_included: bool
    public_included: bool
    readiness_verdict: str
    snapshots: int
    exchange_price_snapshots: int
    orderbook_snapshots: int
    markets_seen: int
    opportunities: int
    accepted_fake_trades: int
    skipped_opportunities: int
    realized_fake_pnl: float
    win_rate: float
    max_equity_drawdown: float
    max_position_exposure: float
    average_edge: float
    average_edge_accepted: float | None
    average_edge_skipped: float | None
    accepted_edge_min: float | None
    accepted_edge_median: float | None
    accepted_edge_max: float | None
    accepted_trades_by_side: dict[str, int]
    accepted_trades_by_asset: dict[str, int]
    accepted_trades_by_duration: dict[str, int]
    accepted_trades_by_seconds_bucket: dict[str, int]
    accepted_trades_by_entry_price_bucket: dict[str, int]
    average_pnl_per_trade: float | None
    average_win: float | None
    average_loss: float | None
    profit_factor: float | None
    expectancy_per_trade: float | None
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
    open_positions: int
    unresolved_positions: int
    settlement_unavailable: int
    approximate_expiry_settlements: int
    mark_to_market_settlements: int
    risk_blocked_trades: int
    session_loss_limit_status: str
    warnings: tuple[str, ...]
    verdicts: tuple[str, ...]
    source_coverage: dict[str, int]
    quality_metrics: dict

    def as_text(self) -> str:
        return "\n".join(
            [
                "Backtest/replay report",
                f"Run ID: {self.run_id or 'n/a'}",
                f"Strategy: {self.strategy}",
                f"Mode: {self.mode}",
                f"Source filter: {self.source_filter}",
                f"Active only: {self.active_only}",
                f"Config: {self.config_summary}",
                f"Actual data sources: {_format_map(self.actual_sources)}",
                f"Includes demo data: {self.demo_included}",
                f"Includes public data: {self.public_included}",
                f"Readiness verdict: {self.readiness_verdict}",
                f"Snapshots collected: {self.snapshots}",
                f"Exchange price snapshots: {self.exchange_price_snapshots}",
                f"Orderbook snapshots: {self.orderbook_snapshots}",
                f"Markets seen: {self.markets_seen}",
                f"Opportunities: {self.opportunities}",
                f"Accepted fake trades: {self.accepted_fake_trades}",
                f"Skipped opportunities: {self.skipped_opportunities}",
                f"Realized fake PnL: ${self.realized_fake_pnl:.2f}",
                f"Open positions: {self.open_positions}",
                f"Unresolved positions: {self.unresolved_positions}",
                f"Settlement unavailable: {self.settlement_unavailable}",
                f"Approximate expiry settlements: {self.approximate_expiry_settlements}",
                f"Mark-to-market settlements: {self.mark_to_market_settlements}",
                f"Win rate: {self.win_rate:.2%}",
                f"Max equity drawdown: ${self.max_equity_drawdown:.2f}",
                f"Max position exposure: ${self.max_position_exposure:.2f}",
                f"Average edge: {self.average_edge:.4f}",
                f"Average accepted-trade edge: {_fmt_float(self.average_edge_accepted)}",
                f"Average skipped-trade edge: {_fmt_float(self.average_edge_skipped)}",
                f"Accepted edge min/median/max: {_fmt_float(self.accepted_edge_min)} / {_fmt_float(self.accepted_edge_median)} / {_fmt_float(self.accepted_edge_max)}",
                f"Accepted trades by side: {_format_map(self.accepted_trades_by_side)}",
                f"Accepted trades by asset: {_format_map(self.accepted_trades_by_asset)}",
                f"Accepted trades by duration: {_format_map(self.accepted_trades_by_duration)}",
                f"Accepted trades by seconds-to-expiry bucket: {_format_map(self.accepted_trades_by_seconds_bucket)}",
                f"Accepted trades by entry price bucket: {_format_map(self.accepted_trades_by_entry_price_bucket)}",
                f"Average PnL per trade: {_fmt_money(self.average_pnl_per_trade)}",
                f"Average win: {_fmt_money(self.average_win)}",
                f"Average loss: {_fmt_money(self.average_loss)}",
                f"Profit factor: {_fmt_ratio(self.profit_factor)}",
                f"Expectancy per trade: {_fmt_money(self.expectancy_per_trade)}",
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
                f"Risk-blocked trades: {self.risk_blocked_trades}",
                f"Session loss limit status: {self.session_loss_limit_status}",
                f"Warnings: {', '.join(self.warnings) if self.warnings else 'none'}",
                f"Verdicts: {', '.join(self.verdicts)}",
                f"Source coverage: {_format_map(self.source_coverage)}",
                f"Failed collection attempts: {self.quality_metrics['failed_collection_attempts']}",
                f"Total stale snapshots: {self.quality_metrics['total_stale_snapshots']}",
                f"Stale exchange prices: {self.quality_metrics['stale_exchange_prices']}",
                f"Stale orderbooks: {self.quality_metrics['stale_orderbooks']}",
                f"Expired markets seen: {self.quality_metrics['expired_markets_seen']}",
                f"Invalid timestamps: {self.quality_metrics['invalid_timestamps']}",
                f"Missing orderbooks: {self.quality_metrics['missing_orderbooks']}",
                f"Missing prices: {self.quality_metrics['missing_prices']}",
                f"Wide spreads: {self.quality_metrics['wide_spreads']}",
                f"Low liquidity markets: {self.quality_metrics['low_liquidity_markets']}",
                f"Skipped by reason: {_format_map(self.quality_metrics['skipped_by_reason'])}",
            ]
        )


def build_backtest_report(
    store: SQLiteStore,
    starting_balance: float,
    strategy: str,
    source_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
    active_only: bool = False,
    tiny_only: bool = False,
) -> BacktestReport:
    run_id = _latest_run_id_for_source(store, source_filter, session_id, active_only, tiny_only)
    if run_id:
        report = build_report(store, starting_balance, run_id=run_id)
    elif source_filter:
        report = _empty_report(source_filter, starting_balance)
    else:
        report = build_report(store, starting_balance)
    quality = store.data_quality_metrics(source_filter=source_filter, since=since, until=until, session_id=session_id)
    dataset = store.dataset_summary(source_filter=source_filter, since=since, until=until, session_id=session_id)
    readiness = store.readiness(source_filter=source_filter or "all", since=since, until=until, session_id=session_id)
    run = store.run_by_id(run_id) if run_id else None
    if run_id:
        opportunities = store.rows(
            "SELECT COUNT(*) AS count FROM opportunities WHERE run_id = ?",
            (run_id,),
        )[0]["count"]
        accepted = store.rows("SELECT COUNT(*) AS count FROM trades WHERE run_id = ?", (run_id,))[0]["count"]
        skip_rows = store.rows(
            """
            SELECT reason, COUNT(*) AS count
            FROM opportunities
            WHERE run_id = ? AND decision = 'SKIP'
            GROUP BY reason
            ORDER BY count DESC, reason
            """,
            (run_id,),
        )
        quality = {**quality, "skipped_by_reason": {str(row["reason"]): int(row["count"]) for row in skip_rows}}
    else:
        if source_filter:
            opportunities = 0
            accepted = 0
        else:
            opportunities = store.rows("SELECT COUNT(*) AS count FROM opportunities")[0]["count"]
            accepted = store.rows("SELECT COUNT(*) AS count FROM trades")[0]["count"]
    markets = len(store.collected_markets(source_filter=source_filter, since=since, until=until, session_id=session_id))
    return BacktestReport(
        run_id=run_id,
        strategy=strategy,
        mode=str(run["mode"]) if run else "n/a",
        source_filter=source_filter or "all",
        active_only=active_only,
        config_summary=report.config_summary,
        actual_sources=quality["source_coverage"],
        demo_included=dataset["demo_included"],
        public_included=dataset["public_included"],
        readiness_verdict=readiness["verdict"],
        snapshots=quality["snapshots_collected"],
        exchange_price_snapshots=dataset["exchange_price_snapshots"],
        orderbook_snapshots=dataset["orderbook_snapshots"],
        markets_seen=int(markets),
        opportunities=int(opportunities),
        accepted_fake_trades=int(accepted),
        skipped_opportunities=report.skipped_trades,
        realized_fake_pnl=report.realized_pnl,
        win_rate=report.win_rate,
        max_equity_drawdown=report.max_equity_drawdown,
        max_position_exposure=report.max_position_exposure,
        average_edge=report.average_edge,
        average_edge_accepted=report.average_edge_accepted,
        average_edge_skipped=report.average_edge_skipped,
        accepted_edge_min=report.accepted_edge_min,
        accepted_edge_median=report.accepted_edge_median,
        accepted_edge_max=report.accepted_edge_max,
        accepted_trades_by_side=report.accepted_trades_by_side,
        accepted_trades_by_asset=report.accepted_trades_by_asset,
        accepted_trades_by_duration=report.accepted_trades_by_duration,
        accepted_trades_by_seconds_bucket=report.accepted_trades_by_seconds_bucket,
        accepted_trades_by_entry_price_bucket=report.accepted_trades_by_entry_price_bucket,
        average_pnl_per_trade=report.average_pnl_per_trade,
        average_win=report.average_win,
        average_loss=report.average_loss,
        profit_factor=report.profit_factor,
        expectancy_per_trade=report.expectancy_per_trade,
        top_1_trade_pnl=report.top_1_trade_pnl,
        top_3_trades_pnl=report.top_3_trades_pnl,
        top_5_trades_pnl=report.top_5_trades_pnl,
        top_10pct_trades_pnl=report.top_10pct_trades_pnl,
        top_1_trade_pct_of_total_pnl=report.top_1_trade_pct_of_total_pnl,
        top_3_trades_pct_of_total_pnl=report.top_3_trades_pct_of_total_pnl,
        top_10pct_trades_pct_of_total_pnl=report.top_10pct_trades_pct_of_total_pnl,
        pnl_excluding_top_1=report.pnl_excluding_top_1,
        pnl_excluding_top_3=report.pnl_excluding_top_3,
        pnl_excluding_top_10pct=report.pnl_excluding_top_10pct,
        median_trade_pnl=report.median_trade_pnl,
        bottom_10pct_trades_pnl=report.bottom_10pct_trades_pnl,
        largest_loss=report.largest_loss,
        largest_win=report.largest_win,
        win_loss_payout_ratio=report.win_loss_payout_ratio,
        low_price_trade_count_below_0_05=report.low_price_trade_count_below_0_05,
        low_price_trade_count_below_0_03=report.low_price_trade_count_below_0_03,
        pnl_from_entry_price_below_0_05=report.pnl_from_entry_price_below_0_05,
        pnl_from_entry_price_below_0_03=report.pnl_from_entry_price_below_0_03,
        open_positions=report.open_positions,
        unresolved_positions=report.unresolved_positions,
        settlement_unavailable=report.settlement_unavailable,
        approximate_expiry_settlements=report.approximate_expiry_settlements,
        mark_to_market_settlements=report.mark_to_market_settlements,
        risk_blocked_trades=report.risk_blocked_trades,
        session_loss_limit_status=report.session_loss_limit_status,
        warnings=report.warnings,
        verdicts=report.verdicts,
        source_coverage=quality["source_coverage"],
        quality_metrics=quality,
    )


def _latest_run_id_for_source(store: SQLiteStore, source_filter: str | None, session_id: str | None, active_only: bool, tiny_only: bool) -> str | None:
    if not source_filter and not session_id and not active_only and not tiny_only:
        return store.latest_run_id()
    clauses = []
    params: list[str] = []
    if source_filter:
        clauses.append("data_source = ?")
        params.append(source_filter)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if active_only:
        clauses.append("notes LIKE ?")
        params.append("%active_only=true%")
    if tiny_only:
        clauses.append("notes LIKE ?")
        params.append("%tiny_profile=true%")
    row = store.rows(
        f"""
        SELECT run_id
        FROM runs
        WHERE {' AND '.join(clauses)}
        ORDER BY started_at DESC, rowid DESC
        LIMIT 1
        """,
        tuple(params),
    )
    return str(row[0]["run_id"]) if row else None


def _empty_report(source_filter: str, starting_balance: float) -> Report:
    return Report(
        scope=f"source={source_filter}; no matching run",
        strategy="n/a",
        mode="n/a",
        data_source="n/a",
        config_summary="n/a",
        starting_balance=starting_balance,
        current_balance=starting_balance,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        total_equity=starting_balance,
        max_equity_drawdown=0.0,
        max_position_exposure=0.0,
        max_position_exposure_pct=0.0,
        open_positions=0,
        closed_trades=0,
        unresolved_positions=0,
        settlement_unavailable=0,
        approximate_expiry_settlements=0,
        mark_to_market_settlements=0,
        skipped_trades=0,
        risk_blocked_trades=0,
        win_rate=0.0,
        average_edge=0.0,
        average_edge_accepted=None,
        average_edge_skipped=None,
        accepted_edge_min=None,
        accepted_edge_median=None,
        accepted_edge_max=None,
        accepted_trades_by_side={},
        accepted_trades_by_asset={},
        accepted_trades_by_duration={},
        accepted_trades_by_seconds_bucket={},
        accepted_trades_by_entry_price_bucket={},
        average_pnl_per_trade=None,
        average_win=None,
        average_loss=None,
        profit_factor=None,
        expectancy_per_trade=None,
        session_loss_limit_status="CLEAR",
        top_1_trade_pnl=None,
        top_3_trades_pnl=None,
        top_5_trades_pnl=None,
        top_10pct_trades_pnl=None,
        top_1_trade_pct_of_total_pnl=None,
        top_3_trades_pct_of_total_pnl=None,
        top_10pct_trades_pct_of_total_pnl=None,
        pnl_excluding_top_1=None,
        pnl_excluding_top_3=None,
        pnl_excluding_top_10pct=None,
        median_trade_pnl=None,
        bottom_10pct_trades_pnl=None,
        largest_loss=None,
        largest_win=None,
        win_loss_payout_ratio=None,
        low_price_trade_count_below_0_05=0,
        low_price_trade_count_below_0_03=0,
        pnl_from_entry_price_below_0_05=0.0,
        pnl_from_entry_price_below_0_03=0.0,
        warnings=tuple(),
        verdicts=("RESEARCH_ONLY_VALID",),
    )


def _format_map(values: dict) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


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
