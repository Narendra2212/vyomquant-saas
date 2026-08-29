"""Unit tests for the indicator descriptor specs (task 1.2).

These tests hold the three invariants the registry depends on:

* every advertised indicator is runnable (no runnable-but-unselectable, and no
  selectable-but-broken -- defect SB-04),
* declared ports match what the runtime actually consumes and returns,
* parameters are indicator-specific rather than one generic ``window`` form.

They exercise the real implementations on synthetic candles; nothing is mocked.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from backend_app.backend import indicators_backend as ib

# The 33 implementations that were listed by hand before AVAILABLE_INDICATORS
# became derived. Kept literal here so a silent drop is caught.
PREVIOUSLY_LISTED = [
    "sma", "ema", "wma", "hma",
    "rsi", "macd", "atr", "bollinger_bands",
    "stochastic", "cci", "williams_r", "obv",
    "mfi", "adx", "supertrend", "trix",
    "vortex_indicator", "choppiness_index",
    "awesome_oscillator", "fisher_transform",
    "rolling_z_score", "historical_volatility",
    "rolling_vwap", "momentum", "roc",
    "donchian_channel", "keltner_channels",
    "ichimoku_cloud", "cmf", "psar",
    "fibonacci_rolling", "pivot_standard", "pivot_camarilla",
]

# Every indicator the task requires to expose one port per output.
EXPECTED_OUTPUT_PORTS = {
    "macd": ("macd", "signal", "histogram"),
    "bollinger_bands": ("middle", "lower", "upper", "bandwidth", "percent_b"),
    "stochastic": ("k", "d"),
    "supertrend": ("trend", "direction"),
    "adx": ("adx", "plus_di", "minus_di"),
    "vortex_indicator": ("vi_plus", "vi_minus"),
    "ichimoku_cloud": ("tenkan", "kijun", "senkou_a", "senkou_b", "chikou"),
    "psar": ("value",),
    "donchian_channel": ("upper", "lower", "middle"),
    "keltner_channels": ("middle", "upper", "lower"),
    "pivot_standard": ("pp", "r1", "r2", "r3", "s1", "s2", "s3"),
    "pivot_camarilla": ("pp", "r1", "r2", "r3", "r4", "s1", "s2", "s3", "s4"),
    "fibonacci_rolling": (
        "level_0", "level_236", "level_382",
        "level_500", "level_618", "level_786", "level_100",
    ),
}

BARS = 320


@pytest.fixture(scope="module")
def candles():
    """Deterministic synthetic OHLCV long enough for the widest warmup."""
    rng = np.random.default_rng(20260819)
    steps = rng.normal(0.0, 1.5, BARS)
    close = 30_000.0 + np.cumsum(steps)
    spread = np.abs(rng.normal(0.0, 8.0, BARS)) + 1.0
    return {
        "close": close,
        "series": close,
        "high": close + spread,
        "low": close - spread,
        "open": close - steps,
        "volume": np.abs(rng.normal(1_000.0, 120.0, BARS)) + 1.0,
    }


def _call(spec, candles):
    args = [candles[port.name] for port in spec.inputs]
    return spec.resolve_runtime()(*args, **spec.runtime_kwargs())


def _returned_series_count(result) -> int:
    if isinstance(result, tuple):
        return len(result)
    return 1


# --- derivation -------------------------------------------------------------


def test_available_indicators_is_derived_from_specs():
    assert ib.AVAILABLE_INDICATORS == [s.block_id for s in ib.INDICATOR_SPECS]
    assert len(ib.AVAILABLE_INDICATORS) == len(set(ib.AVAILABLE_INDICATORS))


def test_available_indicators_keeps_every_previously_listed_indicator():
    missing = [name for name in PREVIOUSLY_LISTED if name not in ib.AVAILABLE_INDICATORS]
    assert missing == []
    assert len(ib.INDICATOR_SPECS) == 33


@pytest.mark.parametrize("block_id", ["wma", "hma"])
def test_runnable_indicators_are_also_selectable(block_id):
    """SB-04: wma and hma were runnable but absent from what the UI could offer."""
    assert block_id in ib.AVAILABLE_INDICATORS
    assert callable(ib.resolve_indicator_runtime(block_id))


# --- runnability ------------------------------------------------------------


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_runtime_ref_resolves_to_a_callable(spec):
    assert callable(spec.resolve_runtime())
    assert spec.runtime_ref == f"{ib.MODULE_REF}.{spec.block_id}"


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_input_ports_bind_positionally_to_the_runtime(spec):
    fn = spec.resolve_runtime()
    required = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    assert len(spec.inputs) == len(required)
    for port in spec.inputs:
        assert port.required is True


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_forwarded_params_are_keywords_the_runtime_accepts(spec):
    accepted = set(inspect.signature(spec.resolve_runtime()).parameters)
    for param in spec.params:
        if param.forward_to_runtime:
            assert param.key in accepted


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_spec_executes_and_returns_one_series_per_output_port(spec, candles):
    result = _call(spec, candles)
    assert _returned_series_count(result) == len(spec.outputs), (
        f"{spec.block_id} declares {len(spec.outputs)} output port(s) but returns "
        f"{_returned_series_count(result)} series"
    )
    series = result if isinstance(result, tuple) else (result,)
    for produced in series:
        assert len(np.asarray(produced)) == BARS


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_no_declared_output_port_is_entirely_undefined(spec, candles):
    """A port that is NaN on every bar is a port that does not exist.

    ``adx`` was exactly this: ``plus_dm[0]`` / ``minus_dm[0]`` were left NaN, so the EMA seed
    was NaN and the recursion carried it to the end - all three of its declared ports came
    back 100% NaN for every input length. It was invisible while
    ``dag_engine.IndicatorExecutor`` had no ADX branch and silently computed RSI(14) instead,
    and it surfaced the moment that executor was re-pointed at this module
    (strategy-builder task 5.4). ``BARS`` is far longer than the widest declared warmup, so
    an all-NaN port here is a defect and not a short fixture.
    """
    result = _call(spec, candles)
    series = result if isinstance(result, tuple) else (result,)
    for port, produced in zip(spec.outputs, series):
        values = np.asarray(produced, dtype=float)
        assert not np.isnan(values).all(), (
            f"{spec.block_id}.{port.name} is NaN on all {BARS} bars, so nothing downstream "
            f"can ever read it"
        )
        assert not np.isinf(values).any(), (
            f"{spec.block_id}.{port.name} holds an infinity, which compares True against a "
            f"threshold and would trade"
        )


@pytest.mark.parametrize(
    "block_id,ports", sorted(EXPECTED_OUTPUT_PORTS.items())
)
def test_multi_output_indicators_expose_every_output(block_id, ports):
    spec = ib.get_indicator_spec(block_id)
    assert tuple(p.name for p in spec.outputs) == ports


# --- parameters -------------------------------------------------------------


def test_parameters_are_indicator_specific_not_a_generic_window_form():
    """The old registry emitted one {window: 14, 1..500} form for all 33."""
    shapes = {
        tuple(sorted(p.key for p in spec.params)) for spec in ib.INDICATOR_SPECS
    }
    assert len(shapes) > 1

    macd = ib.get_indicator_spec("macd")
    assert tuple(sorted(p.key for p in macd.params)) == ("fast", "signal", "slow")

    bands = ib.get_indicator_spec("bollinger_bands")
    assert bands.param("num_std").min == 0.1
    assert bands.param("num_std").max == 5.0

    stoch = ib.get_indicator_spec("stochastic")
    assert {p.key for p in stoch.params} == {"k_window", "d_window"}

    sar = ib.get_indicator_spec("psar")
    assert {p.key for p in sar.params} == {"step", "max_step"}

    # Pivots read the previous closed bar; they take no parameters at all.
    assert ib.get_indicator_spec("pivot_standard").params == ()
    assert ib.get_indicator_spec("pivot_camarilla").params == ()

    # Window ranges differ per indicator rather than sharing 1..500.
    assert ib.get_indicator_spec("sma").param("window").max == 1000
    assert ib.get_indicator_spec("rsi").param("window").max == 500


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_declared_defaults_sit_inside_declared_ranges(spec):
    for param in spec.params:
        assert param.default is not None, f"{spec.block_id}.{param.key} has no default"
        if param.options:
            assert param.default in param.options
            continue
        if param.min is not None:
            assert param.default >= param.min
        if param.max is not None:
            assert param.default <= param.max


def test_cross_field_hooks_reject_what_ranges_cannot_express():
    macd = ib.get_indicator_spec("macd")
    assert macd.validate({"fast": 12, "slow": 26, "signal": 9}) == []
    issues = macd.validate({"fast": 30, "slow": 26, "signal": 9})
    assert [i["code"] for i in issues] == ["PARAM_CROSS_FIELD_INVALID"]
    assert issues[0]["field"] == "fast"
    assert issues[0]["fix_hint"]

    sar = ib.get_indicator_spec("psar")
    assert sar.validate({"step": 0.02, "max_step": 0.2}) == []
    assert sar.validate({"step": 0.5, "max_step": 0.2})[0]["field"] == "step"

    ao = ib.get_indicator_spec("awesome_oscillator")
    assert ao.validate({"fast_w": 5, "slow_w": 34}) == []
    assert ao.validate({"fast_w": 34, "slow_w": 5})[0]["field"] == "fast_w"


# --- warmup -----------------------------------------------------------------


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_warmup_is_a_positive_int_for_defaults(spec):
    warmup = spec.warmup(spec.defaults())
    assert isinstance(warmup, int)
    assert warmup >= 1
    assert warmup <= BARS


def test_warmup_follows_the_parameters():
    assert ib.indicator_warmup("sma", {"window": 50}) == 50
    assert ib.indicator_warmup("ema", {"window": 200}) == 600
    assert ib.indicator_warmup("rsi", {"window": 14}) == 15
    assert ib.indicator_warmup("macd", {"fast": 12, "slow": 26, "signal": 9}) == 35
    assert ib.indicator_warmup("stochastic", {"k_window": 14, "d_window": 3}) == 17
    assert ib.indicator_warmup(
        "ichimoku_cloud", {"tenkan": 9, "kijun": 26, "senkou_b": 52}
    ) == 78
    assert ib.indicator_warmup("psar", {}) == 2
    assert ib.indicator_warmup("pivot_standard", {}) == 2


def test_warmup_falls_back_to_defaults_on_junk_params():
    assert ib.indicator_warmup("sma", {"window": "not-a-number"}) == 20
    assert ib.indicator_warmup("sma", {}) == 20


# --- leakage ----------------------------------------------------------------


def test_ichimoku_chikou_is_flagged_for_review():
    spec = ib.get_indicator_spec("ichimoku_cloud")
    chikou = spec.output_port("chikou")
    assert chikou.leakage_risk == ib.LEAKAGE_REVIEW_REQUIRED
    assert spec.leaky_outputs == ("chikou",)
    assert spec.leakage_risk == ib.LEAKAGE_REVIEW_REQUIRED
    for port in spec.outputs:
        if port.name != "chikou":
            assert port.leakage_risk == ib.LEAKAGE_NONE


def test_chikou_really_does_read_the_future():
    """Why chikou is flagged: at bar t it carries the close of bar t+kijun."""
    close = np.arange(100.0, 200.0)
    high = close + 1.0
    low = close - 1.0
    *_, chikou = ib.ichimoku_cloud(high, low, close, tenkan=9, kijun=26, senkou_b=52)
    assert chikou[0] == close[26]


def test_no_other_indicator_is_flagged_leaky():
    flagged = {
        spec.block_id
        for spec in ib.INDICATOR_SPECS
        if spec.leakage_risk == ib.LEAKAGE_REVIEW_REQUIRED
    }
    assert flagged == {"ichimoku_cloud"}


# --- descriptor shape -------------------------------------------------------


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_ports_use_the_canonical_port_type_vocabulary(spec):
    for port in spec.inputs + spec.outputs:
        assert port.type in ib.CANONICAL_PORT_TYPES
    for port in spec.outputs:
        assert port.type in ("PRICE_SERIES", "SCALAR_SERIES", "BOOLEAN_SERIES")


@pytest.mark.parametrize("spec", ib.INDICATOR_SPECS, ids=lambda s: s.block_id)
def test_spec_serializes_for_the_registry(spec):
    payload = spec.to_dict()
    assert payload["block_id"] == spec.block_id
    assert payload["category"] == "INDICATOR"
    assert payload["description"]
    assert payload["display_name"]
    assert len(payload["outputs"]) == len(spec.outputs)
    assert len(payload["params"]) == len(spec.params)
    assert payload["execution_semantics"] in ib.EXECUTION_SEMANTICS


def test_unknown_block_id_is_rejected_rather_than_guessed():
    assert ib.get_indicator_spec("does_not_exist") is None
    with pytest.raises(KeyError):
        ib.resolve_indicator_runtime("does_not_exist")


def test_malformed_ports_and_params_are_rejected_at_construction():
    with pytest.raises(ValueError):
        ib.Port("x", "NOT_A_PORT_TYPE")
    with pytest.raises(ValueError):
        ib.Port("x", "SCALAR_SERIES", leakage_risk="MAYBE")
    with pytest.raises(ValueError):
        ib.ParamSpec(key="w", label="W", type="NOT_A_TYPE")
    with pytest.raises(ValueError):
        ib.ParamSpec(key="w", label="W", type="SELECT")
    with pytest.raises(ValueError):
        ib.ParamSpec(key="w", label="W", type="INTEGER", min=10, max=2)
