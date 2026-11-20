# Billing and Marketplace Audit

## Billing (Razorpay)

| Area | Result | Evidence |
|---|---|---|
| Provider mode | Live-only by design | `_validate_provider_credentials` refuses `rzp_test_` keys: a test key collects no money and would grant paid plans for free |
| Test-mode checkout E2E | **BLOCKED** | Real charges are forbidden by the audit rules, and production cannot run in test mode. To verify, use a staging stack with `rzp_test_` keys and the guard relaxed there only. |
| Plans and localisation | PASS | ₹499 / ₹999 / ₹2,499; localised USD $5.34 / month; annual saves 17 % |
| Entitlements and quotas | PASS | Server-side; Free: 1 strategy, 10 backtests, 0 bots, 0 ML |
| Webhook authentication | PASS | Unsigned or forged → 403 (IP allowlist, then HMAC). The rejection reaches Sentry. |
| Cancel → resume | Cancel un-gated, resume needs AAL2 | SEC-15 design: stopping billing never needs MFA |
| Cancel with no paid plan | **FAIL → fixed (BILL-CANCEL-01, P2)** | Free account got 200 and `subscription_status='cancelled'`; the only free profile of 185 in that state. Reverted. Now 409 `NO_ACTIVE_SUBSCRIPTION`. |
| Metered quota durability | **FAIL → fixed (QUOTA-LEDGER-01, P2)** | Ledger empty; Redis-only meters |
| Invalid request consumes quota | **FAIL, open (QUOTA-422-01, P2)** | 10 / 10 used with 5 stored runs |
| Invoices / payment methods | PASS | `[]` for the QA account (never paid) |

## Marketplace

| Area | Result | Evidence |
|---|---|---|
| Browse / my-strategies / analytics / payouts | PASS | Own data only |
| Publish / subscribe plan gates | PASS | Free: 0 / 0 |
| Live-history eligibility (24 h) | **FAIL → fixed (EXEC-ENV-01, P1)** | Testnet fills qualified a strategy as "live"; now `is_sandbox = false` is required |
| Seller → subscriber journey | **BLOCKED** | No second identity (signup broken); 0 public listings |
| Creator payouts | Manual by policy | No payout was triggered |

## Referral program

| Area | Result | Evidence |
|---|---|---|
| Attribution of `?ref=` signups | **FAIL → fixed (REF-ATTR-01, P1)** | Signup page ignored `ref`; the only attribution code was in an endpoint the UI never called. Now: captured on `/signup`, flushed after first sign-in to `POST /api/referral/claim`. Rules: active code, no self-referral, first claim wins, 7-day window. |
| Payout request for another user | **FAIL → fixed (SEC-DEF-01, P0)** | |
| Referral E2E with a second identity | **BLOCKED** | Signup broken |
