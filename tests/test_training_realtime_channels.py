"""
tests/test_training_realtime_channels.py

The `training.{job_id}` realtime channel: its name, its authorisation, and its
delivery.

Spec: strategy-builder task 6.7 (`design.md` § WebSocket / realtime, § Security and
tenant isolation). Requirements 15.11, 21.5, 21.6, 23.1.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
Five properties, each of which would be a real defect if it stopped holding:

1. **A well-formed name is not an authorised one.** ``ws_channels`` decides only
   whether a channel name is routable; ownership is resolved in
   ``core/websocket_auth``. The two are asserted separately because conflating them
   is how a parameterised channel becomes a wildcard.
2. **Another user's job is REFUSED, and the refusal is reported** (Requirement
   21.6). Not accepted-then-never-delivered, not silently dropped: the client gets
   a frame naming the channel and a reason, and the connection stays open.
3. **A failed ownership lookup denies.** ``004d_training_and_models.sql`` is
   unapplied and there is no local PostgreSQL, so this path is reachable in
   practice. It warns, names the file, and refuses — it never raises, and it never
   allows.
4. **Delivery is confined to the subscribers of that one job.** In particular a
   client subscribed to the manager's special ``"all"`` channel in the SAME tenant
   receives nothing, which is the whole reason the per-job registry is separate
   from ``_channel_subscribers``.
5. **One connection.** Every frame — the refusal, the acknowledgement, the training
   progress and an unrelated channel's traffic — arrives on the single socket the
   client already had (Requirement 23.1). Nothing here accepts a second one.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: ``ws_channels``, ``WebSocketManager``, ``WebSocketConnection``,
``websocket_endpoint``, ``authorize_channel_subscription`` and
``strategy_service.publish_training_event``. The authorisation decision, the
subscribe/refuse branch and the fan-out are all production code.

Doubles, and only these three:

* **The database.** ``FakeSupabase`` from ``tests/test_training_service_admission.py``
  — the same in-memory store, reused rather than re-invented. It does NOT enforce
  RLS, which matters here: under ``tj_owner_select`` a real client would see an
  empty result for another user's job, so BOTH shapes are asserted — the empty
  result (a job id that does not exist) and the visible row with a foreign
  ``user_id`` (what a service-role client would see).
* **The socket.** ``FakeSocket`` records what was sent and replays a scripted list
  of client frames. There is no way to assert "no second socket was opened" against
  a real ``WebSocket``; against this one, the count of ``accept()`` calls is the
  assertion.
* **The token decoder.** Patched so these tests are about channel authorisation
  rather than about JWT signing, which ``tests/test_websocket_auth_fail_closed.py``
  already covers.

Nothing else is stubbed. No authorisation branch is replaced by a test-only
version.
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

from tests.test_training_service_admission import (  # noqa: E402 - shared doubles
    OTHER_USER_ID,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
    live_job,
)

JOB_ID = "job-live"
MIGRATION = "004d_training_and_models.sql"


# ---------------------------------------------------------------------------
# The doubles
# ---------------------------------------------------------------------------


class FakeSocket:
    """A socket that replays scripted client frames and records what was sent.

    ``accepts`` is the count that makes "no second socket" assertable.
    """

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent = []
        self.accepts = 0
        self.closed = None

    async def accept(self):
        self.accepts += 1

    async def receive_json(self):
        while self.script:
            item = self.script.pop(0)
            if callable(item):
                # A probe: runs WHILE the connection is open, which is the only time
                # the manager's registries can be inspected. The endpoint tears the
                # connection down on disconnect, so an assertion made after it
                # returns would be asserting the teardown instead.
                result = item()
                if hasattr(result, "__await__"):
                    await result
                continue
            return item

        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect(code=1000)

    async def send_json(self, message):
        self.sent.append(message)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    # -- inspection --------------------------------------------------------
    def frames(self, frame_type):
        return [frame for frame in self.sent if frame.get("type") == frame_type]


class ExplodingSupabase:
    """A client whose every query fails with something unclassifiable."""

    def table(self, name):
        return self

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        raise RuntimeError("connection reset by peer")


def owner_user(user_id=USER_ID):
    return {"id": user_id, "access_token": f"token_{user_id}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    store = FakeSupabase.seeded()
    live_job(store, job_id=JOB_ID, user_id=USER_ID)
    return store


@pytest.fixture
def manager(monkeypatch):
    """A fresh manager, installed as the module singleton for the test's duration."""
    fresh = WebSocketManager()
    monkeypatch.setattr(WM, "_websocket_manager", fresh)
    return fresh


@pytest.fixture
def no_db(monkeypatch):
    """No database client at all: ``create_request_supabase_async`` returns None."""
    from backend_app.core import dependencies as D

    async def _none(token):
        return None

    monkeypatch.setattr(D, "create_request_supabase_async", _none)


@pytest.fixture
def wired_db(monkeypatch, db):
    """Point the authorisation lookup at the in-memory store."""
    from backend_app.core import dependencies as D

    async def _client(token):
        return db

    monkeypatch.setattr(D, "create_request_supabase_async", _client)
    return db


@pytest.fixture
def authenticated(monkeypatch):
    """Decode any token of the form ``token_{user_id}`` into that user."""

    def _decode(token):
        if not token or not str(token).startswith("token_"):
            return None
        return {"sub": str(token)[len("token_"):]}

    monkeypatch.setattr(WA, "_decode_hs256_token", _decode)
    return _decode


async def connected(manager, *, user_id=USER_ID, tenant_id=None, client_id="c1"):
    """A live connection registered with ``manager``."""
    socket = FakeSocket()
    connection = WebSocketConnection(
        socket, tenant_id or user_id, client_id, user_id=user_id
    )
    await manager.connect(connection, tenant_id or user_id, client_id)
    return socket, connection


# ═══════════════════════════════════════════════════════════════════════════
# 1. The channel name — routable is not the same as authorised
# ═══════════════════════════════════════════════════════════════════════════


class TestTheChannelName:
    def test_the_design_s_name_round_trips(self):
        assert C.training_channel(JOB_ID) == f"training.{JOB_ID}"
        assert C.parse_training_channel(f"training.{JOB_ID}") == JOB_ID
        assert C.is_training_channel(f"training.{JOB_ID}")

    @pytest.mark.parametrize(
        "channel",
        [
            "training.",           # empty job segment
            "training..",          # ditto, with a separator
            "training.a.b",        # a second segment smuggled in
            "training.*",          # a wildcard
            "training.%",
            "training.a b",        # whitespace
            "training./etc/passwd",
            "training.a\\b",
            "training." + "x" * 65,  # long enough to be a payload
            "trainingjob",
            "training",            # the bare prefix names no job
            "orders",
            "",
            None,
            42,
        ],
    )
    def test_nothing_else_parses_as_a_training_channel(self, channel):
        assert C.parse_training_channel(channel) is None
        assert C.is_training_channel(channel) is False

    def test_a_malformed_job_id_cannot_produce_a_channel_name(self):
        for bad in ["", "a.b", "a b", "*", "x" * 65, None]:
            with pytest.raises(ValueError):
                C.training_channel(bad)

    def test_the_fixed_channel_vocabulary_is_untouched(self):
        """``is_valid_channel`` still means "is an enum member", and must.

        ``ws_event_stream.py`` calls ``ChannelType(channel_name)`` on the very next
        line after ``is_valid_channel(channel_name)`` returns True. Admitting a
        parameterised name there would hand it a value the enum cannot construct.
        """
        assert C.is_valid_channel(f"training.{JOB_ID}") is False
        assert C.is_valid_channel("training") is False
        assert "training" not in C.VALID_CHANNELS
        for channel in C.VALID_CHANNELS:
            assert C.ChannelType(channel).value == channel

    def test_the_five_frames_the_design_names_exist(self):
        assert C.TRAINING_LIFECYCLE_EVENTS == {
            "training.queued",
            "training.progress",
            "training.completed",
            "training.failed",
            "training.cancelled",
        }
        for event in C.TRAINING_LIFECYCLE_EVENTS:
            assert C.is_training_event(event)
        # Task 6.3's acknowledgement frame is carried too, and is a different fact
        # from ``cancelled``: the flag is set, the worker has not stopped yet.
        assert C.is_training_event("training.cancel_requested")
        assert C.is_training_event("training.anything_else") is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. Requirements 21.5 / 21.6 — authorised against job.user_id, refused explicitly
# ═══════════════════════════════════════════════════════════════════════════


class TestSubscriptionAuthorization:
    @pytest.mark.asyncio
    async def test_the_owner_is_allowed(self, wired_db):
        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user()
        )

        assert decision.allowed is True
        assert decision.owner_id == USER_ID
        assert decision.code == ""

    @pytest.mark.asyncio
    async def test_another_user_s_job_is_refused_and_says_so(self, wired_db):
        """Requirement 21.6, the case the task text calls out by name."""
        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user(OTHER_USER_ID)
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_FORBIDDEN
        frame = decision.refusal_frame()
        assert frame["type"] == "subscription_refused"
        assert frame["channel"] == f"training.{JOB_ID}"
        assert frame["reason"], "a refusal with no reason is a silent refusal"

    @pytest.mark.asyncio
    async def test_a_refusal_carries_no_resource_data(self, wired_db):
        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user(OTHER_USER_ID)
        )
        frame = decision.refusal_frame()

        assert set(frame) == {"type", "channel", "code", "reason"}
        blob = repr(frame)
        for leak in (USER_ID, STRATEGY_ID, "ver-1", "n_model", "xgboost"):
            assert leak not in blob

    @pytest.mark.asyncio
    async def test_an_invisible_job_and_a_nonexistent_one_answer_the_same(
        self, wired_db
    ):
        """No existence oracle: under RLS these two ARE the same empty result."""
        missing = await authorize_channel_subscription(
            "training.job-does-not-exist", owner_user()
        )
        foreign = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user(OTHER_USER_ID)
        )

        assert missing.allowed is False
        assert missing.code == foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN

    @pytest.mark.asyncio
    async def test_an_unapplied_migration_refuses_and_names_the_file(
        self, monkeypatch, caplog
    ):
        """No ``training_jobs`` relation: warn, name 004d, and DENY."""
        from backend_app.core import dependencies as D

        store = FakeSupabase.seeded(with_training_jobs=False)

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        with caplog.at_level("WARNING"):
            decision = await authorize_channel_subscription(
                f"training.{JOB_ID}", owner_user()
            )

        assert decision.allowed is False, "a missing ownership table must not allow"
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED
        assert MIGRATION in decision.reason
        assert MIGRATION in caplog.text

    @pytest.mark.asyncio
    async def test_no_database_client_refuses(self, no_db):
        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unclassifiable_lookup_failure_refuses_rather_than_raising(self):
        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user(), supabase=ExplodingSupabase()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_an_unauthenticated_subscription_is_refused(self, wired_db):
        for user in (None, {}, {"id": ""}):
            decision = await authorize_channel_subscription(
                f"training.{JOB_ID}", user
            )
            assert decision.allowed is False
            assert decision.code == WA.CHANNEL_REFUSED_UNAUTHENTICATED

    @pytest.mark.asyncio
    async def test_a_job_row_without_an_owner_is_refused(self, monkeypatch):
        from backend_app.core import dependencies as D

        store = FakeSupabase.seeded()
        store.tables["training_jobs"].append({"id": JOB_ID, "user_id": None})

        async def _client(token):
            return store

        monkeypatch.setattr(D, "create_request_supabase_async", _client)

        decision = await authorize_channel_subscription(
            f"training.{JOB_ID}", owner_user()
        )

        assert decision.allowed is False
        assert decision.code == WA.CHANNEL_REFUSED_OWNER_UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_malformed_training_channel_is_refused_before_any_lookup(
        self, wired_db
    ):
        wired_db.touched.clear()

        for channel in ["training.", "training.a.b", "training.*", "training"]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is False, channel
            assert decision.code == WA.CHANNEL_REFUSED_UNKNOWN, channel

        assert wired_db.touched == [], (
            "a name that cannot identify a job must not reach the database"
        )

    @pytest.mark.asyncio
    async def test_an_empty_channel_is_refused(self, wired_db):
        for channel in ["", None, 7]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is False
            assert decision.code == WA.CHANNEL_REFUSED_UNKNOWN

    @pytest.mark.asyncio
    async def test_every_other_channel_keeps_the_authorization_it_had(self, wired_db):
        """Task 6.7 adds one channel; it does not re-decide any existing one.

        Asserted so that a later change which quietly starts allowing or denying
        `orders`, `pnl` or `market.*` from here has to break this test first.
        """
        wired_db.touched.clear()

        for channel in ["orders", "positions", "pnl", "all", "market.BTC/USDT.1h"]:
            decision = await authorize_channel_subscription(channel, owner_user())
            assert decision.allowed is True, channel
            assert decision.code == ""

        assert wired_db.touched == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. Requirement 15.11 — delivery, and only to that job's subscribers
# ═══════════════════════════════════════════════════════════════════════════


class TestDelivery:
    @pytest.mark.asyncio
    async def test_a_subscribed_owner_receives_the_frame(self, manager):
        socket, _ = await connected(manager)
        assert manager.subscribe_training(USER_ID, "c1", JOB_ID) is True

        delivered = await manager.broadcast_training_event(
            JOB_ID,
            "training.progress",
            {"epoch_current": 4, "epochs_total": 10, "loss": 0.21, "val_loss": 0.25},
            owner_id=USER_ID,
        )

        assert delivered == 1
        frame = socket.sent[-1]
        assert frame["type"] == "training.progress"
        assert frame["channel"] == f"training.{JOB_ID}"
        assert frame["job_id"] == JOB_ID

    @pytest.mark.asyncio
    async def test_the_progress_payload_is_loss_curve_data_verbatim(self, manager):
        """Per the 6.1 notes: per-epoch scalars, never prediction vectors.

        The channel forwards the producer's payload unchanged, so what is asserted
        here is that it neither drops the loss-curve scalars nor invents anything.
        """
        socket, _ = await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)
        payload = {
            "epoch_current": 3,
            "epochs_total": 12,
            "loss": 0.4,
            "val_loss": 0.42,
            "progress": 0.25,
            "eta_seconds": None,
        }

        await manager.broadcast_training_event(JOB_ID, "training.progress", payload)

        frame = socket.sent[-1]
        for key, value in payload.items():
            assert frame[key] == value
        assert set(frame) == set(payload) | {
            "type",
            "channel",
            "job_id",
            "broadcast_time",
        }

    @pytest.mark.asyncio
    async def test_an_unsubscribed_connection_receives_nothing(self, manager):
        socket, _ = await connected(manager)

        delivered = await manager.broadcast_training_event(
            JOB_ID, "training.completed", {}
        )

        assert delivered == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_the_all_channel_does_not_pick_up_training_frames(self, manager):
        """The reason the per-job registry is separate from ``_channel_subscribers``.

        ``broadcast_to_tenant`` also delivers to every ``"all"`` subscriber in the
        tenant, and a tenant is not a user. Routing per-job training frames through
        it would hand one tenant member another member's training progress.
        """
        listener, _ = await connected(manager, user_id=OTHER_USER_ID,
                                      tenant_id="tenant-shared", client_id="listener")
        manager.subscribe("tenant-shared", "listener", "all")

        owner_socket, _ = await connected(manager, user_id=USER_ID,
                                         tenant_id="tenant-shared", client_id="owner")
        manager.subscribe_training("tenant-shared", "owner", JOB_ID)

        delivered = await manager.broadcast_training_event(
            JOB_ID, "training.completed", {"status": "COMPLETED"}, owner_id=USER_ID
        )

        assert delivered == 1
        assert owner_socket.sent, "the owner must still receive it"
        assert listener.sent == [], "a tenant sibling must not"

    @pytest.mark.asyncio
    async def test_a_connection_that_is_not_the_owner_is_dropped_at_send_time(
        self, manager
    ):
        """Defence in depth behind the subscribe-time check."""
        socket, _ = await connected(manager, user_id=OTHER_USER_ID,
                                    client_id="intruder", tenant_id=OTHER_USER_ID)
        manager.subscribe_training(OTHER_USER_ID, "intruder", JOB_ID)

        delivered = await manager.broadcast_training_event(
            JOB_ID, "training.failed", {"failure_reason": "X"}, owner_id=USER_ID
        )

        assert delivered == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_unsubscribing_stops_delivery(self, manager):
        socket, _ = await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)

        manager.unsubscribe_training(USER_ID, "c1", JOB_ID)

        assert manager.training_subscriber_count(JOB_ID) == 0
        assert await manager.broadcast_training_event(
            JOB_ID, "training.queued", {}
        ) == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_disconnecting_clears_the_training_registry(self, manager):
        await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)

        manager.disconnect(USER_ID, "c1")

        assert manager.training_subscriber_count(JOB_ID) == 0
        assert manager.get_stats()["training_channels"] == 0

    @pytest.mark.asyncio
    async def test_an_unknown_frame_type_is_not_forwarded(self, manager):
        socket, _ = await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)

        delivered = await manager.broadcast_training_event(
            JOB_ID, "training.definitely_not_a_state", {"progress": 1.0}
        )

        assert delivered == 0
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_subscribing_an_unknown_connection_reports_failure(self, manager):
        assert manager.subscribe_training(USER_ID, "ghost", JOB_ID) is False
        assert manager.training_subscriber_count(JOB_ID) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Requirement 23.1 — multiplexed over the one existing connection
# ═══════════════════════════════════════════════════════════════════════════


class TestTheEndpointMultiplexes:
    @pytest.mark.asyncio
    async def test_the_owner_subscribes_and_then_receives_progress(
        self, manager, wired_db, authenticated
    ):
        """End to end on one socket: subscribe, then a pushed per-epoch frame."""

        async def push_progress():
            assert manager.training_subscriber_count(JOB_ID) == 1
            await manager.broadcast_training_event(
                JOB_ID,
                "training.progress",
                {"epoch_current": 2, "epochs_total": 8, "loss": 0.5,
                 "val_loss": 0.55},
                owner_id=USER_ID,
            )

        socket = FakeSocket([
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
            push_progress,
        ])

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.accepts == 1, "exactly one socket, accepted once"
        assert socket.frames("subscribed"), socket.sent
        assert socket.frames("subscription_refused") == []
        progress = socket.frames("training.progress")
        assert len(progress) == 1
        assert progress[0]["channel"] == f"training.{JOB_ID}"
        assert progress[0]["loss"] == 0.5

    @pytest.mark.asyncio
    async def test_another_user_s_channel_is_refused_on_the_open_socket(
        self, manager, wired_db, authenticated
    ):
        """The refusal is a FRAME, not a disconnect, and the socket keeps working."""
        observed = []

        socket = FakeSocket([
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
            lambda: observed.append(manager.training_subscriber_count(JOB_ID)),
            {"action": "subscribe", "channel": "orders"},
            {"action": "ping"},
        ])

        await websocket_endpoint(socket, f"token_{OTHER_USER_ID}")

        refusals = socket.frames("subscription_refused")
        assert len(refusals) == 1
        assert refusals[0]["channel"] == f"training.{JOB_ID}"
        assert refusals[0]["code"] == WA.CHANNEL_REFUSED_FORBIDDEN
        assert refusals[0]["reason"]
        # Nothing was subscribed for the refused channel — checked while the
        # connection was still open, in the probe below.
        assert observed == [0]
        # ...and the same connection carried on with its other channels.
        assert [f["channel"] for f in socket.frames("subscribed")] == ["orders"]
        assert socket.frames("pong")
        assert socket.accepts == 1
        assert socket.closed is None

    @pytest.mark.asyncio
    async def test_a_refused_subscription_receives_no_frames_afterwards(
        self, manager, wired_db, authenticated
    ):
        """The refusal is not a "subscribed but undelivered" state; it is no state."""
        seen = {}

        async def publish_while_connected():
            seen["before"] = len(socket.sent)
            await manager.broadcast_training_event(
                JOB_ID, "training.completed", {"status": "COMPLETED"},
                owner_id=USER_ID,
            )
            seen["after"] = len(socket.sent)

        socket = FakeSocket([
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
            publish_while_connected,
        ])

        await websocket_endpoint(socket, f"token_{OTHER_USER_ID}")

        assert seen["after"] == seen["before"]
        assert socket.frames("training.completed") == []

    @pytest.mark.asyncio
    async def test_a_malformed_training_channel_is_refused_by_the_endpoint(
        self, manager, wired_db, authenticated
    ):
        socket = FakeSocket([
            {"action": "subscribe", "channel": "training."},
            {"action": "subscribe", "channel": "training.*"},
        ])

        await websocket_endpoint(socket, f"token_{USER_ID}")

        refusals = socket.frames("subscription_refused")
        assert len(refusals) == 2
        assert {r["code"] for r in refusals} == {WA.CHANNEL_REFUSED_UNKNOWN}
        assert socket.frames("subscribed") == [], (
            "a wildcard must not be acknowledged as a subscription"
        )

    @pytest.mark.asyncio
    async def test_unsubscribe_drops_the_training_channel(
        self, manager, wired_db, authenticated
    ):
        counts = []

        socket = FakeSocket([
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
            lambda: counts.append(manager.training_subscriber_count(JOB_ID)),
            {"action": "unsubscribe", "channel": f"training.{JOB_ID}"},
            lambda: counts.append(manager.training_subscriber_count(JOB_ID)),
        ])

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.frames("unsubscribed")
        assert counts == [1, 0]

    @pytest.mark.asyncio
    async def test_an_unauthenticated_connection_never_reaches_a_channel(
        self, manager, wired_db, authenticated
    ):
        socket = FakeSocket([
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
        ])

        await websocket_endpoint(socket, "not-a-token")

        assert socket.accepts == 0
        assert socket.closed is not None
        assert manager.training_subscriber_count(JOB_ID) == 0

    @pytest.mark.asyncio
    async def test_two_channels_share_one_connection(
        self, manager, wired_db, authenticated
    ):
        """The multiplex, stated as an assertion: one socket, one registration."""
        observed = {}

        def inspect():
            stats = manager.get_stats()
            observed["connections"] = stats["total_connections"]
            observed["training_channels"] = stats["training_channels"]
            observed["orders"] = stats["channels"]["orders"]

        socket = FakeSocket([
            {"action": "subscribe", "channel": "orders"},
            {"action": "subscribe", "channel": f"training.{JOB_ID}"},
            inspect,
        ])

        await websocket_endpoint(socket, f"token_{USER_ID}")

        assert socket.accepts == 1
        assert observed == {
            "connections": 1,
            "training_channels": 1,
            "orders": 1,
        }, "both channels rode the one connection"


# ═══════════════════════════════════════════════════════════════════════════
# 5. The producer seam — task 6.3's publisher reaches the channel
# ═══════════════════════════════════════════════════════════════════════════


class TestThePublisherReachesTheChannel:
    @pytest.mark.asyncio
    async def test_publish_training_event_delivers_on_the_job_channel(
        self, manager, monkeypatch
    ):
        """Wired to the EXISTING publisher (task 6.3), not to a second one."""
        from backend_app.backend import strategy_service as S
        from backend_app.core import state as state_module

        monkeypatch.setattr(state_module.app_state, "ws_manager", None, raising=False)

        socket, _ = await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)

        published = await S.publish_training_event(
            USER_ID,
            "training.queued",
            {"job_id": JOB_ID, "status": "QUEUED", "epochs_total": 10},
        )

        assert published is True
        assert socket.sent[-1]["type"] == "training.queued"
        assert socket.sent[-1]["channel"] == f"training.{JOB_ID}"

    @pytest.mark.asyncio
    async def test_a_payload_without_a_job_id_publishes_no_channel_frame(
        self, manager, monkeypatch
    ):
        from backend_app.backend import strategy_service as S
        from backend_app.core import state as state_module

        monkeypatch.setattr(state_module.app_state, "ws_manager", None, raising=False)

        socket, _ = await connected(manager)
        manager.subscribe_training(USER_ID, "c1", JOB_ID)

        published = await S.publish_training_event(
            USER_ID, "training.progress", {"status": "RUNNING"}
        )

        assert published is False
        assert socket.sent == []

    @pytest.mark.asyncio
    async def test_a_publish_with_nobody_listening_is_not_an_error(
        self, manager, monkeypatch
    ):
        """Best effort: an unconnected browser must never fail a save."""
        from backend_app.backend import strategy_service as S
        from backend_app.core import state as state_module

        monkeypatch.setattr(state_module.app_state, "ws_manager", None, raising=False)

        assert await S.publish_training_event(
            USER_ID, "training.failed", {"job_id": JOB_ID, "failure_reason": "X"}
        ) is False
