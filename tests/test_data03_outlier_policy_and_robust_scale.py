"""DATA-03 -- the >=10,000-bar correctness floor is satisfiable, and a bad tick still refuses.

THE DEFECT
----------
``OutlierDetector`` raised on the FIRST exceedance of any detector, so "this window contains
an extreme bar" and "this window is unusable" were the same statement. The quantity that then
decided admission was per-WINDOW, not per-point: ``P(reject) = 1 - (1-p)^n``, which tends to 1
as n grows for any p > 0. Measured over 12,000 live 1m bars each of BTC/USDT, ETH/USDT and
DOGE/USDT, 100% of >=10,000-bar windows were rejected at every threshold from 3.0 to 10.0 --
and the rule only "passed" at 12.0 because 12.0 exceeded the largest z-score present, i.e. by
detecting nothing at all. Requirement 19.11's >=10,000-bar floor was therefore unreachable at
every threshold that detected anything, and no value of ``MARKET_DATA_Z_SCORE_THRESHOLD``
could fix it. That blocked RT-04 (no admitted bar => paper sessions refused) and PAPER-02.

THE DECISION: A + C
-------------------
(A) ``OutlierPolicy.FLAG_AND_ADMIT`` -- flag the extreme bars, report how many, admit the
    window AS OBSERVED, and refuse only when the flagged FRACTION exceeds
    ``max_outlier_fraction``. The guarantee becomes BOUNDED contamination rather than ZERO
    contamination, which is a promise real market data can keep.
(C) ``OutlierMethod.ROBUST_Z_SCORE`` -- median/MAD rather than mean/std, threshold fitted to
    the measured return distribution.

Option (B) -- keep whole-window rejection and restate Requirement 19.11's floor -- was not
taken: the floor is a correctness requirement and weakening it to fit a detector defect would
have made the requirement describe the bug.

WHY THERE ARE TWO GUARDS AND NOT ONE
------------------------------------
DATA-02 established that a genuine single-bar price spike MUST still reject the window. A
fraction budget cannot deliver that -- one bad tick in 10,000 bars is 0.01% contamination,
far inside any usable budget. So the two guards answer different questions:

    fraction budget  "are there too many extreme points?"  -> fat tails, ordinary
    magnitude ceiling "is any single point impossible?"     -> bad ticks, never ordinary

The pre-DATA-03 rule had ONE threshold for both, which is precisely why it could not catch
the second without rejecting all real data for the first. Separating them is what lets both
be right at once, and it is why DATA-02's own spike tests still pass unchanged.

WHAT IS NOT DONE, DELIBERATELY
------------------------------
No bar is modified, excluded or winsorised. The measurement showed that what this rule calls
an "outlier" in real crypto data is overwhelmingly an ORDINARY LARGE MOVE, so the bar is the
venue's own true observation and replacing it would be the fabrication. Winsorising a close
price manufactures a price that never traded, which this module forbids everywhere else
(``_synthetic_fill``, ``GapHandler``'s refusal). Excluding the bar would punch a hole the gap
handler then refuses on the next step -- trading one unsatisfiable gate for another.

SCOPE
-----
Deterministic, offline, no network: the live-venue measurement that produced the constants is
reproduced by ``scripts/measure_market_data_outlier_reality.py``. These tests pin the POLICY
and the ESTIMATOR, using synthetic frames whose properties are stated in each test.
"""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from backend_app.backend.market_data_validation import (
    MAD_TO_SIGMA, MAX_BAR_LOG_MOVE_DEFAULT, MAX_OUTLIER_FRACTION_DEFAULT,
    ROBUST_Z_SCORE_THRESHOLD_DEFAULT, DataValidationError, MarketDataValidator,
    OutlierDetector, OutlierMethod, OutlierPolicy, ValidationConfig)


def _frame(closes, volumes=None) -> pd.DataFrame:
    """An OHLCV frame on a 1-minute grid. O=H=L=C so candle integrity is trivially satisfied
    and only the outlier step is under test."""
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": np.full(n, 100.0) if volumes is None else np.asarray(volumes, float),
        },
        index=idx,
    )


def _fat_tailed(n: int, seed: int = 0) -> np.ndarray:
    """A price path with Student-t log-returns: fat tails, like the real thing.

    t with 3 degrees of freedom is the standard stand-in for a heavy-tailed return series. It
    is what makes this frame a fair model of the measured problem: a long window of it
    reliably contains several points far outside a normal model's expectation.
    """
    rng = np.random.default_rng(seed)
    returns = rng.standard_t(df=3, size=n) * 0.0004
    return 100.0 * np.exp(np.cumsum(returns))


# ══════════════════════════════════════════════════════════════════════════════
#  1. The defect: whole-window rejection cannot meet the floor
# ══════════════════════════════════════════════════════════════════════════════


FLOOR_BARS = 10_000


def test_the_legacy_policy_still_rejects_a_floor_sized_window_of_fat_tailed_data():
    """The defect, reproduced. This is why option (B) was not taken."""
    frame = _frame(_fat_tailed(FLOOR_BARS, seed=7))
    legacy = ValidationConfig(
        outlier_method=OutlierMethod.Z_SCORE,
        outlier_policy=OutlierPolicy.REJECT_WINDOW,
    )
    with pytest.raises(DataValidationError):
        OutlierDetector.detect(frame, legacy.outlier_method, legacy, "FAT/TAIL")


def test_the_new_default_admits_the_same_floor_sized_window():
    """THE HEADLINE: the same bars that the legacy policy refuses are now admitted."""
    frame = _frame(_fat_tailed(FLOOR_BARS, seed=7))
    config = ValidationConfig()

    out, issues = OutlierDetector.detect(
        frame, config.outlier_method, config, "FAT/TAIL"
    )

    assert len(out) == FLOOR_BARS, "the window must be admitted whole"
    flagged = [i for i in issues if i.issue_type == "outliers_detected"]
    assert flagged, (
        "admitting the window must not mean reporting nothing -- the contamination is the "
        "evidence that makes a bounded guarantee auditable"
    )


def test_the_admitted_window_is_returned_unmodified():
    """No winsorising, no exclusion, no fabricated price."""
    closes = _fat_tailed(FLOOR_BARS, seed=11)
    frame = _frame(closes)

    out, _ = OutlierDetector.detect(
        frame, ValidationConfig().outlier_method, ValidationConfig(), "FAT/TAIL"
    )

    assert len(out) == len(frame)
    np.testing.assert_array_equal(out["close"].to_numpy(), frame["close"].to_numpy())


# ══════════════════════════════════════════════════════════════════════════════
#  2. The fraction budget is a real gate, not decoration
# ══════════════════════════════════════════════════════════════════════════════


def test_contamination_above_the_budget_is_refused():
    """A feed whose extreme-move rate has changed materially is still refused."""
    frame = _frame(_fat_tailed(2_000, seed=3))
    config = ValidationConfig(max_outlier_fraction=0.0)

    with pytest.raises(DataValidationError, match="contamination budget"):
        OutlierDetector.detect(frame, config.outlier_method, config, "FAT/TAIL")


def test_the_refusal_states_the_count_the_sample_and_the_budget():
    """An operator must be able to tell 'slightly over' from 'wildly over'."""
    frame = _frame(_fat_tailed(2_000, seed=3))
    config = ValidationConfig(max_outlier_fraction=0.0)

    with pytest.raises(DataValidationError) as caught:
        OutlierDetector.detect(frame, config.outlier_method, config, "FAT/TAIL")

    message = str(caught.value)
    assert "log-returns" in message and "budget" in message
    assert "FAT/TAIL" in message, "the refusal must name the instrument (DATA-03)"


def test_the_reported_issue_carries_the_numbers_not_just_a_sentence():
    frame = _frame(_fat_tailed(FLOOR_BARS, seed=7))
    config = ValidationConfig()

    _, issues = OutlierDetector.detect(
        frame, config.outlier_method, config, "FAT/TAIL"
    )

    details = next(i.details for i in issues if i.issue_type == "outliers_detected")
    assert details["outlier_count"] >= 1
    assert details["sample_size"] == FLOOR_BARS - 1
    assert 0.0 < details["outlier_fraction"] <= details["max_outlier_fraction"]
    assert details["method"] == OutlierMethod.ROBUST_Z_SCORE.value
    assert details["policy"] == OutlierPolicy.FLAG_AND_ADMIT.value
    assert details["threshold"] == ROBUST_Z_SCORE_THRESHOLD_DEFAULT
    assert details["flagged_timestamps"], "which bars were flagged must be recoverable"


# ══════════════════════════════════════════════════════════════════════════════
#  3. The magnitude ceiling: DATA-02's requirement survives DATA-03
# ══════════════════════════════════════════════════════════════════════════════


def test_a_single_impossible_tick_still_refuses_a_floor_sized_window():
    """The case a fraction budget CANNOT catch: 1 bad bar in 10,000 is 0.01%."""
    closes = _fat_tailed(FLOOR_BARS, seed=5)
    closes[FLOOR_BARS // 2] *= 50  # DATA-02's own fixture shape
    frame = _frame(closes)
    config = ValidationConfig()

    with pytest.raises(DataValidationError, match="implausible single-bar move"):
        OutlierDetector.detect(frame, config.outlier_method, config, "SPIKE/USDT")


def test_the_ceiling_refusal_reports_the_move_and_the_limit():
    closes = _fat_tailed(1_000, seed=5)
    closes[500] *= 50
    frame = _frame(closes)

    with pytest.raises(DataValidationError) as caught:
        OutlierDetector.detect(
            frame, ValidationConfig().outlier_method, ValidationConfig(), "SPIKE/USDT"
        )

    message = str(caught.value)
    assert "SPIKE/USDT" in message
    assert "outlier" in message.lower(), (
        "downstream classifiers read this text to route the refusal as a data-quality "
        "failure; tests/test_feed_state_and_data_quality.py asserts on it"
    )
    assert "x move" in message, "the multiple is what makes the number legible"


def test_the_ceiling_is_far_above_any_real_move_and_far_below_a_fabricated_one():
    """The constant's own justification, pinned so a future edit cannot quietly break it.

    Measured on 36,000 live bars (BTC/ETH/DOGE): the largest real single-bar |log-return|
    was 0.0048..0.0084. DATA-02's spike fixtures sit at 3.91 (50x) and 7.05 (1148x).
    """
    largest_real_observed = 0.0084
    smallest_fabricated_fixture = 3.9116

    assert MAX_BAR_LOG_MOVE_DEFAULT > largest_real_observed * 10, (
        "the ceiling must not sit near ordinary volatility -- crypto flash crashes do tens "
        "of percent in a minute and all of those must still pass"
    )
    assert MAX_BAR_LOG_MOVE_DEFAULT < smallest_fabricated_fixture, (
        "the ceiling must still catch DATA-02's 50x spike"
    )
    assert MAX_BAR_LOG_MOVE_DEFAULT == pytest.approx(math.log(2), rel=1e-9)


def test_the_ceiling_does_not_apply_to_volume():
    """Volume routinely doubles between adjacent bars; that is trading, not a bad tick."""
    n = 500
    volumes = np.full(n, 100.0)
    volumes[250] = 100.0 * 1_000  # a 1000x volume burst
    frame = _frame(_fat_tailed(n, seed=2), volumes=volumes)

    # Must not raise on the ceiling. Use a budget wide enough that the fraction gate is not
    # what is under test here.
    config = ValidationConfig(max_outlier_fraction=1.0)
    OutlierDetector.detect(frame, config.outlier_method, config, "VOL/USDT")


def test_a_flash_crash_sized_move_is_still_admitted():
    """A real 30% one-bar move is volatility, not corruption, and must survive."""
    n = 2_000
    closes = _fat_tailed(n, seed=9)
    closes[n // 2:] *= 0.70  # a 30% step down, sustained (not a single-bar round trip)
    frame = _frame(closes)

    config = ValidationConfig(max_outlier_fraction=1.0)
    OutlierDetector.detect(frame, config.outlier_method, config, "CRASH/USDT")


# ══════════════════════════════════════════════════════════════════════════════
#  4. (C) the estimator, and why mean/std was the wrong one
# ══════════════════════════════════════════════════════════════════════════════


def test_the_robust_scale_is_not_inflated_by_the_points_it_judges():
    """The masking effect, demonstrated: std moves with the outliers, MAD does not.

    Measured on live data, std / (1.4826 * MAD) was 1.38..1.49 on every instrument, so every
    ordinary point's classic z-score was deflated by ~40%.
    """
    returns = pd.Series(np.random.default_rng(4).standard_normal(1_000) * 0.001)
    clean_std = returns.std(ddof=0)
    clean_mad = (returns - returns.median()).abs().median() * MAD_TO_SIGMA

    contaminated = returns.copy()
    contaminated.iloc[:5] = 0.5  # five extreme points

    dirty_std = contaminated.std(ddof=0)
    dirty_mad = (contaminated - contaminated.median()).abs().median() * MAD_TO_SIGMA

    assert dirty_std > clean_std * 5, "std is dragged by five points out of a thousand"
    assert dirty_mad == pytest.approx(clean_mad, rel=0.10), (
        "the robust scale must barely move -- that is the whole point of (C)"
    )


def test_the_robust_score_finds_an_extreme_the_classic_score_masks():
    """Why this is a correctness fix and not a threshold tweak."""
    rng = np.random.default_rng(13)
    returns = pd.Series(rng.standard_normal(1_000) * 0.001)
    returns.iloc[:40] = 0.02  # a cluster of extremes that inflates std
    returns.iloc[500] = 0.009  # a real extreme, hidden by that inflation

    classic = abs((returns.iloc[500] - returns.mean()) / returns.std(ddof=0))
    robust = OutlierDetector._robust_z_scores(returns).iloc[500]

    assert robust > classic, (
        "the classic score is deflated by the very cluster it should have caught, so the "
        "genuinely extreme point at index 500 scores LOWER than it deserves"
    )


def test_a_zero_mad_flags_nothing_rather_than_dividing_by_zero():
    """A flat or halted series has no dispersion to be extreme relative to.

    That is a staleness problem for GapHandler and _validate_row_count, not something the
    outlier detector should reinterpret -- and it must certainly not raise ZeroDivisionError.
    """
    flat = pd.Series([0.0] * 100)

    scores = OutlierDetector._robust_z_scores(flat)

    assert (scores == 0).all()
    assert np.isfinite(scores).all()


# ══════════════════════════════════════════════════════════════════════════════
#  5. The defaults are the decision, and REJECT_WINDOW is still reachable
# ══════════════════════════════════════════════════════════════════════════════


def test_the_shipped_defaults_are_a_plus_c():
    config = ValidationConfig()
    assert config.outlier_method == OutlierMethod.ROBUST_Z_SCORE
    assert config.outlier_policy == OutlierPolicy.FLAG_AND_ADMIT
    assert config.max_outlier_fraction == MAX_OUTLIER_FRACTION_DEFAULT


def test_the_budget_leaves_real_headroom_over_the_measured_worst_case():
    """0.16% worst observed at the default threshold, against a 1% budget.

    Pinned because the pair (threshold, budget) is only meaningful together: raising the
    threshold without lowering the budget would quietly turn the gate off.
    """
    measured_worst_case_fraction = 0.0016
    assert MAX_OUTLIER_FRACTION_DEFAULT > measured_worst_case_fraction * 3, (
        "too tight a budget would refuse ordinary data and reintroduce the defect"
    )
    assert MAX_OUTLIER_FRACTION_DEFAULT <= 0.02, (
        "too loose a budget would admit a materially degraded feed"
    )


def test_the_strict_policy_is_still_available_and_unchanged():
    """The change is opt-out, not irreversible."""
    frame = _frame(_fat_tailed(1_000, seed=1))
    strict = ValidationConfig(
        outlier_method=OutlierMethod.Z_SCORE,
        outlier_policy=OutlierPolicy.REJECT_WINDOW,
    )

    with pytest.raises(DataValidationError) as caught:
        OutlierDetector.detect(frame, strict.outlier_method, strict, "STRICT/USDT")

    assert "Dataset rejected - no synthetic data allowed." in str(caught.value), (
        "the legacy message is part of the legacy behaviour and is preserved verbatim"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  6. End to end, through the real pipeline
# ══════════════════════════════════════════════════════════════════════════════


def test_the_full_validator_admits_a_floor_sized_window_and_reports_the_count():
    frame = _frame(_fat_tailed(FLOOR_BARS, seed=21))

    cleaned, report = asyncio.run(
        MarketDataValidator(ValidationConfig()).validate(frame, "FAT/TAIL", "1m")
    )

    assert len(cleaned) == FLOOR_BARS
    assert report.outliers_detected >= 1, (
        "outliers_detected counted ISSUE RECORDS, not outliers, so it could only ever "
        "report 0 or 1 per column -- a field that did not hold what its name says"
    )
    assert report.quality_score >= 70.0, (
        "a window admitted under the budget must not then fail the quality gate, or the "
        "floor is still unreachable by another route"
    )


def test_the_full_validator_still_refuses_an_impossible_tick_end_to_end():
    closes = _fat_tailed(FLOOR_BARS, seed=22)
    closes[123] *= 50
    frame = _frame(closes)

    with pytest.raises(DataValidationError):
        asyncio.run(
            MarketDataValidator(ValidationConfig()).validate(frame, "SPIKE/USDT", "1m")
        )
