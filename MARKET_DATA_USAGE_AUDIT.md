# MARKET_DATA USAGE AUDIT

## Search Methodology
Searched entire repository for `market_data`, `MARKET_DATA`, and `NodeType.MARKET_DATA`.

## Occurrences as a Node Type
1.  **File:** `routers/strategies.py`
    *   **Line:** 72 (`MARKET_DATA = "market_data"`)
    *   **Usage:** Enum definition in local `NodeType` class.
    *   **Runtime Criticality:** Low. Should be mapped to `input` to match Pydantic `DAGNode`.
2.  **File:** `backend/signal_trace_engine.py`
    *   **Line:** 34 (`MARKET_DATA = "market_data"`)
    *   **Usage:** Local observability enum definition.
    *   **Runtime Criticality:** Low. Isolated to tracing.
3.  **File:** `frontend/strategyValidator.js`
    *   **Line:** 220 (`'market_data': ['indicator', 'feature']`)
    *   **Usage:** Frontend visual graph validator.
    *   **Runtime Criticality:** Low. Needs patching to use `'input'` to match the backend.

## Other Occurrences (Not Node Types)
1.  **Files:** `routers/strategies.py` (Line 1343), `core/live_engine.py` (Line 195)
    *   **Usage:** Variable name for the dictionary containing OHLCV pandas DataFrames (e.g., `market_data = {}`).
    *   **Runtime Criticality:** HIGH. Core execution framework variable. Independent of `NodeType` enum.
2.  **Files:** `core/event_bus.py`, `core/redis_streams.py`, `backend/exchange_telemetry.py`, `backend/ws_server.py`
    *   **Usage:** Redis stream topic and websocket event key (e.g., `MARKET_STREAM = "market_data"`).
    *   **Runtime Criticality:** HIGH. Used for pub/sub infrastructure. Independent of `NodeType` enum.

## Conclusion
The string `"market_data"` is a critical core infrastructure constant and variable name used for websockets and data frames. However, its usage specifically as a DAG `NodeType` enum is anachronistic and conflicts with the Pydantic `input` type. Replacing `NodeType.MARKET_DATA` with `NodeType.INPUT` will not impact the websocket or execution variable architectures.
