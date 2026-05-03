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
```

Collect public data:

```powershell
python -m src.main collect
```

Public collection tries Coinbase first and Kraken second for BTC/ETH price data. It uses public Polymarket endpoints for market and orderbook data. If DNS or outbound internet fails, the command records a failed raw snapshot and suggests `collect --demo`.

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

Run the paper-only pair-cost research skeleton:

```powershell
python -m src.main collect --demo
python -m src.main run-paper --strategy pair-cost
python -m src.main report
```

Public collection may fail if DNS or outbound internet is unavailable. When that happens, use `collect --demo`.
