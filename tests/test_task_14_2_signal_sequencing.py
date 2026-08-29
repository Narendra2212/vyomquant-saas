"""
tests/test_task_14_2_signal_sequencing.py

Task 14.2 — the per-deployment ``seq``, the content-level dedup key, and the wiring
that publishes ``GENERATED`` / ``STATUS_CHANGED`` / ``SNAPSHOT`` on
``signal.{deployment_id}``.

Spec: trading-lifecycle-integration. `design.md` § WebSocket design → "Sequencing,
dedup, ordering, backpressure". Requirements 18.1, 18.4, 23.3, 23.6.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **The sequence is what `websocketClient.js`'s existing gap detector assumes.**
   That client initialises ``expectedSequence = 1`` on every fresh connection,
   discards anything below it and asks for a replay of anything above it. So a
   freshly seeded channel's FIRST frame must be ``1`` — a larger first value is a gap
   the client asks to have filled with frames that were never published, and a hole
   that never closes. Asserted against the constant the client's own contract fixes,
   not against a number this file happens to like.

2. **Requirement 23.6: never the same `seq` twice on one channel, and never a reset
   under a live subscriber.** A reset would hand an already-open page a number it has
   already processed, which the same requirement then has it *discard* — so a reset
   silently drops a real state change. Seeding is therefore idempotent, and the
   counter is released only when the channel's LAST subscriber goes.

3. **Requirement 18.4's dedup key is content-level and IN ADDITION to `seq`.** The two
   fail in opposite directions: ``seq`` catches network duplication and reordering but
   cannot catch a reconnect (a new connection reuses low numbers); the content key
   ``f"{signal_id}:{order_lifecycle_state}"`` catches a replayed snapshot but says
   nothing about order. Both are asserted present on every frame, and the key is
   asserted to *distinguish* one signal's successive states — which is the whole
   reason task 14.1 made a transition its own frame type.

4. **A publish failure never fails a persisted transition.** The record is the truth;
   a frame is observability. Every containment path is exercised with a transport that
   raises, and the assertion is that the transition still committed and the caller
   still got its state.

5. **Nothing is reported before it is persisted (Requirement 14.5).** Asserted
   positionally: at the moment each frame was published, the `signals` UPDATE *and*
   the `order_lifecycle_transitions` INSERT for that hop are already in the database
   double's call log.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: `ws_channels` (the counter, the frame builder), `WebSocketManager` (the
registry, the seeding on subscribe), `signal_service.submit_signal` and
`apply_order_lifecycle_state` (the producers).

Doubles, reused rather than re-invented: task 10.2's `FakeSupabase`, `FakeRisk`,
`FakeExecution` and `FakeIdempotencyLayer`; task 6.7's `FakeSocket`/`manager` fixture.

NOT tested here: the channel's name, vocabulary and ownership refusal — task 14.1,
`tests/test_task_14_1_signal_channel.py`. The client-side reducer that applies the
dedup key (Property 16) — the frontend's own task. The retirement of the legacy
`ChannelType.SIGNAL_TRACE` broadcast — task 14.3.
"""

import threading

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend import signal_service as svc
from backend_app.backend import ws_channels as C
from backend_app.backend import websocket_manager as WM
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleRejected,
    OrderLifecycleState,
)
from backend_app.backend.signal_service import (
    ExecutionOutcome,
    RiskVerdict,
    apply_order_lifecycle_state,
    submit_signal,
)
from backend_app.core.distributed_idempotency import DuplicateOrderError

from tests.test_task_10_2_submit_signal import (  # noqa: E402 - shared doubles
    FakeExecution,
    FakeIdempotencyLayer,
    FakeRisk,
    FakeSupabase,
    a_signal,
    deployment_row,
)
from tests.test_training_realtime_channels import (  # noqa: E402 - shared doubles
    connected,
    manager,  # noqa: F401 - fixture
)

DEPLOYMENT_ID = "dep-1111"
OWNER_ID = "user-aaaa"
SIGNAL_CHANNEL = f"signal.{DEPLOYMENT_ID}"


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures and doubles
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _fresh_counters():
    """Every test starts from "no channel has published anything"."""
    C.reset_signal_sequences()
    yield
    C.reset_signal_sequences()


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Both 005b verdicts are cached per process; each test probes for itself."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


class RecordingTransport:
    """A ``WebSocketManager`` stand-in that records every frame the producer published.

    Records the database call log's LENGTH at publish time as well, which is how the
    "persist before reporting" assertions below are positional rather than a matter of
    reading the source: a frame published at index ``n`` can be checked against the
    calls that had already happened by ``n``.
    """

    def __init__(self, *, sb=None, raises=None, delivered=1):
        self.sb = sb
        self.raises = raises
        self.delivered = delivered
        self.published = []

    async def broadcast_signal_event(self, deployment_id, event, payload, owner_id=None):
        self.published.append(
            {
                "deployment_id": deployment_id,
                "event": event,
                "frame": payload,
                "owner_id": owner_id,
                "db_calls": len(self.sb.calls) if self.sb is not None else 0,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.delivered

    # ── inspection ──
    def events(self):
        return [entry["event"] for entry in self.published]

    def frames(self, event):
        return [e["frame"] for e in self.published if e["event"] == event]

    def seqs(self):
        return [entry["frame"]["seq"] for entry in self.published]

    def dedup_keys(self):
        return [entry["frame"]["dedup_key"] for entry in self.published]


@pytest.fixture
def transport(monkeypatch):
    """Install a recording transport as the process singleton the producer resolves."""
    recorder = RecordingTransport()

    def _get():
        return recorder

    monkeypatch.setattr(WM, "get_websocket_manager", _get)
    return recorder


async def submit(signal, *, sb, risk=None, execution=None, layer=None):
    return await submit_signal(
        signal,
        risk_engine=risk if risk is not None else FakeRisk(),
        execution_engine=execution if execution is not None else FakeExecution(),
        sb=sb,
        idempotency_layer=layer if layer is not None else FakeIdempotencyLayer(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE COUNTER — Requirement 23.6
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSequenceCounter:
    def test_a_freshly_seeded_channel_starts_at_one(self):
        """`websocketClient.js` sets ``expectedSequence = 1`` on every new connection.

        A first frame above that is a gap the client asks to have replayed; a first
        frame below it is discarded as a duplicate. Either way the page loses state.
        """
        assert C.SIGNAL_SEQUENCE_START == 1

        C.seed_signal_sequence(DEPLOYMENT_ID)

        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 2
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 3

    def test_seeding_reports_the_position_it_found(self):
        """So a caller can tell a fresh seed from a counter that was already running."""
        assert C.seed_signal_sequence(DEPLOYMENT_ID) == C.SIGNAL_SEQUENCE_START - 1

        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)

        assert C.seed_signal_sequence(DEPLOYMENT_ID) == 2

    def test_seeding_without_restart_never_resets_the_counter(self):
        """The load-bearing case. A reset would DROP state changes, not repeat them.

        Requirement 23.6 has a client discard a frame whose ``seq`` it has already
        processed — so restarting the counter under a live subscriber makes the next
        real transition invisible to it.
        """
        C.seed_signal_sequence(DEPLOYMENT_ID)
        first = [C.next_signal_sequence(DEPLOYMENT_ID) for _ in range(3)]

        C.seed_signal_sequence(DEPLOYMENT_ID)  # a second page opens

        assert C.next_signal_sequence(DEPLOYMENT_ID) == 4
        assert first == [1, 2, 3]

    def test_restart_is_opt_in_and_returns_the_channel_to_one(self):
        """For the FIRST subscriber only, whose ``expectedSequence`` is 1.

        A producer publishing to a channel nobody was watching has already advanced
        the counter, and those frames reached nobody — so restarting loses nothing and
        not restarting would leave the arriving page waiting for a replay of them.
        """
        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)

        assert C.seed_signal_sequence(DEPLOYMENT_ID, restart=True) == (
            C.SIGNAL_SEQUENCE_START - 1
        )
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

    def test_the_counter_is_scoped_to_one_deployment(self):
        """"per channel" in Requirement 23.6 is per deployment, not per process."""
        C.seed_signal_sequence("dep-a")
        C.seed_signal_sequence("dep-b")

        assert C.next_signal_sequence("dep-a") == 1
        assert C.next_signal_sequence("dep-b") == 1
        assert C.next_signal_sequence("dep-a") == 2
        assert C.next_signal_sequence("dep-b") == 2

    def test_publishing_to_an_unsubscribed_channel_still_sequences(self):
        """A producer must not have to know whether a browser is attached.

        An unseeded channel is seeded here rather than refused; the frame simply
        reaches nobody.
        """
        assert C.next_signal_sequence("dep-nobody-watching") == 1

    def test_releasing_restarts_the_next_subscription_at_one(self):
        """Requirement 23.6 only needs monotonicity for a subscription's lifetime.

        Which is exactly why no persistent sequence table is needed: the client resets
        ``expectedSequence`` at the same moment.
        """
        C.seed_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)

        C.release_signal_sequence(DEPLOYMENT_ID)
        C.seed_signal_sequence(DEPLOYMENT_ID)

        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

    def test_releasing_a_channel_that_never_existed_is_not_an_error(self):
        C.release_signal_sequence("dep-never-seen")
        C.release_signal_sequence("not a valid id")
        C.release_signal_sequence(None)

    @pytest.mark.parametrize("bad", ["", "a.b", "a b", "*", "x" * 65, None])
    def test_an_identifier_no_channel_could_carry_is_refused(self, bad):
        """The shape rule is `SIGNAL_FAMILY`'s, applied rather than restated."""
        with pytest.raises(ValueError):
            C.next_signal_sequence(bad)
        with pytest.raises(ValueError):
            C.seed_signal_sequence(bad)

    def test_seeding_from_the_row_the_lookup_read(self):
        """"seeded from the deployment's own row": the row is checked, not re-fetched."""
        row = deployment_row()

        assert C.seed_signal_sequence(row["id"], row) == C.SIGNAL_SEQUENCE_START - 1
        assert C.next_signal_sequence(row["id"]) == 1

    def test_a_row_naming_another_deployment_is_refused(self):
        """One deployment's ownership must not open another deployment's counter."""
        with pytest.raises(ValueError, match="names"):
            C.seed_signal_sequence(DEPLOYMENT_ID, deployment_row(id="dep-somebody-else"))

    def test_concurrent_assignment_hands_out_no_number_twice(self):
        """Requirement 23.6's uniqueness has to survive two threads asking at once.

        Two frames sharing a ``seq`` on one channel is precisely what the requirement
        forbids, and a bare ``+= 1`` on a dict entry is not atomic.
        """
        C.seed_signal_sequence(DEPLOYMENT_ID)
        handed_out = []
        lock = threading.Lock()

        def take():
            for _ in range(200):
                value = C.next_signal_sequence(DEPLOYMENT_ID)
                with lock:
                    handed_out.append(value)

        threads = [threading.Thread(target=take) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(handed_out) == 800
        assert len(set(handed_out)) == 800
        assert sorted(handed_out) == list(range(1, 801))


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE FRAME — Requirement 18.4's two keys, and a payload that re-derives nothing
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSignalFrame:
    def test_the_payload_is_to_public_dict_forwarded_field_for_field(self):
        """Task 10.1's closed field set is what reaches the wire, unmodified.

        Re-projecting it here would be a second place for a credential-bearing field
        to be admitted, which is what makes Requirement 20.3 hold for free.
        """
        signal = a_signal()

        frame = C.signal_frame(C.SignalEvent.GENERATED, signal)

        assert frame["signal"] == signal.to_public_dict()

    def test_every_frame_carries_seq_and_the_content_key(self):
        """Requirement 18.4 + 23.6. Both, on every frame, not one or the other."""
        signal = a_signal()

        for event in C.SignalEvent:
            frame = C.signal_frame(event, signal)
            assert frame["type"] == event.value
            assert frame["channel"] == SIGNAL_CHANNEL
            assert frame["deployment_id"] == DEPLOYMENT_ID
            assert isinstance(frame["seq"], int) and frame["seq"] >= 1
            assert frame["dedup_key"] == f"{signal.id}:GENERATED"
            assert frame["signal_id"] == signal.id
            assert frame["order_lifecycle_state"] == "GENERATED"

    def test_the_content_key_is_the_designs_signal_id_colon_state(self):
        signal = a_signal()

        assert C.signal_dedup_key(signal.id, OrderLifecycleState.SUBMITTED) == (
            f"{signal.id}:SUBMITTED"
        )
        # The enum, its value and a bare string all produce the same key, so the two
        # sides of the boundary cannot disagree about what a replay looks like.
        assert (
            C.signal_dedup_key(signal.id, OrderLifecycleState.SUBMITTED)
            == C.signal_dedup_key(signal.id, "SUBMITTED")
        )

    def test_the_content_key_distinguishes_one_signals_successive_states(self):
        """Requirement 18.4 must not collapse a transition into its predecessor.

        This is why task 14.1 made ``STATUS_CHANGED`` its own frame type: a key that
        did not change with the state would have the page discard the fill.
        """
        signal = a_signal()
        keys = {
            C.signal_dedup_key(signal.id, state)
            for state in (
                OrderLifecycleState.GENERATED,
                OrderLifecycleState.PENDING,
                OrderLifecycleState.SUBMITTED,
                OrderLifecycleState.EXECUTED,
            )
        }

        assert len(keys) == 4

    def test_the_content_key_is_stable_across_a_replay(self):
        """A snapshot replayed after reconnect must carry the key the page already has.

        That is the only thing that makes it discardable independently of ``seq``,
        which a reconnect necessarily resets.
        """
        signal = a_signal()

        live = C.signal_frame(C.SignalEvent.STATUS_CHANGED, signal)
        C.release_signal_sequence(DEPLOYMENT_ID)
        replayed = C.signal_frame(C.SignalEvent.SNAPSHOT, signal)

        assert replayed["seq"] <= live["seq"], "a reconnect reuses low sequence numbers"
        assert replayed["dedup_key"] == live["dedup_key"]

    def test_a_frame_can_be_built_from_the_mapping_to_public_dict_returned(self):
        """Same disposition as ``runtime_state_frame``: the caller's value, not a re-read."""
        signal = a_signal()

        frame = C.signal_frame(C.SignalEvent.SNAPSHOT, signal.to_public_dict())

        assert frame["signal"] == signal.to_public_dict()
        assert frame["channel"] == SIGNAL_CHANNEL

    def test_the_sequence_advances_once_per_frame(self):
        signal = a_signal()

        seqs = [C.signal_frame(C.SignalEvent.STATUS_CHANGED, signal)["seq"] for _ in range(4)]

        assert seqs == [1, 2, 3, 4]

    def test_an_explicit_seq_is_honoured_and_does_not_consume_one(self):
        signal = a_signal()

        assert C.signal_frame(C.SignalEvent.SNAPSHOT, signal, seq=7)["seq"] == 7
        assert C.signal_frame(C.SignalEvent.SNAPSHOT, signal)["seq"] == 1

    def test_another_familys_vocabulary_is_refused(self):
        """A producer that reached for the wrong event name is a bug, not a frame."""
        signal = a_signal()

        for event in ["execution.fill", "deployment.state", "signal.invented"]:
            with pytest.raises(ValueError):
                C.signal_frame(event, signal)

    def test_a_deployment_id_contradicting_the_payload_is_refused(self):
        """A frame must not claim to be about a deployment its payload denies."""
        signal = a_signal()

        with pytest.raises(ValueError, match="disagrees"):
            C.signal_frame(C.SignalEvent.GENERATED, signal, deployment_id="dep-other")

    def test_a_signal_with_no_deployment_has_no_channel(self):
        signal = a_signal()
        orphan = dict(signal.to_public_dict())
        orphan["deployment_id"] = None

        with pytest.raises(ValueError, match="names no deployment"):
            C.signal_frame(C.SignalEvent.GENERATED, orphan)

    def test_something_that_is_not_a_signal_is_refused(self):
        with pytest.raises(TypeError):
            C.signal_frame(C.SignalEvent.GENERATED, object())

    def test_a_transition_frame_names_the_state_it_came_from(self):
        """So the page can order two frames for one signal without re-reading the row."""
        signal = a_signal()

        frame = C.signal_frame(
            C.SignalEvent.STATUS_CHANGED,
            signal,
            previous_state=OrderLifecycleState.PENDING,
            reason="order submitted",
        )

        assert frame["previous_state"] == "PENDING"
        assert frame["reason"] == "order submitted"


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE TRANSPORT — seeded on subscribe, released with the last subscriber
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTransportSeeding:
    @pytest.mark.asyncio
    async def test_subscribing_seeds_so_the_first_frame_is_seq_one(self, manager):
        socket, _ = await connected(manager, user_id=OWNER_ID)
        assert manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL) is True

        frame = C.signal_frame(C.SignalEvent.GENERATED, a_signal())
        delivered = await manager.broadcast_signal_event(
            DEPLOYMENT_ID, frame["type"], frame, owner_id=OWNER_ID
        )

        assert delivered == 1
        assert socket.sent[-1]["seq"] == 1
        assert socket.sent[-1]["dedup_key"].endswith(":GENERATED")

    @pytest.mark.asyncio
    async def test_the_first_page_starts_at_one_even_after_unwatched_traffic(self, manager):
        """The hole this would otherwise leave: a page arriving mid-deployment.

        A running deployment publishes whether or not a browser is attached, so the
        counter is already past 1 by the time the Signal_Trace_Page opens. That page's
        ``expectedSequence`` is 1, and a frame above it is a gap it will sit waiting to
        have replayed — with frames that reached nobody.
        """
        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)

        socket, _ = await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)

        frame = C.signal_frame(C.SignalEvent.SNAPSHOT, a_signal())
        await manager.broadcast_signal_event(
            DEPLOYMENT_ID, frame["type"], frame, owner_id=OWNER_ID
        )

        assert socket.sent[-1]["seq"] == 1

    @pytest.mark.asyncio
    async def test_a_second_page_on_the_same_channel_does_not_restart_the_sequence(
        self, manager
    ):
        """Two tabs, one deployment. The first tab must not be shown a stale ``seq``."""
        first, _ = await connected(manager, user_id=OWNER_ID, client_id="c1")
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

        second, _ = await connected(manager, user_id=OWNER_ID, client_id="c2")
        manager.subscribe_owned(OWNER_ID, "c2", SIGNAL_CHANNEL)

        assert C.next_signal_sequence(DEPLOYMENT_ID) == 2

    @pytest.mark.asyncio
    async def test_the_counter_survives_one_of_two_subscribers_leaving(self, manager):
        await connected(manager, user_id=OWNER_ID, client_id="c1")
        await connected(manager, user_id=OWNER_ID, client_id="c2")
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)
        manager.subscribe_owned(OWNER_ID, "c2", SIGNAL_CHANNEL)
        C.next_signal_sequence(DEPLOYMENT_ID)

        manager.unsubscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)

        assert manager.owned_subscriber_count(SIGNAL_CHANNEL) == 1
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 2, (
            "a subscriber still holds an expectedSequence for this channel"
        )

    @pytest.mark.asyncio
    async def test_the_last_subscriber_leaving_releases_the_counter(self, manager):
        await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)
        C.next_signal_sequence(DEPLOYMENT_ID)
        C.next_signal_sequence(DEPLOYMENT_ID)

        manager.unsubscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)

        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

    @pytest.mark.asyncio
    async def test_a_disconnect_releases_it_too(self, manager):
        """Requirement 23.4 removes the subscription; the counter goes with it."""
        await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)
        C.next_signal_sequence(DEPLOYMENT_ID)

        manager.disconnect(OWNER_ID, "c1")

        assert manager.owned_subscriber_count(SIGNAL_CHANNEL) == 0
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

    @pytest.mark.asyncio
    async def test_subscribing_to_a_sibling_channel_seeds_nothing(self, manager):
        """`deployment.*` and `execution.*` share the identifier and carry no ``seq``."""
        await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", f"execution.{DEPLOYMENT_ID}")
        manager.subscribe_owned(OWNER_ID, "c1", f"deployment.{DEPLOYMENT_ID}")

        # Nothing was seeded, so the first signal frame still starts at 1.
        assert C.next_signal_sequence(DEPLOYMENT_ID) == 1

    @pytest.mark.asyncio
    async def test_broadcast_signal_event_refuses_a_foreign_vocabulary(self, manager):
        socket, _ = await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)

        assert await manager.broadcast_signal_event(
            DEPLOYMENT_ID, "execution.fill", {"seq": 1}
        ) == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_broadcast_signal_event_refuses_a_malformed_deployment_id(self, manager):
        await connected(manager, user_id=OWNER_ID)
        manager.subscribe_owned(OWNER_ID, "c1", SIGNAL_CHANNEL)

        assert await manager.broadcast_signal_event(
            "not a valid id", "signal.generated", {"seq": 1}
        ) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE PRODUCER — Requirement 18.1, and 14.5's "persist before reporting"
# ═══════════════════════════════════════════════════════════════════════════


class TestSubmitSignalPublishes:
    @pytest.mark.asyncio
    async def test_a_submission_announces_generated_then_every_transition(self, transport):
        """Requirement 18.1: the page learns each state change as it is persisted."""
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()

        reached = await submit(signal, sb=sb)

        assert reached is OrderLifecycleState.SUBMITTED
        assert transport.events() == [
            "signal.generated",
            "signal.status_changed",  # GENERATED -> PENDING
            "signal.status_changed",  # PENDING   -> SUBMITTED
        ]
        assert transport.dedup_keys() == [
            f"{signal.id}:GENERATED",
            f"{signal.id}:PENDING",
            f"{signal.id}:SUBMITTED",
        ]

    @pytest.mark.asyncio
    async def test_every_frame_of_one_submission_carries_a_distinct_rising_seq(
        self, transport
    ):
        """Requirement 23.6, end to end through the real producer."""
        sb = FakeSupabase()
        transport.sb = sb

        await submit(a_signal(), sb=sb)

        seqs = transport.seqs()
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        assert seqs[0] == 1

    @pytest.mark.asyncio
    async def test_each_frame_is_published_after_that_hops_write_and_audit(self, transport):
        """Requirement 14.5, asserted positionally rather than by reading the source."""
        sb = FakeSupabase()
        transport.sb = sb

        await submit(a_signal(), sb=sb)

        for entry in transport.published:
            state = entry["frame"]["order_lifecycle_state"]
            already = sb.calls[: entry["db_calls"]]
            audited = [
                call["payload"]["to_state"]
                for call in already
                if call["table"] == "order_lifecycle_transitions"
                and call["verb"] == "insert"
            ]
            assert state in audited, (
                f"a {entry['event']} frame reported {state} before its history row "
                f"was written"
            )

    @pytest.mark.asyncio
    async def test_a_multi_hop_fill_announces_both_transitions_it_wrote(self, transport):
        """``PENDING -> SUBMITTED -> EXECUTED`` is two persisted facts, so two frames.

        And their content keys differ, which is what lets the page apply both.
        """
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()
        execution = FakeExecution(
            outcome=ExecutionOutcome(
                accepted=True, order_id="ord-9", filled=0.25, quantity=0.25
            )
        )

        reached = await submit(signal, sb=sb, execution=execution)

        assert reached is OrderLifecycleState.EXECUTED
        assert transport.dedup_keys() == [
            f"{signal.id}:GENERATED",
            f"{signal.id}:PENDING",
            f"{signal.id}:SUBMITTED",
            f"{signal.id}:EXECUTED",
        ]

    @pytest.mark.asyncio
    async def test_a_risk_refusal_is_announced_as_a_transition(self, transport):
        """A failure IS an ``order_lifecycle_state``; there is no ``signal.rejected``."""
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()
        risk = FakeRisk(RiskVerdict(approved=False, reason="exposure cap"))

        reached = await submit(signal, sb=sb, risk=risk)

        assert reached is OrderLifecycleState.REJECTED
        assert transport.events() == [
            "signal.generated",
            "signal.status_changed",
            "signal.status_changed",
        ]
        assert transport.dedup_keys()[-1] == f"{signal.id}:REJECTED"

    @pytest.mark.asyncio
    async def test_an_indeterminate_failure_announces_no_transition_it_did_not_write(
        self, transport
    ):
        """The signal is HELD at PENDING and nothing is written, so nothing is claimed.

        A frame here would tell the page a transition happened that the crash-recovery
        sweep has not yet resolved.
        """
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()
        execution = FakeExecution(
            outcome=ExecutionOutcome(accepted=False, failure_kind="indeterminate")
        )

        reached = await submit(signal, sb=sb, execution=execution)

        assert reached is OrderLifecycleState.PENDING
        assert transport.dedup_keys() == [
            f"{signal.id}:GENERATED",
            f"{signal.id}:PENDING",
        ]

    @pytest.mark.asyncio
    async def test_a_state_read_back_is_a_snapshot_and_not_a_transition(self, transport):
        """``DuplicateOrderError``: another attempt owns the key and wrote the state.

        This call caused nothing, so the frame is a resync (Requirements 18.6, 23.6)
        rather than history the page should append.
        """
        sb = FakeSupabase(signals_row={"order_lifecycle_state": "SUBMITTED"})
        transport.sb = sb
        signal = a_signal()
        layer = FakeIdempotencyLayer(raises=DuplicateOrderError("already guarded"))

        reached = await submit(signal, sb=sb, layer=layer)

        assert reached is OrderLifecycleState.SUBMITTED
        assert transport.events() == ["signal.generated", "signal.snapshot"]
        snapshot = transport.frames("signal.snapshot")[0]
        assert snapshot["dedup_key"] == f"{signal.id}:SUBMITTED"
        assert snapshot["order_lifecycle_state"] == "SUBMITTED"

    @pytest.mark.asyncio
    async def test_the_withhold_on_an_already_advanced_signal_is_a_snapshot(self, transport):
        """Requirement 19.5's withhold writes NOTHING when the row moved past PENDING.

        So there is no transition to announce — only current state to resync to.
        """
        sb = FakeSupabase(signals_row={"order_lifecycle_state": "SUBMITTED"})
        transport.sb = sb
        signal = a_signal()
        layer = FakeIdempotencyLayer(raises=RuntimeError("redis unreachable"))

        reached = await submit(signal, sb=sb, layer=layer)

        assert reached is OrderLifecycleState.SUBMITTED
        assert transport.events() == ["signal.generated", "signal.snapshot"]

    @pytest.mark.asyncio
    async def test_the_frame_names_the_owner_so_the_transport_can_recheck(self, transport):
        sb = FakeSupabase()
        transport.sb = sb

        await submit(a_signal(), sb=sb)

        assert {entry["owner_id"] for entry in transport.published} == {OWNER_ID}
        assert {entry["deployment_id"] for entry in transport.published} == {DEPLOYMENT_ID}

    @pytest.mark.asyncio
    async def test_the_payload_is_the_signals_own_public_projection(self, transport):
        """One shape reaches the frontend: the REST response's and the frame's."""
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()

        await submit(signal, sb=sb)

        generated = transport.frames("signal.generated")[0]
        assert generated["signal"] == signal.to_public_dict()
        # And no credential-bearing field could have been introduced, because the
        # field set is task 10.1's closed one.
        assert set(generated["signal"]) == set(signal.to_public_dict())


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONTAINMENT — a lost frame is never a lost transition
# ═══════════════════════════════════════════════════════════════════════════


class TestPublishFailureIsContained:
    @pytest.mark.asyncio
    async def test_a_transport_that_raises_does_not_fail_the_submission(
        self, monkeypatch, caplog
    ):
        """The record is the truth; a frame is observability."""
        sb = FakeSupabase()
        recorder = RecordingTransport(sb=sb, raises=RuntimeError("socket exploded"))
        monkeypatch.setattr(WM, "get_websocket_manager", lambda: recorder)

        with caplog.at_level("WARNING"):
            reached = await submit(a_signal(), sb=sb)

        assert reached is OrderLifecycleState.SUBMITTED
        assert len(sb.transitions()) == 3  # genesis, PENDING, SUBMITTED
        assert "could not be published" in caplog.text

    @pytest.mark.asyncio
    async def test_no_manager_at_all_does_not_fail_a_transition(self, monkeypatch):
        """A worker with no WebSocket layer wired still submits orders."""
        sb = FakeSupabase()

        def _explode():
            raise RuntimeError("no websocket layer in this process")

        monkeypatch.setattr(WM, "get_websocket_manager", _explode)

        reached = await submit(a_signal(), sb=sb)

        assert reached is OrderLifecycleState.SUBMITTED

    @pytest.mark.asyncio
    async def test_an_unbuildable_frame_does_not_fail_a_transition(self, monkeypatch):
        """A producer bug in the frame builder must not become a failed order."""
        sb = FakeSupabase()
        recorder = RecordingTransport(sb=sb)
        monkeypatch.setattr(WM, "get_websocket_manager", lambda: recorder)
        monkeypatch.setattr(
            C, "signal_frame", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
        )

        reached = await submit(a_signal(), sb=sb)

        assert reached is OrderLifecycleState.SUBMITTED
        assert recorder.published == []

    @pytest.mark.asyncio
    async def test_a_signal_with_no_deployment_publishes_nothing_and_still_transitions(
        self, transport
    ):
        """A signal minted outside a deployment has no channel; that is not an error."""
        sb = FakeSupabase()
        transport.sb = sb
        signal = svc.mint_signal(
            deployment_row(id=None), {"decision": "BUY", "quantity": 1.0}
        )

        current = await apply_order_lifecycle_state(
            sb, signal, OrderLifecycleState.PENDING, reason="test"
        )

        assert current.order_lifecycle_state is OrderLifecycleState.PENDING
        assert transport.published == []

    @pytest.mark.asyncio
    async def test_a_refused_transition_announces_nothing(self, transport):
        """Requirement 16.6: an illegal hop writes nothing, so it reports nothing."""
        sb = FakeSupabase()
        transport.sb = sb
        signal = a_signal()

        with pytest.raises(OrderLifecycleRejected):
            await apply_order_lifecycle_state(
                sb, signal, OrderLifecycleState.CLOSED, reason="not an edge"
            )

        assert transport.published == []


# ═══════════════════════════════════════════════════════════════════════════
# 6. The server half of Property 16 — the identity a duplicate is keyed by
#
#  `design.md` Property 16 is a claim about the CLIENT: "the client applies each
#  distinct identified state change (keyed by its own stable identity — a signal/state
#  pair, or a channel/sequence pair) at most once". Both of those identities are
#  assigned HERE, and the claim is unprovable on the client if either is ambiguous on
#  the server. What follows is that precondition, over random channel/state traffic:
#  no channel hands out a ``seq`` twice while its subscription is open, and the content
#  key is a function of the signal/state pair and of nothing else.
#
#  **Validates: Requirements 23.6, 18.4**
# ═══════════════════════════════════════════════════════════════════════════


deployment_ids = st.sampled_from(["dep-a", "dep-b", "dep-c", "dep_d", "DEP-e"])
lifecycle_states = st.sampled_from([state for state in OrderLifecycleState])
signal_ids = st.text(
    alphabet="abcdef0123456789-", min_size=4, max_size=36
).filter(lambda text: text.strip("-") != "")


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(traffic=st.lists(deployment_ids, min_size=1, max_size=60))
def test_no_channel_ever_repeats_a_sequence_number(traffic):
    """Requirement 23.6, over any interleaving of publishes across channels.

    Interleaved deliberately: a single global counter, or a counter keyed by anything
    coarser than the deployment, would show up here as a channel whose numbers jump or
    as two channels sharing one.
    """
    C.reset_signal_sequences()
    try:
        seen = {}
        for deployment_id in traffic:
            seq = C.next_signal_sequence(deployment_id)
            issued = seen.setdefault(deployment_id, [])
            assert seq not in issued, f"{deployment_id} repeated seq {seq}"
            assert not issued or seq > issued[-1], "seq went backwards"
            issued.append(seq)

        for deployment_id, issued in seen.items():
            # Contiguous from 1, which is what makes the client's gap detector able to
            # tell a hole from a fresh channel.
            assert issued == list(range(1, len(issued) + 1))
    finally:
        C.reset_signal_sequences()


@settings(max_examples=200, deadline=None)
@given(signal_id=signal_ids, first=lifecycle_states, second=lifecycle_states)
def test_the_content_key_identifies_exactly_the_signal_state_pair(
    signal_id, first, second
):
    """Requirement 18.4: the same pair keys the same, and different pairs do not collide.

    Both halves matter. Stability is what lets a reconnect's snapshot be discarded;
    distinctness is what stops a transition being mistaken for one already applied.
    """
    key = C.signal_dedup_key(signal_id, first)

    assert key == C.signal_dedup_key(signal_id, first)
    assert key == C.signal_dedup_key(signal_id, first.value)
    if first is not second:
        assert key != C.signal_dedup_key(signal_id, second)
    assert C.signal_dedup_key(signal_id + "x", first) != key


@settings(max_examples=100, deadline=None)
@given(
    deployment_id=deployment_ids,
    before=st.integers(min_value=0, max_value=20),
    after=st.integers(min_value=1, max_value=20),
)
def test_reseeding_an_open_channel_never_lowers_its_next_sequence(
    deployment_id, before, after
):
    """The reset that would silently drop state changes, ruled out over any history.

    Requirement 23.6 has a client discard a ``seq`` it has already processed, so a
    counter that ever went backwards under a live subscriber would make the next real
    transition invisible to an already-open page. ``restart`` is the one opt-in that
    may lower it, and only the channel's first subscriber passes it — asserted at the
    transport, above.
    """
    C.reset_signal_sequences()
    try:
        C.seed_signal_sequence(deployment_id)
        issued = [C.next_signal_sequence(deployment_id) for _ in range(before)]
        highest = issued[-1] if issued else C.SIGNAL_SEQUENCE_START - 1

        for _ in range(after):
            C.seed_signal_sequence(deployment_id)

        assert C.next_signal_sequence(deployment_id) == highest + 1
    finally:
        C.reset_signal_sequences()
