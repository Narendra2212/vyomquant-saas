# REGRESSION ORDER ISOLATION — the 8 order-dependent failures, root-caused and fixed

**Status: FIXED.** All 8 had one root cause. It is **test-only contamination** via an
application module-level singleton combined with a module re-import — not a product
defect. The fix is a test-isolation guard in `tests/conftest.py`.

| | |
|---|---|
| Failing file | `tests/test_task_6_1_backtest_execute.py` |
| In the full suite | **8 failed**, 25 passed (at 80–81% of the ordering) |
| Run alone | **33 passed** |
| At clean `HEAD` in a worktree | **33 passed** |
| Deterministic repro | 3 named files, then the target → **exactly 8 failed** |
| After the fix, same order | **91 passed**, 0 failed, and faster (33s vs 79s) |

---

## 1. The exact failing tests

All eight, and every one of them an HTTP **429** where a specific status was expected. The
two `KeyError`s are the same thing: the 429 body carries neither `backtest_id` nor `status`.

| # | Test | Line | Expected | Got |
|---|---|---|---|---|
| 1 | `TestArchivedStrategiesAreRefused::test_it_answers_409_naming_the_operation_and_the_timestamp` | 549 | 409 | 429 |
| 2 | `TestArchivedStrategiesAreRefused::test_a_row_with_no_archival_column_is_treated_as_active` | 577 | 200 | 429 |
| 3 | `TestOwnership::test_another_tenant_gets_404_and_nothing_runs` | 591 | 404 | 429 |
| 4 | `TestOneSynchronousResponse::test_it_returns_the_persisted_result_directly` | 616 | body | `KeyError: 'backtest_id'` |
| 5 | `TestOneSynchronousResponse::test_it_carries_no_job_id_to_poll` | 627 | body | `KeyError: 'status'` |
| 6 | `TestRefusalsFromTheRuntime::test_a_version_that_no_longer_compiles_names_the_version` | 765 | 422 | 429 |
| 7 | `TestRefusalsFromTheRuntime::test_a_version_declaring_no_market_is_refused_not_substituted` | 781 | 422 | 429 |
| 8 | `TestRefusalsFromTheRuntime::test_an_unexpected_failure_is_still_a_500_with_its_code` | 793 | 500 | 429 |

They are the tests from `TestArchivedStrategiesAreRefused` onwards — i.e. the *later* tests
in the file. The earlier 14 pass. That shape is the first clue: something accumulates
across the file's own requests.

## 2. What the 429 actually was

Captured by running a temporary diagnostic **after** the target file, so the request was
byte-identical to the failing one:

```json
{"error":{"code":"MARKETPLACE_RATE_LIMITED",
          "message":"Too many requests were made in a short time. Please wait a moment and try again."},
 "request_id":"eec87cdbb950406aa1d682e15e18e1bb"}
```

`MARKETPLACE_RATE_LIMITED` is `core/rate_limit_keys.py:170` — the structured-error handler
for **slowapi's `RateLimitExceeded`**. So the refusal is the rate limiter, not the backtest
handler.

> Two other 429 sources were considered and ruled out by this body:
> `core/rate_limit_middleware.py` (which answers `{"error":"Rate limit exceeded","limit_type":…}`)
> and `core/subscription_middleware.py`. Neither produced it.
>
> A *third* accumulating resource was found and ruled out on the way: a standalone probe
> hit **403 `BACKTEST_LIMIT_REACHED`** (`current:10 limit:10 resource:backtests`) after
> eight requests — the monthly entitlement ledger. That is already cleared per test by
> conftest's existing `_entitlement_state_is_per_test`, which is why it does not appear in
> the suite and why the standalone probe saw it.

## 3. The preceding state that causes it

`backend_app/core/rate_limit.py` builds **one** module-level limiter:

```python
limiter = Limiter(key_func=get_remote_address, storage_uri=config.storage_uri)
```

Three facts combine:

1. Every router is decorated `@limiter.limit("N/minute")` **at that router's import time**,
   so each route closes over the limiter object that existed then.
2. `backend_app/main.py:593` does `app.state.limiter = limiter`.
3. **Three test files re-import or reload that module**, each creating a brand-new
   `Limiter` with its own `MemoryStorage`:

| File | Position in the suite | What it does |
|---|---|---|
| `tests/test_api03_rate_limit_storage_is_fail_closed.py` | 57% | `sys.modules.pop(...)` + `import_module` (lines 360, 364, 367, 369) |
| `tests/test_rate_limiter_fails_closed_in_production.py` | 57% | `sys.modules.pop(MODULE, None)` in `_reload` (lines 116, 340, 344) |
| `tests/test_rate_limit_backend.py` | 57% | `importlib.reload(...)` (lines 58, 66, 75, 110, 114, 129, 140) |

`test_task_6_1_backtest_execute.py` runs at **80%** — after all three.

### Why the file's own defence did not work

The target file already carries a reset, autouse and function-scoped:

```python
@pytest.fixture(autouse=True)
def clear_rate_limit():
    """The route's 10/minute limit is real and shared; no test may inherit another's count."""
    from backend_app.core.rate_limit import limiter
    limiter.reset()
```

It ran. The problem is **which object it reset**. After a re-import,
`from backend_app.core.rate_limit import limiter` yields the *new* module's limiter, while
the app's routes are still enforcing on the old one. The fixture cleared an unused limiter.

### Measured, not inferred

`staging/probe_order_isolation_429.py` — 6/6 checks, `VERDICT=HYPOTHESIS_CONFIRMED`:

1. the app holds the limiter the routers were decorated with;
2. `sys.modules.pop` + re-import produces a **different** limiter object;
3. the app **still** holds the original after the re-import;
4. so `from ...rate_limit import limiter` now yields the wrong object for a reset;
5. `importlib.reload` rebinds it too;
6. the two limiters have **separate storage**.

`staging/probe_order_isolation_429_body.py` then found **three** live `Limiter` instances
mid-suite and ranked the candidate fixes by experiment:

```
live Limiter instances: 3
  [0] id=0x152679888c0 is_module=True  is_app_state=True   storage=MemoryStorage@0x152666e0920
  [1] id=0x1524f57d370 is_module=False is_app_state=False  storage=MemoryStorage@0x1524f95c230
  [2] id=0x1526a051850 is_module=False is_app_state=False  storage=MemoryStorage@0x1526a0518b0

(1) with the autouse fixtures only           -> 429
(2) after resetting app.state.limiter        -> 429
(3) after resetting EVERY live Limiter       -> 409   <-- correct
(4) after clearing every storage container   -> 409
```

So the object doing the counting is an **orphan**: reachable from neither the module
attribute nor `app.state`.

## 4. Classification

| Question | Answer |
|---|---|
| Shared global state? | Yes — `core/rate_limit.limiter`, a module-level singleton. |
| Environment mutation? | No. |
| Database contamination? | No. |
| Filesystem / Redis state? | No. |
| Monkeypatch leakage? | No — the re-imports are explicit, not patches. |
| Event-loop state? | No. |
| Import / module-cache state? | **Yes — this is the mechanism.** `sys.modules.pop` and `importlib.reload` leave orphaned instances that the live routes keep enforcing on. |
| Verdict | **Test-only contamination**, via an application module-level singleton plus module-cache mutation. |

**Why it is not a product defect.** Nothing in production re-imports
`backend_app.core.rate_limit`; the module is imported once per process. Each real caller
has its own rate-limit key and the windows roll. What the suite does that production does
not is re-import a module that owns a singleton, and share one client address across
~13,000 requests in one process. The limiter was doing exactly its job.

**Who owns it.** The test suite. No application change was made.

## 5. The fix

`tests/conftest.py` — an autouse, function-scoped guard beside the file's existing
shared-singleton and entitlement guards:

```python
@pytest.fixture(autouse=True)
def _rate_limit_counts_are_per_test():
    _reset_bound_rate_limiters()
    try:
        yield
    finally:
        _reset_bound_rate_limiters()
```

`_reset_bound_rate_limiters()` resets **every limiter that could be enforcing**: the ones
discovered in the app's route closures, plus `app.state.limiter`, plus the module's current
one. Discovery is cached in a `WeakSet` and re-run only when the fingerprint
`(id(module limiter), id(app), route count)` changes.

### Two wrong turns, both recorded because they shaped the fix

**`gc.get_objects()` raised `MemoryError`.** The obvious way to find orphaned instances is
a gc sweep. On this process — the full app plus Hypothesis — the returned list alone
exhausted memory, and the suite died with
`ERROR tests/test_task_6_1_backtest_execute.py - MemoryError` during collection. Rejected.

**Where slowapi actually keeps the limiter.** `staging/probe_find_limiters_cheaply.py`
measured it: walking `app.routes[*].endpoint.__closure__` (and the `__wrapped__` chain)
found **2 limiters across 351 route endpoints**, and
`endpoint attributes mentioning 'limit': []` — so the closure is the only place it lives.
That is cheap and reaches exactly the objects that can refuse a request.

**Keying the re-scan on the limiter alone was not enough.** The first version keyed only on
`id(module limiter)`. It scanned once, on the very first test, *before* anything had
imported `backend_app.main` — found no routes, recorded the id, and then never re-scanned.
The guard was inert for the whole session and the 8 failures survived it unchanged. The key
now includes the app's identity and its route count, because the app is built incrementally
and a scan taken too early sees none of it.

## 6. Verification

| Scope | Before | After | Artefact |
|---|---|---|---|
| Deterministic repro: the 3 re-importing files, then the target | **8 failed**, 83 passed | **91 passed**, 0 failed | `staging/f_P6J4.txt` |
| The target file alone (control) | 33 passed | 33 passed | `staging/f_P6J2.txt` |
| Diagnostic request after the target, in the repro order | 429 `MARKETPLACE_RATE_LIMITED` | — | `staging/f_P6H7.txt` |

The repro order also got **faster** (79s → 33s), because a 429 short-circuit is cheaper
than the work the test actually wanted and was being counted as a failure.

> The temporary diagnostic module used to capture the 429 body and enumerate the live
> limiters (`tests/test_zzz_diag_order_isolation.py`) has been **deleted**. Its findings are
> reproduced by the two `staging/probe_order_isolation_*.py` scripts, which remain.

## 7. What was NOT done

* **No assertion was changed.** All eight tests still expect 409 / 200 / 404 / 422 / 500
  and still assert the same bodies. Nothing was marked skip or xfail.
* **The three re-importing files were left as they are.** Their re-imports are the point of
  those tests — they verify the limiter's *construction* under different environments, and
  that requires a fresh module. Forbidding the re-import would delete the control. The
  isolation belongs in the guard, not in a weakened test.
* **`core/rate_limit.py` was not changed.** The singleton is correct for production; making
  it re-import-safe would be designing for the test harness.

## 8. Residual

A re-import leaves an orphaned `Limiter` whose storage is only reachable through the route
closures. The guard handles it, but the underlying hazard is generic: **any module-level
singleton in `backend_app` that a test re-imports will leave an enforcing orphan behind.**
`tests/conftest.py` now guards three of them — the shared Redis manager, the entitlement
ledger and the rate limiter. Recorded as **TEST-03 (P3)**: a conftest guard is a cure, not a
prevention, and the general fix would be for tests to restore `sys.modules` after a
deliberate re-import.

## 9. TEST-04 — the residual, confirmed by a tenth instance found later in the same phase

§8 predicted that a conftest guard is a cure and not a prevention. A tenth order-dependent
failure turned up while verifying an unrelated fix, and it is the same family through a
**different mechanism**, which is exactly the shape §8 warned about.

`tests/test_api03_rate_limit_storage_is_fail_closed.py::test_the_module_exposes_the_decision_it_actually_applied`

| Order | Result |
|---|---|
| that file alone | **34 passed** |
| `test_rate_limiter_fails_closed_in_production.py` then `test_api03_…` | **1 failed**, 51 passed |
| `test_api03_…` then `test_rate_limiter_fails_closed_in_production.py` — alphabetical, i.e. the repository's own collection order | **52 passed** |

The reloading file creates a new `RateLimitConfig` **class object**, while the asserting file
holds the pre-reload class from its own module import. So
`isinstance(rate_limit.config, RateLimitConfig)` is `False` against a perfectly well-formed
`RateLimitConfig(env='testing', …)` — the failure message shows exactly that, an object
printing as a `RateLimitConfig` that "is not" one.

**Not the same mechanism as the eight.** Those were a *live `Limiter` instance* still bound to
route closures and still enforcing. This is *class identity* after a reload, which enforces
nothing and breaks only an `isinstance` check.

**The guard was deliberately not extended to cover it.** The only way a conftest guard could
fix class identity is by restoring `sys.modules` after each reload — which would defeat the
tests that perform the reload on purpose, the same reason §7 gives for not forbidding the
re-imports.

**Not fixed**, for two reasons: it does not occur in the repository's own collection order
(verified: 52 passed alphabetically), and the fix belongs in the asserting test — compare the
dataclass's fields or its `__name__` rather than its identity.

Evidence: `staging/raw_P8D4.txt` (the failure, inside a targeted batch),
`staging/raw_P8E5.txt` (alone: 34 passed; polluting order: 1 failed),
`staging/raw_P8F6.txt` (alphabetical order: 52 passed).

---

# PHASE 7 ADDENDUM — ORDER ISOLATION OF THE NEW TESTS

> Everything above is the Phase 6 record, preserved as written. Phase 6 found that the
> eight order-dependent failures traced to orphaned `slowapi` `Limiter` instances —
> module-level mutable state shared across suites. Phase 7 added 78 tests across five new
> files, and every one of them touches module-level or process-level state. This section
> records what was done about that, because "it passed in isolation" is not the standard
> this document holds.

---

## Four shared-state hazards introduced, and how each is contained

### 1. `connection_engine._exchange_pool` — a module-level dict

`tests/test_exchange_safety_fix.py`'s new `controlled_venue` fixture **snapshots and
restores** it:

```python
saved_pool = dict(ce._exchange_pool)
ce._exchange_pool.clear()
...
finally:
    ce._exchange_pool.clear()
    ce._exchange_pool.update(saved_pool)
```

A leaked entry would make a later test see a pooled client it did not create — exactly the
Phase 6 `Limiter` shape. Restoring rather than merely clearing matters because another suite
may legitimately have pooled something.

The venue class substitution is made on `connection_engine.ccxt` (the module's own
reference) rather than by mutating `ccxt.pro`, so a `monkeypatch` teardown fully reverses it
and no other module that imports `ccxt.pro` is affected while the test runs.

### 2. `ExecutionFlags` / `SystemMode` — class objects other modules hold by reference

The ENV-04 suite needed to prove that `safety_config.ExecutionFlags` and
`feature_flags.ExecutionFlags` cannot disagree. The obvious way is
`importlib.reload` under each flag value — and that was **rejected**, because reloading
`safety_config` mints a new `SystemMode` enum and a new `ExecutionFlags` class mid-session
while `main.py` and others still hold the old ones. Any later `isinstance` or identity
comparison against the old enum would then fail, and it would fail *somewhere else*, in a
test that looks unrelated.

Replaced with an AST assertion that both class bodies assign from
`production_router_enabled()`, plus a value assertion under the session's current
configuration. The property ("one definition, no drift") is proven without mutating a class
other modules alias.

**One reload remains, deliberately.** `tests/test_env03_placeholder_secrets_are_invalid.py::test_reloading_config_does_not_change_the_resolved_dotenv_path`
reloads `core.config` and reloads it again in a `finally`. It follows the precedent already
set by `tests/test_jwt_secret_is_production_grade.py`, whose `settings_for` fixture reloads
that module on every call. `Settings` resolves `os.getenv` in its class body, so there is no
other way to vary its inputs — and `config` exports no enum or sentinel that another module
compares by identity, which is what makes it safe where `safety_config` is not.

### 3. The `SafetyConfig` logger — global logging configuration

`test_an_unrecognised_value_disables_and_says_so` attaches **its own handler** to
`logging.getLogger("SafetyConfig")`, records the level, and restores both in a `finally`,
instead of using `caplog`. `caplog` depends on propagation to the root logger and on
whatever log configuration an earlier test in the session left behind; this repository
configures logging at import in several modules. A direct handler makes the assertion
independent of all of it.

### 4. `_ControlledVenueClient` — a class attribute used as a fixture channel

`instances` and `raise_on_load_markets` are class attributes, reset at both ends of the
fixture. The one test that needed a slow `load_markets` sets it through `monkeypatch` rather
than by assignment, so the slow handshake cannot leak into a later test in the same module
and make it depend on execution order.

---

## Process-level environment state

All five new files clear the variables they exercise **before** setting them, through a
`clean_env`-style fixture, because `tests/conftest.py` sets `ENV=testing` and
`DEV_MODE=true` process-wide. Without that, every negative case would inherit a local
environment and pass for the wrong reason.

Two of them go further and assert the precondition *inside* the negative case:

```python
assert os.environ["DEV_MODE"] == "true", "this cell must exercise the conflict"
```

This is the direct lesson of RUNTIME-01, which survived 131 API-03 tests precisely because
those tests **cleared** `DEV_MODE` before asserting, and so never exercised the conflict
they existed to resolve. A test that deletes the variable whose precedence is the subject is
asserting something else.

---

## Subprocess isolation, where in-process was not possible

Two properties cannot be measured inside one interpreter:

* **The real route table under a given flag** (ENV-04). `PRODUCTION_ROUTER_ENABLED` is read
  at class-body time in two modules and consumed at import time in `main.py`. Measured in a
  subprocess, two cells (`false`, `true`), each with a fresh `dict(os.environ)`.
* **The 15-row environment matrix** of the pre-existing API-03 suite, unchanged.

The subprocess approach also means these cells cannot pollute the parent session at all,
which is the other reason to prefer it over reloading the application graph.

---

## Known limits, stated rather than glossed

* The ENV-04 `unset` cell is **not** measured end-to-end, because `core/config.py`'s
  `load_dotenv()` injects `backend_app/.env` into `os.environ` before the flag is read, so
  removing the variable yields whatever that file says rather than an unset flag. Proven
  in-process instead, by monkeypatching the variable away *after* dotenv has run.
* `ENV=production` cannot be booted on this machine — `core/rate_limit` correctly refuses to
  start outside a local environment with no shared store (API-03). The end-to-end mount
  cells therefore run under `ENV=development`, and environment-independence is established
  by the nine-cell predicate matrix rather than assumed.
* **No new order-dependent failure was observed**, but the Phase 7 targeted runs executed
  the new files in a handful of groupings, not in all orders. The full-regression figures
  below are the only evidence about the repository's actual collection order.

---

## TEST-04 and TEST-05, unchanged

Neither was touched this phase. TEST-04 (a tenth order-dependent failure that does not occur
in the repository's collection order, caused by a dataclass identity comparison across a
module reload) and TEST-05 (the Hypothesis constant-census flake in
`test_order_idempotence_under_retry.py`) stand as recorded above.

One observation that bears on TEST-05: Phase 7 added five new modules to the process, and
Hypothesis 6.x harvests constants from the modules present in the process to seed its draws.
If the constant census moves again in the full run, the mechanism is the one Part 4 already
describes, and the fix is still a change to someone else's property-test design rather than
a repair here.

---

# PHASE 7 ADDENDUM 2 — A THIRD INSTANCE OF THE CLASS, AND IT WAS MINE

> Written after the PostgreSQL lane of the Phase 7 full regression. The addendum above
> listed four shared-state hazards and said no new order-dependent failure was observed.
> That was true of the targeted runs and **false of the full run**, and the full run is the
> only evidence that counts. This section is the correction.

---

## What happened

The PostgreSQL lane produced **eleven failures across four files** that no targeted run had
ever produced:

```
tests/test_marketplace_pipeline.py          6  (every clone test, plus unpublish)
tests/test_tenant_isolation_library_paper.py 2  (the clone and unpublish cells)
tests/test_validation_sweep.py               2  (library.clone_strategy, library.unpublish_strategy)
tests/test_subscriber_restricted_operations.py 1 (the clone refusal's audit entry)
```

All eleven were `500 INTERNAL_SERVER_ERROR` on `POST /api/library/{id}/clone` or
`DELETE /api/library/{id}`. Two of them were the **exact cells this phase had just turned
green**.

Every one of those files passes in isolation. Measured, with the same PostgreSQL DSN:

| Selection | Result |
|---|---|
| `test_validation_sweep.py -k clone_strategy` alone | **6 passed** |
| all four files together | **701 passed**, 10 skipped |
| all four files **plus** `test_lib01_clone_strategy_postgres.py` | 11 failures reproduced |

The fifth file is the one this phase added.

## The mechanism, from the traceback

```
File "backend_app/routers/library.py", line 2371, in clone_strategy
    .execute()
File "tests/paper_pg_client.py", line 317, in execute
    rows = self.client.run(sql, params)
File "tests/paper_pg_client.py", line 343, in run
    with self.conn.cursor(...) as cur:
psycopg2.InterfaceError: connection already closed
```

The route was holding **a test shim whose connection had been closed**.

`tests/test_lib01_clone_strategy_postgres.py::run_clone` did a save/restore on a module
global:

```python
original = module._require_service_client
module._require_service_client = lambda: pg
try:    ...
finally: module._require_service_client = original
```

and `test_14_two_concurrent_clones_by_one_caller_are_bounded_and_owned` calls it **from two
threads at once**. The interleaving:

```
thread A   saves original = <the real function>,  installs <lambda: clientA>
thread B   saves original = <lambda: clientA>,    installs <lambda: clientB>
thread A   restores <the real function>
thread B   restores <lambda: clientA>             <-- leak
```

so the module was left permanently bound to a lambda returning `clientA`, whose connection
the worker then closed in its own `finally`. Every subsequent request to `clone_strategy` or
`unpublish_strategy` in that interpreter got a dead client.

**A read-modify-write on a module global, performed concurrently.** This is the third
instance of the class in this engagement, after the orphaned `slowapi` `Limiter` instances
(Phase 6, 8 failures) and the dataclass identity comparison across a module reload
(TEST-04). It is the first one that was mine.

## The fix: remove the race, do not guard it

A lock around the save/restore would have made the interleaving safe and left a global being
mutated by two threads. Replaced with a `threading.local`:

* a **module-scoped autouse fixture** binds `library._require_service_client` **once**, to a
  function that reads the thread-local, and restores it **once**, from a single thread;
* `run_clone` publishes its client on the thread-local;
* when no client is bound on a thread, the binding **falls through to the real function**, so
  `test_8_an_unavailable_service_client_is_503_not_500` — which exists to exercise the real
  one — keeps working.

There is no read-modify-write left to interleave, and nothing that can be restored wrongly.

Verified in the ordering that produced the leak (pytest collects alphabetically, so
`test_lib01_…` precedes all four victims): **718 passed, 10 skipped, 1 xfailed.**

## And a product defect the leak exposed: LIB-04

The leak is a test defect. What it *revealed* is not.

`psycopg2.InterfaceError` escaped the handler because the LIB-01 repair had narrowed this
module's `except Exception` handlers to `except _PostgrestAPIError`. The narrowing was right
in direction — a broad handler had been relabelling an `AttributeError` from a `None` client
as *"Failed to look up library entry."* — and it meant that **any** failure which is not a
PostgREST `APIError` now escaped to the generic 500.

That is not an edge case in production either. `supabase-py` performs the request with
`httpx`, and the commonest real read failure — DNS, TLS, connection reset, read timeout —
arrives as `httpx.ConnectError` / `httpx.ReadTimeout` / `httpx.RemoteProtocolError`, none of
which is an `APIError`. The narrowing turned "the database was unreachable" from a retryable
503 into a 500.

Fixed by adding a second handler at every client call on both paths, which logs the
exception **type** with `exc_info=True` and answers the same `MARKETPLACE_READ_FAILED`
(503). The `_PostgrestAPIError` handler stays first, so the log still records the database's
own answer when the database answered. Regression:
`tests/test_lib04_a_broken_client_is_503_not_500.py` — **19 passed**, parametrised over
`httpx.ConnectError`, `httpx.ReadTimeout`, `httpx.RemoteProtocolError`,
`psycopg2.InterfaceError`, `RuntimeError`, `AttributeError`, `ValueError` and `KeyError`,
because the property is that **the answer does not depend on the type**.

## What this costs the earlier claim, stated plainly

The addendum above says "no new order-dependent failure was observed, but the Phase 7
targeted runs executed the new files in a handful of groupings, not in all orders". That
caveat was the right one to write and it was not enough: the leak was present the whole
time, in a file that had been run many times, and only a **full-suite run with the
PostgreSQL lane enabled** surfaced it.

Two things follow for anyone reading this later:

1. **"It passed in isolation" and "it passed in every grouping I tried" are different
   claims from "it passed in the suite", and only the third one is evidence.** The four
   victim files passed together; adding the fifth broke them.
2. **The lane matters as much as the order.** This leak could only fire where
   `clone_strategy` is reached with a database configured, so the CI lane — which skips the
   PostgreSQL suites — could never have shown it. Running one lane and reporting it as the
   regression would have shipped LIB-04.
