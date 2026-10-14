"""RT-16 (W-1b) -- the watchdog's executor factory now returns a real executor.

THE DEFECT (audit; W-1b, left explicitly unfixed by the W-1 phase that preceded this one)
    ``_get_executor_for_order`` reached for ``app_state.exchange_router`` (never set
    anywhere in the application) and ``vault.get_exchange_client`` (never a method
    ``APIKeyVault`` defines). Both branches raised, the ``except`` converted that to
    ``None``, and every caller's "no executor -> mark UNKNOWN, alert" fail-safe is
    indistinguishable, from the outside, between "this tenant genuinely holds no venue
    keys" and "the lookup was never wired to begin with". Reconciliation and ambiguous-
    submission adjudication were therefore dead code in production regardless of what ran
    in CI.

THE FIX
    Reuse, not reinvent: RT-21's ``order_control._executor_for`` already builds a real,
    per-tenant executor from this tenant's stored, decrypted exchange keys
    (``APIKeyVault.load_decrypted_keys`` -> ``get_exchange_executor`` -> ``.connect()``),
    for the manual cancel path. ``_get_executor_for_order`` now calls exactly that function,
    with the vault reached from ``app_state.vault`` (which DOES exist -- the audit's own
    account of W-1b is explicit that only ``exchange_router`` and ``get_exchange_client``
    were missing, not ``vault`` itself).

WHAT THIS FILE PROVES (fake vault; no network, no real exchange)
    * a tenant with stored keys gets a connected executor for the order's own exchange_id;
    * a tenant with no stored keys gets ``None``, not an exception -- the existing
      "cannot ask" fail-safe at every call site still applies unchanged;
    * the vault is asked for THIS order's ``tenant_id`` and ``exchange_id``, not a
      different tenant's or a hardcoded venue;
    * ``app_state.vault`` missing (e.g. in a stripped-down test harness) is the same honest
      ``None``, not a crash.

NOT SHOWN: a real exchange sandbox connection (needs real credentials -- EXTERNAL_BLOCKED,
same constraint recorded against RT-16 in the ledger); the broader reconciliation loop
actually being FED real tenants to check (ConsistencyChecker's own tenant list, and
ReconciliationScheduler's own feed, are separate, larger gaps this file does not touch).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig


def _order(tenant_id="11111111-1111-4111-8111-111111111111", exchange_id="binance"):
    return SimpleNamespace(
        execution_id="exec1", tenant_id=tenant_id, exchange_id=exchange_id,
        symbol="ETH/USDT", order_id="venue-order-1",
    )


class _FakeVault:
    def __init__(self, keys_by_tenant_exchange):
        self._keys = keys_by_tenant_exchange

    def load_decrypted_keys(self, user_id, exchange_id):
        key = (str(user_id), str(exchange_id))
        if key not in self._keys:
            raise ValueError(f"No keys found for {user_id}/{exchange_id}.")
        return self._keys[key]


class _FakeExecutor:
    def __init__(self):
        self._exchange = None
        self.connected = False

    async def connect(self):
        self.connected = True
        self._exchange = object()


@pytest.mark.asyncio
class TestExecutorFactoryIsReal:
    async def test_a_tenant_with_stored_keys_gets_a_connected_executor(self, monkeypatch):
        order = _order()
        vault = _FakeVault({
            (order.tenant_id, "binance"): {
                "api_key": "k", "secret_key": "s", "password": None,
                "sandbox": True, "testnet": False,
            },
        })
        monkeypatch.setattr(
            "backend_app.core.state.app_state", SimpleNamespace(vault=vault),
        )
        fake_executor = _FakeExecutor()
        monkeypatch.setattr(
            "backend_app.backend.order_control.get_exchange_executor",
            MagicMock(return_value=fake_executor),
        )

        w = OrderWatchdog(None, WatchdogConfig())
        executor = await w._get_executor_for_order(order)

        assert executor is fake_executor
        assert fake_executor.connected is True, (
            "a not-yet-connected executor must be connected before it is handed back"
        )

    async def test_a_tenant_with_no_stored_keys_gets_none_not_an_exception(self, monkeypatch):
        order = _order()
        vault = _FakeVault({})  # no keys for anyone
        monkeypatch.setattr(
            "backend_app.core.state.app_state", SimpleNamespace(vault=vault),
        )
        w = OrderWatchdog(None, WatchdogConfig())
        executor = await w._get_executor_for_order(order)
        assert executor is None

    async def test_the_lookup_is_scoped_to_this_orders_own_tenant_and_exchange(self, monkeypatch):
        order = _order(tenant_id="tenant-A", exchange_id="kraken")
        seen = {}

        def _load(user_id, exchange_id):
            seen["user_id"], seen["exchange_id"] = user_id, exchange_id
            return {"api_key": "k", "secret_key": "s", "password": None,
                    "sandbox": True, "testnet": False}

        vault = SimpleNamespace(load_decrypted_keys=_load)
        monkeypatch.setattr(
            "backend_app.core.state.app_state", SimpleNamespace(vault=vault),
        )
        monkeypatch.setattr(
            "backend_app.backend.order_control.get_exchange_executor",
            MagicMock(return_value=_FakeExecutor()),
        )
        w = OrderWatchdog(None, WatchdogConfig())
        await w._get_executor_for_order(order)
        assert seen == {"user_id": "tenant-A", "exchange_id": "kraken"}

    async def test_no_vault_on_app_state_is_none_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(
            "backend_app.core.state.app_state", SimpleNamespace(vault=None),
        )
        w = OrderWatchdog(None, WatchdogConfig())
        executor = await w._get_executor_for_order(_order())
        assert executor is None

    async def test_an_already_connected_executor_is_not_reconnected(self, monkeypatch):
        order = _order()
        vault = _FakeVault({
            (order.tenant_id, "binance"): {
                "api_key": "k", "secret_key": "s", "password": None,
                "sandbox": True, "testnet": False,
            },
        })
        monkeypatch.setattr(
            "backend_app.core.state.app_state", SimpleNamespace(vault=vault),
        )
        fake_executor = _FakeExecutor()
        fake_executor._exchange = object()  # already connected
        connect = AsyncMock()
        fake_executor.connect = connect
        monkeypatch.setattr(
            "backend_app.backend.order_control.get_exchange_executor",
            MagicMock(return_value=fake_executor),
        )
        w = OrderWatchdog(None, WatchdogConfig())
        await w._get_executor_for_order(order)
        connect.assert_not_awaited()
