# Phase 11B / W1D-08 — Static Drift Guards: Verification Only, No New Tests Needed

**Date:** 2026-10-10
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **COMPLETE AS A VERIFICATION. NO FILE WAS CREATED OR EDITED.**

Both static invariants are **already covered** by `tests/test_snapshot_dimension_invariants.py`
(W1D-05). The brief's §3 instruction — *"First determine whether existing tests already cover
either invariant. Add no redundant tests"* — is the operative one: writing a new test file
would have duplicated working coverage. Coverage was verified rather than assumed, including
by mutation.

| Invariant | Status |
|---|---|
| **A — deterministic ordering** | **ALREADY COVERED AND VERIFIED.** 6 tests, mutation-proven non-vacuous. Not a substring check. |
| **B — explicit classification decision** | **ALREADY COVERED, BUT AGAINST A TEST-LOCAL DECLARATION.** The production-linked form is **NOT IMPLEMENTABLE** — no production key declaration exists and AP-W1D-3 is NOT GRANTED. |

| | |
|---|---|
| AP-W1D-3 | **NOT GRANTED** — unchanged by this phase |
| W1C-02 | **OPEN** — drift-classification defect **not fixed** |
| Files created or edited | **none** outside `staging/` |
| TENANT-01 / QUOTA-01 | **OPEN** |
| PostgreSQL integration | **UNEXECUTED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Preflight and exit preservation

| Measurement | Preflight | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| `origin/main` / divergence | `074b772a…` / `0 0` | **same** |
| Worktrees | 1 (`main`) | 1 (`main`) |
| Tracked index entries | 2104 | 2104 |
| Staged paths | 22 | **22** |
| Unstaged tracked | 102 | **102** |
| Untracked | 3507 | 3512 (this phase's own `staging/` artifacts) |
| Unmerged | 0 | **0** |
| Stash | 2 | 2 |
| **Digest A** (`sha256` of raw `git ls-files -s`, file order) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **SAME** |
| **Digest C** (`sha256` of sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **SAME** |
| **Index tree** (`git write-tree`, isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **SAME** |
| Shared `.git/objects` | 198 | **198** |

Per the brief, the `.git/index` file hash was **not** used as the staged-content invariant. It
is unreliable here — `core.autocrlf=true` lets ordinary read-only `git status` rewrite its stat
cache. Digest C (scoped to the 22 staged paths) and the index tree are used instead; the tree
is the one value that has reproduced across independent workstreams.

### 1.1 Isolated verification

```
GIT_INDEX_FILE                   = <temp copy of .git/index>
GIT_OBJECT_DIRECTORY             = <temp dir>
GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects
git write-tree  ->  6f40856cee2bc7ea4d741f69f4b1f68b463d70a4
objects created in the isolated dir : 0
```

Zero objects created even in the isolated directory. Both temp dirs deleted. **The shared Git
index was never opened for writing; `git add` was never used.**

### 1.2 Concurrent changes — reported separately, not attributed to this task

`unstaged` was 100 when W1D-06 measured it earlier this session and is **102** now. Both
additions are other workstreams':

```
21:48:56  backend_app/core/subscription_engine.py     <- also moved again during this phase
21:32:35  tests/test_redis_debounce.py
21:22:07  backend_app/backend/order_watchdog.py
```

`backend_app/core/subscription_engine.py` holds `reserve_quota`, the QUOTA-01 production path,
and was measured **clean** earlier this session. It is now modified by concurrent work and its
mtime advanced again *during* this phase (21:23 → 21:48). **This phase did not touch it** and
makes no claim about the change; it is recorded because the brief requires concurrent changes
be separated from mine, and because an unexplained count delta would otherwise look like mine.

**No candidate file for this phase appears in the unstaged set.**

### 1.3 Candidate files — untouched, preflight = exit

| Path | Status | Size | Blob (preflight = exit) |
|---|---|---|---|
| `scripts/verify_schema_drift.py` | `??` untracked | 12,170 | `f78b35a52552967d469a7cc7d33de92db0a65e3b` |
| `scripts/verify_provisioning.py` | `??` untracked | 16,404 | `51cb6d241de3ef437b662241379f8985f7d5a51e` |
| `scripts/verify_production_schema.py` | `??` untracked | 50,868 | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` |
| `tests/test_snapshot_dimension_invariants.py` | `??` untracked | 28,067 | `bcb4f479fca2365cca2be61aae8961449e03beb9` |
| `tests/test_drift_classification.py` | — | — | **does not exist; not created** |

All four are **untracked** (`??`), never in `HEAD`. Their identifiers are therefore computed
digests, not stored objects, and there is no git copy to restore them from — a further reason
not to edit them speculatively.

---

## 2. Authorization boundary — re-confirmed, unchanged

Re-verified in this phase rather than carried from W1D-07:

- **AP-W1D-3: NOT GRANTED.** `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`, in a
  table where all five AP-W1D approvals are negative.
- No production key declaration exists: `COMPARISON_KEY`, `WHOLE_ROW` and `KEY_WIDTH` are
  **absent from all three production scripts** (searched `verify_schema_drift.py`,
  `verify_provisioning.py`, `verify_production_schema.py` — zero occurrences of any).
- The declaration exists only at `tests/test_snapshot_dimension_invariants.py:93`
  (`WHOLE_ROW = "whole-row"`) and `:100` (`COMPARISON_KEY: dict[str, object] = {`).

Nothing in this phase was edited, so no ownership question arose. No competing change appeared
on any candidate file.

---

## 3. Invariant A — deterministic ordering: ALREADY COVERED AND VERIFIED

### 3.1 The six existing tests

From `tests/test_snapshot_dimension_invariants.py` (18 test functions, parametrised to 48
cases):

| Test | Role |
|---|---|
| `test_every_dimension_has_a_result_set_order_by` | the guard: every `SNAPSHOT_QUERIES` key must have a result-set `ORDER BY` |
| `test_order_by_covers_the_declared_key` | the guard's strength: the clause's first *k* sort expressions must equal the first *k* selected expressions |
| `test_control_a_dimension_without_any_order_by_is_detected` | negative control — absence |
| `test_control_an_order_by_only_inside_a_subquery_is_not_counted` | negative control — subquery clause must not satisfy it |
| `test_control_an_order_by_that_misses_part_of_the_key_is_detected` | negative control — insufficient prefix |
| `test_control_an_order_by_on_the_wrong_columns_is_detected` | negative control — wrong columns |

Plus `test_control_the_real_enum_types_query_is_not_satisfied_by_its_subquery`, which asserts
the property against a **live query definition** rather than synthetic SQL.

### 3.2 It is not a substring check — the brief's specific concern

The brief asks to *"verify the assertion is strong enough to detect a missing ordering clause,
rather than merely finding the words `ORDER BY`."* Measured properties of the implementation:

| Property | Present |
|---|---|
| Parenthesis-depth tracking (so subquery clauses do not count) | **yes** |
| String-literal handling (commas and keywords inside `'…'`) | **yes** |
| Explicit subquery-`ORDER BY` reasoning | **yes** |
| A separate "clause covers the declared key" assertion | **yes** |

A plain `"ORDER BY" in sql` check would exhibit none of these. The distinction is load-bearing:
two real dimensions (`primary_keys`, `enum_types`) legitimately contain `ORDER BY` **inside**
`ARRAY(SELECT …)` subqueries that order the contents of a single cell and say nothing about row
order. A substring check would pass those vacuously.

### 3.3 Non-vacuity proven by mutation, re-run against current source

`staging/probe_p11b_w1d05_guard_mutation.py` drives the **real guard functions** with the
**real dicts** perturbed in memory and requires each to raise. Re-run this phase:

```
-- Guard A: result-set ordering
   [BITES ] M1  policies: ORDER BY removed
   [BITES ] M1b policies: ORDER BY removed (key-coverage guard)
   [BITES ] M2  policies: ORDER BY narrowed to a non-unique prefix
   [BITES ] M3  policies: ORDER BY on the wrong columns
   [BITES ] M4  enum_types: result-set ORDER BY removed, subquery one left
-- Guard B: key declaration
   [BITES ] M5  a new dimension with no key declaration
   [BITES ] M6  a stale key declaration
   [BITES ] M7  privileges: key declared None instead of WHOLE_ROW
   [BITES ] M8  tables: key width 9 against 2 selected columns
   [BITES ] M9  sequences: WHOLE_ROW dimension gains an attribute column
-- Surrounding assumptions
   [BITES ] M10 verify_schema_drift redefines SNAPSHOT_QUERIES
   [BITES ] M11 ADVISORY_DIMENSIONS contains a typo

mutations applied : 12      guards bit : 12      silent failures : 0
real dicts restored afterwards : True (16 dimensions, 16 declared keys)
```

**M4 is the one that matters most** for §3.2: it removes the result-set `ORDER BY` from
`enum_types` while leaving its subquery clause in place. A substring-based extractor would
still find `ORDER BY` and pass. It bites.

### 3.4 "Do not assume current ordering remains adequate when a new dimension is added"

Covered, and demonstrated two ways:

- `test_every_dimension_has_a_result_set_order_by` and `test_order_by_covers_the_declared_key`
  both **iterate `SNAPSHOT_QUERIES` dynamically**, so a newly added dimension is checked
  automatically rather than requiring the test to be updated.
- Mutation **M5** adds a new dimension with no key declaration and the guard **bites**;
  **M1** removes an `ORDER BY` and the guard **bites**.

A new dimension cannot be added without either satisfying both guards or failing the suite.

**Verdict: invariant A is already covered, strong, and non-vacuous. No new test is warranted.**

---

## 4. Invariant B — explicit classification decision: COVERED, BUT NOT IN ITS PRODUCTION FORM

### 4.1 The four existing tests

| Test | Asserts |
|---|---|
| `test_every_dimension_declares_a_comparison_key` | the declaration covers `SNAPSHOT_QUERIES` exactly — no missing, no extra |
| `test_a_declared_key_is_whole_row_or_a_valid_width` | each value is the `WHOLE_ROW` sentinel or an in-range width; **`None` is rejected explicitly** |
| `test_whole_row_dimensions_really_have_no_attribute_columns` | a `WHOLE_ROW` claim is checked, not trusted |
| `test_advisory_dimensions_are_real_dimensions` | `ADVISORY_DIMENSIONS` contains no typo'd name |

Plus controls `test_control_a_new_dimension_without_a_key_declaration_is_detected`,
`test_control_a_stale_key_declaration_is_detected`, and
`test_control_a_none_key_is_distinguishable_from_an_absent_one`. The last is the
`WHOLE_ROW`-vs-`None` distinction: `dict.get()` returns `None` for an absent key, so "this
dimension has no attribute columns" (a reviewed conclusion) must not collapse into "nobody
declared this dimension" (an omission). Mutation **M7** confirms it bites.

### 4.2 The honest limitation: the declaration is test-local

The brief's §3 Test B is conditional — *"Every `SNAPSHOT_QUERIES` dimension must have a
corresponding key declaration **in the proposed design, if that declaration already
exists**"* — and instructs: *"If no production key declaration exists yet, report that the
test depends on the authorized classification change; do not introduce a test-local duplicate
declaration that could drift from production."*

Measured state:

- **No production declaration exists.** Zero occurrences of `COMPARISON_KEY`, `WHOLE_ROW` or
  `KEY_WIDTH` in any of the three production scripts.
- **A test-local duplicate already exists**, created by W1D-05 at
  `tests/test_snapshot_dimension_invariants.py:93,100`, because W1D-05 was forbidden from
  editing `scripts/`.

So the brief's prohibition is already satisfied in the only way available: **I did not
introduce one.** The pre-existing one is W1D-05's and is documented in that file's docstring
as an intermediate state.

**What this means precisely.** Invariant B's *decision-forcing* function works today: a new
dimension cannot be added without someone declaring what identifies its rows, and M5/M6 prove
it. What it cannot do is bind that declaration to production behaviour — `compare()` has no key
concept, so the declaration describes an intent the shipped code does not implement. The
drift risk the brief names is real: the test-local map and a future production map could
disagree, and nothing would catch it until they are unified.

**Verdict: invariant B is covered as a decision gate; its production-linked form is NOT
IMPLEMENTABLE without AP-W1D-3.** The fix is a one-line change once authorized — the test
imports `COMPARISON_KEY` from `verify_provisioning` instead of defining it — which is exactly
what W1D-05 §3.1 anticipated and W1D-06 §6.1 item 3 specified.

### 4.3 Why I did not add a production key map

The brief forbids it: *"Since AP-W1D-3 is not granted, do not add a production key map just to
make this test pass."* I did not. Doing so would also have been the substance of AP-W1D-3
delivered under a different label.

---

## 5. Tests run

Run sequentially, narrowest first. The mutation probe perturbs module-level dicts, so it was
run on its own and never concurrently with the suite.

| # | Command | Exit | Passed | Failed | Skipped | xfail |
|---|---|---:|---:|---:|---:|---:|
| 1 | `python staging/probe_p11b_w1d05_guard_mutation.py` | **0** | 12 mutations bit | 0 silent | — | — |
| 2 | `python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **48** | 0 | **0** | 0 |
| 3 | `python -m pytest` on the five-file static-guard / drift group † | **0** | **103** | 0 | **12** | 1 |

† `tests/test_snapshot_dimension_invariants.py`,
`tests/test_schema_drift_checker_is_read_only_postgres.py`, `tests/test_migration_tooling.py`,
`tests/test_orm_models_match_provisioned_schema.py`,
`tests/test_bug049_sql_and_alembic_agree.py`. 46.05s.

Both counts match the figures recorded in W1D-05 and W1D-06 exactly, confirming no drift in
the guard surface.

### 5.1 The 12 skips are not passes

All 12 are `tests/test_schema_drift_checker_is_read_only_postgres.py`, each reporting that it
*needs a real PostgreSQL instance* because `default_transaction_read_only` and SQLSTATE 25006
do not exist in SQLite. They were **deliberately not run** — this phase may not contact a
database — and are **not counted as evidence**. The 1 xfail is
`test_only_one_model_claims_the_positions_table`, a pre-existing strict marker, unrelated.

### 5.2 Not done

No PostgreSQL or Redis connection, no snapshot recapture, no server started, no package
installed, no CI trigger.

---

## 6. Diff and scope audit

| Audit item | Result |
|---|---|
| Only explicitly authorized test files changed | **no file changed at all** |
| Tracked production files modified | **none** — all three scripts are untracked and byte-identical at preflight and exit |
| `tests/test_snapshot_dimension_invariants.py` modified | **no** — blob `bcb4f479…` unchanged; guards not weakened or removed |
| New test file created | **no** — would have been redundant (§3, §4) |
| Production key map added | **no** — forbidden while AP-W1D-3 is NOT GRANTED |
| Staging, commit, push, reset, stash, branch change | **none** |
| Shared Git index mutated | **no** — verification used an isolated index *and* isolated object directory; objects 198 → 198 |
| Staged content identity | **unchanged** — digest C and index tree identical |
| Concurrent changes attributed to this task | **no** — reported separately in §1.2 |
| Migrations, snapshots, DB tooling, shared config, other workstreams' files | **untouched** |

Files created, all untracked and all under `staging/`:

```
PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md   (this report)
w1d08_preflight.py, w1d08_preflight.txt
w1d08_mutation.txt
```

---

## 7. Status of the two invariants, stated separately

Using the brief's own three categories:

| | Invariant A — deterministic ordering | Invariant B — explicit classification decision |
|---|---|---|
| **Already covered and verified** | **YES** — 2 guards + 4 negative controls + 1 live-query control; 48/48 pass; mutations M1–M4 all bite | **PARTLY** — 4 guards + 3 controls; 48/48 pass; mutations M5–M9 all bite |
| **Newly covered and passing** | no — nothing added, nothing needed | no — nothing added |
| **Not implementable without the production declaration / further authorization** | no | **YES, for the production-linked form** — no production `COMPARISON_KEY` exists; binding the guard to production requires **AP-W1D-3** |

---

## 8. Remaining gaps and proposed test cases

No new tests are proposed for the current code — the coverage exists. These become
implementable only once AP-W1D-3 is granted, and are recorded so the work is not re-derived:

1. **Bind invariant B to production.** Change the test to
   `from scripts.verify_provisioning import COMPARISON_KEY, WHOLE_ROW` and delete the
   test-local copy. One-line change; removes the drift risk in §4.2. Depends on the production
   declaration existing.
2. **Key uniqueness is still unverified.** The widths rest on documented catalogue properties,
   not measurement. W1D-05 §8.1 and W1D-06 §8.2 both record this. A wrong width would let two
   distinct objects collide into one key. **Needs a live database** (AP-W1D-1 / AP-W1D-5, both
   also NOT GRANTED).
3. **Ambiguous key collision handling is unspecified.** Raised in W1D-07 §4: the W1D-06 design
   would silently group colliding rows into one key's multiset and misclassify two distinct
   objects as a modification. A static guard cannot detect this; it needs either the uniqueness
   evidence from (2) or an explicit runtime collision check in the authorized classifier.
4. **Duplicate-count output format is unspecified.** Also W1D-07 §4.

Items 3 and 4 are specification work that needs no owner approval. I can draft both read-only
on request.

---

## 9. Disposition

| Item | Status |
|---|---|
| **Invariant A** | **ALREADY COVERED AND VERIFIED** — strong, non-vacuous, dynamic over `SNAPSHOT_QUERIES` |
| **Invariant B** | **COVERED as a decision gate; production-linked form NOT IMPLEMENTABLE** without AP-W1D-3 |
| **New tests added** | **none** — existing coverage made them redundant (§3 of the brief) |
| **W1C-02** | **OPEN** — the drift-classification defect is **not fixed**; `compare()` still diffs whole tuples |
| **AP-W1D-3** | **NOT GRANTED** — unchanged |
| **W1D-05 guards** | **INTACT** — 48/48, file byte-identical |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

The smallest independently safe part of W1C-02 turned out to be **already done** — by W1D-05.
This phase's contribution is evidence that it is done properly: the guards are dynamic over the
dimension set, are not substring checks, and reject all 12 mutations. The remaining part of
W1C-02 is not static and cannot be reached without AP-W1D-3.

Nothing in this report constitutes an approval.
