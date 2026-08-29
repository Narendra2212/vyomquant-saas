"""
tests/test_feature_specs.py

Unit tests for the FEATURE_ENGINEERING block descriptors added to
backend_app/backend/feature_engineering.py.

Covers:
  - all 15 design-tabulated feature blocks are declared, with the design's ids
  - every runtime_ref resolves to a real callable (registry assembly asserts this)
  - leakage classifications match the design table, and the rejectable "global"
    mode is representable on feat_zscore / feat_normalize
  - warmup functions return the design's lookbacks
  - the new leak-safe primitives compute past-only values
  - feat_concat aligns on the timestamp index, not on row position
  - feat_standardize refuses to fit a scaler outside the train split
  - create_feature_matrix is untouched
"""
import numpy as np
import pytest

from backend_app.backend.feature_engineering import (
    FEATURE_SPECS,
    FEATURE_SPECS_BY_ID,
    GLOBAL_STATISTIC_BLOCKS,
    FeatureEngine,
    FeatureMatrix,
    LeakageRisk,
    build_feature_matrix,
    get_feature_spec,
    resolve_feature_runtime,
    validate_feature_params,
)

# The 15 blocks tabulated in design.md § Feature engineering, in table order.
DESIGN_BLOCK_IDS = [
    "feat_lag",
    "feat_returns",
    "feat_log_returns",
    "feat_rolling_mean",
    "feat_rolling_std",
    "feat_volatility",
    "feat_momentum",
    "feat_zscore",
    "feat_normalize",
    "feat_standardize",
    "feat_time",
    "feat_volume",
    "feat_price_transform",
    "feat_concat",
    "feat_select",
]


@pytest.fixture
def prices():
    rng = np.random.default_rng(11)
    return 100 + np.cumsum(rng.normal(0, 0.5, 120)) + 20


@pytest.fixture
def timestamps():
    # hourly bars, on the hour, starting 2020-09-13T12:00:00Z
    return np.arange(120, dtype="int64") * 3600 + 1_600_000_000 + 1600


# ──────────────────────────────────────────────────────────────────────────────
# 1. Descriptor coverage — Requirement 4.3 (at least 15 FE descriptors)
# ──────────────────────────────────────────────────────────────────────────────

class TestDescriptorCoverage:

    def test_all_fifteen_design_blocks_are_declared(self):
        assert [spec.block_id for spec in FEATURE_SPECS] == DESIGN_BLOCK_IDS
        assert len(FEATURE_SPECS) == 15

    def test_block_ids_are_unique_and_indexed(self):
        assert len(FEATURE_SPECS_BY_ID) == len(FEATURE_SPECS)
        for spec in FEATURE_SPECS:
            assert get_feature_spec(spec.block_id) is spec

    def test_every_runtime_ref_resolves_to_a_callable(self):
        for spec in FEATURE_SPECS:
            assert callable(resolve_feature_runtime(spec.runtime_ref)), spec.block_id

    def test_unresolvable_runtime_ref_raises(self):
        with pytest.raises(AttributeError):
            resolve_feature_runtime("FeatureEngine.compute_nothing_at_all")

    def test_every_block_is_feature_engineering_and_emits_a_matrix(self):
        for spec in FEATURE_SPECS:
            assert spec.category == "FEATURE_ENGINEERING"
            assert [port.type for port in spec.outputs] == ["FEATURE_MATRIX"]
            assert spec.inputs, f"{spec.block_id} must declare an input port"

    def test_params_declare_a_control_type_and_help(self):
        for spec in FEATURE_SPECS:
            for param in spec.params:
                assert param.key and param.label and param.help, spec.block_id
                assert param.type is not None

    def test_feat_concat_input_is_variadic(self):
        port = FEATURE_SPECS_BY_ID["feat_concat"].inputs[0]
        assert port.variadic is True
        assert port.type == "FEATURE_MATRIX"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Leakage classification — Requirement 18.1
# ──────────────────────────────────────────────────────────────────────────────

class TestLeakageClassification:

    def test_every_descriptor_publishes_a_leakage_risk(self):
        for spec in FEATURE_SPECS:
            assert isinstance(spec.leakage_risk, LeakageRisk)

    def test_classifications_match_the_design_table(self):
        risk = {spec.block_id: spec.leakage_risk for spec in FEATURE_SPECS}
        assert risk["feat_zscore"] is LeakageRisk.LOW
        assert risk["feat_normalize"] is LeakageRisk.LOW
        assert risk["feat_standardize"] is LeakageRisk.REVIEW_REQUIRED
        for block_id in DESIGN_BLOCK_IDS:
            if block_id in ("feat_zscore", "feat_normalize", "feat_standardize"):
                continue
            assert risk[block_id] is LeakageRisk.NONE, block_id

    def test_global_mode_is_representable_so_validation_can_reject_it(self):
        assert GLOBAL_STATISTIC_BLOCKS == ("feat_zscore", "feat_normalize")
        for block_id in GLOBAL_STATISTIC_BLOCKS:
            mode = FEATURE_SPECS_BY_ID[block_id].param("mode")
            assert mode is not None
            assert "global" in mode.options
            assert mode.default == "rolling"


# ──────────────────────────────────────────────────────────────────────────────
# 3. Warmup / lookback functions
# ──────────────────────────────────────────────────────────────────────────────

class TestWarmupFunctions:

    @pytest.mark.parametrize("block_id,params,expected", [
        ("feat_lag", {"lags": [1, 5, 9]}, 9),
        ("feat_returns", {"periods": [3, 7]}, 7),
        ("feat_log_returns", {}, 1),
        ("feat_rolling_mean", {"window": 30}, 30),
        ("feat_rolling_std", {"window": 14, "ddof": 1}, 14),
        ("feat_volatility", {"window": 20}, 21),      # window + 1
        ("feat_momentum", {"windows": [5, 10, 40]}, 40),
        ("feat_zscore", {"window": 12}, 12),
        ("feat_normalize", {"window": 8}, 8),
        ("feat_standardize", {}, 0),
        ("feat_time", {}, 0),
        ("feat_volume", {"window": 50}, 50),
        ("feat_price_transform", {}, 0),
        ("feat_select", {}, 0),
    ])
    def test_warmup_matches_design_lookback(self, block_id, params, expected):
        assert FEATURE_SPECS_BY_ID[block_id].warmup(params) == expected

    def test_defaults_alone_produce_a_usable_warmup(self):
        for spec in FEATURE_SPECS:
            assert spec.warmup(spec.default_params()) >= 0

    def test_concat_warmup_comes_from_its_inputs(self):
        spec = FEATURE_SPECS_BY_ID["feat_concat"]
        assert spec.warmup({}) == 0
        assert "warmup_from_inputs" in spec.capability_flags


# ──────────────────────────────────────────────────────────────────────────────
# 4. Declarative parameter validation
# ──────────────────────────────────────────────────────────────────────────────

class TestParamValidation:

    def test_valid_params_produce_no_issues(self):
        assert validate_feature_params("feat_lag", {"lags": [1, 2, 3]}) == []
        assert validate_feature_params("feat_zscore", {"window": 20, "mode": "rolling"}) == []

    def test_out_of_range_window_is_rejected(self):
        assert validate_feature_params("feat_rolling_mean", {"window": 900})

    def test_unknown_enum_option_is_rejected(self):
        assert validate_feature_params("feat_normalize", {"window": 20, "method": "quantum"})

    def test_missing_required_param_without_default_is_rejected(self):
        issues = validate_feature_params("feat_select", {})
        assert any("columns" in issue for issue in issues)

    def test_cross_field_rule_ranges_cannot_express(self):
        # ddof must leave a degree of freedom inside the window
        assert validate_feature_params("feat_rolling_std", {"window": 2, "ddof": 1}) == []
        assert validate_feature_params("feat_rolling_std", {"window": 1, "ddof": 1})


# ──────────────────────────────────────────────────────────────────────────────
# 5. Runtime primitives are past-only
# ──────────────────────────────────────────────────────────────────────────────

class TestRuntimePrimitives:

    def test_returns_columns_and_warmup(self, prices):
        out = FeatureEngine.compute_returns(prices, [1, 5])
        assert out.shape == (len(prices), 2)
        assert np.isnan(out[0, 0]) and not np.isnan(out[1, 0])
        assert np.all(np.isnan(out[:5, 1])) and not np.isnan(out[5, 1])
        expected = (prices[5] - prices[0]) / prices[0]
        assert out[5, 1] == pytest.approx(expected)

    def test_rolling_mean_uses_trailing_window_only(self, prices):
        out = FeatureEngine.compute_rolling_mean(prices, 20)
        assert np.all(np.isnan(out[:19]))
        assert out[19] == pytest.approx(np.mean(prices[:20]))
        assert out[50] == pytest.approx(np.mean(prices[31:51]))

    def test_rolling_std_honours_ddof(self, prices):
        pop = FeatureEngine.compute_rolling_std(prices, 20, ddof=0)
        sample = FeatureEngine.compute_rolling_std(prices, 20, ddof=1)
        assert pop[30] == pytest.approx(np.std(prices[11:31]))
        assert sample[30] == pytest.approx(np.std(prices[11:31], ddof=1))
        assert sample[30] > pop[30]

    def test_rolling_std_rejects_degenerate_ddof(self, prices):
        with pytest.raises(ValueError):
            FeatureEngine.compute_rolling_std(prices, 1, ddof=1)

    def test_volatility_annualization_is_opt_in(self, prices):
        log_returns = FeatureEngine.compute_log_returns(prices)
        plain = FeatureEngine.compute_volatility(log_returns, 20)
        annual = FeatureEngine.compute_volatility(log_returns, 20, annualize=True, bars_per_year=4)
        assert annual[60] == pytest.approx(plain[60] * 2.0)

    def test_zscore_rolling_is_past_only_and_global_is_not(self, prices):
        rolling = FeatureEngine.compute_zscore(prices, 20, "rolling")
        assert np.all(np.isnan(rolling[:19]))
        window = prices[11:31]
        assert rolling[30] == pytest.approx((prices[30] - window.mean()) / window.std())

        # A truncated series must not change an earlier rolling value...
        truncated = FeatureEngine.compute_zscore(prices[:60], 20, "rolling")
        assert truncated[40] == pytest.approx(rolling[40])
        # ...but it does change a global one, which is exactly the leak.
        assert FeatureEngine.compute_zscore(prices[:60], 20, "global")[40] != pytest.approx(
            FeatureEngine.compute_zscore(prices, 20, "global")[40]
        )

    def test_zscore_expanding_uses_history_up_to_t(self, prices):
        out = FeatureEngine.compute_zscore(prices, 20, "expanding")
        segment = prices[:31]
        assert out[30] == pytest.approx((prices[30] - segment.mean()) / segment.std())

    def test_zscore_rejects_unknown_mode(self, prices):
        with pytest.raises(ValueError):
            FeatureEngine.compute_zscore(prices, 20, "sideways")

    def test_normalize_minmax_and_robust(self, prices):
        minmax = FeatureEngine.compute_normalize(prices, 20, "minmax", "rolling")
        assert np.all(np.isnan(minmax[:19]))
        finite = minmax[19:]
        assert np.nanmin(finite) >= 0.0 and np.nanmax(finite) <= 1.0
        robust = FeatureEngine.compute_normalize(prices, 20, "robust", "rolling")
        window = prices[11:31]
        iqr = np.percentile(window, 75) - np.percentile(window, 25)
        assert robust[30] == pytest.approx((prices[30] - np.median(window)) / iqr)

    def test_normalize_rejects_unknown_method(self, prices):
        with pytest.raises(ValueError):
            FeatureEngine.compute_normalize(prices, 20, "softmax", "rolling")

    def test_time_features_are_derived_from_the_bar_itself(self, timestamps):
        values, columns = FeatureEngine.compute_time_features(
            timestamps, ["hour", "dow", "dom", "month", "session"]
        )
        assert columns == ["time_hour", "time_dow", "time_dom", "time_month", "time_session"]
        assert values.shape == (len(timestamps), 5)
        assert values[:, 0].min() >= 0 and values[:, 0].max() < 24
        assert set(np.unique(values[:, 1])).issubset({0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0})
        assert set(np.unique(values[:, 4])).issubset({0.0, 1.0, 2.0, 3.0})
        # 2020-09-13T12:26:40Z was a Sunday
        assert values[0, 1] == 6.0
        assert values[0, 2] == 13.0
        assert values[0, 3] == 9.0
        # milliseconds are accepted too, and give the same answer
        ms_values, _ = FeatureEngine.compute_time_features(timestamps * 1000, ["hour"])
        assert np.allclose(ms_values[:, 0], values[:, 0])

    def test_time_features_reject_unknown_part(self, timestamps):
        with pytest.raises(ValueError):
            FeatureEngine.compute_time_features(timestamps, ["fortnight"])

    def test_price_transforms(self):
        open_ = np.array([10.0, 20.0])
        high = np.array([12.0, 24.0])
        low = np.array([8.0, 16.0])
        close = np.array([11.0, 22.0])
        assert np.allclose(
            FeatureEngine.compute_price_transform(open_, high, low, close, "hl2"), [10.0, 20.0]
        )
        assert np.allclose(
            FeatureEngine.compute_price_transform(open_, high, low, close, "hlc3"),
            (high + low + close) / 3,
        )
        assert np.allclose(
            FeatureEngine.compute_price_transform(open_, high, low, close, "ohlc4"),
            (open_ + high + low + close) / 4,
        )
        assert np.allclose(
            FeatureEngine.compute_price_transform(open_, high, low, close, "typical"),
            FeatureEngine.compute_price_transform(open_, high, low, close, "hlc3"),
        )
        assert np.allclose(
            FeatureEngine.compute_price_transform(None, None, None, close, "log"), np.log(close)
        )
        with pytest.raises(ValueError):
            FeatureEngine.compute_price_transform(open_, high, low, close, "vwap")


# ──────────────────────────────────────────────────────────────────────────────
# 6. FeatureMatrix contract and timestamp alignment
# ──────────────────────────────────────────────────────────────────────────────

class TestFeatureMatrix:

    def test_shape_and_index_invariants(self):
        with pytest.raises(ValueError):
            FeatureMatrix(index=np.arange(3), columns=["a", "b"], values=np.zeros((3, 1)))
        with pytest.raises(ValueError):
            FeatureMatrix(index=np.array([3, 1, 2]), columns=["a"], values=np.zeros((3, 1)))
        with pytest.raises(ValueError):
            FeatureMatrix(index=np.arange(2), columns=["a", "a"], values=np.zeros((2, 2)))

    def test_warmup_offset_is_the_worst_column(self, prices, timestamps):
        matrix = build_feature_matrix(
            timestamps,
            ["mean_5", "mean_40"],
            [
                FeatureEngine.compute_rolling_mean(prices, 5),
                FeatureEngine.compute_rolling_mean(prices, 40),
            ],
            node_id="n_fe1",
        )
        assert matrix.column_warmup["mean_5"] == 4
        assert matrix.column_warmup["mean_40"] == 39
        assert matrix.warmup_offset == 39
        assert matrix.provenance["mean_5"] == "n_fe1"
        assert matrix.usable_slice().n_rows == matrix.n_rows - 39

    def test_concat_aligns_by_timestamp_not_position(self, prices, timestamps):
        left = build_feature_matrix(
            timestamps, ["a"], [FeatureEngine.compute_rolling_mean(prices, 5)], node_id="n_a"
        )
        # right starts 3 bars later: positional stacking would shift column a by 3
        right = build_feature_matrix(
            timestamps[3:], ["b"], [FeatureEngine.compute_rolling_mean(prices[3:], 10)], node_id="n_b"
        )
        merged = FeatureEngine.concat_feature_matrices([left, right])

        assert merged.columns == ["a", "b"]
        assert merged.n_rows == len(timestamps) - 3
        assert merged.index[0] == timestamps[3]
        for row, stamp in enumerate(merged.index.tolist()):
            assert merged.values[row, 0] == pytest.approx(
                left.column("a")[left.index.tolist().index(stamp)], nan_ok=True
            )
            assert merged.values[row, 1] == pytest.approx(
                right.column("b")[right.index.tolist().index(stamp)], nan_ok=True
            )
        assert merged.provenance["a"] == "n_a"
        assert merged.warmup_offset == max(merged.column_warmup.values())

    def test_concat_translates_warmup_onto_the_merged_index(self, prices, timestamps):
        left = build_feature_matrix(
            timestamps, ["a"], [FeatureEngine.compute_rolling_mean(prices, 5)]
        )
        right = build_feature_matrix(
            timestamps[3:], ["b"], [FeatureEngine.compute_rolling_mean(prices[3:], 10)]
        )
        merged = FeatureEngine.concat_feature_matrices([left, right])
        # a is trustworthy from timestamps[4]; that is row 1 of the merged index
        assert merged.column_warmup["a"] == 1
        # b is trustworthy from timestamps[3 + 9]; row 9 of the merged index
        assert merged.column_warmup["b"] == 9
        assert not np.isnan(merged.values[merged.warmup_offset]).any()

    def test_concat_needs_two_inputs_and_a_shared_index(self, prices, timestamps):
        one = build_feature_matrix(timestamps, ["a"], [prices])
        with pytest.raises(ValueError):
            FeatureEngine.concat_feature_matrices([one])
        disjoint = build_feature_matrix(timestamps + 10 ** 9, ["b"], [prices])
        with pytest.raises(ValueError):
            FeatureEngine.concat_feature_matrices([one, disjoint])

    def test_concat_disambiguates_duplicate_column_names(self, prices, timestamps):
        first = build_feature_matrix(timestamps, ["a"], [prices])
        second = build_feature_matrix(timestamps, ["a"], [prices * 2])
        merged = FeatureEngine.concat_feature_matrices([first, second])
        assert merged.columns == ["a", "a_2"]

    def test_select_keeps_requested_order_and_rejects_unknown(self, prices, timestamps):
        matrix = build_feature_matrix(timestamps, ["a", "b", "c"], [prices, prices * 2, prices * 3])
        picked = FeatureEngine.select_feature_columns(matrix, ["c", "a"])
        assert picked.columns == ["c", "a"]
        assert np.allclose(picked.column("c"), prices * 3)
        with pytest.raises(ValueError):
            FeatureEngine.select_feature_columns(matrix, ["nope"])
        with pytest.raises(ValueError):
            FeatureEngine.select_feature_columns(matrix, [])


# ──────────────────────────────────────────────────────────────────────────────
# 7. Standardize is fit on the train split only
# ──────────────────────────────────────────────────────────────────────────────

class TestStandardize:

    @pytest.fixture
    def matrix(self, prices, timestamps):
        return build_feature_matrix(timestamps, ["a"], [prices])

    def test_scaler_uses_train_rows_only(self, matrix, prices):
        out = FeatureEngine.compute_standardize(matrix, fit_range=(0, 60))
        train = prices[:60]
        assert out.columns == ["a_std"]
        assert out.column("a_std")[10] == pytest.approx((prices[10] - train.mean()) / train.std())
        # Rows outside the fit range are transformed, not re-fitted
        assert out.column("a_std")[100] == pytest.approx((prices[100] - train.mean()) / train.std())

    def test_missing_fit_range_is_refused(self, matrix):
        with pytest.raises(ValueError):
            FeatureEngine.compute_standardize(matrix)

    def test_invalid_fit_range_is_refused(self, matrix):
        with pytest.raises(ValueError):
            FeatureEngine.compute_standardize(matrix, fit_range=(60, 10))
        with pytest.raises(ValueError):
            FeatureEngine.compute_standardize(matrix, fit_range=(0, 10_000))

    def test_fit_on_anything_but_train_split_is_refused(self, matrix):
        with pytest.raises(ValueError):
            FeatureEngine.compute_standardize(matrix, fit_range=(0, 60), fit_on="all")


# ──────────────────────────────────────────────────────────────────────────────
# 8. The existing pipeline is untouched
# ──────────────────────────────────────────────────────────────────────────────

class TestExistingPipelineIntact:

    def test_create_feature_matrix_still_produces_its_25_columns(self, prices):
        matrix, names = FeatureEngine.create_feature_matrix(
            prices=prices, volumes=np.full(len(prices), 1000.0)
        )
        assert len(names) == 25
        assert matrix.shape == (len(prices), 25)
        assert names[:3] == ["rsi", "ema_20", "ema_50"]
        assert "log_returns" in names and "price_roll_std_50" in names

    def test_volatility_default_call_is_unchanged(self, prices):
        log_returns = FeatureEngine.compute_log_returns(prices)
        matrix, names = FeatureEngine.create_feature_matrix(prices=prices, volumes=None)
        assert np.allclose(
            FeatureEngine.compute_volatility(log_returns, 20),
            matrix[:, names.index("volatility_20")],
            equal_nan=True,
        )

    def test_toggles_still_drop_their_feature_groups(self, prices):
        _, names = FeatureEngine.create_feature_matrix(
            prices=prices,
            volumes=None,
            include_indicators=False,
            include_volume_features=False,
        )
        assert "rsi" not in names
        assert "volume_mean" not in names
        assert "log_returns" in names
