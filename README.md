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
python -m src.main diagnostics --source demo
python -m src.main sweep --strategy momentum --source demo
python -m src.main runs
python -m src.main compare
python -m src.main observe --cycles 3 --interval-seconds 0
python -m src.main sessions
python -m src.main session-report --latest
python -m src.main research-report --latest
python -m src.main dataset --source public
python -m src.main discover-markets --asset BTC
python -m src.main discover-markets --asset ETH
python -m src.main readiness
python -m src.main markets --source public
python -m src.main diagnostics --source public
python -m src.main sweep --strategy momentum --source public
python -m src.main export --format csv --out exports --session-id <session_id>
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
- Average PnL per trade, average win, average loss, profit factor, expectancy per trade
- Risk-blocked trades, max exposure as a bankroll percentage, and session loss-limit status
- Profit concentration, tail-risk warnings, low-price entry contribution, and run-validity verdicts

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

Phase 10 tightens market lifecycle and stale-data handling. Stale data is no longer measured against the current wall clock. Reports now break it into stale exchange prices, stale orderbooks, expired markets seen, invalid timestamps, and total stale snapshot issues relative to the replay/session window.

## Diagnostics And Threshold Sweeps

Phase 8 adds paper-only diagnostics and threshold sweeps:

```powershell
python -m src.main diagnostics --source public
python -m src.main diagnostics --strategy momentum --source public
python -m src.main diagnostics --strategy pair-cost --source public
python -m src.main sweep --strategy momentum --source public
python -m src.main sweep --strategy pair-cost --source public
```

Diagnostics summarize total opportunities, accepted trades, skipped opportunities, skipped-by-reason counts, edge and spread stats, pair-cost stats, edge-distribution buckets, pair-cost buckets, near-threshold opportunities, and market-level diagnostics.

Timing diagnostics now include seconds-to-expiry and timing buckets such as `too_early`, `valid_window`, `too_late`, `expired`, and `missing_expiry`.

Reports now show the active paper thresholds and assumptions, including `MIN_EDGE`, `MAX_SPREAD`, `PAIR_COST_THRESHOLD`, fees, slippage, position sizing, and failed-fill assumptions.

Accepted `0` trades can be the correct result. If diagnostics show negative edge, wide spreads, or pair cost above threshold, the safe paper engine should keep skipping instead of forcing bad simulated entries.

Threshold sweeps are research only. They replay stored snapshots with temporary paper-only overrides and do not modify the default config, do not call external APIs, and do not prove live profitability even if a row looks better than the default.

Phase 9 adds long-running research sessions. Replay, diagnostics, sweeps, compare, backtest reporting, and export can all be tied back to one observed public-data session instead of mixing later data into the same analysis window.

Phase 10 also adds paper position lifecycle handling:

- `OPEN`
- `CLOSED_BY_MARK_TO_MARKET`
- `CLOSED_BY_EXPIRY`
- `EXPIRED_UNRESOLVED`
- `SETTLEMENT_UNAVAILABLE`

Replay close modes are paper-only:

```powershell
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode none
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode mark-to-market
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode expiry-if-known
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode approximate-expiry
```

`mark-to-market` uses the latest stored midpoint before expiry or session end. `approximate-expiry` uses stored exchange prices near market start and expiry to infer a research-only settlement. It may be wrong and is not proof of live profitability. Unresolved or settlement-unavailable positions are not counted as fake profits.

Phase 11 adds active-market filtering and liquidity diagnostics so replay can focus on tradeable windows instead of treating not-started markets as failed opportunities:

```powershell
python -m src.main active-markets --source public --session-id <session_id>
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only
python -m src.main replay --strategy pair-cost --source public --session-id <session_id> --active-only
python -m src.main diagnostics --source public --session-id <session_id> --active-only
python -m src.main sweep --strategy momentum --source public --session-id <session_id> --active-only
```

Optional timing filters narrow replay to a seconds-to-expiry band:

```powershell
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only --min-seconds-to-expiry 30 --max-seconds-to-expiry 240
```

`active-markets` reports active BTC/ETH counts, 5m vs 15m mix, lifecycle counts, complete YES/NO orderbook counts, missing asks/bids, wide spreads, low liquidity, pair cost, and fee/slippage-adjusted pair cost. Missing asks matter: a pair-cost setup without both asks is not executable even in paper research.

Phase 12 adds tiny-position paper-risk controls so replay can be evaluated against a small-bankroll plan instead of the older larger notional defaults:

```powershell
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only --close-mode approximate-expiry --tiny
python -m src.main replay --strategy pair-cost --source public --session-id <session_id> --active-only --tiny
python -m src.main compare --source public --session-id <session_id> --active-only
python -m src.main compare --source public --session-id <session_id> --active-only --tiny
```

The tiny profile applies a paper-only risk template:

- `MAX_TRADE_USD=1.00`
- `MAX_TOTAL_EXPOSURE_USD=10.00`
- `MAX_OPEN_POSITIONS=5`
- `MAX_TRADES_PER_MARKET=1`
- `MAX_TRADES_PER_SESSION=100`
- `SESSION_LOSS_LIMIT_USD=5.00`
- `DAILY_LOSS_LIMIT_USD=10.00`
- `COOLDOWN_AFTER_LOSS_SECONDS=300`
- `MIN_SECONDS_TO_EXPIRY=30`
- `MAX_SECONDS_TO_EXPIRY=240`

Tiny mode does not create execution. It only changes paper sizing and replay admission rules. If a trade is blocked by exposure, open-position count, market count, session loss, daily loss, or cooldown, diagnostics record that explicitly.

Win rate alone is not enough. Expectancy per trade, average win versus average loss, profit factor, and realized PnL all matter. A strategy can show a decent win rate and still be weak once costs and losses are measured honestly.

Phase 13 adds tail-risk and settlement sanity checks for tiny-mode results:

```powershell
python -m src.main report --latest
python -m src.main backtest-report --source public --session-id <session_id> --active-only --tiny
python -m src.main close-mode-compare --strategy momentum --source public --session-id <session_id> --active-only --tiny
python -m src.main settlement-report --run-id <run_id>
```

New report fields include top-1, top-3, top-5, and top-10%-trade PnL concentration, PnL excluding those top trades, median trade PnL, largest win/loss, win-loss payout ratio, and the contribution from very low entry prices such as `< 0.05` and `< 0.03`.

These warnings matter:

- `TAIL_RISK_CONCENTRATED_PROFIT`: a few trades explain most of the profit, or average edge stays negative while total PnL is positive.
- `LOW_PRICE_BINARY_TAIL_STRATEGY`: most modeled profit comes from very low-priced binary entries with capped losses and rare large payouts.
- `SETTLEMENT_APPROXIMATION_UNCERTAIN`: approximate-expiry closing used nearby exchange prices, not a true market settlement feed.

Positive tiny-mode PnL is not enough by itself. If profit disappears after removing the top few trades, or if average edge is negative while profits are positive, treat the run as research-only and not evidence of a stable edge.

## Observe And Dataset Building

Observe mode repeatedly collects public snapshots and stores them locally. It does not simulate trades, place trades, manage wallets, or require credentials.

```powershell
python -m src.main observe --duration-minutes 5 --interval-seconds 15
python -m src.main observe --cycles 3 --interval-seconds 0
```

Each cycle attempts public exchange prices and public Polymarket market/orderbook data. Recoverable network failures are recorded as failed raw snapshots and the loop continues.

Each observe run creates a `session_id` and records start/end time, cycle counts, sources used, and snapshot totals.

```powershell
python -m src.main sessions
python -m src.main session-report --latest
python -m src.main session-report --session-id <session_id>
python -m src.main research-report --latest
```

`observe` handles `Ctrl+C` gracefully, finalizes the partial session, preserves data, and prints the next analysis command.

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
python -m src.main export --format csv --out exports --session-id <session_id>
```

Exports include raw snapshot summaries, trades, skipped opportunities, runs, and equity snapshots. There are no wallet/private-key fields to export.

Suggested safe workflow:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy momentum
python -m src.main replay --strategy momentum --source demo
python -m src.main observe --duration-minutes 60 --interval-seconds 15
python -m src.main session-report --latest
python -m src.main dataset --source public
python -m src.main readiness
python -m src.main replay --strategy momentum --source public --session-id <session_id>
python -m src.main replay --strategy pair-cost --source public --session-id <session_id>
python -m src.main diagnostics --source public --session-id <session_id>
python -m src.main sweep --strategy momentum --source public --session-id <session_id>
python -m src.main sweep --strategy pair-cost --source public --session-id <session_id>
python -m src.main compare --source public --session-id <session_id>
python -m src.main export --format csv --out exports --session-id <session_id>
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

Session-aware analysis commands can use the stored session window directly:

```powershell
python -m src.main replay --strategy momentum --source public --session-id <session_id>
python -m src.main diagnostics --source public --session-id <session_id>
python -m src.main sweep --strategy pair-cost --source public --session-id <session_id>
python -m src.main backtest-report --source public --session-id <session_id>
python -m src.main compare --source public --session-id <session_id>
```

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
python -m src.main compare --source public
python -m src.main compare --source demo
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
