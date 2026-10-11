# Phase 11B / Workstream 1B — Preparation report

**Deliverable 5 of 5.** The decisions, the approval requests, and the exact change made.

**One file was changed.** No test lane was executed. No database was written. No
provisioning, migration, Redis install, commit, push, CI trigger or production access
occurred.

| Companion deliverable | Covers |
| --- | --- |
| `staging/PHASE11B_W1B_BASELINE.md` | baseline, the collection blocker, the correction, suspended coverage |
| `staging/PHASE11B_W1B_PG_PREFLIGHT.md` | read-only PostgreSQL preflight |
| `staging/PHASE11B_W1B_PG_TEST_SAFETY_MATRIX.md` | per-suite write inventory |
| `staging/PHASE11B_W1B_REDIS_READINESS.md` | real-Redis lane |

---

## 1. Decisions

### D1 — Collection correction: **PASS**

The blocker (W1A-01) is resolved. `python -m pytest --collect-only -q` completes with
**exit 0 and no errors**; before, it ended `Interrupted: 1 error during collection` and
discarded the whole session.

The two files the broken suite depends on, `staging/probe_pg_find.py` and
`staging/pg_keepalive_r1.py`, are **absent and were never committed** — `git log --all`
is empty for both and no blob with either name exists in any tree. There is nothing to
restore. Both existed earlier today (source mtimes `12:57:29` and `12:18:55`, recovered
from surviving bytecode) and were deleted by a filesystem action; `git reflog` shows no
git operation today at all.

Resolution: a **conditional module-level skip** that fires only while
`staging/probe_pg_find.py` is absent, names both missing files, states in capitals that
it is not a pass, and cites the evidence. **No assertion was added, removed, weakened or
re-worded.** The two missing modules were **not** authored — reasoning in
`PHASE11B_W1B_BASELINE.md` §3, decisive point being that both write the shared DSN and
one starts a database, which W1B is forbidden to touch.

Cost: **23 test functions / 27 node IDs** suspended, itemised test-by-test in
`PHASE11B_W1B_BASELINE.md` §5. Of those, 5 functions / 7 node IDs covered DSN-safety
properties and 4 functions / 6 covered the writer-unification property.

### D2 — Read-only PostgreSQL preflight: **FAIL**

Target **identity** passes every check: `127.0.0.1:51805`, `data_directory` =
`staging/pgdata_r1` confirmed by the server, loopback, not in recovery, 89 public tables,
`alembic_version = d4e5f6a7b8c9` (single head), 56 ledger rows all with source on disk,
`bootstrap_pg.py verify` → `PROVISIONED`.

Target **shape** does not. `scripts/verify_schema_drift.py` exits **1** —
`RESULT: DRIFTED`, 12 of 16 dimensions. Mostly additive and explainable by six
post-baseline migrations (`026`–`031`), and pollution is substantially excluded (0 orphan
tables, 0 orphan schemas; the four "unexpected" tables hold the highest OIDs and carry
RLS policies). But the baseline snapshot carries **no provenance** — no DSN, data
directory, server version, role or capture time — so it cannot be shown to describe this
cluster, and **7 items present in the baseline are absent from the database** (1 policy,
1 function overload, 5 privilege grants), which staleness does not explain.

The brief directs aborting on any unexpected property. This is one. **FAIL, and no lane
was proposed on this basis.**

### D3 — PostgreSQL test-write safety: **PASS**

62 PG-related files statically inventoried, every destructive hit then read by hand.

- **Every** suite gates on an explicit DSN and **skips** when absent. No suite can write
  to a database on an unconfigured machine.
- **No `TRUNCATE` reaches a live table.** The alarming `TRUNCATE`/`DROP`/`ALTER`/`GRANT`
  hits in `test_kill03`, `test_money04`, `test_tenant02`, `test_migration_tooling` and
  `test_billing_data_isolation` are **forbidden-string assertions over migration source
  text** — tests asserting those verbs are *absent*.
- All real DDL targets ephemeral objects: `tenant01_canary_<hex8>` /
  `tenant01_norls_<hex8>` tables, a per-run `vq_1323_<hex10>` scratch *schema*, and one
  `drift_readonly_ddl_probe` table — each with `finally` teardown. The cluster currently
  holds **zero** residue.
- 4 suites do commit writes to live shared tables; `test_kill03` commits 2 rows into
  `public.safety_control_audit` with **no cleanup** (W1B-11).

### D4 — Shared-cluster coordination: **NOT AUTHORIZED**

No approval is recorded, so the shared `pgdata_r1` lane is not authorized and was not
run. See approval request **A**.

Beyond authorization, coordination is a live problem rather than a formality: **other
agent sessions are actively working this repository right now** — 10 files written by
other sessions during W1B's own execution window, 8 test files changed since 12:51, and
the collected test count moved 14,461 → 14,440 → 14,467 across three readings today, none
of which W1B caused. Suites that commit with `autocommit = True` into a shared database
cannot be run meaningfully while that is true.

### D5 — Isolated PostgreSQL option: **NOT AVAILABLE**

Two independent blockers, both measured:

| Blocker | Measurement |
| --- | --- |
| **No server factory.** | `pgserver` — the module `staging/bootstrap_pg.py` calls to create and start a cluster — is **ABSENT** (`ModuleNotFoundError`). `testing.postgresql` and `pytest_postgresql` are also absent. `psycopg2`, `alembic`, `sqlalchemy` are present, but none can *create* a cluster. |
| **No disk.** | `C:` has **444 MiB free** of 111.1 GiB. The existing clusters measure 38 MiB (`pgdata`) and 71 MiB (`pgdata_r1`), so a provisioned cluster is ~71 MiB plus WAL — and installing `pgserver` means downloading a bundled PostgreSQL distribution, typically 100–300 MB. That does not comfortably fit. |

So option B requires an install **and** freeing disk. Approval request **B**.

### D6 — Real-Redis lane: **BLOCKED (environmental)**

No Redis server exists on this machine: all of 6379/6380/6381/6399/7379 closed by real
TCP connect; no `redis-server`, `redis-cli`, `valkey-server`, `memurai`, `docker` or
`podman` on PATH; `staging/redis/` absent (so the runbook's documented
`.\staging\redis\redis-server.exe` cannot be run); WSL present but `NO_REDIS_SERVER`.
`AERORA_TEST_REDIS_URL` unset. `redis-py 5.0.8` is installed — only the server is
missing.

The gate and the suites are **correct**: opt-in variable only with no `REDIS_URL`
fallback (AST-verified from the body of `redis_url()`), loopback refusal, PING
verification, three distinct outcomes, and — newly uniform since another session's 13:34
rewrite — a configured-but-unreachable endpoint **fails** rather than skips in all three
suites. CI exports `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` to the job that
has the Redis service.

Installing Redis is not authorized here. Approval request **C**.

### D7 — TENANT-01 evidence: **OPEN**, and not closable by this lane

This is the decision that changed most under measurement, including a correction to my
own first conclusion.

| Role | `rolsuper` | `rolbypassrls` | owns the 89 tables | RLS applies |
| --- | --- | --- | --- | --- |
| `postgres` | True | **True** | **all 89** | **No** |
| `service_role` | False | **True** | 0 | **No** |
| `authenticated` | False | False | 0 | **Yes** |
| `anon` | False | False | 0 | **Yes** |

- The tenant suites **do** adopt a browser identity — `SET LOCAL ROLE authenticated` plus
  `set_config('request.jwt.claim.sub', …, true)` — and `authenticated`/`anon` are
  non-superuser, non-`BYPASSRLS`, non-owner. **RLS is genuinely evaluated for them, and
  their evidence is valid for the PostgREST/browser path.** `FORCE` is irrelevant there,
  because FORCE governs only the owner.
- **Production connects as `postgres`**:
  `DATABASE_URL = postgresql://postgres:<redacted>@db.<project-ref>.supabase.co:6543/postgres`
  — table owner, `BYPASSRLS`, against `relforcerowsecurity = 0` on all 89 tables. **The
  application backend is therefore not constrained by RLS at all.** Backend tenant
  isolation rests entirely on application `WHERE` clauses with no database backstop.

So the harness is sound and the gap is production's connection role. **Running the lane
cannot close TENANT-01** — the fix is a configuration/code change (a non-owner
application role, or `FORCE ROW LEVEL SECURITY`), not a test result. **W1B-05.**

> **Correction.** My first draft of `PHASE11B_W1B_PG_PREFLIGHT.md` §3 stated that a
> passing tenant-isolation run "is not evidence that RLS prevents cross-tenant access".
> That was too strong: it was true of the connection's default `postgres` identity but
> not of the roles the suites actually probe under. The corrected section is §3.1–3.2 of
> that document. W1A-03 is re-scoped rather than confirmed.

### Summary

| # | Decision | Verdict |
| --- | --- | --- |
| D1 | Collection correction | **PASS** |
| D2 | Read-only PG preflight | **FAIL** |
| D3 | PG test-write safety | **PASS** |
| D4 | Shared-cluster coordination | **NOT AUTHORIZED** |
| D5 | Isolated PG option | **NOT AVAILABLE** |
| D6 | Real-Redis lane | **BLOCKED** |
| D7 | TENANT-01 evidence | **OPEN** |

---

## 2. Approval requests

Each states what would be done, the irreversible part, and what it would and would not
buy. None is assumed granted. **The user's wish to see the project finished is not
approval for any of these.**

### Approval A — run the PG lane against the shared `staging/pgdata_r1`

**What:** execute the PG-gated suites against `127.0.0.1:51805` / `pgdata_r1`.

**Writes it would commit** to a database that already holds 57 profiles, 28
exchange_keys, 28 orders, 28 positions and 32 safety_control_audit rows:
`auth.users`, `public.profiles`, `public.orders`, `public.execution_records`,
`public.fee_records`, `public.safety_control_audit`, `public.referral_*` (5 tables),
`public.library_strategies`, `public.bot_ownership_claims`.

**Irreversible:** `test_kill03_authoritative_global_halt.py` commits 2 rows into
`public.safety_control_audit` and does not remove them. A killed run also orphans a
canary table or scratch schema permanently, which would make every later
`verify_schema_drift.py` fail.

**Conditions if granted:** C1 serial execution, no xdist; C2 no other session touching
the cluster; C3 row-count and object/schema sweep before and after; C4 `test_kill03`
excluded or its 2 permanent rows accepted in advance; C5 no `--lf`/`--ff` rerun before the
post-run snapshot. (`PHASE11B_W1B_PG_TEST_SAFETY_MATRIX.md` §4.2.)

**Would buy:** execution evidence for the ~22 writing suites on PostgreSQL 16.2.
**Would not buy:** TENANT-01 closure (D7); resolution of the D2 drift failure, since the
baseline remains unprovenanced; any claim about PostgreSQL 17.x.

**Blocked by:** D2 is FAIL. A green lane against a cluster whose shape cannot be verified
is evidence of less than it appears. Recommend resolving W1B-06 and W1B-08 first, or
preferring approval B.

### Approval B — create a disposable isolated cluster

**What:** install `pgserver`, `initdb` a throwaway cluster on a scratch port, apply
`scripts/provision_database.py --apply`, run the lane, destroy it.

**Needs two things W1B cannot do:**
1. **Install `pgserver`** — absent; a new dependency plus a bundled PostgreSQL download.
2. **Free disk** — 444 MiB available; the install alone is likely 100–300 MB and a
   provisioned cluster ~71 MiB plus WAL.

**Would buy:** the cleanest option. No shared state, no coordination with other sessions,
no baseline needed (nothing persists, so drift is meaningless), and `test_kill03` could
run without leaving anything behind. It would also sidestep D2 entirely.

**Would not buy:** TENANT-01 closure — the same `postgres`-owner posture would be
reproduced unless the cluster is deliberately built with a non-owner application role.
If approved, **build it that way**: it is the only configuration in which the backend
path's isolation can be tested at all.

**Note:** the destructive operations are confined to an object created for the purpose, so
the blast radius is a directory that did not exist beforehand. `provision_database.py`
also refuses non-loopback DSNs and port 6543, and defaults to `--dry-run`.

### Approval C — install a local Redis for the real-Redis lane

**What:** install a Redis server on loopback (Memurai as a Windows service, or
`apt install redis-server` inside WSL, or fetch the runbook's
`staging/redis/redis-server.exe`), then
`$env:AERORA_TEST_REDIS_URL = "redis://127.0.0.1:6379/15"` and run the three gated suites.

**Blast radius:** database index **15** on a dedicated local instance. No `FLUSHDB` or
`FLUSHALL` appears in any of the three suites; keys are namespaced `q01-slot-*`,
`q01-slot-e2e-*`, `q01-real-*`; clients are closed in `finally`. Disk is the practical
constraint again at 444 MiB free.

**Would buy:** `test_webhook_duplicate_delivery_real_redis.py` exercises the production
`redis_manager` facade and asserts the cache is neither `None` nor a `MockRedisClient` —
it would close **P11-U04's Lua half**, the first real-Redis evidence this project has ever
had.

**Would not buy:** full QUOTA-01 closure. `test_quota01_exchange_slot_is_atomic.py`
injects its own `_RealCache` over `sd.redis_manager` and so **bypasses the production
facade**; it evidences the algorithm, not the fail-soft `get()`/`incr()` that is QUOTA-01's
actual open concern (W1B-17).

**Not acceptable as a substitute:** pointing the variable at any shared, staging or
managed Redis. The gate refuses non-loopback hosts unless
`AERORA_TEST_REDIS_ALLOW_REMOTE=1`, and setting that flag to reach a shared instance would
defeat the control.

---

## 3. The exact change

### 3.1 Files modified: one

`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` — untracked (`??`), owned by
Phase 10 Workstream I. 25,661 → 25,739 bytes. One insertion of 42 lines immediately above
the two by-path module loads at what were lines 87–88. **No other line in the file
changed**, and the inserted region is pure ASCII (verified byte-wise; the file's
pre-existing U+2014 and box-drawing characters were left alone).

```diff
--- a/tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
+++ b/tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
@@ -85,6 +85,48 @@ def _load(name, path, register=True):
     spec.loader.exec_module(mod)
     return mod
 
+
+def _missing_under_test():
+    """The modules this suite loads BY PATH, and whether they are on disk right now.
+
+    PHASE 11B / WORKSTREAM 1B -- collection-blocker guard. See
+    `staging/PHASE11B_W1B_BASELINE.md`.
+
+    `pf = _load(..., PROBE_PATH)` below runs at IMPORT time, so when
+    `staging/probe_pg_find.py` is absent the `FileNotFoundError` is raised during
+    collection, not during a test. pytest reports that as an ERROR and
+    `Interrupted: N error(s) during collection` -- it discards the whole session, so
+    every other test in this repository (14,440 at the time of writing) becomes
+    unrunnable because of one missing file. One absent tooling script must not be
+    able to take the suite down.
+
+    This turns that into a module-level SKIP that names what is missing. Nothing else
+    changes: every assertion in this file is untouched, and the moment
+    `staging/probe_pg_find.py` is back the suite runs in full again with no edit here.
+
+    A skip is NOT a pass. The 23 test functions below (27 node IDs) are the only
+    regression evidence for the Workstream I DSN-writer unification, and while this
+    guard is active that evidence does not exist.
+    `staging/PHASE11B_W1B_BASELINE.md` records the lost coverage test by test, and
+    `staging/p11b_w1b_pyc_recovery.txt` carries the surviving bytecode evidence of
+    what both missing files contained, for whoever restores them.
+    """
+    return [p for p in (PROBE_PATH, KEEPALIVE_PATH) if not p.exists()]
+
+
+_MISSING = _missing_under_test()
+if PROBE_PATH in _MISSING:
+    pytest.skip(
+        "W1B collection-blocker guard: "
+        + ", ".join(f"staging/{p.name}" for p in _MISSING)
+        + (" are" if len(_MISSING) > 1 else " is")
+        + " absent from the working tree and never committed "
+        "(`git log --all` is empty), so there is no revision to check out. "
+        "This suite loads probe_pg_find.py by path at import time, which aborted "
+        "collection for the entire repository. Skipping this module instead. "
+        "THIS IS NOT A PASS: "
+        "the Workstream I DSN-writer unification has no regression evidence while "
+        "this guard is active. Recovery evidence: staging/p11b_w1b_pyc_recovery.txt. "
+        "Details: staging/PHASE11B_W1B_BASELINE.md",
+        allow_module_level=True,
+    )
+
 bp = _load("_p10i_bootstrap_pg", BOOTSTRAP_PATH)
 pf = _load("_p10i_probe_pg_find", PROBE_PATH)
```

### 3.2 Deliberately NOT touched

| Path | Why |
| --- | --- |
| `pytest.ini` | shared; an `addopts` exclusion would hide the file silently and change CI |
| `tests/conftest.py` | shared, and currently ` M` from another session |
| any assertion in the guarded file | the brief forbids changing assertions for a green result |
| `staging/pg_dsn.txt` | verified byte-identical before and after (sha256 `A746580D…3A96`, mtime `13:17:04`) |
| `staging/pgdata_r1`, Alembic state, all table contents | verified unchanged (§4) |
| `staging/bootstrap_pg.py`, `staging/dsn.py`, `staging/probe_pg_ready.py` | other workstreams' files |
| `staging/probe_pg_find.py`, `staging/pg_keepalive_r1.py` | **not authored** — D1, and `PHASE11B_W1B_BASELINE.md` §3 |

### 3.3 Files created: 25, all read-only instruments and reports

| Probes (all read-only) | Logs |
| --- | --- |
| `probe_p11b_w1b_pyc_recovery.py` | `p11b_w1b_pyc_recovery.txt` |
| `probe_p11b_w1b_import_time_paths.py` | `p11b_w1b_import_paths.txt` |
| `probe_p11b_w1b_lost_coverage.py` | `p11b_w1b_lost_coverage.txt` |
| `probe_p11b_w1b_import_safety.py` | `p11b_w1b_import_safety.txt` |
| `probe_p11b_w1b_pg_preflight.py` | `p11b_w1b_pg_preflight.txt`, `p11b_w1b_drift.txt` |
| `probe_p11b_w1b_pg_write_matrix.py` | `p11b_w1b_pg_write_matrix.txt` |
| `probe_p11b_w1b_residue.py` | `p11b_w1b_residue.txt` |
| `probe_p11b_w1b_app_role.py` | `p11b_w1b_app_role.txt` |
| `probe_p11b_w1b_redis.py` | `p11b_w1b_redis.txt` |
| `probe_p11b_w1b_isolated_option.py` | `p11b_w1b_isolated_option.txt` |

Plus the 5 deliverable documents. `probe_p11b_w1b_app_role.py` reads `.env` files and
prints **only** usernames, hosts, ports and database names; secret-named variables are
reported as a length plus a SHA-256 prefix, never as values.

---

## 4. Verification that nothing was mutated

| Invariant | Before | After |
| --- | --- | --- |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| ahead / behind `origin/main` | `0 / 0` | **same** |
| `staging/pg_dsn.txt` sha256 | `A746580D3CEAD2EFA056E3F422FA38EF4B0D303AF94BBE1F95B75C2661AF3A96` | **same** |
| `staging/pg_dsn.txt` mtime | `13:17:04` | **same** |
| public tables | 89 | **89** |
| `alembic_version` | `d4e5f6a7b8c9` | **`d4e5f6a7b8c9`** |
| `schema_migrations` rows | 56 | **56** |
| RLS enabled / forced | 82 / 0 | **82 / 0** |
| `profiles` | 57 | **57** |
| `exchange_keys` / `orders` / `positions` | 28 / 28 / 28 | **28 / 28 / 28** |
| `safety_control_audit` | 32 | **32** |
| orphan tables / schemas | 0 / 0 | **0 / 0** |
| commits / pushes / CI runs | — | **none** |

Collection, final state:

```
python -m pytest --collect-only -q
================= 14467 tests collected in 165.48s (0:02:45) ==================
EXIT 0        (no errors, no "Interrupted")

python -m pytest tests/test_p10i_... tests/test_p10h_... -ra -q
======================= 31 passed, 1 skipped in 10.92s ========================
```

The headline collected count read 14,461 → 14,440 → 14,467 across today's three
measurements. **None of that movement is attributable to this change**: a control run with
the file excluded outright (`--ignore=…`) produced exactly the same count as the guarded
run, so the guard contributes 0 collected tests — the same contribution the collection
error made. The drift is other sessions adding and removing tests while W1B ran.

The control that matters most: `tests/test_p10h_bootstrap_pg_selection_is_explicit.py`,
which covers `bootstrap_pg.write_dsn_if_safe` — the shared DSN safety contract itself —
**passes 31/31**. The guard suspended the probe's suite without touching the helper's.

---

## 5. Findings raised by W1B

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| **W1B-01** | **P0** | `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` absent and never committed; both existed today; they are the two DSN writers Workstream I set out to refactor. Recovery evidence in `p11b_w1b_pyc_recovery.txt`. Owner: Workstream I. | OPEN |
| **W1B-02** | **P0** | One missing by-path dependency aborted collection for the whole repository. Structural: `staging/` is not a package, so module-scope by-path loading is the established pattern across the P10H/I/J suites. | MITIGATED for this file |
| **W1B-03** | **P1** | `staging/PHASE10_WSI_POSTGRES_DSN_CLOSEOUT.md` claims a verified closeout for files that do not exist. The verdict must be reopened. | OPEN |
| **W1B-04** | **P2** | The whole P10H/I/J toolchain is untracked, so a filesystem slip loses it with no recovery path. W1A-06's theoretical risk has now actually occurred. | OPEN |
| **W1B-05** | **P0** | Production connects as `postgres` — owner of 89/89, `rolbypassrls = true` — with `FORCE` off everywhere. The 195 RLS policies do not constrain the application backend. Re-scopes W1A-03. | OPEN |
| **W1B-06** | **P1** | `staging/provisioning_snapshot.json` has no provenance, so `verify_schema_drift.py`'s verdict is uninterpretable in either direction. | OPEN |
| **W1B-07** | **P2** | 45 of 56 ledger rows record a path that is not where the file lives. | OPEN |
| **W1B-08** | **P1** | 1 policy, 1 function overload and 5 privilege grants present in the baseline are absent from the database. Not explained by staleness. | OPEN |
| **W1B-09** | **P2** | Target is 16.2; production is 17.x. Results are not version-transferable. | OPEN |
| **W1B-10** | **P1** | `service_role` has `rolbypassrls = true` **and** `TRUNCATE` on 86 tables; `anon` holds DML on 42 tables. | OPEN |
| **W1B-11** | **P1** | `test_kill03` commits 2 rows into `safety_control_audit` with no cleanup; the table's 32 rows are consistent with prior runs. | OPEN |
| **W1B-12** | **P2** | 4 suites create shared-cluster objects and rely on `finally`. Currently 0 orphans, so latent. | OPEN |
| **W1B-13** | **P2** | 3 suites write through ORM/dynamic paths static analysis cannot classify. | OPEN |
| **W1B-14** | **P2** | Local cluster has 9 schemas; production Supabase adds `storage`, `realtime`, `extensions`, `vault`, `graphql`. | OPEN |
| **W1B-15** | **P1** | The real-Redis lane has never executed, anywhere. `P11-U04` and the facade half of QUOTA-01 rest on zero integration evidence. | OPEN |
| **W1B-16** | **P2** | The runbook documents `staging/redis/redis-server.exe`; neither it nor `staging/redis/` exists. | OPEN |
| **W1B-17** | **P2** | `test_quota01_exchange_slot_is_atomic.py` bypasses the production facade; a green run would not close QUOTA-01. | OPEN |
| **W1B-18** | **P3** | `paper-mode-tests` runs a Redis service without exporting the opt-in variable, so the gated suites skip there. | OPEN |
| **W1B-19** | **P2** | `staging/pgdata` carries a **stale `postmaster.pid`** recording port 56595 with nothing listening. Two clusters plus a stale pid file is the Workstream H hazard shape. `bootstrap_pg.resolve_data_dir` fails closed on ambiguity, which contains it. | OPEN |

### 5.1 Prior findings re-checked

| ID | W1B result |
| --- | --- |
| **W1A-01** (P0, collection abort) | **RESOLVED** — D1. Cause narrower than recorded: two files, both never committed. |
| **W1A-03** (P0, owner role / 0 forced) | **RE-SCOPED** → W1B-05. The harness is sound; the gap is production's role. |
| **W1A-06** (P2, untracked instruments) | **CONFIRMED and escalated** → W1B-04, with a realised loss attached. |
| W1A "PG READY" | **NOT REPRODUCED** → D2 is FAIL. W1A did not run `verify_schema_drift.py`. |
| W1A target-state measurements (89 tables, head, 56 rows, 16.2, loopback, row counts, RLS 82/0) | **ALL CONFIRMED** independently. |
| WSK divergence (webhook SKIPs while quota FAILs on unreachable Redis) | **RESOLVED** by another session's 13:34 gate rewrite — now uniformly FAIL. |
| **A3-02** (P0, 39 CI failures) | Still OPEN. Blocks attributing any CI result to the Redis lane. |
| **A3-05** (alembic multiple heads) | Still CLOSED. Single head `d4e5f6a7b8c9` re-confirmed. |

---

## 6. Recommended order of work

1. **Restore `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py`** (Workstream I,
   using `p11b_w1b_pyc_recovery.txt`), then confirm the guard goes inert and all 27 node
   IDs run. Reopen the WSI closeout verdict (W1B-03).
2. **Commit the P10H/I/J toolchain and these suites.** They are the instruments every PG
   decision rests on and they are untracked (W1B-04).
3. **Resolve W1B-05** — a non-owner application role, or `FORCE ROW LEVEL SECURITY`. This
   is the only route to TENANT-01 on the backend path, and no test run substitutes for it.
4. **Prefer approval B over A.** An isolated cluster bypasses the D2 drift failure, needs
   no coordination with other sessions, and can be built with a non-owner role so it also
   serves step 3. It needs an install and disk first.
5. **Approval C** for the real-Redis lane — the single highest-value unit of new evidence
   available, since it would close P11-U04's Lua half.
6. **A3-02** before any CI-based claim.

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
