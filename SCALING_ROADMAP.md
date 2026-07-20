# SCALING_ROADMAP

This roadmap defines the architectural evolution required to support user milestones safely.

## Phase 1: Current State (0 to 100 Users)
**Private Beta**
- Monolithic FastAPI backend on a single container/instance.
- In-memory rate limiting.
- Synchronous backtests.
- Direct Postgres connections.
- *Status*: Deployed.

## Phase 2: Structural Offloading (100 to 500 Users)
**Public Beta Preparation**
*Architectural changes must occur BEFORE hitting 500 users.*
1. **Implement Redis**: 
   - Move `slowapi` rate limiter state to Redis.
   - Add `@cache` decorators to `/api/library` endpoints (TTL: 5 minutes).
2. **Implement Celery/Background Workers**:
   - Refactor `/api/strategies/backtest` to return a `job_id`.
   - Frontend polls `/api/strategies/backtest/{job_id}` for completion.
3. **Connection Pooling**:
   - Update `SUPABASE_URL` to use the PgBouncer pooler port (6543) with `pool_mode=transaction`.

## Phase 3: Cluster Distribution (500 to 1,000 Users)
**Growth Phase**
*Architectural changes must occur BEFORE hitting 1,000 users.*
1. **Engine Separation**:
   - Extract the `UnifiedExecutionEngine` (Paper/Live Trading) into a standalone Python microservice.
   - Isolate Trading Engine compute resources from API CRUD operations to guarantee order execution latency.
2. **WebSockets for Market Data**:
   - Refactor the Engine to consume CCXT websockets rather than polling REST endpoints to handle hundreds of concurrent symbols without rate-limit bans from Binance/Kraken.

## Phase 4: High Availability (1,000 to 5,000+ Users)
**Scale Phase**
1. **Database Read Replicas**:
   - Route all `GET` API traffic (Dashboard, Library) to Postgres Read Replicas.
   - Route `POST/PUT/DELETE` to the Primary Postgres node.
2. **Distributed Backtesting**:
   - Deploy backtest workers on AWS EC2 Spot Instances / ECS Spot Fleet to dynamically scale compute capacity for heavy ML/DAG optimization tasks while keeping costs low.
3. **CDN Optimization**:
   - Move all static assets, UI images, and potentially cached JSON library responses to CloudFront Edge locations.
