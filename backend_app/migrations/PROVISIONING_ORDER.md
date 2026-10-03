# Provisioning order — how to build this database from source

Read this before standing up staging, before a disaster-recovery rebuild, and
before running `alembic upgrade head`.

**The schema is split across two systems and NEITHER IS SUFFICIENT ALONE.**
Sixteen tables are declared only by Alembic revisions; the other fifty-eight are
declared only by the numbered SQL set in this directory and in `migrations/`.
Until migration `020_declare_pre_existing_tables.sql` there were three more that
were declared by **nothing**, which is why no rebuild had ever been possible.
This document records the order that works and the obstacles that remain.

Measured against production (PostgreSQL 17.6, 71 base tables in `public`) in
production-launch-hardening task 13.22.

---

## The order

### Step 0 — `020_declare_pre_existing_tables.sql` FIRST, despite its number

`020` is numbered last and **must be applied first.** It is the only
declaration of `public.profiles`, `public.strategies` and
`public.processed_orders`, and nine files in the numbered set `ALTER` one of the
first two:

| file | ALTERs |
|---|---|
| `backend_app/migrations/001_strategy_architecture.sql` | `strategies` |
| `backend_app/migrations/003_signal_trace_restoration.sql` | `strategies` |
| `backend_app/migrations/005a_strategy_archive.sql` | `strategies` |
| `backend_app/migrations/015_strategy_last_signal_at.sql` | `strategies` |
| `backend_app/migrations/017_plan_entitlements.sql` | `profiles` |
| `migrations/006_reconcile_production_database.sql` | `strategies` |
| `migrations/add_billing_currency_and_dag_hash.sql` | `profiles`, `strategies` |
| `migrations/referral_system_redesign.sql` | `profiles` |

Applied in numeric order on an empty database, `001` fails at its section 6
(`ALTER TABLE strategies ADD COLUMN …`) with `42P01 relation "strategies" does
not exist`. The number could not be lowered: `017`, `018` and `019` are taken,
and migrations here are applied **by hand, per file, with nothing recording
which files an environment has run** — so renaming an existing file is invisible
to an operator who has already applied it. The number is a label, not a
dependency graph; this document is the dependency graph.

`020` is a complete **no-op** against a database that already has the three
tables, so applying it first on an existing environment costs nothing.

### Step 1 — the numbered SQL set, in numeric order

`backend_app/migrations/001` … `019`, then the files in `migrations/`
(`003`–`007`, then the unnumbered ones). `006_reconcile_production_database.sql`
is the one that reconciles shapes rather than adding tables.

### Step 2 — the Alembic chain, for the sixteen tables SQL does not declare

```
alembic upgrade d97ffff9c3bb     # NOT `upgrade head` — see the obstacles below
```

`d97ffff9c3bb` (`execution_schema_rebuild`) is the branchpoint and the revision
production's `alembic_version` actually holds. It declares thirteen of the
sixteen. The remaining three come from revisions past the branchpoint:

| table | declared by |
|---|---|
| `dag_tasks`, `execution_records`, `fills`, `idempotency_keys`, `invoices`, `orders`, `payment_methods`, `positions`, `reconciliation_mismatches`, `subscriptions`, `transaction_checkpoints`, `transaction_records`, `transaction_rollbacks` | `d97ffff9c3bb_execution_schema_rebuild.py` |
| `library_strategies`, `library_ratings` | `e88f9911b5a2_consolidate_full_schema.py` |
| `referrals` | `6f1b3d9c8a7e_add_referrals.py` |

`e88f9911b5a2` also declares `copilot_sessions`, `copilot_messages` and
`waitlist`, but those three are owned by
`018_copilot_and_waitlist_tables.sql` — see that file's header for why the SQL
set, not Alembic, is their authoritative declaration.

### Step 3 — only now can `strategies.source_library_id` get its foreign key

`020`'s fourth foreign key, `strategies_source_library_id_fkey`, references
`library_strategies`, which step 2 creates. `020` guards it on
`to_regclass('public.library_strategies')` and **raises a NOTICE naming the
skipped constraint** when the referent is missing, so a step-0 run says in its
own log that the schema it produced is weaker than production. Re-run `020`
after step 2 — it is idempotent — and the constraint lands.

---

## The obstacles, measured

### 1. `alembic upgrade head` cannot complete on an empty database

`4ef23035a692_baseline.py`'s `upgrade()` contains **only DROPs** — 30 `execute`
calls and zero `op.create_table`. Its seven `CREATE TABLE`s are in
`downgrade()`. Stopping one revision earlier than `d97ffff9c3bb` therefore
leaves no `orders` table at all, which is why step 2 names the branchpoint
explicitly instead of the head.

### 2. `add_foreign_keys_20260817.py` can only run AFTER `020`

It adds `FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE`
five times — on `fills`, `orders`, `positions`, `dag_tasks` and
`execution_records`. Before `020`, **nothing in this repository declared
`profiles`**, so every one of those statements failed. Its per-statement
`try/except Exception` does not rescue it: PostgreSQL aborts the whole
transaction on the first failed statement, so everything after the swallow —
*including Alembic's own `UPDATE alembic_version`* — fails with
`InFailedSqlTransaction`.

**This is the unlock.** With `profiles` declared, the chain past the branchpoint
stops being unrunnable. It has not been run end to end from this environment, so
treat step 2 beyond `d97ffff9c3bb` as untested rather than proven.

### 3. The chain forks and has to be merged

`d97ffff9c3bb` forks into `e88f9911b5a2` and `add_foreign_keys`;
`merge_heads_20260820.py` joins `6f1b3d9c8a7e` and `implement_rls_policies`.
`tests/test_schema_table_reference_drift.py::TestAlembicDagHasOneHead` pins
that shape.

### 4. `alembic_version` is the one table nothing here declares, and that is correct

Alembic issues its own `CREATE TABLE alembic_version` from `env.py`. Declaring
it in a migration would create a second, competing definition of its bookkeeping
table. `TestNothingInProductionIsDeclaredByNothing` asserts that the set of
production tables declared by nothing is **exactly** `{alembic_version}`, so a
new undeclared table appearing in the tree fails the build.

### 5. Privileges are NOT in the SQL set for the three tables `020` declares

All three carry Supabase's default privilege shape in production (`anon`,
`authenticated`, `postgres` and `service_role` each hold the full DML set), and
so do 44 of the 71 `public` tables. `020` deliberately does not restate it: see
its header. On a **non-Supabase** rebuild there is no `ALTER DEFAULT
PRIVILEGES` to supply it, so grants have to be applied separately — and
`020`'s preflight will refuse to run at all without the `authenticated` and
`service_role` roles and the `auth.uid()` function, because the row level
security it reproduces cannot be expressed without them.

### 6. `rls_migration.sql` at the repo root was never applied — do not treat it as the RLS source

It creates `profiles_authenticated_owner`, `strategies_authenticated_owner` and
`processed_orders_authenticated_owner`; production has **none** of those names.
It creates `idx_strategies_user_id` and `idx_processed_orders_user_id`;
production has **neither**. Of everything that file would create, only
`idx_profiles_id` is present. The policies production actually carries are
reproduced in `020` section 5, read off `pg_policies`.
