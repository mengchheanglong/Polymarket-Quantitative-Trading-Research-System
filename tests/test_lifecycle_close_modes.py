from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.collectors.polymarket import _candidate_to_market
from src.main import main
from src.models import Asset, Candle, DiscoveredMarket, MarketClassification, OrderBook, OrderLevel, PriceSnapshot
from src.simulator.lifecycle import classify_market_lifecycle
from src.storage.sqlite import SQLiteStore


def _seed_session_dataset(
    db_path,
    *,
    include_end_price: bool = True,
    later_midpoint: float | None = 0.70,
    include_settlement: bool = False,
) -> str:
    store = SQLiteStore(db_path)
    observed_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session_id = store.start_research_session(observed_at, interval_seconds=15.0, cycles_requested=1, notes="test")
    try:
        start = observed_at - timedelta(minutes=1)
        end = observed_at + timedelta(minutes=2)
        candles = [
            Candle(observed_at - timedelta(minutes=3), 99.0, 101.0, 100.0, 100.5, 1.0),
            Candle(observed_at - timedelta(minutes=2), 100.0, 102.0, 100.5, 101.0, 1.0),
            Candle(observed_at - timedelta(minutes=1), 101.0, 103.0, 101.0, 102.0, 1.0),
        ]
        for ts, price in (
            (start, 100_000.0),
            (observed_at, 100_120.0),
            *(([(end, 100_220.0)] if include_end_price else [])),
        ):
            snapshot = PriceSnapshot(Asset.BTC, price, ts, "public:BTC")
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(ts, snapshot.source, "BTC", "exchange_price", {"price": price}, session_id=session_id)
        store.log_candles("BTC", candles, source="public:BTC", observed_at=observed_at, session_id=session_id)

        candidate = DiscoveredMarket(
            market_id="btc-test",
            slug="btc-updown-5m-1767272400",
            title="Bitcoin Up or Down - test",
            asset_label="BTC",
            classification=MarketClassification.CRYPTO_UP_DOWN,
            classification_reasons=("contains BTC", "contains up/down language", "has token ids", "has public orderbook"),
            token_status="FOUND",
            orderbook_status="FOUND",
            active=True,
            closed=False,
            accepting_orders=True,
            up_token_id="BTC-UP-TOKEN",
            down_token_id="BTC-DOWN-TOKEN",
            condition_id="cond-btc-test",
            window_start=start,
            window_end=end,
            source_url="mock://public",
            accepted=True,
            reason="accepted: fixture public orderbook captured",
        )
        store.log_discovered_markets(observed_at, "polymarket-public", [candidate], session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "market_metadata", {"markets": [candidate.slug]}, session_id=session_id)
        market = _candidate_to_market(candidate)
        books = {
            "BTC-UP-TOKEN": OrderBook("BTC-UP-TOKEN", bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            "BTC-DOWN-TOKEN": OrderBook("BTC-DOWN-TOKEN", bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
        }
        store.replace_collected_market_data(observed_at, [market], books, source_name="polymarket-public", session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "orderbook", {"token_id": "BTC-UP-TOKEN"}, session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "orderbook", {"token_id": "BTC-DOWN-TOKEN"}, session_id=session_id)

        if later_midpoint is not None:
            later = end - timedelta(seconds=10)
            later_books = {
                "BTC-UP-TOKEN": OrderBook("BTC-UP-TOKEN", bids=(OrderLevel(later_midpoint - 0.01, 100),), asks=(OrderLevel(later_midpoint + 0.01, 100),), last_trade_price=later_midpoint),
                "BTC-DOWN-TOKEN": OrderBook("BTC-DOWN-TOKEN", bids=(OrderLevel(1.0 - later_midpoint - 0.01, 100),), asks=(OrderLevel(1.0 - later_midpoint + 0.01, 100),), last_trade_price=1.0 - later_midpoint),
            }
            store.replace_collected_market_data(later, [market], later_books, source_name="polymarket-public", session_id=session_id)
            store.log_raw_snapshot(later, "polymarket-public", "BTC", "orderbook", {"token_id": "BTC-UP-TOKEN"}, session_id=session_id)
            store.log_raw_snapshot(later, "polymarket-public", "BTC", "orderbook", {"token_id": "BTC-DOWN-TOKEN"}, session_id=session_id)

        if include_settlement:
            store.log_raw_snapshot(
                end + timedelta(seconds=5),
                "public:settlement:BTC",
                "BTC",
                "settlement_price",
                {"price": 100_250.0},
                session_id=session_id,
            )
        store.log_raw_snapshot(
            end + timedelta(seconds=5),
            "polymarket-public",
            "BTC",
            "collection_status",
            {"session_complete": True},
            session_id=session_id,
        )
        store.finish_research_session(session_id, end + timedelta(seconds=5))
        return session_id
    finally:
        store.close()


def test_stale_metrics_are_relative_not_wall_clock(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path)
    store = SQLiteStore(db_path)
    try:
        session = store.research_session_by_id(session_id)
        since = datetime.fromisoformat(str(session["started_at"]).replace("Z", "+00:00"))
        until = datetime.fromisoformat(str(session["ended_at"]).replace("Z", "+00:00"))
        metrics = store.data_quality_metrics(source_filter="public", since=since, until=until, session_id=session_id)
        summary = store.dataset_summary(source_filter="public", since=since, until=until, session_id=session_id)
        assert metrics["total_stale_snapshots"] < summary["total_snapshots"]
        assert metrics["stale_exchange_prices"] == 0
        assert metrics["invalid_timestamps"] == 0
    finally:
        store.close()


def test_invalid_timestamp_counts_as_stale(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    try:
        store.conn.execute(
            """
            INSERT INTO raw_snapshots (observed_at, session_id, source_name, asset, snapshot_type, payload_json, status, error_message)
            VALUES ('not-a-timestamp', NULL, 'broken', NULL, 'collection_status', '{}', 'failed', 'bad clock')
            """
        )
        store.conn.commit()
        metrics = store.data_quality_metrics()
        assert metrics["invalid_timestamps"] == 1
        assert metrics["total_stale_snapshots"] >= 1
    finally:
        store.close()


def test_market_lifecycle_and_timing_buckets():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    market = _candidate_to_market(
        DiscoveredMarket(
            market_id="btc-test",
            slug="btc-updown-5m-1767272400",
            title="Bitcoin Up or Down - test",
            asset_label="BTC",
            classification=MarketClassification.CRYPTO_UP_DOWN,
            classification_reasons=(),
            token_status="FOUND",
            orderbook_status="FOUND",
            active=True,
            closed=False,
            accepting_orders=True,
            up_token_id="BTC-UP-TOKEN",
            down_token_id="BTC-DOWN-TOKEN",
            condition_id=None,
            window_start=now,
            window_end=now + timedelta(minutes=5),
            source_url="mock://public",
            accepted=True,
            reason="accepted",
        )
    )
    active = classify_market_lifecycle(market, now + timedelta(minutes=1), min_seconds_before_end=45, max_seconds_after_start=None)
    expired = classify_market_lifecycle(market, now + timedelta(minutes=6), min_seconds_before_end=45, max_seconds_after_start=None)
    late = classify_market_lifecycle(market, now + timedelta(minutes=4, seconds=30), min_seconds_before_end=45, max_seconds_after_start=None)
    assert active.status == "active"
    assert active.timing_bucket == "valid_window"
    assert expired.status == "expired"
    assert expired.timing_bucket == "expired"
    assert late.status == "near_expiry"
    assert late.timing_bucket == "too_late"


def test_replay_close_mode_none_marks_expired_unresolved(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=False, later_midpoint=None)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--close-mode", "none"]) == 0
    store = SQLiteStore(db_path)
    try:
        row = store.rows("SELECT status, pnl FROM trades ORDER BY opened_at LIMIT 1")[0]
        assert row["status"] == "EXPIRED_UNRESOLVED"
        assert row["pnl"] is None
    finally:
        store.close()


def test_replay_close_mode_mark_to_market(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=False, later_midpoint=0.70)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--close-mode", "mark-to-market"]) == 0
    store = SQLiteStore(db_path)
    try:
        row = store.rows("SELECT status, close_mode, pnl FROM trades ORDER BY opened_at LIMIT 1")[0]
        assert row["status"] == "CLOSED_BY_MARK_TO_MARKET"
        assert row["close_mode"] == "mark-to-market"
        assert row["pnl"] is not None
    finally:
        store.close()


def test_replay_close_mode_approximate_expiry(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=None)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--close-mode", "approximate-expiry"]) == 0
    store = SQLiteStore(db_path)
    try:
        row = store.rows("SELECT status, close_mode, settlement_note FROM trades ORDER BY opened_at LIMIT 1")[0]
        assert row["status"] == "CLOSED_BY_EXPIRY"
        assert row["close_mode"] == "approximate-expiry"
        assert "approximate expiry" in str(row["settlement_note"])
    finally:
        store.close()


def test_approximate_expiry_without_end_price_marks_settlement_unavailable(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=False, later_midpoint=None)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--close-mode", "approximate-expiry"]) == 0
    store = SQLiteStore(db_path)
    try:
        row = store.rows("SELECT status, settlement_note FROM trades ORDER BY opened_at LIMIT 1")[0]
        assert row["status"] == "SETTLEMENT_UNAVAILABLE"
        assert "approximate expiry prices unavailable" in str(row["settlement_note"])
    finally:
        store.close()


def test_report_includes_unresolved_counts(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=False, later_midpoint=None)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id, "--close-mode", "none"]) == 0
    assert main(["--db", str(db_path), "report", "--latest"]) == 0
    stdout = capsys.readouterr().out
    assert "Open positions: 0" in stdout
    assert "Unresolved positions: 1" in stdout
    assert "Settlement unavailable: 0" in stdout
