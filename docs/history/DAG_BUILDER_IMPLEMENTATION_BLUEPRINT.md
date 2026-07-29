# DAG BUILDER IMPLEMENTATION BLUEPRINT

This blueprint defines the precise architecture for upgrading the static ReactFlow UI into a fully functional, production-ready Strategy Builder that maps natively to the existing backend API and `pydantic_models.py`.

---

## 1. Node Palette Structure
The left sidebar (`PaletteSidebar.jsx`) provides draggable node templates grouped by backend `NodeType`.

*   **Input** (`type="input"`): Symbol Stream.
*   **Indicator** (`type="indicator"`): RSI, MACD, SMA, EMA, BB, ATR.
*   **Logic** (`type="logic"`): AND, OR, NOT, Greater Than (GT), Less Than (LT), Equal (EQ), GTE, LTE. *(NEW - Must be added)*
*   **ML** (`type="ml"`): Model Inference.
*   **Action** (`type="action"`): Buy Order, Sell Order.

---

## 2. Logic Node Architecture
The backend `DAGCompiler` is strictly mathematical. Indicators output continuous floats (e.g., RSI=75) which **must** be converted to boolean signals (1 or 0) using Logic Nodes before reaching an Action Node.

**Architecture:**
*   A `GT` Logic Node accepts two edge inputs: `Left` (e.g. from an RSI Node) and `Right` (e.g. from a Constant Node, or a static parameter defined in the Logic Node's property form).
*   The Logic Node evaluates the condition (`left > right`) and outputs `1` or `0`.
*   An Action Node only accepts inputs from Logic Nodes or ML Nodes (which internally apply a threshold to output a signal).

---

## 3. Node Configuration Panel Architecture
When a user clicks a node on the canvas, a right-side properties panel (`NodeConfigPanel.jsx`) renders a form specific to the selected node's `type`. Form changes dispatch state updates to mutate the selected node's `data` object.

*See `STATE_MANAGEMENT_PLAN.md` for exact state synchronization details.*

---

## 4. React State Schema vs Backend Mapping

### 4.1 Input Node
*   **React State (`data`)**: `{ label: "BTC Stream", symbol: "BTCUSDT", timeframe: "1h" }`
*   **UI Fields**: Symbol Dropdown, Timeframe Dropdown.
*   **Backend Mapping**: Maps to `DAGNode(type="input", symbol="BTCUSDT", timeframe="1h")`

### 4.2 Indicator Node
*   **React State (`data`)**: `{ label: "RSI", indicator: "rsi", params: { period: 14 } }`
*   **UI Fields**: Dynamic based on indicator type (e.g., Period integer input for RSI; Fast, Slow, Signal integer inputs for MACD).
*   **Backend Mapping**: Maps to `DAGNode(type="indicator", indicator="rsi", params={"period": 14})`

### 4.3 Logic Node
*   **React State (`data`)**: `{ label: "Greater Than", operator: "GT" }`
*   **UI Fields**: Operator dropdown (GT, LT, AND, OR), optional scalar threshold input if comparing an indicator against a fixed number.
*   **Backend Mapping**: Maps to `DAGNode(type="logic", operator="GT")`

### 4.4 ML Node
*   **React State (`data`)**: `{ label: "LSTM Model", model_id: "lstm-v1", confidence_threshold: 0.75 }`
*   **UI Fields**: Model Registry Dropdown (fetches trained models from API), Confidence Threshold Slider (0.0 to 1.0).
*   **Backend Mapping**: Maps to `DAGNode(type="ml", model_id="lstm-v1", confidence_threshold=0.75)`

### 4.5 Action Node
*   **React State (`data`)**: `{ label: "Market Buy", action: "buy", order_type: "market", amount: 0.1 }`
*   **UI Fields**: Action type (Buy/Sell toggle), Order Type dropdown, Position Size/Amount float input.
*   **Backend Mapping**: Maps to `DAGNode(type="action", action="buy", order_type="market", amount=0.1)`

---

## 5. API Workflows & Lifecycles
All operations trigger the serialization of `nodes` and `edges` into a `DAGConfig` object before making API calls. *See `SERIALIZATION_SPEC.md` for exact mapping logic.*

### Validation Flow
1. User clicks "Validate".
2. Frontend serializes graph to `DAGConfig`.
3. Calls `POST /api/strategies/validate` with the config.
4. Render errors mapping backend `node_id` failures directly onto the canvas nodes using error badges.

### Save Flow
1. User clicks "Save".
2. Frontend serializes graph.
3. Constructs `StrategyBlueprint` model (which wraps the `DAGConfig`).
4. Calls `POST /api/strategies` with `Authorization: Bearer <token>`.

### Backtest Flow
1. User clicks "Run Simulation".
2. Frontend serializes graph into `DAGConfig`.
3. Constructs `BacktestRequest` using `dag=DAGConfig`.
4. Calls `POST /api/strategies/backtest`.
5. Awaits the synchronous `BacktestResponse` (replacing the legacy websocket listener).
6. Plots the returned `stats` and `equity_curve` in the bottom panel.

### Deploy Flow
1. User clicks "Deploy to Live".
2. Strategy must be saved first (retrieving a strategy ID).
3. Calls `POST /api/strategies/{id}/deploy` with exchange configuration.
