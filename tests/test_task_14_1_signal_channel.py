"""
tests/test_task_14_1_signal_channel.py

The sixth owned channel family: ``signal.{deployment_id}``.

Spec: trading-lifecycle-integration task 14.1 (`design.md` § WebSocket design → New
Owned_Channel family). Requirements 18.2, 23.1, 23.2.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **The family follows `EXECUTION_FAMILY` exactly, and diverges from it in nothing
   but its namespace and its vocabulary.** The task names that family as the template
   and `design.md` says no field may diverge from the existing four families' response
   conventions, so the shape rule, the malformed-name rule, the resource noun and the
   refusal frame are all asserted against the existing families rather than restated
   as literals this file happens to believe.

2. **No new owner-resolution path.** Requirement 23.1 is answered by the
   ``strategy_deployments`` lookup ``core/websocket_auth`` already performs for
   ``deployment.*`` and ``execution.*``. The assertions here are that the same
   relation is read, the same row, the same owner, and the same refusal — not that a
   new resolver produces a similar-looking answer.

3. **Requirement 23.2: a refusal is not an existence oracle.** A deployment that does
   not exist and one belonging to another tenant must produce byte-identical
   refusals, and neither may carry any resource data.

4. **The legacy `ChannelType.SIGNAL_TRACE` broadcast is untouched.** ``"signal"`` as a
   namespace and ``"signal_trace"`` as a fixed channel name are one character apart
   from colliding; task 14.3 retires the legacy channel for this traffic, and task
   14.1 must not have already broken it.

5. **The five existing families still answer what they answered.** Adding a sixth
   entry to a longest-namespace-first sweep and to a namespace→relation map is exactly
   the kind of change that could reroute an existing name, so the four channels task
   8.5 added and task 6.7's training channel are re-asserted against the extended
   registry.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: `ws_channels`, `WebSocketManager`, `authorize_channel_subscription`.

Doubles, all three reused from task 6.7's file rather than re-invented: the in-memory
`FakeSupabase` (which does NOT enforce RLS, so both the empty-result and the
visible-foreign-row shapes are exercised), `FakeSocket`, and the token decoder.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import ws_channels as C
from backend_app.core import websocket_auth as WA
from backend_app.core.websocket_auth import authorize_channel_subscription

from tests.test_training_realtime_channels import (  # noqa: E402 - shared doubles
    ExplodingSupabase,
    authenticated,  # noqa: F401 - fixture
    connected,
    manager,  # noqa: F401 - fixture
    owner_user,
)
from tests.test_training_service_admission import (  # noqa: E402 - shared doubles
    OTHER_USER_ID,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
)

DEPLOYMENT_ID = "dep-0001"

#: The relation the three deployment-scoped channels resolve their owner through, and
#: the file that creates it. Same two constants task 8.5's file names, because a
#: second spelling of them here would be a second thing to keep in agreement.
DEPLOYMENTS_TABLE = "strategy_deployments"
DEPLOYMENTS_MIGRATION = "001_strategy_architecture.sql"

SIGNAL_CHANNEL = f"signal.{DEPLOYMENT_ID}"

#: The three families keyed on a deployment id. `signal` is the third, and every
#: assertion about ownership below is parameterised over all three rather than written
#: for `signal` alone — that is how "the same lookup, not a similar one" is checked.
DEPLOYMENT_SCOPED_CHANNELS = [
    f"deployment.{DEPLOYMENT_ID}",
    f"execution.{DEPLOYMENT_ID}",
    SIGNAL_CHANNEL,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seeded_store():
    """The shared in-memory store, plus a deployment owned by ``USER_ID``."""
    store = FakeSupabase.seeded()
    store.tables[DEPLOYMENTS_TABLE] = [
        {"id": DEPLOYMENT_ID, "user_id": USER_ID, "strategy_id": STRATEGY_ID}
    ]
    return store


@pytest.fixture
def store():
    return _seeded_store()


@pytest.fixture
def wired(monkeypatch, store):
    """Point the ownership lookup at the in-memory store."""
    from backend_app.core import dependencies as D

    async def _client(token):
        return store

    monkeypatch.setattr(D, "create_request_supabase_async", _client)
    return store


@pytest.fixture
def no_deployments_table(monkeypatch):
    """A store with no ``strategy_deployments`` relation at all."""
    from backend_app.core import dependencies as D

    store = FakeSupabase.seeded()

    async def _client(token):
        return store

    monkeypatch.setattr(D, "create_request_supabase_async", _client)
    return store


# ═══════════════════════════════════════════════════════════════════════════
# 1. The family — Requirement 18.2, and `EXECUTION_FAMILY` as the template
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSignalFamily:
    def test_the_registry_holds_exactly_the_owned_families_and_signal_is_one(self):
        """The exhaustive set.

        `market.{symbol}.{timeframe}` stays outside: a symbol belongs to nobody, so it
        cannot be authorised against an owner.

        UPDATED BY marketplace-subscriptions-paper-trading TASK 26.3, AND WHY
        --------------------------------------------------------------------
        This assertion read "exactly six" when task 14.1 wrote it, because `signal` was the
        sixth and last family then. **Requirement 19.1** of
        marketplace-subscriptions-paper-trading mandates a seventh - "THE Paper_Channel SHALL
        be implemented as a parameterised, ownership-authorised channel family following the
        existing `OwnedChannelFamily` pattern ... AND SHALL be registered in the existing
        WebSocket infrastructure rather than in a new server" - so `paper` is now in the
        registry and the exhaustive set is seven.

        The assertion is still EXHAUSTIVE and not relaxed to a subset: an eighth family that
        appeared without a test saying so would still fail here. What changed is one member,
        and the requirement that mandates it is named above (Requirement 25.8's condition for
        updating an existing suite). `tests/test_task_26_3_paper_channel.py` asserts the same
        set from the other side, so the two cannot drift apart silently.
        """
        assert {family.namespace for family in C.OWNED_CHANNEL_FAMILIES} == {
            "training",
            "builder.validation",
            "strategy",
            "deployment",
            "execution",
            "signal",
            "paper",
        }
        assert C.claims_owned_namespace("market.BTC/USDT.1h") is False

    def test_the_family_matches_execution_in_everything_but_its_namespace(self):
        """Requirement 18.2: "following the existing `OwnedChannelFamily` pattern".

        Asserted against `EXECUTION_FAMILY` itself rather than against literals, so a
        change to the template cannot leave this family behind.
        """
        assert isinstance(C.SIGNAL_FAMILY, C.OwnedChannelFamily)
        assert C.SIGNAL_FAMILY.namespace == "signal"
        assert C.SIGNAL_FAMILY.resource == C.EXECUTION_FAMILY.resource == "deployment_id"
        assert C.SIGNAL_FAMILY.prefix == "signal."
        assert type(C.SIGNAL_FAMILY.events) is type(C.EXECUTION_FAMILY.events)

    def test_the_channel_name_round_trips(self):
        assert C.SIGNAL_FAMILY.channel(DEPLOYMENT_ID) == SIGNAL_CHANNEL
        assert C.SIGNAL_FAMILY.parse(SIGNAL_CHANNEL) == DEPLOYMENT_ID
        assert C.owned_channel(C.SIGNAL_FAMILY, DEPLOYMENT_ID) == SIGNAL_CHANNEL

        reference = C.parse_owned_channel(SIGNAL_CHANNEL)
        assert reference is not None
        assert reference.family is C.SIGNAL_FAMILY
        assert reference.resource_id == DEPLOYMENT_ID
        assert reference.resource == "deployment_id"
        assert reference.channel == SIGNAL_CHANNEL
        assert C.is_owned_channel(SIGNAL_CHANNEL) is True

    @pytest.mark.parametrize(
        "suffix",
        [
            "",              # the bare namespace names no resource
            ".",             # empty id segment
            "..",
            ".a.b",          # a second segment smuggled in
            ".*",            # a wildcard
            ".%",
            ".a b",          # whitespace
            "./etc/passwd",
            ".a\\b",
            "." + "x" * 65,  # long enough to be a payload
        ],
    )
    def test_it_admits_no_name_the_other_five_families_refuse(self, suffix):
        """The shape rule is stated once, and the sixth family must not widen it."""
        channel = f"signal{suffix}"
        assert C.parse_owned_channel(channel) is None, channel
        assert C.is_owned_channel(channel) is False, channel
        assert C.claims_owned_namespace(channel) is True, channel

    def test_a_malformed_id_cannot_produce_a_channel_name(self):
        for bad in ["", "a.b", "a b", "*", "x" * 65, None]:
            with pytest.raises(ValueError):
                C.SIGNAL_FAMILY.channel(bad)

    def test_the_legacy_signal_trace_broadcast_is_untouched(self):
        """`"signal"` and `"signal_trace"` are one character from colliding.

        `ChannelType.SIGNAL_TRACE` is a FIXED channel whose vocabulary is
        `CHANNEL_EVENTS`, and `ws_event_stream` calls `ChannelType(name)` on the line
        after `is_valid_channel(name)` returns True. Task 14.3 retires that channel
        for this page's traffic; task 14.1 must not have already broken it for the
        consumers 14.3 deliberately leaves in place.
        """
        assert C.ChannelType.SIGNAL_TRACE.value == "signal_trace"
        assert C.is_valid_channel("signal_trace") is True
        assert C.claims_owned_namespace("signal_trace") is False
        assert C.parse_owned_channel("signal_trace") is None

        # And the new family is not admitted to the fixed vocabulary, for the same
        # reason no other parameterised family is.
        assert C.is_valid_channel(SIGNAL_CHANNEL) is False
        assert C.VALID_CHANNELS == {ch.value for ch in C.ChannelType}

    def test_longer_namespaces_are_still_tried_first(self):
        """The sweep order is a rule, not a property of how the tuple was typed."""
        lengths = [len(f.namespace) for f in C.OWNED_CHANNEL_FAMILIES]
        assert lengths == sorted(lengths, reverse=True)

    def test_the_five_existing_families_still_route_their_own_names(self):
        """A sixth entry in a prefix sweep is how an existing name gets rerouted."""
        for channel, family in [
            (f"builder.validation.{STRATEGY_ID}", C.BUILDER_VALIDATION_FAMILY),
            (f"strategy.{STRATEGY_ID}", C.STRATEGY_FAMILY),
            (f"deployment.{DEPLOYMENT_ID}", C.DEPLOYMENT_FAMILY),
            (f"execution.{DEPLOYMENT_ID}", C.EXECUTION_FAMILY),
            ("training.job-1", C.TRAINING_FAMILY),
        ]:
            reference = C.parse_owned_channel(channel)
            assert reference is not None, channel
            assert reference.family is family, channel

    def test_the_namespace_map_and_the_event_union_both_include_it(self):
        assert C.OWNED_CHANNEL_FAMILIES_BY_NAMESPACE["signal"] is C.SIGNAL_FAMILY
        assert C.SIGNAL_CHANNEL_EVENTS <= C.OWNED_CHANNEL_EVENTS


# ═══════════════════════════════════════════════════════════════════════════
# 2. The vocabulary — `design.md`'s three, and no fourth
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEventVocabulary:
    def test_the_designs_three_events_are_the_published_ones(self):
        assert C.SIGNAL_CHANNEL_EVENTS == {
            "signal.generated",
            "signal.status_changed",
            "signal.snapshot",
        }
        assert C.SignalEvent.GENERATED.value == "signal.generated"
        assert C.SignalEvent.STATUS_CHANGED.value == "signal.status_changed"
        assert C.SignalEvent.SNAPSHOT.value == "signal.snapshot"

    def test_every_event_is_namespaced_the_way_the_other_families_are(self):
        """No family's frame type may be readable as another's."""
        for event in C.SignalEvent:
            assert event.value.startswith("signal.")
            assert C.SIGNAL_FAMILY.is_event(event.value) is True

    def test_a_transition_is_its_own_frame_and_not_a_second_generation(self):
        """Requirement 18.4's dedup key is `signal_id:order_lifecycle_state`.

        That key is only meaningful if a transition is announced as a transition; a
        client that heard only "generated" would render a decision as though its order
        were still pending.
        """
        assert C.SignalEvent.STATUS_CHANGED.value != C.SignalEvent.GENERATED.value
        assert "signal.status_changed" in C.SIGNAL_CHANNEL_EVENTS

    def test_a_snapshot_is_distinguishable_from_history_repeating(self):
        """Requirement 18.6: a reconnect requests a snapshot, and it is not new events."""
        assert C.SignalEvent.SNAPSHOT.value not in {
            C.SignalEvent.GENERATED.value,
            C.SignalEvent.STATUS_CHANGED.value,
        }

    def test_no_family_accepts_another_familys_vocabulary(self):
        """A producer that reached for the wrong vocabulary is a bug, not a frame."""
        assert C.is_owned_channel_event(SIGNAL_CHANNEL, "execution.fill") is False
        assert C.is_owned_channel_event(SIGNAL_CHANNEL, "deployment.state") is False
        assert C.is_owned_channel_event(SIGNAL_CHANNEL, "training.progress") is False
        assert (
            C.is_owned_channel_event(f"execution.{DEPLOYMENT_ID}", "signal.generated")
            is False
        )
        assert (
            C.is_owned_channel_event(f"deployment.{DEPLOYMENT_ID}", "signal.snapshot")
            is False
        )

    def test_the_legacy_signal_trace_event_names_are_not_borrowed(self):
        """`EventType.SIGNAL_*` belongs to the fixed channel and stays there.

        Two vocabularies for one fact would be two things for the page to keep in
        agreement, so the new family names its own frames rather than reusing
        `signal_received`/`signal_executed`.
        """
        legacy = set(C.CHANNEL_EVENTS[C.ChannelType.SIGNAL_TRACE].values())
        assert C.SIGNAL_CHANNEL_EVENTS & legacy == set()


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirements 23.1 / 23.2 — the SAME deployment lookup, not a new one
# ═══════════════════════════════════════════════════════════════════════════


class TestSubscriptionAuthorization:
    @pytest.mark.asyncio
    async def test_the_owner_is_allowed(self, wired):
        decision = await authorize_channel_subscription(SIGNAL_CHANNEL, owner_user())

        assert decision.allowed is True, decision.reason
        assert decision.owner_id == USER_ID
        assert decision.code == ""

    @pytest.mark.asyncio
    async def test_it_reads_the_deployments_relation_and_nothing_else(self, wired):
        """The whole of task 14.1's authorisation: one map entry, one existing lookup.

        If a resolver of its own had been written, this is where it would show up as a
        different table, a second read, or a read of `signals`.
        """
        wired.touched.clear()

        decision = await authorize_channel_subscription(SIGNAL_CHANNEL, owner_user())

        assert decision.allowed is True
        assert wired.touched == [DEPLOYMENTS_TABLE]

    @pytest.mark.asyncio
    async def test_the_three_deployment_channels_resolve_the_same_row(self, wired):
        """One deployment, one owner. Three ownership rules would be three answers."""
        wired.touched.clear()

        decisions = [
            await authorize_channel_subscription(channel, owner_user())
            for channel in DEPLOYMENT_SCOPED_CHANNELS
        ]

        assert all(d.allowed for d in decisions)
        assert {d.owner_id for d in decisions} == {USER_ID}
        assert wired.touched == [DEPLOYMENTS_TABLE] * 3

    @pytest.mark.asyncio
    async def test_another_users_deployment_is_refused_and_says_so(self, wired):
        """Requirement 23.2's first half: refused, and the refusal is reported."""
        decision = await authorize_channel_subscription(
            SIGNAL_CHANNEL, owner_user(OTHER_USER_ID)
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert decision.owner_id is None
        frame = decision.refusal_frame()
        assert frame["type"] == "subscription_refused"
        assert frame["channel"] == SIGNAL_CHANNEL
        assert frame["reason"], "a refusal with no reason is a silent refusal"

    @pytest.mark.asyncio
    async def test_a_nonexistent_deployment_and_a_foreign_one_are_indistinguishable(
        self, wired
    ):
        """Requirement 23.2's second half, and the reason it is load-bearing.

        A refusal that distinguished the two would let a non-owner confirm another
        tenant's deployment identifier by subscribing to it. Under the owner-scoped
        RLS policy these ARE the same empty result, and the answer must be too —
        field for field, not merely similar.
        """
        missing = await authorize_channel_subscription(
            "signal.does-not-exist", owner_user()
        )
        foreign = await authorize_channel_subscription(
            SIGNAL_CHANNEL, owner_user(OTHER_USER_ID)
        )

        assert missing.allowed is foreign.allowed is False
        assert missing.code == foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert missing.reason == foreign.reason
        assert missing.owner_id == foreign.owner_id is None

        # The frames differ only in the channel the subscriber itself named.
        missing_frame = missing.refusal_frame()
        foreign_frame = foreign.refusal_frame()
        assert set(missing_frame) == set(foreign_frame)
        assert {k: v for k, v in missing_frame.items() if k != "channel"} == {
            k: v for k, v in foreign_frame.items() if k != "channel"
        }

    @pytest.mark.asyncio
    async def test_the_refusal_shape_does_not_diverge_from_the_other_families(self, wired):
        """`design.md`: no field may diverge from the existing families' conventions."""
        intruder = owner_user(OTHER_USER_ID)
        signal = (await authorize_channel_subscription(SIGNAL_CHANNEL, intruder))
        execution = await authorize_channel_subscription(
            f"execution.{DEPLOYMENT_ID}", intruder
        )

        assert set(signal.refusal_frame()) == set(execution.refusal_frame())
        assert signal.code == execution.code
        # Same relation, same noun, therefore the same sentence.
        assert signal.reason == execution.reason

    @pytest.mark.asyncio
    async def test_a_refusal_carries_no_resource_data(self, wired):
        decision = await authorize_channel_subscription(
            SIGNAL_CHANNEL, owner_user(OTHER_USER_ID)
        )
        frame = decision.refusal_frame()

        assert set(frame) == {"type", "channel", "code", "reason"}
        blob = repr(frame)
        for leak in (USER_ID, STRATEGY_ID, "current_version", "v1.0"):
            assert leak not in blob

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_refuses_and_names_the_file(
        self, no_deployments_table, caplog
    ):
        """A missing ownership table is where "allow" would be indefensible."""
        with caplog.at_level("WARNING"):
            decision = await authorize_channel_subscription(
                SIGNAL_CHANNEL, owner_user()
            )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED
        assert DEPLOYMENTS_MIGRATION in decision.reason
        assert DEPLOYMENTS_TABLE in caplog.text

    @pytest.mark.asyncio
    async def test_no_database_client_refuses(self, monkeypatch):
        from backend_app.core import dependencies as D

        async def _none(token):
            return None

        monkeypatch.setattr(D, "create_request_supabase_async", _none)

        decision = await authorize_channel_subscription(SIGNAL_CHANNEL, owner_user())

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unclassifiable_failure_refuses_rather_than_raising(self):
        decision = await authorize_channel_subscription(
            SIGNAL_CHANNEL, owner_user(), supabase=ExplodingSupabase()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unauthenticated_subscription_is_refused(self, wired):
        """Requirement 23.1: an authenticated connection is the precondition."""
        for user in (None, {}, {"id": ""}):
            decision = await authorize_channel_subscription(SIGNAL_CHANNEL, user)
            assert decision.allowed is False
            assert decision.code == WA.CHANNEL_REFUSED_UNAUTHENTICATED

    @pytest.mark.asyncio
    async def test_a_deployment_row_without_an_owner_is_refused(self, monkeypatch):
        from backend_app.core import dependencies as D

        store = _seeded_store()
        store.tables[DEPLOYMENTS_TABLE] = [{"id": DEPLOYMENT_ID, "user_id": None}]

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        decision = await authorize_channel_subscription(SIGNAL_CHANNEL, owner_user())

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_malformed_name_is_refused_before_any_lookup(self, wired):
        wired.touched.clear()

        for suffix in ("", ".", ".a.b", ".*"):
            channel = f"signal{suffix}"
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is False, channel
            assert decision.code == WA.CHANNEL_REFUSED_UNKNOWN, channel

        assert wired.touched == [], (
            "a name that cannot identify a resource must not reach the database"
        )

    @pytest.mark.asyncio
    async def test_a_strategy_id_used_as_a_deployment_id_is_refused(self, wired):
        """The family decides which relation is read, not the id.

        `STRATEGY_ID` is a real, owned strategy. On `signal.*` it names no deployment.
        """
        decision = await authorize_channel_subscription(
            f"signal.{STRATEGY_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN

    @pytest.mark.asyncio
    async def test_the_legacy_broadcast_keeps_the_authorization_it_had(self, wired):
        """Task 14.1 adds one channel; it re-decides none of the existing ones.

        In particular `signal_trace` — the legacy fixed channel — must not be
        swallowed by the new namespace and start being refused here.
        """
        wired.touched.clear()

        for channel in ["signal_trace", "orders", "pnl", "all", "market.BTC/USDT.1h"]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.code == ""

        assert wired.touched == []

    @pytest.mark.asyncio
    async def test_the_five_existing_families_still_authorise_as_they_did(self, wired):
        """The owner is still allowed on every channel that existed before."""
        for channel in [
            f"builder.validation.{STRATEGY_ID}",
            f"strategy.{STRATEGY_ID}",
            f"deployment.{DEPLOYMENT_ID}",
            f"execution.{DEPLOYMENT_ID}",
        ]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.owner_id == USER_ID, channel


# ═══════════════════════════════════════════════════════════════════════════
# 4. Delivery — confined to this channel's authorised subscribers
#
#  Task 14.2 wires the producer and the `seq` counter. What is asserted here is only
#  that the transport routes the family at all, and that sharing `deployment_id` with
#  two other channels does not make their frames interchangeable.
# ═══════════════════════════════════════════════════════════════════════════


class TestDelivery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event", ["signal.generated", "signal.status_changed", "signal.snapshot"]
    )
    async def test_a_subscribed_owner_receives_the_frame(self, manager, event):
        socket, _ = await connected(manager)
        assert manager.subscribe_owned(USER_ID, "c1", SIGNAL_CHANNEL) is True

        delivered = await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, event, {"detail": "x"}, owner_id=USER_ID
        )

        assert delivered == 1
        frame = socket.sent[-1]
        assert frame["type"] == event
        assert frame["channel"] == SIGNAL_CHANNEL
        # The resource id travels under its family's own field name.
        assert frame["deployment_id"] == DEPLOYMENT_ID

    @pytest.mark.asyncio
    async def test_the_three_deployment_channels_do_not_leak_into_each_other(
        self, manager
    ):
        """These three share an identifier BY DESIGN.

        Keying the registry by bare resource id would deliver every fill and every
        lifecycle transition to a client that asked only for signals.
        """
        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", SIGNAL_CHANNEL)

        assert await manager.broadcast_owned_event(
            f"execution.{DEPLOYMENT_ID}", "execution.fill", {"qty": "1"}
        ) == 0
        assert await manager.broadcast_owned_event(
            f"deployment.{DEPLOYMENT_ID}", "deployment.state", {"status": "RUNNING"}
        ) == 0
        assert socket.sent == []

        assert await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, "signal.generated", {"id": "sig-1"}
        ) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_frame_type_is_not_forwarded(self, manager):
        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", SIGNAL_CHANNEL)

        assert await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, "signal.definitely_not_an_event", {"id": "sig-1"}
        ) == 0
        assert await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, "execution.fill", {"qty": "1"}
        ) == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_a_connection_that_is_not_the_owner_is_dropped_at_send_time(
        self, manager
    ):
        socket, _ = await connected(
            manager, user_id=OTHER_USER_ID, client_id="intruder", tenant_id=OTHER_USER_ID
        )
        manager.subscribe_owned(OTHER_USER_ID, "intruder", SIGNAL_CHANNEL)

        delivered = await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, "signal.generated", {"id": "sig-1"}, owner_id=USER_ID
        )

        assert delivered == 0
        assert socket.frames("signal.generated") == []

    @pytest.mark.asyncio
    async def test_unsubscribing_stops_delivery(self, manager):
        """Requirement 23.4 — a closed page's channel stops arriving."""
        await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", SIGNAL_CHANNEL)

        manager.unsubscribe_owned(USER_ID, "c1", SIGNAL_CHANNEL)

        assert manager.owned_subscriber_count(SIGNAL_CHANNEL) == 0
        assert await manager.broadcast_owned_event(
            SIGNAL_CHANNEL, "signal.generated", {}
        ) == 0

    @pytest.mark.asyncio
    async def test_a_malformed_signal_name_never_enters_the_registry(self, manager):
        """The registry's premise is that everything in it was authorised."""
        await connected(manager)

        for channel in ["signal.*", "signal", "signal.", "signal.a.b"]:
            assert manager.subscribe_owned(USER_ID, "c1", channel) is False
        assert manager.get_stats()["owned_channels"] == 0
