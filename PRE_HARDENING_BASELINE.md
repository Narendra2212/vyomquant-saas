# PRE-HARDENING BASELINE

## Objective
Capture the exact state of the system before Sprint 0 safety hardening begins to ensure a rollback point and a basis for comparison.

## 1. Current Route Signatures (`routers/strategies.py`)
- `create_strategy`: `async def create_strategy(body: Dict[str, Any], user: dict = Depends(get_current_user))`
- `validate_strategy`: `async def validate_strategy(body: Dict[str, Any], user: dict = Depends(get_current_user))`
- `backtest`: `def backtest(request: dict)`
- `deploy_bot`: `async def deploy_bot(strategy_id: str, body: Dict[str, Any], user: dict = Depends(get_current_user), fleet=Depends(get_fleet), ws_mgr=Depends(get_ws_manager), _limit=Depends(check_deployment_limit))`

## 2. Current OpenAPI Schema
- **`/api/strategies/` (POST)**
  - Request Body: `application/json` (Untyped `{}`)
  - Responses: `200 Successful Response` (Untyped `{}`)
- **`/api/strategies/validate` (POST)**
  - Request Body: `application/json` (Untyped `{}`)
  - Responses: `200 Successful Response` (Untyped `{}`)
- **`/api/strategies/backtest` (POST)**
  - Request Body: `application/json` (Untyped `{}`)
  - Responses: `200 Successful Response` (Untyped `{}`)

## 3. Existing Strategy Route Behavior
- All endpoints rely on internal dictionary access (`request.get("key")`).
- `backtest` supports both legacy list format (`{"strategies": ["rsi"]}`) and DAG format (`{"dag": {"nodes": [], "edges": []}}`).
- Pydantic models (like `BacktestRequest`) exist in `core/models/pydantic_models.py` but are bypassed.

## 4. Existing Authentication Behavior
- Strategy CRUD routes use `Depends(get_current_user)` enforcing JWT bearer authentication.
- **CRITICAL:** `POST /api/strategies/backtest` lacks `Depends(get_current_user)` and is fully open to unauthenticated actors.

## 5. Existing Rate Limiter Implementation
- `core/rate_limiter.py` exposes a `rate_limiter` instance using Redis.
- `rate_limiter.is_allowed(user_id, tier)` returns `(allowed, remaining, ttl)`.
- Currently, this compute rate limiter is **not** applied to the `/backtest` route.

## 6. Existing Pydantic Models (`core/models/pydantic_models.py`)
- `StrategyBlueprint`: Complete schema for creating/updating strategies.
- `BacktestRequest`: Complete schema for backtests including parameters and DAG structure.
- `ValidateStrategyRequest`: **MISSING.** Currently, no specific Pydantic model exists for the validate endpoint wrapper (needs `dag: DAGConfig`).
- `DeployRequest`: Existing schema expecting `exchange_id: str`.

## 7. Status Check
- Backend Server: **ONLINE** (Verified via Uvicorn local start on port 8000).
- OpenAPI Loading: **SUCCESSFUL** (Schema successfully fetched).
- Existing Strategy Routes: **ONLINE** (Verified in Swagger schema).
