from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models import Candle, Market, OpportunityDecision, OrderBook, OrderLevel, PriceSnapshot, TimingWindow


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    asset TEXT NOT NULL,
    price REAL NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    observed_at TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    probability REAL NOT NULL,
    edge REAL NOT NULL,
    market_price REAL,
    spread REAL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    is_mock INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    market_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    window_end TEXT NOT NULL,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    notional REAL NOT NULL,
    entry_fee REAL NOT NULL,
    slippage_cost REAL NOT NULL,
    total_cost REAL NOT NULL,
    entry_underlying_price REAL NOT NULL,
    exit_underlying_price REAL,
    exit_price REAL,
    exit_fee REAL,
    pnl REAL,
    result TEXT,
    status TEXT NOT NULL,
    is_mock INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bankroll (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    observed_at TEXT NOT NULL,
    balance REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    asset TEXT NOT NULL,
    candle_start TEXT NOT NULL,
    low REAL NOT NULL,
    high REAL NOT NULL,
    open REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collected_markets (
    market_slug TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    title TEXT NOT NULL,
    asset TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    is_mock INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collected_orderbooks (
    token_id TEXT PRIMARY KEY,
    market_slug TEXT NOT NULL,
    bid_price REAL,
    bid_size REAL,
    ask_price REAL,
    ask_size REAL,
    last_trade_price REAL,
    is_mock INTEGER NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    observed_at TEXT NOT NULL,
    cash_balance REAL NOT NULL,
    open_position_value REAL NOT NULL,
    total_equity REAL NOT NULL,
    position_exposure REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    asset TEXT,
    snapshot_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL,
    data_source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    starting_balance REAL NOT NULL,
    ending_balance REAL,
    realized_pnl REAL,
    max_equity_drawdown REAL,
    max_position_exposure REAL,
    accepted_trade_count INTEGER DEFAULT 0,
    skipped_opportunity_count INTEGER DEFAULT 0,
    notes TEXT
);
"""


class SQLiteStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table in ("opportunities", "trades", "bankroll", "equity_snapshots"):
            cols = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if "run_id" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")

    def close(self) -> None:
        self.conn.close()

    def log_price(self, snapshot: PriceSnapshot) -> None:
        self.conn.execute(
            "INSERT INTO price_snapshots (observed_at, asset, price, source) VALUES (?, ?, ?, ?)",
            (
                _iso(snapshot.timestamp),
                snapshot.asset.value,
                snapshot.price,
                snapshot.source,
            ),
        )
        self.conn.commit()

    def log_raw_snapshot(
        self,
        observed_at: datetime,
        source_name: str,
        asset: str | None,
        snapshot_type: str,
        payload: dict[str, Any] | list[Any],
        status: str = "ok",
        error_message: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO raw_snapshots
                (observed_at, source_name, asset, snapshot_type, payload_json, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(observed_at),
                source_name,
                asset,
                snapshot_type,
                json.dumps(payload, sort_keys=True),
                status,
                error_message,
            ),
        )
        self.conn.commit()

    def log_candles(self, asset: str, candles: list[Candle], source: str, observed_at: datetime) -> None:
        self.conn.executemany(
            """
            INSERT INTO candles
                (observed_at, asset, candle_start, low, high, open, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    _iso(observed_at),
                    asset,
                    _iso(candle.start),
                    candle.low,
                    candle.high,
                    candle.open,
                    candle.close,
                    candle.volume,
                    source,
                )
                for candle in candles
            ],
        )
        self.conn.commit()

    def replace_collected_market_data(
        self,
        now: datetime,
        markets: list[Market],
        orderbooks: dict[str, OrderBook],
    ) -> None:
        self.conn.execute("DELETE FROM collected_markets")
        self.conn.execute("DELETE FROM collected_orderbooks")
        self.conn.executemany(
            """
            INSERT INTO collected_markets
                (market_slug, market_id, title, asset, window_start, window_end, up_token_id,
                 down_token_id, source_url, is_mock, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    market.slug,
                    market.market_id,
                    market.title,
                    market.asset.value,
                    _iso(market.window.start),
                    _iso(market.window.end),
                    market.up_token_id,
                    market.down_token_id,
                    market.source_url,
                    int(market.is_mock),
                    _iso(now),
                )
                for market in markets
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO collected_orderbooks
                (token_id, market_slug, bid_price, bid_size, ask_price, ask_size, last_trade_price,
                 is_mock, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    token_id,
                    market.slug,
                    book.best_bid,
                    _size_for_price(book.bids, book.best_bid),
                    book.best_ask,
                    _size_for_price(book.asks, book.best_ask),
                    book.last_trade_price,
                    int(market.is_mock),
                    _iso(now),
                )
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
                for book in (orderbooks[token_id],)
            ],
        )
        self.conn.commit()

    def set_state(self, key: str, value: str, now: datetime) -> None:
        self.conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _iso(now)),
        )
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def start_run(
        self,
        strategy: str,
        mode: str,
        data_source: str,
        starting_balance: float,
        now: datetime,
        notes: str = "",
    ) -> str:
        run_id = str(uuid4())
        self.conn.execute(
            """
            INSERT INTO runs
                (run_id, strategy, mode, data_source, started_at, starting_balance, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, strategy, mode, data_source, _iso(now), starting_balance, notes),
        )
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: str, now: datetime) -> None:
        summary = self.run_summary(run_id)
        self.conn.execute(
            """
            UPDATE runs
            SET ended_at = ?, ending_balance = ?, realized_pnl = ?, max_equity_drawdown = ?,
                max_position_exposure = ?, accepted_trade_count = ?, skipped_opportunity_count = ?
            WHERE run_id = ?
            """,
            (
                _iso(now),
                summary["ending_balance"],
                summary["realized_pnl"],
                summary["max_equity_drawdown"],
                summary["max_position_exposure"],
                summary["accepted_trade_count"],
                summary["skipped_opportunity_count"],
                run_id,
            ),
        )
        self.conn.commit()

    def run_summary(self, run_id: str) -> dict[str, Any]:
        run = self.run_by_id(run_id)
        starting_balance = float(run["starting_balance"]) if run else 0.0
        ending_balance = self.current_balance(default=starting_balance, run_id=run_id)
        realized_pnl = sum(
            float(row["pnl"] or 0.0)
            for row in self.rows("SELECT pnl FROM trades WHERE status = 'CLOSED' AND run_id = ?", (run_id,))
        )
        equity_values = [
            starting_balance,
            *[
                float(row["total_equity"])
                for row in self.rows(
                    "SELECT total_equity FROM equity_snapshots WHERE run_id = ? ORDER BY id",
                    (run_id,),
                )
            ],
        ]
        exposure_values = [
            float(row["position_exposure"])
            for row in self.rows(
                "SELECT position_exposure FROM equity_snapshots WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
        ]
        accepted = self.rows("SELECT COUNT(*) AS count FROM trades WHERE run_id = ?", (run_id,))[0]["count"]
        skipped = self.rows(
            "SELECT COUNT(*) AS count FROM opportunities WHERE run_id = ? AND decision = 'SKIP'",
            (run_id,),
        )[0]["count"]
        return {
            "ending_balance": ending_balance,
            "realized_pnl": realized_pnl,
            "max_equity_drawdown": _max_drawdown(equity_values),
            "max_position_exposure": max(exposure_values, default=0.0),
            "accepted_trade_count": int(accepted),
            "skipped_opportunity_count": int(skipped),
        }

    def run_by_id(self, run_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()

    def run_rows(self) -> list[sqlite3.Row]:
        return self.rows("SELECT * FROM runs ORDER BY started_at, rowid")

    def latest_run_id(self) -> str | None:
        row = self.latest_run()
        return str(row["run_id"]) if row else None

    def log_opportunity(self, now: datetime, decision: OpportunityDecision, run_id: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO opportunities
                (run_id, observed_at, market_slug, asset, direction, probability, edge, market_price,
                 spread, decision, reason, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _iso(now),
                decision.market.slug,
                decision.market.asset.value,
                decision.signal.direction.value,
                decision.signal.probability,
                decision.signal.edge,
                decision.market_price,
                decision.spread,
                decision.decision,
                decision.reason,
                int(decision.market.is_mock),
            ),
        )
        self.conn.commit()

    def open_trade(self, now: datetime, fill: Any, run_id: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO trades
                (trade_id, run_id, opened_at, market_slug, title, asset, direction, window_end,
                 entry_price, shares, notional, entry_fee, slippage_cost, total_cost,
                 entry_underlying_price, status, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                fill.trade_id,
                run_id,
                _iso(now),
                fill.market.slug,
                fill.market.title,
                fill.market.asset.value,
                fill.direction.value,
                _iso(fill.market.window.end),
                fill.entry_price,
                fill.shares,
                fill.notional,
                fill.entry_fee,
                fill.slippage_cost,
                fill.notional + fill.entry_fee + fill.slippage_cost,
                fill.entry_underlying_price,
                int(fill.market.is_mock),
            ),
        )
        self.conn.commit()

    def close_trade(
        self,
        now: datetime,
        trade_id: str,
        exit_underlying_price: float,
        exit_price: float,
        exit_fee: float,
        pnl: float,
        result: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE trades
            SET closed_at = ?, exit_underlying_price = ?, exit_price = ?, exit_fee = ?,
                pnl = ?, result = ?, status = 'CLOSED'
            WHERE trade_id = ?
            """,
            (_iso(now), exit_underlying_price, exit_price, exit_fee, pnl, result, trade_id),
        )
        self.conn.commit()

    def current_balance(self, default: float, run_id: str | None = None) -> float:
        if run_id is None:
            row = self.conn.execute("SELECT balance FROM bankroll ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = self.conn.execute(
                "SELECT balance FROM bankroll WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return float(row["balance"]) if row else default

    def set_balance(self, now: datetime, balance: float, run_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO bankroll (run_id, observed_at, balance) VALUES (?, ?, ?)",
            (run_id, _iso(now), balance),
        )
        self.conn.commit()

    def log_equity_snapshot(
        self,
        now: datetime,
        cash_balance: float,
        open_position_value: float,
        position_exposure: float,
        run_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO equity_snapshots
                (run_id, observed_at, cash_balance, open_position_value, total_equity, position_exposure)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                _iso(now),
                cash_balance,
                open_position_value,
                cash_balance + open_position_value,
                position_exposure,
            ),
        )
        self.conn.commit()

    def open_trades(self, run_id: str | None = None) -> list[sqlite3.Row]:
        if run_id is None:
            return list(self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN'"))
        return list(
            self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN' AND run_id = ?", (run_id,))
        )

    def trade_rows(self, run_id: str | None = None) -> list[sqlite3.Row]:
        if run_id is None:
            return list(self.conn.execute("SELECT * FROM trades ORDER BY opened_at, trade_id"))
        return list(
            self.conn.execute(
                "SELECT * FROM trades WHERE run_id = ? ORDER BY opened_at, trade_id",
                (run_id,),
            )
        )

    def skipped_opportunity_rows(self, run_id: str | None = None) -> list[sqlite3.Row]:
        clause = ""
        params: tuple[Any, ...] = ()
        if run_id is not None:
            clause = "AND run_id = ?"
            params = (run_id,)
        return list(
            self.conn.execute(
                f"""
                SELECT observed_at, market_slug, asset, direction, market_price, spread, edge, reason
                FROM opportunities
                WHERE decision = 'SKIP'
                {clause}
                ORDER BY observed_at, id
                """,
                params,
            )
        )

    def latest_prices(self, source_prefix: str | None = None) -> dict[str, PriceSnapshot]:
        params: tuple[Any, ...] = ()
        source_filter = ""
        if source_prefix:
            source_filter = "WHERE source LIKE ?"
            params = (f"{source_prefix}%",)
        rows = self.rows(
            f"""
            SELECT asset, price, observed_at, source
            FROM price_snapshots
            {source_filter}
            ORDER BY observed_at DESC, id DESC
            """,
            params,
        )
        latest: dict[str, PriceSnapshot] = {}
        for row in rows:
            asset = str(row["asset"])
            if asset in latest:
                continue
            latest[asset] = PriceSnapshot(
                asset=asset_enum(asset),
                price=float(row["price"]),
                timestamp=_from_iso(str(row["observed_at"])),
                source=str(row["source"]),
            )
        return latest

    def recent_candles(
        self,
        asset: str,
        limit: int = 5,
        source_prefix: str | None = None,
    ) -> list[Candle]:
        params: list[Any] = [asset]
        source_filter = ""
        if source_prefix:
            source_filter = "AND source LIKE ?"
            params.append(f"{source_prefix}%")
        rows = self.rows(
            f"""
            SELECT candle_start, low, high, open, close, volume
            FROM candles
            WHERE asset = ?
            {source_filter}
            ORDER BY candle_start DESC, id DESC
            LIMIT ?
            """,
            tuple([*params, limit]),
        )
        candles = [
            Candle(
                start=_from_iso(str(row["candle_start"])),
                low=float(row["low"]),
                high=float(row["high"]),
                open=float(row["open"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]
        return list(reversed(candles))

    def collected_markets(self, mock_only: bool | None = None) -> list[Market]:
        query = """
            SELECT market_id, market_slug, title, asset, window_start, window_end,
                   up_token_id, down_token_id, source_url, is_mock
            FROM collected_markets
        """
        params: tuple[Any, ...] = ()
        if mock_only is not None:
            query += " WHERE is_mock = ?"
            params = (1 if mock_only else 0,)
        query += " ORDER BY window_end"
        rows = self.rows(query, params)
        return [
            Market(
                market_id=str(row["market_id"]),
                slug=str(row["market_slug"]),
                title=str(row["title"]),
                asset=asset_enum(str(row["asset"])),
                window=TimingWindow(
                    start=_from_iso(str(row["window_start"])),
                    end=_from_iso(str(row["window_end"])),
                ),
                up_token_id=str(row["up_token_id"]),
                down_token_id=str(row["down_token_id"]),
                source_url=str(row["source_url"]),
                is_mock=bool(row["is_mock"]),
            )
            for row in rows
        ]

    def collected_orderbook(self, token_id: str) -> OrderBook | None:
        row = self.conn.execute(
            """
            SELECT token_id, bid_price, bid_size, ask_price, ask_size, last_trade_price
            FROM collected_orderbooks
            WHERE token_id = ?
            """,
            (token_id,),
        ).fetchone()
        if row is None:
            return None
        bids = ()
        asks = ()
        if row["bid_price"] is not None and row["bid_size"] is not None:
            bids = (OrderLevel(price=float(row["bid_price"]), size=float(row["bid_size"])),)
        if row["ask_price"] is not None and row["ask_size"] is not None:
            asks = (OrderLevel(price=float(row["ask_price"]), size=float(row["ask_size"])),)
        return OrderBook(
            token_id=str(row["token_id"]),
            bids=bids,
            asks=asks,
            last_trade_price=float(row["last_trade_price"]) if row["last_trade_price"] is not None else None,
        )

    def raw_snapshot_rows(self) -> list[sqlite3.Row]:
        return self.rows("SELECT * FROM raw_snapshots ORDER BY observed_at, id")

    def data_quality_metrics(self, stale_seconds: int = 900, wide_spread: float = 0.10) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        raw_rows = self.raw_snapshot_rows()
        failed = sum(1 for row in raw_rows if row["status"] != "ok")
        stale = 0
        for row in raw_rows:
            observed = _from_iso(str(row["observed_at"]))
            if (now - observed).total_seconds() > stale_seconds:
                stale += 1
        latest_prices = self.latest_prices()
        missing_prices = sum(1 for asset in ("BTC", "ETH") if asset not in latest_prices)
        markets = self.collected_markets()
        missing_orderbooks = 0
        wide_spreads = 0
        low_liquidity = 0
        for market in markets:
            for token_id in (market.up_token_id, market.down_token_id):
                book = self.collected_orderbook(token_id)
                if book is None:
                    missing_orderbooks += 1
                    continue
                if book.spread is not None and book.spread > wide_spread:
                    wide_spreads += 1
                total_size = sum(level.size for level in book.bids) + sum(level.size for level in book.asks)
                if total_size < 10:
                    low_liquidity += 1
        skip_rows = self.rows(
            """
            SELECT reason, COUNT(*) AS count
            FROM opportunities
            WHERE decision = 'SKIP'
            GROUP BY reason
            ORDER BY count DESC, reason
            """
        )
        coverage_rows = self.rows(
            """
            SELECT source_name, COUNT(*) AS count
            FROM raw_snapshots
            GROUP BY source_name
            ORDER BY source_name
            """
        )
        return {
            "snapshots_collected": len(raw_rows),
            "failed_collection_attempts": failed,
            "stale_snapshots": stale,
            "missing_orderbooks": missing_orderbooks,
            "missing_prices": missing_prices,
            "wide_spreads": wide_spreads,
            "low_liquidity_markets": low_liquidity,
            "skipped_by_reason": {str(row["reason"]): int(row["count"]) for row in skip_rows},
            "source_coverage": {str(row["source_name"]): int(row["count"]) for row in coverage_rows},
        }

    def reset_paper_results(self) -> None:
        for table in ("trades", "opportunities", "bankroll", "equity_snapshots", "runs"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def reset_all(self) -> None:
        self.reset_paper_results()
        for table in (
            "price_snapshots",
            "candles",
            "collected_markets",
            "collected_orderbooks",
            "raw_snapshots",
            "app_state",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(query, params))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return max_dd


def _size_for_price(levels: tuple[OrderLevel, ...], price: float | None) -> float | None:
    if price is None:
        return None
    for level in levels:
        if level.price == price:
            return level.size
    return None


def asset_enum(value: str):
    from src.models import Asset

    return Asset(value)
