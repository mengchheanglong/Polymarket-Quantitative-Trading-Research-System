from __future__ import annotations

from dataclasses import dataclass

from src.reports.summary import build_report
from src.storage.sqlite import SQLiteStore


@dataclass(frozen=True)
class BacktestReport:
    strategy: str
    snapshots: int
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
                f"Strategy: {self.strategy}",
                f"Snapshots collected: {self.snapshots}",
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


def build_backtest_report(store: SQLiteStore, starting_balance: float, strategy: str) -> BacktestReport:
    report = build_report(store, starting_balance)
    quality = store.data_quality_metrics()
    opportunities = store.rows("SELECT COUNT(*) AS count FROM opportunities")[0]["count"]
    accepted = store.rows("SELECT COUNT(*) AS count FROM trades")[0]["count"]
    markets = store.rows("SELECT COUNT(*) AS count FROM collected_markets")[0]["count"]
    return BacktestReport(
        strategy=strategy,
        snapshots=quality["snapshots_collected"],
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


def _format_map(values: dict) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())
