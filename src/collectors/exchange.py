from __future__ import annotations

from datetime import datetime, timezone

from src.http_client import JsonHttpClient
from src.models import Asset, Candle, PriceSnapshot


COINBASE_PRODUCTS = {
    Asset.BTC: "BTC-USD",
    Asset.ETH: "ETH-USD",
}


class CoinbaseCollector:
    """Collects public BTC/ETH prices from Coinbase Exchange."""

    def __init__(self, base_url: str, http: JsonHttpClient | None = None):
        self.base_url = base_url
        self.http = http or JsonHttpClient()

    def latest_price(self, asset: Asset) -> PriceSnapshot:
        product = COINBASE_PRODUCTS[asset]
        data = self.http.get_json(self.base_url, f"/products/{product}/ticker")
        timestamp = data.get("time")
        observed_at = (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if timestamp
            else datetime.now(timezone.utc)
        )
        return PriceSnapshot(
            asset=asset,
            price=float(data["price"]),
            timestamp=observed_at.astimezone(timezone.utc),
            source=f"coinbase:{product}",
        )

    def recent_candles(self, asset: Asset, granularity: int = 60) -> list[Candle]:
        product = COINBASE_PRODUCTS[asset]
        rows = self.http.get_json(
            self.base_url,
            f"/products/{product}/candles",
            {"granularity": granularity},
        )
        candles = [
            Candle(
                start=datetime.fromtimestamp(int(row[0]), tz=timezone.utc),
                low=float(row[1]),
                high=float(row[2]),
                open=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
        return sorted(candles, key=lambda candle: candle.start)

    def collect_prices(self) -> list[PriceSnapshot]:
        return [self.latest_price(Asset.BTC), self.latest_price(Asset.ETH)]

