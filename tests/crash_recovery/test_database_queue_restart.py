"""
Task 12.5 - the database/queue-restart failure mode.

Requirements 19.3, 19.4. The fifth and last of Requirement 19.4's named failure modes,
and the only one where the thing that fails is the platform's own memory of what
happened. Tasks 12.1 - 12.4 all break something OUTSIDE the record: the process, the
API, the socket, the venue. This one breaks the record itself, and it breaks it at the
worst possible instant - the venue has accepted the order and the database is gone
before the state can be written down.

WHAT IS ASSERTED, EVERY TIME
    Requirement 19.4's three, through ``harness.assert_crash_recovery_invariants``:
      (a) at most one order is created at the exchange test double per Signal,
      (b) the Signal's persisted Order_Lifecycle_State after recovery matches its true
          state at the exchange,
      (c) no duplicate or orphaned signal/order record exists for that Signal's
          Idempotency_Key.

HOW THE RESTART IS SIMULATED, AND WHY IN FOUR SHAPES
    A "database/queue restart" is not one event, so it is not simulated as one. The
    harness offers four seams and this module uses all of them, because each leaves the
    platform in a different place:

      * ``world.db.available = False`` - the whole database is gone. Every verb on every
        table raises ``DatabaseUnavailable`` carrying PostgreSQL's own ``57P03 the
        database system is starting up``, which is literally what a restarting server
        says.
      * ``ScriptedExecutionEngine(on_call=...)`` - the outage arrives AT the exchange
        call. This is the interesting one and most of this module uses it: the order is
        live at the venue and there is no longer anywhere to record that it is.
      * ``world.db.fail_on / error_on[("signals", "update")]`` - ONE write breaks, either
        by raising or by coming back as ``result.error``. A pooler in front of a
        restarting server does both, and the platform classifies them differently, so
        both are exercised.
      * ``world.redis.available = False`` - the QUEUE side. The idempotency store is the
        queue in front of every submission, and Requirement 19.5 says exactly what must
        happen when it is unreachable: the order is withheld, not sent unguarded.

    None of these is a ``BaseException``. That is the substantive difference from task
    12.1 and it is deliberate: a database going away is a component REPORTING a failure
    to a process that is still running, so ``except Exception`` handlers do run, the
    idempotency layer does release its lock, and the caller does get told. A crash and an
    outage leave different debris, and this module pins the outage's.

WHAT IS REAL HERE
    ``generate_signal``, ``submit_signal``, ``recover_signal``,
    ``recover_in_flight_signals``, ``apply_order_lifecycle_state``, the transition gate
    and table, ``load_current_order_lifecycle_state``, ``idempotency_key_for`` and
    ``DistributedIdempotencyLayer.execute_with_idempotency`` all run as shipped. The
    database, Redis and the venue are ``harness.py``'s doubles.

TWO DEFECTS THIS MODULE FOUND, AND WHERE THEY WERE FIXED
    Section 7. Both came from one root cause - ``load_current_order_lifecycle_state``
    FALLING BACK to this process's in-memory ``Signal`` when the row cannot be read - and
    both tests assert what Requirements 19.2/19.3 require rather than what the code did.
    Writing them the other way round (asserting the broken behaviour) would have made this
    suite certify the very thing Requirement 19.4 exists to catch. The fix is in
    ``signal_service``: the sweep now reads the state through
    ``_persisted_state_for_recovery`` - the row read strictly, then the persisted transition
    history, and never process memory - so both tests pass as ordinary tests.

NOT TESTED HERE
    The other four failure modes (tasks 12.1 - 12.4) and their own interruptions. The
    transition gate's rules (task 1.1), the Idempotency_Key's determinism (task 9.1) and
    submission routing (task 10.2) are covered by their own suites and are only relied on
    here.
"""

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.core.distributed_idempotency import idempotency_key_for
from tests.crash_recovery.harness import (
    DatabaseUnavailable,
    assert_at_most_one_order_per_signal,
    assert_crash_recovery_invariants,
    assert_no_duplicate_or_orphaned_record,
)

#: What a restarting PostgreSQL says, and what the harness's outage raises.
STARTING_UP = "57P03 the database system is starting up"


def history(world, signal):
    """The signal's transition log as ``(from_state, to_state)`` pairs, in order."""
    return [(row["from_state"], row["to_state"]) for row in world.transitions(signal)]


async def outage_at_the_exchange_call(world, *, restart: bool = True):
    """Generate a signal, then lose the database at the instant the venue is called.

    The shared arrangement for most of this module. ``on_call`` fires inside
    ``ScriptedExecutionEngine.submit_order`` immediately before ``place_order``, so the
    order IS accepted by the venue and the ``PENDING -> SUBMITTED`` write that would
    record it finds no database.

    ``restart=True`` brings the database back before returning, which is the "restart"
    half of the failure mode - a restart is an outage that ends. Pass ``restart=False``
    to stay inside the outage.

    The refusal arrives as a CLASSIFIED :class:`svc.SignalPersistenceError` carrying the
    driver's own ``57P03`` text, not as the raw :class:`DatabaseUnavailable`: a raised write
    failure is classified exactly as a reported one, which is what lets the plural sweep
    contain it per signal (see section 5) instead of aborting the whole restart. The
    original exception is still chained as ``__cause__``.
    """
    worker = world.start_worker(
        name="worker-during-the-outage",
        on_call=lambda _signal: setattr(world.db, "available", False),
    )
    signal = await worker.generate()

    with pytest.raises(svc.SignalPersistenceError) as outage:
        await worker.submit(signal)
    assert outage.value.code == "SIGNAL_STATE_NOT_PERSISTED"
    assert STARTING_UP in str(outage.value)
    assert isinstance(outage.value.__cause__, DatabaseUnavailable)

    if restart:
        world.db.available = True
    return signal, worker


# ══════════════════════════════════════════════════════════════════════════
# 1. THE WINDOW ITSELF - what the outage actually leaves behind
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_outage_leaves_a_live_order_and_a_record_still_at_pending(crash_world):
    """The premise of the module, pinned before anything recovers it.

    ``PENDING`` was written BEFORE the execution component was called (task 10.2 orders
    it that way on purpose), so it survives the outage; ``SUBMITTED`` was to be written
    after and never was. The record therefore lags reality rather than lying about it,
    which is the only shape recovery can work from.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)
    key = signal.idempotency_key

    # The venue took the order, and still holds it.
    assert crash_world.exchange.accepted_placements_for(key)
    assert crash_world.exchange.true_state(key) is OrderLifecycleState.SUBMITTED

    # The record says the recoverable thing, not the false one.
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    assert crash_world.signal_row(signal).get("order_id") is None


@pytest.mark.asyncio
async def test_the_history_stops_exactly_where_the_database_did(crash_world):
    """Requirement 16.7's log ends at the last transition that could be written."""
    signal, _ = await outage_at_the_exchange_call(crash_world)

    assert history(crash_world, signal) == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
    ], history(crash_world, signal)


@pytest.mark.asyncio
async def test_the_outage_releases_the_lock_because_the_process_is_still_alive(
    crash_world,
):
    """The substantive difference from task 12.1, asserted rather than described.

    A crashed worker leaves its ``processing:`` lock held until ``PROCESSING_TTL``
    expires it. A database outage does not: the failure is an ``Exception``, so the
    idempotency layer's own ``except Exception`` runs its owner-token release and the key
    is free immediately. Which means Redis is NOT what protects this signal from a second
    order during a database restart - the row and the venue's client-order-id check are,
    and the rest of this module leans on exactly that.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)

    assert crash_world.redis.held_locks() == [], (
        "a database outage is reported to a living process, so the layer releases the "
        "lock it took; only a dead process leaves one held"
    )
    assert crash_world.redis.cached_results() == {}, (
        "nothing completed, so nothing may be cached as this key's outcome"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE RESTART, AND REQUIREMENT 19.4's THREE ASSERTIONS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_after_the_restart_reconciles_the_record_to_the_exchange(
    crash_world,
):
    """The task, in one test: the database comes back, the sweep asks the venue, the
    record catches up. No order is resubmitted."""
    signal, _ = await outage_at_the_exchange_call(crash_world)

    resumed = crash_world.start_worker(name="worker-after-the-restart")
    outcome = await resumed.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.resubmission_allowed is False
    assert outcome.exchange_order_id == "X-1"
    assert not outcome.requires_manual_reconciliation
    assert crash_world.exchange.lookups == [signal.idempotency_key]

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
    # Requirement 19.3's third clause: the reference on the row is the venue's own.
    assert crash_world.signal_row(signal)["order_id"] == "X-1"


@pytest.mark.asyncio
async def test_a_fill_during_the_outage_is_reconciled_after_the_restart(crash_world):
    """The order filled while there was nowhere to write it down.

    The record has to arrive at ``EXECUTED`` through ``SUBMITTED``, because that is where
    the order really went, and the history has to say so.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)
    crash_world.exchange.fill(signal.idempotency_key)

    resumed = crash_world.start_worker(name="worker-after-the-restart")
    outcome = await resumed.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.EXECUTED,
    )
    assert history(crash_world, signal) == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
        ("SUBMITTED", "EXECUTED"),
    ], history(crash_world, signal)


@pytest.mark.asyncio
async def test_one_refused_write_is_classified_as_a_persistence_failure_and_recovers(
    crash_world,
):
    """The other shape a restart takes: the connection is up, the write comes BACK
    refused.

    A pooler in front of a restarting server answers rather than dropping, so the failure
    arrives as ``result.error`` instead of as a raised exception, and ``_update_signal_row``
    is required to turn that into a classified :class:`SignalPersistenceError` rather
    than a bare 500 - Requirement 15.5's shape. The debris is identical to the raised
    case (live order, record at ``PENDING``), so the recovery is identical too.
    """
    world = crash_world

    def refuse_the_next_signals_update(_signal):
        world.db.error_on[("signals", "update")] = f"{STARTING_UP} (queue restarting)"

    worker = world.start_worker(
        name="worker-behind-a-restarting-pooler", on_call=refuse_the_next_signals_update
    )
    signal = await worker.generate()

    with pytest.raises(svc.SignalPersistenceError) as refused:
        await worker.submit(signal)
    assert refused.value.code == "SIGNAL_STATE_NOT_PERSISTED"
    assert STARTING_UP in refused.value.message
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING

    world.db.error_on.clear()  # the pooler reconnected
    resumed = world.start_worker(name="worker-after-the-restart")
    outcome = await resumed.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert_crash_recovery_invariants(
        world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_sweep_run_while_the_database_is_still_down_writes_nothing(crash_world):
    """A sweep during the outage cannot fix anything, and must not pretend it did.

    Requirement 19.4's assertion (b) is not satisfiable while the database is down - the
    record cannot be written at all, and neither can a manual-reconciliation marker. So
    what is asserted here is the honest pair: the outage surfaces to the caller, and the
    record and its history are byte-for-byte what they were. Then the database returns and
    the NEXT sweep is the one that satisfies all three.

    The refusal is ``SIGNAL_STATE_NOT_READABLE``: the venue answered, and the sweep could
    read the signal's persisted state neither from the row nor from its transition history,
    so there is nothing to reconcile the answer against. It refuses rather than reporting a
    verdict - which is the same rule section 5 exercises for a missing client, arriving one
    step later.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world, restart=False)
    before = crash_world.signal_row(signal)
    transitions_before = history(crash_world, signal)

    resumed = crash_world.start_worker(name="worker-mid-outage")
    with pytest.raises(svc.SignalPersistenceError) as refused:
        await resumed.recover(signal)
    assert refused.value.code == "SIGNAL_STATE_NOT_READABLE"

    # It did ask the venue - the venue was never the thing that was down.
    assert crash_world.exchange.lookups == [signal.idempotency_key]
    # And it wrote nothing at all.
    crash_world.db.available = True
    assert crash_world.signal_row(signal) == before
    assert history(crash_world, signal) == transitions_before
    assert crash_world.reconciliation_markers(signal) == []
    assert len(crash_world.exchange.placements) == 1

    # The restart completes, and the next sweep reconciles.
    outcome = await resumed.recover(signal)
    assert outcome.status == svc.RECOVERY_RECONCILED
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_the_resumed_worker_reads_the_row_rather_than_its_own_memory(crash_world):
    """Requirement 19.3's "without re-deriving it from an earlier, superseded state".

    The process that met the outage is still running and still holding a ``Signal`` object
    that says ``GENERATED`` - ``submit_signal`` raised before it could hand back anything
    newer. The row says ``PENDING``. ``reload`` is what a resumed worker must use, and it
    reports the row.
    """
    signal, worker = await outage_at_the_exchange_call(crash_world)
    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED, (
        "the caller's own object is stale by construction; that is the hazard 19.3 names"
    )

    resumed = await worker.reload(signal)

    assert resumed.order_lifecycle_state is OrderLifecycleState.PENDING
    assert resumed.id == signal.id
    assert idempotency_key_for(resumed) == idempotency_key_for(signal)


# ══════════════════════════════════════════════════════════════════════════
# 3. THE CONTROL - an outage that began before the venue saw anything
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_outage_before_submission_leaves_nothing_at_the_venue(crash_world):
    """The whole database is gone before ``submit_signal`` is even called.

    Nothing reaches the venue, because the very first write on the path - the transition
    log's genesis row - fails and the submission is refused before the idempotency lock is
    taken. That refusal is classified (Requirement 15.5), and the signal is left exactly
    where it was.
    """
    worker = crash_world.start_worker(name="worker-before-the-outage")
    signal = await worker.generate()
    crash_world.db.available = False

    with pytest.raises(svc.SignalPersistenceError) as refused:
        await worker.submit(signal)
    assert refused.value.code == "ORDER_LIFECYCLE_TRANSITION_NOT_AUDITED"

    crash_world.db.available = True
    assert crash_world.exchange.placements == []
    assert crash_world.persisted_state(signal) is OrderLifecycleState.GENERATED
    assert crash_world.redis.held_locks() == []

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=False,
        expected_state=OrderLifecycleState.GENERATED,
    )


@pytest.mark.asyncio
async def test_the_restart_licenses_exactly_one_resubmission_when_nothing_was_placed(
    crash_world,
):
    """The one case where resubmission is permitted, and it produces one order.

    The venue definitively answers that it holds nothing for this key, so the key was
    demonstrably never used - which is a different fact from "the database could not tell
    us", and only this one licenses another submission.
    """
    worker = crash_world.start_worker(name="worker-before-the-outage")
    signal = await worker.generate()
    crash_world.db.available = False
    with pytest.raises(svc.SignalPersistenceError):
        await worker.submit(signal)
    crash_world.db.available = True

    resumed = crash_world.start_worker(name="worker-after-the-restart")
    outcome = await resumed.recover(signal)
    assert outcome.status == svc.RECOVERY_NO_ORDER_AT_EXCHANGE
    assert outcome.resubmission_allowed is True
    assert crash_world.reconciliation_markers(signal) == [], (
        "a signal with no order behind it needs no human"
    )

    reached = await resumed.submit(await resumed.reload(signal))

    assert reached is OrderLifecycleState.SUBMITTED
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
    assert history(crash_world, signal) == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
    ], history(crash_world, signal)


# ══════════════════════════════════════════════════════════════════════════
# 4. THE QUEUE SIDE - Requirement 19.5, the store in front of every submission
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unreachable_idempotency_store_withholds_the_submission(
    crash_world, monkeypatch
):
    """Requirement 19.5: withheld, never submitted unguarded.

    ``ENV`` is production for this one call, and that is not a trick - it is the layer's
    documented fail-closed posture, and Requirement 19.5 IS that posture. Outside
    production the layer deliberately degrades instead, which is a different requirement's
    problem and not something this module should silently exercise in place of the real
    one.
    """
    worker = crash_world.start_worker(name="worker-with-no-store")
    signal = await worker.generate()

    crash_world.redis.available = False
    monkeypatch.setenv("ENV", "production")
    reached = await worker.submit(signal)
    monkeypatch.setenv("ENV", "testing")

    assert reached is OrderLifecycleState.PENDING
    assert worker.execution.calls == [], (
        "the execution component must never be reached without an idempotency guarantee"
    )
    assert crash_world.exchange.placements == []

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=False,
        expected_state=OrderLifecycleState.PENDING,
    )


@pytest.mark.asyncio
async def test_the_withheld_submission_is_retried_under_the_same_key(
    crash_world, monkeypatch
):
    """Requirement 19.5's last clause: retried, under the SAME Idempotency_Key, once the
    store is back - and the result is one order, not two."""
    worker = crash_world.start_worker(name="worker-with-no-store")
    signal = await worker.generate()

    crash_world.redis.available = False
    monkeypatch.setenv("ENV", "production")
    withheld = await worker.submit(signal)
    monkeypatch.setenv("ENV", "testing")
    assert withheld is OrderLifecycleState.PENDING

    crash_world.redis.available = True  # the queue finished restarting
    reached = await worker.submit(await worker.reload(signal))

    assert reached is OrderLifecycleState.SUBMITTED
    (placement,) = crash_world.exchange.placements
    assert placement["client_order_id"] == signal.idempotency_key, (
        "Requirement 19.5's retry is under the same key, which is what keeps it at most "
        "one order"
    )
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
    # The withhold left no PENDING -> PENDING row behind it, so the history is a chain.
    assert history(crash_world, signal) == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
        ("PENDING", "SUBMITTED"),
    ], history(crash_world, signal)


# ══════════════════════════════════════════════════════════════════════════
# 5. THE SWEEP'S OWN AUTHORITY - a restart that came back without a connection
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_sweep_with_no_database_client_refuses_to_report(crash_world):
    """Requirement 19.2, read strictly: a sweep that cannot read the record has no
    authority over it.

    A restart that comes back before its connection pool does has no client at all. The
    sweep refuses - it does not fall back to the venue and it does not decide anything -
    because a verdict reached without the persisted state is how a live order gets
    duplicated.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)

    with pytest.raises(svc.SignalSubmissionRefused) as refused:
        await svc.recover_signal(signal, exchange=crash_world.exchange, sb=None, user=None)

    assert refused.value.code == "SIGNAL_NO_PERSISTENCE_CLIENT"
    assert crash_world.exchange.lookups == [], (
        "the venue is not asked at all; there would be nowhere to record the answer"
    )
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    assert crash_world.reconciliation_markers(signal) == []
    assert len(crash_world.exchange.placements) == 1


@pytest.mark.asyncio
async def test_the_plural_sweep_records_the_refusal_and_resubmits_nothing(crash_world):
    """The same refusal in the shape a restart actually has: per signal, as an outcome.

    ``recover_in_flight_signals`` turns the refusal into a
    ``MANUAL_RECONCILIATION_REQUIRED`` outcome with ``marked=False`` - the signal needs a
    human AND the platform could not even write that down - and, crucially,
    ``resubmission_allowed=False``.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)

    outcomes = await svc.recover_in_flight_signals(
        [signal], exchange=crash_world.exchange, sb=None
    )

    outcome = outcomes[signal.id]
    assert outcome.status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED
    assert outcome.requires_manual_reconciliation is True
    assert outcome.marked is False
    assert outcome.resubmission_allowed is False

    assert_at_most_one_order_per_signal(crash_world, signal, expect_order=True)
    assert_no_duplicate_or_orphaned_record(crash_world, signal)


# ══════════════════════════════════════════════════════════════════════════
# 6. A RESTART THAT COMES BACK WITH A DIFFERENT SCHEMA STATE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_restart_without_the_transition_log_still_reconciles_the_record(
    crash_world,
):
    """A database that comes back with migration 005b section 2 missing.

    A restart onto a replica, or onto a server whose schema cache has not caught up, is a
    real way for ``order_lifecycle_transitions`` to be absent from under a running
    platform. The module's stated posture is that Requirement 16.7's audit is conditioned
    on the audit store being reachable while the reconciliation is not - refusing to
    record a real fill because a log table is missing would be strictly worse - and this
    is that posture under test: all three of Requirement 19.4's assertions still hold, the
    history simply stops.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)
    crash_world.db.transitions_table = False

    resumed = crash_world.start_worker(name="worker-after-the-restart")
    outcome = await resumed.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert svc.transitions_table_support_state() is False, (
        "the absence must be detected and remembered, not retried per write"
    )
    assert history(crash_world, signal) == [
        (None, "GENERATED"),
        ("GENERATED", "PENDING"),
    ], "the rows written before the restart are all the history there is"

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 7. THE TWO DEFECTS THIS MODULE FOUND, NOW FIXED AND PINNED
# ══════════════════════════════════════════════════════════════════════════
#
# Both tests below assert what Requirements 19.2/19.3 require, and both used to fail for
# one shared reason:
#
#     load_current_order_lifecycle_state, when the row cannot be read, LOGS and RETURNS
#     ``signal.order_lifecycle_state`` - this process's in-memory value.
#
# For its two original callers that is a defensible fallback and it is unchanged. For
# recover_signal it was not, and recover_signal's own docstring said why: "a sweep that
# cannot read that state has no authority to report on this signal at all". It enforced
# that for a missing CLIENT and not for a failed READ, and a database/queue restart
# produces the second far more often than the first. The in-memory value a restart-era
# caller holds is the GENERATED it was minted with - the superseded state Requirement 19.3
# names by name.
#
# The sweep now reads the state through ``_persisted_state_for_recovery``: the row read
# strictly (result.error inspected, a missing row treated as a failed read), then the
# newest row of the persisted transition history, and nothing else. An unreadable record
# is UNKNOWN, and unknown cannot rule out a live order.
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_sweep_whose_read_is_refused_must_not_re_derive_from_memory(crash_world):
    """The pooler is back for writes and still refusing reads. Requirements 19.3, 19.4(c).

    ``error_on[("signals", "select")]`` is the shape a pooler in front of a restarting
    server actually produces: a response, carrying an error, rather than a dropped
    connection. That refusal used to read as "no row", the fallback returned the caller's
    stale ``GENERATED``, and the sweep walked ``GENERATED -> PENDING -> SUBMITTED`` over a
    record that was already at ``PENDING`` - two ``GENERATED -> PENDING`` rows, so the log
    stopped being one chain. That is assertion (c), and it is the "split into two or more
    records" hazard Requirement 19.3 names, in the audit log rather than the row.

    Now the refused SELECT is recognised as a failed read and the sweep falls back to the
    OTHER persisted record of the same fact - the newest row of the transition history,
    which says ``PENDING``. So the reconciliation still happens, from the right state, and
    the history is still one chain.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world)
    crash_world.db.error_on[("signals", "select")] = f"{STARTING_UP} (reads refused)"

    resumed = crash_world.start_worker(name="worker-after-a-partial-restart")
    await resumed.recover(signal)

    crash_world.db.error_on.clear()
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_sweep_that_cannot_read_the_row_must_not_rule_out_a_live_order(
    crash_world,
):
    """The database and the venue are both down. Requirement 19.2's marking clause.

    A restart taking the venue's connection with it is one event, not two - the same
    network partition does both - so this is the combination worth pinning. The sweep
    exhausts its three attempts and cannot write a marker (there is no database to write it
    to, which is honest). It used to then report that this signal does not need a human at
    all - reading its own in-memory ``GENERATED`` and concluding no order could be live -
    while a live order sat at the venue with nothing on the record pointing at it.

    Now an unreadable record is UNKNOWN rather than ``GENERATED``, and unknown cannot rule
    out a live order, so the signal is reported as requiring manual reconciliation. The
    state the sweep reports is ``None``, because it established none.
    """
    signal, _ = await outage_at_the_exchange_call(crash_world, restart=False)
    crash_world.exchange.reachable = False

    resumed = crash_world.start_worker(name="worker-mid-outage")
    outcome = await resumed.recover(signal)

    assert outcome.status == svc.RECOVERY_MANUAL_RECONCILIATION_REQUIRED
    assert outcome.resubmission_allowed is False
    assert outcome.attempts == svc.RECOVERY_MAX_ATTEMPTS
    assert outcome.marked is False, (
        "there is no database to record the marker in, and reporting otherwise would be "
        "the lie; this part is correct"
    )
    crash_world.db.available = True
    assert crash_world.persisted_state(signal) is OrderLifecycleState.PENDING
    assert len(crash_world.exchange.placements) == 1

    assert outcome.requires_manual_reconciliation is True, (
        f"a live order sits at the venue under {signal.idempotency_key} and the record "
        f"does not reference it; the sweep read its own memory "
        f"({outcome.order_lifecycle_state}) instead of the row and concluded nothing "
        f"could be live"
    )
    assert outcome.order_lifecycle_state is None, (
        "the sweep could read neither the row nor the transition history, so it must "
        "report NO state rather than the superseded one its caller happens to hold"
    )
