# Polymarket BTC/ETH UP-DOWN Paper Agent

This is a safety-first paper-trading research agent for BTC/ETH Polymarket UP/DOWN markets. It collects public Coinbase/Kraken BTC/ETH prices, reads public Polymarket market and CLOB orderbook data when available, evaluates paper strategies, and records fake paper trades in SQLite.

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
python -m src.main runs
python -m src.main compare
python -m src.main observe --cycles 3 --interval-seconds 0
python -m src.main dataset --source public
python -m src.main discover-markets --asset BTC
python -m src.main discover-markets --asset ETH
python -m src.main readiness
python -m src.main markets --source public
python -m src.main export --format csv --out exports --source public
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

## Observe And Dataset Building

Observe mode repeatedly collects public snapshots and stores them locally. It does not simulate trades, place trades, manage wallets, or require credentials.

```powershell
python -m src.main observe --duration-minutes 5 --interval-seconds 15
python -m src.main observe --cycles 3 --interval-seconds 0
```

Each cycle attempts public exchange prices and public Polymarket market/orderbook data. Recoverable network failures are recorded as failed raw snapshots and the loop continues.

Summarize the local dataset:

```powershell
python -m src.main dataset
python -m src.main dataset --source demo
python -m src.main dataset --source public
```

Probe public BTC/ETH market discovery directly:

```powershell
python -m src.main discover-markets --asset BTC
python -m src.main discover-markets --asset ETH
python -m src.main discover-markets --asset all
```

Export local research data to CSV:

```powershell
python -m src.main export --format csv --out exports
python -m src.main export --format csv --out exports --source public
```

Exports include raw snapshot summaries, trades, skipped opportunities, runs, and equity snapshots. There are no wallet/private-key fields to export.

Suggested safe workflow:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy momentum
python -m src.main replay --strategy momentum --source demo
python -m src.main observe --duration-minutes 5 --interval-seconds 15
python -m src.main dataset --source public
python -m src.main readiness
python -m src.main replay --strategy momentum --source public
python -m src.main replay --strategy pair-cost --source public
python -m src.main compare
python -m src.main export --format csv --out exports --source public
```

## Source-Aware Research

Phase 6 separates deterministic demo data from observed public data. Commands that read stored snapshots can filter by source so backtests do not silently mix mock markets with real public observations:

```powershell
python -m src.main dataset --source demo
python -m src.main dataset --source public
python -m src.main replay --strategy momentum --source demo
python -m src.main replay --strategy momentum --source public
python -m src.main replay --strategy pair-cost --source public
python -m src.main backtest-report --source public
python -m src.main export --format csv --out exports --source public
```

Optional timestamp filters accept simple ISO timestamps:

```powershell
python -m src.main dataset --source public --since "2026-05-04T00:00:00"
python -m src.main replay --strategy momentum --source public --since "2026-05-04T00:00:00"
```

Public replay never falls back to demo data. If public Polymarket markets or orderbooks are missing, replay exits clearly and leaves demo data unused.

Check whether the dataset is ready for public replay:

```powershell
python -m src.main readiness
python -m src.main readiness --source public
```

Readiness reports whether exchange prices, Polymarket markets, orderbooks, overlapping timestamps/assets, spreads, and snapshot counts are sufficient. Verdicts include `READY_FOR_REPLAY`, `INSUFFICIENT_PUBLIC_DATA`, `MIXED_DEMO_AND_PUBLIC_DATA`, `MISSING_ORDERBOOKS`, and `MISSING_EXCHANGE_PRICES`.

Phase 7 adds public-market-specific readiness verdicts:

- `READY_FOR_PUBLIC_REPLAY`
- `NO_PUBLIC_CRYPTO_MARKETS`
- `NO_PUBLIC_TOKEN_IDS`
- `NO_PUBLIC_ORDERBOOKS`
- `INSUFFICIENT_OVERLAP`
- `INSUFFICIENT_SNAPSHOTS`

Audit discovered Polymarket markets:

```powershell
python -m src.main markets --source public
```

The market audit shows market id, slug, asset, title, source, classification, token status, orderbook status, first seen timestamp, latest seen timestamp, and accepted/rejected reason.

Public discovery uses public Gamma search, active/open Gamma events, active/open Gamma markets, and direct BTC/ETH `updown` slug probes near the current time. Token IDs are extracted from public fields such as `clobTokenIds`, `tokens`, and outcome-token objects when available. Public orderbook capture uses public CLOB market-data only. No wallet, private key, authenticated API, or order execution path is involved.

## Runs And Experiments

Every `run-paper` and `replay` command creates a new isolated run by default. Trades, skipped opportunities, fake balances, and equity snapshots are linked to that `run_id`, so momentum and pair-cost runs no longer get mixed accidentally.

Inspect runs:

```powershell
python -m src.main runs
```

Reports default to the latest run:

```powershell
python -m src.main report
python -m src.main report --latest
python -m src.main report --run-id <run_id>
python -m src.main report --strategy momentum
python -m src.main report --all
```

Compare stored strategies without calling external APIs:

```powershell
python -m src.main compare
```

Reset paper results while keeping raw snapshots:

```powershell
python -m src.main reset --paper-results
```

Delete all local research data, including raw snapshots:

```powershell
python -m src.main reset --all
```

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
