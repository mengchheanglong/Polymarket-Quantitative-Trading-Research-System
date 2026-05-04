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
- Source-aware replay can be restricted to `--source demo` or `--source public`; public replay does not silently fall back to demo data.
- Backtest reporting summarizes stored snapshots, fake trades, and data quality only, and can be filtered by source.
- Observe mode only collects public snapshots. It never simulates trades or executes trades.
- Dataset, readiness, market audit, and export commands can separate demo snapshots from public snapshots.
- Export writes local research CSV files only. There are no wallet, private-key, signer, or credential columns.
- The pair-cost strategy is a paper-only simulator. It models partial-fill and second-leg failure risk but cannot submit either leg anywhere.
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

Data quality metrics are paper research diagnostics only. They report snapshot counts, failed collection attempts, stale data, missing prices, missing orderbooks, wide spreads, low liquidity, and skipped fake opportunities by reason.

Source filters are safety and research-integrity controls. Use `--source public` when evaluating observed public data and `--source demo` when validating deterministic offline behavior. Mixed datasets are allowed in one SQLite file, but readiness and reports make the mix explicit so mock data is not mistaken for public market evidence.
