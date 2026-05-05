from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import AgentConfig
from src.main import main
from src.models import Asset, Direction, Market, OrderBook, OrderLevel, PriceSnapshot, TimingWindow
from src.reports.summary import build_report
from src.storage.sqlite import SQLiteStore
from src.strategies.stuck_state_markov import (
    build_markov_model,
    bucket_for_price,
    evaluate_stuck_markov_market,
    transition_summary,
)


def _book(token_id: str, ask: float, bid: float | None = None, size: float = 20.0) -> OrderBook:
    bid = ask - 0.01 if bid is None else bid
    return OrderBook(
        token_id=token_id,
        bids=(OrderLevel(bid, size),),
        asks=(OrderLevel(ask, size),),
        last_trade_price=(ask + bid) / 2.0,
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="mock://orderbook",
    )


def _seed_markov_dataset(db_path) -> tuple[str, dict[str, Market]]:
    store = SQLiteStore(db_path)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    session_id = store.start_research_session(base, interval_seconds=15.0, cycles_requested=3, notes="stuck-markov-test")
    try:
        stuck_market = Market(
            market_id="btc-stuck",
            slug="btc-stuck-5m",
            title="BTC stuck 5m",
            asset=Asset.BTC,
            window=TimingWindow(base, base + timedelta(minutes=5)),
            up_token_id="BTC-STUCK-UP",
            down_token_id="BTC-STUCK-DOWN",
            source_url="mock://btc-stuck",
        )
        ref_market = Market(
            market_id="btc-ref",
            slug="btc-ref-5m",
            title="BTC ref 5m",
            asset=Asset.BTC,
            window=TimingWindow(base - timedelta(minutes=10), base - timedelta(minutes=5)),
            up_token_id="BTC-REF-UP",
            down_token_id="BTC-REF-DOWN",
            source_url="mock://btc-ref",
        )
        eth_market = Market(
            market_id="eth-stuck",
            slug="eth-stuck-5m",
            title="ETH stuck 5m",
            asset=Asset.ETH,
            window=TimingWindow(base, base + timedelta(minutes=5)),
            up_token_id="ETH-STUCK-UP",
            down_token_id="ETH-STUCK-DOWN",
            source_url="mock://eth-stuck",
        )
        btc_15m_market = Market(
            market_id="btc-long",
            slug="btc-long-15m",
            title="BTC 15m",
            asset=Asset.BTC,
            window=TimingWindow(base, base + timedelta(minutes=15)),
            up_token_id="BTC-LONG-UP",
            down_token_id="BTC-LONG-DOWN",
            source_url="mock://btc-15m",
        )

        cycles = [
            (
                base + timedelta(minutes=1),
                100_000.0,
                {
                    "BTC-STUCK-UP": _book("BTC-STUCK-UP", 0.74),
                    "BTC-STUCK-DOWN": _book("BTC-STUCK-DOWN", 0.26),
                    "BTC-REF-UP": _book("BTC-REF-UP", 0.72),
                    "BTC-REF-DOWN": _book("BTC-REF-DOWN", 0.28),
                    "ETH-STUCK-UP": _book("ETH-STUCK-UP", 0.74),
                    "ETH-STUCK-DOWN": _book("ETH-STUCK-DOWN", 0.26),
                    "BTC-LONG-UP": _book("BTC-LONG-UP", 0.74),
                    "BTC-LONG-DOWN": _book("BTC-LONG-DOWN", 0.26),
                },
            ),
            (
                base + timedelta(minutes=2),
                100_120.0,
                {
                    "BTC-STUCK-UP": _book("BTC-STUCK-UP", 0.74),
                    "BTC-STUCK-DOWN": _book("BTC-STUCK-DOWN", 0.26),
                    "BTC-REF-UP": _book("BTC-REF-UP", 0.74),
                    "BTC-REF-DOWN": _book("BTC-REF-DOWN", 0.26),
                    "ETH-STUCK-UP": _book("ETH-STUCK-UP", 0.74),
                    "ETH-STUCK-DOWN": _book("ETH-STUCK-DOWN", 0.26),
                    "BTC-LONG-UP": _book("BTC-LONG-UP", 0.74),
                    "BTC-LONG-DOWN": _book("BTC-LONG-DOWN", 0.26),
                },
            ),
            (
                base + timedelta(minutes=3),
                100_260.0,
                {
                    "BTC-STUCK-UP": _book("BTC-STUCK-UP", 0.74),
                    "BTC-STUCK-DOWN": _book("BTC-STUCK-DOWN", 0.26),
                    "BTC-REF-UP": _book("BTC-REF-UP", 0.88),
                    "BTC-REF-DOWN": _book("BTC-REF-DOWN", 0.12),
                    "ETH-STUCK-UP": _book("ETH-STUCK-UP", 0.74),
                    "ETH-STUCK-DOWN": _book("ETH-STUCK-DOWN", 0.26),
                    "BTC-LONG-UP": _book("BTC-LONG-UP", 0.74),
                    "BTC-LONG-DOWN": _book("BTC-LONG-DOWN", 0.26),
                },
            ),
        ]

        for observed_at, btc_price, books in cycles:
            store.log_price(PriceSnapshot(Asset.BTC, btc_price, observed_at, "public:BTC"), session_id=session_id)
            store.log_raw_snapshot(observed_at, "public:BTC", "BTC", "exchange_price", {"price": btc_price}, session_id=session_id)
            store.log_price(PriceSnapshot(Asset.ETH, 2_000.0, observed_at, "public:ETH"), session_id=session_id)
            store.log_raw_snapshot(observed_at, "public:ETH", "ETH", "exchange_price", {"price": 2_000.0}, session_id=session_id)
            store.replace_collected_market_data(
                observed_at,
                [stuck_market, ref_market, eth_market, btc_15m_market],
                books,
                source_name="polymarket-public",
                session_id=session_id,
            )
            store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "market_metadata", {"markets": ["btc-stuck-5m", "btc-ref-5m"]}, session_id=session_id)

        expiry_price_time = base + timedelta(minutes=5)
        store.log_price(PriceSnapshot(Asset.BTC, 100_400.0, expiry_price_time, "public:BTC"), session_id=session_id)
        store.log_raw_snapshot(expiry_price_time, "public:BTC", "BTC", "exchange_price", {"price": 100_400.0}, session_id=session_id)
        store.finish_research_session(session_id, expiry_price_time)
        return session_id, {
            "btc_stuck": stuck_market,
            "btc_ref": ref_market,
            "eth_stuck": eth_market,
            "btc_15m": btc_15m_market,
        }
    finally:
        store.close()


def test_stuck_state_detection_and_market_filters(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    session_id, markets = _seed_markov_dataset(db_path)
    store = SQLiteStore(db_path)
    config = AgentConfig(strategy="stuck-markov", use_demo_markets=False)
    try:
        model = build_markov_model(store, source_filter="public", since=None, until=None, session_id=session_id, config=config)
        observed_at = datetime(2026, 1, 1, 12, 3, tzinfo=timezone.utc)

        accepted = evaluate_stuck_markov_market(
            store, model, markets["btc_stuck"], observed_at=observed_at, source_filter="public", session_id=session_id, config=config
        ).decision
        assert accepted.decision == "TRADE"
        assert accepted.state_bucket == "0.70-0.75"
        assert accepted.stuck_cycles == 3

        too_early = evaluate_stuck_markov_market(
            store, model, markets["btc_stuck"], observed_at=datetime(2026, 1, 1, 12, 2, tzinfo=timezone.utc), source_filter="public", session_id=session_id, config=config
        ).decision
        assert too_early.reason == "not enough stuck cycles"

        not_btc = evaluate_stuck_markov_market(
            store, model, markets["eth_stuck"], observed_at=observed_at, source_filter="public", session_id=session_id, config=config
        ).decision
        assert not_btc.reason == "not BTC"

        not_5m = evaluate_stuck_markov_market(
            store, model, markets["btc_15m"], observed_at=observed_at, source_filter="public", session_id=session_id, config=config
        ).decision
        assert not_5m.reason == "not 5m"
    finally:
        store.close()


def test_bucket_transition_matrix_and_markov_report(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id, _markets = _seed_markov_dataset(db_path)
    store = SQLiteStore(db_path)
    config = AgentConfig(strategy="stuck-markov", use_demo_markets=False)
    try:
        model = build_markov_model(store, source_filter="public", since=None, until=None, session_id=session_id, config=config)
        summary = transition_summary(model, Direction.UP, "5m", "0.70-0.75")
        assert summary.total_count >= 1
        assert summary.toward_one_probability > 0.0
        assert bucket_for_price(0.74) == "0.70-0.75"
    finally:
        store.close()

    assert main(["--db", str(db_path), "markov-report", "--source", "public", "--session-id", session_id]) == 0
    stdout = capsys.readouterr().out
    assert "Markov transition report" in stdout
    assert "5m UP:" in stdout
    assert "0.70-0.75" in stdout


def test_replay_stuck_markov_tiny_and_tail_metrics(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id, _markets = _seed_markov_dataset(db_path)

    assert main([
        "--db",
        str(db_path),
        "replay",
        "--strategy",
        "stuck-markov",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
        "--close-mode",
        "approximate-expiry",
    ]) == 0
    replay_out = capsys.readouterr().out
    assert "Replay complete. Strategy: stuck-markov" in replay_out

    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
        report = build_report(store, 1000.0, run_id=run_id)
        assert report.max_position_exposure <= 10.0
        assert report.top_1_trade_pnl is not None
        assert report.verdicts
    finally:
        store.close()

    assert main(["--db", str(db_path), "diagnostics", "--strategy", "stuck-markov", "--source", "public", "--session-id", session_id, "--active-only", "--tiny"]) == 0
    diagnostics_out = capsys.readouterr().out
    assert "stuck_signals_found=" in diagnostics_out
    assert "result_by_bucket=" in diagnostics_out
