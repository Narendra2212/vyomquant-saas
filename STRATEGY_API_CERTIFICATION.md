# STRATEGY API CONTRACT CERTIFICATION

## Executive Summary
This audit evaluated the exact API contracts for the strategy management and execution endpoints. A **critical structural flaw** was discovered: while robust Pydantic schemas exist in `core/models/pydantic_models.py` (e.g., `StrategyBlueprint`, `DAGConfig`, `BacktestRequest`), the active API routes in `routers/strategies.py` **completely bypass them**, opting instead for untyped `Dict[str, Any]` payloads. This disables FastAPI's automatic validation, documentation, and error formatting.

Furthermore, a critical security vulnerability was discovered: the `/backtest` endpoint is unauthenticated.

---

### 1. Create Strategy (`POST /api/strategies`)

* **Exact Payload Schema:** Untyped `Dict[str, Any]`. The Pydantic model `StrategyBlueprint` is ignored.
* **Required Fields:** None enforced at the API layer.
* **Optional Fields:** `name`, `symbol`, `timeframe`, `buy_logic`, `sell_logic`, `risk`, `indicators`, `ml_model_path`, `exchange_id`, `nodes`, `edges`.
* **Response Schema:** Untyped JSON. `{"strategy_id": "uuid", "status": "created"}`.
* **Validation Constraints:** If `nodes` and `edges` are provided, they are passed to `DAGCompiler.compile`. Invalid DAGs will trigger a 400 or 500 error, but missing general fields will fail silently.
* **Authentication:** Required (`Depends(get_current_user)`).
* **Rate Limits:** No route-specific rate limiting applied.

---

### 2. Validate DAG (`POST /api/strategies/validate`)

* **Exact Payload Schema:** Untyped `Dict[str, Any]`.
* **Required Fields:** None enforced at the API layer.
* **Optional Fields:** `dag` (Object containing `nodes` and `edges`).
* **Response Schema:** 
  ```json
  {
      "valid": boolean,
      "errors": ["string"],
      "warnings": ["string"],
      "execution_path": ["string"],
      "node_types": { "node_id": "type" },
      "stats": { "total_nodes": int, "action_nodes": int, "estimated_time_ms": float }
  }
  ```
* **Validation Constraints:** Executes deep topological and cyclical DAG validation.
* **Authentication:** Required (`Depends(get_current_user)`).
* **Rate Limits:** None.

---

### 3. Run Backtest (`POST /api/strategies/backtest`)

* **Exact Payload Schema:** Untyped `dict`. The Pydantic model `BacktestRequest` is imported but ignored in the route signature.
* **Required Fields:** Either `dag` (object) OR `strategies` (array of strings). If neither is provided, returns an inline error object instead of an HTTP 422.
* **Optional Fields:** `strategy_name`, `symbols` (array), `timeframe`, `initial_capital` (float), `trade_size_pct` (float).
* **Response Schema:** Complex custom object (Untyped). Contains `total_return_pct`, `final_equity`, `equity` (curve array), `dag_results`, etc.
* **Validation Constraints:** Percentages must be decimals (e.g., 0.1 for 10%), but because the `BacktestRequest` validator is bypassed, users passing `10` instead of `0.1` will cause a runtime logical failure rather than a 422 Bad Request.
* **Authentication:** 🚨 **MISSING**. There is no `Depends(get_current_user)` on this route. It is open to the public, creating a severe compute exhaustion vulnerability.
* **Rate Limits:** None.

---

### 4. Deploy Bot (`POST /api/strategies/{id}/deploy`)

* **Exact Payload Schema:** Untyped `Dict[str, Any]`.
* **Required Fields:** `strategy_id` (Path parameter).
* **Optional Fields:** `exchange_id`, `user_id`.
* **Response Schema:** Untyped JSON. `{"status": "running"}`.
* **Validation Constraints:** 
  - Validates that the `strategy_id` exists and belongs to the authenticated user.
  - If `user_id` is passed in the body, it must match the authenticated token ID.
* **Authentication:** Required (`Depends(get_current_user)`).
* **Rate Limits:** Enforced via `check_deployment_limit` dependency (Subscription tier quota limit, not a TPS rate limit).
