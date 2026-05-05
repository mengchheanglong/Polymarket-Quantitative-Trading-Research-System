# Safety

Phase 1 is paper-only by design.

- `DRY_RUN` defaults to `true`.
- Any attempt to set `DRY_RUN=false` exits with a safety error.
- Any live-trading flag such as `LIVE_TRADING=true`, `ALLOW_LIVE_TRADING=true`, `POLYMARKET_LIVE=true`, or `REAL_TRADES=true` exits with a safety error.
- There is no wallet creation or wallet management code.
- There is no wallet or private-key configuration requirement.
- There is no order-submission client.
- There is no signing, authenticated CLOB trading, or real order execution path.
- There are no Polymarket trading credential requirements.
- The `.env.example` file contains only paper-trading configuration.
- Demo mode is the recommended first smoke test because it works offline and never calls external APIs.
- Public data collection can fail when DNS or network access is unavailable. Failures are recorded as raw snapshots and the CLI suggests demo mode.
- Public collection uses unauthenticated exchange and Polymarket endpoints only.
- Replay mode reads stored SQLite snapshots and does not call external APIs.
- Diagnostics and sweep modes read stored SQLite snapshots only and do not call external APIs.
- Source-aware replay can be restricted to `--source demo` or `--source public`; public replay does not silently fall back to demo data.
- Backtest reporting summarizes stored snapshots, fake trades, and data quality only, and can be filtered by source.
- `discover-markets` and `observe` use only public Gamma and public CLOB market-data endpoints.
- Public market discovery records accepted and rejected candidates, token-id status, and public orderbook status for research auditability.
- If public token IDs or public orderbooks are missing, public replay refuses clearly instead of substituting demo data.
- Observe mode only collects public snapshots. It never simulates trades or executes trades.
- Observe duration is wall-clock bounded. That is an operational safeguard for research sessions only; it does not alter what data is collected or add any external control path.
- Observe mode creates research sessions, keeps partial data on interruption, and never deletes data automatically.
- Dataset, readiness, market audit, and export commands can separate demo snapshots from public snapshots.
- Session reports and research reports summarize stored public research data only. They do not authorize trading and do not call authenticated APIs.
- Export writes local research CSV files only. There are no wallet, private-key, signer, or credential columns.
- The pair-cost strategy is a paper-only simulator. It models partial-fill and second-leg failure risk but cannot submit either leg anywhere.
- Threshold sweeps are temporary paper-only research runs against stored snapshots. They do not modify the default config and do not prove live profitability.
- Session-scoped replay, diagnostics, sweeps, backtest reports, and exports stay inside the selected research session or explicit time window.
- Replay close modes such as `mark-to-market`, `expiry-if-known`, and `approximate-expiry` are paper-only accounting choices. They do not place orders and do not turn stored public data into a real settlement feed.
- `--active-only` is a research filter only. It changes which stored market windows are evaluated; it does not loosen thresholds and does not create any execution path.
- `--tiny` is a paper-only risk profile. It reduces fake notional and fake exposure; it does not connect the project to any venue or add an execution path.
- `markov-report` and `stuck-markov` are paper-only analytics over stored market snapshots. They do not query authenticated APIs, do not place orders, and do not convert public observations into a live system.
- `signal-audit`, `close-divergence`, and `momentum-audit` are paper-only audit commands. They replay stored snapshots or inspect stored runs only.
- `conservative-tiny` is a paper-only preset. It narrows momentum research to a smaller, stricter subset of the stored public market windows, but it does not create any execution path.
- `conservative-report` is a paper-only validation command. It replays stored sessions with the conservative preset only, aggregates skepticism metrics such as top-trade concentration and side correctness, and never calls authenticated APIs.
- `validate-conservative` is also paper-only. It may create additional stored replay runs, but only by replaying already-collected public snapshots inside SQLite. It still cannot place orders or touch authenticated APIs.
- Tail-risk warnings such as `TAIL_RISK_CONCENTRATED_PROFIT` and `LOW_PRICE_BINARY_TAIL_STRATEGY` are there to stop a misleading paper result from being mistaken for a stable edge.
- Tiny momentum is not trusted just because mark-to-market looks positive. Phase 15 explicitly checks accepted-trade edge, side correctness at approximate expiry, and whether close-mode disagreement is large enough to invalidate the paper result.
- Run IDs isolate paper experiments so reports do not accidentally mix unrelated strategy runs.
- `reset --paper-results` deletes fake trades, fake opportunities, fake balances, equity snapshots, and run metadata only. Raw public/demo snapshots are preserved.
- `reset --all` deletes local research data, including raw snapshots. It still does not touch wallets or external systems.

All simulated fills, balances, positions, and PnL are fake research records written to SQLite.

Report metrics are paper metrics only:

- `Current cash balance` is fake bankroll cash after simulated entries/exits.
- `Realized fake PnL` is closed simulated trade PnL.
- `Unrealized fake PnL` marks open simulated positions from stored paper values.
- `Total fake equity` is fake cash plus marked open simulated position value.
- `Max equity drawdown` is calculated from equity snapshots, not raw cash drawdown from opening positions.
- `Max position exposure` is reported separately from drawdown.
- `Average PnL per trade`, `average win`, `average loss`, `profit factor`, and `expectancy per trade` are paper metrics only. They are accounting summaries, not evidence of live tradability.
- Profit-concentration metrics such as top-trade PnL share, PnL excluding top trades, and low-price-entry contribution are skepticism tools. They are meant to expose whether modeled gains come from a few outsized binary payouts rather than repeatable small-profit behavior.
- Stuck-state / Markov transitions are research heuristics only. A side price that stays near `0.74` for several cycles while BTC moves can still be noise, stale liquidity, or a misleading microstructure artifact.
- `EXPIRED_UNRESOLVED` and `SETTLEMENT_UNAVAILABLE` are not fake profits. They are explicit paper outcomes for markets where replay cannot justify a settled result.
- `approximate-expiry` is still research-only. It infers settlement from nearby stored exchange prices and can be wrong even when the accounting looks attractive.
- `CLOSE_MODE_DIVERGENCE`, `ACCEPTED_EDGE_NEGATIVE`, and `DIRECTIONAL_SIGNAL_FAILED` are skepticism warnings, not trading signals. They exist to keep a paper result from being over-interpreted.
- `PAPER_PROMISING` is a research verdict, not a go-live decision. It is only a stricter paper-screening label and still does not authorize live trading, wallet setup, signing, or order execution.

Data quality metrics are paper research diagnostics only. They report snapshot counts, failed collection attempts, stale exchange prices, stale orderbooks, expired markets seen, invalid timestamps, missing prices, missing orderbooks, wide spreads, low liquidity, and skipped fake opportunities by reason.

Skip-heavy results are not a failure by themselves. A paper-only engine that rejects negative-edge or wide-spread public opportunities is behaving more safely than one that forces simulated fills to manufacture activity.

Not-started markets are not failed trade ideas. They are inventory observed before the valid entry window opened. Phase 11 separates those from active market windows so liquidity and strategy quality can be evaluated more honestly.

Normal replay exposure can be much larger than a tiny real bankroll would tolerate. Phase 12 adds explicit paper caps for per-trade size, total open exposure, open-position count, market concentration, per-session trade count, cooldown after losses, and session or daily loss ceilings. These are discipline controls for research only.

Source filters are safety and research-integrity controls. Use `--source public` when evaluating observed public data and `--source demo` when validating deterministic offline behavior. Mixed datasets are allowed in one SQLite file, but readiness and reports make the mix explicit so mock data is not mistaken for public market evidence.
