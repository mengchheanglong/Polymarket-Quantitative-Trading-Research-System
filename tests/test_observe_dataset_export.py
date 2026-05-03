from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.main import main
from src.models import Asset, Candle, Market, OrderBook, OrderLevel, PriceSnapshot, TimingWindow
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

    assert main(["--db", str(db_path), "reset", "--all"]) == 0
    store = SQLiteStore(db_path)
    try:
        assert len(store.raw_snapshot_rows()) == 0
    finally:
        store.close()
