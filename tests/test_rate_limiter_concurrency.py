"""
tests/test_rate_limiter_concurrency.py

Regression tests for the RateLimiter per-endpoint locking fix.

Three properties are verified:
  1. Rate limits are still correctly enforced per-endpoint under concurrent
     load — many coroutines hammering the same endpoint must not burst
     beyond the configured rate.
  2. Different endpoints can now proceed concurrently — two endpoints that
     would individually each need ~(N-1) * min_interval seconds must
     complete in roughly that time, not 2x.
  3. No TOCTOU race: last_request_time is always updated atomically
     relative to the next waiter on the same endpoint.
  4. First call on a fresh endpoint is never delayed.

Note: all timing tests use min_interval >= 50 ms to stay well above
Windows's default system-timer resolution (~15 ms).
"""
import asyncio
import time

import pytest

from backend_app.backend.exchange_executor import RateLimiter


# ---------------------------------------------------------------------------
# Shared async helper
# ---------------------------------------------------------------------------

async def _acquire_and_record(limiter: RateLimiter, endpoint: str, results: list):
    """Acquire a token; record the wall-clock monotonic time when granted."""
    await limiter.acquire(endpoint)
    results.append(time.monotonic())


# ---------------------------------------------------------------------------
# Test 1 — Per-endpoint rate limit still enforced under concurrent load
# ---------------------------------------------------------------------------

def test_same_endpoint_rate_limit_respected():
    """
    N concurrent calls to the same endpoint must each be separated by at
    least min_interval.  The per-endpoint lock serializes them correctly;
    if two coroutines could both skip the sleep the gap would be ~0.
    """
    async def _run():
        rps = 10.0           # min_interval = 100 ms (above Windows timer floor)
        n = 4                # 3 sleeps of 100 ms → total ~300 ms
        limiter = RateLimiter(requests_per_second=rps)
        timestamps: list = []

        await asyncio.gather(
            *[_acquire_and_record(limiter, "place_order", timestamps)
              for _ in range(n)]
        )

        timestamps.sort()
        min_interval = 1.0 / rps
        tolerance    = 0.030   # 30 ms scheduling headroom on slow CI

        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert gap >= min_interval - tolerance, (
                f"Rate limit violated between grants {i-1} → {i}: "
                f"gap={gap*1000:.1f} ms, min={min_interval*1000:.0f} ms "
                f"(tolerance={tolerance*1000:.0f} ms)"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2 — Different endpoints do NOT serialize on each other's sleep
# ---------------------------------------------------------------------------

def test_different_endpoints_run_concurrently():
    """
    Fire N calls on 'place_order' and N calls on 'cancel_order' concurrently.

    Old (global lock): total ≈ 2*(N-1)*min_interval (fully serialized).
    New (per-endpoint): total ≈ (N-1)*min_interval (parallel).

    We assert elapsed < 1.5*(N-1)*min_interval + headroom to prove the
    two endpoint pipelines ran in parallel, not series.
    """
    async def _run():
        rps = 10.0           # 100 ms min_interval
        n = 3                # 2 sleeps per endpoint → ~200 ms if parallel
        min_interval = 1.0 / rps
        limiter = RateLimiter(requests_per_second=rps)

        ts_a: list = []
        ts_b: list = []

        start = time.monotonic()
        await asyncio.gather(
            *[_acquire_and_record(limiter, "place_order",  ts_a) for _ in range(n)],
            *[_acquire_and_record(limiter, "cancel_order", ts_b) for _ in range(n)],
        )
        elapsed = time.monotonic() - start

        sequential_one_endpoint = (n - 1) * min_interval   # 200 ms
        # Serialized (old bug) would be: ~2 * sequential_one_endpoint = 400 ms.
        # Parallel (new)    would be: ~1 * sequential_one_endpoint = 200 ms.
        max_allowed = 1.5 * sequential_one_endpoint + 0.10  # 400 ms total gate

        assert elapsed < max_allowed, (
            f"Endpoints appear to have serialized:\n"
            f"  elapsed         = {elapsed*1000:.0f} ms\n"
            f"  max_allowed     = {max_allowed*1000:.0f} ms\n"
            f"  (sequential_one = {sequential_one_endpoint*1000:.0f} ms, rps={rps})"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3 — No TOCTOU race on the same endpoint
# ---------------------------------------------------------------------------

def test_no_toctou_race_on_same_endpoint():
    """
    Many concurrent callers on one endpoint must never receive grants less
    than min_interval apart.  A TOCTOU bug (reading last_request_time
    outside the lock) would let two coroutines both see elapsed=0, both
    skip the sleep, and record timestamps ~0 ms apart.
    """
    async def _run():
        rps = 10.0           # 100 ms min_interval
        n = 4
        limiter = RateLimiter(requests_per_second=rps)
        timestamps: list = []

        await asyncio.gather(
            *[_acquire_and_record(limiter, "get_balance", timestamps)
              for _ in range(n)]
        )

        timestamps.sort()
        min_interval = 1.0 / rps
        tolerance    = 0.030   # 30 ms headroom

        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert gap >= min_interval - tolerance, (
                f"TOCTOU race detected: two grants on 'get_balance' were "
                f"issued only {gap*1000:.1f} ms apart "
                f"(min_interval={min_interval*1000:.0f} ms, "
                f"tolerance={tolerance*1000:.0f} ms)"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4 — First call on a fresh endpoint is never delayed
# ---------------------------------------------------------------------------

def test_first_call_is_immediate():
    """The very first acquire on any endpoint must complete without sleeping."""
    async def _run():
        rps = 5.0           # min_interval = 200 ms
        limiter = RateLimiter(requests_per_second=rps)

        start = time.monotonic()
        await limiter.acquire("place_order")
        elapsed = time.monotonic() - start

        assert elapsed < 0.050, (
            f"First call delayed unexpectedly: {elapsed*1000:.1f} ms "
            f"(should be near-zero; min_interval={1000/rps:.0f} ms)"
        )

    asyncio.run(_run())
