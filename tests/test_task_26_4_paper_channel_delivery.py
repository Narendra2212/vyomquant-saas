"""
tests/test_task_26_4_paper_channel_delivery.py

Replay, backpressure, cleanup and the pre-emit ownership check of ``paper.{session_id}``.

Spec: marketplace-subscriptions-paper-trading tasks 26.4 and 26.5. ``design.md`` ->
"``paper/paper_events.py`` and the Paper_Channel", the "Replay buffer", "Heartbeat, slow consumers,
cleanup" and "Ownership re-verification before each emit" paragraphs. Requirements 19.8, 19.9,
19.10, 19.11, 19.12, 19.13, 21.1, 21.7, 27.5.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
1. **The replay is capped at 5000 rows and the cap is on the STATEMENT.** A cap applied only in
   process would still have pulled every row of a long session over the wire. Both halves are
   asserted: the ``limit`` the statement carried, and the length of what came back.

2. **An unrecoverable gap replays NOTHING partial.** Requirement 19.9 is a refusal to hand a client
   a history with a hole in it, because a client that applied a partial replay would show a
   position, a balance and an equity curve computed from events it never received. The three ways a
   gap arises are each exercised, and every one of them asserts an EMPTY replay alongside the
   ``HISTORY_INCOMPLETE`` frame - ``ReplayOutcome`` refuses to represent the other combination at
   all.

3. **One slow consumer costs itself and nobody else.** The 1001st pending event closes that
   connection with ``paper_error{code:'CLIENT_FELL_BEHIND'}``; every other connection keeps
   receiving, and receives **in ascending sequence order with nothing dropped**. That second half is
   the one worth having: a delivery loop that aborted on the first failure would satisfy every
   assertion about the connection that was dropped and none about the ones that were not.

4. **A raising handler is contained.** It is logged with the session id and the event type, the
   handlers beside it still run, the next event still arrives, and the subscription keeps ALL of its
   handlers - including the one that raised. Requirement 19.13's "SHALL NOT leave the connection
   registered without a handler" is a prohibition on quietly unregistering the thing that failed.

5. **The identity compared before each emit is the AUTHENTICATED one.** This is the load-bearing
   assertion of task 26.5 and it is made directly: a subscribe message that carries ``user_id``,
   ``owner_id``, ``identity`` and ``tenant_id`` naming another tenant changes nothing, and a
   subscription whose recorded identity no longer matches ``paper_sessions.user_id`` is closed with
   nothing further emitted on it.

6. **No control is weakened.** ``authorize_channel_subscription`` is untouched, and
   :class:`TestNoControlIsWeakened` re-asserts that another user's session and a nonexistent one are
   still refused identically - the pre-emit check may only ADD refusals.

WHAT IS REAL HERE AND WHAT IS A DOUBLE
--------------------------------------
Real: ``paper_channel``, ``paper_repository``, ``paper_events``, ``ws_channels``,
``api_ws.ws_manager.ConnectionManager``, ``core.websocket_auth``.

Doubles: ``tests/test_paper_repository.FakeSupabase`` - the ONE Persistence_Layer double this
repository has, extended by this task with a ``gt`` predicate and numeric ordering because the
replay read needs both - and a recording WebSocket connection. There is no mock of
``paper_channel``, of ``ws_manager`` or of the repository: every statement these tests observe is
issued by the code production issues it from.

``_run`` is ``tests/test_paper_order_lifecycle_writes._run_coroutine``, imported rather than
re-written, so every coroutine in the session runs on the process's ONE event loop. See that
module's ``_HARNESS_LOOP`` note: a loop per call exhausts Windows' ephemeral port range through
``socket._fallback_socketpair`` and hangs the suite. ``asyncio.run`` appears nowhere in this file.

WHAT THIS FILE DOES NOT CLAIM
-----------------------------
Nothing here establishes the WALL-CLOCK half of Requirements 19.10 and 19.11: that a connection
failing two consecutive heartbeats is closed within five seconds, or that the pending count matches
an operating system's socket buffer occupancy. The heartbeat is ``api_ws/ws_routes._heartbeat_task``
and is used unchanged; what is asserted here is what happens to the REGISTRIES once a connection is
gone, which is the part Requirement 19.10 makes this module responsible for. See
``paper_channel``'s "WHAT A SINGLE-PROCESS TEST CANNOT ESTABLISH" section.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.api_ws.ws_manager import (  # noqa: E402
    MAX_PENDING_EVENTS_PER_CONNECTION,
    ConnectionManager,
)
from backend_app.backend import ws_channels as C  # noqa: E402
from backend_app.backend.paper import paper_channel as pc  # noqa: E402
from backend_app.backend.paper import paper_events as events  # noqa: E402
from backend_app.backend.paper import paper_repository as repo  # noqa: E402
from backend_app.core import websocket_auth as WA  # noqa: E402

from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run  # noqa: E402
from tests.test_paper_repository import (  # noqa: E402 - the one Persistence_Layer double
    FakeSupabase,
    OTHER_SESSION,
    OTHER_USER,
    SESSION,
    USER,
)

NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# DOUBLES AND HELPERS
# ══════════════════════════════════════════════════════════════════════════


class Connection:
    """A recording stand-in for a ``WebSocket``.

    ``sent`` is the ORDER assertion's evidence, so it is a list and never a set. ``fail_from`` makes
    the socket start refusing writes at a given call, which is how a heartbeat-closed socket
    presents itself to the delivery loop.
    """

    def __init__(self, name: str = "ws", fail_from: Optional[int] = None) -> None:
        self.name = name
        self.sent: List[str] = []
        self.closed_with: List[Optional[int]] = []
        self.fail_from = fail_from

    async def send_text(self, text: str) -> None:
        if self.fail_from is not None and len(self.sent) >= self.fail_from:
            raise RuntimeError(f"{self.name} is gone")
        self.sent.append(text)

    async def close(self, code: Optional[int] = None) -> None:
        self.closed_with.append(code)

    # -- assertion helpers -------------------------------------------------
    def frames(self) -> List[Dict[str, Any]]:
        import json

        return [json.loads(text) for text in self.sent]

    def sequences(self) -> List[int]:
        return [frame["sequence"] for frame in self.frames()]

    def types(self) -> List[str]:
        return [frame["type"] for frame in self.frames()]

    def __repr__(self) -> str:  # pragma: no cover - test output only
        return f"<Connection {self.name} sent={len(self.sent)}>"


def _session_row(
    session_id: str = SESSION,
    user_id: str = USER,
    *,
    event_sequence: int = 0,
) -> Dict[str, Any]:
    return {
        "id": session_id,
        "user_id": user_id,
        "session_state": "RUNNING",
        "environment": "PAPER",
        "exchange_id": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "market_data_source": "mds",
        "feed_state": "HEALTHY",
        "event_sequence": event_sequence,
    }


def _client(sessions: Optional[List[Dict[str, Any]]] = None, **kwargs: Any) -> FakeSupabase:
    return FakeSupabase(sessions=list(sessions or [_session_row()]), **kwargs)


def _tick_payload(index: int) -> Dict[str, Any]:
    """One ``market_tick`` payload, priced exactly. No float, ever (Requirement 18.1)."""
    return {
        "symbol": "BTC/USDT",
        "timestamp": NOW + timedelta(minutes=index),
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100") + Decimal(index),
        "volume": Decimal("1"),
        "source_event_id": f"candle-{index}",
        "feed_state": "HEALTHY",
    }


def _frame(sequence: int, session_id: str = SESSION) -> Dict[str, Any]:
    """One live ``market_tick`` frame at ``sequence``, built the way a producer builds it."""
    return events.build_envelope(
        session_id=session_id,
        event_type=events.PaperEvent.MARKET_TICK,
        payload=_tick_payload(sequence),
        sequence=sequence,
        emitted_at=NOW + timedelta(seconds=sequence),
    ).frame()


def _seed_events(
    client: FakeSupabase,
    count: int,
    *,
    session_id: str = SESSION,
    user_id: str = USER,
    first: int = 1,
) -> None:
    """Append ``count`` rows straight into the double's ``paper_events`` list.

    Written directly rather than through ``insert_session_event`` for one reason: the double
    enforces ``uq_paper_event_seq`` and ``uq_paper_event_id`` with a linear scan, so seeding 5001
    events through the insert path is quadratic and would make the cap test cost more than the rest
    of the suite. The rows are the shape ``insert_session_event`` produces - that shape is asserted
    against the real writer in :func:`test_the_seeded_rows_match_what_the_real_writer_produces`, so
    this shortcut cannot drift into a fiction.
    """
    # Validated ONCE and then varied by the two fields that have to differ per row. Validating five
    # thousand payloads to assert a row cap would spend the suite's time on Pydantic rather than on
    # the cap; the template is still produced by the real validator, so the stored shape is real.
    template = events.payload_jsonb(
        events.validate_payload(events.PaperEvent.MARKET_TICK, _tick_payload(first))
    )
    for offset in range(count):
        sequence = first + offset
        payload = dict(
            template,
            close=str(Decimal("100") + Decimal(sequence)),
            source_event_id=f"candle-{sequence}",
        )
        client.events.append(
            {
                "id": f"paper_events-{session_id}-{sequence}",
                "session_id": session_id,
                "user_id": user_id,
                "sequence": sequence,
                "event_id": f"evt-{session_id}-{sequence}",
                "event_type": events.PaperEvent.MARKET_TICK.value,
                "schema_version": events.PAPER_EVENT_SCHEMA_VERSION,
                "payload": payload,
                "emitted_at": events.format_emitted_at(NOW + timedelta(seconds=sequence)),
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        )


def _registry() -> pc.PaperChannelRegistry:
    """A registry over a ``ConnectionManager`` of its own.

    Not the process singletons: ``pc.REGISTRY`` and ``ws_manager.manager`` are shared with every
    other module in the session, and a test that left a subscription on either would be a test that
    changed another file's answer.
    """
    return pc.PaperChannelRegistry(manager=ConnectionManager())


#: Distinguishes "this test did not care who the user is" from "this test passed ``None``
#: deliberately". Without it the helper's own default would swallow the unauthenticated case, and
#: :func:`TestTheRecordedIdentity.test_a_subscription_with_no_authenticated_identity_is_refused`
#: would pass by never testing it.
_UNSET = object()


def _subscribe(
    registry: pc.PaperChannelRegistry,
    connection: Connection,
    *,
    user: Any = _UNSET,
    session_id: str = SESSION,
    handlers: Any = (),
    message: Optional[Dict[str, Any]] = None,
) -> pc.PaperSubscription:
    return _run(
        registry.subscribe(
            session_id,
            connection,
            user={"id": USER} if user is _UNSET else user,
            handlers=handlers,
            message=message,
        )
    )


@pytest.fixture(autouse=True)
def _fresh_state() -> Any:
    """Forget the migration verdict and the owner cache around every test.

    The owner cache is module scope by design (see ``paper_channel``), which means it is exactly the
    kind of state that makes one test decide another's answer. Cleared both ways round.
    """
    repo.reset_persistence_probe()
    pc.invalidate_session_owner()
    yield
    pc.invalidate_session_owner()
    repo.reset_persistence_probe()


# ══════════════════════════════════════════════════════════════════════════
# 0. THE DOUBLE AND THE SEED ARE HONEST
# ══════════════════════════════════════════════════════════════════════════


class TestTheDoubleAndTheSeed:
    def test_the_double_compares_gt_numerically(self) -> None:
        """``sequence > 9`` must not exclude 10. Text comparison says it does."""
        client = _client()
        _seed_events(client, 12)

        rows = repo.read_session_events(client, USER, SESSION, after_sequence=9)

        assert [row["sequence"] for row in rows] == [10, 11, 12]

    def test_the_double_orders_sequences_numerically(self) -> None:
        client = _client()
        _seed_events(client, 12)

        rows = repo.read_session_events(client, USER, SESSION, after_sequence=0)

        assert [row["sequence"] for row in rows] == list(range(1, 13))

    def test_the_seeded_rows_match_what_the_real_writer_produces(self) -> None:
        """The shortcut in :func:`_seed_events` is checked against ``insert_session_event``."""
        client = _client()
        _seed_events(client, 1)
        seeded = dict(client.events[0])

        written = repo.insert_session_event(
            client,
            session_id=SESSION,
            user_id=USER,
            sequence=2,
            event_id="evt-real",
            event_type=events.PaperEvent.MARKET_TICK.value,
            schema_version=events.PAPER_EVENT_SCHEMA_VERSION,
            payload=events.payload_jsonb(
                events.validate_payload(events.PaperEvent.MARKET_TICK, _tick_payload(2))
            ),
            emitted_at=NOW,
        )

        assert set(seeded) == set(written), (
            "the seeded rows carry different columns from the ones insert_session_event writes, so "
            "every replay assertion in this file would be about a row shape that never exists"
        )
        assert set(seeded["payload"]) == set(written["payload"])


# ══════════════════════════════════════════════════════════════════════════
# 1. REPLAY — Requirement 19.8, and the 5000-row cap
# ══════════════════════════════════════════════════════════════════════════


class TestReplay:
    def test_it_replays_every_event_above_the_supplied_sequence_in_order(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=6)])
        _seed_events(client, 6)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=2
        )

        assert outcome.history_incomplete is False
        assert outcome.error_frame is None
        assert [frame["sequence"] for frame in outcome.frames] == [3, 4, 5, 6]
        assert outcome.current_sequence == 6
        assert outcome.truncated is False

    def test_a_last_sequence_of_zero_replays_the_whole_log(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=4)])
        _seed_events(client, 4)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=0
        )

        assert [frame["sequence"] for frame in outcome.frames] == [1, 2, 3, 4]

    def test_a_replayed_frame_is_the_same_eight_fields_a_live_one_carries(self) -> None:
        """Requirement 19.8's client-side dedupe needs one frame shape, not two."""
        client = _client(sessions=[_session_row(event_sequence=1)])
        _seed_events(client, 1)

        replayed = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=0
        ).frames[0]
        live = _frame(1)

        assert list(replayed) == list(live) == list(events.ENVELOPE_FIELDS)
        assert replayed["channel"] == C.PAPER_FAMILY.channel(SESSION)
        assert replayed["schema_version"] == events.PAPER_EVENT_SCHEMA_VERSION
        assert replayed["event_id"], "a replayed frame with no event_id cannot be deduplicated"

    def test_the_replay_is_capped_at_five_thousand_rows_and_the_cap_is_on_the_statement(
        self,
    ) -> None:
        """Requirement 19.8 and ``design.md``: at most 5000 rows per replay.

        Both halves. A cap applied only after the rows arrived would still have pulled the whole
        log over the wire, and a cap applied only in the statement would be undone by any transport
        that ignored ``limit``.
        """
        client = _client(sessions=[_session_row(event_sequence=5001)])
        _seed_events(client, 5001)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=0
        )

        assert repo.SESSION_EVENT_REPLAY_CAP == 5000
        assert len(outcome.frames) == 5000
        assert [frame["sequence"] for frame in outcome.frames] == list(range(1, 5001))
        assert outcome.truncated is True, (
            "a capped replay that did not say so would leave the client believing it is up to date"
        )

        reads = [
            statement
            for statement in client.statements_on(repo.EVENTS_TABLE, "select")
            if statement.limit_value is not None
        ]
        assert reads, "the replay issued no read against paper_events"
        assert reads[-1].limit_value == 5000
        assert reads[-1].cols == repo.EVENT_SELECT, "the replay read must not select('*')"

    def test_a_caller_cannot_ask_for_more_than_the_cap(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=5001)])
        _seed_events(client, 5001)

        rows = repo.read_session_events(
            client, USER, SESSION, after_sequence=0, limit=999_999
        )

        assert len(rows) == 5000
        assert client.statements_on(repo.EVENTS_TABLE, "select")[-1].limit_value == 5000

    def test_the_replay_read_is_scoped_to_the_authenticated_identity(self) -> None:
        """Requirements 21.2 and 21.5: ``user_id`` is a predicate, not a post-filter."""
        client = _client(
            sessions=[_session_row(event_sequence=3), _session_row(OTHER_SESSION, OTHER_USER)]
        )
        _seed_events(client, 3)
        _seed_events(client, 3, session_id=OTHER_SESSION, user_id=OTHER_USER)

        repo.read_session_events(client, USER, SESSION, after_sequence=0)

        statement = client.statements_on(repo.EVENTS_TABLE, "select")[-1]
        assert statement.filter_value("user_id") == USER
        assert statement.filter_value("session_id") == SESSION

    def test_another_users_log_is_never_reachable(self) -> None:
        client = _client(
            sessions=[_session_row(event_sequence=3), _session_row(OTHER_SESSION, OTHER_USER, event_sequence=3)]
        )
        _seed_events(client, 3, session_id=OTHER_SESSION, user_id=OTHER_USER)

        outcome = _registry().replay(
            client, session_id=OTHER_SESSION, user={"id": USER}, last_sequence=0
        )

        # No session is readable for this identity, so this is Requirement 19.9's gap - and it
        # carries no row of the other tenant's log either way.
        assert outcome.frames == ()
        assert outcome.history_incomplete is True
        assert OTHER_USER not in repr(outcome)

    def test_a_replay_needs_an_authenticated_identity(self) -> None:
        client = _client()

        for user in (None, {}, {"id": ""}):
            with pytest.raises(PermissionError):
                _registry().replay(
                    client, session_id=SESSION, user=user, last_sequence=0
                )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE UNRECOVERABLE GAP — Requirement 19.9
# ══════════════════════════════════════════════════════════════════════════


class TestHistoryIncomplete:
    def test_a_negative_last_sequence_replays_nothing_partial(self) -> None:
        """The condition ``design.md`` names first. Nothing partial, one error, live position given."""
        client = _client(sessions=[_session_row(event_sequence=6)])
        _seed_events(client, 6)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=-1
        )

        assert outcome.history_incomplete is True
        assert outcome.frames == (), (
            "Requirement 19.9 forbids a partial replay: a client that applied one would render a "
            "balance and an equity curve computed from events it never received"
        )
        assert outcome.current_sequence == 6
        frame = outcome.error_frame
        assert frame is not None
        assert frame["type"] == events.PaperEvent.ERROR.value
        assert frame["payload"]["code"] == pc.ERROR_HISTORY_INCOMPLETE
        assert frame["payload"]["recoverable"] is False
        assert frame["sequence"] == 6, "the client must be told where the live stream is"

    def test_a_session_whose_events_went_with_it_replays_nothing_partial(self) -> None:
        """The second condition: the counter says events were emitted and none is retained."""
        client = _client(sessions=[_session_row(event_sequence=9)])

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=4
        )

        assert outcome.history_incomplete is True
        assert outcome.frames == ()
        assert outcome.error_frame is not None
        assert outcome.error_frame["payload"]["code"] == pc.ERROR_HISTORY_INCOMPLETE

    def test_a_hole_between_what_the_client_holds_and_what_is_retained(self) -> None:
        """The third condition, and the one a durable buffer is supposed to make impossible.

        Checked rather than assumed away: it is what a deletion looks like from the replay's side.
        """
        client = _client(sessions=[_session_row(event_sequence=12)])
        _seed_events(client, 3, first=10)  # 10, 11, 12 retained; the client holds 4

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=4
        )

        assert outcome.history_incomplete is True
        assert outcome.frames == ()

    def test_a_client_claiming_more_than_the_session_emitted_is_told_to_reload(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=3)])
        _seed_events(client, 3)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=99
        )

        assert outcome.history_incomplete is True
        assert outcome.frames == ()

    def test_a_client_that_is_up_to_date_gets_no_error_and_no_events(self) -> None:
        """The boundary the three gap conditions must not swallow."""
        client = _client(sessions=[_session_row(event_sequence=3)])
        _seed_events(client, 3)

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=3
        )

        assert outcome.history_incomplete is False
        assert outcome.error_frame is None
        assert outcome.frames == ()
        assert outcome.current_sequence == 3

    def test_a_fresh_session_with_no_events_is_not_a_gap(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=0)])

        outcome = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=0
        )

        assert outcome.history_incomplete is False
        assert outcome.frames == ()

    def test_the_error_frame_carries_no_internal_detail(self) -> None:
        """Requirement 22.9: a code to branch on, a safe sentence, and nothing else."""
        client = _client(sessions=[_session_row(event_sequence=4)])

        frame = _registry().replay(
            client, session_id=SESSION, user={"id": USER}, last_sequence=-5
        ).error_frame

        assert frame is not None
        assert set(frame["payload"]) == {"code", "message", "recoverable", "at"}
        blob = repr(frame)
        for leak in ("Traceback", "select", "backend_app", "paper_events", USER):
            assert leak not in blob

    def test_the_outcome_cannot_represent_a_partial_replay_with_an_error(self) -> None:
        """The invariant is in the type, so no future caller can produce the forbidden pair."""
        with pytest.raises(AssertionError):
            pc.ReplayOutcome(
                frames=(_frame(1),),
                current_sequence=1,
                history_incomplete=True,
                error_frame=None,
            )

    def test_the_two_new_codes_are_in_the_catalogue(self) -> None:
        assert pc.ERROR_HISTORY_INCOMPLETE in events.PAPER_ERROR_CODES
        assert pc.ERROR_CLIENT_FELL_BEHIND in events.PAPER_ERROR_CODES


# ══════════════════════════════════════════════════════════════════════════
# 3. THE SLOW CONSUMER — Requirement 19.11
# ══════════════════════════════════════════════════════════════════════════


class TestTheSlowConsumer:
    def test_the_ceiling_is_a_thousand_and_a_thousand_is_still_served(self) -> None:
        """"Exceeds 1000" read strictly: 1000 pending is served, 1001 is not."""
        registry = _registry()
        client = _client()
        connection = Connection("slow")
        _subscribe(registry, connection)

        registry.manager.note_pending(connection, MAX_PENDING_EVENTS_PER_CONNECTION - 1)
        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert MAX_PENDING_EVENTS_PER_CONNECTION == 1000
        assert outcome.delivered == (registry.subscriptions(SESSION)[0],)
        assert outcome.fell_behind == ()
        assert connection.sequences() == [1]

    def test_the_thousand_and_first_pending_event_closes_it_with_client_fell_behind(
        self,
    ) -> None:
        registry = _registry()
        client = _client()
        connection = Connection("slow")
        subscription = _subscribe(registry, connection)

        registry.manager.note_pending(connection, MAX_PENDING_EVENTS_PER_CONNECTION)
        outcome = _run(registry.broadcast(SESSION, _frame(7), supabase=client))

        assert outcome.fell_behind == (subscription,)
        assert outcome.delivered == ()
        # The one frame it receives is the error, and it is the LAST thing it receives.
        assert connection.types() == [events.PaperEvent.ERROR.value]
        assert connection.frames()[0]["payload"]["code"] == pc.ERROR_CLIENT_FELL_BEHIND
        assert connection.frames()[0]["payload"]["recoverable"] is True, (
            "a client that fell behind must be told to reconnect and resume, not that it failed"
        )
        assert connection.closed_with == [pc.CLOSE_CODE_FELL_BEHIND]

    def test_it_is_removed_from_every_registry(self) -> None:
        """Requirement 19.11's second clause, and 19.10's."""
        registry = _registry()
        client = _client()
        connection = Connection("slow")
        _subscribe(registry, connection)
        manager = registry.manager

        assert connection in manager._all_connections
        assert connection in manager._user_connections[USER]
        assert connection in manager._get_store(pc.PAPER_STORE)[SESSION]

        manager.note_pending(connection, MAX_PENDING_EVENTS_PER_CONNECTION)
        _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert registry.subscriptions(SESSION) == ()
        assert connection not in manager._all_connections
        assert USER not in manager._user_connections
        assert SESSION not in manager._get_store(pc.PAPER_STORE)
        assert manager.pending_depth(connection) == 0

    def test_every_other_connection_keeps_receiving_in_order(self) -> None:
        """Requirement 19.11's third clause - and the assertion that makes the rest meaningful.

        Six frames, three connections, and the middle one falls behind at the third. The other two
        must receive **all six, in ascending sequence order, with nothing dropped and nothing
        repeated**. A delivery loop that aborted on the first casualty would pass every assertion
        about the connection it dropped and fail this one.
        """
        registry = _registry()
        client = _client()
        first, slow, last = Connection("first"), Connection("slow"), Connection("last")
        _subscribe(registry, first)
        _subscribe(registry, slow)
        _subscribe(registry, last)

        for sequence in range(1, 7):
            if sequence == 3:
                registry.manager.note_pending(slow, MAX_PENDING_EVENTS_PER_CONNECTION)
            _run(registry.broadcast(SESSION, _frame(sequence), supabase=client))

        assert first.sequences() == [1, 2, 3, 4, 5, 6]
        assert last.sequences() == [1, 2, 3, 4, 5, 6]
        assert first.closed_with == [] and last.closed_with == []
        # The casualty received its two frames, then the error, and then nothing.
        assert slow.sequences()[:2] == [1, 2]
        assert slow.types()[-1] == events.PaperEvent.ERROR.value
        assert len(slow.sent) == 3
        assert {s.connection for s in registry.subscriptions(SESSION)} == {first, last}

    def test_a_connection_that_cannot_be_written_to_costs_nobody_else(self) -> None:
        """The heartbeat's own casualty, as the delivery loop sees it: a raising ``send_text``."""
        registry = _registry()
        client = _client()
        healthy = Connection("healthy")
        gone = Connection("gone", fail_from=1)  # the second write raises
        # The casualty is registered FIRST, deliberately: a loop that stopped at the first failure
        # would still serve a healthy connection that happened to be ahead of it in the order, so
        # putting the casualty last would make this assertion pass for the wrong reason.
        gone_subscription = _subscribe(registry, gone)
        _subscribe(registry, healthy)

        outcomes = [
            _run(registry.broadcast(SESSION, _frame(sequence), supabase=client))
            for sequence in (1, 2, 3)
        ]

        assert healthy.sequences() == [1, 2, 3]
        assert gone.sequences() == [1]
        assert outcomes[1].failed == (gone_subscription,)
        assert {s.connection for s in registry.subscriptions(SESSION)} == {healthy}
        assert registry.manager.pending_depth(gone) == 0, (
            "a released connection that kept a pending count would leak it into the next socket "
            "the allocator hands out at the same address"
        )

    def test_the_counter_returns_to_zero_after_a_normal_broadcast(self) -> None:
        """A counter that only went up would close every long-lived connection eventually."""
        registry = _registry()
        client = _client()
        connection = Connection("steady")
        _subscribe(registry, connection)

        # More than the ceiling, so a counter that never decremented would have closed this
        # connection somewhere around the thousandth frame.
        frame = _frame(1)
        for _ in range(MAX_PENDING_EVENTS_PER_CONNECTION + 200):
            _run(registry.broadcast(SESSION, frame, supabase=client))

        assert len(connection.sent) == MAX_PENDING_EVENTS_PER_CONNECTION + 200
        assert registry.manager.pending_depth(connection) == 0
        assert registry.manager.has_fallen_behind(connection) is False


# ══════════════════════════════════════════════════════════════════════════
# 4. A RAISING HANDLER — Requirement 19.13
# ══════════════════════════════════════════════════════════════════════════


class TestARaisingHandler:
    def test_it_is_logged_with_the_session_id_and_the_event_type(self, caplog: Any) -> None:
        registry = _registry()
        client = _client()

        def explodes(frame: Any) -> None:
            raise RuntimeError("handler boom")

        _subscribe(registry, Connection(), handlers=[explodes])

        with caplog.at_level(logging.ERROR, logger="PaperChannel"):
            outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert len(outcome.handler_failures) == 1
        assert SESSION in caplog.text
        assert events.PaperEvent.MARKET_TICK.value in caplog.text

    def test_the_remaining_handlers_still_run_and_none_is_unregistered(self) -> None:
        """Requirement 19.13's "SHALL NOT leave the connection registered without a handler"."""
        registry = _registry()
        client = _client()
        seen: List[str] = []

        def before(frame: Any) -> None:
            seen.append(f"before-{frame['sequence']}")

        def explodes(frame: Any) -> None:
            raise RuntimeError("handler boom")

        def after(frame: Any) -> None:
            seen.append(f"after-{frame['sequence']}")

        connection = Connection()
        subscription = _subscribe(
            registry, connection, handlers=[before, explodes, after]
        )

        _run(registry.broadcast(SESSION, _frame(1), supabase=client))
        _run(registry.broadcast(SESSION, _frame(2), supabase=client))

        assert seen == ["before-1", "after-1", "before-2", "after-2"], (
            "a handler that raised either aborted its siblings or was unregistered"
        )
        assert subscription.handlers == [before, explodes, after]
        assert subscription.closed is False
        assert connection.sequences() == [1, 2]
        assert registry.subscriptions(SESSION) == (subscription,)

    def test_one_connections_handler_does_not_cost_another_connection(self) -> None:
        registry = _registry()
        client = _client()

        def explodes(frame: Any) -> None:
            raise RuntimeError("handler boom")

        first = Connection("first")
        second = Connection("second")
        _subscribe(registry, first, handlers=[explodes])
        _subscribe(registry, second)

        _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert first.sequences() == [1]
        assert second.sequences() == [1]
        assert len(registry.subscriptions(SESSION)) == 2

    def test_an_async_handler_is_refused_as_a_handler_failure_not_awaited(self) -> None:
        """Awaiting an unknown coroutine inside the loop would let one subscriber stall the rest."""
        registry = _registry()
        client = _client()

        async def asynchronous(frame: Any) -> None:  # pragma: no cover - never awaited
            return None

        subscription = _subscribe(registry, Connection(), handlers=[asynchronous])

        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.handler_failures == ((subscription, "asynchronous"),)
        assert subscription.closed is False


# ══════════════════════════════════════════════════════════════════════════
# 5. CLEANUP — Requirements 19.10 and 19.12
# ══════════════════════════════════════════════════════════════════════════


class TestCleanup:
    def test_unsubscribe_removes_it_from_every_registry(self) -> None:
        registry = _registry()
        connection = Connection()
        subscription = _subscribe(registry, connection)
        manager = registry.manager

        _run(registry.unsubscribe(subscription))

        assert registry.subscriptions(SESSION) == ()
        assert connection not in manager._all_connections
        assert USER not in manager._user_connections
        assert SESSION not in manager._get_store(pc.PAPER_STORE)
        assert manager.pending_depth(connection) == 0
        assert subscription.closed is True

    def test_unsubscribe_is_idempotent(self) -> None:
        registry = _registry()
        subscription = _subscribe(registry, Connection())

        _run(registry.unsubscribe(subscription))
        _run(registry.unsubscribe(subscription))

        assert registry.subscriptions(SESSION) == ()

    def test_ws_manager_disconnect_sweeps_the_paper_store_too(self) -> None:
        """The sweep is ``ws_manager``'s, reused. A store it did not know about would leak."""
        registry = _registry()
        connection = Connection()
        _subscribe(registry, connection)
        manager = registry.manager

        _run(manager.disconnect(connection))

        assert SESSION not in manager._get_store(pc.PAPER_STORE)
        assert connection not in manager._all_connections
        assert USER not in manager._user_connections
        assert manager.pending_depth(connection) == 0

    def test_the_sweep_covers_every_store_the_manager_has(self) -> None:
        """A hard-coded list of six stores is how four registries kept their dead connections."""
        manager = ConnectionManager()
        swept = manager._stores()

        for name in (
            "ticker",
            "orderbook",
            "candles",
            "user",
            "pnl",
            "marketplace",
            "dashboard",
            "strategy",
            "signal_trace",
            "admin",
            "paper",
        ):
            assert manager._get_store(name) in swept, name

    def test_session_stop_releases_every_subscription_for_that_session(self) -> None:
        """Requirement 19.12."""
        registry = _registry()
        client = _client(
            sessions=[_session_row(), _session_row(OTHER_SESSION, USER)]
        )
        first, second = Connection("first"), Connection("second")
        elsewhere = Connection("elsewhere")
        _subscribe(registry, first)
        _subscribe(registry, second)
        _subscribe(registry, elsewhere, session_id=OTHER_SESSION)

        released = _run(registry.release_session(SESSION, reason="session stopped"))

        assert len(released) == 2
        assert registry.subscriptions(SESSION) == ()
        assert SESSION not in registry.sessions()
        assert first.closed_with == [pc.CLOSE_CODE_SESSION_RELEASED]
        assert second.closed_with == [pc.CLOSE_CODE_SESSION_RELEASED]
        assert all(subscription.closed for subscription in released)

        # Released means released from ``ws_manager``'s registries too, not merely forgotten by this
        # registry - a connection left in ``_all_connections`` counts against the global stream
        # limit for as long as the process lives.
        manager = registry.manager
        assert SESSION not in manager._get_store(pc.PAPER_STORE)
        assert first not in manager._all_connections
        assert second not in manager._all_connections

        # The other session of the same user is untouched, and still receives.
        assert len(registry.subscriptions(OTHER_SESSION)) == 1
        _run(registry.broadcast(OTHER_SESSION, _frame(1, OTHER_SESSION), supabase=client))
        assert elsewhere.sequences() == [1]

    def test_a_released_session_receives_nothing_further(self) -> None:
        registry = _registry()
        client = _client()
        connection = Connection()
        _subscribe(registry, connection)

        _run(registry.release_session(SESSION, reason="session deleted"))
        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.delivered == ()
        assert connection.sent == []

    def test_releasing_a_session_with_no_subscriptions_is_a_no_op(self) -> None:
        registry = _registry()

        assert _run(registry.release_session(SESSION)) == ()

    def test_a_broadcast_with_no_subscribers_reads_nothing(self) -> None:
        """Requirement 27.5's spirit: no statement is issued for a session nobody is watching."""
        registry = _registry()
        client = _client()

        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.delivered == ()
        assert client.statements == []


# ══════════════════════════════════════════════════════════════════════════
# 6. THE PRE-EMIT OWNERSHIP CHECK — Requirements 21.1 and 21.7
# ══════════════════════════════════════════════════════════════════════════


class TestPreEmitOwnership:
    def test_the_owner_is_re_read_before_the_first_emit(self) -> None:
        registry = _registry()
        client = _client()
        _subscribe(registry, Connection())
        client.statements.clear()

        _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        reads = client.statements_on(repo.SESSIONS_TABLE, "select")
        assert len(reads) == 1
        assert reads[0].cols == repo.SESSION_OWNER_SELECT
        assert reads[0].filter_value("id") == SESSION

    def test_a_mismatch_emits_nothing_further_and_closes_the_subscription(self) -> None:
        """Requirement 21.7, exactly as it is written.

        "emit no further events on that subscription and close it" - so not even a ``paper_error``,
        because an error frame WOULD be something further.
        """
        registry = _registry()
        client = _client()
        connection = Connection()
        subscription = _subscribe(registry, connection)

        _run(registry.broadcast(SESSION, _frame(1), supabase=client))
        assert connection.sequences() == [1]

        # The session changes hands. Any paper_sessions write invalidates the cached owner.
        repo.update_session_feed(
            client, user_id=USER, session_id=SESSION, feed_state="DEGRADED"
        )
        client.sessions[0]["user_id"] = OTHER_USER

        outcome = _run(registry.broadcast(SESSION, _frame(2), supabase=client))

        assert outcome.ownership_revoked == (subscription,)
        assert outcome.delivered == ()
        assert connection.sequences() == [1], "nothing further may be emitted on that subscription"
        assert connection.closed_with == [pc.CLOSE_CODE_OWNERSHIP]
        assert registry.subscriptions(SESSION) == ()
        assert subscription.closed is True

    def test_a_deleted_session_closes_its_subscriptions(self) -> None:
        """No owner could be established, so it fails closed rather than open."""
        registry = _registry()
        client = _client()
        connection = Connection()
        _subscribe(registry, connection)

        client.sessions.clear()
        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.delivered == ()
        assert len(outcome.ownership_revoked) == 1
        assert connection.sent == []
        assert registry.subscriptions(SESSION) == ()

    def test_an_ownership_read_that_did_not_complete_fails_closed(self) -> None:
        registry = _registry()
        client = _client(raise_on={("select", repo.SESSIONS_TABLE)})
        connection = Connection()
        _subscribe(registry, connection)

        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.delivered == ()
        assert len(outcome.ownership_revoked) == 1
        assert connection.sent == []

    def test_a_failed_read_is_not_cached_as_a_refusal(self) -> None:
        """A transient failure must not pin a refusal for five seconds."""
        client = _client(raise_on={("select", repo.SESSIONS_TABLE)})
        registry = _registry()

        assert registry.session_owner(client, SESSION) is None
        assert pc.cached_session_owner(SESSION) is None

        client.raise_on.clear()
        assert registry.session_owner(client, SESSION) == USER

    def test_the_owner_is_cached_for_five_seconds(self) -> None:
        registry = _registry()
        client = _client()
        _subscribe(registry, Connection())

        assert pc.OWNER_CACHE_SECONDS == 5.0
        for offset in (0.0, 1.0, 4.9):
            _run(registry.broadcast(SESSION, _frame(1), supabase=client, now=offset))
        assert len(client.statements_on(repo.SESSIONS_TABLE, "select")) == 1

        _run(registry.broadcast(SESSION, _frame(2), supabase=client, now=5.1))
        assert len(client.statements_on(repo.SESSIONS_TABLE, "select")) == 2

    def test_any_paper_sessions_write_invalidates_the_cache(self) -> None:
        """The invalidation is the repository's, so a caller cannot forget it."""
        registry = _registry()
        client = _client()

        assert registry.session_owner(client, SESSION, now=0.0) == USER
        assert pc.cached_session_owner(SESSION, now=0.0) == USER

        repo.update_session_feed(
            client, user_id=USER, session_id=SESSION, feed_state="DEGRADED"
        )

        assert pc.cached_session_owner(SESSION, now=0.0) is None

    def test_allocating_a_sequence_invalidates_the_cache_too(self) -> None:
        """``allocate_session_event_sequence`` writes ``paper_sessions``; that counts as a write."""
        registry = _registry()
        client = _client()

        assert registry.session_owner(client, SESSION, now=0.0) == USER
        repo.allocate_session_event_sequence(client, USER, SESSION)

        assert pc.cached_session_owner(SESSION, now=0.0) is None

    def test_the_ownership_read_projects_two_columns_and_no_more(self) -> None:
        """A pre-emit check whose whole output is a comparison has no business reading a payload."""
        assert repo.SESSION_OWNER_SELECT == "id,user_id"

        client = _client()
        repo.read_session_owner(client, SESSION)

        statement = client.statements_on(repo.SESSIONS_TABLE, "select")[-1]
        assert statement.cols == "id,user_id"
        assert "config" not in (statement.cols or "")


# ══════════════════════════════════════════════════════════════════════════
# 7. THE RECORDED IDENTITY — the load-bearing part of task 26.5
# ══════════════════════════════════════════════════════════════════════════


class TestTheRecordedIdentity:
    def test_it_is_taken_from_the_authenticated_session(self) -> None:
        registry = _registry()
        subscription = _subscribe(registry, Connection(), user={"id": USER})

        assert subscription.identity == USER

    def test_a_subscribe_message_claiming_another_identity_changes_nothing(self) -> None:
        """Requirements 19.5 and 21.1. The assertion task 26.5 exists for.

        The message names another tenant four different ways. The recorded identity is still the
        authenticated one, the comparison before each emit is against that, and the subscription
        keeps receiving - which is only correct because the AUTHENTICATED user does own the session.
        """
        registry = _registry()
        client = _client()
        connection = Connection()
        message = {
            "action": "subscribe",
            "channel": f"paper.{SESSION}",
            "last_sequence": 0,
            "user_id": OTHER_USER,
            "owner_id": OTHER_USER,
            "identity": OTHER_USER,
            "tenant_id": OTHER_USER,
        }

        subscription = _subscribe(
            registry, connection, user={"id": USER}, message=message
        )

        assert subscription.identity == USER
        assert OTHER_USER not in repr(subscription)

        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))
        assert outcome.delivered == (subscription,)
        assert connection.sequences() == [1]

    def test_a_subscribe_message_claiming_the_owner_does_not_help_an_intruder(self) -> None:
        """The converse, and the one that matters: the message cannot buy the comparison.

        Authenticated as ``OTHER_USER``, with a message that names ``USER`` every way it can. The
        recorded identity is ``OTHER_USER``, so the pre-emit comparison against
        ``paper_sessions.user_id`` fails and the subscription is closed with nothing emitted on it.

        (At the transport this subscription would never have been created:
        ``authorize_channel_subscription`` refuses a foreign session before this point, which
        :class:`TestNoControlIsWeakened` re-asserts. It is created here deliberately, to show that
        the pre-emit check is a second, independent refusal rather than a restatement of the first.)
        """
        registry = _registry()
        client = _client()
        connection = Connection()
        message = {
            "channel": f"paper.{SESSION}",
            "user_id": USER,
            "owner_id": USER,
            "identity": USER,
            "tenant_id": USER,
        }

        subscription = _subscribe(
            registry, connection, user={"id": OTHER_USER}, message=message
        )
        assert subscription.identity == OTHER_USER

        outcome = _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert outcome.ownership_revoked == (subscription,)
        assert outcome.delivered == ()
        assert connection.sent == [], "an intruder received a frame"
        assert connection.closed_with == [pc.CLOSE_CODE_OWNERSHIP]

    def test_a_replay_reads_last_sequence_from_the_message_and_identity_from_the_user(
        self,
    ) -> None:
        """The one value a message legitimately supplies is a POSITION, not an identity."""
        registry = _registry()
        client = _client(sessions=[_session_row(event_sequence=4)])
        _seed_events(client, 4)
        message = {"last_sequence": 2, "user_id": OTHER_USER}

        outcome = registry.replay(
            client,
            session_id=SESSION,
            user={"id": USER},
            last_sequence=message["last_sequence"],
        )

        assert [frame["sequence"] for frame in outcome.frames] == [3, 4]
        assert client.statements_on(repo.EVENTS_TABLE, "select")[-1].filter_value(
            "user_id"
        ) == USER

    def test_a_subscription_with_no_authenticated_identity_is_refused(self) -> None:
        registry = _registry()

        for user in (None, {}, {"id": ""}, {"id": None}):
            with pytest.raises(PermissionError):
                _subscribe(registry, Connection(), user=user)
        assert registry.subscriptions(SESSION) == ()

    @pytest.mark.parametrize(
        "user",
        [
            {"id": USER},
            {"sub": USER},
            {"user_id": USER},
            {"id": USER, "sub": OTHER_USER},
            None,
            {},
            {"id": ""},
        ],
    )
    def test_it_reads_the_same_identity_websocket_auth_reads(self, user: Any) -> None:
        """One identity for the subscription and for the authorisation that admitted it.

        Duplicated expressions are how two modules come to disagree about who the caller is, so the
        duplication ``paper_channel.authenticated_identity`` documents is pinned here instead of
        argued for in a comment.
        """
        assert pc.authenticated_identity(user) == WA._user_identity(user)


# ══════════════════════════════════════════════════════════════════════════
# 8. NO CONTROL IS WEAKENED — Requirements 19.4 and 21.4
# ══════════════════════════════════════════════════════════════════════════


class TestNoControlIsWeakened:
    @pytest.mark.asyncio
    async def test_a_foreign_session_and_a_nonexistent_one_are_still_indistinguishable(
        self,
    ) -> None:
        """Task 26.4 and 26.5 add refusals. They may not change this one.

        The same assertion ``tests/test_task_26_3_paper_channel.py`` makes, repeated here because
        these tasks are where a "helpful" error message about a session that does not exist would
        have been added.
        """
        store = FakeSupabase(sessions=[_session_row()])

        from backend_app.core import dependencies as D

        async def _client_for(token: Any) -> Any:
            return store

        original = D.create_request_supabase_async
        D.create_request_supabase_async = _client_for  # type: ignore[assignment]
        try:
            missing = await WA.authorize_channel_subscription(
                "paper.does-not-exist", {"id": USER}
            )
            foreign = await WA.authorize_channel_subscription(
                f"paper.{SESSION}", {"id": OTHER_USER}
            )
        finally:
            D.create_request_supabase_async = original  # type: ignore[assignment]

        assert missing.allowed is foreign.allowed is False
        assert missing.code == foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN
        assert missing.reason == foreign.reason
        missing_frame = missing.refusal_frame()
        foreign_frame = foreign.refusal_frame()
        assert {k: v for k, v in missing_frame.items() if k != "channel"} == {
            k: v for k, v in foreign_frame.items() if k != "channel"
        }

    def test_the_paper_family_and_its_relation_are_unchanged(self) -> None:
        assert C.PAPER_FAMILY.namespace == pc.PAPER_STORE == "paper"
        relation = WA._owner_relations()["paper"]
        assert relation.table == repo.SESSIONS_TABLE
        assert relation.owner_column == "user_id"

    def test_the_delivery_path_writes_nothing(self) -> None:
        """A broadcast is a read and a send. Requirement 21.4's "leave records unchanged"."""
        registry = _registry()
        client = _client()
        _subscribe(registry, Connection())
        client.statements.clear()

        _run(registry.broadcast(SESSION, _frame(1), supabase=client))

        assert client.wrote_anything() is False

    def test_a_replay_writes_nothing(self) -> None:
        client = _client(sessions=[_session_row(event_sequence=3)])
        _seed_events(client, 3)

        _registry().replay(client, session_id=SESSION, user={"id": USER}, last_sequence=0)

        assert client.wrote_anything() is False

    def test_the_gap_error_is_not_appended_to_the_append_only_log(self) -> None:
        """A reconnect loop must not be able to write a row per attempt."""
        client = _client(sessions=[_session_row(event_sequence=9)])
        registry = _registry()

        for _ in range(5):
            outcome = registry.replay(
                client, session_id=SESSION, user={"id": USER}, last_sequence=-1
            )
            assert outcome.history_incomplete is True

        assert client.events == []
        assert client.wrote_anything() is False
