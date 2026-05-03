from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.collectors.exchange import CoinbaseCollector
from src.collectors.mock_markets import MockMarketSource
from src.collectors.polymarket import PolymarketPublicCollector
from src.config import AgentConfig, load_config
from src.http_client import HttpError
from src.models import Asset, OpportunityDecision, OrderBook, Signal
from src.reports.ledger import build_trade_ledger
from src.reports.summary import build_report
from src.safety import SafetyError, enforce_paper_only
from src.simulator.engine import PaperTradingEngine
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
    subcommands.add_parser("report", help="Summarize fake trading results")
    subcommands.add_parser("trades", help="Show simulated trades and skipped opportunities")
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
            return report(config)
        if args.command == "trades":
            return trades(config)
    except SafetyError as exc:
        print(f"Safety error: {exc}", file=sys.stderr)
        return 2
    except HttpError as exc:
        print(f"Public data error: {exc}", file=sys.stderr)
        if args.command in {"collect", "run-paper"}:
            print("Try demo mode: python -m src.main collect --demo", file=sys.stderr)
        return 1
    return 0


def collect(config: AgentConfig) -> int:
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
                store.log_price(snapshot)
                store.log_candles(
                    asset=snapshot.asset.value,
                    candles=demo.recent_candles(snapshot.asset, now=now),
                    source=snapshot.source,
                    observed_at=now,
                )
            store.replace_collected_market_data(now, markets, orderbooks)
            store.set_state("last_collection_mode", "demo", now)
            print("Collected deterministic demo dataset.")
            print(f"Inserted mock prices: {len(prices)}")
            print(f"Inserted mock markets: {len(markets)}")
            print(f"Inserted mock orderbooks: {len(orderbooks)}")
            return 0

        exchange = CoinbaseCollector(config.coinbase_base_url)
        prices = exchange.collect_prices()
        for snapshot in prices:
            store.log_price(snapshot)
            store.log_candles(
                asset=snapshot.asset.value,
                candles=exchange.recent_candles(snapshot.asset, granularity=60),
                source=snapshot.source,
                observed_at=now,
            )
            print(f"{snapshot.asset.value} {snapshot.price:.2f} from {snapshot.source}")

        polymarket = PolymarketPublicCollector(config.gamma_base_url, config.clob_base_url)
        markets = polymarket.discover_updown_markets(config.max_market_duration_minutes)
        if not markets:
            print("No active short-duration BTC/ETH UP-DOWN markets discovered from public endpoints.")
            print("Use collect --demo or USE_MOCK_DATA=true for offline demo data.")
        else:
            orderbooks = {
                token_id: polymarket.orderbook(token_id)
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
            }
            store.replace_collected_market_data(now, markets, orderbooks)
            store.set_state("last_collection_mode", "public", now)
            print(f"Discovered {len(markets)} candidate Polymarket UP/DOWN markets.")
            for market in markets[:10]:
                print(f"{market.asset.value} {market.slug} ends {market.window.end.isoformat()}")
    finally:
        store.close()
    return 0


def run_paper(config: AgentConfig) -> int:
    now = datetime.now(timezone.utc)
    store = SQLiteStore(config.database_path)
    try:
        engine = PaperTradingEngine(config, store)
        mode = _effective_data_mode(config, store)
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

        print(f"Paper cycle complete. Accepted fake trades: {accepted}; skipped: {skipped}; closed: {closed}.")
        print(f"Fake balance: ${store.current_balance(default=config.starting_balance):.2f}")
    finally:
        store.close()
    return 0


def report(config: AgentConfig) -> int:
    store = SQLiteStore(config.database_path)
    try:
        print(build_report(store, config.starting_balance).as_text())
    finally:
        store.close()
    return 0


def trades(config: AgentConfig) -> int:
    store = SQLiteStore(config.database_path)
    try:
        print(build_trade_ledger(store))
    finally:
        store.close()
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
            engine.store.log_opportunity(now, decision)
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
            store.replace_collected_market_data(now, markets, orderbooks)
            store.set_state("last_collection_mode", "demo", now)
        markets = store.collected_markets(mock_only=True)
        return current_prices, _StoredCandleSource(store, "mock:demo:spot:"), markets, _StoredOrderBookSource(store), demo

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


class _StoredCandleSource:
    def __init__(self, store: SQLiteStore, source_prefix: str):
        self.store = store
        self.source_prefix = source_prefix

    def recent_candles(self, asset: Asset, granularity: int = 60):
        return self.store.recent_candles(asset.value, limit=5, source_prefix=self.source_prefix)


class _StoredOrderBookSource:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def orderbook(self, token_id: str) -> OrderBook | None:
        return self.store.collected_orderbook(token_id)


if __name__ == "__main__":
    raise SystemExit(main())
