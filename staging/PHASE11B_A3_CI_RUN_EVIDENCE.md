# PHASE 11B-A3 — QUOTA-01 REAL-REDIS CI VERIFICATION

**Objective:** obtain actual GitHub Actions evidence that the D5′ real-Redis gate works in CI
and that `test_concurrent_reservations_are_atomic_against_real_redis` **executes and passes**
against the CI Redis service.

**Baseline HEAD:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`, == `origin/main`)
**Remote:** `https://github.com/Narendra2212/vyomquant-saas.git`
**Date:** 2026-10-10

## ▶ DECISION: **UNVERIFIED**

No eligible CI run exists. The entire D5′ change set — the shared gate, all three real-Redis
test files, and the `AERORA_TEST_REDIS_URL` switch — is **uncommitted and present on no
remote ref**, so no GitHub Actions run has ever contained it. Obtaining the evidence requires
a push, which was not authorized and **was not performed**.

**Live trading OFF. No commit, push, branch, PR, workflow dispatch, reset, clean, stash or
revert occurred.** §10 confirms this.

---

## 1. Baseline and ownership boundaries

| Category | Count | Note |
|---|---|---|
| Staged | **22** | Other workstreams. Untouched, byte-identical at exit. |
| Unstaged | 97 | Includes 2 files relevant here, **both owned elsewhere** |
| Untracked (QUOTA-01 + real-Redis corpus) | **7** | The subject of this task |

### 1.1 Two dirty files are shared, and neither is mine to move

| File | State | Owner |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | ` M` | **Two unrelated changes share it** — see §1.2 |
| `tests/conftest.py` | ` M` | **NOT D5′.** A rate-limiter leak guard (`REGRESSION_ORDER_ISOLATION.md`): an autouse fixture resetting every bound `slowapi` Limiter. Nothing to do with Redis. |

**Correction to an earlier assumption of mine:** I had expected `conftest.py`'s dirty state to
*be* the shared real-Redis gate. It is not. The two lines D5′ actually depends on —
`os.environ["REDIS_URL"] = ""` and `os.environ["VYOMQUANT_MODE"] = "safe"` — are **already
committed on `origin/main`**; the unstaged diff only appends at line 269+.

### 1.2 `01-pr-check.yml` carries two unrelated changes

Read the diff rather than assuming. The one unstaged file contains:

1. **D5′ (27 lines)** — adds `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` to the
   `Run Unit Tests` step env, with a comment explaining that `REDIS_URL` is blanked by
   conftest so the real-Redis tests always skipped.
2. **A separate `provisioning-and-isolation` job (~149 lines)** — PostgreSQL provisioning,
   privilege hardening and tenant isolation. **Different workstream. Out of scope.**

Both were preserved. Nothing in that file was edited.

### 1.3 The 7 untracked files

```
tests/real_redis_gate.py                                   <-- the shared D5' gate
tests/test_quota01_metered_reservation_is_hard.py          <-- carries the REQUIRED test
tests/test_quota01_exchange_slot_is_atomic.py
tests/test_quota01_enforcement_is_wired.py
tests/test_quota01_hard_enforcer_is_deprecated_in_code.py
tests/test_redis_fallback_does_not_fake_a_distributed_lock.py
tests/test_webhook_duplicate_delivery_real_redis.py
```

Also untracked and relevant to §7: `tests/test_api03_rate_limit_storage_is_fail_closed.py`,
`tests/test_api04_store_failure_after_startup.py`.

---

## 2. Is there an eligible remote run? **NO.**

Two independent checks, both negative.

### 2.1 Every remote ref — MEASURED

`python staging/a3_check_remote_presence.py`, after `git fetch --all --prune`:

```
Remote branches inspected: 12
branch                                               gate+tests   AERORA_TEST_REDIS_URL
origin/main                                          0/4          no
origin/ci/unblock-deploy-gate                        0/4          no
origin/feat/marketplace-subscriptions-paper-trading  0/4          no
origin/feat/trading-lifecycle-integration            0/4          no
origin/launch-hardening/wave5-section12-safety-proofs 0/4         no
origin/chore/connect-vyomquant-in-domain             0/4          no
origin/fix/perf-account-health                       0/4          no
origin/fix/perf-supabase-async                       0/4          no
origin/fix/perf-supabase-migrate-callers             0/4          no
origin/fix/perf-vault-singleton                      0/4          no
origin/perf/final-validation                         0/4          no
```

**0 of 4 files on all 12 branches. The switch is absent from all 12.**

### 2.2 Actual Actions run history — MEASURED

`gh run list --workflow=01-pr-check.yml --limit 20`, then testing each run's **tested commit**
with `git ls-tree <sha>`:

```
Runs inspected: 14
run id       sha        branch                      result            D5 files   SWITCH
37346853502  6c5660a1   ci/unblock-deploy-gate      completed/failure 0/4        no
37343548204  56297a57   ci/unblock-deploy-gate      completed/failure 0/4        no
37342904270  979eb35f   ci/unblock-deploy-gate      completed/failure 0/4        no
37341664120  630a33c5   ci/unblock-deploy-gate      completed/failure 0/4        no
37337211390  1dfba58c   ci/unblock-deploy-gate      completed/failure 0/4        no
37336280698  e7816f4f   ci/unblock-deploy-gate      completed/failure 0/4        no
37335793537  0264450c   ci/unblock-deploy-gate      completed/failure 0/4        no
37326385750  90318820   ci/unblock-deploy-gate      completed/failure 0/4        no
37304309325  f1376280   ci/unblock-deploy-gate      completed/failure 0/4        no
36266778219  d8ca1794   launch-hardening/wave5…     completed/failure 0/4        no
35584354898  9822c5f6   feat/marketplace-subs…      completed/failure 0/4        no
35580281685  d83b3ec5   feat/marketplace-subs…      completed/failure 0/4        no
34730941772  8a6e24f7   chore/connect-vyomquant…    completed/failure 0/4        no
33266679438  f0e4fc6a   feat/trading-lifecycle…     completed/failure 0/4        no

Runs containing the complete D5' change set: 0
```

`gh workflow list --all` shows 7 registered workflows — `01`–`06` plus Dependabot. No
real-Redis or quota workflow exists remotely.

---

## 3. Run identity and tested commit

**None.** There is no run to identify. Every field §3 of the brief asks for is unobtainable:

| Required field | Value |
|---|---|
| Workflow URL | **n/a — no eligible run** |
| Run ID | **n/a** |
| Commit SHA | **n/a** (the change set exists only in the local working tree) |
| Job name | would be `unit-tests` in `01 PR Check` |
| Service health | **not observed** |
| Test result | **not observed** |

---

## 4. Redis service health and gate behaviour

### 4.1 CI service — already committed, never exercised by these tests

`origin/main`'s `01-pr-check.yml` **already contains** the service, so this half needs no
change:

```yaml
services:
  redis:
    image: redis:7-alpine
    ports: [6379:6379]
```
and the step exports `REDIS_URL: redis://localhost:6379`.

**MEASURED:** `AERORA_TEST_REDIS_URL` appears **nowhere** in the committed file. **INFERRED
from source:** because committed `tests/conftest.py` executes `os.environ["REDIS_URL"] = ""`
at import, the gate reads `''` and skips — so the healthy service has never been able to
switch these tests on. That is precisely the defect D5′ exists to fix, and the fix is
uncommitted.

### 4.2 Gate review — static, not integration

`tests/real_redis_gate.py` reviewed in full. It is correctly constructed:

| Property | Finding |
|---|---|
| Opt-in variable | `AERORA_TEST_REDIS_URL` **only**; `REDIS_URL` deliberately not consulted |
| Three states | `skip` (unset) / `fail` (set but invalid, non-loopback, or no PING) / `run` |
| Non-loopback | **refused** unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1` |
| Secret hygiene | no URL, host or credential in any message — only variable names |
| Would CI reach `run`? | **INFERRED yes**: `redis://localhost:6379/15` is loopback, so the only remaining condition is the PING. **NOT OBSERVED.** |

A configured-but-dead endpoint **fails** rather than skipping, so a broken CI Redis could not
masquerade as a green run. Good design; still unexecuted.

### 4.3 Gate state here — MEASURED

```
real-Redis gate: state=skip (AERORA_TEST_REDIS_URL=unset, remote_allowed=False)
GATE_STATE = skip
```

Local Redis: `NO REDIS on 127.0.0.1:6379` (TCP connect). So the local skip is the gate's
**legitimate** opt-out, and its own reason says: *"A skip here is NOT a pass."*

---

## 5. The required concurrency test

`tests/test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis`

### 5.1 Required result: **PASSED**. Actual: **SKIPPED locally, never run in CI.**

```
SKIPPED [1] tests/test_quota01_metered_reservation_is_hard.py:228:
  AERORA_TEST_REDIS_URL is not set. … A skip here is NOT a pass.
```

**This does not satisfy the requirement.** §3 demands PASSED, not SKIPPED.

### 5.2 Static review of the test body — INFERRED, satisfies the construction criteria

| §3 criterion | Finding |
|---|---|
| Executes Redis-backed concurrency assertions | **Yes** — `redis.asyncio.from_url(redis_url)`, 50 concurrent racers against a limit of 10, server-side `incrby`/`decrby` |
| Not `MockRedisClient` / in-memory | **Confirmed** — the real async client; `MockRedisClient` is used only by the six sibling *logic* tests |
| Asserts the hard limit | `sum(admitted) == limit` **and** `int(await client.get(key)) == limit` |
| Cleanup does not touch unrelated data | **Confirmed** — `await client.delete(key)` in `finally`, on a single key derived from a uuid user *and* a uuid period. **No `FLUSHDB` anywhere in the corpus.** |
| Gate wiring | `@real_redis_required` (skip when unasked) + `require_real_redis()` (fail when asked-and-broken) |
| Would collect and run | `pytest-asyncio` reports `mode=Mode.STRICT`; the module sets `pytestmark = pytest.mark.asyncio`, which strict mode requires. `SubscriptionEngine._meter_key` exists. |

So the test is correctly built and would execute if the gate reached `run`. **That is a
source review, not evidence of execution.**

---

## 6. Exchange-slot, webhook and quota regression results

Local focused run — `66 passed, 9 skipped, 0 failed`, exit 0. Every test accounted for:

| File | PASS | SKIP | FAIL |
|---|---|---|---|
| `test_quota01_enforcement_is_wired.py` | 17 | 0 | 0 |
| `test_quota01_exchange_slot_is_atomic.py` | 20 | **2** | 0 |
| `test_quota01_hard_enforcer_is_deprecated_in_code.py` | 7 | 0 | 0 |
| `test_quota01_metered_reservation_is_hard.py` | 6 | **1** | 0 |
| `test_redis_fallback_does_not_fake_a_distributed_lock.py` | 16 | 0 | 0 |
| `test_webhook_duplicate_delivery_real_redis.py` | 0 | **6** | 0 |
| **TOTAL** | **66** | **9** | **0** |

All 9 skips, by name, every one the identical gate reason (`AERORA_TEST_REDIS_URL` unset):

```
test_quota01_metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis
test_quota01_exchange_slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis
test_quota01_exchange_slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis
test_webhook_duplicate_delivery_real_redis.py::test_the_credential_gate_refuses_test_keys
test_webhook_duplicate_delivery_real_redis.py::test_a_redelivered_webhook_is_ignored
test_webhook_duplicate_delivery_real_redis.py::test_the_lock_is_recorded_in_redis_under_the_documented_key
test_webhook_duplicate_delivery_real_redis.py::test_a_different_event_id_is_not_deduplicated
test_webhook_duplicate_delivery_real_redis.py::test_an_in_flight_delivery_blocks_a_concurrent_one
test_webhook_duplicate_delivery_real_redis.py::test_an_invalid_signature_is_rejected_and_leaves_no_lock_behind
```

**Confirming the brief's expectation:** the webhook file does activate through the shared
gate — all 6 of its tests are gated, and `test_webhook_duplicate_delivery_real_redis.py` has
**zero** non-gated tests, so with D5′ in CI it goes from 6 skips to 6 executions. That is the
largest behavioural change D5′ produces and it is entirely unverified.

**The 66 passes are logic coverage on the in-process substrate.** They do not substitute for
the atomicity proof: `MockRedisClient.incrby` only *models* a server-side atomic INCR.

---

## 7. Independent failure — recorded separately, not hidden, not worked around

`tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated`

```
tests/test_api04_store_failure_after_startup.py:172: in test_…_are_decorated
    from backend_app.routers import auth, billing
backend_app/routers/billing.py:51:   from backend_app.core.database import get_db
backend_app/core/database.py:32:     from backend_app.core.database_pool import get_db as _get_db_pool
backend_app/core/database_pool.py:141:
    raise RuntimeError("DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.")
E   RuntimeError: DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.
```

| Probe | Result |
|---|---|
| Group run (`api03` + `api04`) | **1 failed, 45 passed**, exit 1 |
| With CI's `AERORA_MODE=safe` | **still fails** — so CI would hit it too |
| In isolation (`-k sensitive_endpoints`, 11 deselected) | **still fails** — deterministic, not order-dependent |
| `api03` alone | **34 passed, 0 failed** |

**It is a database-configuration failure, wholly independent of Redis and of D5′.**

Root-cause attribution, stated honestly: I checked the two obvious mechanisms and **neither
explains it.** Committed `conftest.py` blanks `DATABASE_URL` and sets
`VYOMQUANT_MODE=safe`; the file's own autouse `clean_env` fixture then `delenv`s both
`VYOMQUANT_MODE` and `AERORA_MODE`. **MEASURED:** with both absent,
`get_vyomquant_mode('safe')` returns `'safe'`, which does **not** satisfy
`_env_mode in ["paper","live"]`. So the paper/live value is introduced somewhere inside that
test's own import chain, and identifying it belongs to the API04 / rate-limit owner.

**Per the brief: the database fallback behaviour was NOT changed, the assertion was NOT
weakened, and the failure was NOT converted to a skip.** `database_pool.py`,
`database.py` and both API test files are unmodified.

**Consequence for the CI job outcome:** the `unit-tests` job would be **RED** on this test
even if the real-Redis verification succeeded. The two statuses are therefore separate:

| Statement | Status |
|---|---|
| Overall `unit-tests` job outcome (predicted) | **FAIL** — independent API04 database-config failure |
| D5′ real-Redis verification | **UNVERIFIED** — never executed |

---

## 8. Final decision

### **UNVERIFIED**

Against §5's definitions: no eligible run exists, the required test skipped rather than
executed, and no log establishes real Redis execution. All three UNVERIFIED conditions hold.

Not **FAIL**: nothing demonstrates the test or the gate is broken. The gate behaved
*correctly* for its inputs — it skipped because the opt-in was genuinely unset, which is its
documented legitimate skip.

**QUOTA-01 is NOT closed and is NOT advanced.** Per §7 of the brief, a passing real-Redis test
would close only the "no test exercises the quota path against a real Redis" evidence gap —
and that test has not passed. No scorecard, launch-gate register or readiness artefact was
touched.

---

## 9. The smallest justified next action

One push and one dispatch. Nothing else is required, and nothing smaller suffices.

**What must be authorized:**

1. **Create a branch and commit exactly 8 paths** — the 7 untracked QUOTA-01/real-Redis files
   plus the `AERORA_TEST_REDIS_URL` line in `01-pr-check.yml`.

   ⚠️ **`01-pr-check.yml` cannot be committed wholesale.** It also carries the unrelated
   ~149-line `provisioning-and-isolation` job (§1.2), which would silently ship another
   workstream's work. This needs either a path-scoped partial commit (`git add -p`) or that
   workstream's sign-off. **This is the only non-trivial step.**

2. **Push the branch** (`git push -u origin <branch>`).

3. **Let `01 PR Check` run**, or `gh workflow run 01-pr-check.yml --ref <branch>`.
   Credentials are already sufficient — **MEASURED:** `gh` 2.100.0, authenticated as
   `Narendra2212`, token scopes include `workflow`.

4. **Read the `unit-tests` job log** for:
   - `redis` service health → healthy;
   - `test_concurrent_reservations_are_atomic_against_real_redis` → **PASSED**, not SKIPPED;
   - the other 8 gated tests → all executed;
   - the API04 failure (§7) recorded separately as the job's independent red.

**Expected outcome:** the real-Redis verification should turn PASS while the overall job stays
RED on API04. Those must be reported as two separate facts.

---

## 10. Confirmation: no unauthorized remote action occurred

| Action | Performed? |
|---|---|
| commit / push / branch / tag | **NO** |
| PR opened | **NO** |
| workflow dispatched or re-run | **NO** |
| reset / clean / stash / revert / checkout of tracked files | **NO** |
| index staged or modified | **NO** — staged set is 22, unchanged |

**Read-only remote calls only:** `git fetch --all --prune` (updates remote-tracking refs; no
local branch or working-tree change), `gh auth status`, `gh run list`, `gh workflow list`.

**Application and test files changed by this task: NONE.**

Files added — all evidence and tooling under `staging/`, matching the existing `staging/*`
probe convention:

```
staging/PHASE11B_A3_CI_RUN_EVIDENCE.md    this report
staging/a3_check_remote_presence.py       per-remote-ref presence check (§2.1)
staging/a3_summarise_runs.py              per-run D5' containment check (§2.2)
staging/a3_runs.json                      captured gh run metadata
staging/a3_parse_pytest.py                per-test outcome table (§6, §7)
staging/a3_failure_detail.py              failure-block extractor (§7)
staging/a3_local_quota.log                §6 raw evidence
staging/a3_local_api.log                  §7 raw evidence
staging/a3_api04_cimode.log               §7 CI-mode reproduction
staging/a3_api04_alone.log                §7 isolation reproduction
```

Out-of-scope work explicitly **not** done: atomic exchange-slot lock release; order-rate,
daily-trade or active-order quota gates; scorecard or launch-gate register edits; exchange
routing, tenant isolation, reconciliation or payment changes.

**Live trading remains OFF.** No paper or live trading was authorized. This report makes no
trading-readiness and no production-certification claim.
