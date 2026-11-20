"""`/ws/pnl`'s background push loop must be paced, and must survive a bad tick.

THE DEFECT THESE PIN. `_push_pnl` was::

    while True:
        try:
            result = await telemetry.get_live_pnl(user_id)
            ...
                    await manager.broadcast_pnl(user_id, pnl_data)
        except asyncio.CancelledError:
            break

Two separate faults in six lines:

1. NO SLEEP ANYWHERE. `get_live_pnl` issues a QuestDB REST query, so the loop
   re-queried QuestDB and re-broadcast to the websocket as fast as a loopback
   HTTP round-trip completes -- hundreds of queries a second for every
   connected client, against a QuestDB sidecar sharing the API's 1 vCPU. One
   authenticated user opening `/ws/pnl/{user_id}` degraded the whole API.

2. ONLY `CancelledError` WAS CAUGHT. Nothing awaits this task (``ws_pnl`` only
   calls ``push_task.cancel()`` in its ``finally``), so any other exception
   ended P&L for that connection for good and showed up only as a
   never-retrieved task exception whenever the task was collected.

The pacing assertions are deterministic rather than wall-clock: `asyncio.sleep`
is replaced with a recorder that cancels after a fixed number of ticks, so what
is asserted is "one sleep per iteration, at the declared interval", which is
the property that matters and does not depend on how loaded the machine is.
"""
import asyncio

import pytest
from unittest.mock import patch

from backend_app.api_ws import ws_routes

#: One QuestDB `live_user_pnl` row, in the shape execute_query returns.
DATASET = {"columns": [{"name": "unrealized_pnl"}], "dataset": [[12.5]]}


class _Telemetry:
    """Returns (or raises) a scripted result per call, holding the last one."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def get_live_pnl(self, user_id):
        self.calls += 1
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


class _Manager:
    def __init__(self):
        self.frames = []

    async def broadcast_pnl(self, user_id, data):
        self.frames.append(data)


class _SleepRecorder:
    """Stands in for asyncio.sleep; cancels the loop after `stop_after` ticks."""

    def __init__(self, stop_after):
        self.delays = []
        self.stop_after = stop_after

    async def __call__(self, delay):
        self.delays.append(delay)
        if len(self.delays) >= self.stop_after:
            raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_push_pnl_sleeps_once_per_iteration_at_the_declared_interval():
    telemetry = _Telemetry([DATASET])
    manager = _Manager()
    sleeper = _SleepRecorder(stop_after=3)

    with patch.object(ws_routes.asyncio, "sleep", sleeper):
        await ws_routes._push_pnl(telemetry, "11111111-1111-1111-1111-111111111111", manager)

    # Three polls, three sleeps: the loop cannot iterate without pacing itself.
    assert telemetry.calls == 3
    assert len(manager.frames) == 3
    assert sleeper.delays == [ws_routes._PNL_PUSH_INTERVAL_SECONDS] * 3, (
        "every iteration must await the declared push interval; an unpaced "
        "iteration is the hot loop this test exists to prevent"
    )


@pytest.mark.asyncio
async def test_the_declared_interval_is_a_real_positive_pause():
    """A zero or negative interval would satisfy the test above while still
    spinning, so the constant itself is pinned."""
    assert ws_routes._PNL_PUSH_INTERVAL_SECONDS > 0
    # Fast enough to look live, slow enough not to be a query amplifier.
    assert 0.1 <= ws_routes._PNL_PUSH_INTERVAL_SECONDS <= 30


@pytest.mark.asyncio
async def test_push_pnl_survives_a_telemetry_failure_and_keeps_pushing():
    """QuestDB being briefly unreachable must cost one frame, not the feed."""
    telemetry = _Telemetry([RuntimeError("questdb connection refused"), DATASET])
    manager = _Manager()
    sleeper = _SleepRecorder(stop_after=3)

    with patch.object(ws_routes.asyncio, "sleep", sleeper):
        await ws_routes._push_pnl(telemetry, "11111111-1111-1111-1111-111111111111", manager)

    assert telemetry.calls == 3, "the loop must keep polling after a failed tick"
    # The first tick raised, so it broadcast nothing; the next two succeeded.
    assert len(manager.frames) == 2
    # And the error path is paced too -- otherwise a persistent QuestDB outage
    # reintroduces the hot loop through the exception handler.
    assert sleeper.delays == [ws_routes._PNL_PUSH_INTERVAL_SECONDS] * 3


@pytest.mark.asyncio
async def test_push_pnl_stops_rather_than_retrying_a_malformed_user_id():
    """`get_live_pnl` raises ValueError from its own `_safe_uuid` guard. That is
    permanent for the connection, so retrying every tick would log forever."""
    telemetry = _Telemetry([ValueError("user_id is not a uuid")])
    manager = _Manager()
    sleeper = _SleepRecorder(stop_after=5)

    with patch.object(ws_routes.asyncio, "sleep", sleeper):
        await ws_routes._push_pnl(telemetry, "'; DROP TABLE --", manager)

    assert telemetry.calls == 1
    assert manager.frames == []
    assert sleeper.delays == [], "a permanent failure should break, not sleep and retry"


@pytest.mark.asyncio
async def test_push_pnl_exits_when_the_route_cancels_it():
    """ws_pnl's finally block calls push_task.cancel(); the loop must end."""
    telemetry = _Telemetry([asyncio.CancelledError()])
    manager = _Manager()
    sleeper = _SleepRecorder(stop_after=5)

    with patch.object(ws_routes.asyncio, "sleep", sleeper):
        await ws_routes._push_pnl(telemetry, "11111111-1111-1111-1111-111111111111", manager)

    assert telemetry.calls == 1
    assert sleeper.delays == []


@pytest.mark.asyncio
async def test_push_pnl_broadcasts_nothing_for_an_empty_dataset():
    """An empty `live_user_pnl` result is not a frame, and must not be sent as
    one -- but it must also not stop the loop."""
    telemetry = _Telemetry([{"columns": [{"name": "unrealized_pnl"}], "dataset": []}])
    manager = _Manager()
    sleeper = _SleepRecorder(stop_after=2)

    with patch.object(ws_routes.asyncio, "sleep", sleeper):
        await ws_routes._push_pnl(telemetry, "11111111-1111-1111-1111-111111111111", manager)

    assert telemetry.calls == 2
    assert manager.frames == []
    assert sleeper.delays == [ws_routes._PNL_PUSH_INTERVAL_SECONDS] * 2
