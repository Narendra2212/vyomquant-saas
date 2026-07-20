# STRATEGY HARDENING PLAN

## 1. Existing Pydantic Models
The `core/models/pydantic_models.py` file already contains robust, well-defined schemas for strategy management:
- `StrategyBlueprint`: Models the full strategy configuration including `buy_logic`, `sell_logic`, `risk`, and `indicators`.
- `DAGNode` / `DAGEdge` / `DAGConfig`: Strongly typed components for the visual DAG builder, including strict enums for `NodeType` and `LogicOperator`.
- `BacktestRequest`: Full schema for backtesting with strict validation (e.g., `trade_size_pct` must be decimal <= 1.0).
- `DeployRequest`: Simple schema containing `exchange_id`.
- `TrainModelRequest`: Strict schema enforcing fields like `strategy_name`, `symbol`, and `indicators`.

## 2. Routes Currently Bypassing Schemas
All major endpoints in `routers/strategies.py` currently bypass the Pydantic type system:
- `POST /api/strategies/` uses `body: Dict[str, Any]` instead of `StrategyBlueprint` or a dedicated creation schema.
- `POST /api/strategies/validate` uses `body: Dict[str, Any]` instead of `DAGConfig`.
- `POST /api/strategies/backtest` uses `request: dict` instead of `BacktestRequest`.
- `POST /api/strategies/{id}/deploy` uses `body: Dict[str, Any]` instead of `DeployRequest`.

## 3. Exact Migration Path
1. **Update Route Signatures:**
   - Change `async def create_strategy(body: Dict[str, Any], ...)` to `async def create_strategy(body: StrategyBlueprint, ...)`.
   - Change `def backtest(request: dict)` to `def backtest(request: BacktestRequest)`.
   - Change `async def deploy_bot(strategy_id: str, body: Dict[str, Any], ...)` to `async def deploy_bot(strategy_id: str, body: DeployRequest, ...)`.
2. **Refactor Attribute Access:**
   - Replace dictionary `.get()` syntax (e.g., `request.get("dag")`) with Pydantic dot notation (e.g., `request.dag`).
   - Update `DAGCompiler` calls to handle Pydantic objects instead of dictionaries, or use `.model_dump()` before passing them.
3. **Remove Redundant Validation:**
   - Delete manual checks for required fields that Pydantic now handles automatically.
   - Delete manual bounds checking that overlaps with `Field(ge=0, le=1)` validators.

## 4. Backwards Compatibility Impact
- **Strict Enforcement:** The frontend must send perfectly typed JSON. Sending a string `"10"` where a float `10.0` is expected, or missing a required field, will now immediately reject the request with an HTTP 422 Unprocessable Entity.
- **Legacy Fallback:** The `BacktestRequest` schema natively supports the legacy `strategies` array mode, so older API clients will still function if they meet the strict type requirements.

## 5. OpenAPI Improvements Gained
- **Swagger Documentation:** The Swagger UI will automatically display the full nested schemas, allowing developers and the Copilot to understand exactly what to send.
- **Error Formatting:** Standardized FastAPI 422 error details will tell the client exactly which nested field failed and why (e.g., "trade_size_pct must be <= 1.0").

## 6. Frontend Changes Required
- The frontend will need to correctly serialize percentage forms. `10%` must be sent as `0.1` to pass Pydantic validation.
- The frontend API client must be prepared to handle `422 Unprocessable Entity` responses and parse the `detail` array to map errors back to specific UI form fields.

## 7. Security Issues Resolved
- **Payload Bloat:** Pydantic automatically strips unrecognized fields unless `extra="allow"` is explicitly set, mitigating the risk of NoSQL injection or mass assignment vulnerabilities.
- **Logical Failures:** By enforcing strict decimals and boundaries on risk parameters, the system prevents extreme integer values from causing liquidation or simulation crashes.
