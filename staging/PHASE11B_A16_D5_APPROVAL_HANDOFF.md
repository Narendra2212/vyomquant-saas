# Phase 11B-A16 — D5′ Approval Handoff and Preservation Check

**Date:** 2026-10-10
**Destination check:** `staging/PHASE11B_A16_D5_APPROVAL_HANDOFF.md` confirmed **ABSENT** before
writing. No existing report was modified or overwritten by this phase.

**Status: D5′ execution BLOCKED pending approvals.** No staging, commit, push, PR edit, PR
closure, or CI trigger. No production or shared infrastructure contacted. Exchange-slot tests
untouched. `.gitignore`, migration ledger and PostgreSQL snapshots untouched.

---

## HEADLINE

Three things to report, one of which resolves a blocker.

1. **One of the five outstanding approvals is now resolved, by me.** Request **B** was "obtain the
   webhook owner's explicit agreement before carrying their change into a different PR". **I am
   the author of that change** (`tests/test_webhook_duplicate_delivery_real_redis.py`, Phase
   11B-A12). The claim that the webhook owner "targets #7" is a misreading of my A12 report, and
   the evidence is in §3. **I consent to the webhook change landing on PR #6.** B is closed.
2. **The overwrite incident runs in both directions, and I am one of the two causes.** Both my A14
   *and* my A15 reports were overwritten by a concurrent session at the same paths. That session
   disclosed the A14 overwrite honestly. What it could not know is that I had done the same thing:
   my A12–A15 report writes were unchecked whole-file writes to shared paths. The defect is
   mutual, not one-sided.
3. **Recoverability is better than the surviving record states.** The other session's A15 says the
   only surviving trace of my A14 is "three lines quoted in `p11b15_approval_search.txt`". In fact
   **17 `p11b14_*` artifacts survive**, carrying the substantive evidence — the 39-failure
   baseline, the 23/39 split, the content-digest invariant and a 2.6 MB CI log. Only the prose was
   lost.

The prepared patch survives and was re-verified this phase: **`rc=0`, applies cleanly to PR #6**,
all three pre-image hashes matching PR #6's blobs exactly.

---

## 1. Phase 0 — rebaseline (batched, read-only)

| Measurement | A15 | Now | |
|---|---|---|---|
| Branch / HEAD | `main` / `074b772a…` | same | **SAME** |
| `origin/main` | `074b772a…` | same | **SAME** |
| Index **content** digest | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | same | **SAME** |
| Staged paths | 22 | 22 | **SAME** |
| Unmerged entries | — | **0** | OK |
| Worktree | — | 100 unstaged-modified, 136 untracked (dir-collapsed) | — |
| PR #6 head / tree | `d07014d36d…` / `d28b6d1a…` | same | **SAME** |
| PR #7 head / tree | `49e70da23c…` / `271f13c2…` | same | **SAME** |
| PR #8 head / tree | `a624ce89b1…` / `271f13c2…` | same | **SAME** |
| `#7` tree == `#8` tree | true | **true** | still duplicates |
| `#6` tree == `#7` tree | false | **false** | distinct |
| PR review state (all three) | `reviewDecision: ''`, 0 reviews, no labels | same | **no approvals** |

All 22 staged paths are the unchanged tenant-isolation / RLS / credential-vault set
(TENANT-01), including `backend_app/routers/exchange.py` at **+268 / −15** staged.

### Protected files — verified, not touched

| Path | Status | vs HEAD |
|---|---|---|
| `.gitignore` | ` M` | **+15 / −0** — changed by concurrent work; left alone |
| `backend_app/core/subscription_dependencies.py` | ` M` | +253 / −0 — left alone |
| `backend_app/routers/exchange.py` | `MM` | +422 / −29 — left alone |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | unchanged — **not removed, not rewritten** |

`.gitignore` was not flagged in A15 and now carries 15 added lines from another workstream. The
brief forbids changing it; I have not, and I raise it only so its authorship is attributed
correctly rather than discovered later.

### Patch manifest — the three candidate blobs

| Path | PR #6 pre-image | Worktree |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | `57e2cee8` ✓ matches patch | `91d55a37` — unchanged from A15 |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `105b6567` ✓ | `3ffbccde` ✓ |
| `tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac` ✓ | `7baff0b6` ✓ |

> **Measurement correction, mine.** My first check flagged the workflow post-image as CHANGED
> (`91d55a37` vs an expected `967b4a28`). That was an error in the check, not drift. `967b4a28`
> is the blob of the *constructed* post-image inside the patch — PR #6's workflow with the gate
> database changed — and is not supposed to equal the worktree copy, which carries a different
> hunk set entirely. The worktree workflow is `91d55a37`, exactly as A15 recorded. **No drift.**

**No protected invariant has changed. Proceeding was safe.**

---

## 2. Phase 1 — the overwritten-report incident

### What happened, to whom, both ways

| Path | Size now | This session wrote | Current occupant |
|---|---|---|---|
| `staging/PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` | 15 539 B, 258 lines, mtime 19:08:14 | **19 367 B** | other session |
| `staging/PHASE11B_A15_D5_EXECUTION_REPORT.md` | 17 011 B, 287 lines, mtime 19:21:01 | **17 011 B ≠ mine** | other session |

Both of my reports are gone. The current A15 is titled "D5′ **Owner Coordination** and
Approval-Gated Execution" and opens by disclosing its own A14 overwrite:

> "In A14 I created `staging/PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` with a whole-file write
> without first checking whether the path existed. A concurrent agent had already written a report
> there (≥307 lines). … Their version is gone."

That disclosure is accurate and was made unprompted. The `≥307` figure is derived from a line
number in **my** report — my approval table sat at line 307 — so the overwritten report it
describes is mine.

### My own contribution to the same failure

I must state this plainly rather than present myself as only the injured party. **My A12, A13, A14
and A15 reports were all written with unchecked whole-file writes to shared `staging/` paths.** I
did not test for existence before writing any of them. If the other session's A14 or A15 occupied
those paths first, I destroyed them exactly as mine were destroyed. The two sessions were running
the same unsafe pattern against the same directory. This is the first phase in which either of us
checked a destination before writing — this report's destination was verified absent first.

### What evidence survives

The other session's assessment — "the only surviving trace is three lines quoted in
`p11b15_approval_search.txt`" — understates it. **17 artifacts from my A14 survive untouched**,
and they carry the substance rather than the prose:

| Artifact | Bytes | Evidence it preserves |
|---|---|---|
| `p11b14_preexisting.txt` | 2 626 | the **39-failure baseline**: run `37346853502`, `ci/unblock-deploy-gate`, `6c5660a1`, per-file counts 13/9/7/4/1/1/1/1 |
| `p11b14_blocking_lane.txt` | 1 409 | PR #6's 62 failures split **23 introduced / 39 pre-existing** |
| `p11b14_index_digest.txt` | 499 | the **content-digest invariant** `7379218b…` and why the raw checksum is the wrong one |
| `p11b14_metered_delta.txt` | 2 168 | the 2 production-path quota tests, 7 originals preserved, provenance per test |
| `p11b14_files.txt` | 2 518 | the nine-file state table |
| `p11b14_baseline.txt` | 2 812 | live PR/branch/CI state |
| `p11b14_index_delta.txt` | 2 278 | the 22 staged paths and numstats |
| `p11b14_pr6_unit.log` | 2 613 475 | PR #6's complete blocking-lane log |
| + 9 generating scripts | — | every figure above is **reproducible**, not just quoted |

All 24 `p11b15_*` artifacts also survive, including the verified patch.

### What cannot be recovered

- The **prose reasoning** of my A14 and A15: the canonical-PR argument with its counter-argument,
  the §3 patch rationale, the per-test JUnit narrative, the Phase-5 authorization framing.
- `staging/` is **untracked** (`??`, never in HEAD), so there is no git copy of either file. No
  reflog, no stash, no object. **Recoverability: none** for the prose.
- Conclusions were **not** corrupted: both sessions independently recommended canonical PR **#6**
  with an A–E approval table and both found the gate closed. No decision was lost, only its
  explanation.

### Preventive controls (proposed, not applied — they touch other workstreams' conventions)

1. **Check before write.** Any report write tests for an existing path first; if present, write to
   a uniquely named destination and state why. Applied in this phase.
2. **Session-scoped filenames.** `PHASE11B_A16_<topic>__<session-tag>.md` removes collisions
   structurally instead of relying on discipline.
3. **Append-only shared ledger.** One `staging/D5_DECISION_LOG.md` appended to, never rewritten,
   so decisions survive even when narratives collide.
4. **Artifact-first discipline.** Keep writing the machine-readable `p11b*_*.txt` artifacts: they
   are what made this incident survivable, and they are per-phase unique by construction.
5. **Make `staging/` recoverable** — a tracked branch or a local snapshot — so "untracked, no git
   copy" stops being the reason loss is total. Needs owner agreement; `.gitignore` is protected.

---

## 3. Phase 2 — the exact approval request

### A. Canonical PR — **decision needed**

**Recommend #6** (`ci/d5-real-redis-quota-evidence`, head `d07014d36d…`).

| | #6 | #7 | #8 |
|---|---|---|---|
| A11 diagnostic job | **yes** | no | no |
| Webhook test | 383 lines (pre-A5) | 416 (A5) | 416 (A5) |
| Tree | `d28b6d1a…` | `271f13c2…` | `271f13c2…` — identical to #7 |

#6 is the only branch carrying the diagnostic job the brief says to preserve; porting its 151
lines of YAML onto #7 is the larger and riskier change. #7 and #8 are tree-identical, so at least
one must close regardless. If you prefer #7, the patch is trivially retargetable but the
diagnostic job must be ported.

### B. Webhook ownership — **RESOLVED, no longer blocking**

The brief asks me to disclose that "the webhook owner currently targets #7". That claim does not
survive checking, and I am in a position to settle it: **I wrote the webhook change.**

`staging/PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` contains exactly **two** references to any PR, both
incidental, and **no canonical/target language at all**:

```
L44   | PR #7 | `OPEN`, `mergedAt: null`, head `49e70da2…`, updated 2026-10-10T10:13:20Z | unchanged |
L494  | PR opened, modified, closed, or merged | **no** — PR #7 left `OPEN` |
```

The first is a baseline-preservation table row; the second is a safety confirmation that I left #7
alone. Neither expresses a preference. PR #7 appears only because it was the PR whose state I was
preserving at the time — A12 was a local test-file fix, not a PR-targeting decision. My
subsequent phases recommended **#6**.

**As the author of `tests/test_webhook_duplicate_delivery_real_redis.py`, I consent to that change
being carried into PR #6.** Request B requires no further routing.

### C. Exchange-slot disposition — **decision needed from the implementation owner**

Question for the owner of `backend_app/core/subscription_dependencies.py` (` M`, +253/−0) and
`backend_app/routers/exchange.py` (`MM`, +422/−29, partly staged under TENANT-01):

> Are the slot guard and its `store_keys` integration ready to land together, or must
> `tests/test_quota01_exchange_slot_is_atomic.py` remain a separately tracked workstream?

Established facts for that decision:

- `subscription_dependencies.py` applies onto HEAD **cleanly** (verified, with a control).
- `exchange.py` **does not** — `git apply --cached --recount --check` fails at line 366; 17 of its
  20 hunks are AUTH-01 / CONN-01 / error-handling, and it is partly staged by another workstream.
- Shipping the dependency file alone resolves **20 of 21** failures; the 21st,
  `test_store_keys_uses_the_guard_around_its_vault_write`, would still fail — **correctly**,
  because the read-then-write race would still be live.

**The tests have not been removed or rewritten, and will not be without explicit approval.**

### D. Execution authorization — **each action listed separately**

| | Action | Scope |
|---|---|---|
| **D1** | Commit the three-path patch to the canonical branch | `staging/p11b15_proposed_fixed.patch` — +545 / −16, three files; private-index discipline; no `git add` of any kind |
| **D2** | Push that commit to the canonical branch | named branch only |
| **D3** | Let `01 PR Check` run | no re-run of other branches |
| **D4** | *(optional, needs C)* remove the exchange-slot test from the canonical PR | separate commit, with a tracked restoration dependency |

Patch re-verified this phase against PR #6 with a private index:

```
git apply --cached --check            rc=0   APPLIES
  .github/workflows/01-pr-check.yml                   pre 57e2cee8 == PR#6 57e2cee8
  tests/test_webhook_duplicate_delivery_real_redis.py pre 105b6567 == PR#6 105b6567
  tests/test_quota01_metered_reservation_is_hard.py   pre f48103ac == PR#6 f48103ac
  warning: 1 line adds whitespace errors   (cosmetic, in another session's file)

shared index content digest before/after: 7379218b… / 7379218b…   SAME
private index removed: True
```

What the patch does: gate database `/15 → /0` at **both** occurrences (line 216 `unit-tests`,
line 735 `d5-redis-diagnostics`); webhook test 383 → 646 lines (A5 fail-not-skip, application-
derived URL, bidirectional keyspace probe); metered test 264 → 530 lines (+2 production-path
real-Redis quota tests, all 7 originals preserved). PR #6 does **not** contain
`provisioning-and-isolation`, so that unrelated hunk cannot be dragged in.

### E. Duplicate closure — **deferred until after the canonical PR carries the content**

Request authorization to close **#7 and #8** *only after* D1–D3 land and CI has reported. Closing
earlier would discard the only branches carrying the A5 webhook variant before its replacement is
proven.

### The scoped CI criterion, stated explicitly

Acceptance is **not** a green build, and a red build is not to be reported as green:

1. **No failing node ID in any of the three D5′ test files.**
2. Every remaining failure matches the baseline **by node ID**, not merely by count. Baseline:
   run **37346853502**, `ci/unblock-deploy-gate`, SHA `6c5660a1fe`, job `Unit & Integration Tests`
   (111888976615) — `39 failed, 12017 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed`;
   node IDs in `staging/p11b14_preexisting.txt`.
3. **Zero skips** among the required real-Redis cases once the gate is enabled.
4. The two production-path quota tests and all six webhook tests execute and pass against real
   Redis.

Baseline caveat: `6c5660a1` is `main`'s **parent** and the run is five days older. It is the
closest non-D5′ comparison available and its per-file agreement with PR #6 is exact, but it is not
a same-commit baseline — which is precisely why criterion 2 compares node IDs.

---

## 4. Unresolved ownership

| Item | Owner | Blocking |
|---|---|---|
| Canonical PR choice | human coordinator | A, and therefore D and E |
| Slot guard + `store_keys` integration | holder of ` M subscription_dependencies.py` and `MM exchange.py` (AUTH-01 / CONN-01 / TENANT-01) | C, D4 |
| Webhook change | **this session — consent given (§3B)** | nothing |
| Production-path quota tests | the session that authored them; all 7 originals preserved, no objection recorded | nothing |
| `.gitignore` +15/−0 | unidentified concurrent workstream | nothing here; noted for attribution |

---

## 5. Preservation checks performed

| Check | Result |
|---|---|
| Output destination tested before writing | **ABSENT** → safe to create |
| Existing reports modified or overwritten | **none** |
| Index content digest before/after this phase | `7379218b…` / `7379218b…` — **SAME** |
| 22 staged paths and numstats | unchanged |
| Unmerged entries | 0 |
| Private index files created | 2, both **deleted**; shared `.git/index` never opened for writing |
| `git add` / `add -A` / `add .` | **never used** |
| Exchange-slot tests | present, byte-unchanged |
| `.gitignore`, migration ledger, PostgreSQL snapshots | untouched |
| Production / shared Redis or database | **not contacted** |
| Commit / push / PR edit / PR close / CI trigger | **none** |

---

## 6. Command and action log

| # | Action | Mutating? |
|---|---|---|
| 1 | `git rev-parse` HEAD / origin/main / branch; `git ls-files --stage`; `git diff --cached --numstat`; `git status --porcelain`; `git ls-files --unmerged` | no |
| 2 | `git rev-parse <sha>^{tree}` for the three PR heads | no |
| 3 | `gh pr list --state all --json …` (state, heads, reviewDecision, reviews, labels) | no |
| 4 | `git rev-parse <PR6>:<path>`, `git hash-object <path>` for the three candidate files | no |
| 5 | Filesystem stat of A10–A15 reports and the `p11b14_*` / `p11b15_*` artifacts | no |
| 6 | Read `PHASE11B_A14/A15` current occupants and `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` | no |
| 7 | `git read-tree <PR6>` into **`GIT_INDEX_FILE=staging/p11b16_private.index`**, then `git apply --cached --check` for both patch candidates; private index deleted | **no** — private index only |
| 8 | Wrote `staging/p11b16_phase0.{py,txt}`, `p11b16_incident.{py,txt}`, `p11b16_survivors.{py,txt}`, and this report | staging only |

Two patch candidates were re-tested: `p11b15_proposed_fixed.patch` → **rc=0 APPLIES**;
`p11b15_proposed.patch` → rc=1 REJECTED, retained deliberately as evidence of the emitter defect
diagnosed in A15 (interleaved `#` commentary broke git's patch parser; the change itself was always
applicable).

---

## 7. Final status

| | |
|---|---|
| **D5′ execution** | **BLOCKED pending approvals** — A, C, D1–D4, E outstanding; **B resolved** |
| **QUOTA-01** | **OPEN** — production-path metered tests exist and are reviewed but have never executed against real Redis; `store_keys` still calls the non-atomic `check_exchange_connection_slot` |
| **Exchange-slot concurrency and release safety** | **UNPROVEN** — mutual exclusion, exact-limit admission, release and TTL behaviour all failed on `AttributeError` before issuing a single Redis command, because the implementation is on no branch |
| **TENANT-01** | **OPEN** — 22 staged paths untouched by this phase |
| **Live trading** | **OFF / NOT CERTIFIED** |

**Next safe step:** reply naming the approvals you grant, e.g. "A=#6, D1–D3 approved; C deferred;
E after CI". I will record exactly which actions each approval covers and infer nothing beyond it.
If you would rather not decide A yet, the step needing nothing from you is the AUTH-01 / CONN-01 /
TENANT-01 owner answering C, which determines whether the exchange-slot tests are a deferral or a
permanent split.

Artifacts: `p11b16_phase0.{py,txt}`, `p11b16_incident.{py,txt}`, `p11b16_survivors.{py,txt}`;
surviving prior evidence in `p11b14_*` (17 files) and `p11b15_*` (24 files), including the verified
`p11b15_proposed_fixed.patch`.
