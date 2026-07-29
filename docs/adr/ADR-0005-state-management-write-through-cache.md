# ADR-0005: State Management Write-Through Cache

## Status
Accepted

## Context
Trading systems demand microsecond state retrieval (e.g., current position sizing, open order statuses) while guaranteeing data durability in the event of a crash. The previous state management was fragmented, with some modules reading directly from PostgreSQL (slow) and others reading from in-memory dicts (ephemeral and dangerous).

## Decision
We centralized all order and position state management into `state_service.py` using a strict **Write-Through Cache** pattern with Redis and PostgreSQL.
1. **Reads**: Always hit the Redis Cache (DB 0). On miss, query Postgres and hydrate Redis.
2. **Writes**: Synchronously update Redis (for immediate speed) and asynchronously flush to Postgres (for durability).
3. **Idempotency**: All writes require a unique idempotency key validated against Redis (DB 3) to prevent duplicate execution during network retries.

## Consequences
- **Positive**: Guarantees ultra-low latency for order processing checks without sacrificing the ACID properties of PostgreSQL.
- **Positive**: Strictly eliminates duplicate execution of trading commands.
- **Negative**: High complexity during Disaster Recovery scenarios. If Postgres is restored via PITR (Point-in-Time Recovery), the Redis caches must be explicitly flushed to prevent the system from reading orphaned "future" state.
