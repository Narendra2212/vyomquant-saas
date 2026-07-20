# TEE_CLUSTER_IMPLEMENTATION

## Architecture
The Trading Execution Engine (TEE) has been refactored from a standalone worker into a horizontally scalable Cluster. Multiple instances of `backend_app/tee/main.py` can now run concurrently without risking duplicate trades for the same strategy.

## Key Mechanisms Added

### 1. Leader Election (Redis Locks)
Instead of relying solely on the Consumer Group distribution algorithm, the TEE now actively acquires a distributed Redis lock (`tee:lock:{strategy_id}`) prior to initiating a local execution loop. If the lock is held by another worker, the deployment is skipped.

### 2. The Heartbeat Loop
A 5-second `asyncio` background task extends the TTL of all active locks owned by the local TEE instance to 15 seconds. If the lock expires or is hijacked (e.g. Redis timeout/split-brain), the worker forcefully shuts down its local bot loop, guaranteeing it will not execute trades if it loses the lock.

### 3. The Global Reconciler
A 10-second background task queries PostgreSQL for strategies in `running` status. If it successfully acquires a Redis lock for a strategy not in its local execution pool, it "adopts" the strategy and launches it, seamlessly recovering work left behind by crashed nodes.

### 4. Graceful Shutdown
Upon receiving a `KeyboardInterrupt` or `SIGTERM`, the TEE instantly deletes all owned Redis locks in the `shutdown()` hook. This allows other nodes to instantly adopt the strategies without waiting for the 15-second TTL to expire, enabling zero-downtime rolling deployments.
