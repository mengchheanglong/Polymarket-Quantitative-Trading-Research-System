from __future__ import annotations


class SizingError(ValueError):
    pass


def size_position(
    bankroll: float,
    market_price: float,
    max_position_pct: float,
    max_position_usd: float,
    max_trade_usd: float | None = None,
) -> tuple[float, float]:
    if bankroll <= 0:
        raise SizingError("bankroll must be positive")
    if not 0 < market_price < 1:
        raise SizingError("market_price must be between 0 and 1")
    if not 0 < max_position_pct <= 1:
        raise SizingError("max_position_pct must be within (0, 1]")
    if max_position_usd <= 0:
        raise SizingError("max_position_usd must be positive")
    if max_trade_usd is not None and max_trade_usd <= 0:
        raise SizingError("max_trade_usd must be positive")

    caps = [bankroll * max_position_pct, max_position_usd]
    if max_trade_usd is not None:
        caps.append(max_trade_usd)
    notional = min(caps)
    shares = notional / market_price
    return round(notional, 8), round(shares, 8)
