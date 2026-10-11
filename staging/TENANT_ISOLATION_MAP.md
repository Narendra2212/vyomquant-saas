# TENANT ISOLATION MAP — Aerora / VyomQuant

Deliverable for TENANT-01. Every claim is either read from the live PostgreSQL catalogue
or from code at a named file:line. Where something could not be established, it says so.

Evidence artifacts: `staging/tenant_probe_RUNZ6.txt` (catalogue),
`staging/authuid_RUNB8.txt` (`auth.uid()` definition and the `orders` policies),
`staging/identity_RUNF3.txt` (identity/enum/check requirements),
`staging/tenant_matrix_RUNH5.txt` (the negative matrix result).

---

## 0. HEADLINE

**There is no live cross-tenant vulnerability on the request path, and the unused tenant
framework would not have prevented one.** The audit finding "the tenant dependency family
is unwired" is factually correct but was the wrong thing to be alarmed about.

Measured against the live database: **13 tenant-scoped tables probed, 0 cross-tenant
leaks** for read, update and delete, with the victim row verified unchanged afterwards.

The genuine residual risk is elsewhere and is documented in §5: **no table has
`FORCE ROW LEVEL SECURITY`, and the application's SQLAlchemy connection is the table
owner**, so on that path RLS is skipped entirely and application-level `WHERE user_id = …`
is the only control.

---

## 1. THE ACTUAL MODEL

### There is no tenant above the user

`backend_app/core/dependencies.py:318`:

```python
tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id
```

In practice `tenant_id == user_id`. A "tenant" in this product **is** a user. This is the
single most important fact for the decision in §6: a multi-tenant framework is modelling a
hierarchy that does not exist.

### Two layers, and which one is load-bearing depends on the client

**Layer B — database RLS via `auth.uid()`.** Verified on the live database
(`staging/authuid_RUNB8.txt`):

```sql
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
$$
```

A request arrives through PostgREST as role `authenticated` with `request.jwt.claim.sub`
set from the JWT. The per-request client is built at
`core/dependencies.py:219-279` (`create_request_supabase_async`, the hot path) and
`:143-172` (sync), exposed as `get_request_supabase` (`:283-296`, 46 `Depends`) and as
per-router `_sb(user)` helpers (`billing.py:179`, `strategies.py:772`, `risk.py:92`,
`signals.py:60`, `paper_trading.py:558`).

`get_supabase()` (`:102-141`) returns the **anon** singleton and documents at `:105-106`
that it is not for RLS-scoped table access. It is only what `get_current_user` depends on.

**Layer A — application `.eq("user_id", user["id"])`.** Present on nearly every
tenant-scoped read and write, and it is the **only** layer on the service-role and
SQLAlchemy paths.

---

## 2. PER-SUBSYSTEM MECHANISM

Mechanism key: **B** = RLS via `auth.uid()`; **A** = application ownership check;
**C** = service-role / owner access that bypasses RLS; **D** = none/ambiguous.

RLS column verified against `pg_policy` on the live database.

| Subsystem | Mechanism | RLS predicate (live) | Caller-supplied id? | Matrix verdict |
|---|---|---|---|---|
| Strategies | B + A | `auth.uid()` | no | **DENIED** |
| Strategy versions | B + A | `auth.uid()` + a no-predicate policy | no | not probed (no owner column — ownership is via `strategies.user_id`) |
| Deployments / bots | B + A | `auth.uid()` | no | not probed (needs a parent strategy) |
| Signals | B + A | `auth.uid()` | no | not probed (FK) |
| **Exchange credentials** | **C** + A | `auth.uid()` | no | **DENIED** |
| Orders | B + A | `auth.uid()` **+ inert + service_role** | no | **DENIED** |
| Positions | B + A | `auth.uid()` + inert + service_role | no | **DENIED** |
| Fills | B + A | `auth.uid()` + inert + service_role | no | **DENIED** |
| Execution records | B + A | `auth.uid()` + inert + service_role | no | not probed (FK) |
| Risk settings | **C** + A | `auth.uid()` | no | **DENIED** |
| Profiles | B + A | `auth.uid()` | **yes — webhook** | seeded by the harness |
| Billing invoices | **C** | `auth.uid()` + service_role | **yes — webhook** | **DENIED** |
| Payment methods | B + A | `auth.uid()` + service_role | no | **DENIED** |
| Referrals | **C** (deliberate) + A | `auth.uid()` + service_role | **yes — `code` in body** | not probed (FK / auto-provisioned) |
| Marketplace | **C** + A | `auth.uid()` on `library_strategies` etc. | listing ids, re-verified | **DENIED** (`library_strategies`) |
| Notifications | B + A | `auth.uid()` | no | **DENIED** |
| Security logs | B + A | `auth.uid()` | no | **DENIED** |
| Training jobs / models | B + A | `auth.uid()` | no | not probed (FK) |
| Paper sessions/accounts | B + A | `auth.uid()` — the reference implementation | no | **DENIED** |

**Correction to an earlier assumption.** The audit treated
`orders`/`positions`/`fills`/`execution_records` as having only the inert
`current_setting('app.current_tenant_id')` policy. The live catalogue shows **three**
policies each: a real `*_owner_access` for `authenticated` on `auth.uid() = tenant_id`, a
`*_service_role` allow-all, and the inert one. Permissive policies are OR'd, so the
`auth.uid()` policy governs for a request and the inert one contributes nothing. They are
**not** unprotected on the request path.

**Five tables genuinely rely solely on the inert predicate**: `dag_tasks`,
`idempotency_keys`, `transaction_records`, `transaction_checkpoints`,
`reconciliation_mismatches`. Because nothing sets that setting, an `authenticated` caller
matches **no row** — so this **fails closed**, not open. The policy is decorative and the
real control on those tables is the application's `WHERE`. Pinned by
`test_no_table_relies_solely_on_the_inert_tenant_setting` so the list cannot grow
unnoticed.

---

## 3. APPLICATION-LEVEL vs DATABASE-LEVEL

**Database-level (RLS actually bites):** every table in §2 showing an `auth.uid()`
predicate, **when reached through a request-scoped PostgREST client**. Verified by the
matrix: 13/13 denied.

**Application-level only:**
- the five inert-predicate tables above (fails closed for `authenticated`, so the `WHERE`
  is the control)
- every path using a service-role client (§4)
- every path using SQLAlchemy as the owner (§5)

---

## 4. SERVICE-ROLE / RLS-BYPASSING PATHS

| Path | Reaches | `user_id` predicate in the query? | What constrains it |
|---|---|---|---|
| `billing.py:187-194 _background_sb()` | `profiles`, `billing_invoices` | yes — `.eq("id", user_id)` `:1085`; `rpc(target_user_id)` `:1101` | **The webhook payload.** HMAC signature + IP allowlist |
| `library.py:146-160 _get_service_client()` | all marketplace tables | yes at every owner-scoped site (~40) | `_safe_uuid(user["id"])`, token-derived |
| `referral.py:47-85` | `referral_codes` | **no** — filters on `code` only | caller must know the code; reasoned at `:66-73` |
| `backend/api_key_vault.py:75-82` | `exchange_keys` (encrypted credentials) | yes — `.eq("user_id", user_id)` | **a Python argument only** |
| `core/risk_manager.py:111-116` | `risk_settings` | yes; documented as intentional at `:90-93` | Python argument + `_log_service_role_access` |
| `workers/marketplace_expiry_worker.py` | marketplace subscriptions | **no — cross-tenant by design** | nothing; it is a sweep |
| `core/supabase_connection.py` via `auth.py:198-220` | referral tables, `profiles` | mixed | registration flow only |

**Highest-consequence item: `api_key_vault`.** Encrypted exchange credentials behind a
service-role client whose only isolation is the `user_id` argument. One wrong caller is a
cross-tenant credential read. The matrix shows `exchange_keys` DENIED for the
`authenticated` path, which is the RLS layer — it does **not** exercise the vault.

---

## 5. PATHS THAT BYPASS RLS ENTIRELY

**Verified on the live database:** `tables with FORCE ROW LEVEL SECURITY: 0`.

PostgreSQL skips RLS for a table's owner unless `FORCE ROW LEVEL SECURITY` is set. The
application's SQLAlchemy connection is `postgres` (the owner of every table). Therefore:

> On the SQLAlchemy path, **no RLS policy applies to any table**, and application-level
> `WHERE user_id = …` is the complete isolation story.

SQLAlchemy call sites reaching tenant-scoped tables: `main.py:392` (order watchdog),
`:410` (PnL engine), `backend/dag_worker.py:208`/`:361`/`:589`,
`backend/task_recovery.py:132`/`:231`/`:454` (a deliberate cross-tenant stuck-task scan),
`backend/startup_recovery.py:188`, and the QuestDB/telemetry reads at
`routers/orders.py:1068`/`:1071` (parameterised on `user["id"]`).

**Do not turn `FORCE` on yet.** `core/tenant_db_context.py:4-20` warns why: enabling it
before something sets a tenant setting converts every owner-path read into a silently
empty result set. The correct order is: wire the setter, then add owner-applicable
policies, then force.

---

## 6. CALLER-SUPPLIED TENANT IDENTITY

| # | Where | Verdict |
|---|---|---|
| 1 | **Billing webhooks.** `billing.py:2099` (`client_reference_id`), `:2245`, `:2290`, `:2353` (Stripe metadata), `:2507` (`notes.get("user_id")`, Razorpay) → `_apply_billing_entitlement(user_id, …)` → `_background_sb()` → `profiles` write | **The only caller-supplied id reaching a privileged write.** No ownership check is possible — there is no authenticated caller. Control is HMAC + IP allowlist. Means a signing-secret compromise is a *write-any-tenant* primitive. **Recommendation (not yet implemented): assert the `user_id` exists in `profiles` and that the gateway customer maps to it.** |
| 2 | WebSocket `user_id` query/path param | **Safe.** `_resolve_ws_subject(claimed_user_id=…)` rejects on `subject != claimed_user_id` and closes 4001; the channel name is built *after* the check |
| 3 | `distributed_execution.py:308` admin tenant override | **Safe.** Gated on `app_metadata.role`, the non-escalatable location |
| 4 | `referral.py:646-654` code lookup | **Accepted by design**, reasoned in-code |
| 5 | Resource ids (`library_id`, `deployment_id`, …) | Re-verified against `user["id"]` at every site examined. Not exhaustively enumerated across all 265 handlers |

---

## 7. THE NEGATIVE MATRIX

`tests/test_tenant01_cross_tenant_matrix_postgres.py` — **11 passed**.

Real PostgreSQL. Impersonates a PostgREST request exactly: `SET LOCAL ROLE authenticated`
plus `set_config('request.jwt.claim.sub', <tenant>, true)`. No FakeSupabase.

**A 403 is never accepted as evidence.** Every negative case asserts *zero rows affected*
**and** re-reads the victim row **as the table owner** to prove no mutation landed.

Result (`staging/tenant_matrix_RUNH5.txt`): **13 probed, 0 leaks.**

```
billing_invoices  exchange_keys  fills  library_strategies  notifications
orders  paper_accounts  paper_sessions  payment_methods  positions
risk_settings  security_logs  strategies            -> all DENIED
```

Three controls make that meaningful:

1. **Positive** — tenant A can read its own rows. Without it, a database denying
   everything would look isolated.
2. **Detection, weakened policy** — a scratch table with `USING (true)` is probed by the
   same harness, which must report the leak. If it does not, the harness is blind.
3. **Detection, RLS off** — a scratch table with RLS disabled, same requirement.

16 tables were not probed and **the reason is reported for each** rather than hidden:
mostly second-order FKs needing a chained parent (`strategy_id`, `library_id`,
`listing_id`, `session_id`), plus `strategy_versions` (ownership is via
`strategies.user_id`, not a local column) and `profiles`/`referral_codes` (already created
by the identity seed — note a `profiles` insert auto-provisions a `referral_codes` row).
**This is a coverage gap in the test, not a finding about the product.**

---

## 8. THE DECISION

> **Formally deprecate `core/tenant_middleware.py`, `core/tenant_db_context.py` and
> `core/entitlement_dependencies.py`. Do NOT install `TenantMiddleware`. Keep
> `core/tenant.py`.**

Reasoning:

1. **`core/tenant.py` is live production code** — imported by `backend/dag_worker.py:27`,
   `core/hard_quota_enforcer.py:34`, `core/entitlement_engine.py:23`,
   `core/dag_task_queue.py:14`, `backend/ml_training_policy.py:134`. "Delete the tenant
   framework" must not mean deleting it. The other three have **zero** production
   importers.

2. **It models the wrong shape.** `dependencies.py:318` collapses `tenant_id` to
   `user_id`. A `TenantContext` extracted by middleware would carry the value
   `user["id"]` already carries, through a second code path with a second set of bugs —
   `tenant_middleware.py:57-59` documents a CVE-class `startswith` auth-skip bug it
   already had.

3. **Installing it would close nothing.** The real gap is §5: owner-path RLS bypass.
   `TenantMiddleware` sets `request.state.tenant`; it does **not** call
   `set_config('app.current_tenant_id', …)`. Only `tenant_db_context.py` does, and that is
   a session helper, not middleware. Middleware alone buys the affected subsystem nothing.

4. **The measured model holds.** 13/13 denied, with positive and detection controls.
   Replacing a working ownership model with an invented tenant model is the risk here, not
   the reward.

### What to do instead, in priority order

| # | Action | Why |
|---|---|---|
| 1 | Constrain `api_key_vault` — pass the request-scoped client, or assert `user_id == authenticated subject` | Encrypted credentials with a Python argument as the only isolation |
| 2 | Validate the webhook `user_id` against `profiles` and the gateway customer mapping | Turns a signing-secret compromise from write-any-tenant into write-that-customer |
| 3 | Harvest `tenant_db_context.py`'s `set_config` logic into the SQLAlchemy session factory, then add owner-applicable policies, **then** `FORCE ROW LEVEL SECURITY` | Closes §5 in the only safe order |
| 4 | Give the five inert-predicate tables an `auth.uid()` policy | They currently fail closed; make the policy mean something |
| 5 | Add a repository seam in `library.py` that cannot issue an owner-scoped statement without a `user_id` | ~40 load-bearing `.eq()` calls is a lot of places to be right |
| 6 | `paper_trading_service.py:290-297` — make the anon-singleton fallback raise instead of degrading silently | Fails closed today, but silently |
| 7 | Revoke `anon` write grants on `orders`/`positions`/`fills` | `anon` holds DELETE/INSERT/SELECT/UPDATE. Inert (no matching policy) but the same pattern as the already-fixed RLS-03 |

**None of 1–7 is implemented yet.** They are the output of this map, not its conclusion.

---

## 9. STATUS

```
TENANT-01: DOWNGRADED from P1 to P2, NOT CLOSED
```

Downgraded because the feared defect — exploitable cross-tenant access on the request path
— is **measured absent** (13/13 denied, with controls proving the measurement works), and
because the prescribed fix (wire the framework) is now shown to be the wrong action.

**Not closed** because: the owner-path RLS bypass (§5) is real and unaddressed; the vault
and webhook hardening in §8 are unimplemented; and 16 of 29 target tables were not probed.

---

# PHASE 7 ADDENDUM — TENANT-02: `fee_records` has no row level security

> Everything above is the Phase 4 record, preserved as written. That record probed **13
> tables** and found **0 cross-tenant leaks**, which was true of those 13 tables. This
> section is a measurement over **44–70** tables, taken for the first time in this
> engagement, and it found one.

---

## Why this was never seen before

The PostgreSQL-only suites **skip** in the repository's primary CI lane, because that lane
runs with no `DATABASE_URL` and no `AERORA_TEST_DATABASE_URL`. Phase 6's full regression ran
that lane only. Phase 7 ran a **second lane** with a provisioned PostgreSQL 16.2, which
converted **402 skips into runs** — and three of those runs independently report the same
leak.

That is the whole argument for the brief's instruction not to report a suite as green while
tests are skipped for want of infrastructure. One lane would have shipped this.

## What three independent tests say

| Test | Finding |
|---|---|
| `test_cross_tenant_read_sweep_postgres::test_anon_cannot_read_any_customer_row` | `fee_records   anon saw 1 of 1 row(s)` — with **43** other tables in the BLOCKED column |
| `test_cross_tenant_read_sweep_postgres::test_authenticated_cannot_read_another_customers_row` | `fee_records   authenticated saw 1 of 1 row(s)` — with **69** other tables BLOCKED |
| `test_production_security_probe_rolls_back_postgres::test_it_actually_measured_something` | `fee_records (b_select=ALLOWED; b_update=ALLOWED; b_delete=ALLOWED)` |

So: **one table out of 44/70**, not a general RLS failure — and on that one table the other
tenant can not only read but **update and delete**.

Confirmed directly against the server (`staging/probe_fee_records_exposure.py`):

```
fee_records          rls_enabled=False forced=False
invoices             rls_enabled=True  forced=False
paper_orders         rls_enabled=True  forced=False
strategies           rls_enabled=True  forced=False

(no policies on fee_records)

fee_records   anon           SELECT / INSERT / UPDATE / DELETE
fee_records   authenticated  SELECT / INSERT / UPDATE / DELETE

PUBLIC_TABLES_WITHOUT_RLS=8
WITHOUT_RLS_SAMPLE=alembic_version, fee_records, plan_features, plan_limits,
                   plan_migration_mapping, plan_prices, plans, schema_migrations
```

**Seven of those eight are not this finding.** `alembic_version` and `schema_migrations` are
migration bookkeeping; `plans`, `plan_features`, `plan_limits`, `plan_prices` and
`plan_migration_mapping` are reference catalogues. None holds per-user rows, and RLS-off on a
public price list is a normal, deliberate choice. `fee_records` is the only one of the eight
that carries customer data, and it carries a `tenant_id` column.

## Root cause, and it is structural

`fee_records` exists **only as a SQLAlchemy model** —
`backend_app/backend/fee_engine.py::FeeRecordModel` — and is created by `main.py`'s
`Base.metadata.create_all()`.

**Measured: zero `.sql` files anywhere in this repository mention `fee_records`** (every
migration and script, excluding `.venv`, `node_modules` and `pgdata`). So there is no
`ALTER TABLE fee_records ENABLE ROW LEVEL SECURITY` and no policy for it *anywhere* —
because those statements live in the migrations, and this table has none.

Every migration-created table gets them. A `create_all()`-created table cannot.

This is the same ORM-versus-migration divergence family that
`tests/test_orm_models_match_provisioned_schema.py` exists for, in the direction nobody had
connected to security. That file's docstring describes the divergence as *"the ORM silently
binds to a table it cannot read"*. This is the mirror image: **the ORM created a table the
security model never saw.**

## What is measured, and what is not

**Measured:** the leak, on the local verification substrate provisioned by
`scripts/provision_database.py --create-substrate`.

**Not measured:** production. This engagement has had no production DSN in any phase, so the
production state is **unverified** and is not claimed either way.

**But the mechanism is repository-level, not substrate-level.** The missing RLS statement is
missing from the *repository*, so a production database built from these migrations also has
no RLS on this table. The only part that could legitimately differ is the GRANT — and
Supabase's default privileges on the `public` schema grant `anon` and `authenticated` by
default, which is the direction that makes it worse rather than better.

The honest summary: **the absence of RLS on `fee_records` is a fact about this repository.
Whether a particular deployment also grants `anon` access to it is a fact about that
deployment, and this audit cannot see one.**

## Reported, not fixed

The repair is a migration that creates the table properly, enables RLS and adds an owner
policy keyed on `tenant_id`. That is a deliberate schema change, and it needs a decision
this audit cannot make for the owner: **should `fee_records` be reachable through PostgREST
at all?** If the answer is no, revoking the grant is the smaller and better fix than adding a
policy.

It is also not something to write at the end of a regression run, against the database that
regression is measuring.

## One more thing the sweep is right about

`test_every_readable_table_was_actually_exercised[anon]` fails separately, and for a reason
worth preserving:

```
1 table(s) could not be seeded for role anon, so isolation was NOT measured for them:
    risk_settings   duplicate key value violates unique constraint "risk_settings_user_id_key"
```

The sweep **refuses to claim coverage it did not achieve**. That is a sweep-fixture defect
(classified TEST DEFECT), not a leak — and it is exactly the behaviour that makes the other
two assertions in the same file worth believing.

---

# PHASE 8 — 2026-10-09. TENANT-02 CLOSED: `fee_records`

**Substrate: LOCAL PostgreSQL 16.2 (pgserver). No production database was connected to in
any phase of this audit. Everything below is local-substrate evidence.**

## What was wrong, and why the existing backstops did not catch it

`fee_records` carries per-customer trading fees keyed on `tenant_id`. On the provisioned
database it had **ROW LEVEL SECURITY DISABLED**, zero policies, and `anon` and
`authenticated` each holding `SELECT, INSERT, UPDATE, DELETE`. Three independent Phase 7
PostgreSQL-lane assertions agreed, and they were precise rather than sweeping — 43 and 69
other tables blocked correctly in the same sweeps.

The root cause was structural rather than an oversight in a policy list:

| Fact | Consequence |
|---|---|
| The table existed **only** as a SQLAlchemy model (`fee_engine.FeeRecordModel`), created by `Base.metadata.create_all()` at application startup | Zero `.sql` files in the repository mentioned it, so the `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY` pair that every migration-created table receives did not exist for it anywhere |
| `scripts/provision_database.py` runs `ALTER DEFAULT PRIVILEGES … GRANT ALL ON TABLES TO anon, authenticated, service_role` | The grants were applied **at creation time**, without the table ever being named. Re-running the hardening migration would not retract them — a per-table `REVOKE` is required |
| `migrations/hardening/999_privilege_hardening.sql` section 4 already has a backstop that `RAISE`s on any table carrying a per-customer column, and `tenant_id` is already in its list | It enumerates `pg_class` during provisioning **step 4**, and the table does not exist yet — it is created later, at app startup |

So the one control that would have caught this ran before the table existed. That is the
finding worth carrying forward: a table created by `create_all()` is invisible to every
schema-time control in this repository.

## The remediation

`backend_app/migrations/026_fee_records_row_level_security.sql` (applied; recorded in
`public.schema_migrations`).

It **creates** the table rather than only hardening it. "Harden only if it exists" was
considered and rejected: on a fresh provision the table does not exist at migration time,
so the finding would simply not be fixed. Creating it in the migration sequence also puts
it in front of 999's section-4 backstop, which then covers it from that point on.

Column types were copied from the **live schema**, not inferred from the Python model —
`fee_id` is `varchar`, not `uuid` (it is built as `f"fee_{execution_id}_{ts}"`), and
`created_at` was deliberately left with **no server default** so the declaration
reproduces exactly what `create_all()` produces. Adding `DEFAULT now()` would have
introduced migration/ORM divergence, which is the precise class of defect
`tests/test_orm_models_match_provisioned_schema.py` exists to catch.

`ENABLE`, not `FORCE`. The application connects as `postgres`, which owns the table, and
an owner is exempt from its own policies unless `FORCE` is set. `ENABLE` closes the
PostgREST surface and leaves all six `FeeEngine` methods working; `FORCE` would break
them. This is the same reasoning `999_privilege_hardening.sql` records for itself.

**Should `fee_records` be exposed through PostgREST at all? No.** Measured rather than
assumed: all six `FeeEngine` methods go through one SQLAlchemy `Session`; there are zero
`.table("fee_records")` calls, zero Supabase-client accesses and zero raw psycopg2 row
accesses; and no router or worker imports `fee_engine`. The browser-role grants were never
load-bearing, so both roles are revoked outright with nothing granted back — the same
posture `payment_methods` and `creator_analytics` already have.

The owner policy is `FOR SELECT`, not `FOR ALL`. A fee record is written by the platform
from an exchange fill; a customer reads their own fees and never authors one.

## Evidence

`tests/test_tenant02_fee_records_rls_postgres.py` — **22 passed**.

* RLS status, both policy predicates and every role privilege are read from `pg_class`,
  `pg_policies` and `information_schema.role_table_grants`. An application that returns no
  rows may be filtering correctly, failing for an unrelated reason, or reading a different
  table; the catalogues are the authority.
* `anon` `SELECT`/`INSERT`/`UPDATE`/`DELETE` each refused, and an attempted write leaves
  existing rows byte-identical.
* **The owner policy is exercised, not merely counted.** With every privilege revoked the
  policy has nothing to filter, so "the policy exists" would be the strongest available
  claim — and a policy nobody has executed is a claim. The cross-tenant tests therefore
  `GRANT SELECT` to `authenticated` inside a transaction and roll it back, which is the
  only way to show the predicate isolates tenants. A further test confirms the temporary
  grant does not leak.
* `service_role` holds `BYPASSRLS` on this database (measured: `rolbypassrls = true`), so
  row security does not apply to it at all and `fee_records_service_role` is defence in
  depth rather than the thing that lets the backend work. Stated rather than left for a
  reader to assume.
* Re-applying 026 inside a savepoint changes nothing.

## Ownership key

`tenant_id`, and it equals the auth user id. Not assumed from the name:
`add_foreign_keys_20260817.py` FKs `execution_records.tenant_id → profiles(id)`;
`migrations/006` performs `UPDATE execution_records SET user_id = tenant_id`; and 006's own
policy is `USING (auth.uid() = user_id OR auth.uid() = tenant_id)` with no cast.
`fee_records.tenant_id` is already `uuid`, so the comparison is plain uncast uuid equality.

## New finding raised here — FEE-01 (P2, OPEN)

`FeeEngine._get_tenant_id_from_execution` and `_get_tenant_id_from_position` fall back to
`UUID(int=0)` when the parent execution or position row is missing, so a fee whose parent
cannot be resolved is written against a tenant that does not exist.

After 026 such a row is at least not visible to a **wrong** owner — the policy is keyed on
`auth.uid() = tenant_id` and no real customer holds the all-zero uuid, so it is visible
only to `service_role`. That is fail-closed for *isolation* and still wrong for
*accounting*: the fee is real, the customer is not billed, and nothing reconciles the
orphans. Recorded in 026's own `COMMENT ON COLUMN`. Not fixed — refusing the write and
refusing to discard the fee are both defensible, and choosing is a product decision.

## Sweep coverage went up, not down

A second defect was found in the measuring instrument itself. `staging/anon_read_sweep.py`
documents `OWNER_COLUMNS` as "seeded with the probe identity", and `_synth` does that — but
`_synth` is only reached for columns that are **not** foreign keys, while the FK branch
runs first. So every table whose owner column carries a foreign key was seeded with an
arbitrary pre-existing parent. `risk_settings.user_id` is `UNIQUE` with an FK to
`profiles(id)` and 18 of 20 profiles already had a row, so the insert collided and the
table **dropped out of coverage entirely** — which
`test_every_readable_table_was_actually_exercised` correctly refused to accept.

An owner column that is also a foreign key now takes the probe identity, guarded by an
existence check so an owner column pointing somewhere other than `profiles`/`auth.users`
still falls back to the original walk. Result:

| Sweep | Before | After |
|---|---|---|
| anon | 0 leaked, 43 proven blocked, 1 not proven | 0 leaked, **44** proven blocked, **0** not proven |
| authenticated | 0 leaked, 69 proven blocked, 1 not proven | 0 leaked, **70** proven blocked, **0** not proven |

`risk_settings` is now measured at "0 of 19 rows" for both roles.


# PHASE 9 — 2026-10-10. NEW TENANT-SCOPED TABLES; NO ISOLATION FINDING RE-OPENED

Phase 9's scope was operational safety and financial integrity, not tenant isolation. No
isolation finding in this map was re-opened or re-measured. Two tenant-scoped tables were
added by Phase 9 migrations and are recorded here so the isolation surface stays complete:

* **`safety_control_audit`** (migration 028) — carries `tenant_id` on each kill-switch
  transition where one is known; append-only RLS (SELECT, INSERT only), with
  `service_role` ALL explicitly revoked first.
* **`referral_obligations`** (migration 029) — carries the resolved referrer and a
  `tenant`/customer scope on each owed-commission row; same append-only RLS discipline.

Both exist as **migration-created** tables (not `create_all()`-created), so unlike the
`fee_records`/TENANT-02 case they receive their `ENABLE ROW LEVEL SECURITY` and policies from
the migration itself. All isolation evidence remains **LOCAL-substrate**; no production DSN
existed in any phase, so production isolation stays unverified and `PRODUCTION` stays NOT
CLASSIFIED.

**Documented DSN discrepancy (carried, not fixed):** TENANT-02's suite resolves its DSN via a
fallback to `staging/pg_dsn.txt`; every new Phase 9 PostgreSQL suite gates strictly on
`AERORA_TEST_DATABASE_URL` with no fallback. Noted, out of Phase 9 scope.


# PHASE 10 — 2026-10-10. SWEEP COVERAGE RESTORED; TENANT-01 STILL OPEN

Phase 10 restored cross-tenant sweep coverage for one table and added credential-vault
negative controls. It did not change the TENANT-01 architectural decision; the finding
remains OPEN.

* **Sweep coverage gap closed (CORR-P10-01).** The Phase 9 report attributed the PostgreSQL
  sweep failure to `risk_settings`; measured in Phase 10, that case was already handled, and
  the actual NOT-PROVEN table was `referral_obligations` — the table Phase 9 added (migration
  029) — whose `currency ~ '^[A-Z]{3}$'` CHECK the generic seeder could not satisfy. Added
  `referral_obligations: {currency: "USD"}` to `CHECK_OVERRIDES` in
  `staging/anon_read_sweep.py`. The sweep now reports **0 leaked, 0 not proven** for both
  `anon` and `authenticated`, and `test_cross_tenant_read_sweep_postgres` passes 6/6. The
  authenticated attacker reads 0 of the owner's `referral_obligations` row, so RLS isolates it.

* **Credential vault (Redis-backed) tenant isolation** now has executed negative-control
  coverage — see `tests/test_tenant01_credential_vault_isolation.py` and the AUTH_RLS_AUDIT.md
  Phase 10 note.

* **FORCE ROW LEVEL SECURITY** is still set on zero tables, and the SQLAlchemy owner path
  still bypasses RLS. These are the deliberate residuals that keep TENANT-01 OPEN. All
  isolation evidence remains LOCAL-substrate; PRODUCTION stays NOT CLASSIFIED.
