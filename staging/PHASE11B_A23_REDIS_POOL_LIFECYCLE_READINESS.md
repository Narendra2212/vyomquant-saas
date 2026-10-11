# Phase 11B-A23 — Redis pool lifecycle (G1/G2/G3): Step 0 authorization gate

**STATUS: STOPPED AT THE PHASE 1 AUTHORIZATION GATE. No production code was changed.**

The brief states *"only with explicit authorization, implement"* and *"Do not infer
implementation approval from this prompt or from the recommendation in A22V."* No explicit
authorization for Step 0 is present, so this task completed Phase 0 source analysis and
stopped. Phase 2 and Phase 3 were not entered.

**Date:** 2026-10-10 · Entry 21:55:41 · Exit 22:0x

Reports read, neither overwritten: `PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md`
(26,508 B, 21:39:06), `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` (21:5x).

---

## 1. Target ownership and preservation state

| File | Tracked | Status | Size | sha256 (24) | vs A22V |
| --- | --- | --- | --- | --- | --- |
| `backend_app/backend/redis_manager.py` | yes | **clean** | 21,497 B | `1FD5CA799E792CCB0CF0DBA7` | **unchanged** |
| `tests/test_redis_debounce.py` | yes | ` M` | 16,634 B | `251E7CF076816CB489E3544C` | **unchanged** |
| `backend_app/core/cache/redis_manager.py` | yes | ` M` | 59,890 B | `66D273FC2E582D16F60E327A` | **unchanged** |
| `tests/conftest.py` | yes | ` M` | 22,317 B | `9D7BC7FE48C0FFB816C67DE7` | **unchanged** |

**The target file is tracked and clean** — no competing edits, which is why Step 0 needs no
cross-workstream coordination. `tests/test_redis_debounce.py` remains the A22 session's
in-flight work and was not touched. The other two remain their owners' per the A22V
boundary.

| Property | Entry | Exit | |
| --- | --- | --- | --- |
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | unchanged |
| Ahead / behind, worktrees, unmerged | `0 / 0`, 1, **0** | same | unchanged |
| Index tree (copied index, isolated object dir) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **identical** | unchanged |
| Shared git objects | 198 | **198** | **0 written**, isolated dir also 0 |
| Staged entries / canonical digest | 22 / `2e994018…7152` | **identical** | unchanged |
| `git status --porcelain` | 261 | — | other sessions active |

---

## 2. Client API, established from the installed package

| Fact | Value |
| --- | --- |
| Import in the target | `import redis.asyncio as aioredis` (line 25) |
| Installed | **redis-py 5.0.8** (`psycopg2`-style pin: `redis` in `requirements-base.txt`) |
| `Redis.aclose` | **exists** |
| `Redis.close` | exists, and its source is: |

```python
@deprecated_function(version="5.0.1", reason="Use aclose() instead", name="close")
async def close(self, close_connection_pool: Optional[bool] = None) -> None:
    """Alias for aclose(), for backwards compatibility"""
    await self.aclose(close_connection_pool)
```

| `ConnectionPool.aclose` / `.disconnect` | both exist |

**Conclusion for Phase 2 item 4.** `close()` is a thin alias that forwards to `aclose()`,
so it is **functionally correct** on 5.0.8 — but it is decorated `@deprecated_function`,
so `RedisManager.close()` emits **3 DeprecationWarnings per shutdown**. Moving to
`aclose()` is therefore warranted and needs **no compatibility shim** at the pinned
version. A `getattr(client, "aclose", client.close)` fallback would be justified only if
the pin were relaxed below 5.0.1; that is a judgement for the owner, and I would not add
the shim without it, because an unexercised fallback branch is untested code.

> **Correction to A22V.** A22V called the `.close()` usage "cosmetic beside G3". The
> deprecation decorator makes it slightly more than cosmetic (warning noise on every
> shutdown) and slightly less than a defect (behaviour is identical). Both framings were
> imprecise; this is the measured position.

---

## 3. Call-site analysis — three findings A22 and A22V both missed

`staging/probe_p11b_a23_callers.py` → `staging/p11b_a23_callers.txt`. AST over 1,090 files
under `backend_app/`, `tests/`, `scripts/`, so a call inside a string or comment cannot be
miscounted.

### 3.1 `initialize()` has THREE callers, not two

| Site | Debounced? | Locked? |
| --- | --- | --- |
| `backend/redis_manager.py:213` — inside `try_reconnect()` | yes (`self._last_failed_at`) | yes (instance lock) |
| `backend/redis_manager.py:571` — inside `get_redis_manager()` | yes (`_get_manager_last_failed`) | yes (module lock) |
| **`core/cache/redis_manager.py:625`** — inside `connect()` | **NO** | **NO** |

```python
# backend_app/core/cache/redis_manager.py:621-625   (other workstream, read-only here)
async def connect(self):
    """Connect to Redis (delegates to underlying manager)."""
    await self._ensure_manager()
    if self._redis_manager and not self._redis_manager._cache:
        await self._redis_manager.initialize()
```

Both A22's call graph and A22V's listed only the two internal callers. The third bypasses
**both** cooldowns and **both** locks. It is reached from `main.py:370`
(`redis_manager.connect()`).

**Consequence for Phase 2 item 7** ("concurrent initialization and shutdown cannot publish
a partially initialized client set"): that guarantee cannot be made by editing the target
file alone, because one entry point into `initialize()` is unlocked and lives in another
workstream's file. Step 0 can make `initialize()` *internally* safe — idempotent cleanup,
atomic publish — but "no concurrent initialization" is **not** achievable within the Step 0
boundary. Stated here rather than claimed later.

### 3.2 `RedisManager.close()` IS reached in production — G3 is live, not latent

I initially hypothesised `close()` was dead code. It is not:

```
main.py:607            await redis_manager.disconnect()        # core/cache object
core/cache:616-619     async def disconnect(self):
                           if self._redis_manager:
                               await self._redis_manager.close()   # ← BACKEND manager
backend:215-224        async def close(self): ...                  # G3 lives here
```

Plus seven script call sites (`scripts/test_{data_stream,execution,strategy}_worker.py`,
`scripts/test_event_bus.py`, `scripts/test_order_event_flow.py` ×3) on the compat proxy.

`main.py:74` imports `redis_manager` from `backend_app.core.cache` — a **different object**
with `connect()`/`disconnect()` — which is why a naive search attributes the shutdown path
to the wrong manager. It delegates to the backend one.

### 3.3 NEW FINDING A23-01 — reconnect-after-shutdown is a dead path

G1/G2/G3 were characterised individually. Traced together they compose into a
production-reachable failure that neither prior report names:

```
1  main.py:607  disconnect()      → core/cache:619 → backend close()
2  close()      closes all three clients, but does NOT reset _connected
                and does NOT unbind _cache/_queue/_events            ← G3, proven
3  main.py:370  connect()         → core/cache:623 _ensure_manager()
4  _ensure_manager() → get_redis_manager()
                fast path :548 sees _connected is STILL True
                → returns the same stale manager immediately          ← G3 feeds R6
5  core/cache:624  guard `not self._redis_manager._cache`
                _cache is STILL BOUND → guard is False
                → initialize() is SKIPPED                             ← G3 feeds connect()
6  the application proceeds on CLOSED clients
```

**Both halves of G3 are load-bearing, and in different places.** The un-reset flag defeats
`get_redis_manager()`'s fast path; the still-bound `_cache` defeats `connect()`'s own
guard. So a fix that only flips `_connected = False` leaves step 5 broken — `connect()`
would still skip `initialize()`. **`close()` must unbind as well as invalidate.** That
constraint is only visible by tracing the two files together, and it is the single most
important design input in this report.

---

## 4. Lifecycle invariants — current state

Per the brief's Phase 0 item 5. G1/G2/G3 evidence is from
`staging/probe_p11b_a22v_lifecycle_gaps.py` (A22V), re-read not re-run here.

| # | Invariant | Current state |
| --- | --- | --- |
| 1 | Every client created during failed initialization is closed, including on an intermediate ping failure | **VIOLATED (G1).** 3 created, **0** closed; the `except` at `:177-182` drops the locals. 3 pools leaked per attempt, once per 3.0 s cooldown |
| 2 | Existing clients stay valid until the replacement is ready, then are closed exactly once | **HALF-HELD.** The "stay valid" half holds — `initialize()` builds into locals and pings all three before committing at `:167-170`, so a failed rebuild cannot publish a partial set. The "closed exactly once" half is **VIOLATED (G2)**: the outgoing three are never closed |
| 3 | Shutdown invalidates health and unbinds clients | **VIOLATED (G3),** and composes into A23-01 |
| 4 | Cleanup failures do not mask the original initialization error | **N/A today** — there is no cleanup to fail. Becomes a live requirement the moment Step 0 adds one |
| 5 | Repeated cleanup and shutdown are safe | **UNVERIFIED.** `close()` is not idempotent by construction: it re-calls `.close()` on already-closed clients. redis-py's `aclose()` is tolerant of this, but no test covers it |

Invariant 2's first half being already correct is worth stating plainly: the existing
`initialize()` is **not** naive. It deliberately builds into locals and verifies before
publishing — the comment at `:163` says *"Verify connectivity before committing to
self.*"*. Step 0 adds cleanup to a structure that is already right about ordering.

---

## 5. Proposed minimal change

Confined to `backend_app/backend/redis_manager.py` (tracked, clean). **Not applied.**

| # | Change | Gap | Lines |
| --- | --- | --- | --- |
| 1 | Collect the three locals in a list as they are created; in the `except`, close every one inside a nested `try/except` that swallows secondary errors, then re-raise/return the original failure unchanged | **G1** | `:140-166`, `:177-182` |
| 2 | Before rebinding on success, capture the outgoing `self._cache/_queue/_events`; publish the new set; then close the outgoing set. Publish first so a cleanup error cannot leave the manager unpublished, and never close a client that is in the newly published set | **G2** | `:167-171` |
| 3 | `close()` sets `_connected = False` and sets `_cache = _queue = _events = None`, after closing. Tolerate already-closed clients so repeated `close()` is safe | **G3**, **A23-01**, invariant 5 | `:215-224` |
| 4 | Use `aclose()` (present on 5.0.8); no shim unless the owner wants the pin relaxed | warnings | `:218-222` |

Ordering rationale, which is the part that needs review rather than the mechanics:

- **Publish-then-close for success (change 2).** Closing first would leave a window where
  `self._cache` points at a closed client while the new one is built. Publishing first
  means the worst case is a leaked pool, not a live reference to a dead client.
- **Close-then-unbind for shutdown (change 3).** Unbinding first would lose the handles
  needed to close them.
- **Original error wins (change 1).** A secondary close failure during cleanup of a failed
  init must not replace the diagnosis of why init failed.

Explicitly **out of scope**, per the brief: no event-loop identity rebuilding, no runtime
command-failure invalidation, no change to retry policy, cooldown semantics, executor
behaviour, tenant boundaries, or the core-cache proxy. **R6 stays OPEN after Step 0.**

---

## 6. Focused test plan

The recorder harness already exists in `staging/probe_p11b_a22v_lifecycle_gaps.py`
(`FakeClient` / `FakeAioredis`, `object.__new__` to bypass the singleton). It would move
into a test file. **Destination not yet created and needs authorization** — the A22 session
owns `tests/test_redis_debounce.py`, so a **new** file is required, e.g.
`tests/test_a23_redis_pool_lifecycle.py`, subject to the owner confirming no collision.

| # | Test | Must fail before, pass after |
| --- | --- | --- |
| 1 | third client's `ping()` raises → all 3 created clients closed exactly once | yes — currently 0 closed |
| 2 | cleanup itself raises → `initialize()` still returns `False` and the original error is logged, not the cleanup error | yes — no cleanup exists |
| 3 | successful re-init → the 3 outgoing clients closed exactly once | yes — currently 0 closed |
| 4 | successful re-init → the 3 **new** clients remain open | **passes today**; a regression guard against over-closing |
| 5 | `close()` → `_connected is False` and all three attrs are `None` | yes — currently True/bound |
| 6 | `close()` twice → no exception, no double-close beyond tolerance | unverified today |
| 7 | failed init cannot leave the manager marked connected | **passes today** (`:180`); guard |
| 8 | **A23-01 composite:** `close()` then `connect()`-shaped reinit actually rebuilds | yes — currently skipped |

Test 8 is the one that proves A23-01 and it is the most valuable of the eight, because it
is the only one asserting the two gaps do not compose. Tests 4 and 7 pass today by design
and are included as negative controls so a fix cannot over-reach.

Run plan: new file first, then `tests/test_redis_debounce.py` (10 tests) **serially**,
then the wider Redis-adjacent unit tests. No Redis server; endpoints confirmed unset and
6379 closed.

---

## 7. Compatibility and lifecycle uncertainties

1. **Unlocked third entry point.** `core/cache:625` calls `initialize()` with no cooldown
   and no lock (§3.1). Step 0 cannot make concurrent initialization safe on its own.
2. **Seven script call sites** use `redis_manager.close()` through the compat proxy. After
   change 3 the manager is correctly unusable until re-initialized; if any script closes
   and then keeps using it, it would now fail fast instead of silently using closed
   clients. That is the intended behaviour, but it is a **behavioural change** and those
   scripts were not read line by line.
3. **`_initialized` flag.** `__new__` sets `cls._instance._initialized = False`; its
   interaction with `__init__` re-entry on the singleton was not traced and is not touched
   by the proposal.
4. **Invariant 5 unverified** — repeated `close()` tolerance rests on redis-py's behaviour,
   not on a test.
5. **A22 F5 (two independent cooldowns)** remains unreproduced; Step 0 does not address it.
6. **No full-suite run.** Nothing here was executed against the application.
7. **G1/G2/G3 evidence is A22V's**, re-read from `staging/p11b_a22v_lifecycle_gaps.txt`,
   not re-executed in A23.

---

## 8. Authorization still required

| # | Decision | Owner | Status |
| --- | --- | --- | --- |
| **1** | **Implement Step 0 changes 1–4 in `backend_app/backend/redis_manager.py`** | owner of that file (tracked, clean, no competing edits) | **NOT GRANTED — the gate** |
| **2** | **Create `tests/test_a23_redis_pool_lifecycle.py`** (new file; the A22 session owns `test_redis_debounce.py`) | A22 session + test owner | **NOT GRANTED** |
| 3 | Whether to add an `aclose`/`close` compatibility shim, i.e. whether the redis-py pin may drop below 5.0.1 | dependency owner | **NOT GRANTED** |
| 4 | Accept the behavioural change for the 7 script `close()` call sites (§7.2) | owner of `scripts/test_*_worker.py` | **NOT GRANTED** |
| 5 | Add locking/debounce to `core/cache:625`, or accept that concurrent-init safety is out of Step 0's reach | **owner of `backend_app/core/cache/redis_manager.py`** (` M`, in flight) | **NOT GRANTED** |

Decisions 1 and 2 are the minimum to begin Phase 2. Decision 5 is **not** required for
Step 0 but must be recorded as a known limitation, or Phase 2 item 7 will be reported as
satisfied when it is not.

---

## 9. Exact next step

> With decisions 1 and 2 granted: implement changes 1–4 of §5 in
> `backend_app/backend/redis_manager.py`, and add the 8 tests of §6 in a new
> `tests/test_a23_redis_pool_lifecycle.py`, reusing the recorder harness from
> `staging/probe_p11b_a22v_lifecycle_gaps.py`. Confirm tests 1, 2, 3, 5, 6 and 8 fail
> against current behaviour before the edit and pass after, and that tests 4 and 7 pass in
> both states. No Redis server.

---

## 10. Scope and integrity

- **No production source changed.** `backend_app/backend/redis_manager.py` byte-identical,
  21,497 B, sha256 `1FD5CA799E792CCB0CF0DBA7…`, clean.
- **No test added or changed.** `tests/test_redis_debounce.py` byte-identical at
  16,634 B / `251E7CF0…`.
- **Ownership respected.** `core/cache/redis_manager.py` and `tests/conftest.py` read only,
  both byte-identical.
- **Neither A22 report overwritten.**
- **Nothing staged.** Index tree `6f40856c…` identical; staged digest `2e994018…`, 22
  entries. Shared object count 198 → 198; verification used a copied index in an isolated
  object directory and wrote **0** objects anywhere.
- **No Redis, database, network, migration, commit, push, PR action, workflow trigger or
  deployment.**

Artifacts created, all new, all under `staging/`: `probe_p11b_a23_callers.py`,
`p11b_a23_callers.txt`, and this report.

---

## Status

| | |
| --- | --- |
| **Step 0 (G1/G2/G3)** | **NOT IMPLEMENTED — blocked at the authorization gate** |
| G1 / G2 / G3 | reproduced in A22V; **not fixed** |
| **A23-01** (reconnect-after-shutdown dead path) | **NEW — identified, not fixed** |
| A23 §3.1 (third, unlocked `initialize()` caller) | **NEW — identified** |
| R6 | **OPEN.** Step 0 would not close it; no runtime-failure or cross-loop recovery is claimed |
| A22 F5 (two cooldowns) | still unreproduced |
| Real-Redis lane | **UNEXECUTED** |
| TENANT-01 / QUOTA-01 / D5 Redis | **untouched** |
| Live trading | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from A22V's recommendation, from silence, or
from the existence of a plan. Skipped tests are never counted as passes.
