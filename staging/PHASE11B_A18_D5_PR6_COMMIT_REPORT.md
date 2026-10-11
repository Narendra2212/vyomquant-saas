# Phase 11B-A18 — D5′ Canonical PR #6 Commit

**Output path:** `staging/PHASE11B_A18_D5_PR6_COMMIT_REPORT.md` — confirmed absent before
writing.

**Outcome: D1 executed. Commit `72bae29b` created on `ci/d5-real-redis-quota-evidence`
with exactly the three authorized paths, after full exact-manifest verification.**

**One material event occurred during execution and must be read before anything else: a
concurrent session pushed a byte-identical commit to the same branch.** The remote head
moved from `d07014d3` to `f5b99e57` after my preconditions passed. My commit and theirs
have the **identical tree** `844bf038a3a6fbd48af47638b46f1d4ff257d1fe` and the identical
parent. The local branch now diverges from `origin` by one redundant commit that I am not
authorized to remove. Detail in §6.

| | |
|---|---|
| QUOTA-01 | **OPEN** |
| TENANT-01 | **OPEN** |
| Exchange-slot safety | **UNPROVEN** |
| D5′ acceptance | **NOT CLAIMED** |
| Live trading | **OFF / NOT CERTIFIED** |

**Not performed:** no push (D2), no CI trigger/rerun/cancel (D3), no exchange-slot test
removal (D4), no PR closure or edit on #7/#8 (E), no amend/rebase/reset/force-push, no
`git add -A` or `git add .`, no database access, no change to any other workstream's file.

---

## 1. Repository invariants — entry and exit

| Invariant | Entry | Exit | Moved? |
|---|---|---|---|
| Shared `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| Shared branch | `main` | `main` | **no** |
| Upstream `HEAD...origin/main` | `0 / 0` | `0 / 0` | no |
| **Canonical staged digest** | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **no — TENANT-01 work preserved** |
| Staged paths | **22** | **22** | **no** |
| Unstaged paths | 100 | 100 | no |
| Unmerged entries | 0 | 0 | no |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | — | recorded separately from the digest, not interchangeable |
| Worktrees | 1 | **1** | temporary worktree created and removed |
| PR #6 branch (local) | `d07014d3` | **`72bae29b`** | **advanced by this commit (local only)** |
| PR #6 branch (remote) | `d07014d3` | **`f5b99e57`** | **moved by a concurrent push — §6** |
| PR #7 / PR #8 | `49e70da2` / `a624ce89` | `49e70da2` / `a624ce89` | **no — untouched** |

Canonical digest method (A16R): sha256 over `git diff --cached --raw`, CRLF→LF, trailing
whitespace stripped per line, blank lines dropped, LF-joined.

`git write-tree` was captured with `GIT_OBJECT_DIRECTORY` redirected to a temporary
directory and `GIT_ALTERNATE_OBJECT_DIRECTORIES` pointing at the real store: **0 objects
were written to the temp dir**, so the tree already existed and nothing entered
`.git/objects` during validation.

---

## 2. Independently recomputed hashes — all three files

PR #6 head at verification: `d07014d36d32c63db44f8fb9659933c0e84e82f2`, state **OPEN**,
`isDraft: false`, base `main`, local ref == remote ref. Re-fetched read-only via
`gh pr view 6`.

| Path | Pre-image (PR #6) | Post-image | Expected | Match |
|---|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | `57e2cee8ea448266efafa1441c2443a3c668e06e` | **`967b4a28e29157f97f75d31a48eb9ce5b4133ce5`** | `967b4a28…` | **yes** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `105b6567` | **`3ffbccde68c3b57485a9971d4977c34aff598b47`** | `3ffbccde…` | **yes** |
| `tests/test_quota01_metered_reservation_is_hard.py` | `f48103ac` | **`7baff0b64f0c7441f4192a9a6b2df53e7a68dce9`** | `7baff0b6…` | **yes** |

The stale `a2f39ebb…` fingerprint is **retired** — see
`staging/PHASE11B_A16R_D5_EVIDENCE_RECONCILIATION.md` §2.1. It reproduces from no
documented input. `967b4a28` is the reproducible value and is corroborated by
`staging/p11b15_wf_only.patch`'s post-image index line.

### 2.1 Workflow candidate — rebuilt from the current PR #6 blob, not the worktree

Base: `d07014d3:.github/workflows/01-pr-check.yml` = `57e2cee8…`, 812 lines, LF.
Transformation: literal replacement of
`AERORA_TEST_REDIS_URL: redis://localhost:6379/15` → `…/0`.

> **Method note.** A16R documented the transformation as a regex
> (`r"(^\s*AERORA_TEST_REDIS_URL:…)/15\s*$"` → `r"\1/0"`). A first attempt here used it and
> the shell mangled the `$`, so the pattern never matched and the workflow silently failed
> to stage — caught because the staged set showed 2 paths instead of 3, not 3. The literal
> replacement was substituted and **produces the identical blob `967b4a28`**, verified by
> `git hash-object` before staging. The regex and the literal form are equivalent on this
> input because no edited line carries trailing whitespace.

| Requirement | Verified |
|---|---|
| Lines changed | **2** |
| `AERORA_TEST_REDIS_URL … /15` remaining | **0** |
| `AERORA_TEST_REDIS_URL … /0` present | **2** — `unit-tests` job and `d5-redis-diagnostics` job |
| `d5-redis-diagnostics` job retained | **yes** |
| `provisioning-and-isolation` not added | **absent** (it is absent from PR #6 and stayed absent) |
| Unrelated historical comments preserved | **yes** — the `/15` narrative remains in the comment block |

### 2.2 Metered quota test — all seven originals plus exactly two

Extracted by AST-equivalent regex over both the PR #6 blob and the candidate. PR #6: **7**
tests. Candidate: **9**. Originals missing: **none**.

Preserved: `test_concurrent_reservations_admit_exactly_the_limit_and_no_more`,
`test_a_sequential_run_to_the_limit_then_one_more_refuses`,
`test_a_zero_allowance_reserves_nothing_and_accumulates_nothing`,
`test_an_unlimited_plan_always_admits`, `test_reservations_are_isolated_per_user`,
`test_the_full_enforce_quota_path_refuses_at_the_limit`,
`test_concurrent_reservations_are_atomic_against_real_redis`.

Added (**2**): `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis`,
`test_production_zero_allowance_writes_no_meter_against_real_redis`.

> An earlier extraction pattern returned 0 tests on both sides — a vacuous `0 == 0` pass.
> It was rejected and replaced with a pattern that allows leading whitespace and `async`,
> which produced the 7/9 result above.

---

## 3. Exchange-slot deferral boundary — verified, nothing staged

Inspected read-only. **None of these was staged, committed, removed, rewritten, or
cherry-picked.**

| Path | Status in shared checkout | In the commit? |
|---|---|---|
| `backend_app/core/subscription_dependencies.py` | ` M` (+253 / −0) | **no** |
| `backend_app/routers/exchange.py` | **`MM`** (staged +268 / −15, unstaged +154 / −14) | **no** |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` in the shared worktree | **no — and retained** |

The slot test is **already committed on PR #6** as blob
`2d7ccbbb04b3af04675b365809d89b4429db4888`, byte-identical to the worktree copy, and it is
**still present on commit `72bae29b`** (verified with `git cat-file -e`). It was not
touched. D4 (removal) remains unauthorized and was not exercised.

### 3.1 Restoration dependency — recorded

The exchange-slot acceptance cannot be restored to green by any test-side change. The
implementation owner must land, **in one coordinated change of their own**:

1. the slot guard, **and**
2. its `store_keys` integration in `backend_app/routers/exchange.py`,

because that file carries two concurrent layers (staged +268/−15 from the TENANT-01
workstream and unstaged +154/−14), so only that owner can land it without absorbing
someone else's work. **Then** the real-Redis slot-lock acceptance tests must execute
against the landed implementation. Until that sequence completes,
`tests/test_quota01_exchange_slot_is_atomic.py` will keep failing on PR #6 and
**exchange-slot concurrency and release safety remain UNPROVEN**. This dependency is also
recorded in the commit message body.

---

## 4. The commit

| Property | Value |
|---|---|
| **Commit SHA** | **`72bae29b7bab8373dc0b9fe2de80f0e6c845e2e9`** |
| **Parent SHA** | **`d07014d36d32c63db44f8fb9659933c0e84e82f2`** — exactly the approved PR #6 head |
| **Tree** | `844bf038a3a6fbd48af47638b46f1d4ff257d1fe` |
| Branch | `ci/d5-real-redis-quota-evidence` — the approved canonical branch |
| Totals | **3 files changed, 545 insertions(+), 16 deletions(-)** |

Exact three-path diff, `d07014d3 → 72bae29b`:

```
:100644 100644 57e2cee8 967b4a28 M  .github/workflows/01-pr-check.yml
:100644 100644 f48103ac 7baff0b6 M  tests/test_quota01_metered_reservation_is_hard.py
:100644 100644 105b6567 3ffbccde M  tests/test_webhook_duplicate_delivery_real_redis.py

2    2    .github/workflows/01-pr-check.yml
266  0    tests/test_quota01_metered_reservation_is_hard.py
277  14   tests/test_webhook_duplicate_delivery_real_redis.py
```

**Path count: 3. No other path entered the commit.** Confirmed by
`git diff --name-only HEAD~1 HEAD | count` = 3 and by the raw listing above. The 545/16
totals reconcile exactly with the per-file numstat (2+266+277 = 545; 2+0+14 = 16).

Explicitly **not** in the commit: `.gitignore`, `backend_app/routers/exchange.py`,
`backend_app/core/subscription_dependencies.py`, `backend_app/backend/redis_manager.py`,
`backend_app/core/cache/redis_manager.py`, `backend_app/routers/billing.py`, and every
other workstream file.

### 4.1 Method — why the shared index was never involved

The shared checkout holds 22 unrelated staged paths and is on `main`, shared with several
live sessions. Switching branches there would have moved `HEAD` for all of them, and
staging there would have mixed into the TENANT-01 index. Neither was done.

Instead a **linked worktree** was created at
`%TEMP%\a18_pr6_wt` on `ci/d5-real-redis-quota-evidence` (that branch was checked out
nowhere, so this was permitted). A linked worktree carries its **own HEAD and own index**:

1. `git worktree add %TEMP%\a18_pr6_wt ci/d5-real-redis-quota-evidence` — target confirmed
   absent first.
2. Three files written there; the two test files copied byte-for-byte from the shared
   worktree, the workflow rebuilt from the PR #6 blob.
3. `git add -- <path>` **once per path**, three times. No `-A`, no `.`, no directory.
4. Staged set inspected — exactly 3 entries with the expected blob transitions.
5. `git commit -F <message file> -- <the three paths>` — explicit pathspec.
6. Commit SHA, parent, tree and three-path diff verified.
7. `git worktree remove` — the branch ref survives at `72bae29b`.

The commit message was supplied via a file because an inline multi-line message with nested
quotes hung the shell on a first attempt; **that attempt created no commit** (branch head
was re-verified as still `d07014d3` before retrying). No hooks exist in `.git/hooks`.

Temporary artifacts: the worktree and the message file, both outside the repository, both
removed and their absence verified. No stray file was left in `staging/` or `%TEMP%`.

---

## 5. Shared-state preservation confirmations

| Check | Result |
|---|---|
| TENANT-01 22-path staged work | **PRESERVED** — 22 paths, digest `2e994018…2767152`, unchanged entry to exit |
| Shared `HEAD` / branch | **unchanged** — `074b772a` / `main` |
| Shared index altered? | **no** — never named in any command |
| `git add -A` / `git add .` / directory staging | **never used** |
| Amend / rebase / reset / force-push | **never used** |
| **Push** | **NOT performed** (D2 unauthorized) |
| **CI** | **not triggered, rerun, or cancelled** (D3 unauthorized) |
| PRs #7 / #8 | **not closed, not edited**; refs unchanged (E unauthorized) |
| Exchange-slot tests | **not removed** (D4 unauthorized) |
| Database | **not accessed** |
| Objects written to `.git/objects` during validation | **0** |

---

## 6. The concurrent push — read this before acting on the commit

**During execution, a concurrent session pushed an equivalent commit to PR #6.**

| | |
|---|---|
| Remote head at precondition check | `d07014d36d32c63db44f8fb9659933c0e84e82f2` |
| Remote head at exit | **`f5b99e57e1f7644bdbb10712e21f85e2ff236920`** |
| Its subject | "D5′: align the real-Redis gate database and add production-path quota coverage" |
| Its parent | `d07014d3` — **the same parent as mine** |
| Its tree | **`844bf038a3a6fbd48af47638b46f1d4ff257d1fe`** |
| My tree | **`844bf038a3a6fbd48af47638b46f1d4ff257d1fe`** |
| `git diff 72bae29b f5b99e57` | **no differences** |
| Its changed paths vs `d07014d3` | the **same three**, with the **same blob transitions** |
| Divergence `local...origin` | **1 / 1** |

**I did not push.** The remote moved independently, after my preconditions had passed and
been verified twice (local ref == remote ref, plus `gh pr view 6` reporting `d07014d3`).

Two readings, both worth stating:

* **Corroboration.** Two workstreams independently produced the *byte-identical* tree from
  the same parent. That is strong evidence the manifest verification in §2 is correct and
  that `967b4a28` — not `a2f39ebb` — is the right workflow candidate.
* **A redundant local commit now exists.** `refs/heads/ci/d5-real-redis-quota-evidence`
  points at `72bae29b` while `origin/…` points at `f5b99e57`. Same content, different SHA
  (message, author and timestamp differ). The local branch is **1 ahead / 1 behind**.

**Recommended disposition — not performed, because it needs authorization this task does
not grant.** The authorized content is already on the remote, so the cleanest resolution is
to point the local branch at `origin` and discard the duplicate SHA:

```
git branch -f ci/d5-real-redis-quota-evidence origin/ci/d5-real-redis-quota-evidence
```

That is a local ref move only. It is **not** done here: §4/§5 forbid `reset`, and the
branch is the D5′ owner's to re-point. **Do not push `72bae29b`** — it would create a
second commit carrying content the branch already has.

---

## 7. Outstanding permissions and risks

| Item | Status |
|---|---|
| **D2 — push** | **UNAUTHORIZED.** Not performed. And now unnecessary: the identical content is already on `origin` via `f5b99e57`. |
| **D3 — CI trigger** | **UNAUTHORIZED.** Not performed. Note the concurrent push will have started its own `01 PR Check` run on `f5b99e57`; that run is not mine and its result is not evidence produced by this task. |
| **D4 — exchange-slot test removal** | **UNAUTHORIZED.** Not performed; the test is retained on `72bae29b`. |
| **E — duplicate PR closure** | **UNAUTHORIZED.** #7 (`49e70da2`) and #8 (`a624ce89`) untouched and still open; they remain byte-identical duplicates of each other. |
| **F1 — exchange-slot guard** | **BLOCKED.** Deferred by decision. Restoration dependency in §3.1. Concurrency and release safety **UNPROVEN**. |
| **F4 — `get_redis_url` discards an explicit DB, captured at import** | **UNOWNED, unfixed.** `backend_app/backend/redis_manager.py` is clean. The `/0` alignment in this commit is a compensation, not a fix. |
| **F5 — webhook idempotency cannot distinguish "new" from "cache unavailable"** | **UNOWNED. Billing-critical.** Not addressed by this commit. |
| **Local/remote divergence on PR #6** | **OPEN** — §6. Needs the D5′ owner to re-point the local branch. |
| **D5′ acceptance** | **NOT PASSED and NOT CLAIMED.** No test was executed by this task; no real Redis is configured locally. Real-Redis results and opt-out skips must continue to be reported separately. |

**Next permitted action:** have the D5′ owner re-point the local branch to `origin`
(§6), then read the CI result of the concurrent `f5b99e57` push — separating real-Redis
executions from opt-out skips, and D5′ failures from the pre-existing baseline, per
`staging/PHASE11B_A16R_D5_EVIDENCE_RECONCILIATION.md`.

---

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · exchange-slot safety **UNPROVEN** · D5′ acceptance
**NOT CLAIMED** · live trading **OFF / NOT CERTIFIED** · no push · no CI trigger · no PR
closure · no test removal · 22-path TENANT-01 staged work preserved
