# LOGIC EXECUTION AUDIT

## The Issue
The backend `LogicExecutor` evaluates mathematical conditions (like Greater Than, Less Than) using the `aligned` Pandas DataFrame constructed from incoming edges. 

**File**: `backend/dag_engine.py`
```python
        elif operator == "GT":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] > aligned.iloc[:, 1]
```

This implementation assumes that the DAG will always provide two incoming edges to a Logic node (e.g. `RSI_Node -> Logic_Node` and `Constant_Node -> Logic_Node`). 
However, **there is no `Constant` or `Scalar` node type in the backend.** It is therefore impossible to supply the second parameter (the threshold) via an edge. 

If only one edge is connected (e.g., `RSI -> GT`), `len(input_series)` is `1`, meaning the block is entirely bypassed, and the Logic node outputs a default `False` series.

## Solution
The `LogicExecutor` must be able to read a static value directly from the Logic node's own parameters. Since all `DAGNode` instances contain a `params` dictionary, we can seamlessly inject a `threshold` parameter directly into the Logic node itself.

```python
# Desired Node Configuration
{
  "id": "logic-1",
  "type": "logic",
  "operator": "GT",
  "params": {
    "threshold": 70.0
  }
}
```

The `LogicExecutor.execute` method should be patched to extract `params.get("threshold")` and append it to `input_series` if it exists. This solves the scalar issue cleanly without needing to invent a new `Constant` node type or update the Pydantic schemas.
