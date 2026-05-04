from datetime import datetime, timedelta, timezone

from src.collectors.polymarket import _candidate_to_market
from src.main import main
from src.models import Asset, Candle, DiscoveredMarket, MarketClassification, OrderBook, OrderLevel, PriceSnapshot
from src.storage.sqlite import SQLiteStore


def _seed_public_replayable_dataset(db_path):
    store = SQLiteStore(db_path)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        candles = [
            Candle(now - timedelta(minutes=2), 99.0, 101.0, 100.0, 101.0, 1.0),
            Candle(now - timedelta(minutes=1), 100.0, 102.0, 101.0, 102.0, 1.0),
            Candle(now, 101.0, 103.0, 102.0, 103.0, 1.0),
        ]
        for asset, price, settle in ((Asset.BTC, 100_000.0, 100_100.0), (Asset.ETH, 2_000.0, 2_010.0)):
            snapshot = PriceSnapshot(asset, price, now, f"public:{asset.value}")
            store.log_price(snapshot)
            store.log_raw_snapshot(now, f"public:{asset.value}", asset.value, "exchange_price", {"price": price})
            store.log_candles(asset.value, candles, source=f"public:{asset.value}", observed_at=now)
            store.log_raw_snapshot(
                now + timedelta(minutes=5),
                f"public:settlement:{asset.value}",
                asset.value,
                "settlement_price",
                {"price": settle},
            )
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
        store.log_discovered_markets(now, "polymarket-public", [candidate])
        store.log_raw_snapshot(now, "polymarket-public", "BTC", "market_metadata", {"markets": [candidate.slug]})
        market = _candidate_to_market(candidate)
        books = {
            "tok-up": OrderBook("tok-up", bids=(OrderLevel(0.39, 100),), asks=(OrderLevel(0.40, 100),), last_trade_price=0.395),
            "tok-down": OrderBook("tok-down", bids=(OrderLevel(0.59, 100),), asks=(OrderLevel(0.60, 100),), last_trade_price=0.595),
        }
        store.replace_collected_market_data(now, [market], books, source_name="polymarket-public")
    finally:
        store.close()


def test_diagnostics_output_and_market_section(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "pair-cost", "--source", "demo"]) == 0
    assert main(["--db", str(db_path), "diagnostics", "--source", "demo"]) == 0

    stdout = capsys.readouterr().out
    assert "Strategy diagnostics" in stdout
    assert "Source: demo" not in stdout
    assert "edge_distribution=" in stdout
    assert "pair_cost_distribution=" in stdout
    assert "Market-level diagnostics:" in stdout
    assert "demo-btc-updown-main" in stdout


def test_diagnostics_skip_reason_aggregation_and_config_visibility(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0
    assert main(["--db", str(db_path), "diagnostics", "--strategy", "momentum", "--source", "demo"]) == 0

    stdout = capsys.readouterr().out
    assert "config=min_edge=0.02" in stdout
    assert "max_spread=0.1" in stdout
    assert "skipped_by_reason=edge below threshold=1" in stdout
    assert "near_threshold=" in stdout


def test_sweep_uses_stored_snapshots_only_and_does_not_persist_runs(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0

    def fail_network(*_args, **_kwargs):
        raise AssertionError("sweep must not call external APIs")

    monkeypatch.setattr("src.http_client.JsonHttpClient.get_json", fail_network)
    monkeypatch.setattr("src.http_client.JsonHttpClient.post_json", fail_network)

    store = SQLiteStore(db_path)
    try:
        before_runs = len(store.run_rows())
    finally:
        store.close()

    assert main(["--db", str(db_path), "sweep", "--strategy", "momentum", "--source", "demo"]) == 0

    stdout = capsys.readouterr().out
    assert "Threshold sweep" in stdout
    assert "Research only" in stdout
    assert "min_edge=0.00; max_spread=0.02" in stdout

    store = SQLiteStore(db_path)
    try:
        assert len(store.run_rows()) == before_runs
    finally:
        store.close()


def test_pair_cost_distribution_buckets_and_sweep(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "pair-cost", "--source", "demo"]) == 0
    assert main(["--db", str(db_path), "diagnostics", "--strategy", "pair-cost", "--source", "demo"]) == 0
    stdout = capsys.readouterr().out
    assert "pair_cost_distribution=" in stdout
    assert "< 0.97" in stdout or "0.97 to 0.99" in stdout or "0.99 to 1.00" in stdout

    assert main(["--db", str(db_path), "sweep", "--strategy", "pair-cost", "--source", "demo"]) == 0
    sweep_out = capsys.readouterr().out
    assert "pair_cost_threshold=0.98" in sweep_out
    assert "failed_second_leg_probability=0.10" in sweep_out


def test_compare_source_filters_demo_and_public(tmp_path, capsys):
    db_path = tmp_path / "paper.sqlite3"

    assert main(["--db", str(db_path), "collect", "--demo"]) == 0
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "demo"]) == 0
    _seed_public_replayable_dataset(db_path)
    assert main(["--db", str(db_path), "replay", "--strategy", "momentum", "--source", "public"]) == 0

    assert main(["--db", str(db_path), "compare", "--source", "demo"]) == 0
    demo_out = capsys.readouterr().out
    assert "Source filter: demo" in demo_out
    assert "Aggregate by strategy:" in demo_out
    assert "momentum" in demo_out

    assert main(["--db", str(db_path), "compare", "--source", "public"]) == 0
    public_out = capsys.readouterr().out
    assert "Source filter: public" in public_out
    assert "Latest runs:" in public_out
    assert "momentum" in public_out
