from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.main import main
from src.models import Asset, Candle, Market, OrderBook, OrderLevel, PriceSnapshot, TimingWindow
from src.storage.sqlite import SQLiteStore


def _seed_active_filter_dataset(
    db_path,
    *,
    missing_yes_ask: bool = False,
    missing_no_ask: bool = False,
) -> str:
    store = SQLiteStore(db_path)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session_id = store.start_research_session(base, interval_seconds=15.0, cycles_requested=1, notes="active-filter-test")
    try:
        prices = [
            PriceSnapshot(Asset.BTC, 100_000.0, base - timedelta(minutes=3), "public:BTC"),
            PriceSnapshot(Asset.BTC, 100_100.0, base - timedelta(minutes=2), "public:BTC"),
            PriceSnapshot(Asset.BTC, 100_200.0, base - timedelta(minutes=1), "public:BTC"),
            PriceSnapshot(Asset.BTC, 100_250.0, base, "public:BTC"),
        ]
        candles = [
            Candle(base - timedelta(minutes=3), 99.0, 101.0, 100.0, 100.0, 1.0),
            Candle(base - timedelta(minutes=2), 100.0, 102.0, 100.0, 100.2, 1.0),
            Candle(base - timedelta(minutes=1), 100.2, 103.0, 100.2, 100.4, 1.0),
            Candle(base, 100.4, 104.0, 100.4, 100.7, 1.0),
        ]
        for snapshot in prices:
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(snapshot.timestamp, snapshot.source, "BTC", "exchange_price", {"price": snapshot.price}, session_id=session_id)
        store.log_candles("BTC", candles, source="public:BTC", observed_at=base, session_id=session_id)

        active = Market(
            market_id="active-btc",
            slug="active-btc-5m",
            title="Active BTC Up or Down",
            asset=Asset.BTC,
            window=TimingWindow(base - timedelta(minutes=1), base + timedelta(minutes=2)),
            up_token_id="ACTIVE-UP",
            down_token_id="ACTIVE-DOWN",
            source_url="mock://active",
        )
        future = Market(
            market_id="future-btc",
            slug="future-btc-5m",
            title="Future BTC Up or Down",
            asset=Asset.BTC,
            window=TimingWindow(base + timedelta(minutes=5), base + timedelta(minutes=10)),
            up_token_id="FUTURE-UP",
            down_token_id="FUTURE-DOWN",
            source_url="mock://future",
        )
        expired = Market(
            market_id="expired-btc",
            slug="expired-btc-5m",
            title="Expired BTC Up or Down",
            asset=Asset.BTC,
            window=TimingWindow(base - timedelta(minutes=10), base - timedelta(minutes=1)),
            up_token_id="EXPIRED-UP",
            down_token_id="EXPIRED-DOWN",
            source_url="mock://expired",
        )
        markets = [active, future, expired]
        books = {
            "ACTIVE-UP": OrderBook("ACTIVE-UP", bids=(OrderLevel(0.39, 100),), asks=() if missing_yes_ask else (OrderLevel(0.40, 100),), last_trade_price=0.395),
            "ACTIVE-DOWN": OrderBook("ACTIVE-DOWN", bids=(OrderLevel(0.59, 100),), asks=() if missing_no_ask else (OrderLevel(0.60, 100),), last_trade_price=0.595),
            "FUTURE-UP": OrderBook("FUTURE-UP", bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            "FUTURE-DOWN": OrderBook("FUTURE-DOWN", bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
            "EXPIRED-UP": OrderBook("EXPIRED-UP", bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            "EXPIRED-DOWN": OrderBook("EXPIRED-DOWN", bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
        }
        store.replace_collected_market_data(base, markets, books, source_name="polymarket-public", session_id=session_id)
        store.log_raw_snapshot(base, "polymarket-public", "BTC", "market_metadata", {"markets": [market.slug for market in markets]}, session_id=session_id)
        for token_id in books:
            store.log_raw_snapshot(base, "polymarket-public", "BTC", "orderbook", {"token_id": token_id}, session_id=session_id)
        store.finish_research_session(session_id, base + timedelta(seconds=1))
        return session_id
    finally:
        store.close()


def test_active_only_replay_excludes_not_started_and_expired(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_active_filter_dataset(db_path)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--active-only"]) == 0
    store = SQLiteStore(db_path)
    try:
        opportunities = store.rows("SELECT market_slug, reason FROM opportunities ORDER BY observed_at, id")
        assert any(row["market_slug"] == "active-btc-5m" for row in opportunities)
        assert all(row["market_slug"] != "future-btc-5m" for row in opportunities)
        assert all(row["market_slug"] != "expired-btc-5m" for row in opportunities)
    finally:
        store.close()


def test_seconds_to_expiry_filters(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_active_filter_dataset(db_path)
    assert main([
        "--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id,
        "--active-only", "--min-seconds-to-expiry", "30", "--max-seconds-to-expiry", "90",
    ]) == 1


def test_active_markets_report_output(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_active_filter_dataset(db_path)
    assert main(["--db", str(db_path), "active-markets", "--source", "public", "--session-id", session_id]) == 0
    stdout = capsys.readouterr().out
    assert "Active market report" in stdout
    assert "Active market count: 1" in stdout
    assert "Lifecycle counts:" in stdout
    assert "Pair cost below threshold:" in stdout


def test_liquidity_diagnostics_breaks_down_missing_yes_and_no_asks(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_active_filter_dataset(db_path, missing_yes_ask=True)
    assert main(["--db", str(db_path), "replay", "--strategy", "pair-cost", "--source", "public", "--session-id", session_id, "--active-only"]) == 0
    assert main(["--db", str(db_path), "diagnostics", "--strategy", "pair-cost", "--source", "public", "--session-id", session_id, "--active-only"]) == 0
    stdout = capsys.readouterr().out
    assert "liquidity=" in stdout
    assert "missing_yes_ask=1" in stdout

    db_path_2 = tmp_path / "paper2.sqlite3"
    session_id_2 = _seed_active_filter_dataset(db_path_2, missing_no_ask=True)
    assert main(["--db", str(db_path_2), "replay", "--strategy", "pair-cost", "--source", "public", "--session-id", session_id_2, "--active-only"]) == 0
    assert main(["--db", str(db_path_2), "diagnostics", "--strategy", "pair-cost", "--source", "public", "--session-id", session_id_2, "--active-only"]) == 0
    stdout_2 = capsys.readouterr().out
    assert "missing_no_ask=1" in stdout_2


def test_momentum_timing_breakdown_and_active_only_sweep(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_active_filter_dataset(db_path)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id]) == 0
    assert main(["--db", str(db_path), "diagnostics", "--strategy", "momentum", "--source", "public", "--session-id", session_id]) == 0
    stdout = capsys.readouterr().out
    assert "momentum_timing=" in stdout
    assert "not_started=1" in stdout
    assert "expired=1" in stdout

    assert main(["--db", str(db_path), "sweep", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--active-only"]) == 0
    sweep_out = capsys.readouterr().out
    assert "Threshold sweep" in sweep_out
    assert "Source filter: public" in sweep_out
