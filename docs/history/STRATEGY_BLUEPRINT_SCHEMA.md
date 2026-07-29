# STRATEGY BLUEPRINT SCHEMA

## Route Information
* **Endpoint:** `POST /api/strategies`
* **File:** `routers/strategies.py` (Line ~655)
* **Controller:** `create_strategy`

## Request Model
* **Model Name:** `StrategyBlueprint`
* **File:** `core/models/pydantic_models.py`
* **Extra Attributes:** `extra="allow"` is explicitly enabled.

## Schema Definition
### Required Fields
1. `name` (str)
2. `buy_logic` (LogicBlock)
3. `sell_logic` (LogicBlock)
4. `risk` (RiskParameters)

### Optional Fields
1. `symbol` (str, default: "BTC/USDT")
2. `timeframe` (str, default: "5m")
3. `ml_model_path` (Optional[str], default: None)
4. `indicators` (List[str], default: [])

### Validation Rules
* `buy_logic` and `sell_logic` must comply with `LogicBlock` (requires `operator` and `conditions` list).
* `risk` must comply with `RiskParameters` (requires `position_size_pct`, `stop_loss_pct`, `take_profit_pct` within strict bounded float ranges).

## Persistence Path
1. **DAG Extraction:** The route extracts `nodes` and `edges` using `body.get()`.
2. **DAG Validation:** The engine runs `validate_dag()` and `DAGCompiler.compile()`.
3. **Database Map:** Because there are no dedicated DB columns for DAG structures, the route forcefully injects them into the `buy_logic` JSONB dictionary:
   ```python
   buy_logic["_nodes"] = nodes
   buy_logic["_edges"] = edges
   buy_logic["_dag_version"] = 1
   ```
4. **Supabase Insert:** The final payload is stored in the `strategies` table.

## Example Valid Payload
```json
{
  "name": "DAG Strategy V1",
  "symbol": "BTCUSDT",
  "buy_logic": {
    "operator": "AND",
    "conditions": []
  },
  "sell_logic": {
    "operator": "AND",
    "conditions": []
  },
  "risk": {
    "position_size_pct": 0.1,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.1
  },
  "nodes": [{"id": "1", "type": "input"}],
  "edges": []
}
```
