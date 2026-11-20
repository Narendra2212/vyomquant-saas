# Phase 11B-A3 — Webhook Gate Correction and D5′ CI Preparation

## Status

| Item | Result |
|---|---|
| Stage 1 — webhook gate correction | **DONE**, verified on all three gate states |
| Stage 2 — D5′ commit manifest | **VERIFIED**, isolates cleanly |
| Stage 3 — remote actions | **NONE TAKEN.** Awaiting explicit authorization |
| QUOTA-01 | **OPEN** — untouched |
| Live trading | **OFF** |

**No remote state changed.** No branch created, no commit, no push, no PR, no workflow
triggered or rerun. No scorecard, gate register or production configuration modified.

---

## 1. Baseline — re-established, not assumed

```
branch                       : main
HEAD                         : 074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main                  : 074b772ab16cece62cbfca0f2b78497be2ae56d5
ahead / behind               : 0 / 0
tracked modified             : 120
files staged in .git/index   : 22      <-- see §2, this is the important one
```

All facts from the A2 report re-confirmed: the D5′ workflow change is modified-but-uncommitted,
the helper and test files are untracked, and no CI run has tested D5′.

---

## 2. A hazard found in the prescribed Stage 2 procedure

The brief directs: stage the D5′ paths, then `git diff --cached` and `git diff --cached --check`.

**That would have been unsafe here.** `.git/index` already holds **22 staged files** from
concurrent workstreams:

```
.github/workflows/05-security.yml            docs/adr/ADR-0006-bridge-tenancy-...md
.kiro/security-template.md                   docs/adr/ADR-0007-per-tenant-...md
algo22-terminal/src/pages/ExchangeManager.jsx docs/saas-architecture/tenant-isolation-matrix.md
backend_app/core/dependencies.py             docs/security-reviews/ALGO22_..._10032026.md
backend_app/core/tenant_db_context.py        scripts/verify_rls.py
backend_app/routers/auth.py                  scripts/verify_rls_enforcement.py
backend_app/routers/exchange.py              terraform/alb.tf, cloudtrail.tf  ... (22 total)
```

`git add` into that index would make `git diff --cached` report the D5′ files **plus those
22**, and any subsequent `git commit` would capture another workstream's staged work. The
instruction "confirm that all unrelated staged and unstaged changes remain untouched" cannot
be satisfied by staging into a shared dirty index.

**Resolution.** The prospective commit was built in a **private index** via
`GIT_INDEX_FILE` pointed at a scratch file under `TEMP`. `.git/index` was never written —
asserted by hashing it before and after:

```
shared .git/index sha256 BEFORE : 6d965c8eafdd23ee...
shared .git/index sha256 AFTER  : 6d965c8eafdd23ee...
unchanged                        : True
files staged by others           : 22 (was 22)
```

The same mechanism must be used for the eventual commit (§5), because `git commit --only
<paths>` cannot express a *partial* file and the workflow file needs exactly that.

---

## 3. Stage 1 — the webhook gate correction

### What was wrong

`tests/test_webhook_duplicate_delivery_real_redis.py` owned its own predicate:

```python
def _redis_reachable() -> bool:          # '' -> False, failed PING -> False
    ...
pytestmark = [
    pytest.mark.skipif(not _redis_reachable(),
        reason="AERORA_TEST_REDIS_URL is unset or unreachable. ..."),
    pytest.mark.asyncio,
]
```

It read the right **variable**, so this was never a production-safety problem. The defect was
that one predicate answered two different questions and gave both the same outcome: "nobody
asked for a real Redis" and "the Redis you configured is dead" both produced a **SKIP**. The
two quota files use `tests/real_redis_gate.py` and therefore **FAIL** on the second case — so
a dead CI Redis would have failed those three while showing these six as green skips, which
is precisely the acceptance rule that a required Redis assertion may not be satisfied by a
skip.

### What changed — one file, four edits

| Edit | Change |
|---|---|
| 1 | Module docstring: the stale "Gated on a reachable Redis at REDIS_URL ... Skips otherwise" line now states the three-state contract |
| 2 | Deleted `_redis_reachable()`; imported `real_redis_required`, `require_real_redis`, `redis_url` from the shared gate |
| 3 | `REDIS_URL` is now `_gate_redis_url()` — kept as a module constant because the fixtures hand it to the backend cache manager, but resolved in **one** place instead of read from the environment a second time |
| 4 | `pytestmark = [real_redis_required, pytest.mark.asyncio]`, plus a new autouse fixture `_real_redis_is_usable()` calling `require_real_redis()` |

**Not touched:** webhook business logic, signature validation, idempotency behaviour, lock
semantics, production Redis configuration. No assertion weakened; no failure converted to a
skip. The helper's contract was **not** broadened — its existing public interface
(`real_redis_required`, `require_real_redis`, `redis_url`) already expressed everything needed.

### Why an autouse fixture rather than a call in each of the six bodies

The quota files call `require_real_redis()` as the first line of each test body. Copying that
here would have left a hole, and the hole is the exact defect being fixed:

`real_redis_cache` is a fixture, so it runs **before** any test body. It asks the backend
manager for a cache and, if it gets the in-memory mock back, calls `pytest.skip(...)`. A dead
Redis makes the manager fall back to that mock — so a body-level check would never be reached
and the dead endpoint would still have produced a **SKIP**.

The autouse fixture is ordered first, so the gate's explicit refusal pre-empts every other
fixture. It also covers tests added to this file later, which six hand-written calls do not.

**One consequence, stated plainly:** a failure raised in a fixture is reported by pytest as an
**ERROR** (setup failure), not as `FAILED` like the quota files. It is non-pass, non-skip, and
turns the exit code non-zero, which satisfies "fail, never skip" — but a reader comparing the
two file groups in a CI log will see `ERROR` here and `FAILED` there.

### Focused test evidence — all three gate states

| State | Command | Result |
|---|---|---|
| **Opt-in unset** (the default) | `pytest tests/test_webhook_duplicate_delivery_real_redis.py -rs` | **6 skipped**, exit 0 |
| **Configured, unreachable** | same, with `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15` | **6 errors**, exit 1 — **no skips** |
| **Configured, non-loopback** | same, with `AERORA_TEST_REDIS_URL=redis://redis.example.internal:6379/15` | **errors**, exit 1 |

The unset case now reports the **shared gate's** reason, which is how adoption is visible:

> `AERORA_TEST_REDIS_URL is not set. Real-Redis coverage is opt-in: ... A skip here is NOT a pass.`

The non-loopback case preserves the remote restriction and does not echo the host:

> `AERORA_TEST_REDIS_URL names a non-loopback host, and AERORA_TEST_REDIS_ALLOW_REMOTE=1 was
> not set. Refusing to connect ... (The host is deliberately not echoed.)`

All three gated files together, default path:

```
pytest tests/test_webhook_duplicate_delivery_real_redis.py \
       tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py -p no:randomly --timeout=240
=> 26 passed, 9 skipped, 0 failed   (exit 0)
```

The 9 skips are the 6 webhook + 3 quota real-Redis tests. The 26 passes are logic tests on the
in-process substrate.

**The reachable path was NOT exercised.** No Redis server is available locally
(`staging/redis/redis-server.exe` absent, nothing listening on 6379). That is the gap only CI
can close, and this report does not claim otherwise.

---

## 4. Stage 2 — the proposed commit manifest

### Path allowlist — 5 files

| # | Path | Inclusion | Size |
|---|---|---|---|
| 1 | `.github/workflows/01-pr-check.yml` | **hunk 1 only** | +27 / −0 |
| 2 | `tests/real_redis_gate.py` | whole (new) | +187 |
| 3 | `tests/test_quota01_exchange_slot_is_atomic.py` | whole (new) | +867 |
| 4 | `tests/test_quota01_metered_reservation_is_hard.py` | whole (new) | +264 |
| 5 | `tests/test_webhook_duplicate_delivery_real_redis.py` | whole (new, incl. §3) | +383 |

**Total: 5 files, 1,728 insertions, 0 deletions.**

**No additional file is required for the webhook correction.** It uses only the shared gate's
existing public interface, so allowlist item 6 in the brief is empty.

### Hunk allowlist for the workflow file

The file has exactly **two** hunks, classified by content rather than by position:

| Hunk | Header | Lines | Contains `AERORA_TEST_REDIS_URL` | Contains `provisioning-and-isolation` | Disposition |
|---|---|---|---|---|---|
| 1 | `@@ -183,10 +183,37 @@` | +27 / −0 | **yes** | no | **INCLUDE — D5′** |
| 2 | `@@ -465,6 +492,149 @@` | +143 / −0 | no | **yes** | **EXCLUDE — other workstream** |

27 + 143 = 170 = the file's total added lines. **Zero deletions anywhere**, so excluding hunk 2
cannot remove a line the other workstream added — its job is preserved byte-for-byte by simply
not being staged.

### Dry-run verification (`staging/verify_p11b3_commit_manifest.py`, exit 0)

```
PROSPECTIVE COMMIT CONTENTS (5 files):
   .github/workflows/01-pr-check.yml
   tests/real_redis_gate.py
   tests/test_quota01_exchange_slot_is_atomic.py
   tests/test_quota01_metered_reservation_is_hard.py
   tests/test_webhook_duplicate_delivery_real_redis.py
   5 files changed, 1728 insertions(+)

whitespace/conflict check (git diff --cached --check): CLEAN

OTHER WORKSTREAM PRESERVED?
   staged workflow diff contains AERORA_TEST_REDIS_URL      : True
   staged workflow diff contains provisioning-and-isolation : False
   files outside the allowlist in the commit                : none

shared .git/index unchanged : True        files staged by others : 22 (was 22)

MANIFEST VERIFIED ... NOTHING WAS COMMITTED.
```

The script **stops rather than staging** if either hunk fails its content check or if hunk 1
does not apply — so a future mixed hunk cannot be committed by rerunning it blindly.

### Explicitly excluded

`tests/conftest.py`, `tests/test_api04_store_failure_after_startup.py`, all application code,
launch scorecards, the shared gate register, and all `staging/` reports — none appears in the
prospective commit (`files outside the allowlist: none`).

**Also excluded, and worth a reviewer's attention:** two further untracked quota test files
exist in the tree — `tests/test_quota01_enforcement_is_wired.py` and
`tests/test_quota01_hard_enforcer_is_deprecated_in_code.py` — plus
`staging/PHASE10_WSK_QUOTA_REAL_REDIS_REVIEW.md` and
`staging/PHASE11B_A_REAL_REDIS_QUOTA_REPORT.md`. They are outside the brief's allowlist and
neither test imports the shared gate, so excluding them is correct for D5′. The consequence is
that they will **not** run in CI either, which bounds any "quota regression suite is green"
claim made from the resulting run.

---

## 5. Exact commands for after authorization — NOT EXECUTED

The private index is load-bearing here; `git add` would capture the 22 files described in §2.

```bash
# 0. scratch index and the D5' workflow hunk, regenerated by the verifier
export GIT_INDEX_FILE="$(mktemp -t d5_index.XXXXXX)"
git read-tree HEAD

# 1. the four new files, whole
git add -- tests/real_redis_gate.py \
           tests/test_quota01_exchange_slot_is_atomic.py \
           tests/test_quota01_metered_reservation_is_hard.py \
           tests/test_webhook_duplicate_delivery_real_redis.py

# 2. the workflow, HUNK 1 ONLY (patch written by
#    staging/verify_p11b3_commit_manifest.py; or `git add -p` and accept the first hunk)
git apply --cached d5_workflow_hunk1.patch

# 3. re-verify before writing anything
git diff --cached --stat        # expect exactly the 5 files, 1728 insertions
git diff --cached --check       # expect clean
git diff --cached -- .github/workflows/01-pr-check.yml | grep -c provisioning-and-isolation
                                # expect 0

# 4. branch + commit from the private index, leaving main and .git/index alone
git checkout -b ci/d5-prime-real-redis
git commit -m "D5': point the real-Redis tests at the CI redis service"
unset GIT_INDEX_FILE

# 5. push and open the PR that triggers 01-pr-check.yml
git push -u origin ci/d5-prime-real-redis
gh pr create --fill --base main
```

**Authorization required for steps 4 and 5.** Steps 0–3 are the dry run already performed.

---

## 6. Remaining blockers

1. **No CI run exists for D5′, and creating one is not authorized.** Unchanged from A2;
   the single blocker for VERIFIED.
2. **The reachable-Redis path is untested anywhere.** No local Redis server. The gate's
   "run" branch has never executed — in CI or locally. Only "skip" and "fail" are proven.
3. **`tests/real_redis_gate.py` has no test of its own.** Its three-state logic is exercised
   only indirectly, through the three consumer files. A direct test was deliberately **not**
   written: it is not required for the webhook correction and would exceed the approved scope.
4. **A second skip path survives in the webhook file.** `real_redis_cache` calls
   `pytest.skip(...)` if the backend manager hands back `MockRedisClient`. That guard is
   correct and deliberate — a green result from the in-memory dict is the false assurance this
   file exists to prevent — and it is **not silent** (it names the resolved type). But it is a
   skip, so the CI run must be read with it in mind: if the manager cannot use the service the
   gate just PINGed successfully, these six will skip rather than fail. Left as-is; changing
   it is outside this correction.
5. **Fixture-raised refusals report as `ERROR`, not `FAILED`** (§3). Cosmetic but worth
   knowing when reading the CI log.
6. **The workflow file cannot be committed whole.** It carries another workstream's job; the
   partial-staging procedure in §5 is mandatory, not optional.
7. **Pre-existing CI failures.** All twelve most recent `01-pr-check.yml` runs concluded
   `failure` on other branches. The eventual run may be red for unrelated reasons, so
   acceptance must be read per-test.

---

## 7. Concurrent-work and safety confirmation

| Check | Baseline | Final |
|---|---|---|
| HEAD | `074b772a` | `074b772a` |
| `origin/main` | `074b772a` | `074b772a` |
| ahead / behind | 0 / 0 | **0 / 0** |
| tracked modified | 120 | **120** |
| files staged in `.git/index` | 22 | **22** |
| `.git/index` sha256 | `6d965c8eafdd23ee…` | **identical** |
| reflog head | `074b772a commit: Give the deploy gate…` | **unchanged** |
| local branches | 12 | **12** — none created |

- Commits: **none**. Pushes / PRs / workflow triggers: **none**.
- reset / clean / stash / revert: **none**.
- Files modified by this workstream: **one** — `tests/test_webhook_duplicate_delivery_real_redis.py`
  (still untracked; working tree only). Everything else created is new and under `staging/`.
- `.github/workflows/01-pr-check.yml` was **read, never written** — its 170-line diff,
  including the other workstream's 143 lines, is byte-for-byte as found.
- Production Redis / database / exchange endpoints: **not contacted**. The unreachable-endpoint
  test dialled `127.0.0.1:6399`, where nothing listens, by design.
- Infrastructure installed, quota limits changed: **none**.
- QUOTA-01: **OPEN**. Launch scorecard and shared gate register: **untouched**.
- Live trading: **OFF** — no `LIVE*` / `TRADING*` / `PRODUCTION_ROUTER*` variable set.

Files created, all new and all under `staging/`:
`PHASE11B_A3_CI_PREPARATION_REPORT.md`, `verify_p11b3_commit_manifest.py`,
`p11b3_webhook_unset.txt`, `p11b3_webhook_dead.txt`, `p11b3_focused_all.txt`,
`p11b3_manifest.txt`.

---

## 8. Scope boundaries honoured

Not implemented, per the brief: **D9** atomic lock release, **D2** database backstop, **D3**
production population analysis. **QUOTA-01 stays OPEN** until its broader outstanding
requirements are independently resolved. Nothing here authorizes paper or live trading.

Stage 4 (CI acceptance classification) is **not reached** — it requires a run that does not
exist. Per the brief, neither the CI configuration nor the local in-memory results in §3 are
real-Redis verification, and none is presented as such.

---

## 9. Request for authorization

Stage 1 and Stage 2 are complete and verified. The next action needs explicit approval:

> **Create branch `ci/d5-prime-real-redis`, commit the 5-file manifest in §4 using the private
> index procedure in §5, push it, and open a PR against `main` so `01-pr-check.yml` runs.**

On approval I will execute §5 steps 4–5, then inspect the run and report run URL/ID, commit
SHA, job name, Redis service health, the three quota tests' per-test outcomes, all six webhook
tests accounted for, quota regression results, every unrelated failure, and a
**PASS / FAIL / UNVERIFIED** classification — with the overall job outcome reported separately
from D5′'s per-test evidence.
