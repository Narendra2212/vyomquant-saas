# FEATURE USAGE AUDIT

## Search Methodology
Searched entire repository for `FEATURE`, `"feature"`, and `NodeType.FEATURE`.

## Occurrences as a Node Type
1.  **File:** `routers/strategies.py`
    *   **Line:** 74 (`FEATURE = "feature"`)
    *   **Usage:** Enum definition in local `NodeType` class.
    *   **Runtime Criticality:** Low. Causes validation mismatches.
2.  **File:** `routers/strategies.py`
    *   **Line:** 83-85 (`NodeType.FEATURE.value`)
    *   **Usage:** References in `TYPE_COMPATIBILITY` map.
    *   **Runtime Criticality:** Low. Dead code path because incoming nodes use Pydantic which strips `feature` nodes.
3.  **File:** `frontend/strategyValidator.js`
    *   **Line:** 222 (`'feature': ['ml', 'logic']`)
    *   **Usage:** Frontend visual graph validator type map.
    *   **Runtime Criticality:** Low. Controls edge drawing UI.

## Other Occurrences (Not Node Types)
1.  **Files:** `test_feature_engineering.py`, `test_ml_pipeline.py`
    *   **Usage:** Print statements and log outputs (e.g., `print("🔧 FEATURE ENGINEERING TEST")`).
    *   **Runtime Criticality:** Zero.

## Conclusion
The `feature` node type does not exist in the canonical Pydantic model (`DAGNode`). It is entirely legacy dead-code residing solely in local configuration maps in `strategies.py` and the frontend validation script.
