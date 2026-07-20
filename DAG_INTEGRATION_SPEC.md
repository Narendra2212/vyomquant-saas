# DAG INTEGRATION SPECIFICATION

## Overview
This specification details the structural mismatch between the current frontend `ReactFlow` implementation in `StrategyBuilder.jsx` and the backend `DAGCompiler` expectations, along with the necessary transformation layer required to bridge them.

## 1. Current Backend Format (Expected JSON Schema)
The backend `DAGCompiler` (`routers/strategies.py` and `backend/dag_engine.py`) enforces strict validation rules and type checking.

### Nodes Schema
```json
{
    "id": "string",
    "type": "string", // MUST be one of: "input", "indicator", "ml", "logic", "action"
    
    // IF type == "indicator":
    "indicator": "rsi | macd | sma | ema | bb | atr",
    "params": { "period": 14, "fast": 12, "slow": 26, "signal": 9, "std_dev": 2 },
    
    // IF type == "ml":
    "model_id": "string",
    "confidence_threshold": 0.7,
    
    // IF type == "logic":
    "operator": "AND | OR | NOT | GT | LT | GTE | LTE | EQ",
    
    // IF type == "action":
    "action": "buy | sell | hold"
}
```

### Edges Schema
```json
{
    "source": "string", // Must match a node id
    "target": "string"  // Must match a node id
}
```

### Type Compatibility Rules (Backend Enforced)
- `input` → `indicator`, `feature`
- `indicator` → `feature`, `logic`, `ml`
- `feature` → `ml`, `logic`
- `ml` → `signal`, `logic`
- `logic` → `signal`, `action`
- `action` → (terminal, no outgoing edges)

---

## 2. Current Frontend Format (ReactFlow)
Currently, `StrategyBuilder.jsx` defines nodes strictly for visual presentation using generic ReactFlow schemas.

### Nodes Schema
```javascript
{ 
    id: '1', 
    position: { x: 250, y: 50 }, 
    data: { label: 'Binance Data Stream' }, 
    type: 'input', // ReactFlow visual type ('input', 'output', or default)
    style: { background: '#1A222C', color: '#E6EDF3', ... } 
}
```

### Edges Schema
```javascript
{ 
    id: 'e1-2', 
    source: '1', 
    target: '2', 
    animated: true, 
    style: { stroke: '#2962FF' } 
}
```

---

## 3. Transformation Layer Required
Because ReactFlow reserves the `type` field for visual components (e.g., handles), the backend semantic `type` and its parameters must be nested inside the ReactFlow `data` object.

**Frontend State should look like:**
```javascript
{ 
    id: '1', 
    position: { x: 250, y: 50 }, 
    type: 'customNode', // ReactFlow visual type
    data: { 
        label: 'RSI Indicator',
        backendType: 'indicator',
        config: {
            indicator: 'rsi',
            params: { period: 14 }
        }
    } 
}
```

**Serializer Function Required:**
```javascript
function serializeForBackend(reactFlowNodes, reactFlowEdges) {
    const nodes = reactFlowNodes.map(n => ({
        id: n.id,
        type: n.data.backendType,
        ...n.data.config
    }));
    
    const edges = reactFlowEdges.map(e => ({
        source: e.source,
        target: e.target
    }));
    
    return { nodes, edges };
}
```

---

## 4. Missing Components

### Missing Node Types in Frontend
The frontend `NODE_CATEGORIES` list contains display names like `"Max Drawdown Guard"` and `"Position Sizer"`. However, the backend engine only understands primitive logic blocks. 
- **Missing:** Explicit `Logic` nodes (AND, OR, NOT, GT, LT, EQ) to build complex conditions.
- **Missing:** Explicit `Action` nodes mapped correctly to `buy`, `sell`.

### Missing Parameter Forms
- `StrategyBuilder.jsx` lacks configuration panels. Clicking a node does not open a modal to set `period=14` for an RSI node or `operator=GT` for a logic node. These parameters are strictly required by `dag_engine.py`.

---

## 5. API Endpoints to Use

### Save Strategy
- **Endpoint:** `POST /api/strategies/`
- **Payload:** 
```json
{
    "name": "My Strategy",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "nodes": [...], // Serialized nodes
    "edges": [...]  // Serialized edges
}
```

### Validate Strategy (Dry-Run / Linting)
- **Endpoint:** `POST /api/strategies/validate`
- **Payload:** 
```json
{
    "dag": {
        "nodes": [...],
        "edges": [...]
    }
}
```

### Backtest Strategy
- **Endpoint:** `POST /api/strategies/backtest`
- **Payload:** 
```json
{
    "dag": {
        "nodes": [...],
        "edges": [...],
        "strategy_name": "My Strategy",
        "symbols": ["BTCUSDT"],
        "timeframe": "1h"
    },
    "initial_capital": 10000.0,
    "trade_size_pct": 0.1
}
```

---

## 6. Files to Modify

| File | Action | Estimated LOC |
|------|--------|---------------|
| `algo22-terminal/src/components/StrategyBuilder.jsx` | Add node click handlers, serialization logic, API calls to `/validate`, `/backtest`, and `/api/strategies` | ~250 LOC |
| `algo22-terminal/src/components/NodeConfigModal.jsx` | **[NEW]** Build forms dynamically based on node type (`backendType`) | ~150 LOC |
| `algo22-terminal/src/api/modules/strategies.js` | Add `validateDag` and `backtestDag` helper methods | ~20 LOC |
