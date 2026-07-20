# SPRINT 1A PATCHSET

## Patch 1: Remove Conflicting Enum

*   **File:** `routers/strategies.py`
*   **Function:** `NodeType` class definition (global scope)
*   **Line Range:** 70-79
*   **Old Code:**
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
*   **New Code:** `from core.models.pydantic_models import NodeType`
*   **Reason:** Eliminates the local enumeration entirely, forcing `routers/strategies.py` to use the canonical Pydantic model definition. This ensures that incoming `DAGNode` payloads match the compiler validation keys.
*   **LOC Changed:** -10
*   **Risk Level:** Medium. (Requires modifying `TYPE_COMPATIBILITY` to match).

## Patch 2: Repair Type Compatibility Map

*   **File:** `routers/strategies.py`
*   **Function:** `TYPE_COMPATIBILITY` declaration
*   **Line Range:** 81-90
*   **Old Code:**
    ```python
    TYPE_COMPATIBILITY: Dict[str, List[str]] = {
        NodeType.MARKET_DATA.value: [NodeType.INDICATOR.value, NodeType.FEATURE.value],
        NodeType.INDICATOR.value: [NodeType.FEATURE.value, NodeType.LOGIC.value, NodeType.ML.value],
        NodeType.FEATURE.value: [NodeType.ML.value, NodeType.LOGIC.value],
        NodeType.ML.value: [NodeType.SIGNAL.value, NodeType.LOGIC.value],
        NodeType.LOGIC.value: [NodeType.SIGNAL.value, NodeType.ACTION.value],
        NodeType.SIGNAL.value: [NodeType.ACTION.value, NodeType.LOGIC.value],
        NodeType.ACTION.value: [],
    }
    ```
*   **New Code:**
    ```python
    TYPE_COMPATIBILITY: Dict[str, List[str]] = {
        NodeType.INPUT.value: [NodeType.INDICATOR.value],
        NodeType.INDICATOR.value: [NodeType.LOGIC.value, NodeType.ML.value],
        NodeType.ML.value: [NodeType.LOGIC.value, NodeType.ACTION.value],
        NodeType.LOGIC.value: [NodeType.ACTION.value, NodeType.LOGIC.value],
        NodeType.ACTION.value: [],
    }
    ```
*   **Reason:** Aligns the compiler's connectivity graph with the canonical `NodeType` enum. Replaces `market_data` with `input`. Removes unsupported/deprecated internal types like `feature` and `signal` that no longer exist in the Pydantic models.
*   **LOC Changed:** ~10
*   **Risk Level:** Medium.

## Patch 3: Inject Threshold Support

*   **File:** `backend/dag_engine.py`
*   **Function:** `LogicExecutor.execute()`
*   **Line Range:** 548-554
*   **Old Code:**
    ```python
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                # Convert scalar to series
                input_series.append(pd.Series(value, index=market_data.index))
    ```
*   **New Code:**
    ```python
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                # Convert scalar to series
                input_series.append(pd.Series(value, index=market_data.index))
                
        # --- CERTIFIED THRESHOLD INJECTION ---
        params = node.get("params", {})
        threshold = params.get("threshold")
        if threshold is not None:
            input_series.append(pd.Series(threshold, index=market_data.index))
    ```
*   **Reason:** Allows nodes like `GT(threshold=70)` to append a static scalar to the input series list, fulfilling the `len(input_series) >= 2` requirement and preventing silent `None` returns.
*   **LOC Changed:** +5
*   **Risk Level:** Low. Inherently safe dictionary access.
