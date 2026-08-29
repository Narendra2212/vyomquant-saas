# -*- coding: utf-8 -*-
"""
tests/test_feature_matrix_contract.py

Unit tests for the FEATURE_MATRIX port contract added in
`backend_app/backend/strategy_dag/feature_matrix.py`.

Spec: strategy-builder task 5.1. Requirements 18.12 ("THE Feature_Matrix SHALL carry a
strictly increasing timestamp index, unique column names, a warmup offset and the producing
node identifier for each column") and 18.13 ("WHEN feature outputs are combined, THE
Strategy_Compiler SHALL align them on their timestamp index").

What this file is really testing
--------------------------------
Requirement 18.13 is one sentence, but the failure it guards against does not announce
itself. Two feature columns with different warmups have different leading NaN counts; stack
them by row position and every column shifts relative to every other column and relative to
the label. Shapes still match. Nothing raises. The model scores well on information it will
never have when trading - the recorded ML-1 defect class.

So it is not enough to test that the join happens to align correctly on the inputs a test
author thought of. The tests below are organised around the six mechanisms the module
docstring claims make positional alignment *structurally* impossible, and each one is
checked as a mechanism rather than as a happy path:

1. a matrix cannot exist without its index          -> TestIndexIsMandatory
2. one combine function, with no positional switch  -> TestNoPositionalCombinePathExists
3. the join verifies its own mapping                -> TestTimestampJoin
4. re-indexing goes through timestamps              -> TestTimestampAddressing
5. the value block is read-only                     -> TestPayloadIsImmutable
6. warmup travels as a timestamp, not a row count   -> TestWarmupTranslation

The decisive case is `test_equal_row_counts_do_not_license_positional_stacking`: two
matrices with the *same number of rows* over *different* timestamps. That is precisely the
input on which a shape check passes and a positional merge silently lies.

`TestOneFeatureMatrixExists` guards the consolidation itself. A second `FeatureMatrix` class
living in `feature_engineering.py` would be the same dual-source-of-truth defect as the two
DAG compilers (SB-01), so the test asserts object identity between the two import paths, not
merely that both are importable.

Nothing is mocked. Every matrix is built from real NumPy series through the real public API.
"""

import ast
import inspect

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.strategy_dag import feature_matrix as fm
from backend_app.backend.strategy_dag.feature_matrix import (
    PORT_TYPE,
    FeatureAlignmentError,
    FeatureColumnError,
    FeatureIndexError,
    FeatureMatrix,
    FeatureMatrixError,
    FeatureShapeError,
    build_feature_matrix,
    concat_matrices,
    select_columns,
)
from backend_app.backend.strategy_dag.schema import PortType

HOUR = 3600
EPOCH = 1_600_000_000


def stamps(count: int, start_bar: int = 0) -> np.ndarray:
    """`count` hourly bar timestamps, beginning `start_bar` bars after the epoch."""
    return np.arange(start_bar, start_bar + count, dtype="int64") * HOUR + EPOCH


def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """A leading-NaN rolling mean, so a column's warmup is real rather than declared."""
    out = np.full(len(values), np.nan)
    for i in range(window - 1, len(values)):
        out[i] = float(np.mean(values[i - window + 1 : i + 1]))
    return out


def same_payload(left: FeatureMatrix, right: FeatureMatrix) -> bool:
    """Compare two matrices field by field, treating NaN as equal to NaN.

    `to_dict() == to_dict()` cannot be used: feature columns are full of warmup NaNs, and
    `nan != nan`, so two identical matrices would never compare equal.
    """
    a, b = left.to_dict(), right.to_dict()
    if {k: v for k, v in a.items() if k != "values"} != {
        k: v for k, v in b.items() if k != "values"
    }:
        return False
    return np.allclose(
        np.asarray(a["values"], dtype=float).reshape(left.n_rows, left.n_columns),
        np.asarray(b["values"], dtype=float).reshape(right.n_rows, right.n_columns),
        equal_nan=True,
    )


@pytest.fixture
def prices():
    rng = np.random.default_rng(5)
    return 100 + np.cumsum(rng.normal(0, 0.5, 60))


# ---------------------------------------------------------------------------
# 0. Exactly one FeatureMatrix exists
# ---------------------------------------------------------------------------


class TestOneFeatureMatrixExists:
    """The contract is consolidated, not duplicated."""

    def test_feature_engineering_reexports_the_same_class_object(self):
        from backend_app.backend import feature_engineering as engine

        assert engine.FeatureMatrix is FeatureMatrix
        assert engine.build_feature_matrix is build_feature_matrix

    def test_the_engine_runtime_refs_delegate_to_the_one_join(self, prices):
        """feat_concat / feat_select must not grow a second implementation."""
        from backend_app.backend.feature_engineering import FeatureEngine

        index = stamps(len(prices))
        left = build_feature_matrix(index, ["a"], [rolling_mean(prices, 5)])
        right = build_feature_matrix(index, ["b"], [rolling_mean(prices, 9)])

        through_engine = FeatureEngine.concat_feature_matrices([left, right])
        through_contract = concat_matrices([left, right])
        assert same_payload(through_engine, through_contract)

        assert same_payload(
            FeatureEngine.select_feature_columns(through_engine, ["b"]),
            select_columns(through_contract, ["b"]),
        )

    def test_the_contract_does_not_import_the_engine(self):
        """The dependency runs engine -> contract. The reverse would be a cycle.

        Only import statements are inspected; the module docstring is free to name the
        engine, and does.
        """
        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(fm))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = sorted(
            name for name in imported if "feature_engineering" in name.split(".")
        )
        assert not offenders, f"the contract imports the engine: {offenders}"

    def test_the_payload_is_bound_to_the_port_type(self):
        assert PORT_TYPE is PortType.FEATURE_MATRIX


# ---------------------------------------------------------------------------
# 1. A matrix cannot exist without its index (mechanism 1)
# ---------------------------------------------------------------------------


class TestIndexIsMandatory:
    """Requirement 18.12: a strictly increasing timestamp index, one entry per row."""

    def test_index_is_a_required_field(self):
        parameters = inspect.signature(FeatureMatrix).parameters
        assert parameters["index"].default is inspect.Parameter.empty
        assert inspect.signature(build_feature_matrix).parameters["index"].default is (
            inspect.Parameter.empty
        )

    def test_row_count_must_match_the_index(self):
        with pytest.raises(FeatureShapeError):
            FeatureMatrix(index=stamps(3), columns=["a", "b"], values=np.zeros((3, 1)))
        with pytest.raises(FeatureShapeError):
            FeatureMatrix(index=stamps(3), columns=["a"], values=np.zeros((4, 1)))

    def test_index_must_be_strictly_increasing(self):
        with pytest.raises(FeatureIndexError):
            FeatureMatrix(index=np.array([3, 1, 2]), columns=["a"], values=np.zeros((3, 1)))
        with pytest.raises(FeatureIndexError):
            # A duplicated timestamp makes an exact join ambiguous.
            FeatureMatrix(index=np.array([1, 1, 2]), columns=["a"], values=np.zeros((3, 1)))

    def test_an_inexactly_joinable_index_is_refused(self):
        """A float index cannot be matched by equality, so it is not a timestamp index."""
        with pytest.raises(FeatureIndexError):
            FeatureMatrix(
                index=np.array([1.0, 2.5, 3.0]), columns=["a"], values=np.zeros((3, 1))
            )
        with pytest.raises(FeatureIndexError):
            FeatureMatrix(index=np.array(["a", "b"]), columns=["x"], values=np.zeros((2, 1)))

    def test_a_datetime64_index_is_accepted(self):
        index = np.array(["2020-01-01T00", "2020-01-01T01"], dtype="datetime64[h]")
        matrix = FeatureMatrix(index=index, columns=["a"], values=np.zeros((2, 1)))
        assert matrix.n_rows == 2

    def test_column_names_are_unique(self):
        with pytest.raises(FeatureColumnError):
            FeatureMatrix(index=stamps(2), columns=["a", "a"], values=np.zeros((2, 2)))

    def test_annotations_cannot_name_a_column_that_does_not_exist(self):
        """A renamed column that keeps its old warmup key is a silently wrong offset."""
        with pytest.raises(FeatureColumnError):
            FeatureMatrix(
                index=stamps(2),
                columns=["a"],
                values=np.zeros((2, 1)),
                column_warmup={"b": 1},
            )
        with pytest.raises(FeatureColumnError):
            FeatureMatrix(
                index=stamps(2),
                columns=["a"],
                values=np.zeros((2, 1)),
                provenance={"b": "n_1"},
            )

    def test_a_one_dimensional_value_block_is_read_as_a_single_column(self):
        matrix = FeatureMatrix(index=stamps(3), columns=["a"], values=np.arange(3.0))
        assert matrix.n_columns == 1
        assert matrix.column("a").tolist() == [0.0, 1.0, 2.0]

    def test_series_length_is_checked_against_the_index(self, prices):
        with pytest.raises(FeatureShapeError):
            build_feature_matrix(stamps(10), ["a"], [prices])
        with pytest.raises(FeatureShapeError):
            build_feature_matrix(stamps(len(prices)), ["a", "b"], [prices])


# ---------------------------------------------------------------------------
# 2. No positional combine path exists (mechanism 2)
# ---------------------------------------------------------------------------


class TestNoPositionalCombinePathExists:
    """The reintroduction guard: positional alignment must stay inexpressible.

    Every other test here checks that the join *behaves* correctly. This one checks that
    nobody can add an opt-out later without the test going red - the same role the
    anti-fallback palette test plays for the block registry.
    """

    #: Names by which a positional-alignment escape hatch would arrive.
    BANNED_PARAMETERS = frozenset(
        {
            "axis",
            "how",
            "by_position",
            "positional",
            "ignore_index",
            "on_position",
            "align",
            "align_by",
            "reindex",
            "fill_value",
            "join",
        }
    )

    def _public_callables(self):
        for name in fm.__all__:
            member = getattr(fm, name)
            if inspect.isfunction(member):
                yield name, member
            elif inspect.isclass(member):
                for attr, value in vars(member).items():
                    if inspect.isfunction(value) and not attr.startswith("_"):
                        yield f"{name}.{attr}", value

    def test_no_public_callable_accepts_an_alignment_mode(self):
        offenders = {}
        for label, func in self._public_callables():
            found = self.BANNED_PARAMETERS & set(inspect.signature(func).parameters)
            if found:
                offenders[label] = sorted(found)
        assert not offenders, (
            f"positional-alignment escape hatch reintroduced: {offenders}. Feature "
            "matrices are joined on timestamps; an alignment mode makes the ML-1 shift "
            "expressible again."
        )

    def test_concat_takes_matrices_and_nothing_else(self):
        assert list(inspect.signature(concat_matrices).parameters) == ["matrices"]

    def test_concat_only_accepts_payloads_carrying_their_own_index(self, prices):
        """A bare array has no timestamps, so it cannot be aligned - only stacked."""
        index = stamps(len(prices))
        left = build_feature_matrix(index, ["a"], [prices])
        with pytest.raises(FeatureAlignmentError):
            concat_matrices([left, prices])
        with pytest.raises(FeatureAlignmentError):
            concat_matrices([left, {"values": prices}])

    def test_combining_fewer_than_two_matrices_is_refused(self, prices):
        one = build_feature_matrix(stamps(len(prices)), ["a"], [prices])
        with pytest.raises(FeatureAlignmentError):
            concat_matrices([one])
        with pytest.raises(FeatureAlignmentError):
            concat_matrices([])


# ---------------------------------------------------------------------------
# 3. The timestamp join (mechanism 3) - Requirement 18.13
# ---------------------------------------------------------------------------


class TestTimestampJoin:

    def test_equal_row_counts_do_not_license_positional_stacking(self, prices):
        """The decisive case: same shape, different timestamps.

        `left` covers bars 0..29 and `right` bars 5..34 - both 30 rows. Positional
        stacking would pair bar 0 with bar 5 and report 30 rows, and no shape check
        anywhere would notice the 5-bar shift. The join must instead return the 25
        timestamps the two actually share.
        """
        left = build_feature_matrix(stamps(30, 0), ["a"], [prices[:30]])
        right = build_feature_matrix(stamps(30, 5), ["b"], [prices[:30]])
        assert left.n_rows == right.n_rows  # a shape check would pass

        merged = concat_matrices([left, right])

        assert merged.n_rows == 25
        assert merged.index[0] == stamps(1, 5)[0]
        assert merged.index[-1] == stamps(1, 29)[0]
        for row, stamp in enumerate(merged.index.tolist()):
            assert merged.values[row, 0] == pytest.approx(
                left.column("a")[left.row_of(stamp)], nan_ok=True
            )
            assert merged.values[row, 1] == pytest.approx(
                right.column("b")[right.row_of(stamp)], nan_ok=True
            )

    def test_differing_warmups_stay_on_their_own_timestamps(self, prices):
        """Two windows over one index: the 40-bar column must not shift the 5-bar one."""
        index = stamps(len(prices))
        left = build_feature_matrix(
            index, ["mean_5"], [rolling_mean(prices, 5)], node_id="n_a"
        )
        right = build_feature_matrix(
            index, ["mean_40"], [rolling_mean(prices, 40)], node_id="n_b"
        )
        merged = concat_matrices([left, right])

        assert merged.n_rows == len(prices)
        assert np.array_equal(merged.index, index)
        assert merged.column_warmup == {"mean_5": 4, "mean_40": 39}
        assert merged.warmup_offset == 39
        assert np.allclose(merged.column("mean_5"), left.column("mean_5"), equal_nan=True)
        assert np.allclose(merged.column("mean_40"), right.column("mean_40"), equal_nan=True)

    def test_rows_are_the_intersection_in_ascending_order(self, prices):
        a = build_feature_matrix(stamps(20, 0), ["a"], [prices[:20]])
        b = build_feature_matrix(stamps(20, 4), ["b"], [prices[:20]])
        c = build_feature_matrix(stamps(20, 8), ["c"], [prices[:20]])
        merged = concat_matrices([a, b, c])

        assert np.array_equal(merged.index, stamps(12, 8))
        assert list(np.diff(merged.index)) == [HOUR] * 11

    def test_a_disjoint_index_raises_instead_of_falling_back(self, prices):
        a = build_feature_matrix(stamps(10, 0), ["a"], [prices[:10]])
        b = build_feature_matrix(stamps(10, 500), ["b"], [prices[:10]])
        with pytest.raises(FeatureAlignmentError):
            concat_matrices([a, b])

    def test_a_gapped_index_joins_on_the_surviving_timestamps(self, prices):
        """A feed with a hole must not slide the other input's rows up into the gap."""
        full = stamps(12)
        gapped = np.delete(full, [3, 7])
        left = build_feature_matrix(full, ["a"], [prices[:12]])
        right = build_feature_matrix(gapped, ["b"], [prices[:10]])

        merged = concat_matrices([left, right])

        assert np.array_equal(merged.index, gapped)
        for row, stamp in enumerate(merged.index.tolist()):
            assert merged.values[row, 0] == pytest.approx(left.column("a")[left.row_of(stamp)])
            assert merged.values[row, 1] == pytest.approx(right.column("b")[right.row_of(stamp)])

    def test_duplicate_column_names_from_two_inputs_are_suffixed(self, prices):
        index = stamps(len(prices))
        first = build_feature_matrix(index, ["a"], [prices], node_id="n_1")
        second = build_feature_matrix(index, ["a"], [prices * 2], node_id="n_2")
        third = build_feature_matrix(index, ["a"], [prices * 3], node_id="n_3")
        merged = concat_matrices([first, second, third])

        assert merged.columns == ["a", "a_2", "a_3"]
        assert np.allclose(merged.column("a_2"), prices * 2)
        assert merged.provenance == {"a": "n_1", "a_2": "n_2", "a_3": "n_3"}

    def test_the_index_dtype_survives_the_join(self):
        index = np.arange(
            np.datetime64("2020-01-01T00", "h"), np.datetime64("2020-01-01T06", "h")
        )
        left = FeatureMatrix(index=index, columns=["a"], values=np.arange(6.0))
        right = FeatureMatrix(index=index[2:], columns=["b"], values=np.arange(4.0))
        merged = concat_matrices([left, right])

        assert np.issubdtype(merged.index.dtype, np.datetime64)
        assert np.array_equal(merged.index, index[2:])
        assert merged.column("a").tolist() == [2.0, 3.0, 4.0, 5.0]

    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        left_start=st.integers(min_value=0, max_value=12),
        left_len=st.integers(min_value=2, max_value=25),
        right_start=st.integers(min_value=0, max_value=12),
        right_len=st.integers(min_value=2, max_value=25),
    )
    def test_every_merged_cell_came_from_its_own_timestamp(
        self, left_start, left_len, right_start, right_len
    ):
        """The core invariant, over arbitrary overlaps rather than chosen ones.

        For every merged row, each column holds the value its source held at that same
        timestamp - which is what "aligned on the timestamp index" means, stated as a
        property instead of an example.
        """
        left_index = stamps(left_len, left_start)
        right_index = stamps(right_len, right_start)
        left = build_feature_matrix(
            left_index, ["a"], [np.asarray(left_index, dtype=float)]
        )
        right = build_feature_matrix(
            right_index, ["b"], [np.asarray(right_index, dtype=float)]
        )

        overlap = np.intersect1d(left_index, right_index)
        if overlap.size == 0:
            with pytest.raises(FeatureAlignmentError):
                concat_matrices([left, right])
            return

        merged = concat_matrices([left, right])
        assert np.array_equal(merged.index, overlap)
        # Each column was seeded with its own timestamp, so a shift is visible as a value.
        assert np.array_equal(merged.column("a"), overlap.astype(float))
        assert np.array_equal(merged.column("b"), overlap.astype(float))


# ---------------------------------------------------------------------------
# 4. Row addressing goes through timestamps (mechanism 4)
# ---------------------------------------------------------------------------


class TestTimestampAddressing:

    def test_row_of_resolves_a_timestamp(self, prices):
        index = stamps(len(prices), 7)
        matrix = build_feature_matrix(index, ["a"], [prices])
        assert matrix.row_of(index[0]) == 0
        assert matrix.row_of(index[11]) == 11

    def test_row_of_refuses_a_timestamp_it_does_not_hold(self, prices):
        matrix = build_feature_matrix(stamps(10), ["a"], [prices[:10]])
        with pytest.raises(FeatureAlignmentError):
            matrix.row_of(stamps(1, 999)[0])

    def test_align_to_matches_by_timestamp_not_by_leading_rows(self, prices):
        matrix = build_feature_matrix(stamps(20), ["a"], [prices[:20]])
        target = stamps(5, 12)

        aligned = matrix.align_to(target)

        assert np.array_equal(aligned.index, target)
        # Positional slicing would have handed back rows 0..4; timestamps give 12..16.
        assert np.allclose(aligned.column("a"), prices[12:17])

    def test_align_to_refuses_a_missing_timestamp_rather_than_filling_it(self, prices):
        matrix = build_feature_matrix(stamps(10), ["a"], [prices[:10]])
        with pytest.raises(FeatureAlignmentError):
            matrix.align_to(stamps(12))

    def test_unknown_columns_are_named_rather_than_returning_nothing(self, prices):
        matrix = build_feature_matrix(stamps(10), ["a"], [prices[:10]])
        with pytest.raises(FeatureColumnError):
            matrix.column("nope")


# ---------------------------------------------------------------------------
# 5. The payload is immutable (mechanism 5)
# ---------------------------------------------------------------------------


class TestPayloadIsImmutable:

    def test_the_value_block_cannot_be_written(self, prices):
        matrix = build_feature_matrix(stamps(len(prices)), ["a"], [prices])
        assert matrix.values.flags.writeable is False
        with pytest.raises(ValueError):
            matrix.values[0, 0] = 1.0

    def test_freezing_the_payload_does_not_freeze_the_caller_array(self, prices):
        supplied = np.column_stack([prices])
        FeatureMatrix(index=stamps(len(prices)), columns=["a"], values=supplied)
        supplied[0, 0] = 1.0  # the caller still owns their array
        assert supplied[0, 0] == 1.0

    def test_fields_cannot_be_reassigned(self, prices):
        matrix = build_feature_matrix(stamps(len(prices)), ["a"], [prices])
        with pytest.raises(Exception):
            matrix.warmup_offset = 0
        with pytest.raises(Exception):
            matrix.index = stamps(len(prices), 5)


# ---------------------------------------------------------------------------
# 6. Warmup travels as a timestamp (mechanism 6)
# ---------------------------------------------------------------------------


class TestWarmupTranslation:

    def test_warmup_offset_is_the_worst_column(self, prices):
        matrix = build_feature_matrix(
            stamps(len(prices)),
            ["mean_5", "mean_40"],
            [rolling_mean(prices, 5), rolling_mean(prices, 40)],
            node_id="n_fe1",
        )
        assert matrix.column_warmup == {"mean_5": 4, "mean_40": 39}
        assert matrix.warmup_offset == 39
        assert not np.isnan(matrix.values[matrix.warmup_offset]).any()

    def test_a_passed_offset_is_a_floor_not_the_answer(self, prices):
        """warmup_offset is always re-derived, so it cannot understate reality."""
        matrix = FeatureMatrix(
            index=stamps(len(prices)),
            columns=["a", "b"],
            values=np.column_stack([prices, prices]),
            warmup_offset=3,
            column_warmup={"b": 11},
        )
        assert matrix.column_warmup == {"a": 3, "b": 11}
        assert matrix.warmup_offset == 11

    def test_warmup_is_clamped_into_the_matrix(self, prices):
        matrix = FeatureMatrix(
            index=stamps(5),
            columns=["a"],
            values=np.zeros((5, 1)),
            column_warmup={"a": 99},
        )
        assert matrix.warmup_offset == 5
        negative = FeatureMatrix(
            index=stamps(5), columns=["a"], values=np.zeros((5, 1)), column_warmup={"a": -4}
        )
        assert negative.warmup_offset == 0

    def test_a_column_that_is_never_real_warms_up_nowhere(self):
        matrix = build_feature_matrix(stamps(6), ["a"], [np.full(6, np.nan)])
        assert matrix.column_warmup["a"] == 6
        merged = concat_matrices(
            [matrix, build_feature_matrix(stamps(6), ["b"], [np.arange(6.0)])]
        )
        assert merged.column_warmup["a"] == merged.n_rows

    def test_the_offset_is_recomputed_from_the_first_trustworthy_timestamp(self, prices):
        """The row count is not carried across; the timestamp is.

        `left` is trustworthy from bar 4. The merged index starts at bar 3, so bar 4 is
        merged row 1 - not row 4, which is what carrying the integer across would give.
        """
        left = build_feature_matrix(stamps(30, 0), ["a"], [rolling_mean(prices, 5)[:30]])
        right = build_feature_matrix(stamps(27, 3), ["b"], [rolling_mean(prices[3:30], 10)])
        merged = concat_matrices([left, right])

        assert left.column_warmup["a"] == 4
        assert merged.index[0] == stamps(1, 3)[0]
        assert merged.column_warmup["a"] == 1
        assert merged.column_warmup["b"] == 9
        assert not np.isnan(merged.values[merged.warmup_offset]).any()

    def test_align_to_retranslates_the_warmup(self, prices):
        matrix = build_feature_matrix(stamps(30), ["a"], [rolling_mean(prices, 5)[:30]])
        aligned = matrix.align_to(stamps(26, 4))
        assert matrix.column_warmup["a"] == 4
        assert aligned.column_warmup["a"] == 0

    def test_usable_slice_drops_exactly_the_warmup_rows(self, prices):
        matrix = build_feature_matrix(
            stamps(len(prices)),
            ["mean_5", "mean_40"],
            [rolling_mean(prices, 5), rolling_mean(prices, 40)],
        )
        usable = matrix.usable_slice()
        assert usable.n_rows == matrix.n_rows - 39
        assert usable.warmup_offset == 0
        assert usable.index[0] == matrix.index[39]
        assert not np.isnan(usable.values).any()


# ---------------------------------------------------------------------------
# 7. Provenance - Requirement 18.12
# ---------------------------------------------------------------------------


class TestProvenance:

    def test_each_column_records_its_producing_node(self, prices):
        matrix = build_feature_matrix(
            stamps(len(prices)), ["a", "b"], [prices, prices * 2], node_id="n_feat_1"
        )
        assert matrix.provenance == {"a": "n_feat_1", "b": "n_feat_1"}

    def test_provenance_follows_a_column_through_the_join_and_a_selection(self, prices):
        index = stamps(len(prices))
        left = build_feature_matrix(index, ["a"], [prices], node_id="n_a")
        right = build_feature_matrix(index, ["b"], [prices * 2], node_id="n_b")
        merged = concat_matrices([left, right])
        assert merged.provenance == {"a": "n_a", "b": "n_b"}
        assert select_columns(merged, ["b"]).provenance == {"b": "n_b"}


# ---------------------------------------------------------------------------
# 8. Column projection
# ---------------------------------------------------------------------------


class TestSelectColumns:

    @pytest.fixture
    def matrix(self, prices):
        return build_feature_matrix(
            stamps(len(prices)), ["a", "b", "c"], [prices, prices * 2, prices * 3]
        )

    def test_requested_order_is_preserved(self, matrix, prices):
        picked = select_columns(matrix, ["c", "a"])
        assert picked.columns == ["c", "a"]
        assert np.allclose(picked.column("c"), prices * 3)
        assert np.array_equal(picked.index, matrix.index)

    def test_an_unknown_column_is_an_error_not_a_silent_drop(self, matrix):
        with pytest.raises(FeatureColumnError):
            select_columns(matrix, ["a", "nope"])

    def test_an_empty_selection_is_refused(self, matrix):
        with pytest.raises(FeatureColumnError):
            select_columns(matrix, [])

    def test_a_repeated_column_is_refused(self, matrix):
        """Duplicates would break the unique-column invariant on the way out."""
        with pytest.raises(FeatureColumnError):
            select_columns(matrix, ["a", "a"])

    def test_selection_keeps_each_columns_own_warmup(self, prices):
        matrix = build_feature_matrix(
            stamps(len(prices)),
            ["mean_5", "mean_40"],
            [rolling_mean(prices, 5), rolling_mean(prices, 40)],
        )
        assert select_columns(matrix, ["mean_5"]).warmup_offset == 4


# ---------------------------------------------------------------------------
# 9. Wire form
# ---------------------------------------------------------------------------


class TestWireForm:

    def test_round_trip_reproduces_every_field(self, prices):
        index = stamps(len(prices))
        original = concat_matrices(
            [
                build_feature_matrix(index, ["a"], [rolling_mean(prices, 5)], node_id="n_a"),
                build_feature_matrix(index, ["b"], [rolling_mean(prices, 12)], node_id="n_b"),
            ]
        )
        restored = FeatureMatrix.from_dict(original.to_dict())

        assert same_payload(restored, original)
        assert np.array_equal(restored.index, original.index)
        assert np.allclose(restored.values, original.values, equal_nan=True)
        assert restored.column_warmup == original.column_warmup
        assert restored.provenance == original.provenance

    def test_a_non_object_payload_is_refused(self):
        with pytest.raises(FeatureMatrixError):
            FeatureMatrix.from_dict([1, 2, 3])

    def test_an_empty_matrix_round_trips(self):
        empty = FeatureMatrix(index=stamps(0), columns=[], values=np.empty((0, 0)))
        assert empty.warmup_offset == 0
        assert FeatureMatrix.from_dict(empty.to_dict()).n_rows == 0


# ---------------------------------------------------------------------------
# 10. Error typing
# ---------------------------------------------------------------------------


class TestErrorContract:

    def test_every_error_is_a_value_error_with_a_code(self):
        """Existing callers already handle ValueError; the code is what tests quote."""
        for error in (
            FeatureMatrixError,
            FeatureShapeError,
            FeatureIndexError,
            FeatureColumnError,
            FeatureAlignmentError,
        ):
            assert issubclass(error, ValueError)
            assert isinstance(error.code, str) and error.code

    def test_codes_are_distinct(self):
        codes = {
            error.code
            for error in (
                FeatureShapeError,
                FeatureIndexError,
                FeatureColumnError,
                FeatureAlignmentError,
            )
        }
        assert len(codes) == 4
