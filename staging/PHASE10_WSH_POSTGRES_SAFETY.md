# Phase 10 — Workstream H: PostgreSQL Test-Environment Safety

Objective: eliminate the risk that staging database startup silently points
PostgreSQL-gated tests at the wrong or empty data directory.

**Live trading was OFF throughout.** No application code was touched. No PostgreSQL
process was started, stopped, killed or restarted by this workstream; no data directory
was deleted, renamed or initialised; the shared DSN was never written. Verified in
§5.

---

## 1. Baseline (read-only, `staging/probe_p10h_baseline.py`)

```
1. SHARED DSN FILE
   staging/pg_dsn.txt = postgresql://postgres:@127.0.0.1:51805/postgres

2. IS THE CURRENT TARGET REACHABLE AND PROVISIONED?
   REACHABLE   public tables = 89
   data_directory            = C:/aerora_quant_backend_updated_final1/staging/pgdata_r1
   pg_is_in_recovery()       = False
   markers present           = 9/9

3. CANDIDATE DATA DIRECTORIES
   pgdata      PG_VERSION=16  base/ 22,177,237 B  postmaster.pid pid=13708 port=56595 -> STALE
   pgdata_r1   PG_VERSION=16  base/ 35,329,537 B  postmaster.pid pid=12924 port=51805 -> LIVE
```

So at baseline: the DSN was **correct**, `pgdata_r1` was the live provisioned cluster, and
`pgdata` was a **dormant** empty cluster whose postmaster was gone — a stale pid file, not
an active rehearsal. Both were verified before anything was changed, and `pgdata` was
deliberately **not started** in order to inspect it: establishing "not running" came from
its pid file plus the process table, because starting it would have created a second live
postmaster, which is the opposite of this workstream's purpose.

`current_setting('data_directory')` is the key primitive discovered here: it answers "which
directory does this DSN actually serve?" from the server itself, rather than by inference
from a port.

---

## 2. Root cause

Two independent defects that combined badly. Neither is a crash; both are silent.

### 2a. The directory was selected by convention, with a silent default that named a different cluster

```python
DATA_DIR = pathlib.Path(__file__).resolve().parent / os.environ.get("PG_DATA_DIR", "pgdata")
```

The correct directory was carried in **each caller's environment** rather than being a
property of the provisioned cluster, and **four different names are in circulation**:

| Caller | `PG_DATA_DIR` |
|---|---|
| `staging/start_pg.ps1` | `pgdata_r1` |
| `staging/pg_keepalive_r1.py` | `pgdata_r1` (also hardcodes the path) |
| `DATABASE_PROVISIONING_RUNBOOK.md` | `pgdata_z1` |
| `PAPER_ONLY_LAUNCH_READINESS.md` | `pgdata_z1` |
| `RLS_TENANT_ISOLATION_VERIFICATION.md` | `pgdata_z1` |
| `BILLING_SECURITY_VERIFICATION.md` | `pgdata_local` |
| `FINAL_LAUNCH_READINESS_REPORT.md` | `pgdata_local` |
| **unset** | **`pgdata`** |

Meanwhile the command printed by the module's own docstring, by `staging/dsn.py:78`, and by
the skip message of roughly eight PG-gated test files is the **bare**
`python staging/bootstrap_pg.py start` — which takes the default. On a machine whose
provisioned cluster is `pgdata_r1`, that `initdb`s a brand-new empty cluster in
`staging/pgdata`, starts it, and makes it the target.

This is exactly what was observed earlier in this phase: a second cluster appeared at
`staging/pgdata` on port 56595 and the shared DSN was repointed at it.

### 2b. The shared DSN was rewritten before anything checked what it pointed at

`pgserver` allocates a new random port on every start, so the DSN file **must** be
rewritten — but `start()` wrote it immediately after `get_uri()`, with no connection
attempt, no schema check, and no comparison against what the file already named. The
measured consequence was `staging/pg_dsn.txt` pointing at a cluster with
`public tables = 0`, where every PG-gated suite skips or fails for reasons unrelated to the
code under test.

A port check would not have helped. During the outage the port accepted TCP connections
while the postmaster was wedged in crash recovery and never completed a handshake, so
`Test-NetConnection` reported success and `psycopg2.connect` timed out.

### 2c. Three writers, only one of which validated

| Writer | Before |
|---|---|
| `staging/bootstrap_pg.py` | unconditional |
| `staging/pg_keepalive_r1.py` | unconditional, then checked connectivity **after** the fact |
| `staging/probe_pg_find.py` | **guarded** — writes only if `public_tables > 50` |

`probe_pg_find.py` already had the right discipline. The fix puts the same rule on the two
paths that actually create the file.

---

## 3. Changes

All in staging tooling and tests. **No application code and no tracked file was modified**
(every file below was already untracked or is new), so there is no interaction with
concurrent work.

### `staging/bootstrap_pg.py` — rewritten around two rules

**Selection is explicit, and ambiguity fails closed.** `--data-dir` > `PG_DATA_DIR` >
inference, and inference is permitted only where it cannot be wrong:

- no cluster exists → the documented default (a fresh checkout and CI keep working);
- the only cluster **is** the default → the default, same answer either way;
- any other cluster exists → **refuse**, listing the candidates and both ways to choose.

Also refused: `PG_DATA_DIR=` set but blank (that is what an unset variable expands to in a
shell script, and treating it as "use the default" is how a script that meant to select a
cluster silently selects another), and a relative path containing `..` (it resolves
differently per working directory). An absolute path is honoured — a cluster may
legitimately live outside `staging/`. A bare name always resolves under `staging/`, never
against the caller's working directory. A directory is recognised as a cluster by the
presence of `PG_VERSION`, not by its name, so `pgdata_notes` is not mistaken for one.

**The DSN is written last, and only after validation.** `write_dsn_if_safe` checks, in order:

1. the target is **reachable** — a real connection, not an open port;
2. the server's own `current_setting('data_directory')` **matches the directory requested**,
   so "I asked for `pgdata_r1`" and "the DSN I am recording serves `pgdata_r1`" become one
   statement rather than two hopes;
3. if `--expect-provisioned`, all schema markers are present;
4. the write is **not a downgrade** — if the file currently names a better-provisioned
   target, refuse.

On every failure the existing file is left byte-for-byte alone. `PG_ALLOW_DSN_DOWNGRADE=1`
is the deliberate override, because the from-scratch provisioning rehearsal this directory
is also used for is legitimate; it just has to be stated.

Targets are classified `UNREACHABLE` / `EMPTY` / `PARTIAL` / `PROVISIONED`, with the
`PARTIAL` threshold set to `probe_pg_find.py`'s existing `> 50` tables so the two agree.

Added a `verify` action that reports on the current DSN and changes nothing, so "is my test
database the one I think it is?" no longer requires restarting anything. Distinct exit
codes (`2` selection, `3` refused write) let a wrapper tell the two apart.

### `staging/pg_keepalive_r1.py`

Its unconditional write now goes through `write_dsn_if_safe` with
`expect_provisioned=True`, and it waits for the server to answer before judging it (crash
recovery on this cluster has taken over a minute; a single immediate probe would classify a
healthy server as unreachable). If the write is refused the server is still left **up** and
the keepalive still runs — a reachable server is useful even when the DSN should not be
repointed — and the refusal is printed rather than swallowed.

### `staging/start_pg.ps1`

Now passes `--data-dir pgdata_r1 --expect-provisioned`. The directory is named in the
command itself as well as the environment, and because this wrapper exists to serve the
regression lane, a cluster without the provisioned schema should fail loudly here rather
than produce a run in which every PG-gated suite skips.

### One incidental robustness fix, found while verifying

`bootstrap_pg.py` deliberately does **not** use `from __future__ import annotations`.
Because `staging/` is not a package the module is loaded by path a lot — by
`pg_keepalive_r1.py`, by its test, and by any probe needing it. With postponed annotations,
`dataclasses` resolves `TargetState`'s field annotations through
`sys.modules[cls.__module__].__dict__` while building the class, so a caller that exec'd
the module **without first registering it in `sys.modules`** got

```
AttributeError: 'NoneType' object has no attribute '__dict__'
```

from inside `dataclasses` — a baffling error for "I imported your file". Both real
consumers happen to register it, so this was latent; it surfaced the first time the module
was loaded the unforgiving way during verification. Real annotations cost nothing here and
remove the trap. Pinned by
`test_the_module_loads_by_path_without_sys_modules_registration`.

### New files

`tests/test_p10h_bootstrap_pg_selection_is_explicit.py`,
`staging/probe_p10h_baseline.py`, `staging/probe_p10h_validation_against_live.py`.

---

## 4. Test evidence

### Focused unit suite — 30 passed

`python -m pytest tests/test_p10h_bootstrap_pg_selection_is_explicit.py --timeout=120`

```
31 passed in 6.00s
```

| passed | failed | skipped | collection errors |
|---|---|---|---|
| 31 | 0 | 0 | 0 |

Hermetic by construction: data directories are `tmp_path` fixtures carrying a fake
`PG_VERSION`; the server factory and the database probe are injected fakes. No process is
started, no real data directory is read or written, and `dsn_file` is always a `tmp_path` —
with a final guard test asserting the real `staging/pg_dsn.txt` still exists and still
looks like a DSN, because a future test that forgets that argument would repoint the shared
DSN for every other suite on the machine.

Coverage against the brief:

| Required | Tests |
|---|---|
| Explicit selection of the provisioned directory | `test_pg_data_dir_selects_the_provisioned_cluster`, `test_the_data_dir_argument_overrides_the_environment` |
| Missing or invalid configuration | `test_an_unset_variable_refuses_when_another_cluster_exists`, `test_an_unset_variable_refuses_with_several_clusters`, `test_a_blank_variable_is_a_configuration_error_not_a_request_for_the_default`, `test_a_relative_path_climbing_out_of_staging_is_refused` |
| Refusal to select an empty/unverified cluster as the provisioned DB | `test_an_empty_cluster_is_refused_when_provisioned_is_required`, `test_a_server_serving_a_different_directory_is_not_recorded` |
| Refusal to overwrite the DSN on failed validation | `test_an_unreachable_target_is_not_recorded`, `test_an_empty_cluster_cannot_replace_a_provisioned_one` |
| DSN preserved on startup failure | `test_start_refuses_before_touching_the_dsn_when_selection_is_ambiguous`, `test_start_preserves_the_dsn_when_validation_fails` |
| Paths and env overrides | `test_an_absolute_path_is_honoured`, `test_a_bare_name_resolves_under_staging_not_the_working_directory`, `test_a_directory_without_pg_version_is_not_mistaken_for_a_cluster` |
| Loadable by path (see §3 note) | `test_the_module_loads_by_path_without_sys_modules_registration` |

Negative controls are included deliberately, so the fix is not "refuse everything":
`test_a_fresh_checkout_still_gets_the_documented_default`,
`test_the_default_is_used_when_it_is_the_only_cluster`,
`test_an_empty_cluster_is_allowed_when_provisioned_is_not_required`,
`test_the_downgrade_can_be_performed_deliberately`,
`test_an_unreachable_current_target_may_be_replaced` (the ordinary post-restart case — the
tool must still be able to refresh its own DSN after `pgserver` picks a new port).

### The original failure mode, re-run against the real CLI

The exact command that caused the incident, with `PG_DATA_DIR` cleared:

```
$ python staging/bootstrap_pg.py start
bootstrap_pg: refusing to guess which PostgreSQL data directory to use.
  PG_DATA_DIR is unset, so this would have defaulted to 'pgdata' -- but these clusters
  already exist in C:\aerora_quant_backend_updated_final1\staging:
      pgdata
      pgdata_r1
  Defaulting here is how a provisioned cluster gets bypassed in favour of an empty one,
  which then becomes the target of every PG-gated test.
  Choose explicitly:
      PG_DATA_DIR=pgdata_r1 python staging/bootstrap_pg.py start
      python staging/bootstrap_pg.py start --data-dir pgdata_r1

exit code: 2
DSN before : postgresql://postgres:@127.0.0.1:51805/postgres
DSN after  : postgresql://postgres:@127.0.0.1:51805/postgres
```

It refuses **before** starting anything, names both clusters, suggests the provisioned one,
and leaves the DSN untouched.

### Real validation code against the live cluster — all checks passed

`python staging/probe_p10h_validation_against_live.py` (exit 0). `pgserver` is never
imported; every write goes to a temp file; the shared DSN is checksummed before and after.

```
1. REAL PROBE: PROVISIONED  public_tables=89  data_directory=.../staging/pgdata_r1
2. resolve_data_dir('pgdata_r1') vs the server's own data_directory -> _same_directory=True
3. a verified provisioned target IS recorded (temp file)            -> recorded=True
4. an empty cluster may NOT replace the live provisioned one        -> refused, DSN preserved
5. staging/pg_dsn.txt sha256 before == after                        -> unchanged
```

Check 2 also confirms the cross-platform path comparison: `pathlib` yields
`...\staging\pgdata_r1` while PostgreSQL reports `.../staging/pgdata_r1`, and they match.

### Database-backed regression (target validated first, bounded timeouts)

Target validated with the new non-mutating command before running anything:

```
$ python staging/bootstrap_pg.py verify
DSN_FILE: ...\staging\pg_dsn.txt
TARGET:   PROVISIONED  public_tables=89  data_directory=.../staging/pgdata_r1
exit 0
```

| Group | Command | passed | failed | skipped | collection errors |
|---|---|---|---|---|---|
| schema + DSN consumers | `pytest test_orm_models_match_provisioned_schema test_schema_drift_checker_is_read_only_postgres test_production_security_probe_rolls_back_postgres test_schema_table_reference_drift --timeout=240` | 122 (+1 xfailed) | 0 | 0 | 0 |
| bootstrap-coupled PG suites | `pytest test_bug049_identity_types_postgres test_bug056_… test_bug057_… test_cross_tenant_read_sweep_postgres test_money01_… --timeout=240` | 140 | 0 | 1 | 0 |

The single skip is pre-existing and documented — `authenticated` holds no SELECT on
`payment_methods`, so that policy cannot be exercised from that role
(`RLS_TENANT_ISOLATION_VERIFICATION.md` §8.3). It is **not** a "needs a real PostgreSQL"
skip, which is the distinction that matters here: those mass environment skips are the
symptom this workstream removes.

Every invocation carried `--timeout=240` and was sized well under the harness timeout,
because a force-killed shell sending `STATUS_CONTROL_C_EXIT` into the postmaster's process
group is what took this cluster down earlier in the phase.

---

## 5. Final diff inspection

| Check | Result |
|---|---|
| Data directory deleted? | **No.** `pgdata` base/ = 22,177,237 B, byte-identical to baseline. `pgdata_r1` = 35,386,881 B vs 35,329,537 B at baseline — grew ~57 KB from the test runs' catalogue/WAL activity, which is growth, not deletion. |
| Database process killed or restarted? | **No.** `PID 12924 -D .../staging/pgdata_r1 -p 51805` — the same pid and port as baseline. |
| New cluster initialised? | **No.** `pgdata`'s mtime and size are unchanged; the bare `start` refused before reaching `initdb`. |
| Stale pid files renamed or removed? | **No.** `postmaster.pid.stale_*` count is 0 in both directories. |
| Shared DSN modified? | **No.** Identical to the copy taken before testing, and sha256-verified unchanged by the live-validation probe. |
| Tracked files modified? | **None.** `staging/bootstrap_pg.py`, `staging/pg_keepalive_r1.py` and `staging/start_pg.ps1` were all already untracked; everything else is new. |
| Concurrent in-flight changes preserved? | **Yes.** No tracked file was touched, so the other tabs' modified files (`backend_app/**`, `tests/conftest.py`, the CCXT upgrade probe under `staging/.ccxt_upgrade_probe/`) are untouched. |

Scratch files created during verification were removed
(`staging/pg_dsn.txt.p10h_preserve`, `staging/p10h_dsn_before.txt`); the `staging/p10h_*.txt`
run logs are retained as evidence.

---

## 6. Remaining risks

1. **`staging/pgdata` still exists as a dormant empty cluster.** The brief forbids deleting
   or renaming either directory in this task, so it remains. Its presence is now *harmless
   by design* — it is precisely the condition that makes the bare `start` refuse — but it
   also means the refusal is the normal path on this machine until someone decides whether
   that rehearsal cluster is still wanted. Deleting it would restore silent-default
   behaviour (one cluster, equal to the default), so **if it is removed, the explicit
   `--data-dir` in `start_pg.ps1` becomes the only thing keeping selection unambiguous.**
2. **`staging/probe_pg_find.py` still rewrites the shared DSN** on its own `> 50 tables`
   rule. That rule is sound and predates this work, but it does not check
   `data_directory` and has no downgrade guard, so it is a third writer with weaker rules
   than the other two. Out of scope here; worth routing through `write_dsn_if_safe`.
3. **Seven runbooks and reports still name three different `PG_DATA_DIR` values**
   (`pgdata_z1`, `pgdata_local`, `pgdata_r1`). They are now safe — an explicit value is
   always honoured — but a reader following `DATABASE_PROVISIONING_RUNBOOK.md` will create
   `pgdata_z1`, adding a fourth cluster and another name to the set. Reconciling the docs
   on one name is a documentation task that was not in this brief.
4. **`pgserver`'s hardcoded 10-second `pg_ctl` timeout is untouched.** It is the reason the
   earlier recovery attempt failed, since the data-directory fsync alone took 67 seconds on
   this cluster. `bootstrap_pg.py` now reports a clear refusal instead of a confusing
   partial success, but it cannot make a slow start succeed; `staging/pg_keepalive_r1.py`
   remains the way to bring up a cluster that needs a long recovery.
5. **The validation is a point-in-time check.** `verify` and the write-time probe tell you
   what was true when they ran. Nothing prevents a cluster from being stopped afterwards,
   which is what the Phase 9/10 suites' own skip gates are for.
6. **`PG_EXPECT_PROVISIONED` / `--expect-provisioned` is opt-in.** Only `start_pg.ps1` and
   `pg_keepalive_r1.py` assert it. A hand-run `start --data-dir pgdata_z1` against an empty
   cluster will still succeed and record it — correctly, since that is a legitimate
   rehearsal — so the protection for the *shared* DSN in that case rests on the downgrade
   guard rather than on the marker check.
7. **Not addressed, by instruction:** migration `032`, referral settlement, exchange
   reconciliation startup, and the CCXT upgrade. None was touched.
