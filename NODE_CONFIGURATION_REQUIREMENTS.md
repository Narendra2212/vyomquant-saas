# NODE CONFIGURATION REQUIREMENTS

The current `ReactFlow` nodes in `StrategyBuilder.jsx` are completely static and lack configurable parameters. To map to the backend `DAGNode` schema, a Properties/Configuration Panel must be built. When a user clicks a node, the panel should open and allow them to set the following fields, which are then saved to the node's `data` object.

## 1. Indicator Nodes
Must populate the `indicator` string and `params` dictionary.
*   **RSI**: Requires `period` (int, default: 14).
*   **MACD**: Requires `fast` (int, default: 12), `slow` (int, default: 26), `signal` (int, default: 9).
*   **SMA / EMA**: Requires `period` (int, default: 20).
*   **Bollinger Bands (BB)**: Requires `period` (int, default: 20) and `std_dev` (float, default: 2.0).
*   **ATR**: Requires `period` (int, default: 14).

## 2. ML Nodes
Must populate the `model_id` and `confidence_threshold`.
*   **Model Selector**: Dropdown to select a trained `model_id` from the user's ML registry.
*   **Confidence Threshold**: Slider or float input (0.0 to 1.0, default 0.7) dictating the certainty required to emit a signal.

## 3. Logic Nodes
Must populate the `operator` field.
*   **Comparison Nodes (GT, LT, EQ, etc.)**: Needs an input field to compare against a static float scalar (e.g. 70.0), OR needs to accept two edge connections (e.g. comparing fast EMA to slow EMA).
*   **Combinatorial Nodes (AND, OR)**: Inherently configured by their edge connections.

## 4. Action Nodes
Must populate `action`, `order_type`, and `amount`.
*   **Action Type**: Dropdown for `buy` or `sell`.
*   **Order Type**: Dropdown for `market`, `limit`, `twap`, etc.
*   **Amount**: Float input for the position size (or percentage).

## 5. Input Nodes
Must populate `symbol` and `timeframe`.
*   **Symbol**: Dropdown (e.g. `BTCUSDT`).
*   **Timeframe**: Dropdown (e.g. `1m`, `5m`, `1h`).
