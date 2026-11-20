# PHASE 11B-A4 — EXISTING-CODEBASE ANALYSIS AND SAFE D5′ VERIFICATION PREPARATION

**Analysis only. Nothing was modified, staged, committed, pushed, branched or dispatched.**

**Baseline HEAD:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`, == `origin/main`)
**Date:** 2026-10-10 · **Live trading OFF and NOT CERTIFIED.**

## ▶ RECOMMENDATION (detail in §9)

**Proceed to an explicitly authorized, narrowly scoped CI verification — and treat §3.B as a
separate, acknowledged limitation rather than something the CI run will close.**

The D5′ change needed is **one functional line**. It is cleanly isolable. But the required
test does **not** exercise the production reservation path, so a green run closes less than
QUOTA-01's residual literally asks for. That is a scope decision, not a blocker.

**Two corrections to `PHASE11B_A3_CI_RUN_EVIDENCE.md` are recorded in §7 and §3.B.**

---

## 1. Phase 0 — current state, re-measured

Nothing has changed since A3.

| Item | Value |
|---|---|
| HEAD / `origin/main` | both `074b772a` — **identical** |
| Unpushed commits | **0** |
| Staged files | **22** (other workstreams) |
| Newest `01 PR Check` run | `37346853502`, `2026-10-05T17:13:50Z` — **same as A3; no new runs** |
| D5′ committed since A3? | **No** |
| Eligible CI run? | **No** — 0/14 runs contain the change set |
| API04 fixed by another workstream? | **No** — still reproducible |

### 1.1 Git status of the 11 named files — MEASURED

```
 M .github/workflows/01-pr-check.yml        <-- MIXED: D5' + another workstream (§6)
 M tests/conftest.py                        <-- NOT D5'. Rate-limiter leak guard.
?? tests/real_redis_gate.py
?? tests/test_quota01_metered_reservation_is_hard.py
?? tests/test_quota01_exchange_slot_is_atomic.py
?? tests/test_quota01_enforcement_is_wired.py
?? tests/test_quota01_hard_enforcer_is_deprecated_in_code.py
?? tests/test_redis_fallback_does_not_fake_a_distributed_lock.py
?? tests/test_webhook_duplicate_delivery_real_redis.py
?? tests/test_api03_rate_limit_storage_is_fail_closed.py
?? tests/test_api04_store_failure_after_startup.py
```

### 1.2 Ownership boundaries

| Artefact | Owner | Action permitted here |
|---|---|---|
| 9 untracked `tests/*.py` | **this workstream** | analysis only |
| `01-pr-check.yml` hunk 1 (D5′) | **this workstream** | analysis only |
| `01-pr-check.yml` hunk 2 (`provisioning-and-isolation`) | **another workstream** | **must not ship** |
| `tests/conftest.py` diff | **another workstream** (`REGRESSION_ORDER_ISOLATION.md`) | do not touch |
| 22 staged paths | other workstreams | untouched, verified 22 at exit |
| `tests/test_api04_*`, `backend_app/core/database_pool.py` | **another workstream** | diagnose only (§7) |

---

## 2. Phase 1.A — Real-Redis gate

### **Implemented, integration verification pending**

`tests/real_redis_gate.py`, re-read in full; unchanged since A3.

| Requirement | Finding | Evidence |
|---|---|---|
| Distinct unset / healthy / broken outcomes | **Yes — three** | `_classify()` returns `skip` (opt-in unset), `fail` (set but non-`redis://`, non-loopback, library missing, or PING fails), `run` (reachable) |
| Unsafe remote rejected by default | **Yes** | `_LOOPBACK_HOSTS = {localhost, 127.0.0.1, ::1, [::1]}`; anything else → `fail` unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1` |
| Messages avoid connection details | **Yes** | every reason names the *variable*; three carry "deliberately not echoed". No URL, host or credential in any branch |
| CI reaches the `run` state | **INFERRED, NOT OBSERVED** | `redis://localhost:6379/15` is loopback → permitted; the committed `redis:7-alpine` service exists. Only the PING is unproven. |

The important design property: **a configured-but-dead endpoint FAILS rather than skipping**,
so a broken CI Redis cannot masquerade as green. `real_redis_required` handles "not asked
for"; `require_real_redis()` handles "asked for and broken" — both are needed because a pytest
marker cannot express a failure.

**Local gate state — MEASURED:**
`real-Redis gate: state=skip (AERORA_TEST_REDIS_URL=unset, remote_allowed=False)`
No Redis on `127.0.0.1:6379` (TCP connect). The skip is the gate's legitimate opt-out, and its
own text says *"A skip here is NOT a pass."*

---

## 3. Phase 1.B — Required quota concurrency test

### **Partially implemented** — and this is the most consequential finding in this report.

### 3.A What the test does

`test_concurrent_reservations_are_atomic_against_real_redis` is well-built on its own terms:

| Property | Finding |
|---|---|
| Real client | **Yes** — `redis.asyncio.from_url(redis_url)`, not `MockRedisClient` |
| Concurrency | 50 racers, limit 10, via `asyncio.gather` |
| Assertions | `sum(admitted) == limit` **and** `int(await client.get(key)) == limit` |
| Cleanup | `finally: await client.delete(key)` — **one** uuid-scoped key |
| Destructive cleanup | **None.** No `FLUSHDB`, `flushall`, `keys()` or `scan_iter` anywhere in the corpus |
| Key collision | impossible — key derives from a uuid user **and** a uuid period |
| Error handling | `require_real_redis()` fails on a broken endpoint before any work |
| Collection | `pytest-asyncio mode=STRICT` (measured) + module-level `pytestmark = pytest.mark.asyncio` ✓ |

### 3.B THE GAP: it does not exercise the production path

Traced the full call path.

**Production (`backend_app/core/subscription_engine.py:959` `reserve_quota`):**
```
reserve_quota → redis_manager.incrby(cache_key, amount)
              → redis_manager.expire(...)
              → redis_manager.decrby(cache_key, amount)   # on overflow
```

**The test instead re-implements that logic inline**, against the raw client:
```python
async def _reserve_real():
    # Mirror reserve_quota's increment-first-revert against the real server.
    new_usage = await client.incrby(key, 1)
    if new_usage > limit:
        await client.decrby(key, 1)
        return False
    return True
```

So:

| Claim | Established? |
|---|---|
| Redis server-side `INCR`/`DECR` is atomic under 50-way concurrency | **would be yes** — but that is a property of **Redis**, not of this codebase |
| `SubscriptionEngine.reserve_quota` is atomic against a real Redis | **NO — it is never called by this test** |
| `reserve_quota`'s logic is correct | yes, by the 6 sibling tests — but against `MockRedisClient` |

**Therefore no test in this corpus exercises production `reserve_quota` against a real
Redis.** The real-Redis test uses a real server but bypasses the production code; the
production-code tests run on an in-memory substitute.

**Why it was written this way — MEASURED:** `redis_manager._get_active_cache()` short-circuits
to the mock whenever `DEV_MODE` is set (`backend_app/core/cache/redis_manager.py:728`), and
`tests/conftest.py` sets `os.environ["DEV_MODE"] = "true"`. So `reserve_quota` cannot reach a
real server under pytest as configured. A seam **does** exist —
`SharedRedisManager._set_test_mode()` / `_dev_mode_bypass` (`redis_manager.py:501-515`) — and
no test uses it.

**Consequence for QUOTA-01.** The residual is worded *"no test exercises the quota path
against a real Redis."* A green run of this test satisfies that only on a loose reading. On
the literal reading it does not, because the quota path (`reserve_quota`) is still not the
thing being driven. **This is a correction to A3**, which reported the test as satisfying the
construction criteria without flagging that it bypasses the production function.

---

## 4. Phase 1.C — The nine gated tests

### **Implemented, integration verification pending.** External effects assessed: **SAFE.**

All nine accounted for, and all nine would execute when the gate opens:

| File | Gated tests | Would execute |
|---|---|---|
| `test_quota01_metered_reservation_is_hard.py` | 1 | the required test |
| `test_quota01_exchange_slot_is_atomic.py` | 2 | `..._mutually_exclusive_against_real_redis`, `..._admits_exactly_the_limit_against_real_redis` |
| `test_webhook_duplicate_delivery_real_redis.py` | **6** | all 6 — this file has **zero** non-gated tests |

**Enabling the gate is not assumed safe because they currently skip.** Inspected:

| Risk | Finding |
|---|---|
| Outbound network / payment provider | **None.** No `requests`/`httpx` call. `WEBHOOK_SECRET = "test-webhook-secret-not-a-real-credential"`; Razorpay key id/secret are literal placeholders |
| Application startup | **Avoided.** The handler is invoked **as a function** with a `_FakeRequest`, not through `TestClient` |
| Supabase / DB writes | **Avoided by design.** Events use `payment.authorized`; only `payment.captured` reaches `_process_razorpay_entitlement`, so the settlement path is never entered |
| Credential gate | stubbed via `monkeypatch.setattr(billing, "_validate_keys", ...)`, and a separate test asserts the real gate refuses test keys |
| Redis cleanup | explicit `created: list[str]` of `webhook:razorpay:evt_<uuid>` keys, deleted one by one in the fixture teardown. **No FLUSHDB** |
| Env mutation | the fixture `monkeypatch.setenv("REDIS_URL", ...)` for its own duration only — restored by monkeypatch |

**One residual note:** these six tests have **never executed anywhere**. "Safe by inspection"
is not "known to pass". Their first execution is part of what the CI run buys, and they could
legitimately fail on first contact with a real server.

---

## 5. Phase 1.A/B/C status roll-up

| Item | Status |
|---|---|
| Shared gate, three states, loopback guard, no secret leakage | **Implemented and verified** (static) |
| Gate reaches `run` in CI | **Implemented, integration verification pending** |
| Required test construction (concurrency, cleanup, no destructive ops) | **Implemented and verified** (static) |
| Required test drives production `reserve_quota` on real Redis | **Partially implemented** — §3.B |
| 8 other gated tests | **Implemented, integration verification pending** |
| Workflow carries D5′ cleanly | **Confirmed defect** (mixed file) — §6, remedy available |
| API04 failure | **Confirmed defect, another workstream, NOT a CI blocker** — §7 |
| Real-Redis CI evidence | **Blocked by authorization** — §8 |

---

## 6. Phase 1.D — Workflow ownership and exact isolation method

### **Confirmed defect: the file still mixes two workstreams. Cleanly separable.**

`python staging/a4_hunk_map.py` — MEASURED. At `-U3` the diff is **2 disjoint hunks**:

```
HUNK 1  @@ -183,10 +183,37 @@   classification: D5'
          added 27 lines (1 functional)
          FUNCTIONAL >           AERORA_TEST_REDIS_URL: redis://localhost:6379/15

HUNK 2  @@ -465,6 +492,149 @@   classification: OTHER WORKSTREAM
          added 143 lines (77 functional)
          FUNCTIONAL >   provisioning-and-isolation:
          FUNCTIONAL >     name: Provisioning, Privilege Hardening & Tenant Isolation
          ... 75 more
```

**The entire D5′ change is 1 functional line plus a 26-line explanatory comment.** The hunks do
not interleave and are ~300 lines apart, so **no manual hunk splitting is required**.

### Proposed method (NOT executed)

```bash
# 1. branch from the current HEAD (no commits yet)
git switch -c ci/d5-real-redis-switch

# 2. the 7 files this workstream owns, added whole
git add tests/real_redis_gate.py \
        tests/test_quota01_metered_reservation_is_hard.py \
        tests/test_quota01_exchange_slot_is_atomic.py \
        tests/test_quota01_enforcement_is_wired.py \
        tests/test_quota01_hard_enforcer_is_deprecated_in_code.py \
        tests/test_redis_fallback_does_not_fake_a_distributed_lock.py \
        tests/test_webhook_duplicate_delivery_real_redis.py

# 3. the workflow, HUNK 1 ONLY  ->  answer: y  then  n
git add -p .github/workflows/01-pr-check.yml
```

### Mandatory verification before any commit

```bash
# must print exactly 1 added line, the switch
git diff --cached -- .github/workflows/01-pr-check.yml | grep '^+' | grep -v '^+++' | grep -v '^+ *#'

# must print NOTHING — proves the other workstream's job is not staged
git diff --cached | grep -c 'provisioning-and-isolation'

# must still show the other workstream's job as unstaged and intact
git diff -- .github/workflows/01-pr-check.yml | grep -c 'provisioning-and-isolation'
```

**Deliberately excluded:** `tests/conftest.py` (another workstream's rate-limiter guard) and
`tests/test_api03/api04_*.py`. The API tests are **not** required for the real-Redis evidence,
and committing them would import §7's unrelated failure into this change.

**`git stash` must not be used** at any point — 97 unstaged and 22 staged paths belong to other
workstreams.

---

## 7. Phase 1.E — Independent API04 failure: root cause RESOLVED

### **Confirmed defect, another workstream's file, and NOT a CI blocker.**

`tests/test_api04_store_failure_after_startup.py::test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated`

### 7.1 The chain, with file and line evidence

1. `tests/conftest.py` sets `os.environ["VYOMQUANT_MODE"] = "safe"` and
   `os.environ["DATABASE_URL"] = ""` at import.
2. **`tests/test_api04_store_failure_after_startup.py:49-60`** — autouse fixture `clean_env`
   calls `monkeypatch.delenv` on `ENV`, `ENVIRONMENT`, `DEV_MODE`, **`VYOMQUANT_MODE`**,
   **`AERORA_MODE`**, `REDIS_URL` for **every test in the file**. Conftest's `safe` is removed.
3. **`…api04…:172`** — the test runs `from backend_app.routers import auth, billing`.
4. That chain reaches **`backend_app/core/config.py:49-50`**:
   `DOTENV_PATH = find_dotenv()` then `load_dotenv(DOTENV_PATH)`. The file's own comment
   (lines 24-39) documents that `find_dotenv()` walks up from **`backend_app/`** and so
   *"resolves `backend_app/.env` and never reaches the repository-root `.env` at all."*
5. **`backend_app/.env` contains `AERORA_MODE=paper`** — MEASURED. Because step 2 *deleted* the
   variable, `load_dotenv`'s default `override=False` does not protect it: there is nothing to
   preserve, so the file value is applied.
6. **`backend_app/core/safety_config.py:161` `get_vyomquant_mode`** prefers `VYOMQUANT_MODE`
   (absent), falls back to `AERORA_MODE` → `'paper'`, **emitting the deprecation warning**.
7. **`backend_app/core/database_pool.py:139-141`**: `DATABASE_URL` is `""` (falsy) and
   `_env_mode in ["paper","live"]` → `raise RuntimeError("DATABASE_URL must be provided in
   paper/live mode. SQLite fallback disabled.")`

**Corroborating observation:** the failing test's captured log contains
`DEPRECATION WARNING: 'AERORA_MODE' ... deprecated`. `get_vyomquant_mode` emits that **only**
when `VYOMQUANT_MODE` is `None` *and* `AERORA_MODE` is set. Since step 2 deleted both,
`AERORA_MODE` must have been **re-injected from a file** — and `backend_app/.env` is the only
loadable file in the repository that sets it.

**Enumerated — MEASURED:** of all env files, only two carry a mode key:
`backend_app/.env` → `AERORA_MODE=paper` (loadable), and `.env.example` →
`VYOMQUANT_MODE=paper` (a `.example`, never loaded). The root `.env` has **no** mode key.

### 7.2 Residual uncertainty, stated plainly

I could **not** reproduce step 4's resolution outside pytest: a `python -c` probe that deleted
the mode vars and imported the chain reported
`DOTENV_PATH = C:\aerora_quant_backend_updated_final1\.env` (the **root** file, which has no
mode key) and the mode stayed `'safe'`. `find_dotenv()` is call-stack dependent, and in that
probe `config.py` had already been imported transitively from a different frame.

So: **the chain is identified and every link is independently evidenced except step 4's frame
resolution under pytest**, which rests on `config.py`'s own documented behaviour plus the
observed deprecation warning. High confidence, not direct observation.

### 7.3 This CORRECTS A3 — it is not a CI blocker

**MEASURED:** `backend_app/.env` is **untracked and gitignored** (`.gitignore:18: *.env`), and
**no `.env` file is tracked anywhere** in the repository (`git ls-files | grep '(^|/)\.env$'`
→ empty).

So on a fresh CI checkout there is no `.env` for `find_dotenv()` to find, `AERORA_MODE` is
never injected, `get_vyomquant_mode("safe")` returns `'safe'`, and the `RuntimeError` is not
reached. **The test should PASS in CI.**

> **A3 said the `unit-tests` job "would be RED on this test". That prediction was wrong.** It
> was based on exporting `AERORA_MODE=safe` in the shell and observing the failure persist —
> without recognising that `clean_env` deletes that variable and a gitignored local file then
> supplies `paper` in its place. The failure is a **local-developer-environment artifact.**

### 7.4 Owner boundary — nothing changed

Not modified: `tests/test_api04_store_failure_after_startup.py`,
`tests/test_api03_rate_limit_storage_is_fail_closed.py`,
`backend_app/core/database_pool.py`, `backend_app/core/config.py`, `backend_app/.env`.
No fallback altered, no assertion weakened, no failure converted to a skip.

**For the API04 owner (not actioned here):** the file's `clean_env` fixture removes the two
variables that keep `database_pool` on its SQLite path, and a later import re-populates one of
them from a developer's `backend_app/.env`. The fixture could pin `VYOMQUANT_MODE=safe` instead
of deleting it — but that is their call, since deleting it may be the point of the test.

---

## 8. Phase 2 — Commands executed and outcomes

All read-only or local test execution. Exit codes captured with `cmd /v:on` +
`!ERRORLEVEL!` (delayed expansion — `%ERRORLEVEL%` expands at parse time and is unreliable).

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `git fetch --all --prune` | 0 | remote-tracking refs updated; no local change |
| 2 | `git rev-parse HEAD` / `origin/main` | 0 | identical, 0 unpushed |
| 3 | `git status --short -uall -- <11 files>` | 0 | 2 modified, 9 untracked (§1.1) |
| 4 | `git diff -U0 -- 01-pr-check.yml` | 0 | 3 hunks at U0 |
| 5 | `python staging/a4_hunk_map.py` | **0** | **2 disjoint hunks at U3; D5′ = 1 functional line** |
| 6 | `gh run list --workflow=01-pr-check.yml` | 0 | newest `37346853502`; **no new runs since A3** |
| 7 | `python staging/a3_summarise_runs.py` | 0 | **0/14 runs contain D5′** |
| 8 | `python -m pytest tests/test_quota01_metered_reservation_is_hard.py -rs` | **0** | **6 passed, 1 SKIPPED** (the required test) |
| 9 | TCP connect `127.0.0.1:6379` | 0 | **no local Redis** |
| 10 | gate state probe | 0 | `state=skip (AERORA_TEST_REDIS_URL=unset)` |
| 11 | mode probe with vars deleted | 0 | `'safe'` — §7.2 residual |
| 12 | env-file mode enumeration | 0 | `backend_app/.env` → `AERORA_MODE=paper` |
| 13 | `git ls-files \| grep '(^\|/)\.env$'` | 0 | **empty — no `.env` tracked** |
| 14 | `git check-ignore -v backend_app/.env` | 0 | `.gitignore:18: *.env` |

### Mocked vs real integration — stated explicitly

| Category | Count |
|---|---|
| **Passed, in-memory substrate (`MockRedisClient`)** | 6 in the required file; 66 across the full quota corpus (A3) |
| **Skipped, real-Redis gated** | **9** — including the required test |
| **Passed against a real Redis** | **0** |

**No real-Redis verification is claimed.** The required test did not execute. No
infrastructure was installed; no production Redis, database or exchange was contacted.

### Evidence needed from CI

1. `redis` service container healthy;
2. `AERORA_TEST_REDIS_URL` present in the `Run Unit Tests` step env;
3. gate reaches `run` (absence of the skip reason in the log is the signal);
4. `test_concurrent_reservations_are_atomic_against_real_redis` → **PASSED**, not SKIPPED;
5. the other 8 gated tests each PASSED or FAILED — none SKIPPED;
6. API04 result recorded (expected **PASS** per §7.3 — if it fails, §7's diagnosis is wrong and
   must be revisited).

---

## 9. Phase 3 — Proposed execution plan (NOT executed)

### 9.1 CI acceptance checklist

| # | Check | Pass condition |
|---|---|---|
| 1 | Redis service | container healthy; health-check satisfied |
| 2 | Switch present | `AERORA_TEST_REDIS_URL` in the job env |
| 3 | Gate state | `run` — the skip reason string absent from the log |
| 4 | **Required test** | `test_concurrent_reservations_are_atomic_against_real_redis` **PASSED** |
| 5 | Exchange-slot ×2 | both executed; PASS/FAIL recorded, **not** SKIPPED |
| 6 | Webhook ×6 | all six executed; first-ever execution — failures are findings, not surprises |
| 7 | Skip count | real-Redis skips = **0** |
| 8 | No destructive cleanup | no `FLUSHDB`/`FLUSHALL` in the log |
| 9 | Other workstream absent | `provisioning-and-isolation` **not** in the diff or the run |
| 10 | API04 | **expected PASS** (§7.3). A failure invalidates §7 |
| 11 | Overall job | report separately from items 1-10 |

### 9.2 Minimum separate authorizations required

| # | Action | Needed |
|---|---|---|
| 1 | Create branch `ci/d5-real-redis-switch` from `074b772a` | **yes** |
| 2 | Stage 7 untracked test files | **yes** |
| 3 | Stage `01-pr-check.yml` **hunk 1 only** via `git add -p` | **yes** — plus the §6 verification gate |
| 4 | Commit | **yes** |
| 5 | Push `-u origin` | **yes** |
| 6 | PR, or `gh workflow run 01-pr-check.yml --ref <branch>` | **yes** — `gh` already has `workflow` scope |
| 7 | Read the `unit-tests` log | implied by 6 |

**Not requested, and out of scope:** atomic exchange-slot lock release; order-rate, daily-trade
or active-order quota gates; scorecard or launch-gate edits; any change to
`conftest.py`, API03/API04, `database_pool.py` or `backend_app/.env`.

### 9.3 Safety issues discovered

| Issue | Severity | Note |
|---|---|---|
| Workflow file mixes two workstreams | **high if committed carelessly** | §6 — remedy is one `git add -p`, with three verification commands |
| Required test bypasses production `reserve_quota` | **medium** | §3.B — a green run proves Redis atomicity, not the quota path |
| 6 webhook tests have never run anywhere | low–medium | §4 — inspected safe; first execution may still fail |
| `git stash` would bury 119 other-workstream paths | **high** | must not be used |
| A worker-start timeout exits 0 and reports "no tests" | medium | a run reporting no tests is **BLOCKED**, never a pass |

---

## 10. Completion recommendation

### **Proceed to an explicitly authorized, narrowly scoped CI verification.**

Justification:

- The implementation **already exists** and is correctly built. Nothing should be implemented a
  second time: the gate, the three states, the loopback guard, the secret hygiene, the scoped
  cleanup and the nine gated tests are all present.
- The change required is **one functional line** in a workflow, plus adding 7 untracked files.
- The isolation problem is **solved and verified**: 2 disjoint hunks, `git add -p` → `y`, `n`,
  with three mechanical checks proving the other workstream's job stayed out.
- The previously feared prerequisite — the API04 failure — is **not a prerequisite**. §7 shows
  it is a local `backend_app/.env` artifact that cannot exist on a runner, which **corrects
  A3's prediction** that the job would be red.

**With one explicit caveat that must not be lost:** per §3.B, a green run does **not** close
QUOTA-01 on its literal wording, because the required test never calls
`SubscriptionEngine.reserve_quota`. It proves Redis's `INCR`/`DECR` is atomic — true, worth
having, and not the same claim.

Closing QUOTA-01 properly needs one of:

- **(a)** extend the real-Redis test to drive production `reserve_quota` through the existing
  `SharedRedisManager._set_test_mode()` / `_dev_mode_bypass` seam
  (`redis_manager.py:501-515`) — a **new, separately authorized** change; or
- **(b)** an explicit, recorded decision that Redis-level atomicity plus mock-level logic
  coverage is sufficient.

**Do not mark QUOTA-01 closed on the strength of the local mocked passes, and do not mark it
closed on a green run of this test alone.** Live trading remains **OFF and NOT CERTIFIED**.

---

## 11. Confirmation: no state was changed

| Action | Performed? |
|---|---|
| branch / stage / commit / push / PR / workflow dispatch | **NO** |
| reset / clean / stash / revert / checkout of tracked files | **NO** |
| application code, tests, or workflow edited | **NO** |
| scorecard or launch-gate status touched | **NO** |

Read-only remote calls: `git fetch --all --prune`, `gh run list`. Verified at exit: HEAD ==
`origin/main` == `074b772a`, 0 unpushed, staged count **22**, local branch `main`.

Files added — analysis artefacts only, under `staging/`:

```
staging/PHASE11B_A4_CODEBASE_ANALYSIS.md   this report
staging/a4_hunk_map.py                     hunk classifier (§6) — read-only
staging/a4_runs.json                       re-captured run metadata (§1)
staging/a4_metered.log                     §8 run 8 raw evidence
```
