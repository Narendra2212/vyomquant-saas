# Migration and Deployment Checklist

Nothing in this branch is deployed. Merging and deploying are the owner's decision.

## Migration state

| Migration | What | Production | Rollback |
|---|---|---|---|
| 078 `sec_definer_caller_guard` | Revokes / wraps SECURITY DEFINER RPCs | **Applied** 2026-11-19 20:53 UTC (approved); recorded in `supabase_migrations` | SQL in file header |
| 079 `exec_env01_execution_records_is_sandbox` | `ADD COLUMN IF NOT EXISTS is_sandbox boolean` | **Applied** 2026-11-19 (approved). **Not recorded** in `supabase_migrations` (run with `execute_sql` after a lock-timeout retry). | `ALTER TABLE public.execution_records DROP COLUMN is_sandbox;` (only after reverting d10af0cc) |
| 080 `sec_def01_profile_trigger_uses_the_body` | Trigger → definer / `__impl`; guard limited to browser roles | **Not applied. Needs approval.** In production, step 1 is a no-op (no trigger); step 2 relaxes the guard only for callers holding database credentials. | SQL in file header (re-create 078's guard) |

All three are idempotent and change no rows.

## Deployment order

1. **Pre-checks**
   - CI green on the PR head, including the PostgreSQL lane (now lists the SEC-DEF-01 test).
   - Take a Supabase backup / PITR mark.
   - `scripts/verify_schema_drift.py` against production. Expected deltas: 080 pending, and 079 unrecorded.
2. **Apply 080** with `apply_migration`, so that it is recorded. Optionally re-apply 079 the same way, so the ledger records it (a no-op on the schema).
3. **Verify the database**
   - Anon `POST /rest/v1/rpc/increment_ml_addon` → 401/42501.
   - Signed-in user calling `create_referral_payout_request` for another id → 42501.
   - Own `create_referral_code_for_user` → 200.
4. **Deploy the backend** (ECS). It depends on 079: `mark_submitted` writes `is_sandbox`; `_stamp_environment` is best-effort, so an absent column would log rather than fail.
   - Keep `VYOMQUANT_LIVE_SANDBOX_ONLY=true`.
   - Confirm `RESUME_LIVE_DEPLOYMENTS` is unchanged.
5. **Deploy the frontend.** It needs the new `POST /api/referral/claim`, so deploy it after step 4.
6. **Post-deploy smoke tests**
   - Paper account exposure is unchanged across one forced task restart (PAPER-EXP-01).
   - A reducing paper sell is accepted at more than 80 % utilisation (RISK-REDUCE-01).
   - A partial risk save keeps the other fields (RISK-PUT-01).
   - `plan_usage_ledger` gains a row on the next backtest (QUOTA-LEDGER-01).
   - Free-account cancel returns 409 (BILL-CANCEL-01).
   - `pg_stat_activity` shows no watchdog `idle in transaction` older than one cycle (TXN-IDLE-01).
7. **Owner actions** that unblock the remaining tests:
   - Fix Supabase Auth SMTP (AUTH-SMTP-01).
   - Refresh the AWS credentials for CloudWatch.
   - Provide a Testnet key and MFA in the secure UI for the QA identity.
   - Create a Pro QA account for ML training.

## Rollback

- **Code:** redeploy the previous ECS task definition revision and the previous frontend build.
- **Schema:** 078 / 079 / 080 have header rollbacks. Rolling back 078 re-opens the P0, so do it only together with a revoke of anon EXECUTE.
