# Phase 11B-A9 — D5′ CI Failure Diagnostics

**Date:** 2026-10-10
**Scope:** recover the four missing D5′ failure tracebacks from CI run 38044138718, diagnose each
independently, and propose the smallest justified fix per failure. No source, workflow, launch
register, or unrelated file was modified. PR #7 left open.

---

## HEADLINE

**The tracebacks were retrievable.** `staging/PHASE11B_A5_A6_D5_PR_RESULT.md` claimed they were lost
to GitHub log truncation. That claim was wrong, and the correction matters: `gh` refuses to emit a
log containing ANSI escape sequences unless `--allow-escape-sequences` is passed, and the earlier
download path silently returned a **short** log — 13 310 lines against the real **14 559**. The
missing 1 249 lines contained the entire `FAILURES` block and the `short test summary info` section.

With the full log, the four failures resolve into **two distinct root causes, neither of which is a
defect in Redis behaviour or in the application's Redis-backed logic**:

| # | Failure | Root cause | Reached real Redis? |
|---|---|---|---|
| 1 | `test_the_slot_lock_is_mutually_exclusive_against_real_redis` | `AttributeError` — the slot-guard implementation is **not on `main`** | **No** |
| 2 | `test_the_guard_admits_exactly_the_limit_against_real_redis` | same `AttributeError` | **No** |
| 3 | `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **Keyspace split** — app cache on DB 0, test client on DB 15 | Yes, wrong database |
| 4 | `test_an_in_flight_delivery_blocks_a_concurrent_one` | same keyspace split | Yes, wrong database |

The brief's caution was warranted: these are **2 + 2**, not one shared cause.

Unchanged from A5/A6: **D5′ CI wiring = VERIFIED**, **Redis integration acceptance = FAILED**.
QUOTA-01 **OPEN**. Live trading **OFF / NOT CERTIFIED**. PR #7 **not merged**.

---

## 1. Phase 0 baseline (read-only, re-measured)

| Measurement | Value | Matches A5/A6 report? |
|---|---|---|
| Checked-out branch | `main` | yes |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | yes |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | yes |
| `refs/heads/ci/d5-prime-real-redis` | `49e70da23c2858ffc9a6f34d5db4521401477c82` | yes |
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` | yes |
| Staged entries in shared index | 22 | yes |
| PR #7 | `OPEN`, `mergedAt: null`, head `49e70da2…`, base `main`, 5 files, +1761 / −0 | yes |
| PR #7 last updated | 2026-10-10T10:13:20Z | unchanged since A6 |
| CI run 38044138718 | `completed` / `failure`, `run_attempt: 1`, event `pull_request` | yes |

Re-confirmed at the end of this phase: branch `main`, HEAD `074b772a…`, index hash `6d965c8e…`,
22 staged. **Nothing outside `staging/` was written.**

### Ownership of every file implicated below

| File | Git status | Owner |
|---|---|---|
| `backend_app/core/subscription_dependencies.py` | ` M` | **other workstream** — read-only here |
| `backend_app/core/cache/redis_manager.py` | ` M` | **other workstream** — read-only here |
| `backend_app/routers/billing.py` | ` M` | **other workstream** — read-only here |
| `tests/conftest.py` | ` M` | **other workstream** — read-only here |
| `backend_app/backend/redis_manager.py` | clean (= `main`) | unowned; production wiring |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | **D5′ (mine)** |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` | **D5′ (mine)** |
| `tests/real_redis_gate.py` | `??` | **D5′ (mine)** |

---

## 2. Evidence sources

Attempted in the order the brief specifies.

| # | Source | Outcome |
|---|---|---|
| 1 | Check annotations — `GET /check-runs/114190594445/annotations` | **4 annotations, no tracebacks.** 2 failures: `Process completed with exit code 1.` and `Event loop is closed`. Useful as a lead, not as evidence. |
| 1b | Check-run `output.summary` / `output.text` | **Empty** (`summary: ''`, `text` length 0) |
| 1c | Run artifacts — `GET /actions/runs/38044138718/artifacts` | **`total_count: 0`** — nothing to download |
| 2 | Raw job log — `gh api .../actions/jobs/114190594445/logs` | **Refused:** `the response contains terminal escape sequences; pass --allow-escape-sequences` |
| 2b | Same with `--allow-escape-sequences` | **SUCCESS — 2 614 914 bytes, 14 559 lines.** Complete, including `FAILURES` and `short test summary info` |

A rerun was therefore **not required**, and no workflow edit was needed. Steps 3 and 4 of the
brief's preferred order did not have to be exercised.

Artifacts written this phase (all under `staging/`, none committed):
`p11b9_job_raw.log` (raw, with escapes), `p11b9_job_clean.log` (escapes and timestamps stripped,
UTF-8), `p11b9_checkrun.json`, `p11b9_redis_manager_ci.py`, `p11b9_backend_mgr.txt`,
`p11b9_conn_region.txt`, `p11b9_symbols.txt`, `p11b9_imports.txt`, `p11b9_env.txt`.

### Reproduction command, for anyone re-deriving this

```powershell
python -c "import subprocess; r=subprocess.run(['gh','api','--allow-escape-sequences','repos/Narendra2212/vyomquant-saas/actions/jobs/114190594445/logs'],capture_output=True); open('job.log','wb').write(r.stdout)"
```

Write **bytes**, not a PowerShell `>` redirect — `>` produces UTF-16LE with a BOM, which is the
other trap that corrupted the A5/A6 extraction pass.

### Corrections to `staging/PHASE11B_A5_A6_D5_PR_RESULT.md`

| Claim in A5/A6 | Status | Correct value |
|---|---|---|
| "D5′ tracebacks NOT retrievable" | **WRONG** | Retrievable via `--allow-escape-sequences`; all four present |
| "`= FAILURES =` block contains exactly ONE traceback header" | **WRONG** — artifact of the short log | 25 traceback headers, including all four D5′ tests |
| "`short test summary info` = 0 hits" | **WRONG** — same artifact | Present at clean-log line 14 322 |
| "SKIPPED 21" | **WRONG** | Authoritative totals line: **62 failed, 12029 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed, 315 warnings in 587.72s** |
| "RDB saves prove the webhook path reached real Redis" | **OVERSTATED** | RDB saves prove real writes occurred; the metered quota test's own DB-15 client accounts for them. The webhook handler did reach real Redis — on DB 0 — but that is proven by tests 2 and 4 passing, not by RDB activity |
| "9/9 executed, 0 skipped" | **CONFIRMED** | All nine have explicit `PASSED`/`FAILED` result lines in the full log |
| "62 failed / 12029 passed" | **CONFIRMED** | matches the totals line exactly |
| "quota failures not attributable to real-Redis behaviour without tracebacks" | **CONFIRMED, and now explained** | They never executed a single Redis command |

---

## 3. Diagnosis — Failure 1

### `tests/test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis`

**Exact traceback** (clean log line 13508):

```
_________ test_the_slot_lock_is_mutually_exclusive_against_real_redis __________
tests/test_quota01_exchange_slot_is_atomic.py:658: in test_the_slot_lock_is_mutually_exclusive_against_real_redis
    lock_key = sd._slot_lock_key(user, EXCHANGE_CONNECTIONS)
               ^^^^^^^^^^^^^^^^^
E   AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute '_slot_lock_key'
```

**Setup and environment.** `REDIS_URL: redis://localhost:6379`,
`AERORA_TEST_REDIS_URL: redis://localhost:6379/15` (clean log lines 957-958). The test calls
`require_real_redis()` first, which **succeeded** — the gate reached the server. It then built its
own client, `aioredis.from_url(redis_url, decode_responses=True)`, and failed on the **next line**.

**Real Redis or mock?** **Neither.** The gate PINGed successfully and the client object was
constructed, but **no Redis command was ever issued**. The test died on an attribute lookup.

**Why.** `_slot_lock_key` does not exist in the `subscription_dependencies.py` that CI ran:

```
=== symbols in subscription_dependencies.py AS SHIPPED IN CI (commit 49e70da2 / main) ===
  exchange_connection_slot_guard: ABSENT
  _slot_lock_key:                 ABSENT
  check_exchange_connection_slot: PRESENT
=== same file IN WORKING TREE (uncommitted, ' M') ===
  exchange_connection_slot_guard: PRESENT   (line 1036)
  _slot_lock_key:                 PRESENT   (line 903)
  check_exchange_connection_slot: PRESENT   (line 838)
```

The approved five-file D5′ manifest shipped the **tests** for QUOTA-01's atomic slot guard but not
the **implementation**, which lives only in another workstream's uncommitted working-tree edit. This
is why the file passes locally and fails in CI: the two environments are running different code.

**Classification: environment / wiring defect — commit-manifest scope error.**
Not a test defect (the test is correct about what it expects). Not an application behaviour defect
(the behaviour was never executed).

**Smallest justified fix.** Ship `backend_app/core/subscription_dependencies.py` together with these
tests, or hold the two real-Redis tests back until it lands. **Owner: the workstream holding
` M backend_app/core/subscription_dependencies.py`.** I must not commit another workstream's
uncommitted file, and doing so would also drag in whatever else that edit contains.

**Reproduction / regression.** Reproduce by running the file against a tree where
`subscription_dependencies.py` matches `main`:
`git stash`-free check — `git show origin/main:backend_app/core/subscription_dependencies.py | Select-String _slot_lock_key` returns nothing. The regression guard that belongs with the fix is a
collection-time import assertion, so a missing implementation fails **loudly at collection** rather
than as 21 scattered `AttributeError`s.

**Confidence: HIGH.** Verified by direct symbol comparison between the exact committed tree and the
working tree, corroborated by 19 sibling failures carrying the identical exception.

---

## 4. Diagnosis — Failure 2

### `tests/test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis`

**Exact traceback** (clean log line 13513):

```
__________ test_the_guard_admits_exactly_the_limit_against_real_redis __________
tests/test_quota01_exchange_slot_is_atomic.py:717: in test_the_guard_admits_exactly_the_limit_against_real_redis
    lock_key = sd._slot_lock_key(user, EXCHANGE_CONNECTIONS)
               ^^^^^^^^^^^^^^^^^
E   AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute '_slot_lock_key'
```

Identical cause, setup, classification, fix, owner, and confidence to Failure 1. Recorded separately
because the brief requires per-failure accounting; grouped only on the strength of byte-identical
exceptions at the same call.

### The 19 siblings, and one true positive among them

The same file's 19 non-Redis failures all carry:

```
E   AttributeError: module 'backend_app.core.subscription_dependencies' has no attribute 'exchange_connection_slot_guard'
```

That accounts for 20 of the file's 21 failures. **The 21st is a genuine finding and should not be
dismissed with the rest:**

```
tests/test_quota01_exchange_slot_is_atomic.py:592: in test_store_keys_uses_the_guard_around_its_vault_write
    assert "exchange_connection_slot_guard" in src, (
E   AssertionError: store_keys does not use exchange_connection_slot_guard; the connection limit is
E   back to a read-then-write race (QUOTA-01)
```

The assertion message includes the live source of `store_keys`, which calls
`await check_exchange_connection_slot(user, supabase, body.exchange_id)` — the **non-atomic**
predecessor. So on `main` today, the exchange-connection limit is still a read-then-write race.
**This is a true positive and it is QUOTA-01's core defect, confirmed present on `main`.**

---

## 5. Diagnosis — Failure 3

### `tests/test_webhook_duplicate_delivery_real_redis.py::test_the_lock_is_recorded_in_redis_under_the_documented_key`

**Exact traceback** (clean log line 14000):

```
_________ test_the_lock_is_recorded_in_redis_under_the_documented_key __________
tests/test_webhook_duplicate_delivery_real_redis.py:344: in test_the_lock_is_recorded_in_redis_under_the_documented_key
    assert value is not None, f"no idempotency key at {key} after a delivery"
E   AssertionError: no idempotency key at webhook:razorpay:evt_9980b90aa904 after a delivery
E   assert None is not None
------------------------------ Captured log call -------------------------------
WARNING  BillingRouter:billing.py:421 Webhook IP validation disabled - development mode
WARNING  BillingRouter:billing.py:527 Missing timestamp header for razorpay webhook
WARNING  backend_app.core.cache.redis_manager:redis_manager.py:650 Cache GET failed for 'webhook:razorpay:evt_9980b90aa904': Event loop is closed
```

**Setup and environment.** `ENV=test`, `ENABLE_WEBHOOK_IP_VALIDATION=false`, Razorpay secret and
placeholder keys set, `DISABLE_REDIS` deleted, and `REDIS_URL` monkeypatched to
`redis://localhost:6379/15`. The A5 `pytest.fail` guard did **not** fire, so
`_get_active_cache()` returned a genuine cache object — not `MockRedisClient`, not `None`.

**Real Redis or mock?** **Real Redis on both sides — but on two different databases.**

**Why — the keyspace split.** `backend_app/backend/redis_manager.py` (clean, identical to `main`):

```python
43: REDIS_URL = os.getenv("REDIS_URL", "").strip()        # captured at IMPORT time
44: REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
45: REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

37: REDIS_DB_CACHE = 0

49: def get_redis_url(db: int = 0) -> str:
57:     if REDIS_URL:
62:         parsed = urlparse(REDIS_URL)
65:         query_params = parse_qs(parsed.query)
69:         query_params['db'] = str(db)                   # OVERRIDES any configured DB
82:         return new_url
83:     else:
86:         return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"

140:         cache = await aioredis.from_url(
141:             get_redis_url(REDIS_DB_CACHE),             # -> db 0
```

In CI the two Redis variables are (clean log lines 957-958):

```
REDIS_URL: redis://localhost:6379              <- no database path
AERORA_TEST_REDIS_URL: redis://localhost:6379/15
```

Both branches of `get_redis_url` therefore land on **DB 0**:

- `REDIS_URL` non-empty → `redis://localhost:6379?db=0` → DB 0
- `REDIS_URL` empty (conftest line 13 blanks it before this module imports) → `redis://localhost:6379/0` → DB 0

Meanwhile the test's own client is `redis.Redis.from_url(REDIS_URL, decode_responses=True)` where
the module constant is `REDIS_URL = _gate_redis_url()` = `AERORA_TEST_REDIS_URL` → **DB 15**.

So the handler wrote `webhook:razorpay:evt_9980b90aa904` to **DB 0**, and the test looked for it in
**DB 15**. `None`.

Two compounding details, both verified:

- `monkeypatch.setenv("REDIS_URL", …)` in `billing_env` and `os.environ["REDIS_URL"] = …` in
  `real_redis_cache` **cannot work**: line 43 is a module-level constant captured at import.
- Even a URL that *does* name a database is overridden. Measured locally with redis 5.0.8:
  `parse_url('redis://localhost:6379/15?db=0')` → `{'db': 0, 'host': 'localhost', 'port': 6379}`.
  The appended `?db=` wins over the path. So this is not merely a "CI forgot the `/15`" problem;
  the override is unconditional.

**The pass/fail partition confirms it — all six webhook tests, no exceptions:**

| # | Test | Needs the sync client and the handler to share a keyspace? | CI |
|---|---|---|---|
| 1 | `test_the_credential_gate_refuses_test_keys` | no — refused before the idempotency block | PASSED |
| 2 | `test_a_redelivered_webhook_is_ignored` | no — handler ↔ handler, both DB 0 | PASSED |
| 3 | `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **YES** — handler writes DB 0, test reads DB 15 | **FAILED** |
| 4 | `test_a_different_event_id_is_not_deduplicated` | no — negative assertion, handler ↔ handler | PASSED |
| 5 | `test_an_in_flight_delivery_blocks_a_concurrent_one` | **YES** — test writes DB 15, handler reads DB 0 | **FAILED** |
| 6 | `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | no — nothing is written anywhere | PASSED |

6 of 6 predicted correctly. Tests 2 and 4 are also the proof that the handler's Redis path genuinely
worked — dedup fired correctly against the real server, just in DB 0.

**Classification: environment / wiring defect in the test harness.** The test assumes the database
index it configures is the one the application cache will use. That assumption is false by design of
`get_redis_url()`. The application's dedup logic is **not** implicated by this failure.

**Smallest justified fix.** Resolve the test's client from the application's **effective** cache URL
rather than from `AERORA_TEST_REDIS_URL` directly, so both ends are guaranteed to agree:

```python
from backend_app.backend.redis_manager import REDIS_DB_CACHE, get_redis_url
_APP_CACHE_URL = get_redis_url(REDIS_DB_CACHE)   # whatever the app will actually use
```

and build `redis_client` from `_APP_CACHE_URL`, while the **gate** keeps using
`AERORA_TEST_REDIS_URL` for opt-in and liveness. **Owner: me** — the file is `??` and part of D5′.
Scoped to one test file; no application code; no other workstream's file touched.

Trade-off to state plainly: this moves the webhook test off the dedicated DB 15 and onto the
application's DB 0, which weakens keyspace isolation from other tests. The alternative that
preserves isolation is the application-side fix in §8, which is larger and needs its own
authorization.

**Reproduction / regression.** Reproduce without any application code by connecting two clients to
one server, one on `/0` and one on `/15`, writing via the first and reading via the second. The
regression guard that belongs with the fix is an explicit assertion, at fixture time, that the test
client and `_get_active_cache()` observe the **same** keyspace — write a sentinel through the
manager, read it with the sync client, and fail if absent. That converts this entire class of silent
mismatch into an immediate, named failure.

**Confidence: HIGH.** The CI environment echo, the pinned `REDIS_DB_CACHE = 0`, the import-time
capture, the measured `parse_url` precedence, and a 6-for-6 pass/fail partition all agree.

---

## 6. Diagnosis — Failure 4

### `tests/test_webhook_duplicate_delivery_real_redis.py::test_an_in_flight_delivery_blocks_a_concurrent_one`

**Exact traceback** (clean log line 14009):

```
______________ test_an_in_flight_delivery_blocks_a_concurrent_one ______________
tests/test_webhook_duplicate_delivery_real_redis.py:390: in test_an_in_flight_delivery_blocks_a_concurrent_one
    assert result.get("status") == "duplicate", (
E   AssertionError: a delivery arriving while evt_f94e849e12d5 was still in flight was processed anyway: {'status': 'success'}
E   assert 'success' == 'duplicate'
E     - duplicate
E     + success
------------------------------ Captured log call -------------------------------
WARNING  BillingRouter:billing.py:421 Webhook IP validation disabled - development mode
WARNING  BillingRouter:billing.py:527 Missing timestamp header for razorpay webhook
WARNING  backend_app.core.cache.redis_manager:redis_manager.py:650 Cache GET failed for 'webhook:razorpay:evt_f94e849e12d5': Event loop is closed
```

**Setup and environment.** Same `billing_env` as Failure 3. The test installs the in-flight marker
itself with the **sync** client — `client.set(key, "processing", nx=True, ex=60)` on **DB 15** — then
delivers through the handler.

**Real Redis or mock?** Real on both sides; different databases, as in Failure 3.

**Why.** The handler's Razorpay idempotency block, as shipped in the commit
(`backend_app/routers/billing.py:2346-2355`):

```python
idempotency_key = f"webhook:razorpay:{event_id}" if event_id else None
if idempotency_key:
    cached_val = await redis_manager.get(idempotency_key)
    if cached_val in ("completed", "1", "processing"):
        return {"status": "duplicate"}
    acquired = await redis_manager.set(idempotency_key, "processing", nx=True, ex=60)
    if not acquired:
        return {"status": "duplicate"}
```

The `processing` marker is in DB 15. The handler's `get` looks in DB 0 and finds nothing, so the
`NX` set in DB 0 succeeds and the delivery is processed → `{'status': 'success'}`.

The outcome is **over-determined**, which is worth stating precisely: the captured warning shows the
`get` additionally failed with `Event loop is closed`, returning `None` by fail-soft. But even a
fully healthy `get` against DB 0 would have returned `None`, because the marker was never in DB 0.
**The keyspace split is independently sufficient**; the closed loop is a confounder that produces
the same answer. This is why the diagnosis does not rest on the event-loop issue.

**Classification: environment / wiring defect in the test harness** — same cause, same fix, same
owner, same regression guard as Failure 3. **Confidence: HIGH.**

---

## 7. Secondary findings surfaced by the run (not the cause of any of the four)

Recorded because real-Redis coverage exists to surface exactly this class of thing, and because
neither item is visible under `MockRedisClient`.

### 7a. Webhook idempotency fails in both directions on a cache fault

`backend_app/core/cache/redis_manager.py` as shipped:

```python
643:    async def get(self, key: str):
645:        client = await self._get_active_cache()
646:        if client:
647:            try:
648:                return await client.get(key)
649:            except Exception as e:
650:                logger.warning(f"Cache GET failed for '{key}': {e}")
651:        return None

653:    async def set(self, key: str, value: Any, **kwargs):
657:            try:
658:                return await client.set(key, value, **kwargs)
659:            except Exception as e:
660:                logger.warning(f"Cache SET failed for '{key}': {e}")
661:        return False
```

A blanket `except Exception` plus a neutral return value means, in the webhook block above:

- **`get` faults → `None`** → the "already seen" check is skipped → a redelivery is **processed
  again**. For a billing webhook that is a double settlement.
- **`set` faults → `False`** → `not acquired` → the handler returns `{"status": "duplicate"}` with
  HTTP 200 → the provider stops retrying → **a real payment is never settled.**

Both are silent; the only trace is a `WARNING`. Observed live in this run — the
`Cache GET failed … Event loop is closed` lines are precisely this path executing.

**Confidence: HIGH** that the code path behaves this way (read directly from the committed source
and observed in the log). **MEDIUM** on production impact magnitude — not reproduced against a
faulting production cache. Owner: `backend_app/core/cache/redis_manager.py` (` M`, other workstream)
and `backend_app/routers/billing.py` (` M`, other workstream). **Needs its own ticket; out of scope
here.**

### 7b. Async Redis connections outlive their event loop

Two independent sightings:

- The webhook `Cache GET failed … Event loop is closed` warnings — a connection created on an
  earlier test's event loop, reused after pytest-asyncio closed it.
- An unrelated `PytestUnraisableExceptionWarning` under `tests/test_rate_limiter.py` (clean log
  ~14 260): `AbstractConnection.__del__` → `_close` → `RuntimeError: Event loop is closed`. This is
  the origin of the `Event loop is closed` check annotation, which is **not** from the webhook tests.

Harmless in production (one long-lived loop); a real cross-test contamination hazard in the suite.
Owner: `tests/conftest.py` (` M`, other workstream). **Separate ticket.**

### 7c. `get_redis_url()` discards an explicitly configured database index

An operator setting `REDIS_URL=redis://host:6379/3` gets **DB 0**, silently. `REDIS_URL` is also read
once at import, so any later environment change is ignored. Production-relevant beyond tests.
Owner: `backend_app/backend/redis_manager.py` (clean). **Separate ticket** — see §8.

---

## 8. Proposed minimal fixes

Nothing below has been applied. **No fix is claimed as verified:** there is no Redis server on this
machine and installing or starting one is outside scope, so none of these can be executed against
real Redis here.

| # | Fix | File | Owner | Risk | Authorization |
|---|---|---|---|---|---|
| F1 | Ship the slot-guard implementation with its tests, or hold the two real-Redis tests until it lands | `backend_app/core/subscription_dependencies.py` | other workstream | — | **coordination required** |
| F2 | Derive the webhook test's client from `get_redis_url(REDIS_DB_CACHE)` so both ends share a keyspace | `tests/test_webhook_duplicate_delivery_real_redis.py` | **me** | low — one test file, no app code | smallest; ready on request |
| F3 | Assert at fixture time that the test client and `_get_active_cache()` see the same keyspace | `tests/test_webhook_duplicate_delivery_real_redis.py` | **me** | low | pairs with F2 |
| F4 | Honour an explicit DB in `REDIS_URL`; read it at call time, not import time | `backend_app/backend/redis_manager.py` | unowned | **medium — changes connection behaviour for every deployment** | separate |
| F5 | Make webhook idempotency distinguish "cache says new" from "cache is unavailable" | `billing.py` + `core/cache/redis_manager.py` | other workstreams | **high — billing path** | separate |

F2 + F3 are the only items inside this phase's scope and ownership. F2 alone turns Failures 3 and 4
from environment artifacts into a genuine test of the application's dedup logic. F1 is the only
thing that can make Failures 1 and 2 pass — **until it lands they cannot pass in CI by
construction**, because the function they call does not exist.

---

## 9. Acceptance posture — the distinction preserved

| Claim | Verdict | Basis |
|---|---|---|
| **D5′ CI wiring** | **VERIFIED** (unchanged) | `redis:7-alpine` healthy; `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` in effect; 9/9 required tests have explicit result lines; **0 skipped** among them. The silent-skip defect the A5 fix targeted is gone, and the A5 `pytest.fail` guard correctly did **not** fire — the cache manager returned a real cache. |
| **Redis integration acceptance** | **FAILED** (unchanged) | 2 of 3 required quota real-Redis tests failed, 2 of 6 webhook tests failed. Acceptance requires all three quota tests to pass. |
| **Genuine end-to-end real-Redis coverage achieved** | **1 test of 9** | Only `test_concurrent_reservations_are_atomic_against_real_redis` exercised real Redis end-to-end against code present on `main` and passed — it uses its own `aioredis` client on DB 15 and does not depend on the application cache manager. |
| Overall job | `failure` | 62 failed, 12029 passed, 51 skipped, 322 deselected, 4 xfailed, 3 xpassed, 587.72s. **4 D5′-attributable, 58 unrelated** — not repaired, out of scope. |
| QUOTA-01 | **OPEN** | And now with its core defect confirmed live on `main`: `store_keys` still calls the non-atomic `check_exchange_connection_slot` (§4). The previously listed lock-release, database-backstop, production-population, and quota-limit gaps all remain. |
| Live trading | **OFF / NOT CERTIFIED** | unchanged |
| PR #7 | **OPEN, not merged** | unchanged |

A green job was never treated as sufficient evidence, and the red job is likewise not sufficient to
reject D5′ — the per-test accounting is what carries each verdict.

---

## 10. Safety confirmations

| Invariant | State |
|---|---|
| Files modified outside `staging/` | **none** |
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` — unchanged |
| 22 pre-staged files from other workstreams | intact |
| Checked-out branch / HEAD | `main` / `074b772a…` — unchanged |
| Commits, pushes, merges, reruns this phase | **none** |
| Staging, reset, stash, clean, revert | **none** |
| Production Redis / databases accessed | **no** |
| Infrastructure installed or started | **none** |
| Real orders / live trading | **none / OFF** |
| Unrelated failures touched | **none** |

---

## 11. Next authorized action

**Recommended, smallest first.**

1. **Apply F2 + F3** to `tests/test_webhook_duplicate_delivery_real_redis.py` (my file, test-only),
   then re-run only the three D5′ files in CI. Expected: webhook 6/6 pass — or, if they still fail,
   the failure is then a genuine application finding rather than a keyspace artifact. This is the
   single highest-information next step and it carries no application risk.
2. **Raise F1 with the `subscription_dependencies.py` owner.** Until the slot guard is committed,
   `test_the_slot_lock_is_mutually_exclusive_against_real_redis` and
   `test_the_guard_admits_exactly_the_limit_against_real_redis` cannot pass, and the 19 sibling
   failures will persist on any branch carrying this test file. Decide explicitly: ship the
   implementation, or hold the test file.
3. **File three tickets** for §7a (webhook idempotency fails open on read and closed on write),
   §7b (event-loop-scoped connection reuse), §7c (`REDIS_URL` database index discarded and captured
   at import). Each needs its own authorization; none is in this phase's scope.

No merge is recommended. PR #7 stays open, QUOTA-01 stays open, live trading stays off.
