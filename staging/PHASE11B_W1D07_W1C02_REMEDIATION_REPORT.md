# Phase 11B / W1D-07 — W1C-02 Drift Comparison Remediation

**Date:** 2026-10-10
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## STATUS: **BLOCKED — stopped at the preflight authorization gate, before any edit**

**No source file, test, snapshot, migration or configuration was modified.** The preflight
required by §1 of the brief found that the authorization this phase depends on is **explicitly
recorded as NOT GRANTED**, so §1's stop condition applies and implementation was not begun.

| | |
|---|---|
| AP-W1D-3 (implement the classification) | **NOT GRANTED** — explicit negative on record |
| Shared-classifier scope across all three functions | **NO GRANT FOUND** |
| Reordered-identical-rows behaviour change | **NO GRANT FOUND** |
| W1C-02 | **OPEN** — reproduced (W1D-06), specified, **not implemented** |
| W1D-05 static guards | **INTACT** — 48/48 passing, untouched |
| PostgreSQL catalog correctness / live DB verification | **NOT CLAIMED, NOT PERFORMED** |
| TENANT-01 / QUOTA-01 / D5 Redis / live trading | **untouched** · live trading **OFF / NOT CERTIFIED** |

---

## 1. Authorization and approved scope

The brief states: *"Stop without editing if authorization is absent… Do not infer approval
from this prompt or from the readiness report."* The prompt's own framing — "Implement the
**approved** W1C-02 remediation" and "**Subject to** the preflight authorization" — is
therefore not treatable as the grant. I searched for an actual grant and found the opposite.

### 1.1 The decisive record

`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`, in a table of five approvals:

```
| **AP-W1D-1** | Evaluate per-statement completeness predicates for 022–025 …  | DB owner  | **NOT GRANTED** |
| **AP-W1D-2** | Insert 4 ledger rows for migrations verified FULLY APPLIED …  | DB owner  | **NOT GRANTED** |
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression
                 tests (§3.5–3.6)                                             | Code owner | **NOT GRANTED** |
| **AP-W1D-4** | Add the `_provenance` envelope and recapture the snapshot    | DB + code  | **NOT GRANTED** |
| **AP-W1D-5** | Install `pgserver`; create the isolated cluster and role     | Coord + DB | **NOT GRANTED** |
```

AP-W1D-3 is precisely this phase's authorization, and it is an explicit negative — not a
silence that might be read either way.

### 1.2 Is that negative superseded anywhere? No

| Search | Result |
|---|---|
| `AP-W1D-3` / `AP-W1D3` across all of `staging/` | **4 references**: 1 forward-looking mention (W1D-05:80), 2 requests (W1D-06:414, W1D-ledger:802), 1 incidental (W1D-ledger:808). **Zero grants.** |
| Grant-shaped language near "shared classifier" | **NO MATCH** |
| Grant-shaped language near "implement W1C-02" | **NO MATCH** |
| Grant-shaped language near "reorder(ed)" | 1 match — my own W1D-06 §8.5 text saying it *"needs explicit acceptance"*. A request. |
| `git log --all -i --grep` for `W1D-3` / `W1C-02` / `drift classification` | **no matching commit** |
| Repository-wide `GRANTED` scan (all `.md/.txt/.json/.yml`, excluding vendor dirs) | 181 affirmative lines, **all false positives** on inspection: licence texts (`python_portable/LICENSE.txt`, `Git/**`), SQL `GRANT`/privilege prose (`DATABASE_PROVISIONING_RUNBOOK.md`, `RLS_TENANT_ISOLATION_VERIFICATION.md`), billing entitlement grants, OTP session grants. **None mentions AP-W1D-3, W1C-02, or drift classification.** |
| Decision-log files found | `staging/PHASE11B_A*_APPROVAL_*.md`, `p11b15_approval_search.txt`, `p10k_redis_authorization.txt` — all D5′/Redis or PostgreSQL workstreams; none records a W1D grant |

**Methodological note on my own tooling:** the automated verdict line in
`staging/w1d07_grant_check.py` printed *"181 affirmative 'GRANTED' line(s) found"* because it
classified any `GRANTED` not adjacent to `NOT` as affirmative. That heuristic is too coarse
and its headline number is **wrong as a signal**. The conclusion above comes from reading all
181 contexts, not from that counter. Recorded so the artifact is not quoted out of context.

### 1.3 W1C-02's status in every report that mentions it

| Report | Line | Statement |
|---|---|---|
| `PHASE11B_W1C_RECOVERY_REPORT.md` | 597 | defect described, priority **P2** |
| `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` | 567, 837, 932 | *"remains OPEN… analysis and a design, not an implementation"* |
| `PHASE11B_W1D05_STATIC_GUARDS_REPORT.md` | 281 | *"OPEN pending implementation and regression evidence — the classification was **not** implemented"* |
| `PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md` | 15, 443, 446 | *"OPEN — reproduced, remediation specified, not implemented"* |

Four independent reports agree. None records an approval.

### 1.4 Scope ambiguity, independently of authorization

Even if AP-W1D-3 were granted, two of the brief's own prerequisites remain unresolved:

- **Shared classifier across all three functions.** W1D-06 §9 item 2 recorded this as
  **UNRESOLVED** with a recommendation, not a decision. The brief's §2 instructs a shared
  classifier, but §1 requires me to confirm *the owner* approved that scope.
- **Reordered-identical-rows verdict change.** W1D-06 §6.3(a) measured this as the one
  behaviour change the fix introduces (hard failure → clean) and flagged it as needing
  explicit acceptance. W1D-06 §9 item 3: **UNRESOLVED**.

So the stop condition is met on three independent grounds, not one.

---

## 2. Preflight and exit preservation measurements

| Measurement | Preflight | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **same** |
| `origin/main` / divergence | `074b772a…` / `0 0` | **same** |
| Worktrees | 1 (`main`) | 1 (`main`) |
| Tracked index entries | 2104 | 2104 |
| Staged paths | 22 | **22** |
| Unstaged tracked | 100 | **101** — concurrent, not mine (§2.2) |
| Untracked | 3477 | 3484 (this phase's own `staging/` artifacts) |
| Unmerged | 0 | 0 |
| Stash | 2 | 2 |
| **Digest A** (`sha256` of raw `git ls-files -s`, file order) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **SAME** |
| **Digest C** (`sha256` of sorted `git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **SAME** |
| **Index tree** (`git write-tree`, isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **SAME** |
| Shared `.git/objects` | 198 | **198 — delta 0** |

### 2.1 Isolation of the tree verification

The brief forbids using the shared index for temporary verification. Both the index **and**
the object directory were isolated:

```
GIT_INDEX_FILE                   = <temp copy of .git/index>
GIT_OBJECT_DIRECTORY             = <temp dir>
GIT_ALTERNATE_OBJECT_DIRECTORIES = <repo>/.git/objects    # read-only fallback
git write-tree  ->  6f40856cee2bc7ea4d741f69f4b1f68b463d70a4   (exit 0)
objects created in the isolated dir : 0
shared .git/objects                 : 198 -> 198
```

Zero objects were created even in the isolated directory — every tree for the current staged
state already exists and was reached through the alternate, so `write-tree` was a genuine
no-op. Both temp directories were deleted and their absence asserted. **The shared index was
never opened for writing and `git add` was never used.**

### 2.2 The unstaged count moved from 100 to 101 — not this phase

Identified rather than assumed. The two most recently modified tracked files are:

```
21:23:43  backend_app/core/subscription_engine.py
21:22:07  backend_app/backend/order_watchdog.py
```

Both are another workstream's concurrent edits. **None of this phase's five target paths
appears in the unstaged set**, and all four existing targets carry byte-identical blob hashes
at preflight and exit (§2.3).

Worth flagging to the relevant owner: `backend_app/core/subscription_engine.py` was measured
**clean** in earlier phases this session (it holds `reserve_quota`, the QUOTA-01 production
path). It is now modified by concurrent work. This phase did not touch it and makes no claim
about that change; it is recorded because the brief forbids modifying QUOTA-01 and the count
change would otherwise look like mine.

### 2.3 Target files — untouched, preflight = exit

| Path | Status | Size | Blob (preflight = exit) |
|---|---|---|---|
| `scripts/verify_provisioning.py` | `??` untracked | 16,404 | `51cb6d241de3ef437b662241379f8985f7d5a51e` |
| `scripts/verify_schema_drift.py` | `??` untracked | 12,170 | `f78b35a52552967d469a7cc7d33de92db0a65e3b` |
| `scripts/verify_production_schema.py` | `??` untracked | 50,868 | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` |
| `tests/test_snapshot_dimension_invariants.py` | `??` untracked | 28,067 | `bcb4f479fca2365cca2be61aae8961449e03beb9` |
| `tests/test_drift_classification.py` | **does not exist** | — | would have been created |

All four existing targets are **untracked** (`??`), never in `HEAD`. Two consequences: their
"blob hashes" are computed identifiers rather than stored objects, and there is no git copy to
restore them from if they were damaged — which is a further reason not to edit them without
authorization. **No conflicting concurrent edit was found on any target**: all mtimes and
hashes are unchanged from the W1D-06 measurements taken earlier this session.

---

## 3. Exact source and test files changed

**None.**

| Path | Intended change (§2 of the brief) | Actual |
|---|---|---|
| `scripts/verify_provisioning.py` | add production `COMPARISON_KEY` + `WHOLE_ROW` sentinel + shared classifier | **not changed** |
| `scripts/verify_schema_drift.py` | route `compare()` through the shared classifier | **not changed** |
| `scripts/verify_production_schema.py` | route `check_drift()` through the shared classifier | **not changed** |
| `tests/test_snapshot_dimension_invariants.py` | import production `COMPARISON_KEY`, drop the test-local copy | **not changed** |
| `tests/test_drift_classification.py` | new regression suite | **not created** |

Files created, all untracked and all under `staging/`:

```
PHASE11B_W1D07_W1C02_REMEDIATION_REPORT.md   (this report)
w1d07_preflight.py      w1d07_preflight.txt, w1d07_exit.txt
w1d07_grant_check.py    w1d07_grant.txt
w1d07_guards.txt
```

---

## 4. Classifier design — specified, not implemented

Carried from `PHASE11B_W1D06_W1C02_REMEDIATION_READINESS.md` §6, where it was demonstrated in
a probe against the real `compare()`. **It remains a specification.** Required-case coverage,
with the W1D-06 measured outcome:

| Required case | Specified behaviour | W1D-06 measured (probe) | Implemented |
|---|---|---|---|
| Attribute change on same key | one `modified`, no false removal+addition | `1 modified, 0 missing, 0 added` + `attr[4]: '' -> '(a = b)'` | **NO** |
| Modified object rendered once | single `MODIFIED <key>` line | confirmed | **NO** |
| Changed attributes identified | attribute position + old → new | confirmed | **NO** |
| Genuine removal | `missing` | `0 modified, 1 missing, 0 added` | **NO** |
| Genuine addition | `added` | `0 modified, 0 missing, 1 added` | **NO** |
| Identical snapshots | no drift | confirmed | **NO** |
| Empty dimensions | no drift | confirmed | **NO** |
| `None` vs empty string | distinguished | `1 modified` + `attr[0]: None -> ''` | **NO** |
| Whole-row dimensions | removal/addition only, no invented modification | `privileges` → `0 modified, 1 missing, 1 added` | **NO** |
| Advisory dimensions | still route to advisories | `recorded_migrations` → advisories, `1 modified` | **NO** |
| Reordered identical rows | no drift *(approval-gated)* | clean | **NO** |
| Duplicate multiplicity | report actual counts for the affected key | **partially** — W1D-06 §6.3(b) recorded the probe renderer printing `MODIFIED` with no delta; the brief now requires actual counts, so the specification needs this refinement before implementation | **NO** |
| No anonymous nonzero drift line | every emitted failure names a key or row | confirmed for all probe cases | **NO** |
| Ambiguous key collision | report explicitly or fail clearly | **NOT YET SPECIFIED** — new requirement in this brief's §3; W1D-06's design silently groups colliding rows into one key's multiset and would misclassify two distinct objects as a modification. **This must be designed before implementation.** | **NO** |

Two specification gaps are therefore open even once authorization arrives: duplicate-count
reporting detail, and key-collision handling. Both are new requirements in this brief that
W1D-06's design does not yet satisfy.

---

## 5. Tests run

No implementation was performed, so the brief's §5 verification lane (which begins *"After
inspecting the code and implementing the change"*) does not apply. One baseline confirmation
was run to establish that the guards are intact and that nothing in this phase disturbed them.

| # | Command | Exit | Passed | Failed | Skipped | xfail |
|---|---|---:|---:|---:|---:|---:|
| 1 | `python -m pytest tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **48** | 0 | **0** | 0 |

48 passed in 1.81s. Identical to the count recorded in W1D-05 and re-confirmed in W1D-06, so
**W1D-05's static guards are intact and unweakened.**

Not run, and why:

- `tests/test_drift_classification.py` — does not exist; it is the deliverable that
  authorization would unblock.
- The five-file drift/migration group — W1D-06 measured it at **103 passed, 12 skipped,
  1 xfailed, 0 failed**. With no production change since, re-running it would confirm an
  unchanged baseline at ~28s cost and prove nothing about a fix that was not made.
- Any PostgreSQL-gated test. **The 12 skips in that group are not passes**: all are
  `tests/test_schema_drift_checker_is_read_only_postgres.py`, gated on a live server, and this
  phase is forbidden from contacting a database.

---

## 6. Final diff and scope audit

| Audit item | Result |
|---|---|
| Changes outside the approved paths | **none — there are no changes at all** |
| Broken imports or circular dependencies | n/a — no code modified |
| Key-width coverage across 16 dimensions | **not implemented**; the reviewed widths remain only in `tests/test_snapshot_dimension_invariants.py`'s test-local `COMPARISON_KEY` |
| Duplicate multiplicity reporting | **not implemented**; specification gap recorded (§4) |
| Whole-row and advisory behaviour | **not implemented**; current behaviour unchanged |
| Consistency across the three production functions | **not achieved** — all three still carry the whole-tuple membership diff (`verify_schema_drift.py:203-204`, `verify_provisioning.py:240-241`, `verify_production_schema.py:242-243`) |
| W1D-05 guards weakened or removed | **no** — 48/48 pass, file byte-identical |
| SQL queries, migrations, snapshots, credentials, grants, Redis config | **unmodified** |
| Shared Git index preserved | **yes** — digest C and index tree identical; `git add` never used; verification used isolated index + object dir |
| Pre-existing staged / unstaged / untracked work preserved | **yes** — 22 staged unchanged; the one unstaged delta is another workstream's (§2.2) |
| Staging, commit, push, reset, rebase, branch change, CI trigger | **none** |
| PostgreSQL / Redis contacted | **no** |

---

## 7. Remaining ambiguity

1. **Key uniqueness is unverified.** The reviewed key widths rest on documented catalogue
   properties, not measurement. W1D-05 §8.1 and W1D-06 §8.2 both record this. A wrong width
   would let two distinct objects collide into one key and be reported as a modification —
   which is exactly the failure mode this brief's §3 now asks to be detected and reported
   explicitly. **Settling it requires a live database** (AP-W1D-1 / AP-W1D-5, both also
   NOT GRANTED).
2. **Key-collision handling is unspecified** (§4). Needed before implementation.
3. **Duplicate-count reporting detail is unspecified** (§4).
4. **External report-text consumers.** W1D-06 searched the repository and found that nothing
   in code or tests parses the literal `"N missing, M unexpected"` string — only `staging/`
   reports and probes quote it. An out-of-tree consumer (a CI job defined elsewhere, a
   runbook, a dashboard) would not be visible from here.
5. **Scope across the three functions** and **the reorder verdict change** remain owner
   decisions (§1.4).

---

## 8. Completion criterion assessment

The brief's criterion: *all three authorized consumers use the shared classifier, regression
tests cover the original misleading policy case and multiplicity defect, W1D-05 guards remain
intact, focused tests pass, and shared Git state is preserved.*

| Criterion | Met |
|---|---|
| All three consumers use the shared classifier | **NO** — not implemented |
| Regression tests cover the policy case and the multiplicity defect | **NO** — not written |
| W1D-05 guards intact | **YES** — 48/48, file unchanged |
| Focused tests pass | **YES** for the existing guard file; the new file does not exist |
| Shared Git state preserved | **YES** — digests and index tree identical, 22 staged untouched |

Two of five criteria are met. **W1C-02 remains OPEN.**

---

## 9. What would unblock this

A single explicit grant, from the code owner who holds the drift/provisioning tooling:

> **AP-W1D-3 granted** — implement the four-way drift classification in
> `scripts/verify_provisioning.py`, `scripts/verify_schema_drift.py`,
> `scripts/verify_production_schema.py`, `tests/test_snapshot_dimension_invariants.py` and a
> new `tests/test_drift_classification.py`.

Plus the two scope decisions, which can be answered in the same reply:

> **Scope** — one shared classifier routed through all three functions (recommended), or
> `compare()` only, leaving `diff_snapshots()` and `check_drift()` for a later change.
>
> **Reorder behaviour** — accept that reordered identical rows change from hard failure to
> clean. It is the correct answer and W1D-05's Guard A means no current dimension can reach
> that path, but it is a behaviour change.

Two specification items should be settled before coding, and neither needs an owner: the
duplicate-count output format and the key-collision detection rule (§4). I can draft both
read-only on request.

Nothing in this report constitutes an approval, and nothing in it should be read as one.

---

W1C-02 **OPEN** · AP-W1D-3 **NOT GRANTED** · implementation **NOT STARTED** ·
W1D-05 guards **INTACT (48/48)** · PostgreSQL catalog correctness **NOT CLAIMED** ·
live database verification **NOT PERFORMED** · TENANT-01 **OPEN** · QUOTA-01 **OPEN** ·
live trading **OFF / NOT CERTIFIED**
