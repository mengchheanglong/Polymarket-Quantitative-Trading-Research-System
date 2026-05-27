from datetime import datetime, timedelta, timezone

from src.main import main
from src.models import Asset, PriceSnapshot
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def _seed_divergent_btc_session(db_path):
    store = SQLiteStore(db_path)
    started = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
    session_id = store.start_research_session(started, interval_seconds=15.0, cycles_requested=3, notes="test")
    try:
        stale_exchange_time = (started - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        rows = [
            (started, "coinbase:BTC-USD", 79_642.90, stale_exchange_time),
            (started, "kraken:XBTUSD", 79_818.90, None),
            (started + timedelta(seconds=15), "coinbase:BTC-USD", 79_642.90, stale_exchange_time),
            (started + timedelta(seconds=15), "kraken:XBTUSD", 79_841.00, None),
            (started + timedelta(seconds=30), "coinbase:BTC-USD", 79_642.90, stale_exchange_time),
            (started + timedelta(seconds=30), "kraken:XBTUSD", 79_850.40, None),
        ]
        for observed_at, source, price, exchange_timestamp in rows:
            snapshot = PriceSnapshot(Asset.BTC, price, observed_at, source)
            store.log_price(snapshot, session_id=session_id)
            payload = {"price": price}
            if exchange_timestamp is not None:
                payload["exchange_timestamp"] = exchange_timestamp
            store.log_raw_snapshot(
                observed_at,
                source,
                "BTC",
                "exchange_price",
                payload,
                session_id=session_id,
            )
        store.finish_research_session(session_id, started + timedelta(seconds=45))
        return session_id
    finally:
        store.close()


def test_exchange_divergence_and_stale_repeat_detected(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_divergent_btc_session(db_path)

    store = SQLiteStore(db_path)
    try:
        quality = store.exchange_price_quality_summary(source_filter="public", session_id=session_id, asset="BTC")
        latest = store.latest_price("BTC", source_filter="public", session_id=session_id)
        coinbase_visible = store.latest_price(
            "BTC",
            source_prefix="coinbase:BTC-USD",
            source_filter="public",
            session_id=session_id,
        )
        assert quality["divergence_count"] == 3
        assert quality["stale_repeat_count_by_source"]["coinbase:BTC-USD"] >= 2
        assert quality["suspect_snapshot_count_by_source"]["coinbase:BTC-USD"] >= 2
        assert quality["cycles_excluded_due_to_exchange_quality"] == 0
        assert latest is not None
        assert latest.source == "kraken:XBTUSD"
        assert latest.price == 79_850.40
        assert coinbase_visible is None
    finally:
        store.close()


def test_approximate_expiry_marks_settlement_unavailable_when_exchange_quality_unresolved(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=None)
    end = datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc)
    store = SQLiteStore(db_path)
    try:
        end_iso = end.isoformat().replace("+00:00", "Z")
        store.conn.execute(
            "DELETE FROM price_snapshots WHERE session_id = ? AND observed_at = ? AND asset = 'BTC'",
            (session_id, end_iso),
        )
        store.conn.execute(
            "DELETE FROM raw_snapshots WHERE session_id = ? AND observed_at = ? AND snapshot_type = 'exchange_price' AND asset = 'BTC'",
            (session_id, end_iso),
        )
        store.conn.commit()
        for source, price in (("coinbase:BTC-USD", 100_220.0), ("kraken:XBTUSD", 100_420.0)):
            snapshot = PriceSnapshot(Asset.BTC, price, end, source)
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(
                end,
                source,
                "BTC",
                "exchange_price",
                {"price": price},
                session_id=session_id,
            )
    finally:
        store.close()

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
        row = store.rows("SELECT status, settlement_note FROM trades ORDER BY opened_at LIMIT 1")[0]
        assert row["status"] == "SETTLEMENT_UNAVAILABLE"
        assert "EXCHANGE_PRICE_QUALITY" in str(row["settlement_note"])
    finally:
        store.close()


def test_session_report_prints_exchange_quality_diagnostics(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_divergent_btc_session(db_path)

    assert main(["--db", str(db_path), "session-report", "--session-id", session_id]) == 0
    output = capsys.readouterr().out
    assert "Exchange quality:" in output
    assert "divergence_count=3" in output
    assert "stale_repeat_by_source=" in output
    assert "suspect_by_source=" in output
    assert "Session replay safety: SAFE_FOR_REPLAY" in output


def test_candidate_and_outsample_reports_warn_on_exchange_price_quality(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=None)
    store = SQLiteStore(db_path)
    try:
        for observed_at, source, price in (
            (datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), "coinbase:BTC-USD", 100_120.0),
            (datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), "kraken:XBTUSD", 100_121.0),
            (datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc), "coinbase:BTC-USD", 100_220.0),
            (datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc), "kraken:XBTUSD", 100_420.0),
        ):
            store.log_price(PriceSnapshot(Asset.BTC, price, observed_at, source), session_id=session_id)
            store.log_raw_snapshot(
                observed_at,
                source,
                "BTC",
                "exchange_price",
                {"price": price},
                session_id=session_id,
            )
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
            "--refresh",
        ]
    ) == 0
    _ = capsys.readouterr().out

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
    report_output = capsys.readouterr().out
    assert "EXCHANGE_PRICE_QUALITY_WARNING" in report_output

    assert main(
        [
            "--db",
            str(db_path),
            "outsample-report",
            "--source",
            "public",
            "--since",
            "2025-12-31T00:00:00Z",
        ]
    ) == 0
    outsample_output = capsys.readouterr().out
    assert "EXCHANGE_PRICE_QUALITY_WARNING" in outsample_output
