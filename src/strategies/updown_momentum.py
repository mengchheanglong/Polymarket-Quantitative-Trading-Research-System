from __future__ import annotations

from src.models import Asset, Candle, Direction, Signal


class MomentumUpDownStrategy:
    """Simple research heuristic for short-window UP/DOWN markets."""

    def signal(self, asset: Asset, candles: list[Candle]) -> Signal:
        if len(candles) < 2:
            return Signal(
                asset=asset,
                direction=Direction.UP,
                probability=0.50,
                edge=0.0,
                reason="not enough candle history",
            )

        first = candles[-5].close if len(candles) >= 5 else candles[0].close
        last = candles[-1].close
        move = (last - first) / first
        direction = Direction.UP if move >= 0 else Direction.DOWN
        probability = min(0.65, 0.50 + abs(move) * 20.0)
        return Signal(
            asset=asset,
            direction=direction,
            probability=probability,
            edge=0.0,
            reason=f"recent momentum {move:.4%}",
        )

