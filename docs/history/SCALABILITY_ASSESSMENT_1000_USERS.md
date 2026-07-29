# SCALABILITY_ASSESSMENT_1000_USERS

## Executive Summary
The Aerora Quant Platform architecture, as currently implemented, is capable of supporting the initial Private Beta of ~100 users, but it is **not structurally capable** of supporting 1,000 concurrent active users.

The primary bottleneck is the synchronous, CPU-bound execution of backtests (via Pandas/VectorBT) and the Paper Trading Scheduler running within the main FastAPI asynchronous event loop, combined with direct PostgreSQL database connections without an intermediate connection pooler (e.g., PgBouncer) or caching layer (Redis).

## 1. Backend Capacity
- **Current State**: A monolithic FastAPI application handling HTTP CRUD, Heavy compute (Backtesting), and long-running stateful loops (Sandbox scheduler).
- **1k User Estimate**: 
  - Requests Per Second (RPS): ~50-100 RPS.
  - Connection Limits: Supabase standard tier will exhaust max connections (typically 60-100 direct connections) if 1,000 users query the marketplace or dashboard simultaneously.
- **Verdict**: Will fail under heavy load.

## 2. Database
- **RLS Overhead**: Row-Level Security adds slight compute overhead per query. At 1,000 users, this is negligible compared to connection limit exhaustion.
- **N+1 Queries**: The marketplace `GET /api/library` does not suffer from N+1 as it utilizes a flattened schema, but retrieving user clones/ratings dynamically per request will cause DB churn without a cache.

## 3. Marketplace
- **Browse Throughput**: Currently high, but will degrade linearly with the number of users due to lack of caching. 1000 users refreshing the marketplace will strain the Supabase free/pro tiers.
- **Clone / Publish Throughput**: Clone operations use RPCs or transaction blocks. They are safe but un-optimized for mass concurrency.

## 4. Backtesting Engine
- **Current State**: Backtests are executed in-process. 
- **1k User Limit**: If 50 users hit `/backtest` concurrently, the FastAPI worker thread pool will exhaust, CPU utilization will hit 100%, and the remaining 950 users will experience HTTP 504 timeouts.
- **Requirement**: Backtesting MUST be moved to a background worker queue (e.g., Celery/Redis).

## 5. Paper Trading
- **Current State**: The `UnifiedExecutionEngine` polls prices and evaluates DAGs.
- **1k User Limit**: 1,000 concurrent paper trading bots evaluating indicators every 1-minute will freeze the Asyncio event loop, causing massive execution latency/slippage. 
- **Requirement**: Requires a distributed cluster of worker nodes.

## 6. Redis Assessment
- **Is Redis Required?** **YES.**
- **Where**: 
  1. **Celery Queue**: For offloading `/api/strategies/backtest`.
  2. **Response Caching**: For `/api/library` (Marketplace) to serve 1,000 users instantly.
  3. **Rate Limiting**: `slowapi` currently uses in-memory storage (which breaks across multiple load-balanced Docker containers).

## 7. Security
- **Rate Limiting**: Active, but as noted, relies on local memory. If we scale to 5 Docker containers, a user gets 5x the rate limit.
- **DDoS**: Vulnerable to compute-exhaustion attacks (e.g., submitting complex, un-cached backtests repeatedly).

## Final Assessment
The codebase is solid and well-written, but the deployment topology must evolve to a distributed service model before scaling to 1,000 users.
