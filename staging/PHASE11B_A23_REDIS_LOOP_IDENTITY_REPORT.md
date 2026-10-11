# Phase 11B-A23 — Redis Loop Identity (B-2′): Approval Gate — INVESTIGATION ONLY

**STATUS: STOPPED AT THE PHASE 1 APPROVAL GATE. NO IMPLEMENTATION WAS PERFORMED.**

No production code, no test, no config and no existing report was modified. No Redis server was
started, no database, exchange or network call was made, no CI was triggered. Nothing staged,
committed or pushed.

**Entry 2026-10-10 · destination verified ABSENT immediately before writing.**

**The headline is not the missing approval — it is that the approval being sought is for a change
that cannot satisfy its own stated verification criterion.** §4.1. Two prior reports disagree about
this and one of them is wrong.

---

## 1. Phase 1 verdict — approval ABSENT, from three independent records

The brief forbids treating the file being clean, a prior recommendation, or an existing xfail as
authorization. All three of those exist here, and nothing else does.

| Record | What it says about B-2′ in `backend_app/backend/redis_manager.py` |
|---|---|
| `PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md` §1 — titled **"Authorization and ownership gate — FAILED"** | "**No approval record exists from any owner.**" Grepped `B-1`, `conftest.*approv`, `APPROVED`, `GRANTED`; every hit was its own recommendation or A22's out-of-scope note. Target row: "clean, unowned, but A22 notes *'clean is not authorization'*" → **not edited** |
| `PHASE11B_A21...` §6 **R1** | "**Approve B-2′ instead of B-1** — loop-identity detection and rebuild in `backend_app/backend/redis_manager.py` · that file's owner". A **request**. Its closing line: "**Next permitted action: R1** — put B-2′ to the … owner." The request was never answered |
| `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` §Decisions, row 2 | "Approve **option B** in the same file · same owner · **NOT GRANTED**". A22V's option B **is** loop-identity rebuild (§"Option B", and §321-322 "loop-identity rebuild on mismatch; no rebuild on match (control)"). So B-2′ ≡ option B = **NOT GRANTED** |
| `PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` (**concurrent session, 22:02 today**) §8 row 1 | "Implement Step 0 changes 1–4 in `backend_app/backend/redis_manager.py` · owner of that file · **NOT GRANTED — the gate**" |

Three sessions have now requested permission to edit this one file. All three record it refused or
unanswered. There is no approval for loop-identity detection, none for the required test change, and
no scope statement from the file's owner.

### 1.1 A concurrent session is at the same gate on the same file

`staging/PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` was written at **22:02 today**, minutes
before this phase began. It shares my phase label (A23) and my target file, and it stopped at the
same gate.

**We are adjacent, not overlapping.** That report's §5 states its scope explicitly excludes mine:
"Explicitly **out of scope**, per the brief: **no event-loop identity rebuilding**, no runtime
command-failure invalidation…". It addresses pool lifecycle (G1/G2/G3) in `initialize()` and
`close()`; B-2′ addresses loop identity at the reuse point. Different hunks, same file.

If both are ever approved, **order matters**, and that is new coordination information: its
change 3 makes `close()` unbind `_cache/_queue/_events` and reset `_connected`. B-2′'s rebuild path
must then tolerate `None` clients rather than assume a bound-but-stale set. Approving B-2′ first and
pool lifecycle second would require revisiting B-2′. **Pool lifecycle should land first.** Neither
brief says so, because neither session could see both scopes.

That session's §3.3 finding **A23-01** is also directly load-bearing for B-2′ and I confirmed its
mechanism against source: `close()` (`:215-224`) closes all three clients but resets neither
`_connected` nor the bindings, so `get_redis_manager()`'s fast path (`:548-550`) hands back a
manager on closed clients. A loop-identity check placed in that fast path would *also* catch this
case — the clients' loop is gone — which means B-2′ and pool lifecycle partially overlap in effect
even though they do not overlap in code.

### 1.2 Test ownership is not unclear — it is held by another session, in flight

| File | Status | Owner evidence |
|---|---|---|
| `tests/test_redis_debounce.py` | **` M` dirty, 329 lines** | A22V §Decisions row 5: "Who owns `tests/test_redis_debounce.py` going forward · **the A22 session** · open — I did not edit it". Concurrent A23: "remains the A22 session's in-flight work and was not touched" |
| `backend_app/core/cache/redis_manager.py` | ` M` dirty, 1338 lines | A22V row 3: owner's file, in flight. Read-only per brief |
| `tests/conftest.py` | ` M` dirty, 382 lines | A21: "+172 / −0, another workstream". Read-only per brief |
| `pytest.ini` | ` M` dirty, 62 lines | A21: "+13 / −0, concurrent". Read-only per brief |
| `backend_app/backend/redis_manager.py` | **clean**, 533 lines | unowned — and per A22, "clean is not authorization" |

Phase 2 item 7 says to stop before changing the test if ownership is unclear. It is not unclear; it
is *taken*. Phase 2 item 7 also says to reuse the existing strict xfail "if it is still valid and
owned by this workstream." It is owned by A22, and §4.1 shows it is **not valid for B-2′** anyway.

---

## 2. Preflight and exit preservation

| Invariant | Entry | Exit |
|---|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | same |
| upstream divergence | 0 ahead / 0 behind | same |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | same |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | same |
| staged paths / unmerged | 22 / 0 | 22 / 0 |
| unstaged tracked | 102 | 102 |
| untracked | 3522 | 3523 (this report) |

`backend_app/backend/redis_manager.py` byte-unchanged and still clean. The four dirty files were
opened **read-only** and remain byte-identical. No existing report was overwritten; the concurrent
`PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` was read, not modified. No `git add`, `reset`,
`checkout`, `clean`, `stash`, `commit`, `push`, branch switch or index mutation.

**Digest-method note.** The staged digest above is
`sha256(git diff --cached, CRLF→LF)`. A21 and the concurrent A23 report a different canonical digest
(`2e994018…7152`) under their own method (sha256 over sorted `git ls-files --stage`). **Both are
stable and both agree the staged content has not moved**; they are different functions of it, not a
disagreement. The concurrent session additionally declines to run `git write-tree` because it writes
unreferenced objects to the shared object database, and uses a copied index in an isolated object
dir instead — a stricter reading than mine. I used `write-tree` directly; the value matches theirs,
so the two methods corroborate.

---

## 3. Source findings

### 3.1 The target file today

533 lines. `RedisManager` is a `__new__`-based singleton (`:104-109`) with a module-global
`_redis_manager` (`:536`) and an async accessor `get_redis_manager()` (`:548-578`).

`initialize()` (`:131-184`) is **not naive**: it builds all three clients into locals, pings all
three, and only then commits to `self.*` (`:167-170`), with the comment at `:163` "Verify
connectivity before committing to self.*". `try_reconnect()` (`:186-213`) debounces on
`_last_failed_at` with a 3.0 s cooldown and a per-instance lock.
`close()` (`:215-224`) closes the three clients and resets nothing.

**There is no event-loop tracking of any kind.** A search of the file for
`get_running_loop|get_event_loop|_loop|is_closed|set_event_loop` returns **zero matches**. So B-2′
is not a repair of a broken check — it is a new mechanism, and the manager currently has no way to
know which loop its pools belong to.

### 3.2 Phase 0 item 5 — is loop identity available at the reuse point? **Partly, and that is the finding.**

There are two reuse points, and they differ:

| Reuse point | Async? | `asyncio.get_running_loop()` available? | Can rebuild? |
|---|---|---|---|
| `get_redis_manager()` fast path, `:548-550` | **yes** (`async def`) | **yes** | **yes** — can await `initialize()` |
| `RedisManagerCompatProxy.__getattr__`, `:581-591` | **no** — plain `def` | **not reliably**; raises `RuntimeError` outside a running loop | **no** — cannot await anything |

The proxy reads the module global and returns `getattr(_redis_manager._cache, name)` **directly**.
It never calls `get_redis_manager()`, so a loop check placed there is bypassed entirely.

**And the proxy is the majority consumer path, including the kill switch.** From an import sweep of
`backend_app/`:

*Through the synchronous proxy (`import redis_manager`) — no loop check possible:*
* `backend_app/core/global_safety.py` — **11 lazy import sites** (`:243, 253, 265, 274, 283, 314,
  333, 350, 369, 383`): `GlobalKillSwitch` get/set/lpush/lrange/expire/smembers/sadd/srem and the
  transition lock
* `backend_app/core/audit_trail.py:31`
* `backend_app/core/alerting_system.py:35`
* `backend_app/core/distributed_rate_limiter.py:25`
* `backend_app/core/load_protection.py:24`
* `backend_app/routers/dag_tasks.py:18`
* `backend_app/core/rate_limiter.py:176-180` — reads `redis_manager._connected` and
  `redis_manager.cache` through the proxy, bypassing both the accessor and any health logic

*Through the async accessor (`import get_redis_manager`) — loop check possible:*
* `backend_app/routers/exchange.py:27`
* `backend_app/core/cache/redis_manager.py:494`
* `backend_app/backend/training_worker.py:3224`
* `backend_app/backend/strategy_service.py:6168`

**Consequence: B-2′ implemented in `get_redis_manager()` would leave the kill switch, audit trail,
alerting, distributed rate limiter, load protection and the DAG task router unprotected.** Neither
A21 nor A22V states this. A21 §4 says the file "can host loop-identity rebuild without touching any
concurrent file," which is true of the *mechanism* but overstates the *coverage*. Extending the
protection to the proxy would mean changing a synchronous accessor into something that can rebuild —
which is a materially larger design than "detect and rebuild in the accessor," and is the part that
most needs the owner's judgement.

---

## 4. The finding that changes the plan

### 4.1 `test_r6_d` is not B-2′'s gate, and B-2′ would not convert it to a pass

A21 §5 and §6 assert that B-2′ is "**Gated by A22's existing `strict` xfail, so it is verifiable
without Redis**" and "already has a server-free pass/fail test waiting for it." My brief inherits
this: Phase 3 requires establishing that "the contract passes after the fix and the strict xfail is
correctly converted to a passing test."

**Read against the test body, that is not achievable.**
`test_r6_d_desired_a_runtime_failure_should_be_recoverable`
(`tests/test_redis_debounce.py:379-414`):

```python
cache = _failing_cache()            # :261-267 — an AsyncMock whose set/get/ping
                                    #            raise RuntimeError("Event loop is closed")
mgr = _connected_manager_with(cache)  # :270-278 — binds the mock to all three slots,
                                      #            sets _connected = True
with _RecordingManagerGlobals() as rm:
    rm._redis_manager = mgr
    ...
    returned = await rm.get_redis_manager()
    await returned.cache.set("r6:probe", "2")   # fails today
```

The whole test runs inside **one live `anyio` event loop**. The failure is an **injected mock
exception whose message happens to read "Event loop is closed"** — `_OBSERVED_RUNTIME_ERROR` at
`:237`, copied verbatim from CI run 38061973739 so that "the fault injected here is the fault that
actually occurs rather than a convenient stand-in." The *string* is faithful. The *loop state* is
not: no loop is ever closed, and no second loop is ever created.

So at the moment `get_redis_manager()` runs, a loop-identity check sees a **live, matching,
unclosed** loop. It would correctly decline to rebuild. `test_r6_d` would still fail, remain
`xfail`, and never XPASS.

**A22V already said this, in its own Option B assessment:**

> Recovery from real connection loss | **No.** A connection lost inside the *same* loop presents an
> identical loop identity. **Does not fix R6.**

And `test_r6_d` is R6's contract — A22V: "R6 … Contract pinned by `test_r6_d` (`xfail`,
`strict=True`)." The test's own docstring offers two routes, "**option A**" (invalidate health) and
"**option B**" (notice the pool belongs to a dead loop); only option A addresses the arrangement the
test actually builds.

**A21 conflated the two.** Its §4 table correctly distinguishes B-2 from B-2′, but its §5 and §6
then borrow R6's test as B-2′'s gate. On A22V's own analysis — which A21 cites approvingly — that
test is gated on health invalidation, which lives in the ~25 handlers at
`core/cache/redis_manager.py:753-1016` (A22V decision 3, **NOT GRANTED**, and that file is dirty).

### 4.2 Therefore B-2′ has no existing verification, and needs a new test file

No test in the repository creates a second event loop, or closes one, and then reuses the cached
manager. `test_r6_a/b/c` and `test_r6_d` all inject a failing mock on a live loop; the six
`test_redis_debounce.py` debounce tests use `_make_fresh_manager()` (`object.__new__`, bypassing the
singleton) within one loop.

So B-2′ requires a genuinely new contract test that constructs a real loop, binds clients on it,
closes it, and then reuses the singleton from a second loop. Its natural home —
`tests/test_redis_debounce.py` — is **owned by A22 and dirty**, so B-2′ needs a new file, exactly as
the concurrent A23 session concluded for its own scope. That is a **second** approval, not covered
by R1, and it is unrequested anywhere.

### 4.3 Which means the brief's Phase 3 criteria split in two

| Phase 3 criterion | Achievable by B-2′? |
|---|---|
| "the original closed-loop failure is reproduced by the contract before the fix" | **Not by `test_r6_d`** — it reproduces a message, not a loop state. Achievable only by a new test (§4.2) |
| "the contract passes after the fix and the strict xfail is correctly converted to a passing test" | **No.** `test_r6_d` is gated on health invalidation (option A), not loop identity. Converting it is A22V decision 3's business, in another owner's dirty file |
| "repeated access on the same live loop does not rebuild unnecessarily" | yes — the negative control A22V §321-322 already specifies |
| "a different or closed loop cannot reuse an invalid client/pool" | yes, **at `get_redis_manager()` only** — not through the proxy (§3.2) |
| "rebuild is deterministic and does not leak unhandled task exceptions" | yes |
| "relevant adjacent manager and rate-limiter tests remain green" | yes |

**The honest position: B-2′ is implementable and would close the cross-loop class, but it does not
close R6 and it cannot be validated by the xfail the brief points at.** Saying otherwise would be
claiming verification I cannot perform.

---

## 5. Tests run

**None.** Phase 3 was not entered, because Phase 1 failed. Running the existing suites would have
measured only unmodified behaviour already recorded by A21 (3 runs) and A22V.

No Redis server was started; no real-Redis test was run. **The real-Redis lane remains UNEXECUTED**,
and per A21 §5 the webhook assertions there are 0 of 6 executed, 6 skipped. Skips are not passes.
**No D5′ acceptance is claimed**, and none could be from a server-free lifecycle test.

---

## 6. Remaining risks and limitations

1. **The cross-loop defect is not reproduced anywhere in this repository.** A19's CI setup errors
   are the field evidence; no local test exercises actual loop closure (§4.2). Until one exists,
   B-2′'s premise rests on CI observation plus source reasoning, not on a local reproduction.
2. **Proxy coverage gap (§3.2)** — the single most important design input, and unstated in A21/A22V.
3. **Ordering dependency on the concurrent A23 scope (§1.1)** — pool lifecycle should land first.
4. **The third, unlocked `initialize()` caller** at `core/cache/redis_manager.py:625` (concurrent
   A23 §3.1) bypasses both cooldowns and both locks, and lives in a dirty file. Any concurrency
   claim for B-2′ is bounded by it.
5. **`RedisManager._instance` vs `_redis_manager`** are two separate caches of the same object; a
   rebuild must consider both or a stale instance can be re-adopted. `tests/test_rate_limiter.py:24-27`
   resets both, which is evidence the duplication already bites in tests.
6. **`_initialized` re-entry** on the singleton (`:107`, `:112-113`) was not traced; a rebuild path
   that re-runs `__init__` would silently no-op.
7. **A22 F5** (two independent cooldowns) remains unreproduced and is untouched here.
8. G1/G2/G3 evidence is A22V's, re-read, not re-executed in this phase.

---

## 7. What is needed to proceed

| # | Request | Owner | Status |
|---|---|---|---|
| **A23L-1** | Approve loop-identity detection and rebuild in `backend_app/backend/redis_manager.py` (= A21 R1 = A22V option B), **with a decision on §3.2**: accessor-only, or extend to the synchronous proxy | owner of that file | **NOT GRANTED** |
| **A23L-2** | Approve a **new** test file (e.g. `tests/test_a23_redis_loop_identity.py`) — `tests/test_redis_debounce.py` is A22's, dirty, and §4.1 shows its xfail is not B-2′'s gate | test owner + A22 session | **NOT GRANTED / unrequested** |
| **A23L-3** | Confirm sequencing: concurrent A23 pool lifecycle lands **before** B-2′ (§1.1) | coordinator | **open** |
| **A23L-4** | Correct the A21 §5/§6 claim that `test_r6_d` gates B-2′ (§4.1). A21 is another session's report and was not edited | A21 session | **open** |

A23L-1 and A23L-2 are the minimum to enter Phase 2. Without A23L-4 the next session will inherit the
same incorrect premise.

---

## 8. Status

| | |
|---|---|
| **B-2′ (loop identity)** | **NOT IMPLEMENTED — blocked at the approval gate** |
| **New this phase** | B-2′ cannot convert `test_r6_d` (§4.1); the synchronous proxy is an uncoverable reuse path including the kill switch (§3.2); ordering dependency on the concurrent A23 scope (§1.1) |
| R6 | **OPEN.** Gated on health invalidation in another owner's dirty file (A22V decision 3), not on B-2′ |
| G1 / G2 / G3 / A23-01 | identified by A22V and the concurrent A23; **not fixed**; not in this scope |
| `test_r6_d` | still `xfail(strict=True)`, unmodified, in another session's file |
| Real-Redis lane | **UNEXECUTED** — 0 of 6 webhook assertions executed |
| D5′ | **no acceptance claimed** |
| QUOTA-01 / TENANT-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from the file being clean, from A21's or A22V's
recommendations, from the existence of the xfail contract, or from silence. Skipped tests are never
counted as passes.
