"""
backend_app/backend/paper/paper_channel.py - delivery, replay, backpressure and the pre-emit
ownership check for ``paper.{session_id}``.

Spec: marketplace-subscriptions-paper-trading tasks 26.4 and 26.5. ``design.md`` ->
"``paper/paper_events.py`` and the Paper_Channel", the "Replay buffer", "Heartbeat, slow consumers,
cleanup" and "Ownership re-verification before each emit" paragraphs. Requirements 19.8, 19.9,
19.10, 19.11, 19.12, 19.13, 21.1, 21.7, 27.5.

Exposes
-------
PaperSubscription             one connection's registration on one session, and its identity
PaperChannelRegistry          the session -> subscriptions registry, and the delivery path
REGISTRY                      the process's registry; the module functions delegate to it
subscribe / unsubscribe / release_session / broadcast / replay
                              the module-level forms, which is what ``paper_events`` re-exports
ReplayOutcome / BroadcastOutcome
                              what a replay and a broadcast report, so a caller asserts on a value
frame_from_row                one stored ``paper_events`` row as the frame a subscriber receives
error_frame                   a ``paper_error`` frame, built through the envelope like any other
authenticated_identity        the subscription's identity, from the AUTHENTICATED user - only
invalidate_session_owner      drop the cached owner for one session, or for all of them
OWNER_CACHE_SECONDS / REPLAY_ROW_CAP / MAX_PENDING_EVENTS

WHAT IS REUSED, AND WHAT IS NEW
-------------------------------
Almost everything here is a composition of parts that already existed. Stated explicitly, because
"extend in place" is the instruction and a reader has to be able to check it:

* **Authorisation: nothing new, not one line.** ``core/websocket_auth.authorize_channel_subscription``
  already resolves ``paper_sessions.user_id`` through the ``_OwnerRelation`` registered for
  ``PAPER_FAMILY``, and already refuses another user's session and a nonexistent one through the
  same ``_forbidden`` frame, so the two are indistinguishable (Requirements 19.4, 21.4). This module
  is reached AFTER that decision. :func:`PaperChannelRegistry.broadcast`'s owner comparison can only
  ADD a refusal to the ones already made; there is no path here by which a subscription that
  authorisation refused becomes admitted.
* **Registries and sweeping: ``api_ws/ws_manager.py``.** ``"paper"`` is a store in
  ``ConnectionManager._get_store``, so ``subscribe`` / ``unsubscribe`` / ``disconnect`` sweep it
  along with ``_all_connections``, ``_user_connections`` and every other per-channel store
  (Requirement 19.10). No sweeping is written here - :meth:`PaperChannelRegistry._release` calls
  ``manager.unsubscribe``.
* **Heartbeat: untouched.** It lives in ``api_ws/ws_routes.py``
  (``_heartbeat_task``: ping every ``_HEARTBEAT_INTERVAL_SECONDS = 10``, close on
  ``_HEARTBEAT_TIMEOUT_SECONDS = 30`` of silence) and closes the socket, which lands here as a
  failed send and then as a ``manager.unsubscribe``. See "WHAT A SINGLE-PROCESS TEST CANNOT
  ESTABLISH" below for what that does and does not prove.
* **The buffer: ``paper_events`` the TABLE.** ``paper_repository.read_session_events`` is the replay
  read. There is no in-memory ring, deliberately: a ring restarts empty, and Requirement 19.8's
  floor ("at least the most recent 1000 events for at least 5 minutes") has to hold across the
  restart a reconnecting client is often reconnecting *because of*.
* **The envelope: ``paper_events``.** Every frame this module produces - replayed rows included -
  goes through ``PaperEventEnvelope``, so a replayed frame and a live one are the same eight fields
  in the same order.

New here: the per-session subscription registry with the recorded identity, the pending-depth
policy that turns ``ws_manager``'s counter into Requirement 19.11's close, the replay gap
classification of Requirement 19.9, and the five-second owner cache of Requirement 21.7.

THE IDENTITY COMPARED IS THE AUTHENTICATED ONE. THAT IS THE LOAD-BEARING PART
----------------------------------------------------------------------------
:meth:`PaperChannelRegistry.subscribe` takes ``user`` - the authenticated server-side session's
user mapping - and records :func:`authenticated_identity` of it on the subscription. It also takes
the client's ``message``, because that is where ``last_sequence`` legitimately comes from, and it
reads **nothing else** out of it: not ``user_id``, not ``owner_id``, not ``identity``, not
``tenant_id``. Requirement 21.1 forbids deriving the acting identity from a WebSocket message, and
Requirement 19.5 says ownership is re-verified rather than taken from the subscribe message. A
subscribe message that names another user therefore changes nothing at all - the recorded identity
is the authenticated one, the comparison in :meth:`broadcast` is against that, and
``tests/test_task_26_4_paper_channel_delivery.py`` asserts it directly rather than by inspection.

WHAT A SINGLE-PROCESS TEST CANNOT ESTABLISH, STATED HERE RATHER THAN IMPLIED
---------------------------------------------------------------------------
Three of Requirement 19.10's and 19.11's clauses are about wall-clock behaviour of a real socket
and are **not** established by anything in this repository's test suite:

* "**failing to respond to 2 consecutive heartbeats**" is counted by ``ws_routes._heartbeat_task``
  as a 30-second silence against a 10-second ping - which is three intervals, not two. That
  mechanism is used unchanged, as the task requires, so the mapping from "2 consecutive
  heartbeats" to "30 seconds without a pong" is an interpretation of the existing timer and not
  something this module enforces.
* "**closed within 5 seconds of that classification**" is the timer's own promptness. A test that
  asserted it would be asserting ``asyncio.sleep``.
* The pending-queue depth counted here is **events handed to a connection that have not returned
  from ``send_text``**. On a real ASGI transport that is not the same number as the operating
  system's socket send buffer occupancy: a frame ``send_text`` has returned from may still be in
  flight. The counter is the strongest measure available from inside the process, and the
  requirement's threshold is applied to it consistently, which is what makes it testable at all.

NO ``random``, AND ONE FIRST-PARTY IMPORT WORTH NAMING
-----------------------------------------------------
``tests/test_paper_no_random.py`` walks this package. This module imports ``asyncio``, ``json``,
``logging``, ``time``, ``dataclasses``, ``typing`` - and, first-party,
``backend_app.api_ws.ws_manager`` (read: ``asyncio``, ``json``, ``logging``, ``collections``,
``typing``, ``fastapi`` and ``backend_app.core.cache``; no ``random``, no reconnect jitter),
``backend_app.backend.ws_channels``, ``backend_app.backend.paper.paper_events`` and
``backend_app.backend.paper.paper_repository``.
There is no draw on any path here: an ``event_id`` comes from ``paper_events.new_event_id``
(``uuid4``, the platform CSPRNG, for the reason recorded there) and nothing else is generated.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.api_ws.ws_manager import (
    MAX_PENDING_EVENTS_PER_CONNECTION,
    ConnectionManager,
)
from backend_app.api_ws.ws_manager import manager as default_manager
from backend_app.backend.metrics import guarded_collector
from backend_app.backend.paper import paper_repository as repository
from backend_app.backend.paper.paper_events import (
    PaperErrorPayload,
    PaperEvent,
    PaperEventEnvelope,
    build_envelope,
    format_emitted_at,
    paper_channel,
    paper_event,
    utc_now,
)
from backend_app.backend.ws_channels import PAPER_FAMILY

logger = logging.getLogger("PaperChannel")

# ══════════════════════════════════════════════════════════════════════════
# THE FIGURES, EACH SPELLED ONCE
# ══════════════════════════════════════════════════════════════════════════

#: ``ws_manager``'s channel key for this family. Read off ``PAPER_FAMILY`` rather than written as
#: ``"paper"``, so the store key, the channel name and the owner relation cannot drift apart -
#: ``ws_manager._get_store`` has to hold the same string, and that one is a literal because it is a
#: dictionary key in a module the paper package must not be imported into.
PAPER_STORE = PAPER_FAMILY.namespace

#: Requirement 19.8's cap, owned by the repository (it is the ``limit`` on the statement) and
#: re-exported here so a caller reads one number.
REPLAY_ROW_CAP = repository.SESSION_EVENT_REPLAY_CAP

#: Requirement 19.11's ceiling, owned by ``ws_manager`` (it counts the depth) and re-exported for
#: the same reason.
MAX_PENDING_EVENTS = MAX_PENDING_EVENTS_PER_CONNECTION

#: Requirement 21.7's cache lifetime, in seconds. ``design.md``'s figure, exactly.
OWNER_CACHE_SECONDS = 5.0

#: The two ``paper_error`` codes tasks 26.4 and 26.5 add. ``paper_events.PAPER_ERROR_CODES`` is the
#: catalogue; these are named here as well so the emitting call sites spell them once.
ERROR_CLIENT_FELL_BEHIND = "CLIENT_FELL_BEHIND"
ERROR_HISTORY_INCOMPLETE = "HISTORY_INCOMPLETE"

#: The sentence a client that fell behind receives. Safe by Requirement 22.9: a code to branch on,
#: no identifier, no path, no internal detail.
FELL_BEHIND_MESSAGE = (
    "This connection fell behind the session's event stream and was closed. Reconnect and resume "
    "from the last sequence number you applied."
)

#: The sentence Requirement 19.9 asks for: history is incomplete, reload session state from the API.
HISTORY_INCOMPLETE_MESSAGE = (
    "The event history you asked to resume from is no longer complete. Reload the session state "
    "from the Paper Trading API; new events continue from the current sequence number."
)

#: WebSocket close codes. 1011 (internal error) for an ownership answer that no longer holds, 1013
#: (try again later) for a consumer that fell behind - a client that reconnects and resumes is
#: exactly what 1013 means, and it is not the same event as a policy violation.
CLOSE_CODE_OWNERSHIP = 1011
CLOSE_CODE_FELL_BEHIND = 1013
CLOSE_CODE_SESSION_RELEASED = 1000


# ══════════════════════════════════════════════════════════════════════════
# THE AUTHENTICATED IDENTITY
# ══════════════════════════════════════════════════════════════════════════


def authenticated_identity(user: Any) -> Optional[str]:
    """The acting user's id, from the AUTHENTICATED session mapping. ``None`` when there is none.

    The same three keys ``core/websocket_auth._user_identity`` reads (``id``, then ``sub``, then
    ``user_id``) and an object's ``.id``, so a subscription's recorded identity and the identity
    ``authorize_channel_subscription`` compared are the same string for the same user.

    Written here rather than imported, for one reason: importing ``core.websocket_auth`` into this
    package would pull ``core.auth_middleware`` and, through it, a dependency graph that
    ``tests/test_paper_no_random.py``'s pinned first-party list would have to be widened for. The
    duplication is one expression, and
    ``tests/test_task_26_4_paper_channel_delivery.py::TestTheRecordedIdentity::
    test_it_reads_the_same_identity_websocket_auth_reads`` asserts the two agree over a matrix of
    mappings, so they cannot diverge silently.

    ``user`` is never a WebSocket message. Requirement 21.1.
    """
    if user is None:
        return None
    if isinstance(user, Mapping):
        candidate = user.get("id") or user.get("sub") or user.get("user_id")
    else:
        candidate = getattr(user, "id", None)
    text = str(candidate) if candidate else ""
    return text or None


def _require_session_id(session_id: Any) -> str:
    text = "" if session_id is None else str(session_id).strip()
    if not text:
        raise ValueError("a Paper_Channel operation needs a session id")
    return text


# ══════════════════════════════════════════════════════════════════════════
# FRAMES
# ══════════════════════════════════════════════════════════════════════════


def frame_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One stored ``paper_events`` row as the frame a subscriber receives.

    The row is put back through :class:`~backend_app.backend.paper.paper_events.PaperEventEnvelope`
    rather than reshaped by hand, so a replayed frame is the same eight fields in the same order as
    a live one - which is what lets a client apply both through one code path and deduplicate on
    ``event_id`` (Requirement 19.8).

    ``build_envelope`` is deliberately NOT used: it re-validates the payload against its per-type
    model, and this payload was already validated when it was emitted. Re-validating a stored row
    would mean a replay could fail on a payload the database happily holds - a schema that widened
    since the row was written would make old history unreadable, which is the opposite of what a
    durable buffer is for. The envelope's own constraints (``sequence >= 1``, the sixteen types, a
    non-empty ``event_id`` and channel) are still applied.
    """
    session_id = _require_session_id(row.get("session_id"))
    payload = row.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"paper_events.payload for session {session_id!r} sequence {row.get('sequence')!r} "
            f"is a {type(payload).__name__}, not a mapping; it cannot be framed"
        )
    envelope = PaperEventEnvelope(
        schema_version=row.get("schema_version"),
        channel=paper_channel(session_id),
        session_id=session_id,
        type=paper_event(row.get("event_type")),
        sequence=row.get("sequence"),
        event_id=row.get("event_id"),
        emitted_at=row.get("emitted_at"),
        payload=dict(payload),
    )
    return envelope.frame()


def error_frame(
    session_id: Any,
    *,
    code: str,
    message: str,
    recoverable: bool,
    sequence: Any,
    at: Any = None,
) -> Dict[str, Any]:
    """A ``paper_error`` frame, built through the envelope like every other frame.

    NOT written to ``paper_events``, and that is a decision rather than an omission. Both codes this
    module emits - ``HISTORY_INCOMPLETE`` and ``CLIENT_FELL_BEHIND`` - are facts about ONE
    CONNECTION, not about the session: a client that reconnects in a loop would otherwise append a
    row to an append-only log and consume a session sequence number on every attempt, and every
    other subscriber of that session would receive an error that did not happen to them.
    ``paper_market_feed``'s ``FEED_DISCONNECTED`` is recorded, and correctly so - a dropped feed
    happened to the session.

    ``sequence`` therefore carries the session's CURRENT sequence rather than a freshly allocated
    one: it tells the client where the live stream is, which is precisely what Requirement 19.9's
    "continue delivering newly emitted events from the current sequence number" needs it to know.
    Floored at 1, because ``chk_paper_event_sequence`` is ``>= 1`` and a session that has emitted
    nothing has no sequence to report.
    """
    moment = at if at is not None else utc_now()
    number = int(sequence or 0)
    return build_envelope(
        session_id=_require_session_id(session_id),
        event_type=PaperEvent.ERROR,
        sequence=max(number, 1),
        emitted_at=moment,
        payload=PaperErrorPayload(
            code=code,
            message=message,
            recoverable=recoverable,
            at=format_emitted_at(moment),
        ),
    ).frame()


# ══════════════════════════════════════════════════════════════════════════
# THE 5-SECOND OWNER CACHE (Requirement 21.7)
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT IS CACHED, WHAT INVALIDATES IT, AND WHAT WINDOW REMAINS
# -----------------------------------------------------------
# Cached: ``paper_sessions.user_id`` for one session id, for :data:`OWNER_CACHE_SECONDS`.
#
# Invalidated by: any ``paper_sessions`` write issued through ``paper_repository``. That is
# mechanical rather than remembered - the repository is the only module in this package that issues
# statements, and :func:`install_session_write_invalidation` registers this module's invalidator
# with ``paper_repository.on_session_write`` so ``update_session_feed`` and
# ``allocate_session_event_sequence`` both notify it. A caller cannot forget to invalidate, because
# a caller never invalidates.
#
# The window that remains, stated plainly:
#
# * **A write from another process or another instance is not seen.** The cache is per-process and
#   PostgREST offers no invalidation channel, so an ownership change made elsewhere is visible only
#   when the entry expires - up to five seconds. That is the staleness ``design.md`` accepts and it
#   is the reason the lifetime is five seconds rather than five minutes.
# * **A row changed outside this repository is not seen either** - a manual SQL update, or a future
#   module that writes ``paper_sessions`` without going through ``paper_repository``. The second is
#   the one to watch, and it is why the notification lives in the repository: a new writer there
#   inherits the invalidation, while a new writer elsewhere would not.
# * **In practice the cache almost never serves a hit on a busy session**, because
#   ``allocate_session_event_sequence`` writes ``paper_sessions.event_sequence`` for every emitted
#   event and therefore invalidates the entry it is about to need. That makes the common path a read
#   per emit - which is *more* correct than the design's five-second window and is recorded here
#   because it is a cost (one extra statement per event) that a reader of ``design.md`` would not
#   expect. It is not compensated for by lengthening the lifetime: the whole value of the cache is
#   an ownership answer that is at most five seconds old.


@dataclass(frozen=True)
class _CachedOwner:
    """One session's owner, and the monotonic instant the entry stops being usable."""

    owner: Optional[str]
    expires_at: float


#: ``session_id -> _CachedOwner``. Module scope, because the subscriptions it guards are
#: process-wide and a per-registry cache would answer differently in two registries.
_OWNER_CACHE: Dict[str, _CachedOwner] = {}


def invalidate_session_owner(session_id: Any = None) -> None:
    """Drop the cached owner for ``session_id``, or the whole cache when ``session_id`` is ``None``.

    ``None`` clears everything, which is what a listener with no id to offer must do: an
    invalidation that cannot name its session and therefore dropped nothing would be worse than no
    cache at all.
    """
    if session_id is None:
        _OWNER_CACHE.clear()
        return
    _OWNER_CACHE.pop(str(session_id).strip(), None)


def cached_session_owner(session_id: Any, *, now: Optional[float] = None) -> Optional[str]:
    """The cached owner of ``session_id``, or ``None`` when there is no live entry.

    ``None`` is deliberately ambiguous between "not cached" and "cached as unowned", because both
    lead to the same place: a fresh read, and a refusal if that read cannot establish an owner.
    """
    entry = _OWNER_CACHE.get(_require_session_id(session_id))
    if entry is None:
        return None
    if entry.expires_at <= (time.monotonic() if now is None else now):
        return None
    return entry.owner


def install_session_write_invalidation() -> None:
    """Register :func:`invalidate_session_owner` with ``paper_repository``. Idempotent.

    Called at import, so the cache cannot be live before its invalidation is.
    """
    repository.on_session_write(invalidate_session_owner)


install_session_write_invalidation()


# ══════════════════════════════════════════════════════════════════════════
# SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class PaperSubscription:
    """One connection's registration on one Paper_Session.

    ``identity`` is the authenticated identity recorded at subscribe time (Requirements 19.5, 21.1,
    21.7) and is never rewritten - there is no setter and no code path that assigns it after
    construction, which is what makes the pre-emit comparison meaningful.

    ``handlers`` is a list rather than a tuple because Requirement 19.13 is about what happens to
    the OTHER handlers when one raises: a raising handler is logged and KEPT, so the count cannot
    drift downwards and a connection cannot end up registered with fewer handlers than it asked
    for. See :meth:`PaperChannelRegistry._deliver_to_handlers`.
    """

    session_id: str
    channel: str
    connection: Any
    identity: str
    handlers: List[Callable[[Mapping[str, Any]], Any]] = field(default_factory=list)
    closed: bool = False
    #: Why it was closed, for a caller that wants to report it. Never sent to the client as-is.
    closed_reason: str = ""


@dataclass(frozen=True)
class ReplayOutcome:
    """What a replay request produced. Requirements 19.8, 19.9.

    ``frames`` and ``error_frame`` are mutually exclusive by construction: a gap replays
    **nothing partial**, so ``history_incomplete`` implies ``frames == ()``.
    """

    frames: Tuple[Dict[str, Any], ...]
    current_sequence: int
    truncated: bool = False
    history_incomplete: bool = False
    error_frame: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.history_incomplete and self.frames:
            raise AssertionError(
                "Requirement 19.9 forbids replaying a partial history alongside "
                "HISTORY_INCOMPLETE; this outcome carries both"
            )


@dataclass(frozen=True)
class BroadcastOutcome:
    """Who received one frame, and who stopped being a subscriber because of it.

    Four disjoint groups, so a caller can assert on the reason rather than on a count:
    ``delivered`` was sent to, ``fell_behind`` exceeded the pending ceiling (Requirement 19.11),
    ``ownership_revoked`` failed the pre-emit owner comparison (Requirement 21.7), ``failed`` could
    not be written to at all.
    """

    delivered: Tuple[PaperSubscription, ...] = ()
    fell_behind: Tuple[PaperSubscription, ...] = ()
    ownership_revoked: Tuple[PaperSubscription, ...] = ()
    failed: Tuple[PaperSubscription, ...] = ()
    handler_failures: Tuple[Tuple[PaperSubscription, str], ...] = ()

    @property
    def released(self) -> Tuple[PaperSubscription, ...]:
        """Every subscription this broadcast removed, for any reason."""
        return self.fell_behind + self.ownership_revoked + self.failed


class PaperChannelRegistry:
    """``session_id -> the subscriptions on it``, plus the delivery path over them.

    The registry is ORDERED per session (a list, in registration order) rather than a set, because
    Requirement 19.11 requires that dropping one connection leave the others "without dropping or
    reordering their events" - an assertion that needs a defined order to be about anything. The
    per-connection ``ws_manager`` store stays a set, which is fine: it answers "is this connection
    registered", not "in what order are they served".
    """

    def __init__(self, manager: Optional[ConnectionManager] = None) -> None:
        #: The ``ws_manager`` whose registries and pending counter this registry uses. Injectable so
        #: a test drives a manager of its own rather than the process singleton.
        self.manager = manager if manager is not None else default_manager
        self._by_session: Dict[str, List[PaperSubscription]] = {}

    # ── query ─────────────────────────────────────────────────────────────

    def subscriptions(self, session_id: Any) -> Tuple[PaperSubscription, ...]:
        """Every live subscription on ``session_id``, in registration order."""
        return tuple(self._by_session.get(_require_session_id(session_id), ()))

    def sessions(self) -> Tuple[str, ...]:
        """Every session with at least one subscription."""
        return tuple(self._by_session)

    # ── register / release ────────────────────────────────────────────────

    async def subscribe(
        self,
        session_id: Any,
        connection: Any,
        *,
        user: Any,
        handlers: Sequence[Callable[[Mapping[str, Any]], Any]] = (),
        message: Optional[Mapping[str, Any]] = None,
    ) -> PaperSubscription:
        """Register ``connection`` on ``session_id`` under the AUTHENTICATED identity of ``user``.

        Call order at the transport: ``authorize_channel_subscription`` FIRST, then this. Nothing
        here re-decides that authorisation and nothing here can admit a connection it refused; this
        is the registration, and the identity it records is what every later emit is compared
        against.

        ``message`` is the client's subscribe frame. It is accepted so the signature reflects
        reality - ``last_sequence`` comes from it and is read by :meth:`replay` - and **no identity
        is read from it**. A message carrying ``user_id``, ``owner_id``, ``identity`` or
        ``tenant_id`` changes nothing (Requirements 19.5, 21.1).

        Raises:
            PermissionError: ``user`` carries no identity. A subscription with no identity could
                not be compared against an owner, so it is refused rather than registered - the
                same direction ``websocket_auth`` refuses in for the same absence.
        """
        sid = _require_session_id(session_id)
        identity = authenticated_identity(user)
        if not identity:
            raise PermissionError(
                "a Paper_Channel subscription needs an authenticated identity; the identity is "
                "taken from the authenticated session and never from the subscribe message "
                "(Requirements 19.5, 21.1, 21.6)"
            )

        subscription = PaperSubscription(
            session_id=sid,
            channel=paper_channel(sid),
            connection=connection,
            identity=identity,
            handlers=list(handlers),
        )

        # ws_manager owns the registries. ``user_id`` is passed explicitly because the channel key
        # is a session id, so ``_extract_user_id`` can read nothing from it - see its docstring.
        await self.manager.subscribe(PAPER_STORE, sid, connection, user_id=identity)
        self._by_session.setdefault(sid, []).append(subscription)
        logger.info(
            "[PaperChannel] +sub %s (%d subscription(s) on this session)",
            subscription.channel,
            len(self._by_session[sid]),
        )
        return subscription

    async def unsubscribe(
        self, subscription: PaperSubscription, *, reason: str = "unsubscribed"
    ) -> None:
        """Release one subscription. Idempotent."""
        await self._release(subscription, reason=reason, close_code=None)

    async def release_session(
        self, session_id: Any, *, reason: str = "session released"
    ) -> Tuple[PaperSubscription, ...]:
        """Release EVERY subscription on ``session_id``. Requirement 19.12.

        Called by the session stop and delete paths. Every subscription is released even if one of
        them fails to close: a session that has stopped must not keep a registration alive because
        a socket misbehaved on the way out, which is why each close is attempted independently and
        the list is snapshotted before the loop.
        """
        sid = _require_session_id(session_id)
        released = tuple(self._by_session.get(sid, ()))
        for subscription in released:
            await self._release(
                subscription, reason=reason, close_code=CLOSE_CODE_SESSION_RELEASED
            )
        # ``_release`` empties the list as it goes; drop the key so ``sessions()`` does not report a
        # session with nothing on it.
        self._by_session.pop(sid, None)
        if released:
            logger.info(
                "[PaperChannel] released %d subscription(s) on paper.%s (%s)",
                len(released),
                sid,
                reason,
            )
        return released

    async def _release(
        self,
        subscription: PaperSubscription,
        *,
        reason: str,
        close_code: Optional[int],
    ) -> None:
        """Remove ``subscription`` from every registry, and close its socket when asked.

        No sweeping is written here. ``manager.unsubscribe`` is what removes the connection from the
        ``"paper"`` store, from ``_all_connections``, from ``_user_connections`` and from the pending
        counter (Requirement 19.10); this only forgets the ordered per-session entry that
        ``ws_manager`` has no concept of.
        """
        subscription.closed = True
        subscription.closed_reason = reason
        entries = self._by_session.get(subscription.session_id)
        if entries is not None:
            self._by_session[subscription.session_id] = [
                entry for entry in entries if entry is not subscription
            ]
            if not self._by_session[subscription.session_id]:
                self._by_session.pop(subscription.session_id, None)

        try:
            await self.manager.unsubscribe(
                PAPER_STORE,
                subscription.session_id,
                subscription.connection,
                user_id=subscription.identity,
            )
        except Exception as exc:  # noqa: BLE001
            # A registry that refused a removal is a leak, so it is reported - but it must not stop
            # the socket from being closed, which is the part the client can observe.
            logger.warning(
                "[PaperChannel] releasing %s from the ws_manager registries failed (%s)",
                subscription.channel,
                exc,
            )
        self.manager.forget_connection(subscription.connection)

        if close_code is None:
            return
        close = getattr(subscription.connection, "close", None)
        if close is None:
            return
        try:
            result = close(code=close_code)
            if hasattr(result, "__await__"):
                await result
        except TypeError:
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "[PaperChannel] closing %s reported %s; it is already gone",
                    subscription.channel,
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[PaperChannel] closing %s reported %s; it is already gone",
                subscription.channel,
                exc,
            )

    # ── replay (Requirements 19.8, 19.9) ──────────────────────────────────

    def replay(
        self,
        supabase: Any,
        *,
        session_id: Any,
        user: Any,
        last_sequence: Any = 0,
    ) -> ReplayOutcome:
        """Every retained event of ``session_id`` above ``last_sequence``, or the gap report.

        ``last_sequence`` is the ONE value read out of the client's subscribe message, and it is a
        position in a stream rather than an identity - it cannot widen what the client may see,
        because the read underneath it has ``user_id`` as a predicate.

        The three unrecoverable-gap conditions, and why they are the only three (Requirement 19.9):

        1. ``last_sequence < 0``. A sequence starts at 1, so a negative value names no position and
           cannot be reconciled.
        2. The session is not readable, or its log is empty while its counter says events were
           emitted above ``last_sequence`` - "a session whose events went with it".
        3. The oldest retained event above ``last_sequence`` is not ``last_sequence + 1``, i.e.
           there is a hole between what the client holds and what is retained. Because
           ``paper_events`` retains everything this cannot arise from expiry, which is exactly why
           it is checked rather than assumed away: it is what a deletion would look like.

        A fourth, related case is folded into (2): ``last_sequence`` ABOVE the session's current
        sequence. The client claims to hold events the session never emitted, so its history cannot
        be reconciled either, and telling it to reload is the only honest answer.

        Every gap replays **nothing partial** and reports the current sequence, so the caller emits
        the error frame and then continues from the live stream.
        """
        sid = _require_session_id(session_id)
        identity = authenticated_identity(user)
        if not identity:
            raise PermissionError(
                "a Paper_Channel replay needs an authenticated identity (Requirements 21.1, 21.6)"
            )
        floor = int(last_sequence or 0)

        session = repository.read_session(supabase, identity, sid)
        current = 0
        if session is not None:
            try:
                current = int(session.get("event_sequence") or 0)
            except (TypeError, ValueError):
                current = 0

        if floor < 0 or session is None or floor > current:
            return self._gap(sid, current)

        # No ``limit`` is passed. The cap is the repository's - it is the ``limit`` on the statement
        # and the truncation of what comes back - and it has exactly one owner for a reason: a cap
        # supplied by every caller is a cap the next caller can supply differently, and this one is
        # what keeps an unbounded row set off the wire.
        rows = repository.read_session_events(
            supabase, identity, sid, after_sequence=floor
        )
        if not rows:
            if current > floor:
                # The counter says there are events above ``floor`` and none is retained: they went
                # with something. Reported as a gap rather than as "you are up to date", because
                # the second would leave the client silently missing history.
                return self._gap(sid, current)
            return ReplayOutcome(frames=(), current_sequence=current)

        lowest = int(rows[0].get("sequence"))
        if lowest > floor + 1:
            return self._gap(sid, current)

        frames = tuple(frame_from_row(row) for row in rows)
        highest = int(rows[-1].get("sequence"))
        return ReplayOutcome(
            frames=frames,
            current_sequence=current,
            truncated=len(rows) >= REPLAY_ROW_CAP and current > highest,
        )

    def _gap(self, session_id: str, current: int) -> ReplayOutcome:
        """Requirement 19.9's answer: no partial history, one ``paper_error``, and where live is."""
        logger.warning(
            "[PaperChannel] replay for paper.%s cannot be reconciled; emitting %s and continuing "
            "from sequence %d",
            session_id,
            ERROR_HISTORY_INCOMPLETE,
            current,
        )
        return ReplayOutcome(
            frames=(),
            current_sequence=current,
            history_incomplete=True,
            error_frame=error_frame(
                session_id,
                code=ERROR_HISTORY_INCOMPLETE,
                message=HISTORY_INCOMPLETE_MESSAGE,
                recoverable=False,
                sequence=current,
            ),
        )

    # ── the pre-emit ownership check (Requirement 21.7) ───────────────────

    def session_owner(
        self, supabase: Any, session_id: Any, *, now: Optional[float] = None
    ) -> Optional[str]:
        """``paper_sessions.user_id`` for ``session_id``, through the five-second cache.

        ``None`` means no owner could be established - a deleted session, an unreadable row, or a
        read that did not complete. Every one of those is a refusal at the call site, and none of
        them is cached: a transient transport failure must not pin a refusal for five seconds, and a
        deletion will be answered the same way by the next read anyway.
        """
        sid = _require_session_id(session_id)
        instant = time.monotonic() if now is None else now
        entry = _OWNER_CACHE.get(sid)
        if entry is not None and entry.expires_at > instant:
            return entry.owner

        try:
            owner = repository.read_session_owner(supabase, sid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[PaperChannel] the pre-emit ownership read for paper.%s did not complete (%s); "
                "no event is emitted on its subscriptions",
                sid,
                exc,
            )
            return None

        if owner is None:
            # Not cached: see the docstring. A session that is genuinely gone costs one read per
            # emit until its subscriptions are released, and they are released by this same emit.
            return None
        _OWNER_CACHE[sid] = _CachedOwner(owner=owner, expires_at=instant + OWNER_CACHE_SECONDS)
        return owner

    # ── delivery (Requirements 19.11, 19.13, 21.7) ────────────────────────

    async def broadcast(
        self,
        session_id: Any,
        frame: Mapping[str, Any],
        *,
        supabase: Any,
        now: Optional[float] = None,
    ) -> BroadcastOutcome:
        """Deliver ``frame`` to every subscription on ``session_id``. One frame, one owner read.

        The loop is deliberately the shape ``ws_manager._broadcast`` already had, and that shape was
        read before it was relied on: it iterates a snapshot of the subscribers, ``await``s each
        send inside its own ``try``, collects the failures into ``dead`` and only then removes them.
        Nothing aborts the loop, so one connection cannot cost another its frame - which is what
        Requirement 19.11's "continue delivering events to all other connections without dropping or
        reordering their events" needs. This version adds three things per connection and changes
        nothing about that structure:

        1. the pre-emit owner comparison (Requirement 21.7),
        2. the pending-depth check (Requirement 19.11),
        3. per-handler fault isolation (Requirement 19.13).

        ORDER is preserved for the survivors in the only sense that means anything: this coroutine
        writes one frame to each live connection before it returns, and the caller emits frames in
        ascending ``sequence``, so no connection can receive sequence n+1 before sequence n.

        The owner is read ONCE per broadcast rather than once per subscription. Same session, same
        row, same instant - a second read could only differ by being staler.
        """
        sid = _require_session_id(session_id)
        subscribers = [s for s in self._by_session.get(sid, ()) if not s.closed]
        if not subscribers:
            return BroadcastOutcome()

        # Requirement 26.6's per-WebSocket-event-type figures. `perf_counter` rather than
        # `time.time`: this is an interval, and a wall-clock correction mid-fan-out must not turn
        # into a negative duration.
        _measured_from = time.perf_counter()
        deepest_queue = 0

        owner = self.session_owner(supabase, sid, now=now)
        text = json.dumps(dict(frame))
        event_type = str(frame.get("type") or "")

        delivered: List[PaperSubscription] = []
        fell_behind: List[PaperSubscription] = []
        revoked: List[PaperSubscription] = []
        failed: List[PaperSubscription] = []
        handler_failures: List[Tuple[PaperSubscription, str]] = []

        for subscription in subscribers:
            # (1) Requirement 21.7. Nothing further is emitted on this subscription - not the frame
            # and not an error frame, because an error frame WOULD be something further - and it is
            # closed. An unresolvable owner takes the same branch, deliberately: fail closed.
            if owner is None or subscription.identity != owner:
                logger.warning(
                    "[PaperChannel] the recorded identity of a subscription to %s no longer owns "
                    "the session; emitting nothing further and closing it",
                    subscription.channel,
                )
                await self._release(
                    subscription,
                    reason="ownership re-verification failed",
                    close_code=CLOSE_CODE_OWNERSHIP,
                )
                revoked.append(subscription)
                continue

            # (2) Requirement 19.11. Counted before the send, which is the only order in which an
            # undrained queue is visible.
            depth = self.manager.note_pending(subscription.connection)
            try:
                deepest_queue = max(deepest_queue, int(depth))
            except (TypeError, ValueError):
                # A registry double that returned something unreadable. The depth is not
                # recorded; the delivery below is unaffected, which is the property that matters.
                pass
            if self.manager.has_fallen_behind(subscription.connection):
                await self._close_fell_behind(subscription)
                fell_behind.append(subscription)
                continue

            try:
                await self._send(subscription, text)
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "[PaperChannel] %s could not be written to (%s); releasing it",
                    subscription.channel,
                    exc,
                )
                self.manager.note_delivered(subscription.connection)
                await self._release(
                    subscription, reason="the connection could not be written to", close_code=None
                )
                failed.append(subscription)
                continue

            self.manager.note_delivered(subscription.connection)
            delivered.append(subscription)

            # (3) Requirement 19.13, per handler.
            for name in self._deliver_to_handlers(subscription, frame, event_type):
                handler_failures.append((subscription, name))

        # Requirement 26.6: `paper.ws.{event_type}.emitted`, `.latency_ms`, `.errors`,
        # `paper.ws.queue_depth` and `paper.ws.slow_consumer_disconnects`, recorded from what
        # this fan-out MEASURED rather than from what it intended (Requirement 27.6). The
        # emitted count is `delivered`, not `len(subscribers)`: a frame counted for a connection
        # that was closed for falling behind would disagree with what any client received.
        _record_fanout(
            event_type,
            delivered=len(delivered),
            duration_ms=(time.perf_counter() - _measured_from) * 1000.0,
            queue_depth=deepest_queue,
            fell_behind=len(fell_behind),
            revoked=len(revoked),
            failed=len(failed),
            handler_failures=len(handler_failures),
        )

        return BroadcastOutcome(
            delivered=tuple(delivered),
            fell_behind=tuple(fell_behind),
            ownership_revoked=tuple(revoked),
            failed=tuple(failed),
            handler_failures=tuple(handler_failures),
        )

    async def _send(self, subscription: PaperSubscription, text: str) -> None:
        """Write one serialised frame to one connection.

        ``send_text`` is what ``ws_manager._broadcast`` uses and what every other channel on this
        platform is delivered through, so a Paper_Channel frame is written the same way. A
        connection with no ``send_text`` is a handler-only subscription and is not an error: its
        frames arrive through :meth:`_deliver_to_handlers`.
        """
        send = getattr(subscription.connection, "send_text", None)
        if send is None:
            return
        result = send(text)
        if hasattr(result, "__await__"):
            await result

    async def _close_fell_behind(self, subscription: PaperSubscription) -> None:
        """Requirement 19.11: tell the client it fell behind, then remove it.

        The ``paper_error`` is attempted BEFORE the release and its failure is ignored: a consumer
        that is 1001 events behind may well not read this either, and the close is the part that
        must happen regardless. This frame is the one exception to "a closed subscription receives
        nothing further" and the requirement asks for it by name - the client needs a code to
        branch on so it reconnects and resumes rather than treating the close as a fault.
        """
        logger.warning(
            "[PaperChannel] %s has more than %d events pending; closing it with %s",
            subscription.channel,
            MAX_PENDING_EVENTS,
            ERROR_CLIENT_FELL_BEHIND,
        )
        try:
            await self._send(
                subscription,
                json.dumps(
                    error_frame(
                        subscription.session_id,
                        code=ERROR_CLIENT_FELL_BEHIND,
                        message=FELL_BEHIND_MESSAGE,
                        recoverable=True,
                        sequence=1,
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[PaperChannel] %s could not be told it fell behind (%s); closing it anyway",
                subscription.channel,
                exc,
            )
        await self._release(
            subscription,
            reason=ERROR_CLIENT_FELL_BEHIND,
            close_code=CLOSE_CODE_FELL_BEHIND,
        )

    def _deliver_to_handlers(
        self,
        subscription: PaperSubscription,
        frame: Mapping[str, Any],
        event_type: str,
    ) -> List[str]:
        """Run every handler, catching each one separately. Returns the names that raised.

        Requirement 19.13, in three parts and all three of them here:

        * the failure is LOGGED with the session identifier and the event type,
        * the remaining handlers still run, and the next event still reaches this connection,
        * the connection stays registered with its handlers - the raising one is **not** removed,
          so a subscription cannot be whittled down to no handler by a handler that misbehaves,
          which is what "SHALL NOT leave the connection registered without a handler" forbids.

        A handler returning an awaitable is not awaited here: this is a synchronous fan-out inside
        the delivery loop, and awaiting an unknown coroutine would let one subscriber's handler
        stall every other subscriber's frame. Such a handler is refused loudly rather than
        silently dropped.
        """
        failures: List[str] = []
        for handler in list(subscription.handlers):
            name = getattr(handler, "__name__", repr(handler))
            try:
                result = handler(frame)
                if hasattr(result, "__await__"):
                    # Closed before the refusal is raised, so the un-awaited coroutine does not
                    # surface later as a RuntimeWarning attributed to whatever ran next.
                    closer = getattr(result, "close", None)
                    if closer is not None:
                        closer()
                    raise TypeError(
                        "a Paper_Channel handler must be synchronous; an awaited handler would "
                        "let one subscriber stall the delivery loop"
                    )
            except Exception as exc:  # noqa: BLE001 - the whole point of Requirement 19.13
                failures.append(name)
                logger.error(
                    "[PaperChannel] handler %s raised for session %s event %s (%s); the "
                    "connection stays registered with its %d handler(s)",
                    name,
                    subscription.session_id,
                    event_type,
                    exc,
                    len(subscription.handlers),
                )
        return failures


# ══════════════════════════════════════════════════════════════════════════
# INSTRUMENTATION (Requirements 19.11, 26.6, 27.6)
# ══════════════════════════════════════════════════════════════════════════

#: Why one delivery did not reach its subscriber. Stable strings, because they are metric label
#: values and a runbook matches on a code rather than on prose. Each one is a DIFFERENT operator
#: action: a revoked ownership is an authorisation event, a slow consumer is a capacity problem, a
#: failed write is a transport problem, and a raising handler is a bug in this process.
WS_ERROR_OWNERSHIP_REVOKED = "OWNERSHIP_REVOKED"
WS_ERROR_FELL_BEHIND = "CLIENT_FELL_BEHIND"
WS_ERROR_WRITE_FAILED = "WRITE_FAILED"
WS_ERROR_HANDLER_FAILED = "HANDLER_FAILED"


def _record_fanout(
    event_type: str,
    *,
    delivered: int,
    duration_ms: float,
    queue_depth: int,
    fell_behind: int,
    revoked: int,
    failed: int,
    handler_failures: int,
) -> None:
    """One :meth:`PaperChannelRegistry.broadcast`'s measurements, onto the one collector.

    ``guarded_collector()`` returns ``None`` when the collector is unavailable, and this returns
    without recording - instrumentation is never what drops a frame, so every subscriber is
    still written to.
    """
    collector = guarded_collector()
    if collector is None:
        return
    collector.record_paper_ws_fanout(
        event_type,
        delivered=delivered,
        duration_ms=duration_ms,
        queue_depth=queue_depth,
    )
    if revoked:
        collector.record_paper_ws_error(event_type, WS_ERROR_OWNERSHIP_REVOKED, revoked)
    if fell_behind:
        collector.record_paper_ws_error(event_type, WS_ERROR_FELL_BEHIND, fell_behind)
        collector.record_paper_ws_slow_consumer_disconnect(fell_behind)
    if failed:
        collector.record_paper_ws_error(event_type, WS_ERROR_WRITE_FAILED, failed)
    if handler_failures:
        collector.record_paper_ws_error(
            event_type, WS_ERROR_HANDLER_FAILED, handler_failures
        )


# ══════════════════════════════════════════════════════════════════════════
# THE PROCESS REGISTRY, AND THE MODULE-LEVEL FORMS
# ══════════════════════════════════════════════════════════════════════════

#: The registry the running process uses. One, for the reason ``ws_manager.manager`` is one: two
#: registries would deliver a session's events twice or not at all depending on which one the
#: emitter reached.
REGISTRY = PaperChannelRegistry()


async def subscribe(
    session_id: Any,
    connection: Any,
    *,
    user: Any,
    handlers: Sequence[Callable[[Mapping[str, Any]], Any]] = (),
    message: Optional[Mapping[str, Any]] = None,
) -> PaperSubscription:
    """:meth:`PaperChannelRegistry.subscribe` on :data:`REGISTRY`."""
    return await REGISTRY.subscribe(
        session_id, connection, user=user, handlers=handlers, message=message
    )


async def unsubscribe(
    subscription: PaperSubscription, *, reason: str = "unsubscribed"
) -> None:
    """:meth:`PaperChannelRegistry.unsubscribe` on :data:`REGISTRY`."""
    await REGISTRY.unsubscribe(subscription, reason=reason)


async def release_session(
    session_id: Any, *, reason: str = "session released"
) -> Tuple[PaperSubscription, ...]:
    """:meth:`PaperChannelRegistry.release_session` on :data:`REGISTRY`. Requirement 19.12."""
    return await REGISTRY.release_session(session_id, reason=reason)


def replay(
    supabase: Any, *, session_id: Any, user: Any, last_sequence: Any = 0
) -> ReplayOutcome:
    """:meth:`PaperChannelRegistry.replay` on :data:`REGISTRY`."""
    return REGISTRY.replay(
        supabase, session_id=session_id, user=user, last_sequence=last_sequence
    )


async def broadcast(
    session_id: Any,
    frame: Mapping[str, Any],
    *,
    supabase: Any,
    now: Optional[float] = None,
) -> BroadcastOutcome:
    """:meth:`PaperChannelRegistry.broadcast` on :data:`REGISTRY`.

    ``design.md`` names this ``paper_events.broadcast(session_id, frame)``, and
    ``paper_events.broadcast`` is a one-line delegation to this function - the name the design uses
    resolves, and the delivery path is in one place.
    """
    return await REGISTRY.broadcast(session_id, frame, supabase=supabase, now=now)


__all__: List[str] = [
    "BroadcastOutcome",
    "CLOSE_CODE_FELL_BEHIND",
    "CLOSE_CODE_OWNERSHIP",
    "CLOSE_CODE_SESSION_RELEASED",
    "ERROR_CLIENT_FELL_BEHIND",
    "ERROR_HISTORY_INCOMPLETE",
    "FELL_BEHIND_MESSAGE",
    "HISTORY_INCOMPLETE_MESSAGE",
    "MAX_PENDING_EVENTS",
    "OWNER_CACHE_SECONDS",
    "PAPER_STORE",
    "PaperChannelRegistry",
    "PaperSubscription",
    "REGISTRY",
    "REPLAY_ROW_CAP",
    "ReplayOutcome",
    "WS_ERROR_FELL_BEHIND",
    "WS_ERROR_HANDLER_FAILED",
    "WS_ERROR_OWNERSHIP_REVOKED",
    "WS_ERROR_WRITE_FAILED",
    "authenticated_identity",
    "broadcast",
    "cached_session_owner",
    "error_frame",
    "frame_from_row",
    "install_session_write_invalidation",
    "invalidate_session_owner",
    "release_session",
    "replay",
    "subscribe",
    "unsubscribe",
]
