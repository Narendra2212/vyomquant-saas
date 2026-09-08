"""
backend/paper/paper_session_service.py - the Paper_Session pipeline and its state machine.

Spec: marketplace-subscriptions-paper-trading tasks 27.1, 27.2, 27.3 and 27.4. ``design.md`` ->
"``paper/paper_session_service.py``". Requirements 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9,
17.10, 17.13, 17.14, 17.15, 19.12, 21.4, 23.1, 23.5, 27.3, 27.4.

Exposes
-------
start_session(...)                the Requirement 17.9 pipeline, in order
pause_session / resume_session / stop_session / reset_session
                                  the four operations of Requirement 17.7, on one implementation
apply_operation(...)              that implementation - the gate, the write, the record
OperationFinaliser                the seam between the transition and the record (Req 17.8)
StopOutcome / ResetOutcome        what a stop released and what a reset restored
StopOutcome.complete              the ONLY thing a route may report a stop complete on (Req 17.8)
SessionFinals / commit_session_finals(...)
                                  the closing equity point and the metrics row
release_session_resources(...)    the mds unsubscribe, the local one, then the registrations
recorded_capital(...)             the RECORDED initial capital, read back in both units (Req 17.15)
OPEN_ORDER_STATES                 the two states a reset cancels, and the only two it may
PAPER_SESSION_TRANSITIONS         the six permitted edges, as an adjacency map
SESSION_OPERATIONS                ``start``, ``pause``, ``resume``, ``stop``, ``reset``
OPERATION_TRANSITIONS             ``operation -> {from_state: to_state}``
can_operate / target_state        the gate, as two pure functions
PaperSessionOperationRejected     409 ``PAPER_SESSION_OPERATION_REJECTED`` naming BOTH (Req 17.14)
session_not_found(session_id)      the ONE "no such session" refusal, for the route layer too
PaperSessionLimitReached          429 ``PAPER_SESSION_LIMIT_REACHED`` with a reason (Req 27.4)
PaperStartRefused                 409/403/422 ``PAPER_START_REFUSED`` naming the validation
VALIDATION_*                      the ``details["validation"]`` vocabulary, one per check
MAX_CONCURRENT_PAPER_SESSIONS_PER_USER / resolve_concurrency_limit
MAX_SESSION_CAPITAL_MAJOR_UNITS / resolve_capital_maximum_major
validate_capital(...)             Requirement 17.5, as a pure function
validate_timeframe(...)           the supported-timeframe check, against the platform's one table
assert_strategy_supports_market(...)  the strategy/symbol/timeframe compatibility check
DEPLOYABLE_LIFECYCLE_STATES       the version states a Paper_Session may execute

THE ORDER IS THE CONTRACT, NOT A TIDY HABIT
-------------------------------------------
Requirement 17.9 states the pipeline as a sequence and Requirement 17.13 states what a refusal
leaves behind: no Paper_Session, no paper order, no paper balance and no market-data
subscription, with every existing Paper_Account unchanged. Those two are one statement. The way
this module keeps it is structural rather than defensive: **every** validation runs before the
first INSERT is issued, so "nothing was created" is a fact about which statements exist above
the create block, not a claim about exception handling. :func:`start_session` reads top to bottom
as:

  1. ``entitlement_resolver.resolve`` - the SINGLE admission decision (Requirement 11.10, P-16).
     This module does not implement a second one, does not read ``library_subscriptions`` and
     does not compare an author id: deployment and Paper_Session start reach the same function so
     that the two cannot answer differently.
  2. :func:`~paper_simulator.assert_paper_simulator` - Requirement 13.11's refusal, before a
     session exists to be misconfigured.
  3. the version resolve and its deployable-lifecycle check (Requirement 17.4, "the strategy's
     publication or lifecycle status").
  4. :func:`validate_capital` - Requirement 17.5, exact and in Minor_Units.
  5. the exchange market metadata lookup - :func:`~paper_simulator.resolve_market_metadata`,
     which refuses rather than defaulting a precision (Requirement 28.3).
  6. :func:`validate_timeframe` - against the platform's one timeframe table.
  7. :func:`assert_strategy_supports_market` - the plan's own resolved markets.
  8. the per-user concurrent cap (Requirement 27.4), LAST of the validations, because it is the
     only one that is a fact about the caller's other sessions rather than about this request:
     refusing it first would tell a caller they are at their limit for a request that was never
     admissible anyway.

Only then the three creates, then the feed, then ``RUNNING``.

WHAT "ONE TRANSACTION" MEANS OVER THIS TRANSPORT - READ THIS FIRST
-----------------------------------------------------------------
Task 27.1 and ``design.md`` write the create block as ``BEGIN TRANSACTION`` / ``COMMIT`` around
three INSERTs. **This deployment has no transaction.** The Persistence_Layer is reached through
PostgREST, which speaks one HTTP statement per request: there is no ``BEGIN``, no ``COMMIT``, no
``ROLLBACK``, no ``SELECT ... FOR UPDATE`` and no ``RETURNING`` expression.
``paper_simulator``'s "WHAT ``ONE TRANSACTION`` MEANS OVER THIS TRANSPORT" section header,
``paper_repository``'s "WHAT SUBSTITUTES FOR ``SELECT ... FOR UPDATE``" and
``paper_events``' sequence-allocation note all record this already. This module reproduces the
strongest ordering the transport supports and states the residual gap rather than describing a
transaction it does not have.

The three creates are three requests, in this order and no other:

  1. ``paper_sessions`` at ``CREATED``, with the frozen ``config`` and ``event_sequence = 0``.
  2. ``paper_accounts`` scoped to that session, at ``version = 1``.
  3. the opening ``paper_equity_snapshots`` row, ``cause = 'SESSION_START'``.

The order is chosen so that every prefix of it is a state the rest of the system reads correctly:

  * A session with no account is a session at ``CREATED`` that is not ``RUNNING``, holds no order,
    no position and no balance, and can therefore trade nothing. Every accounting statement in
    ``paper_repository`` is scoped by ``(user_id, session_id)`` and finds nothing.
  * A session with an account and no opening snapshot has an equity curve that starts at its
    first fill instead of at its start. Requirement 18.9 already reports zero drawdown below two
    snapshots, so the figure is not wrong - it is a missing point, and
    :func:`~paper_repository.get_equity_snapshots` reports the series it has.

  The reverse orders are both worse: an account before its session would be an orphan the
  ``session_id`` foreign key refuses outright, and a snapshot before its account would record an
  equity that no account states.

**The residual gap, stated plainly.** If the process dies between the three requests, there is no
``ROLLBACK`` and the prefix that committed stays committed. Concretely:

  * died after (1): a ``paper_sessions`` row at ``CREATED`` with no ``paper_accounts`` row. It is
    visible in ``GET /api/paper/sessions``, it never ran, and it counts against nothing - the
    concurrency cap of Requirement 27.4 counts ``RUNNING`` rows only, so it cannot lock a user
    out. It is inert, not dangerous, and it is not cleaned up by this module: a delete would be
    the one statement that could remove a session a user is looking at.
  * died after (2): the same plus an account at the recorded initial capital and no opening
    snapshot. Balances are correct; the equity series is short by its first point.
  * died after (3): the create block completed. The session is at ``CREATED`` with no feed, which
    is exactly the state a feed refusal leaves and is already a state the API reports.

  What CANNOT happen is the failure that would matter: an account whose balances disagree with
  its session's ``initial_capital_minor``, or two accounts for one session. The first is ruled out
  because both figures are derived from the same validated ``initial_capital_minor`` in one place
  (:func:`validate_capital`) before either statement is issued; the second by
  ``uq_paper_account_session``, and :func:`~paper_repository.get_or_create_account` is written to
  lose to that index and read the winner. Neither is this module's optimism - one is arithmetic
  done before any write, the other is the database's.

  Closing the gap completely means moving the three INSERTs into one database function (``rpc``),
  so PostgreSQL holds the transaction. That is a schema and deployment change outside tasks 27.1
  and 27.3, and it is recorded here rather than glossed over.

THE FEED IS OPENED AFTER THE CREATES, AND A REFUSAL LEAVES ``CREATED``
---------------------------------------------------------------------
:func:`~paper_market_feed.open_feed` is called after the create block and before the transition
to ``RUNNING``. Its two refusals - the correctness-floor block (Requirement 14.4) and the
DEV_MODE mock interface (Requirement 14.8) - both raise before it publishes ``subscribe`` to
``mds:commands`` and before it subscribes to the data channel, so a refused feed leaves the
session at ``CREATED`` with **no subscription** and no ``market_data_source`` write. This module
adds nothing to that guarantee; it simply does not transition the session until ``open_feed``
has returned.

One necessary consequence, stated rather than hidden: ``paper_sessions.market_data_source`` is
``NOT NULL`` and ``paper_simulator.freeze_session_config`` requires the source identity, so the
source has to be KNOWN before statement (1). It is obtained with
:func:`~paper_market_feed.select_market_data_source`, the pure half of the same rule
``open_feed`` runs - whose own docstring says it exists "so a session-start path can show a
would-be refusal before it creates a session row". A blocked selection is therefore refused
BEFORE the creates, which is strictly stronger than leaving a session at ``CREATED``: there is
nothing honest to write in that column for a deployment whose feed is inadmissible, and
inventing a source identity to fill it would be the fabricated measurement Requirement 28.3
forbids. The mock-interface refusal and a failed ``mds`` handshake remain post-create and leave
``CREATED``, which is the case Requirement 14.4 describes. The selection is deterministic in its
``measurements`` argument, so the source ``open_feed`` selects is the source the config recorded.

THE STATE MACHINE, ENFORCED THREE WAYS
--------------------------------------
``CREATED``, ``RUNNING``, ``PAUSED``, ``STOPPED``; ``start`` from ``CREATED``, ``pause`` from
``RUNNING``, ``resume`` from ``PAUSED``, ``stop`` from ``RUNNING`` or ``PAUSED``, ``reset`` from
``STOPPED``. Five operations over six edges, and the three enforcement points are the same three
the order machine has:

  1. ``chk_paper_session_state`` - the four values are the only values the column holds.
  2. ``paper_session_allowed_transitions`` + ``trg_paper_session_guard`` - the six edges are the
     only edges an UPDATE may take, including a ``psql`` UPDATE and a future handler.
  3. :func:`can_operate` in this module, consulted BEFORE any statement is issued, so Requirement
     17.14's "leave the state, orders, positions, balances and persisted history unchanged" is a
     fact about statements not issued rather than about a constraint violation caught late.

**009's seed and these five operations agree, checked rather than assumed.** 009 section 2 seeds
``('CREATED','RUNNING'), ('RUNNING','PAUSED'), ('PAUSED','RUNNING'), ('RUNNING','STOPPED'),
('PAUSED','STOPPED'), ('STOPPED','CREATED')`` - six pairs, no seventh, no self-edge - and
:data:`PAPER_SESSION_TRANSITIONS` flattens to exactly that set.
``tests/test_submission_state_agreement.py::TestPaperSessionStateAgreement`` holds the two
against each other by parsing the migration, which is what keeps them in step; 009's own seed
comment asks for precisely this constant.

An operation from a state that does not permit it raises
:class:`PaperSessionOperationRejected` - 409 ``PAPER_SESSION_OPERATION_REJECTED`` carrying BOTH
the current state and the rejected operation in ``details``, per Requirement 17.14.

ANOTHER USER'S SESSION ANSWERS EXACTLY AS AN UNKNOWN ONE DOES (Requirement 21.4)
-------------------------------------------------------------------------------
Every read of a session here goes through :func:`~paper_repository.read_session`, which carries
``user_id`` as a PREDICATE. So another tenant's session is never fetched, not merely never
returned, and :func:`apply_operation` cannot tell the two cases apart to answer them differently:
both produce ``None`` and both raise the same ``NOT_FOUND``. There is no branch in this module
that could be made to disclose existence, because there is no value in scope that differs
between the two.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* **The DAG runtime, and the Signal_Trace write.** The session loop (task 27.2) IS here - see
  :func:`session_loop`, :func:`step_session` and :func:`spawn_session_loop` - but the two
  collaborators it drives are not, and deliberately: the platform's ONE DAG execution runtime and
  its ONE Signal_Trace recorder are injected, so this package implements neither a second strategy
  evaluation path (Requirement 17.10) nor a second signal store (Requirement 23.5), and its pinned
  import graph (``tests/test_paper_no_random.py``) does not have to grow to reach them.
  :func:`no_session_loop` remains the DEFAULT ``spawn_loop`` for exactly one reason: this module has
  no runtime to default to, so the only loop it could install unasked is one with ``evaluate=None``
  - a session producing bars and no signals. The wiring is :func:`spawn_session_loop`'s, and the
  layer that owns the runtime performs it.

  **The seam's shape, and why it is four arguments and not two.** ``spawn(session_row, feed_handle,
  *, config, account_id)``. The loop cannot step without the frozen configuration and the session's
  Paper_Account, and :func:`start_session` CREATES both - so an installing layer holding a
  two-argument seam had to either re-derive Requirement 16.12's frozen configuration (a second
  implementation of it, free to disagree with the row) or install nothing. The pipeline supplies
  what it created; the installing layer supplies what it owns. That is what makes the runtime a
  seam rather than a gap, and it is why the module's import list does not have to grow.
* **The objects a stop releases.** :func:`stop_session` performs the whole Requirement 17.8
  ordering - gate, transition, loop settled, finals committed, record and broadcast, market-data
  subscription released, Paper_Channel registrations closed, and only then a report - but it does
  not OWN the two objects it releases. The ``FeedHandle`` and the loop ``asyncio.Task`` are passed
  in, because :func:`spawn_session_loop` deliberately returns the task rather than storing it and a
  registry in this module would be a second place a session could be tracked from. A stop given
  neither reports the release as outstanding (``StopOutcome.complete is False``) rather than
  claiming one it could not perform. The section header "THE STOP AND THE RESET" states the whole
  ordering, the reason each step is where it is, and the residual gap prefix by prefix.
* **A ``DELETE``.** Nowhere in this module, and that is what makes Requirement 17.15's "nothing is
  deleted" structural: :func:`reset_session` cancels, closes to zero and starts a new
  ``series_index``, and the pre-reset rows stay readable because no statement removes them.
* **Arithmetic.** ``paper_accounting`` owns every balance and equity computation.
  :func:`validate_capital` converts Minor_Units to the account's major-unit figure and does
  nothing else; the opening snapshot's four money columns are written from the account row the
  database returned.
* **A second entitlement decision, a second timeframe table and a second market-metadata
  reader.** Each of those exists once, elsewhere, and is called from here.
* **``random``, and any HTTP status other than the three this module's own exceptions carry.**
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from starlette.concurrency import run_in_threadpool

from backend_app.backend.asset_universe import load_exchange_markets
from backend_app.backend.market_data_contract import (
    MarketDataContractError,
    interval_for,
)
from backend_app.backend.marketplace.entitlement_resolver import (
    Entitlement,
    EntitlementReadFailed,
    resolve as resolve_entitlement,
)
from backend_app.backend.marketplace.money import (
    UnsupportedCurrency,
    minor_unit_exponent,
)
from backend_app.backend.metrics import guarded_collector
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleState,
    normalise_lifecycle_state,
)
from backend_app.backend.paper import paper_accounting as accounting
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import (
    NOT_FOUND,
    PAPER_SESSION_LIMIT_REACHED,
    PAPER_SESSION_OPERATION_REJECTED,
    PAPER_START_REFUSED,
    PaperError,
    paper_read_failed,
)
from backend_app.backend.paper.paper_events import (
    PaperEvent,
    PaperEventEnvelope,
    broadcast,
    record_event,
    utc_now,
)
from backend_app.backend.paper.paper_market_feed import (
    FeedHandle,
    MarketEvent,
    PaperFeedConfig,
    PaperFeedError,
    PaperMarketDataUnavailable,
    next_validated_event,
    open_feed,
    publish_unsubscribe,
    select_market_data_source,
)
from backend_app.backend.paper.paper_order_state import PaperOrderState
from backend_app.backend.paper.paper_simulator import (
    SIMULATOR_MODULE,
    SIMULATOR_QUALNAME,
    FillOutcome,
    InvalidOrderIntent,
    InvalidSessionConfig,
    OrderIntent,
    SessionConfig,
    SubmitOutcome,
    account_of,
    assert_paper_simulator,
    check_resting_orders,
    freeze_session_config,
    positions_of,
    resolve_market_metadata,
    session_config_from_jsonb,
    submit_intent,
)

logger = logging.getLogger("PaperSessionService")


# ══════════════════════════════════════════════════════════════════════════
# THE SIMULATOR IDENTITY GUARD RUNS BEFORE ANYTHING IS CREATED (Req 13.11)
# ══════════════════════════════════════════════════════════════════════════


class _PaperSimulatorIdentity:
    """The identity ``assert_paper_simulator`` is given when the caller names no simulator.

    ``paper_simulator`` records the Paper_Simulator this package owns as the pair
    ``(SIMULATOR_MODULE, SIMULATOR_QUALNAME)`` and deliberately holds
    :data:`~paper_simulator.FORBIDDEN_SIMULATORS` as string pairs so that resolving it imports
    nothing. The class object itself is not in the tree yet - its execution surface arrived as
    module-level functions (``submit_intent``, ``apply_fill``, ``check_resting_orders``) rather
    than as a class - so there is no ``PaperSimulator`` to hand the guard.

    This is that object, and it is nothing more: two dunder attributes carrying the SAME two
    strings ``config["simulator"]`` records, so ``assert_paper_simulator(...)`` performs the real
    identity comparison against the real recorded identity rather than being skipped for want of
    an argument. It is not a stand-in for the simulator, it is never called, and when the class
    lands a caller passes it and this object stops being reached.
    """

    __module__ = SIMULATOR_MODULE
    __qualname__ = SIMULATOR_QUALNAME


#: The default ``simulator`` argument of :func:`start_session`. Named so a test can assert that
#: the guard ran against the recorded identity and not against ``None``.
DEFAULT_SIMULATOR: Any = _PaperSimulatorIdentity


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION, WITH ITS RANGE AS PART OF THE DEFINITION
# ══════════════════════════════════════════════════════════════════════════
#
# Both figures are POLICY, not measurement, which is why a default is legitimate here while a
# default price, precision or fee would not be (Requirement 28.3). Each is a module-level default
# plus an explicit argument, and each argument is validated against the range the requirement
# states - so a deployment that configures 0 or 10_000 concurrent sessions is refused at the point
# of configuration rather than discovered when a user cannot start a session. There is no
# ``os.getenv`` here, following ``paper_market_feed`` and the marketplace package: nothing under
# ``backend_app/backend/paper/`` reads the environment, so the whole package stays importable and
# testable without one, and the route layer (task 28.1) is where a deployment value is bound.

#: Requirement 27.4's per-user concurrent Paper_Session cap. Three.
MAX_CONCURRENT_PAPER_SESSIONS_PER_USER = 3

#: The range that figure is configurable within, inclusive at both ends. One, because a
#: deployment that permits no concurrent session permits no session at all - the cap counts
#: ``RUNNING`` rows and a start would refuse itself. Twenty, because each session holds an
#: ``asyncio`` task, an ``mds`` subscription and a Paper_Channel registration.
MIN_CONCURRENT_SESSION_LIMIT = 1
MAX_CONCURRENT_SESSION_LIMIT = 20

#: Requirement 17.5's per-session maximum initial simulated capital, in MAJOR currency units.
MAX_SESSION_CAPITAL_MAJOR_UNITS = Decimal("1000000")

#: The range that figure is configurable within, inclusive, in major units - Requirement 17.5's
#: "1 to 1,000,000,000 major currency units", verbatim.
MIN_SESSION_CAPITAL_MAXIMUM_MAJOR = Decimal("1")
MAX_SESSION_CAPITAL_MAXIMUM_MAJOR = Decimal("1000000000")


def resolve_concurrency_limit(value: Any = None) -> int:
    """The per-user concurrent session cap, defaulted and range-checked.

    ``None`` takes :data:`MAX_CONCURRENT_PAPER_SESSIONS_PER_USER`. Anything else must be an exact
    integer inside ``[MIN_CONCURRENT_SESSION_LIMIT, MAX_CONCURRENT_SESSION_LIMIT]``.

    Raises:
        ValueError: the value is not an exact integer, or is outside the configurable range. A
            ``ValueError`` and not a :class:`PaperError` because a misconfigured cap is a
            deployment fault rather than something a caller did, and it must not reach a user as a
            4xx describing their request.
    """
    if value is None:
        return MAX_CONCURRENT_PAPER_SESSIONS_PER_USER
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"MAX_CONCURRENT_PAPER_SESSIONS_PER_USER must be an exact int, got {value!r}"
        )
    if not (MIN_CONCURRENT_SESSION_LIMIT <= value <= MAX_CONCURRENT_SESSION_LIMIT):
        raise ValueError(
            f"MAX_CONCURRENT_PAPER_SESSIONS_PER_USER must be between "
            f"{MIN_CONCURRENT_SESSION_LIMIT} and {MAX_CONCURRENT_SESSION_LIMIT} inclusive "
            f"(Requirement 27.4), got {value}"
        )
    return int(value)


def resolve_capital_maximum_major(value: Any = None) -> Decimal:
    """The per-session capital maximum in major units, defaulted and range-checked.

    ``None`` takes :data:`MAX_SESSION_CAPITAL_MAJOR_UNITS`. A ``float`` is refused outright: the
    maximum is compared against an exact decimal amount, and a binary float has already lost
    exactness before the comparison (Requirement 18.1).

    Raises:
        ValueError: the value is not an exact decimal, or is outside Requirement 17.5's
            configurable range.
    """
    if value is None:
        return MAX_SESSION_CAPITAL_MAJOR_UNITS
    if isinstance(value, float):
        raise ValueError(
            "the per-session capital maximum must not be a float; pass a Decimal, an int or a "
            "decimal string (Requirement 18.1)"
        )
    try:
        amount = Decimal(value) if not isinstance(value, Decimal) else value
    except (ArithmeticError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"the per-session capital maximum must be an exact decimal, got {value!r}"
        ) from exc
    if not (
        MIN_SESSION_CAPITAL_MAXIMUM_MAJOR
        <= amount
        <= MAX_SESSION_CAPITAL_MAXIMUM_MAJOR
    ):
        raise ValueError(
            f"the per-session capital maximum must be between "
            f"{MIN_SESSION_CAPITAL_MAXIMUM_MAJOR} and {MAX_SESSION_CAPITAL_MAXIMUM_MAJOR} major "
            f"currency units inclusive (Requirement 17.5), got {amount}"
        )
    return amount


# ══════════════════════════════════════════════════════════════════════════
# THE STATE MACHINE (Requirements 17.7, 17.14 - task 27.3)
# ══════════════════════════════════════════════════════════════════════════

#: ``chk_paper_session_state``'s four values, imported from the module that owns the column so
#: there is one list. Re-exported under this name because a reader of the state machine looks for
#: it here.
SESSION_STATES: Tuple[str, ...] = repo.SESSION_STATES

STATE_CREATED = "CREATED"
STATE_RUNNING = "RUNNING"
STATE_PAUSED = "PAUSED"
STATE_STOPPED = "STOPPED"

#: The six permitted edges of Requirement 17.7, as an adjacency map - the same shape
#: ``paper_order_state.PAPER_ORDER_TRANSITIONS`` and
#: ``strategy_lifecycle.VERSION_TRANSITIONS`` use, including the rule that **every** state is a
#: key so a missing key cannot read as "terminal by accident".
#:
#: This is the Python constant ``009_paper_trading.sql`` section 2's seed comment asks task 27.3
#: for ("Task 27.3 defines the matching Python constant; keep the two in step"). Flattened it is
#: exactly 009's six pairs, and ``tests/test_submission_state_agreement.py`` parses the migration
#: and asserts the two are the same machine.
#:
#: ``STOPPED -> CREATED`` is the reset edge and it is the only way back: there is no
#: ``STOPPED -> RUNNING``, so a stopped session is restarted by resetting it and starting it
#: again, and the equity series that follows is a NEW series
#: (``paper_equity_snapshots.series_index = previous + 1``, Requirement 17.15) rather than a
#: continuation of one that ended.
PAPER_SESSION_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    STATE_CREATED: (STATE_RUNNING,),
    STATE_RUNNING: (STATE_PAUSED, STATE_STOPPED),
    STATE_PAUSED: (STATE_RUNNING, STATE_STOPPED),
    STATE_STOPPED: (STATE_CREATED,),
}

OPERATION_START = "start"
OPERATION_PAUSE = "pause"
OPERATION_RESUME = "resume"
OPERATION_STOP = "stop"
OPERATION_RESET = "reset"

#: The five operations of Requirement 17.7, in the order the requirement names them.
SESSION_OPERATIONS: Tuple[str, ...] = (
    OPERATION_START,
    OPERATION_PAUSE,
    OPERATION_RESUME,
    OPERATION_STOP,
    OPERATION_RESET,
)

#: ``operation -> {permitted from_state: resulting to_state}``. Five operations over the same six
#: edges :data:`PAPER_SESSION_TRANSITIONS` holds - ``stop`` is the one with two sources, which is
#: why the requirement has five operations and the seed six pairs.
#:
#: The operation table and the edge table are kept as two objects rather than one derived from the
#: other, and :func:`_assert_machine_agrees` asserts at import that they describe the same six
#: edges. Deriving one would hide the disagreement that matters: an edge with no operation is an
#: edge nothing can take, and an operation with no edge is a route that would be refused by
#: ``trg_paper_session_guard`` after this module had already accepted it.
OPERATION_TRANSITIONS: Dict[str, Mapping[str, str]] = {
    OPERATION_START: {STATE_CREATED: STATE_RUNNING},
    OPERATION_PAUSE: {STATE_RUNNING: STATE_PAUSED},
    OPERATION_RESUME: {STATE_PAUSED: STATE_RUNNING},
    OPERATION_STOP: {STATE_RUNNING: STATE_STOPPED, STATE_PAUSED: STATE_STOPPED},
    OPERATION_RESET: {STATE_STOPPED: STATE_CREATED},
}

#: Which of the sixteen Paper_Channel types records each operation (Requirement 19.2 fixes the
#: sixteen and admits no seventeenth).
#:
#: **``reset`` is a stated gap, not an oversight.** Requirement 19.2 lists exactly sixteen event
#: types and ``chk_paper_event_type`` enforces the same sixteen, and none of them means "this
#: session was reset". Requirement 17.7 nevertheless requires every accepted operation recorded
#: with the requesting user and the resulting state. Both are satisfied here by recording the
#: reset under ``paper_session_stopped`` with ``session_state = 'CREATED'``: the payload's
#: ``session_state`` field carries the truth - and it exists precisely so "a client that missed a
#: frame must be able to read the current state off the one it did receive" - while the TYPE is
#: the nearest of the sixteen, since a reset session is, like a stopped one, not running.
#: ``final_metrics`` is ``None``, which the payload model documents as "could not be computed"
#: rather than "ended flat".
#:
#: This is recorded as a defect in the requirement pair rather than resolved by inventing a
#: seventeenth type: adding ``paper_session_reset`` would put this module's enum ahead of
#: ``chk_paper_event_type`` and produce a ``23514`` in production, and it would contradict
#: Requirement 19.2's closed list. Resolving it properly needs either a seventeenth type in 19.2
#: (and a migration adding it to the CHECK) or an explicit statement that the reset record shares
#: the stopped type. Neither is task 27.3's to decide.
OPERATION_EVENT_TYPE: Dict[str, PaperEvent] = {
    OPERATION_START: PaperEvent.SESSION_STARTED,
    OPERATION_PAUSE: PaperEvent.SESSION_PAUSED,
    OPERATION_RESUME: PaperEvent.SESSION_RESUMED,
    OPERATION_STOP: PaperEvent.SESSION_STOPPED,
    OPERATION_RESET: PaperEvent.SESSION_STOPPED,
}

#: Which lifecycle timestamp column each operation writes. ``paper_sessions`` carries three -
#: ``started_at``, ``paused_at``, ``stopped_at`` - and no ``resumed_at`` and no ``reset_at``, so
#: ``resume`` and ``reset`` write none: a resume that stamped ``started_at`` again would erase when
#: the session actually began, and inventing a column is not this task's to do. The instant of
#: every operation is nonetheless recorded, because the ``paper_events`` row carries
#: ``emitted_at`` and the payload carries ``at`` - which is where Requirement 17.7's "and a
#: timestamp" is satisfied for all five.
OPERATION_TIMESTAMP_COLUMN: Dict[str, Optional[str]] = {
    OPERATION_START: "started_at",
    OPERATION_PAUSE: "paused_at",
    OPERATION_RESUME: None,
    OPERATION_STOP: "stopped_at",
    OPERATION_RESET: None,
}

#: The four operations a caller may request against an EXISTING session. ``start`` is absent
#: deliberately: it is not a route, it is the tail of :func:`start_session`, and a session that
#: could be "started" a second time from ``CREATED`` would be a session with two feeds and two
#: loops.
CALLER_OPERATIONS: FrozenSet[str] = frozenset(
    {OPERATION_PAUSE, OPERATION_RESUME, OPERATION_STOP, OPERATION_RESET}
)


def _flatten(table: Mapping[str, Any]) -> FrozenSet[Tuple[str, str]]:
    """One adjacency or operation table as a set of ``(from, to)`` pairs."""
    pairs = set()
    for source, targets in table.items():
        if isinstance(targets, Mapping):
            for from_state, to_state in targets.items():
                pairs.add((str(from_state), str(to_state)))
        else:
            for target in targets:
                pairs.add((str(source), str(target)))
    return frozenset(pairs)


def _assert_machine_agrees() -> None:
    """The two tables describe the same six edges, checked at import.

    Cheap, and it fails at the one moment a reader can act on it: an edge added to one table and
    not the other is a machine this module and ``trg_paper_session_guard`` disagree about, and the
    disagreement would surface as a ``23514`` on a user's pause.
    """
    edges = _flatten(PAPER_SESSION_TRANSITIONS)
    operations = _flatten(OPERATION_TRANSITIONS)
    if edges != operations:
        raise RuntimeError(
            "PAPER_SESSION_TRANSITIONS and OPERATION_TRANSITIONS describe different machines; "
            f"only in the edge table: {sorted(edges - operations)}; only in the operation table: "
            f"{sorted(operations - edges)}"
        )
    unknown = {
        state
        for pair in edges
        for state in pair
        if state not in SESSION_STATES
    }
    if unknown:
        raise RuntimeError(
            f"the Paper_Session machine names state(s) {sorted(unknown)} that "
            f"chk_paper_session_state does not admit; the four values are {list(SESSION_STATES)}"
        )
    if set(PAPER_SESSION_TRANSITIONS) != set(SESSION_STATES):
        raise RuntimeError(
            "every one of chk_paper_session_state's four values must be a key of "
            "PAPER_SESSION_TRANSITIONS, so a state with no successor reads as an explicit "
            f"empty tuple rather than as a missing key; got {sorted(PAPER_SESSION_TRANSITIONS)}"
        )
    if set(OPERATION_TRANSITIONS) != set(SESSION_OPERATIONS):
        raise RuntimeError(
            "OPERATION_TRANSITIONS must have one entry per operation of Requirement 17.7; got "
            f"{sorted(OPERATION_TRANSITIONS)} against {sorted(SESSION_OPERATIONS)}"
        )


_assert_machine_agrees()


def normalise_state(value: Any) -> Optional[str]:
    """``value`` as one of the four states, or ``None``.

    ``None`` rather than a raise, following ``paper_order_state.paper_order_state`` and
    ``strategy_lifecycle.normalise_lifecycle_state``: the caller decides whether an unrecognised
    label is a refusal - in :func:`apply_operation` it is - and gets to say what it was in its own
    error body.
    """
    if value is None:
        return None
    label = str(getattr(value, "value", value)).strip().upper()
    return label if label in SESSION_STATES else None


def normalise_operation(value: Any) -> Optional[str]:
    """``value`` as one of the five operations, or ``None``. Same convention as above."""
    if value is None:
        return None
    label = str(getattr(value, "value", value)).strip().lower()
    return label if label in SESSION_OPERATIONS else None


def legal_transitions(state: Any) -> Tuple[str, ...]:
    """Every state ``state`` may legally move to. ``()`` for an unrecognised label."""
    label = normalise_state(state)
    return PAPER_SESSION_TRANSITIONS.get(label, ()) if label else ()


def is_transition_legal(current: Any, target: Any) -> bool:
    """Whether ``current -> target`` is one of 009's six edges."""
    label = normalise_state(target)
    return bool(label) and label in legal_transitions(current)


def target_state(operation: Any, state: Any) -> Optional[str]:
    """The state ``operation`` moves a session in ``state`` to, or ``None`` when it may not.

    The gate, as a value. ``None`` is the whole of "that operation is not permitted from here" -
    an unrecognised operation, an unrecognised state and a permitted operation requested from the
    wrong state all produce it, because all three mean the same thing to a caller and none of them
    may result in a write.
    """
    name = normalise_operation(operation)
    label = normalise_state(state)
    if name is None or label is None:
        return None
    return OPERATION_TRANSITIONS[name].get(label)


def can_operate(operation: Any, state: Any) -> bool:
    """Whether ``operation`` is permitted from ``state``. Requirement 17.7's gate."""
    return target_state(operation, state) is not None


# ══════════════════════════════════════════════════════════════════════════
# THE THREE REFUSALS THIS MODULE MAKES
# ══════════════════════════════════════════════════════════════════════════
#
# Each carries a catalogue code that already exists in ``marketplace/errors.py`` - no new code is
# added, and none of the three chooses a status the catalogue does not permit. The public sentence
# stays the catalogue's in every case; what varies is ``details``, and ``details`` carries only the
# caller's own values and this module's own vocabulary.

#: ``details["validation"]`` values, one per check Requirement 17.4 and 17.5 list, plus the two
#: that belong to the pipeline rather than to a requirement clause. Named constants rather than
#: inline strings for the reason ``PaperMarketMetadataUnavailable`` gives: a caller branches on
#: them and a test asserts on them without matching a sentence.
VALIDATION_ENTITLEMENT = "ENTITLEMENT"
VALIDATION_STRATEGY_NOT_EXECUTABLE = "STRATEGY_NOT_EXECUTABLE"
VALIDATION_CAPITAL_NOT_POSITIVE = "CAPITAL_NOT_POSITIVE"
VALIDATION_CAPITAL_EXCEEDS_MAXIMUM = "CAPITAL_EXCEEDS_MAXIMUM"
VALIDATION_CAPITAL_PRECISION = "CAPITAL_PRECISION_EXCEEDED"
VALIDATION_CURRENCY_NOT_SUPPORTED = "CURRENCY_NOT_SUPPORTED"
VALIDATION_TIMEFRAME_NOT_SUPPORTED = "TIMEFRAME_NOT_SUPPORTED"
VALIDATION_STRATEGY_MARKET_INCOMPATIBLE = "STRATEGY_SYMBOL_TIMEFRAME_INCOMPATIBLE"
VALIDATION_CONFIG_NOT_BUILDABLE = "SESSION_CONFIG_NOT_BUILDABLE"

#: Every value above, so a test can assert that a refusal names one of them and that the pipeline
#: covers each. ``SYMBOL_NOT_LISTED`` / ``MARKET_METADATA_UNAVAILABLE`` /
#: ``MARKET_METADATA_INCOMPLETE`` / ``MARKET_TYPE_UNSUPPORTED`` are deliberately NOT here: they are
#: ``paper_simulator.PaperMarketMetadataUnavailable``'s, raised by the market-metadata lookup this
#: pipeline calls, and re-listing them would be a second vocabulary for one check.
START_VALIDATIONS: Tuple[str, ...] = (
    VALIDATION_ENTITLEMENT,
    VALIDATION_STRATEGY_NOT_EXECUTABLE,
    VALIDATION_CAPITAL_NOT_POSITIVE,
    VALIDATION_CAPITAL_EXCEEDS_MAXIMUM,
    VALIDATION_CAPITAL_PRECISION,
    VALIDATION_CURRENCY_NOT_SUPPORTED,
    VALIDATION_TIMEFRAME_NOT_SUPPORTED,
    VALIDATION_STRATEGY_MARKET_INCOMPATIBLE,
    VALIDATION_CONFIG_NOT_BUILDABLE,
)


class PaperStartRefused(PaperError):
    """``PAPER_START_REFUSED`` - one start validation refused, and nothing was created.

    Requirement 17.13: the refusal names which validation failed, creates no Paper_Session, no
    paper order and no paper balance, leaves every existing Paper_Account unchanged, and opens no
    market-data subscription. The first of those is this class; the rest is
    :func:`start_session`'s ordering.

    ``http_status`` is 409 by default and may be 403 or 422 - the three Requirement 17.13 permits
    and the three ``ALLOWED_HTTP_STATUS_FOR_CODE`` admits for this code. The rule this module
    applies, stated once:

    * **403** for the entitlement refusal, and only for it: the caller is not entitled, which is
      an authorisation answer.
    * **422** for a value in the request that cannot be right at any time - a capital that is not
      positive, that exceeds the maximum, that carries too many decimal places, a currency the
      platform has no minor-unit exponent for, and a timeframe outside the supported set.
    * **409** for everything else, which is the catalogue default and the honest one: the strategy
      is not executable, the plan does not cover this market, the configuration could not be
      built. Each is a state the caller could not have known when they asked.

    ``details`` carries ``validation`` and whichever of the caller's own values make the refusal
    actionable. It never carries the resolved ``version_id``, the ``source_strategy_id`` or any
    part of a plan: a subscriber is entitled to be told their session was refused, not to be told
    the shape of somebody else's strategy (Requirements 19.7, 23.3).
    """

    def __init__(
        self,
        validation: str,
        *,
        http_status: int = 409,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.validation = str(validation)
        merged: Dict[str, Any] = {"validation": self.validation}
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_START_REFUSED, http_status, details=merged)


class PaperSessionLimitReached(PaperError):
    """429 ``PAPER_SESSION_LIMIT_REACHED`` - the per-user concurrent cap, with a reason.

    Requirement 27.4. ``details`` carries the ``limit``, the ``running`` count that met it and a
    ``reason`` sentence naming what the caller can do about it - which is the whole point of a
    429 here rather than a bare refusal: the condition is transient and the caller resolves it by
    stopping a session, so the response says so.

    The status is pinned: ``PAPER_SESSION_LIMIT_REACHED`` is absent from
    ``ALLOWED_HTTP_STATUS_FOR_CODE``, so the catalogue's 429 is the only status this can carry and
    no call site can turn it into a 200 or a 500.
    """

    def __init__(self, *, running: int, limit: int) -> None:
        self.running = int(running)
        self.limit = int(limit)
        super().__init__(
            PAPER_SESSION_LIMIT_REACHED,
            details={
                "limit": self.limit,
                "running": self.running,
                "reason": (
                    f"{self.running} of your paper sessions are already running and this "
                    f"deployment permits {self.limit}. Stop one before starting another; "
                    f"a stopped session keeps its history and can be reset and started again."
                ),
            },
        )


class PaperSessionOperationRejected(PaperError):
    """409 ``PAPER_SESSION_OPERATION_REJECTED`` - naming BOTH the state and the operation.

    Requirement 17.14, exactly: the operation is rejected, the session's state, orders, positions,
    balances and persisted history are unchanged, and the error indicates the current state **and**
    the rejected operation. ``details`` carries ``session_state``, ``operation`` and
    ``permitted_from`` - the states that operation IS permitted from - because a caller who asked
    for a resume on a stopped session needs to know that resume comes from ``PAUSED``, and that is
    this module's own vocabulary rather than anybody's data.

    "Changes nothing" is not asserted by this class; it is a property of where it is raised. The
    gate runs before any statement is issued, so there is no write to undo - which matters,
    because there is no ``ROLLBACK`` over this transport to undo one with.

    The status is pinned to the catalogue's 409.
    """

    def __init__(self, *, session_state: Any, operation: Any) -> None:
        self.session_state = str(session_state)
        self.operation = str(operation)
        name = normalise_operation(operation)
        permitted = sorted(OPERATION_TRANSITIONS[name]) if name else []
        super().__init__(
            PAPER_SESSION_OPERATION_REJECTED,
            details={
                "session_state": self.session_state,
                "operation": self.operation,
                "permitted_from": permitted,
            },
        )


def _session_not_found(session_id: Any) -> PaperError:
    """The ONE answer for "no such session" and "another user's session" (Requirement 21.4).

    Built in one place and from one value - the identifier the caller themselves supplied - so the
    two cases cannot be told apart by status, by body or by which branch produced it. There is
    deliberately no ``owner``, no ``exists`` flag and no distinct code: a caller probing another
    tenant's identifier learns exactly what they would learn probing a random one.
    """
    return PaperError(NOT_FOUND, details={"session_id": str(session_id)})


#: The same builder under a public name, for the ONE caller that needs it from outside this module:
#: the route layer's session reads (task 28.1). ``GET /api/paper/sessions/{id}`` reads through
#: ``paper_repository.read_session_summary`` rather than through :func:`apply_operation`, so it has
#: to raise this refusal itself - and spelling a second ``NOT_FOUND`` beside this one is exactly how
#: the two cases Requirement 21.4 requires to be byte-identical would drift apart. One construction
#: site, two names, no copy.
session_not_found = _session_not_found


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION 3 - THE VERSION RESOLVE AND ITS LIFECYCLE CHECK (Req 17.4)
# ══════════════════════════════════════════════════════════════════════════

#: ``strategy_versions``, the projection this module reads. Explicit, never ``select("*")``, for
#: the same reason ``paper_repository``'s projections are.
#:
#: ``compiled_plan`` is here because it carries ``action_symbols`` and ``action_timeframes`` - the
#: markets the compiler RESOLVED for this version's actions - which is what
#: :func:`assert_strategy_supports_market` compares against. It is read and immediately reduced to
#: two sets of strings; no part of it reaches a response, a log line or an event payload
#: (Requirements 19.7, 23.3).
STRATEGY_VERSIONS_TABLE = "strategy_versions"
STRATEGY_VERSION_SELECT = "id,strategy_id,lifecycle_state,is_draft,compiled_plan"

#: The version ``lifecycle_state`` values from which a Paper_Session may execute.
#:
#: ``chk_lifecycle_state`` (migration 004 part 1) admits eleven values;
#: ``strategy_builder.LIFECYCLE_READY`` is the one the live deploy gate asserts, and a version that
#: is already ``DEPLOYED``, ``RUNNING`` or ``PAUSED`` passed that gate to get there. ``DRAFT``,
#: ``VALIDATED``, ``SAVED``, ``TRAINING`` and ``TRAINED`` have not; ``STOPPED`` and ``ARCHIVED``
#: are past it - and Requirement 3.3 makes an archived strategy undeployable outright.
#:
#: Spelled here rather than imported because ``strategy_lifecycle`` reaches
#: ``deployment_binding``, ``strategy_builder`` and ``core.audit_trail`` at module scope, and
#: ``tests/test_paper_no_random.py`` pins this package's first-party dependency list precisely so
#: that graph is not widened without an argument. The agreement is kept by a test instead: it
#: imports both freely and asserts this tuple against ``strategy_lifecycle``'s constants and
#: against ``strategy_builder.LIFECYCLE_STATES``.
DEPLOYABLE_LIFECYCLE_STATES: Tuple[str, ...] = (
    "READY",
    "DEPLOYED",
    "RUNNING",
    "PAUSED",
)


def _rows(response: Any, what: str) -> Tuple[Mapping[str, Any], ...]:
    """The rows of a PostgREST response, or ``()``. Raises when the response signals an error.

    The reading ``paper_repository`` establishes, applied to the one non-``paper_*`` table this
    module reads: ``()`` is an answer, returned only when the statement completed and matched
    nothing. A driver exception or an ``error`` envelope is a failure and is reported as one -
    answering "no such version" for a read that did not complete would refuse a start that should
    have been admitted, and it would name the strategy as the cause.
    """
    if response is None:
        raise PaperError(
            PAPER_START_REFUSED,
            details={
                "validation": VALIDATION_STRATEGY_NOT_EXECUTABLE,
                "reason": "STRATEGY_VERSION_READ_DID_NOT_COMPLETE",
            },
        )
    if isinstance(response, Mapping):
        if response.get("error"):
            raise paper_read_failed({"operation": what})
        data = response.get("data")
    else:
        if getattr(response, "error", None):
            raise paper_read_failed({"operation": what})
        data = getattr(response, "data", None)
    if data is None:
        return ()
    if isinstance(data, Mapping):
        return (data,)
    return tuple(row for row in data if isinstance(row, Mapping))


def read_strategy_version(supabase: Any, version_id: Any) -> Optional[Mapping[str, Any]]:
    """One ``strategy_versions`` row by id, or ``None`` when the read matched nothing.

    **Why this read carries no caller predicate, and why that is not a weakening.** The version
    behind a Listing belongs to its AUTHOR, not to the subscriber starting a paper session, so a
    ``user_id`` predicate here would match nothing for every subscriber and the lifecycle check
    would be unimplementable. The authorisation for reaching this row has already happened: it is
    :func:`~entitlement_resolver.resolve`'s entitling verdict, and ``version_id`` is a value that
    decision produced server-side - never one a caller supplied. Nothing off the row reaches the
    caller: the lifecycle state becomes an admit-or-refuse, and ``compiled_plan`` becomes two sets
    of strings that are compared and discarded. This is the same shape, and the same argument,
    ``paper_repository.read_session_owner`` documents for the one unscoped read it makes.

    Raises:
        PaperError: ``PAPER_READ_FAILED`` when the statement did not complete.
    """
    key = str(version_id).strip()
    if not key:
        return None
    response = (
        supabase.table(STRATEGY_VERSIONS_TABLE)
        .select(STRATEGY_VERSION_SELECT)
        .eq("id", key)
        .limit(1)
        .execute()
    )
    for row in _rows(response, f"{STRATEGY_VERSIONS_TABLE} read"):
        if str(row.get("id")) == key:
            return row
    return None


def assert_version_deployable(version_row: Optional[Mapping[str, Any]]) -> str:
    """The version's ``lifecycle_state``, or refuse. Requirement 17.4's status check.

    A missing row, a draft, an absent ``lifecycle_state`` and a state outside
    :data:`DEPLOYABLE_LIFECYCLE_STATES` are all one refusal -
    ``STRATEGY_NOT_EXECUTABLE`` - and deliberately so: each of the four means the platform cannot
    say this version is executable right now, and telling a subscriber WHICH of the four would
    describe the author's work in progress.

    An absent ``lifecycle_state`` is refused rather than admitted. That is the safe direction and
    the same one ``strategy_lifecycle.edit_disposition`` takes for the same column: a database
    where migration 004 part 1 is unapplied has no lifecycle at all, and admitting a session
    against an unknown state would make Requirement 17.4's check read as passed while never having
    run.
    """
    if version_row is None:
        raise PaperStartRefused(VALIDATION_STRATEGY_NOT_EXECUTABLE)
    if version_row.get("is_draft"):
        raise PaperStartRefused(VALIDATION_STRATEGY_NOT_EXECUTABLE)
    raw = version_row.get("lifecycle_state")
    state = str(raw).strip().upper() if raw is not None else ""
    if state not in DEPLOYABLE_LIFECYCLE_STATES:
        raise PaperStartRefused(VALIDATION_STRATEGY_NOT_EXECUTABLE)
    return state


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION 4 - THE CAPITAL (Requirement 17.5)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ValidatedCapital:
    """One accepted initial simulated capital, in both the forms storage needs.

    ``paper_sessions.initial_capital_minor`` is a ``BIGINT`` of Minor_Units and
    ``paper_accounts.initial_capital`` / ``available_balance`` / ``total_equity`` are
    ``NUMERIC(28,10)`` major-unit decimals. Both are derived here, once, from the same validated
    integer - which is what makes "the account's balances agree with its session's recorded
    capital" arithmetic done before any write rather than a hope about two statements.
    """

    #: The exact whole number of Minor_Units. What the session row stores.
    minor: int
    #: The same amount in major units, exact. What the account row stores.
    major: Decimal
    #: The currency's Minor_Units exponent, from the platform's persisted table.
    exponent: int
    #: The currency, upper-cased.
    currency: str


def validate_capital(
    initial_capital_minor: Any,
    currency: Any,
    *,
    maximum_major: Any = None,
) -> ValidatedCapital:
    """Requirement 17.5, as a pure function. Refuses; never adjusts.

    Four checks, and each has its own ``details["validation"]`` so a caller is told which one:

    * the currency must be one the platform holds a Minor_Units exponent for. Read from
      ``marketplace.money.minor_unit_exponent`` - the one persisted table - and never assumed to
      be 2: every quantized money figure in the session depends on it (Requirement 18.2).
    * the amount must be an EXACT whole number of Minor_Units. A ``float`` is refused outright and
      a ``Decimal`` carrying a fraction of a minor unit is refused as
      ``CAPITAL_PRECISION_EXCEEDED`` - which is Requirement 17.5's "carries more decimal places
      than the currency's Minor_Units precision", expressed against the unit the column actually
      stores. ``100.5`` cents is not a rounding matter; it is an amount no ledger can hold.
    * greater than zero. ``chk_paper_capital`` says the same thing in the database; this says it
      first, so the caller gets a named refusal instead of a ``23514``.
    * at or below the configured per-session maximum, compared in MAJOR units because that is the
      unit Requirement 17.5 states the maximum in.

    Returns:
        A :class:`ValidatedCapital` carrying the amount in both units and the exponent that
        relates them.

    Raises:
        PaperStartRefused: 422, for each of the four. 422 and not 409 because every one of them is
            a property of the value the caller sent, which cannot become correct later.
    """
    label = str(currency or "").strip().upper()
    try:
        exponent = minor_unit_exponent(label)
    except UnsupportedCurrency:
        raise PaperStartRefused(
            VALIDATION_CURRENCY_NOT_SUPPORTED,
            http_status=422,
            details={"currency": label},
        ) from None

    if isinstance(initial_capital_minor, float):
        raise PaperStartRefused(
            VALIDATION_CAPITAL_PRECISION,
            http_status=422,
            details={"currency": label, "minor_unit_exponent": exponent},
        )
    try:
        amount = (
            initial_capital_minor
            if isinstance(initial_capital_minor, Decimal)
            else Decimal(initial_capital_minor)
        )
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        raise PaperStartRefused(
            VALIDATION_CAPITAL_PRECISION,
            http_status=422,
            details={"currency": label, "minor_unit_exponent": exponent},
        ) from None

    if amount != amount.to_integral_value():
        raise PaperStartRefused(
            VALIDATION_CAPITAL_PRECISION,
            http_status=422,
            details={"currency": label, "minor_unit_exponent": exponent},
        )
    minor = int(amount)

    if minor <= 0:
        raise PaperStartRefused(
            VALIDATION_CAPITAL_NOT_POSITIVE,
            http_status=422,
            details={"currency": label},
        )

    maximum = resolve_capital_maximum_major(maximum_major)
    major = (Decimal(minor).scaleb(-exponent)).normalize()
    if major > maximum:
        raise PaperStartRefused(
            VALIDATION_CAPITAL_EXCEEDS_MAXIMUM,
            http_status=422,
            details={
                "currency": label,
                "maximum_major_units": str(maximum),
                "minor_unit_exponent": exponent,
            },
        )

    return ValidatedCapital(
        minor=minor, major=major, exponent=exponent, currency=label
    )


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION 6 - THE TIMEFRAME (Requirement 17.4)
# ══════════════════════════════════════════════════════════════════════════


def validate_timeframe(timeframe: Any) -> str:
    """``timeframe`` as a supported label, or refuse.

    The supported set is the PLATFORM's, consulted through
    ``market_data_contract.interval_for`` - which reads
    ``market_data_validation.TIMEFRAME_MINUTES``, the same table ``/registry/timeframes``
    intersects and the same one the live deploy path's ``assert_timeframe_supported`` starts from.
    There is deliberately no second table here: a label this module admitted and the pipeline
    could not resample would be a session whose bars never arrive, reported as healthy.

    Raises:
        PaperStartRefused: 422 ``TIMEFRAME_NOT_SUPPORTED``, carrying the label the caller sent and
            the supported set the contract itself named - which is the caller's own value plus a
            platform vocabulary, and nothing else.
    """
    label = str(timeframe or "").strip()
    if not label:
        raise PaperStartRefused(
            VALIDATION_TIMEFRAME_NOT_SUPPORTED,
            http_status=422,
            details={"timeframe": label},
        )
    try:
        interval_for(label)
    except MarketDataContractError as exc:
        supported = exc.details.get("supported")
        details: Dict[str, Any] = {"timeframe": label}
        if isinstance(supported, (list, tuple)):
            details["supported_timeframes"] = sorted(str(item) for item in supported)
        raise PaperStartRefused(
            VALIDATION_TIMEFRAME_NOT_SUPPORTED, http_status=422, details=details
        ) from None
    return label


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION 7 - STRATEGY / SYMBOL / TIMEFRAME COMPATIBILITY (Req 17.4)
# ══════════════════════════════════════════════════════════════════════════


def plan_markets(version_row: Optional[Mapping[str, Any]]) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """``(symbols, timeframes)`` the version's compiled plan resolved for its ACTION nodes.

    Read straight off the stored ``strategy_versions.compiled_plan`` JSONB, whose
    ``action_symbols`` and ``action_timeframes`` members ``strategy_dag.plan`` writes precisely so
    that "re-deriving the traded market at execution time" is not put in every consumer - its own
    words. Reading the two maps therefore introduces no second resolution rule and pulls no part of
    the DAG runtime into this package's import graph.

    An empty set on either side means **unknown**, not "no markets", and the two are unknown for
    different reasons:

    * ``action_symbols`` is complete for any plan compiled by 2.1.0 or later; it is empty only for
      a plan stamped 2.0.0, which did not resolve it.
    * ``action_timeframes`` is DELIBERATELY partial even at 2.1.0 -
      ``resolve_action_markets``' docstring is explicit that an action whose closure gives no
      single timeframe has no entry, because a 5m entry filtered by a 1h trend is a legitimate
      strategy. Its own instruction to consumers is to "treat it as unknown rather than substitute
      one".

    :func:`assert_strategy_supports_market` acts on that distinction rather than flattening it.
    """
    if version_row is None:
        return frozenset(), frozenset()
    plan = version_row.get("compiled_plan")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (TypeError, ValueError):
            return frozenset(), frozenset()
    if not isinstance(plan, Mapping):
        return frozenset(), frozenset()

    def _values(key: str) -> FrozenSet[str]:
        raw = plan.get(key)
        if not isinstance(raw, Mapping):
            return frozenset()
        return frozenset(
            str(value).strip()
            for value in raw.values()
            if isinstance(value, str) and value.strip()
        )

    return _values("action_symbols"), _values("action_timeframes")


def assert_strategy_supports_market(
    version_row: Optional[Mapping[str, Any]],
    *,
    symbol: Any,
    timeframe: Any,
) -> None:
    """Refuse a session whose symbol or timeframe the version's plan does not trade.

    Requirement 17.4's last validation: "the strategy's compatibility with the selected symbol and
    timeframe". Compared against :func:`plan_markets`, with the asymmetry that function documents:

    * a NON-EMPTY resolved symbol set that does not contain ``symbol`` is a refusal. The plan says
      which market each of its actions trades, and a session on another market would evaluate the
      strategy against bars it was never wired to.
    * a NON-EMPTY resolved timeframe set that does not contain ``timeframe`` is a refusal, for the
      same reason.
    * an EMPTY set on either side ADMITS. That is not laxity, it is the honest reading of a value
      whose own producer defines absence as "no single answer" - refusing on it would reject the
      multi-timeframe strategies the platform is meant to support, and substituting a default would
      be the invented ``"1m"`` that ``resolve_action_markets`` exists to delete. The consequence is
      stated rather than hidden: a plan compiled by 2.0.0, and an action whose closure spans two
      timeframes, are admitted on this check and constrained only by the market-metadata and
      timeframe validations above.

    Raises:
        PaperStartRefused: 409 ``STRATEGY_SYMBOL_TIMEFRAME_INCOMPATIBLE``, carrying only the
            caller's own ``symbol`` and ``timeframe``. The plan's resolved sets are NOT in
            ``details``: they are the strategy's structure, and a subscriber refused a session must
            not learn which markets somebody else's strategy trades (Requirements 19.7, 23.3).
    """
    wanted_symbol = str(symbol or "").strip()
    wanted_timeframe = str(timeframe or "").strip()
    symbols, timeframes = plan_markets(version_row)

    if symbols and wanted_symbol not in symbols:
        raise PaperStartRefused(
            VALIDATION_STRATEGY_MARKET_INCOMPATIBLE,
            details={"symbol": wanted_symbol, "timeframe": wanted_timeframe},
        )
    if timeframes and wanted_timeframe not in timeframes:
        raise PaperStartRefused(
            VALIDATION_STRATEGY_MARKET_INCOMPATIBLE,
            details={"symbol": wanted_symbol, "timeframe": wanted_timeframe},
        )


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION 8 - THE PER-USER CONCURRENT CAP (Requirement 27.4)
# ══════════════════════════════════════════════════════════════════════════


def assert_within_session_limit(
    supabase: Any, user_id: Any, *, limit: Any = None
) -> int:
    """Refuse when the caller already has ``limit`` sessions ``RUNNING``. Returns the count.

    One round trip, through :func:`~paper_repository.count_running_sessions`, whose predicates are
    exactly ``idx_paper_sessions_running``'s - so the answer is one index scan over the user's
    running sessions rather than a walk of their whole session history.

    Checked LAST of the validations and BEFORE the first INSERT. Both halves matter: last, because
    it is the only validation that is a fact about the caller's other sessions rather than about
    this request, and telling somebody they are at their limit for a request that was inadmissible
    anyway is a worse answer; before the INSERT, because a cap enforced after the session row
    exists is not a cap.

    A read that did not complete raises out of ``count_running_sessions`` rather than being read as
    zero, so the cap cannot be bypassed by a broken read.
    """
    ceiling = resolve_concurrency_limit(limit)
    running = repo.count_running_sessions(supabase, user_id)
    if running >= ceiling:
        logger.info(
            "[paper-session] refusing start: %s running sessions, cap %s (Requirement 27.4)",
            running,
            ceiling,
        )
        raise PaperSessionLimitReached(running=running, limit=ceiling)
    return running


# ══════════════════════════════════════════════════════════════════════════
# EMISSION - RECORD FIRST, THEN BROADCAST (Requirements 17.7, 19.3)
# ══════════════════════════════════════════════════════════════════════════


def config_digest(config: Any) -> str:
    """``sha256`` of the frozen configuration, hex - what ``paper_session_started`` carries.

    A digest and NOT the configuration, because ``PaperSessionStartedPayload`` says so and says
    why: the frozen ``config`` carries fee rates, slippage and precision, and a subscriber
    watching somebody else's shared strategy has no business reading them (Requirement 19.7). A
    digest lets a client tell one configuration from another without being told what it is.

    Computed over the canonical JSON of the stored payload - sorted keys, no whitespace - so the
    same configuration digests identically in this process, in another instance and after a
    restart. ``hashlib`` and not a ``hash()``: Python's built-in hash is salted per process and
    would produce a different digest for the same configuration on every restart.
    """
    payload = config.to_jsonb() if hasattr(config, "to_jsonb") else config
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _emit(
    supabase: Any,
    *,
    user_id: Any,
    session_id: Any,
    event_type: PaperEvent,
    payload: Mapping[str, Any],
    emitted_at: Any,
) -> Any:
    """Record one frame in ``paper_events``, then broadcast it. In that order, always.

    ``record_event`` allocates the sequence against ``paper_sessions.event_sequence`` and appends
    the row; only then is the frame put on the channel. The order is not interchangeable: a frame
    delivered before it is recorded is a frame a reconnecting client cannot replay
    (Requirement 19.8), and it would carry a sequence this process chose rather than one the
    database issued.

    A broadcast failure is logged and swallowed; a RECORD failure is not. The asymmetry is the
    point: the ``paper_events`` row is the persisted history Requirement 17.1 serves and
    Requirement 17.7 requires the operation recorded in, so a failure to write it must reach the
    caller. Delivery to whoever happens to be watching is best-effort by construction - the client
    replays from the row - and failing an accepted state transition because a socket was slow would
    report a transition that DID happen as one that did not.
    """
    envelope = record_event(
        supabase,
        user_id=user_id,
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        emitted_at=emitted_at,
    )
    try:
        await broadcast(
            session_id, envelope.model_dump(mode="json"), supabase=supabase
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning(
            "[paper-session] %s for session %s was recorded at sequence %s but not delivered "
            "(%s); a reconnecting client replays it from paper_events",
            envelope.type.value,
            session_id,
            envelope.sequence,
            exc,
        )
    return envelope


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION LOOP SEAM (task 27.2 owns the body)
# ══════════════════════════════════════════════════════════════════════════

#: What :func:`start_session` calls to start the per-session loop:
#: ``spawn(session_row, feed_handle, *, config, account_id) -> Any``, optionally a coroutine.
#:
#: **Why the two keyword arguments are part of the seam.** The loop cannot step without the frozen
#: :class:`~paper_simulator.SessionConfig` and the session's own Paper_Account, and BOTH are
#: created inside :func:`start_session` - the config several validations deep, the account by the
#: second statement of the create block - which is strictly after the point at which ``spawn_loop``
#: was handed in. A two-argument seam therefore forced the installing layer either to re-derive the
#: frozen configuration (a second implementation of Requirement 16.12, and one that could disagree
#: with the row) or to install no loop at all. So the pipeline SUPPLIES the two values it created,
#: at the moment it created them, and the installing layer closes over only what it owns: the DAG
#: runtime, the plan and the Signal_Trace recorder.
#:
#: ``Callable[..., Any]`` rather than a spelled-out parameter list because ``typing.Callable``
#: cannot express keyword-only parameters; the shape above is the contract, and
#: :func:`spawn_session_loop` is its one production implementation.
SessionLoopSpawner = Callable[..., Any]


async def no_session_loop(
    session: Mapping[str, Any],
    feed: FeedHandle,
    *,
    config: Optional[SessionConfig] = None,
    account_id: Any = None,
) -> None:
    """The default ``spawn_loop``: log loudly that no loop is installed, and return.

    The loop itself landed with task 27.2 and is :func:`session_loop`; what is still a caller's
    decision is whether one is INSTALLED, and this is what a caller that installed none gets.

    It stays the default rather than being replaced by :func:`spawn_session_loop` because THIS
    MODULE has no strategy runtime to install one with: it may not import the platform's DAG
    execution path (Requirement 17.10) or its Signal_Trace recorder (Requirement 23.5), so the only
    loop it could default to is one with ``evaluate=None`` - a session that drains the feed and
    emits ticks while producing no signal and no order, which is a session that looks alive and
    evaluates nothing. The wiring is :func:`spawn_session_loop`'s, performed by the layer that owns
    the runtime, and its absence is reported here rather than papered over.

    ``config`` and ``account_id`` are accepted and unused: this default does nothing with them, and
    declaring them is what keeps it substitutable for a real spawner rather than a narrower shape
    ``_spawn`` would have to call differently.

    It deliberately does NOT pass quietly. A session reported ``RUNNING`` with nothing draining its
    feed is a session that will never produce a bar, an order or an equity point, and the only
    person who can tell is the operator reading this line: the API would report ``RUNNING``, the
    feed would report ``HEALTHY``, and every figure would be a truthful zero (Requirement 28.5's
    distinction between "no trades" and "not measured" is exactly what would be lost).
    """
    logger.error(
        "[paper-session] session %s is RUNNING with feed %s but NO session loop is installed. "
        "Nothing will consume its market data, so it will produce no bar, no signal, no order and "
        "no equity point. Build one with spawn_session_loop(...) and pass it as spawn_loop= to "
        "start_session.",
        session.get("id"),
        getattr(feed, "channel", None),
    )
    return None


async def _spawn(
    spawn_loop: Optional[SessionLoopSpawner],
    session: Mapping[str, Any],
    feed: FeedHandle,
    *,
    config: SessionConfig,
    account_id: Any,
) -> Any:
    """Call the loop seam, awaiting it when it returns an awaitable. Returns what it produced.

    Accepts both shapes because both are legitimate: 27.2's spawner is expected to create an
    ``asyncio`` task and return it synchronously, while a test's is often a coroutine function.
    Whatever it produced is RETURNED rather than discarded, because for the production spawner it
    is the loop ``asyncio.Task`` and :func:`stop_session` takes that task as ``loop_task=`` and
    settles it before it commits a closing figure (Requirement 17.8, step 3). Discarding it here
    would leave the only handle on a running loop in a local variable that goes out of scope.

    ``config`` and ``account_id`` are the two values the loop cannot step without and that the
    installing layer could not have had: see :data:`SessionLoopSpawner`.

    A failure here is logged and swallowed - the session IS ``RUNNING``, the transition IS
    committed and the ``paper_session_started`` frame IS recorded, so raising would report a
    started session as unstarted and leave the caller with no identifier for the session that
    exists. The log line names the session, which is what a caller needs to stop it.
    """
    spawner = spawn_loop or no_session_loop
    try:
        outcome = spawner(session, feed, config=config, account_id=account_id)
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "[paper-session] the session loop for %s could not be started (%s); the session is "
            "RUNNING and its feed is open, so stop it rather than leaving it",
            session.get("id"),
            exc,
        )
        return None


# ══════════════════════════════════════════════════════════════════════════
# THE PIPELINE (task 27.1 - Requirements 17.4, 17.5, 17.6, 17.9, 17.13, 27.4)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StartedSession:
    """What :func:`start_session` returns: the row, its account, its config and its feed.

    A value and not a dict, so a caller cannot mistake the account for the session or read a
    field that is not there. ``config`` is the in-memory :class:`~paper_simulator.SessionConfig`
    the row stores - the same object, frozen - so the loop does not have to read it back.
    """

    session: Mapping[str, Any]
    account: Mapping[str, Any]
    config: SessionConfig
    feed: FeedHandle
    #: The entitlement decision that admitted the start. Carried so a caller can record WHY it was
    #: admitted without resolving it a second time.
    entitlement: Entitlement
    #: Whatever the installed ``spawn_loop`` produced - the loop ``asyncio.Task`` for
    #: :func:`spawn_session_loop`, and ``None`` for :func:`no_session_loop`. Carried out to the
    #: caller because :func:`stop_session` takes it as ``loop_task=`` and settles it before it
    #: commits a closing figure (Requirement 17.8, step 3): this module deliberately keeps no
    #: registry of running loops, so the layer that started the session is the layer that holds
    #: the task. ``Any`` and not ``asyncio.Task`` because the seam permits any spawner.
    loop_task: Any = None

    @property
    def session_id(self) -> str:
        return str(self.session.get("id"))


async def start_session(
    supabase: Any,
    caller: Any,
    *,
    listing_id: Any,
    exchange_id: Any,
    symbol: Any,
    timeframe: Any,
    initial_capital_minor: Any,
    currency: Any = repo.DEFAULT_CURRENCY,
    now: Optional[datetime] = None,
    markets: Any = None,
    exchange: Any = None,
    redis: Any = None,
    measurements: Optional[Mapping[str, Any]] = None,
    concurrency_limit: Any = None,
    capital_maximum_major: Any = None,
    precision_mode: Optional[str] = None,
    simulator: Any = DEFAULT_SIMULATOR,
    spawn_loop: Optional[SessionLoopSpawner] = None,
) -> StartedSession:
    """The Requirement 17.9 pipeline, in order. Validate everything, then create, then run.

    Read the module docstring's "THE ORDER IS THE CONTRACT" and "WHAT ``ONE TRANSACTION`` MEANS
    OVER THIS TRANSPORT" sections first: the sequence below is the whole of Requirement 17.13, and
    the create block is three requests rather than a transaction.

    Args:
        caller: the authenticated server-side identity. Only its ``id`` is read. No user, tenant
            or owner identifier from a request participates in authorisation (Requirement 21.1) -
            the route layer resolves the identity and passes it here.
        listing_id: the ``library_strategies.id`` the session runs. It is the only strategy
            reference this function takes, because :func:`~entitlement_resolver.resolve` is keyed
            on it and because the ``source_strategy_id`` and ``version_id`` the session records are
            values that DECISION produces, never values a caller supplies.
        exchange_id: the venue whose market metadata the symbol is validated against and whose
            candles the feed subscribes to.
        initial_capital_minor: the starting capital, as an exact whole number of Minor_Units.
        currency: the Paper_Account's currency. Defaults to ``paper_repository.DEFAULT_CURRENCY``,
            which is what the existing ``/api/paper/*`` account uses.
        now: the instant to resolve the entitlement's expiry against and to stamp the session with.
            Passed in so the whole pipeline is deterministic (Requirement 11.7, property P-11);
            defaults to :func:`~paper_events.utc_now`.
        markets: the venue's market map. ``None`` loads it through
            ``asset_universe.load_exchange_markets`` - the platform's existing reader, which
            already refuses the DEV_MODE mock market map. Injectable so the validation is testable
            without a connection.
        exchange: the resolved exchange connection, passed straight to
            :func:`~paper_market_feed.open_feed`. ``None`` is a legitimate answer - Requirement
            15.3 lets a session start for a user holding no exchange credentials - and ``None`` is
            not mocked.
        measurements: the recorded market-data measurements, keyed by source identity. Nothing is
            invented for a missing one: an unmeasured candidate fails the correctness floor and the
            start is refused (Requirement 14.4).
        simulator: the simulator whose identity is checked and recorded. Defaults to
            :data:`DEFAULT_SIMULATOR`, this package's own; a forbidden one is refused before
            anything is created (Requirement 13.11).
        spawn_loop: task 27.2's seam. See :func:`no_session_loop`.

    Returns:
        A :class:`StartedSession`. The session row is at ``RUNNING`` and its
        ``paper_session_started`` frame is recorded.

    Raises:
        PaperStartRefused: 403/409/422, naming the validation. Nothing was created.
        PaperMarketMetadataUnavailable: 409 ``PAPER_START_REFUSED`` from the metadata lookup,
            naming ``SYMBOL_NOT_LISTED`` / ``MARKET_METADATA_UNAVAILABLE`` /
            ``MARKET_METADATA_INCOMPLETE`` / ``MARKET_TYPE_UNSUPPORTED``. Nothing was created.
        PaperSimulatorMisconfigured: 500. Nothing was created.
        PaperSessionLimitReached: 429 with a reason. Nothing was created.
        PaperMarketDataUnavailable: 409. For the correctness-floor block, nothing was created (see
            the module docstring's feed section); for the mock-interface refusal, the session
            exists at ``CREATED`` with no subscription.
        PaperError: 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied,
            ``PAPER_READ_FAILED`` when a read this pipeline needs did not complete.
    """
    instant = now or utc_now()
    caller_id = str(getattr(caller, "id", None) or (caller or {}).get("id") or "").strip()
    if not caller_id:
        # Not a 403 and not a refusal naming a validation: an unauthenticated request must be
        # refused before any data access, and the route layer is what returns 401 (Requirement
        # 21.6). Reaching here with no identity is a programming error in the caller.
        raise ValueError(
            "start_session requires the authenticated server-side identity; no identifier from a "
            "request body, query or path may stand in for it (Requirement 21.1)"
        )

    # ── 1. THE SINGLE ADMISSION DECISION (Requirements 17.4, 11.10, property P-16) ──
    try:
        entitlement = await resolve_entitlement(caller, listing_id, supabase, instant)
    except EntitlementReadFailed as exc:
        # A read the decision needed did not complete, so there IS no decision. Reported as a
        # failure rather than as "not entitled": refusing a paying subscriber because a read
        # timed out would be indistinguishable to them from an expired subscription.
        logger.error(
            "[paper-session] the entitlement decision for listing %s did not complete: %s",
            listing_id,
            exc,
        )
        raise paper_read_failed({"operation": "entitlement_resolve"}) from exc
    if not entitlement.entitling:
        logger.info(
            "[paper-session] refusing start for listing %s: %s",
            listing_id,
            entitlement.reason.value,
        )
        raise PaperStartRefused(
            VALIDATION_ENTITLEMENT,
            http_status=403,
            details={
                "listing_id": str(listing_id),
                # The resolver's own reason, which is the caller's own situation - OWNED,
                # SUBSCRIBED, NOT_SUBSCRIBED, EXPIRED, LISTING_UNAVAILABLE,
                # SUBSCRIPTION_SUSPENDED - and is what Requirement 7.10 makes distinct on the
                # wire. Nothing about the Listing's internals travels with it.
                "reason": entitlement.reason.value,
            },
        )

    # ── 2. THE SIMULATOR GUARD (Requirement 13.11) ──
    # Before the version resolve, because a server wired to a random-number generator must refuse
    # every start regardless of which strategy was asked for.
    simulator_module, simulator_qualname = assert_paper_simulator(simulator)

    # ── 3. THE VERSION RESOLVE AND ITS LIFECYCLE CHECK (Requirement 17.4) ──
    # The version is the one the ADMISSION DECISION named, read back for its lifecycle rather than
    # re-resolved: two resolutions could disagree, and the one that authorised the start is the one
    # whose executability matters.
    version_row = read_strategy_version(supabase, entitlement.version_id)
    assert_version_deployable(version_row)

    # ── 4. THE CAPITAL (Requirement 17.5) ──
    capital = validate_capital(
        initial_capital_minor, currency, maximum_major=capital_maximum_major
    )

    # ── 5. THE EXCHANGE MARKET METADATA (Requirement 17.4) ──
    market_map = markets if markets is not None else await load_exchange_markets(str(exchange_id))
    metadata = resolve_market_metadata(
        market_map,
        exchange_id=exchange_id,
        symbol=symbol,
        precision_mode=precision_mode,
    )

    # ── 6. THE TIMEFRAME (Requirement 17.4) ──
    frame = validate_timeframe(timeframe)

    # ── 7. STRATEGY / SYMBOL / TIMEFRAME COMPATIBILITY (Requirement 17.4) ──
    assert_strategy_supports_market(version_row, symbol=metadata.symbol, timeframe=frame)

    # ── 8. THE PER-USER CONCURRENT CAP (Requirement 27.4) ──
    assert_within_session_limit(supabase, caller_id, limit=concurrency_limit)

    # ── The market-data source identity, needed BEFORE the insert ──
    # ``paper_sessions.market_data_source`` is NOT NULL and the frozen config records the same
    # identity, so the selection has to be known here. This is the pure half of the rule
    # ``open_feed`` runs, and its docstring exists for exactly this call. A blocked selection is
    # refused now, with nothing created - there is nothing honest to write in that column for a
    # deployment whose feed is inadmissible.
    decision = select_market_data_source(measurements)
    if decision.blocked:
        logger.error(
            "[paper-session] refusing start before creating anything: %s - %s",
            decision.rule,
            decision.reason,
        )
        raise PaperMarketDataUnavailable(decision.rule, decision.reason)
    market_data_source = str(decision.selected)

    # ── The frozen configuration (Requirement 16.12) ──
    try:
        config = freeze_session_config(
            metadata=metadata,
            currency=capital.currency,
            market_data_source=market_data_source,
            simulator=simulator,
        )
    except InvalidSessionConfig as exc:
        # Every input has been validated above, so this is a configuration the platform cannot
        # build rather than a value the caller got wrong - hence 409 and no echo of a rate or a
        # precision. Still refused before any create.
        logger.error("[paper-session] the session configuration could not be built: %s", exc)
        raise PaperStartRefused(VALIDATION_CONFIG_NOT_BUILDABLE) from exc

    if f"{simulator_module}.{simulator_qualname}" != config.simulator:
        # The identity the guard admitted and the identity the config recorded must be the same
        # string. They are derived from the same argument, so a disagreement is a bug in this
        # module rather than a caller's problem - and it is checked because the recorded identity is
        # what a replay and an audit read (Requirement 15.6).
        raise RuntimeError(
            f"the admitted simulator {simulator_module}.{simulator_qualname} is not the one the "
            f"frozen config recorded ({config.simulator}); nothing has been created"
        )

    # ══════════════════════════════════════════════════════════════════════
    # THE CREATE BLOCK - three requests, in the one order whose every prefix reads correctly.
    # There is no transaction here; see the module docstring for the residual gap and for what
    # happens if the process dies between them.
    # ══════════════════════════════════════════════════════════════════════
    session = repo.insert_session(
        supabase,
        user_id=caller_id,
        listing_id=entitlement.listing_id,
        source_strategy_id=entitlement.source_strategy_id,
        version_id=entitlement.version_id,
        exchange_id=exchange_id,
        symbol=metadata.symbol,
        timeframe=frame,
        initial_capital_minor=capital.minor,
        currency=capital.currency,
        config=config,
        market_data_source=market_data_source,
    )
    session_id = str(session.get("id"))

    account = repo.get_or_create_account(
        supabase,
        caller_id,
        capital.currency,
        session_id,
        initial_capital=capital.major,
    )

    repo.insert_equity_snapshot(
        supabase,
        user_id=caller_id,
        session_id=session_id,
        series_index=0,
        # Written from the account row the database returned, not recomputed: ``total_equity`` is
        # ``available + locked + position_market_value`` and recomputing it here would be a second
        # implementation of the one identity Requirement 18.3 holds to zero tolerance.
        total_equity=account.get("total_equity"),
        available_balance=account.get("available_balance"),
        locked_balance=account.get("locked_balance"),
        # Exactly zero, and it is a measurement: a session that has just been created holds no
        # position, so the sum over its open positions is zero rather than unknown.
        position_market_value=Decimal("0"),
        stale=False,
        cause="SESSION_START",
        taken_at=instant,
    )

    # ── THE FEED. A refusal here leaves the session at CREATED with no subscription. ──
    feed = await open_feed(
        PaperFeedConfig(
            session_id=session_id,
            user_id=caller_id,
            exchange_id=str(exchange_id),
            symbol=metadata.symbol,
            timeframe=frame,
        ),
        exchange=exchange,
        redis=redis,
        supabase=supabase,
        measurements=measurements,
    )

    # ── RUNNING, then the frame, then the loop ──
    try:
        session = repo.transition_session_state(
            supabase,
            user_id=caller_id,
            session_id=session_id,
            from_state=STATE_CREATED,
            to_state=STATE_RUNNING,
            started_at=instant,
        )
    except Exception:
        # The subscription is open and the session is not running, which is the one combination
        # nothing downstream expects: the feed would accumulate candles for a session that will
        # never step. Released here so the failure leaves the state a feed refusal would have -
        # CREATED with no subscription - and then re-raised unchanged.
        await feed.close()
        raise

    await _emit(
        supabase,
        user_id=caller_id,
        session_id=session_id,
        event_type=PaperEvent.SESSION_STARTED,
        payload={
            "session_id": session_id,
            # The Listing the caller named, or the strategy behind it for an owned start. Never a
            # definition, a graph or a plan (Requirement 19.7).
            "strategy_ref": str(
                entitlement.listing_id or entitlement.source_strategy_id or listing_id
            ),
            "symbol": metadata.symbol,
            "timeframe": frame,
            "initial_capital_minor": capital.minor,
            "currency": capital.currency,
            "market_data_source": feed.market_data_source,
            "feed_transport": feed.feed_transport,
            "config_digest": config_digest(config),
            "started_at": instant,
            "actor_id": caller_id,
        },
        emitted_at=instant,
    )

    # The frozen config and the account this pipeline created are handed to the spawner rather than
    # left for the installing layer to re-derive: they are the same two objects the rows above
    # record, so the loop cannot step on a configuration that disagrees with the session's.
    loop_task = await _spawn(
        spawn_loop, session, feed, config=config, account_id=account.get("id")
    )

    logger.info(
        "[paper-session] session %s is RUNNING on %s %s at %s over source %s",
        session_id,
        exchange_id,
        metadata.symbol,
        frame,
        feed.market_data_source,
    )
    return StartedSession(
        session=session,
        account=account,
        config=config,
        feed=feed,
        entitlement=entitlement,
        loop_task=loop_task,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE FOUR OPERATIONS (task 27.3 - Requirements 17.7, 17.14, 21.4)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class OperationOutcome:
    """One accepted operation: what it was, where it came from, and where it left the session.

    Returned rather than the bare row so a caller - and a test - can assert on the transition
    itself instead of re-reading the session to infer it. ``event_sequence`` is the sequence the
    ``paper_events`` record took, which is what a client resumes a replay from.

    :class:`StopOutcome` and :class:`ResetOutcome` extend it with what those two operations did
    beyond the transition, so a caller that only needs the transition reads this shape unchanged.
    """

    operation: str
    from_state: str
    to_state: str
    session: Mapping[str, Any]
    event_sequence: int
    at: datetime


#: What :func:`apply_operation` calls after the guarded transition and before the record:
#: ``finalise(session_row) -> Optional[Mapping[str, Any]]``, optionally a coroutine. The mapping it
#: returns becomes the ``paper_session_stopped`` frame's ``final_metrics``, and ``None`` leaves that
#: field absent - which ``PaperSessionStoppedPayload`` documents as "could not be computed" and is a
#: different fact from a session that ended flat (Requirement 28.5).
#:
#: It exists for exactly one caller, :func:`stop_session`, and for one reason: Requirement 17.8
#: requires the finals COMMITTED before the stop is reported, and Requirement 17.7 requires the
#: operation RECORDED with its resulting state - so the commit has to sit between the transition
#: and the record, which is the one place a caller cannot reach by wrapping this function.
OperationFinaliser = Callable[[Mapping[str, Any]], Any]


def _caller_identity(caller: Any, what: str) -> str:
    """The authenticated server-side identity, or a programming error naming the caller.

    Not a 403 and not a refusal naming a validation: the route layer returns 401 for an
    unauthenticated request (Requirement 21.6), so reaching one of these functions with no identity
    is a bug in the layer above rather than a user's problem. No identifier from a request body,
    query or path may stand in for it (Requirement 21.1).
    """
    identity = str(getattr(caller, "id", None) or (caller or {}).get("id") or "").strip()
    if not identity:
        raise ValueError(
            f"{what} requires the authenticated server-side identity (Requirement 21.1)"
        )
    return identity


def _gate(
    supabase: Any, caller_id: str, session_id: Any, operation: Any
) -> Tuple[Mapping[str, Any], str, str]:
    """The ownership-scoped read and the state gate, BEFORE any statement that writes.

    Steps 1 and 2 of :func:`apply_operation`, extracted because :func:`reset_session` needs them
    without the transition: its body has to run before the ``STOPPED -> CREATED`` write (see that
    function for the prefix argument), so it gates here, does its work, and then calls
    :func:`apply_operation` - which gates again through this same function and takes the guarded
    transition. Two reads, one gate implementation; the alternative was a second gate that could
    disagree with this one.

    Returns:
        ``(session_row, current_state, operation_name)`` - the row as read, the state normalised,
        and the operation normalised. All three are what the caller needs and none of them is
        re-derived downstream.

    Raises:
        PaperError: ``NOT_FOUND``, identically for "no such session" and "another user's session"
            (Requirement 21.4).
        PaperSessionOperationRejected: 409 naming BOTH the current state and the rejected operation.
            Nothing has been written, because nothing has been issued.
    """
    row = repo.read_session(supabase, caller_id, session_id)
    if row is None:
        raise _session_not_found(session_id)
    current = normalise_state(row.get("session_state"))
    name = normalise_operation(operation)

    if name is None or name not in CALLER_OPERATIONS or not can_operate(name, current):
        # One raise for four causes - an unknown operation, ``start``, an unrecognised stored state
        # and a permitted operation from the wrong state - because all four mean "not permitted from
        # here" to the caller and none of them may write. ``session_state`` in the body is the
        # value as STORED, not the normalised one, so an operator reading a rejection caused by a
        # state outside chk_paper_session_state sees the value that caused it.
        logger.info(
            "[paper-session] rejecting %r on session %s in state %r (Requirement 17.14)",
            operation,
            session_id,
            row.get("session_state"),
        )
        raise PaperSessionOperationRejected(
            session_state=row.get("session_state"), operation=operation
        )
    return row, str(current), name


async def apply_operation(
    supabase: Any,
    caller: Any,
    session_id: Any,
    operation: Any,
    *,
    now: Optional[datetime] = None,
    finalise: Optional[OperationFinaliser] = None,
) -> OperationOutcome:
    """Gate, write, record. The ONE implementation the four operations share.

    The order below is Requirement 17.14's guarantee, and it is structural:

    1. **Read the session, scoped by the caller.** Through
       :func:`~paper_repository.read_session`, which carries ``user_id`` as a predicate - so
       another user's session is never fetched and answers exactly as an unknown one does
       (Requirement 21.4). There is no value in scope here that differs between the two cases.
    2. **Gate on the state, before any write.** :func:`can_operate` against the state that was
       just read. A rejected operation raises :class:`PaperSessionOperationRejected` at this point,
       which is why "changes nothing" is a fact about statements not issued rather than a claim
       about a rollback this transport does not have.
    3. **The guarded UPDATE**, carrying the state that was read as a predicate
       (:func:`~paper_repository.transition_session_state`). A concurrent operation that already
       moved the session makes it match zero rows and raise, rather than overwriting a state this
       caller never saw. That is the substitute for ``SELECT ... FOR UPDATE``, and it is the same
       one the money path uses.
    4. **The finaliser, when one was given** (:data:`OperationFinaliser`). It runs after the
       transition and before the record, which is where Requirement 17.8's commit belongs: the
       transition is what stops the session's loop stepping, so a "final" figure computed before it
       would be superseded by the next bar, and the record has to carry those figures. It is only
       :func:`stop_session`'s, and a failure inside it is contained THERE rather than here - see
       that function.
    5. **The record**, in ``paper_events``, with the requesting user and the RESULTING state -
       Requirement 17.7's "record each accepted operation with the requesting user, the resulting
       state and a timestamp", all three. Recorded after the write, so a recorded operation is one
       that happened.

    ``operation`` may be any of the five, but a caller-facing route passes one of
    :data:`CALLER_OPERATIONS`: ``start`` is the tail of :func:`start_session` and is not a route.
    Requesting ``start`` here is refused as a rejected operation from whatever state the session
    is in - including ``CREATED``, where the edge exists - because a second start would open a
    second feed and a second loop against one session. That refusal is deliberate and is the one
    place this function is stricter than the edge table.

    Raises:
        PaperError: ``NOT_FOUND`` when no session of that id belongs to this caller. Identical for
            "no such session" and "another user's session" (Requirement 21.4).
        PaperSessionOperationRejected: 409, naming the current state and the rejected operation.
            Nothing was written.
        PaperConcurrencyConflict: the guarded UPDATE matched no row because the state moved
            between the read and the write. Nothing was written; the caller re-reads.
        PaperError: 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
    """
    instant = now or utc_now()
    caller_id = _caller_identity(caller, "apply_operation")

    row, current, name = _gate(supabase, caller_id, session_id, operation)

    target = target_state(name, current)
    timestamps: Dict[str, Any] = {}
    column = OPERATION_TIMESTAMP_COLUMN[name]
    if column is not None:
        timestamps[column] = instant

    session = repo.transition_session_state(
        supabase,
        user_id=caller_id,
        session_id=session_id,
        from_state=current,
        to_state=target,
        **timestamps,
    )

    final_metrics: Optional[Mapping[str, Any]] = None
    if finalise is not None:
        outcome = finalise(session)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if outcome is not None and not isinstance(outcome, Mapping):
            raise TypeError(
                "an operation finaliser returns the frame's final_metrics mapping or None, got "
                f"{type(outcome).__name__}; PaperSessionStoppedPayload declares final_metrics as "
                "an optional mapping and forbids every other field (extra='forbid')"
            )
        final_metrics = outcome

    payload: Dict[str, Any] = {
        "session_id": str(session_id),
        # The RESULTING state, which is what Requirement 17.7 requires recorded and what a client
        # that missed a frame reads its current state off.
        "session_state": target,
        "at": instant,
        # The REQUESTING user, the other half of Requirement 17.7. The authenticated identity, not
        # anything the request carried.
        "actor_id": caller_id,
    }
    event_type = OPERATION_EVENT_TYPE[name]
    if event_type is PaperEvent.SESSION_STOPPED:
        # The figures the finaliser COMMITTED, or ``None``. ``None`` is a different fact from "the
        # session ended flat" (Requirement 28.5), which is why an uncomputable metric travels as an
        # absent field rather than as the zero Requirement 28.3 forbids. A reset finalises nothing
        # and therefore carries ``None`` here on purpose: reporting the pre-reset figures on a reset
        # frame would tell a client the session it is now watching has them.
        payload["final_metrics"] = final_metrics

    envelope = await _emit(
        supabase,
        user_id=caller_id,
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        emitted_at=instant,
    )

    logger.info(
        "[paper-session] %s moved session %s from %s to %s (event %s at sequence %s)",
        name,
        session_id,
        current,
        target,
        event_type.value,
        envelope.sequence,
    )
    return OperationOutcome(
        operation=name,
        from_state=current,
        to_state=target,
        session=session,
        event_sequence=int(envelope.sequence),
        at=instant,
    )


async def pause_session(
    supabase: Any, caller: Any, session_id: Any, *, now: Optional[datetime] = None
) -> OperationOutcome:
    """``RUNNING -> PAUSED``. Requirement 17.7.

    The transition and its record, and nothing else: the loop stops stepping because it reads
    ``session_state`` (task 27.2), the feed subscription STAYS OPEN, and no balance, order or
    position is touched. A pause that released the subscription would be a stop with a different
    name, and the resume that followed would start from a gap in the candle series.
    """
    return await apply_operation(
        supabase, caller, session_id, OPERATION_PAUSE, now=now
    )


async def resume_session(
    supabase: Any, caller: Any, session_id: Any, *, now: Optional[datetime] = None
) -> OperationOutcome:
    """``PAUSED -> RUNNING``. Requirement 17.7.

    ``started_at`` is NOT rewritten - see :data:`OPERATION_TIMESTAMP_COLUMN` - so the session
    keeps the instant it actually began. The instant of the resume itself is on its
    ``paper_events`` row.
    """
    return await apply_operation(
        supabase, caller, session_id, OPERATION_RESUME, now=now
    )


# ══════════════════════════════════════════════════════════════════════════
# THE STOP AND THE RESET (task 27.4 - Requirements 17.2, 17.8, 17.15, 19.12)
# ══════════════════════════════════════════════════════════════════════════
#
# THE ORDER IS THE REQUIREMENT, NOT A TIDINESS PREFERENCE
# ------------------------------------------------------
# Requirement 17.8 is a SEQUENCE and its last clause is what makes it one: "SHALL report the stop as
# complete only after those steps have committed". A caller must not be able to observe "stop
# complete" while either the market-data subscription or the Paper_Channel registration is still
# open. :func:`stop_session` therefore performs, in this order and no other:
#
#   1. the ownership-scoped read and the state gate (:func:`_gate`) - nothing written on a refusal;
#   2. the guarded ``RUNNING``/``PAUSED -> STOPPED`` transition, with ``stopped_at``;
#   3. the session loop SETTLED - cancelled and awaited (:func:`_settle_loop`) - so no later step
#      is racing a bar that is still mid-write. ``session_loop`` deliberately releases nothing on
#      ``CancelledError`` (its own docstring says why), so the release below is unambiguously this
#      function's;
#   4. the finals COMMITTED (:func:`commit_session_finals`): the closing
#      ``paper_equity_snapshots`` row at ``cause='SESSION_STOP'`` and the ``paper_metrics`` row;
#   5. the ``paper_session_stopped`` record and its broadcast, carrying those figures as
#      ``final_metrics``;
#   6. the market-data subscription RELEASED - the ``PUBLISH {'action':'unsubscribe',…}`` to
#      ``mds:commands`` AND the local unsubscribe, both through ``FeedHandle.close``;
#   7. the Paper_Channel registration CLOSED (``paper_channel.release_session``, Requirement 19.12);
#   8. only then does this return, and :attr:`StopOutcome.complete` is what says the report may be
#      made. It is ``False`` - with the reason logged - whenever any of 4, 6 or 7 did not happen.
#
# Steps 5 and 6/7 are in that order for a reason a reader would otherwise reverse: the frame has to
# be BROADCAST before the registrations are closed, or the subscribers who are watching the session
# would never receive the event that says it stopped. Requirement 19.12 releases them after they
# have been told, not instead of telling them.
#
# WHAT THE STOP DOES NOT DO: IT PERSISTS THE FINAL ORDERS, IT DOES NOT CANCEL THEM
# -------------------------------------------------------------------------------
# Requirement 17.8 says "persist its final orders, positions, balances, trades, metrics and equity
# curve". Persist, not cancel: the orders, positions, balances, trades and fills of a session are
# ALREADY rows - ``paper_simulator`` wrote each one as it happened - so the two things a stop has to
# add are the two derived figures nothing else writes, the closing equity point and the metrics. A
# stop that cancelled resting orders would be doing Requirement 17.15's job, and 17.15 gives that
# job to the RESET. A stopped session's ``ACCEPTED`` order therefore stays ``ACCEPTED`` and its
# ``locked_balance`` stays locked; neither can move again, because ``admit_execution`` refuses a fill
# for a session that is not ``RUNNING`` and the reset is what returns the balance.
#
# WHAT "ONE TRANSACTION" MEANS HERE - THE RESIDUAL GAP, PREFIX BY PREFIX (Requirement 17.8)
# ----------------------------------------------------------------------------------------
# Task 27.4 and ``design.md`` write the stop as one transaction. **This deployment has no
# transaction** - see the module docstring's "WHAT ``ONE TRANSACTION`` MEANS OVER THIS TRANSPORT",
# which states it once for the whole module: PostgREST speaks one HTTP statement per request, so
# there is no ``BEGIN``, no ``COMMIT``, no ``ROLLBACK``, no ``SELECT ... FOR UPDATE`` and no
# ``RETURNING``. Requirement 24.6's single transaction is NOT achievable over this transport for
# either of these two operations. What is reproduced is the strongest ordering the transport
# supports, and here is what each prefix leaves:
#
#   * died after (2): the session is ``STOPPED`` with ``stopped_at`` set, its loop ends by itself
#     the next time it reads the row, and it has no closing equity point and no metrics row. The
#     equity series ends at its last ``REVALUATION`` point, which
#     :func:`~paper_repository.get_equity_snapshots` reports as the series it has and Requirement
#     18.9's drawdown measures correctly; :func:`~paper_repository.get_metrics` answers the last
#     row a running bar computed, or ``None``, which is "not computed" and not a zero. The
#     subscription and the registrations are still open - the local pubsub handle died with the
#     process, the ``mds`` stream did not, and the registrations belong to sockets that closed.
#   * died after (4): the same plus both finals. The one thing missing is the ``paper_events``
#     record, so a client polling ``GET /api/paper/sessions/{id}`` reads ``STOPPED`` while a client
#     watching the channel never saw the frame. Requirement 17.7's "record each accepted operation"
#     is unmet for that one operation and that is a gap, not a rounding of one.
#   * died after (5): everything is committed and recorded; only the two releases are outstanding.
#
#   **The one that cannot be retried through this function, stated plainly.** After (2) the session
#   is ``STOPPED``, and Requirement 17.14 requires a second ``stop`` from ``STOPPED`` REFUSED - so a
#   stop interrupted after its transition cannot be resumed by calling :func:`stop_session` again.
#   That is why :func:`release_session_resources` and :func:`commit_session_finals` are PUBLIC and
#   both idempotent-safe: the route layer (and an operator) can complete an interrupted stop by
#   calling them, and a repeated ``commit_session_finals`` appends a second measurement rather than
#   overwriting the first. Closing the gap completely means moving the finals into one database
#   function (``rpc``) so PostgreSQL holds the transaction, which is a schema and deployment change
#   outside this task; it is recorded here rather than glossed over.
#
# THE RESET RUNS ITS BODY BEFORE ITS TRANSITION, AND THAT IS THE OPPOSITE CHOICE
# -----------------------------------------------------------------------------
# :func:`reset_session` gates, does its work, and transitions last. The prefix argument is the whole
# reason, and it runs the other way from the stop's:
#
#   * body then transition: dying in between leaves a ``STOPPED`` session whose balances have
#     returned and whose new series has begun. ``reset`` is still legal from ``STOPPED``, so a
#     retry finishes it, and every step of the body is written to be a no-op the second time.
#   * transition then body: dying in between leaves a ``CREATED`` session holding the previous
#     series' balances, positions and open orders - and ``start`` is legal from ``CREATED``, so the
#     session could be RUN again on them. Requirement 17.15 would be violated with no way back,
#     because ``reset`` is not legal from ``CREATED``.
#
# The second is strictly worse, so the reset transitions last. The cost is one extra
# ``paper_sessions`` read - :func:`_gate` runs here and again inside :func:`apply_operation` - and
# that is paid deliberately: two reads against one gate implementation, rather than a second gate
# that could disagree with the first.


#: The two order states that are OPEN in the sense Requirement 17.15 means, and they are the same
#: two ``LEGACY_STATUS_FOR_STATE`` maps to ``'OPEN'`` - which is what
#: ``GET /api/paper/orders?status=OPEN`` has always returned. They are also, and not by coincidence,
#: exactly the two states ``PAPER_ORDER_TRANSITIONS`` gives a ``CANCELLED`` edge to (Requirement
#: 16.2): the set of orders a reset must cancel is the set of orders that may legally be cancelled.
#:
#: ``CREATED`` is deliberately absent. It is ``legacy_status = 'NEW'``, not ``'OPEN'``, it is the
#: transient state ``insert_order`` writes before ``submit_intent`` accepts or rejects the order in
#: the same call, and Requirement 16.2 gives it no cancel edge - only ``ACCEPTED`` and ``REJECTED``.
#: An order persisted at ``CREATED`` is therefore a process that died mid-submit; the reset REPORTS
#: it (:attr:`ResetOutcome.orders_not_cancellable`) rather than forcing a transition
#: ``trg_paper_order_transition_guard`` would answer with a ``23514``.
OPEN_ORDER_STATES: Tuple[PaperOrderState, ...] = (
    PaperOrderState.ACCEPTED,
    PaperOrderState.PARTIALLY_FILLED,
)

#: ``paper_equity_snapshots.cause`` for the closing point of a stopped session. One of
#: ``chk_paper_equity_cause``'s five and one of Requirement 18.11's five points.
STOP_SNAPSHOT_CAUSE = "SESSION_STOP"

#: ``paper_equity_snapshots.cause`` for the first point of the series a reset begins. The same cause
#: :func:`start_session` writes, because it is the same kind of point: the opening equity of a series
#: that has traded nothing.
RESET_SNAPSHOT_CAUSE = "SESSION_START"

#: ``paper_balance_events.cause`` for the reset's balance movement - one of
#: ``chk_paper_balance_event_cause``'s five, and the one 009 added for exactly this. The reset moves
#: cash without a fill, so it is recorded as its own cause rather than as an ``ORDER_UNLOCK`` that
#: named no order.
RESET_BALANCE_CAUSE = "RESET"

#: Why a stop could not commit a final figure. Each is a REASON, reported and logged, never a zero
#: written in place of a measurement (Requirements 28.3, 28.5).
FINALS_NO_LIFECYCLE_ROW = "SESSION_NOT_READABLE"
FINALS_NO_CONFIG = "SESSION_CONFIG_NOT_READABLE"
FINALS_NO_ACCOUNT = "NO_READABLE_ACCOUNT"
FINALS_NO_VALIDATED_PRICE = "OPEN_POSITION_WITH_NO_VALIDATED_PRICE"
FINALS_INVARIANT_VIOLATED = "STORED_EQUITY_IDENTITY_VIOLATED"
FINALS_STATEMENT_FAILED = "A_FINALS_STATEMENT_DID_NOT_COMPLETE"

#: The vocabulary above, as a tuple, so a caller can branch on the set rather than on a sentence.
FINALS_REASONS: Tuple[str, ...] = (
    FINALS_NO_LIFECYCLE_ROW,
    FINALS_NO_CONFIG,
    FINALS_NO_ACCOUNT,
    FINALS_NO_VALIDATED_PRICE,
    FINALS_INVARIANT_VIOLATED,
    FINALS_STATEMENT_FAILED,
)

#: What :func:`stop_session` calls to close the Paper_Channel registrations:
#: ``release(session_id) -> awaitable``. Typed so the seam is a declared interface; the default is
#: ``paper_channel.release_session`` and a caller replaces it only in a test.
ChannelRelease = Callable[[Any], Any]


@dataclass(frozen=True)
class SessionFinals:
    """What a stop committed: the closing equity point, the metrics row, and the figures on both.

    A value rather than a pair of booleans, because "the stop may be reported complete" is a claim
    about specific rows and a caller has to be able to name the one that is missing.

    Every field is ``None`` when the thing it names was not written, and :attr:`reason` says which
    of :data:`FINALS_REASONS` prevented it. There is no field that is zero-when-absent: an
    uncomputable metric is reported absent (Requirements 28.3, 28.5).
    """

    #: The ``cause='SESSION_STOP'`` row, or ``None``.
    snapshot: Optional[Mapping[str, Any]] = None
    #: The ``paper_metrics`` row as inserted, or ``None``.
    metrics_row: Optional[Mapping[str, Any]] = None
    #: The computed figures, before they were split between the row and the frame.
    metrics: Optional[accounting.SessionMetrics] = None
    #: The mapping the ``paper_session_stopped`` frame carries as ``final_metrics``, or ``None``.
    final_metrics: Optional[Mapping[str, Any]] = None
    #: Whether the closing figures were derived from LAST validated prices rather than from a price
    #: at the stop instant (Requirement 18.15). True whenever the session still held an open
    #: position, because a stop supplies no new bar.
    stale: bool = False
    #: The equity series the closing point was appended to.
    series_index: int = 0
    #: One of :data:`FINALS_REASONS` when something is absent, else ``None``.
    reason: Optional[str] = None

    @property
    def committed(self) -> bool:
        """Whether BOTH the closing equity point and the metrics row are persisted."""
        return self.snapshot is not None and self.metrics_row is not None


@dataclass(frozen=True)
class StopOutcome(OperationOutcome):
    """One completed - or one incompletely completed - stop. Requirement 17.8.

    An :class:`OperationOutcome` with the release record attached, so every existing caller that
    reads ``to_state`` keeps working and a caller that has to honour "report the stop as complete
    only after those steps have committed" reads :attr:`complete`.
    """

    #: What was committed at step 4. See :class:`SessionFinals`.
    finals: SessionFinals = SessionFinals()
    #: Whether the session's loop task was cancelled and awaited here. ``False`` when the caller
    #: passed none - which is legitimate (another worker holds it, or none was installed) and is
    #: why it is reported rather than assumed.
    loop_settled: bool = False
    #: Whether the ``mds:commands`` unsubscribe was published.
    mds_released: bool = False
    #: Whether this process released its own pubsub subscription. ``False`` when the caller passed
    #: no :class:`~paper_market_feed.FeedHandle`, because the handle lives in the worker that
    #: opened it and this process cannot release one it does not hold.
    subscription_released: bool = False
    #: How many Paper_Channel subscriptions were released (Requirement 19.12), or ``None`` when the
    #: release did not complete. ``0`` is an answer - nobody was watching - and is not ``None``.
    registrations_closed: Optional[int] = None

    @property
    def complete(self) -> bool:
        """Whether Requirement 17.8's "report the stop as complete" may be reported.

        All four: the finals committed, the ``mds`` unsubscribe published, this process's own
        subscription released, and the registrations closed. Any one of them outstanding makes this
        ``False``, and :func:`stop_session` has already logged which.
        """
        return (
            self.finals.committed
            and self.mds_released
            and self.subscription_released
            and self.registrations_closed is not None
        )


@dataclass(frozen=True)
class ResetOutcome(OperationOutcome):
    """One completed reset. Requirement 17.15.

    An :class:`OperationOutcome` plus what the body did, so "balances returned, orders cancelled,
    positions closed, a new series begun, and nothing deleted" is assertable against the return
    value rather than only against the rows.
    """

    #: The exact whole number of Minor_Units the balances returned to - the figure
    #: ``paper_sessions.initial_capital_minor`` records, never a re-derived one.
    initial_capital_minor: int = 0
    #: The same amount in the major units ``paper_accounts`` stores.
    initial_capital: Decimal = Decimal("0")
    #: The account row as it stands after the return.
    account: Optional[Mapping[str, Any]] = None
    #: The ``cause='RESET'`` ledger row recording the movement.
    balance_event: Optional[Mapping[str, Any]] = None
    #: The ids of the orders moved to ``CANCELLED``.
    cancelled_orders: Tuple[str, ...] = ()
    #: The ids of orders that were neither open nor cancellable - a row persisted at ``CREATED``.
    #: See :data:`OPEN_ORDER_STATES` for why forcing them would be a ``23514``.
    orders_not_cancellable: Tuple[str, ...] = ()
    #: The symbols whose positions were closed to size zero.
    closed_positions: Tuple[str, ...] = ()
    #: The series the pre-reset snapshots belong to.
    previous_series_index: int = 0
    #: ``previous_series_index + 1`` - the series this reset began.
    series_index: int = 0
    #: The opening ``SESSION_START`` point of that new series.
    opening_snapshot: Optional[Mapping[str, Any]] = None


def recorded_capital(initial_capital_minor: Any, currency: Any) -> ValidatedCapital:
    """The RECORDED initial simulated capital, read back in both units. Requirement 17.15.

    :func:`validate_capital` is deliberately NOT reused, and the difference is the point. That
    function answers "may this request be accepted", and part of its answer is a comparison against
    the CONFIGURED per-session maximum - a deployment figure that can be lowered after a session has
    started. Re-running it here would refuse the reset of a session that was legitimately started
    under a higher maximum, which is a refusal Requirement 17.15 does not license.

    So this checks only what has to be true of a stored figure: the currency is one the platform
    holds a Minor_Units exponent for, and the amount is an exact positive whole number of those
    units. The major-unit conversion is the same arithmetic ``validate_capital`` performs -
    ``Decimal(minor).scaleb(-exponent)`` - so the value written back is character-for-character the
    value :func:`start_session` wrote at creation.

    Raises:
        ValueError: the stored figure is not a positive exact integer, or its currency has no
            recorded exponent. Both are impossible for a row this package wrote (``chk_paper_capital``
            and the start-time validation) and both are refused rather than repaired: returning
            balances to a guessed capital would be the fabricated figure Requirement 28.3 forbids.
    """
    label = str(currency or "").strip().upper()
    try:
        exponent = minor_unit_exponent(label)
    except UnsupportedCurrency as exc:
        raise ValueError(
            f"the session's recorded currency {label!r} has no Minor_Units exponent, so the "
            f"recorded initial capital cannot be expressed in the units paper_accounts stores "
            f"({exc}); nothing was written"
        ) from None

    if isinstance(initial_capital_minor, bool) or isinstance(
        initial_capital_minor, float
    ):
        raise ValueError(
            f"paper_sessions.initial_capital_minor must be an exact integer number of Minor_Units, "
            f"got {type(initial_capital_minor).__name__}; nothing was written"
        )
    try:
        amount = (
            initial_capital_minor
            if isinstance(initial_capital_minor, Decimal)
            else Decimal(initial_capital_minor)
        )
    except (ArithmeticError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"paper_sessions.initial_capital_minor is not a readable amount of Minor_Units "
            f"({initial_capital_minor!r}): {exc}; nothing was written"
        ) from None
    if amount != amount.to_integral_value():
        raise ValueError(
            f"paper_sessions.initial_capital_minor carries a fraction of a minor unit ({amount}), "
            "which is not a representable amount of money; nothing was written"
        )
    minor = int(amount)
    if minor <= 0:
        raise ValueError(
            f"paper_sessions.initial_capital_minor must be greater than zero, got {minor} "
            "(chk_paper_capital); nothing was written"
        )
    return ValidatedCapital(
        minor=minor,
        major=(Decimal(minor).scaleb(-exponent)).normalize(),
        exponent=exponent,
        currency=label,
    )


def _accounting_config(lifecycle: Mapping[str, Any]) -> accounting.AccountingConfig:
    """The session's frozen configuration as an :class:`~paper_accounting.AccountingConfig`.

    Read back through :func:`~paper_simulator.session_config_from_jsonb`, which refuses an
    incomplete payload rather than completing it: a session whose recorded configuration is missing
    a rounding mode or a precision must not have its closing figures computed under a filled-in one
    (Requirements 16.12, 28.3).
    """
    return session_config_from_jsonb(lifecycle.get("config")).accounting()


async def _settle_loop(task: Any, session_id: Any) -> bool:
    """Cancel the session's loop task and await it, so nothing is mid-write after this returns.

    Step 3 of the stop. It is this function's job and not ``session_loop``'s: that function
    re-raises ``CancelledError`` untouched and releases nothing, precisely so the release below
    cannot race a bar that was still writing (its own docstring records the reason).

    ``await task`` after ``cancel()`` is what makes "the loop has actually stopped stepping" a fact
    rather than a hope - ``cancel()`` only requests it. A ``CancelledError`` from the awaited task
    is swallowed **only when that task is the one that was cancelled**; if it is this coroutine
    being cancelled from outside, it is re-raised, because swallowing our own cancellation would
    turn a shutdown into a silently half-finished stop.

    Returns:
        Whether a task was settled here. ``False`` for ``None``, which is legitimate: the loop may
        live in another worker (where it ends by itself the next time it reads ``session_state``),
        or no loop may have been installed at all.
    """
    if task is None:
        logger.info(
            "[paper-session] no session loop task was handed to the stop of %s; if one is running "
            "it will end by itself the next time it reads session_state, and this process will not "
            "wait for it",
            session_id,
        )
        return False

    done = getattr(task, "done", None)
    if callable(done) and done():
        return True

    cancel = getattr(task, "cancel", None)
    if callable(cancel):
        cancel()
    try:
        await task
    except asyncio.CancelledError:
        cancelled = getattr(task, "cancelled", None)
        if not (callable(cancelled) and cancelled()):
            # Not the task's cancellation - ours. Re-raised, so a shutdown is not misread as a
            # completed settle.
            raise
    except Exception as exc:  # noqa: BLE001 - the loop's own containment already logged the bar
        logger.warning(
            "[paper-session] the session loop for %s ended with %s while it was being stopped; the "
            "stop continues, because the loop is no longer stepping either way",
            session_id,
            exc,
        )
    return True


def commit_session_finals(
    supabase: Any,
    *,
    user_id: Any,
    session_id: Any,
    instant: datetime,
    lifecycle: Optional[Mapping[str, Any]] = None,
) -> SessionFinals:
    """Persist a stopped session's closing equity point and its metrics row. Requirement 17.8.

    Step 4 of the stop, and PUBLIC because it is also the way an interrupted stop is finished (see
    the section header's prefix analysis). It writes two rows and computes nothing of its own: the
    arithmetic is ``paper_accounting``'s in full, exactly as :func:`_revalue` divides it.

    Two writes, in the one order whose every prefix reads correctly:

      1. the ``paper_equity_snapshots`` row at ``cause='SESSION_STOP'``;
      2. the ``paper_metrics`` row.

    That order, because Requirement 18.9's maximum drawdown is computed FROM the persisted series
    and the closing point is part of the series it measures. Metrics written first would report a
    drawdown of a curve one point shorter than the one the API then serves - a figure that no longer
    matches the rows it claims to summarise. The reverse prefix is harmless: a series with a closing
    point and no metrics row is a series :func:`~paper_repository.get_equity_snapshots` reports in
    full while :func:`~paper_repository.get_metrics` answers ``None``, which means "not computed"
    rather than zero.

    NOTHING IS INVENTED, AND AN UNCOMPUTABLE FIGURE IS REPORTED ABSENT
    -----------------------------------------------------------------
    Every refusal below returns a :class:`SessionFinals` naming one of :data:`FINALS_REASONS` and
    writes nothing:

    * an open position whose symbol has no validated price - Requirement 18.15 forbids substituting
      a synthesised, interpolated or zero price, and ``position_market_value`` is a column that
      cannot be ``NULL``, so the closing point is not written at all;
    * a stored account whose ``total_equity`` does not equal ``available + locked +
      position_market_value`` - ``assert_invariants`` names the breach and it is reported rather
      than snapshotted, because writing it would put a figure that fails Requirement 18.3 into the
      equity curve;
    * a statement that did not complete.

    ``stale`` is ``True`` whenever the session still held an open position, and that is deliberate
    rather than pessimistic: a stop supplies no new bar, so every price used is the LAST validated
    one and Requirement 18.15 marks a figure derived from it as stale. It is ``False`` for a session
    holding no position, where ``position_market_value`` is exactly zero as a MEASUREMENT - the sum
    over an empty set - and nothing was derived from a price at all.

    Returns:
        A :class:`SessionFinals`. :attr:`SessionFinals.committed` is what a caller checks; it never
        raises, because the caller has releases to perform whether or not these two rows landed.
    """
    uid = str(user_id)
    sid = str(session_id)

    row = lifecycle
    try:
        if row is None:
            row = repo.read_session_lifecycle(supabase, uid, sid)
        if row is None:
            logger.error(
                "[paper-session] the finals of session %s cannot be committed: no session of that "
                "id is readable for this identity",
                sid,
            )
            return SessionFinals(reason=FINALS_NO_LIFECYCLE_ROW)

        try:
            acct_config = _accounting_config(row)
        except (InvalidSessionConfig, ValueError) as exc:
            logger.error(
                "[paper-session] the finals of session %s cannot be committed: its recorded "
                "configuration is not readable (%s); no figure is computed under a filled-in one",
                sid,
                exc,
            )
            return SessionFinals(reason=FINALS_NO_CONFIG)

        currency = str(row.get("currency") or repo.DEFAULT_CURRENCY)
        account_row = repo.read_account(supabase, uid, currency, sid)
        if account_row is None:
            logger.error(
                "[paper-session] the finals of session %s cannot be committed: it has no readable "
                "%s account, so there are no balances to close on",
                sid,
                currency,
            )
            return SessionFinals(reason=FINALS_NO_ACCOUNT)
        account_id = str(account_row.get("id"))

        account = account_of(account_row)
        positions = positions_of(
            repo.get_positions(
                supabase, uid, account_id=account_id, session_id=sid
            )
        )
        # The prices Requirement 18.15 admits at a stop, and no others: the last validated price
        # already recorded on each position. There is no bar here to supply a current one, and
        # nothing is interpolated or zeroed.
        prices = accounting.last_validated_prices(positions.values())
        open_positions = [p for p in positions.values() if p.is_open]
        stale = bool(account_row.get("stale")) or bool(open_positions)

        try:
            # Requirement 18.3, before the write and not after it: an equity identity that does not
            # hold must not reach the equity curve, and ``assert_invariants`` names the one that
            # broke. This also covers Requirement 18.4 and 18.5 on the stored rows.
            accounting.assert_invariants(account, positions, prices, acct_config)
            market_value = accounting.position_market_value(
                positions, prices, acct_config
            )
        except accounting.StalePrice as exc:
            logger.error(
                "[paper-session] session %s holds an open position with no validated price, so no "
                "closing equity point is written and no price is substituted (%s)",
                sid,
                exc,
            )
            return SessionFinals(reason=FINALS_NO_VALIDATED_PRICE, stale=True)
        except accounting.InvariantViolation as exc:
            logger.error(
                "[paper-session] the stored figures of session %s violate %s, so they are reported "
                "rather than written into its equity curve: %s",
                sid,
                exc.invariant if hasattr(exc, "invariant") else "an accounting invariant",
                exc,
            )
            return SessionFinals(reason=FINALS_INVARIANT_VIOLATED, stale=stale)

        series_index = current_series_index(supabase, uid, sid)

        snapshot = repo.insert_equity_snapshot(
            supabase,
            user_id=uid,
            session_id=sid,
            series_index=series_index,
            # Read off the account row, not recomputed: ``total_equity`` is
            # ``available + locked + position_market_value`` and the assertion above is what says
            # the stored value is that sum. Recomputing it here would be a second implementation of
            # the one identity Requirement 18.3 holds to zero tolerance.
            total_equity=account.total_equity,
            available_balance=account.available_balance,
            locked_balance=account.locked_balance,
            position_market_value=market_value,
            stale=stale,
            cause=STOP_SNAPSHOT_CAUSE,
            taken_at=instant,
        )

        # Read back AFTER the closing point, so the drawdown is the drawdown of the series the API
        # now serves. ``_series`` keeps it to one series: a drop from the end of one series to the
        # start of the next is not a decline anything experienced (Requirement 18.9).
        snapshots = _series(
            repo.get_equity_snapshots(supabase, uid, session_id=sid), series_index
        )
        orders = repo.get_orders(supabase, uid, account_id=account_id, session_id=sid)
        fills = repo.get_fills(supabase, uid, session_id=sid)
        trades = repo.get_trades(
            supabase, uid, account_id=account_id, session_id=sid
        )

        metrics = accounting.compute_metrics(
            account,
            positions,
            prices,
            snapshots,
            trades,
            acct_config,
            initial_capital=account_row.get("initial_capital"),
            stale=stale,
            price_at=accounting.latest_price_at(positions.values()),
        )

        metrics_row = repo.insert_metrics(
            supabase,
            user_id=uid,
            session_id=sid,
            computed_at=instant,
            total_return_pct=metrics.total_return_pct,
            realized_pnl=metrics.realized_pnl,
            unrealized_pnl=metrics.unrealized_pnl,
            max_drawdown_amount=metrics.max_drawdown_amount,
            max_drawdown_fraction=metrics.max_drawdown_fraction,
            win_rate=metrics.win_rate,
            closed_trade_count=metrics.closed_trade_count,
            order_count=len(orders),
            fill_count=len(fills),
        )
    except (
        repo.PaperRepositoryError,
        accounting.PaperAccountingError,
        ValueError,
        KeyError,
    ) as exc:
        # Contained, not propagated: the caller still has a subscription and a set of channel
        # registrations to release, and a stop that abandoned those because a metrics INSERT failed
        # would leave the session holding resources. The failure is reported through
        # :attr:`SessionFinals.reason`, which makes :attr:`StopOutcome.complete` ``False``.
        logger.exception(
            "[paper-session] a statement or computation in the finals of session %s did not "
            "complete (%s); the stop continues and will NOT be reported complete",
            sid,
            exc,
        )
        return SessionFinals(reason=FINALS_STATEMENT_FAILED)

    final_metrics = dict(metrics.to_row())
    # The two counts that live on the row rather than on ``SessionMetrics``, so the frame carries
    # every figure the row does. Everything else ``to_row`` produces - ``total_equity``,
    # ``position_market_value``, ``margin_usage``, ``stale``, ``price_at`` - has no column in 009
    # and travels on the frame only; an absent one is OMITTED by ``to_row`` rather than sent as a
    # zero (Requirements 18.10, 18.12, 28.5).
    final_metrics["order_count"] = int(metrics_row.get("order_count") or len(orders))
    final_metrics["fill_count"] = int(metrics_row.get("fill_count") or len(fills))

    logger.info(
        "[paper-session] the finals of session %s are committed: equity point %s at series %s and "
        "metrics %s (stale=%s)",
        sid,
        snapshot.get("id"),
        series_index,
        metrics_row.get("id"),
        stale,
    )
    return SessionFinals(
        snapshot=snapshot,
        metrics_row=metrics_row,
        metrics=metrics,
        final_metrics=final_metrics,
        stale=stale,
        series_index=series_index,
    )


async def release_session_resources(
    session_id: Any,
    *,
    feed: Optional[FeedHandle] = None,
    redis: Any = None,
    exchange_id: Any = None,
    symbol: Any = None,
    release_registrations: Optional[ChannelRelease] = None,
) -> Tuple[bool, bool, Optional[int]]:
    """Steps 6 and 7 of the stop: release the subscription, THEN close the registrations.

    PUBLIC, and idempotent in both halves, because it is also how an interrupted stop is finished -
    see the section header. Both halves are the paths that already exist; neither is reimplemented
    here:

    * ``FeedHandle.close`` publishes ``{'action': 'unsubscribe', …}`` to ``mds:commands`` through
      ``paper_market_feed.publish_unsubscribe`` and then releases this process's own pubsub
      subscription. It is the ONE release path and the only unsubscribe command in the codebase.
    * ``paper_channel.release_session`` removes every subscription on the session from every
      registry it was added to and closes each socket (Requirement 19.12).

    THE ORDER, AND WHY IT IS THIS WAY ROUND
    --------------------------------------
    Requirement 17.8 lists the subscription before the registration and the sequence is observable:
    a client whose socket is still registered can be sent a frame, and a frame produced from a
    candle that arrived after the "stopped" frame would be a session emitting events after it
    reported stopping. Releasing the market data first closes that window.

    WHEN THIS PROCESS DOES NOT HOLD THE HANDLE
    -----------------------------------------
    ``feed=None`` with a ``redis`` client publishes the ``mds`` unsubscribe anyway - that half is
    process-independent, and it is the half that leaves a separate service streaming a pair nobody
    consumes. The local unsubscribe is reported as NOT done, because a handle this process does not
    hold is not one it can release; the worker that holds it stops stepping as soon as it reads the
    session's state (``session_loop``) and releases it when its own shutdown reaches
    ``FeedHandle.close``. That is a real gap between two workers and it is reported, not papered
    over: :attr:`StopOutcome.complete` is ``False`` and the log line names what is outstanding.

    Returns:
        ``(mds_released, subscription_released, registrations_closed)``. The third is ``None`` when
        the registry release did not complete, and ``0`` when it completed and nobody was watching.
    """
    sid = str(session_id)
    mds_released = False
    subscription_released = False

    if feed is not None:
        # The ONE release path: the command, then the local unsubscribe, in that order.
        mds_released = await feed.close()
        subscription_released = True
    elif redis is not None:
        mds_released = await publish_unsubscribe(
            redis,
            exchange_id=exchange_id,
            symbol=symbol,
            session_id=sid,
        )
        logger.warning(
            "[paper-session] session %s was stopped by a process that does not hold its feed "
            "handle: the mds unsubscribe was %s, and the LOCAL subscription is released by the "
            "worker that opened it. This stop is not reported complete.",
            sid,
            "published" if mds_released else "NOT published",
        )
    else:
        logger.warning(
            "[paper-session] session %s was stopped with neither a feed handle nor a Redis client, "
            "so its market-data subscription is NOT released: the mds stream for its pair keeps "
            "running and no unsubscribe was published (Requirement 17.8). This stop is not "
            "reported complete.",
            sid,
        )

    registrations_closed: Optional[int] = None
    release = release_registrations
    if release is None:
        # Imported here and not at module scope, the way ``paper_events.broadcast`` reaches the same
        # module: ``paper_channel`` pulls in ``api_ws.ws_manager`` and ``ws_channels``, and this
        # package's module-scope import graph does not grow to reach them for one call.
        from backend_app.backend.paper.paper_channel import release_session

        release = release_session
    try:
        released = release(sid)
        if inspect.isawaitable(released):
            released = await released
        registrations_closed = len(released or ())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, because a leak must not be silent
        logger.error(
            "[paper-session] the Paper_Channel registrations of session %s were NOT released "
            "(%s); Requirement 19.12 is unmet for this stop and it is not reported complete",
            sid,
            exc,
        )

    return mds_released, subscription_released, registrations_closed


async def stop_session(
    supabase: Any,
    caller: Any,
    session_id: Any,
    *,
    now: Optional[datetime] = None,
    feed: Optional[FeedHandle] = None,
    loop_task: Any = None,
    redis: Any = None,
    release_registrations: Optional[ChannelRelease] = None,
) -> StopOutcome:
    """``RUNNING`` or ``PAUSED`` -> ``STOPPED``: commit the finals, then release, then report.

    Requirement 17.8 in the order it states, and the order is the requirement - read the section
    header above for the whole argument and for the residual gap prefix by prefix. In short:

        gate -> transition -> settle the loop -> commit the finals -> record and broadcast
        -> release the market-data subscription -> close the Paper_Channel registrations -> return

    A caller must not report the stop as complete on the strength of this function RETURNING. It
    returns whatever happened, and :attr:`StopOutcome.complete` is the claim: ``True`` only when the
    finals committed, the ``mds`` unsubscribe was published, this process's own subscription was
    released and the registrations were closed. Every outstanding piece is logged at warning or
    error level naming what it is, so an operator reading the log sees the same thing the caller
    sees.

    Args:
        feed: the session's live :class:`~paper_market_feed.FeedHandle`. The route layer holds it
            (``start_session`` returned it) and passes it here, because the object that owns a
            session's lifecycle is this path and a registry inside this module would be a second
            place a session could be tracked from. ``None`` is accepted and reported - see
            :func:`release_session_resources`.
        loop_task: the ``asyncio.Task`` :func:`spawn_session_loop` created. Cancelled and AWAITED
            before anything is committed or released, so nothing below races a bar that is still
            writing. ``None`` is accepted and reported.
        redis: a Redis client, for the case where this process does not hold the handle but can
            still publish the ``mds`` unsubscribe. Ignored when ``feed`` is given, because the
            handle publishes through the same one function.
        release_registrations: the Requirement 19.12 release. Defaults to
            ``paper_channel.release_session``.

    Returns:
        A :class:`StopOutcome`. It is an :class:`OperationOutcome`, so a caller that only needs the
        transition reads the same six fields it always did.

    Raises:
        PaperError: ``NOT_FOUND`` when no session of that id belongs to this caller (Requirement
            21.4), or 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
        PaperSessionOperationRejected: 409 naming the current state and ``stop``. Nothing was
            written, nothing was cancelled and nothing was released - a second stop of a ``STOPPED``
            session reaches this and NOT the release path, which is the one prefix the section
            header calls out as unretryable through this function.
        PaperConcurrencyConflict: the guarded transition matched no row. Nothing was written.
    """
    instant = now or utc_now()
    caller_id = _caller_identity(caller, "stop_session")
    finals = SessionFinals()

    async def _finalise(session: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """Steps 3 and 4, between the transition and the record. See :data:`OperationFinaliser`."""
        nonlocal finals, settled
        settled = await _settle_loop(loop_task, session_id)
        finals = commit_session_finals(
            supabase,
            user_id=caller_id,
            session_id=session_id,
            instant=instant,
        )
        return finals.final_metrics

    settled = False
    outcome = await apply_operation(
        supabase, caller, session_id, OPERATION_STOP, now=instant, finalise=_finalise
    )

    lifecycle: Optional[Mapping[str, Any]] = None
    if feed is None and redis is not None:
        # Only for the no-handle case, and only because ``publish_unsubscribe`` needs the pair: the
        # session row is where the exchange and symbol are recorded, and reading it when a handle
        # was supplied would be a statement for a value the handle already carries.
        lifecycle = repo.read_session_lifecycle(supabase, caller_id, session_id)

    mds_released, subscription_released, registrations_closed = (
        await release_session_resources(
            session_id,
            feed=feed,
            redis=redis,
            exchange_id=(lifecycle or {}).get("exchange_id"),
            symbol=(lifecycle or {}).get("symbol"),
            release_registrations=release_registrations,
        )
    )

    stopped = StopOutcome(
        operation=outcome.operation,
        from_state=outcome.from_state,
        to_state=outcome.to_state,
        session=outcome.session,
        event_sequence=outcome.event_sequence,
        at=outcome.at,
        finals=finals,
        loop_settled=settled,
        mds_released=mds_released,
        subscription_released=subscription_released,
        registrations_closed=registrations_closed,
    )

    if stopped.complete:
        logger.info(
            "[paper-session] session %s is STOPPED and its stop is COMPLETE: finals committed, "
            "mds unsubscribed, subscription released, %s channel registration(s) closed "
            "(Requirement 17.8)",
            session_id,
            registrations_closed,
        )
    else:
        outstanding = [
            name
            for name, done in (
                (f"finals ({finals.reason})", finals.committed),
                ("the mds:commands unsubscribe", mds_released),
                ("the local subscription release", subscription_released),
                ("the Paper_Channel registrations", registrations_closed is not None),
            )
            if not done
        ]
        logger.warning(
            "[paper-session] session %s is STOPPED but its stop is NOT complete: %s outstanding. "
            "Requirement 17.8 permits reporting a stop complete only after those steps have "
            "committed, so do not report it; release_session_resources and commit_session_finals "
            "are callable to finish it.",
            session_id,
            ", ".join(outstanding),
        )
    return stopped


async def reset_session(
    supabase: Any, caller: Any, session_id: Any, *, now: Optional[datetime] = None
) -> ResetOutcome:
    """``STOPPED -> CREATED``: return the balances, cancel, close, begin a new series. Req 17.15.

    The gate, then the body, then the transition - the opposite order from :func:`stop_session`, for
    the prefix reason the section header sets out in full: a reset that transitioned first and then
    died would leave a ``CREATED`` session holding the previous series' money, and ``start`` is
    legal from ``CREATED`` while ``reset`` is not.

    THE BODY, IN THE ONE ORDER WHOSE EVERY PREFIX READS CORRECTLY
    ------------------------------------------------------------
    1. **Every open order to ``CANCELLED``**, one guarded UPDATE each (:data:`OPEN_ORDER_STATES`).
       First, because a prefix in which the balances have been returned while an ``ACCEPTED`` order
       is still on the book is an order resting against ``locked_balance = 0``. Cancelling changes
       none of the four money columns, so Requirement 18.3's identity still holds after this step.
    2. **The balances back to the recorded initial capital**, in ONE version-guarded UPDATE -
       ``available_balance = initial_capital``, ``locked_balance = 0``, ``realized_pnl = 0``,
       ``total_equity = initial_capital``, ``stale = False``, ``last_price_at = NULL`` - followed by
       the ``cause='RESET'`` ``paper_balance_events`` row recording the three deltas and the three
       resulting values. The ledger is what keeps the movement readable after the fact.
    3. **Every open position to size zero** with ``closed_at`` set, never deleted (Requirement
       18.5).
    4. **The opening point of the new series**, ``series_index = previous + 1``, at
       ``cause='SESSION_START'``. Last, because it claims ``position_market_value = 0`` and that is
       only true once step 3 has run.

    **The one window in which Requirement 18.3 does not hold, stated rather than hidden.** Steps 2
    and 3 are two statements and this transport has no transaction to put them in (Requirement 24.6
    is not achievable here - see the module docstring), so between them the account states
    ``total_equity = initial_capital`` while a position still carries value:
    ``available + locked + position_market_value`` exceeds it. The window is one statement long, it
    is only reachable from ``STOPPED`` - a session that is not trading and whose loop has ended -
    and the direction is the conservative one: equity is UNDERSTATED, never overstated. The
    alternative order overstates it, and dying inside that window would leave a session whose cash
    is the old series' and whose positions read as though they had been liquidated at no price. A
    retry of the reset, still legal from ``STOPPED``, closes this window; every step above is a
    no-op the second time.

    NOTHING IS DELETED, AND WHAT ``series_index`` ACTUALLY DISTINGUISHES
    -------------------------------------------------------------------
    No DELETE is issued anywhere in this function. The pre-reset orders, fills, trades, metrics and
    equity snapshots all stay readable through ``/api/paper/sessions/{id}/*``.

    Of those five, only ``paper_equity_snapshots`` carries ``series_index``, and that is the honest
    scope of the task's "distinguished by ``series_index``": 009 gives the column to that table and
    to no other, so the snapshots of the two series are separable by a predicate while the orders,
    fills, trades and metrics of the two are separated by TIME - each is append-only or
    monotonically stamped, and the reset's instant is recorded on its ``paper_events`` row. Adding a
    ``series_index`` to the other four is a migration and is not this task's.

    Two rows are the exception to "nothing is lost", and both are exceptions the schema intends:
    ``paper_accounts`` is a single mutable row, so its pre-reset balances are superseded - and
    recoverable from the ``paper_balance_events`` ledger, which is append-only and now carries a
    ``RESET`` row stating exactly what moved. ``paper_positions`` likewise updates its one open row
    per symbol to size zero, which is how Requirement 18.5 says a closed position is represented,
    and the quantity that was open is recoverable from ``paper_fills``.

    A RESET IS NOT A LIQUIDATION
    ----------------------------
    Step 3 discards the open quantity; it does not sell it. No ``paper_trades`` row is written and
    no realized PnL is recorded for it, because there was no fill and no price at which it closed -
    inventing either would be the fabricated measurement Requirement 28.3 forbids. ``realized_pnl``
    returns to zero on the account because the new series has realized nothing; the figure the old
    series realized stays readable on the final pre-reset ``paper_metrics`` row (the stop that had to
    precede this reset wrote one), on ``paper_trades``, and on ``paper_balance_events.realized_after``.

    Returns:
        A :class:`ResetOutcome`, which is an :class:`OperationOutcome` plus what the body did.

    Raises:
        PaperError: ``NOT_FOUND`` (Requirement 21.4) or 503 ``PAPER_PERSISTENCE_UNAVAILABLE``.
        PaperSessionOperationRejected: 409 naming the current state and ``reset``, from
            :func:`_gate` BEFORE the body issues a statement - so a reset from a state that does not
            permit it leaves the balances, the orders, the positions and the history untouched
            (Requirement 17.14).
        ValueError: the session's recorded capital or configuration is unreadable. Raised BEFORE any
            write, because returning balances to a guessed figure is worse than refusing.
        PaperConcurrencyConflict: a guarded UPDATE matched no row - an order, a position or the
            account moved while this reset was in flight, or the state moved before the transition.
    """
    instant = now or utc_now()
    caller_id = _caller_identity(caller, "reset_session")

    # ── THE GATE, before a single statement that writes (Requirement 17.14) ──
    _gate(supabase, caller_id, session_id, OPERATION_RESET)

    sid = str(session_id)
    lifecycle = repo.read_session_lifecycle(supabase, caller_id, sid)
    if lifecycle is None:
        # Between the gate's read and this one the row stopped being readable for this identity.
        # Answered exactly as an unknown session is (Requirement 21.4), and nothing was written.
        raise _session_not_found(session_id)

    capital = recorded_capital(
        lifecycle.get("initial_capital_minor"), lifecycle.get("currency")
    )
    acct_config = _accounting_config(lifecycle)

    account_row = repo.read_account(supabase, caller_id, capital.currency, sid)
    if account_row is None:
        raise ValueError(
            f"session {sid} has no readable {capital.currency} paper account, so there are no "
            "balances to return to its recorded initial capital; nothing was written"
        )
    account_id = str(account_row.get("id"))
    previous_series = current_series_index(supabase, caller_id, sid)

    # ── 1. EVERY OPEN ORDER TO CANCELLED (Requirement 17.15) ──
    cancelled: List[str] = []
    not_cancellable: List[str] = []
    for order in repo.get_orders(
        supabase, caller_id, account_id=account_id, session_id=sid
    ):
        order_id = str(order.get("id"))
        try:
            state = PaperOrderState(str(order.get("order_state")))
        except ValueError:
            # A stored value outside the six. ``chk_paper_order_state`` makes it unreachable; a
            # hand-reconciled table does not. Reported verbatim rather than coerced.
            logger.error(
                "[paper-session] order %s of session %s carries order_state %r, which is not one "
                "of the six; it is left as it is and reported",
                order_id,
                sid,
                order.get("order_state"),
            )
            not_cancellable.append(order_id)
            continue
        if state not in OPEN_ORDER_STATES:
            if state is PaperOrderState.CREATED:
                # Requirement 16.2 gives CREATED no cancel edge. See OPEN_ORDER_STATES.
                logger.warning(
                    "[paper-session] order %s of session %s is persisted at CREATED, which "
                    "Requirement 16.2 gives no CANCELLED edge; it is not open (legacy_status NEW) "
                    "so Requirement 17.15 is satisfied, and it is reported rather than forced",
                    order_id,
                    sid,
                )
                not_cancellable.append(order_id)
            continue
        repo.update_order(
            supabase,
            user_id=caller_id,
            order_id=order_id,
            order_state=PaperOrderState.CANCELLED,
            # The state that was READ, as a predicate: this transport's substitute for
            # ``SELECT ... FOR UPDATE``. A fill that landed in between makes this match no row and
            # raise, rather than cancelling an order that has since filled.
            expected_state=state,
        )
        cancelled.append(order_id)

    # ── 2. THE BALANCES, TO THE RECORDED INITIAL CAPITAL, GUARDED ON THE VERSION READ ──
    locked = repo.lock_account_for_update(supabase, caller_id, account_id=account_id)
    before = account_of(locked)
    zero = Decimal("0")
    account_after = repo.bump_version(
        supabase,
        user_id=caller_id,
        account_id=account_id,
        expected_version=locked["version"],
        payload={
            "available_balance": capital.major,
            "locked_balance": zero,
            "realized_pnl": zero,
            # ``available + locked + position_market_value`` with no position, which is what step 3
            # makes true. Written as the identity's value and not as an independent running total.
            "total_equity": capital.major,
            # No price has been used by the series that starts here, so nothing is derived from one
            # and nothing is stale (Requirement 18.15).
            "stale": False,
            "last_price_at": None,
        },
    )
    balance_event = repo.insert_balance_event(
        supabase,
        account_id=account_id,
        user_id=caller_id,
        session_id=sid,
        cause=RESET_BALANCE_CAUSE,
        available_delta=capital.major - before.available_balance,
        locked_delta=-before.locked_balance,
        realized_delta=-before.realized_pnl,
        available_after=capital.major,
        locked_after=zero,
        realized_after=zero,
        occurred_at=instant,
    )

    # ── 3. EVERY OPEN POSITION TO SIZE ZERO, NEVER DELETED (Requirement 18.5) ──
    closed: List[str] = []
    positions = positions_of(
        repo.get_positions(supabase, caller_id, account_id=account_id, session_id=sid)
    )
    for symbol, position in positions.items():
        if not position.is_open:
            continue
        repo.upsert_position(
            supabase,
            account_id=account_id,
            user_id=caller_id,
            session_id=sid,
            symbol=symbol,
            # ``side`` and ``entry_price`` are kept as they were: they are what the position WAS,
            # and ``chk_paper_position_side`` admits no third value to mean "none".
            side=position.side,
            size=zero,
            entry_price=position.entry_price,
            opened_at=position.opened_at or instant,
            # The last validated price and its instant are kept - they are measurements that were
            # taken - while the unrealized PnL of a zero-size position is exactly zero, which is a
            # measurement rather than a placeholder.
            current_price=position.current_price,
            unrealized_pnl=zero,
            price_at=position.price_at,
            closed_at=instant,
        )
        closed.append(symbol)

    # ── 4. THE NEW SERIES BEGINS AT previous + 1 (Requirement 17.15) ──
    series_index = previous_series + 1
    opening = repo.insert_equity_snapshot(
        supabase,
        user_id=caller_id,
        session_id=sid,
        series_index=series_index,
        total_equity=capital.major,
        available_balance=capital.major,
        locked_balance=zero,
        # Exactly zero, and a measurement: step 3 left no open position, so the sum over them is
        # zero rather than unknown.
        position_market_value=zero,
        stale=False,
        cause=RESET_SNAPSHOT_CAUSE,
        taken_at=instant,
    )

    # The final state, checked before the transition reports it: Requirement 18.3's identity, 18.4's
    # non-negative balances and 18.5's position rule, on the rows this body just wrote. It closes
    # the window step 3 opened - and it names the invariant if one is still broken, instead of
    # leaving a caller to discover it from a curve.
    accounting.assert_invariants(
        account_of(account_after),
        positions_of(
            repo.get_positions(
                supabase, caller_id, account_id=account_id, session_id=sid
            )
        ),
        {},
        acct_config,
    )

    # ── THE TRANSITION AND THE RECORD, LAST ──
    outcome = await apply_operation(
        supabase, caller, session_id, OPERATION_RESET, now=instant
    )

    logger.info(
        "[paper-session] session %s is reset: balances back to %s minor units, %d order(s) "
        "cancelled, %d position(s) closed to zero, equity series %s -> %s, and nothing deleted "
        "(Requirement 17.15)",
        sid,
        capital.minor,
        len(cancelled),
        len(closed),
        previous_series,
        series_index,
    )
    return ResetOutcome(
        operation=outcome.operation,
        from_state=outcome.from_state,
        to_state=outcome.to_state,
        session=outcome.session,
        event_sequence=outcome.event_sequence,
        at=outcome.at,
        initial_capital_minor=capital.minor,
        initial_capital=capital.major,
        account=account_after,
        balance_event=balance_event,
        cancelled_orders=tuple(cancelled),
        orders_not_cancellable=tuple(not_cancellable),
        closed_positions=tuple(closed),
        previous_series_index=previous_series,
        series_index=series_index,
        opening_snapshot=opening,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION LOOP (task 27.2 - Requirements 17.10, 23.1, 23.5, 27.3)
# ══════════════════════════════════════════════════════════════════════════
#
# ``design.md`` -> ``ASYNC FUNCTION session_loop(session, feed)``, in its order, split into two
# callables rather than one:
#
#   :func:`step_session`  ONE market event, end to end. Every emission, every write and every
#                         refusal happens here, in the order the task states.
#   :func:`session_loop`  the ``while`` around it, plus the state gate and the disconnection
#                         branch. It contains no per-event logic of its own.
#
# The split is the one ``next_validated_event`` already uses and it is not cosmetic: "the per-event
# order is the contract" is a claim about one call, and a claim about one call can be asserted
# without racing a background task. It is also what lets ``paper_replay`` (task 27.5) drive the
# same per-event path over a recorded event stream instead of a live feed.
#
# AN ``asyncio`` TASK, AND WHAT IS OFFLOADED (Requirement 27.3)
# ------------------------------------------------------------
# :func:`spawn_session_loop` returns a spawner that calls ``asyncio.create_task`` - not a thread,
# not a process, and not a second loop. Every I/O step below is ``await``ed. The ONE CPU-bound
# step - the DAG evaluation and the indicator computation for a bar - goes through
# ``starlette.concurrency.run_in_threadpool``, the platform's existing thread-offload facility, so
# a slow indicator delays this session's next bar and stalls nothing else. There is no
# ``time.sleep`` anywhere in this module: the pause poll and the reconnection backoff are
# ``asyncio.sleep`` and ``FeedHandle.reconnect``'s own bounded, jitter-free delay.
#
# **The residual gap, stated rather than glossed.** ``paper_repository``'s functions are
# SYNCHRONOUS - PostgREST over ``supabase-py`` - so the statements this loop issues occupy the
# event loop's thread while they are in flight, exactly as they do in ``paper_simulator``,
# ``paper_market_feed`` and the retained ``paper_trading_service``. Requirement 27.3's "using the
# platform's existing asynchronous and thread-offload facilities" is satisfied for the step this
# task owns and for the emission path; making the whole repository layer non-blocking is a change
# to that module's signature set and to every one of its callers, and it is recorded here rather
# than claimed.
#
# WHY THE DAG RUNTIME AND THE SIGNAL_TRACE RECORDER ARE INJECTED
# -------------------------------------------------------------
# Requirement 17.10 requires the EXISTING DAG execution runtime and the EXISTING
# signal-generation path, and forbids a second strategy evaluation path. Requirement 23.5
# requires ``PAPER`` signals recorded through the SAME path as ``LIVE`` ones and forbids a second
# signal store. Both are therefore arguments here and not implementations:
#
#   * ``evaluate(plan, event) -> signals`` is the platform's runtime, called through
#     :func:`run_in_threadpool`. This module does not know what a node is, does not compute an
#     indicator and does not read a plan - ``plan`` is opaque to it.
#   * ``record_signal(signal, environment=..., paper_session_id=...)`` is the Signal_Trace
#     recorder. Task 29.2 owns its ``PAPER`` write (``environment='PAPER'``, ``paper_session_id``
#     set, ``deployment_id`` left null); THIS task owns the call and its arguments, which is why
#     the default is :func:`no_signal_trace_recorder` and why that default logs at ERROR instead
#     of returning quietly.
#
# The second consequence is deliberate: because both are seams, this package's pinned first-party
# import list (``tests/test_paper_no_random.py::
# test_every_first_party_import_of_the_paper_package_is_enumerated``) does not grow by one line to
# reach ``dag_engine`` - whose transitive graph is numpy, pandas, the feature validators and the
# ML readiness gate - or ``signal_service``. A package whose no-``random`` guarantee is bounded by
# its dependency list does not get to import the whole runtime to call one function.


#: How long the loop waits before re-reading a ``PAUSED`` session's state. Fixed, because a
#: jittered poll would need a random draw and this package has none; ``asyncio.sleep``, because a
#: blocking sleep would stall every other session in the process (Requirement 27.3).
#:
#: A paused session KEEPS its subscription (see :func:`pause_session`) and simply stops stepping,
#: so the candles that close while it is paused are candles it never processed. They are not
#: back-filled on resume - Requirement 14.9 forbids interpolating across a gap, and
#: ``FeedHandle.reconnect``'s docstring records the same rule for an outage.
PAUSE_POLL_SECONDS = 1.0

#: The Execution_Environment every signal this loop records carries. Requirements 23.1 and 13.1.
#: Spelled as this one constant so no call site can write ``'LIVE'`` by accident;
#: ``SignalGeneratedPayload.environment`` is additionally pinned to it by its own ``Literal``.
SIGNAL_ENVIRONMENT = "PAPER"

#: The order type a signal that names none produces. ``'market'``, which is
#: ``OrderIntent.from_mapping``'s default and the existing ``POST /api/paper/orders`` body's.
DEFAULT_SIGNAL_ORDER_TYPE = "market"

#: ``decision -> intent side``, for the two decisions that state one themselves.
#: ``signal_service._normalise_decision`` produces ``BUY``, ``SELL``, ``EXIT`` and ``CLOSE`` and
#: never ``HOLD`` - a HOLD is not a signal - so these four are the whole vocabulary.
SIDE_FOR_DECISION: Dict[str, str] = {"BUY": "buy", "SELL": "sell"}

#: The decisions whose side depends on the OPEN POSITION rather than on the decision.
EXIT_DECISIONS: FrozenSet[str] = frozenset({"EXIT", "CLOSE"})

#: The intent side that closes a position of each side. ``LONG`` is closed by a sell.
CLOSING_SIDE_FOR_POSITION: Dict[str, str] = {
    accounting.LONG: "sell",
    accounting.SHORT: "buy",
}

#: Why one signal produced no order intent. Stable strings, because they are log values and a
#: runbook matches on a code rather than on prose - the same convention
#: ``paper_market_feed.INVALID_*`` and ``paper_simulator.REJECTION_*`` use.
#:
#: None of these is a rejection: a rejected ORDER is a persisted ``paper_orders`` row carrying one
#: of Requirement 16.5's eight reasons, and it exists only once an intent does. These are the
#: reasons no intent was built at all, and every one of them is a fact about the signal.
INTENT_NO_SIDE = "SIGNAL_STATES_NO_SIDE"
INTENT_NO_QUANTITY = "SIGNAL_STATES_NO_QUANTITY"
INTENT_NOT_POSITIVE = "SIGNAL_QUANTITY_NOT_POSITIVE"
INTENT_NO_OPEN_POSITION = "NO_OPEN_POSITION_TO_CLOSE"
INTENT_NO_LIMIT_PRICE = "LIMIT_SIGNAL_STATES_NO_LIMIT_PRICE"

NO_INTENT_REASONS: Tuple[str, ...] = (
    INTENT_NO_SIDE,
    INTENT_NO_QUANTITY,
    INTENT_NOT_POSITIVE,
    INTENT_NO_OPEN_POSITION,
    INTENT_NO_LIMIT_PRICE,
)

#: The platform's DAG execution runtime, as this loop sees it: ``evaluate(plan, event)`` returning
#: whatever the existing signal-generation path produces for that bar. SYNCHRONOUS on purpose - it
#: is the CPU-bound step, and :func:`run_in_threadpool` is what it is handed to.
StrategyEvaluator = Callable[[Any, MarketEvent], Any]

#: The Signal_Trace recorder, as ``design.md`` calls it:
#: ``record(signal, environment=..., paper_session_id=...)``, optionally a coroutine.
SignalTraceRecorder = Callable[..., Any]


# ══════════════════════════════════════════════════════════════════════════
# INSTRUMENTATION (Requirements 26.6, 27.6)
# ══════════════════════════════════════════════════════════════════════════

#: Why one strategy output produced no submitted order. Stable strings, because they are metric
#: label values and a runbook matches on a code rather than on prose. Every one of them is a
#: DIFFERENT operator action, which is why they are not folded into a single "signal error":
#: no runtime is a deployment defect, an unreadable output is a strategy defect, a symbol mismatch
#: is a plan defect, a missing quantity is a sizing gap, and a refused submission is a market or
#: funding condition.
SIGNAL_ERROR_NO_RUNTIME = "NO_STRATEGY_RUNTIME"
SIGNAL_ERROR_UNREADABLE = "OUTPUT_NOT_READABLE_AS_SIGNAL"
SIGNAL_ERROR_SYMBOL_MISMATCH = "SIGNAL_FOR_ANOTHER_SYMBOL"
SIGNAL_ERROR_NO_QUANTITY = "NO_EXACT_QUANTITY_TO_BROADCAST"
SIGNAL_ERROR_NOT_EXECUTABLE = "NO_EXECUTABLE_INTENT"
SIGNAL_ERROR_INTENT_NOT_REPRESENTABLE = "INTENT_NOT_REPRESENTABLE"
SIGNAL_ERROR_SUBMISSION_REFUSED = "SUBMISSION_REFUSED"


#: The three ``paper.signal.*`` recorders below all reach the collector through
#: :func:`backend_app.backend.metrics.guarded_collector`, which yields ``None`` when it is
#: unavailable. Each of them then records nothing and returns: instrumentation must never be what
#: stops a session stepping, so the bar is still evaluated and every signal it produced is still
#: submitted.


def _record_signal_latency(symbol: str, duration_ms: float) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_signal_latency(symbol, duration_ms)


def _record_signal_generated(symbol: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_signal_generated(symbol=symbol)


def _record_signal_error(reason: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_signal_error(reason)


class PaperSignalUnreadable(ValueError):
    """One value the evaluator produced cannot be read as a signal at all.

    Raised by :func:`paper_signal` and CONTAINED by :func:`step_session`: one unreadable output
    must not end a session, for the reason Requirement 14.7 gives on the live path - "a node
    evaluation error on one event SHALL NOT halt the runtime's processing of later events".
    """


class PaperSignalNotExecutable(Exception):
    """The signal was read, but states no order intent this session can submit.

    Not an error and not a rejection - see :data:`NO_INTENT_REASONS`. ``reason`` is one of those
    codes, so a log line and a test both match on a code rather than on a sentence.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {detail}" if detail else self.reason)


@dataclass(frozen=True)
class PaperSignal:
    """One signal, projected onto the fields a Paper_Session may act on and disclose.

    **This field set IS Requirement 23.5's safe projection, and that is why it is a closed
    dataclass rather than a dict.** ``SignalGeneratedPayload``'s nine fields are built from the
    first nine attributes below and from nothing else, so "the frame carries no plan, node,
    indicator, feature, ML-inference or risk-rule field" is a property of the type: there is no
    such attribute here to leak, and the object the evaluator produced is never passed to the
    emission path. It goes to the Signal_Trace recorder - which is the ONE place a signal's full
    detail belongs, behind Requirement 23.3's owner/subscriber projection - and nowhere else.

    Every numeric field is an exact ``Decimal`` or ``None``. A ``float`` is REFUSED rather than
    converted, which is a real constraint on the wiring layer and is stated here rather than
    softened: ``Decimal(str(0.1))`` would silently substitute one number for another, and
    Requirement 18.1 admits no such computation. The existing ``signal_service.Signal.quantity``
    is typed ``Optional[float]``, so a wiring layer handing one straight over gets a named
    refusal; widening that column's Python type is ``signal_service``'s change to make, not
    something this loop may paper over by rounding.

    Attributes:
        signal_id: the Signal Trace identifier. Carried onto ``paper_orders.signal_id``, which is
            what ties a paper order back to the decision that produced it.
        decision: ``BUY``, ``SELL``, ``EXIT`` or ``CLOSE`` - the existing vocabulary, upper-cased.
        symbol: the market. Compared against the session's own symbol before anything is
            submitted.
        side: ``buy`` / ``sell`` where the signal states one, else ``None``. An ``EXIT`` whose
            closing side depends on the open position legitimately states none.
        quantity: the requested quantity, or ``None`` when the signal stated only a sizing
            intention. ``None`` is not zero and is not filled in from anywhere.
        price: the price the decision was made at, where the signal carries one. ``None``
            otherwise - Requirement 14.9 forbids synthesising one, and a market order is priced
            by the simulator from the validated event instead.
        order_lifecycle_state: the unchanged nine-value vocabulary of
            ``backend/order_lifecycle_state.py``. No paper-specific state is added.
        generated_at: the instant of the decision. Defaults to the market instant of the bar it
            was made on, never to a clock read - a clock read would make a replay diverge
            (Requirement 15.4).
        order_type: ``market`` or ``limit``, lower-cased.
        limit_price: the limit, for a limit signal.
    """

    signal_id: str
    decision: str
    symbol: str
    side: Optional[str]
    quantity: Optional[Decimal]
    price: Optional[Decimal]
    order_lifecycle_state: OrderLifecycleState
    generated_at: datetime
    order_type: str = DEFAULT_SIGNAL_ORDER_TYPE
    limit_price: Optional[Decimal] = None

    @property
    def is_exit(self) -> bool:
        """Whether this decision closes a position rather than opening one."""
        return self.decision in EXIT_DECISIONS


def _signal_value(source: Any, *names: str) -> Any:
    """The first of ``names`` that ``source`` reports as something other than ``None``.

    Two shapes, because two callers exist: the existing path produces a
    ``signal_service.Signal`` (an object) and a test or a future producer may hand over the
    mapping ``to_public_dict`` returns. The same reading ``signal_service._pick`` performs, and
    for the same reason - a projection over named fields rather than an iteration, so a field
    that happens to carry a strategy's internals has no route in.
    """
    for name in names:
        if isinstance(source, Mapping):
            if source.get(name) is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return None


def _signal_decimal(source: Any, what: str, *names: str) -> Optional[Decimal]:
    """One numeric field off a signal as an exact ``Decimal``, or ``None`` when absent.

    ``accounting.to_decimal`` does the refusing, so a ``float`` is a
    :class:`PaperSignalUnreadable` naming Requirement 18.1 rather than a lossy conversion.
    """
    raw = _signal_value(source, *names)
    if raw is None:
        return None
    try:
        return accounting.to_decimal(raw, what)
    except accounting.PaperAccountingError as exc:
        raise PaperSignalUnreadable(str(exc)) from exc


def _signal_instant(source: Any, fallback: datetime) -> datetime:
    """The signal's ``generated_at``, or the bar's own market instant.

    ``fallback`` is the market instant of the event the decision was made on, NOT ``utc_now()``:
    every timestamp this loop writes comes from the event, so a replay of the same events
    reproduces the same series (Requirement 15.4).
    """
    raw = _signal_value(source, "generated_at", "at", "timestamp")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            logger.warning(
                "[paper-session] a signal's generated_at %r is not an ISO-8601 instant; the bar's "
                "own market instant is recorded instead, because this loop reads no clock",
                raw,
            )
    return fallback


def paper_signal(produced: Any, *, event: MarketEvent) -> PaperSignal:
    """Project one value the evaluator produced onto :class:`PaperSignal`.

    A PROJECTION and not a second signal model: every field is read by name off whatever the
    existing signal-generation path returned, and nothing is derived, defaulted or computed
    except the two defaults the type documents (``order_type`` and ``generated_at``).

    Raises:
        PaperSignalUnreadable: no identifier, no decision or no symbol; an unrecognised
            ``order_lifecycle_state``; an order type outside ``market`` / ``limit``; or a numeric
            field that is a ``float`` (Requirement 18.1). Each is contained by
            :func:`step_session` and logged, so one unreadable output costs one signal rather
            than the session.
    """
    signal_id = _signal_value(produced, "signal_id", "id")
    if signal_id is None or not str(signal_id).strip():
        raise PaperSignalUnreadable(
            "the value the strategy runtime produced carries no signal identifier; a paper order "
            "records signal_id so a fill can be traced back to the decision that caused it"
        )
    decision = _signal_value(produced, "decision")
    if decision is None or not str(decision).strip():
        raise PaperSignalUnreadable(
            f"signal {str(signal_id)[:16]} carries no decision; BUY, SELL, EXIT and CLOSE are the "
            "vocabulary, and a HOLD is not a signal"
        )
    symbol = _signal_value(produced, "symbol")
    if symbol is None or not str(symbol).strip():
        raise PaperSignalUnreadable(
            f"signal {str(signal_id)[:16]} names no symbol, so there is no market to submit it on"
        )

    raw_state = _signal_value(produced, "order_lifecycle_state")
    state = (
        OrderLifecycleState.GENERATED
        if raw_state is None
        else normalise_lifecycle_state(raw_state)
    )
    if state is None:
        raise PaperSignalUnreadable(
            f"signal {str(signal_id)[:16]} carries order_lifecycle_state {raw_state!r}, which is "
            "outside backend/order_lifecycle_state.py's nine values; this loop adds no "
            "paper-specific state vocabulary"
        )

    raw_type = _signal_value(produced, "order_type") or DEFAULT_SIGNAL_ORDER_TYPE
    order_type = str(raw_type).strip().lower()
    if order_type not in ("market", "limit"):
        raise PaperSignalUnreadable(
            f"signal {str(signal_id)[:16]} asks for order type {order_type!r}; a Paper_Session "
            "supports market and limit orders only"
        )

    side_value = _signal_value(produced, "side")
    side = str(side_value).strip().lower() if side_value is not None else None
    if side is not None and side not in ("buy", "sell"):
        raise PaperSignalUnreadable(
            f"signal {str(signal_id)[:16]} names side {side_value!r}; chk_paper_order_side admits "
            "buy and sell"
        )

    return PaperSignal(
        signal_id=str(signal_id).strip(),
        decision=str(decision).strip().upper(),
        symbol=str(symbol).strip(),
        side=side,
        quantity=_signal_decimal(produced, "signal quantity", "quantity"),
        price=_signal_decimal(produced, "signal price", "price"),
        order_lifecycle_state=state,
        generated_at=_signal_instant(produced, event.event_timestamp),
        order_type=order_type,
        limit_price=_signal_decimal(produced, "signal limit_price", "limit_price"),
    )


def safe_signal_payload(signal: PaperSignal) -> Dict[str, Any]:
    """``signal_generated``'s payload: Requirement 23.5's safe projection, and nothing else.

    Nine keys, built one at a time from :class:`PaperSignal`'s named attributes.
    ``order_type`` and ``limit_price`` are deliberately ABSENT even though the projection carries
    them: ``SignalGeneratedPayload`` declares nine fields and forbids a tenth, and the order's own
    type and limit reach the subscriber on the five ``paper_order_*`` frames, where they describe
    an order that exists rather than an intention.

    ``price`` stays ``None`` when the signal stated none. Requirement 14.9 forbids synthesising a
    price and Requirement 28.5 forbids substituting a zero, so the frame says "no price was
    stated" by carrying ``null`` - which is a different fact from "the price was nothing".
    """
    return {
        "signal_id": signal.signal_id,
        "decision": signal.decision,
        "symbol": signal.symbol,
        # The side the ORDER will carry. An exit whose side the signal left to the open position
        # is reported as the decision word rather than as a guess: ``side`` is required by the
        # payload, and ``decision`` is the honest answer when no side was stated.
        "side": signal.side or signal.decision.lower(),
        "quantity": signal.quantity,
        "price": signal.price,
        "order_lifecycle_state": signal.order_lifecycle_state,
        "generated_at": signal.generated_at,
        "environment": SIGNAL_ENVIRONMENT,
    }


async def no_signal_trace_recorder(
    signal: Any, *, environment: str, paper_session_id: Any
) -> None:
    """The default ``record_signal``: log loudly that no recorder is installed, and return.

    Task 29.2 owns the ``PAPER`` write - ``environment='PAPER'``, ``paper_session_id`` set,
    ``deployment_id`` left null, through ``signal_service``'s existing ``public.signals`` path and
    NOT through a second store (Requirement 23.5). This task owns the call, and it deliberately
    does not pass quietly: a paper session whose signals reach no Signal_Trace is a session whose
    decisions cannot be compared against its fills, and Requirement 23.1's environment column
    would hold nothing for it.

    Same shape as :func:`no_session_loop`, and for the same reason: installing 29.2's recorder is
    a substitution, not a signature change.
    """
    logger.error(
        "[paper-session] signal %s on session %s was NOT recorded in the Signal_Trace: no "
        "recorder is installed (task 29.2). It must be written with environment=%r and "
        "paper_session_id set, through signal_service's existing signals path - never a second "
        "signal store (Requirements 23.1, 23.5). Pass record_signal= to spawn_session_loop.",
        str(_signal_value(signal, "signal_id", "id") or "?")[:16],
        paper_session_id,
        environment,
    )
    return None


async def _record_signal(
    recorder: Optional[SignalTraceRecorder],
    produced: Any,
    *,
    session_id: Any,
) -> Any:
    """Record one signal in the Signal_Trace, with the environment and the session on it.

    The ORIGINAL value the evaluator produced is handed over, not :class:`PaperSignal`: the
    Signal_Trace is where a signal's full detail belongs - Requirement 23.2 lists thirteen fields
    it records per signal and Requirement 23.3 projects them for a subscriber at READ time - so
    projecting here would silently narrow the record rather than the disclosure.

    A failure is logged at error level and swallowed, and the intent is still submitted. That is a
    DECISION and not an oversight, and it differs from the live path on purpose: ``signal_service``
    refuses to route a signal it could not persist (Requirement 15.5), because a live order moves
    real money and an unrecorded one cannot be reconciled. A paper order moves none, and it is
    itself durably recorded - ``paper_orders``, ``paper_fills`` and the ``paper_events`` log all
    carry it, keyed to the session - so refusing to trade because the trace store was briefly
    unavailable would suppress the very figures the session exists to produce. The gap is visible
    in the log rather than inferred from a missing row.
    """
    record = recorder or no_signal_trace_recorder
    try:
        outcome = record(
            produced,
            environment=SIGNAL_ENVIRONMENT,
            paper_session_id=str(session_id),
        )
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.error(
            "[paper-session] the Signal_Trace record for a signal on session %s could not be "
            "written (%s); the paper order still stands and is recorded in paper_orders and "
            "paper_events, so the session's own history is complete and the trace has a gap",
            session_id,
            exc,
        )
        return None


def signal_to_intent(
    signal: PaperSignal,
    *,
    positions: Mapping[str, Any],
    idempotency_key: Optional[str] = None,
) -> OrderIntent:
    """``design.md``'s ``signal_to_intent(signal, session)``. Refuses; never invents.

    The three readings, each of which is a fact the signal or the book already states:

    * **side.** ``BUY`` and ``SELL`` state it. ``EXIT`` and ``CLOSE`` legitimately do not - the
      closing side depends on which way the open position points - so it is taken from that
      position (:data:`CLOSING_SIDE_FOR_POSITION`). With no open position there is nothing to
      close and no intent is built.
    * **quantity.** An entry must state one: a signal that carried only a sizing intention
      (strength, confidence, a percentage of capital) has not said how much, and turning one into
      a quantity is position sizing - a decision the strategy owns, not this loop. An EXIT that
      states none closes the WHOLE open position, and that size is a persisted fact rather than an
      invention.
    * **limit price.** A limit signal must state one. ``paper_simulator`` prices a resting fill at
      exactly the limit, so a limit order with no limit is not an order.

    Raises:
        PaperSignalNotExecutable: naming one of :data:`NO_INTENT_REASONS`. The signal is still
            recorded in the Signal_Trace and still broadcast as ``signal_generated`` - it WAS
            generated - so this refusal costs an order, not a record.
        InvalidOrderIntent: the values cannot be read as an intent at all. Contained by
            :func:`step_session` like every other per-signal failure.
    """
    position = positions.get(signal.symbol)
    open_position = position if position is not None and position.is_open else None

    side = signal.side or SIDE_FOR_DECISION.get(signal.decision)
    if side is None and signal.is_exit:
        if open_position is None:
            raise PaperSignalNotExecutable(
                INTENT_NO_OPEN_POSITION,
                f"{signal.decision} on {signal.symbol} with no open position",
            )
        side = CLOSING_SIDE_FOR_POSITION.get(open_position.side)
    if side is None:
        raise PaperSignalNotExecutable(
            INTENT_NO_SIDE, f"decision {signal.decision} states no side"
        )

    quantity = signal.quantity
    if quantity is None and signal.is_exit:
        if open_position is None:
            raise PaperSignalNotExecutable(
                INTENT_NO_OPEN_POSITION,
                f"{signal.decision} on {signal.symbol} with no open position",
            )
        quantity = open_position.size
    if quantity is None:
        raise PaperSignalNotExecutable(
            INTENT_NO_QUANTITY,
            "the signal states a sizing intention rather than a quantity; position sizing is the "
            "strategy's decision and this loop does not make it",
        )
    if quantity <= Decimal("0"):
        raise PaperSignalNotExecutable(
            INTENT_NOT_POSITIVE, f"quantity {quantity} is not greater than zero"
        )

    limit_price = signal.limit_price
    if signal.order_type == "limit" and limit_price is None:
        # ``price`` is what the decision was made at, which is not the same thing as a limit -
        # using it would put a limit the strategy never asked for on a resting order.
        raise PaperSignalNotExecutable(
            INTENT_NO_LIMIT_PRICE,
            "a limit signal must state its limit; the decision price is not a limit",
        )

    return OrderIntent(
        symbol=signal.symbol,
        side=side,
        order_type=signal.order_type,
        quantity=quantity,
        limit_price=limit_price,
        idempotency_key=idempotency_key,
        signal_id=signal.signal_id,
    )


def signal_intent_idempotency_key(signal: PaperSignal) -> str:
    """The Idempotency_Key one signal's order carries: ``"signal:{signal_id}"``.

    The same derivation ``signal_service.idempotency_key_for`` uses for a live signal, spelled
    here because that module is not in this package's import graph and because the string is the
    contract rather than the function. One decision therefore produces at most one paper order:
    a redelivered market event, a restarted loop or a second worker re-evaluating the same bar
    reaches ``paper_simulator``'s idempotency probe, which returns the recorded order unchanged and
    moves no money (Requirement 16.8).

    Bounded at 128 characters by ``chk_paper_order_idem_len``; a UUID signal id leaves it at 43.
    """
    return f"signal:{signal.signal_id}"


@dataclass(frozen=True)
class SessionStep:
    """What one market event produced. Every field is what happened, never what was intended.

    Returned rather than logged-and-discarded so the per-event ORDER is assertable against one
    call: the emissions are in the order they were recorded, and ``tick`` being ``None`` while
    ``event`` is set could only mean the tick was not emitted.
    """

    #: The validated event, or ``None`` when the feed dropped this one (an invalid candle, a
    #: duplicate, an out-of-order arrival). A drop is a no-op, not an error.
    event: Optional[MarketEvent] = None
    #: The ``market_tick`` frame.
    tick: Optional[PaperEventEnvelope] = None
    #: The signals this bar produced, as safe projections, in the order they were acted on.
    signals: Tuple[PaperSignal, ...] = ()
    #: The ``signal_generated`` frames, one per signal that carried a quantity.
    signal_frames: Tuple[PaperEventEnvelope, ...] = ()
    #: One outcome per submitted intent.
    submissions: Tuple[SubmitOutcome, ...] = ()
    #: ``(signal_id, reason)`` for each signal that produced no intent. See
    #: :data:`NO_INTENT_REASONS`.
    skipped: Tuple[Tuple[str, str], ...] = ()
    #: The fills this event triggered on resting limit orders.
    resting_fills: Tuple[FillOutcome, ...] = ()
    #: The revaluation, or ``None`` when the session held no open position to revalue.
    valuation: Optional[accounting.Valuation] = None
    #: The ``REVALUATION`` equity row, when one was written.
    snapshot: Optional[Mapping[str, Any]] = None
    #: Whether the revaluation had to fall back to a last validated price (Requirement 18.15).
    stale: bool = False
    #: The ``paper_pnl_updated`` and ``paper_drawdown_updated`` frames.
    pnl: Optional[PaperEventEnvelope] = None
    drawdown: Optional[PaperEventEnvelope] = None

    @property
    def dropped(self) -> bool:
        """Whether the feed dropped this event, so nothing else in the step ran."""
        return self.event is None


def current_series_index(supabase: Any, user_id: Any, session_id: Any) -> int:
    """The equity series this session is currently writing: the highest ``series_index``, or 0.

    Read once per loop rather than once per bar. :func:`reset_session` is what begins a new series
    (Requirement 17.15) and a reset is only legal from ``STOPPED``, which is a state this loop has
    already ended in - so the value cannot change underneath a running one, and re-reading it every
    bar would be a round trip per candle to learn a constant.

    It is also what :func:`reset_session` reads to compute ``previous + 1`` and what
    :func:`commit_session_finals` reads to append the closing point to the series the session was
    actually writing.
    """
    rows = repo.get_equity_snapshots(supabase, user_id, session_id=session_id)
    indexes = [
        int(row["series_index"])
        for row in rows
        if isinstance(row.get("series_index"), int)
    ]
    return max(indexes) if indexes else 0


def _series(
    rows: Sequence[Mapping[str, Any]], series_index: int
) -> List[Mapping[str, Any]]:
    """The snapshots of ONE series, in the order they were read.

    ``get_equity_snapshots`` returns every series ascending by ``(series_index, taken_at)``.
    ``max_drawdown`` over the concatenation of two series would report the drop from the end of a
    stopped session to the start of the one that replaced it as a drawdown, which is not a decline
    anything experienced (Requirement 18.9 measures one series).
    """
    return [row for row in rows if int(row.get("series_index") or 0) == series_index]


async def _revalue(
    supabase: Any,
    session: Mapping[str, Any],
    event: MarketEvent,
    *,
    config: SessionConfig,
    account_id: Any,
    series_index: int,
) -> Tuple[
    Optional[accounting.Valuation],
    Optional[Mapping[str, Any]],
    bool,
    Optional[PaperEventEnvelope],
    Optional[PaperEventEnvelope],
]:
    """Revalue the book at this bar's close, snapshot the equity, emit PnL and drawdown.

    ``design.md``'s ``await paper_accounting.revalue(session, evt) // + equity snapshot +
    pnl/drawdown``, as the four writes and two frames it decomposes into. The arithmetic is
    ``paper_accounting``'s in full - this function reads rows, calls ``recalculate`` /
    ``compute_metrics``, asserts the invariants and writes back what came out. It computes no
    balance, no equity, no PnL and no drawdown of its own, which is the same division
    ``paper_trading_service`` and ``paper_simulator`` keep.

    **A book with no open position is not revalued, and that is the honest answer.** A revaluation
    restates what open positions are worth; with none, ``available + locked`` is unchanged and
    there is nothing to restate - so no position write, no account write, no equity row and no PnL
    or drawdown frame. Requirement 18.11 asks for a snapshot at "each position revaluation from a
    validated price", and a bar that revalued nothing is not one. Writing a row per candle for an
    idle session would fill the equity curve with points that measure nothing and would make
    Requirement 18.9's drawdown a function of how long the session ran rather than of what it did.

    Returns:
        ``(valuation, snapshot_row, stale, pnl_frame, drawdown_frame)``. Every element is ``None``
        or ``False`` when there was nothing to revalue.
    """
    user_id = str(session.get("user_id"))
    session_id = str(session.get("id"))

    position_rows = repo.get_positions(
        supabase, user_id, account_id=account_id, session_id=session_id
    )
    positions = positions_of(position_rows)
    if not any(position.is_open for position in positions.values()):
        return (None, None, False, None, None)

    # ``lock_account_for_update`` by ``account_id`` and not ``read_account`` by currency: the row
    # ``paper_repository.read_session`` returns is ``SESSION_FEED_SELECT``, which carries no
    # ``currency`` - so locating the account by currency here would silently locate a USD account
    # for a session denominated in something else. The account identifier is the one this session
    # was created with, and the read carries the ``version`` every write below is guarded by, which
    # is this transport's substitute for ``SELECT ... FOR UPDATE``. It RAISES rather than answering
    # ``None`` when there is no such account, and that failure reaches ``session_loop``'s bar-scope
    # containment: a zero equity for an unreadable account would be the fabricated measurement
    # Requirement 28.3 forbids.
    account_row = repo.lock_account_for_update(
        supabase, user_id, account_id=account_id
    )
    account = account_of(account_row)
    acct_config = config.accounting()

    # The prices, in the order Requirement 18.15 admits them: this bar's validated close for the
    # symbol it is for, then the last validated price already recorded on each position. Nothing
    # is interpolated and nothing is zeroed - a symbol with neither is what ``stale`` reports.
    supplied: Dict[str, Decimal] = {event.symbol: event.close}
    prices: Dict[str, Decimal] = dict(accounting.last_validated_prices(positions.values()))
    prices.update(supplied)
    open_symbols = [p.symbol for p in positions.values() if p.is_open]
    stale = any(symbol not in supplied for symbol in open_symbols)

    try:
        valuation = accounting.recalculate(
            account, positions, prices, acct_config, price_at=event.event_timestamp
        )
    except accounting.StalePrice as exc:
        # An open position on a symbol this session has never had a validated price for. Nothing
        # is written: the persisted figures stay as they were and the session is reported stale
        # (Requirement 18.15). Unreachable for a position this package wrote - every fill records
        # its price - so it is logged as the schema-level surprise it would be.
        logger.warning(
            "[paper-session] session %s holds an open position with no validated price, so this "
            "bar was not revalued and no price was substituted: %s",
            session_id,
            exc,
        )
        return (None, None, True, None, None)

    # Requirement 18.3, before the write and not after it: an equity identity that does not hold
    # must not reach a column, and ``assert_invariants`` names the one that broke.
    accounting.assert_invariants(
        valuation.account, valuation.positions, prices, acct_config
    )

    for symbol, position in valuation.positions.items():
        if not position.is_open:
            continue
        repo.upsert_position(
            supabase,
            account_id=account_id,
            user_id=user_id,
            session_id=session_id,
            symbol=symbol,
            side=position.side,
            size=position.size,
            entry_price=position.entry_price,
            opened_at=position.opened_at or event.event_timestamp,
            current_price=position.current_price,
            unrealized_pnl=position.unrealized_pnl,
            price_at=position.price_at or event.event_timestamp,
            closed_at=position.closed_at,
        )

    # Cash is untouched by a revaluation, so only the three price-derived columns move. Written
    # under the version read above, which is this transport's substitute for SELECT ... FOR UPDATE:
    # a fill that landed in between makes this match no row and raise, and the next bar re-reads.
    account_after = repo.bump_version(
        supabase,
        user_id=user_id,
        account_id=account_id,
        expected_version=account_row["version"],
        payload={
            "total_equity": valuation.account.total_equity,
            "last_price_at": valuation.price_at or event.event_timestamp,
            "stale": stale,
        },
    )

    snapshot = repo.insert_equity_snapshot(
        supabase,
        user_id=user_id,
        session_id=session_id,
        series_index=series_index,
        total_equity=valuation.account.total_equity,
        available_balance=valuation.account.available_balance,
        locked_balance=valuation.account.locked_balance,
        position_market_value=valuation.position_market_value,
        stale=stale,
        cause="REVALUATION",
        taken_at=event.event_timestamp,
    )

    snapshots = _series(
        repo.get_equity_snapshots(supabase, user_id, session_id=session_id), series_index
    )
    metrics = accounting.compute_metrics(
        valuation.account,
        valuation.positions,
        prices,
        snapshots,
        repo.get_trades(supabase, user_id, account_id=account_id, session_id=session_id),
        acct_config,
        initial_capital=account_after.get("initial_capital"),
        stale=stale,
        price_at=valuation.price_at,
    )

    pnl = await _emit(
        supabase,
        user_id=user_id,
        session_id=session_id,
        event_type=PaperEvent.PNL_UPDATED,
        payload={
            "realized_pnl": metrics.realized_pnl,
            "unrealized_pnl": metrics.unrealized_pnl,
            "total_pnl": metrics.realized_pnl + metrics.unrealized_pnl,
            "total_return_pct": metrics.total_return_pct,
            "price_at": metrics.price_at,
            "stale": stale,
        },
        emitted_at=event.received_at,
    )
    drawdown = await _emit(
        supabase,
        user_id=user_id,
        session_id=session_id,
        event_type=PaperEvent.DRAWDOWN_UPDATED,
        payload={
            "max_drawdown_amount": metrics.max_drawdown_amount,
            "max_drawdown_fraction": metrics.max_drawdown_fraction,
            # The highest total_equity this series reached. ``Drawdown`` reports the decline and
            # the fraction it was of its peak, not the peak itself, so it is read back off the
            # series that produced them rather than recomputed from a different one.
            "peak_equity": _peak_equity(snapshots, valuation.account.total_equity),
            "snapshot_count": len(snapshots),
        },
        emitted_at=event.received_at,
    )
    return (valuation, snapshot, stale, pnl, drawdown)


def _peak_equity(snapshots: Sequence[Mapping[str, Any]], fallback: Decimal) -> Decimal:
    """The highest ``total_equity`` in a persisted series, exact. ``fallback`` for an empty one."""
    values = [
        accounting.to_decimal(row["total_equity"], "snapshot total_equity")
        for row in snapshots
        if row.get("total_equity") is not None
    ]
    return max(values) if values else fallback


async def step_session(
    supabase: Any,
    session: Mapping[str, Any],
    feed: FeedHandle,
    *,
    config: SessionConfig,
    account_id: Any,
    evaluate: Optional[StrategyEvaluator] = None,
    plan: Any = None,
    record_signal: Optional[SignalTraceRecorder] = None,
    series_index: int = 0,
) -> SessionStep:
    """One market event, end to end, in the order task 27.2 states and no other.

    ``emit market_tick`` -> ``step the DAG`` -> for each signal ``record`` then
    ``emit signal_generated`` then ``submit the intent`` -> ``check resting orders`` -> ``revalue
    and snapshot equity with the PnL and drawdown emissions``.

    The order is load-bearing at three points:

    1. **``market_tick`` first.** It is the bar the whole step is about, so a subscriber watching
       the channel sees the input before the decisions it caused. A tick emitted after the fills
       would arrive with a lower sequence number than events that depended on it.
    2. **Record before broadcast.** Requirement 23.1's environment column is what makes a
       ``PAPER`` signal distinguishable from a ``LIVE`` one; a frame delivered before the record
       exists is a frame whose signal a reader cannot look up.
    3. **Resting orders after the new intents, revaluation last.** A limit order accepted on this
       bar rests and is checked against the NEXT one - ``check_resting_orders`` reads the persisted
       set, so an order submitted above is in it only if this bar could legitimately fill it - and
       the revaluation values the book as it stands after every fill this bar produced, which is
       what makes the equity point Requirement 18.11 asks for the equity at that bar.

    **Every per-signal failure is contained.** An unreadable output, a signal that states no
    executable intent, a refusal from the simulator (an invalid order, insufficient funds, an
    unhealthy feed, an exhausted retry) is logged and the loop moves to the next signal, then to
    the next bar. That is Requirement 14.7's rule on the live path applied here: one bad decision
    must not end a session, because a session that stopped on the first refusal would report
    nothing about the ninety-nine bars after it. What is NOT contained is a failure of the
    ``paper_events`` record itself, which is the persisted history the session IS.

    Args:
        session: the ``paper_sessions`` row. Only ``id``, ``user_id`` and ``currency`` are read
            here; the simulator re-reads the row itself for the feed gate, so a row that has aged
            cannot admit a fill the persisted state forbids.
        config: the session's frozen configuration (Requirement 16.12).
        account_id: the session's isolated Paper_Account.
        evaluate: the platform's DAG execution runtime, ``evaluate(plan, event)``. Offloaded with
            :func:`run_in_threadpool`. ``None`` steps no DAG and produces no signal, which is
            logged - see the section header for why it is a seam.
        plan: whatever ``evaluate`` needs to identify the strategy. Opaque here.
        record_signal: the Signal_Trace recorder. Defaults to
            :func:`no_signal_trace_recorder`.
        series_index: the equity series this session is writing (:func:`current_series_index`).

    Raises:
        PaperFeedDisconnected: the subscription dropped. :func:`session_loop` owns the
            reconnection; this function does not swallow it, because a caller that kept stepping
            through a dropped feed would be stepping over candles it never received.
        PaperError / PaperRepositoryError: a statement this step needs did not complete.
    """
    event = await next_validated_event(feed)
    if event is None:
        # A drop, with its reason already counted by the feed: an invalid candle, a duplicate
        # (Requirement 14.7) or an arrival out of order. Nothing is emitted, nothing is repaired
        # and nothing is interpolated - this bar simply did not happen for this session.
        return SessionStep()

    user_id = str(session.get("user_id"))
    session_id = str(session.get("id"))

    tick = await _emit(
        supabase,
        user_id=user_id,
        session_id=session_id,
        event_type=PaperEvent.MARKET_TICK,
        payload={
            "symbol": event.symbol,
            "timestamp": event.event_timestamp,
            "open": event.open,
            "high": event.high,
            "low": event.low,
            "close": event.close,
            "volume": event.volume,
            # The feed's own candle identity, so a client can recognise a republished bar - and so
            # P-55 can hold every broadcast price against the ``paper_market_events`` row it came
            # from. Nothing here is recomputed: the five values are the event's.
            "source_event_id": event.source_event_id,
            "latency_ms": event.latency_ms,
            "feed_state": event.feed_state,
        },
        emitted_at=event.received_at,
    )

    # ── the ONE CPU-bound step, off the event loop's thread (Requirement 27.3) ──
    outputs: Any = ()
    if evaluate is None:
        logger.error(
            "[paper-session] session %s stepped bar %s with NO strategy runtime installed, so it "
            "produced no signal. Pass evaluate= to spawn_session_loop; this module implements no "
            "second evaluation path (Requirement 17.10).",
            session_id,
            event.source_event_id[:16],
        )
        # An installed-runtime-less bar is a real failure to generate a signal, so it is counted
        # on `paper.signal.errors` rather than left as a silent zero on `.generated`: an operator
        # cannot tell "no signal because the market was quiet" from "no signal because nothing was
        # wired up" from the generated count alone.
        _record_signal_error(SIGNAL_ERROR_NO_RUNTIME)
    else:
        # `paper.signal.latency_ms` (Requirements 26.6, 27.6): the measured duration of the
        # generation step itself, which is what Requirement 27.3 offloads and what an operator
        # would tune. `perf_counter`, because this is an interval.
        _signal_measured_from = time.perf_counter()
        try:
            outputs = await run_in_threadpool(evaluate, plan, event)
        finally:
            _record_signal_latency(
                event.symbol, (time.perf_counter() - _signal_measured_from) * 1000.0
            )

    signals: List[PaperSignal] = []
    frames: List[PaperEventEnvelope] = []
    submissions: List[SubmitOutcome] = []
    skipped: List[Tuple[str, str]] = []

    produced_values = list(_iter_outputs(outputs))
    positions: Mapping[str, Any] = {}
    if produced_values:
        # Read once for the whole bar rather than once per signal: the closing side and the
        # closing size of an EXIT both come from the open position, and two signals on one bar
        # read the same book.
        positions = positions_of(
            repo.get_positions(
                supabase, user_id, account_id=account_id, session_id=session_id
            )
        )

    for produced in produced_values:
        try:
            signal = paper_signal(produced, event=event)
        except PaperSignalUnreadable as exc:
            logger.error(
                "[paper-session] session %s discarded one strategy output it could not read as a "
                "signal: %s. Later outputs and later bars continue.",
                session_id,
                exc,
            )
            _record_signal_error(SIGNAL_ERROR_UNREADABLE)
            continue
        if signal.symbol != event.symbol:
            # A session subscribes to ONE market and its orders are validated against that
            # symbol, so a signal for another one has no validated price here and no subscription
            # behind it. Refused rather than submitted against this bar's price.
            logger.warning(
                "[paper-session] session %s discarded signal %s: it names %s and this session "
                "trades %s",
                session_id,
                signal.signal_id[:16],
                signal.symbol,
                event.symbol,
            )
            _record_signal_error(SIGNAL_ERROR_SYMBOL_MISMATCH)
            continue

        signals.append(signal)
        # `paper.signal.generated` (Requirement 26.6): counted here, after the signal was read
        # and accepted for this market, so the figure is signals the session ACTED on rather than
        # strategy outputs it received.
        _record_signal_generated(signal.symbol)
        await _record_signal(record_signal, produced, session_id=session_id)

        # ``SignalGeneratedPayload.quantity`` is required and exact. An EXIT that stated none
        # closes the whole open position, so the figure the ORDER will carry is that position's
        # persisted size and reporting it reports what will happen. With neither a stated quantity
        # nor a position to read one off, there is nothing true to put in the field: the signal is
        # RECORDED in the Signal_Trace above - where its sizing intention is itself a column - and
        # no frame is broadcast, because Requirement 28.5 forbids substituting a zero.
        broadcast_signal = signal
        if signal.quantity is None and signal.is_exit:
            closing = positions.get(signal.symbol)
            if closing is not None and closing.is_open:
                broadcast_signal = replace(signal, quantity=closing.size)
        if broadcast_signal.quantity is None:
            logger.warning(
                "[paper-session] session %s recorded signal %s in the Signal_Trace but broadcast "
                "no signal_generated frame for it: no quantity was stated and none could be read "
                "off an open position, and the frame's quantity is exact and required",
                session_id,
                signal.signal_id[:16],
            )
            _record_signal_error(SIGNAL_ERROR_NO_QUANTITY)
        else:
            frames.append(
                await _emit(
                    supabase,
                    user_id=user_id,
                    session_id=session_id,
                    event_type=PaperEvent.SIGNAL_GENERATED,
                    payload=safe_signal_payload(broadcast_signal),
                    emitted_at=event.received_at,
                )
            )

        try:
            intent = signal_to_intent(
                signal,
                positions=positions,
                idempotency_key=signal_intent_idempotency_key(signal),
            )
        except PaperSignalNotExecutable as refusal:
            skipped.append((signal.signal_id, refusal.reason))
            _record_signal_error(SIGNAL_ERROR_NOT_EXECUTABLE)
            logger.info(
                "[paper-session] session %s built no order intent for signal %s: %s",
                session_id,
                signal.signal_id[:16],
                refusal,
            )
            continue
        except InvalidOrderIntent as exc:
            skipped.append((signal.signal_id, "INTENT_NOT_REPRESENTABLE"))
            _record_signal_error(SIGNAL_ERROR_INTENT_NOT_REPRESENTABLE)
            logger.error(
                "[paper-session] session %s could not turn signal %s into an order intent: %s",
                session_id,
                signal.signal_id[:16],
                exc,
            )
            continue

        try:
            submissions.append(
                await submit_intent(
                    supabase,
                    session,
                    intent,
                    config=config,
                    account_id=account_id,
                    latest_event=event,
                    filled_at=event.event_timestamp,
                    series_index=series_index,
                )
            )
        except (PaperError, PaperFeedError, InvalidOrderIntent) as exc:
            # Every one of these is a defined refusal: an unhealthy feed (Requirement 14.5), an
            # order the session's configuration will not represent, an exhausted retry
            # (Requirement 16.10). Logged and stepped over - see the docstring's containment note.
            skipped.append((signal.signal_id, type(exc).__name__))
            _record_signal_error(SIGNAL_ERROR_SUBMISSION_REFUSED)
            logger.warning(
                "[paper-session] session %s did not submit signal %s: %s",
                session_id,
                signal.signal_id[:16],
                exc,
            )

    resting = await check_resting_orders(
        supabase,
        session,
        event,
        config=config,
        account_id=account_id,
        series_index=series_index,
    )

    valuation, snapshot, stale, pnl, drawdown = await _revalue(
        supabase,
        session,
        event,
        config=config,
        account_id=account_id,
        series_index=series_index,
    )

    return SessionStep(
        event=event,
        tick=tick,
        signals=tuple(signals),
        signal_frames=tuple(frames),
        submissions=tuple(submissions),
        skipped=tuple(skipped),
        resting_fills=tuple(resting),
        valuation=valuation,
        snapshot=snapshot,
        stale=stale,
        pnl=pnl,
        drawdown=drawdown,
    )


async def session_loop(
    supabase: Any,
    session: Mapping[str, Any],
    feed: FeedHandle,
    *,
    config: SessionConfig,
    account_id: Any,
    evaluate: Optional[StrategyEvaluator] = None,
    plan: Any = None,
    record_signal: Optional[SignalTraceRecorder] = None,
) -> int:
    """``WHILE session.session_state = 'RUNNING'``: :func:`step_session`. Returns the bar count.

    The whole of the loop's own logic is the state gate and the disconnection branch:

    * **The state is re-read from the database every iteration**, through
      ``paper_repository.read_session`` with ``user_id`` as a predicate. Not from the row this was
      spawned with, and not from a process-local flag: a pause or a stop arrives on another
      request, in another worker, and the persisted row is the only thing both can see. That is
      what makes :func:`pause_session`'s "the loop stops stepping because it reads
      ``session_state``" true.
    * ``PAUSED`` waits :data:`PAUSE_POLL_SECONDS` and re-reads. It does NOT step and it does not
      close the subscription, which is exactly the difference between a pause and a stop.
    * ``STOPPED``, ``CREATED`` (a completed reset) and a row that is no longer readable all end
      the loop. It closes nothing on the way out: the subscription, the channel registration and
      the closing snapshot are :func:`stop_session`'s, and releasing them here would report a
      paused-then-stopped session as having been shut down twice - and would race the stop that is
      committing its finals.
    * A dropped subscription is ``FeedHandle.mark_degraded`` - which persists ``DEGRADED`` and
      records one ``paper_error``, so no order fills while the feed is down (Requirement 14.5) -
      then ``FeedHandle.reconnect``, one bounded, jitter-free attempt per iteration, driven from
      here because this is the only thing that knows whether the session has since been stopped.

    Cancellation is a first-class exit: ``asyncio.CancelledError`` is re-raised untouched, so the
    task dies where it was awaiting and nothing half-writes. It is deliberately NOT used to
    release the feed - the awaited step may have been mid-write, and a release here would race the
    stop path that issued the cancellation. :func:`_settle_loop` is the other end of that
    arrangement: it cancels this task and AWAITS it, so by the time :func:`stop_session` commits a
    closing figure or releases anything, this loop has demonstrably stopped stepping.

    Returns:
        How many events were processed, which is what a caller logs to tell a session that ran
        from one that never received a bar.
    """
    user_id = str(session.get("user_id"))
    session_id = str(session.get("id"))
    series_index = current_series_index(supabase, user_id, session_id)
    processed = 0

    logger.info(
        "[paper-session] the session loop for %s is running on %s (equity series %s)",
        session_id,
        feed.channel,
        series_index,
    )

    while True:
        row = repo.read_session(supabase, user_id, session_id)
        if row is None:
            logger.warning(
                "[paper-session] the session loop for %s is stopping: its row is no longer "
                "readable for this identity",
                session_id,
            )
            break
        state = normalise_state(row.get("session_state"))
        if state == STATE_PAUSED:
            await asyncio.sleep(PAUSE_POLL_SECONDS)
            continue
        if state != STATE_RUNNING:
            logger.info(
                "[paper-session] the session loop for %s is stopping: the session is %s after "
                "%s event(s)",
                session_id,
                row.get("session_state"),
                processed,
            )
            break

        try:
            step = await step_session(
                supabase,
                row,
                feed,
                config=config,
                account_id=account_id,
                evaluate=evaluate,
                plan=plan,
                record_signal=record_signal,
                series_index=series_index,
            )
        except asyncio.CancelledError:
            raise
        except PaperFeedError as dropped:
            # Requirement 14.5, in the order it states: record the degradation, then attempt one
            # bounded reconnection. Nothing is back-filled across the gap - the session resumes
            # from the first event that passes validation, and the candles that closed during the
            # outage are candles it never saw (Requirement 14.9).
            await feed.mark_degraded(str(dropped))
            if not await feed.reconnect():
                logger.warning(
                    "[paper-session] session %s could not re-open its subscription to %s; "
                    "another attempt follows on the next iteration",
                    session_id,
                    feed.channel,
                )
            continue
        except Exception as exc:  # noqa: BLE001 - Requirement 14.7's containment, at bar scope
            # One bar's processing failed in a way its own containment did not cover - a statement
            # that did not complete, a payload that would not validate. Logged at error level
            # (Requirement 26.5) and the next bar runs, because a session that stopped here would
            # report nothing about the bars after it and would leave its feed open with nobody
            # draining it.
            logger.exception(
                "[paper-session] session %s aborted the processing of one bar; later bars "
                "continue (%s)",
                session_id,
                exc,
            )
            continue

        if not step.dropped:
            processed += 1

    return processed


def spawn_session_loop(
    supabase: Any,
    *,
    evaluate: Optional[StrategyEvaluator] = None,
    plan: Any = None,
    record_signal: Optional[SignalTraceRecorder] = None,
) -> SessionLoopSpawner:
    """Build the ``spawn_loop`` :func:`start_session` calls: one ``asyncio`` task per session.

    The seam is ``spawn(session_row, feed_handle, *, config, account_id)``, so what is closed over
    here is exactly what the INSTALLING layer owns and the pipeline cannot know - the DAG execution
    runtime, the plan it evaluates and the Signal_Trace recorder - while the two values the pipeline
    itself creates arrive as arguments at spawn time. That split is the reason this is a factory and
    the reason the seam has four parameters rather than two: see :data:`SessionLoopSpawner` for why
    ``config`` and ``account_id`` could not be closed over by the installing layer without
    re-deriving Requirement 16.12's frozen configuration.

    The layer that owns the DAG execution runtime (the route layer, task 28.1) is what calls this,
    and that is where the wiring belongs: this package must not import the runtime (see the section
    header).

    ``asyncio.create_task`` and not a thread, not a process and not ``asyncio.run``: the loop runs
    on the process's existing event loop, alongside the HTTP handlers, and is stopped by
    cancelling the task it returns. The task is returned rather than stored, because the object
    that owns a session's lifecycle is :func:`stop_session` - which takes it as ``loop_task=`` and
    settles it before it commits or releases anything - and a registry here would be a second place
    a session could be tracked from. It travels back to the installing layer through ``_spawn`` and
    :attr:`StartedSession.loop_task`, which is what a route hands to that ``loop_task=``.

    The returned spawner is deliberately synchronous: ``_spawn`` accepts both shapes, and creating
    the task without awaiting it is what makes :func:`start_session` return as soon as the session
    is ``RUNNING`` rather than after the first bar.

    ``asyncio.create_task`` binds to the RUNNING loop, which is why the spawner is called from
    inside :func:`start_session`'s coroutine and not from synchronous code: a task created against
    the policy loop would never be driven, and the ``RuntimeError`` that says so is better than a
    session whose loop object exists and never runs.
    """

    def spawn(
        session: Mapping[str, Any],
        feed: FeedHandle,
        *,
        config: SessionConfig,
        account_id: Any,
    ) -> "asyncio.Task[int]":
        return asyncio.create_task(
            session_loop(
                supabase,
                session,
                feed,
                config=config,
                account_id=account_id,
                evaluate=evaluate,
                plan=plan,
                record_signal=record_signal,
            ),
            name=f"paper-session-loop:{session.get('id')}",
        )

    return spawn


def _iter_outputs(outputs: Any) -> Sequence[Any]:
    """Whatever the evaluator returned, as a sequence of candidate signals.

    ``None`` and ``()`` are "this bar produced nothing", which is the common case for a candle. A
    single object is one signal - a runtime that returns the one decision it made should not have
    to wrap it - and a mapping is likewise ONE signal rather than an iteration over its keys,
    because a signal is exactly what a mapping of signal fields is.
    """
    if outputs is None:
        return ()
    if isinstance(outputs, Mapping):
        return (outputs,)
    if isinstance(outputs, (str, bytes)):
        raise PaperSignalUnreadable(
            f"the strategy runtime returned {type(outputs).__name__}, which is not a signal or a "
            "sequence of signals"
        )
    if isinstance(outputs, (list, tuple, set, frozenset)):
        return tuple(outputs)
    return (outputs,)


__all__ = [
    "CALLER_OPERATIONS",
    "CLOSING_SIDE_FOR_POSITION",
    "DEFAULT_SIGNAL_ORDER_TYPE",
    "DEFAULT_SIMULATOR",
    "DEPLOYABLE_LIFECYCLE_STATES",
    "EXIT_DECISIONS",
    "INTENT_NOT_POSITIVE",
    "INTENT_NO_LIMIT_PRICE",
    "INTENT_NO_OPEN_POSITION",
    "INTENT_NO_QUANTITY",
    "INTENT_NO_SIDE",
    "MAX_CONCURRENT_PAPER_SESSIONS_PER_USER",
    "MAX_CONCURRENT_SESSION_LIMIT",
    "MAX_SESSION_CAPITAL_MAJOR_UNITS",
    "MAX_SESSION_CAPITAL_MAXIMUM_MAJOR",
    "MIN_CONCURRENT_SESSION_LIMIT",
    "MIN_SESSION_CAPITAL_MAXIMUM_MAJOR",
    "FINALS_INVARIANT_VIOLATED",
    "FINALS_NO_ACCOUNT",
    "FINALS_NO_CONFIG",
    "FINALS_NO_LIFECYCLE_ROW",
    "FINALS_NO_VALIDATED_PRICE",
    "FINALS_REASONS",
    "FINALS_STATEMENT_FAILED",
    "NO_INTENT_REASONS",
    "OPEN_ORDER_STATES",
    "OPERATION_EVENT_TYPE",
    "OPERATION_PAUSE",
    "OPERATION_RESET",
    "OPERATION_RESUME",
    "OPERATION_START",
    "OPERATION_STOP",
    "OPERATION_TIMESTAMP_COLUMN",
    "OPERATION_TRANSITIONS",
    "PAPER_SESSION_TRANSITIONS",
    "PAUSE_POLL_SECONDS",
    "RESET_BALANCE_CAUSE",
    "RESET_SNAPSHOT_CAUSE",
    "SESSION_OPERATIONS",
    "SESSION_STATES",
    "SIDE_FOR_DECISION",
    "SIGNAL_ENVIRONMENT",
    "START_VALIDATIONS",
    "STATE_CREATED",
    "STATE_PAUSED",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STOP_SNAPSHOT_CAUSE",
    "STRATEGY_VERSIONS_TABLE",
    "STRATEGY_VERSION_SELECT",
    "VALIDATION_CAPITAL_EXCEEDS_MAXIMUM",
    "VALIDATION_CAPITAL_NOT_POSITIVE",
    "VALIDATION_CAPITAL_PRECISION",
    "VALIDATION_CONFIG_NOT_BUILDABLE",
    "VALIDATION_CURRENCY_NOT_SUPPORTED",
    "VALIDATION_ENTITLEMENT",
    "VALIDATION_STRATEGY_MARKET_INCOMPATIBLE",
    "VALIDATION_STRATEGY_NOT_EXECUTABLE",
    "VALIDATION_TIMEFRAME_NOT_SUPPORTED",
    "ChannelRelease",
    "OperationFinaliser",
    "OperationOutcome",
    "PaperSessionLimitReached",
    "PaperSessionOperationRejected",
    "PaperSignal",
    "PaperSignalNotExecutable",
    "PaperSignalUnreadable",
    "PaperStartRefused",
    "ResetOutcome",
    "SessionFinals",
    "SessionLoopSpawner",
    "SessionStep",
    "SignalTraceRecorder",
    "StartedSession",
    "StopOutcome",
    "StrategyEvaluator",
    "ValidatedCapital",
    "apply_operation",
    "assert_strategy_supports_market",
    "assert_version_deployable",
    "assert_within_session_limit",
    "can_operate",
    "commit_session_finals",
    "config_digest",
    "current_series_index",
    "is_transition_legal",
    "legal_transitions",
    "no_session_loop",
    "no_signal_trace_recorder",
    "normalise_operation",
    "normalise_state",
    "paper_signal",
    "pause_session",
    "plan_markets",
    "read_strategy_version",
    "recorded_capital",
    "release_session_resources",
    "reset_session",
    "resolve_capital_maximum_major",
    "resolve_concurrency_limit",
    "resume_session",
    "safe_signal_payload",
    "session_loop",
    "session_not_found",
    "signal_intent_idempotency_key",
    "signal_to_intent",
    "spawn_session_loop",
    "start_session",
    "step_session",
    "stop_session",
    "target_state",
    "validate_capital",
    "validate_timeframe",
]
