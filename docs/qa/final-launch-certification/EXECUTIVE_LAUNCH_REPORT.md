# VyomQuant — Final Launch Certification: Executive Report

**Audit window:** 2026-11-19 → 2026-11-20 (UTC) · **Environment:** production (`app.vyomquant.in`, Supabase `wrkexcjqnidkdrayhlsi`, ECS Fargate) with Binance Testnet only · **Branch:** `claude/vyomquant-launch-blockers-wv92gv`

## Verdict: **NO-GO** (becomes **CONDITIONAL GO** once the three release conditions below are met)

The trading core held up under re-test. Tenant isolation, RLS, the live-execution gate, kill switches, exchange-key encryption and webhook authentication all passed. Even so, I cannot certify launch today, for three reasons:

1. **New users cannot sign up with email (P0, open, owner action).** Supabase Auth `/signup` returns 500 "Error sending confirmation email". The auth log shows SMTP `535 Authentication credentials invalid`. Google OAuth works. Until SMTP is fixed, email signup, password reset and the email-confirmation flow are down for every new customer.
2. **Fixes for 12 defects (one P0, seven P1, four P2) are committed but not deployed.** Two schema migrations (078 and 079) are already live in production. A third (080) is pending. The code that depends on them is not deployed: P0 SEC-DEF-01's live half is the migration, but the P1 code fixes (paper exposure, live entitlement, referral attribution, testnet history, watchdog transactions, risk settings) only take effect after a backend and frontend deploy.
3. **Several critical paths are BLOCKED rather than PASS.** These need inputs the audit was not allowed to fabricate:
   - QA-identity exchange connect and Testnet deploy need the owner's Testnet key plus MFA.
   - Subscriber, second-tenant and referral E2E need signup to work.
   - Paid checkout cannot be tested, because production is live-key-only by design and the rules forbid real charges.
   - ML training needs a Pro plan.
   - CloudWatch is unavailable because the AWS credentials expired.

## Release conditions for CONDITIONAL GO

| # | Condition | Owner |
|---|---|---|
| C1 | Fix the Supabase Auth SMTP credentials; re-run the email signup and confirmation test | Platform owner |
| C2 | Merge the PR from this branch; apply migration 080; deploy backend, then frontend (078/079 already applied) | Platform owner |
| C3 | Re-run the BLOCKED rows in `FULL_TEST_REGISTER.md` (Testnet deploy with the QA identity, subscriber flow, ML training on a Pro QA account) and record PASS | QA + owner |

## Defects found and fixed in this audit

| ID | Sev | Summary | State |
|---|---|---|---|
| SEC-DEF-01 | **P0** | Anon key could run SECURITY DEFINER RPCs for any user (reproduced: `increment_ml_addon` granted an ML add-on unauthenticated) | **Fixed + migration 078 applied to prod**, re-verified |
| SEC-DEF-01b | P1 | 078's guard broke every profile insert on a **repository-provisioned** database (staging/CI/DR) through the referral-code trigger, and refused DB-credentialed callers. Production has no such trigger, so prod signup was not affected. | Fixed: migration 080 (pending apply) |
| PAPER-EXP-01 | P1 | Paper exposure re-seeded ~$5k per restart (QA account at ~$80k against a $5k cap) | Fixed in code, not deployed |
| RISK-REDUCE-01 | P1 | Risk limits blocked reducing / closing orders (could not de-risk at 80% utilisation) | Fixed in code |
| REF-ATTR-01 | P1 | Referral links never attributed a signup | Fixed in code |
| LIVE-ENT-01 | P1 | Live entitlement never re-checked per order after the deploy gate | Fixed in code |
| EXEC-ENV-01 | P1 | Testnet fills counted as marketplace "live" history | Fixed; migration 079 applied |
| TXN-IDLE-01 | P1 | Order watchdog held a 2.5-hour idle transaction (blocked DDL) | Fixed in code |
| QUOTA-LEDGER-01 | P2 | Durable usage ledger had 0 rows; metered quotas lived only in Redis | Fixed in code |
| RISK-PUT-01 | P2 | A partial risk-settings save silently loosened Max Daily Loss (250 → 500, reproduced in prod) | Fixed in code |
| NOTIF-RISK-01 | P2 | Only a user's first risk-settings change ever produced a notification | Fixed in code |
| BILL-CANCEL-01 | P2 | A free account could "cancel" a non-existent subscription (state written, then reverted) | Fixed in code |

Open, not fixed (see `BUG_REMEDIATION_LOG.md`):
- **P2:** QUOTA-422-01 (invalid requests consume backtest quota), BT-504-01, BT-RISK-01, STRAT-QUOTA-01, MARGIN-01.
- **P3:** seven items.

## What was proven, with evidence

- **RLS and tenant isolation:**
  - RLS is on for all 90 public tables.
  - Anon reads get 0 rows on 12 sensitive tables.
  - An IDOR sweep of 55 parameterised routes leaked no cross-tenant data.
  - Another tenant's support ticket or notification returns 404.
- **Role and plan gates:**
  - A GET sweep of 120 routes returned no 5xx except the two P3s; all 28 admin/internal routes gave 403 to a customer.
  - Plan gating is enforced server-side: ML training is 403 on Free, and so is the backtest quota.
- **Backtests:** the engine applies commission, slippage and capital; 4× costs produce a worse result. Every limitation is disclosed in the response (`configuration_not_applied`, `truncated_by_cap`).
- **Billing webhook:** unsigned and forged requests get 403 (IP allowlist, then HMAC).
- **Support:** create, list, view, comment and close work; raw secrets in a ticket are refused; staff routes give a customer 403.
- **Sentry:** ingestion is live, with events arriving within seconds of the audit's own probes.
- **Test suites:** frontend 4,056/4,056; backend in `FULL_TEST_REGISTER.md` §Suites.

## Safety attestation

Every rule in the master prompt §1 held:
- Testnet only; no production exchange credentials.
- No real-money orders, charges, emails or payouts.
- MFA never bypassed.
- No secret printed or committed.
- QA data labelled `[QA-CERT]`.
- Two state changes on the QA account (ML add-on, cancellation flags) were made, observed and reverted.
- Migrations 078/079 were applied only after explicit owner approval, each with a rollback.
- Nothing was merged or deployed.
