# Phase 11B / W1D-06 — W1C-02 Drift-Reporting Investigation and Remediation Readiness

**Date:** 2026-10-10
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **REPRODUCED**

W1C-02 is reproduced against the current source, in memory, with no database. The root cause
is confirmed, the minimal correction is defined, and a working classifier has been
demonstrated side by side with the real `compare()`. **No production code, test, snapshot,
migration or shared configuration was modified.**

| | |
|---|---|
| W1C-02 | **OPEN** — reproduced, remediation specified, **not implemented** |
| W1D-05 static guards | **CLOSED and not defective** — the defect is entirely downstream |
| PostgreSQL integration lane | **UNEXECUTED** |
| Live database verification | **NOT PERFORMED** |
| TENANT-01 / QUOTA-01 / D5 Redis | **untouched** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## HEADLINE

1. **W1C-02 reproduced from pure data.** `compare()` is a pure function over two dicts, so
   the misleading report needs no PostgreSQL. An attribute change on an object present on
   both sides prints `policies: 1 missing, 1 unexpected` — claiming the object is
   simultaneously absent from the database and foreign to the repository (§3).
2. **Root cause: key construction + comparison logic.** `compare()` has no notion of object
   identity; it tests whole-tuple membership, so it cannot pair an old and a new version of
   the same row. Not query coverage, not snapshot shape, not null handling (§4).
3. **Two further defects found in the same code, beyond W1C-02's scope as written.** A
   multiplicity difference, and (hypothetically) a reordering, both produce a **hard failure
   that names nothing at all**: the literal text `columns: 0 missing, 0 unexpected`. The
   multiplicity variant does not depend on ordering, so W1D-05's Guard A does not cover it
   (§3.2).
4. **The defect is in three production functions, not one.**
   `verify_schema_drift.compare()`, `verify_provisioning.diff_snapshots()` and
   `verify_production_schema.check_drift()` all carry the identical pattern (§5).
5. **No test anywhere invokes any of them.** The comparison logic is entirely unguarded, so
   the proposed regression test is new coverage rather than a duplicate (§5.2).
6. **The proposed classifier is demonstrated, not described** — and I am reporting one
   intentional verdict change and one rough edge in my own proposal rather than presenting
   it as clean (§6.3).

---

## 1. Entry and exit preservation measurements

Identical at entry and exit.

| Measurement | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| `origin/main` / divergence | `074b772a…` / `0 0` | **same** |
| Tracked index entries | 2104 | 2104 |
| Staged paths | 22 | 22 |
| Unstaged tracked | 100 | 100 |
| Unmerged | 0 | 0 |
| Stash | 2 | 2 |
| **Digest A** (`git ls-files -s`, raw lines, file order) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **SAME** |
| **Digest C** (sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **SAME** |
| **Index tree** (`git write-tree`, isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **SAME** |
| Shared `.git/objects` | 198 | **198 — delta 0** |
| Untracked | 3436 | 3464 (this phase's own `staging/` artifacts) |

The index tree matches the value recorded in `PHASE11B_W1D05_STATIC_GUARDS_REPORT.md` §1,
which is a cross-workstream corroboration rather than a self-comparison.

**Digest method, stated so it is compared only like with like.** Digest A is
`sha256` of the newline-joined raw `git ls-files -s` output in file order — the method this
session has used since A9. W1D-05 and the PG workstream quote
`2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` under a different
serialisation (`git diff --cached --raw`, normalised). **Those two numbers are not
comparable and neither is wrong.** The only invariant that has reproduced across
independent workstreams is `git write-tree` = `6f40856c…`, which is a property of content
rather than of a serialisation choice, and it is the one to rely on.

### Isolation of the tree verification

The brief requires the index tree be verified without writing verification objects to the
shared store. Both the index **and** the object directory were isolated:

```
GIT_INDEX_FILE                   = <temp copy of .git/index>
GIT_OBJECT_DIRECTORY             = <temp dir>
GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects    # read-only fallback
git write-tree  ->  6f40856cee2bc7ea4d741f69f4b1f68b463d70a4
objects written to the isolated dir : 0
shared .git/objects                 : 198 -> 198
```

Zero objects were created even in the isolated directory — every tree for the current
staged state already exists and was found through the alternate, so `write-tree` was a
genuine no-op. Both temp directories were removed and their absence asserted.

### Concurrent activity observed

`git worktree list` at entry showed a **second worktree** belonging to another workstream:

```
C:/aerora_quant_backend_updated_final1              074b772a [main]
C:/Users/aa/AppData/Local/Temp/a18_pr6_wt           72bae29b [ci/d5-real-redis-quota-evidence]
```

That is the D5′ Redis workstream, at a commit (`72bae29b`) that did not exist earlier in
this session — so D5′ has advanced. **I did not touch it**, and it has no bearing on W1C-02.
It is recorded because the brief forbids changing the D5 workstream and this is evidence
that it is live. By exit the list showed only the main worktree, so that temporary worktree
was removed by its owner during this phase.

### Target files — untouched

| Path | Status | Size | Blob hash (entry = exit) |
|---|---|---|---|
| `scripts/verify_provisioning.py` | `??` untracked | 16,404 | `51cb6d241de3ef437b662241379f8985f7d5a51e` |
| `scripts/verify_schema_drift.py` | `??` untracked | 12,170 | `f78b35a52552967d469a7cc7d33de92db0a65e3b` |
| `tests/test_snapshot_dimension_invariants.py` | `??` untracked | 28,067 | `bcb4f479fca2365cca2be61aae8961449e03beb9` |

---

## 2. Correction to one premise in the brief

The brief locates `SNAPSHOT_QUERIES` in `verify_provisioning.py` — correct — and asks for
`verify_schema_drift.py`'s "query execution, row normalization, comparisons, and drift
classification". Two clarifications from the source:

- **There is no drift classification today.** `compare()` produces only two buckets,
  `missing` and `extra`. The four-way classification the brief implies does not exist; that
  absence *is* W1C-02.
- **Row normalisation is not implicated.** `_norm()` at `verify_provisioning.py:200` is
  `value if isinstance(value, (str, int, float, bool, type(None))) else str(value)` — it
  preserves `None` as `None` on both sides, and `_as_tuples()` already reconciles JSON's
  list/tuple coercion. I tested both null cases explicitly (§6.2) and neither is the cause.

---

## 3. Confirmed root cause and minimal reproduction

### 3.1 W1C-02 — the misleading report

Reproduced by calling the **real** `scripts.verify_schema_drift.compare()` on in-memory
dicts, under `PGHOST=255.255.255.255 PGPORT=1 PGCONNECT_TIMEOUT=1`. Minimal case — one RLS
policy whose `WITH CHECK` expression changed, key `(tablename, policyname)`:

```
baseline : ('fee_records', 'fee_select', 'PERMISSIVE', '{authenticated}', 'SELECT', '(uid = x)', '')
live     : ('fee_records', 'fee_select', 'PERMISSIVE', '{authenticated}', 'SELECT', '(uid = x)', '(a = b)')

OUTPUT TODAY:
  policies: 1 missing, 1 unexpected
      MISSING from the database : ('fee_records', 'fee_select', …, '')
      NOT IN this repository    : ('fee_records', 'fee_select', …, '(a = b)')
```

**Expected behaviour:** the policy exists on both sides; exactly one attribute changed. It
should be reported once, as modified, naming the changed attribute.

**Why it misleads, concretely.** The same object is asserted to be *absent from the
database* and *not part of this repository* at the same time — two mutually exclusive
claims about one policy that is in fact present in both. The script's own remediation text
then instructs the reader to "write the missing migration", which for this finding would
mean issuing `CREATE POLICY` for a policy that already exists. The correct action is an
`ALTER`/`DROP`+`CREATE` of an existing object, or a baseline refresh.

### 3.2 Two further defects with a worse signature

Both produce a **hard failure that names no rows**:

```
reordered rows, identical content      -> "columns: 0 missing, 0 unexpected"   (1 failure line, nothing else)
duplicate multiplicity differs         -> "columns: 0 missing, 0 unexpected"   (1 failure line, nothing else)
```

Mechanism: `expected == actual` is list equality, so it is order- and multiplicity-sensitive
and the fast path is skipped. The fallback `[r for r in expected if r not in actual]` is
membership, so it is order- and multiplicity-**insensitive** and both lists come back empty.
The run exits 1 having printed a drift it cannot describe.

**These are not the same risk.** W1D-05's Guard A proves all 16 current queries carry a
total `ORDER BY`, so the reorder variant is unreachable today — it is a latent trap, not a
live defect. The **multiplicity variant does not depend on ordering at all** and is
therefore reachable now if any dimension can ever return a duplicate row. Whether one can
is a live-catalogue question I cannot answer statically (§8).

### 3.3 Reproducible without a database — confirmed

`compare(baseline, live)` takes two `dict[str, list]`. It performs no I/O. Importing both
modules under a routed-to-nowhere `PGHOST` completed in **1.334 s** and `compare()` ran
with no connection attempt. Every case in §6 is pure in-memory data, so the regression test
can run in the default lane with **no gate and no skip**.

### 3.4 Do W1D-05's guards detect this? No — and they are not defective

The guards constrain the **SQL** (every dimension has a total `ORDER BY`; every dimension
declares a key width). The defect is in how two already-ordered result sets are **diffed**.
Guard A is what keeps §3.2's reorder variant unreachable, so the guards actively help; they
are simply not positioned to catch W1C-02. **No change to W1D-05 is proposed, and its
48/48 still pass (§7).** W1D-05's own report §8.2 already states this limitation.

---

## 4. Root-cause classification

Against the options the brief lists:

| Candidate cause | Verdict | Evidence |
|---|---|---|
| Query coverage | **No** | all 16 dimensions are queried and compared |
| Row ordering | **No** (today) | Guard A proves a total `ORDER BY` on all 16; shares §3.2's signature but cannot fire |
| **Key construction** | **YES** | `compare()` has no key concept; whole-tuple membership cannot pair an object's old and new versions |
| Missing-versus-null handling | **No** | `_norm()` preserves `None`; both null cases classify correctly under the proposal (§6.2) |
| Snapshot shape | **No** | list-of-tuples is adequate; `_as_tuples()` already handles JSON coercion |
| **Comparison logic** | **YES** | set-style membership instead of a keyed, multiset-aware diff |

Primary cause: **comparison logic, for want of a key declaration in production code.** The
key widths already exist as a reviewed constant — but in the *test file*
(`COMPARISON_KEY`), because W1D-05 was forbidden from editing `scripts/`.

---

## 5. Relevant source and test locations

### 5.1 The defect signature, repository-wide

Pattern `[r for r in X if r not in Y]` — whole-row membership diffing:

| Location | Function | Bearing |
|---|---|---|
| `scripts/verify_schema_drift.py:203-204` | `compare()` | **W1C-02 subject** |
| `scripts/verify_provisioning.py:240-241` | `diff_snapshots()` | **same defect** — reproducibility diff between two provisioning runs |
| `scripts/verify_production_schema.py:242-243` | `check_drift()` | **same defect** — a third consumer |
| `scripts/post_migration_validation.py:91` | `validate_production_db()` | **not affected** — compares RPC *names*, where the name is the whole identity |
| `tests/test_snapshot_dimension_invariants.py:19-20` | module docstring | **not a defect** — W1D-05 quotes `compare()`'s body to explain what it guards |

Four consumers of `SNAPSHOT_QUERIES` exist: `verify_provisioning.py` (definition, line 68,
plus `diff_snapshots`), `verify_schema_drift.py` (import line 69, two sites),
`verify_production_schema.py` (import line 100, two sites), and W1D-05's test file.

### 5.2 Existing test coverage: none

Three test files reference these modules — `test_bug049_sql_and_alembic_agree.py`,
`test_schema_drift_checker_is_read_only_postgres.py`,
`test_snapshot_dimension_invariants.py` — but **no test in the repository invokes
`compare()`, `diff_snapshots()` or `check_drift()`.** Searched for `= compare(`,
`compare(baseline`, `compare({`, `diff_snapshots(`, `check_drift(`: zero matches.

The comparison logic is therefore entirely unguarded. The proposed regression test is new
coverage, and its absence explains how W1C-02 survived.

---

## 6. Proposed minimal correction

**Not applied.** This phase is read-only.

### 6.1 Exact files and functions

| # | File | Change |
|---|---|---|
| 1 | `scripts/verify_provisioning.py` | **Add** `COMPARISON_KEY: dict[str, int \| str]` and `WHOLE_ROW = "whole-row"` immediately after `SNAPSHOT_QUERIES` (line 68). 16 entries, values already reviewed by W1D-05. **No existing code changes.** |
| 2 | `scripts/verify_schema_drift.py` | **Replace** the body of the per-dimension diff inside `compare()` (lines ~203-215) with a keyed, multiset-aware classifier returning `unchanged / modified / missing / added`, and extend the printed lines to show a modified object **once** with its changed attributes. Import `COMPARISON_KEY` alongside the existing `SNAPSHOT_QUERIES, _norm`. |
| 3 | `tests/test_snapshot_dimension_invariants.py` | **Change the test-local `COMPARISON_KEY` to an import** from `verify_provisioning`, so W1D-05's guards begin checking production data instead of a test constant. This is the end state W1D-05 §3.1 explicitly anticipated. |
| 4 | `tests/test_drift_classification.py` | **New** — the regression test in §6.4. |

Deliberately **excluded** from the minimal change, and why:

- `verify_provisioning.diff_snapshots()` and `verify_production_schema.check_drift()` carry
  the same defect. Fixing all three at once is larger than W1C-02 and touches two more
  consumers' output formats. **Owner decision required (§9).** My recommendation: extract
  the classifier into one shared helper in `verify_provisioning.py` and have all three call
  it, because three independent implementations of the same diff is how this defect arose
  in triplicate. But that is a scope call, not mine.
- No change to the 16 SQL queries, the snapshot format, migrations, or W1D-05's guards.

### 6.2 Demonstrated behaviour, measured side by side

The classifier was implemented in `staging/probe_p11b_w1d06_proposed.py` and run against
the real `compare()` on identical inputs:

| Case | Today | Proposed | Verdict |
|---|---|---|---|
| **W1C-02 — attribute modified** | `1 missing, 1 unexpected` | `1 modified, 0 missing, 0 added` + `attr[4]: '' -> '(a = b)'` | SAME (drift) |
| duplicate multiplicity | `0 missing, 0 unexpected` | `1 modified, 0 missing, 0 added, 1 unchanged` | SAME (drift) |
| reordered rows | `0 missing, 0 unexpected` (**hard failure**) | *(no drift)* | **CHANGED** — see §6.3 |
| genuine removal | `1 missing, 0 unexpected` | `0 modified, 1 missing, 0 added` | SAME |
| genuine addition | `0 missing, 1 unexpected` | `0 modified, 0 missing, 1 added` | SAME |
| unchanged | no drift | no drift | SAME |
| both empty | no drift | no drift | SAME |
| whole-row dim changed (`privileges`) | `1 missing, 1 unexpected` | `0 modified, 1 missing, 1 added` | SAME — correct, there is no key to pair on |
| advisory dim modified (`recorded_migrations`) | `1 missing, 1 unexpected` → advisories | `1 modified` + `attr[0]: 'abc123' -> 'def456'` → advisories | SAME, still advisory |
| `None` → `''` | `1 missing, 1 unexpected` | `1 modified` + `attr[0]: None -> ''` | SAME |
| `'integer'` → `None` | `1 missing, 1 unexpected` | `1 modified` + `attr[0]: 'integer' -> None` | SAME |

### 6.3 Two honest caveats about my own proposal

**(a) One verdict changes, deliberately.** The reorder case goes from hard failure to
clean. That is the correct answer — identical content in a different order is not drift —
but it is a behaviour change, and it is the only one. It can only turn a currently-failing
run green in a case that was never real drift, and Guard A means no current dimension can
reach it. **It still needs to be an accepted change rather than a silent one.**

**(b) My demo renderer has a gap on pure multiplicity.** When only the row *count* differs,
it prints `MODIFIED ('t1','c1')` with no attribute delta, because the attribute tuples are
identical and the Counter difference carries no old/new pair. That is better than today's
empty-handed failure but still imprecise; the real implementation should say
`appears 2x in baseline, 1x in live`. Recorded as a required refinement, not glossed over.

### 6.4 Regression test — fails today, passes after

New file `tests/test_drift_classification.py`, pure data, no gate, no skip:

| Test | Asserts | Fails today because |
|---|---|---|
| `test_a_modified_attribute_is_reported_as_modified_not_as_removal_and_addition` | a `policies` row differing only in `with_check` yields 1 modified, 0 missing, 0 added | today yields 1 missing + 1 added |
| `test_a_modified_object_is_named_once` | the object's key appears exactly once in the rendered lines | today it appears in two unlinked lines |
| `test_the_changed_attribute_is_identified` | output names the changed column index and old → new values | today prints whole tuples with no delta |
| `test_drift_is_never_reported_without_naming_rows` | for every case where a failure line is emitted, at least one row/key is named | today emits `0 missing, 0 unexpected` |
| `test_duplicate_multiplicity_is_reported` | `[A, A]` vs `[A]` names A | today names nothing |
| **Negative controls** | | |
| `test_a_genuine_removal_is_still_missing` | 1 missing, 0 modified | passes today — must keep passing |
| `test_a_genuine_addition_is_still_added` | 1 added, 0 modified | passes today |
| `test_identical_snapshots_report_nothing` | no lines | passes today |
| `test_empty_dimensions_report_nothing` | no lines, both sides empty | passes today |
| `test_a_null_attribute_change_is_modified` | `None` → `''` is 1 modified | today 1 missing + 1 added |
| `test_a_whole_row_dimension_has_no_modified_bucket` | `privileges` change → 1 missing + 1 added, 0 modified | passes today |
| `test_advisory_dimensions_stay_advisory` | `recorded_migrations` routes to advisories, not failures | passes today — must keep passing |
| `test_every_dimension_has_a_comparison_key` | `COMPARISON_KEY` covers `SNAPSHOT_QUERIES` exactly | becomes a production-data check once item 3 lands |

### 6.5 Compatibility

- **Snapshot files:** unchanged. No format change; existing `staging/provisioning_snapshot.json`
  is read as-is. The key declaration is metadata about columns, not part of the snapshot.
- **Exit codes:** unchanged in every case except §6.3(a).
- **Report text:** changes. Any consumer parsing the literal string
  `"N missing, M unexpected"` would break. Searched: the only references are in `staging/`
  reports and probes — **no code or test parses it**.
- **Advisory routing:** unchanged; `ADVISORY_DIMENSIONS` keeps its meaning.
- **Aggregate reconciliation:** `missing + modified` equals today's `missing`, and
  `added + modified` equals today's `extra`, for every keyed case — so historical counts in
  prior reports remain interpretable.

### 6.6 Rollback

Items 1–4 are additive or self-contained. Reverting item 2 restores the previous `compare()`
body; items 1 and 4 can remain harmlessly (an unused constant and a test that would then
fail, so item 4 should be reverted with item 2). Item 3 must be reverted with item 1. No
data migration, no snapshot regeneration, no state to unwind — the change is confined to
how two in-memory lists are compared and printed.

---

## 7. Commands run, with exact counts

All read-only. No database, Redis, migration, role or grant operation.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `python staging/w1d06_phase0.py` (entry) | 0 | baseline §1; destination FREE |
| 2 | `python staging/probe_p11b_w1d06_repro.py` | 0 | **D1, D2, D3 all REPRODUCED** against real `compare()` |
| 3 | `python staging/probe_p11b_w1d06_sites.py` | 0 | 16 signature occurrences; 3 affected production functions; **0 tests invoke them** |
| 4 | `python staging/probe_p11b_w1d06_proposed.py` | 0 | 11 cases measured; P1 YES, P2a YES, P2b YES, P3 **NO** (one intentional change, §6.3a) |
| 5 | `python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **48 passed, 0 failed, 0 skipped** in 5.27s |
| 6 | `python -m pytest` on the 5-file drift group (`test_snapshot_dimension_invariants`, `test_schema_drift_checker_is_read_only_postgres`, `test_migration_tooling`, `test_orm_models_match_provisioned_schema`, `test_bug049_sql_and_alembic_agree`) `-q --no-header -rs` | **0** | **103 passed, 12 skipped, 1 xfailed, 0 failed** in 28.48s |
| 7 | `python staging/w1d06_phase0.py` (exit) | 0 | all invariants SAME |

### 7.1 The 12 skips are not evidence

All 12 are `tests/test_schema_drift_checker_is_read_only_postgres.py`, every one reporting:

> *needs a real PostgreSQL instance: `default_transaction_read_only` and SQLSTATE 25006 do
> not exist in SQLite, so the read-only guarantee cannot be measured there.*

They are **deliberately not run** — this phase is forbidden from contacting a database — and
are **not counted as passes**. The 1 xfail is `test_only_one_model_claims_the_positions_table`,
a pre-existing strict marker, unrelated.

### 7.2 The four evidence classes, kept separate

| Class | Status from this phase |
|---|---|
| **Static invariant correctness** | **VERIFIED** — 48/48 guards pass, 0 skips, no database |
| **Drift-comparison correctness** | **DEFECT REPRODUCED** — in memory, against current source; correction demonstrated but **not implemented** |
| **Actual PostgreSQL catalog correctness** | **NOT ESTABLISHED** — nothing here reads a catalogue |
| **Live database verification** | **NOT PERFORMED** — no connection attempted; PG lane UNEXECUTED |

Neither of the last two can be inferred from the first two, and nothing in this report does so.

---

## 8. Remaining uncertainties

1. **Can any dimension actually return duplicate rows?** This decides whether §3.2's
   multiplicity defect is live or latent. It is a live-catalogue property and cannot be
   settled statically. If every dimension's declared key is genuinely unique, duplicates are
   impossible and the defect is latent.
2. **Are the declared key widths genuinely unique in PostgreSQL?** W1D-05 §8.1 records the
   same limitation. The proposal inherits those widths; a wrong width would let two distinct
   objects collide into one key and be reported as modified rather than as a removal plus an
   addition. Needs a live database to confirm.
3. **Scope: one function or three?** `diff_snapshots()` and `check_drift()` are equally
   affected. Owner decision (§9).
4. **Does any external consumer parse the report text?** Searched the repository: no. An
   out-of-tree consumer (a CI job, a runbook) would not be visible to me.
5. **The reorder verdict change** (§6.3a) needs explicit acceptance.

---

## 9. Required approvals

| # | Decision | Owner | Status |
|---|---|---|---|
| **1** | Authorise implementation of items 1–4 in `scripts/verify_provisioning.py`, `scripts/verify_schema_drift.py` and the two test files | the drift/provisioning tooling owner (approval **AP-W1D-3** per W1D-05 §3.1) | **NOT GRANTED** |
| **2** | Scope: fix `compare()` only, or extract a shared classifier and fix `diff_snapshots()` and `check_drift()` too | same owner | **UNRESOLVED** — recommendation in §6.1 |
| **3** | Accept the single verdict change in §6.3(a) | same owner | **UNRESOLVED** |
| **4** | Authorise a PostgreSQL lane run to settle uncertainties 1 and 2 | the PostgreSQL lane owner | **NOT REQUESTED** — not needed to implement the fix, only to close the key-uniqueness question |

Approval 1 is sufficient to proceed; 2 and 3 shape the patch; 4 is independent and can wait.

---

## 10. Confirmations

This phase did **not**: edit any production source, test, snapshot, migration or shared
configuration; stage, commit, push, or trigger CI; create, move or remove a branch or
worktree; connect to PostgreSQL, Redis, or any other service; run the PostgreSQL lane;
install a package or start a server; write to the shared Git object database (198 → 198,
verified with an isolated object directory); overwrite any existing report; or alter
TENANT-01, QUOTA-01, the D5 Redis workstream, or any live-trading control.

Files created, all in `staging/`:

```
PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md   (this report)
w1d06_phase0.py, w1d06_entry.txt, w1d06_exit.txt
probe_p11b_w1d06_repro.py,    p11b_w1d06_repro.txt
probe_p11b_w1d06_sites.py,    p11b_w1d06_sites.txt
probe_p11b_w1d06_proposed.py, p11b_w1d06_proposed.txt
w1d06_focused.txt, w1d06_broader.txt
```

**W1C-02 is NOT closed.** It is reproduced and specified. Closure requires the authorised
implementation phase plus the regression evidence described in §6.4.

W1C-02 **OPEN** · W1D-05 **CLOSED, not defective** · PostgreSQL lane **UNEXECUTED** ·
live database verification **NOT PERFORMED** · TENANT-01 **OPEN** · QUOTA-01 **OPEN** ·
live trading **OFF / NOT CERTIFIED**
