# PHASE 11A — NEXT ACTIONS

Ordered by what unblocks the most launch-critical evidence first. Each lists exact files,
tests, and the approvals / infrastructure / credentials required. Nothing below has been done
in this phase where it needs an approval, a credential, or infrastructure.

---

## 1. Produce venue-sandbox order-flow evidence (the top paper-launch blocker)

**Why first:** it is the single gate that turns three venues from `OFFLINE_MECHANISM_VERIFIED`
into `SANDBOX_ORDER_FLOW_VERIFIED`, and it is the one piece of evidence no amount of code can
substitute for (per the brief: a local/CI test cannot stand in for venue-sandbox execution).

**Requires (approval + credentials + infra):**
- Testnet/demo API keys for **binance**, **bybit**, **okx**, exported as
  `AERORA_SANDBOX_<VENUE>_API_KEY` / `_SECRET` (okx also `_PASSPHRASE`). Testnet/demo only —
  never production keys (the harness refuses 19 production-shaped names).
- Each sandbox account **funded with testnet assets**.
- A reachable **local Redis** (the kill switch fails closed without it, so every order would be
  refused).

**Exact command (one venue per run):**
```powershell
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'; $env:AERORA_SANDBOX_VENUE='binance'
$env:AERORA_SANDBOX_BINANCE_API_KEY='...'; $env:AERORA_SANDBOX_BINANCE_SECRET='...'
python -m pytest tests/exchange_sandbox/test_sandbox_order_lifecycle.py -q
```
**Files/tests:** `tests/exchange_sandbox/test_sandbox_order_lifecycle.py`,
`tests/exchange_sandbox/harness.py` (no code change needed). Run scenario 8 (duplicate
clientOrderId refused by the venue) first — it is the only thing proving a retry cannot become a
second real order.

**Evidence needed to close:** a green lifecycle run for ≥1 venue, with scenario 8 passing,
recorded in `PHASE11A_SANDBOX_CERTIFICATION_REPORT.md` §2.

---

## 2. Make exchange reconciliation operational and safe (RECON-01)

**Why second:** without it, a live (and a credible paper-to-live) deployment has no running
control comparing internal order/fill state against the venue. The dangerous behaviours are
already disarmed (fail-safe refusal/withhold), but the service does nothing because it is not
started and has no leader guard.

**Exact files to change (requires architecture approval — touches startup + multi-worker
coordination):**
- `backend_app/main.py` lifespan — start a reconciliation runner with a **single-leader** guard.
- `backend_app/backend/exchange_reconciliation.py` — accept a per-tenant exchange client and a
  tenant enumeration source; it already fails safe on no-client/error/no-fetch_order.
- Reuse the existing leader election in `backend_app/backend/distributed_execution/`
  (`LeaderElectionManager`) rather than inventing a second one.

**Focused tests:** a 2-process test proving exactly one runner reconciles (no duplicate state
transitions); extend `tests/test_exchange_reconciliation_startup_assessment.py`; a sandbox
reconciliation scenario once credentials exist (scenario 12).

**Evidence needed to close:** `SANDBOX_RECONCILIATION_VERIFIED` for ≥1 venue under a
leader-elected single runner, proven under a multi-process test.

**Safety:** do NOT start the service without the leader guard — a multi-worker deployment would
run N reconciliation loops over all tenants. This is why it is NOT started in this phase.

---

## 3. Verify the quota hard-limit against a real multi-process Redis (QUOTA-01)

**Why third:** the hard-limit guarantee rests on Redis `incrby`/`decrby` atomicity. The logic is
verified against the in-memory meter and is fail-closed outside dev, but the integration is
UNVERIFIED because no Redis server is available.

**Requires (infra):** a reachable Redis at `AERORA_TEST_REDIS_URL` (or `REDIS_URL`). Do NOT
install Redis/Docker without authorization.

**Exact command:**
```powershell
$env:AERORA_TEST_REDIS_URL='redis://127.0.0.1:6379'
python -m pytest tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis -q
```
**Files/tests:** `tests/test_quota01_metered_reservation_is_hard.py` (the real-Redis variant is
already written and skips when absent — no code change needed).

**Evidence needed to close:** the real-Redis concurrency variant passes (N racers, exactly K
admitted) against an out-of-process atomic counter.

---

## Secondary (not launch-blocking for restricted paper, blocking for production)

### TENANT-01 — force RLS on the ORM path (production blocker)
- **Files:** wire `backend_app/core/tenant_db_context.py` into the request DB dependency (set
  `app.current_tenant_id` GUC per request) **or** connect the ORM as a non-owner role, then add a
  migration issuing `FORCE ROW LEVEL SECURITY` on the tenant tables.
- **Approval required:** yes — this changes the DB connection role and every request's session,
  and a migration. High blast radius; out of Phase 11A's safe-change scope.
- **Test:** a PostgreSQL test proving a tenant-scoped session cannot read another tenant's row
  through the ORM with RLS forced.

### Rate-limiter concurrency flake
- **File/test:** `tests/test_rate_limiter_concurrency.py::test_different_endpoints_run_concurrently`
  — make the wall-clock budget load-adaptive (owner: rate-limiter test). Not a launch gate.

---

## Required approvals summary

| Action | Approval / resource |
|---|---|
| 1. Sandbox E2E | Testnet/demo credentials (3 venues) + local Redis |
| 2. RECON-01 wiring | Architecture approval (startup + leader election); sandbox creds for E2E |
| 3. Quota real-Redis | A reachable Redis (no install without authorization) |
| TENANT-01 FORCE RLS | Approval for a DB-role/migration change (production track) |
