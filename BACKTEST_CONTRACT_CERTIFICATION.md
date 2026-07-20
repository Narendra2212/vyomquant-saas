# BACKTEST CONTRACT CERTIFICATION

## Objective
Verify the exact function signature and dependencies used for the `POST /api/strategies/backtest` route in `routers/strategies.py`.

## Code Verification (`routers/strategies.py`)

### 1. Decorator
```python
@router.post("/backtest")
```

### 2. Exact Function Signature
```python
async def backtest(request: BacktestRequest, user: dict = Depends(get_current_user)):
```

### 3. Imported Pydantic Model
The Pydantic model is explicitly imported at the file level:
```python
from core.models import BacktestRequest, StrategyBlueprint, DeployRequest
```

### 4. Authentication Dependency
Authentication is enforced at the parameter level using FastAPI's dependency injection:
```python
user: dict = Depends(get_current_user)
```

### 5. Rate Limiter Dependency
The rate limiter is imported inline and explicitly called within the route logic:
```python
from core.rate_limiter import rate_limiter
# ...
allowed, remaining, ttl = await rate_limiter.is_allowed(user["id"], tier, limit_type="backtest")
```

## OpenAPI Schema Certification
I queried the live FastAPI `/openapi.json` endpoint to verify how this route is exposed to frontend clients.

The OpenAPI schema explicitly registers the route's requestBody schema as:
```json
{
  "$ref": "#/components/schemas/BacktestRequest"
}
```

**Confirmation:** FastAPI OpenAPI correctly displays the strongly-typed **`BacktestRequest`** model rather than a generic object. The unvalidated dictionary payload has been completely eliminated from the Swagger contract.
