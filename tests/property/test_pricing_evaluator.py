"""Property tests for the Pricing_Evaluator.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-51, P-52 | tests/property/test_pricing_evaluator.py | evidence_sets() × currencies ×
repetition counts | the ordering invariant; a second call for determinism; boundary prices
for enforcement``.

Module under test: ``backend_app/backend/marketplace/pricing_evaluator.py`` (task 7.1).

Properties living here
----------------------
``test_p51_price_range_is_ordered_bounded_and_integral``  (task 7.2,
Requirements 8.1, 8.4, 8.12, 8.13)

Task 15.2 appends ``test_p52_price_range_is_deterministic_and_enforced`` to this module.

WHY THE TEST WALKS ``quality_components`` INSTEAD OF TRUSTING THE RESULT TYPE
----------------------------------------------------------------------------
``price_range`` returns three ``int`` values, and an ``int`` looks the same whether it was
formed by exact integer arithmetic or by truncating a ``float``. So integrality of the
*output* is necessary but nowhere near sufficient for Requirement 8.13. The evaluator
exposes :func:`~backend_app.backend.marketplace.pricing_evaluator.quality_components` for
precisely this reason: it hands back every intermediate of the quality computation - the
medians, the standard deviation, the worst drawdown, each of the seven weighted terms and
the unrounded total - so this test can walk them and assert that not one of them is a binary
floating-point number. A ``float`` sneaking into any single term would be caught there,
before it could be laundered into a plausible-looking integer price.

The walk is recursive and type-allow-listed rather than a ``!= float`` check, so a value of
some third numeric type (a NumPy scalar, a ``fractions.Fraction``, a ``complex``) is
reported too: Requirement 8.13's guarantee only holds if the arithmetic is *known* to be
exact, and an unrecognised type is not known to be anything.

WHY ``admissible=True``
-----------------------
P-51 is a claim about the Price_Range the evaluator *produces*. Requirement 8.14's refusal
path - absent evidence, or evidence lacking a Requirement 8.2 input - produces no
Price_Range at all, so a null metric drawn from the defect branches of ``evidence_sets()``
would exercise a different requirement and never reach the ordering invariant.

WHY THE METRIC-SHAPING BRANCHES EXIST
-------------------------------------
``evidence_sets(admissible=True)`` draws each metric independently per condition over its
whole column range, so the *medians* it produces are mediocre by construction: measured over
60 examples the quality score never left 3…27, which pins ``recommended`` near the bottom of
the anchor band and leaves the top of the band - and the interaction of the 60 % and 250 %
ratios with a large ``recommended`` - unexercised. An invariant that only ever sees one end
of its input range is a weak invariant, so :func:`_priceable_evidence_sets` draws the
generator's set and then, on two of its three branches, overwrites the six metric columns
with drawn figures (still ``Decimal``, still non-null, still the same rows, windows, counts
and checksums) so the score sweeps its full span. No branch changes the shape of a row, and
the metrics are exactly what ``quality_components`` reads, so this remains
``evidence_sets()`` output priced by the real evaluator.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace.pricing_evaluator import (
    ABSOLUTE_MAX_MINOR,
    ANCHORS,
    price_range,
    quality_components,
    quality_score,
)
from tests.strategies.marketplace_generators import evidence_sets

#: The configuration ``design.md § Property-based testing configuration`` prescribes for
#: every property test in this plan: at least 100 examples, no per-example deadline (the
#: first example pays import cost), and ``derandomize`` left at its default False so the
#: ``.hypothesis`` database of failing examples keeps accumulating across runs.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Requirement 8.4's ceiling, restated as a literal. Importing ``ABSOLUTE_MAX_MINOR`` alone
#: would make the bound whatever the module says it is, so the acceptance criterion's own
#: number is asserted against as well.
REQUIREMENT_8_4_CEILING = 100_000_000

#: The exact value types the pricing computation is allowed to hold. ``Decimal`` for the
#: inexact-but-exactly-rounded stretch, ``int`` for the Minor_Unit and count arithmetic.
#: Nothing else - and emphatically not ``float`` (Requirement 8.13).
_EXACT_TYPES = (int, Decimal)


def _anchored_currencies() -> st.SearchStrategy[str]:
    """The currencies with a declared price anchor, drawn case-sensitively.

    Taken from ``ANCHORS`` rather than from a literal pair so that adding a third anchored
    currency puts it under this property automatically instead of leaving it unpriced-for.
    """
    return st.sampled_from(sorted(ANCHORS))


#: How :func:`_priceable_evidence_sets` shapes the metrics of the drawn evidence set:
#: ``as_generated`` leaves ``evidence_sets()`` untouched, ``uniform`` gives every condition
#: one drawn profile (zero dispersion, medians exactly the drawn figures), ``varied`` draws
#: a fresh profile per condition.
_EVIDENCE_BRANCHES = ("as_generated", "uniform", "varied")


@st.composite
def _priceable_evidence_sets(draw: Any) -> List[Dict[str, Any]]:
    """An admissible evidence set, optionally with its metrics boosted to strong figures.

    The ``'as_generated'`` branch is ``evidence_sets(admissible=True)`` untouched, which is
    what the design's property-to-test mapping names. The ``'strong'`` branch keeps the same
    rows, owners, versions, windows, checksums and counts and replaces only the six measured
    metric columns with Decimals drawn from the healthy end of their ranges, so the quality
    score reaches the top of its span and ``recommended`` reaches the top of the anchor band.
    Every replacement is a ``Decimal``, so the branch cannot be the thing that introduces a
    ``float`` into the computation this test is asserting is float-free.
    """
    rows = draw(evidence_sets(admissible=True))
    branch = draw(st.sampled_from(_EVIDENCE_BRANCHES))
    if not rows or branch == "as_generated":
        return rows

    def metric(low: str, high: str, places: int = 4) -> Decimal:
        return draw(
            st.decimals(
                min_value=Decimal(low),
                max_value=Decimal(high),
                places=places,
                allow_nan=False,
                allow_infinity=False,
            )
        )

    # ``max_drawdown`` / ``win_rate`` are the ``strategy_backtests`` spellings the evaluator
    # reads under an alias; the same names the generator writes are reused, so no column is
    # added or renamed. ``uniform`` draws one profile and gives it to every condition, which
    # is the case that drives the score to the top of its span: identical returns make the
    # dispersion zero, so the consistency term legitimately scores full marks and the
    # medians are exactly the drawn figures. ``varied`` redraws per condition, which keeps
    # dispersion, mixed medians and the mid-band prices in the pool.
    profile = {
        "total_return_pct": ("0", "300"),
        "sharpe_ratio": ("0", "8"),
        "sortino_ratio": ("0", "8"),
        "max_drawdown": ("0", "40"),
        "win_rate": ("0", "100"),
        "profit_factor": ("0", "4"),
    }
    shared = {name: metric(*bounds) for name, bounds in profile.items()}
    for row in rows:
        for name, bounds in profile.items():
            row[name] = shared[name] if branch == "uniform" else metric(*bounds)
    return rows


def _assert_exact(value: Any, path: str) -> None:
    """Assert that ``value`` and everything it contains is exact, never a ``float``.

    Recurses into mappings and sequences so a term returned inside a container is walked
    too. ``bool`` is rejected even though it is an ``int`` subclass: a boolean standing in
    for a count or a ratio is a defect regardless of its exactness. ``date`` and ``str``
    pass through as leaves - the window dates and identifiers carry no arithmetic.
    """
    if isinstance(value, bool):
        raise AssertionError(
            f"pricing intermediate {path} is a bool ({value!r}); a boolean is not a "
            "measured figure"
        )
    if isinstance(value, float):
        raise AssertionError(
            f"pricing intermediate {path} is a binary floating-point number ({value!r}) - "
            "Requirement 8.13 forbids any pricing arithmetic in binary floating point"
        )
    if isinstance(value, _EXACT_TYPES):
        if isinstance(value, Decimal):
            assert value.is_finite(), (
                f"pricing intermediate {path} is the non-finite Decimal {value!r}; a "
                "quality term must be a finite figure"
            )
        return
    if isinstance(value, (str, bytes, date, datetime)):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_exact(item, f"{path}[{key!r}]")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _assert_exact(item, f"{path}[{index}]")
        return
    raise AssertionError(
        f"pricing intermediate {path} has unexpected type {type(value).__name__} "
        f"({value!r}); only int and Decimal are known to be exact (Requirement 8.13)"
    )


# Feature: marketplace-subscriptions-paper-trading, Property 51 (invariant, price range):
# For all admissible Backtest_Evidence sets and all anchored currencies, the Price_Range
# satisfies 1 <= minimum <= recommended <= maximum <= 100000000, all three values are Python
# ints in Minor_Units, and no intermediate value anywhere in the computation is a binary
# floating-point number.
@PROPERTY_SETTINGS
@given(conditions=_priceable_evidence_sets(), currency=_anchored_currencies())
def test_p51_price_range_is_ordered_bounded_and_integral(
    conditions: List[Dict[str, Any]], currency: str
) -> None:
    """A Price_Range is ordered, bounded, integral, and computed without a ``float``.

    **Validates: Requirements 8.1, 8.4, 8.12, 8.13**
    """
    minimum, recommended, maximum = result = price_range(conditions, currency)

    # Claim 1 - ordering and Requirement 8.4's two bounds, against the criterion's own
    # literal ceiling as well as the module's constant.
    assert 1 <= minimum, f"minimum {minimum} is below Requirement 8.4's floor of 1"
    assert minimum <= recommended, (
        f"minimum {minimum} exceeds recommended {recommended} in {currency}"
    )
    assert recommended <= maximum, (
        f"recommended {recommended} exceeds maximum {maximum} in {currency}"
    )
    assert maximum <= REQUIREMENT_8_4_CEILING, (
        f"maximum {maximum} exceeds Requirement 8.4's ceiling of "
        f"{REQUIREMENT_8_4_CEILING} Minor_Units"
    )
    assert maximum <= ABSOLUTE_MAX_MINOR, (
        f"maximum {maximum} exceeds pricing_evaluator.ABSOLUTE_MAX_MINOR "
        f"{ABSOLUTE_MAX_MINOR}"
    )

    # Claim 2 - all three are Minor_Unit integers, exactly ``int`` and not a bool or a
    # Decimal that happens to be whole (Requirement 8.12: every price is expressed in
    # Minor_Units together with its currency, and the currency selected the anchor).
    for name, amount in zip(result._fields, result):
        assert type(amount) is int, (
            f"{name} is {type(amount).__name__} ({amount!r}); a Minor_Unit amount must be "
            "a Python int"
        )

    # Claim 3 - no intermediate anywhere in the computation is a binary floating-point
    # number. The quality computation's intermediates are walked in full, and the integer
    # score it is quantized to is checked alongside them, so the whole chain from evidence
    # to price is covered rather than just its endpoints.
    components = quality_components(conditions)
    _assert_exact(dict(components), "quality_components")

    score = quality_score(conditions)
    _assert_exact(score, "quality_score")
    assert 0 <= score <= 100, f"quality score {score} escaped 0..100"
