from __future__ import annotations

from datetime import datetime

from src.storage.sqlite import SQLiteStore


def build_dataset_summary(
    store: SQLiteStore,
    source_filter: str | None = None,
    since: datetime | None = None,
) -> str:
    summary = store.dataset_summary(source_filter=source_filter, since=since)
    return "\n".join(
        [
            "Dataset summary",
            f"Source filter: {summary['source_filter']}",
            f"Total snapshots: {summary['total_snapshots']}",
            f"Exchange price snapshots: {summary['exchange_price_snapshots']}",
            f"Polymarket market snapshots: {summary['market_snapshots']}",
            f"Orderbook snapshots: {summary['orderbook_snapshots']}",
            f"Failed snapshots: {summary['failed_snapshots']}",
            f"First snapshot: {summary['first_snapshot']}",
            f"Latest snapshot: {summary['latest_snapshot']}",
            f"Assets seen: {_format_list(summary['assets_seen'])}",
            f"Markets seen: {_format_list(summary['markets_seen'])}",
            f"Missing prices: {summary['missing_prices']}",
            f"Missing orderbooks: {summary['missing_orderbooks']}",
            f"Stale snapshots: {summary['stale_snapshots']}",
            f"Data sources: {_format_map(summary['source_coverage'])}",
            f"Includes demo data: {summary['demo_included']}",
            f"Includes public data: {summary['public_included']}",
        ]
    )


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _format_map(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())
