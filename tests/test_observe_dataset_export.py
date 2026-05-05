from datetime import datetime, timedelta, timezone
from pathlib import Path
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
            Candle(now - timedelta(minutes=1), 99.0, 101.0, 100.0, 101.0, 1.0),
            Candle(now, 100.0, 102.0, 101.0, 102.0, 1.0),
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
        candidates = []
        for market in markets:
            if asset_filter and market.asset != asset_filter:
                continue
            candidates.append(
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
        return candidates

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


def test_observe_cycles_with_mocked_public_collectors(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"
    monkeypatch.setattr("src.main.FallbackExchangeCollector", lambda _collectors: FakeExchange())
    monkeypatch.setattr("src.main.PolymarketPublicCollector", FakePolymarket)

    exit_code = main(
        [
            "--db",
            str(db_path),
            "observe",
            "--cycles",
            "3",
            "--interval-seconds",
            "0",
        ]
    )

    stdout = capsys.readouterr().out
    assert exit_code == 0
    assert "Observe cycle 1/3" in stdout
    assert "Observe complete. Successful cycles: 3; failed cycles: 0." in stdout

    store = SQLiteStore(db_path)
    try:
        summary = store.dataset_summary()
        assert summary["exchange_price_snapshots"] == 6
        assert summary["market_snapshots"] == 3
        assert summary["orderbook_snapshots"] == 6
        assert summary["failed_snapshots"] == 0
    finally:
        store.close()


def test_observe_records_failed_collection_attempts(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"

    def fail_http(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr("src.http_client.JsonHttpClient.get_json", fail_http)

    exit_code = main(
        [
            "--db",
            str(db_path),
            "observe",
            "--cycles",
            "2",
            "--interval-seconds",
            "0",
        ]
    )

    stderr = capsys.readouterr().err
    assert exit_code == 0
    assert "Continuing observe loop" in stderr

    store = SQLiteStore(db_path)
    try:
        metrics = store.data_quality_metrics()
        assert metrics["failed_collection_attempts"] == 2
    finally:
        store.close()


def test_dataset_summary_output(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "dataset"]) == 0

    stdout = capsys.readouterr().out
    assert "Dataset summary" in stdout
    assert "Total snapshots:" in stdout
    assert "Exchange price snapshots: 2" in stdout
    assert "Orderbook snapshots: 6" in stdout
    assert "Assets seen: BTC, ETH" in stdout
    assert "Data sources:" in stdout


def test_replay_handles_insufficient_observed_data_gracefully(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        snapshot = PriceSnapshot(Asset.BTC, 100.0, now, "observed:BTC")
        store.log_price(snapshot)
        store.log_raw_snapshot(now, "observed:BTC", "BTC", "exchange_price", {"price": 100.0})
    finally:
        store.close()

    exit_code = main(["--db", str(db_path), "replay", "--strategy", "momentum"])

    stdout = capsys.readouterr().out
    assert exit_code == 1
    assert "no stored Polymarket markets" in stdout


def test_export_writes_safe_csv_files(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    out_dir = tmp_path / "exports"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "export", "--format", "csv", "--out", str(out_dir)]) == 0

    expected = {
        "raw_snapshots.csv",
        "trades.csv",
        "skipped_opportunities.csv",
        "runs.csv",
        "equity_snapshots.csv",
    }
    assert expected == {path.name for path in out_dir.glob("*.csv")}
    for path in out_dir.glob("*.csv"):
        header = path.read_text(encoding="utf-8").splitlines()[0].lower()
        assert "private" not in header
        assert "secret" not in header
        assert "wallet" not in header
        assert "signer" not in header


def test_reset_paper_results_preserves_observed_snapshots_and_reset_all_deletes(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "reset", "--paper-results"]) == 0

    store = SQLiteStore(db_path)
    try:
        assert len(store.raw_snapshot_rows()) > 0
        assert store.rows("SELECT COUNT(*) AS count FROM trades")[0]["count"] == 0
    finally:
        store.close()


def test_observe_duration_uses_wall_clock_elapsed_and_no_extra_sleep(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"

    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    clock = FakeClock()
    calls = {"count": 0}

    def fake_collect(config, session_id=None):
        calls["count"] += 1
        store = SQLiteStore(config.database_path)
        try:
            ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=calls["count"])
            store.log_raw_snapshot(ts, "wall-clock-test", "BTC", "exchange_price", {"price": 1.0}, session_id=session_id)
        finally:
            store.close()
        clock.now += 40.0
        return 0

    monkeypatch.setattr("src.main.collect", fake_collect)
    monkeypatch.setattr("src.main.time.monotonic", clock.monotonic)
    monkeypatch.setattr("src.main.time.sleep", clock.sleep)

    assert main(["--db", str(db_path), "observe", "--duration-minutes", "1", "--interval-seconds", "15"]) == 0
    output = capsys.readouterr().out
    assert calls["count"] == 2
    assert clock.sleeps == []
    assert "actual_duration_seconds=80.00" in output
    assert "avg_cycle_duration_seconds=40.00" in output


def test_observe_profile_is_recorded_and_summary_printed(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"
    monkeypatch.setattr("src.main.FallbackExchangeCollector", lambda _collectors: FakeExchange())
    monkeypatch.setattr("src.main.PolymarketPublicCollector", FakePolymarket)

    assert main(["--db", str(db_path), "observe", "--profile", "conservative-momentum", "--cycles", "1", "--interval-seconds", "0"]) == 0
    output = capsys.readouterr().out
    assert "Observe performance:" in output

    store = SQLiteStore(db_path)
    try:
        session = store.latest_research_session()
        assert session is not None
        assert "profile=conservative-momentum" in str(session["notes"] or "")
    finally:
        store.close()

    assert main(["--db", str(db_path), "session-report", "--latest"]) == 0
    report_out = capsys.readouterr().out
    assert "Observe profile: conservative-momentum" in report_out

    assert main(["--db", str(db_path), "reset", "--all"]) == 0
    store = SQLiteStore(db_path)
    try:
        assert len(store.raw_snapshot_rows()) == 0
    finally:
        store.close()
