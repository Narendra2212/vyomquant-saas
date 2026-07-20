# SIGNAL USAGE AUDIT

## Search Methodology
Searched entire repository for `SIGNAL`, `"signal"`, and `NodeType.SIGNAL`.

## Occurrences as a Node Type
1.  **File:** `routers/strategies.py`
    *   **Line:** 77 (`SIGNAL = "signal"`)
    *   **Usage:** Enum definition in local `NodeType` class.
    *   **Runtime Criticality:** Low. Outdated type map.
2.  **File:** `routers/strategies.py`
    *   **Line:** 86-88 (`NodeType.SIGNAL.value`)
    *   **Usage:** References in `TYPE_COMPATIBILITY` map.
    *   **Runtime Criticality:** Low.
3.  **File:** `frontend/strategyValidator.js`
    *   **Line:** 225 (`'signal': ['action', 'logic']`)
    *   **Usage:** Frontend visual graph validator type map.
    *   **Runtime Criticality:** Low. Controls edge drawing UI.

## Other Occurrences (Not Node Types)
1.  **Files:** `backend/signal_validator.py`, `backend/execution_guard.py`, `backend/execution_router.py`, `validate_*.py`
    *   **Usage:** Used extensively as a property key for the execution payload (e.g., `{"signal": "buy"}`).
    *   **Runtime Criticality:** HIGH. This represents the actual buy/sell/hold payload traversing the execution engines and queues. However, this is independent of the DAG `NodeType` enum.
2.  **File:** `core/backpressure_v2.py`
    *   **Usage:** Queue name constant (`SIGNAL = "signal"`).
    *   **Runtime Criticality:** HIGH. Queue routing key. Independent of DAG compilation.

## Conclusion
The use of `"signal"` as an execution payload and queue name is highly critical and pervasive. However, the use of `NodeType.SIGNAL` as a DAG structural node type is completely obsolete, missing from Pydantic `DAGNode`, and safe to remove from `strategies.py` and `strategyValidator.js`.
