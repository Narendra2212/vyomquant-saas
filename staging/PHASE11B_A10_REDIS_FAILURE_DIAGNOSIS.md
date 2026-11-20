# Phase 11B-A10 — Isolating the D5′ Real-Redis Failures

**Status: diagnosis advanced, root cause NOT yet proven — blocked on capturing the four
tracebacks, which requires an authorized focused CI re-run.** No test, assertion, skip, or
shared fixture was changed. PR #6 remains open and unchanged.

---

## Phase 0 — Read-only baseline

| Item | Value |
|---|---|
| Repo HEAD / branch | `074b772a` / `main` (== `origin/main`) |
| PR #6 | head `b43e990dc31766b7456dad8abd8dc5705c143814`, base `main`, state OPEN, `mergedAt: null` |
| Commit diagnosed | **`b43e990d`** (unchanged since 11B-A6) |
| Failed CI run | `38043513862`; `Unit & Integration Tests` job `114188821574` = failure |
| Shared staged index | 22 files (unrelated tenant-isolation/RLS/terraform workstream), untouched |
| Ownership | All commits + the workflow authored by `Narendra2212`; the four D5′ test files are owned by this workstream (introduced in `b43e990d`) |

Base `conftest.py` (the version in the D5′ tree) **already contains** the shared-singleton leak
guard `_restore_shared_redis_singleton` (autouse) but does **NOT** contain the rate-limiter leak
guard. The rate-limiter guard (`_reset_bound_rate_limiters` / `_rate_limit_counts_are_per_test`)
exists ONLY in the uncommitted working-tree `conftest.py`, which D5′ deliberately excluded.

## Phase 1 — Reproduction attempt on the same commit

Attempted to recover per-test tracebacks from CI run `38043513862` (the authoritative
real-Redis environment). Findings:
- The `unit-tests` job log is 13,302 lines; the pytest `FAILURES` banner is at the very end
  (line 13221) and contains the **full traceback for only one test**
  (`test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`). The step log is
  **truncated** before the D5′ tracebacks.
- Each failing D5′ test appears in the log **only on its `FAILED [NN%]` progress line** — no
  traceback block survives for any of the four.
- The run produced **no artifacts** (`actions/.../artifacts` → `total_count: 0`); the workflow
  emits no JUnit XML.

So the four D5′ tracebacks are **not recoverable from the existing run**. Capturing them
requires a focused re-run (`--tb=long`, ideally JUnit XML), which needs a push to the PR branch
or a diagnostic workflow job — gated on authorization this phase does not carry.

## Phase 1 — Precise per-test outcomes (recovered from CI progress lines)

**`tests/test_quota01_metered_reservation_is_hard.py` — 7/7 PASSED** (incl. the required
`test_concurrent_reservations_are_atomic_against_real_redis`). This file calls
`SubscriptionEngine` directly.

**`tests/test_webhook_duplicate_delivery_real_redis.py` — 4 PASSED / 2 FAILED:**
- PASSED: `test_the_credential_gate_refuses_test_keys`, `test_a_redelivered_webhook_is_ignored`,
  `test_a_different_event_id_is_not_deduplicated`, `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind`
- FAILED: `test_the_lock_is_recorded_in_redis_under_the_documented_key`,
  `test_an_in_flight_delivery_blocks_a_concurrent_one`

**`tests/test_quota01_exchange_slot_is_atomic.py` — 1 PASSED / 21 FAILED:**
- The only PASS: `test_retrying_a_refused_request_does_not_eventually_succeed`
- Everything else FAILED, including purely in-process, non-concurrent tests
  (`test_the_lock_is_released_when_the_guarded_write_raises`,
  `test_an_unreachable_lock_store_refuses_rather_than_admits`,
  `test_a_bare_delete_on_release_fails_this_suite`, the two `_against_real_redis` ones, etc.).

## Phase 2 — Hypotheses, proved or rejected

### REJECTED: "the whole slot-lock file fails because of rate-limiter / TestClient contamination"
This was the Phase 11B-A6 framing. Reading the source **refutes it for the slot-lock file**:
`test_quota01_exchange_slot_is_atomic.py` uses an in-process `_LockStore` and `_Account`,
wired via `monkeypatch.setattr(sd, "redis_manager", locks)` in the `slot_env` fixture. These
tests never build a `TestClient` and never touch the rate limiter, so the excluded
rate-limiter leak guard cannot explain their failure. The earlier "pre-existing contamination"
label was too coarse for this file.

### REJECTED: "real Redis is unhealthy / the gate failed to run"
The Redis service was healthy (container log: `Redis version=7.4.11 … Ready to accept
connections`, standalone :6379). The gate reached `run` (the nine tests executed, none
skipped), and the metered real-Redis test plus four webhook tests PASSED against it — real
Redis works.

### REJECTED: "all four failures share one cause"
They do not. The webhook file fails only 2 of 6 (specific behaviors), while the slot-lock file
fails 21 of 22 (near-total). These are two different problems.

### OPEN / MOST LIKELY (slot-lock, 21/22): a collection- or fixture-level failure specific to CI
A file where nearly every test fails but one — spanning concurrent AND sequential, HTTP-free,
self-injected-cache tests — is the signature of a shared fixture or setup failing, not 21
independent logic defects. Candidates still consistent with the evidence (cannot be
distinguished without the traceback):
- The `slot_env` fixture or an interaction between it and the autouse
  `_restore_shared_redis_singleton` guard under CI's env (`DEV_MODE=true`, `REDIS_URL=""`,
  `AERORA_TEST_REDIS_URL` set), e.g. `sd.redis_manager` not being the object the production
  guard actually reads at call time in the CI import graph.
- An import-time effect of `tests/real_redis_gate` (it runs a PING at import) altering module
  state the slot file relies on.
- A genuine `_acquire_slot_lock` / `_release_slot_lock` behavior difference the in-process
  `_LockStore` happens to tolerate locally but not in the CI ordering.
The single PASS (`test_retrying_a_refused_request_does_not_eventually_succeed`, the only
sequential-20-attempts test with no `delay`/`gather` and no exception path) is a clue but not
conclusive.

### OPEN (webhook, 2/6): a real handler/Redis behavior on the two failing idempotency assertions
`test_the_lock_is_recorded_in_redis_under_the_documented_key` asserts the key exists with a
value in `("processing","completed","1")` AND `ttl > 0`; `test_an_in_flight_delivery_blocks_a_concurrent_one`
asserts a delivery arriving while a `processing` marker is set returns `status == "duplicate"`.
That the other four webhook tests (incl. real redelivery dedup) PASS suggests these two probe a
narrower property — possibly the lock value/TTL shape or the `processing`-phase refusal — that
may be a **genuine handler finding** rather than contamination. Unprovable without the traceback.

## Phase 3 — Minimum justified correction

**Not taken.** A correction is only justified "after the failure is reproduced and diagnosed",
and the diagnosis is blocked on the tracebacks. Guessing a code change now would risk editing
correct code or another workstream's fixture. No assertion weakened, no skip/xfail added.

## Phase 4 — Acceptance

NOT met. 4 of 9 required real-Redis tests fail; cause unproven. PR #6 kept open.

| File | Pass | Fail | Skip |
|---|---|---|---|
| metered_reservation | 7 | 0 | 0 |
| exchange_slot | 1 | 21 | 0 |
| webhook | 4 | 2 | 0 |

Unrelated failures (reported separately, unchanged from baseline run `6c5660a1`):
`test_tenant_isolation_matrix` (Req 21.8), `test_no_credential_in_api_response_body`,
`test_strategy_status_publish`, `test_task_6_1_backtest_execute`, `test_validation_sweep`,
`test_task_33_2_rate_limit_keys`, `test_tenant_isolation_library_paper`,
`test_ml_deployment_guards`, `test_task_8_4_runtime_readiness_gate`. These are consistent with
the rate-limiter/ordering leak the excluded working-tree `conftest.py` addresses and are NOT
part of this workstream.

## The single smallest next fix

**Capture the four tracebacks via one focused CI re-run, then fix the one demonstrated cause.**
Concretely, the smallest authorized action is to run — in the real-Redis CI environment, as its
own invocation so output is not truncated and nothing contaminates it:

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       tests/test_webhook_duplicate_delivery_real_redis.py \
       -p no:randomly -v --tb=long --junitxml=staging/d5_redis.xml
  env: REDIS_URL=redis://localhost:6379, AERORA_TEST_REDIS_URL=redis://localhost:6379/15,
       ENV=test, AERORA_MODE=safe, PYTHONPATH=.
```

Running the three files **alone** also tests the contamination hypothesis directly: if the
21/22 slot-lock failures disappear when the file runs in isolation, the cause is full-suite
state leakage (point to the rate-limiter guard / ordering) rather than a slot-lock defect; if
they persist in isolation against real Redis, the cause is in the test's own real-Redis wiring
or the guard — a genuine finding to fix at its source.

**This requires authorization not held in this phase:** either (a) push a diagnostic step/commit
to the `ci/d5-real-redis-quota-evidence` branch, or (b) add an isolated, non-blocking diagnostic
job to `.github/workflows/01-pr-check.yml` (owner = `Narendra2212`; a minimal additive job that
leaves every existing job untouched). Prepare the minimal diff and obtain approval before either.

## Preservation

- `main`/`origin/main` at `074b772a`; PR #6 head still `b43e990d`, OPEN, not merged.
- Shared staged index still 22 files; no concurrent work touched; no new push; workflow unchanged.
- Temporary diagnostic scripts/logs deleted.
- QUOTA-01 remains OPEN. Live trading OFF / NOT CERTIFIED.
