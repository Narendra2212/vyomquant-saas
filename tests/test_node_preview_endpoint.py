# -*- coding: utf-8 -*-
"""tests/test_node_preview_endpoint.py

A preview is the runtime, over a bounded window. Not a second opinion about it.

Spec: strategy-builder task 5.7. ``design.md`` -> Data preview and honesty.
Requirements 24.7, 24.8, plus the controls the endpoint must not weaken: 12.1 / SB-06
(no exchange identifier or credential), 21.x (auth and tenant isolation), 20.4 (recorded
numeric conditions), 25.x (bounded cost).

THE DEFECT CLASS THIS FILE EXISTS FOR
-------------------------------------
SB-01 was two compilers with drifted rule sets. A preview computed by its own evaluator is
the same defect in a new location, and a worse one: the number on screen at the moment an
author decides whether to deploy would be produced by code that never trades. So the
assertions here are not "the preview returns plausible numbers". They are:

* **Parity.** For an indicator, a feature block and a math block, every sampled value in the
  response equals the value the real chain - real validator, real ``StrategyCompiler``, real
  ``plan_to_engine_graph``, real ``DAGEngine`` - produces for that node on the same bars.
  Computed here by running that chain, never by restating a formula.
* **Structure.** ``routers/strategy_operations.py`` reaches the four canonical seams and
  defines no evaluator of its own: no indicator library, no feature engine, no block kernel,
  no second execution loop. Asserted with ``ast`` over the module, in the spirit of
  ``tests/test_compiler_architecture.py``, so the property survives the next edit to the
  file rather than resting on this docstring.

WHAT IS SUPPLIED AND WHAT IS REAL
---------------------------------
Two things are supplied, both of them *outside* the property under test:

1. **The bars.** ``_fetch_preview_bars`` is the endpoint's one I/O boundary; the tests hand
   it a deterministic OHLCV window instead of reaching a live venue. Deterministic input is
   the precondition for a parity assertion, and the parity assertion is run against the same
   frame the endpoint built, through the endpoint's own transport helper.
2. **The strategy row.** A fake ``StrategyService`` that applies the real ownership
   predicate - ``id`` **and** ``user_id`` must both match, exactly as
   ``StrategyService.get_strategy``'s ``.eq("id", …).eq("user_id", …)`` does - so the
   cross-tenant case is a real 404 and not a mocked one.

Everything the requirement is about is real: the registry is the assembled registry, the
graphs are canonical graphs of published descriptors, and validation, compilation,
adaptation and execution are the shipped objects.
"""

import ast
import inspect
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.feature_engineering import FeatureEngine
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (EdgeSpec, NodeSpec,
                                                     StrategyGraph)
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.routers import strategy_operations as SO

#: The pool the fake feed serves from. Deliberately larger than the server-side cap, so a
#: request for more bars than the cap allows can be *served* and must still be clamped.
POOL_BARS = 1_500

OWNER = {
    "id": "usr_preview_owner",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}

INTRUDER = {
    "id": "usr_preview_intruder",
    "email": "intruder@example.com",
    "role": "authenticated",
    "access_token": "token_intruder",
}

STRATEGY_ID = "stg_preview_0001"


def preview_path(node_id: str, strategy_id: str = STRATEGY_ID) -> str:
    return (
        f"/api/strategy-operations/strategies/{strategy_id}"
        f"/nodes/{node_id}/preview"
    )


# ---------------------------------------------------------------------------
# The real registry, real graphs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The assembled registry the endpoint itself resolves. Assembly is shared."""
    return registry_module.get_registry()


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def _required(descriptor):
    """Required params filled from what the descriptor itself publishes."""
    values = {}
    for spec in descriptor.params:
        value = spec.default if spec.default is not None else spec.example
        if spec.required and value is not None:
            values[spec.key] = value
    return values


@pytest.fixture(scope="module")
def pool():
    """A deterministic OHLCV pool whose five columns are all different.

    Different columns matter for the same reason they matter in
    ``tests/test_dag_engine_port_addressing.py``: a frame whose volume equals its close
    cannot detect a preview that sampled the wrong port.
    """
    stamps = pd.date_range("2024-01-01", periods=POOL_BARS, freq="5min", name="timestamp")
    close = pd.Series(
        100.0 + 20.0 * np.sin(np.linspace(0.0, 12.0, POOL_BARS)), index=stamps
    )
    return pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 1.5,
            "low": close - 0.5,
            "close": close,
            "volume": pd.Series(
                np.linspace(1_000.0, 9_000.0, POOL_BARS), index=stamps
            ),
        },
        index=stamps,
    )


def ccxt_rows(frame: pd.DataFrame, limit: int):
    """``frame``'s tail as the CCXT OHLCV shape the real feed returns."""
    tail = frame.tail(int(limit))
    return [
        [
            int(stamp.value // 1_000_000),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        ]
        for stamp, row in tail.iterrows()
    ]


def graph_with(reg, extra_nodes, wiring, *, symbol="ETH/USDT", timeframe="5m"):
    """``ohlcv_feed -> <extra nodes> -> gt -> action``, validated.

    The ``gt -> action`` tail is scaffolding that makes the graph legal (a valid graph needs
    a DATA node and an ACTION node); no assertion here is about it, and the preview does not
    execute it unless it is upstream of the previewed node.
    """
    data = _node(
        reg,
        "ohlcv_feed",
        symbol=symbol,
        timeframe=timeframe,
        market_type="spot",
        mode="streaming",
    )
    gate = _node(reg, "gt", **_required(reg["gt"]))
    threshold = _node(reg, "constant", value=0.5)
    action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))

    graph = StrategyGraph(
        nodes=[data, *extra_nodes, gate, threshold, action],
        edges=[
            *wiring(data, gate, threshold),
            EdgeSpec.create(threshold.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    report = V.validate(graph, reg)
    assert report.valid, f"the fixture graph must validate: {report.codes()}"
    return graph, {"data": data.id, "gate": gate.id, "action": action.id}


@pytest.fixture(scope="module")
def ema_graph(reg):
    """One EMA on close. The simplest thing an author previews."""
    ema = _node(reg, "ema", window=20)
    graph, ids = graph_with(
        reg,
        [ema],
        lambda data, gate, threshold: [
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gate.id, "left"),
        ],
    )
    return graph, {**ids, "ema": ema.id}


@pytest.fixture(scope="module")
def macd_graph(reg):
    """A multi-output indicator: three declared ports, three different series."""
    macd = _node(reg, "macd", **_required(reg["macd"]))
    graph, ids = graph_with(
        reg,
        [macd],
        lambda data, gate, threshold: [
            EdgeSpec.create(data.id, "close", macd.id, "series"),
            EdgeSpec.create(macd.id, "histogram", gate.id, "left"),
        ],
    )
    return graph, {**ids, "macd": macd.id}


@pytest.fixture(scope="module")
def feature_graph(reg):
    """A FEATURE_ENGINEERING node producing a named column per lag (Requirement 24.8)."""
    lag = _node(reg, "feat_lag", lags=[1, 2, 3])
    model = _node(reg, "xgboost", **_required(reg["xgboost"]))
    graph, ids = graph_with(
        reg,
        [lag, model],
        lambda data, gate, threshold: [
            EdgeSpec.create(data.id, "close", lag.id, "series"),
            EdgeSpec.create(lag.id, "matrix", model.id, "features"),
            EdgeSpec.create(model.id, "prediction", gate.id, "left"),
        ],
    )
    return graph, {**ids, "lag": lag.id, "model": model.id}


@pytest.fixture(scope="module")
def divide_by_zero_graph(reg):
    """``close / 0``: a recorded numeric condition, which is what a preview is *for*."""
    divider = _node(reg, "divide")
    zero = _node(reg, "constant", value=0.0)
    graph, ids = graph_with(
        reg,
        [divider, zero],
        lambda data, gate, threshold: [
            EdgeSpec.create(data.id, "close", divider.id, "numerator"),
            EdgeSpec.create(zero.id, "value", divider.id, "denominator"),
            EdgeSpec.create(divider.id, "out", gate.id, "left"),
        ],
    )
    return graph, {**ids, "divider": divider.id, "zero": zero.id}


# ---------------------------------------------------------------------------
# The two supplied things: the strategy row and the bars
# ---------------------------------------------------------------------------


class FakeStrategyService:
    """``StrategyService.get_strategy``'s ownership predicate, and nothing else.

    The real method filters ``.eq("id", strategy_id).eq("user_id", user["id"])`` on a
    request-scoped client that also carries RLS. Both halves of that predicate are applied
    here, so "another tenant's strategy is a 404" is a property this fake can actually
    fail. Every other service method is absent on purpose: if the endpoint ever reached for
    one - to write a row, to queue a job - the test would raise rather than pass quietly.
    """

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def get_strategy(self, user, strategy_id):
        self.calls.append({"user_id": user.get("id"), "strategy_id": strategy_id})
        for row in self.rows:
            if row["id"] == strategy_id and row["user_id"] == user["id"]:
                return {
                    "strategy": row,
                    "version": row.get("version"),
                    "deployments": [],
                    "performance": {},
                }
        return None


class RecordingFeed:
    """The endpoint's one I/O boundary, replaced by a deterministic window."""

    def __init__(self, pool):
        self.pool = pool
        self.calls = []

    async def __call__(self, symbol, timeframe, bars):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "bars": int(bars)})
        return ccxt_rows(self.pool, bars)


@pytest.fixture
def feed(pool, monkeypatch):
    recorder = RecordingFeed(pool)
    monkeypatch.setattr(SO, "_fetch_preview_bars", recorder)
    return recorder


@pytest.fixture
def service(monkeypatch):
    """A saved strategy owned by ``OWNER``, carrying no canonical graph of its own.

    The row's ``version`` is populated per-test where the saved-version path is exercised;
    the default is a row with no graph, so a test that forgets to send a blueprint fails
    loudly instead of previewing something unrelated.
    """
    state = FakeStrategyService(
        [{"id": STRATEGY_ID, "user_id": OWNER["id"], "name": "Preview", "version": {}}]
    )

    async def factory():
        return state

    monkeypatch.setattr(SO, "get_strategy_service", factory)
    return state


@pytest.fixture
def as_owner():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def as_intruder():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: INTRUDER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous():
    """No override: the real auth dependency runs."""
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# The reference: the same chain, run here
# ---------------------------------------------------------------------------


def runtime_values(reg, graph, node_id, port, frame):
    """What the *runtime* produces for ``node_id``'s ``port`` on ``frame``.

    The real validator, the real compiler, the real adapter, the real engine - the chain
    ``dag_worker`` and ``dag_event_loop`` use. Nothing about a preview appears here, which
    is the point: this is the answer the preview has to match.
    """
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    engine = DAGEngine(enable_event_buffer=False)
    engine.execute_dag(nodes, edges, frame)
    return engine, engine.node_outputs[(node_id, port)]


def served_frame(feed_recorder, index=-1, timeframe="5m"):
    """The frame the endpoint executed against, rebuilt from what the feed served.

    ``_preview_frame`` takes the timeframe as of task 7.5: it now routes the rows through
    ``market_data_contract.closed_bar_frame``, which needs the bar length to tell a closed
    bar from a forming one, and returns the drop counters alongside the frame. The pool's
    bars are all historical, so nothing is dropped here - but this reference is built by
    the same call the endpoint makes, so it stays the endpoint's frame either way.
    """
    call = feed_recorder.calls[index]
    frame, _counters = SO._preview_frame(
        ccxt_rows(feed_recorder.pool, call["bars"]), call["bars"], timeframe
    )
    return frame


def as_floats(values):
    """A response's sampled values as floats, with ``null`` read back as NaN."""
    return np.asarray(
        [np.nan if item is None else float(item) for item in values], dtype=float
    )


def post_preview(client, node_id, graph, **body):
    payload = {"blueprint": graph.to_dict(), **body}
    return client.post(preview_path(node_id), json=payload)


# ---------------------------------------------------------------------------
# 1. Requirement 24.7 - the same executors, over a bounded window
# ---------------------------------------------------------------------------


class TestPreviewAgreesWithRuntime:
    def test_an_indicator_preview_equals_the_runtime_series(
        self, reg, ema_graph, as_owner, service, feed
    ):
        """The headline property: every sampled value is the runtime's value.

        Not "close to", not "the same shape". The preview transports the values the engine
        produced, so the comparison is exact bar for bar (NaN included, which is how the
        warmup region must read).
        """
        graph, ids = ema_graph
        response = post_preview(as_owner, ids["ema"], graph)
        assert response.status_code == 200, response.text
        body = response.json()

        _engine, expected = runtime_values(
            reg, graph, ids["ema"], "value", served_frame(feed)
        )
        port = next(entry for entry in body["outputs"] if entry["name"] == "value")
        sample = as_floats(port["values"])

        assert port["produced"] is True
        assert port["kind"] == "series"
        assert port["length"] == len(expected)
        np.testing.assert_array_equal(
            sample, np.asarray(expected.tail(len(sample)), dtype=float)
        )

    def test_it_is_the_last_n_values_and_the_timestamps_say_so(
        self, ema_graph, as_owner, service, feed
    ):
        """"The last N computed values" - the tail, labelled with its own bars."""
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph).json()

        frame = served_frame(feed)
        port = next(entry for entry in body["outputs"] if entry["name"] == "value")

        assert len(port["values"]) == SO.PREVIEW_SAMPLE_ROWS
        assert len(port["index"]) == len(port["values"])
        assert port["index"][-1] == pd.Timestamp(frame.index[-1]).isoformat()
        assert port["index"][0] == pd.Timestamp(
            frame.index[-SO.PREVIEW_SAMPLE_ROWS]
        ).isoformat()

    def test_a_multi_output_indicator_previews_every_declared_port(
        self, reg, macd_graph, as_owner, service, feed
    ):
        """``macd`` publishes three ports, and they are three *different* series.

        Before task 5.4 an edge from a port the executor did not return received whichever
        one it did. A preview that showed one series three times would hide exactly that.
        """
        graph, ids = macd_graph
        body = post_preview(as_owner, ids["macd"], graph).json()

        frame = served_frame(feed)
        ports = {entry["name"]: entry for entry in body["outputs"]}
        declared = {port.name for port in reg["macd"].outputs}
        assert set(ports) == declared
        assert declared == {"macd", "signal", "histogram"}

        seen = {}
        for name in declared:
            _engine, expected = runtime_values(reg, graph, ids["macd"], name, frame)
            sample = as_floats(ports[name]["values"])
            np.testing.assert_array_equal(
                sample, np.asarray(expected.tail(len(sample)), dtype=float)
            )
            seen[name] = sample

        assert not np.allclose(seen["macd"], seen["signal"], equal_nan=True)
        assert not np.allclose(seen["macd"], seen["histogram"], equal_nan=True)

    def test_a_math_node_preview_equals_the_kernel(
        self, reg, divide_by_zero_graph, as_owner, service, feed
    ):
        graph, ids = divide_by_zero_graph
        body = post_preview(as_owner, ids["divider"], graph).json()

        _engine, expected = runtime_values(
            reg, graph, ids["divider"], "out", served_frame(feed)
        )
        port = next(entry for entry in body["outputs"] if entry["name"] == "out")
        np.testing.assert_array_equal(
            as_floats(port["values"]),
            np.asarray(expected.tail(len(port["values"])), dtype=float),
        )

    def test_the_warmup_region_is_null_rather_than_a_number(
        self, reg, as_owner, service, feed
    ):
        """A warmup bar has no value, and the preview says so.

        A zero here would be a number an author could reasonably compare against a
        threshold. ``null`` cannot be mistaken for one.
        """
        slow = _node(reg, "sma", window=300)
        graph, ids = graph_with(
            reg,
            [slow],
            lambda data, gate, threshold: [
                EdgeSpec.create(data.id, "close", slow.id, "series"),
                EdgeSpec.create(slow.id, "value", gate.id, "left"),
            ],
        )
        response = post_preview(as_owner, slow.id, graph, bars=SO.PREVIEW_MIN_BARS)
        assert response.status_code == 200, response.text
        body = response.json()

        port = next(entry for entry in body["outputs"] if entry["name"] == "value")
        window = body["window"]
        assert window["warmup_bars"] >= 300
        # The window was grown past the request to cover warmup with room to spare,
        # because the runtime refuses a series that is mostly warmup - so a window inside
        # the warmup region does not produce a preview of nulls, it produces no preview.
        assert window["bars"] > SO.PREVIEW_MIN_BARS
        assert window["needed_bars"] == (
            SO.PREVIEW_WARMUP_HEADROOM * window["warmup_bars"] + SO.PREVIEW_SAMPLE_ROWS
        )
        assert window["bars"] >= window["needed_bars"]
        assert window["warmup_exceeds_window"] is False

        # The warmup region really is null, and the sampled tail really is not: the whole
        # series is checked, not just the transported tail.
        _engine, produced = runtime_values(
            reg, graph, slow.id, "value", served_frame(feed)
        )
        leading = np.asarray(produced.iloc[: window["warmup_bars"] - 1], dtype=float)
        assert np.isnan(leading).all(), "a warmup bar carried a number"
        assert port["empty_values"] == 0, "the sampled tail should be past warmup"


# ---------------------------------------------------------------------------
# 2. Requirement 24.8 - a FEATURE_ENGINEERING preview names its columns
# ---------------------------------------------------------------------------


class TestFeatureMatrixPreview:
    def test_it_carries_the_produced_column_names(
        self, reg, feature_graph, as_owner, service, feed
    ):
        """The names come off the ``FeatureMatrix`` the block produced.

        ``feat_lag`` with ``lags=[1, 2, 3]`` produces one column per lag, and the whole
        point of showing the names is that an author can tell which lag is which.
        """
        graph, ids = feature_graph
        response = post_preview(as_owner, ids["lag"], graph)
        assert response.status_code == 200, response.text
        body = response.json()

        _engine, matrix = runtime_values(
            reg, graph, ids["lag"], "matrix", served_frame(feed)
        )
        port = next(entry for entry in body["outputs"] if entry["name"] == "matrix")

        assert port["kind"] == "feature_matrix"
        assert port["columns"] == list(matrix.columns)
        assert port["column_count"] == len(matrix.columns)
        assert port["sample_truncated"] is False
        assert set(port["provenance"].values()) == {ids["lag"]}
        assert port["column_warmup"] == {
            name: int(offset) for name, offset in matrix.column_warmup.items()
        }
        assert port["warmup_offset"] == int(matrix.warmup_offset)

    def test_the_sampled_values_are_the_produced_values(
        self, reg, feature_graph, as_owner, service, feed
    ):
        """Column by column, against the block's own published runtime.

        The expected numbers come from ``FeatureEngine.compute_lag_features`` - the
        implementation the FEATURE_SPECS descriptor points at - so this cannot pass by
        restating a formula in the test.
        """
        graph, ids = feature_graph
        body = post_preview(as_owner, ids["lag"], graph).json()

        frame = served_frame(feed)
        port = next(entry for entry in body["outputs"] if entry["name"] == "matrix")
        lags = [1, 2, 3]
        # ``compute_lag_features`` returns one *column* per lag, in the order the lags were
        # given: shape (bars, len(lags)). The names the engine publishes carry the lag, so
        # the position of a column in the response is checked against the lag it claims -
        # which is the assertion Requirement 24.8 is actually about.
        expected = FeatureEngine.compute_lag_features(
            np.asarray(frame["close"], dtype=float), lags
        )
        rows = len(port["values"])

        assert port["sampled_columns"] == [f"lag_{lag}" for lag in lags]
        for position, name in enumerate(port["sampled_columns"]):
            lag = int(name.rsplit("_", 1)[1])
            column = as_floats([row[position] for row in port["values"]])
            np.testing.assert_array_equal(
                column,
                np.asarray(expected[-rows:, lags.index(lag)], dtype=float),
            )

    def test_a_wide_matrix_states_that_its_value_sample_is_truncated(
        self, reg, as_owner, service, feed
    ):
        """Every name, a bounded number of value columns, and the truncation declared."""
        lags = list(range(1, SO.PREVIEW_SAMPLE_COLUMNS + 6))
        wide = _node(reg, "feat_lag", lags=lags)
        model = _node(reg, "xgboost", **_required(reg["xgboost"]))
        graph, _ids = graph_with(
            reg,
            [wide, model],
            lambda data, gate, threshold: [
                EdgeSpec.create(data.id, "close", wide.id, "series"),
                EdgeSpec.create(wide.id, "matrix", model.id, "features"),
                EdgeSpec.create(model.id, "prediction", gate.id, "left"),
            ],
        )

        response = post_preview(as_owner, wide.id, graph)
        assert response.status_code == 200, response.text
        body = response.json()
        port = next(entry for entry in body["outputs"] if entry["name"] == "matrix")

        assert port["column_count"] == len(lags)
        assert len(port["columns"]) == len(lags)
        assert len(port["sampled_columns"]) == SO.PREVIEW_SAMPLE_COLUMNS
        assert port["sample_truncated"] is True
        assert all(len(row) == SO.PREVIEW_SAMPLE_COLUMNS for row in port["values"])


# ---------------------------------------------------------------------------
# 3. Bounded, and honest about it
# ---------------------------------------------------------------------------


class TestTheWindowIsBoundedServerSide:
    def test_a_client_supplied_count_is_clamped_to_the_cap(
        self, ema_graph, as_owner, service, feed
    ):
        """A preview must not become an unbounded backtest because a client asked."""
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph, bars=100_000).json()

        assert body["window"]["requested"] == 100_000
        assert body["window"]["bars"] == SO.PREVIEW_MAX_BARS
        assert body["window"]["max_bars"] == SO.PREVIEW_MAX_BARS
        assert body["window"]["clamped"] is True
        # And the *fetch* was bounded too, not just the report of it.
        assert feed.calls[-1]["bars"] == SO.PREVIEW_MAX_BARS

    @pytest.mark.parametrize("requested", [-5, 0, None])
    def test_a_missing_or_nonsense_count_falls_back_to_the_default(
        self, ema_graph, as_owner, service, feed, requested
    ):
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph, bars=requested).json()

        assert body["window"]["bars"] == SO.PREVIEW_DEFAULT_BARS
        assert feed.calls[-1]["bars"] == SO.PREVIEW_DEFAULT_BARS

    @pytest.mark.parametrize(
        "requested", [None, -1, 0, 1, SO.PREVIEW_MIN_BARS, 10_000, 10 ** 9]
    )
    @pytest.mark.parametrize("warmup", [0, 1, 7, 300, 480, 3_000, 100_000])
    def test_the_window_is_inside_the_cap_for_every_request_and_warmup(
        self, requested, warmup
    ):
        """The bound is a property of the sizing rule, not of one response.

        ``_preview_window`` is pure, so the cap can be asserted over the whole input space
        instead of over the handful of graphs the fixtures happen to build.
        """
        window = SO._preview_window(requested, warmup)

        assert SO.PREVIEW_MIN_BARS <= window["bars"] <= SO.PREVIEW_MAX_BARS
        assert window["node_warmup_bars"] == max(0, warmup)
        if requested is not None and requested > window["bars"]:
            assert window["clamped"] is True

    def test_a_node_needing_more_history_than_the_cap_is_refused_with_both_figures(
        self, reg, as_owner, service, feed
    ):
        """The cap does not silently produce an untrustworthy preview.

        An EMA(600) composes to 1800 warmup bars. No window under the cap leaves its warmup
        a minority, and the runtime refuses a series that is mostly warmup - so the honest
        answer is a refusal that names the warmup, the window it would need and the ceiling,
        not a column of nulls and not ``Excessive NaN values (94.7%)``.
        """
        slow = _node(reg, "ema", window=600)
        graph, ids = graph_with(
            reg,
            [slow],
            lambda data, gate, threshold: [
                EdgeSpec.create(data.id, "close", slow.id, "series"),
                EdgeSpec.create(slow.id, "value", gate.id, "left"),
            ],
        )
        response = post_preview(as_owner, slow.id, graph)

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "PREVIEW_WARMUP_EXCEEDS_WINDOW"
        assert detail["node_warmup_bars"] >= 600
        assert detail["needed_bars"] > SO.PREVIEW_MAX_BARS
        assert detail["max_bars"] == SO.PREVIEW_MAX_BARS
        assert detail["hint"]
        # Refused before any market data was read.
        assert feed.calls == []

    def test_the_window_is_sized_from_the_previewed_node_not_the_whole_plan(
        self, reg, as_owner, service, feed
    ):
        """A fast node in a slow graph previews over a fast node's window.

        ``plan.warmup_bars`` is the ACTION path's composed figure. Sizing from it would make
        this EMA(20) fetch a window for the SMA(400) beside it, and would refuse outright
        once the slow branch alone exceeded the cap - for a preview that never touches it.
        """
        fast = _node(reg, "ema", window=20)
        slow = _node(reg, "sma", window=400)
        spread = _node(reg, "subtract")
        graph, ids = graph_with(
            reg,
            [fast, slow, spread],
            lambda data, gate, threshold: [
                EdgeSpec.create(data.id, "close", fast.id, "series"),
                EdgeSpec.create(data.id, "close", slow.id, "series"),
                EdgeSpec.create(fast.id, "value", spread.id, "a"),
                EdgeSpec.create(slow.id, "value", spread.id, "b"),
                EdgeSpec.create(spread.id, "out", gate.id, "left"),
            ],
        )
        response = post_preview(as_owner, fast.id, graph)

        assert response.status_code == 200, response.text
        window = response.json()["window"]
        assert window["warmup_bars"] == 60, "EMA(20) composes to 3 * 20"
        assert window["plan_warmup_bars"] >= 400
        assert window["bars"] == SO.PREVIEW_DEFAULT_BARS
        assert feed.calls[-1]["bars"] == SO.PREVIEW_DEFAULT_BARS

    def test_only_the_previewed_node_and_its_ancestors_are_executed(
        self, ema_graph, as_owner, service, feed
    ):
        """The bound on *work*, and the reason an untrained model cannot break a preview."""
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph).json()

        assert set(body["executed_nodes"]) == {ids["data"], ids["ema"]}
        assert ids["action"] not in body["executed_nodes"]

    def test_a_feature_preview_does_not_execute_the_untrained_model_downstream(
        self, feature_graph, as_owner, service, feed
    ):
        """The graph holds an ML node with no bound model; the FE preview still answers.

        Executing it would raise ``MODEL NOT FOUND`` - correctly, at deploy time. A preview
        of a *feature* block has no business asking that question, and answering it with a
        training job would be worse still.
        """
        graph, ids = feature_graph
        response = post_preview(as_owner, ids["lag"], graph)

        assert response.status_code == 200, response.text
        assert ids["model"] not in response.json()["executed_nodes"]


# ---------------------------------------------------------------------------
# 4. Requirement 20.4 - why the bar is empty
# ---------------------------------------------------------------------------


class TestRecordedConditionsAreSurfaced:
    def test_a_division_by_zero_is_reported_against_the_previewed_node(
        self, divide_by_zero_graph, as_owner, service, feed
    ):
        graph, ids = divide_by_zero_graph
        body = post_preview(as_owner, ids["divider"], graph).json()

        assert [issue["code"] for issue in body["issues"]] == ["DIVISION_BY_ZERO"]
        assert body["issues"][0]["node_id"] == ids["divider"]
        assert body["issues"][0]["bars_affected"] == body["window"]["available_bars"]

        port = next(entry for entry in body["outputs"] if entry["name"] == "out")
        assert all(value is None for value in port["values"]), (
            "an undefined division produced numbers"
        )

    def test_a_healthy_node_reports_no_conditions(
        self, ema_graph, as_owner, service, feed
    ):
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph).json()

        assert body["issues"] == []


# ---------------------------------------------------------------------------
# 5. Controls that must not be weakened
# ---------------------------------------------------------------------------


class TestAuthAndTenantIsolation:
    def test_an_unauthenticated_caller_is_refused(self, anonymous, ema_graph):
        graph, ids = ema_graph
        response = anonymous.post(
            preview_path(ids["ema"]), json={"blueprint": graph.to_dict()}
        )
        assert response.status_code in (401, 403), response.text

    def test_another_tenants_strategy_is_reported_as_missing(
        self, as_intruder, service, feed, ema_graph
    ):
        """404, identically to a strategy that does not exist: existence must not leak."""
        graph, ids = ema_graph
        response = post_preview(as_intruder, ids["ema"], graph)

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"
        # Nothing was computed on the refused path.
        assert feed.calls == []

    def test_ownership_is_resolved_before_anything_is_computed(
        self, as_owner, service, feed, ema_graph
    ):
        graph, ids = ema_graph
        post_preview(as_owner, ids["ema"], graph)

        assert service.calls == [
            {"user_id": OWNER["id"], "strategy_id": STRATEGY_ID}
        ]

    def test_an_unknown_strategy_is_a_404(self, as_owner, service, feed, ema_graph):
        graph, ids = ema_graph
        response = as_owner.post(
            preview_path(ids["ema"], strategy_id="stg_does_not_exist"),
            json={"blueprint": graph.to_dict()},
        )
        assert response.status_code == 404

    def test_the_endpoint_carries_the_auth_dependency_and_a_rate_limit(self):
        """The same two controls every other endpoint in this router carries."""
        signature = inspect.signature(SO.preview_node)
        assert "user" in signature.parameters
        default = signature.parameters["user"].default
        assert getattr(default, "dependency", None) is get_current_user

        assert any(
            getattr(route, "path", "").endswith("/nodes/{node_id}/preview")
            for route in SO.router.routes
        ), "the preview route is not registered"
        assert "@limiter.limit(" in _preview_decorators()


def _preview_decorators() -> str:
    """The decorator lines attached to ``preview_node`` in the router source."""
    module = ast.parse(inspect.getsource(SO))
    source_lines = inspect.getsource(SO).splitlines()
    for node in ast.walk(module):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "preview_node":
            first = min(dec.lineno for dec in node.decorator_list) - 1
            return "\n".join(source_lines[first : node.lineno])
    raise AssertionError("preview_node is not defined in the router")


class TestNoExchangeIdentifierOrCredential:
    """SB-06 / Requirement 12.1, at the request and at the response."""

    FORBIDDEN = (
        "exchange",
        "api_key",
        "apikey",
        "secret",
        "passphrase",
        "password",
        "binance",
        "bybit",
        "kraken",
        "coinbase",
        "okx",
    )

    def test_the_request_model_refuses_an_exchange_field(self, as_owner, service, feed, ema_graph):
        graph, ids = ema_graph
        response = as_owner.post(
            preview_path(ids["ema"]),
            json={"blueprint": graph.to_dict(), "exchange": "binance"},
        )

        assert response.status_code == 422, response.text
        assert feed.calls == []

    def test_the_request_model_refuses_a_credential_field(self, as_owner, service, feed, ema_graph):
        graph, ids = ema_graph
        response = as_owner.post(
            preview_path(ids["ema"]),
            json={"blueprint": graph.to_dict(), "api_key": "AK", "secret": "SK"},
        )

        assert response.status_code == 422
        assert feed.calls == []

    def test_a_data_node_carrying_an_exchange_param_is_reported_and_not_honoured(
        self, reg, as_owner, service, feed
    ):
        """The graph half of SB-06: the DATA descriptor publishes no exchange parameter.

        Requirement 12.2 is about what the *registry* publishes, and the validator says so
        for this graph: ``PARAM_UNKNOWN`` naming ``exchange`` and listing the parameters the
        DATA block actually has. It is a warning rather than an error - unknown params are
        ignored, not fatal - so the control being asserted here is that the value is
        **ignored**: the venue still comes from the server's own setting, the smuggled one
        reaches no fetch, and it appears in no response field.
        """
        data = NodeSpec.create(
            "ohlcv_feed",
            reg["ohlcv_feed"].category,
            params={
                "symbol": "ETH/USDT",
                "timeframe": "5m",
                "market_type": "spot",
                "mode": "streaming",
                "exchange": "binance",
            },
        )
        ema = _node(reg, "ema", window=20)
        gate = _node(reg, "gt", **_required(reg["gt"]))
        threshold = _node(reg, "constant", value=0.5)
        action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))
        graph = StrategyGraph(
            nodes=[data, ema, gate, threshold, action],
            edges=[
                EdgeSpec.create(data.id, "close", ema.id, "series"),
                EdgeSpec.create(ema.id, "value", gate.id, "left"),
                EdgeSpec.create(threshold.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
            ],
        )

        report = V.validate(graph, reg)
        unknown = [
            warning
            for warning in report.warnings
            if warning["code"] == "PARAM_UNKNOWN" and warning["field"] == "exchange"
        ]
        assert unknown, "the validator did not report the undeclared 'exchange' param"
        assert "exchange" not in unknown[0]["expected"]

        response = post_preview(as_owner, ema.id, graph)

        assert response.status_code == 200, response.text
        # The smuggled value was ignored: it named no venue, and it is nowhere on the wire.
        assert "binance" not in response.text.lower()
        assert "exchange" not in response.text.lower()
        assert feed.calls[-1]["symbol"] == "ETH/USDT"

    def test_the_response_names_no_venue(self, ema_graph, as_owner, service, feed):
        graph, ids = ema_graph
        text = post_preview(as_owner, ids["ema"], graph).text.lower()

        for token in self.FORBIDDEN:
            assert token not in text, f"the preview response leaked '{token}'"

    def test_a_feed_failure_is_reported_without_naming_the_venue(
        self, ema_graph, as_owner, service, monkeypatch
    ):
        async def explode(symbol, timeframe, bars):
            raise RuntimeError("binance rejected the request: invalid apiKey")

        monkeypatch.setattr(SO, "_fetch_preview_bars", explode)
        graph, ids = ema_graph
        response = post_preview(as_owner, ids["ema"], graph)

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error"] == "PREVIEW_DATA_UNAVAILABLE"
        for token in ("binance", "apikey"):
            assert token not in response.text.lower()

    def test_the_market_is_the_data_blocks_own_symbol_and_timeframe(
        self, ema_graph, as_owner, service, feed
    ):
        graph, ids = ema_graph
        body = post_preview(as_owner, ids["ema"], graph).json()

        assert body["market"] == {"symbol": "ETH/USDT", "timeframe": "5m"}
        assert feed.calls[-1]["symbol"] == "ETH/USDT"
        assert feed.calls[-1]["timeframe"] == "5m"


class TestAPreviewChangesNothing:
    def test_no_order_can_be_placed_on_the_preview_path(
        self, reg, as_owner, service, feed, monkeypatch
    ):
        """An ACTION node is previewed, with order placement booby-trapped."""
        from backend_app.backend.exchange_executor import CCXTExchangeExecutor

        async def refuse(*args, **kwargs):
            raise AssertionError("a preview attempted to place an order")

        monkeypatch.setattr(CCXTExchangeExecutor, "place_order", refuse)

        ema = _node(reg, "ema", window=20)
        graph, ids = graph_with(
            reg,
            [ema],
            lambda data, gate, threshold: [
                EdgeSpec.create(data.id, "close", ema.id, "series"),
                EdgeSpec.create(ema.id, "value", gate.id, "left"),
            ],
        )
        response = post_preview(as_owner, ids["action"], graph)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["category"] == "ACTION"
        # The adapted ACTION node carries no runtime_ref by design, so there is nothing on
        # this path that could reach an exchange.
        assert "runtime_ref" not in response.text

    def test_the_endpoint_writes_nothing_through_the_strategy_service(
        self, ema_graph, as_owner, service, feed
    ):
        """The fake service exposes only ``get_strategy``; a write would raise."""
        graph, ids = ema_graph
        assert post_preview(as_owner, ids["ema"], graph).status_code == 200
        assert [call["strategy_id"] for call in service.calls] == [STRATEGY_ID]


# ---------------------------------------------------------------------------
# 6. Refusals that are honest rather than empty
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_an_invalid_graph_gets_the_report_and_no_preview(
        self, reg, as_owner, service, feed
    ):
        """A graph the runtime would refuse has no preview - not a preview of nothing."""
        ema = _node(reg, "ema", window=20)
        graph = StrategyGraph(nodes=[ema], edges=[])

        response = post_preview(as_owner, ema.id, graph)

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_VALIDATION_FAILED"
        assert detail["report"] is not None
        assert detail["codes"], "the refusal carried no codes to act on"
        assert feed.calls == []

    def test_a_node_the_graph_does_not_hold_is_a_404(
        self, ema_graph, as_owner, service, feed
    ):
        graph, _ids = ema_graph
        response = post_preview(as_owner, "n_not_here", graph)

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "PREVIEW_NODE_NOT_FOUND"

    def test_an_unreadable_blueprint_is_a_422(self, as_owner, service, feed):
        response = as_owner.post(
            preview_path("n_anything"), json={"blueprint": {"not": "a graph"}}
        )

        assert response.status_code == 422
        assert response.json()["detail"]["error"] in (
            "STRATEGY_GRAPH_UNREADABLE",
            "PREVIEW_NODE_NOT_FOUND",
        )

    def test_a_strategy_with_no_saved_graph_and_no_blueprint_is_a_422(
        self, as_owner, service, feed
    ):
        response = as_owner.post(preview_path("n_anything"), json={})

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "PREVIEW_GRAPH_UNAVAILABLE"

    def test_the_saved_version_is_previewed_when_no_blueprint_is_sent(
        self, ema_graph, as_owner, service, feed
    ):
        graph, ids = ema_graph
        service.rows[0]["version"] = {"graph_json": graph.to_dict()}

        response = as_owner.post(preview_path(ids["ema"]), json={})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["graph_source"] == "current_version"
        assert body["dag_hash"]

    def test_a_graph_reading_two_markets_is_refused_rather_than_guessed(
        self, reg, as_owner, service, feed
    ):
        """One frame, one market. Choosing one would be choosing a feed for the author."""
        first = _node(
            reg,
            "ohlcv_feed",
            symbol="ETH/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        )
        second = _node(
            reg,
            "ohlcv_feed",
            symbol="BTC/USDT",
            timeframe="5m",
            market_type="spot",
            mode="streaming",
        )
        left = _node(reg, "ema", window=10)
        right = _node(reg, "ema", window=10)
        gate = _node(reg, "gt", **_required(reg["gt"]))
        action = _node(reg, "action_buy_market", **_required(reg["action_buy_market"]))
        graph = StrategyGraph(
            nodes=[first, second, left, right, gate, action],
            edges=[
                EdgeSpec.create(first.id, "close", left.id, "series"),
                EdgeSpec.create(second.id, "close", right.id, "series"),
                EdgeSpec.create(left.id, "value", gate.id, "left"),
                EdgeSpec.create(right.id, "value", gate.id, "right"),
                EdgeSpec.create(gate.id, "out", action.id, "signal"),
            ],
        )
        if not V.validate(graph, reg).valid:
            pytest.skip("the validator refuses a two-feed graph before the preview does")

        response = post_preview(as_owner, left.id, graph)

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "PREVIEW_MULTIPLE_MARKETS"

    def test_an_empty_window_is_reported_rather_than_rendered(
        self, ema_graph, as_owner, service, monkeypatch
    ):
        async def nothing(symbol, timeframe, bars):
            return []

        monkeypatch.setattr(SO, "_fetch_preview_bars", nothing)
        graph, ids = ema_graph
        response = post_preview(as_owner, ids["ema"], graph)

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "PREVIEW_DATA_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 7. The structural guard: no second evaluator, ever
# ---------------------------------------------------------------------------


ROUTER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend_app",
    "routers",
    "strategy_operations.py",
)


def router_module_ast() -> ast.Module:
    with open(ROUTER_PATH, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read())


def imported_modules(tree: ast.Module) -> set:
    """Every module the router imports, at module level or inside a function."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


class TestThePreviewHasNoEvaluatorOfItsOwn:
    """Requirement 24.7's load-bearing half, held in place structurally.

    "Through the same executors" is a property of the *code path*, not of one response. A
    later edit that computes an EMA in the router to save a compile would satisfy every
    assertion above on the day it was written and drift the day after. These assertions are
    about the module, so that edit is a red test.
    """

    def test_the_preview_reaches_the_four_canonical_seams(self):
        source = _preview_source()

        for seam in (
            "load_graph",
            "get_compiler().compile_plan",
            "plan_to_engine_graph",
            "DAGEngine",
            "execute_dag",
        ):
            assert seam in source, f"the preview path does not reach {seam}"

    def test_the_router_imports_no_computation_library(self):
        """No indicator library, no feature engine, no block kernels, no ML models.

        Each of these is a way to answer "what does this block produce?" a second time.
        The router's job is transport; the answer belongs to the engine.
        """
        imports = imported_modules(router_module_ast())
        forbidden = (
            "backend_app.backend.indicators_backend",
            "backend_app.backend.feature_engineering",
            "backend_app.backend.strategy_dag.block_specs",
            "backend_app.backend.ml_models",
            "talib",
        )
        leaked = sorted(name for name in imports if name in forbidden)
        assert leaked == [], (
            f"the router imports a computation library: {leaked}. A preview computed there "
            "is a second answer to what a block produces."
        )

    def test_the_router_defines_no_execution_loop_of_its_own(self):
        """No function in the router walks nodes and dispatches work for each one.

        The signature of a reimplemented engine: a loop over nodes that *invokes* something
        per node. The preview instead hands a node list to ``DAGEngine.execute_dag`` once
        and reads the map it published.

        The test looks at calls, not at prose. An earlier form of this assertion matched the
        word "executor" anywhere in the function - including in a docstring - which made
        :func:`_preview_closure` an offender for *explaining* the engine it does not
        reimplement, and would have let a real reimplementation pass simply by not using the
        word. What is checked here is a dispatch call inside a loop over nodes.
        """
        dispatch_names = {"execute", "execute_node", "execute_dag", "compute", "apply"}
        tree = router_module_ast()
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for loop in ast.walk(node):
                if not isinstance(loop, (ast.For, ast.While)):
                    continue
                calls = [
                    child
                    for child in ast.walk(loop)
                    if isinstance(child, ast.Call)
                ]
                dispatched = any(
                    (isinstance(call.func, ast.Attribute) and call.func.attr in dispatch_names)
                    or (isinstance(call.func, ast.Name) and call.func.id in dispatch_names)
                    for call in calls
                )
                if dispatched:
                    offenders.append(node.name)
                    break
        assert offenders == [], f"a router function executes nodes itself: {offenders}"

    def test_the_guard_above_would_catch_a_real_reimplementation(self):
        """The guard bites. Asserted against a synthetic offender, not assumed.

        A structural guard that cannot fail is decoration. This runs the same predicate over
        a module that *does* loop and dispatch per node, and requires a hit - so a later
        loosening of the predicate breaks here rather than passing silently for years.
        """
        offender = ast.parse(
            "def evaluate(plan, frame):\n"
            "    values = {}\n"
            "    for node_id in plan.execution_order:\n"
            "        values[node_id] = EXECUTORS[node_id].execute(frame)\n"
            "    return values\n"
        )
        dispatch_names = {"execute", "execute_node", "execute_dag", "compute", "apply"}
        hits = []
        for node in ast.walk(offender):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for loop in ast.walk(node):
                if not isinstance(loop, (ast.For, ast.While)):
                    continue
                for child in ast.walk(loop):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in dispatch_names
                    ):
                        hits.append(node.name)
        assert hits == ["evaluate"], "the structural guard would not catch a second evaluator"

    def test_the_preview_defines_no_per_block_arithmetic(self):
        """The transport helpers sample values; none of them compute one.

        A rolling window, an exponential weighting or a comparison against a threshold in
        this module would be the beginning of the second evaluator. ``_json_number`` uses
        ``math.isfinite``, which is a JSON-safety check on a value that already exists.
        """
        source = _preview_source(include_helpers=True)
        for token in (
            "rolling(",
            "ewm(",
            ".diff(",
            ".pct_change(",
            "np.convolve",
            "cumsum(",
        ):
            assert token not in source, (
                f"the preview path computes something ({token}); it must only transport "
                "what the engine produced"
            )


def _preview_source(include_helpers: bool = False) -> str:
    """The preview endpoint's source, optionally with its transport helpers."""
    names = ["preview_node"]
    if include_helpers:
        names += [
            "_preview_window",
            "_preview_market",
            "_preview_frame",
            "_preview_closure",
            "_series_sample",
            "_matrix_sample",
            "_output_sample",
            "_node_outputs_payload",
            "_json_number",
            "_timestamp_labels",
        ]
    chunks = []
    for name in names:
        target = getattr(SO, name)
        chunks.append(inspect.getsource(getattr(target, "__wrapped__", target)))
    return "\n".join(chunks)
