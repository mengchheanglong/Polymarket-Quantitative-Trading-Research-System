from datetime import datetime, timezone

from src.main import main
from src.storage.sqlite import SQLiteStore


def test_raw_snapshot_storage(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        store.log_raw_snapshot(
            now,
            "test-source",
            "BTC",
            "exchange_price",
            {"price": 100.0},
            status="ok",
        )
        row = store.raw_snapshot_rows()[0]
        assert row["source_name"] == "test-source"
        assert row["asset"] == "BTC"
        assert row["snapshot_type"] == "exchange_price"
        assert row["status"] == "ok"
    finally:
        store.close()


def test_replay_uses_stored_demo_data_without_external_apis(tmp_path, monkeypatch):
    db_path = tmp_path / "paper.sqlite3"
    assert main(["--db", str(db_path), "collect", "--demo"]) == 0

    def fail_network(*_args, **_kwargs):
        raise AssertionError("replay must not call external APIs")

    monkeypatch.setattr("src.http_client.JsonHttpClient.get_json", fail_network)

    assert main(["--db", str(db_path), "replay", "--strategy", "momentum"]) == 0

    store = SQLiteStore(db_path)
    try:
        trades = store.rows("SELECT COUNT(*) AS count FROM trades")[0]["count"]
        assert int(trades) >= 1
        assert store.get_state("last_replay_strategy") == "momentum"
    finally:
        store.close()


def test_replay_strategy_selection_pair_cost(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "pair-cost"]) == 0

    store = SQLiteStore(db_path)
    try:
        assert store.get_state("last_replay_strategy") == "pair-cost"
        directions = store.rows("SELECT direction FROM trades WHERE market_slug = 'demo-btc-updown-main'")
        assert [row["direction"] for row in directions] == ["UP", "DOWN"]
    finally:
        store.close()


def test_backtest_report_summary_fields(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "backtest-report"]) == 0

    stdout = capsys.readouterr().out
    assert "Backtest/replay report" in stdout
    assert "Strategy: momentum" in stdout
    assert "Config:" in stdout
    assert "Snapshots collected:" in stdout
    assert "Markets seen:" in stdout
    assert "Opportunities:" in stdout
    assert "Accepted fake trades:" in stdout
    assert "Failed collection attempts:" in stdout
    assert "Skipped by reason:" in stdout


def test_data_quality_metrics(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper"]) == 0

    store = SQLiteStore(db_path)
    try:
        metrics = store.data_quality_metrics()
        assert metrics["snapshots_collected"] >= 9
        assert metrics["failed_collection_attempts"] == 0
        assert metrics["missing_prices"] == 0
        assert metrics["missing_orderbooks"] == 0
        assert metrics["wide_spreads"] == 0
        assert metrics["source_coverage"]["mock:demo"] >= 1
        assert metrics["skipped_by_reason"]["edge below threshold"] >= 1
    finally:
        store.close()
