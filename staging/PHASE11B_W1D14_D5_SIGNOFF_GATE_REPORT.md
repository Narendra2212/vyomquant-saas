# Phase 11B / W1D-14 — D5 Sign-off Gate and Second-Change Readiness

**Date:** 2026-10-11
**Destination verified ABSENT before writing.** No existing report or artifact was overwritten.

## OUTCOME: **NO IMPLEMENTATION — SIGN-OFF REQUIRED**

Phase 0 passed: the W1D-13 result is intact and nothing concurrent has touched the five
paths. Phase 1 found **no affirmative D5 production-audit sign-off anywhere**, so Phase 2 was
not entered and **no file was edited**.

| | |
|---|---|
| Phase 0 preservation gate | **PASSED** — 15/15 |
| D5 production-audit sign-off | **ABSENT** |
| Second change (`diff_snapshots`, `check_drift`) | **NOT STARTED** |
| W1C-02 | **OPEN** — assessment in §5 |
| TENANT-01 / QUOTA-01 | **OPEN** |
| PostgreSQL integration | **UNVERIFIED** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Fresh baseline and authorization evidence

### 1.1 Preservation gate — 15 checks, all passed

| Check | Value | Result |
|---|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **PASS** |
| Branch / divergence | `main` / `0 0` | **PASS** |
| Staged count | 22 | **PASS** |
| Digest A | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **PASS** |
| Digest C | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **PASS** |
| Unstaged / untracked / unmerged / stash | 102 / 3578 / **0** / 2 | recorded |
| Worktrees | 1 (`main`) | recorded |

**W1D-13's result is intact** — all five fingerprints match the values that report recorded:

| Path | Blob | Result |
|---|---|---|
| `scripts/verify_provisioning.py` | `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | **PASS** |
| `scripts/verify_schema_drift.py` | `f853c1482ca295440c78e4f25a8989438fab041e` | **PASS** |
| `tests/test_drift_classification.py` | `fcffbe8e01367aa7f99fffe2677267821fb8424d` | **PASS** |
| `tests/test_snapshot_dimension_invariants.py` | `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | **PASS** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` — **still the original** | **PASS** |

**Concurrent edits to any of the five paths: none.** All five are clean in both the staged
and unstaged layers.

### 1.2 The authoritative ledger

`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` lines 800–804 — all five AP-W1D items
remain `NOT GRANTED`. **D5 is not an AP-W1D ledger item at all**; it exists only in the
W1D-12 decision record.

### 1.3 What the decision record says

`staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md`:

```
L24  | **D5** | Preserve check_drift()'s existing three-tier severity routing **exactly**
            | **CONDITIONALLY GRANTED -- condition NOT satisfied** | Code owner |
L69  You granted D5 *subject to* production-audit stakeholder sign-off. **That sign-off has not
L72  recording D5 as YES**, and §1 shows it as conditionally granted with the condition open.
L83  | D5's condition gates | the **second** change (routing diff_snapshots() and check_drift()) |
L221 | **D5** | **CONDITIONALLY GRANTED** -- production-audit stakeholder sign-off **outstanding** |
```

### 1.4 Repository-wide sign-off search — one candidate, and it is a false positive

Searched all `.md`, `.txt`, `.json`, `.yml` for narrow affirmative phrasing
(`sign-off given/granted/received/recorded`, `production-audit owner/stakeholder
approves/approved/signs off`, `D5 = signed-off/approved/granted`, `I approve … severity /
check_drift / audit`).

**One candidate**, in `staging/PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md` line 77:

> *"A repository-wide sweep for affirmative grant language (`I approve`, `hereby grant`, …"*

That is my own earlier report **describing the search terms it used**, not granting anything.
The "records approving both functions" hit is the same file for the same reason. **Zero
genuine sign-offs.**

### 1.5 A naming collision worth flagging

`git log -i --grep=D5` returns five commits — `72bae29b`, `f5b99e57`, `d07014d3`,
`a624ce89`, `49e70da2` — all from the **D5′ real-Redis** workstream
("D5-prime: let the real-Redis tests read the database the gate actually writes", etc.).
That is an entirely different thing from classifier decision **D5**.

The token `D5` is overloaded in this repository. Anyone searching git history for evidence
that "D5 was approved" will find commits that have nothing to do with the severity routing.
Recorded so the two are not conflated in either direction.

### 1.6 Explicitly not treated as approval

Per the brief, none of these was taken as a sign-off: W1D-13's 124 passing tests; the
classifier being technically correct; the real-snapshot corroboration in W1D-13 §3.5; the
Code owner's conditional *"D5 — YES, subject to production-audit stakeholder sign-off"*;
W1D-10/W1D-11's recommendations; or silence.

---

## 2. Exact files changed

**None.** Phase 2 was not entered. The five paths carry the fingerprints in §1.1, identical
to W1D-13's exit state.

Artifacts created, all untracked and under `staging/`:

```
PHASE11B_W1D14_D5_SIGNOFF_GATE_REPORT.md   (this report)
w1d14_gate.py, w1d14_gate.txt               the gate and the sign-off search
```

---

## 3. Tests actually executed

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `python staging/w1d14_gate.py` | 3 | Phase 0 passed; Phase 1 flagged 1 candidate for human reading (§1.4 — false positive) |
| 2 | `pytest tests/test_drift_classification.py tests/test_snapshot_dimension_invariants.py -q --no-header -p no:cacheprovider` | **0** | **124 passed, 0 failed, 0 skipped, 0 xfailed** in 13.58s |

No failures, no retries, no skips, no xfails in command 2. The gate's exit 3 is its own
"needs human reading" signal, not a test failure; I read the candidate and classified it.

**Evidence deliberately reused rather than re-run.** The five fingerprints are byte-identical
to W1D-13's exit state, so that report's results stand unchanged and were not repeated: 76 +
48 focused, 12/12 mutations biting, and the five-file group at 103 passed / 12 skipped /
1 xfailed. Command 2 above is a fresh confirmation that the 124 focused cases are still green
under the current tree.

**Not run, and why:** the mutation probe and the five-file group would re-measure unchanged
files. The PostgreSQL-gated tests were not run and **their skips are not passes** — no
database integration is claimed. No CI was dispatched and no production database contacted.

---

## 4. Preservation results

| Item | Result |
|---|---|
| Files changed | **none** |
| `scripts/verify_production_schema.py` | **byte-identical**, `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` — unmoved |
| Staged count | **22** — unchanged |
| Digest A / Digest C | unchanged |
| Unmerged paths | **0** |
| Staged index and unrelated uncommitted work | **preserved** — 102 unstaged and 3578 untracked paths untouched |
| `reset`, `clean`, `stash`, `checkout`, branch switch, commit, remote Git | **none used** |
| Database, Redis, network, CI | **none contacted** |

---

## 5. Can W1C-02 be closed under its existing acceptance criteria?

**Not yet — and the reason is narrower than it looks.**

W1C-02's criterion, as recorded across W1D-05 §9, W1D-06 §1 and the W1C recovery report
§597, is *"OPEN pending implementation and regression evidence"*, and the finding names
`verify_schema_drift.py` specifically.

| Criterion | State |
|---|---|
| Implementation in `verify_schema_drift.py` | **DONE** (W1D-13) — `compare()` routed through the shared classifier |
| Regression evidence | **DONE** — 76 runtime tests, the first ever to call `compare()`; 48 static guards preserved and now checking the production key map; 12/12 mutations biting |
| The misleading output itself | **GONE** — a modified object is named once with the changed attribute; no drift can be reported without a subject |

On the narrowest reading of the finding's own wording, those criteria are met. Two things
argue against closing:

1. **The identical defect persists in two sibling tools.** `diff_snapshots()`
   (`verify_provisioning.py:234`) and `check_drift()` (`verify_production_schema.py:217`)
   still carry the whole-tuple membership diff. Closing W1C-02 now would record "fixed" while
   two tools continue producing the same misleading report — including the **production
   audit** tool. That is the second change, and it is what D5 gates.
2. **No drift run has exercised the new path against a live schema.** All W1D-13 evidence is
   pure-data unit tests plus a file-based snapshot self-comparison. That is strong evidence
   about the classifier and **no evidence at all** about PostgreSQL.

**Recommendation: keep W1C-02 OPEN**, with its remaining scope stated as exactly those two
items. Closing it is the finding owner's decision, not mine, and I am not recording a
closure.

---

## 6. Remaining blockers

| # | Blocker | Owner role | Status |
|---|---|---|---|
| **1** | **D5 production-audit sign-off** — the only thing stopping the second change | **production-audit stakeholder** | **ABSENT** |
| 2 | A real drift run exercising the new `compare()` against a live schema | DB owner / coordinator — needs **AP-W1D-1** or **AP-W1D-5** | both **NOT GRANTED** |
| 3 | Key uniqueness unverified for all 16 dimensions | DB owner (read access) — **AP-W1D-1** | **NOT GRANTED**; the classifier is built to stay correct without it (decision D1) |
| 4 | Nothing is committed — W1D-13's work is unstaged and untracked | Code owner | one `git checkout` from loss, as is all other uncommitted work in this tree |

---

## 7. The decision request

### The exact missing decision

> **D5 production-audit sign-off.** Approve that `check_drift()`'s existing three-tier
> severity routing is preserved **exactly** when its whole-tuple diff is replaced by the
> shared classifier.

### The authorized decision-maker

**The production-audit stakeholder** — the owner of `scripts/verify_production_schema.py`'s
criticality policy. Per W1D-12 §2 I could not identify any role holder by name from
repository evidence: `git log` shows a single committer for all history, there is no
`CODEOWNERS` file, and every PR carries 0 assignees and 0 reviewers. The role is named in the
W1D-12 record; no individual is attested.

### What the stakeholder is being asked to confirm

`check_drift()` routes drift findings into three tiers, and the routing is independent of how
the diff is computed:

| Tier | Dimensions |
|---|---|
| `f.critical` | `tables`, `columns`, `primary_keys`, `foreign_keys`, `row_level_security`, `policies`, `privileges`, `unique_and_check_constraints`, `functions`, `triggers` |
| `f.minor` | `alembic_version`, `recorded_migrations` (advisory), plus `indexes`, `sequences`, `extensions`, `enum_types` |

It also differs from `compare()` in ways the second change must not disturb: it takes a live
connection and a `Findings` sink, **prints directly**, returns the live snapshot rather than a
diff, caps enumerated rows at **8** (not 12), and labels removals `MISSING from production`
(not `MISSING from the database`).

> **Sufficient reply:** *"D5 signed off — preserve check_drift()'s three-tier severity
> routing, 8-row cap and `MISSING from production` label exactly; the shared classifier may
> replace its diff."*
>
> If instead the severity policy **should** change, that is a production-audit policy decision
> in its own right and needs its own scope statement — it is not part of W1C-02.

### Exact next permitted action

**Stop.** With sign-off, the second change needs a separately authorized task that re-runs the
Phase 0 gate immediately before editing, because the fingerprints in §1.1 are the thing that
makes editing safe rather than assumed.

Independently of D5, the one action needing nothing from the production-audit stakeholder is
**AP-W1D-1** (DB owner, read access), which would settle blockers 2 and 3.

---

## 8. Confirmations

This phase did **not**: edit any file outside `staging/`; create or modify a test; implement
any part of the second change; touch `scripts/verify_production_schema.py`; connect to
PostgreSQL, Redis or any network service; dispatch CI; stage, commit, push, reset, clean,
stash, checkout or switch branches; mutate the shared Git index; or overwrite any existing
report.

No approval was created or implied. No PostgreSQL catalogue or live-schema claim is made, and
no gated skip is counted as a pass.

| Item | Status |
|---|---|
| **Outcome** | **NO IMPLEMENTATION — SIGN-OFF REQUIRED** |
| **W1C-02** | **OPEN** — `compare()` fixed and covered; two sibling tools and the live run outstanding |
| **D5** | **sign-off ABSENT** — production-audit stakeholder |
| **TENANT-01 / QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNVERIFIED** |
| **Live trading** | **OFF / NOT CERTIFIED** |
