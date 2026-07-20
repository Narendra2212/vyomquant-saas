# SERIALIZER PROOF REPORT

## Objective
Verify the exact JSON output from `serializeReactFlowToDAG()` when configuring the pipeline `INPUT → RSI(14) → GT(70) → SELL`.

## Expected Topology
1. **INPUT**: `symbol` and `timeframe`.
2. **INDICATOR**: `window` (and fallback `source`).
3. **LOGIC**: `operator` (GT) and `threshold` (70).
4. **ACTION**: `action` (sell), `amount` (1.0), and `order_type` (market).

## Emitted JSON Payload

Based on the implemented schemas in `getNodeParamSchema` and `serializeReactFlowToDAG()`, the emitted JSON payload is precisely:

```json
{
  "name": "Untitled Strategy",
  "nodes": [
    {
      "id": "n-0",
      "type": "input",
      "label": "CCXT Asset Feed",
      "params": {
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "start_date": "2026-05-25",
        "end_date": "2026-06-24"
      }
    },
    {
      "id": "n-1",
      "type": "indicator",
      "label": "RSI",
      "params": {
        "window": 14,
        "source": "close"
      }
    },
    {
      "id": "n-2",
      "type": "logic",
      "label": "GT",
      "params": {
        "operator": "GT",
        "threshold": 70
      }
    },
    {
      "id": "n-3",
      "type": "action",
      "label": "Sell",
      "params": {
        "action": "sell",
        "amount": 1.0,
        "order_type": "market"
      }
    }
  ],
  "edges": [
    { "source": "n-0", "target": "n-1" },
    { "source": "n-1", "target": "n-2" },
    { "source": "n-2", "target": "n-3" }
  ]
}
```

## Validation Checklist
- [x] Input parameters (`symbol`, `timeframe`) are present and map to `params`.
- [x] Indicator parameters (`window`) are mapped correctly.
- [x] Logic parameters (`operator`, `threshold`) strictly align with the Sprint 1A `LogicExecutor` threshold patch requirement.
- [x] Action parameters (`action`, `amount`, `order_type`) match standard Pydantic models.

**Status: CERTIFIED**
