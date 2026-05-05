from src.risk.sizing import size_position
from src.simulator.fees import execution_price, fee_amount, slippage_amount


def test_trade_sizing_uses_smaller_of_pct_and_cap():
    notional, shares = size_position(
        bankroll=1_000.0,
        market_price=0.50,
        max_position_pct=0.05,
        max_position_usd=100.0,
    )

    assert notional == 50.0
    assert shares == 100.0


def test_trade_sizing_uses_absolute_cap():
    notional, shares = size_position(
        bankroll=10_000.0,
        market_price=0.25,
        max_position_pct=0.05,
        max_position_usd=100.0,
    )

    assert notional == 100.0
    assert shares == 400.0


def test_trade_sizing_respects_max_trade_usd():
    notional, shares = size_position(
        bankroll=10_000.0,
        market_price=0.50,
        max_position_pct=0.05,
        max_position_usd=100.0,
        max_trade_usd=1.0,
    )

    assert notional == 1.0
    assert shares == 2.0


def test_fee_and_slippage_calculation():
    assert fee_amount(100.0, 10.0) == 0.10
    assert slippage_amount(100.0, 25.0) == 0.25
    assert round(execution_price(0.50, 25.0), 6) == 0.50125
