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
                f"Win rate: {self.win_rate:.2%}",
                f"Max equity drawdown: ${self.max_equity_drawdown:.2f}",
                f"Max position exposure: ${self.max_position_exposure:.2f}",
                f"Average edge: {self.average_edge:.4f}",
                f"Source coverage: {_format_map(self.source_coverage)}",
                f"Failed collection attempts: {self.quality_metrics['failed_collection_attempts']}",
                f"Stale snapshots: {self.quality_metrics['stale_snapshots']}",
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
) -> BacktestReport:
    run_id = _latest_run_id_for_source(store, source_filter)
    if run_id:
        report = build_report(store, starting_balance, run_id=run_id)
    elif source_filter:
        report = _empty_report(source_filter, starting_balance)
    else:
        report = build_report(store, starting_balance)
    quality = store.data_quality_metrics(source_filter=source_filter, since=since)
    dataset = store.dataset_summary(source_filter=source_filter, since=since)
    readiness = store.readiness(source_filter=source_filter or "all", since=since)
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
    markets = len(store.collected_markets(source_filter=source_filter, since=since))
    return BacktestReport(
        run_id=run_id,
        strategy=strategy,
        mode=str(run["mode"]) if run else "n/a",
        source_filter=source_filter or "all",
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
        source_coverage=quality["source_coverage"],
        quality_metrics=quality,
    )


def _latest_run_id_for_source(store: SQLiteStore, source_filter: str | None) -> str | None:
    if not source_filter:
        return store.latest_run_id()
    row = store.rows(
        """
        SELECT run_id
        FROM runs
        WHERE data_source = ?
        ORDER BY started_at DESC, rowid DESC
        LIMIT 1
        """,
        (source_filter,),
    )
    return str(row[0]["run_id"]) if row else None


def _empty_report(source_filter: str, starting_balance: float) -> Report:
    return Report(
        scope=f"source={source_filter}; no matching run",
        starting_balance=starting_balance,
        current_balance=starting_balance,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        total_equity=starting_balance,
        max_equity_drawdown=0.0,
        max_position_exposure=0.0,
        closed_trades=0,
        skipped_trades=0,
        win_rate=0.0,
        average_edge=0.0,
    )


def _format_map(values: dict) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())
