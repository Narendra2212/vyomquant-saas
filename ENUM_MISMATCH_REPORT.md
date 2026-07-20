# ENUM MISMATCH REPORT

## The Issue
There is a critical duplication and mismatch of the `NodeType` Enum across the backend. This causes the `DAGCompiler` to instantly reject all valid DAG configurations.

### 1. The Pydantic Definition
**File**: `core/models/pydantic_models.py`
```python
class NodeType(str, Enum):
    INDICATOR = "indicator"
    ML = "ml"
    LOGIC = "logic"
    ACTION = "action"
    INPUT = "input"
```
This is the single source of truth for the API contract.

### 2. The Rogue Definition
**File**: `routers/strategies.py` (Line 70)
```python
class NodeType(Enum):
    MARKET_DATA = "market_data"
    INDICATOR = "indicator"
    FEATURE = "feature"
    ML = "ml"
    LOGIC = "logic"
    SIGNAL = "signal"
    ACTION = "action"
```

### 3. The Validation Collision
**File**: `routers/strategies.py` (Line 168 and Line 82)
The compiler itself expects `VALID_NODE_TYPES = {"indicator", "ml", "logic", "action", "input"}`. 
However, the `TYPE_COMPATIBILITY` mapping uses the rogue `NodeType` definition.
When `DAGCompiler._validate_type_compatibility` runs, it checks if `node.type` (e.g., `"input"`) exists in `TYPE_COMPATIBILITY`. It does not, resulting in an immediate crash.

## Solution
1. Delete the local `NodeType` class from `routers/strategies.py`.
2. Import `NodeType` from `core.models.pydantic_models`.
3. Rewrite `TYPE_COMPATIBILITY` to use the correct 5 core node types.
