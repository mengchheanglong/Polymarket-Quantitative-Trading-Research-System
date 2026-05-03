from datetime import datetime, timedelta, timezone

from src.config import AgentConfig
from src.models import Asset, Direction, Market, OpportunityDecision, OrderBook, OrderLevel, PriceSnapshot, Signal, TimingWindow
from src.simulator.engine import PaperTradingEngine, resolve_binary_value
from src.storage.sqlite import SQLiteStore


def test_binary_resolution_for_up_and_down():
    assert resolve_binary_value(Direction.UP, 100.0, 101.0) == 1.0
    assert resolve_binary_value(Direction.UP, 100.0, 99.0) == 0.0
    assert resolve_binary_value(Direction.DOWN, 100.0, 99.0) == 1.0
    assert resolve_binary_value(Direction.DOWN, 100.0, 101.0) == 0.0


def test_fake_trade_open_and_close_tracks_pnl(tmp_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    market = Market(
        market_id="demo",
        slug="demo-btc-updown",
        title="DEMO BTC Up or Down",
        asset=Asset.BTC,
        window=TimingWindow(now - timedelta(minutes=1), now + timedelta(minutes=1)),
        up_token_id="UP",
        down_token_id="DOWN",
        source_url="mock://demo",
        is_mock=True,
    )
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    config = AgentConfig(
        database_path=tmp_path / "paper.sqlite3",
        min_edge=-1.0,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    engine = PaperTradingEngine(config, store)
    orderbook = OrderBook("UP", bids=(OrderLevel(0.49, 100),), asks=(OrderLevel(0.50, 100),))
    signal = Signal(Asset.BTC, Direction.UP, probability=0.60, edge=0.10, reason="test")
    decision = OpportunityDecision(market, signal, 0.50, 0.01, "TRADE", "test")

    fill = engine.enter(
        decision,
        orderbook,
        PriceSnapshot(Asset.BTC, 100.0, now, "test"),
        now=now,
    )
    assert fill is not None

    closed = engine.close_expired(
        {"BTC": PriceSnapshot(Asset.BTC, 101.0, now + timedelta(minutes=2), "test")},
        now=now + timedelta(minutes=2),
    )

    assert closed == 1
    trade = store.rows("SELECT pnl, result FROM trades WHERE trade_id = ?", (fill.trade_id,))[0]
    assert trade["result"] == "WIN"
    assert float(trade["pnl"]) == 50.0
    store.close()

