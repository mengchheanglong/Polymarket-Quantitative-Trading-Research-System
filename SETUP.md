# Setup

## Install

```powershell
python -m pip install -e ".[dev]"
```

Copy the safe sample config if you want local overrides:

```powershell
Copy-Item .env.example .env
```

## Run

Run the test suite:

```powershell
python -m pytest -q
```

Recommended first smoke test, fully offline:

```powershell
python -m src.main collect --demo
python -m src.main run-paper
python -m src.main report
python -m src.main trades
python -m src.main replay --strategy momentum
python -m src.main replay --strategy momentum --tiny
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
python -m src.main export --format csv --out exports --session-id <session_id>
python -m src.main export --format csv --out exports --source public
```

Collect public data:

```powershell
python -m src.main collect
```

Public collection tries Coinbase first and Kraken second for BTC/ETH price data. It uses public Polymarket endpoints for market and orderbook data. If DNS or outbound internet fails, the command records a failed raw snapshot and suggests `collect --demo`.

Observe public snapshots repeatedly without trading:

```powershell
python -m src.main observe --duration-minutes 5 --interval-seconds 15
python -m src.main observe --cycles 3 --interval-seconds 0
```

Inspect stored research sessions:

```powershell
python -m src.main sessions
python -m src.main session-report --latest
python -m src.main session-report --session-id <session_id>
python -m src.main research-report --latest
```

Summarize the local dataset:

```powershell
python -m src.main dataset
python -m src.main dataset --source demo
python -m src.main dataset --source public
python -m src.main dataset --source public --since "2026-05-04T00:00:00"
```

Check public replay readiness and inspect market discovery:

```powershell
python -m src.main readiness
python -m src.main readiness --source public
python -m src.main discover-markets --asset BTC
python -m src.main discover-markets --asset ETH
python -m src.main markets --source public
python -m src.main active-markets --source public
python -m src.main diagnostics --source public
python -m src.main sweep --strategy momentum --source public
```

Export local research data:

```powershell
python -m src.main export --format csv --out exports
python -m src.main export --format csv --out exports --source public
```

Run one paper cycle after public or demo collection:

```powershell
python -m src.main run-paper
```

Force offline demo collection through environment config:

```powershell
$env:USE_MOCK_DATA='true'; python -m src.main collect; Remove-Item Env:\USE_MOCK_DATA
```

Show the report:

```powershell
python -m src.main report
```

Show the simulated trade ledger:

```powershell
python -m src.main trades
```

Replay stored snapshots without external APIs:

```powershell
python -m src.main replay --strategy momentum --source demo
python -m src.main replay --strategy momentum --source public
python -m src.main replay --strategy pair-cost --source public
python -m src.main replay --strategy momentum --source public --session-id <session_id>
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode mark-to-market
python -m src.main replay --strategy momentum --source public --session-id <session_id> --close-mode approximate-expiry
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only
python -m src.main replay --strategy pair-cost --source public --session-id <session_id> --active-only
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only --min-seconds-to-expiry 30 --max-seconds-to-expiry 240
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only --close-mode approximate-expiry --tiny
```

Summarize replay/backtest and data quality:

```powershell
python -m src.main backtest-report
python -m src.main backtest-report --source public
```

List isolated experiment runs:

```powershell
python -m src.main runs
```

Compare strategies across stored runs:

```powershell
python -m src.main compare
python -m src.main compare --source public
python -m src.main compare --source demo
python -m src.main compare --source public --session-id <session_id>
python -m src.main compare --source public --session-id <session_id> --active-only
python -m src.main compare --source public --session-id <session_id> --active-only --tiny
```

Inspect skip reasons and market-level diagnostics:

```powershell
python -m src.main diagnostics --source demo
python -m src.main diagnostics --source public
python -m src.main diagnostics --strategy momentum --source public
python -m src.main diagnostics --strategy pair-cost --source public
```

Replay diagnostics now include timing buckets and seconds-to-expiry summaries. Reports and ledgers also distinguish open, closed, unresolved, and settlement-unavailable paper positions.
Phase 12 diagnostics also separate strategy skips from risk-blocked skips and report exposure over time, average trade size, largest single trade, max simultaneous positions, cooldown skips, and loss-limit skips.

Run a research-only threshold sweep on stored snapshots:

```powershell
python -m src.main sweep --strategy momentum --source demo
python -m src.main sweep --strategy pair-cost --source demo
python -m src.main sweep --strategy momentum --source public
python -m src.main sweep --strategy pair-cost --source public
python -m src.main sweep --strategy pair-cost --source public --session-id <session_id>
```

Report scopes:

```powershell
python -m src.main report --latest
python -m src.main report --run-id <run_id>
python -m src.main report --strategy momentum
python -m src.main report --all
```

Reset paper results while preserving raw snapshots:

```powershell
python -m src.main reset --paper-results
```

Delete all local research data:

```powershell
python -m src.main reset --all
```

Suggested safe workflow:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy momentum
python -m src.main observe --cycles 3 --interval-seconds 0
python -m src.main session-report --latest
python -m src.main dataset --source public
python -m src.main readiness
python -m src.main replay --strategy momentum --source public --session-id <session_id>
python -m src.main replay --strategy pair-cost --source public --session-id <session_id>
python -m src.main compare --source public --session-id <session_id>
python -m src.main export --format csv --out exports --session-id <session_id>
```

Run the paper-only pair-cost research skeleton:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy pair-cost
python -m src.main report
```

Public collection may fail if DNS or outbound internet is unavailable. When that happens, use `collect --demo`.

Public discovery process:

- Gamma public search for BTC/ETH directional markets.
- Active/open Gamma events with embedded markets.
- Active/open Gamma markets list.
- Direct `btc-updown-*` and `eth-updown-*` slug probes near the current time.
- Token extraction from public `clobTokenIds`, `tokens`, and outcome-token objects.
- Observe sessions track session ids, cycle counts, and partial-session completion safely.
- Public CLOB orderbook capture through public market-data endpoints only.
- Diagnostics and sweeps use stored snapshots only. They never call external APIs.
- `mark-to-market` and `approximate-expiry` close modes are paper-only research tools. They do not use authenticated APIs and do not imply real settlement certainty.
- `--active-only` limits replay and sweeps to valid active market windows with stored orderbooks, so not-started or expired markets are separated from true strategy failures.
- `--tiny` applies the paper-only small-bankroll profile. It is the safer default for evaluating whether a strategy survives tiny position sizing and strict exposure discipline.
