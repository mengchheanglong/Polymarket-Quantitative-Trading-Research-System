from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.collectors.polymarket import _candidate_to_market
from src.config import AgentConfig
from src.main import _apply_momentum_preset, main
from src.models import Asset, Candle, DiscoveredMarket, MarketClassification, OrderBook, OrderLevel, PriceSnapshot
from src.reports.diagnostics import build_diagnostics
from src.reports.summary import build_report
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def _seed_divergent_session_dataset(db_path) -> str:
    store = SQLiteStore(db_path)
    observed_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session_id = store.start_research_session(observed_at, interval_seconds=15.0, cycles_requested=1, notes="divergence")
    try:
        start = observed_at - timedelta(minutes=1)
        end = observed_at + timedelta(minutes=2)
        candles = [
            Candle(observed_at - timedelta(minutes=3), 99.0, 101.0, 100.0, 100.5, 1.0),
            Candle(observed_at - timedelta(minutes=2), 100.0, 102.0, 100.5, 101.0, 1.0),
            Candle(observed_at - timedelta(minutes=1), 101.0, 103.0, 101.0, 102.0, 1.0),
        ]
        for ts, price in (
            (start, 100_000.0),
            (observed_at, 100_120.0),
            (end, 99_800.0),
        ):
            snapshot = PriceSnapshot(Asset.BTC, price, ts, "public:BTC")
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(ts, snapshot.source, "BTC", "exchange_price", {"price": price}, session_id=session_id)
        store.log_candles("BTC", candles, source="public:BTC", observed_at=observed_at, session_id=session_id)
        candidate = DiscoveredMarket(
            market_id="btc-test",
            slug="btc-updown-5m-1767272400",
            title="Bitcoin Up or Down - test",
            asset_label="BTC",
            classification=MarketClassification.CRYPTO_UP_DOWN,
            classification_reasons=("contains BTC",),
            token_status="FOUND",
            orderbook_status="FOUND",
            active=True,
            closed=False,
            accepting_orders=True,
            up_token_id="BTC-UP-TOKEN",
            down_token_id="BTC-DOWN-TOKEN",
            condition_id="cond-btc-test",
            window_start=start,
            window_end=end,
            source_url="mock://public",
            accepted=True,
            reason="accepted",
        )
        market = _candidate_to_market(candidate)
        books = {
            "BTC-UP-TOKEN": OrderBook("BTC-UP-TOKEN", bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            "BTC-DOWN-TOKEN": OrderBook("BTC-DOWN-TOKEN", bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
        }
        store.log_discovered_markets(observed_at, "polymarket-public", [candidate], session_id=session_id)
        store.replace_collected_market_data(observed_at, [market], books, source_name="polymarket-public", session_id=session_id)
        later = end - timedelta(seconds=10)
        later_books = {
            "BTC-UP-TOKEN": OrderBook("BTC-UP-TOKEN", bids=(OrderLevel(0.69, 100),), asks=(OrderLevel(0.71, 100),), last_trade_price=0.70),
            "BTC-DOWN-TOKEN": OrderBook("BTC-DOWN-TOKEN", bids=(OrderLevel(0.29, 100),), asks=(OrderLevel(0.31, 100),), last_trade_price=0.30),
        }
        store.replace_collected_market_data(later, [market], later_books, source_name="polymarket-public", session_id=session_id)
        store.finish_research_session(session_id, end + timedelta(seconds=5))
        return session_id
    finally:
        store.close()


def test_report_and_diagnostics_include_accepted_trade_edge_metrics(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main([
        "--db",
        str(db_path),
        "replay",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
        "--close-mode",
        "approximate-expiry",
    ]) == 0

    store = SQLiteStore(db_path)
    try:
        report = build_report(store, 1000.0)
        diagnostics = build_diagnostics(store, AgentConfig(), run_id=store.latest_run_id())
    finally:
        store.close()

    text = report.as_text()
    assert "Average accepted-trade edge:" in text
    assert "Average skipped-trade edge:" in text
    assert "Accepted trades by side:" in text
    assert "Accepted trades by duration:" in text
    assert "accepted_edge_stats=" in diagnostics
    assert "accepted_trades_by_side=" in diagnostics
    assert "accepted_trades_by_duration=" in diagnostics


def test_signal_audit_output(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    assert main([
        "--db",
        str(db_path),
        "replay",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
        "--close-mode",
        "approximate-expiry",
    ]) == 0
    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
    finally:
        store.close()
    assert run_id is not None
    assert main(["--db", str(db_path), "signal-audit", "--run-id", run_id]) == 0
    output = capsys.readouterr().out
    assert "Signal audit" in output
    assert "actual_result=" in output
    assert "side_matched=" in output
    assert "edge_at_entry=" in output


def test_close_divergence_warning_and_directional_failure(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_divergent_session_dataset(db_path)
    assert main([
        "--db",
        str(db_path),
        "replay",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
        "--close-mode",
        "mark-to-market",
    ]) == 0
    assert main([
        "--db",
        str(db_path),
        "replay",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
        "--close-mode",
        "approximate-expiry",
    ]) == 0
    store = SQLiteStore(db_path)
    try:
        report = build_report(store, 1000.0)
        report_text = report.as_text()
    finally:
        store.close()
    assert "CLOSE_MODE_DIVERGENCE" in report_text
    assert "DIRECTIONAL_SIGNAL_FAILED" in report_text

    assert main([
        "--db",
        str(db_path),
        "close-divergence",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
    ]) == 0
    output = capsys.readouterr().out
    assert "Close mode divergence report" in output
    assert "Winning in mark-to-market but losing at approximate-expiry" in output
    assert "Divergent duration breakdown:" in output


def test_momentum_audit_and_presets(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)
    assert main([
        "--db",
        str(db_path),
        "momentum-audit",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--tiny",
    ]) == 0
    output = capsys.readouterr().out
    assert "Momentum audit" in output
    assert "up-only" in output
    assert "down-only" in output
    assert "btc-only" in output
    assert "eth-only" in output
    assert "5m-only" in output
    assert "15m-only" in output
    assert "balanced-tiny-momentum" in output
    assert "conservative-tiny-momentum" in output
    assert "Timing buckets by close mode:" in output


def test_conservative_preset_behavior():
    preset = _apply_momentum_preset(AgentConfig(), "conservative-tiny-momentum")
    assert preset.tiny_profile is True
    assert preset.momentum_asset_filter == "BTC"
    assert preset.momentum_duration_filter == "5m"
    assert preset.momentum_min_entry_price == 0.05
    assert preset.momentum_max_entry_price == 0.85
