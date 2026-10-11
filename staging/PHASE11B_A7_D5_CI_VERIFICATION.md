# PHASE 11B-A7 — D5′ CI VERIFICATION AND PRODUCTION-PATH GAP

**Date:** 2026-10-10 · **Live trading OFF / NOT CERTIFIED** · **QUOTA-01 remains OPEN**

| | |
|---|---|
| Branch | `ci/d5-real-redis-switch` |
| Commit | `a624ce89b16005b0c73197005d6d0672737dad65` |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (== `origin/main`) |
| PR | **https://github.com/Narendra2212/vyomquant-saas/pull/8** — OPEN, **not merged** |
| CI run | **38046625040** — `01 PR Check`, event `pull_request`, head `a624ce89` |
| Job | `Unit & Integration Tests` — id `114197790236` |
| Job URL | https://github.com/Narendra2212/vyomquant-saas/actions/runs/38046625040/job/114197790236 |

## ▶ HEADLINE

**The D5′ mechanism works. It is verified, in CI, against a real Redis.**
The gate reached `run`, **zero** real-Redis tests skipped, and the required concurrency test
**PASSED**.

**But real-Redis integration overall classifies as FAILED**, because four other previously
dormant real-Redis tests executed for the first time and four of them failed.

**And this does not close QUOTA-01** — the required test does not call
`SubscriptionEngine.reserve_quota`. §6.

---

## 1. Phase 0 — fresh baseline and ownership

Re-measured, not taken from A4. One material change since A4 is recorded.

| Item | A4 | Now |
|---|---|---|
| HEAD / `origin/main` | `074b772a` / same | **unchanged** |
| ahead / behind | 0 / 0 | **0 / 0** |
| Staged entries | 22 | **22** |
| Unstaged | 97 | **100 — CHANGED** |
| `.git/index` SHA-256 | — | `6D965C8E…458613` (232,081 bytes) |

**New concurrent activity since A4** (recorded, not touched): unstaged count rose 97 → 100;
new untracked `tests/test_api01_strategies_update_allowlist.py`,
`tests/test_api05_documentation_exposure_matrix.py`; newly modified
`tests/property/test_paper_confluence.py`. **None intersects the five-path manifest.**

### 1.1 Manifest validated — `staging/a7_manifest_check.py`, exit 0

| Path | State | SHA-256 (16) | Bytes |
|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | ` M` | `03d65b03231f86bd` | 44,466 |
| `tests/real_redis_gate.py` | `??` | `5fa398a1dd360e24` | 8,003 |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` | `ffdb7f47bd797a1b` | 11,978 |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | `b2e3822d6fc1b428` | 41,804 |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | `c6f1b45c90059994` | 19,483 |

Four safety properties, all measured:

1. **No `tests.*` dependency outside the manifest.** All three test files import exactly
   `tests.real_redis_gate`, which is in the manifest. An omitted dependency would have
   produced an ImportError on CI's first collection.
2. **Every `backend_app.*` import is already committed** — `subscription_engine`,
   `subscription_dependencies`, `cache.redis_manager`, `routers.billing`. The branch depends
   on no other workstream's untracked source.
3. **No destructive Redis cleanup** — no `FLUSHDB`, `FLUSHALL`, `scan_iter` or `.keys(` in
   any manifest file.
4. **Infrastructure the tests rely on is committed, so `conftest.py` is correctly excluded:**
   `redis_manager._bypass_dev_mode` exists on `origin/main`, and so do
   `_restore_shared_redis_singleton` / `_REDIS_SINGLETON_SEAMS` — whose tuple includes
   `_dev_mode_bypass`, so flipping the bypass cannot leak into another test.

**The two extra quota files A4 discussed were deliberately NOT added**
(`test_quota01_enforcement_is_wired.py`,
`test_quota01_hard_enforcer_is_deprecated_in_code.py`,
`test_redis_fallback_does_not_fake_a_distributed_lock.py`). They were not in the approved
five-path manifest.

### 1.2 Dependency-divergence risk, checked

`backend_app/routers/billing.py` is **modified-unstaged by another workstream**, and its
diff rewrites `razorpay_webhook` substantially (6 hunks, ~300 added lines). The webhook tests
would run against the **committed** version on CI. Verified on `origin/main`:
`razorpay_webhook`, `_validate_keys` and `_process_razorpay_entitlement` all exist, and
`billing.py:2346` implements the exact two-phase lock the tests assert —
`idempotency_key = f"webhook:razorpay:{event_id}"`, `set(..., nx=True, ex=60)`,
`delete` on bad signature, `set(..., "completed", ex=86400)` on success. Compatible.

### 1.3 Workflow hunk — still disjoint

`staging/a4_hunk_map.py`, re-run: **2 hunks at `-U3`**, ~300 lines apart, no interleaving.

| Hunk | Owner | Added | Functional |
|---|---|---|---|
| 1 | **D5′** | 27 | **1** |
| 2 | other workstream (`provisioning-and-isolation`) | 143 | 77 |

### 1.4 No pre-existing eligible run

`staging/a3_summarise_runs.py`: **0 of 14** prior `01 PR Check` runs contained the change
set; newest was `37346853502` (2026-10-05), unchanged since A3. So run 38046625040 is the
**first run in the repository's history** to exercise D5′.

---

## 2. Phase 2 — local evidence

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `pytest` on the 3 manifest test files, opt-in **unset** | **0** | **26 passed, 9 skipped, 0 failed** |
| 2 | same, `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15` (nothing listening) | **1** | **1 failed, 6 passed** |
| 3 | `python staging/a7_manifest_check.py` | **0** | 4/4 safety properties |
| 4 | `python staging/a4_hunk_map.py` | **0** | 2 disjoint hunks |

**Gate state 1 — unset → SKIP.** All 9 real-Redis tests skipped, each with the gate's reason
ending *"A skip here is NOT a pass."*

**Gate state 2 — configured but unreachable → FAIL, not skip.** From `real_redis_gate.py:175`:

> `AERORA_TEST_REDIS_URL is set but the server did not answer PING (TimeoutError). A
> configured endpoint that cannot be reached is a BROKEN TEST SERVICE, not an absent one, so
> this fails rather than skipping -- otherwise a dead Redis in CI would look like a green
> run. (The endpoint is deliberately not echoed.)`

**Endpoint leakage: 0.** The string `6399` appears nowhere in the output.

**Gate state 3 — reachable → RUN.** Not verifiable locally; no Redis was installed or started,
per the boundaries. Verified in CI instead (§4).

**No local run is described as real-Redis verified.** Locally the required test SKIPPED.

---

## 3. Phase 3 — isolated commit, and the near-miss the gate caught

### 3.1 Method

The shared `.git/index` holds 22 other-workstream entries, so every index operation ran with
`GIT_INDEX_FILE` pointed at a scratch file, **seeded with `git read-tree HEAD`** rather than
copied from the shared index — which is what makes it impossible for those 22 entries to
reach this commit. `git commit-tree` then created the commit object without moving `HEAD`,
and `git branch` created the ref without a checkout. **`git stash`, `reset`, `clean` and bare
`git add` were never used.**

### 3.2 A wrong tree was built first, and the verification gate rejected it

The first attempt reconstructed the workflow by taking the **working copy** and deleting the
other workstream's added lines, matched by content. That was wrong: lines such as
`runs-on: ubuntu-latest`, `env:`, `run: |` and `- name: Checkout code` occur in several jobs,
so "delete the first occurrence" removed them from **pre-existing** jobs and left the new
job's copies in place. The result had the other job's **name** line gone — so a substring
check for `provisioning-and-isolation` returned **False** — while its entire 77-line body
survived.

The gate stopped it: the workflow showed **46 functional added lines** instead of 1.

**That is why the gate is set equality, not a count.** It now requires the committed added
lines to be **byte-equal to the reviewed D5′ hunk**, plus: 0 removed lines, exactly 1
functional line, and absence of three distinctive body markers
(`provisioning-and-isolation`, `postgres:17-alpine`, `provision_database.py`). A
name-substring check alone would have passed the wrong tree.

The second method applies the hunk **positionally** to HEAD's blob and asserts every context
line matches before emitting anything.

### 3.3 Verification of the committed tree — all green

```
parent (HEAD)                     : 074b772ab16cece62cbfca0f2b78497be2ae56d5
prospective tree                  : 271f13c29d3c09a205dbaae959cad002cc0fd0a6
paths changed vs parent            : exactly the 5 manifest paths
workflow added lines              : 27 (1 functional)
workflow removed lines            : 0
matches reviewed D5' hunk exactly : True
'provisioning-and-isolation'      : absent
shared .git/index SHA256 (before) : 6d965c8e…458613
shared .git/index SHA256 (after)  : 6d965c8e…458613   <- byte-identical
shared staged entries             : 22
=== ISOLATION PROVEN ===
```

Independent re-check of the commit: `git diff-tree` shows `M` on the workflow and `A` on the
four test files, `+1761 insertions, 0 deletions`, and **0** occurrences of
`provisioning-and-isolation|postgres:17-alpine|provision_database` in the full diff.

The hand-built blob was additionally parsed as YAML: it loads, carries the **same 7 jobs** as
`origin/main` (no provisioning job), and `unit-tests → Run Unit Tests → env` now reads
`{REDIS_URL, AERORA_TEST_REDIS_URL, ENV, AERORA_MODE, PYTHONPATH}`.

### 3.4 Push, verified

The push reported only **1 object / 980 bytes**, which is too small for 1,761 insertions, so
it was checked rather than assumed: `origin/ci/d5-real-redis-switch` = `a624ce89`, all four
test files present in the remote tree, and `AERORA_TEST_REDIS_URL` present in the remote
workflow. The small transfer was packing negotiation, not a partial push.

---

## 4. Phase 4 — CI evidence for `a624ce89`

### 4.1 Redis service: HEALTHY, and genuinely written to

```
Starting redis service container ... redis:7-alpine -p 6379:6379
   --health-cmd "redis-cli ping" --health-interval 10s --health-timeout 5s --health-retries 5
10:59:13  redis service is healthy.
```

Service container log:

```
Redis version=7.4.11, bits=64 ... just started
Running mode=standalone, port=6379.
Ready to accept connections tcp
11:04:00  * 100 changes in 300 seconds. Saving...
11:04:00  * DB saved on disk
```

**`100 changes in 300 seconds` is the decisive line.** It is Redis itself reporting ≥100
write operations against its own keyspace. No mock produces that. It independently
establishes that real writes reached the real server.

### 4.2 Switch present

```
AERORA_TEST_REDIS_URL: redis://localhost:6379/15
```

### 4.3 Gate result: **`run`**, not `skip`

- gate skip-reason (`"Real-Redis coverage is opt-in"`) occurrences in the log: **0**
- real-Redis tests reported `SKIPPED`: **0**

All nine previously dormant tests **executed**. That is the D5′ fix doing exactly its job.

### 4.4 The nine gated tests, individually

**Three quota Redis tests — 1 passed, 2 failed:**

| Result | Test |
|---|---|
| **PASSED** | `test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` ← **the required test** |
| **FAILED** | `test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` |
| **FAILED** | `test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` |

**Six webhook tests — 4 passed, 2 failed:**

| Result | Test |
|---|---|
| PASSED | `test_the_credential_gate_refuses_test_keys` |
| PASSED | `test_a_redelivered_webhook_is_ignored` |
| **FAILED** | `test_the_lock_is_recorded_in_redis_under_the_documented_key` |
| PASSED | `test_a_different_event_id_is_not_deduplicated` |
| **FAILED** | `test_an_in_flight_delivery_blocks_a_concurrent_one` |
| PASSED | `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` |

### 4.5 Overall job result — reported separately

**`Unit & Integration Tests`: completed / FAILURE.** Six of seven jobs in the run succeeded
(static audit, format, database-tests, paper-mode, docker, frontend); the unit-test job is
the only red one, so the **run** concluded failure.

Counts from the `-v` progress lines: **11,836 passed · 60 failed · 51 skipped · 0 errors.**

### 4.6 Failure attribution

| Count | Attribution | File |
|---|---|---|
| 21 | **this commit** | `tests/test_quota01_exchange_slot_is_atomic.py` |
| 2 | **this commit** | `tests/test_webhook_duplicate_delivery_real_redis.py` |
| 0 | this commit | `tests/test_quota01_metered_reservation_is_hard.py` |
| 13 | pre-existing | `tests/test_no_credential_in_api_response_body.py` |
| 9 | pre-existing | `tests/test_validation_sweep.py` |
| 7 | pre-existing | `tests/test_task_6_1_backtest_execute.py` |
| 4 | pre-existing | `tests/test_strategy_status_publish.py` |
| 1 each | pre-existing | `property/test_tenant_isolation_matrix.py`, `test_ml_deployment_guards.py`, `test_task_33_2_rate_limit_keys.py`, `test_task_8_4_runtime_readiness_gate.py` |

**23 attributable · 37 pre-existing.**

The 23 are attributable **by construction**: those two files did not exist before this
commit, so every failure in them is newly surfaced. The exchange-slot file contributes 21 —
i.e. most of the file failed, not only its 2 real-Redis tests, which points to a systemic
problem in that file rather than a Redis one.

**Attribution of the 37 is INFERRED, not measured against a control.** The reasoning: this
commit touches 5 paths, none of them those files, and its only behavioural change is one
environment variable read exclusively by `tests/real_redis_gate.py`. Corroborating: **all 14
prior `01 PR Check` runs concluded `failure`**, so the job was already red. I did not have a
same-baseline control run to subtract, so this is reasoning rather than measurement.

### 4.7 Limitation: per-failure reasons were not obtainable

The downloaded job log (2,733,680 chars / 13,303 lines) contains **one** `FAILURES` section
header, at line 13,221, immediately before teardown. pytest's per-failure tracebacks were
**truncated** by the log transport. So the `-v` outcome lines are complete and trustworthy,
but the *reasons* for the 23 attributable failures are not in hand.

**Do not treat the 21 exchange-slot failures as diagnosed.** Reading them requires the job
UI or a re-run with a pytest report artifact. Recorded as a follow-up, not guessed at.

### 4.8 Classification

Applying the brief's rules literally:

| Claim | Status |
|---|---|
| **D5′ mechanism** (switch → gate reaches `run` → dormant tests execute) | **VERIFIED** |
| **Required concurrency test** `test_concurrent_reservations_are_atomic_against_real_redis` | **PASSED** against the real service |
| **Real-Redis integration overall** | **FAILED** — four required real-Redis tests executed and failed |
| Overall CI job | **FAILURE** (reported separately, §4.5) |

Not **UNVERIFIED**: nothing skipped, and the Redis write evidence establishes real execution.
Not **PASS**: PASS requires *all* required tests to have passed, and four did not.

---

## 5. Production-path coverage gap

Stated plainly, and unchanged by this run.

Production is:

```
SubscriptionEngine.reserve_quota  (subscription_engine.py:959)
    -> redis_manager.incrby / expire / decrby
```

The required test instead opens its own `redis.asyncio` client and **mirrors** that logic:

```python
new_usage = await client.incrby(key, 1)
if new_usage > limit:
    await client.decrby(key, 1)
```

| Claim | Established by this run? |
|---|---|
| Redis server-side `INCR`/`DECR` is atomic under 50-way concurrency | **Yes** — but that is a property of **Redis**, not of this codebase |
| `SubscriptionEngine.reserve_quota` is atomic against a real Redis | **NO — it is never called** |
| `reserve_quota`'s logic is correct | Yes, by the 6 sibling tests — against `MockRedisClient` |

Why it is written that way: `redis_manager._get_active_cache()` returns `MockRedisClient`
whenever `DEV_MODE` is set (`redis_manager.py:728`) and `conftest.py` sets it. A
`_bypass_dev_mode` seam exists (`redis_manager.py:501-515`) and the **webhook** fixture
already uses it; the metered test does not.

**So the production reservation path remains unverified against a real Redis, and this report
does not claim otherwise.** Nothing in the production path was modified and no new
integration test was added, per the boundaries.

---

## 6. Remaining QUOTA-01 blockers

**QUOTA-01 stays OPEN.** This run closed one evidence gap and opened visibility into others.

| # | Blocker | Owner |
|---|---|---|
| 1 | **Production path untested on real Redis.** Needs an authorized test driving `reserve_quota` through `_bypass_dev_mode`, or a recorded decision that Redis-level atomicity plus mock-level logic coverage suffices | quota owner |
| 2 | **21 failures in `test_quota01_exchange_slot_is_atomic.py`**, including both of its real-Redis tests. Reasons not yet readable (§4.7) | quota owner |
| 3 | **2 webhook failures** — `test_the_lock_is_recorded_in_redis_under_the_documented_key`, `test_an_in_flight_delivery_blocks_a_concurrent_one`. First-ever execution. Note `billing.py` is mid-rewrite unstaged elsewhere (§1.2), so these may reflect committed-vs-local divergence | billing / webhook owner |
| 4 | **Atomic exchange-slot lock release** — explicitly out of scope here | quota owner |
| 5 | **Order-rate, daily-trade, active-order quota gates** — out of scope here | quota owner |
| 6 | **37 pre-existing unit-test failures** keep the job red independently | respective owners |

### Recommended follow-up (not performed)

A separately authorized test that drives `reserve_quota()` against a real Redis via the
existing `_bypass_dev_mode` seam. It must first inspect the current
`SharedRedisManager`, the `_REDIS_SINGLETON_SEAMS` restore fixture and
`SubscriptionEngine.reserve_quota` — all three have moved during this phase series, and the
webhook fixture is the working precedent to copy.

---

## 7. State at exit

| Check | Value |
|---|---|
| `.git/index` SHA-256 | `6D965C8EAFDD23EECCEA41A6BFF25DF5D9F1EE9FC64DFA17C29C965210458613` — **byte-identical to entry** |
| Staged entries | **22** — untouched |
| `HEAD` | `074b772a` — unmoved |
| Current branch | `main` — never left |
| Unstaged | 100 — unchanged by this task |
| PR #8 | **OPEN, not merged** |

Boundaries honoured: no merge; no production database or Redis access; no infrastructure
installed or started (the unreachable-endpoint test used an unused loopback port); no exchange
credentials or venue orders; no live-trading enablement; no unrelated source change; launch
scorecard and gate registers untouched.

Artefacts added, all under `staging/`: this report, `a7_manifest_check.py`,
`a7_prepare_commit.py`, `a7_pr_body.md`, `a7_poll_run.py`, `a7_fetch_job_log.py`,
`a7_analyse_failures.py`, `a7_error_lines.py`, `a7_show_block.py`, `a7_billing_committed.py`,
plus the captured logs and JSON.
