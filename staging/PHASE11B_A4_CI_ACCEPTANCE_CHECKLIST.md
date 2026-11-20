# Phase 11B-A4 — CI Acceptance Checklist (for the authorized PR run)

Use this to judge the first `01-pr-check.yml` run on a commit that contains the D5′ changes
(see `PHASE11B_A4_D5_COMMIT_MANIFEST.md`). The run is NOT triggered yet — it requires explicit
authorization to commit + push + open the PR.

## Pre-run (confirm the tested revision)
- [ ] Run commit SHA is the D5′ commit (contains the 4 new test files + the 2 workflow hunks).
      Verify: `git show <SHA>:tests/real_redis_gate.py` exists AND
      `git show <SHA>:.github/workflows/01-pr-check.yml` contains `AERORA_TEST_REDIS_URL`.
- [ ] The tested revision does NOT contain the `provisioning-and-isolation` job unless that
      separate workstream was intentionally included (it should be absent for a clean D5′ PR).

## Redis service & gate
- [ ] `unit-tests` job's `redis:7-alpine` service reports healthy (health-check passed).
- [ ] The job env exports `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` AND retains
      `REDIS_URL: redis://localhost:6379`.
- [ ] The gate resolved to `run` (reachable loopback), NOT `skip` and NOT `fail`.

## Required tests — must EXECUTE and PASS (a skip does NOT satisfy these)
- [ ] `tests/test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` → **PASSED** (not SKIPPED)
- [ ] `tests/test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` → **PASSED** (not SKIPPED)
- [ ] `tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` → **PASSED** (not SKIPPED)

## Webhook tests — all six individually accounted for
Confirm each is PASSED (not SKIPPED) in the run log:
- [ ] `test_the_credential_gate_refuses_test_keys`
- [ ] `test_a_redelivered_webhook_is_ignored`
- [ ] `test_the_lock_is_recorded_in_redis_under_the_documented_key`
- [ ] `test_a_different_event_id_is_not_deduplicated`
- [ ] `test_an_in_flight_delivery_blocks_a_concurrent_one`
- [ ] `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind`

## Logic (regression) tests still green
- [ ] The 26 in-memory logic tests (6 metered + 20 slot) remain PASSED.

## Separate / unrelated outcomes (record, do not mask)
- [ ] Note the overall `unit-tests` job result. A known-unrelated failure may make it RED
      even if all D5′ tests pass:
      - `tests/property/test_tenant_isolation_matrix.py::test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`
        (seen RED in run 37346853502, Requirement 21.8). If the D5′ branch is off current
        `main` WITHOUT the tenant-isolation workstream, this test's current state should be
        re-observed — it must be dispositioned separately and NOT hidden by the D5′ result.
- [ ] The previously-reported API04 rate-limit test: confirm whether it is present/green in
      this run; report separately.

## Decision rule
- **PASS (real-Redis evidence closed):** all three required real-Redis tests EXECUTED and
  PASSED, all six webhook tests accounted for, and the run SHA contains the approved D5′ changes.
- **FAIL:** the Redis service is unhealthy, the gate FAILED, or any required test failed.
- **UNVERIFIED:** any required test SKIPPED, or logs do not establish real Redis execution.

A required Redis assertion is **never** satisfied by a skip.

## Boundary (unchanged by a PASS here)
Closing this checklist closes ONLY the real-Redis evidence gap. QUOTA-01 stays OPEN pending
independent dispositions of D9, D3, D2 and the remaining quota-enforcement gaps (order-rate,
daily-trade, active-order) and the slot-lock release non-atomicity residual. Do not modify the
launch scorecard, close QUOTA-01, or authorize paper/live trading on the strength of this run.
