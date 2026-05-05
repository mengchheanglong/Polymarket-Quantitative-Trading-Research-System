# References

These are design references only. They do not authorize live trading, wallet support, authenticated APIs, signing, or real order execution in this repository.

## Polymarket Official Docs

- Useful for: public market discovery, public CLOB orderbook reads, field names, endpoint behavior, observe-mode capture, raw snapshot design, token-id extraction, active-market filtering, liquidity diagnostics, and validating whether discovered markets are real public markets or demo fixtures. Also useful for reasoning about repeated public orderbook observations and bucket-state transitions in short BTC markets.
- Do not copy yet: authenticated trading flows, API key flows, wallet flows, signing, or order submission.
- Safety warning: public reads are acceptable for Phase 2 and Phase 3; anything involving credentials or execution is out of scope.
- Phase: Phase 2 and Phase 3 for public data capture; Phase 6 for source-aware readiness and market discovery audit; Phase 7 for directional-market discovery, token-id extraction, and public orderbook capture; later only if the project explicitly remains safety-reviewed.

## Gabagool Repo

- Useful for: the pair-cost arbitrage research idea that YES plus NO can be evaluated as a combined cost.
- Do not copy yet: live execution logic, private-key handling, wallet interactions, production trade loops, or order-management code.
- Safety warning: Phase 2 only simulates this idea with deterministic mock data or public orderbook reads.
- Phase: Phase 2 for paper-only strategy skeleton; Phase 3 or later for deeper research.
- Source: https://github.com/strongca22-cpu/gabagool

## Freqtrade

- Useful for: dry-run discipline, backtesting concepts, strategy interfaces, run isolation, replay/reporting ergonomics, dataset export patterns, source filters, diagnostics, threshold-sweep workflows, long-running research-session workflows, conservative paper position accounting, active-window market filtering, tiny-position paper risk limits, and skeptical analysis of whether reported gains are concentrated in a few outsized trades.
- Also useful for: defining stricter paper-readiness criteria for a promoted research preset without treating paper results as evidence of live tradability.
- Do not copy yet: exchange adapters, live order routers, credential handling, or deployment patterns for live bots.
- Safety warning: use as architecture inspiration, not as permission to add exchange execution.
- Phase: Phase 2 and Phase 3 for reporting, replay, and dry-run inspiration; Phase 6 for source-aware replay and dataset validation; later for richer backtesting design.
- Source: https://github.com/freqtrade/freqtrade.git

## NautilusTrader

- Useful for: professional event-driven trading engine architecture, run metadata, ledgers, fills, positions, lifecycle/state transitions, risk controls, market-data boundaries, observe loops, source-aware replay boundaries, diagnostics, research-session modeling, settlement-state handling, Markov-style state modeling, and future event replay design.
- Do not copy yet: broker adapters, live venues, account integrations, or execution clients.
- Safety warning: this project should borrow concepts such as explicit events and accounting, not live connectivity.
- Phase: Phase 3 as architectural inspiration for replay boundaries; Phase 6 for explicit demo/public data boundaries; later for richer event modeling.
- Source: http://github.com/nautechsystems/nautilus_trader

## ProbablyProfit

- Useful for: AI-assisted workflow, strategy-definition inspiration, and safe research workflow checklists.
- Do not copy yet: autonomous live execution, broker integrations, credential workflows, or production deployment patterns.
- Safety warning: any AI workflow in this repo must remain bounded to paper research and transparent logging.
- Phase: Phase 2 for documentation inspiration; Phase 6 for source-aware research workflow ideas; later for research workflow ideas.
- Source: http://github.com/randomness11/probablyprofit

## aulekator/Polymarket-BTC-15-Minute-Trading-Bot

Purpose:
Architecture reference for a BTC 15-minute Polymarket bot.

Useful ideas:
- 15-minute BTC-only strategy focus
- multi-signal architecture
- risk-first config
- monitoring with Prometheus/Grafana
- simulation/live separation pattern
- NautilusTrader-style architecture ideas
- strategy-audit workflow ideas such as checking whether mark-to-market behavior survives a stricter expiry-based settlement approximation
- conservative preset promotion ideas: narrower market filters, smaller paper exposure caps, and per-session validation before treating one strategy branch as the main research candidate

Do not copy yet:
- live execution
- wallet/private-key handling
- simulation/live toggle
- self-learning live optimization
- any code that can place orders

Relevant future phases:
- Phase 14+: strategy signal design
- Phase 15+: monitoring/reporting and strategy-audit workflow
- Phase 16+: conservative preset validation, multi-session side-correctness review, and paper-readiness criteria
- Phase 17+: live-executor design review, separate from paper engine

Source: https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot.git
