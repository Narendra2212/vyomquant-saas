# Database and API Audit

## Database (Supabase Postgres 17.6, project `wrkexcjqnidkdrayhlsi`)

| Area | Result | Notes |
|---|---|---|
| RLS coverage | PASS | 90 / 90 public tables RLS-enabled |
| SECURITY DEFINER caller checks | FIXED (078) | See RISK_AND_SECURITY_AUDIT §1 |
| Migration drift | **FOUND** | 023's `increment_ml_addon` revoke was never live; prod `create_referral_code_for_user` was SECURITY DEFINER where the repo's is not. Both are superseded by 078. |
| `execution_records.is_sandbox` | ADDED (079) | Nullable boolean; legacy rows NULL, which the gate treats as not-live |
| Durable usage ledger | **FAIL → fixed (QUOTA-LEDGER-01)** | `plan_usage_ledger`: **0 rows** platform-wide; only service_role may INSERT; the app wrote with the user's client and swallowed the error |
| Long transactions | **FAIL → fixed (TXN-IDLE-01)** | 2 h 27 m idle in transaction from the watchdog |
| Schema snapshot | PASS | `staging/provisioning_snapshot.json` regenerated from a fresh provision. Deltas are exactly 078 and 079; `scripts/verify_schema_drift.py` is clean. |
| Advisors | P3 backlog | 34 mutable `search_path`; 15 tables with RLS and no policy (service-only); GraphQL exposed |

### Migration ledger note

- **078** was applied with `apply_migration`, so it is recorded in `supabase_migrations`.
- **079** was applied with `execute_sql` after a lock-timeout retry, so it is **not** recorded. It is idempotent (`ADD COLUMN IF NOT EXISTS`). To record it, re-apply it through the migration tool.

## API

| Sweep | Result |
|---|---|
| GET sweep, 120 routes as a customer | 88 × 200, 28 × 403 (all admin/internal), 4 × 422; no 5xx except the two P3s below |
| IDOR sweep, 55 parameterised routes | No cross-tenant data; some 200-empty instead of 404 (P3) |
| Mutating routes covered by the validation sweep test | Plus `POST /api/referral/claim` (new) |
| Error shape | Uniform `{error, message, detail, status_code, timestamp, path}`; no stack traces or SQL in bodies |

Open API defects:

| ID | Sev | Route | Defect |
|---|---|---|---|
| BT-504-01 | P2 | `POST …/backtests/execute` | 504 at the 60 s gateway while the run completes; use the async start endpoint |
| QUOTA-422-01 | P2 | metered routes (backtests / optimisations / ML) | The reserving quota dependency runs **before** body validation, so a 422 still spends a unit. Fix: release in the app's `RequestValidationError` handler, through a reservation recorded on `request.state` (a yield dependency was tried; callers `await` the guards directly, so it breaks the guard's contract). |
| TF-500-01 | P3 | `/api/market/candles\|data/{s}/{tf}` | Invalid timeframe → 500 |
| COPILOT-404-01 | P3 | `/api/v1/copilot/sessions/{id}/messages` | Unknown session → 500 |
| FX-META-01 | P3 | `/api/billing/plans` | Top-level `fx_rate: 1.0` while prices are FX-converted (localised prices are correct) |
| PERF-01 | P3 | entitlements, unread-count, legal/acceptance | 1–6.5 s on cold load; entitlements polled about every 15 s at 1–2 s |
