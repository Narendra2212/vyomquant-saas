# BACKTEST RATE LIMIT CERTIFICATION

## Objective
Verify the exact post-hardening rate limiting implementation currently active for `POST /api/strategies/backtest`.

## 1. Exact File Paths
- **Route Implementation:** `d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1\routers\strategies.py`
- **Rate Limiter Implementation:** `d:\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1\core\rate_limiter.py`

## 2. Exact Rate Limiter Imported
```python
from core.rate_limiter import rate_limiter
```

## 3. Exact Rate Limiter Function Called
```python
allowed, remaining, ttl = await rate_limiter.is_allowed(user["id"], tier, limit_type="backtest")
```

## 4. Exact Line Numbers

**In `routers/strategies.py`:**
- **Line 1169:** `from core.rate_limiter import rate_limiter`
- **Line 1177:** `allowed, remaining, ttl = await rate_limiter.is_allowed(user["id"], tier, limit_type="backtest")`
- **Line 1178-1179:** Enforcement and exception raising:
  ```python
  if not allowed:
      raise HTTPException(status_code=429, detail=f"Rate limit exceeded for backtests. Try again in {ttl} seconds.")
  ```

**In `core/rate_limiter.py`:**
- **Lines 14-21:** Definition and backtest routing logic:
  ```python
  async def is_allowed(self, user_id: str, tier: str = "basic", limit_type: str = "copilot") -> tuple[bool, int, int]:
      """Returns (allowed, remaining, reset_in_seconds)."""
      r = await self._get_redis()
      key = f"rate:{limit_type}:{user_id}"
      
      if limit_type == "backtest":
          limit = 60 if tier in ["pro_999", "elite_1999", "premium"] else 5
          window = 60  # 1 minute
  ```

## 5. Actual Limits Evaluated
Based on `core/rate_limiter.py` evaluation of the `tier` parameter against `["pro_999", "elite_1999", "premium"]` for `limit_type == "backtest"` (with `window = 60` seconds):

* **free**: 5 backtests per minute (default fallback)
* **pro (`pro_999`)**: 60 backtests per minute
* **premium (`premium`)**: 60 backtests per minute
* **elite (`elite_1999`)**: 60 backtests per minute

## 6. Confirmation of System Used
The system is explicitly using:
**A) `core/rate_limiter.py`**

The `distributed_rate_limiter.py` is not being imported or utilized by the `backtest` route. The native Copilot rate limiter (`core/rate_limiter.py`) was extended to support isolated `limit_type` namespaces natively instead of duplicating systems.
