from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import AgentConfig
from src.models import Direction, Market, OpportunityDecision, OrderBook, PriceSnapshot, Signal
from src.risk.sizing import size_position
from src.simulator.fees import execution_price, fee_amount, slippage_amount
from src.storage.sqlite import SQLiteStore
from src.strategies.pair_cost_arbitrage import PairCostDecision


@dataclass(frozen=True)
class FakeFill:
    trade_id: str
    market: Market
    direction: Direction
    entry_price: float
    shares: float
    notional: float
    entry_fee: float
    slippage_cost: float
    entry_underlying_price: float


class PaperTradingEngine:
    def __init__(self, config: AgentConfig, store: SQLiteStore, run_id: str | None = None):
        self.config = config
        self.store = store
        self.run_id = run_id
        self.random = random.Random(config.random_seed)

    def evaluate(
        self,
        market: Market,
        signal: Signal,
        orderbook: OrderBook,
        now: datetime | None = None,
    ) -> OpportunityDecision:
        now = now or datetime.now(timezone.utc)
        market_price = orderbook.best_ask or orderbook.midpoint
        spread = orderbook.spread if orderbook.spread is not None else self.config.assumed_spread
        if not market.window.is_tradeable(
            now,
            min_seconds_before_end=self.config.min_seconds_before_end,
            max_seconds_after_start=self.config.max_seconds_after_start,
        ):
            return OpportunityDecision(market, signal, market_price, spread, "SKIP", "outside timing window")
        if market_price is None:
            return OpportunityDecision(market, signal, market_price, spread, "SKIP", "missing market price")
        estimated_edge = signal.probability - market_price - (spread or 0.0) / 2.0
        signal = Signal(
            asset=signal.asset,
            direction=signal.direction,
            probability=signal.probability,
            edge=estimated_edge,
            reason=signal.reason,
        )
        if estimated_edge < self.config.min_edge:
            return OpportunityDecision(market, signal, market_price, spread, "SKIP", "edge below threshold")
        if self.random.random() < self.config.failed_fill_probability:
            return OpportunityDecision(market, signal, market_price, spread, "SKIP", "simulated failed fill")
        return OpportunityDecision(market, signal, market_price, spread, "TRADE", "paper trade accepted")

    def enter(
        self,
        decision: OpportunityDecision,
        orderbook: OrderBook,
        price_snapshot: PriceSnapshot,
        now: datetime | None = None,
    ) -> FakeFill | None:
        now = now or datetime.now(timezone.utc)
        self.store.log_opportunity(now, decision, run_id=self.run_id)
        if decision.decision != "TRADE" or decision.market_price is None:
            return None

        balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
        entry = execution_price(decision.market_price, self.config.slippage_bps)
        notional, shares = size_position(
            bankroll=balance,
            market_price=entry,
            max_position_pct=self.config.max_position_pct,
            max_position_usd=self.config.max_position_usd,
        )
        entry_fee = fee_amount(notional, self.config.fee_bps)
        slip = slippage_amount(notional, self.config.slippage_bps)
        total_cost = notional + entry_fee + slip
        if total_cost > balance:
            skipped = OpportunityDecision(
                market=decision.market,
                signal=decision.signal,
                market_price=decision.market_price,
                spread=decision.spread,
                decision="SKIP",
                reason="insufficient fake bankroll",
            )
            self.store.log_opportunity(now, skipped, run_id=self.run_id)
            return None

        fill = FakeFill(
            trade_id=str(uuid.uuid4()),
            market=decision.market,
            direction=decision.signal.direction,
            entry_price=entry,
            shares=shares,
            notional=notional,
            entry_fee=entry_fee,
            slippage_cost=slip,
            entry_underlying_price=price_snapshot.price,
        )
        self.store.open_trade(now, fill, run_id=self.run_id)
        self.store.set_balance(now, balance - total_cost, run_id=self.run_id)
        self.record_equity(now)
        return fill

    def enter_pair(
        self,
        decision: PairCostDecision,
        price_snapshot: PriceSnapshot,
        now: datetime | None = None,
    ) -> list[FakeFill]:
        now = now or datetime.now(timezone.utc)
        signal = Signal(
            asset=decision.market.asset,
            direction=Direction.UP,
            probability=1.0,
            edge=decision.edge,
            reason="pair-cost arbitrage",
        )
        self.store.log_opportunity(
            now,
            OpportunityDecision(
                market=decision.market,
                signal=signal,
                market_price=decision.pair_cost,
                spread=None,
                decision=decision.decision,
                reason=decision.reason,
            ),
            run_id=self.run_id,
        )
        if decision.decision != "TRADE" or decision.up_entry_price is None or decision.down_entry_price is None:
            return []

        balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
        pair_cost = decision.up_entry_price + decision.down_entry_price
        max_notional, _ = size_position(
            bankroll=balance,
            market_price=min(0.99, max(pair_cost / 2.0, 0.01)),
            max_position_pct=self.config.max_position_pct,
            max_position_usd=self.config.max_position_usd,
        )
        shares = max_notional / pair_cost
        up_notional = shares * decision.up_entry_price
        down_notional = shares * decision.down_entry_price
        entry_fee = fee_amount(up_notional + down_notional, self.config.fee_bps)
        slip = slippage_amount(up_notional + down_notional, self.config.slippage_bps)
        total_cost = up_notional + down_notional + entry_fee + slip
        if total_cost > balance:
            skipped = OpportunityDecision(
                market=decision.market,
                signal=signal,
                market_price=decision.pair_cost,
                spread=None,
                decision="SKIP",
                reason="insufficient fake bankroll",
            )
            self.store.log_opportunity(now, skipped, run_id=self.run_id)
            return []

        fills = [
            FakeFill(
                trade_id=str(uuid.uuid4()),
                market=decision.market,
                direction=Direction.UP,
                entry_price=decision.up_entry_price,
                shares=shares,
                notional=up_notional,
                entry_fee=entry_fee / 2.0,
                slippage_cost=slip / 2.0,
                entry_underlying_price=price_snapshot.price,
            ),
            FakeFill(
                trade_id=str(uuid.uuid4()),
                market=decision.market,
                direction=Direction.DOWN,
                entry_price=decision.down_entry_price,
                shares=shares,
                notional=down_notional,
                entry_fee=entry_fee / 2.0,
                slippage_cost=slip / 2.0,
                entry_underlying_price=price_snapshot.price,
            ),
        ]
        for fill in fills:
            self.store.open_trade(now, fill, run_id=self.run_id)
        self.store.set_balance(now, balance - total_cost, run_id=self.run_id)
        self.record_equity(now)
        return fills

    def close_expired(
        self,
        current_prices: dict[str, PriceSnapshot],
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(timezone.utc)
        closed = 0
        for trade in self.store.open_trades(run_id=self.run_id):
            end = datetime.fromisoformat(trade["window_end"].replace("Z", "+00:00"))
            if now < end:
                continue
            snapshot = current_prices.get(trade["asset"])
            if snapshot is None:
                continue
            exit_value = resolve_binary_value(
                direction=Direction(trade["direction"]),
                entry_underlying_price=float(trade["entry_underlying_price"]),
                exit_underlying_price=snapshot.price,
            )
            proceeds = float(trade["shares"]) * exit_value
            exit_fee = fee_amount(proceeds, self.config.fee_bps)
            pnl = proceeds - exit_fee - float(trade["total_cost"])
            balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
            self.store.close_trade(
                now=now,
                trade_id=trade["trade_id"],
                exit_underlying_price=snapshot.price,
                exit_price=exit_value,
                exit_fee=exit_fee,
                pnl=pnl,
                result="WIN" if exit_value == 1.0 else "LOSS",
            )
            self.store.set_balance(now, balance + proceeds - exit_fee, run_id=self.run_id)
            self.record_equity(now)
            closed += 1
        return closed

    def record_equity(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
        open_trades = self.store.open_trades(run_id=self.run_id)
        open_value = sum(float(trade["shares"]) * float(trade["entry_price"]) for trade in open_trades)
        exposure = sum(float(trade["total_cost"]) for trade in open_trades)
        self.store.log_equity_snapshot(now, balance, open_value, exposure, run_id=self.run_id)


def resolve_binary_value(
    direction: Direction,
    entry_underlying_price: float,
    exit_underlying_price: float,
) -> float:
    if direction == Direction.UP:
        return 1.0 if exit_underlying_price >= entry_underlying_price else 0.0
    return 1.0 if exit_underlying_price < entry_underlying_price else 0.0
