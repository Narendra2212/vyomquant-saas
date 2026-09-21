"""
tests/property/test_paper_replay_model.py - P-31, the model-based replay agreement.

Spec: marketplace-subscriptions-paper-trading task 25.15. ``design.md`` -> "Property-to-test
mapping". Requirements 15.4, 18.3, 18.13, and the accounting rules of Requirement 18 that both
implementations re-derive (18.2, 18.5 - 18.11).

THE ONE PROPERTY LIVING HERE
----------------------------
``test_p31_simulator_agrees_with_the_reference_ledger``

For all generated market-event and order-intent sequences, the simulator's final balances,
positions, realized profit and loss, equity series and order states equal those produced by
``paper_replay.ReferenceLedger`` on the same sequence. Exactly one ``test_p31_`` function at module
scope, and nothing nested carries that prefix: ``tests/property/test_property_coverage.py``
discovers by ``ast.walk`` and would count a nested one too.

WHAT IS THE SUBJECT AND WHAT IS THE ORACLE
------------------------------------------
* **Subject** - the real ``paper_simulator.submit_intent``, ``check_resting_orders`` and, through
  them, ``apply_fill`` (tasks 25.3 - 25.6), driven against
  ``tests/test_paper_repository.FakeSupabase`` - the one Persistence_Layer double this repository
  has. No second double, no mock of the repository, no direct call to ``apply_fill``: every
  statement this property observes is issued by the same code production issues it from, and every
  figure it compares is read back out of the double's rows rather than off a returned object.
* **Oracle** - ``paper_replay.ReferenceLedger``, the second independent implementation of the
  Requirement 18 accounting rules from task 9.2. It is **not modified** by this file. It is driven
  from the generated step list, in the generated order, and never from what the simulator did: a
  ledger fed the simulator's own answers would agree with it by construction and would detect
  nothing.

THE ONE PIECE OF THE MODEL THIS FILE HAS TO SUPPLY, AND WHY
----------------------------------------------------------
``ReferenceLedger``'s own docstring says the fill model is deliberately **not** in it: "Slippage
direction, the limit-price trigger and the participation cap are ``paper_simulator``'s. This ledger
is handed a quantity, a price and a fee that some caller already decided on." So driving it from a
generated *event* sequence needs a reading of task 25.5's fill rules to turn an event into
``(quantity, price, fee)``, and that reading is :func:`_oracle_reference`,
:func:`_oracle_market_fill_price`, :func:`_oracle_fee`, :func:`_oracle_triggered` and
:func:`_oracle_fillable` below - written here from the task text, calling nothing in
``paper_simulator``. :func:`test_the_written_down_fill_model_is_the_modules_reading` holds that
literal against the module on named examples, which is where a "this is a faithful reading of the
rule" claim belongs; it is never used to *build* an expectation inside the property.

What P-31 therefore compares is the whole of the accounting: which side a fill opens, what the
weighted-average entry becomes, how much a reversal closes, what realized PnL is recorded, what
cash moves, what each order's state and filled quantity become, and what the equity series is.
Those are two independent implementations and the comparison is exact.

THE FIVE COMPARISONS, SEPARATELY, SO A FAILURE NAMES WHAT DIVERGED
-----------------------------------------------------------------
1. balances - ``available_balance``, ``locked_balance``, ``total_equity``
2. positions - side, size and entry price, per symbol
3. realized profit and loss
4. the ordered equity series - one row per applied fill
5. order states plus ``filled_quantity``, per order, in submission order

and a sixth that task 25.15 does not name but that follows the same rule:

6. the closed round trips - the ``paper_trades`` rows, with their closed quantity, entry price,
   exit price, realized PnL and fee. Kept separate from comparison 3 because two ledgers can agree
   on *cumulative* realized PnL while disagreeing about which quantity at which entry produced it,
   and a reversal's split into "the trip that closed" and "the surplus that opened the other side"
   is visible nowhere else. The simulator's fee is read back from the integer
   ``paper_trades.fee_minor`` at the session's recorded ``minor_unit_exponent``.

Every one is exact ``Decimal`` equality. No ``pytest.approx``, no ``round()``, no tolerance
anywhere: Requirement 18.3 says "exact decimal equality with zero tolerance", and a tolerance would
turn P-31 into a statement about nothing.

THE THREE KNOWN DIVERGENCES, HONOURED RATHER THAN PAPERED OVER
-------------------------------------------------------------
1. **The ledger models no ``feed_state``.** ``ReferenceLedger.apply_fill`` has no feed gate;
   ``paper_simulator.apply_fill`` re-reads the ``paper_sessions`` row inside every attempt and
   requires ``admit_execution`` to admit it, and ``TRADEABLE_FEED_STATES`` is exactly
   ``("HEALTHY",)``. A session whose feed is not ``HEALTHY`` therefore applies no fill on the
   subject side while the oracle would apply one - not a disagreement about accounting but a rule
   the oracle does not model. So **every session here is ``HEALTHY`` from the first step to the
   last** (``_seed()``'s default, asserted in :func:`_run_programme`), and the feed gate is P-52's
   subject rather than something this property weakens. Nothing about the gate is bypassed,
   stubbed or forced: it runs, and it admits.
2. **``ReferenceLedger.submit`` returns an existing order for a repeated idempotency key without
   comparing the parameters**, which is the naive in-memory reading of ``uq_paper_order_idem``.
   ``submit_intent`` compares the ``order_fingerprint`` and raises 409
   ``PAPER_IDEMPOTENCY_CONFLICT`` on a mismatch (Requirement 16.15). The simulator is right and the
   ledger is naive, so this generator issues a **distinct key per step** and the divergence is
   never reached. The idempotency rules themselves are P-21 and P-22's
   (``tests/property/test_paper_idempotence.py``), which assert them against the subject directly.
3. **``ReferenceLedger.submit`` quantizes the intent's quantity and limit price; the simulator
   persists the intent's own values.** For a *rejected* order that matters: an intent whose
   quantity carries more decimal places than the symbol's recorded precision
   (``QUANTITY_PRECISION``) would be quantized by the ledger to ``0``, and a ``paper_orders`` row
   with ``quantity = 0`` is not representable - ``009_paper_trading.sql``'s
   ``chk_paper_order_quantity`` declares ``CHECK (quantity > 0)``. Requirement 16.5 requires the
   rejection to be *persisted* naming the failed check, so the simulator must store the value it
   was sent; ``ReferenceLedger.submit``'s own docstring says the rejection catalogue of Requirement
   16.5 is not its concern. The ledger is naive here too, and the generator therefore draws only
   the two rejection conditions whose intents are exact at the recorded precisions
   (:data:`REJECTION_CASES`). The precision rejections are P-24's, which asserts them against the
   subject in both branches.

``release_from_locked`` IS NOT HAND-COMPUTED FOR THE SUBJECT
-----------------------------------------------------------
It is an *argument* to ``ReferenceLedger.apply_fill`` and is *derived* inside
``paper_simulator.apply_fill`` for a limit order (``_release_for_fill``). So the subject is driven
through ``submit_intent`` and ``check_resting_orders``, which never pass it - the simulator derives
its own - and only the **oracle** is handed one, computed by
``ReferenceLedger.required_funds(fill_quantity, limit_price)`` clamped to the ledger's own
``locked_balance``. That is Requirement 16.6's formula evaluated by the oracle's own arithmetic,
not the subject's.

NON-VACUITY: A FORCED SPINE, A CENSUS, AND FLOORS THAT ARE NOT NEGOTIABLE
------------------------------------------------------------------------
An agreement between two ledgers that never moved is not evidence of anything, so every example
forces a spine of twelve steps and the census counts what the run **observed** in the double's
rows - not what the spine intended. The floors that read ``EXAMPLES`` (or a multiple) are forcing
claims: the spine puts that case in every example, so anything below means the forcing stopped
working. A shortfall is fixed by FORCING the case in the generator, never by lowering a floor.

The spine reaches, in every example: market orders and limit orders; a partially filled resting
order that then fills exactly (``PARTIALLY_FILLED -> PARTIALLY_FILLED -> FILLED``); a resting order
no candle can reach, so ``locked_balance`` is positive at the end; a position that grows, so the
weighted-average entry price moves; a partial close that records realized PnL and writes **no**
``paper_trades`` row; a position reversing through zero, which writes one; a long and a short held
at the same time; three symbols; a non-zero fee **and** a non-zero slippage on the same fill; and
one persisted rejection.

GAPS LEFT OPEN, STATED RATHER THAN ASSERTED AROUND
--------------------------------------------------
1. **One market-data shape per step.** A market intent is priced from the event handed to
   ``submit_intent``; a resting order is filled by an event handed to ``check_resting_orders``. The
   generated sequence keeps those separate, so no single event does both here. The fill model's
   own edges - a candle that trades through a limit, a tick source with no candle fields - are
   ``tests/test_paper_order_lifecycle_writes.py``'s.
2. **No cancellation and no revaluation.** ``paper_simulator`` writes no ``CANCELLED`` (see
   ``tests/property/test_paper_order_lifecycle.py``'s gap 1) and holds no revaluation path;
   ``ReferenceLedger.revalue`` and ``ReferenceLedger.cancel`` are therefore not driven, so the
   equity series compared here is exactly the ``FILL`` series. ``SESSION_START`` and
   ``SESSION_STOP`` snapshots belong to the session-start and stop services of task 26.x and are
   asserted separately.
3. **``market_type`` is absent from the frozen config** (``paper_simulator``'s recorded gap), so
   neither side reports a margin figure and P-31 compares none.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_replay as replay
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The shared harness. A second one would be a second account of what a session is. ──────
from tests.test_paper_order_lifecycle_writes import (
    NOW,
    SYMBOL,
    _config,
    _event,
    _intent,
    _run_coroutine,
    _seed,
    _Sleeps,
    _submit,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example drives a double-digit number of real simulator calls against the double,
#: so ``too_slow`` is suppressed rather than the example count being cut.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

_Recorder = Recorder
_publish_hypothesis_statistics = publish_hypothesis_statistics

#: ``test_property_coverage.py``'s own pattern, so the guard at the bottom of this file reads the
#: module exactly as the scoreboard does.
PROPERTY_FUNCTION_PATTERN = re.compile(r"^test_p(\d+)_")

ZERO = Decimal("0")
ONE = Decimal("1")

#: The three symbols one generated session trades. ``SYMBOL`` is the harness's, whose market
#: metadata the frozen config's precisions and maximum were read from; the other two are added to
#: ``validated_symbols`` so Requirement 16.5's symbol check admits them. They share that one
#: metadata reading, which is what a session with one recorded precision set means - not three
#: markets with three precisions.
SECOND_SYMBOL = "ETH/USDT"
THIRD_SYMBOL = "SOL/USDT"
SESSION_SYMBOLS: Tuple[str, ...] = (SYMBOL, SECOND_SYMBOL, THIRD_SYMBOL)

#: The capital every generated session starts from. Large enough that no forced step is ever an
#: accidental ``INSUFFICIENT_FUNDS``: the whole spine's notional is under a thousand.
RICH = Decimal("1000000")

#: A buy limit no generated candle can reach, for the order that must stay ``ACCEPTED`` with its
#: funds locked to the last step. Every generated candle low is at or above 40.
UNTRIGGERABLE_LIMIT = "1"


# ══════════════════════════════════════════════════════════════════════════
# THE FILL MODEL, WRITTEN DOWN HERE (task 25.5, design.md)
# ══════════════════════════════════════════════════════════════════════════
#
# Literals and formulae read off the task text, not imported from ``paper_simulator``. They are
# what turns a generated event into the ``(quantity, price, fee)`` triple ``ReferenceLedger``
# expects, because the ledger deliberately models no fill model of its own (its module docstring:
# "WHAT IS DELIBERATELY NOT HERE ... The fill model").
# ``test_the_written_down_fill_model_is_the_modules_reading`` holds them against the module on
# named examples; nothing inside the property calls ``paper_simulator`` to build an expectation.

#: task 25.5: "the ``ask`` / ``bid`` of the latest validated event when the selected source
#: supplies them, otherwise its ``close``".
REFERENCE_FIELD: Mapping[str, str] = {"buy": "ask", "sell": "bid"}

#: task 25.5: ``buy: low <= limit`` (candle), ``last <= limit`` (tick), ``close`` last resort;
#: ``sell: high >= limit``, ``last >= limit``, ``close``.
TRIGGER_FIELDS: Mapping[str, Tuple[str, ...]] = {
    "buy": ("low", "last", "close"),
    "sell": ("high", "last", "close"),
}


def _decimal_of(event: Mapping[str, Any], name: str) -> Optional[Decimal]:
    """One numeric field off a generated event as an exact ``Decimal``, or ``None``.

    The generated events carry exact decimal **strings**, which is what
    ``paper_market_events.payload`` stores; a ``float`` would be refused by the code under test
    (Requirement 18.1) and is never produced here.
    """
    value = event.get(name)
    return None if value is None else Decimal(str(value))


def _quantize(value: Decimal, places: int, rounding: str) -> Decimal:
    """``value`` at ``places`` decimal places under the session's recorded rounding mode.

    Requirement 18.2's "one rounding mode recorded in the session configuration and applied to
    every such computation", spelled out here rather than borrowed from ``AccountingConfig``.
    """
    return value.quantize(ONE.scaleb(-places), rounding=rounding)


def _oracle_reference(
    event: Optional[Mapping[str, Any]], side: str
) -> Optional[Decimal]:
    """The reference a market order is priced from: the side's quote, else ``close``.

    ``None`` when the event supplies neither as a strictly positive number - which is
    ``NO_VALIDATED_PRICE`` rather than a synthesised price (Requirement 14.9).
    """
    if event is None:
        return None
    candidates: List[Optional[Decimal]] = []
    quote_field = REFERENCE_FIELD.get(side)
    if quote_field is not None:
        candidates.append(_decimal_of(event, quote_field))
    candidates.append(_decimal_of(event, "close"))
    for candidate in candidates:
        if candidate is not None and candidate > ZERO:
            return candidate
    return None


def _oracle_market_fill_price(
    reference: Decimal, side: str, config: sim.SessionConfig
) -> Decimal:
    """``reference x (1 +/- slippage_rate)``, adverse only: a buy slips up, a sell slips down."""
    drift = config.slippage_rate if side == "buy" else -config.slippage_rate
    return _quantize(reference * (ONE + drift), config.price_precision, config.rounding_mode)


def _oracle_fee(quantity: Decimal, price: Decimal, config: sim.SessionConfig) -> Decimal:
    """``quantize(quantity x price x fee_rate, minor_units)`` - quantized once, at the end."""
    qty = _quantize(quantity, config.quantity_precision, config.rounding_mode)
    px = _quantize(price, config.price_precision, config.rounding_mode)
    return _quantize(qty * px * config.fee_rate, config.minor_unit_exponent, config.rounding_mode)


def _oracle_slippage(
    quantity: Decimal, price: Decimal, reference: Decimal, config: sim.SessionConfig
) -> Decimal:
    """``quantize(quantity x |price - reference|, minor_units)`` - the cost the slip imposed.

    Not part of the ledger's arithmetic (it takes the fill price already slipped); recorded here
    because the census counts a non-zero slippage as an observed fact.
    """
    qty = _quantize(quantity, config.quantity_precision, config.rounding_mode)
    px = _quantize(price, config.price_precision, config.rounding_mode)
    ref = _quantize(reference, config.price_precision, config.rounding_mode)
    return _quantize(abs(px - ref) * qty, config.minor_unit_exponent, config.rounding_mode)


def _oracle_triggered(side: str, limit: Decimal, event: Mapping[str, Any]) -> bool:
    """Whether ``event`` fills a resting limit order at ``limit`` on ``side``.

    The first of the side's three fields the event carries decides, and nothing else does.
    """
    if limit <= ZERO:
        return False
    for name in TRIGGER_FIELDS.get(side, ()):
        candidate = _decimal_of(event, name)
        if candidate is None:
            continue
        return candidate <= limit if side == "buy" else candidate >= limit
    return False


def _oracle_fillable(
    remaining: Decimal, event: Mapping[str, Any], config: sim.SessionConfig
) -> Decimal:
    """``MIN(remaining, quantize(volume x participation_rate))`` - a deterministic cap.

    An event stating no volume states nothing about liquidity, so the whole remainder is fillable;
    inventing a figure to cap against would be a fabricated measurement (Requirement 28.3). There
    is no probability anywhere in this rule, which is the whole of task 25.5's partial-fill model.
    """
    if remaining <= ZERO:
        return ZERO
    volume = _decimal_of(event, "volume")
    if volume is None or volume <= ZERO or config.participation_rate <= ZERO:
        return remaining
    cap = _quantize(
        volume * config.participation_rate, config.quantity_precision, config.rounding_mode
    )
    return cap if cap < remaining else remaining


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATED STEP LIST
# ══════════════════════════════════════════════════════════════════════════

MARKET = "market"
LIMIT = "limit"
CANDLE = "candle"
REJECT = "reject"


class _Step:
    """One step of a generated sequence: an order intent, or a market event.

    A plain value object. Both drivers - the simulator's and the ledger's - read the **same**
    ``_Step`` list, in the same order, and neither reads anything the other produced.
    """

    __slots__ = (
        "kind",
        "symbol",
        "side",
        "quantity",
        "price",
        "spread",
        "volume",
        "offset",
        "reason",
        "overrides",
        "label",
    )

    def __init__(
        self,
        kind: str,
        *,
        symbol: str = SYMBOL,
        side: str = "buy",
        quantity: str = "1",
        price: str = "100",
        spread: Optional[str] = None,
        volume: Optional[str] = None,
        offset: str = "0",
        reason: str = "",
        overrides: Optional[Dict[str, Any]] = None,
        label: str = "",
    ) -> None:
        self.kind = kind
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
        self.spread = spread
        self.volume = volume
        self.offset = offset
        self.reason = reason
        self.overrides = dict(overrides or {})
        self.label = label

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Step({self.kind!r}, label={self.label!r}, symbol={self.symbol!r}, "
            f"side={self.side!r}, quantity={self.quantity!r}, price={self.price!r}, "
            f"spread={self.spread!r}, volume={self.volume!r}, offset={self.offset!r}, "
            f"reason={self.reason!r})"
        )


class _Programme:
    """One generated session: its recorded rates, and the step list they are applied to."""

    __slots__ = ("fee_rate", "slippage_rate", "participation_rate", "steps")

    def __init__(
        self,
        fee_rate: str,
        slippage_rate: str,
        participation_rate: str,
        steps: Tuple[_Step, ...],
    ) -> None:
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.participation_rate = participation_rate
        self.steps = tuple(steps)

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Programme(fee={self.fee_rate!r}, slippage={self.slippage_rate!r}, "
            f"participation={self.participation_rate!r}, steps={list(self.steps)!r})"
        )


#: Recorded fee and slippage rates. Both are always **non-zero**, which is what makes the census's
#: "a fill recorded a non-zero fee AND a non-zero slippage" reachable in every example: a rate is a
#: property of the whole session, so a zero one could not be forced away for a single fill. The
#: zero-rate cases are Requirement 18.6's conservation reading and belong to P-27 and P-28.
FEE_RATES: Tuple[str, ...] = ("0.001", "0.0025", "0.01")
SLIPPAGE_RATES: Tuple[str, ...] = ("0.001", "0.002", "0.005")

#: ``(limit order quantity, participation_rate, first candle volume)`` triples for which the
#: deterministic cap ``quantize(volume x participation_rate)`` is **exactly half** the order
#: quantity at the symbol's 8-decimal quantity precision. Half and not "some fraction", because
#: that is what makes the resting spine deterministic: the first candle leaves the order
#: ``PARTIALLY_FILLED``, and the second - whose volume is far larger than the cap needs - lands on
#: the quantity **exactly**, which is Requirement 16.14's equality. Every product is exact in
#: decimal: 2x0.25, 2x0.5, 2x0.2 and 8x0.25 need no rounding.
LIMIT_TRIPLES: Tuple[Tuple[str, str, str], ...] = (
    ("1", "0.25", "2"),
    ("2", "0.50", "2"),
    ("0.8", "0.20", "2"),
    ("4", "0.25", "8"),
)

#: The volume of the candle that closes a resting order: any cap above the remainder does.
CLOSING_VOLUME = "1000"

#: Prices, all at or below the market's recorded 2-decimal price precision and all far above the
#: untriggerable limit, so a candle can be drawn below them without reaching it.
PRICES: Tuple[str, ...] = ("50", "100", "96.25", "120.50")

#: ``(first close, second close)`` for the two buys that build the long position. **Distinct** and
#: at least 3.75 apart, so the weighted-average entry price provably moves between them - two equal
#: closes would leave the entry where it was and the census's forcing claim would be empty.
CLOSE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("50", "100"),
    ("100", "50"),
    ("96.25", "120.50"),
    ("120.50", "96.25"),
    ("50", "96.25"),
    ("100", "120.50"),
)

#: How far above the higher of the two buy closes a selling close is drawn. At or above 5, which
#: is more than the widest spread plus the widest slippage can move a price at these magnitudes -
#: so a close that sells out of the long realizes a **strictly positive** PnL and the census's
#: "realized PnL moved" is a forced fact rather than a hope.
SELL_OFFSETS: Tuple[str, ...] = ("5", "10", "25")

#: The two-sided quote a market event may carry. ``None`` is a candle-only source, whose reference
#: is its ``close``; a number is an ``ask``/``bid`` pair around the close, whose reference is the
#: side's own quote. Both halves of task 25.5's reference rule are therefore drawn.
QUOTE_SPREADS: Tuple[Optional[str], ...] = (None, "0.05", "0.50", "1")

#: How far through its limit a triggering candle traded. ``"0"`` is included deliberately: a candle
#: whose low **equals** a buy limit is the boundary of the trigger rule.
TRIGGER_OFFSETS: Tuple[str, ...] = ("0", "1", "5.25")

#: Quantities for the position the spine builds and unwinds.
FIRST_QUANTITIES: Tuple[str, ...] = ("1", "2", "1.5")
SECOND_QUANTITIES: Tuple[str, ...] = ("0.5", "1", "2")
CLOSE_QUANTITIES: Tuple[str, ...] = ("0.25", "0.5", "1")
SURPLUS_QUANTITIES: Tuple[str, ...] = ("0.25", "0.5", "1")

#: The two Requirement 16.5 rejections whose intents are **exact** at the recorded precisions, so
#: the ledger's quantization of a submitted intent cannot make the two records differ. See known
#: divergence 3 in the module docstring for why the two precision rejections are not drawn here and
#: where they are asserted instead.
REJECTION_CASES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("SYMBOL_NOT_VALIDATED", {"symbol": "DOGE/USDT", "quantity": "0.5"}),
    ("QUANTITY_ABOVE_MAX", {"quantity": "2000"}),
)

#: Tail step kinds. None of them can reclassify a spine order: a market order fills itself, an
#: untriggerable resting order never fills, and a rejection touches one new order. They may well
#: reverse a position a second time or close one further, which is the point of having them - the
#: ledger and the simulator have to agree about that too.
TAIL_KINDS: Tuple[str, ...] = ("market", "untriggerable_limit", "reject")
TAIL_QUANTITIES: Tuple[str, ...] = ("0.1", "0.25", "0.5")


@st.composite
def replay_programmes(draw: Any) -> _Programme:
    """A session whose steps force every accounting case P-31 has to compare.

    The spine, in order, and in every example:

    1. a **market buy** opening a long in ``SYMBOL``;
    2. a **market buy** at a different close, so the position grows and its weighted-average
       entry price moves (Requirement 18.8's one cost-basis convention);
    3. a **market sell** of less than the open size: a partial close that records realized PnL and
       writes **no** ``paper_trades`` row, because the position has not reached zero;
    4. a **limit buy** in ``SECOND_SYMBOL``, which rests and locks its required funds;
    5. a **limit buy** at :data:`UNTRIGGERABLE_LIMIT` in ``SECOND_SYMBOL``, which rests to the end,
       so ``locked_balance`` is strictly positive in the final comparison;
    6. a **candle** whose participation cap is exactly half step 4's quantity -> partially filled;
    7. a **candle** with ample volume -> the remainder fills exactly -> ``FILLED``;
    8. a **limit sell** in ``THIRD_SYMBOL``, resting on the other side of the trigger rule;
    9. a **candle** filling half of it -> a short opens, partially filled;
    10. a **candle** with ample volume -> ``FILLED``;
    11. a **market sell** of the whole remaining long plus a surplus: a reversal through zero,
        which closes the position (one ``paper_trades`` row) and opens a short with the surplus;
    12. a **market buy** adding to the resting long at a price the limit was not, so the position
        that survives to the final comparison carries a weighted average of **two different**
        prices rather than one price repeated - without it, every surviving entry price would equal
        some single fill price and the position comparison would not see a wrong cost basis;
    13. an **invalid intent** -> one persisted ``REJECTED`` order, no balance movement.

    Then one to three drawn tail steps, so no example is exactly the spine. Every numeric value,
    every price, every rate, every quantity and every quote shape is drawn.
    """
    fee_rate = draw(st.sampled_from(FEE_RATES))
    slippage_rate = draw(st.sampled_from(SLIPPAGE_RATES))
    limit_quantity, participation_rate, first_volume = draw(st.sampled_from(LIMIT_TRIPLES))

    first_close, second_close = draw(st.sampled_from(CLOSE_PAIRS))
    higher = max(Decimal(first_close), Decimal(second_close))
    partial_close = str(higher + Decimal(draw(st.sampled_from(SELL_OFFSETS))))
    reversal_close = str(higher + Decimal(draw(st.sampled_from(SELL_OFFSETS))))

    first_quantity = draw(st.sampled_from(FIRST_QUANTITIES))
    second_quantity = draw(st.sampled_from(SECOND_QUANTITIES))
    closing_quantity = draw(st.sampled_from(CLOSE_QUANTITIES))
    surplus = draw(st.sampled_from(SURPLUS_QUANTITIES))
    # The long that step 11 reverses out of. Computed from the drawn quantities, not from anything
    # either implementation reported: min(1) + min(0.5) - max(1) = 0.5, so it is always positive.
    remaining_long = (
        Decimal(first_quantity) + Decimal(second_quantity) - Decimal(closing_quantity)
    )
    reversal_quantity = str(remaining_long + Decimal(surplus))

    buy_limit = draw(st.sampled_from(PRICES))
    sell_limit = draw(st.sampled_from(PRICES))
    buy_trigger_offset = draw(st.sampled_from(TRIGGER_OFFSETS))
    sell_trigger_offset = draw(st.sampled_from(TRIGGER_OFFSETS))
    spreads = tuple(draw(st.lists(st.sampled_from(QUOTE_SPREADS), min_size=4, max_size=4)))

    steps: List[_Step] = [
        _Step(
            MARKET,
            symbol=SYMBOL,
            side="buy",
            quantity=first_quantity,
            price=first_close,
            spread=spreads[0],
            label="open-long",
        ),
        _Step(
            MARKET,
            symbol=SYMBOL,
            side="buy",
            quantity=second_quantity,
            price=second_close,
            spread=spreads[1],
            label="add-to-long",
        ),
        _Step(
            MARKET,
            symbol=SYMBOL,
            side="sell",
            quantity=closing_quantity,
            price=partial_close,
            spread=spreads[2],
            label="partial-close",
        ),
        _Step(
            LIMIT,
            symbol=SECOND_SYMBOL,
            side="buy",
            quantity=limit_quantity,
            price=buy_limit,
            label="resting-buy",
        ),
        _Step(
            LIMIT,
            symbol=SECOND_SYMBOL,
            side="buy",
            quantity=limit_quantity,
            price=UNTRIGGERABLE_LIMIT,
            label="untriggerable-buy",
        ),
        _Step(
            CANDLE,
            symbol=SECOND_SYMBOL,
            side="buy",
            price=buy_limit,
            offset=buy_trigger_offset,
            volume=first_volume,
            label="capped-buy-candle",
        ),
        _Step(
            CANDLE,
            symbol=SECOND_SYMBOL,
            side="buy",
            price=buy_limit,
            offset=buy_trigger_offset,
            volume=CLOSING_VOLUME,
            label="closing-buy-candle",
        ),
        _Step(
            LIMIT,
            symbol=THIRD_SYMBOL,
            side="sell",
            quantity=limit_quantity,
            price=sell_limit,
            label="resting-sell",
        ),
        _Step(
            CANDLE,
            symbol=THIRD_SYMBOL,
            side="sell",
            price=sell_limit,
            offset=sell_trigger_offset,
            volume=first_volume,
            label="capped-sell-candle",
        ),
        _Step(
            CANDLE,
            symbol=THIRD_SYMBOL,
            side="sell",
            price=sell_limit,
            offset=sell_trigger_offset,
            volume=CLOSING_VOLUME,
            label="closing-sell-candle",
        ),
        _Step(
            MARKET,
            symbol=SYMBOL,
            side="sell",
            quantity=reversal_quantity,
            price=reversal_close,
            spread=spreads[3],
            label="reversal",
        ),
        _Step(
            MARKET,
            symbol=SECOND_SYMBOL,
            side="buy",
            quantity=draw(st.sampled_from(SECOND_QUANTITIES)),
            price=str(Decimal(buy_limit) + Decimal(draw(st.sampled_from(SELL_OFFSETS)))),
            spread=draw(st.sampled_from(QUOTE_SPREADS)),
            label="blend-resting-long",
        ),
    ]

    reason, overrides = draw(st.sampled_from(REJECTION_CASES))
    steps.append(_Step(REJECT, reason=reason, overrides=overrides, label="rejection"))

    for index in range(draw(st.integers(min_value=1, max_value=3))):
        kind = draw(st.sampled_from(TAIL_KINDS))
        if kind == "market":
            steps.append(
                _Step(
                    MARKET,
                    symbol=draw(st.sampled_from(SESSION_SYMBOLS)),
                    side=draw(st.sampled_from(("buy", "sell"))),
                    quantity=draw(st.sampled_from(TAIL_QUANTITIES)),
                    price=draw(st.sampled_from(PRICES)),
                    spread=draw(st.sampled_from(QUOTE_SPREADS)),
                    label=f"tail-market-{index}",
                )
            )
        elif kind == "untriggerable_limit":
            steps.append(
                _Step(
                    LIMIT,
                    symbol=draw(st.sampled_from((SECOND_SYMBOL, THIRD_SYMBOL))),
                    side="buy",
                    quantity=draw(st.sampled_from(TAIL_QUANTITIES)),
                    price=UNTRIGGERABLE_LIMIT,
                    label=f"tail-resting-{index}",
                )
            )
        else:
            tail_reason, tail_overrides = draw(st.sampled_from(REJECTION_CASES))
            steps.append(
                _Step(
                    REJECT,
                    reason=tail_reason,
                    overrides=tail_overrides,
                    label=f"tail-rejection-{index}",
                )
            )

    return _Programme(
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        participation_rate=participation_rate,
        steps=tuple(steps),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS
# ══════════════════════════════════════════════════════════════════════════

#: How many steps the forced spine has. A programme longer than this carried a drawn tail.
SPINE_STEPS = 13

P31_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    # The spine's five market orders and its three limit orders, counted off the stored rows.
    "market_orders_filled": EXAMPLES * 5,
    "limit_orders_filled": EXAMPLES * 2,
    "limit_orders_partially_filled_then_filled": EXAMPLES * 2,
    "resting_orders_left_accepted": EXAMPLES,
    "rejections_persisted": EXAMPLES,
    # The accounting cases.
    "weighted_average_entry_moved": EXAMPLES,
    "surviving_entry_blends_two_prices": EXAMPLES,
    "partial_close_without_a_trade_row": EXAMPLES,
    "position_reversed_through_zero": EXAMPLES,
    "closed_trade_rows_written": EXAMPLES,
    "realized_pnl_moved": EXAMPLES * 2,
    "long_position_held_at_the_end": EXAMPLES,
    "short_position_held_at_the_end": EXAMPLES,
    "symbols_carrying_a_position": EXAMPLES * 3,
    # The recorded costs, and the money that stayed locked.
    "non_zero_fee_recorded": EXAMPLES,
    "non_zero_slippage_recorded": EXAMPLES,
    "fee_and_slippage_on_one_fill": EXAMPLES,
    "locked_balance_positive_at_the_end": EXAMPLES,
    # The series, and the tail.
    "equity_snapshots": EXAMPLES * 9,
    "tail_beyond_the_spine": EXAMPLES,
    "two_sided_quote_priced_a_market_order": EXAMPLES // 4,
    "candle_close_priced_a_market_order": EXAMPLES // 4,
}

P31_LABELS: Dict[str, str] = {
    "market_orders_filled": "a market order filled against the event it was priced from",
    "limit_orders_filled": "a resting limit order reached FILLED",
    "non_zero_fee_recorded": "a fill recorded a non-zero fee",
    "non_zero_slippage_recorded": "a fill recorded a non-zero slippage",
    "symbols_carrying_a_position": "more than one symbol carried a position",
    "limit_orders_partially_filled_then_filled": (
        "a resting order was filled in two capped parts and reached FILLED exactly"
    ),
    "surviving_entry_blends_two_prices": (
        "a position surviving to the comparison carried a blend of two different fill prices"
    ),
    "resting_orders_left_accepted": "a resting order no candle reached kept its locked funds",
    "weighted_average_entry_moved": "a position grew and its weighted-average entry moved",
    "partial_close_without_a_trade_row": (
        "a partial close recorded realized PnL and wrote no paper_trades row"
    ),
    "position_reversed_through_zero": "a position reversed through zero",
    "closed_trade_rows_written": "a round trip reached zero and wrote a paper_trades row",
    "fee_and_slippage_on_one_fill": "one fill recorded a non-zero fee AND a non-zero slippage",
    "locked_balance_positive_at_the_end": "locked_balance was strictly positive at the end",
    "rejections_persisted": "a rejected order was persisted with its reason",
    "long_position_held_at_the_end": "a long position was open at the end",
    "short_position_held_at_the_end": "a short position was open at the end",
    "two_sided_quote_priced_a_market_order": "a market order was priced from an ask/bid quote",
    "candle_close_priced_a_market_order": "a market order was priced from a candle close",
    "tail_beyond_the_spine": "the programme carried steps beyond the forced spine",
}


# ══════════════════════════════════════════════════════════════════════════
# THE TWO PROJECTIONS P-31 COMPARES
# ══════════════════════════════════════════════════════════════════════════


def _numeric(value: Any) -> Decimal:
    """One ``NUMERIC(28,10)`` column as an exact ``Decimal``.

    The double stores what the repository writes - a decimal **string** - so this is a lossless
    read and never a ``float`` conversion (Requirement 18.1).
    """
    return Decimal(str(value))


def _from_minor(value: Any, config: sim.SessionConfig) -> Decimal:
    """One ``*_minor`` integer column as the amount it counts, at the session's money scale.

    ``009_paper_trading.sql`` stores ``paper_trades.fee_minor`` as a whole number of the currency's
    smallest unit, so the amount is that count scaled by the **session's recorded**
    ``minor_unit_exponent`` (Requirement 18.2's one money scale) rather than by an assumed two.
    """
    return Decimal(int(value)).scaleb(-config.minor_unit_exponent)


def _position_rank(row: Mapping[str, Any]) -> Tuple[int, str, int]:
    """How recent a ``paper_positions`` row is, for picking the symbol's current one.

    An OPEN row outranks every closed one - ``uq_paper_position_open`` guarantees there is at most
    one. Among closed rows the later ``closed_at`` wins, then the higher ``version``: a symbol that
    reversed and then closed again holds two closed rows, and the ledger's single entry per symbol
    is the **last** state that symbol reached.
    """
    return (
        0 if row.get("closed_at") else 1,
        str(row.get("closed_at") or ""),
        int(row.get("version") or 1),
    )


def _simulator_state(supabase: Any, config: sim.SessionConfig) -> Dict[str, Any]:
    """Everything P-31 compares, read out of the double's rows.

    Read from the **rows**, never from a returned ``FillOutcome`` or ``SubmitOutcome``: the
    property is about what the session persisted, and an object the code under test handed back is
    that code's own account of itself.

    ``config`` is the session's frozen configuration, and it is here for one column:
    ``paper_trades.fee_minor`` is an integer count of the currency's **minor units**, so turning it
    back into an amount needs the exponent the session recorded. That exponent is
    ``config.minor_unit_exponent``, read from ``marketplace.money.minor_unit_exponent`` by
    ``paper_simulator.freeze_session_config`` - which refuses an unsupported currency rather than
    defaulting to 2. A literal ``2`` here would be right for USD and silently wrong for any
    currency with another exponent, which is the whole reason that refusal exists.
    """
    assert len(supabase.accounts) == 1, (
        f"the session should hold exactly one Paper_Account, found {len(supabase.accounts)}"
    )
    account = supabase.accounts[0]

    positions: Dict[str, Dict[str, Any]] = {}
    for row in supabase.positions:
        symbol = str(row["symbol"])
        current = positions.get(symbol)
        if current is None or _position_rank(row) > _position_rank(current):
            positions[symbol] = dict(row)

    return {
        "balances": {
            "available_balance": _numeric(account["available_balance"]),
            "locked_balance": _numeric(account["locked_balance"]),
            "total_equity": _numeric(account["total_equity"]),
        },
        "positions": {
            symbol: {
                "side": str(row["side"]),
                "size": _numeric(row["size"]),
                "entry_price": _numeric(row["entry_price"]),
            }
            for symbol, row in positions.items()
        },
        "realized_pnl": _numeric(account["realized_pnl"]),
        "closed_trades": [
            {
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "quantity": _numeric(row["quantity"]),
                "entry_price": _numeric(row["entry_price"]),
                "exit_price": _numeric(row["exit_price"]),
                "realized_pnl": _numeric(row["realized_pnl"]),
                "fee": _from_minor(row["fee_minor"], config),
            }
            for row in supabase.trades
        ],
        "equity_series": [
            {
                "total_equity": _numeric(row["total_equity"]),
                "available_balance": _numeric(row["available_balance"]),
                "locked_balance": _numeric(row["locked_balance"]),
                "position_market_value": _numeric(row["position_market_value"]),
            }
            for row in supabase.equity_snapshots
        ],
        "orders": [
            {
                "symbol": str(row["symbol"]),
                "side": str(row["side"]),
                "order_type": str(row["order_type"]),
                "quantity": _numeric(row["quantity"]),
                "limit_price": (
                    None if row.get("limit_price") is None else _numeric(row["limit_price"])
                ),
                "order_state": str(row["order_state"]),
                "filled_quantity": _numeric(row["filled_quantity"]),
                "rejection_reason": (
                    None
                    if row.get("rejection_reason") is None
                    else str(row["rejection_reason"])
                ),
            }
            for row in supabase.orders
        ],
    }


def _ledger_state(ledger: replay.ReferenceLedger) -> Dict[str, Any]:
    """The same five figures off the oracle, in the same shape.

    ``ReferenceLedger.state()`` carries all of them and more; this narrows it to what P-31
    compares, so the ``==`` below is over the five subjects and not over a timestamp or a field
    only one side records.
    """
    state = ledger.state()
    return {
        "balances": {
            "available_balance": state["available_balance"],
            "locked_balance": state["locked_balance"],
            "total_equity": state["total_equity"],
        },
        "positions": {
            symbol: {
                "side": position["side"],
                "size": position["size"],
                "entry_price": position["entry_price"],
            }
            for symbol, position in state["positions"].items()
        },
        "realized_pnl": state["realized_pnl"],
        "closed_trades": [
            {
                "symbol": trade["symbol"],
                "side": trade["side"],
                "quantity": trade["quantity"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "realized_pnl": trade["realized_pnl"],
                "fee": trade["fee"],
            }
            for trade in state["closed_trades"]
        ],
        "equity_series": [
            {
                "total_equity": row["total_equity"],
                "available_balance": row["available_balance"],
                "locked_balance": row["locked_balance"],
                "position_market_value": row["position_market_value"],
            }
            for row in state["equity_series"]
            if row["cause"] == replay.FILL
        ],
        "orders": [
            {
                "symbol": order["symbol"],
                "side": order["side"],
                "order_type": order["order_type"],
                "quantity": order["quantity"],
                "limit_price": order["limit_price"],
                "order_state": order["order_state"],
                "filled_quantity": order["filled_quantity"],
                "rejection_reason": order["rejection_reason"],
            }
            for order in state["orders"].values()
        ],
    }


def _assert_agreement(
    subject: Mapping[str, Any], model: Mapping[str, Any], programme: _Programme
) -> None:
    """The five comparisons, one at a time, so a failure names which of them diverged."""
    context = f"\n\nProgramme: {programme!r}"

    # ── 1: balances (Requirements 18.3, 18.4) ──
    assert subject["balances"] == model["balances"], (
        "P-31 diverged on BALANCES. The simulator's persisted paper_accounts row and the "
        "ReferenceLedger disagree about the cash:\n"
        f"  simulator: {subject['balances']}\n"
        f"  ledger:    {model['balances']}\n"
        "The comparison is exact decimal equality with zero tolerance (Requirement 18.3), so a "
        "difference of one minor unit is a difference." + context
    )

    # ── 2: positions (Requirements 18.5, 18.8) ──
    assert set(subject["positions"]) == set(model["positions"]), (
        "P-31 diverged on WHICH SYMBOLS CARRY A POSITION:\n"
        f"  simulator: {sorted(subject['positions'])}\n"
        f"  ledger:    {sorted(model['positions'])}\n"
        "A fully closed position is size zero with closed_at set and is never deleted "
        "(Requirement 18.5), so the two sets must match." + context
    )
    for symbol in sorted(model["positions"]):
        assert subject["positions"][symbol] == model["positions"][symbol], (
            f"P-31 diverged on the POSITION in {symbol}:\n"
            f"  simulator: {subject['positions'][symbol]}\n"
            f"  ledger:    {model['positions'][symbol]}\n"
            "side, size and entry price are all compared exactly: the side is the explicit LONG "
            "or SHORT of Requirement 18.5, and the entry price is Requirement 18.8's one "
            "cost-basis convention." + context
        )

    # ── 3: realized PnL (Requirement 18.8) ──
    assert subject["realized_pnl"] == model["realized_pnl"], (
        "P-31 diverged on REALIZED PROFIT AND LOSS:\n"
        f"  simulator: {subject['realized_pnl']}\n"
        f"  ledger:    {model['realized_pnl']}\n"
        "Realized PnL is closed quantity at recorded fill prices with no fee netted in "
        "(Requirement 18.8)." + context
    )

    # ── 4: the equity series (Requirements 18.3, 18.11) ──
    assert len(subject["equity_series"]) == len(model["equity_series"]), (
        "P-31 diverged on the LENGTH OF THE EQUITY SERIES: the simulator persisted "
        f"{len(subject['equity_series'])} snapshot(s) and the ledger recorded "
        f"{len(model['equity_series'])} for the applied fills. Requirement 18.11 wants exactly "
        "one snapshot per applied fill, and Requirement 18.13 none for a repeated event."
        + context
    )
    for index, (persisted, expected) in enumerate(
        zip(subject["equity_series"], model["equity_series"])
    ):
        assert persisted == expected, (
            f"P-31 diverged on EQUITY SERIES row {index}:\n"
            f"  simulator: {persisted}\n"
            f"  ledger:    {expected}\n"
            "Each row is total_equity = available + locked + position_market_value at that fill's "
            "price (Requirement 18.3), and the series is compared in order." + context
        )

    # ── 5: order states and filled quantities (Requirements 16.1, 16.13, 16.14) ──
    assert len(subject["orders"]) == len(model["orders"]), (
        f"P-31 diverged on the ORDER COUNT: the simulator persisted {len(subject['orders'])} "
        f"order(s) and the ledger recorded {len(model['orders'])}. One generated intent is one "
        "order on both sides." + context
    )
    for index, (persisted, expected) in enumerate(zip(subject["orders"], model["orders"])):
        assert persisted == expected, (
            f"P-31 diverged on ORDER {index} (submission order):\n"
            f"  simulator: {persisted}\n"
            f"  ledger:    {expected}\n"
            "The state is one of Requirement 16.1's six values and is FILLED if and only if the "
            "filled quantity equals the ordered quantity (Requirements 16.13, 16.14)." + context
        )

    # ── 6: the closed round trips (Requirement 18.10) ──
    #
    # Not one of task 25.15's five named subjects, and kept as its own comparison rather than
    # folded into realized PnL: a ``paper_trades`` row is written only when a position quantity
    # reaches **exactly** zero, so it is the one place the reversal's split into "the trip that
    # closed" and "the surplus that opened the other side" is visible. Two ledgers can agree on
    # cumulative realized PnL while disagreeing about which quantity at which entry closed it.
    assert len(subject["closed_trades"]) == len(model["closed_trades"]), (
        f"P-31 diverged on the CLOSED TRADE COUNT: the simulator wrote "
        f"{len(subject['closed_trades'])} paper_trades row(s) and the ledger recorded "
        f"{len(model['closed_trades'])}. A round trip is closed when its position quantity "
        "reaches zero and only then (Requirement 18.10)." + context
    )
    for index, (persisted, expected) in enumerate(
        zip(subject["closed_trades"], model["closed_trades"])
    ):
        assert persisted == expected, (
            f"P-31 diverged on CLOSED TRADE {index}:\n"
            f"  simulator: {persisted}\n"
            f"  ledger:    {expected}\n"
            "quantity, entry price, exit price, realized PnL and fee are all exact. The fee sits "
            "beside realized PnL rather than netted into it (Requirement 18.8), and the "
            "simulator's is read back from the integer paper_trades.fee_minor at the session's "
            "recorded minor-unit exponent." + context
        )


# ══════════════════════════════════════════════════════════════════════════
# THE TWO DRIVERS
# ══════════════════════════════════════════════════════════════════════════


class _Resting:
    """One resting limit order as the **oracle's** book holds it.

    Built from the generated step list and updated only by the oracle's own fill decisions, so the
    ledger is never told what the simulator did. ``order_id`` is the ledger's own
    ``ref-order-{n}``.
    """

    __slots__ = ("order_id", "symbol", "side", "limit", "quantity", "filled")

    def __init__(
        self, order_id: str, symbol: str, side: str, limit: Decimal, quantity: Decimal
    ) -> None:
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.limit = limit
        self.quantity = quantity
        self.filled = ZERO


def _ledger_config(config: sim.SessionConfig) -> Dict[str, Any]:
    """The ``paper_sessions.config`` mapping the oracle reads, off the same frozen config.

    The **inputs** are shared - they are the session's recorded rates and precisions, and a model
    run under different ones would be measuring a different session - but no arithmetic is: every
    figure below is a string or an int, and ``ReferenceLedger`` re-derives every computation from
    Requirement 18. ``market_type`` is absent for the reason ``paper_simulator``'s ``CONFIG_KEYS``
    omits it, so neither side reports a margin figure.
    """
    return {
        "fee_rate": str(config.fee_rate),
        "slippage_rate": str(config.slippage_rate),
        "rounding_mode": config.rounding_mode,
        "cost_basis": config.cost_basis,
        "price_precision": config.price_precision,
        "quantity_precision": config.quantity_precision,
        "minor_unit_exponent": config.minor_unit_exponent,
    }


def _market_event(
    *,
    symbol: str,
    close: str,
    at: datetime,
    source_event_id: str,
    spread: Optional[str] = None,
    low: Optional[str] = None,
    high: Optional[str] = None,
    volume: Optional[str] = None,
) -> Dict[str, Any]:
    """One validated market event, in the harness's shape, for ``symbol``.

    The harness's ``_event`` pins its symbol to the session's one; every value is an exact decimal
    **string**, which is what ``paper_market_events.payload`` stores and what
    ``paper_simulator._event_decimal`` accepts (Requirement 18.1).
    """
    ask = bid = None
    if spread is not None:
        ask = str(Decimal(close) + Decimal(spread))
        bid = str(Decimal(close) - Decimal(spread))
    event = _event(
        close=close,
        low=low,
        high=high,
        volume=volume,
        ask=ask,
        bid=bid,
        source_event_id=source_event_id,
        at=at,
    )
    event["symbol"] = symbol
    return event


def _candle_for(step: _Step, at: datetime, index: int) -> Dict[str, Any]:
    """The candle a ``CANDLE`` step publishes: it trades at or through ``step.price``.

    A buy step draws ``low = price - offset`` and a sell step ``high = price + offset``, so the
    trigger rule answers ``True`` for a resting order at that price and the ``offset`` of ``"0"``
    puts the boundary - a candle that touched the limit exactly - into the run. ``close`` is the
    limit itself, so a resting fill's price (exactly the limit) is the same number whatever the
    candle did around it.
    """
    price = Decimal(step.price)
    offset = Decimal(step.offset)
    if step.side == "buy":
        low = price - offset
        high = price + Decimal("10")
    else:
        low = price - Decimal("10")
        high = price + offset
    assert low > ZERO, f"a generated candle low of {low} is not a price: {step!r}"
    return _market_event(
        symbol=step.symbol,
        close=step.price,
        low=str(low),
        high=str(high),
        volume=step.volume,
        source_event_id=f"evt-{index}",
        at=at,
    )


def _run_programme(programme: _Programme, recorder: _Recorder) -> None:
    """Drive both implementations over one generated step list and compare the five figures.

    The subject is driven through ``submit_intent`` and ``check_resting_orders`` only, so every
    fill decision, every ``release_from_locked`` and every state transition on that side is the
    module's own. The oracle is driven from the same ``_Step`` list with the oracle's own fill
    model and the oracle's own ``required_funds``.
    """
    config = _config(
        fee_rate=Decimal(programme.fee_rate),
        slippage_rate=Decimal(programme.slippage_rate),
        participation_rate=Decimal(programme.participation_rate),
        validated_symbols=SESSION_SYMBOLS,
    )
    supabase, session, account_id = _seed(capital=RICH)
    # Known divergence 1: the ledger models no feed_state, so the session stays HEALTHY for the
    # whole programme and the gate admits every step rather than being bypassed.
    assert session["feed_state"] == "HEALTHY", (
        "P-31 requires a HEALTHY feed for the whole programme, because ReferenceLedger models no "
        f"feed state at all; the seeded session reports {session['feed_state']!r}"
    )

    ledger = replay.ReferenceLedger(
        config=_ledger_config(config),
        initial_balance=RICH,
        currency="USD",
        started_at=NOW,
    )
    sleeps = _Sleeps()
    book: List[_Resting] = []
    entry_after_first_buy: Optional[Decimal] = None

    for index, step in enumerate(programme.steps, start=1):
        at = NOW + timedelta(minutes=index)
        key = f"key-{index}"

        if step.kind == MARKET:
            event = _market_event(
                symbol=step.symbol,
                close=step.price,
                spread=step.spread,
                source_event_id=f"evt-{index}",
                at=at,
            )
            intent = _intent(
                symbol=step.symbol,
                side=step.side,
                order_type="market",
                quantity=step.quantity,
                idempotency_key=key,
            )

            outcome = _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=intent,
                latest_event=event,
                sleep=sleeps,
            )
            assert outcome.fill is not None and outcome.fill.applied, (
                f"the market step {step!r} did not fill: rejection="
                f"{outcome.rejection_reason!r}, outcome="
                f"{None if outcome.fill is None else outcome.fill.outcome!r}"
            )

            # ── the oracle, from the generated step alone ──
            reference = _oracle_reference(event, step.side)
            assert reference is not None, f"the generated event states no reference: {step!r}"
            price = _oracle_market_fill_price(reference, step.side, config)
            quantity = Decimal(step.quantity)
            fee = _oracle_fee(quantity, price, config)
            order = ledger.submit(dict(intent, created_at=at))
            ledger.accept(order.order_id)
            ledger.fill_order(
                order.order_id,
                quantity=quantity,
                price=price,
                fee=fee,
                fill_event_id=f"market-{index}",
                filled_at=at,
            )

            # Which half of task 25.5's reference rule this step drew. A property of the generated
            # event's shape, so it is counted here; the recorded costs are counted off the stored
            # ``paper_fills`` rows in :func:`_record_census` instead.
            if step.spread is None:
                recorder.mark("candle_close_priced_a_market_order")
            else:
                recorder.mark("two_sided_quote_priced_a_market_order")

            if step.label == "open-long":
                entry_after_first_buy = _numeric(
                    _open_position_row(supabase, step.symbol)["entry_price"]
                )
            elif step.label == "add-to-long":
                grown = _numeric(_open_position_row(supabase, step.symbol)["entry_price"])
                assert entry_after_first_buy is not None
                assert grown != entry_after_first_buy, (
                    "the spine's second buy left the weighted-average entry price at "
                    f"{grown}; CLOSE_PAIRS and the rates have drifted apart: {programme!r}"
                )
                recorder.mark("weighted_average_entry_moved")
            elif step.label == "partial-close":
                assert supabase.trades == [], (
                    "a partial close wrote a paper_trades row; Requirement 18.10 closes a trade "
                    f"when the position quantity reaches zero: {programme!r}"
                )
                recorder.mark("partial_close_without_a_trade_row")
            elif step.label == "reversal":
                assert len(supabase.trades) == 1, (
                    "the reversal through zero wrote "
                    f"{len(supabase.trades)} paper_trades row(s), expected exactly one: "
                    f"{programme!r}"
                )
                recorder.mark("position_reversed_through_zero")
                recorder.mark("closed_trade_rows_written")
            elif step.label == "blend-resting-long":
                blended = _numeric(_open_position_row(supabase, step.symbol)["entry_price"])
                assert blended != price, (
                    f"the blending buy left {step.symbol}'s entry price at this fill's own price "
                    f"{price}, so a surviving position's entry is not a blend: {programme!r}"
                )
                recorder.mark("surviving_entry_blends_two_prices")

        elif step.kind == LIMIT:
            intent = _intent(
                symbol=step.symbol,
                side=step.side,
                order_type="limit",
                quantity=step.quantity,
                limit_price=step.price,
                idempotency_key=key,
            )
            outcome = _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=intent,
                filled_at=at,
                sleep=sleeps,
            )
            assert outcome.accepted, (
                f"the limit step {step!r} was not accepted: {outcome.rejection_reason!r}"
            )

            # ── the oracle: submit, accept, and lock Requirement 16.6's required funds ──
            order = ledger.submit(dict(intent, created_at=at))
            ledger.accept(order.order_id)
            ledger.lock(ledger.required_funds(Decimal(step.quantity), Decimal(step.price)))
            book.append(
                _Resting(
                    order.order_id,
                    step.symbol,
                    step.side,
                    ledger.price(step.price),
                    ledger.qty(step.quantity),
                )
            )

        elif step.kind == CANDLE:
            event = _candle_for(step, at, index)
            _run_coroutine(
                sim.check_resting_orders(
                    supabase,
                    session,
                    event,
                    config=config,
                    account_id=account_id,
                    sleep=sleeps,
                )
            )

            # ── the oracle: the same book, in submission order, under this file's fill model ──
            for resting in book:
                if resting.symbol != step.symbol:
                    continue
                remaining = ledger.qty(resting.quantity - resting.filled)
                if remaining <= ZERO:
                    continue
                if not _oracle_triggered(resting.side, resting.limit, event):
                    continue
                quantity = _oracle_fillable(remaining, event, config)
                if quantity <= ZERO:
                    continue
                fee = _oracle_fee(quantity, resting.limit, config)
                needed = ledger.required_funds(quantity, resting.limit)
                release = needed if needed < ledger.locked_balance else ledger.locked_balance
                ledger.fill_order(
                    resting.order_id,
                    quantity=quantity,
                    price=resting.limit,
                    fee=fee,
                    fill_event_id=f"limit-{index}-{resting.order_id}",
                    filled_at=at,
                    release_from_locked=release,
                )
                resting.filled = ledger.qty(resting.filled + quantity)

        elif step.kind == REJECT:
            intent = _intent(order_type="market", idempotency_key=key, **step.overrides)
            outcome = _submit(
                supabase,
                session,
                account_id,
                config=config,
                intent=intent,
                latest_event=_market_event(
                    symbol=SYMBOL,
                    close="100",
                    source_event_id=f"evt-{index}",
                    at=at,
                ),
                sleep=sleeps,
            )
            assert outcome.rejection_reason == step.reason, (
                f"the rejection step {step!r} was rejected for "
                f"{outcome.rejection_reason!r} rather than {step.reason!r}"
            )
            order = ledger.submit(dict(intent, created_at=at))
            ledger.reject(order.order_id, step.reason)
            recorder.mark("rejections_persisted")

        else:  # pragma: no cover - the four kinds above are the whole vocabulary
            raise AssertionError(f"unknown step kind {step.kind!r}")

    assert sleeps.delays == [], (
        f"a retry backoff was slept, so a statement conflicted: {sleeps.delays}"
    )

    subject = _simulator_state(supabase, config)
    model = _ledger_state(ledger)
    _assert_agreement(subject, model, programme)
    _record_census(supabase, subject, recorder, programme)


def _open_position_row(supabase: Any, symbol: str) -> Mapping[str, Any]:
    """The account's open ``paper_positions`` row for ``symbol``. Absence is a failure."""
    for row in supabase.positions:
        if str(row["symbol"]) == symbol and not row.get("closed_at"):
            return row
    raise AssertionError(
        f"no open paper_positions row for {symbol!r} is stored; rows: {supabase.positions}"
    )


def _record_census(
    supabase: Any,
    subject: Mapping[str, Any],
    recorder: _Recorder,
    programme: _Programme,
) -> None:
    """Count what this example **observed** in the stored rows, not what the spine intended."""
    fills_per_order: Dict[str, int] = {}
    for row in supabase.fills:
        fills_per_order[str(row["order_id"])] = fills_per_order.get(str(row["order_id"]), 0) + 1
        fee_minor = int(row.get("fee_minor") or 0)
        slippage_minor = int(row.get("slippage_minor") or 0)
        if fee_minor > 0:
            recorder.mark("non_zero_fee_recorded")
        if slippage_minor > 0:
            recorder.mark("non_zero_slippage_recorded")
        # Both costs on the **same** stored row, which is the claim: a run where every fee-bearing
        # fill slipped by nothing and every slipping fill was free would satisfy the two counts
        # above and exercise neither cost together.
        if fee_minor > 0 and slippage_minor > 0:
            recorder.mark("fee_and_slippage_on_one_fill")

    for row in supabase.orders:
        state = str(row["order_state"])
        order_type = str(row["order_type"])
        count = fills_per_order.get(str(row["id"]), 0)
        if order_type == "market" and state == "FILLED":
            recorder.mark("market_orders_filled")
        if order_type == "limit" and state == "FILLED":
            recorder.mark("limit_orders_filled")
            if count >= 2:
                recorder.mark("limit_orders_partially_filled_then_filled")
        if order_type == "limit" and state == "ACCEPTED" and count == 0:
            recorder.mark("resting_orders_left_accepted")

    for row in supabase.balance_events:
        if _numeric(row.get("realized_delta") or 0) != ZERO:
            recorder.mark("realized_pnl_moved")

    for symbol, position in subject["positions"].items():
        recorder.mark("symbols_carrying_a_position")
        if position["size"] > ZERO and position["side"] == "LONG":
            recorder.mark("long_position_held_at_the_end")
        if position["size"] > ZERO and position["side"] == "SHORT":
            recorder.mark("short_position_held_at_the_end")

    if subject["balances"]["locked_balance"] > ZERO:
        recorder.mark("locked_balance_positive_at_the_end")
    recorder.mark("equity_snapshots", len(subject["equity_series"]))
    # ``SPINE_STEPS`` and not the literal 12: the forced spine is thirteen steps long once the
    # rejection is counted, so a "> 12" here would mark this bucket for a bare spine and the floor
    # would stop being a claim about the drawn tail at all.
    if len(programme.steps) > SPINE_STEPS:
        recorder.mark("tail_beyond_the_spine")


# ══════════════════════════════════════════════════════════════════════════
# P-31 (task 25.15) - THE MODEL-BASED REPLAY AGREEMENT
# ══════════════════════════════════════════════════════════════════════════


def test_p31_simulator_agrees_with_the_reference_ledger(request: Any) -> None:
    """The simulator and ``paper_replay.ReferenceLedger`` agree on the whole of the accounting.

    For all generated market-event and order-intent sequences, the simulator's final balances
    (``available_balance``, ``locked_balance``, ``total_equity``), positions (side, size and entry
    price per symbol), realized profit and loss, ordered equity series and order states with their
    filled quantities equal those the reference ledger produces on the same sequence, as do the
    closed ``paper_trades`` round trips. Every comparison is exact ``Decimal`` equality with zero
    tolerance, and the subjects are compared separately so a failure names which of them diverged.

    The subject is driven through ``submit_intent`` and ``check_resting_orders`` against
    ``tests/test_paper_repository.FakeSupabase``, and every figure compared is read back out of the
    persisted rows. The oracle is driven from the same generated step list and never from anything
    the subject reported. **The session's feed state is ``HEALTHY`` for the whole programme**,
    because ``ReferenceLedger`` models no feed state and ``paper_simulator.apply_fill`` requires
    ``admit_execution`` to admit the session before any fill; the gate runs and admits rather than
    being bypassed. Idempotency keys are distinct per step, because
    ``ReferenceLedger.submit`` returns an existing order for a repeated key without comparing the
    parameters while ``submit_intent`` raises ``PAPER_IDEMPOTENCY_CONFLICT`` on a fingerprint
    mismatch (Requirement 16.15) - the simulator is right and the ledger is naive there.

    **Validates: Requirements 15.4, 18.3**
    """
    recorder = _Recorder("P-31", P31_FLOORS, P31_LABELS)

    @PROPERTY_SETTINGS
    @given(programme=replay_programmes())
    def check(programme: _Programme) -> None:
        recorder.start()
        recorder.mark("examples")
        _run_programme(programme, recorder)
        recorder.finish()

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE GUARDS ON THIS FILE'S OWN LITERALS
# ══════════════════════════════════════════════════════════════════════════


def test_the_written_down_fill_model_is_the_modules_reading() -> None:
    """This file's fill-model literal is ``paper_simulator``'s rule, on named examples.

    P-31's oracle needs a reading of task 25.5 to turn a generated event into the
    ``(quantity, price, fee)`` triple ``ReferenceLedger`` takes, because the ledger deliberately
    models no fill model. That reading is checked here, on fixed examples, rather than inside the
    property: an expectation built by calling ``paper_simulator`` would hold for any implementation
    of it, including one that returned a constant.
    """
    config = _config(
        fee_rate=Decimal("0.0025"),
        slippage_rate=Decimal("0.002"),
        participation_rate=Decimal("0.25"),
        validated_symbols=SESSION_SYMBOLS,
    )
    quote = _market_event(
        symbol=SYMBOL,
        close="100",
        spread="0.50",
        source_event_id="evt-quote",
        at=NOW,
        volume="8",
    )
    candle = _market_event(
        symbol=SYMBOL,
        close="100",
        low="95",
        high="105",
        volume="8",
        source_event_id="evt-candle",
        at=NOW,
    )

    # The reference: the side's own quote when the source publishes one, else the close.
    assert _oracle_reference(quote, "buy") == sim.reference_price(quote, "buy")
    assert _oracle_reference(quote, "sell") == sim.reference_price(quote, "sell")
    assert _oracle_reference(candle, "buy") == sim.reference_price(candle, "buy")
    assert _oracle_reference(candle, "sell") == sim.reference_price(candle, "sell")

    # The market fill price: adverse slippage only.
    for side in ("buy", "sell"):
        reference = _oracle_reference(quote, side)
        assert reference is not None
        assert _oracle_market_fill_price(reference, side, config) == sim.market_fill_price(
            reference, side, config
        )

    # The fee, and the slippage cost.
    quantity = Decimal("1.5")
    price = Decimal("100.20")
    reference = Decimal("100")
    assert _oracle_fee(quantity, price, config) == sim.fee_amount(quantity, price, config)
    assert _oracle_slippage(quantity, price, reference, config) == sim.slippage_amount(
        quantity, price, reference, config
    )

    # The trigger, including the boundary where the candle touched the limit exactly.
    for side, limit in (("buy", "95"), ("buy", "94.99"), ("sell", "105"), ("sell", "105.01")):
        order = {"side": side, "limit_price": limit}
        assert _oracle_triggered(side, Decimal(limit), candle) == sim.limit_fill_triggered(
            order, candle
        ), f"the trigger reading differs for a {side} limit at {limit}"

    # The participation cap: a cap, never a coin.
    for remaining in (Decimal("0.5"), Decimal("2"), Decimal("8")):
        order = {"quantity": str(remaining), "filled_quantity": "0"}
        assert _oracle_fillable(remaining, candle, config) == sim.fillable_quantity(
            order, candle, config
        ), f"the participation cap differs for a remaining quantity of {remaining}"
    volumeless = _market_event(
        symbol=SYMBOL, close="100", low="95", source_event_id="evt-novol", at=NOW
    )
    assert _oracle_fillable(Decimal("2"), volumeless, config) == sim.fillable_quantity(
        {"quantity": "2", "filled_quantity": "0"}, volumeless, config
    )


def test_the_session_config_the_oracle_reads_is_the_frozen_one() -> None:
    """The oracle is handed the session's recorded figures, and no others.

    ``ReferenceLedger`` reads eight config keys. Seven are the session's own recorded values and
    ``market_type`` is absent - which is ``paper_simulator``'s stated gap, and means neither side
    reports a margin figure (Requirement 18.12). Asserted so a later change to ``CONFIG_KEYS``
    cannot silently leave the oracle running on a default.
    """
    config = _config(
        fee_rate=Decimal("0.01"),
        slippage_rate=Decimal("0.005"),
        participation_rate=Decimal("0.20"),
        validated_symbols=SESSION_SYMBOLS,
    )
    payload = _ledger_config(config)

    assert payload == {
        "fee_rate": "0.01",
        "slippage_rate": "0.005",
        "rounding_mode": ROUND_HALF_EVEN,
        "cost_basis": "WEIGHTED_AVERAGE",
        "price_precision": 2,
        "quantity_precision": 8,
        "minor_unit_exponent": 2,
    }
    assert "market_type" not in payload

    ledger = replay.ReferenceLedger(config=payload, initial_balance=RICH, currency="USD")
    assert ledger.fee_rate == config.fee_rate
    assert ledger.slippage_rate == config.slippage_rate
    assert ledger.rounding_mode == config.rounding_mode
    assert ledger.cost_basis == config.cost_basis
    assert ledger.margin_usage() is None
    assert ledger.available_balance == RICH


def test_exactly_one_property_function_lives_in_this_module() -> None:
    """One property, one ``test_p{n}_`` function, at module scope only.

    ``tests/property/test_property_coverage.py`` discovers by ``ast.walk``, which counts a nested
    function too, so a helper accidentally named ``test_p31_...`` inside a body would claim P-31
    twice and the scoreboard would report a duplicate rather than a gap.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8-sig"), filename=__file__)
    claims = sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and PROPERTY_FUNCTION_PATTERN.match(node.name) is not None
    )
    assert claims == ["test_p31_simulator_agrees_with_the_reference_ledger"], (
        f"this module claims {claims}; P-31 is the one property it may claim"
    )
