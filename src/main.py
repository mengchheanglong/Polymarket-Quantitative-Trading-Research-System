from __future__ import annotations

import argparse
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from src.collectors.exchange import CoinbaseCollector, FallbackExchangeCollector, KrakenCollector
from src.collectors.mock_markets import MockMarketSource
from src.collectors.polymarket import PolymarketPublicCollector, _candidate_to_market
from src.config import AgentConfig, load_config
from src.http_client import HttpError
from src.models import Asset, Direction, Market, OpportunityDecision, OrderBook, Signal
from src.reports.backtest import build_backtest_report
from src.reports.active_markets import build_active_market_report, select_market_snapshots
from src.reports.compare import build_strategy_comparison
from src.reports.conservative_report import (
    ConservativeSessionRow,
    aggregate_variant_row,
    build_conservative_report,
    conservative_readiness_verdict,
)
from src.reports.close_divergence import build_close_divergence_report
from src.reports.dataset import build_dataset_summary
from src.reports.diagnostics import build_diagnostics
from src.reports.ledger import build_trade_ledger
from src.reports.momentum_audit import build_momentum_audit_report
from src.reports.config_view import parse_config_notes
from src.reports.side_audit import (
    build_candidate_comparison_report,
    build_candidate_ranking_report,
    build_side_audit_report,
    build_side_sweep_report,
)
from src.reports.settlement import build_settlement_report
from src.reports.signal_audit import (
    build_signal_audit,
    load_signal_audit_rows,
    load_signal_audit_rows_from_records,
    summarize_signal_audit_rows,
)
from src.reports.summary import build_report
from src.reports.sweep import SweepRow, build_sweep_report
from src.safety import SafetyError, enforce_paper_only
from src.simulator.engine import PaperTradingEngine, resolve_binary_value
from src.simulator.lifecycle import classify_market_lifecycle
from src.storage.export import export_csv
from src.storage.sqlite import SQLiteStore
from src.strategies.pair_cost_arbitrage import PairCostArbitrageStrategy
from src.strategies.stuck_state_markov import (
    build_markov_model,
    build_markov_report,
    evaluate_stuck_markov_market,
    latest_observation_time,
    latest_tradeable_observation_time,
)
from src.strategies.updown_momentum import MomentumUpDownStrategy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket BTC/ETH UP-DOWN paper agent")
    parser.add_argument(
        "--db",
        help="SQLite database path. Defaults to DATABASE_PATH or paper_trading.sqlite3.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    collect_parser = subcommands.add_parser("collect", help="Collect public BTC/ETH and market metadata")
    collect_parser.add_argument(
        "--demo",
        action="store_true",
        help="Use deterministic offline mock prices, candles, markets, and orderbooks.",
    )
    run_parser = subcommands.add_parser("run-paper", help="Run one paper-trading research cycle")
    run_parser.add_argument(
        "--demo",
        action="store_true",
        help="Use demo UP/DOWN markets if live public discovery finds none.",
    )
    run_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost", "stuck-markov"),
        default=None,
        help="Paper strategy to simulate.",
    )
    run_parser.add_argument(
        "--preset",
        choices=("balanced-tiny", "conservative-tiny", "conservative-tiny-reverse", "conservative-entry-30-70"),
        default=None,
        help="Apply a paper-only preset.",
    )
    run_parser.add_argument("--reverse-signal", action="store_true", help="Paper-only research mode: flip momentum UP/DOWN entries.")
    run_parser.add_argument("--new-run", action="store_true", help="Start a fresh run. This is the default.")
    report_parser = subcommands.add_parser("report", help="Summarize fake trading results")
    report_parser.add_argument("--latest", action="store_true", help="Report only the latest run.")
    report_parser.add_argument("--all", action="store_true", help="Report all runs combined.")
    report_parser.add_argument("--run-id", help="Report a specific run.")
    report_parser.add_argument("--strategy", choices=("momentum", "pair-cost", "stuck-markov"), help="Report runs for a strategy.")
    trades_parser = subcommands.add_parser("trades", help="Show simulated trades and skipped opportunities")
    trades_parser.add_argument("--run-id", help="Show ledger for a specific run.")
    trades_parser.add_argument("--all", action="store_true", help="Show ledger for all runs.")
    replay_parser = subcommands.add_parser("replay", help="Replay stored snapshots without external APIs")
    replay_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost", "stuck-markov"),
        default=None,
        help="Paper strategy to replay.",
    )
    replay_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    replay_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    replay_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    replay_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    replay_parser.add_argument("--active-only", action="store_true", help="Replay only markets inside a valid active trading window with stored orderbooks.")
    replay_parser.add_argument("--min-seconds-to-expiry", type=int, default=None)
    replay_parser.add_argument("--max-seconds-to-expiry", type=int, default=None)
    replay_parser.add_argument("--tiny", action="store_true", help="Apply the tiny-position paper-risk profile.")
    replay_parser.add_argument(
        "--preset",
        choices=("balanced-tiny", "conservative-tiny", "conservative-tiny-reverse", "conservative-entry-30-70"),
        default=None,
        help="Apply a paper-only preset.",
    )
    replay_parser.add_argument("--reverse-signal", action="store_true", help="Paper-only research mode: flip momentum UP/DOWN entries.")
    replay_parser.add_argument(
        "--momentum-preset",
        choices=("balanced-tiny-momentum", "conservative-tiny-momentum"),
        default=None,
        help="Apply a paper-only momentum research preset.",
    )
    replay_parser.add_argument(
        "--close-mode",
        choices=("none", "mark-to-market", "expiry-if-known", "approximate-expiry"),
        default=None,
        help="Paper-only replay close handling.",
    )
    replay_parser.add_argument("--new-run", action="store_true", help="Start a fresh run. This is the default.")
    backtest_parser = subcommands.add_parser("backtest-report", help="Summarize stored snapshots and replay output")
    backtest_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost", "stuck-markov"),
        default=None,
        help="Strategy label to show in the report.",
    )
    backtest_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    backtest_parser.add_argument("--since", help="Only summarize snapshots at or after this UTC ISO timestamp.")
    backtest_parser.add_argument("--until", help="Only summarize snapshots at or before this UTC ISO timestamp.")
    backtest_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    backtest_parser.add_argument("--active-only", action="store_true", help="Summarize active-only replay runs for the selected scope.")
    backtest_parser.add_argument("--tiny", action="store_true", help="Summarize only tiny-profile replay runs for the selected scope.")
    subcommands.add_parser("runs", help="List experiment runs")
    compare_parser = subcommands.add_parser("compare", help="Compare stored strategies by run metadata")
    compare_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    compare_parser.add_argument("--session-id", help="Filter runs for a research session.")
    compare_parser.add_argument("--active-only", action="store_true", help="Compare only runs created with --active-only.")
    compare_parser.add_argument("--tiny", action="store_true", help="Compare only runs created with the tiny-position profile.")
    diagnostics_parser = subcommands.add_parser("diagnostics", help="Explain accepted/skipped paper opportunities")
    diagnostics_parser.add_argument("--run-id", help="Inspect a specific run.")
    diagnostics_parser.add_argument("--strategy", choices=("momentum", "pair-cost", "stuck-markov"), help="Filter by strategy.")
    diagnostics_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    diagnostics_parser.add_argument("--session-id", help="Filter runs for a research session.")
    diagnostics_parser.add_argument("--active-only", action="store_true", help="Filter to runs created with --active-only and show active-market liquidity diagnostics.")
    diagnostics_parser.add_argument("--tiny", action="store_true", help="Filter to runs created with the tiny-position profile.")
    diagnostics_parser.add_argument("--min-seconds-to-expiry", type=int, default=None)
    diagnostics_parser.add_argument("--max-seconds-to-expiry", type=int, default=None)
    sweep_parser = subcommands.add_parser("sweep", help="Run a paper-only threshold sweep on stored snapshots")
    sweep_parser.add_argument("--strategy", choices=("momentum", "pair-cost"), required=True)
    sweep_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    sweep_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    sweep_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    sweep_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    sweep_parser.add_argument("--active-only", action="store_true", help="Sweep only markets inside a valid active trading window.")
    sweep_parser.add_argument("--min-seconds-to-expiry", type=int, default=None)
    sweep_parser.add_argument("--max-seconds-to-expiry", type=int, default=None)
    reset_parser = subcommands.add_parser("reset", help="Delete research data safely")
    reset_parser.add_argument("--paper-results", action="store_true", help="Delete paper runs, trades, opportunities, and equity only.")
    reset_parser.add_argument("--all", action="store_true", help="Delete paper results and collected snapshot data.")
    observe_parser = subcommands.add_parser("observe", help="Collect public snapshots repeatedly without trading")
    observe_parser.add_argument("--duration-minutes", type=float, default=None, help="Maximum observe duration.")
    observe_parser.add_argument("--interval-seconds", type=float, default=15.0, help="Seconds between cycles.")
    observe_parser.add_argument("--cycles", type=int, default=None, help="Maximum cycles, useful for tests.")
    observe_parser.add_argument(
        "--profile",
        choices=("conservative-momentum", "conservative-entry-30-70"),
        default=None,
        help="Label and optionally narrow a public-data collection profile.",
    )
    subcommands.add_parser("sessions", help="List public-data research sessions")
    session_report_parser = subcommands.add_parser("session-report", help="Summarize a research observation session")
    session_report_parser.add_argument("--session-id", help="Show a specific session.")
    session_report_parser.add_argument("--latest", action="store_true", help="Show the latest session.")
    research_report_parser = subcommands.add_parser("research-report", help="Summarize how to analyze a research session")
    research_report_parser.add_argument("--session-id", help="Analyze a specific session.")
    research_report_parser.add_argument("--latest", action="store_true", help="Analyze the latest session.")
    dataset_parser = subcommands.add_parser("dataset", help="Summarize stored public/demo snapshots")
    dataset_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    dataset_parser.add_argument("--since", help="Only summarize snapshots at or after this UTC ISO timestamp.")
    dataset_parser.add_argument("--until", help="Only summarize snapshots at or before this UTC ISO timestamp.")
    dataset_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    readiness_parser = subcommands.add_parser("readiness", help="Check whether stored data is replay-ready")
    readiness_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    readiness_parser.add_argument("--since", help="Only inspect snapshots at or after this UTC ISO timestamp.")
    readiness_parser.add_argument("--until", help="Only inspect snapshots at or before this UTC ISO timestamp.")
    readiness_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    markets_parser = subcommands.add_parser("markets", help="Audit discovered Polymarket markets")
    markets_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    markets_parser.add_argument("--since", help="Only inspect markets collected at or after this UTC ISO timestamp.")
    markets_parser.add_argument("--until", help="Only inspect markets collected at or before this UTC ISO timestamp.")
    markets_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    active_markets_parser = subcommands.add_parser("active-markets", help="Summarize active market windows and liquidity")
    active_markets_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    active_markets_parser.add_argument("--since", help="Only inspect markets collected at or after this UTC ISO timestamp.")
    active_markets_parser.add_argument("--until", help="Only inspect markets collected at or before this UTC ISO timestamp.")
    active_markets_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    active_markets_parser.add_argument("--min-seconds-to-expiry", type=int, default=None)
    active_markets_parser.add_argument("--max-seconds-to-expiry", type=int, default=None)
    discover_parser = subcommands.add_parser("discover-markets", help="Probe public Polymarket market discovery")
    discover_parser.add_argument("--asset", choices=("BTC", "ETH", "all"), default="all")
    markov_parser = subcommands.add_parser("markov-report", help="Summarize stuck-state transition probabilities from stored snapshots")
    markov_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    markov_parser.add_argument("--since", help="Only inspect snapshots at or after this UTC ISO timestamp.")
    markov_parser.add_argument("--until", help="Only inspect snapshots at or before this UTC ISO timestamp.")
    markov_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    export_parser = subcommands.add_parser("export", help="Export local research data")
    export_parser.add_argument("--format", choices=("csv",), default="csv")
    export_parser.add_argument("--out", default="exports")
    export_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    export_parser.add_argument("--since", help="Only export raw snapshots at or after this UTC ISO timestamp.")
    export_parser.add_argument("--until", help="Only export raw snapshots at or before this UTC ISO timestamp.")
    export_parser.add_argument("--session-id", help="Export only data tied to a research session when possible.")
    export_parser.add_argument("--validation", choices=("conservative",), default=None, help="Export a paper-validation package.")
    export_parser.add_argument(
        "--candidate",
        choices=("conservative-entry-30-70",),
        default=None,
        help="Export the validation package for one named paper candidate.",
    )
    close_mode_compare_parser = subcommands.add_parser("close-mode-compare", help="Compare replay close modes on stored snapshots")
    close_mode_compare_parser.add_argument("--strategy", choices=("momentum", "pair-cost", "stuck-markov"), required=True)
    close_mode_compare_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    close_mode_compare_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    close_mode_compare_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    close_mode_compare_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    close_mode_compare_parser.add_argument("--active-only", action="store_true")
    close_mode_compare_parser.add_argument("--tiny", action="store_true")
    close_mode_compare_parser.add_argument("--min-seconds-to-expiry", type=int, default=None)
    close_mode_compare_parser.add_argument("--max-seconds-to-expiry", type=int, default=None)
    settlement_report_parser = subcommands.add_parser("settlement-report", help="Inspect approximate-expiry settlement inputs for a run")
    settlement_report_parser.add_argument("--run-id", help="Inspect a specific run.")
    settlement_report_parser.add_argument("--latest", action="store_true", help="Inspect the latest run.")
    signal_audit_parser = subcommands.add_parser("signal-audit", help="Audit accepted momentum signals for a run")
    signal_audit_parser.add_argument("--run-id", help="Inspect a specific run.")
    signal_audit_parser.add_argument("--latest", action="store_true", help="Inspect the latest run.")
    side_audit_parser = subcommands.add_parser("side-audit", help="Audit conservative momentum side correctness across stored sessions")
    side_audit_parser.add_argument("--source", choices=("public", "all"), default="public")
    side_audit_parser.add_argument("--session-id", help="Limit the audit to one research session.")
    side_audit_parser.add_argument("--run-id", help="Inspect a specific stored run instead of the conservative aggregate.")
    side_audit_parser.add_argument("--details", action="store_true", help="Print accepted-trade feature rows.")
    close_divergence_parser = subcommands.add_parser("close-divergence", help="Compare mark-to-market and approximate-expiry outcomes")
    close_divergence_parser.add_argument("--strategy", choices=("momentum",), default="momentum")
    close_divergence_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    close_divergence_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    close_divergence_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    close_divergence_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    close_divergence_parser.add_argument("--active-only", action="store_true")
    close_divergence_parser.add_argument("--tiny", action="store_true")
    close_divergence_parser.add_argument(
        "--momentum-preset",
        choices=("balanced-tiny-momentum", "conservative-tiny-momentum"),
        default=None,
    )
    momentum_audit_parser = subcommands.add_parser("momentum-audit", help="Run paper-only momentum filter and timing experiments")
    momentum_audit_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    momentum_audit_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    momentum_audit_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    momentum_audit_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    momentum_audit_parser.add_argument("--tiny", action="store_true")
    side_sweep_parser = subcommands.add_parser("side-sweep", help="Compare conservative momentum side-selection variants")
    side_sweep_parser.add_argument("--source", choices=("public", "all"), default="public")
    side_sweep_parser.add_argument("--session-id", help="Limit the sweep to one research session.")
    candidate_ranking_parser = subcommands.add_parser("candidate-ranking", help="Rank conservative momentum paper variants")
    candidate_ranking_parser.add_argument("--source", choices=("public", "all"), default="public")
    candidate_ranking_parser.add_argument("--session-id", help="Limit the ranking to one research session.")
    compare_candidates_parser = subcommands.add_parser("compare-candidates", help="Compare the main conservative paper candidates")
    compare_candidates_parser.add_argument("--source", choices=("public", "all"), default="public")
    compare_candidates_parser.add_argument("--session-id", help="Limit the comparison to one research session.")
    conservative_report_parser = subcommands.add_parser("conservative-report", help="Validate the conservative tiny momentum preset across stored sessions")
    conservative_report_parser.add_argument("--source", choices=("public", "all"), default="public")
    conservative_report_parser.add_argument(
        "--preset",
        choices=("conservative-tiny", "conservative-entry-30-70"),
        default="conservative-entry-30-70",
        help="Named paper-only momentum preset to aggregate.",
    )
    preset_report_parser = subcommands.add_parser("preset-report", help="Summarize one named conservative momentum preset across stored sessions")
    preset_report_parser.add_argument("--source", choices=("public", "all"), default="public")
    preset_report_parser.add_argument(
        "--preset",
        choices=("conservative-tiny", "conservative-entry-30-70"),
        required=True,
        help="Named paper-only momentum preset to aggregate.",
    )
    validate_conservative_parser = subcommands.add_parser("validate-conservative", help="Create or reuse conservative momentum runs across replay-ready public sessions")
    validate_conservative_parser.add_argument("--source", choices=("public", "all"), default="public")
    validate_conservative_parser.add_argument(
        "--preset",
        choices=("conservative-tiny", "conservative-entry-30-70"),
        default="conservative-entry-30-70",
        help="Named paper-only momentum preset to validate.",
    )
    validate_conservative_parser.add_argument("--rerun", action="store_true", help="Re-run matching conservative sessions even if stored runs already exist.")
    validate_candidate_parser = subcommands.add_parser("validate-candidate", help="Alias for validate-conservative using --candidate")
    validate_candidate_parser.add_argument("--source", choices=("public", "all"), default="public")
    validate_candidate_parser.add_argument(
        "--candidate",
        choices=("conservative-entry-30-70",),
        required=True,
        help="Named paper-only candidate preset to validate.",
    )
    validate_candidate_parser.add_argument("--rerun", action="store_true", help="Re-run matching candidate sessions even if stored runs already exist.")
    candidate_report_parser = subcommands.add_parser("candidate-report", help="Alias for preset-report using --candidate")
    candidate_report_parser.add_argument("--source", choices=("public", "all"), default="public")
    candidate_report_parser.add_argument(
        "--candidate",
        choices=("conservative-entry-30-70",),
        required=True,
        help="Named paper-only candidate preset to summarize.",
    )
    args = parser.parse_args(argv)

    config = load_config()
    if args.db:
        config = _replace_database_path(config, args.db)
    if getattr(args, "demo", False):
        config = _replace_demo_flag(config, True)
    if getattr(args, "strategy", None):
        config = _replace_strategy(config, args.strategy)
    if getattr(args, "close_mode", None):
        config = _replace_close_mode(config, args.close_mode)
    if getattr(args, "tiny", False) and args.command in {"replay", "run-paper", "close-mode-compare", "close-divergence", "momentum-audit"}:
        config = _replace_tiny_profile(config)
    if getattr(args, "preset", None):
        config = _apply_named_preset(config, args.preset)
    if getattr(args, "momentum_preset", None):
        config = _apply_momentum_preset(config, args.momentum_preset)
    if getattr(args, "reverse_signal", False):
        config = _replace_config_values(config, reverse_signal=True)
    if getattr(args, "profile", None):
        config = _replace_config_values(config, observe_profile=args.profile)

    try:
        enforce_paper_only(config.dry_run, config.execution_mode)
        if args.command == "collect":
            return collect(config)
        if args.command == "run-paper":
            return run_paper(config)
        if args.command == "report":
            return report(config, args)
        if args.command == "trades":
            return trades(config, args)
        if args.command == "replay":
            return replay(config, args)
        if args.command == "backtest-report":
            return backtest_report(config, args)
        if args.command == "runs":
            return runs(config)
        if args.command == "compare":
            return compare(config, args)
        if args.command == "diagnostics":
            return diagnostics(config, args)
        if args.command == "sweep":
            return sweep(config, args)
        if args.command == "reset":
            return reset(config, args)
        if args.command == "observe":
            return observe(config, args)
        if args.command == "sessions":
            return sessions(config)
        if args.command == "session-report":
            return session_report(config, args)
        if args.command == "research-report":
            return research_report(config, args)
        if args.command == "dataset":
            return dataset(config, args)
        if args.command == "readiness":
            return readiness(config, args)
        if args.command == "markets":
            return market_audit(config, args)
        if args.command == "active-markets":
            return active_markets(config, args)
        if args.command == "discover-markets":
            return discover_markets(config, args)
        if args.command == "markov-report":
            return markov_report(config, args)
        if args.command == "export":
            return export_data(config, args)
        if args.command == "close-mode-compare":
            return close_mode_compare(config, args)
        if args.command == "settlement-report":
            return settlement_report(config, args)
        if args.command == "signal-audit":
            return signal_audit(config, args)
        if args.command == "side-audit":
            return side_audit(config, args)
        if args.command == "close-divergence":
            return close_divergence(config, args)
        if args.command == "momentum-audit":
            return momentum_audit(config, args)
        if args.command == "side-sweep":
            return side_sweep(config, args)
        if args.command == "candidate-ranking":
            return candidate_ranking(config, args)
        if args.command == "compare-candidates":
            return compare_candidates(config, args)
        if args.command == "conservative-report":
            return conservative_report(config, args)
        if args.command == "preset-report":
            return preset_report(config, args)
        if args.command == "validate-conservative":
            return validate_conservative(config, args)
        if args.command == "validate-candidate":
            return validate_candidate(config, args)
        if args.command == "candidate-report":
            return candidate_report(config, args)
    except SafetyError as exc:
        print(f"Safety error: {exc}", file=sys.stderr)
        return 2
    except HttpError as exc:
        print(f"Public data error: {exc}", file=sys.stderr)
        if args.command in {"collect", "run-paper"}:
            print("Try demo mode: python -m src.main collect --demo", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 1
    return 0


def collect(config: AgentConfig, session_id: str | None = None) -> int:
    now = datetime.now(timezone.utc)
    store = SQLiteStore(config.database_path)
    try:
        if config.use_demo_markets:
            demo = MockMarketSource()
            prices = demo.collect_prices(now=now)
            markets = demo.markets(now=now)
            orderbooks = {
                token_id: demo.orderbook(token_id)
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
            }
            for snapshot in prices:
                store.log_price(snapshot, session_id=session_id)
                store.log_raw_snapshot(
                    now,
                    snapshot.source,
                    snapshot.asset.value,
                    "exchange_price",
                    {"price": snapshot.price, "source": snapshot.source},
                    status="ok",
                    session_id=session_id,
                )
                store.log_candles(
                    asset=snapshot.asset.value,
                    candles=demo.recent_candles(snapshot.asset, now=now),
                    source=snapshot.source,
                    observed_at=now,
                    session_id=session_id,
                )
            store.replace_collected_market_data(now, markets, orderbooks, source_name="mock:demo", session_id=session_id)
            _log_market_raw_snapshots(store, now, session_id, "mock:demo", markets, orderbooks, "ok")
            for settlement in demo.settlement_prices(now=now):
                store.log_raw_snapshot(
                    settlement.timestamp,
                    settlement.source,
                    settlement.asset.value,
                    "settlement_price",
                    {"price": settlement.price, "source": settlement.source},
                    status="ok",
                    session_id=session_id,
                )
            store.set_state("last_collection_mode", "demo", now)
            print("Collected deterministic demo dataset.")
            print(f"Inserted mock prices: {len(prices)}")
            print(f"Inserted mock markets: {len(markets)}")
            print(f"Inserted mock orderbooks: {len(orderbooks)}")
            return 0

        profile = _observe_profile_settings(config.observe_profile)
        exchange = FallbackExchangeCollector(
            [
                CoinbaseCollector(config.coinbase_base_url),
                KrakenCollector(config.kraken_base_url),
            ]
        )
        try:
            prices = _collect_public_prices(exchange, profile["assets"])
        except Exception as exc:
            store.log_raw_snapshot(
                now,
                "public-exchange",
                None,
                "collection_status",
                {},
                status="failed",
                error_message=str(exc),
                session_id=session_id,
            )
            raise HttpError(f"public exchange collection failed: {exc}") from exc
        for snapshot in prices:
            store.log_price(snapshot, session_id=session_id)
            store.log_raw_snapshot(
                now,
                snapshot.source,
                snapshot.asset.value,
                "exchange_price",
                {"price": snapshot.price, "source": snapshot.source},
                status="ok",
                session_id=session_id,
            )
            try:
                candles = exchange.recent_candles(snapshot.asset, granularity=60)
            except Exception as exc:
                store.log_raw_snapshot(
                    now,
                    snapshot.source,
                    snapshot.asset.value,
                    "exchange_candles",
                    {},
                    status="failed",
                    error_message=str(exc),
                    session_id=session_id,
                )
                raise HttpError(f"public exchange candle collection failed: {exc}") from exc
            store.log_candles(
                asset=snapshot.asset.value,
                candles=candles,
                source=snapshot.source,
                observed_at=now,
                session_id=session_id,
            )
            print(f"{snapshot.asset.value} {snapshot.price:.2f} from {snapshot.source}")

        polymarket = PolymarketPublicCollector(config.gamma_base_url, config.clob_base_url)
        try:
            candidates = polymarket.discover_market_candidates(
                asset_filter=profile["asset_filter"],
                max_duration_minutes=profile["max_market_duration_minutes"],
            )
            candidates, orderbooks = polymarket.capture_orderbooks(candidates)
        except Exception as exc:
            store.log_raw_snapshot(
                now,
                "polymarket-public",
                None,
                "collection_status",
                {},
                status="failed",
                error_message=str(exc),
                session_id=session_id,
            )
            raise HttpError(f"public Polymarket collection failed: {exc}") from exc
        store.log_discovered_markets(now, "polymarket-public", candidates, session_id=session_id)
        accepted_markets = [
            market
            for candidate in candidates
            for market in [_candidate_to_market(candidate)]
            if market is not None
        ]
        complete_markets = [
            market
            for candidate in candidates
            if candidate.orderbook_status == "FOUND"
            for market in [_candidate_to_market(candidate)]
            if market is not None
        ]
        complete_orderbooks = {
            token_id: orderbooks[token_id]
            for market in complete_markets
            for token_id in (market.up_token_id, market.down_token_id)
            if token_id in orderbooks
        }
        _log_discovery_raw_snapshots(store, now, session_id, "polymarket-public", candidates, complete_orderbooks)
        store.set_state("last_collection_mode", "public", now)
        if not accepted_markets:
            print("No active short-duration BTC/ETH UP-DOWN markets discovered from public endpoints.")
            print("Use collect --demo or USE_MOCK_DATA=true for offline demo data.")
            store.replace_collected_market_data(now, [], {}, source_name="polymarket-public", session_id=session_id)
        else:
            store.replace_collected_market_data(
                now,
                complete_markets,
                complete_orderbooks,
                source_name="polymarket-public",
                session_id=session_id,
            )
            print(f"Discovered {len(accepted_markets)} directional public Polymarket markets.")
            print(f"Stored complete orderbooks for {len(complete_markets)} markets.")
            for market in complete_markets[:10]:
                print(f"{market.asset.value} {market.slug} ends {market.window.end.isoformat()}")
    finally:
        store.close()
    return 0


def observe(config: AgentConfig, args) -> int:
    successes = 0
    failures = 0
    store = SQLiteStore(config.database_path)
    started_at = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    requested_cycles = args.cycles
    requested_duration_seconds = (args.duration_minutes * 60.0) if args.duration_minutes is not None else None
    if requested_cycles is None and requested_duration_seconds is None:
        requested_cycles = 1
    deadline_mono = (started_mono + requested_duration_seconds) if requested_duration_seconds is not None else None
    session_id = store.start_research_session(
        started_at,
        interval_seconds=args.interval_seconds,
        cycles_requested=requested_cycles,
        notes=(
            f"duration_minutes={args.duration_minutes or 'none'}; interval_seconds={args.interval_seconds}; "
            f"profile={getattr(args, 'profile', None) or 'default'}"
        ),
    )
    print(f"Observe session started: {session_id}")
    interrupted = False
    cycle_durations: list[float] = []
    effective_intervals: list[float] = []
    previous_cycle_start_mono: float | None = None
    cycle_index = 0
    summary = {"total_snapshots": "n/a", "failed_snapshots": "n/a"}
    found: int | str = "n/a"
    orderbooks: int | str = "n/a"
    summary_error: str | None = None
    try:
        while True:
            now_mono = time.monotonic()
            if requested_cycles is not None and cycle_index >= requested_cycles:
                break
            if cycle_index > 0 and deadline_mono is not None and now_mono >= deadline_mono:
                break
            if previous_cycle_start_mono is not None and args.interval_seconds > 0:
                target_start = previous_cycle_start_mono + args.interval_seconds
                if now_mono < target_start:
                    time.sleep(target_start - now_mono)
                    now_mono = time.monotonic()
            cycle_started_mono = time.monotonic()
            cycle_started_wall = datetime.now(timezone.utc)
            cycle_label = f"{cycle_index + 1}" if requested_cycles is None else f"{cycle_index + 1}/{requested_cycles}"
            print(f"Observe cycle {cycle_label}")
            try:
                collect(config, session_id=session_id)
                successes += 1
                snapshot_count = len(store.raw_snapshot_rows(session_id=session_id))
                cycle_duration = time.monotonic() - cycle_started_mono
                cycle_durations.append(cycle_duration)
                effective_interval = (
                    cycle_started_mono - previous_cycle_start_mono
                    if previous_cycle_start_mono is not None
                    else 0.0
                )
                if previous_cycle_start_mono is not None:
                    effective_intervals.append(effective_interval)
                print(
                    f"Observe cycle {cycle_index + 1} complete. Session snapshots: {snapshot_count}; "
                    f"failures: {failures}; cycle_duration={cycle_duration:.2f}s; "
                    f"effective_interval={effective_interval:.2f}s."
                )
            except HttpError as exc:
                failures += 1
                cycle_duration = time.monotonic() - cycle_started_mono
                cycle_durations.append(cycle_duration)
                effective_interval = (
                    cycle_started_mono - previous_cycle_start_mono
                    if previous_cycle_start_mono is not None
                    else 0.0
                )
                if previous_cycle_start_mono is not None:
                    effective_intervals.append(effective_interval)
                print(f"Observe cycle {cycle_index + 1} failed: {exc}", file=sys.stderr)
                print("Continuing observe loop. Use collect --demo for offline data.", file=sys.stderr)
                print(
                    f"Observe cycle {cycle_index + 1} failed after {cycle_duration:.2f}s; "
                    f"effective_interval={effective_interval:.2f}s.",
                    file=sys.stderr,
                )
            store.update_research_session_progress(
                session_id,
                cycles_completed=cycle_index + 1,
                successful_cycles=successes,
                failed_cycles=failures,
            )
            previous_cycle_start_mono = cycle_started_mono
            cycle_index += 1
    except KeyboardInterrupt:
        interrupted = True
        print("\nObserve interrupted. Finalizing partial session...")
    finally:
        ended_at = datetime.now(timezone.utc)
        store.finish_research_session(session_id, ended_at)
        session = store.research_session_by_id(session_id)
        summary_error = None
        try:
            summary = store.dataset_summary(source_filter="public", session_id=session_id)
            markets = store.market_audit_rows(source_filter="public", session_id=session_id)
            found = sum(1 for row in markets if row["accepted"])
            orderbooks = sum(1 for row in markets if row["orderbook_status"] == "FOUND")
        except Exception as exc:
            summary = {"total_snapshots": "n/a", "failed_snapshots": "n/a"}
            found = "n/a"
            orderbooks = "n/a"
            summary_error = str(exc)
        store.close()

    print(f"Observe complete. Successful cycles: {successes}; failed cycles: {failures}.")
    if session is not None:
        print(
            f"Session summary: session_id={session_id} | cycles_completed={session['cycles_completed']} | "
            f"snapshots={session['snapshot_count']} | failed_snapshots={session['failed_snapshot_count']}"
        )
    actual_duration = time.monotonic() - started_mono
    avg_cycle = (sum(cycle_durations) / len(cycle_durations)) if cycle_durations else 0.0
    min_cycle = min(cycle_durations, default=0.0)
    max_cycle = max(cycle_durations, default=0.0)
    cycles_per_hour = ((cycle_index / actual_duration) * 3600.0) if actual_duration > 0 else 0.0
    print(
        "Observe performance: "
        f"requested_duration_seconds={requested_duration_seconds if requested_duration_seconds is not None else 'none'}; "
        f"actual_duration_seconds={actual_duration:.2f}; cycles_completed={cycle_index}; "
        f"avg_cycle_duration_seconds={avg_cycle:.2f}; min_cycle_duration_seconds={min_cycle:.2f}; "
        f"max_cycle_duration_seconds={max_cycle:.2f}; effective_cycles_per_hour={cycles_per_hour:.2f}; "
        f"successful_cycles={successes}; failed_cycles={failures}; "
        f"snapshots_collected={summary['total_snapshots']}; failed_snapshots={summary['failed_snapshots']}; "
        f"markets_found={found}; orderbooks_captured={orderbooks}"
    )
    if summary_error is not None:
        print(
            f"Observe summary warning: session_id={session_id}; summary calculation failed after finalization: {summary_error}"
        )
    if interrupted:
        print(f"Analyze the partial session with: python -m src.main session-report --session-id {session_id}")
        return 0
    print(f"Analyze this session with: python -m src.main session-report --session-id {session_id}")
    return 0


def sessions(config: AgentConfig) -> int:
    store = SQLiteStore(config.database_path)
    try:
        rows = store.research_session_rows()
        print("Research sessions")
        if not rows:
            print("No sessions found.")
            return 0
        for row in rows:
            print(
                " | ".join(
                    [
                        str(row["session_id"]),
                        f"started={row['started_at']}",
                        f"ended={row['ended_at'] or 'OPEN'}",
                        f"duration_seconds={row['duration_seconds'] or 0}",
                        f"cycles={row['cycles_completed']}/{row['cycles_requested'] or row['cycles_completed']}",
                        f"successful={row['successful_cycles']}",
                        f"failed={row['failed_cycles']}",
                        f"snapshots={row['snapshot_count']}",
                        f"failed_snapshots={row['failed_snapshot_count']}",
                    ]
                )
            )
    finally:
        store.close()
    return 0


def session_report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session = _session_row(store, args)
        if session is None:
            print("No matching session found.")
            return 1
        session_id, since, until = _session_bounds(store, str(session["session_id"]))
        try:
            summary = store.dataset_summary(source_filter="public", since=since, until=until, session_id=session_id)
        except Exception as exc:
            print("Research session report")
            print(f"Session ID: {session_id}")
            print(f"Started: {session['started_at']}")
            print(f"Ended: {session['ended_at'] or 'OPEN'}")
            print("Summary error: " + str(exc))
            print("Stored session metadata was preserved. Retry after fixing the dataset summary path.")
            return 1
        quality = store.data_quality_metrics(source_filter="public", since=since, until=until, session_id=session_id)
        readiness_result = store.readiness(source_filter="public", since=since, until=until, session_id=session_id)
        markets = store.market_audit_rows(source_filter="public", since=since, until=until, session_id=session_id)
        found = sum(1 for row in markets if row["accepted"])
        orderbooks = sum(1 for row in markets if row["orderbook_status"] == "FOUND")
        assets = summary["assets_seen"]
        session_notes = parse_config_notes(str(session["notes"] or ""))
        print("Research session report")
        print(f"Session ID: {session_id}")
        print(f"Started: {session['started_at']}")
        print(f"Ended: {session['ended_at'] or 'OPEN'}")
        print(f"Duration seconds: {session['duration_seconds'] or 0}")
        print(f"Interval seconds: {session['interval_seconds']}")
        print(f"Observe profile: {session_notes.get('profile', 'default')}")
        print(f"Requested duration minutes: {session_notes.get('duration_minutes', 'none')}")
        print(f"Cycles completed: {session['cycles_completed']}")
        print(f"Successful cycles: {session['successful_cycles']}")
        print(f"Failed cycles: {session['failed_cycles']}")
        print(f"Assets observed: {_format_sources(assets)}")
        print(f"Snapshot count: {summary['total_snapshots']}")
        print(f"Failed snapshot count: {summary['failed_snapshots']}")
        print(f"Public sources used: {_format_sources(summary['source_coverage'].keys())}")
        print(f"BTC/ETH markets found: {found}")
        print(f"Orderbooks captured: {orderbooks}")
        print(f"Readiness verdict: {readiness_result['verdict']}")
        print(
            "Data quality: "
            f"failed={quality['failed_collection_attempts']}, "
            f"total_stale={quality['total_stale_snapshots']}, "
            f"stale_exchange_prices={quality['stale_exchange_prices']}, "
            f"stale_orderbooks={quality['stale_orderbooks']}, "
            f"expired_markets_seen={quality['expired_markets_seen']}, "
            f"invalid_timestamps={quality['invalid_timestamps']}, "
            f"missing_orderbooks={quality['missing_orderbooks']}, "
            f"missing_prices={quality['missing_prices']}"
        )
        print(f"Market discovery summary: accepted={found}, rejected={len(markets) - found}")
        print(f"Recommended next command: {_recommended_next_command(readiness_result['verdict'], session_id)}")
    finally:
        store.close()
    return 0


def research_report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session = _session_row(store, args)
        if session is None:
            print("No matching session found.")
            return 1
        session_id, since, until = _session_bounds(store, str(session["session_id"]))
        readiness_result = store.readiness(source_filter="public", since=since, until=until, session_id=session_id)
        run_rows = store.rows(
            """
            SELECT run_id, strategy, mode, realized_pnl, accepted_trade_count, skipped_opportunity_count
            FROM runs
            WHERE session_id = ?
            ORDER BY started_at, rowid
            """,
            (session_id,),
        )
        print("Research report")
        print(f"Session ID: {session_id}")
        print(f"Readiness verdict: {readiness_result['verdict']}")
        if not run_rows:
            print("No stored replay runs for this session yet.")
            print(f"Next: python -m src.main replay --strategy momentum --source public --session-id {session_id}")
            print(f"Next: python -m src.main replay --strategy pair-cost --source public --session-id {session_id}")
            print(f"Next: python -m src.main diagnostics --source public --session-id {session_id}")
            print(f"Next: python -m src.main sweep --strategy momentum --source public --session-id {session_id}")
            print(f"Next: python -m src.main sweep --strategy pair-cost --source public --session-id {session_id}")
            print(f"Next: python -m src.main backtest-report --source public --session-id {session_id}")
            return 0
        print("Stored session runs:")
        for row in run_rows:
            print(
                " | ".join(
                    [
                        str(row["run_id"]),
                        f"strategy={row['strategy']}",
                        f"mode={row['mode']}",
                        f"realized_pnl={_fmt_money(row['realized_pnl'])}",
                        f"accepted={row['accepted_trade_count']}",
                        f"skipped={row['skipped_opportunity_count']}",
                    ]
                )
            )
        print(
            build_backtest_report(
                store,
                config.starting_balance,
                store.get_state("last_replay_strategy") or config.strategy,
                source_filter="public",
                since=since,
                until=until,
                session_id=session_id,
            ).as_text()
        )
        print(build_strategy_comparison(store, source_filter="public", session_id=session_id))
        print(build_diagnostics(store, config, strategy=None, source_filter="public", session_id=session_id, since=since, until=until))
    finally:
        store.close()
    return 0


def run_paper(config: AgentConfig) -> int:
    now = datetime.now(timezone.utc)
    store = SQLiteStore(config.database_path)
    try:
        mode = _effective_data_mode(config, store)
        run_id = store.start_run(
            strategy=config.strategy,
            mode="paper",
            data_source=mode,
            starting_balance=config.starting_balance,
            now=now,
            notes=_config_notes(config),
        )
        engine = PaperTradingEngine(config, store, run_id=run_id)
        current_prices, candle_source, markets, orderbook_source, settlement_source = _load_run_context(
            config,
            store,
            mode,
            now,
        )
        closed = engine.close_expired(current_prices, now=now)
        if not markets:
            print("No tradeable markets found. Recorded prices only.")
            print(f"Closed expired fake positions: {closed}")
            store.finish_run(run_id, now)
            return 0

        engine.record_equity(now)
        if config.strategy == "pair-cost":
            accepted, skipped = _run_pair_cost(config, engine, markets, orderbook_source, current_prices, now)
        else:
            accepted, skipped = _run_momentum(
                config,
                engine,
                markets,
                orderbook_source,
                candle_source,
                current_prices,
                now,
            )

        if settlement_source is not None:
            settlement_prices = {
                snapshot.asset.value: snapshot for snapshot in settlement_source.settlement_prices(now=now)
            }
            settlement_now = max(market.window.end for market in markets)
            closed += engine.close_expired(settlement_prices, now=settlement_now)
            now = settlement_now

        store.finish_run(run_id, now)
        print(f"Paper cycle complete. Accepted fake trades: {accepted}; skipped: {skipped}; closed: {closed}.")
        print(f"Run ID: {run_id}")
        print(f"Fake balance: ${store.current_balance(default=config.starting_balance, run_id=run_id):.2f}")
    finally:
        store.close()
    return 0


def report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        print(
            build_report(
                store,
                config.starting_balance,
                run_id=getattr(args, "run_id", None),
                strategy=getattr(args, "strategy", None),
                all_runs=bool(getattr(args, "all", False)),
            ).as_text()
        )
    finally:
        store.close()
    return 0


def trades(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        run_id = None if getattr(args, "all", False) else (getattr(args, "run_id", None) or store.latest_run_id())
        print(build_trade_ledger(store, run_id=run_id))
    finally:
        store.close()
    return 0


def replay(config: AgentConfig, args) -> int:
    now = datetime.now(timezone.utc)
    store = SQLiteStore(config.database_path)
    try:
        source_filter = _clean_source_filter(getattr(args, "source", None))
        session_id, since, until = _resolved_time_filters(store, args)
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        outcome = _simulate_replay(
            config,
            source_filter=source_filter,
            since=since,
            until=until,
            session_id=session_id,
            active_only=bool(getattr(args, "active_only", False)),
            min_seconds_to_expiry=min_seconds_to_expiry,
            max_seconds_to_expiry=max_seconds_to_expiry,
            now=now,
            data_store=store,
            result_store=store,
            mode="replay",
            since_label=getattr(args, "since", None),
        )
        print(f"Replay source filter: {source_filter or 'all'}")
        print(f"Replay stored sources: {_format_sources(outcome['actual_sources'])}")
        if not outcome["ok"]:
            print(outcome["message"])
            return 1
        store.set_state("last_replay_strategy", config.strategy, outcome["finished_at"])
        print(
            f"Replay complete. Strategy: {config.strategy}; accepted fake trades: {outcome['accepted']}; "
            f"skipped: {outcome['skipped']}; closed: {outcome['closed']}."
        )
        print(f"Close mode: {config.close_mode}")
        print(f"Run ID: {outcome['run_id']}")
    finally:
        store.close()
    return 0


def backtest_report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        strategy = store.get_state("last_replay_strategy") or config.strategy
        print(
            build_backtest_report(
                store,
                config.starting_balance,
                strategy,
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                since=since,
                until=until,
                session_id=session_id,
                active_only=bool(getattr(args, "active_only", False)),
                tiny_only=bool(getattr(args, "tiny", False)),
            ).as_text()
        )
    finally:
        store.close()
    return 0


def runs(config: AgentConfig) -> int:
    store = SQLiteStore(config.database_path)
    try:
        rows = store.run_rows()
        print("Runs")
        if not rows:
            print("No runs found.")
            return 0
        for row in rows:
            print(
                " | ".join(
                    [
                        str(row["run_id"]),
                        f"strategy={row['strategy']}",
                        f"mode={row['mode']}",
                        f"data_source={row['data_source']}",
                        f"started={row['started_at']}",
                        f"ending_balance={_fmt_money(row['ending_balance'])}",
                        f"realized_pnl={_fmt_money(row['realized_pnl'])}",
                        f"accepted={row['accepted_trade_count']}",
                        f"skipped={row['skipped_opportunity_count']}",
                    ]
                )
            )
    finally:
        store.close()
    return 0


def compare(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        print(
            build_strategy_comparison(
                store,
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                session_id=getattr(args, "session_id", None),
                active_only=bool(getattr(args, "active_only", False)),
                tiny_only=bool(getattr(args, "tiny", False)),
            )
        )
    finally:
        store.close()
    return 0


def diagnostics(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        print(
            build_diagnostics(
                store,
                config,
                run_id=getattr(args, "run_id", None),
                strategy=getattr(args, "strategy", None),
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                session_id=session_id,
                since=since,
                until=until,
                active_only=bool(getattr(args, "active_only", False)),
                tiny_only=bool(getattr(args, "tiny", False)),
                min_seconds_to_expiry=min_seconds_to_expiry,
                max_seconds_to_expiry=max_seconds_to_expiry,
            )
        )
    finally:
        store.close()
    return 0


def sweep(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None))
    strategy = getattr(args, "strategy")
    data_store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(data_store, args)
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        rows: list[SweepRow] = []
        for override in _sweep_configs(config, strategy):
            with tempfile.TemporaryDirectory(prefix="paper-sweep-") as temp_dir:
                scratch = SQLiteStore(Path(temp_dir) / "sweep.sqlite3")
                try:
                    outcome = _simulate_replay(
                        override,
                        source_filter=source_filter,
                        since=since,
                        until=until,
                        session_id=session_id,
                        active_only=bool(getattr(args, "active_only", False)),
                        min_seconds_to_expiry=min_seconds_to_expiry,
                        max_seconds_to_expiry=max_seconds_to_expiry,
                        now=datetime.now(timezone.utc),
                        data_store=data_store,
                        result_store=scratch,
                        mode="sweep",
                        since_label=getattr(args, "since", None),
                    )
                    if not outcome["ok"]:
                        print(outcome["message"])
                        return 1
                    report = build_report(scratch, override.starting_balance, run_id=outcome["run_id"])
                    rows.append(
                        SweepRow(
                            label=_sweep_label(override),
                            accepted_trades=outcome["accepted"],
                            skipped_opportunities=outcome["skipped"],
                            realized_pnl=report.realized_pnl,
                            win_rate=report.win_rate,
                            max_equity_drawdown=report.max_equity_drawdown,
                            max_position_exposure=report.max_position_exposure,
                            average_edge=report.average_edge,
                        )
                    )
                finally:
                    scratch.close()
        print(build_sweep_report(strategy, source_filter, rows))
    finally:
        data_store.close()
    return 0


def reset(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        if getattr(args, "all", False):
            store.reset_all()
            print("Reset all research data, including raw snapshots.")
            return 0
        if getattr(args, "paper_results", False):
            store.reset_paper_results()
            print("Reset paper results. Raw snapshots were preserved.")
            return 0
        print("Choose --paper-results or --all.")
        return 1
    finally:
        store.close()


def dataset(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        print(
            build_dataset_summary(
                store,
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                since=since,
                until=until,
                session_id=session_id,
            )
        )
    finally:
        store.close()
    return 0


def export_data(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        candidate = getattr(args, "candidate", None)
        validation = getattr(args, "validation", None)
        if candidate is not None and validation is None:
            validation = "conservative"
        paths = export_csv(
            store,
            args.out,
            source_filter=_clean_source_filter(getattr(args, "source", None)),
            since=since,
            until=until,
            session_id=session_id,
            validation=validation,
            candidate=candidate,
        )
        print("Export complete.")
        for path in paths:
            print(str(path))
    finally:
        store.close()
    return 0


def markov_report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        print(
            build_markov_report(
                store,
                config,
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                since=since,
                until=until,
                session_id=session_id,
            )
        )
    finally:
        store.close()
    return 0


def close_mode_compare(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None))
    data_store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(data_store, args)
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        lines = [
            "Close mode comparison",
            "Research only: stored-snapshot replay. No external APIs and no execution.",
            f"Strategy: {getattr(args, 'strategy')}",
            f"Source filter: {source_filter or 'all'}",
            f"Session ID: {session_id or 'none'}",
            f"Active only: {bool(getattr(args, 'active_only', False))}",
            f"Tiny: {bool(getattr(args, 'tiny', False))}",
        ]
        for close_mode in ("none", "mark-to-market", "approximate-expiry", "expiry-if-known"):
            override = _replace_close_mode(config, close_mode)
            with tempfile.TemporaryDirectory(prefix="close-mode-compare-") as temp_dir:
                scratch = SQLiteStore(Path(temp_dir) / "compare.sqlite3")
                try:
                    outcome = _simulate_replay(
                        override,
                        source_filter=source_filter,
                        since=since,
                        until=until,
                        session_id=session_id,
                        active_only=bool(getattr(args, "active_only", False)),
                        min_seconds_to_expiry=min_seconds_to_expiry,
                        max_seconds_to_expiry=max_seconds_to_expiry,
                        now=datetime.now(timezone.utc),
                        data_store=data_store,
                        result_store=scratch,
                        mode="replay",
                        since_label=getattr(args, "since", None),
                    )
                    if not outcome["ok"]:
                        lines.append(f"{close_mode}: {outcome['message']}")
                        continue
                    report = build_report(scratch, override.starting_balance, run_id=outcome["run_id"])
                    lines.append(
                        " | ".join(
                            [
                                f"close_mode={close_mode}",
                                f"accepted_trades={outcome['accepted']}",
                                f"closed_trades={report.closed_trades}",
                                f"open_positions={report.open_positions}",
                                f"realized_pnl=${report.realized_pnl:.2f}",
                                f"unrealized_pnl=${report.unrealized_pnl:.2f}",
                                f"win_rate={report.win_rate:.2%}",
                                f"max_drawdown=${report.max_equity_drawdown:.2f}",
                                f"max_exposure=${report.max_position_exposure:.2f}",
                                f"expectancy={_fmt_money(report.expectancy_per_trade)}",
                                f"top_1_pnl={_fmt_money(report.top_1_trade_pnl)}",
                                f"top_3_pnl={_fmt_money(report.top_3_trades_pnl)}",
                                f"top_1_pct={'n/a' if report.top_1_trade_pct_of_total_pnl is None else f'{report.top_1_trade_pct_of_total_pnl:.2%}'}",
                                f"top_3_pct={'n/a' if report.top_3_trades_pct_of_total_pnl is None else f'{report.top_3_trades_pct_of_total_pnl:.2%}'}",
                                f"settlement_unavailable={report.settlement_unavailable}",
                                f"verdicts={', '.join(report.verdicts)}",
                            ]
                        )
                    )
                finally:
                    scratch.close()
        print("\n".join(lines))
    finally:
        data_store.close()
    return 0


def settlement_report(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        run_id = getattr(args, "run_id", None)
        if not run_id:
            run_id = store.latest_run_id() if bool(getattr(args, "latest", False)) or not getattr(args, "run_id", None) else None
        if not run_id:
            print("No run selected.")
            return 1
        print(build_settlement_report(store, run_id))
    finally:
        store.close()
    return 0


def signal_audit(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        run_id = getattr(args, "run_id", None)
        if not run_id:
            run_id = store.latest_run_id() if bool(getattr(args, "latest", False)) or not getattr(args, "run_id", None) else None
        if not run_id:
            print("No run selected.")
            return 1
        print(build_signal_audit(store, run_id))
    finally:
        store.close()
    return 0


def side_audit(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    store = SQLiteStore(config.database_path)
    try:
        selected_run_id = getattr(args, "run_id", None)
        selected_session_id = getattr(args, "session_id", None)
        if selected_run_id:
            run = store.run_by_id(selected_run_id)
            if run is None:
                print("Run not found.")
                return 1
            _, rows = load_signal_audit_rows(store, selected_run_id)
            summary = summarize_signal_audit_rows(rows)
            report = build_report(store, config.starting_balance, run_id=selected_run_id)
            print(
                build_side_audit_report(
                    source_filter=source_filter,
                    ready_session_count=1,
                    conservative_run_count=1,
                    missing_session_ids=[],
                    rows=rows,
                    summary=summary,
                    run_verdicts={selected_run_id: report.verdicts},
                    details=bool(getattr(args, "details", False)),
                )
            )
            return 0

        ready_sessions = _ready_public_sessions(store, source_filter=source_filter, session_id=selected_session_id)
        rows = []
        missing_sessions: list[str] = []
        matched_runs = 0
        run_verdicts: dict[str, tuple[str, ...]] = {}
        for session_id, _since, _until in ready_sessions:
            run = _matching_momentum_run(
                store,
                session_id=session_id,
                source_filter=source_filter,
                preset="conservative-tiny",
                reverse_signal=False,
            )
            if run is None:
                missing_sessions.append(session_id)
                continue
            matched_runs += 1
            current_run_id = str(run["run_id"])
            _, run_rows = load_signal_audit_rows(store, current_run_id)
            rows.extend(run_rows)
            run_verdicts[current_run_id] = build_report(store, config.starting_balance, run_id=current_run_id).verdicts
        summary = summarize_signal_audit_rows(rows)
        print(
            build_side_audit_report(
                source_filter=source_filter,
                ready_session_count=len(ready_sessions),
                conservative_run_count=matched_runs,
                missing_session_ids=missing_sessions,
                rows=rows,
                summary=summary,
                run_verdicts=run_verdicts,
                details=bool(getattr(args, "details", False)),
            )
        )
    finally:
        store.close()
    return 0


def close_divergence(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None))
    data_store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(data_store, args)
        active_only = bool(getattr(args, "active_only", False))
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        results = {}
        for close_mode in ("mark-to-market", "approximate-expiry"):
            override = _replace_close_mode(config, close_mode)
            with tempfile.TemporaryDirectory(prefix="close-divergence-") as temp_dir:
                scratch = SQLiteStore(Path(temp_dir) / f"{close_mode}.sqlite3")
                try:
                    outcome = _simulate_replay(
                        override,
                        source_filter=source_filter,
                        since=since,
                        until=until,
                        session_id=session_id,
                        active_only=active_only,
                        min_seconds_to_expiry=min_seconds_to_expiry,
                        max_seconds_to_expiry=max_seconds_to_expiry,
                        now=datetime.now(timezone.utc),
                        data_store=data_store,
                        result_store=scratch,
                        mode="replay",
                        since_label=getattr(args, "since", None),
                    )
                    if not outcome["ok"]:
                        print(outcome["message"])
                        return 1
                    results[close_mode] = scratch.trade_rows(run_id=outcome["run_id"])
                finally:
                    scratch.close()
        print(
            build_close_divergence_report(
                strategy=getattr(args, "strategy"),
                source_filter=source_filter,
                session_id=session_id,
                active_only=active_only,
                tiny=bool(getattr(args, "tiny", False)),
                mark_rows=results["mark-to-market"],
                approx_rows=results["approximate-expiry"],
            )
        )
    finally:
        data_store.close()
    return 0


def momentum_audit(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None))
    data_store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(data_store, args)
        base_active_only = True
        experiments: list[tuple[str, AgentConfig]] = [
            ("up-only", _replace_config_values(config, momentum_side_filter="UP")),
            ("down-only", _replace_config_values(config, momentum_side_filter="DOWN")),
            ("btc-only", _replace_config_values(config, momentum_asset_filter="BTC")),
            ("eth-only", _replace_config_values(config, momentum_asset_filter="ETH")),
            ("5m-only", _replace_config_values(config, momentum_duration_filter="5m")),
            ("15m-only", _replace_config_values(config, momentum_duration_filter="15m")),
            ("expiry-30-60", _replace_config_values(config, min_seconds_to_expiry=30, max_seconds_to_expiry=60)),
            ("expiry-60-120", _replace_config_values(config, min_seconds_to_expiry=60, max_seconds_to_expiry=120)),
            ("expiry-120-180", _replace_config_values(config, min_seconds_to_expiry=120, max_seconds_to_expiry=180)),
            ("expiry-180-240", _replace_config_values(config, min_seconds_to_expiry=180, max_seconds_to_expiry=240)),
            ("balanced-tiny-momentum", _apply_momentum_preset(config, "balanced-tiny-momentum")),
            ("conservative-tiny-momentum", _apply_momentum_preset(config, "conservative-tiny-momentum")),
        ]
        baseline_rows = []
        timing_rows = []
        for close_mode in ("approximate-expiry", "mark-to-market"):
            override = _replace_close_mode(config, close_mode)
            summary = _scratch_replay_summary(
                override,
                data_store=data_store,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
                active_only=base_active_only,
                min_seconds_to_expiry=override.min_seconds_to_expiry,
                max_seconds_to_expiry=override.max_seconds_to_expiry,
                mode="replay",
                label="baseline",
            )
            baseline_rows.append(summary)
            timing_rows.extend(_timing_bucket_rows(summary["trade_rows"], close_mode))
        experiment_rows = []
        for label, override in experiments:
            summary = _scratch_replay_summary(
                _replace_close_mode(override, "approximate-expiry"),
                data_store=data_store,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
                active_only=base_active_only,
                min_seconds_to_expiry=override.min_seconds_to_expiry,
                max_seconds_to_expiry=override.max_seconds_to_expiry,
                mode="replay",
                label=label,
            )
            experiment_rows.append(summary)
        print(
            build_momentum_audit_report(
                source_filter=source_filter,
                session_id=session_id,
                tiny=bool(getattr(args, "tiny", False)),
                baseline_rows=baseline_rows,
                timing_rows=timing_rows,
                experiment_rows=experiment_rows,
            )
        )
    finally:
        data_store.close()
    return 0


def side_sweep(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    store = SQLiteStore(config.database_path)
    try:
        session_scope = getattr(args, "session_id", None) or "all ready public sessions"
        rows = _side_sweep_variant_rows(store, config, source_filter=source_filter, session_id=getattr(args, "session_id", None))
        print(build_side_sweep_report(source_filter=source_filter, session_scope=session_scope, rows=rows))
    finally:
        store.close()
    return 0


def candidate_ranking(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    store = SQLiteStore(config.database_path)
    try:
        session_scope = getattr(args, "session_id", None) or "all ready public sessions"
        rows = _side_sweep_variant_rows(store, config, source_filter=source_filter, session_id=getattr(args, "session_id", None))
        print(build_candidate_ranking_report(source_filter=source_filter, session_scope=session_scope, rows=rows))
    finally:
        store.close()
    return 0


def compare_candidates(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    store = SQLiteStore(config.database_path)
    try:
        session_scope = getattr(args, "session_id", None) or "all ready public sessions"
        rows = _side_sweep_variant_rows(store, config, source_filter=source_filter, session_id=getattr(args, "session_id", None))
        selected_labels = (
            "conservative-tiny",
            "conservative-entry-30-70",
            "reverse conservative",
            "entry-0.40-0.75",
            "DOWN-only",
            "expiry-120-180",
        )
        selected_rows = [row for row in rows if row.label in selected_labels]
        print(build_candidate_comparison_report(source_filter=source_filter, session_scope=session_scope, rows=selected_rows))
    finally:
        store.close()
    return 0


def conservative_report(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    data_store = SQLiteStore(config.database_path)
    try:
        preset = getattr(args, "preset", None) or "conservative-entry-30-70"
        print(_build_preset_report_text(data_store, config, source_filter, preset))
    finally:
        data_store.close()
    return 0


def preset_report(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    data_store = SQLiteStore(config.database_path)
    try:
        print(_build_preset_report_text(data_store, config, source_filter, args.preset))
    finally:
        data_store.close()
    return 0


def validate_conservative(config: AgentConfig, args) -> int:
    source_filter = _clean_source_filter(getattr(args, "source", None)) or "public"
    preset = getattr(args, "preset", None) or "conservative-entry-30-70"
    rerun = bool(getattr(args, "rerun", False))
    store = SQLiteStore(config.database_path)
    try:
        ready_sessions = _ready_public_sessions(store, source_filter=source_filter)
        print("Conservative validation")
        print(f"Source filter: {source_filter}")
        print(f"Preset: {preset}")
        print(f"Ready sessions found: {len(ready_sessions)}")
        if not ready_sessions:
            print("No replay-ready public sessions found.")
            return 0
        validation_config = _replace_close_mode(
            _replace_strategy(_apply_named_preset(config, preset), "momentum"),
            "approximate-expiry",
        )
        for session_id, since, until in ready_sessions:
            existing_run = None if rerun else _matching_momentum_run(
                store,
                session_id=session_id,
                source_filter=source_filter,
                preset=validation_config.momentum_preset or preset,
                reverse_signal=validation_config.reverse_signal,
            )
            if existing_run is not None:
                print(f"{session_id} | reused_run_id={existing_run['run_id']} | realized_pnl={_fmt_money(existing_run['realized_pnl'])}")
                continue
            outcome = _simulate_replay(
                validation_config,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
                active_only=True,
                min_seconds_to_expiry=validation_config.min_seconds_to_expiry,
                max_seconds_to_expiry=validation_config.max_seconds_to_expiry,
                now=datetime.now(timezone.utc),
                data_store=store,
                result_store=store,
                mode="replay",
                since_label=since.isoformat() if since else None,
            )
            if not outcome["ok"]:
                print(f"{session_id} | failed={outcome['message']}")
                continue
            print(
                f"{session_id} | created_run_id={outcome['run_id']} | accepted={outcome['accepted']} | "
                f"skipped={outcome['skipped']} | closed={outcome['closed']}"
            )
        print(_build_preset_report_text(store, config, source_filter, preset))
    finally:
        store.close()
    return 0


def validate_candidate(config: AgentConfig, args) -> int:
    alias_args = argparse.Namespace(**vars(args))
    alias_args.preset = args.candidate
    return validate_conservative(config, alias_args)


def candidate_report(config: AgentConfig, args) -> int:
    alias_args = argparse.Namespace(**vars(args))
    alias_args.preset = args.candidate
    return preset_report(config, alias_args)


def readiness(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        source_filter = _clean_source_filter(getattr(args, "source", None))
        session_id, since, until = _resolved_time_filters(store, args)
        result = store.readiness(
            source_filter=source_filter or "all",
            since=since,
            until=until,
            session_id=session_id,
        )
        print("Dataset readiness")
        print(f"Verdict: {result['verdict']}")
        print(f"Source filter: {result['source_filter']}")
        print(f"Dataset includes demo: {result['dataset_sources']['demo']}")
        print(f"Dataset includes public: {result['dataset_sources']['public']}")
        print(f"Exchange prices: {result['has_exchange_prices']}")
        print(f"Polymarket markets: {result['has_polymarket_markets']}")
        print(f"Public token ids: {result['has_public_token_ids']}")
        print(f"Polymarket orderbooks: {result['has_polymarket_orderbooks']}")
        print(f"Asset overlap: {_format_sources(result['asset_overlap'])}")
        print(f"Timestamps overlap: {result['timestamps_overlap']}")
        print(f"Minimum snapshot count met: {result['minimum_snapshot_count_met']}")
        print(f"Snapshots: {result['snapshot_count']}")
        print(f"Markets: {result['market_count']}")
        print(f"Token-ready markets: {result['token_count']}")
        print(f"Orderbooks: {result['orderbook_count']}")
        print(f"Exchange price snapshots: {result['exchange_price_count']}")
        print(f"Wide spreads: {result['wide_spreads']}")
        print(f"Missing orderbooks: {result['missing_orderbooks']}")
        quality = store.data_quality_metrics(
            source_filter=source_filter or "all",
            since=since,
            until=until,
            session_id=session_id,
        )
        print(f"Stale exchange prices: {quality['stale_exchange_prices']}")
        print(f"Stale orderbooks: {quality['stale_orderbooks']}")
        print(f"Expired markets seen: {quality['expired_markets_seen']}")
        print(f"Invalid timestamps: {quality['invalid_timestamps']}")
        print(f"Total stale snapshots: {quality['total_stale_snapshots']}")
    finally:
        store.close()
    return 0


def market_audit(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        rows = store.market_audit_rows(
            source_filter=_clean_source_filter(getattr(args, "source", None)),
            since=since,
            until=until,
            session_id=session_id,
        )
        print("Polymarket market discovery audit")
        if not rows:
            print("No markets found for the requested source/time filter.")
            return 0
        for row in rows:
            print(
                " | ".join(
                    [
                        row["market_id"],
                        row["slug"],
                        f"asset={row['asset']}",
                        f"source={row['source']}",
                        f"classification={row['classification']}",
                        f"token_status={row['token_status']}",
                        f"orderbook_status={row['orderbook_status']}",
                        f"accepted={row['accepted']}",
                        f"first_seen={row['first_seen']}",
                        f"latest_seen={row['latest_seen']}",
                        f"reason={row['reason']}",
                        f"title={row['title']}",
                    ]
                )
            )
    finally:
        store.close()
    return 0


def active_markets(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        session_id, since, until = _resolved_time_filters(store, args)
        min_seconds_to_expiry, max_seconds_to_expiry = _effective_expiry_filters(config, args)
        print(
            build_active_market_report(
                store,
                config,
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                since=since,
                until=until,
                session_id=session_id,
                min_seconds_to_expiry=min_seconds_to_expiry,
                max_seconds_to_expiry=max_seconds_to_expiry,
            )
        )
    finally:
        store.close()
    return 0


def discover_markets(config: AgentConfig, args) -> int:
    collector = PolymarketPublicCollector(config.gamma_base_url, config.clob_base_url)
    asset_filter = _asset_filter(getattr(args, "asset", "all"))
    candidates = collector.discover_market_candidates(
        asset_filter=asset_filter,
        max_duration_minutes=config.max_market_duration_minutes,
    )
    candidates, _books = collector.capture_orderbooks(candidates)
    print("Public Polymarket discovery probe")
    if not candidates:
        print("No candidate markets found.")
        return 0
    for candidate in candidates:
        print(
            " | ".join(
                [
                    candidate.market_id,
                    candidate.slug,
                    f"asset={candidate.asset_label}",
                    f"classification={candidate.classification.value}",
                    f"active={candidate.active}",
                    f"closed={candidate.closed}",
                    f"token_status={candidate.token_status}",
                    f"orderbook_status={candidate.orderbook_status}",
                    f"accepted={candidate.accepted}",
                    f"yes_token={candidate.up_token_id or 'n/a'}",
                    f"no_token={candidate.down_token_id or 'n/a'}",
                    f"window_end={candidate.window_end.isoformat() if candidate.window_end else 'n/a'}",
                    f"reason={candidate.reason}",
                    f"title={candidate.title}",
                ]
            )
        )
    return 0


def _run_momentum(
    config: AgentConfig,
    engine: PaperTradingEngine,
    markets,
    orderbook_source,
    candle_source,
    current_prices,
    now: datetime,
) -> tuple[int, int]:
    strategy = MomentumUpDownStrategy()
    accepted = 0
    skipped = 0
    for market in markets:
        signal = _signal_for(strategy, candle_source, market.asset)
        if config.reverse_signal:
            signal = _reverse_signal(signal)
        filter_decision = _apply_momentum_filters(config, market, signal, now)
        if filter_decision is not None:
            engine.store.log_opportunity(now, filter_decision, run_id=engine.run_id)
            skipped += 1
            continue
        orderbook = _orderbook_for(orderbook_source, market, signal)
        if orderbook is None:
            decision = OpportunityDecision(
                market=market,
                signal=signal,
                market_price=None,
                spread=None,
                decision="SKIP",
                reason="public orderbook unavailable",
            )
            engine.store.log_opportunity(now, decision, run_id=engine.run_id)
            skipped += 1
            continue
        filter_decision = _apply_momentum_filters(
            config,
            market,
            signal,
            now,
            market_price=orderbook.best_ask or orderbook.midpoint,
            spread=orderbook.spread if orderbook.spread is not None else config.assumed_spread,
        )
        if filter_decision is not None:
            engine.store.log_opportunity(now, filter_decision, run_id=engine.run_id)
            skipped += 1
            continue
        decision = engine.evaluate(market, signal, orderbook, now=now)
        snapshot = current_prices[market.asset.value]
        fill = engine.enter(decision, orderbook, snapshot, now=now)
        if fill:
            accepted += 1
        else:
            skipped += 1
    return accepted, skipped


def _run_pair_cost(
    config: AgentConfig,
    engine: PaperTradingEngine,
    markets,
    orderbook_source,
    current_prices,
    now: datetime,
) -> tuple[int, int]:
    strategy = PairCostArbitrageStrategy(
        threshold=config.pair_cost_threshold,
        max_spread=config.max_spread,
        slippage_bps=config.slippage_bps,
        failed_second_leg_probability=config.pair_cost_failed_second_leg_probability,
        random_seed=config.random_seed,
    )
    accepted = 0
    skipped = 0
    for market in markets:
        decision = strategy.evaluate(
            market,
            orderbook_source.orderbook(market.up_token_id),
            orderbook_source.orderbook(market.down_token_id),
        )
        fills = engine.enter_pair(decision, current_prices[market.asset.value], now=now)
        if fills:
            accepted += len(fills)
        else:
            skipped += 1
    return accepted, skipped


def _market_duration_label(market: Market) -> str:
    minutes = market.window.duration_minutes
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"


def _reverse_signal(signal: Signal) -> Signal:
    return Signal(
        asset=signal.asset,
        direction=Direction.DOWN if signal.direction == Direction.UP else Direction.UP,
        probability=signal.probability,
        edge=signal.edge,
        reason=f"{signal.reason}; reverse-signal",
    )


def _momentum_skip_decision(
    config: AgentConfig,
    market: Market,
    signal: Signal,
    reason: str,
    observed_at: datetime,
    market_price: float | None = None,
    spread: float | None = None,
) -> OpportunityDecision:
    lifecycle = classify_market_lifecycle(
        market,
        observed_at,
        min_seconds_before_end=config.min_seconds_before_end,
        max_seconds_after_start=config.max_seconds_after_start,
    )
    return OpportunityDecision(
        market=market,
        signal=signal,
        market_price=market_price,
        spread=spread,
        decision="SKIP",
        reason=reason,
        seconds_to_expiry=lifecycle.seconds_to_expiry,
        lifecycle_status=lifecycle.status,
        timing_bucket=lifecycle.timing_bucket,
    )


def _apply_momentum_filters(
    config: AgentConfig,
    market: Market,
    signal: Signal,
    observed_at: datetime,
    *,
    market_price: float | None = None,
    spread: float | None = None,
) -> OpportunityDecision | None:
    if config.momentum_asset_filter and market.asset.value != config.momentum_asset_filter:
        return _momentum_skip_decision(config, market, signal, "asset filter", observed_at, market_price, spread)
    if config.momentum_duration_filter and _market_duration_label(market) != config.momentum_duration_filter:
        return _momentum_skip_decision(config, market, signal, "duration filter", observed_at, market_price, spread)
    if config.momentum_side_filter and signal.direction.value != config.momentum_side_filter:
        return _momentum_skip_decision(config, market, signal, "side filter", observed_at, market_price, spread)
    if market_price is not None and config.momentum_min_entry_price is not None and market_price < config.momentum_min_entry_price:
        return _momentum_skip_decision(config, market, signal, "entry price below minimum", observed_at, market_price, spread)
    if market_price is not None and config.momentum_max_entry_price is not None and market_price > config.momentum_max_entry_price:
        return _momentum_skip_decision(config, market, signal, "entry price above maximum", observed_at, market_price, spread)
    return None


def _run_replay_momentum(
    config: AgentConfig,
    engine: PaperTradingEngine,
    data_store: SQLiteStore,
    markets,
    *,
    close_mode: str,
    settlement_prices,
    status_counts: dict[str, int],
    source_filter: str | None,
    session_id: str | None,
) -> tuple[int, int]:
    strategy = MomentumUpDownStrategy()
    accepted = 0
    skipped = 0
    for market in markets:
        observed_at = market.observed_at or market.window.start
        _merge_close_counts(
            status_counts,
            _close_replay_positions(
                config,
                engine,
                data_store,
                close_mode=close_mode,
                replay_end=observed_at,
                count_open=False,
                source_filter=source_filter,
                session_id=session_id,
                settlement_prices=settlement_prices,
            ),
        )
        price_snapshot = data_store.latest_price(
            market.asset.value,
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if price_snapshot is None:
            engine.store.log_opportunity(
                observed_at,
                OpportunityDecision(
                    market=market,
                    signal=Signal(asset=market.asset, direction=Direction.UP, probability=0.5, edge=0.0, reason="missing underlying price"),
                    market_price=None,
                    spread=None,
                    decision="SKIP",
                    reason="missing underlying price",
                ),
                run_id=engine.run_id,
            )
            skipped += 1
            continue
        signal = _signal_for_at(strategy, data_store, market.asset, observed_at, source_filter, session_id)
        if config.reverse_signal:
            signal = _reverse_signal(signal)
        filter_decision = _apply_momentum_filters(config, market, signal, observed_at)
        if filter_decision is not None:
            engine.store.log_opportunity(observed_at, filter_decision, run_id=engine.run_id)
            skipped += 1
            continue
        orderbook = data_store.collected_orderbook(
            market.token_for(signal.direction),
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if orderbook is None:
            lifecycle = classify_market_lifecycle(
                market,
                observed_at,
                min_seconds_before_end=config.min_seconds_before_end,
                max_seconds_after_start=config.max_seconds_after_start,
            )
            decision = OpportunityDecision(
                market=market,
                signal=signal,
                market_price=None,
                spread=None,
                decision="SKIP",
                reason="public orderbook unavailable",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
            )
            engine.store.log_opportunity(observed_at, decision, run_id=engine.run_id)
            skipped += 1
            continue
        filter_decision = _apply_momentum_filters(
            config,
            market,
            signal,
            observed_at,
            market_price=orderbook.best_ask or orderbook.midpoint,
            spread=orderbook.spread if orderbook.spread is not None else config.assumed_spread,
        )
        if filter_decision is not None:
            engine.store.log_opportunity(observed_at, filter_decision, run_id=engine.run_id)
            skipped += 1
            continue
        decision = engine.evaluate(market, signal, orderbook, now=observed_at)
        fill = engine.enter(decision, orderbook, price_snapshot, now=observed_at)
        if fill:
            accepted += 1
        else:
            skipped += 1
    return accepted, skipped


def _run_replay_pair_cost(
    config: AgentConfig,
    engine: PaperTradingEngine,
    data_store: SQLiteStore,
    markets,
    *,
    close_mode: str,
    settlement_prices,
    status_counts: dict[str, int],
    source_filter: str | None,
    session_id: str | None,
) -> tuple[int, int]:
    strategy = PairCostArbitrageStrategy(
        threshold=config.pair_cost_threshold,
        max_spread=config.max_spread,
        slippage_bps=config.slippage_bps,
        failed_second_leg_probability=config.pair_cost_failed_second_leg_probability,
        random_seed=config.random_seed,
    )
    accepted = 0
    skipped = 0
    for market in markets:
        observed_at = market.observed_at or market.window.start
        _merge_close_counts(
            status_counts,
            _close_replay_positions(
                config,
                engine,
                data_store,
                close_mode=close_mode,
                replay_end=observed_at,
                count_open=False,
                source_filter=source_filter,
                session_id=session_id,
                settlement_prices=settlement_prices,
            ),
        )
        price_snapshot = data_store.latest_price(
            market.asset.value,
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if price_snapshot is None:
            signal = Signal(asset=market.asset, direction=Direction.UP, probability=1.0, edge=0.0, reason="missing underlying price")
            engine.store.log_opportunity(
                observed_at,
                OpportunityDecision(
                    market=market,
                    signal=signal,
                    market_price=None,
                    spread=None,
                    decision="SKIP",
                    reason="missing underlying price",
                ),
                run_id=engine.run_id,
            )
            skipped += 1
            continue
        lifecycle = classify_market_lifecycle(
            market,
            observed_at,
            min_seconds_before_end=config.min_seconds_before_end,
            max_seconds_after_start=config.max_seconds_after_start,
        )
        if lifecycle.timing_bucket != "valid_window":
            signal = Signal(asset=market.asset, direction=Direction.UP, probability=1.0, edge=0.0, reason="pair-cost timing check")
            reason = {
                "too_early": "market not started",
                "too_late": "outside timing window",
                "expired": "market expired",
                "missing_expiry": "missing expiry",
            }.get(lifecycle.timing_bucket, "outside timing window")
            engine.store.log_opportunity(
                observed_at,
                OpportunityDecision(
                    market=market,
                    signal=signal,
                    market_price=None,
                    spread=None,
                    decision="SKIP",
                    reason=reason,
                    seconds_to_expiry=lifecycle.seconds_to_expiry,
                    lifecycle_status=lifecycle.status,
                    timing_bucket=lifecycle.timing_bucket,
                ),
                run_id=engine.run_id,
            )
            skipped += 1
            continue
        decision = strategy.evaluate(
            market,
            data_store.collected_orderbook(
                market.up_token_id,
                source_filter=source_filter,
                until=observed_at,
                session_id=session_id,
            ),
            data_store.collected_orderbook(
                market.down_token_id,
                source_filter=source_filter,
                until=observed_at,
                session_id=session_id,
            ),
        )
        decision = replace(
            decision,
            seconds_to_expiry=lifecycle.seconds_to_expiry,
            lifecycle_status=lifecycle.status,
            timing_bucket=lifecycle.timing_bucket,
        )
        fills = engine.enter_pair(decision, price_snapshot, now=observed_at)
        if fills:
            accepted += len(fills)
        else:
            skipped += 1
    return accepted, skipped


def _run_replay_stuck_markov(
    config: AgentConfig,
    engine: PaperTradingEngine,
    data_store: SQLiteStore,
    markets,
    *,
    close_mode: str,
    settlement_prices,
    status_counts: dict[str, int],
    source_filter: str | None,
    session_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[int, int]:
    model = build_markov_model(
        data_store,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        config=config,
    )
    accepted = 0
    skipped = 0
    for market in markets:
        observed_at = latest_tradeable_observation_time(model, market, config) or market.observed_at or market.window.start
        _merge_close_counts(
            status_counts,
            _close_replay_positions(
                config,
                engine,
                data_store,
                close_mode=close_mode,
                replay_end=observed_at,
                count_open=False,
                source_filter=source_filter,
                session_id=session_id,
                settlement_prices=settlement_prices,
            ),
        )
        price_snapshot = data_store.latest_price(
            market.asset.value,
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if price_snapshot is None:
            engine.store.log_opportunity(
                observed_at,
                OpportunityDecision(
                    market=market,
                    signal=Signal(asset=market.asset, direction=Direction.UP, probability=0.5, edge=0.0, reason="missing underlying price"),
                    market_price=None,
                    spread=None,
                    decision="SKIP",
                    reason="missing underlying price",
                ),
                run_id=engine.run_id,
            )
            skipped += 1
            continue
        strategy_decision = evaluate_stuck_markov_market(
            data_store,
            model,
            market,
            observed_at=observed_at,
            source_filter=source_filter,
            session_id=session_id,
            config=config,
        ).decision
        if strategy_decision.decision != "TRADE":
            engine.store.log_opportunity(observed_at, strategy_decision, run_id=engine.run_id)
            skipped += 1
            continue
        orderbook = data_store.collected_orderbook(
            market.token_for(strategy_decision.signal.direction),
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if orderbook is None:
            engine.store.log_opportunity(
                observed_at,
                replace(strategy_decision, decision="SKIP", reason="missing orderbook"),
                run_id=engine.run_id,
            )
            skipped += 1
            continue
        fill = engine.enter(strategy_decision, orderbook, price_snapshot, now=observed_at)
        if fill:
            accepted += 1
        else:
            skipped += 1
    return accepted, skipped


def _close_replay_positions(
    config: AgentConfig,
    engine: PaperTradingEngine,
    data_store: SQLiteStore,
    *,
    close_mode: str,
    replay_end: datetime,
    count_open: bool,
    source_filter: str | None,
    session_id: str | None,
    settlement_prices,
) -> dict[str, int]:
    status_counts = {
        "closed_by_mark_to_market": 0,
        "closed_by_expiry": 0,
        "expired_unresolved": 0,
        "settlement_unavailable": 0,
        "open": 0,
    }
    for trade in engine.store.open_trades(run_id=engine.run_id):
        window_end = _trade_time(trade["window_end"]) or replay_end
        window_start = _trade_time(trade["window_start"]) or _trade_time(trade["opened_at"]) or window_end
        close_at = min(window_end, replay_end)
        if close_mode == "mark-to-market":
            book = data_store.collected_orderbook(
                str(trade["token_id"]),
                source_filter=source_filter,
                until=close_at,
                session_id=session_id,
            )
            midpoint = book.midpoint if book is not None else None
            if midpoint is None:
                status = "SETTLEMENT_UNAVAILABLE" if replay_end >= window_end else "OPEN"
                if status == "OPEN":
                    if count_open:
                        status_counts["open"] += 1
                    continue
                engine.close_position(
                    now=close_at,
                    trade=trade,
                    exit_underlying_price=None,
                    exit_price=None,
                    status=status,
                    close_mode=close_mode,
                    settlement_note="mark-to-market midpoint unavailable",
                )
                status_counts["settlement_unavailable"] += 1
                continue
            underlying = data_store.latest_price(
                str(trade["asset"]),
                source_filter=source_filter,
                until=close_at,
                session_id=session_id,
            )
            engine.close_position(
                now=close_at,
                trade=trade,
                exit_underlying_price=underlying.price if underlying else None,
                exit_price=midpoint,
                status="CLOSED_BY_MARK_TO_MARKET",
                close_mode=close_mode,
                settlement_note="latest midpoint before expiry/session end",
            )
            status_counts["closed_by_mark_to_market"] += 1
            continue

        if replay_end < window_end:
            if count_open:
                status_counts["open"] += 1
            continue

        if close_mode == "approximate-expiry":
            start_price = data_store.nearest_price(
                str(trade["asset"]),
                window_start,
                max_delta_seconds=60,
                source_filter=source_filter,
                session_id=session_id,
            )
            end_price = data_store.nearest_price(
                str(trade["asset"]),
                window_end,
                max_delta_seconds=60,
                source_filter=source_filter,
                session_id=session_id,
            )
            if start_price is None or end_price is None:
                engine.close_position(
                    now=window_end,
                    trade=trade,
                    exit_underlying_price=end_price.price if end_price else None,
                    exit_price=None,
                    status="SETTLEMENT_UNAVAILABLE",
                    close_mode=close_mode,
                    settlement_note="approximate expiry prices unavailable",
                )
                status_counts["settlement_unavailable"] += 1
                continue
            exit_value = resolve_binary_value(
                direction=Direction(str(trade["direction"])),
                entry_underlying_price=start_price.price,
                exit_underlying_price=end_price.price,
            )
            engine.close_position(
                now=window_end,
                trade=trade,
                exit_underlying_price=end_price.price,
                exit_price=exit_value,
                status="CLOSED_BY_EXPIRY",
                close_mode=close_mode,
                settlement_note="approximate expiry from stored exchange prices",
            )
            status_counts["closed_by_expiry"] += 1
            continue

        settlement_price = settlement_prices.get(str(trade["asset"])) if close_mode == "expiry-if-known" else None
        if settlement_price is None and close_mode == "expiry-if-known":
            engine.close_position(
                now=window_end,
                trade=trade,
                exit_underlying_price=None,
                exit_price=None,
                status="SETTLEMENT_UNAVAILABLE",
                close_mode=close_mode,
                settlement_note="no stored settlement price available",
            )
            status_counts["settlement_unavailable"] += 1
            continue

        if close_mode == "none":
            engine.close_position(
                now=window_end,
                trade=trade,
                exit_underlying_price=None,
                exit_price=None,
                status="EXPIRED_UNRESOLVED",
                close_mode=close_mode,
                settlement_note="market expired with no close mode",
            )
            status_counts["expired_unresolved"] += 1
            continue

        if settlement_price is None:
            engine.close_position(
                now=window_end,
                trade=trade,
                exit_underlying_price=None,
                exit_price=None,
                status="EXPIRED_UNRESOLVED",
                close_mode=close_mode,
                settlement_note="expiry settlement unavailable",
            )
            status_counts["expired_unresolved"] += 1
            continue
        exit_value = resolve_binary_value(
            direction=Direction(str(trade["direction"])),
            entry_underlying_price=float(trade["entry_underlying_price"]),
            exit_underlying_price=settlement_price.price,
        )
        engine.close_position(
            now=window_end,
            trade=trade,
            exit_underlying_price=settlement_price.price,
            exit_price=exit_value,
            status="CLOSED_BY_EXPIRY",
            close_mode=close_mode,
            settlement_note="settled from stored expiry reference price",
        )
        status_counts["closed_by_expiry"] += 1
    return status_counts


def _simulate_replay(
    config: AgentConfig,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
    active_only: bool,
    min_seconds_to_expiry: int | None,
    max_seconds_to_expiry: int | None,
    now: datetime,
    data_store: SQLiteStore,
    result_store: SQLiteStore,
    mode: str,
    since_label: str | None,
) -> dict:
    current_prices, candle_source, markets, orderbook_source, settlement_prices, actual_sources = _load_replay_context(
        data_store,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        config=config,
        active_only=active_only,
        min_seconds_to_expiry=min_seconds_to_expiry,
        max_seconds_to_expiry=max_seconds_to_expiry,
    )
    if not current_prices:
        return {
            "ok": False,
            "actual_sources": actual_sources,
            "message": (
                "Replay cannot run: no stored BTC/ETH price snapshots for the requested source. "
                "Run observe for public data or collect --demo for demo data."
            ),
        }
    if not markets:
        if source_filter == "public":
            message = (
                "Replay cannot run with --source public: public data is insufficient; "
                "no stored public Polymarket markets were found. Demo data was not used."
            )
        else:
            message = (
                "Replay cannot run: no stored Polymarket markets for the requested source. "
                "Observe may not have found UP/DOWN markets; use collect --demo for a complete offline dataset."
            )
        return {"ok": False, "actual_sources": actual_sources, "message": message}
    missing_books = [
        market.slug
        for market in markets
        if orderbook_source.orderbook(market.up_token_id) is None
        or orderbook_source.orderbook(market.down_token_id) is None
    ]
    if missing_books and source_filter == "public":
        return {
            "ok": False,
            "actual_sources": actual_sources,
            "message": (
                "Replay cannot run with --source public: public data is insufficient; "
                "stored public orderbooks are missing for " + ", ".join(missing_books[:5]) + ". "
                "Demo data was not used."
            ),
        }
    if missing_books and config.strategy == "pair-cost":
        return {
            "ok": False,
            "actual_sources": actual_sources,
            "message": "Replay cannot run pair-cost: missing stored orderbooks for " + ", ".join(missing_books[:5]),
        }

    replay_end = until or _replay_end_time(data_store, source_filter=source_filter, since=since, until=until, session_id=session_id) or now
    run_id = result_store.start_run(
        strategy=config.strategy,
        mode=mode,
        data_source=source_filter or "all",
        starting_balance=config.starting_balance,
        now=now,
        notes=(
            f"{_config_notes(config)}; source_filter={source_filter or 'all'}; "
            f"since={since_label or 'none'}; until={until.isoformat() if until else 'none'}; "
            f"active_only={'true' if active_only else 'false'}; "
            f"min_seconds_to_expiry_filter={min_seconds_to_expiry if min_seconds_to_expiry is not None else 'none'}; "
            f"max_seconds_to_expiry_filter={max_seconds_to_expiry if max_seconds_to_expiry is not None else 'none'}; "
            f"close_mode={config.close_mode}"
        ),
        session_id=session_id,
    )
    engine = PaperTradingEngine(config, result_store, run_id=run_id)
    engine.record_equity(now)
    if mode in {"replay", "sweep"}:
        close_counts = {
            "closed_by_mark_to_market": 0,
            "closed_by_expiry": 0,
            "expired_unresolved": 0,
            "settlement_unavailable": 0,
            "open": 0,
        }
        if config.strategy == "pair-cost":
            accepted, skipped = _run_replay_pair_cost(
                config,
                engine,
                data_store,
                markets,
                close_mode=config.close_mode,
                settlement_prices=settlement_prices,
                status_counts=close_counts,
                source_filter=source_filter,
                session_id=session_id,
            )
        elif config.strategy == "stuck-markov":
            accepted, skipped = _run_replay_stuck_markov(
                config,
                engine,
                data_store,
                markets,
                close_mode=config.close_mode,
                settlement_prices=settlement_prices,
                status_counts=close_counts,
                source_filter=source_filter,
                session_id=session_id,
                since=since,
                until=until,
            )
        else:
            accepted, skipped = _run_replay_momentum(
                config,
                engine,
                data_store,
                markets,
                close_mode=config.close_mode,
                settlement_prices=settlement_prices,
                status_counts=close_counts,
                source_filter=source_filter,
                session_id=session_id,
            )
        _merge_close_counts(
            close_counts,
            _close_replay_positions(
                config,
                engine,
                data_store,
                close_mode=config.close_mode,
                replay_end=replay_end,
                count_open=True,
                source_filter=source_filter,
                session_id=session_id,
                settlement_prices=settlement_prices,
            ),
        )
        closed = close_counts["closed_by_expiry"] + close_counts["closed_by_mark_to_market"]
        finished_at = replay_end
    else:
        if config.strategy == "pair-cost":
            accepted, skipped = _run_pair_cost(config, engine, markets, orderbook_source, current_prices, now)
        else:
            accepted, skipped = _run_momentum(
                config,
                engine,
                markets,
                orderbook_source,
                candle_source,
                current_prices,
                now,
            )
        closed = 0
        finished_at = now
        if settlement_prices:
            settlement_now = max(market.window.end for market in markets)
            closed = engine.close_expired(settlement_prices, now=settlement_now)
            finished_at = settlement_now
    result_store.finish_run(run_id, finished_at)
    return {
        "ok": True,
        "actual_sources": actual_sources,
        "accepted": accepted,
        "skipped": skipped,
        "closed": closed,
        "run_id": run_id,
        "finished_at": finished_at,
    }


def _scratch_replay_summary(
    config: AgentConfig,
    *,
    data_store: SQLiteStore,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
    active_only: bool,
    min_seconds_to_expiry: int | None,
    max_seconds_to_expiry: int | None,
    mode: str,
    label: str,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="momentum-audit-") as temp_dir:
        scratch = SQLiteStore(Path(temp_dir) / "audit.sqlite3")
        try:
            outcome = _simulate_replay(
                config,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
                active_only=active_only,
                min_seconds_to_expiry=min_seconds_to_expiry,
                max_seconds_to_expiry=max_seconds_to_expiry,
                now=datetime.now(timezone.utc),
                data_store=data_store,
                result_store=scratch,
                mode=mode,
                since_label=since.isoformat() if since else None,
            )
            if not outcome["ok"]:
                return {
                    "ok": False,
                    "label": label,
                    "close_mode": config.close_mode,
                    "accepted": 0,
                    "closed": 0,
                    "realized_pnl": 0.0,
                    "win_rate": 0.0,
                    "average_edge": 0.0,
                    "average_edge_accepted": None,
                    "risk_blocked_trades": 0,
                    "max_exposure": 0.0,
                    "warnings": ("FAILED_REPLAY",),
                    "verdicts": (outcome["message"],),
                    "trade_rows": [],
                    "signal_rows": [],
                    "summary": {"matched": 0, "mismatched": 0, "unknown": 0, "correctness_rate": None, "by_asset": {}, "by_duration": {}, "by_side": {}},
                    "report": None,
                }
            report = build_report(scratch, config.starting_balance, run_id=outcome["run_id"])
            run_row = scratch.run_by_id(outcome["run_id"])
            opportunity_map = {
                (str(row["market_slug"]), str(row["direction"]), str(row["observed_at"])): {
                    "edge": float(row["edge"] or 0.0) if row["edge"] is not None else None,
                    "spread": float(row["spread"]) if row["spread"] is not None else None,
                }
                for row in scratch.rows(
                    """
                    SELECT market_slug, direction, observed_at, edge, spread
                    FROM opportunities
                    WHERE run_id = ? AND decision = 'TRADE'
                    """,
                    (outcome["run_id"],),
                )
            }
            signal_rows = load_signal_audit_rows_from_records(
                data_store,
                run_row,
                scratch.trade_rows(run_id=outcome["run_id"]),
                opportunity_map,
            ) if run_row is not None else []
            return {
                "ok": True,
                "label": label,
                "run_id": outcome["run_id"],
                "close_mode": config.close_mode,
                "accepted": outcome["accepted"],
                "closed": report.closed_trades,
                "realized_pnl": report.realized_pnl,
                "win_rate": report.win_rate,
                "average_edge": report.average_edge,
                "average_edge_accepted": report.average_edge_accepted,
                "risk_blocked_trades": report.risk_blocked_trades,
                "max_exposure": report.max_position_exposure,
                "warnings": report.warnings,
                "verdicts": report.verdicts,
                "trade_rows": scratch.trade_rows(run_id=outcome["run_id"]),
                "signal_rows": signal_rows,
                "summary": summarize_signal_audit_rows(signal_rows),
                "report": report,
            }
        finally:
            scratch.close()


def _timing_bucket_rows(trade_rows, close_mode: str) -> list[dict[str, object]]:
    buckets: dict[str, list] = {}
    for row in trade_rows:
        bucket = _report_seconds_bucket(row)
        buckets.setdefault(bucket, []).append(row)
    output = []
    for bucket, rows in sorted(buckets.items()):
        closed = [row for row in rows if row["pnl"] is not None]
        wins = sum(1 for row in closed if float(row["pnl"] or 0.0) > 0)
        output.append(
            {
                "close_mode": close_mode,
                "bucket": bucket,
                "closed": len(closed),
                "wins": wins,
                "win_rate": wins / len(closed) if closed else 0.0,
                "realized_pnl": sum(float(row["pnl"] or 0.0) for row in closed),
                "avg_entry_price": (
                    sum(float(row["entry_price"]) for row in rows) / len(rows)
                    if rows
                    else None
                ),
            }
        )
    return output


def _conservative_variant_summary(
    config: AgentConfig,
    *,
    data_store: SQLiteStore,
    source_filter: str | None,
    session_id: str,
    label: str,
) -> dict:
    summary = _scratch_replay_summary(
        config,
        data_store=data_store,
        source_filter=source_filter,
        since=None,
        until=None,
        session_id=session_id,
        active_only=True,
        min_seconds_to_expiry=config.min_seconds_to_expiry,
        max_seconds_to_expiry=config.max_seconds_to_expiry,
        mode="replay",
        label=label,
    )
    if not summary["ok"] or summary["report"] is None:
        return summary
    report = summary["report"]
    summary["session_id"] = session_id
    summary["paper_verdicts"] = conservative_readiness_verdict(
        closed_trades=report.closed_trades,
        realized_pnl=report.realized_pnl,
        expectancy=report.expectancy_per_trade,
        pnl_excluding_top_3=report.pnl_excluding_top_3,
        top_1_trade_pct=report.top_1_trade_pct_of_total_pnl,
        side_correctness_rate=summary["summary"]["correctness_rate"],
        max_drawdown=report.max_equity_drawdown,
        drawdown_limit=config.session_loss_limit_usd,
    )
    summary["drawdown_limit"] = config.session_loss_limit_usd
    return summary


def _ready_public_sessions(
    store: SQLiteStore,
    *,
    source_filter: str,
    session_id: str | None = None,
) -> list[tuple[str, datetime | None, datetime | None]]:
    sessions: list[tuple[str, datetime | None, datetime | None]] = []
    for session in store.research_session_rows():
        current_session_id = str(session["session_id"])
        if session_id and current_session_id != session_id:
            continue
        _, since, until = _session_bounds(store, current_session_id)
        readiness_result = store.readiness(
            source_filter=source_filter,
            since=since,
            until=until,
            session_id=current_session_id,
        )
        if readiness_result["verdict"] == "READY_FOR_PUBLIC_REPLAY":
            sessions.append((current_session_id, since, until))
    return sessions


def _matching_momentum_run(
    store: SQLiteStore,
    *,
    session_id: str,
    source_filter: str,
    preset: str,
    reverse_signal: bool,
) -> object | None:
    rows = store.rows(
        """
        SELECT *
        FROM runs
        WHERE session_id = ? AND strategy = 'momentum' AND data_source = ?
        ORDER BY started_at DESC, rowid DESC
        """,
        (session_id, source_filter),
    )
    for row in rows:
        notes = parse_config_notes(str(row["notes"] or ""))
        if (
            notes.get("momentum_preset") == preset
            and notes.get("reverse_signal", "false") == ("true" if reverse_signal else "false")
            and notes.get("active_only") == "true"
            and notes.get("close_mode") == "approximate-expiry"
        ):
            return row
    return None


def _side_sweep_variant_rows(
    store: SQLiteStore,
    config: AgentConfig,
    *,
    source_filter: str,
    session_id: str | None,
) -> list[ConservativeAggregateRow]:
    ready_sessions = _ready_public_sessions(store, source_filter=source_filter, session_id=session_id)
    if not ready_sessions:
        return []
    baseline_config = _replace_close_mode(
        _replace_strategy(_apply_named_preset(config, "conservative-tiny"), "momentum"),
        "approximate-expiry",
    )
    variant_rows: list[ConservativeAggregateRow] = []
    for label, variant_config in _side_sweep_variants(baseline_config):
        payloads = []
        for current_session_id, _since, _until in ready_sessions:
            payload = _conservative_variant_summary(
                variant_config,
                data_store=store,
                source_filter=source_filter,
                session_id=current_session_id,
                label=label,
            )
            if payload["ok"]:
                payloads.append(payload)
        if payloads:
            variant_rows.append(aggregate_variant_row(label=label, session_rows=payloads))
    return variant_rows


def _side_sweep_variants(config: AgentConfig) -> list[tuple[str, AgentConfig]]:
    return [
        ("conservative-tiny", config),
        ("reverse conservative", _replace_config_values(config, momentum_preset="conservative-tiny-reverse", reverse_signal=True)),
        ("conservative-entry-30-70", _apply_named_preset(config, "conservative-entry-30-70")),
        ("higher-min-edge", _replace_config_values(config, min_edge=max(config.min_edge, 0.05))),
        ("lower-max-spread", _replace_config_values(config, max_spread=min(config.max_spread, 0.01))),
        ("entry-0.40-0.75", _replace_config_values(config, momentum_min_entry_price=0.40, momentum_max_entry_price=0.75)),
        ("entry-0.20-0.80", _replace_config_values(config, momentum_min_entry_price=0.20, momentum_max_entry_price=0.80)),
        ("BTC-only", _replace_config_values(config, momentum_asset_filter="BTC", momentum_duration_filter=None)),
        ("ETH-only", _replace_config_values(config, momentum_asset_filter="ETH", momentum_duration_filter=None)),
        ("UP-only", _replace_config_values(config, momentum_side_filter="UP")),
        ("DOWN-only", _replace_config_values(config, momentum_side_filter="DOWN")),
        ("5m-only", _replace_config_values(config, momentum_asset_filter=None, momentum_duration_filter="5m")),
        ("15m-only", _replace_config_values(config, momentum_asset_filter=None, momentum_duration_filter="15m")),
        ("expiry-60-120", _replace_config_values(config, min_seconds_to_expiry=60, max_seconds_to_expiry=120)),
        ("expiry-120-180", _replace_config_values(config, min_seconds_to_expiry=120, max_seconds_to_expiry=180)),
    ]


def _build_preset_report_text(
    data_store: SQLiteStore,
    config: AgentConfig,
    source_filter: str,
    preset: str,
) -> str:
    sessions = [
        row
        for row in data_store.research_session_rows()
        if data_store.dataset_summary(source_filter=source_filter, session_id=str(row["session_id"]))["total_snapshots"] > 0
    ]
    baseline_config = _replace_close_mode(
        _replace_strategy(_apply_named_preset(config, preset), "momentum"),
        "approximate-expiry",
    )
    session_payloads: list[dict] = []
    variant_payloads: dict[str, list[dict]] = {
        "BTC-only candidate": [],
        "ETH-only candidate": [],
        "BTC+ETH candidate": [],
        "5m-only candidate": [],
        "15m-only candidate": [],
    }
    for session in sessions:
        session_id = str(session["session_id"])
        baseline = _conservative_variant_summary(
            baseline_config,
            data_store=data_store,
            source_filter=source_filter,
            session_id=session_id,
            label=preset,
        )
        if baseline["ok"]:
            session_payloads.append(baseline)
        variant_specs = [
            ("BTC-only candidate", {}),
            ("ETH-only candidate", {"momentum_asset_filter": "ETH"}),
            ("BTC+ETH candidate", {"momentum_asset_filter": None}),
            ("5m-only candidate", {"momentum_duration_filter": "5m"}),
            ("15m-only candidate", {"momentum_duration_filter": "15m"}),
        ]
        for label, overrides in variant_specs:
            variant = _conservative_variant_summary(
                _replace_config_values(baseline_config, **overrides),
                data_store=data_store,
                source_filter=source_filter,
                session_id=session_id,
                label=label,
            )
            if variant["ok"]:
                variant_payloads[label].append(variant)
    session_rows = [
        ConservativeSessionRow(
            session_id=payload["session_id"],
            accepted_trades=payload["accepted"],
            closed_trades=payload["report"].closed_trades,
            realized_pnl=payload["report"].realized_pnl,
            win_rate=payload["report"].win_rate,
            expectancy=payload["report"].expectancy_per_trade,
            max_drawdown=payload["report"].max_equity_drawdown,
            max_exposure=payload["report"].max_position_exposure,
            top_1_trade_pct=payload["report"].top_1_trade_pct_of_total_pnl,
            pnl_excluding_top_1=payload["report"].pnl_excluding_top_1,
            pnl_excluding_top_3=payload["report"].pnl_excluding_top_3,
            settlement_unavailable=payload["report"].settlement_unavailable,
            matched=payload["summary"]["matched"],
            mismatched=payload["summary"]["mismatched"],
            unknown=payload["summary"]["unknown"],
            side_correctness_rate=payload["summary"]["correctness_rate"],
            warnings=payload["report"].warnings,
            verdicts=payload["paper_verdicts"],
        )
        for payload in session_payloads
    ]
    aggregate = aggregate_variant_row(label=preset, session_rows=session_payloads)
    variant_rows = [
        aggregate_variant_row(label=label, session_rows=rows)
        for label, rows in variant_payloads.items()
        if rows
    ]
    return build_conservative_report(
        source_filter=source_filter,
        preset_name=preset,
        preset_summary=_conservative_preset_summary(baseline_config),
        session_rows=session_rows,
        aggregate_row=aggregate,
        variant_rows=variant_rows,
        by_asset=_aggregate_correctness_breakdowns(session_payloads, "by_asset"),
        by_duration=_aggregate_correctness_breakdowns(session_payloads, "by_duration"),
        by_side=_aggregate_correctness_breakdowns(session_payloads, "by_side"),
    )


def _aggregate_correctness_breakdowns(session_payloads: list[dict], key: str) -> dict[str, dict[str, float | int | None]]:
    merged: dict[str, dict[str, int]] = {}
    for payload in session_payloads:
        for name, stats in payload["summary"].get(key, {}).items():
            target = merged.setdefault(name, {"matched": 0, "mismatched": 0, "unknown": 0})
            target["matched"] += int(stats["matched"])
            target["mismatched"] += int(stats["mismatched"])
            target["unknown"] += int(stats["unknown"])
    output: dict[str, dict[str, float | int | None]] = {}
    for name, stats in merged.items():
        resolved = stats["matched"] + stats["mismatched"]
        output[name] = {
            **stats,
            "correctness_rate": (stats["matched"] / resolved) if resolved else None,
        }
    return output


def _effective_data_mode(config: AgentConfig, store: SQLiteStore) -> str:
    if config.use_demo_markets:
        return "demo"
    if store.get_state("last_collection_mode") == "demo":
        return "demo"
    return "public"


def _load_run_context(
    config: AgentConfig,
    store: SQLiteStore,
    mode: str,
    now: datetime,
):
    if mode == "demo":
        demo = MockMarketSource()
        current_prices = store.latest_prices(source_prefix="mock:demo:spot:")
        if not current_prices:
            current_prices = {snapshot.asset.value: snapshot for snapshot in demo.collect_prices(now=now)}
            for snapshot in current_prices.values():
                store.log_price(snapshot)
                store.log_candles(
                    asset=snapshot.asset.value,
                    candles=demo.recent_candles(snapshot.asset, now=now),
                    source=snapshot.source,
                    observed_at=now,
                )
            markets = demo.markets(now=now)
            orderbooks = {
                token_id: demo.orderbook(token_id)
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
            }
            store.replace_collected_market_data(now, markets, orderbooks, source_name="mock:demo")
            store.set_state("last_collection_mode", "demo", now)
        markets = store.collected_markets(mock_only=True, source_filter="demo")
        return current_prices, _StoredCandleSource(store, "mock:demo:spot:", source_filter="demo"), markets, _StoredOrderBookSource(store, source_filter="demo"), demo

    exchange = CoinbaseCollector(config.coinbase_base_url)
    current_prices = {snapshot.asset.value: snapshot for snapshot in exchange.collect_prices()}
    for snapshot in current_prices.values():
        store.log_price(snapshot)
        store.log_candles(
            asset=snapshot.asset.value,
            candles=exchange.recent_candles(snapshot.asset, granularity=60),
            source=snapshot.source,
            observed_at=now,
        )
    polymarket = PolymarketPublicCollector(config.gamma_base_url, config.clob_base_url)
    markets = polymarket.discover_updown_markets(config.max_market_duration_minutes)
    return current_prices, exchange, markets, polymarket, None


def _load_replay_context(
    store: SQLiteStore,
    config: AgentConfig,
    source_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
    active_only: bool = False,
    min_seconds_to_expiry: int | None = None,
    max_seconds_to_expiry: int | None = None,
):
    if source_filter == "demo":
        current_prices = store.latest_prices(source_prefix="mock:demo:spot:", source_filter="demo", since=since, until=until, session_id=session_id)
        candle_prefix = "mock:demo:spot:"
    elif source_filter == "public":
        current_prices = store.latest_prices(source_filter="public", since=since, until=until, session_id=session_id)
        candle_prefix = None
    else:
        current_prices = store.latest_prices(source_filter=source_filter, since=since, until=until, session_id=session_id)
        candle_prefix = None
    selected = select_market_snapshots(
        store,
        config,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        active_only=active_only,
        min_seconds_to_expiry=min_seconds_to_expiry,
        max_seconds_to_expiry=max_seconds_to_expiry,
    )
    markets = [
        Market(
            market_id=item.market.market_id,
            slug=item.market.slug,
            title=item.market.title,
            asset=item.market.asset,
            window=item.market.window,
            up_token_id=item.market.up_token_id,
            down_token_id=item.market.down_token_id,
            source_url=item.market.source_url,
            is_mock=item.market.is_mock,
            observed_at=item.observed_at,
            latest_observed_at=item.market.latest_observed_at,
        )
        for item in selected
    ]
    markets.sort(key=lambda market: (market.observed_at or market.window.start, market.window.end, market.slug))
    settlement_prices = _stored_settlement_prices(store, source_filter=source_filter, since=since, until=until, session_id=session_id)
    actual_sources = [str(row["source_name"]) for row in store.raw_snapshot_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)]
    return (
        current_prices,
        _StoredCandleSource(store, candle_prefix, source_filter=source_filter, since=since, until=until, session_id=session_id),
        markets,
        _StoredOrderBookSource(store, source_filter=source_filter, since=since, until=until, session_id=session_id),
        settlement_prices,
        sorted(set(actual_sources)),
    )


def _stored_settlement_prices(
    store: SQLiteStore,
    source_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
):
    clauses = ["snapshot_type = 'settlement_price'", "status = 'ok'"]
    params = []
    if source_filter == "demo":
        clauses.append("source_name LIKE ?")
        params.append("mock:%")
    elif source_filter == "public":
        clauses.append("source_name NOT LIKE ?")
        params.append("mock:%")
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if since is not None:
        clauses.append("observed_at >= ?")
        params.append(since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    if until is not None:
        clauses.append("observed_at <= ?")
        params.append(until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    rows = store.rows(
        f"""
        SELECT asset, payload_json, observed_at, source_name
        FROM raw_snapshots
        WHERE {' AND '.join(clauses)}
        ORDER BY observed_at DESC, id DESC
        """,
        tuple(params),
    )
    import json

    output = {}
    for row in rows:
        asset = str(row["asset"])
        if asset in output:
            continue
        payload = json.loads(str(row["payload_json"]))
        output[asset] = type("_ReplayPrice", (), {
            "asset": asset,
            "price": float(payload["price"]),
            "timestamp": row["observed_at"],
            "source": row["source_name"],
        })()
    return output


def _log_market_raw_snapshots(
    store: SQLiteStore,
    now: datetime,
    session_id: str | None,
    source_name: str,
    markets,
    orderbooks,
    status: str,
) -> None:
    store.log_raw_snapshot(
        now,
        source_name,
        None,
        "market_metadata",
        {
            "markets": [
                {
                    "market_id": market.market_id,
                    "slug": market.slug,
                    "title": market.title,
                    "asset": market.asset.value,
                    "window_start": market.window.start.isoformat(),
                    "window_end": market.window.end.isoformat(),
                    "is_mock": market.is_mock,
                }
                for market in markets
            ]
        },
        status=status,
        session_id=session_id,
    )
    for token_id, book in orderbooks.items():
        store.log_raw_snapshot(
            now,
            source_name,
            None,
            "orderbook",
            {
                "token_id": token_id,
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
                "spread": book.spread,
                "last_trade_price": book.last_trade_price,
            },
            status=status,
            session_id=session_id,
        )


def _log_discovery_raw_snapshots(
    store: SQLiteStore,
    now: datetime,
    session_id: str | None,
    source_name: str,
    candidates,
    orderbooks,
) -> None:
    store.log_raw_snapshot(
        now,
        source_name,
        None,
        "market_metadata",
        {
            "markets": [
                {
                    "market_id": candidate.market_id,
                    "slug": candidate.slug,
                    "title": candidate.title,
                    "asset": candidate.asset_label,
                    "classification": candidate.classification.value,
                    "token_status": candidate.token_status,
                    "orderbook_status": candidate.orderbook_status,
                    "accepted": candidate.accepted,
                    "reason": candidate.reason,
                }
                for candidate in candidates
            ]
        },
        status="ok",
        session_id=session_id,
    )
    for candidate in candidates:
        if candidate.orderbook_status == "FOUND":
            for token_id in (candidate.up_token_id, candidate.down_token_id):
                if token_id not in orderbooks:
                    continue
                book = orderbooks[token_id]
                store.log_raw_snapshot(
                    now,
                    source_name,
                    candidate.asset_label if candidate.asset_label in {"BTC", "ETH"} else None,
                    "orderbook",
                    {
                        "market_slug": candidate.slug,
                        "token_id": token_id,
                        "best_bid": book.best_bid,
                        "best_ask": book.best_ask,
                        "spread": book.spread,
                        "last_trade_price": book.last_trade_price,
                    },
                    status="ok",
                    session_id=session_id,
                )
        elif candidate.orderbook_status.startswith("FAILED"):
            store.log_raw_snapshot(
                now,
                source_name,
                candidate.asset_label if candidate.asset_label in {"BTC", "ETH"} else None,
                "orderbook",
                {"market_slug": candidate.slug, "token_ids": [candidate.up_token_id, candidate.down_token_id]},
                status="failed",
                error_message=candidate.reason,
                session_id=session_id,
            )


def _signal_for(strategy: MomentumUpDownStrategy, candle_source, asset: Asset) -> Signal:
    candles = candle_source.recent_candles(asset)
    return strategy.signal(asset, candles)


def _observe_profile_settings(profile: str | None) -> dict[str, object]:
    if profile == "conservative-entry-30-70":
        return {
            "assets": (Asset.BTC,),
            "asset_filter": Asset.BTC,
            "max_market_duration_minutes": 5,
        }
    return {
        "assets": (Asset.BTC, Asset.ETH),
        "asset_filter": None,
        "max_market_duration_minutes": 60,
    }


def _collect_public_prices(exchange, assets: tuple[Asset, ...]) -> list[PriceSnapshot]:
    if hasattr(exchange, "latest_price"):
        return [exchange.latest_price(asset) for asset in assets]
    collected = exchange.collect_prices()
    wanted = {asset.value for asset in assets}
    return [snapshot for snapshot in collected if snapshot.asset.value in wanted]


def _signal_for_at(
    strategy: MomentumUpDownStrategy,
    store: SQLiteStore,
    asset: Asset,
    observed_at: datetime,
    source_filter: str | None,
    session_id: str | None,
) -> Signal:
    candles = store.recent_candles(
        asset.value,
        limit=5,
        source_filter=source_filter,
        until=observed_at,
        session_id=session_id,
    )
    return strategy.signal(asset, candles)


def _orderbook_for(source, market, signal: Signal) -> OrderBook | None:
    try:
        return source.orderbook(market.token_for(signal.direction))
    except HttpError:
        return None


def _replace_database_path(config: AgentConfig, db_path: str) -> AgentConfig:
    return AgentConfig(**{**config.__dict__, "database_path": Path(db_path)})


def _replace_demo_flag(config: AgentConfig, value: bool) -> AgentConfig:
    return AgentConfig(**{**config.__dict__, "use_demo_markets": value})


def _replace_strategy(config: AgentConfig, value: str) -> AgentConfig:
    return AgentConfig(**{**config.__dict__, "strategy": value})


def _replace_config_values(config: AgentConfig, **updates) -> AgentConfig:
    return AgentConfig(**{**config.__dict__, **updates})


def _replace_close_mode(config: AgentConfig, value: str) -> AgentConfig:
    return AgentConfig(**{**config.__dict__, "close_mode": value})


def _asset_filter(value: str) -> Asset | None:
    if value == "BTC":
        return Asset.BTC
    if value == "ETH":
        return Asset.ETH
    return None


def _observe_cycles(duration_minutes: float | None, interval_seconds: float, cycles: int | None) -> int:
    if cycles is not None:
        return max(0, cycles)
    if duration_minutes is None:
        return 1
    if interval_seconds <= 0:
        return 1
    total_seconds = max(0.0, duration_minutes * 60.0)
    return max(1, int((total_seconds + interval_seconds - 1) // interval_seconds))


def _config_notes(config: AgentConfig) -> str:
    return (
        f"min_edge={config.min_edge}; max_spread={config.max_spread}; fee_bps={config.fee_bps}; "
        f"slippage_bps={config.slippage_bps}; max_position_pct={config.max_position_pct}; "
        f"max_position_usd={config.max_position_usd}; max_trade_usd={config.max_trade_usd}; "
        f"max_total_exposure_usd={config.max_total_exposure_usd}; max_open_positions={config.max_open_positions}; "
        f"max_trades_per_market={config.max_trades_per_market}; max_trades_per_session={config.max_trades_per_session}; "
        f"session_loss_limit_usd={config.session_loss_limit_usd}; daily_loss_limit_usd={config.daily_loss_limit_usd}; "
        f"cooldown_after_loss_seconds={config.cooldown_after_loss_seconds}; "
        f"min_seconds_to_expiry={config.min_seconds_to_expiry if config.min_seconds_to_expiry is not None else 'none'}; "
        f"max_seconds_to_expiry={config.max_seconds_to_expiry if config.max_seconds_to_expiry is not None else 'none'}; "
        f"failed_fill_probability={config.failed_fill_probability}; "
        f"pair_cost_threshold={config.pair_cost_threshold}; "
        f"pair_cost_failed_second_leg_probability={config.pair_cost_failed_second_leg_probability}; "
        f"stuck_state_min_cycles={config.stuck_state_min_cycles}; "
        f"stuck_price_bucket_min={config.stuck_price_bucket_min}; "
        f"stuck_price_bucket_max={config.stuck_price_bucket_max}; "
        f"stuck_max_spread={config.stuck_max_spread}; "
        f"stuck_min_seconds_to_expiry={config.stuck_min_seconds_to_expiry}; "
        f"stuck_max_seconds_to_expiry={config.stuck_max_seconds_to_expiry}; "
        f"momentum_preset={config.momentum_preset or 'none'}; "
        f"reverse_signal={'true' if config.reverse_signal else 'false'}; "
        f"momentum_side_filter={config.momentum_side_filter or 'none'}; "
        f"momentum_asset_filter={config.momentum_asset_filter or 'none'}; "
        f"momentum_duration_filter={config.momentum_duration_filter or 'none'}; "
        f"momentum_min_entry_price={config.momentum_min_entry_price if config.momentum_min_entry_price is not None else 'none'}; "
        f"momentum_max_entry_price={config.momentum_max_entry_price if config.momentum_max_entry_price is not None else 'none'}; "
        f"close_mode={config.close_mode}; tiny_profile={'true' if config.tiny_profile else 'false'}"
    )


def _replace_tiny_profile(config: AgentConfig) -> AgentConfig:
    return _replace_config_values(
        config,
        max_trade_usd=1.0,
        max_total_exposure_usd=10.0,
        max_open_positions=5,
        max_trades_per_market=1,
        max_trades_per_session=100,
        session_loss_limit_usd=5.0,
        daily_loss_limit_usd=10.0,
        cooldown_after_loss_seconds=300,
        min_seconds_to_expiry=30,
        max_seconds_to_expiry=240,
        tiny_profile=True,
    )


def _apply_named_preset(config: AgentConfig, preset: str) -> AgentConfig:
    mapping = {
        "balanced-tiny": "balanced-tiny-momentum",
        "conservative-tiny": "conservative-tiny-momentum",
        "conservative-tiny-reverse": "conservative-tiny-reverse",
        "conservative-entry-30-70": "conservative-entry-30-70",
    }
    if preset in mapping:
        return _apply_momentum_preset(config, mapping[preset])
    return _apply_momentum_preset(config, preset)


def _apply_momentum_preset(config: AgentConfig, preset: str) -> AgentConfig:
    base = _replace_tiny_profile(config)
    if preset in {"balanced-tiny", "balanced-tiny-momentum"}:
        return _replace_config_values(
            base,
            momentum_preset="balanced-tiny",
            min_seconds_to_expiry=60,
            max_seconds_to_expiry=180,
            max_spread=min(base.max_spread, 0.05),
        )
    if preset in {"conservative-tiny", "conservative-tiny-momentum"}:
        return _replace_config_values(
            base,
            momentum_preset="conservative-tiny",
            momentum_asset_filter="BTC",
            momentum_duration_filter="5m",
            momentum_min_entry_price=0.05,
            momentum_max_entry_price=0.85,
            min_edge=max(base.min_edge, 0.03),
            max_spread=min(base.max_spread, 0.02),
            max_total_exposure_usd=min(base.max_total_exposure_usd, 5.0),
            min_seconds_to_expiry=60,
            max_seconds_to_expiry=180,
        )
    if preset == "conservative-entry-30-70":
        return _replace_config_values(
            base,
            momentum_preset="conservative-entry-30-70",
            momentum_asset_filter="BTC",
            momentum_duration_filter="5m",
            momentum_min_entry_price=0.30,
            momentum_max_entry_price=0.70,
            min_edge=max(base.min_edge, 0.03),
            max_spread=min(base.max_spread, 0.02),
            max_total_exposure_usd=min(base.max_total_exposure_usd, 5.0),
            min_seconds_to_expiry=60,
            max_seconds_to_expiry=180,
        )
    if preset == "conservative-tiny-reverse":
        conservative = _apply_momentum_preset(config, "conservative-tiny-momentum")
        return _replace_config_values(
            conservative,
            momentum_preset="conservative-tiny-reverse",
            reverse_signal=True,
        )
    raise ValueError(f"unsupported momentum preset: {preset}")


def _conservative_preset_summary(config: AgentConfig) -> str:
    return (
        f"momentum_preset={config.momentum_preset or 'none'} | "
        f"reverse_signal={'true' if config.reverse_signal else 'false'} | "
        f"tiny_profile={'true' if config.tiny_profile else 'false'} | "
        f"momentum_asset_filter={config.momentum_asset_filter or 'none'} | "
        f"momentum_duration_filter={config.momentum_duration_filter or 'none'} | "
        f"min_edge={config.min_edge:.2f} | "
        f"max_spread={config.max_spread:.2f} | "
        f"momentum_min_entry_price={config.momentum_min_entry_price if config.momentum_min_entry_price is not None else 'none'} | "
        f"momentum_max_entry_price={config.momentum_max_entry_price if config.momentum_max_entry_price is not None else 'none'} | "
        f"max_trade_usd={config.max_trade_usd:.2f} | "
        f"max_total_exposure_usd={config.max_total_exposure_usd:.2f} | "
        f"min_seconds_to_expiry={config.min_seconds_to_expiry if config.min_seconds_to_expiry is not None else 'none'} | "
        f"max_seconds_to_expiry={config.max_seconds_to_expiry if config.max_seconds_to_expiry is not None else 'none'}"
    )


def _effective_expiry_filters(config: AgentConfig, args) -> tuple[int | None, int | None]:
    min_seconds = getattr(args, "min_seconds_to_expiry", None)
    max_seconds = getattr(args, "max_seconds_to_expiry", None)
    if min_seconds is None:
        min_seconds = config.min_seconds_to_expiry
    if max_seconds is None:
        max_seconds = config.max_seconds_to_expiry
    return min_seconds, max_seconds


def _sweep_configs(config: AgentConfig, strategy: str) -> list[AgentConfig]:
    if strategy == "momentum":
        return [
            _replace_config_values(config, strategy="momentum", min_edge=min_edge, max_spread=max_spread)
            for min_edge in (0.00, 0.01, 0.02, 0.03)
            for max_spread in (0.02, 0.05, 0.10)
        ]
    return [
        _replace_config_values(
            config,
            strategy="pair-cost",
            pair_cost_threshold=threshold,
            max_spread=max_spread,
            pair_cost_failed_second_leg_probability=failed_leg_probability,
        )
        for threshold in (0.98, 0.99, 1.00)
        for max_spread in (0.02, 0.05, 0.10)
        for failed_leg_probability in (0.0, 0.1)
    ]


def _sweep_label(config: AgentConfig) -> str:
    if config.strategy == "pair-cost":
        return (
            f"pair_cost_threshold={config.pair_cost_threshold:.2f}"
            f"; max_spread={config.max_spread:.2f}"
            f"; failed_second_leg_probability={config.pair_cost_failed_second_leg_probability:.2f}"
        )
    return f"min_edge={config.min_edge:.2f}; max_spread={config.max_spread:.2f}"


def _fmt_money(value) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):.2f}"


def _clean_source_filter(value: str | None) -> str | None:
    if value in (None, "", "all"):
        return None
    return value


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_until(value: str | None) -> datetime | None:
    return _parse_since(value)


def _resolved_time_filters(store: SQLiteStore, args) -> tuple[str | None, datetime | None, datetime | None]:
    session_id = getattr(args, "session_id", None)
    since = _parse_since(getattr(args, "since", None))
    until = _parse_until(getattr(args, "until", None))
    if session_id and (since is not None or until is not None):
        _, session_since, session_until = _session_bounds(store, session_id)
        since = max(value for value in (since, session_since) if value is not None)
        if until is None:
            until = session_until
        elif session_until is not None:
            until = min(until, session_until)
    return session_id, since, until


def _session_row(store: SQLiteStore, args):
    session_id = getattr(args, "session_id", None)
    latest = bool(getattr(args, "latest", False))
    if session_id:
        return store.research_session_by_id(session_id)
    if latest:
        return store.latest_research_session()
    return store.latest_research_session()


def _session_bounds(store: SQLiteStore, session_id: str) -> tuple[str, datetime, datetime | None]:
    row = store.research_session_by_id(session_id)
    if row is None:
        raise ValueError(f"unknown session_id: {session_id}")
    since = _parse_since(str(row["started_at"]))
    until = _parse_until(str(row["ended_at"])) if row["ended_at"] else None
    if until is None:
        session_rows = store.raw_snapshot_rows(session_id=session_id)
        if session_rows:
            until = _parse_until(str(session_rows[-1]["observed_at"]))
    return session_id, since, until


def _recommended_next_command(verdict: str, session_id: str) -> str:
    if verdict == "READY_FOR_PUBLIC_REPLAY":
        return f"python -m src.main research-report --session-id {session_id}"
    return f"python -m src.main observe --cycles 10 --interval-seconds 15"


def _format_sources(values) -> str:
    items = [str(value) for value in values]
    return ", ".join(items) if items else "none"


def _replay_end_time(
    store: SQLiteStore,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> datetime | None:
    rows = store.raw_snapshot_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
    if not rows:
        return None
    return _parse_until(str(rows[-1]["observed_at"]))


def _trade_time(value) -> datetime | None:
    if not value:
        return None
    return _parse_until(str(value))


def _report_seconds_bucket(trade_row) -> str:
    opened = _trade_time(trade_row["opened_at"])
    expiry = _trade_time(trade_row["window_end"])
    if opened is None or expiry is None:
        return "unknown"
    seconds = max(0.0, (expiry - opened).total_seconds())
    if seconds < 60:
        return "30-60"
    if seconds < 120:
        return "60-120"
    if seconds < 180:
        return "120-180"
    if seconds < 240:
        return "180-240"
    return "240+"


class _StoredCandleSource:
    def __init__(
        self,
        store: SQLiteStore,
        source_prefix: str | None,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ):
        self.store = store
        self.source_prefix = source_prefix
        self.source_filter = source_filter
        self.since = since
        self.until = until
        self.session_id = session_id

    def recent_candles(self, asset: Asset, granularity: int = 60):
        prefix = self.source_prefix or None
        return self.store.recent_candles(
            asset.value,
            limit=5,
            source_prefix=prefix,
            source_filter=self.source_filter,
            since=self.since,
            until=self.until,
            session_id=self.session_id,
        )


class _StoredOrderBookSource:
    def __init__(
        self,
        store: SQLiteStore,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ):
        self.store = store
        self.source_filter = source_filter
        self.since = since
        self.until = until
        self.session_id = session_id

    def orderbook(self, token_id: str) -> OrderBook | None:
        return self.store.collected_orderbook(
            token_id,
            source_filter=self.source_filter,
            since=self.since,
            until=self.until,
            session_id=self.session_id,
        )


def _merge_close_counts(target: dict[str, int], update: dict[str, int]) -> None:
    for key, value in update.items():
        target[key] = target.get(key, 0) + value


if __name__ == "__main__":
    raise SystemExit(main())
