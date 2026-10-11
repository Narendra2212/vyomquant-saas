# Phase 11B / W1D-12 — Classifier Decision Record and Authorized Implementation Task

**Date:** 2026-10-11
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

**This document is the decision register entry.** It records six decisions issued by the Code
owner, identifies one condition that is **not yet satisfied**, and defines the separately
authorized implementation task. **No code was written in this phase** — the four authorized
files are byte-unchanged (§4).

---

## 1. Decisions as recorded

Each recorded independently. Approval of one implies nothing about the others.

| # | Decision | State | Issued by | Condition |
|---|---|---|---|---|
| **AP-W1D-3** | Authorize the four-file scope in W1D-11 §5.1. **No other files are authorized.** | **GRANTED** | Code owner (§2) | none |
| **D1** | Ambiguous comparison-key collisions are **hard failures with distinct labels** | **GRANTED** | Code owner | none |
| **D2** | Reordered identical rows are classified **unchanged** — the only identified observable behaviour change | **GRANTED** | Code owner | none |
| **D3** | **Option 1** — one shared classifier, three separate renderers, delivered as two separately reviewable changes | **GRANTED** | Code owner | none |
| **D4** | Place the classifier in **`scripts/verify_provisioning.py`**; **not** in `verify_schema_drift.py` (import cycle) | **GRANTED** | Code owner | none |
| **D5** | Preserve `check_drift()`'s existing three-tier severity routing **exactly** | **CONDITIONALLY GRANTED — condition NOT satisfied** | Code owner | **production-audit stakeholder sign-off, which has not been given** |

**Tally: 5 GRANTED, 1 CONDITIONALLY GRANTED (condition open), 0 REJECTED.**

### 1.1 The authorized file scope, verbatim

| # | Path | Authorized change |
|---|---|---|
| 1 | `scripts/verify_provisioning.py` | add `WHOLE_ROW`, `COMPARISON_KEY`, pure `classify_dimension()` |
| 2 | `scripts/verify_schema_drift.py` | route `compare()` through the classifier |
| 3 | `tests/test_drift_classification.py` | **new** — 14 cases |
| 4 | `tests/test_snapshot_dimension_invariants.py` | import the production key map instead of duplicating it |

**Explicitly NOT authorized:** `scripts/verify_production_schema.py`,
`scripts/provision_database.py`, `staging/apply_migration.py`, migrations, snapshots, shared
configuration, and any other workstream's files.

---

## 2. The authorized Code owner — what I can and cannot establish

**Recorded fact:** the grant in §1 was issued by the user in this session, acting in the
**Code owner** role named at `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`. That is the
provenance of this authorization.

**What I could not do, stated plainly:** I cannot identify the Code owner as a named
individual from repository evidence, and I did not invent one. The evidence:

| Source | Result |
|---|---|
| `git log` across all history | a **single committer** for every commit — identifies the repository account, not a role holder |
| PR assignees / reviewers (D5′ line, PRs #6/#7/#8) | **0 assignees, 0 reviewers** on all three |
| `CODEOWNERS` file | **none in the repository** |
| Approval ledger owner column | role labels only: `Code owner`, `DB owner`, `Coordinator` |

So the register entry is: **authorization issued by the session Code owner; no named
individual is attested in the repository.** Anyone auditing this later should read it that way
rather than as an identification.

---

## 3. D5 — the condition is open, and it does not block the authorized work

### 3.1 No stakeholder decision exists to record

You granted D5 *subject to* production-audit stakeholder sign-off. **That sign-off has not
been given** — not in your message, and not anywhere in the repository. I searched for it and
found nothing; there is no production-audit approval record of any kind. **I am therefore not
recording D5 as YES**, and §1 shows it as conditionally granted with the condition open.

### 3.2 Why it is not a blocker

`check_drift()` lives in `scripts/verify_production_schema.py`, which **AP-W1D-3 explicitly
excludes**. Under D3 Option 1 the first change touches only the four authorized files, so
`check_drift()` and its three-tier severity routing are preserved **by construction** — the
implementation task cannot alter them because it may not open the file.

| | |
|---|---|
| D5's condition gates | the **second** change (routing `diff_snapshots()` and `check_drift()` through the classifier) |
| D5's condition does **not** gate | the **first** change (the four authorized files) |

So the first change proceeds. The second change stays blocked until the production-audit
stakeholder rules on D5 — and that is the correct place for the dependency, because the second
change is precisely the one that would touch the severity routing.

### 3.3 What the stakeholder is actually being asked

For whoever holds that role: `check_drift()` routes drift findings into three tiers —
`f.critical` for a hard-coded 10-dimension list (`tables`, `columns`, `primary_keys`,
`foreign_keys`, `row_level_security`, `policies`, `privileges`,
`unique_and_check_constraints`, `functions`, `triggers`), and `f.minor` for advisory
dimensions **and** for `indexes`, `sequences`, `extensions`, `enum_types`. The question is
whether that criticality policy stays byte-identical when the diff underneath it is replaced.
Preserving it is the recommendation; changing it would be a production-audit policy decision,
not a classification detail.

---

## 4. Preservation at the time of recording

| Measurement | Value |
|---|---|
| `HEAD` / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` |
| Staged paths | 22 |
| **Digest A** | `98ae5bdf2af9a0ae…` — unchanged |
| **Digest C** | `8d9c475b86a4a71d…` — unchanged |

Authorized-file fingerprints, re-verified immediately before writing this record:

| Path | Blob | State |
|---|---|---|
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | **SAME** |
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | **SAME** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | **SAME** (out of scope; recorded to prove it is untouched) |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | **SAME** |
| `tests/test_drift_classification.py` | — | **does not exist** |

**No file was edited in this phase.**

---

## 5. The separately authorized implementation task

**Status: DEFINED, NOT EXECUTED.** This document is the charter. Execution requires its own
turn; recording a decision is not performing the work.

### 5.1 Mandatory pre-edit gate

Immediately before the first edit, re-verify and abort on any mismatch:

| Check | Expected |
|---|---|
| `git hash-object scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` |
| `git hash-object scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` |
| `git hash-object tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` |
| `tests/test_drift_classification.py` | absent |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Staged count / Digest C | 22 / `8d9c475b86a4a71d…` |
| Concurrent edits to any authorized file | none |

**A concurrent session is actively reading these files** — `p11b24_*`, working on
`PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md`. Assessed in W1D-11 §7.2 as a coordination
survey rather than a competing implementation, but the gate above is what makes that
assessment safe rather than assumed.

### 5.2 Work, in landing order

1. **`scripts/verify_provisioning.py`** — add `WHOLE_ROW = "whole-row"`, `COMPARISON_KEY`
   (16 entries, widths per W1D-09 §5), and pure `classify_dimension(dimension, expected,
   actual) -> ClassifiedDimension`. Standard library only (`collections.Counter`). Do not
   alter `_norm()`, `_as_tuples()`, `diff_snapshots()`, the snapshot format, or any of the 16
   SQL queries.
2. **`scripts/verify_schema_drift.py`** — route `compare()` through the classifier; import
   `COMPARISON_KEY` alongside the existing `SNAPSHOT_QUERIES, _norm`.
3. **`tests/test_drift_classification.py`** — new, all 14 cases from W1D-10 §3, pure data, no
   gate, no skip, no database.
4. **`tests/test_snapshot_dimension_invariants.py`** — replace the test-local `WHOLE_ROW`
   (L93) and `COMPARISON_KEY` (L100) with imports from `verify_provisioning`. All 48 existing
   cases must still pass.

### 5.3 Invariants that must hold

1. `RESULT: NO DRIFT` and `RESULT: DRIFTED` **verbatim** — four launch documents quote them as
   evidence.
2. Exit codes 0 / 1 / 2 unchanged.
3. Advisory routing unchanged; an advisory-only difference must not fail the run.
4. `check_drift()` untouched (D5, and out of scope).
5. **No nonzero drift may emit a line without a subject** — the fix for today's
   `columns: 0 missing, 0 unexpected`.
6. **Null-safe sorting.** `_norm` preserves `None` and `None < 'x'` raises `TypeError` in
   Python 3. Sort on `(element is None, str(element))`. `columns` has four un-coalesced
   attribute columns, so a plain `sorted()` crashes on real data.
7. W1D-05's static guards not weakened: 48/48 passing, 12 mutations still biting.
8. Key uniqueness is **unverified for 0 of 16** dimensions. The classifier must stay correct
   when a declared key turns out non-unique — that is what D1's collision policy is for. **No
   uniqueness claim may be made.**

### 5.4 Verification, sequential

```
python -m pytest tests/test_drift_classification.py -q --no-header -p no:cacheprovider
python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider
python staging/probe_p11b_w1d05_guard_mutation.py
python -m pytest tests/test_snapshot_dimension_invariants.py \
                tests/test_schema_drift_checker_is_read_only_postgres.py \
                tests/test_migration_tooling.py \
                tests/test_orm_models_match_provisioned_schema.py \
                tests/test_bug049_sql_and_alembic_agree.py -q --no-header -rs
```

Expected: 48 passed on the guard file; 12 applied / 12 bit / 0 silent on the mutation probe;
**103 passed, 12 skipped, 1 xfailed** on the group. The mutation probe perturbs module dicts,
so it runs alone. **The 12 skips are PostgreSQL-gated and are not passes.** No catalogue or
live-database claim may be derived from any of these.

### 5.5 Prohibited

No staging, commit, push, reset, stash or branch change; no shared-index or shared-object
write; no database or Redis connection; no snapshot recapture; no package install; no server
start; no CI trigger; no edit to any file outside the four in §1.1; no removal or weakening of
any existing test.

### 5.6 Rollback

Steps 1 and 3 are additive. Reverting step 2 restores the previous `compare()` body and step 3
must be reverted with it; step 4 must be reverted with step 1. No data migration, no snapshot
regeneration — the change is confined to how two in-memory lists are compared and printed.

---

## 6. Disposition

| Item | Status |
|---|---|
| **AP-W1D-3** | **GRANTED** — four-file scope, recorded §1.1 |
| **D1 / D2 / D3 / D4** | **GRANTED** |
| **D5** | **CONDITIONALLY GRANTED** — production-audit stakeholder sign-off **outstanding**; blocks only the second change (§3.2) |
| **Code owner** | authorization issued by the session Code owner; **no named individual attested in the repository** (§2) |
| **Implementation** | **task DEFINED, NOT EXECUTED** — requires its own authorized turn |
| **W1C-02** | **OPEN** — until the implementation is completed and verified |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

Nothing in this document performs the implementation.
