# 🔥 STEP 9 — BACKPRESSURE SYSTEM

## Goal: Prevent System Overload for 500 Users (≈150 Active)

**Focus:**
- No system crash under load
- Graceful degradation
- Load shedding
- Priority-based rejection

---

## PROBLEM

Without backpressure:
- ❌ System crashes when queues overflow
- ❌ No graceful degradation
- ❌ Critical tasks dropped with background tasks
- ❌ Cascading failures
- ❌ Recovery takes long time

---

## SOLUTION: BACKPRESSURE SYSTEM

### Strategy

```
IF queue_size > threshold:
    THEN slow down new tasks
    THEN reject low-priority signals
    THEN shed load gracefully
```

### Load Levels

| Level | Queue Size | Action |
|-------|-----------|--------|
| **NORMAL** | < 50 | Accept all tasks |
| **WARNING** | 50-100 | Slow down, reduce low-priority |
| **CRITICAL** | 100-200 | Reject low-priority |
| **EMERGENCY** | 200+ | Reject all but critical |

### Priority Levels

| Priority | Task Types | Drop At |
|----------|-----------|---------|
| **CRITICAL** | Emergency liquidation | Never |
| **HIGH** | Order execution, margin calls | Emergency only |
| **NORMAL** | DAG tasks, strategies | Critical |
| **LOW** | Portfolio snapshots, PnL reports | Warning |
| **BACKGROUND** | Log cleanup, metrics | Any load |

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `core/backpressure.py` | Backpressure controller | 400+ |
| `SCALING_STEP_9_SUMMARY.md` | This documentation | - |

---

## BACKPRESSURE CONTROLLER (`core/backpressure.py`)

### Features

- **Graduated response**: Different actions per load level
- **Priority-based rejection**: Critical tasks preserved
- **Probabilistic acceptance**: Smooth transitions
- **Circuit breaker**: Protects overloaded subsystems
- **Metrics**: Track rejections and load levels
- **Admission control**: Context manager for easy use

### Usage

#### Check Before Queueing

```python
from core.backpressure import backpressure, Priority

# Check if task can be accepted
if backpressure.can_accept(Priority.HIGH):
    await task_queue.put(task)
else:
    logger.warning("Task rejected due to backpressure")
    # Handle rejection (notify user, retry later, etc.)
```

#### Admission Control (Context Manager)

```python
from core.backpressure import backpressure, Priority

# Automatically rejects if load too high
async with backpressure.admission_control(Priority.NORMAL):
    await process_task(task)
```

#### Wait for Acceptance

```python
# Try to accept, waiting up to 5 seconds
if await backpressure.try_accept(Priority.HIGH, timeout=5.0):
    await task_queue.put(task)
else:
    raise HTTPException(503, "Service temporarily unavailable")
```

#### Automatic Priority Detection

```python
priority = backpressure.get_priority_for_task("order_execution")
# Returns Priority.HIGH

priority = backpressure.get_priority_for_task("log_cleanup")
# Returns Priority.BACKGROUND
```

### Thresholds

```python
from core.backpressure import BackpressureConfig

config = BackpressureConfig(
    warning_threshold=50,
    critical_threshold=100,
    emergency_threshold=200
)

backpressure = BackpressureController(config)
```

---

## CIRCUIT BREAKER

### Pattern

```
CLOSED  ──failures──>  OPEN  ──timeout──>  HALF_OPEN  ──success──>  CLOSED
 (OK)      >= 5          (Reject)    60s      (Test)      3 calls      (OK)
```

### Usage

```python
from core.backpressure import CircuitBreaker

cb = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60.0
)

if await cb.can_execute():
    try:
        result = await risky_operation()
        await cb.record_success()
    except Exception:
        await cb.record_failure()
        raise
else:
    # Circuit breaker open, reject immediately
    raise ServiceUnavailable()
```

---

## INTEGRATION

### Task Queue Integration

```python
from core.backpressure import backpressure, Priority
from core.scaling_metrics import scaling_metrics

async def submit_task(task_type: str, data: dict):
    # Update backpressure with current queue size
    queue_size = await get_queue_size()
    backpressure.update_queue_size("dag", queue_size)
    scaling_metrics.set_queue_size("dag", queue_size)
    
    # Determine priority
    priority = backpressure.get_priority_for_task(task_type)
    
    # Check backpressure
    if not backpressure.can_accept(priority):
        # Reject task
        backpressure.record_rejection(priority, "queue_full")
        raise HTTPException(
            status_code=503,
            detail=f"System overloaded. Task {task_type} rejected. Retry later."
        )
    
    # Accept task
    await task_queue.put({"type": task_type, "data": data, "priority": priority.value})
```

### WebSocket Integration

```python
from core.backpressure import backpressure, Priority

async def handle_ws_message(message: dict):
    # High priority for order-related messages
    if message["type"] in ["place_order", "cancel_order"]:
        priority = Priority.HIGH
    else:
        priority = Priority.NORMAL
    
    async with backpressure.admission_control(priority):
        await process_message(message)
```

### FastAPI Middleware

```python
from fastapi import FastAPI, HTTPException
from core.backpressure import backpressure, Priority

app = FastAPI()

@app.middleware("http")
async def backpressure_middleware(request, call_next):
    # Skip health checks
    if "/health" in request.url.path:
        return await call_next(request)
    
    # Determine priority based on endpoint
    if "/api/orders" in request.url.path:
        priority = Priority.HIGH
    elif "/api/admin" in request.url.path:
        priority = Priority.CRITICAL
    else:
        priority = Priority.NORMAL
    
    # Check backpressure
    if not backpressure.can_accept(priority):
        return HTTPException(
            status_code=503,
            detail="Service temporarily unavailable due to high load"
        )
    
    return await call_next(request)
```

---

## MONITORING

### Metrics

```python
# Get current stats
stats = backpressure.get_stats()
print(stats)
# {
#     "total_accepted": 15432,
#     "total_rejected": 234,
#     "rejection_by_priority": {
#         "CRITICAL": 0,
#         "HIGH": 12,
#         "NORMAL": 145,
#         "LOW": 67,
#         "BACKGROUND": 10
#     },
#     "current_load_level": "warning",
#     "queue_sizes": {
#         "dag": 67,
#         "execution": 12,
#         "portfolio": 34
#     }
# }
```

### Alerts

| Alert | Condition | Severity |
|-------|-----------|----------|
| BackpressureWarning | Load level = WARNING | warning |
| BackpressureCritical | Load level = CRITICAL | critical |
| BackpressureEmergency | Load level = EMERGENCY | critical |
| CircuitBreakerOpen | Circuit breaker opened | critical |

---

## TESTING

### Test Backpressure

```python
import asyncio
from core.backpressure import backpressure, Priority, BackpressureConfig

async def test_backpressure():
    # Set high load
    backpressure.update_queue_size("dag", 150)  # CRITICAL
    
    # CRITICAL priority should pass
    assert backpressure.can_accept(Priority.CRITICAL) is True
    
    # HIGH priority should mostly pass (90%)
    # NORMAL priority should partially pass (50%)
    # LOW priority should be rejected (0%)
    assert backpressure.can_accept(Priority.LOW) is False
    
    print("[PASS] Backpressure working correctly")

asyncio.run(test_backpressure())
```

### Test Circuit Breaker

```python
from core.backpressure import CircuitBreaker

async def test_circuit_breaker():
    cb = CircuitBreaker(failure_threshold=3)
    
    # Should start closed
    assert cb.get_state() == "CLOSED"
    
    # Record failures
    for _ in range(3):
        await cb.record_failure()
    
    # Should be open now
    assert cb.get_state() == "OPEN"
    assert await cb.can_execute() is False
    
    print("[PASS] Circuit breaker working correctly")

asyncio.run(test_circuit_breaker())
```

---

## EXPECTED RESULTS

### Before (No Backpressure)
- ❌ System crashes when overloaded
- ❌ All tasks rejected equally (including critical)
- ❌ Cascading failures
- ❌ Long recovery time

### After (With Backpressure)
- ✅ Graceful degradation under load
- ✅ Critical tasks always processed
- ✅ Low-priority tasks shed first
- ✅ Faster recovery
- ✅ Predictable behavior

---

## SUMMARY

**Goal:** Prevent system crash under load for 500 users

**Step 9 Complete:** ✅
- Graduated load response (4 levels)
- Priority-based task acceptance
- Circuit breaker pattern
- Queue size monitoring
- Metrics and alerting
- Easy integration (context manager, decorator)

**Thresholds:**
- Warning: 50 tasks
- Critical: 100 tasks
- Emergency: 200 tasks

**Priorities:**
- CRITICAL: Never dropped
- HIGH: Drop only in emergency
- NORMAL: Drop in critical
- LOW: Drop in warning
- BACKGROUND: Drop first

**Status:** Ready for production
