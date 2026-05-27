# Final Research Status

Date: 2026-05-27

This repository is archived as a paper-only Polymarket BTC UP/DOWN research system. It should not be treated as a live-trading bot or a live-ready strategy.

## Bottom Line

The infrastructure became useful, but the tested strategy did not clear the research bar.

What worked:

- Public BTC-focused observe sessions collect usable Coinbase and Kraken prices.
- Public Polymarket BTC 5-minute markets and public orderbooks are captured.
- SQLite replay, run isolation, candidate caches, source filters, readiness checks, and consistency audits work.
- Exchange-quality guards catch stale or divergent BTC exchange prices.
- Paper risk controls keep tiny simulated positions and exposure caps.

What did not hold up:

- `conservative-entry-30-70` had positive PnL but weak directional correctness.
- `conservative-entry-40-75` improved early results but degraded with more out-of-sample data.
- `conservative-up-only-40-75` also degraded after later clean sessions.
- The final out-of-sample results stayed below the side-correctness and stability gates.

Do not live trade this strategy from this repository.

## Latest Candidate Takeaway

The stricter candidates still failed directional validation:

- `conservative-entry-40-75`
  - Aggregate side correctness after later sessions: about `54%`
  - Verdict: `NEEDS_MORE_DATA`, `DIRECTIONAL_SIGNAL_FAILED`

- `conservative-up-only-40-75`
  - Aggregate side correctness after later sessions: about `54.55%`
  - Verdict: `NEEDS_MORE_DATA`, `DIRECTIONAL_SIGNAL_FAILED`

Out-of-sample reports also showed degradation, weak side correctness, and fragile PnL after top-trade removal. That is not enough for paper promotion.

## Useful Commands

Install and test:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

Safety check:

```powershell
$env:LIVE_TRADING='true'; python -m src.main report; $code=$LASTEXITCODE; Remove-Item Env:\LIVE_TRADING; Write-Output "exit_code=$code"
```

Review latest stored research data:

```powershell
python -m src.main sessions
python -m src.main session-report --latest
python -m src.main compare-candidates --source public
```

Review the final candidate state:

```powershell
python -m src.main candidate-report --candidate conservative-entry-40-75 --source public
python -m src.main candidate-report --candidate conservative-up-only-40-75 --source public
python -m src.main outsample-report --candidate conservative-up-only-40-75 --source public --since 2026-05-07T12:00:00Z
```

Export local research data:

```powershell
python -m src.main export --format csv --out exports --candidate conservative-up-only-40-75
```

## If This Project Is Resumed

Start from the assumption that the current momentum presets are not proven. The best next research direction would be to create a new frozen paper candidate from the `near-flat pre-entry BTC move` filter and validate it out of sample. Do not increase position size or add live-trading code.

Any future continuation should preserve these constraints:

- public data only
- paper replay only
- no wallet support
- no private keys
- no signing
- no authenticated trading APIs
- no real order execution

## Repository Safety State

The codebase is designed to fail closed if live trading is requested. `LIVE_TRADING=true` should exit with a safety error. `.env.example` must not contain private key or wallet fields.

SQLite databases and exports are intentionally ignored by git.
