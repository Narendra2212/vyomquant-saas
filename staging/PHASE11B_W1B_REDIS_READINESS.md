# Phase 11B / Workstream 1B — Real-Redis lane readiness

**Scope.** Deliverable 4 of 5. Whether the real-Redis integration lane can run, and
what it would prove if it did. Nothing was installed, no server was started, and no
Redis endpoint was contacted — there is none to contact.

**Verdict up front: BLOCKED, for an environmental reason only.** The gate is correctly
built, the three gated suites are correctly wired, and CI is correctly configured. The
single missing element is a Redis server on this machine, and installing one is not
authorized here — approval request **C**.

W1A reported this state. Three of the files involved were modified by other agent
sessions after that reading (`tests/real_redis_gate.py` at 13:34,
`tests/test_quota01_*.py` at 13:51, `tests/test_webhook_duplicate_delivery_real_redis.py`
at 15:35), so everything below was re-measured.

---

## 1. Environment — nothing configured

`staging/probe_p11b_w1b_redis.py`, log `staging/p11b_w1b_redis.txt`.

| Variable | State |
| --- | --- |
| `AERORA_TEST_REDIS_URL` | **UNSET** |
| `AERORA_TEST_REDIS_ALLOW_REMOTE` | UNSET |
| `REDIS_URL` | UNSET |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | UNSET |
| `CELERY_BROKER_URL` | UNSET |

### 1.1 No server, by real TCP connect

Not a ping — an actual socket connect to each candidate port:

```
127.0.0.1:6379   timeout
127.0.0.1:6380   timeout
127.0.0.1:6381   timeout
127.0.0.1:6399   timeout
127.0.0.1:7379   timeout
open ports: NONE
```

### 1.2 No server binary, and no container runtime

| Candidate | Result |
| --- | --- |
| `redis-server`, `redis-cli` | not on PATH |
| `valkey-server` | not on PATH |
| `memurai`, `memurai-server` | not on PATH |
| `docker`, `podman` | **not on PATH** |
| `wsl` | present at `C:\Windows\system32\wsl.EXE` |
| `redis-server` inside WSL | **`NO_REDIS_SERVER`** |
| `staging/redis/redis-server.exe` | **does not exist** |
| `staging/redis/` directory | **does not exist** |
| `C:\Program Files\Redis\redis-server.exe` | does not exist |
| `C:\Program Files\Memurai\memurai.exe` | does not exist |

`DATABASE_PROVISIONING_RUNBOOK.md` documents starting Redis with
`.\staging\redis\redis-server.exe --port 6379 --appendonly no`. **That binary is not
present**, so the documented procedure cannot be followed as written. There is no
container runtime to substitute, and no Redis inside WSL.

The Python client **is** available: `redis-py 5.0.8`.

---

## 2. The gate — correctly built

`tests/real_redis_gate.py`, 8003 bytes, mtime `13:34:04` (rewritten by another session
after W1A's reading).

| Constant | Value |
| --- | --- |
| `OPT_IN_VAR` | `'AERORA_TEST_REDIS_URL'` |
| `ALLOW_REMOTE_VAR` | `'AERORA_TEST_REDIS_ALLOW_REMOTE'` |
| `_LOOPBACK_HOSTS` | `{'localhost', '127.0.0.1', '::1', '[::1]'}` |
| `_PING_TIMEOUT_SECONDS` | `2` |
| `REAL_REDIS_AVAILABLE` | `GATE_STATE == 'run'` |

### 2.1 No `REDIS_URL` fallback

```python
def redis_url() -> str:
    """The configured test endpoint, or ``''``. Never logged by this module."""
    return (os.environ.get(OPT_IN_VAR) or "").strip()
```

`redis_url()` reads **only** `AERORA_TEST_REDIS_URL`.

> **Correction to my own measurement.** A first revision of my probe answered this by
> scanning the file for `REDIS_URL` and reported a fallback **YES**. That was wrong. The
> module's docstring discusses `REDIS_URL` at length *precisely to explain why the
> fallback was removed*, and a comment on line 60 repeats it. The probe now resolves it
> from the body of `redis_url()` by AST: `reads = ["OPT_IN_VAR='AERORA_TEST_REDIS_URL'"]`
> → **no fallback**.

This matters because `tests/conftest.py:13` still executes
`os.environ["REDIS_URL"] = ""` unconditionally at import. A gate reading `REDIS_URL`
could therefore never fire under pytest — which was the original D5′ defect. The opt-in
variable is untouched by conftest, so the design holds.

### 2.2 Three outcomes, and a broken endpoint FAILS

`_classify()` returns:

| Condition | State | Rationale, from the source |
| --- | --- | --- |
| variable unset | **skip** | "Real-Redis coverage is opt-in… **A skip here is NOT a pass.**" |
| set but not `redis://`/`rediss://` | **fail** | "asked for, so this is a misconfiguration rather than an absence" |
| non-loopback host without `ALLOW_REMOTE=1` | **fail** | "a real-Redis test must not be able to reach a shared or managed instance by accident" |
| `redis` client not importable | **fail** | configured but unusable |
| `PING` fails or returns falsey | **fail** | "a configured endpoint that cannot be reached is a **BROKEN TEST SERVICE, not an absent one**… otherwise a dead Redis in CI would look like a green run" |
| reachable | **run** | — |

The gate is exposed through two paired objects, because a pytest marker cannot express
a failure:

```python
real_redis_required = pytest.mark.skipif(GATE_STATE == "skip", reason=GATE_REASON)

def require_real_redis() -> str:
    if GATE_STATE == "fail":
        pytest.fail(GATE_REASON)
    if GATE_STATE != "run":
        pytest.skip(GATE_REASON)
    return redis_url()
```

Endpoint values, hosts and credentials are never echoed in any reason string — only
variable names and conditions.

### 2.3 A divergence W1A recorded is now RESOLVED

W1A/WSK recorded that the webhook suite **skipped** on an unreachable endpoint while the
quota suites **failed** — an inconsistency that let a dead Redis pass as a skip in one
file. All three suites now use the same two-part pattern:

| Suite | Marker | `require_real_redis()` |
| --- | --- | --- |
| `test_quota01_exchange_slot_is_atomic.py` | `@real_redis_required` on both tests (L641, L700) | L654, L713 |
| `test_quota01_metered_reservation_is_hard.py` | `@real_redis_required` (L228) | L238 |
| `test_webhook_duplicate_delivery_real_redis.py` | `pytestmark = [real_redis_required, …]` (L99) — module level, so a new test in the file cannot forget it | L119, in an autouse fixture |

Unreachable-endpoint behaviour is now uniform: **FAIL**, in all three. This was fixed by
another session's 13:34 rewrite, not by W1B.

---

## 3. What the lane would actually prove

| Suite | Reaches real Redis through… | Evidence quality |
| --- | --- | --- |
| `test_webhook_duplicate_delivery_real_redis.py` | `redis_manager._bypass_dev_mode(True)`, then **asserts the cache is neither `None` nor a `MockRedisClient`** | **Strongest.** It exercises the production facade and proves it reached a real server. This is the suite that would close **P11-U04's Lua half**. |
| `test_quota01_metered_reservation_is_hard.py` | asserts not `MockRedisClient` | Good |
| `test_quota01_exchange_slot_is_atomic.py` | injects its **own `_RealCache`** over `sd.redis_manager`; asserts cache is not `None` | **Weaker, and this must be stated.** It proves the *algorithm* is atomic against a real server, but it **bypasses the production `redis_manager` facade** — so it cannot show that the facade, with its fail-soft `get()`/`incr()`, behaves correctly against real Redis. QUOTA-01's fail-soft concern is not addressed by this suite. |

### 3.1 Key-space hygiene

No `FLUSHDB` or `FLUSHALL` appears anywhere in the three suites. Keys are namespaced
(`q01-slot-*`, `q01-slot-e2e-*`, `q01-real-*`) and clients are closed in `finally`. CI
points at **database index 15** (`redis://localhost:6379/15`), not 0. So the lane is safe
against a dedicated local instance, and would not be safe against a shared one — which
is exactly what the loopback refusal and `ALLOW_REMOTE` flag exist to prevent.

---

## 4. CI configuration — correct, and unreachable

`.github/workflows/01-pr-check.yml`:

| Line | Content |
| --- | --- |
| 4–5 | `on:` / `pull_request:` — **`pull_request` only** |
| 136–137 | `unit-tests` job has service `redis: image: redis:7-alpine` |
| 215 | `REDIS_URL: redis://localhost:6379` — **retained deliberately**; `test_api03_*`/`test_api04_*` assert on how the application behaves when it is present |
| 216 | `AERORA_TEST_REDIS_URL: redis://localhost:6379/15` — the D5′ change, present |
| 258–259 | `paper-mode-tests` also has a `redis:7-alpine` service |
| 306 | `paper-mode-tests` exports `REDIS_URL` only — **no `AERORA_TEST_REDIS_URL`** |

Two consequences:

1. **The gate would fire in `unit-tests`** — the service is healthy and the opt-in
   variable is exported to that job.
2. **The gated suites skip in `paper-mode-tests`**, which has a Redis service it cannot
   switch them on with. Not a defect (they are not that job's subject), but it means a
   green `paper-mode-tests` is not real-Redis evidence.

### 4.1 Why CI cannot be used to close this

| Blocker | Detail |
| --- | --- |
| **The workflow triggers on `pull_request` only.** | No `workflow_dispatch`, no `push`. The lane cannot be invoked without opening a pull request. |
| **Every D5′ path is uncommitted.** | `tests/real_redis_gate.py` and all three gated suites are `??`. A pull request would require committing and pushing, which W1B is not authorized to do. |
| **`Unit & Integration Tests` is already red.** | Last 10 runs: **0 successes**. Newest run `37346853502` (SHA `6c5660a1`) → **39 FAILED**. A new run would not be attributable to the Redis lane. This is **A3-02**, still open. |
| **No prior run has ever executed these tests.** | 0 log mentions of any gated test name across the 10 runs examined in Phase 11B-A2. |

So the CI path is blocked by authorization (commit + push + PR), not by capability.

---

## 5. Decision

| Item | Decision |
| --- | --- |
| **Real-Redis lane** | **BLOCKED — environmental.** No server, no binary, no container runtime, no Redis in WSL, and no endpoint configured. `redis-py 5.0.8` is present, so only the server is missing. |
| **Gate correctness** | **PASS.** Opt-in only, no `REDIS_URL` fallback, loopback-refusing, PING-verified, three distinct outcomes, broken-endpoint→FAIL uniform across all three suites, no value ever echoed. |
| **CI configuration** | **PASS, unreachable.** `AERORA_TEST_REDIS_URL` is exported to the job with the Redis service. Blocked on commit/push/PR authorization and on A3-02's 39 pre-existing failures. |
| **P11-U04 (Lua/atomicity)** | **STILL OPEN.** The webhook suite would close its Lua half, and it cannot run. |
| **QUOTA-01** | **STILL OPEN**, and note §3 — even a green lane would leave the facade's fail-soft path unexercised, because the slot suite injects its own cache. |

### 5.1 What would unblock it (approval C)

Minimum viable, in preference order:

1. **A local Redis on loopback, dedicated, index 15.** Then
   `$env:AERORA_TEST_REDIS_URL = "redis://127.0.0.1:6379/15"` and the lane runs. Needs
   an install, hence approval C. Options: the runbook's `staging/redis/redis-server.exe`
   (absent — would be a download), Memurai (Windows service), `apt install redis-server`
   inside WSL, or a container runtime (also absent).
2. **CI.** Needs commit + push + PR authorization, and A3-02 resolved first so the result
   is attributable.

**Not acceptable as a substitute:** pointing `AERORA_TEST_REDIS_URL` at any shared,
staging or managed instance. The gate would refuse it as non-loopback unless
`AERORA_TEST_REDIS_ALLOW_REMOTE=1` were also set, and setting that flag to reach a shared
instance would defeat the control. The flag exists for a dedicated remote test service,
and there is none.

---

## 6. Findings

| ID | Sev | Finding |
| --- | --- | --- |
| **W1B-15** | **P1** | The real-Redis lane has never executed, anywhere. No server exists locally and CI has never run the gated tests. `P11-U04` and the facade half of `QUOTA-01` therefore rest on zero integration evidence. The blocker is environmental and authorizational, not technical. |
| **W1B-16** | **P2** | `DATABASE_PROVISIONING_RUNBOOK.md` documents `.\staging\redis\redis-server.exe`, but neither the binary nor `staging/redis/` exists. The documented procedure cannot be followed. The runbook should either carry the acquisition step or stop naming a path that is not there. |
| **W1B-17** | **P2** | `test_quota01_exchange_slot_is_atomic.py` injects its own `_RealCache` over `sd.redis_manager`, bypassing the production facade. Its real-Redis result would evidence the *algorithm*, not the facade. Since QUOTA-01's open concern is the facade's fail-soft `get()`/`incr()`, a green run of this suite must not be read as closing it. |
| **W1B-18** | **P3** | `paper-mode-tests` runs a `redis:7-alpine` service without exporting `AERORA_TEST_REDIS_URL`, so the gated suites skip there. Harmless, but the job pays for a service it cannot use. |

### 6.1 W1A re-checks

| W1A claim | W1B re-measurement |
| --- | --- |
| `AERORA_TEST_REDIS_URL` / `REDIS_URL` unset | **Confirmed** |
| 6379/6380/6399 closed | **Confirmed**, and extended to 6381/7379 — all closed |
| No `redis-server`/`redis-cli`/`docker`/`podman`/`memurai` | **Confirmed**, and `valkey-server` also absent |
| WSL present but `NO_REDIS_SERVER` | **Confirmed** (needed a 120s timeout; a 30s probe timed out inconclusively) |
| `redis-py 5.0.8` | **Confirmed** |
| `staging/redis/` absent | **Confirmed** |
| Gate reads `AERORA_TEST_REDIS_URL` with no `REDIS_URL` fallback | **Confirmed** by AST of the function body |
| Webhook suite SKIPS on unreachable while quota suites FAIL | **No longer true — RESOLVED.** All three now FAIL uniformly, after another session's 13:34 rewrite. |

---

Live trading: OFF / NOT CERTIFIED · Production database: NOT ACCESSED · Shared PostgreSQL integration lane: NOT RUN without explicit approval · Real-Redis integration: UNVERIFIED · TENANT-01: OPEN · QUOTA-01: OPEN · Skipped tests: never counted as passes.
