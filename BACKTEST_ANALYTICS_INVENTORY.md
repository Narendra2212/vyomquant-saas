# BACKTEST ANALYTICS INVENTORY

## Objective
Audit the execution metrics returned by the DAG Backtest Engine (`routers/strategies.py` and `core/execution_engine.py`) and verify their correctness.

## Metric Audit

| Metric | Status | Source/Calculation | Verification Notes |
|--------|--------|---------------------|--------------------|
| **total_trades** | ✅ Present | `stats['total_trades']` | Derived from length of `ExecutionEngine.trade_log`. |
| **win_rate** | ✅ Present | `stats['win_rate_pct']` | Ratio of winning trades to total trades. |
| **pnl** | ✅ Present | `stats['total_pnl']` | Sum of PnL across all trades. |
| **drawdown** | ✅ Present | `stats['max_drawdown_pct']` | Tracked via `ExecutionEngine.peak_equity` vs `current_equity`. |
| **sharpe** | ⚠️ Partial | `routers/strategies.py` | Calculated manually using `avg_win`/`avg_loss`. Assumes standard trade lengths. Not accurately annualized. |
| **sortino** | ❌ Mocked | `routers/strategies.py` | Hardcoded as `sharpe_ratio * 1.2`. Not dynamically calculated from downside deviation. |
| **calmar** | ✅ Present | `routers/strategies.py` | Calculated as `total_return_pct / max_drawdown_pct`. |
| **expectancy** | ❌ Missing | - | Present in the legacy VectorBT engine, but completely missing from the DAG backtest payload. |
| **profit_factor** | ⚠️ Partial | `stats.get('profit_factor')` | The `ExecutionEngine` does not actually compute `profit_factor`. It falls back to `1.0` in the payload. |

## Deficiencies Identified
1. **Sortino Ratio**: Mocked logic (`sharpe_ratio * 1.2`). Must be replaced with actual downside deviation calculation.
2. **Expectancy**: Missing from the API response payload in DAG execution mode.
3. **Profit Factor**: Not implemented in `ExecutionEngine.get_stats()`, causing a fallback to a default value of `1.0`.
4. **Sharpe Ratio**: Uses trade-level average win/loss rather than daily return standard deviation.

**Verdict**: The Analytics payload requires a patch to accurately reflect advanced quantitative metrics.
