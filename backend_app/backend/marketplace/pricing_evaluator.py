"""backend_app/backend/marketplace/pricing_evaluator.py - the statistical Pricing_Evaluator.

Spec: marketplace-subscriptions-paper-trading task 7.1. ``design.md`` ->
"``marketplace/pricing_evaluator.py``".
Requirements 8.1, 8.2, 8.3, 8.4, 8.6, 8.7, 8.13, 8.14.

Exposes
-------
EVALUATOR_VERSION       ``'pricing-evaluator/1.0.0-statistical'`` - persisted beside every
                        evaluation, and the thing Requirement 8.3's "the same evaluator
                        version" names
WEIGHTS                 the seven quality weights, summing to 100
ANCHORS                 per-currency ``(base_minor, span_minor)`` recommended band
MINIMUM_RATIO_PCT       60 - ``minimum`` as a percentage of ``recommended``
MAXIMUM_RATIO_PCT       250 - ``maximum`` as a percentage of ``recommended``
ABSOLUTE_MAX_MINOR      100_000_000 - Requirement 8.4's ceiling
PRICING_INPUTS          the Requirement 8.2 input names, per Backtest_Condition
DIGEST_INPUTS           the input names :func:`inputs_digest` covers
PriceRange              the returned triple, named for the persisted columns
quality_score(...)      the deterministic integer 0…100
quality_components(...) the same computation's intermediates, for explainability and for
                        the "no binary floating point anywhere" assertion of P-51
price_range(...)        ``(minimum, recommended, maximum)`` in integer Minor_Units
inputs_digest(...)      the canonical, order-independent SHA-256 over the evidence
missing_evidence_inputs(...)  which Requirement 8.2 inputs a condition set lacks
MissingEvidenceInputs   raised when it lacks any of them (Requirement 8.14)
InvalidEvidenceInput    raised for a present but inexact or non-finite input
UnsupportedCurrency     raised for a currency with no anchor

STATISTICAL, NOT ML - AND WHY THAT IS NOT A COMPROMISE
------------------------------------------------------
Requirement 8.6 makes a statistical model mandatory while no labelled pricing dataset
satisfies the minimum-data gate ``backend/ml_training_policy.py`` defines, and no such
dataset exists: with zero ``marketplace_settlements`` rows there are zero
(evidence -> realised-price) labels, so there is nothing to fit and nothing to hold out.

Two consequences are structural here rather than aspirational:

* This module imports neither ``backend.ml_models`` nor ``backend.model_versioning``. There
  is no code path through which a model artifact could be loaded, so Requirement 8.5's
  obligations (checksum, feature schema, hyperparameters, split metrics) cannot be silently
  bypassed - they simply do not apply yet.
* Nothing here returns an accuracy, confidence, precision or error figure for a Price_Range,
  because none has been measured on a held-out split of real data (Requirement 8.7). A
  "confidence: 0.87" beside a price would be a fabricated number, and
  ``marketplace_price_evaluations`` deliberately has no column to store one in. The
  Price_Range is guidance derived from stated evidence, and the only claim made for it is
  the one this module can prove: it is a documented, deterministic function of that evidence.

When the label gate is eventually satisfied, a ``pricing-evaluator/2.0.0-model`` variant
would be persisted through ``model_versioning`` and pinned by version. Until then the
version string ends in ``-statistical`` so that a persisted evaluation says which family
produced it.

WHY THIS MODULE IS PURE
-----------------------
The layering rule ``money.py``, ``subscription_period.py`` and ``subscription_state.py``
already follow: import pulls only the standard library and one sibling pure module - no
FastAPI, no database handle, no clock read, no I/O. The evidence arrives as rows the caller
has already read, so the whole of Requirements 8.2 … 8.4 is property-testable with no
fixture and no database (P-51), and the price-setting endpoint of task 15.1 can call the
arithmetic without holding a connection open across it.

Nothing here reads the current time and nothing here persists. ``inputs_digest`` is what
lets a *caller* decide whether a stored evaluation is still valid (Requirement 8.8), but the
lookup and the write belong to the service layer.

WHY THERE IS NO ``float`` IN THIS FILE
--------------------------------------
Requirement 8.13 forbids pricing arithmetic in binary floating point, and Requirement 8.3
demands that two runs over the same evidence agree exactly. Both are met the same way:

* Every quality input is coerced to ``Decimal`` before it is used, and a ``float`` argument
  is **refused** rather than converted. Converting would launder an error that already
  happened upstream - ``0.07`` is not seven hundredths in binary - and would make the digest
  depend on how the row happened to be deserialised. A decimal *string* is accepted, because
  that is the exact form the Postgres driver hands back for ``NUMERIC``.
* The quality computation runs under an explicitly constructed ``Decimal`` context
  (``prec=28``, ``ROUND_HALF_EVEN``, pinned exponent range and traps), passed to
  ``localcontext`` so the caller's ambient context cannot change a single digit of the
  result. ``Decimal`` arithmetic and ``Decimal.sqrt`` are correctly rounded by
  specification, so the same inputs give the same digits on every platform, interpreter and
  build - which is what Requirement 8.3 actually asks for.
* ``quality_score`` quantizes to an **integer** 0…100 before that value is used, and every
  step after it is integer arithmetic on Minor_Units. So the only inexact stretch of the
  whole evaluation is bounded, is decimal, and ends before the price is formed.

WHY A MISSING INPUT IS AN EXCEPTION AND NOT A DEFAULT
-----------------------------------------------------
Requirement 8.14 is explicit: absent evidence, or evidence lacking any Requirement 8.2
input, produces **no** Price_Range. Substituting a zero for a missing Sharpe ratio would
produce a plausible-looking price from evidence that does not exist, so
:class:`MissingEvidenceInputs` carries the sorted input names instead and the caller turns
them into the owner-facing list of what to supply. A persisted ``NULL`` counts as lacking:
the column being present in the row does not make the figure measured.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The 30/60s rate limit, the ``marketplace_price_evaluations`` write and the
  ``minimum <= price <= maximum`` enforcement point (Requirements 8.8 … 8.10, 8.15). Those
  need a request context and a database handle; task 15.1 owns them and calls this module.
* The removal of the ``29.99 / 49.99 / 99.99 / 199.99`` price points from ``publish_strategy``
  (Requirement 8.11). Task 15.1 again - this module is the replacement, not the deletion.
* Any FX or cross-currency arithmetic. A Price_Range is computed per currency from that
  currency's anchor; there is no conversion between them.
* An ``evaluation_score``-style single number offered to callers as a rating.
  :func:`quality_score` exists because the price is derived from it, and
  ``marketplace_backtest_evidence`` is the only thing it summarises.

READING A CONDITION ROW
-----------------------
A condition is either a mapping keyed by column name (what ``supabase-py`` returns) or an
object exposing the columns as attributes. Two column names are read under an alias,
because the immutable evidence copy and its ``strategy_backtests`` source spell them
differently: ``max_drawdown_pct``/``max_drawdown`` and ``win_rate_pct``/``win_rate``, and
``source_backtest_id``/``id``. The alias is a rename only - no value is rescaled, because
rescaling a figure whose unit is not recorded would be guessing at the evidence rather than
reading it. Win rate and drawdown are therefore taken as percentages, as
``marketplace_backtest_evidence``'s ``win_rate_pct`` and ``max_drawdown_pct`` columns state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from types import MappingProxyType
from typing import Any, Iterable, Mapping, NamedTuple, Sequence, Tuple

from backend_app.backend.marketplace.money import (
    MINOR_UNIT_EXPONENT,
    MAX_AMOUNT_MINOR,
    UnsupportedCurrency as _MoneyUnsupportedCurrency,
)

__all__ = [
    "EVALUATOR_VERSION",
    "WEIGHTS",
    "ANCHORS",
    "MINIMUM_RATIO_PCT",
    "MAXIMUM_RATIO_PCT",
    "ABSOLUTE_MAX_MINOR",
    "EVALUATION_DIGITS",
    "PRICING_INPUTS",
    "DIGEST_INPUTS",
    "PriceRange",
    "PricingEvaluatorError",
    "MissingEvidenceInputs",
    "InvalidEvidenceInput",
    "UnsupportedCurrency",
    "quality_score",
    "quality_components",
    "price_range",
    "inputs_digest",
    "missing_evidence_inputs",
]


# ══════════════════════════════════════════════════════════════════════════
# VERSION, WEIGHTS, ANCHORS AND BOUNDS
# ══════════════════════════════════════════════════════════════════════════

#: The evaluator identity persisted with every ``marketplace_price_evaluations`` row. Any
#: change to a weight, an anchor, a saturation constant or the shape of the computation is a
#: change to this string - Requirement 8.3's determinism is stated *per evaluator version*,
#: so a silent formula change under an unchanged version would make a stored evaluation
#: unreproducible. The ``-statistical`` suffix records the model family (Requirement 8.6).
EVALUATOR_VERSION = "pricing-evaluator/1.0.0-statistical"

#: The seven quality weights, in whole points of a 100-point score. Read-only: a caller
#: mutating a weight would change the price of every subsequent evaluation while the
#: persisted ``evaluator_version`` went on claiming this one.
WEIGHTS: Mapping[str, int] = MappingProxyType(
    {
        "sharpe": 30,
        "ret": 20,
        "drawdown": 15,
        "winrate": 10,
        "profit_factor": 10,
        "consistency": 10,
        "breadth": 5,
    }
)

#: Per-currency ``(base_minor, span_minor)``. ``recommended`` runs from ``base`` at quality 0
#: to ``base + span`` at quality 100, so the band is ``$20.00 … $200.00`` for USD and
#: ``INR 1500.00 … INR 15000.00`` for INR. Both are expressed in Minor_Units, so no
#: conversion happens between the anchor and the price (Requirement 8.12).
ANCHORS: Mapping[str, Tuple[int, int]] = MappingProxyType(
    {
        "USD": (2_000, 18_000),
        "INR": (150_000, 1_350_000),
    }
)

#: ``minimum = 60 %`` of ``recommended``, floored at one Minor_Unit (Requirement 8.4).
MINIMUM_RATIO_PCT = 60

#: ``maximum = 250 %`` of ``recommended``, capped at :data:`ABSOLUTE_MAX_MINOR`.
MAXIMUM_RATIO_PCT = 250

#: Requirement 8.4's ceiling: ``maximum_price <= 100000000`` Minor_Units.
ABSOLUTE_MAX_MINOR = 100_000_000

#: The ``Decimal`` context ``prec`` for the quality computation: 28 significant decimal
#: digits, far more than any ratio, percentage or standard deviation in the evidence carries.
#: Set explicitly rather than inherited, so the score cannot depend on an ambient context.
#: It is a property of the arithmetic, not a claim about the Price_Range - Requirement 8.7
#: forbids reporting a precision *figure* for a price, and nothing here reports one.
EVALUATION_DIGITS = 28

# The saturation points of the seven quality terms. Named rather than inlined because each
# one is a claim about what "good" means for that statistic, and a reviewer has to be able
# to see the claim without decoding an expression.
#: Sharpe + Sortino at or above 6 saturates the risk-adjusted-return term.
SHARPE_SUM_SATURATION = Decimal(6)
#: A median total return at or above 60 % saturates the return term.
RETURN_SATURATION_PCT = Decimal(60)
#: A worst-case drawdown of 0 % scores full marks; 30 % or worse scores nothing.
DRAWDOWN_SATURATION_PCT = Decimal(30)
#: Win rate scores from 40 % (nothing) to 70 % (full marks).
WINRATE_FLOOR_PCT = Decimal(40)
WINRATE_SPAN_PCT = Decimal(30)
#: Profit factor scores from 1.0 (nothing) to 2.5 (full marks).
PROFIT_FACTOR_FLOOR = Decimal(1)
PROFIT_FACTOR_SPAN = Decimal("1.5")
#: Breadth: 3 conditions (nothing) to 10 (full marks) - Requirement 3.1's admissible range.
CONDITION_COUNT_FLOOR = 3
CONDITION_COUNT_SPAN = 7
#: Breadth: 60 trades in total (nothing) to 300 (full marks).
TRADE_COUNT_FLOOR = 60
TRADE_COUNT_SPAN = 240
#: Breadth: a shortest window of 90 days (nothing) to 365 (full marks) - 90 is
#: ``evidence_validator.THRESHOLDS['MIN_WINDOW_DAYS']``, the shortest admissible window.
WINDOW_DAYS_FLOOR = 90
WINDOW_DAYS_SPAN = 275

#: The Requirement 8.2 inputs, per Backtest_Condition. ``total_return_pct`` carries three of
#: them at once: the total return itself, the return volatility across conditions and the
#: cross-condition consistency are all computed from that one column across the set.
#: ``start_date``/``end_date`` carry the tested duration in calendar days, and the number of
#: distinct Backtest_Conditions is the length of the set itself.
PRICING_INPUTS: Tuple[str, ...] = (
    "total_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown_pct",
    "win_rate_pct",
    "profit_factor",
    "total_trades",
    "start_date",
    "end_date",
)

#: What :func:`inputs_digest` covers: every pricing input, plus the identity and
#: reproducibility fields that make two evidence sets the same evidence set. The digest
#: includes ``dataset_checksum``, ``dag_hash`` and ``executed_bar_count`` so that evidence
#: recomputed against different data, a different graph or a different bar count produces a
#: different digest even when every metric happens to land on the same figure.
DIGEST_INPUTS: Tuple[str, ...] = PRICING_INPUTS + (
    "source_backtest_id",
    "dataset_checksum",
    "dag_hash",
    "executed_bar_count",
    "final_capital",
)

#: Column aliases. The evidence copy and its ``strategy_backtests`` source spell these
#: differently; the alias is a rename only, never a rescale. See "READING A CONDITION ROW".
_ALIASES: Mapping[str, Tuple[str, ...]] = MappingProxyType(
    {
        "source_backtest_id": ("source_backtest_id", "id"),
        "max_drawdown_pct": ("max_drawdown_pct", "max_drawdown"),
        "win_rate_pct": ("win_rate_pct", "win_rate"),
    }
)

#: The digest's JSON key per canonical input name, exactly as ``design.md`` specifies it.
_DIGEST_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "source_backtest_id": "id",
        "dataset_checksum": "dataset_checksum",
        "dag_hash": "dag_hash",
        "start_date": "start",
        "end_date": "end",
        "total_trades": "trades",
        "executed_bar_count": "bars",
        "total_return_pct": "ret",
        "sharpe_ratio": "sharpe",
        "sortino_ratio": "sortino",
        "max_drawdown_pct": "dd",
        "win_rate_pct": "win",
        "profit_factor": "pf",
        "final_capital": "final",
    }
)

#: The inputs that are whole counts rather than measured ratios.
_INTEGER_INPUTS = frozenset({"total_trades", "executed_bar_count"})

#: The inputs that are calendar dates.
_DATE_INPUTS = frozenset({"start_date", "end_date"})

#: The inputs that are opaque identifiers or checksums, compared as text and never as numbers.
_TEXT_INPUTS = frozenset({"source_backtest_id", "dataset_checksum", "dag_hash"})

# Fully specified so that no field is inherited from ``DefaultContext`` or from whatever the
# caller happened to have installed. ``localcontext`` copies this, so concurrent callers do
# not share mutable state and flags raised here never escape.
_EVALUATION_CONTEXT = Context(
    prec=EVALUATION_DIGITS,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)
_THREE = Decimal(3)
_HUNDRED = 100

# Import-time consistency checks. Each one is a claim made in prose above; a wrong constant
# would otherwise surface as a mispriced Listing rather than as a failed import.
if sum(WEIGHTS.values()) != 100:
    raise AssertionError(
        f"WEIGHTS must sum to 100, got {sum(WEIGHTS.values())} - the quality score is "
        "stated as a 0..100 figure and every weighted term is bounded by its weight"
    )
if not set(ANCHORS) <= set(MINOR_UNIT_EXPONENT):
    raise AssertionError(
        "every anchored currency needs a persisted minor-unit exponent in money.py; "
        f"unknown: {sorted(set(ANCHORS) - set(MINOR_UNIT_EXPONENT))}"
    )
if ABSOLUTE_MAX_MINOR > MAX_AMOUNT_MINOR:
    raise AssertionError(
        "a price the evaluator can recommend must be an amount money.py will settle"
    )
for _currency, (_base, _span) in ANCHORS.items():
    if not (isinstance(_base, int) and isinstance(_span, int)):
        raise AssertionError(f"anchor for {_currency} must be integer Minor_Units")
    if _base < 1 or _span < 0 or _base + _span > ABSOLUTE_MAX_MINOR:
        raise AssertionError(
            f"anchor for {_currency} must satisfy 1 <= base and "
            f"base + span <= {ABSOLUTE_MAX_MINOR}"
        )
del _currency, _base, _span


# ══════════════════════════════════════════════════════════════════════════
# RESULT TYPE AND ERRORS
# ══════════════════════════════════════════════════════════════════════════


class PriceRange(NamedTuple):
    """The Price_Range triple, in integer Minor_Units.

    The field names match the ``marketplace_price_evaluations`` columns the triple is
    persisted into, so the write site cannot transpose two of them. It is still a plain
    tuple, so ``mn, rec, mx = price_range(...)`` reads as the design's pseudocode does.
    """

    minimum_price_minor: int
    recommended_price_minor: int
    maximum_price_minor: int


class PricingEvaluatorError(Exception):
    """Base class for every refusal this module makes.

    The service layer catches this to map a refusal onto one owner-facing error code;
    callers that need to distinguish "the evidence is incomplete" from "the evidence is
    unusable" from "that currency has no anchor" catch the subclasses.
    """


class MissingEvidenceInputs(PricingEvaluatorError):
    """The evidence is absent, or lacks a required input (Requirement 8.14).

    ``names`` is the sorted tuple of canonical input names that at least one condition
    lacks - the list the API turns into "which required Backtest_Evidence inputs are
    missing". No Price_Range is produced, and the caller creates no Listing and changes no
    Submission_State.

    A persisted ``NULL`` counts as lacking. Requirement 8.2 asks for a figure *read from*
    persisted Backtest_Evidence; a column that is present and null holds no figure.
    """

    def __init__(self, names: Iterable[str]) -> None:
        self.names: Tuple[str, ...] = tuple(sorted(set(names)))
        super().__init__(
            "backtest evidence is missing required pricing inputs: "
            + (", ".join(self.names) if self.names else "(evidence absent)")
        )


class InvalidEvidenceInput(PricingEvaluatorError):
    """An input is present but is not an exact, finite figure.

    Raised for a ``float`` or a ``bool``, for ``NaN`` and the infinities, for a string that
    is not a decimal number or an ISO-8601 date, and for a trade or bar count that is not a
    whole number. A ``float`` is refused rather than converted: Requirement 8.13 forbids
    binary floating-point pricing arithmetic, and a ``float`` argument means the inexactness
    already happened upstream where this module cannot account for it.
    """


class UnsupportedCurrency(PricingEvaluatorError, _MoneyUnsupportedCurrency):
    """No anchor is defined for the requested currency.

    It also inherits ``money.UnsupportedCurrency`` so that a caller which already handles
    "this platform cannot express amounts in that currency" catches both refusals in one
    place, rather than the same condition having two unrelated meanings.
    """


# ══════════════════════════════════════════════════════════════════════════
# THE QUALITY SCORE (Requirements 8.2, 8.3, 8.13)
# ══════════════════════════════════════════════════════════════════════════


def quality_components(conditions: Any) -> Mapping[str, Any]:
    """Return every intermediate of the quality computation, in order.

    Exposed for two reasons. It is the explainability surface an owner-facing "why this
    price" view can be built from without re-deriving the formula, and it is what P-51's
    "no intermediate value anywhere in the computation is a binary floating-point number"
    can be asserted against by walking the returned values.

    Every value is a ``Decimal`` or an ``int``; ``float`` appears nowhere.

    Args:
        conditions: The Backtest_Conditions, as mappings keyed by column name or as objects
            exposing the columns as attributes.

    Returns:
        A read-only mapping. ``'total'`` is the unrounded weighted sum;
        :func:`quality_score` is that value quantized to an integer.

    Raises:
        MissingEvidenceInputs: If ``conditions`` is empty or any condition lacks a
            Requirement 8.2 input.
        InvalidEvidenceInput: If an input is present but inexact or non-finite.
    """
    rows = _as_rows(conditions)
    _require(rows, PRICING_INPUTS)

    n = len(rows)
    returns = [_decimal_input(row, "total_return_pct") for row in rows]
    sharpes = [_decimal_input(row, "sharpe_ratio") for row in rows]
    sortinos = [_decimal_input(row, "sortino_ratio") for row in rows]
    drawdowns = [_decimal_input(row, "max_drawdown_pct") for row in rows]
    win_rates = [_decimal_input(row, "win_rate_pct") for row in rows]
    profit_factors = [_decimal_input(row, "profit_factor") for row in rows]
    trade_counts = [_integer_input(row, "total_trades") for row in rows]
    window_lengths = [_window_days(row) for row in rows]

    with localcontext(_EVALUATION_CONTEXT):
        r_med = _median(returns)
        sigma_r = _population_sd(returns)
        s_med = _median(sharpes)
        so_med = _median(sortinos)
        d_max = max(abs(value) for value in drawdowns)
        w_med = _median(win_rates)
        pf_med = _median(profit_factors)
        t_tot = sum(trade_counts)
        days_min = min(window_lengths)

        q_sharpe = _clamp01((s_med + so_med) / SHARPE_SUM_SATURATION) * WEIGHTS["sharpe"]
        q_return = _clamp01(r_med / RETURN_SATURATION_PCT) * WEIGHTS["ret"]
        q_drawdown = (
            _clamp01((DRAWDOWN_SATURATION_PCT - d_max) / DRAWDOWN_SATURATION_PCT)
            * WEIGHTS["drawdown"]
        )
        q_winrate = (
            _clamp01((w_med - WINRATE_FLOOR_PCT) / WINRATE_SPAN_PCT) * WEIGHTS["winrate"]
        )
        q_profit_factor = (
            _clamp01((pf_med - PROFIT_FACTOR_FLOOR) / PROFIT_FACTOR_SPAN)
            * WEIGHTS["profit_factor"]
        )
        # Consistency is dispersion inverted, scaled by the size of the median return so
        # that a 5-point spread around a 10 % median counts as inconsistent while the same
        # spread around a 200 % median does not. The floor of 1 keeps the denominator away
        # from zero for a median return of zero, where any spread at all is inconsistent.
        consistency = _ONE - _clamp01(sigma_r / max(abs(r_med), _ONE))
        q_consistency = consistency * WEIGHTS["consistency"]
        q_breadth = (
            (
                _clamp01(Decimal(n - CONDITION_COUNT_FLOOR) / CONDITION_COUNT_SPAN)
                + _clamp01(Decimal(t_tot - TRADE_COUNT_FLOOR) / TRADE_COUNT_SPAN)
                + _clamp01(Decimal(days_min - WINDOW_DAYS_FLOOR) / WINDOW_DAYS_SPAN)
            )
            / _THREE
            * WEIGHTS["breadth"]
        )

        total = (
            q_sharpe
            + q_return
            + q_drawdown
            + q_winrate
            + q_profit_factor
            + q_consistency
            + q_breadth
        )

    return MappingProxyType(
        {
            "condition_count": n,
            "return_median": r_med,
            "return_sd": sigma_r,
            "sharpe_median": s_med,
            "sortino_median": so_med,
            "drawdown_worst": d_max,
            "win_rate_median": w_med,
            "profit_factor_median": pf_med,
            "trade_total": t_tot,
            "window_days_shortest": days_min,
            "consistency": consistency,
            "q_sharpe": q_sharpe,
            "q_return": q_return,
            "q_drawdown": q_drawdown,
            "q_winrate": q_winrate,
            "q_profit_factor": q_profit_factor,
            "q_consistency": q_consistency,
            "q_breadth": q_breadth,
            "total": total,
        }
    )


def quality_score(conditions: Any) -> int:
    """Score the Backtest_Evidence as an integer 0…100.

    A deterministic function of the Requirement 8.2 inputs and of nothing else: no clock, no
    random source, no persisted state, no ambient ``Decimal`` context. Computed in
    ``Decimal`` under :data:`_EVALUATION_CONTEXT` (``prec=28``, ``ROUND_HALF_EVEN``) and
    quantized to an integer with ``ROUND_HALF_EVEN`` **before** it is used, so every
    subsequent step of :func:`price_range` is integer arithmetic (Requirements 8.3, 8.13).

    Each of the seven terms is a ``clamp01`` fraction times its weight, and the weights sum
    to 100, so the unrounded total lies in ``[0, 100]`` by construction and the quantized
    score does too.

    Args:
        conditions: The Backtest_Conditions for one Submission.

    Returns:
        The score, an ``int`` in ``0…100``.

    Raises:
        MissingEvidenceInputs: If ``conditions`` is empty or any condition lacks a
            Requirement 8.2 input.
        InvalidEvidenceInput: If an input is present but inexact or non-finite.
    """
    components = quality_components(conditions)
    with localcontext(_EVALUATION_CONTEXT):
        score = int(components["total"].quantize(_ONE, rounding=ROUND_HALF_EVEN))

    _invariant(
        0 <= score <= 100,
        f"quality score {score} escaped 0..100 - a weight or a clamp is wrong",
    )
    return score


# ══════════════════════════════════════════════════════════════════════════
# THE PRICE RANGE (Requirements 8.1, 8.4, 8.12, 8.14)
# ══════════════════════════════════════════════════════════════════════════


def price_range(conditions: Any, currency: Any) -> PriceRange:
    """Compute the Price_Range for one Submission in one currency.

    Integer Minor_Units end to end from :func:`quality_score` onward:
    ``recommended = base + (span * Q) // 100``, ``minimum = max(1, (rec * 60) // 100)`` and
    ``maximum = min(100_000_000, (rec * 250) // 100)``. ``//`` on non-negative integers is
    exact at every magnitude, so there is no rounding policy to choose and no precision
    ceiling to overflow (Requirements 8.1, 8.13).

    Args:
        conditions: The Backtest_Conditions for the Submission, as mappings keyed by column
            name or as objects exposing the columns as attributes.
        currency: The Listing's ISO 4217 currency code, matched case-sensitively against
            :data:`ANCHORS`.

    Returns:
        A :class:`PriceRange` of three ``int`` Minor_Unit amounts satisfying
        ``1 <= minimum <= recommended <= maximum <= 100_000_000`` (Requirement 8.4).

    Raises:
        MissingEvidenceInputs: If the evidence is absent or lacks a Requirement 8.2 input.
            No Price_Range is produced (Requirement 8.14).
        InvalidEvidenceInput: If an input is present but inexact or non-finite.
        UnsupportedCurrency: If no anchor is defined for ``currency``.
    """
    rows = _as_rows(conditions)
    _require(rows, PRICING_INPUTS)

    base, span = _anchor(currency)
    quality = quality_score(rows)

    recommended = base + (span * quality) // _HUNDRED
    minimum = max(1, (recommended * MINIMUM_RATIO_PCT) // _HUNDRED)
    maximum = min(ABSOLUTE_MAX_MINOR, (recommended * MAXIMUM_RATIO_PCT) // _HUNDRED)

    # The closing invariant of Requirement 8.4, stated once, at the only place a Price_Range
    # is produced. A raise rather than a bare ``assert``: ``python -O`` strips ``assert``,
    # and an out-of-order range written to ``marketplace_price_evaluations`` would become the
    # bound a later price submission is accepted against.
    _invariant(
        1 <= minimum <= recommended <= maximum <= ABSOLUTE_MAX_MINOR,
        f"price range out of order or out of bounds: {minimum}, {recommended}, {maximum} "
        f"for quality {quality} in {currency}",
    )
    return PriceRange(minimum, recommended, maximum)


# ══════════════════════════════════════════════════════════════════════════
# THE INPUTS DIGEST (Requirement 8.8's "the same persisted Backtest_Evidence")
# ══════════════════════════════════════════════════════════════════════════


def inputs_digest(conditions: Any) -> str:
    """Return the canonical SHA-256 digest of a Backtest_Evidence set.

    The digest answers exactly one question: is this the same evidence the stored evaluation
    was computed from? So it is:

    * **order-independent** - the conditions are sorted by ``source_backtest_id`` (with the
      canonical serialisation of the entry as tie-break, so two rows sharing an id cannot
      make the digest depend on list order). The owner listing the same backtests in a
      different order gets the same digest and the same cached evaluation.
    * **exact** - every ``Decimal`` contributes its ``str()`` form, which is the persisted
      decimal text; no value is rounded, rescaled or passed through a ``float``.
    * **canonical** - JSON with sorted keys, no whitespace and ``ensure_ascii``, so the byte
      string being hashed does not depend on ``dict`` ordering or on a serialiser default.
    * **total over the inputs** - identity, reproducibility fields and every metric. A
      change to any of them changes the digest, which is what makes Requirement 8.8's
      "otherwise recompute" branch fire.

    Args:
        conditions: The Backtest_Conditions for one Submission.

    Returns:
        The 64-character lowercase hex digest.

    Raises:
        MissingEvidenceInputs: If the evidence is absent or lacks any digested input.
        InvalidEvidenceInput: If an input is present but inexact or non-finite.
    """
    rows = _as_rows(conditions)
    _require(rows, DIGEST_INPUTS)

    entries = [_digest_entry(row) for row in rows]
    entries.sort(key=lambda entry: (entry["id"], _canonical_json(entry)))
    return hashlib.sha256(_canonical_json(entries).encode("utf-8")).hexdigest()


def missing_evidence_inputs(
    conditions: Any, required: Sequence[str] = PRICING_INPUTS
) -> Tuple[str, ...]:
    """Return the sorted canonical names of ``required`` inputs the evidence lacks.

    The non-raising form of the Requirement 8.14 check, for a caller that wants to report
    what is missing without handling an exception - a submission form's readiness indicator,
    for instance. An empty tuple means every required input is present and non-null on every
    condition.

    Args:
        conditions: The Backtest_Conditions for one Submission. ``None`` and an empty
            sequence both mean the evidence is absent, and every required name is returned.
        required: Which input names to check. Defaults to the Requirement 8.2 set;
            :data:`DIGEST_INPUTS` is the other meaningful argument.

    Returns:
        The sorted tuple of missing canonical input names.
    """
    rows = _as_rows(conditions)
    if not rows:
        return tuple(sorted(set(required)))

    missing = {
        name for name in required for row in rows if _read(row, name) is _ABSENT
    }
    return tuple(sorted(missing))


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS - READING A CONDITION
# ══════════════════════════════════════════════════════════════════════════


class _Absent:
    """The "this row has no such figure" sentinel.

    Distinct from ``None`` on purpose: ``None`` is what a persisted ``NULL`` arrives as, and
    both mean "lacking" (Requirement 8.14), but the sentinel makes the absence explicit at
    every call site rather than overloading a value that could also be a legitimate reading
    in some other module.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "<absent>"


_ABSENT = _Absent()


def _as_rows(conditions: Any) -> Tuple[Any, ...]:
    """Materialise ``conditions`` once, so a generator is not consumed by the first pass."""
    if conditions is None:
        return ()
    if isinstance(conditions, (str, bytes, Mapping)):
        raise InvalidEvidenceInput(
            "conditions must be a sequence of Backtest_Condition rows, got "
            f"{type(conditions).__name__}"
        )
    return tuple(conditions)


def _read(row: Any, name: str) -> Any:
    """Read one canonical input from a condition row, or :data:`_ABSENT`.

    Tries the canonical column name first, then any alias, on a mapping key and then on an
    attribute. A present-but-``NULL`` value is :data:`_ABSENT`: the column existing does not
    make the figure measured.
    """
    for candidate in _ALIASES.get(name, (name,)):
        if isinstance(row, Mapping):
            value = row.get(candidate, _ABSENT)
        else:
            value = getattr(row, candidate, _ABSENT)
        if value is not _ABSENT and value is not None:
            return value
    return _ABSENT


def _require(rows: Sequence[Any], required: Sequence[str]) -> None:
    """Raise :class:`MissingEvidenceInputs` unless every ``required`` input is present.

    Every missing name is collected before raising, so the owner is told the whole list in
    one response rather than discovering it one resubmission at a time (Requirement 8.14).
    """
    missing = missing_evidence_inputs(rows, required)
    if missing:
        raise MissingEvidenceInputs(missing)


def _decimal_input(row: Any, name: str) -> Decimal:
    """Read one measured figure as an exact, finite ``Decimal``."""
    value = _read(row, name)
    if value is _ABSENT:  # pragma: no cover - _require has already run
        raise MissingEvidenceInputs((name,))

    # An allow-list of exact types, not a deny-list, for the reason ``money.py`` gives: the
    # admissible forms are the two exact numeric types and the decimal text a driver returns.
    # Anything else - a binary floating-point value above all - is refused rather than
    # converted, so this module never names, holds or arithmetises one (Requirement 8.13).
    if isinstance(value, bool):
        raise InvalidEvidenceInput(f"{name} must be a numeric figure, got a boolean")
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, str):
        try:
            decimal_value = Decimal(value.strip())
        except InvalidOperation as exc:
            raise InvalidEvidenceInput(f"{name} is not a decimal number") from exc
    else:
        raise InvalidEvidenceInput(
            f"{name} must be a Decimal, an int or a decimal string, got "
            f"{type(value).__name__} - a binary floating-point value has already lost "
            "exactness by the time it reaches this module (Requirement 8.13)"
        )

    if not decimal_value.is_finite():
        raise InvalidEvidenceInput(f"{name} is not a finite figure")
    return decimal_value


def _integer_input(row: Any, name: str) -> int:
    """Read one whole count (``total_trades``, ``executed_bar_count``) as an ``int``."""
    value = _read(row, name)
    if value is _ABSENT:  # pragma: no cover - _require has already run
        raise MissingEvidenceInputs((name,))

    if isinstance(value, bool):
        raise InvalidEvidenceInput(f"{name} must be a whole count, got a boolean")
    if isinstance(value, int):
        return value
    # Anything else goes through the same allow-list, so a count arriving as an inexact
    # value is refused there rather than being truncated into a plausible integer.
    decimal_value = _decimal_input(row, name)
    if decimal_value != decimal_value.to_integral_value():
        raise InvalidEvidenceInput(f"{name} must be a whole count, got {decimal_value}")
    return int(decimal_value)


def _date_input(row: Any, name: str) -> date:
    """Read ``start_date`` / ``end_date`` as a ``datetime.date``.

    A ``datetime`` is narrowed to its date and an ISO-8601 string is parsed, because the
    column is ``DATE`` and a driver may hand back any of the three. The tested duration is
    counted in whole calendar days, so a time component would contribute nothing but a
    difference in the digest.
    """
    value = _read(row, name)
    if value is _ABSENT:  # pragma: no cover - _require has already run
        raise MissingEvidenceInputs((name,))

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise InvalidEvidenceInput(f"{name} is not an ISO-8601 date") from exc
    raise InvalidEvidenceInput(
        f"{name} must be a date or an ISO-8601 date string, got {type(value).__name__}"
    )


def _text_input(row: Any, name: str) -> str:
    """Read an identifier or checksum as text, without interpreting it."""
    value = _read(row, name)
    if value is _ABSENT:  # pragma: no cover - _require has already run
        raise MissingEvidenceInputs((name,))
    if isinstance(value, (bytes, bytearray)):
        raise InvalidEvidenceInput(f"{name} must be text, got {type(value).__name__}")
    return str(value)


def _window_days(row: Any) -> int:
    """The tested duration of one condition, in inclusive calendar days.

    The same count ``evidence_validator.window_days`` uses, and the same one
    ``chk_evidence_window CHECK (end_date - start_date + 1 >= 90)`` enforces in the
    database: integer day arithmetic on two ``date`` values, so there is nothing to round.
    An end before its start yields a non-positive count, which the breadth term clamps to
    zero rather than treating as a bonus - admissibility of the window is
    ``evidence_validator``'s decision, not the price's.
    """
    return (_date_input(row, "end_date") - _date_input(row, "start_date")).days + 1


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS - EXACT DECIMAL STATISTICS
# ══════════════════════════════════════════════════════════════════════════


def _clamp01(value: Decimal) -> Decimal:
    """Clamp to ``[0, 1]``. Comparison and selection only - nothing is rounded here."""
    if value < _ZERO:
        return _ZERO
    if value > _ONE:
        return _ONE
    return value


def _median(values: Sequence[Decimal]) -> Decimal:
    """The median of a non-empty sequence, exact for an odd count.

    For an even count the mean of the two middle values, which halves exactly in decimal.
    The median rather than the mean, because one exceptional Backtest_Condition should not
    carry a price on its own - Requirement 8.2 asks for cross-condition figures, and the
    dispersion is accounted for separately by the consistency term.
    """
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / _TWO


def _population_sd(values: Sequence[Decimal]) -> Decimal:
    """The population standard deviation, via ``Decimal.sqrt``.

    Population rather than sample: the Backtest_Conditions *are* the tested population, not
    a sample drawn from a larger one, and dividing by ``n - 1`` would be undefined for the
    single-condition case. ``Decimal.sqrt`` is correctly rounded by specification, so the
    result is identical on every platform (Requirement 8.3) - which is exactly what
    ``math.sqrt`` on a ``float`` would not guarantee, quite apart from Requirement 8.13.
    """
    count = len(values)
    mean = sum(values, _ZERO) / count
    variance = sum(((value - mean) * (value - mean) for value in values), _ZERO) / count
    return variance.sqrt()


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS - CANONICAL SERIALISATION
# ══════════════════════════════════════════════════════════════════════════


def _digest_entry(row: Any) -> dict:
    """One condition as the digest's canonical mapping.

    Keys are ``design.md``'s short names. Values are JSON-native: text as text, counts as
    integers, dates as ISO-8601, and every measured figure as the ``str()`` of its exact
    ``Decimal`` - never as a JSON float, which would reintroduce binary rounding into the
    one value whose whole purpose is to detect change.
    """
    entry: dict = {}
    for name in DIGEST_INPUTS:
        key = _DIGEST_KEYS[name]
        if name in _TEXT_INPUTS:
            entry[key] = _text_input(row, name)
        elif name in _DATE_INPUTS:
            entry[key] = _date_input(row, name).isoformat()
        elif name in _INTEGER_INPUTS:
            entry[key] = _integer_input(row, name)
        else:
            entry[key] = str(_decimal_input(row, name))
    return entry


def _canonical_json(payload: Any) -> str:
    """JSON with sorted keys, no whitespace and ASCII escaping.

    ``allow_nan=False`` is belt and braces: no ``float`` reaches this function, so no
    ``NaN`` or ``Infinity`` can - and if one ever did, the serialiser refuses rather than
    emitting the non-standard ``NaN`` literal and hashing it.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _anchor(currency: Any) -> Tuple[int, int]:
    """The ``(base_minor, span_minor)`` anchor for ``currency``.

    Matched case-sensitively: ISO 4217 codes are upper case, and accepting ``'usd'`` here
    would let two spellings of one currency price differently.
    """
    if not isinstance(currency, str) or currency not in ANCHORS:
        raise UnsupportedCurrency(
            f"no price anchor for currency {currency!r}; anchored: {sorted(ANCHORS)}"
        )
    return ANCHORS[currency]


def _invariant(holds: bool, message: str) -> None:
    """Raise ``AssertionError`` unless ``holds``.

    The design's ``ASSERT``, expressed so that ``python -O`` cannot remove it. Every use
    guards a value that would otherwise be persisted and later trusted as a bound, so a
    stripped check would turn a code defect into a mispriced Listing that no read detects.
    """
    if not holds:
        raise AssertionError(message)
