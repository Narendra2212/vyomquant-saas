"""
Task 12.2 - the API-process-restart failure mode.

Requirements 19.3, 19.4. The shared harness, the shared world and the shared three
assertions all come from ``tests/crash_recovery/harness.py`` (task 12.1); this module
supplies only its own interruption and the one thing that interruption changes.

WHAT MAKES AN API PROCESS RESTART A DIFFERENT FAILURE MODE FROM A WORKER CRASH
    A worker crash (task 12.1) loses a process that nobody was waiting on: the runtime
    notices on restart, sweeps, and reconciles. An API process restart loses a process
    that a CALLER was waiting on, and that difference produces the three facts this
    module is about:

      1. THE REQUEST IS NEVER ANSWERED. The caller cannot tell "the order was placed and
         the response was lost" from "nothing happened", so it does the only thing it
         can: it retries the same request.
      2. THE RETRY ARRIVES IMMEDIATELY, at a DIFFERENT process. Immediately means while
         the dead process's ``processing:`` lock is still held - only ``PROCESSING_TTL``
         releases it, 60 seconds later - so the contended-lock path is the NORMAL path
         here rather than an edge case. Task 12.1 never reaches it, because nothing
         retried.
      3. THE PROCESS THAT ANSWERS THE RETRY HAS NO MEMORY. Everything it knows about the
         signal it reads back from the row, including the Idempotency_Key. This is
         Requirement 19.3's "THE Signal SHALL resume from its last persisted
         Order_Lifecycle_State without re-deriving it from an earlier, superseded
         state", and it is why every retry in this module goes through
         ``Worker.reload(signal)`` rather than through the ``Signal`` object the dead
         process held.

    There is also a case a worker crash cannot have at all: the submission COMPLETED and
    the process died before the response reached the caller. The idempotency layer has
    the outcome cached, so the retry is answered from the cache and the venue is never
    called again - see
    ``test_a_retry_after_the_response_was_lost_is_answered_from_the_cache``.

WHAT IS ASSERTED, EVERY TIME
    Requirement 19.4's three, through ``harness.assert_crash_recovery_invariants``:
      (a) at most one order is created at the exchange test double per Signal,
      (b) the Signal's persisted Order_Lifecycle_State after recovery matches its true
          state at the exchange,
      (c) no duplicate or orphaned signal/order record exists for its Idempotency_Key.

    Assertion (b) is Requirement 19.4's "after recovery", so it is asserted after the
    sweep has run. Where a test observes the window BEFORE recovery (the retry arriving
    into a held lock), it asserts (a) and (c) on their own - they hold at every instant -
    and then runs the sweep and asserts all three. Calling
    ``assert_crash_recovery_invariants`` mid-window instead would be asserting something
    Requirement 19.4 does not claim.

HOW THE RESTART IS SIMULATED
    ``harness.ApiProcessRestart``, a ``WorkerCrash`` and therefore a ``BaseException``.
    A restarted process does not run ``except Exception`` handlers on its way out, so an
    ``Exception`` here would be caught by ``_call_execution_engine`` and recorded as
    ``FAILED`` - a terminal state the signal never reached, with a live order behind it -
    and the idempotency layer's own ``except Exception`` would release a lock the dead
    process would still be holding. Both would make this suite exercise error handling
    instead of restart recovery. ``test_the_restart_is_not_an_exception_...`` pins that.

WHAT IS REAL HERE
    ``generate_signal``, ``submit_signal``, ``recover_signal``,
    ``apply_order_lifecycle_state``, the transition gate and its table,
    ``idempotency_key_for`` and ``DistributedIdempotencyLayer.execute_with_idempotency``
    - including the atomic Lua check-and-set, the wait-for-peer loop, the cached-result
    path and the owner-token release - all run as shipped. The database, Redis and the
    venue are the harness doubles, because this environment has neither PostgreSQL nor
    Redis.

WHAT IS OUT OF SCOPE
    A retry that MINTS A NEW Signal id is not this failure mode. The Idempotency_Key is
    derived from the Signal's own id (Requirement 19.1), so a retry addressing the same
    persisted Signal computes the same key - which is exactly what
    ``test_the_retried_request_derives_its_key_from_the_row_...`` establishes - while a
    request that re-mints identity is a NEW decision, guarded on its own key. Also out of
    scope: the recovery sweep's own bounds (3 attempts / 30 seconds), the unreachable and
    no-lookup venues, and the plural ``recover_in_flight_signals`` shape - all owned by
    task 12.1's module against the same harness. Tasks 12.3, 12.4 and 12.5 own the
    WebSocket, exchange-connection and database/queue interruptions.
"""

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleRejected,
    OrderLifecycleState,
)
from backend_app.core.distributed_idempotency import (
    DuplicateOrderError,
    idempotency_key_for,
)
from tests.crash_recovery.harness import (
    AFTER_EXCHANGE_ACCEPTED,
    BEFORE_EXCHANGE_CALL,
    ApiProcessRestart,
    WorkerCrash,
    assert_at_most_one_order_per_signal,
    assert_crash_recovery_invariants,
    assert_no_duplicate_or_orphaned_record,
)


async def api_process_dies_mid_request(world, *, crash_at=AFTER_EXCHANGE_ACCEPTED, **kwargs):
    """The first API process: it takes the request, gets to ``crash_at``, never answers.

    The shared arrangement for this whole module. Returns the signal as THAT process last
    saw it - deliberately, because every retry below has to start from the row instead.
    """
    api = world.start_worker(
        name="api-1", crash_at=crash_at, crash_with=ApiProcessRestart, **kwargs
    )
    signal = await api.generate()
    with pytest.raises(ApiProcessRestart):
        await api.submit(signal)
    return signal, api


def restarted_api(world, *, name="api-2", **kwargs):
    """The process that comes up in its place and receives the caller's retry.

    ``RETRY_DELAY = 0`` on this instance, and nothing else, is the only concession this
    module makes to test runtime. The layer's wait-for-peer loop still runs its full
    ``MAX_RETRY_ATTEMPTS`` rechecks against a lock that is still held, and still ends in
    ``DuplicateOrderError`` - the outcome and the number of attempts are unchanged. What
    changes is that ten 0.5-second real sleeps do not become five real seconds of test
    time. ``PROCESSING_TTL`` itself is untouched, and is expired only through
    ``world.redis.advance_past_processing_lock()``.
    """
    api = world.start_worker(name=name, **kwargs)
    api.layer.RETRY_DELAY = 0.0
    return api


# ══════════════════════════════════════════════════════════════════════════
# 1. WHAT THE RESTART LEAVES BEHIND
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_restart_leaves_a_live_order_an_unanswered_caller_and_a_held_lock(
    crash_world,
):
    """The premise of everything below, pinned before anything recovers it.

    Three facts, and each one is a precondition for a different test in this module: the
    venue holds an order, the record does not know it yet (``PENDING``, which is
    recoverable, rather than ``SUBMITTED``, which would be a lie), and the lock the dead
    process took is still held - which is what the caller's immediate retry will run into.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)
    key = signal.idempotency_key

    assert crash_world.exchange.accepted_placements_for(key), (
        "the restart was supposed to land AFTER the exchange accepted the order"
    )
    assert crash_world.exchange.true_state(key) is OrderLifecycleState.SUBMITTED

    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    assert crash_world.signal_row(signal).get("order_id") is None

    held = crash_world.redis.held_locks()
    assert len(held) == 1 and key in held[0], (
        f"the dead process's lock must still be held (only PROCESSING_TTL releases it); "
        f"Redis holds {crash_world.redis.store}"
    )


@pytest.mark.asyncio
async def test_the_restart_is_not_an_exception_the_submission_path_can_catch(crash_world):
    """The fidelity of the simulation itself, asserted rather than left to the harness.

    If ``ApiProcessRestart`` were an ``Exception``, ``_call_execution_engine`` would catch
    it and this signal would be recorded ``FAILED`` - terminal, unrecoverable, and false,
    with a live order sitting at the venue. Every test in this module would then be
    exercising error handling. So the hierarchy is pinned, and so is the consequence: the
    record stops at ``PENDING`` and the history stops with it.
    """
    assert issubclass(ApiProcessRestart, WorkerCrash)
    assert issubclass(ApiProcessRestart, BaseException)
    assert not issubclass(ApiProcessRestart, Exception)

    signal, _ = await api_process_dies_mid_request(crash_world)

    assert crash_world.persisted_state(signal) is not OrderLifecycleState.FAILED
    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history


# ══════════════════════════════════════════════════════════════════════════
# 2. THE RETRY - the part a worker crash does not have
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_retried_request_derives_its_key_from_the_row_not_from_process_memory(
    crash_world,
):
    """Why a restart cannot lose the guarantee: the key is a function of the record.

    The process that answers the retry has no memory of the first attempt. It reads the
    row, and ``idempotency_key_for`` derives the same key from it that the dead process
    used - because the key is derived from the Signal's own id (Requirement 19.1) and that
    id is durable. If this were not true, every restart would be a licence to place a
    second order, and the rest of this module would be unfalsifiable.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)

    row = crash_world.signal_row(signal)
    assert idempotency_key_for(row) == signal.idempotency_key
    assert row["idempotency_key"] == signal.idempotency_key

    restarted = restarted_api(crash_world)
    resumed = await restarted.reload(signal)
    assert idempotency_key_for(resumed) == signal.idempotency_key
    assert resumed.id == signal.id


@pytest.mark.asyncio
async def test_a_retry_into_the_dead_processs_still_held_lock_places_no_second_order(
    crash_world,
):
    """The normal case for this failure mode: the caller retries within seconds.

    The dead process's lock has 60 seconds left on it, so the fresh process cannot acquire
    it, waits for a peer that will never finish, and ends at ``DuplicateOrderError`` -
    which ``submit_signal`` reads as Requirement 19.1 holding, not as a failure, and
    answers with the signal's persisted state. The execution component is never reached,
    so the venue is never called a second time.

    Assertions (a) and (c) are checked in the window itself, because they hold at every
    instant. (b) is Requirement 19.4's "after recovery", so the sweep is run and all three
    are then checked together - and the record ends where the exchange actually is.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)

    restarted = restarted_api(crash_world)
    resumed = await restarted.reload(signal)
    answered = await restarted.submit(resumed)

    # The caller gets the truth as the record currently has it, not a second order.
    assert answered is OrderLifecycleState.PENDING
    assert restarted.execution.calls == [], (
        "the retry must not reach the execution component while another attempt owns the "
        "Idempotency_Key"
    )
    assert restarted.risk.calls == [], "the at-most-once body must not have run at all"
    # And the answer came from the ROW, through the DuplicateOrderError branch - there is
    # no completed result cached for this key, because the process that held the lock never
    # finished. This is what distinguishes this path from the lost-response case below.
    assert crash_world.redis.cached_results() == {}
    assert len(crash_world.exchange.placements) == 1
    assert_at_most_one_order_per_signal(crash_world, signal, expect_order=True)
    assert_no_duplicate_or_orphaned_record(crash_world, signal)

    # And the retry did not release a lock it does not own. The owner-token release is
    # what makes that safe, and a restart is precisely when it matters: the token the
    # dead process wrote is not this process's.
    held = crash_world.redis.held_locks()
    assert len(held) == 1 and signal.idempotency_key in held[0]

    # Then the sweep runs, and Requirement 19.4's three assertions hold.
    outcome = await restarted.recover(signal)
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.resubmission_allowed is False
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_retry_after_the_response_was_lost_is_answered_from_the_cache(crash_world):
    """The case only an API restart has: the work finished, the ANSWER was lost.

    The submission completed - order placed, state persisted, outcome cached under the
    Idempotency_Key - and the process died before the caller heard about it. The retry
    therefore finds a COMPLETED result rather than a lock, and the layer returns it
    without running the submission again. Nothing new reaches the venue, nothing is
    written, and the caller finally gets the same answer the first request had produced.
    """
    finished = crash_world.start_worker(name="api-1")
    signal = await finished.generate()
    first_answer = await finished.submit(signal)
    assert first_answer is OrderLifecycleState.SUBMITTED

    cached = crash_world.redis.cached_results()
    assert any(signal.idempotency_key in key for key in cached), (
        f"the completed outcome must be cached under the Idempotency_Key; Redis holds "
        f"{list(cached)}"
    )
    transitions_before = len(crash_world.transitions(signal))

    # ...and now the process is gone, before the response was written. The caller retries.
    restarted = restarted_api(crash_world)
    resumed = await restarted.reload(signal)
    retry_answer = await restarted.submit(resumed)

    assert retry_answer is OrderLifecycleState.SUBMITTED
    assert restarted.execution.calls == []
    assert restarted.risk.calls == [], (
        "a cached outcome must be returned without re-running the operation at all"
    )
    assert len(crash_world.exchange.placements) == 1
    assert len(crash_world.transitions(signal)) == transitions_before, (
        "an answer served from the cache must not append a transition that did not happen"
    )

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. REQUIREMENT 19.3 - RESUME FROM THE PERSISTED STATE, NOT A SUPERSEDED ONE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_restarted_process_sweeps_first_and_then_refuses_the_retry(crash_world):
    """Requirements 19.2 and 19.3 in the order they actually happen on a restart.

    The new process sweeps "before taking any further action", which moves the record to
    where the venue really is, and only then looks at the retry. Resuming from that
    persisted ``SUBMITTED`` - not from the ``GENERATED`` the dead process's ``Signal``
    object still claims - is what makes the retry a no-op: ``SUBMITTED -> PENDING`` is not
    an edge of Requirement 16.4's table, so the transition gate refuses it before the
    execution component is reached.
    """
    signal, dead_process = await api_process_dies_mid_request(crash_world)
    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED, (
        "the dead process's in-memory record is exactly the superseded state Requirement "
        "19.3 forbids resuming from"
    )

    restarted = restarted_api(crash_world)
    outcome = await restarted.recover(signal)
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.resubmission_allowed is False

    crash_world.redis.advance_past_processing_lock()
    resumed = await restarted.reload(signal)
    assert resumed.order_lifecycle_state is OrderLifecycleState.SUBMITTED

    with pytest.raises(OrderLifecycleRejected) as refusal:
        await restarted.submit(resumed)
    assert "SUBMITTED" in str(refusal.value)

    assert restarted.execution.calls == []
    assert len(crash_world.exchange.placements) == 1
    assert crash_world.redis.held_locks() == [], (
        "the refused retry's own lock must be released by its owner; only the DEAD "
        "process's lock is left to PROCESSING_TTL"
    )

    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
    ], history

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_the_retry_is_not_permitted_to_move_the_record_back_behind_a_fill(
    crash_world,
):
    """Requirement 19.3's first clause, on the state that costs the most to get wrong.

    The order filled while the API was down. The sweep brings the record to ``EXECUTED``,
    and the caller's retry - which is still asking for a submission - cannot move it back:
    the gate has no route from ``EXECUTED`` to ``PENDING``, so nothing is written and the
    fill stands.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)
    crash_world.exchange.fill(signal.idempotency_key)

    restarted = restarted_api(crash_world)
    outcome = await restarted.recover(signal)
    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED

    crash_world.redis.advance_past_processing_lock()
    resumed = await restarted.reload(signal)
    assert resumed.order_lifecycle_state is OrderLifecycleState.EXECUTED

    history_before = crash_world.transitions(signal)
    with pytest.raises(OrderLifecycleRejected):
        await restarted.submit(resumed)

    assert crash_world.persisted_state(signal) is OrderLifecycleState.EXECUTED
    assert crash_world.transitions(signal) == history_before
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.EXECUTED,
    )


@pytest.mark.asyncio
async def test_a_restart_loop_reconciles_once_and_then_changes_nothing(crash_world):
    """A pod that restarts three times sweeps three times. The record must not drift.

    Requirement 19.3 says a restart may not overwrite an in-flight signal's state with an
    earlier one, and a crash loop is where that would show up as an accumulating history
    rather than a single wrong value. The second and third sweeps find the record already
    consistent, write nothing, and append nothing.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)

    first = restarted_api(crash_world, name="api-2")
    assert (await first.recover(signal)).status == svc.RECOVERY_RECONCILED
    settled = crash_world.transitions(signal)

    for attempt in range(2):
        again = restarted_api(crash_world, name=f"api-{attempt + 3}")
        outcome = await again.recover(signal)
        assert outcome.status == svc.RECOVERY_ALREADY_CONSISTENT
        assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
        assert crash_world.transitions(signal) == settled

    assert crash_world.reconciliation_markers(signal) == [], (
        "a signal the sweep resolved needs no human"
    )
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. REQUIREMENT 19.3's OTHER TWO CLAUSES - NOT SPLIT, NOT MIS-REFERENCED
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_retried_request_cannot_split_the_signal_into_two_records(crash_world):
    """"...split into two or more signal/order records sharing the same Idempotency_Key".

    The one call that could produce a second row for an existing key is a restarted
    process re-deriving a known signal's record - ``generate_signal(signal_id=...)``.
    Migration 005b's ``uq_signals_idempotency_key`` refuses it, and ``signal_service``
    translates that ``23505`` into ``DuplicateOrderError`` rather than a 500, so the retry
    is answered as a duplicate instead of failing.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)

    restarted = restarted_api(crash_world)
    with pytest.raises(DuplicateOrderError):
        await svc.generate_signal(
            crash_world.deployment_row(),
            crash_world.action_output(),
            sb=restarted.sb,
            signal_id=signal.id,
        )

    assert len(crash_world.signal_rows_for_key(signal.idempotency_key)) == 1
    assert_no_duplicate_or_orphaned_record(crash_world, signal)


@pytest.mark.asyncio
async def test_the_record_never_references_an_order_the_exchange_does_not_hold(crash_world):
    """"...left referencing an order identifier that does not match the exchange's own".

    Before recovery the row references nothing at all, which is the truth - the reference
    never arrived. After recovery it references exactly what the venue holds, and the
    refused retry does not disturb it.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)
    assert crash_world.signal_row(signal).get("order_id") is None

    restarted = restarted_api(crash_world)
    await restarted.recover(signal)

    at_exchange = crash_world.exchange.orders_for(signal.idempotency_key)[0]
    assert crash_world.signal_row(signal)["order_id"] == at_exchange["order_id"]

    crash_world.redis.advance_past_processing_lock()
    with pytest.raises(OrderLifecycleRejected):
        await restarted.submit(await restarted.reload(signal))

    assert crash_world.signal_row(signal)["order_id"] == at_exchange["order_id"]
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. THE RESTART THAT COMES UP ON A COLD CACHE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_restart_onto_a_cold_idempotency_cache_still_places_no_second_order(
    crash_world,
):
    """A new pod, an evicted key, a flushed store - the guarantee is not Redis's alone.

    Requirement 19.1 says the key's recorded outcome must not be evicted before the signal
    is terminal, and the layer sizes its TTL for that. This test asks what holds when it
    happens anyway: the record is durable, so the sweep still learns the truth from the
    venue and the gate still refuses the retry. Redis is the fast path; the row and the
    exchange are the guarantee.
    """
    signal, _ = await api_process_dies_mid_request(crash_world)
    crash_world.redis.flush()
    assert crash_world.redis.held_locks() == []
    assert crash_world.redis.cached_results() == {}

    restarted = restarted_api(crash_world)
    outcome = await restarted.recover(signal)
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.resubmission_allowed is False

    with pytest.raises(OrderLifecycleRejected):
        await restarted.submit(await restarted.reload(signal))

    assert restarted.execution.calls == []
    assert len(crash_world.exchange.placements) == 1
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. THE OTHER SIDE OF THE WINDOW - the retry that SHOULD place the order
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_restart_before_the_exchange_call_lets_the_retry_place_exactly_one_order(
    crash_world,
):
    """The only case in this module where a retry is allowed to reach the venue.

    The process died before the order was offered, so the venue definitively holds nothing
    for this key - a different fact from "we could not find out", and the only one that
    licenses another submission. The retry then places exactly one order, under the same
    key, and the record follows it.
    """
    signal, _ = await api_process_dies_mid_request(
        crash_world, crash_at=BEFORE_EXCHANGE_CALL
    )
    assert not crash_world.exchange.orders

    restarted = restarted_api(crash_world)
    outcome = await restarted.recover(signal)
    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcome.resubmission_allowed is True
    assert crash_world.reconciliation_markers(signal) == []
    assert_crash_recovery_invariants(crash_world, signal, expect_order=False)

    crash_world.redis.advance_past_processing_lock()
    reached = await restarted.submit(await restarted.reload(signal))

    assert reached is OrderLifecycleState.SUBMITTED
    assert len(crash_world.exchange.accepted_placements_for(signal.idempotency_key)) == 1
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
