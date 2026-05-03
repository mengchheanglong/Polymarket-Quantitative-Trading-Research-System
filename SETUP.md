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
python -m src.main backtest-report
python -m src.main runs
python -m src.main compare
python -m src.main observe --cycles 3 --interval-seconds 0
python -m src.main dataset
python -m src.main export --format csv --out exports
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

Summarize the local dataset:

```powershell
python -m src.main dataset
```

Export local research data:

```powershell
python -m src.main export --format csv --out exports
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
python -m src.main replay --strategy momentum
python -m src.main replay --strategy pair-cost
```

Summarize replay/backtest and data quality:

```powershell
python -m src.main backtest-report
```

List isolated experiment runs:

```powershell
python -m src.main runs
```

Compare strategies across stored runs:

```powershell
python -m src.main compare
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
python -m src.main dataset
python -m src.main replay --strategy momentum
python -m src.main compare
python -m src.main export --format csv --out exports
```

Run the paper-only pair-cost research skeleton:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy pair-cost
python -m src.main report
```

Public collection may fail if DNS or outbound internet is unavailable. When that happens, use `collect --demo`.
