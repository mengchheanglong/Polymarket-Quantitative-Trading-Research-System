from __future__ import annotations

import argparse
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from src.collectors.exchange import CoinbaseCollector, FallbackExchangeCollector, KrakenCollector
from src.collectors.mock_markets import MockMarketSource
from src.collectors.polymarket import PolymarketPublicCollector, _candidate_to_market
from src.config import AgentConfig, load_config
from src.http_client import HttpError
from src.models import Asset, OpportunityDecision, OrderBook, Signal
from src.reports.backtest import build_backtest_report
from src.reports.compare import build_strategy_comparison
from src.reports.dataset import build_dataset_summary
from src.reports.diagnostics import build_diagnostics
from src.reports.ledger import build_trade_ledger
from src.reports.summary import build_report
from src.reports.sweep import SweepRow, build_sweep_report
from src.safety import SafetyError, enforce_paper_only
from src.simulator.engine import PaperTradingEngine
from src.storage.export import export_csv
from src.storage.sqlite import SQLiteStore
from src.strategies.pair_cost_arbitrage import PairCostArbitrageStrategy
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
        choices=("momentum", "pair-cost"),
        default=None,
        help="Paper strategy to simulate.",
    )
    run_parser.add_argument("--new-run", action="store_true", help="Start a fresh run. This is the default.")
    report_parser = subcommands.add_parser("report", help="Summarize fake trading results")
    report_parser.add_argument("--latest", action="store_true", help="Report only the latest run.")
    report_parser.add_argument("--all", action="store_true", help="Report all runs combined.")
    report_parser.add_argument("--run-id", help="Report a specific run.")
    report_parser.add_argument("--strategy", choices=("momentum", "pair-cost"), help="Report runs for a strategy.")
    trades_parser = subcommands.add_parser("trades", help="Show simulated trades and skipped opportunities")
    trades_parser.add_argument("--run-id", help="Show ledger for a specific run.")
    trades_parser.add_argument("--all", action="store_true", help="Show ledger for all runs.")
    replay_parser = subcommands.add_parser("replay", help="Replay stored snapshots without external APIs")
    replay_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost"),
        default=None,
        help="Paper strategy to replay.",
    )
    replay_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    replay_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    replay_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    replay_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    replay_parser.add_argument("--new-run", action="store_true", help="Start a fresh run. This is the default.")
    backtest_parser = subcommands.add_parser("backtest-report", help="Summarize stored snapshots and replay output")
    backtest_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost"),
        default=None,
        help="Strategy label to show in the report.",
    )
    backtest_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    backtest_parser.add_argument("--since", help="Only summarize snapshots at or after this UTC ISO timestamp.")
    backtest_parser.add_argument("--until", help="Only summarize snapshots at or before this UTC ISO timestamp.")
    backtest_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    subcommands.add_parser("runs", help="List experiment runs")
    compare_parser = subcommands.add_parser("compare", help="Compare stored strategies by run metadata")
    compare_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    compare_parser.add_argument("--session-id", help="Filter runs for a research session.")
    diagnostics_parser = subcommands.add_parser("diagnostics", help="Explain accepted/skipped paper opportunities")
    diagnostics_parser.add_argument("--run-id", help="Inspect a specific run.")
    diagnostics_parser.add_argument("--strategy", choices=("momentum", "pair-cost"), help="Filter by strategy.")
    diagnostics_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    diagnostics_parser.add_argument("--session-id", help="Filter runs for a research session.")
    sweep_parser = subcommands.add_parser("sweep", help="Run a paper-only threshold sweep on stored snapshots")
    sweep_parser.add_argument("--strategy", choices=("momentum", "pair-cost"), required=True)
    sweep_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    sweep_parser.add_argument("--since", help="Only use stored snapshots at or after this UTC ISO timestamp.")
    sweep_parser.add_argument("--until", help="Only use stored snapshots at or before this UTC ISO timestamp.")
    sweep_parser.add_argument("--session-id", help="Use the stored time window for a research session.")
    reset_parser = subcommands.add_parser("reset", help="Delete research data safely")
    reset_parser.add_argument("--paper-results", action="store_true", help="Delete paper runs, trades, opportunities, and equity only.")
    reset_parser.add_argument("--all", action="store_true", help="Delete paper results and collected snapshot data.")
    observe_parser = subcommands.add_parser("observe", help="Collect public snapshots repeatedly without trading")
    observe_parser.add_argument("--duration-minutes", type=float, default=None, help="Maximum observe duration.")
    observe_parser.add_argument("--interval-seconds", type=float, default=15.0, help="Seconds between cycles.")
    observe_parser.add_argument("--cycles", type=int, default=None, help="Maximum cycles, useful for tests.")
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
    discover_parser = subcommands.add_parser("discover-markets", help="Probe public Polymarket market discovery")
    discover_parser.add_argument("--asset", choices=("BTC", "ETH", "all"), default="all")
    export_parser = subcommands.add_parser("export", help="Export local research data")
    export_parser.add_argument("--format", choices=("csv",), default="csv")
    export_parser.add_argument("--out", default="exports")
    export_parser.add_argument("--source", choices=("demo", "public", "all"), default=None)
    export_parser.add_argument("--since", help="Only export raw snapshots at or after this UTC ISO timestamp.")
    export_parser.add_argument("--until", help="Only export raw snapshots at or before this UTC ISO timestamp.")
    export_parser.add_argument("--session-id", help="Export only data tied to a research session when possible.")
    args = parser.parse_args(argv)

    config = load_config()
    if args.db:
        config = _replace_database_path(config, args.db)
    if getattr(args, "demo", False):
        config = _replace_demo_flag(config, True)
    if getattr(args, "strategy", None):
        config = _replace_strategy(config, args.strategy)

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
        if args.command == "discover-markets":
            return discover_markets(config, args)
        if args.command == "export":
            return export_data(config, args)
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

        exchange = FallbackExchangeCollector(
            [
                CoinbaseCollector(config.coinbase_base_url),
                KrakenCollector(config.kraken_base_url),
            ]
        )
        try:
            prices = exchange.collect_prices()
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
                max_duration_minutes=config.max_market_duration_minutes,
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
    cycles = _observe_cycles(args.duration_minutes, args.interval_seconds, args.cycles)
    successes = 0
    failures = 0
    store = SQLiteStore(config.database_path)
    started_at = datetime.now(timezone.utc)
    session_id = store.start_research_session(
        started_at,
        interval_seconds=args.interval_seconds,
        cycles_requested=args.cycles if args.cycles is not None else cycles,
        notes=f"duration_minutes={args.duration_minutes or 'none'}; interval_seconds={args.interval_seconds}",
    )
    print(f"Observe session started: {session_id}")
    interrupted = False
    try:
        for index in range(cycles):
            print(f"Observe cycle {index + 1}/{cycles}")
            try:
                collect(config, session_id=session_id)
                successes += 1
                snapshot_count = len(store.raw_snapshot_rows(session_id=session_id))
                print(f"Observe cycle {index + 1} complete. Session snapshots: {snapshot_count}; failures: {failures}.")
            except HttpError as exc:
                failures += 1
                print(f"Observe cycle {index + 1} failed: {exc}", file=sys.stderr)
                print("Continuing observe loop. Use collect --demo for offline data.", file=sys.stderr)
            store.update_research_session_progress(
                session_id,
                cycles_completed=index + 1,
                successful_cycles=successes,
                failed_cycles=failures,
            )
            if index < cycles - 1 and args.interval_seconds > 0:
                time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        interrupted = True
        print("\nObserve interrupted. Finalizing partial session...")
    finally:
        ended_at = datetime.now(timezone.utc)
        store.finish_research_session(session_id, ended_at)
        session = store.research_session_by_id(session_id)
        store.close()

    print(f"Observe complete. Successful cycles: {successes}; failed cycles: {failures}.")
    if session is not None:
        print(
            f"Session summary: session_id={session_id} | cycles_completed={session['cycles_completed']} | "
            f"snapshots={session['snapshot_count']} | failed_snapshots={session['failed_snapshot_count']}"
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
        summary = store.dataset_summary(source_filter="public", since=since, until=until, session_id=session_id)
        quality = store.data_quality_metrics(source_filter="public", since=since, until=until, session_id=session_id)
        readiness_result = store.readiness(source_filter="public", since=since, until=until, session_id=session_id)
        markets = store.market_audit_rows(source_filter="public", since=since, until=until, session_id=session_id)
        found = sum(1 for row in markets if row["accepted"])
        orderbooks = sum(1 for row in markets if row["orderbook_status"] == "FOUND")
        assets = summary["assets_seen"]
        print("Research session report")
        print(f"Session ID: {session_id}")
        print(f"Started: {session['started_at']}")
        print(f"Ended: {session['ended_at'] or 'OPEN'}")
        print(f"Duration seconds: {session['duration_seconds'] or 0}")
        print(f"Interval seconds: {session['interval_seconds']}")
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
        print(f"Data quality: failed={quality['failed_collection_attempts']}, stale={quality['stale_snapshots']}, missing_orderbooks={quality['missing_orderbooks']}, missing_prices={quality['missing_prices']}")
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
        print(build_strategy_comparison(store, source_filter="public", session_id=session_id))
        print(build_diagnostics(store, strategy=None, source_filter="public", session_id=session_id))
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
        outcome = _simulate_replay(
            config,
            source_filter=source_filter,
            since=since,
            until=until,
            session_id=session_id,
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
            )
        )
    finally:
        store.close()
    return 0


def diagnostics(config: AgentConfig, args) -> int:
    store = SQLiteStore(config.database_path)
    try:
        print(
            build_diagnostics(
                store,
                run_id=getattr(args, "run_id", None),
                strategy=getattr(args, "strategy", None),
                source_filter=_clean_source_filter(getattr(args, "source", None)),
                session_id=getattr(args, "session_id", None),
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
        paths = export_csv(
            store,
            args.out,
            source_filter=_clean_source_filter(getattr(args, "source", None)),
            since=since,
            until=until,
            session_id=session_id,
        )
        print("Export complete.")
        for path in paths:
            print(str(path))
    finally:
        store.close()
    return 0


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


def _simulate_replay(
    config: AgentConfig,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
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

    run_id = result_store.start_run(
        strategy=config.strategy,
        mode=mode,
        data_source=source_filter or "all",
        starting_balance=config.starting_balance,
        now=now,
        notes=(
            f"{_config_notes(config)}; source_filter={source_filter or 'all'}; "
            f"since={since_label or 'none'}; until={until.isoformat() if until else 'none'}"
        ),
        session_id=session_id,
    )
    engine = PaperTradingEngine(config, result_store, run_id=run_id)
    engine.record_equity(now)
    if config.strategy == "pair-cost":
        accepted, skipped = _run_pair_cost(config, engine, markets, orderbook_source, current_prices, now)
    else:
        accepted, skipped = _run_momentum(
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
    source_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session_id: str | None = None,
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
    markets = store.collected_markets(source_filter=source_filter, since=since, until=until, session_id=session_id)
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
        f"max_position_usd={config.max_position_usd}; failed_fill_probability={config.failed_fill_probability}; "
        f"pair_cost_threshold={config.pair_cost_threshold}; "
        f"pair_cost_failed_second_leg_probability={config.pair_cost_failed_second_leg_probability}"
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
