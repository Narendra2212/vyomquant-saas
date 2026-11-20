"""RECON-01: ExchangeReconciliationService no longer trusts a single, constructor-
injected exchange client to belong to whichever tenant_id a caller passes in.

THE DEFECT
    __init__(self, db_session, exchange_client=None, ...) stored exactly one exchange
    client, used by _fetch_exchange_orders for EVERY tenant_id passed to
    reconcile_open_orders. A caller that (today, nothing does; this is the "exists but
    never started" half of this finding's own title) reconciled tenant A and tenant B
    through the same service instance would compare tenant B's local order rows
    against tenant A's real exchange account -- RECON-01's own ledger entry names this
    exact risk ("a wrong guess risks reconciling one tenant's orders against another's
    exchange account").

THE FIX
    A new, optional `vault` constructor parameter. When set, _fetch_exchange_orders
    resolves a FRESH executor scoped to the (tenant_id, exchange_id) pair of THIS
    call, via the same order_control._executor_for helper RT-16's OrderWatchdog fix
    and RT-21's manual cancel already use -- instead of self.exchange. The old
    exchange_client path is kept, unchanged, for backward compatibility with existing
    callers/tests that already construct this service with one.

    Also added: ExchangeExecutor.fetch_open_orders(symbol=None), extracted from
    cancel_all_open_orders' own internal listing step, so a caller that only wants to
    COMPARE orders does not have to go through a method whose purpose is to cancel
    them.

NOT fixed, deliberately, and still open: leader election (nothing stops N worker
processes from all reconciling the same rows) and tenant enumeration (nothing calls
reconcile_open_orders for any real tenant). This file proves the one structural defect
--  one client used for every tenant -- is closed; it does not claim this service is
now safe to start.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend_app.backend.exchange_executor import ExchangeError
from backend_app.backend.exchange_reconciliation import ExchangeReconciliationService
from tests.test_rt21_users_can_cancel_resting_orders import ScriptedVenue, make_executor


class _FakeVault:
    def __init__(self, keys_by_tenant_exchange):
        self._keys = keys_by_tenant_exchange

    def load_decrypted_keys(self, user_id, exchange_id):
        key = (str(user_id), str(exchange_id))
        if key not in self._keys:
            raise ValueError(f"No keys found for {user_id}/{exchange_id}.")
        return self._keys[key]


class _FakeExecutor:
    def __init__(self, open_orders):
        self._exchange = object()  # already "connected"
        self.fetch_open_orders = AsyncMock(return_value=open_orders)


@pytest.fixture(autouse=True)
def _patch_get_exchange_executor(monkeypatch):
    """order_control._executor_for calls get_exchange_executor(...) then .connect()
    only if ._exchange is None -- our fakes are pre-connected, so .connect() is never
    reached, keeping this test free of any real network/ccxt dependency."""
    executors_by_tenant: dict = {}
    monkeypatch.setattr(
        "backend_app.backend.order_control.get_exchange_executor",
        MagicMock(side_effect=lambda exchange_id, api_key, *a, **k: executors_by_tenant[api_key]),
    )
    return executors_by_tenant


@pytest.mark.asyncio
class TestPerTenantExecutorResolution:
    async def test_vault_configured_resolves_a_fresh_executor_per_call(
        self, _patch_get_exchange_executor
    ):
        tenant_a_orders = [{"id": "a1", "symbol": "BTC/USDT", "side": "buy",
                             "amount": 1, "filled": 0, "remaining": 1,
                             "average": 0, "price": 100, "status": "open"}]
        executor_a = _FakeExecutor(tenant_a_orders)
        _patch_get_exchange_executor["key-a"] = executor_a

        vault = _FakeVault({
            ("tenant-a", "binance"): {
                "api_key": "key-a", "secret_key": "s", "password": None,
                "sandbox": True, "testnet": False,
            },
        })
        service = ExchangeReconciliationService(db_session=None, vault=vault)

        orders = await service._fetch_exchange_orders("tenant-a", "binance")

        assert [o["order_id"] for o in orders] == ["a1"]
        executor_a.fetch_open_orders.assert_awaited_once()

    async def test_two_tenants_through_the_same_service_get_their_own_executors_never_each_others(
        self, _patch_get_exchange_executor
    ):
        """The actual correctness property this fix exists for: tenant B's call must
        never see tenant A's orders, and vice versa."""
        executor_a = _FakeExecutor([{"id": "a-order", "symbol": "BTC/USDT", "side": "buy",
                                      "amount": 1, "filled": 0, "remaining": 1,
                                      "average": 0, "price": 1, "status": "open"}])
        executor_b = _FakeExecutor([{"id": "b-order", "symbol": "ETH/USDT", "side": "sell",
                                      "amount": 2, "filled": 0, "remaining": 2,
                                      "average": 0, "price": 1, "status": "open"}])
        _patch_get_exchange_executor["key-a"] = executor_a
        _patch_get_exchange_executor["key-b"] = executor_b

        vault = _FakeVault({
            ("tenant-a", "binance"): {"api_key": "key-a", "secret_key": "s",
                                       "password": None, "sandbox": True, "testnet": False},
            ("tenant-b", "binance"): {"api_key": "key-b", "secret_key": "s",
                                       "password": None, "sandbox": True, "testnet": False},
        })
        service = ExchangeReconciliationService(db_session=None, vault=vault)

        orders_a = await service._fetch_exchange_orders("tenant-a", "binance")
        orders_b = await service._fetch_exchange_orders("tenant-b", "binance")

        assert [o["order_id"] for o in orders_a] == ["a-order"]
        assert [o["order_id"] for o in orders_b] == ["b-order"]

    async def test_vault_without_an_exchange_id_refuses_rather_than_guessing(self):
        vault = _FakeVault({})
        service = ExchangeReconciliationService(db_session=None, vault=vault)

        with pytest.raises(RuntimeError, match="exchange_id"):
            await service._fetch_exchange_orders("tenant-a", None)

    async def test_no_vault_still_uses_the_old_shared_client_unchanged(self):
        """Backward compatibility: existing callers/tests that construct this service
        with a plain exchange_client (no vault) must see no behaviour change."""
        shared_client = AsyncMock()
        shared_client.fetch_open_orders = AsyncMock(return_value=[
            {"id": "shared-1", "symbol": "BTC/USDT", "side": "buy", "amount": 1,
             "filled": 0, "remaining": 1, "average": 0, "price": 1, "status": "open"},
        ])
        service = ExchangeReconciliationService(db_session=None, exchange_client=shared_client)

        orders = await service._fetch_exchange_orders("any-tenant", "binance")

        assert [o["order_id"] for o in orders] == ["shared-1"]


@pytest.mark.asyncio
class TestExecutorFetchOpenOrders:
    """The new ExchangeExecutor.fetch_open_orders(symbol=None), on its own."""

    async def test_a_healthy_call_returns_the_venues_raw_orders(self):
        venue = ScriptedVenue(open_orders=[
            {"id": "v1", "symbol": "BTC/USDT"}, {"id": "v2", "symbol": "ETH/USDT"},
        ])
        executor = make_executor(venue)

        orders = await executor.fetch_open_orders()

        assert [o["id"] for o in orders] == ["v1", "v2"]

    async def test_a_venue_error_is_classified_and_raised_not_swallowed_to_empty(self):
        """An empty list and 'the venue could not be asked' are different facts; a
        caller that treated them the same would read every open order as closed."""
        venue = ScriptedVenue(list_error=ConnectionError("venue unreachable"))
        executor = make_executor(venue)

        with pytest.raises(ExchangeError):
            await executor.fetch_open_orders()
