# 🔥 STEP 5 — RATE LIMITING SYSTEM

## Goal: Prevent User Overload for 500 Users (≈150 Active)

**Focus:**
- No system abuse
- Stable execution
- Fair resource allocation

---

## PROBLEM

Without rate limiting:
- ❌ Users can spam trade requests
- ❌ System overload from burst traffic
- ❌ Single user can impact others
- ❌ No protection against runaway strategies
- ❌ Resource exhaustion attacks

---

## SOLUTION: MULTI-TIER RATE LIMITING

### Per-User Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| **Trades/Second** | 5 | Prevent API spam |
| **Trades/Minute** | 100 | Prevent burst abuse |
| **Open Positions** | 20 | Prevent position bloat |

### Storage Schema

```
rate_limit:user:{id}:trades:second  → ZSET (timestamp scores)
rate_limit:user:{id}:trades:minute  → ZSET (timestamp scores)
rate_limit:user:{id}:positions      → STRING (count)
```

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `core/rate_limiter.py` | Core rate limiting logic | 300+ |
| `core/rate_limit_middleware.py` | FastAPI integration | 200+ |
| `SCALING_STEP_5_SUMMARY.md` | This documentation | - |

---

## RATE LIMITER (`core/rate_limiter.py`)

### Features

- **Sliding window**: Precise rate limiting (not bucket-based)
- **Redis-backed**: Distributed across workers
- **Automatic cleanup**: Keys expire after window period
- **Multiple limits**: Per-second, per-minute, positions
- **Status reporting**: Real-time limit status

### Usage

```python
from core.rate_limiter import rate_limiter, RateLimitExceeded

# Check trade allowed
try:
    await rate_limiter.check_trade_allowed(user_id="user-123")
    # Execute trade...
except RateLimitExceeded as e:
    return {"error": e.message, "retry_after": e.retry_after}

# Check position limit
can_open = await rate_limiter.can_open_position(user_id="user-123")

# Get status
status = await rate_limiter.get_status(user_id="user-123")
print(f"Trades/sec: {status.trades_per_second}/{status.trades_per_second_limit}")
```

### Methods

| Method | Purpose |
|--------|---------|
| `check_trade_allowed(user_id)` | Validates trade/sec and trade/min limits |
| `can_open_position(user_id)` | Check if under position limit |
| `check_position_limit(user_id)` | Validates position limit, raises if exceeded |
| `get_open_position_count(user_id)` | Current open positions |
| `increment_position_count(user_id)` | Add position (on open) |
| `decrement_position_count(user_id)` | Remove position (on close) |
| `get_status(user_id)` | Full rate limit status |
| `reset_limits(user_id)` | Admin reset |

---

## FASTAPI MIDDLEWARE (`core/rate_limit_middleware.py`)

### Automatic Protection

Middleware automatically rate-limits these endpoints:
- `POST /api/orders/*`
- `POST /api/trades/*`
- `POST /api/positions/open`
- `POST /api/execution`

### Usage

```python
from fastapi import FastAPI
from core.rate_limit_middleware import RateLimitMiddleware

app = FastAPI()
app.add_middleware(RateLimitMiddleware)
```

### Response Headers

```
X-RateLimit-Trades-Second: 3/5
X-RateLimit-Trades-Minute: 45/100
X-RateLimit-Positions: 12/20
```

### Rate Limit Response (HTTP 429)

```json
{
  "error": "Rate limit exceeded",
  "detail": "Rate limit exceeded: trades_per_second (5/5). Retry after 0.8s",
  "limit_type": "trades_per_second",
  "current": 5,
  "limit": 5,
  "retry_after": 0.8
}
```

### Decorator for Custom Endpoints

```python
from core.rate_limit_middleware import require_rate_limit

@app.post("/api/custom-trade")
@require_rate_limit
async def custom_trade(request: Request):
    ...
```

---

## ARCHITECTURE

```
┌─────────────────────────────────────────┐
│           USER REQUEST                  │
│  POST /api/orders/place                 │
└──────────────┬──────────────────────────┘
               │
        ┌──────▼──────┐
        │  Middleware │  ← Extracts user_id
        │  Rate Check │  ← Checks Redis
        └──────┬──────┘
               │
       ┌───────┴───────┐
       │               │
   ┌───▼───┐      ┌───▼───┐
   │ALLOW  │      │DENY   │
   │        │      │(429)  │
   └───┬───┘      └───┬───┘
       │               │
  ┌────▼────┐     ┌────▼────┐
  │Process  │     │Return   │
  │Request  │     │Error    │
  └─────────┘     └─────────┘
```

---

## REDIS DATA STRUCTURE

### Trades/Second (Sliding Window)

```
ZADD rate_limit:user:123:trades:second 1699123456.789 "1699123456.789"
ZADD rate_limit:user:123:trades:second 1699123456.912 "1699123456.912"
ZADD rate_limit:user:123:trades:second 1699123457.034 "1699123457.034"

ZREMRANGEBYSCORE key 0 (now - 1.0)  ← Remove old entries
ZCARD key  ← Count current window (must be < 5)
```

### Positions

```
SET rate_limit:user:123:positions 12
INCR rate_limit:user:123:positions  ← On open
DECR rate_limit:user:123:positions  ← On close
```

---

## TESTING

### Test Rate Limits

```python
import asyncio
from core.rate_limiter import rate_limiter, RateLimitExceeded

async def test_rate_limits():
    user_id = "test-user"
    
    # Test trades/second (should allow 5, reject 6th)
    for i in range(7):
        try:
            await rate_limiter.check_trade_allowed(user_id)
            print(f"Trade {i+1}: ALLOWED")
        except RateLimitExceeded:
            print(f"Trade {i+1}: DENIED (rate limit)")
    
    # Reset for next test
    await rate_limiter.reset_limits(user_id)

asyncio.run(test_rate_limits())
```

### Test Position Limit

```python
async def test_position_limit():
    user_id = "test-user"
    
    # Add 20 positions
    for i in range(20):
        await rate_limiter.increment_position_count(user_id)
    
    # 21st should fail
    can_open = await rate_limiter.can_open_position(user_id)
    print(f"Can open 21st position: {can_open}")  # False

asyncio.run(test_position_limit())
```

---

## MONITORING

### Alerts

| Alert | Condition | Severity |
|-------|-----------|----------|
| RateLimitViolations | > 100/minute | warning |
| RateLimitBlocked | > 50% requests | critical |

### Metrics

```python
# Track rate limit hits
rate_limit_hits_total{user_id="123", limit_type="trades_per_second"}
rate_limit_positions_current{user_id="123"}
```

---

## EXPECTED RESULTS

### Before (No Rate Limiting)
- ❌ Users can spam unlimited trades
- ❌ Burst traffic causes system overload
- ❌ No protection against abuse

### After (With Rate Limiting)
- ✅ Max 5 trades/second per user
- ✅ Max 100 trades/minute per user
- ✅ Max 20 open positions per user
- ✅ Fair resource allocation
- ✅ System stability maintained

---

## SUMMARY

**Goal:** Prevent user overload for 500 users

**Step 5 Complete:** ✅
- Rate limiter with sliding window
- Per-user limits (5/sec, 100/min, 20 positions)
- FastAPI middleware for automatic protection
- Redis-backed distributed rate limiting

**Limits:**
- Trades/Second: 5
- Trades/Minute: 100
- Open Positions: 20

**Status:** Ready for production
