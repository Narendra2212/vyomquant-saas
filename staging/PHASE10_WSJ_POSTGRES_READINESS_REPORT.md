# Phase 10 — Workstream J: PostgreSQL Readiness Probe Consistency

Closes the last inconsistency Workstream I recorded in the PostgreSQL tooling:
`staging/probe_pg_ready.py` judged readiness by its own hand-maintained table list instead
of the shared classification.

**Live trading was OFF throughout.** No production database was contacted. No PostgreSQL
cluster was started, stopped, restarted, initialised, repaired, deleted or renamed. The
shared DSN was never written — sha256 identical before and after (§5). No migration was
applied and no application code was touched. Nothing was committed, pushed, reset, stashed,
cleaned or reverted. No historical report and no shared launch registry or scorecard was
modified.

---

## 1. The original inconsistency

Workstream I reported that this probe "reads the DSN without classifying it [...] so it can
report `PG_READY=True` against a cluster the shared contract would call PARTIAL". Reading
the current code confirmed that and found three more problems. **The first is worse than
what was reported.**

### 1a. `PG_READY=True` was printed unconditionally

```python
for schema, table in WANTED:
    cur.execute(...)
    present = cur.fetchone()[0] > 0
    print(f"TABLE {schema}.{table}={'PRESENT' if present else 'ABSENT'}")

cur.execute("SELECT count(*) FROM paper_order_allowed_transitions")
print(f"ALLOWED_TRANSITION_ROWS={cur.fetchone()[0]}")
...
print("PG_READY=True")
```

`present` was computed, printed, and then **discarded**. No branch gated the final line. So
a cluster missing ten of the eleven relations still reported ready, provided
`paper_order_allowed_transitions` happened to exist — because that one table was queried
directly afterwards, and an exception was the only thing that could stop the script.
**"Ready" meant "the process did not crash."**

This is a structural property of the control flow, established by reading the code. It was
not reproduced against a deliberately PARTIAL cluster, because doing so would have required
creating or degrading a database, which the brief forbids. The replacement behaviour is
pinned by the tests in §4 instead.

### 1b. A second definition of readiness

The hand-maintained list was disjoint from `bootstrap_pg.SCHEMA_MARKERS`, so "provisioned"
meant one thing in `bootstrap_pg.py` and `probe_pg_find.py` and another thing here — the
exact condition Workstreams H and I exist to remove. When a safety property has two
definitions, the weaker one is the real one.

### 1c. An unreachable server produced a traceback, not a verdict

`psycopg2.connect(dsn, connect_timeout=10)` was unguarded at module level. "The database is
down" — the single most likely reason to run a readiness probe — exited with a stack trace
instead of a readable answer.

### 1d. It bypassed the shared DSN resolver

It read `staging/pg_dsn.txt` directly, so `AERORA_TEST_DATABASE_URL` did **not** win, even
though that variable is what `tests/conftest.py` requires before a test process may talk to
a real server and what `staging/dsn.py` documents as taking precedence. The probe and the
suite it gated could therefore examine different databases — which is precisely the class of
error `dsn.py`'s own docstring calls "the worst possible output of this tooling".

### Side-effect audit (Stage 1)

The probe was confirmed read-only before and after: `SELECT` only, no writes, no server
lifecycle. Verified by AST in §4 rather than by inspection alone.

---

## 2. The chosen shared contract

Verified from current code, not from report line numbers.

`staging/bootstrap_pg.py`:

```python
SCHEMA_MARKERS = ("profiles", "execution_records", "fee_records", "referral_payouts",
                  "referral_obligations", "safety_control_audit", "alembic_version")
MIN_PROVISIONED_TABLES = 50
_RANK = {"UNREACHABLE": 0, "EMPTY": 1, "PARTIAL": 2, "PROVISIONED": 3}

@property
def classification(self):
    if not self.reachable:                                      return "UNREACHABLE"
    if self.public_tables == 0:                                  return "EMPTY"
    if self.missing_markers or self.public_tables < MIN_PROVISIONED_TABLES:
                                                                 return "PARTIAL"
    return "PROVISIONED"
```

`_default_probe(dsn, timeout=10)` is the non-mutating classification path and was the
natural thing to reuse: it issues only
`SELECT count(*) FROM information_schema.tables`, `SELECT current_setting('data_directory')`
and `SELECT to_regclass(...)` per marker; it **never raises** (an unreachable target is a
`TargetState`, not an exception); and it closes its connection in a `finally`. It starts
nothing and writes nothing.

Note that **directory identity is not part of the classification** — `data_directory` is
captured but `classification` does not consult it. In the shared design the directory check
lives in the *write* contract (`write_dsn_if_safe` via `_same_directory`). For a readiness
probe it is therefore made **opt-in** via `--data-dir`, matching the brief's conditional
phrasing ("when directory identity is part of the contract").

### The change, and the one thing deliberately not done

- The DSN now comes from `staging/dsn.py::resolve_dsn()` — the authoritative resolver,
  which also announces which source won.
- Classification comes from `bootstrap_pg._default_probe` + `TargetState.classification`.
  **No marker list and no table threshold is redefined**; asserted at source level by
  `test_the_shared_classification_is_reused_not_redefined`.
- `PG_READY=True` now requires the shared classification to be `PROVISIONED`, and the
  verdict is derived from the measurements rather than from reaching the last line.
- The lane query is guarded, so an absent transition table is reported instead of fatal.
- `evaluate(state, lane_present, intended_data_dir, same_directory)` is a pure function of
  the measurements, so every classification case is testable without a database.

**Not done: replacing the hand-written list with `SCHEMA_MARKERS`.** That was the obvious
change and it would have **lost coverage**. `SCHEMA_MARKERS` describes the provisioned test
database in general and contains **no paper-trading table**; the eleven relations this probe
checks are what the paper-trading lane needs, which is the reason the probe exists. So they
are retained as an **additional requirement layered on top** of the shared classification,
not as a competing definition of "provisioned". `PG_READY=True` needs both gates, and each
refusal is reported separately so it is always clear which one objected. Pinned by
`test_the_lane_relations_are_additional_not_a_replacement`, which also fails if
`SCHEMA_MARKERS` ever grows a `paper_*` table and makes the layering argument stale.

No second readiness contract was introduced, so Stage 2's stop-condition was not reached.

---

## 3. Files changed

| File | Change |
|---|---|
| `staging/probe_pg_ready.py` | **Rewritten** (was 56 lines, now 252). Shared resolver + shared classification; `evaluate()` extracted as a pure function; guarded lane query; optional `--data-dir`; exit 0/1 |
| `tests/test_p10j_probe_pg_ready_uses_shared_classification.py` | **New** — 20 tests |

Both files were already **untracked**; **zero tracked files were modified** (§5).

### Behavioural diff

| | Before | After |
|---|---|---|
| DSN source | `pg_dsn.txt` read directly | `dsn.py::resolve_dsn()` (env var wins, source announced) |
| Readiness rule | unconditional `PG_READY=True` | `classification == PROVISIONED` **and** all lane relations present |
| UNREACHABLE | traceback | `CLASSIFICATION=UNREACHABLE`, `ERROR=…`, `PG_READY=False`, exit 1 |
| EMPTY / PARTIAL | reported ready | `PG_READY=False`, exit 1, with `NOT_READY=` reasons |
| Absent transition table | `UndefinedTable`, script died | `ALLOWED_TRANSITION_ROWS=n/a` + `ABSENT` line |
| Directory identity | not checked | opt-in `--data-dir` |
| Exit code | 0 unless an exception escaped | 0 ready, 1 not ready |

### Output compatibility

Every key in the previously captured run (`staging/probe_PGR.txt`) is still emitted with the
same spelling: `DSN_PORT=`, `SERVER=`, eleven `TABLE <schema>.<table>=PRESENT|ABSENT`,
`ALLOWED_TRANSITION_ROWS=`, `PG_READY=`. Pinned by
`test_the_recorded_output_keys_are_preserved`.

**Additions** (documented, as Stage 3 item 8 requires): `CLASSIFICATION=`,
`PUBLIC_TABLES=`, `DATA_DIRECTORY=`, `MISSING_MARKERS=`, `ERROR=` (unreachable only),
`LANE_ERROR=` (lane query failed), `REQUESTED_DATA_DIRECTORY=` (with `--data-dir`), and one
`NOT_READY=<reason>` line per refusal.

**One intentional change:** `PG_READY` can now be `False`, and the exit code is then `1`.
Previously the line was always `True` and the exit code always `0` on completion. Any
consumer treating a zero exit as "ready" keeps working; a consumer that ignored the exit
code and grepped for `PG_READY=True` also keeps working. No automated consumer was found —
the probe is invoked by a wrapper that captures stdout and `EXITCODE` to
`staging/probe_PGR.txt`.

---

## 4. Focused test results

```
python -m pytest tests/test_p10j_probe_pg_ready_uses_shared_classification.py \
    -p no:randomly -q --no-header --timeout=120
```

**First run: 1 failed, 19 passed.** Reported rather than quietly fixed. The failure was my
own test's false positive: `test_only_select_statements_are_issued` scanned *every* string
constant for SQL keywords and flagged the module docstring, which contains the sentence
"delete the hand-written list" — prose, not SQL. Rewritten to inspect only strings passed to
`cur.execute(...)`, i.e. AST position rather than raw text. That is the third time in this
phase a text-matching guard has tripped over documentation describing the defect it forbids;
position in the AST is the discriminator every time.

**Final run: 20 passed.**

| passed | failed | skipped | xfailed | collection errors |
|---|---|---|---|---|
| 20 | 0 | 0 | 0 | 0 |

### Affected regressions

```
python -m pytest tests/test_p10j_probe_pg_ready_uses_shared_classification.py \
                 tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py \
                 tests/test_p10h_bootstrap_pg_selection_is_explicit.py \
    -p no:randomly -q --no-header --timeout=120
=> 78 passed in 18.17s
```

| suite | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| WS-J readiness | 20 | 0 | 0 | 0 |
| WS-I DSN contract | 27 | 0 | 0 | 0 |
| WS-H selection | 31 | 0 | 0 | 0 |
| **total** | **78** | **0** | **0** | **0** |

`bootstrap_pg.py` was not modified, which is why WS-H and WS-I pass unchanged.

### Required scenario coverage

| # | Scenario | Test |
|---|---|---|
| 1 | Provisioned + markers classified correctly | `test_a_provisioned_target_with_all_lane_relations_is_ready` |
| 2 | Empty not ready | `test_an_empty_target_is_not_ready` |
| 3 | Partial not ready | `test_a_partial_target_is_not_ready`, `test_a_target_below_the_table_threshold_is_not_ready` |
| 4 | Unreachable not ready | `test_an_unreachable_target_is_not_ready_and_does_not_raise` |
| 5 | Wrong data directory rejected | `test_a_server_reporting_another_data_directory_is_rejected`, `test_the_matching_data_directory_is_accepted`, `test_the_directory_check_is_opt_in` |
| 6 | No DSN write | `test_the_probe_contains_no_dsn_write_call_site`, `test_this_suite_did_not_touch_the_real_dsn_file` |
| 7 | No server start/stop | `test_the_probe_never_starts_or_stops_a_server`, `test_every_statement_the_probe_executes_is_a_select` |
| 8 | Output / exit-code compatibility | `test_the_recorded_output_keys_are_preserved`, `test_main_returns_zero_only_when_ready` |
| — | No redefined contract | `test_the_shared_classification_is_reused_not_redefined`, `test_the_lane_relations_are_additional_not_a_replacement`, `test_the_dsn_comes_from_the_shared_resolver` |
| — | Lane gate is real | `test_a_provisioned_target_missing_a_lane_relation_is_not_ready`, `test_unchecked_lane_relations_are_not_treated_as_present` |

Hermetic: `evaluate()` is pure, so classifications are exercised with fabricated
`TargetState`s; `main()` is driven with the database layer faked out entirely. No database
contacted, no cluster created, no `tmp_path` DSN promoted to the real one.

### Live read-only execution (actually executed)

The probe is read-only by construction — `test_every_statement_the_probe_executes_is_a_select`
asserts it — so it was run against the live local cluster. **This is a local staging cluster,
not production.**

```
$ python staging/probe_pg_ready.py
[dsn] AERORA_TEST_DATABASE_URL -> postgresql://127.0.0.1:51805/postgres
DSN_PORT=51805
SERVER=PostgreSQL 16.2 on x86_64-pc-mingw64
CLASSIFICATION=PROVISIONED
PUBLIC_TABLES=89
DATA_DIRECTORY=C:/aerora_quant_backend_updated_final1/staging/pgdata_r1
MISSING_MARKERS=none
TABLE public.paper_accounts=PRESENT        ... (11 relations, all PRESENT)
ALLOWED_TRANSITION_ROWS=9
PG_READY=True
exit 0
```

`ALLOWED_TRANSITION_ROWS=9` matches the value in `staging/probe_PGR.txt` exactly, and the
`[dsn] AERORA_TEST_DATABASE_URL ->` line is the direct evidence that defect 1d is fixed.

Directory identity, both ways, against the real server:

```
$ python staging/probe_pg_ready.py --data-dir pgdata_r1
REQUESTED_DATA_DIRECTORY=...\staging\pgdata_r1
PG_READY=True                                                        exit 0

$ python staging/probe_pg_ready.py --data-dir pgdata
REQUESTED_DATA_DIRECTORY=...\staging\pgdata
NOT_READY=server reports data_directory='.../staging/pgdata_r1',
          which is not the requested '...\staging\pgdata'
PG_READY=False                                                       exit 1
```

**Not executed:** the EMPTY, PARTIAL and UNREACHABLE paths were **not** exercised against a
real database. Doing so would require creating, degrading or stopping a cluster, all
forbidden here. Those three classifications are covered by the hermetic tests only, and
this report does not claim live validation of them.

### DSN writer inventory re-scan

```
$ python staging/probe_p10i_writer_inventory.py
python files scanned            : 1267
files that could not be scanned : 0
1. staging/bootstrap_pg.py: ok line 436 dsn_file.write_text(...) in ['write_dsn_if_safe']
2. staging/probe_pg_find.py: 0 write site(s); delegates=True
   staging/pg_keepalive_r1.py: 0 write site(s); delegates=True
3. 14 other site(s), all tests/diagnostics on supplied paths
INVENTORY CLEAN   (exit 0)
```

**No new direct DSN writer has appeared.** The file count rose from 1,261 to 1,267 —
concurrent tabs added files — and `probe_pg_ready.py` does not appear in the write-site
list at all.

---

## 5. Confirmation that the DSN and PostgreSQL were untouched

| Check | Baseline | Final |
|---|---|---|
| DSN sha256 | `a746580d3cead2efa056e3f422fa38ef4b0d303af94bbe1f95b75c2661af3a96` | **identical** |
| DSN bytes | 47 | 47 |
| server-reported `data_directory` | `.../staging/pgdata_r1` | identical |
| classification | PROVISIONED, 89 tables | identical |
| postmaster | PID 12924, started 11:31:08 | **PID 12924, started 11:31:08** |
| `staging/pgdata` | present, `base/` 22,177,237 B | present, **22,177,237 B** |
| `staging/pgdata_r1` | present, `base/` 35,419,649 B | present, **35,419,649 B** |
| git head | `074b772a` | `074b772a` |

The DSN hash was also captured immediately before and after the live probe run
specifically, and was unchanged.

### Concurrent work preserved

Tracked-modified count went **118 → 120** during the session. The delta is **not** from this
workstream: both files I changed were already untracked, and a filtered check of the
tracked-modified list for every path I touched returns nothing. The two new entries are
another tab's work (`support.py` and `exchange_certification.py`, both written at 13:37).
Nothing was committed, reverted or stashed.

---

## 6. Remaining limitations

1. **EMPTY / PARTIAL / UNREACHABLE are not live-validated.** Covered by hermetic tests
   only; exercising them for real needs a cluster this workstream may not create or
   degrade.
2. **Defect 1a was established by reading the control flow, not by observing a false
   `PG_READY=True`.** The conclusion is structural and I am confident in it — the value was
   computed and discarded, with no branch before the final print — but it was not
   demonstrated against a degraded database.
3. **The lane relation list is still hand-maintained.** It is no longer the readiness
   *gate*, but it is still a list someone must keep in step with the paper-trading schema.
   Deriving it from the migrations would remove that, and is a larger change than this
   brief allows.
4. **`--data-dir` is opt-in, and nothing invokes the probe with it.** A caller that omits it
   gets no directory-identity guarantee. No wrapper was updated, because no automated
   consumer of this probe exists.
5. **Two connections per run.** `_default_probe` opens one for classification and
   `lane_facts` opens another for the lane relations, because the shared probe returns a
   `TargetState` rather than a live cursor. Both are read-only and closed; the cost is one
   extra connection, and the alternative was to fork `_default_probe` — which is exactly
   the duplication this workstream removes.
6. **The writer-inventory scan is still not wired into CI**, so it protects only what it is
   pointed at.
7. **Readiness is point-in-time.** A pass means the database was ready when the probe ran.

---

## 7. Acceptance recommendation

**Recommend acceptance of this workstream.**

The shared contract was reused safely — `bootstrap_pg._default_probe` and
`TargetState.classification`, with no marker list or threshold redefined and no second
readiness contract introduced — and the focused tests pass (20/20, plus 78/78 including the
WS-H and WS-I regressions). The probe now distinguishes UNREACHABLE, EMPTY, PARTIAL and
PROVISIONED, and refuses to report ready on a manually selected table list alone. The shared
DSN, both data directories, the running postmaster and all concurrent changes are verified
untouched.

### Scope disclaimer

This workstream covers the readiness **probe's** consistency with the PostgreSQL
classification contract, and nothing else. It does **not** close **QUOTA-01**, **RECON-01**,
**TENANT-01**, or any exchange certification gate; those remain independent launch-readiness
questions and are unaffected by this change. It also makes no claim about production: no
production database was contacted, and the only live execution was against the local
staging cluster.
