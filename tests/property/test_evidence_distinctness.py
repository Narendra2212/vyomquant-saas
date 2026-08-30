"""Property tests for Backtest_Condition distinctness (Requirement 3, properties P-33 … P-40).

Spec: marketplace-subscriptions-paper-trading, task 6.2 onwards. ``design.md`` maps
P-33 … P-40 to this one module, generated from
``tests/strategies/marketplace_generators.py``'s ``backtest_conditions()`` and
``evidence_sets()``.

The subject is ``backend_app/backend/marketplace/evidence_validator.py`` - a pure module
whose import pulls only the standard library, so every property here runs with no database,
no fixture, no clock and no network.

HOW THIS FILE GROWS
-------------------
One property, one test function, appended in property order:

* P-33 ``test_p33_distinct_is_symmetric``            (task 6.2, here)
* P-34 ``test_p34_distinct_is_irreflexive``          (task 6.3)
* P-35 ``test_p35_overlap_threshold_and_shrink_preserves_distinctness`` (task 6.4)
* P-36 ``test_p36_differing_datasets_are_always_distinct``             (task 6.5)
* P-37 ``test_p37_evidence_set_admission_iff_every_criterion``         (task 6.6)
* P-38 ``test_p38_revalidation_is_idempotent``                         (task 6.7)
* P-39, P-40                                                           (tasks 6.8, 6.9)

Later tasks append their generator helpers and their test below the existing ones and add
nothing above them, so two agents editing this module do not collide. :data:`property_test`
is the shared settings decorator every one of them applies - the design's minimum of 100
examples, written once here rather than restated per test.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from backend_app.backend.marketplace.evidence_validator import (
    MAX_OVERLAP_NUMERATOR,
    MIN_WINDOW_DAYS,
    distinct,
    window_days,
)
from tests.strategies.marketplace_generators import DATASETS, backtest_conditions

#: The design's property-test configuration: at least 100 examples, no deadline (the row
#: builder is a large draw, not slow production code), and ``derandomize=False`` so
#: ``.hypothesis/`` keeps accumulating failing examples across runs.
property_test = settings(
    max_examples=100,
    deadline=None,
    derandomize=False,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# Shared generator: a pair of conditions whose relationship is worth testing
# ---------------------------------------------------------------------------


@st.composite
def condition_pairs(
    draw,
    *,
    satisfying: Optional[bool] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Two ``strategy_backtests`` rows, drawn so both branches of ``distinct`` are reached.

    A pair of independently drawn rows almost always differs in ``dataset``, which makes
    Requirement 3.5(a) short-circuit and leaves 3.5(b)'s window arithmetic untested. So the
    second row is drawn with:

    * the first row's ``dataset`` roughly half the time, forcing the overlap comparison;
    * a start date offset from the first row's by ``-450 … +450`` days, so the windows land
      identical, nested, partially overlapping and fully disjoint across a run.

    ``satisfying=None`` (the default) lets each row be well formed or defective, so absent
    dates, unreadable windows and short windows - the cases where ``distinct`` refuses to
    certify rather than computing - are in the input space too.
    """
    first = draw(backtest_conditions(satisfying=satisfying))

    share_dataset = draw(st.booleans())
    dataset = first["dataset"] if share_dataset else None

    start = first.get("start_date")
    if isinstance(start, date):
        offset = draw(st.integers(min_value=-450, max_value=450))
        candidate = start + timedelta(days=offset)
        # Stay inside the generator's own date range so no draw is wasted.
        second_start = candidate if date(2000, 1, 1) <= candidate else None
    else:
        second_start = None

    second = draw(
        backtest_conditions(
            dataset=dataset,
            start_date=second_start,
            satisfying=satisfying,
        )
    )
    return first, second


# ---------------------------------------------------------------------------
# P-33
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 33: For all pairs of
# Backtest_Conditions (c1, c2): distinct(c1, c2) == distinct(c2, c1).
#
# Requirement 3.5 states distinctness as a relation between two conditions and gives no
# privileged first argument, and Requirement 3.6 applies it to "every pair" - an unordered
# pair. If the predicate disagreed with itself on argument order, then EV_DISTINCT's verdict
# for a Submission would depend on the order eligibility_gate happened to read the rows in,
# and the same evidence set could be admitted or rejected by the order of a SELECT.


@property_test
@given(pair=condition_pairs())
def test_p33_distinct_is_symmetric(pair: Tuple[Dict[str, Any], Dict[str, Any]]) -> None:
    """``distinct`` gives the same verdict whichever condition is named first.

    **Validates: Requirements 3.5, 3.6**
    """
    first, second = pair

    forward = distinct(first, second)
    backward = distinct(second, first)

    assert forward == backward, (
        "distinct is not symmetric: "
        f"distinct(a, b)={forward} but distinct(b, a)={backward} for "
        f"a=(dataset={first.get('dataset')!r}, "
        f"start={first.get('start_date')!r}, end={first.get('end_date')!r}) and "
        f"b=(dataset={second.get('dataset')!r}, "
        f"start={second.get('start_date')!r}, end={second.get('end_date')!r})"
    )

# ---------------------------------------------------------------------------
# P-34
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 34: For all
# Backtest_Conditions c: distinct(c, c) is false.
#
# Requirement 3.5 asks whether two conditions test *genuinely different* conditions, and
# Requirement 3.6 applies that to every pair in the evidence set. A condition is never a
# genuinely different test from itself, so if `distinct` were reflexive, an owner could
# satisfy EV_DISTINCT with the same run listed several times: the count check of Requirement
# 3.1 would see three references, and the pairwise check would wave them through. The two
# branches of `distinct` are what rule that out - equal datasets deny branch (a), and the
# self-overlap `w * 4 <= w` denies branch (b) for every positive `w` - and this property
# holds both of them to it at once.
#
# NON-VACUITY. `distinct` also returns False whenever distinctness cannot be *established*
# (an unreadable or non-positive window), so a run that happened to draw only defective rows
# would pass this property without ever exercising the window arithmetic that is the
# interesting half of the claim. The test therefore counts how many drawn conditions had a
# real readable positive window and asserts a meaningful share of them did - which is why the
# Hypothesis-driven part is an inner function: the share is a fact about the whole run, not
# about any single example, and can only be asserted once the run is over.


def test_p34_distinct_is_irreflexive() -> None:
    """No condition is ever distinct from itself, whatever its window.

    **Validates: Requirements 3.5, 3.6**
    """
    examples = 0
    readable = 0

    @property_test
    @given(condition=backtest_conditions(satisfying=None))
    def check(condition: Dict[str, Any]) -> None:
        nonlocal examples, readable
        examples += 1

        days = window_days(condition)
        has_real_window = days is not None and days > 0
        if has_real_window:
            readable += 1
        event(f"readable positive window: {has_real_window}")

        assert distinct(condition, condition) is False, (
            "distinct is reflexive: a condition was judged distinct from itself for "
            f"(dataset={condition.get('dataset')!r}, "
            f"start={condition.get('start_date')!r}, "
            f"end={condition.get('end_date')!r}, window_days={days!r})"
        )

    check()

    # The inner function is what carries the property, so a run that generated nothing would
    # otherwise pass silently.
    assert examples >= 100, f"the property body ran on only {examples} conditions"

    # The property is only meaningful if branch (b)'s arithmetic actually ran. A quarter of
    # the draws is a floor far below what the generator produces (it nulls a window endpoint
    # only via the `null_parameter` defect) and far above what a degenerate run would.
    assert readable * 4 >= examples, (
        "vacuous run: only "
        f"{readable} of {examples} drawn conditions had a readable positive window, so "
        "irreflexivity was mostly established by distinct() refusing to certify rather than "
        "by the overlap arithmetic"
    )

# ---------------------------------------------------------------------------
# P-35
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 35 (metamorphic, overlap
# threshold): For all pairs with equal `dataset` values, `distinct` is true if and only if the
# calendar-day overlap of the two windows is at most 25 percent of the shorter window's
# calendar-day length; and shrinking the overlap of a distinct pair keeps it distinct.
#
# Requirement 3.5(b) is the only quantitative half of distinctness, and it is the half an
# owner can game: re-run the same strategy over two windows that differ by a fortnight and
# call it two tests. The `iff` matters in both directions. Were `distinct` too generous, a
# pair sharing most of its history would be admitted and Requirement 3's whole purpose would
# be defeated; were it too strict, an owner with three genuinely separate windows would be
# refused with no way to comply. Requirement 3.5 also fixes the arithmetic - inclusive
# calendar days, `0` for disjoint windows, no rounding - so this property checks the
# *quantity*, not just the direction of the comparison.
#
# THE ORACLE. The overlap is recomputed here from ordinal day sets:
# `set(range(start.toordinal(), end.toordinal() + 1))` per window, intersected. That is
# deliberately slower and obviously correct - it counts the shared days one by one, over the
# proleptic Gregorian ordinals that carry leap years and month lengths for free - and it
# shares no code with `intersection_days`'s `max`/`min` endpoint arithmetic, so an off-by-one
# or a mishandled boundary in the subject cannot hide behind the same mistake in the check.
# The 25 percent is written as `overlap * 100 <= 25 * shorter` from the requirement's own
# words rather than reusing `MAX_OVERLAP_NUMERATOR`; the test asserts once that the module's
# numerator really does encode 25 percent, so a change to that constant surfaces here as a
# failure instead of silently redefining the property.
#
# THE METAMORPHIC HALF. Shrinking is done by sliding the later window further away, which
# leaves both window *lengths* untouched - so the shorter window, and with it the threshold,
# is unchanged and only the overlap moves. A pair that was distinct must stay distinct.
#
# WHY THE PAIRS ARE DRAWN WELL FORMED. `distinct` returns False whenever distinctness cannot
# be *established* (an unreadable or non-positive window), which is a refusal rather than a
# verdict about overlap. The `iff` is claimed only where a verdict exists, so both rows are
# drawn `satisfying=True`: equal `dataset`, real dates, positive windows. P-33 and P-34
# already cover the defective rows.


@st.composite
def same_dataset_pairs_with_shift(
    draw,
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """Two well-formed rows sharing a ``dataset``, plus a shift for the shrink step.

    The second row's start is derived from a drawn *target overlap* rather than from a blind
    date offset, and the target from a named ``mode``, because the interesting region is one
    day wide: with windows of 90 … 400 days the threshold sits at ``shorter // 4``, so
    uniformly drawn offsets land on one side of it and never on it. Each mode is drawn about
    as often as the others, so every mutation of the comparison fails on some run:

    * ``exact_quarter`` - the shorter window is a multiple of four and the overlap is exactly a
      quarter of it, so ``overlap x 4 == shorter``. This is the one input that distinguishes
      ``<=`` from ``<``; Requirement 3.5 says *at most* 25 percent, so it must be distinct.
    * ``just_over`` - one day past the threshold, the smallest overlap that must *not* be
      distinct. An off-by-one anywhere in the intersection count shows up here.
    * ``at_threshold`` - the three days around the threshold.
    * ``coarse`` - disjoint (``0``), barely touching (``1``) and fully nested (``shorter``).
    * ``uniform`` - anywhere in range, so the property is not only ever checked on edges.

    ``before`` decides whether the second window precedes or follows the first, so the pair is
    built with each row in turn supplying the ``max(start)`` of the intersection. Both
    constructions give an intersection of exactly ``target`` days; the oracle in the test
    checks that rather than trusting it.

    The third element is the number of days the shrink step slides the later window by.
    """
    mode = draw(
        st.sampled_from(
            ("exact_quarter", "just_over", "at_threshold", "coarse", "uniform")
        )
    )

    if mode == "exact_quarter":
        # Both spans multiples of four with the first the shorter, so ``shorter % 4 == 0``
        # and a quarter of it is a whole number of days.
        quarter = draw(
            st.integers(min_value=(MIN_WINDOW_DAYS + 3) // 4, max_value=100)
        )
        first_span = 4 * quarter
        second_span = 4 * draw(st.integers(min_value=quarter, max_value=100))
    else:
        first_span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=400))
        second_span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=400))

    first = draw(backtest_conditions(satisfying=True, window_days=first_span))

    shorter = min(first_span, second_span)
    threshold = shorter // 4
    if mode == "exact_quarter":
        target = threshold
    elif mode == "just_over":
        target = threshold + 1
    elif mode == "at_threshold":
        target = draw(st.sampled_from((threshold - 1, threshold, threshold + 1)))
    elif mode == "coarse":
        target = draw(st.sampled_from(sorted({0, 1, shorter})))
    else:
        target = draw(st.integers(min_value=0, max_value=shorter))
    target = max(0, min(target, shorter))

    before = draw(st.booleans())
    if before:
        second_start = first["start_date"] - timedelta(days=second_span - target)
    else:
        second_start = first["start_date"] + timedelta(days=first_span - target)

    second = draw(
        backtest_conditions(
            satisfying=True,
            dataset=first["dataset"],
            start_date=second_start,
            window_days=second_span,
        )
    )
    shift = draw(st.integers(min_value=1, max_value=200))
    return first, second, shift


def _ordinal_window(condition: Dict[str, Any]) -> set:
    """Every calendar day of ``condition``'s inclusive window, as ordinal day numbers.

    The slow, obvious implementation: one element per day, so the count *is* the length and
    there is no endpoint arithmetic to get wrong.
    """
    start: date = condition["start_date"]
    end: date = condition["end_date"]
    return set(range(start.toordinal(), end.toordinal() + 1))


def _oracle_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    """Calendar days shared by two windows, counted by intersecting their day sets."""
    return len(_ordinal_window(a) & _ordinal_window(b))


def _slide_away(
    condition: Dict[str, Any],
    reference: Dict[str, Any],
    shift: int,
) -> Dict[str, Any]:
    """``condition`` moved ``shift`` days further from ``reference``, same window length.

    Sliding rather than trimming is what makes this a valid shrink: the window keeps its
    length, so ``min(window_days(a), window_days(b))`` - and therefore the 25 percent
    threshold - is exactly what it was, and the only thing that changed is the overlap.
    """
    away = shift if condition["start_date"] >= reference["start_date"] else -shift
    delta = timedelta(days=away)
    return dict(
        condition,
        start_date=condition["start_date"] + delta,
        end_date=condition["end_date"] + delta,
    )


def test_p35_overlap_threshold_and_shrink_preserves_distinctness() -> None:
    """Equal datasets: distinct iff overlap is within 25 percent, and shrinking preserves it.

    **Validates: Requirements 3.5**
    """
    # The module states the percentage as a numerator over an implicit four; if that ever
    # changes, this property is about a different rule and must be rewritten rather than
    # quietly re-interpreted.
    assert MAX_OVERLAP_NUMERATOR == 4, (
        "P-35 encodes Requirement 3.5's 25 percent; evidence_validator now uses "
        f"MAX_OVERLAP_NUMERATOR={MAX_OVERLAP_NUMERATOR}"
    )

    examples = 0
    judged_distinct = 0
    judged_not_distinct = 0
    at_exact_quarter = 0
    at_first_failing_day = 0
    shrinks_checked = 0

    @property_test
    @given(drawn=same_dataset_pairs_with_shift())
    def check(drawn: Tuple[Dict[str, Any], Dict[str, Any], int]) -> None:
        nonlocal examples, judged_distinct, judged_not_distinct
        nonlocal at_exact_quarter, at_first_failing_day, shrinks_checked
        examples += 1

        first, second, shift = drawn

        assert first["dataset"] == second["dataset"], "the pair must share a dataset"

        first_days = len(_ordinal_window(first))
        second_days = len(_ordinal_window(second))
        overlap = _oracle_overlap(first, second)
        shorter = min(first_days, second_days)

        # The two window lengths the subject computes must agree with the day-set count
        # before any claim about their ratio means anything.
        assert window_days(first) == first_days
        assert window_days(second) == second_days

        # Requirement 3.5(b), in the requirement's own words: at most 25 percent of the
        # shorter window, evaluated without rounding.
        within_threshold = overlap * 100 <= 25 * shorter
        verdict = distinct(first, second)

        if verdict:
            judged_distinct += 1
        else:
            judged_not_distinct += 1
        # The two inputs that pin the comparison down to the day: an overlap of exactly a
        # quarter (which "at most 25 percent" must admit, so `<` would be wrong) and the very
        # next day (which it must refuse, so `<=` on a miscounted intersection would be wrong).
        if overlap * 4 == shorter:
            at_exact_quarter += 1
        if overlap == shorter // 4 + 1:
            at_first_failing_day += 1
        event(f"within 25 percent: {within_threshold}")

        assert verdict is within_threshold, (
            "distinct disagrees with the 25 percent rule for equal datasets: "
            f"distinct={verdict} but overlap={overlap} of a shorter window of {shorter} days "
            f"({'within' if within_threshold else 'over'} 25 percent) for "
            f"a=[{first['start_date']} … {first['end_date']}] ({first_days} days), "
            f"b=[{second['start_date']} … {second['end_date']}] ({second_days} days)"
        )

        # ── The metamorphic half: shrink a distinct pair's overlap ──────
        if verdict:
            shifted = _slide_away(second, first, shift)
            shrunk = _oracle_overlap(first, shifted)

            # Sliding away can only reduce the shared days; if this ever failed the
            # transformation would not be a shrink and the claim below would be untested.
            assert shrunk <= overlap, (
                "sliding the later window away increased the overlap, from "
                f"{overlap} to {shrunk} days, so the shrink step is not a shrink"
            )
            assert len(_ordinal_window(shifted)) == second_days, (
                "the shrink step changed a window length, so the 25 percent threshold moved "
                "and the pair is no longer comparable"
            )

            if shrunk < overlap:
                shrinks_checked += 1

            assert distinct(first, shifted) is True, (
                "shrinking a distinct pair's overlap lost distinctness: overlap went from "
                f"{overlap} to {shrunk} days against a shorter window of {shorter} days, "
                f"after sliding b by {shift} days to "
                f"[{shifted['start_date']} … {shifted['end_date']}]"
            )

    check()

    assert examples >= 100, f"the property body ran on only {examples} pairs"

    # NON-VACUITY. An `iff` proven only on one side of the threshold is half a property, a
    # metamorphic claim never exercised is none, and an `iff` never checked *on* the boundary
    # would pass against `<` as happily as against `<=`. Each floor was set by mutating
    # `distinct` - dropping the inclusive `+ 1` from the intersection, and narrowing `<=` to
    # `<` - and checking the corresponding counter is what makes the mutation fail.
    assert judged_distinct >= 10, (
        f"only {judged_distinct} of {examples} pairs were judged distinct, so the "
        "'within 25 percent implies distinct' direction is barely tested"
    )
    assert judged_not_distinct >= 10, (
        f"only {judged_not_distinct} of {examples} pairs were judged not distinct, so the "
        "'over 25 percent implies not distinct' direction is barely tested"
    )
    assert at_exact_quarter >= 5, (
        f"only {at_exact_quarter} of {examples} pairs had an overlap of exactly a quarter of "
        "the shorter window, the one input that tells 'at most 25 percent' apart from 'under "
        "25 percent'"
    )
    assert at_first_failing_day >= 5, (
        f"only {at_first_failing_day} of {examples} pairs sat one day over the threshold, the "
        "smallest overlap that must be refused"
    )
    assert shrinks_checked >= 5, (
        f"only {shrinks_checked} of {examples} pairs had their overlap genuinely shrunk, so "
        "the metamorphic half of P-35 is barely tested"
    )

# ---------------------------------------------------------------------------
# P-36
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 36 (invariant, dataset difference
# sufficiency): For all pairs with different `dataset` values, `distinct` is true regardless of
# window overlap.
#
# Requirement 3.5 joins its two criteria with "at least one of the following holds", so (a) is
# sufficient on its own: two runs over different market data are different tests whatever their
# dates say, and an owner who tested the same idea on BTC and on NIFTY over the very same
# calendar quarter has produced two conditions, not one. `distinct` implements that as an early
# `return True` before any window is read, which is the only reason the pair survives - branch
# (b) would refuse an identical window outright, since `w * 4 <= w` is false for every positive
# `w`. So this property is not a corollary of P-35; it is the claim that the short-circuit is
# there and is reached.
#
# WHY "REGARDLESS OF OVERLAP" DECIDES THE GENERATOR. Two independently drawn rows almost never
# share a window, and a pair that overlaps by nothing is distinct under *either* branch - such a
# draw cannot tell an implementation that short-circuits from one that fell through to the
# arithmetic and happened to be let past. The input that separates them is a *high* overlap, so
# `differing_dataset_pairs` builds the overlap on purpose and puts the fully identical window -
# 100 percent, the most hostile case branch (b) has - in the pool as a named mode.
#
# THE WITNESS. For every drawn pair whose overlap is over Requirement 3.5(b)'s 25 percent, the
# test also checks the same pair with the datasets made *equal*, and requires that twin to be
# judged not distinct. That is what turns "distinct was true" into evidence about branch (a):
# the windows alone would have failed the pair, and the differing `dataset` is the only thing
# that changed. Without it, an implementation that simply returned `True` everywhere would
# satisfy P-36's assertion on every example.
#
# WHY DEFECTIVE ROWS ARE IN THE POOL TOO. `distinct` returns False whenever distinctness cannot
# be *established* from the windows - an absent endpoint, a non-positive span. Requirement
# 3.5(a) needs no window at all, so a differing `dataset` must certify even then, and the
# `defective` mode carries exactly those rows. The `dataset` of every drawn row is overwritten
# after the draw, because the generator's own `null_parameter` defect can null it, and a `None`
# dataset is not a *differing* dataset - it is an unrecorded one, which is `EV_PARAMS`'s business
# and not this property's.


@st.composite
def differing_dataset_pairs(
    draw,
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Two rows with different ``dataset`` values, across the whole overlap range.

    The window geometry comes from a named ``mode`` rather than a blind date offset, so the
    hostile cases are hit on every run instead of being left to chance:

    * ``identical`` - one window, drawn once and used twice: overlap is 100 percent of both
      windows. The single input that distinguishes the ``dataset`` short-circuit from the
      overlap arithmetic, since branch (b) refuses this pair for any positive window.
    * ``nested`` - the second window strictly inside the first, so the overlap is the whole of
      the shorter window: 100 percent again, with unequal lengths.
    * ``partial`` - an overlap drawn anywhere from ``0`` to the shorter window, on either side of
      the first window, built the same way :func:`same_dataset_pairs_with_shift` builds it.
    * ``disjoint`` - a gap of 1 … 500 days, the case both branches would admit.
    * ``defective`` - two independently drawn rows that may be defective, so sub-minimum windows
      and every other per-condition defect are in the input space.
    * ``unreadable`` - the same, with one endpoint of one row nulled outright, so there is *no*
      window to compute an overlap from. Left to the ``null_parameter`` defect this case turns
      up only by luck (it has eight columns to choose from); Requirement 3.5(a) claims to
      certify without any window at all, so it is drawn on purpose.

    The third element is the mode, reported in failure messages so a counterexample says which
    geometry broke.
    """
    left_dataset, right_dataset = draw(
        st.lists(st.sampled_from(DATASETS), min_size=2, max_size=2, unique=True)
    )
    mode = draw(
        st.sampled_from(
            ("identical", "nested", "partial", "disjoint", "defective", "unreadable")
        )
    )

    if mode in ("defective", "unreadable"):
        first = draw(backtest_conditions(satisfying=None))
        second = draw(backtest_conditions(satisfying=None))
        if mode == "unreadable":
            blanks = draw(
                st.lists(
                    st.sampled_from(("start_date", "end_date")),
                    min_size=1,
                    max_size=2,
                    unique=True,
                )
            )
            if draw(st.booleans()):
                first = dict(first, **{name: None for name in blanks})
            else:
                second = dict(second, **{name: None for name in blanks})
    else:
        first_span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=400))
        first = draw(backtest_conditions(satisfying=True, window_days=first_span))
        start: date = first["start_date"]

        if mode == "identical":
            second_span = first_span
            second_start = start
        elif mode == "nested":
            second_span = draw(
                st.integers(min_value=MIN_WINDOW_DAYS, max_value=first_span)
            )
            inset = draw(st.integers(min_value=0, max_value=first_span - second_span))
            second_start = start + timedelta(days=inset)
        elif mode == "partial":
            second_span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=400))
            target = draw(st.integers(min_value=0, max_value=min(first_span, second_span)))
            if draw(st.booleans()):
                second_start = start - timedelta(days=second_span - target)
            else:
                second_start = start + timedelta(days=first_span - target)
        else:  # disjoint - the second window begins after the first has ended
            second_span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=400))
            gap = draw(st.integers(min_value=1, max_value=500))
            second_start = start + timedelta(days=first_span - 1 + gap)

        second = draw(
            backtest_conditions(
                satisfying=True,
                start_date=second_start,
                window_days=second_span,
            )
        )

    # Set after the draw, not through the generator's ``dataset`` argument, so a
    # ``null_parameter`` defect cannot leave a ``None`` here and turn "different datasets" into
    # "unrecorded dataset" - a different claim, belonging to EV_PARAMS.
    return (
        dict(first, dataset=left_dataset),
        dict(second, dataset=right_dataset),
        mode,
    )


def _oracle_overlap_share(
    a: Dict[str, Any],
    b: Dict[str, Any],
) -> Optional[Tuple[int, int]]:
    """``(shared days, shorter window length)`` from the day-set oracle, or ``None``.

    ``None`` when either window is unreadable or empty, which is the only honest answer for a
    row that never recorded its dates - and the case where the overlap arithmetic has nothing
    to say, so no claim about the threshold can be made about it.
    """
    for condition in (a, b):
        if not isinstance(condition.get("start_date"), date):
            return None
        if not isinstance(condition.get("end_date"), date):
            return None
    left = _ordinal_window(a)
    right = _ordinal_window(b)
    if not left or not right:
        return None
    return len(left & right), min(len(left), len(right))


def test_p36_differing_datasets_are_always_distinct() -> None:
    """A differing ``dataset`` makes a pair distinct, whatever the windows do.

    **Validates: Requirements 3.5**
    """
    examples = 0
    identical_windows = 0
    over_threshold = 0
    witnessed = 0
    unreadable = 0

    @property_test
    @given(drawn=differing_dataset_pairs())
    def check(drawn: Tuple[Dict[str, Any], Dict[str, Any], str]) -> None:
        nonlocal examples, identical_windows, over_threshold, witnessed, unreadable
        examples += 1

        first, second, mode = drawn

        # The generator's contract, and the property's precondition: two *recorded* and
        # different datasets.
        assert first["dataset"] is not None and second["dataset"] is not None
        assert first["dataset"] != second["dataset"]

        share = _oracle_overlap_share(first, second)
        if share is None:
            unreadable += 1
            overlap = shorter = None
            past_threshold = False
        else:
            overlap, shorter = share
            past_threshold = overlap * 100 > 25 * shorter
            if overlap == shorter and window_days(first) == window_days(second):
                identical_windows += 1
        event(f"mode: {mode}")
        event(f"overlap past 25 percent: {past_threshold}")

        assert distinct(first, second) is True, (
            "differing datasets did not make a pair distinct: "
            f"mode={mode}, datasets={first['dataset']!r} vs {second['dataset']!r}, "
            f"overlap={overlap!r} of a shorter window of {shorter!r} days, "
            f"a=[{first.get('start_date')!r} … {first.get('end_date')!r}], "
            f"b=[{second.get('start_date')!r} … {second.get('end_date')!r}]"
        )

        # ── The witness: the windows alone would have refused this pair ──
        if past_threshold:
            over_threshold += 1
            twin = dict(second, dataset=first["dataset"])
            if distinct(first, twin) is False:
                witnessed += 1
            else:
                raise AssertionError(
                    "the same pair with equal datasets was still judged distinct, so an "
                    f"overlap of {overlap} days against a shorter window of {shorter} days "
                    "was not actually over Requirement 3.5(b)'s threshold and this example "
                    f"says nothing about the dataset short-circuit (mode={mode})"
                )

    check()

    assert examples >= 100, f"the property body ran on only {examples} pairs"

    # NON-VACUITY. The claim is "regardless of window overlap", and a run of disjoint windows
    # would confirm nothing: those pairs are distinct under branch (b) as well, so an
    # implementation with no dataset short-circuit at all would pass. What has to be reached is
    # a *high* overlap, and the identical window in particular.
    assert identical_windows >= 5, (
        f"only {identical_windows} of {examples} pairs had fully identical windows, the one "
        "geometry that leaves the dataset short-circuit as the sole reason the pair can be "
        "distinct"
    )
    assert over_threshold * 4 >= examples, (
        f"vacuous run: only {over_threshold} of {examples} pairs overlapped by more than 25 "
        "percent of the shorter window, so P-36 was mostly established on pairs the overlap "
        "rule would have admitted anyway"
    )
    assert witnessed == over_threshold, (
        f"only {witnessed} of {over_threshold} over-threshold pairs were confirmed to be "
        "rejected once their datasets were made equal"
    )
    # Requirement 3.5(a) needs no window at all, so the rows that never recorded one must be
    # in the pool - not asserted as a share, only as present, since the ``defective`` mode is
    # one of six and the other five build real windows on purpose.
    assert unreadable >= 5, (
        "no drawn pair had an unreadable window, so the claim that a differing dataset "
        "certifies distinctness without the windows being readable went untested"
    )

# ---------------------------------------------------------------------------
# P-37
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 37 (invariant, set admission): For
# all generated Backtest_Evidence sets, the set is admitted if and only if it contains at least
# three conditions that are pairwise distinct, share one `version_id`, have pairwise different
# `dataset_checksum` values, and each satisfy the completeness, duration, trade-count and
# bar-count criteria of Requirement 3.
#
# P-33 … P-36 pin down one predicate; this is the first property about the decision the product
# actually makes. `eligibility_gate` admits a Submission on `all_passed(validate(...))` and
# nothing else, so a criterion that `validate` forgets to emit is a criterion that does not
# exist: an owner could publish three runs of one lucky quarter, or two versions of a strategy
# passed off as one, and every downstream requirement - the price range of Requirement 8, the
# metrics on the Listing of Requirement 6 - would be computed from evidence Requirement 3 was
# written to refuse. The `iff` is what makes that unmissable. The forward direction is the one
# that keeps bad evidence out; the reverse direction is the one that keeps an owner with three
# genuinely separate, complete, long-enough runs from being refused with nothing to fix.
#
# THE ORACLE. `_p37_admissible` restates Requirement 3's criteria 1 … 8 from the requirement
# text and shares no line of code with `validate`:
#
# * criterion 1 - three to ten conditions, each naming a *different* `strategy_backtests` row
#   (equal `id` references are one run listed twice, not two conditions), each owned by the
#   strategy owner and belonging to the submitted strategy;
# * criterion 2 - `status` 'completed', a recorded `completed_at`, a null or empty
#   `error_message`;
# * criterion 3 - one and the same non-null `version_id` across the whole set;
# * criterion 4 - all eight parameters recorded, and readable as the kind of value the column
#   holds (a present-but-unreadable capital or date has not recorded the parameter in any usable
#   sense - the reading `_parameters_recorded` applies, restated here from Requirement 3.11's
#   ban on substituting a figure the evidence does not contain);
# * criteria 5 and 6 - every unordered pair distinct, and pairwise different
#   `dataset_checksum` values, none absent;
# * criterion 7 - at least 90 inclusive calendar days and at least 20 trades per condition;
# * criterion 8 - a recorded bar count of at least 50 per condition.
#
# The numbers are written as the literals Requirement 3 states rather than imported from
# `THRESHOLDS`, and the test asserts once that the module's thresholds still are those numbers -
# so a change to a threshold surfaces here as a failure instead of silently redefining the
# property. The distinctness half reuses P-35's day-set overlap oracle for the same reason it
# exists there: it counts shared days one by one instead of repeating `intersection_days`'s
# endpoint arithmetic.
#
# WHY THE ORACLE JUDGES AND THE GENERATOR ONLY SHAPES. `evidence_sets(admissible=False)` is a
# statement of intent, not of fact: its `defective_condition` defect can null `sharpe_ratio` -
# a column no criterion of Requirement 3 reads - and produce a set that must be *admitted*
# despite the label. So the expected verdict is computed, never taken from the generator, and
# such a set is a useful example rather than a false failure.
#
# WHY THE INPUTS ARE STEERED PER CRITERION. An `iff` over ten criteria is only as good as the
# inputs that make each side of it fail alone. Independently drawn defects concentrate on
# whichever criterion is easiest to break, so `admission_candidates` draws a *target criterion*
# first and then a defect known to break it, which puts every one of the ten codes in the pool
# in comparable numbers - and the run asserts each was seen failing at least once. Without that,
# an implementation missing `EV_BARS` entirely could pass on a run where no drawn set happened
# to have a short bar count.


#: Requirement 3.1's bounds, 3.7's two floors and 3.8's minimum, written as the requirement
#: writes them. :func:`test_p37_evidence_set_admission_iff_every_criterion` asserts the module
#: still agrees with them, so the oracle cannot drift into re-reading whatever the code says.
_P37_MIN_CONDITIONS = 3
_P37_MAX_CONDITIONS = 10
_P37_MIN_WINDOW_DAYS = 90
_P37_MIN_TRADES = 20
_P37_MIN_BARS = 50
_P37_MAX_OVERLAP_NUMERATOR = 4

#: Requirement 3.4's eight parameters, split by the kind of value each records so that
#: "recorded" can be read as strictly here as the requirement reads it.
_P37_TEXT_PARAMETERS = ("dataset", "dataset_checksum", "dag_hash")
_P37_DATE_PARAMETERS = ("start_date", "end_date")
_P37_NUMBER_PARAMETERS = ("initial_capital", "commission", "slippage")


def _p37_text(value: Any) -> Optional[str]:
    """``value`` as a recorded non-blank string, or ``None``.

    A blank string counts as unrecorded, which is what Requirement 3.2 asks for
    ``error_message`` ("null or empty") and what stops an empty ``dataset_checksum`` from
    passing as a fingerprint.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _p37_calendar_date(value: Any) -> Optional[date]:
    """``value`` as a calendar date, or ``None`` when absent or unreadable.

    The generator records ``datetime.date`` objects, so the readable case is an ``isinstance``
    check; anything else is unreadable rather than guessed at. ``bool`` is refused explicitly
    because it is not a date however Python classifies it.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date):
        return value
    return None


def _p37_is_number(value: Any) -> bool:
    """Whether ``value`` is recorded *and* readable as a finite number.

    Requirement 3.4 asks for the parameter to be recorded and Requirement 3.11 forbids
    substituting a figure the evidence does not contain, so a value that cannot be read as a
    number has not recorded one. ``bool`` is refused for the same reason as above.
    """
    if value is None or isinstance(value, bool):
        return False
    candidate = value.strip() if isinstance(value, str) else value
    if candidate == "":
        return False
    try:
        numeric = float(candidate)
    except (TypeError, ValueError):
        return False
    # NaN is the one value not equal to itself; the infinities are named rather than compared
    # against a magic bound.
    return numeric == numeric and numeric not in (float("inf"), float("-inf"))


def _p37_count(value: Any) -> Optional[int]:
    """``value`` as a whole recorded count, or ``None`` when absent or unreadable.

    Floored, so a fractional count can only fail its threshold more easily and never pass one
    a whole number would not have passed.
    """
    if not _p37_is_number(value):
        return None
    return int(float(value) // 1)


def _p37_same_id(left: Any, right: Any) -> bool:
    """Whether two identifiers denote the same row owner or strategy.

    Case-folded text, because a UUID may arrive as an object from one driver and as its hex
    string from another. An absent identifier on either side never matches: an unreadable owner
    authorises nothing.
    """
    left_text = _p37_text(left)
    right_text = _p37_text(right)
    if left_text is None or right_text is None:
        return False
    return left_text.casefold() == right_text.casefold()


def _p37_window_days(condition: Dict[str, Any]) -> Optional[int]:
    """Inclusive calendar days of ``condition``'s window, or ``None`` when unreadable.

    Requirement 3.7 counts the window "between ``start_date`` and ``end_date`` inclusive", so a
    single-day window is one day. A window that was never recorded has no length - ``0`` would
    be a fabricated one.
    """
    start = _p37_calendar_date(condition.get("start_date"))
    end = _p37_calendar_date(condition.get("end_date"))
    if start is None or end is None:
        return None
    return (end - start).days + 1


def _p37_distinct(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Requirement 3.5's distinctness, restated: (a) datasets differ, or (b) overlap within 25%.

    The overlap comes from :func:`_oracle_overlap` - P-35's day-set count - rather than from any
    endpoint arithmetic, and the comparison is the requirement's own
    ``intersection_days x 4 <= shorter_window_days``, so nothing is divided and nothing is
    rounded. A pair whose windows cannot be read is *not* distinct: Requirement 3.6 demands
    every pair be distinct, and a pair that cannot be shown to be has not met the demand.
    """
    left = _p37_text(a.get("dataset"))
    right = _p37_text(b.get("dataset"))
    if left is not None and right is not None and left != right:
        return True

    a_days = _p37_window_days(a)
    b_days = _p37_window_days(b)
    if a_days is None or b_days is None or a_days <= 0 or b_days <= 0:
        return False
    overlap = _oracle_overlap(a, b)
    return overlap * _P37_MAX_OVERLAP_NUMERATOR <= min(a_days, b_days)


def _p37_admissible(
    rows: list,
    owner_id: Any,
    strategy_id: Any,
) -> Tuple[bool, Tuple[str, ...]]:
    """``(admitted, failing criterion codes)`` for one evidence set, from Requirement 3 alone.

    Every criterion is evaluated - nothing short-circuits - so the returned codes name every
    reason the set was refused and a counterexample says which criterion the subject and the
    oracle disagreed about.
    """
    from backend_app.backend.marketplace.evidence_validator import (
        EV_BARS,
        EV_CHECKSUMS,
        EV_COMPLETED,
        EV_COUNT,
        EV_DISTINCT,
        EV_DURATION,
        EV_ONE_VERSION,
        EV_OWNERSHIP,
        EV_PARAMS,
        EV_TRADES,
    )

    failures = set()

    # ── Requirement 3.1: how many, and all different runs ──────────────
    references = [_p37_text(row.get("id")) for row in rows]
    recorded = [reference for reference in references if reference is not None]
    if not (_P37_MIN_CONDITIONS <= len(rows) <= _P37_MAX_CONDITIONS):
        failures.add(EV_COUNT)
    if len(set(recorded)) != len(recorded):
        failures.add(EV_COUNT)

    for row in rows:
        # Requirement 3.1: the owner's own run of the submitted strategy.
        if not (
            _p37_same_id(row.get("user_id"), owner_id)
            and _p37_same_id(row.get("strategy_id"), strategy_id)
        ):
            failures.add(EV_OWNERSHIP)

        # Requirement 3.2: completed, with a completion instant, and no error text.
        status = _p37_text(row.get("status"))
        if (
            status is None
            or status.lower() != "completed"
            or _p37_text(row.get("completed_at")) is None
            or _p37_text(row.get("error_message")) is not None
        ):
            failures.add(EV_COMPLETED)

        # Requirement 3.4: all eight parameters recorded and readable.
        recorded_parameters = (
            all(_p37_text(row.get(name)) is not None for name in _P37_TEXT_PARAMETERS)
            and all(
                _p37_calendar_date(row.get(name)) is not None
                for name in _P37_DATE_PARAMETERS
            )
            and all(_p37_is_number(row.get(name)) for name in _P37_NUMBER_PARAMETERS)
        )
        if not recorded_parameters:
            failures.add(EV_PARAMS)

        # Requirement 3.7: a long enough window, and enough trades.
        days = _p37_window_days(row)
        if days is None or days < _P37_MIN_WINDOW_DAYS:
            failures.add(EV_DURATION)
        trades = _p37_count(row.get("total_trades"))
        if trades is None or trades < _P37_MIN_TRADES:
            failures.add(EV_TRADES)

        # Requirement 3.8: a *recorded* bar count, at or above the engine's minimum.
        bars = _p37_count(row.get("executed_bar_count"))
        if bars is None or bars < _P37_MIN_BARS:
            failures.add(EV_BARS)

    # ── Requirement 3.3: one immutable Strategy_Version across the set ─
    versions = {_p37_text(row.get("version_id")) for row in rows}
    if len(versions) != 1 or None in versions:
        failures.add(EV_ONE_VERSION)

    # ── Requirement 3.6: pairwise different data fingerprints ──────────
    checksums = [_p37_text(row.get("dataset_checksum")) for row in rows]
    if None in checksums or len(set(checksums)) != len(checksums):
        failures.add(EV_CHECKSUMS)

    # ── Requirements 3.5 and 3.6: every pair genuinely distinct ────────
    for index, first in enumerate(rows):
        for second in rows[index + 1 :]:
            if not _p37_distinct(first, second):
                failures.add(EV_DISTINCT)

    return not failures, tuple(sorted(failures))


@st.composite
def admission_candidates(draw) -> Tuple[Any, Any, list, str, Optional[str]]:
    """An evidence set with its owner and strategy, steered so each criterion fails alone.

    ``owner_id``, ``strategy_id`` and ``version_id`` are drawn here and handed to
    ``evidence_sets`` so the ownership criterion has something to be evaluated *against* - a set
    validated against a freshly drawn owner would fail ``EV_OWNERSHIP`` on every row and the
    other nine criteria would never decide anything.

    Four modes, each about a quarter of the draws:

    * ``admissible`` - ``evidence_sets(admissible=True)``: 3 … 10 complete rows, one version, one
      owner, different fingerprints, windows laid end-to-end with a gap. The reverse direction of
      the ``iff`` rests entirely on these.
    * ``set_defect`` - one named defect from ``EVIDENCE_SET_DEFECTS``, drawn one at a time rather
      than in combinations, so the set-level criteria (count, one version, fingerprints, pairwise
      distinctness) each get their turn as the only thing wrong.
    * ``condition_defect`` - an admissible set with one row swapped for a row carrying a defect
      chosen *for the criterion it breaks*: the target criterion is drawn first, uniformly over
      the seven per-condition criteria, and then a defect known to break it. The replacement
      keeps the original row's dataset, window, fingerprint, owner and version, so the set's
      geometry is untouched and the targeted criterion is the only casualty.
    * ``free`` - ``evidence_sets(admissible=None)``, which draws its own combinations of one or
      two defects, so pairs and triples of failures are in the input space too.

    The mode and the targeted criterion travel with the rows so a counterexample names them.
    """
    from backend_app.backend.marketplace.evidence_validator import (
        EV_BARS,
        EV_COMPLETED,
        EV_DURATION,
        EV_ONE_VERSION,
        EV_OWNERSHIP,
        EV_PARAMS,
        EV_TRADES,
    )
    from tests.strategies.marketplace_generators import (
        EVIDENCE_SET_DEFECTS,
        evidence_sets,
        identifiers,
    )

    #: One condition-level defect per per-condition criterion of Requirement 3, so drawing a
    #: criterion and then a defect reaches every criterion in comparable numbers.
    defects_for_criterion = {
        EV_OWNERSHIP: ("foreign_owner",),
        EV_COMPLETED: ("not_completed", "no_completed_at", "error_message"),
        EV_PARAMS: ("null_parameter",),
        EV_DURATION: ("short_window",),
        EV_TRADES: ("too_few_trades",),
        EV_BARS: ("null_bar_count", "too_few_bars"),
        EV_ONE_VERSION: ("null_version_id",),
    }

    owner = draw(identifiers())
    strategy = draw(identifiers())
    version = draw(identifiers())
    mode = draw(
        st.sampled_from(("admissible", "set_defect", "condition_defect", "free"))
    )
    targeted: Optional[str] = None

    if mode == "admissible":
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                admissible=True,
            )
        )
    elif mode == "set_defect":
        targeted = draw(st.sampled_from(EVIDENCE_SET_DEFECTS))
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                defects=[targeted],
            )
        )
    elif mode == "condition_defect":
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                admissible=True,
            )
        )
        targeted = draw(st.sampled_from(sorted(defects_for_criterion)))
        defect = draw(st.sampled_from(defects_for_criterion[targeted]))
        index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        template = rows[index]
        rows[index] = draw(
            backtest_conditions(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                dataset=template["dataset"],
                start_date=template["start_date"],
                window_days=_p37_window_days(template),
                dataset_checksum=template["dataset_checksum"],
                defects=[defect],
            )
        )
    else:
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                admissible=None,
            )
        )

    return owner, strategy, rows, mode, targeted


def test_p37_evidence_set_admission_iff_every_criterion() -> None:
    """A set is admitted exactly when every criterion of Requirement 3 holds for it.

    **Validates: Requirements 3.1, 3.3, 3.4, 3.6, 3.7, 3.8**
    """
    from backend_app.backend.marketplace.evidence_validator import (
        EV_CODES,
        THRESHOLDS,
        all_passed,
        failed_outcomes,
        validate,
    )

    # The oracle restates Requirement 3's numbers rather than importing them. If the module's
    # thresholds ever move, this property is about a different rule and must be rewritten
    # rather than quietly re-interpreted.
    assert THRESHOLDS["MIN_CONDITIONS"] == _P37_MIN_CONDITIONS
    assert THRESHOLDS["MAX_CONDITIONS"] == _P37_MAX_CONDITIONS
    assert THRESHOLDS["MIN_WINDOW_DAYS"] == _P37_MIN_WINDOW_DAYS
    assert THRESHOLDS["MIN_TRADES"] == _P37_MIN_TRADES
    assert THRESHOLDS["MIN_BARS"] == _P37_MIN_BARS
    assert THRESHOLDS["MAX_OVERLAP_NUMERATOR"] == _P37_MAX_OVERLAP_NUMERATOR

    examples = 0
    admitted = 0
    rejected = 0
    observed_failures = set()

    # Ten criteria, four modes and seven targeted per-condition criteria need more than the
    # shared minimum to give each of them a fair share of a run; the rest of the configuration
    # is the shared one.
    @settings(property_test, max_examples=250)
    @given(drawn=admission_candidates())
    def check(drawn: Tuple[Any, Any, list, str, Optional[str]]) -> None:
        nonlocal examples, admitted, rejected
        examples += 1

        owner, strategy, rows, mode, targeted = drawn

        expected, reasons = _p37_admissible(rows, owner, strategy)
        outcomes = validate(rows, owner, strategy)
        actual = all_passed(outcomes)

        if actual:
            admitted += 1
        else:
            rejected += 1
        observed_failures.update(reasons)
        event(f"mode: {mode}")
        event(f"admitted: {actual}")

        emitted = tuple(outcome.code for outcome in failed_outcomes(outcomes))
        assert actual == expected, (
            "validate disagrees with Requirement 3's criteria on admission: "
            f"all_passed={actual} but the criteria say admitted={expected} "
            f"(mode={mode}, targeted={targeted!r}, {len(rows)} conditions). "
            f"Criteria unmet per the requirement: {reasons or '(none)'}; "
            f"criteria validate reported failed: {emitted or '(none)'}"
        )

    check()

    assert examples >= 200, f"the property body ran on only {examples} evidence sets"

    # NON-VACUITY. An `iff` is two claims, and each needs its own inputs. A run of nothing but
    # rejected sets would leave "every criterion holds implies admitted" untested, and an
    # implementation that refused every Submission would pass; a run of nothing but admitted
    # sets would leave the direction that keeps bad evidence out untested, and an
    # implementation that admitted everything would pass. Both floors were set by mutating
    # `validate` in each direction and checking that the corresponding counter is what fails.
    assert admitted >= 25, (
        f"only {admitted} of {examples} drawn sets were admitted, so the 'every criterion "
        "holds implies admitted' direction of P-37 is barely tested"
    )
    assert rejected >= 25, (
        f"only {rejected} of {examples} drawn sets were rejected, so the 'admitted implies "
        "every criterion holds' direction of P-37 is barely tested"
    )

    # Each criterion has to have been the thing that failed at least once, or a criterion
    # `validate` never evaluates at all would go unnoticed: the ten codes are exactly the ten
    # criteria of Requirement 3 the validator is answerable for.
    missing = tuple(sorted(set(EV_CODES) - observed_failures))
    assert not missing, (
        f"over {examples} sets these criteria were never once unmet: {missing}, so P-37 says "
        "nothing about whether validate enforces them"
    )
