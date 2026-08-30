"""
backend_app/backend/marketplace/evidence_validator.py - Backtest_Evidence quality rules.

Spec: marketplace-subscriptions-paper-trading task 6.1. ``design.md`` ->
"``marketplace/evidence_validator.py`` - distinctness and quality (pure)".
Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.12, 3.13, 3.14 and 2.10.

Exposes
-------
THRESHOLDS               the six numeric rules, in one mapping (Requirements 3.1, 3.5, 3.7, 3.8)
EV_CODES                 the ten stable criterion codes this module emits, in emission order
EV_PUBLIC_MESSAGES       the owner-actionable sentence per code (Requirement 2.10)
CriterionOutcome         one criterion's verdict: code, passed, message, subjects
BacktestCondition        one Backtest_Condition as this module reads it, with ``from_row``
window_days(c)           inclusive calendar days of one condition's window
intersection_days(a, b)  inclusive calendar days shared by two windows, ``0`` when disjoint
distinct(a, b)           Requirement 3.5's distinctness predicate, integer arithmetic only
validate(...)            every criterion of Requirement 3, evaluated, none short-circuiting
all_passed(outcomes)     whether an outcome list admits the evidence set
failed_outcomes(...)     the failures alone, for the one response of Requirement 3.14

WHY THIS MODULE IS PURE
-----------------------
The layering rule ``design.md`` states for this package, and the convention
``order_lifecycle_state.py``, ``strategy_dag/schema.py`` and the sibling ``money.py`` and
``submission_state.py`` already establish: module import pulls only the standard library -
no FastAPI, no database handle, no HTTP client, no clock read, no I/O of any kind.

Two consequences matter here rather than being stylistic:

* :func:`validate` is a pure function of the rows handed to it, so re-validating unchanged
  persisted rows yields the same admit/reject outcome *and* the same per-criterion outcomes
  by construction. That is Requirement 3.12 (property P-38) made structural - there is no
  cached state, no clock and no query for a second call to differ on.
* The whole input space of Requirement 3 is property-testable with no fixture and no
  database (properties P-33 … P-40).

The reads themselves belong to ``eligibility_gate.evaluate``, which owns the four owner-scoped
round trips and hands the resulting ``strategy_backtests`` rows here.

WHY NOTHING SHORT-CIRCUITS
--------------------------
Requirement 2.10 obliges "every criterion of this requirement that failed rather than only
the first failure", and Requirement 3.14 obliges the identifiers of the affected
Backtest_Conditions with it. :func:`validate` therefore appends an outcome for every
criterion and every condition and every pair unconditionally - the ``passed`` flag carries
the verdict, control flow never does. A caller that wants only the failures asks
:func:`failed_outcomes`; a caller that wants the admit decision asks :func:`all_passed`.

WHY THE ARITHMETIC IS INTEGER ARITHMETIC
----------------------------------------
Requirement 3.5 states the overlap comparison as ``intersection_days x 4 <=
shorter_window_days`` and says explicitly that it is evaluated without rounding. Expressing
"at most 25 percent" by multiplying the left side by four keeps every value an ``int``: there
is no ``0.25`` to represent inexactly, no division to round and no rounding policy to choose
or document. ``MAX_OVERLAP_NUMERATOR`` is that four, named rather than inlined, so the one
place the percentage lives is :data:`THRESHOLDS`.

WHY A NULL IS A FAILURE AND NEVER AN INFERENCE
----------------------------------------------
Requirement 3.8 requires a *recorded* bar count and Requirement 3.11 forbids substituting a
figure the evidence does not contain. So a ``NULL`` ``executed_bar_count`` fails ``EV_BARS``
and a ``NULL`` ``version_id`` fails ``EV_ONE_VERSION``; neither is inferred from the length of
the equity curve, from ``total_trades``, or from any other column that happens to correlate
with it. The same reading applies to every parameter of Requirement 3.4: absent means failed,
never defaulted.

WHY AN OWNERSHIP FAILURE SAYS NOTHING ABOUT WHY
-----------------------------------------------
``EV_OWNERSHIP`` carries the same code and the same sentence whether the referenced row
belongs to another user or does not exist at all. The gate's ``strategy_backtests`` read is
already scoped to the owner, so a row owned by someone else simply is not in the list this
module receives - the validator cannot distinguish the two cases and therefore cannot leak
the difference (Requirement 3.13, property P-40).

WHY THE MESSAGES CARRY NO NUMBERS
---------------------------------
Requirement 2.10 excludes internal validation thresholds, internal identifiers, database
column names, query text, stack traces and security-control detail from the owner-facing
response. Every sentence in :data:`EV_PUBLIC_MESSAGES` is therefore written in the owner's
vocabulary - "a long enough test window", not the number of days - and names no column and no
table. ``errors.py::PUBLIC_MESSAGE_FOR_CODE`` remains the single place the *HTTP* body's
sentence per code is written; these are the validator's own sentences for the same codes, and
``tests/test_marketplace_error_surface.py`` holds both to the same deny-list.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Reading ``strategy_backtests``. That needs a database handle, which is what this module
  must not have. ``eligibility_gate.evaluate`` reads, this module decides.
* Persisting the immutable evidence copy of Requirements 3.9, 3.10 and 3.15.
  ``submission_service.create_submission`` owns that transaction.
* ``MarketplaceError`` and the FastAPI exception handler (task 5.5). ``errors.py`` imports
  FastAPI, so importing it here would break the purity this module's tests depend on. A
  :class:`CriterionOutcome` becomes an HTTP status at the service layer, not here.
* The seven-metric completeness check of Requirement 2.6. That is the gate's
  ``MP_METRICS_COMPLETE``, kept there because it is an eligibility criterion rather than an
  evidence-distinctness one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from types import MappingProxyType
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "EV_BARS",
    "EV_CHECKSUMS",
    "EV_CODES",
    "EV_COMPLETED",
    "EV_COUNT",
    "EV_DISTINCT",
    "EV_DURATION",
    "EV_ONE_VERSION",
    "EV_OWNERSHIP",
    "EV_PARAMS",
    "EV_PUBLIC_MESSAGES",
    "EV_TRADES",
    "MAX_CONDITIONS",
    "MAX_OVERLAP_NUMERATOR",
    "MIN_BARS",
    "MIN_CONDITIONS",
    "MIN_TRADES",
    "MIN_WINDOW_DAYS",
    "REQUIRED_PARAMETER_FIELDS",
    "THRESHOLDS",
    "BacktestCondition",
    "CriterionOutcome",
    "all_passed",
    "coerce_condition",
    "coerce_conditions",
    "distinct",
    "failed_outcomes",
    "intersection_days",
    "validate",
    "window_days",
]


# ══════════════════════════════════════════════════════════════════════════
# THE THRESHOLDS (Requirements 3.1, 3.5, 3.7, 3.8)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 3.1 - at least three conditions, so no single window can be sold as a track
#: record.
MIN_CONDITIONS = 3
#: Requirement 3.1 - at most ten, so one Submission cannot make the review surface unbounded.
MAX_CONDITIONS = 10
#: Requirement 3.7 - inclusive calendar days each condition must span.
MIN_WINDOW_DAYS = 90
#: Requirement 3.7 - the statistical-significance threshold ``backtesting_engine.py`` already
#: warns on (``if trades_count < 20`` at line 379). Same number, one meaning, two enforcement
#: points: a warning during the run, a hard criterion for publication.
MIN_TRADES = 20
#: Requirement 3.8 - the minimum-data guard ``backtesting_engine.py`` already raises on
#: (``if len(price_data) < 50`` at line 189), applied here to the *recorded* bar count.
MIN_BARS = 50
#: Requirement 3.5 - "at most 25 percent" expressed as ``intersection x 4 <= shorter`` so the
#: comparison stays in integers with nothing to round.
MAX_OVERLAP_NUMERATOR = 4

#: The six rules in one read-only mapping. ``tests/test_marketplace_error_surface.py`` reads
#: this to assert no owner-facing message leaks a threshold's digits (Requirement 2.10), and
#: the property tests read it rather than re-spelling the numbers.
THRESHOLDS: Mapping[str, int] = MappingProxyType(
    {
        "MIN_CONDITIONS": MIN_CONDITIONS,
        "MAX_CONDITIONS": MAX_CONDITIONS,
        "MIN_WINDOW_DAYS": MIN_WINDOW_DAYS,
        "MIN_TRADES": MIN_TRADES,
        "MIN_BARS": MIN_BARS,
        "MAX_OVERLAP_NUMERATOR": MAX_OVERLAP_NUMERATOR,
    }
)


# ══════════════════════════════════════════════════════════════════════════
# THE CRITERION CODES (Requirements 3.13, 3.14, 2.10)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 3.1 - the count is within bounds and every reference names a different run.
EV_COUNT = "EV_COUNT"
#: Requirement 3.1 and 3.13 - the run is the owner's own run of the submitted strategy.
EV_OWNERSHIP = "EV_OWNERSHIP"
#: Requirement 3.2 - completed, with a completion instant and no error text.
EV_COMPLETED = "EV_COMPLETED"
#: Requirement 3.4 - every run parameter is recorded.
EV_PARAMS = "EV_PARAMS"
#: Requirement 3.7 - the window is long enough.
EV_DURATION = "EV_DURATION"
#: Requirement 3.7 - the run produced enough trades to mean anything.
EV_TRADES = "EV_TRADES"
#: Requirement 3.8 - a recorded bar count, at or above the engine's own minimum.
EV_BARS = "EV_BARS"
#: Requirement 3.3 - one immutable Strategy_Version across the whole set.
EV_ONE_VERSION = "EV_ONE_VERSION"
#: Requirement 3.6 - pairwise different dataset fingerprints.
EV_CHECKSUMS = "EV_CHECKSUMS"
#: Requirement 3.5 and 3.6 - every pair is genuinely distinct.
EV_DISTINCT = "EV_DISTINCT"

#: The ten codes in the order :func:`validate` emits them. Set-level first, then per-condition,
#: then set-level again, then pairwise - see :func:`validate` for why that order is fixed.
EV_CODES: Tuple[str, ...] = (
    EV_COUNT,
    EV_OWNERSHIP,
    EV_COMPLETED,
    EV_PARAMS,
    EV_DURATION,
    EV_TRADES,
    EV_BARS,
    EV_ONE_VERSION,
    EV_CHECKSUMS,
    EV_DISTINCT,
)

#: One owner-actionable sentence per code, carrying no threshold digit, no internal
#: identifier, no column name, no table name and no query text (Requirement 2.10). Read-only
#: so a call site cannot rewrite a public sentence in passing.
EV_PUBLIC_MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        EV_COUNT: (
            "A submission must reference several different completed backtest runs of this "
            "strategy, and each reference must name a different run."
        ),
        EV_OWNERSHIP: (
            "Every referenced backtest run must be one of your own completed runs of this "
            "strategy."
        ),
        EV_COMPLETED: (
            "Every referenced backtest run must have finished successfully, with no reported "
            "error."
        ),
        EV_PARAMS: (
            "Every referenced backtest run must have recorded the full set of settings it "
            "ran with. Re-run any test that is missing them."
        ),
        EV_DURATION: (
            "Every referenced backtest run must cover a long enough stretch of market "
            "history."
        ),
        EV_TRADES: (
            "Every referenced backtest run must contain enough trades for its results to be "
            "meaningful."
        ),
        EV_BARS: (
            "Every referenced backtest run must have executed over enough market data, and "
            "must have recorded how much."
        ),
        EV_ONE_VERSION: (
            "Every referenced backtest run must test the same saved version of this strategy."
        ),
        EV_CHECKSUMS: (
            "Every referenced backtest run must use a different set of market data."
        ),
        EV_DISTINCT: (
            "Referenced backtest runs must test genuinely different conditions. Two runs over "
            "largely the same stretch of the same market data count as one test."
        ),
    }
)

#: Requirement 3.4's eight parameters, named once so ``EV_PARAMS`` and the immutable evidence
#: copy of Requirement 3.9 cannot drift apart on which fields "recorded" means.
REQUIRED_PARAMETER_FIELDS: Tuple[str, ...] = (
    "dataset",
    "start_date",
    "end_date",
    "initial_capital",
    "commission",
    "slippage",
    "dataset_checksum",
    "dag_hash",
)


# ══════════════════════════════════════════════════════════════════════════
# THE TWO VALUE TYPES
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CriterionOutcome:
    """One criterion's verdict, for one condition or one pair or the whole set.

    Frozen because an outcome is evidence of an evaluation: the gate records the list in the
    Audit_Log (Requirement 2.11) and the API renders it (Requirements 2.10, 3.14), and neither
    may edit a verdict in place on the way through.

    ``subjects`` names the Backtest_Conditions the verdict is about - one identifier for a
    per-condition criterion, two for a pairwise one, none for a set-level one - which is
    Requirement 3.14's "identifiers of the affected Backtest_Conditions".
    """

    #: One of :data:`EV_CODES`. Stable, machine-readable, and free of internal detail.
    code: str
    #: The verdict. ``False`` never stops the evaluation - see the module docstring.
    passed: bool
    #: The owner-actionable sentence, defaulted from :data:`EV_PUBLIC_MESSAGES`.
    message: str = ""
    #: The affected condition identifiers, as strings.
    subjects: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BacktestCondition:
    """One Backtest_Condition: one ``strategy_backtests`` row as this module reads it.

    Every field but the identifier is optional, and that is the point rather than laxity: a
    missing value has to reach a criterion in order to *fail* it. Coercing a ``NULL``
    ``executed_bar_count`` to ``0`` at the boundary, or defaulting an absent ``dataset``, would
    substitute a figure the evidence does not contain, which Requirements 3.8 and 3.11 forbid.

    :meth:`from_row` is the sanctioned adapter from a driver row. It reads only the columns
    Requirement 3 names and ignores everything else the row carries, so the validator's input
    surface stays exactly the evidence.
    """

    #: The source ``strategy_backtests`` row identifier, reported in ``subjects``.
    id: Optional[str] = None
    #: The run's owner. Compared against the strategy owner (Requirements 3.1, 3.13).
    user_id: Optional[str] = None
    #: The strategy the run was for. Compared against the submitted strategy (Req 3.1).
    strategy_id: Optional[str] = None
    #: The immutable Strategy_Version under test. ``None`` fails ``EV_ONE_VERSION`` (Req 3.3).
    version_id: Optional[str] = None
    #: The run status. Only ``'completed'`` passes (Requirement 3.2).
    status: Optional[str] = None
    #: The completion instant. Any non-empty value passes; the value itself is not compared.
    completed_at: Optional[Any] = None
    #: The run's error text. ``None`` or blank passes (Requirement 3.2).
    error_message: Optional[str] = None
    #: The market data set identifier. Requirement 3.5(a)'s distinctness input.
    dataset: Optional[str] = None
    #: Window start, inclusive (Requirements 3.5, 3.7).
    start_date: Optional[Any] = None
    #: Window end, inclusive (Requirements 3.5, 3.7).
    end_date: Optional[Any] = None
    #: Starting capital (Requirement 3.4).
    initial_capital: Optional[Any] = None
    #: Commission setting (Requirement 3.4).
    commission: Optional[Any] = None
    #: Slippage setting (Requirement 3.4).
    slippage: Optional[Any] = None
    #: The data fingerprint. Pairwise different across the set (Requirements 3.4, 3.6).
    dataset_checksum: Optional[str] = None
    #: The compiled-graph fingerprint (Requirement 3.4).
    dag_hash: Optional[str] = None
    #: Trades the run produced. ``None`` fails ``EV_TRADES`` (Requirement 3.7).
    total_trades: Optional[Any] = None
    #: Bars the run executed over, as recorded by the runtime. ``None`` fails ``EV_BARS``
    #: (Requirement 3.8) - it is never inferred from the equity curve or the trade count.
    executed_bar_count: Optional[Any] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "BacktestCondition":
        """A condition from a ``strategy_backtests`` row mapping.

        Absent keys and ``NULL`` values arrive as ``None`` and fail their criterion; no key is
        defaulted to a value that would pass one.
        """
        return cls(
            id=_as_text(row.get("id")),
            user_id=_as_text(row.get("user_id")),
            strategy_id=_as_text(row.get("strategy_id")),
            version_id=_as_text(row.get("version_id")),
            status=_as_text(row.get("status")),
            completed_at=row.get("completed_at"),
            error_message=_as_text(row.get("error_message")),
            dataset=_as_text(row.get("dataset")),
            start_date=row.get("start_date"),
            end_date=row.get("end_date"),
            initial_capital=row.get("initial_capital"),
            commission=row.get("commission"),
            slippage=row.get("slippage"),
            dataset_checksum=_as_text(row.get("dataset_checksum")),
            dag_hash=_as_text(row.get("dag_hash")),
            total_trades=row.get("total_trades"),
            executed_bar_count=row.get("executed_bar_count"),
        )


#: What :func:`validate` accepts per condition: the value type, or a row mapping.
ConditionLike = Union[BacktestCondition, Mapping[str, Any]]


def coerce_condition(condition: ConditionLike) -> BacktestCondition:
    """``condition`` as a :class:`BacktestCondition`, accepting a row mapping as well.

    One adapter, so the gate can pass driver rows straight through and a test can pass the
    value type, and both take exactly the same code path afterwards (Requirement 3.12).
    """
    if isinstance(condition, BacktestCondition):
        return condition
    if isinstance(condition, Mapping):
        return BacktestCondition.from_row(condition)
    raise TypeError(
        "a Backtest_Condition must be a BacktestCondition or a row mapping, "
        f"not {type(condition).__name__}"
    )


def coerce_conditions(
    conditions: Iterable[ConditionLike],
) -> Tuple[BacktestCondition, ...]:
    """Every element of ``conditions`` coerced, order preserved.

    Order is preserved because it fixes the order of the emitted outcomes, and a stable
    outcome order is half of Requirement 3.12's "the same per-criterion outcomes".
    """
    return tuple(coerce_condition(item) for item in conditions)


# ══════════════════════════════════════════════════════════════════════════
# THE WINDOW ARITHMETIC (Requirement 3.5)
# ══════════════════════════════════════════════════════════════════════════


def window_days(condition: ConditionLike) -> Optional[int]:
    """Inclusive calendar days in ``condition``'s ``[start_date, end_date]`` window.

    ``None`` when either endpoint is absent or unreadable, which is the honest answer for a
    window that was never recorded - ``EV_PARAMS`` reports it and ``EV_DURATION`` fails with
    it. A ``0`` would be a fabricated length, and Requirement 3.11 forbids fabricating.

    Inclusive per Requirement 3.7 ("between ``start_date`` and ``end_date`` inclusive"), so a
    single-day window is one day, not zero.
    """
    resolved = coerce_condition(condition)
    start = _as_date(resolved.start_date)
    end = _as_date(resolved.end_date)
    if start is None or end is None:
        return None
    return (end - start).days + 1


def intersection_days(a: ConditionLike, b: ConditionLike) -> Optional[int]:
    """Inclusive calendar days shared by two windows. ``0`` when they do not overlap.

    ``None`` only when a window is unreadable - Requirement 3.5's ``0`` is reserved for its
    own stated case, "the intersection length is 0 when the windows do not overlap", and is
    not overloaded to mean "unknown".

    Symmetric in its arguments, because ``max``, ``min`` and the day difference are, which is
    what makes :func:`distinct` symmetric (property P-33).
    """
    first = coerce_condition(a)
    second = coerce_condition(b)
    a_start, a_end = _as_date(first.start_date), _as_date(first.end_date)
    b_start, b_end = _as_date(second.start_date), _as_date(second.end_date)
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return None
    low = max(a_start, b_start)
    high = min(a_end, b_end)
    if high < low:
        return 0
    return (high - low).days + 1


def distinct(a: ConditionLike, b: ConditionLike) -> bool:
    """Requirement 3.5's distinctness predicate, in integers, with no rounding.

    Two conditions are distinct when either

    (a) their ``dataset`` values differ - a different market entirely is a different test
        whatever the dates say, so this short-circuits to ``True`` (Requirement 3.5(a),
        property P-36); or
    (b) ``intersection_days x MAX_OVERLAP_NUMERATOR <= min(window_days(a), window_days(b))``,
        which is Requirement 3.5(b)'s "at most 25 percent of the shorter window" with the
        percentage moved to the other side of the comparison so nothing has to be divided or
        rounded.

    Symmetric, because every operand of both branches is (property P-33). Irreflexive for any
    real window, because a condition against itself has equal datasets and reduces to
    ``w * 4 <= w``, false for every ``w >= 1`` (property P-34).

    ``False`` - not distinct - whenever distinctness cannot be *established*: an unreadable
    window, or a non-positive one. Refusing to certify is the conservative direction here,
    since a wrongly-distinct pair is exactly what Requirement 3 exists to prevent, and it
    keeps the predicate symmetric and total. ``EV_PARAMS`` and ``EV_DURATION`` are what tell
    the owner *why* such a pair could not be judged.
    """
    first = coerce_condition(a)
    second = coerce_condition(b)

    if (
        first.dataset is not None
        and second.dataset is not None
        and first.dataset != second.dataset
    ):
        return True

    first_days = window_days(first)
    second_days = window_days(second)
    if first_days is None or second_days is None:
        return False
    if first_days <= 0 or second_days <= 0:
        return False

    overlap = intersection_days(first, second)
    if overlap is None:  # pragma: no cover - unreachable once both windows resolved
        return False
    return overlap * MAX_OVERLAP_NUMERATOR <= min(first_days, second_days)


# ══════════════════════════════════════════════════════════════════════════
# THE VALIDATION (Requirements 3.1 … 3.8, 3.12, 3.13, 3.14)
# ══════════════════════════════════════════════════════════════════════════


def validate(
    conditions: Sequence[ConditionLike],
    owner_id: Any,
    strategy_id: Any,
) -> List[CriterionOutcome]:
    """Every criterion of Requirement 3, evaluated against ``conditions``.

    Returns one :class:`CriterionOutcome` per criterion evaluation - the set-level criteria
    once each, the per-condition criteria once per condition, and ``EV_DISTINCT`` once per
    unordered pair - in a fixed order:

    1. ``EV_COUNT`` for the set.
    2. ``EV_OWNERSHIP``, ``EV_COMPLETED``, ``EV_PARAMS``, ``EV_DURATION``, ``EV_TRADES`` and
       ``EV_BARS`` for each condition, in the order the conditions arrived.
    3. ``EV_ONE_VERSION`` and ``EV_CHECKSUMS`` for the set.
    4. ``EV_DISTINCT`` for each unordered pair, in input order.

    Nothing short-circuits, so the caller can report every failure in one response
    (Requirements 2.10, 3.14). Nothing is read, cached or timed, so the same rows produce the
    same list every time (Requirement 3.12).

    ``owner_id`` is the strategy owner and ``strategy_id`` the submitted strategy; a condition
    matching neither fails ``EV_OWNERSHIP`` with the same code and sentence a non-existent row
    produces (Requirement 3.13).
    """
    resolved = coerce_conditions(conditions)
    outcomes: List[CriterionOutcome] = []

    # ── Requirement 3.1: how many, and all different runs ──────────────
    count_in_range = MIN_CONDITIONS <= len(resolved) <= MAX_CONDITIONS
    identifiers = [c.id for c in resolved if c.id is not None]
    references_are_distinct = len(set(identifiers)) == len(identifiers)
    outcomes.append(_outcome(EV_COUNT, count_in_range and references_are_distinct))

    # ── The per-condition criteria (Requirements 3.2, 3.4, 3.7, 3.8) ───
    for condition in resolved:
        subjects = _subjects(condition)

        # Requirement 3.1 and 3.13. Identical verdict for a foreign row and an absent one.
        outcomes.append(
            _outcome(
                EV_OWNERSHIP,
                _same_id(condition.user_id, owner_id)
                and _same_id(condition.strategy_id, strategy_id),
                subjects,
            )
        )

        # Requirement 3.2. Completed, with a completion instant, and no error text.
        outcomes.append(
            _outcome(
                EV_COMPLETED,
                _as_text(condition.status) is not None
                and str(condition.status).strip().lower() == "completed"
                and _is_present(condition.completed_at)
                and _as_text(condition.error_message) is None,
                subjects,
            )
        )

        # Requirement 3.4. Every parameter recorded, and readable as what it claims to be.
        outcomes.append(_outcome(EV_PARAMS, _parameters_recorded(condition), subjects))

        # Requirement 3.7. A long enough window. ``None`` days fails, never defaults.
        days = window_days(condition)
        outcomes.append(
            _outcome(
                EV_DURATION, days is not None and days >= MIN_WINDOW_DAYS, subjects
            )
        )

        # Requirement 3.7. Enough trades to be statistically meaningful.
        trades = _as_int(condition.total_trades)
        outcomes.append(
            _outcome(EV_TRADES, trades is not None and trades >= MIN_TRADES, subjects)
        )

        # Requirement 3.8. A *recorded* bar count. Nothing is inferred from anything else.
        bars = _as_int(condition.executed_bar_count)
        outcomes.append(
            _outcome(EV_BARS, bars is not None and bars >= MIN_BARS, subjects)
        )

    # ── Requirement 3.3: one immutable Strategy_Version across the set ─
    versions = {c.version_id for c in resolved}
    outcomes.append(
        _outcome(EV_ONE_VERSION, len(versions) == 1 and None not in versions)
    )

    # ── Requirement 3.6: pairwise different data fingerprints ──────────
    checksums = [c.dataset_checksum for c in resolved]
    outcomes.append(
        _outcome(
            EV_CHECKSUMS,
            len(set(checksums)) == len(checksums) and None not in checksums,
        )
    )

    # ── Requirements 3.5 and 3.6: every pair genuinely distinct ────────
    for first, second in combinations(resolved, 2):
        outcomes.append(
            _outcome(
                EV_DISTINCT,
                distinct(first, second),
                _subjects(first) + _subjects(second),
            )
        )

    return outcomes


def all_passed(outcomes: Iterable[CriterionOutcome]) -> bool:
    """Whether every outcome passed - the evidence set's admit decision.

    An empty list is vacuously ``True``, which is why the gate calls :func:`validate` rather
    than trusting a bare list: ``EV_COUNT`` is what refuses an empty set, and it is always
    emitted.
    """
    return all(outcome.passed for outcome in outcomes)


def failed_outcomes(outcomes: Iterable[CriterionOutcome]) -> List[CriterionOutcome]:
    """The failures alone, in evaluation order, for the one response of Requirement 3.14."""
    return [outcome for outcome in outcomes if not outcome.passed]


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════════════════════════════════════════


def _outcome(
    code: str,
    passed: bool,
    subjects: Tuple[str, ...] = (),
) -> CriterionOutcome:
    """One outcome, with its sentence taken from the one place sentences are written."""
    return CriterionOutcome(
        code=code,
        passed=bool(passed),
        message=EV_PUBLIC_MESSAGES[code],
        subjects=subjects,
    )


def _subjects(condition: BacktestCondition) -> Tuple[str, ...]:
    """``condition``'s identifier as a ``subjects`` tuple, empty when it has none."""
    return (condition.id,) if condition.id is not None else ()


def _parameters_recorded(condition: BacktestCondition) -> bool:
    """Whether all eight of Requirement 3.4's parameters are recorded and readable.

    "Recorded" is read strictly: a value that is present but cannot be read as the kind of
    thing the column holds - a date that does not parse, a capital figure that is not a
    number - has not recorded the parameter in any usable sense, and admitting it would leave
    the later criteria comparing against something they cannot interpret.
    """
    if any(getattr(condition, name) is None for name in REQUIRED_PARAMETER_FIELDS):
        return False
    if _as_date(condition.start_date) is None or _as_date(condition.end_date) is None:
        return False
    return all(
        _as_decimal(getattr(condition, name)) is not None
        for name in ("initial_capital", "commission", "slippage")
    )


def _is_present(value: Any) -> bool:
    """Whether ``value`` is a recorded value rather than a ``NULL`` or a blank string."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _as_text(value: Any) -> Optional[str]:
    """``value`` as a non-blank string, or ``None``.

    A blank string is ``None`` here so that ``''`` and ``NULL`` behave identically - which is
    what Requirement 3.2 asks for ``error_message`` ("null or empty") and what keeps an empty
    ``dataset_checksum`` from passing as a recorded fingerprint.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _same_id(left: Any, right: Any) -> bool:
    """Whether two identifiers denote the same row owner or strategy.

    Compared as case-folded text: a UUID may arrive as a ``uuid.UUID`` from one driver and as
    its hex string from another, and hex is case-insensitive. A missing identifier on either
    side is never a match - an unreadable owner does not authorise anything.
    """
    left_text = _as_text(left)
    right_text = _as_text(right)
    if left_text is None or right_text is None:
        return False
    return left_text.casefold() == right_text.casefold()


def _as_date(value: Any) -> Optional[date]:
    """``value`` as a calendar date, or ``None`` when it is absent or unreadable.

    Accepts a ``date``, a ``datetime`` (its date part - the window is stated in calendar days,
    so the clock time plays no part), and an ISO-8601 date or timestamp string, including the
    ``Z`` suffix PostgREST returns. Anything else is unreadable rather than guessed at.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _as_text(value)
    if text is None:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(candidate[:10])
    except ValueError:
        return None


def _as_decimal(value: Any) -> Optional[Decimal]:
    """``value`` as a finite ``Decimal``, or ``None`` when unreadable or not finite.

    ``Decimal`` rather than ``float`` because these are money and rate parameters, and the
    only question asked of them here is "was it recorded" - so nothing is gained by moving
    them through binary floating point on the way to that answer. ``bool`` is refused because
    ``isinstance(True, int)`` is ``True`` in Python, and ``True`` is not a recorded capital.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    text = _as_text(value)
    if text is None:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _as_int(value: Any) -> Optional[int]:
    """``value`` as a whole count, or ``None`` when it is absent or unreadable.

    A fractional value is floored, so a nonsensical count can only ever fail its threshold
    more easily, never pass one it would not have passed as a whole number. ``bool`` is
    refused for the same reason as in :func:`_as_decimal`.
    """
    parsed = _as_decimal(value)
    if parsed is None:
        return None
    return int(parsed.to_integral_value(rounding="ROUND_FLOOR"))
