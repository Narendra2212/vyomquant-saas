# PHASE 10 — D5′: ENABLE REAL-REDIS GATES IN CI

**Status: `IMPLEMENTED — REAL-REDIS VERIFICATION PENDING`**

The gate now flips correctly and the tests are selected rather than skipped under CI's exact
environment. No Redis server exists in this environment and installing one was not authorized,
so the tests have not yet been **executed to completion against a real server**.

**QUOTA-01 is not closed.** Its atomic lock-release, production over-limit population and
database-backstop questions are untouched by this workstream.

**Label key.** **VERIFIED** = established here by a command whose output is recorded.
**PENDING** = requires a real Redis. **NOT CHANGED** = deliberately left alone.

---

## 1. Root cause

Two defects in the gate predicate, not in the Redis service.

### 1a. The `REDIS_URL` half of the predicate was dead

`tests/conftest.py:13` executes, unconditionally and at import time, before any test module
loads:

```python
# Clear REDIS_URL to force in-memory rate limiting even if set in environment
os.environ["REDIS_URL"] = ""
```

The three real-Redis gates read:

```python
_REAL_REDIS_URL = os.environ.get("AERORA_TEST_REDIS_URL") or os.environ.get("REDIS_URL") or ""
```

So the CI `unit-tests` job — which provisions `redis:7-alpine`, maps 6379, health-checks it,
and exports `REDIS_URL: redis://localhost:6379` — could never switch these tests on. The gate
read `''` and skipped on every pull request. Measured in `staging/p10k_gate_predicate.txt`.

`conftest.py`'s blanking is **correct and was not touched**: it exists so the 11,000+ tests in
that lane cannot be re-pointed at a live cache by whatever happens to be exported, and
`core/rate_limit.py` raises at import when `REDIS_URL` is absent outside dev.

The fallback was also a hazard in the other direction. `REDIS_URL` is the name **production**
sets; any context importing those modules without `conftest` having run first would have read a
production endpoint out of it.

### 1b. A string-shape gate cannot tell a live server from a dead one

`not _REAL_REDIS_URL.startswith(("redis://", "rediss://"))` admits a URL no server answers. A
broken CI Redis would then have produced either a crash deep inside a test body or — if the gate
had been copied from `tests/test_webhange_duplicate_delivery_real_redis.py`'s reachability
shape — a **skip**, which reads as "nobody wanted this" rather than "the service you configured
is broken". Both hide a broken service behind a green run.

### 1c. Consequence beyond QUOTA-01

`tests/test_quota01_metered_reservation_is_hard.py`'s real-Redis variant uses the same predicate,
so the **pre-existing** metered-reservation atomicity proof had also never executed in CI. It has
been carried as evidence-in-waiting since it was written.

---

## 2. Files changed, and why each

| File | Change | Rationale |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | **+1 env line** (`AERORA_TEST_REDIS_URL: redis://localhost:6379/15`) on the `unit-tests` job, plus a comment block explaining it | Uses the Redis service **already** provisioned in that job. `AERORA_TEST_REDIS_URL` is the existing "this one is deliberate" convention — the same pattern `AERORA_TEST_DATABASE_URL` uses for PostgreSQL in `database-tests`, and the name `test_webhook_duplicate_delivery_real_redis.py` already read. `conftest` does not blank it. `/15` pins a dedicated database index. **`REDIS_URL` retained unchanged** — `test_api03_*` and `test_api04_*` assert on application behaviour when it is present, and `conftest` blanks it for everyone regardless, so removing it would alter an unrelated lane for no benefit |
| `tests/real_redis_gate.py` | **NEW**, 1 module | One place decides the gate for all three files. Reads `AERORA_TEST_REDIS_URL` **only** (the dead, hazardous `REDIS_URL` fallback is gone), PINGs to decide reachability, refuses a non-loopback host without an explicit flag, and separates the three outcomes |
| `tests/test_quota01_exchange_slot_is_atomic.py` | replaced the local predicate with the shared gate on 2 tests; dropped the now-unused `import os` | Removes the duplicated, defective predicate |
| `tests/test_quota01_metered_reservation_is_hard.py` | same, on 1 test; dropped the now-unused `import os` | Same |

### Deliberately NOT changed

* **`tests/conftest.py`** — **NOT CHANGED** (**VERIFIED**: a diff of that file contains zero D5′
  edits; the ` M` status on it is another tab's concurrent work). It does not erase
  `AERORA_TEST_REDIS_URL`, so requirement 2 was already satisfied, and its `REDIS_URL` blanking
  is a safety property worth keeping.
* **`tests/test_webhook_duplicate_delivery_real_redis.py`** — **NOT CHANGED**. It already reads
  the correct variable and gates on reachability. Its unreachable-endpoint behaviour is a *skip*
  rather than a *fail*, which now differs from the quota gates; that divergence is recorded in
  §6 rather than silently changed in a file outside this workstream's scope.
* **No application code.** **VERIFIED**: `git diff --numstat` for
  `subscription_dependencies.py` (`253/0`), `connection_engine.py` (`188/18`) and
  `redis_manager.py` (`185/5`) are all unchanged from the figures recorded by the preceding
  workstreams.
* **No atomic lock-release fix** — explicitly out of scope, and still blocked on a real server.
* **No dependency or infrastructure added.** `redis-py 5.0.8` was already installed.

---

## 3. Before / after gate behaviour

| Condition | Before | After |
|---|---|---|
| CI `unit-tests` (`REDIS_URL` set by the job) | **SKIP** — gate read `''` | **RUN** |
| Local, nothing set | SKIP | SKIP (the one legitimate skip) |
| `AERORA_TEST_REDIS_URL` set, server reachable | RUN | RUN |
| `AERORA_TEST_REDIS_URL` set, **server down** | RUN, then crash inside the test body | **FAIL** with a named reason |
| `AERORA_TEST_REDIS_URL` set, **malformed** | SKIP (silently) | **FAIL** |
| `AERORA_TEST_REDIS_URL` set, **non-loopback host** | RUN — would contact it | **FAIL**, refused unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1` |
| `REDIS_URL` pointing at production, no opt-in | gate would read it | **ignored entirely** |

Mapping to the seven required properties:

| # | Requirement | Status |
|---|---|---|
| 1 | CI test process receives the isolated endpoint | **VERIFIED** — §4 YAML parse |
| 2 | Harness does not silently erase the variable tests consume | **VERIFIED** — `conftest` never touches `AERORA_TEST_REDIS_URL`; measured in `staging/p10k_gate_predicate.txt` case B |
| 3 | No fallback to production or shared Redis | **VERIFIED** — `REDIS_URL` removed from the predicate; non-loopback refused by default |
| 4 | Skips only when genuinely unavailable or opt-in disabled | **VERIFIED** — §4 state 1 |
| 5 | Configured-but-unreachable fails explicitly | **VERIFIED** — §4 state 2, `3 failed`, exit 1 |
| 6 | Non-Redis tests still work without Redis | **VERIFIED** — §4 |
| 7 | No secrets in logs | **VERIFIED** — §4; reasons name variables, never values |

---

## 4. Test evidence

Every command was run with a bounded `--timeout`. Counts are verbatim.

### State 1 — opt-in unset ⇒ SKIP (the only legitimate skip)

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       -p no:randomly -q --no-header -p no:warnings -rs -k "real_redis" --timeout=120
-> 3 skipped, 26 deselected in 38.46s        EXIT=0
```

Reason text now states *"A skip here is NOT a pass."*

### State 2 — configured but unreachable ⇒ FAIL (the defect this closes)

```
AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15  pytest <same> -k "real_redis"
-> 3 failed, 26 deselected in 28.16s         EXIT=1
   Failed: AERORA_TEST_REDIS_URL is set but the server did not answer PING (TimeoutError).
           … (The endpoint is deliberately not echoed.)
```

### State 3 — CI's exact environment ⇒ tests are SELECTED, not skipped

`staging/run_d5prime_ci_sim.ps1` → `staging/d5prime_ci_env_selects_tests.txt`, with
`REDIS_URL=redis://localhost:6379`, `AERORA_TEST_REDIS_URL=redis://localhost:6379/15`,
`ENV=test`, `AERORA_MODE=safe`:

```
-> 3 failed, 26 deselected in 13.51s         EXIT=1   (0 skipped)
```

**This is the proof the fix works.** Before D5′, this exact environment produced
`3 skipped`. It now produces `3 selected`. They fail only because no server answers on this
machine — in CI, where the service is health-checked before the step runs, the PING succeeds and
the bodies execute.

### Guard logic — malformed, remote refusal, and the escape hatch

| Input | `GATE_STATE` | Leaks host/URL? |
|---|---|---|
| `localhost:6379` (no scheme) | `fail` | — |
| `redis://redis.example.com:6379/15` | `fail` (refused as non-loopback) | **host: no, url: no** (**VERIFIED**) |
| same + `AERORA_TEST_REDIS_ALLOW_REMOTE=1` | `fail` at the **PING**, not at the refusal | — |

### Requirement 6 — non-Redis and Redis-sensitive tests unaffected

```
pytest tests/test_api03_rate_limit_storage_is_fail_closed.py \
       tests/test_api04_store_failure_after_startup.py \
       tests/test_redis_fallback_does_not_fake_a_distributed_lock.py \
       tests/test_webhook_duplicate_delivery_real_redis.py --timeout=300
-> 1 failed, 61 passed, 6 skipped            EXIT=1
```

**The 1 failure is pre-existing and is NOT caused by D5′** — see §5.

### No regression on the quota suites

```
pytest tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py \
       tests/test_quota01_enforcement_is_wired.py \
       tests/test_quota01_hard_enforcer_is_deprecated_in_code.py --timeout=300
-> 50 passed, 3 skipped                      EXIT=0

# after the unused-import cleanup, the two edited files alone:
-> 26 passed, 3 skipped                      EXIT=0
```

### Workflow validity

```
yaml.safe_load('.github/workflows/01-pr-check.yml')
-> parses OK; 8 jobs
   unit-tests redis service image: redis:7-alpine
   health-cmd present: True
   step env keys: ['AERORA_MODE','AERORA_TEST_REDIS_URL','ENV','PYTHONPATH','REDIS_URL']
   AERORA_TEST_REDIS_URL = redis://localhost:6379/15
   REDIS_URL retained    = redis://localhost:6379
```

The existing `--health-cmd "redis-cli ping" --health-interval 10s --health-timeout 5s
--health-retries 5` options already make GitHub Actions wait for the service to be healthy
before the step runs, so "service healthy before tests start" needs no change.

### Skipped-test accounting

| Test | State now | Will run in CI? |
|---|---|---|
| `…slot_is_atomic.py::test_the_slot_lock_is_mutually_exclusive_against_real_redis` | SKIPPED locally (no opt-in) | **Yes** |
| `…slot_is_atomic.py::test_the_guard_admits_exactly_the_limit_against_real_redis` | SKIPPED locally | **Yes** |
| `…metered_reservation_is_hard.py::test_concurrent_reservations_are_atomic_against_real_redis` | SKIPPED locally | **Yes** — previously dormant |
| `test_webhook_duplicate_delivery_real_redis.py` (6 tests) | SKIPPED locally | **Yes** — see the §6 risk |

**No skipped test is reported as passed anywhere in this document.**

---

## 5. The one failure, and why it is not D5′

`tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated`
fails at **import** of `backend_app.core.database_pool`:

```
RuntimeError: DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.
  backend_app/core/database_pool.py:141
```

Attribution, established rather than asserted:

1. **Airtight test.** `tests/real_redis_gate.py` was moved aside and the test re-run
   (`staging/run_d5prime_attribution.ps1` → `staging/d5prime_api04_without_helper.txt`):
   **`1 failed`, exit 1, with the helper absent**, then the helper was restored
   (`helper restored: True`). The failure does not depend on anything D5′ wrote.
2. `backend_app/core/database_pool.py`, where the raise lives, is **clean / committed**
   (**VERIFIED** via `git status --porcelain`).
3. The only modified files in that import chain — `backend_app/routers/billing.py` and
   `backend_app/core/safety_config.py` — are **other tabs'** concurrent work.
4. `tests/test_api04_store_failure_after_startup.py` is **untracked** (`??`): it has never been
   committed and has therefore never run in CI, so it carries no prior green record.
5. D5′'s helper is not imported by it (grep: 0 matches), and the CI YAML is not read by pytest.
6. It fails identically whether run alone or after another suite, so it is a consistent
   condition in this working tree rather than an ordering artefact.

**Not fixed here** — it is outside this workstream's scope and belongs to whoever owns that test
or the concurrent `safety_config.py` / `billing.py` changes. Flagged for the coordinator.

One consequence of D5′ worth stating plainly: because that test lives in `tests/`, it is already
in the `unit-tests` selector. Its red state is **independent** of this change and would surface
the moment the file is committed, with or without D5′.

---

## 6. Remaining limitations

1. **No real Redis was exercised.** The decisive assertions — cross-process `SET NX` exclusion,
   TTL readback, server-side `INCR`/`DECR` atomicity, bounded admission — have **not** been
   evaluated against a server. Nothing on this machine could provide one: no `redis-server`, no
   `redis-cli`, no `docker`, no `podman`, nothing listening on 6379/6380/6399, and WSL reports
   `NO_REDIS_SERVER`. Installing one was not authorized. This is the sole reason the status is
   `IMPLEMENTED — REAL-REDIS VERIFICATION PENDING`.
2. **A fourth file activates in CI, and this workstream could not exercise it.**
   `AERORA_TEST_REDIS_URL` is the shared opt-in, so
   `tests/test_webhook_duplicate_delivery_real_redis.py` (6 tests, previously dormant for the
   same reason) will also begin running. That is the intent of the convention, but it is
   **unverified by me** and is the main risk in this change: if those tests have decayed, the
   first PR after this lands goes red. Mitigation if that happens: the failure will be in that
   file only, and reverting one `env:` line restores the previous behaviour.
3. **Divergent unreachable-endpoint semantics.** The quota gates now **fail** on a configured-
   but-dead endpoint; `test_webhook_duplicate_delivery_real_redis.py` still **skips**. Not
   unified, because changing a file outside this workstream's scope to satisfy a stylistic
   consistency is not a minimum change. Worth a follow-up.
4. **The import-time PING costs one round trip** per session for any collection that includes a
   gated module, and up to 2 s when the endpoint is configured but dead. Negligible against a
   90-minute job; noted because it is a new network operation at collection time.
5. **Database index 15 is a convention, not an enforced isolation boundary.** CI's service
   container is ephemeral and exclusive to the job, which is what actually provides isolation.
   No test issues `FLUSHDB` or `FLUSHALL` (**VERIFIED** — no such call exists in any of the
   three files).
6. **`REDIS_URL` is still exported in CI.** Intentional (§2), but it means a future reader can
   still mistake it for the lever. The comment block added to the workflow is there to prevent
   that.

---

## 7. Safety confirmation

| Statement | Status |
|---|---|
| **Live trading remains OFF** | **VERIFIED** — no `LIVE*`/`TRADING*` variable set; no execution or exchange code ran |
| No production Redis, Terraform-managed Redis, or production database accessed | **VERIFIED** — the gate refuses a non-loopback host by default; the only endpoints touched were `127.0.0.1`/`127.0.0.2` on closed ports |
| No Redis data flushed or written | **VERIFIED** — no server was reachable; no `FLUSHDB`/`FLUSHALL` exists in any gated file |
| No infrastructure installed or provisioned | **VERIFIED** — no install command run; `redis-py` was already present |
| No dependency added | **VERIFIED** — no requirements file changed |
| No quota limits, subscription semantics, schema, or trading behaviour changed | **VERIFIED** — no application file edited |
| Atomic lock-release **not** implemented | **VERIFIED** — out of scope by instruction |
| Finding registry / launch scorecard not edited | **VERIFIED** — no write issued to `staging/full_audit_results.json` or `staging/FINAL_LAUNCH_SCORECARD.md` |
| Concurrent work preserved | **VERIFIED** — the workflow diff still contains another tab's entire `provisioning-and-isolation` job, untouched; `tests/conftest.py` carries zero D5′ edits |
| No commit, push, reset, stash, clean, or revert | **VERIFIED** — `HEAD` still `074b772a`; no `git` write command issued |
| No credential or connection URL printed | **VERIFIED** — gate reasons name variables only; the non-loopback test confirmed neither host nor URL appears |

### Final Git state

```
HEAD    074b772ab16cece62cbfca0f2b78497be2ae56d5   (unchanged)
branch  main
git status --short   253 entries (248 at the start of D5'; growth is other tabs)
```

### Exact diff summary

```
 M  .github/workflows/01-pr-check.yml     +170 insertions, 0 deletions
                                           (+28 are D5': one env line + its comment block;
                                            +142 are another tab's provisioning-and-isolation
                                            job, present before D5' and preserved intact)
 ?? tests/real_redis_gate.py              NEW   — the shared gate
 ?? tests/test_quota01_exchange_slot_is_atomic.py       edited (untracked, no git baseline)
 ?? tests/test_quota01_metered_reservation_is_hard.py   edited (untracked, no git baseline)
 M  tests/conftest.py                     NOT CHANGED BY D5' (other tabs' work)
```

Evidence and harness files added under `staging/`: `d5prime_ci_env_selects_tests.txt`,
`d5prime_api04_without_helper.txt`, `d5prime_api04_attribution.txt`,
`probe_d5prime_api04_attribution.py`, `run_d5prime_ci_sim.ps1`,
`run_d5prime_attribution.ps1`, and this report.

---

## 8. Completion

```
D5': IMPLEMENTED — REAL-REDIS VERIFICATION PENDING
```

The configuration defect is fixed and demonstrated: under CI's exact environment the three
real-Redis tests are now **selected** where they were previously **skipped**, a dead endpoint
now **fails** instead of skipping, and a production endpoint can no longer be picked up by
accident. What remains is one CI run to show the assertions passing against the live service —
which cannot be done from here, because no Redis exists in this environment and installing one
was not authorized.

**QUOTA-01 remains OPEN.** Atomic lock-release (D9), the production over-limit population (D3)
and the database backstop (D2) are separate and untouched.
