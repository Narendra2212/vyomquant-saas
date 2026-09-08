"""
tests/property/test_paper_channel_sequence.py - P-53.

Spec: marketplace-subscriptions-paper-trading task 26.6. ``design.md`` -> "Property-to-test
mapping" and "``paper/paper_events.py`` and the Paper_Channel". Requirements 19.3, 19.8.

THE ONE PROPERTY LIVING HERE
----------------------------
``test_p53_channel_sequence_is_contiguous_from_one``   (Requirements 19.3, 19.8)

Exactly one ``test_p{n}_`` function at module scope, and nothing nested carries that prefix:
``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and would count a nested
helper too, so a helper named ``test_p53_...`` inside a body would claim P-53 twice.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
Real: ``paper_events.record_event`` (the emit path: allocate, build, append),
``paper_repository.allocate_session_event_sequence`` (the version-guarded optimistic swap - three
attempts, no in-process counter), ``paper_repository.insert_session_event``,
``paper_repository.next_session_event_sequence`` (task 24.4's feed writer's allocator),
``paper_channel.replay`` and ``paper_channel.broadcast``.

Doubled: only the Persistence_Layer, through ``tests/test_paper_repository.FakeSupabase`` - the one
double this repository has, which enforces ``uq_paper_event_seq UNIQUE (session_id, sequence)`` and
``uq_paper_event_id UNIQUE (session_id, event_id)``, the two indexes this property leans on. There
is no mock of the repository, of ``paper_events`` or of the channel.

The harness is ``tests/test_task_26_4_paper_channel_delivery.py``'s, imported rather than rebuilt -
``Connection``, ``_client``, ``_session_row``, ``_tick_payload``, ``_registry``, ``_subscribe`` and
the ``_fresh_state`` autouse fixture (which clears the module-scope owner cache and the persistence
probe around the run) - and the census is ``tests/property/paper_census.py``'s. Coroutines are
driven by ``tests/test_paper_order_lifecycle_writes._run_coroutine``, which uses the process's ONE
event loop: ``asyncio.run`` appears nowhere here, because a fresh loop per call exhausts Windows'
ephemeral port range through ``socket._fallback_socketpair`` and hung this suite.

HOW TWO CONCURRENT PRODUCERS ARE MADE TO GENUINELY INTERLEAVE
------------------------------------------------------------
Task 26.6 asks for "emissions interleaved from two concurrent producers", and a run in which no
allocation ever collided would prove nothing about contiguity. ``asyncio.gather`` alone cannot
deliver that here: the Persistence_Layer double is synchronous, so one producer's whole
read-then-swap runs to completion before the other's first statement. So the second producer runs
BOTH ways, and the census counts what actually happened rather than what was intended:

* **At the scheduler.** Both producers are coroutines gathered together, each yielding with
  ``await asyncio.sleep(0)`` between emissions. That is real concurrency of the two emit loops.
* **Inside the first producer's request window**, through the double's two seams - the same
  mechanism ``tests/test_paper_order_lifecycle_writes._version_racer`` uses:

  - ``after_select`` on ``paper_sessions``: the read of ``event_sequence`` has completed and its row
    is already copied out, so a second producer that emits an event HERE leaves the first holding a
    correct image of a counter that has since moved. Its guarded ``UPDATE ... WHERE event_sequence =
    N`` then matches zero rows and it retries - a genuine allocation collision, counted below.
  - ``before_insert`` on ``paper_events``: the row has not landed yet, so a second writer that
    appends at the same sequence HERE makes the first's INSERT violate ``uq_paper_event_seq``.
    That second writer uses ``paper_repository.next_session_event_sequence`` -
    ``max(sequence) + 1`` over the log, which is task 24.4's feed writer's allocator and a real
    production path - because that is precisely the writer the counter's optimistic swap cannot
    arbitrate against, and the database's refusal is what does.

Both intrusions are the same production code the first producer runs. Neither is a mock, and
neither weakens anything: the retry that follows a refusal is the one ``record_event``'s docstring
prescribes for its caller ("the caller retries with a fresh allocation"), written here because task
27's session loop - the production caller - does not exist yet.

THE ORACLE
----------
``{1, 2, ..., k}``, computed by the test from ``k = len(rows)`` and compared with the SET of
``sequence`` values the log holds. Nothing about the expectation is obtained by asking the subject
what it allocated: ``paper_sessions.event_sequence`` is compared against that same ``k``
afterwards, as a separate claim, rather than being used to derive it.

Every comparison is exact: sequences are ``int``, sets are compared with ``==``, and the delivered
order is compared against ``list(range(1, k + 1))``. There is no tolerance anywhere, and none would
mean anything here.

WHAT THIS PROPERTY DOES **NOT** ESTABLISH - THE HONEST SCOPE
-----------------------------------------------------------
The allocation and the ``paper_events`` INSERT that consumes it are **two PostgREST requests with
no transaction around them** (``allocate_session_event_sequence`` records why: no ``BEGIN``, no
``FOR UPDATE``, and no server-side expression in a ``PATCH``). A process that dies between them
burns a number and leaves a HOLE in that session's sequence, and no amount of in-process testing
can rule that out - closing the gap needs both statements inside one database function (``rpc``),
which is a deployment change.

**P-53 is therefore a property of the LIVE emit path, not of crash recovery.** What it does
establish is that no interleaving of two producers, no lost compare-and-swap, and no
duplicate-sequence refusal can produce a hole or a repeat while the process stays up: a loser never
reuses the number it read, a refused INSERT is retried with a FRESH allocation, and an exhausted
allocation emits nothing at all rather than emitting at a number it does not own.

Two smaller boundaries, stated rather than implied:

1. **One process.** The swap is atomic in the database, so two instances are arbitrated by the same
   predicate; that is an argument about the statement, not a measurement, and this file does not
   claim to have measured it.
2. **Live delivery order is asserted over frames emitted in ascending sequence**, which is what a
   session loop does (allocate, append, broadcast, one event at a time) and what
   ``paper_channel.broadcast``'s contract is written against. This file feeds the delivery loop from
   ``paper_channel.replay`` - the real ascending read of Requirement 19.8 - so the order under test
   is the log's own, not one the test chose.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_channel as pc
from backend_app.backend.paper import paper_events as events
from backend_app.backend.paper import paper_repository as repo

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The one event loop this process has. Never ``asyncio.run``. ───────────────────────────
from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run
from tests.test_paper_repository import OTHER_SESSION, SESSION, USER

# ── The Paper_Channel harness of task 26.4, imported rather than rebuilt. ─────────────────
from tests.test_task_26_4_paper_channel_delivery import (  # noqa: F401 - _fresh_state is autouse
    NOW,
    Connection,
    _client,
    _fresh_state,
    _registry,
    _session_row,
    _subscribe,
    _tick_payload,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example drives all five interleavings below, so ``too_slow`` is suppressed rather
#: than the example count being cut.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: How many times the producer re-emits after ``uq_paper_event_seq`` refuses its INSERT. Three, so a
#: forced pair of consecutive refusals still lands - and it is a RETRY WITH A FRESH ALLOCATION,
#: which is the whole reason a refusal cannot cost the log its contiguity.
EMIT_ATTEMPTS = 3

#: The five interleavings, every one of them FORCED into every example. Drawing one of them per
#: example would make each census floor a coin toss over the run rather than a guaranteed minimum,
#: and P-53 is trivially true in the ``single`` shape - which is exactly the shape a freely drawn
#: schedule spends most of its time in.
#:
#: ``single``     one producer, no intrusion at all                  -> the baseline
#: ``collide``    one or two lost compare-and-swaps                  -> Requirement 19.3 under a race
#: ``exhaust``    three consecutive losses, so the allocation gives up -> nothing is emitted, and
#:                                                                      no number is burned
#: ``duplicate``  the feed writer takes the sequence first           -> uq_paper_event_seq refuses
#:                                                                      the INSERT, and the retry
#:                                                                      keeps the log contiguous
#: ``mixed``      both seams in one run
SHAPES: Tuple[str, ...] = ("single", "collide", "exhaust", "duplicate", "mixed")


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATED CASE, AND THE PLAN EACH SHAPE MAKES OF IT
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Case:
    """The numbers one example draws. The five shapes are forced; their sizes are generated."""

    lead: int
    extra_from_a: int
    events_from_b: int
    swap_run: int
    insert_run: int
    second_session_events: int


@dataclass(frozen=True)
class _Plan:
    """One run: how many events each producer emits, and where the second one intrudes.

    ``swaps`` is consumed one token per ``paper_sessions`` read, ``inserts`` one token per
    ``paper_events`` INSERT attempt. A ``True`` token intrudes; a ``False`` one lets the statement
    through, which is how a run reaches its second or third emission before anything collides.
    """

    shape: str
    events_from_a: int
    events_from_b: int
    swaps: Tuple[bool, ...]
    inserts: Tuple[bool, ...]

    @property
    def expects_collisions(self) -> bool:
        return any(self.swaps)

    @property
    def expects_duplicates(self) -> bool:
        return any(self.inserts)

    @property
    def expects_exhaustion(self) -> bool:
        # Three consecutive intrusions inside one allocation is exactly
        # ``SEQUENCE_ALLOCATION_ATTEMPTS`` losses, which is where the allocator gives up.
        return self.swaps[: repo.SEQUENCE_ALLOCATION_ATTEMPTS] == (True,) * (
            repo.SEQUENCE_ALLOCATION_ATTEMPTS
        )


@st.composite
def emission_plans(draw: Any) -> _Case:
    """The sizes one example uses, for all five interleavings.

    ``lead`` is what makes the collision land on a LATER emission rather than always on the first:
    a leading ``False`` token lets one allocation through untouched, so the second producer arrives
    at a session whose counter is already above zero. ``events_from_a`` is floored at
    ``1 + lead`` for a mechanical reason worth naming - a token is only consumed by a statement
    that is actually issued, so a plan with more leading tokens than emissions would leave its
    intrusions unfired and the census, which counts what HAPPENED, would report the shortfall.
    """
    return _Case(
        lead=draw(st.integers(min_value=0, max_value=1)),
        extra_from_a=draw(st.integers(min_value=0, max_value=2)),
        events_from_b=draw(st.integers(min_value=0, max_value=2)),
        swap_run=draw(st.integers(min_value=1, max_value=2)),
        insert_run=draw(st.integers(min_value=1, max_value=2)),
        second_session_events=draw(st.integers(min_value=1, max_value=2)),
    )


def _plan_for(case: _Case, shape: str) -> _Plan:
    """``case`` as the plan ``shape`` runs. One function, so every shape reads off one case."""
    if shape == "single":
        return _Plan(shape, 1 + case.lead + case.extra_from_a, case.events_from_b, (), ())
    if shape == "collide":
        return _Plan(
            shape,
            1 + case.lead + case.extra_from_a,
            case.events_from_b,
            (False,) * case.lead + (True,) * case.swap_run,
            (),
        )
    if shape == "exhaust":
        # No lead: the three losses have to fall inside ONE allocation to exhaust it.
        return _Plan(
            shape,
            1 + case.extra_from_a,
            case.events_from_b,
            (True,) * repo.SEQUENCE_ALLOCATION_ATTEMPTS,
            (),
        )
    if shape == "duplicate":
        return _Plan(
            shape,
            1 + case.lead + case.extra_from_a,
            case.events_from_b,
            (),
            (False,) * case.lead + (True,) * case.insert_run,
        )
    if shape == "mixed":
        # ``swap_run`` is capped at 2, so this shape collides without ever exhausting - the two
        # failures stay distinguishable in the census.
        return _Plan(
            shape,
            1 + case.lead + case.extra_from_a,
            case.events_from_b,
            (False,) * case.lead + (True,) * case.swap_run,
            (False,) * case.lead + (True,),
        )
    raise AssertionError(f"unknown shape {shape!r}")


# ══════════════════════════════════════════════════════════════════════════
# THE PRODUCERS
# ══════════════════════════════════════════════════════════════════════════


class _Tally:
    """What one run OBSERVED, kept separately from what its plan intended.

    ``allocations_returned`` counts every call that came back holding a number - including the one
    whose INSERT was then refused, because that number was still allocated. It is what makes the
    collision count derivable from the double's own statement log rather than from this harness's
    intentions: every allocation attempt issues exactly one ``UPDATE`` on ``paper_sessions``, so

        collisions = (updates on paper_sessions) - (allocations that returned a number)

    is the number of compare-and-swaps that matched zero rows, measured on the subject.
    """

    __slots__ = ("allocations_returned", "duplicates", "exhausted", "feed_rows", "index")

    def __init__(self) -> None:
        self.allocations_returned = 0
        self.duplicates = 0
        self.exhausted = 0
        self.feed_rows = 0
        self.index = 0

    def next_index(self) -> int:
        self.index += 1
        return self.index


def _emit(
    client: Any,
    tally: _Tally,
    recorder: Recorder,
    *,
    session_id: str = SESSION,
) -> Optional[events.PaperEventEnvelope]:
    """One emission through the production writer, with the retry ``record_event`` prescribes.

    Returns the envelope, or ``None`` when the allocation was exhausted - in which case nothing was
    written and no number was consumed, which is the branch that keeps a lost race from becoming a
    hole.
    """
    index = tally.next_index()
    for _attempt in range(EMIT_ATTEMPTS):
        try:
            envelope = events.record_event(
                client,
                user_id=USER,
                session_id=session_id,
                event_type=events.PaperEvent.MARKET_TICK,
                payload=_tick_payload(index),
                emitted_at=NOW + timedelta(seconds=index),
            )
        except repo.PaperDuplicateSessionEvent:
            # The number was allocated and then refused at the INSERT. Retried with a FRESH
            # allocation, never with the refused one.
            tally.allocations_returned += 1
            tally.duplicates += 1
            recorder.mark("duplicate_insert_refused")
            continue
        except repo.PaperConcurrencyConflict:
            tally.exhausted += 1
            recorder.mark("allocation_exhausted")
            return None
        tally.allocations_returned += 1
        return envelope
    return None


class _ConcurrentWriter:
    """The second producer, and the two seams that put its statements inside the first's.

    One instance per run. ``busy`` is a re-entrancy guard rather than a lock: the intruding writer
    issues its own reads and inserts, and without the guard its ``paper_sessions`` read would fire
    the ``after_select`` seam again and recurse without bound.
    """

    def __init__(
        self,
        client: Any,
        plan: _Plan,
        tally: _Tally,
        recorder: Recorder,
    ) -> None:
        self.client = client
        self.tally = tally
        self.recorder = recorder
        self._swaps: Deque[bool] = deque(plan.swaps)
        self._inserts: Deque[bool] = deque(plan.inserts)
        self.busy = False

    # ── seam 1: the read-then-swap race (Requirement 19.3) ────────────────

    def after_select(self, client: Any, query: Any) -> None:
        """Emit a whole event while the other producer holds a stale ``event_sequence``."""
        if query.table_name != repo.SESSIONS_TABLE or query.op != "select":
            return
        if self.busy or not self._swaps or not self._swaps.popleft():
            return
        self.busy = True
        try:
            _emit(client, self.tally, self.recorder)
        finally:
            self.busy = False

    # ── seam 2: the duplicate-sequence refusal (uq_paper_event_seq) ────────

    def before_insert(self, client: Any, query: Any) -> None:
        """Append at the sequence the other producer is about to use, through the feed writer.

        ``next_session_event_sequence`` is ``max(paper_events.sequence) + 1``: task 24.4's writer,
        which reads the LOG rather than the counter and therefore cannot be arbitrated by the
        counter's compare-and-swap. ``uq_paper_event_seq`` arbitrates it instead, and that refusal
        is what the other producer then retries around.
        """
        if query.table_name != repo.EVENTS_TABLE or query.op != "insert":
            return
        if self.busy or not self._inserts or not self._inserts.popleft():
            return
        self.busy = True
        try:
            index = self.tally.next_index()
            sequence = repo.next_session_event_sequence(client, USER, SESSION)
            envelope = events.build_envelope(
                session_id=SESSION,
                event_type=events.PaperEvent.MARKET_TICK,
                payload=_tick_payload(index),
                sequence=sequence,
                emitted_at=NOW + timedelta(seconds=index),
            )
            repo.insert_session_event(
                client,
                session_id=envelope.session_id,
                user_id=USER,
                sequence=envelope.sequence,
                event_id=envelope.event_id,
                event_type=envelope.type.value,
                schema_version=envelope.schema_version,
                payload=envelope.payload,
                emitted_at=envelope.emitted_at,
            )
            self.tally.feed_rows += 1
        finally:
            self.busy = False

    def arm(self) -> None:
        self.client.after_select = self.after_select
        self.client.before_insert = self.before_insert

    def disarm(self) -> None:
        """Take both seams off, so every later phase of the run is uninterfered with."""
        self.client.after_select = None
        self.client.before_insert = None


async def _produce(client: Any, count: int, tally: _Tally, recorder: Recorder) -> None:
    """One producer's emit loop, yielding between events so the two genuinely interleave."""
    for _ in range(count):
        _emit(client, tally, recorder)
        await asyncio.sleep(0)


async def _produce_concurrently(
    client: Any, plan: _Plan, tally: _Tally, recorder: Recorder
) -> None:
    """Both producers, gathered.

    ``asyncio.gather`` is called from INSIDE a coroutine on purpose: called from synchronous code it
    binds its future to the event-loop policy's loop, which is not the harness's one loop, and
    ``run_until_complete`` then refuses it ("the future belongs to a different loop"). Awaited here,
    it binds to the loop that is running - which is the process's only loop.
    """
    await asyncio.gather(
        _produce(client, plan.events_from_a, tally, recorder),
        _produce(client, plan.events_from_b, tally, recorder),
    )


# ══════════════════════════════════════════════════════════════════════════
# READERS AND THE ORACLE
# ══════════════════════════════════════════════════════════════════════════


def _log(client: Any, session_id: str = SESSION) -> List[Dict[str, Any]]:
    """One session's ``paper_events`` rows, in insertion order.

    Read straight off the double rather than through the replay read, deliberately: the replay read
    is one of the things under test, so using it to establish what the log CONTAINS would let a
    replay that dropped a row agree with itself.
    """
    return [dict(row) for row in client.events if str(row.get("session_id")) == session_id]


def _swap_attempts(client: Any) -> int:
    """Every compare-and-swap the allocator issued: one ``UPDATE`` per attempt, won or lost."""
    return sum(
        1
        for statement in client.statements
        if statement.table_name == repo.SESSIONS_TABLE and statement.op == "update"
    )


def _counter(client: Any, session_id: str = SESSION) -> int:
    for row in client.sessions:
        if str(row.get("id")) == session_id:
            return int(row.get("event_sequence") or 0)
    raise AssertionError(f"no paper_sessions row {session_id!r} is stored")


P53_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "runs": EXAMPLES * len(SHAPES),
    "single_producer_run": EXAMPLES,
    "two_producer_run": EXAMPLES * (len(SHAPES) - 1),
    # ``collide`` forces at least one, ``exhaust`` exactly three, ``mixed`` at least one.
    "allocation_collision": EXAMPLES * 5,
    # ``duplicate`` forces at least one, ``mixed`` exactly one.
    "duplicate_insert_refused": EXAMPLES * 2,
    "allocation_exhausted": EXAMPLES,
    "events_emitted": EXAMPLES * len(SHAPES),
    "replayed_the_whole_log": EXAMPLES * len(SHAPES),
    "delivered_to_two_subscriptions": EXAMPLES * len(SHAPES),
    "durable_uq_paper_event_id_refused": EXAMPLES * len(SHAPES),
    "second_session_started_again_at_one": EXAMPLES * len(SHAPES),
}

P53_LABELS: Dict[str, str] = {
    "single_producer_run": "one producer, no intrusion",
    "two_producer_run": "two producers, interleaved at a statement seam",
    "allocation_collision": "a compare-and-swap matched zero rows and was retried",
    "duplicate_insert_refused": "uq_paper_event_seq refused a duplicate sequence",
    "allocation_exhausted": "an allocation lost three times and emitted nothing",
    "durable_uq_paper_event_id_refused": "uq_paper_event_id refused a repeated event_id",
    "second_session_started_again_at_one": "a second session numbered from 1 independently",
}


# ══════════════════════════════════════════════════════════════════════════
# ONE RUN OF THE PROPERTY
# ══════════════════════════════════════════════════════════════════════════


def _assert_the_log_is_contiguous_from_one(
    case: _Case, shape: str, recorder: Recorder
) -> None:
    """P-53 for one interleaving: the log, the counter, and what a subscriber received."""
    plan = _plan_for(case, shape)
    recorder.mark("runs")
    client = _client(
        sessions=[_session_row(), _session_row(OTHER_SESSION, USER)]
    )
    tally = _Tally()
    writer = _ConcurrentWriter(client, plan, tally, recorder)
    writer.arm()

    # ── the two producers, gathered ───────────────────────────────────────
    _run(_produce_concurrently(client, plan, tally, recorder))
    writer.disarm()

    context = (
        f"shape {shape}, plan {plan!r}, case {case!r}; observed "
        f"allocations={tally.allocations_returned} duplicates={tally.duplicates} "
        f"exhausted={tally.exhausted} feed_rows={tally.feed_rows}"
    )

    # ── what actually happened, measured on the subject ───────────────────
    collisions = _swap_attempts(client) - tally.allocations_returned
    assert collisions >= 0, (
        f"P-53 harness: more allocations returned than compare-and-swaps were issued, which "
        f"cannot happen; the collision count would be meaningless. {context}"
    )
    if collisions:
        recorder.mark("allocation_collision", collisions)
    if collisions or tally.duplicates or tally.feed_rows:
        recorder.mark("two_producer_run")
    else:
        recorder.mark("single_producer_run")

    # The intrusions a shape FORCES must have fired. Asserted here rather than left to the census,
    # so a seam that silently stopped working is a failure naming its shape instead of a floor
    # shortfall a hundred examples later.
    if plan.expects_collisions:
        assert collisions >= 1, (
            f"P-53 would be vacuous for this shape: no compare-and-swap was lost, so nothing "
            f"about contiguity under contention was exercised. {context}"
        )
    else:
        assert collisions == 0, (
            f"P-53: an unplanned allocation collision occurred, so the ``single`` baseline is not "
            f"a baseline. {context}"
        )
    if plan.expects_duplicates:
        assert tally.duplicates >= 1, (
            f"P-53 would be vacuous for this shape: uq_paper_event_seq never refused an INSERT. "
            f"{context}"
        )
    if plan.expects_exhaustion:
        assert tally.exhausted >= 1, (
            f"P-53: three consecutive losses were forced but the allocation still succeeded, so "
            f"the give-up branch - the one that emits NOTHING rather than emitting at a number it "
            f"does not own - was never entered. {context}"
        )

    # ── the oracle: {1..k}, computed here ─────────────────────────────────
    rows = _log(client)
    sequences = [int(row["sequence"]) for row in rows]
    k = len(rows)
    assert k >= 1, f"P-53: nothing was emitted, so there is no sequence to be contiguous. {context}"
    recorder.mark("events_emitted", k)

    assert set(sequences) == set(range(1, k + 1)), (
        f"P-53 (Requirement 19.3): the {k} event(s) of this session carry sequences "
        f"{sorted(sequences)}, which is not {{1..{k}}}. A missing value is a HOLE and an extra one "
        f"is a repeat; either makes a reconnecting client's replay unreconcilable. {context}"
    )
    assert len(sequences) == len(set(sequences)), (
        f"P-53: a sequence value appears more than once: {sorted(sequences)}. {context}"
    )
    event_ids = [str(row["event_id"]) for row in rows]
    assert len(event_ids) == len(set(event_ids)), (
        f"P-53 (Requirement 19.3): an event_id appears more than once in the session, so a client "
        f"deduplicating on it would discard a real event. {context}"
    )

    # The counter agrees with the log: every number the allocator issued was consumed by exactly
    # one row. This is the claim the "two requests, no transaction" gap in the module docstring is
    # about - it holds for the live path, and a process death between the two statements is what
    # would break it.
    assert _counter(client) == k, (
        f"P-53: paper_sessions.event_sequence stands at {_counter(client)} while the log holds {k} "
        f"row(s), so a number was allocated and never written - a hole waiting for the next "
        f"emission. {context}"
    )

    # ── uq_paper_event_id, asked directly (Requirement 19.3) ──────────────
    # The emitter's event_id is a UUID4, so "each event_id appears once" above is nearly free. The
    # DURABLE arbiter is therefore exercised on its own: a fresh sequence with an event_id the log
    # already holds must be refused.
    try:
        repo.insert_session_event(
            client,
            session_id=SESSION,
            user_id=USER,
            sequence=k + 1,
            event_id=event_ids[0],
            event_type=events.PaperEvent.MARKET_TICK.value,
            schema_version=events.PAPER_EVENT_SCHEMA_VERSION,
            payload=events.payload_jsonb(
                events.validate_payload(events.PaperEvent.MARKET_TICK, _tick_payload(1))
            ),
            emitted_at=NOW,
        )
    except repo.PaperDuplicateSessionEvent:
        recorder.mark("durable_uq_paper_event_id_refused")
    else:
        raise AssertionError(
            f"P-53 (Requirement 19.3): uq_paper_event_id accepted a second row carrying "
            f"{event_ids[0]!r}, so 'an event identifier unique within the session' is not enforced "
            f"by the Persistence_Layer. {context}"
        )
    assert len(_log(client)) == k, (
        f"P-53: the refused INSERT left a row behind. {context}"
    )

    # ── a second session numbers itself from 1, independently ─────────────
    # The counter is per session (Requirement 19.3's "per-session sequence number"), so a session
    # created alongside one that has already emitted ``k`` events still starts at 1. An in-process
    # counter shared across sessions would pass every assertion above and fail this one.
    for _ in range(case.second_session_events):
        _emit(client, tally, recorder, session_id=OTHER_SESSION)
    other = _log(client, OTHER_SESSION)
    assert [int(row["sequence"]) for row in other] == list(
        range(1, case.second_session_events + 1)
    ), (
        f"P-53 (Requirement 19.3): the second session's sequences are "
        f"{[row['sequence'] for row in other]} rather than "
        f"{list(range(1, case.second_session_events + 1))}, so the counter is not per session. "
        f"{context}"
    )
    recorder.mark("second_session_started_again_at_one")

    # ── delivery: ascending, no gap, no repetition (Requirement 19.8) ─────
    registry = _registry()
    first, second = Connection("first"), Connection("second")
    _subscribe(registry, first)
    _subscribe(registry, second)

    outcome = registry.replay(client, session_id=SESSION, user={"id": USER}, last_sequence=0)
    assert outcome.history_incomplete is False, (
        f"P-53: the replay of a contiguous log reported HISTORY_INCOMPLETE. {context}"
    )
    replayed = [int(frame["sequence"]) for frame in outcome.frames]
    assert replayed == list(range(1, k + 1)), (
        f"P-53 (Requirement 19.8): the replay produced {replayed} rather than 1..{k} in ascending "
        f"order. {context}"
    )
    recorder.mark("replayed_the_whole_log")

    for frame in outcome.frames:
        delivered = _run(registry.broadcast(SESSION, frame, supabase=client))
        assert delivered.released == (), (
            f"P-53: broadcasting sequence {frame['sequence']} released a subscription "
            f"({delivered!r}). {context}"
        )
        assert len(delivered.delivered) == 2, (
            f"P-53: sequence {frame['sequence']} reached {len(delivered.delivered)} of the two "
            f"subscriptions. {context}"
        )

    for connection in (first, second):
        received = connection.sequences()
        assert received == list(range(1, k + 1)), (
            f"P-53 (Requirements 19.3, 19.8): {connection.name} received {received} rather than "
            f"1..{k} in ascending order with no gap and no repetition. {context}"
        )
        ids = [frame["event_id"] for frame in connection.frames()]
        assert ids == event_ids, (
            f"P-53: {connection.name} received event ids {ids}, which is not the log's own order "
            f"{event_ids}. {context}"
        )
        assert len(ids) == len(set(ids)), (
            f"P-53: {connection.name} received one event_id twice. {context}"
        )
    recorder.mark("delivered_to_two_subscriptions")

    _run(registry.release_session(SESSION, reason="the run is over"))


def test_p53_channel_sequence_is_contiguous_from_one(request: Any) -> None:
    """A session's emitted sequences are exactly ``{1..k}``, once each, delivered in order.

    For all sessions and all generated emission sequences - including emissions interleaved from
    two concurrent producers, a lost compare-and-swap, an allocation that gives up after three
    losses, and an INSERT that ``uq_paper_event_seq`` refuses - the set of ``sequence`` values the
    session's log holds is exactly ``{1, 2, ..., k}``, each value appears once, each ``event_id``
    appears once, and the events delivered on a subscription arrive in ascending ``sequence`` order
    with no gap and no repetition.

    The oracle is ``{1..k}`` computed by this test from the row count.
    ``paper_sessions.event_sequence`` is compared against ``k`` as a separate claim afterwards, so
    the expectation is never derived from the counter it is checking.

    The interleaving is genuine, not simulated: the second producer's statements land inside the
    first's request window through the double's ``after_select`` and ``before_insert`` seams, and
    the census reports the number of compare-and-swaps that actually matched zero rows - measured
    from the statement log, not from what the plan intended.

    Scope: this is a property of the live emit path. The allocation and the INSERT are two requests
    with no transaction around them, so a process that dies between them burns a number; see the
    module docstring.

    **Validates: Requirements 19.3, 19.8**
    """
    recorder = Recorder("P-53", P53_FLOORS, P53_LABELS)

    @PROPERTY_SETTINGS
    @given(case=emission_plans())
    def check(case: _Case) -> None:
        recorder.start()
        recorder.mark("examples")
        for shape in SHAPES:
            pc.invalidate_session_owner()
            _assert_the_log_is_contiguous_from_one(case, shape, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()
