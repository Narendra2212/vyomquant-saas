# Phase 11B-A23C — Redis Recovery Consolidation and Approval Readiness

**INVESTIGATION ONLY. No production or test file was changed. No report owned by another session
was edited.** No Redis started, no database, exchange, network or CI contact. Nothing staged,
committed or pushed.

**Destination verified ABSENT immediately before writing.**

---

## 0. Headline

**Six independent sessions have now reached the same unmade decision on one clean file.** The
analysis is not short of evidence; it is short of an answer.

| # | Report | Written | Verdict |
|---|---|---|---|
| 1 | `PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` | 21:39 | test-only; "approval … is required" |
| 2 | `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` | 21:49 | option B **NOT GRANTED** |
| 3 | `PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md` | 21:54 | gate **FAILED**; "no approval record exists from any owner" |
| 4 | `PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` | 22:02 | **STOPPED** at gate |
| 5 | `PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` | 22:08 | **STOPPED** at gate |
| 6 | `PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` | 22:12 | **STOPPED** at gate |

Report 6 measured the aggregate at "~83 KB of investigation behind one unmade decision." This
document adds ~1 more and then stops asking for study. §7 is a decision sheet.

`backend_app/backend/redis_manager.py` is byte-identical to HEAD (blob `d7b0e811b615`), and a
search of it for `_loop|get_running_loop|get_event_loop|is_closed|set_event_loop|aclose` returns
**zero matches**. Nothing has been implemented by anyone.

**Two documented claims are wrong and are corrected here (§3).** One of them — that the existing
strict xfail validates loop-identity recovery — would have caused a future session to report a
false pass.

---

## 1. Rebaseline and ownership

| Invariant | Value |
|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` |
| upstream | 0 ahead / 0 behind |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` |
| staged / unmerged | 22 / 0 |
| unstaged tracked | 102 |
| untracked | 3537 at entry |

**Digest-method reconciliation.** Three methods are in circulation and they do **not** conflict —
they are different functions of identical content. A22/A21/A23×3 report
`7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` (sha256 over sorted
`git ls-files --stage`); I report `65dfc453…` (sha256 over `git diff --cached`, CRLF→LF); the tree
is `6f40856c…` from `git write-tree`. All three have been stable across every session today. Report
4 declines `write-tree` as a shared-object-database write and uses a copied index in an isolated
object dir, reporting the same tree hash — so the strict and direct methods corroborate.

### Ownership, measured this phase

| File | Status | Delta | Owner / disposition |
|---|---|---|---|
| `backend_app/backend/redis_manager.py` | **clean** | — | **the target.** Unowned. Per A22, "clean is not authorization" |
| `backend_app/core/rate_limiter.py` | **clean** | — | unowned; reads the proxy (§2 item 7) |
| `backend_app/core/cache/redis_manager.py` | ` M` | +185 / −5 | other workstream, in flight — read-only |
| `backend_app/core/global_safety.py` | ` M` | **+653 / −70** | **newly dirty this phase** — see §5.3 |
| `tests/test_redis_debounce.py` | ` M` | +225 / −0 | **the A22 session's** R6 tests, intact |
| `tests/conftest.py` | ` M` | +172 / −0 | other workstream — read-only |
| `pytest.ini` | ` M` | +13 / −0 | concurrent (`realaudit` marker docs); `xfail_strict` still absent |

---

## 2. Reconciled evidence matrix

Legend: **EXEC** confirmed by execution · **SRC** confirmed by source inspection · **HYP**
hypothesis, untested · **BLOCKED** by authorization or ownership.

| # | Item | Status | Evidence | Fix location | Blocked by |
|---|---|---|---|---|---|
| **1** | **G1 / F4** — failed `initialize()` leaks every client it built; the `except` at `:178-183` drops three locals unclosed, and `self._*` keep the old objects while `_connected` goes `False` | **EXEC** (A22V probe: 3 created, **0** closed) + **SRC** (`:138-184`) | `staging/probe_p11b_a22v_lifecycle_gaps.py`; A22 F4 | `initialize()` except block | **decision 1** |
| **2** | **G2 / F3** — successful rebuild overwrites `self._cache/_queue/_events` at `:167-170` without closing the outgoing three; `close()` is never called by `initialize()` | **EXEC** (A22V) + **SRC** | A22 F3 (`:168-170` vs `:215`) | `initialize()` commit point | **decision 1** |
| **3** | **G3 / A23-01** — `close()` (`:215-224`) closes the clients but resets neither `_connected` nor the bindings. Composite: `disconnect()` → `close()` → then `get_redis_manager()`'s fast path (`:548-549`) sees `_connected` still `True` and returns the manager on closed clients; **and** `core/cache.connect()`'s guard `not self._redis_manager._cache` is `False`, so `initialize()` is **skipped** | **EXEC** (A22V G3) + **SRC** (composite traced in report 4 §3.3) | report 4 §3.3 | `close()` must **both** reset health **and** unbind | **decision 1** |
| **4** | **B-2′** — cross-event-loop reuse. No loop is recorded anywhere; a manager cached on loop A is handed to loop B with pools bound to A | **SRC only** (zero matches for `_loop`/`get_running_loop` in the target) + **field evidence** (A19 CI setup errors, run 38061973739) | §0; A22V option B | `__init__` + `get_redis_manager()` pre-fast-path | **decision 2** |
| **5** | **R6 / option A** — runtime connection failure inside the *same* loop. `_connected` has exactly one writer, `initialize()` (`:122/171/180`); the handlers that observe failures never touch it, so both recovery entry points short-circuit (`:548`, `:192`) | **EXEC** (`r6_a`/`r6_b` PASS, `r6_c` control PASS, `r6_d` XFAIL — 9 passed, 1 xfailed, 6.76 s) | A22 F1/F2 §4 | the ~25 handlers at `core/cache/redis_manager.py:753-1016` | **decision 4 — another owner's dirty file** |
| **6** | **Third, unlocked `initialize()` caller** — `core/cache/redis_manager.py:625` inside `connect()` calls `initialize()` with **no cooldown and no lock**, reached from `main.py:370`. The two internal callers (`:213`, `:571`) are both debounced and locked | **SRC** | report 4 §3.1 | not fixable inside the target | **decision 5** |
| **7** | **Synchronous compat proxy** — `RedisManagerCompatProxy.__getattr__` (`:581-591`) is a plain `def` that reads the module global and returns `getattr(_redis_manager._cache, name)`. It **never calls `get_redis_manager()`** and cannot await, so it can neither detect a loop mismatch nor trigger a rebuild | **SRC** | §3.2 below; my report 5 §3.2 | requires an API decision, not a patch | **decision 3** |
| **8** | **Duplicate singleton + `_initialized` re-entry** — two caches of one object: `RedisManager._instance` (`:105`) and module global `_redis_manager` (`:536`). `__init__` returns early when `_initialized` is `True` (`:112-113`), so re-constructing after clearing only `_redis_manager` yields the **same instance with its old `_cache` and `_connected`** | **SRC**, corroborated by test practice: `tests/test_rate_limiter.py:24-27` clears **both** (`_redis_manager = None`; `RedisManager._instance = None`) | any rebuild path | **decision 2** (design input) |
| **9** | **F5** — two independent cooldowns: `try_reconnect` debounces on `self._last_failed_at` (`:196`), `get_redis_manager` on module-global `_get_manager_last_failed` (`:552`), and `get_redis_manager` calls `initialize()` **directly** (`:571`), bypassing `try_reconnect`'s gate entirely | **SRC only — UNREPRODUCED by any test in 6 reports** | A22 F5 | both files | **decision 5** |

### 2.1 What each item costs if left open

| Item | Consequence if unfixed |
|---|---|
| 1, 2 | connection-pool leak: 3 pools per failed attempt (once per 3.0 s cooldown) and 3 per successful rebuild. Resource exhaustion under flapping |
| 3 | after shutdown, the app proceeds on closed clients and **cannot reconnect** — the dead path |
| 4 | the A19 CI class: cached manager unusable in a new loop |
| 5 | one transient command failure makes the manager permanently stale **for the life of the process** |
| 6, 9 | concurrency and cooldown guarantees cannot be claimed |
| 7 | whatever recovery is built does not reach the proxy's consumers |
| 8 | a "fresh" manager silently is not fresh |

---

## 3. Corrections to documented claims

Recorded here, per the brief. **No other session's report was edited.**

### 3.1 CORRECTION 1 — the strict xfail does NOT validate loop-identity recovery

**Claim, in `PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md`:**
> §4: B-2′ … "Needs that owner's approval; A22 has already requested it and **has a `strict` xfail
> contract test waiting**"
> §5: "This is the strongest argument for preferring B-2′, **whose contract is pinned by A22's
> server-free xfail test**"
> §6 R1: "**Gated by A22's existing `strict` xfail, so it is verifiable without Redis.**"

**This is wrong.** `test_r6_d_desired_a_runtime_failure_should_be_recoverable`
(`tests/test_redis_debounce.py:379-414`) builds:

* `_failing_cache()` (`:261-267`) — an `AsyncMock` whose `set`/`get`/`ping` raise
  `RuntimeError("Event loop is closed")`;
* `_connected_manager_with(cache)` (`:270-278`) — binds that mock to all three slots and sets
  `_connected = True`;
* and runs entirely inside **one live `anyio` loop**.

The string `"Event loop is closed"` is `_OBSERVED_RUNTIME_ERROR` (`:237`), copied verbatim from CI
run 38061973739 so the injected fault matches the real one. The **message** is faithful; the
**loop state** is not. No loop is closed; no second loop exists. A22's own exclusion table says so:
"A closed event loop in the test | **No real loop-bound resource exists** — every client is an
`AsyncMock`. The failure is injected, not environmental."

So a loop-identity check would see a live, matching, unclosed loop and correctly decline to
rebuild. `r6_d` stays XFAIL.

**A22V already stated this**, in its own option B row: "Recovery from real connection loss |
**No.** A connection lost inside the *same* loop presents an identical loop identity. **Does not
fix R6.**" A21 cites A22V approvingly and then contradicts it.

**Report 6 independently reached the same correction and improved on it** (§5, coverage item 6):
> "**the proposed plan does not flip `r6_d`.** Only option A does. If `r6_d` turned XPASS after
> implementing B, that would signal accidental scope creep into option A — which makes the strict
> marker a useful guard during implementation, not just afterwards."

That reframing is better than mine and is adopted here: **`r6_d` is not B-2′'s gate — it is B-2′'s
blast-radius alarm.** It must remain XFAIL through items 1–4 and flip only when item 5 lands.

**Practical consequence:** two of the A23 brief's Phase 3 criteria ("the original closed-loop
failure is reproduced by the contract before the fix"; "the strict xfail is correctly converted to
a passing test") are **unachievable by B-2′** and must not be reported as met.

### 3.2 CORRECTION 2 — "consumers pick up a rebuilt manager automatically" is true of propagation, not of triggering

**Claim, A22 F6:** "**Consumers pick up a rebuilt manager automatically.** `RedisManagerCompatProxy.__getattr__`
re-imports `_redis_manager` on every access (L580-590) … so no consumer caches a stale handle."

**Half right, and the half that is wrong is the half that matters for recovery.** The two statements
must be separated:

* **Propagation — A22 F6 is correct.** The proxy re-imports the module global on every attribute
  access, so once *something else* replaces `_redis_manager`, the proxy sees the new object. No
  consumer holds a stale handle.
* **Triggering — not covered.** The proxy is a **synchronous** `__getattr__`. It never calls
  `get_redis_manager()`, so it cannot *cause* the rebuild it would benefit from, and
  `asyncio.get_running_loop()` is not reliably available there.

My report 5 §3.2 asserted the gap but conflated two differently-named objects. Correcting my own
claim:

| Import | Object | Async? | Calls `get_redis_manager()`? | Covered by accessor-only recovery? |
|---|---|---|---|---|
| `from backend_app.backend.redis_manager import redis_manager` | `RedisManagerCompatProxy` (`:581`) | **no** | **never** | **NO** |
| `backend_app.core.cache.redis_manager`'s `redis_manager` | `SharedRedisManager` | **yes** | **yes — every method awaits `_ensure_manager()` (`:487-498`), which awaits `get_redis_manager()` and re-assigns** | **YES** |

So the `core/cache` path **is** protected by accessor-only recovery, which my report 5 understated.
The uncovered set is specifically the `RedisManagerCompatProxy` consumers:

* `backend_app/core/global_safety.py` — 11 lazy import sites (`:243, 253, 265, 274, 283, 314, 333,
  350, 369, 383`): kill-switch get/set/lpush/lrange/expire/smembers/sadd/srem and the transition lock
* `backend_app/core/audit_trail.py:31`, `alerting_system.py:35`,
  `distributed_rate_limiter.py:25`, `load_protection.py:24`, `routers/dag_tasks.py:18`
* `backend_app/core/rate_limiter.py:168-181` — its `redis` property reads
  `getattr(redis_manager, "_connected", False)` and `getattr(redis_manager, "cache", None)`
  synchronously, then falls back to `self._fallback_redis`

### 3.3 CORRECTION 3 (to my own report 5) — the kill switch fails closed on read

My report 5 implied the kill switch would be silently unsafe on a stale client. **Source says
otherwise for the read path.** `global_safety.py:236-248`:

> "This used to swallow every exception and return `None`, which made the two outcomes
> indistinguishable and is the root of **KILL-01**. Every exception is translated rather than
> propagated as-is so that callers catch one documented type."

`_redis_get` raises `RedisUnavailable`. So a stale client makes the kill-switch read **fail
closed**, not silently permissive. The exposure is availability, not safety — materially less
severe than I implied.

The **write** helpers are different: `_redis_set` (`:252-260`), `_redis_lpush`, `_redis_expire`,
`_redis_sadd`, `_redis_srem` all `return False` on exception. So a stale client would silently fail
to *record* kill-switch state while correctly refusing to *read* it.

**Caveat:** `global_safety.py` became dirty **during this phase** (+653 / −70). A KILL-01 fix
appears to be in flight, so this analysis is of an in-flight file and must be re-verified by its
owner before being relied on.

---

## 4. Test and acceptance matrix

Per the brief's Phase 2. "Exists" means a test already asserts it — nothing is duplicated.

| # | Acceptance gate | Status today | Proposed file | Owner | Real Redis? | Prereq |
|---|---|---|---|---|---|---|
| T1 | Failure at the 1st / 2nd / 3rd client closes everything **that attempt** built, exactly once | **missing** (0 of 3 closed) | `tests/test_a23_redis_pool_lifecycle.py` (new) | target owner | **no** | decision 1 |
| T2 | A cleanup error does not mask the original init failure | **missing** (no cleanup exists) | same | target owner | no | decision 1 |
| T3 | Successful replacement closes all three **outgoing** clients exactly once | **missing** | same | target owner | no | decision 1 |
| T4 | Successful replacement leaves the three **new** clients open — negative control | **passes today** | same | target owner | no | decision 1 |
| T5 | `close()` → `_connected is False` **and** all three attrs `None` | **missing** | same | target owner | no | decision 1 |
| T6 | `close()` twice → no exception, no double-close beyond tolerance (idempotence) | **missing / UNVERIFIED** | same | target owner | no | decision 1 |
| T7 | **A23-01 composite:** `close()` then a `connect()`-shaped reinit actually rebuilds | **missing — the most valuable of the set** | same | target owner | no | decision 1 |
| T8 | Failed init cannot leave the manager marked connected — negative control | **passes today** (`:180`) | same | target owner | no | decision 1 |
| T9 | **Reuse from a genuinely different loop** rebuilds: two successive `asyncio.run` calls over one manager | **missing — nothing in the repo closes a loop** | `tests/test_a23_redis_loop_identity.py` (new) | target owner | **no** | decision 2 |
| T10 | Healthy **same-loop** reuse does **not** rebuild — negative control | **exists** — `test_healthy_fast_path_skips_lock` (`:128`) and `r6_b` (`:301`) | — | A22 | no | — |
| T11 | Clearing only `_redis_manager` does not resurrect a stale instance via `_initialized` (item 8) | **missing** | loop-identity file | target owner | no | decision 2 |
| T12 | Runtime failure on the **same** loop becomes recoverable | **`r6_d`, XFAIL(strict)** — must stay XFAIL until item 5 | `tests/test_redis_debounce.py` | **A22 session** | no | decision 4 |
| T13 | Proxy behaviour after invalidation/shutdown | **unspecifiable today** — see §4.1 | — | **blocked** | no | decision 3 |
| T14 | Cooldown + concurrent-init behaviour, including the unlocked third caller | **partial** — 6 debounce tests exist for the two internal callers; the third caller is **untestable from the target alone** | spans two files | both owners | no | decision 5 |
| T15 | End-to-end recovery against a real server | **UNEXECUTED** | existing real-Redis lane | release owner | **YES** | W1A A-3 or CI auth |

### 4.1 T13 — safe proxy recovery cannot be established without an API redesign

Stating the limitation the brief asks for rather than papering over it.

`RedisManagerCompatProxy.__getattr__` must return a value synchronously. Recovery requires
`await initialize()`. The options are all unattractive and all larger than "detect and rebuild in
the accessor":

1. **Make the proxy async** — breaks all 11 `global_safety` sites plus 6 other modules.
2. **Schedule a rebuild as a background task and return the stale client this once** — returns a
   known-dead client, and risks unhandled task exceptions, which the brief explicitly forbids.
3. **Raise on loop mismatch instead of returning** — fails fast and correctly, but converts a
   silent staleness into a new exception class for 17+ call sites, several on the kill-switch path.
   Given §3.3, the read path already raises `RedisUnavailable`, so this is the most consistent
   option — but it is a behaviour change to KILL-01 surface, in a file that is currently dirty.
4. **Accept the gap** — document that proxy consumers are not loop-recovered.

**Recommendation: option 4 for now, option 3 later as its own decision.** Do not claim proxy
coverage from accessor-only recovery.

---

## 5. Ownership and concurrency limitations

1. **Only one file in scope is clean.** `backend_app/backend/redis_manager.py`. Items 1–4 and 8 are
   fixable there. Items 5, 6, 7 and 9 are not.
2. **Item 5 (R6) lives in a dirty file.** The ~25 handlers at `core/cache/redis_manager.py:753-1016`
   belong to an active workstream (+185 / −5).
3. **`close()`'s contract is observable from that same dirty file.** Report 6 §4b found **9 call
   sites**, one being `core/cache/redis_manager.py:619` (via `disconnect()`, reached from
   `main.py:607`) plus 7 scripts. So **item 3's fix needs the cache-proxy owner's acknowledgement**,
   which the first four reports treated as an option-A-only concern.
4. **`get_redis_manager` is a FastAPI dependency 5× in `routers/exchange.py`** (`:318` `store_keys`
   with `require_aal2`, `:534`, `:670`, `:772`, `:1138`) — report 6 §4a. A rebuild is picked up
   *between* requests but not *during* one. `exchange.py` is `MM` and is the
   **AUTH-01 / CONN-01 / QUOTA-01 / TENANT-01** file, so rebuild semantics intersect that
   workstream even though no edit to it is proposed.
5. **`global_safety.py` is newly dirty** (+653 / −70, this phase) — §3.3's analysis is of in-flight
   code.
6. **`tests/test_redis_debounce.py` is the A22 session's** (+225 / −0, intact). Report 6 confirms
   A22 added `r6_d` itself, so retiring the marker needs no cross-session coordination — but it
   needs item 5 implemented first. All new tests go in **new files**, which both report 4 and
   report 6 independently recommended.
7. **Items 1, 2, 9 are unreproduced by any test** across all six reports. Items 1 and 2 have
   A22V **probe** evidence (`staging/probe_p11b_a22v_lifecycle_gaps.py`), which is a script, not a
   test.

---

## 6. Sequenced plan

The brief's proposed sequence is **sound and I recommend it unchanged**, with one ordering
constraint it does not state and two scope corrections.

**The constraint: phase P1 must precede P2.** P1 makes `close()` unbind the clients and reset
`_connected`. A loop-identity rebuild written before that must assume a bound-but-stale trio;
written after, it must tolerate `None`. Approving P2 first means revisiting it. Neither the A23
pool-lifecycle brief nor the A23 loop-identity brief says this, because neither session could see
both scopes.

| Phase | Content | Fixes | Leaves open | File(s) | Decision |
|---|---|---|---|---|---|
| **P1** | Pool lifecycle: G1/G2/G3 + A23-01. Collect locals and close on failure; publish-then-close on success; `close()` closes → resets `_connected` → unbinds, idempotently | **items 1, 2, 3** | 4, 5, 6, 7, 9 | target only | **D-A23C-1** |
| **P2** | Loop identity: record the creating loop beside `_connected = True`; check identity **before** the health fast path and **before** the cooldown gate (a loop mismatch must not wait out a connection-failure cooldown); handle item 8's dual cache | **item 4**, + item 8 | 5, 6, 7, 9 | target only | **D-A23C-2** (+ **D-A23C-3** for scope) |
| **P3** | Runtime-health invalidation for same-loop failures, in the ~25 handlers | **item 5**; flips `r6_d` | 6, 7, 9 | **cache proxy owner's file** | **D-A23C-4** |
| **P4** | Reconcile the two cooldowns and the unlocked third `initialize()` caller | **items 6, 9** | 7 | both files | **D-A23C-5** |
| **P5** | Focused new-file tests → `tests/test_redis_debounce.py` **serially** → wider Redis-adjacent suite → separately authorized real-Redis lane | verification | — | tests | **D-A23C-6** for the real-Redis lane |

**Scope corrections to the brief's sequence:**

* Its P2 says "with an explicit decision on accessor-only versus proxy coverage." Per §4.1 the
  honest answer is **accessor-only**, with the proxy gap documented and option 3 raised separately.
  Folding proxy recovery into P2 would turn a contained change into a 17-site API change on the
  kill-switch surface.
* Its P4 says "before claiming concurrency safety." Stronger: **concurrency safety cannot be
  claimed after P1 or P2 at all**, because the third `initialize()` caller is unlocked and lives in
  another owner's file. P1 and P2 can make `initialize()` *internally* safe; that is all.

**What remains open after every phase:** item 7 (proxy), unless D-A23C-3 chooses option 3. And
`r6_d` must stay XFAIL until P3 — an XPASS before then is the scope-creep alarm (§3.1).

---

## 7. Authorization — searched, and absent

Checked this phase: every `*.md` in `staging/` and the repo root; the launch-gate register; files
named `*APPROV*`/`*AUTHORIZ*`/`*DECISION*`/`*GRANT*`/`*SIGNOFF*`. Report 6 independently ran the
same search: "**17 hits, all requests. Zero grants.**" The one explicit marking is
`A22V:332 | 2 | Approve option B in the same file | same | **NOT GRANTED**`.

A clean file, six recommendations, and an existing xfail are present. None is approval.

| # | Decision | Owner | State |
|---|---|---|---|
| **D-A23C-1** | **Approve P1** — pool lifecycle repair (items 1, 2, 3) in `backend_app/backend/redis_manager.py` | owner of that file | **NOT GRANTED** |
| **D-A23C-2** | **Approve P2** — loop-identity detection and rebuild in the same file | same | **NOT GRANTED** |
| **D-A23C-3** | **Scope of P2: accessor-only, or extend to the proxy?** §4.1 recommends accessor-only now and a separate decision on raising from the proxy | same, + KILL-01 owner if the proxy is included | **NOT GRANTED — needs an explicit answer, not a default** |
| **D-A23C-4** | **Approve P3** — runtime-health invalidation in `core/cache/redis_manager.py:753-1016` | **owner of that file** (` M`, +185/−5) | **NOT GRANTED — the blocker for R6** |
| **D-A23C-5** | **Approve P4** — cooldown reconciliation + locking the third `initialize()` caller | both owners | **NOT GRANTED** |
| **D-A23C-6** | Real-Redis lane: local server (W1A **A-3**) or CI authorization | coordinator / release owner | **NOT GRANTED** |
| **D-A23C-7** | Create `tests/test_a23_redis_pool_lifecycle.py` and `tests/test_a23_redis_loop_identity.py` (new files; `test_redis_debounce.py` stays A22's) | target owner + A22 session | **NOT GRANTED** |
| **D-A23C-8** | Acknowledge that `close()`'s new contract is observable from `core/cache/redis_manager.py:619` and 7 scripts | cache-proxy owner + script owner | **NOT GRANTED** |
| **D-A23C-9** | Acknowledge rebuild semantics for the 5 `Depends(get_redis_manager)` handlers in `exchange.py` | AUTH-01 / CONN-01 / TENANT-01 owner | **NOT GRANTED** |
| **D-A23C-10** | Whether `aclose()` may be standardised on, raising the redis-py floor above 5.0.1 (installed: 5.0.8; `close()` is a deprecated alias emitting 3 warnings per shutdown) | target owner + release owner | open |
| **D-A23C-11** | Correct A21 §4/§5/§6's claim that `r6_d` gates B-2′ (§3.1). A21 is another session's report; **not edited** | A21 session | open |

**Minimum to start: D-A23C-1 plus D-A23C-7.** That unblocks P1 — the phase with the most confirmed
evidence (items 1–3, two of them execution-confirmed), the smallest blast radius, and no dependency
on any dirty file. D-A23C-8 should be recorded alongside it as a known consequence.

---

## 8. Tests run

**None.** Phase 4 is conditioned on approval, which is absent, and nothing changed that could alter
an outcome. The current state of the module, from A22's execution:

| Class | Count | Detail |
|---|---|---|
| Passed | 9 | 6 debounce + `r6_a`, `r6_b`, `r6_c` |
| Failed | 0 | — |
| **XFAIL** | **1** | `r6_d`, strict, failing at line 413 (`args = ('r6:probe', '2')` — the contract assertion, not the setup) |
| XPASS | 0 | — |
| Real-Redis executed | **0** | **lane UNEXECUTED**; 0 of 6 webhook assertions run, 6 skipped |

**Skips are not passes. No D5′ acceptance is claimed** — none is possible from a server-free
lifecycle test.

---

## 9. Status

| | |
|---|---|
| Items 1, 2, 3 (G1/G2/G3/A23-01) | **confirmed, NOT fixed** — awaiting D-A23C-1 |
| Item 4 (B-2′ loop identity) | **source-confirmed, NOT fixed** — awaiting D-A23C-2/3 |
| Item 5 (R6 / option A) | **execution-confirmed, NOT fixed** — blocked on D-A23C-4, another owner's dirty file |
| Items 6, 9 (third caller, F5) | **source-confirmed / unreproduced**, NOT fixed |
| Item 7 (proxy) | **gap documented; no safe fix without an API decision** (§4.1) |
| Item 8 (dual singleton) | **source-confirmed**, design input for P2 |
| `r6_d` | **XFAIL(strict)** — must remain so until P3; an XPASS earlier is a scope alarm |
| Corrections issued | **3** — §3.1 (A21), §3.2 (A22 F6), §3.3 (my own report 5) |
| Real-Redis lane | **UNEXECUTED** |
| D5′ | **no acceptance claimed** |
| QUOTA-01 / TENANT-01 | **OPEN** |
| Production Redis reliability | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from any of the six reports' recommendations, from the
target file being clean, from the existing strict xfail, or from silence.
