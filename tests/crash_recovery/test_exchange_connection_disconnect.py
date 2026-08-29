"""
Task 12.4 - the exchange-connection-disconnect failure mode.

Requirements 19.3, 19.4. Reuses task 12.1's shared harness (``tests/crash_recovery/
harness.py``) and its three assertions verbatim; this module supplies only its own
interruption - the exchange connection dropping - and nothing else.

WHAT IS ASSERTED, EVERY TIME
    Requirement 19.4's three, through ``harness.assert_crash_recovery_invariants``:
      (a) at most one order is created at the exchange test double per Signal,
      (b) the Signal's persisted Order_Lifecycle_State after recovery matches its true
          state at the exchange,
      (c) no duplicate or orphaned record shares its Idempotency_Key.

    Every scenario in sections 1, 2 and 3 below ends in that call.

THE ONE DECISION THIS TASK HAD TO MAKE
    "The exchange connection disconnected" is not one event on the submission path. It
    is two facts that a single network incident can present as, and the harness's own
    header says 12.4 has to pick which one it is exercising:

      * THE DROP IS REPORTED. The exchange client raises an ordinary ``Exception``
        (``harness.ExchangeConnectionLost``) and the process lives. This is the shape a
        real ccxt/HTTP client produces: connection reset, read timeout, TLS teardown
        mid-response. ``_call_execution_engine`` catches every ``Exception`` on purpose
        ("an exchange error is an OUTCOME, not a bug") and reports it as a failed
        outcome.
      * THE DROP TOOK THE PROCESS WITH IT. The connection loss is fatal - the pod is
        killed, the event loop is torn down - which the harness models as a
        ``BaseException`` for exactly the reason its header gives: a dying process runs
        no ``except Exception`` handler, so nothing is caught, nothing is written after
        the exchange call, and the Redis lock stays held until ``PROCESSING_TTL``.

    BOTH are exercised here, because both really happen. This module is what found the
    reason they used to diverge: a reported drop was written as a terminal ``FAILED``, and
    when the drop happened AFTER the venue accepted the order that stranded a live order
    behind a terminal record - ``FAILED`` has no outgoing transition, so the Requirement
    19.2 sweep returned ``RECOVERY_TERMINAL`` without ever asking the venue, and no marker
    was written either. The divergence was silent and permanent.

    ``signal_service`` now distinguishes an INDETERMINATE execution failure (no answer -
    network, timeout, transport, an unreadable report; the order MAY be live) from a
    DETERMINATE one (the venue answered and refused; no order exists). An indeterminate
    failure holds the signal at ``PENDING`` and writes no transition at all, which is
    exactly the recoverable shape the fatal reading already had - so both readings of one
    network event now converge on a record the sweep can resolve, and the only thing that
    still differs is the debris (a reported drop releases its lock and caches a verdict; a
    fatal one leaves the lock held until ``PROCESSING_TTL``).

WHAT IS REAL HERE
    ``generate_signal``, ``submit_signal``, ``recover_signal``,
    ``apply_order_lifecycle_state``, the transition gate and table, ``idempotency_key_for``
    and ``DistributedIdempotencyLayer.execute_with_idempotency`` (including its atomic Lua
    check-and-set, its result cache and its owner-token release) all run as shipped. The
    database, Redis and the venue are the doubles in ``harness.py``.

NOT TESTED HERE
    The other four failure modes (tasks 12.1, 12.2, 12.3, 12.5). The bounded check's own
    numbers - 3 attempts, 30 seconds - are pinned by task 12.1 and are not re-pinned here;
    what IS exercised here is a disconnect arriving at each point where that bound
    applies. Submission routing, the transition table's rules and the idempotency key's
    determinism belong to tasks 10.2, 1.1 and 9.1.
"""

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from tests.crash_recovery.harness import (
    AFTER_EXCHANGE_ACCEPTED,
    BEFORE_EXCHANGE_CALL,
    ExchangeConnectionLost,
    WorkerCrash,
    assert_at_most_one_order_per_signal,
    assert_crash_recovery_invariants,
    assert_no_duplicate_or_orphaned_record,
)


class ExchangeConnectionLostFatally(WorkerCrash):
    """The exchange connection dropped and took the process with it.

    Local to this module rather than added to ``harness.py``: three sibling tasks are
    running against that file concurrently, and this is one label over
    :class:`harness.WorkerCrash`'s existing semantics, not a new seam. Deriving from
    ``WorkerCrash`` (and therefore from ``BaseException``, NOT ``Exception``) is the whole
    content of the distinction this module draws - see the module header.
    """


# ══════════════════════════════════════════════════════════════════════════
# THE TWO SHAPES OF ONE NETWORK EVENT, AS ARRANGEMENTS
# ══════════════════════════════════════════════════════════════════════════


async def drop_reported_by_the_client(world, *, crash_at=AFTER_EXCHANGE_ACCEPTED, **kwargs):
    """The connection drops and the exchange client REPORTS it. The process lives.

    ``submit_signal`` therefore returns a state rather than raising, because
    ``_call_execution_engine`` catches every ``Exception`` from the execution seam.
    """
    worker = world.start_worker(
        name="reporting-worker",
        crash_at=crash_at,
        crash_with=ExchangeConnectionLost,
        **kwargs,
    )
    signal = await worker.generate()
    reached = await worker.submit(signal)
    return signal, worker, reached


async def drop_that_killed_the_process(
    world, *, crash_at=AFTER_EXCHANGE_ACCEPTED, **kwargs
):
    """The connection loss is fatal: the process goes away mid-submission."""
    worker = world.start_worker(
        name="disconnected-worker",
        crash_at=crash_at,
        crash_with=ExchangeConnectionLostFatally,
        **kwargs,
    )
    signal = await worker.generate()
    with pytest.raises(ExchangeConnectionLostFatally):
        await worker.submit(signal)
    return signal, worker


def test_the_two_readings_are_distinguished_by_base_class_and_nothing_else():
    """Pinned so the distinction this module rests on cannot be edited away quietly."""
    assert issubclass(ExchangeConnectionLost, Exception)
    assert not issubclass(ExchangeConnectionLost, WorkerCrash)

    assert issubclass(ExchangeConnectionLostFatally, BaseException)
    assert not issubclass(ExchangeConnectionLostFatally, Exception), (
        "a fatal connection loss must not be catchable by an `except Exception` on the "
        "submission path; that is the entire fidelity of this simulation"
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. THE DROP AS THE EXCHANGE CLIENT REPORTS IT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_reported_drop_before_the_venue_saw_anything_is_held_then_cleared(
    crash_world,
):
    """Nothing was sent - but the submission path cannot know that, so it holds.

    THIS TEST CHANGED WITH THE FIX, AND THE REASON IS THE POINT
        It used to assert a terminal ``FAILED`` here, on the grounds that the connection
        died before the order was offered so the venue holds nothing. That is true, and the
        TEST is what knows it: it arranged the drop at ``BEFORE_EXCHANGE_CALL``. The
        production code sees one ``ExchangeConnectionLost`` from the execution seam and
        cannot tell whether the request was delivered - the identical exception arrives when
        the drop happened after the venue accepted (the case below). Writing ``FAILED`` here
        was therefore right by luck, and the same disposition was catastrophic one test
        down.

        So the classification is asymmetric on purpose: an unrecognised failure is
        INDETERMINATE, and the signal is held at ``PENDING``. The cost is one exchange
        lookup by the sweep, which then definitively reports that the venue holds nothing
        and licenses submission under the same key. That is the "false PENDING costs a
        lookup" trade task 10.2's header already argues for, paid here.

    All three assertions hold at ``PENDING``: the venue holds nothing, and ``PENDING`` does
    not claim otherwise.
    """
    signal, worker, reached = await drop_reported_by_the_client(
        crash_world, crash_at=BEFORE_EXCHANGE_CALL
    )

    assert reached is OrderLifecycleState.PENDING
    assert crash_world.exchange.placements == [], "nothing was ever offered to the venue"
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING

    # Held, not closed: no transition was written for the in-doubt failure at all.
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=False,
        expected_state=OrderLifecycleState.PENDING,
    )

    # And the sweep is what resolves it, definitively, without placing anything.
    outcome = await worker.recover(signal)
    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert crash_world.exchange.lookups == [signal.idempotency_key]
    assert outcome.resubmission_allowed is True
    assert crash_world.reconciliation_markers(signal) == [], (
        "the venue answered, so nobody needs a human"
    )
    assert crash_world.exchange.orders == {}, "a sweep reads; it never places"


@pytest.mark.asyncio
async def test_a_reported_drop_releases_the_lock_and_caches_its_own_verdict(crash_world):
    """The contrast with a dead process, stated because the rest of section 1 turns on it.

    A REPORTED drop lets the operation return normally, so the idempotency layer stores
    the result and the lock is replaced by that cached verdict. A FATAL one leaves the
    lock held until ``PROCESSING_TTL`` (section 3's arrangement, and task 12.1's).

    The cached verdict is ``PENDING``, not ``FAILED``: an in-doubt submission is held, and
    the cache records the state the signal is actually in, so a retry absorbed by the cache
    is told the same recoverable thing the row says.
    """
    signal, _, _ = await drop_reported_by_the_client(crash_world)

    assert crash_world.redis.held_locks() == [], (
        "a process that survived the drop must not leave an in-flight lock behind"
    )
    cached = crash_world.redis.cached_results()
    assert len(cached) == 1
    (verdict,) = cached.values()
    assert verdict["result"]["order_lifecycle_state"] == "PENDING"
    assert verdict["result"]["idempotency_key"] == signal.idempotency_key


@pytest.mark.asyncio
async def test_a_reported_drop_after_the_venue_accepted_is_held_and_then_reconciled(
    crash_world,
):
    """THE FINDING THIS MODULE MADE, now the behaviour it pins. Requirement 19.4(b).

    THE SITUATION, WHICH IS AN ORDINARY ONE
        The order reached the venue and the venue accepted it. The response never made it
        back: connection reset, read timeout, TLS teardown - the classic "in-doubt order".
        The exchange client raises an ordinary ``Exception``. The process is fine.

    WHAT THE PRODUCTION CODE USED TO DO WITH IT
        ``_call_execution_engine`` caught it (deliberately - "an exchange error is an
        OUTCOME, not a bug"), reported ``accepted=False, refused=False``,
        ``resolve_execution_state`` read that as ``FAILED``, and ``submit_signal``
        persisted ``PENDING -> FAILED``. ``FAILED`` is one of Requirement 16.1's four
        terminal states and ``ORDER_LIFECYCLE_TRANSITIONS[FAILED]`` is empty, so
        ``recover_signal`` returned ``RECOVERY_TERMINAL`` without querying the venue at
        all. Assertions (a) and (c) held; (b) did not, and no marker was written either,
        because ``_no_definitive_answer`` was never reached. The record said ``FAILED``,
        the venue held a live order, and nothing would ever revisit it.

    WHAT IT DOES NOW
        The failure is classified INDETERMINATE - no answer came back, so the order may be
        live - and ``resolve_execution_state`` reports ``PENDING``, the state the signal is
        already in. So no transition is written, the record stops at the last thing that
        was actually established, and the signal sits exactly where the Requirement 19.2
        sweep can resolve it. The sweep then queries the venue by the same
        Idempotency_Key, finds the live order and reconciles ``PENDING -> SUBMITTED``.

        All three of Requirement 19.4's assertions then hold in their STRICT form: the
        record equals the venue's own state, one order exists, and the history is one chain.
    """
    signal, worker, reached = await drop_reported_by_the_client(
        crash_world, crash_at=AFTER_EXCHANGE_ACCEPTED
    )
    key = signal.idempotency_key

    # The venue took the order, and still holds it.
    assert crash_world.exchange.accepted_placements_for(key), (
        "the drop was supposed to happen AFTER the venue accepted the order"
    )
    assert crash_world.exchange.true_state(key) is OrderLifecycleState.SUBMITTED

    # The record LAGS reality rather than contradicting it, which is what makes it
    # recoverable. Nothing terminal, and no transition written for the in-doubt failure.
    assert reached is OrderLifecycleState.PENDING
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history

    # (a) and (c) held even before the fix; they still do, and (b) is not yet strict-true,
    # because the sweep has not run. What is true already is that nothing OVERCLAIMS.
    assert_at_most_one_order_per_signal(crash_world, signal, expect_order=True)
    assert_no_duplicate_or_orphaned_record(crash_world, signal)

    # The sweep is what closes the gap: FAILED is no longer in the way, so the venue IS
    # asked, by the same Idempotency_Key the submission used.
    outcome = await worker.recover(signal)
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert crash_world.exchange.lookups == [key]
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.exchange_order_id == "X-1"
    assert outcome.resubmission_allowed is False
    assert outcome.requires_manual_reconciliation is False

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
    assert crash_world.signal_row(signal)["order_id"] == "X-1"
    assert [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ] == [(None, "GENERATED"), ("GENERATED", "PENDING"), ("PENDING", "SUBMITTED")]


@pytest.mark.asyncio
async def test_a_network_retry_after_a_reported_drop_never_reaches_the_venue_again(
    crash_world,
):
    """Requirement 19.1's "network retry ... produces at most one order", directly.

    The caller sees a failure and retries the same Signal - the ordinary reaction to a
    dropped connection, and the one Requirement 19.1 names first. The Idempotency_Key is
    derived from ``signal.id``, so the retry computes the same key, finds the layer's
    cached verdict and returns it. The execution component is never called, so the venue
    is never offered a second order.

    Note what the retry is told: ``PENDING``, the in-doubt state, which is the honest answer
    and the one that routes this signal to the recovery sweep rather than closing it.
    """
    signal, first, _ = await drop_reported_by_the_client(crash_world)
    assert len(crash_world.exchange.placements) == 1

    retry = crash_world.start_worker(name="retrying-worker")
    resumed = await retry.reload(signal)
    reached = await retry.submit(resumed)

    assert reached is OrderLifecycleState.PENDING, "the cached verdict, returned as-is"
    assert retry.execution.calls == [], (
        "the retry must be absorbed by the Idempotency_Key's cached result, not routed "
        "to the venue a second time"
    )
    assert retry.risk.calls == [], "nor re-validated: the at-most-once body never ran"
    assert len(crash_world.exchange.placements) == 1

    assert_at_most_one_order_per_signal(crash_world, signal, expect_order=True)
    assert_no_duplicate_or_orphaned_record(crash_world, signal)


# ══════════════════════════════════════════════════════════════════════════
# 2. THE VENUE UNREACHABLE WHEN THE ORDER IS OFFERED
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unreachable_venue_at_submission_places_nothing_and_says_so(crash_world):
    """A hard outage - ``exchange.reachable = False`` - at the moment of submission.

    The order is offered, the venue is not there, nothing is created. THIS TEST ALSO
    CHANGED WITH THE FIX, and for the same reason as section 1's first: "the venue was not
    reachable" and "the venue accepted and the response was lost" both arrive at
    ``_call_execution_engine`` as an ordinary exception from the same seam, and a request
    that fails to connect is indistinguishable there from one that failed while reading the
    reply. So the failure is INDETERMINATE and the signal is held at ``PENDING`` rather
    than closed as ``FAILED``.

    All three assertions still hold, without relaxation: the venue holds nothing, and
    ``PENDING`` does not claim it holds something.
    """
    crash_world.exchange.reachable = False
    worker = crash_world.start_worker(name="worker")
    signal = await worker.generate()

    reached = await worker.submit(signal)

    assert reached is OrderLifecycleState.PENDING
    assert len(crash_world.exchange.placements) == 1, "it was offered"
    assert crash_world.exchange.placements[0]["accepted"] is False, "and refused by absence"
    assert crash_world.exchange.orders == {}

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=False,
        expected_state=OrderLifecycleState.PENDING,
    )


@pytest.mark.asyncio
async def test_a_venue_that_comes_back_is_not_offered_the_order_again_by_itself(
    crash_world,
):
    """Recovery is a read, not a repair. Requirement 19.2's "SHALL NOT resubmit".

    The outage ends and the sweep asks. It does NOT quietly place the order that never went
    out: it reports that the key is unused and hands the decision back. Sending an order is
    the deployment's business, not the recovery path's, and the difference between
    "resubmission is permitted" and "resubmitted" is the whole content of this test.
    """
    crash_world.exchange.reachable = False
    worker = crash_world.start_worker(name="worker")
    signal = await worker.generate()
    await worker.submit(signal)

    crash_world.exchange.reachable = True
    outcome = await worker.recover(signal)

    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcome.resubmission_allowed is True, (
        "the venue definitively holds nothing under this key, which is the one fact that "
        "licenses another submission"
    )
    assert crash_world.exchange.orders == {}, (
        "and the sweep itself placed nothing - it reads, it does not repair"
    )
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=False,
        expected_state=OrderLifecycleState.PENDING,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. THE DROP THAT TOOK THE PROCESS WITH IT - AND THE RECONNECT
# ══════════════════════════════════════════════════════════════════════════
#
# This is where Requirement 19.3's "THE Signal SHALL resume from its last persisted
# Order_Lifecycle_State" does its work. The record is left at PENDING because a dead process
# writes nothing at all, so the sweep has something to reconcile, and the tests below vary
# only what the reconnected connection can answer. Section 1's reported drop now arrives at
# the same PENDING by a different route (an indeterminate failure writes nothing either),
# which is why the two readings of one network event no longer diverge in outcome - only in
# the debris they leave in Redis.


@pytest.mark.asyncio
async def test_a_fatal_drop_leaves_the_record_recoverable_at_pending(crash_world):
    """The premise of this section, pinned before anything recovers it."""
    signal, _ = await drop_that_killed_the_process(crash_world)
    key = signal.idempotency_key

    assert crash_world.exchange.true_state(key) is OrderLifecycleState.SUBMITTED
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING, (
        "a fatal drop must NOT be caught and written as a terminal FAILED; a dead process "
        "runs no handler, so nothing after the exchange call is written at all"
    )
    assert crash_world.signal_row(signal).get("order_id") is None
    assert crash_world.redis.held_locks(), (
        "a dead process releases nothing; only PROCESSING_TTL does"
    )
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history


@pytest.mark.asyncio
async def test_a_reconnected_client_reconciles_the_record_to_the_venues_answer(
    crash_world,
):
    """The connection is back. The sweep asks by Idempotency_Key and reconciles forward."""
    signal, _ = await drop_that_killed_the_process(crash_world)

    reconnected = crash_world.start_worker(name="reconnected-worker")
    outcome = await reconnected.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.resubmission_allowed is False
    assert outcome.exchange_order_id == "X-1"
    assert crash_world.exchange.lookups == [signal.idempotency_key]

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_fill_that_happened_while_the_connection_was_down_is_reconciled(
    crash_world,
):
    """The venue kept trading while nobody could see it. The record catches all the way up.

    Through ``SUBMITTED``, because the order really did pass through it - the same
    ``through_intermediate_states`` route ``recover_signal`` takes when the transition
    table itself finds a multi-hop path.
    """
    signal, _ = await drop_that_killed_the_process(crash_world)
    crash_world.exchange.fill(signal.idempotency_key)

    reconnected = crash_world.start_worker(name="reconnected-worker")
    outcome = await reconnected.recover(signal)

    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.EXECUTED,
    )
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
        ("SUBMITTED", "EXECUTED"),
    ], history


@pytest.mark.asyncio
async def test_a_connection_that_flaps_during_the_sweep_is_retried_then_reconciled(
    crash_world,
):
    """The disconnect outlasts the process but not the sweep. Requirement 19.2's bound.

    Two lookups fail on the still-broken connection, the third answers. Three attempts is
    a ceiling, not a quota, and the waits alone do not consume the 30-second budget.
    """
    signal, _ = await drop_that_killed_the_process(crash_world)
    crash_world.exchange.fail_lookups = 2

    reconnected = crash_world.start_worker(name="flapping-connection-worker")
    outcome = await reconnected.recover(signal)

    assert outcome.attempts == 3
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert sum(crash_world.clock.sleeps) < svc.RECOVERY_TOTAL_BUDGET_SECONDS
    assert crash_world.reconciliation_markers(signal) == [], (
        "an outage that resolved inside the budget needs no human"
    )

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_disconnect_that_outlasts_the_sweep_marks_the_signal_and_writes_nothing(
    crash_world,
):
    """The connection is still down when the bounded check runs out. Requirement 19.2.

    Assertion (b) is checked in its ``unresolvable_if_marked`` form, which is the honest
    reading and the harness's one relaxation: with the venue unreachable its true state is
    unknowable, so what can be demanded is that the platform DECLARES the divergence and
    never overclaims. ``PENDING`` lags the venue's ``SUBMITTED``; it does not lead it.
    """
    signal, _ = await drop_that_killed_the_process(crash_world)
    crash_world.exchange.reachable = False

    reconnected = crash_world.start_worker(name="still-disconnected-worker")
    outcome = await reconnected.recover(signal)

    assert outcome.status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED
    assert outcome.attempts == svc.RECOVERY_MAX_ATTEMPTS
    assert len(crash_world.exchange.lookups) == svc.RECOVERY_MAX_ATTEMPTS
    assert outcome.requires_manual_reconciliation is True
    assert outcome.marked is True
    assert outcome.resubmission_allowed is False

    # Nothing was written to the state: nothing confirmed it (Requirements 19.2, 19.3).
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history

    (marker,) = crash_world.reconciliation_markers(signal)
    assert marker["event_data"]["idempotency_key"] == signal.idempotency_key
    assert marker["event_data"]["order_lifecycle_state"] == "PENDING"

    assert_crash_recovery_invariants(
        crash_world, signal, expect_order=True, unresolvable_if_marked=True
    )


@pytest.mark.asyncio
async def test_the_signal_is_reconciled_exactly_once_when_the_connection_returns(
    crash_world,
):
    """The full arc of this failure mode, end to end, and the strongest test here.

    Drop -> sweep meets a dead connection and marks the signal -> connection returns ->
    the next sweep reconciles from the venue's own record. Assertion (b) is then checked
    in its STRICT form (no relaxation), because the venue can answer again, and exactly
    one order exists at the end of all of it.
    """
    signal, _ = await drop_that_killed_the_process(crash_world)
    crash_world.exchange.reachable = False

    first = crash_world.start_worker(name="sweep-while-disconnected")
    assert (
        await first.recover(signal)
    ).status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED
    assert len(crash_world.reconciliation_markers(signal)) == 1

    # The connection comes back, and the venue partially filled while it was gone.
    crash_world.exchange.reachable = True
    crash_world.exchange.fill(signal.idempotency_key, filled=0.1)  # of 0.25

    second = crash_world.start_worker(name="sweep-after-reconnect")
    outcome = await second.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.PARTIALLY_EXECUTED
    assert outcome.requires_manual_reconciliation is False
    assert len(crash_world.reconciliation_markers(signal)) == 1, (
        "the marker is a record that the signal was once unresolvable; the reconnected "
        "sweep resolves the STATE and does not append a second marker"
    )

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.PARTIALLY_EXECUTED,
    )


@pytest.mark.asyncio
async def test_a_reconnected_client_with_no_lookup_leaves_the_persisted_state_standing(
    crash_world,
):
    """Requirement 19.2's "or does not support such a lookup", after a disconnect.

    The connection is restored but this venue cannot be asked about an order by client
    order identifier. Nothing is queried, nothing is written, nothing is resubmitted - and
    the signal is still marked, because an order may be live behind it and no automated
    path will ever resolve that.
    """
    signal, _ = await drop_that_killed_the_process(crash_world)
    crash_world.exchange.supports_client_order_id_lookup = False

    reconnected = crash_world.start_worker(name="reconnected-without-lookup")
    outcome = await reconnected.recover(signal)

    assert outcome.status == svc.RECOVERY_PERSISTED_STATE_AUTHORITATIVE
    assert outcome.attempts == 0
    assert crash_world.exchange.lookups == []
    assert outcome.resubmission_allowed is False
    assert outcome.order_lifecycle_state is OrderLifecycleState.PENDING
    assert crash_world.reconciliation_markers(signal)

    assert_crash_recovery_invariants(
        crash_world, signal, expect_order=True, unresolvable_if_marked=True
    )


@pytest.mark.asyncio
async def test_a_disconnect_before_the_venue_saw_anything_leaves_the_signal_submittable(
    crash_world,
):
    """The other side of the window: the ONE case where resubmission is permitted.

    The connection died before the order was offered, and the reconnected client
    definitively reports that the venue holds nothing for this Idempotency_Key. "The key
    was demonstrably never used" is a different fact from "we could not find out", and
    only this one licenses another submission - which then produces exactly one order.
    """
    signal, _ = await drop_that_killed_the_process(
        crash_world, crash_at=BEFORE_EXCHANGE_CALL
    )
    assert crash_world.exchange.orders == {}

    crash_world.redis.advance_past_processing_lock()
    reconnected = crash_world.start_worker(name="reconnected-worker")
    outcome = await reconnected.recover(signal)

    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcome.resubmission_allowed is True
    assert crash_world.reconciliation_markers(signal) == []
    assert_crash_recovery_invariants(crash_world, signal, expect_order=False)

    resumed = await reconnected.reload(signal)
    reached = await reconnected.submit(resumed)

    assert reached is OrderLifecycleState.SUBMITTED
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_no_second_order_when_a_resumed_worker_ignores_the_sweeps_refusal(
    crash_world,
):
    """Defence in depth after a disconnect, asserted rather than assumed.

    A caller that ignores ``resubmission_allowed`` is the duplicate-order bug Requirement
    19.2 exists to prevent. After reconciliation the record says ``SUBMITTED``, and
    ``SUBMITTED -> PENDING`` is not an edge of Requirement 16.4's table, so the transition
    gate refuses before the execution component - and therefore the reconnected exchange
    connection - is reached at all.
    """
    from backend_app.backend.order_lifecycle_state import OrderLifecycleRejected

    signal, _ = await drop_that_killed_the_process(crash_world)
    reconnected = crash_world.start_worker(name="reconnected-worker")
    await reconnected.recover(signal)

    crash_world.redis.advance_past_processing_lock()
    resumed = await reconnected.reload(signal)
    assert resumed.order_lifecycle_state is OrderLifecycleState.SUBMITTED

    with pytest.raises(OrderLifecycleRejected):
        await reconnected.submit(resumed)

    assert reconnected.execution.calls == []
    assert len(crash_world.exchange.placements) == 1
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
