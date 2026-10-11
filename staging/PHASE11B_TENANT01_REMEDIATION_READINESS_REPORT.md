# Phase 11B — TENANT-01 ownership coordination and remediation readiness

**This is a READ-ONLY analysis and handoff document. No source file was edited, no role created,
no grant changed, no migration applied or generated, no database connected to, nothing staged,
committed or pushed.**

**Required final status, carried unchanged:**
**TENANT-01 OPEN · CD-4 / CD-5 PROPOSED (not accepted into any register) · PostgreSQL lane
UNEXECUTED · QUOTA-01 OPEN · live trading OFF / NOT CERTIFIED.**

Predecessor: `staging/PHASE11B_TENANT01_AUDIT_REPORT.md`. This document resolves that report's
ownership blockers, settles three of its six unverified risks from source, and adds one new
finding.

---

## 1. Fresh Git and ownership baseline

Measured now, not carried over. The raw `.git/index` SHA-256 is deliberately **not** used as the
invariant — it changes on any `git status` stat refresh. Content digests are used instead.

| property | value |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| branch / upstream | `main` → `origin/main` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (identical, 0 ahead / 0 behind) |
| **index tree object** (`git write-tree`) | **`6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`** |
| **staged diff SHA-256** (first 32) | **`814B8AB272E2C845316D29C90BFE4822`** |
| staged paths | 22 |
| unstaged tracked paths | 100 |
| untracked paths | 3325 (was 3274 in the prior audit — other sessions are writing) |

The index tree and staged-diff digest are the two invariants to re-check after this task. Both are
restated in §8.

### 1.1 Staged vs unstaged, measured separately

| file | staged (+/−) | unstaged (+/−) | needed by the fix |
|---|---|---|---|
| `backend_app/core/tenant_db_context.py` | **300 / 0** | none | **step 1 — the fix itself** |
| `scripts/verify_rls_enforcement.py` | **572 / 0** | none | step 2 verification |
| `backend_app/core/dependencies.py` | 35 / 1 | none | step 1 |
| `backend_app/routers/auth.py` | 11 / 1 | adjacent |
| `backend_app/routers/exchange.py` | **268 / 15** | **154 / 14** | adjacent (**both** staged and unstaged) |
| `backend_app/core/database_pool.py` | none | **none — CLEAN** | step 1 / step 2 |
| `backend_app/main.py` | none | **289 / 36** | step 3 |
| `backend_app/core/config.py` | none | **401 / 33** | step 2 |
| `backend_app/routers/billing.py` | none | **700 / 40** | steps 5–7 |

**`backend_app/core/database_pool.py` is the only file in the whole remediation surface with no
staged and no unstaged modification.** It is also the single real engine/session factory. That
makes it the one viable insertion point that does not require another workstream's sign-off.

### 1.2 Ownership attribution

Owners are attributed from artifact co-location and content, not assumed. **Approval has not been
obtained from any of them; the existence of staged code is explicitly not treated as approval.**

| # | owner (workstream signature) | evidence | files held |
|---|---|---|---|
| **O1** | **Tenancy / security-architecture workstream** | ships `docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md` (**Status: Accepted**), `ADR-0007-per-tenant-credential-encryption-keys.md`, `docs/saas-architecture/tenant-isolation-matrix.md`, `docs/security-reviews/ALGO22_Quant_Platform_Review_10032026.md`; ADR-0006 references `tenant_db_context` 6 times | **`core/tenant_db_context.py`**, `scripts/verify_rls.py`, `scripts/verify_rls_enforcement.py`, `tests/test_tenant_db_context{,_postgres}.py`, `tests/test_verify_rls_script.py` |
| **O2** | **Auth / credential-vault workstream** (same staged batch as O1, plausibly the same team) | `tests/test_sc3_aal_claim_propagation.py` staged alongside; ADR-0007 is per-tenant credential keys | `core/dependencies.py`, `routers/auth.py`, `routers/exchange.py` |
| **O3** | **Unstaged backend workstream** (no staged component at all) | large coordinated unstaged diffs across billing/main/config/execution_engine | `routers/billing.py`, `main.py`, `core/config.py` |
| **O4** | **DB provisioning / migration owner** | `scripts/provision_database.py`, `DATABASE_PROVISIONING_RUNBOOK.md`, `backend_app/migrations/PROVISIONING_ORDER.md` | role creation, grants, RLS migrations |
| **O5** | **PostgreSQL lane owner — Phase 11B workstreams 1B / 1C / A9** | `staging/PHASE11B_W1B_PG_TEST_SAFETY_MATRIX.md`, `PHASE11B_W1C_RECOVERY_REPORT.md`, `PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md`, `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` | the **shared cluster** `staging/pgdata_r1`, `staging/pg_dsn.txt` |
| **O6** | **Test owner for real-PG isolation coverage** | `tests/test_tenant01_cross_tenant_matrix_postgres.py`, `test_cross_tenant_read_sweep_postgres.py`, `test_billing_data_isolation_postgres.py` | the PG isolation suites |

### 1.3 Decisions required — none of these can be inferred

| # | decision | owner | blocks |
|---|---|---|---|
| D1 | Who wires the GUC: O1 inside their staged module, or this workstream inside the clean `database_pool.py`? | **O1** | step 1 |
| D2 | May `database_pool.py` be modified by a workstream other than O1/O3? | **O1 + O3** | step 1 fallback |
| D3 | Authorize provisioning a non-owner, non-superuser, non-`BYPASSRLS` application login + a second DSN | **O4** | step 2 |
| D4 | Authorize gating `Base.metadata.create_all` at `main.py:795` | **O3** | step 3 |
| D5 | Authorize a `FORCE ROW LEVEL SECURITY` migration, table by table | **O4** | step 4 |
| D6 | Authorize editing `billing.py` for CD-3 / CD-4 / CD-5 | **O3** | steps 5–7 |
| D7 | Authorize a PostgreSQL lane run, or grant use of the shared cluster | **O5** | step 6 / all closure evidence |
| D8 | Accept CD-4, CD-5 and the new CD-7 into the launch-gate register | register owner | finding status |

---

## 2. Verified tenant-context call-site inventory

### 2.1 The helper has zero production call sites — re-verified

Searched `backend_app/*.py` and `backend_app/**/*.py` for
`tenant_db_context|tenant_db_dependency|get_tenant_db|apply_tenant_context|tenant_db_session`,
excluding the module itself. **Result: zero matches.** Referenced only by
`tests/test_tenant_db_context.py`, `tests/test_tenant_db_context_postgres.py`,
`tests/test_tenant_isolation_rls_postgres.py`, `tests/test_tenant02_fee_records_rls_postgres.py`,
`tests/test_bandit_sast_gate_is_green.py`.

### 2.2 Every SQLAlchemy session entry point — and a correction to the audit's sizing

| entry point | count | where |
|---|---|---|
| `Depends(get_db)` in routers | **2, in one file** | `routers/billing.py` only |
| `db_pool.get_session()` / `get_db_context()` / `get_db_session()` / `SessionLocal()` | **35 across 16 modules** | `database_pool.py` 9, `database.py` 3, `dag_worker.py` 3, `task_recovery.py` 3, `execution_engine.py` 3, `main.py` 2, `exchange_websocket_listener.py` 2, `dag_task_queue.py` 2, `tenant_db_context.py` 2, `startup_recovery.py` 1, `consistency_checker.py` 1, `readiness.py` 1, `reconciliation_scheduler.py` 1, `safety_audit.py` 1, `routers/dag_tasks.py` 1, `routers/strategies.py` 1 |

**This materially re-sizes the work, in a favourable direction and an unfavourable one.** The HTTP
request surface on the ORM is tiny — **4 router-level sites in 3 files** (`billing.py` ×2,
`dag_tasks.py` ×1, `strategies.py` ×1). The bulk is **~14 worker / background / startup entry
points**, which is where the prior audit's CD-6 (`bot_ownership` `autocommit=True`, no explicit
transaction) bites: those call sites cannot adopt a transaction-scoped GUC without restructuring
their transaction boundaries first.

**Consequence for sequencing:** step 1 should be scoped to the **request path only** and declared as
such. A blanket claim that "tenant context is wired" would be false while 14 worker sites remain
unwired, and step 4 (`FORCE`) must not proceed on tables those workers write.

---

## 3. Current application and migration role design

### 3.1 What exists today — one role, no separation

* **One role only:** whatever is embedded in `DATABASE_URL` (`database_pool.py:138`), with a
  hardcoded `postgres` fallback at `:155` and the `.env.example:17` template naming the Supabase
  `postgres` superuser.
* **No `SET ROLE` / `SET LOCAL ROLE` anywhere** in the repository.
* **No second DSN.** The only alternates are `AERORA_TEST_DATABASE_URL` (test gate),
  `PROVISION_DATABASE_URL` (CI) and `SUPABASE_DB_URL` (the `https://` rewrite fallback) — in CI all
  are `postgresql://postgres:postgres@localhost:5432/postgres`.
* **Role provisioning is `anon` / `authenticated` / `service_role` only** —
  `scripts/provision_database.py:226,229,232` — **all `NOLOGIN`**, so none can be a SQLAlchemy login
  role, and `service_role` is created **`BYPASSRLS`** by design.
* **`terraform/` manages no PostgreSQL roles at all.**
* **`FORCE ROW LEVEL SECURITY` exists nowhere as an executable statement.** Re-verified across
  `backend_app/migrations/*.sql`, `backend_app/alembic/versions/*.py`, `migrations/*.sql`,
  `archived_migrations/**/*.sql`, `scripts/*.py`: the only six hits are **comments and docstrings**
  — `026_fee_records_row_level_security.sql:75`, `verify_rls.py:191`, and
  `verify_rls_enforcement.py:11,197,518,560`.

### 3.2 Proposed design (DESIGN ONLY — requires D3)

| | migration / owner role | application role |
|---|---|---|
| name | existing `postgres` (unchanged) | e.g. `aerora_app` |
| attributes | owner, may be superuser | **`NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE LOGIN`** |
| owns tables | yes | **no** — this is what makes `FORCE` meaningful |
| DSN | existing `DATABASE_URL` | **new** `APP_DATABASE_URL`, preferred by `database_pool.py` when set |
| grants | full | `SELECT/INSERT/UPDATE/DELETE` on tenant tables, `USAGE` on sequences, **no DDL** |
| used by | Alembic, `scripts/provision_database.py`, migrations | the app engine, workers |

Rationale for a **second DSN rather than `SET ROLE`**: `SET ROLE` is reversible by the session
(`RESET ROLE`), so it is not a privilege boundary — a code path could step back up to the owner.
A separate login cannot. It is also compatible with the existing Supavisor rewrite
(`database_pool.py:158-178`), which only mangles the username for routing.

**Not done in this task:** no role created, no grant issued, no secret changed, no DSN written,
nothing connected to.

---

## 4. Ordered remediation plan, with dependencies and rollback

Ordering is not stylistic. **Step 4 before step 1 converts tenant reads into silently empty result
sets** — with the GUC unset, `current_setting('app.current_tenant_id', true)` is NULL, the policy
predicate is NULL rather than TRUE, and an enforced policy matches zero rows on `orders`,
`positions` and `fills`. Both `tenant_db_context.py:12-18` and `verify_rls_enforcement.py:560-570`
state this, and the auditor exits non-zero specifically to stop it.

| step | change | depends on | approval | rollback |
|---|---|---|---|---|
| **1** | Set the validated GUC inside an already-open transaction, on the **request path only** (4 router sites). Prefer O1's `tenant_db_dependency()`; fallback is a narrow hook in the clean `database_pool.py`. | — | **D1, D2** | per-router; revert the dependency. No schema change, no role change. Fully reversible. |
| **1b** | Extend to the ~14 worker/background sites, restructuring `bot_ownership`'s `autocommit=True` first (CD-6). | 1 | D1 | per-call-site |
| **2** | Non-owner application login + `APP_DATABASE_URL` preferred by `database_pool.py`. | — (independent of 1) | **D3 (high)** | revert the env var; the old DSN still works. Role can be dropped. |
| **3** | Gate `Base.metadata.create_all` (`main.py:795`) behind an explicit opt-in, default **off** in deployed environments; stop swallowing the failure into a warning. | — | **D4** | flip the flag on. **Caution:** test lanes and local dev may depend on implicit creation — must be inventoried before the default flips. |
| **4** | `FORCE ROW LEVEL SECURITY`, **one table at a time**, only after 1, 1b, 2, 3 verify green. | **1 + 1b + 2 + 3** | **D5 (high)** | `ALTER TABLE … NO FORCE ROW LEVEL SECURITY` per table. Low-risk to reverse, **high-risk to apply early**. |
| **5** | Webhook identity from a trusted provider→tenant mapping; at minimum apply the existing `_validate_uuid` to `user_id` and assert `subscription_id` belongs to `user_id`. Preserve signature verification, IP allowlist, Redis lock, provider-retry semantics. | — | **D6** | revert; mapping table is additive |
| **6** | Fix `_validate_webhook_timestamp` so it can reject (CD-4). | — | **D6** | revert |
| **7** | Durable webhook idempotency: a dedupe table or a unique index (CD-5, CD-7). | **CD-7 resolved first** | **D6 + D5 (migration)** | drop the constraint |

**Rollback considerations that are not symmetric.** Steps 1, 2, 5, 6 are cheaply reversible. Step 3
has a **forward** risk (test/dev lanes depending on implicit `create_all`) that must be inventoried
before the default changes. Step 4 is cheap to reverse but, applied out of order, produces a
**silent** failure — empty reads, not errors — which is the single most dangerous move available in
this plan.

**Smallest genuinely safe first step:** step 1 scoped to one read-only router, implemented through
O1's existing helper, with O1's approval (**D1**). Nothing else clears its approvals today.

---

## 5. Proposed finding records — NOT register edits

These are **drafts for the register owner (D8)**. They are not accepted, not inserted into any
register, and no launch-readiness number was touched.

### CD-4 (PROPOSED) — webhook timestamp / replay validation cannot reject anything

* **Severity (proposed):** P3 standalone; **P2** compounding CD-3 and CD-5.
* **Location:** `backend_app/routers/billing.py:515-546` (`_validate_webhook_timestamp`), called at
  `:2324` (Stripe) and `:2836` (Razorpay).
* **Mechanism — two independent reasons, each sufficient:**
  1. For Stripe it reads `request.headers.get("stripe-signature")` as the timestamp. That is the
     whole `t=…,v1=…` string, so `int(timestamp_header)` raises `ValueError`.
  2. Even on a successful parse, the `raise HTTPException(403, "Webhook timestamp too old…")` sits
     **inside the `try`** and is caught by the trailing bare `except Exception`, whose own comment
     reads *"Don't fail on timestamp issues but log for monitoring"*.
* **Therefore:** no replay window is enforced by this repository. Razorpay has **no timestamp
  binding at all** — its HMAC covers the body only, so a captured signed body is replayable
  indefinitely once the Redis key expires (see CD-5).
* **Verified:** by reading. **Inferred, not verified:** that Stripe's SDK applies its own ~300 s
  tolerance inside `construct_event` (library behaviour, not code in this repo).
* **Proposed smallest fix:** move the `raise` outside the `try`; parse Stripe's `t=` field rather
  than the whole header; add a Razorpay timestamp binding. **Preserve** signature verification and
  provider retry behaviour. No weakening, no skips.
* **Owner:** O3 (`billing.py`). **Approval:** D6.

### CD-5 (PROPOSED) — webhook idempotency is Redis-only, 24 h, over a per-process mock, with no durable constraint

* **Severity (proposed):** **P2 — financial duplication**, as much a money finding as a tenancy one.
* **Location:** lock `billing.py:2335-2349` (Stripe) / `:2841-2855` (Razorpay); completion
  `:2802-2812` / `~:3148-3158` with `ex=86400`.
* **What is correct:** the two-phase lock is crash-safe (60 s processing TTL), and the signature is
  verified **before** the lock is acquired, so an unsigned request cannot poison the key.
* **Mechanism:**
  1. **Redis-only.** `tests/conftest.py:13` sets `REDIS_URL = ""` unconditionally, so the default
     lane runs on `MockRedisClient` — *"a plain dict on a module-global instance — per process, so
     `SET NX` always succeeds. That is BUG-027"*
     (`tests/test_webhook_duplicate_delivery_real_redis.py:54-67`). With no Redis, or multiple pods,
     dedupe is a no-op.
  2. **24 h TTL.** A redelivery past it re-executes the handler.
  3. **No event id → no dedupe at all.** `if idempotency_key:` guards the entire mechanism.
  4. **No durable backstop — now confirmed, not suspected.** `migrations/006_reconcile_production_database.sql:796-811`
     creates `billing_invoices` with `id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text` and a
     **non-unique** `idx_billing_invoices_user ON (user_id)`. The only other statement touching the
     table in any migration is `ALTER TABLE … ENABLE ROW LEVEL SECURITY` (`:811`). **There is no
     unique constraint on `(provider, provider_payment_id)`, and no webhook-event dedupe table
     exists anywhere in the migration history** (searched `CREATE TABLE … webhook`,
     `webhook_events`, `processed_events`, `provider_event` — zero hits).
  5. **`increment_ml_addon` (`:1361`) is not idempotent.** The tier write naturally is.
* **Duplicate effects past the window:** a second `billing_invoices` row and a second
  `process_referral_commission` credit.
* **What already is durably idempotent, for contrast:** `referral_obligations` under
  `uq_referral_obligation_event UNIQUE (provider, provider_payment_id)`
  (`backend_app/migrations/029_referral_obligations.sql:151`, whose own comment says *"Only a unique
  constraint makes the check and the insert one atomic decision"*), and marketplace settlement under
  `uq_settlement_reference_reversal`. **029 is the model to copy.**
* **Proposed smallest fix:** mirror 029 — a unique constraint on the provider event identity — plus
  a dedupe row written in the same transaction as the financial effect. Provider event identifiers
  available today: Stripe `event["id"]` (`:2338`); Razorpay `payload["event_id"]` falling back to
  `payload.payload.payment.entity.id` (`:2843`). **Preserve** legitimate provider retries: a
  duplicate must return success, not an error.
* **Owner:** O3 + O4 (migration). **Approval:** D6 + D5.

### CD-7 (PROPOSED, **NEW in this task**) — the webhook's invoice INSERT and the declared schema disagree, and the mismatch is swallowed

* **Severity (proposed):** **P2 — undetermined direction** (see below).
* **Mechanism:** the declared `billing_invoices` schema
  (`migrations/006_reconcile_production_database.sql:796-809`) has exactly these columns:
  `id, user_id, amount, currency, status, invoice_url, invoice_pdf, period_start, period_end,
  created_at, updated_at`.
  The webhook inserts (`billing.py:2457-2468`, `:2670-2684`, `:2975-2986`):
  `user_id, provider, provider_payment_id, amount_usd, amount_inr, currency, status, plan,
  created_at`.
  **Five written columns — `provider`, `provider_payment_id`, `amount_usd`, `amount_inr`, `plan` —
  are not declared by any migration**, and `amount` is never written. No `ALTER TABLE …
  ADD COLUMN` for this table exists anywhere; the only `ALTER` is the RLS enable at `:811`.
* **Why it is not visibly broken:** all three inserts are wrapped in
  `except Exception: logger.warning(f"[BILLING] Could not insert billing_invoices…")`.
* **Two possible live states, and I cannot distinguish them without DB access:**
  * **(a)** the live Supabase table has these columns, created out-of-band by something not in this
    repository → the insert works, and **CD-5 stands as written**;
  * **(b)** the live table matches the migration → **every webhook invoice insert fails silently**,
    and there is no payment record at all. CD-5's "duplicate invoice" risk would then be moot and
    replaced by a worse one: **no** invoice.
* **Note:** `tests/test_orm_models_match_provisioned_schema.py` — the test written for exactly this
  defect class — does **not** cover `billing_invoices` (searched; zero hits). Neither does
  `scripts/verify_production_schema.py` or `verify_schema_drift.py`.
* **Resolution requires D7** (a database to query) and must be settled **before** CD-5's constraint
  is designed, since the constraint would target columns whose existence is unconfirmed.
* **Owner:** O3 + O4.

---

## 6. Real-PostgreSQL verification plan — prerequisites and exact commands

### 6.1 Lane status: UNEXECUTED, and not this workstream's to run

| fact | value |
|---|---|
| `AERORA_TEST_DATABASE_URL` | **unset** |
| `DATABASE_URL` | **unset** |
| `pgserver` python package | **available** |
| `psycopg2` | available |
| port 51805 | **LISTENING** |
| `staging/pg_dsn.txt` | `postgresql://postgres:@127.0.0.1:51805/postgres` |

The listening port is **not** a stale artifact as the prior audit assumed — it is the **shared
cluster `staging/pgdata_r1` owned by O5** (Phase 11B workstreams 1B / 1C / A9). Their own reports
record read-only discipline against it and that four suites require per-suite authorization
(`PHASE11B_W1B_PG_TEST_SAFETY_MATRIX.md`), and an authorization process already exists in
`PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`.

**I did not connect to it, did not start a cluster, and did not use the DSN** — both because the
brief forbids it and because it is another session's shared state. **Lane status: UNEXECUTED.**
Running it needs **D7**, and should route through O5's existing authorization path rather than a
parallel one.

### 6.2 Checks, mapped to evidence class

| # | claim | evidence class | new or existing |
|---|---|---|---|
| 1 | tenant A cannot read / update / delete tenant B's rows **through the ORM** | **real PG** | **new** |
| 2 | missing or malformed tenant context fails closed (0 rows; `TenantContextUnavailable`) | **real PG** + unit | partly `test_tenant_db_context_postgres.py` |
| 3 | the application role holds neither SUPERUSER nor BYPASSRLS, and owns no tenant table with FORCE off | **real PG** | **existing** — `scripts/verify_rls_enforcement.py` |
| 4 | pooled-connection tenant context does not leak between requests | **real PG** | **new** |
| 5 | webhook tenant identity derives from a trusted provider mapping | integration (mockable) | **new** |
| 6 | duplicate provider event cannot duplicate an invoice or a referral commission | **real PG** | **new** |
| 7 | existing same-tenant behaviour intact | unit + real PG | **existing** |

### 6.3 Exact commands

```powershell
# (a) Unit lane - no database. Must stay green. Safe to run today.
$env:PYTHONUTF8='1'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m pytest tests/test_tenant_db_context.py `
    tests/test_verify_rls_script.py `
    -q -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE

# (b) PostgreSQL lane - REQUIRES D7. Do NOT point at staging/pg_dsn.txt (O5's shared cluster).
#     Use a lane authorized by O5, or an isolated instance they approve.
$env:AERORA_TEST_DATABASE_URL = "<authorized isolated DSN>"
.venv\Scripts\python.exe -m pytest `
    tests/test_tenant_db_context_postgres.py `
    tests/test_tenant01_cross_tenant_matrix_postgres.py `
    tests/test_cross_tenant_read_sweep_postgres.py `
    tests/test_billing_data_isolation_postgres.py `
    tests/test_tenant02_fee_records_rls_postgres.py `
    tests/test_tenant_isolation_rls_postgres.py `
    -q -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfE

# (c) The role / ownership auditor. READ-ONLY by construction (SELECT + a rolled-back
#     SET LOCAL). Still requires D7 because it connects.
.venv\Scripts\python.exe scripts\verify_rls_enforcement.py
```

**Evidence rules.** A skipped PostgreSQL test is **not** a pass. Mocked tests are not PostgreSQL
evidence. Real-Redis evidence (`test_webhook_duplicate_delivery_real_redis.py`) is its own class and
does not substitute for either. No assertion may be weakened and no skip added to make any check
green.

### 6.4 Closure criteria for TENANT-01

1. `scripts/verify_rls_enforcement.py` exits **0** — no SUPERUSER, no BYPASSRLS, no owner-exempt
   table, no policy-coverage gap, no `tenant_id` type mismatch.
2. Check 1 passes on real PostgreSQL with RLS **forced**.
3. Check 2 passes — a context-less session reads **zero** rows, not all rows.
4. Check 4 passes — no GUC survives a pooled handover.
5. The worker/background sites (§2.2) are either wired or explicitly excluded from the forced tables.
6. Unit lane and PG lane both green, with pre-existing xfails/xpasses reported unchanged.

---

## 7. Approvals required before each implementation step

| step | approval | owner | status |
|---|---|---|---|
| 1 — wire GUC on request path | D1 (+ D2 if via `database_pool.py`) | O1 (+ O3) | **NOT OBTAINED** |
| 1b — wire workers | D1 | O1 | **NOT OBTAINED** |
| 2 — application role + second DSN | **D3** | O4 | **NOT OBTAINED** |
| 3 — gate `create_all` | **D4** | O3 | **NOT OBTAINED** |
| 4 — `FORCE RLS` migration | **D5** | O4 | **NOT OBTAINED** |
| 5/6/7 — billing CD-3 / CD-4 / CD-5 | **D6** (+ D5 for 7) | O3 (+ O4) | **NOT OBTAINED** |
| PG lane | **D7** | O5 | **NOT OBTAINED** |
| register acceptance of CD-4 / CD-5 / CD-7 | **D8** | register owner | **NOT OBTAINED** |

**No approval was inferred from the existence of staged code or from any prior report.**

---

## 8. Commands run, results, limitations, unresolved risks

### 8.1 What was executed — all read-only

| # | operation | result |
|---|---|---|
| 1 | `Test-Path` on the output path | did not exist → safe to create |
| 2 | `git rev-parse` HEAD / branch / upstream / `origin/main`; `git status -sb` | baseline §1 |
| 3 | `git write-tree`; SHA-256 of `git diff --cached` | index tree `6f40856c…`, staged digest `814B8AB2…` |
| 4 | `git diff --cached --numstat` and `git diff --numstat` per key file | §1.1 |
| 5 | grep `FORCE ROW LEVEL SECURITY` across all migrations + scripts | **6 hits, all comments/docstrings; zero executable statements** |
| 6 | grep `CREATE ROLE` / `ALTER ROLE` / `BYPASSRLS` / `NOSUPERUSER` | only `provision_database.py:226,229,232`; no app login role |
| 7 | grep tenant-context symbols in `backend_app/` excluding the module | **zero production call sites** |
| 8 | grep `Depends(get_db)` and session-factory callers, grouped by file | §2.2 — 2 router sites in 1 file; 35 sites in 16 modules |
| 9 | grep `billing_invoices` and `provider_payment_id` across all migrations | **no unique constraint; five written columns undeclared → CD-7** |
| 10 | grep for a webhook-event dedupe table | **none exists** |
| 11 | grep `referral_obligations` constraints | `uq_referral_obligation_event UNIQUE (provider, provider_payment_id)` — the model to copy |
| 12 | env check, `pgserver`/`psycopg2` availability, listening ports, `staging/pg_dsn.txt` | §6.1 — lane is O5's shared cluster |
| 13 | read `migrations/006…sql:793-814`; `docs/adr/ADR-0006` head; three O5 reports | §3, §1.2, §6.1 |

**No test was executed in this task** — the focused unit lane in §6.3(a) is *proposed*, not run,
because the brief's deliverable is the plan and every PG-backed check requires D7. Nothing was
staged, committed, pushed; no branch or worktree created; no role, grant, secret, migration or
database touched.

### 8.2 Exit-state invariants — unchanged from §1

| property | baseline | exit |
|---|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same |
| index tree | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | same |
| staged diff SHA-256 (32) | `814B8AB272E2C845316D29C90BFE4822` | same |
| staged / unstaged paths | 22 / 100 | same |

Only one file was created: this report, under `staging/`.

### 8.3 Risks resolved from the prior audit

| prior | was | now |
|---|---|---|
| UR-4 — `billing_invoices` uniqueness | unverified | **RESOLVED: no unique constraint exists** (and CD-7 found) |
| "stale DSN" | assumed stale | **CORRECTED: it is O5's live shared cluster** |
| Audit's request-path sizing | implied broad | **CORRECTED: 4 router sites; the bulk is ~14 worker sites** |

### 8.4 Unresolved risks

1. **UR-1 / UR-2 (carried):** the live role's `rolsuper` / `rolbypassrls`, and per-table
   `relowner` / `relforcerowsecurity`. Needs D7. **The entire TENANT-01 severity assessment rests
   on this and remains live-unverified.**
2. **CD-7 direction undetermined** — whether the webhook files invoices at all, or files them
   against undeclared columns. Needs D7. **Blocks CD-5's constraint design.**
3. **UR-3 (carried):** whether `transactional_execution_manager` / `tenant_rls_validator` are
   reachable from a request path.
4. **UR-5 (carried):** `marketplace/settlement_service.py` writes, taken from docstrings.
5. **New:** step 3's forward risk — which test and dev lanes depend on implicit
   `Base.metadata.create_all`. Not inventoried; must be before the default flips.
6. **New:** the ~14 worker entry points are the real cost of step 1b, and `bot_ownership`'s
   `autocommit=True` (CD-6) must be restructured before any of them can hold a transaction-scoped
   GUC.
7. **Coordination risk:** `routers/exchange.py` carries **both** staged (268/15) and unstaged
   (154/14) changes — two workstreams in one file. Anything touching it needs both O1/O2 and
   whoever owns the unstaged half.

---

## 9. Final status

| item | status |
|---|---|
| **TENANT-01** | **OPEN** — P1 production, P2 restricted paper. Mechanism code-verified; live state unverified. Not closed, not downgraded. |
| **CD-4** | **PROPOSED** — not accepted into any register |
| **CD-5** | **PROPOSED** — not accepted into any register; durable-constraint absence now confirmed |
| **CD-7** | **PROPOSED, NEW** — schema/writer disagreement masked by a swallowed exception; direction undetermined |
| **PostgreSQL lane** | **UNEXECUTED** — and owned by O5; requires D7 |
| **QUOTA-01** | **OPEN** — untouched by this task |
| **Live trading** | **OFF / NOT CERTIFIED** |
| Launch-readiness percentages | **unchanged** |
| Audit JSON mirrors | **unchanged** |
| Certification registers | **unchanged** |

Nothing in this document establishes exchange correctness, exactly-once execution, paper-trading
certification, or live-trading readiness.
