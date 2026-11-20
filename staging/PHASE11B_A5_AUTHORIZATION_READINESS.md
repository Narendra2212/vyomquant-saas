# Phase 11B-A5 — D5′ Commit Isolation & Authorization Readiness

**Decision: READY FOR EXPLICIT AUTHORIZATION.**
Verification and planning only. No branch, commit, push, PR, or workflow run was created. The
shared index and working tree are untouched. QUOTA-01 remains OPEN.

---

## Phase 0 — Baseline (re-established, unchanged from 11B-A4)

| Item | Value |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Branch | `main` |
| origin/main | `074b772a` (identical to HEAD) |
| Last commit | `074b772a` "Give the deploy gate permission to read the answer it refuses…" |
| Shared staged index | 22 files, all a tenant-isolation/RLS/terraform/ADR workstream (NOT D5′) |
| D5′ workflow | `.github/workflows/01-pr-check.yml` modified (`M`) |
| D5′ test files | 4 untracked (`??`) |
| conftest.py / pytest.ini | modified (`M`), excluded from D5′ |

## Phase 1 — Commit contents validated

**Four D5′ test files (untracked, whole-file includes):**

| File | Lines |
|---|---|
| `tests/real_redis_gate.py` | 149 |
| `tests/test_quota01_metered_reservation_is_hard.py` | 209 |
| `tests/test_quota01_exchange_slot_is_atomic.py` | 684 |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | 300 |

The three gated test files import exactly one shared symbol source — `tests.real_redis_gate`
(included). No other cross-`tests` import exists.

**Workflow (`01-pr-check.yml`) — partial, D5′ hunk only.** Full working-tree diff = 170 added /
0 removed, which `git diff -U3` renders as two hunks:
- Hunk A (`@@ -183,10 +183,37 @@`): the D5′ comment block + `AERORA_TEST_REDIS_URL: redis://localhost:6379/15`. **INCLUDE.**
- Hunk B (`@@ -465,6 +492,149 @@`): the `provisioning-and-isolation` PostgreSQL/privilege/tenant job. **EXCLUDE** (separate D-series workstream).

**Exclusions confirmed by new evidence this phase:**
- `tests/conftest.py` diff contains **zero** `REDIS`/`redis` references (grep empty). It is the
  rate-limiter leak guard. D5′ needs no conftest change (committed HEAD already blanks
  `REDIS_URL` and never touches `AERORA_TEST_REDIS_URL`).
- `pytest.ini` diff contains **zero** `REDIS`/`asyncio_mode` references (grep empty). It adds a
  `realaudit` PostgreSQL marker. Not D5′.

**CI trigger / SHA targeting.** `01-pr-check.yml` triggers on `pull_request` to `[main, master]`
(types `opened, synchronize, reopened`). **There is no `push` or `workflow_dispatch` trigger.**
Consequence: pushing the D5′ branch alone does **not** run CI — a **PR must be opened** against
`main`/`master`. The run then tests the PR's commit (the branch head, via GitHub's PR merge ref),
so the intended D5′ SHA is what gets exercised.

## Phase 2 — Index isolation proven via a private temporary index

Method: `GIT_INDEX_FILE` pointed at a temp index seeded with `git read-tree HEAD`; the four
files added by name; the D5′ workflow hunk applied with `git apply --cached --recount` (the
`--recount` flag absorbs the CRLF/autocrlf line-count offset — `core.autocrlf=true` here). The
shared index was never named in any command.

**Resulting proposed tree vs HEAD (`git diff --cached HEAD --name-status`):**
```
M  .github/workflows/01-pr-check.yml      (27 added, 0 removed)
A  tests/real_redis_gate.py
A  tests/test_quota01_exchange_slot_is_atomic.py
A  tests/test_quota01_metered_reservation_is_hard.py
A  tests/test_webhook_duplicate_delivery_real_redis.py
```

Checks:
1. **Exactly** the approved D5′ files + workflow change. ✔
2. `provisioning-and-isolation` is **absent** from the proposed workflow diff (added lines
   contain `AERORA_TEST_REDIS_URL`, not `provisioning-and-isolation`); workflow numstat is
   27/0, not 170/0 — the 143-line job is excluded. ✔
3. **No** unrelated staged file (the 22) and **no** other untracked file entered the tree. ✔
4. Parent = HEAD `074b772a` (temp index seeded from HEAD; diff computed against HEAD). ✔
5. Patch applied cleanly with `--recount`; whitespace is not newly introduced (additions are
   comment lines + one env line matching the file's existing CRLF style). ✔

**Shared index after the dry run: still 22 files, HEAD still `074b772a`, D5′ files still
`M`/`??`.** All temp indexes and patch files were deleted.

> Patch-integrity note: a naive `git apply --check` on a PowerShell-exported ASCII patch FAILED
> because the file contains UTF-8 box-drawing bytes and uses CRLF; the ASCII round-trip mangled
> them. The working method avoids any lossy re-encoding (UTF-8-no-BOM write + `--recount`). This
> is a tooling caveat for whoever stages for real: prefer `git add -p` (answer the single D5′
> hunk `y`, the provisioning hunk `n`) over a hand-built patch file. The private-index dry run
> confirms the *content* is isolable; the staging mechanism at authorization time should be
> `git add -p`, not this patch file.

## Phase 3 — Current test evidence (no integration claim)

`AERORA_TEST_REDIS_URL` unset; port 6379 unreachable. Command:
`pytest tests/test_quota01_metered_reservation_is_hard.py tests/test_quota01_exchange_slot_is_atomic.py tests/test_webhook_duplicate_delivery_real_redis.py`
→ **26 passed, 9 skipped** (exit 0).

- **9 skipped (real-Redis, legitimate opt-out — NOT passes):** the 3 required quota tests
  (`test_concurrent_reservations_are_atomic_against_real_redis`,
  `test_the_slot_lock_is_mutually_exclusive_against_real_redis`,
  `test_the_guard_admits_exactly_the_limit_against_real_redis`) + all 6 webhook tests.
- **26 passed (in-memory logic / mock):** 6 metered-reservation + 20 slot-lock logic tests.

**No integration success is claimed.** It cannot be, without a reachable real Redis and actual
execution of the three quota tests + six webhook tests. Local remains BLOCKED_ON_INFRA.

## Phase 4 — Decision

**Ownership boundaries:** D5′ = 4 new test files + 2 workflow hunk (27 lines). Everything else
in the working tree and the 22-file shared index belongs to other workstreams and stays out.

**Proposed commit manifest (unchanged, re-proven):**
- add by name: the 4 `tests/…` files
- `git add -p .github/workflows/01-pr-check.yml` → include the D5′ hunk, exclude the
  provisioning hunk
- parent: `074b772a`

**Proof unrelated staged changes are untouched:** shared index remained 22 files before and
after a private-index dry run that never named it; HEAD unchanged.

**Remaining uncertainties:**
1. The pre-existing 22-file shared staged index. A real commit on `main` without first moving to
   a new branch would sweep it in. Mitigation: create the D5′ branch and stage ONLY the D5′ paths
   by name / `-p` (the staged index does not auto-propagate to a `git commit <paths>` that lists
   only D5′ paths, but `git commit` with no pathspec WOULD include the 22). **Must stage D5′
   explicitly and commit with an explicit pathspec, or unstage the 22 first on a separate branch.**
2. The `test_tenant_isolation_matrix` failure seen in the last real CI run (11B-A3) is unrelated
   and may still red the job; it must be dispositioned separately, not masked by a D5′ PASS.
3. CI integration result itself is still UNVERIFIED until an authorized PR run executes.

**Authorizations required (none taken):**
| Action | Needs explicit authorization |
|---|---|
| Create branch `ci/d5-real-redis-quota-evidence` off `074b772a` | YES |
| Stage the 4 files + the single workflow hunk (`git add -p`) | YES |
| Commit (explicit D5′ pathspec, excluding the 22 staged files) | YES |
| `git push -u origin <branch>` | YES |
| `gh pr create` targeting `main` (required — no push/dispatch trigger) | YES |
| (optional) any `workflow_dispatch` | N/A — not configured |

## Completion

**READY FOR EXPLICIT AUTHORIZATION.** The D5′ change set is isolable and was proven isolated in a
private index without touching shared state. Local tests behave correctly (logic green,
real-Redis opt-out skipped). CI integration remains UNVERIFIED pending an authorized PR run.
QUOTA-01 stays OPEN pending independent dispositions of D9/D3/D2, the remaining quota-enforcement
gaps (order-rate, daily-trade, active-order), and the slot-lock release non-atomicity residual.
Launch-gate status and live-trading authorization unchanged.
