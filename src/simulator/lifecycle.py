from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.models import Market


@dataclass(frozen=True)
class MarketLifecycle:
    market_slug: str
    asset: str
    start_time: datetime | None
    end_time: datetime | None
    duration_type: str
    status: str
    seconds_to_expiry: float | None
    timing_bucket: str


def classify_market_lifecycle(
    market: Market,
    snapshot_time: datetime,
    *,
    min_seconds_before_end: int,
    max_seconds_after_start: int | None,
) -> MarketLifecycle:
    start = market.window.start
    end = market.window.end
    duration_type = _duration_type(market)
    if start is None or end is None:
        return MarketLifecycle(
            market_slug=market.slug,
            asset=market.asset.value,
            start_time=start,
            end_time=end,
            duration_type=duration_type,
            status="settlement_unavailable",
            seconds_to_expiry=None,
            timing_bucket="missing_expiry",
        )

    seconds_to_expiry = (end - snapshot_time).total_seconds()
    if snapshot_time < start:
        status = "not_started"
        timing_bucket = "too_early"
    elif snapshot_time >= end:
        status = "expired"
        timing_bucket = "expired"
    elif seconds_to_expiry < min_seconds_before_end:
        status = "near_expiry"
        timing_bucket = "too_late"
    elif max_seconds_after_start is not None and (snapshot_time - start).total_seconds() > max_seconds_after_start:
        status = "near_expiry"
        timing_bucket = "too_late"
    else:
        status = "active"
        timing_bucket = "valid_window"
    return MarketLifecycle(
        market_slug=market.slug,
        asset=market.asset.value,
        start_time=start,
        end_time=end,
        duration_type=duration_type,
        status=status,
        seconds_to_expiry=seconds_to_expiry,
        timing_bucket=timing_bucket,
    )


def _duration_type(market: Market) -> str:
    minutes = market.window.duration_minutes
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"
