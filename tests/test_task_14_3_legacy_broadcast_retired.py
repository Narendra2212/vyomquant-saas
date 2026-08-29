"""
tests/test_task_14_3_legacy_broadcast_retired.py

Task 14.3 — the legacy, non-resource-scoped ``ChannelType.SIGNAL_TRACE`` broadcast is
retired FOR THIS TRAFFIC, and for this traffic only.

Spec: trading-lifecycle-integration. `design.md` § WebSocket design ("`Live_Runtime` and
`signal_trace_engine` stop publishing to the legacy, unauthorized
`ChannelType.SIGNAL_TRACE` broadcast for the Signal_Trace_Page's traffic … that legacy
channel is left in place for any other consumer not covered by this spec, unchanged").
Requirement 23.7.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **The Live_Runtime publishes nothing on the legacy channel any more.**
   ``DAGEventLoop._emit_signal`` used to publish a signal-status frame TWICE per emitted
   Signal: in-process through ``ws_event_stream.publish_signal_trace`` (whose ``channel``
   argument is literally ``ChannelType.SIGNAL_TRACE``) and cross-process through
   ``EventPublisher.publish_signal_trace``, whose Redis channel ``ws_server.py``'s
   ``_redis_listener`` fans back out under the same name. Both reached a client over a
   FLAT broadcast that names no owner and no deployment, which is exactly the delivery
   Requirement 23.7 forbids. Both are asserted absent — not by reading the source, but by
   arming every seam as a tripwire and running the method.

2. **Absence is asserted through the real seam, including the async one.** The old code
   published inside ``asyncio.create_task``, so a test that only checked the synchronous
   return would have missed it. Every assertion here drains the loop first, and one test
   asserts directly that the method schedules no background task at all.

3. **Retiring the publish did not retire the method.** The exactly-once Redis guard, the
   ``signals_emitted`` counter and the ``signal_callbacks`` fan-out are all still what
   ``_emit_signal`` does; a "stop publishing" change that quietly stopped emitting would
   pass a naive absence test and break every legacy consumer.

4. **The channel itself is untouched.** ``ChannelType.SIGNAL_TRACE``, its
   ``CHANNEL_EVENTS`` vocabulary, ``ws_event_stream.publish_signal_trace``,
   ``EventPublisher.publish_signal_trace`` and ``ws_server.CHANNELS`` are all asserted
   still present and still pointed at the same channel. 14.3 retires the channel for one
   producer's traffic; deleting the channel would break consumers this spec never
   surveyed, which is why the task says "leave that channel in place … unchanged".

5. **`signal_trace_engine` never had a client delivery seam, and still has none.**
   Requirement 23.7 names it alongside the Live_Runtime. Asserted rather than assumed,
   because "we checked and there was nothing there" is not a fact that survives in a
   reader's head.

6. **The traffic that replaced it goes out exactly once, on the owned channel.** One
   ``submit_signal`` is run with the legacy seams armed AND a recording transport
   attached: every frame is asserted to be on ``signal.{deployment_id}`` through
   ``broadcast_signal_event``, one frame per persisted state and no state twice, and the
   legacy tripwires are asserted never to have fired.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: ``DAGEventLoop._emit_signal``, ``signal_service.submit_signal`` and its publish
seam, ``ws_channels`` (the family, the counter, the frame builder), the legacy channel
declarations.

Doubles, reused rather than re-invented: task 10.2's ``FakeSupabase`` / ``FakeRisk`` /
``FakeExecution`` / ``FakeIdempotencyLayer`` / ``a_signal`` / ``deployment_row``.

NOT tested here: the channel's name, vocabulary and ownership refusal — task 14.1. The
``seq``, the dedup key and the persist-before-report ordering — task 14.2. Neither is
re-asserted; this file only asserts WHERE the frames went and where they did not.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from backend_app.backend import dag_event_loop as DEL
from backend_app.backend import event_publisher as EP
from backend_app.backend import signal_trace_engine as STE
from backend_app.backend import websocket_manager as WM
from backend_app.backend import ws_channels as C
from backend_app.backend import ws_event_stream as WES
from backend_app.backend import signal_service as svc
from backend_app.backend.dag_event_loop import DAGEventLoop, Signal
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.backend.signal_service import submit_signal
from backend_app.core.feature_flags import ExecutionFlags

from tests.test_task_10_2_submit_signal import (  # noqa: E402 - shared doubles
    FakeExecution,
    FakeIdempotencyLayer,
    FakeRisk,
    FakeSupabase,
    a_signal,
    deployment_row,
)

DEPLOYMENT_ID = deployment_row()["id"]
SIGNAL_CHANNEL = f"signal.{DEPLOYMENT_ID}"
LEGACY_CHANNEL = C.ChannelType.SIGNAL_TRACE.value


# ═══════════════════════════════════════════════════════════════════════════
# Tripwires
# ═══════════════════════════════════════════════════════════════════════════


class LegacyTripwires:
    """Every route from this codebase to the legacy broadcast, armed to record a hit.

    Recording rather than raising, deliberately. A raise would be caught by
    ``_emit_signal``'s own containment (and by ``publish_signal_frame``'s, on the other
    path) and reported as a warning, so the test would pass whether the call happened or
    not. A recorder cannot be swallowed.
    """

    def __init__(self):
        self.hits = []

    def arm(self, monkeypatch):
        async def in_process(*args, **kwargs):
            self.hits.append(("ws_event_stream.publish_signal_trace", args, kwargs))
            return None

        async def raw_publish(*args, **kwargs):
            # The layer BELOW the helper: a caller that bypassed
            # ``publish_signal_trace`` and reached for the streamer directly is the same
            # violation, so the seam is armed one level down as well.
            self.hits.append(("ws_streamer.publish_event", args, kwargs))
            return None

        class Recorder:
            async def publish_signal_trace(inner, tenant_id, data):  # noqa: N805
                self.hits.append(("EventPublisher.publish_signal_trace", tenant_id, data))

            async def _publish(inner, channel, data):  # noqa: N805
                self.hits.append(("EventPublisher._publish", channel, data))

        async def get_publisher():
            self.hits.append(("get_event_publisher", None, None))
            return Recorder()

        monkeypatch.setattr(WES, "publish_signal_trace", in_process)
        monkeypatch.setattr(WES.ws_streamer, "publish_event", raw_publish)
        monkeypatch.setattr(EP, "get_event_publisher", get_publisher)

    @property
    def channels(self):
        return [name for name, _a, _k in self.hits]


@pytest.fixture
def tripwires(monkeypatch):
    wires = LegacyTripwires()
    wires.arm(monkeypatch)
    return wires


@pytest.fixture
def loop(monkeypatch):
    """A ``DAGEventLoop`` whose legacy emission path can actually be reached.

    The two things standing between a test and ``_emit_signal``'s body are the STEP-1
    safety flag and the Redis exactly-once set. The flag is flipped (this test is about
    the publish, not about the lockdown) and the Redis pair is replaced with an
    in-memory set that records the same two facts, because no Redis exists here.
    """
    monkeypatch.setattr(ExecutionFlags, "EVENT_LOOP_TRADING_ENABLED", True)

    made = DAGEventLoop(
        dag_nodes=[{"id": "n1", "type": "indicator", "label": "RSI"}],
        symbols=["BTC/USDT"],
        tenant_id="tenant-1",
    )
    made.marked = set()

    async def check(tenant_id, signal_id):
        return signal_id in made.marked

    async def mark(tenant_id, signal_id, ttl_seconds=86400):
        made.marked.add(signal_id)

    monkeypatch.setattr(made, "_check_signal_executed", check)
    monkeypatch.setattr(made, "_mark_signal_executed", mark)
    return made


def a_dag_signal(**overrides):
    fields = {
        "timestamp": datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
        "symbol": "BTC/USDT",
        "action": "buy",
        "strength": 0.8,
    }
    fields.update(overrides)
    return Signal(**fields)


async def drain():
    """Let anything ``asyncio.create_task``'d actually run before asserting absence."""
    for _ in range(5):
        await asyncio.sleep(0)


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE LIVE_RUNTIME NO LONGER REACHES THE LEGACY BROADCAST — Req 23.7
# ═══════════════════════════════════════════════════════════════════════════


class TestTheLiveRuntimeStopsPublishing:
    @pytest.mark.asyncio
    async def test_emitting_a_signal_touches_no_legacy_route(self, loop, tripwires):
        """The whole of Requirement 23.7 for this producer, in one assertion.

        Both routes are armed: the in-process helper (``ChannelType.SIGNAL_TRACE``
        directly) and the Redis publisher whose channel the dedicated WS server fans
        back out under the same name.
        """
        await loop._emit_signal(a_dag_signal())
        await drain()

        assert tripwires.hits == [], (
            f"the Live_Runtime still reached the legacy signal_trace broadcast via "
            f"{tripwires.channels}"
        )

    @pytest.mark.asyncio
    async def test_emitting_many_signals_touches_no_legacy_route(self, loop, tripwires):
        """A per-signal publish would show up on the second one even if the first were
        somehow guarded."""
        for minute in range(4):
            await loop._emit_signal(
                a_dag_signal(timestamp=datetime(2024, 5, 1, 12, minute))
            )
        await drain()

        assert tripwires.hits == []
        assert loop.signals_emitted == 4

    @pytest.mark.asyncio
    async def test_emitting_a_signal_schedules_no_background_publish(self, loop, tripwires):
        """The old publishes were ``asyncio.create_task``'d.

        Asserted separately from the tripwires because a task that is created and never
        awaited is a different failure from a call that is made: it escapes the method's
        own ``except``, and a test that only drained the loop after patching could still
        be fooled by a task that resolves a name at call time.
        """
        before = {t for t in asyncio.all_tasks() if not t.done()}

        await loop._emit_signal(a_dag_signal())

        created = {t for t in asyncio.all_tasks() if not t.done()} - before
        assert created == set(), f"_emit_signal scheduled {created}"
        await drain()
        assert tripwires.hits == []

    @pytest.mark.asyncio
    async def test_a_loop_with_no_dag_nodes_publishes_nothing_either(
        self, monkeypatch, tripwires
    ):
        """The retired block had a fabricating ``else`` branch for exactly this shape.

        With no ``dag_nodes`` it invented a hardcoded ``RSI 65.0`` indicator reading and
        published it as though measured. There is no branch left to reach.
        """
        monkeypatch.setattr(ExecutionFlags, "EVENT_LOOP_TRADING_ENABLED", True)
        bare = DAGEventLoop(dag_nodes=[], symbols=["BTC/USDT"], tenant_id="t")

        async def yes(*a, **k):
            return False

        async def noop(*a, **k):
            return None

        monkeypatch.setattr(bare, "_check_signal_executed", yes)
        monkeypatch.setattr(bare, "_mark_signal_executed", noop)

        await bare._emit_signal(a_dag_signal())
        await drain()

        assert tripwires.hits == []


class TestTheEmissionItselfStillWorks:
    """A "stop publishing" change that stopped EMITTING would pass every test above."""

    @pytest.mark.asyncio
    async def test_registered_callbacks_still_receive_the_signal(self, loop, tripwires):
        received = []
        loop.add_signal_callback(received.append)

        signal = a_dag_signal()
        await loop._emit_signal(signal)

        assert received == [signal]
        assert loop.signals_emitted == 1

    @pytest.mark.asyncio
    async def test_async_callbacks_are_still_awaited(self, loop, tripwires):
        received = []

        async def handler(signal):
            received.append(signal)

        loop.add_signal_callback(handler)
        await loop._emit_signal(a_dag_signal())

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_the_exactly_once_guard_still_rejects_a_repeat(self, loop, tripwires):
        """The Redis idempotency set is upstream of the retired block, and stayed."""
        received = []
        loop.add_signal_callback(received.append)
        signal = a_dag_signal()

        await loop._emit_signal(signal)
        await loop._emit_signal(signal)  # same symbol, timestamp and node -> same id

        assert len(received) == 1
        assert loop.signals_emitted == 1
        assert tripwires.hits == []


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE CHANNEL IS LEFT IN PLACE, UNCHANGED — the other half of 14.3
# ═══════════════════════════════════════════════════════════════════════════


class TestTheLegacyChannelSurvives:
    def test_the_channel_type_still_exists_and_is_still_valid(self):
        assert C.ChannelType.SIGNAL_TRACE.value == "signal_trace"
        assert C.is_valid_channel("signal_trace") is True

    def test_its_event_vocabulary_is_unchanged(self):
        """Six events, the same six. A consumer switching on these still works."""
        assert C.CHANNEL_EVENTS[C.ChannelType.SIGNAL_TRACE] == {
            "SIGNAL_RECEIVED": C.EventType.SIGNAL_RECEIVED.value,
            "SIGNAL_VALIDATED": C.EventType.SIGNAL_VALIDATED.value,
            "SIGNAL_RISK_CHECKED": C.EventType.SIGNAL_RISK_CHECKED.value,
            "SIGNAL_EXECUTED": C.EventType.SIGNAL_EXECUTED.value,
            "SIGNAL_REJECTED": C.EventType.SIGNAL_REJECTED.value,
            "SIGNAL_FAILED": C.EventType.SIGNAL_FAILED.value,
        }

    def test_the_new_family_shares_no_event_name_with_it(self):
        """So a frame from either channel is unambiguous about which it came from."""
        legacy = set(C.CHANNEL_EVENTS[C.ChannelType.SIGNAL_TRACE].values())
        assert C.SIGNAL_CHANNEL_EVENTS & legacy == set()

    def test_the_in_process_publisher_is_still_exported(self):
        assert "publish_signal_trace" in WES.__all__
        assert callable(WES.publish_signal_trace)

    @pytest.mark.asyncio
    async def test_the_in_process_publisher_still_targets_the_legacy_channel(self):
        """Nothing about the helper moved — only its former caller did."""
        seen = {}

        class Streamer:
            async def publish_event(self, **kwargs):
                seen.update(kwargs)
                return "message"

        result = await WES.publish_signal_trace(
            streamer=Streamer(),
            tenant_id="t",
            bot_id="b",
            strategy_id="s",
            signal_data={"status": "executed"},
        )

        assert result == "message"
        assert seen["channel"] is C.ChannelType.SIGNAL_TRACE
        assert seen["event_type"] is C.EventType.SIGNAL_EXECUTED

    @pytest.mark.asyncio
    async def test_the_redis_publisher_still_targets_the_legacy_channel(self):
        publisher = EP.EventPublisher.__new__(EP.EventPublisher)
        published = []

        async def _publish(channel, data):
            published.append((channel, data))

        publisher._publish = _publish

        await EP.EventPublisher.publish_signal_trace(publisher, "t", {"id": "sig-1"})

        assert published[0][0] == LEGACY_CHANNEL
        assert published[0][1]["payload"] == {"id": "sig-1"}

    def test_the_dedicated_ws_server_still_subscribes_to_it(self):
        """``ws_server.py``'s fanout is the "any other consumer" the task protects."""
        from backend_app.backend import ws_server

        assert LEGACY_CHANNEL in ws_server.CHANNELS

    def test_the_owned_family_still_does_not_claim_the_legacy_name(self):
        """One character apart. Task 14.1 asserted it; retiring the traffic must not
        have changed which of the two a subscription resolves to."""
        assert C.claims_owned_namespace("signal_trace") is False
        assert C.SIGNAL_FAMILY.parse("signal_trace") is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. signal_trace_engine HAS NO CLIENT DELIVERY SEAM — the other name in 23.7
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTraceEngineDeliversToNoClient:
    def test_it_exposes_no_publish_or_broadcast_entry_point(self):
        """Requirement 23.7 names the Signal_Trace_Engine beside the Live_Runtime.

        It records; it has never delivered. Asserted so that a future change which adds
        a broadcast seam to it has to come past this test and read 23.7.
        """
        offenders = [
            name
            for name in dir(STE)
            if any(word in name.lower() for word in ("publish", "broadcast", "streamer"))
        ]
        assert offenders == []

    def test_its_engine_class_exposes_none_either(self):
        offenders = [
            name
            for name in dir(STE.SignalTraceEngine)
            if any(word in name.lower() for word in ("publish", "broadcast", "streamer"))
        ]
        assert offenders == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE REPLACEMENT TRAFFIC: EXACTLY ONCE, ON THE OWNED CHANNEL — Req 18.1
# ═══════════════════════════════════════════════════════════════════════════


class OwnedChannelRecorder:
    """Records what ``signal_service`` published, and on which channel name."""

    def __init__(self):
        self.published = []

    async def broadcast_signal_event(self, deployment_id, event, payload, owner_id=None):
        self.published.append(
            {
                "channel": C.SIGNAL_FAMILY.channel(deployment_id),
                "event": event,
                "frame": payload,
                "owner_id": owner_id,
            }
        )
        return 1


@pytest.fixture(autouse=True)
def _fresh_counters():
    C.reset_signal_sequences()
    yield
    C.reset_signal_sequences()


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


@pytest.fixture
def owned(monkeypatch):
    recorder = OwnedChannelRecorder()
    monkeypatch.setattr(WM, "get_websocket_manager", lambda: recorder)
    return recorder


class TestTheReplacementTraffic:
    @pytest.mark.asyncio
    async def test_a_submission_publishes_only_on_the_per_deployment_channel(
        self, owned, tripwires
    ):
        sb = FakeSupabase()

        reached = await submit_signal(
            a_signal(),
            risk_engine=FakeRisk(),
            execution_engine=FakeExecution(),
            sb=sb,
            idempotency_layer=FakeIdempotencyLayer(),
        )

        assert reached is OrderLifecycleState.SUBMITTED
        assert owned.published, "the submission published nothing anywhere"
        assert {entry["channel"] for entry in owned.published} == {SIGNAL_CHANNEL}
        assert tripwires.hits == [], (
            f"the signal path duplicated its frames onto the legacy broadcast via "
            f"{tripwires.channels}"
        )

    @pytest.mark.asyncio
    async def test_each_state_is_announced_exactly_once(self, owned, tripwires):
        """"Exactly once" is per persisted state, which is what the dedup key names.

        Two frames for one state would be the duplicate publish 14.3 exists to remove,
        just moved onto the new channel instead of the old one.
        """
        sb = FakeSupabase()
        signal = a_signal()

        await submit_signal(
            signal,
            risk_engine=FakeRisk(),
            execution_engine=FakeExecution(),
            sb=sb,
            idempotency_layer=FakeIdempotencyLayer(),
        )

        keys = [entry["frame"]["dedup_key"] for entry in owned.published]
        assert keys == [
            f"{signal.id}:GENERATED",
            f"{signal.id}:PENDING",
            f"{signal.id}:SUBMITTED",
        ]
        assert len(set(keys)) == len(keys)
        assert tripwires.hits == []

    @pytest.mark.asyncio
    async def test_the_frames_name_the_deployment_the_legacy_channel_never_did(
        self, owned, tripwires
    ):
        """The point of the migration, not a restatement of 14.1's naming test.

        The legacy frame carried ``tenant_id`` / ``bot_id`` / ``strategy_id`` and was
        delivered to whoever subscribed to a flat channel name. Every replacement frame
        carries the deployment whose owner the subscription was authorised against.
        """
        sb = FakeSupabase()

        await submit_signal(
            a_signal(),
            risk_engine=FakeRisk(),
            execution_engine=FakeExecution(),
            sb=sb,
            idempotency_layer=FakeIdempotencyLayer(),
        )

        for entry in owned.published:
            assert entry["frame"]["deployment_id"] == DEPLOYMENT_ID
            assert entry["owner_id"] is not None
        assert tripwires.hits == []
