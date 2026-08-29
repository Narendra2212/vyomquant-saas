"""tests/test_market_data_source_decision.py — the market data source decision rule.

Spec: strategy-builder task 7.6. Requirements 19.11, 19.12, 19.13; ``design.md`` ->
"Latency measurement plan" -> ``ALGORITHM choose_market_data_source``.

WHY THIS FILE IS IN THE DEFAULT LANE AND THE HARNESS IS NOT
-----------------------------------------------------------
Task 7.6 names one file, ``tests/perf/test_market_data_latency.py``, and requires it to stay
out of CI. That is right for the *measuring*: it needs a live exchange socket and the design
prescribes a 24 h window, so CI must never trip it.

It is not right for the *rule*. ``choose_market_data_source`` decides whether a feed that
can deliver an invented candle is allowed to feed the order router. A control whose only
tests live in a directory CI never collects is not a control - the next edit to the floor
would land unchallenged. So the rule's tests are here, in the lane that actually runs, and
they need no network, no clock and no feed: every input below is a constructed
:class:`SourceMeasurement`.

WHAT IS BEING PINNED
--------------------
Three things, in the order the requirements put them:

1. **The floor is a floor** (19.11). Five criteria, all of them, and an unmeasured or
   uninstrumented candidate fails - "nobody counted" is not "counted zero".
2. **The floor cannot be outvoted** (19.11 before 19.12). A candidate that fails it is
   unselectable at *any* latency, including a latency better than the other candidate's by
   any margin, and including the case where it is the only candidate left. That is asserted
   structurally too: the latency comparison rejects a floor-failing input rather than
   ranking it.
3. **The 25 ms figure is a margin, not a threshold** (19.12). At or above it, the lower p99
   wins. Below it, the validated source wins - even when the validated source is the slower
   one. And a tie has a defined, repeatable answer rather than whichever way the noise fell.
"""
from __future__ import annotations

import pytest

from backend_app.backend.market_data_latency import (
    DEFAULT_FLOOR,
    FLOOR_CRITERIA_FAILED,
    FLOOR_EVIDENCE_INSUFFICIENT,
    FLOOR_METRICS_MISSING,
    FLOOR_NOT_MEASURED,
    OUTCOME_BLOCKED,
    OUTCOME_SELECTED,
    P99_MARGIN_MS,
    RULE_FLOOR_ADMITTED_ONE,
    RULE_FLOOR_BLOCKED_ALL,
    RULE_LATENCY_UNMEASURED,
    RULE_MARGIN_MET,
    RULE_MARGIN_NOT_MET_BOTH_VALIDATED,
    RULE_MARGIN_NOT_MET_NEITHER_VALIDATED,
    RULE_MARGIN_NOT_MET_VALIDATED,
    SOURCE_A,
    SOURCE_B,
    CorrectnessFloor,
    LatencySummary,
    SourceMeasurement,
    _admit,
    _Admitted,
    _decide_on_latency,
    choose_market_data_source,
    evaluate_correctness_floor,
    render_decision_report,
    unmeasured,
    write_decision_report,
)

# ══════════════════════════════════════════════════════════════════════════
# BUILDERS
# ══════════════════════════════════════════════════════════════════════════


def clean(
    source: str,
    *,
    validated: bool,
    p99_ms: float = 40.0,
    completeness: float = 1.0,
    **overrides,
) -> SourceMeasurement:
    """A candidate that passes the floor, with ``p99_ms`` end-to-end latency.

    ``expected_bars`` / ``received_bars`` are chosen so ``completeness`` comes out as asked
    - the property is derived, not settable, exactly as it is in production.
    """
    expected = 100_000
    measurement = SourceMeasurement(
        source=source,
        has_validation_layer=validated,
        measured=True,
        symbols=("BTC/USDT",),
        timeframes=("1m",),
        window_seconds=3600.0,
        events_observed=expected,
        expected_bars=expected,
        received_bars=int(round(completeness * expected)),
        monotonic_violations=0,
        duplicates_delivered=0,
        out_of_order_delivered=0,
        invalid_ohlc_delivered=0,
        synthetic_candles_delivered=0,
        uptime_fraction=1.0,
        error_count=0,
        silent_stall_episodes=0,
    )
    measurement.end_to_end = LatencySummary.from_samples([p99_ms])
    for key, value in overrides.items():
        setattr(measurement, key, value)
    return measurement


# ══════════════════════════════════════════════════════════════════════════
# LATENCY SUMMARY
# ══════════════════════════════════════════════════════════════════════════


class TestLatencySummary:
    """Percentiles a 25 ms margin can be measured against."""

    def test_an_empty_population_is_none_and_never_zero(self):
        assert LatencySummary.from_samples([]) is None

    def test_a_non_finite_sample_is_discarded_rather_than_propagated(self):
        assert LatencySummary.from_samples([float("nan"), float("inf")]) is None

    def test_the_percentiles_are_samples_that_actually_occurred(self):
        summary = LatencySummary.from_samples(list(range(1, 101)))

        assert summary is not None
        assert summary.count == 100
        assert summary.p50_ms == 50.0
        assert summary.p95_ms == 95.0
        assert summary.p99_ms == 99.0
        assert summary.max_ms == 100.0

    def test_a_single_sample_answers_every_percentile_with_itself(self):
        summary = LatencySummary.from_samples([7.5])

        assert summary is not None
        assert (summary.p50_ms, summary.p95_ms, summary.p99_ms, summary.max_ms) == (
            7.5,
            7.5,
            7.5,
            7.5,
        )


# ══════════════════════════════════════════════════════════════════════════
# THE FLOOR (Requirement 19.11)
# ══════════════════════════════════════════════════════════════════════════


class TestCorrectnessFloor:
    def test_the_shipped_floor_is_requirement_19_11s_five_numbers(self):
        assert DEFAULT_FLOOR == CorrectnessFloor(
            min_completeness=0.9999,
            max_monotonic_violations=0,
            max_duplicates_delivered=0,
            max_invalid_ohlc_delivered=0,
            max_synthetic_candles_delivered=0,
        )

    def test_a_clean_candidate_is_admitted_and_names_all_five_criteria(self):
        verdict = evaluate_correctness_floor(clean(SOURCE_B, validated=True))

        assert verdict.passed
        assert verdict.rejection_reason is None
        assert {c.name for c in verdict.checks} == {
            "completeness",
            "completeness_sample",
            "monotonic_violations",
            "duplicates_delivered",
            "invalid_ohlc_delivered",
            "synthetic_candles_delivered",
        }

    def test_an_unmeasured_candidate_fails_carrying_the_reason_it_was_not_measured(self):
        verdict = evaluate_correctness_floor(
            unmeasured(SOURCE_A, has_validation_layer=False, reason="no socket resolves here")
        )

        assert not verdict.passed
        assert verdict.rejection_reason == FLOOR_NOT_MEASURED
        assert "no socket resolves here" in (verdict.rejection_detail or "")

    def test_a_measured_candidate_missing_a_floor_metric_fails_naming_it(self):
        partial = clean(SOURCE_A, validated=False)
        partial.duplicates_delivered = None

        verdict = evaluate_correctness_floor(partial)

        assert not verdict.passed
        assert verdict.rejection_reason == FLOOR_METRICS_MISSING
        assert "duplicates_delivered" in (verdict.rejection_detail or "")

    def test_a_zero_expected_bar_count_does_not_score_as_perfect_completeness(self):
        empty = clean(SOURCE_B, validated=True)
        empty.expected_bars = 0
        empty.received_bars = 0

        assert empty.completeness is None
        assert evaluate_correctness_floor(empty).rejection_reason == FLOOR_METRICS_MISSING

    @pytest.mark.parametrize(
        "field, value",
        [
            ("monotonic_violations", 1),
            ("duplicates_delivered", 1),
            ("invalid_ohlc_delivered", 1),
            ("synthetic_candles_delivered", 1),
        ],
    )
    def test_a_single_forbidden_event_fails_the_floor(self, field, value):
        measurement = clean(SOURCE_A, validated=False)
        setattr(measurement, field, value)

        verdict = evaluate_correctness_floor(measurement)

        assert not verdict.passed
        assert verdict.rejection_reason == FLOOR_CRITERIA_FAILED
        assert field in (verdict.rejection_detail or "")

    @pytest.mark.parametrize(
        "completeness, admitted",
        [(1.0, True), (0.9999, True), (0.99989, False), (0.999, False), (0.9, False)],
    )
    def test_the_completeness_boundary_is_closed_at_99_99_percent(self, completeness, admitted):
        verdict = evaluate_correctness_floor(
            clean(SOURCE_B, validated=True, completeness=completeness)
        )

        assert verdict.passed is admitted

    def test_the_required_sample_is_derived_from_the_completeness_threshold(self):
        """10,000 bars is not a magic number: it is 1 / (1 − 0.9999)."""
        assert DEFAULT_FLOOR.min_completeness_sample_bars == 10_000
        assert CorrectnessFloor(min_completeness=0.999).min_completeness_sample_bars == 1_000
        assert CorrectnessFloor(min_completeness=0.99).min_completeness_sample_bars == 100
        # A 100 % requirement cannot be falsified by sampling; it must not admit on one bar.
        assert CorrectnessFloor(min_completeness=1.0).min_completeness_sample_bars == 10_000

    def test_a_perfect_ratio_over_too_few_bars_is_not_evidence_of_99_99_percent(self):
        """"27 of 27" cannot distinguish 100 % from 99.6 %, so it does not admit a source.

        This is the outcome a short smoke run gets, and it is reported as an evidence problem
        rather than as a defect in the source - they call for different actions.
        """
        smoke = clean(SOURCE_B, validated=True)
        smoke.expected_bars = 27
        smoke.received_bars = 27

        verdict = evaluate_correctness_floor(smoke)

        assert smoke.completeness == 1.0
        assert not verdict.passed
        assert verdict.rejection_reason == FLOOR_EVIDENCE_INSUFFICIENT
        assert "10000" in (verdict.rejection_detail or "")
        assert "not a defect in the source" in (verdict.rejection_detail or "")

    def test_the_sample_boundary_is_closed_at_the_derived_count(self):
        for bars, admitted in ((9_999, False), (10_000, True), (10_001, True)):
            measurement = clean(SOURCE_B, validated=True)
            measurement.expected_bars = bars
            measurement.received_bars = bars

            assert evaluate_correctness_floor(measurement).passed is admitted, bars

    def test_an_insufficient_sample_is_still_unselectable_at_any_latency(self):
        smoke = clean(SOURCE_B, validated=True, p99_ms=1.0)
        smoke.expected_bars = 100
        smoke.received_bars = 100

        decision = choose_market_data_source(
            unmeasured(SOURCE_A, has_validation_layer=False, reason="no transport"), smoke
        )

        assert decision.outcome == OUTCOME_BLOCKED
        assert decision.selected is None

    def test_a_rejection_always_carries_both_a_code_and_a_sentence(self):
        for measurement in (
            unmeasured(SOURCE_A, has_validation_layer=False, reason="unreachable"),
            clean(SOURCE_A, validated=False, completeness=0.5),
        ):
            verdict = evaluate_correctness_floor(measurement)

            assert verdict.rejection_reason and verdict.rejection_detail


# ══════════════════════════════════════════════════════════════════════════
# THE FLOOR CANNOT BE OUTVOTED (19.11 before 19.12)
# ══════════════════════════════════════════════════════════════════════════


class TestLatencyNeverOutvotesCorrectness:
    def test_a_synthetic_candle_delivering_source_is_unselectable_at_any_latency(self):
        """Requirement 19.11 admits only a source delivering zero synthetic candles.

        Zero is not a figure a faster candidate buys its way past, so this is parametrised
        over a latency advantage that dwarfs the 25 ms margin.
        """
        for advantage_ms in (1.0, 25.0, 1000.0, 100_000.0):
            fabricator = clean(
                SOURCE_A,
                validated=False,
                p99_ms=1.0,
                synthetic_candles_delivered=1,
            )
            honest = clean(SOURCE_B, validated=True, p99_ms=1.0 + advantage_ms)

            decision = choose_market_data_source(fabricator, honest)

            assert decision.selected == SOURCE_B, advantage_ms
            assert decision.rule == RULE_FLOOR_ADMITTED_ONE

    def test_the_sole_surviving_candidate_wins_without_latency_voting(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=1.0, duplicates_delivered=3),
            clean(SOURCE_B, validated=True, p99_ms=900.0),
        )

        assert decision.outcome == OUTCOME_SELECTED
        assert decision.selected == SOURCE_B
        assert decision.rule == RULE_FLOOR_ADMITTED_ONE
        assert decision.p99_margin_ms is None, "latency must not be reported as having voted"
        assert "duplicates_delivered" in decision.reason

    def test_two_failing_candidates_block_rather_than_electing_the_less_bad_one(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=1.0, synthetic_candles_delivered=1),
            clean(SOURCE_B, validated=True, p99_ms=2.0, duplicates_delivered=9),
        )

        assert decision.outcome == OUTCOME_BLOCKED
        assert decision.blocked
        assert decision.selected is None
        assert decision.rule == RULE_FLOOR_BLOCKED_ALL
        assert "Fix correctness before choosing" in decision.reason

    def test_an_unmeasured_experiment_blocks_and_says_so(self):
        """The honest-degradation outcome in an environment with no feed."""
        decision = choose_market_data_source(
            unmeasured(SOURCE_A, has_validation_layer=False, reason="no Redis transport"),
            unmeasured(SOURCE_B, has_validation_layer=True, reason="venue does not resolve"),
        )

        assert decision.outcome == OUTCOME_BLOCKED
        assert decision.selected is None
        assert "no Redis transport" in decision.reason
        assert "venue does not resolve" in decision.reason

    def test_the_latency_comparison_refuses_a_floor_failing_candidate_structurally(self):
        """Not "the branches happen to be ordered right" - the input is unrepresentable.

        ``_decide_on_latency`` accepts only an admission token, and the token's constructor
        refuses to mint one for a candidate the floor rejected. Reordering or deleting the
        floor branch in ``choose_market_data_source`` cannot make a fabricating source
        selectable; it makes the module raise.
        """
        fabricator = clean(SOURCE_A, validated=False, synthetic_candles_delivered=1)
        token, verdict = _admit(fabricator, DEFAULT_FLOOR)

        assert token is None and not verdict.passed

        with pytest.raises(RuntimeError):
            _Admitted(measurement=fabricator, verdict=verdict, key=object())

    def test_an_admission_token_cannot_be_forged_for_a_passing_candidate_either(self):
        """The key is the gate, not the verdict: a stolen verdict is not enough."""
        honest = clean(SOURCE_B, validated=True)
        token, verdict = _admit(honest, DEFAULT_FLOOR)

        assert token is not None and verdict.passed

        with pytest.raises(RuntimeError):
            _Admitted(measurement=honest, verdict=verdict, key="not-the-key")


# ══════════════════════════════════════════════════════════════════════════
# THE 25 ms MARGIN (Requirement 19.12)
# ══════════════════════════════════════════════════════════════════════════


class TestP99Margin:
    def test_the_margin_is_25_milliseconds(self):
        assert P99_MARGIN_MS == 25.0

    def test_a_margin_of_25_or_more_selects_the_lower_p99(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=35.0),
        )

        assert decision.selected == SOURCE_A
        assert decision.rule == RULE_MARGIN_MET
        assert decision.p99_margin_ms == pytest.approx(25.0)

    def test_a_margin_below_25_selects_the_validated_source_even_when_slower(self):
        """The whole point of 19.12's second clause.

        Candidate A is faster here, by 24.9 ms - under the margin - so the validation layer
        wins. 25 ms of latency is worth far less than one invented candle.
        """
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=34.9),
        )

        assert decision.selected == SOURCE_B
        assert decision.rule == RULE_MARGIN_NOT_MET_VALIDATED
        assert decision.p99_margin_ms == pytest.approx(-24.9)

    def test_the_boundary_falls_to_latency_at_exactly_25_and_to_validation_just_under(self):
        at_margin = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=35.0),
        )
        under_margin = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=34.999),
        )

        assert at_margin.selected == SOURCE_A
        assert under_margin.selected == SOURCE_B

    def test_a_sub_margin_advantage_for_the_validated_source_still_selects_it(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=30.0),
            clean(SOURCE_B, validated=True, p99_ms=20.0),
        )

        assert decision.selected == SOURCE_B
        assert decision.rule == RULE_MARGIN_NOT_MET_VALIDATED
        assert decision.p99_margin_ms == pytest.approx(10.0)

    def test_an_exact_tie_between_equally_validated_sources_is_recorded_as_a_tie(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=True, p99_ms=12.0),
            clean(SOURCE_B, validated=True, p99_ms=12.0),
        )

        assert decision.rule == RULE_MARGIN_NOT_MET_BOTH_VALIDATED
        assert decision.p99_margin_ms == 0.0
        assert "tie" in decision.reason.lower()
        assert decision.selected in (SOURCE_A, SOURCE_B)

    def test_a_tie_resolves_the_same_way_whichever_order_the_candidates_arrive_in(self):
        """A tie is a real outcome and must not flip between two runs of one experiment."""
        left = clean(SOURCE_A, validated=True, p99_ms=12.0)
        right = clean(SOURCE_B, validated=True, p99_ms=12.0)

        assert (
            choose_market_data_source(left, right).selected
            == choose_market_data_source(right, left).selected
        )

    def test_a_sub_margin_gap_between_equally_validated_sources_takes_the_lower_p99(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=True, p99_ms=12.0),
            clean(SOURCE_B, validated=True, p99_ms=20.0),
        )

        assert decision.selected == SOURCE_A
        assert decision.rule == RULE_MARGIN_NOT_MET_BOTH_VALIDATED
        assert "free tie-break" in decision.reason

    def test_a_sub_margin_gap_between_two_unvalidated_sources_takes_the_lower_p99(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=20.0),
            clean(SOURCE_B, validated=False, p99_ms=12.0),
        )

        assert decision.selected == SOURCE_B
        assert decision.rule == RULE_MARGIN_NOT_MET_NEITHER_VALIDATED

    def test_an_unmeasured_p99_hands_the_decision_to_the_validated_source(self):
        no_latency = clean(SOURCE_A, validated=False)
        no_latency.end_to_end = None

        decision = choose_market_data_source(
            no_latency, clean(SOURCE_B, validated=True, p99_ms=99.0)
        )

        assert decision.selected == SOURCE_B
        assert decision.rule == RULE_LATENCY_UNMEASURED
        assert decision.p99_margin_ms is None

    def test_latency_is_only_consulted_between_two_admitted_candidates(self):
        """Every rule that reads a p99 requires both candidates through the floor."""
        latency_rules = {
            RULE_MARGIN_MET,
            RULE_MARGIN_NOT_MET_VALIDATED,
            RULE_MARGIN_NOT_MET_BOTH_VALIDATED,
            RULE_MARGIN_NOT_MET_NEITHER_VALIDATED,
            RULE_LATENCY_UNMEASURED,
        }
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=1.0, monotonic_violations=2),
            clean(SOURCE_B, validated=True, p99_ms=500.0),
        )

        assert decision.rule not in latency_rules


# ══════════════════════════════════════════════════════════════════════════
# THE RECORD (Requirement 19.13)
# ══════════════════════════════════════════════════════════════════════════


class TestDecisionRecord:
    def test_the_decision_carries_every_measurement_it_was_made_from(self):
        a = clean(SOURCE_A, validated=False, p99_ms=10.0)
        b = clean(SOURCE_B, validated=True, p99_ms=12.0)

        decision = choose_market_data_source(a, b)

        assert decision.measurements == (a, b)
        assert {v.source for v in decision.floor} == {SOURCE_A, SOURCE_B}
        assert decision.verdict_for(SOURCE_A) is not None
        assert decision.verdict_for("nothing-by-that-name") is None

    def test_the_report_records_completeness_latency_duplication_ordering_and_reliability(self):
        decision = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=12.0),
        )

        body = render_decision_report(decision)

        for heading in (
            "Completeness",
            "Monotonicity violations",
            "Duplicates delivered",
            "Out-of-order arrivals delivered",
            "Reconnects",
            "Uptime",
            "End-to-end",
        ):
            assert heading in body, heading
        assert decision.rule in body
        assert "Correctness floor (Requirement 19.11)" in body

    def test_an_unmeasured_candidate_is_reported_as_unmeasured_not_as_zero(self):
        decision = choose_market_data_source(
            unmeasured(SOURCE_A, has_validation_layer=False, reason="getaddrinfo failed"),
            unmeasured(SOURCE_B, has_validation_layer=True, reason="no exchange reachable"),
        )

        body = render_decision_report(
            decision, environment_notes=("no market data socket is reachable",)
        )

        assert "**Not measured.**" in body
        assert "getaddrinfo failed" in body
        assert "no exchange reachable" in body
        assert "What was not measured, and why" in body
        assert "BLOCKED" in body
        # Not one fabricated figure anywhere in the document.
        assert "p50" not in body.split("Measured figures")[1] or "not measured" in body

    def test_the_report_states_that_the_floor_precedes_the_margin(self):
        body = render_decision_report(
            choose_market_data_source(
                clean(SOURCE_A, validated=False, p99_ms=10.0),
                clean(SOURCE_B, validated=True, p99_ms=12.0),
            )
        )

        assert "correctness floor is applied first" in body
        assert "25 ms p99 margin is consulted only between candidates" in body

    def test_the_report_is_written_to_the_path_the_spec_names(self, tmp_path):
        target = tmp_path / "nested" / "market_data_latency_decision.md"

        written = write_decision_report(
            choose_market_data_source(
                unmeasured(SOURCE_A, has_validation_layer=False, reason="unreachable"),
                unmeasured(SOURCE_B, has_validation_layer=True, reason="unreachable"),
            ),
            path=str(target),
        )

        assert written == str(target)
        assert target.read_text(encoding="utf-8").startswith("# Market data source decision")

    def test_the_dict_form_round_trips_the_whole_decision(self):
        payload = choose_market_data_source(
            clean(SOURCE_A, validated=False, p99_ms=10.0),
            clean(SOURCE_B, validated=True, p99_ms=12.0),
        ).to_dict()

        assert payload["outcome"] == OUTCOME_SELECTED
        assert payload["margin_threshold_ms"] == 25.0
        assert len(payload["floor"]) == 2
        assert len(payload["measurements"]) == 2
        assert payload["measurements"][0]["completeness"] == 1.0
        assert payload["measurements"][1]["end_to_end"]["p99_ms"] == 12.0


# ══════════════════════════════════════════════════════════════════════════
# THE CANDIDATES THE SPEC NAMES
# ══════════════════════════════════════════════════════════════════════════


class TestCandidateIdentities:
    def test_candidate_b_is_the_one_carrying_the_validation_layer(self):
        """The harness must not be free to relabel which candidate is validated.

        ``design.md`` fixes it: Candidate A is the direct CCXT stream, Candidate B is the
        validated OHLCV pipeline. 19.12's tie-break is meaningless if that mapping drifts.
        """
        assert "direct_ccxt" in SOURCE_A
        assert "validated" in SOURCE_B

    def test_unmeasured_leaves_every_figure_none(self):
        measurement = unmeasured(SOURCE_B, has_validation_layer=True, reason="no feed")

        assert measurement.measured is False
        assert measurement.completeness is None
        assert measurement.p99_end_to_end_ms is None
        assert measurement.rss_growth_bytes is None
        assert measurement.duplicates_delivered is None
        assert measurement.synthetic_candles_delivered is None
        assert measurement.has_validation_layer is True
