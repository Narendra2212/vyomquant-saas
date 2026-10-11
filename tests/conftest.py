"""Pytest configuration for test environment setup."""
import asyncio
import os
import warnings

import pytest

# Set ENV=testing at module import time BEFORE any backend imports
# This ensures rate limiter uses in-memory storage
os.environ["ENV"] = "testing"

# Clear REDIS_URL to force in-memory rate limiting even if set in environment
os.environ["REDIS_URL"] = ""

# Set DEV_MODE for testing
os.environ["DEV_MODE"] = "true"

# Set JWT secrets for testing
os.environ["JWT_SECRET"] = "dev-secret-change-in-production"
os.environ["SUPABASE_JWT_SECRET"] = "dev-secret-change-in-production"

# Set DEFAULT_EXCHANGE for websocket tests
os.environ["DEFAULT_EXCHANGE"] = "binance"

# Set VYOMQUANT_MODE=safe to override .env file's paper mode and ensure SQLite fallback
os.environ["VYOMQUANT_MODE"] = "safe"

# Clear DATABASE_URL to prevent any .env or .env.example placeholder from being used.
#
# Task 13.21 adds ONE opt-in. The `database-tests` job in `.github/workflows/01-pr-check.yml`
# needs a real DATABASE_URL to reach its PostgreSQL service, but it also needs that URL to
# survive this file -- and the default must stay "blank it", or all 11,000+ tests in the
# SQLite lane become sensitive to whatever DATABASE_URL happens to be in the environment.
# AERORA_TEST_DATABASE_URL is the second name that says "this one is deliberate": setting
# DATABASE_URL alone still gets blanked here, so provisioning a schema does not silently
# re-point every SessionLocal() in the suite at a real server.
_EXPLICIT_TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
if _EXPLICIT_TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _EXPLICIT_TEST_DATABASE_URL
else:
    os.environ["DATABASE_URL"] = ""

# Must be set BEFORE any backend imports to override .env file

# ══════════════════════════════════════════════════════════════════════════════
#  SHARED-SINGLETON LEAK GUARD
#
#  `backend_app.core.cache.redis_manager` is ONE object for the whole session:
#  `SharedRedisManager.__new__` caches `_instance`, so `RedisClient()`,
#  `SharedRedisManager()` and the module-level `redis_manager` are all the same
#  instance, and `backend_app.core.subscription_engine.redis_manager` is that same
#  instance again under a second name. A test that reaches for one of its dependency
#  injection seams is therefore writing state that outlives it.
#
#  Three of those seams decide where every later cache read in the session goes:
#
#    _dev_mode_bypass  set by `_bypass_dev_mode()`. While true, `_get_active_cache()`
#                      stops returning the in-memory `MockRedisClient` and returns
#                      `_redis_manager.cache` instead.
#    _redis_manager    the backend manager `_get_active_cache()` reads `.cache` off.
#                      Tests bind this to an `AsyncMock`.
#    _test_mode        set by `_set_test_mode()`. While true, `_ensure_manager()` will
#                      not replace `_redis_manager`, so an injected mock is pinned.
#
#  Left dirty together they turn `redis_manager.setex`/`getdel`/`incr`/`get` into
#  `AsyncMock` factories for the rest of the run: writes never reach the mock store,
#  reads hand back `AsyncMock` objects, and the failures land in whichever file runs
#  next rather than in the file that caused them. That is what produced
#  `TypeError: '<' not supported between instances of 'AsyncMock' and 'int'` out of
#  backend_app/core/subscription_engine.py and the "passes alone, fails in the suite"
#  results across the ws-ticket, credential-redaction and tenant-isolation files.
#
#  This fixture is a net, not a licence: the module that mutates a seam should still
#  restore it (tests/test_durable_event_bus.py does). It exists so the next test to
#  reach for a seam cannot silently poison the files that follow it. It snapshots and
#  restores, so a test that legitimately injects a mock is unaffected *within* its own
#  body — only the leak past the test boundary is closed. No assertion is touched and
#  no ordering is imposed.
# ══════════════════════════════════════════════════════════════════════════════

#: Instance attributes on the shared manager that redirect cache traffic.
_REDIS_SINGLETON_SEAMS = (
    "_test_mode",
    "_dev_mode_bypass",
    "_redis_manager",
    "_mock_client",
)


@pytest.fixture(autouse=True)
def _restore_shared_redis_singleton():
    """Restore the shared Redis manager's injection seams after every test.

    Snapshots `__dict__` membership as well as value: `_dev_mode_bypass` does not
    exist until something calls `_bypass_dev_mode()`, and "did not exist" has to be
    restorable rather than replaced with a guessed default.
    """
    try:
        from backend_app.core.cache import redis_manager
    except Exception:
        # A test that does not import the backend at all should not be made to.
        yield
        return

    before = {
        name: redis_manager.__dict__[name]
        for name in _REDIS_SINGLETON_SEAMS
        if name in redis_manager.__dict__
    }
    try:
        yield
    finally:
        for name in _REDIS_SINGLETON_SEAMS:
            if name in before:
                redis_manager.__dict__[name] = before[name]
            else:
                redis_manager.__dict__.pop(name, None)


# ══════════════════════════════════════════════════════════════════════════════
#  EVENT-LOOP INSTALLATION GUARD
#
#  `asyncio.run()` does not only run a coroutine. On exit it closes its loop and
#  then calls `asyncio.set_event_loop(None)`, which flips the policy's `_set_called`
#  flag and leaves the main thread with NO current loop. From that moment on, for the
#  rest of the process, `asyncio.get_event_loop()` no longer auto-creates a loop — it
#  raises `RuntimeError: There is no current event loop in thread 'MainThread'`.
#
#  Around a dozen files in this suite drive coroutines through the older
#  `asyncio.get_event_loop().run_until_complete(...)`, so any module that calls
#  `asyncio.run` ahead of them breaks them — and only when the two are collected into
#  the same session, which is why the symptom was "passes alone, fails in the suite".
#  tests/test_expiry_sweep.py, tests/test_marketplace_concurrency.py,
#  tests/test_marketplace_pipeline.py and others already work around this one direction
#  at a time, each with its own local save-and-restore wrapper whose docstring names the
#  neighbour it is protecting. Those wrappers are the right instinct applied one file at
#  a time; this fixture applies it to every file at once, including the ones that use
#  plain `asyncio.run` (tests/test_durable_event_bus.py among them) and therefore have
#  nothing local to restore.
#
#  The invariant: a test always starts with a usable loop installed on this thread, and
#  always leaves one behind. ONE fallback loop is reused for the whole session rather
#  than a fresh loop per test, because thousands of unclosed loops would exhaust
#  selector handles on Windows — and reuse is also what the deprecated
#  `get_event_loop()` gave these tests historically, so it changes nothing they relied
#  on.
#
#  This installs a loop; it never removes or replaces a live one. A test that installs
#  its own loop (pytest-asyncio does this for every `async def` test) is left alone,
#  because the guard only acts when nothing usable is installed. No assertion is
#  touched, no ordering is imposed, and no test is skipped.
# ══════════════════════════════════════════════════════════════════════════════

#: Reused across the session; replaced only if a test closes it.
_FALLBACK_EVENT_LOOP = None


def _installed_event_loop():
    """The loop installed on this thread, or ``None``.

    The `DeprecationWarning` this accessor emits on 3.12 is suppressed rather than
    propagated: the warning is aimed at application code, and surfacing it once per
    test would bury the warnings the suite actually cares about.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            return asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            return None


def _ensure_event_loop_installed():
    """Install the fallback loop if, and only if, nothing usable is installed."""
    global _FALLBACK_EVENT_LOOP

    current = _installed_event_loop()
    if current is not None and not current.is_closed():
        return

    if _FALLBACK_EVENT_LOOP is None or _FALLBACK_EVENT_LOOP.is_closed():
        _FALLBACK_EVENT_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_FALLBACK_EVENT_LOOP)


@pytest.fixture(autouse=True)
def _event_loop_is_always_installed():
    """Guarantee a current event loop before each test, and restore one after."""
    _ensure_event_loop_installed()
    try:
        yield
    finally:
        _ensure_event_loop_installed()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTITLEMENT STATE GUARD
#
#  Two of the plan-ladder's mechanisms are STATEFUL across a process, and the
#  in-memory `MockRedisClient` the suite runs on holds that state in a plain dict
#  (`self._store`) that no test clears:
#
#    quota:{user}:{resource}:{YYYY-MM}   a monthly RESERVATION. `check_ml_quota`,
#                                        `check_backtest_quota` and
#                                        `check_optimization_quota` increment it as
#                                        they check — reserving inside the check is
#                                        what stops two concurrent requests both
#                                        seeing the last unit as free — and it is
#                                        deliberately never decremented on
#                                        completion, so "retry until it works" is not
#                                        an unmetered allowance.
#    entitlement:plan:{user}             the resolved plan, cached for 15 seconds so a
#                                        route carrying three gates makes one profile
#                                        read instead of three.
#
#  Both are correct in production and both leak between tests. The reservation is the
#  one that bites: `tests/test_training_worker.py` queues dozens of training jobs for
#  one user id, and `tests/test_training_status.py` queues more against the same id in
#  the same calendar month. Past Pro Quant's 50-run monthly allowance the route starts
#  refusing, so the second file saw `state: 'BLOCKED'` where it expected `'QUEUED'` —
#  the gate working exactly as designed, against a meter that should have been reset
#  with the fixture rather than carried across files. "Passes alone, fails in the
#  suite", for the third distinct reason this conftest documents.
#
#  Clearing the meter is the right fix and weakening the reservation is not: a
#  reservation that does not accumulate is not a reservation, and the production
#  behaviour under test elsewhere
#  (`tests/test_pricing_ladder.py`, `tests/test_saas_entitlements_gating_audit.py`)
#  depends on it accumulating.
#
#  Only these two namespaces are touched. Every other key in the store — rate-limit
#  counters, cached profiles, ws tickets — is left exactly as it was, so a test that
#  depends on its own cache writes is unaffected.
# ══════════════════════════════════════════════════════════════════════════════

#: Key prefixes the plan ladder writes. Cleared between tests; nothing else is.
_ENTITLEMENT_KEY_PREFIXES = ("quota:", "entitlement:plan:")


def _clear_entitlement_keys():
    """Drop every plan-ladder key from the shared in-memory cache store.

    Reaches for the mock store directly rather than going through `redis_manager.delete`
    because the keys are not enumerable through the public surface — there is no
    `SCAN` on the mock — and because this must work whether or not a test has left a
    dependency-injection seam pointing somewhere else.
    """
    try:
        from backend_app.core.cache import redis_manager
    except Exception:
        return

    for holder in (redis_manager, getattr(redis_manager, "_mock_client", None)):
        store = getattr(holder, "_store", None)
        if not isinstance(store, dict):
            continue
        for key in [
            k for k in list(store)
            if isinstance(k, str) and k.startswith(_ENTITLEMENT_KEY_PREFIXES)
        ]:
            store.pop(key, None)


@pytest.fixture(autouse=True)
def _entitlement_state_is_per_test():
    """Clear monthly reservations and the cached plan around every test."""
    _clear_entitlement_keys()
    try:
        yield
    finally:
        _clear_entitlement_keys()


# ══════════════════════════════════════════════════════════════════════════════
#  RATE-LIMITER LEAK GUARD  (REGRESSION_ORDER_ISOLATION.md)
#
#  The 8 order-dependent failures in `test_task_6_1_backtest_execute.py` were all an
#  HTTP 429 where 409/200/404/422/500 was expected -- 33 passed when that file ran
#  alone, 33 passed at HEAD, 8 failed at 80% of the full-suite ordering.
#
#  WHY A PER-FILE RESET WAS NOT ENOUGH. That file already carries
#
#      @pytest.fixture(autouse=True)
#      def clear_rate_limit():
#          from backend_app.core.rate_limit import limiter
#          limiter.reset()
#
#  autouse and function-scoped, so it ran. The problem is WHICH OBJECT it reset.
#  `core/rate_limit.py` builds ONE module-level `limiter`, the routers are decorated
#  with `@limiter.limit(...)` at THEIR import time, and `main.py:593` does
#  `app.state.limiter = limiter`. Three test files then re-import or reload that
#  module -- `test_api03_rate_limit_storage_is_fail_closed.py`,
#  `test_rate_limiter_fails_closed_in_production.py` (both `sys.modules.pop` +
#  `import_module`) and `test_rate_limit_backend.py` (`importlib.reload`) -- which
#  REBINDS `rate_limit.limiter` to a brand-new `Limiter` with its own storage while
#  every registered route keeps closing over the original.
#
#  From that point on `from backend_app.core.rate_limit import limiter` yields a
#  different object than the app is counting on, so the per-file reset cleared an
#  unused limiter and the app's per-minute windows accumulated across thousands of
#  TestClient requests until the routes started answering 429. The two rate-limit
#  files run at 57% and `test_task_6_1` at 80%, which is the ordering observed.
#
#  Measured, not inferred: `staging/probe_order_isolation_429.py` confirms all six
#  steps -- the app holds the original, a re-import yields a different object, the app
#  still holds the original, the re-imported one is therefore the wrong reset target,
#  `importlib.reload` does the same, and the two have separate storage.
#
#  THE FIX. Reset the limiter THE APP HOLDS, which is reachable as
#  `app.state.limiter` whatever the module currently exports. Done here rather than in
#  five separate files because it is a property of the session, not of any one file.
#
#  TEST-ONLY CONTAMINATION, not a product defect. Nothing in production re-imports
#  `core.rate_limit`; each caller has its own rate-limit key and the windows roll.
#  What the suite does that production does not is share one client address across
#  ~13,000 requests in one process.
# ══════════════════════════════════════════════════════════════════════════════


#: Every ``Limiter`` instance this session has seen, including the ORPHANS a module
#: re-import leaves behind. A ``WeakSet`` so a limiter that really does become garbage is
#: not kept alive by this guard.
_SEEN_LIMITERS = None

#: The fingerprint of the last scan: ``(id(module limiter), id(app), route count)``.
#:
#: Keyed on the APP as well as the limiter, and that was a correction. Keying on the
#: limiter alone scanned once -- on the first test, before anything had imported
#: ``backend_app.main`` -- found no routes, recorded the limiter id, and then never
#: re-scanned, so the guard was inert for the whole session and the 8 failures survived
#: it. The route count is in the key too, because the app is built incrementally and a
#: scan taken before the last router was registered would miss its limiter.
_LAST_SCAN_KEY = None


def _scan_for_limiters():
    """Find every ``Limiter`` the app's routes are actually enforcing on, and remember it.

    MEASURED NECESSITY, not defensiveness. ``staging/probe_order_isolation_429_body.py``
    found THREE live ``Limiter`` instances mid-suite: ``app.state.limiter`` (which is also
    the module's) and two ORPHANS left behind by the module re-imports. Resetting
    ``app.state.limiter`` left the request at 429; resetting every live limiter brought it
    to the expected 409. So the object doing the counting is reachable from neither the
    module attribute nor ``app.state``.

    WHY THE ROUTE CLOSURES AND NOT ``gc.get_objects()``. The gc sweep was tried first and
    raised ``MemoryError`` on this process -- the app plus Hypothesis is millions of
    objects and the returned list alone exhausted memory. ``slowapi`` keeps the limiter in
    the closure of the wrapper its ``@limiter.limit(...)`` decorator installs (measured:
    ``staging/probe_find_limiters_cheaply.py`` found 2 limiters across 351 route
    endpoints, and ``endpoint attributes mentioning 'limit'`` is empty, so the closure is
    the only place it lives). Walking 351 endpoints is cheap and reaches exactly the
    objects that can refuse a request.
    """
    global _SEEN_LIMITERS

    try:
        from slowapi import Limiter
    except Exception:
        return

    import sys
    import weakref

    if _SEEN_LIMITERS is None:
        _SEEN_LIMITERS = weakref.WeakSet()

    main_module = sys.modules.get("backend_app.main")
    app = getattr(main_module, "app", None) if main_module is not None else None
    for route in getattr(app, "routes", ()) or ():
        func = getattr(route, "endpoint", None)
        for _ in range(6):  # a short __wrapped__ chain; decorators here do not nest deep
            if func is None:
                break
            for cell in getattr(func, "__closure__", None) or ():
                try:
                    value = cell.cell_contents
                except ValueError:  # an empty cell
                    continue
                if isinstance(value, Limiter):
                    _SEEN_LIMITERS.add(value)
            func = getattr(func, "__wrapped__", None)


def _reset_bound_rate_limiters():
    """Reset every limiter object that could be holding a count for this session.

    Deliberately does NOT import ``backend_app.main``. A test that never touches the app
    should not pay for importing it, and forcing the import here would change what
    thousands of tests load. The app is reset only if it is already in ``sys.modules``.
    """
    global _LAST_SCAN_KEY

    import sys

    rate_limit_module = sys.modules.get("backend_app.core.rate_limit")
    current = getattr(rate_limit_module, "limiter", None)

    main_module = sys.modules.get("backend_app.main")
    app = getattr(main_module, "app", None) if main_module is not None else None

    # Re-scan when anything that could introduce a new enforcing limiter has changed:
    # the module's limiter being swapped by a re-import, a different app object, or more
    # routes registered than the last scan saw.
    scan_key = (
        id(current) if current is not None else None,
        id(app) if app is not None else None,
        len(getattr(app, "routes", ()) or ()) if app is not None else 0,
    )
    if scan_key != _LAST_SCAN_KEY:
        _LAST_SCAN_KEY = scan_key
        _scan_for_limiters()

    targets = []
    if _SEEN_LIMITERS is not None:
        targets.extend(list(_SEEN_LIMITERS))

    bound = getattr(getattr(app, "state", None), "limiter", None)
    for candidate in (bound, current):
        if candidate is not None and not any(candidate is item for item in targets):
            targets.append(candidate)

    for limiter in targets:
        reset = getattr(limiter, "reset", None)
        if reset is None:
            continue
        try:
            reset()
        except Exception:
            # A limiter whose storage refuses a reset must not fail the test that was
            # about to run; the counts it keeps are the thing being guarded against,
            # and a failure here would be reported against an innocent test.
            pass


@pytest.fixture(autouse=True)
def _rate_limit_counts_are_per_test():
    """No test inherits another's rate-limit count. See the banner above."""
    _reset_bound_rate_limiters()
    try:
        yield
    finally:
        _reset_bound_rate_limiters()
