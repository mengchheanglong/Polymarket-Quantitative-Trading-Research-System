from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import AgentConfig
from src.models import Direction, Market, OpportunityDecision, OrderBook, PriceSnapshot, Signal
from src.risk.sizing import size_position
from src.simulator.fees import execution_price, fee_amount, slippage_amount
from src.simulator.lifecycle import classify_market_lifecycle
from src.storage.sqlite import SQLiteStore
from src.strategies.pair_cost_arbitrage import PairCostDecision

RISK_BLOCK_REASONS = {
    "max trade size cap",
    "max exposure cap",
    "max open positions cap",
    "max trades per market cap",
    "max trades per session cap",
    "session loss limit reached",
    "daily loss limit reached",
    "cooldown after loss",
    "insufficient fake bankroll",
}


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
    state_bucket: str | None = None
    stuck_cycles: int | None = None
    transition_probability: float | None = None
    exchange_move: float | None = None


class PaperTradingEngine:
    def __init__(self, config: AgentConfig, store: SQLiteStore, run_id: str | None = None):
        self.config = config
        self.store = store
        self.run_id = run_id
        self.random = random.Random(config.random_seed)
        self.market_trade_counts: dict[str, int] = {}

    def evaluate(
        self,
        market: Market,
        signal: Signal,
        orderbook: OrderBook,
        now: datetime | None = None,
    ) -> OpportunityDecision:
        now = now or datetime.now(timezone.utc)
        lifecycle = classify_market_lifecycle(
            market,
            now,
            min_seconds_before_end=self.config.min_seconds_before_end,
            max_seconds_after_start=self.config.max_seconds_after_start,
        )
        market_price = orderbook.best_ask or orderbook.midpoint
        spread = orderbook.spread if orderbook.spread is not None else self.config.assumed_spread
        if lifecycle.timing_bucket != "valid_window":
            reason = {
                "too_early": "market not started",
                "too_late": "outside timing window",
                "expired": "market expired",
                "missing_expiry": "missing expiry",
            }.get(lifecycle.timing_bucket, "outside timing window")
            return OpportunityDecision(
                market,
                signal,
                market_price,
                spread,
                "SKIP",
                reason,
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
        if market_price is None:
            return OpportunityDecision(
                market,
                signal,
                market_price,
                spread,
                "SKIP",
                "missing market price",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
        if spread is not None and spread > self.config.max_spread:
            return OpportunityDecision(
                market,
                signal,
                market_price,
                spread,
                "SKIP",
                "spread above threshold",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
        estimated_edge = signal.probability - market_price - (spread or 0.0) / 2.0
        signal = Signal(
            asset=signal.asset,
            direction=signal.direction,
            probability=signal.probability,
            edge=estimated_edge,
            reason=signal.reason,
        )
        if estimated_edge < self.config.min_edge:
            return OpportunityDecision(
                market,
                signal,
                market_price,
                spread,
                "SKIP",
                "edge below threshold",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
        if self.random.random() < self.config.failed_fill_probability:
            return OpportunityDecision(
                market,
                signal,
                market_price,
                spread,
                "SKIP",
                "simulated failed fill",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
        return OpportunityDecision(
            market,
            signal,
            market_price,
            spread,
            "TRADE",
            "paper trade accepted",
            seconds_to_expiry=lifecycle.seconds_to_expiry,
            lifecycle_status=lifecycle.status,
            timing_bucket=lifecycle.timing_bucket,
        )

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
            max_trade_usd=self.config.max_trade_usd,
        )
        if notional <= 0 or shares <= 0:
            self.store.log_opportunity(now, self._risk_block_decision(decision, "max trade size cap"), run_id=self.run_id)
            return None
        entry_fee = fee_amount(notional, self.config.fee_bps)
        slip = slippage_amount(notional, self.config.slippage_bps)
        total_cost = notional + entry_fee + slip
        risk_reason = self._risk_block_reason(
            now=now,
            market_slug=decision.market.slug,
            requested_positions=1,
            requested_trade_rows=1,
            requested_market_entries=1,
            requested_total_cost=total_cost,
            balance=balance,
        )
        if risk_reason is not None:
            self.store.log_opportunity(now, self._risk_block_decision(decision, risk_reason), run_id=self.run_id)
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
            state_bucket=decision.state_bucket,
            stuck_cycles=decision.stuck_cycles,
            transition_probability=decision.transition_probability,
            exchange_move=decision.exchange_move,
        )
        self.store.open_trade(now, fill, run_id=self.run_id)
        self.store.set_balance(now, balance - total_cost, run_id=self.run_id)
        self.market_trade_counts[decision.market.slug] = self.market_trade_counts.get(decision.market.slug, 0) + 1
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
                spread=decision.combined_spread,
                decision=decision.decision,
                reason=decision.reason,
                seconds_to_expiry=decision.seconds_to_expiry,
                lifecycle_status=decision.lifecycle_status,
                timing_bucket=decision.timing_bucket,
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
            max_trade_usd=self.config.max_trade_usd,
        )
        shares = max_notional / pair_cost
        up_notional = shares * decision.up_entry_price
        down_notional = shares * decision.down_entry_price
        entry_fee = fee_amount(up_notional + down_notional, self.config.fee_bps)
        slip = slippage_amount(up_notional + down_notional, self.config.slippage_bps)
        total_cost = up_notional + down_notional + entry_fee + slip
        if max_notional <= 0 or shares <= 0:
            self.store.log_opportunity(
                now,
                OpportunityDecision(
                    market=decision.market,
                    signal=signal,
                    market_price=decision.pair_cost,
                    spread=decision.combined_spread,
                    decision="SKIP",
                    reason="max trade size cap",
                    seconds_to_expiry=decision.seconds_to_expiry,
                    lifecycle_status=decision.lifecycle_status,
                    timing_bucket=decision.timing_bucket,
                ),
                run_id=self.run_id,
            )
            return []
        risk_reason = self._risk_block_reason(
            now=now,
            market_slug=decision.market.slug,
            requested_positions=2,
            requested_trade_rows=2,
            requested_market_entries=1,
            requested_total_cost=total_cost,
            balance=balance,
        )
        if risk_reason is not None:
            self.store.log_opportunity(
                now,
                OpportunityDecision(
                    market=decision.market,
                    signal=signal,
                    market_price=decision.pair_cost,
                    spread=decision.combined_spread,
                    decision="SKIP",
                    reason=risk_reason,
                    seconds_to_expiry=decision.seconds_to_expiry,
                    lifecycle_status=decision.lifecycle_status,
                    timing_bucket=decision.timing_bucket,
                ),
                run_id=self.run_id,
            )
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
                state_bucket=None,
                stuck_cycles=None,
                transition_probability=None,
                exchange_move=None,
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
                state_bucket=None,
                stuck_cycles=None,
                transition_probability=None,
                exchange_move=None,
            ),
        ]
        for fill in fills:
            self.store.open_trade(now, fill, run_id=self.run_id)
        self.store.set_balance(now, balance - total_cost, run_id=self.run_id)
        self.market_trade_counts[decision.market.slug] = self.market_trade_counts.get(decision.market.slug, 0) + 1
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
                status="CLOSED_BY_EXPIRY",
                close_mode="expiry-if-known",
            )
            self.store.set_balance(now, balance + proceeds - exit_fee, run_id=self.run_id)
            self.record_equity(now)
            closed += 1
        return closed

    def close_position(
        self,
        *,
        now: datetime,
        trade: dict,
        exit_underlying_price: float | None,
        exit_price: float | None,
        status: str,
        close_mode: str,
        settlement_note: str | None = None,
    ) -> None:
        balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
        proceeds = 0.0
        exit_fee = 0.0
        pnl = None
        result = None
        if exit_price is not None:
            proceeds = float(trade["shares"]) * exit_price
            exit_fee = fee_amount(proceeds, self.config.fee_bps)
            pnl = proceeds - exit_fee - float(trade["total_cost"])
            result = "WIN" if pnl >= 0 else "LOSS"
        self.store.close_trade(
            now=now,
            trade_id=str(trade["trade_id"]),
            exit_underlying_price=exit_underlying_price,
            exit_price=exit_price,
            exit_fee=exit_fee if exit_price is not None else None,
            pnl=pnl,
            result=result,
            status=status,
            close_mode=close_mode,
            settlement_note=settlement_note,
        )
        if exit_price is not None:
            self.store.set_balance(now, balance + proceeds - exit_fee, run_id=self.run_id)
        self.record_equity(now)

    def record_equity(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        balance = self.store.current_balance(default=self.config.starting_balance, run_id=self.run_id)
        open_trades = self.store.open_trades(run_id=self.run_id)
        open_value = sum(float(trade["shares"]) * float(trade["entry_price"]) for trade in open_trades)
        exposure = sum(float(trade["total_cost"]) for trade in open_trades)
        self.store.log_equity_snapshot(now, balance, open_value, exposure, run_id=self.run_id)

    def _risk_block_decision(self, decision: OpportunityDecision, reason: str) -> OpportunityDecision:
        return OpportunityDecision(
            market=decision.market,
            signal=decision.signal,
            market_price=decision.market_price,
            spread=decision.spread,
            decision="SKIP",
            reason=reason,
            seconds_to_expiry=decision.seconds_to_expiry,
            lifecycle_status=decision.lifecycle_status,
            timing_bucket=decision.timing_bucket,
        )

    def _risk_block_reason(
        self,
        *,
        now: datetime,
        market_slug: str,
        requested_positions: int,
        requested_trade_rows: int,
        requested_market_entries: int,
        requested_total_cost: float,
        balance: float,
    ) -> str | None:
        if requested_total_cost > balance:
            return "insufficient fake bankroll"
        open_trades = self.store.open_trades(run_id=self.run_id)
        open_exposure = sum(float(trade["total_cost"]) for trade in open_trades)
        if open_exposure + requested_total_cost > self.config.max_total_exposure_usd:
            return "max exposure cap"
        if len(open_trades) + requested_positions > self.config.max_open_positions:
            return "max open positions cap"
        market_entries = self.market_trade_counts.get(market_slug, 0)
        if market_entries + requested_market_entries > self.config.max_trades_per_market:
            return "max trades per market cap"
        if len(self.store.trade_rows(run_id=self.run_id)) + requested_trade_rows > self.config.max_trades_per_session:
            return "max trades per session cap"
        realized_pnl = 0.0
        daily_pnl = 0.0
        last_loss_time: datetime | None = None
        for trade in self.store.trade_rows(run_id=self.run_id):
            pnl = float(trade["pnl"] or 0.0)
            if trade["status"] and str(trade["status"]).startswith("CLOSED"):
                realized_pnl += pnl
                closed_at = _trade_time_value(trade["closed_at"])
                if closed_at is not None and closed_at.date() == now.date():
                    daily_pnl += pnl
                if pnl < 0 and closed_at is not None and (last_loss_time is None or closed_at > last_loss_time):
                    last_loss_time = closed_at
        if realized_pnl <= -self.config.session_loss_limit_usd:
            return "session loss limit reached"
        if daily_pnl <= -self.config.daily_loss_limit_usd:
            return "daily loss limit reached"
        if (
            last_loss_time is not None
            and self.config.cooldown_after_loss_seconds > 0
            and (now - last_loss_time).total_seconds() < self.config.cooldown_after_loss_seconds
        ):
            return "cooldown after loss"
        return None


def _trade_time_value(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def resolve_binary_value(
    direction: Direction,
    entry_underlying_price: float,
    exit_underlying_price: float,
) -> float:
    if direction == Direction.UP:
        return 1.0 if exit_underlying_price >= entry_underlying_price else 0.0
    return 1.0 if exit_underlying_price < entry_underlying_price else 0.0
