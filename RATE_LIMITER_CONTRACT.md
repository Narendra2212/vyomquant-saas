# RATE LIMITER CONTRACT AUDIT

## Objective
Identify the exact contracts of the available rate limiting systems to ensure Sprint 0 hardening uses correct method signatures and architecture.

## 1. Copilot Rate Limiter (`core/rate_limiter.py`)
- **Instance Name:** `rate_limiter`
- **Method:** `async def is_allowed(self, user_id: str, tier: str = "basic") -> tuple[bool, int, int]`
- **Return Value:** `(allowed, remaining, reset_in_seconds)`
- **Behavior:**
  - Token bucket via Redis pipeline.
  - Hardcoded key prefix `rate:copilot:{user_id}`.
  - Limits are drawn from `settings.copilot_rate_limit_premium` and `settings.copilot_rate_limit_basic` (default 24h window).
- **Suitability for Backtest:** Poor fit. Limits are hardcoded to "copilot" namespace and 24-hour windows.

## 2. Distributed Rate Limiter (`core/distributed_rate_limiter.py`)
- **Instance Access:** `get_distributed_rate_limiter()` or singleton injection.
- **Method:** `async def check_rate_limit(self, user_id: str, limit_type: RateLimitType, custom_limit: Optional[int] = None) -> RateLimitStatus`
- **Return Value:** `RateLimitStatus` (dataclass with `.allowed`, `.current_count`, `.limit`, `.remaining`, `.reset_time`, `.window_seconds`).
- **Raises:** `RateLimitExceeded` (if using `acquire()`).
- **Behavior:**
  - Generates key `rate:{user_id}:{limit_type}:{window_timestamp}`.
  - Supports custom limits and fine-grained time windows.
- **Suitability for Backtest:** Ideal architecture.

## 3. Subscription Tier Limits (`core/dependencies.py`)
- **Helper:** `_get_cached_profile(user_id, supabase) -> dict`
- **Tier Attribute:** `profile.get("subscription_tier", "free")`

## SPRINT 0 RATE LIMITER IMPLEMENTATION STRATEGY
Since the goal is to target 5 backtests/min for free users and preserve existing architecture without duplicating limiters:
1. We will use the `DistributedRateLimiter` architecture, which already supports custom limits and time windows.
2. We will inject a limit type `API_CALLS_PER_MINUTE` or define a custom configuration block within the route.
3. To strictly follow the "use existing architecture" mandate, we will use `rate_limiter.is_allowed(user_id, tier)` from `core/rate_limiter.py` ONLY if we repurpose it, BUT it hardcodes the prefix to "copilot".
4. *Decision:* Since `core/distributed_rate_limiter.py` is the actual "STEP 6 - DISTRIBUTED RATE LIMITING" designed for scaling, we will use it with a custom limit override to hit the 5/min target. We will use `await rate_limiter.acquire(user["id"], RateLimitType.API_CALLS_PER_SECOND, custom_limit=5)` (adjusted for minute windows by defining a new enum or using custom windows).
