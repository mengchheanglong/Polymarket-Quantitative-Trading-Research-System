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
python -m src.main observe --profile conservative-momentum --duration-minutes 60 --interval-seconds 15
```

`--duration-minutes` is wall-clock bounded. If a cycle takes longer than the requested interval, the next cycle starts immediately and the loop stops once the requested elapsed duration has been reached.

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
python -m src.main replay --strategy stuck-markov --source public --session-id <session_id> --active-only --tiny --close-mode approximate-expiry
python -m src.main replay --strategy momentum --source public --session-id <session_id> --active-only --tiny --close-mode approximate-expiry --momentum-preset conservative-tiny-momentum
python -m src.main replay --strategy momentum --preset conservative-tiny --source public --session-id <session_id> --active-only --close-mode approximate-expiry
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
python -m src.main close-mode-compare --strategy momentum --source public --session-id <session_id> --active-only --tiny
python -m src.main close-mode-compare --strategy stuck-markov --source public --session-id <session_id> --active-only --tiny
python -m src.main settlement-report --run-id <run_id>
python -m src.main markov-report --source public --session-id <session_id>
python -m src.main signal-audit --run-id <run_id>
python -m src.main side-audit --source public
python -m src.main side-audit --source public --details
python -m src.main close-divergence --strategy momentum --source public --session-id <session_id> --active-only --tiny
python -m src.main momentum-audit --source public --session-id <session_id> --tiny
python -m src.main conservative-report --source public
python -m src.main validate-conservative --source public
python -m src.main validate-conservative --preset conservative-entry-30-70 --source public
python -m src.main preset-report --preset conservative-entry-30-70 --source public
python -m src.main validate-candidate --candidate conservative-entry-40-75 --source public
python -m src.main candidate-report --candidate conservative-entry-40-75 --source public
python -m src.main validate-candidate --candidate conservative-up-only-40-75 --source public
python -m src.main candidate-report --candidate conservative-up-only-40-75 --source public
python -m src.main replay --strategy momentum --preset conservative-tiny-reverse --source public --session-id <session_id> --active-only --close-mode approximate-expiry
python -m src.main side-sweep --source public
python -m src.main candidate-ranking --source public
python -m src.main compare-candidates --source public
python -m src.main degradation-audit --candidate conservative-entry-30-70 --source public
python -m src.main strict-candidate-sweep --candidate conservative-entry-30-70 --source public
python -m src.main strict-candidate-ranking --source public
python -m src.main outsample-report --source public --since 2026-05-07T12:00:00Z
python -m src.main validation-target --source public --since 2026-05-07T12:00:00Z
python -m src.main consistency-audit --run-id <run_id>
```

Final archive note:

- The current momentum candidates are not live-ready and did not clear out-of-sample directional validation.
- Use [docs/FINAL_STATUS.md](docs/FINAL_STATUS.md) before resuming the project.
- Do not add wallet support, private keys, signing, authenticated trading APIs, or order execution to this repository.

Inspect skip reasons and market-level diagnostics:

```powershell
python -m src.main diagnostics --source demo
python -m src.main diagnostics --source public
python -m src.main diagnostics --strategy momentum --source public
python -m src.main diagnostics --strategy pair-cost --source public
```

Replay diagnostics now include timing buckets and seconds-to-expiry summaries. Reports and ledgers also distinguish open, closed, unresolved, and settlement-unavailable paper positions.
Phase 12 diagnostics also separate strategy skips from risk-blocked skips and report exposure over time, average trade size, largest single trade, max simultaneous positions, cooldown skips, and loss-limit skips.
Phase 13 adds profit-concentration metrics, low-price-entry contribution, settlement sanity checks, and close-mode comparison on the same stored session.
Phase 14 adds a paper-only BTC 5-minute stuck-state / Markov strategy that uses stored public orderbook snapshots to detect side prices stuck in the `0.70-0.80` zone while BTC moves underneath.
Phase 20 adds degradation and strict-candidate audit commands for `conservative-entry-30-70`. Use them after `validate-candidate` or `candidate-report` to check whether recent sessions are weakening, whether matched trades differ from mismatched trades, and whether any stricter stored-snapshot sub-candidate improves side correctness without increasing tail-risk concentration.

The `55%` side-correctness threshold is only a research continuation gate. Do not treat it as live readiness. A stronger paper-promising bar should include `100+` closed trades, `58-60%+` side correctness, positive expectancy, positive PnL excluding top trades, and no degrading trend.
Phase 21 adds out-of-sample validation. Pick an ISO cutoff after the sessions used to discover the variants, then run `outsample-report` and `validation-target`. The frozen variants are evaluated before and after that cutoff so later sessions test the hypothesis instead of re-optimizing it.
Phase 22 adds a consistency audit for approximate-expiry accounting. If a run shows positive PnL with `0%` side correctness, run `consistency-audit` first, then refresh candidate caches with `validate-candidate --refresh` and `candidate-report --refresh` before trusting the updated summaries.

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
- Tail-risk warnings such as `TAIL_RISK_CONCENTRATED_PROFIT` and `LOW_PRICE_BINARY_TAIL_STRATEGY` are research warnings, not success badges. Positive PnL can still be dominated by a few low-priced binary payouts.
- `markov-report` and `stuck-markov` use stored snapshots only. They do not call live endpoints during replay and they do not create an execution path.
- `signal-audit` checks whether the chosen momentum side matched the approximate-expiry direction implied by stored exchange prices near market start and expiry.
- `conservative-tiny` is the current main momentum research preset. It keeps the paper engine in tiny mode, prefers BTC 5-minute markets, tightens the edge/spread window, blocks very low and very high entries, and caps total paper exposure at `$5.00`.
- `conservative-report` replays that preset across all stored public sessions using stored snapshots only, then reports side correctness, top-trade concentration, PnL excluding top trades, BTC-vs-ETH comparisons, 5m-vs-15m comparisons, and paper-readiness verdicts.
- `validate-conservative` is the session runner for that report. It finds replay-ready public sessions, reuses matching conservative runs if they already exist, creates missing ones, and prints updated paper-readiness progress.
- `side-audit` is the directional deep-dive for that conservative branch. It summarizes matched vs mismatched approximate-expiry outcomes across stored conservative runs and can print a full accepted-trade feature table with entry price, spread, edge, entry/expiry exchange prices, pre/post entry exchange moves, and mismatch lag flags.
- `conservative-tiny-reverse` and `--reverse-signal` are paper-only research tools. They use the same tiny risk controls and entry filters, but flip `UP` to `DOWN` and `DOWN` to `UP` so the repo can test whether the conservative momentum side is backward.
- `side-sweep` compares the normal conservative branch, the reverse branch, and tighter filtered variants such as higher edge, lower spread, narrower entry-price bands, asset filters, side filters, duration filters, and tighter expiry windows.
- `candidate-ranking` ranks those paper candidates by side correctness, expectancy, closed trades, PnL excluding top trades, concentration, drawdown, exposure, and verdicts.
- `conservative-entry-30-70` is the older main paper validation candidate. It remains useful as a baseline, but recent cached comparisons showed its directional quality lagging stricter entry filters.
- `conservative-entry-40-75` is the current stricter paper hypothesis. It keeps BTC-only, 5-minute, tiny-risk, `min_edge=0.03`, `max_spread=0.02`, and the `60-180` second expiry window, but uses entry prices from `0.40` to `0.75`.
- `conservative-up-only-40-75` adds an `UP` side filter to the same `0.40-0.75` entry band. It is an experimental paper-only check for whether DOWN-side momentum is degrading the candidate.
- `close-divergence` compares trade-level outcomes between `mark-to-market` and `approximate-expiry` on the same stored session.
- `momentum-audit` runs paper-only filter experiments such as `UP` only, `DOWN` only, `BTC` only, `ETH` only, `5m` only, `15m` only, tighter expiry windows, and the `balanced-tiny-momentum` / `conservative-tiny-momentum` research presets.
- Tiny momentum remains under audit. Positive mark-to-market output is not enough if approximate-expiry is weak or directionally wrong.
