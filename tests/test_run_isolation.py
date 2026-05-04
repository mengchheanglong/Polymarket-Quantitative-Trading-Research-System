from src.main import main
from src.storage.sqlite import SQLiteStore


def test_run_metadata_and_rows_are_linked(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0

    store = SQLiteStore(db_path)
    try:
        runs = store.run_rows()
        assert len(runs) == 1
        run_id = runs[0]["run_id"]
        assert runs[0]["strategy"] == "momentum"
        assert runs[0]["mode"] == "paper"
        assert runs[0]["data_source"] == "demo"
        assert runs[0]["accepted_trade_count"] == 2
        assert runs[0]["skipped_opportunity_count"] == 1
        assert store.rows("SELECT COUNT(*) AS count FROM trades WHERE run_id = ?", (run_id,))[0]["count"] == 2
        assert store.rows("SELECT COUNT(*) AS count FROM opportunities WHERE run_id = ?", (run_id,))[0]["count"] == 3
        assert store.rows("SELECT COUNT(*) AS count FROM equity_snapshots WHERE run_id = ?", (run_id,))[0]["count"] > 0
    finally:
        store.close()


def test_report_latest_and_by_run_id_are_isolated(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "pair-cost"]) == 0

    store = SQLiteStore(db_path)
    try:
        first_run = store.run_rows()[0]["run_id"]
    finally:
        store.close()

    assert main(["--db", str(db_path), "report", "--latest"]) == 0
    latest_stdout = capsys.readouterr().out
    assert "Config:" in latest_stdout
    assert "Closed trades: 4" in latest_stdout
    assert "Skipped trades: 1" in latest_stdout

    assert main(["--db", str(db_path), "report", "--run-id", first_run]) == 0
    run_stdout = capsys.readouterr().out
    assert f"Scope: run_id={first_run}" in run_stdout
    assert "Closed trades: 2" in run_stdout
    assert "Skipped trades: 1" in run_stdout


def test_compare_momentum_vs_pair_cost(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "pair-cost"]) == 0
    assert main(["--db", str(db_path), "compare"]) == 0

    stdout = capsys.readouterr().out
    assert "Strategy comparison" in stdout
    assert "momentum | runs=1" in stdout
    assert "pair-cost | runs=1" in stdout
    assert "accepted_trades=" in stdout
    assert "max_equity_drawdown=" in stdout


def test_reset_paper_results_preserves_raw_snapshots(tmp_path):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "run-paper", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "reset", "--paper-results"]) == 0

    store = SQLiteStore(db_path)
    try:
        assert len(store.raw_snapshot_rows()) > 0
        assert store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"] == 0
        assert store.rows("SELECT COUNT(*) AS count FROM trades")[0]["count"] == 0
        assert store.rows("SELECT COUNT(*) AS count FROM opportunities")[0]["count"] == 0
    finally:
        store.close()


def test_backtest_report_uses_latest_run_not_all_runs(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "pair-cost"]) == 0
    assert main(["--db", str(db_path), "backtest-report"]) == 0

    stdout = capsys.readouterr().out
    assert "Strategy: pair-cost" in stdout
    assert "Accepted fake trades: 4" in stdout
    assert "Skipped opportunities: 1" in stdout
