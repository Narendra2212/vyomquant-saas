# PRODUCTION_ROLLBACK_GUIDE

In the event of a catastrophic system failure (e.g., execution engine loops, database corruption, massive latency spikes), follow these steps:

## Level 1: Trading Halt (The Kill Switch)
If the Execution Engine begins behaving erratically (e.g., opening incorrect positions):
1. Immediately change the environment variable `AERORA_MODE=safe`.
2. Restart the backend container cluster.
3. **Result**: The `SafetyMonitor` will enforce `ExecutionFlags.LIVE_TRADING_ENABLED = False`. The API will remain online for users to view data, but ALL order routing to CCXT will throw a `SafetyViolationError`.

## Level 2: API Blackout
If a severe data leak or zero-day authentication bypass is detected:
1. Revoke the Supabase `SUPABASE_ANON_KEY`.
2. Scale backend containers to 0.
3. Place a static "Maintenance" HTML page on the Frontend CDN.

## Level 3: Database Reversion
If user strategies or marketplace ratings are massively corrupted:
1. Navigate to the Supabase Dashboard -> Database -> Backups.
2. Select the Point-In-Time-Recovery (PITR) snapshot immediately preceding the incident.
3. Confirm the restoration.
4. Notify users via email regarding the data loss window.

## Recovery Operations
1. After the bug is identified and patched, merge the hotfix.
2. Deploy the hotfix to staging.
3. Run `EXCHANGE_RECOVERY_VALIDATION` checks to ensure the engine correctly reconciles open positions from the exchange against the reverted database state.
4. Set `AERORA_MODE=live` and deploy to production.
