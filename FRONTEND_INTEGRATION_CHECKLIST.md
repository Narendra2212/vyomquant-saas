# FRONTEND INTEGRATION CHECKLIST

## Overview
This checklist defines the exact payload structures the frontend MUST send to successfully interface with the untyped Python backend routes. Because the backend bypasses Pydantic validation, the frontend must strictly enforce these schemas to prevent silent runtime failures.

---

## 1. Mismatches with DAG_INTEGRATION_SPEC

⚠️ **CRITICAL DISCOVERY:**
The `DAG_INTEGRATION_SPEC` correctly identified the structural needs of the `dag_engine.py` and `DAGCompiler`. However, the API routing layer (`routers/strategies.py`) introduces additional complexities:

1. **Missing Pydantic Enforcement:** The backend expects decimals for percentages (e.g., `0.1` for 10%). Because the `BacktestRequest` Pydantic model is ignored, passing `10` from the frontend will not throw a 422 error; it will silently execute a backtest assuming a 1000% trade size, causing a complete simulation crash or liquidation. The frontend must divide percentages by 100 before dispatch.
2. **Backtest Auth Bypass:** The frontend will not receive a 401 Unauthorized if the user's token expires while clicking "Run Backtest", as the route is completely unauthenticated.
3. **Legacy Fallback:** The backend backtest route supports a "legacy" mode (`strategies` array). The frontend must ensure the `dag` object is fully formed, otherwise the backend might silently fall back to legacy mode and throw cryptic errors.

---

## 2. API Contract Examples

### A. Validate Strategy (`POST /api/strategies/validate`)

**Frontend Payload (Send this):**
```json
{
  "dag": {
    "nodes": [
      {
        "id": "1",
        "type": "indicator",
        "indicator": "rsi",
        "params": { "period": 14 }
      },
      {
        "id": "2",
        "type": "action",
        "action": "buy"
      }
    ],
    "edges": [
      {
        "source": "1",
        "target": "2"
      }
    ]
  }
}
```

**Backend Response (Expect this):**
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["No INPUT or INDICATOR nodes found. DAG may not have data sources."],
  "execution_path": ["1", "2"],
  "node_types": {
    "1": "indicator",
    "2": "action"
  },
  "stats": {
    "total_nodes": 2,
    "action_nodes": 1,
    "estimated_time_ms": 5.0,
    "execution_order": ["1", "2"],
    "input_nodes": []
  }
}
```

---

### B. Run Backtest (`POST /api/strategies/backtest`)

**Frontend Payload (Send this):**
```json
{
  "dag": {
    "strategy_name": "Alpha Neural V2",
    "symbols": ["BTCUSDT"],
    "timeframe": "1h",
    "nodes": [...],
    "edges": [...]
  },
  "initial_capital": 10000.0,
  "trade_size_pct": 0.15,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05
}
```
*Note: Ensure `trade_size_pct`, `stop_loss_pct`, and `take_profit_pct` are formatted as decimals (e.g., 15% -> 0.15).*

**Backend Response (Expect this):**
```json
{
  "total_return_pct": 14.5,
  "final_equity": 11450.0,
  "total_trades": 24,
  "win_rate_pct": 62.5,
  "total_pnl": 1450.0,
  "max_drawdown_pct": 3.2,
  "total_fees": 12.4,
  "symbols_traded": 1,
  "profit_factor": 1.8,
  "sharpe_ratio": 1.45,
  "sortino_ratio": 1.74,
  "calmar_ratio": 4.53,
  "equity": [
    { "time": 0, "value": 10000.0 },
    { "time": 1, "value": 10050.0 }
  ],
  "dag_results": {
    "nodes_count": 5,
    "edges_count": 4,
    "execution_order": ["1", "2"],
    "action_nodes": ["2"],
    "node_results": {}
  },
  "execution_mode": "dag",
  "timeframe": "1h",
  "initial_capital": 10000.0
}
```

---

### C. Save Strategy (`POST /api/strategies`)

**Frontend Payload (Send this):**
```json
{
  "name": "Alpha Neural V2",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "exchange_id": "binance",
  "nodes": [...],
  "edges": [...]
}
```

**Backend Response (Expect this):**
```json
{
  "strategy_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "created"
}
```

---

### D. Deploy Strategy (`POST /api/strategies/{id}/deploy`)

**Frontend Payload (Send this):**
```json
{
  "exchange_id": "binance",
  "user_id": "optional-but-must-match-auth-token-if-sent"
}
```

**Backend Response (Expect this):**
```json
{
  "status": "running"
}
```
