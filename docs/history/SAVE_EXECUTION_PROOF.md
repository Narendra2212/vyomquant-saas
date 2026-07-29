# SAVE EXECUTION PROOF

## Request
`POST /api/strategies`

**Body**:
```json
{
  "name": "Golden Strategy",
  "nodes": [
    {"id": "n-input", "type": "input", "symbol": "BTCUSDT", "timeframe": "1h"},
    {"id": "n-rsi", "type": "indicator", "indicator": "rsi", "params": {"window": 14}},
    {"id": "n-gt", "type": "logic", "operator": "GT", "params": {"value": 70}},
    {"id": "n-sell", "type": "action", "action": "sell", "order_type": "market", "amount": 0.15}
  ],
  "edges": [
    {"id": "e1", "source": "n-input", "target": "n-rsi"},
    {"id": "e2", "source": "n-rsi", "target": "n-gt"},
    {"id": "e3", "source": "n-gt", "target": "n-sell"}
  ],
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
  "symbol": "BTCUSDT",
  "timeframe": "1h"
}
```

## Response
`HTTP 201 Created`

**Body**:
```json
{
  "status": "created",
  "strategy_id": "strat_01J13A2BZ9Q9",
  "message": "Strategy saved successfully"
}
```

## Evidence
- Required fields (`buy_logic`, `sell_logic`, `risk`) successfully injected by `handleSaveStrategy`.
- Node metadata cleanly serialized using `serializeReactFlowToDAG`.
- `strategy_id` (`strat_01J13A2BZ9Q9`) was captured via `setStrategyIdState(res.strategy_id)` confirming state preservation in the React builder.
