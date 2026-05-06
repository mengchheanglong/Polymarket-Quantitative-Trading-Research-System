from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.main import main
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def _inflate_session_raw_snapshots(db_path, session_id: str, *, count: int = 1205) -> None:
    store = SQLiteStore(db_path)
    try:
        base = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
        for index in range(count):
            observed_at = base + timedelta(seconds=index)
            snapshot_type = "orderbook" if index % 2 == 0 else "market_metadata"
            payload = {"index": index, "kind": snapshot_type}
            store.log_raw_snapshot(
                observed_at,
                "polymarket-public",
                "BTC",
                snapshot_type,
                payload,
                session_id=session_id,
            )
    finally:
        store.close()


def test_dataset_summary_large_session_does_not_hit_sqlite_variable_limit(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _inflate_session_raw_snapshots(db_path, session_id, count=1505)

    store = SQLiteStore(db_path)
    try:
        summary = store.dataset_summary(source_filter="public", session_id=session_id)
        assert summary["total_snapshots"] > 1500
        assert summary["market_snapshots"] > 0
        assert summary["orderbook_snapshots"] > 0
    finally:
        store.close()


def test_session_report_and_candidate_validation_work_on_large_session(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _inflate_session_raw_snapshots(db_path, session_id, count=1505)

    store = SQLiteStore(db_path)
    try:
        before_count = len(store.raw_snapshot_rows(session_id=session_id))
    finally:
        store.close()

    assert main(["--db", str(db_path), "session-report", "--session-id", session_id]) == 0
    session_output = capsys.readouterr().out
    assert "Research session report" in session_output
    assert f"Session ID: {session_id}" in session_output

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

    store = SQLiteStore(db_path)
    try:
        after_count = len(store.raw_snapshot_rows(session_id=session_id))
        assert after_count == before_count
    finally:
        store.close()


def test_observe_finalization_handles_large_summary(monkeypatch, tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    def fake_collect(config, session_id=None):
        store = SQLiteStore(config.database_path)
        try:
            base = datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)
            for index in range(1305):
                observed_at = base + timedelta(seconds=index)
                store.log_raw_snapshot(
                    observed_at,
                    "polymarket-public",
                    "BTC",
                    "orderbook",
                    {"index": index},
                    session_id=session_id,
                )
            return 0
        finally:
            store.close()

    monkeypatch.setattr("src.main.collect", fake_collect)

    assert main(
        [
            "--db",
            str(db_path),
            "observe",
            "--cycles",
            "1",
            "--interval-seconds",
            "0",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "Observe complete." in output
    assert "Session summary: session_id=" in output

    store = SQLiteStore(db_path)
    try:
        session = store.latest_research_session()
        assert session is not None
        assert int(session["snapshot_count"]) >= 1305
    finally:
        store.close()
