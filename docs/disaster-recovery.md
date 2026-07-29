# Disaster Recovery Runbook: Postgres Restore & Redis Reconciliation

## Ownership & Escalation
- **Primary Owner**: Platform Engineering Lead
- **On-Call Execution**: Primary backend on-call engineer.
- **Escalation**: If Postgres cannot be restored via the Supabase dashboard within 15 minutes, immediately escalate to Supabase Enterprise Support.

## 1. SLA & Capabilities (Supabase Managed Postgres)
Our primary datastore is a Supabase-managed Postgres instance (Pro tier+).
- **RPO (Recovery Point Objective)**: **1 minute**. Supabase performs Point-in-Time Recovery (PITR) using WAL log archiving to S3.
- **RTO (Recovery Time Objective)**: **~15 - 30 minutes**. PITR requires spinning up a new database instance and sequentially replaying the WAL logs up to the specified minute.
- **Retention**: Physical backups are retained for 7 days (Pro) or 30 days (Enterprise).

## 2. Postgres Restore Procedure
> [!CAUTION]
> A Point-in-Time Restore in Supabase involves provisioning a *new* database instance. You must update connection strings across all services once the restore completes.

1. **Trigger Restore**:
   - Log into the Supabase Dashboard.
   - Navigate to **Database > Backups > Point in Time Recovery (PITR)**.
   - Select the exact minute to restore to (Timestamp `T`).
   - Click **Restore**.
2. **Monitor**:
   - Wait ~15-30 minutes for the new database instance to provision and replay WAL logs.
3. **Update Infrastructure**:
   - Once the new database is available, retrieve the new `DB_HOST` and `DB_PASSWORD` (if reset).
   - Update the configuration secrets in AWS Parameter Store / Terraform.
   - Restart the ECS services (`api`, `workers`) so they connect to the new database instance.

## 3. Redis State Reconciliation (CRITICAL)
Postgres and Redis (`ElastiCache`) are strictly coupled. If Postgres is rolled back to Timestamp `T`, but Redis continues running, Redis will contain "future" orphaned state. **If you do not reconcile Redis, the application will read corrupted cache data and execute duplicate trades.**

### Step 3.1: Stop all Workers
Before reconciling, ensure no ECS workers are actively reading/writing data:
```bash
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-worker-backtest-service-prod --desired-count 0
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-worker-command-service-prod --desired-count 0
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-worker-dag-service-prod --desired-count 0
```

### Step 3.2: Connect to ElastiCache
Gain CLI access to Redis (e.g., via a bastion host or AWS CloudShell inside the VPC):
```bash
redis-cli -h <vyomquant-redis-cluster-endpoint> -p 6379
```

### Step 3.3: Flush Orphaned Caches
The `state_service.py` heavily caches `Order` and `Position` state in DB 0. Idempotency keys live in DB 3. Since Postgres is restored, these keys are now invalid.
```redis
# Purge Cache (Orders, Positions, Sessions)
SELECT 0
FLUSHDB

# Purge Idempotency Keys to allow legitimately lost requests to be retried
SELECT 3
FLUSHDB
```

### Step 3.4: Event Sourcing Recovery (Stream Replay)
The Command Bus uses Redis Streams (DB 2). Any trades/commands processed *after* Timestamp `T` were acknowledged by consumers in Redis but their resulting state changes were erased in the Postgres rollback.
Because Streams have a 7-day TTL, we can **replay** these commands to achieve zero data loss.

1. **Find the Stream ID** matching Timestamp `T`. 
   Redis stream IDs are formatted as `<millisecondsTime>-<sequenceNumber>`. 
   Convert Timestamp `T` to UNIX epoch milliseconds (e.g., `1690000000000`).
2. **Reset Consumer Group Offsets**:
```redis
SELECT 2
# Force the consumer group to rewind to Timestamp T
XGROUP SETID command_queue command_workers 1690000000000-0
XGROUP SETID market_data market_workers 1690000000000-0
XGROUP SETID strategy_signal strategy_workers 1690000000000-0
XGROUP SETID risk_signal risk_workers 1690000000000-0
XGROUP SETID execution_signal execution_workers 1690000000000-0
```

### Step 3.5: Resume Operations
Scale the workers back up to their original desired counts:
```bash
aws ecs update-service --cluster vyomquant-cluster-prod --service vyomquant-worker-command-service-prod --desired-count 3
# Repeat for other workers...
```
*The workers will automatically consume and process all streams from Timestamp `T` forward, rebuilding the database state.*

## 4. Scheduled DR Drills
This runbook must be tested **Quarterly** in the Staging environment.

**Drill Execution Protocol**:
1. Schedule a 2-hour window and notify the engineering team.
2. Ensure test transactions are actively generating load in Staging.
3. Record Timestamp `T`. Wait 10 minutes.
4. Execute the Postgres PITR restore to Timestamp `T` using the Supabase Dashboard.
5. Follow Section 3 strictly to clear caches and rewind stream offsets.
6. **Validation**: Assert that the test transactions processed in the 10-minute gap are successfully replayed by the workers and exist in the restored Postgres database.
7. Update this runbook with any friction points discovered during the drill.
