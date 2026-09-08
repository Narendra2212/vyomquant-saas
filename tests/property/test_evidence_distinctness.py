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

    @settings(property_test, max_examples=250)
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


#: P-36's six window geometries. Named rather than left to a blind date offset so each can be
#: *forced*, which is what turns the non-vacuity floors below from likely into guaranteed.
_P36_MODES: Tuple[str, ...] = (
    "identical",
    "nested",
    "partial",
    "disjoint",
    "defective",
    "unreadable",
)

#: Examples per forced mode in the sweep that follows the unforced run.
#:
#: ``identical`` and ``nested`` are the two geometries whose overlap is 100 percent of the
#: shorter window *by construction*, so they are the only ones that can guarantee the
#: ``over_threshold * 4 >= examples`` floor. Each of their examples adds 1 to
#: ``over_threshold`` and 1 to ``examples``, i.e. 4 - 1 = +3 to that inequality's margin;
#: every other example (the 250 unforced ones and the four presence-only sweeps) adds 1 to
#: ``examples`` and, in the worst case, 0 to ``over_threshold``, i.e. -1 each. So the floor
#: holds regardless of the seed as long as
#:
#:     3 * (identical + nested) >= 250 + partial + disjoint + defective + unreadable
#:     3 * (60 + 60) = 360      >= 250 + 12 + 12 + 12 + 12 = 298
#:
#: which leaves 62 examples of slack for replayed examples out of ``.hypothesis/``. The four
#: presence-only modes are sized like P-37's sweep: they exist to reach a case, not to explore
#: it, and the unforced run explores all six.
_P36_SWEEP_EXAMPLES: Dict[str, int] = {
    "identical": 60,
    "nested": 60,
    "partial": 12,
    "disjoint": 12,
    "defective": 12,
    "unreadable": 12,
}

#: What each forced mode promises about the pair it produces, checked on every swept example so
#: a generator that stops keeping its half of the bargain fails loudly instead of leaving the
#: sweep certifying nothing. ``defective`` promises nothing: it is the mode whose rows may be
#: well formed or defective, which is the point of it.
_P36_GUARANTEES: Dict[str, str] = {
    "identical": "identical readable windows, so overlap is 100 percent of both",
    "nested": "readable windows with the shorter wholly inside the longer, so overlap is 100 "
    "percent of the shorter",
    "partial": "two readable windows",
    "disjoint": "two readable windows sharing no day",
    "unreadable": "at least one window with no readable start or end date",
}


@st.composite
def differing_dataset_pairs(
    draw,
    *,
    mode: Optional[str] = None,
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

    ``mode`` FORCES THE GEOMETRY
    ---------------------------
    Left at ``None`` the mode is drawn, which is how the unforced run explores all six. Passed
    explicitly it is *forced*, which is what the sweep at the end of the property uses: the
    non-vacuity floors demand that an identical window, an over-threshold overlap and an
    unreadable window each turn up several times in a run, and a uniform draw over six modes
    only makes that **likely**. Forcing the mode makes the coverage those floors demand a
    property of the code rather than of the seed. Everything *inside* a mode - spans, start
    dates, insets, which endpoint is nulled, whether the defective rows are well formed - stays
    drawn, so forcing narrows the geometry and not the input space beneath it.
    """
    left_dataset, right_dataset = draw(
        st.lists(st.sampled_from(DATASETS), min_size=2, max_size=2, unique=True)
    )
    if mode is None:
        mode = draw(st.sampled_from(_P36_MODES))
    else:
        assert mode in _P36_MODES, f"unknown window geometry {mode!r}"

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

    def body(
        drawn: Tuple[Dict[str, Any], Dict[str, Any], str],
        forced: Optional[str] = None,
    ) -> None:
        """The property itself, over one drawn pair.

        ``forced`` is the sweep's own check: when the geometry was demanded rather than drawn,
        the pair is verified to actually *have* that geometry before the counters are trusted.
        """
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
            identical = False
        else:
            overlap, shorter = share
            past_threshold = overlap * 100 > 25 * shorter
            identical = overlap == shorter and window_days(first) == window_days(second)
            if identical:
                identical_windows += 1
        event(f"mode: {mode}")
        event(f"overlap past 25 percent: {past_threshold}")

        # ── The sweep's half of the bargain ──
        # The generator was *asked* for this geometry. If it produced something else, the
        # counters it was supposed to guarantee would be silently short and the floors below
        # would be back to depending on the seed - the exact failure this treatment removes.
        if forced is not None:
            assert mode == forced, (
                f"differing_dataset_pairs(mode={forced!r}) produced a {mode!r} pair"
            )
            geometry = (
                f"overlap={overlap!r} of a shorter window of {shorter!r} days, "
                f"a=[{first.get('start_date')!r} … {first.get('end_date')!r}], "
                f"b=[{second.get('start_date')!r} … {second.get('end_date')!r}]"
            )
            promise = _P36_GUARANTEES.get(forced)
            if forced == "identical":
                assert identical and past_threshold, (
                    f"mode={forced!r} promises {promise} but produced {geometry}, so the "
                    f"sweep that guarantees the identical-window and over-threshold floors "
                    f"certifies neither"
                )
            elif forced == "nested":
                assert share is not None and overlap == shorter and past_threshold, (
                    f"mode={forced!r} promises {promise} but produced {geometry}, so the "
                    f"sweep that guarantees the over-threshold floor certifies nothing"
                )
            elif forced == "partial":
                assert share is not None, (
                    f"mode={forced!r} promises {promise} but produced {geometry}"
                )
            elif forced == "disjoint":
                assert share is not None and overlap == 0, (
                    f"mode={forced!r} promises {promise} but produced {geometry}"
                )
            elif forced == "unreadable":
                assert share is None, (
                    f"mode={forced!r} promises {promise} but produced {geometry}, so the "
                    f"sweep that guarantees the unreadable-window floor certifies nothing"
                )

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

    @settings(property_test, max_examples=250)
    @given(drawn=differing_dataset_pairs())
    def check(drawn: Tuple[Dict[str, Any], Dict[str, Any], str]) -> None:
        body(drawn)

    check()

    # THE SWEEP. The unforced run above draws its mode uniformly from six, so it only makes the
    # three non-vacuity floors below *likely* - which is why one of them failed once on an
    # unlucky seed and then passed on every rerun. Each geometry additionally gets its own run
    # against a generator forced to produce it, and ``body``'s ``forced`` branch checks the
    # generator kept its half of the bargain. The counters accumulate across both runs, so the
    # coverage the floors demand becomes a property of the code and not of the seed:
    #
    #   identical  ->  identical_windows >= 60 and over_threshold >= 60
    #   nested     ->  over_threshold    >= 60
    #   unreadable ->  unreadable        >= 12
    #
    # See :data:`_P36_SWEEP_EXAMPLES` for the arithmetic behind the ratio floor.
    def sweep_for(geometry: str):
        @settings(property_test, max_examples=_P36_SWEEP_EXAMPLES[geometry])
        @given(drawn=differing_dataset_pairs(mode=geometry))
        def sweep(drawn: Tuple[Dict[str, Any], Dict[str, Any], str]) -> None:
            body(drawn, forced=geometry)

        return sweep

    for geometry in _P36_MODES:
        sweep_for(geometry)()

    assert examples >= 100, f"the property body ran on only {examples} pairs"

    # NON-VACUITY. The claim is "regardless of window overlap", and a run of disjoint windows
    # would confirm nothing: those pairs are distinct under branch (b) as well, so an
    # implementation with no dataset short-circuit at all would pass. What has to be reached is
    # a *high* overlap, and the identical window in particular.
    #
    # All three floors below are unchanged. What changed is that the sweep above *guarantees*
    # them instead of leaving them to a lucky draw: the ``identical`` sweep alone contributes
    # 60 identical windows and 60 over-threshold pairs, and the ``unreadable`` sweep 12
    # unreadable windows, all by construction and all checked by ``body``'s ``forced`` branch.
    # A failure here now means the *code* stopped producing the case, not that the seed did.
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
#: Examples per criterion in P-37's per-criterion sweep. Ten criteria x this many is what makes
#: "every criterion was unmet at least once" a guarantee instead of a probability; it is small
#: because the sweep is about reaching each criterion, not about exploring its input space (the
#: 350-example unforced run does that).
_P37_SWEEP_EXAMPLES = 12

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
def admission_candidates(
    draw,
    criterion: Optional[str] = None,
) -> Tuple[Any, Any, list, str, Optional[str]]:
    """An evidence set with its owner and strategy, steered so each criterion fails alone.

    ``owner_id``, ``strategy_id`` and ``version_id`` are drawn here and handed to
    ``evidence_sets`` so the ownership criterion has something to be evaluated *against* - a set
    validated against a freshly drawn owner would fail ``EV_OWNERSHIP`` on every row and the
    other nine criteria would never decide anything.

    THE CHOICE IS "WHICH CRITERION DO I VIOLATE", NOT "WHICH DEFECT DO I INJECT"
    ---------------------------------------------------------------------------
    An earlier version drew a *defect name* uniformly from ``EVIDENCE_SET_DEFECTS`` and hoped the
    ten criteria all came up over a run. They did not: ``EV_CHECKSUMS`` is reachable through
    exactly one set-level defect (``duplicate_checksum``), so a run that never drew that one name
    left P-37 saying nothing about the fingerprint criterion and the non-vacuity assertion at the
    end of the property failed intermittently. Every defective mode now draws the **criterion**
    first and then a defect that breaks *that* criterion, and :func:`criterion` forces the choice
    outright so the property can sweep all ten deterministically.

    Four modes, each about a quarter of the draws when ``criterion`` is ``None``:

    * ``admissible`` - ``evidence_sets(admissible=True)``: 3 … 10 complete rows, one version, one
      owner, different fingerprints, windows laid end-to-end with a gap. The reverse direction of
      the ``iff`` rests entirely on these.
    * ``set_criterion`` - one of the four *set-level* criteria (count, one version, fingerprints,
      pairwise distinctness) drawn first, then a set-level defect known to break it, injected on
      its own rather than in combinations - so each gets its turn as the only thing wrong.
    * ``condition_criterion`` - an admissible set with one row swapped for a row that breaks one
      of the seven *per-condition* criteria, again criterion-first. The replacement keeps the
      original row's dataset, window, fingerprint and version, so the set's geometry is untouched
      and the targeted criterion is the only casualty.
    * ``free`` - ``evidence_sets(admissible=None)``, which draws its own combinations of one or
      two defects, so pairs and triples of failures are in the input space too.

    WHEN ``criterion`` IS FORCED, ONLY DEFECTS THAT BREAK IT *BY CONSTRUCTION* ARE ELIGIBLE
    --------------------------------------------------------------------------------------
    ``multiple_versions`` and ``foreign_owner`` re-draw an identifier and rely on it differing
    from the one the set already carries. That is true with overwhelming probability and false in
    principle - two draws of the all-zero UUID would leave the set admissible - so they stay in
    the free and unforced draws but are excluded from a forced one, where ``null_version`` and an
    explicitly-filtered foreign owner give the same criterion with no probability attached.

    The mode and the targeted criterion travel with the rows so a counterexample names them.
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
    from tests.strategies.marketplace_generators import evidence_sets, identifiers

    #: Condition-level defects per per-condition criterion of Requirement 3. Every entry breaks
    #: its criterion by construction except ``foreign_owner`` - see the docstring.
    defects_for_criterion = {
        EV_OWNERSHIP: ("foreign_owner",),
        EV_COMPLETED: ("not_completed", "no_completed_at", "error_message"),
        EV_PARAMS: ("null_parameter",),
        EV_DURATION: ("short_window",),
        EV_TRADES: ("too_few_trades",),
        EV_BARS: ("null_bar_count", "too_few_bars"),
        EV_ONE_VERSION: ("null_version_id",),
    }

    #: Set-level defects per set-level criterion. ``duplicate_checksum`` is the only route to
    #: ``EV_CHECKSUMS``, which is exactly why the criterion has to be drawn rather than the name.
    set_defects_for_criterion = {
        EV_COUNT: ("too_few_conditions", "too_many_conditions"),
        EV_ONE_VERSION: ("null_version", "multiple_versions"),
        EV_CHECKSUMS: ("duplicate_checksum",),
        EV_DISTINCT: ("overlapping_windows",),
    }

    #: The defects excluded from a *forced* criterion: they break it with probability 1 - 2**-122
    #: rather than by construction, and a forced sweep must not rest on a probability.
    probabilistic = ("multiple_versions", "foreign_owner")

    owner = draw(identifiers())
    strategy = draw(identifiers())
    version = draw(identifiers())
    targeted: Optional[str] = criterion

    if criterion is None:
        mode = draw(
            st.sampled_from(
                ("admissible", "set_criterion", "condition_criterion", "free")
            )
        )
    elif criterion in set_defects_for_criterion and criterion in defects_for_criterion:
        # EV_ONE_VERSION is reachable from either level; both are exercised.
        mode = draw(st.sampled_from(("set_criterion", "condition_criterion")))
    elif criterion in set_defects_for_criterion:
        mode = "set_criterion"
    elif criterion in defects_for_criterion:
        mode = "condition_criterion"
    else:  # pragma: no cover - a typo in a caller, not a drawable state
        raise AssertionError(f"no defect is known to break {criterion!r}")

    def eligible(names: Tuple[str, ...]) -> Tuple[str, ...]:
        """The defect names usable for this draw, in the generator's declared order."""
        if criterion is None:
            return names
        guaranteed = tuple(name for name in names if name not in probabilistic)
        assert guaranteed, f"no by-construction defect breaks {criterion!r}"
        return guaranteed

    if mode == "admissible":
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                admissible=True,
            )
        )
    elif mode == "set_criterion":
        if targeted is None:
            targeted = draw(st.sampled_from(sorted(set_defects_for_criterion)))
        defect = draw(st.sampled_from(eligible(set_defects_for_criterion[targeted])))
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                defects=[defect],
            )
        )
    elif mode == "condition_criterion":
        rows = draw(
            evidence_sets(
                owner_id=owner,
                strategy_id=strategy,
                version_id=version,
                admissible=True,
            )
        )
        if targeted is None:
            targeted = draw(st.sampled_from(sorted(defects_for_criterion)))
        index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        template = rows[index]
        row_owner = owner
        defects: Optional[list]
        if targeted == EV_OWNERSHIP and criterion is not None:
            # A drawn-and-filtered foreign owner, so the row's user_id *cannot* equal the
            # owner the set is validated against. `foreign_owner` re-draws without filtering,
            # which is why it is not eligible for a forced draw - and why the substitution is
            # decided HERE, before `eligible` is consulted at all. Asking `eligible` for a
            # by-construction defect that breaks EV_OWNERSHIP would trip its own assertion:
            # `foreign_owner` is the only defect that reaches that criterion and it is the
            # probabilistic one.
            row_owner = draw(
                identifiers().filter(
                    lambda candidate: candidate.casefold() != str(owner).casefold()
                )
            )
            defects = None
        else:
            defects = [draw(st.sampled_from(eligible(defects_for_criterion[targeted])))]
        rows[index] = draw(
            backtest_conditions(
                owner_id=row_owner,
                strategy_id=strategy,
                version_id=version,
                dataset=template["dataset"],
                start_date=template["start_date"],
                window_days=_p37_window_days(template),
                dataset_checksum=template["dataset_checksum"],
                satisfying=True if defects is None else None,
                defects=defects,
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

    def body(
        drawn: Tuple[Any, Any, list, str, Optional[str]],
        expect_unmet: Optional[str] = None,
    ) -> None:
        """The property itself, over one drawn set. ``expect_unmet`` is the sweep's own check."""
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

        # The generator was *asked* for a set that leaves this criterion unmet. If it produced
        # one that does not, the sweep below would silently prove nothing about that criterion -
        # which is the exact failure mode the non-vacuity assertion caught intermittently.
        if expect_unmet is not None:
            assert expect_unmet in reasons, (
                f"admission_candidates(criterion={expect_unmet!r}) produced a set that meets "
                f"that criterion (mode={mode}, {len(rows)} conditions, unmet="
                f"{reasons or '(none)'}), so the sweep over it certifies nothing"
            )

    # Ten criteria, four modes and seven targeted per-condition criteria need more than the
    # shared minimum to give each of them a fair share of a run; the rest of the configuration
    # is the shared one.
    @settings(property_test, max_examples=350)
    @given(drawn=admission_candidates())
    def check(drawn: Tuple[Any, Any, list, str, Optional[str]]) -> None:
        body(drawn)

    check()

    # THE SWEEP. The unforced draw above spreads over the ten criteria but *guarantees* none of
    # them: `EV_CHECKSUMS` has exactly one route into it, and runs that never took that route
    # left P-37 silent about the fingerprint criterion (the non-vacuity assertion below is what
    # reported it). So every criterion additionally gets its own run against a generator that is
    # forced to violate it, and `expect_unmet` checks the generator kept its half of the bargain.
    # This makes the coverage the non-vacuity assertion demands a property of the code rather
    # than of the seed.
    def sweep_for(code: str):
        @settings(property_test, max_examples=_P37_SWEEP_EXAMPLES)
        @given(drawn=admission_candidates(criterion=code))
        def sweep(drawn: Tuple[Any, Any, list, str, Optional[str]]) -> None:
            body(drawn, expect_unmet=code)

        return sweep

    for code in sorted(EV_CODES):
        sweep_for(code)()

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

# ---------------------------------------------------------------------------
# P-38
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 38 (idempotence, revalidation):
# For all admitted and rejected Backtest_Evidence sets and all repetition counts n >= 1,
# revalidating the same rows n times produces the same outcome and the same per-criterion
# outcomes.
#
# Requirement 3.12 says `validate` reads, caches and times nothing, so the same persisted rows
# produce the same list every time. That is what lets the gate re-run the check on a Submission
# without the verdict drifting: an owner who was rejected sees the same failing criteria on the
# next look, and one who was admitted is not quietly re-refused because a set iterated in a
# different order or a distinctness pair was compared the other way round. If `validate` ever
# grew a clock, a random tie-break or a mutable accumulator shared across calls, two runs over
# one input could disagree - and every downstream step that trusts a stored verdict (the Listing
# metrics of Requirement 6, the audit record of Requirement 2.11) would be built on a figure
# that no longer reproduces.
#
# THE CLAIM IS EQUALITY OF THE WHOLE LIST, not just of `all_passed`. `CriterionOutcome` is a
# frozen dataclass, so `==` compares code, passed flag, message and subjects field by field, and
# list equality also pins the *order* - the fixed sequence `validate` documents (the count, then
# each condition's seven criteria in arrival order, then the two set criteria, then each pair in
# input order). Comparing the lists directly therefore holds all of that at once: a reordering,
# a dropped outcome, a flipped flag or a changed subject tuple all surface as inequality.
#
# WHY n RUNS AND NOT JUST TWO. Two calls catch a first-call-differs bug; drawing n in 2 … 5 and
# comparing every run against the first also catches state that accumulates - an outcome list
# appended to across calls, a cache that only diverges on the third read. Each run is compared to
# the first rather than to its predecessor, so the failure message names how far a diverging run
# had drifted from the original.
#
# WHY BOTH ADMITTED AND REJECTED SETS. Idempotence of a verdict is only interesting where a
# verdict exists to be stable, and it must hold for *both* verdicts: a `validate` that memoised
# only its rejections, or only recomputed on the admitted path, would pass a run of one kind and
# fail the other. `admission_candidates` already draws both; the run asserts each was seen, so a
# degenerate run that produced only one verdict cannot pass this property vacuously.


def test_p38_revalidation_is_idempotent() -> None:
    """Revalidating the same rows any number of times yields the identical outcome list.

    **Validates: Requirements 3.12**
    """
    from backend_app.backend.marketplace.evidence_validator import all_passed, validate

    examples = 0
    admitted = 0
    rejected = 0

    @settings(property_test, max_examples=200)
    @given(drawn=admission_candidates(), n=st.integers(min_value=2, max_value=5))
    def check(drawn: Tuple[Any, Any, list, str, Optional[str]], n: int) -> None:
        nonlocal examples, admitted, rejected
        examples += 1

        owner, strategy, rows, mode, targeted = drawn

        first = validate(rows, owner, strategy)
        if all_passed(first):
            admitted += 1
        else:
            rejected += 1
        event(f"mode: {mode}")
        event(f"admitted: {all_passed(first)}")

        # Every subsequent run must reproduce the first, list for list. CriterionOutcome is
        # frozen, so `==` compares code, passed, message and subjects, and list equality also
        # pins their order - the whole of Requirement 3.12's "same outcome and the same
        # per-criterion outcomes" in one comparison.
        for run in range(2, n + 1):
            again = validate(rows, owner, strategy)
            assert again == first, (
                "validate is not idempotent: "
                f"run {run} of {n} produced a different outcome list from run 1 "
                f"(mode={mode}, targeted={targeted!r}, {len(rows)} conditions). "
                f"run 1: {[(o.code, o.passed, o.subjects) for o in first]!r}; "
                f"run {run}: {[(o.code, o.passed, o.subjects) for o in again]!r}"
            )

    check()

    assert examples >= 100, f"the property body ran on only {examples} evidence sets"

    # NON-VACUITY. Idempotence is only meaningful where a verdict exists, and it must hold on
    # both sides of the decision - a validator that memoised only one path would pass a run of
    # a single verdict. Both floors force the other kind of set into the run.
    assert admitted >= 10, (
        f"only {admitted} of {examples} drawn sets were admitted, so P-38 barely tested that "
        "an admitting verdict is stable across revalidation"
    )
    assert rejected >= 10, (
        f"only {rejected} of {examples} drawn sets were rejected, so P-38 barely tested that "
        "a rejecting verdict is stable across revalidation"
    )

# ---------------------------------------------------------------------------
# P-39
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 39 (metamorphic, no substitution):
# For all Backtest_Evidence sets with a missing metric on any condition, the set is rejected and
# no displayed or stored Listing metric takes a value absent from the evidence.
#
# Two requirements meet here. Requirement 3.4 makes an absent run parameter a hard failure, and
# Requirement 3.11 forbids the Marketplace from computing, estimating, extrapolating, defaulting
# or *substituting* any backtest figure the evidence does not contain - "absent means failed,
# never defaulted", as the module docstring puts it. P-33 … P-38 are about which sets pass; this
# is about what the validator must never quietly *fabricate* on the way to that verdict. An owner
# whose run never recorded, say, its `initial_capital` or its `total_trades` must be refused with
# a code, not silently handed a zero or a figure inferred from the equity curve so the Submission
# slips through and a made-up number reaches a Listing.
#
# WHAT THE VALIDATOR IS ANSWERABLE FOR. `validate` reads only the figures Requirement 3's
# criteria name: the eight parameters of Requirement 3.4 (EV_PARAMS), `total_trades` (EV_TRADES)
# and `executed_bar_count` (EV_BARS). Null any one of these on a single otherwise-admissible
# condition and the set must be rejected, with that criterion's code among the failures - the
# "rejected" half of P-39, restricted to the criteria the module owns (the seven-metric
# completeness check of Requirement 2.6 is the gate's `MP_METRICS_COMPLETE`, evaluated a layer
# up). The remaining result metrics - `sharpe_ratio`, `sortino_ratio`, `max_drawdown`,
# `win_rate`, `profit_factor`, `final_capital` - are figures the validator must *not* read at
# all; they are the display-only Listing figures Requirement 3.11 governs.
#
# THE METAMORPHIC "NOTHING SUBSTITUTED" HALF. For every nulled field, the test also validates a
# *twin* set in which that one field carries a fabricated, obviously-traceable value the evidence
# never held, everything else equal, and compares the two runs:
#
# * For a display-only metric the validator must ignore, the two verdicts must be *identical* -
#   the full outcome list, code, pass flag, message and subjects. A validator that read the
#   metric, defaulted it, or leaked it into a message would diverge on the twin, because one run
#   sees `None` and the other sees the fabricated value; identical output is the evidence that
#   the figure played no part.
# * For an enforced field, nulling it must fail its criterion, and *no* outcome of the nulled run
#   may carry the fabricated value anywhere in its own fields - `validate` reports the failure,
#   it does not fill the hole. `CriterionOutcome` exposes only `code`, `passed`, `message` and
#   `subjects`; the test asserts those are the whole of it, that every `subjects` entry is an
#   identifier the input set actually carried (nothing fabricated), and that the fabricated
#   sentinel appears in none of them.
#
# NON-VACUITY (the inner-function counting pattern of P-34 and P-37). Each half needs its own
# inputs: a run of only display-only nulls never checks the "rejected" half, a run of only
# enforced nulls never checks that an ignored metric is truly ignored, and a claim about "any
# condition" checked only on the first row is half a claim. The inner function tallies which
# category each draw nulled and on which row, and the counters below hold the run to having
# exercised all of them - the tallies are facts about the whole run, assertable only once it is
# over, which is why the Hypothesis-driven body is a nested function.


#: Requirement 3.4's eight parameters - the fields whose absence the validator must fail with
#: EV_PARAMS. Same tuple the module exports; asserted equal below so a drift is a failure here.
_P39_PARAM_FIELDS: Tuple[str, ...] = (
    "dataset",
    "start_date",
    "end_date",
    "initial_capital",
    "commission",
    "slippage",
    "dataset_checksum",
    "dag_hash",
)

#: Result metrics the validator *does* read, each via its own criterion, so nulling one must
#: reject the set with that code.
_P39_ENFORCED_METRICS: Dict[str, str] = {
    "total_trades": "EV_TRADES",
    "executed_bar_count": "EV_BARS",
}

#: Result metrics the validator must never read - the display-only Listing figures of
#: Requirement 3.11. Nulling one must change no verdict, and the validator must invent nothing
#: in its place.
_P39_DISPLAY_METRICS: Tuple[str, ...] = (
    "total_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "final_capital",
)

#: A value the evidence never contains, of a shape that could pass for any of the fields above
#: had the validator tried to read it - so if any outcome carries it, something substituted it.
_P39_SENTINEL = "P39-SUBSTITUTED-SENTINEL"


@st.composite
def missing_metric_candidates(
    draw,
) -> Tuple[Any, Any, list, int, str, str]:
    """An admissible set with one field nulled on one row, plus what was nulled and where.

    ``owner``, ``strategy`` and ``version`` are drawn here and handed to ``evidence_sets`` so the
    set is admissible *but for* the single nulled field - the ownership and one-version criteria
    have the identifiers they are evaluated against, exactly as P-37's ``admission_candidates``
    arranges, so the nulled field is the only thing wrong.

    The field to null is drawn from a named ``category`` - ``param``, ``enforced_metric`` or
    ``display_metric`` - drawn about equally often so each half of P-39 is fed on every run, and
    the row index is drawn across the whole set so "any condition" is not only ever the first.

    The field is nulled directly on the chosen row, the way ``evidence_sets`` nulls
    ``version_id`` for its own ``null_version`` defect, because the property must know *which*
    field went missing; the generator's ``null_parameter`` / ``null_metric`` defects pick at
    random and would leave that unknown.

    Returns ``(owner, strategy, rows, index, field, category)``.
    """
    from tests.strategies.marketplace_generators import evidence_sets, identifiers

    owner = draw(identifiers())
    strategy = draw(identifiers())
    version = draw(identifiers())

    rows = draw(
        evidence_sets(
            owner_id=owner,
            strategy_id=strategy,
            version_id=version,
            admissible=True,
        )
    )

    category = draw(st.sampled_from(("param", "enforced_metric", "display_metric")))
    if category == "param":
        field = draw(st.sampled_from(_P39_PARAM_FIELDS))
    elif category == "enforced_metric":
        field = draw(st.sampled_from(sorted(_P39_ENFORCED_METRICS)))
    else:
        field = draw(st.sampled_from(_P39_DISPLAY_METRICS))

    index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
    rows[index] = dict(rows[index], **{field: None})

    return owner, strategy, rows, index, field, category


def test_p39_missing_metric_rejects_and_nothing_is_substituted() -> None:
    """A missing metric rejects the set, and the validator substitutes nothing in its place.

    **Validates: Requirements 3.11, 2.6**
    """
    import dataclasses

    from backend_app.backend.marketplace.evidence_validator import (
        REQUIRED_PARAMETER_FIELDS,
        CriterionOutcome,
        all_passed,
        failed_outcomes,
        validate,
    )

    # The oracle names Requirement 3.4's eight parameters itself; if the module's set ever
    # changes, this property is about a different rule and must be rewritten, not silently
    # re-read from whatever the code now lists.
    assert tuple(REQUIRED_PARAMETER_FIELDS) == _P39_PARAM_FIELDS, (
        "P-39 encodes Requirement 3.4's parameter list; evidence_validator now records "
        f"{REQUIRED_PARAMETER_FIELDS!r}"
    )

    # CriterionOutcome must expose no value-bearing field. If a later edit adds one, "nothing is
    # substituted" would no longer be structurally guaranteed and this property must be revisited
    # rather than passing while a new channel leaks a fabricated figure.
    outcome_fields = frozenset(f.name for f in dataclasses.fields(CriterionOutcome))
    assert outcome_fields == frozenset({"code", "passed", "message", "subjects"}), (
        "P-39 assumes a CriterionOutcome carries only code/passed/message/subjects; it now "
        f"carries {sorted(outcome_fields)}"
    )

    examples = 0
    nulled_param = 0
    nulled_enforced_metric = 0
    nulled_display_metric = 0
    rejected_for_param = 0
    rejected_for_enforced = 0
    display_verdict_unchanged = 0
    nulled_non_first_row = 0

    @settings(property_test, max_examples=300)
    @given(drawn=missing_metric_candidates())
    def check(drawn: Tuple[Any, Any, list, int, str, str]) -> None:
        nonlocal examples, nulled_param, nulled_enforced_metric, nulled_display_metric
        nonlocal rejected_for_param, rejected_for_enforced
        nonlocal display_verdict_unchanged, nulled_non_first_row
        examples += 1

        owner, strategy, rows, index, field, category = drawn

        # The precondition: the field really is missing on exactly the row we think it is.
        assert rows[index][field] is None, "the generator did not null the field it reported"
        assert index >= 0
        if index != 0:
            nulled_non_first_row += 1

        outcomes = validate(rows, owner, strategy)
        recorded_ids = {row.get("id") for row in rows if row.get("id") is not None}
        recorded_id_texts = {str(i) for i in recorded_ids}

        # ── "Nothing substituted", the part that holds for every category ──
        # A verdict is a code, a pass flag, a public sentence and the affected identifiers - and
        # nothing else. Every subject must be an identifier the set actually carried (the
        # validator names affected rows, it does not fabricate them), and the traceable sentinel
        # - a value the evidence never held - must appear in none of an outcome's fields.
        for outcome in outcomes:
            assert isinstance(outcome, CriterionOutcome)
            for subject in outcome.subjects:
                assert subject in recorded_id_texts, (
                    "an outcome named a subject the evidence set never contained: "
                    f"{subject!r} not in {sorted(recorded_id_texts)}"
                )
            assert _P39_SENTINEL not in outcome.code
            assert _P39_SENTINEL not in outcome.message
            assert all(_P39_SENTINEL not in s for s in outcome.subjects)

        # ── The metamorphic twin: the same set, the nulled field given a fabricated value ──
        twin_rows = list(rows)
        twin_rows[index] = dict(rows[index], **{field: _P39_SENTINEL})
        twin_outcomes = validate(twin_rows, owner, strategy)

        event(f"category: {category}")
        event(f"field: {field}")

        if category == "param":
            nulled_param += 1
            # Requirement 3.4: the set is rejected, and EV_PARAMS is among the reasons.
            assert not all_passed(outcomes), (
                f"a null {field!r} parameter did not reject the set (row {index})"
            )
            failed_codes = {o.code for o in failed_outcomes(outcomes)}
            assert "EV_PARAMS" in failed_codes, (
                f"a null {field!r} parameter did not fail EV_PARAMS; failures were "
                f"{sorted(failed_codes)}"
            )
            rejected_for_param += 1
            # Requirement 3.11: no outcome of the *nulled* run may carry the fabricated value,
            # so validate cannot have read a substitute for the missing parameter.
            for outcome in outcomes:
                assert _P39_SENTINEL not in outcome.code
                assert _P39_SENTINEL not in outcome.message
                assert all(_P39_SENTINEL not in s for s in outcome.subjects)

        elif category == "enforced_metric":
            nulled_enforced_metric += 1
            expected_code = _P39_ENFORCED_METRICS[field]
            assert not all_passed(outcomes), (
                f"a null {field!r} metric did not reject the set (row {index})"
            )
            failed_codes = {o.code for o in failed_outcomes(outcomes)}
            assert expected_code in failed_codes, (
                f"a null {field!r} metric did not fail {expected_code}; failures were "
                f"{sorted(failed_codes)}"
            )
            rejected_for_enforced += 1

        else:  # display_metric - a figure the validator must never read
            nulled_display_metric += 1
            # Requirement 3.11's core: the verdict must not depend on a display-only metric. The
            # nulled run and the fabricated-value run must agree in full - same codes, same pass
            # flags, same messages, same subjects - which can only hold if validate neither read
            # the figure nor substituted one for it.
            assert twin_outcomes == outcomes, (
                f"validate's verdict changed when the display-only metric {field!r} went from "
                "absent to a fabricated value, so the validator read a figure Requirement 3.11 "
                "forbids it to read or substitute"
            )
            display_verdict_unchanged += 1
            # And the fabricated value must not have leaked into the twin's outcomes either.
            for outcome in twin_outcomes:
                assert _P39_SENTINEL not in outcome.code
                assert _P39_SENTINEL not in outcome.message
                assert all(_P39_SENTINEL not in s for s in outcome.subjects)

    check()

    assert examples >= 100, f"the property body ran on only {examples} sets"

    # NON-VACUITY. Each half of P-39 needs its own inputs, and "any condition" needs a nulled
    # field somewhere other than the first row. Each floor was set by mutating validate - having
    # it default a missing parameter, and having it read a display-only metric - and checking the
    # matching counter is what surfaces the mutation.
    assert nulled_param >= 10, (
        f"only {nulled_param} of {examples} draws nulled a Requirement 3.4 parameter, so the "
        "EV_PARAMS rejection half is barely tested"
    )
    assert rejected_for_param == nulled_param, (
        f"only {rejected_for_param} of {nulled_param} nulled-parameter sets were rejected with "
        "EV_PARAMS"
    )
    assert nulled_enforced_metric >= 10, (
        f"only {nulled_enforced_metric} of {examples} draws nulled a validator-read metric, so "
        "the EV_TRADES / EV_BARS rejection half is barely tested"
    )
    assert rejected_for_enforced == nulled_enforced_metric, (
        f"only {rejected_for_enforced} of {nulled_enforced_metric} nulled-metric sets were "
        "rejected with the matching criterion"
    )
    assert nulled_display_metric >= 10, (
        f"only {nulled_display_metric} of {examples} draws nulled a display-only metric, so the "
        "'nothing substituted' half was barely tested on the figures Requirement 3.11 governs"
    )
    assert display_verdict_unchanged == nulled_display_metric, (
        f"only {display_verdict_unchanged} of {nulled_display_metric} display-only nulls left "
        "the verdict unchanged against a fabricated value"
    )
    assert nulled_non_first_row >= 10, (
        f"only {nulled_non_first_row} of {examples} draws nulled a field on a row other than the "
        "first, so 'any condition' is barely tested"
    )

# ---------------------------------------------------------------------------
# P-40
# ---------------------------------------------------------------------------

# Feature: marketplace-subscriptions-paper-trading, Property 40 (error-condition, ownership):
# For all references that fail ownership, the EV_OWNERSHIP verdict a foreign-owned row produces
# is indistinguishable from the one a non-existent row produces - the same code, the same pass
# flag and the same owner-facing sentence - so ownership of a foreign row cannot be probed
# through the response.
#
# Requirement 3.13 is the only criterion of Requirement 3 that is about *secrecy* rather than
# quality: a referenced `strategy_backtests` row that is absent, owned by another user, or not
# `completed` must be rejected, and the rejection "SHALL NOT disclose whether the row exists
# under another owner". The gate reads `strategy_backtests` scoped to the authenticated owner,
# so a row belonging to someone else is simply never in the list `validate` receives - the
# foreign-owned case and the never-existed case arrive here as the *same* input, an owner-scoped
# read that came back without that row. What P-40 pins down is that the validator, faced with a
# reference it cannot attribute to this owner and this strategy, answers in a way that carries no
# trace of which of the two it was: were the message, the code or the pass flag to differ, an
# owner could flip a single `user_id` and read from the response whether that run exists under
# another account - exactly the probe Requirement 3.13 forbids.
#
# WHY THE THREE FAILURE MODES MUST AGREE. `validate`'s EV_OWNERSHIP check passes only when the
# row's `user_id` matches the strategy owner *and* its `strategy_id` matches the submitted
# strategy. There are three ways a reference reaches this module without satisfying that: its
# owner is someone else's identifier (a foreign account holds the run), its strategy is someone
# else's (the run is of a different strategy), or the row the owner-scoped read expected was not
# returned at all and a mismatching reference stands in its place - the "does not exist under
# this owner" case. The property holds all three EV_OWNERSHIP outcomes equal to one canonical
# ownership failure, so no branch of the check has grown a distinguishing message or a different
# code.
#
# THE INDISTINGUISHABILITY IS OF THE CALLER-VISIBLE OUTCOME. `CriterionOutcome` is what the
# service layer turns into the HTTP body (Requirements 2.10, 3.14), so "indistinguishable to the
# caller" is equality of `code`, `passed` and `message`. `subjects` is deliberately *excluded*
# from that equality: it names the affected Backtest_Condition by its own row id, which is the
# owner's own reference echoed back (Requirement 3.14's "identifiers of the affected
# Backtest_Conditions") and says nothing about the absent-or-foreign row's existence. The test
# instead asserts the stronger secrecy fact directly: the foreign owner's identifier - the datum
# Requirement 3.13 forbids disclosing - appears in *no* field of *any* outcome, not the failing
# EV_OWNERSHIP one and not the others.
#
# WHY THE ROW IS OTHERWISE WELL FORMED. A row carrying other defects would fail other criteria
# too, and a run where EV_OWNERSHIP was never the discriminating failure would say nothing about
# whether *it* leaks. Each row is drawn `satisfying=True` and then has exactly its ownership
# broken, so EV_OWNERSHIP is the one criterion whose verdict changes between the passing twin and
# the failing row - which is what lets the test assert that its message is constant across the
# three ways of breaking it and equal to the message on a set with no owner at all.


@st.composite
def ownership_failures(draw) -> Tuple[Any, Any, Dict[str, Any], Dict[str, Any], str]:
    """A well-formed row whose ownership is broken one of three ways, plus its passing twin.

    ``owner`` and ``strategy`` are the authenticated strategy owner and the submitted strategy,
    drawn here and handed to ``backtest_conditions`` so the twin genuinely belongs to them - the
    EV_OWNERSHIP check has real identifiers to match against, exactly as P-37's
    ``admission_candidates`` arranges. The failing row is that same twin with one attribution
    changed, so EV_OWNERSHIP is the only criterion whose verdict differs between the two:

    * ``foreign_user`` - ``user_id`` replaced by a freshly drawn identifier: the run is held by
      another account, the literal "owned by another user" of Requirement 3.13.
    * ``foreign_strategy`` - ``strategy_id`` replaced: the run is of a different strategy of this
      same owner, which the ownership check must refuse just as firmly.
    * ``absent`` - *both* replaced, standing for the row an owner-scoped read never returned: a
      reference that matches neither this owner nor this strategy, the "does not exist under this
      owner" case that must be answered identically to the two foreign ones.

    The replacement identifier is drawn to differ from the owner's, because a UUID collision
    would leave the row correctly attributed and the "failure" would not fail. The mode travels
    with the rows so a counterexample names which attribution was broken; the fabricated foreign
    identifiers are what the test then checks never surface in any outcome.

    Returns ``(owner, strategy, failing_row, passing_twin, mode)``.
    """
    owner = draw(identifiers_for_p40())
    strategy = draw(identifiers_for_p40())

    twin = draw(
        backtest_conditions(
            owner_id=owner,
            strategy_id=strategy,
            satisfying=True,
        )
    )

    # Foreign identifiers, each drawn until it differs from the value it replaces so the break
    # is a genuine mismatch rather than an accidental match.
    foreign_user = draw(
        identifiers_for_p40().filter(lambda value: value != owner)
    )
    foreign_strategy = draw(
        identifiers_for_p40().filter(lambda value: value != strategy)
    )

    mode = draw(st.sampled_from(("foreign_user", "foreign_strategy", "absent")))
    if mode == "foreign_user":
        failing = dict(twin, user_id=foreign_user)
    elif mode == "foreign_strategy":
        failing = dict(twin, strategy_id=foreign_strategy)
    else:  # absent - matches neither owner nor strategy
        failing = dict(twin, user_id=foreign_user, strategy_id=foreign_strategy)

    return owner, strategy, failing, twin, mode


def identifiers_for_p40() -> st.SearchStrategy:
    """The generators' shared identifier strategy, imported once for P-40's own draws.

    Kept out of the module-level imports so nothing above P-40 changes; ``identifiers`` is the
    same UUID-as-text strategy ``evidence_sets`` and ``admission_candidates`` already hand to
    ``backtest_conditions`` for owner, strategy and version.
    """
    from tests.strategies.marketplace_generators import identifiers

    return identifiers()


def _p40_ownership_outcome(outcomes: list) -> Any:
    """The single EV_OWNERSHIP outcome for a one-condition set, or fail loudly.

    ``validate`` emits exactly one EV_OWNERSHIP per condition, so a one-row set has exactly one;
    anything else means the emission contract this property rests on has changed.
    """
    from backend_app.backend.marketplace.evidence_validator import EV_OWNERSHIP

    owned = [o for o in outcomes if o.code == EV_OWNERSHIP]
    assert len(owned) == 1, (
        f"expected exactly one EV_OWNERSHIP outcome for a one-condition set, got {len(owned)}"
    )
    return owned[0]


def test_p40_foreign_owned_reference_is_indistinguishable_from_absent() -> None:
    """A foreign-owned reference fails ownership exactly as a non-existent one does.

    **Validates: Requirements 3.13**
    """
    from backend_app.backend.marketplace.evidence_validator import (
        EV_OWNERSHIP,
        EV_PUBLIC_MESSAGES,
        validate,
    )

    examples = 0
    saw_foreign_user = 0
    saw_foreign_strategy = 0
    saw_absent = 0

    # The one owner-facing sentence Requirement 3.13 permits: it names no owner, no row and no
    # reason beyond "one of your own completed runs of this strategy". If this ever changed, the
    # secrecy claim is about a different message and must be rewritten.
    canonical_message = EV_PUBLIC_MESSAGES[EV_OWNERSHIP]

    # Three modes drawn uniformly need more than the shared 100 to give each a comfortable
    # margin over its non-vacuity floor; the rest of the configuration is the shared one.
    @settings(property_test, max_examples=200)
    @given(drawn=ownership_failures())
    def check(drawn: Tuple[Any, Any, Dict[str, Any], Dict[str, Any], str]) -> None:
        nonlocal examples, saw_foreign_user, saw_foreign_strategy, saw_absent
        examples += 1

        owner, strategy, failing, twin, mode = drawn
        if mode == "foreign_user":
            saw_foreign_user += 1
        elif mode == "foreign_strategy":
            saw_foreign_strategy += 1
        else:
            saw_absent += 1
        event(f"mode: {mode}")

        # The passing twin: the very same run, correctly attributed to this owner and strategy.
        # Its EV_OWNERSHIP outcome is the reference point - ownership is the criterion that must
        # flip, and only it.
        twin_ownership = _p40_ownership_outcome(validate([twin], owner, strategy))
        assert twin_ownership.passed is True, (
            "the passing twin did not pass ownership, so the failing row differs from it in "
            f"more than ownership (mode={mode})"
        )

        # The failing row, whichever way its attribution was broken.
        outcomes = validate([failing], owner, strategy)
        failing_ownership = _p40_ownership_outcome(outcomes)

        # (1) It is a rejection with the ownership code and the one permitted sentence - the same
        # code and sentence for a foreign owner, a foreign strategy and a row that is not there.
        assert failing_ownership.passed is False, (
            f"a reference that is not this owner's own completed run of this strategy passed "
            f"ownership (mode={mode})"
        )
        assert failing_ownership.code == EV_OWNERSHIP
        assert failing_ownership.message == canonical_message, (
            "an ownership failure carried a message other than the single permitted sentence, "
            f"so the response can be told apart by mode: mode={mode}, "
            f"message={failing_ownership.message!r}"
        )
        # Same code and sentence as the passing twin's outcome, too: only the pass flag moves,
        # never the owner-facing text.
        assert failing_ownership.code == twin_ownership.code
        assert failing_ownership.message == twin_ownership.message, (
            "the ownership failure's sentence differs from the passing case's sentence, so the "
            f"verdict itself is legible in the message (mode={mode})"
        )

        # (2) Requirement 3.13's secrecy, stated directly: the fabricated foreign identifiers -
        # the data an owner would be probing for - appear in no field of any outcome. The only
        # identifier an outcome may name is the owner's own row reference (``subjects``), which is
        # the reference the owner supplied, not evidence of any row's existence under another
        # account.
        foreign_values = {failing.get("user_id"), failing.get("strategy_id")} - {
            twin.get("user_id"),
            twin.get("strategy_id"),
        }
        own_row_id = str(failing.get("id"))
        for outcome in outcomes:
            assert isinstance(outcome.message, str)
            for foreign in foreign_values:
                if foreign is None:
                    continue
                assert str(foreign) not in outcome.code
                assert str(foreign) not in outcome.message
                assert all(str(foreign) not in subject for subject in outcome.subjects)
            # Every subject an outcome names is the owner's own supplied reference.
            for subject in outcome.subjects:
                assert subject == own_row_id, (
                    "an outcome named a subject that was not the owner's own supplied "
                    f"reference: {subject!r} (mode={mode})"
                )

    check()

    assert examples >= 100, f"the property body ran on only {examples} references"

    # NON-VACUITY. The claim is that all three ways of failing ownership are indistinguishable,
    # so all three must be exercised: a run of only ``foreign_user`` draws would never compare a
    # foreign strategy or an absent row against it, and an implementation that leaked on one of
    # the untested modes would pass. Each mode is one of three sampled uniformly, so a floor well
    # below a third of the run still guarantees each was seen.
    assert saw_foreign_user >= 10, (
        f"only {saw_foreign_user} of {examples} references broke ownership by a foreign owner, "
        "so that mode of P-40 is barely tested"
    )
    assert saw_foreign_strategy >= 10, (
        f"only {saw_foreign_strategy} of {examples} references broke ownership by a foreign "
        "strategy, so that mode of P-40 is barely tested"
    )
    assert saw_absent >= 10, (
        f"only {saw_absent} of {examples} references stood for a row absent under this owner, so "
        "the non-existent-row mode of P-40 is barely tested"
    )
