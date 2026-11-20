# Phase 11B-A23B — Redis Recovery Coverage and Authorization Reconciliation

**Destination check:** `staging/PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` verified **ABSENT**
before writing. No existing report overwritten.

**READ-ONLY. No production edit, no test edit, no dependency change, no staging, no commit,
no push, no database or exchange call, no deployment.** Live trading **OFF / NOT CERTIFIED**.

---

## HEADLINE

**Every approval in this area is NOT GRANTED or UNCLEAR. Nothing is implementable today.**

Three results from source that change the shape of the decision:

1. **The compat proxy lives in the same clean file as `get_redis_manager()`.** `RedisManagerCompatProxy`
   is defined at `backend_app/backend/redis_manager.py:581-591` — the file that is *tracked
   and clean*. So covering Path B is a **scope question for one owner**, not a
   cross-workstream coordination problem. **This corrects the framing in my own A23 report**,
   which implied the proxy gap needed a different owner. §3.2
2. **The proxy's real hazard is not only that it skips recovery — it hands out bound methods
   of a possibly-dead client**, and being synchronous it cannot await a rebuild. Any fix
   changes its contract, and its largest consumer `core/global_safety.py` is itself ` M`
   **+653 / −70**. That is where the coordination risk actually sits. §3.2
3. **A22V's sequencing conclusion is confirmed directly from source, not inherited.** `initialize()`
   leaks on both the failure and the success path, and `close()` never resets health. Any
   change that makes `initialize()` run more often — which is every proposed fix — converts
   a stale-manager defect into a pool-exhaustion defect. **Step 0 is a hard prerequisite.** §2

---

## 1. Baseline and files examined

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` divergence | `0 / 0` |
| **Canonical staged digest** | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` |
| Staged paths | **22** |
| `git write-tree` (isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4`, **0 objects** written |
| Unstaged / untracked / worktrees | 102 / 3,534 / **1** |

### 1.1 Ownership, measured live

| File | Status | numstat | Bearing |
|---|---|---|---|
| **`backend_app/backend/redis_manager.py`** | **clean** | — | 593 lines, mtime 08-17. **Hosts both reuse paths and the proxy.** No competing edit |
| `backend_app/core/cache/redis_manager.py` | ` M` | +185 / −5 | F5 workstream. Where runtime exceptions are caught |
| **`backend_app/core/global_safety.py`** | ` M` | **+653 / −70** | **the kill switch — heavily in flight**. 11 proxy call sites |
| `tests/test_redis_debounce.py` | ` M` | +225 / −0 | A22's. Hosts the R6 strict xfail. **Ownership recorded as "open"** |
| `tests/conftest.py` | ` M` | +172 / −0 | other workstream |
| `pytest.ini` | ` M` | +13 / −0 | concurrent |

Reports read: `PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md`,
`PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md`,
`PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md`.

---

## 2. Approval matrix — each item independently

Authoritative record: `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` §5.

| # | Item | File | Owner | Status | Evidence |
|---|---|---|---|---|---|
| 1 | **Pool lifecycle repair — G1 / G2 / G3** ("Step 0") | `backend/redis_manager.py` (clean) | that file's owner | **NOT GRANTED** | A22V §5 row 1 |
| 2 | **Loop-identity recovery** (option B / B-2′) | same file | same | **NOT GRANTED** | A22V §5 row 2; A23 §2 |
| 3 | **Runtime-health invalidation in the core-cache proxy** (~25 handlers, `core/cache/redis_manager.py:753-1016`) | `core/cache/redis_manager.py` (` M`) | F5 owner | **NOT GRANTED — recorded as the blocker for closing R6** | A22V §5 row 3 |
| 4 | **Cooldown behaviour changes** (A22 F5, the two-cooldown reconciliation) | both files | both owners | **NOT GRANTED** | A22V §5 row 4 |
| 5 | **Test-file ownership / creation** | `tests/test_redis_debounce.py` | A22 session | **UNCLEAR — "open"** | A22V §5 row 5: *"Who owns `tests/test_redis_debounce.py` going forward … open — I did not edit it"* |

Searched `staging/*.md` for a grant (`approv`, `APPROVED`, `GRANTED`, `Decision`). Every hit
is a request or a recommendation. **No grant exists for any of the five.**

### 2.1 G1 / G2 / G3 re-confirmed from source, independently

A22V proved these by execution; I confirmed each from the current file so the sequencing
argument does not rest on a single session.

| ID | Defect | Source evidence |
|---|---|---|
| **G1** | **Partial-init leak.** `initialize()` creates three clients and `await`s `ping()` on each (`:162-164`) *before* committing. The `except Exception` (`:178-182`) sets `_connected = False` and `_last_failed_at`, then `return False` — **it never closes the three locals.** At one retry per 3.0 s cooldown a persistent outage leaks 3 pools per attempt | `:133-182` |
| **G2** | **Rebuild leak.** A *successful* re-`initialize()` rebinds `self._cache/_queue/_events` at `:168-170` **without closing the outgoing three** | `:168-170` |
| **G3** | **`close()` does not reset health.** `close()` (`:215-222`) awaits `.close()` on the three clients and **neither sets `_connected = False` nor unbinds them**. It also uses the deprecated `.close()` rather than `.aclose()` | `:215-222` |

**Why Step 0 is not optional.** `_connected` is written at exactly three places — `:122`
(False at construction), `:171` (True), `:180` (False). Every proposed fix increases how
often `initialize()` runs. Shipping any of them before G1/G2 are closed trades a
stale-manager defect for connection-pool exhaustion. G3 is independent of that and closes a
*second* route to the R6 symptom on its own (§4).

---

## 3. The two reuse paths, and their distinct risks

### 3.1 Path A — `get_redis_manager()` (`:536-575`)

**How runtime command failures reach the manager: they do not.** The class exposes **21**
`async def` command methods; each wraps its call in its own `except Exception`, logs, and
returns a fallback (`:248, 262, 276, 285, 318, 340, 348, 361, 370, 387, 397, 421, …`).
**None of them writes `_connected`.** So after a command fails, `_connected` is still `True`
and the fast path

```python
if _redis_manager is not None and _redis_manager._connected:
    return _redis_manager          # :547-549
```

returns the broken manager for the remaining life of the process. **That is R6, confirmed
from source.**

**Can loop identity be checked before the healthy fast path?** **Yes.** `get_redis_manager`
is `async def`, so `asyncio.get_running_loop()` is available at `:547`. Nothing in the
module records a loop today (`get_event_loop` / `get_running_loop`: **0 occurrences**), so
the check would be new state.

**Locking / cooldown / init / cleanup interactions, as they exist:**

| Mechanism | Location | Note |
|---|---|---|
| Module cooldown | `_get_manager_last_failed` + `_RECONNECT_COOLDOWN` (3.0 s, env `REDIS_RECONNECT_COOLDOWN`) | `:552-554`, re-checked inside the lock |
| Module lock | `_ensure_get_manager_lock()`, `lock.locked()` skip + double-check | `:556-566` |
| Instance cooldown | `self._last_failed_at` + same constant | `try_reconnect` `:196`, `:210` |
| Instance lock | `_get_reconnect_lock()`, same skip + double-check shape | `:204-213` |
| Reconstruction | **`initialize()` only. There is no `connect()`** (0 matches) | reachable via `try_reconnect()` or `get_redis_manager()` |
| Cleanup on rebuild | **none** (G1, G2) | — |

**A22 F5 confirmed in shape:** `get_redis_manager` calls `await _redis_manager.initialize()`
at `:570` **directly**, bypassing `try_reconnect()` and therefore the *instance* cooldown.
Two independent cooldowns govern the same reconstruction. Still unreproduced; §2 item 4.

### 3.2 Path B — `RedisManagerCompatProxy.__getattr__` (`:581-591`)

```python
class RedisManagerCompatProxy:
    def __getattr__(self, name):                       # synchronous
        from backend_app.backend.redis_manager import _redis_manager
        if _redis_manager is not None:
            if hasattr(_redis_manager, name):
                return getattr(_redis_manager, name)           # bound to the manager
            if _redis_manager._cache is not None and hasattr(_redis_manager._cache, name):
                return getattr(_redis_manager._cache, name)    # bound to the CLIENT
            if _redis_manager._queue is not None and hasattr(_redis_manager._queue, name):
                return getattr(_redis_manager._queue, name)
        raise AttributeError(...)
redis_manager = RedisManagerCompatProxy()               # :593
```

**How consumers obtain methods.** `core/global_safety.py` does
`from backend_app.backend.redis_manager import redis_manager` and then
`await redis_manager.<method>(...)` at **11 sites** — `:244 get`, `:255 setex`, `:257 set`,
`:266 lpush`, `:275 lrange`, `:284 expire`, `:315 smembers`, `:334 sadd`, `:351 srem`,
`:370 set` (transition-lock acquire), `:384 delete` (release). Same pattern in
`core/audit_trail.py`, `core/alerting_system.py`, `core/load_protection.py`,
`core/distributed_rate_limiter.py`, `backend/exchange_websocket_listener.py`,
`routers/dag_tasks.py`; `core/rate_limiter.py:179-180` reads `_connected` / `.cache` directly.

**Two distinct risks, not one:**

| Risk | Detail |
|---|---|
| **B-i — recovery is never triggered** | `__getattr__` reaches `_redis_manager` directly and **never calls `get_redis_manager()`**. A loop-identity or health check added to Path A does nothing here |
| **B-ii — stale bindings are handed out** | Resolution returns a **bound method**, possibly bound to `_cache` itself. After a rebuild replaces `_cache`, any already-resolved method still targets the old client. In `global_safety.py` the access-then-await is immediate, so the window is one call — but the *resolution* still bypasses recovery entirely, and after `close()` it resolves to a **closed** client (G3) |

**Can a synchronous proxy safely detect stale loop ownership?** **Not usefully.**

* `asyncio.get_running_loop()` raises outside a coroutine, and `__getattr__` runs at
  attribute access — which is inside the caller's coroutine in the `await redis_manager.x()`
  shape, but is **not guaranteed** to be in general.
* Even on detection it **cannot rebuild**: `initialize()` is `async`, and `__getattr__`
  cannot await. Blocking (`run_until_complete`) inside a running loop is an error.
* So the only safe shapes are (a) **raise** on a stale/closed client — fail-closed, but a
  behaviour change for ~12 consumers including the kill switch, or (b) return an **async
  wrapper** that performs the check and recovery at *await* time, which is a contract change
  to the proxy.

**Ownership refinement.** The proxy is in the **clean** file, so (a) and (b) need the *same*
single approval as Path A — a scope decision, not a second coordination. The genuine
coordination risk is that its biggest consumer, `global_safety.py`, is ` M` **+653 / −70**:
changing proxy semantics underneath an in-flight kill-switch rewrite is the hazard.
**Not changed here.**

### 3.3 Path C — shutdown and reconstruction

```
close()  :215-222   awaits .close() on _cache/_queue/_events
                    does NOT set _connected = False        <-- G3
                    does NOT unbind self._cache/_queue/_events
          (no connect() exists; reconstruction is initialize() only)
```

**Composite consequence, independent of any runtime failure:** after an orderly
`await close()`, `_connected` is still `True`, so

* `get_redis_manager()`'s fast path (`:547-549`) returns the **closed** manager as healthy;
* the compat proxy resolves methods on the **closed** `_cache`;
* `try_reconnect()` short-circuits at `:192` (`if self._connected: return True`) and never
  rebuilds.

So **shutdown-then-reuse reproduces the R6 symptom by a second, independent route** — and
it is reachable without any connection fault at all. A health-invalidation design that
covers only command failures leaves this half open; `close()` must participate.

**How health invalidation and unbinding must interact with the proxy.** If `close()` sets
`_connected = False` *and* unbinds the three clients to `None`, then the proxy's
`hasattr(_redis_manager, name)` branch still returns manager methods (which will fail
internally on `self.cache is None`), and the `_cache`/`_queue` fall-through branches stop
matching and the proxy raises `AttributeError`. **That is a behaviour change for the 11
`global_safety.py` sites** — from "silently use a dead client" to "raise". Arguably correct
and fail-closed, but it must be a decided change, not a side effect. This is precisely why
Step 0's `close()` half and the proxy question should be reviewed together.

---

## 4. Which phase fixes which failure class

| Failure class | Observed as | Fixed by |
|---|---|---|
| **Cross-loop reuse** | A19 CI webhook setup errors — `Cache SET failed … Event loop is closed` | **option B** (loop identity), Path A callers only |
| **Same-loop runtime connection loss (R6)** | `_connected` stays `True`; broken manager returned forever | **option A** (health invalidation at the catch site) — needs F5 |
| **Shutdown-then-reuse (G3)** | closed clients handed out as healthy | **Step 0** `close()` half |
| **Pool exhaustion under retry (G1/G2)** | 3 pools leaked per failed attempt; 3 per successful rebuild | **Step 0** |
| **Direct proxy access (B-i / B-ii)** | kill switch etc. never recover, and can hold stale bindings | **none of the above as scoped** — needs an explicit proxy decision |
| **Duplicate init storms (F5)** | two cooldowns governing one reconstruction | cooldown reconciliation — unreproduced |

**No single approved step closes R6.** Option B alone does not (same loop ⇒ same identity).
Option A alone leaves G1/G2 worse and G3 open. Step 0 alone fixes no stale-manager class but
is a prerequisite to both.

---

## 5. Test acceptance matrix

The R6 strict xfail was re-run read-only for Phase 0 (§6). **`tests/test_redis_debounce.py`
was not edited** — ownership is "open" per A22V §5 row 5.

Current meaning of `test_r6_d_desired_a_runtime_failure_should_be_recoverable[asyncio]`,
from its own marker: *a runtime command failure should make the manager recoverable;
`_connected` is written only by `initialize()`, so a failed command leaves it True and the
fast path returns the broken manager.* **It contracts option A, not loop identity** —
so option B would leave it xfailing. (This restates the correction already recorded in A23 §3.4.)

| # | Acceptance criterion | Current coverage | Proposed file | Owner | Harness | Approval needed |
|---|---|---|---|---|---|---|
| 1 | **Failed init closes all three locally created clients** (G1) | **none** — A22V proved the leak by probe, not by a committed test | new `tests/test_redis_manager_pool_lifecycle.py` | implementer of Step 0 | fake client (`AsyncMock`), third `ping()` raises; assert 3 created / 3 closed | #1 + #5 |
| 2 | **Successful replacement closes the outgoing three and not the new three** (G2) | **none** | same | same | fake client; two `initialize()` calls; assert close-count on the first trio only | #1 + #5 |
| 3 | **`close()` invalidates health and unbinds** (G3) | **none** | same | same | fake client; assert `_connected is False` and `_cache/_queue/_events is None`; assert `aclose` preferred | #1 + #5 |
| 4 | **Cross-loop reuse recovers via the async manager path** | **none** | new `tests/test_redis_manager_loop_identity.py` | implementer of option B | **two successive `asyncio.run()` calls**; assert the second does not reuse the first loop's client | #2 + #5 |
| 5 | **Healthy same-loop access does not rebuild** (control for 4) | **none** | same | same | same loop, two calls; assert `initialize` awaited **0** extra times | #2 + #5 |
| 6 | **Same-loop runtime failure triggers recovery** (R6) | **`test_r6_d` — xfail(strict), server-free, PRESENT** | reuse `tests/test_redis_debounce.py`; convert xfail→pass when option A lands | **A22 session — UNCLEAR** | existing `AsyncMock` harness | #3 + #5 |
| 7 | **The proxy cannot silently hand out unusable clients after failure or shutdown** | **none** | new `tests/test_redis_compat_proxy_contract.py` | proxy-scope decider | fake client; after `close()`, assert the proxy raises or recovers — **not** that it returns a dead bound method | proxy scope decision + #5 |
| 8 | **Cooldown stays bounded; concurrent callers do not storm init** | partial — existing debounce tests in `test_redis_debounce.py` cover the module path | reuse + extend | A22 / implementer | `asyncio.gather` of N callers; assert `initialize` awaited once | #4 + #5 |
| 9 | **Composite `close()` → reconstruct works** | **none** | `tests/test_redis_manager_pool_lifecycle.py` | implementer of Step 0 | fake client; `close()` then `get_redis_manager()`; assert a *new* trio and `_connected True` | #1 + #5 |

**All nine are server-free** — fake clients and isolated event loops only. None requires a
real Redis. That matters: the real-Redis webhook gate (6 tests) stays **UNEXECUTED** and is
a *separate* integration question, not a substitute for any row above.

**Tests must prove behaviour, not flags.** Rows 1-3 and 9 assert close/create **counts** on
the client objects, not `_connected` alone; row 5 asserts an `await_count`; row 7 asserts
the caller-visible outcome. A22's harness already demonstrates the distinction — its control
test observes `initialize` awaited exactly once so that "no rebuild" cannot be confused with
"the patch did not take".

---

## 6. Verification performed this phase

Read-only. No implementation exists to verify.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `pytest tests/test_redis_debounce.py -p no:randomly -q --no-header -rxX` (A23, unchanged since) | **0** | **9 passed, 1 xfailed**; server-free |

**Real-Redis gates: NOT RUN.** `AERORA_TEST_REDIS_URL` unset, no server. The six webhook
duplicate-delivery tests remain opt-out skips. **A skip is not a pass.** No Redis started, no
database, no exchange, no CI, no network call.

---

## 7. Recommended sequence, and the non-goals

**Recommendation — unchanged in order from A22V, now with independent source confirmation
and one addition.**

| Step | Content | Coordination | Gate |
|---|---|---|---|
| **0** | **G1 + G2 + G3** — close the three locals in `initialize()`'s `except`; close the outgoing trio before rebinding on success; have `close()` set `_connected = False`, unbind, and prefer `aclose()` | **none** — clean file | rows 1, 2, 3, 9 |
| **0b** | **Decide the proxy's contract** (§3.2) — raise, or async-wrap, or explicitly accept the gap | **none for the file**; but **must be agreed with the `global_safety.py` owner** (` M` +653/−70) before landing | row 7 |
| **1** | **Option B** — loop identity at `:547` | none — same clean file | rows 4, 5 |
| **2** | **Option A** — health invalidation at the catch site | **F5 owner required** | row 6 (`test_r6_d` → pass) |
| **3** | Cooldown reconciliation (F5) | both owners | row 8 |

**Step 0 before anything else, and 0b decided before Step 1 lands.** Steps 1-3 all raise
initialization frequency; without Step 0 that converts a stale-manager defect into pool
exhaustion. 0b is new here: Step 0's `close()`-unbind half **changes what the proxy returns**
(§3.3), so the proxy decision cannot trail behind it.

**Explicit non-goals.**

* **Do not ship Step 0 + option B as "Redis recovery".** They do not close R6 — a same-loop
  connection loss is untouched. R6 closes only with Step 2, which is blocked on F5.
* Do not claim the cross-loop fix covers the compat-proxy consumers (§3.2 B-i).
* Do not treat a server-free lifecycle test as D5′ acceptance, or as evidence about the six
  real-Redis webhook tests.
* No change to `tests/conftest.py`, `pytest.ini`, loop scope, quota semantics, or the
  webhook keyspace-probe assertions.
* No edit to `tests/test_redis_debounce.py` while its ownership is "open".

---

## 8. Blockers and the next authorization decision

| # | Blocker | Owner | Status |
|---|---|---|---|
| **1** | Step 0 (G1/G2/G3) | `backend/redis_manager.py` owner | **NOT GRANTED** |
| **2** | Proxy contract decision (0b) | same owner **+** `global_safety.py` owner | **not yet asked — raised here** |
| **3** | Option B | same owner | **NOT GRANTED** |
| **4** | Option A — health invalidation in ~25 core-cache handlers | F5 owner | **NOT GRANTED — the R6 blocker** |
| **5** | Cooldown reconciliation | both | **NOT GRANTED** |
| **6** | Where new tests live, given `test_redis_debounce.py` ownership is "open" | A22 session + coordinator | **UNCLEAR** |
| **7** | Real-Redis lane for the webhook half | coordinator / release owner | **not held** |

**Next authorization decision: blocker 1 together with blocker 6.** Step 0 is the only item
that needs a single owner, touches a clean file, fixes defects confirmed twice
independently, and is a prerequisite to everything else — but it cannot be *verified* until
row 1-3/9 tests have a sanctioned home, which is blocker 6. Asking for them together avoids
approving code that then has nowhere to be tested.

Blocker 2 should be put to both owners at the same time, because Step 0's `close()`-unbind
changes proxy behaviour whether or not anyone intends it to.

---

## 9. Preservation

| Check | Entry | Exit |
|---|---|---|
| HEAD / branch | `074b772a` / `main` | **identical** |
| Canonical staged digest | `2e994018…2767152` | **identical** |
| Staged paths | 22 | **22** |
| `git write-tree` (isolated) | `6f40856c…` | **identical**, **0 objects** written |
| Unstaged | 102 | **102** |
| Worktrees | 1 | **1** |
| `backend/redis_manager.py` | clean | **clean — unchanged** |
| `core/cache/redis_manager.py` | ` M` +185/−5 | **unchanged** |
| `core/global_safety.py` | ` M` +653/−70 | **unchanged** |
| `tests/test_redis_debounce.py` | ` M` +225/−0 | **unchanged** |
| `tests/conftest.py` / `pytest.ini` | ` M` +172/−0 / +13/−0 | **unchanged** |

Confirmed **NOT** done: production edit, test edit, dependency change, staging, commit,
push, reset, stash, branch switch, shared-index mutation, PR change, CI trigger, Redis
start, database access, exchange call, deployment, report overwrite. The only new file is
this report.

---

## 10. Status

| Item | Status |
|---|---|
| R6 (same-loop runtime failure) | **OPEN** — contract pinned by `test_r6_d`; blocked on F5 |
| Cross-loop reuse (A19 webhook class) | **OPEN** — unapproved; and Path-A-only in coverage |
| G1 / G2 (pool leaks) | **CONFIRMED** by A22V execution **and** by source here. **OPEN** |
| G3 (`close()` leaves health True) | **CONFIRMED**. **OPEN** — second independent route to the symptom |
| Compat-proxy gap (B-i / B-ii) | **OPEN** — decision not yet requested before this report |
| F5 (two cooldowns) | shape confirmed in source (`:570` bypasses `try_reconnect`); **unreproduced** |
| Real-Redis webhook lane | **UNEXECUTED** |
| D5′ acceptance | **NOT CLAIMED** |
| QUOTA-01 / TENANT-01 | **OPEN** / **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

---

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · R6 **OPEN** · G1/G2/G3 **OPEN** · D5′ acceptance
**NOT CLAIMED** · real Redis **NOT USED** · no CI triggered · **no file changed** · live
trading **OFF / NOT CERTIFIED**
