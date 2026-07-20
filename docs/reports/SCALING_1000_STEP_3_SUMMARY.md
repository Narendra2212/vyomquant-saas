# 🔥 STEP 3 — BACKPRESSURE + LOAD SHEDDING

## Goal: Prevent System Overload for 1000+ Users

**Focus:**
- System degrades gracefully
- No crashes under spikes
- Critical execution preserved
- Analytics shed first

---

## PROBLEM

Without advanced backpressure:
- ❌ System crashes under traffic spikes
- ❌ All tasks treated equally (critical + analytics)
- ❌ No graceful degradation
- ❌ Cascading failures
- ❌ Recovery takes long time

---

## SOLUTION: BACKPRESSURE V2

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  BACKPRESSURE CONTROLLER V2                    │
│                                                                 │
│   Multi-Queue Monitoring:                                      │
│   ├── DAG Queue (50-200 tasks)                                  │
│   ├── Signal Queue (100-500 signals)                           │
│   ├── Analytics Queue (10-50 jobs)                             │
│   └── Execution Queue (20-100 orders)                          │
│                                                                 │
│   Load Levels:                                                 │
│   ├── NORMAL: All systems go                                   │
│   ├── WARNING: Throttle non-critical                           │
│   ├── CRITICAL: Shed load aggressively                         │
│   └── EMERGENCY: Survival mode                                 │
│                                                                 │
│   Actions by Level:                                            │
│   ├── WARNING: Throttle DAG to 80%, drop 50% LOW priority      │
│   ├── CRITICAL: Throttle to 50%, drop MED+LOW, block strategies│
│   └── EMERGENCY: Throttle to 20%, drop all non-CRITICAL        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Priority Levels

| Priority | Examples | Drop At | Description |
|----------|----------|---------|-------------|
| **CRITICAL** | Order execution, Position updates | Never | Core trading - never drop |
| **HIGH** | Price updates, Risk checks | Emergency | Important signals |
| **MEDIUM** | Indicator updates, DAG tasks | Critical | Standard processing |
| **LOW** | Analytics, Logging, Reports | Warning | Shed first |

### Thresholds

| Queue | Warning | Critical | Emergency |
|-------|---------|----------|-----------|
| **DAG** | 50 | 100 | 200 |
| **Signal** | 100 | 250 | 500 |
| **Analytics** | 10 | 25 | 50 |
| **Execution** | 20 | 50 | 100 |

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `core/backpressure_v2.py` | Advanced backpressure controller | 500+ |
| `SCALING_1000_STEP_3_SUMMARY.md` | This documentation | - |

---

## BACKPRESSURE V2 (`core/backpressure_v2.py`)

### Features

- **Multi-queue monitoring**: DAG, Signal, Analytics, Execution
- **Priority-based shedding**: CRITICAL → HIGH → MEDIUM → LOW
- **Graduated response**: Throttle → Drop → Block
- **Strategy admission control**: Block new strategies under load
- **Automatic recovery**: Resume normal ops when load drops
- **Decorators**: Easy integration with `@with_backpressure`

### Usage

#### Basic Backpressure Check

```python
from core.backpressure_v2 import backpressure_v2, Priority

# Check if task can be accepted
if backpressure_v2.can_accept(Priority.MEDIUM, "dag_task"):
    await process_task(task)
else:
    logger.info("Task dropped due to backpressure")
```

#### Throttled Execution

```python
# Apply automatic throttling delay
async with backpressure_v2.throttled_execution():
    await heavy_operation()
```

#### Decorator Pattern

```python
from core.backpressure_v2 import with_backpressure, Priority

@with_backpressure(Priority.MEDIUM, "signal")
async def process_price_update(price_data):
    # Automatically dropped if load is high
    await update_indicators(price_data)

@with_backpressure(Priority.LOW, "analytics")
async def generate_report():
    # First to be dropped under load
    await create_report()
```

#### Strategy Admission Control

```python
from core.backpressure_v2 import strategy_admission_control, StrategyBlockedError

@strategy_admission_control()
async def start_strategy(strategy_id: str, config: dict):
    # Automatically rejected if system overloaded
    await strategy_engine.start(strategy_id, config)

# Usage
try:
    await start_strategy("strategy_123", config)
except StrategyBlockedError as e:
    logger.warning(f"Strategy blocked: {e}")
    # Queue for later or notify user
```

### Integration Helpers

```python
from core.backpressure_v2 import (
    DAGTaskThrottler,
    SignalLoadShedder,
    StrategyAdmissionController
)

# DAG Task Throttling
dag_throttler = DAGTaskThrottler()
success = await dag_throttler.submit_task(task, Priority.MEDIUM)

# Signal Load Shedding
signal_shedder = SignalLoadShedder()
if signal_shedder.should_process("price_update", Priority.HIGH):
    await process_signal()

# Strategy Admission
strategy_admission = StrategyAdmissionController()
if strategy_admission.can_start("new_strategy"):
    await start_strategy()
```

---

## INTEGRATION

### With DAG Worker

```python
from backend.dag_worker import dag_worker
from core.backpressure_v2 import with_backpressure, Priority

class BackpressureDAGWorker:
    @with_backpressure(Priority.MEDIUM, "dag_task")
    async def process_task(self, task):
        # Automatically throttled/dropped based on load
        return await dag_worker.execute(task)
```

### With Signal Processing

```python
from core.backpressure_v2 import SignalLoadShedder, Priority

class SignalProcessor:
    def __init__(self):
        self.shedder = SignalLoadShedder()
    
    async def on_price_update(self, price_data):
        if not self.shedder.should_process("price", Priority.HIGH):
            return  # Dropped
        
        await self.process_price(price_data)
    
    async def on_indicator_update(self, indicator_data):
        if not self.shedder.should_process("indicator", Priority.MEDIUM):
            return  # Dropped
        
        await self.process_indicator(indicator_data)
```

### With Strategy Manager

```python
from core.backpressure_v2 import strategy_admission_control

class StrategyManager:
    @strategy_admission_control()
    async def activate_strategy(self, strategy_id: str):
        # Blocked automatically if system overloaded
        await self._activate(strategy_id)
```

---

## MONITORING

### Get Statistics

```python
stats = backpressure_v2.get_stats()
print(stats)
# {
#     "timestamp": "2024-01-15T10:30:00",
#     "load_level": "warning",
#     "queue_sizes": {
#         "dag": 75,
#         "signal": 120,
#         "analytics": 5,
#         "execution": 15
#     },
#     "throttle_rate": 0.8,
#     "strategy_blocking_enabled": true,
#     "blocked_strategies_count": 3,
#     "actions": {
#         "tasks_throttled": 45,
#         "signals_dropped": 12,
#         "strategies_blocked": 3
#     },
#     "dropped_by_priority": {
#         "CRITICAL": 0,
#         "HIGH": 0,
#         "MEDIUM": 5,
#         "LOW": 7
#     }
# }
```

### Register Callbacks

```python
# Load level change callback
def on_level_change(old_level, new_level):
    logger.warning(f"Load changed: {old_level} → {new_level}")
    # Send alert, scale up, etc.

backpressure_v2.register_level_change_callback(on_level_change)

# Load shedding callback
def on_shed(priority, reason):
    logger.info(f"Shed {priority} priority item: {reason}")
    # Track metrics

backpressure_v2.register_shed_callback(on_shed)
```

---

## EXPECTED RESULTS

### Before (Without Backpressure V2)
- ❌ System crashes under spikes
- ❌ All tasks dropped equally
- ❌ No graceful degradation
- ❌ Strategies start even when overloaded

### After (With Backpressure V2)
- ✅ Graceful degradation (throttle → drop → block)
- ✅ Critical execution preserved (orders never dropped)
- ✅ Analytics shed first (acceptable loss)
- ✅ Strategy admission control (no new load during crisis)
- ✅ Automatic recovery when load decreases

---

## SUMMARY

**Goal:** Prevent system overload for 1000+ users

**Step 3 Complete:** ✅
- Multi-queue backpressure monitoring
- Priority-based load shedding
- Graduated response (throttle/drop/block)
- Strategy admission control
- Decorator integration
- Automatic recovery

**Key Components:**
- `BackpressureControllerV2` - Main controller
- `Priority` - CRITICAL, HIGH, MEDIUM, LOW
- `LoadLevel` - NORMAL, WARNING, CRITICAL, EMERGENCY
- `@with_backpressure` - Decorator for functions
- `@strategy_admission_control()` - Decorator for strategies
- Integration helpers for DAG, signals, strategies

**Status:** Ready for 1000+ users with graceful degradation
