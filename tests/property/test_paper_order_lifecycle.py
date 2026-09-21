"""
tests/property/test_paper_order_lifecycle.py - P-17, P-18, P-19, P-20 and P-24.

Spec: marketplace-subscriptions-paper-trading tasks 25.7, 25.8, 25.9, 25.10 and 25.14.
``design.md`` -> "Property-to-test mapping". Requirements 16.1, 16.2, 16.3, 16.4, 16.5, 16.6,
16.7, 16.13, 16.14.

THE FIVE PROPERTIES LIVING HERE
-------------------------------
``test_p17_every_paper_order_state_is_reachable_from_created``  (Requirements 16.1, 16.2)
``test_p18_terminal_paper_order_states_are_final``              (Requirement 16.3)
``test_p19_illegal_paper_order_transition_leaves_state``         (Requirements 16.2, 16.4)
``test_p20_fill_sum_is_bounded_and_filled_iff_equal``            (Requirements 16.7, 16.13, 16.14)
``test_p24_invalid_intents_are_rejected_without_side_effects``   (Requirements 16.5, 16.6)

Exactly one ``test_p{n}_`` function per property at module scope, and nothing nested carries that
prefix: ``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and counts a nested
function too, so a helper named ``test_p20_...`` inside the body would claim P-20 twice.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
The real ``paper_simulator.submit_intent``, ``apply_fill`` and ``check_resting_orders`` are driven
against ``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this
repository has, which enforces the eight unique indexes ``009_paper_trading.sql`` declares. There
is no second paper double and no mock of the repository: every statement these properties observe
is issued by the same code production issues it from.

The harness is ``tests/test_paper_order_lifecycle_writes.py``'s, imported rather than rebuilt -
``_run_coroutine``, ``_Sleeps``, ``_config``, ``_seed``, ``_mark``, ``_wrote_since``, ``_event``,
``_intent``, ``_submit``, ``_snapshot``, ``_accepted_market_order``, ``_fill`` and the constants.
A second harness would be a second account of what a session is. ``_run_coroutine`` drives every
coroutine on a private loop; ``asyncio.run`` is never used, because it closes the loop it created
and breaks any later test in the session that expected one.

THE ORACLES, AND WHY EACH IS INDEPENDENT OF THE CODE UNDER TEST
--------------------------------------------------------------
* **P-17** - :data:`REACHABLE_FROM_CREATED`, the transitive closure of Requirement 16.2's
  transition relation computed **in this file** by :func:`_reachable_from`, and the relation itself
  written down in :data:`REQUIREMENT_16_2_TRANSITIONS` as a literal read off the requirement text.
  The observed path of each order is reconstructed from the ``paper_orders`` statement log by
  :func:`_replay_order_states`, which applies the double's own ``WHERE`` semantics - so the path
  the oracle judges is not a value the simulator reported about itself.
  ``paper_order_state.REACHABLE_FROM_CREATED`` is deliberately NOT used as the oracle: it is the
  module's own closure of the module's own table, and asserting against it would hold for any
  table whatsoever. :func:`test_the_written_down_transition_table_matches_the_module` guards the
  literal instead, which is where a table claim belongs.
* **P-18** - the order row and its ``paper_fills`` rows, snapshotted **before** the subsequent
  events and compared field by field afterwards. No expectation is computed by calling anything.
* **P-19** - :data:`ILLEGAL_PAIRS`, the set complement of :data:`REQUIREMENT_16_2_TRANSITIONS`
  over the six Requirement 16.1 states. 36 ordered pairs, 9 permitted, 27 not.
  ``paper_order_state.can_transition`` is never called.
* **P-20** - the fill sum recomputed by :func:`_fill_sum_from_rows` over ``paper_fills``, never
  read from ``paper_orders.filled_quantity``. The column is then compared against that sum, so a
  drift between the rows and the column is a failure rather than something the oracle inherits.
* **P-24** - each generated case **declares** the reason it must be rejected for and the branch it
  must take; :func:`_oracle_rejection` and :func:`_oracle_column_refusal` re-implement Requirement
  16.5's eight checks in the requirement's own order and 009's four CHECK predicates as literals,
  and verify the declaration before the module is asked. ``sim.static_rejection_reason`` and
  ``sim.unrepresentable_order_constraint`` are never called to build an expectation.

EVERY COMPARISON IS EXACT ``Decimal``
-------------------------------------
No ``pytest.approx``, no ``round()``, no tolerance, anywhere. Quantities, prices and fill sums are
compared with ``==`` on ``Decimal``; fees and slippage are compared as the exact ``int`` minor
units the columns store. A tolerance would make P-20's "if and only if" and P-18's "unchanged"
into statements about nothing.

NON-VACUITY: A FORCED SPINE, A CENSUS, AND FLOORS THAT ARE NOT NEGOTIABLE
------------------------------------------------------------------------
A generated order that is submitted and never touched again satisfies P-18 and P-20 trivially, and
a run in which no illegal pair is ever attempted satisfies P-19 for free. So every generator
**forces** its hard cases into every example and every property carries a :class:`_Recorder` with
an explicit floor per bucket and a Hypothesis ``event`` label, following
``tests/property/test_paper_persistence_roundtrip.py`` and
``tests/property/test_market_event_dedupe.py``. What is counted is what the run **observed**, not
what the spine intended, so an example in which a forced case was reclassified is reported by the
floors rather than assumed away. A shortfall is fixed by FORCING the case in the generator, never
by lowering a floor.

The floors that read ``EXAMPLES`` are forcing claims: the spine puts that case in every example,
so anything below the example count means the forcing stopped working, not that the case is rare.

Between them the five spines reach: every one of the six Requirement 16.1 states as a persisted
value; every one of the nine Requirement 16.2 transitions including ``PARTIALLY_FILLED ->
PARTIALLY_FILLED``; every one of the three terminal states receiving later fills, later market
events and later session activity; seventeen of the twenty-seven illegal ordered pairs (see the
gap below); a fill sum equal to the order quantity at the **last representable minor unit** and
one unit short of it; a refused over-fill; an ignored duplicate; and every one of P-24's six
rejection conditions in both of the branches Requirement 16.5 has.

THE RECORDED REQUIREMENT 16.5 DIVERGENCE, HONOURED RATHER THAN FOUGHT
--------------------------------------------------------------------
``009_paper_trading.sql`` lines 728-731 declare ``chk_paper_order_side``, ``chk_paper_order_type``,
``chk_paper_order_quantity`` and ``chk_paper_order_limit_price``. Four of Requirement 16.5's eight
rejections describe an intent whose own values those constraints refuse, so the **persisted**
``REJECTED`` order the requirement asks for cannot exist. ``paper_simulator`` answers 400
``PAPER_ORDER_INVALID`` carrying the same reason in ``details["validation"]`` plus
``details["blocked_by"]``, and writes no row.

P-24 therefore asserts **both** branches - a persisted ``REJECTED`` order where the column permits
the value, and a 400 with no row where it does not - and in both cases that no balance, position or
equity value moved. The CHECK constraints are not relaxed: relaxing them to let a rejected order
carry a nonsense value weakens a control that currently keeps such a row out of the table
entirely, and that trade is the spec's to make. See ``paper_simulator.ORDER_COLUMN_CONSTRAINTS``.

GAPS LEFT OPEN, STATED RATHER THAN ASSERTED AROUND
--------------------------------------------------
1. **``paper_simulator`` has no cancel path.** Nothing in the module writes ``CANCELLED`` - task
   25.x does not implement a cancellation, and ``grep -i cancel`` over
   ``backend/paper/paper_simulator.py`` returns nothing (pinned by
   :func:`test_the_simulator_writes_neither_created_nor_cancelled`). Task 25.7 names cancellations
   as part of P-17's input, and ``CANCELLED`` is one of Requirement 16.1's six states, so the
   cancellation steps in :func:`order_lifecycle_programmes` are issued through
   ``paper_repository.update_order`` - the Persistence_Layer - guarded by the state the driver
   read. Without them ``ACCEPTED -> CANCELLED`` and ``PARTIALLY_FILLED -> CANCELLED`` would never
   be observed and two ninths of Requirement 16.2 would be untested. The same is true of
   ``ACCEPTED -> REJECTED``: Requirement 16.2 permits it and no simulator path produces it.
2. **Ten of P-19's twenty-seven illegal pairs are not attemptable through ``paper_simulator``**,
   because it never *targets* ``CREATED`` (it only ever INSERTs at it) and never targets
   ``CANCELLED`` (gap 1). Those ten are the six pairs targeting ``CREATED`` and the four illegal
   pairs targeting ``CANCELLED``. That is not assumed - the property asserts, over every statement
   of every example, that no ``paper_orders`` UPDATE ever carried either target. The ten are
   P-49's, which issues the same attempt directly to the Persistence_Layer.
3. **A partial write past the account UPDATE is this transport's, not this property's.**
   PostgREST offers no ``ROLLBACK``; ``paper_simulator._NoRetryPastTheMoney`` reports the residue
   rather than hiding it. P-19 exercised that boundary and found a genuine defect there - see the
   note on :func:`test_p19_illegal_paper_order_transition_leaves_state`.
"""

from __future__ import annotations

import ast
import inspect
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.errors import PAPER_ORDER_INVALID
from backend_app.backend.paper.paper_order_state import (
    LEGACY_STATUS_FOR_STATE,
    PAPER_ORDER_TRANSITIONS,
    PaperOrderState,
)

# ── The census, written once for the four paper property modules that need it. ────────────
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
#: deadline. One example of each property drives a double-digit number of real simulator calls,
#: so ``too_slow`` is suppressed rather than the example count being cut.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 16.1 AND 16.2, WRITTEN DOWN HERE
# ══════════════════════════════════════════════════════════════════════════
#
# Literals read off the requirement text, not imported from ``paper_order_state``. The oracles
# below are built from these, so a disagreement between the requirement and the module is a
# failure this file can see. ``test_the_written_down_transition_table_matches_the_module`` guards
# the literals themselves, which is where a table claim belongs.

#: Requirement 16.1: "exactly 6 values".
REQUIREMENT_16_1_STATES: Tuple[str, ...] = (
    "CREATED",
    "ACCEPTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "REJECTED",
)

#: Requirement 16.2: "SHALL permit only the following transitions", in the requirement's order.
REQUIREMENT_16_2_TRANSITIONS: Tuple[Tuple[str, str], ...] = (
    ("CREATED", "ACCEPTED"),
    ("CREATED", "REJECTED"),
    ("ACCEPTED", "PARTIALLY_FILLED"),
    ("ACCEPTED", "FILLED"),
    ("ACCEPTED", "CANCELLED"),
    ("ACCEPTED", "REJECTED"),
    ("PARTIALLY_FILLED", "PARTIALLY_FILLED"),
    ("PARTIALLY_FILLED", "FILLED"),
    ("PARTIALLY_FILLED", "CANCELLED"),
)

#: Requirement 16.3: "FILLED, CANCELLED and REJECTED SHALL be terminal".
REQUIREMENT_16_3_TERMINAL: Tuple[str, ...] = ("FILLED", "CANCELLED", "REJECTED")

PERMITTED_PAIRS: FrozenSet[Tuple[str, str]] = frozenset(REQUIREMENT_16_2_TRANSITIONS)

ALL_PAIRS: FrozenSet[Tuple[str, str]] = frozenset(
    (origin, target)
    for origin in REQUIREMENT_16_1_STATES
    for target in REQUIREMENT_16_1_STATES
)

#: P-19's oracle: the complement of the permitted set. 36 - 9 = 27 ordered pairs.
ILLEGAL_PAIRS: FrozenSet[Tuple[str, str]] = ALL_PAIRS - PERMITTED_PAIRS


def _reachable_from(origin: str) -> FrozenSet[str]:
    """The transitive closure of :data:`REQUIREMENT_16_2_TRANSITIONS` from ``origin``.

    P-17's oracle, computed here from the written-down relation. Breadth over the pair list rather
    than over a ``state -> targets`` mapping, so the closure is derived from the same literal the
    requirement was read into and not from a second arrangement of it.
    """
    seen: Set[str] = {origin}
    frontier: List[str] = [origin]
    while frontier:
        state = frontier.pop()
        for source, target in REQUIREMENT_16_2_TRANSITIONS:
            if source == state and target not in seen:
                seen.add(target)
                frontier.append(target)
    return frozenset(seen)


#: Every persisted Paper_Order_State must be in here (P-17).
REACHABLE_FROM_CREATED: FrozenSet[str] = _reachable_from("CREATED")


# ══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 16.5's CHECK ORDER, AND 009's FOUR COLUMN CONSTRAINTS
# ══════════════════════════════════════════════════════════════════════════

#: The eight static checks of Requirement 16.5 plus Requirement 16.6's, in evaluation order. Used
#: by :func:`_oracle_rejection`; :func:`test_the_written_down_rejection_order_matches_the_module`
#: holds the list against ``sim.REJECTION_REASONS``.
REQUIREMENT_16_5_ORDER: Tuple[str, ...] = (
    "QUANTITY_NOT_POSITIVE",
    "QUANTITY_ABOVE_MAX",
    "QUANTITY_PRECISION",
    "SYMBOL_NOT_VALIDATED",
    "ORDER_TYPE_UNSUPPORTED",
    "SIDE_UNSUPPORTED",
    "LIMIT_PRICE_NOT_POSITIVE",
    "LIMIT_PRICE_PRECISION",
    "NO_VALIDATED_PRICE",
    "INSUFFICIENT_FUNDS",
)

#: ``009_paper_trading.sql`` lines 728-731, verbatim, as ``constraint -> the CHECK clause``.
#: Written down here so P-24's branch prediction is a statement about the migration rather than a
#: call to ``sim.unrepresentable_order_constraint``.
#: :func:`test_the_written_down_column_checks_are_009s_own_text` holds them against the file.
ORDER_COLUMN_CHECKS: Tuple[Tuple[str, str], ...] = (
    ("chk_paper_order_quantity", "CHECK (quantity > 0)"),
    ("chk_paper_order_limit_price", "CHECK (limit_price IS NULL OR limit_price > 0)"),
    ("chk_paper_order_type", "CHECK (order_type IN ('market', 'limit'))"),
    ("chk_paper_order_side", "CHECK (side IN ('buy', 'sell'))"),
)

#: The vocabularies those two CHECKs admit, written down rather than imported.
COLUMN_ORDER_TYPES: Tuple[str, ...] = ("market", "limit")
COLUMN_SIDES: Tuple[str, ...] = ("buy", "sell")


def _places(value: Decimal) -> int:
    """How many decimal places ``value`` carries once trailing zeros are removed.

    The oracle's own reading of "carries more decimal places than the recorded precision". A
    padded ``0.50`` carries one place, not two, because a client that padded has not exceeded
    anything - and ``Decimal('100')`` normalizes to ``1E+2`` and carries none.
    """
    exponent = Decimal(value).normalize().as_tuple().exponent
    return -int(exponent) if isinstance(exponent, int) and exponent < 0 else 0


def _oracle_column_refusal(intent: Mapping[str, Any]) -> Optional[str]:
    """The ``paper_orders`` CHECK ``intent``'s own values violate, or ``None``.

    :data:`ORDER_COLUMN_CHECKS`, evaluated here against the raw intent mapping. Independent of
    ``sim.unrepresentable_order_constraint`` by construction: it shares no code with it and reads
    the predicates out of this file's literal.
    """
    quantity = Decimal(str(intent["quantity"]))
    limit_price = intent.get("limit_price")
    if quantity <= 0:
        return "chk_paper_order_quantity"
    if limit_price is not None and Decimal(str(limit_price)) <= 0:
        return "chk_paper_order_limit_price"
    if str(intent.get("order_type") or "market").lower() not in COLUMN_ORDER_TYPES:
        return "chk_paper_order_type"
    if str(intent["side"]).lower() not in COLUMN_SIDES:
        return "chk_paper_order_side"
    return None


def _oracle_rejection(
    intent: Mapping[str, Any],
    config: sim.SessionConfig,
    *,
    available_balance: Decimal,
    reference_price: Optional[Decimal],
) -> Optional[str]:
    """The first Requirement 16.5 / 16.6 check ``intent`` fails, or ``None``.

    Re-implemented from the requirement text in :data:`REQUIREMENT_16_5_ORDER`'s order, reading
    only the session configuration's recorded **figures**. It calls nothing in
    ``paper_simulator``: an expectation built from ``static_rejection_reason`` would hold for any
    implementation of it, including one that returned a constant.

    ``INSUFFICIENT_FUNDS`` is evaluated as ``quantity * price > available_balance``, which is a
    **lower bound** on Requirement 16.6's ``notional + fee + slippage`` allowance - so this oracle
    only ever claims insufficiency where it is certain, whatever rates the session recorded. The
    generator builds that case with a wide margin for exactly this reason.
    """
    quantity = Decimal(str(intent["quantity"]))
    side = str(intent["side"]).lower()
    order_type = str(intent.get("order_type") or "market").lower()
    raw_limit = intent.get("limit_price")
    limit_price = None if raw_limit is None else Decimal(str(raw_limit))

    if quantity <= 0:
        return "QUANTITY_NOT_POSITIVE"
    if quantity > config.max_order_quantity:
        return "QUANTITY_ABOVE_MAX"
    if _places(quantity) > config.quantity_precision:
        return "QUANTITY_PRECISION"
    if str(intent["symbol"]) not in config.validated_symbols:
        return "SYMBOL_NOT_VALIDATED"
    if order_type not in config.supported_order_types:
        return "ORDER_TYPE_UNSUPPORTED"
    if side not in config.supported_sides:
        return "SIDE_UNSUPPORTED"
    if limit_price is not None:
        if limit_price <= 0:
            return "LIMIT_PRICE_NOT_POSITIVE"
        if _places(limit_price) > config.price_precision:
            return "LIMIT_PRICE_PRECISION"

    price = limit_price if limit_price is not None else reference_price
    if price is None or price <= 0:
        return "NO_VALIDATED_PRICE"
    if quantity * price > available_balance:
        return "INSUFFICIENT_FUNDS"
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES EACH RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════


#: ``_Recorder`` and ``_publish_hypothesis_statistics`` used to be defined here. Tasks 25.11-25.13
#: added ``test_paper_idempotence.py`` (P-21, P-22) and ``test_paper_confluence.py`` (P-23), which
#: need the same census verbatim, so both moved to ``tests/property/paper_census.py``: three copies
#: of a census helper are three censuses, and the one that gets fixed is never the one that is
#: running. The module-private aliases are kept because the properties below refer to the census by
#: these names, and renaming twenty-odd call sites would be a diff about nothing.
_Recorder = Recorder
_publish_hypothesis_statistics = publish_hypothesis_statistics


# ══════════════════════════════════════════════════════════════════════════
# SHARED READERS OVER THE PERSISTENCE_LAYER DOUBLE
# ══════════════════════════════════════════════════════════════════════════

#: The capital every driver below starts from. Large enough that no forced case is ever an
#: accidental ``INSUFFICIENT_FUNDS`` - P-24 is the one property that wants that rejection, and it
#: seeds its own small account for it.
RICH = Decimal("1000000")

#: The price band every generated market event and limit price sits in. The floor of 10 is what
#: makes an "untriggerable" buy limit at 1 genuinely untriggerable, whatever the tail draws.
PRICE_FLOOR = Decimal("10")

#: A buy limit no generated candle can reach, for an order that must stay ``ACCEPTED``.
UNTRIGGERABLE_LIMIT = "1"


def _order_rows(supabase: Any) -> List[Dict[str, Any]]:
    """Every ``paper_orders`` row the double holds, as plain dicts."""
    return [dict(row) for row in supabase.orders]


def _order_by_id(supabase: Any, order_id: Any) -> Dict[str, Any]:
    """One stored ``paper_orders`` row, by id. Absence is a failure, never an empty dict."""
    for row in supabase.orders:
        if str(row["id"]) == str(order_id):
            return dict(row)
    raise AssertionError(f"no paper_orders row {order_id!r} is stored; rows: {supabase.orders}")


def _fill_sum_from_rows(supabase: Any, order_id: Any) -> Decimal:
    """P-20's oracle: the sum of ``paper_fills.quantity`` for one order, as exact ``Decimal``.

    Recomputed from the fill ROWS. ``paper_orders.filled_quantity`` is deliberately not read here
    - it is the value the property then compares against this one, so that a drift between the two
    is a failure rather than something the oracle inherits.
    """
    total = Decimal("0")
    for row in supabase.fills:
        if str(row["order_id"]) == str(order_id):
            total += Decimal(str(row["quantity"]))
    return total


def _fee_minor_from_rows(supabase: Any, order_id: Any) -> int:
    """The exact minor units of fee recorded on one order's fills."""
    return sum(
        int(row.get("fee_minor") or 0)
        for row in supabase.fills
        if str(row["order_id"]) == str(order_id)
    )


def _fills_of(supabase: Any, order_id: Any) -> List[Dict[str, Any]]:
    """One order's ``paper_fills`` rows, as plain dicts, in insertion order."""
    return [dict(row) for row in supabase.fills if str(row["order_id"]) == str(order_id)]


def _order_update_targets(supabase: Any) -> List[str]:
    """Every ``order_state`` value a ``paper_orders`` UPDATE carried, in statement order.

    Gap 2's evidence: ``paper_simulator`` never *targets* ``CREATED`` or ``CANCELLED``, and this
    is how that is checked rather than assumed.
    """
    return [
        str((query.payload or {}).get("order_state"))
        for query in supabase.statements
        if query.table_name == repo.ORDERS_TABLE and query.op == "update"
    ]


def _replay_order_states(
    supabase: Any,
) -> Tuple[List[str], Dict[str, List[str]], Dict[str, str]]:
    """Reconstruct each order's state path from the ``paper_orders`` statement log.

    Returns ``(insert_states, paths, final)``: the ``order_state`` every INSERT landed at, the
    observed path per order id, and the state each order ended at.

    The reconstruction applies the double's own ``WHERE`` semantics and nothing else: an UPDATE
    carrying ``.eq("order_state", E)`` applied if and only if the order stood at ``E`` when the
    statement was issued, which is exactly what ``paper_repository.update_order``'s guard means
    and exactly what ``FakeSupabase._matching`` does. So a guarded UPDATE that matched no row
    contributes no step - which is the "leaves the stored state unchanged" of Requirement 16.4,
    read off the log rather than taken on trust.

    Every path starts at ``CREATED`` because every order is INSERTed there; the caller asserts
    that separately on ``insert_states``, and a path is therefore a claim only about what the log
    contains.
    """
    insert_states: List[str] = []
    paths: Dict[str, List[str]] = {}
    final: Dict[str, str] = {}

    for query in supabase.statements:
        if query.table_name != repo.ORDERS_TABLE:
            continue
        if query.op == "insert":
            insert_states.append(str((query.payload or {}).get("order_state")))
            continue
        if query.op != "update":
            continue
        columns = query.filtered_columns()
        if "id" not in columns:
            continue
        order_id = str(query.filter_value("id"))
        target = str((query.payload or {}).get("order_state"))
        path = paths.setdefault(order_id, ["CREATED"])
        state = final.setdefault(order_id, "CREATED")
        if "order_state" in columns and str(query.filter_value("order_state")) != state:
            # The guard matched no row: another writer moved the order first, so nothing was
            # written and the order did not move.
            continue
        final[order_id] = target
        path.append(target)

    return insert_states, paths, final


def _created_order(
    supabase: Any,
    account_id: str,
    *,
    quantity: str = "1",
    order_type: str = "market",
    limit_price: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``paper_orders`` row left at ``CREATED``, written through the repository.

    ``tests/test_paper_order_lifecycle_writes._accepted_market_order`` cannot serve here: it moves
    the order to ``ACCEPTED`` on the way out, and ``CREATED`` is one of the six origins P-19 has to
    attempt an illegal transition from.
    """
    return repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=USER,
        session_id=SESSION,
        symbol=SYMBOL,
        side="buy",
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        reference_price="100",
        fingerprint=f"fp-{order_type}-{quantity}",
        order_state=PaperOrderState.CREATED,
    )


def _move_order(
    supabase: Any, order: Mapping[str, Any], target: PaperOrderState
) -> Dict[str, Any]:
    """Move one order to ``target`` through the Persistence_Layer, guarded by its stored state.

    The cancellation and the ``ACCEPTED -> REJECTED`` of gap 1: ``paper_simulator`` writes neither,
    so a property that needs those two of Requirement 16.2's nine transitions to be observed has
    to issue them here. Guarded by ``expected_state`` read off the row, so the transition is
    validated exactly as every other write of a paper order state is.
    """
    return repo.update_order(
        supabase,
        user_id=USER,
        order_id=order["id"],
        order_state=target,
        expected_state=PaperOrderState(str(order["order_state"])),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATED MATERIAL THE FIVE SPINES SHARE
# ══════════════════════════════════════════════════════════════════════════

#: ``(order_quantity, participation_rate, event_volume)`` triples for which the deterministic
#: participation cap ``quantize(volume x participation_rate)`` divides the order quantity into
#: **exactly four** equal fills at the symbol's 8-decimal quantity precision.
#:
#: Four and not "some number", because that is what makes the partial-fill spine deterministic:
#: event 1 leaves ``PARTIALLY_FILLED``, events 2 and 3 exercise Requirement 16.2's one
#: self-transition ``PARTIALLY_FILLED -> PARTIALLY_FILLED``, and event 4 lands on the order
#: quantity **exactly**, which is Requirement 16.14's equality and P-20's "if and only if".
#: Every product below is exact in decimal: 1x0.25, 2x0.25, 1x0.2 and 2x0.5 need no rounding.
FILL_TRIPLES: Tuple[Tuple[str, str, str], ...] = (
    ("1", "0.25", "1"),
    ("2", "0.25", "2"),
    ("0.8", "0.20", "1"),
    ("4", "0.50", "2"),
)

#: Buy-limit prices. Two decimals at most (``price_precision`` is 2 for this market) and every one
#: comfortably above :data:`PRICE_FLOOR`, so a candle can be drawn below them without reaching
#: :data:`UNTRIGGERABLE_LIMIT`.
LIMIT_PRICES: Tuple[str, ...] = ("50", "100", "96.25", "120.50")

#: How far below its limit a triggering candle's ``low`` is drawn. Zero is included deliberately:
#: a candle whose low **equals** the limit is the boundary of ``limit_fill_triggered``.
LOW_OFFSETS: Tuple[str, ...] = ("0", "1", "5.25")

#: Market-order quantities, and market-order reference closes.
MARKET_QUANTITIES: Tuple[str, ...] = ("0.5", "1", "2")
MARKET_CLOSES: Tuple[str, ...] = ("50", "100", "96.25")

#: Fee and slippage rates the session records. Zero is in both, because a zero-fee fill is
#: Requirement 18.6's conservation case; a non-zero one is what makes P-18's "or its recorded
#: fees" a claim about a number rather than about two zeros.
FEE_RATES: Tuple[str, ...] = ("0", "0.001", "0.0025")
SLIPPAGE_RATES: Tuple[str, ...] = ("0", "0.0005", "0.002")

#: Non-zero fee rates, for the properties that need a recorded fee to compare.
NON_ZERO_FEE_RATES: Tuple[str, ...] = ("0.001", "0.0025", "0.01")

#: The four Requirement 16.5 rejections the ``paper_orders`` CHECK constraints PERMIT, so the
#: persisted ``REJECTED`` order the requirement asks for exists. ``(reason, intent overrides)``.
PERSISTED_REJECTION_INTENTS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("SYMBOL_NOT_VALIDATED", {"symbol": "ETH/USDT", "quantity": "0.5"}),
    ("QUANTITY_ABOVE_MAX", {"quantity": "2000"}),
    ("QUANTITY_PRECISION", {"quantity": "0.000000001"}),
    ("LIMIT_PRICE_PRECISION", {"order_type": "limit", "limit_price": "100.001"}),
)


def _candle(
    *,
    limit_price: str,
    low_offset: str,
    volume: str,
    index: int,
) -> Dict[str, Any]:
    """One validated market event that triggers a buy limit at ``limit_price``.

    ``low = limit_price - low_offset``, so the candle traded at or through the limit and
    ``limit_fill_triggered`` answers ``True``. ``close`` is the limit itself, which keeps the
    resting fill price - exactly the limit, with no slippage in either direction - the same number
    whatever the candle did around it. ``source_event_id`` is unique per index, because
    ``resting_fill_event_id`` is derived from it and a repeated identity would be a duplicate fill
    rather than a further partial one.
    """
    low = Decimal(limit_price) - Decimal(low_offset)
    assert low >= PRICE_FLOOR, (
        f"a generated candle low of {low} would reach the untriggerable limit "
        f"{UNTRIGGERABLE_LIMIT}; LIMIT_PRICES and LOW_OFFSETS have drifted apart"
    )
    return _event(
        close=limit_price,
        low=str(low),
        high=str(Decimal(limit_price) + Decimal("10")),
        volume=volume,
        source_event_id=f"evt-{index}",
        at=NOW + timedelta(minutes=index),
    )


# ══════════════════════════════════════════════════════════════════════════
# P-17 (task 25.7) - REACHABILITY
# ══════════════════════════════════════════════════════════════════════════

#: The tail step kinds :func:`order_lifecycle_programmes` may append after the spine. None of them
#: can reclassify a spine order: an untriggerable resting order never fills, a market order fills
#: itself and nothing else, and a rejection touches one new order.
TAIL_STEPS: Tuple[str, ...] = ("bad_intent", "untriggerable_limit", "market")


class _Programme:
    """One generated order-lifecycle programme: the spine's drawn values, and its tail."""

    __slots__ = (
        "fill_triple",
        "limit_price",
        "low_offsets",
        "market_quantity",
        "market_close",
        "bad_intent",
        "fee_rate",
        "slippage_rate",
        "tail_steps",
        "tail_events",
    )

    def __init__(
        self,
        fill_triple: Tuple[str, str, str],
        limit_price: str,
        low_offsets: Tuple[str, ...],
        market_quantity: str,
        market_close: str,
        bad_intent: Tuple[str, Dict[str, Any]],
        fee_rate: str,
        slippage_rate: str,
        tail_steps: Tuple[str, ...],
        tail_events: int,
    ) -> None:
        self.fill_triple = fill_triple
        self.limit_price = limit_price
        self.low_offsets = low_offsets
        self.market_quantity = market_quantity
        self.market_close = market_close
        self.bad_intent = bad_intent
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.tail_steps = tail_steps
        self.tail_events = int(tail_events)

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Programme(triple={self.fill_triple}, limit={self.limit_price!r}, "
            f"lows={self.low_offsets}, market={self.market_quantity!r}@{self.market_close!r}, "
            f"bad={self.bad_intent[0]!r}, fee={self.fee_rate!r}, slip={self.slippage_rate!r}, "
            f"tail={self.tail_steps}, tail_events={self.tail_events})"
        )


@st.composite
def order_lifecycle_programmes(draw: Any) -> _Programme:
    """A programme of intents, market events and cancellations that reaches EVERY reachable state.

    The spine below is forced into every example, because a programme that only submits and fills
    market orders would exercise four of Requirement 16.2's nine transitions and would never
    persist ``CANCELLED`` or ``PARTIALLY_FILLED`` - P-17 would then be a statement about a third
    of the state machine. Everything about the spine is **drawn** - which quantity and
    participation cap, which limit price, how far below it each candle traded, which of the four
    persistable rejections, which fee and slippage rates, and what follows - so forcing the *shape*
    of each case does not fix its *values*.

    The tail is appended rather than interleaved, and is restricted to steps that cannot
    reclassify a spine order: one to three further orders, none of which any candle can trigger or
    which fill only themselves, and one or two further candles. At most two candles, because the
    order that must end ``PARTIALLY_FILLED`` needs four caps to fill and receives one on the
    spine's last candle - three in total is still short of four, whatever the tail draws. At
    **least** one of each, because a tail drawn from zero left ``tail_beyond_the_spine`` at 23 of a
    floor of 25 on one run: a bucket whose occupancy is a coin toss is forced in the generator, not
    traded away by lowering its floor.
    """
    return _Programme(
        fill_triple=draw(st.sampled_from(FILL_TRIPLES)),
        limit_price=draw(st.sampled_from(LIMIT_PRICES)),
        low_offsets=tuple(
            draw(st.lists(st.sampled_from(LOW_OFFSETS), min_size=4, max_size=4))
        ),
        market_quantity=draw(st.sampled_from(MARKET_QUANTITIES)),
        market_close=draw(st.sampled_from(MARKET_CLOSES)),
        bad_intent=draw(st.sampled_from(PERSISTED_REJECTION_INTENTS)),
        fee_rate=draw(st.sampled_from(FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        tail_steps=tuple(
            draw(st.lists(st.sampled_from(TAIL_STEPS), min_size=1, max_size=3))
        ),
        tail_events=draw(st.integers(min_value=1, max_value=2)),
    )


def _run_lifecycle_programme(programme: _Programme) -> Tuple[Any, Dict[str, Any]]:
    """Drive one programme through the real simulator. Returns the client and the spine's order ids.

    The order of the steps is what makes each transition observable rather than hoped for:

    ``bad``   an invalid intent                              -> ``CREATED -> REJECTED``
    ``A``     a resting order cancelled untouched            -> ``ACCEPTED -> CANCELLED``
    ``B``     a resting order rejected untouched             -> ``ACCEPTED -> REJECTED``
    ``C``     one capped fill, then cancelled                -> ``ACCEPTED -> PARTIALLY_FILLED``,
                                                                 ``PARTIALLY_FILLED -> CANCELLED``
    ``D``     four capped fills                              -> ``ACCEPTED -> PARTIALLY_FILLED``,
                                                                 ``PARTIALLY_FILLED ->
                                                                 PARTIALLY_FILLED`` twice,
                                                                 ``PARTIALLY_FILLED -> FILLED``
    ``G``     one capped fill on the last candle             -> ends ``PARTIALLY_FILLED``
    ``E``     a resting order no candle can reach            -> ends ``ACCEPTED``
    ``market`` a market order                                -> ``CREATED -> ACCEPTED``,
                                                                 ``ACCEPTED -> FILLED``

    ``A`` is cancelled and ``B`` rejected **before** the first candle, because
    ``check_resting_orders`` fills every triggered resting order on one event and an untouched
    ``ACCEPTED`` order would otherwise not stay untouched.
    """
    quantity, participation, volume = programme.fill_triple
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(programme.fee_rate),
        slippage_rate=Decimal(programme.slippage_rate),
        participation_rate=Decimal(participation),
    )
    sleeps = _Sleeps()
    ids: Dict[str, Any] = {}

    def resting(name: str, *, limit: str, size: str) -> None:
        outcome = _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=_intent(
                order_type="limit", quantity=size, limit_price=limit, idempotency_key=f"key-{name}"
            ),
            sleep=sleeps,
        )
        assert outcome.accepted, f"{name} was not accepted: {outcome.rejection_reason}"
        ids[name] = outcome.order["id"]

    def candle(index: int) -> None:
        _run_coroutine(
            sim.check_resting_orders(
                supabase,
                session,
                _candle(
                    limit_price=programme.limit_price,
                    low_offset=programme.low_offsets[min(index - 1, 3)],
                    volume=volume,
                    index=index,
                ),
                config=config,
                account_id=account_id,
                sleep=sleeps,
            )
        )

    # 1. an invalid intent -> a persisted REJECTED order from CREATED
    _reason, overrides = programme.bad_intent
    rejected = _submit(
        supabase, session, account_id, config=config, intent=_intent(**overrides), sleep=sleeps
    )
    ids["bad"] = rejected.order["id"]

    # 2/3. one resting order cancelled and one rejected while still untouched
    resting("A", limit=programme.limit_price, size=quantity)
    _move_order(supabase, _order_by_id(supabase, ids["A"]), PaperOrderState.CANCELLED)
    resting("B", limit=programme.limit_price, size=quantity)
    _move_order(supabase, _order_by_id(supabase, ids["B"]), PaperOrderState.REJECTED)

    # 4/5. the two orders the first candle fills
    resting("C", limit=programme.limit_price, size=quantity)
    resting("D", limit=programme.limit_price, size=quantity)
    candle(1)
    _move_order(supabase, _order_by_id(supabase, ids["C"]), PaperOrderState.CANCELLED)

    # 6/7. D's self-transitions
    candle(2)
    candle(3)

    # 8. the order that ends PARTIALLY_FILLED, filled once by the last candle
    resting("G", limit=programme.limit_price, size=quantity)
    candle(4)

    # 9. the order that ends ACCEPTED, at a limit no generated candle reaches
    resting("E", limit=UNTRIGGERABLE_LIMIT, size=quantity)

    # 10. the market order
    market = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(quantity=programme.market_quantity, idempotency_key="key-market"),
        latest_event=_event(
            close=programme.market_close, source_event_id="evt-market", at=NOW + timedelta(hours=1)
        ),
        sleep=sleeps,
    )
    ids["market"] = market.order["id"]

    # 11. the freely drawn tail
    for index, step in enumerate(programme.tail_steps):
        if step == "bad_intent":
            _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=_intent(**programme.bad_intent[1]),
                sleep=sleeps,
            )
        elif step == "untriggerable_limit":
            resting(f"tail-{index}", limit=UNTRIGGERABLE_LIMIT, size=quantity)
        else:
            _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=_intent(
                    quantity=programme.market_quantity, idempotency_key=f"key-tail-{index}"
                ),
                latest_event=_event(
                    close=programme.market_close,
                    source_event_id=f"evt-tail-{index}",
                    at=NOW + timedelta(hours=2, minutes=index),
                ),
                sleep=sleeps,
            )
    for extra in range(programme.tail_events):
        candle(5 + extra)

    assert sleeps.delays == [], (
        f"a retry backoff was slept, so a statement conflicted and the programme is not the one "
        f"the spine describes: {sleeps.delays}"
    )
    return supabase, ids


P17_TRANSITION_KEYS: Tuple[str, ...] = tuple(
    f"transition_{origin}->{target}" for origin, target in REQUIREMENT_16_2_TRANSITIONS
)
P17_STATE_KEYS: Tuple[str, ...] = tuple(
    f"state_{state}" for state in REQUIREMENT_16_1_STATES
)
#: ``CREATED`` is absent on purpose: ``submit_intent`` transitions an order out of ``CREATED``
#: inside the same attempt that inserted it, so no programme leaves one resting there. It is
#: observed as every path's ORIGIN instead, which ``state_CREATED`` counts.
P17_FINAL_KEYS: Tuple[str, ...] = (
    "final_ACCEPTED",
    "final_PARTIALLY_FILLED",
    "final_FILLED",
    "final_CANCELLED",
    "final_REJECTED",
)

P17_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "orders": EXAMPLES * 8,
    "paths_longer_than_three_steps": EXAMPLES,
    "tail_beyond_the_spine": EXAMPLES,
    **{key: EXAMPLES for key in P17_TRANSITION_KEYS},
    **{key: EXAMPLES for key in P17_STATE_KEYS},
    **{key: EXAMPLES for key in P17_FINAL_KEYS},
}

P17_LABELS: Dict[str, str] = {
    "tail_beyond_the_spine": "the programme carried steps beyond the forced spine",
    "paths_longer_than_three_steps": "an order moved through more than three states",
    "transition_PARTIALLY_FILLED->PARTIALLY_FILLED": (
        "Requirement 16.2's one self-transition was taken"
    ),
    "final_PARTIALLY_FILLED": "an order was left PARTIALLY_FILLED",
    "final_CANCELLED": "an order was left CANCELLED",
    "final_REJECTED": "an order was left REJECTED",
    "final_FILLED": "an order was left FILLED",
    "final_ACCEPTED": "an order was left ACCEPTED",
}


def _assert_every_persisted_state_is_reachable(
    supabase: Any, programme: _Programme, recorder: _Recorder
) -> None:
    """P-17, asserted in four parts against the closure computed in this file."""
    insert_states, paths, final = _replay_order_states(supabase)
    rows = _order_rows(supabase)

    assert rows, f"the programme persisted no order at all: {programme!r}"
    assert set(insert_states) == {"CREATED"}, (
        "P-17 (Requirement 16.2): every paper order must enter the state machine at CREATED - it "
        f"is the only origin the relation has - but INSERTs landed at {sorted(set(insert_states))}."
        f" Programme: {programme!r}"
    )

    stored = {str(row["id"]): str(row["order_state"]) for row in rows}
    recorder.mark("orders", len(rows))

    unknown = sorted({state for state in stored.values()} - set(REQUIREMENT_16_1_STATES))
    assert not unknown, (
        "P-17 (Requirement 16.1): the Paper_Order_State is one of exactly six values, and "
        f"{unknown} is not among them. Programme: {programme!r}"
    )

    observed_states: Set[str] = set()
    observed_transitions: Set[Tuple[str, str]] = set()
    for order_id, path in sorted(paths.items()):
        observed_states.update(path)
        for step, (origin, target) in enumerate(zip(path, path[1:])):
            observed_transitions.add((origin, target))
            assert (origin, target) in PERMITTED_PAIRS, (
                f"P-17 (Requirement 16.2): order {order_id} took {origin} -> {target} at step "
                f"{step + 1} of {path}, which is not one of the nine permitted transitions. "
                f"Programme: {programme!r}"
            )
        if len(path) > 4:
            recorder.mark("paths_longer_than_three_steps")

    for order_id, state in sorted(stored.items()):
        replayed = final.get(order_id, "CREATED")
        assert replayed == state, (
            f"P-17: order {order_id} is stored at {state} but the paper_orders statement log "
            f"replays to {replayed}; the log and the row disagree about the same order, so one of "
            f"them is not what the state machine did. Programme: {programme!r}"
        )
        assert state in REACHABLE_FROM_CREATED, (
            f"P-17 (Requirements 16.1, 16.2): order {order_id} is persisted at {state}, which is "
            f"not reachable from CREATED under the Requirement 16.2 transitions. The closure is "
            f"{sorted(REACHABLE_FROM_CREATED)}. Programme: {programme!r}"
        )

    for state in sorted(observed_states):
        recorder.mark(f"state_{state}")
    for origin, target in sorted(observed_transitions):
        recorder.mark(f"transition_{origin}->{target}")
    for state in sorted(set(stored.values())):
        if f"final_{state}" in recorder.counts:
            recorder.mark(f"final_{state}")


def test_p17_every_paper_order_state_is_reachable_from_created(request: Any) -> None:
    """Every persisted Paper_Order_State is reachable from ``CREATED`` by Requirement 16.2.

    For all generated sequences of order intents, market events and cancellations: every order
    enters at ``CREATED``, every state it is persisted at is one of Requirement 16.1's six, every
    step it took is one of Requirement 16.2's nine permitted transitions, and the state it ends at
    lies in the transitive closure of that relation from ``CREATED``.

    The oracle is the closure computed here by :func:`_reachable_from` over
    :data:`REQUIREMENT_16_2_TRANSITIONS`, a literal read off the requirement text; the path each
    order took is reconstructed from the ``paper_orders`` statement log by
    :func:`_replay_order_states` rather than reported by the simulator about itself, and the
    replayed end state is compared against the stored row so the reconstruction cannot drift from
    what the database holds.

    **Validates: Requirements 16.1, 16.2**
    """
    recorder = _Recorder("P-17", P17_FLOORS, P17_LABELS)

    @PROPERTY_SETTINGS
    @given(programme=order_lifecycle_programmes())
    def check(programme: _Programme) -> None:
        recorder.start()
        recorder.mark("examples")
        if programme.tail_steps or programme.tail_events:
            recorder.mark("tail_beyond_the_spine")
        supabase, _ids = _run_lifecycle_programme(programme)
        _assert_every_persisted_state_is_reachable(supabase, programme, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-18 (task 25.8) - TERMINALITY
# ══════════════════════════════════════════════════════════════════════════

#: What a session may do to an order **after** it reached a terminal state. Every one of the four
#: is forced into every example; the tail draws more of the same.
SUBSEQUENT_EVENTS: Tuple[str, ...] = ("fill", "replayed_fill", "candle", "new_order")

#: Fill quantities and prices a later fill attempt may carry. Both are strictly positive, because
#: ``apply_fill`` refuses a non-positive one as a malformed call before any guard runs and that
#: would test the argument check rather than Requirement 16.3. ``0.00000001`` is the smallest
#: quantity the symbol's 8-decimal precision can express - the fill most likely to slip past a
#: guard that compared loosely.
LATER_FILL_QUANTITIES: Tuple[str, ...] = ("0.00000001", "0.1", "1", "2")
LATER_FILL_PRICES: Tuple[str, ...] = ("50", "100", "1000")


class _Aftermath:
    """One generated "what happens after the order became terminal" programme."""

    __slots__ = (
        "fill_triple",
        "limit_price",
        "low_offsets",
        "bad_intent",
        "fee_rate",
        "slippage_rate",
        "later_quantity",
        "later_price",
        "market_quantity",
        "market_close",
        "tail",
    )

    def __init__(
        self,
        fill_triple: Tuple[str, str, str],
        limit_price: str,
        low_offsets: Tuple[str, ...],
        bad_intent: Tuple[str, Dict[str, Any]],
        fee_rate: str,
        slippage_rate: str,
        later_quantity: str,
        later_price: str,
        market_quantity: str,
        market_close: str,
        tail: Tuple[str, ...],
    ) -> None:
        self.fill_triple = fill_triple
        self.limit_price = limit_price
        self.low_offsets = low_offsets
        self.bad_intent = bad_intent
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.later_quantity = later_quantity
        self.later_price = later_price
        self.market_quantity = market_quantity
        self.market_close = market_close
        self.tail = tail

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Aftermath(triple={self.fill_triple}, limit={self.limit_price!r}, "
            f"bad={self.bad_intent[0]!r}, fee={self.fee_rate!r}, slip={self.slippage_rate!r}, "
            f"later={self.later_quantity!r}@{self.later_price!r}, tail={self.tail})"
        )


@st.composite
def terminal_order_aftermaths(draw: Any) -> _Aftermath:
    """A programme that reaches ALL THREE terminal states and then keeps acting on the session.

    All three, in every example, because Requirement 16.3 names three states and a property that
    only ever reached ``FILLED`` would say nothing about the other two. And each terminal order is
    reached the way a session reaches it - the ``FILLED`` one by four capped fills so it carries an
    accumulated filled quantity and a **non-zero** recorded fee, the ``CANCELLED`` one after a
    partial fill so its filled quantity and fee are non-zero too. Without that, "no subsequent
    event changes its filled quantity or its recorded fees" would be a comparison of zero with
    zero: :data:`P18_FLOORS` fails the run if either stops being non-zero.

    ``fee_rate`` is drawn from :data:`NON_ZERO_FEE_RATES` for the same reason.

    The tail draws one to three **further** subsequent events on top of the forced four. At least
    one, because a tail drawn from zero left ``tail_beyond_the_spine`` at 23 against a floor of 25
    on one run - a bucket whose occupancy is a coin toss is forced in the generator, never fixed by
    lowering its floor.
    """
    return _Aftermath(
        fill_triple=draw(st.sampled_from(FILL_TRIPLES)),
        limit_price=draw(st.sampled_from(LIMIT_PRICES)),
        low_offsets=tuple(
            draw(st.lists(st.sampled_from(LOW_OFFSETS), min_size=4, max_size=4))
        ),
        bad_intent=draw(st.sampled_from(PERSISTED_REJECTION_INTENTS)),
        fee_rate=draw(st.sampled_from(NON_ZERO_FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        later_quantity=draw(st.sampled_from(LATER_FILL_QUANTITIES)),
        later_price=draw(st.sampled_from(LATER_FILL_PRICES)),
        market_quantity=draw(st.sampled_from(MARKET_QUANTITIES)),
        market_close=draw(st.sampled_from(MARKET_CLOSES)),
        tail=tuple(
            draw(st.lists(st.sampled_from(SUBSEQUENT_EVENTS), min_size=1, max_size=3))
        ),
    )


def _order_fingerprint_of_state(supabase: Any, order_id: Any) -> Dict[str, Any]:
    """Everything P-18 claims is unchanged, for one order, as comparable values.

    The three the requirement names - the state, the filled quantity and the recorded fees - are
    read out separately as an exact ``Decimal`` and exact ``int`` so a failure names the claim, and
    the whole row and the whole fill set travel alongside so nothing else can move either.
    """
    row = _order_by_id(supabase, order_id)
    return {
        "order_state": str(row["order_state"]),
        "filled_quantity": Decimal(str(row["filled_quantity"])),
        "order_fee_minor": int(row.get("fee_minor") or 0),
        "order_slippage_minor": int(row.get("slippage_minor") or 0),
        "fills_fee_minor": _fee_minor_from_rows(supabase, order_id),
        "fill_sum": _fill_sum_from_rows(supabase, order_id),
        "row": row,
        "fills": _fills_of(supabase, order_id),
    }


def _assert_terminal_order_did_not_move(
    supabase: Any,
    order_id: Any,
    before: Mapping[str, Any],
    *,
    what: str,
    aftermath: _Aftermath,
) -> None:
    """Requirement 16.3, asserted field by field on one terminal order after ``what``."""
    after = _order_fingerprint_of_state(supabase, order_id)
    context = f"order {order_id} ({before['order_state']}) after {what}. Aftermath: {aftermath!r}"

    assert after["order_state"] == before["order_state"], (
        f"P-18 (Requirement 16.3): a terminal state is final, but {context} moved to "
        f"{after['order_state']}"
    )
    assert after["filled_quantity"] == before["filled_quantity"], (
        f"P-18 (Requirement 16.3): the filled quantity of a terminal order changed from "
        f"{before['filled_quantity']} to {after['filled_quantity']} - {context}"
    )
    assert after["order_fee_minor"] == before["order_fee_minor"], (
        f"P-18 (Requirement 16.3): paper_orders.fee_minor changed from "
        f"{before['order_fee_minor']} to {after['order_fee_minor']} - {context}"
    )
    assert after["fills_fee_minor"] == before["fills_fee_minor"], (
        f"P-18 (Requirement 16.3): the fees recorded on the order's fills changed from "
        f"{before['fills_fee_minor']} to {after['fills_fee_minor']} - {context}"
    )
    assert after["order_slippage_minor"] == before["order_slippage_minor"], (
        f"P-18: the slippage recorded on a terminal order changed - {context}"
    )
    assert after["fill_sum"] == before["fill_sum"], (
        f"P-18: the sum over the order's paper_fills rows changed from {before['fill_sum']} to "
        f"{after['fill_sum']} - {context}"
    )
    assert after["fills"] == before["fills"], (
        f"P-18: the order's paper_fills rows are not the rows it had - {context}"
    )
    assert after["row"] == before["row"], (
        f"P-18: the paper_orders row is not the row it was - {context}. "
        f"Differences: "
        + str(
            {
                key: (before["row"].get(key), after["row"].get(key))
                for key in set(before["row"]) | set(after["row"])
                if before["row"].get(key) != after["row"].get(key)
            }
        )
    )


P18_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "terminal_FILLED": EXAMPLES,
    "terminal_CANCELLED": EXAMPLES,
    "terminal_REJECTED": EXAMPLES,
    "filled_order_carries_a_nonzero_fee": EXAMPLES,
    "filled_order_accumulated_four_fills": EXAMPLES,
    "cancelled_order_carries_a_partial_fill": EXAMPLES,
    "rejected_order_carries_no_fill": EXAMPLES,
    "subsequent_fill": EXAMPLES * 3,
    "subsequent_replayed_fill": EXAMPLES * 2,
    "subsequent_candle": EXAMPLES,
    "subsequent_new_order": EXAMPLES,
    "terminal_checks": EXAMPLES * 12,
    "tail_beyond_the_spine": EXAMPLES,
}

P18_LABELS: Dict[str, str] = {
    "filled_order_carries_a_nonzero_fee": "the FILLED order carried a non-zero recorded fee",
    "cancelled_order_carries_a_partial_fill": "the CANCELLED order carried a partial fill",
    "rejected_order_carries_no_fill": "the REJECTED order carried no fill",
    "subsequent_replayed_fill": "a terminal order was handed one of its own fills again",
    "subsequent_candle": "a triggering candle arrived after the orders became terminal",
    "subsequent_new_order": "the session accepted a further order after the terminal ones",
    "tail_beyond_the_spine": "the aftermath carried events beyond the forced four",
}


def _run_terminal_aftermath(aftermath: _Aftermath, recorder: _Recorder) -> None:
    """Reach all three terminal states, then keep acting on the session and assert nothing moved."""
    quantity, participation, volume = aftermath.fill_triple
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(aftermath.fee_rate),
        slippage_rate=Decimal(aftermath.slippage_rate),
        participation_rate=Decimal(participation),
    )
    sleeps = _Sleeps()
    counter = {"event": 0}

    def resting(name: str) -> Any:
        outcome = _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=_intent(
                order_type="limit",
                quantity=quantity,
                limit_price=aftermath.limit_price,
                idempotency_key=f"key-{name}",
            ),
            sleep=sleeps,
        )
        assert outcome.accepted, f"{name} was not accepted: {outcome.rejection_reason}"
        return outcome.order["id"]

    def candle() -> List[Any]:
        counter["event"] += 1
        index = counter["event"]
        return _run_coroutine(
            sim.check_resting_orders(
                supabase,
                session,
                _candle(
                    limit_price=aftermath.limit_price,
                    low_offset=aftermath.low_offsets[min(index - 1, 3)],
                    volume=volume,
                    index=index,
                ),
                config=config,
                account_id=account_id,
                sleep=sleeps,
            )
        )

    # ── the premise: one order into each of Requirement 16.3's three terminal states ──
    filled_id = resting("filled")
    candle()
    cancelled_id = resting("cancelled")
    candle()
    _move_order(supabase, _order_by_id(supabase, cancelled_id), PaperOrderState.CANCELLED)
    candle()
    candle()
    rejected_id = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(**aftermath.bad_intent[1]),
        sleep=sleeps,
    ).order["id"]

    terminals = {
        "FILLED": filled_id,
        "CANCELLED": cancelled_id,
        "REJECTED": rejected_id,
    }
    snapshots = {
        name: _order_fingerprint_of_state(supabase, order_id)
        for name, order_id in terminals.items()
    }

    for name, snapshot in snapshots.items():
        assert snapshot["order_state"] == name, (
            f"the premise is wrong: the {name} order is stored at {snapshot['order_state']}. "
            f"Aftermath: {aftermath!r}"
        )
        recorder.mark(f"terminal_{name}")

    assert snapshots["FILLED"]["order_fee_minor"] > 0, (
        "the FILLED order recorded no fee, so P-18's 'or its recorded fees' would compare zero "
        f"with zero. Aftermath: {aftermath!r}"
    )
    recorder.mark("filled_order_carries_a_nonzero_fee")
    assert len(snapshots["FILLED"]["fills"]) == 4, (
        f"the FILLED order carries {len(snapshots['FILLED']['fills'])} fill(s), not the four the "
        f"participation cap divides its quantity into. Aftermath: {aftermath!r}"
    )
    recorder.mark("filled_order_accumulated_four_fills")
    assert snapshots["CANCELLED"]["fill_sum"] > 0, (
        "the CANCELLED order carries no partial fill, so its filled quantity claim would be zero "
        f"against zero. Aftermath: {aftermath!r}"
    )
    recorder.mark("cancelled_order_carries_a_partial_fill")
    assert snapshots["REJECTED"]["fills"] == [], (
        f"the REJECTED order carries a fill: {snapshots['REJECTED']['fills']}"
    )
    recorder.mark("rejected_order_carries_no_fill")

    # ── the subsequent events, each followed by the same three assertions ──
    def check_all(what: str) -> None:
        for name, order_id in terminals.items():
            _assert_terminal_order_did_not_move(
                supabase, order_id, snapshots[name], what=what, aftermath=aftermath
            )
            recorder.mark("terminal_checks")

    fresh = {"n": 0}

    def apply_subsequent(kind: str) -> None:
        if kind == "fill":
            for name, order_id in terminals.items():
                fresh["n"] += 1
                outcome = _fill(
                    supabase,
                    session,
                    _order_by_id(supabase, order_id),
                    config=config,
                    quantity=aftermath.later_quantity,
                    price=aftermath.later_price,
                    fill_event_id=f"later-{fresh['n']}",
                )
                assert outcome.outcome == sim.FILL_TERMINAL, (
                    f"P-18 (Requirement 16.3): a fill against the {name} order returned "
                    f"{outcome.outcome}, not FILL_TERMINAL. Aftermath: {aftermath!r}"
                )
                assert outcome.applied is False and outcome.admission is None
                recorder.mark("subsequent_fill")
            check_all("a fresh fill event")
        elif kind == "replayed_fill":
            for name, order_id in terminals.items():
                recorded = snapshots[name]["fills"]
                if not recorded:
                    continue
                outcome = _fill(
                    supabase,
                    session,
                    _order_by_id(supabase, order_id),
                    config=config,
                    quantity=str(recorded[0]["quantity"]),
                    price=str(recorded[0]["price"]),
                    fill_event_id=str(recorded[0]["fill_event_id"]),
                )
                # The terminal guard sits AHEAD of the duplicate guard, so a terminal order
                # answers FILL_TERMINAL for its own event rather than FILL_DUPLICATE. Both are
                # no-ops; which one is reported is Requirement 16.3's ordering.
                assert outcome.outcome == sim.FILL_TERMINAL, (
                    f"P-18: replaying the {name} order's own fill returned {outcome.outcome}. "
                    f"Aftermath: {aftermath!r}"
                )
                recorder.mark("subsequent_replayed_fill")
            check_all("a replay of the order's own fill")
        elif kind == "candle":
            outcomes = candle()
            touched = [
                str(outcome.order["id"])
                for outcome in outcomes
                if str(outcome.order["id"]) in {str(v) for v in terminals.values()}
            ]
            assert touched == [], (
                "P-18 (Requirement 16.3): check_resting_orders selected a terminal order "
                f"{touched}; legacy_status maps only ACCEPTED and PARTIALLY_FILLED to OPEN. "
                f"Aftermath: {aftermath!r}"
            )
            recorder.mark("subsequent_candle")
            check_all("a triggering market event")
        else:
            fresh["n"] += 1
            outcome = _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=_intent(
                    quantity=aftermath.market_quantity,
                    idempotency_key=f"key-later-{fresh['n']}",
                ),
                latest_event=_event(
                    close=aftermath.market_close,
                    source_event_id=f"evt-later-{fresh['n']}",
                    at=NOW + timedelta(hours=1, minutes=fresh["n"]),
                ),
                sleep=sleeps,
            )
            assert outcome.accepted, f"the later market order was refused: {outcome!r}"
            recorder.mark("subsequent_new_order")
            check_all("a further accepted order and its fill")

    for kind in SUBSEQUENT_EVENTS:
        apply_subsequent(kind)
    for kind in aftermath.tail:
        apply_subsequent(kind)

    assert sleeps.delays == [], (
        f"a retry backoff was slept, so a statement conflicted: {sleeps.delays}"
    )


def test_p18_terminal_paper_order_states_are_final(request: Any) -> None:
    """A ``FILLED``, ``CANCELLED`` or ``REJECTED`` order is not moved by anything that follows.

    For all orders reaching ``FILLED``, ``CANCELLED`` or ``REJECTED`` and all generated sequences
    of subsequent events - a fresh fill event, a replay of the order's own fill, a triggering
    market event, and further accepted orders on the same account - no subsequent event changes the
    order's state, its filled quantity or its recorded fees, where the fees are read both from
    ``paper_orders.fee_minor`` and as the exact sum of ``paper_fills.fee_minor``. The whole
    ``paper_orders`` row and the whole set of the order's ``paper_fills`` rows are compared as
    well, so nothing else moves either.

    The oracle is the snapshot taken before the subsequent events; quantities are compared as exact
    ``Decimal`` and fees as exact ``int`` minor units, with no tolerance.

    **Validates: Requirements 16.3**
    """
    recorder = _Recorder("P-18", P18_FLOORS, P18_LABELS)

    @PROPERTY_SETTINGS
    @given(aftermath=terminal_order_aftermaths())
    def check(aftermath: _Aftermath) -> None:
        recorder.start()
        recorder.mark("examples")
        if aftermath.tail:
            recorder.mark("tail_beyond_the_spine")
        _run_terminal_aftermath(aftermath, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-19 (task 25.9) - ILLEGAL TRANSITION REJECTION
# ══════════════════════════════════════════════════════════════════════════
#
# WHICH OF THE TWENTY-SEVEN ILLEGAL PAIRS ``paper_simulator`` CAN BE MADE TO ATTEMPT
# ---------------------------------------------------------------------------------
# The module writes a paper order state in exactly two places, and each fixes the TARGET:
#
# * ``apply_fill`` targets ``PARTIALLY_FILLED`` or ``FILLED``, according to whether the cumulative
#   filled quantity reaches the order quantity (Requirements 16.13, 16.14). The origin is whatever
#   the order was re-read at inside the attempt, so all six are expressible by seeding the order
#   there first.
# * ``submit_intent`` targets ``ACCEPTED`` or ``REJECTED``, always on an order it INSERTed at
#   ``CREATED`` in the same attempt. To make the origin anything else, a concurrent writer has to
#   move that row between the INSERT and the UPDATE - which is precisely the race Requirement
#   16.4's guard exists for, and which ``FakeSupabase.before_update`` injects.
#
# That is 8 + 9 = seventeen of the twenty-seven. The remaining ten are the six pairs targeting
# ``CREATED`` and the four illegal pairs targeting ``CANCELLED``: the module never UPDATEs an order
# TO ``CREATED`` (it only ever INSERTs there) and has no cancel path at all. Those ten are P-49's,
# which issues the same attempt directly to the Persistence_Layer. This property does not assume
# they are unattemptable - it asserts, over every statement of every example, that no
# ``paper_orders`` UPDATE the simulator issued carried either target.

FILL_TARGETS: Tuple[str, ...] = ("PARTIALLY_FILLED", "FILLED")
SUBMIT_TARGETS: Tuple[str, ...] = ("ACCEPTED", "REJECTED")

#: Every ``(origin, target)`` ``apply_fill`` can be made to attempt. Twelve: six origins, and the
#: target chosen by whether the fill quantity closes the order or leaves a remainder.
FILL_ATTEMPTS: Tuple[Tuple[str, str], ...] = tuple(
    (origin, target) for origin in REQUIREMENT_16_1_STATES for target in FILL_TARGETS
)
ILLEGAL_FILL_ATTEMPTS: Tuple[Tuple[str, str], ...] = tuple(
    pair for pair in FILL_ATTEMPTS if pair in ILLEGAL_PAIRS
)
LEGAL_FILL_ATTEMPTS: Tuple[Tuple[str, str], ...] = tuple(
    pair for pair in FILL_ATTEMPTS if pair in PERMITTED_PAIRS
)

#: Every illegal ``(origin, target)`` ``submit_intent`` can be made to attempt: its two targets,
#: from any origin other than ``CREATED`` - where ``CREATED`` is excluded because that is the
#: origin the module legitimately writes from, not an illegal one.
ILLEGAL_SUBMIT_ATTEMPTS: Tuple[Tuple[str, str], ...] = tuple(
    (origin, target)
    for origin in REQUIREMENT_16_1_STATES
    for target in SUBMIT_TARGETS
    if origin != "CREATED" and (origin, target) in ILLEGAL_PAIRS
)

ATTEMPTABLE_ILLEGAL: FrozenSet[Tuple[str, str]] = frozenset(
    ILLEGAL_FILL_ATTEMPTS
) | frozenset(ILLEGAL_SUBMIT_ATTEMPTS)

#: The ten pairs no ``paper_simulator`` path can attempt, because it never targets either state.
UNATTEMPTABLE_ILLEGAL: FrozenSet[Tuple[str, str]] = ILLEGAL_PAIRS - ATTEMPTABLE_ILLEGAL

#: The four targets the module does write. Gap 2's claim, asserted per example.
SIMULATOR_UPDATE_TARGETS: FrozenSet[str] = frozenset(
    {"ACCEPTED", "REJECTED", "PARTIALLY_FILLED", "FILLED"}
)

#: ``(order_quantity, half)`` - the fill that closes the order and the fill that leaves a
#: remainder, so the target ``apply_fill`` computes is chosen rather than hoped for. Every half is
#: exact at the symbol's 8-decimal quantity precision.
QUANTITY_HALVES: Tuple[Tuple[str, str], ...] = (
    ("1", "0.5"),
    ("2", "1"),
    ("0.8", "0.4"),
    ("4", "2"),
)


class _TransitionCase:
    """The drawn values one example applies to all twenty-one transition attempts."""

    __slots__ = (
        "quantities",
        "fill_price",
        "fee_rate",
        "slippage_rate",
        "bad_intent",
        "market_quantity",
        "market_close",
    )

    def __init__(
        self,
        quantities: Tuple[str, str],
        fill_price: str,
        fee_rate: str,
        slippage_rate: str,
        bad_intent: Tuple[str, Dict[str, Any]],
        market_quantity: str,
        market_close: str,
    ) -> None:
        self.quantities = quantities
        self.fill_price = fill_price
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.bad_intent = bad_intent
        self.market_quantity = market_quantity
        self.market_close = market_close

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_TransitionCase(quantities={self.quantities}, price={self.fill_price!r}, "
            f"fee={self.fee_rate!r}, slip={self.slippage_rate!r}, bad={self.bad_intent[0]!r}, "
            f"market={self.market_quantity!r}@{self.market_close!r})"
        )


@st.composite
def transition_cases(draw: Any) -> _TransitionCase:
    """The values every one of the twenty-one attempts is made with.

    Every attemptable pair is exercised in **every** example rather than one drawn pair per
    example, because P-19 is a claim about the whole complement of the permitted set and a run that
    sampled pairs would leave some of them unattempted in most runs - the floors in
    :data:`P19_FLOORS` are the example count for each of the seventeen for exactly that reason.
    What is drawn is the material: the order quantity and the fill that closes it, the fill price,
    the recorded rates, and the two intents the ``submit_intent`` route is driven with.

    The four **legal** fill pairs are attempted alongside, and asserted to be APPLIED. Without them
    the property would be satisfied by an implementation that refused every transition.
    """
    return _TransitionCase(
        quantities=draw(st.sampled_from(QUANTITY_HALVES)),
        fill_price=draw(st.sampled_from(LATER_FILL_PRICES)),
        fee_rate=draw(st.sampled_from(FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        bad_intent=draw(st.sampled_from(PERSISTED_REJECTION_INTENTS)),
        market_quantity=draw(st.sampled_from(MARKET_QUANTITIES)),
        market_close=draw(st.sampled_from(MARKET_CLOSES)),
    )


def _order_at(supabase: Any, account_id: str, origin: str, quantity: str) -> Dict[str, Any]:
    """One ``paper_orders`` row stored at ``origin``, reached by permitted transitions only.

    ``CREATED`` is the INSERT itself; every other origin is reached from it through
    ``paper_repository.update_order`` under a guard, so the premise of an illegal-transition test is
    itself never an illegal transition.
    """
    if origin == "CREATED":
        return _created_order(supabase, account_id, quantity=quantity)
    return _accepted_market_order(
        supabase, account_id, quantity=quantity, state=PaperOrderState(origin)
    )


def _state_racer(origin: str) -> Any:
    """A concurrent writer that moves every ``CREATED`` order to ``origin`` before an UPDATE lands.

    Injected through ``FakeSupabase.before_update``, which fires *before* the update's rows are
    matched - so ``submit_intent``'s ``.eq("order_state", "CREATED")`` predicate matches zero rows
    and ``paper_repository.update_order`` raises rather than overwriting a state it never read.
    That is Requirement 16.4 exercised through the simulator: the write that would have set the
    order to ``ACCEPTED`` or ``REJECTED`` from ``origin`` is refused and the stored state stands.
    """

    def racer(client: Any, query: Any) -> None:
        if query.table_name != repo.ORDERS_TABLE or query.op != "update":
            return
        for row in client.orders:
            if str(row.get("order_state")) == "CREATED":
                row["order_state"] = origin
                row["legacy_status"] = LEGACY_STATUS_FOR_STATE[PaperOrderState(origin)]

    return racer


def _updates_since(supabase: Any, mark: int) -> List[str]:
    """Every ``order_state`` a ``paper_orders`` UPDATE carried after ``mark``.

    ``mark`` matters: the seeding in :func:`_order_at` legitimately targets ``CANCELLED`` and the
    terminal states through the Persistence_Layer, and gap 2's claim is about what the SIMULATOR
    issues.
    """
    return [
        str((query.payload or {}).get("order_state"))
        for query in supabase.statements[mark:]
        if query.table_name == repo.ORDERS_TABLE and query.op == "update"
    ]


P19_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "refused_by_the_terminal_guard": EXAMPLES * len(REQUIREMENT_16_3_TERMINAL) * 2,
    "refused_before_any_write": EXAMPLES * 2,
    "refused_by_the_state_guard": EXAMPLES * len(ILLEGAL_SUBMIT_ATTEMPTS),
    "applied_legal_transition": EXAMPLES * len(LEGAL_FILL_ATTEMPTS),
    **{f"illegal_{origin}->{target}": EXAMPLES for origin, target in sorted(ATTEMPTABLE_ILLEGAL)},
    **{f"legal_{origin}->{target}": EXAMPLES for origin, target in sorted(LEGAL_FILL_ATTEMPTS)},
}

P19_LABELS: Dict[str, str] = {
    "refused_by_the_terminal_guard": "a fill against a terminal order was a no-op",
    "refused_before_any_write": "an illegal transition was refused before any statement",
    "refused_by_the_state_guard": "a moved order made the guarded UPDATE match no row",
    "applied_legal_transition": "a permitted transition was applied",
}


def _attempt_fill_transition(
    origin: str, target: str, case: _TransitionCase, recorder: _Recorder
) -> None:
    """Drive ``apply_fill`` at an order stored at ``origin`` so that it must target ``target``."""
    ordered, half = case.quantities
    quantity = ordered if target == "FILLED" else half
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(case.fee_rate), slippage_rate=Decimal(case.slippage_rate)
    )
    order = _order_at(supabase, account_id, origin, ordered)
    before = _snapshot(supabase)
    mark = _mark(supabase)

    legal = (origin, target) in PERMITTED_PAIRS
    if legal:
        outcome = _fill(
            supabase,
            session,
            order,
            config=config,
            quantity=quantity,
            price=case.fill_price,
            fill_event_id=f"fill-{origin}-{target}",
        )
        assert outcome.applied is True, (
            f"P-19 control: {origin} -> {target} is one of Requirement 16.2's nine permitted "
            f"transitions and must be APPLIED, but apply_fill answered {outcome.outcome}. "
            f"Case: {case!r}"
        )
        assert str(_order_by_id(supabase, order["id"])["order_state"]) == target, (
            f"P-19 control: {origin} -> {target} was applied but the stored state is "
            f"{_order_by_id(supabase, order['id'])['order_state']}. Case: {case!r}"
        )
        recorder.mark(f"legal_{origin}->{target}")
        recorder.mark("applied_legal_transition")
    elif origin in REQUIREMENT_16_3_TERMINAL:
        outcome = _fill(
            supabase,
            session,
            order,
            config=config,
            quantity=quantity,
            price=case.fill_price,
            fill_event_id=f"fill-{origin}-{target}",
        )
        assert outcome.outcome == sim.FILL_TERMINAL, (
            f"P-19 (Requirements 16.2, 16.3): {origin} -> {target} is not permitted and {origin} "
            f"is terminal, so the fill must be a no-op; apply_fill answered {outcome.outcome}. "
            f"Case: {case!r}"
        )
        assert outcome.applied is False
        recorder.mark("refused_by_the_terminal_guard")
        recorder.mark(f"illegal_{origin}->{target}")
    else:
        # ``CREATED`` is the one non-terminal origin from which neither fill target is reachable.
        with pytest.raises(sim.PaperOrderInvalid) as caught:
            _fill(
                supabase,
                session,
                order,
                config=config,
                quantity=quantity,
                price=case.fill_price,
                fill_event_id=f"fill-{origin}-{target}",
            )
        details = caught.value.details
        assert details["validation"] == "ILLEGAL_TRANSITION", (
            f"P-19 (Requirement 16.4): the refusal must name the rejected transition; got "
            f"{details!r}. Case: {case!r}"
        )
        assert (details["from"], details["to"]) == (origin, target), (
            f"P-19 (Requirement 16.4): the error names {details.get('from')} -> "
            f"{details.get('to')}, not {origin} -> {target}. Case: {case!r}"
        )
        recorder.mark("refused_before_any_write")
        recorder.mark(f"illegal_{origin}->{target}")

    if not legal:
        stored = str(_order_by_id(supabase, order["id"])["order_state"])
        assert stored == origin, (
            f"P-19 (Requirement 16.4): the attempted {origin} -> {target} left the stored state at "
            f"{stored}; it must leave it unchanged. Case: {case!r}"
        )
        assert _snapshot(supabase) == before, (
            f"P-19 (Requirements 16.4, 16.10): the refused {origin} -> {target} changed stored "
            f"rows. A rejected write leaves NO partial write behind. Case: {case!r}"
        )
        assert _wrote_since(supabase, mark) == [], (
            f"P-19: the refused {origin} -> {target} issued writes "
            f"{_wrote_since(supabase, mark)}. Case: {case!r}"
        )

    unexpected = sorted(set(_updates_since(supabase, mark)) - SIMULATOR_UPDATE_TARGETS)
    assert unexpected == [], (
        f"P-19 gap 2: paper_simulator UPDATEd an order to {unexpected}, which it is not supposed "
        f"to target at all - so the ten pairs this property records as unattemptable are "
        f"attemptable after all and must be exercised. Case: {case!r}"
    )


def _attempt_submit_transition(
    origin: str, target: str, case: _TransitionCase, recorder: _Recorder
) -> None:
    """Drive ``submit_intent`` so its guarded UPDATE would set ``origin -> target``."""
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(case.fee_rate), slippage_rate=Decimal(case.slippage_rate)
    )
    supabase.before_update = _state_racer(origin)
    before = _snapshot(supabase)
    mark = _mark(supabase)

    if target == "REJECTED":
        intent = _intent(**case.bad_intent[1])
        latest = None
    else:
        intent = _intent(quantity=case.market_quantity)
        latest = _event(close=case.market_close, source_event_id="evt-submit")

    with pytest.raises(sim.PaperConcurrencyExhausted) as caught:
        _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=intent,
            latest_event=latest,
            sleep=_Sleeps(),
        )
    assert caught.value.attempts == sim.RETRY_ATTEMPTS, (
        f"P-19: the refused {origin} -> {target} did not use Requirement 16.10's three attempts: "
        f"{caught.value.details!r}. Case: {case!r}"
    )

    states = {str(row["order_state"]) for row in supabase.orders}
    # Every order row still stands where the concurrent writer left it, so the write that would
    # have set ``origin -> target`` did not land. ``origin == target`` for the two self-illegal
    # pairs (``ACCEPTED -> ACCEPTED`` and ``REJECTED -> REJECTED``), which is why the claim is
    # stated as "the stored state is unchanged" and not as "the target is absent" - the latter
    # would be unsatisfiable for exactly those two.
    assert states == {origin}, (
        f"P-19 (Requirement 16.4): after the refused {origin} -> {target} the stored order states "
        f"are {sorted(states)}; every one must still be {origin}. Case: {case!r}"
    )

    after = _snapshot(supabase)
    for table in ("accounts", "fills", "positions", "balance_events", "trades", "equity_snapshots"):
        assert after[table] == before[table], (
            f"P-19 (Requirement 16.4): the refused {origin} -> {target} moved {table}; a rejected "
            f"transition changes no balance, position or equity value. Case: {case!r}"
        )

    unexpected = sorted(set(_updates_since(supabase, mark)) - SIMULATOR_UPDATE_TARGETS)
    assert unexpected == [], (
        f"P-19 gap 2: paper_simulator UPDATEd an order to {unexpected}. Case: {case!r}"
    )

    recorder.mark("refused_by_the_state_guard")
    recorder.mark(f"illegal_{origin}->{target}")


def test_p19_illegal_paper_order_transition_leaves_state(request: Any) -> None:
    """Every ordered pair outside Requirement 16.2's nine is refused, and the stored state stands.

    For all seventeen of the twenty-seven illegal ordered pairs that ``paper_simulator`` can be
    made to attempt - eight through ``apply_fill``, nine through ``submit_intent`` racing a
    concurrent writer - the attempt is rejected, the stored Paper_Order_State is unchanged, and no
    balance, position, fill or equity row moves. The four permitted pairs the same two routes reach
    are attempted alongside and asserted to be **applied**, so the property cannot be satisfied by
    an implementation that refuses everything.

    The oracle is :data:`ILLEGAL_PAIRS`, the set complement of :data:`REQUIREMENT_16_2_TRANSITIONS`
    over Requirement 16.1's six states, computed in this file. Distinct from P-49, which issues the
    same attempt directly to the Persistence_Layer; this one goes through ``paper_simulator``.

    The remaining ten pairs target ``CREATED`` or ``CANCELLED``, which the module never UPDATEs an
    order to. That is asserted rather than assumed, on every statement of every example.

    A PRODUCTION DEFECT THIS PROPERTY FOUND
    ---------------------------------------
    ``apply_fill`` validated ``state -> target`` only at its fifth write, inside
    ``paper_repository.update_order``. A fill against an order at ``CREATED`` therefore wrote the
    fill row, moved the account balance, appended the ledger row and upserted the position, and
    *then* refused the transition - leaving the stored state unchanged, as Requirement 16.4 asks,
    but leaving a partial write that Requirement 16.10 forbids and that this transport cannot roll
    back. The transition is now checked before the first statement, next to the over-fill guard.

    **Validates: Requirements 16.2, 16.4**
    """
    recorder = _Recorder("P-19", P19_FLOORS, P19_LABELS)

    @PROPERTY_SETTINGS
    @given(case=transition_cases())
    def check(case: _TransitionCase) -> None:
        recorder.start()
        recorder.mark("examples")
        for origin, target in FILL_ATTEMPTS:
            _attempt_fill_transition(origin, target, case, recorder)
        for origin, target in ILLEGAL_SUBMIT_ATTEMPTS:
            _attempt_submit_transition(origin, target, case, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-20 (task 25.10) - FILL ACCUMULATION
# ══════════════════════════════════════════════════════════════════════════

#: One unit at the symbol's recorded quantity precision - the smallest quantity ``paper_fills`` can
#: express for this market. The near-miss and the closing fill are both this size, because a
#: comparison that is going to be wrong is wrong at the last representable digit and nowhere else.
ONE_MINOR_QUANTITY = Decimal("0.00000001")


class _FillProgramme:
    """One generated fill programme: how each order is filled, and by how much."""

    __slots__ = (
        "fill_triple",
        "limit_price",
        "low_offsets",
        "capped_fills",
        "market_quantity",
        "market_close",
        "bad_intent",
        "fee_rate",
        "slippage_rate",
        "extra_idle_orders",
    )

    def __init__(
        self,
        fill_triple: Tuple[str, str, str],
        limit_price: str,
        low_offsets: Tuple[str, ...],
        capped_fills: int,
        market_quantity: str,
        market_close: str,
        bad_intent: Tuple[str, Dict[str, Any]],
        fee_rate: str,
        slippage_rate: str,
        extra_idle_orders: int,
    ) -> None:
        self.fill_triple = fill_triple
        self.limit_price = limit_price
        self.low_offsets = low_offsets
        self.capped_fills = int(capped_fills)
        self.market_quantity = market_quantity
        self.market_close = market_close
        self.bad_intent = bad_intent
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.extra_idle_orders = int(extra_idle_orders)

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_FillProgramme(triple={self.fill_triple}, limit={self.limit_price!r}, "
            f"capped_fills={self.capped_fills}, market={self.market_quantity!r}@"
            f"{self.market_close!r}, bad={self.bad_intent[0]!r}, fee={self.fee_rate!r}, "
            f"slip={self.slippage_rate!r}, idle={self.extra_idle_orders})"
        )


@st.composite
def fill_programmes(draw: Any) -> _FillProgramme:
    """A programme that puts a fill sum on both sides of the order quantity, and exactly on it.

    Six orders are forced into every example, because P-20's "if and only if" has two directions
    and a run that only ever filled orders completely would establish one of them:

    * a **market** order, whose single fill equals its quantity -> ``FILLED``;
    * a **limit** order filled to one minor unit short of its quantity and then closed by exactly
      that one unit -> ``PARTIALLY_FILLED`` at the near miss, ``FILLED`` at the equality. Between
      the two an over-fill of two minor units is refused and the first fill is replayed, so the sum
      is asserted to be unmoved by both;
    * a **limit** order left after one to three capped fills -> ``PARTIALLY_FILLED``, sum strictly
      below the quantity;
    * a **limit** order no candle can reach -> sum exactly zero, ``ACCEPTED``;
    * a **limit** order with one capped fill and then cancelled -> a TERMINAL order whose sum is
      strictly below its quantity, which is the case a naive "terminal implies filled" reading gets
      wrong;
    * a **rejected** order with a strictly positive quantity -> sum zero, ``REJECTED``.

    ``capped_fills`` is drawn from 1..3 rather than fixed, so the partially filled order's sum is
    one, two or three quarters of its quantity; ``extra_idle_orders`` appends further zero-fill
    orders. Everything numeric is drawn.
    """
    return _FillProgramme(
        fill_triple=draw(st.sampled_from(FILL_TRIPLES)),
        limit_price=draw(st.sampled_from(LIMIT_PRICES)),
        low_offsets=tuple(
            draw(st.lists(st.sampled_from(LOW_OFFSETS), min_size=4, max_size=4))
        ),
        capped_fills=draw(st.integers(min_value=1, max_value=3)),
        market_quantity=draw(st.sampled_from(MARKET_QUANTITIES)),
        market_close=draw(st.sampled_from(MARKET_CLOSES)),
        bad_intent=draw(st.sampled_from(PERSISTED_REJECTION_INTENTS)),
        fee_rate=draw(st.sampled_from(FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        extra_idle_orders=draw(st.integers(min_value=1, max_value=2)),
    )


P20_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "orders": EXAMPLES * 6,
    "fill_sum_equals_quantity": EXAMPLES * 2,
    "fill_sum_below_quantity_and_non_zero": EXAMPLES * 2,
    "fill_sum_zero": EXAMPLES * 2,
    "exact_equality_at_the_last_minor_unit": EXAMPLES,
    "near_miss_one_minor_unit_short": EXAMPLES,
    "over_fill_refused": EXAMPLES,
    "duplicate_fill_ignored": EXAMPLES,
    "terminal_non_filled_carries_partial_fills": EXAMPLES,
    "rejected_order_with_a_positive_quantity": EXAMPLES,
    "accepted_order_with_no_fill": EXAMPLES,
    "tail_beyond_the_spine": EXAMPLES,
}

P20_LABELS: Dict[str, str] = {
    "exact_equality_at_the_last_minor_unit": (
        "a fill sum reached the order quantity at the last representable digit"
    ),
    "near_miss_one_minor_unit_short": "a fill sum stopped one minor unit short of the quantity",
    "over_fill_refused": "an over-fill of one minor unit was refused",
    "duplicate_fill_ignored": "a replayed fill left the sum unmoved",
    "terminal_non_filled_carries_partial_fills": (
        "a CANCELLED order carried a sum strictly below its quantity"
    ),
    "fill_sum_zero": "an order carried no fill at all",
    "tail_beyond_the_spine": "the programme carried orders beyond the forced six",
}


def _assert_fill_sum_is_bounded_and_filled_iff_equal(
    supabase: Any, programme: Any, recorder: Optional[_Recorder] = None
) -> None:
    """P-20, over every stored order, with the sum recomputed from ``paper_fills``."""
    rows = _order_rows(supabase)
    assert rows, f"the programme persisted no order: {programme!r}"

    for row in rows:
        order_id = str(row["id"])
        ordered = Decimal(str(row["quantity"]))
        fill_sum = _fill_sum_from_rows(supabase, order_id)
        state = str(row["order_state"])

        assert fill_sum <= ordered, (
            f"P-20 (Requirement 16.7): order {order_id} has fills summing to {fill_sum}, which "
            f"exceeds its quantity {ordered}. The sum of all fills for one order is applied to at "
            f"most the order's quantity. Programme: {programme!r}"
        )
        assert (state == "FILLED") == (fill_sum == ordered), (
            f"P-20 (Requirements 16.13, 16.14): order {order_id} is {state} and its fills sum to "
            f"{fill_sum} of {ordered}. The state is FILLED if and only if that sum equals the "
            f"order quantity - a sum below it is PARTIALLY_FILLED and an equal sum is FILLED. "
            f"Programme: {programme!r}"
        )
        assert Decimal(str(row["filled_quantity"])) == fill_sum, (
            f"P-20: order {order_id} records filled_quantity "
            f"{Decimal(str(row['filled_quantity']))} while its paper_fills rows sum to {fill_sum}. "
            f"The column is a projection of the rows, so a drift between the two is a failure "
            f"whichever of them is right. Programme: {programme!r}"
        )

        if recorder is None:
            continue
        recorder.mark("orders")
        if fill_sum == ordered:
            recorder.mark("fill_sum_equals_quantity")
        elif fill_sum == 0:
            recorder.mark("fill_sum_zero")
        else:
            recorder.mark("fill_sum_below_quantity_and_non_zero")
        if state in REQUIREMENT_16_3_TERMINAL and state != "FILLED" and fill_sum > 0:
            recorder.mark("terminal_non_filled_carries_partial_fills")
        if state == "REJECTED" and ordered > 0 and fill_sum == 0:
            recorder.mark("rejected_order_with_a_positive_quantity")
        if state == "ACCEPTED" and fill_sum == 0:
            recorder.mark("accepted_order_with_no_fill")


def _run_fill_programme(programme: _FillProgramme, recorder: _Recorder) -> None:
    """Drive one fill programme and assert P-20 after every step that moves a sum."""
    ordered_text, participation, volume = programme.fill_triple
    ordered = Decimal(ordered_text)
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(programme.fee_rate),
        slippage_rate=Decimal(programme.slippage_rate),
        participation_rate=Decimal(participation),
    )
    sleeps = _Sleeps()
    counter = {"event": 0}

    def resting(name: str, *, limit: str) -> Any:
        outcome = _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=_intent(
                order_type="limit",
                quantity=ordered_text,
                limit_price=limit,
                idempotency_key=f"key-{name}",
            ),
            sleep=sleeps,
        )
        assert outcome.accepted, f"{name} was not accepted: {outcome.rejection_reason}"
        return outcome.order["id"]

    def candle() -> None:
        counter["event"] += 1
        index = counter["event"]
        _run_coroutine(
            sim.check_resting_orders(
                supabase,
                session,
                _candle(
                    limit_price=programme.limit_price,
                    low_offset=programme.low_offsets[min(index - 1, 3)],
                    volume=volume,
                    index=index,
                ),
                config=config,
                account_id=account_id,
                sleep=sleeps,
            )
        )

    # ── phase A: the candle-driven orders. Everything triggerable is submitted first, so the
    #    direct-fill order of phase B cannot be touched by a candle. ──
    partial_id = resting("partial", limit=programme.limit_price)
    cancelled_id = resting("cancelled", limit=programme.limit_price)
    idle_id = resting("idle", limit=UNTRIGGERABLE_LIMIT)
    candle()
    _move_order(supabase, _order_by_id(supabase, cancelled_id), PaperOrderState.CANCELLED)
    for _extra in range(programme.capped_fills - 1):
        candle()
    _assert_fill_sum_is_bounded_and_filled_iff_equal(supabase, programme)

    # ── phase B: the boundary. One minor unit short, an over-fill, a replay, then exact equality.
    exact_id = resting("exact", limit=programme.limit_price)
    near = ordered - ONE_MINOR_QUANTITY
    first = _fill(
        supabase,
        session,
        _order_by_id(supabase, exact_id),
        config=config,
        quantity=str(near),
        price=programme.limit_price,
        fill_event_id="exact-1",
    )
    assert first.applied is True, f"the near-miss fill was not applied: {first.outcome}"
    assert str(_order_by_id(supabase, exact_id)["order_state"]) == "PARTIALLY_FILLED", (
        f"P-20 (Requirement 16.13): a sum of {near} against a quantity of {ordered} - one minor "
        f"unit short - must be PARTIALLY_FILLED, not "
        f"{_order_by_id(supabase, exact_id)['order_state']}. Programme: {programme!r}"
    )
    assert _fill_sum_from_rows(supabase, exact_id) == near
    recorder.mark("near_miss_one_minor_unit_short")
    _assert_fill_sum_is_bounded_and_filled_iff_equal(supabase, programme)

    sum_before_refusals = _fill_sum_from_rows(supabase, exact_id)
    with pytest.raises(sim.PaperOverFill):
        _fill(
            supabase,
            session,
            _order_by_id(supabase, exact_id),
            config=config,
            quantity=str(ONE_MINOR_QUANTITY * 2),
            price=programme.limit_price,
            fill_event_id="exact-over",
        )
    assert _fill_sum_from_rows(supabase, exact_id) == sum_before_refusals, (
        f"P-20 (Requirement 16.7): a refused over-fill moved the sum. Programme: {programme!r}"
    )
    recorder.mark("over_fill_refused")

    replayed = _fill(
        supabase,
        session,
        _order_by_id(supabase, exact_id),
        config=config,
        quantity=str(near),
        price=programme.limit_price,
        fill_event_id="exact-1",
    )
    assert replayed.outcome == sim.FILL_DUPLICATE, (
        f"replaying exact-1 answered {replayed.outcome}. Programme: {programme!r}"
    )
    assert _fill_sum_from_rows(supabase, exact_id) == sum_before_refusals, (
        f"P-20: a replayed fill moved the sum. Programme: {programme!r}"
    )
    recorder.mark("duplicate_fill_ignored")
    _assert_fill_sum_is_bounded_and_filled_iff_equal(supabase, programme)

    closing = _fill(
        supabase,
        session,
        _order_by_id(supabase, exact_id),
        config=config,
        quantity=str(ONE_MINOR_QUANTITY),
        price=programme.limit_price,
        fill_event_id="exact-2",
    )
    assert closing.applied is True, f"the closing fill was not applied: {closing.outcome}"
    assert _fill_sum_from_rows(supabase, exact_id) == ordered, (
        f"P-20: the closing minor unit left the sum at "
        f"{_fill_sum_from_rows(supabase, exact_id)} rather than {ordered}. "
        f"Programme: {programme!r}"
    )
    assert str(_order_by_id(supabase, exact_id)["order_state"]) == "FILLED", (
        f"P-20 (Requirement 16.14): a sum of exactly {ordered} must be FILLED, not "
        f"{_order_by_id(supabase, exact_id)['order_state']}. Programme: {programme!r}"
    )
    recorder.mark("exact_equality_at_the_last_minor_unit")

    # ── phase C: a market order, whose single fill equals its quantity ──
    market = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(quantity=programme.market_quantity, idempotency_key="key-market"),
        latest_event=_event(
            close=programme.market_close, source_event_id="evt-market", at=NOW + timedelta(hours=1)
        ),
        sleep=sleeps,
    )
    assert market.fill is not None and market.fill.applied is True

    # ── phase D: a rejected order, whose quantity is positive and whose sum is zero ──
    _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(**programme.bad_intent[1]),
        sleep=sleeps,
    )

    # ── the tail: further orders no candle can reach ──
    for index in range(programme.extra_idle_orders):
        resting(f"idle-{index}", limit=UNTRIGGERABLE_LIMIT)
    recorder.mark("tail_beyond_the_spine")

    # The premise the census rests on, asserted rather than assumed.
    assert 0 < _fill_sum_from_rows(supabase, partial_id) < ordered, (
        f"the partially filled order's sum is {_fill_sum_from_rows(supabase, partial_id)}, not "
        f"strictly between zero and {ordered}. Programme: {programme!r}"
    )
    assert 0 < _fill_sum_from_rows(supabase, cancelled_id) < ordered, (
        f"the cancelled order carries no partial fill. Programme: {programme!r}"
    )
    assert _fill_sum_from_rows(supabase, idle_id) == 0
    assert sleeps.delays == [], f"a retry backoff was slept: {sleeps.delays}"

    _assert_fill_sum_is_bounded_and_filled_iff_equal(supabase, programme, recorder)


def test_p20_fill_sum_is_bounded_and_filled_iff_equal(request: Any) -> None:
    """A fill sum never exceeds the order quantity, and ``FILLED`` means exactly that it equals it.

    For all generated fill programmes and every order they persist: the sum of the order's fill
    quantities is at most the order quantity, and the order's state is ``FILLED`` if and only if
    that sum equals it.

    The oracle is :func:`_fill_sum_from_rows`, which recomputes the sum over the ``paper_fills``
    rows. ``paper_orders.filled_quantity`` is **not** read to build it - it is compared against it,
    so a drift between the column and the rows is a failure rather than something the oracle
    inherits. Every comparison is exact ``Decimal`` equality; the boundary is exercised at the last
    representable digit of the symbol's recorded quantity precision, one minor unit short of the
    quantity and then exactly on it, with a refused over-fill and a replayed fill in between.

    **Validates: Requirements 16.7, 16.13, 16.14**
    """
    recorder = _Recorder("P-20", P20_FLOORS, P20_LABELS)

    @PROPERTY_SETTINGS
    @given(programme=fill_programmes())
    def check(programme: _FillProgramme) -> None:
        recorder.start()
        recorder.mark("examples")
        _run_fill_programme(programme, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-24 (task 25.14) - THE REJECTION CONDITIONS
# ══════════════════════════════════════════════════════════════════════════

#: The tables Requirement 16.5's "SHALL make no change to the Paper_Account's balances or
#: positions" and Requirement 16.6's "SHALL lock no funds" are claims about, plus the equity series
#: Requirement 18.11 would otherwise extend. ``orders`` is excluded because a persisted rejection
#: writes exactly one row there - that is the outcome, not a side effect.
UNTOUCHED_TABLES: Tuple[str, ...] = (
    "accounts",
    "fills",
    "positions",
    "balance_events",
    "trades",
    "equity_snapshots",
)

PERSISTED = "persisted"
COLUMN_REFUSAL = "column"


class _RejectionCase:
    """One of P-24's six conditions, in one of the two branches Requirement 16.5 has.

    ``reason`` and ``branch`` are **declared** by the case rather than computed by asking the module.
    :func:`_oracle_rejection` and :func:`_oracle_column_refusal` then verify the declaration against
    the requirement text and 009's own CHECK clauses before the module is called at all, so a
    mis-built intent is reported as a broken generator instead of quietly asserting the wrong reason.
    """

    __slots__ = ("reason", "branch", "constraint", "overrides", "narrowing", "capital")

    def __init__(
        self,
        reason: str,
        branch: str,
        constraint: Optional[str],
        overrides: Dict[str, Any],
        *,
        narrowing: Optional[Dict[str, Any]] = None,
        capital: Decimal = RICH,
    ) -> None:
        self.reason = reason
        self.branch = branch
        self.constraint = constraint
        self.overrides = dict(overrides)
        self.narrowing = dict(narrowing or {})
        self.capital = capital

    @property
    def label(self) -> str:
        return f"{self.reason}/{self.branch}/{sorted(self.overrides.items())}"

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_RejectionCase({self.reason!r}, {self.branch!r}, constraint={self.constraint!r}, "
            f"overrides={self.overrides!r}, narrowing={self.narrowing!r}, "
            f"capital={self.capital})"
        )


#: The six conditions task 25.14 enumerates, each in every branch it has. Fourteen cases: three
#: spellings of a non-positive quantity, two symbols outside the validated set, two non-positive
#: limit prices, two unsupported order types the column also refuses plus one it permits, two
#: unsupported sides the column also refuses plus one it permits, and required funds beyond the
#: available balance.
#:
#: The two "persisted" order-type and side cases are the branch in which Requirement 16.5 is
#: satisfied in full: a session whose RECORDED supported set is narrower than the column's CHECK
#: rejects a value the column can still store, so the ``REJECTED`` order exists and names the check.
P24_CASES: Tuple[_RejectionCase, ...] = (
    _RejectionCase(
        "QUANTITY_NOT_POSITIVE", COLUMN_REFUSAL, "chk_paper_order_quantity", {"quantity": "0"}
    ),
    _RejectionCase(
        "QUANTITY_NOT_POSITIVE", COLUMN_REFUSAL, "chk_paper_order_quantity", {"quantity": "-1"}
    ),
    _RejectionCase(
        "QUANTITY_NOT_POSITIVE",
        COLUMN_REFUSAL,
        "chk_paper_order_quantity",
        {"quantity": "-0.00000001"},
    ),
    _RejectionCase("SYMBOL_NOT_VALIDATED", PERSISTED, None, {"symbol": "ETH/USDT"}),
    _RejectionCase("SYMBOL_NOT_VALIDATED", PERSISTED, None, {"symbol": "BTC/USD"}),
    _RejectionCase(
        "LIMIT_PRICE_NOT_POSITIVE",
        COLUMN_REFUSAL,
        "chk_paper_order_limit_price",
        {"order_type": "limit", "limit_price": "0"},
    ),
    _RejectionCase(
        "LIMIT_PRICE_NOT_POSITIVE",
        COLUMN_REFUSAL,
        "chk_paper_order_limit_price",
        {"order_type": "limit", "limit_price": "-100"},
    ),
    _RejectionCase(
        "ORDER_TYPE_UNSUPPORTED", COLUMN_REFUSAL, "chk_paper_order_type", {"order_type": "stop"}
    ),
    _RejectionCase(
        "ORDER_TYPE_UNSUPPORTED",
        COLUMN_REFUSAL,
        "chk_paper_order_type",
        {"order_type": "trailing_stop"},
    ),
    _RejectionCase(
        "ORDER_TYPE_UNSUPPORTED",
        PERSISTED,
        None,
        {"order_type": "limit", "limit_price": "100"},
        narrowing={"supported_order_types": ("market",)},
    ),
    _RejectionCase("SIDE_UNSUPPORTED", COLUMN_REFUSAL, "chk_paper_order_side", {"side": "short"}),
    _RejectionCase("SIDE_UNSUPPORTED", COLUMN_REFUSAL, "chk_paper_order_side", {"side": "hold"}),
    _RejectionCase(
        "SIDE_UNSUPPORTED",
        PERSISTED,
        None,
        {"side": "sell"},
        narrowing={"supported_sides": ("buy",)},
    ),
    _RejectionCase(
        "INSUFFICIENT_FUNDS",
        PERSISTED,
        None,
        {"order_type": "limit", "quantity": "4", "limit_price": "120.50"},
        capital=Decimal("100"),
    ),
)

#: The six conditions, for the per-reason floors.
P24_REASONS: Tuple[str, ...] = tuple(
    dict.fromkeys(case.reason for case in P24_CASES)
)


class _RejectionRun:
    """The values one example applies to all fourteen cases and to the accepted control."""

    __slots__ = ("quantity", "fee_rate", "slippage_rate", "market_close", "control_quantity")

    def __init__(
        self,
        quantity: str,
        fee_rate: str,
        slippage_rate: str,
        market_close: str,
        control_quantity: str,
    ) -> None:
        self.quantity = quantity
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.market_close = market_close
        self.control_quantity = control_quantity

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_RejectionRun(quantity={self.quantity!r}, fee={self.fee_rate!r}, "
            f"slip={self.slippage_rate!r}, close={self.market_close!r}, "
            f"control={self.control_quantity!r})"
        )


@st.composite
def rejection_runs(draw: Any) -> _RejectionRun:
    """The material every one of P-24's fourteen cases and its control is driven with.

    All fourteen in every example, because P-24 names six conditions and Requirement 16.5 has two
    branches for two of them - a run that sampled one case per example would leave most of the
    conditions unexercised in most runs, which is why :data:`P24_FLOORS` is the example count for
    each reason and for each branch. The base quantity, the recorded rates and the reference close
    are drawn, so what is fixed is the *condition* and not the numbers it is expressed with.

    The **accepted control** is drawn alongside and asserted to move the balances. Without it "no
    balance, position or equity value changes" would be satisfied by a session that never changed
    anything at all.
    """
    return _RejectionRun(
        quantity=draw(st.sampled_from(("0.5", "1", "2"))),
        fee_rate=draw(st.sampled_from(FEE_RATES)),
        slippage_rate=draw(st.sampled_from(SLIPPAGE_RATES)),
        market_close=draw(st.sampled_from(MARKET_CLOSES)),
        control_quantity=draw(st.sampled_from(MARKET_QUANTITIES)),
    )


P24_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "cases": EXAMPLES * len(P24_CASES),
    "branch_persisted": EXAMPLES
    * len([case for case in P24_CASES if case.branch == PERSISTED]),
    "branch_column_refusal": EXAMPLES
    * len([case for case in P24_CASES if case.branch == COLUMN_REFUSAL]),
    "accepted_control_moved_the_balances": EXAMPLES,
    **{f"reason_{reason}": EXAMPLES for reason in P24_REASONS},
    **{
        f"blocked_by_{constraint}": EXAMPLES
        for constraint, _predicate in ORDER_COLUMN_CHECKS
    },
}

P24_LABELS: Dict[str, str] = {
    "branch_persisted": "a rejection was persisted as a REJECTED order naming its check",
    "branch_column_refusal": "a rejection the column constraints forbid answered 400 with no row",
    "accepted_control_moved_the_balances": "the accepted control order moved the balances",
    "reason_INSUFFICIENT_FUNDS": "an order was refused for funds and locked nothing",
}


def _apply_rejection_case(
    case: _RejectionCase, run: _RejectionRun, recorder: _Recorder
) -> None:
    """Submit one invalid intent and assert the rejection and the absence of every side effect."""
    supabase, session, account_id = _seed(capital=case.capital)
    config = _config(
        fee_rate=Decimal(run.fee_rate),
        slippage_rate=Decimal(run.slippage_rate),
        **case.narrowing,
    )
    payload = _intent(**{"quantity": run.quantity, **case.overrides})
    latest = _event(close=run.market_close, source_event_id="evt-reject")
    available = Decimal(str(supabase.accounts[0]["available_balance"]))

    # ── the generator's own declaration, verified against the requirement before the module runs ──
    expected = _oracle_rejection(
        payload,
        config,
        available_balance=available,
        reference_price=Decimal(run.market_close),
    )
    assert expected == case.reason, (
        f"the generated intent does not fail the check the case declares: this file's reading of "
        f"Requirement 16.5 / 16.6 says {expected!r}, the case says {case.reason!r}. "
        f"Case: {case!r}; intent {payload!r}"
    )
    blocked_by = _oracle_column_refusal(payload)
    if case.branch == COLUMN_REFUSAL:
        assert blocked_by == case.constraint, (
            f"the case declares {case.constraint!r} but 009's CHECK clauses, as written down in "
            f"ORDER_COLUMN_CHECKS, say {blocked_by!r}. Case: {case!r}"
        )
    else:
        assert blocked_by is None, (
            f"the case declares a persisted rejection but {blocked_by} refuses the intent's own "
            f"values, so no row could exist. Case: {case!r}"
        )

    before = _snapshot(supabase)
    account_before = dict(supabase.accounts[0])
    mark = _mark(supabase)

    if case.branch == PERSISTED:
        outcome = _submit(
            supabase,
            session,
            account_id,
            config=config,
            intent=payload,
            latest_event=latest,
            sleep=_Sleeps(),
        )
        assert outcome.rejected is True, (
            f"P-24 (Requirement 16.5): the intent was not rejected: {outcome!r}. Case: {case!r}"
        )
        assert outcome.rejection_reason == case.reason, (
            f"P-24 (Requirement 16.5): the recorded reason is {outcome.rejection_reason!r}, not "
            f"the {case.reason!r} this file's reading of the requirement names. Case: {case!r}"
        )
        assert str(outcome.order["order_state"]) == "REJECTED", (
            f"P-24: the persisted order is {outcome.order['order_state']}, not REJECTED. "
            f"Case: {case!r}"
        )
        assert str(outcome.order["rejection_reason"]) == case.reason, (
            f"P-24 (Requirement 16.5): the ROW must carry the reason naming the failed check, and "
            f"it carries {outcome.order['rejection_reason']!r}. Case: {case!r}"
        )
        assert str(outcome.order["legacy_status"]) == "REJECTED"

        rows = [dict(row) for row in supabase.orders]
        assert len(rows) == len(before["orders"]) + 1, (
            f"P-24: {len(rows) - len(before['orders'])} order rows were written, not one. "
            f"Case: {case!r}"
        )
        inserts = supabase.statements_on(repo.ORDERS_TABLE, "insert")
        assert [query.payload["order_state"] for query in inserts] == ["CREATED"], (
            f"P-24 (Requirement 16.5): the rejection must be set 'to REJECTED from CREATED', so "
            f"the INSERT lands at CREATED. Case: {case!r}"
        )
        updates = supabase.statements_on(repo.ORDERS_TABLE, "update")
        assert updates[-1].filter_value("order_state") == "CREATED", (
            f"P-24: the UPDATE to REJECTED is not guarded by CREATED. Case: {case!r}"
        )
        recorder.mark("branch_persisted")
    else:
        with pytest.raises(sim.PaperOrderInvalid) as caught:
            _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=payload,
                latest_event=latest,
                sleep=_Sleeps(),
            )
        error = caught.value
        assert error.code == PAPER_ORDER_INVALID
        assert error.http_status == 400
        assert error.details["validation"] == case.reason, (
            f"P-24: the 400 names {error.details['validation']!r}, not the {case.reason!r} "
            f"Requirement 16.5 names for this check. Case: {case!r}"
        )
        assert error.details["blocked_by"] == case.constraint, (
            f"P-24: the 400 blames {error.details.get('blocked_by')!r}, not {case.constraint!r}. "
            f"Case: {case!r}"
        )
        assert error.details["persisted"] is False
        assert [dict(row) for row in supabase.orders] == before["orders"], (
            f"P-24: {case.constraint} refuses the intent's own values, so no paper_orders row can "
            f"exist - and one was written. Case: {case!r}"
        )
        assert _wrote_since(supabase, mark) == [], (
            f"P-24: the refused intent issued writes {_wrote_since(supabase, mark)}. "
            f"Case: {case!r}"
        )
        recorder.mark("branch_column_refusal")
        recorder.mark(f"blocked_by_{case.constraint}")

    # ── the half of Requirement 16.5 and 16.6 that is about what did NOT happen ──
    after = _snapshot(supabase)
    for table in UNTOUCHED_TABLES:
        assert after[table] == before[table], (
            f"P-24 (Requirements 16.5, 16.6): the rejected intent changed {table}. A rejection "
            f"makes no change to the Paper_Account's balances or positions and locks no funds. "
            f"Case: {case!r}"
        )
    account_after = dict(supabase.accounts[0])
    for column in ("available_balance", "locked_balance", "total_equity", "realized_pnl", "version"):
        assert Decimal(str(account_after[column])) == Decimal(str(account_before[column])), (
            f"P-24: {column} moved from {account_before[column]} to {account_after[column]} on a "
            f"rejected intent. Case: {case!r}"
        )
    assert supabase.statements_on(repo.ACCOUNTS_TABLE, "update") == [], (
        f"P-24: a statement was issued against paper_accounts on a rejected intent. Case: {case!r}"
    )
    assert supabase.statements_on(repo.POSITIONS_TABLE, "insert") == []
    assert supabase.statements_on(repo.POSITIONS_TABLE, "update") == []

    recorder.mark("cases")
    recorder.mark(f"reason_{case.reason}")


def _apply_accepted_control(run: _RejectionRun, recorder: _Recorder) -> None:
    """The control: a valid, funded intent IS accepted and DOES move the balances.

    Requirement 16.5's "no change" is only a claim if a change is possible, so one example of the
    change is driven in every example. Without it, an implementation that rejected every intent
    would satisfy P-24 completely.
    """
    supabase, session, account_id = _seed(capital=RICH)
    config = _config(
        fee_rate=Decimal(run.fee_rate), slippage_rate=Decimal(run.slippage_rate)
    )
    before = dict(supabase.accounts[0])

    outcome = _submit(
        supabase,
        session,
        account_id,
        config=config,
        intent=_intent(quantity=run.control_quantity, idempotency_key="key-control"),
        latest_event=_event(close=run.market_close, source_event_id="evt-control"),
        sleep=_Sleeps(),
    )
    assert outcome.rejection_reason is None, (
        f"the control intent was rejected for {outcome.rejection_reason}: {run!r}"
    )
    assert outcome.fill is not None and outcome.fill.applied is True
    after = dict(supabase.accounts[0])
    assert Decimal(str(after["available_balance"])) != Decimal(str(before["available_balance"])), (
        f"the control order did not move the available balance, so P-24's 'no change' claim is "
        f"about a change that cannot happen: {run!r}"
    )
    assert [row["cause"] for row in supabase.balance_events] == ["FILL"]
    assert len(supabase.positions) == 1
    assert len(supabase.equity_snapshots) == 1
    recorder.mark("accepted_control_moved_the_balances")


def test_p24_invalid_intents_are_rejected_without_side_effects(request: Any) -> None:
    """Every invalid intent is rejected with a recorded reason, and nothing else moves.

    For all intents with a quantity at or below zero, a symbol outside the session's validated set,
    a limit price at or below zero, an unsupported order type, an unsupported side, or required
    funds exceeding the available balance: the order is rejected with a reason naming the failed
    check, and no balance, position or equity value changes.

    Both of the branches Requirement 16.5 has are asserted. Where the ``paper_orders`` CHECK
    constraints permit the intent's values, the rejection is a persisted ``REJECTED`` order carrying
    the reason, set from ``CREATED``. Where they refuse them - ``chk_paper_order_quantity``,
    ``chk_paper_order_limit_price``, ``chk_paper_order_type`` and ``chk_paper_order_side``, from
    ``009_paper_trading.sql`` lines 728-731 - no row can exist, so the answer is 400
    ``PAPER_ORDER_INVALID`` carrying the same reason in ``details["validation"]`` and the constraint
    in ``details["blocked_by"]``, with nothing written at all. The constraints are not relaxed; the
    divergence is recorded here and in ``paper_simulator.ORDER_COLUMN_CONSTRAINTS``.

    The oracle is each case's declared reason, checked against :func:`_oracle_rejection` - this
    file's own reading of Requirement 16.5's eight checks in the requirement's order - and against
    :func:`_oracle_column_refusal`, this file's own reading of 009's four CHECK clauses. Neither
    calls ``sim.static_rejection_reason`` or ``sim.unrepresentable_order_constraint``. An accepted,
    funded control order is driven in every example and asserted to move the balances, so the
    "no change" half of the claim is a statement about a change that can happen.

    **Validates: Requirements 16.5, 16.6**
    """
    recorder = _Recorder("P-24", P24_FLOORS, P24_LABELS)

    @PROPERTY_SETTINGS
    @given(run=rejection_runs())
    def check(run: _RejectionRun) -> None:
        recorder.start()
        recorder.mark("examples")
        for case in P24_CASES:
            _apply_rejection_case(case, run, recorder)
        _apply_accepted_control(run, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLES' OWN TABLES, GUARDED
# ══════════════════════════════════════════════════════════════════════════
#
# Every oracle above rests on a literal written down in this file. A literal that had drifted from
# the requirement, from the module or from the migration would make a property fail - or pass - for
# a reason that had nothing to do with the code. These are deterministic companions, not properties:
# none of them carries a ``test_p{n}_`` name, so the scoreboard counts none of them.


def test_the_written_down_transition_table_matches_the_module() -> None:
    """:data:`REQUIREMENT_16_2_TRANSITIONS` is exactly ``PAPER_ORDER_TRANSITIONS``.

    The table is written down rather than imported so that P-17's closure and P-19's complement are
    statements about Requirement 16.2 and not about ``paper_order_state``. This is where the two are
    held against each other - the one place a disagreement should surface, and the reason a
    disagreement is legible as "the module and the requirement differ" rather than as a property
    failure somewhere else.
    """
    assert {state.value for state in PaperOrderState} == set(REQUIREMENT_16_1_STATES), (
        "Requirement 16.1 fixes exactly six Paper_Order_State values and PaperOrderState no longer "
        "spells that set"
    )
    module_pairs = {
        (origin.value, target.value)
        for origin, targets in PAPER_ORDER_TRANSITIONS.items()
        for target in targets
    }
    assert module_pairs == PERMITTED_PAIRS, (
        "the module's transition relation and Requirement 16.2's differ. Only in the module: "
        f"{sorted(module_pairs - PERMITTED_PAIRS)}; only in the requirement: "
        f"{sorted(PERMITTED_PAIRS - module_pairs)}"
    )
    assert len(PERMITTED_PAIRS) == 9
    assert ("PARTIALLY_FILLED", "PARTIALLY_FILLED") in PERMITTED_PAIRS, (
        "Requirement 16.2's one self-transition is what makes successive partial fills "
        "representable, and it is gone"
    )
    for terminal in REQUIREMENT_16_3_TERMINAL:
        outgoing = sorted(target for origin, target in PERMITTED_PAIRS if origin == terminal)
        assert outgoing == [], (
            f"Requirement 16.3: {terminal} is terminal, and the relation lets it move to {outgoing}"
        )


def test_the_reachability_closure_covers_every_state_and_is_computed_here() -> None:
    """P-17's oracle reaches all six values, and answers ``CREATED`` alone for a terminal origin.

    The second half matters: a closure function that simply returned every state would make P-17
    vacuous, so the closure is checked to *discriminate* - from ``FILLED`` it reaches nothing but
    ``FILLED`` itself.
    """
    assert REACHABLE_FROM_CREATED == set(REQUIREMENT_16_1_STATES), (
        f"the closure from CREATED is {sorted(REACHABLE_FROM_CREATED)}, which is not all six "
        "Requirement 16.1 values - one of them would then be an orphan state"
    )
    for terminal in REQUIREMENT_16_3_TERMINAL:
        assert _reachable_from(terminal) == {terminal}, (
            f"the closure from the terminal state {terminal} reaches "
            f"{sorted(_reachable_from(terminal))}"
        )
    assert _reachable_from("ACCEPTED") == {
        "ACCEPTED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REJECTED",
    }


def test_the_illegal_pair_partition_is_exhaustive_and_names_its_own_gap() -> None:
    """36 ordered pairs, 9 permitted, 27 illegal, 17 attemptable through ``paper_simulator``.

    And the ten that are not attemptable are **exactly** the pairs targeting ``CREATED`` or
    ``CANCELLED`` - the arithmetic of gap 2, stated as a check so that a new write path in
    ``paper_simulator`` (a cancel, say) makes this fail and forces P-19 to grow rather than
    silently leaving pairs unattempted.
    """
    assert len(ALL_PAIRS) == 36
    assert len(ILLEGAL_PAIRS) == 27
    assert len(ATTEMPTABLE_ILLEGAL) == 17
    assert len(UNATTEMPTABLE_ILLEGAL) == 10
    assert UNATTEMPTABLE_ILLEGAL == frozenset(
        pair for pair in ILLEGAL_PAIRS if pair[1] in ("CREATED", "CANCELLED")
    ), (
        "the pairs P-19 records as unattemptable are no longer exactly those targeting CREATED or "
        f"CANCELLED: {sorted(UNATTEMPTABLE_ILLEGAL)}"
    )
    assert len(ILLEGAL_FILL_ATTEMPTS) == 8
    assert len(LEGAL_FILL_ATTEMPTS) == 4
    assert len(ILLEGAL_SUBMIT_ATTEMPTS) == 9
    assert ATTEMPTABLE_ILLEGAL | UNATTEMPTABLE_ILLEGAL == ILLEGAL_PAIRS
    assert PERMITTED_PAIRS & ILLEGAL_PAIRS == frozenset()


def test_the_written_down_rejection_order_matches_the_module() -> None:
    """:data:`REQUIREMENT_16_5_ORDER` is ``sim.REJECTION_REASONS``, in order.

    P-24's oracle evaluates the checks in this order, and the order is contractual: an intent that
    fails two checks is reported under the first, so a reordering would change the reason a caller
    reads without changing whether the order was rejected.
    """
    assert REQUIREMENT_16_5_ORDER == sim.REJECTION_REASONS
    assert sim.STATIC_REJECTION_REASONS == REQUIREMENT_16_5_ORDER[:8]
    assert set(case.reason for case in P24_CASES) <= set(REQUIREMENT_16_5_ORDER)


def test_the_written_down_column_checks_are_009s_own_text() -> None:
    """:data:`ORDER_COLUMN_CHECKS` is read out of ``009_paper_trading.sql``, verbatim.

    P-24 predicts which branch each rejection takes from this table, so the table has to be the
    migration's own text. Asserted against the file rather than against
    ``paper_simulator.ORDER_COLUMN_CONSTRAINTS``, which is the module's copy of the same thing and
    could have drifted from the schema in the same direction.
    """
    migration = (
        Path(__file__).resolve().parents[2] / repo.PAPER_TRADING_MIGRATION
    )
    assert migration.is_file(), f"{migration} is not readable"
    text = migration.read_text(encoding="utf-8-sig")
    for constraint, predicate in ORDER_COLUMN_CHECKS:
        needle = f"CONSTRAINT {constraint} {predicate}"
        assert needle in text, (
            f"{needle!r} is not in {repo.PAPER_TRADING_MIGRATION}. P-24 predicts a 400-with-no-row "
            "from this clause, so a changed constraint changes which branch the rejection takes."
        )
    assert set(constraint for constraint, _ in ORDER_COLUMN_CHECKS) == set(
        constraint for constraint, _ in sim.ORDER_COLUMN_CONSTRAINTS
    ), "this file and paper_simulator disagree about which four CHECKs are in the way"


def test_the_simulator_writes_neither_created_nor_cancelled() -> None:
    """Gap 1 and gap 2, as facts about ``paper_simulator``'s source rather than as prose.

    ``CANCELLED`` appears nowhere in the module - there is no cancel path, which is why P-17's
    cancellations and P-19's ten unattemptable pairs are what they are - and no
    ``repo.update_order`` call targets ``CREATED``, which is only ever an INSERT's starting state.
    """
    source = inspect.getsource(sim)
    assert "CANCELLED" not in source, (
        "paper_simulator now mentions CANCELLED. If it writes it, P-17's cancellation steps should "
        "go through the simulator rather than the Persistence_Layer and P-19 has four more illegal "
        "pairs to attempt."
    )

    tree = ast.parse(source)
    targets: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", "")
        )
        if name not in ("update_order", "insert_order"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "order_state":
                targets.add(f"{name}:{ast.unparse(keyword.value)}")

    updates = {
        value.split(":", 1)[1] for value in targets if value.startswith("update_order:")
    }
    assert "PaperOrderState.CREATED" not in updates, (
        "paper_simulator UPDATEs an order TO CREATED, so the six pairs P-19 records as "
        f"unattemptable are attemptable. update_order targets: {sorted(updates)}"
    )
    assert updates <= {"PaperOrderState.ACCEPTED", "PaperOrderState.REJECTED", "target"}, (
        f"paper_simulator gained a new order-state target: {sorted(updates)}. P-19 enumerates the "
        "attemptable pairs from the targets the module writes, so a new one has to be enumerated."
    )
    assert {
        value.split(":", 1)[1] for value in targets if value.startswith("insert_order:")
    } == {"PaperOrderState.CREATED"}, (
        "an order is inserted at something other than CREATED, and CREATED is the only origin "
        "Requirement 16.2's relation has"
    )


def test_the_transition_guard_runs_before_the_first_write() -> None:
    """The defect P-19 found, pinned structurally: ``can_transition`` precedes ``insert_fill``.

    ``apply_fill`` used to validate ``state -> target`` only inside its fifth write, by which point
    the fill row, the account balance, the ledger row and the position had been written and this
    transport had no ``ROLLBACK`` to undo them. Asserted on the source positions because the
    ordering *is* the fix: a check that is present but late is the bug.
    """
    source = inspect.getsource(sim.apply_fill)
    guard = source.index("can_transition(")
    for write in ("repo.insert_fill(", "repo.bump_version(", "repo.upsert_position("):
        assert guard < source.index(write), (
            f"apply_fill checks the transition after {write} - so an illegal transition would "
            "leave a partial write behind, which is exactly the defect P-19 reported"
        )
    assert "ILLEGAL_TRANSITION" in source, (
        "Requirement 16.4 asks for an error naming the rejected transition, and apply_fill no "
        "longer raises one"
    )


def test_the_fill_sum_oracle_reads_the_rows_and_not_the_column() -> None:
    """P-20's oracle would be worthless if it read ``paper_orders.filled_quantity``.

    The whole content of task 25.10's "rather than read from ``paper_orders.filled_quantity``, so a
    drift between the two is a failure" is that the oracle and the column are two independent
    records. Asserted on :func:`_fill_sum_from_rows`' own source, and then demonstrated: a column
    poisoned behind the simulator's back does not move the oracle's answer, and P-20's drift
    assertion catches it.
    """
    body = inspect.getsource(_fill_sum_from_rows)
    assert "filled_quantity" not in body.split('"""')[-1], (
        "_fill_sum_from_rows reads filled_quantity, so P-20's drift check compares a value with "
        "itself"
    )

    supabase, session, account_id = _seed(capital=RICH)
    config = _config(fee_rate=Decimal("0"), slippage_rate=Decimal("0"))
    order = _accepted_market_order(supabase, account_id, quantity="1")
    _fill(
        supabase,
        session,
        order,
        config=config,
        quantity="0.6",
        price="100",
        fill_event_id="drift-1",
    )
    assert _fill_sum_from_rows(supabase, order["id"]) == Decimal("0.6")

    for row in supabase.orders:
        row["filled_quantity"] = "0"
    assert _fill_sum_from_rows(supabase, order["id"]) == Decimal("0.6"), (
        "the oracle followed the poisoned column, so it is not independent of it"
    )
    with pytest.raises(AssertionError, match="a drift between the two is a failure"):
        _assert_fill_sum_is_bounded_and_filled_iff_equal(supabase, "a poisoned column")
    repo.reset_persistence_probe()


def test_the_census_recorder_refuses_an_unknown_bucket_and_reports_a_shortfall() -> None:
    """The non-vacuity machinery itself, because a census that silently dropped a bucket is worse
    than none: every floor would pass and every property would look non-vacuous.
    """
    recorder = _Recorder("probe", {"seen": 2, "never": 1}, {"seen": "a label"})
    recorder.start()
    recorder.mark("seen", 2)
    with pytest.raises(AssertionError, match="is not a census bucket"):
        recorder.mark("typo")
    with pytest.raises(AssertionError, match="probe would be vacuous"):
        recorder.assert_not_vacuous()

    with pytest.raises(AssertionError, match="labelled buckets with no floor"):
        _Recorder("probe", {"seen": 1}, {"unfloored": "a label"})
