from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.collectors.exchange import CoinbaseCollector, FallbackExchangeCollector, KrakenCollector
from src.collectors.mock_markets import MockMarketSource
from src.collectors.polymarket import PolymarketPublicCollector
from src.config import AgentConfig, load_config
from src.http_client import HttpError
from src.models import Asset, OpportunityDecision, OrderBook, Signal
from src.reports.backtest import build_backtest_report
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
    replay_parser = subcommands.add_parser("replay", help="Replay stored snapshots without external APIs")
    replay_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost"),
        default=None,
        help="Paper strategy to replay.",
    )
    backtest_parser = subcommands.add_parser("backtest-report", help="Summarize stored snapshots and replay output")
    backtest_parser.add_argument(
        "--strategy",
        choices=("momentum", "pair-cost"),
        default=None,
        help="Strategy label to show in the report.",
    )
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
        if args.command == "replay":
            return replay(config)
        if args.command == "backtest-report":
            return backtest_report(config)
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
                store.log_raw_snapshot(
                    now,
                    snapshot.source,
                    snapshot.asset.value,
                    "exchange_price",
                    {"price": snapshot.price, "source": snapshot.source},
                    status="ok",
                )
                store.log_candles(
                    asset=snapshot.asset.value,
                    candles=demo.recent_candles(snapshot.asset, now=now),
                    source=snapshot.source,
                    observed_at=now,
                )
            store.replace_collected_market_data(now, markets, orderbooks)
            _log_market_raw_snapshots(store, now, "mock:demo", markets, orderbooks, "ok")
            for settlement in demo.settlement_prices(now=now):
                store.log_raw_snapshot(
                    settlement.timestamp,
                    settlement.source,
                    settlement.asset.value,
                    "settlement_price",
                    {"price": settlement.price, "source": settlement.source},
                    status="ok",
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
            )
            raise HttpError(f"public exchange collection failed: {exc}") from exc
        for snapshot in prices:
            store.log_price(snapshot)
            store.log_raw_snapshot(
                now,
                snapshot.source,
                snapshot.asset.value,
                "exchange_price",
                {"price": snapshot.price, "source": snapshot.source},
                status="ok",
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
                )
                raise HttpError(f"public exchange candle collection failed: {exc}") from exc
            store.log_candles(
                asset=snapshot.asset.value,
                candles=candles,
                source=snapshot.source,
                observed_at=now,
            )
            print(f"{snapshot.asset.value} {snapshot.price:.2f} from {snapshot.source}")

        polymarket = PolymarketPublicCollector(config.gamma_base_url, config.clob_base_url)
        try:
            markets = polymarket.discover_updown_markets(config.max_market_duration_minutes)
        except Exception as exc:
            store.log_raw_snapshot(
                now,
                "polymarket-public",
                None,
                "collection_status",
                {},
                status="failed",
                error_message=str(exc),
            )
            raise
        if not markets:
            print("No active short-duration BTC/ETH UP-DOWN markets discovered from public endpoints.")
            print("Use collect --demo or USE_MOCK_DATA=true for offline demo data.")
            store.log_raw_snapshot(
                now,
                "polymarket-public",
                None,
                "market_metadata",
                {"markets": []},
                status="ok",
            )
        else:
            orderbooks = {}
            complete_markets = []
            for market in markets:
                try:
                    up_book = polymarket.orderbook(market.up_token_id)
                    down_book = polymarket.orderbook(market.down_token_id)
                except Exception as exc:
                    store.log_raw_snapshot(
                        now,
                        "polymarket-public",
                        market.asset.value,
                        "orderbook",
                        {"market_slug": market.slug},
                        status="failed",
                        error_message=str(exc),
                    )
                    continue
                orderbooks[market.up_token_id] = up_book
                orderbooks[market.down_token_id] = down_book
                complete_markets.append(market)
            store.replace_collected_market_data(now, complete_markets, orderbooks)
            _log_market_raw_snapshots(store, now, "polymarket-public", markets, orderbooks, "ok")
            store.set_state("last_collection_mode", "public", now)
            print(f"Discovered {len(markets)} candidate Polymarket UP/DOWN markets.")
            print(f"Stored complete orderbooks for {len(complete_markets)} markets.")
            for market in complete_markets[:10]:
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


def replay(config: AgentConfig) -> int:
    now = datetime.now(timezone.utc)
    store = SQLiteStore(config.database_path)
    try:
        engine = PaperTradingEngine(config, store)
        current_prices, candle_source, markets, orderbook_source, settlement_prices = _load_replay_context(store)
        if not current_prices or not markets:
            print("No stored snapshots available for replay. Run collect --demo or collect first.")
            return 1
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
        if settlement_prices:
            settlement_now = max(market.window.end for market in markets)
            closed = engine.close_expired(settlement_prices, now=settlement_now)
        store.set_state("last_replay_strategy", config.strategy, now)
        print(
            f"Replay complete. Strategy: {config.strategy}; accepted fake trades: {accepted}; "
            f"skipped: {skipped}; closed: {closed}."
        )
    finally:
        store.close()
    return 0


def backtest_report(config: AgentConfig) -> int:
    store = SQLiteStore(config.database_path)
    try:
        strategy = store.get_state("last_replay_strategy") or config.strategy
        print(build_backtest_report(store, config.starting_balance, strategy).as_text())
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


def _load_replay_context(store: SQLiteStore):
    current_prices = store.latest_prices(source_prefix="mock:demo:spot:")
    if not current_prices:
        current_prices = store.latest_prices()
    markets = store.collected_markets()
    settlement_prices = _stored_settlement_prices(store)
    return (
        current_prices,
        _StoredCandleSource(store, ""),
        markets,
        _StoredOrderBookSource(store),
        settlement_prices,
    )


def _stored_settlement_prices(store: SQLiteStore):
    rows = store.rows(
        """
        SELECT asset, payload_json, observed_at, source_name
        FROM raw_snapshots
        WHERE snapshot_type = 'settlement_price' AND status = 'ok'
        ORDER BY observed_at DESC, id DESC
        """
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


class _StoredCandleSource:
    def __init__(self, store: SQLiteStore, source_prefix: str):
        self.store = store
        self.source_prefix = source_prefix

    def recent_candles(self, asset: Asset, granularity: int = 60):
        prefix = self.source_prefix or None
        return self.store.recent_candles(asset.value, limit=5, source_prefix=prefix)


class _StoredOrderBookSource:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def orderbook(self, token_id: str) -> OrderBook | None:
        return self.store.collected_orderbook(token_id)


if __name__ == "__main__":
    raise SystemExit(main())
