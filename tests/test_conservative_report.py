from __future__ import annotations

from types import SimpleNamespace

from src.config import AgentConfig
from src.main import (
    _apply_named_preset,
    _feature_breakdown_lines,
    _frozen_outsample_variants,
    _outsample_verdict,
    _strict_candidate_variants,
    _strict_score,
    main,
)
from src.reports.conservative_report import ConservativeAggregateRow, conservative_readiness_verdict
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


def test_conservative_entry_30_70_named_preset():
    preset = _apply_named_preset(AgentConfig(), "conservative-entry-30-70")
    assert preset.tiny_profile is True
    assert preset.momentum_preset == "conservative-entry-30-70"
    assert preset.momentum_asset_filter == "BTC"
    assert preset.momentum_duration_filter == "5m"
    assert preset.momentum_min_entry_price == 0.30
    assert preset.momentum_max_entry_price == 0.70
    assert preset.max_total_exposure_usd == 5.0
    assert preset.min_seconds_to_expiry == 60
    assert preset.max_seconds_to_expiry == 180


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
    assert "Preset name: conservative-entry-30-70" in output
    assert "Per-session conservative results:" in output
    assert "Aggregate conservative result:" in output
    assert "BTC-only candidate" in output
    assert "ETH-only candidate" in output
    assert "BTC+ETH candidate" in output
    assert "5m-only candidate" in output
    assert "15m-only candidate" in output
    assert "Side correctness by asset:" in output
    assert "Closed trades progress:" in output
    assert "Observation recommendation:" in output
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


def test_candidate_indexes_and_summary_cache(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0

    store = SQLiteStore(db_path)
    try:
        indexes = {
            str(row["name"])
            for row in store.rows(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "idx_raw_snapshots_session_source_type_time" in indexes
        assert "idx_runs_strategy_source_session_notes" in indexes
        assert "idx_trades_run_status" in indexes
        assert "idx_opportunities_run_reason" in indexes
        assert "idx_candidate_session_summaries_lookup" in indexes
        assert store.rows("SELECT COUNT(*) AS count FROM candidate_session_summaries")[0]["count"] == 1
        assert store.rows("SELECT COUNT(*) AS count FROM candidate_aggregate_summaries")[0]["count"] == 1
    finally:
        store.close()


def test_candidate_report_uses_cache_and_refresh_does_not_duplicate_runs(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        raw_count = store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"]
        run_count = store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"]
    finally:
        store.close()

    assert main(
        [
            "--db",
            str(db_path),
            "candidate-report",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    report_output = capsys.readouterr().out
    assert "Preset name: conservative-entry-30-70" in report_output
    assert "Aggregate conservative result:" in report_output

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    validate_output = capsys.readouterr().out
    assert "runs_reused=1" in validate_output
    assert "runs_created=0" in validate_output

    assert main(
        [
            "--db",
            str(db_path),
            "candidate-report",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
            "--refresh",
        ]
    ) == 0
    refresh_output = capsys.readouterr().out
    assert "Aggregate conservative result:" in refresh_output

    store = SQLiteStore(db_path)
    try:
        assert store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"] == raw_count
        assert store.rows("SELECT COUNT(*) AS count FROM runs")[0]["count"] == run_count
        assert store.rows("SELECT COUNT(*) AS count FROM candidate_aggregate_summaries")[0]["count"] == 1
    finally:
        store.close()


def test_preset_report_and_compare_candidates(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _seed_session_dataset(db_path, include_end_price=False, later_midpoint=None, include_settlement=False)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-conservative",
            "--preset",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0

    assert main(
        [
            "--db",
            str(db_path),
            "preset-report",
            "--preset",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    report_output = capsys.readouterr().out
    assert "Preset name: conservative-entry-30-70" in report_output
    assert "Closed trades still needed:" in report_output
    assert "Approximate sessions still needed:" in report_output

    assert main(["--db", str(db_path), "compare-candidates", "--source", "public"]) == 0
    compare_output = capsys.readouterr().out
    assert "Candidate comparison" in compare_output
    assert "conservative-tiny" in compare_output
    assert "conservative-entry-30-70" in compare_output
    assert "reverse conservative" in compare_output
    assert "entry-0.40-0.75" in compare_output
    assert "DOWN-only" in compare_output
    assert "expiry-120-180" in compare_output


def test_candidate_alias_commands_and_export(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    out_dir = tmp_path / "exports"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    validate_output = capsys.readouterr().out
    assert "Conservative validation" in validate_output
    assert "Preset: conservative-entry-30-70" in validate_output

    assert main(
        [
            "--db",
            str(db_path),
            "candidate-report",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    report_output = capsys.readouterr().out
    assert "Preset name: conservative-entry-30-70" in report_output

    assert main(
        [
            "--db",
            str(db_path),
            "export",
            "--format",
            "csv",
            "--out",
            str(out_dir),
            "--candidate",
            "conservative-entry-30-70",
        ]
    ) == 0
    exported = {path.name for path in out_dir.glob("*.csv")}
    assert "runs.csv" in exported
    runs_text = (out_dir / "runs.csv").read_text(encoding="utf-8")
    assert "conservative-entry-30-70" in runs_text


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


def test_degradation_audit_and_feature_breakdown_output(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.30, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    assert main(
        [
            "--db",
            str(db_path),
            "degradation-audit",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Degradation audit" in output
    assert "Matched vs mismatched feature breakdown:" in output
    assert "Performance trend:" in output


def test_matched_vs_mismatched_feature_breakdown_buckets():
    rows = [
        SimpleNamespace(
            side="UP",
            entry_price=0.45,
            seconds_to_expiry=100,
            spread=0.008,
            edge_at_entry=0.06,
            entry_timestamp=SimpleNamespace(hour=7),
            pre_entry_exchange_move=0.0001,
            post_entry_exchange_move=0.0005,
            side_matched=True,
            pnl=1.2,
        ),
        SimpleNamespace(
            side="DOWN",
            entry_price=0.35,
            seconds_to_expiry=160,
            spread=0.015,
            edge_at_entry=0.10,
            entry_timestamp=SimpleNamespace(hour=15),
            pre_entry_exchange_move=-0.0005,
            post_entry_exchange_move=-0.0005,
            side_matched=False,
            pnl=-1.0,
        ),
    ]
    output = "\n".join(_feature_breakdown_lines(rows))
    assert "entry price:" in output
    assert "0.30-0.40" in output
    assert "0.40-0.50" in output
    assert "seconds-to-expiry:" in output
    assert "spread:" in output
    assert "edge:" in output


def test_strict_candidate_sweep_and_ranking_use_cache(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        raw_count = store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"]
    finally:
        store.close()

    assert main(
        [
            "--db",
            str(db_path),
            "strict-candidate-sweep",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    sweep_output = capsys.readouterr().out
    assert "Strict candidate sweep" in sweep_output
    assert "base conservative-entry-30-70" in sweep_output
    assert "entry-0.40-0.70" in sweep_output
    assert "No new preset added automatically." in sweep_output

    assert main(["--db", str(db_path), "strict-candidate-ranking", "--source", "public"]) == 0
    ranking_output = capsys.readouterr().out
    assert "Strict candidate ranking" in ranking_output
    assert "Top variants overall:" in ranking_output
    assert "Recommended next research candidate:" in ranking_output

    assert main(["--db", str(db_path), "strict-candidate-ranking", "--source", "public"]) == 0
    cached_output = capsys.readouterr().out
    assert "summaries_reused=" in cached_output

    assert main(["--db", str(db_path), "strict-candidate-ranking", "--source", "public", "--refresh"]) == 0
    refreshed_output = capsys.readouterr().out
    assert "summaries_refreshed=" in refreshed_output

    store = SQLiteStore(db_path)
    try:
        assert store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"] == raw_count
    finally:
        store.close()


def test_strict_variant_filter_predicates():
    config = _apply_named_preset(AgentConfig(), "conservative-entry-30-70")
    variants = {label: predicate for label, _variant_config, predicate in _strict_candidate_variants(config)}
    row = SimpleNamespace(entry_price=0.45, side="UP", edge_at_entry=0.06, spread=0.008, seconds_to_expiry=100)
    assert variants["entry-0.40-0.70"](row)
    assert variants["UP-only"](row)
    assert not variants["DOWN-only"](row)
    assert variants["edge >= 0.05"](row)
    assert not variants["edge >= 0.08"](row)
    assert variants["spread <= 0.01"](row)
    assert not variants["spread <= 0.005"](row)
    assert variants["seconds-to-expiry 90-150"](row)
    assert variants["entry-0.40-0.70 + edge >= 0.05"](row)


def test_strict_ranking_penalizes_side_correctness_and_tail_risk():
    strong = ConservativeAggregateRow(
        label="strong",
        sessions_tested=5,
        accepted_trades=40,
        closed_trades=35,
        realized_pnl=12.0,
        win_rate=0.60,
        expectancy=0.34,
        max_drawdown=1.0,
        max_exposure=1.0,
        top_1_trade_pct=0.20,
        pnl_excluding_top_1=9.0,
        pnl_excluding_top_3=5.0,
        settlement_unavailable=0,
        matched=21,
        mismatched=14,
        unknown=0,
        side_correctness_rate=0.60,
        warnings=(),
        verdicts=("NEEDS_MORE_DATA",),
    )
    weak_side = ConservativeAggregateRow(
        **{**strong.__dict__, "label": "weak_side", "side_correctness_rate": 0.48, "matched": 17, "mismatched": 18}
    )
    tail_risk = ConservativeAggregateRow(
        **{
            **strong.__dict__,
            "label": "tail_risk",
            "top_1_trade_pct": 0.75,
            "pnl_excluding_top_3": -1.0,
            "verdicts": ("TAIL_RISK_DOMINATED",),
        }
    )
    assert _strict_score(strong) > _strict_score(weak_side)
    assert _strict_score(strong) > _strict_score(tail_risk)


def test_outsample_report_splits_sessions_by_cutoff(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    first = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    second = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        store.conn.execute(
            "UPDATE research_sessions SET started_at = ?, ended_at = ? WHERE session_id = ?",
            ("2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", first),
        )
        store.conn.execute(
            "UPDATE research_sessions SET started_at = ?, ended_at = ? WHERE session_id = ?",
            ("2026-01-02T10:00:00+00:00", "2026-01-02T11:00:00+00:00", second),
        )
        store.conn.commit()
    finally:
        store.close()
    assert main(
        [
            "--db",
            str(db_path),
            "outsample-report",
            "--source",
            "public",
            "--since",
            "2026-01-02T00:00:00Z",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Out-of-sample report" in output
    assert "Frozen variants:" in output
    assert "in-sample | sessions=1" in output
    assert "out-of-sample | sessions=1" in output
    assert "Out-of-sample promising variants:" in output


def test_frozen_outsample_variants_are_stable():
    labels = [label for label, _predicate in _frozen_outsample_variants()]
    assert labels == [
        "base conservative-entry-30-70",
        "expiry-90-150",
        "UP-only entry-0.40-0.70",
        "entry-0.40-0.50",
        "entry-0.40-0.70",
        "near-flat pre-entry BTC move",
        "entry-0.40-0.70 + expiry-90-150",
        "UP-only entry-0.40-0.70 + expiry-90-150",
    ]
    row = SimpleNamespace(
        side="UP",
        entry_price=0.45,
        seconds_to_expiry=100,
        pre_entry_exchange_move=0.0001,
    )
    predicates = dict(_frozen_outsample_variants())
    assert predicates["expiry-90-150"](row)
    assert predicates["UP-only entry-0.40-0.70"](row)
    assert predicates["entry-0.40-0.50"](row)
    assert predicates["near-flat pre-entry BTC move"](row)


def test_outsample_promotion_gate_requires_sample_side_and_tail_quality():
    promising = {
        "closed_trades": 35,
        "side_correctness_rate": 0.60,
        "expectancy": 0.20,
        "pnl_excluding_top_3": 5.0,
        "top_1_trade_pct": 0.25,
        "trend": "mixed",
        "profitable_sessions": 6,
        "sessions_with_closed": 10,
    }
    assert _outsample_verdict(promising) == ("OUTSAMPLE_PROMISING",)

    low_sample = {**promising, "closed_trades": 29}
    assert "NEEDS_MORE_OUTSAMPLE_DATA" in _outsample_verdict(low_sample)

    weak_side = {**promising, "side_correctness_rate": 0.55}
    assert "OUTSAMPLE_FAILED_SIDE_CORRECTNESS" in _outsample_verdict(weak_side)

    tail_risk = {**promising, "top_1_trade_pct": 0.55}
    assert "OUTSAMPLE_TAIL_RISK" in _outsample_verdict(tail_risk)


def test_validation_target_output_and_no_raw_deletion(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    store = SQLiteStore(db_path)
    try:
        raw_before = store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"]
    finally:
        store.close()

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        store.conn.execute(
            "UPDATE research_sessions SET started_at = ?, ended_at = ?, duration_seconds = ? WHERE session_id = ?",
            ("2026-01-02T10:00:00+00:00", "2026-01-02T11:00:00+00:00", 3600.0, session_id),
        )
        store.conn.commit()
    finally:
        store.close()
    assert main(
        [
            "--db",
            str(db_path),
            "validation-target",
            "--source",
            "public",
            "--since",
            "2026-01-02T00:00:00Z",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Validation target" in output
    assert "closed_needed_for_30=" in output
    assert "estimated_more_hours=" in output

    store = SQLiteStore(db_path)
    try:
        assert store.rows("SELECT COUNT(*) AS count FROM raw_snapshots")[0]["count"] == raw_before
    finally:
        store.close()


def test_validate_candidate_explains_missing_exchange_price_sessions(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    store = SQLiteStore(db_path)
    try:
        store.conn.execute("DELETE FROM price_snapshots WHERE session_id = ?", (session_id,))
        store.conn.execute(
            "DELETE FROM raw_snapshots WHERE session_id = ? AND snapshot_type = 'exchange_price'",
            (session_id,),
        )
        store.conn.commit()
    finally:
        store.close()

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert f"{session_id} | reason=MISSING_EXCHANGE_PRICES" in output
    assert "exchange_price_snapshots=0" in output
    assert "stale_exchange_prices=" in output
