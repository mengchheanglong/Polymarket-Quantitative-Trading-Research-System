from datetime import datetime, timedelta, timezone

from src.config import AgentConfig
from src.main import main
from src.models import Asset, Direction, Market, OrderBook, OrderLevel, PriceSnapshot, Signal, TimingWindow
from src.reports.diagnostics import build_diagnostics
from src.reports.summary import build_report
from src.simulator.engine import PaperTradingEngine
from src.storage.sqlite import SQLiteStore


def _market(slug: str, now: datetime) -> Market:
    return Market(
        market_id=slug,
        slug=slug,
        title=slug,
        asset=Asset.BTC,
        window=TimingWindow(start=now - timedelta(minutes=1), end=now + timedelta(minutes=5)),
        up_token_id=f"{slug}-up",
        down_token_id=f"{slug}-down",
        source_url="mock://market",
        is_mock=True,
        observed_at=now,
        latest_observed_at=now,
    )


def _signal() -> Signal:
    return Signal(asset=Asset.BTC, direction=Direction.UP, probability=0.80, edge=0.0, reason="test")


def _book(token_id: str) -> OrderBook:
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(0.49, 10.0),),
        asks=(OrderLevel(0.50, 10.0),),
        last_trade_price=0.495,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="mock://book",
    )


def _engine_config(**updates) -> AgentConfig:
    base = AgentConfig(
        max_position_pct=1.0,
        max_position_usd=100.0,
        max_trade_usd=1.0,
        max_total_exposure_usd=10.0,
        max_open_positions=5,
        max_trades_per_market=5,
        max_trades_per_session=100,
        session_loss_limit_usd=5.0,
        daily_loss_limit_usd=10.0,
        cooldown_after_loss_seconds=0,
    )
    return AgentConfig(**{**base.__dict__, **updates})


def _accepted_fill(engine: PaperTradingEngine, store: SQLiteStore, market: Market, now: datetime):
    decision = engine.evaluate(market, _signal(), _book(market.up_token_id), now=now)
    assert decision.decision == "TRADE"
    return engine.enter(
        decision,
        _book(market.up_token_id),
        PriceSnapshot(Asset.BTC, 100_000.0, now, "mock://spot"),
        now=now,
    )


def test_max_total_exposure_cap_blocks_second_trade(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(max_total_exposure_usd=1.5)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="test")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    try:
        assert _accepted_fill(engine, store, _market("m1", now), now) is not None
        blocked = _accepted_fill(engine, store, _market("m2", now + timedelta(seconds=1)), now + timedelta(seconds=1))
        assert blocked is None
        reasons = [str(row["reason"]) for row in store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id = ?", (run_id,))]
        assert "max exposure cap" in reasons
    finally:
        store.close()


def test_max_open_positions_cap_blocks_second_trade(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(max_open_positions=1, max_total_exposure_usd=10.0)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="test")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    try:
        assert _accepted_fill(engine, store, _market("m1", now), now) is not None
        blocked = _accepted_fill(engine, store, _market("m2", now + timedelta(seconds=1)), now + timedelta(seconds=1))
        assert blocked is None
        reasons = [str(row["reason"]) for row in store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id = ?", (run_id,))]
        assert "max open positions cap" in reasons
    finally:
        store.close()


def test_max_trades_per_market_cap_blocks_repeat_entry(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(max_trades_per_market=1)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="test")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    market = _market("same-market", now)
    try:
        assert _accepted_fill(engine, store, market, now) is not None
        blocked = _accepted_fill(engine, store, market, now + timedelta(seconds=1))
        assert blocked is None
        reasons = [str(row["reason"]) for row in store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id = ?", (run_id,))]
        assert "max trades per market cap" in reasons
    finally:
        store.close()


def test_session_loss_limit_cap_blocks_new_trade(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(session_loss_limit_usd=0.5)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="test")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    try:
        fill = _accepted_fill(engine, store, _market("loss-market", now), now)
        assert fill is not None
        trade = store.open_trades(run_id=run_id)[0]
        engine.close_position(
            now=now + timedelta(seconds=10),
            trade=trade,
            exit_underlying_price=99_000.0,
            exit_price=0.0,
            status="CLOSED_BY_MARK_TO_MARKET",
            close_mode="mark-to-market",
            settlement_note="forced loss",
        )
        blocked = _accepted_fill(engine, store, _market("next-market", now + timedelta(seconds=20)), now + timedelta(seconds=20))
        assert blocked is None
        reasons = [str(row["reason"]) for row in store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id = ?", (run_id,))]
        assert "session loss limit reached" in reasons
    finally:
        store.close()


def test_cooldown_after_loss_blocks_trade(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(cooldown_after_loss_seconds=300)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="test")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    try:
        fill = _accepted_fill(engine, store, _market("loss-market", now), now)
        assert fill is not None
        trade = store.open_trades(run_id=run_id)[0]
        engine.close_position(
            now=now + timedelta(seconds=10),
            trade=trade,
            exit_underlying_price=99_000.0,
            exit_price=0.0,
            status="CLOSED_BY_MARK_TO_MARKET",
            close_mode="mark-to-market",
            settlement_note="forced loss",
        )
        blocked = _accepted_fill(engine, store, _market("cooldown-market", now + timedelta(seconds=20)), now + timedelta(seconds=20))
        assert blocked is None
        reasons = [str(row["reason"]) for row in store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP' AND run_id = ?", (run_id,))]
        assert "cooldown after loss" in reasons
    finally:
        store.close()


def test_tiny_flag_applies_expected_config_and_lowers_exposure(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0
    store = SQLiteStore(db_path)
    try:
        normal_run = store.latest_run_id()
        normal_report = build_report(store, 1000.0, run_id=normal_run)
    finally:
        store.close()

    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo", "--tiny"]) == 0
    store = SQLiteStore(db_path)
    try:
        tiny_run = store.latest_run_id()
        tiny_report = build_report(store, 1000.0, run_id=tiny_run)
        notes = str(store.run_by_id(tiny_run)["notes"])
    finally:
        store.close()

    assert "tiny_profile=true" in notes
    assert "max_trade_usd=1.0" in notes
    assert tiny_report.max_position_exposure < normal_report.max_position_exposure


def test_report_includes_expectancy_and_average_pnl(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo", "--tiny"]) == 0
    store = SQLiteStore(db_path)
    try:
        report = build_report(store, 1000.0)
        text = report.as_text()
    finally:
        store.close()

    assert "Average PnL per trade:" in text
    assert "Average win:" in text
    assert "Average loss:" in text
    assert "Expectancy per trade:" in text


def test_risk_diagnostics_show_risk_blocked_trade_counts(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    config = _engine_config(max_open_positions=1, tiny_profile=True)
    run_id = store.start_run("momentum", "replay", "demo", config.starting_balance, now, notes="tiny_profile=true; max_open_positions=1")
    engine = PaperTradingEngine(config, store, run_id=run_id)
    try:
        assert _accepted_fill(engine, store, _market("m1", now), now) is not None
        assert _accepted_fill(engine, store, _market("m2", now + timedelta(seconds=1)), now + timedelta(seconds=1)) is None
        engine.record_equity(now + timedelta(seconds=1))
        store.finish_run(run_id, now + timedelta(seconds=2))
        output = build_diagnostics(store, config, run_id=run_id)
    finally:
        store.close()

    assert "risk_blocked_trades=1" in output
    assert "risk_skips=max open positions cap=1" in output
