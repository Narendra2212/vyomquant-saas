# RLS / Tenant Isolation Verification

**Scope.** Row level security and cross-tenant data isolation on `public` and `auth`, as
provisioned from this repository by `scripts/provision_database.py`.
**Database.** PostgreSQL 16.2, provisioned fresh from this repository, 85 tables, 188
policies. Confirmed to match the provisioning baseline exactly:
`scripts/verify_schema_drift.py` → `RESULT: NO DRIFT`, exit 0.
**Method.** Behavioural. Every claim below was produced by seeding a real row owned by
one customer and then reading it as a different principal. Policy text was used to
*explain* results, never to establish them.
**Date.** 2026-10-06.

---

## 1. Verdict

| Question | Answer | Basis |
| --- | --- | --- |
| Can `anon` (browser key, no login) read any customer's row? | **No** | 49 tables measured, 0 leaked, 0 unmeasured |
| Can a logged-in customer read a different customer's row? | **No** | 79 tables measured, 0 leaked, 0 unmeasured |
| Can a logged-in customer modify a different customer's row? | **No** | UPDATE / DELETE / forged INSERT, per table |
| Does `service_role` see everything? | **Yes, by design** | `rolbypassrls = true` |
| Is the table owner (`postgres`) subject to RLS? | **No, by design** | owners are exempt unless `FORCE` is set |
| Is tenant isolation enforced for **production**? | **NOT VERIFIED** | this is a staging database built from this tree |

Three defects were found in the course of this verification, all now fixed and all with
a regression test that was shown to fail before the fix. They are in section 5.

The final row of that table is the important one and is not a formality. Everything here
was measured against a database **provisioned from this repository**. Production's schema
may have been changed through the Supabase dashboard, which leaves no migration in this
tree. Nothing in this document should be read as a statement about the production
database until the same verifiers are run against it.

---

## 2. What was measured, and how

### 2.1 The three principals

| Role | `rolcanlogin` | `rolbypassrls` | What it represents |
| --- | --- | --- | --- |
| `anon` | false | false | the browser key before login |
| `authenticated` | false | false | a logged-in customer, with `auth.uid()` from their JWT |
| `service_role` | false | **true** | the backend's own privileged client |
| `postgres` | true | true (superuser) | migrations and the provisioner |

`anon` and `authenticated` are `NOLOGIN`. They are never connected to directly; PostgREST
connects as an authenticator role and switches with `SET ROLE`. The tests reproduce that
exactly: `SELECT set_config('request.jwt.claim.sub', <uuid>, true)` followed by
`SET LOCAL ROLE authenticated`, both transaction-scoped so no identity survives into the
next test through the connection pool. `auth.uid()` reads that claim, so the input is the
same input a real request supplies.

### 2.2 Why policy inspection was not sufficient

Two of the three defects in section 5 are invisible to a policy inventory:

- 62 policies are granted to role `public`, which **includes** `anon`. A query filtering
  on `roles = '{anon}'` returns zero rows and reads as reassuring. It is not evidence.
- `relrowsecurity = true` says a table has RLS enabled. It says nothing about whether the
  policy predicate is `user_id = auth.uid()` or the constant `true`. `library_ratings` had
  the latter, so it reported as secured while filtering nothing.

The user instruction for the billing tables — do not mark this PASS from source
inspection — applies with equal force here, and the same discipline was used.

### 2.3 The breadth sweep

`staging/anon_read_sweep.py [anon|authenticated]` asks one question of every table where
the role holds `SELECT`:

> seed a row owned by customer A, adopt the role, `SELECT count(*)`

Everything runs inside a savepoint that is always rolled back, so the sweep leaves no
rows behind. Per-column values are synthesised from
`information_schema.columns`; enum columns take the first label from `pg_enum`; foreign
keys are satisfied by walking to the parent table and seeding it too, bounded at four
levels because the schema contains chains like
`strategy_versions → strategies → profiles`. CHECK constraints that a synthesised value
cannot satisfy are supplied from an explicit `CHECK_OVERRIDES` table, so "expected" is a
decision recorded in the file rather than a surprise.

**An earlier revision of this sweep reported "0 leaked" while proving almost nothing.** It
counted only rows that happened to be present, and 44 of 49 tables were empty. A role
reads zero rows from an empty table whether or not a policy exists. The seeding step is
what turned the sweep into evidence, and the distinction is preserved in the output: a
table that could not be seeded is reported `NOT PROVEN`, never as a pass.

### 2.4 Results

```
staging/anon_read_sweep.py anon
  49 table(s) in public grant SELECT to anon
  SUMMARY: 0 leaked, 44 proven blocked, 5 intentionally public, 0 not proven

staging/anon_read_sweep.py authenticated
  79 table(s) in public grant SELECT to authenticated
  SUMMARY: 0 leaked, 69 proven blocked, 10 intentionally public, 0 not proven
```

Evidence: `staging/sweep_anon.txt`, `staging/sweep_authenticated.txt`. Both exit 0.
Enforced by `tests/test_cross_tenant_read_sweep_postgres.py` (6 tests), which asserts
`leaked == 0`, asserts `not proven == 0` so no table can drop out of coverage, and holds
a floor on the number of tables covered so that a broken grant query cannot silently
empty the measurement.

The "intentionally public" tables are listed explicitly in the script: the five plan /
price catalogue tables, the four `*_allowed_transitions` state-machine lookups, and
`block_registry_snapshots`. None carries a per-customer column. A pricing page is
entitled to read its prices.

### 2.5 The depth tests

`tests/test_tenant_isolation_rls_postgres.py` — **29 tests, all passing** — takes the
opposite shape: a sample of tables, exercised thoroughly. Six fixed tenants
(`1111...`–`6666...`, fixed rather than random so a failure names the same ids every
run), each owning one row in every table under test.

| Claim | Tests |
| --- | --- |
| A tenant sees only its own row | 3 (one per `auth.uid()` table) |
| A keyed lookup of a foreign row returns nothing | 3 |
| UPDATE of a foreign row affects 0 rows | 3 |
| DELETE of a foreign row affects 0 rows | 3 |
| INSERT of a row owned by another tenant is rejected | 3 |
| No JWT claim means no rows | 3 |
| All six tenants are mutually isolated | 1 |
| `service_role` sees every tenant | 1 |
| The table owner is exempt from its own policies | 1 |
| Reviews are public, favourites are not | 3 |
| No per-customer table has an unconstrained policy | 1 |
| `app.current_tenant_id` policies deny when unset | 2 |
| `app.current_tenant_id` policies isolate when set | 2 |

The keyed-lookup tests exist because a policy that filters a list but not a lookup by
primary key would pass the first group and still leak. The "sees only its own row" tests
assert both directions — the tenant sees *something* and sees *only* its own — so a
policy of `USING (false)` cannot pass by denying everyone. That distinction is what
separates isolation from an outage, and it is asserted rather than assumed.

Billing gets its own file: `tests/test_billing_data_isolation_postgres.py`, 27 tests,
covering `invoices`, `payment_methods` and `subscriptions`. See
`BILLING_SECURITY_VERIFICATION.md`.

---

## 3. The policy inventory, for explanation

85 tables in `public`. 78 have RLS enabled, 7 do not, 0 use `FORCE`.

The 7 without RLS are `plans`, `plan_features`, `plan_limits`, `plan_prices`,
`plan_migration_mapping`, `alembic_version`, `schema_migrations`. None has a per-customer
column. For the catalogue tables RLS would need a policy of `USING (true)`, which filters
nothing and only creates the appearance of a control — so the control is a privilege
revoke instead, and `migrations/hardening/999_privilege_hardening.sql` section 2 says so
in as many words. The two migration-bookkeeping tables are revoked from both browser
roles entirely.

`transaction_rollbacks` has RLS enabled and **zero** policies. That is fail-closed: with
RLS on and no policy, a non-owner matches no rows. Deliberate, and verified by the sweep.

188 policies across 77 tables:

| | count |
| --- | --- |
| `ALL` | 105 |
| `SELECT` | 43 |
| `INSERT` | 20 |
| `UPDATE` | 14 |
| `DELETE` | 6 |

By predicate, the five largest families:

| predicate | policies |
| --- | --- |
| `true` | 58 |
| `user_id = auth.uid()` | 41 |
| `auth.uid() = user_id` | 26 |
| `(auth.uid())::text = user_id` | 14 |
| `tenant_id::text = current_setting('app.current_tenant_id', true)` | 9 |

51 of the 58 `true` policies are `service_role ALL`. `service_role` holds `BYPASSRLS`, so
a `USING (true)` policy grants it nothing it does not already have; these are intentional
and the sweep confirms they change nothing. The remaining 7 were examined one at a time,
and one of them was a defect — see 5.1.

These counts are two lower than before this verification (190 → 188) because two
superseded policies on `referral_codes` were dropped. See 5.3.

### 3.1 `FORCE ROW LEVEL SECURITY` is deliberately not used

A table's owner is exempt from its own policies unless `FORCE` is set. Enabling `FORCE`
on the billing tables would break `backend_app/routers/billing.py` at lines 1598 and
2576, which read through SQLAlchemy as `postgres`. That was measured, not guessed. The
tradeoff is recorded rather than silently taken: owner exemption is relied upon, so
compromise of the owner credential is not contained by RLS. See
`POST_LAUNCH_IMPROVEMENTS.md`.

### 3.2 `app.current_tenant_id` — sound, and inert

Nine policies, on `orders` and `positions`, compare `tenant_id::text` to
`current_setting('app.current_tenant_id', true)`. **Nothing on any request path sets that
setting.** `backend_app/core/tenant_db_context.py` provides the mechanism and its only
importers are tests; ADR-0006 records its adoption as not started.

With the setting absent the comparison yields NULL rather than true, so a role subject to
RLS matches **zero rows** — fail-closed, verified by
`test_guc_policy_denies_a_non_owner_when_the_setting_is_absent`. And when the setting *is*
installed the policies isolate correctly, verified by the companion test across all six
tenants.

Both halves were measured because together they decide the remediation. If the predicate
were wrong the policies would need rewriting; since it is right, adopting
`tenant_db_context` on the request path is sufficient — a materially smaller change to
recommend. Carried in `POST_LAUNCH_IMPROVEMENTS.md`.

This is a latent gap, not a live exposure: those two tables are reached by the backend as
`service_role` or `postgres`, both of which bypass or are exempt from RLS, so no request
currently depends on the GUC being set.

---

## 4. Privileges, and the ones RLS cannot filter

`anon` holds `SELECT` on 49 tables and `INSERT`/`UPDATE`/`DELETE` on 43.
`authenticated` holds `SELECT` on 79, `INSERT` on 65, `UPDATE` on 54, `DELETE` on 47.

Those grants are broad, and they are backstopped by RLS — which the sweeps in 2.4
confirm by measurement rather than by argument. **But RLS does not filter every
statement.** `TRUNCATE` ignores policies entirely: it is a table-level operation, so a
role holding `TRUNCATE` empties the table regardless of what any `USING` clause says.

`TRUNCATE public.profiles CASCADE` as `anon` **succeeded** on the provisioned database
before remediation. Also `orders`, `execution_records`, `referral_wallets`.

`migrations/hardening/999_privilege_hardening.sql` section 0 now revokes `TRUNCATE`,
`REFERENCES` and `TRIGGER` from both browser roles and sets `ALTER DEFAULT PRIVILEGES` so
tables created later do not re-acquire them. Verified by `staging/truncate_probe.py`
(exit 0) and asserted as a provisioning gate by section 4's second backstop.

**Reachability, stated precisely rather than dramatically.** `anon` and `authenticated`
are `NOLOGIN`, PostgREST exposes no `TRUNCATE` verb, and no function in the schema issues
one. So this was a latent blast-radius defect, **not** a confirmed remote exploit. It is
reported as the former. The reason it still mattered: `TRIGGER` on another user's table
and `TRUNCATE` on `profiles` are the two privileges that convert any future SQL-injection
or function-definer mistake from a row leak into total data loss.

By contrast, the browser **is** able to reach PostgREST directly:
`algo22-terminal/src/supabase.js` creates a Supabase client with
`VITE_SUPABASE_ANON_KEY`, so after login the browser holds a credential that authenticates
as `authenticated` and can issue arbitrary table reads. That is why the SELECT findings in
section 5 are reachable and the TRUNCATE finding is not — the distinction is a verb, not a
guess.

---

## 5. Defects found by this verification

All three were found by the breadth sweep, not by reading policies. Each fix was shown to
be load-bearing by restoring the defect and watching the test go red.

### 5.1 `library_ratings` — RLS enabled, predicate `true`

`POLICY library_ratings_select_authenticated FOR SELECT TO authenticated USING (true)`.
RLS on, no row filter, `relrowsecurity = true` in every inventory.

The table is dual-purpose, which is what makes the blanket policy wrong. `rating IS NOT
NULL` rows are marketplace reviews, public by design. `rating IS NULL` rows are
**favourites** — written by `POST /api/library/{id}/favorite`, read back by
`GET /api/library/favorites`, which `tests/property/test_tenant_isolation_matrix.py`
classifies `OWNER_SCOPED_LIST`.

**Fixed** by `999_privilege_hardening.sql` section 3b:
`USING (rating IS NOT NULL OR user_id = auth.uid())`. Server-side reads in
`routers/library.py` all go through the service client, so none changed behaviour.

**An honest note on impact.** At the moment the policy was found, `rating` was
`NOT NULL`, so every row was a review and the practical confidentiality loss was limited
to data intended to be public. The policy was a loaded gun rather than a fired one. It
became load-bearing immediately, because fixing 5.2 makes NULL-rating rows possible — and
the two changes are only correct together. Neither should be applied alone.

### 5.2 `library_ratings.rating NOT NULL` — two of three writers rejected

Found while fixing 5.1. `rating smallint NOT NULL` with no default, against three
documented write paths in `routers/library.py`:

```
staging/library_ratings_probe.py
  rating column: is_nullable=NO  default=(none)
    REJECTED  POST /{id}/favorite  (rating = NULL)      23502 not-null violation
    REJECTED  POST /{id}/clone     (rating key absent)  23502 not-null violation
    ACCEPTED  POST /{id}/rate      (rating = 5)
```

Two different failure modes, both bad. Favouriting raised, was caught by a bare
`except Exception`, and returned `HTTPException(500)` — for every user, every call, so
`GET /api/library/favorites` always returned empty. The clone marker's insert is wrapped
in `except _PostgrestAPIError` and logged as non-critical, so **cloning succeeded while
silently never recording that it happened**: `is_verified_clone` was false for every row in
the table, permanently.

`tests/test_marketplace_pipeline.py` asserts the clone marker is written, and passes. It
writes into `patched_db.stores["library_ratings"]`, a Python list, which does not enforce
`NOT NULL`. This is precisely the failure mode that motivated provisioning a real
database.

**Fixed** by alembic `c3d4e5f6a7b8`: `rating` is nullable, plus
`CHECK (rating IS NULL OR rating BETWEEN 1 AND 5)` — a range the original `smallint NOT
NULL` never actually enforced. The `downgrade()` refuses, with a count, if NULL-rating
rows exist, because restoring `NOT NULL` would mean deleting or inventing a rating for
every favourite and clone marker.

No shipped UI calls these three endpoints (`grep` for `favorite` across all `.js/.jsx/.ts/.tsx`
outside `node_modules`: no matches), so no customer was seeing the 500. That bounds the
severity; it does not change the defect.

### 5.3 `referral_codes` — a superseded policy that was never dropped

```
staging/anon_read_sweep.py authenticated       (before the fix)
  referral_codes      authenticated saw 1 of 1 row(s)
```

Two overlapping permissive SELECT policies:

| policy | from | predicate |
| --- | --- | --- |
| `referral_codes_select` | `006_reconcile_production_database.sql:569` | `user_id = auth.uid() OR is_active = true` |
| `referral_codes_select_own` | `referral_system_redesign.sql:91` | `user_id = auth.uid()` |

Permissive policies are **OR-ed**. The redesign added the correct policy and never
dropped the old one, so the broad predicate still decided every outcome and the redesign
achieved nothing for confidentiality. Since every code is active, `is_active = true`
reduces to `true`: any logged-in customer could run one unfiltered select and obtain
**every referral code in the business together with the `user_id` that owns it**.
`REFERRAL_SYSTEM_REDESIGN_REPORT.md` states the intent as "Users can only see their own
referral code". It was not achieved.

This one is reachable, by the route in section 4: the browser holds the anon key, so a
logged-in customer can issue the select directly.

**Fixed as a pair,** because dropping the policy alone would have broken a feature.
`POST /referral/validate` looks up *somebody else's* code — that is what validation is —
and it was using `get_request_supabase`, a client bound to the caller's JWT, so it
depended on the broad policy.

1. `migrations/referral_system_redesign.sql` now drops `referral_codes_select` and
   `referral_codes_insert`, with the measurement recorded next to the drop.
2. `routers/referral.py` gains `_get_service_client()`, and the validate lookup runs as
   `service_role`.

The response is unchanged — `valid`, plus `referrer_id` **for a code the caller already
supplied**. Knowing one code's owner is the referral feature working; enumerating all of
them is not. The privilege moved to the server and the disclosure stayed where it was.

A latent bug in the same handler was fixed in passing: the `except` branch logged
`request.referral_code`, but `request` is the FastAPI `Request` and the body is
`request_body`, so the handler itself raised `AttributeError` and replaced every real
error with a misleading one.

### 5.4 Closing the class, not the instance

Both 5.1 and 5.3 are the same shape — a policy that exists and does not discriminate —
and neither is visible to a check that asks whether RLS is enabled. Three gates were added
rather than three point fixes:

1. `999_privilege_hardening.sql` section 4, third backstop: provisioning **aborts** if any
   table with a per-customer column has a permissive policy whose predicate is the
   constant `true` for `anon`, `authenticated` or `public`. `service_role` excluded, for
   the `BYPASSRLS` reason above.
2. `tests/test_tenant_isolation_rls_postgres.py::test_no_per_customer_table_has_an_unconstrained_policy`
   — the same sweep as a test, so a new table fails a run even if nobody re-provisions.
3. `tests/test_cross_tenant_read_sweep_postgres.py` — the behavioural sweep, including the
   `not proven == 0` assertion that stops a table from dropping out of coverage.

The definition of "per-customer column" is 12 names and now appears in exactly three
places, with a comment in each saying they must not drift. An earlier revision of
`staging/exposure_check.py` was missing `creator_id`, and that single omission is why
`creator_analytics` — `creator_id` plus `total_earnings` — was missed on the first pass.

---

## 6. Re-running this verification

```powershell
# 1. a real PostgreSQL instance
$env:PG_DATA_DIR = "pgdata_z1"
.venv\Scripts\python.exe staging\bootstrap_pg.py start

# 2. provision from this repository
.venv\Scripts\python.exe staging\reprovision.py

# 3. the breadth sweeps -- both must exit 0 with "0 not proven"
.venv\Scripts\python.exe staging\anon_read_sweep.py anon
.venv\Scripts\python.exe staging\anon_read_sweep.py authenticated

# 4. the targeted probes -- all must exit 0
.venv\Scripts\python.exe staging\exposure_check.py            # RLS switched OFF
.venv\Scripts\python.exe staging\permissive_policy_probe.py   # RLS ON, predicate TRUE
.venv\Scripts\python.exe staging\truncate_probe.py            # what RLS cannot filter
.venv\Scripts\python.exe staging\billing_exposure_probe.py    # 22 original exposures
.venv\Scripts\python.exe staging\library_ratings_probe.py     # the write paths

# 5. the test suites
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging\pg_dsn.txt)
.venv\Scripts\python.exe -m pytest tests\test_tenant_isolation_rls_postgres.py `
    tests\test_cross_tenant_read_sweep_postgres.py `
    tests\test_billing_data_isolation_postgres.py -q
```

To confirm the regression tests are load-bearing rather than merely green:

```powershell
.venv\Scripts\python.exe staging\regress_old_ratings_policy.py   # restore the defect
.venv\Scripts\python.exe -m pytest tests\test_tenant_isolation_rls_postgres.py -q
#   -> 2 failed, as recorded in section 5.1
.venv\Scripts\python.exe staging\apply_hardening.py              # restore the fix
```

---

## 7. What this document does not establish

- **Production.** Everything here is a staging database built from this tree. Production
  may differ, and a dashboard-applied change leaves no trace in these files. Running
  section 6 against production is a prerequisite for any claim about it.
- **Application-layer authorisation.** This is about what the *database* permits. An
  endpoint that queries as `service_role` and forgets its own `WHERE user_id = ...` is
  not constrained by anything measured here. RLS is the second line, not the first.
- **The `auth` schema.** `auth.users` was seeded and read as part of the fixtures, but
  Supabase's own GoTrue policies on `auth.*` were not independently audited.
- **Column-level exposure.** RLS filters rows, not columns. A policy that correctly
  returns the caller's own row still returns every column of it. No column-level grant
  review was performed.
- **Service-role ownership paths.** Which backend code paths run as `service_role`, and
  whether each applies its own tenant filter, was **NOT VERIFIED**. This is carried as an
  open item in `FINAL_LAUNCH_READINESS_REPORT.md`, and it is the single largest remaining
  gap in the isolation story: `service_role` holds `BYPASSRLS`, so for those paths the
  application filter is the *only* control.

---

## 8. Addendum — the full behavioural matrix, and the production status

Added in the production-drift pass. Section 2 of this document measured whether a role
could **read** a row it should not. This section measures the complete matrix the launch
gate specifies, including writes, and records what remains unverified.

### 8.1 The matrix

`scripts/verify_production_security.py`, per table carrying an ownership column:

| probe | question |
| --- | --- |
| `owner_select` | can A read its own row? **The positive control.** |
| `b_select` | can a different logged-in customer read A's row? |
| `b_update` | can B modify A's row? |
| `b_delete` | can B delete A's row? |
| `b_forged_insert` | can B insert a row claiming A's identity? |
| `anon_select` / `anon_update` / `anon_delete` | can a caller with no JWT do any of it? |

Everything runs inside one transaction that ends in an unconditional `ROLLBACK`, so the
database is unchanged. Proven by `tests/test_production_security_probe_rolls_back_postgres.py`,
which fingerprints row counts across every table in `public` plus `auth.users` before and
after a complete run.

### 8.2 Result — LOCAL VERIFIED

```
64 ISOLATED    owner reads its own row, nobody else can
 0 LEAK        no cross-tenant or anon attempt succeeded anywhere
 5 NO CONTROL  every attempt blocked INCLUDING the owner's
 1 NOT SEEDED  no row could be created
```

`invoices` is the model result and worth quoting in full, because it is the table the
launch gate cares about most:

```
invoices
  owner_select         ALLOWED
  b_select             NO ROWS
  b_update             REFUSED(42501)
  b_delete             REFUSED(42501)
  b_forged_insert      REFUSED(42501)
  anon_select          REFUSED(42501)
  anon_update          REFUSED(42501)
  anon_delete          REFUSED(42501)
```

The owner reads its own row; another customer sees none; every write path is refused
outright for want of a privilege. That is isolation demonstrated, not inferred.

### 8.3 Why `NO CONTROL` is reported and not counted as a pass

Ten tables blocked every attempt **including the owner's own**. That is not evidence of
isolation. A policy of `USING (false)` blocks every attack and also breaks the product,
and it produces output indistinguishable from perfect isolation unless the positive
control is measured separately. Two causes, kept apart in the output:

Only five tables remain in this category, and four of them are the same four:

**(a) `authenticated` holds no grant at all** — `payment_methods`, `creator_analytics`,
`marketplace_analytics`, `transaction_rollbacks`. More restrictive than required, not
less, and intentional in every case:

- `payment_methods` is served exclusively over SQLAlchemy, and
  `999_privilege_hardening.sql` revokes both browser roles while keeping an owner-scoped
  policy as defence in depth should a grant ever be re-added. The comment at that line
  says so.
- `creator_analytics` and `marketplace_analytics` were exposures; the hardening revoked
  them.
- `transaction_rollbacks` has RLS enabled with **zero** policies — fail-closed by design,
  as recorded in section 3.

**(b) a grant exists but no policy matched the seeded row** — `referral_profiles` alone.
Reported as a coverage gap rather than dressed up in either direction. One table out of
seventy, and the remaining `NOT SEEDED` entry is `library_subscriptions`, where a unique
constraint on `(user_id, library_id)` collides with a row the probe created earlier in
the same transaction.

**How this category shrank from ten tables to five.** Not by changing a threshold. Four
calibration defects in the probe were found and fixed, the last of them a single index:
`fks[name]` is `(schema, table, column)` and the ownership check read `ref[2]` — the
referenced *column*, almost always `id` — so the comparison was never true and **every
ownership column that had a foreign key fell through to the generic parent-borrow**. The
seeded row ended up owned by whichever customer happened to be first in the parent table,
and the owner's own-row SELECT correctly found nothing.

It was found by printing the statement the seeder actually executed
(`staging/diagnose_seed.py`), which named a borrowed `user_id` rather than the probe's.
Reasoning about the code had already produced three wrong hypotheses.

### 8.4 Overlapping permissive policies — all thirteen read

Section 5.3 found `referral_codes` carrying two permissive SELECT policies where the
broader one decided every outcome. Permissive policies are OR-ed, so that shape is a
general hazard, and the schema has thirteen instances of it.

`staging/overlapping_policy_detail.py` prints every predicate and judges each disjunct.
All thirteen pairs are ownership-scoped on both sides, with one exception read and
accepted with a written reason:

- **`marketplace_listings [SELECT]`** — `All users can view published listings` is
  `USING (is_published = true)` granted to `public`, which includes `anon`. That is the
  marketplace storefront and the breadth is the feature. The companion policy adds the
  owner's *unpublished* rows, which is all it can add. Checked: the table carries no
  financial column — settlement figures live in `marketplace_settlements`, which is
  owner/purchaser scoped.

The acceptance is an allowlist entry with the justification next to it, not a suppression:
any pair not named there still fails the probe.

Two false positives were fixed in the detector itself along the way — a paren-strip that
ate `auth.uid()`, and a missing `purchaser_id` in the ownership-column list. The second
shares its root cause with the `creator_id` omission that produced a false *negative* on
the first sweep, and is now fixed structurally: `scripts/owner_columns.py` derives the set
from `pg_constraint` and keeps the written list only as a floor.

### 8.5 Identity column types — a finding against this schema

`staging/identity_type_matrix.py`: 81 identity columns, 43 correct, **12 of the wrong
type**, 26 `uuid` without a declared reference.

`user_id` is `uuid` on most tables, `character varying(36)` on `invoices`,
`payment_methods` and `subscriptions`, and `text` on nine others including
`exchange_keys`. A foreign key cannot span two types, so those 12 declare no reference to
the customer they belong to.

**This does not affect isolation** — the policies cast both sides, and section 8.2 shows
`invoices` and `subscriptions` returning `ISOLATED`. It affects integrity and retention:
a row can name a non-existent customer, and deleting a customer orphans rather than
cascades. Recorded as BUG-049.

### 8.6 Production status

| | |
| --- | --- |
| Isolation **LOCAL VERIFIED** | yes — sections 2, 5 and 8 above |
| Isolation **PRODUCTION VERIFIED** | **no** |
| Reason | no production DSN exists in this workspace. `.env` holds a `sqlite` URL, a localhost Supabase URL and the literal `"dummy_key"`. Network egress was tested and works, so a credential is the only missing input. |
| Tooling ready | yes — `scripts/verify_production_security.py`, gated by 9 tests proving it commits nothing |

Running it is one command, in `PAPER_ONLY_LAUNCH_READINESS.md` section 1. Until then,
every result in this document describes a database provisioned from this repository, and
**nothing here is a statement about production.**

---

## 9. Addendum — isolation after the BUG-049 conversion

The twelve customer-identity columns became `uuid` and gained foreign keys
(`alembic d4e5f6a7b8c9` / `migrations/hardening/998_bug049_identity_types.sql`). That
required dropping and recreating every RLS policy on those tables, so the isolation
claims in this document had to be re-established rather than assumed to carry over.

### 9.1 The policies could not be restored verbatim, and the first attempt failed

`a1b2c3d4e5f6` restored policies verbatim and that was correct there — every predicate it
moved was already double-cast. These were not. `billing_invoices` carried

```sql
USING ((auth.uid())::text = user_id)
```

Left side cast, column bare. Valid against a `text` column; against a `uuid` column it
raises `operator does not exist: text = uuid`. Alembic's transactional DDL rolled the
whole revision back — 188 policies intact, columns still `text`.

14 of the 29 captured predicates needed the column cast to `(user_id)::text` to type-check
again. The rewrite is applied **only** when the server itself rejects the verbatim form
with SQLSTATE 42883, so a predicate that still type-checks is never touched, and the
result is the same double-cast shape `invoices` already used.

The alternative — rewriting to a native `auth.uid() = user_id` — was rejected because uuid
equality normalises case and would therefore **change who can see a row**. A type
conversion is not the place for that. Carried to `POST_LAUNCH_IMPROVEMENTS.md` item 23.

### 9.2 Isolation re-verified, not assumed

| claim | result |
| --- | --- |
| RLS still enabled on all 12 converted tables | **LOCAL VERIFIED** — 12 tests |
| Every table still has at least one policy | **LOCAL VERIFIED** — 12 tests |
| Every restored policy **type-checks** | **LOCAL VERIFIED** — forced via `EXPLAIN`, 11 tests + 1 documented skip |
| Billing isolation unaffected | **LOCAL VERIFIED** — 27 tests |
| Tenant isolation unaffected | **LOCAL VERIFIED** — 29 tests |
| Behavioural cross-tenant matrix | **LOCAL VERIFIED** — 64 ISOLATED, **0 LEAK** |

The `EXPLAIN` check is the one worth drawing out. A policy that is merely *present* is not
necessarily one PostgreSQL can evaluate — that is exactly the state the first migration
attempt would have produced had it not rolled back. `EXPLAIN` plans the query, which
forces every operator in the predicate to resolve, without reading a row.

The single skip is `payment_methods`, where `authenticated` holds no grant at all, so its
policy cannot be exercised from that role. Recorded as a skip with a reason — **not** as a
pass. It is the same table that reports `NO CONTROL` in section 8.3, for the same reason,
and it is intentional: the table is served exclusively over SQLAlchemy.

### 9.3 Deletion behaviour is now part of the isolation story

Before this change, no converted table declared a reference to the customer owning its
rows. RLS controlled *who could read* a row; nothing controlled *what happened to it when
the customer left*. For `exchange_keys` that meant an encrypted exchange API key could
outlive the account — a credential nobody could audit, rotate or revoke through the
product, and invisible to every check in sections 1–8 because it is not a read-access
question at all.

Both directions are now proven by executing the deletion:

- deleting a customer **removes** their `exchange_keys` rows (`CASCADE`);
- deleting a customer who has an invoice is **refused**, and the invoice survives
  (`RESTRICT`);
- inserting a credential for a customer who does not exist is **rejected**.

`tests/test_bug049_identity_types_postgres.py`. The `RESTRICT` choice was made only after
confirming there is no account-deletion path in `backend_app/` to break.

### 9.4 Three further policy-defect classes are now checked

`scripts/verify_production_schema.py` section 3 grew from three checks to six, so the
checker detects the **class** rather than the tables where each class was first found:

| | class | local result |
| --- | --- | --- |
| 3a | constant-`TRUE` predicate over a per-customer table | **0** |
| 3b | overlapping permissive policies OR-ing into broader access | 13, all predicates read, 1 accepted by design |
| 3c | per-customer table with RLS off while `anon` holds `SELECT` | **0** |
| 3d | policy depending on a session GUC nothing sets | 9 — **AMBER**, fail-closed |
| 3e | policy on a per-customer table naming no ownership column | 1, accepted by design |
| 3f | policy naming `anon` on a per-customer table | **0** |

3d is the nine `app.current_tenant_id` policies from section 3.2. AMBER rather than RED
because an unset GUC yields NULL and matches zero rows — fail-closed. It is still reported,
because those policies provide no isolation while appearing to, and a change that starts
setting the GUC on one code path would alter access silently and untested.

3e initially reported four, three of which were `profiles`' own correct
`auth.uid() = id` policies — `profiles` is owned by its `id`, and a bare `id` is excluded
from the global ownership-column list because including it would match every table. Fixed
rather than accepted: a checker that calls the most sensitive table in the schema defective
is a checker whose output gets ignored, which is how a true positive gets missed.

### 9.5 Production status, unchanged

Still **NOT VERIFIED**. Every result above is a database provisioned from this repository.
The tooling to answer the question for production exists, is gated by 41 tests, and needs
a credential. See `staging/FINAL_PRODUCTION_VERIFICATION.md`.
