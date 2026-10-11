# Final Launch Readiness Report

**Scope** Remediation and verification pass following the pre-launch audit. Baseline
documents: `FINAL_PRE_LAUNCH_TEST_REPORT.md`, `FINAL_PRE_LAUNCH_BUGS.md`,
`POST_LAUNCH_IMPROVEMENTS.md`. Nothing in those is downgraded here.

**Date** 6 October 2026
**Environment** Real PostgreSQL 16.2 and real Redis 5.0.14.1, both stood up locally for
this pass. No production system was read or written.

---

## VERDICT: NO-GO

**PAPER-ONLY READINESS: NO-GO.**
**LIVE-TRADING READINESS: NOT CERTIFIED. Live trading is OFF and was never assessed.**

The strict paper-only decision, with its evidence matrix, is in
`PAPER_ONLY_LAUNCH_READINESS.md`. This document is the engineering record behind it.

### What changed since the previous verdict

Three of the four blockers that produced the previous NO-GO are **closed by execution**,
not by argument. Each was reproduced against a real database before being fixed, and each
fix was shown to be load-bearing by restoring the defect and watching the test go red.

| # | Previous blocker | Now |
| --- | --- | --- |
| 1 | Billing tables possibly readable by the browser anon key — unchecked | **CLOSED.** The question was answered the hard way: **22 exposures reproduced**, then fixed, then regression-tested. See below. |
| 2 | Referral payout endpoint cannot succeed for anyone | **CLOSED.** Root cause was worse than reported — crediting *and* reversing a commission both failed. Forward migration + 12 tests. |
| 3 | `positions` had three competing declarations; reconciliation read through a model that raises | **CLOSED.** Canonical shape chosen on evidence, alembic `b2c3d4e5f6a7`, 6 lifecycle tests. A fourth defect surfaced and was fixed: `create()` silently dropped `exchange_id`. |
| 4 | Billing cannot be exercised with sandbox credentials, by design | **STILL OPEN.** Not a technical item. It requires a named owner to accept the risk in writing, and that is not mine to do. |

### Why it is still NO-GO

Not because of item 4 alone, and not because paper trading is broken — the paper path is
the deployed configuration and remains the best-covered part of the system.

| # | Reason | Status |
|---|---|---|
| 1 | **No verification has been run against production.** The tooling now exists and is gated by 21 tests proving it is safe to run there — but `.env` holds placeholders only (`sqlite` URL, `"dummy_key"`), so **no production DSN was available**. Network egress is not the obstacle; a credential is. | **NOT VERIFIED — blocked on a DSN** |
| 1a | ~~BUG-049: 12 identity columns cannot carry a foreign key.~~ All 12 are now `uuid` with a foreign key to `profiles(id)` and an ON DELETE chosen per table — `CASCADE` for credentials so an encrypted API key cannot outlive its owner, `RESTRICT` for financial records so deletion cannot silently destroy them. 76 regression tests prove the behaviour, not just the DDL. | **FIXED** |
| 1b | **The deploy workflow does not provision the real database.** `.github/workflows/01-pr-check.yml` now does, against an ephemeral container (job `provisioning-and-isolation`), and that job also fails on drift and runs all ten probes. `03-deploy.yml` deliberately does **not**: running DDL against production Supabase from CI is hard to reverse and should be an explicit decision with a backup taken first. | **DECISION NEEDED** |
| 3 | **Billing cannot be exercised with sandbox credentials.** `_validate_keys` accepts live prefixes only, in every environment, by explicit design. The revenue path will execute for the first time against real customer money. | **NOT VERIFIABLE — needs written risk acceptance** |
| 4 | **The `service_role` code paths have not been verified.** `service_role` holds `BYPASSRLS`, so for every endpoint that queries through it the application's own tenant filter is the *only* control. `routers/library.py` alone has more than a dozen such call sites. | **NOT VERIFIED** |
| 5 | **No security sweep has been run against the running application.** Everything verified here is at the database layer. | **NOT DONE** |
| 6 | **The "110 exchanges" figure and the ML capability claims have not been reconciled with what the code does.** A truthfulness question, not a technical one. | **NOT DONE** |

Item 1 is the one that matters most, and it is also the cheapest: **the schema this work
verified has never been compared against the schema production runs.** Every finding in
this document — including the reassuring ones — is a statement about a database on a
laptop until `scripts/verify_schema_drift.py` has been pointed at the real target. It
reads shape only, never row data, so doing so is safe.

---

## Live trading status

**Live customer trading is OFF.** Verified by executing the guard, not by reading config.

**TEST** `staging/verify_live_trading_off.py` — invokes
`SafetyMonitor.check_execution_allowed("live_order")` in a subprocess under five
environments
**ACTUAL** All five refused. Exit 0.

| Environment | Verdict |
|---|---|
| Production as deployed: `ENV=production`, `VYOMQUANT_MODE=paper`, enable flag unset | BLOCKED |
| `VYOMQUANT_ENABLE_LIVE_TRADING=true` but mode still `paper` | BLOCKED |
| `VYOMQUANT_MODE=live` but enable flag unset | BLOCKED |
| Deprecated alias `AERORA_ENABLE_LIVE_TRADING=true`, mode `paper` | BLOCKED |
| **Both switches set: `VYOMQUANT_MODE=live` AND `VYOMQUANT_ENABLE_LIVE_TRADING=true`** | **BLOCKED** — `Live trading flag disabled` |

The last row is the one worth knowing. With both environment switches set to the values
that are supposed to enable live trading, execution is still refused — by
`ExecutionFlags.LIVE_TRADING_ENABLED`, which is a hardcoded `bool = False` class attribute
at `backend_app/core/safety_config.py:114` that is read from **no environment variable at
all**. So enabling live customer trading requires a code change, not a configuration
change. Two environment variables are not the only thing standing between this deployment
and real customer money; there is a third control and it is in source.

Supporting facts:

- `VYOMQUANT_MODE=paper` in `ecs-task-definition-full.json`.
- `VYOMQUANT_ENABLE_LIVE_TRADING` appears nowhere in the task definition, the workflows,
  or the repository.
- `AERORA_ENABLE_TRADING` is also absent from the task definition, and
  `ExecutionFlags.PAPER_TRADING_ENABLED` is likewise a hardcoded `False`. Note the
  consequence: in the four paper-mode cases above the refusal arrives from that upstream
  flag rather than from the live-specific branch. Whatever enables paper execution at
  runtime must set it in-process; it is not environment-driven.
- Nothing in this pass set either flag, and no real-money order was placed at any point.
- No production credential was read. No production database was contacted. The provisioner
  refuses port 6543 and any non-localhost host (`scripts/provision_database.py`).

---

## What converts this to PAPER-ONLY GO

The three technical blockers from the previous revision are closed. What remains is
listed in the order it should be done, with the smallest first.

1. ~~Wire provisioning and drift checking into CI.~~ **DONE.**
   `.github/workflows/01-pr-check.yml` gains a `provisioning-and-isolation` job: provision
   an empty `postgres:17-alpine` from repository source, provision it **again** (the
   privilege-re-grant regression from BUG-041), fail on drift from the committed baseline,
   run all ten probes, run the nine PostgreSQL test files. This is what turns the three
   `RAISE EXCEPTION` backstops in `migrations/hardening/999_privilege_hardening.sql` from
   scripts into merge gates.

   **Wired, not yet executed.** The sequence is verified locally against a vanilla
   PostgreSQL with no Supabase substrate — the same starting condition as the container —
   but on 16.2 rather than 17. The first pull request closes that gap, and the uncertainty
   is recorded in the job's own comment.

2. **Supply a production DSN and run the two verifiers.** This is now the whole of the
   remaining blocker: the tools are built, calibrated and gated.

   ```powershell
   # Read-only. Server-enforced (default_transaction_read_only verified 'on'), reads no
   # row data, refuses the 6543 pooler, prints no credential. Safe to point at production.
   python scripts\verify_production_schema.py --dsn "<production DSN>" `
          --out staging\production_schema.json

   # Then, as a separate deliberate act: the behavioural half. Writes synthetic rows to
   # measure cross-tenant isolation and commits NOTHING -- one transaction, unconditional
   # ROLLBACK. Refuses to run without the flag.
   python scripts\verify_production_security.py --dsn "<production DSN>" `
          --i-understand-this-writes-and-rolls-back `
          --out staging\production_isolation.json
   ```

   The first exits 0 / 1 / 2 for GREEN / AMBER / RED and applies the gate's own
   classification table. **If it returns RED, stop** and produce the remediation package
   — impact, SQL, rollback, backup, locking assessment, verification query,
   post-migration behavioural test — before any DDL.

   This is the step that answers whether any of the 51 defects in
   `FINAL_PRE_LAUNCH_BUGS.md` are live. **It has not been done, and no finding in this
   document should be read as a statement about production until it has.**

3. **Enumerate and verify the `service_role` call sites.** For each one, assert a tenant
   filter is applied. See `POST_LAUNCH_IMPROVEMENTS.md` item 15.

4. **Run a security sweep against the running application**, not just the database.

5. **Correct the exchange-count and ML claims**, or remove them from anything
   customer-facing.

6. **Accept, in writing, that billing is unverified.** It cannot be verified
   pre-production in this codebase: `_validate_keys` accepts live prefixes only, in every
   environment, by design — and that design is defensible for the entitlement-granting
   paths, because honouring a test key would hand out paid plans for free. The consequence
   is still that the revenue path executes for the first time against real customer money.
   This is a conscious risk acceptance with a named owner, not a test result, and it is
   the one item on this list that no amount of engineering closes.

---

## Blocker-by-blocker

### BUG-016 — equity and cash accounting had no single source of truth

**STATUS** FIXED and VERIFIED
**ENVIRONMENT** In-process, no infrastructure required
**TEST** `tests/test_execution_engine_accounting_identity.py` (40),
`tests/test_bug016_backtest_impact.py` (7)
**COMMAND** `pytest tests/test_execution_engine_accounting_identity.py tests/test_bug016_backtest_impact.py`
**EXPECTED** One accounting identity holds after every operation
**ACTUAL** 47 passed

**EVIDENCE** `backend_app/core/execution_engine.py` now follows the model
`backend/paper/paper_accounting.py` already used, rather than a second one beside it:
cash is the residual, `cash_delta = -(new_position_value - old_position_value) - fee`, and
`equity = cash + position_market_value`. `current_equity` is only ever recomputed by
`_sync_equity()`, never incremented. The affordability check in `open_position` moved from
`current_equity` to `self.cash`, which is the actual over-commitment fix — checking
against equity let a position be opened against money already committed to another
position.

`handle_partial_fill` previously applied four divergent equity rules depending on which
branch it took; all four now route through `_apply_cash_delta`.

A latent `KeyError` was found while writing the tests: `get_stats()`'s early-return branch
(taken when no trade has closed yet) returned a dict missing the keys its own callers
read. Fixed to return the full shape.

**REGRESSION TEST** The 47 above, plus a 881-test selection across execution, accounting,
equity, drawdown, portfolio, backtest and risk — 881 passed, 0 failed.

**REMAINING RISK** Low. One note on historical results, below.

#### Did BUG-016 invalidate stored paper or backtest results?

No, and this was checked rather than assumed, because the instruction was not to silently
preserve stale numbers.

The two backtest paths are separate. `backtest_runtime.py` — the one that persists to
`strategy_backtests` and therefore produces marketplace evidence — contains **zero**
references to `ExecutionEngine` and computes through vectorbt. `backtest_internal` is the
one that uses `ExecutionEngine`, and it writes only ephemeral Redis job status via
`_write_status`. So nothing durable was produced by the corrected code path, and there is
no stored result to invalidate or version. `tests/test_bug016_backtest_impact.py` pins
that separation so a future change that routes persisted backtests through
`ExecutionEngine` fails loudly instead of quietly re-dating old numbers.

---

### BUG-012 — weak JWT secret only warned

**STATUS** FIXED and VERIFIED
**ENVIRONMENT** In-process
**TEST** `tests/test_jwt_secret_is_production_grade.py` (32)
**COMMAND** `pytest tests/test_jwt_secret_is_production_grade.py`
**EXPECTED** A deployed environment with a weak or short secret refuses to boot
**ACTUAL** 32 passed

**EVIDENCE** `backend_app/core/config.py` gained
`assert_jwt_secret_is_production_grade()`, `jwt_secret_problems()`,
`MIN_JWT_SECRET_BYTES = 32` and `WEAK_JWT_SECRET_PATTERNS`. It is called from
`backend_app/main.py`'s `lifespan`, after the `missing_settings` check and before the
DEV_MODE check, and raises `RuntimeError` — the process does not start. The two
`logger.warning` blocks in `validate_required` that previously absorbed this are gone.

Enforcement is gated on `_is_deployed_environment()`, which treats an **unset** `ENV` as
deployed. That is deliberately the opposite default from the auth gate: a missing `ENV`
should fail closed on secret strength.

One thing the tests caught in my own first implementation: the error message included the
weak pattern that matched, which leaked up to 9 characters of the configured secret into
logs. The message now names the condition and not the value.

**REGRESSION TEST** The 32 above, plus a 930-test selection across config, settings, auth,
jwt, startup and main — 930 passed, 1 failed.

**REMAINING RISK** The 1 failure is pre-existing and unrelated: BUG-026,
`test_protected_logic_containment`, which matches the substring `'vie'` and false-positives.
It was failing before this pass and is recorded in the audit baseline.

---

### Schema and migrations — the provisioning process did not work, and now does

**STATUS** FIXED and VERIFIED for from-source provisioning. Production state NOT VERIFIED.
**ENVIRONMENT** Real PostgreSQL 16.2, provisioned from repository source onto an empty
data directory
**TEST** `scripts/provision_database.py --apply --create-substrate`, then
`staging/verify_schema.py`
**EXPECTED** A complete schema from an empty database
**ACTUAL** Provisioning exits 0 from scratch. Verification: 22 pass, 1 fail (the known
`positions` divergence).

This is the item that produced the most findings, because running the process is the only
way to learn whether it works, and nobody had. The audit established that **nothing ever
ran migrations** — not the Dockerfile, not `startup.sh`, not a Procfile, not any workflow,
not any script. This pass built the missing provisioner and ran it, repeatedly, until it
completed.

**Seventeen distinct migration defects were found by execution.** Six were found in this
pass and are new; the rest are from the earlier remediation session. Each was found by the
provisioner failing, not by reading.

#### The structural one: alembic `head` was unreachable, so the RLS migration never ran

`.github/workflows/01-pr-check.yml` and `backend_app/migrations/PROVISIONING_ORDER.md`
both state that `alembic upgrade head` cannot provision a fresh database, attributing it to
`add_foreign_keys` referencing a `profiles` table that no migration declares. That cause
does not apply to this provisioner, which declares `profiles` in step 0 before alembic
runs. Running it anyway surfaced the real cause:

```
Warning: Could not add FK for execution_records.task_id: (DatatypeMismatch)
  Key columns "task_id" and "task_id" are of incompatible types:
  uuid and character varying.
...
sqlalchemy.exc.InternalError: (psycopg2.errors.InFailedSqlTransaction)
[SQL: UPDATE alembic_version SET version_num='add_foreign_keys' ...]
```

All seven `ALTER TABLE ... ADD CONSTRAINT` statements ran in one transaction, each wrapped
in `try/except Exception` with a printed warning. The author's intent is plain from that
shape — best-effort, warn about what the schema cannot support, keep going — but
PostgreSQL aborts the entire transaction on a failed statement, so the `except` caught the
error, printed its warning, and the revision died a few statements later on alembic's own
version bump.

The consequence reaches well past that file. `alembic history` puts
`implement_rls_policies` immediately downstream of `add_foreign_keys`, on the only path to
`head`. While `add_foreign_keys` could not commit, **the nine tenant-isolation policies
that migration declares were installed on no database built by migration** — on `orders`,
`positions`, `fills`, `dag_tasks`, `execution_records`, `idempotency_keys`,
`transaction_records`, `transaction_checkpoints` and `reconciliation_mismatches`. Those are
the execution and financial tables, and they had no row filtering at all. Measured before
the fix by `staging/rls_inventory.py`: 0 policies keyed on `app.current_tenant_id`, and all
nine tables reported `RLS DISABLED`.

Fixed by giving each constraint its own SAVEPOINT via `connection.begin_nested()`, which is
what the `try/except` was reaching for. `add_foreign_keys` now commits, and after it
`implement_rls_policies` runs: 9 policies present, RLS enabled on 72 tables rather than 62,
`alembic_version = ['merge_heads']`.

#### Two defects that broke user signup outright

Both were reached only because fixing the above made `public.profiles` inserts actually
execute. Either one alone makes **every user signup fail** on a database provisioned from
this repository.

**pgcrypto is never declared anywhere.** `generate_unique_referral_code()` in
`migrations/referral_system_redesign.sql` calls `gen_random_bytes(4)`, which is pgcrypto's.
No file in the repository issued `CREATE EXTENSION` — the only occurrence of the phrase was
inside an error-message *string* in `009_paper_trading.sql`, which tells an operator to run
it but does not run it. `trigger_create_referral_code` on `public.profiles` calls that
function, so:

```
UndefinedFunction: function gen_random_bytes(integer) does not exist
CONTEXT: PL/pgSQL function generate_unique_referral_code() line 10 at assignment
         SQL statement "SELECT create_referral_code_for_user(NEW.id)"
         PL/pgSQL function trigger_create_referral_code() line 4 at PERFORM
[SQL: INSERT INTO public.profiles (id) VALUES (...)]
```

Production is unaffected today because Supabase enables pgcrypto by default — which is
exactly why the dependency stayed invisible, and exactly why a disaster-recovery rebuild
onto plain PostgreSQL would not have produced a working system.

**And the preflight that existed to catch this checked the wrong function.** `009`'s
precondition tests `gen_random_uuid()`, which has been in **core** PostgreSQL since
version 13. On any modern server that check passes whether or not pgcrypto is installed.
The one function that genuinely requires the extension went unverified. `009` now checks
`gen_random_bytes` as well, and names the consequence in its failure message.

**An ambiguous column in the same function.** With pgcrypto resolved, the next insert
failed again:

```
AmbiguousColumn: column reference "code" is ambiguous
```

The function declared a local variable `code`, and `referral_codes` has a column `code`, so
`WHERE code = code` is ambiguous. Resolving it the other way would not have helped: read as
column-equals-column it is a tautology, so `NOT EXISTS` would be false as soon as one code
existed, all ten attempts would fail, and the function would end at its own
`RAISE EXCEPTION`. Two different failures from one name collision. Renamed to `v_code`,
matching the `v_` convention the file already uses, with the subqueries aliased.

#### Three further new defects

- **`6f1b3d9c8a7e` adds a column production already has.** `op.add_column('profiles',
  'available_discounts')` fails with `DuplicateColumn`, because
  `020_declare_pre_existing_tables.sql` — whose stated job is reproducing what Supabase
  already had — declares it. This would fail against production too, and because the
  transaction aborts, the revision could not record itself, so it would fail again on every
  retry. Guarded with `sa.inspect` for the column, the table and the index.
- **A backfill guarded on the wrong precondition.** `referral_system_redesign.sql`'s
  `referral_wallets` initialisation was gated on whether `referrals` exists. The real
  dependency is the column set: `006_reconcile_production_database.sql` is authoritative
  for production and declares `balance_usd` / `total_earned_usd` / `pending_usd`, while
  this file expects four `*_balance_usd` columns. `CREATE TABLE IF NOT EXISTS` meant 006
  won. The block had never fired because `referrals` never existed; the moment head became
  reachable it fired and failed on `UndefinedColumn: pending_balance_usd`. The guard now
  tests the column it writes.
- **Provisioning order is not interchangeable, in two directions.** Alembic must run
  *before* the numbered SQL set, because `006_reconcile_production_database.sql` ALTERs
  `execution_records`, which only alembic declares — so `PROVISIONING_ORDER.md`'s step-2
  placement is circular. And it must run to `head` *before* the SQL set for a second
  reason: `e88f9911b5a2` issues bare `CREATE TABLE`s for tables the SQL set also declares
  (`copilot_sessions` among them), so alembic-second aborts on `DuplicateTable`, while
  alembic-first works because the SQL files use `CREATE TABLE IF NOT EXISTS`. Both
  directions were measured.

#### What the verified schema confirms

The audit's single largest unknown is now answered:

- **`execution_records` PRIMARY KEY is `['execution_id']`.** CONFIRMED against a real
  server. The idempotency design depends on this and it had never been verified.
- `idempotency_keys` PK = `['idempotency_key', 'tenant_id']`
- `orders` PK = `['id']`; `profiles`, `strategies` PK = `['id']`
- `paper_orders.uq_paper_order_idem` and `uq_strategy_versions_one_current` both present
- 104 foreign keys; 48 migration files recorded in `public.schema_migrations`
- `alembic_version = ['merge_heads']`

**REMAINING RISK** This verifies the schema the repository *produces*. It does not verify
production, which was deliberately not touched. The two may differ, and the places they
are most likely to differ are named in the next two sections.

**A precondition before anyone runs this against production.** `implement_rls_policies`
now actually executes, and it enables RLS on nine financial tables. It issues `ENABLE`, not
`FORCE`, and PostgreSQL exempts a table's owner from its own policies unless FORCE is set —
so if the connecting role owns those tables, the policies are inert and nothing changes.
Every DSN in the repository carries username `postgres`, and alembic runs under the same
`DATABASE_URL`, so the role that creates the tables is the role that connects. But the
production DSN is a Secrets Manager reference
(`arn:aws:secretsmanager:...:vyomquant/production/database_url`) and is **not in the
repository**, so the production username cannot be established from code. Confirm the
connecting role owns those nine tables, or has BYPASSRLS, before running `upgrade head`
against production. If it does not, every query against them returns zero rows — silently,
because nothing sets the GUC the policies compare against.

---

### Tenant isolation and RLS — verified by executing cross-tenant access

**STATUS** VERIFIED
**ENVIRONMENT** Real PostgreSQL 16.2, schema provisioned from source, Supabase-equivalent
roles and default privileges
**TEST** `tests/test_tenant_isolation_rls_postgres.py` (25)
**COMMAND** `AERORA_TEST_DATABASE_URL=<dsn> pytest tests/test_tenant_isolation_rls_postgres.py`
**EXPECTED** Six tenants, mutually isolated, with reads, writes and forged-ownership
inserts all refused across tenants
**ACTUAL** 25 passed

No part of this is a reading of `pg_policies`. Every assertion follows a real statement
issued under an adopted identity.

**EVIDENCE**

- `authenticated` with tenant A's JWT claim sees exactly A's rows on `strategies`,
  `notifications` and `paper_accounts` — never zero (which would be a missing GRANT, not
  isolation), never another tenant's.
- A keyed lookup of another tenant's row by primary key returns 0 rows, across all 30
  ordered tenant pairs per table. A policy that filters a list but not a keyed read would
  pass a naive test and fail this one.
- Cross-tenant `UPDATE` affects 0 rows, and the victim's ownership is then re-checked **as
  the owner**, so a successful write cannot be hidden from the verification query by the
  same policy that was supposed to prevent it.
- Cross-tenant `DELETE` is refused. On `paper_accounts` it is refused by *privilege* rather
  than policy, because `009_paper_trading.sql` deliberately withholds DELETE from
  `authenticated` — "a session is deleted only by the account cascade, not by a client".
  The test distinguishes the two mechanisms instead of conflating them; both leave the row
  intact, but they are different controls.
- A forged-ownership `INSERT` is refused by `WITH CHECK`. A policy with `USING` and no
  `WITH CHECK` would allow it.
- With no JWT claim installed, 0 rows are visible. `USING (true)` would pass every other
  test here and fail only this one.

**Three findings that only execution produces:**

1. **`service_role` sees all six tenants.** BYPASSRLS confirmed. So on every path using
   the service-role key — `routers/library.py` wholesale, the billing webhook, the TEE
   reconciler — the database applies no tenant filtering at all and the Python-level
   ownership checks are the *only* isolation control. This is asserted rather than assumed,
   so it fails loudly if someone later believes RLS is a second layer there.
2. **The table owner is exempt, so RLS protects nothing on the SQLAlchemy path.** Every
   repository DSN is `postgres`, which owns the tables, and no migration issues FORCE.
   Tenant isolation on that path rests entirely on application `WHERE tenant_id = ...`
   clauses with no database backstop. The reassuring corollary is that enabling the nine
   policies cannot break the application while it connects as the owner.
3. **The nine `app.current_tenant_id` policies are sound and are never given an input.**
   With the GUC unset a non-owner role sees 0 rows; with it set, each tenant sees exactly
   its own. So the predicates are correct and the remediation is adoption, not rewriting —
   a materially smaller change to recommend. `core/tenant_db_context.py` already provides
   the mechanism; ADR-0006 records its adoption as not started, and its only importers are
   tests.

**REGRESSION TEST** The 25 above. Skips cleanly when `AERORA_TEST_DATABASE_URL` is unset,
so the default lane is unaffected.

**REMAINING RISK** Verified against the schema this repository produces, not against
production. The substrate reproduces Supabase's roles, `auth.uid()` and default privileges;
it is a faithful stand-in, not the real platform.

---

### NEW — four tables with per-customer data have no row filtering

**STATUS** NOT VERIFIED against production. CONFIRMED present in the repository.
**SEVERITY** Potential cross-customer disclosure of billing data. Launch blocker until
answered.
**ENVIRONMENT** Real PostgreSQL 16.2, provisioned from source
**TEST** `staging/exposure_check.py`
**EXPECTED** Every table with a per-customer column either has RLS enabled or an explicit
REVOKE
**ACTUAL** Four do not

| Table | Owner column | RLS | Explicit GRANT/REVOKE in any migration |
|---|---|---|---|
| `invoices` | `user_id` | **disabled** | none |
| `payment_methods` | `user_id` | **disabled** | none |
| `subscriptions` | `user_id` | **disabled** | none |
| `transaction_rollbacks` | `tenant_id` | **disabled** | none |

**EVIDENCE** In Supabase's model, `anon` and `authenticated` hold privileges on `public` by
default, and row level security — not table privilege — is what separates one customer's
rows from another's on the PostgREST path. The repository is consistent with this: the
newer migrations (007, 008, 009, 017, 018, 019, 004d) issue careful per-table GRANTs and
explicit `REVOKE ALL ... FROM anon`, while older tables carry no grant statements at all
because the platform defaults already covered them.

These four carry neither RLS nor a REVOKE. `020_declare_pre_existing_tables.sql` states at
line 124 that they are declared in `alembic/versions/` and deliberately not re-declared
there. `implement_rls_policies` enables RLS on nine tables and none of these is among them.
`006_reconcile_production_database.sql` enables RLS on **`billing_invoices`** — a different
table from `invoices`.

So on any database built from this repository, a holder of the anon key — a public,
browser-shipped credential — can read every customer's invoices, subscriptions and payment
methods.

**WHY THIS IS NOT VERIFIED** RLS may have been enabled on them in production through the
Supabase dashboard rather than by a migration in this tree. That is plausible and would
make the production exposure nil. It is also exactly the kind of thing that is assumed and
turns out not to be true. It cannot be checked from here and must not be guessed.

**REGRESSION TEST** `staging/exposure_check.py` exits non-zero while any per-customer table
lacks both RLS and a REVOKE.

**REMAINING RISK** Entirely dependent on the production answer. If RLS is off there, this
is an active breach of billing data and the only correct launch action is to fix it first.

---

### NEW — the referral payout endpoint cannot succeed for anyone

**STATUS** BROKEN. Found by inspection grounded in the verified live schema; the endpoint
itself was not executed.
**ENVIRONMENT** Schema verified on real PostgreSQL; code read at
`backend_app/routers/referral.py`
**EXPECTED** A user with approved referral earnings can request a payout
**ACTUAL** Every request is rejected as insufficient balance, regardless of actual earnings

**EVIDENCE** The provisioned `referral_wallets` — matching `006`, the file that reconciles
against production — has columns `balance_usd`, `total_earned_usd`, `pending_usd`. It has
no `approved_balance_usd`.

`routers/referral.py` reads:

```python
wallet = wallet_resp.data[0]
approved_balance = float(wallet.get("approved_balance_usd", 0))
...
if payout_request.amount_usd > approved_balance:
    raise HTTPException(status_code=400, detail=f"Insufficient approved balance. ...")
```

`select("*")` cannot return a column that does not exist, so `.get(..., 0)` returns the
default. `approved_balance` is therefore always `0.0`, and since the endpoint separately
enforces a $10 minimum, the comparison `amount > 0` is true for every well-formed request.
The endpoint answers **400 "Insufficient approved balance. Available: $0.00"** to everyone.

It fails quietly rather than erroring, which is why it would not show up as an exception in
logs — it looks like a user with no earnings. The subsequent
`.gte("approved_balance_usd", ...)` filter would also fail at PostgREST, but is unreachable.

**ROOT CAUSE** `migrations/referral_system_redesign.sql` and
`migrations/006_reconcile_production_database.sql` declare the same five tables with
different column sets, and `006` wins because it runs first and the redesign uses
`CREATE TABLE IF NOT EXISTS`. The router was written against the redesign's shape. The
repository's own `.kiro/specs/production-launch-hardening/tasks.md` already records the
divergence; what is new here is that a shipped endpoint depends on the losing side.

**NOT FIXED, deliberately.** Choosing which shape is canonical is a product and data
decision with an owner — the redesign carries a four-state balance model
(pending/approved/paid/lifetime) that `006`'s three columns cannot express, so this is not
a rename. Picking one under launch pressure, with live wallet balances in production, is
not a call to make from here.

**REMAINING RISK** Users who have earned referral commission cannot withdraw it and are
told they have nothing. Recommend disabling the endpoint for launch if the schema decision
cannot be made first.

---

### Billing — not verifiable before production, by design

**STATUS** NOT VERIFIED. Structurally, not for want of credentials.
**ENVIRONMENT** Real Redis 5.0.14.1
**EXPECTED** A sandbox settlement end to end
**ACTUAL** Impossible in this codebase

**EVIDENCE** `_validate_keys` in `backend_app/routers/billing.py` is explicit:

> Live prefixes only, in every environment. There is no test-credential bypass: a
> `sk_test_`/`rzp_test_` credential collects no money, so honouring one on a path whose
> success grants a paid entitlement would hand out plans for free and book revenue that
> never arrived.

Observed directly — supplying `rzp_test_...` to the webhook handler produces
`500: Razorpay is configured with a TEST key id. Live credentials are required: a test key
collects no money.` The gate runs before everything else on the path.

The reasoning is sound for the *entitlement-granting* paths. Its side effect is that **no
billing path can be exercised in staging or CI at all**, including the webhook, so the
revenue path will run for the first time against real customer money. Live credentials were
not used here and must not be: that constraint is not negotiable and is why this stays
NOT VERIFIED rather than being forced green.

#### Duplicate webhook delivery — the mandatory test — VERIFIED

**TEST** `tests/test_webhook_duplicate_delivery_real_redis.py` (6)
**COMMAND** `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6379/0 pytest tests/test_webhook_duplicate_delivery_real_redis.py`
**ACTUAL** 6 passed, against real Redis 5.0.14.1 and the real handler

Razorpay's webhook authentication is HMAC-SHA256 over the raw body with a shared secret, so
a correctly signed request can be produced locally. That makes the dedup control testable
without a payment account, even though settlement is not.

- A redelivery of the same `event_id` returns `{"status": "duplicate"}`, and — equally
  important — the **first** delivery is not treated as a duplicate. A webhook that is never
  processed is a lost settlement, which is the opposite failure and just as expensive.
- The lock is recorded at `webhook:razorpay:{event_id}` and carries a TTL. A lock with no
  expiry would dedup for ever.
- A **different** `event_id` is not deduplicated. A lock written under a constant key would
  pass the redelivery test and silently discard every subsequent payment.
- A delivery arriving while the first is still in flight is refused on the `processing`
  marker alone, before any `completed` value exists. That is the realistic race, since
  providers retry on timeout.
- An invalid signature is rejected **and leaves no lock behind**, so a forged request
  cannot deny settlement for the genuine event that follows it.

The credential gate is asserted to still refuse test keys, in the same file, before being
stepped around to reach the control behind it. The isolation is documented in the test, not
hidden.

**What this does not establish** No provider interaction, no entitlement grant, no
settlement, no invoice. Only redelivery behaviour.

#### A note on what the previous billing test measured

`tests/test_billing_e2e.py` verified this behaviour with
`assert source.count("await redis_manager.delete(idempotency_key)") >= 2`. That establishes
two `delete` calls are written in the file. It does not establish that a redelivery is
ignored, that the first delivery is not, that the lock is released when processing raises,
or that any of it works against a real Redis. For payment webhooks — the one place where
processing an event twice moves money twice — string counting is not a sufficient standard.

---

### The test suite's Redis was never real

**STATUS** Findings recorded; opt-in mechanism added
**EVIDENCE** `tests/conftest.py` line 13 sets `os.environ["REDIS_URL"] = ""`
unconditionally and deliberately, so the whole suite runs on the in-memory
`MockRedisClient`. That is the right default — 12,000 tests should not depend on whatever
Redis is on a developer's machine — but it means **no Redis-backed control in this
repository had ever executed against a real server.**

This matters because `MockRedisClient` is a plain dict on a module-global instance, so it
is per-process and `SET NX` always succeeds. That is BUG-027 from the audit: the
distributed locks behind `master_executor`'s execution lock and
`IdempotentExchangeSubmission.claim_submission` silently became per-pod no-ops while still
reporting success.

Added `AERORA_TEST_REDIS_URL` as a second opt-in, following the precedent
`AERORA_TEST_DATABASE_URL` already sets. Unset, the new tests skip and the default lane is
untouched; set, they run against a real server. The webhook tests above are the first
Redis-backed tests in this repository to have done so.

---

### The CI PostgreSQL lane had never run. It passes.

**STATUS** VERIFIED
**ENVIRONMENT** Real PostgreSQL 16.2
**TEST** The four files `.github/workflows/01-pr-check.yml`'s `database-tests` job selects,
plus two gated files it does not select
**COMMAND** `staging/run_pg_lane.py`
**ACTUAL** 47 passed, 1 deselected

| File | Result |
|---|---|
| `test_database_isolation_level_postgres.py` | 7 passed |
| `test_atomic_order_cancellation_fix.py` | 3 passed |
| `test_position_delta_race_condition_fix.py` | 4 passed |
| `test_strategy_lifecycle_concurrency_postgres.py` | 9 passed |
| `test_tenant_db_context_postgres.py` | 11 passed (not in the CI selection) |
| `test_json_extract_dialect_portability.py` | 13 passed (not in the CI selection) |

Controls confirmed by this that could not be confirmed on SQLite:

- `SELECT ... FOR UPDATE` row-level locking actually locks — the FIN-CRITICAL-002 control,
  whose test failed on every SQLite run because SQLite's parser rejects the clause.
- The partial unique index in `021_strategy_deployment_live_uniqueness.sql` refuses a
  second live deployment, with two real sessions racing off a `threading.Barrier`.
- `set_config(..., true)` is transaction-scoped and a second transaction on the same
  connection cannot see the first tenant's value — the property the pgbouncer
  transaction-pooling design rests on.

**The one test CI deselects genuinely fails.** `test_transaction_isolation_for_cancellation`
asserts the general `SessionLocal()` reports `serializable`; against a real server it
reports `read committed`. CI deselects it against a documented design conflict —
`core/database.py` deliberately makes only FINANCIAL sessions SERIALIZABLE — and that
conflict is unresolved, not resolved. Recorded as an open decision, matching the
repository's own position. It is not a new defect and it is not quietly passing either.

---

## NOT VERIFIED — stated plainly

Per the instruction, none of these is converted into a pass.

| Item | Why | What would close it |
|---|---|---|
| **Real exchange testnet order** | `testnet.binance.vision:443` is reachable from here — network was confirmed, contradicting the audit's offline conclusion. No testnet API key or secret is available, and fabricating evidence of a trade is not an option. | A Binance testnet key/secret pair, then the single CCXT call site at `exchange_executor.py:811` |
| **Billing settlement end-to-end** | The code refuses test credentials in every environment by design; live credentials are forbidden here. | A conscious decision to allow sandbox keys on non-production, or accepted risk with a named owner |
| **Reconciliation mismatch** | Not reached this pass. Note that `core/consistency_checker.py` reads positions through `PositionModel`, which cannot query the `positions` table — so this may fail outright, and that needs testing rather than assuming. | Induce a local-vs-venue divergence with a mocked venue and assert the mismatch is recorded |
| **ML train → persist → reload → inference** | Not reached. Blocked in the default test lane by `VYOMQUANT_MODE=safe`. | Run in staging with a mode permitting `ml_inference` |
| **Paper trading end-to-end against real PostgreSQL** | Not reached this pass. Well covered in-process by the main suite, but not against a real server. | `Paper_Session` / `PaperTradingService.place_order` against the staging DSN |
| **Production-like rehearsal** | Not reached. | Boot API and worker against staging PG and Redis with `ENV=production`, `VYOMQUANT_MODE=paper`; check startup validation, health, restart recovery |
| **Production schema state** | Production was deliberately not touched. | Read-only inspection by someone with access, starting with the four-table RLS query above |
| **`td15.json` / `td22.json`** | UTF-16 encoded, unparsed. Carried from the audit. | Decode and audit |

---

## Known defects carried forward, not fixed

| Defect | Why not fixed here |
|---|---|
| `PositionModel` cannot query `positions` — declares 14 columns, the table has 5. Proven by execution: `UndefinedColumn: column positions.position_id does not exist`. Three competing declarations exist (alembic `d97ffff9c3bb`, `core/position_model.py:369`, `backend/state_service.py:333`). Blast radius includes `position_engine`, `pnl_engine`, `metrics_system`, `fee_engine` and `consistency_checker`. | Three declarations is a design decision needing an owner. Guarded by `tests/test_orm_models_match_provisioned_schema.py` — 4 pass, 1 `xfail(strict=True)` so the finding neither disappears nor leaves the suite permanently red. |
| 5 of 7 FK constraints in `add_foreign_keys` cannot be created. `tenant_id` is `varchar(36)` on `orders`/`positions`/`fills`/`dag_tasks` but `profiles.id` is `uuid`; `execution_records.task_id` is `uuid` against a `varchar(36)` primary key. Referential integrity on those columns does not exist. | The ORM reproduces both conventions *consistently* on each side — `execution_tables.py` and `dag_task.py` declare `String(36)`, `execution_record.py`, `reconciliation.py` and `fee_engine.py` declare `PG_UUID`. Normalising means retyping columns on four financial tables and changing the ORM declarations that currently match them. The constraints stay declared and keep warning, which is what keeps the gap visible. |
| Five tables declared differently by `referral_system_redesign.sql` and `006`. | Same decision as the payout item above. |
| BUG-026 `test_protected_logic_containment` false-positives on the substring `'vie'`. | Pre-existing, cosmetic, recorded in the audit baseline. |
| General `SessionLocal()` is `read committed` while three tests assert `serializable`. | Documented design conflict with an open decision; `core/database.py` deliberately scopes SERIALIZABLE to FINANCIAL sessions, and nothing outside `paper_repository.py` retries on `40001`. |

---

## Regression position

| Suite | Result |
|---|---|
| Audit baseline (before any fix) | 12,509 collected, 12,412 passed, 19 failed |
| Audit final | 12,672 collected, **12,577 passed**, 17 failed, 70 skipped, 5 xfailed, 3 xpassed |
| Accounting selection (BUG-016) | 881 passed, 6 skipped, 1 xpassed, **0 failed** |
| Config/auth selection (BUG-012) | 930 passed, **1 failed** (pre-existing BUG-026) |
| PostgreSQL lane (first ever execution) | **47 passed**, 1 deselected |
| Tenant isolation / RLS | **25 passed** |
| Webhook duplicate delivery (real Redis) | **6 passed** |
| ORM vs provisioned schema | 4 passed, 1 xfailed (strict) |
| Live trading gate, 5 environments | **5 blocked**, exit 0 |
| Migration / schema-text selection | **240 passed** |
| `test_p58_migration_set_is_idempotent` in isolation | **1 passed** in 1001.88s |
| **Full suite, this pass** | **17 failed, 12,660 passed, 101 skipped, 6 xfailed, 3 xpassed** in 52m58s |
| The failing files, re-run in isolation after the fix below | **8 failed, 484 passed** |

### Reconciling the full suite against the baseline

The totals add up exactly, which is the first thing to check:

```
baseline total   12,672  = 12,577 passed +  17 failed +  70 skipped + 5 xfailed + 3 xpassed
this pass        12,787  = 12,660 passed +  17 failed + 101 skipped + 6 xfailed + 3 xpassed
difference          115  =     +83            +0           +31          +1          +0
```

115 is exactly the number of tests remediation added (47 accounting + 32 JWT secret + 25
tenant isolation + 6 webhook + 4 ORM + 1 strict xfail). The +83 passed and +31 skipped
split is the gated tests skipping in the default lane, which is the intended behaviour and
was checked directly: with `AERORA_TEST_DATABASE_URL` and `AERORA_TEST_REDIS_URL` unset,
those six files report 83 passed, 31 skipped, 1 xfailed, 0 failed.

### One of the 17 was mine, and it is fixed

`tests/test_bandit_sast_gate_is_green.py::test_every_interpolation_into_sql_is_constrained_before_it_gets_there`
failed because of this pass, not before it. My rewrite of `add_foreign_keys_20260817.py`
assembled the `ALTER TABLE` statements with f-strings, and that gate exists precisely to
forbid it — it is the invariant the repository's `# nosec B608` suppressions rest on, and
it only accepts an interpolated SQL identifier when something guarantees it is one.

Reported rather than suppressed, and fixed by making the DDL literal text instead of
built from parts: each constraint now carries its own complete `ALTER` and `DROP` string,
nothing is interpolated into SQL anywhere in the revision, and the constraint name and
table survive only as **bound parameters** in the `pg_constraint` lookup. The gate is
green (3 passed) and the migration's behaviour is unchanged — re-provisioned from scratch
after the rewrite, the same 2 creatable constraints land, the same 5 are refused, head is
still reached, 185 policies on 72 tables.

A note on the gate itself: it caught a real mistake of mine within one suite run, which is
a good argument for keeping it exactly as strict as it is.

### The remaining failures

Re-running the seven files that failed gives **8 failures, 484 passed**. The difference is
informative:

- **8 deterministic.** `test_protected_logic_containment` (BUG-026, the `'vie'` substring
  false positive, pre-existing and recorded in the audit baseline);
  `test_exchange_safety_fix::test_connection_pooling_safety`;
  `test_strategy_status_publish` (4 tests);
  `test_task_8_4_runtime_readiness_gate::test_a_verified_checksum_clears_the_awaiting_model_hold`;
  `test_validation_sweep[library.clone_strategy]`.
- **7 order-dependent.** All of `test_task_6_1_backtest_execute.py` fails in the full suite
  and passes in isolation. That is the same transient class the audit baseline identified
  (12 of its 19 original failures behaved this way) and is test pollution, not a product
  defect — though it is worth fixing, because a suite that fails differently depending on
  ordering is a suite people stop reading.

**No failure in this run is attributable to the remediation work except the SAST gate,
which is fixed.** I did not re-run the full 53-minute suite after that fix, so the "8
deterministic" figure comes from a targeted re-run of the affected files rather than from a
complete pass — stated that way rather than presented as a clean full-suite number.

Tests added this pass: **31** new (25 tenant isolation, 6 webhook duplicate delivery), plus
**47** pre-existing PostgreSQL tests brought into execution for the first time. Tests added
across remediation overall: **125**.

**On the migration edits specifically.** Six migration files were changed, and several
tests in this repository assert against migration *source text* rather than behaviour. That
is the obvious place for an edit to break something, so it was checked directly instead of
waiting on the full suite: the nine files that reference the edited migrations by name —
`test_marketplace_paper_migrations.py`, `test_migration_tooling.py`,
`test_schema_as_code_completeness.py`, `test_schema_table_reference_drift.py`,
`test_referral_missing_tables.py`, `test_pr_check_postgres_service.py`,
`test_training_and_model_tables_migration.py`, `test_registry_snapshot_persistence.py` and
`test_marketplace_paper_schema_contract.py` — total **240 passed, 0 failed**.

**A note on the full suite, because the first attempt looked like a hang.** Run with
`--timeout=300` it stalls at 37% and never moves.
`test_p58_migration_set_is_idempotent` takes **16m41s** on its own — a Hypothesis test
driving a character-at-a-time SQL parser over the whole migration corpus — and
`pytest-timeout`'s thread method cannot interrupt a tight Python loop, so the run neither
progresses nor fails. That test passes; it is simply slower than any sane per-test timeout.
The Phase-1 baseline of 12,577 passed was measured with no timeout, which is why it
completed, and the comparable re-run here is also untimed. Recorded as a post-launch item,
because anyone who adds a timeout to CI will hit this and is likely to read it as a
deadlock.

---

## Honest summary

The audit found defects by reading. This pass found them by running, and that turned out to
be a different and larger category. Seventeen migration defects, two of which broke user
signup outright, were sitting in a provisioning process that had never been executed end to
end — because nothing in the repository ever ran migrations at all. The nine
tenant-isolation RLS policies on the execution and financial tables were not inert as the
audit recorded; they were absent, because the revision upstream of them could not commit.

What is genuinely better than the audit believed: `execution_records` has the primary key
the idempotency design needs, the `auth.uid()` policy family provides real tenant isolation
on the PostgREST path across six tenants and every operation, the `app.current_tenant_id`
predicates are correct and merely unadopted, the row-locking and deployment-uniqueness
controls work against a real server, and the webhook dedup is sound.

What is worse: the billing surface cannot be verified before it handles real money, the
referral payout path cannot work at all, and four tables holding customer financial records
may be readable with a public browser key — a question that takes one query to answer and
has not been answered.

Paper trading, which is the deployed configuration, is in reasonable shape. The blockers
are around it, not in it. That is why the verdict is NO-GO rather than something softer: the
remaining work is small, specific, and mostly not even code.

---

### Supporting artefacts

| Path | Contents |
|---|---|
| `scripts/provision_database.py` | The provisioner. `--dry-run` by default; refuses port 6543 and non-localhost |
| `staging/verify_schema.py` | 23 schema assertions against a real server |
| `staging/rls_inventory.py` | Policy inventory from `pg_policy`, by predicate family |
| `staging/exposure_check.py` | The four-table exposure finding |
| `staging/fk_typecheck.py` | All 7 FKs attempted independently under savepoints |
| `staging/try_alembic_head.py` | Head reachability, one revision at a time |
| `staging/run_pg_lane.py` | The CI PostgreSQL lane |
| `staging/bootstrap_pg.py` | PostgreSQL 16.2 lifecycle |
| `staging/verify_live_trading_off.py` | The five-environment live-trading gate probe |

### Reproducing any of it

The infrastructure this pass used was local and disposable, and has been shut down and
removed — no PostgreSQL or Redis process is left running, and the data directories and the
downloaded Redis build are gone. `.gitignore` now excludes those so they cannot be
committed; the verifiers themselves are small and were kept because the report cites them.

To rebuild from scratch:

```powershell
# PostgreSQL 16.2, from the pgserver wheel; no Docker required
$env:PG_DATA_DIR = "pgdata_local"
.venv\Scripts\python.exe staging\bootstrap_pg.py start
$dsn = (Get-Content staging\pg_dsn.txt -Raw).Trim()

# Full schema from repository source. --dry-run is the default; this applies.
.venv\Scripts\python.exe scripts\provision_database.py --dsn $dsn --apply --create-substrate

# The verifiers
.venv\Scripts\python.exe staging\verify_schema.py
.venv\Scripts\python.exe staging\rls_inventory.py
.venv\Scripts\python.exe staging\exposure_check.py
.venv\Scripts\python.exe staging\fk_typecheck.py
.venv\Scripts\python.exe staging\verify_live_trading_off.py

# The gated test lanes
$env:AERORA_TEST_DATABASE_URL = $dsn
.venv\Scripts\python.exe -m pytest tests/test_tenant_isolation_rls_postgres.py -q
.venv\Scripts\python.exe staging\run_pg_lane.py
```

Redis, for the webhook lane, was the `tporadowski/redis` 5.0.14.1 Windows build; set
`AERORA_TEST_REDIS_URL` to its URL and run
`tests/test_webhook_duplicate_delivery_real_redis.py`.

---

# Addendum — the post-remediation closure pass

**Date** 6 October 2026, later the same day.
**Scope** Close the four blockers above. No new audit was performed; the verifiers built
for BUG-028…038 were run, and what they found was fixed.

The nine new defects they found are BUG-039…BUG-048 in `FINAL_PRE_LAUNCH_BUGS.md`. This
section records what was *verified*, because that is the part a reader has to be able to
check.

## 1. Billing — the question that made this NO-GO, answered

It was not fine.

`staging/billing_exposure_probe.py` adopts each role and **executes the statement** rather
than inspecting `relrowsecurity`. On `invoices`, `payment_methods` and `subscriptions` it
found **22 distinct (role, table, verb) exposures**:

- `anon` could `SELECT` every row of all three tables;
- `authenticated` could `SELECT`, `UPDATE` and `DELETE` other customers' rows, and
  `INSERT` rows owned by another customer.

Root cause: `006_reconcile_production_database.sql` PART 11 creates correct owner-scoped
policies and never issues `ENABLE ROW LEVEL SECURITY`. A policy on a table with RLS
disabled is inert.

Reachable, not theoretical: `algo22-terminal/src/supabase.js` builds a browser Supabase
client from `VITE_SUPABASE_ANON_KEY`, so a logged-in customer holds a credential that
reaches PostgREST directly.

**Fixed, regressed, documented.** `migrations/hardening/999_privilege_hardening.sql`
section 1; 27 tests in `tests/test_billing_data_isolation_postgres.py`; full reasoning in
`BILLING_SECURITY_VERIFICATION.md`, which answers the eleven enumerated questions one at a
time.

Note what this does **not** establish: production was not checked. The exposure was in a
database built from this repository. Whether production has the same shape is item 2 of
[What converts this to PAPER-ONLY GO](#what-converts-this-to-paper-only-go).

## 2. Tenant isolation — measured across every readable table, not a sample

`staging/anon_read_sweep.py` seeds a real row owned by customer A and reads it as a
different principal, for every table that principal can `SELECT`:

```
as anon           49 tables:  0 leaked, 44 proven blocked, 5 intentionally public, 0 not proven
as authenticated  79 tables:  0 leaked, 69 proven blocked, 10 intentionally public, 0 not proven
```

`0 not proven` is the number that makes the rest meaningful. An earlier revision of this
sweep reported `0 leaked` while proving almost nothing: it counted only rows that happened
to exist, and 44 of 49 tables were empty. A role reads zero rows from an empty table
whether or not a policy exists. The seeding step is what converts the sweep from a
reassuring number into evidence, and tables that cannot be seeded are reported
`NOT PROVEN` rather than passed.

It found two defects a policy inventory cannot see:

- **`library_ratings`** had RLS *enabled* and a `SELECT` policy of `USING (true)` —
  filtering nothing while `relrowsecurity` reported it as secured (BUG-046).
- **`referral_codes`** carried two overlapping permissive policies. The redesign added the
  correct own-rows-only policy and never dropped 006's broader one; permissive policies
  are OR-ed, so any logged-in customer could enumerate **every referral code in the
  business with the `user_id` that owns it** (BUG-045).

Both fixed. The *class* is closed rather than the instances: a third `RAISE EXCEPTION`
backstop in hardening section 4 aborts provisioning on any constant-`TRUE` policy over a
per-customer table, and `tests/test_cross_tenant_read_sweep_postgres.py` (6 tests) runs the
sweep as a test. Full detail: `RLS_TENANT_ISOLATION_VERIFICATION.md`.

## 3. Provisioning — reproducible, and now checkable against a live database

`scripts/verify_provisioning.py` creates a scratch database, provisions it **twice** from
repository source, and diffs 16 dimensions:

```
RESULT: REPRODUCIBLE
  85 tables, 1135 columns, 85 primary keys, 109 foreign keys,
  122 unique and check constraints, 400 indexes, 188 policies,
  991 privilege grants, 36 functions, 61 triggers, 1 sequence,
  6 enum types, 49 recorded migrations          -- identical on both runs
```

Twice, not once, because re-running **was not idempotent and failed in the dangerous
direction**: a `GRANT ALL ON ALL TABLES` in the substrate re-granted 787 privilege rows on
run 2 while the migrations' own `REVOKE`s were correctly skipped. Run 2 ended less secure
than run 1 and nothing reported it (BUG-041).

`scripts/verify_schema_drift.py` is new and closes the gap `verify_provisioning.py`
cannot: it compares a *live* schema against the baseline snapshot. It was verified by
catching a real difference — pointed at a database provisioned before the
`referral_wallets` column-retention change, it reported the three missing columns and the
two changed file checksums and exited 1. Pointed at a freshly provisioned database:
`RESULT: NO DRIFT`, exit 0.

Runbook: `DATABASE_PROVISIONING_RUNBOOK.md`.

## 4. The probe set

```
staging/run_all_probes.py        -> all 10 probes exited 0
  exposure_check.py                 per-customer tables with RLS switched OFF
  permissive_policy_probe.py        RLS ON but policy predicate is TRUE
  truncate_probe.py                 the privileges row level security cannot filter
  billing_exposure_probe.py         the 22 original billing exposures
  library_ratings_probe.py          library_ratings documented write paths
  anon_read_sweep.py anon           behavioural: anon reads a seeded row
  anon_read_sweep.py authenticated  behavioural: another customer reads a seeded row
  which_referral_funcs.py           which referral_wallets shape the functions write
  verify_live_trading_off.py        live trading refused on every path
  verify_schema.py                  23 structural schema checks
```

`verify_live_trading_off.py` still refuses all five cases, including both environment
switches set — the refusal arrives from `ExecutionFlags.LIVE_TRADING_ENABLED`, a hardcoded
`False` at `core/safety_config.py:114` read from no environment variable. **Live trading
remains OFF and nothing in this pass changed that.**

## 5. Regression position

`staging/run_pg_suite.py` — the PostgreSQL-only lane, now 16 files:

```
16/16 file(s) green          184 passed, 1 xfailed, 1 deselected
```

Counting only the files created for this remediation and the one before it:

| file | tests |
| --- | --- |
| `test_tenant_isolation_rls_postgres.py` | 29 |
| `test_cross_tenant_read_sweep_postgres.py` | 6 |
| `test_billing_data_isolation_postgres.py` | 27 |
| `test_tenant_referential_integrity_postgres.py` | 30 |
| `test_pgcrypto_preflight_postgres.py` | 5 |
| `test_signup_integration_postgres.py` | 12 |
| `test_position_lifecycle_postgres.py` | 6 |
| `test_referral_money_lifecycle_postgres.py` | 12 |
| | **127** |

All against real PostgreSQL. The remaining 57 in the lane are pre-existing tests that
needed a real database and had never had one.

Full suite (SQLite lane, PostgreSQL and Redis deliberately unset):

| | previous pass | mid-pass | **final** |
| --- | --- | --- | --- |
| failed | 17 | 18 | **16** |
| passed | 12,660 | 12,659 | **12,661** |
| skipped | 101 | 193 | **203** |
| xfailed / xpassed | 6 / 3 | 6 / 3 | **6 / 3** |
| duration | 52m58s | 50m32s | **36m18s** |

The middle column is the run that caught the two defects this pass introduced. The right
column is after fixing them: **16 failures, one fewer than the baseline of 17.**

The skip count rose because the new PostgreSQL tests skip when
`AERORA_TEST_DATABASE_URL` is unset, which is correct — they would be meaningless against
SQLite, which has no roles and no RLS. Those 123 tests are not skipped in the lane built
for them; they are 16/16 green there, and the new `provisioning-and-isolation` CI job runs
them on every pull request.

### The 18 mid-pass failures, classified by re-running every one individually

**Two were mine, and both are fixed and verified green:**

| Test | Cause | Now |
| --- | --- | --- |
| `test_bandit_sast_gate_is_green::test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | 24 f-string SQL interpolation sites across my two new alembic revisions | **3 passed.** Fixed by adding `_validate_table_name` (raises, never sanitises) to both files and routing every identifier through it — and by *binding* the column defaults in `b2c3d4e5f6a7` rather than splicing them, since a default is a value. Not suppressed: the gate reads the AST and ignores `# nosec`. |
| `test_marketplace_paper_schema_contract::test_no_destructive_statements` | my `DROP COLUMN` on `referral_wallets` | **1 passed.** Requirement 24.7 forbids destructive migrations and the test was right. The three 006 money columns are now retained — nullable, defaulted, written by no installed function. |

Both gates catching a defect *I* introduced is the clearest evidence in this document that
they work.

**Eight are order-dependent.** All eight in `tests/test_task_6_1_backtest_execute.py` fail
in a full-suite run and **pass in isolation** — verified: 95 passed when that file was run
alongside three others. State is leaking between modules.
`POST_LAUNCH_IMPROVEMENTS.md` item 19.

**Eight are pre-existing and unrelated to this pass.** Confirmed by `git status`: none of
the files involved is in the set this pass modified.

| Test | Why it fails |
| --- | --- |
| `test_strategy_status_publish` (4 tests) | `DeployBlueprintIncomplete` — `strategy_service.py:442` refuses a graph blueprint lacking `exchange_id`/`buy_logic`/`sell_logic`/`risk`. The fixtures predate that refusal. |
| `test_task_8_4_runtime_readiness_gate::test_a_verified_checksum_clears_the_awaiting_model_hold` | pre-existing |
| `test_protected_logic_containment::test_trace_and_export_surfaces_contain_no_protected_logic` | pre-existing; related to BUG-026 |
| `test_exchange_safety_fix::test_connection_pooling_safety` | calls ccxt `load_markets()`, a **real network request to Binance**. No egress in this environment. Environmental, not a code defect — and it is also what made an earlier batch run appear to hang. |
| `test_validation_sweep[library.clone_strategy]` | answers 500 `"Failed to look up library entry"` because no Supabase is configured. The test's own message says it surfaces a genuine defect regardless. |

**Confirmed by re-running the whole suite after the fixes:** 16 failed, 12,661 passed,
203 skipped, 6 xfailed, 3 xpassed, in 36m18s. The failing set is exactly the eight
order-dependent plus the eight pre-existing — both of the ones this pass introduced are
gone, and the count is **one below the baseline of 17**.

No net regression, and the two gates that caught my own defects did so before any of this
was written down.

## 6. What this addendum does not establish

Restated here rather than left implied:

- **Production.** Not touched, not read, not verified. Every number above is a staging
  database built from this repository.
- **The deployment path.** The provisioner and the drift check are not invoked by any
  workflow.
- **Billing end-to-end.** Unchanged and unverifiable pre-production by design. The
  duplicate-webhook control is verified against real Redis; settlement, provider
  interaction and entitlement grant are not.
- **`service_role` paths.** `BYPASSRLS` means the application filter is the only control
  there, and it was not audited.
- **The application layer.** No security sweep was run against the running service.
- **BotRunner paper fills.** Still invisible to the paper-trading UI. The accounting
  divergence is reported, not fixed; the earlier claim that BotRunner cannot execute in
  paper mode was obsolete and has been corrected.

---

# Addendum 2 — the production drift gate

**Date** 6 October 2026, third pass.
**Objective** Determine whether production actually has the schema, constraints, grants,
RLS configuration, policies, triggers and security properties that the verified
remediation environment has.
**Outcome** The verification was built and proven safe to run against a live customer
database. **It was not run: no production DSN exists in this workspace.**

## 1. Why it did not run

`.env` contains placeholders only. Checked without printing any value:

| variable | what it holds |
| --- | --- |
| `DATABASE_URL` | a `sqlite` URL |
| `SUPABASE_URL` | a localhost `http` URL |
| `SUPABASE_KEY` | the literal string `"dummy_key"` |

Network egress was tested rather than assumed, because the earlier
`test_connection_pooling_safety` failure looked like a firewall: DNS resolves
(`supabase.com` → an A record) and an outbound TCP connection to port 443 succeeds. So
that ccxt failure is an API-level block, not a network one, and **a credential is the only
missing input.**

A GREEN / AMBER / RED classification is a statement about a database that was read. None
was. The classification is therefore **NOT CLASSIFIED**, which is not AMBER and must not
be rounded to it.

## 2. What was built

### `scripts/verify_production_schema.py` — read-only

All 16 comparison dimensions plus seven absolute security checks that do not depend on the
baseline being correct. Two gaps in the comparison surface were found and closed while
building it:

- **columns were not compared on identity/generated status.** A column that is
  `GENERATED ALWAYS AS IDENTITY` in one database and a plain integer with a sequence
  default in the other has the same name, type, nullability and often the same default,
  and behaves differently on INSERT.
- **tables were not compared on owner.** Ownership is a security property in this schema:
  an owner bypasses its own policies unless `FORCE ROW LEVEL SECURITY` is set, and this
  schema deliberately does not set it.

Read-only is **server-enforced and asserted**, not conventional:
`default_transaction_read_only` is set and then read back, and the script aborts if the
server does not confirm it — so a write would be rejected with SQLSTATE 25006 no matter
what the script did next. Port 6543 is refused, because session settings do not survive
Supabase's transaction pooler and that guarantee would become unverifiable. No row data is
read; only catalogues, plus `COUNT(*)` so that an empty table is not mistaken for a secure
one. The DSN is parsed and redacted — scheme, host, port, database — so no credential
reaches stdout or a report.

### `scripts/verify_production_security.py` — writes, commits nothing

The behavioural matrix the gate specifies: A creates a row; B attempts SELECT, UPDATE,
DELETE and a forged INSERT claiming A's identity; `anon` attempts all four; A re-reads its
own row.

**The gate asks for two things that cannot both be literal** — "do not modify production
data" and "create synthetic test records". A synthetic row is a modification. The
resolution is that nothing is **committed**: one transaction, `ROLLBACK` in a `finally`,
unconditionally. It refuses without an explicit acknowledgement flag, refuses if either
live-trading switch is set, refuses `VYOMQUANT_MODE=live`, refuses the pooler, and reports
its own rollback plus a count of leftover probe identities.

### The verifiers are gated before the database is

21 tests exist for no other purpose, because the production run will be the first time
any of this touches customer data:

| Property | Proof |
| --- | --- |
| the read-only session genuinely refuses DML | a write is attempted and asserted to fail with 25006 |
| it refuses DDL too | same, for `CREATE TABLE` |
| a full run changes nothing | row counts for every table plus catalogue object counts, before and after |
| no credential can leak | `redact()` tested against a password containing `@`, and against a DSN with no `@` at all |
| the pooler refusal does not echo the DSN | asserted |
| the security probe commits nothing | row counts across all of `public` plus `auth.users`, before and after a full run |
| …and actually measured something | ≥40 tables reaching `ISOLATED`, because a script that did nothing would also change nothing |
| it refuses without acknowledgement, and under live-trading env | 4 tests |

## 3. What the tooling found when pointed at the local database first

Not a formality — this is where the value of the exercise showed up. **BUG-049**:
`user_id` is declared with three different types across the schema, so 12 columns can
carry no foreign key to the customer they belong to. `invoices`, `payment_methods` and
`subscriptions` use `varchar(36)`; nine more including `exchange_keys` use `text`; 43
columns use the canonical `uuid`.

This is BUG-033's unfixed sibling — alembic `a1b2c3d4e5f6` normalised `tenant_id` and left
`user_id` alone. Impact is **integrity and retention, not confidentiality**: rows can name
a non-existent customer, and deleting a customer orphans rather than cascades, which for
`exchange_keys` means encrypted venue credentials outliving the account. RLS still
isolates all 12 tables, because their policies cast both sides — confirmed behaviourally,
with `invoices` and `subscriptions` both returning `ISOLATED`.

**The structural point matters more than the bug.** A drift check compares production to
this repository. BUG-049 is in the repository, so a drift check would report *no drift* and
both sides would be wrong. That is why `verify_production_schema.py` runs absolute checks
alongside the differential one, and it is the argument for pointing a new verifier at a
known-good database before trusting it anywhere.

Two further defects in the tooling itself, fixed: a paren-stripping bug that made
`auth.uid()` unrecognisable to the overlapping-policy detector, and a hand-maintained
ownership-column list missing `purchaser_id`. The second shares its root cause with a
false *negative* that did matter — `creator_id` was missing from the same list on the first
sweep and `creator_analytics` went unexamined. Fixed structurally:
`scripts/owner_columns.py` now derives the set from `pg_constraint`, and running it
immediately surfaced three more names no human had listed.

## 4. Local behavioural result, for calibration

```
58 ISOLATED    owner reads its own row, nobody else can
 0 LEAK        no cross-tenant or anon attempt succeeded anywhere
10 NO CONTROL  every attempt blocked INCLUDING the owner's -- NOT a pass
 2 NOT SEEDED  no row could be created -- nothing measured
ROLLBACK issued. synthetic identities still present after rollback: 0
```

The probe's first run reported **21 failures, all of them its own fault.** Four
calibration defects — it set only one of the schema's two policy families; it ran the
destructive attempts before the positive control; the foreign-key borrow overrode the
ownership column; and it only populated NOT NULL columns, leaving a NULL owner on the
blob-shaped tables. Each is written up at the end of `FINAL_PRE_LAUNCH_BUGS.md`, because a
reader is entitled to know why the numbers moved.

`NO CONTROL` is the honest residue and is deliberately not counted as a pass: every
attempt blocked including the legitimate one means the probe did not establish that the
policy *discriminates*, and `USING (false)` would produce the same output as perfect
isolation.

## 5. Status taxonomy, applied from here on

| | meaning | count in this work |
| --- | --- | --- |
| **LOCAL VERIFIED** | measured against PostgreSQL provisioned from this repository | the large majority |
| **PRODUCTION VERIFIED** | measured against the live customer database | **zero** |
| **NOT VERIFIED** | nothing measured | enumerated in `PAPER_ONLY_LAUNCH_READINESS.md` section 5 |

No row in any document in this set carries PRODUCTION VERIFIED, and no local pass has been
promoted into one.
