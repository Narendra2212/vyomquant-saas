# Phase 11B / W1D-10 — Classifier Implementation Readiness and Authorization Gate

**Date:** 2026-10-10
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **IMPLEMENTATION-READY PLAN. AUTHORIZATION ABSENT. NOTHING IMPLEMENTED.**

No code, test, migration, snapshot, shared configuration or other workstream's artifact was
changed. No database or Redis connection, no snapshot recapture, no staging, commit, push or
CI trigger. **AP-W1D-3 remains NOT GRANTED**, rechecked against the authoritative record.

| | |
|---|---|
| W1C-02 | **OPEN** |
| AP-W1D-3 | **NOT GRANTED** |
| TENANT-01 / QUOTA-01 | **OPEN** |
| PostgreSQL integration | **UNEXECUTED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## HEADLINE

Four results, one of which settles the scope question with hard evidence rather than preference.

1. **The classifier must live in `scripts/verify_provisioning.py`. Any other placement creates
   a circular import.** The three scripts form a strict DAG with `verify_provisioning` at the
   root: it imports neither sibling, while both siblings import it. A classifier in
   `verify_schema_drift.py` would force `verify_provisioning → verify_schema_drift` for
   `diff_snapshots()`, closing a cycle against the existing `verify_schema_drift →
   verify_provisioning` edge (§2.2). This is a structural fact, not a judgement.
2. **Share the classifier; do not share the renderer.** The three call paths have genuinely
   different output contracts — different return types, different severity models, different
   row caps and different labels. A shared *pure diff* is a refactor; a shared *renderer*
   would be a behaviour change to provisioning and production tooling that this work has no
   mandate to make (§2.3).
3. **Every `compare()` hit in `tests/` is a false positive.** Three test files appear to call
   it; none imports any drift module, and each defines its own local `compare` / `_compare_*`.
   `compare()` has **exactly one caller in the repository**: its own `main()`. So does each of
   the other two (§2.1). The blast radius of the change is three `main()` functions.
4. **`check_drift()` is the most divergent path and the riskiest to touch.** It takes a live
   connection and a `Findings` sink, prints directly, returns the live snapshot rather than a
   diff, caps rows at 8 not 12, says `MISSING from production` not `MISSING from the database`,
   and routes findings into **three severity tiers** via an explicit 10-dimension critical
   list that `compare()` has no equivalent of (§2.3).

---

## 1. Current source baseline

Verified unchanged from W1D-09 — no re-analysis was repeated, only fingerprint confirmation.

| Path | Blob | vs W1D-09 |
|---|---|---|
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | **SAME** |
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | **SAME** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | **SAME** |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | **SAME** |

All four are **untracked** (`??`), never in `HEAD`, so these are computed identifiers rather
than stored objects and there is no git copy to restore from.

Prior artifacts read, all present: `PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md`
(36,329 B), `p11b_w1d06x_multiplicity.txt` (17,798 B), `p11b_w1d06x_prototype.txt` (11,436 B),
`PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md` (19,337 B),
`PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (56,397 B).

Carried forward from W1D-09 without re-deriving: 16 dimensions, 12 keyed / 4 whole-row,
0 undeclared, 12 exposed to W1C-02; `drift.SNAPSHOT_QUERIES is prov.SNAPSHOT_QUERIES` is
`True`; uniqueness verified for **0 of 16**; no production `COMPARISON_KEY` exists.

---

## 2. The three-path call graph and scope recommendation

### 2.1 Definitions and callers — measured by AST walk over the repository

| Function | Defined at | Signature | Production callers |
|---|---|---|---|
| `compare` | `verify_schema_drift.py:192` | `(baseline, live) -> tuple[list[str], list[str]]` | **1** — `verify_schema_drift.py:264` in `main()` |
| `diff_snapshots` | `verify_provisioning.py:234` | `(first, second) -> list[str]` | **1** — `verify_provisioning.py:333` in `main()` |
| `check_drift` | `verify_production_schema.py:217` | `(conn, dsn, baseline, f: Findings) -> dict` | **1** — `verify_production_schema.py:1062` in `main()` |

**Each function has exactly one production caller, which is its own module's `main()`.** There
is no cross-script call of any of the three.

**False positives ruled out.** An AST scan reported 30+ `compare()` call sites in `tests/`.
All are local functions:

| Test file | Imports a drift module? | Defines its own |
|---|---|---|
| `tests/regression/test_baseline_unchanged.py` | **No** | `compare`, `_compare_objects`, `_compare_arrays`, `_compare_records_by_identity`, `_compare_string_arrays` |
| `tests/test_no_duplicate_services.py` | **No** | `compare` |
| `tests/test_paper_api_shape_compatibility.py` | **No** | — (calls something else named `compare`) |

This matters: it means **no existing test exercises any of the three production functions**,
consistent with W1D-06's finding, and the change cannot break a test by signature.

The only other callers are `staging/` probes (this session's and the concurrent W1D-06x
session's), which are not production.

### 2.2 Import graph — the structural constraint

Measured import edges among the three:

```
scripts/verify_schema_drift.py:69       from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm
scripts/verify_production_schema.py:100 from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm
scripts/verify_production_schema.py:101 from scripts.verify_schema_drift  import ADVISORY_DIMENSIONS, _as_tuples,
                                                                                 _refuse_transaction_pooler,
                                                                                 assert_read_only, redact
scripts/verify_provisioning.py          imports NEITHER sibling
```

So the dependency DAG is:

```
verify_provisioning        (root — depends on neither)
      ^            ^
      |            |
verify_schema_drift  --->  verify_production_schema
```

**Consequence, decisive for placement:**

| Classifier placed in | `compare()` can use it | `check_drift()` can use it | `diff_snapshots()` can use it | Cycle? |
|---|---|---|---|---|
| **`verify_provisioning.py`** | yes (edge exists) | yes (edge exists) | yes (same module) | **No — acyclic** |
| `verify_schema_drift.py` | yes (same module) | yes (edge exists) | would need `provisioning → schema_drift` | **YES — cycle** against `schema_drift → provisioning` |
| a new module e.g. `scripts/_drift_classify.py` | yes | yes | yes | No |

`verify_provisioning.py`'s module-level imports are `__future__`, `argparse`, `json`, `os`,
`pathlib`, `psycopg2`, `psycopg2.extensions`, `subprocess`, `sys`. All three callers already
import `psycopg2`, so placing the classifier there adds no new import cost. The classifier
needs only `collections` (for `Counter`) from the standard library.

A separate `scripts/_drift_classify.py` is also acyclic and arguably cleaner, but it is a new
file in `scripts/` — a larger surface than the brief's "smallest" framing, and one more path
for an owner to review. **Recommendation: `verify_provisioning.py`, beside `SNAPSHOT_QUERIES`
and the `COMPARISON_KEY` that W1D-09 §5 specifies for the same module.**

### 2.3 Why the renderer must NOT be shared

The three paths differ in ways that are not incidental:

| | `compare()` | `diff_snapshots()` | `check_drift()` |
|---|---|---|---|
| Purpose | live schema vs baseline | provisioning run 1 vs run 2 (reproducibility) | production audit |
| Returns | `tuple[list[str], list[str]]` (failures, advisories) | `list[str]` (problems) | `dict` (the live snapshot) |
| Emits by | returning strings | returning strings | **`print()` directly** |
| Severity model | 2 tiers: failure / advisory | **none** — one flat list | **3 tiers**: `f.critical` for a named 10-dimension list, `f.minor` for advisory *and* for indexes/sequences/extensions/enum_types |
| Row cap | 12 | **4** | **8** |
| Missing label | `MISSING from the database` | `only in run 1` | `MISSING from production` |
| Added label | `NOT IN this repository` | `only in run 2` | `NOT IN this repository` |
| Header includes row counts | no | **yes** — `{len(a)} -> {len(b)} row(s)` | **yes** — `baseline=… live=…` |
| Takes a connection | no (pure) | no (pure) | **yes** — `conn`, plus a `Findings` sink |

Two consequences worth stating plainly:

- **`check_drift()`'s 3-tier severity routing is unrelated to classification.** It decides
  *criticality per dimension* from a hard-coded list (`tables, columns, primary_keys,
  foreign_keys, row_level_security, policies, privileges, unique_and_check_constraints,
  functions, triggers` → critical; the rest → minor). Replacing its diff must leave that
  routing untouched, or production audit severities change — which is exactly the "unrelated
  provisioning behaviour" the brief forbids disturbing.
- **`diff_snapshots()` is already less wrong than `compare()` on the multiplicity case**,
  because its header prints `{len(a)} -> {len(b)} row(s)`. For `[A,A]` vs `[A]` it emits
  `2 -> 1 row(s); 0 disappeared, 0 appeared` — still contradictory, but it at least reveals
  that the counts changed, whereas `compare()` emits `0 missing, 0 unexpected` and nothing
  else. The fix improves both; the starting points differ.

### 2.4 Scope recommendation

**Option 1 — one shared classifier, three separate renderers.** Recommended.

| Element | Placement | Shared? |
|---|---|---|
| `WHOLE_ROW` sentinel, `COMPARISON_KEY` (16 entries) | `verify_provisioning.py`, beside `SNAPSHOT_QUERIES` | **yes** |
| `classify_dimension(dimension, expected, actual) -> ClassifiedDimension` — pure, no I/O | `verify_provisioning.py` | **yes** |
| Rendering + severity routing + caps + labels | each caller's existing function, unchanged in contract | **no** |

This satisfies "do not duplicate the classifier logic across three implementations" while
leaving each path's observable contract intact apart from the per-dimension detail text.

**Option 2 — repair `compare()` only, track the other two as open defects.** Viable and
smaller. Its cost is that `diff_snapshots()` and `check_drift()` keep a known-wrong diff, and
the three would then hold two different diff algorithms — the condition that produced the
defect in triplicate.

**My recommendation is Option 1 with staged landing:** introduce the classifier and route
`compare()` through it in one commit; route `diff_snapshots()` and `check_drift()` in a second,
separately reviewable commit. That gets the shared implementation without a single large diff
across three tools, and the second commit can be deferred without leaving duplicate logic
behind — only unconverted callers.

**Scope status: `UNRESOLVED`.** No owner decision exists (§6). The above is a recommendation.

---

## 3. Output and verdict decision matrix

Baseline is W1D-09 §3–§4, not silently redesigned. New here: the per-case exit behaviour and
the test that pins each row.

`v` = dimension verdict. Process exit: `0` no drift, `1` drift, `2` could not run — today's
semantics, preserved.

| # | Case | Result object | Rendered detail | `v` | Exit | Regression test |
|---|---|---|---|---|---|---|
| 1 | **Undeclared key** | `notes=["UNDECLARED DIMENSION"]`, all buckets empty | `UNDECLARED DIMENSION -- no comparison key; not classified` | `inconclusive` | **1** (non-advisory) / advisory routing if advisory | `test_an_undeclared_dimension_is_inconclusive` |
| 2 | **Invalid key** (not `WHOLE_ROW`, not `int` in `1..len(cols)`, or `None`) | `notes=["INVALID KEY DECLARATION: <v>"]` | `INVALID KEY DECLARATION <value>` | `inconclusive` | **1** | `test_an_invalid_key_declaration_is_inconclusive` |
| 3 | **Unique keyed rows, all equal** | `unchanged=n` | *(nothing)* | `same` | 0 | `test_identical_snapshots_report_nothing` |
| 4 | **Matched key, non-key attribute changed** | `modified=[{key, changes:[{index, was, now}]}]` | `MODIFIED <key>` + `attr[i]: <was> -> <now>` | `drift` | 1 | `test_a_modified_attribute_is_one_modified_not_a_removal_and_addition` |
| 5 | **Key only in baseline** | `missing=[row]` | `MISSING from the database : <row>` | `drift` | 1 | `test_a_genuine_removal_is_missing` |
| 6 | **Key only in live** | `added=[row]` | `NOT IN this repository    : <row>` | `drift` | 1 | `test_a_genuine_addition_is_added` |
| 7 | **>1 row sharing a key, different attrs** (collision) | `ambiguous=[{key, baseline, live}]` + multiset `missing`/`added` for that group; **`modified` stays empty for it** | `AMBIGUOUS KEY <key>: baseline=n, live=m  -- not classified; compared as whole rows` | `drift` **and** flagged ambiguous | 1 | `test_two_distinct_objects_sharing_a_key_are_not_one_modification` |
| 8 | **Identical duplicates, equal multiplicity** | `unchanged=n` (counts both) | *(nothing)* | `same` | 0 | `test_equal_duplicate_multiplicity_is_unchanged` |
| 9 | **Identical duplicates, differing multiplicity** | `duplicated=[{subject, baseline, live}]` | `DUPLICATED <subject>: baseline=n, live=m` | `drift` | 1 | `test_differing_duplicate_multiplicity_reports_counts` |
| 10 | **Whole-row dimension, any change** | `missing`/`added` by multiset; **`modified` always empty** | `MISSING…` / `NOT IN…` | `same`/`drift` | 0/1 | `test_a_whole_row_dimension_never_reports_modified` |
| 11 | **Advisory dimension, any change** | as per its case above | same text | `drift` but **routed to advisories** | **0** — advisory alone must not fail | `test_advisory_dimensions_stay_advisory` |
| 12 | **Reordered, identical content** | `unchanged=n` | *(nothing)* | `same` | **0** — **behaviour change**, see §6 | `test_reordered_identical_rows_are_not_drift` |
| 13 | **`None`-valued attributes** | normal classification | `attr[i]: None -> ''` | per case | per case | `test_output_is_deterministic_with_null_attributes` |
| 14 | **Summary marker vs detail** | — | per-dimension marker must equal the detail verdict | — | — | `test_the_summary_marker_agrees_with_the_detail` |

### 3.1 The sorting hazard, restated because it is a real defect a naive fix introduces

`_norm()` preserves `None`, and in Python 3 `None < 'x'` raises `TypeError`. Deterministic
ordering therefore **must** sort on a null-safe projection, e.g. each element mapped to
`(element is None, str(element))`. `columns` has seven attribute columns of which four are
**not** `coalesce(...)`-guarded, so a `NULL` is reachable. A plain `sorted()` over normalised
tuples would crash on real data. Case 13 exists to pin this.

### 3.2 Invariant that kills the original symptom

If `v != "same"`, at least one of `modified`, `missing`, `added`, `duplicated`, `ambiguous`,
`notes` must be non-empty. This is the explicit fix for today's
`columns: 0 missing, 0 unexpected` — a hard failure naming nothing. It should be asserted
across **all** cases, not just the ones that trigger it.

### 3.3 Compatibility rules (from W1D-09 §4.5, evidence unchanged)

- `RESULT: NO DRIFT` and `RESULT: DRIFTED` **verbatim** — 4 launch documents quote them as
  evidence (`DATABASE_PROVISIONING_RUNBOOK.md`, `FINAL_LAUNCH_READINESS_REPORT.md`,
  `PAPER_ONLY_LAUNCH_READINESS.md`, `RLS_TENANT_ISOLATION_VERIFICATION.md`). No machine parses
  them.
- Per-dimension detail lines are free to change — **no code consumer parses them**.
- Exit codes 0/1/2 and advisory routing preserved.
- `check_drift()`'s 3-tier severity routing preserved exactly (§2.3).
- Legacy counts stay derivable as `missing + modified` and `added + modified` **except** cases
  9 and 12, where today's output is wrong or empty-handed. That is the only intentional count
  divergence.

---

## 4. Existing versus missing test coverage

### 4.1 W1D-09 acceptance criteria mapped

| W1D-09 # | Criterion | Static coverage today | Runtime coverage today | Action |
|---|---|---|---|---|
| 1 | New dimension with no declaration | **YES** — `test_every_dimension_declares_a_comparison_key`; mutation **M5** bites | **none** | add case 1 |
| 2 | Missing/invalid declaration | **YES** — `test_a_declared_key_is_whole_row_or_a_valid_width`, `test_control_a_none_key_is_distinguishable_from_an_absent_one`; **M7**, **M8** | **none** | add case 2 |
| 3 | Key collision not reported as one modification | no | **none** | **add case 7** |
| 4 | Equal duplicate multiplicity → unchanged | no | **none** | add case 8 |
| 5 | Differing multiplicity → counts | no | **none** | **add case 9** |
| 6 | Non-key attribute change classified and identified | no | **none** | **add case 4** |
| 7 | Whole-row never `modified` | **PARTLY** — `test_whole_row_dimensions_really_have_no_attribute_columns`; **M9** | **none** | add case 10 |
| 8 | Deterministic output | no | **none** | add case 13 (must include `None`) |
| 9 | Backward compatibility | **PARTLY** — advisory routing demonstrated correct by the W1D-06x probe | **none** | add cases 11, 3.3 assertions |
| 10 | Inconclusive when evidence insufficient | no | **none** | add cases 1, 2 |

**Do not duplicate:** `tests/test_snapshot_dimension_invariants.py` covers both static
invariants at 48/48 with 12 mutations biting (W1D-08). Its one change under AP-W1D-3 is to
**import** `COMPARISON_KEY` from `verify_provisioning` instead of defining it locally, which
converts the guard from checking a test constant to checking production data.

### 4.2 The W1D-06x prototype — confirmed against current source, with its gaps recorded

The concurrent session's prototype (`p11b_w1d06x_prototype.txt`, 21:22) exercises 13 cases
with 0 problems and validates against the real 396 KB snapshot: self-comparison clean, all 16
markers `same`, one real policy attribute change → `1 modified`, all 188 real policies
reversed → no drift. Its cases map to my matrix as follows:

| Prototype case | Matrix case | Adopt? |
|---|---|---|
| D1 modified attribute | 4 | yes |
| D2 reordered identical | 12 | yes — **but it is the approval-gated behaviour change** |
| D3 / D3b duplicate one side | 9 | yes |
| genuine deletion / addition | 5 / 6 | yes |
| identical / both empty / one-sided-empty | 3 | yes |
| mixed unchanged+modified+missing+added | 3–6 combined | yes |
| null identical / null → non-null | 13 (partly) | yes |
| duplicate **key** reported not collapsed (its §4) | 7 | yes — this is the collision case |

**Prototype coverage gaps**, which is why it is a starting point and not the test suite:

1. **No undeclared / invalid declaration case** — matrix cases 1 and 2 are absent. The
   prototype assumes a well-formed key map.
2. **No determinism-under-repetition assertion.** It prints results once; it does not run twice
   and compare, and its null cases do not force the `None`-vs-`str` sort path (§3.1).
3. **No summary-marker-agreement assertion.** It *observes* the inconsistency at
   `verify_schema_drift.py:269` in prose but does not assert the fixed behaviour.
4. **No exit-code or verdict-line assertions** — compatibility is argued, not pinned.
5. **Collision case covers duplicates with identical attributes**; matrix case 7 requires two
   rows sharing a key with **different** attributes, which is the dangerous variant.
6. **Single-dimension inputs.** It does not test a snapshot where several dimensions each hit a
   different case simultaneously, which is how a real run behaves.

Its code must not be copied blindly for those reasons. Its *evidence* — the duplicate census,
the `privileges` by-construction finding, the real-snapshot validation — is sound and is reused
in §3 and W1D-09 §5.

### 4.3 Proposed new test file

`tests/test_drift_classification.py` — 14 cases from §3 plus the §3.2 invariant asserted
across all of them, pure data, no gate, no skip, no database. **Not created in this phase.**

---

## 5. Unresolved owner decisions

| # | Decision | Options | Recommendation | Status |
|---|---|---|---|---|
| **D1** | **Ambiguous-key findings: hard failure or advisory?** | hard failure with a distinct label / advisory | **Hard failure.** A collision means either the database holds unexpected duplicates or the declared key is wrong; both need a human. It must be *labelled* distinctly so it is not mistaken for a schema change. | **UNRESOLVED** |
| **D2** | **Reordered-identical rows: keep today's hard failure, or treat as clean?** | keep / change to clean | **Change to clean.** Identical content in a different order is not drift. W1D-05's Guard A means no current dimension can reach that path, so this changes no observable behaviour today — it removes a trap. It is still the only intentional verdict change and needs explicit acceptance. | **UNRESOLVED** |
| **D3** | **Scope: all three call paths, or `compare()` only?** | Option 1 (shared classifier, 3 renderers) / Option 2 (`compare()` only) | **Option 1, landed in two commits** (§2.4) | **UNRESOLVED** |
| **D4** | Classifier placement | `verify_provisioning.py` / new `scripts/_drift_classify.py` | `verify_provisioning.py` — acyclic, no new import cost, beside the key map it needs | **UNRESOLVED** (technically constrained: it cannot be `verify_schema_drift.py`) |
| **D5** | Does `check_drift()`'s 3-tier severity routing stay byte-identical? | yes / revisit | **Yes, untouched.** Revisiting it is a production-audit policy change, not W1C-02. | **UNRESOLVED** |

None of these is granted. A specification recommendation is not a decision.

---

## 6. Authorization status and the exact next permitted action

### 6.1 Rechecked in this phase

```
staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression tests | Code owner | **NOT GRANTED** |

git log --all -i --oneline --grep=AP-W1D   ->   no matching commit
```

A regex sweep of `staging/` for scope-grant language returned 12 candidates; on inspection
**all 12 are reports discussing scope or authorization**, including four of my own
(`W1D07`, `W1D08`, `W1D09`) and unrelated workstreams (`PHASE10_EXCH01`, `PHASE11B_TENANT01`,
`PHASE11B_A20Q`, `PHASE11B_A23C`). **Zero grants.** As in W1D-07, the coarse count should not
be quoted as a signal — the conclusion comes from reading the contexts.

AP-W1D-1 through AP-W1D-5 are all recorded NOT GRANTED in the same table.

### 6.2 The exact next permitted action

**Nothing further is permitted read-only.** The specification (W1D-09) and this readiness plan
are complete; there is no remaining analysis that would change the implementation.

The next action is a human reply granting, in one message:

> **AP-W1D-3 granted** for `scripts/verify_provisioning.py` (add `WHOLE_ROW`,
> `COMPARISON_KEY`, `classify_dimension`), `scripts/verify_schema_drift.py` (route
> `compare()` through it), `tests/test_snapshot_dimension_invariants.py` (import the
> production key map), and a new `tests/test_drift_classification.py`.
> **D1** = ambiguous is a hard failure. **D2** = accept the reorder change.
> **D3** = Option 1, second commit for `diff_snapshots()` / `check_drift()`.

Implementation then requires a **separately authorized task** — this prompt is
investigation-only and I am not treating it as a grant.

Independently of AP-W1D-3, and needing no code owner: **AP-W1D-1 / AP-W1D-5** would settle
whether any declared key is actually unique in a live catalogue (verified for 0 of 16). The
classifier is designed to stay correct without that evidence; obtaining it would let `modified`
be *trusted* rather than merely *safe*.

---

## 7. Preservation evidence and concurrent work

### 7.1 Entry = exit

| Measurement | Value (entry and exit) |
|---|---|
| `HEAD` / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` |
| `origin/main` divergence | `0 0` |
| Worktrees | **1** (`main`) |
| Tracked entries | 2104 |
| Staged paths | **22** |
| Unstaged tracked | **102** |
| Untracked | 3542 (this session's `staging/` artifacts included) |
| Unmerged | **0** |
| Stash | 2 |
| **Digest A** (`sha256`, raw `git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` — **SAME** |
| **Digest C** (`sha256`, sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` — **SAME** |
| **Index tree** (isolated index + isolated object dir) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` — **SAME** |
| Objects into the isolated dir | **0** |
| Shared `.git/objects` | **198 → 198** |

The `.git/index` file hash was deliberately not used as the invariant — `core.autocrlf=true`
lets read-only `git status` rewrite its stat cache.

### 7.2 Concurrent work — no conflict on this phase's files

Active concurrent sessions are working an **unrelated** Redis-recovery line (A23):
`PHASE11B_A23C_REDIS_RECOVERY_CONSOLIDATION.md` (22:19),
`PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` (22:16),
`PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` (22:12), plus `p11b23_*` and
`p11b_a23r_*` probes. **None touches the drift/provisioning scripts.**

All four candidate fingerprints are unchanged from W1D-09 (§1), so no competing edit exists on
the files this plan would modify.

The `unstaged` count has been **102** since W1D-08, having risen from 100 during W1D-07. The
additions are other workstreams': `backend_app/core/subscription_engine.py`,
`backend_app/backend/order_watchdog.py`, `tests/test_redis_debounce.py`. **Not mine**, and no
candidate file of this phase is in the unstaged set.

The W1D line now has **six** parallel efforts recorded (W1D-05, W1D-06, W1D-06x, W1D-07/08,
W1D-09, this phase). Consolidation remains an open coordination item.

### 7.3 Artifacts created, all untracked and under `staging/`

```
PHASE11B_W1D10_CLASSIFIER_IMPLEMENTATION_READINESS.md   (this report)
w1d10_callgraph.py                                       call graph, import DAG, renderer contrasts
w1d10_preflight.py, w1d10_preflight.txt                  destination, authorization, baseline, concurrency
```

---

## 8. Confirmations

This phase did **not**: edit any production script, test, migration, snapshot or shared
configuration; create or modify a test; add a production key map; connect to PostgreSQL or
Redis; recapture a snapshot; make a network call; install a package; start a server; trigger
CI; stage, commit, push, reset, stash or switch branches; mutate the shared Git index; write to
the shared Git object database (198 → 198, verified with an isolated object directory);
overwrite any existing report; or alter another workstream's artifacts.

No static test result is offered as evidence of database uniqueness or production correctness.
No PostgreSQL catalogue claim is made.

---

## 9. Disposition

| Item | Status |
|---|---|
| **W1C-02** | **OPEN** — remains open until an authorized implementation is completed and verified |
| **AP-W1D-3** | **NOT GRANTED** — rechecked; no commit, no record, no supersession |
| **Scope (D3)** | **UNRESOLVED** — Option 1 recommended, two-commit landing |
| **D1 / D2 / D4 / D5** | **UNRESOLVED** — recommendations recorded, none granted |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

Planning is complete. **Implementation does not follow automatically** and requires a
separately authorized task. Nothing in this report constitutes an approval.
