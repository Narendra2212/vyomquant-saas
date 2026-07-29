# Redis Cluster Migration Plan

This document details the exact methodology, required code changes, and sequence to migrate the current single-shard Redis setup to a multi-sharded Redis Cluster. It serves as a ready-to-execute guide when scale demands it.

## 1. Scale Triggers for Migration

The decision to execute this migration should not be arbitrary. Wait until the following thresholds (informed by task P5-6's pool sizing validation) are hit during normal operation (excluding brief, acceptable spikes):

- **Memory Utilization**: `> 75%` sustained over 15 minutes.
- **Engine CPU Utilization**: `> 70%` sustained over 15 minutes.
- **Network Bandwidth**: Approaching `80%` of the instance type's baseline network performance.
- **Throughput Exhaustion**: Reaching ~50,000-80,000 operations per second, where single-threaded event loop latency begins causing application-side timeouts.

## 2. Infrastructure Changes (Terraform)

Currently, `terraform/redis.tf` defines an ElastiCache Replication Group with Cluster Mode Enabled but only a single node group (`num_node_groups = 1`):
```terraform
  multi_az_enabled = true
  num_node_groups  = 1  # Action: Increase to 3+
  replicas_per_node_group = 2
```
To execute the migration, `num_node_groups` must be scaled up to distribute the hash slots across multiple shards. **AWS ElastiCache supports online scaling**—adding node groups to an existing cluster will dynamically rebalance slots with near-zero downtime.

## 3. Required Client Library & Code Changes

Before scaling `num_node_groups` in Terraform, the application backend **must** be updated to use a Cluster-aware client. A standard `redis.Redis` client will fail in a multi-shard environment due to `MOVED` redirection errors.

### 3.1 Client Initialization
In `backend_app/backend/redis_manager.py`:
- **Swap Client**: Replace `redis.asyncio.Redis` with `redis.asyncio.cluster.RedisCluster` (as modeled in `core/redis_cluster.py`).
- **Node Discovery**: The cluster client automatically discovers topology via the configuration endpoint.

### 3.2 Handling Hash Slots & Multi-Key Operations (Caching & Rate Limiting)
Redis Cluster shards data using a CRC16 hash of the key. Commands operating on multiple keys (e.g., `MGET`, Lua scripts for rate limiting) **will fail with a `CROSSSLOT` error** if the keys do not map to the same shard.
- **Solution**: Use **Hash Tags**. Enclose the common identifier in `{}`.
- **Rate Limiting Example**: Change key `rate_limit:user123:api` to `rate_limit:{user123}:api`.
- **Cache Pipelines**: If you run a pipeline spanning disparate keys, either use hash tags or replace the pipeline with concurrent `asyncio.gather` single-key calls.

### 3.3 Event Bus & Streams (Trading Command Semantics)
The command bus uses Redis Streams (`event_bus.py`). This is the most critical use case due to consumer-group semantics.
- **Consumer Groups on Clusters**: A Consumer Group is tied exclusively to the shard hosting its Stream key.
- **XREADGROUP Operations**: If a worker calls `XREADGROUP` to consume from multiple streams simultaneously, **all streams must reside on the same shard**.
- **Action Required**: Since `event_bus.py` lists discrete streams (`command_queue`, `market_data`, etc.), ensure any multi-stream readers use a hash tag like `{events}:command_queue` to force them onto the same shard, OR refactor workers to strictly read one stream at a time per blocking call. (Currently, workers typically read a single stream, so `XREADGROUP` will operate safely per-shard).

### 3.4 WebSocket Fan-out (Pub/Sub)
- Redis 7.0+ supports **Sharded Pub/Sub** (`SSUBSCRIBE` / `SPUBLISH`).
- Standard `PUBLISH` broadcasts to the entire cluster, generating immense internal network traffic.
- **Action Required**: The WebSocket bridging logic in `ws_server.py` should be migrated to `SPUBLISH` and `SSUBSCRIBE`, hashing the channel name to a specific shard to conserve cluster bandwidth.

## 4. Migration Sequence

1. **Deploy Code Changes**: Update the backend to use `redis.asyncio.cluster.RedisCluster` and deploy hash tags for multi-key scripts. Since ElastiCache is already configured with `cluster.on` (even with 1 shard), the cluster client is fully backward-compatible with the current setup.
2. **Monitor**: Allow the cluster-aware code to bake in production on the single-shard setup for at least 48 hours. Ensure no `CROSSSLOT` exceptions occur in logs.
3. **Execute Scale**: Update `terraform/redis.tf` to `num_node_groups = 3`.
4. **Apply**: Run `terraform apply`. ElastiCache will spin up the new shards and perform an online resharding (slot migration). 
5. **Verify**: Ensure cluster health shows `cluster_state:ok` and memory utilization is evenly distributed across nodes.

## 5. Rollback Plan

If unexpected application errors occur during or after slot migration:
1. **Revert Terraform**: Change `num_node_groups` back to 1. ElastiCache supports online scale-in. It will migrate all slots back to the primary node group before destroying the extras.
2. **Revert Code**: Once Terraform scale-in completes, roll back the backend application deployment if client library changes caused the instability.

## 6. Cost Comparison

| Configuration | Hourly Rate (Approx) | Monthly Cost | Data Processing | Resilience |
|---|---|---|---|---|
| **Current** (1 Shard, 2 Replicas) | `3 * node_cost` | Baseline (e.g. $150) | Negligible | Node-level HA |
| **Target** (3 Shards, 2 Replicas each) | `9 * node_cost` | 3x Baseline (e.g. $450) | Negligible | Node + Shard HA |

*Note: The cost triples. This highlights why this migration should strictly be gated by the utilization triggers identified in Section 1.*
