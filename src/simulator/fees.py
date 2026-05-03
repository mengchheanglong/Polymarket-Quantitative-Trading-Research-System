from __future__ import annotations


def fee_amount(notional: float, fee_bps: float) -> float:
    return notional * fee_bps / 10_000.0


def slippage_amount(notional: float, slippage_bps: float) -> float:
    return notional * slippage_bps / 10_000.0


def execution_price(best_ask: float, slippage_bps: float) -> float:
    if not 0 < best_ask < 1:
        raise ValueError("best_ask must be between 0 and 1")
    return min(0.99, best_ask * (1.0 + slippage_bps / 10_000.0))

