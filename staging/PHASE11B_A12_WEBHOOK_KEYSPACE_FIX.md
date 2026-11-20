# Phase 11B-A12 — D5′ Webhook Redis Keyspace Alignment (F2 + F3)

**Date:** 2026-10-10
**Scope:** test-harness only. One file changed: `tests/test_webhook_duplicate_delivery_real_redis.py`.
No application code, no workflow, no `tests/conftest.py`, no `redis_manager.py`, no
`subscription_dependencies.py`. No Redis installed or started. **No remote action of any kind.**

---

## VERDICT

| Item | Status |
|---|---|
| F2 — sync client derives the application's effective cache URL | **IMPLEMENTED** |
| F3 — fixture-time bidirectional keyspace proof | **IMPLEMENTED** |
| Keyspace assertion exercised against the exact CI condition | **YES** — detected, with the sentinels cleaned up |
| Opt-out behaviour unchanged | **VERIFIED** — 6 skipped, exit 0 |
| Configured-but-unreachable fails closed | **VERIFIED** — 6 errors, exit 1, 0 skips |
| Mock cache still refused | **VERIFIED** — 1 passed / 5 errors / 0 skips, exit 1, both fallback branches |
| No regression across the three D5′ files | **VERIFIED** — 26 passed, 9 skipped, 0 failed (identical to the pre-fix baseline) |
| Six webhook tests passing against real Redis | **NOT VERIFIED** — no reachable Redis on this machine |
| **Real-Redis acceptance** | **UNVERIFIED** |
| Files changed outside the authorization | **none** |
| Commits / pushes / PR actions / CI triggers | **none** |
| QUOTA-01 | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

F2 + F3 are **locally verified** in the sense the acceptance criteria define: the focused tests
pass and the keyspace assertion is exercised. **Real-Redis acceptance remains UNVERIFIED** and is
not claimed.

---

## 1. Phase 0 — baseline, re-measured

| Measurement | Value | Same at end of task? |
|---|---|---|
| Checked-out branch | `main` | yes |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | yes |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | yes |
| `ci/d5-prime-real-redis` | `49e70da23c2858ffc9a6f34d5db4521401477c82` | yes, unmoved |
| Shared `.git/index` SHA-256 | `6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613` | **identical** |
| Staged entries in shared index | 22 | **22** |
| PR #7 | `OPEN`, `mergedAt: null`, head `49e70da2…`, updated 2026-10-10T10:13:20Z | unchanged |
| Unstaged modified tracked files (`git diff --name-only`) | 100 | — |

The only `gh` command issued in this task was `gh pr view 7 --json …`, which is read-only.

### Ownership

| File | Status | Disposition |
|---|---|---|
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `??` | **mine — the only file edited** |
| `tests/real_redis_gate.py` | `??` | mine; **read only**, imported from, not changed |
| `tests/conftest.py` | ` M` | other workstream — read only |
| `backend_app/core/cache/redis_manager.py` | ` M` | other workstream — read only |
| `backend_app/core/subscription_dependencies.py` | ` M` | other workstream — read only |
| `.github/workflows/01-pr-check.yml` | ` M` | other workstream (hunk 2) — read only |
| `pytest.ini` | ` M` | other workstream — read only |
| `backend_app/backend/redis_manager.py` | clean (= `main`) | read only; the source of the derivation |

### Trace performed before editing

`tests/real_redis_gate.py` resolves the opt-in from `AERORA_TEST_REDIS_URL` alone
(`OPT_IN_VAR`, line 61; `redis_url()`, line 74), classifies once at import into
skip / fail / run, and refuses a non-loopback host without `AERORA_TEST_REDIS_ALLOW_REMOTE=1`.
`tests/conftest.py:13` sets `os.environ["REDIS_URL"] = ""` unconditionally at import.
`backend_app/backend/redis_manager.py` captures `REDIS_URL` into a **module-level constant at
line 43**, pins `REDIS_DB_CACHE = 0` at line 37, and `get_redis_url(db)` **overrides** any
database the URL named by appending `?db=` (line 69) or by rebuilding the URL from
`REDIS_HOST`/`REDIS_PORT` (line 86). `initialize()` calls `get_redis_url(REDIS_DB_CACHE)` at
line 141. `backend_app/core/cache/__init__.py` re-exports the **singleton instance**
`redis_manager`, whose `get`/`set`/`delete` proxies swallow every exception and return
`None`/`False`/`0`.

Which tests need the two clients to share keys — determined by reading all six, not assumed:

| Test | Needs shared keyspace | CI (run 38044138718) |
|---|---|---|
| `test_the_credential_gate_refuses_test_keys` | no — does not use `redis_client` at all | PASSED |
| `test_a_redelivered_webhook_is_ignored` | no — handler ↔ handler | PASSED |
| `test_the_lock_is_recorded_in_redis_under_the_documented_key` | **yes** — app writes, test reads | **FAILED** |
| `test_a_different_event_id_is_not_deduplicated` | no — negative, handler ↔ handler | PASSED |
| `test_an_in_flight_delivery_blocks_a_concurrent_one` | **yes** — test writes, app reads | **FAILED** |
| `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | no — nothing written anywhere | PASSED |

---

## 2. Root cause, measured rather than inferred

`staging/p11b12_url_derivation.py` resolves `get_redis_url(REDIS_DB_CACHE)` in a fresh
subprocess per environment shape, so the import-time constant capture happens for real, and
compares the result with the opt-in URL through redis-py's own `parse_url`:

```
A. CI shape, REDIS_URL blanked by tests/conftest.py (the real test process)
  REDIS_URL (env)             : ''
  derived app cache URL       : 'redis://localhost:6379/0'      -> db 0   host=localhost
  opt-in AERORA_TEST_REDIS_URL: 'redis://localhost:6379/15'     -> db 15
  KEYSPACE: DISAGREE  (app db=0, opt-in db=15)

B. CI shape, REDIS_URL still visible at import
  derived app cache URL       : 'redis://localhost:6379?db=0'   -> db 0
  KEYSPACE: DISAGREE  (app db=0, opt-in db=15)

C. REDIS_URL explicitly names /15 -- the override still wins
  derived app cache URL       : 'redis://localhost:6379/15?db=0' -> db 0
  KEYSPACE: DISAGREE  (app db=0, opt-in db=15)

D. non-loopback REDIS_HOST (what the loopback guard refuses)
  derived app cache URL       : 'redis://redis.internal.example:6379/0' -> db 0  host=redis.internal.example
```

Three things this settles:

- Every branch lands on **DB 0**, so the split was unconditional, not a CI misconfiguration.
- Case C shows the override beats an explicitly configured database — `/15` cannot be reached
  from the application side by any environment setting.
- Case A is what the test process actually sees, and it is the measured defect: **app db 0,
  test client db 15.**

Line 43 being a module constant also rules out the obvious alternative fix: the
`monkeypatch.setenv("REDIS_URL", …)` in `billing_env` and the `os.environ` assignment in
`real_redis_cache` cannot retroactively change it.

---

## 3. Exact diff

One file, **+240 / −10**. Full diff at `staging/p11b12_diff.txt`; executable lines only at
`staging/p11b12_diff_code.txt`. Comment and docstring lines are elided below with `…`.

```diff
--- a/tests/test_webhook_duplicate_delivery_real_redis.py
+++ b/tests/test_webhook_duplicate_delivery_real_redis.py
@@ import json
+from urllib.parse import urlsplit

@@ WEBHOOK_SECRET = "test-webhook-secret-not-a-real-credential"
-from tests.real_redis_gate import (real_redis_required, redis_url as _gate_redis_url,
+from tests.real_redis_gate import (ALLOW_REMOTE_VAR, _LOOPBACK_HOSTS,
+                                   real_redis_required, redis_url as _gate_redis_url,
                                    require_real_redis)

+def _app_cache_url() -> str:
+    """…F2 rationale, the CI evidence, and the stated trade-off…"""
+    from backend_app.backend.redis_manager import REDIS_DB_CACHE, get_redis_url
+
+    url = get_redis_url(REDIS_DB_CACHE)
+
+    host = (urlsplit(url).hostname or "").lower()
+    if host not in _LOOPBACK_HOSTS and (os.environ.get(ALLOW_REMOTE_VAR) or "").strip() != "1":
+        pytest.fail(
+            f"the application's effective cache URL (REDIS_URL / REDIS_HOST / REDIS_PORT, "
+            f"database {REDIS_DB_CACHE}) names a non-loopback host and "
+            f"{ALLOW_REMOTE_VAR}=1 was not set. …(The host is deliberately not echoed.)"
+        )
+    return url
+
+_PROBE_PREFIX = "aerora:test:keyspace-probe"
+_PROBE_TTL_SECONDS = 30
+
+async def _prove_one_keyspace(manager, client) -> None:
+    """…F3 rationale: bidirectional, per-test, failed-write kept distinct from mismatch…"""
+    import redis as _redis
+    from backend_app.backend.redis_manager import REDIS_DB_CACHE
+
+    nonce = uuid.uuid4().hex
+    key_app_wrote = f"{_PROBE_PREFIX}:app-wrote:{nonce}"
+    key_test_wrote = f"{_PROBE_PREFIX}:test-wrote:{nonce}"
+    token = f"keyspace-probe-{nonce}"
+
+    try:
+        wrote = await manager.set(key_app_wrote, token, ex=_PROBE_TTL_SECONDS)
+        if not wrote:
+            pytest.fail("…REFUSED a write… This is not a keyspace mismatch…")
+
+        seen_by_test = client.get(key_app_wrote)
+        if seen_by_test != token:
+            pytest.fail("…KEYSPACE SPLIT… REDIS_DB_CACHE={REDIS_DB_CACHE}…")
+
+        client.set(key_test_wrote, token, ex=_PROBE_TTL_SECONDS)
+        seen_by_app = await manager.get(key_test_wrote)
+        if seen_by_app != token:
+            pytest.fail("…KEYSPACE SPLIT (reverse direction)…")
+    except (_redis.exceptions.RedisError, OSError) as exc:
+        pytest.fail("…could not use the application's effective cache URL (database …)…")
+    finally:
+        try:
+            await manager.delete(key_app_wrote, key_test_wrote)
+        except Exception:  # noqa: BLE001
+            pass
+        try:
+            client.delete(key_app_wrote, key_test_wrote)
+        except Exception:  # noqa: BLE001
+            pass

@@ async def real_redis_cache(billing_env):
-    If the manager still hands back the mock, the test skips rather than passing: …
+    If the manager still hands back the mock, the test FAILS rather than passing or
+    skipping (Phase 11B-A5, see the guard below): …
+
+    Note that this fixture establishes only that the manager returned a REAL cache. It
+    does not establish that the real cache is the one this file's own client is looking
+    at … `redis_client` / `_prove_one_keyspace` now answer it explicitly.

@@ async def test_the_credential_gate_refuses_test_keys(billing_env):
-@pytest.fixture
-def redis_client():
+@pytest_asyncio.fixture
+async def redis_client(real_redis_cache):
+    """…now on the SAME keyspace as the application cache, proven…"""
     import redis as _redis

-    client = _redis.Redis.from_url(REDIS_URL, decode_responses=True)
+    client = _redis.Redis.from_url(_app_cache_url(), decode_responses=True)
     created: list[str] = []
-    yield client, created
-    for key in created:
-        try:
-            client.delete(key)
-        except Exception:  # noqa: BLE001
-            pass
+    try:
+        await _prove_one_keyspace(real_redis_cache, client)
+        yield client, created
+    finally:
+        for key in created:
+            try:
+                client.delete(key)
+            except Exception:  # noqa: BLE001
+                pass
+        try:
+            client.close()
+        except Exception:  # noqa: BLE001
+            pass
```

### Design notes, and what was deliberately not done

- **Derived, not hard-coded.** `get_redis_url` is a pure function of its module's constants, so
  calling it returns the exact string `initialize()` handed `aioredis.from_url` — by
  construction. A literal `/0` would agree today and drift silently the moment
  `REDIS_DB_CACHE`, the override, or the host/port fallback changes.
- **Lazy import, on purpose.** `_app_cache_url()` imports inside the call so it runs *after*
  `real_redis_cache` has resolved the manager. By then the backend module is imported and its
  constants are frozen to what the application itself observed, so the function cannot report a
  URL the application never used. This is why `redis_client` now depends on `real_redis_cache`.
- **The gate keeps the opt-in.** `REDIS_URL = _gate_redis_url()`, `billing_env`,
  `real_redis_cache` and `require_real_redis()` are untouched. `AERORA_TEST_REDIS_URL` still
  decides opt-in and liveness. Only where *this file's own client* looks has moved.
- **`_LOOPBACK_HOSTS` and `ALLOW_REMOTE_VAR` are imported from the gate**, not copied, so the
  production-safety rule has one definition. The derived URL gets the same refusal the opt-in
  URL gets, because this client writes and deletes keys and must not be able to reach a shared
  instance. The refusal does not echo the host (CI logs are world-readable).
- **No skips added.** Every new failure path is `pytest.fail`. The mock is never accepted.
- **No global Redis semantics changed.** Nothing sets, exports or mutates `REDIS_URL`,
  `REDIS_HOST` or `REDIS_PORT`. No application file was opened for writing.
- **One docstring correction included:** `real_redis_cache` still said the fixture "skips" when
  the manager returns a mock, directly above the `pytest.fail` that A5 put there. Comment-only,
  zero behaviour change, in my own file, and disclosed rather than slipped in.

### Trade-off, stated plainly

The test client no longer sits on the dedicated database index named by
`AERORA_TEST_REDIS_URL`; it sits on the application's cache database. That is **less keyspace
isolation than `/15` promised** — but `/15` was never where the application wrote, so the
isolation bought nothing and cost two false failures. Every key this file creates is
uuid-suffixed and registered for teardown, and the probe sentinels additionally carry a 30 s
TTL. Restoring genuine isolation requires `get_redis_url` to honour an explicitly configured
database, which is an application change, owned elsewhere, and out of scope here (it is F4 in
`staging/PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md`).

---

## 4. Keyspace assertion design

Four Redis commands per test, both directions, because the two CI failures were in **opposite**
directions and a one-way probe would have caught only one:

| Direction | Mirrors | Probe step |
|---|---|---|
| app writes → test reads | `test_the_lock_is_recorded_in_redis_under_the_documented_key` | `await manager.set(…)` then `client.get(…)` |
| test writes → app reads | `test_an_in_flight_delivery_blocks_a_concurrent_one` | `client.set(…)` then `await manager.get(…)` |

- **Sentinels:** `aerora:test:keyspace-probe:{app-wrote|test-wrote}:{uuid4().hex}`, value
  `keyspace-probe-{nonce}`. Prefixed and uuid-suffixed so they cannot collide with an
  application key; 30 s TTL so an interrupted run leaves nothing behind.
- **Cleanup:** a `finally` deletes both keys through **both** clients, so they go whichever side
  can still reach them, and regardless of which assertion fired. `pytest.fail` raises a
  `BaseException` subclass, so it passes straight through the `except Exception` guards there.
- **Per test, not per session,** because `_redis_manager`, `_dev_mode_bypass` and `_test_mode`
  are injection seams on a session-wide singleton (`_REDIS_SINGLETON_SEAMS` in
  `tests/conftest.py`). Agreement proven at session start is not agreement now.
- **A failed write is reported as a failed write.** The manager's proxies return `False`/`None`
  on any exception, so a cache fault and an empty keyspace look identical from the return value.
  The write result is checked first, so the diagnosis does not blame the wrong thing.
- **An unusable application endpoint fails closed.** The gate PINGs the *opt-in* endpoint; the
  derived application endpoint may differ, so a `RedisError`/`OSError` becomes a `pytest.fail`
  that says exactly that, rather than a confusing error inside a test body.

---

## 5. Commands and complete results

### 5a. Opt-out — `AERORA_TEST_REDIS_URL` unset

```
python -m pytest tests/test_webhook_duplicate_delivery_real_redis.py -p no:randomly --no-header -q
  → 6 skipped in 6.36s          exit 0
```

Unchanged. The default lane is untouched.

### 5b. All three D5′ files, opt-out, per-test

```
python -m pytest tests/test_webhook_duplicate_delivery_real_redis.py \
                 tests/test_quota01_exchange_slot_is_atomic.py \
                 tests/test_quota01_metered_reservation_is_hard.py \
                 -p no:randomly --no-header -v --tb=no -rA
  → 26 passed, 9 skipped, 0 failed, 0 error, 0 xfail, 0 xpass    exit 0
```

**Identical to the pre-fix local baseline** recorded in A5/A6 (26 passed / 9 skipped / 0 failed).
No regression. The nine skips, enumerated individually:

| # | Skipped test | Reason |
|---|---|---|
| 1-6 | all six in `test_webhook_duplicate_delivery_real_redis.py` | opt-in unset — the one legitimate skip |
| 7 | `test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` | same |
| 8 | `test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` | same |
| 9 | `test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` | same |

Full output: `staging/p11b12_optout_verbose.txt`.

### 5c. Configured but unreachable — `AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15`

```
  → 6 errors in 15.38s          exit 1          0 skipped
E  Failed: AERORA_TEST_REDIS_URL is set but the server did not answer PING (TimeoutError).
   A configured endpoint that cannot be reached is a BROKEN TEST SERVICE, not an absent one,
   so this fails rather than skipping … (The endpoint is deliberately not echoed.)
```

Fails closed, unchanged by this task.

### 5d. Mock / `None` cache still refused (the A5 guard)

```
P11B5_FALLBACK=mock  -p staging.p11b5_force_gate  → 1 passed, 5 errors, 0 skipped   exit 1
P11B5_FALLBACK=none  -p staging.p11b5_force_gate  → 1 passed, 5 errors, 0 skipped   exit 1
```

Matches the A5 baseline exactly. The single pass is
`test_the_credential_gate_refuses_test_keys`, which needs no cache. **The in-memory dict is
still not accepted as real Redis.**

### 5e. The probe is on the path every cache-dependent test takes

`staging/p11b5_force_gate.py` forces the manager onto `MockRedisClient`/`None`, which trips the
A5 guard *before* the probe. So `staging/p11b12_force_realish_cache.py` hands the manager a stub
that is neither — the A5 guard then correctly passes, and execution reaches the probe:

```
python -m pytest tests/test_webhook_duplicate_delivery_real_redis.py \
                 -p staging.p11b12_force_realish_cache -p no:randomly --no-header -q

tests/test_webhook_duplicate_delivery_real_redis.py:245: in redis_client
    await _prove_one_keyspace(real_redis_cache, client)
tests/test_webhook_duplicate_delivery_real_redis.py:278: in _prove_one_keyspace
E   Failed: the shared gate reached the endpoint named by its own opt-in variable, but this
    file's client could not use the application's effective cache URL (database 0):
    ConnectionError. …(Neither is echoed.)

  → 1 passed, 5 errors, 0 skipped    exit 1
```

This is direct evidence of four things at once: `_app_cache_url()` resolved and reported
**database 0**; the A5 guard did not fire on a non-mock cache; `_prove_one_keyspace` runs for
all five cache-dependent tests; and an unreachable application endpoint **errors rather than
skipping**. Output: `staging/p11b12_probe_reached.txt`.

### 5f. The probe's detection logic, all five conditions

`staging/p11b12_probe_harness.py` drives `_prove_one_keyspace` with in-process doubles — no
server, no network — to show it detects each condition and always cleans up:

```
OK  1. same keyspace -> probe accepts                                    passed    sentinels left: none
OK  2. split keyspace (the CI defect) -> probe FAILS, direction 1        failed    "KEYSPACE SPLIT"
OK  3. reverse-only split -> probe FAILS, direction 2                    failed    "reverse direction"
OK  4. manager refuses the write -> reported as a refusal, not a mismatch failed   "REFUSED a write"
OK  5. sync client cannot reach the app endpoint -> fails closed          failed   "could not use the application's effective cache URL"
==============================================================================
5/5 scenarios behaved as specified
```

Scenario 2 is the precise CI condition. Scenario 4 is the reason the write result is checked
before the read: the manager's fail-soft returns would otherwise have this report a mismatch
when nothing was ever written. No sentinels survived any scenario, including the four that
failed. Output: `staging/p11b12_probe_out.txt`.

### 5g. The loopback guard on the derived URL

```
1. non-loopback host, ALLOW_REMOTE unset  → FAILED-CLOSED: "…names a non-loopback host and
                                             AERORA_TEST_REDIS_ALLOW_REMOTE=1 was not set…"
2. non-loopback host, ALLOW_REMOTE=1      → RETURNED: redis://redis.internal.example:6379/0
3. default localhost                      → RETURNED: redis://localhost:6379/0
```

The refusal names the variable, never the host.

---

## 6. What was and was not verified against real Redis

**Not verified — no Redis is reachable here.** TCP 6379 on `127.0.0.1` is closed
(`TimeoutError`), `AERORA_TEST_REDIS_URL` is unset, `fakeredis` / `redislite` / `pytest-redis`
are all absent, and installing or starting infrastructure is outside this task's authorization.

| Claim | Verified? | How |
|---|---|---|
| The derived URL is the one the application connects with | **yes** | `get_redis_url(REDIS_DB_CACHE)` resolved per environment shape in a fresh subprocess; it is a pure function of the same constants `initialize()` uses |
| App and test client were on different databases before the fix | **yes** | measured: app db 0, opt-in db 15, in all four shapes |
| The probe detects a split, both directions | **yes** | in-process doubles, 5/5 scenarios |
| The probe is reached by every cache-dependent test | **yes** | real fixture path, non-mock stub cache |
| The probe cleans up its sentinels | **yes** | asserted in all five scenarios, including failures |
| Opt-out, unreachable, and mock paths behave as before | **yes** | 5a, 5c, 5d |
| **The six webhook tests pass against a real Redis** | **NO** | requires a reachable server |
| **The handler's dedup logic is correct against real Redis** | **NO** | that is what the six tests would establish |
| **Real-Redis integration acceptance** | **NO — UNVERIFIED** | unchanged from A6/A9 |

What the fix is *expected* to produce in CI, stated as a prediction and not a result: the test
client resolves to `redis://localhost:6379/0`, the same database the application cache uses, so
the two tests that failed on an empty keyspace should now exercise the handler's real lock. If
they still fail, the probe will have passed first — which means the failure is a genuine finding
about the application's dedup behaviour, not a harness artifact. That distinction is the point
of F3.

---

## 7. Final Git status and change proof

```
branch                 : main
HEAD                   : 074b772ab16cece62cbfca0f2b78497be2ae56d5
origin/main            : 074b772ab16cece62cbfca0f2b78497be2ae56d5
ci/d5-prime-real-redis : 49e70da2  (origin/ci/d5-prime-real-redis, unmoved)
shared .git/index      : 6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613
staged entries         : 22
```

The shared index hash is **byte-identical** to the Phase 0 measurement and still holds the other
workstreams' 22 staged files.

Blob comparison of the D5′ files against commit `49e70da2`:

```
SAME     tests/real_redis_gate.py                           d40a1bb7…
SAME     tests/test_quota01_exchange_slot_is_atomic.py       2d7ccbbb…
SAME     tests/test_quota01_metered_reservation_is_hard.py   f48103ac…
CHANGED  tests/test_webhook_duplicate_delivery_real_redis.py worktree 3ffbccde… | commit f74a0a17…
CHANGED  .github/workflows/01-pr-check.yml                   worktree ee11bd3b… | commit 79bcdaf2…
```

The workflow entry is **not** a change made here. Its delta against `origin/main` is unchanged
from the A6 record — `170 insertions, 0 deletions`, hunks `@@ -183,10 +183,37 @@` (hunk 1, in the
commit) and `@@ -465,6 +492,149 @@` (hunk 2, the other workstream's `provisioning-and-isolation`
job, deliberately excluded from the commit). It differs from the commit because the commit holds
hunk 1 only. The only write operations issued in this task were four `str_replace` calls against
`tests/test_webhook_duplicate_delivery_real_redis.py` and new files under `staging/`.

Artifacts created (all under `staging/`, none committed, none staged):
`p11b12_probe_harness.py`, `p11b12_url_derivation.py`, `p11b12_force_realish_cache.py`,
`p11b12_loopback_child.py`, `p11b12_filter_diff.py`, `p11b12_diff.txt`,
`p11b12_diff_code.txt`, `p11b12_probe_out.txt`, `p11b12_url_out.txt`,
`p11b12_probe_reached.txt`, `p11b12_optout_verbose.txt`, `p11b12_proxies.txt`.
The temporary `p11b12_before.py` snapshot was deleted after the diff was captured, so no
duplicate copy of a test module is left in `staging/`.

---

## 8. No remote action was taken

**Explicitly: nothing in this task touched the remote or the shared index.**

| Action | Performed? |
|---|---|
| `git add` / staging of any kind | **no** |
| `git commit` (incl. plumbing `commit-tree` / `update-ref`) | **no** |
| `git push` | **no** |
| PR opened, modified, closed, or merged | **no** — PR #7 left `OPEN` |
| CI run triggered or re-run | **no** |
| `git reset` / `stash` / `clean` / `revert` / `checkout -b` | **no** |
| Redis or any other infrastructure installed or started | **no** |
| Production Redis or database accessed | **no** |
| Real orders / live trading | **no — OFF** |
| `gh` commands issued | one, read-only: `gh pr view 7 --json …` |

---

## 9. Standing items

- **QUOTA-01: OPEN.** Untouched here. The atomic slot-guard implementation
  (`exchange_connection_slot_guard`, `_slot_lock_key`) is absent from `main` and present only in
  another workstream's uncommitted edit to `backend_app/core/subscription_dependencies.py`. It
  was **not** copied into this task. Until its owner ships it, the two quota real-Redis tests
  cannot pass in CI by construction, and `store_keys` still calls the non-atomic
  `check_exchange_connection_slot` — QUOTA-01's core read-then-write race, confirmed live on
  `main` in A9.
- **Live trading: OFF / NOT CERTIFIED.**
- **Real-Redis acceptance: UNVERIFIED.** It becomes verifiable only when the three D5′ files
  execute against a reachable real Redis.
- Separate tickets from A9, none of them addressed here: webhook idempotency failing open on a
  cache read fault and closed on a write fault (F5); async connections outliving their event loop;
  `get_redis_url` discarding an explicitly configured database index and capturing `REDIS_URL` at
  import (F4).

### Smallest next action, needing its own authorization

Run the three D5′ files against a reachable real Redis — in CI on a new commit of this file, or
locally if starting a container is authorized. That is the only thing that can move real-Redis
acceptance off UNVERIFIED. No commit, push, or PR action is included in this task's scope.
