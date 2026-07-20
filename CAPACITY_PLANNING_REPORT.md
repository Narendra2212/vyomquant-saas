# CAPACITY_PLANNING_REPORT

## 1. Compute Requirements (CPU/Memory)
Data science libraries (`pandas`, `numpy`, `vectorbt`) are extremely memory-intensive.
- **Backtest**: A 1-year 1m-timeframe backtest on BTC/USDT can consume ~200MB RAM during calculation.
- **1,000 User Impact**: 100 concurrent backtests = 20GB of RAM required.
- **Solution**: Strict bounds on backtest timeframes (e.g., max 6 months for free users) and offloading to horizontally scaled worker nodes.

## 2. Connection Pool Capacity
- **Supabase Limit**: 500 direct connections maximum.
- **1,000 User Impact**: With 5 API containers running 10 FastAPI workers each, you hit 50 connections base. During traffic spikes, connections will queue, leading to `TimeoutError`.
- **Solution**: Supabase Connection Pooling (PgBouncer) must be activated, resolving thousands of logical connections down to a few dozen physical ones.

## 3. Storage & Bandwidth
- **Marketplace Images**: Minimal overhead (S3/Supabase Storage handles this seamlessly).
- **Marketplace Browsing**: 1,000 users browsing yields heavy JSON payload traffic.
- **Solution**: Cloudflare or AWS CloudFront edge-caching for the public `/api/library` catalogue.

## 4. Execution Engine Capacity
- **Paper Trading**: At 1,000 users, assume 500 active paper trading bots.
- **Tick Rate**: If checking 500 bots every 10 seconds, that's 50 DB calls + 500 CCXT REST calls per second.
- **Solution**: Must move from REST to WebSocket ingestion for market data, and evaluate DAGs in memory rather than fetching state per tick.
