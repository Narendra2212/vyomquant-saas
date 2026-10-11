# Phase 11B-A23 — Redis Lifecycle Repair (B + F3/F4): Authorization Gate

**STATUS: STOPPED AT THE PHASE 1 AUTHORIZATION GATE. NO IMPLEMENTATION OCCURRED.**

**Date:** 2026-10-10
**Destination check:** `staging/PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` verified
**ABSENT** before writing. Three sibling reports on this topic exist and were **read, not
modified**.

No file was changed by this task — not production source, not tests, not `pytest.ini`, not
`tests/conftest.py`. No stage, commit, push, branch change, or index mutation. No Redis, database,
exchange, or CI contact.

---

## HEADLINE

**Approval does not exist, and that is now the finding rather than a gap in analysis.**

The brief requires "authoritative approval from the owner of
`backend_app/backend/redis_manager.py`" and states that a recommendation, clean Git status, an
existing xfail, or prior investigation is **not** approval. I searched every `*.md` in `staging/`
and the repository root for any line combining an approval word with an option-B / F3 / F4 /
`redis_manager.py` reference. **Every hit is a request. Not one is a grant.** One is explicitly
marked otherwise:

```
PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md:332
  | 2 | Approve option B in the same file | same | **NOT GRANTED** |
```

**Five independent sessions have now reached this same gate and all five stopped:**

| Report | Verdict recorded |
|---|---|
| `PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md` (17 694 B) | "B-1 was NOT implemented… no approval record exists from any owner" |
| `PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` (26 508 B, mine) | "No implementation was performed. Approval … is required" |
| `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` (21 296 B) | option B "**NOT GRANTED**" |
| `PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` (17 848 B) | "**STOPPED AT THE PHASE 1 AUTHORIZATION GATE.**" |
| **this report** | same |

And the target file is untouched by all of them — byte-identical to `HEAD`:

```
backend_app/backend/redis_manager.py   status ''   blob d7b0e811b615…   HEAD d7b0e811b615…
occurrences of  _loop  get_running_loop  aclose  _created_on_loop  _pools   ->  0 0 0 0 0
```

**~83 KB of investigation across five reports now sits behind one unmade decision.** Further
analysis has reached diminishing returns; §6 is deliberately a decision request, not more study.

This report therefore adds only what the four siblings do not already contain (§4), and defers to
them rather than restating their call graphs.

---

## 1. Preservation baseline — entry and exit identical

| Invariant | Value | |
|---|---|---|
| Branch / HEAD | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| Index **content digest** | `7379218b992868bbbb693e22f7c94b0ae7d45ff9bbacf71d9ee4efe1a201392d` | **SAME as A22** |
| Staged paths | 22 | unchanged |
| Unmerged | 0 | unchanged |
| Files changed by this task | **0** | — |

Entry snapshot: `staging/p11b23_entry_status.txt`.

### Target and restricted files, re-measured (not taken from A22)

| File | Status | vs HEAD | Blob | Disposition |
|---|---|---|---|---|
| `backend_app/backend/redis_manager.py` | **clean** | — | `d7b0e811b615` | **target; NOT edited.** Clean is not authorization |
| `backend_app/core/cache/redis_manager.py` | ` M` | +185 / −5 | `6c8a3692c350` | other workstream — read-only |
| `tests/conftest.py` | ` M` | +172 / −0 | `c6c67672d007` | other workstream — read-only |
| `pytest.ini` | ` M` | **+13 / −0** | `a6f0a3d4bed3` | concurrent — read-only |
| `backend_app/core/subscription_engine.py` | ` M` | **+61 / −4** | `256f03994b39` | quota owner (was +57/−4 at A22 — still moving) |
| `tests/test_redis_debounce.py` | ` M` | +225 / −0 | `5a8db7a8afbd` | **my A22 R6 tests, intact** |

`pytest.ini`'s +13 is the `realaudit` marker documentation — unrelated to xfail handling.
**`xfail_strict` is still absent**, so the explicit `strict=True` on the R6 contract test remains
necessary and correct.

### The strict-xfail node ID and its ownership

```
tests/test_redis_debounce.py::test_r6_d_desired_a_runtime_failure_should_be_recoverable
  marker at L379-388 (xfail, strict=True)   test body at L390
```

Added by **this session** in A22, when the file was tracked and clean. No other workstream has
edited it since (` M`, +225/−0, the A22 delta exactly). So retiring it after an authorized fix
needs no cross-workstream coordination — but it does need the fix first, and
`PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md:258` notes another session intends a *new* file
(`tests/test_a23_redis_pool_lifecycle.py`) rather than editing this one, which avoids a collision.

---

## 2. Authorization search — method and result

| Source searched | Result |
|---|---|
| Every `*.md` in `staging/` and the repo root, for an approval word co-occurring with option B / F3 / F4 / `redis_manager.py` | **17 hits, all requests.** Zero grants |
| Files whose *name* records an approval (`*APPROV*`, `*AUTHORIS*`, `*AUTHORIZ*`, `*SIGNOFF*`, `*GRANT*`) | 18 found; **none grants this change** |
| `git log --all` for commits touching the target file | 9 commits, none recent, none an approval record. The file has not been modified on any branch in this work |
| This task's own brief | Conditions on approval ("only after explicit owner approval"); does not grant it |

Representative hits, all of which are asks:

```
A21:224  | R1 | Approve B-2′ instead of B-1 — loop-identity detection and rebuild …
A22:375  | backend_app/backend/redis_manager.py | clean | Approve or reject option B …
A22V:331 | 1 | Approve Step 0 (pool cleanup + close() health reset) …
A22V:332 | 2 | Approve option B in the same file | same | **NOT GRANTED** |
A23R:257 | 1 | Implement Step 0 changes 1–4 in backend_app/backend/redis_manager.py | owner …
```

**Gate verdict: CLOSED.** Phase 2 and Phase 3 were not entered.

---

## 3. Source findings confirmed (not re-derived)

The call graph, the `_connected` single-writer finding, the sticky fast path, F3 and F4 are
established in A20 §2, A22 §2, and the sibling A21/A22V/A23R reports. I verified they still hold
against today's file (`d7b0e811b615`, unchanged) and do not restate them.

Confirmed afresh:

| # | Fact | Line |
|---|---|---|
| | three pools created per `initialize()` | 140, 148, 156 |
| | `close()` exists and is never called by `initialize()` | 215-222 |
| | `_connected` written only at 122 / 171 / 180 | — |
| | `get_redis_manager()` sticky fast path | 548-549 |
| | `try_reconnect()` early return on health | 192 |

---

## 4. What this report adds that the four siblings do not

### 4a. The rebuild would change what five FastAPI handlers receive — in the AUTH-01 file

`get_redis_manager` is not only awaited directly; it is wired as a **FastAPI dependency** five
times, all in `backend_app/routers/exchange.py`:

| Line | Endpoint |
|---|---|
| 318 | `store_keys` — `POST /keys` (also `Depends(require_aal2)`) |
| 534 | `list_exchanges` — `GET ""` / `GET "/"` |
| 670 | `delete_connection` — `DELETE /{exchange_id}` |
| 772 | `get_exchange_auth_schema` — `GET /schema/{exchange_id}` |
| 1138 | active-connections listing |

Consequence for the design: a rebuild is picked up **between** requests, because the dependency
resolves per request — but **not during** one, since the handler holds the object it was handed.
A handler that obtained a manager and then hit a loop mismatch mid-request would still use the
stale client for the remainder of that request.

This matters twice over: `exchange.py` is `MM` and is the **AUTH-01 / CONN-01 / QUOTA-01 /
TENANT-01** file. So rebuild semantics intersect that workstream's surface, and the owner should
be told even though no edit to `exchange.py` is proposed.

The other direct consumers re-resolve on every use and are therefore unaffected:
`asset_universe.py:557`, `strategy_service.py:6170`, `training_worker.py:3226`,
`core/cache/redis_manager.py:496`, plus `RedisManagerCompatProxy.__getattr__` (580-590), which
re-imports the module global on each attribute access.

### 4b. F3's "make close() idempotent" reaches into the other workstream's file

`RedisManager.close()` has **9 distinct call sites**, and one is not ours:

```
backend_app/core/cache/redis_manager.py:619   await self._redis_manager.close()   <- OTHER WORKSTREAM
scripts/test_data_stream_worker.py:90
scripts/test_event_bus.py:98
scripts/test_execution_worker.py:106
scripts/test_order_event_flow.py:57, 86, 114
scripts/test_strategy_worker.py:131
```

So any change to `close()`'s contract — idempotency, health reset, or `aclose` migration — is
observable from `core/cache/redis_manager.py`. That is a **coordination requirement for F3**,
not just for option A. The four sibling reports treat the cache proxy as an option-A concern only;
this call site shows F3 touches it too.

### 4c. The installed close/teardown surface, measured

```
redis 5.0.8
Redis.aclose              True
Redis.close               True     signature: close(self, close_connection_pool: Optional[bool] = None) -> None
ConnectionPool.aclose     True
ConnectionPool.disconnect True
```

The current `close()` uses `.close()`, which in redis-py 5.x is the **deprecated alias** for
`aclose()`. Both exist on 5.0.8, so either works today, but a design that standardises on
`aclose()` raises a floor on the redis-py pin.
`PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md:259` already flags that pin question as an open
decision; this measurement answers *what is installed*, not *what may be required*.

---

## 5. Proposed minimal diff plan — NOT APPLIED

Line anchors are against `backend_app/backend/redis_manager.py` @ `d7b0e811b615`. Four changes,
one file, no behaviour change to any other module.

| # | Change | Location | Intent |
|---|---|---|---|
| **1** | Record the creating loop when pools are installed | `__init__` L119-125 (add a `_pool_loop` attribute, default `None`) and `initialize()` L168-171 (set it to `asyncio.get_running_loop()` beside `_connected = True`) | Give the manager a way to notice the loop its pools belong to |
| **2** | Check loop identity **before** the health fast path, and **before** the cooldown gate | `get_redis_manager()` L548-553 | A loop mismatch must force a rebuild, and must not have to wait out a connection-failure cooldown it has nothing to do with (A22 §6). Preserve the healthy same-loop fast path untouched |
| **3** | **F3** — close superseded clients on a successful rebuild | `initialize()` L168-171 | Capture the outgoing `self._cache/_queue/_events` **before** assigning the new ones, assign the new trio, then best-effort close the old trio. Closing after assignment, not before, so a usable client is never withdrawn ahead of its replacement |
| **4** | **F4** — close what this attempt built, on partial failure | `initialize()` L138-184 | Track the locals created so far; in the `except` at L178, best-effort close them, swallowing cleanup errors, then preserve the original failure (`_connected = False`, `_last_failed_at`, `return False`). Leave the previously installed `self._*` in place — do not clear usable references on a failed attempt |

Deliberately **excluded** from the plan:

- **Option A** (runtime-failure invalidation). Needs the `core/cache/redis_manager.py` owner,
  because the exception is caught there. Kept separate for future coordination, as instructed.
- **F5** (the two-cooldown asymmetry: `get_redis_manager` calls `initialize()` directly and does
  not consult `self._last_failed_at`). Still unreproduced; raising it here would widen scope.
- Any `aclose()` migration beyond what change 3/4 require — see §4c and the pin question.
- Any edit to `tests/conftest.py`, `pytest.ini`, loop scope, the cache proxy, quota code, or
  exchange-slot code.

### Regression coverage the plan would need

Mapped against the brief's six items, and against what already exists so nothing is duplicated:

| # | Required | Status |
|---|---|---|
| 1 | Same-loop healthy access does not rebuild | **exists** — `test_healthy_fast_path_skips_lock` (L128) and R6 `r6_b` (L301) both assert `initialize` is not called |
| 2 | A different/closed loop cannot reuse an invalid client | **missing** — new: two successive `asyncio.run` calls over one manager |
| 3 | Successful replacement closes all superseded clients | **missing** — new: assert `aclose`/`close` awaited on all three outgoing clients |
| 4 | Failure at the 1st / 2nd / 3rd client closes everything that attempt built | **missing** — new, three parametrised cases |
| 5 | Cleanup errors do not mask the original failure | **missing** — new: make the close raise, assert the original failure is what surfaces |
| 6 | R6 characterization stays accurate until option A is authorized | **exists** — `r6_b`/`r6_c` describe the *runtime-failure* path, which this plan does not change. `r6_d` (strict xfail) stays **XFAIL**, because B alone does not make a runtime failure recoverable |

Item 6 is worth stating explicitly: **the proposed plan does not flip `r6_d`.** Only option A
does. If `r6_d` turned XPASS after implementing B, that would signal accidental scope creep into
option A — which makes the strict marker a useful guard during implementation, not just
afterwards.

`PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md:258` proposes putting the new tests in
`tests/test_a23_redis_pool_lifecycle.py`. I agree and would not add them to
`tests/test_redis_debounce.py`: keeping the loop/pool lifecycle tests in their own module avoids
two sessions editing one file.

---

## 6. Approval required

| # | Decision | Owner | State |
|---|---|---|---|
| **1** | **Implement changes 1–4 of §5** in `backend_app/backend/redis_manager.py` | owner of that file (tracked, clean, no competing edit) | **NOT GRANTED** |
| **2** | Create `tests/test_a23_redis_pool_lifecycle.py` with coverage items 2–5 | same | **NOT GRANTED** |
| 3 | Accept that `close()`'s contract becomes visible to `core/cache/redis_manager.py:619` (§4b) | **owner of `core/cache/redis_manager.py`** | **NOT GRANTED** — newly identified here |
| 4 | Accept the rebuild semantics for the five `Depends(get_redis_manager)` handlers in `exchange.py` (§4a) | **owner of the AUTH-01 / CONN-01 / TENANT-01 work** | **NOT GRANTED** — newly identified here |
| 5 | Whether `aclose()` may be standardised on, raising the redis-py floor | same as 1, plus release owner | open (also A23R item 3) |

Items 3 and 4 are new to this report. Both are consequences of F3 and of option B respectively,
and neither was in the four sibling reports' coordination lists.

**Option A remains separate** and is not requested here. It needs the cache-proxy owner and
carries the broad blast radius documented in A20 §3e and A22 §6.

---

## 7. Verification performed

No implementation, so no implementation verification. Two read-only confirmations:

```
$ git status --porcelain -- backend_app/backend/redis_manager.py
(empty)                                    # byte-identical to HEAD

$ python -c "import redis, redis.asyncio as a; …"
redis 5.0.8 · Redis.aclose True · Redis.close True
ConnectionPool.aclose True · ConnectionPool.disconnect True
```

**No test suite was run in this phase.** Nothing changed that could alter a test outcome, and the
brief's Phase 4 is conditioned on Phase 2 having been entered. For the record, the R6 results from
A22 stand unchanged and are the current state of that module:

| Class | Count | Detail |
|---|---|---|
| Passed | 9 | 6 pre-existing + `r6_a`, `r6_b`, `r6_c` |
| Failed | 0 | — |
| XFAIL | 1 | `r6_d`, strict, failing at line 413 for the documented reason |
| XPASS | 0 | — |
| Skipped real-Redis tests | 0 | none in this module; it is server-free by construction |
| Real-Redis lane executed | **no** | no server started, none contacted |

---

## 8. Limitations

1. **The decision, not the analysis, is the blocker.** Five reports, ~83 KB, one unmade call.
2. **F3 and F4 remain unreproduced.** No test demonstrates the pool leak on rebuild or on partial
   initialization; both are read from source (`initialize()` never calls `close()`).
3. **F5 remains unreproduced** and is excluded from the plan.
4. **The §4a mid-request staleness is reasoned, not measured.** It follows from FastAPI resolving
   dependencies per request, but no test exercises a rebuild during an in-flight request.
5. **The webhook errors are untouched.** Five setup errors in CI run 38061973739 persist; this
   phase changed no behaviour.
6. **`subscription_engine.py` is still moving** (+61/−4, was +57/−4 at A22). The quota workstream
   is active; A20's Decision Record A is theirs, not mine.

---

## 9. Status

| | |
|---|---|
| **A23 implementation** | **NOT PERFORMED** — Phase 1 gate closed, Phases 2–3 not entered |
| **Loop identity (option B)** | designed and line-anchored; **awaiting approval** |
| **F3 / F4** | designed; **awaiting approval**; F3 newly shown to need the cache-proxy owner too |
| **Option A** | **deliberately separate**, for future coordination with the cache-proxy owner |
| **R6 contract** | characterized in A22; `r6_d` remains XFAIL and **should remain XFAIL after B** |
| **QUOTA-01** | **OPEN** |
| **TENANT-01** | **OPEN** |
| **Production Redis reliability** | **OPEN** |
| **Live trading** | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from the sibling reports' recommendations, from the
clean status of the target file, from the existing strict xfail, or from silence.

Artifacts: `p11b23_phase0.{py,txt}`, `p11b23_recon.{py,txt}`, `p11b23_consumers.{py,txt}`,
`p11b23_entry_status.txt`.
