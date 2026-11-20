# Billing Security Verification

**Date** 6 October 2026
**Environment** Real PostgreSQL 16.2, schema provisioned from this repository by
`scripts/provision_database.py`, with Supabase's role model and default privileges
reproduced by `--create-substrate`.
**Method** Every statement below was executed and its outcome recorded. No result in this
document is inferred from reading a policy or a migration.

---

## Summary

| Question | Before | After |
|---|---|---|
| Can `anon` read every customer's invoices? | **YES** | No — `permission denied` |
| Can `anon` read every customer's stored card metadata? | **YES** | No — `permission denied` |
| Can `anon` read every customer's subscription records? | **YES** | No — `permission denied` |
| Can an authenticated user read another customer's billing rows? | **YES** | No — 0 rows |
| Can an authenticated user UPDATE another customer's billing rows? | **YES** | No — `permission denied` |
| Can an authenticated user DELETE another customer's billing rows? | **YES** | No — `permission denied` |
| Can an authenticated user INSERT a row owned by someone else? | **YES** | No — `permission denied` |
| Can a user still read their own invoices? | yes | **yes** (unchanged) |
| Can `service_role` still do its server-side work? | yes | **yes** (unchanged) |
| Can a browser credential TRUNCATE a table? | **YES, including `profiles CASCADE`** | No — `permission denied` |

**22 distinct exposures were confirmed by execution and are now closed.** Regression
coverage: `tests/test_billing_data_isolation_postgres.py`, 27 tests.

---

## 1. Which role the browser uses

`backend_app/core/dependencies.py:242-281` (`create_request_supabase_async`) builds the
per-request PostgREST client with:

```
apiKey:        SUPABASE_ANON_KEY
Authorization: Bearer <end user's JWT>
```

PostgREST resolves that to the **`authenticated`** role with `auth.uid()` populated from
the token. With no token, the same anon key resolves to **`anon`**.

The anon key ships in the browser bundle. It is not a secret, and the verification below
treats it as a credential every visitor holds.

**Confirmed independently, and this is what makes the findings below reachable rather
than theoretical.** `algo22-terminal/src/supabase.js:161` calls
`createClient(supabaseUrl, supabaseAnonKey, …)` with `import.meta.env.VITE_SUPABASE_ANON_KEY`
— so the shipped frontend holds a working PostgREST client, and after login its requests
carry the user's JWT and resolve to `authenticated`. The app already issues table queries
through it (`src/lib/downloadAnalyticsApi.js`), and the module's own stand-in client
enumerates "the Postgrest query-builder methods this app actually chains". A customer can
therefore issue an arbitrary `select` against any table their role is granted, and **RLS
is the only thing standing between that request and the row**.

`service_role` is the backend's key. It carries `BYPASSRLS` — confirmed from `pg_roles`:

```
rolname       rolcanlogin  rolbypassrls  rolsuper
anon          False        False         False
authenticated False        False         False
service_role  False        True          False
postgres      True         True          True
```

Note `anon` and `authenticated` are **NOLOGIN**: neither can open a direct PostgreSQL
connection. Their only route in is PostgREST.

---

## 2. What was wrong

`migrations/006_reconcile_production_database.sql` PART 11 created **correct**
owner-access policies on `invoices` and `subscriptions`:

```sql
CREATE POLICY "invoices_owner_access" ON public.invoices
    FOR SELECT TO authenticated USING (auth.uid()::text = user_id::text);
CREATE POLICY "invoices_service_role" ON public.invoices
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

and **never issued `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`** for either. PostgreSQL
does not consult a policy on a table whose `relrowsecurity` is false, so both policies
were inert. `payment_methods` had no policy at all.

Reading `pg_policies` would not have found this. The policies were present and correct;
the flag that made them do nothing is a separate fact.

Combined with Supabase's default
`GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role`, the
measured state was:

```
invoices
    RLS enabled=False  forced=False  policies=2
    anon          = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
    authenticated = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
payment_methods
    RLS enabled=False  forced=False  policies=0
    anon          = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
subscriptions
    RLS enabled=False  forced=False  policies=2
    anon          = DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
```

---

## 3. The access matrix, before

Two users were seeded with one billing row each in all three tables, then every
role/operation pair was executed. `staging/billing_exposure_probe.py`, exit 1:

```
TABLE: invoices
  [VISIBLE] anon                   read ALL rows            -> 2 rows, owners=[A, B]
  [VISIBLE] anon                   read A's row by PK       -> 1 row
  [VISIBLE] authenticated as A     read ALL rows            -> 2 rows, owners=[A, B]
  [VISIBLE] authenticated as A     read B's row by PK       -> 1 row   <-- CROSS-TENANT
  [VISIBLE] authenticated as B     read A's row by PK       -> 1 row   <-- CROSS-TENANT
  [ALLOWED] authenticated as A     UPDATE B's row           -> 1 row   <-- CROSS-TENANT
  [ALLOWED] authenticated as A     DELETE B's row           -> 1 row   <-- CROSS-TENANT
  [ALLOWED] authenticated as A     INSERT a row owned by B  -> 1 row   <-- FORGED
  [VISIBLE] service_role           read ALL rows            -> 2 rows  (expected)

TABLE: payment_methods   — identical, all eight exposed
TABLE: subscriptions     — identical except two writes blocked by a UNIQUE
                            constraint on user_id, which is not a security control;
                            the DELETE succeeded

RESULT: 22 EXPOSURE(S) CONFIRMED BY EXECUTION
```

`payment_methods` holds `brand`, `last4`, `expiry_month`, `expiry_year` and the provider's
`payment_method_id`. No full card number is stored, which limits the severity — but the
instrument metadata and the provider handle were readable and deletable by any anon-key
holder.

---

## 4. A second, worse finding: TRUNCATE is not filtered by RLS

Row level security filters **rows**. `TRUNCATE` is a table-level operation and PostgreSQL
never consults a policy for it. Supabase's default `GRANT ALL` includes `TRUNCATE`.

`staging/truncate_probe.py` — every attempt inside a SAVEPOINT and rolled back, so nothing
was destroyed — run *after* the per-table RLS fix was already in place:

```
profiles           rls=True pol=4  anon  TRUNCATE          refused: referenced by a FK
profiles           rls=True pol=4  anon  TRUNCATE CASCADE  *** SUCCEEDED ***
orders             rls=True pol=3  anon  TRUNCATE CASCADE  *** SUCCEEDED ***
execution_records  rls=True pol=3  anon  TRUNCATE          *** SUCCEEDED ***
referral_wallets   rls=True pol=3  anon  TRUNCATE          *** SUCCEEDED ***

12 TABLE/ROLE PAIR(S) CAN BE WIPED BY A BROWSER CREDENTIAL
```

`profiles` is the identity table every other table references, and `CASCADE` truncates the
referencing tables with it. That line is the whole database in one statement. Note also
that the plain-`TRUNCATE` refusals on `profiles` and `orders` were **not** a control — they
failed only because a foreign key referenced them, and `CASCADE` asks for the referencing
tables too, which the same role could also truncate. Incidental protection, measured and
then measured around.

### Reachability, stated accurately

**Not reachable through the documented client API today.** This is a blast-radius defect,
not a patched exploit, and overstating it would be wrong:

- `anon` and `authenticated` are `NOLOGIN` — neither can open a PostgreSQL connection.
- PostgREST exposes no `TRUNCATE` verb.
- No function in `public` performs a `TRUNCATE` — checked with
  `pg_get_functiondef(p.oid) ILIKE '%truncate%'`, which returns nothing, so there is no
  RPC path either.

It is still closed, for two reasons. It sets the blast radius of anything that goes wrong
later: with these privileges held, one SQL injection or one careless `SECURITY INVOKER`
function turns a row-scoped bug into total data destruction. And `TRIGGER` lets a role
attach a trigger to a table, which is the ability to run chosen code on another user's
write.

---

## 5. The fix

`migrations/hardening/999_privilege_hardening.sql`, applied by
`scripts/provision_database.py` as **STEP 4 — last, after every other file**.

### Why it runs last, and why that is not a detail

The hardening was first written into `006_reconcile_production_database.sql` and **did not
work there**. `006` runs mid-sequence and `migrations/create_plan_tables.sql` runs after
it, so the `IF EXISTS` guards silently skipped tables that did not exist yet and the ones
created later inherited the Supabase defaults anyway. Measured after that attempt: `anon`
still held `DELETE, INSERT, TRUNCATE, UPDATE` on all five plan tables.

A privilege posture can only be asserted once every object it applies to exists.

### What it does

**Section 0 — remove the privileges RLS cannot filter.** `REVOKE TRUNCATE, REFERENCES,
TRIGGER` from `anon` and `authenticated` on all tables, plus `ALTER DEFAULT PRIVILEGES` so
the next table created does not re-inherit them.

**Section 1 — per-customer tables.** `ENABLE ROW LEVEL SECURITY` so `006`'s policies
finally bind; `REVOKE ALL` from `anon`; narrow `authenticated` to `SELECT` on
`invoices`/`subscriptions` and to nothing at all on `payment_methods`,
`creator_analytics`, `marketplace_analytics` and `transaction_rollbacks`. Policies created
for `payment_methods` and `creator_analytics`, which had none.

**Section 2 — the plan catalogue** (`plans`, `plan_prices`, `plan_features`,
`plan_limits`, `plan_migration_mapping`): reads stay open because prices are public
product information; `INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER` revoked. `anon`
previously held write access to `plan_prices.price_cents` — what customers are charged.
RLS is deliberately **not** enabled here: there is no owning customer, so a policy would
have to read `USING (true)`, which filters nothing and only looks like a control. The
privilege revoke *is* the control.

**Section 3 — migration bookkeeping** (`alembic_version`, `schema_migrations`): revoked
from both browser roles. `anon` could previously set `alembic_version.version_num` and
make every later deployment reason from a false migration state.

**Section 4 — two backstops that ABORT provisioning.** The named lists above cannot cover
a table added later, and a warning in a provisioning log is a warning nobody reads. So the
file ends with two `RAISE EXCEPTION` checks: any table carrying a per-customer column that
still grants `SELECT` to `anon`, and any browser role still holding
`TRUNCATE`/`REFERENCES`/`TRIGGER`.

### ENABLE, not FORCE — and why that is deliberate

`routers/billing.py` reads `payment_methods` through SQLAlchemy at lines 1598 and 2576 via
`get_db`, and every DSN in this repository connects as `postgres`, which **owns** these
tables. A table's owner is exempt from its own policies unless `FORCE ROW LEVEL SECURITY`
is set, so `ENABLE` closes the PostgREST surface and leaves the API untouched. `FORCE`
would break those two endpoints.

`tests/test_billing_data_isolation_postgres.py::test_the_api_path_for_payment_methods_still_works`
asserts this explicitly, so a future change to `FORCE` fails there instead of in
production.

---

## 6. The access matrix, after

`staging/billing_exposure_probe.py`, exit **0**:

```
invoices         RLS=True  anon=(none)  authenticated=SELECT  service_role=full
payment_methods  RLS=True  anon=(none)  authenticated=(none)  service_role=full
subscriptions    RLS=True  anon=(none)  authenticated=SELECT  service_role=full

TABLE: invoices
  [DENIED_PRIVILEGE] anon               read ALL rows            -> permission denied
  [DENIED_PRIVILEGE] anon               read A's row by PK       -> permission denied
  [VISIBLE         ] authenticated as A read ALL rows            -> 1 row, owners=[A]
  [EMPTY           ] authenticated as A read B's row by PK       -> 0 rows
  [EMPTY           ] authenticated as B read A's row by PK       -> 0 rows
  [DENIED_PRIVILEGE] authenticated as A UPDATE B's row           -> permission denied
  [DENIED_PRIVILEGE] authenticated as A DELETE B's row           -> permission denied
  [DENIED_PRIVILEGE] authenticated as A INSERT a row owned by B  -> permission denied
  [VISIBLE         ] service_role       read ALL rows            -> 2 rows

TABLE: payment_methods — every browser operation DENIED_PRIVILEGE; service_role full

RESULT: No cross-customer or anonymous access succeeded.
```

The required matrix, explicitly:

| Actor | Own billing | Other customer's billing |
|---|---|---|
| User A | **can access** | **cannot access** |
| User B | **can access** | **cannot access** |
| Anonymous | cannot access | cannot access |
| `service_role` | full, server-side only | full, server-side only (intended) |

`staging/truncate_probe.py`, exit **0**: *No TRUNCATE succeeded for anon or authenticated.*

`staging/exposure_check.py`, exit **0**: 0 tables carrying per-customer data with RLS off
and no REVOKE.

---

## 7. Related finding: `creator_analytics`

Found by the same sweep after its column list was corrected — `creator_id` was missing
from the sweep's notion of a per-customer column, and that omission hid a real exposure.

`creator_analytics` holds `creator_id`, `total_earnings`, subscriber and clone counts. Its
declaring migration (`archived_migrations/root_migrations/008_create_creator_analytics.sql`)
sets no RLS, no policy and no grant, so it inherited `GRANT ALL`: one creator's earnings
were readable **and writable** by any visitor. Now RLS-enabled with an owner policy on
`creator_id`, and both browser roles revoked.

`marketplace_analytics` (platform-wide `total_revenue`, `top_creator_id`) is commercially
rather than personally sensitive and has no owning customer, so both browser roles are
revoked outright and no owner policy is invented.

---

## 8. What this does NOT verify

Stated plainly rather than left implicit.

- **Production.** Every result here is against a database provisioned from this
  repository. Production was deliberately not touched. Whether production's
  `invoices`/`payment_methods`/`subscriptions` currently have RLS enabled — possibly set
  through the Supabase dashboard rather than by a migration in this tree — **cannot be
  determined from here and must be checked before launch**:

  ```sql
  SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND relname IN ('invoices','payment_methods','subscriptions',
                    'creator_analytics','transaction_rollbacks');
  ```

  And the privilege half:

  ```sql
  SELECT table_name, grantee, privilege_type
  FROM information_schema.role_table_grants
  WHERE table_schema = 'public' AND grantee IN ('anon','authenticated')
    AND privilege_type IN ('TRUNCATE','REFERENCES','TRIGGER');
  ```

- **The payment provider path.** No checkout, settlement, refund or entitlement grant was
  exercised. `_validate_keys` accepts live key prefixes only, in every environment, with
  no test-credential bypass — so the billing paths cannot be exercised with sandbox
  credentials at all. See `FINAL_LAUNCH_READINESS_REPORT.md`.

- **Webhook signature and replay** are covered separately by
  `tests/test_webhook_duplicate_delivery_real_redis.py` (6 tests, real Redis), not here.

- **Column-level exposure.** RLS filters rows, not columns. A policy that correctly
  returns only the caller's own `invoices` row still returns every column of it. No
  column-level grant review was performed.

- **The `service_role` billing paths.** `service_role` holds `BYPASSRLS`, so for any
  billing read or write that goes through it the application's own filter is the only
  control. Not audited. `POST_LAUNCH_IMPROVEMENTS.md` item 15.

---

## 9. One finding since this document was first written

A **third** backstop was added to `999_privilege_hardening.sql` section 4 after a defect
was found that the first two could not see.

Both original backstops key on `relrowsecurity`. `library_ratings` had RLS **enabled** and
a `SELECT` policy of `USING (true)` — a predicate that applies no row filter at all, so
the table behaved exactly as if RLS were off while every `relrowsecurity` check reported
it as secured. The same shape then turned up on `referral_codes`, where
`referral_system_redesign.sql` added a correct own-rows-only policy and never dropped
006's broader `USING (user_id = auth.uid() OR is_active = true)`; permissive policies are
OR-ed, so the broad one still decided every outcome.

Neither is a billing table, and neither changes any result in sections 1–8. They are
recorded here because they change what this document's method is worth: **a verification
that asks only whether RLS is enabled would have passed both of them.** The sections
above do not rely on that question — every result in the access matrices was produced by
executing the statement as the role — and the two findings are what justified making that
the method rather than a preference.

Full detail: `RLS_TENANT_ISOLATION_VERIFICATION.md` sections 5.1 and 5.3, and BUG-045 /
BUG-046 in `FINAL_PRE_LAUNCH_BUGS.md`.

---

## Reproducing this

```powershell
$env:PG_DATA_DIR = "pgdata_local"
.venv\Scripts\python.exe staging\bootstrap_pg.py start
$dsn = (Get-Content staging\pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe scripts\provision_database.py --dsn $dsn --apply --create-substrate

.venv\Scripts\python.exe staging\billing_exposure_probe.py   # expect exit 0
.venv\Scripts\python.exe staging\truncate_probe.py           # expect exit 0
.venv\Scripts\python.exe staging\exposure_check.py           # expect exit 0
.venv\Scripts\python.exe staging\permissive_policy_probe.py  # expect exit 0 (section 9)

# or all ten probes at once
.venv\Scripts\python.exe staging\run_all_probes.py           # expect "all 10 probes exited 0"

$env:AERORA_TEST_DATABASE_URL = $dsn
.venv\Scripts\python.exe -m pytest tests/test_billing_data_isolation_postgres.py -v
```

---

## 10. Addendum — the behavioural matrix, and the production status

Sections 1–9 measured billing isolation against a database **provisioned from this
repository**. This section records the full write-path matrix and states plainly what
remains unverified.

### 10.1 The complete matrix, LOCAL VERIFIED

`scripts/verify_production_security.py` seeds a billing row owned by customer A, then
attempts every access path as customer B and as `anon`, and finally re-reads as A.
Everything inside one transaction ending in an unconditional `ROLLBACK`.

```
invoices                                     subscriptions
  owner_select         ALLOWED                 owner_select         ALLOWED
  b_select             NO ROWS                 b_select             NO ROWS
  b_update             REFUSED(42501)          b_update             REFUSED(42501)
  b_delete             REFUSED(42501)          b_delete             REFUSED(42501)
  b_forged_insert      REFUSED(42501)          b_forged_insert      REFUSED(42501)
  anon_select          REFUSED(42501)          anon_select          REFUSED(42501)
  anon_update          REFUSED(42501)          anon_update          REFUSED(42501)
  anon_delete          REFUSED(42501)          anon_delete          REFUSED(42501)
```

Both reach the `ISOLATED` verdict, which requires **two** things and not one: the owner
can read its own row, **and** nobody else can touch it. The positive control is what
separates isolation from an outage — a policy of `USING (false)` would produce seven
identical lines and a broken product.

Note the asymmetry between `NO ROWS` and `REFUSED(42501)`. B's SELECT is *filtered* by
RLS; B's writes are *refused* for want of a privilege. Both are correct outcomes, and the
difference is worth seeing: SELECT is the only verb `authenticated` holds on these tables,
so RLS is doing the work on reads and the grant is doing the work on writes. Two
independent controls, neither relied upon alone.

### 10.2 `payment_methods` reports NO CONTROL, deliberately

```
payment_methods
  owner_select         REFUSED(42501)
  (every other probe)  REFUSED(42501)
```

`authenticated` holds **no grant at all**. So the owner cannot read its own payment
methods through PostgREST either, and the probe correctly declines to call that a pass —
it records `NO CONTROL`, meaning isolation was not disproved and was not proved.

This is intentional and already documented at the site:
`999_privilege_hardening.sql` section 1 revokes both browser roles from
`payment_methods` while keeping an owner-scoped policy, because the table is served
exclusively by the API over SQLAlchemy and the policy is defence in depth should a grant
ever be re-added — which `ALTER DEFAULT PRIVILEGES` could do. The feature works because
`routers/billing.py` reads it as the owner.

Reported rather than quietly passed, because "nobody can read it" and "only the owner can
read it" are different properties and a billing document should not blur them.

### 10.3 `user_id` on all three billing tables is `varchar(36)` — BUG-049

`staging/identity_type_matrix.py` finds `invoices.user_id`, `payment_methods.user_id` and
`subscriptions.user_id` declared `character varying(36)` while `profiles.id` and
`auth.users.id` are `uuid`. A foreign key cannot span two types, so **none of the three
tables declares a reference to the customer whose money it records.**

Consequences, stated precisely:

- an invoice can name a customer who does not exist;
- deleting a customer does not cascade — there is no `ON DELETE` to cascade — so billing
  rows are orphaned. For invoices that is arguably the desired retention behaviour, but it
  is currently an accident rather than a decision;
- **isolation is unaffected.** The policies cast both sides,
  `(auth.uid())::text = (user_id)::text`, so the type mismatch creates no cross-tenant
  access — and 10.1 demonstrates that rather than arguing it.

Not fixed here. `ALTER COLUMN user_id TYPE uuid USING user_id::uuid` on tables holding
invoices needs its own review, a backup, and a precheck that **refuses with a count and a
sample** if any value is not a UUID — the discipline alembic `a1b2c3d4e5f6` already
established for `tenant_id`.

### 10.4 Production status

| Question | Status |
| --- | --- |
| Are the 22 exposures fixed in a database built from this repository? | **LOCAL VERIFIED** |
| Are they fixed in **production**? | **NOT VERIFIED** |
| Does production have RLS enabled on the three billing tables? | **NOT VERIFIED** |
| Can a logged-in customer read another customer's invoice in production? | **NOT VERIFIED** |

The query in section 8 is still the one to run, and
`scripts/verify_production_schema.py` now runs it as part of a wider check that also
reports the policies, their predicates, the browser-role grants and the row counts for all
three tables — read-only, server-enforced, with no credential in its output.

**Why it has not been run:** there is no production DSN in this workspace. `.env` holds a
`sqlite` `DATABASE_URL`, a localhost `SUPABASE_URL` and the literal string `"dummy_key"`.
Network egress was tested and works. A credential is the only missing input, and
fabricating a result in its absence is not an option — the whole value of this document is
that its conclusions were executed rather than assumed.

---

## 11. Addendum — billing identity integrity, and what production still owes

### 11.1 `user_id` on all three billing tables is now `uuid` with a foreign key

Section 10.3 recorded BUG-049 as open: `invoices.user_id`, `payment_methods.user_id` and
`subscriptions.user_id` were `character varying(36)` while `profiles.id` is `uuid`, so
**none of the three tables declared a reference to the customer whose money it recorded.**

All three are now `uuid` with `ON DELETE RESTRICT` to `profiles(id)`:

| constraint | action |
| --- | --- |
| `fk_bug049_invoices_user_id` | `RESTRICT` |
| `fk_bug049_payment_methods_user_id` | `RESTRICT` |
| `fk_bug049_subscriptions_user_id` | `RESTRICT` |
| `fk_bug049_billing_invoices_user_id` | `RESTRICT` |

### 11.2 `RESTRICT` and not `CASCADE`, deliberately

`CASCADE` on a billing table would mean that deleting a customer silently destroys their
invoices. For a system that has to answer tax and chargeback questions, that is the wrong
default, and it would be a default rather than a decision.

`SET NULL` was unavailable: all four columns are NOT NULL. So `RESTRICT`, which makes the
database refuse to delete a customer who still has financial records and forces whoever
builds an account-deletion path to decide what happens to them.

Checked before choosing it: there is **no account-deletion path in `backend_app/`** today
— no `DELETE FROM profiles`, no `delete_user`. `RESTRICT` constrains a flow that does not
exist yet, which is the right time to constrain it rather than discovering the question
after the first deletion.

Proven by executing it, not by reading the catalogue:

```
test_deleting_a_customer_who_has_an_invoice_is_refused   PASSED
  -> the DELETE raises a foreign-key violation
  -> the invoice is still present afterwards
```

### 11.3 Isolation was re-verified, because the conversion rewrote the policies

Retyping a column requires dropping and recreating every RLS policy on its table, so the
billing isolation established in sections 1–10 could not be assumed to carry over.

`invoices` and `subscriptions` needed no predicate change — they were already double-cast,
`(auth.uid())::text = (user_id)::text`, which type-checks against `uuid` as well as
`text`. `billing_invoices` did: its predicate was `(auth.uid())::text = user_id`, which
becomes `text = uuid` and raises. The conversion casts the column; the alternative, a
native `auth.uid() = user_id`, was rejected because uuid equality normalises case and
would therefore change who can see a row.

Re-measured afterwards:

- **27 tests** in `tests/test_billing_data_isolation_postgres.py` — all passing;
- the behavioural matrix still returns `ISOLATED` for `invoices` and `subscriptions`:
  owner reads its own row, another customer sees none, every write path refused;
- every restored policy **type-checks**, forced via `EXPLAIN` rather than inferred from
  its presence in `pg_policies` — because a policy that is merely present is not
  necessarily one PostgreSQL can evaluate, and that is precisely the state the first
  migration attempt would have left behind had it not rolled back.

Six read-back queries in the isolation tests changed from `SELECT user_id` to
`SELECT user_id::text`. No assertion was relaxed: psycopg2 renders a `uuid` column as a
`uuid.UUID` object, and the failures read `"user A sees other owners: [UUID('a0a0...')]"`
where `a0a0` **is** user A — the ownership asserted was exactly right and only the
comparison was comparing a `UUID` to a `str`.

`core/models/billing.py` was aligned to match: `PaymentMethodModel.user_id` is now
`Uuid(as_uuid=False)`. `as_uuid=True` would return `uuid.UUID` objects and silently
falsify every `row.user_id != user["id"]` comparison against a JWT claim.
`payment_methods.id` was deliberately left `character varying(36)` — it is a surrogate
key, not a customer identity, and BUG-049 is about identity columns.

### 11.4 Production status — unchanged, and this is the whole remaining gap

| question | status |
| --- | --- |
| Are the 22 exposures fixed in a database built from this repository? | **LOCAL VERIFIED** |
| Do the billing tables have RLS enabled in **production**? | **NOT VERIFIED** |
| Can a logged-in customer read another's invoice in **production**? | **NOT VERIFIED** |
| Do the billing tables have the identity foreign keys in **production**? | **NOT VERIFIED** |
| Is billing end-to-end verifiable at all? | **NO** — `_validate_keys` accepts live prefixes only, in every environment, by design |

`scripts/verify_production_schema.py` answers the first three in one read-only command and
reports the policies, their predicates, the browser-role grants and the row counts for all
three tables. It has not been run: `.env` holds a `sqlite` `DATABASE_URL`, a localhost
`SUPABASE_URL` and the literal `"dummy_key"`. Network egress was tested and works, so a
credential is the only missing input.

Fabricating a result was the alternative and it is not available. The value of this
document is that its conclusions were executed; a line claiming production is fine would
destroy that for every other line.

Full record: `staging/FINAL_PRODUCTION_VERIFICATION.md`.
