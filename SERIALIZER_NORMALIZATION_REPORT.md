# SERIALIZER NORMALIZATION REPORT

## Objective
Verify that the `serializeReactFlowToDAG` function correctly hoists required fields out of `params` and maps keys precisely to the target `DAGConfig` structures for `indicator`, `logic`, and `action` nodes.

## Implementation Applied
The `serializeReactFlowToDAG` function in `App.jsx` was refactored:

1. **Indicator Nodes**:
   - Hoists `indicator` mapping from `n.data.label.toLowerCase()`.
   - Converts `params.window` to `params.period`.
2. **Logic Nodes**:
   - Hoists `operator` from `params.operator` or `n.data.label` to the top-level property.
3. **Action Nodes**:
   - Hoists `action` (e.g., "sell"), `amount`, and `order_type` directly to top-level properties and strips them from `params`.

## Emitted Result (INPUT → RSI(14) → GT(70) → SELL)
```json
{
  "name": "Untitled Strategy",
  "nodes": [
    {
      "id": "n-0",
      "type": "input",
      "label": "CCXT Asset Feed",
      "params": {},
      "symbol": "BTC/USDT",
      "timeframe": "15m"
    },
    {
      "id": "n-1",
      "type": "indicator",
      "label": "RSI",
      "params": {
        "period": 14,
        "source": "close"
      },
      "indicator": "rsi"
    },
    {
      "id": "n-2",
      "type": "logic",
      "label": "GT",
      "params": {
        "threshold": 70
      },
      "operator": "GT"
    },
    {
      "id": "n-3",
      "type": "action",
      "label": "Sell",
      "params": {},
      "action": "sell",
      "amount": 1.0,
      "order_type": "market"
    }
  ],
  "edges": [
    { "source": "n-0", "target": "n-1" },
    { "source": "n-1", "target": "n-2" },
    { "source": "n-2", "target": "n-3" }
  ]
}
```

**Status: CERTIFIED** - Schema fully compliant with actual backend Pydantic expectations.
