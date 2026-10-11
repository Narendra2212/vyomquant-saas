# Phase 11B-A20 — Quota Refusal Semantics and Async Redis Lifecycle: Remediation Readiness

**Date:** 2026-10-10
**Destination check:** `staging/PHASE11B_A20_DEFECT_REMEDIATION_READINESS.md` verified **ABSENT**
before writing. No existing report modified.
**Nothing was implemented.** No source, test, fixture, workflow or artifact changed other than this
report and its `p11b20_*` evidence files.

---

## HEADLINE

Both defects are root-caused in source. Two findings go beyond A19's scope:

1. **The blocking lane completed, and the node-ID comparison is now clean.** All **37** baseline
   failures recurred by exact node ID across the same 8 files; **0 disappeared**; and **all 22 new
   failures are inside the three D5′ files**. No unrelated test regressed.
2. **A more serious adjacent defect, found by reading the source: a Redis error on `INCRBY`
   admits the reservation.** `SharedRedisManager.incrby` returns `0` on any exception, `0 > limit`
   is false, so `reserve_quota` returns `(True, 0, limit)`. **The hard limit fails open on cache
   error.** This is **INVESTIGATED, NOT REPRODUCED** — I have not executed it.
3. **The inflated refusal figure reaches end users verbatim.**
   `backend_app/routers/strategies.py:2081` renders it into an HTTP 403 body:
   `f"Quota exceeded for {resource}: {current_usage}/{limit}."` — so A19's observation means a
   user can be told **"Quota exceeded for bots: 36/10"**.
4. **The async-lifecycle staleness is sticky by design.** `_connected` is set once and is never
   invalidated by a runtime failure, and `get_redis_manager()` short-circuits on it, so one
   closed-loop error disables Redis for the rest of the process.

**Risk ranking: Defect 2 (async lifecycle) is the riskier change; Defect 1 (quota) can proceed
independently and is the smaller, safer fix.** Details in §6.

---

## 1. Repository invariants

| Invariant | At entry | At exit |
|---|---|---|
| Branch / local `main` HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical |
| Shared index content digest | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | identical |
| Staged paths | 22 | 22 |
| Worktrees | one: `C:/aerora_quant_backend_updated_final1 [main]` | unchanged |
| Exchange-slot test blob | `2d7ccbbb04b3af04675b365809d89b4429db4888` | unchanged |
| PR #6 / #7 / #8 | untouched | untouched |

### Target-file status and ownership boundaries

| File | Status | Owner / constraint |
|---|---|---|
| `backend_app/core/subscription_engine.py` | **clean** (= `main`) | No concurrent edit. **Clean ≠ authorized**; Defect 1's fix lands here and needs approval |
| `backend_app/backend/redis_manager.py` | **clean** (= `main`) | Same. Holds the pool lifecycle |
| `backend_app/core/cache/redis_manager.py` | ` M` +≈180 lines | **Other workstream** — read-only here |
| `tests/conftest.py` | ` M` | **Other workstream** — read-only here. Holds `_REDIS_SINGLETON_SEAMS` and the autouse restore fixture |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` | D5′, on PR #6 |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | D5′, on PR #6 |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | D5′ — **not touched, not weakened** |

---

## 2. Defect 1 — quota refusal semantics

### 2a. Source locations

`backend_app/core/subscription_engine.py`

```
 944  _meter_key()                      ->  quota:{user}:{resource}:{YYYY-MM}
 956  _METER_TTL_SECONDS = 60*60*24*40
 958  reserve_quota() -> Tuple[bool, int, int]   # (allowed, usage_after_the_decision, limit)
 975  """... The reverted value is reported rather than the inflated one, so a refusal
      never tells a user they have consumed more than they have."""      <-- STATED INTENT
 992  new_usage = await redis_manager.incrby(cache_key, amount)
 993  if new_usage == amount:
 994      await redis_manager.expire(cache_key, cls._METER_TTL_SECONDS)
 996  if new_usage > effective:
 997      reverted = await redis_manager.decrby(cache_key, amount)
1006      return False, reverted, effective                              <-- THE DEFECT
```

`backend_app/core/cache/redis_manager.py` (the proxies it depends on)

```
 927  async def incrby(...):  try: return await client.incrby(...)
 933                          except Exception as e: logger.warning(...)
 935                          return 0                                   <-- FAIL-OPEN SOURCE
 937  async def decrby(...):  try: return await client.decrby(...)
 943                          except Exception as e: logger.warning(...)
 945                          return 0
```

### 2b. Existing behaviour vs intended

**Intended** (docstring, line 975): a refusal reports the post-revert usage and never overstates.

**Actual:** `reverted` is `DECRBY`'s return value — the counter **after this caller's own
decrement but before the other racers' decrements**. With 50 racers at limit 10 the interleaving
is 50 `INCRBY` then 40 `DECRBY`; a racer whose decrement lands early observes a value drawn from
that middle. A19 measured **36**.

> **Correction to A19.** A19 described this as "the transient pre-revert count". That is
> imprecise: the code returns `reverted` (the `DECRBY` result), not `new_usage`. The value is
> post-*this*-caller's revert and pre-*other*-callers' reverts. The distinction matters, because
> it rules out the obvious "return `reverted` instead of `new_usage`" fix — that is already what
> the code does.

**What is NOT broken** — established by the `>` marker in A19's traceback, which identifies the
single raising line; every assertion above it passed:

| Property | Status |
|---|---|
| Exact-limit admission (10 of 50) | **correct** |
| Refusal count (40) | **correct** |
| `get_quota_usage` after settling == 10 | **correct** |
| Raw meter key after settling == 10 | **correct** |
| `effective` returned to refused callers | **correct** |
| Usage value returned to refused callers | **WRONG — 36** |

### 2c. Two further failure modes of the same lines

| # | Condition | Behaviour | Severity | Status |
|---|---|---|---|---|
| 1b | `INCRBY` raises (Redis down, closed loop, timeout) | proxy returns `0` → `0 == amount` false → `0 > effective` false → **`return True, 0, effective`**. **The reservation is GRANTED and usage reported as 0.** The hard limit fails **open** on cache error | **HIGH** | **INVESTIGATED, NOT REPRODUCED** |
| 1c | `INCRBY` succeeds, `DECRBY` raises | proxy returns `0` → `return False, 0, effective`. Refusal reports **zero** usage while the counter keeps a **leaked** increment permanently (until the 40-day TTL) | MEDIUM | **INVESTIGATED, NOT REPRODUCED** |
| 1d | Transient overshoot visible to readers | between the `INCRBY` storm and the reverts the meter legitimately holds up to 50 against a limit of 10; any concurrent `get_quota_usage` reads that | MEDIUM | **INVESTIGATED** — A19 confirmed the *settled* value only |

1b is more serious than the reported defect: a quota that fails open under cache failure is a
revenue and capacity control that silently stops controlling.

### 2d. Blast radius — the value is user-visible

| Caller | Use |
|---|---|
| `backend_app/routers/strategies.py:2077` | `raise HTTPException(403, detail=f"Quota exceeded for {Resource.BOTS.value}: {current_usage}/{limit}. Upgrade your plan…")` — **rendered to the API consumer** |
| `backend_app/backend/strategy_service.py:1852` | `raise ValueError(f"Quota exceeded for bots: {current_usage}/{limit}. …")` |
| `backend_app/core/usage_ledger.py:494` | returns `(allowed, current, effective)` onward at line 513 |

### 2e. Candidate corrections

| Option | Mechanism | Verdict |
|---|---|---|
| **1. Clamp the reported figure** — `return False, min(reverted, effective), effective` | One expression. No extra round trip, no new race. Bounded by construction, so it can never overstate | **RECOMMENDED as the immediate fix.** Smallest possible change; satisfies the docstring's promise; preserves every other return semantic |
| **2. Re-read via `get_quota_usage` after the revert** | Separate non-atomic `GET` | **REJECTED.** Adds a round trip on the refusal path and is still racy — it can read mid-storm and return >limit again, or read after a release and under-report. Explicitly the "separate, non-atomic read" the brief warns about |
| **3. Server-side atomic script** (`EVAL`): read, compare, conditionally `INCRBY`, conditionally `EXPIRE`, return `(allowed, usage)` in one round trip | Removes the compensating increment/revert entirely, so 1c and 1d disappear and the refusal value is authoritative by construction | **RECOMMENDED as the structural fix, second step.** The surface exists: `SharedRedisManager.eval` (L656) and `eval_lua` (L635), and `MockRedisClient` implements both. Larger change; needs the mock's script semantics verified against the real server, so it should not be bundled with the one-line fix |
| **4. Fail closed on proxy error** — distinguish "cache returned a value" from "cache failed" instead of collapsing both to `0` | Addresses 1b and 1c at the source | **RECOMMENDED but NOT in `subscription_engine.py`.** The `return 0` lines are in `core/cache/redis_manager.py`, which another workstream currently holds. Needs coordination, and changing the proxy contract affects every caller — it must not be done as a side effect of a quota fix |

Options 1 and 3 are sequential, not alternatives: ship 1 to stop the user-visible wrong number,
then 3 to remove the revert design. Option 4 is a separate ticket against a file I may not edit.

### 2f. Regression matrix — Defect 1

| # | Test | Asserts | Substrate |
|---|---|---|---|
| Q1 | sequential refusal payload | one caller past the limit reports `usage == effective`, never above | mock + real |
| Q2 | **concurrent refusal payload** | 50 racers at limit 10 → **every** refused tuple satisfies `0 <= usage <= limit` (the A19 reproduction, as the regression guard) | **real Redis** |
| Q3 | exact-limit admission unchanged | exactly 10 admitted, 40 refused | real Redis |
| Q4 | production reader agreement | `get_quota_usage` and the raw key both == limit after settling | real Redis |
| Q5 | zero allowance | `effective <= 0` → refuse, **no key written** (`EXISTS == 0`) | real Redis |
| Q6 | unlimited plan | `is_unlimited` → always admit, usage increments | mock |
| Q7 | per-user isolation | two users' meters independent | mock |
| Q8 | post-exhaustion retry | refusal is stable and does not erode the meter | real Redis |
| Q9 | **`INCRBY` error fails CLOSED** | proxy raising → reservation **refused**, not granted (covers 1b) | injected fault |
| Q10 | **`DECRBY` error does not report 0** | refusal reports a bounded figure, and the leak is logged (covers 1c) | injected fault |

Q1–Q8 already exist in `tests/test_quota01_metered_reservation_is_hard.py` (9 tests; Q2 is the
currently failing one). **Q9 and Q10 are new and do not exist anywhere.**

---

## 3. Defect 2 — asynchronous Redis lifecycle

### 3a. Source locations

`backend_app/backend/redis_manager.py`

```
  99  _instance: Optional['RedisManager'] = None        # process-wide singleton
 109  def __new__(cls): ...                             # caches _instance
 122  self._connected: bool = False
 133  async def initialize(self) -> bool:
 140      cache  = await aioredis.from_url(get_redis_url(REDIS_DB_CACHE),  max_connections=50)
 148      queue  = await aioredis.from_url(...)
 156      events = await aioredis.from_url(...)          # pools BOUND TO THE CREATING LOOP
 171      self._connected = True
 180      self._connected = False                        # ONLY on initialize() failure
 192  async def try_reconnect: if self._connected: return True      # refuses to act
 523  _redis_manager: Optional[RedisManager] = None     # module-level global
 536  async def get_redis_manager():
 548      if _redis_manager is not None and _redis_manager._connected:
 549          return _redis_manager                      # STICKY FAST PATH
```

`backend_app/core/cache/redis_manager.py`

```
 463  class SharedRedisManager:  _instance (L471), _redis_manager (L472)
 489  async def _ensure_manager():  mgr = await get_redis_manager(); self._redis_manager = mgr
1488  redis_manager = SharedRedisManager()               # one instance for the process
      occurrences of `_connected`: NONE                  # proxies never invalidate it
```

`tests/conftest.py` — pytest-asyncio has **no** `asyncio_mode` or loop-scope setting anywhere in
`pytest.ini`, `pyproject.toml`, `tox.ini` or `setup.cfg`, so the default applies: **strict mode,
one function-scoped event loop per test**. The autouse `_restore_shared_redis_singleton` (L90-115)
snapshots and restores the four **seams** in `_REDIS_SINGLETON_SEAMS` — and nothing else.
Verified absent from conftest: `aclose`, `close()`, `disconnect`, `_cache = None`,
`_connected = False`, `initialize(`. **No pool is ever closed or recreated between tests.**

### 3b. Mechanism, end to end

1. The first test that reaches a real cache triggers `initialize()`; three `aioredis` pools are
   created **on that test's event loop** and `_connected = True`.
2. pytest-asyncio closes that loop at the end of that test.
3. Every later test gets a fresh loop but the **same** singleton and the **same** pools. The first
   command raises `RuntimeError: Event loop is closed`.
4. The core proxy catches it, logs a warning, and returns the neutral value (`False` for `set`,
   `None` for `get`, `0` for `incrby`/`decrby`).
5. Because the proxy never touches `_connected`, it stays `True`; `get_redis_manager()`'s fast
   path (L548) returns the stale manager; `try_reconnect` (L192) returns early. **The failure is
   permanent for the remainder of the process.**

### 3c. Evidence — and the decisive comparison

| Observation | Source |
|---|---|
| `Cache SET failed for 'aerora:test:keyspace-probe:app-wrote:…': Event loop is closed` ×4, at `redis_manager.py:660` | A19 artifact `webhook.log` L100, 183, 266, 349 |
| The probe reported a **refused write**, not a keyspace mismatch; `KEYSPACE SPLIT` appears nowhere | A19 §5b |
| **Isolated** run (diagnostics job, file alone): 2 passed / 4 errors — test 2 succeeded | `webhook.xml` |
| **Full suite** (blocking lane): **1 passed / 5 errors** — test 2 **also** errored | `p11b20_nodeids.txt` |

That last pair is the clincher. In isolation, the file's own first cache-using test creates the
pool on its own live loop and passes. In the full suite, ~12 000 earlier tests have already
created and closed the pool's loop, so even the first webhook test fails. **Order-dependent,
loop-scoped staleness — not a keyspace, credential or provisioning problem.** Redis itself was
provisioned and reachable: the gate recorded `state=run` and the client resolved to
`Connection(host=localhost,port=6379,db=0)`.

### 3d. Production relevance, stated separately from the test symptom

With a single long-lived loop the closed-loop trigger does not occur in production. But the
**stickiness is not test-specific**: any runtime connection loss that surfaces as an exception
inside a proxy — a server restart, a failover, a network blip — leaves `_connected == True`, so
neither `get_redis_manager()` nor `try_reconnect()` will ever rebuild the pool. The documented
reconnection machinery is unreachable from the state that most needs it. **This is a production
robustness gap in its own right**, and it is the reason I rank this workstream as the riskier of
the two: the correct fix touches production reconnection behaviour, not just fixtures.

### 3e. Candidate corrections

| Option | Mechanism | Verdict |
|---|---|---|
| **1. Invalidate `_connected` on a runtime failure** so the existing reconnect path can run | The proxies (or a small helper they call) mark the manager unhealthy when a command raises; `get_redis_manager()` then rebuilds the pool on the current loop | **RECOMMENDED — correct for production as well as tests.** It makes the existing `try_reconnect` / cooldown machinery actually reachable. Touches `core/cache/redis_manager.py` (other workstream) and/or `backend/redis_manager.py` |
| **2. Bind pool identity to the running loop** — record the loop that created the pools and rebuild when `asyncio.get_running_loop()` differs | Directly targets the root cause and is inert in production (the loop never changes) | **RECOMMENDED as a complement to 1**, and the narrower of the two in behavioural terms. Still `backend/redis_manager.py` |
| **3. Session-scoped event loop for the suite** | One loop for all tests, so the pool never outlives it | **REJECTED as a primary fix.** It is a test-only workaround that leaves the production stickiness in place, and it changes the loop contract for ~12 000 tests — including the `asyncio.run` / `get_event_loop` guard that `conftest.py` L124-193 exists to manage. Not safe to change without inspecting every consumer, which the brief explicitly warns about |
| **4. Explicit manager reset between tests** — add pool teardown to an autouse fixture | Closes and clears the pool per test | **REJECTED as primary, acceptable as a stopgap.** It is in `tests/conftest.py`, which another workstream holds; it would reconnect per test (slower); and like option 3 it hides a production defect behind test scaffolding |
| **5. Narrow the proxies' `except Exception`** so a closed loop is distinguishable from a cache miss | Addresses the diagnosis gap | **RECOMMENDED as a separate ticket.** Related to Defect 1's option 4 — the same `return 0` / `return False` collapse underlies both defects |

Options 1 + 2 together are the least invasive correct fix: they repair production reconnection and
make the test symptom disappear as a consequence, without touching `conftest.py` or the loop scope.

### 3f. Regression matrix — Defect 2

| # | Test | Asserts | Substrate |
|---|---|---|---|
| R1 | **each webhook test reaches its assertions** | all six execute; zero setup errors; zero skips | real Redis, **full suite** (the isolated run already passes test 2, so isolation cannot prove this) |
| R2 | manager `get`/`set` work on a live connection after a prior loop closed | write then read through `redis_manager` across two successive `asyncio.run` loops | real Redis |
| R3 | no closed-loop client is silently reused | a command after loop replacement either succeeds on a rebuilt pool or raises — never returns a neutral value | real Redis + fault injection |
| R4 | dedup keyspace stays consistent | the bidirectional sentinel probe passes in both directions (already implemented; currently blocked by R3) | real Redis |
| R5 | **Redis failure is distinguishable from a cache miss or a duplicate event** | a forced command failure does not present as "key absent" / "not a duplicate" | fault injection |
| R6 | `_connected` is invalidated on runtime failure | after a forced failure, `get_redis_manager()` attempts a rebuild rather than returning the stale manager | unit, no server |

R4's machinery exists in `tests/test_webhook_duplicate_delivery_real_redis.py`. **R1, R2, R3, R5
and R6 do not exist.** R5 is the one that would have caught A9's fail-open webhook idempotency.

---

## 4. Decision record A — quota remediation

| Field | Content |
|---|---|
| **Root cause** | `subscription_engine.py:997,1006` — `DECRBY`'s return value is reported as the authoritative post-revert usage, but it is an intermediate of the concurrent interleaving |
| **Contract** | `reserve_quota -> (allowed: bool, usage_after_the_decision: int, limit: int)`; three production callers unpack it; two render `usage` into a user-facing message |
| **Recommended approach** | Step 1: clamp — `return False, min(reverted, effective), effective`. Step 2 (separate): replace increment-then-revert with one atomic `EVAL`, removing 1c and 1d |
| **Regression tests** | Q1–Q10 (§2f); Q2 is the existing A19 reproduction; Q9/Q10 are new |
| **Files to modify** | Step 1: `backend_app/core/subscription_engine.py` (clean) + `tests/test_quota01_metered_reservation_is_hard.py` (D5′, on PR #6). Step 2 adds nothing new. Option 4 would touch `backend_app/core/cache/redis_manager.py` (**other workstream**) |
| **Owner approval required** | `subscription_engine.py` is clean and unowned, but **clean is not authorization** — explicit approval needed. No coordination required for step 1 if the proxy contract is left alone |

## 5. Decision record B — async Redis remediation

| Field | Content |
|---|---|
| **Root cause** | Pools are created once per process on the creating loop (`backend/redis_manager.py:140-156`) and `_connected` (L171) is invalidated **only** by an `initialize()` failure (L180). The core proxies never touch it (zero occurrences). `get_redis_manager()`'s fast path (L548) and `try_reconnect` (L192) both short-circuit on it, so a runtime failure is permanent |
| **Correct lifecycle ownership** | The manager must own pool validity, not a boolean set once. Health should be derived from the pool's usability on the **current** loop, with the existing debounce/cooldown governing rebuild attempts |
| **Recommended approach** | Options 1 + 2: invalidate on runtime failure, and rebuild when the running loop differs from the pool's creating loop. Leave loop scope and `conftest.py` alone |
| **Regression tests** | R1–R6 (§3f); R1 must run in the **full suite**, because isolation masks it |
| **Files to modify** | `backend_app/backend/redis_manager.py` (clean) primarily; a minimal hook in `backend_app/core/cache/redis_manager.py` (**other workstream**) if invalidation is raised from the proxies |
| **Coordination required** | **Yes.** `tests/conftest.py` is held by another workstream — and the recommendation is specifically *not* to change it, which should be stated to that owner so they do not implement option 3 or 4 in parallel. If proxy invalidation is chosen, the `core/cache/redis_manager.py` owner must agree |

### 6. Risk ranking and independence

| | Workstream | Risk | Can proceed independently? |
|---|---|---|---|
| 1 | **Quota, step 1 (clamp)** | **LOW** — one expression in a clean file; bounded by construction; no new round trip; no race; no contract change | **YES** |
| 2 | Quota, step 2 (atomic script) | MEDIUM — replaces the reservation algorithm; needs the mock's `EVAL` semantics verified against the real server | Yes, after step 1 |
| 3 | **Async lifecycle (options 1+2)** | **HIGH** — changes production reconnection behaviour for every Redis consumer in the application, and the blast radius is the whole cache layer | **NO** — needs coordination with the `core/cache/redis_manager.py` owner, and the `conftest.py` owner informed |
| 4 | Proxy error contract (quota option 4 / async option 5) | HIGH — `return 0` / `return False` / `return None` on error is relied on implicitly by every caller | No — separate ticket, own analysis |

**Quota step 1 is the only item that is both low-risk and independent.** The async work is the
riskier of the two despite producing more test errors, because its correct fix is a production
behaviour change rather than a reporting change.

---

## 7. Baseline comparison — COMPLETE

The blocking lane finished during this phase: run **38061973739**, job **114242427268**,
conclusion **failure**, 661.56 s.

```
current  (f5b99e57): 61 failed, 12027 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed, 5 errors
baseline (6c5660a1): 39 failed, 12017 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed
```

Node-ID set comparison (full IDs, not counts):

| | Count |
|---|---|
| In **both** — established baseline failures | **37** |
| **New** in this run | **22** |
| **Gone** since baseline | **0** |

Baseline recurrences by file, unchanged from `p11b14_preexisting.txt`:
`test_no_credential_in_api_response_body.py` 13, `test_validation_sweep.py` 9,
`test_task_6_1_backtest_execute.py` 7, `test_strategy_status_publish.py` 4,
`test_task_8_4_runtime_readiness_gate.py` 1, `property/test_tenant_isolation_matrix.py` 1,
`test_ml_deployment_guards.py` 1, `test_task_33_2_rate_limit_keys.py` 1.
(37 parsed + 2 line-wrapped = 39, matching the baseline total.)

### Category separation

| Category | Count | Detail |
|---|---|---|
| Established baseline failures | **37** (+2 wrapped = 39) | identical node IDs, 0 drift |
| **Quota refusal-payload failure** | **1** | `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis` |
| **Webhook async-lifecycle errors** | **5** | all five cache-using webhook tests, ERROR at setup |
| **Expected exchange-slot failures (deferred guard)** | **21** | all `AttributeError` on the absent symbols, plus the `store_keys` assertion |
| Infrastructure failures | **0** | Redis was provisioned and reachable: gate `state=run`, `db=0` resolved, real commands executed |
| **Unclassified** | **0** | — |

Reconciliation: 39 baseline + 21 slot + 1 quota = **61 failed** ✓, and the 5 webhook errors are
counted separately as errors ✓. **Every failure in the run is accounted for, and no unrelated
test regressed.**

**Skips in the three D5′ files: 0.** The run's 51 skips are all elsewhere.

The non-blocking diagnostics job reported `success` because it sets `continue-on-error: true`
while its own JUnit recorded 26 failures and errors. **That green badge is not D5′ acceptance and
is not treated as such anywhere here.**

---

## 8. Investigated / reproduced / fixed / verified

| Finding | Investigated | Reproduced | Fixed | Verified |
|---|---|---|---|---|
| D1 — refusal reports an intermediate usage value | **yes** — source, line-level | **yes** — CI, usage 36 at limit 10, twice (isolated + full suite) | **no** | **no** |
| D1b — `INCRBY` error admits the reservation (fail-open) | **yes** — source: proxy `return 0` + the `>` comparison | **NO** | no | no |
| D1c — `DECRBY` error reports usage 0 and leaks an increment | **yes** — source | **NO** | no | no |
| D1d — meter transiently exceeds the limit, visible to readers | **yes** — source | partially: A19 confirmed the *settled* value only | no | no |
| D1e — the wrong figure is rendered into a 403 body | **yes** — `routers/strategies.py:2081` | not separately exercised | no | no |
| D2 — pools outlive their creating loop | **yes** — source, line-level | **yes** — 4 errors isolated, 5 full suite | **no** | **no** |
| D2b — `_connected` never invalidated at runtime, so recovery is unreachable | **yes** — source: 0 occurrences in the core proxies; L548/L192 gates | inferred from the permanence of the symptom; **not separately injected** | no | no |
| Baseline unchanged by node ID | **yes** | **yes** — 37 recurred, 0 gone | n/a | **yes** |
| Exchange-slot failures are the deferred guard | **yes** | **yes** — 21, all on absent symbols | no — deferred | n/a |

Nothing in this phase was fixed or verified. Two of the three highest-severity items
(D1b, D2b) are **source-derived and unreproduced**, and are labelled so rather than asserted.

---

## 9. Evidence matrix

| Claim | Evidence |
|---|---|
| Refusal returns an intermediate figure | `subscription_engine.py:997,1006`; A19 `metered.log` raising line `assert 0 <= usage <= limit`, `usage 36` |
| Intent was the opposite | `subscription_engine.py:975` docstring |
| Hard limit itself is correct | A19 traceback: every assertion above the `>` marker passed |
| Figure reaches users | `routers/strategies.py:2079-2082`; `strategy_service.py:1854` |
| `INCRBY`/`DECRBY` collapse errors to 0 | `core/cache/redis_manager.py:927-935, 937-945` |
| Pools bound to the creating loop | `backend/redis_manager.py:140-156` |
| `_connected` set once, never invalidated | `backend/redis_manager.py:122,171,180`; 0 occurrences in `core/cache/redis_manager.py` |
| Recovery unreachable | `backend/redis_manager.py:548` fast path; `:192` `try_reconnect` early return |
| No pool teardown between tests | `tests/conftest.py`: `aclose`/`close()`/`disconnect`/`_cache = None`/`_connected = False`/`initialize(` all absent |
| Function-scoped loops | no `asyncio_mode`/loop-scope setting in any config file → pytest-asyncio default |
| Order-dependent staleness | isolated 2 passed/4 errors vs full-suite 1 passed/5 errors |
| Redis genuinely used | gate `state=run`; `Connection(host=localhost,port=6379,db=0)`; 0 skips in the D5′ files |
| Baseline intact | 37 node IDs recurred, 0 gone, per-file counts identical |

Artifacts: `p11b20_nodeids.{py,txt}`, `p11b20_trace.{py,txt}`, `p11b20_trace2.{py,txt}`,
`p11b20_cur_unit.log`, `p11b20_base_unit.log`; A19's `p11b19_diag/` retained.

---

## 10. Smallest safe next implementation step

**Defect 1:** change one expression in `backend_app/core/subscription_engine.py:1006` to
`return False, min(reverted, effective), effective`, and add **Q2** (50 racers at limit 10,
asserting every refused tuple is bounded) as the regression guard. Two files, one of them already
on PR #6. Needs approval for `subscription_engine.py`; needs no coordination.

**Defect 2:** do **not** start with a fix. Start with **R6** — a unit test, no server required,
asserting that `get_redis_manager()` attempts a rebuild after a runtime command failure rather
than returning the stale manager. It pins the contract before any production behaviour changes,
and it is the cheapest way to make the §3e decision concrete for the
`core/cache/redis_manager.py` owner.

Do not bundle them. Defect 1 is a reporting correction; Defect 2 is a change to how the whole
application recovers from cache loss.

---

## 11. Status

| Gate | Verdict |
|---|---|
| **D5′ acceptance** | **NOT MET** — 5 webhook assertions unexecuted, 1 production quota assertion failed |
| **D5′ real-Redis wiring** | **VERIFIED** — gate `state=run`, 0 skips in the D5′ files, `db=0`, real commands executed |
| **Baseline integrity** | **VERIFIED** — 37 node IDs recurred, 0 drift, no unrelated regression |
| **QUOTA-01** | **OPEN** — hard limit now evidenced against real Redis, but the refusal payload is wrong, `INCRBY` failure appears to fail open (unreproduced), and `store_keys` still carries the read-then-write race |
| **Exchange-slot concurrency and release safety** | **UNPROVEN** — 21 of 22 tests issued no Redis command; guard absent from the branch; tests retained unweakened |
| **TENANT-01** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |
