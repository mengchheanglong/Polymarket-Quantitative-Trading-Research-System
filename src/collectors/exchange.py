from __future__ import annotations

from datetime import datetime, timezone

from src.http_client import JsonHttpClient
from src.models import Asset, Candle, PriceSnapshot


COINBASE_PRODUCTS = {
    Asset.BTC: "BTC-USD",
    Asset.ETH: "ETH-USD",
}

KRAKEN_PRODUCTS = {
    Asset.BTC: "XBTUSD",
    Asset.ETH: "ETHUSD",
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


class KrakenCollector:
    """Collects unauthenticated BTC/ETH prices from Kraken public REST."""

    def __init__(self, base_url: str, http: JsonHttpClient | None = None):
        self.base_url = base_url
        self.http = http or JsonHttpClient()

    def latest_price(self, asset: Asset) -> PriceSnapshot:
        product = KRAKEN_PRODUCTS[asset]
        data = self.http.get_json(self.base_url, "/0/public/Ticker", {"pair": product})
        result = data.get("result") or {}
        if data.get("error"):
            raise ValueError(f"Kraken public ticker returned errors: {data['error']}")
        ticker = next(iter(result.values()))
        return PriceSnapshot(
            asset=asset,
            price=float(ticker["c"][0]),
            timestamp=datetime.now(timezone.utc),
            source=f"kraken:{product}",
        )

    def recent_candles(self, asset: Asset, granularity: int = 60) -> list[Candle]:
        product = KRAKEN_PRODUCTS[asset]
        interval = max(1, granularity // 60)
        data = self.http.get_json(
            self.base_url,
            "/0/public/OHLC",
            {"pair": product, "interval": interval},
        )
        if data.get("error"):
            raise ValueError(f"Kraken public OHLC returned errors: {data['error']}")
        result = data.get("result") or {}
        rows = next(value for key, value in result.items() if key != "last")
        candles = [
            Candle(
                start=datetime.fromtimestamp(int(row[0]), tz=timezone.utc),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[6]),
            )
            for row in rows[-20:]
        ]
        return sorted(candles, key=lambda candle: candle.start)

    def collect_prices(self) -> list[PriceSnapshot]:
        return [self.latest_price(Asset.BTC), self.latest_price(Asset.ETH)]


class FallbackExchangeCollector:
    """Primary/fallback public exchange collector with no auth requirements."""

    def __init__(self, collectors: list[object]):
        self.collectors = collectors

    def collect_prices(self) -> list[PriceSnapshot]:
        errors: list[str] = []
        for collector in self.collectors:
            try:
                return collector.collect_prices()
            except Exception as exc:
                errors.append(f"{collector.__class__.__name__}: {exc}")
        raise RuntimeError("; ".join(errors))

    def recent_candles(self, asset: Asset, granularity: int = 60) -> list[Candle]:
        errors: list[str] = []
        for collector in self.collectors:
            try:
                return collector.recent_candles(asset, granularity)
            except Exception as exc:
                errors.append(f"{collector.__class__.__name__}: {exc}")
        raise RuntimeError("; ".join(errors))
