from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class Asset(StrEnum):
    BTC = "BTC"
    ETH = "ETH"


class Direction(StrEnum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class TimingWindow:
    start: datetime
    end: datetime

    def seconds_to_end(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (self.end - now).total_seconds()

    def is_open(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.start <= now < self.end

    def is_tradeable(
        self,
        now: datetime | None = None,
        min_seconds_before_end: int = 45,
        max_seconds_after_start: int | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        if not self.is_open(now):
            return False
        if self.seconds_to_end(now) < min_seconds_before_end:
            return False
        if max_seconds_after_start is not None:
            seconds_after_start = (now - self.start).total_seconds()
            if seconds_after_start > max_seconds_after_start:
                return False
        return True

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass(frozen=True)
class PriceSnapshot:
    asset: Asset
    price: float
    timestamp: datetime
    source: str


@dataclass(frozen=True)
class Candle:
    start: datetime
    low: float
    high: float
    open: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    bids: tuple[OrderLevel, ...]
    asks: tuple[OrderLevel, ...]
    last_trade_price: float | None = None

    @property
    def best_bid(self) -> float | None:
        return max((level.price for level in self.bids), default=None)

    @property
    def best_ask(self) -> float | None:
        return min((level.price for level in self.asks), default=None)

    @property
    def midpoint(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return self.last_trade_price

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return max(0.0, self.best_ask - self.best_bid)


@dataclass(frozen=True)
class Market:
    market_id: str
    slug: str
    title: str
    asset: Asset
    window: TimingWindow
    up_token_id: str
    down_token_id: str
    source_url: str
    is_mock: bool = False

    def token_for(self, direction: Direction) -> str:
        return self.up_token_id if direction == Direction.UP else self.down_token_id


@dataclass(frozen=True)
class Signal:
    asset: Asset
    direction: Direction
    probability: float
    edge: float
    reason: str


@dataclass(frozen=True)
class OpportunityDecision:
    market: Market
    signal: Signal
    market_price: float | None
    spread: float | None
    decision: str
    reason: str

