# Phase 11B-A20 — Quota Refusal Semantics and Async Redis Lifecycle

> **Output path.** The prescribed destination
> `staging/PHASE11B_A20_DEFECT_REMEDIATION_READINESS.md` was **absent at preflight and
> occupied by the time I came to write** (30,461 B, `10-10 21:20:42`, another session).
> Per the brief I did not overwrite it and used this explicitly reported alternative:
> **`staging/PHASE11B_A20Q_QUOTA_REFUSAL_AND_LOOP_LIFECYCLE.md`** (confirmed absent).

**Outcome.** Workstream A (quota refusal semantics) is **FIXED and tested** in a file with
clear sole ownership. Workstream B (async Redis lifecycle) is **ROOT-CAUSED but NOT
CHANGED** — every candidate target belongs to another workstream, so it stops at a
coordination request, as instructed.

**A second, more serious defect was found in the same function and fixed: an unmeasurable
quota was ADMITTING.** §3.2.

| Status | |
|---|---|
| Quota correctness | **FIXED locally, in-process tested.** Real-Redis assertion **NOT re-verified** (no local server) |
| Async lifecycle | **ROOT-CAUSED. NOT FIXED.** Blocked on cross-workstream ownership |
| D5′ acceptance | **NOT PASSED, NOT CLAIMED** — requires a CI run, which is unauthorized |
| QUOTA-01 | **OPEN** |
| TENANT-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Preflight and ownership

| Item | Entry | Exit |
|---|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | **unchanged** |
| vs `origin/main` | `0 / 0` | `0 / 0` |
| Staged paths | **22** | **22** |
| Canonical staged digest | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **unchanged** |
| Unstaged paths | 100 | 101 (+1 = my `subscription_engine.py`) |
| Untracked | 3,477 | +2 (my test file, this report) |
| Worktrees | 1 | **1** |

**Ownership decided the whole shape of this phase:**

| Target | Status | Owner | Action |
|---|---|---|---|
| `backend_app/core/subscription_engine.py` | **clean** | none — no concurrent work | **EDITED** (Workstream A) |
| `backend_app/core/cache/redis_manager.py` | ` M` **+185 / −5** | F5 fail-soft-proxy workstream | **not touched** |
| `tests/conftest.py` | ` M` **+172 / −0** | shared, explicitly protected by the brief | **not touched** |
| `pytest.ini` | ` M` **+13 / −0** | concurrent | **not touched** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | webhook owner | **not touched** |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` | D5′ (committed on PR #6) | **not touched** — new tests went in a separate file so the D5′ manifest is unchanged |
| `backend_app/routers/exchange.py`, `core/subscription_dependencies.py` | `MM` / ` M` | excluded by the brief | **not touched** |

All five protected files still read at their entry numstats (`185/5`, `172/0`, `13/0`,
`154/14`, `253/0`) — verified at exit.

---

## 2. Evidence verified against current source

The A19 CI report was treated as a claim to check, not as fact. Both defects reproduce from
current source, and the quota one is reproduced by executable tests added here.

---

## 3. Workstream A — quota refusal semantics

### 3.1 Defect 1 — a refusal reported the mid-contention transient

`SubscriptionEngine.reserve_quota` (now at `subscription_engine.py:959`) did:

```python
new_usage = await redis_manager.incrby(cache_key, amount)
...
if new_usage > effective:
    reverted = await redis_manager.decrby(cache_key, amount)
    return False, reverted, effective          # <-- the defect
```

**Root cause.** `DECRBY` answers with the meter as it stood *after this caller's*
decrement. Concurrent refusals have already incremented and have not yet reverted theirs,
so `reverted` is a transient that can sit far above the limit. With 50 racers at a limit of
10, the counter peaks near 46; a caller that reverts early reads ~35.

**Enforcement was never wrong.** In CI the assertions that ran *before* the failure all
passed: `len(admitted) == 10`, `len(refused) == 40`, `get_quota_usage() == 10`, raw key
`== 10`. Only the figure *returned to the caller* was wrong. The docstring claimed "the
reverted value is reported rather than the inflated one" — a guarantee the code did not
deliver under concurrency. That docstring is now corrected rather than left aspirational.

**Why it matters beyond the test.** `backend_app/backend/strategy_service.py:1855` renders
the value straight to the user:

```python
raise ValueError(f"Quota exceeded for bots: {current_usage}/{limit}. Upgrade your plan…")
```

So a refused customer could be told "35/10". All three production callers
(`routers/strategies.py:2077`, `backend/strategy_service.py:1852`,
`core/usage_ledger.py:494`) consume the value only for refusal messaging — none does
arithmetic on it — so bounding it is safe and strictly improves the message.

**Fix (smallest correct).** A refused caller consumed nothing, so the only figure true for
it is the capacity it was denied. Clamp into `[0, effective]`; log the transient so
contention stays observable rather than being silently discarded:

```python
settled = min(max(reverted, 0), effective)
if settled != reverted:
    logger.info("Monthly meter for %s read %s mid-contention; reporting the settled "
                "figure %s/%s to user %s", resource, reverted, settled, effective, user_id)
return False, settled, effective
```

This is a bound on the reported value, **not** a re-read of the counter — it introduces no
new race, which the brief explicitly warned against.

### 3.2 Defect 2 — an unmeasurable quota ADMITTED (new finding, not in A19)

The brief asked me to *preserve* "fail-closed semantics when Redis is unavailable". **That
semantic did not exist.**

`redis_manager.incrby` is a fail-soft proxy (`cache/redis_manager.py:927`): on any
exception it logs a warning and **returns `0`**. That `0` flowed into
`if new_usage > effective`, false for every positive limit, so the reservation was
**admitted**. A cache fault silently disabled the quota at exactly the moment it could no
longer be measured.

No test caught this because on the in-memory substrate `incrby` always succeeds — the fault
is only reachable through the proxy's exception path.

**Fix.** A healthy `INCRBY` of a positive amount can never return less than that amount:
the meter only grows through this branch and every revert is matched to its own increment,
so the key never holds a negative figure. A smaller answer therefore means the increment
did not land:

```python
if amount > 0 and new_usage < amount:
    logger.error("Monthly meter for %s could not be incremented (INCRBY returned %s for "
                 "amount %s); refusing user %s plan %s because the quota is unmeasurable",
                 resource, new_usage, amount, user_id, plan_key)
    return False, 0, effective
```

Guarded on `amount > 0` so a legitimate `amount=0` call keeps its meaning. Residual: a key
left negative by some earlier bug would trip this and refuse — which is the fail-closed
direction, and is logged at `ERROR`.

### 3.3 Preserved, each asserted by a test

Hard-limit enforcement · exact-limit concurrency · per-user/resource/period isolation ·
zero-allowance writes no meter · unlimited-plan admission · the public
`Tuple[bool, int, int]` return shape — **unchanged**. No plan, limit, schema, or
exchange-slot logic was touched.

### 3.4 Exact file scope

| Path | Change |
|---|---|
| `backend_app/core/subscription_engine.py` | `reserve_quota` only — the unmeasurable guard, the refusal clamp + transient log, and a corrected docstring. **No other function touched.** |
| `tests/test_a20_quota_refusal_semantics.py` | **new**, 10 tests |

New tests were put in a separate file deliberately: `test_quota01_metered_reservation_is_hard.py`
is part of the D5′ manifest committed on PR #6, and editing it would alter that candidate.

---

## 4. Workstream B — async Redis lifecycle. ROOT-CAUSED, NOT CHANGED.

### 4.1 The exact cause, from the CI artifact

The A19 report said only that the manager "refused a write". The captured log names the
reason:

```
WARNING backend_app.core.cache.redis_manager:redis_manager.py:660
  Cache SET failed for 'aerora:test:keyspace-probe:app-wrote:3effca46…': Event loop is closed
```

Four occurrences, one per errored test. **Which object survives:** `SharedRedisManager`
caches its singleton in `__new__` (`cache/redis_manager.py:471-480`), and with it the async
client and connection pool bound to the event loop that first built them. pytest-asyncio
runs in `STRICT` mode with `asyncio_default_fixture_loop_scope` unset, so each test gets a
**fresh function-scoped loop** and the previous one is closed. Every call after the first
test therefore touches a pool attached to a dead loop, the fail-soft proxy swallows
`RuntimeError: Event loop is closed`, `set` returns `False`, and the webhook fixture's
keyspace probe correctly refuses to proceed.

`tests/conftest.py:82-113` already snapshots and restores `_REDIS_SINGLETON_SEAMS`, but
that restores *references*; it does not close or rebuild the loop-bound client. This is the
same lesson the file itself records at line 288 about the rate limiter — "the problem is
WHICH OBJECT it reset".

### 4.2 Classification — the four cases the brief asked to distinguish

| Case | Verdict |
|---|---|
| Redis unavailable | **NO** — service container started, `PING` answered, gate `state=run`, `reason=(none)` |
| Actual keyspace mismatch | **NO** — the probe says so explicitly: *"This is not a keyspace mismatch — the write never landed anywhere."* |
| Manager refused a write | **Symptom, not cause** — `set` returned `False` because the proxy swallowed the loop error |
| **Event-loop lifecycle failure** | **YES — this is it** |

### 4.3 Why no code was written, and what is requested

All three candidate targets carry concurrent work (§1). The brief forbids editing
`tests/conftest.py` before its owner coordinates, and §5 forbids competing edits generally.
Three options, for the owners to choose:

| # | Location | Change | Trade-off |
|---|---|---|---|
| **B-1** | `tests/conftest.py` (owner: shared/conftest) | Extend the existing `_REDIS_SINGLETON_SEAMS` autouse fixture to **close and clear** the manager's cached async client at teardown, so the next test rebuilds on its own loop | Test-only, smallest blast radius. **Recommended first.** Needs the conftest owner |
| **B-2** | `backend_app/core/cache/redis_manager.py` (owner: F5 workstream) | On use, detect a client whose loop is closed or foreign and rebuild it | Most robust, and helps production where a worker restarts its loop. Touches the file the F5 fail-soft work is in |
| **B-3** | `pytest.ini` (concurrent) | Set `asyncio_default_fixture_loop_scope = session` | One line, but the brief warns against a blanket session loop without verifying every existing async test. **Not recommended without that verification** |

**Explicitly not proposed:** widening fixture lifetimes globally, or making the proxy
re-raise. The proxy's fail-soft behaviour is F5's open question and must not be changed
here as a side effect.

**One correction to the task framing.** The objective describes the webhook tests as
erroring "during setup", which is right, but calls it a *shared async Redis connection
reused after its creating event loop closes* — accurate — and the remedy is therefore
**not** in the webhook test or the keyspace probe. Those are working: the probe is the only
reason this defect is visible at all rather than passing vacuously.

---

## 5. Tests executed — exact commands and results

**No real Redis was used. `AERORA_TEST_REDIS_URL` is unset and no local server exists, so
no real-Redis assertion was reproduced locally. The 3 real-Redis skips below are NOT
passes.**

| # | Command | Exit | Result | Real Redis? |
|---|---|---|---|---|
| 1 | `python -m py_compile backend_app/core/subscription_engine.py` | **0** | clean | n/a |
| 2 | `pytest tests/test_a20_quota_refusal_semantics.py -p no:randomly -q -rfE` | **0** | **10 passed** | **no** |
| 3 | `pytest tests/test_quota01_metered_reservation_is_hard.py -p no:randomly -q` | **0** | **6 passed, 3 skipped** (3 real-Redis, opt-in) | **no** |
| 4 | `pytest` over 9 files — A20, quota01 ×4, usage-ledger, subscription — `-rfE --timeout=900` | **0** | **177 passed, 6 skipped, 0 failed, 0 errors** in 90.25 s | **no** |

Run 2 had one intermediate failure, in **my test code**, not the fix: two assertions used
`record.message % record.args`, which raised `TypeError`. The captured log already showed
the fix behaving correctly —
`Monthly meter for backtests read 35 mid-contention; reporting the settled figure 10/10` —
and the assertions were corrected to `record.getMessage()`.

### 5.1 What run 2 proves, mapped to the brief's required tests

| Required proof | Covered by | Substrate |
|---|---|---|
| Redis errors do not turn a refusal into an admission | `test_a_failed_increment_refuses_instead_of_admitting`, `test_a_negative_increment_result_also_refuses` | in-process fault injection |
| Rollback failures observable and handled safely | `test_a_rollback_that_itself_fails_is_handled_safely`, plus `caplog` assertions on both log lines | in-process |
| Every refusal returns usage within the contract | `test_a_refusal_never_reports_more_than_the_limit` (the exact 35-vs-10 case), `test_a_refusal_never_reports_a_negative_usage`, `test_an_uncontended_refusal_reports_the_limit_exactly` | in-process |
| Zero allowance creates no meter | `test_a_zero_allowance_refuses_and_writes_no_meter` | in-process |
| Unlimited plan unaffected | `test_an_unlimited_plan_is_unaffected` | in-process |
| Admission path unchanged (incl. first-write TTL) | `test_an_admission_still_reports_its_own_increment` | in-process |
| `amount=0` not mistaken for a fault | `test_a_zero_amount_is_not_mistaken_for_a_failure` | in-process |
| **Limit 10, 50 racers admit exactly 10; reader and raw meter both 10** | `test_quota01_metered_reservation_is_hard.py` — in-memory variant **passed**; real-Redis variant **SKIPPED** | **in-memory only** |

The last row is the gap: the concurrency property is proven in-memory and **not**
re-verified against real Redis in this phase.

---

## 6. Cross-workstream approval still needed

| # | Request | Owner |
|---|---|---|
| **B-1** | Extend the conftest Redis-seam fixture to close/clear the loop-bound client | `tests/conftest.py` owner |
| **B-2** | *(alternative)* closed/foreign-loop detection in the manager | F5 / `cache/redis_manager.py` owner |
| **B-3** | *(alternative, not recommended unsupported)* `asyncio_default_fixture_loop_scope` | `pytest.ini` owner |
| **C-1** | Authorization to trigger CI, to re-verify the real-Redis assertions on PR #6 | release owner — **not held; no CI was triggered** |
| **C-2** | Whether this quota fix lands on PR #6 or as its own change. It is **not** in the D5′ three-file manifest | coordinator |

---

## 7. Preservation — final measurements

| Check | Result |
|---|---|
| Canonical staged digest | `2e994018…2767152` — **unchanged** |
| Staged paths | **22 → 22** |
| HEAD / branch / worktrees | `074b772a` / `main` / 1 — **unchanged** |
| `tests/conftest.py` | **not modified** — still `+172 / −0` |
| `cache/redis_manager.py` | **not modified** — still `+185 / −5` |
| `pytest.ini` | **not modified** — still `+13 / −0` |
| `exchange.py` / `subscription_dependencies.py` | **not modified** — `+154 / −14`, `+253 / −0` |
| Exchange-slot tests | **not removed, skipped, weakened or rewritten** |
| Staged / committed / pushed / reset / rebased / branch-switched | **none** |
| PR #6 / #7 / #8 | **untouched** |
| CI | **not triggered or rerun** |
| Schema / migrations / credentials / Redis deployment / live-trading controls | **unchanged** |
| Local database / Redis / venue calls | **none** |
| Another session's report overwritten | **no** — alternative path used |

---

## 8. Status, separately

| Area | Status |
|---|---|
| **Quota correctness** | Two defects fixed in `reserve_quota`: the refusal transient and the fail-open unmeasurable quota. **10/10 focused + 177 regression tests pass.** The real-Redis concurrency assertion that failed in CI is **not re-verified locally** — no server, 3 skips, and a skip is not a pass |
| **Async lifecycle** | **NOT FIXED.** Cause established as the `SharedRedisManager` singleton's loop-bound client versus function-scoped pytest-asyncio loops. Blocked on B-1/B-2/B-3 ownership |
| **D5′ acceptance** | **NOT PASSED and NOT CLAIMED.** 5 of 6 webhook tests still error at setup until Workstream B lands. Focused local tests cannot establish PR #6 acceptance |
| **QUOTA-01** | **OPEN.** Production-path quota enforcement now has a corrected refusal contract but no real-Redis re-verification; exchange-slot concurrency and release safety remain unproven; order-rate, daily-trade and active-order ceilings remain unimplemented |
| **TENANT-01** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

**Next permitted action:** obtain B-1 from the `tests/conftest.py` owner. It is the smallest
change that lets all six webhook tests execute their assertions, and until it lands the
webhook half of D5′ cannot be evidenced either way.

---

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · D5′ acceptance **NOT CLAIMED** · exchange-slot
safety **UNPROVEN** · live trading **OFF / NOT CERTIFIED** · no real Redis used · no CI
triggered · no commit, push, or PR change · 22-path staged work preserved
