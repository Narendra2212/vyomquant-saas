"""
backend/strategy_dag/feature_matrix.py - the FEATURE_MATRIX port contract.

A ``FEATURE_MATRIX`` port carries a 2-D block of feature columns. This module owns the
*shape* of that payload and the *one* rule for combining two of them. It is data plus an
alignment algebra: no feature is computed here, and no feature formula is restated here.
``feature_engineering.FeatureEngine`` remains the only place features are computed, and
``FEATURE_SPECS`` remains the only place feature blocks are declared; both re-export the
type defined here so exactly one ``FeatureMatrix`` exists in the codebase.

Exposes
-------
``FeatureMatrix``        the port payload: ``index``, ``columns``, ``values``,
                         ``warmup_offset``, per-column ``column_warmup`` and per-column
                         ``provenance``
``build_feature_matrix`` assemble one from equally-long 1-D series plus their index
``concat_matrices``      the ONLY way to combine 2..N matrices, joined on timestamps
``select_columns``       project columns by name, order preserved
``PORT_TYPE``            the ``PortType`` member whose payload this is

Why the index is the contract, not the row number (Requirement 18.13)
--------------------------------------------------------------------
Two feature columns produced by two blocks almost never share a warmup. A rolling mean over
5 bars is trustworthy from row 4; one over 40 bars from row 39; a matrix sliced to start
three bars later begins at a different timestamp entirely. Stack those by row position and
every column silently shifts relative to every other column and, fatally, relative to the
label - the recorded ML-1 defect class. The shift does not raise, does not show up in a
shape check, and produces a model that scores well in a backtest on information it will
never have when trading.

So position is not an addressing mode in this module. Timestamps are:

1. **A matrix cannot exist without its index.** ``index`` is a required field of exactly
   ``len(values)`` strictly increasing integer or ``datetime64`` timestamps. There is no
   constructor, and no ``build_*`` helper, that takes values without an index.
2. **There is one combine function and it has no positional switch.**
   :func:`concat_matrices` takes matrices and nothing else - no ``axis``, no ``how``, no
   ``ignore_index``, no ``by_position``. A caller cannot opt into positional stacking
   because the option is not expressible.
3. **The join checks its own arithmetic.** After mapping each input onto the merged index,
   :func:`concat_matrices` re-reads the source timestamps at the positions it computed and
   asserts they equal the merged timestamps. A mis-mapped join raises
   :class:`FeatureAlignmentError` instead of returning a shifted column.
4. **Re-indexing goes through timestamps.** :meth:`FeatureMatrix.align_to` looks each
   requested timestamp up and refuses one it does not hold, rather than taking the first
   ``n`` rows.
5. **The value block is read-only.** ``values.flags.writeable`` is ``False``, so a caller
   cannot splice one matrix's column into another's rows in place and bypass the join.
6. **Warmup travels as a timestamp, not as a row count.** When a column moves onto a new
   index, its first trustworthy *row* is recomputed from its first trustworthy *timestamp*
   (see :func:`_translate_warmup`). Carrying the integer across would be the same
   off-by-one wearing a different hat.

``warmup_offset`` vs ``column_warmup``
-------------------------------------
``design.md`` gives ``FeatureMatrix`` a single ``warmup_offset``. Per-column warmups are
tracked as well, because a matrix built from a 5-bar mean and a 40-bar mean has one honest
answer per column and the matrix-level figure has to be the *worst* of them - the first row
at which every column is real. Deriving it (rather than trusting a passed-in value) is what
makes ``usable_slice()`` safe to hand to a model: below ``warmup_offset`` at least one
column is still NaN.

Purity
------
Imports the standard library, NumPy and ``strategy_dag.schema`` only: no FastAPI, no
database handle, no CCXT, no execution or credential module (Requirement 21.10). In
particular this module does **not** import ``feature_engineering``, which imports *it* -
the dependency runs from the engine towards the contract, never back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from backend_app.backend.strategy_dag.schema import PortType

#: The port type whose payload this module defines. Stated here so the contract and the
#: port vocabulary cannot drift apart silently.
PORT_TYPE = PortType.FEATURE_MATRIX

__all__ = [
    "PORT_TYPE",
    "FeatureMatrixError",
    "FeatureShapeError",
    "FeatureIndexError",
    "FeatureColumnError",
    "FeatureAlignmentError",
    "FeatureMatrix",
    "build_feature_matrix",
    "concat_matrices",
    "select_columns",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FeatureMatrixError(ValueError):
    """A feature matrix contract breach.

    A ``ValueError`` subclass on purpose: every one of these is a caller contract breach,
    and the existing callers of ``build_feature_matrix`` / ``concat_feature_matrices``
    already handle ``ValueError``. The ``code`` is what the validator and the API surface
    quote, so a message can be reworded without breaking a test or a client.
    """

    code = "FEATURE_MATRIX_INVALID"


class FeatureShapeError(FeatureMatrixError):
    """``values`` does not match ``(len(index), len(columns))``."""

    code = "FEATURE_MATRIX_SHAPE"


class FeatureIndexError(FeatureMatrixError):
    """The index is not a strictly increasing sequence of timestamps."""

    code = "FEATURE_MATRIX_INDEX"


class FeatureColumnError(FeatureMatrixError):
    """Column names are duplicated, unknown, or annotated for a column that is absent."""

    code = "FEATURE_MATRIX_COLUMNS"


class FeatureAlignmentError(FeatureMatrixError):
    """Two matrices cannot be aligned on their timestamp index.

    Raised instead of falling back to row position. There is no failure mode in this module
    worse than a successful positional merge.
    """

    code = "FEATURE_MATRIX_ALIGNMENT"


# ---------------------------------------------------------------------------
# Index handling
# ---------------------------------------------------------------------------


def _as_index(index: Any) -> np.ndarray:
    """Normalise a timestamp index, rejecting anything that cannot join exactly.

    Integer and ``datetime64`` indexes are accepted. Float and object indexes are not: an
    equality join on floats is not reliable, and ``astype("int64")`` would truncate two
    distinct timestamps onto one row. Refusing here is cheaper than debugging a feature
    that is one bar out.
    """
    array = np.asarray(index)
    if array.ndim != 1:
        raise FeatureIndexError(
            f"FeatureMatrix index must be 1-D, got shape {array.shape}"
        )
    if array.size == 0:
        # An empty index holds no timestamp, so its dtype carries no meaning - and
        # `np.asarray([])` reports float64, which the check below would reject. Normalise
        # rather than refuse: an empty matrix is a legitimate (if useless) payload, and it
        # has to survive a to_dict/from_dict round trip.
        return array.astype("int64") if not np.issubdtype(
            array.dtype, np.datetime64
        ) else array
    if not (
        np.issubdtype(array.dtype, np.integer)
        or np.issubdtype(array.dtype, np.datetime64)
    ):
        raise FeatureIndexError(
            f"FeatureMatrix index must hold integer or datetime64 timestamps, got dtype "
            f"{array.dtype}. A float or object index cannot be joined exactly, and an "
            "inexact join is a silently shifted feature."
        )
    return array


def _int_view(index: np.ndarray) -> np.ndarray:
    """The index as int64, for comparison and searching. Lossless for accepted dtypes."""
    if np.issubdtype(index.dtype, np.datetime64):
        return index.astype("datetime64[ns]").astype("int64")
    return index.astype("int64")


def _positions_of(haystack: np.ndarray, needles: np.ndarray) -> np.ndarray:
    """Row positions in ``haystack`` for every timestamp in ``needles``.

    Preconditions
        ``haystack`` is strictly increasing (a matrix invariant) and every value of
        ``needles`` is present in it.

    Postconditions
        ``haystack[result] == needles`` elementwise - verified, not assumed. That check is
        mechanism 3 from the module docstring: it is the difference between "we intended to
        join on timestamps" and "we did".

    Raises
        :class:`FeatureAlignmentError` naming a missing timestamp.
    """
    left = _int_view(haystack)
    right = _int_view(needles)
    if right.size == 0:
        return np.empty(0, dtype=int)

    positions = np.searchsorted(left, right)
    in_range = positions < left.size
    matched = np.zeros(right.size, dtype=bool)
    if left.size:
        matched[in_range] = left[positions[in_range]] == right[in_range]

    if not matched.all():
        missing = right[~matched]
        shown = missing[:5].tolist()
        raise FeatureAlignmentError(
            f"Timestamps {shown}{'...' if missing.size > 5 else ''} are absent from the "
            "target index, so those rows cannot be resolved by timestamp. Row position is "
            "not an alternative."
        )
    return positions.astype(int)


# ---------------------------------------------------------------------------
# FeatureMatrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class FeatureMatrix:
    """Aligned 2-D feature block - the ``FEATURE_MATRIX`` port payload.

    ``index``
        Strictly increasing timestamps, one per row. Integer or ``datetime64``.
    ``columns``
        Unique, stable column names, one per value column.
    ``values``
        Shape ``(len(index), len(columns))``, float, **read-only**.
    ``warmup_offset``
        The first row at which *every* column is trustworthy. Always derived as the worst
        per-column warmup; a value passed to the constructor is a floor, never the answer.
    ``column_warmup``
        ``column -> first trustworthy row``, in this matrix's own row space.
    ``provenance``
        ``column -> producing node_id``, so a feature can be traced back to the block that
        made it (Requirement 18.12) and a leakage finding can name a node.

    Frozen: a matrix is a value carried on a port, and one immutable payload cannot have
    its warmup or its index rewritten by a downstream node. Recompute instead of mutating.
    """

    index: np.ndarray
    columns: List[str]
    values: np.ndarray
    warmup_offset: int = 0
    column_warmup: Dict[str, int] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        index = _as_index(self.index)
        columns = [str(name) for name in self.columns]
        values = np.asarray(self.values, dtype=float)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2:
            raise FeatureShapeError(
                f"FeatureMatrix values must be 2-D, got shape {values.shape}"
            )
        if values.shape != (len(index), len(columns)):
            raise FeatureShapeError(
                f"FeatureMatrix shape mismatch: values={values.shape}, "
                f"index={len(index)}, columns={len(columns)}"
            )
        if len(set(columns)) != len(columns):
            raise FeatureColumnError(
                f"FeatureMatrix column names must be unique: {columns}"
            )
        if len(index) > 1:
            deltas = np.diff(_int_view(index))
            if not np.all(deltas > 0):
                raise FeatureIndexError(
                    "FeatureMatrix index must be strictly increasing; a duplicated or "
                    "out-of-order timestamp makes an exact join ambiguous"
                )

        known = set(columns)
        for label, mapping in (("column_warmup", self.column_warmup), ("provenance", self.provenance)):
            unknown = sorted(str(key) for key in mapping if str(key) not in known)
            if unknown:
                raise FeatureColumnError(
                    f"FeatureMatrix {label} annotates columns that do not exist: "
                    f"{unknown}. Columns are {columns}."
                )

        # Every column gets an honest warmup: the supplied one, or the matrix-level floor.
        # The matrix-level figure is then *re-derived* as the worst of them, so
        # `usable_slice()` cannot hand a model a row in which some column is still NaN.
        n_rows = int(values.shape[0])
        resolved: Dict[str, int] = {}
        for name in columns:
            raw = self.column_warmup.get(name, self.warmup_offset)
            resolved[name] = int(min(max(0, int(raw)), n_rows))

        # Read-only: mechanism 5. In-place splicing of another matrix's column is the one
        # way left to bypass the timestamp join, so the door is shut. A fresh view is taken
        # first, so freezing this payload never freezes an array the caller still owns.
        values = values.view()
        values.flags.writeable = False

        object.__setattr__(self, "index", index)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "column_warmup", resolved)
        object.__setattr__(
            self, "warmup_offset", int(max(resolved.values())) if resolved else 0
        )
        object.__setattr__(
            self, "provenance", {str(k): str(v) for k, v in self.provenance.items()}
        )

    # -- shape ------------------------------------------------------------

    @property
    def n_rows(self) -> int:
        return int(self.values.shape[0])

    @property
    def n_columns(self) -> int:
        return int(self.values.shape[1])

    # -- addressing -------------------------------------------------------

    def column(self, name: str) -> np.ndarray:
        """One column by name. Unknown names raise rather than returning nothing."""
        if name not in self.columns:
            raise FeatureColumnError(
                f"Unknown feature column {name!r}; this matrix holds {self.columns}"
            )
        return self.values[:, self.columns.index(name)]

    def row_of(self, timestamp: Any) -> int:
        """The row holding ``timestamp``.

        The only row lookup in the contract, and it is by timestamp. A caller that wants
        "the row three bars after the start" has to say which timestamp that is.
        """
        return int(_positions_of(self.index, _as_index([timestamp]))[0])

    def align_to(self, index: Any) -> "FeatureMatrix":
        """This matrix re-indexed onto ``index``, matched timestamp by timestamp.

        Preconditions
            Every timestamp in ``index`` is present in this matrix. A missing one raises
            :class:`FeatureAlignmentError`; it is never filled, and the rows are never
            taken by position.

        Postconditions
            The result holds one row per requested timestamp, in the requested order, and
            each column's warmup is recomputed from its first trustworthy *timestamp*.
        """
        target = _as_index(index)
        positions = _positions_of(self.index, target)
        return FeatureMatrix(
            index=target,
            columns=list(self.columns),
            values=self.values[positions, :],
            column_warmup={
                name: _translate_warmup(self, name, target) for name in self.columns
            },
            provenance=dict(self.provenance),
        )

    def usable_slice(self) -> "FeatureMatrix":
        """Drop the warmup rows. Every remaining row is trustworthy in every column."""
        start = int(self.warmup_offset)
        return FeatureMatrix(
            index=self.index[start:],
            columns=list(self.columns),
            values=self.values[start:, :],
            column_warmup={name: 0 for name in self.columns},
            provenance=dict(self.provenance),
        )

    # -- wire form --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """The one serialization path, matching the field names used at every layer."""
        return {
            "index": self.index.tolist(),
            "columns": list(self.columns),
            "values": self.values.tolist(),
            "warmup_offset": int(self.warmup_offset),
            "column_warmup": dict(self.column_warmup),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "FeatureMatrix":
        """Parse the wire form. ``from_dict(m.to_dict())`` reproduces ``m``."""
        if not isinstance(data, Mapping):
            raise FeatureMatrixError(
                f"FeatureMatrix must be an object, got {type(data).__name__}"
            )
        columns = list(data.get("columns") or [])
        rows = data.get("values") or []
        values = (
            np.asarray(rows, dtype=float)
            if rows
            else np.empty((len(data.get("index") or []), len(columns)), dtype=float)
        )
        return cls(
            index=data.get("index") or [],
            columns=columns,
            values=values,
            warmup_offset=int(data.get("warmup_offset") or 0),
            column_warmup=dict(data.get("column_warmup") or {}),
            provenance=dict(data.get("provenance") or {}),
        )


# ---------------------------------------------------------------------------
# Warmup translation
# ---------------------------------------------------------------------------


def _translate_warmup(
    matrix: FeatureMatrix, column: str, target_index: np.ndarray
) -> int:
    """``column``'s first trustworthy row, expressed in ``target_index``'s row space.

    The warmup crosses index boundaries as a *timestamp*, never as a row count: the source
    row is turned into the timestamp it sits on, and that timestamp is located in the
    target. Carrying the integer straight across is exactly the off-by-one this module
    exists to prevent.

    Postconditions
        The result is in ``[0, len(target_index)]``. A column that is trustworthy nowhere in
        the source is trustworthy nowhere in the target either, so it returns the full row
        count. A column whose first trustworthy timestamp was dropped by the join lands on
        the first surviving timestamp at or after it.
    """
    source_warmup = int(matrix.column_warmup.get(column, matrix.warmup_offset))
    if source_warmup >= matrix.n_rows:
        return int(len(target_index))
    first_real = _int_view(matrix.index)[source_warmup]
    return int(np.searchsorted(_int_view(target_index), first_real))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_feature_matrix(
    index: Sequence[Any],
    columns: Sequence[str],
    series: Sequence[np.ndarray],
    column_warmup: Optional[Mapping[str, int]] = None,
    node_id: Optional[str] = None,
) -> FeatureMatrix:
    """Assemble a :class:`FeatureMatrix` from equally-long 1-D series and their index.

    Column warmups default to the count of leading NaNs *actually present* in the produced
    column, so the offset reflects the data rather than an assumed lookback - a block whose
    declared warmup understates reality is caught here rather than at inference time.

    Preconditions
        ``len(series) == len(columns)`` and every series has ``len(index)`` entries.

    Postconditions
        The result satisfies every :class:`FeatureMatrix` invariant, and
        ``warmup_offset == max(column_warmup.values())``.
    """
    resolved_index = _as_index(index)
    names = [str(name) for name in columns]
    if len(names) != len(series):
        raise FeatureShapeError(
            f"build_feature_matrix got {len(names)} column names for {len(series)} series"
        )

    arrays = [np.asarray(item, dtype=float).reshape(-1) for item in series]
    for name, array in zip(names, arrays):
        if array.size != resolved_index.size:
            raise FeatureShapeError(
                f"Series {name!r} has {array.size} values for an index of "
                f"{resolved_index.size} timestamps"
            )
    stacked = (
        np.column_stack(arrays)
        if arrays
        else np.empty((resolved_index.size, 0), dtype=float)
    )

    warmup: Dict[str, int] = {}
    for position, name in enumerate(names):
        if column_warmup is not None and name in column_warmup:
            warmup[name] = int(column_warmup[name])
            continue
        col = stacked[:, position]
        finite = np.flatnonzero(~np.isnan(col))
        warmup[name] = int(finite[0]) if finite.size else int(col.size)

    return FeatureMatrix(
        index=resolved_index,
        columns=names,
        values=stacked,
        warmup_offset=max(warmup.values()) if warmup else 0,
        column_warmup=warmup,
        provenance={name: node_id for name in names} if node_id else {},
    )


# ---------------------------------------------------------------------------
# The alignment algebra
# ---------------------------------------------------------------------------


def _merged_index(matrices: Sequence[FeatureMatrix]) -> np.ndarray:
    """The timestamps present in every input, ascending, in the first input's dtype.

    Loop invariant
        ``surviving`` is the intersection of the indexes seen so far, so it shrinks
        monotonically and the result is a subsequence of every input index - which is what
        makes the ``_positions_of`` lookup total.
    """
    first = matrices[0].index
    surviving = _int_view(first)
    for matrix in matrices[1:]:
        surviving = np.intersect1d(surviving, _int_view(matrix.index), assume_unique=True)
    if surviving.size == 0:
        raise FeatureAlignmentError(
            "The feature matrices share no common timestamp, so there is no row on which "
            "they can be combined. Check that the inputs cover the same symbol, timeframe "
            "and date range."
        )
    # Read the surviving timestamps back out of the first input rather than returning the
    # raw int64 intersection, so a datetime64-indexed graph stays datetime64-indexed.
    return first[np.searchsorted(_int_view(first), surviving)]


def _unique_name(name: str, taken: Sequence[str]) -> str:
    """``name``, or ``name_2`` / ``name_3`` when a previous input already used it."""
    if name not in taken:
        return name
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"


def concat_matrices(matrices: Sequence[FeatureMatrix]) -> FeatureMatrix:
    """Merge 2..N matrices into one, joined on the timestamp index.

    This is the **only** way to combine feature matrices, and it takes no alignment mode:
    there is no ``axis``, no ``how``, no ``ignore_index`` and no ``by_position``, so
    positional stacking is not something a caller can ask for (mechanism 2).

    Preconditions
        At least two matrices, sharing at least one timestamp.

    Postconditions
        The result's rows are exactly the timestamps present in every input, ascending.
        Every column keeps the value its source held *at that timestamp* - re-verified
        after the mapping is computed (mechanism 3). Duplicate column names from different
        inputs are suffixed rather than silently overwriting each other. Each column's
        warmup is translated onto the merged index by timestamp, and the matrix-level
        warmup is the worst of them.

    Raises
        :class:`FeatureAlignmentError` for fewer than two inputs or a disjoint index.
    """
    ordered = list(matrices)
    if len(ordered) < 2:
        raise FeatureAlignmentError(
            f"Combining feature matrices needs at least 2 inputs, got {len(ordered)}"
        )
    for position, matrix in enumerate(ordered):
        if not isinstance(matrix, FeatureMatrix):
            raise FeatureAlignmentError(
                f"Input {position} is {type(matrix).__name__}, not a FeatureMatrix; only a "
                "payload carrying its own timestamp index can be aligned"
            )

    merged_index = _merged_index(ordered)

    columns: List[str] = []
    series: List[np.ndarray] = []
    column_warmup: Dict[str, int] = {}
    provenance: Dict[str, str] = {}

    for matrix in ordered:
        positions = _positions_of(matrix.index, merged_index)
        for offset, name in enumerate(matrix.columns):
            unique = _unique_name(name, columns)
            columns.append(unique)
            series.append(matrix.values[positions, offset])
            column_warmup[unique] = _translate_warmup(matrix, name, merged_index)
            source = matrix.provenance.get(name)
            if source:
                provenance[unique] = source

    return FeatureMatrix(
        index=merged_index,
        columns=columns,
        values=(
            np.column_stack(series)
            if series
            else np.empty((merged_index.size, 0), dtype=float)
        ),
        column_warmup=column_warmup,
        provenance=provenance,
    )


def select_columns(matrix: FeatureMatrix, columns: Sequence[str]) -> FeatureMatrix:
    """Keep only the named columns, in the order requested.

    An unknown column name is an error, not a silent drop: a model trained on a feature set
    that quietly lost a column is a model whose input contract no longer means what the
    graph says it means.
    """
    requested = [str(name) for name in columns]
    if not requested:
        raise FeatureColumnError("Selecting feature columns requires at least one column")
    missing = [name for name in requested if name not in matrix.columns]
    if missing:
        raise FeatureColumnError(
            f"Unknown feature columns {missing}; this matrix holds {matrix.columns}"
        )
    duplicated = sorted({name for name in requested if requested.count(name) > 1})
    if duplicated:
        raise FeatureColumnError(
            f"Feature columns may be selected once each; {duplicated} repeated"
        )

    positions = [matrix.columns.index(name) for name in requested]
    return FeatureMatrix(
        index=matrix.index,
        columns=requested,
        values=matrix.values[:, positions],
        column_warmup={
            name: int(matrix.column_warmup.get(name, matrix.warmup_offset))
            for name in requested
        },
        provenance={
            name: matrix.provenance[name]
            for name in requested
            if matrix.provenance.get(name)
        },
    )
