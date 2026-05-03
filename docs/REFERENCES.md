# References

These are design references only. They do not authorize live trading, wallet support, authenticated APIs, signing, or real order execution in this repository.

## Polymarket Official Docs

- Useful for: public market discovery, public CLOB orderbook reads, field names, and endpoint behavior.
- Do not copy yet: authenticated trading flows, API key flows, wallet flows, signing, or order submission.
- Safety warning: public reads are acceptable for Phase 2; anything involving credentials or execution is out of scope.
- Phase: Phase 2 for public data references; later only if the project explicitly remains safety-reviewed.

## Gabagool Repo

- Useful for: the pair-cost arbitrage research idea that YES plus NO can be evaluated as a combined cost.
- Do not copy yet: live execution logic, private-key handling, wallet interactions, production trade loops, or order-management code.
- Safety warning: Phase 2 only simulates this idea with deterministic mock data or public orderbook reads.
- Phase: Phase 2 for paper-only strategy skeleton; Phase 3 or later for deeper research.

## Freqtrade

- Useful for: dry-run discipline, backtesting concepts, strategy interfaces, reporting ergonomics, and clear separation between simulation and execution.
- Do not copy yet: exchange adapters, live order routers, credential handling, or deployment patterns for live bots.
- Safety warning: use as architecture inspiration, not as permission to add exchange execution.
- Phase: Phase 2 for reporting and dry-run inspiration; later for richer backtesting design.

## NautilusTrader

- Useful for: professional event-driven trading engine architecture, ledgers, fills, positions, risk controls, and market-data boundaries.
- Do not copy yet: broker adapters, live venues, account integrations, or execution clients.
- Safety warning: this project should borrow concepts such as explicit events and accounting, not live connectivity.
- Phase: Later, after the paper simulator has stronger accounting and replay tests.

## ProbablyProfit

- Useful for: AI-assisted workflow and strategy-definition inspiration.
- Do not copy yet: autonomous live execution, broker integrations, credential workflows, or production deployment patterns.
- Safety warning: any AI workflow in this repo must remain bounded to paper research and transparent logging.
- Phase: Phase 2 for documentation inspiration; later for research workflow ideas.
