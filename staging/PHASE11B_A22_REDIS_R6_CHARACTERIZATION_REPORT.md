# Phase 11B-A22 — Redis Lifecycle R6 Characterization

**Date:** 2026-10-10
**Destination check:** `staging/PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` verified
**ABSENT** before writing. No existing report overwritten.
**Scope honoured:** test-only. One file changed — `tests/test_redis_debounce.py`, **+225 / −0**.
No production source, no `tests/conftest.py`, no loop scope, no fixture semantics, no commit,
push, PR change, Redis/database/network call.

---

## HEADLINE

**The recovery contract is now pinned by executable evidence.** Four tests in
`tests/test_redis_debounce.py`: three characterization tests that pass today and one
`xfail(strict=True)` contract test that fails today for the intended reason, at the intended line.

```
9 passed, 1 xfailed in 6.76s      (6 pre-existing + 4 new, zero skips, server-free)
```

Two findings a rebaseline was needed to see:

1. **`get_redis_manager()` does not attempt a rebuild after a runtime command failure — proven,
   not inferred.** `initialize` is awaited **0** times, the identical stale manager object comes
   back, and the cache it exposes is still the failing client. A control test with the same
   harness observes `initialize` awaited **exactly once**, so "no rebuild" cannot be confused with
   "the patch didn't take".
2. **A20's quota Decision Record A has already been implemented by its owner.**
   `backend_app/core/subscription_engine.py` was **clean at A20** and is now ` M` **+57 / −4**,
   carrying both the clamp *and* the fail-open fix I had flagged as unreproduced. That workstream
   is no longer unowned; §7 records the handoff.

A third finding changes the design ranking: **option B can be implemented entirely within
`backend/redis_manager.py` and needs no coordination; option A cannot, because the exception is
caught in the other workstream's file.** But B alone does not fix real connection loss. See §6.

---

## 1. Entry / exit repository state

| | Entry | Exit | |
|---|---|---|---|
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical | SAME |
| `origin/main` divergence | 0 / 0 | 0 / 0 | SAME |
| Index **content digest** | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | identical | **SAME** |
| Staged paths | 22 | 22 | SAME |
| Unmerged | 0 | 0 | SAME |
| Worktrees | 1 (`main`) | 1 | SAME |
| Porcelain entries | 260 | 261 | **+1, accounted for below** |

Full snapshots: `staging/p11b22_entry_staged.txt`, `staging/p11b22_entry_status.txt`.

| Change class | Count | Detail |
|---|---|---|
| Newly appearing | **1** | ` M tests/test_redis_debounce.py` — this task's only edit |
| Disappeared | **0** | no pre-existing work lost |
| Status changed | **0** | no file's staged/unstaged class altered |

Forbidden-file verification (entry status vs exit status, both recorded):

| File | Entry | Exit | |
|---|---|---|---|
| `tests/conftest.py` | ` M` | ` M` | **unchanged by me** |
| `backend_app/core/cache/redis_manager.py` | ` M` | ` M` | **unchanged by me** |
| `backend_app/backend/redis_manager.py` | clean | clean | **unchanged by me** |
| `backend_app/core/subscription_engine.py` | ` M` | ` M` | **unchanged by me** (see §7) |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | `??` | blob `2d7ccbbb` **SAME** |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` | `??` | blob `7baff0b6` **SAME** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | `??` | blob `3ffbccde` **SAME** |
| `.github/workflows/01-pr-check.yml` | ` M` | ` M` | unchanged by me |
| `pytest.ini` | ` M` | ` M` | unchanged by me |

### Ownership, re-assessed at entry

| File | Status | Lines | Disposition |
|---|---|---|---|
| `backend_app/backend/redis_manager.py` | **clean** | 593 | Read-only here. **Clean is not authorization** |
| `backend_app/core/cache/redis_manager.py` | ` M` +185/−5 | 1488 | Other workstream — read-only |
| `tests/conftest.py` | ` M` +172/−0 | 443 | Other workstream — read-only |
| `tests/test_redis_debounce.py` | tracked, **clean** | 188 → 413 | **In scope.** Not owned by another workstream, not in the forbidden list |

---

## 2. Source call graph — the recovery path

Traced through `backend_app/backend/redis_manager.py` and
`backend_app/core/cache/redis_manager.py`, step by step as the brief specifies.

```
(1) a command fails at runtime
      core/cache/redis_manager.py  SharedRedisManager.set / get / incrby / decrby
      e.g. set():  try: return await client.set(...)
                   except Exception as e: logger.warning("Cache SET failed …"); 
                   return False                                        <- swallowed here
(2) the proxy catches it and returns a neutral value. `_connected` is NOT referenced
      occurrences of `_connected` in core/cache/redis_manager.py: ZERO
(3) manager health state is NOT updated
      backend/redis_manager.py: `_connected` is written at L122 (init False),
      L171 (initialize success -> True), L180 (initialize failure -> False).
      initialize() is the ONLY writer.
(4) get_redis_manager()  L536
      L548  if _redis_manager is not None and _redis_manager._connected:
      L549      return _redis_manager                                  <- STICKY FAST PATH
      L552  cooldown gate on module-global _get_manager_last_failed
      L556  if lock.locked(): return _redis_manager
      L571  success = await _redis_manager.initialize()                <- never reached
(5) initialize()  L133
      L140/148/156  three aioredis.from_url pools, bound to the CREATING loop
      L164-166      ping all three
      L168-171      assign to self._cache/_queue/_events; _connected = True
      L178-184      except: log, _connected = False, _last_failed_at = now
                    -- the three local pools are DISCARDED WITHOUT close()
                    -- self._cache etc. still hold the OLD pools
(6) try_reconnect()  L186
      L192  if self._connected: return True                            <- also short-circuits
      L196  cooldown on self._last_failed_at (_RECONNECT_COOLDOWN, default 3.0s,
            env REDIS_RECONNECT_COOLDOWN)
      L199  if lock.locked(): return self._connected
      L213  return await self.initialize()
(7) old pools are never closed on a rebuild
      close() exists at L215 and is never called from initialize() or try_reconnect()
```

### Findings

| # | Finding | Evidence |
|---|---|---|
| F1 | **A runtime failure cannot change manager health.** `_connected` has exactly one writer, `initialize()`; the proxies that observe failures never touch it | L122/171/180; zero `_connected` occurrences in the core proxy |
| F2 | **Both recovery entry points short-circuit on that flag**, so the documented machinery is unreachable from the state that needs it | L548, L192 |
| F3 | **A successful rebuild leaks the previous three pools.** `initialize()` overwrites `self._cache/_queue/_events` without closing them; `close()` is never called | L168-170 vs L215 |
| F4 | **Partial initialization failure leaks too, and leaves stale pools installed.** If `events.ping()` fails after two pools are built, all three locals are dropped unclosed and `self._*` keep the old objects while `_connected` goes False | L140-184 |
| F5 | **Two independent cooldowns exist.** `try_reconnect` debounces on `self._last_failed_at`; `get_redis_manager` debounces on the module global `_get_manager_last_failed` — and `get_redis_manager` calls `initialize()` **directly**, bypassing `try_reconnect`'s gate | L196 vs L552, L571 |
| F6 | **Consumers pick up a rebuilt manager automatically.** `RedisManagerCompatProxy.__getattr__` re-imports `_redis_manager` on every access (L580-590) and `SharedRedisManager._ensure_manager()` re-assigns from `get_redis_manager()` each call — so no consumer caches a stale handle | L580-590; core L489-498 |

F6 matters: it means a rebuild **propagates** without changes to any consumer. The blocker is
purely that no rebuild is triggered.

### Was the A20 R6 expectation compatible with the real path?

Partly. A20 proposed asserting that `get_redis_manager()` "attempts a rebuild after a runtime
command failure". That is testable and is what §4 does — but A20 did not note that the only
signal source is a *different workstream's* file (F1), nor F5's two-cooldown asymmetry. Both are
now recorded, and both change the design ranking in §6.

---

## 3. Test seam

Prefer-existing-seams review, before writing anything:

| Need | Existing seam used | Why not something new |
|---|---|---|
| A manager not entangled with the process singleton | **`_make_fresh_manager()`** — already in this module; builds via `object.__new__(RedisManager)` | Avoids `RedisManager._instance` entirely, so no singleton leak and no need for any fixture |
| Isolate `get_redis_manager()`'s state | Save/restore `_redis_manager`, `_get_manager_last_failed`, `_get_manager_lock` — the pattern already in `test_get_redis_manager_debounced` | Wrapped in a small local context manager so it cannot be forgotten; **no conftest change, no autouse fixture** |
| Inject the fault | `AsyncMock` with `side_effect=RuntimeError("Event loop is closed")` | The exception observed in CI run 38061973739, verbatim — not a convenient stand-in |
| Distinguish stale-return from rebuild | `patch.object(mgr, "initialize", AsyncMock(...))` and assert `await_count` | Instance-level patch; no `autospec` complexity; counts invocations rather than reading a private flag |
| Lock/cooldown isolation | set `_get_manager_last_failed` explicitly per test | No production change needed |

No new fixture, no new conftest entry, no loop-scope touch, no `pytest.ini` change.

---

## 4. The tests, and exactly what they prove

Appended to `tests/test_redis_debounce.py` (+225 lines, 0 deletions). Four tests plus two helpers
and a save/restore context manager.

| Test | Role | Today |
|---|---|---|
| `test_r6_a_runtime_command_failure_is_real_and_leaves_health_unchanged` | **guard** — the injected fault actually fires (`cache.set.await_count == 1`) | PASS |
| `test_r6_b_get_redis_manager_returns_the_stale_manager_without_attempting_a_rebuild` | **characterization** — `initialize` awaited **0** times; returned object `is` the stale manager; its cache `is` the failing double; a second command still raises | PASS |
| `test_r6_c_the_same_harness_detects_a_genuine_rebuild_attempt` | **control** — one variable changed (`_connected = False`); `initialize` awaited **exactly 1**; the rebuilt cache is the healthy double and a command succeeds | PASS |
| `test_r6_d_desired_a_runtime_failure_should_be_recoverable` | **desired contract**, `xfail(strict=True)` | **XFAIL** |

### Why this proves the contract rather than a broken mock

The brief requires distinguishing a real defect from a broken mock, a singleton leak, a closed
test loop, or fixture ordering. Each is excluded by construction:

| Alternative explanation | Excluded by |
|---|---|
| The mock never raises, so "no rebuild" is vacuous | `r6_a` asserts the raise **and** `await_count == 1` |
| The `initialize` patch never took effect, so `await_count == 0` is meaningless | `r6_c` uses the **same** patch mechanism and observes `await_count == 1`. The harness is demonstrably able to see a rebuild |
| A singleton leak from another test | `_make_fresh_manager()` bypasses `RedisManager._instance` via `object.__new__`; the three module globals are saved and restored |
| A closed event loop in the test | No real loop-bound resource exists — every client is an `AsyncMock`. The failure is injected, not environmental |
| Fixture ordering / autouse interference | No fixture is added or changed; state is arranged inside each test body |
| Cooldown accidentally suppressing the rebuild | `_get_manager_last_failed` is set explicitly per test; `r6_c` proves a rebuild **is** reachable with the same cooldown arrangement |

`r6_c` is the load-bearing one: it converts `r6_b`'s negative assertion into a falsifiable claim.
That is precisely the brief's requirement that *"the stale-manager fast path does not silently
count as a successful rebuild attempt."*

### Why `xfail(strict=True)` rather than a plainly failing test

A20/A19 established that the blocking lane's acceptance is **node-ID based**. A new permanently
red test would be indistinguishable from a regression in that comparison and would actively
damage the gate it was meant to inform. `xfail(strict=True)`:

- **executes** (it is not a skip — the brief's "never report unexecuted tests as passing" holds);
- reports as `xfailed`, a visible, explainable delta rather than a failure;
- becomes a **reported failure (XPASS)** the moment a fix lands, which is the exact signal the
  owner needs to retire the marker.

`xfail_strict` is **not** set in `pytest.ini`, so `strict=True` is passed explicitly.

**Blocking-lane impact, stated for the record:** the lane's baseline carries 4 xfailed; this adds
one, so a future run should show **5 xfailed** and no new failures. That delta must be expected,
not treated as drift.

---

## 5. Commands and results

```
$ python -m pytest tests/test_redis_debounce.py -p no:randomly --no-header -v --tb=short
tests/test_redis_debounce.py::test_thundering_herd_one_attempt[asyncio]                        PASSED
tests/test_redis_debounce.py::test_reconnect_cooldown_respected[asyncio]                       PASSED
tests/test_redis_debounce.py::test_reconnect_allowed_after_cooldown[asyncio]                   PASSED
tests/test_redis_debounce.py::test_recovery_after_outage[asyncio]                              PASSED
tests/test_redis_debounce.py::test_healthy_fast_path_skips_lock[asyncio]                       PASSED
tests/test_redis_debounce.py::test_get_redis_manager_debounced[asyncio]                        PASSED
tests/test_redis_debounce.py::test_r6_a_runtime_command_failure_is_real_…[asyncio]             PASSED
tests/test_redis_debounce.py::test_r6_b_get_redis_manager_returns_the_stale_manager_…[asyncio] PASSED
tests/test_redis_debounce.py::test_r6_c_the_same_harness_detects_a_genuine_rebuild_…[asyncio]  PASSED
tests/test_redis_debounce.py::test_r6_d_desired_a_runtime_failure_should_be_recoverable[asyncio] XFAIL
======================== 9 passed, 1 xfailed in 6.76s =========================
exit code 0
```

All six pre-existing tests still pass, unmodified.

### The xfail fails for the intended reason — verified at the statement level

```
$ python -m pytest "tests/test_redis_debounce.py::test_r6_d_…" --runxfail --tb=long
            with pytest.raises(RuntimeError, match=_OBSERVED_RUNTIME_ERROR):
                await mgr.cache.set("r6:probe", "1")          # caught, as arranged
            returned = await rm.get_redis_manager()
            # Fails today: the same failing client comes straight back.
>           await returned.cache.set("r6:probe", "2")
tests\test_redis_debounce.py:413:
self = <AsyncMock name='mock.set' …>, args = ('r6:probe', '2')
E               RuntimeError: Event loop is closed
1 failed in 6.41s      exit code 1
```

The `>` marker is on **line 413** — the desired-contract assertion — with `args = ('r6:probe',
'2')`, the **second** call. The arranged raise at line 408 was caught by its `pytest.raises`. So
the failure is the contract, not the setup.

> **Methodology note, recorded because it nearly produced a wrong claim.** My first `--runxfail`
> capture took only the last 26 lines and showed `tests\test_redis_debounce.py:408:` with
> `args = ('r6:probe', '1')`, which reads as "the fault injection itself failed". That was a
> truncation artifact: the `:408:` label belonged to an earlier frame in the chain. Re-running
> with the full traceback gave line 413 and args `'2'`. This is the second time in this
> engagement that a truncated log nearly inverted a conclusion; full-output capture is now the
> default for any claim about *which* line failed.

### Adjacent suites

```
$ python -m pytest tests/test_redis_cluster.py tests/test_dag_redis_migration.py \
      tests/test_redis_fallback_does_not_fake_a_distributed_lock.py tests/test_durable_event_bus.py -q
43 passed in 23.74s      exit code 0
```

### Stage 3 confirmations

| Requirement | Result |
|---|---|
| Server-free | **yes** — every client is an `AsyncMock`; `initialize()` is patched or never reached, so `aioredis.from_url` is never called. No Redis, DB, network or credential |
| Existing assertions intact | **yes** — 6/6 pre-existing tests pass, untouched |
| No skips introduced | **yes** — 0 skipped; the xfail executes |
| No loop config / fixture semantics changed | **yes** — `pytest.ini` untouched, no fixture added or modified |
| Failure attributable to the contract | **yes** — line 413, verified above |
| Full-suite rerun | **not performed**, per instruction |

---

## 6. Design decision — A vs B vs C

Assessed against the source read in §2, not assumptions.

| Dimension | **A — runtime invalidation + existing reconnect path** | **B — track the creating loop, rebuild on mismatch** | **C — both** |
|---|---|---|---|
| Do existing locks/cooldown prevent duplicate rebuilds? | **Yes**, reused as-is: `try_reconnect` L196/L199 and `get_redis_manager` L552/L556. Already proven by `test_thundering_herd_one_attempt` (≤2 attempts for 50 callers) and `test_reconnect_cooldown_respected` | **Yes**, but only if the loop check is placed *before* the cooldown gate; otherwise a loop change waits out a 3 s cooldown it has nothing to do with | Yes — loop mismatch bypasses cooldown, failure-invalidation respects it |
| Partial initialization failure | **Unchanged and still defective (F4)**: locals dropped unclosed, stale `self._*` retained. A invalidates more often, so it hits this path more often | Same exposure | **Must be fixed as part of C** — close what was built before re-raising |
| Old pools closed without masking the original error? | **Not today (F3).** `close()` is never called from `initialize()`. Needs `close()` in a `finally`/`except` that swallows close errors so the original failure survives | Same | Same; the correct shape is "close best-effort, preserve the first exception" |
| Recovers a real runtime connection loss without a process restart? | **YES — this is the only option that does.** A server restart or failover surfaces as a proxy exception with the loop unchanged | **NO.** The loop is the same, so nothing triggers a rebuild. B fixes only the loop-replacement case | **YES** |
| Effect on all Redis consumers | Broad: every `get`/`set`/`incrby`/`decrby`/stream caller. Mitigated by F6 — consumers re-resolve the manager each call, so no handle is cached | Narrow: inert in production, where the loop never changes | Broad, same as A |
| Must the core cache proxy participate? | **YES.** The exception is caught in `core/cache/redis_manager.py` (` M`, **other workstream**), so the invalidation signal must originate there. **Requires coordination** | **NO.** Entirely inside `backend/redis_manager.py` (clean). **No coordination required** | Yes, for the A half |
| Rebuild storms under repeated failure | Governed by the two existing cooldowns, but **F5 is a hazard**: `get_redis_manager` calls `initialize()` directly and does not consult `self._last_failed_at`, so an invalidation-driven loop could retry on the module cooldown while the instance cooldown is ignored. Should be unified | Not storm-prone: a loop mismatch is a one-off per loop | Needs F5 unified before shipping |
| Regression tests required first | R6 b/c/d (**delivered**), plus: invalidation is idempotent; cooldown still honoured after invalidation; partial-init failure closes what it built; original exception not masked | A loop-identity unit test (two successive `asyncio.run` calls); plus F3/F4 coverage | Union of both |

### Recommendation

**C, implemented as B-then-A, not as one change.**

- **B first.** It is the only half that is independently shippable: `backend/redis_manager.py` is
  clean, no other workstream is involved, and it is inert in production because the loop never
  changes there. It makes the four webhook setup errors disappear and it is testable without a
  server.
- **A second, with coordination.** A is the half that matters for production: it is the *only*
  option that recovers a genuine connection loss. It cannot be done without the
  `core/cache/redis_manager.py` owner, because that file is where the exception is observed.
- **F3, F4 and F5 are prerequisites for A**, not optional polish. Invalidating more often while
  `initialize()` still leaks pools on every rebuild and partial failure would trade one defect for
  a slower leak.

**Rejected:** choosing B alone. It fixes the pytest symptom and leaves the production stickiness
untouched — exactly the outcome the brief warns against. B is a first step, not a solution.

**No implementation was performed.** Approval from the `backend/redis_manager.py` owner is
required before B; coordination with the `core/cache/redis_manager.py` owner before A. The
`tests/conftest.py` owner should be told that **loop-scope and pool-teardown changes are not part
of this workstream** — options 3 and 4 from A20 §3e were rejected precisely so their file is not
disturbed.

---

## 7. Material change since A20 — the quota workstream now has an owner

A20 Decision Record A recorded `backend_app/core/subscription_engine.py` as **clean and unowned**.
At this task's entry it is ` M` **+57 / −4**, two hunks, both inside `reserve_quota`. The
concurrent change implements **both** A20 findings:

```python
+        if amount > 0 and new_usage < amount:
+            logger.error("… refusing user %s plan %s because the quota is unmeasurable", …)
+            return False, 0, effective          # D1b: the INCRBY fail-open, now fails CLOSED
...
+            settled = min(max(reverted, 0), effective)
+            if settled != reverted:
+                logger.info("… read %s mid-contention; reporting the settled figure %s/%s …")
+            return False, settled, effective    # D1: the clamp
```

Their clamp is a strict improvement on A20's suggestion: `max(reverted, 0)` additionally guards
the negative case. And they fixed D1b, which A20 could only label **INVESTIGATED, NOT
REPRODUCED**.

**Consequence:** A20's "quota step 1 is low-risk and independent" recommendation should now be
**routed to that owner, not re-implemented.** I did not touch the file.

**One discrepancy to reconcile, flagged not resolved:** their comments cite *"a refusal reporting
35 against a limit of 10"* in run `38061973739`. A19's artifact and A20 both record **36**
(`AssertionError: a refusal reported usage 36, outside [0, 10]`, `assert 36 <= 10`). Either a
different racer's value from the same run or a transcription slip. It does not affect the fix's
correctness; it should be corrected for the record by whoever owns the comment.

---

## 8. Remaining limitations

1. **Characterization, not proof of production behaviour.** `r6_b` proves the *contract* with
   doubles. It does not prove a real Redis under a real failover behaves identically — though
   A19's CI evidence (4 isolated / 5 full-suite setup errors) is the corroborating real-world
   observation.
2. **F3 and F4 are source-derived and unreproduced.** No test yet demonstrates the pool leak on
   rebuild or on partial initialization. They are inferred from `initialize()` never calling
   `close()`, which is strong but not executed.
3. **F5 is likewise unreproduced** — the two-cooldown asymmetry is read from source.
4. **No full-suite run.** The declared blocking-lane impact (+1 xfailed) is a prediction, not an
   observation.
5. **The webhook errors are not fixed.** This task deliberately changed no behaviour, so the five
   webhook setup errors in run 38061973739 persist.

---

## 9. Owner coordination required

| Owner | File | Ask |
|---|---|---|
| `backend_app/backend/redis_manager.py` | clean | **Approve or reject option B** (loop-identity rebuild) and the F3/F4/F5 prerequisites. This is the approval boundary for the next task |
| `backend_app/core/cache/redis_manager.py` | ` M` | **Coordination for option A only.** Invalidation must originate where the exception is caught. Nothing needed for B |
| `tests/conftest.py` | ` M` | **Information only:** loop-scope and pool-teardown changes are explicitly out of scope and were rejected as fixes |
| `backend_app/core/subscription_engine.py` | ` M` | **Handoff:** A20 Decision Record A is already implemented by you; please reconcile the 35 vs 36 citation (§7) |

---

## 10. Recommended next step and approval boundary

**Next step:** obtain the `backend/redis_manager.py` owner's decision on §6 — specifically,
approval to implement **option B alone** as a first, independently shippable change, together with
the F3 (close-on-rebuild) and F4 (partial-failure cleanup) prerequisites.

**Approval boundary:** nothing in `backend_app/` may be edited until that approval is explicit. In
particular, do **not** implement option A as a side effect of B — it requires the core cache proxy
owner and carries the broad blast radius documented in §6.

R6 is now available as the executable contract that decision can be judged against:
`r6_b` records what happens today, `r6_c` proves the harness can see the alternative, and `r6_d`
turns into a reported XPASS the moment the behaviour changes.

---

## 11. Status

| | |
|---|---|
| **R6 characterization** | **COMPLETE** — 3 passing characterization tests + 1 `xfail(strict=True)` contract test, server-free, verified failing at the intended statement |
| **Redis recovery contract** | **CHARACTERIZED, NOT FIXED** — no production change made or authorized |
| **Webhook async-lifecycle errors** | **OPEN** — unchanged by this task |
| **QUOTA-01** | **OPEN** — not closed by A22. The metered refusal fix is in another workstream's uncommitted tree and unverified against real Redis; `store_keys` still carries the read-then-write race |
| **Exchange-slot concurrency and release safety** | **UNPROVEN** — tests retained, byte-unchanged (`2d7ccbbb`) |
| **TENANT-01** | **OPEN** — 22 staged paths untouched |
| **Production Redis reliability gate** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

Artifacts: `p11b22_stage0.{py,txt}`, `p11b22_entry_staged.txt`, `p11b22_entry_status.txt`,
`p11b22_integrity.{py,txt}`, `p11b22_xfail.txt`, `p11b22_quota_concurrent.diff`,
`p11b22_quota_lines.txt`.
