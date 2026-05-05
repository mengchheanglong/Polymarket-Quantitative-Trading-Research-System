from __future__ import annotations

from src.config import AgentConfig
from src.main import _apply_named_preset, main
from src.reports.conservative_report import conservative_readiness_verdict
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def test_conservative_named_preset_alias():
    preset = _apply_named_preset(AgentConfig(), "conservative-tiny")
    assert preset.tiny_profile is True
    assert preset.momentum_preset == "conservative-tiny"
    assert preset.momentum_asset_filter == "BTC"
    assert preset.momentum_duration_filter == "5m"
    assert preset.momentum_min_entry_price == 0.05
    assert preset.momentum_max_entry_price == 0.85
    assert preset.max_total_exposure_usd == 5.0


def test_conservative_readiness_verdicts():
    assert conservative_readiness_verdict(
        closed_trades=60,
        realized_pnl=25.0,
        expectancy=0.4,
        pnl_excluding_top_3=12.0,
        top_1_trade_pct=0.20,
        side_correctness_rate=0.65,
        max_drawdown=2.0,
        drawdown_limit=5.0,
    ) == ("PAPER_PROMISING",)

    verdicts = conservative_readiness_verdict(
        closed_trades=7,
        realized_pnl=-5.0,
        expectancy=-0.5,
        pnl_excluding_top_3=-1.0,
        top_1_trade_pct=0.90,
        side_correctness_rate=0.0,
        max_drawdown=6.0,
        drawdown_limit=5.0,
    )
    assert "INSUFFICIENT_CLOSED_TRADES" in verdicts
    assert "NEEDS_MORE_DATA" in verdicts
    assert "NEGATIVE_EXPECTANCY" in verdicts
    assert "TAIL_RISK_DOMINATED" in verdicts
    assert "DIRECTIONAL_SIGNAL_FAILED" in verdicts


def test_replay_with_conservative_preset_alias(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    assert main(
        [
            "--db",
            str(db_path),
            "replay",
            "--strategy",
            "momentum",
            "--preset",
            "conservative-tiny",
            "--source",
            "public",
            "--session-id",
            session_id,
            "--active-only",
            "--close-mode",
            "approximate-expiry",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        latest = store.latest_run()
        assert latest is not None
        notes = str(latest["notes"] or "")
        assert "momentum_preset=conservative-tiny" in notes
        assert "max_total_exposure_usd=5.0" in notes
    finally:
        store.close()


def test_conservative_report_output_and_variants(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _seed_session_dataset(db_path, include_end_price=False, later_midpoint=None, include_settlement=False)

    assert main(["--db", str(db_path), "conservative-report", "--source", "public"]) == 0
    output = capsys.readouterr().out
    assert "Conservative report" in output
    assert "Per-session conservative results:" in output
    assert "Aggregate conservative result:" in output
    assert "BTC-only conservative" in output
    assert "ETH-only conservative" in output
    assert "BTC+ETH conservative" in output
    assert "5m-only conservative" in output
    assert "15m-only conservative" in output
    assert "Side correctness by asset:" in output
    assert "Closed trades progress:" in output
    assert "Profitable sessions:" in output
    assert "Aggregate verdict:" in output


def test_validate_conservative_reuses_existing_runs_and_can_rerun(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(["--db", str(db_path), "validate-conservative", "--source", "public"]) == 0
    store = SQLiteStore(db_path)
    try:
        first_count = store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"]
        assert first_count >= 1
    finally:
        store.close()

    assert main(["--db", str(db_path), "validate-conservative", "--source", "public"]) == 0
    store = SQLiteStore(db_path)
    try:
        second_count = store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"]
        assert second_count == first_count
    finally:
        store.close()

    assert main(["--db", str(db_path), "validate-conservative", "--source", "public", "--rerun"]) == 0
    store = SQLiteStore(db_path)
    try:
        third_count = store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"]
        assert third_count > second_count
    finally:
        store.close()


def test_validation_export_package(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    out_dir = tmp_path / "exports"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(["--db", str(db_path), "validate-conservative", "--source", "public"]) == 0
    assert main(
        [
            "--db",
            str(db_path),
            "export",
            "--format",
            "csv",
            "--out",
            str(out_dir),
            "--validation",
            "conservative",
        ]
    ) == 0

    expected = {
        "sessions.csv",
        "raw_snapshots.csv",
        "trades.csv",
        "skipped_opportunities.csv",
        "runs.csv",
        "equity_snapshots.csv",
        "signal_audit_summaries.csv",
        "conservative_report_summary.csv",
    }
    assert expected.issubset({path.name for path in out_dir.glob("*.csv")})
