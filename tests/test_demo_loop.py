from pathlib import Path

from src.main import main
from src.storage.sqlite import SQLiteStore


def test_stale_package_tree_is_removed():
    assert not Path("src/polymarket_paper_agent").exists()


def test_demo_collect_inserts_deterministic_mock_data(tmp_path, monkeypatch):
    db_path = tmp_path / "paper.sqlite3"

    def fail_network(*_args, **_kwargs):
        raise AssertionError("demo mode should not call external APIs")

    monkeypatch.setattr("src.http_client.JsonHttpClient.get_json", fail_network)

    exit_code = main(["--db", str(db_path), "collect", "--demo"])

    assert exit_code == 0
    store = SQLiteStore(db_path)
    try:
        prices = store.latest_prices(source_prefix="mock:demo:spot:")
        markets = store.collected_markets(mock_only=True)
        btc_candles = store.recent_candles("BTC", limit=5, source_prefix="mock:demo:spot:")
        orderbook = store.collected_orderbook("DEMO-BTC-UP-MAIN")

        assert prices["BTC"].price == 100_800.0
        assert prices["ETH"].price == 1_980.0
        assert len(markets) == 3
        assert [market.slug for market in markets] == [
            "demo-btc-updown-main",
            "demo-eth-updown-main",
            "demo-btc-updown-skip",
        ]
        assert [candle.close for candle in btc_candles] == [
            100_000.0,
            100_200.0,
            100_400.0,
            100_600.0,
            100_800.0,
        ]
        assert orderbook is not None
        assert orderbook.best_ask == 0.47
        assert store.get_state("last_collection_mode") == "demo"
    finally:
        store.close()


def test_run_paper_processes_demo_collection_end_to_end(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper"]) == 0

    store = SQLiteStore(db_path)
    try:
        closed_trades = store.rows("SELECT result, pnl FROM trades WHERE status LIKE 'CLOSED%'")
        skipped = store.rows("SELECT COUNT(*) AS count FROM opportunities WHERE decision = 'SKIP'")[0]["count"]
        balance = store.current_balance(default=1000.0)

        assert len(closed_trades) == 2
        assert {row["result"] for row in closed_trades} == {"WIN", "LOSS"}
        assert int(skipped) >= 1
        exposure = store.rows("SELECT MAX(position_exposure) AS exposure FROM equity_snapshots")[0][
            "exposure"
        ]
        assert balance != 1000.0
        assert float(exposure) > 0
    finally:
        store.close()


def test_report_after_demo_collection_and_run_paper(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper"]) == 0
    assert main(["--db", str(db_path), "report"]) == 0

    stdout = capsys.readouterr().out
    assert "Closed trades: 2" in stdout
    assert "Open positions: 0" in stdout
    assert "Unresolved positions: 0" in stdout
    assert "Skipped trades: 1" in stdout
    assert "Win rate: 50.00%" in stdout
    assert "Starting balance: $1000.00" in stdout
    assert "Realized fake PnL:" in stdout
    assert "Unrealized fake PnL:" in stdout
    assert "Total fake equity:" in stdout
    assert "Max equity drawdown:" in stdout
    assert "Max position exposure:" in stdout
    assert "Average edge:" in stdout


def test_trade_ledger_after_demo_run_contains_trades_and_skips(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper"]) == 0
    assert main(["--db", str(db_path), "trades"]) == 0

    stdout = capsys.readouterr().out
    assert "Simulated trade ledger" in stdout
    assert "demo-btc-updown-main" in stdout
    assert "entry_fee=" in stdout
    assert "slippage=" in stdout
    assert "pnl=" in stdout
    assert "Skipped opportunities:" in stdout
    assert "reason=edge below threshold" in stdout


def test_pair_cost_strategy_runs_on_demo_data(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "pair-cost"]) == 0

    store = SQLiteStore(db_path)
    try:
        trades = store.rows("SELECT direction FROM trades WHERE market_slug = 'demo-btc-updown-main'")
        skipped = store.rows("SELECT reason FROM opportunities WHERE decision = 'SKIP'")

        assert [row["direction"] for row in trades] == ["UP", "DOWN"]
        assert any(row["reason"] == "pair cost above threshold" for row in skipped)
    finally:
        store.close()
