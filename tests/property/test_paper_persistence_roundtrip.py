"""
tests/property/test_paper_persistence_roundtrip.py - P-32 (round-trip, persistence).

Feature: marketplace-subscriptions-paper-trading, task 23.6.
Design reference: ``design.md § Property-to-test mapping`` ->
``P-32 | tests/property/test_paper_persistence_roundtrip.py | session states | write-then-read
equality as exact ``Decimal``, with every in-process cache cleared between``.

Property living here
--------------------
``test_p32_session_state_round_trips_without_precision_loss``   (Requirements 17.2, 17.11, 18.1)

One property, one test function, so the scoreboard in ``tests/property/test_property_coverage.py``
reads exactly one ``test_p32_`` name. Nothing nested inside it carries that prefix, because
``ast.walk`` would count a nested function too. Everything else in this module is a deterministic
companion that records what this property does and does not demonstrate.

WHAT P-32 CLAIMS
----------------
For all generated Paper_Session states: persisting the state and reading it back yields balances,
positions, orders, fills, trades and equity snapshots equal to the values persisted, with no
precision loss.

All six categories are written on every example - ``min_size=1`` everywhere, so an empty-input
pass is structurally impossible - and every one of them is read back through
``paper_repository``, which is the Persistence_Layer's read surface.

THE COMPARISON IS EXACT ``Decimal`` EQUALITY, AND WHY THAT IS NOT A TOLERANCE
----------------------------------------------------------------------------
Every money, price and quantity column in ``009_paper_trading.sql`` is ``NUMERIC(28,10)``, read
and written as a decimal string so no driver-side ``float`` conversion occurs (009's header, and
``design.md § "Everything financial"``). So the comparison here is ``==`` on ``Decimal``: no
``float``, no ``pytest.approx``, no tolerance, no ``round()``. A tolerance would make the whole
property vacuous - ten fractional digits is precisely what is being claimed.

``NUMERIC(28,10)`` GUARANTEES THE NUMBER, NOT THE DECLARED SCALE
---------------------------------------------------------------
A ``NUMERIC(28,10)`` column coerces what it stores to scale 10, so a value written as ``1.1`` is
read back as ``1.1000000000``. Those are the same number and Python agrees:
``Decimal("1.1") == Decimal("1.1000000000")`` is ``True``, because ``Decimal.__eq__`` compares
values and not representations (verified, and pinned by
:func:`test_decimal_equality_is_numeric_so_a_widened_scale_is_still_the_same_number` below). So
exact ``Decimal`` ``==`` is the equality the schema actually provides - it admits a difference in
*declared scale* and admits no difference at all in *value*.

String equality of the stored representation is therefore deliberately NOT asserted. It would be
an assertion about how the in-process double happens to store ``str(Decimal)``, and the real
column would fail it for every value written at a shorter scale. What is asserted alongside the
equality is that the value read back is exactly representable at scale 10
(``got == got.quantize(1E-10)``) - so a digit beyond the tenth can neither survive nor be
invented.

WHAT IS CLEARED BETWEEN THE WRITE AND THE READ
----------------------------------------------
The whole in-process inventory, enumerated by reading both modules rather than assumed:

1. ``paper_repository._paper_persistence_supported`` / ``_paper_persistence_checked_at`` - the
   migration probe verdict, the only module-level mutable state in ``paper_repository``. Cleared
   with ``repo.reset_persistence_probe()``, and the property asserts the verdict is ``None``
   afterwards *and* that the next read re-took the probe (a ``select`` on ``paper_accounts``
   projecting ``repo.PROBE_SELECT``).
2. ``paper_trading_service._paper_service_instance`` - the module singleton. Set to ``None``, and
   a fresh :class:`PaperTradingService` is built; the property asserts the reader instance is not
   the writer instance.
3. ``PaperTradingService._supabase`` - the bound handle. Unbound, then rebound to the same store,
   because the store is the database and the database is what must survive.
4. ``PaperTradingService._positions_of`` - inspected and found NOT memoised: it is a pure
   transform over the rows handed to it, holding nothing between calls. The class docstring's
   inventory of removed caches (``_accounts``, ``_positions``, ``_orders``, ``_trades``,
   ``_idempotency_cache``, ``_user_locks``, ``_recalculate_account``) is accurate - there is no
   ``lru_cache``, no ``cached_property`` and no dict of figures anywhere in the service.

AND HOW THE READ IS PROVEN TO BE A GENUINE RE-QUERY
---------------------------------------------------
Three separate pieces of evidence, because "the read went to storage" is the load-bearing claim:

* every expected value compared against is the ``Decimal`` the *generator* produced, never a
  value taken out of a row the write returned;
* a ``select`` statement is asserted to have been issued, after the cache clear, on each of the
  six tables - so the answer came from a statement and not from a remembered object;
* the whole read is then done a SECOND time after every dict the first read returned has been
  poisoned. If a read handed back the store's own object graph, the poison would corrupt the
  second read and the comparison would fail. It does not, which is what makes the equality a
  statement about what the Persistence_Layer stored.

The structural reason that last one holds is ``paper_repository._rows``, which ends every
statement with ``[dict(row) for row in data]`` - so no caller of this module ever holds a row the
Persistence_Layer still owns, whatever the driver or the double hands back.
:func:`test_every_read_copies_its_rows_out_of_the_persistence_layer` pins that, because it is the
line that makes the poison round-trip meaningful rather than merely passing.

Each of these was confirmed to bite rather than assumed to: quantizing the write path to two
decimals, routing the write through ``float``, dropping the copy in ``_rows``, turning
``reset_persistence_probe`` into a no-op, and flattening an absent ``unrealized_pnl`` to zero on
read each make this property fail, and each names the column and the two values in its message.

WHAT THIS PROPERTY DOES *NOT* DEMONSTRATE, STATED PLAINLY
---------------------------------------------------------
The Persistence_Layer double is ``tests/test_paper_repository.FakeSupabase`` - the single
sanctioned double in this repository, which enforces the five unique indexes 009 declares. It
stores decimal strings, exactly as the driver transports them, so it cannot lose precision the
way a ``double precision`` column would. What runs here in-process is therefore the
*repository's own conversion and projection path*: ``_numeric`` / ``_optional_numeric`` /
``_minor_units`` / ``_exact_int`` / ``_instant`` on the way in, and the ``*_SELECT`` column lists
and tenant/scope predicates on the way out. A ``float()`` anywhere on either side, a ``round()``,
a column dropped from a projection, a nullable column flattened to a zero, or a scope predicate
that fetched the wrong rows - all of those fail here. That the *column* is ``NUMERIC(28,10)``
rather than ``double precision`` is asserted by ``tests/test_marketplace_paper_schema_contract.py``
against the migration text, which is where a schema claim belongs.

An out-of-range value is not generated. ``NUMERIC(28,10)`` holds at most 18 integral and exactly
10 fractional digits; a wider value is refused by the column with ``numeric field overflow``, and
:func:`test_an_out_of_range_value_is_the_columns_refusal_and_is_never_generated` records that
``paper_repository._numeric`` does not bound it itself. Generating one and asserting it survived
would assert a round-trip the database does not offer.

WHY THERE IS NO ``_run_coroutine`` HERE
---------------------------------------
``paper_repository`` and the read paths of ``paper_trading_service`` are synchronous throughout -
by design, because ``routers/risk.py`` calls the service's read methods synchronously from inside
``async def`` handlers. No coroutine is driven by this file, so the loop leak that
``tests/test_settlement_service._run_coroutine`` exists to prevent cannot arise, and
``asyncio.run`` appears nowhere in it.

NON-VACUITY
-----------
A round-trip property passes for free on an empty state, on a state of nothing but zeros, and on
a state whose values need only two decimal places. So the run keeps a census and asserts floors
on it at the end: every category wrote rows on every example; the tenth fractional digit was
exercised; values carrying trailing zeros at scale 10 were persisted beside the same numbers at
a shorter scale; values within a factor of ten of the 28-digit ceiling were persisted, and the
ceiling itself; negative values were persisted (realized PnL, and the signed equity figures);
short positions and closed-at-zero positions occurred; and each of the three order fill outcomes
occurred. A flaky bucket is FORCED by the generator, never traded away by lowering a floor.
The same buckets are also emitted as Hypothesis ``event`` labels, so
``--hypothesis-show-statistics`` prints the observed distribution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from backend_app.backend import paper_trading_service as paper_service_module
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.paper_accounting import InvalidQuantity
from backend_app.backend.paper.paper_order_state import (
    LEGACY_STATUS_FOR_STATE,
    PaperOrderState,
)
from tests.paper_seed import bind_paper_persistence, release_paper_persistence
from tests.property.paper_census import publish_hypothesis_statistics
from tests.strategies.paper_generators import assert_no_binary_floats

#: The configuration ``design.md § Property-based testing configuration`` prescribes for every
#: property test in this plan: at least 100 examples and no per-example deadline. One example
#: drives on the order of a hundred statements against the double, which is slow rather than
#: wrong, so ``too_slow`` is suppressed instead of the example count being cut.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

USER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SESSION = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CURRENCY = "USD"

#: A working precision comfortably above the 28 digits ``NUMERIC(28,10)`` can hold, so a
#: ``quantize`` or a ``normalize`` in the assertions below is exact rather than rounded at the
#: ambient context's 28. The same reason ``paper_accounting`` sets 34 explicitly.
ASSERTION_PRECISION = 40

# ══════════════════════════════════════════════════════════════════════════
# THE COLUMN SPACE ``NUMERIC(28,10)`` DEFINES
# ══════════════════════════════════════════════════════════════════════════

#: ``NUMERIC(28, 10)`` - 009 declares every balance, capital, price, quantity, pnl and equity
#: column with exactly this type. 28 total digits, 10 of them fractional, so 18 integral.
NUMERIC_PRECISION = 28
NUMERIC_SCALE = 10
MAX_INTEGRAL_DIGITS = NUMERIC_PRECISION - NUMERIC_SCALE

#: The scale-10 quantum, and the largest magnitude the column can hold.
QUANTUM = Decimal("1E-10")
CEILING = Decimal("999999999999999999.9999999999")

#: Within a factor of ten of the integral ceiling: 10**17, i.e. 18 integral digits.
NEAR_CEILING = Decimal("100000000000000000")

#: ``BIGINT`` and ``INTEGER`` ceilings, for the exact-integer columns: ``fee_minor`` and
#: ``slippage_minor`` are ``BIGINT`` Minor_Units, ``series_index`` is ``INTEGER``.
BIGINT_MAX = 2 ** 63 - 1
INT_MAX = 2 ** 31 - 1

#: ``(unscaled_coefficient, scale)`` pairs that pin the corners of the column space. Sampled at
#: roughly half of every figure draw, which is what makes the corner cases occur at a rate no
#: uniform integer draw would deliver.
#:
#: ``(11, 1)`` and ``(11 * 10**9, 10)`` are the SAME NUMBER at two declared scales - ``1.1`` and
#: ``1.1000000000`` - which is the pair the trailing-zero question is about.
EDGE_FIGURES: Tuple[Tuple[int, int], ...] = (
    (0, 0),                                        # 0
    (0, NUMERIC_SCALE),                            # 0E-10  - zero declared at full scale
    (1, NUMERIC_SCALE),                            # 0.0000000001 - the smallest representable
    (10 ** NUMERIC_SCALE, NUMERIC_SCALE),          # 1.0000000000
    (11, 1),                                       # 1.1
    (11 * 10 ** (NUMERIC_SCALE - 1), NUMERIC_SCALE),   # 1.1000000000 - the same number
    (123456789, NUMERIC_SCALE),                    # 0.0123456789 - ten fractional digits
    (10 ** NUMERIC_PRECISION - 1, NUMERIC_SCALE),  # 999999999999999999.9999999999 - the ceiling
    (10 ** MAX_INTEGRAL_DIGITS - 1, 0),            # 999999999999999999 - max integral, scale 0
    (10 ** (NUMERIC_PRECISION - 1) + 1, NUMERIC_SCALE),  # 100000000000000000.0000000001
)

#: ``fee_minor`` / ``slippage_minor`` corners: nothing, one unit, and the ``BIGINT`` ceiling.
EDGE_MINOR_UNITS: Tuple[int, ...] = (0, 1, BIGINT_MAX)

#: ``series_index`` corners, including the ``INTEGER`` ceiling.
EDGE_SERIES_INDEX: Tuple[int, ...] = (0, 1, INT_MAX)

SYMBOLS: Tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT")
POSITION_SIDES: Tuple[str, ...] = ("LONG", "SHORT")
ORDER_SIDES: Tuple[str, ...] = ("buy", "sell")
ORDER_TYPES: Tuple[str, ...] = ("market", "limit")

#: ``chk_paper_equity_cause``, verbatim.
EQUITY_CAUSES: Tuple[str, ...] = (
    "SESSION_START",
    "FILL",
    "FEE",
    "REVALUATION",
    "SESSION_STOP",
)

BASE_INSTANT = datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# READING A ``Decimal`` WITHOUT DOING ARITHMETIC TO IT
# ══════════════════════════════════════════════════════════════════════════
#
# Every classifier below reads ``as_tuple()`` rather than computing with the value. A remainder
# or a division would be a context operation, and a classifier that rounded its own input would
# mis-report the very digit the census exists to count.


def declared_scale(value: Decimal) -> int:
    """The number of fractional digits the value's representation declares."""
    return max(0, -value.as_tuple().exponent)


def significant_scale(value: Decimal) -> int:
    """The fractional digits the value actually needs, trailing zeros removed."""
    if value == 0:
        return 0
    with localcontext() as ctx:
        ctx.prec = ASSERTION_PRECISION
        return max(0, -value.normalize().as_tuple().exponent)


def uses_tenth_fractional_digit(value: Decimal) -> bool:
    """Whether the tenth fractional digit - the last one ``NUMERIC(28,10)`` holds - is non-zero.

    The coefficient's last digit *is* the tenth fractional digit exactly when the exponent is
    ``-10``; at any shorter scale that digit is a zero the column will supply.
    """
    parts = value.as_tuple()
    return -parts.exponent == NUMERIC_SCALE and parts.digits[-1] != 0


def carries_trailing_zeros(value: Decimal) -> bool:
    """Whether the value is written at a wider scale than the number needs (``1.1000000000``)."""
    return declared_scale(value) > significant_scale(value)


def within_numeric_28_10(value: Decimal) -> bool:
    """Whether ``value`` is inside what a ``NUMERIC(28, 10)`` column can hold.

    At most 10 fractional digits and at most 18 integral ones. A value outside this is refused by
    the column, so the generators never produce one - see
    :func:`test_an_out_of_range_value_is_the_columns_refusal_and_is_never_generated`.
    """
    parts = value.as_tuple()
    if parts.exponent > 0:
        # A positive exponent means implied trailing zeros: 1E+2 is 100. Count them as integral
        # digits rather than reading len(digits) and understating the magnitude.
        integral = len(parts.digits) + parts.exponent
        return integral <= MAX_INTEGRAL_DIGITS
    scale = -parts.exponent
    integral = len(parts.digits) - scale
    return scale <= NUMERIC_SCALE and integral <= MAX_INTEGRAL_DIGITS


# ══════════════════════════════════════════════════════════════════════════
# GENERATORS - THE COLUMN SPACE, NOT A REALISTIC PRICE RANGE
# ══════════════════════════════════════════════════════════════════════════
#
# ``tests/strategies/paper_generators`` draws prices at ``PRICE_PRECISION = 2`` and quantities at
# ``QUANTITY_PRECISION = 8``, which is the *reported* scale of a session's figures and the right
# space for the accounting properties (P-25 ... P-31). P-32 is about the STORAGE contract, so it
# needs the full ``NUMERIC(28,10)`` space instead: ten fractional digits, eighteen integral ones,
# and both signs. Reusing the accounting generators here would silently cap the claim at eight
# fractional digits and would never approach the precision limit.


def _exact(unscaled: int, scale: int, *, negative: bool = False) -> Decimal:
    """``unscaled * 10**-scale`` as an exact ``Decimal``, built from its string form.

    String construction is context-free, so no ambient precision can round a 28-digit
    coefficient on the way in - which ``Decimal(unscaled).scaleb(-scale)`` would be exposed to.
    """
    return Decimal(f"{'-' if negative else ''}{unscaled}E-{scale}")


@st.composite
def figures(
    draw: Any,
    *,
    signed: bool = False,
    strictly_positive: bool = False,
) -> Decimal:
    """A value a ``NUMERIC(28, 10)`` column can hold, exactly.

    Args:
        signed: admit negative values - realized PnL, an equity figure below zero.
        strictly_positive: the column's check constraint requires ``> 0`` (a price, a quantity).
            A drawn zero is FORCED up to one quantum rather than filtered out, so the branch
            cannot become a source of flakiness.
    """
    if draw(st.booleans()):
        unscaled, scale = draw(st.sampled_from(EDGE_FIGURES))
    else:
        scale = draw(st.integers(min_value=0, max_value=NUMERIC_SCALE))
        unscaled = draw(
            st.integers(
                min_value=0,
                max_value=10 ** (MAX_INTEGRAL_DIGITS + scale) - 1,
            )
        )
    if strictly_positive and unscaled == 0:
        unscaled = 1
    negative = signed and unscaled != 0 and draw(st.booleans())
    return _exact(unscaled, scale, negative=negative)


def optional_figures(**kwargs: Any) -> st.SearchStrategy:
    """:func:`figures`, or ``None`` - the columns that are genuinely absent until priced."""
    return st.none() | figures(**kwargs)


def minor_units() -> st.SearchStrategy:
    """An exact non-negative ``BIGINT`` count of Minor_Units, corners included."""
    return st.sampled_from(EDGE_MINOR_UNITS) | st.integers(min_value=0, max_value=10 ** 12)


def series_indexes() -> st.SearchStrategy:
    """An exact non-negative ``INTEGER`` series index, corners included."""
    return st.sampled_from(EDGE_SERIES_INDEX) | st.integers(min_value=0, max_value=1000)


def _instant_at(offset_seconds: int) -> datetime:
    """A whole-second UTC instant. Whole seconds so the ISO strings sort as they compare."""
    return BASE_INSTANT + timedelta(seconds=offset_seconds)


@st.composite
def balances(draw: Any) -> Dict[str, Decimal]:
    """The account row's five money columns.

    ``available_balance`` and ``locked_balance`` stay at or above zero because
    ``chk_paper_balances_non_negative`` requires it; ``realized_pnl`` and ``total_equity`` are
    signed, because a losing session's are.
    """
    return {
        "initial_capital": draw(figures(strictly_positive=True)),
        "available_balance": draw(figures()),
        "locked_balance": draw(figures()),
        "realized_pnl": draw(figures(signed=True)),
        "total_equity": draw(figures(signed=True)),
    }


@st.composite
def positions(draw: Any) -> List[Dict[str, Any]]:
    """One to three positions, on distinct symbols.

    Distinct symbols because ``uq_paper_position_open`` is UNIQUE ``(account_id, symbol) WHERE
    closed_at IS NULL`` and ``upsert_position`` *updates* the open row when it finds one - so a
    repeated symbol would restate one position instead of persisting two, and the state would
    quietly be smaller than the generator claims.

    A closed position carries ``size`` exactly zero with ``closed_at`` set, which is Requirement
    18.5's shape and the row ``include_closed=True`` reads back.
    """
    symbols = draw(
        st.lists(st.sampled_from(SYMBOLS), min_size=1, max_size=3, unique=True)
    )
    drawn: List[Dict[str, Any]] = []
    for index, symbol in enumerate(symbols):
        closed = draw(st.booleans())
        drawn.append(
            {
                "symbol": symbol,
                "side": draw(st.sampled_from(POSITION_SIDES)),
                "size": Decimal("0") if closed else draw(figures()),
                "entry_price": draw(figures(strictly_positive=True)),
                "current_price": draw(optional_figures(strictly_positive=True)),
                "unrealized_pnl": draw(optional_figures(signed=True)),
                "opened_at": _instant_at(index * 3600),
                "price_at": _instant_at(index * 3600 + 60),
                "closed_at": _instant_at(index * 3600 + 120) if closed else None,
            }
        )
    return drawn


@st.composite
def orders(draw: Any) -> List[Dict[str, Any]]:
    """One to three orders, each with the fill outcome its state machine allows.

    The three outcomes are drawn as a *kind* rather than left to whether one drawn figure
    happened to exceed another, because a full fill would otherwise be rare enough to be a
    coverage gap rather than a case - and the drawn kind is then FORCED, so a partial is a
    partial. ``filled_quantity <= quantity`` is ``chk_paper_order_fill_bound``, which a strict
    partial satisfies.
    """
    count = draw(st.integers(min_value=1, max_value=3))
    drawn: List[Dict[str, Any]] = []
    for index in range(count):
        quantity = draw(figures(strictly_positive=True))
        kind = draw(st.sampled_from(("unfilled", "partial", "full")))
        if kind == "unfilled":
            filled = Decimal("0")
        elif kind == "full":
            filled = quantity
        else:
            # A strict partial: 0 < filled < quantity, exactly, at a scale the column holds.
            #
            # A figure drawn independently of the quantity does NOT deliver one - it lands on
            # zero (an unfilled) or at or above the quantity (a full) often enough that the
            # partial bucket starves, which is the flake this branch used to be. The value is
            # forced instead: the quantity's unscaled integer at scale 10 is the number of
            # quanta it spans, and any k in ``[1, span - 1]`` is a partial NUMERIC(28,10)
            # represents exactly.
            #
            # A quantity of exactly one quantum spans no interior point at all, so the QUANTITY
            # is redrawn to at least two quanta rather than the fill being reclassified - the
            # branch always yields a real partial.
            with localcontext() as ctx:
                ctx.prec = ASSERTION_PRECISION
                # scaleb at 40 digits of precision, so a 28-digit coefficient is shifted
                # exactly rather than rounded at the ambient 28.
                span = int(quantity.scaleb(NUMERIC_SCALE))
                if span < 2:
                    span = draw(
                        st.integers(
                            min_value=2, max_value=10 ** NUMERIC_PRECISION - 1
                        )
                    )
                    quantity = _exact(span, NUMERIC_SCALE)
                filled = _exact(
                    draw(st.integers(min_value=1, max_value=span - 1)), NUMERIC_SCALE
                )

        order_type = draw(st.sampled_from(ORDER_TYPES))
        drawn.append(
            {
                "symbol": draw(st.sampled_from(SYMBOLS)),
                "side": draw(st.sampled_from(ORDER_SIDES)),
                "order_type": order_type,
                "quantity": quantity,
                # chk_paper_order_limit_price: NULL or > 0. A limit order has one by definition.
                "limit_price": (
                    draw(figures(strictly_positive=True)) if order_type == "limit" else None
                ),
                "reference_price": draw(optional_figures(strictly_positive=True)),
                "filled_quantity": filled,
                "avg_fill_price": (
                    None if kind == "unfilled" else draw(figures(strictly_positive=True))
                ),
                "fee_minor": draw(minor_units()),
                "slippage_minor": draw(minor_units()),
                "fill_kind": kind,
                "index": index,
            }
        )
    return drawn


@st.composite
def fills(draw: Any, *, order_count: int) -> List[Dict[str, Any]]:
    """One to three fills, each hanging off one of the generated orders.

    ``fill_event_id`` is distinct per fill, so ``uq_paper_fill_event`` - UNIQUE ``(order_id,
    fill_event_id)`` - never refuses one and the state is the size the generator claims.
    """
    count = draw(st.integers(min_value=1, max_value=3))
    drawn: List[Dict[str, Any]] = []
    for index in range(count):
        drawn.append(
            {
                "order_index": draw(st.integers(min_value=0, max_value=order_count - 1)),
                "fill_event_id": f"p32-fill-{index}",
                "quantity": draw(figures(strictly_positive=True)),
                "price": draw(figures(strictly_positive=True)),
                "fee_minor": draw(minor_units()),
                "slippage_minor": draw(minor_units()),
                "filled_at": _instant_at(7200 + index * 60),
            }
        )
    return drawn


@st.composite
def trades(draw: Any) -> List[Dict[str, Any]]:
    """One to three closed round-trips. ``realized_pnl`` is signed - a losing trade's is."""
    count = draw(st.integers(min_value=1, max_value=3))
    drawn: List[Dict[str, Any]] = []
    for index in range(count):
        drawn.append(
            {
                "symbol": draw(st.sampled_from(SYMBOLS)),
                # 009 declares no check constraint on paper_trades.side, unlike
                # chk_paper_position_side, so the position side that closed is written as-is.
                "side": draw(st.sampled_from(POSITION_SIDES)),
                "quantity": draw(figures(strictly_positive=True)),
                "entry_price": draw(figures(strictly_positive=True)),
                "exit_price": draw(figures(strictly_positive=True)),
                "realized_pnl": draw(figures(signed=True)),
                "fee_minor": draw(minor_units()),
                "opened_at": _instant_at(10800 + index * 120),
                "closed_at": _instant_at(10800 + index * 120 + 60),
            }
        )
    return drawn


@st.composite
def equity_snapshots(draw: Any) -> List[Dict[str, Any]]:
    """One to four equity snapshots. ``total_equity`` and ``position_market_value`` are signed.

    ``position_market_value`` can be negative under the retained SHORT convention
    (``size * (2 * entry_price - price)``), and ``total_equity`` follows it, so both are drawn
    signed. ``available_balance`` and ``locked_balance`` mirror the account's non-negative
    contract.
    """
    count = draw(st.integers(min_value=1, max_value=4))
    drawn: List[Dict[str, Any]] = []
    for index in range(count):
        drawn.append(
            {
                "series_index": draw(series_indexes()),
                "total_equity": draw(figures(signed=True)),
                "available_balance": draw(figures()),
                "locked_balance": draw(figures()),
                "position_market_value": draw(figures(signed=True)),
                "stale": draw(st.booleans()),
                "cause": draw(st.sampled_from(EQUITY_CAUSES)),
                "taken_at": _instant_at(14400 + index * 60),
            }
        )
    return drawn


@st.composite
def session_states(draw: Any) -> Dict[str, Any]:
    """A whole Paper_Session state: all six categories, none of them ever empty."""
    drawn_orders = draw(orders())
    return {
        "balances": draw(balances()),
        "positions": draw(positions()),
        "orders": drawn_orders,
        "fills": draw(fills(order_count=len(drawn_orders))),
        "trades": draw(trades()),
        "snapshots": draw(equity_snapshots()),
    }


# ══════════════════════════════════════════════════════════════════════════
# THE WRITE - THROUGH THE PERSISTENCE_LAYER, AS PRODUCTION WRITES
# ══════════════════════════════════════════════════════════════════════════

#: ``paper_orders`` state after the two transitions each generated order is driven through.
#: ``ACCEPTED`` is the only origin the guard admits for all three, and each is reachable from it
#: under ``PAPER_ORDER_TRANSITIONS`` (Requirement 16.2).
STATE_FOR_FILL_KIND: Mapping[str, PaperOrderState] = {
    "unfilled": PaperOrderState.CANCELLED,
    "partial": PaperOrderState.PARTIALLY_FILLED,
    "full": PaperOrderState.FILLED,
}


def _persist(store: Any, state: Mapping[str, Any]) -> Dict[str, Any]:
    """Write the whole state and return, for each row, only its id and the values persisted.

    The returned structure deliberately carries **no row the write produced** - only the row's
    ``id`` (its key, never a value under test) and the ``Decimal`` / ``int`` / ``str`` objects the
    generator produced. So the later comparison cannot be satisfied by a remembered object graph:
    there is nothing to remember.
    """
    money = state["balances"]

    created = repo.get_or_create_account(
        store, USER, CURRENCY, SESSION, initial_capital=money["initial_capital"]
    )
    account_id = str(created["id"])
    repo.bump_version(
        store,
        user_id=USER,
        account_id=account_id,
        expected_version=created["version"],
        payload={
            "available_balance": money["available_balance"],
            "locked_balance": money["locked_balance"],
            "realized_pnl": money["realized_pnl"],
            "total_equity": money["total_equity"],
        },
    )

    expected: Dict[str, Any] = {
        "account_id": account_id,
        "account": {
            "figures": dict(money),
            # Created at 1 and bumped exactly once, so the counter is 2 by construction rather
            # than by reading it back out of the row the write returned.
            "exact_ints": {"version": 2},
            "texts": {"currency": CURRENCY, "session_id": SESSION, "user_id": USER},
            "flags": {},
        },
        "positions": {},
        "orders": {},
        "fills": {},
        "trades": {},
        "snapshots": {},
    }

    for position in state["positions"]:
        row = repo.upsert_position(
            store,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=position["symbol"],
            side=position["side"],
            size=position["size"],
            entry_price=position["entry_price"],
            current_price=position["current_price"],
            unrealized_pnl=position["unrealized_pnl"],
            opened_at=position["opened_at"],
            price_at=position["price_at"],
            closed_at=position["closed_at"],
        )
        expected["positions"][str(row["id"])] = {
            "figures": {
                "size": position["size"],
                "entry_price": position["entry_price"],
                "current_price": position["current_price"],
                "unrealized_pnl": position["unrealized_pnl"],
            },
            "exact_ints": {"version": 1},
            "texts": {
                "symbol": position["symbol"],
                "side": position["side"],
                "opened_at": _iso(position["opened_at"]),
                "price_at": _iso(position["price_at"]),
                "closed_at": _iso(position["closed_at"]),
            },
            "flags": {},
        }

    order_ids: List[str] = []
    for order in state["orders"]:
        target = STATE_FOR_FILL_KIND[order["fill_kind"]]
        row = repo.insert_order(
            store,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=order["symbol"],
            side=order["side"],
            order_type=order["order_type"],
            quantity=order["quantity"],
            limit_price=order["limit_price"],
            reference_price=order["reference_price"],
            fingerprint=f"p32-order-{order['index']}",
            idempotency_key=f"p32-key-{order['index']}",
            order_state=PaperOrderState.CREATED,
        )
        order_id = str(row["id"])
        order_ids.append(order_id)
        repo.update_order(
            store,
            user_id=USER,
            order_id=order_id,
            order_state=PaperOrderState.ACCEPTED,
            expected_state=PaperOrderState.CREATED,
        )
        repo.update_order(
            store,
            user_id=USER,
            order_id=order_id,
            order_state=target,
            expected_state=PaperOrderState.ACCEPTED,
            filled_quantity=order["filled_quantity"],
            avg_fill_price=order["avg_fill_price"],
            fee_minor=order["fee_minor"],
            slippage_minor=order["slippage_minor"],
        )
        expected["orders"][order_id] = {
            "figures": {
                "quantity": order["quantity"],
                "limit_price": order["limit_price"],
                "reference_price": order["reference_price"],
                "filled_quantity": order["filled_quantity"],
                "avg_fill_price": order["avg_fill_price"],
            },
            "exact_ints": {
                "fee_minor": order["fee_minor"],
                "slippage_minor": order["slippage_minor"],
            },
            "texts": {
                "symbol": order["symbol"],
                "side": order["side"],
                "order_type": order["order_type"],
                "order_state": target.value,
                "legacy_status": LEGACY_STATUS_FOR_STATE[target],
                "idempotency_key": f"p32-key-{order['index']}",
            },
            "flags": {},
        }

    for fill in state["fills"]:
        row = repo.insert_fill(
            store,
            order_id=order_ids[fill["order_index"]],
            user_id=USER,
            session_id=SESSION,
            fill_event_id=fill["fill_event_id"],
            quantity=fill["quantity"],
            price=fill["price"],
            fee_minor=fill["fee_minor"],
            slippage_minor=fill["slippage_minor"],
            filled_at=fill["filled_at"],
        )
        expected["fills"][str(row["id"])] = {
            "figures": {"quantity": fill["quantity"], "price": fill["price"]},
            "exact_ints": {
                "fee_minor": fill["fee_minor"],
                "slippage_minor": fill["slippage_minor"],
            },
            "texts": {
                "order_id": order_ids[fill["order_index"]],
                "fill_event_id": fill["fill_event_id"],
                "filled_at": _iso(fill["filled_at"]),
            },
            "flags": {},
        }

    for trade in state["trades"]:
        row = repo.insert_trade(
            store,
            account_id=account_id,
            user_id=USER,
            session_id=SESSION,
            symbol=trade["symbol"],
            side=trade["side"],
            quantity=trade["quantity"],
            entry_price=trade["entry_price"],
            exit_price=trade["exit_price"],
            realized_pnl=trade["realized_pnl"],
            fee_minor=trade["fee_minor"],
            opened_at=trade["opened_at"],
            closed_at=trade["closed_at"],
        )
        expected["trades"][str(row["id"])] = {
            "figures": {
                "quantity": trade["quantity"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "realized_pnl": trade["realized_pnl"],
            },
            "exact_ints": {"fee_minor": trade["fee_minor"]},
            "texts": {
                "symbol": trade["symbol"],
                "side": trade["side"],
                "opened_at": _iso(trade["opened_at"]),
                "closed_at": _iso(trade["closed_at"]),
            },
            "flags": {},
        }

    for snapshot in state["snapshots"]:
        row = repo.insert_equity_snapshot(
            store,
            user_id=USER,
            session_id=SESSION,
            series_index=snapshot["series_index"],
            total_equity=snapshot["total_equity"],
            available_balance=snapshot["available_balance"],
            locked_balance=snapshot["locked_balance"],
            position_market_value=snapshot["position_market_value"],
            stale=snapshot["stale"],
            cause=snapshot["cause"],
            taken_at=snapshot["taken_at"],
        )
        expected["snapshots"][str(row["id"])] = {
            "figures": {
                "total_equity": snapshot["total_equity"],
                "available_balance": snapshot["available_balance"],
                "locked_balance": snapshot["locked_balance"],
                "position_market_value": snapshot["position_market_value"],
            },
            "exact_ints": {"series_index": snapshot["series_index"]},
            "texts": {
                "cause": snapshot["cause"],
                "taken_at": _iso(snapshot["taken_at"]),
            },
            "flags": {"stale": snapshot["stale"]},
        }

    return expected


def _iso(instant: Optional[datetime]) -> Optional[str]:
    """The UTC ISO-8601 string ``paper_repository._instant`` writes for ``instant``."""
    if instant is None:
        return None
    return instant.astimezone(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════
# THE CACHE CLEAR - THE PROCESS IS REBUILT, THE STORAGE IS NOT
# ══════════════════════════════════════════════════════════════════════════


def _rebuild_the_process(store: Any, writer: Any) -> Any:
    """Drop every in-process cache and return a fresh service bound to the same storage.

    Modelling a restart, which is the condition Requirement 17.2 is about: the ``paper_*`` rows
    survive and nothing else does.
    """
    # 1. The bound handle goes, and the migration probe verdict with it.
    release_paper_persistence(writer)
    assert repo._paper_persistence_supported is None, (
        "reset_persistence_probe left a cached migration verdict, so the next read would skip "
        "the probe and the clear would be incomplete"
    )

    # 2. The module singleton goes, so nothing can reach the instance that did the writing.
    paper_service_module._paper_service_instance = None
    reader = paper_service_module.get_paper_trading_service()
    assert reader is not writer, (
        "the reader is the same PaperTradingService instance that wrote, so any instance state "
        "would be indistinguishable from storage"
    )

    # 3. The storage - and only the storage - is handed to the new instance.
    reader.bind_persistence(store)
    repo.reset_persistence_probe()
    return reader


def _read_persisted(reader: Any) -> Dict[str, Any]:
    """Read all six categories back through the Persistence_Layer."""
    store = reader._supabase
    account = repo.read_account(store, USER, CURRENCY, SESSION)
    assert account is not None, (
        "the account written for this session is not readable, so there is no round trip to "
        "assert - the write did not persist or the read is scoped wrongly"
    )
    account_id = str(account["id"])
    return {
        "account_id": account_id,
        "account": account,
        "positions": repo.get_positions(
            store,
            USER,
            account_id=account_id,
            session_id=SESSION,
            include_closed=True,
        ),
        "orders": repo.get_orders(store, USER, account_id=account_id, session_id=SESSION),
        "fills": repo.get_fills(store, USER, session_id=SESSION),
        "trades": repo.get_trades(store, USER, account_id=account_id, session_id=SESSION),
        "snapshots": repo.get_equity_snapshots(store, USER, session_id=SESSION),
    }


#: ``(table, key in the read structure)`` for each of the six categories, so "a statement was
#: issued for this category" is asserted from the table name the double recorded.
READ_TABLES: Tuple[Tuple[str, str], ...] = (
    (repo.ACCOUNTS_TABLE, "account"),
    (repo.POSITIONS_TABLE, "positions"),
    (repo.ORDERS_TABLE, "orders"),
    (repo.FILLS_TABLE, "fills"),
    (repo.TRADES_TABLE, "trades"),
    (repo.EQUITY_SNAPSHOTS_TABLE, "snapshots"),
)


def _assert_the_read_was_a_statement(store: Any, mark: int) -> None:
    """Assert the read re-queried: a probe, plus a ``select`` on each of the six tables.

    The probe is expected because the caller resets the verdict immediately before every read,
    so its absence here means ``reset_persistence_probe`` did not take effect and a stale
    in-process verdict was serving reads.
    """
    issued = store.statements[mark:]
    selects = [s for s in issued if s.op == "select"]

    assert any(
        s.table_name == repo.ACCOUNTS_TABLE and s.cols == repo.PROBE_SELECT for s in selects
    ), (
        "no migration probe was re-taken after the cache clear, so paper_repository answered "
        "from the cached verdict and the clear did not take effect"
    )

    for table, _ in READ_TABLES:
        assert any(s.table_name == table for s in selects), (
            f"no select was issued against {table} after the cache clear, so whatever was "
            "compared did not come out of the Persistence_Layer"
        )


def _poison(read_back: Mapping[str, Any]) -> None:
    """Overwrite every value in the rows the read returned.

    If a read handed back the store's own object graph rather than a copy, this corrupts the
    stored rows and the second read fails. That is the point: it turns "the read re-queries" from
    an assumption into an assertion.
    """
    rows: List[Dict[str, Any]] = [read_back["account"]]
    for _, key in READ_TABLES[1:]:
        rows.extend(read_back[key])
    for row in rows:
        for column in list(row):
            row[column] = "POISONED"


# ══════════════════════════════════════════════════════════════════════════
# THE COMPARISON - EXACT ``Decimal``, NOTHING ELSE
# ══════════════════════════════════════════════════════════════════════════


def _assert_figure_round_tripped(written: Optional[Decimal], read_value: Any, where: str) -> None:
    """``read_value`` is exactly the number ``written`` was, or both are absent."""
    if written is None:
        assert read_value is None, (
            f"{where}: nothing was persisted, but {read_value!r} was read back - Requirement "
            "18.15 forbids substituting a figure for an absent one"
        )
        return

    assert read_value is not None, (
        f"{where}: {written} was persisted and nothing was read back"
    )
    assert not isinstance(read_value, float), (
        f"{where}: read back as a float ({read_value!r}); every NUMERIC(28,10) column is "
        "transported as a decimal string precisely so no binary float exists (Requirement 18.1)"
    )
    got = read_value if isinstance(read_value, Decimal) else Decimal(str(read_value))

    assert got == written, (
        f"{where}: persisted {written}, read back {got}; the difference is "
        f"{'a sign' if -got == written else 'a value'} and NUMERIC(28,10) loses neither"
    )
    with localcontext() as ctx:
        ctx.prec = ASSERTION_PRECISION
        assert got == got.quantize(QUANTUM), (
            f"{where}: {got} is not representable at NUMERIC(28,10)'s scale of "
            f"{NUMERIC_SCALE}, so a digit beyond the tenth was either kept or invented"
        )


def _assert_int_round_tripped(written: int, read_value: Any, where: str) -> None:
    """``read_value`` is exactly the integer ``written`` was - no rounding, no float."""
    assert isinstance(read_value, int) and not isinstance(read_value, bool), (
        f"{where}: {written} was persisted as an exact integer and {read_value!r} "
        f"({type(read_value).__name__}) was read back"
    )
    assert read_value == written, f"{where}: persisted {written}, read back {read_value}"


def _assert_rows_round_tripped(
    expected: Mapping[str, Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    category: str,
) -> None:
    """Every persisted row is readable exactly once, with every value it was persisted with."""
    by_id = {str(row["id"]): row for row in rows}
    assert set(by_id) == set(expected), (
        f"{category}: persisted {sorted(expected)} and read back {sorted(by_id)}"
    )
    for row_id, claim in expected.items():
        row = by_id[row_id]
        for column, written in claim["figures"].items():
            _assert_figure_round_tripped(written, row.get(column), f"{category}.{column}")
        for column, written in claim["exact_ints"].items():
            _assert_int_round_tripped(written, row.get(column), f"{category}.{column}")
        for column, written in claim["texts"].items():
            assert row.get(column) == written, (
                f"{category}.{column}: persisted {written!r}, read back {row.get(column)!r}"
            )
        for column, written in claim["flags"].items():
            assert row.get(column) is written, (
                f"{category}.{column}: persisted {written!r}, read back {row.get(column)!r}"
            )


def _assert_state_round_tripped(expected: Mapping[str, Any], read_back: Mapping[str, Any]) -> None:
    """The whole state: balances, positions, orders, fills, trades, equity snapshots."""
    assert read_back["account_id"] == expected["account_id"], (
        "a different account was read back than the one written, so nothing below would be "
        "about this session's balances (Requirement 17.6)"
    )
    _assert_rows_round_tripped(
        {expected["account_id"]: expected["account"]}, [read_back["account"]], "balances"
    )
    for _, key in READ_TABLES[1:]:
        _assert_rows_round_tripped(expected[key], read_back[key], key)


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES THE RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════

CENSUS_KEYS: Tuple[str, ...] = (
    "examples",
    "figures",
    "tenth_fractional_digit",
    "trailing_zeros_at_full_scale",
    "short_scale",
    "near_the_precision_ceiling",
    "at_the_precision_ceiling",
    "negative",
    "absent_optional_column",
    "bigint_ceiling_minor_units",
    "short_position",
    "closed_position_at_zero",
    "order_unfilled",
    "order_partially_filled",
    "order_fully_filled",
    "multi_row_category",
)

#: The census buckets a run must fill, and the floor for each. Chosen well below what a healthy
#: run delivers (the generator samples the corner pool at roughly half of every figure draw, and
#: an example draws on the order of twenty-five figures) and far above what a degenerate run
#: would. A bucket that under-fills is FORCED by the generator, never traded away by lowering
#: the number here.
CENSUS_FLOORS: Mapping[str, int] = {
    "examples": 100,
    "tenth_fractional_digit": 25,
    "trailing_zeros_at_full_scale": 25,
    "short_scale": 25,
    "near_the_precision_ceiling": 25,
    "at_the_precision_ceiling": 10,
    "negative": 25,
    "absent_optional_column": 25,
    "bigint_ceiling_minor_units": 10,
    "short_position": 25,
    "closed_position_at_zero": 25,
    "order_unfilled": 10,
    "order_partially_filled": 10,
    "order_fully_filled": 10,
    "multi_row_category": 25,
}

#: The census bucket each generated order fill outcome lands in.
CENSUS_KEY_FOR_FILL_KIND: Mapping[str, str] = {
    "unfilled": "order_unfilled",
    "partial": "order_partially_filled",
    "full": "order_fully_filled",
}

#: The census buckets that are also emitted as a Hypothesis ``event``, so the observed
#: distribution is printed by ``--hypothesis-show-statistics`` and not only asserted.
EVENT_LABELS: Mapping[str, str] = {
    "tenth_fractional_digit": "a value used the tenth fractional digit",
    "trailing_zeros_at_full_scale": "a value carried trailing zeros at scale 10",
    "short_scale": "a value was written at a scale below 10",
    "near_the_precision_ceiling": "a value had 18 integral digits",
    "at_the_precision_ceiling": "a value was the NUMERIC(28,10) ceiling",
    "negative": "a negative value was persisted",
    "absent_optional_column": "an optional numeric column was persisted absent",
    "bigint_ceiling_minor_units": "a Minor_Units column was the BIGINT ceiling",
    "short_position": "a SHORT position was persisted",
    "closed_position_at_zero": "a position was persisted closed at size zero",
}


def _new_census() -> Dict[str, int]:
    return {key: 0 for key in CENSUS_KEYS}


def _record(census: Dict[str, int], state: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    """Count what this example actually exercised, and emit the same reading as events."""
    census["examples"] += 1

    seen: Dict[str, bool] = {key: False for key in EVENT_LABELS}

    claims = [expected["account"]]
    for _, key in READ_TABLES[1:]:
        claims.extend(expected[key].values())

    for claim in claims:
        for value in claim["figures"].values():
            if value is None:
                census["absent_optional_column"] += 1
                seen["absent_optional_column"] = True
                continue
            assert within_numeric_28_10(value), (
                f"the generator produced {value}, which NUMERIC(28,10) cannot hold; an "
                "out-of-range value is the column's refusal and must never be asserted to "
                "round-trip"
            )
            census["figures"] += 1
            if uses_tenth_fractional_digit(value):
                census["tenth_fractional_digit"] += 1
                seen["tenth_fractional_digit"] = True
            if declared_scale(value) == NUMERIC_SCALE and carries_trailing_zeros(value):
                census["trailing_zeros_at_full_scale"] += 1
                seen["trailing_zeros_at_full_scale"] = True
            if declared_scale(value) < NUMERIC_SCALE:
                census["short_scale"] += 1
                seen["short_scale"] = True
            if abs(value) >= NEAR_CEILING:
                census["near_the_precision_ceiling"] += 1
                seen["near_the_precision_ceiling"] = True
            if abs(value) == CEILING:
                census["at_the_precision_ceiling"] += 1
                seen["at_the_precision_ceiling"] = True
            if value < 0:
                census["negative"] += 1
                seen["negative"] = True
        for value in claim["exact_ints"].values():
            if value == BIGINT_MAX:
                census["bigint_ceiling_minor_units"] += 1
                seen["bigint_ceiling_minor_units"] = True

    for position in state["positions"]:
        if position["side"] == "SHORT":
            census["short_position"] += 1
            seen["short_position"] = True
        if position["closed_at"] is not None:
            assert position["size"] == 0, (
                "a closed position must persist at exactly zero (Requirement 18.5)"
            )
            census["closed_position_at_zero"] += 1
            seen["closed_position_at_zero"] = True

    for order in state["orders"]:
        census[CENSUS_KEY_FOR_FILL_KIND[order["fill_kind"]]] += 1

    if any(len(state[key]) > 1 for key in ("positions", "orders", "fills", "trades", "snapshots")):
        census["multi_row_category"] += 1

    for key, label in EVENT_LABELS.items():
        if seen[key]:
            event(label)


# ══════════════════════════════════════════════════════════════════════════
# P-32
# ══════════════════════════════════════════════════════════════════════════


#: The helper moved to ``tests/property/paper_census.py`` at tasks 25.11-25.13, when the fourth
#: paper property module needed the same nested-``check`` statistics collector. Four copies of it
#: were four chances to fix only one. The alias is kept so the call site below still reads the way
#: this module describes it.
_publish_hypothesis_statistics = publish_hypothesis_statistics


def test_p32_session_state_round_trips_without_precision_loss(request: Any) -> None:
    """Persisting a Paper_Session state and reading it back returns exactly what was persisted.

    Balances, positions, orders, fills, trades and equity snapshots, compared as exact
    ``Decimal`` with no tolerance, after every in-process cache has been cleared - so the
    equality is a statement about ``NUMERIC(28,10)`` and not about a memoised object.

    **Validates: Requirements 17.2, 17.11, 18.1**
    """
    census = _new_census()
    original_singleton = paper_service_module._paper_service_instance

    @PROPERTY_SETTINGS
    @given(state=session_states())
    def check(state: Mapping[str, Any]) -> None:
        # Requirement 18.1 read literally: no binary float exists anywhere in the input.
        assert_no_binary_floats(state)

        paper_service_module._paper_service_instance = None
        writer = paper_service_module.get_paper_trading_service()
        store = bind_paper_persistence(writer)
        reader = writer
        try:
            expected = _persist(store, state)
            _record(census, state, expected)

            # ── The restart: every in-process cache goes, the rows stay. ──
            reader = _rebuild_the_process(store, writer)

            mark = len(store.statements)
            read_back = _read_persisted(reader)
            _assert_the_read_was_a_statement(store, mark)
            _assert_state_round_tripped(expected, read_back)

            # ── And again, after poisoning everything the first read handed out. A read that
            # ── returned the store's own objects would now fail; one that re-queries cannot.
            # ── The probe verdict is dropped a second time so the read is re-taken from scratch
            # ── rather than trusting the verdict the first read cached.
            _poison(read_back)
            repo.reset_persistence_probe()
            mark = len(store.statements)
            second = _read_persisted(reader)
            _assert_the_read_was_a_statement(store, mark)
            _assert_state_round_tripped(expected, second)
        finally:
            release_paper_persistence(reader)
            paper_service_module._paper_service_instance = None

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        paper_service_module._paper_service_instance = original_singleton
        repo.reset_persistence_probe()

    # ── Non-vacuity: the run actually reached every corner the claim rests on. ──
    shortfalls = {
        key: (census[key], floor)
        for key, floor in CENSUS_FLOORS.items()
        if census[key] < floor
    }
    assert not shortfalls, (
        "P-32 would be vacuous: "
        + ", ".join(
            f"{key} occurred {seen} time(s), floor {floor}"
            for key, (seen, floor) in sorted(shortfalls.items())
        )
        + f" -- full census {census}. Fix a shortfall by FORCING the case in the generator, "
        "never by lowering the floor."
    )


# ══════════════════════════════════════════════════════════════════════════
# DETERMINISTIC COMPANIONS - WHAT THE PROPERTY RESTS ON, AND WHAT IT DOES NOT CLAIM
# ══════════════════════════════════════════════════════════════════════════


def test_decimal_equality_is_numeric_so_a_widened_scale_is_still_the_same_number() -> None:
    """``==`` on ``Decimal`` compares values, which is the equality ``NUMERIC(28,10)`` provides.

    A ``NUMERIC(28,10)`` column coerces what it stores to scale 10, so ``1.1`` is read back as
    ``1.1000000000``. That is the same number and this is the assertion P-32 makes. It is NOT a
    tolerance: a difference of one quantum at the tenth digit is still a difference, and is
    asserted here to be one.
    """
    assert Decimal("1.1") == Decimal("1.1000000000")
    assert Decimal("1.10") == Decimal("1.1")
    assert Decimal("0") == Decimal("0E-10")

    # ...and the equality is exact, so the smallest representable difference is a difference.
    assert Decimal("1.1000000000") != Decimal("1.1000000001")
    assert CEILING != CEILING - QUANTUM

    # The representations differ, which is why P-32 does not assert string equality.
    assert str(Decimal("1.1")) != str(Decimal("1.1000000000"))


def test_the_classifiers_read_the_representation_they_claim_to_read() -> None:
    """The census counts what it says it counts - otherwise the floors mean nothing."""
    assert uses_tenth_fractional_digit(Decimal("0.0000000001"))
    assert uses_tenth_fractional_digit(Decimal("100000000000000000.0000000001"))
    assert not uses_tenth_fractional_digit(Decimal("1.1000000000"))
    assert not uses_tenth_fractional_digit(Decimal("1.1"))

    assert carries_trailing_zeros(Decimal("1.1000000000"))
    assert carries_trailing_zeros(Decimal("0E-10"))
    assert not carries_trailing_zeros(Decimal("1.1"))
    assert not carries_trailing_zeros(Decimal("0.0000000001"))

    assert declared_scale(Decimal("1.1000000000")) == 10
    assert declared_scale(Decimal("1.1")) == 1
    assert declared_scale(Decimal("11")) == 0

    assert abs(CEILING) == CEILING
    assert abs(Decimal("999999999999999999")) >= NEAR_CEILING


def test_an_out_of_range_value_is_the_columns_refusal_and_is_never_generated() -> None:
    """A value wider than ``NUMERIC(28,10)`` is refused by the column, not by the repository.

    Recorded rather than glossed. ``paper_repository._numeric`` validates *exactness* - it routes
    through ``paper_accounting.to_decimal``, which refuses a ``float`` outright (Requirement
    18.1) - and it does not bound the magnitude or the scale. A 19-integral-digit value or an
    eleventh fractional digit is therefore accepted here and refused by the column with
    ``numeric field overflow`` / a silent rescale, which is a *database* guarantee asserted
    against the migration text by ``tests/test_marketplace_paper_schema_contract.py``.

    So P-32 generates only in-range values, and :func:`_record` asserts that on every example.
    Generating a wider one and asserting it survived would assert a round trip the database does
    not offer.
    """
    assert within_numeric_28_10(CEILING)
    assert within_numeric_28_10(Decimal("999999999999999999"))
    assert within_numeric_28_10(Decimal("0E-10"))

    too_many_integral_digits = Decimal("1000000000000000000")  # 19 digits
    too_many_fractional_digits = Decimal("0.00000000001")      # 11 digits
    assert not within_numeric_28_10(too_many_integral_digits)
    assert not within_numeric_28_10(too_many_fractional_digits)

    # The repository passes both through: the bound lives in the column, and this is the record
    # of where it lives.
    assert repo._numeric(too_many_integral_digits, "probe") == str(too_many_integral_digits)
    assert repo._numeric(too_many_fractional_digits, "probe") == str(
        too_many_fractional_digits
    )

    # What the repository DOES refuse, on the same call, is the inexact value Requirement 18.1
    # is about.
    with pytest.raises(InvalidQuantity):
        repo._numeric(0.1, "probe")


def test_every_read_copies_its_rows_out_of_the_persistence_layer() -> None:
    """``paper_repository._rows`` copies, so a caller never holds a row the storage still owns.

    This is the line that makes P-32's poison round-trip meaningful. ``_rows`` ends every
    statement with ``[dict(row) for row in data]``, so mutating what a read returned cannot
    change what the next read returns - which is exactly the guarantee a caller of a database
    has, and the reason "the read re-queried" can be asserted rather than hoped for.

    It is worth pinning on its own: if the copy were dropped as a micro-optimisation, the paper
    read paths would start handing out live handles to whatever the driver cached, and P-32's
    second read would silently become an assertion about the first read's objects.
    """
    from tests.test_paper_repository import FakeSupabase

    repo.reset_persistence_probe()
    store = FakeSupabase()
    try:
        account = repo.get_or_create_account(store, USER, CURRENCY, SESSION)
        stored = [row for row in store.accounts if str(row["id"]) == str(account["id"])]
        assert len(stored) == 1

        first = repo.read_account(store, USER, CURRENCY, SESSION)
        assert first is not None
        assert first is not stored[0], "read_account returned the row the store still owns"

        first["available_balance"] = "POISONED"
        first["user_id"] = "POISONED"

        second = repo.read_account(store, USER, CURRENCY, SESSION)
        assert second is not None, (
            "poisoning what the first read returned made the account unreadable, so the read "
            "hands out the Persistence_Layer's own rows"
        )
        assert second["available_balance"] == account["available_balance"]
    finally:
        repo.reset_persistence_probe()


def test_neither_paper_module_holds_a_figure_between_calls() -> None:
    """The in-process cache inventory P-32's clear is built from, asserted rather than assumed.

    ``paper_repository`` carries exactly one piece of module-level mutable state - the migration
    probe verdict - and ``PaperTradingService`` carries the configuration and the bound handle.
    If either grew a dict of balances, positions or orders, clearing the probe and rebuilding the
    instance would no longer be a complete clear and P-32's equality could be satisfied from
    memory. This is the guard on that.
    """
    repo.reset_persistence_probe()
    assert repo._paper_persistence_supported is None
    assert repo._paper_persistence_checked_at == 0.0

    service = paper_service_module.PaperTradingService()
    held = {
        name: value
        for name, value in vars(service).items()
        if isinstance(value, (dict, list, set))
    }
    assert not held, (
        "PaperTradingService now holds a mutable collection per instance "
        f"({sorted(held)}); if it carries figures, P-32's cache clear is incomplete and the "
        "round trip could be satisfied from process memory (Requirements 17.2, 28.3)"
    )
    assert service._supabase is None
    # _positions_of is a pure transform over the rows handed to it: same rows in, equal value
    # objects out, and nothing retained between the two calls.
    rows = [
        {
            "symbol": "BTC/USDT",
            "side": "LONG",
            "size": "1.5000000000",
            "entry_price": "30000.0000000000",
            "current_price": None,
            "unrealized_pnl": None,
            "price_at": None,
            "opened_at": _iso(BASE_INSTANT),
            "closed_at": None,
        }
    ]
    first, _ = service._positions_of(rows)
    second, _ = service._positions_of([])
    assert set(first) == {"BTC/USDT"}
    assert second == {}, (
        "_positions_of returned positions for an empty row set, so it is memoising - which "
        "would make a read after a cache clear answer from the previous call"
    )
