from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class AgentConfig:
    dry_run: bool = True
    execution_mode: str = "paper"
    database_path: Path = Path("paper_trading.sqlite3")
    starting_balance: float = 1_000.0
    max_position_pct: float = 0.05
    max_position_usd: float = 100.0
    max_trade_usd: float = 100.0
    max_total_exposure_usd: float = 1_000.0
    max_open_positions: int = 100
    max_trades_per_market: int = 100
    max_trades_per_session: int = 10_000
    session_loss_limit_usd: float = 1_000.0
    daily_loss_limit_usd: float = 1_000.0
    cooldown_after_loss_seconds: int = 0
    min_seconds_to_expiry: int | None = None
    max_seconds_to_expiry: int | None = None
    min_edge: float = 0.02
    max_spread: float = 0.10
    fee_bps: float = 10.0
    slippage_bps: float = 25.0
    assumed_spread: float = 0.02
    failed_fill_probability: float = 0.0
    pair_cost_threshold: float = 0.98
    pair_cost_failed_second_leg_probability: float = 0.0
    stuck_state_min_cycles: int = 3
    stuck_price_bucket_min: float = 0.70
    stuck_price_bucket_max: float = 0.80
    stuck_max_spread: float = 0.02
    stuck_min_seconds_to_expiry: int = 30
    stuck_max_seconds_to_expiry: int = 180
    min_seconds_before_end: int = 45
    max_seconds_after_start: int | None = None
    max_market_duration_minutes: int = 60
    use_demo_markets: bool = False
    strategy: str = "momentum"
    close_mode: str = "expiry-if-known"
    tiny_profile: bool = False
    random_seed: int = 7
    gamma_base_url: str = "https://gamma-api.polymarket.com"
    clob_base_url: str = "https://clob.polymarket.com"
    coinbase_base_url: str = "https://api.exchange.coinbase.com"
    kraken_base_url: str = "https://api.kraken.com"


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> AgentConfig:
    load_dotenv()
    max_after = os.environ.get("MAX_SECONDS_AFTER_START")
    min_to_expiry = os.environ.get("MIN_SECONDS_TO_EXPIRY")
    max_to_expiry = os.environ.get("MAX_SECONDS_TO_EXPIRY")
    use_mock_data = parse_bool(os.environ.get("USE_MOCK_DATA"), default=False)
    use_demo_markets = parse_bool(os.environ.get("USE_DEMO_MARKETS"), default=False)
    return AgentConfig(
        dry_run=parse_bool(os.environ.get("DRY_RUN"), default=True),
        execution_mode=os.environ.get("EXECUTION_MODE", "paper"),
        database_path=Path(os.environ.get("DATABASE_PATH", "paper_trading.sqlite3")),
        starting_balance=float(os.environ.get("STARTING_BALANCE", "1000")),
        max_position_pct=float(os.environ.get("MAX_POSITION_PCT", "0.05")),
        max_position_usd=float(os.environ.get("MAX_POSITION_USD", "100")),
        max_trade_usd=float(os.environ.get("MAX_TRADE_USD", "100")),
        max_total_exposure_usd=float(os.environ.get("MAX_TOTAL_EXPOSURE_USD", "1000")),
        max_open_positions=int(os.environ.get("MAX_OPEN_POSITIONS", "100")),
        max_trades_per_market=int(os.environ.get("MAX_TRADES_PER_MARKET", "100")),
        max_trades_per_session=int(os.environ.get("MAX_TRADES_PER_SESSION", "10000")),
        session_loss_limit_usd=float(os.environ.get("SESSION_LOSS_LIMIT_USD", "1000")),
        daily_loss_limit_usd=float(os.environ.get("DAILY_LOSS_LIMIT_USD", "1000")),
        cooldown_after_loss_seconds=int(os.environ.get("COOLDOWN_AFTER_LOSS_SECONDS", "0")),
        min_seconds_to_expiry=int(min_to_expiry) if min_to_expiry else None,
        max_seconds_to_expiry=int(max_to_expiry) if max_to_expiry else None,
        min_edge=float(os.environ.get("MIN_EDGE", "0.02")),
        max_spread=float(os.environ.get("MAX_SPREAD", "0.10")),
        fee_bps=float(os.environ.get("FEE_BPS", "10")),
        slippage_bps=float(os.environ.get("SLIPPAGE_BPS", "25")),
        assumed_spread=float(os.environ.get("ASSUMED_SPREAD", "0.02")),
        failed_fill_probability=float(os.environ.get("FAILED_FILL_PROBABILITY", "0")),
        pair_cost_threshold=float(os.environ.get("PAIR_COST_THRESHOLD", "0.98")),
        pair_cost_failed_second_leg_probability=float(
            os.environ.get("PAIR_COST_FAILED_SECOND_LEG_PROBABILITY", "0")
        ),
        stuck_state_min_cycles=int(os.environ.get("STUCK_STATE_MIN_CYCLES", "3")),
        stuck_price_bucket_min=float(os.environ.get("STUCK_PRICE_BUCKET_MIN", "0.70")),
        stuck_price_bucket_max=float(os.environ.get("STUCK_PRICE_BUCKET_MAX", "0.80")),
        stuck_max_spread=float(os.environ.get("STUCK_MAX_SPREAD", "0.02")),
        stuck_min_seconds_to_expiry=int(os.environ.get("STUCK_MIN_SECONDS_TO_EXPIRY", "30")),
        stuck_max_seconds_to_expiry=int(os.environ.get("STUCK_MAX_SECONDS_TO_EXPIRY", "180")),
        min_seconds_before_end=int(os.environ.get("MIN_SECONDS_BEFORE_END", "45")),
        max_seconds_after_start=int(max_after) if max_after else None,
        max_market_duration_minutes=int(os.environ.get("MAX_MARKET_DURATION_MINUTES", "60")),
        use_demo_markets=use_mock_data or use_demo_markets,
        strategy=os.environ.get("STRATEGY", "momentum"),
        close_mode=os.environ.get("CLOSE_MODE", "expiry-if-known"),
        tiny_profile=parse_bool(os.environ.get("TINY_PROFILE"), default=False),
        random_seed=int(os.environ.get("RANDOM_SEED", "7")),
        gamma_base_url=os.environ.get("GAMMA_BASE_URL", "https://gamma-api.polymarket.com"),
        clob_base_url=os.environ.get("CLOB_BASE_URL", "https://clob.polymarket.com"),
        coinbase_base_url=os.environ.get(
            "COINBASE_BASE_URL", "https://api.exchange.coinbase.com"
        ),
        kraken_base_url=os.environ.get("KRAKEN_BASE_URL", "https://api.kraken.com"),
    )
