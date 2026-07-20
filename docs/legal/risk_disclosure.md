# Risk Disclosure Statement — Aerora Quant Platform Beta

*Effective Date: June 5, 2026*

Quantitative, algorithmic, and digital asset trading involves substantial risk of loss. This Risk Disclosure Statement highlights the primary risk vectors associated with using the Aerora Quant Platform.

## 1. Algorithmic and Execution Risk
- Automated strategies execute at high speeds and can generate substantial order volumes. 
- Programming errors, API rate limit exhaustion, connection dropouts, or execution delays may result in unintended trades, multi-order loops, or capital loss.

## 2. Platform Status & Simulator Fidelity
- The Platform is in Beta. Backtests are based on historical tick data and do not guarantee future performance.
- Simulated paper trading attempts to model exchange order matching, but cannot fully replicate real market depth, latency, order queue priorities, slippage, and fee calculations.

## 3. Leverage and Margin Safety
- Trading with leverage amplifies both gains and losses. 
- Rapid market swings can deplete free margin instantly, triggering global circuit breakers, margin calls, or liquidation events on your exchange account.

## 4. API Key Custody & System Circuit Breakers
- Saving API keys in the Exchange Vault grants the Platform permissions to route trade instructions.
- Our Risk Settings panel provides global kill switches and daily loss limits. However, you must monitor your exchange account directly. 

## 5. Market Volatility & Black Swan Events
- Cryptocurrencies and digital assets are highly volatile. Extreme price shifts, regulatory crackdowns, or technical failures can disrupt exchange order books, rendering algorithms ineffective or causing massive execution slippage.
