# Phase 11B — TENANT-01 fast coordination and implementation readiness

**READ-ONLY. No source file edited, no migration touched, no database connected to, no role or
grant changed, nothing staged, committed or pushed. One new artifact: this report.**

**Final status carried forward: TENANT-01 OPEN · CD-4 / CD-5 / CD-7 PROPOSED (pending register
owner) · PostgreSQL lane UNEXECUTED · QUOTA-01 OPEN · live trading OFF / NOT CERTIFIED.**

**Recommendation up front: OPTION 3 — defer implementation, resolve D1 first.** The reason is
specific and is the main finding of this phase: **the clean file is not the right place to wire the
GUC, and the right place is not clean.**

---

## 1. Phase 0 — baseline and preservation invariants

Output path `staging/PHASE11B_TENANT01_FAST_COORDINATION_REPORT.md` verified **absent** before
writing. No existing report was overwritten.

| property | prior comparison point | measured now | verdict |
|---|---|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **match** |
| branch / upstream | `main` → `origin/main` | `main` → `origin/main` | match |
| `origin/main` | — | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical to HEAD |
| ahead / behind | — | `0 / 0` | in sync |
| **`git write-tree`** | **`6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`** | **`6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`** | **match — staged content provably untouched** |
| staged paths | 22 | 22 | match |
| unstaged tracked paths | 100 | 100 | match |
| untracked paths | 3325 | **3374** | **+49 drift** |

### 1.1 Two baseline notes that matter

**(a) The prior staged digest `814B8AB272E2C845316D29C90BFE4822` is NOT comparable and should be
retired.** It was produced by hashing `git diff --cached | Out-String` in PowerShell, which wraps
and re-encodes line endings, so it is not reproducible across invocations or platforms. It is
replaced by a byte-level normalized digest:

```
STAGED_DIGEST_SHA256 = 65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c
STAGED_DIFF_BYTES    = 241804          (CRLF normalized to LF before hashing)
```

Method, for reproducibility: `sha256( git diff --cached (raw bytes).replace(b'\r\n', b'\n') )`.
**The authoritative invariant remains `git write-tree`**, which matches exactly — that is what
proves staged content is unchanged, independent of any diff-rendering choice. Per the brief, the raw
`.git/index` hash is not used at all (it mutates on any stat refresh).

**(b) Drift observed and bounded.** Untracked paths moved 3325 → 3374 (+49) during this task.
Staged paths, unstaged paths, the index tree and HEAD are all unchanged, so **the drift is confined
to untracked space** — other sessions writing new `staging/` artifacts (e.g.
`staging/PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md` appeared). No shared tracked state moved.
**No operation was taken that could affect shared state.**

### 1.2 Staged vs unstaged, per TENANT-01-relevant file

| file | staged (+/−) | unstaged (+/−) | tracked? |
|---|---|---|---|
| `backend_app/core/tenant_db_context.py` | **300 / 0** | clean | tracked |
| `backend_app/core/dependencies.py` | 35 / 1 | clean | tracked |
| `backend_app/core/database_pool.py` | **clean** | **clean** | tracked |
| `backend_app/routers/dag_tasks.py` | **clean** | **clean** | tracked |
| `backend_app/routers/billing.py` | clean | **700 / 40** | tracked |
| `backend_app/routers/strategies.py` | clean | **123 / 0** | tracked |
| `backend_app/routers/exchange.py` | 268 / 15 | 154 / 14 | tracked |
| `backend_app/routers/auth.py` | 11 / 1 | clean | tracked |
| `scripts/verify_rls_enforcement.py` | 572 / 0 | clean | tracked |
| `backend_app/core/bot_ownership.py` | — | — | **UNTRACKED (new file, another workstream)** |

Only two files in the whole surface are clean on both sides: **`core/database_pool.py`** and
**`routers/dag_tasks.py`**. §4 explains why neither is usable for step 1.

---

## 2. Phase 1A — tenant-context helper and request lifecycle

### 2.1 Tenant identity: authenticated, then canonicalised

`tenant_db_context.py:246-270` (`get_tenant_db`) takes the tenant from
`_canonical_tenant_id((user or {}).get("id"))`, where `user` comes from
`Depends(get_current_user)` resolved in `tenant_db_dependency()` (`:289-300`). **The tenant is
never read from a request body or query parameter** — stated in the docstring and true in the code.
`_canonical_tenant_id` (`:100-131`) refuses `None`, refuses empty-after-strip, and normalises
through `str(UUID(x))`. The normalisation is load-bearing, not cosmetic: seven of the nine policies
compare a `varchar(36)` column to a `text` setting, so `'7B3F…'` and `'7b3f…'` are different
strings and an upper-cased tenant would match none of its own rows while appearing authenticated.

### 2.2 Is the setting transaction-local, on the same connection and transaction as the query? Yes.

* **Transaction-local:** `_SET_TENANT_SQL` (`:67-69`) is
  `SELECT set_config('app.current_tenant_id', :tenant_id, true)` — third argument `is_local=true`.
  This is required, not stylistic: `database_pool.py:176,178` appends
  `pgbouncer=true&pool_mode=transaction` to the Supabase URL, so a backend connection returns to the
  pool at transaction end. A session-scoped `set_config(..., false)` would survive that handover and
  leave the previous tenant installed for the next request — a cross-tenant read under the very
  mechanism meant to prevent one.
* **Same connection and transaction:** applied by `session.execute(...)` on the *same* `Session`
  object that is yielded to the handler (`:198`, then `:265-269`), inside `with session.begin():`.
  A SQLAlchemy `Session` holds one connection for the life of its transaction, so the GUC and the
  protected query are on the same connection by construction.
* **Parameter-bound, not interpolated:** `set_config` is an ordinary function call, so the tenant id
  binds like any other parameter. The pre-existing `SET LOCAL app.current_tenant_id = :tenant_id`
  sites (`transactional_execution_manager.py:225-227`, `tenant_rls_validator.py:438-441`) only work
  because psycopg2 interpolates client-side; they would **fail on asyncpg**.

### 2.3 The lifecycle mismatch — the key mechanical finding

`database_pool.py:301-306`:

```python
def get_session(self):
    """Get a database session from the pool."""
    if not self._initialized:
        self.initialize()
    return self._session_factory()
```

The factory is `sessionmaker(bind=engine, autocommit=False, autoflush=False)`
(`database_pool.py:246-250`). A session returned this way has **no transaction open** — SQLAlchemy
begins lazily on first use. So immediately after `get_db` yields, `session.in_transaction()` is
`False`, and `apply_tenant_context` would raise `TenantContextNotTransactional` (`:195-201`).

**That is the helper behaving correctly, and it rules out one of the three options:**

> **Wiring the GUC inside `get_db` is not safe.** To make it work you must open a transaction before
> yielding, which changes commit/rollback semantics for **every existing handler** that uses
> `Depends(get_db)` — including any that rely on "no commit means no write". This is precisely why
> O1 made `get_tenant_db` a **separate, additive** dependency and left `get_db` untouched
> (`tenant_db_context.py:248-262` says so explicitly).

### 2.4 Is `tenant_db_dependency()` suitable per router? Conditionally.

One behavioural difference a caller must accept: `get_tenant_db` **commits** on normal exit
(`with session.begin():` at `:265`), whereas `get_db` never commits. For a **read-only** endpoint
that is harmless — there is nothing to commit. For a mutating handler that already calls
`db.commit()` it is also fine (the outer block finds nothing left). It is **not** fine for a handler
relying on absence-of-commit as a rollback. **This is the strongest argument for selecting a
read-only endpoint first**, which is what the brief asks for.

No conflict with authentication or authorization: `tenant_db_dependency()` composes
`Depends(get_current_user)` and adds nothing else. Session cleanup is `finally: session.close()`
(`:270`), matching `get_db`'s own contract.

### 2.5 Minimal request-path call-site map — 4 sites, 3 files

| # | site | endpoint | tenant-scoped? | read-only? | mechanism | file status |
|---|---|---|---|---|---|---|
| R1 | `routers/billing.py:1894` | `GET /api/billing/payment-methods` (`get_payment_methods`) | **yes** (`Depends(get_current_user)`) | **yes** | `db: Session = Depends(get_db)` | **unstaged 700/40 (O3)** |
| R2 | `routers/billing.py:3223` | `DELETE /api/billing/payment-methods/{method_id}` | yes | no — mutating | `Depends(get_db)` | **unstaged 700/40 (O3)** |
| R3 | `routers/dag_tasks.py:530` | dead-letter-queue listing | **no — admin/service level** | yes | `SessionLocal()` inline | **clean** |
| R4 | `routers/strategies.py:983` | `last_execution_at` read helper; already guards `user_id` is a tenant UUID | yes | yes | `with get_db_context() as session:` | **unstaged 123/0** |

**Proposed first endpoint, and the blocker.** On technical merit the answer is unambiguous: **R1**.
It is read-only, already authenticated, already takes `Depends(get_db)`, so adoption is a one-line
substitution to `Depends(TenantDB)` with no transaction-semantics change and a trivially reversible
diff. **But R1 is in `billing.py`, which carries 700/40 unstaged lines from O3 — so it is blocked on
D6.**

Of the alternatives: **R3 is the only clean file, and it is not tenant-scoped** — wiring an
admin dead-letter listing would produce no tenancy evidence at all, so it would be motion without
progress. **R4** is read-only and already tenant-aware, but it uses a context manager inside a
helper rather than a `Depends`, so adoption needs restructuring, and `strategies.py` carries 123/0
unstaged.

> **Conclusion: no endpoint can be safely selected today. The blocker is ownership, not technique.**
> The single decision that unblocks it is **D1**, plus **D6** if R1 is the chosen endpoint.

---

## 3. Phase 1B — background and worker session paths

### 3.1 Full grouping, and a correction to the prior estimate

36 raw hits for `db_pool.get_session()` / `get_db_context()` / `get_db_session()` / `SessionLocal()`.
Excluding 14 that are not call sites — `database_pool.py` ×9 (the definitions themselves),
`database.py` ×3 (the re-export shim), `tenant_db_context.py` ×2 (the helper) — leaves **22 real
call sites**. Adding the 2 `Depends(get_db)` sites in `billing.py` gives **24 ORM entry points**.

| context | count | sites |
|---|---|---|
| **HTTP request** | **4** | `billing.py:1894`, `billing.py:3223`, `dag_tasks.py:530`, `strategies.py:983` |
| **Worker** | **10** | `dag_worker.py:208,361,589`; `task_recovery.py:132,231,454`; `exchange_websocket_listener.py:477,508`; `dag_task_queue.py:400,912` |
| **Startup** | **3** | `main.py:425,443`; `startup_recovery.py:188` |
| **Scheduler** | **1** | `reconciliation_scheduler.py:127` |
| **Service / internal** | **6** | `execution_engine.py:877,1502,2495`; `consistency_checker.py:286`; `readiness.py:193`; `safety_audit.py:128` |
| **non-HTTP total** | **20** | |

**Correction:** the prior report's "approximately 14 worker/background entry points" is an
**undercount — the correct figure is 20.** Step 1b is ~43% larger than previously scoped.

### 3.2 `bot_ownership` — two separate obstacles, not one

`backend_app/core/bot_ownership.py` is an **untracked new file** from another workstream.

* `_claim_store_dsn()` (`:304-315`) prefers `AERORA_TEST_DATABASE_URL`, then `DATABASE_URL`, and
  returns `None` for SQLite/empty rather than raising.
* Three connections — `:373-375` (claim), `:407-410` (release), `:457-458` (heartbeat) — each
  `psycopg2.connect(dsn)` followed by **`conn.autocommit = True`**.

**Obstacle 1 — autocommit.** With autocommit there is no explicit transaction; each statement gets
its own implicit one. A transaction-local `set_config(..., true)` issued there would be discarded
before the next statement ran. To apply tenant context safely: drop `autocommit = True`, wrap
claim/release/heartbeat in explicit transactions, and issue the GUC inside the same transaction as
the protected statement. That changes the concurrency semantics of a claim store whose whole point
is cross-process races — so it needs its own analysis, not a mechanical edit.

**Obstacle 2, not previously recorded — the helper does not cover this path at all.**
`apply_tenant_context` takes a SQLAlchemy `Session` (it calls `session.execute`, inspects
`session.bind.dialect.name`, and `session.in_transaction()`). `bot_ownership` uses **raw psycopg2**.
So even after the transaction restructuring, a **second, psycopg2-flavoured implementation** of the
GUC application is required. `bot_ownership` is not the only raw-psycopg2 path in the backend, so
this generalises.

**Not attempted in this phase**, per the brief.

---

## 4. Phase 1C — RLS and database roles (re-confirmed from executable SQL)

* **`FORCE ROW LEVEL SECURITY` exists nowhere as an executable statement.** Searched
  `backend_app/migrations/*.sql`, `backend_app/alembic/versions/*.py`, `migrations/*.sql`,
  `migrations/hardening/*.sql`, `archived_migrations/**/*.sql`, `scripts/*.py`. **Six hits, every one
  a comment or docstring:** `026_fee_records_row_level_security.sql:75`, `verify_rls.py:191`,
  `verify_rls_enforcement.py:11,197,518,560`.
* **Role provisioning creates no application login.** `scripts/provision_database.py:226,229,232`
  creates `anon`, `authenticated`, `service_role` — all **`NOLOGIN`**, `service_role` additionally
  **`BYPASSRLS`**. `:304-310` sets `ALTER DEFAULT PRIVILEGES FOR ROLE postgres`, whose own comment
  states `postgres` "is what runs this script and therefore creates every table that follows".
* **No `SET ROLE` / `SET LOCAL ROLE` anywhere.** No second DSN. `terraform/` manages no PostgreSQL
  roles.

| claim | establishable statically? |
|---|---|
| no executable `FORCE` statement exists | **yes — established** |
| no application login role is provisioned | **yes — established** |
| no `SET ROLE` exists | **yes — established** |
| the app role's `rolsuper` / `rolbypassrls` | **no — requires authorized live PG (D7)** |
| per-table `relowner` / `relforcerowsecurity` | **no — requires authorized live PG (D7)** |

---

## 5. Phase 1D — billing schema reconciliation matrix

Three INSERT sites, all into `billing_invoices`, all through the **`SUPABASE_SERVICE_ROLE_KEY`**
client `_background_sb()` (`billing.py:187-195`) which is `BYPASSRLS` by design:

| site | event | columns written |
|---|---|---|
| `billing.py:2457-2468` | Stripe `checkout.session.completed` | `user_id, provider, provider_payment_id, amount_usd, amount_inr, currency, status, plan, created_at` |
| `billing.py:2670-2684` | Stripe `invoice.payment_succeeded` (renewal) | same shape |
| `billing.py:2975-2986` | Razorpay `payment.captured` | same shape |

### 5.1 Reconciliation matrix

Declared schema: `migrations/006_reconcile_production_database.sql:796-809`.

| column | declared? | written by webhook? | note |
|---|---|---|---|
| `id` | yes — `TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text` | no | surrogate key; **does not dedupe anything** |
| `user_id` | yes `TEXT NOT NULL` | **yes** | payload-derived (CD-3) |
| `currency` | yes `VARCHAR(10)` | **yes** | — |
| `status` | yes `VARCHAR(50)` | **yes** | — |
| `created_at` | yes `TIMESTAMPTZ` | **yes** | — |
| `amount` | **yes** | **NO** | declared, never written |
| `invoice_url` | yes | no | declared, never written |
| `invoice_pdf` | yes | no | declared, never written |
| `period_start` | yes | no | declared, never written |
| `period_end` | yes | no | declared, never written |
| `updated_at` | yes | no | declared, never written |
| **`provider`** | **NO** | **yes** | **undeclared** |
| **`provider_payment_id`** | **NO** | **yes** | **undeclared — and the natural dedupe key** |
| **`amount_usd`** | **NO** | **yes** | **undeclared** |
| **`amount_inr`** | **NO** | **yes** | **undeclared** |
| **`plan`** | **NO** | **yes** | **undeclared** |

* **Missing / incompatible:** 5 written columns are undeclared; 6 declared columns are never written,
  including `amount` — the only declared money column.
* **Later migration or ORM model that changes this conclusion: none.** Searched every migration tree
  for `ALTER TABLE … billing_invoices`: the **only** hit is
  `ALTER TABLE public.billing_invoices ENABLE ROW LEVEL SECURITY` (`006:811`). There is **no ORM
  model** for this table — billing writes it through Supabase PostgREST, not SQLAlchemy.
* **Swallowed failure paths:** all three inserts are wrapped in
  `except Exception: logger.warning(f"[BILLING] Could not insert billing_invoices…")`. A total
  failure is a log line at WARNING, after the customer has been charged.
* **Uniqueness:** none. The only index is `idx_billing_invoices_user ON (user_id)` (`006:810`) —
  **non-unique**. No webhook-event dedupe table exists anywhere (searched `CREATE TABLE … webhook`,
  `webhook_events`, `processed_events`, `provider_event` — zero hits).
* **Test blind spot, structural:** `tests/test_orm_models_match_provisioned_schema.py` compares
  `Base.metadata.tables` (`:149-162`) against the live schema (`:184-210`). Because
  `billing_invoices` has no ORM model, it **can never appear** in that comparison. The test written
  for exactly this defect class cannot see this table. Same for
  `scripts/verify_production_schema.py` and `verify_schema_drift.py` (zero hits).
* **Provider event identifiers available for a durable key:** Stripe `event["id"]`
  (`billing.py:2338`); Razorpay `payload["event_id"]` falling back to
  `payload.payload.payment.entity.id` (`:2843`).
* **The model to copy:** `backend_app/migrations/029_referral_obligations.sql:151` —
  `CONSTRAINT uq_referral_obligation_event UNIQUE (provider, provider_payment_id)`, whose own
  comment (`:58-63`) says *"Only a unique constraint makes the check and the insert one atomic
  decision"*.

### 5.2 Evidence required from the authorized live database (D7)

1. `information_schema.columns` for `public.billing_invoices` — does it have `provider`,
   `provider_payment_id`, `amount_usd`, `amount_inr`, `plan`?
2. `pg_indexes` / `pg_constraint` for that table — any unique constraint?
3. Row count and a sample — are invoices being written at all, or is every insert failing silently?

**Until (1) is answered, a uniqueness constraint cannot be designed**, because the columns it would
target are of unconfirmed existence. This is CD-7 blocking CD-5, and it is the one dependency that
reorders the plan.

### 5.3 Safe test coverage needed before any schema or idempotency fix

* A schema-contract test that covers **non-ORM** tables (the current one structurally cannot) —
  real PG, D7.
* A duplicate-event test: same provider event id delivered twice → exactly one invoice row and one
  commission — real PG, D7.
* A test that the invoice insert **failure is surfaced**, not swallowed — can be written against a
  double today, no DB needed.

---

## 6. Phase 2 — safe test classification and results

### 6.1 Classification (fixtures inspected before running anything)

| class | suites | basis |
|---|---|---|
| **Safe now** | `tests/test_tenant_db_context.py`, `tests/test_verify_rls_script.py` | No `create_engine`, no `psycopg2.connect`, no `db_pool` reference. The first drives a `_RecordingSession` double; the second drives fake cursors and its first test is `test_module_imports_without_credentials`. `tests/conftest.py:13` blanks `REDIS_URL`; `:37-41` blanks `DATABASE_URL` unless `AERORA_TEST_DATABASE_URL` is set — both unset here. |
| **Conditional** | `test_orm_models_match_provisioned_schema.py` | **Connects at import time** to distinguish "unset" from "stale" (`:78-89`). Safe only because both DSNs are unset; would connect if either were set. |
| **Owner-authorized only (O5 / D7)** | `test_tenant_db_context_postgres.py`, `test_tenant01_cross_tenant_matrix_postgres.py`, `test_cross_tenant_read_sweep_postgres.py`, `test_billing_data_isolation_postgres.py`, `test_tenant02_fee_records_rls_postgres.py`, `test_tenant_isolation_rls_postgres.py` | real PostgreSQL; the only cluster available is O5's shared `staging/pgdata_r1` |

### 6.2 Executed — the safe batch only

```powershell
$env:PYTHONUTF8='1'
Remove-Item Env:\AERORA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:\AERORA_TEST_REDIS_URL -ErrorAction SilentlyContinue
.venv\Scripts\python.exe -m pytest tests/test_tenant_db_context.py tests/test_verify_rls_script.py `
    -q -p no:cacheprovider -p no:warnings -o addopts="" --tb=short -rfEs
```

**Result: `35 passed` · 0 failed · 0 skipped · 0 errors · exit code 0 · 8.01 s (wall 12.5 s).**

No PostgreSQL, Redis, container or shared service was started, stopped or connected to.
`staging/pg_dsn.txt` was not read or used.

**What this evidence does and does not establish.** It establishes that the tenant-context helper's
refusals, canonicalisation, `is_local=true` choice, parameter binding, dialect detection and
setting-name/policy agreement hold — 20 of the 35 — and that the RLS auditor's own decision logic is
sound — 15 of the 35. **It is not proof of real PostgreSQL RLS enforcement**, which is
D7-gated and remains **UNEXECUTED**. A skipped test is not a pass; none of the 35 skipped.

---

## 7. Phase 3 — owner decision matrix (D1–D8)

**No decision below is marked approved. No approval was inferred from staged code or any prior
report.**

| # | exact question | owner | affects | evidence established | remaining uncertainty | cost of deferring |
|---|---|---|---|---|---|---|
| **D1** | Will O1 wire the GUC through its own `tenant_db_dependency()`, or delegate that to another workstream? | **O1** | `core/tenant_db_context.py` | helper is correct and transaction-local (§2.2); **zero production call sites** | which router O1 wants first | **This is the single blocking decision. Nothing in step 1 can start.** |
| **D2** | Is modifying `core/database_pool.py` authorized as a fallback? | **O1 + O3** | `core/database_pool.py` (clean) | file is clean; **but §2.3 shows `get_db` is the wrong place** — a transaction would have to be forced on every handler | whether a narrow non-`get_db` hook is wanted | low — §4 recommends *against* this route on technical grounds |
| **D3** | Authorize a `NOSUPERUSER NOBYPASSRLS` application login + `APP_DATABASE_URL`? | **O4** | `provision_database.py`, `core/config.py`, `core/database_pool.py` | no app login exists today (§4); `service_role` is `NOLOGIN BYPASSRLS` | live role attributes (UR-1) | step 4 can never be safe; TENANT-01 cannot close |
| **D4** | Authorize gating `Base.metadata.create_all` (`main.py:795`), after inventorying dependent lanes? | **O3** | `backend_app/main.py` (+289/−36) | runs at import, swallows failure into a warning; makes the app role table owner | **which test/dev lanes depend on implicit creation — not yet inventoried** | the app keeps becoming table owner for new ORM-only tables |
| **D5** | Authorize table-by-table `FORCE ROW LEVEL SECURITY` **after** prerequisites pass? | **O4** | a new migration | no executable `FORCE` exists | live `relowner`/`relforcerowsecurity` (UR-2) | TENANT-01 stays OPEN |
| **D6** | Authorize billing webhook remediation (CD-3 / CD-4 / CD-5) in `routers/billing.py`? | **O3** | `routers/billing.py` (+700/−40) | CD-3/4/5 mechanisms read from source; R1 is the best first endpoint | CD-7 must resolve first for CD-5 | R1 unavailable; duplicate-financial-effect window stays open |
| **D7** | Which isolated PostgreSQL lane and scope are authorized — and may O5's shared `staging/pgdata_r1` be used? | **O5** | the shared cluster | lane is O5's; an authorization path already exists in `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`; four suites need per-suite authorization per `PHASE11B_W1B_PG_TEST_SAFETY_MATRIX.md` | — | **every closure criterion is PG-gated; UR-1, UR-2 and CD-7 all stay unresolved** |
| **D8** | Accept CD-4, CD-5, **CD-7** into the launch-gate register? | register owner | register only | all three read from source; CD-5's missing constraint and CD-7's undeclared columns now **confirmed** (§5.1) | CD-7's live direction | findings stay invisible to launch gating |

---

## 8. Phase 4 — recommended next implementation

### 8.1 Option comparison, on inspected code

| option | viability | why |
|---|---|---|
| **1 — wire one request endpoint via O1's helper** | **technically correct, ownership-blocked** | R1 (`billing.py:1894 GET /payment-methods`) is read-only, already authenticated, already `Depends(get_db)` → one-line swap, no transaction-semantics change, trivially reversible. **But `billing.py` carries 700/40 unstaged (O3) → needs D6**, and D1 must settle who does the wiring. The only clean router (R3) is admin-level and not tenant-scoped, so it would yield no tenancy evidence. |
| **2 — modify the clean `database_pool.py`** | **available but technically wrong** | The file is clean on both sides, so it needs no one's diff. But §2.3 is decisive: `get_session()` returns a session with **no transaction open**, so wiring the GUC into `get_db` requires opening a transaction and thereby changing commit/rollback semantics for **every** handler using `Depends(get_db)`. That is a larger behavioural change than the fix it delivers, and it is exactly what O1 avoided by making the helper additive. |
| **3 — defer and resolve ownership** | **RECOMMENDED** | The clean file is the wrong place; the right place is not clean. One decision (**D1**, plus **D6** if R1) converts this from blocked to a one-line change. |

### 8.2 Recommendation: **Option 3 — defer. Resolve D1 (and D6 for R1).**

When authorized, the implementation is:

**File allowlist — exactly two files, nothing else:**
```
backend_app/routers/billing.py     # R1 only: swap Depends(get_db) -> Depends(TenantDB) at :1894,
                                   # plus one module-scope `TenantDB = tenant_db_dependency()`
tests/test_tenant_db_request_path_postgres.py   # NEW - the real-PG proof
```
`core/tenant_db_context.py` is **not** in the allowlist — it is used as-is, unmodified.
`core/database_pool.py`, `main.py`, `config.py`, `dependencies.py`, every migration and every other
router are **out of scope**.

**Focused test plan:**
1. Safe now, no DB: the §6.2 batch (35 tests) must stay green.
2. Safe now, no DB: `GET /api/billing/payment-methods` returns the caller's own methods unchanged
   (same-tenant regression) — existing billing tests.
3. D7-gated, real PG: a new test asserting (a) the GUC is set to the caller's canonical tenant id
   inside the handler's transaction, (b) a second request on the same pooled connection sees the GUC
   **unset** at transaction start, (c) with RLS forced on one table, tenant A's request cannot read
   tenant B's row.

**Rollback:** revert one `Depends(...)` reference and delete one module-scope line. No schema change,
no role change, no migration, no change to `get_db` or to any other handler. Reversible in a single
edit.

**Acceptance criteria:**
* The §6.2 batch stays at 35 passed.
* `GET /api/billing/payment-methods` behaviour is unchanged for the owning tenant.
* The new real-PG test passes (D7) — **a skip is not acceptance**.
* `scripts/verify_rls_enforcement.py` output is unchanged (this step changes no role and no table).
* The report states explicitly that **3 of 4 request sites and all 20 non-HTTP sites remain
  unwired** — no global isolation claim.

### 8.3 Sequencing constraints — preserved and restated

* **Do not force RLS** before request *and* worker context coverage, role separation (D3) and the
  schema prerequisites are verified. Out of order, `current_setting(...)` is NULL, the predicate is
  NULL, and enforced reads return **zero rows** — `orders`, `positions`, `fills` go silently empty
  rather than erroring (`tenant_db_context.py:12-18`, `verify_rls_enforcement.py:560-570`).
* **Do not claim global tenant isolation** while 20 non-HTTP sites are unwired and `bot_ownership`
  needs both a transaction restructuring and a psycopg2-flavoured GUC implementation (§3.2).
* **Do not design billing idempotency around unconfirmed columns** — CD-7 must resolve before CD-5.
* **No launch-readiness percentage, audit JSON mirror or certification register was changed.**

---

## 9. Remaining uncertainties and evidence required for closure

| # | uncertainty | evidence required | gate |
|---|---|---|---|
| U1 | app role `rolsuper` / `rolbypassrls` | `scripts/verify_rls_enforcement.py` exit 0 against an authorized DB | **D7** |
| U2 | per-table `relowner` / `relforcerowsecurity` | same | **D7** |
| U3 | **CD-7 direction** — does `billing_invoices` have the five written columns, or is every insert failing silently? | `information_schema.columns` + `pg_constraint` + a row sample | **D7** — **blocks CD-5** |
| U4 | which test/dev lanes depend on implicit `Base.metadata.create_all` | static inventory of `create_all` dependants; can be done read-only | none — doable next |
| U5 | reachability of `transactional_execution_manager` / `tenant_rls_validator` from a request path | call-graph trace; read-only | none — doable next |
| U6 | `marketplace/settlement_service.py` actual writes | read the source (not yet read) | none — doable next |
| U7 | `bot_ownership` transaction restructuring impact on claim races | design review with its owner | file is **untracked**, owner unidentified |

U4, U5 and U6 are the three highest-value items obtainable **without any approval** — read-only
static work that would shrink D4's and CD-5's uncertainty before those decisions are asked for.

---

## 10. Exit verification

Re-measured after writing this report.

| property | Phase 0 | exit | verdict |
|---|---|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | *(see §11)* | — |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | *(see §11)* | — |
| staged digest (normalized) | `65dfc453…bd0c` | *(see §11)* | — |
| staged / unstaged paths | 22 / 100 | *(see §11)* | — |

§11 records the measured exit values. **The only new artifact is this report.** No tracked file was
modified by this task; every tracked modification present belongs to another workstream and was
recorded in §1.2.

## 11. Measured exit state

```
HEAD                 = 074b772ab16cece62cbfca0f2b78497be2ae56d5
INDEX_TREE           = 6f40856cee2bc7ea4d741f69f4b1f68b463d70a4
STAGED_DIGEST_SHA256 = 65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c
STAGED_COUNT         = 22
UNSTAGED_COUNT       = 100
```

All four match Phase 0. Untracked count continues to drift upward from other sessions; that is
expected and confined to untracked space.

---

**Nothing in this document establishes exchange correctness, exactly-once execution, paper-trading
certification, or live-trading readiness. No production remediation has been completed or started.**
