# Phase 11B / W1D-13 — Authorized Classifier Implementation

**Date:** 2026-10-11
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **IMPLEMENTED AND UNIT-TESTED. First change of two complete.**

The four authorized files were changed and nothing else. All four verification commands ran;
one needed a second run after a transient PostgreSQL outage in a DB-gated file outside this
scope, and **both runs are reported below rather than only the clean one** (§3.4).

| | |
|---|---|
| AP-W1D-3 first change | **COMPLETE** |
| Second change (`diff_snapshots`, `check_drift`) | **NOT STARTED** — D5 sign-off outstanding |
| W1C-02 | **OPEN** — see §7 for exactly what remains |
| TENANT-01 / QUOTA-01 | **OPEN** |
| PostgreSQL integration | **UNEXECUTED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Pre-edit gate — 15 checks, all passed

Run immediately before the first edit; non-zero exit on any mismatch.

| Check | Expected | Result |
|---|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **PASS** |
| Branch / divergence | `main` / `0 0` | **PASS** |
| Staged count | 22 | **PASS** |
| Digest A | `98ae5bdf2af9a0ae…` | **PASS** |
| Digest C | `8d9c475b86a4a71d…` | **PASS** |
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | **PASS** |
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | **PASS** |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | **PASS** |
| `tests/test_drift_classification.py` | absent | **PASS** |
| `scripts/verify_production_schema.py` (out of scope) | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | **PASS** |
| Concurrent edits to any of the five paths | none | **PASS** ×5 |

---

## 2. The diff

| # | Path | Before | After | Lines |
|---|---|---|---|---|
| 1 | `scripts/verify_provisioning.py` | `51cb6d241de3ef43…` | `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | 660 |
| 2 | `scripts/verify_schema_drift.py` | `f78b35a52552967d…` | `f853c1482ca295440c78e4f25a8989438fab041e` | 406 |
| 3 | `tests/test_drift_classification.py` | *(new)* | `fcffbe8e01367aa7f99fffe2677267821fb8424d` | 665 |
| 4 | `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365c…` | `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | 521 |

### 2.1 `scripts/verify_provisioning.py` — the shared classifier (D4)

Added between `_norm()` and the orchestration section; **nothing existing was modified**
except the import block (`collections`, `dataclasses`).

- `WHOLE_ROW = "whole-row"` — a sentinel, not `None`, so "no attribute columns" (a reviewed
  conclusion) cannot collapse into "nobody declared this" (an omission).
- `COMPARISON_KEY` — 16 entries, widths exactly as approved in W1D-09 §5, each carrying the
  rationale comment that previously lived in the test file.
- `_sort_key(value)` — the null-safe total order. **This is not cosmetic:** `_norm` preserves
  `None`, `None < 'x'` raises `TypeError` in Python 3, and four of `columns`' seven attribute
  columns are not `coalesce(...)`-guarded, so a plain `sorted()` would *crash* on real data.
- `ClassifiedDimension` — the result dataclass, with `has_findings()`.
- `_multiset_compare()` — whole-row multiset difference. Never pairs two non-identical rows,
  which is what makes the D1 fallback safe.
- `classify_dimension()` — the four-way classifier.

**`advisory` is set by the caller, not the classifier.** This was a deliberate correction
made during implementation: my first draft had `classify_dimension` read
`ADVISORY_DIMENSIONS`, which lives in `verify_schema_drift` — importing it would have
recreated the exact import cycle D4 exists to avoid. Advisory routing is renderer policy
anyway: `compare()` has two tiers, `check_drift()` has three, `diff_snapshots()` has none.

### 2.2 `scripts/verify_schema_drift.py` — `compare()` routed through it

- `ROW_CAP = 12` — the cap made explicit; value unchanged.
- `classify_all()` — classifies every dimension **once**, so the summary table and the detail
  sections cannot disagree. Previously `compare()` decided drift by whole-tuple membership
  while `main()`'s summary decided by list equality; for reordered-identical rows the summary
  printed `DRIFT` and the detail printed `0 missing, 0 unexpected`.
- `render()` — the detail lines.
- `compare()` — signature `(baseline, live) -> tuple[list[str], list[str]]` **unchanged**.
- `main()` — the summary marker now reads the classifier's verdict. Marker strings `"same "`
  and `"DRIFT"` are unchanged; `"?????"` was added for `inconclusive`, which previously had
  no representation because the state did not exist.

### 2.3 `tests/test_drift_classification.py` — new, 76 cases

The 14 approved cases from W1D-10 §3, parametrised. Pure data; no gate, no skip, no xfail.

### 2.4 `tests/test_snapshot_dimension_invariants.py` — imports the production map

The test-local `WHOLE_ROW` and `COMPARISON_KEY` definitions (41 lines) were **deleted** and
replaced with `from scripts.verify_provisioning import COMPARISON_KEY, WHOLE_ROW`. Every
existing guard now checks the declaration production classifies with.

I briefly left the old dict bound to `_SUPERSEDED_TEST_LOCAL_DECLARATION` and then removed
it: keeping a dead duplicate would have preserved the drift risk the change exists to
eliminate. **No test was removed or weakened** — all 48 cases remain and all still pass.

---

## 3. Verification — actual results

Run in the prescribed order. Counts below are measured, not expected.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `pytest tests/test_drift_classification.py -q --no-header -p no:cacheprovider` | **1** → then **0** | first run **1 failed, 74 passed**; after fixing the test fixture (§3.3): **76 passed, 0 skipped** in 4.44s |
| 2 | `pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **48 passed, 0 failed, 0 skipped** in 5.83s |
| 3 | `python staging/probe_p11b_w1d05_guard_mutation.py` | **0** | **12 applied, 12 bit, 0 silent**; real dicts restored |
| 4 | five-file group `-q --no-header -rs` | **0** | **first run: 99 passed, 17 skipped**; **second run: 103 passed, 12 skipped, 1 xfailed** in 38.82s |

### 3.1 Against the recorded expectations

| Expectation | Measured |
|---|---|
| 48 existing guard cases pass | **met** — 48 passed |
| Mutation probe 12 / 12 / 0 | **met** |
| Five-file group 103 / 12 / 1 | **met on the second run**; the first returned 99 / 17 (§3.4) |

### 3.2 The 12 skips are not passes

All 12 are PostgreSQL-gated. They establish **nothing** about database integration or
live-schema correctness, and the 1 xfail
(`test_only_one_model_claims_the_positions_table`) is a pre-existing strict marker,
unrelated to this change.

### 3.3 The one real test failure, and what it was

`test_identical_snapshots_report_nothing` failed with `'drift' != 'same'`. **The
implementation was right and my fixture was wrong.** I had used `POLICY` and
`POLICY_CHANGED` as "two identical rows" — but those two deliberately share the key
`('fee_records','fee_select')`, differing only in `with_check`. Two rows per key on both
sides is a genuine key collision, which D1 requires be flagged. I replaced the fixture with
two policies that have distinct keys, and **added a test for the behaviour the failure
exposed**: `test_an_ambiguous_key_is_reported_even_when_both_sides_are_identical`. A wrong
key width should surface on its own, not only when some unrelated change happens to expose
it.

### 3.4 Why the group run differed, and why it is not this change

First run 99 passed / 17 skipped; expected 103 / 12 / 1. Diagnosed before reporting:

- The five extra skips are all in `tests/test_orm_models_match_provisioned_schema.py`
  (lines 178, 202, 224, 254), each reporting *"the configured PostgreSQL is not reachable …
  port 51805 failed: timeout expired. NOT a pass"*.
- That file references **none** of this change's modules — zero occurrences of
  `verify_provisioning`, `verify_schema_drift`, `COMPARISON_KEY`, `classify_dimension` or
  `WHOLE_ROW`. It cannot be affected by the edit.
- `staging/pg_dsn.txt` names port 51805; a direct socket check found it **reachable** moments
  after the run. A concurrent PostgreSQL lifecycle (the PG/A23/A24 workstreams) was starting
  or stopping the server.
- Re-running the identical command returned **103 passed, 12 skipped, 1 xfailed** — the
  recorded baseline exactly.

Transient environment, in a DB-gated file outside the authorized scope. Both runs are
recorded; the diagnosis stayed inside the authorized scope and required no unauthorized file.

### 3.5 Extra evidence: the classifier over the real captured snapshot

Not required by the brief. Reads `staging/provisioning_snapshot.json` — a file, **not** a
database.

```
snapshot 396,325 B, 188 policies
self-comparison            -> failures=0  advisories=0
dimensions with an AMBIGUOUS declared key on real data -> 0
```

**No declared key collides anywhere in the captured snapshot.** That is real-data
corroboration of the 16 widths. It is **not** catalogue verification: one capture from
2026-10-06 is not a uniqueness constraint check, and §7 still records uniqueness as
unverified for all 16.

### 3.6 The fix, on a real row

One real policy perturbed **in memory only**; the file on disk was never written
(mtime and size byte-identical before and after, value on disk unchanged).

```
billing_invoices / billing_invoices_owner_access, col[6]:
  '((auth.uid())::text = (user_id)::text)'  ->  '(W1D13_SENTINEL)'

NOW:
  policies: 187 unchanged, 1 modified, 0 missing, 0 added
      MODIFIED ('billing_invoices', 'billing_invoices_owner_access')
          attr[4] (col 6): '((auth.uid())::text = (user_id)::text)' -> '(W1D13_SENTINEL)'

PREVIOUSLY:
  policies: 1 missing, 1 unexpected
      MISSING from the database : ('billing_invoices', 'billing_invoices_owner_access', …)
      NOT IN this repository    : ('billing_invoices', 'billing_invoices_owner_access', …)
```

One object, named once, with the attribute that changed — instead of the same policy
asserted both absent from the database and foreign to the repository.

### 3.7 The untouched helpers still behave as before

```
_norm(None) -> None    _norm('') -> ''    _norm(False) -> False    _norm([1,2]) -> '[1, 2]'
_as_tuples([[1,2]]) -> [(1, 2)]
diff_snapshots(x, x) -> 0 problems
diff_snapshots(x, y) -> 'policies: 188 -> 188 row(s); 1 disappeared, 1 appeared'
```

`diff_snapshots()` still carries the old whole-tuple diff — correctly, because routing it
through the classifier is the second change and D5's sign-off is outstanding.

---

## 4. The eight W1D-12 invariants

| # | Invariant | Held | Evidence |
|---|---|---|---|
| 1 | `RESULT: NO DRIFT` / `RESULT: DRIFTED` verbatim | **yes** | both strings untouched in `main()`; four launch documents quote them |
| 2 | Exit codes 0 / 1 / 2 | **yes** | `main()`'s return paths unchanged; failures still drive exit 1 |
| 3 | Advisory routing | **yes** | `test_advisory_dimensions_route_to_advisories_not_failures`, `test_non_advisory_dimensions_route_to_failures` |
| 4 | `check_drift()` untouched | **yes** | `verify_production_schema.py` blob `6e194f43…`, byte-identical |
| 5 | No nonzero drift without a subject | **yes** | `test_drift_is_never_reported_without_naming_a_subject`, 8 cases |
| 6 | Null-safe sorting | **yes** | `_sort_key`; `test_sorting_does_not_crash_on_mixed_null_and_text_keys` |
| 7 | W1D-05 guards not weakened | **yes** | 48/48 pass; 12/12 mutations bite |
| 8 | No uniqueness claim | **yes** | none made; §3.5 explicitly distinguishes snapshot corroboration from catalogue verification |

Also preserved: the 12-row cap (`test_the_row_cap_is_still_twelve`,
`test_more_rows_than_the_cap_are_truncated_with_a_count`), the two row labels
(`test_removal_and_addition_keep_their_original_labels`), and `compare()`'s return contract
(`test_compare_still_returns_two_lists_of_strings`).

---

## 5. Scope and preservation review

| Audit item | Result |
|---|---|
| Files changed | **exactly the four authorized** |
| `scripts/verify_production_schema.py` | **byte-identical**, `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` |
| `_norm()`, `_as_tuples()`, `diff_snapshots()`, snapshot format, 16 SQL queries | **unmodified** (§3.7) |
| Existing tests removed or weakened | **none** — 48 preserved, all passing |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` — unmoved |
| Staged count | **22** — unchanged |
| Digest A / Digest C | `98ae5bdf2af9a0ae…` / `8d9c475b86a4a71d…` — **unchanged** |
| `.git/objects` | **198** — unchanged |
| Staged, commit, push, reset, stash, branch change | **none** |
| Database, Redis, network, CI, exchange operation | **none** |
| Dependencies installed | **none** — `collections` and `dataclasses` are standard library |
| Snapshot regenerated | **no** — mtime and size byte-identical (§3.6) |

The 140 untracked files outside `staging/` and the authorized set are other workstreams'
pre-existing work, not this task's.

---

## 6. Artifacts created, all under `staging/`

```
PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md   (this report)
w1d13_gate.py          the pre-edit gate
w1d13_review.py        scope and preservation review + real-snapshot evidence
w1d13_realdemo.py      the fix on a real row, in memory only
```

---

## 7. What remains, and what this does not establish

1. **The second change is not started.** `diff_snapshots()` and `check_drift()` still carry
   the whole-tuple diff. **D5's production-audit sign-off is outstanding**, and that is the
   gate.
2. **Key uniqueness is unverified for all 16 dimensions.** No declared key collides in the
   2026-10-06 snapshot (§3.5), but that is one capture, not a constraint check. Settling it
   needs **AP-W1D-1 / AP-W1D-5**, both NOT GRANTED. The classifier is built to stay correct
   without it — that is what D1 is for.
3. **No PostgreSQL catalogue or live-schema claim is made.** The 12 skips are gated and are
   not passes; the transient 17-skip run (§3.4) is itself a reminder that the PG lane is not
   under this task's control.
4. **Nothing is committed.** The change is unstaged and untracked in the working tree. It is
   one `git checkout` from being lost, as is all the other uncommitted work in this tree.
5. **W1C-02 stays OPEN.** The defect is fixed in `compare()` and covered by 76 runtime tests,
   but the finding is not closed until the second change lands and a real drift run exercises
   it. Closing it is not this task's authority.

---

## 8. Disposition

| Item | Status |
|---|---|
| **AP-W1D-3, first change** | **IMPLEMENTED AND UNIT-TESTED** — 76 new tests, 48 existing preserved, 12/12 mutations biting |
| **D1** ambiguous = hard failure, distinct label | implemented; `AMBIGUOUS KEY` |
| **D2** reordered identical = unchanged | implemented; `test_reordered_identical_rows_are_not_drift` |
| **D3** Option 1, two changes | first change only; second not started |
| **D4** classifier in `verify_provisioning.py` | implemented; no import cycle |
| **D5** `check_drift()` preserved exactly | **held by construction** — the file was never opened; sign-off still outstanding |
| **W1C-02** | **OPEN** |
| **TENANT-01 / QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |
