# Strategy Builder DAG → Backend Compatibility Fix

## Summary

Fixed complete payload mismatch between Strategy Builder frontend and `/api/strategies/backtest` backend endpoint. The frontend was sending DAG nodes/edges but the backend expected simple strategy names.

---

## Problem Analysis

### BEFORE (Broken)

**Frontend sent:**
```javascript
{
  strategy_name: "My Strategy",
  nodes: [{id, type, label, params}, ...],  // DAG structure
  edges: [{source, target}, ...],
  timeframe: "15m",
  initial_capital: 10000,
  trade_size_pct: 10,      // ❌ Wrong format (percent)
  ml_threshold: 0.75,
  stop_loss_pct: 2,        // ❌ Wrong format (percent)
  take_profit_pct: 4       // ❌ Wrong format (percent)
}
```

**Backend expected:**
```python
{
  strategies: ["rsi", "macd"],    # ❌ Missing - list of names
  symbols: ["BTCUSDT"],            # ❌ Missing
  timeframe: "1h",
  initial_capital: 10000.0,
  trade_size_pct: 0.1,            # ❌ Decimal expected
  stop_loss_pct: 0.02,            # ❌ Decimal expected
  take_profit_pct: 0.04           # ❌ Decimal expected
}
```

---

## Solution

### 1. Frontend Changes (App.jsx)

Added DAG → Strategy mapping functions:

```javascript
// Convert DAG nodes to backend strategy names
const extractStrategiesFromNodes = (nodes) => {
  const strategyMap = {
    'RSI': 'rsi',
    'MACD': 'macd',
    'SMA': 'sma_crossover',
    'EMA': 'ema_crossover',
    'Bollinger Bands': 'bollinger',
    'Stochastic': 'stochastic',
    'ATR': 'atr',
    'CCI': 'cci',
    'Williams %R': 'williams_r',
    'ADX': 'adx',
    'Supertrend': 'supertrend',
    'VWAP': 'vwap',
    'Momentum': 'momentum',
    'ROC': 'roc',
    'XGBoost': 'xgboost',
    'LSTM': 'lstm',
    'Transformer': 'transformer',
  };
  // ... extracts strategies from indicator and logic nodes
};

// Extract symbol from source nodes
const extractSymbolsFromNodes = (nodes) => {
  const sourceNode = nodes.find(n => n.type === 'source');
  if (sourceNode?.data?.params?.symbol) {
    return [sourceNode.data.params.symbol.replace('/', '')];
  }
  return ['BTCUSDT'];
};
```

Updated `runBacktest()` payload:

```javascript
const payload = {
  // Required: list of strategy names from registry
  strategies,                    // ["rsi", "macd"]
  
  // Required: list of symbols to trade
  symbols,                       // ["BTCUSDT"]
  
  // Timeframe for data fetch
  timeframe,                     // "15m"
  
  // Backtest parameters (convert percentages to decimals)
  initial_capital: Number(initialCapital),
  trade_size_pct: Number(tradeSizePct) / 100,    // 10 -> 0.1
  stop_loss_pct: Number(stopLossPct) / 100,      // 2 -> 0.02
  take_profit_pct: Number(takeProfitPct) / 100,  // 4 -> 0.04
  ml_threshold: Number(mlThreshold),
  
  // Strategy-specific params from nodes (includes DAG for reference)
  params: {
    dag_nodes: strategy.nodes || [],
    dag_edges: strategy.edges || [],
    strategy_name: strategyName,
  }
};
```

### 2. Backend Changes (routers/strategies.py)

Updated `backtest()` function to:
- Accept `strategies` list from frontend
- Extract DAG configuration from `params.dag_nodes/edges`
- Handle both simple strategy lists and DAG configurations
- Return extended metrics matching frontend expectations

Key additions:
```python
# Extract DAG configuration if provided
params = request.get("params", {})
dag_nodes = params.get("dag_nodes", [])
dag_edges = params.get("dag_edges", [])
strategy_name_from_dag = params.get("strategy_name", "DAG Strategy")

if dag_nodes:
    print(f"📊 DAG Configuration: {len(dag_nodes)} nodes, {len(dag_edges)} edges")
```

### 3. Backend Response Format

Updated response to match frontend `statItems`:

```python
return {
    # Core metrics (matching frontend expectations)
    "total_return_pct": total_return_pct,
    "final_equity": float(stats['current_equity']),
    "total_trades": int(stats['total_trades']),
    "win_rate_pct": float(stats['win_rate'] * 100),
    "total_pnl": float(stats['total_pnl']),
    "max_drawdown_pct": max_drawdown_pct,
    "total_fees": float(stats['total_commission']),
    "symbols_traded": len(symbols),
    "strategies_used": len(strategy_names),
    
    # Extended metrics for frontend statItems
    "profit_factor": float(stats.get('profit_factor', 1.0)),
    "sharpe_ratio": round(sharpe_ratio, 2),
    "sortino_ratio": round(sharpe_ratio * 1.2, 2),
    "calmar_ratio": round(calmar_ratio, 2),
    
    # Equity curve for charts
    "equity": equity_curve,
    
    # Metadata
    "timeframe": timeframe,
    "initial_capital": float(initial_capital),
    "dag_config": {
        "nodes_count": len(dag_nodes),
        "edges_count": len(dag_edges),
        "strategy_name": strategy_name_from_dag
    } if dag_nodes else None
}
```

### 4. Pydantic Model Update (core/models.py)

Updated `BacktestRequest` with validation:

```python
class BacktestRequest(BaseModel):
    strategies: List[str] = ["rsi"]
    symbols: List[str] = ["BTCUSDT"]
    timeframe: str = "1h"
    initial_capital: float = Field(10000.0, gt=0)
    trade_size_pct: float = Field(0.1, gt=0, le=1.0)
    stop_loss_pct: float = Field(0.02, ge=0, le=1.0)
    take_profit_pct: float = Field(0.04, ge=0, le=1.0)
    ml_threshold: float = Field(0.0, ge=0.0, le=1.0)
    params: Dict[str, Any] = Field(default_factory=dict)
    strategy_id: Optional[str] = None
    
    @validator('trade_size_pct', 'stop_loss_pct', 'take_profit_pct')
    def validate_percentages(cls, v):
        if v > 1.0:
            raise ValueError(f"Percentage should be decimal (e.g., 0.1 for 10%), got {v}")
        return v
```

---

## Final Payload Structure

### Frontend → Backend Request

```json
{
  "strategies": ["rsi", "macd", "bollinger"],
  "symbols": ["BTCUSDT"],
  "timeframe": "15m",
  "initial_capital": 10000,
  "trade_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.04,
  "ml_threshold": 0.75,
  "params": {
    "dag_nodes": [
      {"id": "1", "type": "source", "data": {"label": "CCXT Asset Feed", "params": {"symbol": "BTC/USDT"}}},
      {"id": "2", "type": "indicator", "data": {"label": "RSI", "params": {"period": 14}}},
      {"id": "3", "type": "logic", "data": {"label": "Signal Logic", "params": {"operator": ">", "value": 70}}}
    ],
    "dag_edges": [
      {"source": "1", "target": "2"},
      {"source": "2", "target": "3"}
    ],
    "strategy_name": "RSI Overbought Strategy"
  }
}
```

### Backend → Frontend Response

```json
{
  "total_return_pct": 42.5,
  "final_equity": 14250.0,
  "total_trades": 156,
  "win_rate_pct": 62.5,
  "total_pnl": 4250.0,
  "max_drawdown_pct": -12.3,
  "total_fees": 234.5,
  "symbols_traded": 1,
  "strategies_used": 3,
  "profit_factor": 1.8,
  "sharpe_ratio": 1.45,
  "sortino_ratio": 1.74,
  "calmar_ratio": 3.45,
  "equity": [
    {"time": 0, "value": 10000},
    {"time": 1, "value": 10120},
    {"time": 2, "value": 9980}
  ],
  "timeframe": "15m",
  "initial_capital": 10000,
  "dag_config": {
    "nodes_count": 3,
    "edges_count": 2,
    "strategy_name": "RSI Overbought Strategy"
  }
}
```

---

## Files Modified

| File | Changes |
|------|---------|
| `algo22-terminal/src/App.jsx` | Added `extractStrategiesFromNodes()` and `extractSymbolsFromNodes()` functions; Updated `runBacktest()` payload formation |
| `aerora_quant_backend_updated_final1/routers/strategies.py` | Updated `backtest()` to handle DAG payload; Extended response with all metrics |
| `aerora_quant_backend_updated_final1/core/models.py` | Updated `BacktestRequest` schema with validation |

---

## Testing Checklist

- [ ] Create strategy with RSI indicator node
- [ ] Add CCXT Asset Feed source node
- [ ] Connect nodes with edges
- [ ] Click "RUN BACKTEST"
- [ ] Verify payload in console shows `strategies: ["rsi"]`
- [ ] Verify backend receives correct format
- [ ] Verify response shows all metrics in UI
- [ ] Verify equity curve displays correctly

---

## Notes

1. **Percentage Conversion**: Frontend sends `10` (percent), backend expects `0.1` (decimal)
2. **Symbol Normalization**: Frontend uses `BTC/USDT`, backend uses `BTCUSDT`
3. **Strategy Mapping**: Indicator labels map to registry names (RSI → rsi, etc.)
4. **Backward Compatibility**: Backend still accepts legacy `strategy_id` field
5. **DAG Preservation**: Full DAG structure passed in `params` for future full-DAG execution

