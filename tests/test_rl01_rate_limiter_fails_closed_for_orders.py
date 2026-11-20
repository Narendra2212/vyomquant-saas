"""tests/test_rl01_rate_limiter_fails_closed_for_orders.py

RL-01 (P3) -- ``core/distributed_rate_limiter.py::DistributedRateLimiter.check_rate_limit``
and ``core/exchange_rate_limiter.py::DistributedRateLimiterIntegration.check_user_rate_limit``
both answered "allowed" for EVERY rate limit type on ANY failure (a Redis outage, a
wiring error) with no distinction between them. ``ORDERS_PER_MINUTE``/``"orders"`` is the
one whose whole job is keeping a user's own order submissions under whatever a real
exchange's API limit allows -- exceeding it risks the exchange banning the account, a
categorically worse outcome than refusing a request this check could not verify. The
other limit types (signals, API calls, WebSocket connections) are internal/UI-facing,
where fail-open remains the better trade-off (refusing a legitimate request on a
transient blip is worse there).

REACHABILITY NOTE
------------------
Neither of these two layers is reached by any live request in this codebase today:
``exchange_executor.py`` and ``connection_engine.py`` (the actual order/connection
paths) use ccxt's own built-in ``enableRateLimit: True`` instead, which is purely
in-process and has no Redis dependency to fail open or closed on. This file still
verifies the fix, for the same reason SEC-11 fixed a logging line in dead code: a
fail-open landmine in an unreached module becomes a live bug the moment something is
wired up to call it, and proving the fix now is cheaper than re-discovering the gap
later.
"""
from __future__ import annotations

import pytest

from backend_app.core.distributed_rate_limiter import (
    DistributedRateLimiter,
    RateLimitType,
)
from backend_app.core import exchange_rate_limiter as erl_module


class _ExplodingRedisManager:
    """Stands in for ``backend_app.backend.redis_manager.redis_manager``, failing every
    call the way a genuine Redis outage would.
    """

    async def pipeline(self):
        raise ConnectionError("simulated Redis outage")

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("simulated Redis outage")

    async def delete(self, *_args, **_kwargs):
        raise ConnectionError("simulated Redis outage")


@pytest.fixture
def exploding_redis(monkeypatch):
    monkeypatch.setattr(
        "backend_app.core.distributed_rate_limiter.redis_manager", _ExplodingRedisManager()
    )


class TestDistributedRateLimiterFailsClosedForOrders:
    @pytest.mark.asyncio
    async def test_orders_per_minute_denies_on_a_redis_outage(self, exploding_redis):
        limiter = DistributedRateLimiter()
        status = await limiter.check_rate_limit("user-1", RateLimitType.ORDERS_PER_MINUTE)
        assert status.allowed is False, (
            "ORDERS_PER_MINUTE must fail CLOSED on a Redis outage -- exceeding it risks "
            "a real exchange ban, which is worse than refusing an order this check "
            "could not verify"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "limit_type",
        [
            RateLimitType.SIGNALS_PER_MINUTE,
            RateLimitType.API_CALLS_PER_SECOND,
            RateLimitType.WEBSOCKET_CONNECTIONS,
        ],
    )
    async def test_cosmetic_limit_types_still_fail_open_on_a_redis_outage(
        self, exploding_redis, limit_type
    ):
        """NEGATIVE CONTROL: this fix must not blanket fail-closed everything -- only
        the venue-rate-limit-relevant type. Refusing a legitimate signal/API call/
        WebSocket connection on a transient Redis blip is the worse outcome for these.
        """
        limiter = DistributedRateLimiter()
        status = await limiter.check_rate_limit("user-1", limit_type)
        assert status.allowed is True

    @pytest.mark.asyncio
    async def test_with_a_healthy_redis_orders_are_still_allowed_under_the_limit(self, monkeypatch):
        """NEGATIVE CONTROL: the fail-closed path is reached only on an actual
        exception -- this is not a blanket "always deny orders" regression.
        """
        class _HealthyPipe:
            async def execute(self):
                return [1]  # first request this window

            def incr(self, *_a, **_k):
                pass

            def expire(self, *_a, **_k):
                pass

        class _HealthyRedisManager:
            async def pipeline(self):
                return _HealthyPipe()

        monkeypatch.setattr(
            "backend_app.core.distributed_rate_limiter.redis_manager", _HealthyRedisManager()
        )
        limiter = DistributedRateLimiter()
        status = await limiter.check_rate_limit("user-1", RateLimitType.ORDERS_PER_MINUTE)
        assert status.allowed is True


class TestExchangeRateLimiterIntegrationFailsClosedForOrders:
    @pytest.mark.asyncio
    async def test_orders_operation_denies_when_the_layer_beneath_it_raises(self, monkeypatch):
        """``check_user_rate_limit`` resolves ``get_distributed_rate_limiter`` via a
        LOCAL ``from ... import`` at call time, so patching the attribute on its
        source module (``distributed_rate_limiter``, not ``exchange_rate_limiter``) is
        what actually takes effect.
        """
        import backend_app.core.distributed_rate_limiter as drl_module

        def _raising_get_limiter():
            raise RuntimeError("simulated failure above check_rate_limit")

        monkeypatch.setattr(drl_module, "get_distributed_rate_limiter", _raising_get_limiter)

        allowed = await erl_module.DistributedRateLimiterIntegration.check_user_rate_limit(
            "user-1", operation="orders"
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_non_order_operation_still_fails_open_on_the_same_failure(self, monkeypatch):
        import backend_app.core.distributed_rate_limiter as drl_module

        def _raising_get_limiter():
            raise RuntimeError("simulated failure above check_rate_limit")

        monkeypatch.setattr(drl_module, "get_distributed_rate_limiter", _raising_get_limiter)

        allowed = await erl_module.DistributedRateLimiterIntegration.check_user_rate_limit(
            "user-1", operation="signals"
        )
        assert allowed is True
