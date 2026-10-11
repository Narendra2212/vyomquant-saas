# Phase 11B — TENANT-01 readiness audit and minimal remediation plan

**Status of this document: READ-ONLY AUDIT. No file was modified, no migration applied, no role
changed, no commit made.**

**Baseline revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5`, branch `main`, in sync with
`origin/main`.
**Live trading:** OFF / NOT CERTIFIED. No production database was accessed. No data mutated.
**Not changed by this audit:** launch-readiness percentages, audit JSON mirrors, certification
registers.

> **Headline disposition: TENANT-01 remains OPEN, and it must not be closed by the obvious patch.**
> The code-level mechanism is confirmed by reading. The remediation is **blocked on ownership**, not
> on analysis: every file the fix touches is concurrently modified by other workstreams, and one
> plausible-looking fix (`FORCE ROW LEVEL SECURITY`) would convert reads into **silently empty
> result sets** rather than errors, because the tenant GUC is set on no request path.

---

## Phase 0 — read-only rebaseline

### 0.1 Git state, recorded before anything was read

| | |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| branch / upstream | `main` → `origin/main`; `## main...origin/main` (no ahead/behind) |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (identical) |
| staged entries | **22** |
| unstaged tracked files | **100** |
| untracked paths | **3274** |
| `.git/index` SHA-256 | `3DBF1CB07E58B1E9FFB1A70154F9C008AD539188F45AB2CBE2B367FC1CAF5676` |
| `.git/index` size | 232081 bytes |

Nothing was reset, cleaned, stashed, checked out over, or broadly staged. The index was not
touched, so the checksum above is also the exit state.

### 0.2 OWNERSHIP BOUNDARIES — the blocking finding

**The TENANT-01 remediation is already in flight in another workstream's staged index.** Of the 22
staged entries, eleven are tenant-isolation work:

```
A  backend_app/core/tenant_db_context.py                               <- the proposed fix itself
M  backend_app/core/dependencies.py
M  backend_app/routers/auth.py
MM backend_app/routers/exchange.py            (staged AND further modified unstaged)
A  docs/saas-architecture/tenant-isolation-matrix.md
A  docs/adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md
A  docs/adr/ADR-0007-per-tenant-credential-encryption-keys.md
M  scripts/verify_rls.py
A  scripts/verify_rls_enforcement.py
A  tests/test_tenant_db_context.py
A  tests/test_tenant_db_context_postgres.py
```

Additionally, files the fix would have to touch carry large **unstaged** diffs from other
workstreams:

| file | unstaged diff | needed by the fix? |
|---|---|---|
| `backend_app/routers/billing.py` | **+700 / −40** | yes (webhook identity) |
| `backend_app/main.py` | +289 / −36 | yes (`create_all`, router wiring) |
| `backend_app/core/config.py` | +401 / −33 | yes (a second DSN / role) |
| `backend_app/core/execution_engine.py` | +911 / −28 | adjacent |
| `backend_app/core/database_pool.py` | **clean** | yes (session factory) |

**Consequence, and it is the Phase 0 stop condition:** every candidate patch except one overlaps a
concurrently-modified file. Per the brief I am stopping at the proposal. **No edit was made.**

### 0.3 Files and code paths actually inspected

Read directly (not inferred): `backend_app/core/tenant_db_context.py` (all 300 lines);
`backend_app/core/database_pool.py` (lines 49–106, 135–302, 375–452, 477–562, 579–662);
`backend_app/core/database.py`; `backend_app/main.py` (79–134, 793–849, 837–952);
`backend_app/routers/billing.py` (186–245, 402–612, 955–1097, 1288–1450, 2299–2430, 2454–2520,
2534–2596, 2604–2780, 2801–2945, 2969–3040, 3099–3208); `scripts/verify_rls_enforcement.py`
(1–75, 149–288, 511–620); `scripts/provision_database.py` (221–362, 511–574);
`staging/PHASE11A_LAUNCH_GATE_REGISTER.md` §TENANT-01; `staging/PHASE11A_NEXT_ACTIONS.md`;
`tests/test_billing_data_isolation_postgres.py` (1–110);
`tests/test_webhook_duplicate_delivery_real_redis.py` (1–120, 349–515);
`tests/test_billing_e2e.py` (1194–1255, 1749–1820).

Two sub-agent investigations supplied the connection/role inventory and the webhook trace with
file:line citations; their claims about files I did not personally open are marked as such below.

### 0.4 Database connection / role inventory

**There is exactly one database role in the entire backend: whatever is embedded in
`DATABASE_URL`.** Confirmed:

* `database_pool.py:138` — `DATABASE_URL = os.getenv("DATABASE_URL")`; `:140-143` refuses to start
  in paper/live without it; `:146-155` rewrites an `https://` Supabase URL to
  `SUPABASE_DB_URL`, defaulting to the hardcoded **`postgresql://postgres:postgres@localhost:5432/postgres`**.
* `database_pool.py:53` — `Base = declarative_base()`; `:222-231` the one `create_engine`;
  `:246-250` the one `sessionmaker`; `:377` the process singleton.
* `database_pool.py:385/395` and `:591/606` — **two** `get_db` / `get_db_context` pairs (the later
  shadows the earlier). Both only `get_session()` → `yield` → `close()`. **Neither sets any role
  or any session variable.**
* `database_pool.py:499-509` — the asyncpg pool passes `server_settings={'jit','application_name'}`
  only: no `role`, no `app.current_tenant_id`.
* **No `SET ROLE` or `SET LOCAL ROLE` exists anywhere in the repository.**
* **No application-role vs owner/migration-role separation exists** in code, `.env.example`,
  `DATABASE_PROVISIONING_RUNBOOK.md`, or `terraform/` (terraform manages no PostgreSQL roles at
  all). `scripts/provision_database.py:224-235` creates only `anon`, `authenticated`,
  `service_role` — all **`NOLOGIN`**, so none is usable as a SQLAlchemy login role, and
  `service_role` is created **`BYPASSRLS`** by design. `:304-310` sets
  `ALTER DEFAULT PRIVILEGES FOR ROLE postgres`, with the comment stating plainly that `postgres`
  "is what runs this script and therefore creates every table that follows".
* `backend_app/core/bot_ownership.py:304-315, 373-375, 407-410, 457-458` — raw psycopg2 on the same
  single role, with **`conn.autocommit = True`**, i.e. no explicit transaction.

Three distinct privilege postures exist against the same tables:

| path | role | RLS? |
|---|---|---|
| PostgREST with anon key + end-user JWT (`dependencies.py:148-176`, `:242-249`) | `authenticated`, `auth.uid()` populated | **enforced** |
| PostgREST with `SUPABASE_SERVICE_ROLE_KEY` (`billing.py:187-195`, `api_key_vault.py:76-89`, `supabase_connection.py:31-38`, `marketplace_expiry_worker.py:154-167`, `tee/main.py:31-33`) | `service_role`, **BYPASSRLS** | **bypassed by design** |
| SQLAlchemy on `DATABASE_URL` | single role, owner/likely superuser | **not applied** (see CD-1) |

---

## Phase 1 — evidence-based risk classification

### Confirmed defects (code-verified by reading)

#### CD-1 — RLS is inert on the SQLAlchemy/ORM path. Four independent reasons, all present.

1. **Policies are ENABLEd, never FORCEd.** `alembic/versions/implement_rls_policies_20260817.py`
   issues only `ENABLE ROW LEVEL SECURITY`. PostgreSQL exempts a table's **owner** from its own
   policies unless `FORCE` is set. *(No `FORCE ROW LEVEL SECURITY` statement exists in any
   migration or provisioning file — sub-agent grep, not personally re-verified.)*
2. **The app role owns the tables.** `backend_app/main.py:794-798` runs
   `Base.metadata.create_all(bind=engine)` **at module import**, against the app's own engine, and
   swallows failure into a warning. Any ORM-only table is therefore created by — and owned by — the
   app's connection role. This has already bitten once: `tests/test_tenant02_fee_records_rls_postgres.py:17-21`
   records `fee_records` existing only as `fee_engine.FeeRecordModel`, with zero `.sql` files
   mentioning it, so it never received the `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` statements
   every migration-created table gets.
3. **The role is likely superuser, which `FORCE` cannot fix.** `.env.example:17` templates the
   Supabase `postgres` superuser, and `database_pool.py:155` hardcodes a `postgres` fallback.
   `scripts/verify_rls_enforcement.py:196-238` checks exactly this and its own output states it:
   *"FORCE ROW LEVEL SECURITY removes only the owner's implicit exemption, not a role attribute."*
4. **Nothing sets the tenant GUC on any request path.** The policies compare `tenant_id` against
   `current_setting('app.current_tenant_id', true)`. The only statement sites are
   `tenant_db_context.py:67-69` (`set_config`, unwired — see CD-2),
   `transactional_execution_manager.py:225-227` and `tenant_rls_validator.py:438-441` (both
   `SET LOCAL`, neither on a request path). `get_db` sets nothing.

**Severity: P1 for production, P2 for restricted paper.** Not currently an open leak: the
application's own queries carry `user_id`/`tenant_id` WHERE clauses, and the Phase 4 breadth sweep
measured 0 cross-tenant leaks — **but that sweep ran on the PostgREST path under `authenticated`,
not on the ORM path**, so it is not evidence about this defect. What is lost is the defence-in-depth
layer: any single query that omits its filter becomes a cross-tenant read with nothing behind it.

**Critical corollary — the obvious fix is dangerous in the wrong order.** Because reason 4 holds,
applying `FORCE ROW LEVEL SECURITY` *first* makes `current_setting(...)` return NULL, the policy
predicate evaluate to NULL rather than TRUE, and every enforced read match **zero rows**. Reads on
`orders`, `positions` and `fills` would go silently empty instead of erroring.
`tenant_db_context.py:12-18` and `verify_rls_enforcement.py:565-570` both say this explicitly. **The
GUC must be wired before anything is forced.**

#### CD-2 — `tenant_db_context.py` is correct and wired to nothing.

The module is well-built: `set_config` rather than `SET LOCAL` (parameter-bindable, driver-agnostic);
`is_local=true` so the setting cannot survive a pgbouncer connection handover;
refuses a session with no open transaction (`TenantContextNotTransactional`); refuses absent/empty/
non-UUID ids (`TenantContextUnavailable`); canonicalises through `UUID()` because seven of nine
policies compare `varchar(36)` to `text`, making case significant; detects unsupported dialects
rather than catching them.

**But:** I grepped `backend_app/**/*.py` for `tenant_db_context|tenant_db_dependency|get_tenant_db|apply_tenant_context|tenant_db_session`
excluding the module itself — **zero matches.** It is referenced only by
`tests/test_tenant_db_context.py`, `tests/test_tenant_db_context_postgres.py`,
`tests/test_tenant_isolation_rls_postgres.py`, `tests/test_tenant02_fee_records_rls_postgres.py`
and `tests/test_bandit_sast_gate_is_green.py`. **No router, no dependency, no worker uses it.**

**Severity: this is the missing prerequisite, not a defect in itself.** It is also **staged by
another workstream**, so it is not mine to wire.

#### CD-3 — Billing webhook tenant identity comes from the provider payload, with no trusted local mapping.

Both webhooks are public by design (`billing.py:2300-2306`, `:2815-2821`; mounted at
`main.py:840` with no router-level dependency) — correct for webhooks. Signature verification
**fails closed** on both providers (`:2307-2333`, `:2822-2869`): missing header → 403, missing
secret → 500, bad HMAC → 403/400, constant-time compare. `_validate_keys` additionally requires
live `sk_live_`/`rzp_live_` prefixes. There is no signature bypass flag.

**The identity, however, is read straight from the payload on every branch:**

| event | line | source |
|---|---|---|
| `checkout.session.completed` | `2357` `user_id = session.get("client_reference_id")` | payload |
| Razorpay `payment.captured` | `2885` `user_id = notes.get("user_id")` | payload |
| `customer.subscription.*` | `2606` payload metadata, `2611` Stripe API fallback | payload, then provider API |
| `invoice.payment_succeeded` | `2651` / `2655` | provider API (sub id from payload) |
| `invoice.payment_failed` → writes `is_frozen` | `2714` / `2718` | provider API |

`user_id` is checked for **presence only** (`2373-2378`, `2887-2892`) — never UUID-validated, even
though `_validate_uuid` exists at `:548-562` and is applied to `library_id`/`subscription_id` at
`:1011-1013`. The writes then go through `_background_sb()` — the **`SUPABASE_SERVICE_ROLE_KEY`**
client, which is BYPASSRLS by design and asserted so in
`tests/test_billing_data_isolation_postgres.py:25-31`. So RLS provides **zero** protection on this
path; the `.eq("id", user_id)` filters are the entire isolation story, and they use the
payload-derived id: `profiles` tier update (`1380`, `1392-1395`), `profiles` freeze (`2723-2727`),
`billing_invoices` inserts (`2457-2468`, `2670-2684`, `2975-2986`), RPCs `increment_ml_addon`
(`1361`), `process_referral_commission` (`2538-2545`), `reverse_referral_commission` (`2760`,
`3107` — **no tenant argument at all**, keyed on `p_payment_id`).

The marketplace sub-path is worse in one specific way: `_apply_billing_entitlement` reads
`subscription_id` from the payload metadata (`1300-1328`) and `_apply_marketplace_entitlement`
settles on it (`1039-1047`), while `user_id` appears **only in log lines** (`1029-1031`,
`1089-1092`). **Nothing checks that the `subscription_id` belongs to `user_id`.** The amount/currency
match in `settle` (Requirement 9.14) is the only correlation control.

**Severity: P2.** Exploitation requires the webhook secret (or provider-dashboard access to set
metadata). But the consequence is a **full cross-tenant privileged-write primitive**: a forged
signed `payment.captured` with `notes.user_id = <any uuid>` grants that tenant any real paid tier,
files a `billing_invoices` row under their id, and credits referral money. The module's own
docstring (`338-359`) concedes the dependency: *"the webhook SECRET was the only remaining control
on free access to every paid tier"*. Mitigations that do hold: HMAC fails closed, IP allowlist
(production-unconditional, `415-424`), and `VALID_ITEM_KEYS` derived from the `Plan` enum
(`200-210`) so an arbitrary tier string cannot be invented.

#### CD-4 — `_validate_webhook_timestamp` cannot reject anything.

`billing.py:515-546`. Two independent reasons, both verified by reading:

1. For Stripe it reads `request.headers.get("stripe-signature")` as the "timestamp" — that is the
   whole `t=...,v1=...` string, so `int(...)` raises `ValueError`.
2. Even on a successful parse, the `raise HTTPException(403, ...)` sits **inside the `try`** and is
   swallowed by the trailing bare `except Exception` whose comment reads *"Don't fail on timestamp
   issues but log for monitoring"*.

So no replay window is enforced by this repository. Stripe's SDK applies its own default tolerance
inside `construct_event` *(inferred — library behaviour, not code here)*. **Razorpay has no
timestamp binding at all**: its HMAC covers the body only, so a captured signed body is replayable
indefinitely once the Redis key expires. **Severity: P3 standalone, P2 compounding CD-3 and CD-5.**

#### CD-5 — Webhook idempotency is Redis-only, 24 h, and degrades to a per-process dict.

Two-phase lock at `billing.py:2335-2349` / `2841-2855`, completion at `2802-2812` with `ex=86400`.
Ordering is correct (signature verified before the lock is acquired). But:

* `tests/conftest.py:13` sets `REDIS_URL = ""` unconditionally, so the default lane uses
  `MockRedisClient`, *"a plain dict on a module-global instance -- per process, so `SET NX` always
  succeeds. That is BUG-027"* (quoted from `tests/test_webhook_duplicate_delivery_real_redis.py:54-67`).
  With no Redis or multiple pods, dedupe is a no-op.
* The 24 h TTL is shorter than Stripe's retry schedule *(inferred)*. A redelivery past it
  re-executes: a **second `billing_invoices` row** and a **second `process_referral_commission`
  credit**. Nothing in `billing.py` references a DB unique index on
  `(provider, provider_payment_id)` for `billing_invoices`. *(Not verified against migrations.)*
* `if idempotency_key:` guards everything — **no event id means no dedupe at all**.
* `increment_ml_addon` (`1361`) is not idempotent; the tier write naturally is.
* Two writes **are** DB-idempotent regardless of Redis: `referral_obligations` under a unique
  constraint on `(provider, provider_payment_id)`, and marketplace settlement under
  `uq_settlement_reference_reversal` with `DUPLICATE_IGNORED` treated as success (`985-1008`).

**Severity: P2 (financial duplication), and it is a money finding as much as a tenancy one.**

#### CD-6 — `bot_ownership.py` connects with `autocommit = True`.

`:373-375`, `:407-410`, `:457-458`. With autocommit there is no explicit transaction, so
`apply_tenant_context` would raise `TenantContextNotTransactional` there by design — correct refusal,
but it means this worker path cannot adopt the GUC without restructuring its transaction boundaries.
**Severity: P3, but it is a dependency of any "workers preserve tenant boundaries" claim.**

### Unverified risks (cannot be settled without authorization)

| # | risk | why unverified |
|---|---|---|
| UR-1 | The production `DATABASE_URL` role's actual name, `rolsuper`, `rolbypassrls` | requires connecting to production. **Forbidden by this brief.** Only `scripts/verify_rls_enforcement.py` run against the real DB settles it. |
| UR-2 | Actual `pg_class.relowner` and `relforcerowsecurity` per table | same |
| UR-3 | Whether `transactional_execution_manager` / `tenant_rls_validator` are reachable from any request path | I confirmed only that `get_db`/`get_db_context`/`dependencies.py` do not set the GUC |
| UR-4 | Whether `billing_invoices` has a DB unique constraint on `(provider, provider_payment_id)` | not referenced in `billing.py`; migrations not read |
| UR-5 | What `marketplace/settlement_service.py` actually writes | taken from docstrings and a test double; source not read |
| UR-6 | The four unread `test_billing*` files' DB backing | off-path in every webhook grep, contents unverified |

### Stale historical findings — do not re-cite as current evidence

* **"13 tables probed, 0 cross-tenant leaks" (Phase 4)** measured the **PostgREST/`authenticated`**
  path. It is not evidence about the ORM path and should not be read as closing CD-1.
* **"TenantMiddleware is not installed"** — still true, and `main.py:918-923` records deliberately
  that `core/tenant_middleware.py` is NOT mounted, including that its `startswith` skip-path shape
  would have been a CVE-2026-48710 authentication-skip vector. Installing it remains the **wrong**
  action, as Phase 4 concluded.

---

## Tenant-path matrix

| path | tenant identity source | trusted? | DB role | bypasses RLS? | GUC set? | missing/malformed identity | cross-tenant negative test | PG-tested? | severity |
|---|---|---|---|---|---|---|---|---|---|
| HTTP routes via PostgREST anon+JWT | verified JWT → `auth.uid()` | **yes** | `authenticated` | no | n/a (uses `auth.uid()`) | fails closed (no rows) | `test_tenant01_cross_tenant_matrix_postgres.py`, `test_cross_tenant_read_sweep_postgres.py` | **yes** | — |
| HTTP routes via SQLAlchemy `get_db` | app `WHERE user_id=...` only | code-level | single `DATABASE_URL` role | **effectively yes** (CD-1) | **no** | depends on handler | none on the ORM path | no | **P1 prod** |
| HTTP routes via service-role PostgREST | caller-supplied in some flows | varies | `service_role` | **yes, by design** | n/a | varies | `test_supabase_callers_tenant_isolation.py` (mocks) | partial | P2 |
| **Stripe webhook** | `client_reference_id` / metadata `user_id` | **NO — payload** | `service_role` | **yes** | no | 400 on absent; **no UUID check** | **none** | **no** | **P2** |
| **Razorpay webhook** | `notes.user_id` | **NO — payload** | `service_role` | **yes** | no | 400 on absent; **no UUID check** | **none** | **no** | **P2** |
| Marketplace settlement sub-path | payload `subscription_id`; `user_id` only logged | **NO — payload** | `service_role` | **yes** | no | 400 if context missing | none correlating the two | no (mocks) | **P2** |
| `bot_ownership` claim/heartbeat | `user_id` argument | caller-dependent | single role, **autocommit** | yes (CD-1) | **no, and cannot be** (CD-6) | — | `test_work03_bot_ownership_claims_postgres.py` | yes (claim logic, not tenancy) | P3 |
| `marketplace_expiry_worker` | none — sweeps all tenants | n/a | `service_role` | **yes** | no | — | none | no | P3 |
| `api_key_vault` (credential vault) | Python `credential_dict.get("tenant_id") != tenant_id` | code-level | `service_role` | **yes** | no | denies | `test_tenant01_credential_vault_isolation.py` | *(unverified backing)* | P2 |
| Exchange/order execution | `transactional_execution_manager` sets `SET LOCAL` | yes where reached | single role | yes (CD-1) | **yes, at that one site** | — | `test_exchange_executor_cache_is_per_tenant.py` | partial | P2 |

---

## Current tests and their limitations

22 test files match `tenant|rls`. The ones that matter here, with what each actually establishes:

| test | backing | establishes | does **not** establish |
|---|---|---|---|
| `test_tenant01_cross_tenant_matrix_postgres.py` | **real PG** | cross-tenant denial under `anon`/`authenticated` | anything about the ORM path |
| `test_cross_tenant_read_sweep_postgres.py` | **real PG** | breadth sweep across readable tables | same |
| `test_billing_data_isolation_postgres.py` | **real PG** | RLS + grants on `invoices`/`payment_methods`/`subscriptions`; **affirmatively asserts `service_role` is BYPASSRLS** | the webhook path — and it documents that that path bypasses RLS |
| `test_tenant02_fee_records_rls_postgres.py` | **real PG** | `fee_records` RLS after remediation | — |
| `test_tenant_db_context.py` | **unit, SQLite/mocks** | the module's refusals and canonicalisation | that it is wired to anything |
| `test_tenant_db_context_postgres.py` | **real PG** | `set_config` behaviour, transaction scoping | that any request path calls it |
| `test_verify_rls_script.py` | unit | the auditor script's own logic | live DB state |
| `test_tenant01_credential_vault_isolation.py` | unverified | vault denial + negative control | — |
| `test_webhook_duplicate_delivery_real_redis.py` | **real Redis, no DB** | the Redis idempotency lock; the **only** test that executes a webhook handler | settlement, entitlement, or tenant identity — it says so itself (`:20-27`) |
| `test_billing_e2e.py` | **mocks + source-text asserts** | branch behaviour; that control *names* appear in the source (`assert control in source`, `:1757-1762`) | that the controls hold |
| `test_validation_sweep.py` | static | — | **excludes both webhooks** from the auth matrix (`:1623-1626`) |

**The gap that matters most: no test anywhere asserts that webhook tenant identity is correct.**
Nothing feeds a signed event carrying tenant B's `user_id` and asserts the write landed on the right
tenant or was refused. And no test exercises cross-tenant denial **through the ORM path**.

**Lane note:** `AERORA_TEST_DATABASE_URL` and `DATABASE_URL` are both **unset** in this environment.
`staging/pg_dsn.txt` holds `postgresql://postgres:@127.0.0.1:51805/postgres` from a prior
`pgserver` instance; that port is reallocated on every start, so it is **presumed stale** and was
not connected to. Every PG-backed test above therefore **skips** here.

---

## Phase 2 — minimal remediation proposal (PROPOSED ONLY, NOT APPLIED)

Ordered so that no step can create a silent-empty-read window. **Steps 1–4 must land in this
order.** Each is independently verifiable.

| # | step | files | authorization needed | overlaps another workstream? |
|---|---|---|---|---|
| **1** | **Wire the GUC on the request path.** Adopt `tenant_db_dependency()` on tenant-scoped routers, or set the GUC inside `get_db` after opening a transaction. No role change, no migration. Reversible per router. | `core/tenant_db_context.py` (staged by others), routers | low — but see overlap | **YES — `tenant_db_context.py` is staged by another workstream** |
| **2** | **Provision a non-owner, non-superuser login role without `BYPASSRLS`**, and add a second DSN (e.g. `APP_DATABASE_URL`) that `database_pool.py` prefers while migrations keep the owner DSN. | `scripts/provision_database.py`, `core/config.py`, `core/database_pool.py` | **HIGH — role creation + grants. Explicitly out of scope without authorization.** | `config.py` (+401/−33); `database_pool.py` is **clean** |
| **3** | **Stop the app creating tables.** Gate `Base.metadata.create_all` at `main.py:795` behind an explicit env flag, default off, so the app role stops becoming owner of new tables — and the swallowed-exception warning stops hiding partial creates. | `backend_app/main.py` | medium (startup behaviour) | `main.py` (+289/−36) |
| **4** | **`FORCE ROW LEVEL SECURITY`, one table at a time**, only after 1–3 verify green. | a new migration | **HIGH — migration. Not to be applied without authorization.** | shared migration surface |
| **5** | **Webhook identity from a trusted mapping.** Resolve the tenant from a stored row keyed by the provider's `customer`/`subscription` id rather than from `client_reference_id`/`notes.user_id`; at minimum apply `_validate_uuid` to `user_id` (the helper already exists at `:548-562`) and assert `subscription_id` belongs to `user_id` before settling. **Preserve** signature verification, the IP allowlist, the Redis lock, and provider-retry semantics. | `backend_app/routers/billing.py` | medium | **YES — `billing.py` has +700/−40 unstaged** |
| **6** | **Fix `_validate_webhook_timestamp`** so it can reject: move the `raise` outside the `try`, parse Stripe's `t=` field rather than the whole header, and add a Razorpay timestamp binding. | `backend_app/routers/billing.py` | low | **YES — same file** |
| **7** | **Back webhook idempotency with a DB constraint** so dedupe survives Redis loss and the 24 h TTL. | migration + `billing.py` | **HIGH — migration** | same |

**Smallest genuinely safe step available today:** **step 3 alone is not safe** (`main.py` contended),
and steps 2/4/7 need authorization. So the smallest safe step is **step 1 on a single read-only
router**, and even that requires the `tenant_db_context.py` owner's sign-off. **I recommend
coordinating with that workstream rather than opening a parallel edit.**

### What must NOT be done

* **Do not apply `FORCE ROW LEVEL SECURITY` before step 1.** With the GUC unset,
  `current_setting(..., true)` is NULL, the predicate is NULL, and enforced reads return **zero
  rows** — `orders`, `positions`, `fills` go silently empty rather than erroring. Both
  `tenant_db_context.py:12-18` and `verify_rls_enforcement.py:565-570` state this, and the script
  exits non-zero specifically to prevent it.
* **Do not install `core/tenant_middleware.py`.** Phase 4 measured that as the wrong action, and
  `main.py:918-923` records that its `startswith` skip-path shape is an authentication-skip vector
  under CVE-2026-48710.
* **Do not treat `FORCE` as sufficient while the role may be superuser/BYPASSRLS** — `FORCE`
  removes only the owner's exemption, never a role attribute.

---

## Phase 3 — verification plan (designed, not run)

Reusable as-is: `test_tenant01_cross_tenant_matrix_postgres.py`,
`test_cross_tenant_read_sweep_postgres.py`, `test_billing_data_isolation_postgres.py`,
`test_tenant_db_context_postgres.py`, and `scripts/verify_rls_enforcement.py` (which already
answers checks 2 and 3 below and exits non-zero correctly).

| # | claim | how | new or existing |
|---|---|---|---|
| 1 | tenant A cannot read/update/delete tenant B's rows **through the ORM** | seed two tenants as owner; open a `tenant_db_session(A)`; SELECT/UPDATE/DELETE B's row; assert 0 rows / 0 rowcount | **new**, real PG |
| 2 | missing or invalid tenant context fails closed | `get_db` with no GUC under forced RLS → 0 rows, and `apply_tenant_context(None/''/'not-a-uuid')` raises `TenantContextUnavailable` | partly `test_tenant_db_context_postgres.py` |
| 3 | the app role cannot bypass enforced RLS | `scripts/verify_rls_enforcement.py` — asserts `rolsuper`/`rolbypassrls` false and no `owner_exempt` table | **existing** |
| 4 | pooling does not leak tenant context between requests | two sequential sessions on the same pooled connection; assert `read_tenant_context()` is `None` at the start of the second (this is what `is_local=true` buys) | **new**, real PG |
| 5 | webhook identity comes from a validated mapping | signed event carrying tenant B's `user_id` while the provider customer maps to tenant A → assert refused, and that **no** write landed on either tenant | **new** |
| 6 | background workers preserve tenant boundaries | drive `marketplace_expiry_worker` / `bot_ownership` with two tenants seeded; assert no cross-tenant write | **new**, real PG |
| 7 | legitimate same-tenant behaviour intact | the existing four-module + full suites must stay green | **existing** |

**Commands** (the repository's existing no-database and PG lanes):

```
# unit lane (no DB) - must stay green
.venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider -p no:warnings -o addopts=""

# PG lane - requires a PostgreSQL AERORA_TEST_DATABASE_URL (currently UNSET here)
$env:AERORA_TEST_DATABASE_URL = "postgresql://..."
.venv\Scripts\python.exe -m pytest tests/test_tenant01_cross_tenant_matrix_postgres.py `
    tests/test_cross_tenant_read_sweep_postgres.py `
    tests/test_billing_data_isolation_postgres.py `
    tests/test_tenant_db_context_postgres.py -q -o addopts=""

# the role/ownership auditor (read-only; SELECT + a rolled-back SET LOCAL)
.venv\Scripts\python.exe scripts\verify_rls_enforcement.py
```

**Evidence required for closure of TENANT-01:**

1. `scripts/verify_rls_enforcement.py` exits **0** against the target database — no SUPERUSER, no
   BYPASSRLS, no owner-exempt table, no policy-coverage gap, no `tenant_id` type mismatch.
2. A real-PostgreSQL test proving a tenant-scoped ORM session cannot read another tenant's row with
   RLS **forced** (check 1).
3. A real-PostgreSQL test proving a context-less session reads **zero** rows rather than all rows
   (check 2).
4. A connection-reuse test proving no GUC leaks across a pooled handover (check 4).
5. A webhook identity test (check 5).
6. The unit lane and the PG lane both green, with pre-existing xfails/xpasses reported unchanged.

Mocked tests do not count as PostgreSQL evidence. No assertion may be weakened and no skip added to
make any of the above green — a required PG assertion satisfied by a skip is not satisfied.

---

## TENANT-01 disposition

**OPEN — P1 for production, P2 for restricted paper. Unchanged by this audit.**

* The **mechanism is confirmed** by reading (CD-1): enabled-not-forced policies, owner-role ORM
  connection, a likely-superuser single role, and no tenant GUC on any request path. Four
  independent reasons, each sufficient on its own.
* The **live state is UNVERIFIED** (UR-1, UR-2) and cannot be verified under this brief, which
  forbids production database access. "Code-verified, live-unverified" is the honest status.
* **No open cross-tenant leak is demonstrated.** Application-level `WHERE` clauses carry isolation
  today, and the breadth sweep found 0 leaks — on the PostgREST path. What is missing is the
  defence-in-depth layer beneath them.
* **Two newly-characterised items in this audit** that the register does not currently carry at this
  resolution: **CD-4** (`_validate_webhook_timestamp` cannot reject anything — two independent
  reasons) and **CD-5** (idempotency is Redis-only with a 24 h TTL over a per-process mock in the
  default lane, with no DB constraint behind it for `billing_invoices`). CD-5 is a **financial
  duplication** finding as much as a tenancy one. Both are reported here; **the register was not
  edited.**
* **Remediation is blocked on ownership, not analysis.** `tenant_db_context.py` — the fix itself —
  is staged by another workstream, and `billing.py`, `main.py` and `config.py` all carry large
  unstaged diffs from others. Steps 2, 4 and 7 additionally need explicit authorization for a role
  change or a migration.

### Immediate next action requested

1. **Coordinate with the workstream that owns the staged `tenant_db_context.py`** — the wiring step
   belongs to them, and a parallel edit would conflict. I recommend they take step 1.
2. **Authorize or decline** the non-owner role (step 2) and the `FORCE RLS` migration (step 4). Both
   are production-track and neither is safe without step 1.
3. **Confirm whether CD-4 and CD-5 should be opened as their own findings** (my recommendation: yes
   — CD-5 has a money consequence independent of tenancy) and whether a PostgreSQL lane
   (`AERORA_TEST_DATABASE_URL`) can be provisioned here, since every PG-backed check currently
   skips.

> Property and integration test coverage described in this report does not establish exchange
> correctness, exactly-once execution, paper-trading certification, or live-trading readiness.
> **Live trading remains OFF / NOT CERTIFIED.**
