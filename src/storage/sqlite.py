from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models import Candle, DiscoveredMarket, Market, OpportunityDecision, OrderBook, OrderLevel, PriceSnapshot, TimingWindow


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    session_id TEXT,
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
    seconds_to_expiry REAL,
    lifecycle_status TEXT,
    timing_bucket TEXT,
    state_bucket TEXT,
    stuck_cycles INTEGER,
    transition_probability REAL,
    exchange_move REAL,
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
    token_id TEXT,
    window_start TEXT,
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
    close_mode TEXT,
    settlement_note TEXT,
    state_bucket TEXT,
    stuck_cycles INTEGER,
    transition_probability REAL,
    exchange_move REAL,
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
    session_id TEXT,
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
    source_name TEXT NOT NULL DEFAULT 'unknown',
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collected_market_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    observed_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_id TEXT NOT NULL,
    title TEXT NOT NULL,
    asset TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    up_token_id TEXT NOT NULL,
    down_token_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    is_mock INTEGER NOT NULL
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
    source_name TEXT NOT NULL DEFAULT 'unknown',
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collected_orderbook_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    observed_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    token_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    bid_price REAL,
    bid_size REAL,
    ask_price REAL,
    ask_size REAL,
    last_trade_price REAL,
    is_mock INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS discovered_markets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    session_id TEXT,
    source_name TEXT NOT NULL,
    market_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    asset_label TEXT NOT NULL,
    classification TEXT NOT NULL,
    classification_reasons_json TEXT NOT NULL,
    token_status TEXT NOT NULL,
    orderbook_status TEXT NOT NULL,
    up_token_id TEXT,
    down_token_id TEXT,
    condition_id TEXT,
    active INTEGER NOT NULL,
    closed INTEGER NOT NULL,
    accepting_orders INTEGER,
    window_start TEXT,
    window_end TEXT,
    source_url TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL
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
    session_id TEXT,
    source_name TEXT NOT NULL,
    asset TEXT,
    snapshot_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT,
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
CREATE TABLE IF NOT EXISTS research_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds REAL,
    interval_seconds REAL NOT NULL,
    cycles_requested INTEGER,
    cycles_completed INTEGER DEFAULT 0,
    successful_cycles INTEGER DEFAULT 0,
    failed_cycles INTEGER DEFAULT 0,
    assets_observed TEXT,
    public_sources_used TEXT,
    snapshot_count INTEGER DEFAULT 0,
    failed_snapshot_count INTEGER DEFAULT 0,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_asset_session_observed
    ON price_snapshots (asset, session_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_source_observed
    ON price_snapshots (source, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_candles_asset_session_candle
    ON candles (asset, session_id, candle_start DESC);
CREATE INDEX IF NOT EXISTS idx_candles_source_observed
    ON candles (source, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_history_session_observed_slug
    ON collected_market_history (session_id, observed_at, market_slug);
CREATE INDEX IF NOT EXISTS idx_market_history_source_observed_slug
    ON collected_market_history (source_name, observed_at, market_slug);
CREATE INDEX IF NOT EXISTS idx_orderbook_history_token_session_observed
    ON collected_orderbook_history (token_id, session_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_orderbook_history_source_observed
    ON collected_orderbook_history (source_name, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_session_observed
    ON raw_snapshots (session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_source_type_observed
    ON raw_snapshots (source_name, snapshot_type, observed_at);
CREATE INDEX IF NOT EXISTS idx_discovered_markets_session_observed
    ON discovered_markets (session_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_discovered_markets_source_observed
    ON discovered_markets (source_name, observed_at);
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
        for table in ("collected_markets", "collected_orderbooks"):
            cols = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if "source_name" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN source_name TEXT NOT NULL DEFAULT 'unknown'")
        for table in ("price_snapshots", "candles", "discovered_markets", "raw_snapshots", "runs"):
            cols = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if "session_id" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN session_id TEXT")
        opportunities_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(opportunities)")}
        for column, ddl in (
            ("seconds_to_expiry", "ALTER TABLE opportunities ADD COLUMN seconds_to_expiry REAL"),
            ("lifecycle_status", "ALTER TABLE opportunities ADD COLUMN lifecycle_status TEXT"),
            ("timing_bucket", "ALTER TABLE opportunities ADD COLUMN timing_bucket TEXT"),
            ("state_bucket", "ALTER TABLE opportunities ADD COLUMN state_bucket TEXT"),
            ("stuck_cycles", "ALTER TABLE opportunities ADD COLUMN stuck_cycles INTEGER"),
            ("transition_probability", "ALTER TABLE opportunities ADD COLUMN transition_probability REAL"),
            ("exchange_move", "ALTER TABLE opportunities ADD COLUMN exchange_move REAL"),
        ):
            if column not in opportunities_cols:
                self.conn.execute(ddl)
        trades_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(trades)")}
        for column, ddl in (
            ("token_id", "ALTER TABLE trades ADD COLUMN token_id TEXT"),
            ("window_start", "ALTER TABLE trades ADD COLUMN window_start TEXT"),
            ("close_mode", "ALTER TABLE trades ADD COLUMN close_mode TEXT"),
            ("settlement_note", "ALTER TABLE trades ADD COLUMN settlement_note TEXT"),
            ("state_bucket", "ALTER TABLE trades ADD COLUMN state_bucket TEXT"),
            ("stuck_cycles", "ALTER TABLE trades ADD COLUMN stuck_cycles INTEGER"),
            ("transition_probability", "ALTER TABLE trades ADD COLUMN transition_probability REAL"),
            ("exchange_move", "ALTER TABLE trades ADD COLUMN exchange_move REAL"),
        ):
            if column not in trades_cols:
                self.conn.execute(ddl)

    def close(self) -> None:
        self.conn.close()

    def log_price(self, snapshot: PriceSnapshot, session_id: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO price_snapshots (observed_at, session_id, asset, price, source) VALUES (?, ?, ?, ?, ?)",
            (
                _iso(snapshot.timestamp),
                session_id,
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
        session_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO raw_snapshots
                (observed_at, session_id, source_name, asset, snapshot_type, payload_json, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(observed_at),
                session_id,
                source_name,
                asset,
                snapshot_type,
                json.dumps(payload, sort_keys=True),
                status,
                error_message,
            ),
        )
        self.conn.commit()

    def log_candles(
        self,
        asset: str,
        candles: list[Candle],
        source: str,
        observed_at: datetime,
        session_id: str | None = None,
    ) -> None:
        self.conn.executemany(
            """
            INSERT INTO candles
                (observed_at, session_id, asset, candle_start, low, high, open, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    _iso(observed_at),
                    session_id,
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
        source_name: str = "unknown",
        session_id: str | None = None,
    ) -> None:
        if source_name == "unknown":
            self.conn.execute("DELETE FROM collected_markets")
            self.conn.execute("DELETE FROM collected_orderbooks")
        else:
            self.conn.execute("DELETE FROM collected_markets WHERE source_name = ?", (source_name,))
            self.conn.execute("DELETE FROM collected_orderbooks WHERE source_name = ?", (source_name,))
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO collected_markets
                (market_slug, market_id, title, asset, window_start, window_end, up_token_id,
                 down_token_id, source_url, is_mock, source_name, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_name,
                    _iso(now),
                )
                for market in markets
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO collected_market_history
                (session_id, observed_at, source_name, market_slug, market_id, title, asset, window_start,
                 window_end, up_token_id, down_token_id, source_url, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session_id,
                    _iso(now),
                    source_name,
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
                )
                for market in markets
            ],
        )
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO collected_orderbooks
                (token_id, market_slug, bid_price, bid_size, ask_price, ask_size, last_trade_price,
                 is_mock, source_name, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    source_name,
                    _iso(now),
                )
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
                for book in (orderbooks[token_id],)
            ],
        )
        self.conn.executemany(
            """
            INSERT INTO collected_orderbook_history
                (session_id, observed_at, source_name, token_id, market_slug, bid_price, bid_size, ask_price,
                 ask_size, last_trade_price, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session_id,
                    _iso(now),
                    source_name,
                    token_id,
                    market.slug,
                    book.best_bid,
                    _size_for_price(book.bids, book.best_bid),
                    book.best_ask,
                    _size_for_price(book.asks, book.best_ask),
                    book.last_trade_price,
                    int(market.is_mock),
                )
                for market in markets
                for token_id in (market.up_token_id, market.down_token_id)
                for book in (orderbooks[token_id],)
            ],
        )
        self.conn.commit()

    def log_discovered_markets(
        self,
        now: datetime,
        source_name: str,
        candidates: list[DiscoveredMarket],
        session_id: str | None = None,
    ) -> None:
        self.conn.executemany(
            """
            INSERT INTO discovered_markets
                (observed_at, source_name, market_id, market_slug, title, asset_label, classification,
                 session_id,
                 classification_reasons_json, token_status, orderbook_status, up_token_id, down_token_id,
                 condition_id, active, closed, accepting_orders, window_start, window_end, source_url,
                 accepted, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    _iso(now),
                    source_name,
                    candidate.market_id,
                    candidate.slug,
                    candidate.title,
                    candidate.asset_label,
                    candidate.classification.value,
                    session_id,
                    json.dumps(list(candidate.classification_reasons), sort_keys=True),
                    candidate.token_status,
                    candidate.orderbook_status,
                    candidate.up_token_id,
                    candidate.down_token_id,
                    candidate.condition_id,
                    int(candidate.active),
                    int(candidate.closed),
                    int(candidate.accepting_orders) if candidate.accepting_orders is not None else None,
                    _iso(candidate.window_start) if candidate.window_start else None,
                    _iso(candidate.window_end) if candidate.window_end else None,
                    candidate.source_url,
                    int(candidate.accepted),
                    candidate.reason,
                )
                for candidate in candidates
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
        session_id: str | None = None,
    ) -> str:
        run_id = str(uuid4())
        self.conn.execute(
            """
            INSERT INTO runs
                (run_id, session_id, strategy, mode, data_source, started_at, starting_balance, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, session_id, strategy, mode, data_source, _iso(now), starting_balance, notes),
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
            for row in self.rows("SELECT pnl FROM trades WHERE pnl IS NOT NULL AND run_id = ?", (run_id,))
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

    def start_research_session(
        self,
        now: datetime,
        interval_seconds: float,
        cycles_requested: int | None,
        notes: str = "",
    ) -> str:
        session_id = str(uuid4())
        self.conn.execute(
            """
            INSERT INTO research_sessions
                (session_id, started_at, interval_seconds, cycles_requested, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, _iso(now), interval_seconds, cycles_requested, notes),
        )
        self.conn.commit()
        return session_id

    def update_research_session_progress(
        self,
        session_id: str,
        *,
        cycles_completed: int,
        successful_cycles: int,
        failed_cycles: int,
    ) -> None:
        self.conn.execute(
            """
            UPDATE research_sessions
            SET cycles_completed = ?, successful_cycles = ?, failed_cycles = ?
            WHERE session_id = ?
            """,
            (cycles_completed, successful_cycles, failed_cycles, session_id),
        )
        self.conn.commit()

    def finish_research_session(self, session_id: str, ended_at: datetime) -> None:
        row = self.research_session_by_id(session_id)
        if row is None:
            return
        started_at = _from_iso(str(row["started_at"]))
        raw_rows = self.raw_snapshot_rows(session_id=session_id)
        assets = sorted({str(snapshot["asset"]) for snapshot in raw_rows if snapshot["asset"]})
        sources = sorted({str(snapshot["source_name"]) for snapshot in raw_rows})
        failed_snapshots = sum(1 for snapshot in raw_rows if str(snapshot["status"]) != "ok")
        self.conn.execute(
            """
            UPDATE research_sessions
            SET ended_at = ?, duration_seconds = ?, assets_observed = ?, public_sources_used = ?,
                snapshot_count = ?, failed_snapshot_count = ?
            WHERE session_id = ?
            """,
            (
                _iso(ended_at),
                (ended_at - started_at).total_seconds(),
                json.dumps(assets, sort_keys=True),
                json.dumps(sources, sort_keys=True),
                len(raw_rows),
                failed_snapshots,
                session_id,
            ),
        )
        self.conn.commit()

    def research_session_by_id(self, session_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    def latest_research_session(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM research_sessions ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()

    def research_session_rows(self) -> list[sqlite3.Row]:
        return self.rows("SELECT * FROM research_sessions ORDER BY started_at, rowid")

    def log_opportunity(self, now: datetime, decision: OpportunityDecision, run_id: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO opportunities
                (run_id, observed_at, market_slug, asset, direction, probability, edge, market_price,
                 spread, decision, reason, seconds_to_expiry, lifecycle_status, timing_bucket, state_bucket,
                 stuck_cycles, transition_probability, exchange_move, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                decision.seconds_to_expiry,
                decision.lifecycle_status,
                decision.timing_bucket,
                decision.state_bucket,
                decision.stuck_cycles,
                decision.transition_probability,
                decision.exchange_move,
                int(decision.market.is_mock),
            ),
        )
        self.conn.commit()

    def open_trade(self, now: datetime, fill: Any, run_id: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO trades
                (trade_id, run_id, opened_at, market_slug, title, asset, direction, token_id, window_start, window_end,
                 entry_price, shares, notional, entry_fee, slippage_cost, total_cost,
                 entry_underlying_price, status, close_mode, settlement_note, state_bucket, stuck_cycles,
                 transition_probability, exchange_move, is_mock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                fill.trade_id,
                run_id,
                _iso(now),
                fill.market.slug,
                fill.market.title,
                fill.market.asset.value,
                fill.direction.value,
                fill.market.token_for(fill.direction),
                _iso(fill.market.window.start),
                _iso(fill.market.window.end),
                fill.entry_price,
                fill.shares,
                fill.notional,
                fill.entry_fee,
                fill.slippage_cost,
                fill.notional + fill.entry_fee + fill.slippage_cost,
                fill.entry_underlying_price,
                getattr(fill, "state_bucket", None),
                getattr(fill, "stuck_cycles", None),
                getattr(fill, "transition_probability", None),
                getattr(fill, "exchange_move", None),
                int(fill.market.is_mock),
            ),
        )
        self.conn.commit()

    def close_trade(
        self,
        now: datetime,
        trade_id: str,
        *,
        exit_underlying_price: float | None,
        exit_price: float | None,
        exit_fee: float | None,
        pnl: float | None,
        result: str | None,
        status: str,
        close_mode: str,
        settlement_note: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE trades
            SET closed_at = ?, exit_underlying_price = ?, exit_price = ?, exit_fee = ?,
                pnl = ?, result = ?, status = ?, close_mode = ?, settlement_note = ?
            WHERE trade_id = ?
            """,
            (_iso(now), exit_underlying_price, exit_price, exit_fee, pnl, result, status, close_mode, settlement_note, trade_id),
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

    def latest_price(
        self,
        asset: str,
        *,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> PriceSnapshot | None:
        clauses = ["asset = ?"]
        params: list[Any] = [asset]
        source_clause, source_params = _source_sql("source", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        row = self.conn.execute(
            f"""
            SELECT asset, price, observed_at, source
            FROM price_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row is None:
            return None
        return PriceSnapshot(
            asset=asset_enum(str(row["asset"])),
            price=float(row["price"]),
            timestamp=_from_iso(str(row["observed_at"])),
            source=str(row["source"]),
        )

    def nearest_price(
        self,
        asset: str,
        target: datetime,
        *,
        max_delta_seconds: int = 300,
        source_filter: str | None = None,
        session_id: str | None = None,
    ) -> PriceSnapshot | None:
        clauses = ["asset = ?"]
        params: list[Any] = [asset]
        source_clause, source_params = _source_sql("source", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        rows = self.rows(
            f"""
            SELECT asset, price, observed_at, source
            FROM price_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY observed_at DESC, id DESC
            """,
            tuple(params),
        )
        if not rows:
            return None
        candidate_rows = []
        for row in rows:
            try:
                observed_at = _from_iso(str(row["observed_at"]))
            except ValueError:
                continue
            delta = abs((observed_at - target).total_seconds())
            if delta <= max_delta_seconds:
                candidate_rows.append((delta, observed_at, row))
        if not candidate_rows:
            return None
        _, _, row = min(candidate_rows, key=lambda item: (item[0], -item[1].timestamp()))
        return PriceSnapshot(
            asset=asset_enum(str(row["asset"])),
            price=float(row["price"]),
            timestamp=_from_iso(str(row["observed_at"])),
            source=str(row["source"]),
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

    def latest_prices(
        self,
        source_prefix: str | None = None,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> dict[str, PriceSnapshot]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_prefix:
            clauses.append("source LIKE ?")
            params.append(f"{source_prefix}%")
        source_clause, source_params = _source_sql("source", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.rows(
            f"""
            SELECT asset, price, observed_at, source
            FROM price_snapshots
            {where}
            ORDER BY observed_at DESC, id DESC
            """,
            tuple(params),
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
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[Candle]:
        params: list[Any] = [asset]
        clauses: list[str] = []
        if source_prefix:
            clauses.append("source LIKE ?")
            params.append(f"{source_prefix}%")
        source_clause, source_params = _source_sql("source", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        extra = f"AND {' AND '.join(clauses)}" if clauses else ""
        rows = self.rows(
            f"""
            SELECT candle_start, low, high, open, close, volume
            FROM candles
            WHERE asset = ?
            {extra}
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

    def collected_markets(
        self,
        mock_only: bool | None = None,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[Market]:
        if session_id is not None or since is not None or until is not None:
            rows = self._market_history_rows(
                mock_only=mock_only,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
            )
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
                    observed_at=_from_iso(str(row["first_seen"])),
                    latest_observed_at=_from_iso(str(row["latest_seen"])),
                )
                for row in rows
            ]
        query = """
            SELECT market_id, market_slug, title, asset, window_start, window_end,
                   up_token_id, down_token_id, source_url, is_mock, collected_at
            FROM collected_markets
        """
        clauses: list[str] = []
        params: list[Any] = []
        if mock_only is not None:
            clauses.append("is_mock = ?")
            params.append(1 if mock_only else 0)
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if since is not None:
            clauses.append("collected_at >= ?")
            params.append(_iso(since))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY window_end"
        rows = self.rows(query, tuple(params))
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
                    observed_at=_from_iso(str(row["collected_at"])),
                    latest_observed_at=_from_iso(str(row["collected_at"])),
                )
                for row in rows
        ]

    def collected_orderbook(
        self,
        token_id: str,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> OrderBook | None:
        if session_id is not None or since is not None or until is not None:
            clauses = ["token_id = ?"]
            params: list[Any] = [token_id]
            source_clause, source_params = _source_sql("source_name", source_filter)
            if source_clause:
                clauses.append(source_clause)
                params.extend(source_params)
            if session_id is not None:
                clauses.append("session_id = ?")
                params.append(session_id)
            if since is not None:
                clauses.append("observed_at >= ?")
                params.append(_iso(since))
            if until is not None:
                clauses.append("observed_at <= ?")
                params.append(_iso(until))
            row = self.conn.execute(
                f"""
                SELECT token_id, bid_price, bid_size, ask_price, ask_size, last_trade_price, observed_at, source_name
                FROM collected_orderbook_history
                WHERE {' AND '.join(clauses)}
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            return _orderbook_from_row(row)
        clauses = ["token_id = ?"]
        params: list[Any] = [token_id]
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if since is not None:
            clauses.append("collected_at >= ?")
            params.append(_iso(since))
        row = self.conn.execute(
            f"""
            SELECT token_id, bid_price, bid_size, ask_price, ask_size, last_trade_price, collected_at AS observed_at, source_name
            FROM collected_orderbooks
            WHERE {' AND '.join(clauses)}
            """,
            tuple(params),
        ).fetchone()
        return _orderbook_from_row(row)

    def raw_snapshot_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[sqlite3.Row]:
        where, params = self._raw_snapshot_where(
            source_filter=source_filter,
            since=since,
            until=until,
            session_id=session_id,
        )
        return self.rows(f"SELECT * FROM raw_snapshots {where} ORDER BY observed_at, id", tuple(params))

    def discovered_market_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.rows(
            f"SELECT * FROM discovered_markets {where} ORDER BY observed_at, market_slug, id",
            tuple(params),
        )

    def dataset_summary(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        where, params = self._raw_snapshot_where(
            source_filter=source_filter,
            since=since,
            until=until,
            session_id=session_id,
        )
        count_row = self.row(
            f"""
            SELECT
                COUNT(*) AS total_snapshots,
                MIN(observed_at) AS first_snapshot,
                MAX(observed_at) AS latest_snapshot,
                SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS failed_snapshots
            FROM raw_snapshots
            {where}
            """,
            tuple(params),
        )
        total_snapshots = int(count_row["total_snapshots"] or 0) if count_row is not None else 0
        first = str(count_row["first_snapshot"]) if count_row and count_row["first_snapshot"] is not None else "n/a"
        latest = str(count_row["latest_snapshot"]) if count_row and count_row["latest_snapshot"] is not None else "n/a"
        failed = int(count_row["failed_snapshots"] or 0) if count_row is not None else 0
        type_counts = {
            str(row["snapshot_type"]): int(row["count"])
            for row in self.rows(
                f"""
                SELECT snapshot_type, COUNT(*) AS count
                FROM raw_snapshots
                {where}
                GROUP BY snapshot_type
                ORDER BY snapshot_type
                """,
                tuple(params),
            )
        }
        assets = [
            str(row["asset"])
            for row in self.rows(
                f"""
                SELECT DISTINCT asset
                FROM raw_snapshots
                {where}
                {'AND' if where else 'WHERE'} asset IS NOT NULL
                ORDER BY asset
                """,
                tuple(params),
            )
        ]
        market_rows = self._visible_market_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        quality = self.data_quality_metrics(source_filter=source_filter, since=since, until=until, session_id=session_id)
        actual_sources = {
            str(row["source_name"])
            for row in self.rows(
                f"""
                SELECT DISTINCT source_name
                FROM raw_snapshots
                {where}
                ORDER BY source_name
                """,
                tuple(params),
            )
        }
        return {
            "source_filter": source_filter or "all",
            "session_id": session_id or "none",
            "total_snapshots": total_snapshots,
            "exchange_price_snapshots": type_counts.get("exchange_price", 0),
            "market_snapshots": type_counts.get("market_metadata", 0),
            "orderbook_snapshots": type_counts.get("orderbook", 0),
            "failed_snapshots": int(failed),
            "first_snapshot": first,
            "latest_snapshot": latest,
            "assets_seen": assets,
            "markets_seen": [str(row["market_slug"]) for row in market_rows],
            "missing_prices": quality["missing_prices"],
            "missing_orderbooks": quality["missing_orderbooks"],
            "stale_snapshots": quality["total_stale_snapshots"],
            "stale_exchange_prices": quality["stale_exchange_prices"],
            "stale_orderbooks": quality["stale_orderbooks"],
            "expired_markets_seen": quality["expired_markets_seen"],
            "invalid_timestamps": quality["invalid_timestamps"],
            "source_coverage": quality["source_coverage"],
            "demo_included": any(_is_demo_source(source) for source in actual_sources),
            "public_included": any(not _is_demo_source(source) for source in actual_sources),
        }

    def _raw_snapshot_where(
        self,
        *,
        source_filter: str | None,
        since: datetime | None,
        until: datetime | None,
        session_id: str | None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def data_quality_metrics(
        self,
        stale_seconds: int = 900,
        wide_spread: float = 0.10,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        raw_rows = self.raw_snapshot_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        failed = sum(1 for row in raw_rows if row["status"] != "ok")
        invalid_timestamps = 0
        for row in raw_rows:
            try:
                _from_iso(str(row["observed_at"]))
            except ValueError:
                invalid_timestamps += 1
        latest_prices = self.latest_prices(source_filter=source_filter, since=since, until=until, session_id=session_id)
        missing_prices = sum(1 for asset in ("BTC", "ETH") if asset not in latest_prices)
        markets = self.collected_markets(source_filter=source_filter, since=since, until=until, session_id=session_id)
        discovered_rows = self.discovered_market_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        missing_orderbooks = 0
        wide_spreads = 0
        low_liquidity = 0
        stale_exchange_prices = 0
        stale_orderbooks = 0
        expired_markets_seen = 0
        if source_filter == "public":
            directional = [
                row
                for row in discovered_rows
                if int(row["accepted"]) == 1
            ]
            missing_orderbooks = sum(1 for row in directional if str(row["orderbook_status"]) != "FOUND")
        for market in markets:
            observed_at = market.observed_at or market.window.start
            if observed_at >= market.window.end:
                expired_markets_seen += 1
            price = self.latest_price(
                market.asset.value,
                source_filter=source_filter,
                until=observed_at,
                session_id=session_id,
            )
            if price is None or (observed_at - price.timestamp).total_seconds() > stale_seconds:
                stale_exchange_prices += 1
            market_has_stale_book = False
            for token_id in (market.up_token_id, market.down_token_id):
                book = self.collected_orderbook(
                    token_id,
                    source_filter=source_filter,
                    until=observed_at,
                    session_id=session_id,
                )
                if book is None:
                    if source_filter != "public":
                        missing_orderbooks += 1
                    market_has_stale_book = True
                    continue
                if book.spread is not None and book.spread > wide_spread:
                    wide_spreads += 1
                total_size = sum(level.size for level in book.bids) + sum(level.size for level in book.asks)
                if total_size < 10:
                    low_liquidity += 1
                if book.observed_at is None or (observed_at - book.observed_at).total_seconds() > stale_seconds:
                    market_has_stale_book = True
            if market_has_stale_book:
                stale_orderbooks += 1
        if source_filter in ("demo", "public"):
            skip_rows = self.rows(
                """
                SELECT opportunities.reason, COUNT(*) AS count
                FROM opportunities
                JOIN runs ON runs.run_id = opportunities.run_id
                WHERE opportunities.decision = 'SKIP' AND runs.data_source = ?
                GROUP BY opportunities.reason
                ORDER BY count DESC, opportunities.reason
                """,
                (source_filter,),
            )
        else:
            skip_rows = self.rows(
                """
                SELECT reason, COUNT(*) AS count
                FROM opportunities
                WHERE decision = 'SKIP'
                GROUP BY reason
                ORDER BY count DESC, reason
                """
            )
        coverage = {}
        for row in raw_rows:
            source_name = str(row["source_name"])
            coverage[source_name] = coverage.get(source_name, 0) + 1
        return {
            "snapshots_collected": len(raw_rows),
            "failed_collection_attempts": failed,
            "stale_exchange_prices": stale_exchange_prices,
            "stale_orderbooks": stale_orderbooks,
            "expired_markets_seen": expired_markets_seen,
            "invalid_timestamps": invalid_timestamps,
            "total_stale_snapshots": stale_exchange_prices + stale_orderbooks + expired_markets_seen + invalid_timestamps,
            "stale_snapshots": stale_exchange_prices + stale_orderbooks + expired_markets_seen + invalid_timestamps,
            "missing_orderbooks": missing_orderbooks,
            "missing_prices": missing_prices,
            "wide_spreads": wide_spreads,
            "low_liquidity_markets": low_liquidity,
            "skipped_by_reason": {str(row["reason"]): int(row["count"]) for row in skip_rows},
            "source_coverage": dict(sorted(coverage.items())),
        }

    def market_audit_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._discovered_market_aggregate_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        if rows:
            output: list[dict[str, Any]] = []
            for row in rows:
                output.append(
                    {
                        "market_id": str(row["market_id"]),
                        "slug": str(row["market_slug"]),
                        "asset": str(row["asset_label"]),
                        "title": str(row["title"]),
                        "source": str(row["source_name"]),
                        "classification": str(row["classification"]),
                        "token_status": str(row["token_status"]),
                        "orderbook_status": str(row["orderbook_status"]),
                        "accepted": bool(row["accepted"]),
                        "reason": str(row["reason"]),
                        "first_seen": str(row["first_seen"]),
                        "latest_seen": str(row["latest_seen"]),
                        "up_token_id": row["up_token_id"],
                        "down_token_id": row["down_token_id"],
                        "active": bool(row["active"]),
                        "closed": bool(row["closed"]),
                    }
                )
            return output
        rows = self._market_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        output: list[dict[str, Any]] = []
        for row in rows:
            up_book = self.collected_orderbook(str(row["up_token_id"]), source_filter=source_filter, since=since, until=until, session_id=session_id)
            down_book = self.collected_orderbook(str(row["down_token_id"]), source_filter=source_filter, since=since, until=until, session_id=session_id)
            output.append(
                {
                    "market_id": str(row["market_id"]),
                    "slug": str(row["market_slug"]),
                    "asset": str(row["asset"]),
                    "title": str(row["title"]),
                    "source": str(row["source_name"]),
                    "classification": "mock" if bool(row["is_mock"]) else _detected_market_type(str(row["title"]), str(row["market_slug"])),
                    "token_status": "FOUND",
                    "orderbook_status": "FOUND" if up_book and down_book else "MISSING",
                    "accepted": True,
                    "reason": "accepted: stored market",
                    "first_seen": str(row["first_seen"]),
                    "latest_seen": str(row["latest_seen"]),
                    "up_token_id": row["up_token_id"],
                    "down_token_id": row["down_token_id"],
                    "active": True,
                    "closed": False,
                }
            )
        return output

    def readiness(
        self,
        source_filter: str | None = "public",
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        summary = self.dataset_summary(source_filter=source_filter, since=since, until=until, session_id=session_id)
        prices = self.latest_prices(source_filter=source_filter, since=since, until=until, session_id=session_id)
        discovered = self._discovered_market_aggregate_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        markets = self.collected_markets(source_filter=source_filter, since=since, until=until, session_id=session_id)
        orderbooks = [
            self.collected_orderbook(token_id, source_filter=source_filter, since=since, until=until, session_id=session_id)
            for market in markets
            for token_id in (market.up_token_id, market.down_token_id)
        ]
        market_assets = {
            str(row["asset_label"])
            for row in discovered
            if str(row["asset_label"]) in {"BTC", "ETH"}
        } or {market.asset.value for market in markets}
        price_assets = set(prices.keys())
        has_exchange_prices = bool(price_assets)
        has_markets = bool(discovered or markets)
        has_orderbooks = bool(orderbooks) and all(book is not None for book in orderbooks)
        directional = [
            row
            for row in discovered
            if str(row["classification"]) in {"crypto_up_down", "crypto_higher_lower"}
            and int(row["accepted"]) == 1
        ]
        token_ready = [row for row in directional if str(row["token_status"]) == "FOUND"]
        orderbook_ready = [row for row in directional if str(row["orderbook_status"]) == "FOUND"]
        verdict = "READY_FOR_PUBLIC_REPLAY" if source_filter == "public" else "READY_FOR_REPLAY"
        if summary["demo_included"] and summary["public_included"] and source_filter in (None, "all"):
            verdict = "MIXED_DEMO_AND_PUBLIC_DATA"
        elif not has_exchange_prices:
            verdict = "MISSING_EXCHANGE_PRICES"
        elif source_filter == "public" and not directional:
            verdict = "NO_PUBLIC_CRYPTO_MARKETS"
        elif source_filter == "public" and not token_ready:
            verdict = "NO_PUBLIC_TOKEN_IDS"
        elif source_filter == "public" and not orderbook_ready:
            verdict = "NO_PUBLIC_ORDERBOOKS"
        elif not has_markets:
            verdict = "INSUFFICIENT_PUBLIC_DATA" if source_filter == "public" else "MISSING_ORDERBOOKS"
        elif not has_orderbooks:
            verdict = "MISSING_ORDERBOOKS"
        elif not (market_assets & price_assets):
            verdict = "INSUFFICIENT_OVERLAP" if source_filter == "public" else "INSUFFICIENT_PUBLIC_DATA"
        elif len(self.raw_snapshot_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)) < 3:
            verdict = "INSUFFICIENT_SNAPSHOTS" if source_filter == "public" else "INSUFFICIENT_PUBLIC_DATA"
        return {
            "verdict": verdict,
            "source_filter": source_filter or "all",
            "dataset_sources": {
                "demo": summary["demo_included"],
                "public": summary["public_included"],
            },
            "has_exchange_prices": has_exchange_prices,
            "has_polymarket_markets": has_markets,
            "has_polymarket_orderbooks": has_orderbooks,
            "has_public_token_ids": bool(token_ready),
            "asset_overlap": sorted(market_assets & price_assets),
            "snapshot_count": summary["total_snapshots"],
            "market_count": len(directional or markets),
            "orderbook_count": sum(1 for book in orderbooks if book is not None),
            "exchange_price_count": summary["exchange_price_snapshots"],
            "wide_spreads": self.data_quality_metrics(source_filter=source_filter, since=since, until=until, session_id=session_id)["wide_spreads"],
            "missing_orderbooks": self.data_quality_metrics(source_filter=source_filter, since=since, until=until, session_id=session_id)["missing_orderbooks"],
            "token_count": len(token_ready),
            "timestamps_overlap": bool(market_assets & price_assets),
            "minimum_snapshot_count_met": summary["total_snapshots"] >= 3,
        }

    def _visible_market_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[sqlite3.Row]:
        discovered = self._discovered_market_aggregate_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)
        if discovered:
            return discovered
        return self._market_rows(source_filter=source_filter, since=since, until=until, session_id=session_id)

    def _discovered_market_aggregate_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.rows(
            f"""
            SELECT dm.market_id, dm.market_slug, dm.title, dm.asset_label, dm.classification,
                   dm.token_status, dm.orderbook_status, dm.up_token_id, dm.down_token_id,
                   dm.source_name, dm.accepted, dm.reason, dm.active, dm.closed,
                   agg.first_seen, agg.latest_seen
            FROM discovered_markets dm
            JOIN (
                SELECT market_slug, MIN(observed_at) AS first_seen, MAX(observed_at) AS latest_seen, MAX(id) AS latest_id
                FROM discovered_markets
                {where}
                GROUP BY market_slug
            ) agg
                ON agg.latest_id = dm.id
            ORDER BY agg.latest_seen, dm.market_slug
            """,
            tuple(params),
        )

    def _market_rows(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        session_id: str | None = None,
    ) -> list[sqlite3.Row]:
        if session_id is not None or since is not None or until is not None:
            return self._market_history_rows(
                mock_only=None,
                source_filter=source_filter,
                since=since,
                until=until,
                session_id=session_id,
            )
        clauses: list[str] = []
        params: list[Any] = []
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if since is not None:
            clauses.append("collected_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("collected_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.rows(
            f"""
            SELECT market_id, market_slug, title, asset, window_start, window_end,
                   up_token_id, down_token_id, source_url, is_mock, source_name,
                   MIN(collected_at) AS first_seen, MAX(collected_at) AS latest_seen
            FROM collected_markets
            {where}
            GROUP BY market_slug
            ORDER BY latest_seen, market_slug
            """,
            tuple(params),
        )

    def _market_history_rows(
        self,
        mock_only: bool | None,
        source_filter: str | None,
        since: datetime | None,
        until: datetime | None,
        session_id: str | None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if mock_only is not None:
            clauses.append("is_mock = ?")
            params.append(1 if mock_only else 0)
        source_clause, source_params = _source_sql("source_name", source_filter)
        if source_clause:
            clauses.append(source_clause)
            params.extend(source_params)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("observed_at >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("observed_at <= ?")
            params.append(_iso(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.rows(
            f"""
            SELECT h.market_id, h.market_slug, h.title, h.asset, h.window_start, h.window_end,
                   h.up_token_id, h.down_token_id, h.source_url, h.is_mock, h.source_name,
                   agg.first_seen, agg.latest_seen
            FROM collected_market_history h
            JOIN (
                SELECT market_slug, MIN(observed_at) AS first_seen, MAX(observed_at) AS latest_seen, MAX(id) AS latest_id
                FROM collected_market_history
                {where}
                GROUP BY market_slug
            ) agg
                ON agg.latest_id = h.id
            ORDER BY agg.latest_seen, h.market_slug
            """,
            tuple(params),
        )

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
            "collected_market_history",
            "collected_orderbooks",
            "collected_orderbook_history",
            "discovered_markets",
            "raw_snapshots",
            "research_sessions",
            "app_state",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(query, params))

    def row(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.conn.execute(query, params).fetchone()


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


def _orderbook_from_row(row: sqlite3.Row | None) -> OrderBook | None:
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
        observed_at=_from_iso(str(row["observed_at"])) if row["observed_at"] is not None else None,
        source=str(row["source_name"]) if row["source_name"] is not None else None,
    )


def _source_sql(column: str, source_filter: str | None) -> tuple[str, list[Any]]:
    if source_filter in (None, "", "all"):
        return "", []
    if source_filter == "demo":
        return f"{column} LIKE ?", ["mock:%"]
    if source_filter == "public":
        return f"{column} NOT LIKE ?", ["mock:%"]
    raise ValueError(f"unsupported source filter: {source_filter}")


def _id_filter(ids: list[int]) -> tuple[str, tuple[Any, ...]]:
    if not ids:
        return "WHERE 0", ()
    return f"WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids)


def _is_demo_source(source_name: str) -> bool:
    return source_name.startswith("mock:")


def _detected_market_type(title: str, slug: str) -> str:
    text = f"{title} {slug}".lower()
    if any(asset in text for asset in ("btc", "bitcoin", "eth", "ethereum")) and any(
        token in text for token in ("up", "down")
    ):
        return "up/down"
    return "unknown"


def asset_enum(value: str):
    from src.models import Asset

    return Asset(value)
