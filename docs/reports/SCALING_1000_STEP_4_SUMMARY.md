# 🔥 STEP 4 — EXECUTION RECONCILIATION LOOP

## Goal: Sync with Exchange Every Few Seconds

**Focus:**
- Local state always matches exchange
- No hidden drift
- Automatic self-healing

---

## PROBLEM

Without reconciliation:
- ❌ Local state drifts from exchange reality
- ❌ Filled orders still show as open
- ❌ Position sizes don't match
- ❌ Hidden errors accumulate
- ❌ User sees stale data

---

## SOLUTION: RECONCILIATION WORKER

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                  RECONCILIATION WORKER                           │
│                    (Runs every 5 seconds)                        │
│                                                                 │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│   │  Fetch      │    │  Compare    │    │  Correct    │          │
│   │  Exchange   │───▶│  Local vs   │───▶│  Local      │          │
│   │  State      │    │  Exchange   │    │  State      │          │
│   └─────────────┘    └─────────────┘    └─────────────┘          │
│                                                                 │
│   Reconciliation Scenarios:                                    │
│   1. Order Status Mismatch: Exchange filled, Local pending      │
│      → Update local order to filled                              │
│                                                                 │
│   2. Position Size Mismatch: Exchange 1.5 BTC, Local 1.0 BTC   │
│      → Update local position, recalculate PnL                  │
│                                                                 │
│   3. Missing Order: Exchange has order, Local doesn't           │
│      → Sync order from exchange to local                         │
│                                                                 │
│   4. Ghost Order: Exchange cancelled, Local still open        │
│      → Mark local order as cancelled                           │
│                                                                 │
│   5. Balance Drift: Exchange 10.5 BTC, Local 10.0 BTC        │
│      → Update balance, investigate cause                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Mismatch Types

| Type | Description | Severity | Auto-Correct |
|------|-------------|----------|--------------|
| **ORDER_STATUS** | Order status differs | Critical | Yes |
| **ORDER_MISSING_LOCAL** | Order on exchange but not locally | Warning | Yes |
| **ORDER_MISSING_EXCHANGE** | Order locally but not on exchange | Critical | Yes (mark cancelled) |
| **POSITION_SIZE** | Position quantity differs | Critical | Yes |
| **POSITION_MISSING** | Position on one side only | Critical | Yes |

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `backend/reconciliation_worker.py` | Reconciliation worker | 600+ |
| `SCALING_1000_STEP_4_SUMMARY.md` | This documentation | - |

---

## RECONCILIATION WORKER (`backend/reconciliation_worker.py`)

### Features

- **Periodic reconciliation**: Runs every 5 seconds (configurable)
- **Multi-exchange support**: Binance, Coinbase, Kraken, etc.
- **Automatic correction**: Fixes mismatches automatically
- **Mismatch detection**: Orders, positions, balances
- **Alerting**: Alerts on significant drift (>5 mismatches)
- **Metrics**: Tracks reconciliation runs, mismatches, corrections

### Usage

#### Basic Setup

```python
from backend.reconciliation_worker import reconciliation_worker

# Register exchange client
reconciliation_worker.register_exchange_client(
    user_id="user_123",
    exchange="binance",
    client=ccxt_client
)

# Start reconciliation loop
await reconciliation_worker.start()

# Runs every 5 seconds automatically
# - Fetches exchange state
# - Compares with local StateService
# - Corrects mismatches
# - Emits events for monitoring
```

#### Manual Reconciliation

```python
# Run one-time manual reconciliation
result = await reconciliation_worker.run_manual_reconciliation(
    user_id="user_123",
    exchange="binance"
)

print(result.to_dict())
# {
#     "user_id": "user_123",
#     "exchange": "binance",
#     "timestamp": "2024-01-15T10:30:00",
#     "orders_checked": 15,
#     "positions_checked": 3,
#     "mismatches_found": 2,
#     "mismatches_corrected": 2,
#     "mismatches": [...],
#     "duration_seconds": 0.45
# }
```

#### Register Callbacks

```python
# Mismatch detection callback
def on_mismatch(mismatch):
    logger.warning(f"Mismatch detected: {mismatch.description}")
    # Send alert, log to monitoring, etc.

reconciliation_worker.register_mismatch_callback(on_mismatch)

# Reconciliation completion callback
def on_reconciliation(result):
    if result.mismatches_found > 0:
        logger.info(f"Reconciliation found {result.mismatches_found} mismatches")
    # Update metrics dashboard

reconciliation_worker.register_reconciliation_callback(on_reconciliation)
```

#### Configuration

```python
from backend.reconciliation_worker import ReconciliationWorker

# Custom configuration
worker = ReconciliationWorker(
    interval_seconds=5.0,        # Reconcile every 5s
    auto_correct=True,          # Automatically fix mismatches
    alert_threshold=5,          # Alert if >5 mismatches
    exchanges=["binance", "coinbase"]  # Supported exchanges
)

await worker.start()
```

---

## INTEGRATION

### With State Service (Step 1)

```python
# StateService is used as source of truth for local state
from backend.state_service import state_service
from backend.reconciliation_worker import reconciliation_worker

# ReconciliationWorker reads/writes to StateService
# - Fetches local orders via state_service.get_user_orders()
# - Fetches local positions via state_service.get_position()
# - Corrects state via state_service.save_order() / update_position()
```

### With CCXT

```python
import ccxt.async_support as ccxt
from backend.reconciliation_worker import reconciliation_worker

# Create CCXT client
exchange = ccxt.binance({
    "apiKey": "...",
    "secret": "...",
})

# Register with reconciliation worker
reconciliation_worker.register_exchange_client(
    user_id="user_123",
    exchange="binance",
    client=exchange
)

await reconciliation_worker.start()
```

### With Event Pipeline (Step 2)

```python
from backend.reconciliation_worker import reconciliation_worker
from core.event_pipeline import get_event_pipeline, EventType

# Emit reconciliation events
async def on_reconciliation(result):
    if result.mismatches_found > 0:
        pipeline = await get_event_pipeline()
        await pipeline.publish(
            tenant_id=result.user_id,
            event_type=EventType.DAG_EXECUTION_COMPLETED,  # Or custom type
            payload={
                "reconciliation_result": result.to_dict(),
                "mismatches": [m.to_dict() for m in result.mismatches]
            },
            source="reconciliation_worker"
        )

reconciliation_worker.register_reconciliation_callback(on_reconciliation)
```

---

## MONITORING

### Metrics

```python
metrics = reconciliation_worker.get_metrics()
print(metrics)
# {
#     "reconciliation_runs_total": 1500,
#     "reconciliations_successful": 1485,
#     "mismatches_detected_total": 23,
#     "mismatches_corrected_total": 23,
#     "last_reconciliation_timestamp": "2024-01-15T10:30:00",
#     "last_reconciliation_duration_ms": 450
# }
```

### Mismatch Details

```python
mismatch = Mismatch(
    mismatch_type=MismatchType.ORDER_STATUS,
    user_id="user_123",
    exchange="binance",
    symbol="BTC-USD",
    local_state={"status": "pending", ...},
    exchange_state={"status": "filled", ...},
    description="Order ord_123 status mismatch: local=pending, exchange=filled",
    severity="critical"
)

print(mismatch.to_dict())
```

### Alerting

```python
# Alert on significant drift
def on_reconciliation(result):
    if result.mismatches_found >= 5:
        send_alert(
            severity="critical",
            message=f"Significant drift detected for {result.user_id}@{result.exchange}",
            details=result.to_dict()
        )

reconciliation_worker.register_reconciliation_callback(on_reconciliation)
```

---

## EXPECTED RESULTS

### Before (Without Reconciliation)
- ❌ Local state drifts from exchange
- ❌ Orders show wrong status
- ❌ Position sizes incorrect
- ❌ PnL calculations wrong
- ❌ Users see stale data

### After (With Reconciliation)
- ✅ Local always matches exchange (within 5 seconds)
- ✅ Orders correctly reflect exchange state
- ✅ Position sizes accurate
- ✅ PnL calculations correct
- ✅ Automatic self-healing
- ✅ No hidden drift

---

## SUMMARY

**Goal:** Sync with exchange every 5 seconds for 1000+ users

**Step 4 Complete:** ✅
- Reconciliation worker runs every 5 seconds
- Fetches exchange state (orders, positions)
- Compares with local StateService
- Automatically corrects mismatches
- Alerts on significant drift
- Multi-exchange support

**Key Components:**
- `ReconciliationWorker` - Main reconciliation loop
- `Mismatch` - Represents detected discrepancies
- `ReconciliationResult` - Summary of reconciliation run
- Auto-correction for order status, position size
- Callbacks for monitoring and alerting

**Status:** Ready for 1000+ users with automatic drift correction
