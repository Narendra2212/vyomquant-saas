"""
tests/test_ml_dataset.py

Unit tests for backend_app/backend/ml_dataset.py — the temporal splits and
supervised dataset builder from design.md § Feature engineering, "Data leakage
protection" mechanisms 2 and 3.

Covers:
  - embargo arithmetic: the floor is feature lookback + label horizon, an
    explicit embargo below it is refused, and a missing one is derived
  - disjointness and chronological ordering of train / val / test
  - the ranges are ranges, not index arrays: there is nothing to shuffle
  - rows(X) == len(y) at horizon 1 and above
  - the trailing horizon rows are dropped rather than labelled
  - backward-only features, forward-only labels

Requirements 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.11.
"""
import numpy as np
import pytest

from backend_app.backend.feature_engineering import FeatureMatrix
from backend_app.backend.ml_dataset import (
    CLASS_DOWN,
    CLASS_FLAT,
    CLASS_UP,
    InsufficientEmbargoError,
    LabelMode,
    SplitRange,
    SupervisedDatasetError,
    TemporalSplitError,
    TemporalSplits,
    build_supervised_dataset,
    make_temporal_splits,
    required_embargo_bars,
    splits_for_dataset,
)


def _index(n: int, start: int = 0, step: int = 60_000) -> np.ndarray:
    """A strictly increasing millisecond timestamp index."""
    return np.arange(start, start + n * step, step, dtype="int64")


def _matrix(n_rows: int, n_cols: int = 3, warmup: int = 0) -> FeatureMatrix:
    values = np.arange(n_rows * n_cols, dtype=float).reshape(n_rows, n_cols)
    return FeatureMatrix(
        index=_index(n_rows),
        columns=[f"f{i}" for i in range(n_cols)],
        values=values,
        warmup_offset=warmup,
        column_warmup={f"f{i}": warmup for i in range(n_cols)},
    )


# ══════════════════════════════════════════════════════════════════════════
#  EMBARGO ARITHMETIC  (Requirement 18.6)
# ══════════════════════════════════════════════════════════════════════════


def test_required_embargo_is_lookback_plus_horizon():
    assert required_embargo_bars(20, 5) == 25
    assert required_embargo_bars(0, 1) == 1
    assert required_embargo_bars(200, 3) == 203


def test_required_embargo_rejects_zero_horizon():
    # A horizon of 0 is not a prediction, so it cannot set an embargo floor.
    with pytest.raises(TemporalSplitError, match="label_horizon must be >= 1"):
        required_embargo_bars(10, 0)


def test_required_embargo_rejects_negative_lookback():
    with pytest.raises(TemporalSplitError, match="feature_lookback must be >= 0"):
        required_embargo_bars(-1, 5)


def test_embargo_derived_when_not_supplied():
    splits = make_temporal_splits(
        _index(1000), 0.15, 0.15, None, feature_lookback=20, label_horizon=5
    )
    assert splits.embargo_bars == 25


def test_explicit_embargo_below_floor_is_refused():
    # 24 < 20 + 5. Refused, not silently raised, so the caller learns their
    # configuration was wrong rather than having it quietly corrected.
    with pytest.raises(InsufficientEmbargoError) as exc:
        make_temporal_splits(
            _index(1000), 0.15, 0.15, 24, feature_lookback=20, label_horizon=5
        )
    assert "24" in str(exc.value)
    assert "25" in str(exc.value)


def test_explicit_embargo_at_floor_is_accepted():
    splits = make_temporal_splits(
        _index(1000), 0.15, 0.15, 25, feature_lookback=20, label_horizon=5
    )
    assert splits.embargo_bars == 25


def test_explicit_embargo_above_floor_is_accepted():
    splits = make_temporal_splits(
        _index(1000), 0.15, 0.15, 60, feature_lookback=20, label_horizon=5
    )
    assert splits.embargo_bars == 60


def test_embargo_required_when_no_floor_inputs():
    with pytest.raises(TemporalSplitError, match="embargo_bars is required"):
        make_temporal_splits(_index(1000), 0.15, 0.15, None)


def test_lookback_and_horizon_must_be_supplied_together():
    with pytest.raises(TemporalSplitError, match="must be supplied together"):
        make_temporal_splits(_index(1000), 0.15, 0.15, 30, feature_lookback=20)


def test_gaps_equal_the_effective_embargo():
    splits = make_temporal_splits(_index(1000), 0.2, 0.2, 30)
    assert splits.gap_train_val == 30
    assert splits.gap_val_test == 30


# ══════════════════════════════════════════════════════════════════════════
#  DISJOINTNESS AND CHRONOLOGICAL ORDER  (Requirement 18.5)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("n", [500, 1000, 2001, 5000])
@pytest.mark.parametrize("embargo", [0, 1, 10, 50])
def test_splits_are_disjoint_and_chronological(n, embargo):
    splits = make_temporal_splits(_index(n), 0.15, 0.15, embargo)

    # Chronological: every range starts at or after the previous one ends.
    assert splits.train.start == 0
    assert splits.train.stop <= splits.val.start
    assert splits.val.stop <= splits.test.start
    assert splits.test.stop == n

    # Disjoint: no row belongs to two splits.
    train_rows = set(range(splits.train.start, splits.train.stop))
    val_rows = set(range(splits.val.start, splits.val.stop))
    test_rows = set(range(splits.test.start, splits.test.stop))
    assert not train_rows & val_rows
    assert not val_rows & test_rows
    assert not train_rows & test_rows

    # Embargoed by at least the configured gap.
    assert splits.gap_train_val >= embargo
    assert splits.gap_val_test >= embargo

    # Every split holds rows.
    assert len(splits.train) > 0
    assert len(splits.val) > 0
    assert len(splits.test) > 0


def test_split_timestamps_are_strictly_ordered_across_splits():
    n = 1000
    index = _index(n)
    splits = make_temporal_splits(index, 0.15, 0.15, 20)

    train_ts = splits.train.take(index)
    val_ts = splits.val.take(index)
    test_ts = splits.test.take(index)

    # Every training timestamp precedes every validation timestamp, and so on.
    assert train_ts.max() < val_ts.min()
    assert val_ts.max() < test_ts.min()


def test_overlapping_split_geometry_is_refused_at_construction():
    # A hand-built TemporalSplits cannot claim an overlap is fine.
    with pytest.raises(TemporalSplitError, match="train and val overlap"):
        TemporalSplits(
            train=SplitRange(0, 60),
            val=SplitRange(50, 80),
            test=SplitRange(80, 100),
            embargo_bars=0,
            total_rows=100,
        )


def test_gap_smaller_than_embargo_is_refused_at_construction():
    with pytest.raises(TemporalSplitError, match=r"train->val gap is 5"):
        TemporalSplits(
            train=SplitRange(0, 60),
            val=SplitRange(65, 80),
            test=SplitRange(90, 100),
            embargo_bars=10,
            total_rows=100,
        )


def test_empty_split_is_refused():
    # A huge embargo eats the training split; that is an error, not a silent
    # zero-row train set.
    with pytest.raises(TemporalSplitError, match="train split is empty"):
        make_temporal_splits(_index(100), 0.15, 0.15, 500)


def test_fractions_must_leave_a_train_split():
    with pytest.raises(TemporalSplitError, match="non-empty train split"):
        make_temporal_splits(_index(1000), 0.6, 0.5, 10)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_fractions_out_of_range_are_refused(bad):
    with pytest.raises(TemporalSplitError, match="out of range"):
        make_temporal_splits(_index(1000), bad, 0.15, 10)


def test_non_monotonic_index_is_refused():
    index = _index(100)
    index[50] = index[49]  # duplicate timestamp breaks strict increase
    with pytest.raises(TemporalSplitError, match="strictly increasing"):
        make_temporal_splits(index, 0.15, 0.15, 5)


def test_negative_embargo_is_refused():
    with pytest.raises(TemporalSplitError, match="embargo_bars must be >= 0"):
        make_temporal_splits(_index(1000), 0.15, 0.15, -1)


def test_split_sizes_report_row_counts():
    splits = make_temporal_splits(_index(1000), 0.15, 0.15, 10)
    sizes = splits.sizes
    assert sizes["train"] == len(splits.train)
    assert sizes["val"] == len(splits.val)
    assert sizes["test"] == len(splits.test)
    # Sizes plus the two embargo gaps account for every row.
    assert sum(sizes.values()) + splits.gap_train_val + splits.gap_val_test == 1000


# ══════════════════════════════════════════════════════════════════════════
#  RANGES, NOT PERMUTATIONS  (Requirement 18.11)
# ══════════════════════════════════════════════════════════════════════════


def test_split_range_exposes_no_permutable_member_list():
    """
    The anti-shuffle mechanism: a caller who wants to shuffle a split has
    nothing to shuffle. SplitRange names its bounds and refuses to materialise
    its members, so chronological order is a property of the type rather than
    of a convention.
    """
    rng = SplitRange(10, 20)
    for shuffle_affordance in ("indices", "tolist", "to_list", "__iter__", "__getitem__"):
        assert not hasattr(rng, shuffle_affordance), (
            f"SplitRange.{shuffle_affordance} would hand back a permutable "
            f"object and reintroduce the possibility the type exists to remove"
        )
    with pytest.raises(TypeError):
        list(rng)  # type: ignore[call-overload]


def test_split_range_take_returns_contiguous_rows_in_order():
    data = np.arange(100, dtype=float)
    rng = SplitRange(10, 20)
    taken = rng.take(data)
    assert np.array_equal(taken, np.arange(10, 20, dtype=float))
    # Contiguous and ascending, by construction.
    assert np.all(np.diff(taken) > 0)


def test_split_range_is_frozen():
    rng = SplitRange(10, 20)
    with pytest.raises(Exception):
        rng.start = 0  # type: ignore[misc]


def test_split_range_rejects_backwards_and_negative():
    with pytest.raises(TemporalSplitError, match="must not run backwards"):
        SplitRange(20, 10)
    with pytest.raises(TemporalSplitError, match="must be >= 0"):
        SplitRange(-1, 10)


def test_split_range_take_refuses_to_read_past_the_array():
    with pytest.raises(TemporalSplitError, match="exceeds array length"):
        SplitRange(90, 110).take(np.arange(100))


def test_split_range_emptiness_and_truthiness():
    assert not SplitRange(5, 5)
    assert SplitRange(5, 5).is_empty
    assert SplitRange(5, 6)
    assert not SplitRange(5, 6).is_empty


def test_split_range_as_slice_matches_take():
    data = np.arange(50, dtype=float)
    rng = SplitRange(7, 19)
    assert np.array_equal(data[rng.as_slice()], rng.take(data))


# ══════════════════════════════════════════════════════════════════════════
#  rows(X) == len(y)  (Requirements 18.9, 18.10) — the ML-1 invariant
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("horizon", [1, 2, 3, 5, 13, 60])
def test_rows_x_equals_len_y_across_horizons(horizon):
    n = 500
    features = _matrix(n)
    prices = np.linspace(100.0, 200.0, n)

    dataset = build_supervised_dataset(features, prices, horizon)

    assert dataset.X.shape[0] == dataset.y.shape[0]
    assert dataset.X.shape[0] == len(dataset.index)
    assert dataset.X.shape[0] == n - horizon


@pytest.mark.parametrize("horizon", [1, 4, 25])
@pytest.mark.parametrize("warmup", [0, 1, 30, 200])
def test_rows_x_equals_len_y_with_warmup(horizon, warmup):
    n = 500
    features = _matrix(n, warmup=warmup)
    prices = np.linspace(100.0, 200.0, n)

    dataset = build_supervised_dataset(features, prices, horizon)

    assert dataset.X.shape[0] == dataset.y.shape[0]
    assert dataset.X.shape[0] == n - warmup - horizon
    assert dataset.dropped_warmup_rows == warmup
    assert dataset.dropped_trailing_rows == horizon


def test_horizon_one_keeps_all_but_the_last_row():
    # The narrowest case, and the one the ML-1 off-by-one hid in.
    n = 10
    features = _matrix(n, n_cols=2)
    prices = np.arange(1.0, n + 1.0)

    dataset = build_supervised_dataset(features, prices, 1)

    assert dataset.X.shape[0] == 9
    assert dataset.y.shape[0] == 9
    assert np.array_equal(dataset.index, features.index[:9])


def test_horizon_below_one_is_refused():
    features = _matrix(50)
    prices = np.linspace(1.0, 2.0, 50)
    for bad in (0, -1):
        with pytest.raises(SupervisedDatasetError, match="horizon must be >= 1"):
            build_supervised_dataset(features, prices, bad)


def test_supervised_dataset_refuses_mismatched_lengths_at_construction():
    from backend_app.backend.ml_dataset import SupervisedDataset

    with pytest.raises(SupervisedDatasetError, match="!= label count"):
        SupervisedDataset(
            X=np.zeros((10, 3)),
            y=np.zeros(9),
            index=np.arange(10),
            row_range=SplitRange(0, 10),
            horizon=1,
            label_mode=LabelMode.REGRESSION,
        )


# ══════════════════════════════════════════════════════════════════════════
#  DROPPED TRAILING HORIZON ROWS  (Requirement 18.10)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("horizon", [1, 3, 7])
def test_trailing_horizon_rows_are_dropped_not_labelled(horizon):
    n = 100
    features = _matrix(n, n_cols=2)
    prices = np.linspace(10.0, 20.0, n)

    dataset = build_supervised_dataset(features, prices, horizon)

    # The last kept row is n-horizon-1; its label reads bar n-1, the last real
    # bar. Nothing beyond that is invented.
    assert dataset.dropped_trailing_rows == horizon
    assert dataset.index[-1] == features.index[n - horizon - 1]
    assert len(dataset.index) == n - horizon
    # No timestamp from the dropped tail survives.
    dropped = set(features.index[n - horizon:].tolist())
    assert not dropped & set(dataset.index.tolist())


def test_too_few_rows_for_a_single_label_is_refused():
    features = _matrix(5, warmup=2)
    prices = np.linspace(1.0, 2.0, 5)
    # 5 rows, 2 to warmup, horizon 3 leaves nothing.
    with pytest.raises(SupervisedDatasetError, match="no labelled rows available"):
        build_supervised_dataset(features, prices, 3)


def test_exactly_one_labelled_row_is_allowed():
    features = _matrix(4, warmup=2)
    prices = np.array([1.0, 2.0, 3.0, 4.0])
    dataset = build_supervised_dataset(features, prices, 1)
    assert dataset.n_rows == 1


# ══════════════════════════════════════════════════════════════════════════
#  BACKWARD-ONLY FEATURES, FORWARD-ONLY LABELS  (Requirements 18.7, 18.8)
# ══════════════════════════════════════════════════════════════════════════


def test_features_are_the_rows_own_bars():
    n = 50
    features = _matrix(n, n_cols=3, warmup=5)
    prices = np.linspace(100.0, 150.0, n)

    dataset = build_supervised_dataset(features, prices, 4)

    # Row i of X is row (i + warmup) of the feature matrix — its own bar, never
    # a later one.
    for i in range(dataset.n_rows):
        assert np.array_equal(dataset.X[i], features.values[i + 5])
        assert dataset.index[i] == features.index[i + 5]


def test_label_reads_exactly_the_horizon_bar_ahead():
    n = 20
    horizon = 3
    features = _matrix(n, n_cols=1)
    prices = np.arange(100.0, 100.0 + n)

    dataset = build_supervised_dataset(
        features, prices, horizon, label_mode=LabelMode.REGRESSION
    )

    for i in range(dataset.n_rows):
        base = prices[i]
        future = prices[i + horizon]
        assert dataset.y[i] == pytest.approx((future - base) / base)


def test_classification_labels_use_the_threshold_band():
    # Three bars up, flat, down beyond / within a 1% band.
    prices = np.array([100.0, 100.0, 100.0, 110.0, 100.05, 90.0])
    features = _matrix(len(prices), n_cols=1)

    dataset = build_supervised_dataset(
        features,
        prices,
        3,
        label_mode=LabelMode.CLASSIFICATION,
        threshold=0.01,
    )

    # row 0: 100 -> 110 = +10%  => UP
    # row 1: 100 -> 100.05 = +0.05% within band => FLAT
    # row 2: 100 -> 90 = -10%   => DOWN
    assert list(dataset.y) == [CLASS_UP, CLASS_FLAT, CLASS_DOWN]


def test_misaligned_prices_are_refused():
    features = _matrix(100)
    with pytest.raises(SupervisedDatasetError, match="prices hold 90 rows"):
        build_supervised_dataset(features, np.linspace(1.0, 2.0, 90), 1)


def test_mismatched_price_index_is_refused():
    features = _matrix(100)
    prices = np.linspace(1.0, 2.0, 100)
    wrong_index = _index(100, start=999)
    with pytest.raises(SupervisedDatasetError, match="price index does not match"):
        build_supervised_dataset(features, prices, 1, price_index=wrong_index)


def test_matching_price_index_is_accepted():
    features = _matrix(100)
    prices = np.linspace(1.0, 2.0, 100)
    dataset = build_supervised_dataset(
        features, prices, 1, price_index=features.index
    )
    assert dataset.n_rows == 99


def test_dataset_stats_report_the_construction_facts():
    features = _matrix(200, n_cols=4, warmup=10)
    prices = np.linspace(1.0, 2.0, 200)
    dataset = build_supervised_dataset(features, prices, 5)

    stats = dataset.stats()
    assert stats["rows"] == 200 - 10 - 5
    assert stats["columns"] == 4
    assert stats["feature_names"] == ["f0", "f1", "f2", "f3"]
    assert stats["horizon"] == 5
    assert stats["dropped_warmup_rows"] == 10
    assert stats["dropped_trailing_rows"] == 5


# ══════════════════════════════════════════════════════════════════════════
#  SPLITTING THE DATASET'S OWN ROW SPACE
# ══════════════════════════════════════════════════════════════════════════


def test_splits_for_dataset_covers_dataset_rows_not_feature_rows():
    features = _matrix(1000, warmup=50)
    prices = np.linspace(100.0, 200.0, 1000)
    dataset = build_supervised_dataset(features, prices, 10)

    splits = splits_for_dataset(dataset, 0.15, 0.15, 40, feature_lookback=20)

    # The split space is the dataset's rows (1000 - 50 - 10), not the feature
    # matrix's 1000 — indexing X with feature-space rows would run off the end.
    assert splits.total_rows == dataset.n_rows == 940
    assert splits.test.stop == dataset.n_rows
    splits.test.take(dataset.X)  # must not raise
    splits.test.take(dataset.y)


def test_splits_for_dataset_folds_the_horizon_into_the_embargo_floor():
    features = _matrix(1000)
    prices = np.linspace(100.0, 200.0, 1000)
    dataset = build_supervised_dataset(features, prices, 10)

    # lookback 20 + horizon 10 = 30; an embargo of 25 is below the floor.
    with pytest.raises(InsufficientEmbargoError, match="30"):
        splits_for_dataset(dataset, 0.15, 0.15, 25, feature_lookback=20)

    derived = splits_for_dataset(dataset, 0.15, 0.15, None, feature_lookback=20)
    assert derived.embargo_bars == 30
