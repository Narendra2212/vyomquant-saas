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
