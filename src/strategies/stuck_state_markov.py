from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from src.config import AgentConfig
from src.models import Asset, Direction, Market, OpportunityDecision, OrderBook, Signal
from src.simulator.lifecycle import classify_market_lifecycle
from src.storage.sqlite import SQLiteStore, _from_iso, _iso


LIQUIDITY_MIN_SIZE = 10.0
PRICE_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.00, 0.10),
    (0.10, 0.20),
    (0.20, 0.30),
    (0.30, 0.40),
    (0.40, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.90),
    (0.90, 1.00),
)


@dataclass(frozen=True)
class SideObservation:
    market_slug: str
    asset: Asset
    side: Direction
    duration_type: str
    observed_at: datetime
    price: float | None
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    top_depth: float
    bucket: str | None
    window_start: datetime | None
    window_end: datetime | None


@dataclass(frozen=True)
class SideTransition:
    from_bucket: str
    to_bucket: str
    count: int
    probability: float


@dataclass(frozen=True)
class TransitionSummary:
    upward_probability: float
    downward_probability: float
    toward_one_probability: float
    toward_zero_probability: float
    total_count: int


@dataclass(frozen=True)
class StuckStateDecision:
    decision: OpportunityDecision


@dataclass(frozen=True)
class StuckMarkovModel:
    observations: dict[tuple[str, str], list[SideObservation]]
    transitions: dict[tuple[str, str], dict[str, Counter[str]]]
    stuck_runs: dict[tuple[str, str], list[dict[str, Any]]]


def build_markov_model(
    store: SQLiteStore,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
    config: AgentConfig,
) -> StuckMarkovModel:
    market_rows = _market_history_rows(
        store,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
    )
    orderbook_rows = _orderbook_history_rows(
        store,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
    )
    orderbooks_by_key = {
        (str(row["token_id"]), str(row["observed_at"])): row
        for row in orderbook_rows
    }
    observations: dict[tuple[str, str], list[SideObservation]] = defaultdict(list)

    for row in market_rows:
        observed_at = _from_iso(str(row["observed_at"]))
        asset = Asset(str(row["asset"]))
        duration_type = _duration_type(
            _from_iso(str(row["window_start"])),
            _from_iso(str(row["window_end"])),
        )
        for side, token_key in ((Direction.UP, "up_token_id"), (Direction.DOWN, "down_token_id")):
            token_id = str(row[token_key])
            book_row = orderbooks_by_key.get((token_id, str(row["observed_at"])))
            if book_row is None:
                continue
            best_bid = _float_or_none(book_row["bid_price"])
            best_ask = _float_or_none(book_row["ask_price"])
            price = best_ask if best_ask is not None else _float_or_none(book_row["last_trade_price"])
            spread = (best_ask - best_bid) if best_ask is not None and best_bid is not None else None
            top_depth = float(book_row["bid_size"] or 0.0) + float(book_row["ask_size"] or 0.0)
            observations[(str(row["market_slug"]), side.value)].append(
                SideObservation(
                    market_slug=str(row["market_slug"]),
                    asset=asset,
                    side=side,
                    duration_type=duration_type,
                    observed_at=observed_at,
                    price=price,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    spread=spread,
                    top_depth=top_depth,
                    bucket=bucket_for_price(price),
                    window_start=_from_iso(str(row["window_start"])) if row["window_start"] else None,
                    window_end=_from_iso(str(row["window_end"])) if row["window_end"] else None,
                )
            )

    for rows in observations.values():
        rows.sort(key=lambda item: item.observed_at)

    transitions: dict[tuple[str, str], dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    stuck_runs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (_, side), rows in observations.items():
        if not rows:
            continue
        duration_type = rows[0].duration_type
        key = (side, duration_type)
        for current, nxt in zip(rows, rows[1:]):
            if current.bucket is None or nxt.bucket is None:
                continue
            transitions[key][current.bucket][nxt.bucket] += 1
        stuck_runs[key].extend(_streaks_for_rows(rows, config))

    return StuckMarkovModel(
        observations=dict(observations),
        transitions={key: dict(value) for key, value in transitions.items()},
        stuck_runs=dict(stuck_runs),
    )


def evaluate_stuck_markov_market(
    store: SQLiteStore,
    model: StuckMarkovModel,
    market: Market,
    *,
    observed_at: datetime,
    source_filter: str | None,
    session_id: str | None,
    config: AgentConfig,
) -> StuckStateDecision:
    base_signal = Signal(
        asset=market.asset,
        direction=Direction.UP,
        probability=0.5,
        edge=0.0,
        reason="stuck-state markov",
    )
    lifecycle = classify_market_lifecycle(
        market,
        observed_at,
        min_seconds_before_end=config.min_seconds_before_end,
        max_seconds_after_start=config.max_seconds_after_start,
    )
    if market.asset != Asset.BTC:
        return StuckStateDecision(_skip(market, base_signal, "not BTC", lifecycle))
    if _duration_type(market.window.start, market.window.end) != "5m":
        return StuckStateDecision(_skip(market, base_signal, "not 5m", lifecycle))
    if market.window.end is None:
        return StuckStateDecision(_skip(market, base_signal, "missing expiry", lifecycle))
    if lifecycle.timing_bucket != "valid_window":
        return StuckStateDecision(_skip(market, base_signal, "not active", lifecycle))
    if lifecycle.seconds_to_expiry is None:
        return StuckStateDecision(_skip(market, base_signal, "missing expiry", lifecycle))
    if lifecycle.seconds_to_expiry < config.stuck_min_seconds_to_expiry or lifecycle.seconds_to_expiry > config.stuck_max_seconds_to_expiry:
        return StuckStateDecision(_skip(market, base_signal, "outside stuck expiry window", lifecycle))

    side_observations = {
        Direction.UP: _current_observation(model, market.slug, Direction.UP, observed_at),
        Direction.DOWN: _current_observation(model, market.slug, Direction.DOWN, observed_at),
    }
    if side_observations[Direction.UP] is None or side_observations[Direction.DOWN] is None:
        return StuckStateDecision(_skip(market, base_signal, "missing orderbook", lifecycle))

    candidates: list[OpportunityDecision] = []
    for side, obs in side_observations.items():
        assert obs is not None
        if obs.best_ask is None:
            candidates.append(_skip(market, base_signal, f"missing {side.value} ask", lifecycle))
            continue
        if obs.spread is None or obs.spread > config.stuck_max_spread:
            candidates.append(_skip(market, base_signal, "spread too wide", lifecycle, market_price=obs.best_ask, spread=obs.spread, state_bucket=obs.bucket))
            continue
        if obs.top_depth < LIQUIDITY_MIN_SIZE:
            candidates.append(_skip(market, base_signal, "liquidity too low", lifecycle, market_price=obs.best_ask, spread=obs.spread, state_bucket=obs.bucket))
            continue
        if obs.bucket is None:
            candidates.append(_skip(market, base_signal, "outside stuck price bucket", lifecycle, market_price=obs.best_ask, spread=obs.spread))
            continue
        bucket_mid = bucket_midpoint(obs.bucket)
        if bucket_mid < config.stuck_price_bucket_min or bucket_mid > config.stuck_price_bucket_max:
            candidates.append(_skip(market, base_signal, "outside stuck price bucket", lifecycle, market_price=obs.best_ask, spread=obs.spread, state_bucket=obs.bucket))
            continue
        history = model.observations.get((market.slug, side.value), [])
        streak = _current_streak(history, observed_at)
        if streak["cycles"] < config.stuck_state_min_cycles:
            candidates.append(
                _skip(
                    market,
                    base_signal,
                    "not enough stuck cycles",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                )
            )
            continue
        start_price = store.nearest_price(
            market.asset.value,
            streak["start_time"],
            max_delta_seconds=300,
            source_filter=source_filter,
            session_id=session_id,
        )
        current_price = store.latest_price(
            market.asset.value,
            source_filter=source_filter,
            until=observed_at,
            session_id=session_id,
        )
        if start_price is None or current_price is None:
            candidates.append(
                _skip(
                    market,
                    base_signal,
                    "missing underlying price",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                )
            )
            continue
        exchange_move = (current_price.price - start_price.price) / start_price.price
        if side == Direction.UP and exchange_move <= 0:
            candidates.append(
                _skip(
                    market,
                    base_signal,
                    "exchange move does not support side",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                    exchange_move=exchange_move,
                )
            )
            continue
        if side == Direction.DOWN and exchange_move >= 0:
            candidates.append(
                _skip(
                    market,
                    base_signal,
                    "exchange move does not support side",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                    exchange_move=exchange_move,
                )
            )
            continue
        summary = transition_summary(model, side, "5m", obs.bucket)
        if summary.toward_one_probability <= summary.toward_zero_probability or summary.total_count == 0:
            candidates.append(
                _skip(
                    market,
                    base_signal,
                    "transition probability unfavorable",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                    transition_probability=summary.toward_one_probability,
                    exchange_move=exchange_move,
                )
            )
            continue
        projected_probability = min(
            0.99,
            obs.best_ask + max(0.0, summary.toward_one_probability - summary.toward_zero_probability) * 0.20,
        )
        edge = projected_probability - obs.best_ask - (obs.spread or 0.0) / 2.0
        signal = Signal(
            asset=market.asset,
            direction=side,
            probability=projected_probability,
            edge=edge,
            reason=(
                f"stuck bucket {obs.bucket} for {streak['cycles']} cycles; "
                f"toward_one={summary.toward_one_probability:.2f}; exchange_move={exchange_move:.4%}"
            ),
        )
        if edge < config.min_edge:
            candidates.append(
                _skip(
                    market,
                    signal,
                    "edge below threshold",
                    lifecycle,
                    market_price=obs.best_ask,
                    spread=obs.spread,
                    state_bucket=obs.bucket,
                    stuck_cycles=streak["cycles"],
                    transition_probability=summary.toward_one_probability,
                    exchange_move=exchange_move,
                )
            )
            continue
        candidates.append(
            OpportunityDecision(
                market=market,
                signal=signal,
                market_price=obs.best_ask,
                spread=obs.spread,
                decision="TRADE",
                reason="stuck-state markov signal accepted",
                seconds_to_expiry=lifecycle.seconds_to_expiry,
                lifecycle_status=lifecycle.status,
                timing_bucket=lifecycle.timing_bucket,
                state_bucket=obs.bucket,
                stuck_cycles=streak["cycles"],
                transition_probability=summary.toward_one_probability,
                exchange_move=exchange_move,
            )
        )

    chosen = [item for item in candidates if item.decision == "TRADE"]
    if chosen:
        chosen.sort(key=lambda item: (float(item.transition_probability or 0.0), float(item.signal.edge)), reverse=True)
        return StuckStateDecision(chosen[0])
    candidates.sort(key=lambda item: float(item.transition_probability or 0.0), reverse=True)
    return StuckStateDecision(candidates[0] if candidates else _skip(market, base_signal, "no stuck-state signal", lifecycle))


def transition_summary(
    model: StuckMarkovModel,
    side: Direction,
    duration_type: str,
    bucket: str,
) -> TransitionSummary:
    counts = model.transitions.get((side.value, duration_type), {}).get(bucket, Counter())
    total = sum(counts.values())
    if total == 0:
        return TransitionSummary(0.0, 0.0, 0.0, 0.0, 0)
    current_mid = bucket_midpoint(bucket)
    upward = 0
    downward = 0
    for target_bucket, count in counts.items():
        target_mid = bucket_midpoint(target_bucket)
        if target_mid > current_mid:
            upward += count
        elif target_mid < current_mid:
            downward += count
    return TransitionSummary(
        upward_probability=upward / total,
        downward_probability=downward / total,
        toward_one_probability=upward / total,
        toward_zero_probability=downward / total,
        total_count=total,
    )


def build_markov_report(
    store: SQLiteStore,
    config: AgentConfig,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> str:
    model = build_markov_model(
        store,
        source_filter=source_filter,
        since=since,
        until=until,
        session_id=session_id,
        config=config,
    )
    lines = [
        "Markov transition report",
        f"Source filter: {source_filter or 'all'}",
        f"Session ID: {session_id or 'none'}",
    ]
    if not model.observations:
        lines.append("No historical orderbook observations found.")
        return "\n".join(lines)

    for duration_type in ("5m", "15m"):
        for side in (Direction.UP, Direction.DOWN):
            key = (side.value, duration_type)
            rows = model.transitions.get(key, {})
            obs_count = sum(len(values) for (slug, row_side), values in model.observations.items() if row_side == side.value and values and values[0].duration_type == duration_type)
            lines.append(f"{duration_type} {side.value}: observations={obs_count}, stuck_runs={len(model.stuck_runs.get(key, []))}")
            for bucket in ("0.70-0.75", "0.75-0.80"):
                summary = transition_summary(model, side, duration_type, bucket)
                lines.append(
                    f"  {bucket}: transitions={summary.total_count}, toward_0.90-1.00={summary.toward_one_probability:.2%}, toward_0.00-0.10={summary.toward_zero_probability:.2%}"
                )
            average_stuck = mean(
                [
                    float(item["seconds"])
                    for item in model.stuck_runs.get(key, [])
                    if item["bucket"] in {"0.70-0.75", "0.75-0.80"}
                ]
            ) if any(item["bucket"] in {"0.70-0.75", "0.75-0.80"} for item in model.stuck_runs.get(key, [])) else 0.0
            lines.append(f"  average_time_stuck_before_transition_seconds={average_stuck:.2f}")
            if rows:
                top_rows = []
                for from_bucket, targets in rows.items():
                    total = sum(targets.values())
                    if total == 0:
                        continue
                    top_target, top_count = max(targets.items(), key=lambda item: item[1])
                    top_rows.append(f"{from_bucket}->{top_target} ({top_count}/{total})")
                if top_rows:
                    lines.append(f"  top_transitions={', '.join(top_rows[:5])}")
    return "\n".join(lines)


def latest_observation_time(model: StuckMarkovModel, market_slug: str) -> datetime | None:
    times = [
        rows[-1].observed_at
        for (slug, _side), rows in model.observations.items()
        if slug == market_slug and rows
    ]
    return max(times) if times else None


def latest_tradeable_observation_time(
    model: StuckMarkovModel,
    market: Market,
    config: AgentConfig,
) -> datetime | None:
    rows = model.observations.get((market.slug, Direction.UP.value), [])
    eligible = []
    for row in rows:
        lifecycle = classify_market_lifecycle(
            market,
            row.observed_at,
            min_seconds_before_end=config.min_seconds_before_end,
            max_seconds_after_start=config.max_seconds_after_start,
        )
        if lifecycle.timing_bucket != "valid_window":
            continue
        if lifecycle.seconds_to_expiry is None:
            continue
        if lifecycle.seconds_to_expiry < config.stuck_min_seconds_to_expiry:
            continue
        if lifecycle.seconds_to_expiry > config.stuck_max_seconds_to_expiry:
            continue
        eligible.append(row.observed_at)
    return max(eligible) if eligible else latest_observation_time(model, market.slug)


def bucket_for_price(price: float | None) -> str | None:
    if price is None:
        return None
    clipped = min(1.0, max(0.0, price))
    for low, high in PRICE_BUCKETS:
        if low <= clipped < high or (high == 1.0 and clipped <= high):
            return f"{low:.2f}-{high:.2f}"
    return None


def bucket_midpoint(bucket: str) -> float:
    low, high = bucket.split("-", 1)
    return (float(low) + float(high)) / 2.0


def _skip(
    market: Market,
    signal: Signal,
    reason: str,
    lifecycle,
    *,
    market_price: float | None = None,
    spread: float | None = None,
    state_bucket: str | None = None,
    stuck_cycles: int | None = None,
    transition_probability: float | None = None,
    exchange_move: float | None = None,
) -> OpportunityDecision:
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
        state_bucket=state_bucket,
        stuck_cycles=stuck_cycles,
        transition_probability=transition_probability,
        exchange_move=exchange_move,
    )


def _current_observation(
    model: StuckMarkovModel,
    market_slug: str,
    side: Direction,
    observed_at: datetime,
) -> SideObservation | None:
    rows = model.observations.get((market_slug, side.value), [])
    eligible = [row for row in rows if row.observed_at <= observed_at]
    return eligible[-1] if eligible else None


def _current_streak(rows: list[SideObservation], observed_at: datetime) -> dict[str, Any]:
    eligible = [row for row in rows if row.observed_at <= observed_at and row.bucket is not None]
    if not eligible:
        return {"cycles": 0, "start_time": observed_at}
    current_bucket = eligible[-1].bucket
    cycles = 0
    start_time = eligible[-1].observed_at
    for row in reversed(eligible):
        if row.bucket != current_bucket:
            break
        cycles += 1
        start_time = row.observed_at
    return {"cycles": cycles, "start_time": start_time}


def _streaks_for_rows(rows: list[SideObservation], config: AgentConfig) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not rows:
        return output
    current_bucket = rows[0].bucket
    current_start = rows[0].observed_at
    current_count = 1
    previous = rows[0]
    for row in rows[1:]:
        if row.bucket == current_bucket and row.bucket is not None:
            current_count += 1
            previous = row
            continue
        if current_bucket is not None and current_count >= config.stuck_state_min_cycles:
            output.append(
                {
                    "bucket": current_bucket,
                    "cycles": current_count,
                    "seconds": (previous.observed_at - current_start).total_seconds(),
                }
            )
        current_bucket = row.bucket
        current_start = row.observed_at
        current_count = 1
        previous = row
    if current_bucket is not None and current_count >= config.stuck_state_min_cycles:
        output.append(
            {
                "bucket": current_bucket,
                "cycles": current_count,
                "seconds": (previous.observed_at - current_start).total_seconds(),
            }
        )
    return output


def _market_history_rows(
    store: SQLiteStore,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> list[Any]:
    clauses: list[str] = []
    params: list[Any] = []
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
        params.append(_iso(since))
    if until is not None:
        clauses.append("observed_at <= ?")
        params.append(_iso(until))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return store.rows(
        f"""
        SELECT observed_at, market_slug, market_id, title, asset, window_start, window_end,
               up_token_id, down_token_id, source_url, is_mock
        FROM collected_market_history
        {where}
        ORDER BY observed_at, id
        """,
        tuple(params),
    )


def _orderbook_history_rows(
    store: SQLiteStore,
    *,
    source_filter: str | None,
    since: datetime | None,
    until: datetime | None,
    session_id: str | None,
) -> list[Any]:
    clauses: list[str] = []
    params: list[Any] = []
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
        params.append(_iso(since))
    if until is not None:
        clauses.append("observed_at <= ?")
        params.append(_iso(until))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return store.rows(
        f"""
        SELECT observed_at, token_id, market_slug, bid_price, bid_size, ask_price, ask_size, last_trade_price
        FROM collected_orderbook_history
        {where}
        ORDER BY observed_at, id
        """,
        tuple(params),
    )


def _duration_type(start: datetime, end: datetime) -> str:
    minutes = (end - start).total_seconds() / 60.0
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    return float(value)
