# PAPER_TRADING_DEPLOY_ROOT_CAUSE.md

**Investigation Role**: Principal Trading Systems Engineer  
**Failure**: `POST /api/strategies/{id}/deploy` → `502 Bad Gateway`  
**Status**: ROOT CAUSE CONFIRMED — 2 failure vectors identified  
**Date**: 2026-06-21  

---

## Executive Summary

The 502 Bad Gateway is caused by Gunicorn killing the uvicorn worker process with **SIGKILL** after its 30-second timeout fires. The root cause is that `FleetManager.start_bot()` is called **directly inside the HTTP request handler** and blocks awaiting `BotRunner._recover_live_state()`, which performs slow multi-step CCXT network I/O (market loading, balance fetch, 1000-bar OHLCV fetch). These calls collectively exceed the Gunicorn worker timeout.

A secondary failure vector causes an **immediate crash** in certain environments: bare module imports `from connection_engine import` silently resolve to `ModuleNotFoundError` in any process that did not execute `main.py`'s `sys.path` mutation.

---

## Traced Execution Path

### Layer 1 — HTTP Router: strategies.py:884

```
POST /api/strategies/{id}/deploy
  deploy_bot()
    await publish_command("start_bot", {...})   <- Redis write OK (fast)
    await fleet.start_bot(user_id, symbol, blueprint)  <- BLOCKS HERE
```

`fleet.start_bot()` is awaited inline inside the HTTP handler. The HTTP worker thread is held hostage until bot initialization completes.

---

### Layer 2 — FleetManager: fleet_manager.py:83

```
FleetManager.start_bot()
  async with self._lock:
    await bot._recover_live_state()   <- BLOCKING, NO TIMEOUT
```

`_recover_live_state()` is called with no timeout guard and no `asyncio.create_task()`.

---

### Layer 3 — BotRunner._recover_live_state(): master_executor.py:157

**FAILURE VECTOR A — Bare imports, depends on sys.path mutation from main.py:**

```python
from connection_engine import ConnectionEngine  # line 157 - works only if backend/ in sys.path
from data_seeking_engine import DataEngine      # line 158 - same problem
from backend_app.core.execution_engine import ExecutionEngine  # line 159
#  execution_engine -> models/__init__ -> billing.py -> database.py
#  -> database_pool.py:71
#  -> RuntimeError: DATABASE_URL must be provided in paper/live mode
```

**FAILURE VECTOR B — CCXT network I/O blocks the HTTP worker:**

```python
self.exchange = await self.ccxt_bridge.connect()   # line 168 - CCXT load_markets
# ConnectionEngine.connect() retries 3x with 30s timeout each = up to 90 seconds

wallet = await self.data_engine.fetch_wallet_balance_snapshot()  # line 177

ohlcv = await self.data_engine.fetch_historical_ohlcv(limit=1_000)  # line 220
# Paginated: 2 pages x 3 retries x 30s timeout = up to 180 seconds
```

---

### Layer 4 — ConnectionEngine: connection_engine.py:116

```python
config = {"timeout": 30_000, ...}   # 30s per CCXT call
# connect() retries 3 times = up to 90 seconds
```

---

### Layer 5 — Gunicorn Worker: startup.sh:65

```bash
exec gunicorn main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000}
    # NO --timeout FLAG
    # DEFAULT TIMEOUT = 30 SECONDS
```

When the worker takes >30 seconds, Gunicorn sends SIGKILL. Railway nginx returns 502.

---

## Primary Root Cause: Synchronous Bot Boot in HTTP Handler

| Field | Value |
|---|---|
| Subsystem | `FleetManager.start_bot()` / `BotRunner._recover_live_state()` |
| File | `backend_app/backend/fleet_manager.py:83` |
| Exception | None — Gunicorn SIGKILL (no exception propagated) |
| HTTP Result | 502 Bad Gateway |

Sequential CCXT calls in `_recover_live_state()` take 30–270 seconds. At the 30-second mark, Gunicorn SIGKILLs the worker. The exception never reaches the handler.

---

## Secondary Root Cause: Bare Module Imports

| Field | Value |
|---|---|
| Subsystem | `BotRunner._recover_live_state()` |
| File | `backend_app/backend/master_executor.py:157-158` |
| Exception | `ModuleNotFoundError: No module named 'connection_engine'` |
| Trigger | command_worker.py or any process without main.py sys.path setup |

`main.py:39` adds `backend/` to `sys.path`. The `command_worker.py` does NOT. Bare imports fail in the worker process.

---

## Why Paper Trading Gets the Same 502

`_recover_live_state()` does not short-circuit for `paper_trading: True`. It performs the full CCXT connection, real balance fetch, and real OHLCV download regardless. For paper trading, none of these are needed.

---

## Double-Start Bug (Non-Critical)

The deploy endpoint calls:
1. `await publish_command("start_bot", ...)` — pushes to Redis (command worker handles)
2. `await fleet.start_bot(...)` — starts bot directly in-process

Every deploy triggers two `start_bot` calls. The second (from the Redis worker) hits the already-running guard and is silently dropped. This is redundant and should be cleaned up.

---

## Patch Plan

1. **Fix 1 (Critical)**: Move `_recover_live_state()` into `asyncio.create_task()`, return HTTP 202 immediately
2. **Fix 2 (Critical)**: Skip CCXT calls when `paper_trading=True` (use mock exchange)  
3. **Fix 3 (Important)**: Replace bare imports with absolute package paths
4. **Fix 4 (Important)**: Add `--timeout 120` to gunicorn in startup.sh
5. **Fix 5 (Cleanup)**: Remove duplicate `publish_command("start_bot")` from deploy_bot()
