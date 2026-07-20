# LAUNCH_DAY_RUNBOOK

## T-7 Days
- **Infrastructure Freeze**: No new infrastructure changes. All environments must mirror production exactly.
- **Data Pruning**: Clear out all test data, test users, and mock strategies from the staging database.
- **Key Rotation**: Rotate all API keys and JWT secrets. Ensure no legacy keys exist in `.env` configs.

## T-3 Days
- **Code Freeze**: Only P0 (Severity Critical) bug fixes permitted.
- **Load Test**: Perform simulated loads hitting `/api/strategies/backtest` and `/api/library` to verify rate-limits function as intended.
- **Monitoring Baseline**: Confirm Sentry is tracking both backend exceptions and frontend unhandled rejections cleanly.

## T-1 Day
- **Database Backup**: Trigger a manual Supabase snapshot (separate from the standard PITR).
- **DNS Provisioning**: Ensure `app.algo22.io` and `api.algo22.io` are resolving globally.
- **Smoke Testing**: Perform a full unauthenticated to authenticated flow against the production URL (not localhost).

## Launch Morning
- **08:00 AM**: DevOps verifies CPU/Memory bounds on ECS/Render clusters.
- **08:15 AM**: DB Admin validates Supabase metrics (connections, CPU).
- **08:30 AM**: Set `AERORA_MODE=live`. Verify Kill-Switch is correctly monitoring.
- **08:45 AM**: Seed Marketplace with 5 "Gold Standard" strategies created by the Admin account.
- **09:00 AM**: Final team huddle.

## Final Go/No-Go
- Ensure 0 P0/P1 bugs in JIRA/Tracker.
- Ensure Sentry Dashboard is connected.
- Determine GO.

## Launch
- **09:30 AM**: Send invites to the first 25 Private Beta users (Batch 1).
- **10:00 AM**: Monitor live Discord/Slack feedback channel.
- **11:00 AM**: Send invites to remaining 75 Private Beta users (Batch 2).

## First Hour
- **Action**: Monitor Sentry heavily. Expect edge-case Python exceptions from unexpected DAG constructs.
- **Action**: Monitor `/api/metrics` for high latency (`>500ms`) on backtests.
- **Action**: Admin sweeps `/api/library/admin/pending` to approve early user publications.

## First Day
- **End of Day Review**: Compile total HTTP 500 count. Identify the most common crash.
- **Communication**: Welcome post in Discord, acknowledging any day 1 hiccups.
- **Data Review**: Check `library_strategies` for orphan data or weird clones.

## First Week
- **Weekly Deploy**: Group non-critical bug fixes into a single end-of-week patch.
- **Feedback Collection**: Send automated survey asking "How was your first backtest?".
- **Scale Up**: If stable, prepare infrastructure for the next cohort (User 101 to 500).
