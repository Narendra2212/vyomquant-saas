# PHASE 11A — LAUNCH GATE REGISTER

**Revision:** HEAD `074b772a` on `main`, working tree dirty.
**Date:** 2026-10-10.
**Scope:** restricted retail crypto SaaS launch. Three separate launch tracks: **paper**,
**live**, **production**. Every gate links to evidence; unknown stays unknown.

Severity: **P0** (money/safety, blocks any launch) · **P1** (blocks the stated track) ·
**P2** (should fix) · **INFO**.

---

## A. PAPER-TRADING LAUNCH GATES

| Gate | Sev | State | Evidence | Remediation / closure criterion |
|---|---|---|---|---|
| **PAPER-SANDBOX-E2E** — a paper deployment's order path verified against a venue sandbox end-to-end | P1 | **BLOCKED (no credential)** | `PHASE11A_SANDBOX_CERTIFICATION_REPORT.md §1,§4`; lifecycle suite skips naming `AERORA_SANDBOX_*` | Supply testnet/demo creds for binance/bybit/okx + local Redis; run `test_sandbox_order_lifecycle.py`; reach `SANDBOX_ORDER_FLOW_VERIFIED` for ≥1 venue |
| **RECON-01** — venue↔internal order/fill reconciliation operational | P1 | **OPEN** (service not started; fail-safe hardened; watchdog covers stale-EXECUTING) | code-verified: `main.py` starts `OrderWatchdog`+`ReconciliationScheduler`, NOT `ExchangeReconciliationService`; `exchange_reconciliation.py` refuses on no-client, re-raises on error, withholds outcome when no `fetch_order`; `order_watchdog.recover_unsubmitted_executions` adjudicates (never resubmits). `tests/test_p10d_exchange_reconciliation_fails_safely.py` + `tests/test_exchange_reconciliation_startup_assessment.py` green | See §RECON-01 below. Not safe to start as-is (no leader election → N workers run N copies). Needs: (a) a running exchange client per tenant, (b) single-leader guard, (c) sandbox evidence. |
| **PAPER-ISOLATION** — paper fills cannot route to a live venue | P1 | **MET by construction** (not venue-observed) | Phase 9/10 `PAPER_TRADING_CERTIFICATION.md`; import-graph/feature-flag/state-machine evidence | Venue-sandbox observation would raise this from "by construction" to "observed" — needs PAPER-SANDBOX-E2E |
| **KILL-SWITCH** — re-read at venue boundary, fail-closed on Redis loss | P0 | **MET** (Phase 9) | `enforce_kill_switch()` at `exchange_executor.place_order`; durable audit (migration 028) | — |
| **QUOTA-HARD-LIMIT** — plan/account limits cannot be exceeded concurrently | P1 | **MET (logic) / UNVERIFIED (real Redis)** | `test_quota01_metered_reservation_is_hard.py` 6 passed/1 skipped; increment-first-revert on Redis `incrby/decrby`; fail-closed outside dev | Run the real-Redis variant (`AERORA_TEST_REDIS_URL`) to move from logic-verified to integration-verified |
| **CONN-01** — credential rotation evicts the pooled client | P2 | **MET (fixed Phase 10)** | `test_conn01_rotation_evicts_pooled_client.py` 4 passed | — |
| **FEE-02** — third-asset fee recorded, not lost | P2 | **MET (fixed Phase 10)** | `test_fee_third_asset_pct_is_nullable_postgres.py` 4 passed; migration 031 | — |
| **MONEY-08** — referral payout writes real columns | P2 | **MET (fixed Phase 10)** | `test_referral_payout_schema_contract_postgres.py` 5 passed | — |
| **REFERRAL-PAYOUT-SCOPE** — payouts excluded from initial release and fail safe | P1 | **MET (excluded, fail-safe)** | no page calls `createPayout`/`getPayouts` (only the API client stub exists); backend `create_payout_request` only inserts `status='pending'` + debits approved wallet (rolls back on failure); NO payment-initiation code exists | Keep excluded. See §7 decision. |
| **TENANT-01** — tenant isolation | P1 (for prod), P2 (for restricted paper) | **OPEN** (architectural residuals) | code-verified: RLS enabled-not-forced + owner-role ORM connection → RLS bypassed on ORM path; `tenant_db_context.py` exists but unwired; billing webhook service-role write on payload `user_id` gated by IP+signature | See §TENANT-01 below. Credential-vault isolation has negative-control coverage (`test_tenant01_credential_vault_isolation.py` 4 passed); breadth sweep 0 leaks. |

## B. LIVE-TRADING GATES (status: OFF / NOT CERTIFIED — unchanged)

| Gate | Sev | State | Evidence |
|---|---|---|---|
| **LIVE-DISABLE** — live order submission off | P0 | **OFF** | No live trading enabled in any phase; venue boundary refuses without a signed authorization + proven sandbox/kill-switch |
| **SANDBOX-ORDER-FLOW** per routable venue | P0 | **BLOCKED (no creds)** | §A PAPER-SANDBOX-E2E |
| **RECON-01 operational** | P0 | **OPEN** | §A |
| **EXCH-01 bitget** | P0 | **MITIGATED** (blocked from routing; test red by upstream) | sandbox report §3 |
| Live classification | — | **TRADING LIVE — OFF / NOT CERTIFIED** | formal status retained |

## C. PRODUCTION GATES (status: NOT CLASSIFIED)

| Gate | Sev | State | Evidence |
|---|---|---|---|
| **PROD-DSN-VERIFY** — gates verified against production schema | P0 | **NOT DONE** | No production DSN connected in any phase; all evidence is LOCAL PostgreSQL 16.2 |
| **TENANT-01 FORCE RLS** | P0 | **OPEN** | §TENANT-01 |
| **MIGRATION-STATE** — 026–031 applied in production | P0 | **NOT DONE** | numbered migrations applied locally only |

---

## RECON-01 — detail

**Exploitability of the current (unstarted) state:** LOW for *paper* (no live venue), because the
dangerous behaviours are already prevented: `ExchangeReconciliationService` **refuses** when it has
no exchange client (would not report "zero open orders"), **re-raises** on any exchange error
(does not resolve orders on a failed fetch), and **withholds** any outcome when the client cannot
`fetch_order` (does not assume FILLED or CANCELLED from absence). The `OrderWatchdog` IS running
and adjudicates stale EXECUTING rows (TRADE-07) without ever resubmitting, leaving ambiguous rows
UNKNOWN + alerting.

**Why it is NOT safe to simply start `ExchangeReconciliationService`:**
1. No exchange client is wired to it in the lifespan (`get_reconciliation_service(db)` defaults
   `exchange_client=None`), so it would immediately raise — correct, but it does nothing useful.
2. **No leader election / single-flight guard** around it. It is a plain asyncio task; in a
   multi-worker deployment every pod would run its own reconciliation loop over all tenants,
   duplicating work and racing on `_update_sync_timestamp`/state transitions. Leader election
   exists only for the execution coordinator (`distributed_execution/`), not here.
3. No tenant enumeration source is wired (it reconciles per `tenant_id`; nothing feeds it the
   tenant set).

**Minimum to close for live:** wire a per-tenant exchange client, add a single-leader guard
(reuse `distributed_execution` leader election), feed a tenant enumeration, and verify end-to-end
against a sandbox. **Closure criterion:** `SANDBOX_RECONCILIATION_VERIFIED` for ≥1 venue with a
leader-elected single runner proven under a 2-process test.

**Does it block the restricted PAPER launch?** No — paper has no live venue to reconcile against,
the stale-recovery watchdog is running and fail-safe, and the service's dangerous paths are
disarmed. It blocks LIVE.

## TENANT-01 — detail

| Risk | Exploitability | Impact | Blocks restricted paper? | Blocks production? |
|---|---|---|---|---|
| RLS enabled-not-forced + owner-role ORM connection | Requires a code path that queries without a tenant filter; the app's own queries filter by `user_id`/`tenant_id` in the WHERE clause | cross-tenant read if any query omits its filter | No (defence-in-depth gap, not an open leak; breadth sweep shows 0 leaks via PostgREST) | **Yes** — a production launch should FORCE RLS or route the ORM through a non-owner role |
| Billing-webhook service-role write on payload `user_id` | Requires forging a signed webhook (needs the shared secret) OR compromising checkout metadata | wrong entitlement granted | No (IP allowlist + HMAC signature + timestamp + idempotency lock) | Should be reviewed before production |
| Credential-vault Python-only tenant check | — | — | No (negative-control tested, denies cross-tenant) | — |

**Minimum defensible remediation (production track):** wire `tenant_db_context.py` (set
`app.current_tenant_id` GUC per request) OR connect the ORM as a non-owner role, then enable
`FORCE ROW LEVEL SECURITY`. This is a focused change with a focused test; **not** a broad rewrite.
Out of Phase 11A's safe-change scope (touches the DB connection role + every request) — flagged as
the top production blocker. **Closure criterion:** a PostgreSQL test proving a tenant-filtered
session cannot read another tenant's row through the ORM path with RLS forced.

---

## Rate-limiter concurrency flake (not a gate — a test-reliability note)

`tests/test_rate_limiter_concurrency.py::test_different_endpoints_run_concurrently`:
- **Full-suite (Phase 10 PG lane, under heavy load):** FAILED — `assert 0.937 < 0.4` (wall-clock).
- **Isolated (this phase, fresh):** PASSED (19.74s). Phase 10: 3/3 passes in isolation.
- **Disposition:** load-induced timing flake, not a product defect, not a launch gate. Owner: the
  rate-limiter test (widen/adaptive budget). Reported separately per the brief.
