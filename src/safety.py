from __future__ import annotations

import os

from .config import parse_bool


class SafetyError(RuntimeError):
    pass


LIVE_TRADING_FLAGS = (
    "ALLOW_LIVE_TRADING",
    "LIVE_TRADING",
    "POLYMARKET_LIVE",
    "REAL_TRADES",
    "DRY_RUN_FALSE",
)


def enforce_paper_only(dry_run: bool, execution_mode: str) -> None:
    if not dry_run:
        raise SafetyError("Refusing to run: DRY_RUN must be true.")
    if execution_mode.strip().lower() not in {"paper", "dry_run", "dry-run", "research"}:
        raise SafetyError("Refusing to run: live execution modes are not supported.")
    for flag in LIVE_TRADING_FLAGS:
        if parse_bool(os.environ.get(flag), default=False):
            raise SafetyError(f"Refusing to run: {flag} attempts to enable live trading.")

