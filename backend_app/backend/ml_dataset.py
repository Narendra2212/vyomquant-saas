"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ML DATASET CONSTRUCTION — temporal splits and supervised datasets       ║
║                                                                          ║
║  design.md § Feature engineering → "Data leakage protection"             ║
║  Mechanism 2 (temporal splits) and mechanism 3 (target construction).    ║
╚══════════════════════════════════════════════════════════════════════════╝

Two leakage mechanisms live here, both expressed as structure rather than as
checks that run after the fact.

1. `make_temporal_splits` returns *ranges*, never index arrays.

   A permutation of row indices can be shuffled; a half-open ``[start, stop)``
   range cannot. `SplitRange` deliberately exposes no method that materialises
   its members as a list, so a caller who wants to shuffle a split has nothing
   to shuffle. Chronological order is a property of the representation, not of
   a convention someone has to remember. (Requirement 18.11)

2. `build_supervised_dataset` derives X and y from one shared row range.

   The recorded ML-1 defect was an off-by-one between features and labels
   (``DL _generate_target`` returned ``len=n`` where the feature slice had
   ``n-1`` rows). The historical repair was ``min_len = min(len(X), len(y))``
   followed by a trim — a correction applied downstream of the mistake, which
   is exactly the shape of fix that cannot prevent the next one. Here both
   slices are taken from a single `SplitRange`, so ``rows(X) == len(y)`` holds
   by construction: there is no second length to disagree with the first.
   (Requirements 18.9, 18.10)

Feature rows read bar ``t`` and earlier; labels read bar ``t + horizon``. The
trailing ``horizon`` rows have no label and are dropped rather than labelled
with a fabricated future.

This module performs no I/O, holds no credentials and touches no execution,
risk or authorization path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, runtime_checkable

import numpy as np

logger = logging.getLogger("MLDataset")

__all__ = [
    "LabelMode",
    "MLDatasetError",
    "TemporalSplitError",
    "InsufficientEmbargoError",
    "SupervisedDatasetError",
    "SplitRange",
    "TemporalSplits",
    "SupervisedDataset",
    "FeatureMatrixLike",
    "required_embargo_bars",
    "make_temporal_splits",
    "build_supervised_dataset",
    "splits_for_dataset",
    "CLASS_DOWN",
    "CLASS_FLAT",
    "CLASS_UP",
    # measurement (the data-sufficiency engine's inputs)
    "MeasurementThresholds",
    "DEFAULT_MEASUREMENT_THRESHOLDS",
    "SplitMeasurements",
    "DatasetMeasurements",
    "measure_dataset",
]


# ══════════════════════════════════════════════════════════════════════════
#  ERRORS
#
#  All derive from ValueError so existing callers that guard dataset
#  preparation with `except ValueError` keep working. Every one carries a
#  concrete reason; none is a bare generic message.
# ══════════════════════════════════════════════════════════════════════════


class MLDatasetError(ValueError):
    """Base class for every dataset-construction refusal in this module."""


class TemporalSplitError(MLDatasetError):
    """A split geometry that cannot be produced without overlap or an empty part."""


class InsufficientEmbargoError(TemporalSplitError):
    """The requested embargo is smaller than feature lookback + label horizon."""


class SupervisedDatasetError(MLDatasetError):
    """Features and prices cannot produce an aligned (X, y) pair."""


# ══════════════════════════════════════════════════════════════════════════
#  LABELS
#
#  Class encoding matches `ml_models._generate_target` (down=0, flat=1, up=2)
#  so the platform carries one label definition rather than two.
# ══════════════════════════════════════════════════════════════════════════

CLASS_DOWN = 0
CLASS_FLAT = 1
CLASS_UP = 2


class LabelMode(str, Enum):
    """How a forward price move becomes a label."""

    REGRESSION = "regression"        # raw forward return
    CLASSIFICATION = "classification"  # down / flat / up around a threshold


# ══════════════════════════════════════════════════════════════════════════
#  FEATURE MATRIX CONTRACT
#
#  Structural, not nominal: this module accepts any object carrying the four
#  fields it reads. That covers `feature_engineering.FeatureMatrix` and any
#  future re-homing of that container without an import edge either way.
# ══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class FeatureMatrixLike(Protocol):
    """The part of a FEATURE_MATRIX payload this module reads."""

    index: Any          # strictly increasing timestamps, one entry per row
    columns: Sequence[str]
    values: Any         # shape (len(index), len(columns))
    warmup_offset: int  # rows before this are not trustworthy


# ══════════════════════════════════════════════════════════════════════════
#  SPLIT RANGE
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SplitRange:
    """
    A half-open contiguous row range ``[start, stop)``.

    This is the whole anti-shuffle mechanism. The range names its bounds and
    nothing else: there is no `indices()`, no `__iter__` and no `tolist()`,
    because any of those would hand back a permutable object and reintroduce
    the possibility this type exists to remove. `take()` returns a contiguous
    slice of the caller's array, so rows arrive in chronological order or they
    do not arrive at all.
    """

    start: int
    stop: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", int(self.start))
        object.__setattr__(self, "stop", int(self.stop))
        if self.start < 0:
            raise TemporalSplitError(f"SplitRange.start must be >= 0: {self.start}")
        if self.stop < self.start:
            raise TemporalSplitError(
                f"SplitRange must not run backwards: [{self.start}, {self.stop})"
            )

    def __len__(self) -> int:
        return self.stop - self.start

    def __bool__(self) -> bool:
        # An empty range is falsey; a range holding rows is truthy. Defined
        # explicitly so `if not splits.train` reads correctly despite __len__.
        return self.stop > self.start

    @property
    def is_empty(self) -> bool:
        return self.stop <= self.start

    def as_slice(self) -> slice:
        """The equivalent builtin slice, for indexing arrays and frames."""
        return slice(self.start, self.stop)

    def take(self, array: Any) -> Any:
        """
        Return this range's contiguous rows of ``array``.

        A view, in chronological order. Never a gather by index list.
        """
        arr = array if isinstance(array, np.ndarray) else np.asarray(array)
        if arr.shape[0] < self.stop:
            raise TemporalSplitError(
                f"range [{self.start}, {self.stop}) exceeds array length {arr.shape[0]}"
            )
        return arr[self.start:self.stop]

    def shift(self, offset: int) -> "SplitRange":
        """This range translated by ``offset`` rows. Used for label alignment."""
        return SplitRange(self.start + int(offset), self.stop + int(offset))

    def to_dict(self) -> Dict[str, int]:
        return {"start": self.start, "stop": self.stop, "rows": len(self)}


# ══════════════════════════════════════════════════════════════════════════
#  TEMPORAL SPLITS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TemporalSplits:
    """
    Train, validation and test as chronological, disjoint, embargoed ranges.

    Invariants enforced at construction, so a `TemporalSplits` that exists is
    a `TemporalSplits` that is safe to train on:

      * train, val and test appear in that chronological order
      * the three ranges are mutually disjoint
      * consecutive ranges are separated by at least ``embargo_bars``
      * every range holds at least one row
    """

    train: SplitRange
    val: SplitRange
    test: SplitRange
    embargo_bars: int
    total_rows: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "embargo_bars", int(self.embargo_bars))
        object.__setattr__(self, "total_rows", int(self.total_rows))

        if self.embargo_bars < 0:
            raise TemporalSplitError(f"embargo_bars must be >= 0: {self.embargo_bars}")

        # Non-empty. An empty split silently turns validation or testing into a
        # no-op and makes a model look better than it is.
        for name, rng in (("train", self.train), ("val", self.val), ("test", self.test)):
            if rng.is_empty:
                raise TemporalSplitError(
                    f"{name} split is empty ({rng.to_dict()}). Provide more rows, "
                    f"reduce the reserved fractions, or reduce the embargo "
                    f"({self.embargo_bars} bars)."
                )

        # Chronological order and disjointness. Half-open ranges are disjoint
        # exactly when each one ends at or before the next one starts.
        if self.train.stop > self.val.start:
            raise TemporalSplitError(
                f"train and val overlap: train={self.train.to_dict()}, "
                f"val={self.val.to_dict()}"
            )
        if self.val.stop > self.test.start:
            raise TemporalSplitError(
                f"val and test overlap: val={self.val.to_dict()}, "
                f"test={self.test.to_dict()}"
            )

        # Embargo. The gap absorbs the longest feature lookback plus the label
        # horizon, so no training row's feature window overlaps a validation
        # row's label window.
        if self.gap_train_val < self.embargo_bars:
            raise TemporalSplitError(
                f"train->val gap is {self.gap_train_val} bars, "
                f"embargo requires {self.embargo_bars}"
            )
        if self.gap_val_test < self.embargo_bars:
            raise TemporalSplitError(
                f"val->test gap is {self.gap_val_test} bars, "
                f"embargo requires {self.embargo_bars}"
            )

        if self.test.stop > self.total_rows:
            raise TemporalSplitError(
                f"test split ends at {self.test.stop} beyond row count {self.total_rows}"
            )

    @property
    def gap_train_val(self) -> int:
        return self.val.start - self.train.stop

    @property
    def gap_val_test(self) -> int:
        return self.test.start - self.val.stop

    @property
    def sizes(self) -> Dict[str, int]:
        """Split sizes, as reported on a training job."""
        return {
            "train": len(self.train),
            "val": len(self.val),
            "test": len(self.test),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train": self.train.to_dict(),
            "val": self.val.to_dict(),
            "test": self.test.to_dict(),
            "embargo_bars": self.embargo_bars,
            "total_rows": self.total_rows,
            "gap_train_val": self.gap_train_val,
            "gap_val_test": self.gap_val_test,
        }


def required_embargo_bars(feature_lookback: int, label_horizon: int) -> int:
    """
    The embargo floor: longest feature lookback plus the label horizon.

    A training row at the end of the train split reads ``feature_lookback``
    bars behind it and its label reads ``label_horizon`` bars ahead of it. Any
    gap smaller than the sum lets that row's label window reach into the
    validation split. (Requirement 18.6)
    """
    lookback = int(feature_lookback)
    horizon = int(label_horizon)
    if lookback < 0:
        raise TemporalSplitError(f"feature_lookback must be >= 0: {lookback}")
    if horizon < 1:
        raise TemporalSplitError(f"label_horizon must be >= 1: {horizon}")
    return lookback + horizon


def _assert_strictly_increasing(index: np.ndarray) -> None:
    if len(index) > 1:
        try:
            deltas = np.diff(index.astype("int64"))
        except (TypeError, ValueError):
            deltas = np.diff(np.asarray(index, dtype="datetime64[ns]").astype("int64"))
        if not np.all(deltas > 0):
            first_bad = int(np.flatnonzero(deltas <= 0)[0]) + 1
            raise TemporalSplitError(
                "index must be strictly increasing; "
                f"row {first_bad} does not advance past row {first_bad - 1}"
            )


def make_temporal_splits(
    index: Sequence[Any],
    val_fraction: float,
    test_fraction: float,
    embargo_bars: Optional[int] = None,
    *,
    feature_lookback: Optional[int] = None,
    label_horizon: Optional[int] = None,
) -> TemporalSplits:
    """
    Split a chronological index into train, validation and test ranges.

    Args:
        index: strictly increasing timestamps, one entry per row.
        val_fraction: share of rows reserved for validation.
        test_fraction: share of rows reserved for testing.
        embargo_bars: gap between consecutive splits. When ``feature_lookback``
            and ``label_horizon`` are supplied this is raised to their sum if it
            is smaller, and refused if it was explicitly set below it. When it
            is ``None`` it is derived from those two.
        feature_lookback: longest lookback of any feature on a path into the
            model, in bars.
        label_horizon: bars ahead the label looks.

    Returns:
        `TemporalSplits` holding three `SplitRange` values — ranges, not index
        arrays, so the result cannot be shuffled into a random split.

    Raises:
        TemporalSplitError: the geometry would overlap, leave a split empty, or
            violate the embargo.
        InsufficientEmbargoError: an explicit embargo below lookback + horizon.

    Preconditions:  index strictly increasing; val + test fractions < 1.
    Postconditions: the three ranges are disjoint, chronologically ordered,
        non-empty, and separated by at least the effective embargo.
    """
    idx = np.asarray(index)
    n = int(idx.shape[0]) if idx.ndim else 0
    _assert_strictly_increasing(idx)

    val_fraction = float(val_fraction)
    test_fraction = float(test_fraction)
    if not 0.0 < val_fraction < 1.0:
        raise TemporalSplitError(f"val_fraction out of range (0, 1): {val_fraction}")
    if not 0.0 < test_fraction < 1.0:
        raise TemporalSplitError(f"test_fraction out of range (0, 1): {test_fraction}")
    if val_fraction + test_fraction >= 1.0:
        raise TemporalSplitError(
            "val_fraction + test_fraction must leave a non-empty train split: "
            f"{val_fraction} + {test_fraction} = {val_fraction + test_fraction}"
        )

    # Effective embargo: at least the lookback plus the horizon, never less.
    floor: Optional[int] = None
    if feature_lookback is not None or label_horizon is not None:
        if feature_lookback is None or label_horizon is None:
            raise TemporalSplitError(
                "feature_lookback and label_horizon must be supplied together; "
                "the embargo floor is their sum"
            )
        floor = required_embargo_bars(feature_lookback, label_horizon)

    if embargo_bars is None:
        if floor is None:
            raise TemporalSplitError(
                "embargo_bars is required unless feature_lookback and "
                "label_horizon are supplied to derive it"
            )
        effective_embargo = floor
    else:
        effective_embargo = int(embargo_bars)
        if effective_embargo < 0:
            raise TemporalSplitError(f"embargo_bars must be >= 0: {effective_embargo}")
        if floor is not None and effective_embargo < floor:
            raise InsufficientEmbargoError(
                f"embargo_bars={effective_embargo} is below the required floor of "
                f"{floor} bars (feature lookback {feature_lookback} + label horizon "
                f"{label_horizon}). A smaller gap lets a training row's label "
                f"window reach into the validation split."
            )

    test_start = n - int(np.floor(n * test_fraction))
    val_start = test_start - int(np.floor(n * val_fraction))

    splits = TemporalSplits(
        train=SplitRange(0, max(0, val_start - effective_embargo)),
        val=SplitRange(
            min(val_start, test_start),
            max(min(val_start, test_start), test_start - effective_embargo),
        ),
        test=SplitRange(test_start, n),
        embargo_bars=effective_embargo,
        total_rows=n,
    )
    logger.debug("[SPLITS] %s", splits.to_dict())
    return splits


# ══════════════════════════════════════════════════════════════════════════
#  SUPERVISED DATASET
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SupervisedDataset:
    """
    An aligned (X, y) pair with the timestamps its rows belong to.

    ``rows(X) == len(y) == len(index) == len(row_range)`` — established by
    construction in `build_supervised_dataset`, restated here as a closing
    check so a hand-built instance cannot claim otherwise.
    """

    X: np.ndarray
    y: np.ndarray
    index: np.ndarray
    row_range: SplitRange
    horizon: int
    label_mode: LabelMode
    columns: Sequence[str] = field(default_factory=tuple)
    dropped_warmup_rows: int = 0
    dropped_trailing_rows: int = 0

    def __post_init__(self) -> None:
        rows = int(self.X.shape[0])
        if rows != int(self.y.shape[0]):
            raise SupervisedDatasetError(
                f"feature rows {rows} != label count {int(self.y.shape[0])}"
            )
        if rows != int(self.index.shape[0]):
            raise SupervisedDatasetError(
                f"feature rows {rows} != index length {int(self.index.shape[0])}"
            )
        if rows != len(self.row_range):
            raise SupervisedDatasetError(
                f"feature rows {rows} != row range {self.row_range.to_dict()}"
            )
        if self.horizon < 1:
            raise SupervisedDatasetError(f"horizon must be >= 1: {self.horizon}")

    @property
    def n_rows(self) -> int:
        return int(self.X.shape[0])

    @property
    def n_columns(self) -> int:
        return int(self.X.shape[1]) if self.X.ndim > 1 else 1

    def stats(self) -> Dict[str, Any]:
        """Dataset statistics the training gate and job status report."""
        return {
            "rows": self.n_rows,
            "columns": self.n_columns,
            "feature_names": list(self.columns),
            "horizon": int(self.horizon),
            "label_mode": self.label_mode.value,
            "dropped_warmup_rows": int(self.dropped_warmup_rows),
            "dropped_trailing_rows": int(self.dropped_trailing_rows),
            "row_range": self.row_range.to_dict(),
        }


def _labels_from(
    base: np.ndarray,
    future: np.ndarray,
    label_mode: LabelMode,
    threshold: float,
) -> np.ndarray:
    """
    Turn a (base, future) price pair into labels.

    ``base`` is each row's own bar — known at that row's timestamp. ``future``
    is the bar ``horizon`` ahead, which is the only forward-looking part of a
    label and is exactly what makes it a label.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        forward_return = np.where(base != 0, (future - base) / base, np.nan)

    if label_mode is LabelMode.REGRESSION:
        return forward_return

    labels = np.full(forward_return.shape, CLASS_FLAT, dtype=int)
    labels[forward_return > threshold] = CLASS_UP
    labels[forward_return < -threshold] = CLASS_DOWN
    return labels


def build_supervised_dataset(
    features: FeatureMatrixLike,
    prices: Sequence[float],
    horizon: int,
    *,
    label_mode: LabelMode = LabelMode.CLASSIFICATION,
    threshold: float = 0.001,
    price_index: Optional[Sequence[Any]] = None,
) -> SupervisedDataset:
    """
    Build an aligned (X, y) dataset from a feature matrix and a price series.

    One row range drives both slices:

        rng    = [features.warmup_offset, len(index) - horizon)
        X      = features.values[rng]
        base   = prices[rng]
        future = prices[rng.shift(horizon)]

    ``X`` and ``y`` are therefore the same length because they are the same
    range, not because a trim made them agree afterwards. This is the invariant
    form of the ML-1 off-by-one. (Requirements 18.7-18.10)

    Args:
        features: any object carrying `index`, `columns`, `values` and
            `warmup_offset` — see `FeatureMatrixLike`.
        prices: the label source, one entry per feature row, same order.
        horizon: bars ahead the label looks. Must be >= 1.
        label_mode: regression (raw forward return) or classification.
        threshold: classification band around zero.
        price_index: optional timestamps for ``prices``; when given it must
            equal ``features.index`` elementwise.

    Returns:
        `SupervisedDataset` with ``rows(X) == len(y)``.

    Raises:
        SupervisedDatasetError: horizon below 1, misaligned prices, or too few
            rows to produce a single labelled example.

    Preconditions:  feature index and price index are the same timestamps.
    Postconditions: row ``i`` uses only bars at or before its own timestamp for
        X and only bar ``i + horizon`` for y; the trailing ``horizon`` rows are
        dropped rather than labelled with a fabricated future.
    """
    horizon = int(horizon)
    if horizon < 1:
        raise SupervisedDatasetError(
            f"horizon must be >= 1: {horizon}. A horizon of 0 labels a row with "
            f"its own bar, which is not a prediction."
        )

    index = np.asarray(features.index)
    values = np.asarray(features.values, dtype=float)
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    price_array = np.asarray(prices, dtype=float)

    n = int(index.shape[0])
    if int(values.shape[0]) != n:
        raise SupervisedDatasetError(
            f"feature values hold {int(values.shape[0])} rows but the index holds {n}"
        )
    if int(price_array.shape[0]) != n:
        raise SupervisedDatasetError(
            f"prices hold {int(price_array.shape[0])} rows but the feature index "
            f"holds {n}. Labels must come from the same bars as the features."
        )
    if price_index is not None:
        supplied = np.asarray(price_index)
        if supplied.shape[0] != n or not np.array_equal(supplied, index):
            raise SupervisedDatasetError(
                "price index does not match the feature index; features and "
                "labels would be drawn from different bars"
            )

    warmup_offset = int(getattr(features, "warmup_offset", 0) or 0)
    if warmup_offset < 0:
        raise SupervisedDatasetError(f"warmup_offset must be >= 0: {warmup_offset}")

    # The one range. Everything below is a slice of it.
    usable_start = min(warmup_offset, n)
    usable_stop = n - horizon
    if usable_stop <= usable_start:
        raise SupervisedDatasetError(
            f"no labelled rows available: {n} rows, {warmup_offset} discarded to "
            f"warmup and the trailing {horizon} have no label. Provide at least "
            f"{warmup_offset + horizon + 1} rows."
        )

    row_range = SplitRange(usable_start, usable_stop)
    label_range = row_range.shift(horizon)

    X = row_range.take(values)
    base = row_range.take(price_array)
    future = label_range.take(price_array)
    y = _labels_from(base, future, label_mode, float(threshold))

    dataset = SupervisedDataset(
        X=X,
        y=y,
        index=row_range.take(index),
        row_range=row_range,
        horizon=horizon,
        label_mode=label_mode,
        columns=tuple(features.columns),
        dropped_warmup_rows=usable_start,
        dropped_trailing_rows=n - usable_stop,
    )
    logger.debug("[DATASET] %s", dataset.stats())
    return dataset


def splits_for_dataset(
    dataset: SupervisedDataset,
    val_fraction: float,
    test_fraction: float,
    embargo_bars: Optional[int] = None,
    *,
    feature_lookback: int = 0,
) -> TemporalSplits:
    """
    Split a `SupervisedDataset` over its own row space.

    Splitting the *feature* index and then indexing X with the result is an
    off-by-one waiting to happen: X starts at the warmup offset and stops
    ``horizon`` rows early, so the two row spaces differ at both ends. This
    helper splits the dataset's own rows instead, and takes the dataset's
    horizon as the label-horizon half of the embargo floor, so the embargo and
    the labels cannot drift apart.
    """
    return make_temporal_splits(
        dataset.index,
        val_fraction,
        test_fraction,
        embargo_bars,
        feature_lookback=feature_lookback,
        label_horizon=dataset.horizon,
    )


# ══════════════════════════════════════════════════════════════════════════
#  MEASUREMENT — the facts the data-sufficiency engine decides on
#
#  WHY THE MEASURING LIVES HERE AND THE DECIDING DOES NOT
#  ------------------------------------------------------
#  `ml_training_policy` owns every threshold and every verdict, and it is kept
#  deliberately import-light: `strategy_dag.validator` imports it at ITS import time to
#  install the ML readiness stage, so a numpy or pandas import at that module's scope
#  would be paid by the whole validator. `tests/test_strategy_dag_architecture.py`
#  measures that in a fresh interpreter.
#
#  This module already holds `y`, `index` and `X`, and already imports numpy. So the
#  division is: measurements are taken here, thresholds and outcomes are decided there.
#  Nothing below returns a verdict, a severity or a limit - only counts, ratios and
#  booleans about data that exists.
#
#  MEASURED, NEVER ESTIMATED
#  -------------------------
#  Same rule the row and column counts already follow. Every field is computed from the
#  built `(X, y)` pair and its index, never from a bar count minus a warmup figure. A
#  quantity that could not be computed is `None`, which the gate renders as "not
#  measured" rather than as a comfortable zero.
#
#  COST
#  ----
#  One pass per column for the feature statistics and one `np.unique` over the labels,
#  on a matrix the caller has already materialised. `max_rows` caps the feature scan on
#  very wide/long matrices by sampling a contiguous head-and-tail window, and says so
#  via `feature_scan_rows`, so a measurement is never silently taken over a subset
#  without the gate being told.
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MeasurementThresholds:
    """Tunables for the measurements. **Not** limits: nothing here refuses anything.

    They decide what counts as "near constant" and what counts as an outlier, which are
    properties of a measurement rather than of a policy. The policy layer reads the
    resulting counts and decides.
    """

    #: A column whose distinct-value ratio is at or below this is NEAR constant. A
    #: strictly constant column is reported separately and needs no ratio.
    near_constant_unique_ratio: float = 0.01
    #: ... and which also has at most this many distinct values. Both conditions, so a
    #: long column with 1% distinct values but thousands of levels is not called
    #: near-constant.
    near_constant_max_unique: int = 3
    #: |z| beyond this is counted as a target outlier. Reported as a count, never used
    #: to drop a row - this module removes nothing.
    outlier_sigma: float = 6.0
    #: A regression target whose distinct-value ratio is at or below this is reported as
    #: degenerate-looking. Again a measurement, not a refusal.
    degenerate_target_unique_ratio: float = 0.001
    #: Cap on rows scanned for the per-column feature statistics. 0 disables the cap.
    max_feature_scan_rows: int = 200_000

    def __post_init__(self) -> None:
        if not 0.0 <= self.near_constant_unique_ratio <= 1.0:
            raise MLDatasetError(
                f"near_constant_unique_ratio must be in [0, 1]: "
                f"{self.near_constant_unique_ratio}"
            )
        if self.near_constant_max_unique < 1:
            raise MLDatasetError(
                f"near_constant_max_unique must be >= 1: {self.near_constant_max_unique}"
            )
        if self.outlier_sigma <= 0:
            raise MLDatasetError(f"outlier_sigma must be > 0: {self.outlier_sigma}")
        if not 0.0 <= self.degenerate_target_unique_ratio <= 1.0:
            raise MLDatasetError(
                f"degenerate_target_unique_ratio must be in [0, 1]: "
                f"{self.degenerate_target_unique_ratio}"
            )
        if self.max_feature_scan_rows < 0:
            raise MLDatasetError(
                f"max_feature_scan_rows must be >= 0: {self.max_feature_scan_rows}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """The tunables as plain JSON, recorded beside the measurements they produced.

        Provenance, not configuration: a stored measurement is only interpretable
        against the thresholds it was taken at. "41 outlier rows" means one thing at 6
        sigma and another at 3, and a reader six months later has no other way to know
        which.
        """
        return {
            "near_constant_unique_ratio": self.near_constant_unique_ratio,
            "near_constant_max_unique": self.near_constant_max_unique,
            "outlier_sigma": self.outlier_sigma,
            "degenerate_target_unique_ratio": self.degenerate_target_unique_ratio,
            "max_feature_scan_rows": self.max_feature_scan_rows,
        }


DEFAULT_MEASUREMENT_THRESHOLDS = MeasurementThresholds()


def _class_counts(labels: np.ndarray) -> Dict[int, int]:
    """``{class: rows}`` over finite integer labels, in ascending class order."""
    finite = labels[np.isfinite(labels)] if labels.size else labels
    if finite.size == 0:
        return {}
    values, counts = np.unique(finite.astype(np.int64), return_counts=True)
    return {int(value): int(count) for value, count in zip(values, counts)}


def _non_finite_counts(values: np.ndarray) -> "tuple[int, int]":
    """``(nan_rows, inf_rows)``. Counted separately because they mean different things.

    A NaN label is a row that could not be labelled; an infinite one is a row whose
    forward return divided by a zero base. Folding them together would hide which.
    """
    if values.size == 0:
        return 0, 0
    as_float = values.astype(float, copy=False)
    return int(np.isnan(as_float).sum()), int(np.isinf(as_float).sum())


@dataclass(frozen=True)
class SplitMeasurements:
    """What one split actually holds, measured over its own contiguous rows.

    This is the half the row-count arithmetic cannot answer. A dataset can carry a
    perfectly adequate total class balance and still put every example of one class in
    the test range, which makes the training split single-class and the validation
    metric meaningless. The gate needs the per-split figures to see that, and a
    per-split figure cannot be derived from a total.
    """

    name: str
    rows: int
    class_counts: Dict[int, int] = field(default_factory=dict)
    target_variance: Optional[float] = None
    target_unique: Optional[int] = None

    @property
    def n_classes(self) -> int:
        return len(self.class_counts)

    @property
    def minority_count(self) -> Optional[int]:
        return min(self.class_counts.values()) if self.class_counts else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "class_counts": {str(k): v for k, v in self.class_counts.items()},
            "n_classes": self.n_classes,
            "minority_count": self.minority_count,
            "target_variance": self.target_variance,
            "target_unique": self.target_unique,
        }


@dataclass(frozen=True)
class DatasetMeasurements:
    """Every fact the data-sufficiency engine needs, measured from the built dataset.

    Deliberately flat and JSON-shaped: it is recorded on the training job and rendered
    in the readiness panel, so it must survive a round trip through JSONB without a
    custom encoder.
    """

    rows: int
    columns: int
    label_mode: str
    horizon: int

    # -- labels ----------------------------------------------------------
    label_nan_rows: int = 0
    label_inf_rows: int = 0
    #: Classification only; empty for regression.
    class_counts: Dict[int, int] = field(default_factory=dict)
    #: Regression only; ``None`` for classification.
    target_variance: Optional[float] = None
    target_std: Optional[float] = None
    target_mean: Optional[float] = None
    target_min: Optional[float] = None
    target_max: Optional[float] = None
    target_unique: Optional[int] = None
    target_outlier_rows: Optional[int] = None

    # -- index ------------------------------------------------------------
    index_strictly_increasing: Optional[bool] = None
    duplicate_timestamps: Optional[int] = None
    non_monotonic_rows: Optional[int] = None
    median_interval: Optional[int] = None
    irregular_interval_rows: Optional[int] = None

    # -- features ---------------------------------------------------------
    feature_nan_rows: Optional[int] = None
    feature_inf_rows: Optional[int] = None
    constant_feature_columns: "tuple[str, ...]" = ()
    near_constant_feature_columns: "tuple[str, ...]" = ()
    duplicate_feature_rows: Optional[int] = None
    #: Rows the per-column scan actually covered. Equal to ``rows`` unless the scan was
    #: capped, which is stated rather than silent.
    feature_scan_rows: int = 0

    # -- splits -----------------------------------------------------------
    splits: "tuple[SplitMeasurements, ...]" = ()
    embargo_bars: Optional[int] = None

    thresholds: MeasurementThresholds = DEFAULT_MEASUREMENT_THRESHOLDS

    # -- derived, so the gate does not restate the arithmetic ------------
    @property
    def is_classification(self) -> bool:
        return self.label_mode == LabelMode.CLASSIFICATION.value

    @property
    def n_classes(self) -> int:
        return len(self.class_counts)

    @property
    def minority_class(self) -> Optional[int]:
        if not self.class_counts:
            return None
        return min(self.class_counts, key=lambda cls: self.class_counts[cls])

    @property
    def minority_count(self) -> Optional[int]:
        return min(self.class_counts.values()) if self.class_counts else None

    @property
    def majority_count(self) -> Optional[int]:
        return max(self.class_counts.values()) if self.class_counts else None

    @property
    def imbalance_ratio(self) -> Optional[float]:
        """``majority / minority``. ``None`` when there is no minority to divide by.

        1.0 is perfectly balanced. Reported rather than judged: what counts as "too
        imbalanced" depends on the task and the model, which is the policy layer's call.
        """
        minority = self.minority_count
        majority = self.majority_count
        if not minority or majority is None:
            return None
        return float(majority) / float(minority)

    @property
    def target_unique_ratio(self) -> Optional[float]:
        if self.target_unique is None or self.rows <= 0:
            return None
        return float(self.target_unique) / float(self.rows)

    def split(self, name: str) -> Optional[SplitMeasurements]:
        for measured in self.splits:
            if measured.name == name:
                return measured
        return None

    @property
    def split_sizes(self) -> Dict[str, int]:
        return {measured.name: measured.rows for measured in self.splits}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "label_mode": self.label_mode,
            "horizon": self.horizon,
            "label_nan_rows": self.label_nan_rows,
            "label_inf_rows": self.label_inf_rows,
            "class_counts": {str(k): v for k, v in self.class_counts.items()},
            "n_classes": self.n_classes,
            "minority_class": self.minority_class,
            "minority_count": self.minority_count,
            "majority_count": self.majority_count,
            "imbalance_ratio": self.imbalance_ratio,
            "target_variance": self.target_variance,
            "target_std": self.target_std,
            "target_mean": self.target_mean,
            "target_min": self.target_min,
            "target_max": self.target_max,
            "target_unique": self.target_unique,
            "target_unique_ratio": self.target_unique_ratio,
            "target_outlier_rows": self.target_outlier_rows,
            "index_strictly_increasing": self.index_strictly_increasing,
            "duplicate_timestamps": self.duplicate_timestamps,
            "non_monotonic_rows": self.non_monotonic_rows,
            "median_interval": self.median_interval,
            "irregular_interval_rows": self.irregular_interval_rows,
            "feature_nan_rows": self.feature_nan_rows,
            "feature_inf_rows": self.feature_inf_rows,
            "constant_feature_columns": list(self.constant_feature_columns),
            "near_constant_feature_columns": list(self.near_constant_feature_columns),
            "duplicate_feature_rows": self.duplicate_feature_rows,
            "feature_scan_rows": self.feature_scan_rows,
            "splits": [measured.to_dict() for measured in self.splits],
            "embargo_bars": self.embargo_bars,
            # The thresholds these figures were taken at, so a stored measurement stays
            # interpretable when a default changes later.
            "thresholds": self.thresholds.to_dict(),
            "source": "measured",
        }


def _measure_index(
    index: np.ndarray,
) -> "tuple[Optional[bool], Optional[int], Optional[int], Optional[int], Optional[int]]":
    """``(strictly_increasing, duplicates, non_monotonic, median_interval, irregular)``.

    `make_temporal_splits` already REFUSES a non-increasing index, and that refusal
    stays. This measures the same property *before* the refusal so the gate can report
    "your window has N duplicate timestamps" with a number, instead of the author
    discovering it as a `TemporalSplitError` after admission said yes.

    It also notices what the strict check cannot: `strategy_service.training_frame`
    drops duplicate timestamps with `keep="first"` before a dataset is ever built, so by
    the time rows reach here duplicates are usually already gone. A non-zero count here
    therefore means a dataset assembled some other way, which is worth saying out loud.

    Returns ``None``s for an index too short to have an interval.
    """
    if index.size <= 1:
        return True, 0, 0, None, 0
    try:
        as_int = index.astype("int64")
    except (TypeError, ValueError):
        try:
            as_int = np.asarray(index, dtype="datetime64[ns]").astype("int64")
        except (TypeError, ValueError):
            # A non-temporal index. Say "not measured" rather than guess.
            return None, None, None, None, None

    deltas = np.diff(as_int)
    duplicates = int((deltas == 0).sum())
    backwards = int((deltas < 0).sum())
    strictly_increasing = bool(duplicates == 0 and backwards == 0)

    positive = deltas[deltas > 0]
    if positive.size == 0:
        return strictly_increasing, duplicates, backwards, None, None
    median = int(np.median(positive))
    irregular = int((deltas != median).sum()) if median > 0 else None
    return strictly_increasing, duplicates, backwards, median, irregular


def _scan_window(rows: int, cap: int) -> SplitRange:
    """The contiguous row range the feature scan covers.

    A head window, because a feature matrix's pathologies (a column that is constant,
    a block of NaN that survived the warmup trim) are not uniformly distributed and a
    random sample would need an index list - which `SplitRange` deliberately does not
    offer. The covered row count is reported, so a capped scan is visible.
    """
    if cap <= 0 or rows <= cap:
        return SplitRange(0, max(0, rows))
    return SplitRange(0, cap)


def _measure_features(
    X: np.ndarray,
    columns: Sequence[str],
    thresholds: MeasurementThresholds,
) -> Dict[str, Any]:
    """Per-column constancy plus matrix-wide NaN/inf and duplicate-row counts."""
    out: Dict[str, Any] = {
        "feature_nan_rows": None,
        "feature_inf_rows": None,
        "constant_feature_columns": (),
        "near_constant_feature_columns": (),
        "duplicate_feature_rows": None,
        "feature_scan_rows": 0,
    }
    if X.size == 0:
        out["feature_scan_rows"] = 0
        out["feature_nan_rows"] = 0
        out["feature_inf_rows"] = 0
        out["duplicate_feature_rows"] = 0
        return out

    matrix = X if X.ndim > 1 else X.reshape(-1, 1)
    window = _scan_window(int(matrix.shape[0]), int(thresholds.max_feature_scan_rows))
    scanned = window.take(matrix).astype(float, copy=False)
    out["feature_scan_rows"] = int(scanned.shape[0])

    out["feature_nan_rows"] = int(np.isnan(scanned).any(axis=1).sum())
    out["feature_inf_rows"] = int(np.isinf(scanned).any(axis=1).sum())

    names = [str(name) for name in (columns or ())]
    constant: list = []
    near_constant: list = []
    for position in range(int(scanned.shape[1])):
        column = scanned[:, position]
        finite = column[np.isfinite(column)]
        label = names[position] if position < len(names) else f"column_{position}"
        if finite.size == 0:
            # Every value is NaN or infinite. That is not "constant", it is unusable,
            # and the NaN/inf counts above already say so. Recording it as constant
            # would make a hole look like a flat feature.
            continue
        distinct = int(np.unique(finite).size)
        if distinct <= 1:
            constant.append(label)
            continue
        ratio = float(distinct) / float(finite.size)
        if (
            ratio <= thresholds.near_constant_unique_ratio
            and distinct <= thresholds.near_constant_max_unique
        ):
            near_constant.append(label)

    out["constant_feature_columns"] = tuple(constant)
    out["near_constant_feature_columns"] = tuple(near_constant)

    try:
        # Exact duplicate feature rows. A handful is normal in a quantised market
        # series; a matrix that is mostly duplicates has far less information than its
        # row count claims, which is the thing worth reporting.
        unique_rows = np.unique(scanned, axis=0)
        out["duplicate_feature_rows"] = int(scanned.shape[0] - unique_rows.shape[0])
    except (TypeError, ValueError) as exc:  # noqa: BLE001 - a count, not a control
        logger.debug("Duplicate feature rows could not be counted: %s", exc)
        out["duplicate_feature_rows"] = None
    return out


def _measure_split(
    name: str,
    rng: SplitRange,
    y: np.ndarray,
    classification: bool,
) -> SplitMeasurements:
    labels = rng.take(y)
    if classification:
        return SplitMeasurements(
            name=name, rows=int(len(rng)), class_counts=_class_counts(labels)
        )
    values = labels.astype(float, copy=False)
    finite = values[np.isfinite(values)]
    return SplitMeasurements(
        name=name,
        rows=int(len(rng)),
        target_variance=float(np.var(finite)) if finite.size else None,
        target_unique=int(np.unique(finite).size) if finite.size else 0,
    )


def measure_dataset(
    dataset: SupervisedDataset,
    splits: Optional[TemporalSplits] = None,
    *,
    thresholds: MeasurementThresholds = DEFAULT_MEASUREMENT_THRESHOLDS,
) -> DatasetMeasurements:
    """Measure everything the data-sufficiency engine decides on. Never refuses.

    Args:
        dataset: the built ``(X, y)`` pair. Its own rows are the row space measured,
            which is the same row space `splits_for_dataset` splits - so a per-split
            figure here and a split size there cannot disagree.
        splits: the temporal splits, when they exist. Supplied, the per-split class and
            variance figures are measured; omitted, ``splits`` is empty and the gate
            reports split feasibility from arithmetic instead of from measurement.
        thresholds: what counts as near-constant, outlying or degenerate. Measurement
            tunables, not limits.

    Returns:
        `DatasetMeasurements`. Every quantity is measured or ``None``; nothing is
        estimated and nothing is defaulted to a comfortable zero.

    Raises:
        Nothing for a pathological dataset - that is the whole point. A single-class
        target, an all-NaN label column and a constant feature matrix are *findings*,
        and a measurement function that raised on them would hand the policy layer an
        exception where it needed a number. It will propagate a genuine programming
        error (a dataset whose ``X`` and ``y`` disagree in length, which
        `SupervisedDataset` already makes unconstructible).
    """
    y = np.asarray(dataset.y)
    X = np.asarray(dataset.X)
    index = np.asarray(dataset.index)
    mode = dataset.label_mode
    classification = mode is LabelMode.CLASSIFICATION

    label_nan, label_inf = _non_finite_counts(y)

    class_counts: Dict[int, int] = {}
    target_variance = target_std = target_mean = None
    target_min = target_max = None
    target_unique: Optional[int] = None
    target_outliers: Optional[int] = None

    if classification:
        class_counts = _class_counts(y)
    else:
        values = y.astype(float, copy=False)
        finite = values[np.isfinite(values)]
        if finite.size:
            target_variance = float(np.var(finite))
            target_std = float(np.std(finite))
            target_mean = float(np.mean(finite))
            target_min = float(np.min(finite))
            target_max = float(np.max(finite))
            target_unique = int(np.unique(finite).size)
            if target_std and target_std > 0:
                deviation = np.abs(finite - target_mean) / target_std
                target_outliers = int((deviation > thresholds.outlier_sigma).sum())
            else:
                # A zero-variance target has no outliers, and dividing by it would
                # manufacture infinities. Zero is the measurement, not a fallback.
                target_outliers = 0
        else:
            target_unique = 0

    increasing, duplicates, backwards, median_interval, irregular = _measure_index(index)
    features = _measure_features(X, getattr(dataset, "columns", ()) or (), thresholds)

    measured_splits: list = []
    embargo: Optional[int] = None
    if splits is not None:
        embargo = int(splits.embargo_bars)
        for name, rng in (
            ("train", splits.train),
            ("val", splits.val),
            ("test", splits.test),
        ):
            measured_splits.append(_measure_split(name, rng, y, classification))

    measurements = DatasetMeasurements(
        rows=int(dataset.n_rows),
        columns=int(dataset.n_columns),
        label_mode=mode.value,
        horizon=int(dataset.horizon),
        label_nan_rows=label_nan,
        label_inf_rows=label_inf,
        class_counts=class_counts,
        target_variance=target_variance,
        target_std=target_std,
        target_mean=target_mean,
        target_min=target_min,
        target_max=target_max,
        target_unique=target_unique,
        target_outlier_rows=target_outliers,
        index_strictly_increasing=increasing,
        duplicate_timestamps=duplicates,
        non_monotonic_rows=backwards,
        median_interval=median_interval,
        irregular_interval_rows=irregular,
        splits=tuple(measured_splits),
        embargo_bars=embargo,
        thresholds=thresholds,
        **features,
    )
    logger.debug("[DATASET] measured %s", measurements.to_dict())
    return measurements
