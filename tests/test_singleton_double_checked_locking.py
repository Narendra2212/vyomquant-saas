"""
tests/test_singleton_double_checked_locking.py

Regression tests for the double-checked locking fix applied to:
  - core/dependencies.py   ::  get_supabase()
  - backend/exchange_executor.py  ::  get_circuit_breaker()

These tests validate that concurrent cold-start callers produce exactly
ONE singleton instance, matching the pattern already in
core/auth_middleware.py::_get_jwks_client().

Both functions are plain sync (not async), so threading.Lock is correct
and tests use threading.Thread to simulate concurrent calls.
"""
import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _run_concurrently(fn, n=20):
    """Run fn() in n threads simultaneously; return all return values."""
    results = [None] * n
    barrier = threading.Barrier(n)   # all threads start at the same moment
    errors = []

    def worker(i):
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if errors:
        raise errors[0]
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Test 1:  get_supabase() creates exactly one client under concurrent load
# ──────────────────────────────────────────────────────────────────────────────

def test_get_supabase_singleton_under_concurrent_cold_start():
    """
    20 threads call get_supabase() simultaneously from a cold state.
    Only one Supabase client must be constructed; all callers receive
    the same object.
    """
    import backend_app.core.dependencies as dep_module

    mock_client = MagicMock(name="FakeSupabaseClient")
    call_count = {"n": 0}
    call_lock = threading.Lock()

    def fake_create_client(url, key):
        with call_lock:
            call_count["n"] += 1
        return mock_client

    # Reset module singleton so we start from a cold state
    dep_module._supabase_client = None

    env_patch = {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
    }

    with patch.dict("os.environ", env_patch):
        with patch("backend_app.core.dependencies.DEV_MODE", False):
            with patch("supabase.create_client", fake_create_client):
                results = _run_concurrently(dep_module.get_supabase, n=20)

    # All results must be the same object
    assert all(r is mock_client for r in results), (
        "Some callers received a different Supabase client instance"
    )
    # Exactly one construction call
    assert call_count["n"] == 1, (
        f"Expected exactly 1 create_client call, got {call_count['n']} "
        "(double-checked locking not working)"
    )

    # Cleanup
    dep_module._supabase_client = None


# ──────────────────────────────────────────────────────────────────────────────
# Test 2:  get_circuit_breaker() creates exactly one breaker per exchange_id
# ──────────────────────────────────────────────────────────────────────────────

def test_get_circuit_breaker_singleton_under_concurrent_cold_start():
    """
    20 threads call get_circuit_breaker("binance") simultaneously from a
    cold state.  Only one CircuitBreaker must be constructed; all callers
    receive the same object.
    """
    import backend_app.backend.exchange_executor as exc_module

    # Remove any existing entry so we start cold
    exc_module._exchange_circuit_breakers.pop("binance", None)

    construction_count = {"n": 0}
    count_lock = threading.Lock()
    real_CircuitBreaker = exc_module.CircuitBreaker

    class CountingCircuitBreaker(real_CircuitBreaker):
        def __init__(self, **kwargs):
            with count_lock:
                construction_count["n"] += 1
            super().__init__(**kwargs)

    with patch.object(exc_module, "CircuitBreaker", CountingCircuitBreaker):
        results = _run_concurrently(
            lambda: exc_module.get_circuit_breaker("binance"), n=20
        )

    # All results must be the same object
    ids = {id(r) for r in results}
    assert len(ids) == 1, (
        f"Expected 1 unique CircuitBreaker instance, got {len(ids)} "
        "(double-checked locking not working)"
    )
    # Exactly one construction call
    assert construction_count["n"] == 1, (
        f"Expected exactly 1 CircuitBreaker construction, got {construction_count['n']}"
    )

    # Cleanup
    exc_module._exchange_circuit_breakers.pop("binance", None)


# ──────────────────────────────────────────────────────────────────────────────
# Test 3:  get_circuit_breaker() isolates distinct exchange_ids
# ──────────────────────────────────────────────────────────────────────────────

def test_get_circuit_breaker_independent_per_exchange():
    """
    Two different exchange_ids must produce two independent CircuitBreaker
    instances, not the same one.
    """
    import backend_app.backend.exchange_executor as exc_module

    exc_module._exchange_circuit_breakers.pop("bybit", None)
    exc_module._exchange_circuit_breakers.pop("coinbase", None)

    cb_bybit    = exc_module.get_circuit_breaker("bybit")
    cb_coinbase = exc_module.get_circuit_breaker("coinbase")

    assert cb_bybit is not cb_coinbase, (
        "Different exchange_ids must produce independent CircuitBreaker instances"
    )

    # Same id produces the same object on a second call (steady-state)
    cb_bybit2 = exc_module.get_circuit_breaker("bybit")
    assert cb_bybit is cb_bybit2, (
        "Repeated call for the same exchange_id should return the same instance"
    )

    # Cleanup
    exc_module._exchange_circuit_breakers.pop("bybit", None)
    exc_module._exchange_circuit_breakers.pop("coinbase", None)
