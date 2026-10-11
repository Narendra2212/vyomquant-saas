# Database Provisioning Runbook

How to build this product's PostgreSQL schema from an empty database, in one command,
reproducibly. Written because before this work there was no way to do it: the migration
set could not be applied in the order its own documentation prescribed, and nobody had
tried from empty.

**Verified state.** An empty PostgreSQL 16.2 database, provisioned twice by one command,
produced byte-identical schemas across 16 dimensions — 85 tables, 1135 columns, 85
primary keys, 109 foreign keys, 122 unique and check constraints, 400 indexes, 188
policies, 991 privilege grants, 36 functions, 61 triggers, 1 sequence, 6 enum types, 49
recorded migrations. Reproduce with `python scripts/verify_provisioning.py`, which prints
those counts and `RESULT: REPRODUCIBLE` and exits 0. The committed snapshot it writes,
`staging/provisioning_snapshot.json`, is the artefact — run logs under `staging/*.log` are
excluded by `.gitignore` deliberately, so nothing here cites one as evidence.

---

## 1. One command

```powershell
# Dry run is the DEFAULT. Nothing executes without --apply.
python scripts\provision_database.py --dsn postgresql://user:pw@127.0.0.1:5432/dbname

# Apply. --create-substrate is for a plain PostgreSQL target that is not Supabase:
# it creates the auth schema, the three roles, and auth.uid() / auth.jwt().
python scripts\provision_database.py --dsn ... --apply --create-substrate
```

Against a real Supabase project, omit `--create-substrate` — the substrate is already
there — and add `--i-understand-this-is-remote`.

### Safety model, as built

| Guard | Behaviour |
| --- | --- |
| `--dry-run` | the default; `--apply` is required to execute anything |
| non-localhost DSN | **refused** unless `--i-understand-this-is-remote` is passed |
| port 6543 | refused — Supabase's transaction pooler cannot run `DO $$ … $$` blocks or explicit `BEGIN`/`COMMIT` |
| DSN in logs | never printed |
| transactions | one file per transaction, with each file's own `BEGIN`/`COMMIT` left exactly as written |
| re-runs | every applied file is recorded in `public.schema_migrations` with a SHA-256 of its contents, so a re-run skips it |
| edited files | **refuses** if a file's checksum differs from the one recorded when it was applied |
| failure | aborts on the first one, naming the file and the driver's error |

The checksum refusal is the guard that matters most in practice. It is the difference
between "this migration already ran" and "a migration with this *name* already ran".

---

## 2. The order, and why it is not the documented order

`PROVISIONING_ORDER.md` prescribes:

```
Step 0   020_declare_pre_existing_tables.sql          (first, despite its number)
Step 1   backend_app/migrations/001..019, then migrations/*
Step 2   alembic upgrade d97ffff9c3bb                 (the branchpoint, NOT head)
Step 3   020 again, so strategies_source_library_id_fkey can land
```

What `scripts/provision_database.py` actually does:

```
STEP 0   020_declare_pre_existing_tables.sql
STEP 0c  alembic upgrade head                         (BEFORE the SQL set, and head)
STEP 0b  archived_migrations/root_migrations/*.sql     (less the two alembic owns)
STEP 1   backend_app/migrations/001..019, then migrations/*
STEP 3   020 again
STEP 4   migrations/hardening/999_privilege_hardening.sql   (always, last)
```

Four deliberate differences. Each was established by running it both ways, not by
reading.

### 2.1 Alembic moves ahead of the SQL set

The documented step-2 placement is circular.
`migrations/006_reconcile_production_database.sql` issues `ALTER TABLE execution_records`,
and `execution_records` is declared only by an alembic revision. So the SQL set cannot
run before alembic. Proven in both directions: with alembic second, step 1 aborts on the
missing table.

### 2.2 Alembic goes to `head`, not the branchpoint

Both `PROVISIONING_ORDER.md` obstacle 1 and the CI `database-tests` job state that head
is unreachable. It was. The stated cause — `add_foreign_keys` needing a `profiles` table
no migration declares — does **not** apply here, because STEP 0 declares `profiles`
first. The actual cause was a `DatatypeMismatch` inside `add_foreign_keys` that aborted
the transaction and took alembic's own version bump down with it.

This matters beyond tidiness: reaching head is what installs the nine tenant-isolation
RLS policies on the execution and financial tables. **No database built by migration had
them before.**

### 2.3 `archived_migrations/root_migrations/` is not actually archived

It holds the only declaration of `library_strategies`, `library_ratings`,
`library_subscriptions`, `deployment_permissions` and the creator/marketplace analytics
tables — and the numbered set requires them. The directory name is wrong; the files are
load-bearing.

Two files in it are excluded because alembic owns those tables; including them created
`TYPE task_status` early and then aborted alembic. Measured: including them took
provisioning to step 2 and stopped there.

### 2.4 Hardening is STEP 4, and it is the last thing that runs

This is the single most important ordering decision in the file, and it was wrong twice
before it was right.

**Attempt 1 — put the privilege hardening in `006_reconcile_production_database.sql`.**
Failed, measurably. `create_plan_tables.sql` runs *after* 006 in step 1, so 006's
`IF EXISTS` guards skipped the plan tables (they did not exist yet), and the tables
created afterwards inherited `GRANT ALL`. Result: `anon` still held
DELETE/INSERT/TRUNCATE/UPDATE on all five plan catalogue tables, including
`plan_prices.price_cents` — what customers are charged.

**Attempt 2 — rename the file so it sorts last (`zz_*.sql`).** Rejected without
shipping. Making a security control depend on filename collation is fragile in exactly
the way that produces this class of defect a second time.

**What it does now.** A dedicated file, `migrations/hardening/999_privilege_hardening.sql`,
applied as its own step after everything else, and **re-applied on every run**
(`_apply_sql(..., already=set())`) because it is a declarative end state rather than a
one-time migration. Its five sections:

| Section | What it asserts |
| --- | --- |
| 0 | blanket `REVOKE TRUNCATE, REFERENCES, TRIGGER` from `anon` and `authenticated`, plus `ALTER DEFAULT PRIVILEGES` so later tables do not re-acquire them |
| 1 | per-customer financial and commercial tables: RLS enabled, browser roles revoked |
| 2 | plan catalogue is `SELECT`-only for browser roles — and says plainly that RLS is *not* used there, because a `USING (true)` policy would filter nothing |
| 3 | `alembic_version` and `schema_migrations` are revoked from browser roles entirely |
| 3b | `library_ratings`: replaces a `USING (true)` SELECT policy with an ownership predicate |
| 4 | **three `RAISE EXCEPTION` backstops** |

The backstops abort provisioning rather than warn:

1. any table with a per-customer column, RLS **disabled**, and `SELECT` still granted to
   `anon`;
2. any browser role still holding `TRUNCATE`, `REFERENCES` or `TRIGGER`;
3. any table with a per-customer column whose permissive policy predicate is the
   constant `true` — RLS enabled and filtering nothing, which every
   `relrowsecurity`-based check reports as secured.

Deliberately exceptions and not notices. A warning in a provisioning log is a warning
nobody reads, and this is the class of defect that produced 22 live billing exposures.

---

## 3. Verifying a provision

### 3.1 Is it reproducible?

```powershell
python scripts\verify_provisioning.py
```

Creates a scratch database `aerora_provision_check_<pid>`, provisions it **twice**, diffs
the two schemas across 16 dimensions, writes `staging/provisioning_snapshot.json`, and
drops the scratch database. Exit 0 and `RESULT: REPRODUCIBLE` is the pass.

Provisioning twice, not once, is the point. See section 4.

### 3.2 Has a live database drifted from what this repository produces?

```powershell
python scripts\verify_schema_drift.py --dsn postgresql://...
```

Compares a live schema against `staging/provisioning_snapshot.json` and prints every
differing row with which side it is on. Read-only; reads shape only, never row data, so
it is safe to point at production.

This closes the gap `verify_provisioning.py` cannot: Supabase lets a schema be edited
through its dashboard, and such an edit leaves **no migration in this tree**, so the
repository and the database can disagree while every migration-based check stays green.

Schema differences fail. `recorded_migrations` and `alembic_version` differences are
reported as ADVISORY, because a database ahead or behind on bookkeeping is a
deployment-state question and conflating the two makes the signal useless.

The tool was verified by catching a real difference, not by passing: run against a
database provisioned before the `referral_wallets` column-retention change, it reported
the three missing columns and the two changed file checksums, and exited 1.

### 3.3 Does the schema match the ORM and the application?

```powershell
python staging\verify_schema.py        # 23 structural checks
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging\pg_dsn.txt)
python -m pytest tests\test_orm_models_match_provisioned_schema.py -q
```

### 3.4 Is anything exposed?

All five must exit 0:

```powershell
python staging\exposure_check.py            # per-customer tables with RLS OFF
python staging\permissive_policy_probe.py   # RLS ON, policy predicate TRUE
python staging\truncate_probe.py            # the privileges RLS cannot filter
python staging\billing_exposure_probe.py    # the 22 original billing exposures
python staging\anon_read_sweep.py anon            # behavioural: 49 tables
python staging\anon_read_sweep.py authenticated   # behavioural: 79 tables
```

The last two are the strongest: they seed a real row owned by one customer and try to
read it as another, for every table the role can `SELECT`. Both must report
`0 leaked` **and** `0 not proven`. See `RLS_TENANT_ISOLATION_VERIFICATION.md`.

---

## 4. The two idempotency defects, and why they were not symmetrical

`scripts/verify_provisioning.py` provisions **twice** because re-running was not
idempotent, and it failed in the direction that matters.

### 4.1 Run 2 re-granted 787 privilege rows

`SUBSTRATE_DDL` contained `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon,
authenticated`. On run 1 that executed against an empty schema and granted nothing. On
run 2 it executed against 85 tables and granted everything.

Worse, the migrations' own `REVOKE` statements were *skipped* on run 2 — their checksums
already matched, so the provisioner correctly declined to re-run them — while the
substrate's `GRANT ALL` ran again because it is substrate setup rather than a recorded
file. So run 2 ended **less secure than run 1**, and nothing reported it.

**Fixed** by removing `GRANT ALL ON ALL TABLES` from `SUBSTRATE_DDL` entirely.
`ALTER DEFAULT PRIVILEGES` in hardening section 0 already covers the fresh case, which
was the only case the blanket grant existed to serve.

**Rejected alternative:** keep the grant and always re-apply hardening. Measured — it
still left 185 re-granted rows, because hardening's named revokes do not cover every
table the blanket grant touches.

### 4.2 A migration that had never once run

`migrations/referral_system_redesign.sql`'s wallet backfill contained an unqualified
`SELECT id` inside a join, which PostgreSQL rejects:

```
AmbiguousColumn: column reference "id" is ambiguous
```

Not a regression. That statement had **never executed successfully**, in any environment,
because nobody had ever applied the migration set from empty. Fixed by qualifying it as
`p.id`.

This is the general lesson of the exercise: a migration set nobody has applied from
scratch is not a migration set, it is a collection of files that resemble one.

---

## 5. Local staging

### 5a. Using the cluster that is already provisioned (the common case)

This is what the PostgreSQL-gated suites expect. The data directory is named
**explicitly** — `bootstrap_pg`'s default is `pgdata`, which on a machine that has been
through a rehearsal is a *different, empty* cluster, and pointing the shared DSN at it
makes every PG-gated suite skip or fail for reasons unrelated to the code. Since Phase 10
/ Workstream H the bare `start` form fails closed instead of guessing, so it is no longer
a trap — but it is also no longer a usable command, which is why it does not appear below.

```powershell
# start (or re-attach to) the provisioned cluster and record its DSN
.venv\Scripts\python.exe staging\bootstrap_pg.py start --data-dir pgdata_r1 --expect-provisioned

# already running, but on a new port? pgserver randomises it on every start.
# This finds it and refreshes staging/pg_dsn.txt, without starting anything:
.venv\Scripts\python.exe staging\probe_pg_find.py

# just checking which database the DSN names right now (changes nothing):
.venv\Scripts\python.exe staging\bootstrap_pg.py verify

# the PostgreSQL-only test suite
.venv\Scripts\python.exe staging\run_pg_suite.py
```

`--expect-provisioned` makes a cluster without the expected schema a **loud failure here**
rather than a run in which every PG-gated suite silently skips.

### 5b. Provisioning from nothing (the rehearsal)

A deliberately separate, clean data directory, so the exercise cannot disturb the
provisioned one. `PG_ALLOW_DSN_DOWNGRADE=1` is **required** on any machine that already
has a provisioned cluster: `staging/pg_dsn.txt` is shared, and the Workstream H safety
contract refuses to repoint it from a provisioned target to an empty one unless the
downgrade is stated. Without it, step 1 exits 3 and tells you exactly this.

```powershell
# 1. download and start PostgreSQL 16.2 (no installer, no admin rights)
#    NOTE: no --expect-provisioned here; the whole point is that it is empty.
$env:PG_ALLOW_DSN_DOWNGRADE = "1"
.venv\Scripts\python.exe staging\bootstrap_pg.py start --data-dir pgdata_z1
#    -> writes the DSN to staging/pg_dsn.txt

# 2. provision
.venv\Scripts\python.exe staging\reprovision.py

# 3. Redis, for the tests that need a real one
.\staging\redis\redis-server.exe --port 6379 --appendonly no

# 4. the PostgreSQL-only test suite
.venv\Scripts\python.exe staging\run_pg_suite.py

# 5. when finished, point the shared DSN back at the provisioned cluster
Remove-Item Env:\PG_ALLOW_DSN_DOWNGRADE
.venv\Scripts\python.exe staging\probe_pg_find.py
```

Step 5 matters: the rehearsal leaves the shared DSN naming the rehearsal cluster, and
every other PostgreSQL-gated suite on the machine reads that file.

### Operational notes, learned the hard way

**A PostgreSQL started from a shell dies when a later command in that shell times out.**
The timeout kills the process tree. This happened twice during this work and produced
cascades of failures that looked like test regressions and were not. Keep foreground
commands short; run anything long in a background process.

**An uncleanly killed data directory may refuse to restart** — `pg_ctl` gives up after
10 seconds while recovery is still running. Provision a fresh one rather than fighting
it; `staging/reprovision.py` takes under two minutes.

**`staging/run_pg_suite.py` runs one pytest process per file, deliberately.** A single
process over all files died with `0xC0000409 STATUS_STACK_BUFFER_OVERRUN` and wrote an
*empty* log, losing every result including the ones that had already passed.

**Long PowerShell one-liners in this workspace echo their own command text back
character by character and then time out** — which kills PostgreSQL, per the first note.
Use `staging/grep_log.py` or a small `.py` helper instead of piping through many
cmdlets.

**`staging/q.py` does not commit DDL.** It is a read helper. Use a script that sets
`ISOLATION_LEVEL_AUTOCOMMIT` — `staging/apply_hardening.py` is the pattern.

**Reading a pytest log:** `staging/grep_log.py <log> <marker> [lines] [filter]` prints a
window after a marker, and `staging/show_failure.py <log>` jumps straight to the
assertion block. The docstrings in this codebase are long enough that a fixed `-Tail`
window usually shows prose rather than the error.

**`grep_log.py` reads UTF-8.** PowerShell's `>` redirection writes UTF-16LE, so a log
captured that way reads as garbage. Use `| Out-File -Encoding utf8` or `Tee-Object`.

**Do not edit documents through PowerShell.** `Get-Content -Raw | ... | Set-Content
-Encoding utf8` round-trips UTF-8 through cp1252 and mangles every em dash and curly
quote. It corrupted 62 characters in one launch document during this work.
`staging/fix_mojibake.py` repairs the damage — it derives the mangled form of each
character by computing the corruption rather than hardcoding codepoints, after a first
attempt at a hardcoded table got them wrong. Use a `.py` helper or the editor instead.

**Reading an isolation result:** `staging/show_probe.py <matrix.json> <table>...` prints
one table's full probe outcome, which is how a `NO CONTROL` verdict gets diagnosed.
`staging/diagnose_owner_select.py <table>` reproduces the positive control step by step,
printing `auth.uid()` and the session role at each stage — that is what identified the
probe's own calibration bugs rather than mistaking them for schema defects.

**`= ANY(:ids)` now fails** with `operator does not exist: uuid = text`, because
`tenant_id` is `uuid` since alembic `a1b2c3d4e5f6`. Use `ANY(CAST(:ids AS uuid[]))`.

---

## 6. What is NOT done

Stated plainly because these are the difference between "provisioning works" and
"provisioning is wired into how this product is deployed".

| Gap | Status |
| --- | --- |
| Provisioning, double-provisioning, drift checking and all ten probes now run on every pull request — `.github/workflows/01-pr-check.yml`, job `provisioning-and-isolation`. | **WIRED — not yet executed** |
| The **deploy** workflow does not provision the real database. `03-deploy.yml` invokes nothing. | **DECISION NEEDED** |
| No verification has been run against **production**. Everything here is a staging database built from this repository. Production may differ, and a dashboard-applied change leaves no trace in these files. | **NOT VERIFIED** |
| Rollback has not been rehearsed. `downgrade()` exists on the new revisions and two of them deliberately **refuse** rather than destroy data, but no restore-from-backup drill has been performed. | **NOT DONE** |

### On the CI job: wired, not yet executed

The sequence it runs is verified locally and repeatedly, against a **vanilla PostgreSQL
with no Supabase substrate** — the same starting condition as the container, because
`--create-substrate` is the only path `staging/reprovision.py` uses. What is *not*
verified is the PostgreSQL major version: local runs are 16.2 from the `pgserver` wheel,
the container is 17 to match production's reported `server_version 17.6`.

It is committed in that state on purpose, and the uncertainty is written into the job's
own comment. The alternative was leaving a hand-verified provisioner that nothing ever
runs, which is documentation rather than a control.

The job also depends on `staging/provisioning_snapshot.json` being committed — it is
(`.gitignore` excludes only `pgdata*/`, `redis/`, `*.log` and `pg_dsn.txt`). Every probe
resolves its database through `staging/dsn.py`, which prefers
`AERORA_TEST_DATABASE_URL` and falls back to the local DSN file, so the set runs with no
local artefacts present. Verified by moving `pg_dsn.txt` aside and re-running: all ten
still exited 0.

### On the deploy half: why it is a decision and not a task

Wiring `provision_database.py` into `03-deploy.yml` means running DDL against the
production Supabase database from CI. That is hard to reverse and affects live customer
data, so it should be an explicit choice with a backup taken first — not something added
because it completes a checklist.

The pieces are ready. The provisioner refuses any non-localhost DSN unless
`--i-understand-this-is-remote` is passed, records a SHA-256 per file, **refuses outright
if a file changed since it was applied**, and aborts on the first failure naming the file.
The three `RAISE EXCEPTION` backstops in hardening would become deploy gates.

---

## 7. Checking a database you did not provision

Sections 1–6 are about building a schema from this repository. This section is about the
opposite direction: a database that already exists — production — and the question of
whether it matches.

### 7.1 Read-only first, always

```powershell
# The whole comparison plus seven absolute security checks. READ-ONLY.
python scripts\verify_production_schema.py --dsn "<DSN>" --out staging\production_schema.json
```

Exit 0 / 1 / 2 for GREEN / AMBER / RED.

Safety properties, all server-enforced rather than conventional:

| Property | How |
| --- | --- |
| cannot write | `default_transaction_read_only` is set **and read back**; aborts if the server does not confirm. A write would fail with SQLSTATE 25006. |
| cannot be fooled by a pooler | port 6543 is refused — session settings do not survive transaction pooling, which would make the guarantee above unverifiable |
| reads no customer data | catalogues and `information_schema` only, plus `COUNT(*)` so an empty table is not mistaken for a secure one |
| leaks no credential | the DSN is parsed and reduced to scheme/host/port/database before anything is printed |

Proven, not asserted: `tests/test_schema_drift_checker_is_read_only_postgres.py` attempts
a write through the same session and requires SQLSTATE 25006, attempts DDL likewise, and
fingerprints row counts and catalogue counts before and after a full run. 12 tests.

### 7.2 Then, as a separate deliberate act, the behavioural half

```powershell
python scripts\verify_production_security.py --dsn "<DSN>" `
       --i-understand-this-writes-and-rolls-back `
       --out staging\production_isolation.json
```

This one **writes** — it has to, because "can customer B read customer A's invoice?"
cannot be answered by reading a catalogue. It commits nothing: one transaction,
`ROLLBACK` in a `finally`, unconditionally. It refuses without the flag, refuses if
`VYOMQUANT_ENABLE_LIVE_TRADING` or `AERORA_ENABLE_LIVE_TRADING` is set, refuses
`VYOMQUANT_MODE=live`, refuses the pooler, and prints a count of leftover probe
identities after the rollback.

Proven by `tests/test_production_security_probe_rolls_back_postgres.py`, 9 tests —
including one that fingerprints row counts across all of `public` plus `auth.users`
around a complete run, and a companion asserting the run actually measured something,
since a script that did nothing would also change nothing.

### 7.3 Validate the tooling against a known-good database first

Both scripts were pointed at the local provisioned database repeatedly before being
considered ready, and their first answers were rejected. The security probe's first run
reported 21 failures; **all 21 were the probe's own fault** (it set one of the schema's
two policy families, ran destructive attempts before the positive control, let the
foreign-key borrow override the ownership column, and populated only NOT NULL columns so
blob-shaped tables got a NULL owner).

This is the same discipline as provisioning twice: a verifier nobody has challenged is
not a verifier. Run them locally, read the output, and disbelieve it once before pointing
them anywhere that matters.

### 7.4 A drift check cannot tell you the baseline is correct

Worth stating because it is the limit of the whole approach. `verify_schema_drift.py`
answers *are they the same*. It does not answer *are they correct*.

BUG-049 is the demonstration: `user_id` is declared with three different types across this
schema, so 12 columns can carry no foreign key to the customer they belong to. That defect
is in **this repository**, so a drift check against production would report no drift and
both sides would be wrong.

That is why `verify_production_schema.py` runs absolute checks — billing exposure,
constant-TRUE policies, TRUNCATE-class grants, the `execution_records` primary key,
`idempotency_keys` uniqueness, identity types — alongside the differential one. Comparison
alone is not verification.

### 7.5 Status

**No production database has been read.** `.env` in this workspace holds placeholders: a
`sqlite` `DATABASE_URL`, a localhost `SUPABASE_URL`, and `SUPABASE_KEY="dummy_key"`.
Network egress was tested and works — DNS resolves and an outbound TCP connection to 443
succeeds — so the only missing input is a credential.

Everything in sections 1–6 of this runbook is **LOCAL VERIFIED**. Nothing is
**PRODUCTION VERIFIED**.

---

## 8. Addendum — STEP 3b, and why alembic alone cannot provision this schema

### 8.1 The order now has six steps

```
STEP 0   020_declare_pre_existing_tables.sql
STEP 0c  alembic upgrade head                          (BEFORE the SQL set, and head)
STEP 0b  archived_migrations/root_migrations/*.sql
STEP 1   backend_app/migrations/001..019, then migrations/*
STEP 3   020 again
STEP 3b  migrations/hardening/998_bug049_identity_types.sql    <-- NEW
STEP 4   migrations/hardening/999_privilege_hardening.sql
```

### 8.2 Why STEP 3b has to exist

Alembic `d4e5f6a7b8c9` performs the BUG-049 identity conversion and is the correct
mechanism for a database that already exists — it is what will run against production.
It cannot do the job here, and that was measured rather than argued:

alembic runs at **STEP 0c**, before the numbered SQL set, because
`006_reconcile_production_database.sql` ALTERs `execution_records` and only alembic
declares it. At STEP 0c, **9 of the 12 target tables do not exist yet** —
`billing_invoices`, `exchange_connections`, `exchange_keys`, `notification_settings`,
`referral_profiles`, `risk_settings`, `risk_settings_audit`, `security_logs`,
`strategy_limits`. The revision's `_table_exists` guard correctly skipped them, and a
freshly provisioned database came out with 9 columns still `text` and 9 foreign keys
missing while the migrated database had them.

Caught by `scripts/verify_production_schema.py`:

```
drift/columns      9 missing, 9 unexpected (baseline 1135, production 1135)
drift/foreign_keys 0 missing, 9 unexpected (baseline 112, production 121)
```

Note which check found it: the **differential** one. The absolute identity-type check
passed, because it was run against the migrated database. That pairing — compare *and*
check absolutely — is why `verify_production_schema.py` does both.

**This is a general property of the ordering, not a quirk of BUG-049.** Any alembic
revision that touches a table created by the SQL set has the same problem and will be
skipped silently on this path. BUG-049 is the first to expose it. The structural fix is a
second alembic pass after the SQL set, or moving the `execution_records` declaration into
SQL so alembic can run last; both are architecture changes and are recorded in
`POST_LAUNCH_IMPROVEMENTS.md` item 25.

### 8.3 Why it is a named step and not a numbered file

Within `migrations/` the files sort numbered-then-alphabetical, and
`referral_system_redesign.sql` currently sorts last. A new `zz_*.sql` would displace
anything relying on that order. This is exactly the fragility rejected for the privilege
hardening, and the same reasoning applies.

STEP 3b runs **after** the SQL set so the tables exist, and **before** STEP 4 so the
privilege hardening's policy work sees the final column types.

### 8.4 What the file does, and what it refuses to do

Four sections, each idempotent:

1. **Precheck.** Every target column is inventoried for values that are not UUID-shaped
   *before the first `ALTER`*. A separate pass, deliberately: if the eleventh table holds
   an unconvertible value, that must be found before the first ten have been rewritten —
   a half-applied conversion on a money table is the worst outcome available. It RAISES
   with the table, column, count and a sample, having altered nothing.
2. **Convert**, capturing and restoring each table's RLS policies, casting the column
   reference in any predicate that no longer type-checks. Asserts the restored count
   equals the captured count: a retyped column with its policy missing is a table whose
   row filtering silently disappeared.
3. **Foreign keys**, with the `ON DELETE` chosen per table. RAISES on orphans rather than
   deleting them — orphans are the defect this prevents, and removing them automatically
   would destroy customer records.
4. **Backstop.** Sweeps the **whole schema** and aborts if any `user_id`/`tenant_id` is
   not `uuid`. The named lists cannot cover a table added later; this can.

### 8.5 Verifying it

```powershell
python scripts\bug049_enumerate.py          # classify before changing: writes the matrix
python staging\show_bug049.py               # read the matrix in review form
python scripts\verify_provisioning.py       # RESULT: REPRODUCIBLE, 121 foreign keys
python scripts\verify_schema_drift.py       # RESULT: NO DRIFT
python staging\identity_type_matrix.py      # 0 of the wrong type

$env:AERORA_TEST_DATABASE_URL = (Get-Content staging\pg_dsn.txt)
python -m pytest tests\test_bug049_identity_types_postgres.py -q      # 76
python -m pytest tests\test_bug049_sql_and_alembic_agree.py -q        # 15, no DB needed
```

The second of those is the one that keeps the duplication honest: it parses both the
alembic revision and the SQL file and fails if they disagree about the target list, the
`ON DELETE` per table, the foreign-key naming convention, the UUID pattern, or whether
each of the SQL file's three sections covers the same tables. A table present in the
conversion list but missing from the precheck would be converted without its values ever
being inventoried.

### 8.6 One more reason not to edit source through PowerShell

Already noted above for mojibake; this pass added a second, worse case.
`Set-Content -Encoding utf8` writes UTF-8 **with a BOM**, and both production verifiers
acquired one. CPython's import machinery tolerates a BOM, so the scripts ran and produced
correct output — while every AST-based safety check against them raised
`SyntaxError: invalid non-printable character U+FEFF` and could not examine its target.

`staging/strip_bom.py` scans 681 source files and removes any BOM. A dedicated test now
asserts their absence **before** the checks that depend on parsing, so the failure says
"there is a BOM" rather than "the commit check could not run".
