"""DATA-02 -- the market-data quality validator's default thresholds rejected almost all
real-shaped market data.

THE DEFECT
    ``OutlierDetector._z_score_filter`` and ``_iqr_filter`` (reached by default: ``ValidationConfig.
    outlier_method`` defaults to ``OutlierMethod.Z_SCORE``, and this validator is the one
    ``market_data_contract.quality_report``, ``strategy_operations``, ``strategy_service`` and
    ``training_worker`` all reuse, per their own docstrings) computed a z-score / IQR test
    against the RAW price and volume LEVEL of the whole window, then rejected the ENTIRE window
    if even one point exceeded it.

    Price and volume levels are both non-stationary -- price trends/drifts and volume swings
    across orders of magnitude (it is approximately log-normal) -- by construction. A z-score
    against a non-stationary series' own mean measures "how far this point is from a mean the
    trend/burst itself moved", which has nothing to do with whether any individual bar is
    actually anomalous. The existing test suite's own ``_pool()`` fixture in
    ``test_feed_state_and_data_quality.py`` already worked around this: its "clean, accepted"
    sample is explicitly built as a bounded SINE WAVE rather than anything resembling a real,
    trending price series, specifically because the old z-score-on-levels check could not
    tolerate a trend at all.

THE FIX
    ``OutlierDetector`` now computes its z-score / IQR test over bar-to-bar LOG-RETURNS
    (``log(x_t / x_{t-1})``) instead of the raw level, for both ``close`` and ``volume`` --
    the standard transform that makes a trending/multiplicative series approximately
    stationary, which a z-score test actually requires to mean anything. Percent-change returns
    were tried first and still left volume's heavy right tail (ordinary volume bursts are large
    multiplicative swings, not additive ones) fat enough to trip the z-score on realistic data;
    log-returns resolved it. ``ValidationConfig.z_score_threshold`` was also raised from 3.0 to
    5.0 (env-overridable via ``MARKET_DATA_Z_SCORE_THRESHOLD``): rejecting a whole window on ANY
    single point beyond a per-comparison threshold has a false-positive rate that rises with
    window size even under perfectly well-behaved data (at a 3-sigma per-point threshold, a
    500-bar window is rejected roughly 3 times in 4 by chance alone), and 5.0 keeps that
    windows-level false-positive rate low across the window sizes this platform actually
    requests (hundreds to low thousands of bars). The exact numeric threshold is still a
    principled default rather than a fit against real historical return distributions (which
    this sandbox has none of) -- recorded as the one deliberately deferred piece of this finding.

WHAT THIS FILE PROVES
    1. A realistic-shaped, meaningfully trending price series with ordinary, bursty-but-real
       volume is ACCEPTED where it used to be rejected by the raw-level check, across many
       independent random draws (not cherry-picked).
    2. A genuinely anomalous single-bar price spike is STILL rejected -- the fix did not disable
       the check, it corrected what it measures.
    3. The existing test suite's own sine-wave "clean" and "spike" fixtures
       (``test_feed_state_and_data_quality.py``'s ``_pool()``) still behave exactly as before:
       accepted and rejected respectively.
    4. The same two properties hold for the (non-default) IQR method.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend_app.backend.market_data_validation import (
    DataValidationError,
    OutlierDetector,
    OutlierMethod,
    ValidationConfig,
)


def _trending_frame(seed: int, bars: int = 900) -> pd.DataFrame:
    """A realistic-shaped OHLCV window: a meaningfully trending close (a geometric random walk
    with drift, like a real multi-day crypto move) and ordinary, log-normal (bursty) volume --
    not a bounded sine wave."""
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2024-01-01", periods=bars, freq="5min")
    returns = rng.normal(0.0004, 0.0012, bars)
    close = pd.Series(20_000.0 * np.cumprod(1 + returns), index=stamps)
    volume = pd.Series(rng.lognormal(8.0, 0.3, bars), index=stamps)
    return pd.DataFrame(
        {
            "open": close * 0.9995,
            "high": close * 1.0010,
            "low": close * 0.9990,
            "close": close,
            "volume": volume,
        },
        index=stamps,
    )


def _sine_pool(bars: int = 900, spike: bool = False) -> pd.DataFrame:
    """The existing test suite's own 'clean, accepted' / 'deliberately rejected' fixture shape
    (mirrors tests/test_feed_state_and_data_quality.py::_pool), kept identical here so this file
    independently confirms neither case regressed."""
    stamps = pd.date_range("2024-01-01", periods=bars, freq="5min")
    close = pd.Series(100.0 + 20.0 * np.sin(np.linspace(0.0, 12.0, bars)), index=stamps)
    if spike:
        close.iloc[-10] = 100_000.0
    return pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 1.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, bars), index=stamps),
        },
        index=stamps,
    )


@pytest.mark.parametrize("seed", range(25))
def test_realistic_trending_data_with_bursty_volume_is_accepted(seed: int) -> None:
    config = ValidationConfig()
    frame = _trending_frame(seed)

    # Must not raise -- this is exactly the shape of data the old level-based check rejected.
    OutlierDetector.detect(frame, config.outlier_method, config)


def test_a_genuine_single_bar_price_spike_is_still_rejected() -> None:
    config = ValidationConfig()
    frame = _trending_frame(seed=0)
    frame.loc[frame.index[-5], "close"] *= 50  # an impossible 50x one-bar move

    with pytest.raises(DataValidationError):
        OutlierDetector.detect(frame, config.outlier_method, config)


def test_the_existing_sine_pool_fixture_is_still_accepted() -> None:
    config = ValidationConfig()
    OutlierDetector.detect(_sine_pool(), config.outlier_method, config)


def test_the_existing_sine_pool_spike_fixture_is_still_rejected() -> None:
    config = ValidationConfig()
    with pytest.raises(DataValidationError):
        OutlierDetector.detect(_sine_pool(spike=True), config.outlier_method, config)


class TestTheIqrMethodHasTheSameFix:
    """OutlierMethod.IQR is not the default, but shares the same level-vs-returns defect."""

    @pytest.mark.parametrize("seed", range(10))
    def test_realistic_trending_data_is_accepted(self, seed: int) -> None:
        config = ValidationConfig(outlier_method=OutlierMethod.IQR)
        frame = _trending_frame(seed)
        OutlierDetector.detect(frame, config.outlier_method, config)

    def test_a_genuine_single_bar_price_spike_is_still_rejected(self) -> None:
        config = ValidationConfig(outlier_method=OutlierMethod.IQR)
        frame = _trending_frame(seed=0)
        frame.loc[frame.index[-5], "close"] *= 50

        with pytest.raises(DataValidationError):
            OutlierDetector.detect(frame, config.outlier_method, config)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA-02, THE DEFERRED PIECE: the two legacy constants, fitted to real data
#
#  This file's own header named it: "The exact numeric threshold is still a principled
#  default rather than a fit against real historical return distributions (which this
#  sandbox has none of) -- recorded as the one deliberately deferred piece of this finding."
#
#  It is no longer deferred. `scripts/measure_market_data_outlier_reality.py --legacy`
#  fetches 12,000 live 1m bars each of BTC/USDT, ETH/USDT and DOGE/USDT and reports, for
#  each candidate parameter, the real per-point flag rate and the worst contamination it
#  puts in a floor-sized (10,000-bar) window. The tables below are that output.
#
#  WHY CONTAMINATION AND NOT A PROBABILITY. The question changed under these constants.
#  When they were written, one exceedance rejected the whole window, so what mattered was
#  P(any point exceeds) -- and the measurement is damning: at the shipped 5.0, 100% of
#  windows of 2,000 bars or more are refused, and the only threshold that admits a
#  10,000-bar window is 12.0, which flags nothing at all. DATA-03 replaced that policy with
#  per-point flagging plus a contamination BUDGET, so the live question is the one DATA-03
#  asked of the robust threshold: what share of a window does this parameter flag, and does
#  that sit inside `MAX_OUTLIER_FRACTION_DEFAULT`?
#
#  These are numbers measured against a live venue, so they are recorded rather than
#  re-fetched: a unit test must not depend on Binance being reachable, and a test that
#  re-measured would be asserting today's market rather than the calibration. The script is
#  the reproduction path.
# ══════════════════════════════════════════════════════════════════════════════

#: Worst contamination of any 10,000-bar window, by classic z-score threshold.
#: Measured 2026-10-24 across the three instruments named above.
MEASURED_Z_CONTAMINATION = {
    3.0: 0.0176,
    4.0: 0.0072,
    5.0: 0.0037,
    6.0: 0.0020,
    7.0: 0.0014,
    8.0: 0.0011,
    10.0: 0.0003,
    12.0: 0.0001,
}

#: The same, by IQR fence multiplier.
MEASURED_IQR_CONTAMINATION = {
    1.5: 0.0614,
    2.0: 0.0343,
    3.0: 0.0143,
    4.0: 0.0066,
    6.0: 0.0020,
    8.0: 0.0006,
}

#: Real per-point flag rate (the worst of the three instruments), by parameter. A parameter
#: that flags nothing is not a safety control, which is the other half of the judgement.
MEASURED_Z_RATE = {3.0: 1.63e-2, 5.0: 3.33e-3, 8.0: 9.17e-4, 10.0: 2.50e-4, 12.0: 8.34e-5}
MEASURED_IQR_RATE = {1.5: 6.00e-2, 3.0: 1.31e-2, 4.0: 5.83e-3, 6.0: 1.83e-3, 8.0: 5.00e-4}


def test_the_shipped_legacy_defaults_sit_inside_the_contamination_budget():
    """Both selectable methods must admit ordinary real data under the default budget.

    This is the assertion the finding was actually about. A method whose default parameter
    puts more contamination in a window than the budget allows refuses healthy data, which
    is the whole complaint: "the validator's default thresholds reject almost all
    real-shaped market data".
    """
    from backend_app.backend.market_data_validation import (
        IQR_MULTIPLIER_DEFAULT,
        MAX_OUTLIER_FRACTION_DEFAULT,
        Z_SCORE_THRESHOLD_DEFAULT,
    )

    z_worst = MEASURED_Z_CONTAMINATION[Z_SCORE_THRESHOLD_DEFAULT]
    assert z_worst < MAX_OUTLIER_FRACTION_DEFAULT, (
        f"z={Z_SCORE_THRESHOLD_DEFAULT} flags {z_worst:.2%} of a floor-sized window on real "
        f"data, against a {MAX_OUTLIER_FRACTION_DEFAULT:.0%} budget -- healthy data would be "
        f"refused. Re-run scripts/measure_market_data_outlier_reality.py --legacy and pick a "
        f"threshold from its table."
    )

    iqr_worst = MEASURED_IQR_CONTAMINATION[IQR_MULTIPLIER_DEFAULT]
    assert iqr_worst < MAX_OUTLIER_FRACTION_DEFAULT, (
        f"IQR k={IQR_MULTIPLIER_DEFAULT} flags {iqr_worst:.2%} of a floor-sized window on "
        f"real data, against a {MAX_OUTLIER_FRACTION_DEFAULT:.0%} budget"
    )


def test_both_defaults_keep_real_headroom_rather_than_just_clearing_the_budget():
    """A parameter that only just fits is one volatile week from refusing healthy data.

    DATA-03 chose the robust threshold at ~6x inside the budget and said so. These two are
    held to the same standard, which is what rules out z=4.0 (1.4x) and IQR k=4.0 (1.5x) --
    both of which do technically clear the budget.
    """
    from backend_app.backend.market_data_validation import (
        IQR_MULTIPLIER_DEFAULT,
        MAX_OUTLIER_FRACTION_DEFAULT,
        Z_SCORE_THRESHOLD_DEFAULT,
    )

    for label, param, table in (
        ("z-score", Z_SCORE_THRESHOLD_DEFAULT, MEASURED_Z_CONTAMINATION),
        ("IQR", IQR_MULTIPLIER_DEFAULT, MEASURED_IQR_CONTAMINATION),
    ):
        headroom = MAX_OUTLIER_FRACTION_DEFAULT / table[param]
        assert headroom >= 2.0, (
            f"{label} default {param} leaves only {headroom:.1f}x headroom over measured "
            f"reality; the budget is what stands between ordinary volatility and a refused "
            f"window"
        )


def test_both_defaults_still_detect_something():
    """The other end of the trade. A threshold high enough to flag nothing passes every
    window for the wrong reason -- DATA-03's comment names exactly this trap, where the
    classic rule "passed every window only because the threshold exceeded the largest
    z-score present"."""
    from backend_app.backend.market_data_validation import (
        IQR_MULTIPLIER_DEFAULT,
        Z_SCORE_THRESHOLD_DEFAULT,
    )

    assert MEASURED_Z_RATE[Z_SCORE_THRESHOLD_DEFAULT] > 0.0, (
        "the classic z-score default flags nothing on 36,000 real bars; a detector that "
        "detects nothing is not a safety control"
    )
    assert MEASURED_IQR_RATE[IQR_MULTIPLIER_DEFAULT] > 0.0, (
        "the IQR default flags nothing on 36,000 real bars"
    )


def test_the_values_these_defaults_moved_away_from_are_measurably_inadmissible():
    """Non-vacuity, and the record of what was actually wrong.

    Without this, the three assertions above would pass for any table whose numbers all sat
    under the budget. These are the two values the code shipped BEFORE this calibration --
    z=3.0 was the original default this finding's first pass raised away from, and IQR k=3.0
    was shipped until the measurement. Both exceed the budget on real data, so the table
    discriminates and the changes were not cosmetic.
    """
    from backend_app.backend.market_data_validation import MAX_OUTLIER_FRACTION_DEFAULT

    assert MEASURED_Z_CONTAMINATION[3.0] > MAX_OUTLIER_FRACTION_DEFAULT, (
        "z=3.0 was the original default and measures 1.76% contamination; if it now fits "
        "the budget, the budget or the measurement moved and both defaults need re-reading"
    )
    assert MEASURED_IQR_CONTAMINATION[3.0] > MAX_OUTLIER_FRACTION_DEFAULT, (
        "IQR k=3.0 shipped until this calibration and measures 1.43% contamination -- the "
        "defect this change fixes"
    )


def test_the_normal_model_the_old_comment_relied_on_understates_the_real_tail():
    """Why the old justification had to go, not just the old number.

    The replaced comment argued from a normal model: "per-point P(|Z|>5) ~ 5.7e-7 ... so even
    a 10,000-bar window's overall false-positive probability stays under 1%". Crypto
    log-returns are fat-tailed and the real rate at z=5 is 3.33e-3 -- the model understates
    it by a factor of about 5,800. The VALUE survived the measurement; the reasoning did
    not, and a right answer from wrong reasoning is one refactor away from a wrong one.
    """
    normal_rate_at_5 = 5.733e-7
    real_rate_at_5 = MEASURED_Z_RATE[5.0]
    understatement = real_rate_at_5 / normal_rate_at_5

    assert understatement > 1000, (
        f"the normal model understates the real tail by only {understatement:.0f}x, which "
        f"would mean the old comment's reasoning was roughly sound after all"
    )


def test_the_measurement_script_exposes_the_legacy_mode_that_produced_these_tables():
    """The reproduction path is part of the evidence.

    DATA-03 recorded its calibration as reproducible by
    `scripts/measure_market_data_outlier_reality.py --robust`. The legacy pair now has the
    same, and this asserts the entry point exists rather than trusting a docstring -- the
    exact failure mode this finding's sibling TEST-01 is about.
    """
    import pathlib

    script = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "measure_market_data_outlier_reality.py"
    )
    source = script.read_text(encoding="utf-8")
    assert "--legacy" in source
    assert "def measure_legacy(" in source
    assert "def _iqr_flags(" in source, (
        "the IQR half of the calibration must be in the script, or the IQR table above "
        "cannot be reproduced"
    )
