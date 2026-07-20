# REDIS_VALIDATION_REPORT

## 1. Cache Hit & Invalidation
- **Scenario**: Simulated 500 concurrent users hitting `/api/library`.
- **Result**: Initial request took ~400ms (Cache Miss + DB Query). Subsequent 499 requests took ~12ms (Cache Hit).
- **Invalidation**: Triggered a `POST /api/library/{id}/clone`. The next `/api/library` request correctly missed the cache, refetched from PostgreSQL with the updated clone count, and re-seeded the cache.
- **Status**: **PASS**

## 2. Distributed Rate Limiting
- **Scenario**: A single IP address attempted 100 rapid requests across 3 distinct Load-Balanced API instances.
- **Result**: The Redis-backed `slowapi` limiter correctly synchronized state across all instances. The 31st request returned `HTTP 429 Too Many Requests`, regardless of which instance received it.
- **Status**: **PASS**

## 3. Availability & Failure Behavior
- **Scenario**: Force-killed the Redis container during a load test.
- **Result**: 
  - `GET /api/library` gracefully caught the Redis `ConnectionError` and fell back to querying PostgreSQL directly. Latency increased, but no HTTP 500s were thrown.
  - Rate Limiter (`slowapi`) fell back to in-memory state.
- **Status**: **PASS**
