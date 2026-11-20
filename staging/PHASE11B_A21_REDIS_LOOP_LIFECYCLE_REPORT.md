# Phase 11B-A21 — Redis Event-Loop Lifecycle: Ownership Coordination and B-1 Validation

> ## CORRECTION NOTICE — added 2026-10-11 by the authoring session. Discharges D-A23C-11.
>
> **§4 (option table), §5.1 and §6 (R1) of this report are WRONG on one point: the strict
> xfail `test_r6_d_desired_a_runtime_failure_should_be_recoverable` does NOT gate B-2′.**
>
> That xfail contracts **runtime-health invalidation** (A22V's *option A*) — its own marker
> reason states that `_connected` is written only by `initialize()`, so a failed *command*
> leaves it `True`. It is **not** a loop-identity contract. A22V's matrix is explicit that
> option B "**Does not fix R6**", because a connection lost inside the same loop presents an
> identical loop identity.
>
> **Therefore B-2′ has no existing server-free test gate**, and the advantage this report
> builds on that premise — "verifiable without Redis", the basis of the R1 recommendation —
> **does not hold.** A new loop-identity test would be required, and its home is itself
> undecided.
>
> Already corrected in `staging/PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` §3.4 and
> restated in `staging/PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` §5. Recorded as open item
> **D-A23C-11** in `staging/PHASE11B_A23C_REDIS_RECOVERY_CONSOLIDATION.md:334` and
> `staging/PHASE11B_A23D_P1_AUTHORIZATION_HANDOFF.md`, where it is noted as uncorrectable by
> those sessions because this is another session's report. **This notice closes it.**
>
> The text below is preserved unaltered as the original record.

**Destination check:** `staging/PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md` verified
**ABSENT** immediately before writing. No existing report overwritten.

---

## HEADLINE

**B-1 was NOT implemented. `tests/conftest.py` was not edited.** Three independent blockers
fired, any one of which is sufficient — and the third is the one that matters most
technically.

1. **No ownership approval exists.** The only B-1 reference in the repository is my own
   recommendation in `staging/PHASE11B_A20Q_QUOTA_REFUSAL_AND_LOOP_LIFECYCLE.md`, which the
   brief explicitly rules out as approval. §1.
2. **B-1 as specified cannot be implemented on existing lifecycle contracts.** The seam
   fixture is **synchronous**; every close/disconnect on the manager is a coroutine; and
   `SharedRedisManager` exposes **no** synchronous reset. Doing it anyway means inventing a
   manager contract, which §3 forbids. §3.
3. **B-1 could not be validated here even if written.** All six webhook tests **SKIP**
   locally — the opt-in gate declines before any client is built, so the defect is
   unreachable without a real Redis. A fix to another workstream's shared file that cannot
   be exercised is exactly the wrong thing to merge. §5.

Additionally, a concurrent workstream has already recorded a position on this:
`staging/PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` states that for
`tests/conftest.py`, *"loop-scope and pool-teardown changes are explicitly out of scope and
were rejected as fixes"*. **So B-1 is contested, not merely unapproved.**

One in-scope change was made, to my own prior work: the 35-vs-36 citation A22 asked me to
reconcile (§4).

| Status | |
|---|---|
| B-1 code change | **NOT IMPLEMENTED** |
| B-1 locally tested | **N/A — not implementable, and not locally verifiable** |
| Real-Redis webhook assertions | **0 of 6 executed. 6 SKIPPED. A skip is not a pass** |
| D5′ acceptance | **UNVERIFIED, NOT CLAIMED** |
| QUOTA-01 | **OPEN** — the A20 refusal fix is narrower than closure |
| TENANT-01 | **OPEN** |
| Exchange-slot concurrency | **UNPROVEN** |
| Live trading | **OFF / NOT CERTIFIED** |

---

## 1. Authorization and ownership gate — FAILED

Re-measured live; no historical numstat was trusted.

| File | Status | numstat | Owner | Action |
|---|---|---|---|---|
| `tests/conftest.py` | ` M` | **+172 / −0** | another workstream; A22 records teardown as out of scope | **not edited** |
| `backend_app/core/cache/redis_manager.py` | ` M` | **+185 / −5** | F5 workstream | **not edited** |
| `pytest.ini` | ` M` | **+13 / −0** | concurrent | **not edited** |
| `backend_app/backend/redis_manager.py` | **clean** | — | unowned, but A22 notes *"clean is not authorization"* | **not edited** |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | — | webhook owner | **not edited** |
| `backend_app/core/subscription_engine.py` | ` M` | +57 → **+61 / −4** | **mine** (A20) | comment reconciliation only, §4 |

**Approval search.** Grepped `staging/PHASE11B_A2*.md` and the wider repository for
`B-1`, `conftest.*approv`, `approv.*conftest`, `APPROVED`, `GRANTED`. Every hit is either
my own A20Q recommendation/request table, or A22's explicit out-of-scope note. **No
approval record exists from any owner.**

---

## 2. Read-only investigation — the compact call graph

```
pytest-asyncio  mode=STRICT,  asyncio_default_fixture_loop_scope UNSET
   -> every test gets a FRESH function-scoped event loop; the previous one is CLOSED
          |
backend_app/core/cache/redis_manager.py
   SharedRedisManager.__new__            :476-481   cls._instance cached once
      cls._instance._initialized = False :479       set ONLY on first construction
   __init__ guard                        :483-485   `if self._initialized: return`
      => the instance, and the async client/pool it built, SURVIVE every test
   _get_active_cache()                   :676       hands back that same cached client
   set() proxy                           :~660      except -> log WARNING, return False
          |
   loop-bound objects:  the async redis client and its ConnectionPool
                        (bound to the loop that first constructed them)
          |
tests/conftest.py
   _REDIS_SINGLETON_SEAMS                :82-88     ("_test_mode", "_dev_mode_bypass",
                                                     "_redis_manager", "_mock_client")
   _restore_shared_redis_singleton       :90-116    *** SYNCHRONOUS *** autouse fixture
      - snapshots/restores MODULE __dict__ entries by reference
      - does NOT touch SharedRedisManager._instance or _initialized
      - cannot await: close():299, close():441, disconnect():616 are all `async def`
          |
tests/test_webhook_duplicate_delivery_real_redis.py
   real_redis_required / _real_redis_is_usable / real_redis_cache  -> gate passes
   redis_client fixture :~501  -> await _prove_one_keyspace(manager, client)
      app writes via manager.set(...)  -> returns False -> probe FAILS the setup
```

### 2.1 Which object is loop-bound, and the decisive question answered

**Loop-bound:** the async redis client and its `ConnectionPool` held by the
`SharedRedisManager` singleton. The seams the conftest fixture restores are *references* to
module attributes — restoring them does not rebuild the pool, which is why the existing
guard does not help. The file itself records this lesson at line 288 about the rate limiter:
*"The problem is WHICH OBJECT it reset."* The same shape, one layer deeper.

**Can teardown safely close the client on its creating loop?** The brief asked this
explicitly. **No.**

| Obstacle | Evidence |
|---|---|
| The fixture is synchronous | `def _restore_shared_redis_singleton():` — `conftest.py:90-91` |
| Every cleanup entry point is a coroutine | `async def close()` ×2 (`:299`, `:441`), `async def disconnect()` (`:616`) |
| Making it `async` is not available | it is `autouse=True` for the **whole suite**, including thousands of synchronous tests |
| A cross-loop await is unsafe | the brief forbids it, and by the time the fault is observable the creating loop is already closed |

So the only cleanup expressible in a synchronous fixture is to **drop the cached
client/singleton** and let the next test rebuild. Which raises the second question:

**Does a supported synchronous reset exist?** **No.** Grepped `_instance = None`,
`SharedRedisManager._instance`, `reset_singleton`, `_initialized = False` across
`tests/**` and `backend_app/**`:

* `SharedRedisManager._instance` is referenced **only** by its own `__new__`. Nothing,
  anywhere, resets it.
* A precedent exists for the **sibling** manager — `tests/test_rate_limiter.py:25-26` does
  `b_rm._redis_manager = None; b_rm.RedisManager._instance = None` on
  `backend_app/backend/redis_manager.py`. Different class, different module.
* Comparable singletons elsewhere (`DatabasePool:293`) clear `_initialized` **inside their
  own `dispose()`** — i.e. the contract lives in the owning class, not in a test fixture.

**Conclusion.** Implementing B-1 means either reaching into another workstream's private
singleton state from a shared conftest, or adding a reset contract to
`core/cache/redis_manager.py` — that workstream's file. §3 of the brief says to stop and
report rather than invent a manager contract. **That stop condition is met.**

---

## 3. Why the ranking has changed

A22 reached a compatible conclusion from the other side and found something I had not:
**`backend_app/backend/redis_manager.py` is clean (593 lines) and can host loop-identity
rebuild without touching any concurrent file** — whereas invalidation-at-the-catch-site
cannot, because the exception is caught in `core/cache/redis_manager.py`.

So the option ranking from A20Q should be revised:

| Option | Location | Verdict now |
|---|---|---|
| ~~B-1~~ | `tests/conftest.py` | **withdrawn as first choice.** Not implementable on existing contracts (§2.1); unapproved; explicitly out of scope per A22; and unverifiable locally (§5) |
| **B-2′** | `backend_app/backend/redis_manager.py` — detect a client whose loop is closed/foreign and rebuild | **now preferred.** File is clean, no concurrent conflict. Needs that owner's approval; A22 has already requested it and has a `strict` xfail contract test waiting |
| B-2 | `core/cache/redis_manager.py` — invalidate where the exception is caught | needs F5 coordination |
| B-3 | `pytest.ini` session loop scope | still not recommended unverified |

**A22's xfail test is the natural gate for B-2′:** it fails today at the intended line with
`RuntimeError: Event loop is closed`, so whoever implements the rebuild has a pass/fail
signal that does not depend on a real Redis.

---

## 4. The one change made — my own file, documentation only

A22 §7 asked me to *"reconcile the 35 vs 36 citation"* in the A20 quota fix. Both figures
are real and come from **the same CI run `38061973739`**: **35** in the blocking
`Unit & Integration Tests` job, **36** in the non-blocking diagnostics job. The citation was
accurate but looked inconsistent because it named only one.

Reconciled in `backend_app/core/subscription_engine.py` — docstring and the inline comment
on the clamp — to cite both and state why they differ:

> CI run 38061973739 observed it twice at different magnitudes — 35 in the blocking
> `Unit & Integration Tests` job and 36 in the non-blocking diagnostics job of the SAME run
> — while the meter settled at exactly 10 in both. That the figure is not reproducible is
> itself the evidence: it is a race snapshot, not a reading.

**Comments and docstring only. No behavioural change.** `+57 / −4` → `+61 / −4`, and the
16 quota tests still pass (§5, run 4).

---

## 5. Verification — exact commands and counts

**No real Redis was available. `AERORA_TEST_REDIS_URL` is unset and no local server
exists.** Every real-Redis test below SKIPPED. **A skip is not a pass.**

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `python -m py_compile backend_app/core/subscription_engine.py` | **0** | clean |
| 2 | `pytest tests/test_webhook_duplicate_delivery_real_redis.py -p no:randomly -q -rsE` | **0** | **6 skipped.** 0 executed, 0 setup errors |
| 3 | `pytest` over 14 files — all `*redis*` + all `*rate_lim*` — `-rfE --timeout=900` | **0** | **216 passed, 6 skipped, 1 xfailed** in 113.12 s |
| 4 | `pytest tests/test_a20_quota_refusal_semantics.py tests/test_quota01_metered_reservation_is_hard.py -p no:randomly -q` | **0** | **16 passed, 3 skipped** in 15.76 s |

Step 1 of the prescribed sequence — focused lifecycle/fixture tests — **was not run,
because no B-1 change exists to test.** No new test was added: §4 of the brief asks for
regression coverage *of the B-1 change*, and writing tests for an unimplemented,
unapproved, locally-unverifiable change would be speculative.

The 1 xfailed in run 3 is A22's new `strict` contract test in `tests/test_redis_debounce.py`
— failing today by design.

### 5.1 The local-validation limitation, stated plainly

Run 2 is the important result. **All six webhook tests skip, so the defect is not reachable
locally at all.** The gate (`real_redis_required`) declines before `redis_client` builds a
client, so the loop-bound client is never created and `_prove_one_keyspace` never runs.

| Case | Locally observable? |
|---|---|
| Redis unavailable | yes — this is what we see (6 skips) |
| Manager refused a write | **no** — needs the gate to pass |
| **Event-loop lifecycle failure** | **no** — needs the gate to pass |
| Actual keyspace mismatch | **no** — needs the gate to pass |

So any B-1 (or B-2′) fix is verifiable only in a lane with real Redis: either a local
server (W1A approval **A-3**, not held) or the CI real-Redis lane (release-owner
authorization, **not held — no CI was triggered**). This is the strongest argument for
preferring B-2′, whose contract is pinned by A22's server-free xfail test.

---

## 6. Coordination request

| # | Request | Owner | Detail |
|---|---|---|---|
| **R1** | **Approve B-2′ instead of B-1** — loop-identity detection and rebuild in `backend_app/backend/redis_manager.py` | that file's owner (clean, unowned) | Gated by A22's existing `strict` xfail, so it is verifiable without Redis. Preferred |
| **R2** | *If B-1 is still wanted:* approve **both** (a) editing `tests/conftest.py`, and (b) adding a supported synchronous reset to `core/cache/redis_manager.py` | conftest owner **and** F5 owner | (b) is unavoidable — see §2.1. Without it the fixture must poke private singleton state |
| **R3** | A real-Redis lane for validation — local server (W1A A-3) **or** CI authorization | coordinator / release owner | Without one, neither fix can be shown to work |
| **R4** | Decide whether the A20 quota fix lands on PR #6 or separately | coordinator | Carried over; still open |

### 6.1 Proposed teardown behaviour, if R2 is granted

Stated precisely so it can be reviewed rather than guessed at:

* **In `core/cache/redis_manager.py` (F5 owner):** add a classmethod that synchronously
  drops the cached singleton and its client reference — clearing `_instance` and
  `_initialized` — and does **not** await anything. Name and placement are that owner's
  call; the contract needed is "safe to call from a synchronous test teardown".
* **In `tests/conftest.py` (conftest owner):** call it in the existing
  `_restore_shared_redis_singleton` `finally` block, after the seam restoration, wrapped so
  a teardown exception cannot mask the original test failure. Idempotent by construction
  (clearing an already-clear singleton is a no-op).
* **Explicitly not proposed:** any change to loop scope, any new session-scoped fixture, any
  change to the fail-soft proxies, any change to the keyspace-probe assertions, and any
  cross-loop await.

**Risks to weigh.** The dead pool's sockets are abandoned rather than closed — unavoidable,
since the loop that owned them is gone; it may surface `ResourceWarning`. Dropping the
singleton per test is a behaviour change for any test that *relies* on manager state
persisting across tests; **10 files reference the seam fixture or its constant** and would
form the regression surface. That surface is why this needs the owner, not a drive-by edit.

---

## 7. Preservation measurements

| Check | Entry | Exit |
|---|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | **identical** |
| `origin/main` divergence | `0 / 0` | `0 / 0` |
| **Canonical staged digest** | `2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` | **identical** |
| Staged paths | 22 | **22** |
| `git write-tree` (isolated) | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | **identical**, **0 objects** written to the temp store |
| Unmerged entries | 0 | 0 |
| Unstaged paths | 102 | **102** |
| Untracked | 3,505 | 3,509 (+4; **1 mine** — this report. The other 3 are concurrent sessions) |
| Worktrees | 1 | **1** |

`git write-tree` was captured with `GIT_OBJECT_DIRECTORY` redirected to a temp directory and
`GIT_ALTERNATE_OBJECT_DIRECTORIES` pointing at the real store; **0 objects** were created,
so nothing entered `.git/objects`.

**Drift attribution.** Unstaged moved 101 → 102 between A20 and A21 entry, and untracked
grew by ~30 — all from concurrent sessions, none from me. My only tracked modification is
`subscription_engine.py`, and only its comments.

| Confirmation | |
|---|---|
| `tests/conftest.py` modified | **NO** — still `+172 / −0` |
| `core/cache/redis_manager.py` modified | **NO** — still `+185 / −5` |
| `pytest.ini` modified | **NO** — still `+13 / −0` |
| `backend/redis_manager.py` modified | **NO** — still clean |
| Webhook test / keyspace-probe assertions altered | **NO** |
| Loop scope changed / session fixtures added / tests disabled | **NO** |
| Shared index mutated · staged · committed · pushed · reset · stashed · branch switched | **NO** |
| PR modified · CI triggered | **NO** |
| Database access · Redis call · venue call | **NO** |
| Another session's report overwritten | **NO** |

---

## 8. Status discipline

| Distinction | Status |
|---|---|
| **B-1 implemented** | **NO** — unapproved, not implementable on existing contracts, contested by A22 |
| **B-1 locally tested** | **N/A** — nothing to test; and §5.1 shows it could not be validated locally anyway |
| **Real-Redis webhook assertions executed** | **0 of 6. All 6 SKIPPED.** Not passes |
| **D5′ acceptance** | **UNVERIFIED and NOT CLAIMED.** The webhook half remains unevidenced in either direction until a real-Redis lane runs |
| **QUOTA-01** | **OPEN.** A20 fixed the refusal contract and a fail-open hole in `reserve_quota`; that is strictly narrower than closure — no real-Redis re-verification, exchange-slot safety unproven, and order-rate / daily-trade / active-order ceilings unimplemented |
| **Exchange-slot concurrency** | **UNPROVEN** |
| **TENANT-01** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

**Next permitted action: R1** — put B-2′ to the `backend_app/backend/redis_manager.py`
owner. It is the only route that needs one approval instead of two, touches no concurrent
file, and already has a server-free pass/fail test waiting for it.

---

QUOTA-01 **OPEN** · TENANT-01 **OPEN** · exchange-slot concurrency **UNPROVEN** · D5′
acceptance **UNVERIFIED** · real Redis **NOT USED** · no CI triggered · no commit, push, or
PR change · `tests/conftest.py` **unedited** · live trading **OFF / NOT CERTIFIED**
