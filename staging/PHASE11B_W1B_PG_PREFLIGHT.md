# Phase 11B / Workstream 1B — Read-only PostgreSQL preflight

**Scope.** Deliverable 2 of 5. Everything here was obtained with **read-only**
operations against the local staging cluster. No provisioning was applied, no migration
was run, no row was written, and `staging/pg_dsn.txt` was not modified.

**Verdict up front: the preflight FAILS.** The target's *identity* checks all pass — it
is the intended cluster, at the intended Alembic head, serving the intended data
directory on loopback. The *schema-drift* check does not pass: `RESULT: DRIFTED` against
the only baseline the repository carries. §5 sets out why that is not dismissible and
what has to happen before a shared-cluster lane can be authorized.

---

## 1. What was run, and why it is safe

| # | Command | Exit | Mutation capability |
| --- | --- | --- | --- |
| 1 | `python staging/bootstrap_pg.py verify` | 0 | None. `verify()` reads the DSN file and issues three SELECTs (`count(*) from information_schema.tables`, `current_setting('data_directory')`, `to_regclass`). It cannot start, stop, or write. |
| 2 | `python scripts/verify_schema_drift.py` | **1 (DRIFTED)** | None. Sets `default_transaction_read_only` and reads it back, refusing to proceed unless the server reports `on`. Refuses port 6543. No write flag exists on its CLI. |
| 3 | `python staging/probe_p11b_w1b_pg_preflight.py` | 0 | None. Same enforced read-only session; SELECTs only. |

### 1.1 Explicitly NOT run

| Tool | Why |
| --- | --- |
| `scripts/provision_database.py --apply` | Would mutate. Unnecessary regardless: the target is already provisioned and at head (§2). |
| `scripts/verify_provisioning.py` | Issues `DROP DATABASE` / `CREATE DATABASE` behind a substring-only locality guard. |
| `staging/run_all_probes.py` | Runs 12 probes including `staging/truncate_probe.py` (real `TRUNCATE` / `TRUNCATE CASCADE`) and committed `INSERT`/`DELETE` seeding. |
| `alembic upgrade` | The baseline revision `4ef23035a692` issues `DROP TABLE ... CASCADE` on `orders`, `positions`, `fills`, `transaction_records`, `transaction_checkpoints`, `transaction_rollbacks`, `idempotency_keys`. |

### 1.2 One import needed clearing first

`scripts/verify_schema_drift.py:69` does
`from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm` — an import of a
script W1B may not run. Importing a module executes its module level, so this was
checked by AST before anything was executed
(`staging/probe_p11b_w1b_import_safety.py`, log `staging/p11b_w1b_import_safety.txt`):

```
scripts/verify_provisioning.py   (16140 bytes, 22 top-level stmts)
  docstring 1 | import 9 | def 8 | literal assign 3 | __main__ guard 1 | other 0
  MODULE-LEVEL SIDE EFFECTS: 0  -- importing this module only binds definitions
  destructive SQL by enclosing function:
     main(): CREATE DATABASE, DROP DATABASE
  destructive functions invoked at module level: NONE
```

The destructive statements are confined to `main()`, reachable only through the
`__name__ == '__main__'` guard. The import is safe. `verify_schema_drift.py` itself has
two module-level statements, both benign (a `psycopg2` import guard and a
`sys.path.insert`).

### 1.3 Read-only was enforced by the server, not assumed

```
read-only enforced : default_transaction_read_only='on', transaction_read_only='on'
```

This is read back from the server after `set_session(readonly=True, autocommit=True)`,
and the probe aborts if it is not `on`. The `autocommit=True` is load-bearing: on
psycopg2, `set_session(readonly=True)` with `autocommit=False` applies to the *next*
transaction, and one is already open, so `default_transaction_read_only` stays `off`
and the "read-only" claim would be false. The readback is the evidence; the call is not.

The probe additionally refuses any non-loopback host and refuses port 6543.

---

## 2. Target identity — all checks PASS

Independently re-measured; not quoted from W1A.

| Property | Measured | Expected | |
| --- | --- | --- | --- |
| Endpoint | `127.0.0.1:51805/postgres` | loopback | PASS |
| `inet_server_addr` | `127.0.0.1` | loopback | PASS |
| `data_directory` | `C:/aerora_quant_backend_updated_final1/staging/pgdata_r1` | `pgdata_r1` | PASS |
| `server_version` | **16.2** | — | see §2.1 |
| `current_database` | `postgres` | `postgres` | PASS |
| `pg_is_in_recovery()` | `False` | False | PASS |
| `pg_postmaster_start_time()` | `2026-10-10 06:01:08.959375+00:00` | — | PASS |
| public tables | **89** | ≥ `MIN_PROVISIONED_TABLES` (50) | PASS |
| `alembic_version` | **`d4e5f6a7b8c9`** | single head `d4e5f6a7b8c9` | PASS |
| `schema_migrations` rows | **56** | — | PASS |
| `bootstrap_pg.py verify` classification | `PROVISIONED` | PROVISIONED | PASS |
| `staging/pg_dsn.txt` unchanged | sha256 `A746580D…3A96`, mtime `13:17:04` before **and** after | unchanged | PASS |

`schema_migrations` schema is `filename:text, sha256:text, applied_at:timestamptz,
step:text`. Rows by step: `step0` 1, `step0b` 10, `step1` 37, `step3b` 1, `step4` 1,
`phase8` 2, `phase9` 3, `phase10` 1 — 56 total.

All 56 recorded migrations have their `.sql` source present on disk.

> **Correction to an earlier reading of my own.** The first run of this probe reported
> "12 recorded migrations with NO source file" (`hardening/998`, `hardening/999`,
> `root_migrations/001`–`010`). That was a defect in the probe, not in the repository:
> it globbed `*.sql` non-recursively across two directories. All 12 exist, at
> `migrations/hardening/` and `archived_migrations/root_migrations/`. After fixing the
> glob to `rglob` across `migrations/`, `backend_app/migrations/` and
> `archived_migrations/`, the count is **56 of 56 present, 0 missing**. The false
> finding is recorded here rather than quietly deleted.

### 2.1 Version divergence from production (not a preflight failure, but a stated limit)

The target is **PostgreSQL 16.2** (`x86_64-pc-mingw64`, MinGW-W64, gcc 12.2.0).
Production reports **17.x**. Any result from this lane is evidence about 16.2 on
Windows. Behaviour that differs across major versions — planner choices, lock
escalation, `MERGE`/`RETURNING` details, RLS plan inlining — is **not** carried to
production by a green run here.

### 2.2 Recorded migration paths do not match where the files live

45 of 56 ledger rows record a path that is not where the file is:

```
recorded 'hardening/998_bug049_identity_types.sql'
     -> actually at 'migrations/hardening/998_bug049_identity_types.sql'
recorded 'migrations/001_strategy_architecture.sql'
     -> actually at 'backend_app/migrations/001_strategy_architecture.sql'
```

The ledger stores a SHA-256 per row, so content can still be checked — but only by
someone who already knows the real root. Recorded as **W1B-07** (P2). It is not a
blocker; it is a reason the drift tool's output reads more alarmingly than the
situation warrants.

---

## 3. Role and ownership — a hard blocker for TENANT-01

| Property | Measured |
| --- | --- |
| `current_user` / `session_user` | **`postgres`** |
| `current_setting('is_superuser')` | **`on`** |
| public tables owned by `current_user` | **89 of 89** |
| `pg_roles.rolbypassrls` for `postgres` | **`true`** |
| RLS **enabled** | **82** of 89 tables |
| RLS **FORCED** | **0** |
| policies in `public` | **195** |

Three reasons the **connection's default identity** is exempt from RLS:

1. **Owner exemption.** A table owner is not subject to its own RLS policies unless
   `FORCE ROW LEVEL SECURITY` is set. `relforcerowsecurity` is **0 across all 89
   tables**, and this role owns all 89.
2. **`BYPASSRLS`.** The role carries `rolbypassrls = true`, which exempts it from RLS
   **even on tables where FORCE is set**. W1A-03 cited owner + 0 forced; the role
   attribute makes the exemption unconditional.
3. **Superuser.** `is_superuser = on`, which bypasses essentially all permission checks.

### 3.1 Correction: the tenant suites DO switch role, and their evidence is valid

> **This section corrects an earlier conclusion of my own.** I first wrote that a passing
> tenant-isolation run against this DSN "is not evidence that RLS prevents cross-tenant
> access". That is **too strong**, and the reason is measurable rather than arguable.

The suites do not probe as the connection's default role. They adopt a browser identity
explicitly:

```python
# tests/test_tenant01_cross_tenant_matrix_postgres.py:323
def _as_tenant(cur, tenant_id: str) -> None:
    """Impersonate a PostgREST request for `tenant_id`."""
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (tenant_id,))

# tests/test_tenant02_fee_records_rls_postgres.py:246
cur.execute("SELECT set_config(%s, %s, true)", (CLAIM_GUC, claim or ""))
cur.execute(f"SET LOCAL ROLE {role}")
```

And the role attributes on this cluster are:

| Role | `rolsuper` | `rolbypassrls` | `rolcanlogin` | owns public tables |
| --- | --- | --- | --- | --- |
| `postgres` | **True** | **True** | True | **89 of 89** |
| `authenticated` | False | **False** | False | 0 |
| `anon` | False | **False** | False | 0 |
| `service_role` | False | **True** | False | 0 |

`rolbypassrls` is evaluated against the **current** role. After `SET LOCAL ROLE
authenticated`, the session is non-superuser, non-`BYPASSRLS`, and not the table owner —
so **RLS is fully evaluated**, and `FORCE` is irrelevant because FORCE only concerns the
owner. The assertions these suites make under `authenticated`/`anon` **are** genuine RLS
evidence.

The suites are also careful about leakage: `SET LOCAL ROLE` and `set_config(..., true)`
are transaction-scoped, `RESET ROLE` is issued on every exit path, and `tenant02` wraps
each probe in a `SAVEPOINT` with `ROLLBACK TO SAVEPOINT` plus an outer `db.rollback()`.

**What remains true:** any assertion made while still the default `postgres` role proves
nothing about RLS. `test_tenant01` deliberately uses that for its side-effect check
(`line 385: # THE SIDE-EFFECT CHECK, as the owner`), which is a correct use — it is
reading ground truth, not testing a policy.

### 3.2 The real gap is production's role, not the test harness's

The production connection string (`.env.example`) is:

```
DATABASE_URL = postgresql://postgres:<redacted>@db.<project-ref>.supabase.co:6543/postgres
```

Role **`postgres`**, i.e. **the table owner, with `BYPASSRLS`**, through the port-6543
transaction pooler. Combined with `relforcerowsecurity = 0` on all 89 tables, this means:

- the **PostgREST / browser path** (`anon`, `authenticated`) **is** governed by the 195
  policies, and that path is what the tenant suites exercise;
- the **application backend path is not governed by RLS at all.** Every query the FastAPI
  service issues runs as owner with `BYPASSRLS`. Tenant isolation on that path rests
  entirely on application-level `WHERE` clauses, with RLS providing no backstop.

So the 195 policies are a real control for one caller and inert for the other. A green
tenant-isolation suite is evidence about the browser path **only**, and must be cited
that way. Closing TENANT-01 for the backend path needs either a non-owner application
role or `FORCE ROW LEVEL SECURITY`, and neither exists today.

W1A-03 is **re-scoped, not simply confirmed** → **W1B-05**. W1A's statement (and my
first draft of this section) attributed the gap to the harness. The harness is sound; the
gap is in production's connection role.

---

## 4. Row counts in the tables a lane would touch

| Table | Rows |
| --- | --- |
| `profiles` | 57 |
| `exchange_keys` | 28 |
| `orders` | 28 |
| `positions` | 28 |
| `safety_control_audit` | 32 |
| `schema_migrations` | 56 |
| `execution_records` | 0 |
| `fee_records` | 0 |
| `referral_payouts` | 0 |
| `referral_obligations` | 0 |
| `bot_ownership_claims` | 0 |

**This cluster holds data.** It is not an empty scratch database. 57 profiles and 28
rows each in `exchange_keys` / `orders` / `positions` are prior fixture residue, and
`safety_control_audit` has 32 rows. Any suite that truncates, or that seeds colliding
primary keys, acts on a populated database shared with every other PG-gated suite and
with whatever other agent sessions are doing. That is the substance of approval request
**A** in deliverable 5.

---

## 5. Schema drift — the preflight failure

`scripts/verify_schema_drift.py` exits **1**:

```
baseline : staging/provisioning_snapshot.json
database : postgresql://127.0.0.1:51805/postgres
read-only: yes -- default_transaction_read_only verified 'on' before reading
server   : 16.2 (PostgreSQL 16.2)

  DRIFT tables                        baseline=85    live=89
  DRIFT columns                       baseline=1135  live=1193
  DRIFT primary_keys                  baseline=85    live=89
  DRIFT foreign_keys                  baseline=121   live=123
  DRIFT unique_and_check_constraints  baseline=122   live=139
  DRIFT indexes                       baseline=400   live=424
  DRIFT row_level_security            baseline=85    live=89
  DRIFT policies                      baseline=188   live=195
  DRIFT privileges                    baseline=991   live=1007
  same  extensions                    baseline=1     live=1
  DRIFT functions                     baseline=36    live=38
  same  triggers                       baseline=61    live=61
  same  sequences                      baseline=1     live=1
  DRIFT enum_types                    baseline=6     live=8
  same  alembic_version                baseline=1     live=1
  DRIFT recorded_migrations           baseline=50    live=56

RESULT: DRIFTED
```

12 of 16 dimensions drift.

### 5.1 Direction of the drift

| Dimension | missing from DB | not in baseline |
| --- | --- | --- |
| `recorded_migrations` | 0 | 6 |
| `tables` | 0 | 4 |
| `columns` | 0 | 58 |
| `primary_keys` | 0 | 4 |
| `foreign_keys` | 0 | 2 |
| `unique_and_check_constraints` | 0 | 17 |
| `indexes` | 0 | 24 |
| `row_level_security` | 0 | 4 |
| `enum_types` | 0 | 2 |
| **`policies`** | **1** | 8 |
| **`privileges`** | **5** | 21 |
| **`functions`** | **1** | 3 |

Mostly additive, but **not purely**: `policies`, `privileges` and `functions` each have
items present in the baseline and absent from the live database. "The baseline is just
older" does not by itself explain a disappearance.

### 5.2 The additive part has a clean explanation

The 6 extra ledger rows are migrations `026`–`031`:

```
migrations/026_fee_records_row_level_security.sql    phase8
migrations/027_referral_ledger_currency.sql          phase8
migrations/028_safety_control_audit.sql              phase9
migrations/029_referral_obligations.sql              phase9
migrations/030_execution_venue_order_id.sql          phase9
migrations/031_fee_records_fee_pct_nullable.sql      phase10
```

All six exist (in `backend_app/migrations/`, per §2.2), each with a SHA-256 in the
ledger, applied through the sanctioned path. They account for the 4 new tables
(`fee_records`, `safety_control_audit`, `referral_obligations`, plus
`bot_ownership_claims` from `025`), the 2 new enums (`feestatus`, `feetype`), and
`execution_records.venue_order_id`.

The baseline snapshot is dated **2026-10-06 20:14:20** — four days older than the live
state. So "stale baseline" is well supported for the additive drift.

### 5.3 Why that is not sufficient to pass the preflight

The drift tool's own closing text is the right frame:

> Either a change was made outside the migration set … or the baseline is stale. Decide
> which, because the two have opposite fixes.

Three facts prevent deciding it from the available evidence:

1. **The snapshot has no provenance.** Its 17 top-level keys are all schema dimensions —
   `tables`, `columns`, `policies`, `recorded_migrations`, … There is **no** recorded
   DSN, data directory, server version, role, or capture timestamp *inside the file*.
   Nothing in it establishes that it was captured from `pgdata_r1` rather than from
   `staging/pgdata`, a CI container, or production. A baseline that cannot be tied to a
   cluster cannot certify one. (**W1B-06**, P1.)
2. **`bot_ownership_claims` is inconsistent with "purely stale".** It appears as an
   *unexpected table*, yet migration `025_work03_bot_ownership_claims.sql` is **not**
   among the 6 unexpected ledger rows — so at baseline time the migration was recorded
   while the table was absent. Compatible with the snapshot having come from a different
   database; not compatible with a simple four-day lag.

   **Pollution is, however, largely excluded as the cause.** A residue sweep
   (`staging/probe_p11b_w1b_residue.py`, log `staging/p11b_w1b_residue.txt`) looked for
   the scratch objects these suites create — `tenant01_canary_*`, `tenant01_norls_*`,
   `drift_readonly_ddl_probe*`, `tenant_probe*`, `tenant_seq_probe*` — and found
   **0**. A first run of that probe reported 2 hits (`marketplace_backtest_evidence`,
   `strategy_backtests`) on a `%_test_%` pattern; both are legitimate schema tables and
   the hits were LIKE-wildcard artifacts, because `_` matches any single character in
   LIKE, so `ktest_` matched `_test_`. Re-run with `ESCAPE '#'` and literal underscores:
   **0 residue**.

   The sweep was also extended to **schemas**, which the first version missed —
   `tests/test_strategy_lifecycle_concurrency_postgres.py` creates no objects in
   `public` at all, only a per-run scratch schema `vq_1323_<hex>`. Result: **0 orphan
   schemas**, and only 9 schemas exist in total (`public`, `auth`, plus the six
   `pg_*`/`information_schema` built-ins).

   The four "unexpected" tables also do not look like residue. Their `pg_class` OIDs are
   the four highest in `public` — `referral_obligations` 41033, `safety_control_audit`
   40979, `bot_ownership_claims` 19369, `fee_records` 19345 — i.e. created last, in the
   order their migrations would run. And each carries RLS with policies attached
   (`rls=True`, 1–2 policies each), which a crashed canary would not: a canary is created
   with at most one policy and a random hex suffix.

   So the additive drift is migrations. What remains unresolved is the snapshot's
   provenance and the seven disappearances below.
3. **Items are missing, not just added.** One policy, one function overload and five
   privilege grants present in the baseline are absent live. The `functions` case is the
   legible one:

   ```
   MISSING from the database : ('process_referral_commission', '…', False)
   NOT IN this repository    : ('process_referral_commission', '…', True)
   ```

   Same name, same argument list, trailing flag flipped `False` → `True`. That is one
   function **redefined**, which `024_remediate_referral_double_commission.sql` would
   plausibly do — but "plausibly" is an inference, and the snapshot cannot confirm it.

### 5.4 Preflight decision

**FAIL / BLOCKED.** The brief directs aborting on any unexpected property. The drift is
an unexpected property, 12 dimensions wide, and it includes disappearances the stale
-baseline theory does not cover. The target is very probably correct and the baseline
very probably obsolete — but "very probably" is not a preflight result, and the whole
point of this gate is to refuse to proceed on a guess about which database is in front
of us.

**No integration lane was run, and none is proposed until this clears.**

Clearing it requires one of:

- **(i)** re-capture the baseline from this cluster and add provenance fields (DSN,
  `data_directory`, `server_version`, role, UTC capture time) to the snapshot — but
  `scripts/verify_provisioning.py` is the tool that writes it and it is forbidden here,
  because it issues `DROP DATABASE` / `CREATE DATABASE`. **This needs explicit approval
  and is not assumed.** Re-capturing against a database that may itself be wrong also
  launders the problem rather than solving it;
- **(ii)** account for the 7 disappearances (1 policy, 1 function, 5 privileges)
  migration by migration, which is a Workstream D/J task, not a W1B one;
- **(iii)** run the lane against a disposable isolated cluster, where no baseline
  comparison is needed because nothing is shared and nothing persists — approval
  request **B** in deliverable 5. This is the only option that needs no decision about
  the shared cluster at all.

---

## 6. Findings

| ID | Sev | Finding |
| --- | --- | --- |
| **W1B-05** | **P0 (TENANT-01)** | Production connects as role **`postgres`** — table owner of 89/89, `rolbypassrls = true` — with `relforcerowsecurity = 0` everywhere. The 195 RLS policies therefore do **not** constrain the application backend; they govern only the PostgREST `anon`/`authenticated` path. Backend tenant isolation rests entirely on application `WHERE` clauses with no database backstop. Re-scopes W1A-03, which attributed the gap to the test harness: the harness correctly adopts `authenticated` via `SET LOCAL ROLE` and its evidence is valid for the browser path. |
| **W1B-10** | **P1** | `service_role` carries `rolbypassrls = true` **and** holds `TRUNCATE` on 86 tables, `DELETE` on 76, `INSERT` on 88. Any path authenticating as `service_role` is unconstrained by RLS and can truncate almost the whole schema. `anon` holds `DELETE`/`INSERT`/`UPDATE` on 42 tables and `SELECT` on 49 — the Supabase blanket-grant posture that `tests/test_billing_data_isolation_postgres.py` documents, measured here as still live. |
| **W1B-06** | **P1** | `staging/provisioning_snapshot.json` (untracked, 2026-10-06) carries **no provenance** — no DSN, data directory, server version, role or capture time. It cannot be shown to describe `pgdata_r1`, so `verify_schema_drift.py`'s verdict is uninterpretable in either direction. `bot_ownership_claims` appearing as an unexpected table while migration `025` is *not* an unexpected ledger row suggests the snapshot came from a different database. |
| **W1B-07** | **P2** | 45 of 56 `schema_migrations` rows record a path that is not where the file lives (`migrations/001_…` is at `backend_app/migrations/001_…`; `hardening/999_…` is at `migrations/hardening/999_…`). Checksums still permit verification, but only for someone who already knows the real root. |
| **W1B-08** | **P1** | 1 policy, 1 function overload and 5 privilege grants present in the baseline are **absent** from the live database. Not explained by a stale baseline. Must be attributed before the shared cluster is certified. |
| **W1B-09** | **P2** | The target is **16.2**; production is **17.x**. Results here are not version-transferable. Must be stated wherever this lane's output is cited. |

### 6.1 W1A re-checks

| W1A claim | W1B re-measurement |
| --- | --- |
| Target already provisioned, at head, 89 tables, `d4e5f6a7b8c9`, 56 ledger rows | **Confirmed**, independently. |
| `data_directory` = `staging/pgdata_r1`, loopback | **Confirmed.** |
| `server_version` 16.2 | **Confirmed.** |
| RLS 82 enabled / 0 forced, role `postgres` is owner | **Confirmed and escalated** — W1A did not record `rolbypassrls = true` or `is_superuser = on`. |
| Row counts (profiles 57, exchange_keys/orders/positions 28, safety_control_audit 32, execution_records/fee_records/referral_payouts 0) | **Confirmed**, all five. |
| "PG READY" | **Not reproduced.** W1A did not run `scripts/verify_schema_drift.py`. It exits 1. The target is ready by *identity*; it is **not** verified by *shape*. |

---

## 7. Artifacts

| Path | Purpose |
| --- | --- |
| `staging/probe_p11b_w1b_import_safety.py` | AST proof that importing `verify_provisioning` has no side effects |
| `staging/p11b_w1b_import_safety.txt` | its output |
| `staging/probe_p11b_w1b_pg_preflight.py` | the independent read-only preflight |
| `staging/p11b_w1b_pg_preflight.txt` | its output |
| `staging/p11b_w1b_drift.txt` | full `verify_schema_drift.py` output (142 lines) |

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
