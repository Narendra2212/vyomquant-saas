# Phase 10 — Workstream I: PostgreSQL DSN Writer Closeout

Closes the two conditions on Workstream H's acceptance: `staging/probe_pg_find.py` was a
second, weaker implementation of DSN safety, and the operational runbooks still named four
different data directories.

**Live trading was OFF throughout.** No application code was touched. No PostgreSQL process
was started, stopped, killed or restarted at any point — baseline or after. No data
directory was deleted, renamed, moved, initialised or repaired. The shared DSN was never
written: its sha256 is byte-identical before and after (§9). Nothing was committed or
pushed, and the full repository suite was not run.

---

## 1. Objective and scope

| In scope | Out of scope |
|---|---|
| Route `probe_pg_find.py` through `write_dsn_if_safe` | Starting/stopping/repairing any cluster |
| Standardise **current operational** runbooks | Rewriting historical reports or audit evidence |
| Focused regressions: directory mismatch, downgrade refusal, DSN preservation, successful refresh | The full repository suite |
| This closeout report | Migration `032`, referral settlement, exchange reconciliation startup, CCXT upgrade |

---

## 2. Baseline evidence (read-only)

### Repository

```
branch/head                     : main @ 074b772a
tracked modified/staged         : 118
untracked (filtered)            : 1201
```

Heavy concurrent work was in flight. Every file this workstream touched was already
**untracked**, so no tracked file was modified and no committed state was involved. The
tracked-modified count is **still exactly 118** at the end (§12).

### Database — measured now, not quoted from the Workstream H report

`python staging/probe_p10i_baseline.py` (starts nothing, writes nothing):

```
DSN                : postgresql://postgres:@127.0.0.1:51805/postgres
DSN sha256         : a746580d3cead2efa056e3f422fa38ef4b0d303af94bbe1f95b75c2661af3a96
DSN bytes          : 47
reachable          : True
public_tables      : 89
data_directory     : C:/aerora_quant_backend_updated_final1/staging/pgdata_r1  (server-reported)
classification     : PROVISIONED  (rank 3)
markers present    : 7/7  [profiles, execution_records, fee_records, referral_payouts,
                           referral_obligations, safety_control_audit, alembic_version]
markers missing    : none
pgdata     base_bytes=22,177,237
pgdata_r1  base_bytes=35,419,649
is the live target pgdata_r1? True
```

The `data_directory` value comes from the server via `current_setting('data_directory')`,
not inferred from the port — a reachable port does not prove which database is answering.

### `verify` confirmed non-mutating *before* being executed

Required by the brief, so it was established by reading the code rather than by running it
and observing: `verify()` calls only `dsn_file.exists()`, `read_text()`, the probe, and
`print()`. It contains no write and never calls `_server()`, so it cannot start, stop or
modify anything. Executed afterwards:

```
$ python staging/bootstrap_pg.py verify
DSN_FILE: ...\staging\pg_dsn.txt
TARGET:   PROVISIONED  public_tables=89  data_directory=.../staging/pgdata_r1
exit 0
```

---

## 3. Complete DSN-writer inventory

Searched by pattern **and** by AST, because two modules legitimately *quote* their old
unguarded write inside a docstring and a text search cannot tell that from live code.

### Writers (before this workstream)

| # | Site | Guard |
|---|---|---|
| 1 | `staging/bootstrap_pg.py:436`, inside `write_dsn_if_safe` | reachable + data-directory match + markers + downgrade refusal |
| 2 | `staging/pg_keepalive_r1.py` | delegates to `write_dsn_if_safe` (Workstream H) |
| 3 | `staging/probe_pg_find.py:33` | **`if n > 50: dsn_file.write_text(...)` — weaker, see §4** |

### Writers (after)

| # | Site | Guard |
|---|---|---|
| 1 | `staging/bootstrap_pg.py`, inside `write_dsn_if_safe` | the single write site in the tree |
| 2 | `staging/pg_keepalive_r1.py` | delegates |
| 3 | `staging/probe_pg_find.py` | **delegates** |

Asserted, not asserted-in-prose: `test_the_shared_helper_is_the_only_direct_writer` (exactly
one AST write call site in `bootstrap_pg.py`) and
`test_no_other_module_writes_the_dsn_file_directly` (zero in the other two).

### Repository-wide re-scan, by AST containment

`staging/probe_p10i_writer_inventory.py` — a repeatable scan over 1,261 Python files,
written to close this report's own "a new writer would not be caught" risk:

```
files that could not be scanned : 0

1. staging/bootstrap_pg.py write sites (must all be inside write_dsn_if_safe):
   ok  line 436: dsn_file.write_text(...)  in ['write_dsn_if_safe']
2. modules that must contain NO DSN write site:
   ok  staging/probe_pg_find.py:    0 write site(s); delegates=True
   ok  staging/pg_keepalive_r1.py:  0 write site(s); delegates=True
3. all other DSN write sites (tests/diagnostics on caller-supplied paths):
   [diagnostic] staging/probe_p10h_validation_against_live.py  (1)
   [test]       tests/test_p10h_bootstrap_pg_selection_is_explicit.py  (6)
   [test]       tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py  (7)

INVENTORY CLEAN
```

The check is **lexical containment**, not naming: the single write must sit inside
`write_dsn_if_safe`, so no path through `bootstrap_pg.py` can reach the file without the
four validation checks.

Two earlier versions of this scanner were wrong in opposite directions, recorded in the
script because they are easy mistakes to repeat. Flagging every DSN-ish `write_text`
produced 18 false offenders — the suites legitimately seed a `tmp_path` DSN file in their
fixtures. Restricting to module-level `UPPER_CASE` constants then **missed the real write
site**, because `write_dsn_if_safe` writes to its injected `dsn_file` parameter, and the
scanner reported "bootstrap_pg.py no longer writes the DSN at all" — a false clean. The
property was never about naming; it is about which paths can bypass validation.

### Readers (unchanged, non-writers — verified)

`staging/dsn.py` (the resolver), `probe_pg_ready.py`, `run_pg_lane.py`, `run_pg_suite.py`,
`apply_migration.py`, `apply_hardening.py`, `fk_typecheck.py`, `verify_schema.py`,
`probe_pg_blockers.py`, `probe_pg_activity.py`, ~20 other `staging/probe_*.py`, and the
PG-gated test files.

`staging/probe_pg_ready.py` and `staging/probe_PGR.txt` appeared during the session from a
concurrent tab and were checked specifically: `probe_pg_ready.py` **reads** the DSN and does
not write it, so the inventory above is complete as of this writing.

---

## 4. Root cause and implementation

### Root cause

`probe_pg_find.py` held a second definition of "safe to record". Its guard was real — it is
why this script never caused the Workstream H incident — but measured against the shared
helper's four checks it was missing three:

1. **No data-directory check.** It never asked `current_setting('data_directory')`, so a
   *different* provisioned cluster answering on a recycled port would be accepted as "the"
   instance and recorded. `pgserver` hands out random high ports and this repository carries
   two clusters, so that is reachable, not theoretical.
2. **No downgrade guard.** It could replace a better-provisioned target.
3. **Table count only.** "Provisioned" was a bare `> 50` with no schema markers, so a
   cluster with 60 unrelated tables qualified.

With two definitions in play, the weaker one sets the real guarantee.

### Implementation

**Discovery stays; the write decision moves.** `probe_pg_find.py` keeps candidate discovery
and hands the selected candidate to `write_dsn_if_safe`, which adjudicates it. `write_dsn_if_safe`
itself was **not modified** — its existing signature covered every requirement, so this is
the smallest compatible change and all its existing callers and tests are unaffected
(re-verified: Workstream H's 31 tests still pass).

Improvements that fell out of the unification:

- **Discovery is now authoritative rather than hardcoded.** It reads the intended cluster's
  own `postmaster.pid` (line 4 is the port), then the current DSN, then explicit `--port`
  values, then the historical list. Reading a pid file is not starting a database, so
  discovery stays separate from startup. The old hardcoded port list needed manual upkeep
  and is retained only as a fallback.
- **The intended directory is explicit** (`--data-dir`, default `pgdata_r1`). The script's
  docstring always said it looked for `pgdata_r1`; now that intent is checked against what
  the server reports.
- **Per-candidate diagnostics** for unreachable / mismatched / empty / partial /
  provisioned, including which markers are missing and a `<- serves a DIFFERENT data
  directory` annotation.
- **`--dry-run`** reports without writing; **`--any-provisioning`** relaxes the marker
  requirement while keeping the downgrade guard.
- Exit codes: `0` recorded, `1` nothing suitable, `3` the shared helper refused.
- The `> 50` threshold is not lost — it survives as `bootstrap_pg.MIN_PROVISIONED_TABLES`,
  which is where this script's rule was adopted from.

### Exercised against the live cluster, writing nothing

```
$ python staging/probe_pg_find.py --dry-run
intended data directory : ...\staging\pgdata_r1
mode                    : DRY RUN (never writes)
candidates              : 5
  port 51805  PROVISIONED  public_tables=89 data_directory=.../staging/pgdata_r1
  port 64780  UNREACHABLE  connection refused
  port 60076  UNREACHABLE  connection refused
  port 56595  UNREACHABLE  connection refused
  port 49323  UNREACHABLE  connection refused
DRY RUN: would record postgresql://postgres:@127.0.0.1:51805/postgres
  Nothing was written.
exit 0
```

Port 51805 came first because it was discovered from `pgdata_r1/postmaster.pid`, and the
duplicate (the current DSN names the same port) was de-duplicated.

---

## 5. Modified files

| File | Lines | Change |
|---|---|---|
| `staging/probe_pg_find.py` | 226 | **Rewritten.** Discovery retained; write decision delegated to `write_dsn_if_safe`; pid-file port discovery; `--data-dir`/`--port`/`--dry-run`/`--any-provisioning`; per-candidate diagnostics |
| `staging/dsn.py` | 74 | Exit instructions name the data directory explicitly and mention `probe_pg_find.py` |
| `staging/run_pg_lane.py` | 89 | "DSN missing" message now prints the explicit provisioned invocation |
| `staging/run_pg_suite.py` | 123 | same |
| `tests/test_orm_models_match_provisioned_schema.py` | 249 | docstring help text only — explicit invocation + why |
| `DATABASE_PROVISIONING_RUNBOOK.md` | 455 | §5 split into 5a (use the provisioned cluster) and 5b (rehearsal), see §6 |
| `tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py` | 375 | **New** — 27 tests |
| `staging/probe_p10i_baseline.py` | 57 | **New** — read-only baseline probe |
| `staging/probe_p10i_writer_inventory.py` | 179 | **New** — repeatable AST writer re-scan |

`staging/bootstrap_pg.py` — **unchanged** by this workstream.
**Zero tracked files modified**; every file above was already untracked.

---

## 6. Runbook changes, and what was deliberately preserved

The first finding here was that the obvious change would have been wrong. All five
root-level documents naming `pgdata_z1` / `pgdata_local` turned out to be
**fresh-provisioning rehearsal** workflows — "Local staging from nothing", "To rebuild from
scratch", "Reproducing this", each followed by `reprovision.py` or
`provision_database.py --apply --create-substrate`. Repointing them at `pgdata_r1` would
have aimed a from-empty provisioning run at the provisioned cluster. The brief's
"do not blindly replace every reference" is doing real work.

### Changed — current operational instructions

| File | Change |
|---|---|
| `DATABASE_PROVISIONING_RUNBOOK.md` §5 | Split. **5a** is the common case and standardises on `start --data-dir pgdata_r1 --expect-provisioned`, plus `probe_pg_find.py` for a port change and `verify` for a read-only check. **5b** keeps the rehearsal on its own clean `pgdata_z1`, and now documents `PG_ALLOW_DSN_DOWNGRADE=1` and a final step to point the shared DSN back. |
| `staging/dsn.py` | Its exit message is live guidance printed to users; it printed the bare `start`, which now exits 2. |
| `staging/run_pg_lane.py`, `staging/run_pg_suite.py` | Both lanes require the provisioned cluster; their "DSN missing" messages printed the bare form. |
| `tests/test_orm_models_match_provisioned_schema.py` | Docstring help text only; no assertion or logic touched. |
| `staging/start_pg.ps1` | Already explicit from Workstream H (`--data-dir pgdata_r1 --expect-provisioned`); re-verified, not changed. |

**A real gap was found and closed while doing this.** Workstream H's downgrade guard means a
from-nothing rehearsal on a machine that already has a provisioned cluster will be
**refused** when it tries to record its DSN. The runbook as written would have failed at
step 1 with no explanation. §5b now documents `PG_ALLOW_DSN_DOWNGRADE=1` as required for
that workflow, and the "exits 3" claim is verified by a test (§7) rather than asserted.

### Preserved — historical reports and completed audit evidence

Not rewritten, deliberately. Each records a command that was actually run at the time;
editing them would falsify the record.

| File | Reference | Why preserved |
|---|---|---|
| `FINAL_LAUNCH_READINESS_REPORT.md` | `pgdata_local` | completed readiness report |
| `BILLING_SECURITY_VERIFICATION.md` | `pgdata_local` | completed verification evidence |
| `RLS_TENANT_ISOLATION_VERIFICATION.md` | `pgdata_z1` | completed verification evidence |
| `PAPER_ONLY_LAUNCH_READINESS.md` | `pgdata_z1` | completed readiness report |
| `staging/PHASE10_WSD_FINANCIAL_INTEGRITY.md`, `staging/PHASE10_WSH_POSTGRES_SAFETY.md` | `pgdata_r1`, bare `start` | phase incident/closeout reports; the bare form appears as the *defect being described* |
| `staging/p10h_bare_start.txt` and other `staging/p10h_*.txt` | — | captured run output |

All four preserved documents describe rehearsal workflows, so they remain *correct* for
their purpose; they are simply not updated with the new override note. That note lives in
the runbook, which is the file people follow.

---

## 7. Focused test commands and observed results

### WS-I suite

```
python -m pytest tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py \
    -p no:randomly -q --no-header --timeout=120
```

**First run: 5 failed, 18 passed.** Reported here rather than quietly fixed, because the
failures were instructive and all three causes were defects in my tests, not the code:

1. `test_the_probe_no_longer_writes_the_dsn_itself` flagged my own docstring — the rewritten
   module *quotes* the old `dsn_file.write_text(...)` so the defect is recorded beside the
   fix, and a substring search cannot distinguish that from live code (a comment filter does
   not help: a docstring is not a comment). Fixed by detecting write **call sites via AST**,
   which documentation cannot trip.
2. The three `..._preserved_byte_for_byte` cases seeded the live port in the fake prober, so
   discovery legitimately found and recorded it — the test was measuring a success path.
   Fixed by making the recorded DSN unreachable and absent from the mapping, so the bad
   candidate is the only reachable one.
3. `test_pg_allow_dsn_downgrade_permits_the_rehearsal_case` hit
   `TypeError: _run() got multiple values for argument 'env'` — my helper's fixture
   parameter was named `env`, colliding with `find_and_record`'s process-environment `env`.
   Renamed to `ctx`.

**Final run: 27 passed.**

| passed | failed | skipped | xfailed | collection errors |
|---|---|---|---|---|
| 27 | 0 | 0 | 0 | 0 |

### Both DSN suites together (Workstream H regression)

```
python -m pytest tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py \
                 tests/test_p10h_bootstrap_pg_selection_is_explicit.py \
    -p no:randomly -q --no-header --timeout=120
=> 58 passed in 5.30s
```

| passed | failed | skipped | xfailed | collection errors |
|---|---|---|---|---|
| 58 (27 + 31) | 0 | 0 | 0 | 0 |

Workstream H's 31 tests pass unchanged, which is the evidence that `write_dsn_if_safe` was
not altered and its other callers are unaffected.

### Required scenario coverage

| # | Scenario | Test |
|---|---|---|
| 1 | Valid provisioned candidate accepted | `test_a_valid_provisioned_candidate_is_recorded` |
| 2 | Requested vs server-reported directory mismatch rejected | `test_a_candidate_serving_a_different_directory_is_not_recorded`, `test_a_sixty_table_cluster_without_markers_is_not_provisioned` |
| 3 | Empty/less-provisioned cannot replace provisioned | `test_an_empty_candidate_cannot_replace_a_provisioned_dsn` |
| 4 | Unreachable candidate rejected | `test_all_candidates_unreachable_records_nothing` |
| 5 | Validation failure preserves DSN byte-for-byte | `test_the_dsn_is_preserved_byte_for_byte_on_every_refusal[unreachable / wrong-directory / partial-schema]` (sha256 over raw bytes, trailing newline included) |
| 6 | Explicit downgrade override | `test_pg_allow_dsn_downgrade_permits_the_rehearsal_case`, `test_a_refused_write_maps_to_cli_exit_code_3`, `test_the_probe_maps_a_refused_write_to_exit_code_3` |
| 7 | Validated endpoint refreshes a dead one | `test_a_validated_endpoint_replaces_an_unreachable_one` |
| 8 | Both scripts use one contract | `test_the_probe_no_longer_writes_the_dsn_itself`, `test_every_remaining_dsn_writer_routes_through_the_shared_helper`, `test_the_shared_helper_is_the_only_direct_writer`, `test_no_other_module_writes_the_dsn_file_directly` |
| 9 | Discovery and CLI still functional | `test_the_pid_file_port_is_discovered_without_starting_anything`, `test_candidate_discovery_is_ordered_and_deduplicated`, `test_a_missing_pid_file_does_not_break_discovery`, `test_a_malformed_pid_file_is_ignored`, `test_the_cli_parses_the_documented_invocations`, `test_dry_run_never_writes` |
| 10 | By-path loading stays compatible | `test_both_modules_load_by_path_without_sys_modules_registration`, `test_the_probe_resolves_its_bootstrap_sibling_at_runtime` |

Hermetic: `tmp_path` data directories with a fake `PG_VERSION`, `tmp_path` DSN files, an
injected fake prober. No PostgreSQL process started, stopped or contacted; the real
`staging/pg_dsn.txt` never opened for writing, with a closing guard test over it.

### Database-backed check (target independently verified first)

Target re-verified via the non-mutating `verify` before running, and **PostgreSQL was not
restarted to make anything pass**.

```
$ python -m pytest tests/test_orm_models_match_provisioned_schema.py \
      -p no:randomly -q --no-header -rsx --timeout=240
=> 4 passed, 1 xfailed in 7.57s
```

| passed | failed | skipped | xfailed | collection errors |
|---|---|---|---|---|
| 4 | 0 | 0 | 1 | 0 |

The xfail is pre-existing and documented (`backend/state_service.py` declares a second class
on `__tablename__ = "positions"`; `strict=True` so resolving it turns the marker red). Run
because this file's docstring was edited; its assertions were untouched.

### Repository-wide writer re-scan

```
$ python staging/probe_p10i_writer_inventory.py
=> INVENTORY CLEAN   (exit 0; 1,261 files scanned, 0 unscannable)
```

Full output in §3.

Smoke checks: `py_compile` clean on all five edited Python files;
`staging/dsn.py::resolve_dsn` still resolves and announces its source.

### Not executed — stated explicitly

- The **full repository suite** — prohibited.
- The **real write path** of `probe_pg_find.py` against the live cluster. Only `--dry-run`
  was run, so the live DSN was never a write target. The write path is covered by the 27
  fakes-based tests and by Workstream H's live-validation probe.
- **Starting a `pgdata_z1` cluster** to confirm §5b end to end. That would have initialised a
  third data directory; the `exit 3` claim is instead verified by
  `test_a_refused_write_maps_to_cli_exit_code_3`.
- `staging/run_pg_lane.py` / `run_pg_suite.py` were **not executed** — they run large PG
  lanes. Only their edited message strings were compiled and reviewed.
- `staging/pg_keepalive_r1.py` was **not executed** (it starts a server); its delegation was
  confirmed statically by AST assertions.

---

## 8. Aggregate counts

| Suite | passed | failed | skipped | xfailed | errors |
|---|---|---|---|---|---|
| `test_p10i_probe_pg_find_uses_shared_dsn_contract.py` | 27 | 0 | 0 | 0 | 0 |
| `test_p10h_bootstrap_pg_selection_is_explicit.py` | 31 | 0 | 0 | 0 | 0 |
| `test_orm_models_match_provisioned_schema.py` | 4 | 0 | 0 | 1 | 0 |
| **Total executed** | **62** | **0** | **0** | **1** | **0** |

---

## 9. DSN checksum and target, before and after

| | Before | After |
|---|---|---|
| DSN | `postgresql://postgres:@127.0.0.1:51805/postgres` | identical |
| sha256 | `a746580d3cead2efa056e3f422fa38ef4b0d303af94bbe1f95b75c2661af3a96` | `a746580d3cead2efa056e3f422fa38ef4b0d303af94bbe1f95b75c2661af3a96` |
| bytes | 47 | 47 |
| server-reported `data_directory` | `.../staging/pgdata_r1` | `.../staging/pgdata_r1` |
| classification | PROVISIONED, 89 tables, 7/7 markers | PROVISIONED, 89 tables, 7/7 markers |

Verified without restarting PostgreSQL: postmaster **PID 12924**, start time
`10/10/2026 11:31:08`, serving `staging/pgdata_r1` — the same process at baseline and at
close.

---

## 10. Database-directory safety findings

| Check | Result |
|---|---|
| `staging/pgdata` present | **Yes** — `base/` 22,177,237 bytes, identical to baseline |
| `staging/pgdata_r1` present | **Yes** — `base/` 35,419,649 bytes, identical to baseline |
| Deleted / renamed / moved | **No** |
| `initdb` or repair run | **No** |
| PostgreSQL started / stopped / killed / restarted | **No**, at any stage |
| Third data directory created | **No** — specifically avoided when verifying §5b |
| Shared DSN written | **No** — sha256 identical (§9) |
| Stale-pid files touched | **No** |

---

## 11. Remaining risks and unverified behaviour

1. **The live write path of `probe_pg_find.py` has not been exercised against a real
   cluster.** Only `--dry-run`. The decision logic is the shared helper's, which Workstream
   H did exercise live, but the probe's own success branch (`write_dsn_if_safe` returning,
   then printing `RECORDED`) has only been run against fakes.
2. **§5b of the runbook is verified only in part.** The `exit 3` refusal is covered by a
   test; the full from-nothing rehearsal with `PG_ALLOW_DSN_DOWNGRADE=1` was not run end to
   end, because that would create a third cluster.
3. **`staging/pgdata` still exists as a dormant empty cluster** (preservation was mandated).
   Its presence is what makes `bootstrap_pg`'s bare `start` fail closed, so the refusal is
   the normal path on this machine. If it is ever deleted, the single-cluster case makes
   inference silent again, and the explicit `--data-dir` in `start_pg.ps1` becomes the only
   thing keeping selection unambiguous.
4. **Four preserved reports still name `pgdata_z1` / `pgdata_local` without the
   `PG_ALLOW_DSN_DOWNGRADE=1` note.** A reader who follows one of them verbatim on this
   machine will hit an `exit 3` refusal they cannot explain from that document. Deliberate —
   they are completed evidence — but the mitigation is only that the refusal message itself
   names the override.
5. **`probe_pg_ready.py` reads the DSN without classifying it.** It checks a hand-written
   table list rather than the shared markers, so it can report `PG_READY=True` against a
   cluster the shared contract would call `PARTIAL`. Not in scope; it is a reader, so it
   cannot repoint anything.
6. **The writer inventory is a point-in-time scan, but it is now repeatable.**
   `staging/probe_p10i_writer_inventory.py` re-scans 1,261 files by AST containment and
   exits non-zero on any unsanctioned write, any write in `bootstrap_pg.py` outside
   `write_dsn_if_safe`, any write reappearing in the two delegating modules, or any file it
   could not parse. It is **not wired into CI**, so it only protects what it is pointed at;
   a new writer added by a concurrent tab is caught the next time someone runs it. Two
   DSN-touching files did appear mid-session from another tab (`probe_pg_ready.py`,
   `probe_PGR.txt`) and the re-scan confirms neither writes the DSN.
7. **`pgserver`'s hardcoded 10-second `pg_ctl` timeout is untouched**, so a cluster needing a
   long recovery still cannot be started by `bootstrap_pg.py`; `pg_keepalive_r1.py` remains
   the route for that.
8. **Validation is point-in-time.** `verify` and the write-time probe report what was true
   when they ran.

---

## 12. Final Git status

```
branch/head              : main @ 074b772a   (unchanged)
tracked modified/staged  : 118               (identical to baseline — zero tracked files touched)
```

Files changed by this workstream, all previously untracked:

```
staging/probe_pg_find.py                                   (rewritten)
staging/dsn.py                                             (exit message)
staging/run_pg_lane.py                                     (error message)
staging/run_pg_suite.py                                    (error message)
tests/test_orm_models_match_provisioned_schema.py          (docstring only)
DATABASE_PROVISIONING_RUNBOOK.md                           (section 5 split)
tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py  (new)
staging/probe_p10i_baseline.py                             (new)
staging/p10i_*.txt                                         (new, run evidence)
```

No commit, no push, no reset, no clean, no revert. Concurrent modifications across
`backend_app/**`, `tests/conftest.py`, `algo22-terminal/**` and
`staging/.ccxt_upgrade_probe/**` are untouched.

---

## 13. Acceptance recommendation

**Recommend acceptance.**

Against the definition of done:

| Requirement | Status |
|---|---|
| Probe uses the shared DSN safety contract | **Met** — sole write site is `write_dsn_if_safe`, enforced by AST assertions so a revert fails a test |
| Focused regressions pass | **Met** — 27 WS-I + 31 WS-H = 58 passed, 0 failed |
| Current operational runbooks consistent | **Met** — runbook, `dsn.py`, both lane runners, the schema test's help text, and `start_pg.ps1` all name the directory explicitly; rehearsal workflows preserved with the override documented |
| Shared DSN preserved | **Met** — sha256 byte-identical |
| Both data directories preserved | **Met** — both present, `base/` sizes identical |
| Concurrent changes preserved | **Met** — zero tracked files modified; count still 118 |

No open blocker. The residual items in §11 are either explicitly mandated (preserving
`pgdata` and the historical reports) or scoped out, and none weakens the DSN safety
contract. The two most material gaps to close next, neither safety-critical: exercising the
probe's live write path, and bringing `probe_pg_ready.py` onto the shared classification.
