"""`ExchangeReconciliationService` must not invent order outcomes it cannot observe.

PHASE 10 / WORKSTREAM D / observation 4.

WHAT WAS FOUND
--------------
The service is **never started**. `backend_app/main.py`'s lifespan registers seven
background services and contains zero references to this module; `get_reconciliation_service`
has no caller anywhere in the repository. The only runtime code that tries to use it,
`backend_app/backend/exchange_websocket_listener.py::_fallback_poll`, imports
`ExchangeReconciliationEngine` -- a name this module does not define -- inside a bare
`try`, so the WebSocket fallback poll raises ImportError on every invocation and has never
once run.

WHY THAT IS NOT FIXED BY STARTING IT
------------------------------------
Two paths resolve order state with no venue confirmation at all, so starting the service
as-is would be the dangerous change rather than the safe one:

1. ``exchange_client is None`` made ``_fetch_exchange_orders`` return ``[]``. An empty list
   is indistinguishable from "the venue has no open orders", so ``reconcile_open_orders``
   treats EVERY active DB order as absent from the exchange and resolves them all. This is
   the same defect as BUG-FIX UK-01, which hardened only the *exception* path and left the
   no-client path returning ``[]``.

2. When the client has no ``fetch_order``, ``_handle_missing_exchange_order`` marked the
   order COMPLETED (if any fill was recorded) or FAILED (if not) purely from its ABSENCE
   from the open-orders list. That contradicts BUG-FIX ORD-03 and BUG-FIX REC-01 in the
   same function, which preserve state for retry whenever the venue's answer is uncertain.

Both now fail safely: (1) raises, (2) returns NO_ACTION with state preserved.

These tests use mocks only. No exchange is contacted, no order is placed, nothing is
reconciled against a real venue -- and this file therefore does NOT claim that exchange
reconciliation is verified end to end. It claims only what it measures: that the two
no-evidence paths no longer fabricate an outcome.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from unittest.mock import AsyncMock, Mock

import pytest

from backend_app.backend.exchange_reconciliation import (
    ExchangeReconciliationService, ReconciliationAction)

REPO = pathlib.Path(__file__).resolve().parents[1]
RECONCILER_SRC = REPO / "backend_app" / "backend" / "exchange_reconciliation.py"
MAIN_SRC = REPO / "backend_app" / "main.py"
WS_LISTENER_SRC = (
    REPO / "backend_app" / "backend" / "exchange_websocket_listener.py"
)


# ═════════════════════════════════════════════════════════════════════════════
#  1. The lifecycle verdict, pinned so it cannot change silently
# ═════════════════════════════════════════════════════════════════════════════

def test_the_service_is_not_registered_in_the_application_lifespan():
    """Records the measured fact, and makes wiring it up a deliberate act.

    THREE REASONS WERE GIVEN HERE. TWO HAVE SINCE BEEN CLOSED; ONE DECIDES IT.

    Closed: "this service is per-tenant with nothing enumerating tenants" --
    ``enumerate_reconcilable_pairs`` now derives the pairs from open orders
    (tests/test_recon01_tenant_enumeration.py). Closed: "gunicorn runs
    `--workers ${WORKERS:-2}` with no leader election, so N processes would reconcile the
    same rows concurrently" -- ``_try_become_leader`` now delegates to
    ``core.leader_lease``, which fails closed
    (tests/test_recon01_exchange_reconciliation_leader_election.py).

    Still decisive, and verified against current source rather than inherited: the first
    reason, that **`OrderWatchdog` already runs a reconciliation loop over the same
    `execution_records` rows**. It is constructed and started in the lifespan, its
    ``_reconciliation_loop`` polls the same PENDING/EXECUTING population every 5-10s, and
    RT-16 gave it a real per-tenant executor via ``_get_executor_for_order``. So the
    capability this entry exists to guarantee is already live, by another route, while
    ``execution_record.py`` records that OrderWatchdog is "the only owner of adjudication,
    and introducing a second one would be worse than the defect".

    Note the asymmetry that remains OrderWatchdog's own, not this service's: the watchdog
    has NO leader election, so both gunicorn workers do run its loop concurrently today.
    That is a live condition recorded against OrderWatchdog, and it is an argument for
    fixing the started service rather than for starting a second one.

    The remaining gap this service alone would close is ghost-order detection -- venue
    orders with no DB row. ``_handle_ghost_order`` currently answers that by INSERTing a
    row with ``strategy_id="reconciled"`` and ``price=None``, which is a policy decision
    about fabricating position state on a live-money path, not a wiring fix.
    """
    main_src = MAIN_SRC.read_text(encoding="utf-8")
    # Strip comments before checking: RT-11's own removal of distributed_execution.router
    # left a comment mentioning `exchange_reconciliation_engine.py` (a different module --
    # the unstarted one under backend/distributed_execution/, not this file's
    # backend_app/backend/exchange_reconciliation.py) as a "separate, not-yet-evaluated
    # concern this change does not touch". That comment's text contains this test's own
    # target substring, which made this assertion fail without a single line of code
    # actually wiring the real service into the lifespan -- confirmed directly: no
    # `import ... exchange_reconciliation` and no call referencing it exists in main.py,
    # only that one comment line.
    main_code_only = "\n".join(
        line for line in main_src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "exchange_reconciliation" not in main_code_only, (
        "backend_app/main.py now references exchange_reconciliation. Starting this "
        "service needs the duplicate-worker, tenant-enumeration and ghost-order-"
        "idempotency questions answered first — see this module's docstring."
    )


def test_the_websocket_fallback_does_not_import_a_name_that_does_not_exist():
    """The ImportError that made the WS fallback poll dead code.

    `_fallback_poll` imported `ExchangeReconciliationEngine`. The class is
    `ExchangeReconciliationService`. The import sat inside a bare `try`, so every call
    logged a generic "Fallback poll error" that read like a transient exchange problem.
    """
    reconciler_tree = ast.parse(RECONCILER_SRC.read_text(encoding="utf-8"))
    defined = {
        node.name for node in ast.walk(reconciler_tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    ws_src = WS_LISTENER_SRC.read_text(encoding="utf-8")
    ws_tree = ast.parse(ws_src)

    imported_from_reconciler: set[str] = set()
    for node in ast.walk(ws_tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
            node.module.endswith("exchange_reconciliation")
        ):
            imported_from_reconciler.update(alias.name for alias in node.names)

    missing = imported_from_reconciler - defined
    assert not missing, (
        f"exchange_websocket_listener.py imports {sorted(missing)} from "
        f"exchange_reconciliation, which defines no such name. The import raises "
        f"ImportError and the surrounding `try` hides it."
    )


# ═════════════════════════════════════════════════════════════════════════════
#  2. No exchange client is not the same as no open orders
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_exchange_client_raises_instead_of_reporting_no_open_orders():
    """THE DEFECT. `[]` would make every active DB order look resolved."""
    service = ExchangeReconciliationService(Mock(), exchange_client=None)

    with pytest.raises(Exception) as exc:
        await service._fetch_exchange_orders("tenant-1", "binance")

    assert "exchange" in str(exc.value).lower(), (
        "the refusal should name the missing exchange client"
    )


@pytest.mark.asyncio
async def test_a_genuinely_empty_order_list_is_still_passed_through():
    """NEGATIVE CONTROL. The fix must not turn a real empty list into an error.

    A configured client reporting zero open orders is a real observation and is still
    reported as `[]`. Only the absence of a client is refused. Preserves the behaviour
    `tests/test_exchange_safety_fix.py::test_empty_response_not_assumed` pins.
    """
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(return_value=[])
    service = ExchangeReconciliationService(Mock(), exchange_client=mock_exchange)

    assert await service._fetch_exchange_orders("tenant-1", "binance") == []


@pytest.mark.asyncio
async def test_an_exchange_error_still_propagates():
    """NEGATIVE CONTROL. BUG-FIX UK-01 must remain in force."""
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(
        side_effect=asyncio.TimeoutError("Exchange timeout")
    )
    service = ExchangeReconciliationService(Mock(), exchange_client=mock_exchange)

    with pytest.raises(Exception):
        await service._fetch_exchange_orders("tenant-1", "binance")


# ═════════════════════════════════════════════════════════════════════════════
#  3. Absence from the open-orders list is not evidence of an outcome
# ═════════════════════════════════════════════════════════════════════════════

def _db_order(filled_size: str = "0"):
    """A minimal stand-in for an active ExecutionRecordModel row."""
    order = Mock()
    order.execution_id = "exec_p10d_1"
    order.order_id = "venue-abc"
    order.symbol = "BTC/USDT"
    order.size = "1"
    order.filled_size = filled_size
    order.avg_price = None
    order.status = "EXECUTING"
    return order


class _ClientWithoutFetchOrder:
    """A venue client that cannot answer "what happened to this one order?".

    `hasattr(client, "fetch_order")` is the branch condition in
    `_handle_missing_exchange_order`, so this object selects the fallback path.
    """

    async def fetch_open_orders(self):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filled_size, forbidden",
    [
        ("0.5", ReconciliationAction.MARK_FILLED),
        ("0", ReconciliationAction.MARK_CANCELLED),
    ],
    ids=["partially-filled-order", "unfilled-order"],
)
async def test_an_unconfirmable_missing_order_is_left_alone(
    filled_size, forbidden, monkeypatch
):
    """THE DEFECT. No `fetch_order` means no evidence, so no outcome may be written.

    Previously: a recorded fill made the order COMPLETED, and no recorded fill made it
    FAILED — both from absence alone, with no venue confirmation and no way back.

    Both state-machine transitions are stubbed so the assertion is about the ACTION this
    function chose, not about the transition helpers' own machinery.
    """
    import backend_app.backend.exchange_reconciliation as mod

    monkeypatch.setattr(mod, "transition_to_filled", lambda **kw: None)
    monkeypatch.setattr(mod, "transition_to_cancelled", lambda **kw: None)

    db = Mock()
    service = ExchangeReconciliationService(
        db, exchange_client=_ClientWithoutFetchOrder()
    )

    result = await service._handle_missing_exchange_order(_db_order(filled_size))

    assert result.action is ReconciliationAction.NO_ACTION, (
        f"an order that merely vanished from the open-orders list was resolved as "
        f"{result.action}; the venue was never asked and could not have been"
    )
    assert result.action is not forbidden
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_a_venue_confirmed_fill_is_still_recorded(monkeypatch):
    """NEGATIVE CONTROL. The fix must not stop real reconciliation from working.

    With a client that CAN answer, a confirmed `closed` order is still marked filled —
    so the change above withholds judgement only where evidence is absent.
    """
    import backend_app.backend.exchange_reconciliation as mod

    monkeypatch.setattr(mod, "transition_to_filled", lambda **kw: None)

    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(return_value=[])
    mock_exchange.fetch_order = AsyncMock(
        return_value={"status": "closed", "filled": 1.0, "average": 60000.0}
    )

    db = Mock()
    service = ExchangeReconciliationService(db, exchange_client=mock_exchange)

    result = await service._handle_missing_exchange_order(_db_order("1"))

    assert result.action is ReconciliationAction.MARK_FILLED
    db.commit.assert_called()
