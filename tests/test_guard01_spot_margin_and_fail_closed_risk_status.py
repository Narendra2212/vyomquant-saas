"""GUARD-01 -- ExecutionGuard no longer assumes 10x margin, nor fails open on risk status.

THE DEFECT
    1. ``_validate_sufficient_balance`` assumed a flat 10% margin requirement (10x
       leverage) for EVERY market, unconditionally. This platform has no margin/
       leverage trading anywhere (RT-21's own finding: ``BotRunner.
       recovered_exposure_coins`` is always non-negative, no short side) -- so a spot
       buy could pass this check with as little as 1/10th of what it actually costs.
    2. ``_validate_risk_engine_status`` wrapped the Redis read AND the parse in one
       ``try``/``except Exception``, treating a dropped connection or a corrupted
       payload exactly like "no status has ever been recorded" and answering PASS
       either way -- a safety gate silently resolving its own read failure into a
       claim that things are fine.

NEITHER CHECK IS ON THE LIVE ORDER PATH TODAY
    ``core/execution_engine.py``'s own comment states ExecutionGuard is called only
    from the two cancel endpoints, never from order placement, and all three existing
    call sites pass ``reduce_only=True`` with a control side -- which closes these two
    checks' coroutines WITHOUT awaiting them (see ``validate_trade``'s own
    ``is_control`` branch). So today this fixes a real bug in code nothing currently
    reaches; it matters for whenever ExecutionGuard is wired into the order-placement
    path, which ``execution_engine.py``'s own comment records as outstanding.

THE FIX
    ``_validate_sufficient_balance`` now requires the full notional plus a small fee
    buffer for ``market_type == "spot"`` (the only one this platform implements), and
    refuses outright for any other claimed market_type rather than guessing a margin
    rate for a feature that does not exist here. ``_validate_risk_engine_status`` now
    separates three cases: a Redis error BLOCKs; a missing key (nothing has ever
    reported unhealthy) PASSes; an unparseable payload BLOCKs.

WHAT THIS FILE PROVES (direct unit calls on an ExecutionGuard instance; no real Redis)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend_app.backend.execution_guard import ExecutionGuard, ValidationSeverity


def _guard(redis_client=None) -> ExecutionGuard:
    return ExecutionGuard(redis_client)


@pytest.mark.asyncio
class TestSpotRequiresTheFullNotional:
    async def test_a_spot_order_needing_the_old_10pct_margin_now_fails(self):
        guard = _guard()
        # notional = 1 * 100 = 100. Old code required only 10 (10%); new code requires
        # ~100.1 (full notional + 0.1% fee buffer). 50 covers the old bug but not the fix.
        result = await guard._validate_sufficient_balance(
            portfolio_state={"available_balance": "50", "free_margin": "50"},
            signal={"size": "1", "price": "100", "market_type": "spot"},
        )
        assert result.passed is False
        assert result.severity == ValidationSeverity.BLOCK

    async def test_a_spot_order_with_the_full_notional_available_passes(self):
        guard = _guard()
        result = await guard._validate_sufficient_balance(
            portfolio_state={"available_balance": "200", "free_margin": "200"},
            signal={"size": "1", "price": "100", "market_type": "spot"},
        )
        assert result.passed is True

    async def test_market_type_defaults_to_spot_when_absent(self):
        guard = _guard()
        result = await guard._validate_sufficient_balance(
            portfolio_state={"available_balance": "50", "free_margin": "50"},
            signal={"size": "1", "price": "100"},  # no market_type key at all
        )
        assert result.passed is False
        assert result.details["market_type"] == "spot"

    async def test_a_non_spot_market_type_is_refused_rather_than_guessed_at(self):
        guard = _guard()
        result = await guard._validate_sufficient_balance(
            portfolio_state={"available_balance": "1000000", "free_margin": "1000000"},
            signal={"size": "1", "price": "100", "market_type": "futures"},
        )
        assert result.passed is False
        assert "futures" in result.message

    async def test_the_fee_buffer_is_included_in_required_margin(self):
        guard = _guard()
        # notional = 100; full notional alone (100) must now fail since the fee buffer
        # pushes the requirement just above it.
        result = await guard._validate_sufficient_balance(
            portfolio_state={"available_balance": "100", "free_margin": "100"},
            signal={"size": "1", "price": "100", "market_type": "spot"},
        )
        assert result.passed is False


class _FakeRedisRaises:
    async def get(self, key):
        raise ConnectionError("redis unreachable")


class _FakeRedisMissing:
    async def get(self, key):
        return None


class _FakeRedisCorrupted:
    async def get(self, key):
        return b"{not valid json or python literal"


class _FakeRedisUnhealthy:
    async def get(self, key):
        return b'{"operational": false, "reason": "circuit breaker tripped"}'


class _FakeRedisHealthy:
    async def get(self, key):
        return b'{"operational": true}'


@pytest.mark.asyncio
class TestRiskEngineStatusFailsClosedOnARealError:
    async def test_a_redis_read_error_blocks(self):
        guard = _guard(_FakeRedisRaises())
        result = await guard._validate_risk_engine_status()
        assert result.passed is False

    async def test_a_missing_key_passes_nothing_has_reported_unhealthy(self):
        guard = _guard(_FakeRedisMissing())
        result = await guard._validate_risk_engine_status()
        assert result.passed is True

    async def test_an_unparseable_payload_blocks_rather_than_assumes_ok(self):
        guard = _guard(_FakeRedisCorrupted())
        result = await guard._validate_risk_engine_status()
        assert result.passed is False

    async def test_an_explicitly_unhealthy_status_still_blocks(self):
        guard = _guard(_FakeRedisUnhealthy())
        result = await guard._validate_risk_engine_status()
        assert result.passed is False

    async def test_an_explicitly_healthy_status_passes(self):
        guard = _guard(_FakeRedisHealthy())
        result = await guard._validate_risk_engine_status()
        assert result.passed is True
