# PERFORMANCE_BOTTLENECK_REPORT

The following structural bottlenecks will cause catastrophic failure at 1,000 users:

## Bottleneck 1: Synchronous Backtesting in FastAPI
- **Issue**: `/api/strategies/backtest` runs heavy math operations on the main event loop thread (or standard starlette threadpool).
- **Trigger**: ~25-50 concurrent users running a backtest.
- **Symptom**: All other API endpoints (including simple logins and dashboard loads) will hang and return 504 Gateway Timeout.

## Bottleneck 2: Rate Limiter State Isolation
- **Issue**: `slowapi` uses `memory://` state.
- **Trigger**: Scaling the backend to >1 Docker container.
- **Symptom**: A malicious user can bypass the 5/min limit by hitting different load-balanced containers.

## Bottleneck 3: Sandbox Scheduler Concurrency
- **Issue**: `UnifiedExecutionEngine` polls sequentially or via basic `asyncio.gather` for all deployed strategies.
- **Trigger**: > 200 deployed Sandbox strategies.
- **Symptom**: Slippage. The engine will fail to evaluate indicators in real-time, executing trades seconds or minutes late, rendering the paper trading results inaccurate.

## Bottleneck 4: Marketplace Database Load
- **Issue**: `GET /api/library` computes joins or filters dynamically without a cache.
- **Trigger**: 500+ users sorting and filtering the marketplace on launch day.
- **Symptom**: High CPU on the Supabase Postgres instance, leading to slow queries across the entire platform (including order routing).
