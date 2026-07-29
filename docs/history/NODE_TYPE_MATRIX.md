# NODE TYPE MATRIX

This matrix defines the exact node configurations expected by the backend `DAGCompiler` and `dag_engine.py`.

| Backend Node Type | UI Category | Required Fields in `node.data` | Optional Fields in `node.data` | Supported Values |
| :--- | :--- | :--- | :--- | :--- |
| **`input`** | Exchange | `symbol` (str), `timeframe` (str) | `label` | Any valid exchange pair (e.g., `BTC/USDT`), timeframe (e.g., `1h`) |
| **`indicator`** | Indicators | `indicator` (str), `params` (dict) | `label` | `rsi`, `macd`, `sma`, `ema`, `bb`, `atr` |
| **`ml`** | ML Models | `model_id` (str) | `label`, `confidence_threshold` (float) | Any trained model UUID in the user's registry. |
| **`logic`** | Logic / Math | `operator` (str) | `label` | `AND`, `OR`, `NOT`, `GT`, `LT`, `EQ`, `GTE`, `LTE` |
| **`action`** | Execution | `action` (str) | `label`, `order_type` (str), `amount` (float) | `buy`, `sell`, `hold` |

*(Note: There is currently no `constant` or `scalar` node type supported by the backend, which breaks logic thresholding.)*
