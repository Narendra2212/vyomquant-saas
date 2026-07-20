# THRESHOLD NODE DESIGN REVIEW

## Problem Statement
The backend DAG engine requires a mechanism to compare continuous values (like RSI or MACD) against static thresholds (like `70` or `0`). 

There are two potential architectural approaches to solve this:
1. **Approach A:** Introduce a new `Constant` node type that outputs a static value to the graph.
2. **Approach B:** Embed a `threshold` parameter directly into the existing `Logic` node.

## Evaluation

### Approach A: New `Constant` Node
*   **Pros:** Highly flexible. A single constant could theoretically be wired to multiple logic nodes.
*   **Cons:** 
    *   Requires modifying the Pydantic API contract (`NodeType` Enum) to include a new type, which is a breaking change for existing clients/strategies.
    *   Requires updating `VALID_NODE_TYPES` in the compiler.
    *   Adds unnecessary clutter to the frontend DAG canvas (users having to drag a "Number" block and wire it into every single comparison).

### Approach B: Embedded `threshold` Parameter (Chosen Path)
*   **Pros:**
    *   **Zero API Changes**: The `DAGNode` schema already exposes an optional `params: Dict` field for *all* node types. We simply use it.
    *   **Cleaner UI**: Users configure the threshold directly inside the Logic node's properties panel.
    *   **Minimal Backend Patch**: Only requires a 4-line patch in `LogicExecutor.execute` to read the parameter.
*   **Cons:**
    *   Less reusable if multiple nodes need the exact same threshold, but in strategy design, thresholds are usually highly specific to the operator anyway.

## Conclusion
We will adopt **Approach B**. 
The `Logic` node in the frontend will contain a scalar input field mapped to `node.data.params.threshold`. The backend `LogicExecutor` will detect this key and automatically promote it to a Pandas Series during execution to perform vectorized comparisons.
