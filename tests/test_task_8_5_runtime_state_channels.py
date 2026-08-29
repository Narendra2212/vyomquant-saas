"""
tests/test_task_8_5_runtime_state_channels.py

The four realtime channels task 8.5 adds, and the two frames that carry a backend
verdict to the canvas.

Spec: strategy-builder task 8.5 (`design.md` § WebSocket / realtime, § Builder UX
contract → Visual states). Requirements 20.12, 21.5, 21.6, 23.1-23.6.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **One parser, five families, and the same shape rule for all of them.** Task 6.7
   wrote the training channel by hand. If the four new families had been transcribed
   from it, each would be a separate chance to admit a name the training channel
   refuses — a wildcard, a second segment, an over-long id. So every malformed-name
   case task 6.7 pins for `training.*` is asserted here for all five families at
   once, through the generic parser the training helpers now delegate to.

2. **Every one of the four is authorised against the owner of the row it names**
   (Requirement 21.5), and a refusal is REPORTED rather than swallowed (21.6). The
   two strategy-scoped channels resolve `strategies.user_id`; the two
   deployment-scoped ones resolve `strategy_deployments.user_id`.

3. **A failed ownership lookup denies, and names the migration.** There is no local
   PostgreSQL, so this is the path that actually runs here.

4. **Delivery is confined to the authorised subscribers of one channel.** In
   particular `deployment.{id}` and `execution.{id}` share a resource id by design,
   and a subscriber to one must not receive the other's frames — which is exactly the
   collision the registry would have had if task 8.5 had kept keying it by bare
   resource id.

5. **Nothing here re-derives a runtime label or a lock rule.** `runtime_state_frame`
   publishes task 8.4's `PlanRuntimeState.to_dict()` and `canvas_state_frame`
   publishes task 8.3's `canvas_state` mapping, both field for field. The tests drive
   the REAL `PlanRuntimeState` and the REAL `strategy_lifecycle.canvas_state` and
   require the frame to agree with them, so a fifth label or a sixth lock rule cannot
   enter through this path.

6. **Task 6.7 still answers what it answered.** Its five training helpers, its three
   transport methods and its `get_stats` figures are re-asserted here, because they
   are now wrappers and a wrapper is where a behaviour change hides.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: `ws_channels`, `WebSocketManager`, `WebSocketConnection`,
`websocket_endpoint`, `authorize_channel_subscription`, `PlanRuntimeState` and
`strategy_lifecycle.canvas_state`.

Doubles, and only these three, all reused from task 6.7's file rather than
re-invented: the in-memory `FakeSupabase` (which does NOT enforce RLS, so both the
empty-result and the visible-foreign-row shapes are asserted), `FakeSocket`, and the
token decoder.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import ws_channels as C
from backend_app.backend import websocket_manager as WM
from backend_app.backend.websocket_manager import (
    WebSocketConnection,
    WebSocketManager,
    websocket_endpoint,
)
from backend_app.core import websocket_auth as WA
from backend_app.core.websocket_auth import authorize_channel_subscription

from tests.test_training_realtime_channels import (  # noqa: E402 - shared doubles
    ExplodingSupabase,
    FakeSocket,
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

#: The relation the two deployment channels resolve their owner through, and the file
#: that creates it. 004e adds binding *columns*; the relation and its ``user_id`` are
#: migration 001's, which is why that is the file the refusal names.
DEPLOYMENTS_TABLE = "strategy_deployments"
DEPLOYMENTS_MIGRATION = "001_strategy_architecture.sql"


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


#: The four channels this task adds, paired with the id each one names.
FOUR_CHANNELS = [
    (f"builder.validation.{STRATEGY_ID}", STRATEGY_ID),
    (f"strategy.{STRATEGY_ID}", STRATEGY_ID),
    (f"deployment.{DEPLOYMENT_ID}", DEPLOYMENT_ID),
    (f"execution.{DEPLOYMENT_ID}", DEPLOYMENT_ID),
]


# ═══════════════════════════════════════════════════════════════════════════
# 1. The names — design.md's table, and nothing wider
# ═══════════════════════════════════════════════════════════════════════════


class TestTheChannelNames:
    def test_the_designs_six_channels_are_the_five_owned_ones_plus_market(self):
        """`design.md`'s table names six. Five are owner-scoped; `market.*` is not.

        A symbol belongs to nobody, so `market.{symbol}.{timeframe}` cannot be
        authorised against an owner and is deliberately outside this machinery.

        A CONTAINMENT check rather than an equality one, and the change is deliberate:
        this file's subject is what *this* spec's table requires, and the registry is
        shared with later specs that add families of their own (trading-lifecycle
        task 14.1's `signal.{deployment_id}` is the first). Asserting equality here
        would make every future family a failure in a file that never analysed it,
        while the fact that actually matters — nothing is *missing* — is what a
        containment check pins. The exhaustive set lives with the family that closed
        it (`tests/test_task_14_1_signal_channel.py`).
        """
        assert {family.namespace for family in C.OWNED_CHANNEL_FAMILIES} >= {
            "training",
            "builder.validation",
            "strategy",
            "deployment",
            "execution",
        }
        assert C.claims_owned_namespace("market.BTC/USDT.1h") is False

    @pytest.mark.parametrize(
        "family,resource_id,expected",
        [
            (C.BUILDER_VALIDATION_FAMILY, "s1", "builder.validation.s1"),
            (C.STRATEGY_FAMILY, "s1", "strategy.s1"),
            (C.DEPLOYMENT_FAMILY, "d1", "deployment.d1"),
            (C.EXECUTION_FAMILY, "d1", "execution.d1"),
            (C.TRAINING_FAMILY, "j1", "training.j1"),
        ],
    )
    def test_every_name_round_trips(self, family, resource_id, expected):
        assert family.channel(resource_id) == expected
        assert family.parse(expected) == resource_id

        reference = C.parse_owned_channel(expected)
        assert reference is not None
        assert reference.family is family
        assert reference.resource_id == resource_id
        assert reference.channel == expected

    def test_each_family_names_the_resource_its_payload_names(self):
        """The frame's id field is the family's own noun, not a positional guess."""
        assert C.TRAINING_FAMILY.resource == "job_id"
        assert C.BUILDER_VALIDATION_FAMILY.resource == "strategy_id"
        assert C.STRATEGY_FAMILY.resource == "strategy_id"
        assert C.DEPLOYMENT_FAMILY.resource == "deployment_id"
        assert C.EXECUTION_FAMILY.resource == "deployment_id"

    @pytest.mark.parametrize(
        "namespace",
        ["builder.validation", "strategy", "deployment", "execution", "training"],
    )
    @pytest.mark.parametrize(
        "suffix",
        [
            "",             # the bare namespace names no resource
            ".",            # empty id segment
            "..",
            ".a.b",         # a second segment smuggled in
            ".*",           # a wildcard
            ".%",
            ".a b",         # whitespace
            "./etc/passwd",
            ".a\\b",
            "." + "x" * 65,  # long enough to be a payload
        ],
    )
    def test_no_family_admits_a_name_the_training_channel_refuses(
        self, namespace, suffix
    ):
        """The shape rule is stated once, so it cannot be looser for four of five.

        This is the property that made one generic parser worth the refactor: every
        case task 6.7 pins for `training.*` alone is pinned here for all five.
        """
        channel = f"{namespace}{suffix}"
        assert C.parse_owned_channel(channel) is None, channel
        assert C.is_owned_channel(channel) is False, channel
        assert C.claims_owned_namespace(channel) is True, channel

    @pytest.mark.parametrize(
        "channel", ["orders", "pnl", "all", "market.BTC/USDT.1h", "", None, 42]
    )
    def test_a_name_outside_these_families_is_not_claimed(self, channel):
        assert C.parse_owned_channel(channel) is None
        assert C.claims_owned_namespace(channel) is False

    def test_the_dotted_namespace_is_parsed_at_its_last_separator(self):
        """`builder.validation` carries a dot of its own and must not be split there."""
        assert C.parse_owned_channel("builder.validation.s1").resource_id == "s1"
        assert C.parse_owned_channel("builder.s1") is None
        assert C.parse_owned_channel("builder.validation.s1.extra") is None

    def test_longer_namespaces_are_tried_first(self):
        """A shorter namespace must never claim a longer one's channel."""
        namespaces = [family.namespace for family in C.OWNED_CHANNEL_FAMILIES]
        lengths = [len(name) for name in namespaces]
        assert lengths == sorted(lengths, reverse=True)

    def test_a_malformed_id_cannot_produce_a_channel_name(self):
        for family in C.OWNED_CHANNEL_FAMILIES:
            for bad in ["", "a.b", "a b", "*", "x" * 65, None]:
                with pytest.raises(ValueError):
                    family.channel(bad)

    def test_the_fixed_channel_vocabulary_is_untouched(self):
        """`is_valid_channel` still means "is a ChannelType member", and must.

        `ws_event_stream.py` calls `ChannelType(name)` on the line after
        `is_valid_channel(name)` returns True, so admitting a parameterised name there
        would hand it a name its very next line cannot construct.
        """
        for channel, _ in FOUR_CHANNELS:
            assert C.is_valid_channel(channel) is False
        assert C.VALID_CHANNELS == {ch.value for ch in C.ChannelType}


class TestTheEventVocabularies:
    def test_the_designs_payloads_are_the_published_events(self):
        assert C.VALIDATION_CHANNEL_EVENTS == {
            "validation.report",
            "validation.failed",
        }
        assert C.STRATEGY_CHANNEL_EVENTS == {
            "strategy.lifecycle",
            "strategy.canvas_state",
        }
        assert C.DEPLOYMENT_CHANNEL_EVENTS == {
            "deployment.state",
            "deployment.guard_trip",
            "deployment.runtime_state",
        }
        assert C.EXECUTION_CHANNEL_EVENTS == {
            "execution.intent",
            "execution.order",
            "execution.fill",
            "execution.rejection",
        }

    def test_no_family_accepts_another_familys_vocabulary(self):
        """A producer that reached for the wrong vocabulary is a bug, not a frame."""
        assert C.is_owned_channel_event(f"deployment.{DEPLOYMENT_ID}", "training.progress") is False
        assert C.is_owned_channel_event(f"training.j1", "deployment.state") is False
        assert C.is_owned_channel_event(f"strategy.{STRATEGY_ID}", "execution.fill") is False
        assert C.is_owned_channel_event(f"execution.{DEPLOYMENT_ID}", "deployment.state") is False

    def test_a_rejection_is_its_own_frame_and_not_an_order(self):
        """Requirements 20.5 / 20.6: a refused intent never became an order.

        Reporting it as one would put an order on a screen that no venue ever saw.
        """
        assert "execution.rejection" in C.EXECUTION_CHANNEL_EVENTS
        assert C.ExecutionEvent.REJECTION.value != C.ExecutionEvent.ORDER.value

    def test_the_runtime_state_frame_rides_the_deployment_channel(self):
        """Requirement 20.12's frame is a property of a run, not of a version.

        The same version deployed twice holds two runtime states at once, so the
        frame cannot live on `strategy.{strategy_id}`.
        """
        assert "deployment.runtime_state" in C.DEPLOYMENT_CHANNEL_EVENTS
        assert "deployment.runtime_state" not in C.STRATEGY_CHANNEL_EVENTS


# ═══════════════════════════════════════════════════════════════════════════
# 2. Requirements 21.5 / 21.6 — authorised against the resource owner
# ═══════════════════════════════════════════════════════════════════════════


class TestSubscriptionAuthorization:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_the_owner_is_allowed(self, wired, channel, _id):
        decision = await authorize_channel_subscription(channel, owner_user())

        assert decision.allowed is True, channel
        assert decision.owner_id == USER_ID
        assert decision.code == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_another_users_resource_is_refused_and_says_so(
        self, wired, channel, _id
    ):
        """Requirement 21.6 for each of the four channels, one at a time."""
        decision = await authorize_channel_subscription(
            channel, owner_user(OTHER_USER_ID)
        )

        assert decision.allowed is False, channel
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN
        frame = decision.refusal_frame()
        assert frame["type"] == "subscription_refused"
        assert frame["channel"] == channel
        assert frame["reason"], "a refusal with no reason is a silent refusal"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_a_refusal_carries_no_resource_data(self, wired, channel, _id):
        decision = await authorize_channel_subscription(
            channel, owner_user(OTHER_USER_ID)
        )
        frame = decision.refusal_frame()

        assert set(frame) == {"type", "channel", "code", "reason"}
        blob = repr(frame)
        for leak in (USER_ID, "current_version", "v1.0"):
            assert leak not in blob

    @pytest.mark.asyncio
    async def test_a_nonexistent_resource_and_a_foreign_one_answer_the_same(self, wired):
        """No existence oracle: under owner-scoped RLS these ARE the same result."""
        missing = await authorize_channel_subscription(
            "deployment.does-not-exist", owner_user()
        )
        foreign = await authorize_channel_subscription(
            f"deployment.{DEPLOYMENT_ID}", owner_user(OTHER_USER_ID)
        )

        assert missing.allowed is False
        assert missing.code == foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert missing.reason == foreign.reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "channel",
        [f"deployment.{DEPLOYMENT_ID}", f"execution.{DEPLOYMENT_ID}"],
    )
    async def test_an_unapplied_migration_refuses_and_names_the_file(
        self, no_deployments_table, caplog, channel
    ):
        """No `strategy_deployments` relation: warn, name the file, and DENY.

        A missing ownership table is precisely the situation in which "allow" would
        be indefensible, and it is a live possibility here rather than a hypothetical:
        004, 004b, 004c, 004d and 004e are unapplied and there is no local PostgreSQL.
        """
        with caplog.at_level("WARNING"):
            decision = await authorize_channel_subscription(channel, owner_user())

        assert decision.allowed is False, "a missing ownership table must not allow"
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED
        assert DEPLOYMENTS_MIGRATION in decision.reason
        assert DEPLOYMENTS_MIGRATION in caplog.text
        assert DEPLOYMENTS_TABLE in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_no_database_client_refuses(self, monkeypatch, channel, _id):
        from backend_app.core import dependencies as D

        async def _none(token):
            return None

        monkeypatch.setattr(D, "create_request_supabase_async", _none)

        decision = await authorize_channel_subscription(channel, owner_user())

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_an_unclassifiable_failure_refuses_rather_than_raising(
        self, channel, _id
    ):
        decision = await authorize_channel_subscription(
            channel, owner_user(), supabase=ExplodingSupabase()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("channel,_id", FOUR_CHANNELS)
    async def test_an_unauthenticated_subscription_is_refused(self, wired, channel, _id):
        for user in (None, {}, {"id": ""}):
            decision = await authorize_channel_subscription(channel, user)
            assert decision.allowed is False
            assert decision.code == WA.CHANNEL_REFUSED_UNAUTHENTICATED

    @pytest.mark.asyncio
    async def test_a_row_without_an_owner_is_refused(self, monkeypatch):
        from backend_app.core import dependencies as D

        store = _seeded_store()
        store.tables[DEPLOYMENTS_TABLE] = [{"id": DEPLOYMENT_ID, "user_id": None}]

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        decision = await authorize_channel_subscription(
            f"deployment.{DEPLOYMENT_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_malformed_name_is_refused_before_any_lookup(self, wired):
        wired.touched.clear()

        for namespace in ("builder.validation", "strategy", "deployment", "execution"):
            for suffix in ("", ".", ".a.b", ".*"):
                channel = f"{namespace}{suffix}"
                decision = await authorize_channel_subscription(channel, owner_user())
                assert decision.allowed is False, channel
                assert decision.code == WA.CHANNEL_REFUSED_UNKNOWN, channel

        assert wired.touched == [], (
            "a name that cannot identify a resource must not reach the database"
        )

    @pytest.mark.asyncio
    async def test_every_other_channel_keeps_the_authorization_it_had(self, wired):
        """Task 8.5 adds four channels; it re-decides none of the existing ones."""
        wired.touched.clear()

        for channel in ["orders", "positions", "pnl", "all", "market.BTC/USDT.1h"]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.code == ""

        assert wired.touched == []

    @pytest.mark.asyncio
    async def test_the_two_deployment_channels_resolve_the_same_row(self, wired):
        """One deployment, one owner. Two ownership rules would be two answers."""
        wired.touched.clear()

        lifecycle = await authorize_channel_subscription(
            f"deployment.{DEPLOYMENT_ID}", owner_user()
        )
        fills = await authorize_channel_subscription(
            f"execution.{DEPLOYMENT_ID}", owner_user()
        )

        assert lifecycle.allowed is True and fills.allowed is True
        assert lifecycle.owner_id == fills.owner_id
        assert wired.touched == [DEPLOYMENTS_TABLE, DEPLOYMENTS_TABLE]

    @pytest.mark.asyncio
    async def test_the_two_strategy_channels_resolve_the_strategies_table(self, wired):
        wired.touched.clear()

        await authorize_channel_subscription(
            f"builder.validation.{STRATEGY_ID}", owner_user()
        )
        await authorize_channel_subscription(f"strategy.{STRATEGY_ID}", owner_user())

        assert wired.touched == ["strategies", "strategies"]

    @pytest.mark.asyncio
    async def test_a_strategy_id_used_as_a_deployment_id_is_refused(self, wired):
        """The two families do NOT share an id space, and must not be interchangeable.

        `STRATEGY_ID` is a real, owned strategy. Presented on `deployment.*` it names
        no deployment, so it is refused — which is the assertion that the family
        decides which relation is read, rather than the id happening to exist
        somewhere.
        """
        decision = await authorize_channel_subscription(
            f"deployment.{STRATEGY_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN


# ═══════════════════════════════════════════════════════════════════════════
# 3. Delivery — confined to one channel's authorised subscribers
# ═══════════════════════════════════════════════════════════════════════════


class TestDelivery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "channel,event",
        [
            (f"builder.validation.{STRATEGY_ID}", "validation.report"),
            (f"strategy.{STRATEGY_ID}", "strategy.lifecycle"),
            (f"deployment.{DEPLOYMENT_ID}", "deployment.state"),
            (f"execution.{DEPLOYMENT_ID}", "execution.fill"),
        ],
    )
    async def test_a_subscribed_owner_receives_the_frame(self, manager, channel, event):
        socket, _ = await connected(manager)
        assert manager.subscribe_owned(USER_ID, "c1", channel) is True

        delivered = await manager.broadcast_owned_event(
            channel, event, {"detail": "x"}, owner_id=USER_ID
        )

        assert delivered == 1
        frame = socket.sent[-1]
        assert frame["type"] == event
        assert frame["channel"] == channel
        assert frame["detail"] == "x"
        # The resource id travels under its family's own field name.
        reference = C.parse_owned_channel(channel)
        assert frame[reference.resource] == reference.resource_id

    @pytest.mark.asyncio
    async def test_deployment_and_execution_do_not_leak_into_each_other(self, manager):
        """The collision a resource-id-keyed registry would have had.

        These two channels share an identifier BY DESIGN, so keying the registry by
        bare id would have delivered every fill to a client that asked only for
        lifecycle transitions — and vice versa.
        """
        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", f"deployment.{DEPLOYMENT_ID}")

        assert await manager.broadcast_owned_event(
            f"execution.{DEPLOYMENT_ID}", "execution.fill", {"qty": "1"}
        ) == 0
        assert socket.frames("execution.fill") == []

        assert await manager.broadcast_owned_event(
            f"deployment.{DEPLOYMENT_ID}", "deployment.state", {"status": "RUNNING"}
        ) == 1

    @pytest.mark.asyncio
    async def test_a_strategy_and_a_deployment_sharing_an_id_do_not_collide(
        self, manager
    ):
        shared = "same-id"
        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", f"strategy.{shared}")

        assert await manager.broadcast_owned_event(
            f"deployment.{shared}", "deployment.state", {"status": "STOPPED"}
        ) == 0
        assert await manager.broadcast_owned_event(
            f"strategy.{shared}", "strategy.lifecycle", {"lifecycle_state": "DEPLOYED"}
        ) == 1

    @pytest.mark.asyncio
    async def test_an_unsubscribed_channel_reaches_nobody(self, manager):
        await connected(manager)

        assert await manager.broadcast_owned_event(
            f"deployment.{DEPLOYMENT_ID}", "deployment.state", {}
        ) == 0

    @pytest.mark.asyncio
    async def test_the_all_channel_does_not_pick_up_owned_frames(self, manager):
        """A tenant is not a user. `broadcast_to_tenant`'s `"all"` must not see these."""
        listener, _ = await connected(
            manager, user_id=OTHER_USER_ID, tenant_id="tenant-shared", client_id="nosy"
        )
        manager.subscribe("tenant-shared", "nosy", "all")

        owner, _ = await connected(
            manager, user_id=USER_ID, tenant_id="tenant-shared", client_id="owner"
        )
        manager.subscribe_owned("tenant-shared", "owner", f"execution.{DEPLOYMENT_ID}")

        delivered = await manager.broadcast_owned_event(
            f"execution.{DEPLOYMENT_ID}",
            "execution.fill",
            {"qty": "1"},
            owner_id=USER_ID,
        )

        assert delivered == 1
        assert listener.frames("execution.fill") == []

    @pytest.mark.asyncio
    async def test_a_connection_that_is_not_the_owner_is_dropped_at_send_time(
        self, manager
    ):
        socket, _ = await connected(
            manager, user_id=OTHER_USER_ID, client_id="intruder", tenant_id=OTHER_USER_ID
        )
        manager.subscribe_owned(OTHER_USER_ID, "intruder", f"deployment.{DEPLOYMENT_ID}")

        delivered = await manager.broadcast_owned_event(
            f"deployment.{DEPLOYMENT_ID}",
            "deployment.guard_trip",
            {"reason": "MAX_DRAWDOWN"},
            owner_id=USER_ID,
        )

        assert delivered == 0
        assert socket.frames("deployment.guard_trip") == []

    @pytest.mark.asyncio
    async def test_unsubscribing_stops_delivery(self, manager):
        """Requirement 23.2 — a closed view's channels stop arriving."""
        await connected(manager)
        channel = f"builder.validation.{STRATEGY_ID}"
        manager.subscribe_owned(USER_ID, "c1", channel)

        manager.unsubscribe_owned(USER_ID, "c1", channel)

        assert manager.owned_subscriber_count(channel) == 0
        assert await manager.broadcast_owned_event(channel, "validation.report", {}) == 0

    @pytest.mark.asyncio
    async def test_disconnecting_clears_every_owned_subscription(self, manager):
        """Requirement 23.5's resubscribe is only honest if nothing is inherited."""
        await connected(manager)
        for channel, _ in FOUR_CHANNELS:
            manager.subscribe_owned(USER_ID, "c1", channel)
        assert manager.get_stats()["owned_channels"] == 4

        manager.disconnect(USER_ID, "c1")

        for channel, _ in FOUR_CHANNELS:
            assert manager.owned_subscriber_count(channel) == 0
        assert manager.get_stats()["owned_channels"] == 0

    @pytest.mark.asyncio
    async def test_an_unknown_frame_type_is_not_forwarded(self, manager):
        socket, _ = await connected(manager)
        channel = f"deployment.{DEPLOYMENT_ID}"
        manager.subscribe_owned(USER_ID, "c1", channel)

        assert await manager.broadcast_owned_event(
            channel, "deployment.definitely_not_a_state", {"status": "RUNNING"}
        ) == 0
        # And another family's vocabulary is equally refused.
        assert await manager.broadcast_owned_event(
            channel, "training.progress", {"epoch_current": 1}
        ) == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_a_name_no_family_routes_never_enters_the_registry(self, manager):
        """The registry's premise is that everything in it was authorised."""
        await connected(manager)

        for channel in ["deployment.*", "orders", "", "deployment"]:
            assert manager.subscribe_owned(USER_ID, "c1", channel) is False
        assert manager.get_stats()["owned_channels"] == 0

    @pytest.mark.asyncio
    async def test_subscribing_an_unknown_connection_reports_failure(self, manager):
        channel = f"strategy.{STRATEGY_ID}"
        assert manager.subscribe_owned(USER_ID, "ghost", channel) is False
        assert manager.owned_subscriber_count(channel) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Requirement 20.12 — the runtime state the canvas renders
# ═══════════════════════════════════════════════════════════════════════════


def _plan_runtime_state(*, bars, warmups, states, missing=None):
    """A REAL `PlanRuntimeState`, populated through its own public methods."""
    from backend_app.backend.dag_engine import PlanRuntimeState, mark_node

    class _Plan:
        def __init__(self, node_ids):
            self.node_index = {node_id: object() for node_id in node_ids}

    state = PlanRuntimeState()
    state.reset_for(_Plan(list(states)), bars, warmups)
    for node_id, label in states.items():
        mark_node(state, node_id, label, (missing or {}).get(node_id, ()))
    return state


class TestTheRuntimeStateFrame:
    def test_the_frame_is_task_8_4s_own_answer_field_for_field(self):
        """Nothing here decides a label. `to_dict()` does, and this forwards it."""
        state = _plan_runtime_state(
            bars=10,
            warmups={"rsi": 14, "data": 0},
            states={"rsi": "WARMING", "data": "READY"},
        )

        frame = C.runtime_state_frame(DEPLOYMENT_ID, state)

        assert frame["type"] == "deployment.runtime_state"
        assert frame["channel"] == f"deployment.{DEPLOYMENT_ID}"
        assert frame["deployment_id"] == DEPLOYMENT_ID
        assert frame["runtime_state"] == state.to_dict()

    def test_warming_carries_the_bar_count_the_canvas_shows(self):
        """Requirement 20.12: "warming **with a bar count**"."""
        state = _plan_runtime_state(
            bars=10, warmups={"rsi": 14}, states={"rsi": "WARMING"}
        )

        node = C.runtime_state_frame(DEPLOYMENT_ID, state)["runtime_state"]["nodes"]["rsi"]

        assert node["state"] == "WARMING"
        assert node["warmup_bars"] == 14
        # The figures are the engine's, including its headroom rule; this test asserts
        # they TRAVEL, and deliberately does not restate the arithmetic that task 8.4
        # owns and pins in its own file.
        assert node["bars_needed"] == state.bars_needed("rsi")
        assert node["bars_remaining"] == state.bars_remaining("rsi")
        assert node["bars_remaining"] > 0

    def test_every_published_label_survives_the_projection(self):
        from backend_app.backend.dag_engine import RUNTIME_STATES

        states = {f"n{i}": label for i, label in enumerate(RUNTIME_STATES)}
        state = _plan_runtime_state(
            bars=500, warmups={key: 1 for key in states}, states=states
        )

        nodes = C.runtime_state_frame(DEPLOYMENT_ID, state)["runtime_state"]["nodes"]

        assert {node["state"] for node in nodes.values()} == set(RUNTIME_STATES)

    def test_the_four_labels_are_the_engines_four_and_nothing_here_adds_a_fifth(self):
        """A fifth label would have to come from `dag_engine`, not from this path."""
        from backend_app.backend.dag_engine import RUNTIME_STATES

        assert set(RUNTIME_STATES) == {
            "NOT_READY",
            "AWAITING_MODEL",
            "WARMING",
            "READY",
        }
        source = open(C.__file__, encoding="utf-8").read()
        # The projection names no label of its own: the words appear only in prose
        # about what it refuses to decide, never as a string literal it emits.
        for label in RUNTIME_STATES:
            assert f'"{label}"' not in source
            assert f"'{label}'" not in source

    def test_a_missing_port_is_named_so_the_author_knows_what_to_wire(self):
        state = _plan_runtime_state(
            bars=100,
            warmups={"add": 0},
            states={"add": "NOT_READY"},
            missing={"add": ("b",)},
        )

        node = C.runtime_state_frame(DEPLOYMENT_ID, state)["runtime_state"]["nodes"]["add"]

        assert node["state"] == "NOT_READY"
        assert node["missing"] == ["b"]

    def test_no_evaluation_yet_publishes_an_empty_node_map_not_a_missing_key(self):
        """Omitting the key would leave a client rendering whatever it last heard."""
        frame = C.runtime_state_frame(DEPLOYMENT_ID, None)

        assert frame["runtime_state"]["nodes"] == {}
        assert frame["runtime_state"]["bars_seen"] == 0

    def test_a_plain_mapping_is_accepted_and_copied(self):
        payload = {"bars_seen": 3, "nodes": {}, "counts": {}}
        frame = C.runtime_state_frame(DEPLOYMENT_ID, payload)

        assert frame["runtime_state"] == payload
        assert frame["runtime_state"] is not payload

    def test_something_that_is_not_a_runtime_state_is_refused(self):
        with pytest.raises(TypeError):
            C.runtime_state_frame(DEPLOYMENT_ID, "READY")

    @pytest.mark.asyncio
    async def test_the_frame_reaches_the_subscribed_owner(self, manager):
        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", f"deployment.{DEPLOYMENT_ID}")
        state = _plan_runtime_state(
            bars=10, warmups={"rsi": 14}, states={"rsi": "WARMING"}
        )

        delivered = await manager.broadcast_runtime_state(
            DEPLOYMENT_ID, state, owner_id=USER_ID
        )

        assert delivered == 1
        frame = socket.sent[-1]
        assert frame["type"] == "deployment.runtime_state"
        assert frame["runtime_state"]["nodes"]["rsi"]["state"] == "WARMING"


class TestTheCanvasStateFrame:
    def test_the_frame_is_task_8_3s_verdict_field_for_field(self):
        """Requirement 9.9's deployed lock, decided by the backend."""
        from backend_app.backend.strategy_lifecycle import canvas_state

        verdict = canvas_state({"id": "v1", "lifecycle_state": "RUNNING"})
        frame = C.canvas_state_frame(STRATEGY_ID, verdict)

        assert frame["type"] == "strategy.canvas_state"
        assert frame["channel"] == f"strategy.{STRATEGY_ID}"
        assert frame["strategy_id"] == STRATEGY_ID
        assert frame["canvas_state"] == verdict
        assert frame["canvas_state"]["read_only"] is True
        assert frame["canvas_state"]["editable"] is False

    def test_an_editable_draft_travels_as_editable(self):
        from backend_app.backend.strategy_lifecycle import canvas_state

        verdict = canvas_state({"id": "v1", "lifecycle_state": "DRAFT"})
        frame = C.canvas_state_frame(STRATEGY_ID, verdict)

        assert frame["canvas_state"]["read_only"] is False
        assert frame["canvas_state"]["editable"] is True

    def test_no_verdict_publishes_an_empty_mapping_never_editable(self):
        """"Nothing has arrived" must not read as "you may edit this"."""
        frame = C.canvas_state_frame(STRATEGY_ID, None)

        assert frame["canvas_state"] == {}
        assert frame["canvas_state"].get("editable") is None

    def test_nothing_here_re_derives_the_lock_rule(self):
        """The read-only states are `strategy_lifecycle`'s, and are not repeated here."""
        from backend_app.backend.strategy_lifecycle import READ_ONLY_LIFECYCLE_STATES

        source = open(C.__file__, encoding="utf-8").read()
        for state in READ_ONLY_LIFECYCLE_STATES:
            assert f'"{state}"' not in source
            assert f"'{state}'" not in source

    @pytest.mark.asyncio
    async def test_the_lock_reaches_the_subscribed_owner(self, manager):
        from backend_app.backend.strategy_lifecycle import canvas_state

        socket, _ = await connected(manager)
        manager.subscribe_owned(USER_ID, "c1", f"strategy.{STRATEGY_ID}")

        delivered = await manager.broadcast_canvas_state(
            STRATEGY_ID,
            canvas_state({"id": "v1", "lifecycle_state": "DEPLOYED"}),
            owner_id=USER_ID,
        )

        assert delivered == 1
        assert socket.sent[-1]["canvas_state"]["read_only"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. Requirement 23.1 / 23.3 — one connection, reauthenticated in place
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEndpointMultiplexes:
    @pytest.mark.asyncio
    async def test_four_channels_and_a_legacy_one_share_one_socket(
        self, manager, wired, authenticated, monkeypatch
    ):
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        observed = {}
        socket = FakeSocket(
            [
                *[{"action": "subscribe", "channel": ch} for ch, _ in FOUR_CHANNELS],
                {"action": "subscribe", "channel": "orders"},
                lambda: observed.update(
                    counts={
                        ch: manager.owned_subscriber_count(ch) for ch, _ in FOUR_CHANNELS
                    }
                ),
                {"action": "ping"},
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.accepts == 1, "Requirement 23.1: exactly one connection"
        assert observed["counts"] == {ch: 1 for ch, _ in FOUR_CHANNELS}
        assert [f["channel"] for f in socket.frames("subscribed")] == [
            *[ch for ch, _ in FOUR_CHANNELS],
            "orders",
        ]
        assert socket.frames("pong")

    @pytest.mark.asyncio
    async def test_another_users_channel_is_refused_on_the_open_socket(
        self, manager, authenticated, monkeypatch
    ):
        """The refusal closes the subscription, not the connection (21.6)."""
        from backend_app.core import dependencies as D

        store = _seeded_store()
        store.tables[DEPLOYMENTS_TABLE] = [
            {"id": DEPLOYMENT_ID, "user_id": OTHER_USER_ID}
        ]

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        socket = FakeSocket(
            [
                {"action": "subscribe", "channel": f"deployment.{DEPLOYMENT_ID}"},
                {"action": "subscribe", "channel": "orders"},
                {"action": "ping"},
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        refusals = socket.frames("subscription_refused")
        assert len(refusals) == 1
        assert refusals[0]["channel"] == f"deployment.{DEPLOYMENT_ID}"
        assert refusals[0]["code"] == WA.CHANNEL_REFUSED_FORBIDDEN
        assert refusals[0]["reason"]
        assert manager.owned_subscriber_count(f"deployment.{DEPLOYMENT_ID}") == 0
        # The connection kept working: the legacy channel was acknowledged after it.
        assert [f["channel"] for f in socket.frames("subscribed")] == ["orders"]
        assert socket.frames("pong")
        assert socket.accepts == 1

    @pytest.mark.asyncio
    async def test_a_wildcard_is_refused_and_never_acknowledged(
        self, manager, wired, authenticated, monkeypatch
    ):
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)
        wired.touched.clear()

        socket = FakeSocket(
            [
                {"action": "subscribe", "channel": "deployment.*"},
                {"action": "subscribe", "channel": "strategy"},
                {"action": "subscribe", "channel": "builder.validation."},
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert len(socket.frames("subscription_refused")) == 3
        assert {f["code"] for f in socket.frames("subscription_refused")} == {
            WA.CHANNEL_REFUSED_UNKNOWN
        }
        assert socket.frames("subscribed") == []
        assert wired.touched == [], "a wildcard must not reach the database"

    @pytest.mark.asyncio
    async def test_unsubscribing_drops_the_channel(
        self, manager, wired, authenticated, monkeypatch
    ):
        """Requirement 23.2, over the endpoint rather than the registry."""
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        channel = f"execution.{DEPLOYMENT_ID}"
        counts = []
        socket = FakeSocket(
            [
                {"action": "subscribe", "channel": channel},
                lambda: counts.append(manager.owned_subscriber_count(channel)),
                {"action": "unsubscribe", "channel": channel},
                lambda: counts.append(manager.owned_subscriber_count(channel)),
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert counts == [1, 0]
        assert [f["channel"] for f in socket.frames("unsubscribed")] == [channel]

    @pytest.mark.asyncio
    async def test_a_refreshed_token_reauthenticates_without_reconnecting(
        self, manager, wired, authenticated, monkeypatch
    ):
        """Requirement 23.3: the EXISTING connection is reauthenticated.

        The assertion that matters is `accepts == 1` — a token refresh that produced a
        reconnect would be a reconnect storm every time a session refreshed, which is
        the whole reason the requirement is worded this way.
        """
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        channel = f"strategy.{STRATEGY_ID}"
        counts = []
        socket = FakeSocket(
            [
                {"action": "subscribe", "channel": channel},
                {"action": "auth", "token": f"token_{USER_ID}"},
                lambda: counts.append(manager.owned_subscriber_count(channel)),
                {"action": "ping"},
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.frames("authenticated"), "the refresh was acknowledged"
        assert socket.accepts == 1, "a refresh must not reconnect"
        assert counts == [1], "the existing subscription survived the refresh"
        assert socket.frames("pong"), "the connection kept working"
        assert socket.closed is None

    @pytest.mark.asyncio
    async def test_a_refresh_for_another_user_closes_the_connection(
        self, manager, wired, authenticated, monkeypatch
    ):
        """Running on under an identity the server cannot vouch for is not an option."""
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        socket = FakeSocket(
            [
                {"action": "auth", "token": f"token_{OTHER_USER_ID}"},
                {"action": "ping"},
            ]
        )

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.frames("auth_failed")
        assert socket.frames("pong") == [], "nothing was served after the failure"
        assert socket.closed is not None

    @pytest.mark.asyncio
    async def test_an_unverifiable_refresh_closes_the_connection(
        self, manager, wired, authenticated, monkeypatch
    ):
        from backend_app.core import dependencies as D

        async def _client(token):
            return wired

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        socket = FakeSocket([{"action": "auth", "token": "not-a-token"}])

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.frames("auth_failed")
        assert socket.closed is not None


# ═══════════════════════════════════════════════════════════════════════════
# 6. Task 6.7 still answers exactly what it answered
# ═══════════════════════════════════════════════════════════════════════════


class TestTaskSixSevenIsUnchanged:
    def test_the_training_helpers_still_answer_the_same(self):
        assert C.training_channel("j1") == "training.j1"
        assert C.parse_training_channel("training.j1") == "j1"
        assert C.is_training_channel("training.j1") is True
        assert C.claims_training_namespace("training") is True
        assert C.claims_training_namespace("orders") is False
        assert C.is_training_event("training.progress") is True
        assert C.is_training_event("training.nope") is False
        assert C.TRAINING_EVENTS == {ev.value for ev in C.TrainingEvent}

    @pytest.mark.asyncio
    async def test_the_training_transport_methods_still_work(self, manager):
        socket, _ = await connected(manager)

        assert manager.subscribe_training(USER_ID, "c1", "job-1") is True
        assert manager.training_subscriber_count("job-1") == 1
        assert (
            await manager.broadcast_training_event(
                "job-1", "training.progress", {"epoch_current": 2}, owner_id=USER_ID
            )
            == 1
        )
        frame = socket.sent[-1]
        assert frame["channel"] == "training.job-1"
        assert frame["job_id"] == "job-1"
        assert frame["epoch_current"] == 2

        manager.unsubscribe_training(USER_ID, "c1", "job-1")
        assert manager.training_subscriber_count("job-1") == 0

    @pytest.mark.asyncio
    async def test_the_training_stats_figures_still_count_training_only(self, manager):
        await connected(manager)
        manager.subscribe_training(USER_ID, "c1", "job-1")
        manager.subscribe_owned(USER_ID, "c1", f"deployment.{DEPLOYMENT_ID}")

        stats = manager.get_stats()

        assert stats["training_channels"] == 1
        assert stats["training_subscribers"] == 1
        assert stats["owned_channels"] == 2

    @pytest.mark.asyncio
    async def test_a_malformed_job_id_is_refused_rather_than_raising(self, manager):
        """The wrappers absorb what `training_channel` raises on, as they must.

        `subscribe_training` returning False beats a `ValueError` escaping into the
        endpoint's message loop, where it would tear down a connection over a name.
        """
        await connected(manager)

        assert manager.subscribe_training(USER_ID, "c1", "a.b") is False
        assert manager.training_subscriber_count("a.b") == 0
        manager.unsubscribe_training(USER_ID, "c1", "a.b")
        assert await manager.broadcast_training_event("a.b", "training.queued", {}) == 0
