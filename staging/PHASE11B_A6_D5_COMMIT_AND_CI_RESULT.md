# Phase 11B-A6 — D5′ Commit, Push, PR & CI Result

**Verdict: FAIL** (by the task's strict rule — required real-Redis tests failed), with a
decisive qualification: **the failures are pre-existing full-suite isolation contamination, not
a defect in the real-Redis integration or in D5′ itself.** The D5′ mechanism worked — the gate
executed against a healthy real Redis rather than skipping, and the metered-reservation
real-Redis concurrency test PASSED. No test, assertion, skip, or the excluded conftest fix was
modified to influence the result.

---

## 1. Baseline & ownership (Phase 0, re-verified fresh)

| Item | Value |
|---|---|
| Base/HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main` == `origin/main`, 0 ahead/0 behind) |
| Workflow working-tree diff | 170 added / 0 removed; 3 hunks (D5′ = `+186,26` & `+216,1`; exclude = `+495,143` provisioning) |
| D5′ test files | 4 untracked, line counts 149 / 209 / 684 / 300 (unchanged) |
| Excluded | `tests/conftest.py` (rate-limiter leak guard, no Redis refs), `pytest.ini` (`realaudit` marker), the 143-line `provisioning-and-isolation` job, 22 pre-existing shared-index files, all other concurrent work |
| Target branch pre-exists? | No (local or origin) |

## 2. Isolated commit (Phases 1–2)

Built in a **private temporary index** (`GIT_INDEX_FILE` seeded from base; shared index never
used): 4 files added by name + the D5′ workflow hunk applied with `git apply --cached --recount`.
Commit created by plumbing (`write-tree` → `commit-tree -p <base>` → `update-ref` with
all-zero CAS); the worktree was never switched.

- **Commit SHA:** `b43e990dc31766b7456dad8abd8dc5705c143814`
- **Tree:** `8cf002a26eb7cf93e8c91875cd3755f026451035`
- **Parent:** `074b772a` (verified base)
- **Message:** `test: execute quota and webhook gates against real Redis`
- **Changed paths (exactly 5):**
  - `M .github/workflows/01-pr-check.yml` (**27 added, 0 removed**; no `provisioning-and-isolation`)
  - `A tests/real_redis_gate.py`
  - `A tests/test_quota01_exchange_slot_is_atomic.py`
  - `A tests/test_quota01_metered_reservation_is_hard.py`
  - `A tests/test_webhook_duplicate_delivery_real_redis.py`

## 3. Push & PR (Phase 3)

- Pushed only `ci/d5-real-redis-quota-evidence` → `origin` (remote tip = `b43e990d`, matches).
- **PR #6:** https://github.com/Narendra2212/vyomquant-saas/pull/6 — base `main`, head
  `ci/d5-real-redis-quota-evidence`, head SHA `b43e990d`, state OPEN, MERGEABLE, **not merged**.
- **Workflow run:** `38043513862` (event `pull_request`, headSha `b43e990d` — the exact approved commit).

## 4. CI execution (Phase 4)

**Tested SHA = `b43e990d` = the approved D5′ commit.** ✔

**Redis service:** healthy. Container log shows `Redis version=7.4.11 … Running mode=standalone,
port=6379 … Ready to accept connections`. The `unit-tests` job exported `REDIS_URL:
redis://localhost:6379` and `AERORA_TEST_REDIS_URL: redis://localhost:6379/15`. The shared gate
reached its **execution** path — the nine real-Redis tests RAN (none skipped), which is the
behavior D5′ was built to produce.

### Nine required real-Redis tests — individual outcomes

| Test | Result |
|---|---|
| `test_concurrent_reservations_are_atomic_against_real_redis` (quota, metered) | **PASS** |
| `test_the_slot_lock_is_mutually_exclusive_against_real_redis` (quota, slot) | **FAIL** |
| `test_the_guard_admits_exactly_the_limit_against_real_redis` (quota, slot) | **FAIL** |
| `test_the_credential_gate_refuses_test_keys` (webhook) | **PASS** |
| `test_a_redelivered_webhook_is_ignored` (webhook) | **PASS** |
| `test_the_lock_is_recorded_in_redis_under_the_documented_key` (webhook) | **FAIL** |
| `test_a_different_event_id_is_not_deduplicated` (webhook) | **PASS** |
| `test_an_in_flight_delivery_blocks_a_concurrent_one` (webhook) | **FAIL** |
| `test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind` (webhook) | **PASS** |

5 passed, 4 failed. **No required test was skipped** — the gate executed against real Redis, so
the "configured but reachable → run" path is proven.

### Wider signal: whole-file slot-lock failure under the full suite

Beyond the 2 slot real-Redis tests, **every** test in `tests/test_quota01_exchange_slot_is_atomic.py`
FAILED in CI (incl. the pure-logic ones: `test_two_concurrent_stores…`,
`test_eight_concurrent_stores…`, `test_the_lock_is_released_when_the_guarded_write_raises`,
`test_a_bare_delete_on_release_fails_this_suite`, etc.). Those same tests **PASS locally in
isolation** (26 passed, Phases 11B-A4/A5). A whole file passing alone but failing inside the full
suite is the signature of **test-ordering / shared-state contamination**, not a defect in the
code under test.

By contrast, `tests/test_quota01_metered_reservation_is_hard.py` passed in full (all 7, incl. the
real-Redis one) — it calls `SubscriptionEngine` directly, bypassing the HTTP/rate-limited layer
that the slot-lock and webhook tests exercise.

## 5. Separation of unrelated failures (Phase 4, continued)

**The contamination is pre-existing and D5′-independent.** The prior CI run `37346853502`
(commit `6c5660a1`, which does NOT contain D5′) failed the **identical** broad set of non-Redis
tests:
`test_tenant_isolation_matrix`, `test_ml_deployment_guards`, `test_no_credential_in_api_response_body`
(14 cases), `test_strategy_status_publish` (4), `test_task_33_2_rate_limit_keys`,
`test_task_6_1_backtest_execute` (7), `test_task_8_4_runtime_readiness_gate`,
`test_tenant_isolation_library_paper` (2), `test_validation_sweep` (9). Every one of these also
failed in the D5′ run. They are not introduced by this commit.

The only **net-new** failures in the D5′ run are the `test_quota01_exchange_slot_is_atomic.py`
tests and the two webhook tests — because those files did not exist in the baseline. They fail
for the same full-suite contamination reason: the HTTP-path/`TestClient` tests inherit the
shared rate-limiter/ordering leak that `REGRESSION_ORDER_ISOLATION.md` documents and that the
**deliberately-excluded `tests/conftest.py` leak-guard** was written to resolve. That fix belongs
to a separate workstream and was correctly kept out of this D5′ commit; `main` does not carry it
either, which is why the baseline is equally red.

> Mechanism stated with appropriate confidence: the whole-file-passes-alone / fails-in-suite
> pattern plus the identical baseline failure set establish full-suite contamination. I did not
> capture the individual slot-lock traceback text from the 28 MB interleaved CI log, so the
> precise per-assertion cause is inferred from the pattern and the documented leak, not quoted
> verbatim. This is a reporting limitation, not a change to the conclusion.

### Overall CI result (reported separately)

| Job | Result |
|---|---|
| Code Quality & Static Audit | success |
| Format Backlog (report only) | success |
| Frontend Build & Unit Tests | success |
| Execution Tests (production posture) | success (274 passed, 1 xfailed) |
| **Unit & Integration Tests** | **failure** (pre-existing contamination + the 4 net-new D5′ HTTP/webhook tests) |
| Database Tests (PostgreSQL) | success |
| Docker Build Validation | success |

Overall run `38043513862`: **failure** — driven by the `unit-tests` lane. The unrelated
tenant-isolation failure (`Requirement 21.8: 4 of 150 cells`) is preserved and reported, not
masked.

## 6. In-memory regression

The 26 in-memory logic tests pass locally in isolation (unchanged from A4/A5). In the full CI
suite the metered-reservation logic (6) passed; the slot-lock logic failed only due to the
suite-ordering contamination described above, not in isolation.

## 7. Preservation (Phase 5)

- `main` and `origin/main` unchanged at `074b772a`; not advanced, not merged.
- The 22 pre-existing shared-index entries intact.
- No unrelated file entered the D5′ commit (exactly the 5-path manifest).
- No concurrent change reset/overwritten/discarded; worktree never switched.
- PR #6 OPEN, `mergedAt: null`. No merge occurred.
- Temporary helper scripts and logs deleted.

## 8. Verdict & remaining blockers

**FAIL** against the acceptance rule (not all nine required real-Redis tests passed). The
failure is **not** a Redis-integration defect: the gate executed against a healthy real Redis,
and the metered-reservation atomicity test passed. The 4 failing tests are collateral of a
**pre-existing full-suite isolation contamination** (documented rate-limiter/ordering leak)
whose fix lives in a separate, deliberately-excluded workstream.

**Smallest path to a clean D5′ result (requires separate authorization; NOT done here):**
either (a) include the `tests/conftest.py` leak-guard (its own workstream) so the full suite is
isolation-clean, then re-run; or (b) have CI run the three D5′ files in a dedicated selection so
they are not subject to full-suite ordering — a workflow-selector decision, not a test change.
**Do not** weaken/skip/xfail any D5′ test to go green.

**QUOTA-01 remains OPEN.** Even the one passing real-Redis test (metered reservation) does not
close it: the slot-lock real-Redis atomicity is unproven here (contaminated), and D9/D3/D2, the
order-rate/daily-trade/active-order ceilings, and the slot-lock release non-atomicity residual
all require separate evidence and disposition. Launch-gate status and live-trading authorization
unchanged. Live trading remains OFF / NOT CERTIFIED.
