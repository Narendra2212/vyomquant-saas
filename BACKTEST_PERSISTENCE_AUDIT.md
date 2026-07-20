# BACKTEST PERSISTENCE AUDIT

## Source Information
**Target File**: `backend_app/routers/strategies.py`
**Endpoint**: `POST /api/strategies/backtest`
**Function**: `backtest`
**Line Range**: 1178-1541

## Runtime Flow
1. Receives the DAG Configuration (nodes, edges, initial capital, symbols).
2. Fetches historical CCXT `ohlcv` data or generates synthetic data.
3. Initializes the `DAGEngine`, `PortfolioEngine`, `ExecutionEngine`, and `RiskEngine`.
4. Executes the simulation through a loop iterating over data length (Line 1409).
5. Calculates core performance metrics (Total Trades, Win Rate, Drawdown, Profit Factor).
6. Constructs the JSON Response object (Line 1504).
7. Directly returns the calculated data to the frontend in memory.

## Persistence Findings
**There is no persistence layer for backtests.**
The execution path `routers/strategies.py -> backend/backtesting_engine.py` is entirely volatile.
- No `INSERT` statements to QuestDB.
- No `.table("backtests")` operations in Supabase.
- Redis is not used to cache these backtest results.

## Actual Data Structure (Volatile Return Payload)
The returned JSON from `/api/strategies/backtest`:
```json
{
  "total_return_pct": float,
  "final_equity": float,
  "total_trades": int,
  "win_rate_pct": float,
  "total_pnl": float,
  "max_drawdown_pct": float,
  "total_fees": float,
  "symbols_traded": int,
  "profit_factor": float,
  "sharpe_ratio": float,
  "sortino_ratio": float,
  "calmar_ratio": float,
  "equity": [{"time": int, "value": float}],
  "dag_results": {
    "nodes_count": int,
    "edges_count": int,
    "execution_order": ["node_id"],
    "action_nodes": ["node_id"],
    "node_results": {
      "node_id": {"samples": [float], "mean": float, "std": float}
    }
  },
  "execution_mode": "dag",
  "timeframe": "1h",
  "initial_capital": float
}
```
