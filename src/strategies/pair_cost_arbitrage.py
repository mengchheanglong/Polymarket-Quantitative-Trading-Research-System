from __future__ import annotations

import random
from dataclasses import dataclass

from src.models import Market, OrderBook
from src.simulator.fees import execution_price


@dataclass(frozen=True)
class PairCostDecision:
    market: Market
    decision: str
    reason: str
    up_entry_price: float | None
    down_entry_price: float | None
    pair_cost: float | None
    edge: float
    failed_second_leg: bool = False


class PairCostArbitrageStrategy:
    """Paper-only YES+NO pair-cost detector.

    This models the research idea that equal-sized YES and NO claims can be
    attractive when total all-in cost is below the configured threshold.
    """

    def __init__(
        self,
        threshold: float = 0.98,
        slippage_bps: float = 25.0,
        failed_second_leg_probability: float = 0.0,
        random_seed: int = 7,
    ):
        self.threshold = threshold
        self.slippage_bps = slippage_bps
        self.failed_second_leg_probability = failed_second_leg_probability
        self.random = random.Random(random_seed)

    def evaluate(
        self,
        market: Market,
        up_book: OrderBook | None,
        down_book: OrderBook | None,
    ) -> PairCostDecision:
        if up_book is None or down_book is None:
            return PairCostDecision(market, "SKIP", "missing one or both orderbooks", None, None, None, 0.0)
        if up_book.best_ask is None or down_book.best_ask is None:
            return PairCostDecision(market, "SKIP", "missing one or both asks", None, None, None, 0.0)

        up_entry = execution_price(up_book.best_ask, self.slippage_bps)
        down_entry = execution_price(down_book.best_ask, self.slippage_bps)
        pair_cost = up_entry + down_entry
        edge = self.threshold - pair_cost
        if pair_cost >= self.threshold:
            return PairCostDecision(
                market,
                "SKIP",
                "pair cost above threshold",
                up_entry,
                down_entry,
                pair_cost,
                edge,
            )
        if self.random.random() < self.failed_second_leg_probability:
            return PairCostDecision(
                market,
                "SKIP",
                "simulated failed second leg",
                up_entry,
                down_entry,
                pair_cost,
                edge,
                failed_second_leg=True,
            )
        return PairCostDecision(
            market,
            "TRADE",
            "paper pair-cost opportunity accepted",
            up_entry,
            down_entry,
            pair_cost,
            edge,
        )
