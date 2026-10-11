# Phase 11B-A13 — Real-Redis Verification and Slot-Lock Integration Planning

**Date:** 2026-10-10
**Nature:** read-only analysis. No file outside `staging/` was written. No commit, push, PR change,
CI trigger, merge, production access, or infrastructure installation.

---

## HEADLINE

Two things changed the shape of this task, and both argue against proceeding to implementation.

**1. The Phase 3 evidence already exists on the remote, and I retrieved it read-only.** The A11
diagnostic job ran on PR #6 (run **38047483583**, job **114200293981**) and uploaded artifact
**11668905597** — JUnit XML plus full per-file logs with complete tracebacks, not expired. The gate
recorded **`state=run`**. Every one of the 35 tests across the three D5′ files executed, with
**zero skips**. No new remote action was needed to answer most of Phase 3.

**2. PR identity is genuinely ambiguous, so per Phase 0 step 5 I stopped before editing.** There are
**three** open D5′ pull requests, and **PR #7 and PR #8 have byte-identical trees** (`271f13c2…`) —
the same five files, the same 1,761 insertions, created eighteen minutes apart. PR #6 carries the
diagnostic machinery but the oldest test files. No single commit contains everything a conclusive
verification needs.

The decisive new result: running each file **alone** against real Redis reproduces **both** failure
classes exactly. That eliminates full-suite state leakage, which was the A11 job's stated
hypothesis. Both A9 diagnoses are confirmed at their cause.

| Question | Answer |
|---|---|
| Gate reached `state=run`, not skip | **YES** — evidenced |
| All nine required real-Redis tests executed | **YES** — 0 skips in all three files |
| Metered reservation file | **7/7 PASSED** |
| Webhook file | **4 passed / 2 failed** — keyspace split, reproduced in isolation |
| Exchange-slot file | **1 passed / 21 FAILED** — implementation absent from every commit |
| Exchange-slot mutual exclusion / exact limit verified | **NO** — never executed a Redis command |
| Metered test exercises the production quota API | **NO** — raw `INCR`/`DECR` only |
| Slot-lock production code safely isolable | **NO** for `routers/exchange.py` |
| Canonical D5′ PR | **UNDETERMINED** — requires your decision |
| **Real-Redis integration acceptance** | **FAILED** (not merely unverified) |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Phase 0 — baseline, re-measured

| Measurement | Value | Same at end? |
|---|---|---|
| Branch | `main` | yes |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | yes |
| Upstream | `origin/main` = `074b772ab16cece62cbfca0f2b78497be2ae56d5`, 0 ahead / 0 behind | yes |
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` | **identical** |
| Staged entries | 22 | **22** |
| `ci/d5-prime-real-redis` | `49e70da2…` | unmoved |

Remote operations performed, all read-only: `git fetch` (no local ref moved), `gh pr list`,
`gh pr view`, `gh api` GETs for runs/jobs/artifacts, and one artifact `zip` download.

### 1a. Two parallel A12 workstreams exist, and the tree moved under me

`staging/` contains **two** A12 reports from two different sessions:

| Report | Author | What it changed |
|---|---|---|
| `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` (27 131 B) | this session | `tests/test_webhook_duplicate_delivery_real_redis.py` — derive the client URL from `get_redis_url(REDIS_DB_CACHE)`, add `_prove_one_keyspace` |
| `PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md` (14 705 B) | **another session** | `.github/workflows/01-pr-check.yml` — `AERORA_TEST_REDIS_URL: …/15` → **`…/0`**, plus a comment rewrite |

They did not collide: that session read my webhook change, judged it the more robust of the two
plans, and deliberately left the file alone. My file's blob is still `3ffbccde…`, unchanged.

**But the workflow moved after my A12 measurements, and my own earlier figures for it are now
superseded.** Re-measured:

| | A12 (my measurement) | now |
|---|---|---|
| vs `origin/main` | 170 insertions, 0 deletions | **202 insertions, 0 deletions** |
| hunk 1 | `@@ -183,10 +183,37 @@` (+27) | `@@ -183,10 +183,69 @@` (**+59**) |
| hunk 2 | `@@ -465,6 +492,149 @@` (+143) | `@@ -465,6 +524,149 @@` (+143, unchanged) |
| line 248 | `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` | **`redis://localhost:6379/0`** |

Still **zero deletions**, so the hunk-isolation property that made the A6 commit safe continues to
hold. The other session described this as three hunks (`+186,58`, `+248,1`, `+527,143`); at git's
default context the first two merge into one. Same content, different context width.

**This changes the keyspace analysis, so it is worth being exact.** There are now *two
independent, uncommitted fixes for the same defect*, and they are complementary rather than
conflicting:

- **Mine (test-side):** the webhook client derives its URL from the application's own
  `get_redis_url(REDIS_DB_CACHE)`, so it agrees with the app **whatever** the gate variable says.
- **Theirs (workflow-side):** the gate variable now names database 0, so the database the gate
  proves reachable is the database the application actually uses — and, incidentally, the
  *pre-A12* webhook client would also have landed on database 0.

Either one alone would have resolved the two webhook failures. Together they are belt-and-braces,
and both should land. Neither has run against real Redis.

One follow-up the other session correctly flagged against **my** file, stated more precisely than
"stale": my `_app_cache_url()` docstring quotes `AERORA_TEST_REDIS_URL: redis://localhost:6379/15`
as the CI value. That remains **accurate for all three PR heads** (`49e70da2`, `a624ce89` and
`d07014d3` all carry `/15`) and is stale only with respect to the uncommitted worktree. The
docstring's conclusion is unaffected either way, because the client no longer reads that variable.
It needs a one-token refresh in whatever commit lands the workflow change. I did not edit it: this
phase carries no edit authorization.

---

## 2. PR identity — three candidates, two identical

```
PR #6  OPEN  ci/d5-real-redis-quota-evidence  -> d07014d36d  "ci(diag): capture D5' real-Redis
                                                              tracebacks per file with JUnit artifacts"
         commit d07014d3  parent b43e990d   .github/workflows/01-pr-check.yml  +151
         commit b43e990d  parent 074b772a   5 files, +1728
PR #7  OPEN  ci/d5-prime-real-redis          -> 49e70da23c  "D5': point the real-Redis tests at the
                                                              CI redis service"      5 files, +1761
PR #8  OPEN  ci/d5-real-redis-switch         -> a624ce89b1  "Let the CI Redis service reach the tests
                                                              that exist to use it"  5 files, +1761
```

**PR #7 and PR #8 are duplicates.** `git diff 49e70da2 a624ce89` → *no differences*; both trees are
`271f13c29d3c09a205dbaae959cad002cc0fd0a6`. Two agent sessions produced the same change set from the
same parent (`074b772a`) at 15:42 and 16:00. One of them should be closed, but **choosing which is
not mine to decide** — both are single-parent commits on `main` with identical content, so there is
no technical tie-breaker, only an ownership one.

**The "A11 diagnostic commit" is `d07014d3`**, the second commit of PR #6. It is workflow-only
(+151 lines, one file) and adds the non-blocking `d5-redis-diagnostics` job.

### Which version of each file is in which commit

```
tests/test_webhook_duplicate_delivery_real_redis.py
  main                  ABSENT
  PR6 base b43e990d       383 lines   A5 fail-not-skip=NO    A12 keyspace fix=NO
  PR6 head d07014d3       383 lines   A5 fail-not-skip=NO    A12 keyspace fix=NO
  PR7 head 49e70da2       416 lines   A5 fail-not-skip=yes   A12 keyspace fix=NO
  PR8 head a624ce89       416 lines   A5 fail-not-skip=yes   A12 keyspace fix=NO
  WORKTREE                646 lines   A5 fail-not-skip=yes   A12 keyspace fix=yes

tests/real_redis_gate.py                           187 lines, IDENTICAL in all four commits + worktree
tests/test_quota01_exchange_slot_is_atomic.py      867 lines, IDENTICAL in all four commits + worktree
tests/test_quota01_metered_reservation_is_hard.py  264 lines, IDENTICAL in all four commits + worktree

backend_app/core/subscription_dependencies.py
  main / PR6 / PR7 / PR8  929 lines   slot guard=NO    lock key=NO
  WORKTREE               1182 lines   slot guard=yes   lock key=yes

backend_app/routers/exchange.py
  main / PR6 / PR7 / PR8  825 lines   store_keys guard=NO
  WORKTREE               1218 lines   store_keys guard=yes

.github/workflows/01-pr-check.yml
  main                    634 lines   opt-in=NO    A11 diag job=NO    provisioning job=NO
  PR6 base b43e990d       661 lines   opt-in=yes   A11 diag job=NO    provisioning job=NO
  PR6 head d07014d3       812 lines   opt-in=yes   A11 diag job=yes   provisioning job=NO
  PR7 / PR8               661 lines   opt-in=yes   A11 diag job=NO    provisioning job=NO
  WORKTREE                836 lines   opt-in=yes   A11 diag job=NO    provisioning job=yes
                                      + the other A12 session's /15 -> /0 change at line 248
```

Gate-variable value, which is the thing the other A12 session moved:

| | `AERORA_TEST_REDIS_URL` |
|---|---|
| PR6 base / PR6 head / PR7 / PR8 | `redis://localhost:6379/15` |
| **worktree** | **`redis://localhost:6379/0`** |

So every committed candidate still carries `/15`, and the `/0` correction exists only in the
uncommitted tree — alongside, and independent of, my test-side fix.

**No single commit holds everything.** A conclusive verification needs the A12 webhook file
(worktree only), the A11 diagnostic job (PR #6 only), and the slot-guard production code (worktree
only). They are currently spread across one branch and an uncommitted working tree.

PR #6 carrying the **pre-A5** webhook file (383 lines) matters: on that branch a mock cache would
still `skip` rather than `fail`. It did not bite in this run — the gate reached a real cache — but
PR #6 is not the lane in which the A5 guarantee holds.

---

## 3. Phase 1 — real-Redis verification readiness

Assessed against the existing A11 diagnostic job (`d07014d3`), read in full:

| Requirement | Status | Evidence |
|---|---|---|
| Job uses a real Redis service | **YES** | `image: redis:7-alpine`, `6379:6379`, `--health-cmd "redis-cli ping"` — copied verbatim from `unit-tests` |
| `AERORA_TEST_REDIS_URL` enables the gate | **YES** | `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` at job level |
| Gate reports `state=run`, not `skip` | **YES** | `gate_state.txt`: `real-Redis gate: state=run (AERORA_TEST_REDIS_URL=set, remote_allowed=False)` / `state=run` / `reason=(none)` |
| Artifacts preserve complete results and tracebacks | **YES** | per-file `--junitxml` + `--tb=long -rf`, `upload-artifact` with `if: always()`; artifact present, 21,802 bytes, not expired |
| **Webhook client and application cache share a keyspace** | **NO** | the job sets `REDIS_URL: redis://localhost:6379` (no DB path) while the test client uses `.../15`; the app pins `REDIS_DB_CACHE = 0`. **Two** fixes now exist for this — my test-side derivation and the other A12 session's `/15 → /0` on the gate variable — and **neither is on any branch** (§1a) |
| Gate unchanged, assertions not weakened, no skip for unreachable Redis | **YES** | the diagnostic commit touches the workflow only |

So Phase 1 is satisfied on four of five counts by work that already exists. The fifth — keyspace
alignment — is precisely what A12 fixed and what no branch yet carries.

One caveat the brief anticipated, and it is real here: the diagnostic job reported
**`conclusion: success`** while 23 of its 35 tests failed, because it sets `continue-on-error: true`
at job level. Its own comment says so explicitly. **The green badge on job 114200293981 is not
evidence of anything.** The per-test JUnit data below is.

---

## 4. Phase 3 evidence — already generated, retrieved read-only

**Run 38047483583**, branch `ci/d5-real-redis-quota-evidence`, head SHA `d07014d36d…`, created
2026-10-10T11:10:41Z, `01 PR Check`, overall conclusion `failure`.
Artifact `d5-redis-diagnostics-d8eb6fc4512bd1f7e1d0b544374d7abb9dbf3937` (id 11668905597).

> The artifact name carries `d8eb6fc4…`, not the head SHA, because `github.sha` on a
> `pull_request` event is the merge commit. Worth knowing before anyone tries to match them.

Each file ran **alone**, so a failure in one could not truncate another's traceback.

### 4a. `tests/test_quota01_metered_reservation_is_hard.py` — 7 tests, 0 failures, 0 skipped (1.133 s)

| Test | Result |
|---|---|
| `test_concurrent_reservations_admit_exactly_the_limit_and_no_more` | PASSED |
| `test_a_sequential_run_to_the_limit_then_one_more_refuses` | PASSED |
| `test_a_zero_allowance_reserves_nothing_and_accumulates_nothing` | PASSED |
| `test_an_unlimited_plan_always_admits` | PASSED |
| `test_reservations_are_isolated_per_user` | PASSED |
| `test_the_full_enforce_quota_path_refuses_at_the_limit` | PASSED |
| `test_concurrent_reservations_are_atomic_against_real_redis` | **PASSED** |

**But read what that last test actually does before counting it as quota evidence.** Phase 3 asks
whether it invokes the production quota API or only raw Redis. Source analysis
(`staging/p11b13_metered_api.py`, comment- and docstring-stripped so prose about code cannot be
mistaken for code):

```
test_concurrent_reservations_are_atomic_against_real_redis   (line 229)
  real-Redis gated : True
  production API   : NONE
  raw Redis calls  : aioredis.from_url, client.incrby, client.decrby, client.get, client.delete

test_the_full_enforce_quota_path_refuses_at_the_limit        (line 166)
  real-Redis gated : False          <-- ran against MockRedisClient
  production API   : enforce_quota, sd.enforce
```

The real-Redis test re-implements `reserve_quota`'s increment-first-revert **inside the test body**:

```python
async def _reserve_real():
    # Mirror reserve_quota's increment-first-revert against the real server.
    new_usage = await client.incrby(key, 1)
    if new_usage > limit:
        await client.decrby(key, 1)
        return False
    return True
```

The only production artefact it borrows is `SubscriptionEngine._meter_key` (the key format).
`reserve_quota` appears in the file **only inside that comment** — an earlier automated scan of mine
reported it as a production call, which was a false positive I corrected by stripping comments
before matching.

**Therefore: no D5′ test exercises a production quota code path against real Redis.** This test
establishes that real Redis `INCR`/`DECR` are atomic — which was never in doubt — and that the
algorithm is correct when hand-written. The test that *does* call `enforce_quota` is not real-Redis
gated and ran on the in-memory mock. That gap is material to QUOTA-01 and was not visible before
this analysis.

### 4b. `tests/test_webhook_duplicate_delivery_real_redis.py` — 6 tests, 2 failures, 0 skipped (1.600 s)

| # | Test | Result |
|---|---|---|
| 1 | `test_the_credential_gate_refuses_test_keys` | PASSED |
| 2 | `test_a_redelivered_webhook_is_ignored` | PASSED |
| 3 | `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **FAILED** |
| 4 | `test_a_different_event_id_is_not_deduplicated` | PASSED |
| 5 | `test_an_in_flight_delivery_blocks_a_concurrent_one` | **FAILED** |
| 6 | `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | PASSED |

```
>       assert value is not None, f"no idempotency key at {key} after a delivery"
E       AssertionError: no idempotency key at webhook:razorpay:evt_90948c27af79 after a delivery
WARNING  redis_manager.py:650 Cache GET failed for 'webhook:razorpay:evt_90948c27af79': Event loop is closed

E       AssertionError: a delivery arriving while evt_6571563fb645 was still in flight was
                        processed anyway: {'status': 'success'}
WARNING  redis_manager.py:650 Cache GET failed for 'webhook:razorpay:evt_6571563fb645': Event loop is closed
```

**Both keyspace directions, accounted for separately as Phase 3 requires:**

| Direction | Test | Result | Reading |
|---|---|---|---|
| app writes → test reads | #3 | **FAILED** | handler wrote the lock to DB 0; the test looked in DB 15 |
| test writes → app reads | #5 | **FAILED** | test installed `processing` in DB 15; the handler read DB 0 and saw nothing |
| neither direction needed | #1, #2, #4, #6 | PASSED | #2 proves the handler's dedup works correctly against real Redis — on DB 0 |

**The partition is identical to the full-suite run (38044138718), test for test.** Since this run
executed the file alone, full-suite state leakage is eliminated as an explanation. The A9 keyspace
diagnosis stands, now confirmed under isolation.

One refinement: `Event loop is closed` also appears in the isolated run, so it is **intra-file**,
not cross-file contamination — pytest-asyncio closes each test's loop while the module-level
singleton retains the connection opened during test #2. It is a confounder, not the cause: even a
healthy read against DB 0 would have found nothing, because the marker was written to DB 15.

### 4c. `tests/test_quota01_exchange_slot_is_atomic.py` — 22 tests, 21 failures, 0 skipped (3.296 s)

**The A11 job's hypothesis is answered, and it was wrong.** Its comment proposed that whole-file
failure with one survivor looked like a fixture- or collection-level signature, possibly full-suite
leakage. Running the file alone against real Redis produced **the same 21 failures**. The cause is
not leakage.

Every failure is one of three messages:

| Count | Message |
|---|---|
| 14 | `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute 'exchange_connection_slot_guard'` |
| 6 | `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute '_slot_lock_key'` |
| 1 | `AssertionError: store_keys does not use exchange_connection_slot_guard; the connection limit is back to a read-then-write race (QUOTA-01)` |

The single pass, `test_retrying_a_refused_request_does_not_eventually_succeed`, survives for a
stated reason in its own docstring — it "asserted on the arguments passed to the cache rather than
by waiting", so it never reaches the missing guard.

**Consequences for the acceptance criteria, stated plainly:**

- `test_the_slot_lock_is_mutually_exclusive_against_real_redis` — **FAILED on an attribute lookup.**
  Exchange-slot mutual exclusion is **NOT verified**; no Redis command was issued.
- `test_the_guard_admits_exactly_the_limit_against_real_redis` — **FAILED the same way.**
  Exact-limit behaviour is **NOT verified**.
- Lock release and TTL (`test_a_normal_exit_releases_the_lock`,
  `test_release_reports_each_ownership_outcome`,
  `test_the_guard_does_not_delete_a_successors_lock_when_its_own_expired`,
  `test_the_lock_carries_a_ttl_so_a_killed_worker_cannot_wedge_an_account`) — all **FAILED**
  on the same absence. Release safety is **NOT verified**.
- The 21st failure is a **true positive, not collateral**: `store_keys` on `main` still calls the
  non-atomic `check_exchange_connection_slot`. QUOTA-01's read-then-write race is **live on `main`
  today**, independently confirmed under file isolation.

---

## 5. Phase 2 — the slot-lock dependency

Full diffs of both files reviewed against `HEAD`, every hunk classified
(`staging/p11b13_phase2_inspect.py`, `staging/p11b13_hunk_detail.py`).

### `backend_app/core/subscription_dependencies.py` — **ISOLABLE**

```
status                 M   (unstaged only; not in the shared index)
vs HEAD           +253 / -0      929 -> 1182 lines
hunks                  3   |  3 of 3 serve the slot lock
```

| Hunk | Content | Serves the slot lock? |
|---|---|---|
| `@@ -45,2 +45,3 @@` | `+import asyncio` | yes — the slot-lock block calls `asyncio.` (verified) |
| `@@ -49,2 +50,6 @@` | `+import random`, `+import time`, `+import uuid`, `+from contextlib import asynccontextmanager` | yes — `random.` is used for the retry jitter (verified) |
| `@@ -872,2 +877,250 @@` | `_SLOT_LOCK_TTL_SECONDS`, `_slot_lock_key`, `_acquire_slot_lock`, `_release_slot_lock`, `exchange_connection_slot_guard` | yes — the implementation itself |

**Zero deletions**, no other workstream's content, not staged by anyone else. This file could be
committed on its own.

### `backend_app/routers/exchange.py` — **NOT ISOLABLE**

```
status                MM   <-- STAGED *and* unstaged. It is one of the 22 entries another
                               workstream has in the shared .git/index.
staged   (HEAD->index)  +268 / -15
unstaged (index->tree)  +154 / -14
total vs HEAD           +422 / -29     825 -> 1218 lines
hunks                    20   |  only 3 mention a slot-lock symbol  |  17 do NOT
```

The 17 unrelated hunks are at least two other named workstreams plus error-handling work:

| Marker | Occurrences | What it is |
|---|---|---|
| `AUTH-01` | 2 | `Depends(require_aal2)` — MFA/AAL2 now required on `store_keys` and the disconnect endpoint |
| `CONN-01` | 5 | "THIS ENDPOINT NOW ACTUALLY RECONNECTS" |
| — | 1 hunk | a 136-line insertion at `@@ -64,0 +67,136 @@` |
| — | ~8 hunks | error-handling rewrites across `store_keys`, delete, auth-schema, connection-test and stored-connection-test endpoints |

And the three slot-lock hunks **cannot be lifted out**:

1. `@@ -24 +25,2 @@` — rewrites the import line (**contains a deletion**).
2. `@@ -199,0 +373,14 @@` — a +14-line docstring in `store_keys`.
3. `@@ -221,10 +420,24 @@` — **−10 / +24**: replaces the bare `vault.store_exchange_keys(...)`
   call with the guarded version.

Two independent blockers. First, these hunks carry deletions, so unlike the A6 workflow case (zero
deletions, where omitting a hunk provably could not drop anyone's lines) a partial application here
can silently discard another workstream's edits. Second, the line offsets have shifted by roughly
200 lines (HEAD 199 → worktree 373; HEAD 221 → 420) *because of* the AUTH-01/CONN-01 insertions
above them — the slot-lock hunks depend on context those workstreams created.

### Empirical confirmation, not just structural

The structural argument above is testable, so I tested it. A **private** index was seeded from
`HEAD` via `GIT_INDEX_FILE`, and each file's unstaged patch was offered to it. The shared
`.git/index` was never opened for writing; its checksum is shown before and after.

```
shared .git/index BEFORE: 6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613

backend_app/routers/exchange.py                  (unstaged patch, 13 886 bytes)
  git apply --cached --recount --check   rc = 1
  error: patch failed: backend_app/routers/exchange.py:366
  error: backend_app/routers/exchange.py: patch does not apply
  VERDICT: DOES NOT APPLY onto HEAD -- entangled with the staged state

CONTROL - backend_app/core/subscription_dependencies.py   (unstaged patch, 13 923 bytes)
  git apply --cached --recount --check   rc = 0
  VERDICT: APPLIES onto HEAD cleanly

private index removed: True
shared .git/index AFTER : 6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613
```

The control matters as much as the test: the same procedure that **refuses** `exchange.py`
**accepts** `subscription_dependencies.py`, so the failure is a property of the entanglement and
not of the method. This independently reproduces the result reported by the other A12 session,
which ran the same check and got the same error at the same line.

### Phase 2 verdict

**Safe inclusion cannot be established. Both files left untouched.**

| Dependency | File | Owner | Blocker |
|---|---|---|---|
| `_slot_lock_key`, `_acquire_slot_lock`, `_release_slot_lock`, `exchange_connection_slot_guard` | `backend_app/core/subscription_dependencies.py` | the workstream holding ` M` on this file | clean in itself, but worthless alone (see below) |
| `store_keys` calling the guard around its vault write | `backend_app/routers/exchange.py` | the AUTH-01 / CONN-01 workstream(s) holding `MM` — **partly staged in the shared index** | 17 of 20 hunks unrelated; slot hunks carry deletions and depend on their context |

Worth being precise about what partial inclusion would buy: shipping
`subscription_dependencies.py` alone would resolve **20 of the 21** slot-lock failures. The 21st,
`test_store_keys_uses_the_guard_around_its_vault_write`, asserts the guard appears in `store_keys`'s
source and would still fail — correctly, because the production race would still be live. So even
the isolable half does not close QUOTA-01, and I did not commit it.

I have no mechanism to obtain the owning workstream's approval, which Phase 2 requires before any
production-code change. Stopping is the specified behaviour.

---

## 6. What is and is not verified against real Redis

| Claim | Verified? | Basis |
|---|---|---|
| The gate reaches `state=run` in CI | **YES** | `gate_state.txt` from the run |
| All 35 tests across the three files execute, zero skips | **YES** | three JUnit XMLs |
| Real Redis `INCR`/`DECR` are atomic under 50-way concurrency | **YES** | metered real-Redis test |
| The metered algorithm admits exactly the limit | **YES** | same test — but hand-written, not production code |
| **The production `reserve_quota` / `enforce_quota` path works against real Redis** | **NO** | the test that calls `enforce_quota` is not real-Redis gated |
| The webhook handler deduplicates a redelivery against real Redis | **YES** | test #2 passed |
| The webhook lock is written under the documented key, with a TTL | **NO** | test #3 failed — keyspace split |
| An in-flight delivery blocks a concurrent one | **NO** | test #5 failed — keyspace split |
| **The A12 keyspace fix** | **NOT TESTED ANYWHERE** | uncommitted; on no branch; never in CI |
| Exchange-slot mutual exclusion | **NO** | `AttributeError` before any Redis command |
| Exchange-slot exact-limit admission | **NO** | same |
| Exchange-slot release safety / TTL | **NO** | same |
| `store_keys` uses the atomic guard | **NO — refuted.** The read-then-write race is live on `main` | true-positive assertion failure |

**Real-Redis integration acceptance: FAILED.** This is a stronger and worse statement than the
A6/A9/A12 verdict of UNVERIFIED. The tests ran, against a healthy real Redis, with the gate in
`run` state and no skips — and 23 of 35 failed. The acceptance criteria are not met, and the
reasons are now known rather than suspected.

---

## 7. Unresolved dependencies

1. **PR identity.** Three open D5′ PRs; #7 and #8 byte-identical. Needs an owner decision, not a
   technical one.
2. **No commit holds a verifiable combination.** The A12 webhook fix (worktree), the `/15 → /0`
   gate-variable fix (worktree, other session), the A11 diagnostic job (PR #6) and the slot guard
   (worktree) are in four different places, and two of those four are uncommitted edits from two
   different sessions sharing one working tree.
3. **`routers/exchange.py` is partly staged by another workstream** and carries AUTH-01 + CONN-01.
   Hard blocker on the `store_keys` guard.
4. **A12 has never run against real Redis.** Its local verification (A12 §5) used in-process doubles.
5. **No production quota path has real-Redis coverage.** Needs a new test, which is new scope.
6. **`Event loop is closed`** on the shared async connection across pytest-asyncio loops — present
   even in isolation. Owner: `tests/conftest.py` (` M`, other workstream).
7. **Webhook idempotency fails open on a read fault and closed on a write fault** (A9 §7a). Owner:
   `billing.py` + `core/cache/redis_manager.py`, both ` M`.
8. **`get_redis_url` discards an explicitly configured database index** and captures `REDIS_URL` at
   import. Owner: `backend_app/backend/redis_manager.py` (clean). This is the root cause of the
   webhook keyspace split; A12 works around it from the test side only.

---

## 8. Next required approvals

Nothing below has been done. Each needs separate authorization.

| # | Action | Why it is the smallest useful step | Risk |
|---|---|---|---|
| 1 | **Decide the canonical D5′ PR** and close the duplicates | Everything else is blocked on it; #7 and #8 are byte-identical so either is technically fine, and #6 is the only one carrying the diagnostic job | none |
| 2 | Land, onto that one branch: my A12 webhook fix + the other session's `/15 → /0` workflow change + the A11 diagnostic job, and refresh the one stale `/15` quote in my docstring and in the diagnostic job's own env | Produces the first run in which the webhook tests can actually pass; the JUnit-artifact path is already proven to survive a failing run | low — test + workflow only |
| 3 | **Drop `tests/test_quota01_exchange_slot_is_atomic.py` from whichever PR lands**, and land it later with its implementation | Without the implementation it fails 21 of 22 in a blocking lane. The other A12 session independently reached the same recommendation | none — removes a guaranteed failure |
| 4 | Ask the `subscription_dependencies.py` owner to ship the slot-lock implementation | Resolves 20 of 21 slot-lock failures; proven to apply onto HEAD cleanly (§5) | low — zero deletions, isolable |
| 5 | Ask the AUTH-01 / CONN-01 owner to ship the `store_keys` guard **in their own change** | The only safe route; closes the 21st failure and the live read-then-write race | theirs to assess |
| 6 | Add real-Redis coverage for the production `reserve_quota` / `enforce_quota` path | Currently zero; QUOTA-01's metered claim cannot be evidenced without it. The other A12 session has already written a step-by-step specification for this test | new scope |
| 7 | Remove the `d5-redis-diagnostics` job once the four failures are closed | Its own comment calls it scaffolding; the four are now classified | none |

I would not merge any D5′ PR on the present evidence. Each of the three would land test files that
fail 23 of 35 in a blocking lane, and item 3 is the cheapest way to change that.

---

## 9. Safety confirmations

| Invariant | State |
|---|---|
| Files written outside `staging/` | **none** |
| Shared `.git/index` SHA-256 | `6d965c8e…` — unchanged, 22 staged entries intact |
| Branch / HEAD | `main` / `074b772a…` — unchanged |
| `backend_app/core/subscription_dependencies.py`, `backend_app/routers/exchange.py` | **untouched** |
| Commit / push / PR change / merge / CI trigger | **none** |
| Staging, reset, stash, clean, revert | **none** |
| Remote operations | read-only: `git fetch`, `gh pr list`, `gh pr view`, `gh api` GETs, one artifact download |
| Production Redis / databases | **not accessed** |
| Infrastructure installed or started | **none** |
| Readiness scores / gate registers | **not updated** — no authorization |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

QUOTA-01 stays open on all four counts named in the brief, and this phase moved none of them:
production quota reservations have **no** real-Redis coverage (§4a); exchange-slot concurrency and
release safety are **unverified** because the implementation is absent from every commit (§4c); and
the outstanding rate/order/trade limits were not in scope here.

Artifacts: `p11b13_diag.zip` and `p11b13_diag/` (the retrieved CI evidence),
`p11b13_junit_summary.txt`, `p11b13_manifest.txt`, `p11b13_phase2.txt`, `p11b13_hunks.txt`,
`p11b13_metered_api.txt`, `p11b13_apply_check.txt`, `p11b13_a11_diag_job.diff`, plus the scripts
that produced them. The private index used for the apply test was deleted.

### Cross-check against the other A12 session

Its report (`staging/PHASE11B_A12_D5_MINIMAL_REPAIR_REPORT.md`) was read in full and its findings
reconciled with mine. We agree, independently, on all four substantive points: the DB-0/DB-15
mechanism, the `exchange.py` entanglement (same `git apply` error at the same line),
`subscription_dependencies.py` being separable but useless alone, and the metered real-Redis test
not exercising the production quota API. Two differences worth recording rather than smoothing
over:

- **Untracked file count.** It reports 3 207; I report 134. Both are correct — `git status
  --porcelain` collapses untracked directories by default, and it counted recursively. Neither
  number is an invariant worth tracking; the shared index checksum is.
- **Workflow hunk count.** It reports three hunks, I report two. Same content at different
  `-U` context widths (§1a).
