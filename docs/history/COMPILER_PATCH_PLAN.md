# COMPILER PATCH PLAN

This document outlines the exact, minimal code changes required in the backend to fix the compiler crashes and enable mathematical logic execution.

## Patch 1: Fix Enum Mismatch
**Target File**: `aerora_quant_backend_updated_final1/routers/strategies.py`

**Current Code:**
```python
# Line 70
class NodeType(Enum):
    MARKET_DATA = "market_data"
    ...
    ACTION = "action"

# Line 82
TYPE_COMPATIBILITY: Dict[str, List[str]] = {
    NodeType.MARKET_DATA.value: [NodeType.INDICATOR.value, NodeType.FEATURE.value],
    ...
```

**Required Change:**
1. Delete the `class NodeType(Enum):` block entirely.
2. Add the import: `from core.models.pydantic_models import NodeType`
3. Update `TYPE_COMPATIBILITY` to use the correct API strings:
```python
TYPE_COMPATIBILITY: Dict[str, List[str]] = {
    NodeType.INPUT.value: [NodeType.INDICATOR.value, NodeType.ML.value],
    NodeType.INDICATOR.value: [NodeType.ML.value, NodeType.LOGIC.value],
    NodeType.ML.value: [NodeType.LOGIC.value, NodeType.ACTION.value],
    NodeType.LOGIC.value: [NodeType.LOGIC.value, NodeType.ACTION.value],
    NodeType.ACTION.value: []
}
```

## Patch 2: Enable Logic Thresholds
**Target File**: `aerora_quant_backend_updated_final1/backend/dag_engine.py`

**Current Code:**
```python
# Line 547
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                input_series.append(pd.Series(value, index=market_data.index))
```

**Required Change:**
Extract the `threshold` from the node's `params` dictionary and append it to `input_series`.
```python
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                input_series.append(pd.Series(value, index=market_data.index))
        
        # --- NEW CODE FOR THRESHOLD SCALAR SUPPORT ---
        params = node.get("params", {})
        threshold = params.get("threshold")
        if threshold is not None:
            input_series.append(pd.Series(threshold, index=market_data.index))
        # ---------------------------------------------
```

## Result
With these two minimal patches:
1. `INPUT` → `INDICATOR` → `LOGIC` → `ACTION` will perfectly pass `DAGCompiler._validate_type_compatibility`.
2. `LogicExecutor` will correctly evaluate conditions like `RSI > 70` because `input_series` will successfully contain exactly two Series (the RSI indicator edge and the embedded parameter threshold).
