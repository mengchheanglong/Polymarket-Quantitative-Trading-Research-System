from datetime import datetime, timezone

import pytest

from src.collectors.exchange import CoinbaseCollector, FallbackExchangeCollector, KrakenCollector
from src.main import main


class FakeHttp:
    def __init__(self, failures: set[str] | None = None):
        self.failures = failures or set()

    def get_json(self, base_url, path="", params=None):
        key = f"{base_url}{path}"
        if key in self.failures:
            raise RuntimeError(f"forced failure for {key}")
        if "coinbase" in base_url and path.endswith("/ticker"):
            return {"price": "100.0", "time": "2026-01-01T00:00:00Z"}
        if "coinbase" in base_url and path.endswith("/candles"):
            return [[1767225600, 99, 101, 100, 100, 1]]
        if "kraken" in base_url and path.endswith("/Ticker"):
            return {"error": [], "result": {"XXBTZUSD": {"c": ["101.0", "1"]}}}
        if "kraken" in base_url and path.endswith("/OHLC"):
            return {"error": [], "result": {"XXBTZUSD": [[1767225600, "99", "101", "98", "101", "100", "1", "1"]], "last": 1}}
        raise AssertionError(f"unexpected request {base_url} {path} {params}")


def test_public_collector_falls_back_to_kraken_when_coinbase_fails():
    http = FakeHttp(failures={"coinbase/products/BTC-USD/ticker"})
    collector = FallbackExchangeCollector(
        [
            CoinbaseCollector("coinbase", http=http),
            KrakenCollector("kraken", http=http),
        ]
    )

    prices = collector.collect_prices()

    assert prices[0].source == "kraken:XBTUSD"
    assert prices[0].price == 101.0


def test_public_collector_reports_clear_failure_when_all_sources_fail():
    class BrokenCollector:
        def collect_prices(self):
            raise RuntimeError("offline")

    collector = FallbackExchangeCollector([BrokenCollector(), BrokenCollector()])

    with pytest.raises(RuntimeError, match="offline"):
        collector.collect_prices()


def test_public_collection_graceful_failure_records_raw_snapshot(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"

    def fail_http(*_args, **_kwargs):
        raise RuntimeError("dns failed")

    monkeypatch.setattr("src.http_client.JsonHttpClient.get_json", fail_http)

    exit_code = main(["--db", str(db_path), "collect"])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "Try demo mode: python -m src.main collect --demo" in stderr

    from src.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path)
    try:
        rows = store.raw_snapshot_rows()
        assert rows[0]["snapshot_type"] == "collection_status"
        assert rows[0]["status"] == "failed"
        assert "dns failed" in rows[0]["error_message"]
    finally:
        store.close()
