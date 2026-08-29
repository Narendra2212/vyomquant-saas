"""
Task 12.1 - the worker-crash-mid-submission failure mode.

Requirements 19.2, 19.4. The failure mode is the narrowest and nastiest window on the
whole trading path: the exchange has accepted the order and the process dies before it can
record that it did. Task 10.2's own header defends leaving that window open ("a false
PENDING costs a lookup; a false SUBMITTED loses a trade and lies in the audit log"), and
that defence is only sound if something actually performs the lookup. This module is where
that claim is cashed.

WHAT IS ASSERTED, EVERY TIME
    Requirement 19.4's three, through
    ``harness.assert_crash_recovery_invariants``:
      (a) at most one order is created at the exchange test double per Signal,
      (b) the Signal's post-recovery Order_Lifecycle_State matches its true state at the
          exchange,
      (c) no duplicate or orphaned record shares its Idempotency_Key.

HOW THE CRASH IS SIMULATED, AND WHY THAT SHAPE
    :class:`harness.WorkerCrash` derives from ``BaseException``. A killed process does not
    run ``except Exception`` handlers, so a crash modelled as an ``Exception`` would be
    caught by ``_call_execution_engine``, classified as an execution failure, and written
    as ``FAILED`` - a terminal state the signal never reached - and the idempotency
    layer's own ``except Exception`` would release a lock that a dead worker would still
    be holding. The test would then be exercising error handling. With ``BaseException``,
    the submission path behaves exactly as it does when the process really goes away:
    nothing is caught, nothing is written after the exchange call, and the Redis lock
    stays held until ``PROCESSING_TTL`` expires it.

WHAT IS REAL HERE
    ``generate_signal``, ``submit_signal``, ``recover_signal``,
    ``apply_order_lifecycle_state``, the transition gate and table, ``idempotency_key_for``
    and ``DistributedIdempotencyLayer.execute_with_idempotency`` (including its atomic Lua
    check-and-set) all run as shipped. The database, Redis and the venue are the doubles
    in ``harness.py``, because this environment has neither PostgreSQL nor Redis.

NOT TESTED HERE
    The other four failure modes - tasks 12.2 (API process restart), 12.3 (WebSocket
    disconnect), 12.4 (exchange connection disconnect), 12.5 (database/queue restart) -
    each reuse this module's harness and its three assertions against their own
    interruption. Submission routing, the transition gate's own rules and the
    idempotency key's determinism belong to tasks 10.2, 1.1 and 9.1 and are covered by
    their suites.
"""

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleRejected,
    OrderLifecycleState,
)
from backend_app.core.distributed_idempotency import DuplicateOrderError
from tests.crash_recovery.harness import (
    AFTER_EXCHANGE_ACCEPTED,
    BEFORE_EXCHANGE_CALL,
    WorkerCrash,
    assert_at_most_one_order_per_signal,
    assert_crash_recovery_invariants,
    assert_no_duplicate_or_orphaned_record,
)


async def crash_mid_submission(world, **worker_kwargs):
    """Generate a signal, then die mid-submission. Returns the signal and the dead worker.

    The shared arrangement for this whole module: one signal, one submission attempt, one
    process that does not come back.
    """
    worker = world.start_worker(
        name="crashing-worker",
        crash_at=worker_kwargs.pop("crash_at", AFTER_EXCHANGE_ACCEPTED),
        crash_with=WorkerCrash,
        **worker_kwargs,
    )
    signal = await worker.generate()
    with pytest.raises(WorkerCrash):
        await worker.submit(signal)
    return signal, worker


# ══════════════════════════════════════════════════════════════════════════
# 1. THE WINDOW ITSELF - what the crash actually leaves behind
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_crash_leaves_a_live_order_and_a_signal_still_at_pending(crash_world):
    """The premise of the whole task, pinned before anything recovers it.

    If this stopped being true - if the crash left the row at ``SUBMITTED``, or left no
    order at the venue - every other test in this module would be exercising a different
    situation than the one Requirement 19.2 is about.
    """
    signal, _ = await crash_mid_submission(crash_world)
    key = signal.idempotency_key

    # The venue took the order.
    assert crash_world.exchange.accepted_placements_for(key), (
        "the crash was supposed to happen AFTER the exchange accepted the order"
    )
    assert crash_world.exchange.true_state(key) is OrderLifecycleState.SUBMITTED

    # The record does not know that yet, and says something recoverable rather than
    # something false: PENDING, not SUBMITTED.
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    # ``to_row`` omits the reference entirely rather than writing NULL, so an absent key
    # here is the record saying "no order reference has arrived", which is the truth.
    assert crash_world.signal_row(signal).get("order_id") is None

    # And the dead worker never released its lock, which is what a dead process does.
    assert crash_world.redis.held_locks(), (
        "a crashed worker's in-flight lock must still be held; only PROCESSING_TTL "
        "releases it"
    )


@pytest.mark.asyncio
async def test_the_crash_records_the_pending_transition_and_nothing_after_it(crash_world):
    """The audit log stops exactly where the process did. Requirement 16.7."""
    signal, _ = await crash_mid_submission(crash_world)

    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history


# ══════════════════════════════════════════════════════════════════════════
# 2. THE SWEEP - Requirement 19.2, and Requirement 19.4's three assertions
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_reconciles_the_record_to_the_exchanges_own_answer(crash_world):
    """The task, in one test: restart, ask the venue by Idempotency_Key, reconcile."""
    signal, _ = await crash_mid_submission(crash_world)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.resubmission_allowed is False
    assert outcome.attempts == 1
    assert outcome.exchange_order_id == "X-1"
    assert not outcome.requires_manual_reconciliation

    # It asked by the SAME derived key the submission used - which is the entire
    # mechanism, so it is asserted rather than assumed.
    assert crash_world.exchange.lookups == [signal.idempotency_key]

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_recovery_reconciles_a_fill_that_happened_while_the_worker_was_down(
    crash_world,
):
    """The order filled with nobody watching. The record has to catch up, through
    SUBMITTED, because that is where the order really went."""
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.fill(signal.idempotency_key)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.EXECUTED,
    )

    # The history says the signal passed through SUBMITTED, because it did.
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
async def test_recovery_reconciles_a_partial_fill_as_a_partial_fill(crash_world):
    """A half-filled order is neither SUBMITTED nor EXECUTED, and is not rounded to
    either."""
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.fill(signal.idempotency_key, filled=0.1)  # of 0.25

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.order_lifecycle_state is OrderLifecycleState.PARTIALLY_EXECUTED
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.PARTIALLY_EXECUTED,
    )


@pytest.mark.asyncio
async def test_recovery_writes_the_exchanges_order_id_onto_the_record(crash_world):
    """Requirement 19.3's third clause: the record must not be left referencing an order
    identifier that does not match the exchange's own."""
    signal, _ = await crash_mid_submission(crash_world)
    assert crash_world.signal_row(signal).get("order_id") is None

    restarted = crash_world.start_worker(name="restarted-worker")
    await restarted.recover(signal)

    exchange_order = crash_world.exchange.orders_for(signal.idempotency_key)[0]
    assert crash_world.signal_row(signal)["order_id"] == exchange_order["order_id"]


# ══════════════════════════════════════════════════════════════════════════
# 3. NO SECOND ORDER, WHICH IS THE POINT OF ALL OF IT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_refuses_resubmission_when_an_order_already_exists(crash_world):
    """Requirement 19.2: "SHALL NOT resubmit an order for a Signal whose Idempotency_Key
    was already used"."""
    signal, _ = await crash_mid_submission(crash_world)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.resubmission_allowed is False
    assert outcome.order_exists_at_exchange


@pytest.mark.asyncio
async def test_a_resumed_worker_that_submits_anyway_still_creates_no_second_order(
    crash_world,
):
    """Defence in depth, asserted rather than assumed.

    A caller that ignores ``resubmission_allowed`` is the bug Requirement 19.2 exists to
    prevent, so what happens when one does is worth knowing: after recovery the record
    says ``SUBMITTED``, and ``SUBMITTED -> PENDING`` is not an edge of Requirement 16.4's
    table, so the transition gate refuses the resubmission before the execution component
    is ever reached. The venue is never called a second time, and all three invariants
    still hold.

    The dead worker's Redis lock is expired first (``PROCESSING_TTL``, 60s) so that this
    test measures the state machine rather than the lock - the lock's own protection
    during those 60 seconds is the layer's, and task 9.1's suite covers it.
    """
    signal, _ = await crash_mid_submission(crash_world)
    restarted = crash_world.start_worker(name="restarted-worker")
    await restarted.recover(signal)

    crash_world.redis.advance_past_processing_lock()
    resumed = await restarted.reload(signal)
    assert resumed.order_lifecycle_state is OrderLifecycleState.SUBMITTED

    with pytest.raises(OrderLifecycleRejected) as refusal:
        await restarted.submit(resumed)
    assert "SUBMITTED" in str(refusal.value)

    assert restarted.execution.calls == [], (
        "the execution component must never be reached for a signal whose order already "
        "exists"
    )
    assert len(crash_world.exchange.placements) == 1

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_the_venue_itself_refuses_a_second_order_under_the_same_key(crash_world):
    """The outermost of the three defences behind assertion (a).

    Redis first, ``uq_signals_idempotency_key`` second, the venue's own client-order-id
    check third. This test removes the first two - the lock is expired and the resumed
    signal is deliberately handed back at ``PENDING`` as though no recovery had run - so
    that the third is what has to hold.
    """
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.redis.advance_past_processing_lock()

    resumed = crash_world.start_worker(name="resumed-without-sweep")
    at_pending = await resumed.reload(signal)
    assert at_pending.order_lifecycle_state is OrderLifecycleState.PENDING

    reached = await resumed.submit(at_pending)

    assert len(crash_world.exchange.placements) == 2, "the second attempt did reach the venue"
    assert_at_most_one_order_per_signal(crash_world, signal, expect_order=True)
    assert_no_duplicate_or_orphaned_record(crash_world, signal)

    # AND THE COST OF SKIPPING THE SWEEP, STATED RATHER THAN LEFT IMPLIED.
    # The venue refused the duplicate, so (a) and (c) still hold - but the refusal is
    # indistinguishable, to the execution path, from any other rejected submission, so the
    # signal is now recorded as FAILED while a live order sits at the venue. That is
    # assertion (b) broken, and it is unrecoverable: FAILED is terminal, so a sweep run
    # afterwards has nothing left to reconcile. This is precisely why Requirement 19.2
    # orders the sweep BEFORE any further action rather than as a repair afterwards, and
    # it is why ``assert_crash_recovery_invariants`` is deliberately NOT called here.
    assert reached is OrderLifecycleState.FAILED
    assert crash_world.exchange.true_state(signal.idempotency_key) is (
        OrderLifecycleState.SUBMITTED
    )
    too_late = await resumed.recover(signal)
    assert too_late.status == svc.RECOVERY_TERMINAL


@pytest.mark.asyncio
async def test_the_durable_unique_index_refuses_a_second_row_for_the_same_key(
    crash_world,
):
    """Assertion (c)'s database-level backstop, exercised directly.

    ``generate_signal(signal_id=...)`` exists for a recovery sweep re-deriving a known
    signal, and it is the one call that could ever produce a second row carrying an
    existing Idempotency_Key. Migration 005b's ``uq_signals_idempotency_key`` is what
    stops it, and ``signal_service`` is required to translate that ``23505`` into
    ``DuplicateOrderError`` rather than a 500.
    """
    signal, _ = await crash_mid_submission(crash_world)

    restarted = crash_world.start_worker(name="restarted-worker")
    with pytest.raises(DuplicateOrderError):
        await svc.generate_signal(
            crash_world.deployment_row(),
            crash_world.action_output(),
            sb=restarted.sb,
            signal_id=signal.id,
        )

    assert len(crash_world.signal_rows_for_key(signal.idempotency_key)) == 1
    assert_no_duplicate_or_orphaned_record(crash_world, signal)


# ══════════════════════════════════════════════════════════════════════════
# 4. A CRASH THAT HAPPENED BEFORE THE VENUE SAW ANYTHING
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_crash_before_the_exchange_call_leaves_the_signal_submittable(crash_world):
    """The other side of the window, and the ONE case where resubmission is permitted.

    The venue is asked and definitively answers that it holds nothing for this key, so the
    key was demonstrably never used - which is a different fact from "we could not find
    out", and only this one licenses another submission.
    """
    signal, _ = await crash_mid_submission(crash_world, crash_at=BEFORE_EXCHANGE_CALL)
    assert not crash_world.exchange.orders

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcome.resubmission_allowed is True
    assert outcome.order_lifecycle_state is OrderLifecycleState.PENDING
    assert not outcome.requires_manual_reconciliation
    assert crash_world.reconciliation_markers(signal) == [], (
        "a signal with no order behind it needs no human"
    )

    assert_crash_recovery_invariants(crash_world, signal, expect_order=False)


@pytest.mark.asyncio
async def test_the_permitted_resubmission_produces_exactly_one_order(crash_world):
    """And then the resumed worker does submit - once, under the same key."""
    signal, _ = await crash_mid_submission(crash_world, crash_at=BEFORE_EXCHANGE_CALL)
    crash_world.redis.advance_past_processing_lock()

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)
    assert outcome.resubmission_allowed is True

    resumed = await restarted.reload(signal)
    reached = await restarted.submit(resumed)

    assert reached is OrderLifecycleState.SUBMITTED
    assert crash_world.exchange.accepted_placements_for(signal.idempotency_key)[0][
        "client_order_id"
    ] == signal.idempotency_key
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. THE BOUNDS ON THE CHECK (Requirement 19.2: 3 attempts, 30 seconds)
# ══════════════════════════════════════════════════════════════════════════


def test_the_bounds_are_the_requirements_own_numbers():
    """Pinned so a later edit to the defaults is a test failure, not a silent widening."""
    assert svc.RECOVERY_MAX_ATTEMPTS == 3
    assert svc.RECOVERY_TOTAL_BUDGET_SECONDS == 30.0
    assert (
        svc.RECOVERY_RETRY_DELAY_SECONDS * (svc.RECOVERY_MAX_ATTEMPTS - 1)
        < svc.RECOVERY_TOTAL_BUDGET_SECONDS
    ), "the waits alone must not be able to exhaust the budget"


@pytest.mark.asyncio
async def test_a_transient_outage_is_retried_and_then_reconciled(crash_world):
    """Two failed lookups, then an answer: 3 attempts is a ceiling, not a quota."""
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.fail_lookups = 2

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.attempts == 3
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert crash_world.clock.sleeps == [
        svc.RECOVERY_RETRY_DELAY_SECONDS,
        svc.RECOVERY_RETRY_DELAY_SECONDS,
    ]
    assert sum(crash_world.clock.sleeps) < svc.RECOVERY_TOTAL_BUDGET_SECONDS
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_an_unreachable_exchange_stops_at_three_attempts_and_marks_the_signal(
    crash_world,
):
    """Requirement 19.2's last clause. The record is not moved, the signal is marked, and
    nothing is resubmitted.

    Assertion (b) is checked in its unresolvable form here, and that is the honest
    reading: with the venue unreachable its true state is unknowable, so what Requirement
    19.4 can demand is that the platform declares the divergence (a marker exists) and
    never overclaims (``PENDING`` lags ``SUBMITTED``; it does not lead it). See
    ``harness.assert_persisted_state_matches_exchange`` for why that relaxation is
    opt-in.
    """
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.reachable = False

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED
    assert outcome.attempts == svc.RECOVERY_MAX_ATTEMPTS
    assert len(crash_world.exchange.lookups) == svc.RECOVERY_MAX_ATTEMPTS
    assert outcome.requires_manual_reconciliation is True
    assert outcome.marked is True
    assert outcome.resubmission_allowed is False
    assert outcome.order_lifecycle_state is OrderLifecycleState.PENDING

    # The state was NOT written to - nothing confirmed it (Requirements 19.2, 19.3).
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history

    # And a human has been told, once, with the key and the stuck state on the record.
    (marker,) = crash_world.reconciliation_markers(signal)
    assert marker["event_data"]["idempotency_key"] == signal.idempotency_key
    assert marker["event_data"]["order_lifecycle_state"] == "PENDING"
    assert marker["event_data"]["attempts"] == svc.RECOVERY_MAX_ATTEMPTS

    assert_crash_recovery_invariants(
        crash_world, signal, expect_order=True, unresolvable_if_marked=True
    )


@pytest.mark.asyncio
async def test_the_thirty_second_budget_cuts_the_attempts_short(crash_world):
    """The second bound, which is the one that is easy to leave unenforced.

    With a wait long enough that a third attempt would fall outside the window, the check
    stops at two rather than overrunning - and the budget is measured across the whole
    check, waits included, not per attempt.
    """
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.reachable = False
    started = crash_world.clock.monotonic()

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal, retry_delay_seconds=20.0)

    assert outcome.attempts == 2
    assert len(crash_world.exchange.lookups) == 2
    assert crash_world.clock.monotonic() - started <= svc.RECOVERY_TOTAL_BUDGET_SECONDS
    assert crash_world.clock.sleeps == [20.0, 10.0], (
        "the last wait must be shortened to what is left of the budget, never overrun it"
    )
    assert outcome.status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_a_venue_with_no_lookup_leaves_the_persisted_state_authoritative(
    crash_world,
):
    """Requirement 19.2's "or does not support such a lookup".

    Nothing is queried, nothing is written, nothing is resubmitted - and the signal is
    still marked, because an order may be live behind it and no automated path will ever
    resolve that. See the task 12 section header in ``signal_service`` for why the marker
    is written here even though the requirement's marking clause names only the
    exhausted-attempts case.
    """
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.supports_client_order_id_lookup = False

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

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
async def test_the_marker_is_written_once_however_many_times_a_worker_restarts(
    crash_world,
):
    """A crash loop must not append one marker per restart."""
    signal, _ = await crash_mid_submission(crash_world)
    crash_world.exchange.reachable = False

    for attempt in range(3):
        worker = crash_world.start_worker(name=f"restart-{attempt}")
        outcome = await worker.recover(signal)
        assert outcome.marked is True

    assert len(crash_world.reconciliation_markers(signal)) == 1


# ══════════════════════════════════════════════════════════════════════════
# 6. WHAT THE SWEEP WILL NOT DO
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_never_moves_a_state_backwards(crash_world):
    """Requirement 19.3: no in-flight Signal's persisted state is overwritten with an
    earlier one.

    The record already says ``EXECUTED``; a stale venue read reports the order as still
    open. Nothing is written, because ``lifecycle_path`` finds no forward route - the
    guarantee holds structurally rather than through a comparison someone remembered to
    write.
    """
    signal, _ = await crash_mid_submission(crash_world)
    restarted = crash_world.start_worker(name="restarted-worker")

    # Bring the record all the way forward first.
    crash_world.exchange.fill(signal.idempotency_key)
    await restarted.recover(signal)
    assert crash_world.persisted_state(signal) is OrderLifecycleState.EXECUTED
    transitions_before = len(crash_world.transitions(signal))

    # Now the venue answers with a stale, earlier picture.
    crash_world.exchange.orders[signal.idempotency_key]["status"] = "open"
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_ALREADY_CONSISTENT
    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED
    assert crash_world.persisted_state(signal) is OrderLifecycleState.EXECUTED
    assert len(crash_world.transitions(signal)) == transitions_before


@pytest.mark.asyncio
async def test_recovery_does_nothing_to_a_signal_already_in_a_terminal_state(crash_world):
    """A terminal signal has nothing in flight, so the sweep does not query, write or
    resubmit."""
    worker = crash_world.start_worker(name="worker", crash_at=None)
    signal = await worker.generate()
    worker.execution.order_status = "cancelled"
    await worker.submit(signal)
    assert crash_world.persisted_state(signal) is OrderLifecycleState.CANCELLED

    outcome = await worker.recover(signal)

    assert outcome.status == svc.RECOVERY_TERMINAL
    assert outcome.attempts == 0
    assert crash_world.exchange.lookups == []
    assert outcome.resubmission_allowed is False
    assert not outcome.requires_manual_reconciliation


@pytest.mark.asyncio
async def test_the_sweep_reports_per_signal_and_one_failure_does_not_abort_the_rest(
    crash_world,
):
    """``recover_in_flight_signals`` is the plural shape a restart actually has."""
    first, _ = await crash_mid_submission(crash_world)
    second_worker = crash_world.start_worker(
        name="second-crashing-worker", crash_at=BEFORE_EXCHANGE_CALL
    )
    second = await second_worker.generate()
    with pytest.raises(WorkerCrash):
        await second_worker.submit(second)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcomes = await svc.recover_in_flight_signals(
        [first, second],
        exchange=crash_world.exchange,
        sb=restarted.sb,
        sleep=crash_world.clock.sleep,
        monotonic=crash_world.clock.monotonic,
    )

    assert set(outcomes) == {first.id, second.id}
    assert outcomes[first.id].status == svc.RECOVERY_RECONCILED
    assert outcomes[first.id].resubmission_allowed is False
    assert outcomes[second.id].status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcomes[second.id].resubmission_allowed is True

    assert_crash_recovery_invariants(
        crash_world,
        first,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
    assert_crash_recovery_invariants(crash_world, second, expect_order=False)
