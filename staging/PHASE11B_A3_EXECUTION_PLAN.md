# PHASE 11B-A3 — EXECUTION PLAN

Sequenced by **dependency**, not by severity. Each workstream states its file manifest,
acceptance criteria, approval requirement and owner. **No work in this plan has started.**

Companion documents: `PHASE11B_A3_BASELINE_AND_OWNERSHIP.md` (evidence),
`PHASE11B_A3_BLOCKER_REGISTER.md` (findings and contradictions).

---

## 0. The dependency that governs everything

```
          ┌─ A3-04 register reconciliation ──────────────► unblocks trustworthy reporting
          │
W1 ───────┼─ test lane (AERORA_TEST_DATABASE_URL + AERORA_TEST_REDIS_URL)
(no        │       └──► P11-U01..U07 (7 unverified critical controls)
 approval  │       └──► D5′ real-Redis (QUOTA-01)
 needed)   │       └──► P11-B09 / TENANT-01 RLS verification
          │
W2 ───────┴─ CI integrity: A3-01 test gate + A3-02 triage the 39 failures
                  └──► any later claim that "CI is green" means anything

W3 ── commit plan (A3-03)  ──► CI can see the remediation at all
                  └──► D5′ verification, PG lane in CI, reviewable migrations

W4 ── risk at the gateway (P11-B03)        ── independent of venues
W5 ── ops (P11-B06, B07)                   ── independent of venues
W6 ── RECON-01 leader election             ── independent of venues
W7 ── venue certification (P11-B01/B02)    ── BLOCKED on credentials
W8 ── production (P11-B10, TENANT-01 FORCE RLS) ── BLOCKED on authorization
```

**The single cheapest, highest-leverage item is W1.** Phase 11 measures it at ~1 engineer-day
and it unblocks seven critical controls plus D5′. It needs **no** approval beyond starting a
local service, and it requires no venue, no production access and no code change.

---

## W1 — Stand up the verification lane  ◀ RECOMMENDED FIRST

**Why first:** 38 PostgreSQL-gated test files (~632 tests) and 3 real-Redis-gated files hold
the proofs for `P11-U01..U07` and `QUOTA-01`/D5′. All skip today. A skip is not a pass. Nothing
else on this list changes what is *known* as cheaply.

| | |
|---|---|
| **File manifest** | **None.** This is environment setup plus test execution. No source file is edited. |
| **Actions** | (a) start a loopback PostgreSQL (a bootstrap cluster is **already reachable** at `127.0.0.1:51805`), provision via `scripts/provision_database.py --apply --create-substrate`; (b) start a loopback Redis on a non-default port; (c) export `AERORA_TEST_DATABASE_URL` and `AERORA_TEST_REDIS_URL`; (d) run the two lanes and classify every failure. |
| **Approval needed** | **Yes, narrow:** authorization to obtain/start a local Redis binary (none is installed; `staging/redis/` is absent though the runbook documents the path). PostgreSQL needs none — it is already running. |
| **Acceptance criteria** | 1. `alembic heads` returns exactly **one** head (closes **A3-05**). 2. The ~632 PG tests **execute**; every failure classified with a named owner. 3. The 3 real-Redis tests **execute and pass** (closes **D5′** locally). 4. `P11-U01..U07` each move to VERIFIED or FAILED — **never** left skipped. 5. Counts recorded per test; no skip reported as a pass. |
| **Expected outcome** | Genuinely unknown, and that is the finding — these tests have never run. Phase 11 flags that material defects here would invalidate its own 15-day feasibility estimate. |
| **Owner** | Platform / A3 |
| **Risk** | Low. Loopback only, non-production, no migration against any shared target. |

---

## W2 — CI integrity (A3-01, A3-02)

**Why second:** until the release gate tests correctness and the 39 failures are triaged, no
CI result — including D5′'s — can be read as evidence.

| | |
|---|---|
| **File manifest** | `.github/workflows/01-pr-check.yml` (add a `push` trigger **or** add a test-result check), `.github/workflows/03-deploy.yml` (consult `01 PR Check` in `pre-deployment-validation`). **Both shared.** No other file. |
| **Approval needed** | **Yes.** `01-pr-check.yml` already carries two uncommitted workstreams' changes (D5′ + `provisioning-and-isolation`); it needs **one owner** before any edit. Changing the deploy gate is a release-process change. |
| **Acceptance criteria** | 1. A commit pushed to `main` with a failing suite does **not** reach `ecs-deployment` — demonstrated, not asserted. 2. The 39 `Unit & Integration Tests` failures each have a named owner and a classification. 3. The existing fail-closed `05 Security` gate is **unchanged**. |
| **Owner** | SRE + coordinator |
| **Risk** | Medium — touches the release path. Must not weaken the security gate. |

---

## W3 — Commit plan (A3-03)

| | |
|---|---|
| **File manifest** | A per-workstream staging plan. **Explicitly not `git add -A`.** |
| **Approval needed** | **Yes — commit/push authorization, per workstream.** |
| **Acceptance criteria** | Each commit is one workstream's files; no file owned by another tab is swept in; the 11 untracked migrations land as a reviewable unit; `tests/conftest.py` and `01-pr-check.yml` are handled by their single owner. |
| **Owner** | Coordinator |

---

## W4 — Risk at the order gateway (P11-B03)

| | |
|---|---|
| **File manifest** | `backend_app/core/execution_engine.py` (daily-loss + drawdown at the gateway), `backend_app/core/risk_engine.py`, new `tests/test_p11b03_*.py`. |
| **Serialisation** | **Mandatory.** Phase 11 records that `P11-B03` and `P11-B04` both edit `_execute_trade_internal` and must not run concurrently. One owner for `execution_engine.py`. |
| **Approval needed** | Yes — changes trading behaviour (orders that previously passed will be refused). |
| **Acceptance criteria** | An account past its configured daily-loss limit cannot get an order authorized; `check_drawdown` no longer returns `(True,'OK')` when `peak_equity == 0`; both fail **closed** on unreadable equity. |
| **Owner** | Risk |

---

## W5 — Operational blockers (P11-B06, P11-B07)

| | |
|---|---|
| **File manifest** | `terraform/` (health check targets, alarms, SNS, backup), `backend_app/core/readiness.py` wiring if the probe path is wrong. |
| **Approval needed** | **Yes** — infrastructure. `P11-B06` is measured at 0.5 d and is the cheapest critical blocker in the corpus. |
| **Acceptance criteria** | A task with a broken required dependency is removed from the ALB rotation (demonstrated against a non-production target); at least one alarm fires on a synthetic condition; a restore drill completes with recorded RTO/RPO. |
| **Owner** | SRE |
| **Constraint** | **No destructive recovery test against production.** |

---

## W6 — RECON-01 single-owner execution

| | |
|---|---|
| **File manifest** | `backend_app/backend/exchange_reconciliation.py`, `backend_app/main.py` (lifespan — **shared**, coordinate), reuse of `distributed_execution/` leader election, new 2-process test. |
| **Approval needed** | Yes — starts a background service. **Do not start an unproven service in production.** |
| **Acceptance criteria** | Under two competing worker processes, **exactly one** performs the reconciliation action; the loop is idempotent; a missing order resolves to UNKNOWN rather than being resolved; restart recovery proven; outcomes auditable. Closure for live additionally needs `SANDBOX_RECONCILIATION_VERIFIED` on ≥1 venue. |
| **Owner** | Reconciliation |
| **Note** | The fail-safe hardening is already in place (refuses with no client, re-raises on error, withholds on no `fetch_order`) — so the current unstarted state is **safe**, just not useful. |

---

## W7 — Venue certification (P11-B01, P11-B02) — BLOCKED

| | |
|---|---|
| **Blocked on** | Testnet credentials under `AERORA_SANDBOX_<VENUE>_API_KEY` / `_SECRET` / `_PASSPHRASE`, **funded**, for binance and bybit; plus a reachable Redis (W1) because `enforce_kill_switch()` fails closed and refuses every order without it. |
| **Order of execution** | **Scenario 8 (duplicate `clientOrderId`) first, per venue** — it is the platform's only defence against a retry becoming a second real order, and `TRADE-08`'s 5-minute bucket compounds it. Then 1–4, 5a, 6a–6c, 7, 9b, 10–12. Record 5b and 9a as **missing evidence**. |
| **Approval needed** | **Yes** — credential supply. Testnet/demo only; the harness refuses if any of 19 production-shaped credential names is merely *present*. |
| **Acceptance criteria** | Per venue: the venue **echoes our id back**, and a second submission under the same id is **refused**. A pass on binance says nothing about bybit. |
| **Excluded** | **bitget** (sandbox routes to production — keep blocked). **okx** (ccxt 4.3.92 `load_markets` broken; needs a dependency upgrade with its own regression, and re-running the sandbox-mechanism probe because `SANDBOX_MECHANISMS` is pinned to 4.3.92 behaviour). |
| **Owner** | Exchange |

---

## W8 — Production and tenant isolation (P11-B10, TENANT-01) — BLOCKED

| | |
|---|---|
| **Blocked on** | Authorized **read-only** production access for schema comparison; a decision on the ORM connection role. |
| **The core finding** | The application connects as **`postgres`, the table owner**, so RLS is **inert on the ORM path** regardless of policies. `FORCE ROW LEVEL SECURITY` is on **zero** tables. `tenant_db_context.py` exists and is **unwired**. |
| **Acceptance criteria** | A PostgreSQL test proving a tenant-scoped session **cannot** read another tenant's row through the ORM path with RLS forced — and the same test proving allowed access still works. Adversarial cross-tenant tests for both allow and deny, on isolated data. **Must not be validated using a privileged role.** |
| **Approval needed** | Yes — changes the DB connection role and every request path. |
| **Owner** | Security + DB |
| **Constraint** | Do not query production customer records without authorization; `D3` (over-limit population) stays **BLOCKED** until then. |

---

## Deliberately deferred

| Item | Why |
|---|---|
| OKX certification | Requires a ccxt upgrade; binance+bybit are the recommended initial allowlist |
| Per-venue rate-limit conformance / `Retry-After` | Not on the critical path for a restricted launch |
| `MKT-01` partial refunds | A **product requirements** decision, not an oversight |
| Referral payouts | Keep **excluded and disabled**; no payment-initiation code exists, and it must stay that way until the full lifecycle is implemented. **Never initiate a real transfer as a test.** |
| `HardQuotaEnforcer` revival | Explicitly the wrong fix — it would add a fail-open path beside a working fail-closed one |

---

## Standing constraints for every workstream

1. Live trading **OFF**. Never enabled to test a hypothesis.
2. No production database, Redis, exchange endpoint, or payment transfer endpoint.
3. No `git add -A`; never stage another workstream's files.
4. No commit, push, PR, workflow dispatch, or infrastructure change without approval **for
   that specific action**.
5. Never weaken an assertion, convert a failure to a skip, or report a skip as a pass.
6. Record every pass, failure, skip, xfail and unexecuted test.
7. Keep the five readiness determinations **separate**: application release · paper trading ·
   exchange sandbox per venue · production infrastructure · live trading.
8. A sandbox mechanism check is **not** venue certification. A unit test over a fake client is
   **not** evidence of venue or production behaviour.
