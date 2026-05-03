from datetime import datetime, timedelta, timezone

from src.models import Asset, Market, OrderBook, OrderLevel, TimingWindow
from src.strategies.pair_cost_arbitrage import PairCostArbitrageStrategy


def _market() -> Market:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Market(
        market_id="demo",
        slug="demo-pair",
        title="DEMO Pair",
        asset=Asset.BTC,
        window=TimingWindow(now, now + timedelta(minutes=15)),
        up_token_id="UP",
        down_token_id="DOWN",
        source_url="mock://demo",
        is_mock=True,
    )


def test_pair_cost_arbitrage_detection():
    strategy = PairCostArbitrageStrategy(threshold=0.98, slippage_bps=0.0)
    up_book = OrderBook("UP", bids=(), asks=(OrderLevel(0.47, 100),))
    down_book = OrderBook("DOWN", bids=(), asks=(OrderLevel(0.48, 100),))

    decision = strategy.evaluate(_market(), up_book, down_book)

    assert decision.decision == "TRADE"
    assert decision.pair_cost == 0.95
    assert round(decision.edge, 4) == 0.03


def test_pair_cost_failed_second_leg_simulation():
    strategy = PairCostArbitrageStrategy(
        threshold=0.98,
        slippage_bps=0.0,
        failed_second_leg_probability=1.0,
    )
    up_book = OrderBook("UP", bids=(), asks=(OrderLevel(0.47, 100),))
    down_book = OrderBook("DOWN", bids=(), asks=(OrderLevel(0.48, 100),))

    decision = strategy.evaluate(_market(), up_book, down_book)

    assert decision.decision == "SKIP"
    assert decision.failed_second_leg is True
    assert decision.reason == "simulated failed second leg"
