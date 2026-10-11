# Phase 11B-A19 — D5′ CI Outcome and Real-Redis Evidence Audit

**Date:** 2026-10-10
**Subject commit:** `f5b99e57e1f7644bdbb10712e21f85e2ff236920` (PR #6, confirmed remote head)
**Destination check:** `staging/PHASE11B_A19_D5_CI_EVIDENCE_REPORT.md` verified **ABSENT** before
writing. No existing report or artifact modified. Read-only phase: no dispatch, no rerun, no push,
no commit, no ref or PR change, no database/Redis/venue call from this machine.

---

## HEADLINE

**Real Redis was genuinely used, and it found two real things.**

1. **The production quota path now has its first real-Redis execution, and the hard limit holds.**
   `SubscriptionEngine.reserve_quota` admitted **exactly 10 of 50** concurrent reservations at
   limit 10; `get_quota_usage` and the raw meter key both settled on exactly 10.
2. **But a refused reservation reports an inflated usage figure.** `assert 0 <= usage <= limit`
   raised with **usage 36 against a limit of 10**. Enforcement is sound; the number handed back to
   a refused caller is not. This is a genuine production finding, surfaced by the new coverage.
3. **Four of six webhook tests never ran their assertions.** They ERRORED at setup with
   `Cache SET failed … : Event loop is closed`. The keyspace probe correctly reported this as a
   *refused write*, not a keyspace mismatch — `KEYSPACE SPLIT` never appeared. Setup errors are
   **not passes**.
4. **The blocking lane is still running**, so the baseline node-ID comparison is **UNVERIFIED**.

| Gate | Verdict |
|---|---|
| Webhook tests: no executable skips | **PASS** — 0 skips |
| Webhook tests: required assertions actually ran | **FAIL** — 4 of 6 errored at setup |
| Both production-path quota tests executed against real Redis | **PASS** — both ran, 0 skips |
| Exact-limit concurrency admits precisely the allowed number | **PASS** — 10 of 50 |
| Zero-allowance writes no meter | **PASS** |
| Baseline failures unchanged by node ID | **UNVERIFIED** — blocking job in progress |
| New failure inside the three D5′ files | **YES — 1** (metered refusal-usage) + 4 webhook setup errors |
| Exchange-slot failures persist because the guard is absent | **CONFIRMED** — 21 of 22 |

---

## 1. Phase 0 — preflight

| Check | Value | |
|---|---|---|
| Branch / local `main` HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| Shared index content digest | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | unchanged |
| Staged paths | 22 | unchanged |
| Exchange-slot test blob | `2d7ccbbb04b3af04675b365809d89b4429db4888` | unchanged |
| PR #6 remote head | `f5b99e57e1f7644bdbb10712e21f85e2ff236920` | **matches expected** |
| PR #6 state | OPEN, `mergedAt: null`, `mergeStateStatus: UNSTABLE` | — |
| PR #7 / #8 | OPEN, `closed: false`, heads `49e70da2…` / `a624ce89…` | untouched |

**Baseline sources still appropriate.** Run `37346853502` — `01 PR Check`,
branch `ci/unblock-deploy-gate`, SHA `6c5660a1fe5b`, conclusion `failure`, 2026-10-05 — and its
SHA **is an ancestor of `main`** (verified). `staging/p11b14_preexisting.txt` present, 2 626 B,
sha256 `be527f100d66c11a…`.

**Other executors.** `git worktree list` now shows **only** the main worktree; the concurrent
session's temporary PR #6 worktree has been removed. A second A18 report exists —
`PHASE11B_A18_D5_PR6_COMMIT_REPORT.md` — inventoried and left untouched. No `PHASE11B_A19*` report
existed before this one.

---

## 2. CI run status

### Run `38061973739` — `01 PR Check` — **IN PROGRESS**

`event=pull_request`, attempt 1, head `f5b99e57…`, branch `ci/d5-real-redis-quota-evidence`,
created 15:01:59Z.

| Job | ID | Result | Completed |
|---|---|---|---|
| Code Quality & Static Audit | 114241894736 | success | 15:04:43 |
| Format Backlog (report only) | 114241894841 | success | 15:03:48 |
| Docker Build Validation | 114242427306 | success | 15:06:09 |
| **D5′ Real-Redis Diagnostics (non-blocking)** | 114242427270 | success¹ | 15:07:41 |
| Database Tests (PostgreSQL) | 114242427274 | success | 15:07:46 |
| Execution Tests (production posture) | 114242427294 | success | 15:08:24 |
| Frontend Build & Unit Tests | 114241894533 | success | 15:10:42 |
| **Unit & Integration Tests** | 114242427268 | **in_progress** | — |

¹ This job sets `continue-on-error: true`. **Its green badge is not a pass signal** and is not
treated as one anywhere in this report — its JUnit XML is, and that XML records 26 failures and
errors across the three files.

Artifact: `d5-redis-diagnostics-c55ba078305d8a602c7df93ee7840c918e81273e`, 26 282 B, not expired,
downloaded and analysed below. (The name carries the PR merge SHA, not the head SHA — normal for
`pull_request` events.)

### Run `38061973701` — `05 Security` — **COMPLETED / success**

Bandit SAST success 15:02:34; Trivy success. Artifact `security-scan-reports`, 48 257 B. Not part
of D5′ acceptance.

---

## 3. Real-Redis execution evidence

Per the brief, `/0` alone is not accepted as proof. Four independent signals:

| Evidence | Value |
|---|---|
| Gate state recorded **before any test ran** | `real-Redis gate: state=run (AERORA_TEST_REDIS_URL=set, remote_allowed=False)` / `state=run` / `reason=(none)` |
| Skips across the three D5′ files | **0** — `skipped=0` in all three JUnit suites |
| Client the app resolved to | `Redis(ConnectionPool(Connection(host=localhost,port=6379,**db=0**)))` — printed in the traceback, the derived application database |
| A real round-trip succeeded | `test_a_redelivered_webhook_is_ignored` **PASSED** — the handler deduplicated a redelivery through the real server; and `test_concurrent_reservations_are_atomic_against_real_redis` plus both production-path tests executed server-side operations |

The gate's three-state discipline held: the opt-in was **set** (so not "unset → skip") and the
endpoint **answered** (so not "configured but unreachable → fail"). `MockRedisClient` was never
resolved — the four occurrences of that string in the log are docstring prose, not a resolution.

---

## 4. Per-file results

### `tests/test_quota01_metered_reservation_is_hard.py` — 9 tests, 1 failure, **0 skipped**, 1.222 s

| Test | Result |
|---|---|
| `test_concurrent_reservations_admit_exactly_the_limit_and_no_more` | PASSED |
| `test_a_sequential_run_to_the_limit_then_one_more_refuses` | PASSED |
| `test_a_zero_allowance_reserves_nothing_and_accumulates_nothing` | PASSED |
| `test_an_unlimited_plan_always_admits` | PASSED |
| `test_reservations_are_isolated_per_user` | PASSED |
| `test_the_full_enforce_quota_path_refuses_at_the_limit` | PASSED |
| `test_concurrent_reservations_are_atomic_against_real_redis` | PASSED |
| **`test_production_reserve_quota_admits_exactly_the_limit_against_real_redis`** | **FAILED** |
| `test_production_zero_allowance_writes_no_meter_against_real_redis` | **PASSED** |

### `tests/test_webhook_duplicate_delivery_real_redis.py` — 6 tests, 0 failures, **4 errors**, **0 skipped**, 1.497 s

| # | Test | Result |
|---|---|---|
| 1 | `test_the_credential_gate_refuses_test_keys` | PASSED |
| 2 | `test_a_redelivered_webhook_is_ignored` | **PASSED** — real dedup round-trip |
| 3 | `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **ERROR at setup** |
| 4 | `test_a_different_event_id_is_not_deduplicated` | **ERROR at setup** |
| 5 | `test_an_in_flight_delivery_blocks_a_concurrent_one` | **ERROR at setup** |
| 6 | `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | **ERROR at setup** |

### `tests/test_quota01_exchange_slot_is_atomic.py` — 22 tests, **21 failures**, 0 skipped, 3.156 s

20 are `AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute
'exchange_connection_slot_guard'` (14) or `'_slot_lock_key'` (6). The 21st is
`AssertionError: store_keys does not use exchange_connection_slot_guard; the connection limit is
back to a read-then-write race (QUOTA-01)`. One passes:
`test_retrying_a_refused_request_does_not_eventually_succeed`.

---

## 5. Root cause of each new failure

### 5a. Metered — the production refusal reports an inflated usage figure

pytest's `>` marker identifies the raising line precisely; everything above it executed without
raising:

```
          assert len(allowed) == limit            <- PASSED: exactly 10 of 50 admitted
          assert len(refused) == racers - limit   <- PASSED: 40 refused
          via_api = await SubscriptionEngine.get_quota_usage(user, resource, period)
          via_key = int(await client.get(key))
          assert via_api == limit                 <- PASSED: production reader == 10
          assert via_key == limit                 <- PASSED: raw meter key == 10
          for ok, usage, eff in refused:
              assert eff == limit                 <- PASSED
>             assert 0 <= usage <= limit, (       <- RAISED
E             AssertionError: a refusal reported usage 36, outside [0, 10] --
E                             the revert either did not happen or overshot
E             assert 36 <= 10
tests/test_quota01_metered_reservation_is_hard.py:469: AssertionError
```

**What this establishes, and what it does not.** The hard limit is **correct** against a real
out-of-process counter: exactly the allowed number admitted, and both the production reader and
the raw key agree on exactly the limit afterwards. What is wrong is the usage value returned to a
**refused** caller: `reserve_quota` increments first and reverts on overflow, so a racer can
observe the transient inflated count (36) and return it. The meter is not left corrupted — it
settles at 10 — but the refusal payload is unreliable under concurrency.

Classification: **new failure in a D5′ file, but the underlying behaviour is pre-existing in
`reserve_quota` and was simply never observable before.** It is not a break introduced by this
patch, and it is not a harness artifact. Treating it as either would be wrong.

### 5b. Webhook — the async connection outlives its event loop

All four setup errors share one cause, now stated verbatim rather than inferred:

```
WARNING backend_app.core.cache.redis_manager:redis_manager.py:660
  Cache SET failed for 'aerora:test:keyspace-probe:app-wrote:3effca46…': Event loop is closed
```

— identical at log lines 100, 183, 266 and 349, one per failing test. The probe's direction-1
`await manager.set(...)` therefore returned `False`, and the probe failed closed:

```
>   pytest.fail(
E   Failed: the application cache manager REFUSED a write it was handed a real cache for
        (`set` returned False). … This is not a keyspace mismatch -- the write never landed
        anywhere.
tests/test_webhook_duplicate_delivery_real_redis.py:237: Failed
```

Three things follow.

- **This is the A9 §7b defect**, previously seen on `GET`, now on `SET`: the shared manager's
  async connection is created on one test's event loop and reused after pytest-asyncio closes it.
  Test 2 passed because it ran on the loop that opened the connection; tests 3–6 did not.
- **The A12 probe's design worked.** It distinguished "the manager refused the write" from "the
  two clients see different keyspaces", and `KEYSPACE SPLIT` appears **nowhere** in the log. The
  earlier keyspace defect is not what is failing now.
- **The keyspace fix is only partially validated.** Test 2's pass is positive evidence that the
  handler and the test client agreed on one keyspace for the one test that got a live connection.
  The remaining four never reached their assertions, so their verdict is **not** "fixed" — it is
  **unexecuted**.

### 5c. Exchange-slot — guard absent, exactly as deferred

The guard symbols exist only in uncommitted work (`subscription_dependencies.py` ` M`). The 21st
failure is the honest one: `store_keys` still wraps its vault write without the guard, so the
read-then-write race is live on this branch and on `main`.

---

## 6. Failure classification

| Count | Failure set | Category |
|---|---|---|
| 21 | `test_quota01_exchange_slot_is_atomic.py` — 20 `AttributeError` + 1 `store_keys` assertion | **3 — exchange-slot guard failure, expected because the guard remains absent** |
| 4 | webhook setup errors — `Event loop is closed` on `Cache SET` | **4 — infrastructure (async connection lifecycle), not Redis provisioning.** Redis was provisioned and reachable: gate `state=run`, and test 2 completed a real round-trip |
| 1 | `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis` | **2 — new failure in a D5′ file**, qualified: the cause is pre-existing `reserve_quota` behaviour newly made observable, not a regression introduced by the commit |
| — | Pre-existing baseline failures (category 1) | **UNVERIFIED** — see §7 |
| 0 | Unclassified (category 5) | none |

No missing, skipped, cancelled or setup-failed test is counted as a pass anywhere above.

---

## 7. Baseline node-ID comparison — UNVERIFIED

**Cannot be completed in this phase.** The `Unit & Integration Tests` job (114242427268) is still
`in_progress`, and it is the only job that runs the full suite. Without its result there is no
node-ID set to compare against run `37346853502`'s 39 pre-existing failures
(`staging/p11b14_preexisting.txt`).

I did not wait indefinitely and did not initiate another run, per instruction.

What can be said now: the diagnostics job runs each D5′ file **in isolation**, so its 26 failures
and errors are attributable to the three files alone and carry no information about the other
~12 000 tests. The expected blocking-lane shape, stated as a prediction and not a result, is
39 baseline + 21 slot-lock + 1 metered + 4 webhook. Confirming that requires the node-ID list.

**The overall `01 PR Check` run is not green and must not be described as green.** Equally, its
eventual red status will not by itself constitute a D5′ regression if every failure resolves to
the baseline plus the deferred exchange-slot work — that determination is exactly what the pending
node-ID comparison decides.

---

## 8. Acceptance criteria, reported separately

| Criterion | Verdict | Evidence |
|---|---|---|
| Webhook tests execute without executable skips | **PASS** | `skipped=0`; 0 executable `pytest.skip` in the pushed blob |
| Webhook required assertions actually run | **FAIL** | 4 of 6 ERROR at setup; assertions never reached |
| Both production-path quota tests execute against real Redis | **PASS** | both present, both executed, gate `state=run`, 0 skips |
| Exact-limit concurrency admits precisely the allowed number | **PASS** | `assert len(allowed) == limit` passed — 10 of 50, 40 refused |
| Production reader agrees with the raw Redis meter key | **PASS** | `via_api == 10` and `via_key == 10` both passed |
| Refused reservations report a sane usage figure | **FAIL** | usage 36 against limit 10 |
| Zero-allowance writes no meter | **PASS** | `test_production_zero_allowance_writes_no_meter_against_real_redis` PASSED, `EXISTS == 0` |
| Baseline failures unchanged by node ID | **UNVERIFIED** | blocking job in progress |
| New failure inside the three D5′ files | **YES — 1 test failure + 4 setup errors** | §5a, §5b |
| Exchange-slot failures persist because the guard is absent | **CONFIRMED** | 21 of 22, all on the missing symbols |

---

## 9. Gate verdicts

| Gate | Verdict | Basis |
|---|---|---|
| **D5′ real-Redis wiring** | **VERIFIED** | gate `state=run`, 0 skips in all three files, app resolved to `db=0`, a real dedup round-trip and real production `reserve_quota` calls executed |
| **D5′ acceptance** | **NOT MET** | 4 webhook assertions unexecuted; 1 production quota assertion failed; baseline comparison pending |
| **QUOTA-01** | **OPEN** | The metered hard limit is now *evidenced* against real Redis — a genuine advance — but the refusal payload is wrong under concurrency, and `store_keys` still has the live read-then-write race |
| **Exchange-slot concurrency and release safety** | **UNPROVEN** | 21 of 22 tests never issued a Redis command; guard absent from the branch |
| **TENANT-01** | **OPEN** | 22 staged paths untouched |
| **Live trading** | **OFF / NOT CERTIFIED** | unchanged |

---

## 10. Boundaries honoured

| | |
|---|---|
| CI dispatched, rerun, or triggered | **no** — both runs are `event=pull_request` from the A18 push; no `gh run`, no `workflow_dispatch` |
| Push / commit / branch change / PR modification | **none** |
| PR #7 or #8 closed | **no** — both OPEN, `closed: false` |
| Exchange-slot test removed, disabled or rewritten | **no** — blob `2d7ccbbb04b3…` unchanged |
| `exchange.py` / `subscription_dependencies.py` modified | **no** |
| Database, Redis or venue call from this machine | **none** — all Redis evidence is CI-side |
| Shared index / worktree / refs | unchanged: digest `7379218b…`, 22 staged, `main` at `074b772a…` |

### Correction to my own working notes

An intermediate artifact (`staging/p11b19_detail.txt`) inferred which assertions had passed by
searching the log for each assertion's message text. **That inference is invalid** — pytest echoes
the entire source of a failing test under `--tb=long`, so a message string appears whether or not
its assertion fired. It is superseded by `staging/p11b19_detail2.txt`, which reads the `>` marker
that identifies the actual raising line. The conclusions in §5a rest on the marker, not the
string search. The flawed artifact is retained, annotated, rather than quietly deleted.

---

## 11. Next, needing authorization

1. **Re-read the blocking job when it completes** and perform the node-ID comparison against run
   `37346853502`. Read-only; this is the one outstanding item for a complete verdict.
2. **File the refusal-usage defect** against `SubscriptionEngine.reserve_quota`: a refused
   reservation should report the post-revert figure, not the transient pre-revert count. Owner:
   `backend_app/core/subscription_engine.py` (clean, unowned).
3. **File the event-loop defect** — the shared async Redis connection outliving its creating event
   loop, now observed on both `GET` and `SET`. Owner: `tests/conftest.py` (` M`, other workstream).
   Until fixed, four webhook assertions cannot execute in CI regardless of the keyspace fix.
4. **Decision C** (slot-guard owner) remains the gate on the 21 expected failures.

Artifacts: `p11b19_preflight.{py,txt}`, `p11b19_artifact.{py,txt}`, `p11b19_detail.{py,txt}`
(superseded), `p11b19_detail2.{py,txt}`, `p11b19_diag.zip`, `p11b19_diag/` (gate_state.txt,
three JUnit XMLs, three logs).
