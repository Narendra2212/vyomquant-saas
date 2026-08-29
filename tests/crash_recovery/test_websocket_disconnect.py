"""
Task 12.3 - the WebSocket-disconnect failure mode.

Requirements 19.3, 19.4. This is the one failure mode in Requirement 19.4's list of five
that is NOT on the order-submission path, and that is the whole point of it: a socket
dropping is an OBSERVABILITY loss, not a trading event. Requirement 19.3 nonetheless names
it alongside the API restart, the exchange disconnect and the database restart and demands
the same three things of it, so what this module has to establish is that the platform's
durable record is genuinely independent of whether a frame was delivered - not merely that
nobody wired a resubmission into the reconnect handler.

WHAT IS ASSERTED, EVERY TIME
    Requirement 19.4's three, through ``harness.assert_crash_recovery_invariants``:
      (a) at most one order is created at the exchange test double per Signal,
      (b) the Signal's post-recovery Order_Lifecycle_State matches its true state at the
          exchange,
      (c) no duplicate or orphaned record shares its Idempotency_Key.

THERE ARE TWO WEBSOCKETS IN THIS PLATFORM AND REQUIREMENT 19.3 DISTINGUISHES NEITHER
    So both are exercised here, because picking one would leave the other's disconnect
    untested under a requirement that names "the WebSocket connection disconnects" without
    qualification:

      * **The OUTBOUND Signal Trace socket** (Requirement 18) - the connection the
        frontend holds to watch a signal's status change. Its disconnect is modelled by
        ``harness.FrameSink``: ``connected = False`` sends published frames to ``.dropped``
        instead of ``.delivered``, and ``snapshot(signal)`` is what Requirement 18.6's
        reconnect ("SHALL request a snapshot of current state rather than assuming no
        updates were missed") asks for. Sections 1 to 3 below.
      * **The INBOUND market-data socket** (Requirement 14.6) - the subscription the
        Live_Runtime feeds its DAG from. Its disconnect is modelled through
        ``LiveSignalPath.observe_feed_state(connected=False, ...)``, which classifies the
        feed ``DISCONNECTED`` through ``feed_state.py``'s own classifier and suspends
        Signal generation. Section 4 below. It belongs in this module because a suspended
        deployment is the mechanism that keeps a market-data drop from producing a SECOND
        signal record for a decision already in flight, which is assertion (c) for this
        failure mode.

WHO CALLS ``publish``, AND WHY THAT IS NOT WHAT IS UNDER TEST
    Task 14.2 owns the production publisher; nothing in the shipped code publishes a
    Signal Trace frame yet. This module therefore publishes the frames itself, always
    derived from the row that was just persisted (Requirement 14.5's "persist, then
    report", so a frame here can never carry something the record does not). That is a
    deliberate limitation and it is stated rather than hidden: what is under test is NOT
    which component calls ``publish``, it is that the durable record, the reconnect
    snapshot and the recovery outcome are all unaffected by whether the frame arrived.
    Every assertion in this module reads the database, the exchange double or
    ``FrameSink.snapshot`` (which reads the row) - none of them reads ``.delivered`` as a
    source of truth, and the two tests that do read it read it to prove a delivered frame
    was STALE and was not used. When 14.2 lands, the ``publish`` calls here become
    redundant rather than wrong.

HOW THE DISCONNECT IS TIMED
    ``disconnect_at_the_exchange_call`` hangs the drop off
    ``ScriptedExecutionEngine.on_call``, the hook that fires immediately before the venue
    is called - which is AFTER ``submit_signal`` has persisted ``PENDING`` and BEFORE it
    can persist ``SUBMITTED``. That is the narrowest window a socket drop can land in on
    this path, and it is the one where a client's last-known state is guaranteed to be
    superseded by the time it reconnects. Requirement 19.3's "SHALL resume from its last
    persisted Order_Lifecycle_State without re-deriving it from an earlier, superseded
    state" is precisely a claim about that window.

NOT TESTED HERE
    The frontend's own reconnect policy, its duplicate-frame suppression and its
    connection-status indicator (Requirement 18.4 - 18.9) are the Signal Trace page's, not
    the recovery path's. The other four failure modes are tasks 12.1, 12.2, 12.4 and 12.5,
    each reusing this same harness and the same three assertions against their own
    interruption.
"""

import copy

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.feed_state import FeedState
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleState,
    normalise_lifecycle_state,
)
from tests.crash_recovery.harness import (
    AFTER_EXCHANGE_ACCEPTED,
    WorkerCrash,
    assert_crash_recovery_invariants,
    assert_no_duplicate_or_orphaned_record,
)


# ══════════════════════════════════════════════════════════════════════════
# THE ARRANGEMENTS
# ══════════════════════════════════════════════════════════════════════════


def frame_for(world, signal, *, sequence):
    """The frame the Signal Trace socket would carry, derived from the PERSISTED row.

    Requirement 14.5: a state is reported only after it is persisted. Building the frame
    from ``signal_row`` rather than from an in-memory object is what makes that true here,
    and it is also what makes the "a delivered frame was stale" tests below meaningful -
    the frame was accurate when it was built, and the record moved on afterwards.
    """
    row = world.signal_row(signal) or {}
    return {
        "signal_id": signal.id,
        "idempotency_key": row.get("idempotency_key"),
        "order_lifecycle_state": row.get("order_lifecycle_state"),
        "order_id": row.get("order_id"),
        "sequence": sequence,
    }


def disconnect_at_the_exchange_call(world, *, publish_first=True):
    """Drop the socket in the window between ``PENDING`` and ``SUBMITTED``.

    Returns an ``on_call`` hook. ``publish_first`` delivers the ``PENDING`` frame before
    the drop, so the client's last-known state is one the record is about to supersede.
    """

    def hook(signal):
        if publish_first:
            world.frames.publish(frame_for(world, signal, sequence=1))
        world.frames.connected = False

    return hook


async def submitted_with_the_socket_dropping_mid_submission(world, **worker_kwargs):
    """One signal, submitted while the Signal Trace socket drops mid-flight."""
    worker = world.start_worker(
        name="worker-losing-its-socket",
        on_call=disconnect_at_the_exchange_call(world),
        **worker_kwargs,
    )
    signal = await worker.generate()
    state = await worker.submit(signal)
    return signal, worker, state


async def crashed_with_the_socket_already_down(world):
    """The nastiest combination: the socket is down AND the worker dies mid-submission.

    The client's last frame said ``GENERATED``; the record then moved to ``PENDING``; the
    venue accepted an order; the process died; and no frame about any of it was delivered.
    This is the arrangement Requirement 19.3's "without re-deriving it from an earlier,
    superseded state" exists for.
    """
    worker = world.start_worker(
        name="crashing-worker-with-no-socket", crash_at=AFTER_EXCHANGE_ACCEPTED
    )
    signal = await worker.generate()

    world.frames.publish(frame_for(world, signal, sequence=0))  # GENERATED, delivered
    world.frames.connected = False

    with pytest.raises(WorkerCrash):
        await worker.submit(signal)
    return signal, worker


def live_signal_path(world, worker):
    """The Requirement 14.6 feed gate, wired to this world's components.

    Task 10.3's object, unmodified, with the harness's risk and execution components and
    the harness's database client - so a market-data disconnect is exercised against the
    same durable world every other test in this module reads.
    """
    world.db.tables.setdefault(
        "strategy_deployments",
        [{"id": world.deployment_id, "user_id": world.user_id}],
    )
    return svc.LiveSignalPath(
        world.deployment_row(),
        risk_engine=worker.risk,
        execution_engine=worker.execution,
        sb=worker.sb,
        idempotency_layer=worker.layer,
        timeframe="1h",
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. WHAT A DISCONNECT TOUCHES: NOTHING
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_disconnect_drops_frames_and_leaves_the_record_byte_for_byte_identical(
    crash_world,
):
    """The premise of the whole failure mode, pinned before anything recovers anything.

    A signal is submitted cleanly, then the socket drops, then frames are published into
    the dark, then it reconnects. The row is compared field by field across all of it, and
    so is the transition history and the number of database calls - because "the disconnect
    changed no record" is only worth asserting if a change anywhere would fail it.
    """
    worker = crash_world.start_worker(name="worker")
    signal = await worker.generate()
    assert await worker.submit(signal) is OrderLifecycleState.SUBMITTED

    row_before = copy.deepcopy(crash_world.signal_row(signal))
    history_before = copy.deepcopy(crash_world.transitions(signal))
    calls_before = len(crash_world.db.calls)

    crash_world.frames.connected = False
    for sequence in (1, 2, 3):
        assert crash_world.frames.publish(frame_for(crash_world, signal, sequence=sequence)) is False
    crash_world.frames.connected = True

    assert len(crash_world.frames.dropped) == 3, "the drop is what the disconnect IS"
    assert crash_world.frames.delivered == []

    assert crash_world.signal_row(signal) == row_before
    assert crash_world.transitions(signal) == history_before
    assert len(crash_world.db.calls) == calls_before, (
        "a socket dropping must not cause a single database call, let alone a write"
    )

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_disconnect_during_the_submission_still_produces_exactly_one_order(
    crash_world,
):
    """The socket drops in the ``PENDING`` -> ``SUBMITTED`` window and the trade is
    unaffected.

    The submission path does not consult the realtime channel, so it must not be able to
    notice. This test is what says so: the drop lands at the narrowest possible moment and
    the outcome is the same ``SUBMITTED`` with the same single order.
    """
    signal, worker, state = await submitted_with_the_socket_dropping_mid_submission(
        crash_world
    )

    assert state is OrderLifecycleState.SUBMITTED
    assert crash_world.frames.connected is False, "the socket really was down"
    assert len(crash_world.exchange.placements) == 1

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
async def test_a_dropped_frame_is_the_only_thing_that_is_lost(crash_world):
    """The loss is real and it is bounded: one undelivered frame, no missing state.

    Stated explicitly so the module cannot be read as claiming a disconnect costs nothing
    at all. It costs the frame. It does not cost the record.
    """
    signal, _, _ = await submitted_with_the_socket_dropping_mid_submission(crash_world)

    # The SUBMITTED frame is published after the drop and is therefore lost.
    assert crash_world.frames.publish(frame_for(crash_world, signal, sequence=2)) is False

    delivered_states = [f["order_lifecycle_state"] for f in crash_world.frames.delivered]
    dropped_states = [f["order_lifecycle_state"] for f in crash_world.frames.dropped]
    assert delivered_states == ["PENDING"]
    assert dropped_states == ["SUBMITTED"]

    # And the state the client never heard about is on the record anyway.
    assert crash_world.persisted_state(signal) is OrderLifecycleState.SUBMITTED


# ══════════════════════════════════════════════════════════════════════════
# 2. THE RECONNECT (Requirement 18.6, Requirement 19.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_reconnect_snapshot_is_the_persisted_row_and_corrects_a_stale_client(
    crash_world,
):
    """Requirement 18.6's snapshot, and why it has to be the row.

    The client's last delivered frame says ``PENDING``. That was true when it was sent and
    is false now. A reconnect that resumed from what the client last held - or from a
    replayed frame - would resume from a superseded state, which is exactly what
    Requirement 19.3 forbids. The snapshot is read from the ``signals`` row instead, so it
    reports ``SUBMITTED``, which is also what the venue holds.
    """
    signal, _, _ = await submitted_with_the_socket_dropping_mid_submission(crash_world)

    stale = crash_world.frames.delivered[-1]
    assert stale["order_lifecycle_state"] == "PENDING"

    crash_world.frames.connected = True
    snapshot = crash_world.frames.snapshot(signal)

    assert snapshot == crash_world.signal_row(signal)
    assert snapshot["order_lifecycle_state"] == "SUBMITTED"
    assert snapshot["order_lifecycle_state"] != stale["order_lifecycle_state"], (
        "the snapshot's whole purpose is to be able to disagree with the last frame"
    )
    assert snapshot["idempotency_key"] == signal.idempotency_key
    assert (
        normalise_lifecycle_state(snapshot["order_lifecycle_state"])
        is crash_world.exchange.true_state(signal.idempotency_key)
    ), "the snapshot must agree with the venue, because it is read from the record"


@pytest.mark.asyncio
async def test_a_reconnect_creates_no_second_order(crash_world):
    """Requirement 19.1 names "WebSocket reconnect" as a path that must produce at most
    one order, so the reconnect is followed all the way through a recovery sweep.

    The sweep finds the record already consistent with the venue, writes nothing and
    resubmits nothing. The venue is asked exactly once, under the same derived key.
    """
    signal, worker, _ = await submitted_with_the_socket_dropping_mid_submission(crash_world)
    transitions_before = len(crash_world.transitions(signal))

    crash_world.frames.connected = True
    crash_world.frames.publish(frame_for(crash_world, signal, sequence=2))

    outcome = await worker.recover(signal)

    assert outcome.status == svc.RECOVERY_ALREADY_CONSISTENT
    assert outcome.resubmission_allowed is False
    assert crash_world.exchange.lookups == [signal.idempotency_key]
    assert len(crash_world.exchange.placements) == 1
    assert len(crash_world.transitions(signal)) == transitions_before

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_repeated_reconnects_neither_split_the_record_nor_the_history(crash_world):
    """A flapping socket is the realistic case, and it must be inert.

    Ten disconnect/reconnect cycles, each publishing and each taking a snapshot. Assertion
    (c) is checked after every one, because "no duplicate or orphaned record" is the
    invariant a reconnect loop would break first if a reconnect wrote anything.
    """
    signal, _, _ = await submitted_with_the_socket_dropping_mid_submission(crash_world)
    row_before = copy.deepcopy(crash_world.signal_row(signal))
    history_before = copy.deepcopy(crash_world.transitions(signal))

    for cycle in range(10):
        crash_world.frames.connected = False
        crash_world.frames.publish(frame_for(crash_world, signal, sequence=100 + cycle))
        crash_world.frames.connected = True
        assert crash_world.frames.snapshot(signal) == row_before
        assert_no_duplicate_or_orphaned_record(crash_world, signal)

    assert crash_world.transitions(signal) == history_before
    assert len(crash_world.exchange.placements) == 1
    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. A DISCONNECT ACROSS A SIGNAL THAT IS GENUINELY IN FLIGHT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_snapshot_of_an_in_flight_signal_is_its_persisted_state_not_the_last_frame(
    crash_world,
):
    """The socket is down and the process is dead. The record still answers.

    The last frame the client received said ``GENERATED``. The record says ``PENDING``,
    which is where the crash left it, and the snapshot says ``PENDING`` too - not the
    superseded ``GENERATED``, and not the ``SUBMITTED`` that nothing has yet confirmed.
    """
    signal, _ = await crashed_with_the_socket_already_down(crash_world)

    assert crash_world.frames.delivered[-1]["order_lifecycle_state"] == "GENERATED"
    assert crash_world.frames.dropped == [], "the drop happened before anything else was published"

    crash_world.frames.connected = True
    snapshot = crash_world.frames.snapshot(signal)

    assert snapshot["order_lifecycle_state"] == "PENDING"
    assert snapshot.get("order_id") is None, (
        "nothing confirmed an order yet, so the snapshot must not name one"
    )


@pytest.mark.asyncio
async def test_a_resumed_worker_reads_the_persisted_state_never_the_superseded_one(
    crash_world,
):
    """Requirement 19.3, as a call. ``reload`` returns ``PENDING``, not ``GENERATED``.

    The distinction is the requirement: a resumed worker handed the state the last
    delivered frame carried would re-derive from an earlier, superseded state and would
    append a second ``GENERATED -> PENDING`` to a history that already has one.
    """
    signal, _ = await crashed_with_the_socket_already_down(crash_world)

    restarted = crash_world.start_worker(name="restarted-worker")
    resumed = await restarted.reload(signal)

    assert resumed.order_lifecycle_state is OrderLifecycleState.PENDING
    assert resumed.id == signal.id
    assert resumed.idempotency_key == signal.idempotency_key

    history = [
        (row["from_state"], row["to_state"]) for row in crash_world.transitions(signal)
    ]
    assert history == [(None, "GENERATED"), ("GENERATED", "PENDING")], history


@pytest.mark.asyncio
async def test_recovery_after_a_reconnect_reconciles_and_creates_no_second_order(
    crash_world,
):
    """The full failure mode, end to end: drop, die, restart, reconnect, reconcile.

    Requirement 19.4's three assertions on the far side of it, and the snapshot the
    reconnecting client is served afterwards is the reconciled row - so the client's view
    is corrected by the record catching up to the venue, never by a replay.
    """
    signal, _ = await crashed_with_the_socket_already_down(crash_world)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.status == svc.RECOVERY_RECONCILED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED
    assert outcome.resubmission_allowed is False
    assert len(crash_world.exchange.placements) == 1

    crash_world.frames.connected = True
    snapshot = crash_world.frames.snapshot(signal)
    assert snapshot["order_lifecycle_state"] == "SUBMITTED"
    assert snapshot["order_id"] == crash_world.exchange.orders_for(signal.idempotency_key)[0][
        "order_id"
    ]

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_fill_that_happened_while_the_socket_was_down_is_reconciled_from_the_venue(
    crash_world,
):
    """Nobody was watching and the order filled. The missed frames are irrecoverable; the
    STATE is not, because it is re-derived from the venue rather than from the stream."""
    signal, _ = await crashed_with_the_socket_already_down(crash_world)
    crash_world.exchange.fill(signal.idempotency_key)

    restarted = crash_world.start_worker(name="restarted-worker")
    outcome = await restarted.recover(signal)

    assert outcome.order_lifecycle_state is OrderLifecycleState.EXECUTED

    crash_world.frames.connected = True
    assert crash_world.frames.snapshot(signal)["order_lifecycle_state"] == "EXECUTED"

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.EXECUTED,
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. THE OTHER WEBSOCKET: THE MARKET-DATA FEED (Requirements 14.6, 19.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_market_data_disconnect_suspends_generation(crash_world):
    """A dropped inbound socket reads ``DISCONNECTED`` and generation stops.

    Through ``feed_state.py``'s own classifier - this asserts the verdict, not a threshold
    of its own - and the deployment's live-data health state is marked, which is the other
    half of Requirement 14.6.
    """
    worker = crash_world.start_worker(name="worker")
    path = live_signal_path(crash_world, worker)

    report = path.observe_feed_state(connected=False, age_seconds=12.0)
    assert report.state is FeedState.DISCONNECTED

    may_generate = await path.apply_feed_state(report)

    assert may_generate is False
    assert path.suspended is True
    assert path.counters["feed_suspensions"] == 1

    (deployment_row,) = crash_world.db.rows("strategy_deployments")
    assert "FEED_NOT_LIVE" in deployment_row[svc.DEPLOYMENT_HEALTH_COLUMN]
    assert "DISCONNECTED" in deployment_row[svc.DEPLOYMENT_HEALTH_COLUMN]


@pytest.mark.asyncio
async def test_a_market_data_disconnect_generates_no_signal_and_places_no_order(
    crash_world,
):
    """Requirement 14.6's "SHALL NOT generate a Signal from stale or gapped market data",
    and assertion (c) for this failure mode.

    An event arriving while the feed is disconnected is refused at the first gate, so no
    ``signals`` row is written and the venue is never called. That is what stops a
    market-data drop from splitting one decision into two records sharing an
    Idempotency_Key.
    """
    worker = crash_world.start_worker(name="worker")
    path = live_signal_path(crash_world, worker)
    rows_before = len(crash_world.db.rows("signals"))

    disconnected = path.observe_feed_state(connected=False, age_seconds=12.0)
    outcome = await path.on_action_output(
        crash_world.action_output(), feed=disconnected
    )

    assert outcome.status == svc.OUTCOME_FEED_SUSPENDED
    assert outcome.code == "FEED_NOT_LIVE"
    assert outcome.signal is None
    assert len(crash_world.db.rows("signals")) == rows_before
    assert crash_world.exchange.placements == []
    assert worker.execution.calls == []
    assert worker.risk.calls == [], "a suspended feed is refused before risk is consulted"


@pytest.mark.asyncio
async def test_generation_resumes_on_reconnect_and_produces_exactly_one_order(crash_world):
    """The feed comes back and one event produces one signal and one order.

    Requirement 14.6's "SHALL resume normal evaluation only after the subscription is
    re-established": automatic, no restart. And the resumed path goes through the SAME
    submission route, so the resulting signal carries one order under one derived key -
    the reconnect is not a second order-placement path.
    """
    worker = crash_world.start_worker(name="worker")
    path = live_signal_path(crash_world, worker)

    await path.apply_feed_state(path.observe_feed_state(connected=False, age_seconds=12.0))
    assert path.suspended is True

    live = path.observe_feed_state(connected=True, age_seconds=30.0)
    assert live.state is FeedState.LIVE

    outcome = await path.on_action_output(crash_world.action_output(), feed=live)

    assert path.suspended is False
    assert path.counters["feed_resumes"] == 1
    assert outcome.status == svc.OUTCOME_SUBMITTED
    assert outcome.order_lifecycle_state is OrderLifecycleState.SUBMITTED

    generated = outcome.signal
    assert len(crash_world.exchange.accepted_placements_for(generated.idempotency_key)) == 1
    assert_crash_recovery_invariants(
        crash_world,
        generated,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )


@pytest.mark.asyncio
async def test_a_market_data_disconnect_does_not_touch_an_in_flight_signals_record(
    crash_world,
):
    """The two halves of the failure mode, together.

    A signal is already in flight at the venue when the market-data socket drops. The
    suspension must not overwrite, re-derive or duplicate its record - Requirement 19.3
    names the WebSocket disconnect in exactly those terms - and the sweep afterwards must
    still reconcile it to the venue's own answer, once, with no second order.
    """
    signal, _ = await crashed_with_the_socket_already_down(crash_world)
    row_before = copy.deepcopy(crash_world.signal_row(signal))
    history_before = copy.deepcopy(crash_world.transitions(signal))

    restarted = crash_world.start_worker(name="restarted-worker")
    path = live_signal_path(crash_world, restarted)
    disconnected = path.observe_feed_state(connected=False, age_seconds=12.0)
    for _event in range(3):
        outcome = await path.on_action_output(
            crash_world.action_output(), feed=disconnected
        )
        assert outcome.status == svc.OUTCOME_FEED_SUSPENDED

    assert crash_world.signal_row(signal) == row_before
    assert crash_world.transitions(signal) == history_before
    assert len(crash_world.signal_rows_for_key(signal.idempotency_key)) == 1

    recovered = await restarted.recover(signal)
    assert recovered.status == svc.RECOVERY_RECONCILED
    assert len(crash_world.exchange.placements) == 1

    assert_crash_recovery_invariants(
        crash_world,
        signal,
        expect_order=True,
        expected_state=OrderLifecycleState.SUBMITTED,
    )
