from datetime import datetime, timedelta, timezone

from src.main import main
from src.models import Asset, Candle, DiscoveredMarket, Market, MarketClassification, OrderBook, OrderLevel, PriceSnapshot, TimingWindow
from src.storage.sqlite import SQLiteStore


class FakeExchange:
    def collect_prices(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            PriceSnapshot(Asset.BTC, 100_000.0, now, "fake-exchange:BTC"),
            PriceSnapshot(Asset.ETH, 2_000.0, now, "fake-exchange:ETH"),
        ]

    def recent_candles(self, asset, granularity=60):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            Candle(now - timedelta(minutes=2), 99.0, 101.0, 100.0, 101.0, 1.0),
            Candle(now - timedelta(minutes=1), 100.0, 102.0, 101.0, 102.0, 1.0),
            Candle(now, 101.0, 103.0, 102.0, 103.0, 1.0),
        ]


class FakePolymarket:
    def __init__(self, *_args, **_kwargs):
        pass

    def discover_updown_markets(self, _max_duration_minutes=60):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            Market(
                market_id="observed-btc",
                slug="observed-btc-updown",
                title="Observed BTC Up or Down",
                asset=Asset.BTC,
                window=TimingWindow(now - timedelta(minutes=1), now + timedelta(minutes=5)),
                up_token_id="OBS-BTC-UP",
                down_token_id="OBS-BTC-DOWN",
                source_url="mock://observed",
            )
        ]

    def discover_market_candidates(self, asset_filter=None, max_duration_minutes=60):
        markets = self.discover_updown_markets(max_duration_minutes)
        output = []
        for market in markets:
            if asset_filter and market.asset != asset_filter:
                continue
            output.append(
                DiscoveredMarket(
                    market_id=market.market_id,
                    slug=market.slug,
                    title=market.title,
                    asset_label=market.asset.value,
                    classification=MarketClassification.CRYPTO_UP_DOWN,
                    classification_reasons=("contains BTC", "contains up/down language", "has token ids"),
                    token_status="FOUND",
                    orderbook_status="PENDING",
                    active=True,
                    closed=False,
                    accepting_orders=True,
                    up_token_id=market.up_token_id,
                    down_token_id=market.down_token_id,
                    condition_id=None,
                    window_start=market.window.start,
                    window_end=market.window.end,
                    source_url=market.source_url,
                    accepted=True,
                    reason="accepted: fixture directional market",
                )
            )
        return output

    def capture_orderbooks(self, candidates):
        books = {}
        updated = []
        for candidate in candidates:
            books[candidate.up_token_id] = self.orderbook(candidate.up_token_id)
            books[candidate.down_token_id] = self.orderbook(candidate.down_token_id)
            updated.append(
                DiscoveredMarket(
                    market_id=candidate.market_id,
                    slug=candidate.slug,
                    title=candidate.title,
                    asset_label=candidate.asset_label,
                    classification=candidate.classification,
                    classification_reasons=candidate.classification_reasons + ("has public orderbook",),
                    token_status=candidate.token_status,
                    orderbook_status="FOUND",
                    active=candidate.active,
                    closed=candidate.closed,
                    accepting_orders=candidate.accepting_orders,
                    up_token_id=candidate.up_token_id,
                    down_token_id=candidate.down_token_id,
                    condition_id=candidate.condition_id,
                    window_start=candidate.window_start,
                    window_end=candidate.window_end,
                    source_url=candidate.source_url,
                    accepted=candidate.accepted,
                    reason="accepted: fixture public orderbook captured",
                )
            )
        return updated, books

    def orderbook(self, token_id):
        return OrderBook(
            token_id=token_id,
            bids=(OrderLevel(0.45, 100),),
            asks=(OrderLevel(0.47, 100),),
            last_trade_price=0.46,
        )


def _run_fake_observe(db_path, monkeypatch):
    monkeypatch.setattr("src.main.FallbackExchangeCollector", lambda _collectors: FakeExchange())
    monkeypatch.setattr("src.main.PolymarketPublicCollector", FakePolymarket)
    assert main(["--db", str(db_path), "observe", "--cycles", "3", "--interval-seconds", "0"]) == 0


def test_research_session_creation_completion_and_snapshot_linking(tmp_path, monkeypatch):
    db_path = tmp_path / "paper.sqlite3"
    _run_fake_observe(db_path, monkeypatch)

    store = SQLiteStore(db_path)
    try:
        sessions = store.research_session_rows()
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]
        assert sessions[0]["cycles_completed"] == 3
        assert sessions[0]["successful_cycles"] == 3
        assert sessions[0]["failed_cycles"] == 0
        assert sessions[0]["ended_at"] is not None
        assert sessions[0]["snapshot_count"] > 0
        assert len(store.raw_snapshot_rows(session_id=session_id)) > 0
    finally:
        store.close()


def test_sessions_and_session_report_latest_and_by_id(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _run_fake_observe(db_path, monkeypatch)

    store = SQLiteStore(db_path)
    try:
        session_id = str(store.latest_research_session()["session_id"])
    finally:
        store.close()

    assert main(["--db", str(db_path), "sessions"]) == 0
    sessions_out = capsys.readouterr().out
    assert "Research sessions" in sessions_out
    assert session_id in sessions_out

    assert main(["--db", str(db_path), "session-report", "--latest"]) == 0
    latest_out = capsys.readouterr().out
    assert "Research session report" in latest_out
    assert "Readiness verdict:" in latest_out
    assert "Recommended next command:" in latest_out

    assert main(["--db", str(db_path), "session-report", "--session-id", session_id]) == 0
    by_id_out = capsys.readouterr().out
    assert f"Session ID: {session_id}" in by_id_out


def test_replay_and_export_by_session(tmp_path, monkeypatch):
    db_path = tmp_path / "paper.sqlite3"
    out_dir = tmp_path / "exports"
    _run_fake_observe(db_path, monkeypatch)

    store = SQLiteStore(db_path)
    try:
        session_id = str(store.latest_research_session()["session_id"])
    finally:
        store.close()

    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public", "--session-id", session_id]) == 0

    store = SQLiteStore(db_path)
    try:
        latest_run = store.latest_run()
        assert latest_run["session_id"] == session_id
    finally:
        store.close()

    assert main(["--db", str(db_path), "export", "--format", "csv", "--out", str(out_dir), "--session-id", session_id]) == 0
    raw_csv = (out_dir / "raw_snapshots.csv").read_text(encoding="utf-8")
    assert "fake-exchange:BTC" in raw_csv

    assert main(["--db", str(db_path), "diagnostics", "--source", "public", "--session-id", session_id]) == 0
    assert main(["--db", str(db_path), "sweep", "--strategy", "momentum", "--source", "public", "--session-id", session_id]) == 0


def test_research_report_latest(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _run_fake_observe(db_path, monkeypatch)

    assert main(["--db", str(db_path), "research-report", "--latest"]) == 0
    stdout = capsys.readouterr().out
    assert "Research report" in stdout
    assert "No stored replay runs for this session yet." in stdout


def test_observe_interrupt_finalizes_partial_session(tmp_path, monkeypatch):
    db_path = tmp_path / "paper.sqlite3"
    calls = {"count": 0}

    def fake_collect(config, session_id=None):
        calls["count"] += 1
        store = SQLiteStore(config.database_path)
        try:
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            store.log_raw_snapshot(now, "interrupt-test", "BTC", "exchange_price", {"price": 1.0}, session_id=session_id)
        finally:
            store.close()
        if calls["count"] == 2:
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr("src.main.collect", fake_collect)
    assert main(["--db", str(db_path), "observe", "--cycles", "3", "--interval-seconds", "0"]) == 0

    store = SQLiteStore(db_path)
    try:
        session = store.latest_research_session()
        assert session["ended_at"] is not None
        assert int(session["cycles_completed"]) >= 1
        assert int(session["snapshot_count"]) >= 1
    finally:
        store.close()
