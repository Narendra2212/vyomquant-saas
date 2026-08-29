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
