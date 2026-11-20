# Phase 11B / Workstream 1B — Baseline and collection correction

**Scope of this document.** Deliverable 1 of 5. It records (a) the repository state
W1B measured for itself rather than inheriting from W1A, (b) the investigation of the
pytest collection blocker W1A-01, and (c) the single correction made, its exact text,
and the regression coverage that correction suspends.

Nothing in this document was taken on trust from `staging/PHASE11B_W1A_VERIFICATION_LANE_PREPARATION.md`.
Every number below was re-measured. Where W1A's figure and W1B's figure differ, both
are shown with the cause.

---

## 1. Measured repository baseline

Captured at `2026-10-10 15:47`–`16:02` local.

| Property | Value |
| --- | --- |
| Branch | `main` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Ahead / behind `origin/main` | `0 / 0` |
| `git status --porcelain` entries | **254** |
| `.git/index` SHA-256 | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` |
| `.git/index` mtime | `2026-10-10 12:51:24` |

Working-tree entries by status code:

| Code | Meaning | Count |
| --- | --- | --- |
| `??` | untracked | 134 |
| ` M` | modified, unstaged | 98 |
| `A ` | added, staged | 12 |
| `M ` | modified, staged | 8 |
| `MM` | staged and further modified | 1 |
| `AM` | added then modified | 1 |

`git reflog` shows **no git operation at all today** — its most recent entry is
`2026-10-05 23:40:19`. So nothing in the working tree today was produced by a
`checkout`, `reset`, `stash` or `clean`. Every change since then is a plain filesystem
edit, and `HEAD` has not moved.

### 1.1 Concurrent activity is live and heavy

This repository is being worked by several agent sessions at once. Files written by
other sessions during W1B's own execution window:

| Time | Path |
| --- | --- |
| 15:35:25 | `tests/test_webhook_duplicate_delivery_real_redis.py` |
| 15:37:38 | `staging/p11b5_case1_unset.txt` |
| 15:38:33 | `staging/p11b5_force_gate.py` |
| 15:40:28 | `staging/p11b5_manifest.txt` |
| 15:41:10 | `staging/a7_manifest_check.py` |
| 15:42:11 | `staging/execute_p11b6_d5_pr.py` |
| 15:42:35 | `staging/a7_billing_committed.py` |
| 15:43:08 | `staging/p11b6_execute.txt` |
| 15:43:46 | `staging/p11b6_runs.json` |
| 15:46:42 | `staging/p11bd2_baseline.txt` |

Test files changed by other sessions since the W1A index timestamp (`12:51:24`):

```
13:06:51   tests/test_orm_models_match_provisioned_schema.py
13:34:04   tests/real_redis_gate.py
13:43:31   tests/test_p10j_probe_pg_ready_uses_shared_classification.py   (new)
13:51:19   tests/test_quota01_exchange_slot_is_atomic.py
13:51:20   tests/test_quota01_metered_reservation_is_hard.py
14:49:18   tests/test_fill01_partial_fill_accounting.py                   (new)
15:06:23   tests/test_dup01_client_order_id_mapping.py                    (new)
15:35:25   tests/test_webhook_duplicate_delivery_real_redis.py
```

This matters for two reasons, both carried into the decisions below:

1. **Counts drift.** Any absolute test count in this document is a reading at a stated
   time, not a stable property of the branch.
2. **Ownership is contested.** Files under `staging/` and `tests/` may be mid-flight in
   another session. W1B therefore made exactly one edit, to one file, and that file is
   argued for explicitly in §3.

---

## 2. The collection blocker (W1A-01), investigated

### 2.1 The failure, reproduced

```
E   FileNotFoundError: [Errno 2] No such file or directory:
    'C:\aerora_quant_backend_updated_final1\staging\probe_pg_find.py'
ERROR tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============= 14461 tests collected, 1 error in 142.77s (0:02:22) =============
```

`tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` loads two `staging/`
modules by path at **module scope**, because `staging/` is not a package:

```python
bp = _load("_p10i_bootstrap_pg", BOOTSTRAP_PATH)
pf = _load("_p10i_probe_pg_find", PROBE_PATH)       # <- raises
```

`_load` is `importlib.util.spec_from_file_location` followed by
`spec.loader.exec_module(mod)`. A missing file therefore raises during **collection**,
and pytest's response to a collection error is to discard the whole session
(`Interrupted`). One absent tooling script made all ~14.4k tests in the repository
unrunnable.

### 2.2 How many blockers of this class exist

Because pytest stops at the first collection error, a second blocker of the same shape
would have stayed invisible. `staging/probe_p11b_w1b_import_time_paths.py` enumerates
them without importing anything: it AST-parses all 506 files under `tests/`, resolves
module-level path constants (`REPO / "staging" / "x.py"`), finds every
`spec_from_file_location` / `_load` / `read_text` / `open` call, and stats the target.

Result (`staging/p11b_w1b_import_paths.txt`):

```
test files scanned                      : 506
unparsable                              : 0

COLLECTION-FATAL missing paths (3)
   tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py:88   _load()
       -> staging/probe_pg_find.py   MISSING
   tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py:336  read_text()
       -> staging/probe_pg_find.py   MISSING
   tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py:484  _load()
       -> staging/probe_pg_find.py   MISSING
```

**One file, one missing dependency.** No other test in the repository loads a missing
path at import time. In particular `tests/test_p10h_bootstrap_pg_selection_is_explicit.py`
and the new `tests/test_p10j_probe_pg_ready_uses_shared_classification.py` both load
targets that exist.

### 2.3 There is nothing to check out

| Check | Result |
| --- | --- |
| `staging/probe_pg_find.py` on disk | **absent** |
| `git log --all --oneline -- staging/probe_pg_find.py` | **empty** |
| `git ls-files -- staging/probe_pg_find.py` | **empty** |
| Name present in any tree in `git rev-list --all` | **no** (exhaustive `ls-tree` sweep) |
| `git stash list` | empty |
| Copy anywhere else in the workspace | **none** |

The file was **never committed**. "Restore" is not available as an operation: there is
no revision, no stash and no backup to recover it from. Any version of this file would
have to be newly authored.

### 2.4 A second file is missing, referenced by the same suite

`staging/pg_keepalive_r1.py` is **also absent**, also never committed, and also
referenced by the same test file:

```python
KEEPALIVE_PATH = REPO / "staging" / "pg_keepalive_r1.py"

@pytest.mark.parametrize("path", [PROBE_PATH, KEEPALIVE_PATH])
def test_every_remaining_dsn_writer_routes_through_the_shared_helper(path): ...

@pytest.mark.parametrize("path", [PROBE_PATH, KEEPALIVE_PATH])
def test_no_other_module_writes_the_dsn_file_directly(path): ...
```

Its absence is **not** collection-fatal — the read happens inside test bodies — so it
would surface as 3 test failures rather than an abort. But it means restoring
`probe_pg_find.py` alone would **not** make this suite pass. Two never-committed
modules would have to be authored, not one.

`staging/probe_p10i_writer_inventory.py:47` depends on both as well:

```python
MUST_NOT_WRITE = ("staging/probe_pg_find.py", "staging/pg_keepalive_r1.py")
```

That probe is therefore also broken. It is a probe, not a test, so it does not affect
collection.

### 2.5 The loss is targeted, recent, and recoverable as evidence

Every `.pyc` in `staging/__pycache__/` was checked against its source. **Exactly two**
have surviving bytecode and no source — precisely the two referenced by the broken
suite:

```
pg_keepalive_r1.py     pyc=4848 bytes   compiled 12:19:26
probe_pg_find.py       pyc=12600 bytes  compiled 13:08:08
```

Every other Workstream H / I / J era file survives (`start_pg.ps1`,
`probe_p10i_baseline.py`, `probe_p10h_validation_against_live.py`, `run_pg_lane.py`,
`run_pg_suite.py`, `bootstrap_pg.py`, `dsn.py`, `probe_pg_ready.py`). This was not a
broad cleanup. The two lost files are exactly the two DSN **writers** that Workstream I
set out to refactor, which is consistent with an interrupted in-place rewrite of that
pair.

`staging/probe_p11b_w1b_pyc_recovery.py` decodes both `.pyc` files
(output: `staging/p11b_w1b_pyc_recovery.txt`). From the PEP 552 headers:

| Module | Source size | Source mtime | Bytecode compiled |
| --- | --- | --- | --- |
| `staging/probe_pg_find.py` | **10,716 bytes** | **2026-10-10 12:57:29** | 13:08:08 |
| `staging/pg_keepalive_r1.py` | **4,198 bytes** | **2026-10-10 12:18:55** | 12:19:26 |

Both existed **today**, hours before W1B began, and were deleted by a filesystem action
(not a git operation — see §1). Recovered from the marshalled code objects:

- the full module docstrings verbatim (2,775 and 1,644 chars);
- all 7 module-level functions of `probe_pg_find` with exact parameter names —
  `_load_bootstrap()`, `_dsn_for_port(port)`, `_port_from_pid_file(data_dir)`,
  `candidate_dsns(data_dir, dsn_file, extra_ports, historical_ports)`,
  `find_and_record(intended_dir_name, extra_ports, dry_run, expect_provisioned, dsn_file, staging_dir, bootstrap, probe, env)`,
  `_build_parser()`, `main(argv)`;
- constants `DEFAULT_INTENDED_DIR = 'pgdata_r1'` and
  `HISTORICAL_PORTS = (64780, 60076, 56595, 51805, 49323)`;
- all 71 single-line string literals, including the refusal and report text.

This is a primary record of the modules that really ran on this machine. It is recorded
here so that **whoever owns Workstream I can restore both files properly**, and so that
any restoration can be checked against the real code objects rather than against the
test that demands them.

---

## 3. Decision: guard the import, do not author the missing modules

**Decision: the suite is suspended by a documented module-level skip. The two missing
modules were NOT authored.**

The brief's preferred resolution is to restore or correct the dependency "only if the
existing codebase establishes its intended behavior and ownership". Behaviour and
ownership *are* well established — Phase 10 Workstream I, a 23 KB test file asserting 23
properties, and the recovered docstrings. So the preference was taken seriously and is
rejected on four specific grounds, not on convenience:

1. **There is nothing to restore; only to author.** §2.3. Recovering source from
   bytecode is reconstruction. Comments and formatting are unrecoverable, and a `.pyc`
   is a build artifact, not the codebase.

2. **It is not a minimum correction.** The two files total **14,914 bytes** of source.
   Authoring that is a Workstream I deliverable, not a test-collection fix inside a
   verification-lane preparation task.

3. **The only available verifier is the test that demands it.** With no committed
   reference, a reconstruction could be checked only against
   `test_p10i_probe_pg_find_uses_shared_dsn_contract.py`. Code written until that file
   goes green is not evidence that the DSN safety contract holds; it is evidence that
   the code was shaped to the assertions. The brief forbids chasing a green result, and
   this is the same failure wearing different clothes.

4. **Decisive: both files write the shared DSN, and one starts a database.**
   `pg_keepalive_r1.py` calls `pgserver.get_server(...)` and then records
   `staging/pg_dsn.txt`; `probe_pg_find.py` records the DSN after probing. W1B is
   explicitly forbidden from altering `staging/pgdata_r1`, its DSN, the migration state
   or database contents. Introducing newly-authored, server-starting, DSN-writing
   scripts into this tree would create exactly the class of incident Workstream H exists
   to prevent — a wrong cluster silently recorded as "the" instance — in exchange for
   recovering regression coverage on *tooling*. That trade is not worth taking, and not
   W1B's to take.

### 3.1 Mechanism, and why not `--ignore`

The obvious alternative was `--ignore=tests/test_p10i_...py` on the lane command only.
That was rejected because it does not actually fix the blocker: every *other*
invocation — by another agent session, by a developer, by
`01-pr-check.yml`'s `unit-tests` job — would still abort at collection. It routes W1B
around the problem and leaves the repository broken for everyone else.

The guard chosen instead is conditional, self-healing, and loud:

- it fires **only** while `staging/probe_pg_find.py` is absent;
- when the file returns, the full suite runs again **with no further edit**;
- it is reported by pytest with a reason naming both missing files, stating in capitals
  that it is not a pass, and citing this document and the recovery evidence;
- **not one assertion was added, removed, weakened or re-worded.** All 23 test
  functions are byte-identical to what Workstream I wrote.

### 3.2 The exact change

One file edited: `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py`
(untracked, `??`). 25,661 → 25,739 bytes. One insertion of 42 lines immediately above
the two by-path loads; **no other line in the file changed**. The inserted region is
pure ASCII (verified byte-wise; the file's pre-existing U+2014 and box-drawing
characters were left alone).

```python
def _missing_under_test():
    """The modules this suite loads BY PATH, and whether they are on disk right now.

    PHASE 11B / WORKSTREAM 1B -- collection-blocker guard. See
    `staging/PHASE11B_W1B_BASELINE.md`.

    `pf = _load(..., PROBE_PATH)` below runs at IMPORT time, so when
    `staging/probe_pg_find.py` is absent the `FileNotFoundError` is raised during
    collection, not during a test. pytest reports that as an ERROR and
    `Interrupted: N error(s) during collection` -- it discards the whole session, so
    every other test in this repository (14,440 at the time of writing) becomes
    unrunnable because of one missing file. One absent tooling script must not be
    able to take the suite down.

    This turns that into a module-level SKIP that names what is missing. Nothing else
    changes: every assertion in this file is untouched, and the moment
    `staging/probe_pg_find.py` is back the suite runs in full again with no edit here.

    A skip is NOT a pass. The 23 test functions below (27 node IDs) are the only
    regression evidence for the Workstream I DSN-writer unification, and while this
    guard is active that evidence does not exist.
    `staging/PHASE11B_W1B_BASELINE.md` records the lost coverage test by test, and
    `staging/p11b_w1b_pyc_recovery.txt` carries the surviving bytecode evidence of
    what both missing files contained, for whoever restores them.
    """
    return [p for p in (PROBE_PATH, KEEPALIVE_PATH) if not p.exists()]


_MISSING = _missing_under_test()
if PROBE_PATH in _MISSING:
    pytest.skip(
        "W1B collection-blocker guard: "
        + ", ".join(f"staging/{p.name}" for p in _MISSING)
        + (" are" if len(_MISSING) > 1 else " is")
        + " absent from the working tree and never committed "
        "(`git log --all` is empty), so there is no revision to check out. "
        "This suite loads probe_pg_find.py by path at import time, which aborted "
        "collection for the entire repository. Skipping this module instead. "
        "THIS IS NOT A PASS: "
        "the Workstream I DSN-writer unification has no regression evidence while "
        "this guard is active. Recovery evidence: staging/p11b_w1b_pyc_recovery.txt. "
        "Details: staging/PHASE11B_W1B_BASELINE.md",
        allow_module_level=True,
    )

bp = _load("_p10i_bootstrap_pg", BOOTSTRAP_PATH)
pf = _load("_p10i_probe_pg_find", PROBE_PATH)
```

**What was deliberately NOT touched:** `pytest.ini` (shared; an `addopts` exclusion
would hide the file silently and change CI), `tests/conftest.py` (shared, and currently
` M` from another session), `staging/bootstrap_pg.py`, `staging/dsn.py`,
`staging/pg_dsn.txt`, and every assertion in the guarded file.

---

## 4. Verification of the correction

All commands run from the repository root with the workspace interpreter
(Python 3.12.10, pytest 9.1.1, `configfile: pytest.ini`).

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `python -m pytest --collect-only -q` | **0** | `14440 tests collected in 66.65s`, **no errors, no `Interrupted`** |
| 2 | `python -m pytest --collect-only -q --ignore=tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` | 0 | `14440 tests collected` — identical to run 1 |
| 3 | `python -m pytest --collect-only -q tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` | 5 | `collected 0 items / 1 skipped` |
| 4 | `python -m pytest tests/test_p10i_... tests/test_p10h_... -ra -q` | 0 | `31 passed, 1 skipped in 2.37s` |
| 5 | `python -m pytest --collect-only -q` (re-run at close of work) | **0** | `14467 tests collected in 165.48s`, no errors |
| 6 | `python -m pytest tests/test_p10i_... tests/test_p10h_... -ra -q` (re-run) | 0 | `31 passed, 1 skipped in 10.92s` |

**Before → after**

| | Before | After |
| --- | --- | --- |
| Collection outcome | `Interrupted: 1 error during collection` | completes normally |
| Exit code | non-zero | **0** |
| Errors | 1 | **0** |
| Tests collected | 14,461 (none from the broken file) | 14,440, then 14,467 |

The headline count read **14,461 → 14,440 → 14,467** across today's three measurements,
and **none of that movement is attributable to this change.** Run 2 proves it: with the
file excluded outright the count is *identical* to the guarded run, so the guard
contributes exactly **0** collected tests — the same contribution the collection error
made. The movement is churn from the test files other sessions edited between 12:51 and
the close of this work (§1.1); the +27 between runs 1 and 5 arrived during W1B's own
execution. Stated plainly: this change did not remove a single test from the collected
set; it removed an abort.

Any absolute count in this document is therefore a reading at a stated time, not a
property of the branch.

Run 4 is the control that matters most: `test_p10h_bootstrap_pg_selection_is_explicit.py`
— the sibling suite that covers `bootstrap_pg.write_dsn_if_safe`, i.e. the shared DSN
safety contract itself — **passes 31/31**. The guard suspended the probe's suite without
touching the helper's.

---

## 5. Suspended coverage, test by test

This is the cost of the decision, stated in full. Measured by
`staging/probe_p11b_w1b_lost_coverage.py` (output `staging/p11b_w1b_lost_coverage.txt`):

**23 test functions / 27 node IDs** no longer run. Of those, **5 functions / 7 node IDs**
assert DSN-safety properties and **4 functions / 6 node IDs** assert the writer-unification
property.

| Node IDs | Test | Property it was the evidence for |
| --- | --- | --- |
| 1 | `test_a_valid_provisioned_candidate_is_recorded` | happy path: a valid provisioned candidate IS recorded |
| 1 | `test_the_pid_file_port_is_discovered_without_starting_anything` | discovery reads `postmaster.pid` without starting a server |
| 1 | `test_a_candidate_serving_a_different_directory_is_not_recorded` | **SAFETY**: `data_directory` mismatch refused (the check the old guard lacked) |
| 1 | `test_a_sixty_table_cluster_without_markers_is_not_provisioned` | **SAFETY**: schema markers required, not a bare `>50` table count |
| 1 | `test_an_empty_candidate_cannot_replace_a_provisioned_dsn` | **SAFETY**: downgrade refusal via the shared helper |
| 1 | `test_all_candidates_unreachable_records_nothing` | **SAFETY**: all-unreachable records nothing |
| 3 | `test_the_dsn_is_preserved_byte_for_byte_on_every_refusal` | **SAFETY**: DSN preserved byte-for-byte on every refusal (sha256) |
| 1 | `test_pg_allow_dsn_downgrade_permits_the_rehearsal_case` | `PG_ALLOW_DSN_DOWNGRADE` override keeps its contract |
| 1 | `test_a_validated_endpoint_replaces_an_unreachable_one` | a live endpoint refreshes a dead one (the tool stays useful) |
| 1 | `test_the_probe_no_longer_writes_the_dsn_itself` | **UNIFICATION**: probe has no direct DSN write call site (anti-revert) |
| 2 | `test_every_remaining_dsn_writer_routes_through_the_shared_helper` | **UNIFICATION**: both remaining writers delegate to `write_dsn_if_safe` |
| 1 | `test_the_shared_helper_is_the_only_direct_writer` | **UNIFICATION**: exactly one direct DSN write call site in the tree |
| 2 | `test_no_other_module_writes_the_dsn_file_directly` | **UNIFICATION**: neither remaining writer touches the file itself (AST) |
| 1 | `test_candidate_discovery_is_ordered_and_deduplicated` | discovery ordering and de-duplication |
| 1 | `test_a_missing_pid_file_does_not_break_discovery` | a stopped cluster still yields candidates |
| 1 | `test_a_malformed_pid_file_is_ignored` | a malformed pid file is ignored |
| 1 | `test_the_cli_parses_the_documented_invocations` | CLI parses the invocations the runbook documents |
| 1 | `test_a_refused_write_maps_to_cli_exit_code_3` | bootstrap CLI maps a refused write to exit 3 (runbook §5b claim) |
| 1 | `test_the_probe_maps_a_refused_write_to_exit_code_3` | probe maps a refused write to the same exit 3 |
| 1 | `test_dry_run_never_writes` | `--dry-run` never writes |
| 1 | `test_both_modules_load_by_path_without_sys_modules_registration` | by-path load without `sys.modules` registration stays working |
| 1 | `test_the_probe_resolves_its_bootstrap_sibling_at_runtime` | the probe resolves its bootstrap sibling at runtime |
| 1 | `test_this_suite_did_not_touch_the_real_dsn_file` | **GUARD**: the suite did not disturb the live `staging/pg_dsn.txt` |

### 5.1 Resulting evidence limitation

State this wherever Workstream I's closeout is cited:

1. **The Workstream I closeout is no longer evidenced.** Workstream I's claim is that
   `probe_pg_find.py` and `pg_keepalive_r1.py` stopped writing `staging/pg_dsn.txt`
   themselves and now delegate to `bootstrap_pg.write_dsn_if_safe`. That claim cannot be
   true or false right now in the way the closeout means it — **both modules are absent**.
   The four unification tests were written as anti-revert guards asserting on module
   *source*, and there is no source to assert on. Any document stating that Workstream I
   is closed and verified is, as of this measurement, **incorrect**:
   `staging/PHASE10_WSI_POSTGRES_DSN_CLOSEOUT.md` cites a file that does not exist.
2. **What *is* still evidenced.** The shared helper
   `bootstrap_pg.write_dsn_if_safe` — the single definition of "safe to record", holding
   all four checks — is covered by `tests/test_p10h_bootstrap_pg_selection_is_explicit.py`,
   which **passes 31/31** (run 4, §4). The DSN safety *contract* is tested. What is
   untested is whether the two callers route through it, because the callers are gone.
3. **No operational risk is created by the skip itself.** The two absent modules were
   the only non-helper writers of `staging/pg_dsn.txt`. While they are absent, nothing
   can mis-record the DSN through them. The suspended coverage is latent, not live: it
   becomes a real gap the moment either file is re-authored, and at that point this guard
   automatically stops skipping and the assertions resume.
4. **`staging/probe_p10i_writer_inventory.py` is also non-functional** and must not be
   cited as having passed. It asserts over both missing files.
5. **A skip is never a pass.** These 27 node IDs must not be counted toward any
   green-suite claim in Phase 11B.

---

## 6. Findings raised

| ID | Sev | Finding |
| --- | --- | --- |
| **W1B-01** | **P0** | `staging/probe_pg_find.py` and `staging/pg_keepalive_r1.py` are absent from the working tree and were never committed. Both existed today (source mtimes 12:57:29 and 12:18:55) and were deleted by a non-git filesystem action. They are the two DSN writers Workstream I set out to refactor. Their suite (23 functions / 27 node IDs) is suspended; `staging/probe_p10i_writer_inventory.py` is non-functional. Recovery evidence in `staging/p11b_w1b_pyc_recovery.txt`. **Owner: Phase 10 Workstream I.** W1B did not author replacements — see §3. |
| **W1B-02** | **P0** | A missing by-path dependency in one test file aborted collection for the entire repository (`Interrupted`), making ~14.4k unrelated tests unrunnable. This is a structural fragility, not a one-off: `staging/` is not a package, so by-path loading at module scope is the established pattern across the P10H/I/J suites. Recommend the same conditional-skip guard shape wherever a suite loads a `staging/` path at import time. |
| **W1B-03** | **P1** | `staging/PHASE10_WSI_POSTGRES_DSN_CLOSEOUT.md` and the register entries citing it claim a verified closeout for files that do not exist. The Workstream I verdict must be reopened, not inherited. |
| **W1B-04** | **P2** | `.pyc` files in `staging/__pycache__/` are the only surviving record of two deliverables. That is an accident, not a backup. The whole P10H/I/J toolchain (`bootstrap_pg.py`, `dsn.py`, `probe_pg_ready.py`, `probe_p10*.py`, and all 7 of these suites) is **untracked**, so a second filesystem slip loses it with no recovery path. Carried forward from W1A-06 with a concrete realised loss attached. |

### 6.1 W1A findings re-checked

| W1A ID | W1B re-measurement |
| --- | --- |
| W1A-01 (P0, collection abort) | **Confirmed, then RESOLVED** — §2, §4. Cause is narrower than W1A recorded: two files, not one, and both never committed. |
| W1A-06 (P2, untracked instruments) | **Confirmed and escalated** → W1B-04. The risk W1A described as theoretical has now actually occurred. |
| A3-05 (alembic multiple heads) | Still CLOSED. Not re-opened by this work. |

---

## 7. Artifacts produced by W1B Phase 0–1

| Path | Kind | Purpose |
| --- | --- | --- |
| `staging/probe_p11b_w1b_pyc_recovery.py` | probe (read-only) | decode the two surviving `.pyc` files |
| `staging/p11b_w1b_pyc_recovery.txt` | log | recovered docstrings, signatures, constants, literals |
| `staging/probe_p11b_w1b_import_time_paths.py` | probe (read-only) | AST sweep of all 506 test files for import-time loads of missing paths |
| `staging/p11b_w1b_import_paths.txt` | log | the 3 collection-fatal references, all one file |
| `staging/probe_p11b_w1b_lost_coverage.py` | probe (read-only) | count and classify the suspended node IDs |
| `staging/p11b_w1b_lost_coverage.txt` | log | per-test suspended-coverage table |

All three probes are read-only: they parse text, stat paths and print. None imports
repository code, starts a server, contacts a database, or writes to any file other than
its own redirected log.

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
