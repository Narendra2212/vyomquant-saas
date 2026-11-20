# Phase 11B / W1D-11 — Classifier Approval Reconciliation and Execution Handoff

**Date:** 2026-10-11
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **ALL SIX DECISIONS UNRESOLVED. NO AUTHORIZATION EXISTS. NOTHING IMPLEMENTED.**

This is a coordination task. It does not grant authorization and no code was written.
No staging, commit, push, database connection, or CI trigger.

### The four required answers, up front

| Question | Answer |
|---|---|
| **Which decisions are explicitly approved?** | **None.** Zero of the six. |
| **Which remain unresolved?** | **All six** — AP-W1D-3, D1, D2, D3, D4, D5 |
| **Who needs to make each?** | AP-W1D-3 → **Code owner** (named in the ledger). D1–D5 have **no ledger entry at all** and default to the same Code owner; D5 additionally needs the production-audit stakeholder (§4) |
| **What is the exact next permitted action?** | A single human reply granting or rejecting the six items in §6.2. Nothing further is achievable read-only. |

| | |
|---|---|
| W1C-02 | **OPEN** |
| AP-W1D-3 | **NOT GRANTED** |
| TENANT-01 / QUOTA-01 | **OPEN** |
| PostgreSQL integration | **UNEXECUTED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Evidence reuse — licensed by unchanged fingerprints

The brief permits reusing the completed call-graph and coverage analysis unless source
fingerprints have changed. They have not:

| Path | Blob | vs W1D-09 / W1D-10 |
|---|---|---|
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | **SAME** |
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | **SAME** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | **SAME** |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | **SAME** |

**No source analysis was repeated.** Carried forward unchanged:

- Each of `compare()`, `diff_snapshots()`, `check_drift()` has **exactly one production
  caller — its own `main()`**; every `compare()` hit in `tests/` is a local function in a file
  that imports no drift module (W1D-10 §2.1).
- The three scripts form a strict DAG with `verify_provisioning` at the root; a classifier
  placed anywhere but `verify_provisioning.py` (or a new module) **closes an import cycle**
  for `diff_snapshots()` (W1D-10 §2.2).
- 16 dimensions, 12 keyed / 4 whole-row, 0 undeclared, 12 exposed to W1C-02; uniqueness
  verified for **0 of 16**; no production `COMPARISON_KEY` exists (W1D-09 §2, §5).
- The 14-case output/verdict matrix and the existing-vs-missing test map (W1D-10 §3, §4).

Prior reports read: `PHASE11B_W1D10_CLASSIFIER_IMPLEMENTATION_READINESS.md` (27,784 B),
`PHASE11B_W1D09_CLASSIFIER_SPECIFICATION_REPORT.md` (36,329 B),
`PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (56,397 B).

---

## 2. The authoritative approval record

`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, lines 800–804 — the complete W1D
approval ledger, with its owner-role column extracted verbatim:

| Approval | Scope | Owner role | State |
|---|---|---|---|
| AP-W1D-1 | Evaluate per-statement completeness predicates for 022–025 under an enforced read-only session | `DB owner (read access)` | **NOT GRANTED** |
| AP-W1D-2 | Insert 4 ledger rows for migrations verified FULLY APPLIED | `DB owner` | **NOT GRANTED** |
| **AP-W1D-3** | **Implement the 4-way drift classification + the 10 regression tests** | **`Code owner`** | **NOT GRANTED** |
| AP-W1D-4 | Add the `_provenance` envelope and recapture the snapshot | `DB owner + code owner` | **NOT GRANTED** |
| AP-W1D-5 | Install `pgserver`; create the isolated cluster and the `aerora_app` role | `Coordinator + DB owner` | **NOT GRANTED** |

**All five are negative.** This is the only formal approval register for the W1D line.

### 2.1 No grant exists anywhere — searched, with the false positives named

A repository-wide sweep for affirmative grant language (`I approve`, `hereby grant`,
`is granted`, `now granted`, `you may implement`, `proceed with implementation`,
`D1..D5 = approved/granted/yes`), filtered to contexts mentioning AP-W1D / classifier / drift:

| Hit | Verdict |
|---|---|
| `staging/p11b24_extract.txt` ×2 | **false positive** — quotes my own reports: `| AP-W1D-3 | **NOT GRANTED** |` and *"implementable only once AP-W1D-3 **is granted**"* (a conditional future) |
| `staging/PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md` | **false positive** — my own text, same conditional phrasing |

**Affirmative grants: 0.** My regex matched the phrase "is granted" inside conditional
clauses; the raw count of 3 is not a signal and should not be quoted. `git log --all -i
--grep=AP-W1D --grep=classifier --grep=W1C-02` returned two commits
(`20388090`, `68b01852`) on unrelated subjects — deploy-engine health reporting and strategy
versioning — matched on generic terms, not on this decision set.

### 2.2 D1–D5 are not in any register

AP-W1D-3 is the only item with a formal ledger entry. **D1 through D5 appear nowhere except
in my own W1D-09 and W1D-10 reports**, where they are recorded as recommendations with status
`UNRESOLVED`. They have never been put to an owner in a tracked register. That is itself a
process gap: five decisions that gate an implementation exist only inside advisory documents.

---

## 3. Decision reconciliation

Each classified strictly. A recommendation, a report's existence, and silence are **not**
grants.

| # | Decision | Recommended (by whom) | Ledger entry | **State** |
|---|---|---|---|---|
| **AP-W1D-3** | Authorize implementation of the specified files + regression tests | W1D-06 → W1D-10 | **yes**, line 802 | **UNRESOLVED** (recorded `NOT GRANTED`) |
| **D1** | Ambiguous-key findings are **hard failures with distinct labels** | W1D-09 §3.4, W1D-10 §5 | none | **UNRESOLVED** |
| **D2** | Reordered identical rows classified **unchanged** | W1D-06 §6.3(a), W1D-06x prototype, W1D-10 §5 | none | **UNRESOLVED** |
| **D3** | **One shared classifier, three existing renderers**, staged across two separately reviewable changes | W1D-10 §2.4 | none | **UNRESOLVED** |
| **D4** | Place the classifier in **`scripts/verify_provisioning.py`** | W1D-10 §2.2 | none | **UNRESOLVED** — but **technically constrained**: any other placement except a new module closes an import cycle |
| **D5** | Preserve `check_drift()`'s **three-tier severity routing exactly** | W1D-10 §2.3, §5 | none | **UNRESOLVED** |

**GRANTED: 0. REJECTED: 0. UNRESOLVED: 6.**

### 3.1 Two notes that matter for the decision, not just its bookkeeping

- **D4 is the least free of the six.** It reads as a placement preference but is largely
  decided by the import graph: `verify_provisioning` imports neither sibling, both siblings
  import it, so a classifier in `verify_schema_drift.py` would require
  `verify_provisioning → verify_schema_drift` for `diff_snapshots()` and close a cycle. The
  genuine choice is `verify_provisioning.py` **or** a new `scripts/_drift_classify.py`; the
  recommendation is the former because it needs no new file and sits beside the
  `COMPARISON_KEY` it consumes.
- **D2 is the only decision that changes observable behaviour.** Reordered-identical rows go
  from hard failure to clean. W1D-05's Guard A proves all 16 current queries carry a total
  `ORDER BY`, so no dimension can reach that path today — the change removes a trap rather
  than altering a current result. It still needs explicit acceptance because it is a verdict
  change, and because the only currently-unreachable path is exactly the kind that becomes
  reachable when a seventeenth dimension is added.

---

## 4. Owner roles for each outstanding decision

Derived from the ledger's owner column and from file ownership evidence. **No individual is
named anywhere in the repository**: `git log` shows a single committer for all history, and
all three PRs in the adjacent D5′ line carry 0 assignees and 0 reviewers. So these are
**roles**, not people.

| Decision | Role that must decide | Basis |
|---|---|---|
| **AP-W1D-3** | **Code owner** | Named explicitly in ledger line 802 |
| **D1** (ambiguous severity) | **Code owner** | Output semantics of `verify_schema_drift.compare()` |
| **D2** (reorder verdict) | **Code owner** | Verdict semantics; the only behavioural change |
| **D3** (scope) | **Code owner** | Spans all three scripts |
| **D4** (placement) | **Code owner** | Module structure; technically constrained (§3.1) |
| **D5** (preserve `check_drift()` routing) | **Code owner**, with the **production-audit stakeholder** | `verify_production_schema.py` is the production audit tool; its three-tier severity policy — a hard-coded 10-dimension critical list — is a product decision about audit criticality, not a classification detail |

Adjacent and independent, needing different roles:

| Item | Role | Why it matters here |
|---|---|---|
| AP-W1D-1 / AP-W1D-5 | `DB owner (read access)` / `Coordinator + DB owner` | Would settle whether any declared key is actually unique in a live catalogue — verified for **0 of 16**. The classifier is designed to stay correct without this; obtaining it would let `modified` be *trusted* rather than merely *safe*. **Not a blocker for AP-W1D-3.** |

---

## 5. The implementation handoff — conditional, not authorized

Recorded so an authorized task needs no further analysis. **This is not a work order.**

### 5.1 Change set, in the order it must land

| Step | File | Change | Gate |
|---|---|---|---|
| 1 | `scripts/verify_provisioning.py` | Add `WHOLE_ROW = "whole-row"`, `COMPARISON_KEY` (16 entries, widths per W1D-09 §5), and the pure `classify_dimension(dimension, expected, actual) -> ClassifiedDimension`. No I/O, stdlib only (`collections.Counter`). Existing code, `_norm()`, `_as_tuples()`, the snapshot format and all 16 SQL queries untouched. | AP-W1D-3 + D4 |
| 2 | `scripts/verify_schema_drift.py` | Route `compare()` through the classifier. Import `COMPARISON_KEY` alongside the existing `SNAPSHOT_QUERIES, _norm`. Preserve the two-tier failure/advisory return contract, the 12-row cap, `MISSING from the database` / `NOT IN this repository`, and exit codes 0/1/2. | AP-W1D-3 + D1 + D2 |
| 3 | `tests/test_drift_classification.py` | **New.** All 14 cases from W1D-10 §3 — including collision with **differing** attributes (case 7), duplicate multiplicity (cases 8, 9), null-safe determinism (case 13), verdict/detail consistency (case 14), and the backward-compatibility assertions (case 11 + §3.3). Pure data, no gate, no skip, no database. | AP-W1D-3 |
| 4 | `tests/test_snapshot_dimension_invariants.py` | Replace the test-local `WHOLE_ROW` / `COMPARISON_KEY` (lines 93, 100) with an import from `verify_provisioning`. This converts W1D-05's Guard B from checking a test constant into checking production data. **All 48 existing cases must still pass.** | AP-W1D-3 + D4 |
| — | `scripts/verify_provisioning.py::diff_snapshots`, `scripts/verify_production_schema.py::check_drift` | **UNCHANGED** in this change. Tracked as open defects carrying the same whole-tuple diff. | second change, D3 |

### 5.2 Invariants the implementation must preserve

1. `RESULT: NO DRIFT` and `RESULT: DRIFTED` **verbatim** — four launch documents quote them
   as evidence (`DATABASE_PROVISIONING_RUNBOOK.md`, `FINAL_LAUNCH_READINESS_REPORT.md`,
   `PAPER_ONLY_LAUNCH_READINESS.md`, `RLS_TENANT_ISOLATION_VERIFICATION.md`). No code parses
   them.
2. Exit codes 0 / 1 / 2 unchanged.
3. Advisory routing unchanged — `ADVISORY_DIMENSIONS` keeps its meaning and an advisory-only
   difference must not fail the run.
4. `check_drift()`'s three-tier severity routing byte-identical (D5).
5. **No nonzero drift may emit a line without a subject** — the explicit fix for today's
   `columns: 0 missing, 0 unexpected`.
6. Null-safe sorting: `_norm` preserves `None`, and `None < 'x'` raises `TypeError` in
   Python 3. Sort on `(element is None, str(element))`. `columns` has four un-coalesced
   attribute columns, so a `NULL` is reachable and a plain `sorted()` would crash on real
   data.
7. W1D-05's static guards not weakened: 48/48 passing, 12 mutations still biting.

### 5.3 Verification the authorized task must run

Sequentially, narrowest first; the mutation probe perturbs module dicts so it runs alone.

```
python -m pytest tests/test_drift_classification.py -q --no-header -p no:cacheprovider
python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider   # expect 48 passed
python staging/probe_p11b_w1d05_guard_mutation.py                                                 # expect 12 applied / 12 bit / 0 silent
python -m pytest <the five-file drift group>  -q --no-header -rs                                  # expect 103 passed, 12 skipped, 1 xfailed
```

The 12 skips are PostgreSQL-gated and **must not be counted as passes**. No PostgreSQL
catalogue or live-database claim may be made from any of these.

### 5.4 Rollback

Steps 1 and 3 are additive. Reverting step 2 restores the previous `compare()` body; step 3
must be reverted with it. Step 4 must be reverted with step 1. No data migration, no snapshot
regeneration, nothing to unwind — the change is confined to how two in-memory lists are
compared and printed.

---

## 6. Authorization state and the exact next permitted action

### 6.1 State

**No authorization exists.** AP-W1D-3 is recorded `NOT GRANTED` and is not superseded by any
report, commit or register. D1–D5 have never been formally put. Per the brief, this
coordination task is **not** implementation authorization, and I am not treating it as one.

### 6.2 The decision request

One reply resolves everything. Each item is independent; granting one implies nothing about
the others.

| # | Decision | Recommendation | Your call |
|---|---|---|---|
| **AP-W1D-3** | Authorize steps 1–4 of §5.1 (two production files, two test files) | **grant** | `GRANT` / `REJECT` |
| **D1** | Ambiguous-key finding = hard failure with a distinct label | **as stated** | `GRANT` / `REJECT` |
| **D2** | Reordered identical rows = unchanged *(only behavioural change)* | **as stated** | `GRANT` / `REJECT` |
| **D3** | One shared classifier + three existing renderers, two staged changes | **as stated** | `GRANT` / `REJECT` |
| **D4** | Classifier in `scripts/verify_provisioning.py` | **as stated** (alternative: a new `scripts/_drift_classify.py`; `verify_schema_drift.py` is **not** available — import cycle) | `GRANT` / `REJECT` |
| **D5** | `check_drift()` severity routing preserved exactly | **as stated** | `GRANT` / `REJECT` |

A minimal sufficient reply: *"AP-W1D-3 granted for the four files in §5.1; D1 yes; D2 yes;
D3 Option 1 two-commit; D4 verify_provisioning.py; D5 preserve."*

### 6.3 The exact next permitted action

**Stop.** Nothing further is achievable read-only — the specification (W1D-09), the readiness
plan (W1D-10) and this handoff leave no analysis that would change the implementation.

On a grant, implementation requires a **separately authorized task**. That task should
re-verify the four fingerprints in §1 immediately before editing, because a concurrent session
is currently reading these files (§7.2).

If the answer is *not yet*, the single next action needing nothing from the Code owner is
**AP-W1D-1** (`DB owner, read access`): it would establish whether any declared key is unique
in a live catalogue, which is the one open question the classifier cannot resolve by design.

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
| Untracked | 3551 (this session's `staging/` artifacts included) |
| Unmerged | **0** |
| Stash | 2 |
| **Digest A** (`sha256`, raw `git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` — **SAME** |
| **Digest C** (`sha256`, sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` — **SAME** |
| **Index tree** (isolated index + isolated object dir) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` — **SAME** |
| Objects into the isolated dir | **0** |
| Shared `.git/objects` | **198 → 198** |

The `.git/index` file hash was deliberately not used as the invariant — `core.autocrlf=true`
lets read-only `git status` rewrite its stat cache.

### 7.2 Concurrent work — one session is reading these files

A concurrent session is active on `PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md`
(artifacts `p11b24_discover.{py,txt}`, `p11b24_extract.{py,txt}`,
`p11b24_entry_status.txt`, 12:40–12:42). Its `p11b24_extract.txt` and
`p11b24_entry_status.txt` **do reference the drift/provisioning scripts**, and it quotes my
W1D-08/W1D-09/W1D-10 text.

**Assessment: a coordination survey, not a competing implementation.** Its destination is a
launch-critical-path coordination report, it is reading broadly across workstreams, and the
four candidate fingerprints are unchanged (§1) — so no competing edit exists. It is reported
because the brief requires concurrent conflicts be surfaced, and because an authorized
implementation task should re-verify fingerprints immediately before editing.

Also active and unrelated: `PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md` (12:37) and the
`p11b23_*` / `p11b_a23*` artifacts — the Redis-recovery line, touching none of these files.

One cross-session note: `p11b24_discover.txt` quotes the index content digest as
`7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` "SAME as A23". That is the
A14-method digest of the same unchanged state; it is **not** comparable with Digest A or C
above, which use different serialisations. All three agree the staged state has not moved.

The W1D line now has **seven** recorded parallel efforts (W1D-05, W1D-06, W1D-06x,
W1D-07, W1D-08, W1D-09/10, this phase). Consolidation remains an open coordination item and is
arguably now a larger risk than the defect itself.

### 7.3 Artifacts created, all untracked and under `staging/`

```
PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md   (this report)
w1d11_preflight.py, w1d11_preflight.txt          fingerprints, ledger, grant search, baseline, concurrency
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
No PostgreSQL catalogue claim is made. No decision in §6.2 is treated as granted.

---

## 9. Disposition

| Item | Status |
|---|---|
| **W1C-02** | **OPEN** — remains open until implementation and verification complete under explicit authorization |
| **AP-W1D-3** | **UNRESOLVED** — recorded `NOT GRANTED`; owner role `Code owner` |
| **D1 / D2 / D3 / D4 / D5** | **UNRESOLVED** — no ledger entry; owner role `Code owner` (D5 also production-audit stakeholder) |
| **Decisions approved** | **0 of 6** |
| **TENANT-01** | **OPEN** |
| **QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

Reconciliation is complete and the handoff is prepared. **Implementation does not follow
automatically and requires a separately authorized task.** Nothing in this report constitutes
an approval.
