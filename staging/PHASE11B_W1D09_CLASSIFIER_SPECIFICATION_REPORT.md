# Phase 11B / W1D-09 — Drift Classifier Specification and Collision Semantics

**Date:** 2026-10-10
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **SPECIFICATION ONLY. NO IMPLEMENTATION. NO DATABASE VALIDATION.**

No application code, test, migration, snapshot, database state or other workstream's file was
changed. Nothing was staged, committed, pushed or run against a server. **AP-W1D-3 remains
NOT GRANTED** — rechecked against the authoritative record in this phase.

---

## HEADLINE

Five things, two of which correct the working assumptions this phase started from.

1. **The specification work is substantially already done by a concurrent session.**
   `staging/p11b_w1d06x_multiplicity.txt` (20:48) and `staging/p11b_w1d06x_prototype.txt`
   (21:22) prototype the classifier, validate 13 cases against the real 396 KB snapshot, and
   already identify the duplicate-key hazard. No report was written for them. **I built on
   them rather than re-deriving, and credit them throughout** (§2.4).
2. **I corrected my own hypothesis mid-phase.** `recorded_migrations` orders by
   `(filename, step)` while declaring key width 1, which looked like evidence that `filename`
   is non-unique. The DDL says `filename text PRIMARY KEY` and the writer uses
   `ON CONFLICT (filename) DO NOTHING`, so **`filename` is unique** and width 1 is correct.
   The provisioner applying `020` at two steps is reconciled by the conflict clause: applied
   twice, **recorded once** (§2.3).
3. **`privileges` can return genuinely duplicate rows by construction** — established by the
   concurrent session and re-read here. `information_schema.role_table_grants` is one row per
   `(grantor, grantee, table, privilege_type, is_grantable)`; the query projects only three of
   those with **no `DISTINCT` and no `GROUP BY`**, so one privilege granted by two grantors
   yields two identical rows. **No dimension uses `DISTINCT` or `GROUP BY`.** This makes
   multiset semantics mandatory rather than defensive (§3.3).
4. **Collision recommendation: fall back, do not group, and escalate to inconclusive only for
   declaration faults.** Grouping colliding rows reproduces W1C-02 in a new disguise; refusing
   to classify anything conflates a declaration bug with a schema change. The safest supported
   semantics is a per-key-group whole-row multiset fallback with an explicit `AMBIGUOUS KEY`
   line (§3).
5. **No code consumer parses the drift output.** Four *documents* quote `RESULT: NO DRIFT` as
   evidence, so the two verdict lines must stay verbatim while per-dimension detail lines are
   free to change (§4.5).

---

## 1. Source files and functions inspected

Read in full or by targeted extraction, all at the blob hashes below (unchanged at exit):

| Path | Blob | What was inspected |
|---|---|---|
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | `compare()` (L~196-220), `_as_tuples()`, `ADVISORY_DIMENSIONS`, `redact()`, `assert_read_only()`, `_refuse_transaction_pooler()`, `live_snapshot()`, `_server_version()`, `main()` output formatter and exit codes |
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | `SNAPSHOT_QUERIES` (L68, all 16 queries), `_norm()` (L200), `diff_snapshots()` (L234) |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | `check_drift()` (L~235-250) and its `SNAPSHOT_QUERIES` import (L100) |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | 18 test functions (48 cases), test-local `WHOLE_ROW` (L93) and `COMPARISON_KEY` (L100) |
| `scripts/provision_database.py` | — | `schema_migrations` DDL (L153), the recording `INSERT` (L480-481), step narrative (L24-35) |
| `staging/apply_migration.py` | — | a **second copy** of the same DDL (L36) and insert (L105-106) |

Reports read: `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`,
`PHASE11B_W1D05_STATIC_GUARDS_REPORT.md`, `PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md`,
`PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md`,
`PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md`, plus the concurrent
`p11b_w1d06x_multiplicity.txt` and `p11b_w1d06x_prototype.txt`.

---

## 2. Current-source findings and discrepancies against prior reports

### 2.1 Where the dimension facts come from

All figures below were derived by **importing the live modules and parsing the live SQL** in
`staging/w1d09_extract.py`, not from W1D-05's published table. Import under
`PGHOST=255.255.255.255 PGPORT=1 PGCONNECT_TIMEOUT=1` completed in **0.229 s**, confirming no
connection at import.

| Fact | Measured |
|---|---|
| Dimensions | **16** |
| `drift.SNAPSHOT_QUERIES is prov.SNAPSHOT_QUERIES` | **True** — one object, two importers |
| `ADVISORY_DIMENSIONS` | `['alembic_version', 'recorded_migrations']` |
| Keyed dimensions | **12** |
| Whole-row dimensions | **4** (`privileges`, `extensions`, `sequences`, `alembic_version`) |
| Undeclared dimensions | **0** |
| Keyed dimensions with ≥1 attribute column (W1C-02 exposure) | **12** |

**No discrepancy with W1D-05's table.** My independent parse of columns, key widths, attribute
counts and `ORDER BY` term counts reproduces it exactly. That is a cross-check from current
source, not a citation.

### 2.2 No production key declaration exists

`COMPARISON_KEY`, `WHOLE_ROW` and `KEY_WIDTH` are **absent from all three production
scripts** (zero occurrences of any). The only declaration is test-local, at
`tests/test_snapshot_dimension_invariants.py:93` and `:100`, with sentinel `'whole-row'` and
16 entries. Per the brief I did **not** add a production map and did **not** replace the
test-local one.

### 2.3 Correction to this phase's own working hypothesis

`recorded_migrations` declares key width **1** (`filename`) but its query is
`SELECT filename, sha256, step FROM public.schema_migrations ORDER BY filename, step` — an
`ORDER BY` **wider than the key**, which is the classic signature of multiple rows per key.
I treated that as a probable collision and went looking for confirmation. The source says
otherwise:

```sql
-- scripts/provision_database.py:153  (and a second copy at staging/apply_migration.py:36)
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename     text        PRIMARY KEY,
    sha256       text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    step         text        NOT NULL
);

-- scripts/provision_database.py:480-481
"INSERT INTO public.schema_migrations (filename, sha256, step) "
"VALUES (%s, %s, %s) ON CONFLICT (filename) DO NOTHING",
```

`filename` is the **PRIMARY KEY**, and the writer is `ON CONFLICT (filename) DO NOTHING`. The
provisioner's narrative — `Step 0  020_declare_pre_existing_tables.sql` and
`Step 3  020 again` — is reconciled by that clause: the file is **applied twice but recorded
once**, keeping `step` at its first value. So **width 1 is correct**, and the wider `ORDER BY`
is merely more than necessary (harmless, and it satisfies Guard A).

This is the strongest uniqueness evidence available for any dimension, and it is still
**source** evidence, not catalogue evidence: it proves the DDL the provisioner *creates*, not
that a given live database actually carries that constraint. A table pre-dating this DDL, or
altered since, could differ. §5 records that distinction per dimension.

### 2.4 A second copy of the DDL

`staging/apply_migration.py` contains a byte-equivalent `schema_migrations` DDL and insert.
Two copies can drift. Not in scope here; recorded so the uniqueness evidence in §5 is known to
rest on two places agreeing today.

### 2.5 Schema scoping, with a correction to my own detector

A key like `relname` is unique only *within* a namespace, so an unscoped query could collide
across schemas. Measured:

| Scoping | Dimensions |
|---|---|
| Explicit schema predicate found | 12 — `tables`, `columns`, `primary_keys`, `foreign_keys`, `unique_and_check_constraints`, `indexes`, `row_level_security`, `policies`, `privileges`, `functions`, `triggers`, `enum_types` (all `= 'public'`) |
| No predicate detected | 4 — `extensions`, `sequences`, `alembic_version`, `recorded_migrations` |

**My detector had a false negative.** `recorded_migrations` *is* scoped — via a
schema-qualified `FROM public.schema_migrations`, a form my regexes did not cover. Of the
genuinely unscoped three, all are **whole-row**, so no key can collide across schemas:
`extensions` (`extname` is globally unique in `pg_extension`), `sequences`, `alembic_version`.
`sequences` reading `information_schema.sequences` without a schema filter could in principle
return same-named sequences from two schemas — which under whole-row semantics is a genuine
duplicate row, handled correctly by multiset counting (§3.3), not a misclassification.

### 2.6 Concurrent work on this exact question

Two artifacts from another session, created during my W1D-07/W1D-08 phases:

| Artifact | Size | Time | Contribution |
|---|---|---|---|
| `p11b_w1d06x_multiplicity.txt` | 17,798 B | 20:48 | real-snapshot duplicate census; the `privileges` by-construction duplicate finding; D1/D2/D3/D3b reproduced through the real `compare()`; advisory routing and `_norm` ruled out |
| `p11b_w1d06x_prototype.txt` | 11,436 B | 21:22 | a working prototype over 13 cases with a stated compatibility invariant; validation against the real 396 KB snapshot; the duplicate-key blind spot a naive keyed fix would create |

No `W1D-06x` or `W1D-09` report exists for them. **This is the fifth parallel effort in the
W1C-02/W1D line.** Their findings are incorporated below with attribution; where their design
and W1D-06's differ, §3 and §4 state which I recommend and why.

### 2.7 Discrepancy against W1D-06's design

W1D-06 §6 proposed a classifier that, for a keyed dimension, groups rows by key into a
`Counter` of attribute tuples and reports `lost`/`gained` per key. **That design silently
tolerates a key collision**: two distinct objects sharing a key land in one group and their
attribute multisets are differenced, producing a "modified" line for what is actually one
removal plus one addition. W1D-07 §4 flagged this as unspecified. The concurrent prototype
independently reached the same conclusion (its §4) and **reports duplicate keys instead of
collapsing them**. §3 below adopts and tightens that position. **Current source plus the
prototype is the baseline; W1D-06's design is superseded on this point.**

---

## 3. Collision semantics

### 3.1 The precise failure mode

For a keyed dimension with declared width *k*, let `key(row) = row[:k]` and
`attrs(row) = row[k:]`. A **collision** is two or more rows on the same side sharing a key with
different attributes. Concretely, if `policies` were keyed on `(tablename)` alone:

```
baseline: ('fee_records', 'p1', ..., '(a)')      key = ('fee_records',)
          ('fee_records', 'p2', ..., '(b)')      key = ('fee_records',)   <- same key
```

Any classifier that groups by key must then decide what `('fee_records',)` *is*. If it
differences the attribute multisets it will report one `MODIFIED ('fee_records',)` with
`'(a)' -> '(b)'` — asserting that one object changed when in truth there are two distinct
policies. That is W1C-02's error (conflating identity with attributes) re-introduced from the
opposite direction.

A **duplicate** is the special case where the colliding rows are *identical*. That is not
necessarily a fault: §3.3 shows it is reachable by construction for `privileges`.

### 3.2 Option comparison

| | **A — Refuse: mark inconclusive** | **B — Fall back to whole-row multiset for the affected group** | **C — Continue grouping** |
|---|---|---|---|
| Preconditions | none | the dimension's rows are comparable as whole tuples (always true — they already are today) | the declared key is genuinely unique |
| Required evidence | none | none | **catalogue proof of uniqueness, which does not exist for any dimension** (§5) |
| Can misclassify two distinct objects as one modification? | **No** | **No** — multiset comparison never pairs non-identical rows | **YES** — the central hazard |
| Are duplicate rows legitimate? | treated as a fault | **treated as data**, counted exactly | invisible — collapsed |
| Behaviour when multiplicities differ | inconclusive | reports `baseline_count` vs `live_count` for the key | **silently wrong** — today's `0 missing, 0 unexpected` |
| Behaviour when a key is missing/invalid | inconclusive (correct) | would need a key to group by — **not applicable** | undefined |
| Marks results inconclusive rather than missing/extra/modified | always | only for the colliding group | never |
| Cost | a declaration bug fails the whole dimension, conflating it with a schema change | loses `modified` precision for the colliding group only | none, and that is the problem |

### 3.3 Why duplicates are legitimate, and therefore why B is not merely defensive

Established by the concurrent session, re-read here from source:

```
privileges:
  SELECT table_name, grantee, privilege_type
  FROM information_schema.role_table_grants
  WHERE table_schema = 'public' AND grantee IN ('anon','authenticated','service_role')
  ORDER BY table_name, grantee, privilege_type
  SELECT DISTINCT present: False      GROUP BY present: False
```

`role_table_grants` has one row per `(grantor, grantee, table, privilege_type, is_grantable)`.
The projection drops `grantor` and `is_grantable` and applies neither `DISTINCT` nor
`GROUP BY`, so **the same privilege granted to the same grantee by two different grantors
produces two identical output rows.** No dimension uses `DISTINCT` or `GROUP BY`.

Whether such a configuration exists on any particular cluster is a **live-database question
and is not asserted here.** What source alone establishes is that nothing prevents it. The
real snapshot (`staging/provisioning_snapshot.json`, 396,325 B, captured 2026-10-06) contains
**zero duplicate rows across all 16 dimensions**, so this is latent, not observed.

### 3.4 Recommendation

**Adopt B for key collisions, and A only for declaration faults.** Precisely:

| Condition | Policy | Verdict | Reported as |
|---|---|---|---|
| Dimension has **no key declaration** | **A — refuse.** There is no safe key to group by, and falling back to whole-row would silently mask an undeclared dimension that Guard B exists to catch. | `inconclusive` | `UNDECLARED DIMENSION` + the dimension name |
| Declared key is **invalid** (not `WHOLE_ROW`, not an `int` in `1..len(cols)`, or `None`) | **A — refuse**, same reasoning | `inconclusive` | `INVALID KEY DECLARATION` + the value |
| Key declared `WHOLE_ROW` | whole-row multiset comparison; `modified` is not applicable | `same` / `drift` | `missing` / `added` with exact counts |
| Keyed, and **every** key group has ≤1 row per side | full four-way classification | `same` / `drift` | `unchanged` / `modified` / `missing` / `added` |
| Keyed, and some key group has **>1 row on either side** | **B — fall back for that group only.** Compare the group's rows as whole tuples by multiset. Do **not** emit `modified` for it. | `drift` if the group differs, and the **dimension** is additionally marked `ambiguous` | `AMBIGUOUS KEY <key>: baseline=<n>, live=<m>` plus the multiset `missing`/`added` rows |

Rationale for the split:

- **C is rejected outright.** It requires uniqueness evidence that does not exist for a single
  dimension (§5), and its failure mode is the exact defect W1C-02 is about.
- **B is chosen over A for collisions** because a collision is itself a finding worth
  reporting precisely, and multiset comparison is *never wrong* — it only declines to say
  "modified". Refusing the whole dimension would discard correct information about its other,
  non-colliding keys.
- **A is retained for declaration faults** because there is no key to fall back *with*, and
  because an undeclared dimension is a process failure that should not be absorbed into a
  schema verdict.
- A collision is a **hard failure for non-advisory dimensions** — it means either the database
  holds unexpected duplicates or the declared key is wrong, and both need a human — but it
  must be **labelled distinctly** so a reader can tell it from a schema change. The module
  docstring already warns against conflating deployment-state questions with schema-shape
  questions; this is the same principle applied to declaration faults.

**No claim of key uniqueness is made anywhere in this specification.** The classifier is
designed so that it stays correct when a declared key turns out not to be unique — which is
the only safe posture while §5's evidence column reads as it does.

---

## 4. Duplicate-count output contract

**Proposed. Not implemented. Today's production output is §4.4.**

### 4.1 Structured result, per dimension

```
ClassifiedDimension:
    dimension      : str                       # key of SNAPSHOT_QUERIES
    key_spec       : int | "whole-row"         # as declared; echoed so the report is self-describing
    verdict        : "same" | "drift" | "inconclusive"
    advisory       : bool                      # dimension in ADVISORY_DIMENSIONS
    unchanged      : int                       # count only; never enumerated
    modified       : [ { key: tuple,
                         changes: [ { index: int, was: any, now: any } ] } ]
    missing        : [ tuple ]                 # whole rows, baseline-only
    added          : [ tuple ]                 # whole rows, live-only
    duplicated     : [ { subject: tuple,       # key for keyed dims, whole row for whole-row dims
                         baseline: int, live: int } ]
    ambiguous      : [ { key: tuple, baseline: int, live: int } ]
    notes          : [ str ]                   # UNDECLARED / INVALID KEY reasons
```

Field semantics, mapped to the brief's six required distinctions:

| Required distinction | Field |
|---|---|
| Present only in the expected snapshot | `missing` |
| Present only in the actual snapshot | `added` |
| Matched object, changed non-key attributes | `modified[].changes` |
| Duplicate rows and differing multiplicities | `duplicated` (counts on both sides) |
| Ambiguous key / inconclusive | `ambiguous`, `verdict="inconclusive"`, `notes` |
| Whole-row dimensions without attribute columns | `key_spec="whole-row"`; `modified` always empty |

### 4.2 Determinism

- `modified` sorted by `key` ascending, tuple-wise; `changes` sorted by `index` ascending.
- `missing`, `added` sorted by the whole row tuple ascending.
- `duplicated`, `ambiguous` sorted by `subject` / `key` ascending.
- Dimensions emitted in `SNAPSHOT_QUERIES` iteration order (insertion order, stable).
- Sorting is on the normalised tuples `_norm()` already produces. **One hazard to handle in
  implementation:** `_norm` preserves `None`, and `None` is not orderable against `str` in
  Python 3. Sort on a key that maps each element to `(element is None, str(element))` so a
  `NULL` attribute cannot raise `TypeError`. This is a real defect a naive `sorted()` would
  introduce; `columns` has seven attribute columns, three of which are `coalesce(...)`-guarded
  and four of which are not.

### 4.3 Rendered text — proposed

```
<dimension>: <u> unchanged, <m> modified, <k> missing, <a> added[, <d> duplicated][, <g> ambiguous]
    MODIFIED <key>
        attr[<i>] <name?>: <was!r> -> <now!r>
    MISSING from the database : <row>
    NOT IN this repository    : <row>
    DUPLICATED <subject>: baseline=<n>, live=<m>
    AMBIGUOUS KEY <key>: baseline=<n>, live=<m>  -- not classified; compared as whole rows
    UNDECLARED DIMENSION -- no comparison key; not classified
```

**Invariant: no nonzero drift may produce a line with no subject.** If `verdict != "same"`,
at least one of `modified`/`missing`/`added`/`duplicated`/`ambiguous`/`notes` is non-empty.
This is the explicit fix for today's `columns: 0 missing, 0 unexpected`.

### 4.4 Worked example with duplicate rows

Using `privileges` (whole-row, the one dimension where identical duplicates are reachable by
construction — §3.3). One grant recorded twice in the baseline, once live:

```
baseline: [('fee_records','authenticated','SELECT'),
           ('fee_records','authenticated','SELECT')]
live:     [('fee_records','authenticated','SELECT')]
```

**Today (measured through the real `compare()` by the concurrent session):**

```
privileges: 0 missing, 0 unexpected          <- hard failure, names nothing
```

`expected == actual` is False (lists differ in length) so the fast path is skipped; then
`[r for r in expected if r not in actual]` finds the row *is* in `actual`, so both lists come
back empty. Exit 1 with nothing to show.

**Proposed:**

```
privileges: 1 unchanged, 0 modified, 0 missing, 0 added, 1 duplicated
    DUPLICATED ('fee_records', 'authenticated', 'SELECT'): baseline=2, live=1
```

The second, keyed illustration — a collision rather than a duplicate — on `policies` keyed
`(tablename, policyname)` where two baseline rows share a key:

```
policies: 0 unchanged, 0 modified, 1 missing, 0 added, 1 ambiguous
    AMBIGUOUS KEY ('fee_records', 'p1'): baseline=2, live=1  -- not classified; compared as whole rows
    MISSING from the database : ('fee_records', 'p1', ..., '(b)')
```

Note what it does **not** say: it does not claim `p1` was modified from `(a)` to `(b)`. The
concurrent prototype demonstrates the same outcome — `duplicated: [(('fee_records','p1'),2,1)]`
with the duplicate key *reported, not collapsed* — and records that a naive dict comprehension
would have kept one row and lost the other.

### 4.5 Compatibility — inspected before recommending

Searched the repository for the distinctive output strings (`MISSING from the database`,
`NOT IN this repository`, `RESULT: DRIFTED`, `RESULT: NO DRIFT`, `only in run 1/2`):

| Category | Files |
|---|---|
| **Producers** | `verify_schema_drift.py`, `verify_production_schema.py`, `verify_provisioning.py` |
| **`staging/` reports and probes** | 14 files (quote the text as evidence) |
| **Non-producer code that parses it** | **none** |
| **Documents quoting a verdict line** | 4 — `DATABASE_PROVISIONING_RUNBOOK.md`, `FINAL_LAUNCH_READINESS_REPORT.md`, `PAPER_ONLY_LAUNCH_READINESS.md`, `RLS_TENANT_ISOLATION_VERIFICATION.md`, all quoting `RESULT: NO DRIFT` |

An earlier pass of mine using generic tokens (`missing, `, `unexpected`, `appeared`) returned
~60 "consumers" — all false positives in ordinary prose and unrelated code. Only the
distinctive strings are diagnostic; that first count should not be quoted.

**Compatibility rules this yields:**

1. **Preserve `RESULT: NO DRIFT` and `RESULT: DRIFTED` verbatim.** Four launch documents cite
   them as evidence. No machine depends on them, but human evidence trails do.
2. **Per-dimension detail lines may change freely** — nothing parses them.
3. **Preserve the per-dimension summary table** (`verify_schema_drift.py:269`) and fix a real
   inconsistency the concurrent prototype found there: for reordered-identical rows today's
   summary prints `DRIFT` while the detail section prints nothing. Under the proposal the
   marker and the detail agree.
4. **Preserve exit codes** 0/1/2 and advisory routing.
5. **Legacy counts remain derivable** as `missing + modified` and `added + modified` for every
   case *except* the multiplicity cases, where today reports `0/0` and is wrong. That is the
   only intentional divergence in counts, and it is a correction.

---

## 5. Dimension-by-dimension evidence matrix

Built from the live `SNAPSHOT_QUERIES` and the only existing declaration (test-local).
**Uniqueness is nowhere verified against a catalogue.** "Source" means a DDL, constraint or
documented PostgreSQL property was read; it is not catalogue evidence.

| # | Dimension | Cols | Declared key (source: test-local) | Attrs | Key columns | Uniqueness evidence | Verified? | Collision policy | Runtime check |
|---|---|---:|---|---:|---|---|---|---|---|
| 1 | `tables` | 2 | 1 | 1 | `c.relname` | `relname` unique per namespace; query scoped `nspname='public'` | **No** | B | group size |
| 2 | `columns` | 9 | 2 | 7 | `table_name`, `column_name` | unique per table; scoped `table_schema='public'` | **No** | B | group size |
| 3 | `primary_keys` | 3 | 2 | 1 | `t.relname`, `c.conname` | `conname` unique per namespace; scoped | **No** | B | group size |
| 4 | `foreign_keys` | 3 | 2 | 1 | `t.relname`, `c.conname` | as above | **No** | B | group size |
| 5 | `unique_and_check_constraints` | 4 | 2 | 2 | `t.relname`, `c.conname` | as above | **No** | B | group size |
| 6 | `indexes` | 3 | 2 | 1 | `tablename`, `indexname` | index names unique per schema; scoped `schemaname='public'` | **No** | B | group size |
| 7 | `row_level_security` | 3 | 1 | 2 | `c.relname` | one row per relation; scoped | **No** | B | group size |
| 8 | `policies` | 7 | 2 | 5 | `tablename`, `policyname` | policy names unique per table; scoped | **No** | B | group size |
| 9 | `privileges` | 3 | **whole-row** | 0 | — | n/a — the row *is* the key | n/a | whole-row multiset | **duplicate counting mandatory** (§3.3) |
| 10 | `extensions` | 1 | **whole-row** | 0 | — | `extname` globally unique in `pg_extension`; query unscoped but that is harmless | n/a | whole-row multiset | — |
| 11 | `functions` | 3 | 2 | 1 | `p.proname`, `pg_get_function_identity_arguments(p.oid)` | **strongest keyed case** — name + identity argument list *is* PostgreSQL's overload identity; scoped | **No** | B | group size |
| 12 | `triggers` | 3 | 2 | 1 | `c.relname`, `t.tgname` | `tgname` unique per table; scoped | **No** | B | group size |
| 13 | `sequences` | 1 | **whole-row** | 0 | — | query **unscoped**; same-named sequences in two schemas would be a genuine duplicate row, handled by multiset | n/a | whole-row multiset | duplicate counting |
| 14 | `enum_types` | 2 | 1 | 1 | `t.typname` | `typname` unique per namespace; scoped | **No** | B | group size |
| 15 | `alembic_version` | 1 | **whole-row** | 0 | — | single-row table by Alembic's design; **advisory** | n/a | whole-row multiset | — |
| 16 | `recorded_migrations` | 3 | 1 | 2 | `filename` | **`filename text PRIMARY KEY`** in the DDL + `ON CONFLICT (filename) DO NOTHING` at the writer (§2.3); **advisory** | **No** (DDL read, catalogue not) | B | group size |

Summary: 12 keyed, 4 whole-row, 0 undeclared, 12 exposed to W1C-02. **Uniqueness verified for
0 of 16.** `recorded_migrations` has the strongest evidence (an explicit PK in the DDL that
provisioning creates); `functions` has the strongest semantic argument (the key is
PostgreSQL's own overload identity). Neither is catalogue confirmation.

**Gap, stated explicitly:** the production key declaration is **absent**. The matrix above
describes a declaration that exists only in a test file. Binding it to production is AP-W1D-3
work. I did not add it.

---

## 6. Acceptance criteria for the future authorized implementation

Mapped to existing coverage. **No tests were created in this phase.**

| # | Criterion | Already covered? | Where / gap |
|---|---|---|---|
| 1 | A new dimension with no declared classification is rejected | **YES (static)** | `test_every_dimension_declares_a_comparison_key`; mutation **M5** bites. **Gap:** no test that the *classifier* emits `UNDECLARED DIMENSION` and `verdict="inconclusive"` |
| 2 | A missing or invalid key declaration is rejected | **YES (static)** | `test_a_declared_key_is_whole_row_or_a_valid_width`, `test_control_a_none_key_is_distinguishable_from_an_absent_one`; mutations **M7**, **M8** bite. **Gap:** no runtime `INVALID KEY DECLARATION` test |
| 3 | Two distinct objects colliding on the key are **not** reported as one modification | **NO** | **New.** Two baseline rows sharing a key → `ambiguous` + whole-row fallback; assert no `MODIFIED` line for that key. Demonstrated by the concurrent prototype §4 |
| 4 | Duplicate rows with **equal** multiplicity → no drift | **NO** | **New.** `[A,A]` vs `[A,A]` → `verdict="same"`, `unchanged=2`, `duplicated=[]` |
| 5 | Duplicate rows with **differing** multiplicity → counts reported | **NO** | **New.** `[A,A]` vs `[A]` → `duplicated=[{A,2,1}]`; assert the output is **not** `0 missing, 0 unexpected`. This is defect D3, reproduced through the real `compare()` by the concurrent session |
| 6 | A matched key with changed non-key attributes → one `modified`, attribute named | **NO** | **New.** The headline W1C-02 case; W1D-06 and the prototype both demonstrate `attr[6]: '' -> '(a = b)'` |
| 7 | A whole-row dimension yields removal/addition only, never `modified` | **PARTLY (static)** | `test_whole_row_dimensions_really_have_no_attribute_columns`; mutation **M9** bites. **Gap:** no runtime test that `modified` is empty for `key_spec="whole-row"` |
| 8 | Deterministic output across repeated runs | **NO** | **New.** Run twice on the same inputs → byte-identical. **Must include a `None`-valued attribute** to catch the `None`-vs-`str` sort hazard in §4.2 |
| 9 | Backward compatibility for existing consumers | **PARTLY** | §4.5: no code parses the output; 4 documents quote the verdict lines. **New:** assert `RESULT: NO DRIFT` / `RESULT: DRIFTED` are emitted verbatim, exit codes 0/1/2 unchanged, and advisory routing preserved (today's routing is already demonstrated correct by the concurrent session) |
| 10 | An inconclusive result when evidence is insufficient | **NO** | **New.** Undeclared or invalid key → `verdict="inconclusive"`, distinct from `drift`, and the dimension's other keys are not silently passed |

**Genuinely new cases: 3, 4, 5, 6, 8, 10, plus the runtime halves of 1, 2, 7 and 9.** The
static halves are already covered and must not be duplicated — W1D-08 established that
`tests/test_snapshot_dimension_invariants.py` covers both static invariants at 48/48 with 12
mutations biting.

Also worth asserting, from §4.5 rule 3: the per-dimension summary marker must agree with the
detail section. Today it does not for reordered-identical rows.

---

## 7. Unresolved questions and authorization dependencies

| # | Question | Blocked on |
|---|---|---|
| 1 | **Is any declared key actually unique in a live catalogue?** Verified for 0 of 16. A wrong width is exactly what policy B is designed to survive, but confirming it would let `modified` be trusted rather than merely safe. | A live database — **AP-W1D-1** / **AP-W1D-5**, both **NOT GRANTED** |
| 2 | **Does `privileges` ever actually contain two-grantor duplicates?** Reachable by construction; **zero** duplicates in the 2026-10-06 snapshot. | A live catalogue query |
| 3 | **Does the live `schema_migrations` carry the `filename` PRIMARY KEY** the DDL declares? A pre-existing or altered table could differ. | A live catalogue query |
| 4 | **Scope:** one shared classifier for `compare()`, `diff_snapshots()` and `check_drift()`, or `compare()` only? Three copies of the defect exist. | Code owner — **UNRESOLVED** since W1D-06 |
| 5 | **Accept the reordered-identical-rows verdict change** (hard failure → clean)? | Code owner — **UNRESOLVED** |
| 6 | **Is the `ambiguous` case a hard failure or an advisory?** §3.4 recommends hard failure with a distinct label; it is a defensible product decision either way. | Code owner |
| 7 | **Consolidate the five parallel efforts** on W1C-02/W1D (W1D-05, W1D-06, W1D-06x, W1D-07/08, this phase). | Coordinator |
| 8 | **Implement any of the above.** | **AP-W1D-3 — NOT GRANTED** |

### Authorization rechecked in this phase

```
staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression tests | Code owner | **NOT GRANTED** |

git log --all -i --grep=AP-W1D  ->  no matching commit
```

Unchanged. No approval is inferred from this prompt, from a report, from a passing suite, or
from any file being writable.

---

## 8. Preflight and exit preservation evidence

| Measurement | Preflight | Exit |
|---|---|---|
| `HEAD` / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | **same** |
| `origin/main` divergence | `0 0` | **same** |
| Worktrees | 1 (`main`) | 1 (`main`) |
| Tracked entries | 2104 | 2104 |
| Staged paths | 22 | **22** |
| Unstaged tracked | 102 | **102** |
| Untracked | 3507 | 3528 (this phase's own `staging/` artifacts) |
| Unmerged | 0 | **0** |
| Stash | 2 | 2 |
| **Digest A** (`sha256` of raw `git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **SAME** |
| **Digest C** (`sha256` of sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **SAME** |
| **Index tree** (isolated index + isolated object dir) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **SAME** |
| Objects into the isolated dir | 0 | 0 |
| Shared `.git/objects` | 198 | **198** |

The `.git/index` file hash was deliberately **not** used as the staged-content invariant —
`core.autocrlf=true` lets read-only `git status` rewrite its stat cache.

### 8.1 Candidate files unchanged

| Path | Blob (preflight = exit) |
|---|---|
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` |
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` |

### 8.2 Concurrent changes — reported, not attributed to this phase

`unstaged` has been 102 since W1D-08, having risen from 100 during W1D-07. The additions are
other workstreams': `backend_app/core/subscription_engine.py`,
`backend_app/backend/order_watchdog.py`, `tests/test_redis_debounce.py`. The two
`p11b_w1d06x_*` artifact pairs (§2.6) are also another session's and were **read, not
modified**. No candidate file of this phase appears in the unstaged set.

### 8.3 Artifacts created, all untracked and under `staging/`

```
PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md   (this report)
w1d09_extract.py        w1d09_extract.txt       dimension facts from live source
w1d09_collision.py      w1d09_collision.txt     schema scoping + ORDER BY-vs-key analysis
w1d09_recorded_key.py   w1d09_recorded.txt      schema_migrations PK evidence
w1d09_consumers.py                              consumers, AP recheck, baseline
```

---

## 9. No implementation and no database validation occurred

Explicitly, this phase did **not**: modify any application code, test, migration, snapshot,
shared configuration or another workstream's file; create a test file; add a production key
map; replace the test-local declaration; connect to PostgreSQL or Redis; make any network
call; recapture a snapshot; install a package; start a server; trigger CI; stage, commit,
push, reset, stash or switch branches; mutate the shared Git index or write to the shared Git
object database (198 → 198, verified with an isolated object directory); or overwrite any
existing report.

**No static test result is offered as evidence of database uniqueness or production
correctness.** The 48/48 static suite constrains SQL text and declaration shape only. Every
uniqueness cell in §5 reads **"Verified? No"** for that reason.

The only execution performed was reading the live modules in-process to extract dimension
metadata, under a routed-to-nowhere `PGHOST` with a 1-second timeout; import completed in
0.229 s, which a connection attempt could not.

---

## 10. Disposition

| Item | Status |
|---|---|
| **W1C-02** | **OPEN.** Specification now complete for collision semantics, the duplicate-count contract, and the whole-row boundary. `compare()`, `diff_snapshots()` and `check_drift()` all still diff whole tuples. |
| **AP-W1D-3** | **NOT GRANTED** — rechecked this phase; no commit, no record, no supersession |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** — and 3 of the 8 open questions in §7 need it |
| **Live trading** | **OFF / NOT CERTIFIED** |

The specification is the deliverable. **No implementation follows automatically**, and nothing
in this report constitutes an approval.
