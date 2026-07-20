# OPENAPI DIFF REPORT

## Pre-Hardening Baseline
In the pre-hardening baseline, the strategy execution layer (`routers/strategies.py`) relied heavily on unvalidated dictionary payloads and lacked proper security schemas.

### Original Endpoints & Contracts
1. **`POST /api/strategies/`**
   - **Request Schema:** Unconstrained `Dict[str, Any]` represented in OpenAPI as `object`.
   - **Security:** Requires HTTPBearer.
   
2. **`POST /api/strategies/validate`**
   - **Request Schema:** Unconstrained `Dict[str, Any]`.
   - **Security:** Requires HTTPBearer.

3. **`POST /api/strategies/backtest`**
   - **Request Schema:** Unconstrained `Dict[str, Any]`.
   - **Security:** **NONE**. Unauthenticated.

4. **`POST /api/strategies/{id}/deploy`**
   - **Request Schema:** Unconstrained `Dict[str, Any]`.
   - **Security:** Requires HTTPBearer.

---

## Post-Hardening State (Current)
The strategy execution layer now strictly enforces Pydantic-backed schemas, drastically improving the precision and security of the API definitions.

### New Endpoints & Contracts
1. **`POST /api/strategies/`**
   - **Request Schema:** Enforces `StrategyBlueprint` model.
     - Mandates `name`, `buy_logic` (LogicBlock), `sell_logic` (LogicBlock), `risk` (RiskParameters).
     - Explicitly types `indicators` as `List[str]`.
   - **Security:** Requires HTTPBearer.

2. **`POST /api/strategies/validate`**
   - **Request Schema:** Enforces `BacktestRequest` model.
     - Specifically validates nested `dag` configurations (`DAGConfig`).
   - **Security:** Requires HTTPBearer.

3. **`POST /api/strategies/backtest`**
   - **Request Schema:** Enforces `BacktestRequest` model.
     - Rejects any values outside safe operational bounds:
       - `trade_size_pct` enforced `> 0` and `<= 1.0`
       - `stop_loss_pct` enforced `<= 1.0`
       - `take_profit_pct` enforced `<= 1.0`
     - Requires strict nested `DAGConfig` structures.
   - **Security:** **ADDED**. Now explicitly requires `HTTPBearer` authentication. Open access has been shut down.
   - **Responses:** Added `429 Too Many Requests` mapping for the new rate limiter.

4. **`POST /api/strategies/{id}/deploy`**
   - **Request Schema:** Enforces `DeployRequest` model.
     - Exposes only the properties meant for deployment configuration, rather than passing through unvalidated dict injections.
   - **Security:** Requires HTTPBearer.

## Key Swagger/Client Impacts
- The auto-generated Swagger UI now accurately generates forms for backtest configurations and strategy blueprints.
- Frontend clients must send authorization tokens to the `/backtest` route.
- Unintentional parameter injection via extra object keys in deployment configurations is automatically sanitized (`model_dump(exclude_unset=True)`).
- Bound constraints (`trade_size_pct` > 1.0) now fail fast at the Pydantic API layer rather than bubbling down to crash the portfolio engine at runtime.
