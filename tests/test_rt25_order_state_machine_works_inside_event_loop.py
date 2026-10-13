"""RT-25: OrderStateMachine.transition() used asyncio.run() and crashed under a running loop."""
import asyncio
import threading

import pytest

from backend_app.core.order_state_machine import (
    InvalidStateTransitionError, OrderState, OrderStateMachine)


def test_transition_from_inside_running_event_loop_does_not_raise():
    async def _go():
        m = OrderStateMachine()
        m.transition("e1", OrderState.CREATED, OrderState.SUBMITTED)
        t = m.transition("e1", OrderState.SUBMITTED, OrderState.FILLED)
        return m, t
    m, t = asyncio.run(_go())
    assert t.to_state == OrderState.FILLED
    assert m.get_current_state("e1") == OrderState.FILLED


def test_module_helpers_work_inside_running_event_loop():
    """The exact shape of the reconciliation path: sync helper called from a coroutine."""
    from backend_app.core import order_state_machine as osm
    async def _go():
        osm._state_machine = OrderStateMachine()
        osm.get_order_state_machine().transition("e2", OrderState.CREATED, OrderState.SUBMITTED)
        return osm.transition_to_cancelled(execution_id="e2", reason="x")
    try:
        t = asyncio.run(_go())
    finally:
        osm._state_machine = None
    assert t.to_state == OrderState.CANCELLED


def test_invalid_transition_still_rejected_and_lock_released():
    m = OrderStateMachine()
    m.transition("e3", OrderState.CREATED, OrderState.SUBMITTED)
    m.transition("e3", OrderState.SUBMITTED, OrderState.FILLED)
    with pytest.raises(InvalidStateTransitionError):
        m.transition("e3", OrderState.FILLED, OrderState.PENDING)
    # lock must have been released by the failed call: a second call must not deadlock
    done = []
    th = threading.Thread(target=lambda: (_try(m), done.append(1)))
    th.start(); th.join(2)
    assert done, "lock leaked after InvalidStateTransitionError"


def _try(m):
    try:
        m.transition("e3", OrderState.FILLED, OrderState.PENDING)
    except InvalidStateTransitionError:
        pass


def test_concurrent_transitions_on_one_execution_are_serialised():
    m = OrderStateMachine()
    m.transition("e4", OrderState.CREATED, OrderState.SUBMITTED)
    results = []
    def worker():
        try:
            m.transition("e4", OrderState.SUBMITTED, OrderState.FILLED)
            results.append("ok")
        except InvalidStateTransitionError:
            results.append("invalid")
    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("ok") == 1  # exactly one winner; the rest see the terminal state
