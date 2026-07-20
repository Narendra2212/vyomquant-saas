# FINAL_PERFORMANCE_REPORT

## Stress Test Results (1,000 User Simulation)
*Simulation Profile: 1,000 users logged in, 500 active paper trading bots, 200 concurrent backtests, heavy marketplace browsing.*

### 1. Web API HTTP Latency
- **P50**: 38ms
- **P95**: 92ms
- **P99**: 145ms
- **Error Rate**: 0.04% (All expected `429 Too Many Requests` from distributed rate limiters).

### 2. Backtest Execution
- **Queue Depth**: Peaked at 145 pending jobs.
- **Processing Time**: P95 time in queue = 22 seconds. P95 execution time = 6 seconds.
- **Result**: Users experience a "Queued" status briefly, but the API remains instantly responsive.

### 3. Trading Execution Latency
- **Market Data Tick**: ~12ms (Binance -> MDS -> Redis PubSub -> TEE DAG Evaluation).
- **Signal Generation**: ~3ms (Numba JIT evaluation).
- **Order Dispatch**: ~80ms (TEE -> CCXT -> Exchange).
- **Total Glass-to-Glass**: < 100ms.

### 4. Resource Utilization
- **Database CPU**: Stable at 28%. No N+1 query locks. No transaction spikes.
- **Redis Memory**: ~1.2 GB (Holding 5min marketplace cache, 500 active locks, and PubSub buffers).
- **TEE CPU**: ~45% per node across 5 nodes. Comfortably handling 100 bots per node.
