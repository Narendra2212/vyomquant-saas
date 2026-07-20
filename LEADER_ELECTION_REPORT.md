# LEADER_ELECTION_REPORT

## Locking Strategy
- **Key Schema**: `tee:lock:{strategy_id}`
- **TTL**: 15 seconds (Renewed every 5s)
- **Algorithm**: Redis `SETNX` (Set if Not eXists).

## Validation Scenarios

### 1. Duplicate Prevention
- **Scenario**: 5 TEE instances are listening to `command_queue`. A deployment payload is pushed.
- **Result**: `tee_group` Consumer Group behavior routes the message to exactly one TEE instance. In an edge case where PEL recovery triggers a duplicate message delivery to two instances, the `SETNX` lock acquisition guarantees exactly one instance calls `fleet.start_bot`.

### 2. Expired Lock Handling
- **Scenario**: TEE Instance A loses its connection to Redis for 20 seconds. The 15s lock expires. TEE Instance B claims the lock via the Reconciler.
- **Result**: TEE Instance A's heartbeat loop throws an error when trying to renew the expired lock, explicitly halting its local bot execution to prevent duplicate signals with TEE Instance B.

## Status
The leader election implementation is highly robust and relies entirely on existing Redis infrastructure.
