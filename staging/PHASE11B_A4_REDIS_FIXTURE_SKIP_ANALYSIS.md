# Phase 11B-A4 — Remaining Redis Fixture Skip: Analysis

## Recommendation: **MINIMAL CORRECTION JUSTIFIED**

One call site, one file, test-only. **Not implemented** — awaiting explicit authorization,
per the brief.

**No files were modified in this task.** No branch, stage, commit, push, PR or CI trigger.
QUOTA-01 remains **OPEN**. Live trading remains **OFF**.

---

## 1. Baseline — re-established, not assumed

```
branch                     : main
HEAD                       : 074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main                : 074b772ab16cece62cbfca0f2b78497be2ae56d5
ahead / behind             : 0 / 0
tracked modified           : 120
files staged in .git/index : 22
reflog head                : 074b772a  commit: "Give the deploy gate permission to read ..."
```

### File ownership, re-measured

| Path | Git state | Owner |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | ` M` modified, uncommitted | **shared** — D5′ hunk 1 + another workstream's hunk 2 |
| `tests/real_redis_gate.py` | `??` untracked | D5′ |
| `tests/test_quota01_exchange_slot_is_atomic.py` | `??` untracked | D5′ |
| `tests/test_quota01_metered_reservation_is_hard.py` | `??` untracked | D5′ |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` untracked | D5′ (carries the A3 correction) |
| `backend_app/core/cache/redis_manager.py` | ` M` modified | **ANOTHER WORKSTREAM — do not touch** |
| `tests/conftest.py` | ` M` modified | **ANOTHER WORKSTREAM — do not touch** |

The A3 report still matches the tree. Two files central to this analysis —
`redis_manager.py` and `conftest.py` — are **concurrently modified**, which sets the
ownership boundary in §5.

Concurrent activity continued during this task (new `staging/PHASE11_*` artefacts from
another workstream). Nothing of theirs was touched.

---

## 2. Fixture execution trace

### The two independent connection paths

This is the heart of it. **The gate and the application manager connect separately.**

| | Shared gate | Backend cache manager |
|---|---|---|
| Variable read | `AERORA_TEST_REDIS_URL` (`real_redis_gate.py`, `OPT_IN_VAR`) | `REDIS_URL` (bridged by the fixture at line 184) |
| Client | **sync** `redis.Redis.from_url(...)` | **async**, via `_ensure_manager()` → `initialize()` |
| Operation | one `PING`, 2 s timeout | pool construction + its own connect |
| Outcome | `GATE_STATE` ∈ {skip, fail, run} | a real cache, `MockRedisClient`, or `None` |

A successful gate PING is therefore **not** a guarantee that the manager will succeed.

### Order of execution, per webhook test — MEASURED

```
collection      real_redis_required           (pytestmark, line 99)   -> SKIP if opt-in unset
setup #1        _real_redis_is_usable          (autouse, line 103)     -> FAIL if configured+broken
setup #2        billing_env                    (line 148)
setup #3        real_redis_cache(billing_env)  (line 163)              -> SKIP on mock/None  <-- the issue
setup #4        handler(real_redis_cache, ...) (line 174)
setup #5        redis_client                   (line 239)
call            the test body
teardown        redis_client deletes `created` keys
```

**Q1 — does the gate run before `real_redis_cache` and its dependencies? YES, measured.**
With `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15` the traceback is:

```
ERROR at setup of test_the_credential_gate_refuses_test_keys
tests\test_webhook_duplicate_delivery_real_redis.py:119: in _real_redis_is_usable
E   Failed: AERORA_TEST_REDIS_URL is set but the server did not answer PING (TimeoutError). ...
```

Line 119 is `require_real_redis()` inside the autouse fixture. It fired for all six tests,
including the one that depends on `billing_env`. So the autouse fixture is ordered ahead of
the explicitly requested fixtures, as intended.

### `_get_active_cache()` — the four branches

`backend_app/core/cache/redis_manager.py:676`:

```python
if DEV_MODE and not getattr(self, '_dev_mode_bypass', False):   # line 727
    return self._mock_client                                     # (A) dev-mode mock

await self._ensure_manager()
if self._redis_manager and getattr(self._redis_manager, "cache", None):
    return self._redis_manager.cache                             # (B) REAL        line 732

if _mock_cache_is_acceptable():
    return self._mock_client                                     # (C) fallback mock

logger.error(...)
return None                                                      # (D) fail-closed
```

Supporting definitions: `DEV_MODE` is a **module-level constant** frozen at import
(line 72: `DEV_MODE` ← `DEV_MODE=true` **or** `ENV=development`); `_mock_cache_is_acceptable()`
(line 43) delegates to `runtime_env.local_state_is_acceptable()`, reading `DEV_MODE`/`ENV`/
`ENVIRONMENT` and **never** the trading mode; `_bypass_dev_mode()` is the manager's own
test seam (line 508).

`real_redis_cache` calls `_bypass_dev_mode(True)` (line 185) to disable (A), then at
line 186–191:

```python
cache = await redis_manager._get_active_cache()
if cache is None or isinstance(cache, MockRedisClient):
    pytest.skip(f"redis_manager still resolves to {type(cache).__name__}; ...")
```

**Q3 — precise skip conditions.** The fixture skips on **both** (C) and (D):

| Branch | Condition | Fixture result |
|---|---|---|
| (A) | `DEV_MODE` and no bypass | disabled by line 185 |
| (B) | `_ensure_manager()` yields `.cache` | proceeds — the intended path |
| (C) | no `.cache` **and** `_mock_cache_is_acceptable()` | **SKIP** |
| (D) | no `.cache` **and not** acceptable | **SKIP** |

**Q2 — can a successful PING be followed by a mock fallback? YES, and it is measured.**
`staging/probe_p11b4_cache_fallback.py`, with `REDIS_URL` pointing at a dead loopback port
and the suite's own preamble (`DEV_MODE=true`, `ENV=test`):

```
DEV_MODE (import-time constant)   = True
_mock_cache_is_acceptable()       = True
1. WITHOUT the bypass:  _get_active_cache() -> MockRedisClient
2. WITH _bypass_dev_mode(True):  _get_active_cache() -> MockRedisClient   (branch C)
   predicate `cache is None or isinstance(cache, MockRedisClient)` -> True
   => pytest.skip(...)
```

So branch (C) is reachable with the bypass applied, and it produces a **skip**. This is an
observation, not a reading of the code.

**Q4 — is there a reliable way to distinguish real from in-memory? YES, and it is already
used.** `isinstance(cache, MockRedisClient)` plus the `None` check at line 187 covers all
three non-real outcomes. **Detection is not the defect; the disposition is.** The fixture
correctly identifies the fallback and then reports it as a skip.

**Q6 — test data isolation and cleanup: SOUND.**

- Event ids are `f"evt_{uuid.uuid4().hex[:12]}"` — unique per run, no collision across runs.
- Keys are registered in `created` and deleted in `redis_client`'s teardown (line 239 ff.),
  each `delete` wrapped so one failure cannot strand the rest.
- `_event()` deliberately uses `payment.authorized`, not `payment.captured`, to stay on the
  idempotency control and off the Supabase settlement path.
- CI pins database index `/15`; **no test issues `FLUSHDB`**.
- `conftest.py`'s autouse `_restore_shared_redis_singleton` restores `_dev_mode_bypass`, so
  the seam flipped at line 185 cannot leak into another test.

---

## 3. Phase 2 — non-destructive verification

| Case | Command / probe | Result |
|---|---|---|
| Gate unset | `pytest tests/test_webhook_duplicate_delivery_real_redis.py -rs` | **6 skipped**, exit 0, shared-gate reason |
| Unreachable loopback | same, `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15` | **6 errors**, exit 1, from `_real_redis_is_usable` line 119 |
| Non-loopback rejected | same, `AERORA_TEST_REDIS_URL=redis://redis.example.internal:6379/15` | **errors**, exit 1, host not echoed |
| Manager after a successful PING | — | **NOT TESTED — no Redis server available** |
| Fixture when manager returns a mock | `staging/probe_p11b4_cache_fallback.py` | **MockRedisClient → predicate True → skip**, measured |
| Quota + webhook regression | all three gated files, default path | **26 passed, 9 skipped, 0 failed** |

### Explicitly untested

- **Branch (B) — a genuinely reachable Redis.** `staging/redis/redis-server.exe` is absent
  and nothing listens on 6379. The gate's `run` state and the manager's real-cache path have
  **never executed**, here or in CI. No infrastructure was installed (not authorized).
- Whether CI's `ENV: test` plus the manager's connect path actually yields a real cache.
- No production Redis, database or exchange endpoint was contacted. The unreachable test
  dialled `127.0.0.1:6399` by design.

---

## 4. Phase 3 — decision

### MINIMAL CORRECTION JUSTIFIED

**The defect.** `tests/test_webhook_duplicate_delivery_real_redis.py:188` calls
`pytest.skip(...)` when `_get_active_cache()` returns `MockRedisClient` or `None`. By the time
that fixture runs, `real_redis_required` has already established that the opt-in **is set**
and `_real_redis_is_usable` has already established that the endpoint **answers PING**. So
reaching line 188 means: *you asked for a real Redis, the gate reached it, and the application
manager still could not use it.* That is a wiring or configuration fault, not an absence — and
it is reported as a skip, which the acceptance rule forbids for a required Redis assertion.

It is the same conflation A3 fixed one layer up, surviving one layer down: the gate now
separates "not asked for" from "asked for and broken", and this fixture still collapses
"broken" into a skip.

**Proposed change — one call site:**

```python
# tests/test_webhook_duplicate_delivery_real_redis.py, line 188
-        pytest.skip(
+        pytest.fail(
             f"redis_manager still resolves to {type(cache).__name__}; cannot exercise "
             f"the lock against the real server, and the in-memory dict would prove "
             f"nothing (see BUG-027)."
         )
```

plus a sentence in the message recording that the gate already PINGed the endpoint
successfully, so the reader knows this is a manager fault rather than a missing server.

### Why this is the smallest safe change

- **Test-only.** No application code. `backend_app/core/cache/redis_manager.py` is **not
  touched** — and must not be, as it is concurrently modified (§1).
- **Already in the D5′ allowlist.** No new file enters the manifest; the five-file scope and
  the 1,728-insertion diffstat are unchanged in shape.
- **Cannot affect the default lane.** With the opt-in unset, `real_redis_required` skips at
  collection and this fixture never runs. The measured default result (6 skipped) is
  unchanged.
- **Cannot affect unrelated suites.** `real_redis_cache` is module-local; nothing else uses
  it (it is defined and consumed only in this file).
- **Detection already exists** and is reliable; only the disposition changes.

### Rejected alternatives

- **Changing `_get_active_cache()` so the fallback cannot occur.** Touches concurrently
  modified application code, alters fail-soft behaviour for every caller, and risks unrelated
  suites. Out of bounds.
- **Adding a "require real cache" flag to the manager.** Broadens the manager's contract for
  a test-only need; the fixture can already tell real from mock.
- **Removing the mock-fallback check entirely.** Would let the in-memory dict satisfy these
  tests — the exact false assurance (BUG-027) the file exists to prevent. Strictly worse.
- **Leaving it alone.** Defensible only if one accepts that a CI run could report these six
  as green skips while the three quota tests fail on the same broken service. The acceptance
  rule says otherwise.

---

## 5. Ownership boundaries

| Component | Boundary |
|---|---|
| `tests/test_webhook_duplicate_delivery_real_redis.py` | **This workstream.** The proposed change is here, and only here. |
| `tests/real_redis_gate.py` | This workstream. **No change needed** — its interface already expresses the three states. |
| `backend_app/core/cache/redis_manager.py` | **Another workstream** (` M`). Read-only here. Any change to `_get_active_cache()` or the fallback needs that owner. |
| `tests/conftest.py` | **Another workstream** (` M`). Read-only; explicitly excluded from the manifest. |
| `.github/workflows/01-pr-check.yml` hunk 2 (`provisioning-and-isolation`) | **Another workstream.** Excluded from the manifest; preserved byte-for-byte. |
| `tests/test_api04_store_failure_after_startup.py` | Independent; excluded. |

---

## 6. Phase 4 — CI authorization readiness

`staging/verify_p11b3_commit_manifest.py` re-run against the current tree (exit 0):

```
PROSPECTIVE COMMIT CONTENTS (5 files):
   .github/workflows/01-pr-check.yml                  |  27 +
   tests/real_redis_gate.py                           | 187 +
   tests/test_quota01_exchange_slot_is_atomic.py      | 867 +
   tests/test_quota01_metered_reservation_is_hard.py  | 264 +
   tests/test_webhook_duplicate_delivery_real_redis.py| 383 +
   5 files changed, 1728 insertions(+)

whitespace/conflict check                                : CLEAN
staged workflow diff contains AERORA_TEST_REDIS_URL      : True
staged workflow diff contains provisioning-and-isolation : False
files outside the allowlist in the commit                : none
shared .git/index unchanged                              : True
files staged by others still                             : 22 (was 22)
```

| Required confirmation | Status |
|---|---|
| Shared index untouched | **CONFIRMED** — sha256 identical, 22 staged files still 22 |
| `provisioning-and-isolation` hunk excluded | **CONFIRMED** — absent from the staged workflow diff; hunk 2 is +143/−0, and with zero deletions anywhere its lines cannot be lost by omission |
| No unrelated test or API04 changes | **CONFIRMED** — `files outside the allowlist: none` |
| Configured-but-broken cannot silently pass through a skip | **PARTIAL.** The *gate* layer is closed and measured (6 errors). The *fixture* layer at line 188 is still a skip — this is the §4 correction |
| Reachable Redis path classified | **UNVERIFIED**, explicitly. Branch (B) has never executed anywhere |

---

## 7. Remaining uncertainty

1. **The reachable-Redis path is unverified** — the only case that matters for VERIFIED, and
   the one no local run can reach. Both the gate's `run` state and the manager's branch (B)
   are untested.
2. **`DEV_MODE` is frozen at import** (`redis_manager.py:72`). Whether CI's `ENV: test` and
   whatever sets `DEV_MODE` combine to let `_bypass_dev_mode(True)` reach branch (B) is
   unverified. If they do not, the fixture guard fires — which is exactly why its disposition
   matters.
3. **`_mock_cache_is_acceptable()` returns True under `ENV=test`** (measured). So in CI the
   fallback is branch (C) — mock — rather than (D) — `None`. Both are caught, but a reader
   should expect `MockRedisClient` in the message, not `NoneType`.
4. **Fixture-raised refusals report as `ERROR`, not `FAILED`** (carried from A3). Non-pass,
   non-skip, exit non-zero.
5. **`tests/real_redis_gate.py` still has no test of its own.** Exercised only through its
   three consumers.
6. **Pre-existing CI failures** on other branches; the eventual run may be red for unrelated
   reasons, so acceptance must be read per-test.

---

## 8. Safety confirmation

| Check | Result |
|---|---|
| Files modified in this task | **none** |
| Commits / branches / pushes / PRs / CI triggers | **none** |
| HEAD, `origin/main`, ahead/behind | `074b772a`, `074b772a`, 0/0 — unchanged |
| tracked modified | 120 — unchanged |
| staged in `.git/index` | 22 — unchanged, sha256 identical |
| reflog head | unchanged |
| Concurrent workstreams | preserved; `redis_manager.py` and `conftest.py` read-only |
| Production Redis / DB / exchange | **not contacted** |
| Infrastructure installed | **none** |
| QUOTA-01 | **OPEN** |
| Launch scorecard / gate register | **untouched** |
| Live trading | **OFF** |

Files created, all new and all under `staging/`:
`PHASE11B_A4_REDIS_FIXTURE_SKIP_ANALYSIS.md`, `probe_p11b4_cache_fallback.py`,
`p11b4_cache.txt`, `p11b4_manifest.txt`.

---

## 9. Smallest safe next action

**Requesting authorization for one edit:**

> Change `pytest.skip(` to `pytest.fail(` at
> `tests/test_webhook_duplicate_delivery_real_redis.py:188`, extending the message to record
> that the shared gate had already PINGed the endpoint successfully — so the reader can tell a
> manager fault from a missing server.

One file, one call site, test-only, already inside the D5′ allowlist. I will then re-run the
three gate states and the focused regression, and re-verify the manifest.

**After that**, the separate authorization already pending from A3 remains the gating step:
create `ci/d5-prime-real-redis`, commit the five-file manifest via the private-index
procedure, push, and open a PR so `01-pr-check.yml` runs. Only that run can move the
reachable-Redis path from UNVERIFIED.

QUOTA-01 stays OPEN. D9 atomic lock release, D2 database backstop and D3 production
population analysis are not in this workstream.
