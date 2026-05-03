from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.models import Asset, Candle, Market, OrderBook, OrderLevel, PriceSnapshot, TimingWindow


class MockMarketSource:
    """Deterministic offline dataset for safe paper-trading demos."""

    def collect_prices(self, now: datetime | None = None) -> list[PriceSnapshot]:
        now = _base_time(now)
        return [
            PriceSnapshot(
                asset=Asset.BTC,
                price=100_800.0,
                timestamp=now,
                source="mock:demo:spot:BTC",
            ),
            PriceSnapshot(
                asset=Asset.ETH,
                price=1_980.0,
                timestamp=now,
                source="mock:demo:spot:ETH",
            ),
        ]

    def recent_candles(self, asset: Asset, now: datetime | None = None) -> list[Candle]:
        now = _base_time(now)
        closes = {
            Asset.BTC: [100_000.0, 100_200.0, 100_400.0, 100_600.0, 100_800.0],
            Asset.ETH: [2_020.0, 2_010.0, 2_000.0, 1_990.0, 1_980.0],
        }[asset]
        candles: list[Candle] = []
        for index, close in enumerate(closes):
            start = now - timedelta(minutes=len(closes) - index)
            candles.append(
                Candle(
                    start=start,
                    open=close - 10.0,
                    high=close + 10.0,
                    low=close - 15.0,
                    close=close,
                    volume=25.0 + index,
                )
            )
        return candles

    def markets(self, now: datetime | None = None) -> list[Market]:
        now = _base_time(now)
        return [
            Market(
                market_id="demo-btc-up-main",
                slug="demo-btc-updown-main",
                title="DEMO BTC Up or Down - main",
                asset=Asset.BTC,
                window=TimingWindow(start=now - timedelta(minutes=2), end=now + timedelta(minutes=3)),
                up_token_id="DEMO-BTC-UP-MAIN",
                down_token_id="DEMO-BTC-DOWN-MAIN",
                source_url="mock://demo-market-source",
                is_mock=True,
            ),
            Market(
                market_id="demo-eth-down-main",
                slug="demo-eth-updown-main",
                title="DEMO ETH Up or Down - main",
                asset=Asset.ETH,
                window=TimingWindow(start=now - timedelta(minutes=2), end=now + timedelta(minutes=4)),
                up_token_id="DEMO-ETH-UP-MAIN",
                down_token_id="DEMO-ETH-DOWN-MAIN",
                source_url="mock://demo-market-source",
                is_mock=True,
            ),
            Market(
                market_id="demo-btc-up-skip",
                slug="demo-btc-updown-skip",
                title="DEMO BTC Up or Down - expensive",
                asset=Asset.BTC,
                window=TimingWindow(start=now - timedelta(minutes=1), end=now + timedelta(minutes=5)),
                up_token_id="DEMO-BTC-UP-SKIP",
                down_token_id="DEMO-BTC-DOWN-SKIP",
                source_url="mock://demo-market-source",
                is_mock=True,
            ),
        ]

    def orderbook(self, token_id: str) -> OrderBook:
        books = {
            "DEMO-BTC-UP-MAIN": OrderBook(
                token_id="DEMO-BTC-UP-MAIN",
                bids=(OrderLevel(price=0.45, size=500.0),),
                asks=(OrderLevel(price=0.47, size=500.0),),
                last_trade_price=0.46,
            ),
            "DEMO-BTC-DOWN-MAIN": OrderBook(
                token_id="DEMO-BTC-DOWN-MAIN",
                bids=(OrderLevel(price=0.46, size=500.0),),
                asks=(OrderLevel(price=0.48, size=500.0),),
                last_trade_price=0.47,
            ),
            "DEMO-ETH-UP-MAIN": OrderBook(
                token_id="DEMO-ETH-UP-MAIN",
                bids=(OrderLevel(price=0.45, size=500.0),),
                asks=(OrderLevel(price=0.47, size=500.0),),
                last_trade_price=0.46,
            ),
            "DEMO-ETH-DOWN-MAIN": OrderBook(
                token_id="DEMO-ETH-DOWN-MAIN",
                bids=(OrderLevel(price=0.44, size=500.0),),
                asks=(OrderLevel(price=0.46, size=500.0),),
                last_trade_price=0.45,
            ),
            "DEMO-BTC-UP-SKIP": OrderBook(
                token_id="DEMO-BTC-UP-SKIP",
                bids=(OrderLevel(price=0.63, size=500.0),),
                asks=(OrderLevel(price=0.64, size=500.0),),
                last_trade_price=0.635,
            ),
            "DEMO-BTC-DOWN-SKIP": OrderBook(
                token_id="DEMO-BTC-DOWN-SKIP",
                bids=(OrderLevel(price=0.36, size=500.0),),
                asks=(OrderLevel(price=0.37, size=500.0),),
                last_trade_price=0.365,
            ),
        }
        return books[token_id]

    def settlement_prices(self, now: datetime | None = None) -> list[PriceSnapshot]:
        now = _base_time(now) + timedelta(minutes=6)
        return [
            PriceSnapshot(
                asset=Asset.BTC,
                price=101_000.0,
                timestamp=now,
                source="mock:demo:settlement:BTC",
            ),
            PriceSnapshot(
                asset=Asset.ETH,
                price=1_990.0,
                timestamp=now,
                source="mock:demo:settlement:ETH",
            ),
        ]


def _base_time(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(second=0, microsecond=0)
