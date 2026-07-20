# FINAL_RISK_REGISTER

## Mitigated Risks (Closed)
1. **Event Loop Starvation**: Addressed via `rq` background workers.
2. **PostgreSQL Connection Exhaustion**: Addressed via PgBouncer `6543` transaction pooling.
3. **Database Compute Overload**: Addressed via Redis Marketplace Caching.
4. **CCXT Rate Limiting**: Addressed via centralized Market Data Service (WebSockets).
5. **Duplicate Trade Execution**: Addressed via TEE Redis Leader Election and Heartbeats.

## Accepted Risks (Active Monitoring)

### 1. Single Point of Failure: Redis
- **Risk**: The entire distributed architecture (Cache, Rate Limiting, PubSub, Queues, Locks) depends on Redis. 
- **Mitigation**: ElastiCache Multi-AZ deployment is strictly required. A total Redis outage will pause trading across the TEE cluster until recovered.

### 2. Single Point of Failure: MDS
- **Risk**: Currently, the MDS is a single instance. If it restarts, market data drops for 5-10 seconds.
- **Mitigation**: ECS Auto Recovery handles this. Given 1-minute minimum candle timeframes, a 10s gap during a restart is statistically unlikely to corrupt a signal.

### 3. API Key Encryption At Rest
- **Risk**: Exchange API keys in Supabase.
- **Mitigation**: Relying on Supabase vault/RLS. Full symmetric KMS encryption at the application layer is recommended for Phase 6.
