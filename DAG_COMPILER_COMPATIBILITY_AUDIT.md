# DAG COMPILER COMPATIBILITY AUDIT

## Objective
Ensure the frontend implementation perfectly matches the backend `DAGCompiler` contract, and identify any discrepancies in the backend itself before development starts.

## Executive Summary
The backend `DAGCompiler` has **two critical logic flaws** that make it mathematically impossible to execute a valid DAG. The frontend cannot be successfully wired until these are fixed in the backend.

### Critical Finding 1: Enum Mismatch
The Pydantic model and `DAGCompiler.VALID_NODE_TYPES` accept the node type `"input"`. However, the edge validation matrix (`TYPE_COMPATIBILITY` in `strategies.py`) expects the type `"market_data"`. 
**Result:** Any edge originating from an `"input"` node will trigger an immediate `DAGCompilationError` because `"input"` is not in the `TYPE_COMPATIBILITY` dictionary.

### Critical Finding 2: Missing Constant Nodes
The `LogicExecutor` (which processes `GT`, `LT`, `EQ` operations) expects two inputs via incoming edges (e.g., `aligned.iloc[:, 0] > aligned.iloc[:, 1]`). However, there is no `Constant` or `Scalar` node type defined in `VALID_NODE_TYPES`. 
**Result:** It is impossible to compare an indicator (like RSI) to a static threshold (like 70), because there is no node that can supply the "70" through an edge, and the Logic node does not accept threshold parameters directly.

---

## 1. Exact Required Fields
According to the `DAGNode` Pydantic model, only two fields are strictly required by the schema:
*   `id` (str)
*   `type` (NodeType enum: "indicator", "ml", "logic", "action", "input")

However, the execution engine (`dag_engine.py`) enforces strict presence of data during execution:
*   **Indicator**: Requires `indicator` string and `params` dict.
*   **ML**: Requires `model_id`.
*   **Logic**: Requires `operator`.
*   **Action**: Requires `action`.

## 2. Exact Optional Fields
The Pydantic model defines the following as `Optional`:
*   `label`
*   `indicator`
*   `params`
*   `model_id`
*   `confidence_threshold` (default 0.7)
*   `operator`
*   `action`
*   `order_type` (default "market")
*   `amount`
*   `symbol`
*   `timeframe`

## 3. Supported Indicators
Extracted from `IndicatorExecutor.execute()`:
*   `rsi` (requires `period`)
*   `macd` (requires `fast`, `slow`, `signal`)
*   `sma` (requires `period`)
*   `ema` (requires `period`)
*   `bb` (requires `period`, `std_dev`)
*   `atr` (requires `period`)

## 4. Supported Logic Operators
Extracted from `LogicOperator` Enum:
*   `AND`, `OR`, `NOT`, `GT`, `LT`, `EQ`, `GTE`, `LTE`

## 5. Supported Action Types
Extracted from `ActionExecutor.execute()`:
*   `buy`
*   `sell`
*   `hold`

## 6. Thresholds & Constant Nodes
*   **Do constant nodes exist?** NO.
*   **Are threshold values expected inside logic nodes?** NO. The `LogicExecutor` specifically iterates over `inputs.items()` (incoming edges). It does not look at the node's `params` or a `threshold` attribute. It expects two incoming series/scalars.

## 7. Edge Validation Rules
Validated in `DAGCompiler._validate_type_compatibility`. A strict adjacency matrix is enforced, which is currently broken due to the `input` vs `market_data` mismatch.

## 8. Node Connection Rules
Validated in `DAGCompiler._check_connectivity` and `_detect_orphans`.
*   Every node must be reachable from an `input` node via BFS.
*   There must be no orphan nodes.
*   Execution must reach at least one `action` node.
*   The graph must contain no cycles (DFS validation).
