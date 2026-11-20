# Bug Remediation Log

Each fix was made only after the defect was reproduced. Each has a regression test that fails without the fix and passes with it; the fail-before check was done by stashing the fix and re-running.

## Fixed

| ID | Sev | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| SEC-DEF-01 | P0 | SECURITY DEFINER RPCs took the target user id from the caller and checked nothing; anon EXECUTE was granted | Migration **078**: revoke service-only primitives; caller-checking wrappers for the user-facing RPCs | `test_sec_def01_definer_functions_check_the_caller_postgres.py` | 0851b9c6 |
| SEC-DEF-01b | P1 | 078's wrapper refused (a) the repository's profiles referral-code trigger, which broke **every profile insert on a repository-provisioned DB** (production has no such trigger), and (b) database-credentialed callers with no JWT claim | Migration **080**: trigger becomes definer and calls `__impl`; the guard applies to browser roles only | same file (+2 tests); PG lane 239 → 0 new failures | 16b3140d |
| PAPER-EXP-01 | P1 | Ledger position was added, not set, into the engine's recovered exposure on every restart | Seed `recovered_exposure_coins = max(0, open_long_size)` | `test_risk_reduce01_…` (exposure cases) | 0851b9c6 |
| RISK-REDUCE-01 | P1 | Opening limits were applied to reducing orders | Reducing orders skip drawdown / daily-loss / streak / utilisation; the kill switch stays absolute | `test_risk_reduce01_reducing_orders_pass_opening_limits.py` (8) | 0851b9c6 |
| REF-ATTR-01 | P1 | Signup never read `?ref=`; attribution lived only in an unused endpoint | `referral_attribution.attribute_referral`; `POST /api/referral/claim`; frontend `pendingReferral` capture and flush | `test_ref_attr01_…`, `pendingReferral.test.js` | c0068300 |
| LIVE-ENT-01 | P1 | Live entitlement asserted only at the deployment gate | Per-order `live_trading_entitlement_holds`, fail closed, audited | `test_live_ent01_…` | a0182912 |
| EXEC-ENV-01 | P1 | No environment on fills; testnet counted as live history | Migration **079** `is_sandbox`; stamped on submit; gate requires `false` | `test_exec_env01_…`, `test_mkt07_…` | d10af0cc |
| TXN-IDLE-01 | P1 | Watchdog loops never ended their read transaction | `_end_cycle_transaction()` each cycle | `test_txn_idle01_…` | f647033c |
| QUOTA-LEDGER-01 | P2 | Ledger written with the user's client; only service_role may INSERT; error swallowed at debug | Ledger read / write / release via the service client; non-duplicate failures log a warning | `test_quota_ledger01_…` | d783934a |
| RISK-PUT-01 | P2 | Update model defaulted omitted fields to platform defaults, so the "keep stored value" branch never ran | Omitted fields default to `None` | `test_risk_put01_…` | 65165bdc |
| NOTIF-RISK-01 | P2 | Constant idempotency key against a durable (user, key) dedupe | Key includes `updated_at` | `test_risk_put01_…::test_each_save_…` | 92206c11 |
| BILL-CANCEL-01 | P2 | Cancel had no "is there a paid plan" check | 409 `NO_ACTIVE_SUBSCRIPTION` on Free | `test_bill_cancel01_…` | 1e59139c |
| CI-LANE-01 | P3 | New PG-gated test missing from the CI PostgreSQL lane (would be silently skipped) | Listed in `01-pr-check.yml` | `test_every_postgres_gated_file_runs_in_a_postgres_lane.py` | 16b3140d |

## Production data corrected during the audit (QA account only)

| What | Action |
|---|---|
| `ml_addons_purchased` 0 → 1, from the SEC-DEF-01 reproduction | Reverted to 0 |
| `subscription_status='cancelled'`, `cancel_at_period_end=true`, from the BILL-CANCEL-01 reproduction | Reverted to `NULL` / `false` |
| Risk settings changed during RK tests | Restored to 500 / (clamped 5) / 3 / armed |
| Support ticket `[QA-CERT]` c33d3685 | Left closed, labelled |
| Idle-in-transaction watchdog backend (2 h 27 m) | Terminated to let 079 apply. The watchdog reconnected on its own. |

## Open (not fixed in this branch)

| ID | Sev | Defect | Proposed fix |
|---|---|---|---|
| AUTH-SMTP-01 | **P0** | Email signup returns 500 (SMTP 535) | Owner: update Supabase Auth SMTP credentials |
| QUOTA-422-01 | P2 | Body-invalid requests consume metered quota | Record the reservation on `request.state` in the guard; release it in `main.validation_exception_handler` |
| STRAT-QUOTA-01 | P2 | Owner can REST-flip a deployment to active, bypassing the bot quota | Trigger refusing browser-role transitions to active / running; status writes via the service role |
| BT-504-01 | P2 | Synchronous backtest 504s at 60 s | UI uses the async start endpoint for long windows |
| BT-RISK-01 | P2 | spread / risk_per_trade / max_drawdown / daily_loss_limit not simulated (disclosed) | Implement in the runtime, or remove them from the form |
| MARGIN-01 | P2 | account- / margin-health disagree with the guard's utilisation | Compute both from the same paper ledger exposure |
| TF-500-01, COPILOT-404-01 | P3 | 500s for an invalid timeframe / unknown session | 422 / 404 |
| IDOR-404-01 | P3 | 200-empty for other tenants' strategy sub-routes | 404 |
| FX-META-01, PERF-01, RK-08 | P3 | Misleading `fx_rate`; slow cold APIs; duplicated daily-loss label | — |
| ADV-01 | P3 | Supabase advisors (search_path, leaked-password protection, GraphQL) | Hardening migration plus dashboard toggle |
