"""
tests/property/test_paper_idempotence.py - P-21 and P-22.

Spec: marketplace-subscriptions-paper-trading tasks 25.11 and 25.12. ``design.md`` ->
"Property-to-test mapping". Requirements 16.8, 16.9, 16.11, 16.15.

THE TWO PROPERTIES LIVING HERE
------------------------------
``test_p21_duplicate_fill_event_changes_nothing``      (Requirements 16.9, 16.11)
``test_p22_duplicate_order_intent_yields_one_order``   (Requirements 16.8, 16.11, 16.15)

Exactly one ``test_p{n}_`` function per property at module scope, and nothing nested carries that
prefix: ``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and counts a nested
function too, so a helper named ``test_p21_...`` inside a body would claim P-21 twice.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
The real ``paper_simulator.apply_fill`` and ``paper_simulator.submit_intent`` are driven against
``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this repository
has, which enforces the eight unique indexes ``009_paper_trading.sql`` declares, including the two
this file is about: ``uq_paper_fill_event`` on ``(order_id, fill_event_id)`` and
``uq_paper_order_idem`` on ``(session_id, idempotency_key)``. There is no second paper double and
no mock of the repository.

The harness is ``tests/test_paper_order_lifecycle_writes.py``'s, imported rather than rebuilt -
``_run_coroutine``, ``_Sleeps``, ``_config``, ``_seed``, ``_mark``, ``_wrote_since``, ``_event``,
``_intent``, ``_submit``, ``_snapshot``, ``_accepted_market_order``, ``_fill`` and the constants -
and the census is ``tests/property/paper_census.py``'s. ``_accepted_market_order`` grew two
keyword arguments (``side`` and ``fingerprint``, both defaulting to what it already used) because
P-21 has to build a **sell** order to close a position; a second copy of those two repository calls
here would be a second account of what "an accepted order" is. ``_run_coroutine`` drives every
coroutine on a private loop; ``asyncio.run`` is never used, because it closes the loop it created
and breaks any later test in the session that expected one.

THE ORACLES, AND WHY EACH IS INDEPENDENT OF THE CODE UNDER TEST
--------------------------------------------------------------
* **P-21 - the single application.** For each generated fill event the property stages a fresh
  store, applies the event **once**, and keeps that store's whole seven-table snapshot as the
  oracle. Every other run of the same example stages the same fresh store and applies the same
  event ``n`` times, and the two snapshots are compared. Nothing about the expectation is computed
  by asking the module what it thinks it did: the answer is what one application of the same input
  to the same premise produced. ``FILL_DUPLICATE`` is never used to *derive* the expectation - the
  expected outcome of a repeat is read off Requirements 16.3 and 16.9 instead (see
  :func:`_expected_repeat_outcome`), so an implementation that reported ``DUPLICATE`` and then
  wrote anyway would fail on the snapshot, and one that reported ``APPLIED`` twice would fail on
  the outcome.
* **P-22 - exactly one row.** The oracle is a count: ``len(paper_orders) == 1`` for the session,
  the single row carrying the generated key. Every response's ``order["id"]`` is compared against
  the id of the row that count identifies, and the whole snapshot is compared against the one taken
  immediately after the first submission. ``SubmitOutcome.duplicate`` is asserted as an additional
  claim, never used as the oracle.
* **Both, for Requirement 16.11.** The **durable** arbiter is exercised directly: a second
  ``paper_repository.insert_fill`` with the same ``(order_id, fill_event_id)`` and a second
  ``paper_repository.insert_order`` with the same ``(session_id, idempotency_key)`` are issued to
  the Persistence_Layer and must be refused. Requirement 16.11 is a claim about the database, so
  asserting it only through ``apply_fill``'s and ``submit_intent``'s in-code probes would leave the
  index itself untested - and the probe is a read, which under a genuine race can miss.

EVERY COMPARISON IS EXACT ``Decimal``
-------------------------------------
No ``pytest.approx``, no ``round()``, no tolerance. Balances, filled quantities and position sizes
are compared with ``==`` on ``Decimal``; fees and slippage as the exact ``int`` minor units the
columns store; and the seven-table snapshots by structural equality, which for these stores is
exact string equality on every persisted decimal. A tolerance would turn "changes nothing" into a
statement about nothing.

NON-VACUITY: A FORCED SPINE, A CENSUS, AND FLOORS THAT ARE NOT NEGOTIABLE
------------------------------------------------------------------------
Both properties are trivially satisfiable by a run that never repeats anything: P-21 holds for free
at ``n = 1`` and P-22 holds for free if the second submission is never issued. So each example
FORCES its hard cases and each property carries a :class:`~tests.property.paper_census.Recorder`
with an explicit floor per bucket and a Hypothesis ``event`` label, following
``tests/property/test_paper_persistence_roundtrip.py`` and
``tests/property/test_market_event_dedupe.py``. What is counted is what the run **observed**, not
what the spine intended. A shortfall is fixed by FORCING the case in the generator, never by
lowering a floor.

* **P-21** forces four fill scenarios into every example - a further partial fill on an order that
  already carries one, a fill that completes a fresh order, a fill that partially closes an open
  position, and a fill that closes one to exactly zero - and applies each of them ``1``, ``2``,
  three-or-four and five-or-six times. So ``n`` genuinely exceeds one on every example and reaches
  at least five; both of Requirement 16.9's "changes nothing" branches are entered (the duplicate
  guard for the two orders left ``PARTIALLY_FILLED``, and Requirement 16.3's terminal guard for the
  two left ``FILLED``); a non-zero fee, a zero fee, a non-zero recorded slippage and a zero one are
  all observed; and realized PnL moves in both directions with a ``paper_trades`` row written.
* **P-22** forces three submission flavours into every example - an accepted resting limit order, an
  accepted market order that fills on acceptance, and a statically rejected order - and against
  each of them at least four further submissions carrying the same key: an identical one, a
  **textually different but fingerprint-identical** one, and a materially different one that must
  answer 409. So Requirement 16.8's duplicate path, Requirement 16.15's conflict, and the
  canonicalisation that keeps ``"1.0"`` and ``"1"`` the same order are each entered on every
  example, against a recorded order in each of the three states a first submission can leave.

GAPS LEFT OPEN, STATED RATHER THAN ASSERTED AROUND
--------------------------------------------------
1. **P-21's repeats are sequential, not concurrent.** ``apply_fill``'s duplicate guard is a READ,
   and a genuine race can slip a second insert past it - which is why the module also catches
   ``uq_paper_fill_event``'s refusal from the INSERT and returns ``FILL_DUPLICATE`` from there.
   That second path is covered by the direct-insert probe of Requirement 16.11 above and by
   ``tests/test_paper_order_lifecycle_writes.py``; the *interleaved* version of it is P-23's
   subject, in ``tests/property/test_paper_confluence.py``.
2. **P-22 does not cross sessions.** Requirement 16.11 scopes the key to one Paper_Session, and
   task 25.12 says "within one Paper_Session", so two sessions reusing one key is not asserted
   here. ``tests/test_paper_repository.py`` covers that scoping, including the default account's
   companion index ``uq_paper_order_idem_default``.
3. **A fill applied through ``check_resting_orders`` is not repeated here.** Its
   ``resting_fill_event_id`` is derived from the market event, so repeating the event is the
   *market-data* dedupe of Requirement 14.7 - P-54's subject in
   ``tests/property/test_market_event_dedupe.py`` - rather than the fill dedupe of Requirement
   16.9. P-21 repeats the fill event itself, at ``apply_fill``, which is the single write path
   every fill in the system goes through.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import PAPER_IDEMPOTENCY_CONFLICT
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The shared harness. A second one would be a second account of what a session is. ──────
from tests.test_paper_order_lifecycle_writes import (
    NOW,
    SESSION,
    SYMBOL,
    USER,
    _accepted_market_order,
    _config,
    _event,
    _fill,
    _intent,
    _mark,
    _run_coroutine,
    _seed,
    _Sleeps,
    _snapshot,
    _submit,
    _wrote_since,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example of P-21 drives four staged scenarios times four repetition counts, and one
#: example of P-22 drives three flavours times at least five submissions, so ``too_slow`` is
#: suppressed rather than the example count being cut.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: The capital every driver below starts from. Large enough that no forced case is ever an
#: accidental ``INSUFFICIENT_FUNDS``: neither property is about the funds check.
RICH = Decimal("1000000")

#: Requirement 16.3's three terminal states, written down here rather than imported, because
#: :func:`_expected_repeat_outcome` reads the expected outcome of a repeated fill off the
#: requirement text and not off ``paper_order_state.is_terminal``.
REQUIREMENT_16_3_TERMINAL: Tuple[str, ...] = ("FILLED", "CANCELLED", "REJECTED")


# ══════════════════════════════════════════════════════════════════════════
# SHARED READERS OVER THE PERSISTENCE_LAYER DOUBLE
# ══════════════════════════════════════════════════════════════════════════


def _order_by_id(supabase: Any, order_id: Any) -> Dict[str, Any]:
    """One stored ``paper_orders`` row, by id. Absence is a failure, never an empty dict."""
    for row in supabase.orders:
        if str(row["id"]) == str(order_id):
            return dict(row)
    raise AssertionError(f"no paper_orders row {order_id!r} is stored; rows: {supabase.orders}")


def _balances(supabase: Any) -> Dict[str, Decimal]:
    """The four figures Requirement 18.3's identity is written over, as exact ``Decimal``."""
    account = supabase.accounts[0]
    return {
        "available_balance": Decimal(str(account["available_balance"])),
        "locked_balance": Decimal(str(account["locked_balance"])),
        "realized_pnl": Decimal(str(account["realized_pnl"])),
        "total_equity": Decimal(str(account["total_equity"])),
    }


def _positions(supabase: Any) -> List[Tuple[Any, ...]]:
    """Every ``paper_positions`` row as a comparable tuple of exact values, in a stable order."""
    return sorted(
        (
            str(row["symbol"]),
            str(row["side"]),
            Decimal(str(row["size"])),
            Decimal(str(row["entry_price"])),
            row["closed_at"] is not None,
        )
        for row in supabase.positions
    )


def _fills_of(supabase: Any, order_id: Any) -> List[Dict[str, Any]]:
    """One order's ``paper_fills`` rows, as plain dicts, in insertion order."""
    return [dict(row) for row in supabase.fills if str(row["order_id"]) == str(order_id)]


def _fill_sum_from_rows(supabase: Any, order_id: Any) -> Decimal:
    """The sum of ``paper_fills.quantity`` for one order, as exact ``Decimal``.

    Recomputed from the fill ROWS. ``paper_orders.filled_quantity`` is deliberately not read here -
    it is the value the property then compares against this one, so a drift between the rows and
    the column is a failure rather than something the comparison inherits.
    """
    total = Decimal("0")
    for row in supabase.fills:
        if str(row["order_id"]) == str(order_id):
            total += Decimal(str(row["quantity"]))
    return total


def _observable(supabase: Any, order_id: Any) -> Dict[str, Any]:
    """Everything P-21 names, for one order and its account, as comparable exact values.

    The four the property names - the order state, the filled quantity, the position and the
    balance - are read out separately so a failure names the claim, and the counts of the two
    append-only tables travel alongside because Requirement 18.13's "no additional equity snapshot
    for a repeated event identifier" is a separate claim from the balance one.
    """
    row = _order_by_id(supabase, order_id)
    return {
        "order_state": str(row["order_state"]),
        "filled_quantity": Decimal(str(row["filled_quantity"])),
        "fill_sum_from_rows": _fill_sum_from_rows(supabase, order_id),
        "order_fee_minor": int(row.get("fee_minor") or 0),
        "order_slippage_minor": int(row.get("slippage_minor") or 0),
        "fill_count": len(_fills_of(supabase, order_id)),
        "balances": _balances(supabase),
        "positions": _positions(supabase),
        "balance_event_count": len(supabase.balance_events),
        "equity_snapshot_count": len(supabase.equity_snapshots),
        "trade_count": len(supabase.trades),
    }


# ══════════════════════════════════════════════════════════════════════════
# P-21 (task 25.11) - A DUPLICATE FILL EVENT CHANGES NOTHING
# ══════════════════════════════════════════════════════════════════════════

#: The four staged scenarios, forced into every example. Each one is a different answer to "what
#: was the order and the position doing when the repeated fill arrived", and between them they
#: enter both of the branches by which a repeat changes nothing:
#:
#: ``partial_from_partial``  an order that already carries a fill, left PARTIALLY_FILLED
#:                           -> the repeat meets Requirement 16.9's DUPLICATE guard
#: ``full_open``             a fresh order the fill completes, left FILLED
#:                           -> the repeat meets Requirement 16.3's TERMINAL guard
#: ``partial_close``         an open LONG position the fill partially closes, order PARTIALLY_FILLED
#:                           -> DUPLICATE guard, with realized PnL already moved
#: ``full_close``            an open LONG position the fill closes to exactly zero, order FILLED
#:                           -> TERMINAL guard, with a ``paper_trades`` row already written
FILL_SCENARIOS: Tuple[str, ...] = (
    "partial_from_partial",
    "full_open",
    "partial_close",
    "full_close",
)

#: Which scenarios run under a session that charges a fee and whose fill price differs from the
#: recorded reference. Split so that a non-zero fee, a zero fee, a non-zero recorded slippage and a
#: zero one are ALL observed on every example: "the filled quantity and the recorded fees are
#: unchanged" is a comparison of two zeros unless something charged something.
PRICED_SCENARIOS: Tuple[str, ...] = ("partial_from_partial", "partial_close")

#: Order and fill quantities. The symbol's quantity precision is 8 decimals, and every product
#: below (``3 x unit``, ``2 x unit``) is exact in decimal at that scale.
UNITS: Tuple[str, ...] = ("0.25", "1", "2")

#: Entry prices. Two decimals at most - ``price_precision`` is 2 for this market.
ENTRY_PRICES: Tuple[str, ...] = ("50", "100", "96.25", "1000")

#: How far ABOVE its entry a partially-closing fill prices, so realized PnL is strictly positive.
UP_DELTAS: Tuple[str, ...] = ("0.25", "5", "50")

#: How far BELOW its entry a fully-closing fill prices, so realized PnL is strictly negative.
#: Bounded by 25 while :data:`ENTRY_PRICES` floors at 50, so the exit price stays above zero.
DOWN_DELTAS: Tuple[str, ...] = ("0.25", "5", "25")

#: How far the recorded reference price sits from the fill price, for the priced scenarios. Never
#: zero, because ``slippage_amount`` is ``|price - reference| x quantity`` and a zero offset would
#: record zero slippage - which is the *other* bucket's job.
REFERENCE_OFFSETS: Tuple[str, ...] = ("0.50", "2", "10")

#: Fee rates for the priced scenarios. Never zero, for the same reason.
NON_ZERO_FEE_RATES: Tuple[str, ...] = ("0.001", "0.0025", "0.01")

#: The ``fill_event_id`` the property repeats. One literal, because the identity is the whole
#: subject: ``uq_paper_fill_event`` de-duplicates on ``(order_id, fill_event_id)``, and a repeated
#: application that carried a fresh identifier would be a second fill rather than a duplicate.
TARGET_FILL_EVENT_ID = "fill-repeated"

#: The identifier of the fill that STAGES ``partial_from_partial``. Distinct from
#: :data:`TARGET_FILL_EVENT_ID`, so the order carries a genuine prior fill that the repeat must not
#: disturb rather than a prior copy of the repeat.
SEED_FILL_EVENT_ID = "fill-prior"


class _FillCase:
    """One generated fill event, and the repetition counts it is applied at."""

    __slots__ = (
        "unit",
        "entry_price",
        "up_delta",
        "down_delta",
        "reference_offset",
        "fee_rate",
        "repetitions",
    )

    def __init__(
        self,
        unit: str,
        entry_price: str,
        up_delta: str,
        down_delta: str,
        reference_offset: str,
        fee_rate: str,
        repetitions: Tuple[int, ...],
    ) -> None:
        self.unit = unit
        self.entry_price = entry_price
        self.up_delta = up_delta
        self.down_delta = down_delta
        self.reference_offset = reference_offset
        self.fee_rate = fee_rate
        self.repetitions = repetitions

    def exit_price(self, scenario: str) -> str:
        """The price the closing scenarios fill at: above entry to close part, below to close all.

        Forced in opposite directions so realized PnL is observed moving both ways on every
        example. ``_realized_on_close`` is fee-free, so both signs are strict.
        """
        entry = Decimal(self.entry_price)
        if scenario == "partial_close":
            return str(entry + Decimal(self.up_delta))
        if scenario == "full_close":
            return str(entry - Decimal(self.down_delta))
        return self.entry_price

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_FillCase(unit={self.unit!r}, entry={self.entry_price!r}, "
            f"up={self.up_delta!r}, down={self.down_delta!r}, "
            f"ref_offset={self.reference_offset!r}, fee={self.fee_rate!r}, "
            f"repetitions={self.repetitions})"
        )


@st.composite
def fill_events_and_repetition_counts(draw: Any) -> _FillCase:
    """A fill event, and the repetition counts ``n`` it will be applied at.

    ``repetitions`` is ``(1, 2, three-or-four, five-or-six)``. The first two are FORCED because a
    run in which ``n`` was drawn freely can spend an example entirely at ``n = 1``, where P-21 is a
    tautology; the last two are drawn, so "``n`` reached five" is a fact about every example rather
    than a coin toss, and *which* value above two it reached is still generated.

    ``n = 1`` is kept even though it compares the oracle against a second run of the oracle's own
    input: that run is a determinism check on the whole staging plus one application, which is
    Requirement 15.4's replay claim in miniature, and dropping it would make the property's
    statement narrower than "for all n >= 1".

    Every other value the four scenarios use is drawn, so forcing the *shape* of each case does not
    fix its *numbers*.
    """
    return _FillCase(
        unit=draw(st.sampled_from(UNITS)),
        entry_price=draw(st.sampled_from(ENTRY_PRICES)),
        up_delta=draw(st.sampled_from(UP_DELTAS)),
        down_delta=draw(st.sampled_from(DOWN_DELTAS)),
        reference_offset=draw(st.sampled_from(REFERENCE_OFFSETS)),
        fee_rate=draw(st.sampled_from(NON_ZERO_FEE_RATES)),
        repetitions=(
            1,
            2,
            draw(st.integers(min_value=3, max_value=4)),
            draw(st.integers(min_value=5, max_value=6)),
        ),
    )


class _Staged:
    """A fresh store, staged up to the instant before the repeated fill is applied."""

    __slots__ = ("supabase", "session", "account_id", "config", "order", "fill_kwargs")

    def __init__(
        self,
        supabase: Any,
        session: Dict[str, Any],
        account_id: str,
        config: sim.SessionConfig,
        order: Mapping[str, Any],
        fill_kwargs: Dict[str, Any],
    ) -> None:
        self.supabase = supabase
        self.session = session
        self.account_id = account_id
        self.config = config
        self.order = order
        self.fill_kwargs = fill_kwargs


def _stage(case: _FillCase, scenario: str) -> _Staged:
    """Build one scenario's premise on a fresh store, through the real module and repository.

    The premise is deterministic in ``case``, which is what lets the single-application run serve
    as the oracle for the ``n``-application runs: the two differ in nothing but how many times the
    one fill event is offered.
    """
    unit = Decimal(case.unit)
    priced = scenario in PRICED_SCENARIOS
    config = _config(
        fee_rate=Decimal(case.fee_rate) if priced else Decimal("0"),
        slippage_rate=Decimal("0"),
    )
    supabase, session, account_id = _seed(capital=RICH)
    price = Decimal(case.exit_price(scenario))
    reference = price + Decimal(case.reference_offset) if priced else price

    def fill_kwargs(quantity: Decimal, at: Decimal) -> Dict[str, Any]:
        return {
            "config": config,
            "quantity": str(quantity),
            "price": str(at),
            "fill_event_id": TARGET_FILL_EVENT_ID,
            "reference": str(at + Decimal(case.reference_offset)) if priced else str(at),
        }

    if scenario == "partial_from_partial":
        order = _accepted_market_order(
            supabase, account_id, quantity=str(unit * 3), fingerprint="fp-target"
        )
        seeded = _fill(
            supabase,
            session,
            order,
            config=config,
            quantity=str(unit),
            price=case.entry_price,
            fill_event_id=SEED_FILL_EVENT_ID,
            reference=str(reference),
        )
        assert seeded.applied, "the staging fill was not applied, so the premise is not the premise"
        return _Staged(
            supabase,
            session,
            account_id,
            config,
            order,
            fill_kwargs(unit, Decimal(case.entry_price)),
        )

    if scenario == "full_open":
        order = _accepted_market_order(
            supabase, account_id, quantity=str(unit), fingerprint="fp-target"
        )
        return _Staged(
            supabase,
            session,
            account_id,
            config,
            order,
            fill_kwargs(unit, Decimal(case.entry_price)),
        )

    # Both closing scenarios open a LONG position first, through a filled buy order, so the
    # repeated fill has something to close and realized PnL is a number rather than a zero.
    open_size = unit * 2 if scenario == "partial_close" else unit
    opening = _accepted_market_order(
        supabase, account_id, quantity=str(open_size), fingerprint="fp-open"
    )
    opened = _fill(
        supabase,
        session,
        opening,
        config=config,
        quantity=str(open_size),
        price=case.entry_price,
        fill_event_id="fill-open",
        reference=str(Decimal(case.entry_price) + Decimal(case.reference_offset))
        if priced
        else case.entry_price,
    )
    assert opened.applied, "the opening fill was not applied, so there is no position to close"
    sell = _accepted_market_order(
        supabase,
        account_id,
        quantity=str(open_size),
        side="sell",
        fingerprint="fp-target",
    )
    closing_quantity = unit if scenario == "partial_close" else unit
    return _Staged(
        supabase, session, account_id, config, sell, fill_kwargs(closing_quantity, price)
    )


def _expected_repeat_outcome(state_after_first: str) -> str:
    """What a repeated fill must report, read off the requirement text rather than off the module.

    Requirement 16.3: ``FILLED``, ``CANCELLED`` and ``REJECTED`` are terminal and any transition out
    of one is refused - so a fill offered to an order the first application left terminal is
    refused **as terminal**. Requirement 16.9: a fill event whose identifier was already applied to
    the order changes nothing - so a fill offered to an order still open is refused **as a
    duplicate**. Both are "changes nothing"; which of the two answers arrives is decided by the
    state, and this function is that decision written down.
    """
    if state_after_first in REQUIREMENT_16_3_TERMINAL:
        return sim.FILL_TERMINAL
    return sim.FILL_DUPLICATE


def _offer_once(staged: _Staged) -> sim.FillOutcome:
    """Offer the staged fill event to ``apply_fill`` one more time.

    The same ``order`` mapping is handed over every time on purpose: ``apply_fill`` re-reads the
    order row inside its attempt and reads only ``id`` and ``account_id`` off the argument, so a
    caller holding a stale image cannot decide a guard. Passing the row back in from the previous
    outcome would hide that.
    """
    return _fill(staged.supabase, staged.session, staged.order, **staged.fill_kwargs)


#: The two columns ``009_paper_trading.sql`` stamps from the wall clock on every write
#: (Requirement 24.5), and the only values in a store that two identical runs are not obliged to
#: agree on. ``paper_repository``'s UPDATE payloads set ``updated_at`` to ``now()``, so a
#: cross-store comparison has to elide them or it would be asserting that two runs happened at the
#: same microsecond.
#:
#: Nothing else is elided. Every **business** instant a paper row carries - ``filled_at``,
#: ``occurred_at``, ``taken_at``, ``opened_at``, ``closed_at``, ``price_at``, ``last_price_at`` - is
#: passed into the module by its caller and is compared exactly, which is what Requirement 15.4's
#: byte-identical replay is a claim about. And the SAME-STORE comparison below keeps these two
#: columns in, because a repeated application that touched a row at all would move its
#: ``updated_at`` even if it wrote the same value into every other column.
ROW_WRITE_TIMESTAMPS: Tuple[str, ...] = ("updated_at",)


def _without_row_write_timestamps(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """``snapshot`` with :data:`ROW_WRITE_TIMESTAMPS` dropped from every row."""
    return {
        table: [
            {key: value for key, value in row.items() if key not in ROW_WRITE_TIMESTAMPS}
            for row in rows
        ]
        for table, rows in snapshot.items()
    }


P21_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    # Four scenarios times the four repetition counts.
    "runs": EXAMPLES * 4 * 4,
    # Per scenario the repeats are 0 + 1 + (2 or 3) + (4 or 5), so at least seven; times four
    # scenarios. The floor is the guaranteed minimum, not the observed rate - an observed rate as a
    # floor would fail the moment the generator drew its low end twice.
    "duplicate_applications": EXAMPLES * 4 * 7,
    "scenario_partial_from_partial": EXAMPLES,
    "scenario_full_open": EXAMPLES,
    "scenario_partial_close": EXAMPLES,
    "scenario_full_close": EXAMPLES,
    "repetition_1": EXAMPLES * 4,
    "repetition_2": EXAMPLES * 4,
    "repetition_three_or_four": EXAMPLES * 4,
    "repetition_five_or_more": EXAMPLES * 4,
    "repeat_met_the_duplicate_guard": EXAMPLES * 2,
    "repeat_met_the_terminal_guard": EXAMPLES * 2,
    "final_state_PARTIALLY_FILLED": EXAMPLES * 2,
    "final_state_FILLED": EXAMPLES * 2,
    "prior_fill_on_the_same_order": EXAMPLES,
    "non_zero_fee_recorded": EXAMPLES,
    "zero_fee_recorded": EXAMPLES,
    "non_zero_slippage_recorded": EXAMPLES,
    "zero_slippage_recorded": EXAMPLES,
    "position_opened": EXAMPLES,
    "position_partially_closed": EXAMPLES,
    "position_closed_to_zero": EXAMPLES,
    "trade_row_written": EXAMPLES,
    "realized_pnl_positive": EXAMPLES,
    "realized_pnl_negative": EXAMPLES,
    "durable_uq_paper_fill_event_refused": EXAMPLES * 4,
}

P21_LABELS: Dict[str, str] = {
    "repetition_five_or_more": "a fill event was offered five or more times",
    "repeat_met_the_duplicate_guard": "a repeat met Requirement 16.9's duplicate guard",
    "repeat_met_the_terminal_guard": "a repeat met Requirement 16.3's terminal guard",
    "prior_fill_on_the_same_order": "the order already carried a different fill",
    "non_zero_fee_recorded": "the repeated fill had charged a non-zero fee",
    "zero_fee_recorded": "the repeated fill had charged no fee",
    "non_zero_slippage_recorded": "the repeated fill had recorded non-zero slippage",
    "position_partially_closed": "the repeated fill had partially closed a position",
    "position_closed_to_zero": "the repeated fill had closed a position to exactly zero",
    "trade_row_written": "a paper_trades row had been written before the repeat",
    "realized_pnl_positive": "realized PnL had moved up before the repeat",
    "realized_pnl_negative": "realized PnL had moved down before the repeat",
    "durable_uq_paper_fill_event_refused": "uq_paper_fill_event refused a direct second insert",
}


def _record_p21_shape(
    recorder: Recorder, staged: _Staged, observable: Mapping[str, Any], scenario: str
) -> None:
    """Census the state ONE application actually reached, not the state the spine intended."""
    recorder.mark(f"scenario_{scenario}")
    state = str(observable["order_state"])
    if f"final_state_{state}" in recorder.counts:
        recorder.mark(f"final_state_{state}")

    fills = _fills_of(staged.supabase, staged.order["id"])
    target = next(
        row for row in fills if str(row["fill_event_id"]) == TARGET_FILL_EVENT_ID
    )
    recorder.mark("non_zero_fee_recorded" if int(target["fee_minor"]) else "zero_fee_recorded")
    recorder.mark(
        "non_zero_slippage_recorded"
        if int(target["slippage_minor"])
        else "zero_slippage_recorded"
    )
    if len(fills) > 1:
        recorder.mark("prior_fill_on_the_same_order")

    positions = observable["positions"]
    assert positions, f"{scenario} left no position at all, so the premise did not hold"
    _symbol, _side, size, _entry, closed = positions[0]
    if scenario in ("partial_from_partial", "full_open"):
        recorder.mark("position_opened")
    elif closed and size == Decimal("0"):
        recorder.mark("position_closed_to_zero")
    else:
        recorder.mark("position_partially_closed")

    if observable["trade_count"]:
        recorder.mark("trade_row_written")
    realized = observable["balances"]["realized_pnl"]
    if realized > 0:
        recorder.mark("realized_pnl_positive")
    elif realized < 0:
        recorder.mark("realized_pnl_negative")


def _assert_the_durable_index_refuses_a_second_fill_row(
    staged: _Staged, scenario: str, recorder: Recorder
) -> None:
    """Requirement 16.11's fill half, asserted against the Persistence_Layer itself.

    ``apply_fill``'s duplicate guard is a READ, and a read can be overtaken. What makes "one fill
    per event identifier" true whatever the interleaving is ``uq_paper_fill_event``, so the index is
    asked directly rather than inferred from the guard having answered.
    """
    supabase = staged.supabase
    before = _snapshot(supabase)
    mark = _mark(supabase)
    with pytest.raises(repo.PaperDuplicateFill):
        repo.insert_fill(
            supabase,
            order_id=str(staged.order["id"]),
            user_id=USER,
            session_id=SESSION,
            fill_event_id=TARGET_FILL_EVENT_ID,
            quantity=staged.fill_kwargs["quantity"],
            price=staged.fill_kwargs["price"],
            fee_minor=0,
            slippage_minor=0,
            filled_at=NOW,
        )
    assert _snapshot(supabase) == before, (
        f"P-21 (Requirement 16.11): uq_paper_fill_event refused the second "
        f"(order_id, fill_event_id) row for {scenario}, but something was persisted anyway: "
        f"{_wrote_since(supabase, mark)}"
    )
    recorder.mark("durable_uq_paper_fill_event_refused")


def _assert_repeating_the_fill_changes_nothing(
    case: _FillCase, scenario: str, recorder: Recorder
) -> None:
    """P-21 for one scenario: every ``n >= 1`` lands exactly where ``n = 1`` landed."""
    oracle_staged = _stage(case, scenario)
    oracle_first = _offer_once(oracle_staged)
    assert oracle_first.outcome == sim.FILL_APPLIED, (
        f"P-21: the single application of the {scenario} fill was not applied "
        f"({oracle_first.outcome}), so there is no oracle to compare against. Case: {case!r}"
    )
    oracle_snapshot = _without_row_write_timestamps(_snapshot(oracle_staged.supabase))
    oracle_observable = _observable(oracle_staged.supabase, oracle_staged.order["id"])
    expected_repeat = _expected_repeat_outcome(str(oracle_observable["order_state"]))

    _record_p21_shape(recorder, oracle_staged, oracle_observable, scenario)
    _assert_the_durable_index_refuses_a_second_fill_row(oracle_staged, scenario, recorder)

    for times in case.repetitions:
        recorder.mark("runs")
        if times == 1:
            recorder.mark("repetition_1")
        elif times == 2:
            recorder.mark("repetition_2")
        elif times <= 4:
            recorder.mark("repetition_three_or_four")
        else:
            recorder.mark("repetition_five_or_more")

        staged = _stage(case, scenario)
        context = f"{scenario} applied {times} time(s). Case: {case!r}"

        first = _offer_once(staged)
        assert first.outcome == sim.FILL_APPLIED, (
            f"P-21: the FIRST application must apply, but reported {first.outcome} for {context}"
        )
        # Taken in THIS store, so the comparison below is exact down to every row's own
        # ``updated_at``: a repeat that merely touched a row would move it.
        after_one_application = _snapshot(staged.supabase)
        repeats = [_offer_once(staged) for _ in range(times - 1)]

        for index, outcome in enumerate(repeats, start=2):
            recorder.mark("duplicate_applications")
            assert outcome.applied is False, (
                f"P-21 (Requirement 16.9): application {index} of the same fill_event_id "
                f"{TARGET_FILL_EVENT_ID!r} reported that it moved something, for {context}"
            )
            assert outcome.outcome == expected_repeat, (
                f"P-21: application {index} of the same fill_event_id must be refused as "
                f"{expected_repeat} - the order stood at {oracle_observable['order_state']} after "
                f"one application, and Requirements 16.3 and 16.9 answer that state that way - but "
                f"it reported {outcome.outcome} for {context}"
            )
            if expected_repeat == sim.FILL_DUPLICATE:
                recorder.mark("repeat_met_the_duplicate_guard")
            else:
                recorder.mark("repeat_met_the_terminal_guard")

        observable = _observable(staged.supabase, staged.order["id"])
        for claim in (
            "order_state",
            "filled_quantity",
            "fill_sum_from_rows",
            "order_fee_minor",
            "order_slippage_minor",
            "fill_count",
            "balances",
            "positions",
            "balance_event_count",
            "equity_snapshot_count",
            "trade_count",
        ):
            assert observable[claim] == oracle_observable[claim], (
                f"P-21 (Requirements 16.9, 16.11): applying the fill {times} times changed "
                f"{claim} - one application produced {oracle_observable[claim]!r}, {times} "
                f"produced {observable[claim]!r}. Case: {case!r}, scenario {scenario}"
            )
        assert observable["filled_quantity"] == observable["fill_sum_from_rows"], (
            f"P-21: paper_orders.filled_quantity ({observable['filled_quantity']}) and the sum "
            f"over paper_fills ({observable['fill_sum_from_rows']}) disagree for {context}"
        )
        # Claim 1, in this store and exact: the repeats wrote nothing at all, not even a touched
        # ``updated_at``.
        assert _snapshot(staged.supabase) == after_one_application, (
            f"P-21 (Requirement 16.9): the {times - 1} repeat(s) changed one of the seven tables "
            f"from what stood there after ONE application, for {context}"
        )
        # Claim 2, against the independently staged single-application store: n applications land
        # where one landed, whatever else the store's history was.
        assert _without_row_write_timestamps(_snapshot(staged.supabase)) == oracle_snapshot, (
            f"P-21 (Requirements 16.9, 16.11): applying the fill {times} times did not leave the "
            f"same seven tables as applying it once to an identically staged account, for "
            f"{context}"
        )


def test_p21_duplicate_fill_event_changes_nothing(request: Any) -> None:
    """Applying one fill event ``n >= 1`` times leaves what applying it once left.

    For all generated fill events ``f`` and repetition counts ``n >= 1``: offering ``f`` with the
    same ``fill_event_id`` ``n`` times produces the same order state, the same filled quantity, the
    same position and the same balances as offering it once - and, alongside those four, the same
    fill rows, the same recorded fee and slippage minor units, the same ledger, the same equity
    series and the same closed trades.

    The oracle is the single-application run itself: a second fresh store, staged identically, with
    the same event offered once. Nothing in the expectation is computed by asking the module what
    it did. The outcome a repeat must report is derived from Requirements 16.3 and 16.9 by
    :func:`_expected_repeat_outcome` rather than from ``paper_order_state.is_terminal``, and
    Requirement 16.11's ``uq_paper_fill_event`` is asked directly, because the module's duplicate
    guard is a read and a read can be overtaken.

    **Validates: Requirements 16.9, 16.11**
    """
    recorder = Recorder("P-21", P21_FLOORS, P21_LABELS)

    @PROPERTY_SETTINGS
    @given(case=fill_events_and_repetition_counts())
    def check(case: _FillCase) -> None:
        recorder.start()
        recorder.mark("examples")
        for scenario in FILL_SCENARIOS:
            _assert_repeating_the_fill_changes_nothing(case, scenario, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-22 (task 25.12) - A DUPLICATE ORDER INTENT YIELDS ONE ORDER
# ══════════════════════════════════════════════════════════════════════════

#: The three submission flavours, forced into every example. Each leaves the recorded order in a
#: different one of the three states a first submission can leave it in, and Requirement 16.8 says
#: a duplicate is answered "with its current Paper_Order_State" - so a property that only ever
#: repeated an accepted order would say nothing about the other two.
#:
#: ``limit``     accepted and resting     -> the recorded order is ACCEPTED
#: ``market``    accepted and filled on acceptance -> the recorded order is FILLED
#: ``rejected``  statically rejected      -> the recorded order is REJECTED, with its reason
SUBMIT_FLAVOURS: Tuple[str, ...] = ("limit", "market", "rejected")

#: The three variant kinds, every one of them forced against every flavour.
#:
#: ``identical``  the same mapping -> a duplicate (Requirement 16.8)
#: ``canonical``  a TEXTUALLY DIFFERENT mapping with the same fingerprint -> also a duplicate; this
#:                is the case that makes the canonicalisation a tested claim rather than a comment
#: ``mismatch``   a materially different quantity -> 409 (Requirement 16.15)
VARIANT_KINDS: Tuple[str, ...] = ("identical", "canonical", "mismatch")

#: Quantities a first submission may carry. Two decimals at most, so padding them stays inside the
#: symbol's 8-decimal quantity precision.
SUBMIT_QUANTITIES: Tuple[str, ...] = ("0.25", "1", "2")

#: Limit prices for the ``limit`` flavour. Two decimals at most (``price_precision`` is 2).
SUBMIT_LIMIT_PRICES: Tuple[str, ...] = ("50", "100", "96.25", "120.50")

#: Market closes for the ``market`` flavour, which prices its reference off the latest event.
SUBMIT_CLOSES: Tuple[str, ...] = ("50", "100", "96.25")

#: How many zeros the ``canonical`` variant pads a decimal with. At least one, because a padding of
#: none would make the variant textually identical and the bucket a lie.
PADDINGS: Tuple[int, ...] = (1, 2, 6)

#: The four Requirement 16.5 rejections the ``paper_orders`` CHECK constraints PERMIT, so the
#: persisted ``REJECTED`` order Requirement 16.5 asks for exists and can be re-fetched by a
#: duplicate. ``(reason, intent overrides)``. The four the constraints REFUSE are P-24's subject:
#: they raise a 400 and persist no row, so there is no recorded order for a key to point at.
REJECTED_FIRST_INTENTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("SYMBOL_NOT_VALIDATED", {"symbol": "ETH/USDT", "quantity": "0.5"}),
    ("QUANTITY_ABOVE_MAX", {"quantity": "2000"}),
    ("QUANTITY_PRECISION", {"quantity": "0.000000001"}),
    ("LIMIT_PRICE_PRECISION", {"order_type": "limit", "limit_price": "100.001"}),
)

#: Fee and slippage rates a session may record. Zero is included: neither of P-22's claims is about
#: a fee, and a session that charges nothing must still return one order for one key.
SUBMIT_FEE_RATES: Tuple[str, ...] = ("0", "0.001", "0.0025")


class _IdempotencyCase:
    """One generated first intent, and the sequence of repeats that follows it."""

    __slots__ = (
        "key",
        "side",
        "quantity",
        "limit_price",
        "close",
        "rejected_intent",
        "fee_rate",
        "padding",
        "variants",
    )

    def __init__(
        self,
        key: str,
        side: str,
        quantity: str,
        limit_price: str,
        close: str,
        rejected_intent: Tuple[str, Dict[str, Any]],
        fee_rate: str,
        padding: int,
        variants: Tuple[str, ...],
    ) -> None:
        self.key = key
        self.side = side
        self.quantity = quantity
        self.limit_price = limit_price
        self.close = close
        self.rejected_intent = rejected_intent
        self.fee_rate = fee_rate
        self.padding = int(padding)
        self.variants = variants

    def other_side(self) -> str:
        """The side the ``market`` flavour uses, so both sides are observed on every example."""
        return "sell" if self.side == "buy" else "buy"

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_IdempotencyCase(key={self.key!r}, side={self.side!r}, "
            f"quantity={self.quantity!r}, limit={self.limit_price!r}, close={self.close!r}, "
            f"rejected={self.rejected_intent[0]!r}, fee={self.fee_rate!r}, "
            f"padding={self.padding}, variants={self.variants})"
        )


@st.composite
def repeated_order_intents(draw: Any) -> _IdempotencyCase:
    """A first intent and at least four repeats of its key, three of them forced.

    The forced spine is :data:`VARIANT_KINDS` in full, so every example enters Requirement 16.8's
    duplicate path, Requirement 16.15's conflict, AND the decimal canonicalisation that keeps
    ``"1.0"`` and ``"1"`` the same order. A freely drawn variant list can spend an example on
    nothing but identical repeats, where the canonicalisation and the conflict are never reached.

    The tail draws one to three FURTHER variants from the same three kinds, so how many times the
    key is reused, and in what order the three kinds arrive, are both generated. At least one, so
    "more than three submissions carried this key" is a fact about every example.

    ``key`` is drawn as text rather than fixed, because ``uq_paper_order_idem`` arbitrates on the
    key's value and a single literal would leave the property silent about any key but that one.
    """
    tail = tuple(draw(st.lists(st.sampled_from(VARIANT_KINDS), min_size=1, max_size=3)))
    return _IdempotencyCase(
        key=draw(
            st.text(
                alphabet=st.characters(
                    min_codepoint=33, max_codepoint=126, blacklist_characters=" "
                ),
                min_size=1,
                max_size=48,
            ).filter(lambda text: text.strip() == text and text.strip() != "")
        ),
        side=draw(st.sampled_from(("buy", "sell"))),
        quantity=draw(st.sampled_from(SUBMIT_QUANTITIES)),
        limit_price=draw(st.sampled_from(SUBMIT_LIMIT_PRICES)),
        close=draw(st.sampled_from(SUBMIT_CLOSES)),
        rejected_intent=draw(st.sampled_from(REJECTED_FIRST_INTENTS)),
        fee_rate=draw(st.sampled_from(SUBMIT_FEE_RATES)),
        padding=draw(st.sampled_from(PADDINGS)),
        variants=VARIANT_KINDS + tail,
    )


def _first_intent(case: _IdempotencyCase, flavour: str) -> Dict[str, Any]:
    """The intent the first submission of ``flavour`` carries."""
    if flavour == "limit":
        return _intent(
            side=case.side,
            order_type="limit",
            quantity=case.quantity,
            limit_price=case.limit_price,
            idempotency_key=case.key,
        )
    if flavour == "market":
        return _intent(
            side=case.other_side(),
            quantity=case.quantity,
            idempotency_key=case.key,
        )
    overrides = dict(case.rejected_intent[1])
    overrides.setdefault("side", case.side)
    return _intent(idempotency_key=case.key, **overrides)


def _padded(value: Any, places: int) -> str:
    """``value`` with ``places`` extra trailing zeros - textually different, numerically the same.

    ``Decimal.quantize`` is not used: it would refuse a value whose own scale already exceeds the
    target, and the point here is to LENGTHEN the text. The result is compared against the original
    as text by the caller, so a padding that failed to change anything is caught rather than
    counted.
    """
    text = str(value)
    return (text if "." in text else text + ".") + ("0" * places)


def _variant_intent(
    case: _IdempotencyCase, flavour: str, kind: str
) -> Tuple[Dict[str, Any], bool]:
    """``(intent, is_a_duplicate)`` for one repeat of ``case.key``.

    The ``canonical`` variant differs from the first intent in **every** way the fingerprint is
    documented to ignore at once - decimals padded with trailing zeros, the side and order type
    upper-cased, the time-in-force lower-cased, the symbol surrounded by whitespace, and a
    ``signal_id`` added - so a fingerprint that stopped canonicalising any one of them would be
    caught here. It is asserted to be textually different and fingerprint-identical before it is
    submitted, so the variant cannot silently degenerate into ``identical``.

    The ``mismatch`` variant moves the quantity by ``0.01``, which
    ``paper_market_feed.canonical_number`` renders differently at any of the scales this file
    generates, so the fingerprints differ and Requirement 16.15's conflict is due.
    """
    first = _first_intent(case, flavour)
    if kind == "identical":
        return dict(first), True

    if kind == "canonical":
        variant = dict(first)
        variant["quantity"] = _padded(first["quantity"], case.padding)
        variant["symbol"] = f"  {first['symbol']}  "
        variant["side"] = str(first["side"]).upper()
        variant["order_type"] = str(first["order_type"]).upper()
        variant["time_in_force"] = sim.DEFAULT_TIME_IN_FORCE.lower()
        variant["signal_id"] = "signal-from-the-retry"
        if first.get("limit_price") is not None:
            variant["limit_price"] = _padded(first["limit_price"], case.padding)
        assert variant["quantity"] != first["quantity"], (
            "the canonical variant is textually identical to the first intent, so it would test "
            f"the identical path twice: {variant['quantity']!r}"
        )
        assert sim.order_fingerprint(variant) == sim.order_fingerprint(first), (
            "the canonical variant does not fingerprint the same as the first intent, so it is a "
            f"mismatch case wearing the wrong label: {variant!r} against {first!r}"
        )
        return variant, True

    variant = dict(first)
    variant["quantity"] = str(Decimal(str(first["quantity"])) + Decimal("0.01"))
    assert sim.order_fingerprint(variant) != sim.order_fingerprint(first), (
        "the mismatch variant fingerprints the same as the first intent, so no conflict is due "
        f"and the bucket would be a lie: {variant!r}"
    )
    return variant, False


def _submit_for(
    supabase: Any,
    session: Dict[str, Any],
    account_id: str,
    config: sim.SessionConfig,
    intent: Mapping[str, Any],
    case: _IdempotencyCase,
    flavour: str,
) -> sim.SubmitOutcome:
    """One submission, with the market flavour's event and instant supplied by the caller."""
    kwargs: Dict[str, Any] = {"config": config, "intent": dict(intent), "sleep": _Sleeps()}
    if flavour != "limit":
        kwargs["latest_event"] = _event(close=case.close, source_event_id="evt-submit")
    return _submit(supabase, session, account_id, **kwargs)


P22_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "flavours": EXAMPLES * 3,
    # Per flavour: the first submission, plus the two forced duplicates. The forced mismatch and any
    # drawn mismatch raise instead of returning, so they are counted in ``conflict_409_raised``.
    "submissions": EXAMPLES * 3 * 3,
    "flavour_limit": EXAMPLES,
    "flavour_market": EXAMPLES,
    "flavour_rejected": EXAMPLES,
    "variant_identical": EXAMPLES * 3,
    "variant_canonical": EXAMPLES * 3,
    "variant_mismatch": EXAMPLES * 3,
    "more_than_three_repeats": EXAMPLES * 3,
    "recorded_order_was_ACCEPTED": EXAMPLES,
    "recorded_order_was_FILLED": EXAMPLES,
    "recorded_order_was_REJECTED": EXAMPLES,
    "duplicate_returned_a_rejection_reason": EXAMPLES,
    "side_buy": EXAMPLES,
    "side_sell": EXAMPLES,
    "conflict_409_raised": EXAMPLES * 3,
    "durable_uq_paper_order_idem_refused": EXAMPLES * 3,
}

P22_LABELS: Dict[str, str] = {
    "variant_canonical": "a textually different but fingerprint-identical repeat was submitted",
    "variant_mismatch": "a materially different repeat answered 409",
    "more_than_three_repeats": "the same key was submitted more than three times",
    "recorded_order_was_ACCEPTED": "the duplicate was answered with a resting ACCEPTED order",
    "recorded_order_was_FILLED": "the duplicate was answered with a FILLED order",
    "recorded_order_was_REJECTED": "the duplicate was answered with a REJECTED order",
    "duplicate_returned_a_rejection_reason": "the duplicate carried the recorded rejection reason",
    "conflict_409_raised": "Requirement 16.15's PAPER_IDEMPOTENCY_CONFLICT was raised",
    "durable_uq_paper_order_idem_refused": "uq_paper_order_idem refused a direct second insert",
}


def _assert_the_durable_index_refuses_a_second_order_row(
    supabase: Any, case: _IdempotencyCase, flavour: str
) -> None:
    """Requirement 16.11's order half, asserted against the Persistence_Layer itself.

    ``submit_intent``'s idempotency probe is a READ inside the attempt, and a read can be
    overtaken by a concurrent first request. What makes "one order per key per session" true
    whatever the interleaving is ``uq_paper_order_idem``, so the index is asked directly.
    """
    before = _snapshot(supabase)
    mark = _mark(supabase)
    with pytest.raises(repo.PaperConcurrencyConflict):
        repo.insert_order(
            supabase,
            account_id=str(supabase.accounts[0]["id"]),
            user_id=USER,
            session_id=SESSION,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity="1",
            reference_price="100",
            fingerprint="fp-a-second-first-request",
            idempotency_key=case.key,
            order_state=PaperOrderState.CREATED,
        )
    assert _snapshot(supabase) == before, (
        f"P-22 (Requirement 16.11): uq_paper_order_idem refused the second "
        f"(session_id, idempotency_key) row for {flavour}, but something was persisted anyway: "
        f"{_wrote_since(supabase, mark)}"
    )


def _assert_one_key_yields_one_order(
    case: _IdempotencyCase, flavour: str, recorder: Recorder
) -> None:
    """P-22 for one flavour: one row, and every response is that row."""
    recorder.mark("flavours")
    recorder.mark(f"flavour_{flavour}")
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(fee_rate=Decimal(case.fee_rate), slippage_rate=Decimal("0"))

    first_intent = _first_intent(case, flavour)
    recorder.mark(f"side_{str(first_intent['side']).lower()}")
    first = _submit_for(
        supabase, session, account_id, config, first_intent, case, flavour
    )
    recorder.mark("submissions")
    after_first = _snapshot(supabase)
    recorded_id = str(first.order["id"])
    recorded_state = str(_order_by_id(supabase, recorded_id)["order_state"])
    if f"recorded_order_was_{recorded_state}" in recorder.counts:
        recorder.mark(f"recorded_order_was_{recorded_state}")

    assert len(supabase.orders) == 1, (
        f"P-22: the first submission of {flavour} persisted {len(supabase.orders)} orders, so the "
        f"premise of a single recorded order does not hold. Case: {case!r}"
    )
    if flavour == "rejected":
        assert recorded_state == PaperOrderState.REJECTED.value, (
            f"P-22: the {flavour} flavour was expected to persist a REJECTED order, but the row "
            f"stands at {recorded_state}. Case: {case!r}"
        )
        assert first.rejection_reason == case.rejected_intent[0], (
            f"P-22: the recorded rejection reason is {first.rejection_reason!r}, not the declared "
            f"{case.rejected_intent[0]!r}. Case: {case!r}"
        )

    if len(case.variants) > 3:
        recorder.mark("more_than_three_repeats")

    for index, kind in enumerate(case.variants, start=1):
        recorder.mark(f"variant_{kind}")
        variant, duplicate = _variant_intent(case, flavour, kind)
        context = (
            f"repeat {index} of {len(case.variants)} ({kind}) against the {flavour} flavour. "
            f"Case: {case!r}"
        )

        if not duplicate:
            with pytest.raises(sim.PaperIdempotencyConflict) as caught:
                _submit_for(
                    supabase, session, account_id, config, variant, case, flavour
                )
            error = caught.value
            recorder.mark("conflict_409_raised")
            assert error.code == PAPER_IDEMPOTENCY_CONFLICT, (
                f"P-22 (Requirement 16.15): the conflict must be reported as "
                f"{PAPER_IDEMPOTENCY_CONFLICT}, got {error.code} for {context}"
            )
            assert error.http_status == 409, (
                f"P-22 (Requirement 16.15): the conflicting reuse must be a 409, got "
                f"{error.http_status} for {context}"
            )
            assert str(error.details["order_id"]) == recorded_id, (
                f"P-22 (Requirement 16.15): the conflict must name the previously created order "
                f"{recorded_id}, named {error.details['order_id']} for {context}"
            )
            assert error.details["fingerprint"] != error.details["recorded_fingerprint"], (
                f"P-22: the conflict reported two identical fingerprints, so it was not a "
                f"conflicting reuse at all, for {context}"
            )
        else:
            recorder.mark("submissions")
            repeat = _submit_for(
                supabase, session, account_id, config, variant, case, flavour
            )
            assert repeat.duplicate is True, (
                f"P-22 (Requirement 16.8): the repeat was not reported as a duplicate for "
                f"{context}"
            )
            assert str(repeat.order["id"]) == recorded_id, (
                f"P-22 (Requirement 16.8): the repeat returned order "
                f"{repeat.order['id']} rather than the previously created {recorded_id} for "
                f"{context}"
            )
            assert str(repeat.order["order_state"]) == recorded_state, (
                f"P-22 (Requirement 16.8): the repeat must return the recorded order with its "
                f"CURRENT state {recorded_state}, returned {repeat.order['order_state']} for "
                f"{context}"
            )
            if flavour == "rejected":
                assert repeat.rejection_reason == case.rejected_intent[0], (
                    f"P-22: the duplicate of a REJECTED order dropped its recorded reason for "
                    f"{context}"
                )
                recorder.mark("duplicate_returned_a_rejection_reason")

        assert len(supabase.orders) == 1, (
            f"P-22 (Requirements 16.8, 16.11): exactly one order may exist for one key in one "
            f"session, found {len(supabase.orders)} after {context}"
        )
        assert str(supabase.orders[0]["idempotency_key"]) == case.key, (
            f"P-22: the single stored order does not carry the generated key {case.key!r} after "
            f"{context}"
        )
        assert str(supabase.orders[0]["id"]) == recorded_id, (
            f"P-22: the single stored order is no longer the one the first submission created, "
            f"after {context}"
        )
        assert _snapshot(supabase) == after_first, (
            f"P-22 (Requirements 16.8, 16.15): the repeat changed persisted state - neither a "
            f"duplicate nor a conflicting reuse may move a balance, a position or an order - "
            f"after {context}"
        )

    _assert_the_durable_index_refuses_a_second_order_row(supabase, case, flavour)
    recorder.mark("durable_uq_paper_order_idem_refused")


def test_p22_duplicate_order_intent_yields_one_order(request: Any) -> None:
    """One idempotency key in one Paper_Session yields exactly one order, returned every time.

    For all generated intents carrying the same key within one Paper_Session: exactly one
    ``paper_orders`` row exists, every response that is not a conflict returns **that** row with
    its current Paper_Order_State, no repeat moves a balance, a position, an order or an equity
    point, and a repeat whose order parameters differ answers 409
    ``PAPER_IDEMPOTENCY_CONFLICT`` naming the recorded order (Requirement 16.15).

    The oracle is a count - one row, carrying the generated key - taken from the Persistence_Layer
    double rather than from ``SubmitOutcome.duplicate``, which is asserted as a further claim.
    Requirement 16.11's ``uq_paper_order_idem`` is asked directly, because the module's idempotency
    probe is a read inside the attempt and a read can be overtaken by a concurrent first request.

    **Validates: Requirements 16.8, 16.11, 16.15**
    """
    recorder = Recorder("P-22", P22_FLOORS, P22_LABELS)

    @PROPERTY_SETTINGS
    @given(case=repeated_order_intents())
    def check(case: _IdempotencyCase) -> None:
        recorder.start()
        recorder.mark("examples")
        for flavour in SUBMIT_FLAVOURS:
            _assert_one_key_yields_one_order(case, flavour, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE LITERALS THIS FILE WROTE DOWN, HELD AGAINST THEIR SOURCES
# ══════════════════════════════════════════════════════════════════════════


def test_the_written_down_terminal_states_are_the_modules_own() -> None:
    """:data:`REQUIREMENT_16_3_TERMINAL` is a literal, so it needs holding to the module.

    P-21's expected-repeat rule is derived from this list rather than from
    ``paper_order_state.is_terminal``, which is what keeps the derivation independent. That
    independence is only worth having if the list itself is right, and a table claim belongs in a
    test of its own rather than inside a property.
    """
    from backend_app.backend.paper.paper_order_state import is_terminal

    for state in PaperOrderState:
        assert is_terminal(state) == (state.value in REQUIREMENT_16_3_TERMINAL), (
            f"Requirement 16.3 names exactly {REQUIREMENT_16_3_TERMINAL} as terminal, but the "
            f"module answers is_terminal({state.value}) = {is_terminal(state)}"
        )


def test_the_declared_rejection_reasons_are_the_modules_own() -> None:
    """The four ``REJECTED_FIRST_INTENTS`` reasons are spelled the way ``paper_simulator`` spells them."""
    declared = {reason for reason, _ in REJECTED_FIRST_INTENTS}
    assert declared <= set(sim.REJECTION_REASONS), (
        f"P-22 declares rejection reasons the module does not know: "
        f"{sorted(declared - set(sim.REJECTION_REASONS))}"
    )


def test_exactly_two_property_functions_are_claimed_by_this_module() -> None:
    """The scoreboard counts nested functions too, so a stray ``test_p21_`` would claim P-21 twice.

    ``tests/property/test_property_coverage.py`` discovers by ``ast.walk``, which descends into
    function bodies - a helper named ``test_p21_something`` inside a property's body would be
    counted as a second claim on P-21 and reported as DUPLICATED. This module must therefore
    contain exactly one ``test_p21_`` and one ``test_p22_`` in the whole file, at module scope.
    """
    import ast
    import inspect
    import re
    from pathlib import Path

    path = Path(inspect.getsourcefile(test_p21_duplicate_fill_event_changes_nothing) or "")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    pattern = re.compile(r"^test_p(\d+)_")
    claimed = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and pattern.match(node.name)
    )
    assert claimed == [
        "test_p21_duplicate_fill_event_changes_nothing",
        "test_p22_duplicate_order_intent_yields_one_order",
    ], f"this module claims {claimed}"

    top_level = sorted(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and pattern.match(node.name)
    )
    assert top_level == claimed, (
        f"a property claim is nested rather than at module scope: {sorted(set(claimed) - set(top_level))}"
    )


def test_this_module_never_creates_its_own_event_loop() -> None:
    """``asyncio.run`` closes the loop it created and breaks a later test that expected one.

    Every coroutine this file drives goes through ``_run_coroutine`` - by way of the harness's
    ``_submit`` and ``_fill`` - which runs it on a private loop and closes only that one.

    Asserted on the parsed CALLS rather than on the source text, because a textual check would trip
    over the name it is looking for inside its own assertion and would then be a test of its own
    spelling.
    """
    import ast
    import inspect
    from pathlib import Path

    path = Path(inspect.getsourcefile(_stage) or "")
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    called = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    forbidden = sorted(
        name
        for name in called
        if name.endswith((".run", "new_event_loop", "set_event_loop", "run_until_complete"))
    )
    assert forbidden == [], f"this module drives coroutines itself: {forbidden}"
    assert callable(_run_coroutine), "the harness's loop runner is not importable"
