from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.collectors.polymarket import _candidate_to_market
from src.config import AgentConfig
from src.main import _apply_named_preset, main
from src.models import Asset, Candle, DiscoveredMarket, MarketClassification, OrderBook, OrderLevel, PriceSnapshot
from src.storage.sqlite import SQLiteStore


def _seed_directional_session(db_path, *, observed_at: datetime, end_price: float) -> str:
    store = SQLiteStore(db_path)
    session_id = store.start_research_session(observed_at, interval_seconds=15.0, cycles_requested=1, notes="test")
    try:
        start = observed_at - timedelta(minutes=3)
        end = observed_at + timedelta(minutes=2)
        candles = [
            Candle(observed_at - timedelta(minutes=3), 99.0, 101.0, 100.0, 100.5, 1.0),
            Candle(observed_at - timedelta(minutes=2), 100.0, 102.0, 100.5, 101.0, 1.0),
            Candle(observed_at - timedelta(minutes=1), 101.0, 103.0, 101.0, 102.0, 1.0),
        ]
        for ts, price in (
            (start, 100_000.0),
            (observed_at, 100_120.0),
            (end, end_price),
        ):
            snapshot = PriceSnapshot(Asset.BTC, price, ts, "public:BTC")
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(ts, snapshot.source, "BTC", "exchange_price", {"price": price}, session_id=session_id)
        store.log_candles("BTC", candles, source="public:BTC", observed_at=observed_at, session_id=session_id)
        candidate = DiscoveredMarket(
            market_id=f"btc-test-{observed_at.timestamp()}",
            slug=f"btc-updown-5m-{int(observed_at.timestamp())}",
            title="Bitcoin Up or Down - test",
            asset_label="BTC",
            classification=MarketClassification.CRYPTO_UP_DOWN,
            classification_reasons=("contains BTC", "contains up/down language", "has token ids", "has public orderbook"),
            token_status="FOUND",
            orderbook_status="FOUND",
            active=True,
            closed=False,
            accepting_orders=True,
            up_token_id=f"BTC-UP-{int(observed_at.timestamp())}",
            down_token_id=f"BTC-DOWN-{int(observed_at.timestamp())}",
            condition_id=f"cond-{int(observed_at.timestamp())}",
            window_start=start,
            window_end=end,
            source_url="mock://public",
            accepted=True,
            reason="accepted",
        )
        market = _candidate_to_market(candidate)
        books = {
            candidate.up_token_id: OrderBook(candidate.up_token_id, bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            candidate.down_token_id: OrderBook(candidate.down_token_id, bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
        }
        store.log_discovered_markets(observed_at, "polymarket-public", [candidate], session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "market_metadata", {"markets": [candidate.slug]}, session_id=session_id)
        store.replace_collected_market_data(observed_at, [market], books, source_name="polymarket-public", session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "orderbook", {"token_id": candidate.up_token_id}, session_id=session_id)
        store.log_raw_snapshot(observed_at, "polymarket-public", "BTC", "orderbook", {"token_id": candidate.down_token_id}, session_id=session_id)
        store.finish_research_session(session_id, end + timedelta(seconds=5))
        return session_id
    finally:
        store.close()


def _run_conservative(db_path, session_id: str, *, preset: str = "conservative-tiny") -> str:
    assert main(
        [
            "--db",
            str(db_path),
            "replay",
            "--strategy",
            "momentum",
            "--preset",
            preset,
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
        return str(latest["run_id"])
    finally:
        store.close()


def test_conservative_reverse_preset_alias():
    preset = _apply_named_preset(AgentConfig(), "conservative-tiny-reverse")
    assert preset.momentum_preset == "conservative-tiny-reverse"
    assert preset.reverse_signal is True
    assert preset.tiny_profile is True


def test_side_audit_summary_and_details(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_up = _seed_directional_session(
        db_path,
        observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_price=100_220.0,
    )
    session_down = _seed_directional_session(
        db_path,
        observed_at=datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc),
        end_price=99_900.0,
    )
    _run_conservative(db_path, session_up)
    _run_conservative(db_path, session_down)

    assert main(["--db", str(db_path), "side-audit", "--source", "public"]) == 0
    output = capsys.readouterr().out
    assert "Side audit" in output
    assert "Matched side count:" in output
    assert "Correctness by asset:" in output
    assert "Correctness by edge bucket:" in output
    assert "Mismatch signal-lag flags:" in output

    assert main(["--db", str(db_path), "side-audit", "--source", "public", "--details"]) == 0
    detail_output = capsys.readouterr().out
    assert "Accepted-trade feature rows:" in detail_output
    assert "match_status=" in detail_output
    assert "verdict_flags=" in detail_output
    assert "pre_entry_move=" in detail_output
    assert "post_entry_move=" in detail_output


def test_reverse_signal_replay_flips_direction(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_directional_session(
        db_path,
        observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_price=100_220.0,
    )
    normal_run_id = _run_conservative(db_path, session_id, preset="conservative-tiny")
    reverse_run_id = _run_conservative(db_path, session_id, preset="conservative-tiny-reverse")

    store = SQLiteStore(db_path)
    try:
        normal_trade = store.trade_rows(normal_run_id)[0]
        reverse_trade = store.trade_rows(reverse_run_id)[0]
        reverse_notes = str(store.run_by_id(reverse_run_id)["notes"] or "")
    finally:
        store.close()

    assert normal_trade["direction"] != reverse_trade["direction"]
    assert "reverse_signal=true" in reverse_notes

    assert main(["--db", str(db_path), "signal-audit", "--run-id", reverse_run_id]) == 0
    audit_output = capsys.readouterr().out
    assert "Signal audit" in audit_output
    assert "side_matched=" in audit_output


def test_side_sweep_and_candidate_ranking(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    _run_conservative(
        db_path,
        _seed_directional_session(
            db_path,
            observed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            end_price=100_220.0,
        ),
    )
    _run_conservative(
        db_path,
        _seed_directional_session(
            db_path,
            observed_at=datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc),
            end_price=99_900.0,
        ),
    )

    assert main(["--db", str(db_path), "side-sweep", "--source", "public"]) == 0
    sweep_output = capsys.readouterr().out
    assert "Side sweep" in sweep_output
    assert "conservative-tiny" in sweep_output
    assert "reverse conservative" in sweep_output
    assert "conservative-entry-30-70" in sweep_output
    assert "expiry-60-120" in sweep_output

    assert main(["--db", str(db_path), "candidate-ranking", "--source", "public"]) == 0
    ranking_output = capsys.readouterr().out
    assert "Candidate ranking" in ranking_output
    assert "1. " in ranking_output
    assert "verdicts=" in ranking_output
