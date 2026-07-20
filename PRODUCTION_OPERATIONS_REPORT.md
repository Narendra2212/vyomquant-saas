# PRODUCTION_OPERATIONS_REPORT

## Deployment & Scaling Topology

### 1. Web API (FastAPI)
- **Scaling**: AWS ECS Service with Auto Scaling (Target 60% CPU).
- **Topology**: 3 to 10 instances.

### 2. Backtest Workers (`rq`)
- **Scaling**: SQS/Redis queue-depth autoscaling.
- **Topology**: 2 to 20 spot instances (compute-optimized).

### 3. TEE Cluster (Execution Engine)
- **Scaling**: Static baseline or step-scaling based on total deployed strategies. 
- **Topology**: 5 active instances (`c6g.large`).
- **Resilience**: Zero-downtime rolling deployments are supported via `SIGTERM` handlers releasing Redis locks instantly.

### 4. Market Data Service (MDS)
- **Scaling**: Single highly-available instance (Active-Passive in future iteration). 
- **Topology**: 1 instance (`t4g.medium`). 
- **Resilience**: Restarts automatically. TEE instances wait for WebSocket streams to resume, defaulting to internal indicators during the gap.

## Health Monitoring
- All sub-systems (`API`, `Worker`, `TEE`, `MDS`) expose `/health` endpoints tracking internal state metrics (e.g., active locks, stream counts, queue depths).
- **Alerts Configured**:
  - TEE Heartbeat Failure
  - MDS WebSocket Disconnects > 5/min
  - HTTP 500s > 1%
  - Redis Memory Utilization > 80%
