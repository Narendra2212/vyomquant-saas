"""
WebSocket Channel Constants

Single source of truth for WebSocket event channels.
Aligned with frontend wsChannels.js
"""

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional, Set, Tuple

# The ONE backend import at module scope in this file, and the reason it is here rather than
# duplicated: see "THE SEVENTH PARAMETERISED CHANNEL — `paper.{session_id}`" below. Requirement
# 19.2's sixteen event types are also ``paper_events.event_type``'s permitted values, so the
# vocabulary lives with the payload models and the migration CHECK that already enumerate it.
# ``paper_events`` imports nothing from this module at module scope, so this direction is acyclic.
from backend_app.backend.paper.paper_events import PAPER_CHANNEL_EVENTS


class ChannelType(str, Enum):
    """
    WebSocket streaming channels.
    
    These MUST match the frontend WS_CHANNELS exactly.
    """
    BOT_STATUS = "bot_status"
    SIGNAL_TRACE = "signal_trace"
    EXECUTION_EVENTS = "execution_events"
    RISK_EVENTS = "risk_events"
    DEPLOYMENT_EVENTS = "deployment_events"
    INFRASTRUCTURE = "infrastructure"


# Valid channel set for runtime validation
VALID_CHANNELS: Set[str] = {ch.value for ch in ChannelType}


def is_valid_channel(channel: str) -> bool:
    """
    Validate a channel name.
    
    Args:
        channel: Channel to validate
        
    Returns:
        True if valid
    """
    return channel in VALID_CHANNELS


def assert_valid_channel(channel: str, context: str = "subscription") -> None:
    """
    Assert channel is valid, raise if not.
    
    Args:
        channel: Channel to validate
        context: Context for error message
        
    Raises:
        ValueError: If channel is invalid
    """
    if not is_valid_channel(channel):
        valid_list = ", ".join(sorted(VALID_CHANNELS))
        raise ValueError(
            f"Invalid WebSocket channel {context}: '{channel}'. "
            f"Valid channels: {valid_list}"
        )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  PARAMETERISED CHANNELS â€” `training.{job_id}`
#
#  strategy-builder task 6.7 / `design.md` Â§ WebSocket / realtime.
#  Requirements 15.11, 21.5, 21.6, 23.1.
#
#  WHY THIS IS NOT A ``ChannelType`` MEMBER
#  ----------------------------------------
#  ``ChannelType`` is a closed vocabulary of FIXED channel names, and two things
#  depend on it being exactly that: ``VALID_CHANNELS`` is derived from it, and
#  ``ws_event_stream.py`` calls ``ChannelType(channel_name)`` immediately after
#  ``is_valid_channel(channel_name)`` returns True. A parameterised channel can
#  never be an enum member (there is one per job), so admitting
#  ``training.{job_id}`` through ``is_valid_channel`` would hand
#  ``ws_event_stream`` a name its very next line cannot construct.
#
#  So the parameterised family gets its own predicate and its own parser, and
#  ``is_valid_channel`` / ``VALID_CHANNELS`` / ``ChannelType`` are left exactly as
#  they were. Nothing that validated before validates differently now.
#
#  AUTHORISATION IS NOT HERE
#  -------------------------
#  This module answers "is this a well-formed channel name". It deliberately does
#  NOT answer "may this user subscribe to it" â€” that is
#  ``core/websocket_auth.authorize_channel_subscription``, which resolves
#  ``job.user_id``. A well-formed name is not an authorised one.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

#: The family name of the per-job training channel.
TRAINING_CHANNEL_NAMESPACE = "training"

#: The prefix of the per-job training channel. One constant, so the producer, the
#: subscriber and the parser cannot disagree about the separator.
TRAINING_CHANNEL_PREFIX = f"{TRAINING_CHANNEL_NAMESPACE}."


#: What a job identifier is allowed to look like inside a channel name.
#:
#: ``training_jobs.id`` is a PostgreSQL ``UUID`` (migration
#: ``004d_training_and_models.sql``), so a stricter UUID-only rule was considered
#: and rejected: the identifier arrives from a client as an opaque string, this
#: predicate is a *shape* check ahead of the ownership check, and a UUID-only rule
#: would make every non-production identifier unroutable while adding no security
#: (the ownership lookup, not the regex, is what refuses another user's job).
#:
#: What it must exclude, and does: an empty segment, a further ``.`` (so
#: ``training.a.b`` cannot smuggle a second segment past the parser), ``*`` and
#: other wildcards, whitespace, ``/`` and ``\`` and ``%``, and anything long
#: enough to be a payload rather than an identifier.
_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The name task 6.7 gave the same pattern, kept so nothing that referred to it breaks.
#: Task 8.5 widened its *use*, not its shape: a strategy id and a deployment id are the
#: same kind of opaque identifier as a job id (all three are ``UUID`` columns), and one
#: shape rule for all five families is what keeps the four new channels from admitting a
#: name the training channel refuses.
_JOB_ID_PATTERN = _RESOURCE_ID_PATTERN


class TrainingEvent(str, Enum):
    """The frames ``training.{job_id}`` carries.

    The first five are ``design.md``'s WebSocket table verbatim: ``queued``,
    ``progress``, ``completed``, ``failed``, ``cancelled`` â€” and they are exactly
    Requirement 15.2's status vocabulary minus ``RUNNING``, plus the per-epoch
    progress frame Requirement 15.11 asks for.

    ``CANCEL_REQUESTED`` is the sixth and is not in that table. It is included
    because it already exists: task 6.3's cancel endpoint emits it when it sets
    ``cancel_requested``, and that is a genuinely different fact from
    ``cancelled`` â€” the flag is set, the worker has not stopped yet, and the job
    is still ``RUNNING``. Folding it into ``cancelled`` would have the channel
    announce a stop that has not happened.
    """

    QUEUED = "training.queued"
    PROGRESS = "training.progress"
    COMPLETED = "training.completed"
    FAILED = "training.failed"
    CANCELLED = "training.cancelled"
    CANCEL_REQUESTED = "training.cancel_requested"


#: The five the design's table names, for a caller that wants to assert on them
#: without the acknowledgement frame.
TRAINING_LIFECYCLE_EVENTS: Set[str] = {
    TrainingEvent.QUEUED.value,
    TrainingEvent.PROGRESS.value,
    TrainingEvent.COMPLETED.value,
    TrainingEvent.FAILED.value,
    TrainingEvent.CANCELLED.value,
}

#: Every frame type the channel may carry.
TRAINING_EVENTS: Set[str] = {event.value for event in TrainingEvent}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  THE OTHER FOUR PARAMETERISED CHANNELS
#
#  strategy-builder task 8.5 / `design.md` Â§ WebSocket / realtime.
#  Requirements 20.12, 21.5, 21.6, 23.1-23.6.
#
#  `design.md`'s table names six channels. `market.{symbol}.{timeframe}` and
#  `training.{job_id}` already exist; this section adds the remaining four:
#  ``builder.validation.{strategy_id}``, ``strategy.{strategy_id}``,
#  ``deployment.{deployment_id}`` and ``execution.{deployment_id}``.
#
#  WHY ONE FAMILY DESCRIPTOR AND NOT FOUR COPIES OF TASK 6.7
#  ---------------------------------------------------------
#  Task 6.7 wrote a prefix constant, a builder, a parser, a namespace predicate and
#  an event-membership predicate for ONE channel. Transcribing that five times over
#  would be five separate places for the separator, the identifier shape and the
#  "claims the namespace but names nothing" rule to drift apart â€” and the one that
#  drifted would be the one a subscriber could smuggle a name past. So the shape rule
#  is stated once (:data:`_RESOURCE_ID_PATTERN`), the family is DATA
#  (:class:`OwnedChannelFamily`), and :func:`parse_owned_channel` /
#  :func:`owned_channel` are the only parser and the only builder. Task 6.7's five
#  training functions are kept as the wrappers they now are, so every existing caller
#  and every existing test keeps the names it used and the answers it got.
#
#  AUTHORISATION IS STILL NOT HERE
#  -------------------------------
#  This module answers "is this a well-formed channel name" and "which resource does
#  it name". It deliberately does NOT know which table owns that resource: that is
#  ``core/websocket_auth``, which holds the family-to-owner map and imports the table
#  and migration names from the services that own them. A family descriptor carrying
#  a table name would be a second place to disagree about which relation records
#  ownership â€” exactly the mistake this section exists to avoid making five times.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class ValidationEvent(str, Enum):
    """The frames ``builder.validation.{strategy_id}`` carries.

    ``design.md``'s payload for this channel is "``ValidationReport`` after async
    validation", so :attr:`REPORT` is the channel's reason to exist.
    :attr:`FAILED` is the honest second frame: a validation that could not be run is
    not a validation that found no issues, and a client that only ever heard
    ``validation.report`` would render the last report as though it were current.
    """

    REPORT = "validation.report"
    FAILED = "validation.failed"


class StrategyEvent(str, Enum):
    """The frames ``strategy.{strategy_id}`` carries.

    :attr:`LIFECYCLE` is ``design.md``'s "lifecycle transitions".
    :attr:`CANVAS_STATE` carries task 8.3's ``strategy_lifecycle.canvas_state``
    verbatim â€” the deployed lock Requirement 9.9 asks the canvas to render. It is a
    separate frame type from :attr:`LIFECYCLE` because it is a separate fact: a
    lifecycle word is what the version *is*, and ``canvas_state`` is the backend's
    decision about what the author may *do*, which the frontend must not re-derive
    from the word.
    """

    LIFECYCLE = "strategy.lifecycle"
    CANVAS_STATE = "strategy.canvas_state"


class DeploymentEvent(str, Enum):
    """The frames ``deployment.{deployment_id}`` carries.

    :attr:`STATE` is ``design.md``'s ``DEPLOYING -> RUNNING -> PAUSED -> STOPPED``
    and :attr:`GUARD_TRIP` its "guard trips".

    :attr:`RUNTIME_STATE` is the frame Requirement 20.12 needs and it belongs here
    rather than on ``strategy.{strategy_id}``: a node is ``WARMING`` or ``READY``
    *for a running deployment over a window of bars*, not in the abstract, and the
    same version deployed twice can hold two different runtime states at once.
    """

    STATE = "deployment.state"
    GUARD_TRIP = "deployment.guard_trip"
    RUNTIME_STATE = "deployment.runtime_state"


class ExecutionEvent(str, Enum):
    """The frames ``execution.{deployment_id}`` carries â€” ``design.md``'s
    "intents, orders, fills, rejections", one frame type each.

    A rejection is its own frame and not an order with a status, because a
    Trade_Intent the execution guard or the risk engine refused never became an
    order at all (Requirements 20.5, 20.6) and reporting it as one would put an
    order on screen that no venue ever saw.
    """

    INTENT = "execution.intent"
    ORDER = "execution.order"
    FILL = "execution.fill"
    REJECTION = "execution.rejection"


# ═══════════════════════════════════════════════════════════════════════════
#  THE SIXTH PARAMETERISED CHANNEL — `signal.{deployment_id}`
#
#  trading-lifecycle-integration task 14.1 / `design.md` § WebSocket design → New
#  Owned_Channel family. Requirements 18.2, 23.1, 23.2.
#
#  WHY A SIXTH FAMILY AND NOT A SIXTH `ExecutionEvent`
#  --------------------------------------------------
#  ``execution.{deployment_id}`` carries what happened at the venue — an intent, an
#  order, a fill, a rejection. The Signal_Trace_Page's subject is the SIGNAL: a row
#  that exists from the moment the strategy decided, before any venue was contacted,
#  and that still exists when no order was ever placed. Folding these frames into the
#  execution vocabulary would make a client that wants the decision history subscribe
#  to a fill-by-fill stream, which is exactly the reason task 8.5 gave for splitting
#  ``execution`` off ``deployment`` in the first place.
#
#  WHY THE SAME RESOURCE AS `EXECUTION_FAMILY`
#  -------------------------------------------
#  ``deployment_id``, deliberately, and not ``signal_id`` or ``strategy_id``: a
#  deployment resolves unambiguously to one user (``strategy_deployments.user_id``)
#  and ``core/websocket_auth`` ALREADY resolves ownership of a ``deployment_id`` for
#  ``DEPLOYMENT_FAMILY`` and ``EXECUTION_FAMILY``. Reusing that identity means this
#  family adds NO owner-resolution path — it maps onto the existing one, which is
#  the whole point: a new lookup would be a new place for "a failed lookup denies"
#  to be got wrong, and Requirement 23.2's "a refusal must not reveal existence"
#  holds here for free because it is the same refusal.
#
#  A ``signal_id``-scoped channel was considered and rejected: the page follows a
#  deployment's whole signal stream, and one channel per signal would mean
#  subscribing to a resource that does not exist yet.
#
#  AUTHORISATION IS STILL NOT HERE
#  -------------------------------
#  Same as every family above: this module says whether a name is well formed.
#  ``core/websocket_auth.authorize_channel_subscription`` says whether the user may
#  have it, through the ``strategy_deployments`` lookup it already performs.
# ═══════════════════════════════════════════════════════════════════════════


class SignalEvent(str, Enum):
    """The frames ``signal.{deployment_id}`` carries.

    ``design.md``'s three, and no fourth:

    * :attr:`GENERATED` — a Signal row now exists. The payload is
      ``signal_service.Signal.to_public_dict()`` (task 10.1), so the frame and the
      signal-trace REST response are the same shape and the page does not have to
      reconcile two.
    * :attr:`STATUS_CHANGED` — that signal's ``order_lifecycle_state`` moved. A
      separate frame type from :attr:`GENERATED` because it is a separate fact: a
      client that heard only "generated" would render a decision as though its order
      were still pending, and Requirement 18.4's dedup key
      (``f"{signal_id}:{order_lifecycle_state}"``) is only meaningful if a transition
      is announced as a transition.
    * :attr:`SNAPSHOT` — current state, sent on (re)subscribe. Requirement 18.6 asks
      a reconnecting page to request a snapshot rather than assume it missed nothing,
      and a replayed snapshot is not a new event: naming it distinctly is what lets
      the page's reducer treat it as a resync rather than as history repeating.

    There is deliberately no ``signal.failed``/``signal.rejected`` frame. A failure
    IS an ``order_lifecycle_state`` (``FAILED``, ``REJECTED``, ``CANCELLED``), so it
    travels as :attr:`STATUS_CHANGED` carrying that state. A second way to say the
    same thing would be a second vocabulary for the page to keep in agreement.
    """

    GENERATED = "signal.generated"
    STATUS_CHANGED = "signal.status_changed"
    SNAPSHOT = "signal.snapshot"


#: The four new vocabularies. Spelled ``*_CHANNEL_EVENTS`` rather than ``*_EVENTS``
#: because ``ChannelType`` already has members literally named ``DEPLOYMENT_EVENTS``
#: and ``EXECUTION_EVENTS`` â€” two *fixed* channels whose vocabulary is
#: :data:`CHANNEL_EVENTS`. A module-level ``DEPLOYMENT_EVENTS`` set beside
#: ``ChannelType.DEPLOYMENT_EVENTS`` would be a reader's trap for no gain.
VALIDATION_CHANNEL_EVENTS: Set[str] = {event.value for event in ValidationEvent}
STRATEGY_CHANNEL_EVENTS: Set[str] = {event.value for event in StrategyEvent}
DEPLOYMENT_CHANNEL_EVENTS: Set[str] = {event.value for event in DeploymentEvent}
EXECUTION_CHANNEL_EVENTS: Set[str] = {event.value for event in ExecutionEvent}

#: trading-lifecycle-integration task 14.1's vocabulary, spelled the same way for the
#: same reason: ``ChannelType.SIGNAL_TRACE`` is a *fixed* channel and a module-level
#: ``SIGNAL_EVENTS`` beside it would be a reader's trap.
SIGNAL_CHANNEL_EVENTS: Set[str] = {event.value for event in SignalEvent}


@dataclass(frozen=True)
class OwnedChannelFamily:
    """One parameterised channel family: ``{namespace}.{resource_id}``.

    "Owned" is the load-bearing word. Every family here names exactly one resource
    that exactly one user owns, which is what makes Requirement 21.5 answerable at
    all â€” a channel that named no resource could not be authorised against an owner.

    ``resource`` is the noun the payload key uses (``job_id``, ``strategy_id``,
    ``deployment_id``), so a frame states which resource it is about in the field name
    a client already reads rather than in a position a client has to infer.
    """

    namespace: str
    resource: str
    events: FrozenSet[str]

    @property
    def prefix(self) -> str:
        """``"{namespace}."`` â€” the one place the separator is spelled."""
        return f"{self.namespace}."

    def channel(self, resource_id: Any) -> str:
        """This family's channel name for ``resource_id``.

        Raises ``ValueError`` for an identifier this family would never route, rather
        than returning a name no subscriber could have subscribed to.
        """
        text = "" if resource_id is None else str(resource_id)
        if not _RESOURCE_ID_PATTERN.match(text):
            raise ValueError(
                f"Invalid {self.resource} for a channel name: {text!r}. "
                f"Expected 1-64 characters of [A-Za-z0-9_-]."
            )
        return f"{self.prefix}{text}"

    def parse(self, channel: Any) -> Optional[str]:
        """The resource id in ``channel``, or ``None`` if this family does not route it."""
        if not isinstance(channel, str):
            return None
        if not channel.startswith(self.prefix):
            return None
        resource_id = channel[len(self.prefix):]
        if not _RESOURCE_ID_PATTERN.match(resource_id):
            return None
        return resource_id

    def claims(self, channel: Any) -> bool:
        """True when ``channel`` is in this family, well-formed or not."""
        return isinstance(channel, str) and (
            channel == self.namespace or channel.startswith(self.prefix)
        )

    def is_event(self, event: Any) -> bool:
        """True when ``event`` is a frame type this family carries."""
        return isinstance(event, str) and event in self.events


#: ``training.{job_id}`` â€” task 6.7's channel, now expressed as a family. Its
#: namespace, prefix, identifier shape and event set are unchanged.
TRAINING_FAMILY = OwnedChannelFamily(
    namespace=TRAINING_CHANNEL_NAMESPACE,
    resource="job_id",
    events=frozenset(TRAINING_EVENTS),
)

#: ``builder.validation.{strategy_id}``. The namespace carries a dot of its own,
#: which the parser handles for free: the separator is the LAST dot of the prefix and
#: the identifier pattern forbids a further one, so ``builder.validation.a.b`` is
#: refused rather than read as strategy ``a``.
BUILDER_VALIDATION_FAMILY = OwnedChannelFamily(
    namespace="builder.validation",
    resource="strategy_id",
    events=frozenset(VALIDATION_CHANNEL_EVENTS),
)

#: ``strategy.{strategy_id}``.
STRATEGY_FAMILY = OwnedChannelFamily(
    namespace="strategy",
    resource="strategy_id",
    events=frozenset(STRATEGY_CHANNEL_EVENTS),
)

#: ``deployment.{deployment_id}``.
DEPLOYMENT_FAMILY = OwnedChannelFamily(
    namespace="deployment",
    resource="deployment_id",
    events=frozenset(DEPLOYMENT_CHANNEL_EVENTS),
)

#: ``execution.{deployment_id}``. The same resource as
#: :data:`DEPLOYMENT_FAMILY` and therefore the same owner â€” two channels because a
#: client that wants lifecycle without a fill-by-fill stream should be able to say so
#: (Requirement 23.2 is about unsubscribing what a view subscribed).
EXECUTION_FAMILY = OwnedChannelFamily(
    namespace="execution",
    resource="deployment_id",
    events=frozenset(EXECUTION_CHANNEL_EVENTS),
)

# ═══════════════════════════════════════════════════════════════════════════
#  THE SEVENTH PARAMETERISED CHANNEL — `paper.{session_id}`
#
#  marketplace-subscriptions-paper-trading task 26.3 / `design.md` § "`paper/paper_events.py`
#  and the Paper_Channel". Requirements 19.1, 19.2, 19.4, 19.5, 21.4, 21.6.
#
#  WHY THE VOCABULARY IS IMPORTED AND NOT DECLARED HERE
#  ---------------------------------------------------
#  Every family above declares its own ``*Event`` enum in this module, and this one deliberately
#  does not. Requirement 19.2's sixteen event types are ALSO the permitted values of
#  ``paper_events.event_type`` — ``chk_paper_event_type`` in
#  ``backend_app/migrations/009_paper_trading.sql`` enumerates them, and 009's own comment says
#  they "MUST equal the type set backend_app/backend/paper/paper_events.py emits". A seventeenth
#  member declared here would be a ``23514`` at insert time rather than a routing bug, so the
#  enum lives with the payload models that validate against it and this module imports the
#  derived set. One definition, three importers (this module, ``paper_repository``,
#  ``paper_market_feed``); ``tests/test_paper_event_schemas.py`` asserts set equality with the
#  migration.
#
#  ``paper_events`` imports nothing from this module at module scope — it reaches
#  :data:`PAPER_FAMILY` through a function-local import inside ``paper_channel()`` — so this
#  import direction carries no cycle.
#
#  AUTHORISATION IS STILL NOT HERE
#  -------------------------------
#  Same as the six families above. ``core/websocket_auth`` gains ONE ``_OwnerRelation`` entry
#  naming ``paper_sessions``; ``authorize_channel_subscription`` already parses through
#  :func:`parse_owned_channel`, resolves ``user_id`` and refuses through the shared
#  ``_forbidden`` / ``_unresolved`` frames — which is why another user's session and a
#  nonexistent one are already indistinguishable (Requirements 19.4, 21.4). No authorisation
#  code is written for this family.
# ═══════════════════════════════════════════════════════════════════════════

#: ``signal.{deployment_id}`` — trading-lifecycle-integration task 14.1. The third
#: family keyed on ``deployment_id`` and therefore the third to resolve through
#: ``strategy_deployments.user_id``, which is the point: ``core/websocket_auth``
#: gains a map entry, not a lookup.
#:
#: The namespace is ``"signal"``, singular, and does NOT collide with the legacy fixed
#: channel ``ChannelType.SIGNAL_TRACE`` (``"signal_trace"``): ``claims`` matches only
#: the exact namespace or the ``"signal."`` prefix, and ``"signal_trace"`` is neither.
#: The legacy broadcast keeps exactly the controls it has (Requirement 23.7 retires
#: it for this traffic separately, in task 14.3 — not here).
SIGNAL_FAMILY = OwnedChannelFamily(
    namespace="signal",
    resource="deployment_id",
    events=frozenset(SIGNAL_CHANNEL_EVENTS),
)

#: ``paper.{session_id}`` — marketplace-subscriptions-paper-trading task 26.3, and Requirement
#: 19.1's "parameterised, ownership-authorised channel family following the existing
#: ``OwnedChannelFamily`` pattern ... keyed by Paper_Session identifier ... registered in the
#: existing WebSocket infrastructure rather than in a new server".
#:
#: Follows :data:`SIGNAL_FAMILY` exactly: one descriptor, one entry in
#: :data:`OWNED_CHANNEL_FAMILIES`, one ``_OwnerRelation`` in ``core/websocket_auth``. The resource
#: is ``session_id`` and it resolves through ``paper_sessions.user_id``, which is the FIRST family
#: to read that relation — every other family here resolves through ``strategies`` or
#: ``strategy_deployments``. That is one map entry, not a new resolver: the lookup, the six refusal
#: branches and the indistinguishable ``_forbidden`` frame are the shared ones, so a paper session
#: belonging to another tenant and a paper session that does not exist are the same answer for the
#: same reason they already are on the other six (Requirements 19.4, 21.4).
#:
#: The vocabulary is :data:`PAPER_CHANNEL_EVENTS`, imported from
#: ``backend_app.backend.paper.paper_events`` rather than declared here — the only family whose
#: event set is also a database CHECK constraint's value list.
PAPER_FAMILY = OwnedChannelFamily(
    namespace="paper",
    resource="session_id",
    events=frozenset(PAPER_CHANNEL_EVENTS),
)

#: Every parameterised, owner-authorised family, longest namespace first.
#:
#: The order is not cosmetic. ``builder.validation`` must be tried before any shorter
#: namespace that could prefix it, so a longest-first sweep is the rule rather than a
#: property of how this tuple happens to be typed. ``market.{symbol}.{timeframe}`` is
#: deliberately absent: it names no owned resource (a symbol belongs to nobody) and
#: keeps exactly the controls it already had.
OWNED_CHANNEL_FAMILIES: Tuple[OwnedChannelFamily, ...] = tuple(
    sorted(
        (
            TRAINING_FAMILY,
            BUILDER_VALIDATION_FAMILY,
            STRATEGY_FAMILY,
            DEPLOYMENT_FAMILY,
            EXECUTION_FAMILY,
            SIGNAL_FAMILY,
            PAPER_FAMILY,
        ),
        key=lambda family: len(family.namespace),
        reverse=True,
    )
)

#: ``namespace -> family``, for a caller that has the namespace already.
OWNED_CHANNEL_FAMILIES_BY_NAMESPACE: Dict[str, OwnedChannelFamily] = {
    family.namespace: family for family in OWNED_CHANNEL_FAMILIES
}

#: Every frame type any owned channel may carry.
OWNED_CHANNEL_EVENTS: Set[str] = set().union(
    *(set(family.events) for family in OWNED_CHANNEL_FAMILIES)
)


@dataclass(frozen=True)
class OwnedChannelRef:
    """A well-formed owned channel, decomposed: which family, which resource."""

    family: OwnedChannelFamily
    resource_id: str

    @property
    def channel(self) -> str:
        return self.family.channel(self.resource_id)

    @property
    def resource(self) -> str:
        return self.family.resource


def owned_channel(family: OwnedChannelFamily, resource_id: Any) -> str:
    """``family``'s channel name for ``resource_id``."""
    return family.channel(resource_id)


def parse_owned_channel(channel: Any) -> Optional[OwnedChannelRef]:
    """Decompose ``channel`` into its family and resource id, or ``None``.

    ``None`` means "no family routes this name" â€” which covers both a channel from
    outside these families and a malformed name inside one. A caller that needs to
    tell those apart uses :func:`claims_owned_namespace`, exactly as task 6.7's pair
    of predicates did for the training family alone.
    """
    for family in OWNED_CHANNEL_FAMILIES:
        resource_id = family.parse(channel)
        if resource_id is not None:
            return OwnedChannelRef(family, resource_id)
    return None


def claims_owned_namespace(channel: Any) -> bool:
    """True when ``channel`` is in one of these families, well-formed or not."""
    return any(family.claims(channel) for family in OWNED_CHANNEL_FAMILIES)


def is_owned_channel(channel: Any) -> bool:
    """True when ``channel`` is a well-formed owned channel name."""
    return parse_owned_channel(channel) is not None


def is_owned_channel_event(channel: Any, event: Any) -> bool:
    """True when ``event`` is a frame type ``channel``'s family carries.

    A producer that reached for the wrong vocabulary â€” a ``training.progress`` on a
    deployment channel â€” is a bug in the producer, and the transport refuses to
    forward it rather than letting a browser render it as a state change.
    """
    reference = parse_owned_channel(channel)
    if reference is None:
        return False
    return reference.family.is_event(event)


# â”€â”€ task 6.7's five training helpers, now the wrappers they are â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Kept by name, and answering exactly what they answered before, because they are
# what ``websocket_manager``, ``strategy_service`` and
# ``tests/test_training_realtime_channels.py`` call. What changed is that the
# separator, the identifier shape and the namespace rule they apply are now the ones
# every family applies, so the training channel cannot end up stricter or looser than
# the four beside it.


def training_channel(job_id: str) -> str:
    """Return the channel name for ``job_id``.

    Raises
        ``ValueError`` when ``job_id`` is not a well-formed identifier, rather than
        returning a name that no subscriber could ever have subscribed to.
    """
    return TRAINING_FAMILY.channel(job_id)


def parse_training_channel(channel: Any) -> Optional[str]:
    """Return the job id in ``channel``, or ``None`` if it is not one.

    ``None`` means "not a training channel, or not a well-formed one" â€” the caller
    must not treat those two differently, because both are equally unroutable.
    """
    return TRAINING_FAMILY.parse(channel)


def is_training_channel(channel: Any) -> bool:
    """True when ``channel`` is a well-formed ``training.{job_id}`` name."""
    return parse_training_channel(channel) is not None


def claims_training_namespace(channel: Any) -> bool:
    """True when ``channel`` is in the training family, well-formed or not.

    The distinction from :func:`is_training_channel` is what makes a malformed name
    refusable rather than ignorable: ``training``, ``training.`` and ``training.*``
    all claim this namespace and none of them identifies a job, so a caller can tell
    "not mine to judge" from "mine, and unroutable".
    """
    return TRAINING_FAMILY.claims(channel)


def is_training_event(event: Any) -> bool:
    """True when ``event`` is a frame type ``training.{job_id}`` carries."""
    return TRAINING_FAMILY.is_event(event)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  THE TWO FRAMES THAT CARRY A BACKEND VERDICT UNCHANGED
#
#  Requirements 20.12 and 9.9. Both of these exist so the canvas renders a decision
#  the backend made rather than one the frontend inferred, and both are deliberately
#  thin: they RE-DERIVE NOTHING.
#
#  * :func:`runtime_state_frame` publishes task 8.4's ``PlanRuntimeState`` through the
#    ``to_dict()`` that task wrote for this purpose. The four labels, the bar counts
#    and the missing-port lists are all its answers. Nothing here decides whether a
#    node is ``WARMING``.
#  * :func:`canvas_state_frame` publishes task 8.3's
#    ``strategy_lifecycle.canvas_state`` mapping as it stands. Nothing here decides
#    whether a version is locked.
#
#  Neither imports the module it publishes: both take the value the caller already
#  holds. That is what keeps ``ws_channels`` free of backend imports at module scope,
#  and it is also the reason a frontend cannot end up with a fifth label or a sixth
#  lock rule â€” there is nowhere in this path that could invent one.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def runtime_state_frame(
    deployment_id: Any, runtime_state: Any
) -> Dict[str, Any]:
    """A :attr:`DeploymentEvent.RUNTIME_STATE` frame for ``runtime_state``.

    ``runtime_state`` is task 8.4's ``PlanRuntimeState`` (anything with a
    ``to_dict()``) or the mapping that method returns. A ``None`` is published as
    such: a deployment that has not evaluated yet knows nothing about its nodes, and
    an empty node map is a truthful statement of that, whereas omitting the key would
    leave the client rendering whatever it last heard.
    """
    if runtime_state is None:
        state: Dict[str, Any] = {"bars_seen": 0, "nodes": {}, "counts": {}}
    elif isinstance(runtime_state, Mapping):
        state = dict(runtime_state)
    else:
        to_dict = getattr(runtime_state, "to_dict", None)
        if not callable(to_dict):
            raise TypeError(
                "runtime_state must be a PlanRuntimeState, a mapping, or None; got "
                f"{type(runtime_state).__name__}"
            )
        state = dict(to_dict())

    return {
        "type": DeploymentEvent.RUNTIME_STATE.value,
        "channel": DEPLOYMENT_FAMILY.channel(deployment_id),
        "deployment_id": str(deployment_id),
        "runtime_state": state,
    }


def canvas_state_frame(strategy_id: Any, canvas_state: Any) -> Dict[str, Any]:
    """A :attr:`StrategyEvent.CANVAS_STATE` frame for ``canvas_state``.

    ``canvas_state`` is the mapping task 8.3's ``strategy_lifecycle.canvas_state``
    returned, forwarded field for field. A ``None`` publishes an empty mapping, which
    the frontend reads as "no verdict has arrived" â€” never as "editable".
    """
    return {
        "type": StrategyEvent.CANVAS_STATE.value,
        "channel": STRATEGY_FAMILY.channel(strategy_id),
        "strategy_id": str(strategy_id),
        "canvas_state": dict(canvas_state) if canvas_state else {},
    }


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 14.2 - THE PER-DEPLOYMENT SEQUENCE, AND THE FRAME IT STAMPS
#
#  Requirements 18.1, 18.4, 23.3, 23.6. ``design.md`` -> "Sequencing, dedup,
#  ordering, backpressure".
#
#  WHAT `seq` IS FOR, AND THEREFORE WHAT IT MAY BE
#  ----------------------------------------------
#  Exactly one consumer: ``websocketClient.js``'s existing connection-level gap
#  detector, which initialises ``expectedSequence = 1`` on every fresh connection,
#  discards a frame whose ``seq`` is BELOW that (Requirement 23.6's "SHALL NOT deliver
#  an event to a client more than once for the same channel and sequence identifier"),
#  buffers one ABOVE it and asks for a replay of the hole (Requirement 18.5's
#  reordering). Nothing new is added to the client; this is the server side of a
#  contract that already exists.
#
#  Two consequences follow from that, and they are the whole design:
#
#    * The first frame a freshly subscribed channel publishes MUST be ``1``. A larger
#      first value reads to the client as a gap it will ask to have replayed, and a
#      replay of frames that were never published is a hole that never closes.
#    * The counter only has to be monotonic for the LIFETIME OF ONE SUBSCRIPTION,
#      which is precisely what the client assumes for the Builder's channels. So there
#      is NO new persistent sequence table, and none is needed: a reconnect gets a new
#      counter and a new ``expectedSequence`` at the same moment, and the frames a
#      reconnect might redeliver are caught by the CONTENT key below rather than by
#      ``seq``.
#
#  "SEEDED FROM THE DEPLOYMENT'S OWN ROW ON SUBSCRIBE"
#  --------------------------------------------------
#  :func:`seed_signal_sequence` is called by ``WebSocketManager.subscribe_owned`` when
#  a ``signal.{deployment_id}`` channel gains its FIRST subscriber - which is reachable
#  only after ``authorize_channel_subscription`` read that deployment's own row from
#  ``strategy_deployments`` and resolved its owner. The seed is taken from that row and
#  from nothing else: the row's ``id`` is what the counter is keyed by, and the row's
#  existence is what says the counter may exist at all. There is no second read, no new
#  relation, and no column this spec had to add - the two columns the ownership lookup
#  already selects (``id``, ``user_id``) are the two facts the seed needs.
#
#  The seeded value is :data:`SIGNAL_SEQUENCE_START` - 1 - minus one, so that the first
#  :func:`next_signal_sequence` returns 1. Nothing on ``strategy_deployments`` counts
#  signals (its counters are ``total_trades``/``winning_trades``, which count FILLS and
#  would start a channel at an arbitrary number the client would read as a gap), and a
#  timestamp column is not a sequence.
#
#  SEEDING IS IDEMPOTENT, AND THAT IS NOT A DETAIL
#  ----------------------------------------------
#  A second subscriber to a channel that already has a counter does NOT reset it.
#  Resetting would hand the FIRST subscriber a ``seq`` it has already processed, and
#  Requirement 23.6 says a client discards such a frame without applying it - so a
#  reset would silently drop real state changes from an already-open page. The counter
#  is released only when the channel's last subscriber goes away
#  (:func:`release_signal_sequence`), which is the point at which no connection holds
#  an ``expectedSequence`` for it any more.
#
#  DEDUP IS ENFORCED TWICE, DELIBERATELY (Requirement 18.4)
#  -------------------------------------------------------
#  ``seq`` catches network-level duplication and reordering. It cannot catch a
#  reconnect: a new connection necessarily reuses low sequence numbers, so a replayed
#  snapshot would look like fresh history. :func:`signal_dedup_key` is the second,
#  CONTENT-level key - ``f"{signal_id}:{order_lifecycle_state}"`` - carried on every
#  frame so the page's reducer can discard a state change it has already applied
#  independently of ``seq``. Two keys because they fail in different directions, and a
#  page that had only one of them would be wrong on exactly the case the other covers.
# ═══════════════════════════════════════════════════════════════════════════


#: The first ``seq`` a freshly seeded ``signal.{deployment_id}`` channel publishes.
#: 1, because ``websocketClient.js`` initialises ``expectedSequence = 1``; a frame
#: below that is discarded as a duplicate and one above it is read as a gap.
SIGNAL_SEQUENCE_START: int = 1

#: ``deployment_id -> the last seq handed out``. In-process by design (see the header):
#: the sequence has to be monotonic for one subscription's lifetime, not forever.
_signal_sequences: Dict[str, int] = {}

#: Guards the counter. The assignment must be atomic even though this module is only
#: ever driven from an event loop: ``run_in_executor`` work and a second loop in a
#: worker thread would both otherwise be able to hand out the same ``seq`` twice, and
#: two frames sharing a ``seq`` on one channel is exactly what Requirement 23.6
#: forbids.
_signal_sequence_lock = threading.Lock()


def _signal_sequence_key(deployment_id: Any) -> str:
    """The counter's key: this deployment's own identifier, validated as a channel would.

    Raises ``ValueError`` for an identifier no ``signal.*`` channel could carry, rather
    than opening a counter for a deployment that could never be subscribed to.
    """
    SIGNAL_FAMILY.channel(deployment_id)  # the one shape rule, applied not restated
    return str(deployment_id)


def seed_signal_sequence(
    deployment_id: Any, deployment: Any = None, *, restart: bool = False
) -> int:
    """Open ``signal.{deployment_id}``'s counter, from the deployment's own row.

    Called on SUBSCRIBE - see this section's header on why a reset under a live
    subscriber would drop state changes from an already-open page.

    ``deployment`` is the ``strategy_deployments`` row the ownership lookup already
    read, passed in rather than fetched. It is only consulted to check that it is the
    row for ``deployment_id``: a row naming a different deployment means the caller
    resolved one deployment's owner and is opening another's channel, which is refused
    rather than seeded. A ``None`` row is accepted, because the id in the channel name
    the subscription was authorised against is itself the row's primary key.

    ``restart`` - THE CALLER ASSERTS NOBODY HOLDS AN ``expectedSequence``
        Off by default, so the ordinary call can only ever ensure a counter exists.

        ``WebSocketManager.subscribe_owned`` passes ``True`` for the channel's FIRST
        subscriber, and only then. That case needs it: a producer publishing to a
        channel nobody has subscribed to still stamps frames (a producer must not have
        to know whether a browser is attached), so by the time the first page arrives
        the counter can already be well above 1 - and that page's ``expectedSequence``
        is 1, so it would read every frame as a gap and sit waiting for a replay of
        frames that reached nobody. Restarting is safe there for the same reason it is
        needed: no connection had an ``expectedSequence`` for those numbers.

    Returns
        The last ``seq`` handed out on this channel after seeding, so the caller can
        tell a fresh start (``SIGNAL_SEQUENCE_START - 1``) from a counter that was
        already running.

    Raises
        ``ValueError`` for a malformed identifier, or for a row that names another
        deployment.
    """
    key = _signal_sequence_key(deployment_id)

    if deployment is not None:
        row_id = _row_field(deployment, "id", "deployment_id")
        if row_id is not None and str(row_id) != key:
            raise ValueError(
                f"Refusing to seed signal sequence for deployment {key!r} from a row "
                f"that names {str(row_id)!r}. The row this channel's ownership was "
                f"resolved against is the row its counter is seeded from."
            )

    with _signal_sequence_lock:
        if restart:
            _signal_sequences[key] = SIGNAL_SEQUENCE_START - 1
            return _signal_sequences[key]
        return _signal_sequences.setdefault(key, SIGNAL_SEQUENCE_START - 1)


def next_signal_sequence(deployment_id: Any) -> int:
    """The next ``seq`` for ``signal.{deployment_id}``. Monotonic, scoped to that channel.

    Never returns the same value twice for one deployment while its counter is open,
    which is Requirement 23.6's per-channel uniqueness. A channel nobody has subscribed
    to is seeded here rather than refused: a producer must not have to know whether a
    browser is attached in order to stamp a frame, and a frame published to an empty
    channel simply reaches nobody.

    Raises
        ``ValueError`` for an identifier no ``signal.*`` channel could carry.
    """
    key = _signal_sequence_key(deployment_id)
    with _signal_sequence_lock:
        current = _signal_sequences.get(key, SIGNAL_SEQUENCE_START - 1) + 1
        _signal_sequences[key] = current
        return current


def release_signal_sequence(deployment_id: Any) -> None:
    """Forget ``signal.{deployment_id}``'s counter. Called when its last subscriber goes.

    That is the only safe moment: while any connection holds an ``expectedSequence``
    for this channel, restarting the counter would republish sequence numbers that
    connection has already processed and Requirement 23.6 has it discard them.

    Never raises - a malformed id simply has no counter to release.
    """
    try:
        key = _signal_sequence_key(deployment_id)
    except ValueError:
        return
    with _signal_sequence_lock:
        _signal_sequences.pop(key, None)


def reset_signal_sequences() -> None:
    """Drop every counter. For a test that wants a known starting point."""
    with _signal_sequence_lock:
        _signal_sequences.clear()


def signal_dedup_key(signal_id: Any, order_lifecycle_state: Any) -> str:
    """Requirement 18.4's content-level identity: ``f"{signal_id}:{state}"``.

    The stable identifier of a STATE CHANGE, not of a frame and not of a stream
    position. Two frames carrying this key describe the same change however they
    arrived - a retransmission, a reconnect-triggered snapshot replay, or a producer
    that published twice - and the page applies it once.

    ``order_lifecycle_state`` may be an ``OrderLifecycleState``, its ``.value``, or any
    string: the enum's value is used where there is one, so the key a frame carries is
    the same key whichever side of the boundary built it.
    """
    state = getattr(order_lifecycle_state, "value", order_lifecycle_state)
    return f"{signal_id}:{state}"


def _row_field(source: Any, *names: str) -> Any:
    """The first of ``names`` present on ``source``, read the same way for dict or object."""
    for name in names:
        if isinstance(source, Mapping):
            if name in source and source[name] is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return None


def signal_frame(
    event: Any,
    signal: Any,
    *,
    deployment_id: Any = None,
    seq: Optional[int] = None,
    reason: Optional[str] = None,
    previous_state: Any = None,
) -> Dict[str, Any]:
    """One ``signal.{deployment_id}`` frame. Re-derives nothing.

    The same disposition as :func:`runtime_state_frame` and :func:`canvas_state_frame`
    above: ``signal`` is task 10.1's ``signal_service.Signal`` (anything with a
    ``to_public_dict()``) or the mapping that method returns, and the payload is that
    mapping forwarded field for field. Nothing here decides a signal's state, its
    lifecycle, or what a client may see - ``to_public_dict`` is the closed field set
    that makes Requirement 20.3 hold on the wire, and re-projecting it here would be a
    second place for a credential-bearing field to be admitted.

    ``deployment_id`` defaults to the signal's own, so the channel a frame names and
    the deployment its payload belongs to cannot disagree. Passing one that contradicts
    the payload is refused rather than published.

    ``seq`` defaults to :func:`next_signal_sequence` for that deployment. It is
    injectable only so a caller that has already taken a number (or a test) can supply
    it; a caller that omits it cannot accidentally publish an unsequenced frame.

    Raises
        ``TypeError`` when ``signal`` exposes no public projection,
        ``ValueError`` for an event outside :class:`SignalEvent`, for a signal that
        names no deployment, or for a ``deployment_id`` that contradicts the payload.
    """
    event_value = getattr(event, "value", event)
    if not SIGNAL_FAMILY.is_event(event_value):
        raise ValueError(
            f"{event_value!r} is not one of signal.{{deployment_id}}'s frame types "
            f"({sorted(SIGNAL_CHANNEL_EVENTS)}). A producer that reached for another "
            f"family's vocabulary is a bug in the producer, not a frame."
        )

    if isinstance(signal, Mapping):
        payload = dict(signal)
    else:
        to_public_dict = getattr(signal, "to_public_dict", None)
        if not callable(to_public_dict):
            raise TypeError(
                "signal must be a signal_service.Signal, a mapping from its "
                f"to_public_dict(), or None; got {type(signal).__name__}"
            )
        payload = dict(to_public_dict())

    resource = _row_field(payload, "deployment_id")
    if deployment_id is None:
        deployment_id = resource
    elif resource is not None and str(resource) != str(deployment_id):
        raise ValueError(
            f"Refusing to publish signal {payload.get('id')!r} on "
            f"signal.{deployment_id} while its own record names deployment "
            f"{resource!r}. A frame must not say it is about a deployment its payload "
            f"disagrees with."
        )
    if deployment_id is None:
        raise ValueError(
            f"Signal {payload.get('id')!r} names no deployment, so there is no "
            f"signal.{{deployment_id}} channel to publish it on."
        )

    channel = SIGNAL_FAMILY.channel(deployment_id)
    state = payload.get("order_lifecycle_state")

    frame: Dict[str, Any] = {
        "type": event_value,
        "channel": channel,
        "deployment_id": str(deployment_id),
        # Requirement 23.6 - per-channel, monotonic, assigned once.
        "seq": next_signal_sequence(deployment_id) if seq is None else int(seq),
        # Requirement 18.4 - the content-level key, IN ADDITION to seq.
        "dedup_key": signal_dedup_key(payload.get("id"), state),
        "signal_id": payload.get("id"),
        "order_lifecycle_state": state,
        "signal": payload,
    }
    if previous_state is not None:
        frame["previous_state"] = getattr(previous_state, "value", previous_state)
    if reason is not None:
        frame["reason"] = reason
    return frame


class EventType(str, Enum):
    """Event types per channel."""
    
    # Bot Status
    BOT_HEALTH = "bot_health"
    BOT_CONNECTED = "bot_connected"
    BOT_DISCONNECTED = "bot_disconnected"
    BOT_ERROR = "bot_error"
    HEARTBEAT = "heartbeat"
    
    # Signal Trace
    SIGNAL_RECEIVED = "signal_received"
    SIGNAL_VALIDATED = "signal_validated"
    SIGNAL_RISK_CHECKED = "signal_risk_checked"
    SIGNAL_EXECUTED = "signal_executed"
    SIGNAL_REJECTED = "signal_rejected"
    SIGNAL_FAILED = "signal_failed"
    
    # Execution Events
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_PARTIAL = "order_partial"
    ORDER_REJECTED = "order_rejected"
    ORDER_ERROR = "order_error"
    
    # Risk Events
    RISK_BLOCK = "risk_block"
    RISK_WARNING = "risk_warning"
    KILL_SWITCH = "kill_switch"
    POSITION_LIMIT = "position_limit"
    DRAWDOWN_ALERT = "drawdown_alert"
    
    # Deployment Events
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILED = "deploy_failed"
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"


# Event types organized by channel
CHANNEL_EVENTS: Dict[ChannelType, Dict[str, str]] = {
    ChannelType.BOT_STATUS: {
        "BOT_HEALTH": EventType.BOT_HEALTH.value,
        "BOT_CONNECTED": EventType.BOT_CONNECTED.value,
        "BOT_DISCONNECTED": EventType.BOT_DISCONNECTED.value,
        "BOT_ERROR": EventType.BOT_ERROR.value,
        "HEARTBEAT": EventType.HEARTBEAT.value
    },
    ChannelType.SIGNAL_TRACE: {
        "SIGNAL_RECEIVED": EventType.SIGNAL_RECEIVED.value,
        "SIGNAL_VALIDATED": EventType.SIGNAL_VALIDATED.value,
        "SIGNAL_RISK_CHECKED": EventType.SIGNAL_RISK_CHECKED.value,
        "SIGNAL_EXECUTED": EventType.SIGNAL_EXECUTED.value,
        "SIGNAL_REJECTED": EventType.SIGNAL_REJECTED.value,
        "SIGNAL_FAILED": EventType.SIGNAL_FAILED.value
    },
    ChannelType.EXECUTION_EVENTS: {
        "ORDER_SUBMITTED": EventType.ORDER_SUBMITTED.value,
        "ORDER_FILLED": EventType.ORDER_FILLED.value,
        "ORDER_PARTIAL": EventType.ORDER_PARTIAL.value,
        "ORDER_REJECTED": EventType.ORDER_REJECTED.value,
        "ORDER_ERROR": EventType.ORDER_ERROR.value
    },
    ChannelType.RISK_EVENTS: {
        "RISK_BLOCK": EventType.RISK_BLOCK.value,
        "RISK_WARNING": EventType.RISK_WARNING.value,
        "KILL_SWITCH": EventType.KILL_SWITCH.value,
        "POSITION_LIMIT": EventType.POSITION_LIMIT.value,
        "DRAWDOWN_ALERT": EventType.DRAWDOWN_ALERT.value
    },
    ChannelType.DEPLOYMENT_EVENTS: {
        "DEPLOY_STARTED": EventType.DEPLOY_STARTED.value,
        "DEPLOY_SUCCESS": EventType.DEPLOY_SUCCESS.value,
        "DEPLOY_FAILED": EventType.DEPLOY_FAILED.value,
        "BOT_STARTED": EventType.BOT_STARTED.value,
        "BOT_STOPPED": EventType.BOT_STOPPED.value
    }
}


# Standard WebSocket message schema
WS_MESSAGE_SCHEMA = {
    "type": "string",           # Event type
    "channel": "string",        # Channel name
    "timestamp": "string",      # ISO 8601 timestamp
    "bot_id": "string|null",    # Associated bot ID
    "strategy_id": "string|null", # Associated strategy ID
    "tenant_id": "string",      # Tenant identifier
    "message_id": "string",     # Unique message ID
    "payload": "object"         # Event-specific data
}


def create_ws_message(
    event_type: EventType,
    channel: ChannelType,
    tenant_id: str,
    bot_id: str = None,
    strategy_id: str = None,
    payload: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Create a standard WebSocket message.
    
    Args:
        event_type: Type of event
        channel: Target channel
        tenant_id: Tenant identifier
        bot_id: Associated bot ID
        strategy_id: Associated strategy ID
        payload: Event-specific data
        
    Returns:
        Standardized message dict
    """
    import uuid
    from datetime import datetime, timezone
    
    return {
        "type": event_type.value,
        "channel": channel.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_id": bot_id,
        "strategy_id": strategy_id,
        "tenant_id": tenant_id,
        "message_id": str(uuid.uuid4()),
        "payload": payload or {}
    }


# Exports
__all__ = [
    "ChannelType",
    "EventType",
    "VALID_CHANNELS",
    "is_valid_channel",
    "assert_valid_channel",
    "CHANNEL_EVENTS",
    "create_ws_message",
    # strategy-builder task 6.7 â€” the parameterised training channel
    "TRAINING_CHANNEL_NAMESPACE",
    "TRAINING_CHANNEL_PREFIX",
    "claims_training_namespace",
    "TrainingEvent",
    "TRAINING_EVENTS",
    "TRAINING_LIFECYCLE_EVENTS",
    "training_channel",
    "parse_training_channel",
    "is_training_channel",
    "is_training_event",
    # strategy-builder task 8.5 â€” the other four parameterised channels
    "OwnedChannelFamily",
    "OwnedChannelRef",
    "OWNED_CHANNEL_FAMILIES",
    "OWNED_CHANNEL_FAMILIES_BY_NAMESPACE",
    "OWNED_CHANNEL_EVENTS",
    "TRAINING_FAMILY",
    "BUILDER_VALIDATION_FAMILY",
    "STRATEGY_FAMILY",
    "DEPLOYMENT_FAMILY",
    "EXECUTION_FAMILY",
    "ValidationEvent",
    "StrategyEvent",
    "DeploymentEvent",
    "ExecutionEvent",
    "VALIDATION_CHANNEL_EVENTS",
    "STRATEGY_CHANNEL_EVENTS",
    "DEPLOYMENT_CHANNEL_EVENTS",
    "EXECUTION_CHANNEL_EVENTS",
    # trading-lifecycle-integration task 14.1 â€” signal.{deployment_id}
    "SIGNAL_FAMILY",
    "SignalEvent",
    "SIGNAL_CHANNEL_EVENTS",
    # trading-lifecycle-integration task 14.2 — the sequence and the frame
    "SIGNAL_SEQUENCE_START",
    "seed_signal_sequence",
    "next_signal_sequence",
    "release_signal_sequence",
    "reset_signal_sequences",
    "signal_dedup_key",
    "signal_frame",
    "owned_channel",
    "parse_owned_channel",
    "claims_owned_namespace",
    "is_owned_channel",
    "is_owned_channel_event",
    "runtime_state_frame",
    "canvas_state_frame",
]
