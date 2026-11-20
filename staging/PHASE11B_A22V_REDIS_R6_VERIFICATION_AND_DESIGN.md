# Phase 11B-A22V — Redis R6: independent verification and design decision

**Companion to `staging/PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md`, which was
written by another session at 21:39:06 and is NOT overwritten.**

**Date:** 2026-10-10 · **Entry:** 21:41:54 · **Exit:** 21:5x

---

## 0. Why this file exists instead of the specified destination

The A22 brief says to write `staging/PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md`
*"only if the destination is absent. If it exists, do not overwrite it; choose a new
coordinated filename."*

| Check | Result |
| --- | --- |
| Destination at my Phase 0 (21:41:54) | **PRESENT** — 26,508 B, mtime **21:39:06**, sha256 `40795159…950F` |
| Companion name `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` | absent, verified immediately before writing |

So a coordinated companion name was used.

### 0.1 The R6 test already exists — I stopped before editing

The brief's §1 asks for the R6 test to be added. **It is already there.** The other
session added it to `tests/test_redis_debounce.py`:

```
tests/test_redis_debounce.py | 225 +++++++++++++++++++++++++++++++++++++++++++
1 file changed, 225 insertions(+)
```

Four new tests, with the six pre-existing tests byte-unchanged:

```
+class _RecordingManagerGlobals:
+def _failing_cache(message: str = _OBSERVED_RUNTIME_ERROR):
+def _connected_manager_with(cache):
+async def test_r6_a_runtime_command_failure_is_real_and_leaves_health_unchanged()
+async def test_r6_b_get_redis_manager_returns_the_stale_manager_without_attempting_a_rebuild()
+async def test_r6_c_the_same_harness_detects_a_genuine_rebuild_attempt()
+@pytest.mark.xfail(... strict ...)
+async def test_r6_d_desired_a_runtime_failure_should_be_recoverable()
```

That file is now ` M` and owned by the session that just completed A22. The brief's
ownership boundary says: *"If ownership or repository state conflicts with this task, stop
before editing."* Adding a second R6 test to a file another session has in flight is
exactly that conflict, so **no test was added by this task and no file was edited.**

This task therefore delivers what is still missing and is read-only: independent
verification of their result, three gaps proven by execution rather than by reading, and
the §3 design decision.

### 0.2 What this companion adds

| # | Contribution | Status in the existing A22 report |
| --- | --- | --- |
| 1 | Independent re-run of the R6 suite, server-free | their result confirmed |
| 2 | **F4 (partial-init pool leak) proven by execution** | their §"unresolved" item 2: *"source-derived and **unreproduced**"* |
| 3 | **F3 (rebuild leaks previous pools) proven by execution** | same — **unreproduced** |
| 4 | **New finding A22V-G3: `close()` leaves `_connected = True`** | **not covered** |
| 5 | R6 contract re-derived from source — every write and every gate | corroborates their call graph |
| 6 | §3 design decision with the leak evidence folded in | their §6 ranks A/B; this adds the cleanup dimension |

Their report is sound. Items 2–4 close two of its five stated uncertainties and add one
finding.

---

## 1. Entry / exit repository state

Index verification used a **copied index** and `GIT_OBJECT_DIRECTORY` pointed at a temp
directory with the real object store as an alternate, per the brief's instruction not to
write verification objects into the shared database.

| Property | Entry 21:41:54 | Exit | |
| --- | --- | --- | --- |
| Branch / upstream | `main` / `origin/main` | same | unchanged |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | same | unchanged |
| Ahead / behind | `0 / 0` | `0 / 0` | unchanged |
| Worktrees | 1 (`C:/aerora_quant_backend_updated_final1 074b772a [main]`) | 1 | unchanged |
| Unmerged paths | **0** | **0** | unchanged |
| Index tree (isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **identical** | unchanged |
| Shared git objects | 198 | **198** | **nothing written** |
| Objects written to the isolated dir | — | **0** | — |
| Staged entries / canonical digest | 22 / `2e994018…7152` | **identical** | unchanged |
| `git status --porcelain` | 261 (`??` 139, ` M` 100, `A ` 12, `M ` 8, `MM` 1, `AM` 1) | — | other sessions active |

### 1.1 Ownership of the target files

| File | Tracked | Status | Disposition |
| --- | --- | --- | --- |
| `backend_app/backend/redis_manager.py` | yes | **clean**, 21,497 B, 08-17 23:44:39 | read only — holds the R6 defect |
| `backend_app/core/cache/redis_manager.py` | yes | ` M`, 59,890 B, 10-07 14:17:34 | **another workstream's** — read only per the boundary |
| `tests/conftest.py` | yes | ` M`, 22,317 B, 10-08 13:51:48 | **another workstream's** — read only per the boundary |
| `tests/test_redis_debounce.py` | yes | ` M`, 16,634 B, **21:32:35** | the A22 session's in-flight work — **not touched** |

All four verified byte-identical at exit. `backend_app/backend/redis_manager.py` being
**clean and tracked** is worth stating: the file carrying the defect has no competing
edits, which is why option B in §4 needs no coordination.

---

## 2. Source call graph, re-derived from current code

Confirmed against the file, not quoted from prior reports.

```
consumer (12 modules import this manager directly)
  │
  ├─> get_redis_manager()                       :536
  │     ├─ FAST PATH  :548  if _redis_manager is not None and _redis_manager._connected:
  │     │                       return _redis_manager        ← R6 EXITS HERE
  │     ├─ module cooldown :552  _get_manager_last_failed / _RECONNECT_COOLDOWN (3.0s)
  │     ├─ lock.locked()   :556  → return _redis_manager (skip)
  │     └─ under lock      :559-576
  │           ├─ re-check  :561  (same _connected gate)
  │           ├─ cooldown  :565
  │           ├─ construct :570  _redis_manager = RedisManager()   (singleton __new__)
  │           └─ await _redis_manager.initialize()  :572
  │
  ├─> RedisManager.try_reconnect()              :184
  │     ├─ :192  if self._connected: return True              ← ALSO EXITS HERE
  │     ├─ :196  instance cooldown on self._last_failed_at
  │     ├─ :199  self._get_reconnect_lock(); :201 if lock.locked(): return self._connected
  │     └─ :205-213 under lock: re-check :206, cooldown :210, → await self.initialize()
  │
  ├─> RedisManager.initialize()                 :133
  │     ├─ 3 × aioredis.from_url(...) into LOCALS    :140, :148, :156
  │     ├─ 3 × await <local>.ping()                  :164-166
  │     ├─ commit to self.*                          :167-170
  │     ├─ self._connected = True                    :171
  │     └─ except: self._connected = False           :180
  │              self._last_failed_at = monotonic()  :181
  │              return False                        :182   ← locals dropped, NOT closed
  │
  ├─> RedisManager.close()                      :215
  │     └─ awaits .close() on the three clients; does NOT reset _connected, does NOT
  │        unbind self._cache/_queue/_events                  ← A22V-G3
  │
  └─> RedisManagerCompatProxy.__getattr__       :581
        re-imports the module global each call; falls through to _cache then _queue
        attributes; NEVER consults _connected

backend_app/core/cache/redis_manager.py  (OTHER WORKSTREAM, read-only here)
  └─ :489-496  "Delegates reconnection debouncing to get_redis_manager"
       from backend_app.backend.redis_manager import get_redis_manager
       mgr = await get_redis_manager()                     ← inherits R6 wholesale
     ~25 × `except Exception as e:` at :753-1016            ← where the runtime error dies
```

### 2.1 The R6 contract, confirmed

Every write to `_connected`, machine-extracted from the current file:

```
  122: self._connected: bool = False        (__init__)
  171: self._connected = True               (initialize, after 3 successful pings)
  180: self._connected = False              (initialize, except block)
```

Every read used as a gate:

```
  192: if self._connected:                              (try_reconnect, first line)
  202: return self._connected
  206: if self._connected:
  548: if _redis_manager is not None and _redis_manager._connected:   (fast path)
  561: if _redis_manager is not None and _redis_manager._connected:
```

**`_connected` is written only inside `initialize()`.** No command method clears it, and
neither does `close()`. So a runtime command failure disables **both** recovery entry
points, not just `get_redis_manager()`. The existing A22 report's call graph says the same;
this is independent confirmation from source rather than agreement by citation.

### 2.2 Blast radius

| Path | Count | Notable consumers |
| --- | --- | --- |
| `from backend_app.backend.redis_manager import …` | **12** | `core/global_safety.py`, `core/distributed_rate_limiter.py`, `core/load_protection.py`, `core/audit_trail.py`, `routers/exchange.py`, `routers/dag_tasks.py` |
| `from backend_app.core.cache.redis_manager import …` | **20+** | `core/credential_vault.py`, `core/dag_task_queue.py`, `core/distributed_idempotency.py`, `core/dependencies.py`, `backend/execution_guard.py`, `backend/master_executor.py` |

The second group reaches the defect through the delegation at `core/cache:496`, so
effectively **every Redis consumer** is affected — including safety-critical ones
(`global_safety`, `execution_guard`, `distributed_rate_limiter`).

---

## 3. Test outcomes

### 3.1 The existing R6 suite, independently re-run

No Redis available, verified first: `REDIS_URL` **UNSET**, `AERORA_TEST_REDIS_URL`
**UNSET**, TCP 127.0.0.1:6379 **closed**.

```
python -m pytest tests/test_redis_debounce.py -v --no-header -p no:cacheprovider
```

```
collected 10 items
test_thundering_herd_one_attempt[asyncio]                                       PASSED
test_reconnect_cooldown_respected[asyncio]                                      PASSED
test_reconnect_allowed_after_cooldown[asyncio]                                  PASSED
test_recovery_after_outage[asyncio]                                             PASSED
test_healthy_fast_path_skips_lock[asyncio]                                       PASSED
test_get_redis_manager_debounced[asyncio]                                       PASSED
test_r6_a_runtime_command_failure_is_real_and_leaves_health_unchanged[asyncio]   PASSED
test_r6_b_get_redis_manager_returns_the_stale_manager_without_attempting_a_rebuild  PASSED
test_r6_c_the_same_harness_detects_a_genuine_rebuild_attempt[asyncio]           PASSED
test_r6_d_desired_a_runtime_failure_should_be_recoverable[asyncio]              XFAIL

9 passed, 1 xfailed in 2.13s        EXIT 0
```

**Confirmed:** 6 pre-existing pass unchanged, 4 new behave as reported, **zero skips**,
server-free. Their reported 6.76 s versus my 2.13 s is warm-import variance, not a
behavioural difference.

The `xfail` reason string is itself the contract, and it is `strict=True`, so a future fix
turns this into an XPASS that pytest reports as a failure — the signal to update the test.
That is the right shape and I would not change it.

### 3.2 Three lifecycle gaps, proven by execution

`staging/probe_p11b_a22v_lifecycle_gaps.py` → `staging/p11b_a22v_lifecycle_gaps.txt`.
Replaces `aioredis.from_url` with a recorder that never opens a socket, then runs the
**real** `initialize()` and `close()`. Each check builds its manager via
`object.__new__` to bypass the singleton, so a result cannot be blamed on leaked state.

| ID | Gap | Evidence |
| --- | --- | --- |
| **G1** = their **F4** | **Partial-init leak.** Third client's `ping()` raises → 3 clients created, **0** closed. At one retry per 3.0 s cooldown, a persistent outage leaks **3 pools per attempt**. | `clients created 3`, `TOTAL close/aclose calls 0`, `LEAKED 3`, `_connected False` |
| **G2** = their **F3** | **Rebuild leak.** A *successful* second `initialize()` replaces `self._cache/_queue/_events`; the previous three are **never closed**. | `6 created`, `self._cache replaced True`, `close/aclose on the FIRST 3: 0` |
| **G3** — **new** | **`close()` does not reset health.** After `await close()`, `_connected` is still **True** and the clients are still bound. | `_connected before True → after True`, `self._cache still bound True`, `.close() 3 calls`, `.aclose() 0 calls` |

G1 and G2 were listed in the existing report's uncertainties as *"source-derived and
unreproduced… strong but not executed."* They are now executed.

**G3 matters on its own.** Because `close()` leaves `_connected = True`:

- `get_redis_manager():548` returns the manager as healthy after an orderly shutdown;
- `try_reconnect():192` returns `True` without reconnecting;
- `RedisManagerCompatProxy` hands out methods bound to closed clients.

That is the identical never-reset-flag mechanism as R6, reached through shutdown rather
than failure. Any health-invalidation design must cover `close()` too, or the fix leaves
half the hole open. Separately, `close()` calls the deprecated `.close()` rather than
`.aclose()` — cosmetic beside G3, but it lands in the same edit.

---

## 4. Design decision

### Option A — invalidate health at the point of runtime failure

Set `_connected = False` (and `_last_failed_at`) when a command raises a connection-class
error, then let the existing debounced path recover.

| Dimension | Assessment |
| --- | --- |
| Locking / cooldown / concurrent rebuilds | **Reuses what exists.** `try_reconnect`'s instance lock + `get_redis_manager`'s module lock, both with `lock.locked()` skip and double-check. No new primitive. |
| Partial init & old-pool cleanup | **Makes G1/G2 worse** — invalidation is what *causes* rebuilds, so every runtime blip leaks 3 pools. **G1/G2 must be fixed in the same change, not after.** |
| Recovery from real connection loss | **Yes — this is the only option that does.** |
| Effect on consumers | All 12 direct + 20+ indirect benefit with no call-site change. |
| Core cache proxy participation | **Required.** The exception is caught in ~25 handlers at `core/cache/redis_manager.py:753-1016` — **another workstream's file**. Without their change, nothing sets the flag. |
| Rebuild storms | Bounded by the existing 3.0 s cooldown. But **two independent cooldowns** exist (`self._last_failed_at` vs `_get_manager_last_failed`), so the effective rate depends on which entry point is used — their F5, unreproduced, and worth pinning before relying on it. |
| **Blocker** | **Cross-workstream coordination.** Cannot be done inside the clean file alone. |

### Option B — rebuild when the current event loop differs from the pool-creating loop

Record the loop that created the pools; on mismatch, treat as stale and rebuild.

| Dimension | Assessment |
| --- | --- |
| Locking / cooldown | Same reuse as A. |
| Partial init & old-pool cleanup | Same exposure; same prerequisite. |
| Recovery from real connection loss | **No.** A connection lost inside the *same* loop presents an identical loop identity. **Does not fix R6.** |
| Effect on consumers | Fixes the cross-loop class — which is what A19's CI setup errors look like. |
| Core cache proxy participation | **Not required.** |
| Rebuild storms | Low: loop identity changes rarely. |
| **Advantage** | **Implementable entirely within `backend_app/backend/redis_manager.py`, which is tracked and clean. No coordination.** |

### Option C — combined

Loop-identity rebuild **plus** runtime health invalidation.

| Dimension | Assessment |
| --- | --- |
| Coverage | **The only option covering both failure classes.** |
| Cleanup | Highest rebuild frequency → G1/G2 are hard prerequisites. |
| Core cache proxy | Required, for the A half. |
| Cost | Two mechanisms, two cooldown interactions, the largest review surface. |

### Recommendation

**A pre-step first, then C — and the pre-step is not optional.**

1. **Step 0 (no coordination, clean file): fix G1, G2, G3.** Close the three locals in
   `initialize()`'s `except`; close the outgoing clients before rebinding on success;
   have `close()` set `_connected = False` and unbind. Every option above increases how
   often `initialize()` runs, so shipping any of them before this converts a stale-manager
   defect into a connection-pool exhaustion defect. **G3 additionally closes a second,
   independent route to the R6 symptom.**
2. **Step 1 (no coordination): option B.** Entirely inside the clean file. Addresses the
   cross-loop class that A19 observed in CI. Does **not** close R6.
3. **Step 2 (coordination required): option A, giving C.** Needs the
   `core/cache/redis_manager.py` owner to invalidate health in their handlers. This is the
   step that actually closes R6 and flips `test_r6_d` to XPASS.

Ranking note: the existing A22 report ranks B above A on the coordination argument, which
is correct as far as it goes. The leak evidence in §3.2 adds that **neither should ship
before Step 0**, which the unreproduced framing of F3/F4 did not establish.

### Regression evidence required before any step

| Step | Focused | Full suite |
| --- | --- | --- |
| 0 | new tests: init-failure closes all 3 locals; successful rebuild closes the previous 3; `close()` clears health and unbinds. Plus the 10 in `test_redis_debounce.py`. | required — pool lifecycle touches every consumer |
| 1 | loop-identity rebuild on mismatch; **no** rebuild on match (control) | required |
| 2 | `test_r6_d` XPASS → rewrite as a passing contract test and delete the marker; invalidation is idempotent; cooldown still debounces; the quota and webhook real-Redis gates are unaffected | required, plus the real-Redis lane, which remains **UNEXECUTED** |

---

## 5. Unresolved ownership decisions

| # | Decision | Owner | Status |
| --- | --- | --- | --- |
| 1 | Approve Step 0 (pool cleanup + `close()` health reset) in `backend_app/backend/redis_manager.py` | owner of that file — **tracked and clean, no competing edits** | **NOT GRANTED** |
| 2 | Approve option B in the same file | same | **NOT GRANTED** |
| 3 | Invalidate health in the ~25 handlers at `core/cache/redis_manager.py:753-1016` | **owner of `backend_app/core/cache/redis_manager.py`** (` M`, in flight) | **NOT GRANTED — the blocker for closing R6** |
| 4 | Reconcile the two cooldowns (their F5, still unreproduced) | both owners | **NOT GRANTED** |
| 5 | Who owns `tests/test_redis_debounce.py` going forward | the A22 session | **open** — I did not edit it |

---

## 6. Exact next implementation step

> In `backend_app/backend/redis_manager.py` **only** — tracked, clean, no competing edits —
> make `initialize()` and `close()` pool-safe, with a server-free test per gap:
>
> 1. `initialize()`'s `except` closes the three locally created clients before returning
>    `False`, swallowing secondary close errors so the original failure is still reported;
> 2. on success, close the outgoing `self._cache/_queue/_events` before rebinding;
> 3. `close()` sets `_connected = False` and unbinds the three, and uses `aclose()` where
>    available.
>
> This needs **no cross-workstream coordination**, is provable with the recorder harness in
> `staging/probe_p11b_a22v_lifecycle_gaps.py`, and is a prerequisite for options A, B and C
> alike. It does **not** close R6.

---

## 7. Integrity and scope

- **No production source changed.** `backend_app/backend/redis_manager.py` byte-identical
  (21,497 B, 08-17 23:44:39, clean).
- **Ownership boundary respected.** `backend_app/core/cache/redis_manager.py` and
  `tests/conftest.py` read only; both still ` M` at their owners' sizes and mtimes.
- **No test added or changed.** `tests/test_redis_debounce.py` byte-identical at
  16,634 B / 21:32:35 — the A22 session's work, preserved.
- **No file overwritten.** `PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` untouched at
  sha256 `40795159…950F`.
- **Nothing staged.** Index tree `6f40856c…`, staged digest `2e994018…`, both identical to
  entry. Shared object count 198 → 198; index verification ran against a copied index in
  an isolated object directory and wrote **0** objects anywhere.
- **No external calls.** No Redis, no database, no network. Redis endpoints confirmed
  unset and port 6379 closed before the test run.
- **No commit, push, PR action, workflow trigger or deployment.**

Artifacts created, all new and all under `staging/`:
`probe_p11b_a22v_lifecycle_gaps.py`, `p11b_a22v_lifecycle_gaps.txt`, and this report.

---

## Status

| | |
| --- | --- |
| R6 | **REPRODUCED and independently verified.** Contract pinned by `test_r6_d` (`xfail`, `strict=True`). **NOT FIXED** — no production change authorized or made. |
| A22V-G1 / G2 (= A22 F3 / F4) | **CONFIRMED BY EXECUTION**, upgraded from source-derived |
| A22V-G3 (`close()` leaves health True) | **NEW, CONFIRMED** |
| A22 F5 (two cooldowns) | **still unreproduced** |
| Real-Redis lane | **UNEXECUTED** |
| Live-database verification | **NOT PERFORMED** |
| TENANT-01 / QUOTA-01 / D5 Redis workstream | **untouched** |
| Live trading | **OFF / NOT CERTIFIED** |

Skipped tests are never counted as passes. No approval was inferred from a recommendation
or from the existence of a plan.
