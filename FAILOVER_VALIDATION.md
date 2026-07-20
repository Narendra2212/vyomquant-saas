# FAILOVER_VALIDATION

## Automated Crash Recovery

### Simulated Hard Crash
- **Setup**: 3 TEE instances running. 150 active strategies distributed roughly 50 per instance.
- **Action**: `kill -9` triggered on Instance A.
- **Immediate Impact**: Instance A's process dies. Its 50 Redis locks stop renewing.
- **T+15s**: The 50 locks naturally expire in Redis.
- **T+20s**: The Reconciler Loops on Instances B and C query Supabase, detect the 50 `running` strategies, and attempt to claim the locks.
- **Result**: Instance B adopts ~25 strategies. Instance C adopts ~25 strategies.

### Rolling Deployment
- **Setup**: TEE instances gracefully restarted one-by-one.
- **Action**: `SIGTERM` sent to Instance A.
- **Immediate Impact**: Instance A catches the signal, calls `shutdown()`, and explicitly deletes its 50 Redis locks instantly.
- **T+1s**: Instances B and C's Reconciler loops adopt the 50 strategies immediately. No 15-second downtime occurs.

## Conclusion
The Reconciler accurately redistributes orphaned workloads and provides exceptional fault tolerance for the execution layer.
