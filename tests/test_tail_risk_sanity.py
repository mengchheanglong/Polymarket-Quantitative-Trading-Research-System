from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.main import main
from src.models import Asset, Direction, Market, OpportunityDecision, Signal, TimingWindow
from src.reports.summary import build_report
from src.storage.sqlite import SQLiteStore
from tests.test_lifecycle_close_modes import _seed_session_dataset


def _market(slug: str, now: datetime) -> Market:
    return Market(
        market_id=slug,
        slug=slug,
        title=slug,
        asset=Asset.BTC,
        window=TimingWindow(now - timedelta(minutes=1), now + timedelta(minutes=5)),
        up_token_id=f"{slug}-up",
        down_token_id=f"{slug}-down",
        source_url="mock://tail-risk",
        is_mock=False,
        observed_at=now,
        latest_observed_at=now,
    )


def _seed_tail_risk_run(db_path) -> str:
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    run_id = store.start_run(
        "momentum",
        "replay",
        "public",
        1000.0,
        now,
        notes="close_mode=approximate-expiry; tiny_profile=true",
    )
    try:
        pnls = [120.0, 90.0, -1.0, -1.0, -1.0]
        entry_prices = [0.02, 0.03, 0.40, 0.50, 0.50]
        edges = [-0.08, -0.07, -0.06, -0.05, -0.04]
        for idx, (pnl, entry_price, edge) in enumerate(zip(pnls, entry_prices, edges), start=1):
            opened_at = now + timedelta(seconds=idx)
            market = _market(f"tail-market-{idx}", opened_at)
            store.log_opportunity(
                opened_at,
                OpportunityDecision(
                    market=market,
                    signal=Signal(Asset.BTC, Direction.UP, 0.50, edge, "tail test"),
                    market_price=entry_price,
                    spread=0.01,
                    decision="TRADE",
                    reason="paper trade accepted",
                    seconds_to_expiry=180.0,
                    lifecycle_status="active",
                    timing_bucket="valid_window",
                ),
                run_id=run_id,
            )
            fill = SimpleNamespace(
                trade_id=str(uuid4()),
                market=market,
                direction=Direction.UP,
                entry_price=entry_price,
                shares=1.0,
                notional=entry_price,
                entry_fee=0.0,
                slippage_cost=0.0,
                entry_underlying_price=100_000.0,
            )
            store.open_trade(opened_at, fill, run_id=run_id)
            store.close_trade(
                now=opened_at + timedelta(minutes=5),
                trade_id=fill.trade_id,
                exit_underlying_price=100_200.0 if pnl >= 0 else 99_800.0,
                exit_price=1.0 if pnl >= 0 else 0.0,
                exit_fee=0.0,
                pnl=pnl,
                result="WIN" if pnl >= 0 else "LOSS",
                status="CLOSED_BY_EXPIRY",
                close_mode="approximate-expiry",
                settlement_note="approximate expiry from stored exchange prices",
            )
        store.finish_run(run_id, now + timedelta(minutes=6))
        return run_id
    finally:
        store.close()


def test_profit_concentration_and_tail_risk_verdicts(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    run_id = _seed_tail_risk_run(db_path)
    store = SQLiteStore(db_path)
    try:
        report = build_report(store, 1000.0, run_id=run_id)
        text = report.as_text()
    finally:
        store.close()

    assert report.top_1_trade_pnl == 120.0
    assert report.top_3_trades_pnl == 209.0
    assert report.pnl_excluding_top_1 == 87.0
    assert report.pnl_excluding_top_3 == -2.0
    assert report.low_price_trade_count_below_0_05 == 2
    assert report.pnl_from_entry_price_below_0_05 == 210.0
    assert report.warnings == (
        "TAIL_RISK_CONCENTRATED_PROFIT",
        "LOW_PRICE_BINARY_TAIL_STRATEGY",
    )
    assert "TAIL_RISK_DOMINATED" in report.verdicts
    assert "SETTLEMENT_APPROXIMATION_UNCERTAIN" in report.verdicts
    assert "INSUFFICIENT_CLOSED_TRADES" in report.verdicts
    assert "Top 1 trade pct of total PnL:" in text
    assert "PnL excluding top 3 trades:" in text
    assert "Warnings: TAIL_RISK_CONCENTRATED_PROFIT, LOW_PRICE_BINARY_TAIL_STRATEGY" in text


def test_close_mode_compare_and_settlement_report(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    session_id = _seed_session_dataset(db_path, include_end_price=True, later_midpoint=0.70, include_settlement=True)

    assert main([
        "--db",
        str(db_path),
        "close-mode-compare",
        "--strategy",
        "momentum",
        "--source",
        "public",
        "--session-id",
        session_id,
        "--active-only",
        "--tiny",
    ]) == 0
    compare_out = capsys.readouterr().out
    assert "Close mode comparison" in compare_out
    assert "close_mode=approximate-expiry" in compare_out
    assert "top_1_pnl=" in compare_out
    assert "verdicts=" in compare_out

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
        "--close-mode",
        "approximate-expiry",
        "--tiny",
    ]) == 0
    store = SQLiteStore(db_path)
    try:
        run_id = store.latest_run_id()
    finally:
        store.close()
    assert run_id is not None

    assert main(["--db", str(db_path), "settlement-report", "--run-id", run_id]) == 0
    settlement_out = capsys.readouterr().out
    assert "Settlement validation report" in settlement_out
    assert "confidence=" in settlement_out
    assert "market=" in settlement_out
