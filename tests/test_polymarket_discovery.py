from datetime import datetime, timedelta, timezone

from src.collectors.polymarket import PolymarketPublicCollector, _candidate_to_market
from src.http_client import HttpError
from src.main import main
from src.models import Asset, Candle, DiscoveredMarket, MarketClassification, OrderBook, OrderLevel, PriceSnapshot, TimingWindow
from src.storage.sqlite import SQLiteStore


class FixtureHttp:
    def __init__(self, *, event=None, events=None, markets=None, book_rows=None):
        self.event = event
        self.events = events or []
        self.markets = markets or []
        self.book_rows = book_rows or {}

    def get_json(self, base_url, path="", params=None):
        if path == "/public-search":
            return {"events": [self.event] if self.event else [], "pagination": {}}
        if path == "/events":
            return self.events
        if path.startswith("/events/slug/"):
            slug = path.rsplit("/", 1)[-1]
            if self.event and self.event.get("slug") == slug:
                return self.event
            return {}
        if path == "/markets":
            return self.markets
        if path == "/book":
            token_id = params["token_id"]
            row = self.book_rows.get(token_id)
            if row is None:
                raise HttpError(f"missing book for {token_id}")
            return row
        raise AssertionError(f"unexpected GET {base_url} {path} {params}")

    def post_json(self, base_url, path="", payload=None, params=None):
        if path != "/books":
            raise AssertionError(f"unexpected POST {base_url} {path} {payload} {params}")
        return [self.book_rows[item["token_id"]] for item in payload if item["token_id"] in self.book_rows]


def _event_market(**overrides):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    market = {
        "id": "btc-1",
        "question": "Bitcoin Up or Down - Jan 1, 1:00PM-1:05PM ET",
        "slug": "btc-updown-5m-1767272400",
        "description": "Bitcoin Up or Down short market.",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["tok-up", "tok-down"]',
        "active": True,
        "closed": False,
        "archived": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "startDateIso": now.isoformat().replace("+00:00", "Z"),
        "endDateIso": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    market.update(overrides)
    return market


def _event_payload(**market_overrides):
    market = _event_market(**market_overrides)
    return {
        "id": "event-btc-1",
        "slug": market["slug"],
        "title": market["question"],
        "description": market["description"],
        "active": True,
        "closed": False,
        "markets": [market],
    }


def _market_payload_tokens():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "id": "eth-1",
        "question": "Ethereum Higher or Lower - Jan 1, 1:00PM-1:15PM ET",
        "slug": "eth-higher-lower-15m-1767272400",
        "description": "Ethereum higher or lower short market.",
        "tokens": [
            {"token_id": "eth-up", "outcome": "Higher"},
            {"token_id": "eth-down", "outcome": "Lower"},
        ],
        "active": True,
        "closed": False,
        "archived": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "startDateIso": now.isoformat().replace("+00:00", "Z"),
        "endDateIso": (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
    }


def _book_row(token_id, best_bid=0.41, best_ask=0.43):
    return {
        "asset_id": token_id,
        "bids": [{"price": str(best_bid), "size": "100"}],
        "asks": [{"price": str(best_ask), "size": "120"}],
        "last_trade_price": "0.42",
    }


def _log_public_prices(store):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(now - timedelta(minutes=2), 99.0, 101.0, 100.0, 101.0, 1.0),
        Candle(now - timedelta(minutes=1), 100.0, 102.0, 101.0, 102.0, 1.0),
    ]
    for asset, price in ((Asset.BTC, 100_000.0), (Asset.ETH, 2_000.0)):
        snapshot = PriceSnapshot(asset, price, now, f"public:{asset.value}")
        store.log_price(snapshot)
        store.log_raw_snapshot(now, f"public:{asset.value}", asset.value, "exchange_price", {"price": price})
        store.log_candles(asset.value, candles, source=f"public:{asset.value}", observed_at=now)


def test_discovery_parses_gamma_event_with_clob_token_ids():
    collector = PolymarketPublicCollector("gamma", "clob", http=FixtureHttp(event=_event_payload()))

    candidates = collector.discover_market_candidates(asset_filter=Asset.BTC)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.asset_label == "BTC"
    assert candidate.classification == MarketClassification.CRYPTO_UP_DOWN
    assert candidate.up_token_id == "tok-up"
    assert candidate.down_token_id == "tok-down"
    assert candidate.token_status == "FOUND"
    assert candidate.accepted is True


def test_discovery_parses_gamma_markets_list_with_token_objects():
    collector = PolymarketPublicCollector("gamma", "clob", http=FixtureHttp(markets=[_market_payload_tokens()]))

    candidates = collector.discover_market_candidates(asset_filter=Asset.ETH)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.asset_label == "ETH"
    assert candidate.classification == MarketClassification.CRYPTO_HIGHER_LOWER
    assert candidate.up_token_id == "eth-up"
    assert candidate.down_token_id == "eth-down"
    assert candidate.token_status == "FOUND"


def test_discovery_marks_missing_token_ids():
    market = _event_market(clobTokenIds=None, outcomes='["Up", "Down"]')
    collector = PolymarketPublicCollector("gamma", "clob", http=FixtureHttp(event=_event_payload(clobTokenIds=None, outcomes='["Up", "Down"]')))

    candidates = collector.discover_market_candidates(asset_filter=Asset.BTC)
    captured, _ = collector.capture_orderbooks(candidates)

    assert market["slug"] == captured[0].slug
    assert captured[0].token_status == "MISSING"
    assert captured[0].orderbook_status == "SKIPPED_NO_TOKEN_IDS"
    assert captured[0].accepted is False


def test_orderbook_capture_success_and_failure():
    http = FixtureHttp(
        event=_event_payload(),
        book_rows={"tok-up": _book_row("tok-up"), "tok-down": _book_row("tok-down")},
    )
    collector = PolymarketPublicCollector("gamma", "clob", http=http)
    candidates = collector.discover_market_candidates(asset_filter=Asset.BTC)
    captured, books = collector.capture_orderbooks(candidates)

    assert captured[0].orderbook_status == "FOUND"
    assert set(books) == {"tok-up", "tok-down"}

    failing = FixtureHttp(event=_event_payload(), book_rows={})
    collector = PolymarketPublicCollector("gamma", "clob", http=failing)
    captured, books = collector.capture_orderbooks(collector.discover_market_candidates(asset_filter=Asset.BTC))
    assert captured[0].orderbook_status == "FAILED"
    assert books == {}


def test_readiness_verdict_no_public_orderbooks(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidate = DiscoveredMarket(
        market_id="btc-1",
        slug="btc-updown-5m-1767272400",
        title="Bitcoin Up or Down - Jan 1, 1:00PM-1:05PM ET",
        asset_label="BTC",
        classification=MarketClassification.CRYPTO_UP_DOWN,
        classification_reasons=("contains BTC", "contains up/down language", "has token ids"),
        token_status="FOUND",
        orderbook_status="FAILED",
        active=True,
        closed=False,
        accepting_orders=True,
        up_token_id="tok-up",
        down_token_id="tok-down",
        condition_id="cond-1",
        window_start=now,
        window_end=now + timedelta(minutes=5),
        source_url="mock://public",
        accepted=True,
        reason="accepted: directional crypto market; orderbook unavailable",
    )
    try:
        _log_public_prices(store)
        store.log_discovered_markets(now, "polymarket-public", [candidate])
        store.log_raw_snapshot(now, "polymarket-public", "BTC", "market_metadata", {"markets": [candidate.slug]})
        result = store.readiness(source_filter="public")
        assert result["verdict"] == "NO_PUBLIC_ORDERBOOKS"
    finally:
        store.close()


def test_readiness_verdict_ready_for_public_replay(tmp_path):
    db_path = tmp_path / "paper.sqlite3"
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidate = DiscoveredMarket(
        market_id="btc-1",
        slug="btc-updown-5m-1767272400",
        title="Bitcoin Up or Down - Jan 1, 1:00PM-1:05PM ET",
        asset_label="BTC",
        classification=MarketClassification.CRYPTO_UP_DOWN,
        classification_reasons=("contains BTC", "contains up/down language", "has token ids", "has public orderbook"),
        token_status="FOUND",
        orderbook_status="FOUND",
        active=True,
        closed=False,
        accepting_orders=True,
        up_token_id="tok-up",
        down_token_id="tok-down",
        condition_id="cond-1",
        window_start=now,
        window_end=now + timedelta(minutes=5),
        source_url="mock://public",
        accepted=True,
        reason="accepted: public orderbook captured",
    )
    try:
        _log_public_prices(store)
        store.log_discovered_markets(now, "polymarket-public", [candidate])
        store.log_raw_snapshot(now, "polymarket-public", "BTC", "market_metadata", {"markets": [candidate.slug]})
        market = _candidate_to_market(candidate)
        books = {
            "tok-up": OrderBook("tok-up", bids=(OrderLevel(0.41, 100),), asks=(OrderLevel(0.43, 120),), last_trade_price=0.42),
            "tok-down": OrderBook("tok-down", bids=(OrderLevel(0.57, 100),), asks=(OrderLevel(0.59, 120),), last_trade_price=0.58),
        }
        store.replace_collected_market_data(now, [market], books, source_name="polymarket-public")
        result = store.readiness(source_filter="public")
        assert result["verdict"] == "READY_FOR_PUBLIC_REPLAY"
    finally:
        store.close()


def test_replay_source_public_refuses_when_orderbook_data_missing(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candidate = DiscoveredMarket(
        market_id="btc-1",
        slug="btc-updown-5m-1767272400",
        title="Bitcoin Up or Down - Jan 1, 1:00PM-1:05PM ET",
        asset_label="BTC",
        classification=MarketClassification.CRYPTO_UP_DOWN,
        classification_reasons=("contains BTC", "contains up/down language", "has token ids"),
        token_status="FOUND",
        orderbook_status="FAILED",
        active=True,
        closed=False,
        accepting_orders=True,
        up_token_id="tok-up",
        down_token_id="tok-down",
        condition_id="cond-1",
        window_start=now,
        window_end=now + timedelta(minutes=5),
        source_url="mock://public",
        accepted=True,
        reason="accepted: token ids found but orderbooks missing",
    )
    try:
        _log_public_prices(store)
        store.log_discovered_markets(now, "polymarket-public", [candidate])
        market = _candidate_to_market(candidate)
        store.conn.execute(
            """
            INSERT INTO collected_markets
                (market_slug, market_id, title, asset, window_start, window_end, up_token_id,
                 down_token_id, source_url, is_mock, source_name, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market.slug,
                market.market_id,
                market.title,
                market.asset.value,
                now.isoformat().replace("+00:00", "Z"),
                (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                market.up_token_id,
                market.down_token_id,
                market.source_url,
                0,
                "polymarket-public",
                now.isoformat().replace("+00:00", "Z"),
            ),
        )
        store.conn.commit()
    finally:
        store.close()

    exit_code = main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public"])
    stdout = capsys.readouterr().out
    assert exit_code == 1
    assert "stored public orderbooks are missing" in stdout
