"""TRADE-03: the second venue-write path cannot write without the TRADE-04 artefact.

THE FINDING
-----------
`exchange_executor.place_order` is the canonical venue boundary and carries the
TRADE-04 live-execution gate. `backend/transactional_execution_manager.py` has its own
`exchange.create_order` / `exchange.cancel_order` calls and does **not** go through it, so
fixing TRADE-04 did not cover it.

It is currently unreachable -- `execute_order` has no callers, its one construction site
(`exchange_websocket_listener.py:343`) is in a module nothing starts, and
`exchange_adapter` is never supplied. But unreachability is a property of today's call
graph. One `get_transactional_execution_manager(db, tenant, adapter)` call from a route or
a worker would arm a live order path with no risk evaluation, no kill switch, no
strategy-ownership check, no notional ceiling, no clientOrderId and no venue minimums.

WHAT THIS FILE ASSERTS
----------------------
Not "the path is dead" -- that is a fact about callers and will stop being true the moment
somebody adds one. It asserts that **even when fully armed** (adapter supplied, validation
passing, a real exchange client attached) the raw write is refused without a signed
`LiveOrderAuthorization`.

The oracle is the same as TRADE-04's: a recording exchange client. `calls == []` means no
order reached a venue.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.live_execution_gate import (  # noqa: E402
    REQUIRED_CHECKS, LiveExecutionRefused, authorize_live_order)

TENANT = uuid.uuid4()


def _run(coro):
    return asyncio.run(coro)


class RecordingExchange:
    """Records every venue write; performs none."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    async def create_order(self, symbol, type_, side, amount, price=None):
        self.calls.append(("create_order", symbol, type_, side, amount, price))
        return {"id": "venue-1", "status": "open", "filled": amount, "average": price}

    async def cancel_order(self, order_id):
        self.calls.append(("cancel_order", order_id))
        return {"id": order_id, "status": "canceled"}


class _Adapter:
    """A FULLY ARMED adapter. The point is that arming is not sufficient."""

    exchange_id = "binance"

    def __init__(self, exchange: RecordingExchange) -> None:
        self._exchange = exchange

    def get_exchange(self):
        return self._exchange


def _manager(exchange: RecordingExchange):
    """A TransactionalExecutionManager with a real adapter attached.

    Built with `object.__new__` so no database session is required and the methods under
    test are the genuine ones.
    """
    from backend_app.backend.transactional_execution_manager import \
        TransactionalExecutionManager

    mgr = object.__new__(TransactionalExecutionManager)
    mgr.db = None
    mgr.tenant_id = TENANT
    mgr.transaction = None
    mgr.exchange_adapter = _Adapter(exchange)
    mgr._current_live_authorization = None
    return mgr


ORDER = {
    "symbol": "BTC/USDT",
    "type": "market",
    "side": "buy",
    "quantity": Decimal("0.01"),
    "price": Decimal("50000"),
    "tenant_id": str(TENANT),
}


# ── 1. armed but unauthorised: no venue write ───────────────────────────────


def test_an_armed_manager_cannot_place_an_order_without_authorization():
    """THE TRADE-03 ASSERTION."""
    exchange = RecordingExchange()
    mgr = _manager(exchange)

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(mgr._execute_on_exchange(dict(ORDER)))

    assert excinfo.value.code == "LIVE_EXECUTION_NOT_AUTHORIZED"
    assert exchange.calls == [], (
        "the second write path reached a venue with no live-execution authorisation. "
        "TRADE-03 is open and TRADE-04's gate does not cover this path."
    )


def test_an_armed_manager_cannot_cancel_without_authorization():
    exchange = RecordingExchange()
    mgr = _manager(exchange)

    with pytest.raises(LiveExecutionRefused):
        _run(mgr._cancel_on_exchange("some-order-id"))

    assert exchange.calls == []


def test_the_refusal_happens_before_the_adapter_is_even_consulted():
    """Ordering matters: the gate must precede any use of the exchange client.

    An adapter whose `get_exchange()` raises proves the gate ran first -- if the gate were
    after the adapter lookup, this would surface the adapter's error instead.
    """
    class _Exploding:
        exchange_id = "binance"

        def get_exchange(self):
            raise AssertionError("the adapter was consulted before the gate refused")

    from backend_app.backend.transactional_execution_manager import \
        TransactionalExecutionManager

    mgr = object.__new__(TransactionalExecutionManager)
    mgr.tenant_id = TENANT
    mgr.exchange_adapter = _Exploding()
    mgr._current_live_authorization = None

    with pytest.raises(LiveExecutionRefused):
        _run(mgr._execute_on_exchange(dict(ORDER)))


# ── 2. a partial or wrong authorisation is also refused ────────────────────


def test_an_authorization_missing_a_check_is_refused():
    exchange = RecordingExchange()
    mgr = _manager(exchange)

    with pytest.raises(LiveExecutionRefused):
        # `authorize_live_order` refuses to issue a partial set at all, which is the
        # first line of defence.
        authorize_live_order(
            execution_id="e", tenant_id=str(TENANT), strategy_id="s",
            symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
            exchange_id="binance", checks=REQUIRED_CHECKS - {"risk"},
        )
    assert exchange.calls == []


def test_an_authorization_for_a_different_order_is_refused():
    exchange = RecordingExchange()
    mgr = _manager(exchange)

    mgr._current_live_authorization = authorize_live_order(
        execution_id="e", tenant_id=str(TENANT), strategy_id="s",
        symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
        exchange_id="binance", checks=REQUIRED_CHECKS,
    )

    bigger = dict(ORDER)
    bigger["quantity"] = Decimal("5")

    with pytest.raises(LiveExecutionRefused) as excinfo:
        _run(mgr._execute_on_exchange(bigger))

    assert excinfo.value.code == "LIVE_AUTHORIZATION_ORDER_MISMATCH"
    assert exchange.calls == []


# ── 3. positive control: a correct authorisation DOES let it through ───────


def test_a_correct_authorization_permits_the_write():
    """Without this, the refusals above could be an unconditional error.

    This is the shape a legitimate reinstatement would take: route the intent through
    `ExecutionEngine.execute_with_idempotency` so an authorisation exists, and this method
    then works unchanged.
    """
    exchange = RecordingExchange()
    mgr = _manager(exchange)

    mgr._current_live_authorization = authorize_live_order(
        execution_id="e", tenant_id=str(TENANT), strategy_id="s",
        symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
        exchange_id="binance", checks=REQUIRED_CHECKS,
    )

    result = _run(mgr._execute_on_exchange(dict(ORDER)))

    assert exchange.calls, "a correctly authorised write was refused; the gate is a brick"
    assert exchange.calls[0][0] == "create_order"
    assert result["order_id"] == "venue-1"


def test_the_authorization_is_consumed():
    """One authorisation, one write -- same contract as the canonical boundary."""
    exchange = RecordingExchange()
    mgr = _manager(exchange)
    mgr._current_live_authorization = authorize_live_order(
        execution_id="e", tenant_id=str(TENANT), strategy_id="s",
        symbol="BTC/USDT", side="buy", size=Decimal("0.01"),
        exchange_id="binance", checks=REQUIRED_CHECKS,
    )

    _run(mgr._execute_on_exchange(dict(ORDER)))
    assert len(exchange.calls) == 1

    with pytest.raises(LiveExecutionRefused):
        _run(mgr._execute_on_exchange(dict(ORDER)))
    assert len(exchange.calls) == 1, "a consumed authorisation permitted a second write"


# ── 4. the authoritative boundary inventory ───────────────────────────────


def test_every_venue_write_in_the_repository_is_accounted_for():
    """The list of external trading side-effect boundaries, pinned.

    Found by AST across `backend_app/`, so a NEW raw `create_order`/`cancel_order` call
    on an attribute fails this test and has to be classified deliberately.
    """
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    backend = repo / "backend_app"

    WRITE_NAMES = {
        "create_order", "create_limit_order", "create_market_order",
        "cancel_order", "cancel_all_orders", "edit_order",
    }
    #: Receivers that are demonstrably NOT an exchange client. The method names collide
    #: with CCXT's, which is why a name-only sweep over-matches -- the first version of
    #: this test reported four "new venue writes" that are all local database or
    #: state-machine calls:
    #:
    #:   core/reconciliation_engine.py:754/756  self.state_service.create_order/cancel_order
    #:                                          -> writes local reconciliation state
    #:   core/database_scaling.py:617           self.order_repo.create_order
    #:                                          -> a database repository write
    #:   core/order_state_engine.py:745         self.cancel_order
    #:                                          -> a self-call to its own state machine;
    #:                                             the code comments say "Simulated cancel"
    #:
    #: Classified by RECEIVER rather than suppressed by file, so the same method name on a
    #: genuine exchange client in one of those files would still be caught.
    LOCAL_RECEIVERS = {
        "self",
        "self.state_service",
        "self.order_repo",
        "self.repo",
        "self.db",
        "service",
    }

    #: file -> the venue-write calls it is ALLOWED to contain, each with its disposition.
    SANCTIONED = {
        "backend_app/backend/exchange_executor.py": "canonical boundary, TRADE-04 gated",
        "backend_app/backend/transactional_execution_manager.py": "TRADE-03 gated",
        "backend_app/backend/exchange_simulator.py": "in-process simulator, no network",
        "backend_app/backend/connection_engine.py": "installs a MOCK over the client",
        "backend_app/backend/exchange_reconciliation.py": "read-only reconciler",
        "backend_app/backend/order_watchdog.py": "read-only watchdog",
        "backend_app/routers/orders.py": "HTTP handlers; execution hard-blocked",
        "backend_app/routers/paper_trading.py": "paper service, no exchange",
        "backend_app/backend/paper_trading_service.py": "paper service, no exchange",
        "backend_app/core/execution_engine.py": "the gateway; calls place_order only",
    }

    offenders = []
    for path in sorted(backend.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(repo).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in WRITE_NAMES:
                continue
            if rel in SANCTIONED:
                continue
            receiver = ast.unparse(func.value)
            if receiver in LOCAL_RECEIVERS:
                continue
            offenders.append(f"{rel}:{node.lineno} -> {receiver}.{func.attr}")

    assert offenders == [], (
        "NEW venue-write call site(s) outside the sanctioned set:\n  "
        + "\n  ".join(offenders)
        + "\n\nEvery external trading side-effect boundary must be classified and, if it "
          "can reach a real venue, must require a LiveOrderAuthorization. If the receiver "
          "is a local service or repository rather than an exchange client, add it to "
          "LOCAL_RECEIVERS with the evidence."
    )
