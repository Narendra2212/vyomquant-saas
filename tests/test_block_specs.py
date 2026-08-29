"""
tests/test_block_specs.py

Unit tests for the DATA, MATH, LOGIC and ACTION descriptors in
backend_app/backend/strategy_dag/block_specs.py.

Covers:
  - every runtime_ref (and every streaming counterpart) resolves to a real callable, so
    build_registry() can never advertise a block the platform cannot run
  - ohlcv_feed declares NO exchange param, and symbol / timeframe are required with no
    default (SB-06, Requirements 12.2, 12.3)
  - every ACTION descriptor is TERMINAL with an empty successor set, carries no
    traded-asset param, and requires quantity_type / quantity with no default
    (Requirements 4.10, 12.8, 5.4)
  - ACTION descriptors are generated from the real exchange_executor.OrderType, with
    coverage asserted in both directions
  - MATH_SPECS and LOGIC_SPECS are non-empty and match the design's block sets
  - the numeric firewall: no infinity leaves a kernel, NaN propagates, division by zero /
    negative roots / non-positive logs yield NaN with a recorded issue
  - crosses are silent through warmup and shift is backward only
"""
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend_app.backend.exchange_executor import OrderType
from backend_app.backend.strategy_dag import block_specs as bs
from backend_app.backend.strategy_dag.block_specs import (
    DATA_SPECS,
    LOGIC_SPECS,
    MATH_SPECS,
    QUANTITY_TYPES,
    BlockSpec,
    ExecutionSemantics,
    ParamSpec,
    ParamType,
    action_descriptors_for,
    action_specs,
    all_block_specs,
    get_block_spec,
    resolve_block_runtime,
    specs_in_category,
    validate_block_params,
)
from backend_app.backend.strategy_dag.schema import BlockCategory, PortType

# design.md -> Math blocks
DESIGN_MATH_IDS = [
    "add", "subtract", "multiply", "divide", "modulo", "min", "max",
    "abs", "round", "floor", "ceil", "sqrt", "log", "exp", "negate", "clamp",
    "constant", "shift",
]

# design.md -> Logic blocks
DESIGN_LOGIC_IDS = [
    "and", "or", "not",
    "gt", "lt", "gte", "lte", "eq", "neq",
    "cross_above", "cross_below", "between", "if_then_else", "to_signal",
]

# design.md -> Action blocks
DESIGN_ACTION_IDS = [
    "action_buy_market", "action_sell_market", "action_close_position",
    "action_buy_limit", "action_sell_limit",
    "action_stop_market", "action_stop_limit",
    "action_take_profit_market", "action_take_profit_limit",
]

DESIGN_DATA_IDS = ["ohlcv_feed", "live_ticker", "orderbook_imbalance"]


def maybe_nan_series(bound: float, min_size: int = 1, max_size: int = 40):
    """Bounded finite floats with NaN mixed in, which is what a warmup region looks like.

    Infinity is excluded from the *inputs* on purpose: the kernels are asserted never to
    *emit* one, and feeding one in would test the caller rather than the firewall.
    """
    element = st.one_of(
        st.just(float("nan")),
        st.floats(
            min_value=-bound, max_value=bound, allow_nan=False, allow_infinity=False
        ),
    )
    return st.lists(element, min_size=min_size, max_size=max_size)


@pytest.fixture(scope="module")
def actions():
    return action_specs()


@pytest.fixture(scope="module")
def every_spec():
    return all_block_specs()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Descriptor coverage
# ──────────────────────────────────────────────────────────────────────────────

class TestDescriptorCoverage:

    def test_math_specs_are_non_empty_and_match_the_design(self):
        assert MATH_SPECS, "MATH_SPECS is empty; the palette section would be blank (SB-03)"
        assert [spec.block_id for spec in MATH_SPECS] == DESIGN_MATH_IDS

    def test_logic_specs_are_non_empty_and_match_the_design(self):
        assert LOGIC_SPECS, "LOGIC_SPECS is empty; the palette section would be blank"
        assert [spec.block_id for spec in LOGIC_SPECS] == DESIGN_LOGIC_IDS

    def test_data_specs_are_the_three_design_descriptors(self):
        assert [spec.block_id for spec in DATA_SPECS] == DESIGN_DATA_IDS

    def test_action_specs_are_non_empty_and_match_the_design(self, actions):
        assert [spec.block_id for spec in actions] == DESIGN_ACTION_IDS

    def test_every_declared_category_is_non_empty(self):
        for category in (
            BlockCategory.DATA,
            BlockCategory.MATH,
            BlockCategory.LOGIC,
            BlockCategory.ACTION,
        ):
            assert specs_in_category(category), f"{category.value} holds no descriptors"

    def test_block_ids_are_unique_across_the_four_families(self, every_spec):
        ids = [spec.block_id for spec in every_spec]
        assert len(set(ids)) == len(ids)

    def test_lookup_returns_the_same_object(self):
        assert get_block_spec("ohlcv_feed") is DATA_SPECS[0]
        assert get_block_spec("action_buy_market").block_id == "action_buy_market"
        with pytest.raises(KeyError):
            get_block_spec("no_such_block")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Runtime integrity — Requirements 4.7, 4.8
# ──────────────────────────────────────────────────────────────────────────────

class TestRuntimeIntegrity:

    def test_every_runtime_ref_resolves_to_a_callable(self, every_spec):
        for spec in every_spec:
            for ref in spec.runtime_refs():
                assert callable(resolve_block_runtime(ref)), (
                    f"{spec.block_id} advertises runtime_ref {ref!r} that is not callable"
                )

    def test_resolve_runtime_helper_agrees_with_the_module_lookup(self):
        spec = get_block_spec("add")
        assert spec.resolve_runtime() is bs.math_add

    def test_data_runtime_refs_point_at_the_real_market_data_engine(self):
        from backend_app.backend.data_seeking_engine import DataEngine

        feed = get_block_spec("ohlcv_feed")
        assert feed.resolve_runtime() is DataEngine.fetch_historical_ohlcv
        assert (
            resolve_block_runtime(feed.streaming_runtime_ref)
            is DataEngine.stream_live_ohlcv
        )

    def test_action_runtime_refs_point_at_the_real_order_path(self, actions):
        from backend_app.backend.exchange_executor import CCXTExchangeExecutor

        for spec in actions:
            assert spec.resolve_runtime() is CCXTExchangeExecutor.place_order

    def test_an_unresolvable_runtime_ref_raises_naming_the_offender(self):
        with pytest.raises(AttributeError) as excinfo:
            resolve_block_runtime("block_specs.does_not_exist")
        assert "block_specs.does_not_exist" in str(excinfo.value)

        with pytest.raises(AttributeError) as excinfo:
            resolve_block_runtime("SomeOtherModule.thing")
        assert "SomeOtherModule" in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# 3. DATA blocks stay exchange-agnostic — SB-06, Requirements 12.2, 12.3
# ──────────────────────────────────────────────────────────────────────────────

class TestDataBlocksAreExchangeAgnostic:

    def test_ohlcv_feed_declares_no_exchange_param(self):
        feed = get_block_spec("ohlcv_feed")
        keys = {param.key for param in feed.params}
        assert "exchange" not in keys
        assert not keys & {"exchange_id", "api_key", "secret", "account_id"}

    def test_no_data_block_declares_an_exchange_param(self):
        for spec in DATA_SPECS:
            assert "exchange" not in {param.key for param in spec.params}, spec.block_id

    def test_ohlcv_feed_symbol_and_timeframe_are_required_with_no_default(self):
        feed = get_block_spec("ohlcv_feed")
        for key in ("symbol", "timeframe"):
            param = feed.param(key)
            assert param is not None, f"ohlcv_feed does not declare {key}"
            assert param.required is True
            assert param.default is None

    def test_every_data_block_requires_a_symbol_with_no_default(self):
        for spec in DATA_SPECS:
            param = spec.param("symbol")
            assert param is not None, f"{spec.block_id} declares no symbol"
            assert param.required is True and param.default is None

    def test_any_declared_timeframe_is_required_with_no_default(self, every_spec):
        for spec in every_spec:
            param = spec.param("timeframe")
            if param is not None:
                assert param.required is True and param.default is None, spec.block_id

    def test_ohlcv_feed_exposes_one_port_per_series_plus_the_frame(self):
        feed = get_block_spec("ohlcv_feed")
        assert [port.name for port in feed.outputs] == [
            "frame", "open", "high", "low", "close", "volume",
        ]
        assert feed.output_port("frame").type is PortType.OHLCV_FRAME
        assert feed.output_port("close").type is PortType.PRICE_SERIES
        assert feed.output_port("volume").type is PortType.SCALAR_SERIES
        assert feed.inputs == ()

    def test_a_symbol_free_data_node_is_rejected_by_param_validation(self):
        feed = get_block_spec("ohlcv_feed")
        issues = validate_block_params(
            feed, {"symbol": None, "timeframe": None, "market_type": "spot", "mode": "streaming"}
        )
        assert any("symbol" in issue for issue in issues)
        assert any("timeframe" in issue for issue in issues)

    def test_a_fully_configured_data_node_validates(self):
        feed = get_block_spec("ohlcv_feed")
        assert validate_block_params(
            feed,
            {
                "symbol": "ETH/USDT",
                "timeframe": "15m",
                "market_type": "spot",
                "mode": "streaming",
            },
        ) == []

    def test_declaring_an_exchange_param_is_structurally_impossible(self):
        with pytest.raises(ValueError) as excinfo:
            ParamSpec(key="exchange", label="Exchange", type=ParamType.TEXT)
        assert "SB-06" in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# 4. ACTION blocks — Requirements 4.10, 12.8, 5.4
# ──────────────────────────────────────────────────────────────────────────────

class TestActionBlocks:

    def test_every_action_is_terminal_with_no_outputs(self, actions):
        for spec in actions:
            assert spec.execution_semantics is ExecutionSemantics.TERMINAL, spec.block_id
            assert spec.is_terminal
            assert spec.outputs == (), spec.block_id

    def test_every_action_has_an_empty_successor_set(self, actions):
        for spec in actions:
            assert spec.allowed_successor_categories == (), spec.block_id

    def test_every_action_carries_no_traded_asset_param(self, actions):
        forbidden = {"symbol", "asset", "pair", "market", "ticker", "exchange"}
        for spec in actions:
            assert not {param.key for param in spec.params} & forbidden, spec.block_id

    def test_every_action_requires_quantity_type_and_quantity_with_no_default(self, actions):
        for spec in actions:
            for key in ("quantity_type", "quantity"):
                param = spec.param(key)
                assert param is not None, f"{spec.block_id} does not declare {key}"
                assert param.required is True, f"{spec.block_id}.{key} is not required"
                assert param.default is None, f"{spec.block_id}.{key} has a default"

    def test_action_price_and_trigger_params_have_no_default(self, actions):
        for spec in actions:
            for key in ("price", "trigger_price", "limit_price"):
                param = spec.param(key)
                if param is not None:
                    assert param.required is True and param.default is None, (
                        f"{spec.block_id}.{key}"
                    )

    def test_every_action_accepts_exactly_one_signal_input(self, actions):
        for spec in actions:
            assert [port.name for port in spec.inputs] == ["signal"], spec.block_id
            assert spec.input_port("signal").type is PortType.SIGNAL
            assert spec.input_port("signal").required is True

    def test_actions_only_accept_logic_predecessors(self, actions):
        for spec in actions:
            assert spec.allowed_predecessor_categories == (BlockCategory.LOGIC,), spec.block_id

    def test_quantity_type_options_are_the_design_set(self):
        spec = get_block_spec("action_buy_market")
        assert spec.param("quantity_type").options == QUANTITY_TYPES

    def test_close_position_is_reduce_only_and_not_authorable(self):
        spec = get_block_spec("action_close_position")
        assert spec.metadata["reduce_only_forced"] is True
        assert spec.param("reduce_only") is None
        assert "percent_of_position" in spec.param("quantity_type").options

    def test_action_descriptors_are_generated_from_the_real_order_type_enum(self, actions):
        declared = {spec.metadata["order_type"] for spec in actions}
        supported = {member.value for member in OrderType}
        assert declared == supported

    def test_each_order_type_yields_at_least_one_descriptor(self):
        for member in OrderType:
            specs = action_descriptors_for(member)
            assert specs, member.value
            assert all(spec.metadata["order_type"] == member.value for spec in specs)

    def test_an_unsupported_order_type_cannot_produce_a_block(self):
        with pytest.raises(ValueError) as excinfo:
            action_descriptors_for("iceberg")
        assert "iceberg" in str(excinfo.value)

    def test_a_new_order_type_without_a_variant_fails_loudly(self):
        class FakeOrderType:
            value = "twap"

        with pytest.raises(ValueError) as excinfo:
            action_specs([FakeOrderType()])
        assert "twap" in str(excinfo.value)

    def test_an_action_missing_its_size_is_rejected_by_param_validation(self):
        spec = get_block_spec("action_buy_market")
        issues = validate_block_params(spec, {"quantity_type": "base_amount"})
        assert any("quantity" in issue for issue in issues)

    def test_a_negative_size_is_rejected(self):
        spec = get_block_spec("action_buy_market")
        issues = validate_block_params(
            spec, {"quantity_type": "base_amount", "quantity": -1}
        )
        assert any("at least 0" in issue for issue in issues)

    def test_an_unknown_quantity_type_is_rejected(self):
        spec = get_block_spec("action_buy_market")
        issues = validate_block_params(
            spec, {"quantity_type": "all_in", "quantity": 1}
        )
        assert any("quantity_type" in issue for issue in issues)

    def test_a_terminal_block_cannot_declare_outputs(self):
        from backend_app.backend.strategy_dag.schema import Port

        with pytest.raises(ValueError) as excinfo:
            BlockSpec(
                block_id="bad_action",
                display_name="Bad",
                category=BlockCategory.ACTION,
                description="",
                inputs=(),
                outputs=(Port(name="out", type=PortType.TRADE_INTENT),),
                params=(),
                warmup_fn=lambda params: 0,
                runtime_ref="CCXTExchangeExecutor.place_order",
                execution_semantics=ExecutionSemantics.TERMINAL,
            )
        assert "TERMINAL" in str(excinfo.value)

    def test_an_action_cannot_declare_a_traded_asset_param(self):
        with pytest.raises(ValueError) as excinfo:
            BlockSpec(
                block_id="bad_action",
                display_name="Bad",
                category=BlockCategory.ACTION,
                description="",
                inputs=(),
                outputs=(),
                params=(
                    ParamSpec(key="pair", label="Pair", type=ParamType.TEXT, required=False),
                ),
                warmup_fn=lambda params: 0,
                runtime_ref="CCXTExchangeExecutor.place_order",
                execution_semantics=ExecutionSemantics.TERMINAL,
            )
        assert "12.8" in str(excinfo.value)


# ──────────────────────────────────────────────────────────────────────────────
# 5. MATH kernels and the numeric firewall
# ──────────────────────────────────────────────────────────────────────────────

class TestMathKernels:

    def test_add_and_multiply_are_variadic(self):
        assert np.allclose(bs.math_add([1, 2], [3, 4], [5, 6]), [9, 12])
        assert np.allclose(bs.math_multiply([1, 2], [3, 4], [2, 2]), [6, 16])

    def test_division_by_zero_yields_nan_not_infinity(self):
        issues = []
        out = bs.math_divide([1.0, 2.0], [0.0, 2.0], node_id="n_1", issues=issues)
        assert math.isnan(out[0])
        assert out[1] == 1.0
        assert not np.isinf(out).any()
        assert issues[0]["code"] == "DIVISION_BY_ZERO"
        assert issues[0]["node_id"] == "n_1"
        assert issues[0]["bar"] == 0

    def test_modulo_by_zero_yields_nan(self):
        out = bs.math_modulo([5.0, 5.0], [0.0, 2.0])
        assert math.isnan(out[0]) and out[1] == 1.0

    def test_negative_square_root_yields_nan_with_an_issue(self):
        issues = []
        out = bs.math_sqrt([-4.0, 9.0], node_id="n_2", issues=issues)
        assert math.isnan(out[0]) and out[1] == 3.0
        assert issues[0]["code"] == "NEGATIVE_ROOT"

    def test_non_positive_log_yields_nan_with_an_issue(self):
        issues = []
        out = bs.math_log([0.0, math.e], node_id="n_3", issues=issues)
        assert math.isnan(out[0])
        assert out[1] == pytest.approx(1.0)
        assert issues[0]["code"] == "NON_POSITIVE_LOG"

    def test_log_base_ten(self):
        assert bs.math_log([1000.0], base="10")[0] == pytest.approx(3.0)

    def test_exp_overflow_yields_nan_not_infinity(self):
        out = bs.math_exp([1000.0])
        assert math.isnan(out[0])

    def test_nan_propagates_within_the_series(self):
        out = bs.math_add([1.0, np.nan, 3.0], [1.0, 1.0, 1.0])
        assert out[0] == 2.0 and math.isnan(out[1]) and out[2] == 4.0

    def test_scalars_broadcast_onto_the_series_length(self):
        assert np.allclose(bs.math_multiply([1.0, 2.0, 3.0], 2.0), [2.0, 4.0, 6.0])

    def test_mismatched_lengths_are_refused(self):
        with pytest.raises(ValueError) as excinfo:
            bs.math_add([1.0, 2.0], [1.0, 2.0, 3.0], node_id="n_4")
        assert "n_4" in str(excinfo.value)

    def test_shift_is_backward_only(self):
        out = bs.math_shift([1.0, 2.0, 3.0], bars=1)
        assert math.isnan(out[0])
        assert np.allclose(out[1:], [1.0, 2.0])
        with pytest.raises(ValueError):
            bs.math_shift([1.0, 2.0, 3.0], bars=-1)

    def test_clamp_constrains_and_its_cross_field_rule_bites(self):
        assert np.allclose(bs.math_clamp([-5.0, 5.0, 50.0], 0, 10), [0.0, 5.0, 10.0])
        spec = get_block_spec("clamp")
        assert validate_block_params(spec, {"lower": 10, "upper": 1})
        assert validate_block_params(spec, {"lower": 1, "upper": 10}) == []

    def test_constant_must_be_finite(self):
        assert bs.math_constant(70) == 70.0
        with pytest.raises(ValueError):
            bs.math_constant(float("inf"))

    def test_constant_block_is_a_source_with_a_scalar_output(self):
        spec = get_block_spec("constant")
        assert spec.inputs == ()
        assert spec.output_port("value").type is PortType.SCALAR
        assert spec.param("value").required and spec.param("value").default is None

    @settings(max_examples=200, deadline=None)
    @given(maybe_nan_series(1e12), maybe_nan_series(1e12))
    def test_no_infinity_ever_leaves_a_math_kernel(self, left, right):
        length = min(len(left), len(right))
        a, b = left[:length], right[:length]
        for out in (
            bs.math_add(a, b),
            bs.math_subtract(a, b),
            bs.math_multiply(a, b),
            bs.math_divide(a, b),
            bs.math_modulo(a, b),
            bs.math_exp(a),
            bs.math_log(a),
            bs.math_sqrt(a),
        ):
            assert not np.isinf(np.asarray(out, dtype=float)).any()

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=40,
        ),
        st.integers(min_value=0, max_value=40),
    )
    def test_shift_never_reads_the_future(self, series, bars):
        out = bs.math_shift(series, bars=bars)
        assert len(out) == len(series)
        for index in range(len(series)):
            if index < bars:
                assert math.isnan(out[index])
            else:
                assert out[index] == pytest.approx(series[index - bars])


# ──────────────────────────────────────────────────────────────────────────────
# 6. LOGIC kernels
# ──────────────────────────────────────────────────────────────────────────────

class TestLogicKernels:

    def test_gates_are_variadic_and_elementwise(self):
        assert list(bs.logic_and([True, True], [True, False], [True, True])) == [True, False]
        assert list(bs.logic_or([False, False], [True, False])) == [True, False]
        assert list(bs.logic_not([True, False])) == [False, True]

    def test_comparators_report_false_on_an_undefined_bar(self):
        left = [np.nan, 2.0, 3.0]
        right = [1.0, 1.0, 5.0]
        assert list(bs.logic_gt(left, right)) == [False, True, False]
        assert list(bs.logic_lte(left, right)) == [False, False, True]

    def test_equality_uses_a_tolerance(self):
        assert bool(bs.logic_eq([0.1 + 0.2], [0.3])[0]) is True
        assert bool(bs.logic_neq([0.1 + 0.2], [0.3])[0]) is False

    def test_cross_above_fires_only_on_the_flip(self):
        fast = [1.0, 1.0, 3.0, 4.0]
        slow = [2.0, 2.0, 2.0, 2.0]
        assert list(bs.logic_cross_above(fast, slow)) == [False, False, True, False]

    def test_cross_below_fires_only_on_the_flip(self):
        fast = [3.0, 3.0, 1.0, 0.5]
        slow = [2.0, 2.0, 2.0, 2.0]
        assert list(bs.logic_cross_below(fast, slow)) == [False, False, True, False]

    def test_the_warmup_region_never_signals(self):
        fast = [np.nan, np.nan, 3.0, 4.0]
        slow = [2.0, 2.0, 2.0, 2.0]
        crosses = bs.logic_cross_above(fast, slow)
        assert crosses[0] is np.False_ or crosses[0] == False  # noqa: E712
        assert not crosses[1]
        assert not crosses[2], "a cross out of NaN would fire a trade on undefined data"

    def test_between_respects_inclusivity(self):
        assert list(bs.logic_between([1.0, 5.0], 1.0, 5.0, inclusive=True)) == [True, True]
        assert list(bs.logic_between([1.0, 3.0], 1.0, 5.0, inclusive=False)) == [False, True]

    def test_if_then_else_selects_per_bar(self):
        out = bs.logic_if_then_else([True, False], [1.0, 1.0], [9.0, 9.0])
        assert list(out) == [1.0, 9.0]

    def test_to_signal_is_signed_bounded_and_needs_a_direction(self):
        assert list(bs.logic_to_signal([True, False], "long")) == [1.0, 0.0]
        assert list(bs.logic_to_signal([True, False], "short", 0.5)) == [-0.5, 0.0]
        with pytest.raises(ValueError):
            bs.logic_to_signal([True], "sideways")
        with pytest.raises(ValueError):
            bs.logic_to_signal([True], "long", 1.5)

    def test_to_signal_direction_has_no_default(self):
        spec = get_block_spec("to_signal")
        param = spec.param("direction")
        assert param.required is True and param.default is None
        assert spec.output_port("out").type is PortType.SIGNAL
        assert spec.allowed_successor_categories == (BlockCategory.ACTION,)

    def test_comparators_emit_boolean_series(self):
        for block_id in ("gt", "lt", "gte", "lte", "eq", "neq"):
            spec = get_block_spec(block_id)
            assert spec.output_port("out").type is PortType.BOOLEAN_SERIES
            assert spec.input_port("left").type is PortType.SCALAR_SERIES
            assert spec.input_port("right").type is PortType.SCALAR_SERIES

    def test_boolean_gates_take_boolean_inputs(self):
        for block_id in ("and", "or", "not"):
            spec = get_block_spec(block_id)
            assert spec.input_port("a").type is PortType.BOOLEAN_SERIES

    @settings(max_examples=200, deadline=None)
    @given(maybe_nan_series(1e6, min_size=2), maybe_nan_series(1e6, min_size=2))
    def test_a_cross_never_fires_on_an_undefined_bar(self, fast, slow):
        length = min(len(fast), len(slow))
        a, b = np.asarray(fast[:length]), np.asarray(slow[:length])
        undefined = np.isnan(a) | np.isnan(b)
        for crosses in (bs.logic_cross_above(a, b), bs.logic_cross_below(a, b)):
            assert not crosses[0], "bar 0 has no previous bar to compare"
            assert not (crosses & undefined).any()
            assert not (crosses[1:] & undefined[:-1]).any()


# ──────────────────────────────────────────────────────────────────────────────
# 7. Order book imbalance kernel
# ──────────────────────────────────────────────────────────────────────────────

class TestOrderBookImbalance:

    def test_imbalance_is_signed_and_bounded(self):
        result = bs.order_book_imbalance({"bids": [[1, 3]], "asks": [[2, 1]]})
        assert result["imbalance"] == pytest.approx(0.5)
        assert result["bid_volume"] == 3.0 and result["ask_volume"] == 1.0

        mirrored = bs.order_book_imbalance({"bids": [[1, 1]], "asks": [[2, 3]]})
        assert mirrored["imbalance"] == pytest.approx(-0.5)

    def test_depth_limits_the_levels_summed(self):
        book = {"bids": [[1, 1], [0.9, 100]], "asks": [[2, 1]]}
        assert bs.order_book_imbalance(book, depth=1)["bid_volume"] == 1.0
        assert bs.order_book_imbalance(book, depth=2)["bid_volume"] == 101.0

    def test_an_empty_book_is_unknown_not_balanced(self):
        assert math.isnan(bs.order_book_imbalance({"bids": [], "asks": []})["imbalance"])

    def test_malformed_levels_are_skipped_rather_than_raising(self):
        book = {"bids": [[1, "x"], [1, 2], [1]], "asks": [[2, 2]]}
        assert bs.order_book_imbalance(book)["bid_volume"] == 2.0

    def test_the_descriptor_ports_match_the_kernel_keys(self):
        spec = get_block_spec("orderbook_imbalance")
        keys = set(bs.order_book_imbalance({"bids": [[1, 1]], "asks": [[1, 1]]}))
        assert {port.name for port in spec.outputs} == keys


# ──────────────────────────────────────────────────────────────────────────────
# 8. Descriptor invariants shared by every family
# ──────────────────────────────────────────────────────────────────────────────

class TestSharedInvariants:

    def test_non_terminal_blocks_declare_at_least_one_output(self, every_spec):
        for spec in every_spec:
            if not spec.is_terminal:
                assert spec.outputs, spec.block_id

    def test_warmup_is_a_non_negative_integer_for_declared_defaults(self, every_spec):
        for spec in every_spec:
            warmup = spec.warmup()
            assert isinstance(warmup, int) and warmup >= 0, spec.block_id

    def test_shift_warmup_tracks_its_bars_param(self):
        assert get_block_spec("shift").warmup({"bars": 20}) == 20
        assert get_block_spec("cross_above").warmup() == 1

    def test_ohlcv_feed_warmup_defers_to_the_compiler_when_blank(self):
        feed = get_block_spec("ohlcv_feed")
        assert feed.warmup() == 0
        assert feed.warmup({"warmup_bars": 500}) == 500

    def test_every_select_param_declares_options(self, every_spec):
        for spec in every_spec:
            for param in spec.params:
                if param.type in (ParamType.SELECT, ParamType.MULTISELECT):
                    assert param.options, f"{spec.block_id}.{param.key}"

    def test_every_param_carries_help_and_an_example(self, every_spec):
        for spec in every_spec:
            for param in spec.params:
                assert param.help, f"{spec.block_id}.{param.key} has no help text"
                assert param.label, f"{spec.block_id}.{param.key} has no label"

    def test_descriptors_serialize_for_the_registry_response(self, every_spec):
        for spec in every_spec:
            payload = spec.to_dict()
            assert payload["block_id"] == spec.block_id
            assert payload["category"] == spec.category.value
            assert payload["runtime_ref"] == spec.runtime_ref
            assert isinstance(payload["params"], list)

    def test_runtime_kwargs_omit_params_the_callable_does_not_accept(self):
        feed = get_block_spec("ohlcv_feed")
        kwargs = feed.runtime_kwargs({"symbol": "BTC/USDT", "timeframe": "1h", "mode": "historical"})
        assert kwargs == {"symbol": "BTC/USDT", "timeframe": "1h", "market_type": "spot"}

    def test_unknown_params_are_reported(self):
        spec = get_block_spec("add")
        issues = validate_block_params(spec, {"window": 14})
        assert any("does not declare" in issue for issue in issues)
