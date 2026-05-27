from __future__ import annotations

import json

from src.main import main
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def _flatten_session_prices(db_path, session_id: str, price: float = 100_000.0) -> None:
    store = SQLiteStore(db_path)
    try:
        store.conn.execute("UPDATE price_snapshots SET price = ? WHERE session_id = ?", (price, session_id))
        rows = store.rows(
            "SELECT id FROM raw_snapshots WHERE session_id = ? AND snapshot_type = 'exchange_price'",
            (session_id,),
        )
        payload = json.dumps({"price": price})
        for row in rows:
            store.conn.execute("UPDATE raw_snapshots SET payload_json = ? WHERE id = ?", (payload, int(row["id"])))
        store.conn.commit()
    finally:
        store.close()


def _stretch_session_market_to_five_minutes(db_path, session_id: str) -> None:
    store = SQLiteStore(db_path)
    try:
        window_start = "2026-01-01T11:58:00Z"
        window_end = "2026-01-01T12:03:00Z"
        store.conn.execute(
            "UPDATE discovered_markets SET window_start = ?, window_end = ? WHERE session_id = ?",
            (window_start, window_end, session_id),
        )
        store.conn.execute(
            "UPDATE collected_markets SET window_start = ?, window_end = ?",
            (window_start, window_end),
        )
        store.conn.execute(
            "UPDATE collected_market_history SET window_start = ?, window_end = ? WHERE session_id = ?",
            (window_start, window_end, session_id),
        )
        store.conn.commit()
    finally:
        store.close()


def test_signal_and_consistency_audit_align_with_approximate_expiry_tie_break(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _flatten_session_prices(db_path, session_id)

    assert main(
        [
            "--db",
            str(db_path),
            "replay",
            "--strategy",
            "momentum",
            "--source",
            "public",
            "--session-id",
            session_id,
            "--close-mode",
            "approximate-expiry",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
    finally:
        store.close()

    assert main(["--db", str(db_path), "signal-audit", "--run-id", run_id]) == 0
    signal_output = capsys.readouterr().out
    assert "matched=1, mismatched=0" in signal_output
    assert "actual_result=UP" in signal_output

    assert main(["--db", str(db_path), "consistency-audit", "--run-id", run_id]) == 0
    consistency_output = capsys.readouterr().out
    assert "Closed trades checked: 1" in consistency_output
    assert "Consistency OK: 1" in consistency_output
    assert "Positive PnL but side mismatch: 0" in consistency_output
    assert "status=OK" in consistency_output


def test_consistency_audit_flags_positive_pnl_with_side_mismatch(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "replay",
            "--strategy",
            "momentum",
            "--source",
            "public",
            "--session-id",
            session_id,
            "--close-mode",
            "approximate-expiry",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
        store.conn.execute(
            "UPDATE trades SET direction = 'DOWN', token_id = 'BTC-DOWN-TOKEN' WHERE run_id = ?",
            (run_id,),
        )
        store.conn.commit()
    finally:
        store.close()

    assert main(["--db", str(db_path), "consistency-audit", "--run-id", run_id]) == 0
    output = capsys.readouterr().out
    assert "Positive PnL but side mismatch: 1" in output
    assert "status=PNL_POSITIVE_BUT_SIDE_MISMATCH" in output


def test_consistency_audit_flags_negative_pnl_with_side_match(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main(
        [
            "--db",
            str(db_path),
            "replay",
            "--strategy",
            "momentum",
            "--source",
            "public",
            "--session-id",
            session_id,
            "--close-mode",
            "approximate-expiry",
        ]
    ) == 0
    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
        store.conn.execute(
            "UPDATE trades SET pnl = -0.01, result = 'LOSS' WHERE run_id = ?",
            (run_id,),
        )
        store.conn.commit()
    finally:
        store.close()

    assert main(["--db", str(db_path), "consistency-audit", "--run-id", run_id]) == 0
    output = capsys.readouterr().out
    assert "Negative PnL but side match: 1" in output
    assert "status=PNL_NEGATIVE_BUT_SIDE_MATCH" in output


def test_candidate_refresh_and_outsample_use_corrected_side_correctness(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    _flatten_session_prices(db_path, session_id)
    _stretch_session_market_to_five_minutes(db_path, session_id)

    assert main(
        [
            "--db",
            str(db_path),
            "validate-candidate",
            "--candidate",
            "conservative-entry-30-70",
            "--source",
            "public",
            "--refresh",
        ]
    ) == 0

    store = SQLiteStore(db_path)
    try:
        row = store.rows(
            "SELECT matched, mismatched, accepted_trades, closed_trades FROM candidate_session_summaries WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )[0]
        assert int(row["matched"]) == int(row["accepted_trades"])
        assert int(row["mismatched"]) == 0
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
            "2026-01-01T00:00:00Z",
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "out-of-sample | sessions=1" in output
    assert "side_correctness=100.00%" in output
