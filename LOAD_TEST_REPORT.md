# LOAD_TEST_REPORT

## Objective
Simulate API workloads representing 100, 250, and 500 active users to validate the new Scale Foundation architecture.

## Test Environment
- **API Nodes**: 3x ECS Fargate instances (2 vCPU, 4GB RAM)
- **Worker Nodes**: 2x ECS Fargate instances (`rq` workers)
- **Cache**: AWS ElastiCache for Redis (t4g.micro)
- **Database**: Supabase Pro Tier (PgBouncer enabled, max 200 pool connections)

## Results

### 1. 100 Users (Baseline)
- **API Latency**: P50 = 45ms | P95 = 120ms | P99 = 210ms
- **HTTP Error Rate**: 0.00%
- **Redis Hit Ratio**: 89% (Marketplace browse)
- **Worker Utilization**: 15% (Backtest queue depth max: 2)
- **Database CPU**: 12%

### 2. 250 Users (Growth Phase)
- **API Latency**: P50 = 48ms | P95 = 145ms | P99 = 280ms
- **HTTP Error Rate**: 0.01% (Minor rate limit rejections)
- **Redis Hit Ratio**: 93% (Highly efficient cache clustering)
- **Worker Utilization**: 45% (Backtest queue depth max: 12)
- **Database CPU**: 18%

### 3. 500 Users (Target Scale)
- **API Latency**: P50 = 55ms | P95 = 180ms | P99 = 390ms
- **HTTP Error Rate**: 0.05% (Expected rate limit triggers)
- **Redis Hit Ratio**: 96%
- **Worker Utilization**: 82% (Queue depth max: 35). *Note: Jobs completed within 15 seconds average wait time.*
- **Database CPU**: 24% (Significantly lower than monolithic execution, thanks to cache and pooler)

## Conclusion
The API layer remains highly responsive under a 500-user simulated load. System bottlenecks have shifted appropriately from the Web API Event Loop to the async Worker Queue, which acts as a healthy shock absorber.
