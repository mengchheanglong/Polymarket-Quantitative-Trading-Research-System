from datetime import datetime, timedelta, timezone

from src.main import main
from src.models import Asset, PriceSnapshot
from src.storage.sqlite import SQLiteStore


def _log_public_price_only(db_path):
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        for asset, price in ((Asset.BTC, 100_000.0), (Asset.ETH, 2_000.0)):
            snapshot = PriceSnapshot(asset, price, now, f"public-test:{asset.value}")
            store.log_price(snapshot)
            store.log_raw_snapshot(
                now,
                f"public-test:{asset.value}",
                asset.value,
                "exchange_price",
                {"price": price},
            )
        store.log_raw_snapshot(
            now,
            "polymarket-public",
            None,
            "market_metadata",
            {"markets": []},
        )
    finally:
        store.close()


def test_dataset_source_filters_demo_and_public(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    _log_public_price_only(db_path)

    assert main(["--db", str(db_path), "dataset", "--source", "demo"]) == 0
    demo_out = capsys.readouterr().out
    assert "Source filter: demo" in demo_out
    assert "Includes demo data: True" in demo_out
    assert "Includes public data: False" in demo_out

    assert main(["--db", str(db_path), "dataset", "--source", "public"]) == 0
    public_out = capsys.readouterr().out
    assert "Source filter: public" in public_out
    assert "Includes demo data: False" in public_out
    assert "Includes public data: True" in public_out


def test_replay_source_demo_uses_demo_only(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    _log_public_price_only(db_path)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0

    store = SQLiteStore(db_path)
    try:
        run = store.latest_run()
        assert run["data_source"] == "demo"
        assert int(run["accepted_trade_count"]) == 2
    finally:
        store.close()


def test_replay_source_public_does_not_fall_back_to_demo(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    _log_public_price_only(db_path)

    exit_code = main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public"])

    stdout = capsys.readouterr().out
    assert exit_code == 1
    assert "Replay cannot run with --source public" in stdout
    assert "Demo data was not used." in stdout


def test_backtest_report_source_public_does_not_mix_demo_runs(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0
    _log_public_price_only(db_path)
    assert main(["--db", str(db_path), "backtest-report", "--source", "public"]) == 0

    stdout = capsys.readouterr().out
    assert "Source filter: public" in stdout
    assert "Run ID: n/a" in stdout
    assert "Accepted fake trades: 0" in stdout
    assert "Skipped opportunities: 0" in stdout
    assert "Realized fake PnL: $0.00" in stdout


def test_readiness_mixed_dataset_verdict(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    _log_public_price_only(db_path)
    assert main(["--db", str(db_path), "readiness"]) == 0

    stdout = capsys.readouterr().out
    assert "Verdict: MIXED_DEMO_AND_PUBLIC_DATA" in stdout
    assert "Dataset includes demo: True" in stdout
    assert "Dataset includes public: True" in stdout


def test_public_readiness_insufficient_data(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    _log_public_price_only(db_path)
    assert main(["--db", str(db_path), "readiness", "--source", "public"]) == 0

    stdout = capsys.readouterr().out
    assert "Verdict: INSUFFICIENT_PUBLIC_DATA" in stdout


def test_market_discovery_audit_output(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "markets", "--source", "demo"]) == 0

    stdout = capsys.readouterr().out
    assert "Polymarket market discovery audit" in stdout
    assert "demo-btc-updown-main" in stdout
    assert "type=mock" in stdout
    assert "orderbook=True" in stdout


def test_export_source_public_excludes_demo_rows(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    out_dir = tmp_path / "exports"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    _log_public_price_only(db_path)
    assert main(["--db", str(db_path), "export", "--format", "csv", "--out", str(out_dir), "--source", "public"]) == 0

    raw_csv = (out_dir / "raw_snapshots.csv").read_text(encoding="utf-8")
    assert "public-test:BTC" in raw_csv
    assert "mock:demo" not in raw_csv


def test_dataset_since_filter(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "dataset", "--source", "demo", "--since", future]) == 0

    stdout = capsys.readouterr().out
    assert "Total snapshots: 0" in stdout
