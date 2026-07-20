# NODETYPE INVENTORY

I have audited the source code and found three conflicting definitions of `NodeType`.

## 1. Local Redefinition in `routers/strategies.py`

*   **File:** `routers/strategies.py`
*   **Lines:** 70-79
*   **Exact Enum:**
    ```python
    class NodeType(Enum):
        """Valid node types in the DAG type system."""
        MARKET_DATA = "market_data"
        INDICATOR = "indicator"
        FEATURE = "feature"
        ML = "ml"
        LOGIC = "logic"
        SIGNAL = "signal"
        ACTION = "action"
    ```
*   **Used at runtime:** Yes. Used by `TYPE_COMPATIBILITY` mapping at line 82 in the same file.

## 2. Canonical Definition in Pydantic Models

*   **File:** `core/models/pydantic_models.py`
*   **Lines:** 171-177
*   **Exact Enum:**
    ```python
    class NodeType(str, Enum):
        INDICATOR = "indicator"
        ML = "ml"
        LOGIC = "logic"
        ACTION = "action"
        INPUT = "input"
    ```
*   **Used at runtime:** Yes. Used by FastAPI to validate incoming JSON payloads for `DAGNode`.

## 3. Observability Engine Definition

*   **File:** `backend/signal_trace_engine.py`
*   **Lines:** 32-43
*   **Exact Enum:**
    ```python
    class NodeType(Enum):
        """Types of DAG nodes."""
        MARKET_DATA = "market_data"
        INDICATOR = "indicator"
        OPERATOR = "operator"
        LOGIC = "logic"
        ML_MODEL = "ml_model"
        RISK = "risk"
        EXECUTION = "execution"
        ORDERBOOK = "orderbook"
        TICKER = "ticker"
    ```
*   **Used at runtime:** Used locally for observability traces, but isolated from compilation.

---

## Determination

*   **Is there more than one NodeType definition?** Yes, there are three.
*   **Is there a market_data vs input mismatch?** Yes. Pydantic expects `input` (line 176), while `strategies.py` expects `market_data` (line 72). Pydantic does not have `market_data`. `strategies.py` does not have `input`.
*   **Is TYPE_COMPATIBILITY using a different enum?** Yes. `TYPE_COMPATIBILITY` references the local `NodeType` in `strategies.py`, which is fundamentally incompatible with the validated Pydantic model nodes passed in the payload.
