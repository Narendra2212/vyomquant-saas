# Phase 11B-A — Next Actions

Baseline: HEAD `074b772a` (`main`), dirty tree (concurrent work preserved).
Status going in: real-Redis quota test SKIPPED locally; CI D5' fix already present in tree.

## What is already done (do not redo)
- CI `unit-tests` job exports `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` against a
  healthy `redis:7-alpine` service. Shared gate `tests/real_redis_gate.py` is in place and
  consumed by all three real-Redis files. No further workflow/test edits are needed.
- In-memory logic verified: 6 passed, 1 skipped.
- Gate state machine verified: unset→skip, bad-shape→fail, ci-value(port-closed)→fail,
  non-loopback-no-flag→fail. Only reachable-loopback→run.

## To CLOSE QUOTA-01 real-Redis on executed evidence (the one remaining step)
1. **Trigger the CI `unit-tests` job on a PR** and confirm in the logs:
   `tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis PASSED`
   (not SKIPPED). This is the first run that executes the test against the live service and is
   what converts the status from "CI-configured" to "integration-verified".
   - Watch for the gate FAILING instead of running — that would mean the `redis:7-alpine`
     service is unhealthy/unmapped, which the gate now surfaces as a loud FAIL (by design).

2. **(Optional, for a local integration run)** With explicit authorization only, start a real
   Redis and run the gated test directly — e.g. a container `docker run -p 6379:6379 redis:7-alpine`,
   then:
   ```
   $env:AERORA_TEST_REDIS_URL = "redis://localhost:6379/15"
   .venv\Scripts\python.exe -m pytest tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis -v
   ```
   `fakeredis` is NOT a valid substitute here (server-side INCR atomicity is the property under test).

## Still OPEN (carry forward)
- **order-rate**, **daily-trade**, **active-order** quota limits: no commercial gate exists.
  Decide whether these are launch-required; if so, design + wire them (metered via
  `reserve=True` like backtests/optimizations) and add real-Redis concurrency coverage.
- **Slot-lock release residual:** read-then-compare-then-delete is ownership-safe but not
  atomic. A portable atomic release needs a real Lua `eval` in the cache facade (currently an
  idempotency-only emulator). Tracked in `staging/PHASE10_WSC_COORDINATOR_REVIEW.md`.

## Guardrails for whoever continues
- Do not commit/push without instruction. Preserve all concurrent working-tree changes.
- Do not add a `DATABASE_URL`/`REDIS_URL` to the `unit-tests` job that would re-point the
  11k-test lane; the `AERORA_TEST_*` opt-in pattern exists precisely to avoid that.
- A SKIP on the real-Redis test is NOT a pass. Only an executed PASS against a reachable server
  closes QUOTA-01 real-Redis.
