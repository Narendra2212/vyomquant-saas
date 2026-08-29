"""backend/market_data_latency.py — the market data source decision, and what it is made of.

Spec: strategy-builder task 7.6. Requirements 19.11, 19.12, 19.13; ``design.md`` ->
"Latency measurement plan" (the metric table and ``ALGORITHM
choose_market_data_source``).

WHAT THIS MODULE IS
-------------------
Two things, and nothing else:

1. :class:`SourceMeasurement` — the shape of one candidate source's measured behaviour,
   one field per row of ``design.md``'s metric table, every field admitting ``None`` for
   *not measured*.
2. :func:`choose_market_data_source` — the decision. The **correctness floor first**
   (Requirement 19.11), the **25 ms p99 margin second** (Requirement 19.12), and a
   rendered record of both (Requirement 19.13).

The measuring itself lives in ``tests/perf/test_market_data_latency.py``, run as a
scripted experiment. This module holds the rule, because the rule is a control: it decides
whether a feed that can deliver an invented candle is allowed to feed the order router,
and a control does not live in a file that CI never collects.

WHY THE FLOOR IS STRUCTURAL AND NOT AN ORDERING
-----------------------------------------------
"Latency never outvotes correctness" is easy to write as::

    if a.correct and not b.correct: return a
    ...
    return min(a, b, key=p99)

and easy to break later by moving one branch. So the floor is not a branch order here. The
latency comparison — :func:`_decide_on_latency` — does not accept a
:class:`SourceMeasurement` at all. It accepts an :class:`_Admitted`, and an
:class:`_Admitted` cannot be constructed except by :func:`_admit`, which mints one only
after :func:`evaluate_correctness_floor` returns a passing verdict. A candidate that fails
the floor is therefore not "scored down" and not "compared later" — it is *unrepresentable*
as an input to the latency rule, at any latency. Deleting or reordering the floor check
does not make a failing candidate selectable; it makes the module raise.

The floor is also a floor and not a preference: there is no weight, no penalty and no
score. Requirement 19.11 admits a source that delivers "zero delivered synthetic candles",
and zero is not a number a faster candidate can buy its way past.

NOT MEASURED IS A FLOOR FAILURE, NOT A NEUTRAL
----------------------------------------------
Requirement 19.11 admits only a source that *delivers* at least 99.99 % completeness with
zero violations, duplicates, invalid candles and synthetic candles. A source nobody
measured has demonstrated none of that. So an unmeasured candidate fails the floor with
reason ``NOT_MEASURED``, and a candidate measured on some metrics but not the five floor
metrics fails naming the missing ones.

That is what makes the harness honest in an environment with no exchange socket: both
candidates come back unmeasured, both fail the floor, the decision is ``BLOCKED``, and the
report says the experiment did not run rather than inventing a winner. A decision document
reporting fabricated latencies is worse than one reporting that nothing was measured.

WHY THE PERCENTILES ARE EXACT AND NOT ``metrics.Histogram``
-----------------------------------------------------------
``backend/metrics.py``'s ``Histogram`` is Prometheus-shaped: it keeps bucket counts, not
samples, so a percentile read out of it is a bucket boundary. The 25 ms margin in
Requirement 19.12 is measured on exactly that quantity, and a bucket edge near 25 ms would
decide the comparison by the bucket layout rather than by the feed. :class:`LatencySummary`
therefore computes nearest-rank percentiles from retained samples. ``Histogram`` stays what
it is, for the dashboards it already serves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ══════════════════════════════════════════════════════════════════════════
# CANDIDATE IDENTITIES (design.md -> "Latency measurement plan")
# ══════════════════════════════════════════════════════════════════════════

#: Candidate A — the direct CCXT stream. ``mds/main.py`` publishes ``candles[-1]`` from
#: ``watch_ohlcv`` on every socket update; ``data_seeking_engine.DataEngine
#: .stream_live_ohlcv`` relays it. No closed-bar gate, no de-duplication, no ordering
#: check and no OHLC validation sit on that path.
SOURCE_A = "candidate_a_direct_ccxt_stream"

#: Candidate B — the validated OHLCV pipeline: ``market_data_contract.validated_window``
#: (closed bars only, first-wins duplicates, late-event drop) over
#: ``market_data_validation.MarketDataValidator``.
SOURCE_B = "candidate_b_validated_ohlcv_pipeline"

#: Requirement 19.12's margin, in milliseconds. A p99 advantage smaller than this does not
#: buy the removal of a validation layer.
P99_MARGIN_MS = 25.0


# ══════════════════════════════════════════════════════════════════════════
# LATENCY SAMPLES
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LatencySummary:
    """p50 / p95 / p99 / max over retained samples, in milliseconds.

    Built through :meth:`from_samples` so the percentile method is stated once. Nearest
    rank on the sorted samples: the smallest sample at or above the requested fraction of
    the population, which never reports a latency no event actually experienced.
    """

    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float

    @staticmethod
    def from_samples(samples: Sequence[float]) -> Optional["LatencySummary"]:
        """``None`` for an empty population — never a zero, which would read as "instant"."""
        usable = [float(s) for s in samples if s is not None and math.isfinite(float(s))]
        if not usable:
            return None
        ordered = sorted(usable)
        return LatencySummary(
            count=len(ordered),
            p50_ms=_nearest_rank(ordered, 0.50),
            p95_ms=_nearest_rank(ordered, 0.95),
            p99_ms=_nearest_rank(ordered, 0.99),
            max_ms=ordered[-1],
            mean_ms=sum(ordered) / len(ordered),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "p50_ms": round(self.p50_ms, 4),
            "p95_ms": round(self.p95_ms, 4),
            "p99_ms": round(self.p99_ms, 4),
            "max_ms": round(self.max_ms, 4),
            "mean_ms": round(self.mean_ms, 4),
        }


def _nearest_rank(ordered: Sequence[float], fraction: float) -> float:
    if not ordered:
        raise ValueError("no samples")
    rank = math.ceil(fraction * len(ordered))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return float(ordered[index])


# ══════════════════════════════════════════════════════════════════════════
# ONE CANDIDATE'S MEASURED BEHAVIOUR
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class SourceMeasurement:
    """One candidate source, as measured. One field per row of ``design.md``'s table.

    ``None`` means **not measured**, everywhere, and is never coerced to a zero or a
    default. Zero duplicates delivered and "nobody counted duplicates" are different facts
    and the floor treats them differently on purpose.

    ``has_validation_layer`` is not a measurement: it is a property of the code path, and
    Requirement 19.12 makes it the tie-break when the p99 margin is not met.
    """

    source: str
    has_validation_layer: bool

    # -- whether the experiment ran at all ---------------------------------
    measured: bool = False
    #: Why not, when ``measured`` is ``False``. Printed verbatim into the report.
    unavailable_reason: Optional[str] = None

    # -- the run ------------------------------------------------------------
    symbols: Tuple[str, ...] = ()
    timeframes: Tuple[str, ...] = ()
    window_seconds: Optional[float] = None
    events_observed: Optional[int] = None

    # -- latency (design.md rows 1-3) --------------------------------------
    end_to_end: Optional[LatencySummary] = None
    ingest: Optional[LatencySummary] = None
    pipeline: Optional[LatencySummary] = None

    # -- resource (rows 4-5) -----------------------------------------------
    cpu_percent_mean: Optional[float] = None
    cpu_percent_p95: Optional[float] = None
    rss_start_bytes: Optional[int] = None
    rss_end_bytes: Optional[int] = None

    # -- throughput (row 6) ------------------------------------------------
    throughput_events_per_second: Optional[float] = None

    # -- reconnect (row 7) -------------------------------------------------
    reconnects: Optional[int] = None
    reconnect_recovery_mean_seconds: Optional[float] = None
    reconnect_recovery_max_seconds: Optional[float] = None
    events_lost_per_reconnect: Optional[float] = None

    # -- completeness (row 8) ----------------------------------------------
    expected_bars: Optional[int] = None
    received_bars: Optional[int] = None

    # -- timestamp quality (row 9) -----------------------------------------
    monotonic_violations: Optional[int] = None
    clock_skew_ms: Optional[float] = None

    # -- duplication and ordering (rows 10-11) ------------------------------
    duplicates_delivered: Optional[int] = None
    out_of_order_delivered: Optional[int] = None

    # -- integrity, the floor's remaining two ------------------------------
    invalid_ohlc_delivered: Optional[int] = None
    synthetic_candles_delivered: Optional[int] = None

    # -- reliability (row 12) ----------------------------------------------
    uptime_fraction: Optional[float] = None
    error_count: Optional[int] = None
    silent_stall_episodes: Optional[int] = None

    #: Free-form observations the harness wants in the report but which are **not**
    #: decision inputs. Anything measured on generated rows rather than on a real feed
    #: belongs here, labelled, so it cannot be mistaken for a feed measurement.
    notes: List[str] = field(default_factory=list)

    # -- derived ------------------------------------------------------------

    @property
    def completeness(self) -> Optional[float]:
        """received ÷ expected, or ``None`` when either side was not counted.

        An expected count of zero yields ``None`` rather than 1.0: a window in which no bar
        was due proves nothing about completeness, and reporting a perfect score for it
        would let a source pass the floor on an empty measurement.
        """
        if self.expected_bars is None or self.received_bars is None:
            return None
        if int(self.expected_bars) <= 0:
            return None
        return float(self.received_bars) / float(self.expected_bars)

    @property
    def rss_growth_bytes(self) -> Optional[int]:
        if self.rss_start_bytes is None or self.rss_end_bytes is None:
            return None
        return int(self.rss_end_bytes) - int(self.rss_start_bytes)

    @property
    def p99_end_to_end_ms(self) -> Optional[float]:
        return None if self.end_to_end is None else self.end_to_end.p99_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "has_validation_layer": self.has_validation_layer,
            "measured": self.measured,
            "unavailable_reason": self.unavailable_reason,
            "symbols": list(self.symbols),
            "timeframes": list(self.timeframes),
            "window_seconds": self.window_seconds,
            "events_observed": self.events_observed,
            "end_to_end": None if self.end_to_end is None else self.end_to_end.to_dict(),
            "ingest": None if self.ingest is None else self.ingest.to_dict(),
            "pipeline": None if self.pipeline is None else self.pipeline.to_dict(),
            "cpu_percent_mean": self.cpu_percent_mean,
            "cpu_percent_p95": self.cpu_percent_p95,
            "rss_start_bytes": self.rss_start_bytes,
            "rss_end_bytes": self.rss_end_bytes,
            "rss_growth_bytes": self.rss_growth_bytes,
            "throughput_events_per_second": self.throughput_events_per_second,
            "reconnects": self.reconnects,
            "reconnect_recovery_mean_seconds": self.reconnect_recovery_mean_seconds,
            "reconnect_recovery_max_seconds": self.reconnect_recovery_max_seconds,
            "events_lost_per_reconnect": self.events_lost_per_reconnect,
            "expected_bars": self.expected_bars,
            "received_bars": self.received_bars,
            "completeness": self.completeness,
            "monotonic_violations": self.monotonic_violations,
            "clock_skew_ms": self.clock_skew_ms,
            "duplicates_delivered": self.duplicates_delivered,
            "out_of_order_delivered": self.out_of_order_delivered,
            "invalid_ohlc_delivered": self.invalid_ohlc_delivered,
            "synthetic_candles_delivered": self.synthetic_candles_delivered,
            "uptime_fraction": self.uptime_fraction,
            "error_count": self.error_count,
            "silent_stall_episodes": self.silent_stall_episodes,
            "notes": list(self.notes),
        }


def unmeasured(source: str, *, has_validation_layer: bool, reason: str) -> SourceMeasurement:
    """A candidate the experiment could not measure, carrying why.

    The honest degradation path. Everything stays ``None``; ``measured`` is ``False``;
    ``reason`` reaches the report verbatim.
    """
    return SourceMeasurement(
        source=source,
        has_validation_layer=bool(has_validation_layer),
        measured=False,
        unavailable_reason=str(reason),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE CORRECTNESS FLOOR (Requirement 19.11)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CorrectnessFloor:
    """Requirement 19.11's admission criteria, stated once, as numbers.

    "THE Market_Data_Pipeline SHALL admit only a market data source that delivers at least
    99.99 percent bar completeness, zero timestamp monotonicity violations, zero delivered
    duplicates, zero delivered invalid candles and zero delivered synthetic candles."

    Five criteria, no mode qualifier and no exception clause. The three zeros are written
    as maxima so the check reads uniformly, not because a non-zero value is configurable.
    """

    min_completeness: float = 0.9999
    max_monotonic_violations: int = 0
    max_duplicates_delivered: int = 0
    max_invalid_ohlc_delivered: int = 0
    max_synthetic_candles_delivered: int = 0

    @property
    def min_completeness_sample_bars(self) -> int:
        """The smallest bar count in which the completeness criterion can be resolved.

        Derived from ``min_completeness``, not chosen: at 99.99 % the smallest observable
        shortfall is one bar in ``1 / (1 - 0.9999)`` = 10,000. A window of 27 bars showing
        27 received cannot distinguish 100 % from 99.6 %, so "27 of 27" is not evidence of
        99.99 % completeness — it is evidence of nothing at that resolution.

        This is why a short smoke run does not admit a source. It is stated as a derived
        property rather than a second constant so that changing the completeness threshold
        moves the required sample with it.
        """
        shortfall = 1.0 - float(self.min_completeness)
        if shortfall <= 0.0:
            # A 100 % requirement is unfalsifiable by sampling; require the same evidence
            # a 99.99 % requirement does rather than admitting on one bar.
            return 10_000
        # `1 - 0.9999` is 9.999999999998899e-05 in binary floating point, and the naive
        # ceiling of its reciprocal is 10001 rather than 10000. Rounded before the ceiling so
        # the required sample is the number the threshold means, not its representation error.
        return int(math.ceil(round(1.0 / shortfall, 6)))


DEFAULT_FLOOR = CorrectnessFloor()

#: Rejection reasons. Stable strings so a runbook or a test matches on a code, not prose.
FLOOR_NOT_MEASURED = "NOT_MEASURED"
FLOOR_METRICS_MISSING = "FLOOR_METRICS_NOT_MEASURED"
FLOOR_EVIDENCE_INSUFFICIENT = "FLOOR_EVIDENCE_INSUFFICIENT"
FLOOR_CRITERIA_FAILED = "FLOOR_CRITERIA_FAILED"


@dataclass(frozen=True)
class FloorCheck:
    """One of the five criteria, with what was required and what was observed."""

    name: str
    requirement: str
    observed: Optional[float]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "requirement": self.requirement,
            "observed": self.observed,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class FloorVerdict:
    """Whether a candidate is admissible at all, and — when not — precisely why.

    ``rejection_reason`` is a code; ``rejection_detail`` is the sentence for the report.
    A failing verdict is a **hard rejection**: nothing downstream of it consults latency.
    """

    source: str
    passed: bool
    checks: Tuple[FloorCheck, ...]
    rejection_reason: Optional[str] = None
    rejection_detail: Optional[str] = None

    def failed_checks(self) -> Tuple[FloorCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "rejection_reason": self.rejection_reason,
            "rejection_detail": self.rejection_detail,
        }


def evaluate_correctness_floor(
    measurement: SourceMeasurement, floor: CorrectnessFloor = DEFAULT_FLOOR
) -> FloorVerdict:
    """Requirement 19.11 applied to one candidate. No latency is read here.

    Three ways to fail, and they are reported distinctly because they call for different
    actions:

    * ``NOT_MEASURED`` — the experiment did not run against this candidate. Run it.
    * ``FLOOR_METRICS_NOT_MEASURED`` — it ran, but one or more of the five floor metrics
      was not counted. Instrument it. A source is not admitted on the strength of metrics
      nobody collected.
    * ``FLOOR_EVIDENCE_INSUFFICIENT`` — it ran and everything was counted, but over too few
      bars for the completeness criterion to mean anything. Run it longer. See
      :attr:`CorrectnessFloor.min_completeness_sample_bars`.
    * ``FLOOR_CRITERIA_FAILED`` — it ran, everything was counted over a sufficient sample,
      and the source delivers something Requirement 19.11 forbids. Fix the source; do not
      re-run hoping for a better sample.
    """
    if not measurement.measured:
        return FloorVerdict(
            source=measurement.source,
            passed=False,
            checks=(),
            rejection_reason=FLOOR_NOT_MEASURED,
            rejection_detail=(
                measurement.unavailable_reason
                or "This candidate was not measured, so it has demonstrated none of "
                "Requirement 19.11's admission criteria."
            ),
        )

    sample_bars = floor.min_completeness_sample_bars
    checks = (
        FloorCheck(
            name="completeness",
            requirement=f">= {floor.min_completeness:.4f} of expected bars received",
            observed=measurement.completeness,
            passed=(
                measurement.completeness is not None
                and measurement.completeness >= floor.min_completeness
            ),
        ),
        FloorCheck(
            name="completeness_sample",
            requirement=(
                f">= {sample_bars} expected bars, the smallest sample in which "
                f"{floor.min_completeness:.4%} completeness is resolvable"
            ),
            observed=measurement.expected_bars,
            passed=(
                measurement.expected_bars is not None
                and int(measurement.expected_bars) >= sample_bars
            ),
        ),
        FloorCheck(
            name="monotonic_violations",
            requirement=f"<= {floor.max_monotonic_violations}",
            observed=measurement.monotonic_violations,
            passed=(
                measurement.monotonic_violations is not None
                and measurement.monotonic_violations <= floor.max_monotonic_violations
            ),
        ),
        FloorCheck(
            name="duplicates_delivered",
            requirement=f"<= {floor.max_duplicates_delivered}",
            observed=measurement.duplicates_delivered,
            passed=(
                measurement.duplicates_delivered is not None
                and measurement.duplicates_delivered <= floor.max_duplicates_delivered
            ),
        ),
        FloorCheck(
            name="invalid_ohlc_delivered",
            requirement=f"<= {floor.max_invalid_ohlc_delivered}",
            observed=measurement.invalid_ohlc_delivered,
            passed=(
                measurement.invalid_ohlc_delivered is not None
                and measurement.invalid_ohlc_delivered <= floor.max_invalid_ohlc_delivered
            ),
        ),
        FloorCheck(
            name="synthetic_candles_delivered",
            requirement=f"<= {floor.max_synthetic_candles_delivered}",
            observed=measurement.synthetic_candles_delivered,
            passed=(
                measurement.synthetic_candles_delivered is not None
                and measurement.synthetic_candles_delivered
                <= floor.max_synthetic_candles_delivered
            ),
        ),
    )

    uncounted = [c.name for c in checks if c.observed is None]
    if uncounted:
        return FloorVerdict(
            source=measurement.source,
            passed=False,
            checks=checks,
            rejection_reason=FLOOR_METRICS_MISSING,
            rejection_detail=(
                "Requirement 19.11 admits a source on measured behaviour, and these floor "
                "metrics were not counted for this candidate: " + ", ".join(uncounted) + "."
            ),
        )

    sample_check = next(c for c in checks if c.name == "completeness_sample")
    if not sample_check.passed:
        return FloorVerdict(
            source=measurement.source,
            passed=False,
            checks=checks,
            rejection_reason=FLOOR_EVIDENCE_INSUFFICIENT,
            rejection_detail=(
                f"The run covered {measurement.expected_bars} expected bars, below the "
                f"{sample_bars} needed for {floor.min_completeness:.4%} completeness to be "
                f"resolvable — a shortfall of one bar in {sample_bars} is the smallest one "
                f"that criterion can detect, so a shorter window cannot demonstrate it "
                f"whatever ratio it reports. This is an insufficient-evidence rejection, not "
                f"a defect in the source: run the experiment for the window `design.md` "
                f"prescribes."
            ),
        )

    failed = [c for c in checks if not c.passed]
    if failed:
        return FloorVerdict(
            source=measurement.source,
            passed=False,
            checks=checks,
            rejection_reason=FLOOR_CRITERIA_FAILED,
            rejection_detail=(
                "This candidate delivers what Requirement 19.11 forbids: "
                + "; ".join(
                    f"{c.name} was {c.observed} against a requirement of {c.requirement}"
                    for c in failed
                )
                + ". No latency advantage makes this source admissible."
            ),
        )

    return FloorVerdict(source=measurement.source, passed=True, checks=checks)


# ══════════════════════════════════════════════════════════════════════════
# ADMISSION TOKEN — why the floor cannot be reordered away
# ══════════════════════════════════════════════════════════════════════════

_ADMISSION_KEY = object()


@dataclass(frozen=True)
class _Admitted:
    """A candidate that has *already* passed the correctness floor.

    Only :func:`_admit` can build one. :func:`_decide_on_latency` takes nothing else. That
    is the structural half of "latency never outvotes correctness": the latency rule has no
    signature through which a floor-failing candidate can be passed to it.
    """

    measurement: SourceMeasurement
    verdict: FloorVerdict
    key: Any

    def __post_init__(self) -> None:
        if self.key is not _ADMISSION_KEY:
            raise RuntimeError(
                "_Admitted may only be minted by _admit() after the Requirement 19.11 "
                "correctness floor has passed. Constructing one directly would let a "
                "source that delivers an invented candle reach the latency comparison."
            )
        if not self.verdict.passed:
            raise RuntimeError(
                f"{self.measurement.source} did not pass the correctness floor "
                f"({self.verdict.rejection_reason}) and cannot be admitted."
            )


def _admit(
    measurement: SourceMeasurement, floor: CorrectnessFloor
) -> Tuple[Optional[_Admitted], FloorVerdict]:
    verdict = evaluate_correctness_floor(measurement, floor)
    if not verdict.passed:
        return None, verdict
    return _Admitted(measurement=measurement, verdict=verdict, key=_ADMISSION_KEY), verdict


# ══════════════════════════════════════════════════════════════════════════
# THE DECISION (Requirements 19.11, 19.12, 19.13)
# ══════════════════════════════════════════════════════════════════════════

OUTCOME_SELECTED = "SELECTED"
OUTCOME_BLOCKED = "BLOCKED"

RULE_FLOOR_BLOCKED_ALL = "FLOOR_ADMITTED_NOTHING"
RULE_FLOOR_ADMITTED_ONE = "FLOOR_ADMITTED_ONE"
RULE_MARGIN_MET = "P99_MARGIN_MET"
RULE_MARGIN_NOT_MET_VALIDATED = "P99_MARGIN_NOT_MET_VALIDATION_LAYER_PREFERRED"
RULE_MARGIN_NOT_MET_BOTH_VALIDATED = "P99_MARGIN_NOT_MET_BOTH_VALIDATED_LOWER_P99"
RULE_MARGIN_NOT_MET_NEITHER_VALIDATED = "P99_MARGIN_NOT_MET_NEITHER_VALIDATED_LOWER_P99"
RULE_LATENCY_UNMEASURED = "P99_UNMEASURED_VALIDATION_LAYER_PREFERRED"


@dataclass(frozen=True)
class SourceDecision:
    """The answer, the rule that produced it, and every input it was produced from.

    Requirement 19.13 — "SHALL record the measured completeness, latency, duplication,
    ordering, reconnection and reliability figures together with the selection decision" —
    is why ``measurements`` is carried rather than discarded once a winner is picked.
    """

    outcome: str
    selected: Optional[str]
    rule: str
    reason: str
    floor: Tuple[FloorVerdict, ...]
    measurements: Tuple[SourceMeasurement, ...]
    margin_threshold_ms: float = P99_MARGIN_MS
    #: Positive when the selected candidate's p99 is lower than the other's, by that many
    #: milliseconds. ``None`` when latency did not vote.
    p99_margin_ms: Optional[float] = None

    @property
    def blocked(self) -> bool:
        return self.outcome == OUTCOME_BLOCKED

    def verdict_for(self, source: str) -> Optional[FloorVerdict]:
        for verdict in self.floor:
            if verdict.source == source:
                return verdict
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "selected": self.selected,
            "rule": self.rule,
            "reason": self.reason,
            "margin_threshold_ms": self.margin_threshold_ms,
            "p99_margin_ms": self.p99_margin_ms,
            "floor": [v.to_dict() for v in self.floor],
            "measurements": [m.to_dict() for m in self.measurements],
        }


def choose_market_data_source(
    result_a: SourceMeasurement,
    result_b: SourceMeasurement,
    *,
    floor: CorrectnessFloor = DEFAULT_FLOOR,
) -> SourceDecision:
    """Pick the market data source, or block. ``design.md``'s algorithm, in two stages.

    **Stage one, correctness (Requirement 19.11).** Each candidate is put through
    :func:`evaluate_correctness_floor`. A failure is a hard rejection carrying a reason, not
    a penalty applied to a score. Nothing that fails is representable as an input to stage
    two — see :class:`_Admitted`.

    * Nothing admitted -> ``BLOCKED``. ``design.md``: "No source meets correctness floor.
      Fix correctness before choosing." No source is returned, not even the less bad one:
      returning one would be this function choosing between two sources that may both
      deliver an invented candle.
    * Exactly one admitted -> that one, *whatever the latencies are*. Correctness decided;
      latency did not vote, and ``p99_margin_ms`` is left ``None`` to say so.

    **Stage two, latency (Requirement 19.12).** Reached only with two admitted candidates.
    "Select the source with the lower 99th-percentile end-to-end latency when the difference
    is 25 milliseconds or greater, and otherwise select the source carrying the validation
    layer."

    * ``diff >= 25 ms`` -> the lower p99.
    * ``diff < 25 ms`` -> the candidate carrying the validation layer. 25 ms of latency is
      worth far less than one invented candle, so a sub-margin advantage does not buy the
      removal of a validation layer.
    * ``diff < 25 ms`` and **both or neither** carry a validation layer -> Requirement
      19.12's tie-break does not discriminate, so the lower p99 is taken as a free
      tie-break, and an exact p99 tie falls to the validated candidate, then to a stable
      source-name order. A tie is a real outcome; it gets a defined, deterministic answer
      rather than a coin flip that could differ between two runs of the same experiment.
    * A p99 that was not measured for either admitted candidate -> the validated candidate.
      Latency cannot vote on a number nobody has.
    """
    admitted_a, verdict_a = _admit(result_a, floor)
    admitted_b, verdict_b = _admit(result_b, floor)
    verdicts = (verdict_a, verdict_b)
    measurements = (result_a, result_b)

    admitted = [a for a in (admitted_a, admitted_b) if a is not None]

    if not admitted:
        return SourceDecision(
            outcome=OUTCOME_BLOCKED,
            selected=None,
            rule=RULE_FLOOR_BLOCKED_ALL,
            reason=(
                "No source meets the Requirement 19.11 correctness floor. Fix correctness "
                "before choosing. "
                + " ".join(
                    f"[{v.source}: {v.rejection_reason} — {v.rejection_detail}]"
                    for v in verdicts
                )
            ),
            floor=verdicts,
            measurements=measurements,
        )

    if len(admitted) == 1:
        winner = admitted[0].measurement
        rejected = verdict_b if admitted[0] is admitted_a else verdict_a
        return SourceDecision(
            outcome=OUTCOME_SELECTED,
            selected=winner.source,
            rule=RULE_FLOOR_ADMITTED_ONE,
            reason=(
                f"{winner.source} is the only candidate meeting the Requirement 19.11 "
                f"correctness floor, so correctness decided and latency did not vote. "
                f"{rejected.source} was rejected: {rejected.rejection_reason} — "
                f"{rejected.rejection_detail}"
            ),
            floor=verdicts,
            measurements=measurements,
        )

    return _decide_on_latency(admitted[0], admitted[1], verdicts, measurements)


def _decide_on_latency(
    first: _Admitted,
    second: _Admitted,
    verdicts: Tuple[FloorVerdict, ...],
    measurements: Tuple[SourceMeasurement, ...],
) -> SourceDecision:
    """Requirement 19.12, over candidates that have already cleared the floor.

    Takes :class:`_Admitted` and not :class:`SourceMeasurement`, deliberately. There is no
    way to reach this comparison with a candidate that failed the floor.
    """
    left, right = first.measurement, second.measurement
    p99_left, p99_right = left.p99_end_to_end_ms, right.p99_end_to_end_ms

    if p99_left is None or p99_right is None:
        preferred = _prefer_validated(left, right)
        missing = [m.source for m in (left, right) if m.p99_end_to_end_ms is None]
        return SourceDecision(
            outcome=OUTCOME_SELECTED,
            selected=preferred.source,
            rule=RULE_LATENCY_UNMEASURED,
            reason=(
                "Both candidates cleared the correctness floor, but end-to-end p99 latency "
                "was not measured for " + ", ".join(missing) + ", so latency cannot vote. "
                f"Selected {preferred.source} as the candidate "
                f"{'carrying' if preferred.has_validation_layer else 'preferred by stable order over'} "
                "the validation layer, which is the conservative answer."
            ),
            floor=verdicts,
            measurements=measurements,
            p99_margin_ms=None,
        )

    best, other = (left, right) if p99_left <= p99_right else (right, left)
    difference = float(other.p99_end_to_end_ms) - float(best.p99_end_to_end_ms)

    if difference >= P99_MARGIN_MS:
        return SourceDecision(
            outcome=OUTCOME_SELECTED,
            selected=best.source,
            rule=RULE_MARGIN_MET,
            reason=(
                f"Both candidates cleared the correctness floor. {best.source} has the "
                f"lower end-to-end p99 ({best.p99_end_to_end_ms:.3f} ms against "
                f"{other.p99_end_to_end_ms:.3f} ms), a margin of {difference:.3f} ms, "
                f"which meets Requirement 19.12's {P99_MARGIN_MS:.0f} ms threshold."
            ),
            floor=verdicts,
            measurements=measurements,
            p99_margin_ms=difference,
        )

    if best.has_validation_layer != other.has_validation_layer:
        validated = best if best.has_validation_layer else other
        margin = (
            difference
            if validated is best
            else -difference  # the validated candidate is the slower one
        )
        return SourceDecision(
            outcome=OUTCOME_SELECTED,
            selected=validated.source,
            rule=RULE_MARGIN_NOT_MET_VALIDATED,
            reason=(
                f"Both candidates cleared the correctness floor and their end-to-end p99 "
                f"figures differ by {difference:.3f} ms, under Requirement 19.12's "
                f"{P99_MARGIN_MS:.0f} ms margin. The margin is therefore not met and the "
                f"source carrying the validation layer is selected: {validated.source}. "
                f"A sub-margin latency advantage does not buy the removal of a validation "
                f"layer — 25 ms of latency is worth far less than one invented candle."
            ),
            floor=verdicts,
            measurements=measurements,
            p99_margin_ms=margin,
        )

    both_validated = best.has_validation_layer and other.has_validation_layer
    if difference == 0.0:
        tied = _prefer_validated(left, right)
        return SourceDecision(
            outcome=OUTCOME_SELECTED,
            selected=tied.source,
            rule=(
                RULE_MARGIN_NOT_MET_BOTH_VALIDATED
                if both_validated
                else RULE_MARGIN_NOT_MET_NEITHER_VALIDATED
            ),
            reason=(
                f"Both candidates cleared the correctness floor, carry "
                f"{'a' if both_validated else 'no'} validation layer, and report an "
                f"identical end-to-end p99 of {best.p99_end_to_end_ms:.3f} ms. Requirement "
                f"19.12's tie-break does not discriminate between them, so the decision "
                f"falls to a stable source order and resolves to {tied.source}. Recorded as "
                f"a tie rather than as a measured preference."
            ),
            floor=verdicts,
            measurements=measurements,
            p99_margin_ms=0.0,
        )

    return SourceDecision(
        outcome=OUTCOME_SELECTED,
        selected=best.source,
        rule=(
            RULE_MARGIN_NOT_MET_BOTH_VALIDATED
            if both_validated
            else RULE_MARGIN_NOT_MET_NEITHER_VALIDATED
        ),
        reason=(
            f"Both candidates cleared the correctness floor and both "
            f"{'carry' if both_validated else 'lack'} a validation layer, so Requirement "
            f"19.12's tie-break does not discriminate between them. Their end-to-end p99 "
            f"figures differ by {difference:.3f} ms, under the "
            f"{P99_MARGIN_MS:.0f} ms margin, so no latency claim is being made: the lower "
            f"p99 is taken as a free tie-break and resolves to {best.source}."
        ),
        floor=verdicts,
        measurements=measurements,
        p99_margin_ms=difference,
    )


def _prefer_validated(left: SourceMeasurement, right: SourceMeasurement) -> SourceMeasurement:
    """The conservative pick: the validated candidate, else a stable source-name order.

    Stable rather than arbitrary so two runs of the same experiment cannot disagree.
    """
    if left.has_validation_layer and not right.has_validation_layer:
        return left
    if right.has_validation_layer and not left.has_validation_layer:
        return right
    return left if left.source <= right.source else right


# ══════════════════════════════════════════════════════════════════════════
# THE RECORD (Requirement 19.13)
# ══════════════════════════════════════════════════════════════════════════

#: Where the harness publishes. ``design.md``: "The chosen source and the measured numbers
#: are recorded in ``reports/market_data_latency_decision.md`` and referenced by the
#: deployment runbook."
DECISION_REPORT_PATH = "reports/market_data_latency_decision.md"


def _fmt(value: Any, unit: str = "", digits: int = 3) -> str:
    """A number, or the words for *not measured*. Never a zero standing in for unknown."""
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}{unit}"
    return f"{value}{unit}"


def _latency_row(label: str, summary: Optional[LatencySummary]) -> str:
    if summary is None:
        return f"| {label} | not measured | not measured | not measured | not measured | 0 |"
    return (
        f"| {label} | {summary.p50_ms:.3f} | {summary.p95_ms:.3f} | "
        f"{summary.p99_ms:.3f} | {summary.max_ms:.3f} | {summary.count} |"
    )


def _measurement_section(m: SourceMeasurement) -> str:
    lines: List[str] = [f"### {m.source}", ""]
    lines.append(
        f"Carries a validation layer: **{'yes' if m.has_validation_layer else 'no'}**."
    )
    if not m.measured:
        lines += [
            "",
            "**Not measured.** " + (m.unavailable_reason or "No reason recorded."),
            "",
            "Every figure below would be an invention, so none is reported. This candidate "
            "has demonstrated none of Requirement 19.11's admission criteria and is "
            "inadmissible until the experiment runs against a real feed.",
            "",
        ]
        if m.notes:
            lines += ["Observations that *were* possible here, and are not feed measurements:", ""]
            lines += [f"- {note}" for note in m.notes]
            lines.append("")
        return "\n".join(lines)

    lines += [
        "",
        f"- Symbols: {', '.join(m.symbols) or 'not recorded'}",
        f"- Timeframes: {', '.join(m.timeframes) or 'not recorded'}",
        f"- Observation window: {_fmt(m.window_seconds, ' s', 1)}",
        f"- Events observed: {_fmt(m.events_observed)}",
        "",
        "**Latency (ms)**",
        "",
        "| Metric | p50 | p95 | p99 | max | samples |",
        "|---|---|---|---|---|---|",
        _latency_row("End-to-end (dag_input − exchange_timestamp)", m.end_to_end),
        _latency_row("Ingest (received − exchange_timestamp)", m.ingest),
        _latency_row("Pipeline (dag_input − received)", m.pipeline),
        "",
        "**Resource, throughput, reconnect**",
        "",
        f"- CPU: mean {_fmt(m.cpu_percent_mean, ' %', 2)}, p95 {_fmt(m.cpu_percent_p95, ' %', 2)}",
        f"- RSS: start {_fmt(m.rss_start_bytes, ' B')}, end {_fmt(m.rss_end_bytes, ' B')}, "
        f"growth {_fmt(m.rss_growth_bytes, ' B')}",
        f"- Throughput: {_fmt(m.throughput_events_per_second, ' events/s', 2)}",
        f"- Reconnects: {_fmt(m.reconnects)}; recovery mean "
        f"{_fmt(m.reconnect_recovery_mean_seconds, ' s', 2)}, max "
        f"{_fmt(m.reconnect_recovery_max_seconds, ' s', 2)}; events lost per reconnect "
        f"{_fmt(m.events_lost_per_reconnect, '', 2)}",
        "",
        "**Correctness and reliability**",
        "",
        f"- Completeness: {_fmt(m.completeness, '', 6)} "
        f"(received {_fmt(m.received_bars)} of {_fmt(m.expected_bars)} expected bars)",
        f"- Monotonicity violations: {_fmt(m.monotonic_violations)}; clock skew "
        f"{_fmt(m.clock_skew_ms, ' ms', 2)}",
        f"- Duplicates delivered: {_fmt(m.duplicates_delivered)}",
        f"- Out-of-order arrivals delivered: {_fmt(m.out_of_order_delivered)}",
        f"- Invalid OHLC candles delivered: {_fmt(m.invalid_ohlc_delivered)}",
        f"- Synthetic candles delivered: {_fmt(m.synthetic_candles_delivered)}",
        f"- Uptime: {_fmt(m.uptime_fraction, '', 6)}; errors {_fmt(m.error_count)}; "
        f"silent stalls {_fmt(m.silent_stall_episodes)}",
        "",
    ]
    if m.notes:
        lines += ["**Notes (not decision inputs)**", ""]
        lines += [f"- {note}" for note in m.notes]
        lines.append("")
    return "\n".join(lines)


def _floor_section(verdict: FloorVerdict) -> str:
    lines = [
        f"### {verdict.source} — "
        + ("**admitted**" if verdict.passed else "**rejected**"),
        "",
    ]
    if verdict.checks:
        lines += [
            "| Criterion | Requirement 19.11 | Observed | Verdict |",
            "|---|---|---|---|",
        ]
        for check in verdict.checks:
            lines.append(
                f"| {check.name} | {check.requirement} | "
                f"{'not measured' if check.observed is None else check.observed} | "
                f"{'pass' if check.passed else 'FAIL'} |"
            )
        lines.append("")
    if not verdict.passed:
        lines += [
            f"Rejection reason: `{verdict.rejection_reason}`.",
            "",
            verdict.rejection_detail or "",
            "",
        ]
    return "\n".join(lines)


def render_decision_report(
    decision: SourceDecision,
    *,
    environment_notes: Sequence[str] = (),
    experiment_notes: Sequence[str] = (),
    generated_at: Optional[datetime] = None,
) -> str:
    """The Requirement 19.13 record, as markdown.

    Ordered floor-first so the document reads the way the decision was made: what was
    measured, whether each candidate is admissible at all, and only then the latency
    comparison. ``environment_notes`` carries what this run could not do, because a
    decision document that omits its own limits is a decision document nobody can act on.
    """
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%SZ")
    a, b = decision.measurements

    lines: List[str] = [
        "# Market data source decision",
        "",
        f"Generated {stamp} by `tests/perf/test_market_data_latency.py` "
        f"(strategy-builder task 7.6; Requirements 19.11, 19.12, 19.13).",
        "",
        "## Decision",
        "",
        f"- Outcome: **{decision.outcome}**",
        f"- Selected source: **{decision.selected or 'none — blocked'}**",
        f"- Rule applied: `{decision.rule}`",
        f"- p99 margin threshold: {decision.margin_threshold_ms:.0f} ms",
        f"- Measured p99 margin: {_fmt(decision.p99_margin_ms, ' ms')}",
        "",
        decision.reason,
        "",
        "The order is fixed and is not a preference: the Requirement 19.11 correctness "
        "floor is applied first and a failure is a hard rejection; the Requirement 19.12 "
        "25 ms p99 margin is consulted only between candidates that already cleared it. "
        "In `backend/market_data_latency.py` the latency comparison cannot be reached with "
        "a floor-failing candidate — it accepts only an admission token that the floor gate "
        "mints — so latency cannot outvote correctness even if the branches were reordered.",
        "",
    ]

    if experiment_notes:
        lines += ["## What this run did", ""]
        lines += [f"- {note}" for note in experiment_notes]
        lines.append("")

    lines += ["## Correctness floor (Requirement 19.11)", ""]
    for verdict in decision.floor:
        lines.append(_floor_section(verdict))

    lines += ["## Measured figures (Requirement 19.13)", ""]
    lines.append(_measurement_section(a))
    lines.append(_measurement_section(b))

    if environment_notes:
        lines += [
            "## What was not measured, and why",
            "",
            "Recorded rather than omitted. A decision document reporting invented latencies "
            "is worse than one reporting that the experiment did not run.",
            "",
        ]
        lines += [f"- {note}" for note in environment_notes]
        lines.append("")

    lines += [
        "## Prescribed experiment, for comparison with what ran",
        "",
        "`design.md` specifies: a fixed symbol set (`BTC/USDT`, `ETH/USDT`, one thin alt), "
        "three timeframes (`1m`, `15m`, `1h`), 24 h continuous plus a 1 h forced-churn "
        "window with induced disconnects. Any run shorter than that, or over fewer symbols "
        "or timeframes, is a smoke run and is not the prescribed experiment. The `What this "
        "run did` section above states which one this was.",
        "",
    ]
    return "\n".join(lines)


def write_decision_report(
    decision: SourceDecision,
    *,
    path: str = DECISION_REPORT_PATH,
    environment_notes: Sequence[str] = (),
    experiment_notes: Sequence[str] = (),
    generated_at: Optional[datetime] = None,
) -> str:
    """Render and write the report. Returns the path written.

    Creates the parent directory if needed. No network, no database and no credential is
    touched: this writes one local markdown file.
    """
    import os

    body = render_decision_report(
        decision,
        environment_notes=environment_notes,
        experiment_notes=experiment_notes,
        generated_at=generated_at,
    )
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return path
