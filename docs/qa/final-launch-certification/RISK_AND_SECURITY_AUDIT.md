# Risk and Security Audit

## 1. P0 — SEC-DEF-01: SECURITY DEFINER RPCs reachable for any user

**Found** through the Supabase security advisor, then **reproduced**. An unauthenticated `POST /rest/v1/rpc/increment_ml_addon` carrying only the public anon key raised the QA account's `ml_addons_purchased` from 0 to 1. It was reverted immediately.

| Function | Who could call it | Impact |
|---|---|---|
| `increment_ml_addon(uuid)` | anon | Grant paid ML add-ons to any account. Migration 023 declared this revoke, but production never had it (drift). |
| `execute_secure_v2 / v3` | anon | Debit any user's `profiles.balance` |
| `reverse_referral_commission` | anon | Reverse any referrer's commission |
| `create_referral_code_for_user` | anon | Create codes and wallets for any user |
| `create_referral_payout_request` | authenticated | File a payout against **another** user's wallet, to payout details you choose |
| `paper_apply_fill_atomic` | authenticated | Write fills into another user's paper account |

**Fix: migration 078, applied to production after owner approval. Narrowing only.**
- Service-only primitives have EXECUTE revoked from PUBLIC, anon and authenticated.
- User-facing RPCs are renamed to `<name>__impl` (service_role only). A same-signature wrapper calls `sec_def01_assert_caller_is(p_user_id)`: the caller must be that user or the service role, otherwise SQLSTATE 42501.
- Callers needed no change. Rollback SQL is in the migration header.

**Verified after the fix:**
- Every exploit request now returns 42501.
- The legitimate self-call returns 200 (referral code CLAUDEBDA7).
- PG test `tests/test_sec_def01_definer_functions_check_the_caller_postgres.py` fails on the pre-078 schema and passes after.

## 2. Authentication

- **AUTH-SMTP-01 (P0, open, owner action).** Email signup returns 500. Supabase Auth's SMTP login is rejected (`535 Authentication credentials invalid`).
  - Fix: Supabase dashboard → Authentication → SMTP settings, with the provider's current credentials.
  - Also affected: password reset and email change.
- **Google OAuth:** PASS.
- **MFA (AAL2):**
  - Required for exchange-key mutation (`require_aal2` on `exchange.py`), billing resume and payment-method deletion.
  - Cancellation is deliberately not MFA-gated (SEC-15, documented and tested).
  - MFA was never bypassed or forged in this audit.
- **Supabase advisor (P3):** leaked-password protection is off. Recommend enabling it.

## 3. Tenant isolation

- RLS is enabled on 90 / 90 public tables. 15 tables have RLS on but no policy; they are service-role only by intent (advisor P3).
- Anon key: 0 rows from 12 sensitive tables (profiles, exchange keys, execution records, paper ledgers, referral wallets, support tickets, and others).
- IDOR sweep of 55 parameterised routes, using other-tenant and random ids: no cross-tenant data.
  - **IDOR-404-01 (P3):** some strategy sub-routes answer 200 with an empty list instead of 404.
- Support ticket, notification and signal of another tenant: 404.
- Profiles guard trigger refuses browser writes to `subscription_tier`, `plan_limit_overrides`, `ml_addons_purchased` and `balance` (403).
- Exchange keys: Fernet-encrypted, sandbox flag, never returned to the browser. The audit never read a decrypted value.

## 4. Authorisation

- 28 admin and internal routes: 403 for a customer. Includes `/api/internal/portfolio-mgmt/*` and `/api/support/admin/*`.
- **STRAT-QUOTA-01 (P2, open):** deployment `status` is owner-writable through REST, which bypasses the bot-count quota. Proposed migration: a trigger that refuses browser-role transitions into `active` / `running`, with status writes moved to the service role.
- `profiles` column-level UPDATE grants are broader than the guard trigger needs (P3, defence in depth).

## 5. Risk controls

| Control | Result |
|---|---|
| Capital-utilisation limit on opening orders | PASS (refused at 80.14 %) |
| Reducing orders over the limit | FIXED (RISK-REDUCE-01) |
| Platform ceilings clamp stored limits | PASS (max positions 7 → 5) |
| Partial save keeps tightened limits | FIXED (RISK-PUT-01: a partial save had reset Max Daily Loss 250 → 500 in production) |
| Every risk change notifies the user | FIXED (NOTIF-RISK-01: one notification per user, ever, because of a constant idempotency key) |
| Margin / account health agrees with the guard | **FAIL, open (MARGIN-01, P2):** account-health reports exposure 0.0 and margin-health free margin $19.86, while the guard reports 80.14 % utilisation on an open paper position |
| Live entitlement per order | FIXED (LIVE-ENT-01) |

## 6. Input handling

- Validation sweep: 4 × 422 for malformed input.
- **P3:** `/api/market/candles|data/{symbol}/{bad timeframe}` returns 500 (should be 422). `/api/v1/copilot/sessions/{unknown}/messages` returns 500 (should be 404).
- Support refuses tickets that contain raw secrets (400).
- Billing webhook: IP allowlist, then HMAC. Unsigned and forged requests get 403.

## 7. Advisor backlog (P3)

- 34 functions with mutable `search_path`.
- GraphQL endpoint exposed to anon (introspection of table names).
- Leaked-password protection off.

None is exploitable on its own today, given RLS and the grants above.
