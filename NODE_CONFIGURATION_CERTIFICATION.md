# NODE CONFIGURATION CERTIFICATION

## Component Schema Alignment

The `getNodeParamSchema` function now strictly forces ReactFlow nodes to configure data identically to the Pydantic backend models:

### 1. Input (`input`)
- `symbol`: Datalist standard symbol format (e.g., "BTC/USDT")
- `timeframe`: Select list (e.g., "15m")
- (Extra metadata like `start_date` and `end_date` is kept for testing modes but does not interfere with the compiler).

### 2. Indicator (`indicator`)
- Lookbacks natively map to generic `window` fields or specific fields (`fast`, `slow`, `signal` for MACD).
- Supported: RSI, MACD, SMA, EMA, BB, ATR.

### 3. Logic (`logic`)
- `operator`: Strict drop-down enum matching the backend (GT, LT, GTE, LTE, EQ, AND, OR, NOT).
- `threshold`: Direct numerical mapping supporting the static threshold patch introduced in Sprint 1A.

### 4. Machine Learning (`ml`)
- `model_id`: Identifier mapping directly to the backend's model repository.
- `confidence_threshold`: The required certainty level for signal emission.

### 5. Action (`action`)
- `action`: Buy / Sell execution routes.
- `amount`: Volume size.
- `order_type`: Market / Limit.

All configuration dynamically updates `node.data.params`, making it natively serializable to the backend DAG dictionary.
