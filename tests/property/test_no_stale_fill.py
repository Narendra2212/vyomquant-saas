"""
tests/property/test_no_stale_fill.py - P-56, and nothing else.

Spec: marketplace-subscriptions-paper-trading task 24.6. Requirements 14.5, 18.15.

**Property P-56 (invariant, stale price)** - for all generated streams and all disconnection
points within them, no fill is applied while ``feed_state != 'HEALTHY'``, and every fill after a
reconnection uses a price from an event that passed validation **after** that reconnection.

WHY THE TASK WAS DEFERRED, AND WHAT IT NOW DRIVES
-------------------------------------------------
Task 24.6 was written with task 24.4, which owns the feed's half of Requirement 14.5: mark the
session ``DEGRADED``, write one ``paper_error``, back off, and resume only from an event that
passes validation. The *simulator's* half - "neither accepts a market order nor fills a resting
limit order" - had no code to test at 24.4: ``paper_market_feed.admit_execution`` existed and
nothing called it, which
``tests/test_paper_market_feed_events.TestTheFeedGateIsOneNamedCallableThatAdmitsOnlyHealthy``
records as the defect it found. Tasks 25.1-25.6 landed the call site. This module therefore drives
**both** halves against each other, which is what P-56 is a property of and what neither half can
establish alone:

* the real ``paper_market_feed.next_validated_event`` receives the generated stream over the real
  ``mds:data:*`` wire shape and writes the session's ``feed_state`` where the simulator reads it;
* the real ``paper_simulator.submit_intent`` is then asked - at **every** step, including every
  step of every outage - to accept and fill a market order priced from the latest validated event
  the session has, which during an outage is a **pre-disconnection** price. That request is the
  adversary: the property is that it is refused.

Nothing between them is stubbed. There is one ``FakeSupabase``, so the row the feed writes is the
row ``admit_execution`` reads, and the token ``apply_fill`` requires can only have been minted by
the gate.

THE ORACLE: THE POST-RECONNECTION VALIDATED-EVENT SET, COMPUTED HERE
-------------------------------------------------------------------
:func:`_plan` walks the generated step list and states, for each step, three things computed from
the stream alone:

1. the ``feed_state`` a correct session is in - ``DEGRADED`` at an outage, and ``HEALTHY`` only at
   a step whose candle the projection **accepted**;
2. the set of event identities validated **since the most recent reconnection**, emptied at every
   outage;
3. which accepted candle the session's latest validated price came from.

The projection is ``tests/property/paper_market_streams.project`` - the distinct-and-sorted
reading P-54 uses - and the identity is that module's ``identity_of``, a ``sha256`` recomputed from
this test material rather than borrowed from ``feed.source_event_id``. So the oracle calls nothing
in ``paper_market_feed`` and nothing in ``paper_simulator``.

Point 1 is the load-bearing one, and it is *not* "the state the module says it is in": the
assertion reads ``paper_sessions.feed_state`` out of the store at every step and compares it with
the oracle's value, so a module that reported ``HEALTHY`` in process while the row said
``DEGRADED`` - or the reverse, which is the window a fill would slip through - fails here.

WHY THE SECOND CONJUNCT IS ASSERTED ON THE IDENTITY AND NOT ON THE NUMBER
------------------------------------------------------------------------
"a price from an event that passed validation after that reconnection" cannot be checked by
comparing prices. The generator draws from four close families, so a pre-disconnection candle and a
post-reconnection one routinely carry the **same** number; an assertion that the recorded price
differs from every pre-outage price would be false for a correct session. What distinguishes them
is the event, so the conjunct is asserted twice over:

* the ``source_event_id`` of the event the fill was priced from is in the post-reconnection set the
  oracle computed - and that set was emptied at the outage, so a fill priced from anything the
  session saw earlier fails;
* the price actually written to ``paper_fills.price`` equals this file's own adverse-slippage
  transform of that event's close (:func:`_adverse_market_price`), recomputed from the frozen
  config rather than by calling ``market_fill_price``. So "the price came from that event" is
  arithmetic and not an association.

THE ADVERSARY IS THE STALE PRICE, DELIBERATELY
----------------------------------------------
At every step the driver offers the fill at the price of the latest event the session validated -
it does **not** withhold the request during an outage, and it does not refresh the price. During an
outage that price is by construction the last close before the disconnection, which is exactly the
figure Requirement 14.5 forbids being "treated as current". So the refusal is asserted against the
worst case rather than against a caller that had already given up.

TWO ARRIVALS THAT MUST NOT END AN OUTAGE, AND WHY EVERY EXAMPLE CONTAINS BOTH
-----------------------------------------------------------------------------
Requirement 14.5 resumes from the first event that **passes validation** - and, in the module, is
also recorded: ``next_validated_event`` moves the feed state at step 10, after the
``paper_market_events`` insert, so a dropped arrival leaves the state alone. A duplicate and an
out-of-order candle are precisely the arrivals that could be mistaken for a recovery, and the
relabelled stream (see ``paper_market_streams``' module docstring) puts both immediately after the
forced outage in every example: the exact republication of the pre-outage candle, then its
respelled republication. Both are dropped, the session must stay ``DEGRADED``, and the fill offered
at the stale price must still be refused. A module that ended the outage on any arrival would pass
a test that only fed it a valid candle after the gap.

WHAT IS COMPARED, AND WITH WHAT TOLERANCE
-----------------------------------------
None. Prices are ``Decimal`` compared with ``==``; the whole ledger is compared structurally by
``tests/test_paper_order_lifecycle_writes._snapshot`` across all seven account-scoped tables, so
"no fill is applied" is a claim about every one of them rather than about ``paper_fills`` alone.

WHY ``_run_coroutine`` IS THE SIMULATOR HARNESS'S AND NOT THE FEED HARNESS'S
---------------------------------------------------------------------------
``tests/test_paper_order_lifecycle_writes._run_coroutine`` drives the process's **one** event loop;
``tests/test_paper_market_feed_selection._run_coroutine`` builds and closes a fresh loop per call.
Both are ``_run_coroutine`` and neither is ``asyncio.run``, but on Windows a loop is two real
loopback TCP connections whose ports sit in ``TIME_WAIT`` after it closes, and that is the
documented cause of the hang the harness's :data:`_HARNESS_LOOP` note describes. This module drives
a feed delivery **and** a simulator submission at every one of up to twenty steps of every one of a
hundred examples, so the deliveries run on the shared loop. Opening the subscription still goes
through the feed harness's ``_feed_on``, which is once per example.

GAPS LEFT OPEN
--------------
* **The resting-limit half of the gate is asserted by 25.x, not here.** P-56 drives a market order,
  because a market order fills on acceptance and therefore makes "no fill while degraded" a claim
  about one call. ``check_resting_orders``' refusal on a degraded feed has its named case in
  ``tests/test_paper_order_lifecycle_writes.test_a_limit_order_is_accepted_on_a_degraded_feed_and_simply_does_not_fill``,
  and P-55 in ``test_no_synthesised_price.py`` drives resting fills for their prices. Folding a
  resting order into this stream would put two order lifecycles into one example without adding a
  disconnection case.
* **``FALLBACK_REST`` and ``TRANSPORT_UNKNOWN`` are not generated states here.** Every frame carries
  ``transport: WEBSOCKET``, so the non-healthy state this stream reaches is ``DEGRADED`` (plus
  ``PENDING`` before the first event). That the gate refuses all four is asserted exhaustively and
  by parametrisation over ``feed.FEED_STATES`` in
  ``tests/test_paper_market_feed_events.TestTheFeedGateIsOneNamedCallableThatAdmitsOnlyHealthy``;
  generating a transport per candle would make the feed-state oracle a second copy of
  ``FEED_STATE_FOR_TRANSPORT`` rather than an independent statement.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.paper_market_feed import FeedNotHealthy
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ── The census, written once for the paper property modules that need it. ──────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The one generated stream, shared with P-54 and P-55. ───────────────────────────────────
from tests.property.paper_market_streams import (
    DROPPED_DUPLICATE,
    Candle,
    Verdict,
    adverse_slippage_price,
    identity_of,
    market_events_for,
    project,
    recorded_close,
    single_symbol_market_event_streams,
    wire_frame,
)

# ── The feed harness. A second market-data double would be a second account of the wire. ──
from tests.test_paper_market_feed_events import _feed_on
from tests.test_paper_market_feed_events import (  # noqa: F401 - used by name as fixtures
    counters,
    no_sleeping,
)
from tests.test_paper_market_feed_selection import (
    PROCESSED_AT,
    SESSION,
    SYMBOL,
    USER,
    _client,
)
from tests.test_paper_market_feed_selection import _config as _feed_config
from tests.test_paper_market_feed_selection import (  # noqa: F401 - autouse, used by name
    _fresh_probe,
)

# ── The simulator harness. See "WHY ``_run_coroutine``" in the module docstring. ───────────
from tests.test_paper_order_lifecycle_writes import _config as _session_config
from tests.test_paper_order_lifecycle_writes import (
    _intent,
    _run_coroutine,
    _snapshot,
    _wrote_since,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example opens a subscription, drives up to fourteen candles and up to four
#: outages through the real validator, and asks the real simulator to fill at every step, so
#: ``too_slow`` is suppressed rather than the example count being cut.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: The capital the session starts on. Generous on purpose: an order that ran out of funds would be
#: persisted ``REJECTED`` with ``INSUFFICIENT_FUNDS`` **before** the feed gate is consulted
#: (``submit_intent`` step 4 precedes it), and the refusal this property is about would then never
#: be reached. Twenty fills of :data:`ORDER_QUANTITY` at the stream's ~60,000 closes cost about
#: 1,200, so the balance is never the binding constraint.
CAPITAL = Decimal("500000")

#: One order's quantity, at the ``0.00000001`` quantity precision the harness's market metadata
#: reports. Small enough that every step of every example can afford a fill.
ORDER_QUANTITY = "0.001"

#: A market order, because a market order fills on acceptance - so "no fill is applied" and "the
#: order is not even accepted" are one observation rather than two.
SIDE = "buy"

#: The forced outage's insertion point: immediately **after** the first candle of the spine, which
#: the projection accepts. So every example contains an outage that interrupts a healthy session
#: mid-stream, and the two arrivals that follow it are the spine's exact and respelled
#: republications - the two that must not end it.
FORCED_OUTAGE_POSITION = 1

#: How many extra outages may be drawn, on top of the forced one.
MAX_EXTRA_OUTAGES = 3

#: The ``paper_sessions`` row handed to ``submit_intent`` as its ``session`` argument. It carries
#: ``feed_state = 'PENDING'`` for the whole run and is **never** updated, which makes every
#: admission below a fact about the row the simulator read out of the store inside its own attempt
#: rather than about the mapping its caller passed. A module that trusted the argument would refuse
#: every fill in this file.
_ARGUMENT_ROW: Mapping[str, Any] = {
    "id": SESSION,
    "user_id": USER,
    "feed_state": feed.FEED_STATE_PENDING,
}

#: The state a session is in before its first validated event: ``open_feed`` writes
#: ``market_data_source`` and leaves ``feed_state`` alone (it passes ``feed_state=None``), and the
#: seeded row is ``PENDING``. Written down here because it is the oracle's initial value.
INITIAL_FEED_STATE = feed.FEED_STATE_PENDING


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATOR - market_event_streams() × DISCONNECTION POINTS
# ══════════════════════════════════════════════════════════════════════════

#: One step of a generated run. ``None`` is a **disconnection point**: the subscription is read
#: with nothing queued, which is what ``FeedHandle._receive`` reads as the dropped subscription of
#: Requirement 14.5. Anything else is a candle to publish.
Step = Optional[Candle]


@st.composite
def streams_with_disconnections(draw: Any) -> List[Step]:
    """A generated stream with disconnection points inserted into it.

    ``market_event_streams()`` relabelled to the session's one traded symbol (see
    ``paper_market_streams``' module docstring for why), then interrupted. The forced outage at
    :data:`FORCED_OUTAGE_POSITION` is what makes the property non-vacuous: a run with no outage
    satisfies both conjuncts trivially, because "no fill while not ``HEALTHY``" has no step to
    range over and "after a reconnection" has no reconnection.

    Everything about the outages except that one position is drawn - how many more there are and
    where - and everything about the stream itself was already drawn by
    ``market_event_streams()``. The extra positions are bounded above by ``len(stream) - 1`` so
    every outage interrupts the stream rather than trailing it: a trailing outage would add a
    refusal case the forced one already covers and would spend the budget on it.
    """
    stream = draw(single_symbol_market_event_streams())
    extra = draw(
        st.lists(
            st.integers(min_value=1, max_value=len(stream) - 1),
            min_size=0,
            max_size=MAX_EXTRA_OUTAGES,
            unique=True,
        )
    )
    positions = {FORCED_OUTAGE_POSITION, *extra}

    steps: List[Step] = []
    for index, candle in enumerate(stream):
        if index in positions:
            steps.append(None)
        steps.append(candle)
    return steps


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE - THE POST-RECONNECTION VALIDATED-EVENT SET
# ══════════════════════════════════════════════════════════════════════════


class _Expectation:
    """What a correct session looks like at one step, computed from the stream alone."""

    __slots__ = (
        "index",
        "candle",
        "verdict",
        "feed_state",
        "post_reconnection",
        "priced_from",
        "outages_so_far",
    )

    def __init__(
        self,
        index: int,
        candle: Step,
        verdict: Optional[Verdict],
        feed_state: str,
        post_reconnection: frozenset,
        priced_from: Optional[Candle],
        outages_so_far: int,
    ) -> None:
        self.index = index
        self.candle = candle
        self.verdict = verdict
        self.feed_state = feed_state
        self.post_reconnection = post_reconnection
        self.priced_from = priced_from
        self.outages_so_far = outages_so_far

    @property
    def is_outage(self) -> bool:
        return self.candle is None

    @property
    def tradeable(self) -> bool:
        """Whether a correct session admits a fill at this step."""
        return self.feed_state in feed.TRADEABLE_FEED_STATES

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Expectation(step {self.index}, "
            f"{'OUTAGE' if self.is_outage else repr(self.candle)}, "
            f"state={self.feed_state}, priced_from={self.priced_from!r}, "
            f"post={len(self.post_reconnection)})"
        )


def _plan(steps: Sequence[Step]) -> List[_Expectation]:
    """The expected feed state and post-reconnection validated set at every step.

    Three rules, and each is a reading of Requirement 14.5 rather than of the module:

    * a disconnection point marks the session degraded and **empties** the post-reconnection set,
      because the reconnection happens on that same call (``next_validated_event`` marks degraded
      and makes one bounded attempt) and nothing the session validated before it is a
      post-reconnection event;
    * a candle the projection **accepted** makes the session healthy and joins the set. A candle it
      dropped - as a duplicate or as out of order - changes neither, which is the "first event that
      passes validation" half of the requirement;
    * the price a session would fill at is the last accepted candle's, whenever that was. It is
      carried across outages on purpose: that is the stale price, and offering it is how the
      refusal gets tested.

    Outages do not touch the dedupe cache or the per-symbol high-water mark, which is why the
    projection can be computed over the candles alone and then interleaved:
    ``FeedHandle.reconnect`` re-opens a subscription and does nothing else, asserted structurally
    by ``tests/test_paper_market_feed_events.test_the_reconnection_only_resubscribes``.
    """
    _processed, verdicts = project([step for step in steps if step is not None])
    remaining = iter(verdicts)

    state = INITIAL_FEED_STATE
    post: set = set()
    priced_from: Optional[Candle] = None
    outages = 0
    plan: List[_Expectation] = []

    for index, step in enumerate(steps):
        verdict: Optional[Verdict] = None
        if step is None:
            state = feed.FEED_STATE_DEGRADED
            post = set()
            outages += 1
        else:
            verdict = next(remaining)
            if verdict.accepted:
                state = feed.FEED_STATE_HEALTHY
                post.add(verdict.identity)
                priced_from = verdict.candle
        plan.append(
            _Expectation(
                index, step, verdict, state, frozenset(post), priced_from, outages
            )
        )
    return plan


# ══════════════════════════════════════════════════════════════════════════
# THE PRICE TRANSFORM, RECOMPUTED FROM THE FROZEN CONFIG
# ══════════════════════════════════════════════════════════════════════════
#
# ``paper_market_streams.adverse_slippage_price`` - written out there, shared with P-55, and
# deliberately **not** a call to ``paper_simulator.market_fill_price``. See that module's section
# header for the arithmetic and for why it is recomputed rather than borrowed.


def _adverse_market_price(close: Any, side: str, config: sim.SessionConfig) -> Decimal:
    """The price a market order on ``side`` fills at, given a validated event's ``close``."""
    return adverse_slippage_price(close, side, config)


# ══════════════════════════════════════════════════════════════════════════
# THE RUN
# ══════════════════════════════════════════════════════════════════════════


def _session() -> Tuple[Any, feed.FeedHandle, Any, str, sim.SessionConfig]:
    """One Paper_Session: a store, its isolated account, and an open subscription.

    The account is created through ``paper_repository.get_or_create_account``, so the premise is
    the premise production runs under. The store is the feed harness's ``_client()`` - one
    ``FakeSupabase`` carrying the ``paper_sessions`` row - which is what makes the ``feed_state``
    the feed writes the ``feed_state`` the simulator reads.
    """
    repo.reset_persistence_probe()
    client = _client()
    account = repo.get_or_create_account(
        client, USER, "USD", SESSION, initial_capital=CAPITAL
    )
    handle, redis, _ = _feed_on([], client=client, config=_feed_config(symbol=SYMBOL))
    # The premise's own statements are dropped, so "nothing was written" below reads what the
    # code under test issued rather than what the fixture did to set it up.
    client.statements.clear()
    client.ops.clear()
    return client, handle, redis, str(account["id"]), _session_config()


def _deliver(handle: feed.FeedHandle) -> Optional[feed.MarketEvent]:
    """One ``next_validated_event`` call on the process's one event loop.

    ``None`` is a drop - an invalid, duplicate, late or undelivered event - and never an
    exception. See "WHY ``_run_coroutine``" in the module docstring for why this is the simulator
    harness's runner rather than the feed harness's.
    """
    return _run_coroutine(feed.next_validated_event(handle))


def _submit(
    client: Any, account_id: str, config: sim.SessionConfig, event: feed.MarketEvent
) -> sim.SubmitOutcome:
    """Ask the real simulator to accept and fill one market order priced from ``event``.

    ``latest_event`` is the event the session last validated, which during an outage is the last
    close **before** the disconnection. The module derives the reference from it
    (``reference_price``) and the fill price from that (``market_fill_price``); nothing about the
    price is chosen here, which is what makes the recorded value a statement by the module.
    """
    return _run_coroutine(
        sim.submit_intent(
            client,
            _ARGUMENT_ROW,
            _intent(side=SIDE, order_type="market", quantity=ORDER_QUANTITY),
            config=config,
            account_id=account_id,
            latest_event=event,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# THE TWO CONJUNCTS, ASSERTED SEPARATELY
# ══════════════════════════════════════════════════════════════════════════


def _assert_the_persisted_state_is_the_oracles(
    client: Any, expectation: _Expectation, steps: Sequence[Step]
) -> None:
    """The premise of both conjuncts: the row the gate reads says what the oracle says.

    Read out of the store rather than off the handle. ``FeedHandle`` lives in one worker's memory
    and the simulator may be in another process (``admit_execution``'s own docstring), so the
    persisted value is the only one that decides - and a module whose in-process state moved while
    the row did not would leave a window in which a dead feed still read ``HEALTHY``.
    """
    stored = client.sessions[0]["feed_state"]
    assert stored == expectation.feed_state, (
        f"P-56 (Requirement 14.5): at {expectation!r} the persisted feed_state is {stored!r} and "
        f"the projection of the stream says {expectation.feed_state!r}. Steps: {list(steps)}"
    )


def _assert_no_fill_while_not_healthy(
    client: Any,
    account_id: str,
    config: sim.SessionConfig,
    event: feed.MarketEvent,
    expectation: _Expectation,
    steps: Sequence[Step],
) -> None:
    """Conjunct 1: the fill is refused, and nothing anywhere moved.

    Asserted three ways, because they are three different claims:

    * ``FeedNotHealthy`` is raised, carrying the state that was read - so the refusal is the gate's
      and not an incidental failure;
    * the whole ledger is byte-identical across all seven account-scoped tables;
    * **no statement was issued** against any of them, so "unchanged" is a fact about what was
      never written rather than about what was written and undone. This transport has no
      ``ROLLBACK``.
    """
    before = _snapshot(client)
    mark = len(client.statements)

    with pytest.raises(FeedNotHealthy) as caught:
        _submit(client, account_id, config, event)

    error = caught.value
    assert error.feed_state == expectation.feed_state, (
        f"P-56: the refusal at {expectation!r} names feed_state {error.feed_state!r}, not the "
        f"{expectation.feed_state!r} the row holds. Steps: {list(steps)}"
    )
    assert error.session_id == SESSION
    assert _snapshot(client) == before, (
        f"P-56 (Requirements 14.5, 18.15): a fill was applied at {expectation!r}, where the feed "
        f"state is {expectation.feed_state!r} and the price on offer was "
        f"{expectation.priced_from!r} - a pre-disconnection close. Steps: {list(steps)}"
    )
    assert _wrote_since(client, mark) == [], (
        f"P-56: the refusal at {expectation!r} issued writes "
        f"{_wrote_since(client, mark)}; a refused fill must reach no table at all, because this "
        f"transport cannot roll one back. Steps: {list(steps)}"
    )


def _assert_the_fill_is_priced_from_a_post_reconnection_event(
    client: Any,
    outcome: sim.SubmitOutcome,
    event: feed.MarketEvent,
    expectation: _Expectation,
    config: sim.SessionConfig,
    steps: Sequence[Step],
) -> None:
    """Conjunct 2: the applied fill's price traces to an event validated after the reconnection.

    Four assertions, narrowest first: the fill happened at all; the gate minted a token and it
    says ``HEALTHY``; the event it was priced from is in the post-reconnection set the oracle
    emptied at the outage; and the recorded price is this file's own transform of that event's
    close. The last one is what turns "priced from" into arithmetic - see
    :func:`_adverse_market_price`.
    """
    assert outcome.fill is not None and outcome.fill.applied, (
        f"P-56: no fill was applied at {expectation!r}, where the persisted feed state is "
        f"{expectation.feed_state!r}. Steps: {list(steps)}"
    )
    admission = outcome.fill.admission
    assert admission is not None and admission.feed_state == feed.FEED_STATE_HEALTHY, (
        f"P-56: the fill at {expectation!r} carries no ExecutionAdmission granted on HEALTHY, so "
        f"the gate was not the thing that let it through. Steps: {list(steps)}"
    )

    identity = event.source_event_id
    assert identity in expectation.post_reconnection, (
        "P-56 (Requirements 14.5, 18.15): the fill at "
        f"{expectation!r} was priced from event {identity[:16]} "
        f"({expectation.priced_from!r}), which is not one of the "
        f"{len(expectation.post_reconnection)} event(s) this session validated after its most "
        f"recent reconnection. A price the session held from before the disconnection was treated "
        f"as current. Steps: {list(steps)}"
    )
    assert expectation.priced_from is not None
    assert identity == identity_of(expectation.priced_from), (
        f"P-56: the feed priced the fill from {identity[:16]} and the projection expected "
        f"{identity_of(expectation.priced_from)[:16]} ({expectation.priced_from!r}). Steps: "
        f"{list(steps)}"
    )

    recorded = Decimal(str(outcome.fill.fill["price"]))
    expected = _adverse_market_price(expectation.priced_from.close, SIDE, config)
    assert recorded == expected, (
        f"P-56 (Requirement 14.9): the fill at {expectation!r} recorded price {recorded}, and the "
        f"adverse-slippage transform of {expectation.priced_from!r}'s close "
        f"{expectation.priced_from.close} under the frozen config is {expected}. Exact decimal, "
        f"no tolerance. Steps: {list(steps)}"
    )

    # And the price is one the session RECORDED, not merely one it returned: the event it was
    # priced from is in this session's own paper_market_events log.
    recorded_identities = {
        row["source_event_id"] for row in market_events_for(client, SESSION)
    }
    assert identity in recorded_identities, (
        f"P-56 (Requirement 15.5): the fill at {expectation!r} was priced from an event that is "
        f"not in this session's paper_market_events log, so a replay could not reproduce it. "
        f"Steps: {list(steps)}"
    )

    assert outcome.order["order_state"] == PaperOrderState.FILLED.value


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES THE RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════

#: Every floor that reads 100 is the example count: :func:`streams_with_disconnections` and the
#: forced spine put that case into EVERY example, so anything below 100 means the forcing stopped
#: working rather than that the case is rare. The two lower floors are the buckets the generator
#: leaves to chance - a drawn second outage and a drawn tail - so they are distribution claims. A
#: shortfall is fixed by FORCING the case in the generator, never by lowering the number here.
P56_FLOORS: Mapping[str, int] = {
    "examples": 100,
    "steps": 900,
    "candles": 800,
    "outages": 100,
    "outage_mid_stream": 100,
    "reconnections": 100,
    "fill_attempts": 900,
    "fills_applied": 500,
    "fills_refused": 300,
    "refused_while_degraded": 100,
    "refused_at_a_pre_disconnection_price": 100,
    "refused_after_a_dropped_arrival_during_the_outage": 100,
    "refused_after_a_respelled_republication": 100,
    "filled_after_a_reconnection": 100,
    "filled_from_an_event_validated_at_an_earlier_step": 100,
    "dropped_duplicate": 100,
    "dropped_out_of_order": 100,
    "non_zero_slippage_on_an_applied_fill": 100,
    "more_than_one_outage": 25,
    "tail_beyond_the_spine": 25,
}

#: The buckets also emitted as a Hypothesis ``event``, so the observed distribution is printed by
#: ``--hypothesis-show-statistics`` rather than only asserted.
P56_LABELS: Mapping[str, str] = {
    "outage_mid_stream": "the subscription dropped mid-stream",
    "reconnections": "a validated event ended an outage",
    "refused_while_degraded": "a fill was refused while the feed was DEGRADED",
    "refused_at_a_pre_disconnection_price": (
        "a fill offered at a pre-disconnection price was refused"
    ),
    "refused_after_a_dropped_arrival_during_the_outage": (
        "an arrival that was dropped did not end the outage"
    ),
    "refused_after_a_respelled_republication": (
        "a respelled republication did not end the outage"
    ),
    "filled_after_a_reconnection": (
        "a fill was applied after a reconnection, from a post-reconnection event"
    ),
    "filled_from_an_event_validated_at_an_earlier_step": (
        "a fill was priced from an event validated at an earlier step"
    ),
    "non_zero_slippage_on_an_applied_fill": "an applied fill recorded non-zero slippage",
    "more_than_one_outage": "the run carried more than one outage",
    "tail_beyond_the_spine": "the stream carried candles beyond the forced spine",
}

#: How many candles the shared generator forces into every example, plus the one forced outage.
SPINE_STEPS = 9


# ══════════════════════════════════════════════════════════════════════════
# P-56
# ══════════════════════════════════════════════════════════════════════════


def _run_one(steps: Sequence[Step], recorder: Recorder) -> None:
    """Drive one generated run and assert both conjuncts at every step."""
    plan = _plan(steps)
    client, handle, redis, account_id, config = _session()

    recorder.mark("steps", len(steps))
    recorder.mark("candles", sum(1 for step in steps if step is not None))
    if len(steps) - SPINE_STEPS > 0:
        recorder.mark("tail_beyond_the_spine")
    if sum(1 for step in steps if step is None) > 1:
        recorder.mark("more_than_one_outage")

    latest: Optional[feed.MarketEvent] = None
    was_degraded = False
    dropped_during_this_outage = False
    respelled_during_this_outage = False

    for expectation in plan:
        # ── one step of the feed ──
        if expectation.is_outage:
            result = _deliver(handle)  # nothing queued: the dropped subscription of 14.5
            recorder.mark("outages")
            if 0 < expectation.index < len(plan) - 1:
                recorder.mark("outage_mid_stream")
            assert result is None, (
                f"P-56: reading an empty subscription at step {expectation.index} returned an "
                f"event; the outage was not an outage. Steps: {list(steps)}"
            )
            was_degraded = True
            dropped_during_this_outage = False
            respelled_during_this_outage = False
        else:
            redis.frames.append(wire_frame(expectation.candle))
            result = _deliver(handle)
            assert expectation.verdict is not None
            assert (result is not None) == expectation.verdict.accepted, (
                f"P-56: the feed {'accepted' if result is not None else 'dropped'} "
                f"{expectation.candle!r} at step {expectation.index} and the projection "
                f"{'accepted' if expectation.verdict.accepted else 'dropped'} it. Steps: "
                f"{list(steps)}"
            )
            if result is not None:
                latest = result
                if was_degraded:
                    recorder.mark("reconnections")
                    was_degraded = False
            else:
                if expectation.verdict.outcome == DROPPED_DUPLICATE:
                    recorder.mark("dropped_duplicate")
                else:
                    recorder.mark("dropped_out_of_order")
                if not expectation.tradeable:
                    dropped_during_this_outage = True
                    if expectation.verdict.respelled:
                        respelled_during_this_outage = True

        # The persisted state is the premise of both conjuncts, so it is checked at every step -
        # including the steps at which no fill is attempted.
        _assert_the_persisted_state_is_the_oracles(client, expectation, steps)

        if latest is None:
            # No validated event yet, so there is no price to offer and nothing to refuse. The
            # PENDING refusal is asserted exhaustively by the 24.4 unit cases.
            continue

        # ── one step of the simulator, at the latest price the session holds ──
        recorder.mark("fill_attempts")
        if expectation.tradeable:
            outcome = _submit(client, account_id, config, latest)
            recorder.mark("fills_applied")
            _assert_the_fill_is_priced_from_a_post_reconnection_event(
                client, outcome, latest, expectation, config, steps
            )
            if expectation.outages_so_far:
                recorder.mark("filled_after_a_reconnection")
            if expectation.verdict is None or not expectation.verdict.accepted:
                recorder.mark("filled_from_an_event_validated_at_an_earlier_step")
            if Decimal(str(outcome.fill.fill["slippage_minor"])) != 0:
                recorder.mark("non_zero_slippage_on_an_applied_fill")
        else:
            _assert_no_fill_while_not_healthy(
                client, account_id, config, latest, expectation, steps
            )
            recorder.mark("fills_refused")
            if expectation.feed_state == feed.FEED_STATE_DEGRADED:
                recorder.mark("refused_while_degraded")
            if expectation.priced_from is not None and expectation.outages_so_far:
                recorder.mark("refused_at_a_pre_disconnection_price")
            if dropped_during_this_outage:
                recorder.mark("refused_after_a_dropped_arrival_during_the_outage")
            if respelled_during_this_outage:
                recorder.mark("refused_after_a_respelled_republication")

    # One last whole-run reading of conjunct 1: every fill row in the store carries a price that is
    # the transform of a close this session RECORDED, so a fill written by some path the step loop
    # did not observe is caught too.
    _assert_every_recorded_fill_traces_to_a_recorded_event(client, config, steps)


def _assert_every_recorded_fill_traces_to_a_recorded_event(
    client: Any, config: sim.SessionConfig, steps: Sequence[Step]
) -> None:
    """Every ``paper_fills.price`` in the store is the transform of a recorded event's close.

    A whole-run reading of the same conjunct the per-step assertions make, and it catches a fill
    written by some path the step loop did not observe: the set of admissible prices is computed
    from ``paper_market_events`` alone, so a price from anywhere else is not in it. P-55 makes this
    the property in its own right, over every price the ledger holds; here it is the backstop that
    no *extra* fill appeared.
    """
    admissible = {
        _adverse_market_price(recorded_close(row), SIDE, config)
        for row in market_events_for(client, SESSION)
    }
    for row in client.fills:
        price = Decimal(str(row["price"]))
        assert price in admissible, (
            f"P-56: paper_fills carries price {price}, which is not the adverse-slippage "
            f"transform of any close in this session's paper_market_events "
            f"({sorted(admissible)}). Steps: {list(steps)}"
        )


def test_p56_no_fill_at_a_pre_disconnection_price(request: Any, monkeypatch: pytest.MonkeyPatch,
                                                  counters: Any,
                                                  no_sleeping: List[Any]) -> None:
    """No fill while the feed is not ``HEALTHY``, and no post-reconnection fill at an old price.

    For all generated streams and all disconnection points within them: no fill is applied while
    ``paper_sessions.feed_state != 'HEALTHY'`` - asserted by the refusal, by the whole ledger being
    unchanged and by no statement having been issued - and every fill applied after a reconnection
    is priced from an event whose identity was validated after that reconnection, with the recorded
    ``paper_fills.price`` equal to this file's own adverse-slippage transform of that event's close.

    The oracle is the post-reconnection validated-event set computed by :func:`_plan` from the
    generated stream, over ``paper_market_streams.project``'s distinct-and-sorted reading and
    ``identity_of``'s independently recomputed digest. Exact ``Decimal`` throughout, no tolerance.

    **Validates: Requirements 14.5, 18.15**
    """
    # The one clock the feed reads, pinned - so paper_market_events.latency_ms is the exact
    # difference between two known instants rather than a figure that depends on when the suite
    # ran, and NUMERIC(10,3) cannot overflow on a candle minutes behind a real clock.
    monkeypatch.setattr(feed, "_utc_now", lambda: PROCESSED_AT)

    recorder = Recorder("P-56", P56_FLOORS, P56_LABELS)

    @PROPERTY_SETTINGS
    @given(steps=streams_with_disconnections())
    def check(steps: List[Step]) -> None:
        recorder.start()
        recorder.mark("examples")
        _run_one(steps, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE'S OWN MATERIAL, GUARDED
# ══════════════════════════════════════════════════════════════════════════


@settings(max_examples=25, deadline=None)
@given(steps=streams_with_disconnections())
def test_the_generator_forces_an_outage_between_two_accepted_candles(
    steps: List[Step],
) -> None:
    """The forced case, checked directly rather than only through the census.

    :data:`FORCED_OUTAGE_POSITION` is only meaningful if the candle before it is accepted and some
    candle after it is too. Both are properties of the shared spine, and a change to that spine
    would empty four census buckets at once; this states the dependency where it can be read, on
    the generator alone and without driving a feed or a simulator.
    """
    plan = _plan(steps)
    outage = next(step for step in plan if step.is_outage)

    assert outage.index == 1, "the forced outage must follow the spine's first candle"
    assert plan[0].feed_state == feed.FEED_STATE_HEALTHY, (
        "the spine's first candle must be accepted, or the forced outage does not interrupt a "
        "healthy session"
    )
    assert outage.feed_state == feed.FEED_STATE_DEGRADED
    assert outage.post_reconnection == frozenset(), (
        "the post-reconnection set must be emptied at the outage, or conjunct 2 has nothing to say"
    )
    assert outage.priced_from is plan[0].verdict.candle, (
        "the price on offer during the outage must be the pre-disconnection close"
    )

    after = [step for step in plan[outage.index + 1:] if step.tradeable]
    assert after, "some candle after the forced outage must be accepted"
    assert after[0].post_reconnection, (
        "the first tradeable step after the outage must carry a non-empty post-reconnection set"
    )


def test_the_price_transform_is_adverse_in_both_directions_and_exact() -> None:
    """:func:`_adverse_market_price` slips a buy up and a sell down, and rounds once.

    The oracle's independence rests on this being a correct statement about the session's recorded
    ``slippage_rate``, made here. If it slipped favourably, P-56's price assertion would expect a
    better price than the market published and would fail against a correct simulator.
    """
    config = _session_config()
    assert config.slippage_rate > 0

    close = Decimal("60000.5")
    buy = _adverse_market_price(close, "buy", config)
    sell = _adverse_market_price(close, "sell", config)

    assert buy > close, "a buy must slip up"
    assert sell < close, "a sell must slip down"
    for price in (buy, sell):
        assert isinstance(price, Decimal)
        assert -int(price.as_tuple().exponent) <= config.price_precision

    # And it is the module's convention, checked once here rather than borrowed everywhere: the
    # two functions must agree on a value neither of them chose.
    assert buy == sim.market_fill_price(close, "buy", config)
    assert sell == sim.market_fill_price(close, "sell", config)


def test_the_argument_row_is_never_the_one_that_decides() -> None:
    """:data:`_ARGUMENT_ROW` says ``PENDING`` forever, and fills still happen.

    Which is the point: every admission in this module is a fact about the row the simulator read
    inside its own attempt. This guards the premise - if ``submit_intent`` ever trusted its
    ``session`` argument, every applied fill above would become a refusal and the failure would
    look like a feed bug.
    """
    assert _ARGUMENT_ROW["feed_state"] not in feed.TRADEABLE_FEED_STATES
    with pytest.raises(FeedNotHealthy):
        feed.admit_execution(_ARGUMENT_ROW)
