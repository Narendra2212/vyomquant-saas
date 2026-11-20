# Phase 11B-A — Real-Redis Quota Evidence Closure

**Scope:** Verify whether QUOTA-01's real-Redis integration test actually executes in CI,
diagnose why it skips when Redis is available, and make the smallest safe correction to
produce objective integration evidence — or report BLOCKED_ON_INFRA if no reachable Redis
exists for local verification.

**Verdict:** `CI-CONFIGURED (WILL RUN)` + `LOCAL BLOCKED_ON_INFRA`.
The CI configuration that executes the real-Redis quota test is **already present** in the
working tree (the D5' fix), and the shared gate's state machine was verified objectively.
A local integration run was **not possible** — no Redis is reachable on this host and
installing one is outside the authorized boundary — so **integration verification is NOT
claimed locally**; it is claimed as *CI-configured and gate-correct*, pending the first PR run.

---

## 1. Baseline

| Item | Value |
|---|---|
| HEAD | `074b772a` (`main`, == `origin/main`) |
| Working tree | dirty — concurrent workstreams preserved, nothing reset/stashed/reverted |
| Python | `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1, pytest-asyncio 1.4.0 (STRICT) |
| OS/shell | Windows / PowerShell |
| Local Redis on 6379 | **CLOSED** (`Test-NetConnection 127.0.0.1:6379` → `False`; connect_ex → 10035) |
| `redis` client lib | installed |
| `fakeredis` | NOT installed (and not an acceptable substitute — see §6) |
| `docker` / `redis-server` / `memurai` / `redis-cli` | NONE on PATH |
| WSL | `Ubuntu-24.04` present, but `redis-server`/`redis-cli` NOT installed in it |
| REDIS env vars | none set; `AERORA_TEST_REDIS_URL` unset |

---

## 2. What was inspected (Step 1)

| File | Finding |
|---|---|
| `.github/workflows/01-pr-check.yml` | `unit-tests` job has a healthy `redis:7-alpine` service (ports `6379:6379`, health-checked) AND exports `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` plus retains `REDIS_URL: redis://localhost:6379`. Selector `pytest tests/ -k "not chaos and not load" -m "not frontend_bundle"` **collects** the quota test file. |
| `tests/conftest.py` | Unconditionally sets `os.environ["REDIS_URL"] = ""` at import (forces in-memory `MockRedisClient` for the ~11k-test lane). Does **NOT** touch `AERORA_TEST_REDIS_URL`. |
| `tests/real_redis_gate.py` | New shared gate. Opt-in var = `AERORA_TEST_REDIS_URL` only (`REDIS_URL` fallback removed). Three states: unset→**skip**, bad-shape/non-loopback-without-flag/unreachable→**fail**, reachable-loopback→**run**. PING-based reachability; refuses non-loopback host unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1`; never logs the URL/host/credential. |
| `tests/test_quota01_metered_reservation_is_hard.py` | 6 in-memory logic tests + 1 real-Redis concurrency test (`@real_redis_required` + `require_real_redis()`), K=10 of N=50, mirrors `reserve_quota`'s increment-first-revert, isolated per-test key via `SubscriptionEngine._meter_key`, cleans up with `delete` + `aclose`. |
| `tests/test_quota01_exchange_slot_is_atomic.py`, `tests/test_webhook_duplicate_delivery_real_redis.py` | Both now consume the same shared gate (`real_redis_gate`). These are the three files the workflow comment names as "affected". |
| `subscription_dependencies.py` `_acquire_slot_lock`/`_release_slot_lock` | Ownership-checked release (see §5). |

---

## 3. Diagnosis — why the test skipped, and why it no longer will in CI

**Root cause (D5'):** The real-Redis quota test originally gated on
`AERORA_TEST_REDIS_URL or REDIS_URL` and checked only the *string shape*. Two defects:

1. The `REDIS_URL` fallback was **dead under pytest** — `conftest.py` blanks `REDIS_URL=""`
   at import, before any test module loads. So even though CI's `redis:7-alpine` service was
   healthy and mapped, the test read `''` and **skipped on every PR**. (Measured:
   `staging/p10k_gate_predicate.txt`.)
2. A "configured-but-unreachable" endpoint was indistinguishable from "not asked for" — a
   broken CI Redis could read as a green skip.

**The fix (already present in the working tree):**
- CI `unit-tests` job now exports `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` — a name
  `conftest.py` does **not** blank, mirroring the existing `AERORA_TEST_DATABASE_URL` pattern.
- The gate moved to `tests/real_redis_gate.py`, which consults **only** `AERORA_TEST_REDIS_URL`,
  PINGs to decide reachability, and has three distinct outcomes so a broken service FAILS loudly.

**Net:** In CI, `AERORA_TEST_REDIS_URL=redis://localhost:6379/15` survives conftest, PINGs the
healthy service → gate state `run` → the concurrency test **executes**. If that service were
ever broken/unmapped, the gate returns `fail`, so the test cannot masquerade as a green skip.

---

## 4. Attribution of changes

**No source or workflow edits were made in this phase.** The complete D5' fix was already
present as uncommitted concurrent work and was preserved intact:

```
 M .github/workflows/01-pr-check.yml   (adds AERORA_TEST_REDIS_URL export + rationale)
 M tests/conftest.py                   (REDIS_URL="" blanking retained; AERORA_TEST_* untouched)
?? tests/real_redis_gate.py            (new shared gate)
?? tests/test_quota01_metered_reservation_is_hard.py
?? tests/test_quota01_exchange_slot_is_atomic.py
?? tests/test_webhook_duplicate_delivery_real_redis.py
```

Because the correction already existed and was correct, the smallest safe action was to
**verify** rather than re-implement. No assertion was weakened; no skip was converted to a pass.

---

## 5. Evidence produced (commands, exit codes, counts)

### 5a. In-memory logic (default substrate)
```
.venv\Scripts\python.exe -m pytest tests/test_quota01_metered_reservation_is_hard.py -v --tb=short
=> 6 passed, 1 skipped, 1 warning in 11.91s   (exit 0)
```
- 6 logic tests PASS: exactly-K-of-N under concurrency, sequential fill+refuse, zero-allowance,
  unlimited plan, per-user isolation, full `enforce_quota` → HTTP 403 refusal.
- 1 real-Redis test **SKIPPED** with the correct opt-out reason (`AERORA_TEST_REDIS_URL is not
  set … A skip here is NOT a pass.`) — the legitimate local default.

### 5b. Gate state-machine (objective proof the CI config resolves correctly)
```
.venv\Scripts\python.exe -c "...from tests.real_redis_gate import _classify..."
unset                     => skip
bad-shape                 => fail
ci-value-but-port-closed  => fail      # exactly CI's redis://localhost:6379/15, no server here
non-loopback-no-flag      => fail
```
Interpretation: the only state that yields `run` is a **reachable loopback** endpoint. CI
supplies exactly that (healthy `redis:7-alpine` on `localhost:6379`), so CI resolves to `run`.
A broken CI service would resolve to `fail` — never a false green. The `ci-value-but-port-closed`
case *is* this host (port closed), which is why the local test skips rather than runs.

### 5c. Regressions
None. The 6 logic tests pass; no source/workflow/test behavior was altered this phase.

### 5d. Skips, with reasons
| Test | State | Reason |
|---|---|---|
| `test_concurrent_reservations_are_atomic_against_real_redis` | SKIP (local) / **RUN (CI)** | `AERORA_TEST_REDIS_URL` unset locally; set to a reachable loopback endpoint in CI. |

---

## 6. BLOCKED_ON_INFRA — local integration evidence

A local run of the real-Redis concurrency test was **not possible**:
- No Redis is reachable on `127.0.0.1:6379` (port closed).
- No `docker`, `redis-server`, `memurai`, or `redis-cli` on PATH; WSL has no Redis installed.
- Installing Redis/Docker is outside the authorized boundary for this phase (no authorization given).
- `fakeredis` is **not** an acceptable substitute: the acceptance criterion requires evidence
  against a *real* out-of-process server, and the test's guarantee is specifically Redis INCR/DECR
  **server-side atomicity** — the one property an in-process fake cannot prove the way a real
  server does. (Also, `fakeredis` is not installed.)

**Therefore integration verification is NOT claimed locally.** What IS objectively established:
(a) CI is demonstrably configured to execute the test, and (b) the gate's state machine resolves
every adjacent state correctly, so the test will RUN against CI's healthy service and FAIL loudly
if that service is ever broken.

---

## 7. Lock release — atomicity & ownership-safety

Two distinct mechanisms exist; the question applies to both:

**A. Metered quota counter (`reserve_quota` → `redis_manager.incrby`/`decrby`).**
Hard-limit soundness depends on INCR being atomic. The real-Redis concurrency test exercises
exactly this (increment-first, revert on overflow) against a genuine server. Atomicity is a
property of the real server, which is why the in-memory `MockRedisClient` (synchronous
read-modify-write, no `await` inside `incrby`) can only *model* it and the real-Redis test is
the end-to-end proof.

**B. Exchange-connection slot lock (`_acquire_slot_lock`/`_release_slot_lock`).**
- Acquire: `SET key token NX EX 15` — atomic, token-owned.
- Release: **ownership-checked** (reads the holder token, deletes only on match; `absent`,
  `not-owner`, and `unverified` all decline to delete). This eliminates the classic bare-delete
  defect where a holder whose critical section outlives the TTL would delete a *successor's* lock.
- **Residual, documented, NOT atomic:** release is read-then-compare-then-delete, so a key that
  expires between the read and the delete can still be mis-deleted. The atomic Lua
  compare-and-delete is deliberately not used because this repo's cache facade exposes `eval`
  only as a purpose-built idempotency emulator, not a general interpreter — a Lua release would
  behave differently in dev vs prod. Recorded as residual risk in
  `staging/PHASE10_WSC_COORDINATOR_REVIEW.md`. The window is narrow (one `exchange_keys` read +
  one upsert against a 15s TTL) and a `not-owner` outcome is logged at ERROR as an operator signal.

**Summary:** ownership-safe (common defect eliminated); fully atomic release is a known,
documented, environment-portability-constrained residual — not newly introduced here.

---

## 8. QUOTA-01 status after this phase

- **Metered reservation hard-limit:** logic VERIFIED in-memory (6 tests). Real-Redis atomicity
  **CI-configured to run**; local integration **BLOCKED_ON_INFRA**. QUOTA-01 real-Redis is
  therefore **NOT yet closed on executed integration evidence** — it closes when the CI
  `unit-tests` job runs the test green against the live `redis:7-alpine` service.

### Remaining quota limits — explicitly OPEN
| Limit | Status |
|---|---|
| strategy-count / bot / trading-account / ml-model / exchange-connection / backtests / optimizations / ml_trainings | ENFORCED (wired in `subscription_dependencies.py`; metered ones via `reserve=True`) |
| **order-rate** | **OPEN** — no commercial quota gate found |
| **daily-trade** | **OPEN** — no commercial quota gate found |
| **active-order** | **OPEN** — no commercial quota gate found |

`HardQuotaEnforcer` remains correctly UNWIRED/deprecated.

---

## 9. Constraints honored
Live trading OFF; no real orders/prod creds touched; no Redis/Docker installed; all concurrent
working-tree changes preserved (no reset/clean/stash/revert/overwrite); no assertion weakened;
no skip converted to a pass; QUOTA-01 NOT closed on in-memory alone; no commit/push; exchange
routing, tenant RLS, reconciliation startup, and unrelated work untouched.
