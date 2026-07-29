# LOGIC RUNTIME SCHEMA

This document details the exact dictionary schema received by `LogicExecutor.execute(node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame)` at runtime.

```python
# The `node` argument
{
    "id": "logic-1",
    "type": "logic",
    "label": "RSI > 70",
    "indicator": None,
    "params": {
        "threshold": 70.0
    },
    "model_id": None,
    "confidence_threshold": 0.7,
    "operator": "GT",
    "action": None,
    "order_type": "market",
    "amount": None,
    "symbol": None,
    "timeframe": None
}

# The `inputs` argument
{
    "node-1-rsi": <pandas.Series object>  # The output from the upstream RSI indicator
}

# The `market_data` argument
<pandas.DataFrame object> # The OHLCV data for index alignment
```

## Observations
Notice that because Pydantic `model_dump()` writes out *all* fields from the `DAGNode` schema, the `node` dictionary contains many `None` values (like `action`, `indicator`, `symbol`) alongside the defaults (like `confidence_threshold: 0.7`).

The executor must gracefully use `node.get("params", {})` to drill down to the `threshold` without colliding with the other unused keys.
