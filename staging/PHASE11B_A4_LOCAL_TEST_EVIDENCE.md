# Phase 11B-A4 — Local Test Evidence

**Environment:** Windows / PowerShell. `AERORA_TEST_REDIS_URL` **unset**, `REDIS_URL` **unset**
(in-shell), port `127.0.0.1:6379` **not reachable** (`Test-NetConnection` → `False`). This is
the legitimate opt-out state, so every real-Redis test is EXPECTED to SKIP (not fail).

**Command:**
```
.venv\Scripts\python.exe -m pytest \
  tests/test_quota01_metered_reservation_is_hard.py \
  tests/test_quota01_exchange_slot_is_atomic.py \
  tests/test_webhook_duplicate_delivery_real_redis.py \
  -v --tb=short -p no:cacheprovider -r a
```

**Result:** `26 passed, 9 skipped, 8 warnings in 35.46s` (exit 0). Warnings are pre-existing
Pydantic-v1/gotrue deprecations, unrelated.

---

## 1. Real-Redis tests — SKIPPED (correct: opt-in unset, "a skip is NOT a pass")

These require a reachable Redis via `AERORA_TEST_REDIS_URL`; none ran against a mock, none
counted as passed. All reported the gate's explicit opt-out reason.

| Test (acceptance-critical ones in **bold**) | Local state |
|---|---|
| **`test_concurrent_reservations_are_atomic_against_real_redis`** | SKIPPED |
| **`test_the_slot_lock_is_mutually_exclusive_against_real_redis`** | SKIPPED |
| **`test_the_guard_admits_exactly_the_limit_against_real_redis`** | SKIPPED |
| `test_webhook_duplicate_delivery_real_redis.py::test_the_credential_gate_refuses_test_keys` | SKIPPED |
| `…::test_a_redelivered_webhook_is_ignored` | SKIPPED |
| `…::test_the_lock_is_recorded_in_redis_under_the_documented_key` | SKIPPED |
| `…::test_a_different_event_id_is_not_deduplicated` | SKIPPED |
| `…::test_an_in_flight_delivery_blocks_a_concurrent_one` | SKIPPED |
| `…::test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` | SKIPPED |

9 skips total = 3 quota real-Redis + 6 webhook. **All six webhook tests individually
accounted for.**

## 2. In-memory / mock logic tests — PASSED (26)

These exercise the quota and slot-lock LOGIC on the in-process substrate and do not need Redis.

- `test_quota01_metered_reservation_is_hard.py` (6): exactly-K-of-N concurrency, sequential
  fill+refuse, zero-allowance, unlimited plan, per-user isolation, full `enforce_quota` → 403.
- `test_quota01_exchange_slot_is_atomic.py` (20): 2-venue and 8-concurrent slot admission,
  same-venue rotation, per-account non-contention, cross-tenant isolation, retry-does-not-
  succeed, lock released on raise/refusal, fail-closed when store unreachable/raises,
  409-not-503 on contention, TTL guard, ownership-safe release (does NOT delete a successor's
  lock), release reports each ownership outcome, and the meta-guards that FAIL the suite if a
  guard stops locking / stops rechecking / does a bare delete.

## 3. Distinguishing real-Redis vs mock (explicit)

- **Real-Redis path:** the 9 SKIPPED tests. They connect via `redis.asyncio`/`redis` to
  `AERORA_TEST_REDIS_URL`; unreachable-but-configured would FAIL (not skip) — see §4.
- **Mock/in-memory path:** the 26 PASSED tests. These intentionally run on
  `MockRedisClient`/in-process state to prove logic, and are the "logic tests beside these"
  the gate reason refers to. They are NOT a substitute for the real-Redis integration proof.

## 4. Webhook silent-skip concern (scope item 4) — NO correction needed

The webhook file is already protected against the "configured-but-unreachable reads as a green
skip" failure mode:
- Module-level `pytestmark = [real_redis_required, pytest.mark.asyncio]` — `real_redis_required`
  skips ONLY when `AERORA_TEST_REDIS_URL` is unset.
- Autouse `_real_redis_is_usable` fixture calls `require_real_redis()`, which `pytest.fail`s
  (not skips) when the var IS set but PING fails or the host is non-loopback without
  `AERORA_TEST_REDIS_ALLOW_REMOTE=1`. It runs before the cache fixtures so a dead endpoint is
  reported with the gate's reason, not an opaque connection error.
- Secondary guard: `real_redis_cache` skips if the manager still resolves to `MockRedisClient`,
  but only AFTER the usability gate has passed — so a configured-but-dead Redis still FAILS.

This matches the gate's three-state contract (unset→skip, configured-broken→fail, reachable→run)
proven in Phase 11B-A. **No change proposed; no owner approval required for a change.**

## 5. Caveats

- This is NOT real-Redis integration evidence. Local verification remains BLOCKED_ON_INFRA (no
  reachable Redis; installing one is unauthorized; `fakeredis` is not acceptable). The skips are
  the correct local outcome and explicitly do not count as passes.
- The acceptance-criteria PASS for the three real-Redis tests can only be demonstrated by an
  authorized CI (or authorized local-Redis) run — see the CI acceptance checklist.
