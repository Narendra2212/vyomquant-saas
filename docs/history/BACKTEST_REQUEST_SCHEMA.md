# BACKTEST REQUEST SCHEMA

## Route Information
* **Endpoint:** `POST /api/strategies/backtest`
* **File:** `routers/strategies.py` (Line ~1162)
* **Controller:** `backtest`
* **Executor:** `_run_backtest_sync`

## Request Model
* **Model Name:** `BacktestRequest`
* **File:** `core/models/pydantic_models.py`

## Schema Definition
### Required Fields
1. `symbols` (List[str], default: `["BTCUSDT"]`)
2. `timeframe` (str, default: `"1h"`)
3. `initial_capital` (float)
4. `trade_size_pct` (float, <= 1.0)
5. `stop_loss_pct` (float, <= 1.0)
6. `take_profit_pct` (float, <= 1.0)
7. `ml_threshold` (float, <= 1.0)
8. `params` (Dict[str, Any])

### Optional Fields
1. `dag` (Optional[DAGConfig])
2. `strategies` (Optional[List[str]])
3. `strategy_id` (Optional[str])

## Resolution Logic (from `_run_backtest_sync`)
1. **DAG Config (PRIMARY):** If `request.get("dag")` exists and contains nodes, it executes the DAG mode. Symbols and timeframe are extracted from `dag_config.get("symbols")` and `dag_config.get("timeframe")`.
2. **Strategies List (FALLBACK):** If `dag` is absent, it reads `strategies` or `strategy_id` to run in legacy backward-compatibility mode. It resolves `symbols` and `timeframe` from the root of the request payload.

## DAGConfig Structure
```python
class DAGConfig(BaseModel):
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    symbols: List[str]
    timeframe: Optional[str]
```

## Example Valid Payload
```json
{
  "symbols": ["BTCUSDT"],
  "timeframe": "1h",
  "initial_capital": 10000.0,
  "trade_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.04,
  "ml_threshold": 0.0,
  "params": {},
  "dag": {
    "nodes": [{"id": "1", "type": "input"}],
    "edges": [],
    "symbols": ["BTCUSDT"],
    "timeframe": "1h"
  }
}
```

## Example Invalid Payload (Current Frontend output)
```json
{
  "symbols": ["BTCUSDT"],
  "timeframe": "1h",
  "initial_capital": 10000.0,
  "trade_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.04,
  "ml_threshold": 0.0,
  "params": {
    "dag_nodes": [{"id": "1"}],
    "dag_edges": []
  }
}
```
*(Fails because `dag` is missing and `strategies` list is empty/missing, triggering a fallback failure).*
