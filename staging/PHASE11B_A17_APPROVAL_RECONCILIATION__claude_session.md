# Phase 11B-A17 — Approval Reconciliation and Execution Readiness

**Date:** 2026-10-10
**Session tag:** `claude_session`
**Destination check:** `staging/PHASE11B_A17_APPROVAL_RECONCILIATION__claude_session.md` verified
**ABSENT** immediately before writing. A concurrent `PHASE11B_A17_D5_READINESS__kiro_session.md`
already exists and was **read, not modified**. No existing report or artifact was overwritten.

**Read-only phase, honoured.** No source change, staging, commit, push, PR edit or closure, CI
trigger, database or Redis access, package install, or test removal.

---

## HEADLINE

Everything is unchanged since A16, the patch still verifies cleanly, and **no approval exists**.
Three substantive additions this phase:

1. **The slot-guard entanglement is differently shaped than A13/A14/A16 said.** `AUTH-01`,
   `CONN-01` **and** `QUOTA-01` markers appear **only in the unstaged layer** of `exchange.py` —
   none is in the staged layer. The staged `+268 / −15` is TENANT-01 work that grows `store_keys`
   from 84 to 120 lines **without adding the guard at all**. My earlier reports described the
   conflict as "slot guard vs staged AUTH-01/CONN-01"; it is actually three workstreams sharing
   one *unstaged* layer, stacked on a staged TENANT-01 base.
2. **The three-path patch is verified safe at the granularity the brief asks for.** Workflow is
   **+2 / −2 — exactly the two `AERORA_TEST_REDIS_URL` lines**, `/15 → /0`; the diagnostics job
   and the provisioning job are untouched. Both test files **preserve every existing test** and
   contain **zero executable `pytest.skip`** — the one textual match is a comment.
3. **An instruction conflict I did not resolve unilaterally.** §1 asks me to record
   `git write-tree`; the safety constraints forbid writing to the shared object database.
   `git write-tree` does exactly that. **I declined to run it** and explain in §1b. The concurrent
   session ran it and recorded `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`.

**Approval status: A, C, D1–D4, E all NOT APPROVED. B resolved only as far as A16 supports.**

---

## 1. Rebaseline and preservation invariants

### 1a. All SAME vs A16

| Invariant | Value | |
|---|---|---|
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | SAME |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | SAME |
| Index **content digest** (A16 method: SHA-256 over sorted `git ls-files --stage`) | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | **SAME** |
| Staged paths | 22 | SAME |
| Unstaged-modified / untracked (collapsed) | 100 / 137 | — |
| Unmerged entries | **0** | SAME |

Protected files — all unchanged, none touched:

| Path | XY | vs HEAD | blob |
|---|---|---|---|
| `.gitignore` | ` M` | +15 / −0 | `4473bd4c` |
| `backend_app/core/subscription_dependencies.py` | ` M` | +253 / −0 | `dcc4db12` |
| `backend_app/routers/exchange.py` | `MM` | +422 / −29 | `4b409735` |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | unchanged | `2d7ccbbb` |

### 1b. `git write-tree` — instruction conflict, flagged not resolved

§1 asks for `git write-tree` alongside the A16-method digest. The safety constraints say "Do not
run commands that mutate the shared index or shared Git object database." **`git write-tree` does
both**: it writes tree objects into the shared ODB and can rewrite the shared index's cache-tree
extension. I treated the prohibition as governing and **did not run it**.

What I did instead: recorded the A16-method content digest, which is a pure in-process hash over
`git ls-files --stage` output and mutates nothing. It is sufficient for the question being asked —
"did concurrent staged work change?" — and it is directly comparable to A14/A15/A16.

The concurrent session made the opposite call and recorded
`write-tree = 6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`. I note two things without prejudice: the
value is now on record either way, and their report states it "matches A16's recorded" value —
**my A16 recorded no write-tree value**, so that continuity claim cannot be checked against A16.
**Decision needed:** confirm whether `git write-tree` is permitted in future phases, since the two
instructions conflict.

Per the brief I have **not** compared the A16-method digest against the write-tree value; they are
different algorithms over different inputs and comparing them would be meaningless.

### 1c. Corrections to my own A16 report

| A16 claim | Correct |
|---|---|
| "17 `p11b14_*` artifacts survive untouched" | **16.** A16's own output listed `p11b14_phase0.py` as ABSENT and 16 as PRESENT; the prose said 17 |
| implied `p11b14_phase0.py` was lost | **It never existed.** I listed it speculatively in A16's survivor check; no such file was created in A14. Not a loss — a list error |

All four load-bearing evidence files are intact: `p11b14_preexisting.txt` (39-failure baseline),
`p11b14_blocking_lane.txt` (23/39 split), `p11b14_index_digest.txt` (digest invariant),
`p11b14_pr6_unit.log` (2.6 MB CI log). `p11b15_*` = 24 files, all present.

---

## 2. Current PR and CI state

| PR | Branch | Head | Tree | State | Merge state | Review | Checks |
|---|---|---|---|---|---|---|---|
| **#6** | `ci/d5-real-redis-quota-evidence` | `d07014d36d…` | `d28b6d1a5d61…` | OPEN | **UNSTABLE** | `''`, 0 reviews, no labels | **11** total, 1 not-success |
| **#7** | `ci/d5-prime-real-redis` | `49e70da23c…` | `271f13c29d3c…` | OPEN | **UNSTABLE** | `''`, 0 reviews, no labels | **10** total, 1 not-success |
| **#8** | `ci/d5-real-redis-switch` | `a624ce89b1…` | `271f13c29d3c…` | OPEN | **UNSTABLE** | `''`, 0 reviews, no labels | **10** total, 1 not-success |

All three: the single failing check is `Unit & Integration Tests`. All heads and trees identical to
A16. **`#7` tree ≡ `#8` tree** (still exact duplicates); `#6` differs. #6's extra check is the A11
diagnostic job, which exists on no other branch.

**No approval signal anywhere on the remote:** `reviewDecision` empty on all three, zero reviews,
zero labels, zero assignees.

---

## 3. Approval matrix

| | Item | Current evidence | Decision-maker | Status | Smallest next action |
|---|---|---|---|---|---|
| **A** | Canonical PR | A16 and the concurrent A17 both *recommend* #6; `reviewDecision: ''` on all three; #7≡#8 | human coordinator | **NOT APPROVED** | Name #6 or #7 in reply |
| **B** | Webhook author consent to carry the change into #6 | I authored `tests/test_webhook_duplicate_delivery_real_redis.py` (A12). A16 records my consent. My A12 report contains 2 incidental PR references and **no** canonical/target language | this session | **RESOLVED** — consent given, scope limited to carrying the change; confers nothing else | none |
| **C** | Slot guard + `store_keys` integration | §4 below: guard symbols exist only in unstaged layers; `exchange.py` unstaged patch rejected onto HEAD at line 366; `subscription_dependencies.py` applies cleanly | owner of the uncommitted `exchange.py` / `subscription_dependencies.py` work — **unidentifiable from repo evidence** (§4c) | **NOT APPROVED / owner unknown** | Route the §4d question |
| **D1** | Commit the three-path patch | Patch verified `rc=0` against current #6 head (§5) | human coordinator | **NOT APPROVED** | Grant D1 explicitly |
| **D2** | Push the named branch | depends on D1 | human coordinator | **NOT APPROVED** | Grant D2 explicitly |
| **D3** | Let the canonical PR's CI run | depends on D2 | human coordinator | **NOT APPROVED** | Grant D3 explicitly |
| **D4** | Remove the exchange-slot test | Tests present, byte-unchanged (`2d7ccbbb`); removal needs a tracked restoration dependency | human coordinator **+** C's owner | **NOT APPROVED** | Decide only after C |
| **E** | Close #7 / #8 | Both OPEN; #7≡#8; neither has changed | human coordinator | **NOT APPROVED — deferred by design** | Only after D1–D3 land and CI is reviewed |

**B's scope, stated narrowly so it cannot be over-read:** my consent covers *carrying the webhook
change into PR #6*. It is not consent to commit, push, trigger CI, close any PR, or remove any
test. A16's recommendation of #6 is a recommendation, not an approval, and I have not treated it
as one.

---

## 4. Slot-guard ownership and integration findings

### 4a. Where the guard symbols actually live

`SD` = `subscription_dependencies.py`, `EX` = `routers/exchange.py`. Three states inspected for
`EX`: `HEAD`, the **staged index**, and the **worktree**.

| Symbol | SD@HEAD | SD@work | EX@HEAD | EX@index | EX@work |
|---|---|---|---|---|---|
| `_slot_lock_key` | — | **yes** | — | — | — |
| `_acquire_slot_lock` | — | **yes** | — | — | — |
| `_release_slot_lock` | — | **yes** | — | — | — |
| `exchange_connection_slot_guard` | — | **yes** | — | — | **yes** |
| `check_exchange_connection_slot` | yes | yes | yes | yes | yes |

### 4b. The `store_keys` path, traced at all three states

| State | `store_keys` lines | uses `check_exchange_connection_slot` | uses the guard | wraps `vault.store_exchange_keys` |
|---|---|---|---|---|
| `HEAD` (what CI runs) | 84 | **yes** | **no** | yes, **unguarded** |
| staged index | 120 | **yes** | **no** | yes, **unguarded** |
| worktree | 214 | yes (as a fast pre-check) | **yes** | yes, inside `async with exchange_connection_slot_guard(...)` |

**This corrects my A13/A14/A16 framing.** Those reports described the obstacle as the slot guard
being entangled with *staged* AUTH-01/CONN-01 work. The markers tell a different story:

```
other workstreams sharing exchange.py (markers in the STAGED version):  (none)
markers added only in the WORKTREE (unstaged):  ['AUTH-01', 'CONN-01', 'QUOTA-01']
```

So AUTH-01 (MFA/AAL2), CONN-01 (reconnect) and QUOTA-01 (the guard) are **all three in the same
unstaged layer**, sitting on a staged TENANT-01 base that does not mention any of them. The
entanglement is *within the uncommitted work*, not between uncommitted and staged work. The
practical consequence is unchanged — and now better explained:

```
subscription_dependencies.py  unstaged patch -> HEAD :  rc=0  APPLIES
routers/exchange.py           unstaged patch -> HEAD :  rc=1  REJECTED
    error: patch failed: backend_app/routers/exchange.py:366
```

`exchange.py`'s unstaged hunks are expressed against the staged TENANT-01 state and interleave
three workstreams, so they cannot be lifted onto `HEAD`. **No exchange code was copied, staged,
rebased, or rewritten.**

### 4c. Owner identification — genuinely not possible from repository evidence

All of this work is **uncommitted**: `subscription_dependencies.py` is ` M` (unstaged only),
`exchange.py` is `MM`. There is no commit, author, or committer record to attribute. The staged
22-path set is identifiable *as* TENANT-01 by content (ADR-0006/0007, `tenant_db_context.py`,
`verify_rls*`, `tests/test_tenant_db_context*`), but that identifies a workstream, not a person,
and it is not the layer carrying the guard. **Owner: UNKNOWN.** Marking it unknown rather than
guessing.

### 4d. The exact decision question for that owner

> `backend_app/core/subscription_dependencies.py` (` M`, `+253 / −0`, zero deletions) applies onto
> `main` cleanly — verified `rc=0`, with `exchange.py` as a failing control. It supplies all four
> guard symbols (`_slot_lock_key`, `_acquire_slot_lock`, `_release_slot_lock`,
> `exchange_connection_slot_guard`).
>
> `backend_app/routers/exchange.py` (`MM`, `+422 / −29`) carries the `store_keys` call site but
> cannot be separated: its unstaged layer interleaves AUTH-01, CONN-01 and QUOTA-01, and is
> expressed against the staged TENANT-01 state (`git apply --check` fails at line 366).
>
> **Choose one:**
> 1. **Land both together** in your own change, with the `store_keys` guard integration intact.
> 2. **Land `subscription_dependencies.py` alone.** This resolves 20 of the 21 slot-lock failures.
>    `test_store_keys_uses_the_guard_around_its_vault_write` would remain red — **correctly**, as
>    an accurate marker that the vault write is still unguarded.
> 3. **Defer both.** The exchange-slot test file then stays in a separately tracked workstream and
>    is excluded from the D5′ PR (which additionally requires approval D4).
>
> Under options 2 and 3 the read-then-write race on `store_keys` remains **live on `main`**, and
> exchange-slot concurrency and release safety remain **UNPROVEN**.

`tests/test_quota01_exchange_slot_is_atomic.py` references the guard symbols 78 times across 22
tests. It has **not** been removed or rewritten, and will not be without explicit approval.

---

## 5. Three-path patch manifest and private-index verification

All hashes recomputed this phase; none trusted from history.

| Path | Patch pre-image | Current PR #6 blob | Match | Worktree blob (post-image source) |
|---|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | `57e2cee8` | `57e2cee8ea448266efafa1441c2443a3c668e06e` | ✓ | `91d55a379115c88edd2fd6be01871b82a75ef498` |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `105b6567` | `105b65671b279efb958905c718e1831719ad26ee` | ✓ | `3ffbccde68c3b57485a9971d4977c34aff598b47` |
| `tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac` | `f48103ac79a2b7adf76339aab0f2861168b1defc` | ✓ | `7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` |

```
git read-tree d07014d3        -> private index (GIT_INDEX_FILE=staging/p11b17_private.index)
git apply --cached --check    -> rc=0   APPLIES CLEANLY
  warning: 1 line adds whitespace errors   (cosmetic; in the other session's metered file)

index content digest before/after : 7379218b992868bb / 7379218b992868bb   SAME
private index file removed        : True
```

Patch artifacts byte-identical to A16: `p11b15_proposed_fixed.patch` 34 947 B
(sha256 `af3848ef676e6cba…`), `p11b15_proposed.patch` 35 695 B (`7a5f084c9c0a9bb7…`, still
rejected, retained as evidence of the A15 emitter defect). **No automatic repair attempted** —
none was needed.

### Workflow: only the intended Redis DB selection, at both locations

```
hunks: @@ -213,7 +213,7 @@   and   @@ -732,7 +732,7 @@
+2 / -2  — the entire change:
  -          AERORA_TEST_REDIS_URL: redis://localhost:6379/15      (unit-tests)
  -      AERORA_TEST_REDIS_URL: redis://localhost:6379/15          (d5-redis-diagnostics)
  +          AERORA_TEST_REDIS_URL: redis://localhost:6379/0
  +      AERORA_TEST_REDIS_URL: redis://localhost:6379/0

diagnostics job present in PR #6 base : True
patch touches the diagnostics job     : False   -> PRESERVED
patch touches the provisioning job    : False   -> untouched
```

### Tests: preservation, and no silent skip conversion

| | base (PR #6) | post-image | |
|---|---|---|---|
| **webhook** tests | 6 | 6 | **0 removed, 0 added** |
| webhook `pytest.skip(` | 1 | 1 | the single match is a **comment** at L399 (`# This was \`pytest.skip(...)\``); **0 executable skips** |
| webhook executable `pytest.fail(` | 0 | **6** | fail-closed guards added |
| webhook `require_real_redis()` | 2 | 2 | gate behaviour unchanged |
| **metered** tests | 7 | 9 | **0 removed**; +2 production-path real-Redis tests |
| metered `pytest.skip(` | 0 | **0** | — |
| metered executable `pytest.fail(` | 0 | **4** | — |
| metered `require_real_redis()` | 1 | **3** | both new tests gate fail-closed |

**No required real-Redis test is silently converted into a skip.** The only `skip` token in either
post-image is prose.

### A claim from the concurrent A17, checked

It states that the worktree blob IDs every prior D5′ report quoted are "computed digests, not
stored Git objects". **True, and verified:**

```
91d55a379115  (workflow worktree)   in-ODB = False
3ffbccde68c3  (webhook worktree)    in-ODB = False
7baff0b64f0c  (metered worktree)    in-ODB = False
57e2cee8ea44  (PR#6 workflow)       in-ODB = True    <- the patch PRE-image
```

**Not consequential, though.** `git hash-object` computes without writing, which is exactly the
correct non-mutating way to fingerprint a worktree file for drift detection — and is why this
phase could run read-only. Patch applicability depends on the *pre-image*, which **is** a stored
object and does match.

---

## 6. Exact next actions

### Authorized now (require nothing further)

| | Action |
|---|---|
| 1 | Nothing that mutates anything. This phase is complete; no authorized mutation exists |
| 2 | Route the §4d question to the `exchange.py` / `subscription_dependencies.py` owner — a communication, not a repository action |

### Not authorized — awaiting explicit grant

| | Action | Gate |
|---|---|---|
| D1 | Commit `p11b15_proposed_fixed.patch` to the canonical branch | A then D1 |
| D2 | Push that branch | D1 |
| D3 | Let `01 PR Check` run | D2 |
| D4 | Remove `tests/test_quota01_exchange_slot_is_atomic.py` | C then D4 |
| E | Close #7 / #8 | after D1–D3 and CI review |

On grant I will: re-verify HEAD and the live PR head immediately before each remote write; build
the commit with `write-tree` / `commit-tree` / `update-ref` against a **private** index file; never
run `git add`, `git add -A`, `git add .`, `git checkout`, or `git commit`; and recompute the
content digest and all 22 staged paths and blob identities afterwards.

### Acceptance criterion, unchanged and still scoped

1. **No failing node ID in any of the three D5′ test files.**
2. Every remaining failure matches the baseline **by node ID**, not by count. Baseline: run
   **37346853502**, `ci/unblock-deploy-gate`, SHA `6c5660a1fe`, job `Unit & Integration Tests`
   (111888976615) — `39 failed, 12017 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed`;
   node IDs in `staging/p11b14_preexisting.txt`.
3. **Zero skips** among the required real-Redis cases once the gate is enabled.
4. The two production-path quota tests and all six webhook tests execute **and pass** against real
   Redis.

A red build whose failures all match the baseline is **not** a green build and will not be
described as one.

---

## 7. Discrepancies requiring a human decision

| # | Discrepancy | Needed |
|---|---|---|
| 1 | **`git write-tree` instruction conflict** — §1 requests it; the safety constraints forbid shared-ODB writes. I declined; the concurrent session ran it | Confirm which governs in future phases |
| 2 | **Two A17 reports now exist** from two sessions, both read-only, with compatible conclusions | Decide whether one is authoritative, or adopt the append-only decision log proposed in A16 |
| 3 | **Canonical PR still unchosen** after three phases of recommendation | Decision A |
| 4 | **Slot-guard owner unidentifiable** from repository evidence because the work is uncommitted | Identify the owner out-of-band, or decide §4d centrally |
| 5 | **Baseline is `main`'s parent**, five days old; no `01 PR Check` run exists on `main` | Accept node-ID comparison (criterion 2) as the mitigation, or authorize a fresh baseline run |

---

## 8. Preservation and action log

| Check | Result |
|---|---|
| Destination tested before writing | ABSENT → safe |
| Existing reports / artifacts modified | **none**; concurrent A17 read only |
| Index content digest before/after | `7379218b…` / `7379218b…` — **SAME** |
| 22 staged paths, numstats, blob ids | unchanged |
| Unmerged entries | 0 |
| Objects written to shared ODB | **none** (`git write-tree` deliberately not run; `apply --check` writes nothing) |
| Private index files | 1 created, **deleted**; shared `.git/index` never opened for writing |
| `git add` variants | **never used** |
| Exchange-slot tests | present, byte-unchanged (`2d7ccbbb`) |
| `.gitignore`, migration ledgers, PostgreSQL snapshots | untouched |
| Database / Redis / network to infrastructure | **not contacted** |
| Packages installed | none |
| Commit / push / PR edit / PR close / CI trigger | **none** |

Commands, all read-only: `git rev-parse`, `git ls-files --stage`, `git ls-files --unmerged`,
`git diff [--cached] [--numstat|--name-only]`, `git status --porcelain`, `git hash-object`,
`git cat-file -e`, `git show`, `gh pr list --json`; plus `git read-tree` + `git apply --cached
--check` under `GIT_INDEX_FILE` pointing at a disposable private index, deleted afterwards.

Artifacts written (staging only): `p11b17_rebaseline.{py,txt}`, `p11b17_recon.{py,txt}`,
`p11b17_verify.{py,txt}`, and this report.

---

## 9. Final status

| | |
|---|---|
| **D5′ execution** | **BLOCKED pending approvals** — A, C, D1–D4, E outstanding; B resolved within its stated scope |
| **QUOTA-01** | **OPEN** — production-path metered tests exist and verify as safe, but have never executed against real Redis; `store_keys` at `HEAD` **and** at the staged index wraps `vault.store_exchange_keys` with no guard, so the read-then-write race is live |
| **Exchange-slot concurrency and release safety** | **UNPROVEN** — guard symbols exist only in uncommitted layers; all 22 tests fail on `AttributeError` before issuing a Redis command |
| **TENANT-01** | **OPEN** — 22 staged paths untouched |
| **Live trading** | **OFF / NOT CERTIFIED** |

No new evidence in this phase supports changing any of these statuses.
