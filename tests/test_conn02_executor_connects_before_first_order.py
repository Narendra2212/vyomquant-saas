"""
tests/test_conn02_executor_connects_before_first_order.py

CONN-02: nothing ever connected the order executor a live bot built.

``BotRunner`` obtained a ``CCXTExchangeExecutor`` from ``get_exchange_executor`` and handed
it to the ``ExecutionEngine``; no caller anywhere invoked ``connect()``. At the first order
``_exchange`` was ``None`` and ``_sandbox_verification`` unset, so under
``VYOMQUANT_LIVE_SANDBOX_ONLY=true`` every live order was refused - measured in production
during the live testnet run (Sentry VYOMQUANT-API-PRODUCTION-Q: "this binance client is
not verified to address one. No order was sent.") - and without that flag it would have
failed on the missing client. No live order could be placed on this path.

Asserted:
1. the first venue call connects, which on a testnet VERIFIES the sandbox endpoint, so the
   LIVE_SANDBOX_ONLY policy then passes;
2. a client that cannot be proven to address the sandbox is still refused (the safety
   property is unchanged);
3. connecting is idempotent: one connect for many calls;
4. every venue-calling method connects first;
5. the bot connects its executor at boot.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from backend_app.backend.exchange_executor import CCXTExchangeExecutor
from tests.test_exch01_sandbox_endpoint_verification import FAKE, SwappingVenue

REPO = Path(__file__).resolve().parents[1]


def _venue(connects):
    class Verified(SwappingVenue):
        def __init__(self, config=None):
            connects.append(1)
            super().__init__(config)

        async def load_markets(self, *_a, **_k):
            return {"BTC/USDC": {"symbol": "BTC/USDC"}}

        async def fetch_balance(self):
            return {"USDC": {"free": 100, "used": 0, "total": 100}}

        async def fetch_open_orders(self, *_a, **_k):
            return []

    return Verified


def _install(monkeypatch, venue_cls):
    from backend_app.backend import exchange_executor as mod

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": venue_cls}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)


def test_the_first_venue_call_connects_and_verifies_the_sandbox(monkeypatch):
    connects = []
    _install(monkeypatch, _venue(connects))
    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    assert ex.sandbox_verified is False and ex._exchange is None   # the state that was refused

    asyncio.run(ex.fetch_open_orders("BTC/USDC"))

    assert ex._connected is True and ex.sandbox_verified is True
    from backend_app.core.live_execution_gate import assert_sandbox_only_policy
    monkeypatch.setenv("VYOMQUANT_LIVE_SANDBOX_ONLY", "true")
    assert_sandbox_only_policy(ex.sandbox_verified, exchange_id="binance")   # no raise


def test_connecting_is_idempotent(monkeypatch):
    connects = []
    _install(monkeypatch, _venue(connects))
    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)

    async def many():
        await asyncio.gather(*(ex._ensure_connected() for _ in range(5)))
        await ex.fetch_open_orders("BTC/USDC")

    asyncio.run(many())
    # One armed client for the connection (the verifier's pristine production probe is a
    # separate, unarmed instance and is not counted as a connection).
    assert ex._connected is True
    first = len(connects)
    asyncio.run(ex._ensure_connected())
    assert len(connects) == first, "an already-connected executor must not reconnect"


def test_an_unprovable_sandbox_is_still_refused(monkeypatch):
    from backend_app.backend.exchange_executor import SandboxVerificationFailed

    class ProductionOnly(SwappingVenue):
        def __init__(self, config=None):
            super().__init__({})          # ignores the sandbox request: stays on production

    _install(monkeypatch, ProductionOnly)
    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    with pytest.raises(SandboxVerificationFailed):
        asyncio.run(ex.fetch_open_orders("BTC/USDC"))
    assert ex.sandbox_verified is False


def test_every_venue_calling_method_connects_first():
    tree = ast.parse((REPO / "backend_app" / "backend" / "exchange_executor.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CCXTExchangeExecutor")
    missing = []
    for fn in cls.body:
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name in ("connect", "_ensure_connected",
                                                                      "disconnect",
                                                                      "_close_unverified_client"):
            continue
        text = ast.unparse(fn)
        if "self._exchange." in text and "_ensure_connected" not in text:
            missing.append(fn.name)
    assert not missing, f"these methods call the venue without connecting first: {missing}"


def test_the_bot_connects_its_executor_at_boot():
    src = (REPO / "backend_app" / "backend" / "master_executor.py").read_text()
    assert "_ensure_connected" in src
    assert src.index("get_exchange_executor(") < src.index("await ensure_connected()")
