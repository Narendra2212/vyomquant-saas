# Final Production Verification

**Date** 6 October 2026.
**Scope** Determine whether production has the schema, constraints, grants, RLS
configuration, policies, triggers and security properties that the verified remediation
environment has.

---

## Production classification

> # NOT CLASSIFIED
>
> **Production could not be accessed. No production DSN exists in this workspace.**

This is the only honest classification available, and it is **not** AMBER. AMBER means a
verified, explicitly understood, non-blocking risk. Unknown production state is not a
non-blocking risk; it is an unknown, and the launch gate says so in as many words.

### Why it could not be accessed

`.env` contains placeholders only. Checked without printing any value:

| variable | what it holds |
| --- | --- |
| `DATABASE_URL` | a `sqlite` URL |
| `SUPABASE_URL` | a localhost `http` URL |
| `SUPABASE_KEY` | the literal string `"dummy_key"` |

Network egress is **not** the obstacle, and that was tested rather than assumed: DNS
resolves (`supabase.com` returns an A record) and an outbound TCP connection to port 443
succeeds. A credential is the only missing input.

No credential was sought elsewhere, no key store was read, and no connection was
attempted against any host.

### What is needed to replace this classification

```powershell
# 1. READ-ONLY. Server-enforced: default_transaction_read_only is set and read back,
#    and the tool aborts if the server does not confirm it. Reads no row data. Refuses
#    the 6543 pooler. Prints no credential.
python scripts\verify_production_schema.py --dsn "<PRODUCTION_DSN>" `
       --out staging\production_schema.json

# 2. Then, as a separate deliberate act: the behavioural half. Writes synthetic rows and
#    commits NOTHING -- one transaction, unconditional ROLLBACK. Refuses without the flag.
python scripts\verify_production_security.py --dsn "<PRODUCTION_DSN>" `
       --i-understand-this-writes-and-rolls-back `
       --out staging\production_isolation.json
```

Use the **direct** PostgreSQL endpoint on 5432. Both tools refuse port 6543, because
session settings do not survive Supabase's transaction pooler and the read-only guarantee
and the `SET LOCAL ROLE` switching both depend on session state.

Before running the second one, confirm:

```
VYOMQUANT_ENABLE_LIVE_TRADING  != true
AERORA_ENABLE_LIVE_TRADING     != true
VYOMQUANT_MODE                 != live
```

The script checks all three itself and refuses rather than overriding. If any is set,
**stop**.

---

## What WAS completed

### 1. BUG-049 — FIXED in the repository baseline

The launch gate required this before production classification, on the grounds that the
baseline must not carry a known customer-identity type defect while the comparison is
pending. A drift check compares production to this repository; if the repository is wrong,
"no drift" is reported and both sides are wrong.

**Enumerated and classified before anything was altered.**
`scripts/bug049_enumerate.py` examined **81** identity-like columns and wrote
`BUG049_IDENTITY_MATRIX.json` with, per column: schema, table, column, current type,
nullability, default, generated/identity status, referenced table and column, existing
foreign key, ON DELETE action, RLS state, the policies referencing it, whether it is
semantically customer-owned, the recommended type, foreign key and deletion behaviour, and
a classification with the evidence that decided it.

| class | meaning | count |
| --- | --- | --- |
| A | canonical customer identity | 72 |
| B | tenant identity | 5 |
| C | external provider identifier — **must not be converted** | 0 |
| D | reference to a non-customer table, or arbitrary text | 4 |
| E | legacy identifier requiring data migration | 0 |
| — | unclassified | 0 |

Only classes A and B were candidates. The class C rule exists because converting
`provider_reference` to a uuid would destroy the only link to Stripe or Razorpay; it did
not fire, and saying so plainly is more useful than claiming a finding. The four class D
columns are `paper_*.account_id`, which are `uuid` references to `paper_accounts` — not a
customer — and were correctly excluded.

**Twelve columns needed conversion.** All twelve are now `uuid` with a foreign key to
`profiles(id)` and an ON DELETE chosen per table:

| ON DELETE | tables | why |
| --- | --- | --- |
| `CASCADE` | `exchange_connections`, `exchange_keys`, `notification_settings`, `referral_profiles`, `risk_settings`, `strategy_limits` | operational per-customer data. `exchange_keys` and `exchange_connections` are the security cases: an orphaned row is an **encrypted exchange API key with no owner**, which cannot be audited, rotated or revoked through the product |
| `RESTRICT` | `billing_invoices`, `invoices`, `payment_methods`, `subscriptions`, `risk_settings_audit`, `security_logs` | financial and audit records that must SURVIVE customer deletion. `CASCADE` would destroy them. Every one of these columns is NOT NULL so `SET NULL` is impossible; `RESTRICT` forces an account-deletion path to handle them explicitly |

`RESTRICT` was chosen only after checking that it breaks nothing: there is **no
account-deletion path in `backend_app/`** today — no `DELETE FROM profiles`, no
`delete_user`. It constrains a flow that does not exist yet, which is the right time.

**No financial column was dropped. No customer data was deleted.**

**It refuses rather than guesses.** Both implementations inventory every column for
values that are not UUID-shaped before altering anything, and RAISE with the table,
column, count and a sample if they find any. Stated plainly: on this database all twelve
tables are **empty**, so the local inventory is vacuously clean and establishes nothing
about production. `BUG049_IDENTITY_MATRIX.json` records that caveat as its own field
(`convertibility_unproven_empty_table`), and the migration re-runs the inventory wherever
it is applied — which is the only place the question can be answered.

**Regression tests: 76 (75 pass, 1 documented skip).**
`tests/test_bug049_identity_types_postgres.py` asserts the type, the foreign key and the
exact ON DELETE per table — and then asserts BEHAVIOUR, which is what matters:

- inserting an exchange credential for a customer who does not exist is **rejected**;
- deleting a customer **removes** their exchange credentials;
- deleting a customer who has an invoice is **refused**, and the invoice survives;
- a schema-wide sweep fails if any `user_id`/`tenant_id` anywhere is not `uuid`;
- RLS is still enabled and every policy came back and **type-checks** (forced via
  `EXPLAIN`, because a policy that is merely present is not necessarily one PostgreSQL
  can evaluate).

The single skip is `payment_methods`: `authenticated` holds no grant on it, so its policy
cannot be exercised from that role. Recorded as a skip with a reason, **not** as a pass.

#### Two defects found in the course of fixing it

**The first attempt failed and rolled back cleanly.** Restoring
`USING ((auth.uid())::text = user_id)` verbatim against a now-`uuid` column raises
`operator does not exist: text = uuid`. Alembic's transactional DDL reverted the whole
revision — 188 policies intact, columns still `text`, revision unmoved. Failing loudly and
leaving the database recoverable is the required behaviour and it held. The fix casts the
column in the predicate, producing the same double-cast shape `invoices` already used, and
only when the server itself rejects the verbatim form.

**The migration ran too early on the provisioning path.** `provision_database.py` runs
alembic at STEP 0c, *before* the numbered SQL set, and 9 of the 12 target tables do not
exist yet. The revision's `_table_exists` guard correctly skipped them and a freshly
provisioned database came out with 9 columns still `text`. Caught by
`scripts/verify_production_schema.py` reporting `drift/columns 9 missing, 9 unexpected`
and `drift/foreign_keys 0 missing, 9 unexpected`.

Fixed by `migrations/hardening/998_bug049_identity_types.sql`, applied as an explicit
**STEP 3b** after the SQL set and before the privilege hardening. Not by filename
ordering — within `migrations/` the files sort numbered-then-alphabetical and a new
`zz_*.sql` would displace it, the same fragility rejected earlier for the hardening.

Two implementations of one decision is a liability. It is bounded by
`tests/test_bug049_sql_and_alembic_agree.py` (15 tests), which parses both files and fails
if they disagree about the target list, the ON DELETE per table, the foreign-key naming
convention, the UUID pattern, or whether each section covers the same tables.

### 2. Rollback safety proof — STRENGTHENED

The gate required that row-count equality must not be described as the database being
"byte-for-byte unchanged". It was, and that was an overclaim. Two properties are now
measured and **reported separately**:

| property | status | basis |
| --- | --- | --- |
| **DATABASE ROWS UNCHANGED** | **asserted** | row counts for all 85 `public` tables plus `auth.users`; the exact primary keys of `profiles` and `auth.users` compared as sets, not counts; every `text`/`varchar` column in `public` swept for the probe's synthetic markers (`probe-`, `example.invalid`, `sweep-reader`); the 10 audit and trigger-backed tables checked by name |
| **DATABASE STATE FULLY RESTORED** | **reported, and currently NO** | sequences compared before and after. `public.reconciliation_mismatches_id_seq` advanced `0 -> 1` and did not roll back |

The sequence advance is **named as an irreversible side effect** rather than hidden inside
a pass. `nextval()` is non-transactional in PostgreSQL by design, so that two concurrent
transactions cannot be handed the same value; no customer data is affected, only the next
surrogate key that will be issued. The documents may therefore claim "rows unchanged" and
may **not** claim "state fully restored".

**The rollback is also structurally enforced, not just observed once.** Measuring one run
proves that run committed nothing; it does not prove the next cannot. So the source is
parsed as an AST and the tests fail on:

- any reachable `commit()` on a connection or transaction — `savepoint.commit()` is the
  one allowed form, because releasing a SAVEPOINT keeps the work inside the same
  uncommitted transaction;
- `autocommit = True` anywhere, as an assignment or a keyword, or an `isolation_level`
  argument;
- a `main()` whose `conn.rollback()` is not inside a `finally`;
- a connection opened before the acknowledgement flag is checked;
- a missing `assert not conn.autocommit` — because the two static checks above cannot
  prove the connection did not *arrive* with autocommit on.

**20 tests, all passing.** A BOM was found and removed while building them: PowerShell's
`Set-Content -Encoding utf8` had added one to both production scripts, and
`ast.parse(... encoding="utf-8")` raises `invalid non-printable character U+FEFF` on it.
CPython's import machinery tolerates a BOM, so the scripts ran and nothing looked wrong —
while every AST-based safety check was unable to parse its own target.
`staging/strip_bom.py` finds and removes them; a dedicated test now asserts their absence
first, so the failure says "there is a BOM" rather than "the commit check could not run".

### 3. Absolute policy checks — EXTENDED to six defect classes

Differential comparison alone is insufficient: a production schema can exactly match a
defective repository baseline. `scripts/verify_production_schema.py` now applies six
absolute checks that do not depend on the baseline being correct, and each detects the
**class** rather than the two tables where the class was first found.

| check | class | local result |
| --- | --- | --- |
| 3a | permissive policy with a constant-`TRUE` predicate over a per-customer table — the `library_ratings` defect | **0** |
| 3b | overlapping permissive policies that OR into broader access — the `referral_codes` defect | 13, every predicate read; 1 accepted by design with a written reason |
| 3c | per-customer table with RLS **off** while `anon` holds `SELECT` — the 22 billing exposures | **0** |
| 3d | policy depending on a session GUC nothing sets | 9 — **AMBER**, fail-closed |
| 3e | policy on a per-customer table naming **no** ownership column | 1, accepted by design |
| 3f | policy naming `anon` on a per-customer table | **0** |

3d is AMBER and not RED because `current_setting('app.current_tenant_id', true)` returns
NULL when unset, so the comparison is NULL and the policy matches zero rows — fail-closed.
It is still reported, because those nine policies provide no isolation while appearing to,
and a change that starts setting the GUC on one code path would alter access silently.

Two false positives were found and fixed while building 3e, rather than accepted:
`profiles` is owned by its own `id`, which is deliberately excluded from the global
ownership-column list (a bare `id` would make every table match), so the three correct
`auth.uid() = id` policies on the most sensitive table in the schema were being reported
as defects. `ALWAYS_PER_CUSTOMER_TABLES` now covers it.

---

## Local verification status — for comparison only

Every line below is **LOCAL VERIFIED**: measured against PostgreSQL 16.2 provisioned from
this repository. **No line in this document is PRODUCTION VERIFIED.**

| | result |
| --- | --- |
| Provisioning reproducible (twice, 16 dimensions) | `RESULT: REPRODUCIBLE` — 85 tables, 1135 columns, **121 foreign keys**, 122 unique/check, 400 indexes, 188 policies, 991 grants, 50 recorded migrations |
| Drift against the baseline | `RESULT: NO DRIFT` |
| Identity type matrix | 81 columns: **55 correct, 0 wrong type**, 26 uuid without an FK (informational) |
| Behavioural cross-tenant isolation | **64 ISOLATED, 0 LEAK**, 5 NO CONTROL, 1 NOT SEEDED |
| `execution_records` primary key | `execution_records_pkey PRIMARY KEY (execution_id)` — present |
| `idempotency_keys` uniqueness | `idempotency_keys_pkey (idempotency_key, tenant_id)` — present |
| Live trading | **OFF** — 5 of 5 cases refused, including both env switches set |

`NO CONTROL` and `NOT SEEDED` are **not** counted as isolation, by the script or by its
tests. `NO CONTROL` means every attempt was blocked including the owner's own, so the
policy was never shown to *discriminate* — and `USING (false)` would produce output
identical to perfect isolation.

---

## The `execution_records` question, answered locally and NOT in production

The gate singles this out, and correctly: the repository's own provisioning documentation
records that several execution tables are declared as *pre-existing* rather than created by
the normal migration path, so whether production has the key cannot be inferred from the
migration set.

**Locally it is present:** `execution_records_pkey PRIMARY KEY (execution_id)`, read from
`pg_constraint` on a database provisioned from this repository.

**In production it is UNKNOWN.** That is the single highest-value query in the whole
remaining list, because it is the database-level guarantee behind order idempotency — if it
is absent, every protection in that layer rests on application logic alone, and two workers
racing past the application check would both insert. `verify_production_schema.py` section
5 checks it, names it, and classifies its absence as launch-critical.

---

## What was NOT done, deliberately

| | |
| --- | --- |
| Production schema modified | **No.** No DDL was applied to any production database. |
| `03-deploy.yml` run | **No.** |
| `alembic upgrade head` against production | **No.** |
| Arbitrary migration SQL executed against production | **No.** |
| Live trading enabled | **No.** Still OFF, verified by executing the guard under five environments including both switches set. |
| `NOT CLASSIFIED` converted to `AMBER` | **No.** |
| A local pass promoted to a production pass | **No.** |

No `staging/PRODUCTION_REMEDIATION_PLAN.md` was produced, because that document is for a
**RED** production result. There is no production result. Producing a remediation plan for
drift nobody has observed would be fiction.

---

## Operator summary

```
PRODUCTION ACCESS:      NOT AVAILABLE  (no DSN in workspace; egress works)
SCHEMA:                 NOT CLASSIFIED (production never read)
SECURITY:               NOT CLASSIFIED (production never read)
BUG-049:                FIXED          (12 columns uuid + FK; 76 regression tests)
EXECUTION_RECORDS PK:   UNKNOWN        (present locally; unverified in production)
BILLING ISOLATION:      UNKNOWN        (PASS locally: 27 tests + behavioural ISOLATED)
TENANT ISOLATION:       UNKNOWN        (PASS locally: 64 ISOLATED, 0 LEAK)
ROLLBACK PROOF:         PASS           (rows unchanged asserted; full restoration
                                        reported NO -- one sequence advanced)
LIVE TRADING:           OFF / NOT CERTIFIED

FINAL PAPER STATUS:     PAPER-ONLY NO-GO
```

`PAPER-ONLY GO` is unavailable. Of the nine GO conditions, the first — "production schema
matches the verified remediation schema" — cannot be assessed at all, and five others
(billing, referral, library, credential and tenant isolation in production) inherit the
same gap. Local evidence is strong and it is not a substitute.

**Live trading remains OFF and NOT CERTIFIED.** Nothing in this document certifies it, and
a paper-only GO would not have either. Live certification requires a separate
testnet/live-execution campaign.
