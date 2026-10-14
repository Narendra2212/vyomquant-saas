"""RT-16 -- ConsistencyChecker's three compounding bugs, fixed or explicitly left open.

THE DEFECT (audit)
    `PositionConsistencyChecker` existed with three compounding problems that make it
    unsafe to turn on, not one:
      1. `_get_active_tenant_exchanges()` returned a hardcoded `[]` -- it checked nothing.
      2. `_get_exchange_positions()` called `get_or_create_exchange()` with NO credentials
         and then `exchange.fetch_positions()` -- a derivatives/margin call that has
         nothing to read on this spot-only platform.
      3. `_handle_mismatch()` called `GlobalKillSwitch.activate()` -- a true,
         platform-wide halt -- unconditionally on ANY mismatch, including a mismatch
         that was really just a failed CHECK (`_check_positions`'s own except-clause
         returns `all_matched=False` with an empty `mismatches` list on an exchange
         fetch error, and nothing distinguished that from a real divergence).

THE FIX (this file proves, with fakes; no network, no real exchange)
    * `_get_exchange_positions` now builds a real per-tenant executor via the same
      vault-based path RT-21/RT-16 already use, and reads `get_balance()` (the correct
      spot call) instead of `fetch_positions()`; it raises (never silently returns {})
      when the vault or an executor isn't available.
    * `_run_consistency_checks` no longer treats a failed CHECK as a mismatch to escalate.
    * `_handle_mismatch` no longer calls the kill switch at all. It writes each
      mismatch to `ReconciliationMismatchRepository` at severity MEDIUM -- never
      CRITICAL -- so `ReconciliationScheduler`'s own, already-correct severity gate
      decides whether anything escalates, instead of this detector having a second,
      ungated path to the same platform-wide halt.

WHAT THIS FILE DOES NOT PROVE, ON PURPOSE
    That real tenant data is safe to enable. `_get_active_tenant_exchanges()` still
    returns `[]`: `PositionModel` (the LOCAL side of the comparison) has no writer
    anywhere in the live/paper trading path, so turning on real enumeration today would
    make every tenant with any exchange balance register a permanent, structural
    MISSING_LOCAL finding on every check, forever -- flooding the very table RT-16 just
    gave a real, useful feed via OrderWatchdog. That gap is tracked, not silently
    dropped -- see `_get_active_tenant_exchanges`'s own docstring and the ledger.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.core.consistency_checker import (ConsistencyCheckResult,
                                                   PositionComparison,
                                                   PositionConsistencyChecker,
                                                   PositionStatus)

TENANT = uuid.UUID("11111111-1111-4111-8111-1111111111ab")


class _FakeExecutor:
    def __init__(self, balances):
        self._balances = balances

    async def get_balance(self):
        return self._balances


@pytest.mark.asyncio
class TestExchangePositionsUsesRealCreditalsAndTheCorrectCall:
    async def test_it_builds_an_executor_from_stored_keys_via_the_vault(self):
        checker = PositionConsistencyChecker()
        fake_executor = _FakeExecutor({
            "BTC": {"free": "0.1", "used": "0", "total": "0.5"},
            "USDT": {"free": "100", "used": "0", "total": "100"},
            "ETH": {"free": "0", "used": "0", "total": "0"},  # zero balance, excluded
        })
        captured = {}

        async def fake_executor_for(tenant_id, exchange_id, vault):
            captured["tenant_id"] = tenant_id
            captured["exchange_id"] = exchange_id
            captured["vault"] = vault
            return fake_executor

        fake_vault = object()
        with patch("backend_app.backend.order_control._executor_for", fake_executor_for), \
             patch("backend_app.core.state.app_state") as mock_state:
            mock_state.vault = fake_vault
            positions = await checker._get_exchange_positions(str(TENANT), "binance")

        assert positions == {"BTC": Decimal("0.5"), "USDT": Decimal("100")}
        assert captured["tenant_id"] == str(TENANT)
        assert captured["exchange_id"] == "binance"
        assert captured["vault"] is fake_vault

    async def test_no_vault_raises_rather_than_a_silent_empty_result(self):
        checker = PositionConsistencyChecker()
        with patch("backend_app.core.state.app_state") as mock_state:
            mock_state.vault = None
            with pytest.raises(RuntimeError):
                await checker._get_exchange_positions(str(TENANT), "binance")

    async def test_this_file_never_calls_fetch_positions_the_derivatives_api(self):
        """Pin the bug this fixes: the old code called `exchange.fetch_positions()`,
        the wrong API for a spot-only platform. A fake executor with that method
        missing (as a real spot CCXTExchangeExecutor's get_balance-only surface would
        be) must still work -- proving nothing here reaches for it."""
        checker = PositionConsistencyChecker()

        class _NoFetchPositionsExecutor:
            async def get_balance(self):
                return {"BTC": {"free": "1", "used": "0", "total": "1"}}
            # Deliberately no fetch_positions / no other method.

        async def fake_executor_for(tenant_id, exchange_id, vault):
            return _NoFetchPositionsExecutor()

        with patch("backend_app.backend.order_control._executor_for", fake_executor_for), \
             patch("backend_app.core.state.app_state") as mock_state:
            mock_state.vault = object()
            positions = await checker._get_exchange_positions(str(TENANT), "binance")

        assert positions == {"BTC": Decimal("1")}


def _result(mismatches, *, error_message=None, all_matched=False):
    return ConsistencyCheckResult(
        tenant_id=str(TENANT), exchange_id="binance",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        all_matched=all_matched, mismatches=mismatches, error_message=error_message,
    )


def _mismatch(symbol="BTC", local=Decimal("1"), exchange=Decimal("0")):
    return PositionComparison(
        symbol=symbol, local_size=local, exchange_size=exchange,
        difference=abs(local - exchange), status=PositionStatus.MISMATCH,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
class TestHandleMismatchNeverCallsTheKillSwitch:
    async def test_a_real_mismatch_is_recorded_at_medium_never_critical(self):
        checker = PositionConsistencyChecker()
        result = _result([_mismatch()])

        recorded = []

        class _FakeRepo:
            def __init__(self, db):
                pass

            def record_mismatch(self, data):
                recorded.append(data)

        with patch(
            "backend_app.core.models.reconciliation.ReconciliationMismatchRepository",
            _FakeRepo,
        ), patch("backend_app.core.database.SessionLocal") as mock_session:
            mock_session.return_value.__enter__ = lambda self: SimpleNamespace()
            mock_session.return_value.__exit__ = lambda self, *a: None
            await checker._handle_mismatch(result)

        assert len(recorded) == 1
        from backend_app.core.models.reconciliation import MismatchSeverity
        assert recorded[0].severity == MismatchSeverity.MEDIUM, (
            "ConsistencyChecker must never claim CRITICAL -- that is "
            "ReconciliationScheduler's own trigger for the GLOBAL kill switch, and "
            "this detector has no production verification to back such a claim"
        )
        assert recorded[0].tenant_id == TENANT
        assert recorded[0].symbol == "BTC"

    async def test_the_global_kill_switch_is_never_imported_or_called(self):
        """The whole point of this fix: no code path in this module reaches for
        `get_global_kill_switch` any more."""
        import backend_app.core.consistency_checker as mod
        assert not hasattr(mod, "get_global_kill_switch"), (
            "consistency_checker.py must not import the kill switch at all -- "
            "a mismatch here is recorded, not used to halt the platform directly"
        )

    async def test_a_record_failure_is_logged_and_does_not_raise(self):
        checker = PositionConsistencyChecker()
        result = _result([_mismatch()])

        with patch("backend_app.core.database.SessionLocal", side_effect=RuntimeError("db down")):
            await checker._handle_mismatch(result)  # must not raise


@pytest.mark.asyncio
class TestAFailedCheckIsNeverTreatedAsAMismatch:
    async def test_an_exchange_fetch_error_does_not_reach_handle_mismatch(self):
        checker = PositionConsistencyChecker()
        checker._handle_mismatch = AsyncMock()

        async def fake_check_positions(tenant_id, exchange_id):
            return _result([], error_message="exchange timeout", all_matched=False)

        checker._check_positions = fake_check_positions
        checker._get_active_tenant_exchanges = AsyncMock(
            return_value=[(str(TENANT), "binance")]
        )

        await checker._run_consistency_checks()

        checker._handle_mismatch.assert_not_called()

    async def test_a_real_mismatch_still_reaches_handle_mismatch(self):
        checker = PositionConsistencyChecker()
        checker._handle_mismatch = AsyncMock()

        async def fake_check_positions(tenant_id, exchange_id):
            return _result([_mismatch()], error_message=None, all_matched=False)

        checker._check_positions = fake_check_positions
        checker._get_active_tenant_exchanges = AsyncMock(
            return_value=[(str(TENANT), "binance")]
        )

        await checker._run_consistency_checks()

        checker._handle_mismatch.assert_called_once()
