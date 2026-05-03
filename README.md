# Polymarket BTC/ETH UP-DOWN Paper Agent

Phase 1 is a safety-first paper-trading research agent for BTC/ETH Polymarket UP/DOWN markets. It collects public Coinbase BTC/ETH prices, reads public Polymarket market and CLOB orderbook data, evaluates a simple momentum signal, and records fake paper trades in SQLite.

It is intentionally not a trading bot. It cannot submit orders, does not manage wallets, does not require a wallet, does not require a private key, does not require Polymarket trading credentials, and defaults to `DRY_RUN=true`.

## Recommended Smoke Test

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m src.main collect --demo
python -m src.main run-paper
python -m src.main report
python -m src.main trades
python -m src.main replay --strategy momentum
python -m src.main backtest-report
```

## What It Tracks

- Public BTC/ETH price snapshots
- Candidate Polymarket UP/DOWN opportunities
- Skipped trades with reasons
- Fake entries and exits
- Fake bankroll and position size
- Entry price, fees, slippage, spread, failed-fill assumptions
- Resolution result and fake PnL
- Starting balance, current cash balance, realized fake PnL, unrealized fake PnL, total fake equity
- Max equity drawdown, max position exposure, win rate, average edge, skipped-trade count

## Paper Strategies

Momentum remains the default:

```powershell
python -m src.main run-paper --strategy momentum
```

Phase 2 also includes a paper-only pair-cost arbitrage skeleton:

```powershell
python -m src.main run-paper --strategy pair-cost
```

Pair-cost checks whether equal-sized UP and DOWN legs can be simulated below `PAIR_COST_THRESHOLD` after fake slippage. It is theoretical research code only. The idea can fail from partial fills, fees, slippage, bad liquidity, market resolution issues, and failed second-leg execution. This repository still has no live execution path.

## Trade Ledger

Inspect simulated trades and skipped opportunities:

```powershell
python -m src.main trades
```

## Replay And Backtest Reports

Phase 3 stores raw public/demo snapshots in SQLite, including exchange prices, Polymarket market metadata, orderbooks, collection status, and failed collection errors.

Replay runs a selected paper strategy against stored snapshots only:

```powershell
python -m src.main replay --strategy momentum
python -m src.main replay --strategy pair-cost
```

Backtest reporting summarizes the stored dataset and replay output:

```powershell
python -m src.main backtest-report
```

The backtest report includes snapshot count, markets seen, opportunities, accepted fake trades, skipped opportunities, realized fake PnL, win rate, equity drawdown, position exposure, average edge, source coverage, and data quality metrics.

Data quality metrics include failed collection attempts, stale snapshots, missing prices, missing orderbooks, wide spreads, low-liquidity markets, and skipped opportunities by reason. These metrics help separate strategy weakness from incomplete or poor-quality data.

## Data Modes

Public collection uses only unauthenticated endpoints.

- Coinbase Exchange public price data for BTC/ETH
- Kraken public REST price data as a fallback
- Polymarket public Gamma discovery endpoints
- Polymarket public CLOB orderbook reads

Public collection can fail because of DNS or network issues in the local environment. Demo mode is the recommended first smoke test because it is deterministic, offline, and never calls external APIs.

Use either of these:

```powershell
python -m src.main collect --demo
```

```powershell
$env:USE_MOCK_DATA='true'; python -m src.main collect; Remove-Item Env:\USE_MOCK_DATA
```

## Market Discovery Limitation

Short-duration Polymarket crypto UP/DOWN markets can be unreliable to discover from public listings because they are short-lived and may not always appear in generic search responses. The collector uses public Gamma search/events endpoints, probes nearby common UP/DOWN slug patterns, and falls back only when explicitly requested via `--demo`, `USE_MOCK_DATA=true`, or `USE_DEMO_MARKETS=true`.

Demo markets are clearly marked as mock data and are for simulator testing only.

## Safety

See [SAFETY.md](SAFETY.md). Live execution is intentionally disabled.

See [docs/REFERENCES.md](docs/REFERENCES.md) for design references and what is intentionally out of scope.
