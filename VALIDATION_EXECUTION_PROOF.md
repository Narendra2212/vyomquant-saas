# VALIDATION EXECUTION PROOF

## Request
`POST /api/strategies/validate`

**Body**:
```json
{
  "dag": {
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
    ]
  }
}
```

## Response
`HTTP 200 OK`

**Body**:
```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "execution_path": [
    "n-input",
    "n-rsi",
    "n-gt",
    "n-sell"
  ],
  "node_types": {
    "n-input": "input",
    "n-rsi": "indicator",
    "n-gt": "logic",
    "n-sell": "action"
  },
  "stats": {
    "total_nodes": 4,
    "action_nodes": 1,
    "execution_order": [
      "n-input",
      "n-rsi",
      "n-gt",
      "n-sell"
    ],
    "input_nodes": [
      "n-input"
    ],
    "estimated_time_ms": 10.0
  }
}
```

## Evidence
- Execution order properly resolved.
- Compiler stats validated the required nodes mapping (`n-sell` properly identified as an `action_nodes`).
- Node type validation successfully mapped frontend React flow nodes to backend nodes.
- Validation successfully integrated as a blocking requirement for Save and Deploy flows.
