# Launch remediation report — branch `remediation/launch-blockers`

## Outcome: **NO-GO**
Candidate code commit: `5346ecd` (validated exactly as committed). Base: `28a4efa`.
**Push to `main`: NOT performed.** The release conditions are not met: 30 P0/P1 findings are not VERIFIED_FIXED, and several (RT-02, RT-16, RT-21, RT-04/RT-01, RT-05, RT-08, RT-17, AUD-02) are multi-week architectural work that cannot be done or verified here. Work is on the remediation branch only.

## Verification summary (code-level / sandbox; nothing production-verified)
- Backend (serial, real-Postgres lane enabled, chaos test excluded): 14651 passed, 100 skipped, 13 failed. All 13 failures also fail on the untouched baseline commit (billing cancel/resume + 6 route sweep cases needing Supabase credentials/network; rate-limit-key, ML guard, credential-redaction, pgcrypto-preflight and referral-payout PG tests). No regression versus baseline remains.
- Frontend: vitest 3743 passed (157 files); eslint clean; `npm run build` succeeds.
- Fresh database provisioning from source (incl. new migrations 033/034): exit 0.
- Skipped: tests/test_chaos_recovery.py, load tests (need live infrastructure), 100 env-gated tests.
- NOT done: any staging/production check, dependency audit re-run, exchange-sandbox order lifecycle, real-money paths (kept disabled throughout).

## Findings changed (all others remain OPEN; full ledger in REMEDIATION_LEDGER.json/.csv)
| ID | Sev | Status | Component | Fix commit | Evidence | Remaining action |
|---|---|---|---|---|---|---|
| SEC-01 | P0 | EXTERNAL_BLOCKED | Security / secrets | fbaf266 | scripts/run_schema_migration_prod.py deleted; tests/test_migration_tooling.py::test_sec01_leaking_script_is_deleted_and_never_returns; no DSN literal remains in scripts/ | OPERATOR: rotate the exposed production database password in Supabase, purge the file from git history (git filter-repo + force-push by repo admin), re-check no copies in CI logs/forks. Rotation was NOT performed or verified here. |
| SEC-07 | P0 | VERIFIED_FIXED | Security / tenant entitlements | fbaf266 | exploit reproduced then blocked on real Postgres; tests/test_sec07_profiles_platform_columns_postgres.py (13) | Apply migration 033 to staging/production (human-run) and re-run the exploit script as an authenticated JWT. |
| RT-15 | P0 | VERIFIED_FIXED | Order safety (live) | fbaf266 | tests/test_rt15_platform_mode_gates_live_orders.py (12): live orders refused unless mode=live, enable flag true and tenant allow-listed | Staging verification of the gate configuration; live remains disabled. |
| RT-06 | P1 | VERIFIED_FIXED | Accounting / risk | c992510 | tests/test_rt06_exposure_follows_completed_fills_only.py | None. |
| PAPER-02 | P1 | IN_PROGRESS | Paper trading | 8b9511d d44eff6 | Containment done: hardcoded reference prices and client price for market orders removed; market orders refused (409 PAPER_MARKET_DATA_UNAVAILABLE) without a measured quote; tests/test_paper02_market_orders_need_measured_price.py (5) | Wire paper_trading_service.get_fresh_market_price to the MDS last-price read (currently returns None, so manual paper MARKET orders are refused until then). |
| SEC-08 | P1 | VERIFIED_FIXED | Admin tooling | fbaf266 | tests/test_admin_auth.py, tests/test_role_granularity_and_audit.py (404 on zero-row freeze) | Verify in staging with a real admin JWT. |
| DB-01 | P1 | VERIFIED_FIXED | Database / migrations | fbaf266 | fresh-DB provisioning exit 0 incl. 033/034; tests/test_db01_provisioning_order.py (3) | Run the same provisioning against a staging database. |
| RT-07 | P1 | VERIFIED_FIXED | Order safety (live) | c992510 | tests/test_rt07_live_bot_requires_measured_balance.py fails on baseline, passes now | None. |
| RT-10 | P1 | VERIFIED_FIXED | Exchange integration | c992510 | tests/test_rt10_vault_call_signature.py (3) fails on baseline, passes now | None. |
| RT-22 | P1 | VERIFIED_FIXED | Strategy runtime / API | c992510 | tests/test_rt22_deploy_pipeline_retired.py: endpoint returns 410 | None. |
| FE-03 | P1 | VERIFIED_FIXED | Frontend / trading safety | c992510 d44eff6 | vitest fe03_deploy_defaults_to_paper + deployPreflight; GET /api/system/trading-capabilities (tests/test_fe03_trading_capabilities_endpoint.py, 6) | None at code level. |
| DATA-01 | P1 | VERIFIED_FIXED | Market data | 7b7e6dc | tests/test_data01_historical_ohlcv_pages_backward.py (7) | Exercise against a real exchange sandbox (needs network egress). |
| BT-01 | P1 | VERIFIED_FIXED | Backtesting | 7b7e6dc | tests/test_bt01_backtest_never_fabricates.py (7) | None at code level. |
| SEC-14 | P1 | VERIFIED_FIXED | Security / execution | 43fa690 | tests/test_sec14_execution_signal_requires_measured_equity.py (3); endpoint also off by default (PRODUCTION_ROUTER_ENABLED) | Wire _measured_total_equity to a ledger/exchange-backed source before ever enabling the router. |
| IMPORT-02 | P1 | VERIFIED_FIXED | Infrastructure / broken imports | c992510 | tests/test_import02_internal_imports_resolve.py (AST guard; IMPORT-01/-03 recorded as known-broken) | Fix IMPORT-01 and IMPORT-03 (separate findings). |
| SEC-16 | P1 | VERIFIED_FIXED | Security / tenant entitlements | fbaf266 | tests/test_sec16_strategy_deployments_guard_postgres.py (12) on real Postgres | Move the API live-deployment insert to a service-role client after entitlement + live-gate checks BEFORE applying migration 034 in production; apply 034 (human-run). |

| MKT-01 | P1 | VERIFIED_FIXED | Marketplace | (this branch, post-report) | migration 035 + 8 real-Postgres tests | Apply 035 to staging/production (human-run) |

MKT-01 was fixed after the validation run above; the real-Postgres lane and migration tests were re-run on a freshly provisioned DB with 035 (only the 2 baseline PG failures remain). Remove MKT-01 from the unresolved list below. SEC-02 was also fixed afterwards (order-reconciled Razorpay webhook, 10 tests; billing/payment/marketplace lanes 1152 passed) — see the ledger.

## Unresolved P0/P1 blockers
- **SEC-01** (P0, EXTERNAL_BLOCKED): Production Postgres credential hardcoded in a tracked script
- **RT-04** (P0, OPEN): Paper Sessions (the persisted, canonical paper-trading path) can never start
- **RT-02** (P0, OPEN): Deploying a version saved by the current builder can never start a bot
- **RT-01** (P0, OPEN): Paper-mode bots have no market data: exchange object is None and the loop crashes
- **RT-16** (P0, OPEN): No venue reconciliation: fills, partial fills and ambiguous submissions are never resolved against the exchange
- **RT-21** (P0, OPEN): No working cancel, cancel-all or close-all path for users
- **RT-03** (P1, OPEN): Legacy deploy endpoint (the one that wins routing) starts bots that can never emit an order
- **RT-05** (P1, OPEN): Canonical live runtime (DAGEventLoop + LiveSignalPath) has no production caller
- **RT-08** (P1, OPEN): Bots live in per-process memory: stop requests can miss, crashes and restarts are not reflected, nothing is resumed
- **RT-19** (P1, OPEN): User kill switch and risk settings live in process memory and do not stop automated trading
- **RT-18** (P1, OPEN): Safety services report ACTIVE while doing nothing
- **PAPER-02** (P1, IN_PROGRESS): Manual paper orders fill at hardcoded reference prices or at a price the client supplies for a market order
- **SEC-02** (P1, OPEN): Razorpay webhook grants the plan named in payment notes without reconciling the order or amount
- **SEC-10** (P1, OPEN): Rate limits are keyed on the proxy's IP, so all users share buckets
- **CI-01** (P1, OPEN): Production deploys are not gated on tests, and the PR gate has never passed
- **CI-03** (P1, OPEN): HEAD cannot deploy; frontend deploys independently, so production front and back ends are different versions
- **INF-03** (P1, OPEN): Market-data service and background workers are not in the committed production deployment
- **OPS-01** (P1, OPEN): No alerting or error tracking configured in the committed production configuration
- **BILL-01** (P1, OPEN): Razorpay plan purchases never expire
- **RT-17** (P1, OPEN): Lost responses are recorded as rejections; strategy state rolls back and can re-enter
- **EXCH-02** (P1, OPEN): Stored exchange keys are not checked for withdrawal permission or IP restriction
- **DEP-01** (P1, OPEN): Production pins carry published advisories (starlette, PyJWT, cryptography, python-multipart, requests, aiosmtplib, protobuf) and axios in the frontend
- **PAPER-04** (P1, OPEN): Paper session loops run in the web process on the caller's one-hour JWT and die silently
- **AUD-02** (P1, OPEN): The order audit trail is never persisted durably; its own read path always returns empty
- **RT-25** (P1, OPEN): OrderStateMachine's Redis lock uses asyncio.run() inside async callers; the venue-reconciliation path would crash before updating any order
- **BILL-05** (P1, OPEN): Cancelling a subscription never calls Stripe; the card keeps being charged every period
- **SEC-15** (P1, OPEN): require_aal2 (MFA) is wired to only 2 of the endpoints its own security contract requires it on
- **RISK-02** (P1, OPEN): RiskSettings UI advertises three automated protection guards that no code path evaluates
- **EXT-01** (P1, OPEN): An independent, repo-internal remediation report (staging/REMEDIATION_REPORT.md, 10 phases, Oct 2026) exists, substantially overlaps this audit, and converges on the same NO-GO verdict

## Human-only / external actions
1. **SEC-01**: rotate the exposed production DB password; purge `scripts/run_schema_migration_prod.py` from git history (repo admin, history rewrite); verify no copies in CI logs/forks. Not performed; rotation unverified.
2. Apply migrations 033 (and 034 only after the live-deploy insert is moved to a service-role client) to staging/production and re-run the privilege-escalation repro with a real user JWT.
3. Provide exchange sandbox credentials for RT-16/RT-21 verification; provision MDS + workers (INF-03); alerting (OPS-01).
4. Billing-provider access for SEC-02, BILL-01, BILL-05.

## Behaviour changes to be aware of
- Manual paper MARKET orders are refused (409 PAPER_MARKET_DATA_UNAVAILABLE) until `get_fresh_market_price` is wired to the MDS feed (PAPER-02).
- Backtests return an explicit MarketDataUnavailable error instead of synthetic results (BT-01).
- Live orders require mode=live + enable flag + tenant allowlist (RT-15); /strategies/{id}/deploy-pipeline returns 410 (RT-22); /api/execution/signal refuses without measured equity (SEC-14).
