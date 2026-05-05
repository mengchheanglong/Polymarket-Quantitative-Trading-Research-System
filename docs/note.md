## Stuck-State / Markov Strategy Candidate

Source claim:
A bot trades only 5-minute BTC UP/DOWN markets. It waits for the market to reach around 74¢, then looks for the price being stuck for 3+ observation cycles. The theory is that Polymarket probability has lagged and may soon reprice.

Research hypothesis:
When Polymarket price remains in a high-confidence bucket, e.g. 0.70–0.80, for multiple cycles while BTC exchange price has moved, the next transition may be predictable enough to create a positive expectancy trade.

Test paper-only:
- bucket Polymarket prices into states
- build transition matrix from stored snapshots
- detect stuck states
- require tight spread
- require complete orderbook
- require active 5-minute BTC market only
- test tiny mode
- report win rate, expectancy, tail-risk concentration, and PnL excluding top trades

Safety:
No live trading, no wallet, no private key.