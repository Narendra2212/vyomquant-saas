# -*- coding: utf-8 -*-
"""tests/test_task_9_3_execution_trace.py

The trace the runtime already recorded, surfaced for one node. Not a third trace store.

Spec: strategy-builder task 9.3. Requirement 24.6 - "THE Strategy_Builder SHALL display the
recorded execution trace for a selected node, comprising that node's inputs, outputs,
duration and recorded failures." ``design.md`` -> Observability: "``dag_engine.ExecutionTracer``
already records per-node inputs, outputs, duration and failures, and ``signal_trace_engine.py``
records signal provenance. Both are reused".

WHAT THIS FILE ASSERTS, AND WHY EACH PART IS HERE
-------------------------------------------------
* **The four subjects reach the wire.** Inputs (one row per bound port, with the recorded
  type and shape), the output, the duration and the run's recorded failures - read out of
  the tracer the engine already owns, on a real run of a real compiled plan.
* **"Reused" is structural, not a claim in a docstring.** ``ExecutionTracer`` has one
  serialiser (:meth:`entry_as_dict`) and both the whole-run and per-node paths go through
  it, so a field added on one cannot be missing from the other. And the router's trace
  functions read the engine and record nothing - asserted with ``ast``, so a later edit that
  starts a third store is a red test.
* **The failure an author can act on is the earliest one at or upstream of the selection**,
  never the newest and never one downstream. Pinned at chosen points and under Hypothesis
  over arbitrary failure orders, because "which failure do we show?" is the whole difference
  between pointing at the block to fix and pointing at its victim.
* **"Did not execute" is a distinct fact** from a node that ran and produced nothing.
* **The controls the endpoint must not weaken.** Authentication, the rate limit, the double
  ownership check, another tenant's strategy as **404 rather than 403**, and no credential or
  venue anywhere in the payload (SB-06, Requirement 12.1).
* **Migrations 004-004e are unapplied here.** Nothing on the trace path reads a column, and
  an unreadable signal-trace store degrades to "not available" with a sentence rather than a
  500.

WHAT IS SUPPLIED AND WHAT IS REAL
---------------------------------
Two things are supplied, both outside the property under test: the **bars**
(``_fetch_preview_bars`` is the endpoint's one I/O boundary) and the **strategy row** (a fake
service applying the real ``id`` + ``user_id`` predicate, so the cross-tenant 404 is real).
The registry, the graphs, the validator, ``StrategyCompiler``, ``plan_to_engine_graph``,
``DAGEngine``, ``ExecutionTracer`` and ``NodeIssueLog`` are the shipped objects.
"""

import ast
import inspect
import os
import sys

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.dag_engine import DAGEngine, ExecutionTracer
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.routers import strategy_operations as SO

POOL_BARS = 1_500

OWNER = {
    "id": "usr_trace_owner",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}

INTRUDER = {
    "id": "usr_trace_intruder",
    "email": "intruder@example.com",
    "role": "authenticated",
    "access_token": "token_intruder",
}

STRATEGY_ID = "stg_trace_0001"

#: Every word that must never appear as a key in a Builder payload (SB-06, Requirement
#: 12.1). The same vocabulary task 9.1 pinned for metric labels and 9.2 for alert payloads.
FORBIDDEN_KEY_WORDS = (
    "exchange",
    "venue",
    "api_key",
    "apikey",
    "secret",
    "passphrase",
    "password",
    "token",
    "credential",
)


def preview_path(node_id: str, strategy_id: str = STRATEGY_ID) -> str:
    return f"/api/strategy-operations/strategies/{strategy_id}/nodes/{node_id}/preview"


# ---------------------------------------------------------------------------
# The real registry, real graphs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    return registry_module.get_registry()


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def _required(descriptor):
    values = {}
    for spec in descriptor.params:
        value = spec.default if spec.default is not None else spec.example
        if spec.required and value is not None:
            values[spec.key] = value
    return values


def graph_with(reg, extra_nodes, wiring, *, symbol="ETH/USDT", timeframe="5m"):
    """``ohlcv_feed -> <extra nodes> -> gt -> action``, validated."""
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
    """One EMA on close: a node with two bound inputs and one produced series."""
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
def divide_by_zero_graph(reg):
    """``close / 0``: a recorded numeric condition, which is what a trace is *for*."""
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


@pytest.fixture(scope="module")
def slow_graph(reg):
    """An SMA whose warmup is far longer than a short feed can supply.

    The point is a run that genuinely **fails inside the engine**: the pipeline guard refuses
    a produced series that is mostly NaN, which is a real runtime control and the reason a
    preview cannot render a mostly-warmup window. The failure is what puts a recorded
    failure in the tracer, and that is the branch Requirement 24.6 matters most in.
    """
    sma = _node(reg, "sma", window=400)
    graph, ids = graph_with(
        reg,
        [sma],
        lambda data, gate, threshold: [
            EdgeSpec.create(data.id, "close", sma.id, "series"),
            EdgeSpec.create(sma.id, "value", gate.id, "left"),
        ],
    )
    return graph, {**ids, "sma": sma.id}


@pytest.fixture(scope="module")
def pool():
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
            "volume": pd.Series(np.linspace(1_000.0, 9_000.0, POOL_BARS), index=stamps),
        },
        index=stamps,
    )


def ccxt_rows(frame: pd.DataFrame, limit: int):
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


# ---------------------------------------------------------------------------
# The two supplied things
# ---------------------------------------------------------------------------


class FakeStrategyService:
    """``StrategyService.get_strategy``'s ownership predicate, and nothing else.

    Both halves of the real ``.eq("id", …).eq("user_id", …)`` filter are applied, so "another
    tenant's strategy is a 404" is a property this fake can actually fail. Every other
    service method is absent on purpose: a trace path that reached for one would raise.
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
    """The endpoint's one I/O boundary, replaced by a deterministic window.

    ``serves`` caps what the feed can actually supply, which is how a venue with limited
    history behaves and how this file produces a genuine in-engine failure.
    """

    def __init__(self, pool, serves=None):
        self.pool = pool
        self.serves = serves
        self.calls = []

    async def __call__(self, symbol, timeframe, bars):
        wanted = int(bars) if self.serves is None else min(int(bars), int(self.serves))
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "bars": wanted})
        return ccxt_rows(self.pool, wanted)


@pytest.fixture
def feed(pool, monkeypatch):
    recorder = RecordingFeed(pool)
    monkeypatch.setattr(SO, "_fetch_preview_bars", recorder)
    return recorder


@pytest.fixture
def short_feed(pool, monkeypatch):
    """A feed that can only supply 60 bars, whatever is asked of it."""
    recorder = RecordingFeed(pool, serves=60)
    monkeypatch.setattr(SO, "_fetch_preview_bars", recorder)
    return recorder


@pytest.fixture
def service(monkeypatch):
    state = FakeStrategyService(
        [{"id": STRATEGY_ID, "user_id": OWNER["id"], "name": "Traced", "version": {}}]
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


def post_preview(client, node_id, graph, **body):
    return client.post(preview_path(node_id), json={"blueprint": graph.to_dict(), **body})


def keys_of(payload):
    """Every key appearing anywhere in a nested payload."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(str(key))
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(payload)
    return found


# ---------------------------------------------------------------------------
# 1. The recorder, on its own terms
# ---------------------------------------------------------------------------


class TestTheTracerIsTheOneRecorder:
    def test_a_node_is_read_back_with_the_inputs_the_run_bound(self):
        tracer = ExecutionTracer()
        tracer.start_node(
            "n1",
            "indicator",
            {"series": pd.Series([1.0, 2.0, 3.0]), "window": 20},
        )
        tracer.complete_node("n1", pd.Series([1.0, 2.0, 3.0]), 4.5)

        entries = tracer.node_trace_as_dict("n1")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["input_shape"]["series"] == {"type": "Series", "length": 3}
        assert entry["input_shape"]["window"] == {"type": "scalar", "value": 20}
        assert entry["input_types"]["series"].startswith("Series[")
        assert entry["output_shape"] == {"type": "Series", "length": 3}
        assert entry["execution_time_ms"] == 4.5
        assert entry["status"] == "success"
        # A plain dict knows no ports, and that reads as unknown rather than as no inputs.
        assert entry["input_ports"] is None

    def test_the_port_view_is_recorded_when_the_inputs_carry_one(self):
        """`input_shape` is keyed by upstream node id, because that is how `PortInputs`
        addresses a value. The port view is recorded **alongside** it, from the object's own
        `port_sources`, so an author can be shown the port on their own block."""
        from backend_app.backend.dag_engine import PortInputs

        inputs = PortInputs(
            {"n_up": pd.Series([1.0, 2.0])}, {"series": ["n_up"]}
        )
        tracer = ExecutionTracer()
        tracer.start_node("n1", "indicator", inputs)
        tracer.complete_node("n1", pd.Series([1.0, 2.0]), 1.0)

        entry = tracer.node_trace_as_dict("n1")[0]
        # The id-keyed maps are untouched: `get_execution_trace` already publishes them.
        assert entry["input_shape"] == {"n_up": {"type": "Series", "length": 2}}
        assert entry["input_ports"] == {"series": ["n_up"]}

    def test_a_node_started_twice_keeps_both_attempts(self):
        """Reporting only the last would hide a first attempt that failed."""
        tracer = ExecutionTracer()
        tracer.start_node("n1", "indicator", {})
        tracer.complete_node("n1", None, 1.0, status="fail", error_message="first")
        tracer.start_node("n1", "indicator", {})
        tracer.complete_node("n1", pd.Series([1.0]), 2.0)

        entries = tracer.node_trace_as_dict("n1")
        assert [item["status"] for item in entries] == ["fail", "success"]
        assert [item["error_message"] for item in entries] == ["first", None]

    def test_failures_are_the_whole_run_oldest_first(self):
        """Requirement 24.6's "recorded failures". The commonest cause is upstream."""
        tracer = ExecutionTracer()
        for node_id in ("a", "b", "c"):
            tracer.start_node(node_id, "indicator", {})
            tracer.complete_node(
                node_id,
                None,
                1.0,
                status="fail" if node_id in {"a", "c"} else "success",
                error_message=f"{node_id} broke" if node_id in {"a", "c"} else None,
            )

        assert [item["node_id"] for item in tracer.failures_as_dict()] == ["a", "c"]
        # `get_last_failure` reports the newest, which is exactly why the list exists.
        assert tracer.get_last_failure().node_id == "c"

    def test_one_serialiser_serves_both_paths(self):
        """A field added to an entry cannot appear on one path and not the other."""
        tracer = ExecutionTracer()
        tracer.start_node("n1", "indicator", {"series": pd.Series([1.0])})
        tracer.complete_node("n1", pd.Series([1.0]), 1.25)

        assert tracer.node_trace_as_dict("n1") == tracer.get_trace_as_dict()
        assert tracer.failures_as_dict() == []
        source = inspect.getsource(ExecutionTracer)
        # Both call the same static method rather than each building a dict.
        assert source.count("entry_as_dict(") >= 3

    def test_a_disabled_tracer_records_nothing_and_still_answers(self):
        tracer = ExecutionTracer(enabled=False)
        tracer.start_node("n1", "indicator", {})
        tracer.complete_node("n1", None, 1.0)

        assert tracer.node_trace_as_dict("n1") == []
        assert tracer.failures_as_dict() == []


# ---------------------------------------------------------------------------
# 2. The projection, over real runs
# ---------------------------------------------------------------------------


def run_plan(reg, graph, frame):
    """Compile and execute ``graph`` through the shipped chain, tracing enabled."""
    plan = SC.compile_graph(graph, reg)
    nodes, edges = SC.plan_to_engine_graph(plan, reg)
    engine = DAGEngine(enable_tracing=True, enable_event_buffer=False)
    order = [node["id"] for node in nodes]
    failure = None
    try:
        engine.execute_dag(nodes, edges, frame)
    except Exception as exc:  # a failed run is a first-class case here
        failure = exc
    return engine, order, failure


class TestTheProjectionReadsWhatTheRunRecorded:
    def test_the_four_subjects_are_on_the_payload(self, reg, ema_graph, pool):
        graph, ids = ema_graph
        engine, order, failure = run_plan(reg, graph, pool.tail(300))
        assert failure is None

        payload = SO._node_trace_payload(engine, ids["ema"], order)

        assert payload["node_id"] == ids["ema"]
        assert payload["status"] == "success"
        assert payload["recorded"] is True
        # Duration: a figure the tracer measured, not one composed here.
        assert payload["duration_ms"] is not None and payload["duration_ms"] >= 0.0
        # Inputs: one row per binding, each naming the port on this block, the upstream node
        # it came from, and its recorded type and shape.
        rows = payload["executions"][0]["inputs"]
        assert "series" in {row["port"] for row in rows}
        assert ids["data"] in {row["source"] for row in rows}
        for row in rows:
            assert set(row) == {"port", "source", "type", "shape"}
        # Output.
        assert payload["executions"][0]["output"]["shape"]["type"] in {"Series", "DataFrame"}
        # Failures: none on a clean run, and the key is present rather than omitted.
        assert payload["failures"] == []
        assert payload["blocking_failure"] is None
        assert payload["executed_nodes"] == order

    def test_the_inputs_are_zipped_from_the_three_maps_the_recorder_writes(
        self, reg, ema_graph, pool
    ):
        """The recorder writes ``input_shape`` and ``input_types`` keyed by upstream node id
        and ``input_ports`` as the port view; a renderer wants one row per binding. The shape
        keeps its own ``type`` key *inside* ``shape``."""
        graph, ids = ema_graph
        engine, order, _ = run_plan(reg, graph, pool.tail(300))

        payload = SO._node_trace_payload(engine, ids["ema"], order)
        row = next(
            item for item in payload["executions"][0]["inputs"] if item["port"] == "series"
        )

        assert row["source"] == ids["data"]
        assert row["type"].startswith("Series[")
        assert row["shape"]["type"] == "Series"
        # The declared type and the shape's own type are different facts and neither
        # overwrote the other.
        assert row["type"] != row["shape"]["type"]

    def test_an_input_bound_to_no_declared_port_is_still_reported(self):
        """A legacy plain-dict call site records no port view. The binding is kept, with a
        null port: dropping it would lose an input the run genuinely bound."""
        engine = DAGEngine(enable_tracing=True, enable_event_buffer=False)
        engine.tracer.start_node("n1", "indicator", {"n_up": pd.Series([1.0])})
        engine.tracer.complete_node("n1", pd.Series([1.0]), 1.0)

        payload = SO._node_trace_payload(engine, "n1", ["n_up", "n1"])

        assert payload["executions"][0]["inputs"] == [
            {
                "port": None,
                "source": "n_up",
                "type": "Series[float64]",
                "shape": {"type": "Series", "length": 1},
            }
        ]

    def test_a_recorded_numeric_condition_arrives_with_its_sentence(
        self, reg, divide_by_zero_graph, pool
    ):
        """Requirement 20.4 inside 24.6: this is why a bar is empty rather than wrong."""
        graph, ids = divide_by_zero_graph
        engine, order, _ = run_plan(reg, graph, pool.tail(300))

        payload = SO._node_trace_payload(engine, ids["divider"], order)

        assert payload["conditions"], "dividing by zero must record a condition"
        condition = payload["conditions"][0]
        assert condition["code"] == "DIVISION_BY_ZERO"
        # The sentence is composed on the server, once, out of the engine's own vocabulary.
        assert condition["code"] in condition["display"]
        assert "bar" in condition["display"]
        assert condition["display"] in payload["summary"]

    def test_a_node_that_never_ran_is_not_a_node_that_produced_nothing(self, reg, ema_graph, pool):
        graph, ids = ema_graph
        engine, order, _ = run_plan(reg, graph, pool.tail(300))

        payload = SO._node_trace_payload(engine, "n_never_reached", order)

        assert payload["status"] == "not_executed"
        assert payload["recorded"] is False
        assert payload["executions"] == []
        # Not zero: a node nobody timed and a node that took no time are different facts.
        assert payload["duration_ms"] is None
        assert "did not execute" in payload["summary"]

    def test_a_failed_run_still_carries_the_trace_and_names_the_upstream_block(
        self, reg, slow_graph, pool
    ):
        """The branch that matters most: the run that produced nothing."""
        graph, ids = slow_graph
        engine, order, failure = run_plan(reg, graph, pool.tail(60))
        assert failure is not None, "a 400-bar warmup over 60 bars must fail in the engine"

        payload = SO._node_trace_payload(engine, ids["gate"], order)

        assert payload["failures"], "the tracer holds the failure even though execute_dag raised"
        assert payload["blocking_failure"] is not None
        # The gate never ran; the block to fix is the one upstream of it.
        assert payload["blocking_failure"]["node_id"] != ids["gate"]
        assert payload["status"] == "not_executed"
        assert payload["blocking_failure"]["node_id"] in payload["summary"]

    def test_two_executions_of_one_node_are_summed_not_replaced(self):
        engine = DAGEngine(enable_tracing=True, enable_event_buffer=False)
        for duration in (4.0, 6.5):
            engine.tracer.start_node("n1", "indicator", {})
            engine.tracer.complete_node("n1", pd.Series([1.0]), duration)

        payload = SO._node_trace_payload(engine, "n1", ["n1"])

        assert len(payload["executions"]) == 2
        assert payload["duration_ms"] == pytest.approx(10.5)


# ---------------------------------------------------------------------------
# 3. Which failure is reported - the property, not the example
# ---------------------------------------------------------------------------


def engine_with_failures(order, failing):
    """An engine whose tracer recorded a run of ``order`` in which ``failing`` failed."""
    engine = DAGEngine(enable_tracing=True, enable_event_buffer=False)
    for node_id in order:
        engine.tracer.start_node(node_id, "indicator", {})
        if node_id in failing:
            engine.tracer.complete_node(
                node_id, None, 1.0, status="fail", error_message=f"{node_id} broke"
            )
        else:
            engine.tracer.complete_node(node_id, pd.Series([1.0]), 1.0)
    return engine


class TestTheBlockingFailureIsTheEarliestOneThatMatters:
    def test_a_failure_downstream_of_the_selection_is_not_blocking(self):
        """A block that failed *after* this one cannot be why this one has nothing."""
        order = ["a", "b", "c"]
        engine = engine_with_failures(order, {"c"})

        payload = SO._node_trace_payload(engine, "b", order)

        assert payload["blocking_failure"] is None
        # It is still in the run's failure list, because the list is the run's.
        assert [item["node_id"] for item in payload["failures"]] == ["c"]

    def test_the_earliest_upstream_failure_wins_over_the_newest(self):
        order = ["a", "b", "c", "d"]
        engine = engine_with_failures(order, {"a", "c"})

        payload = SO._node_trace_payload(engine, "d", order)

        assert payload["blocking_failure"]["node_id"] == "a"

    @settings(max_examples=150, deadline=None)
    @given(
        size=st.integers(min_value=1, max_value=8),
        failing_positions=st.sets(st.integers(min_value=0, max_value=7), max_size=8),
        selected_position=st.integers(min_value=0, max_value=7),
    )
    def test_the_blocking_failure_is_always_the_earliest_at_or_upstream(
        self, size, failing_positions, selected_position
    ):
        """Over any run and any selection: the reported failure is the earliest one at or
        before the selected node in the compiler's order, or none at all.

        Stated as a property because the alternative - a failure chosen from the wrong end,
        or one taken from downstream - is a panel that points at the victim rather than the
        cause, and no finite set of examples pins that.
        """
        order = [f"n{index}" for index in range(size)]
        failing = {order[position] for position in failing_positions if position < size}
        selected = order[selected_position % size]
        engine = engine_with_failures(order, failing)

        payload = SO._node_trace_payload(engine, selected, order)

        upstream = [
            node_id
            for node_id in order[: order.index(selected) + 1]
            if node_id in failing
        ]
        if not upstream:
            assert payload["blocking_failure"] is None
        else:
            assert payload["blocking_failure"]["node_id"] == upstream[0]
        # The run's whole failure list is reported either way, in the order recorded.
        assert [item["node_id"] for item in payload["failures"]] == [
            node_id for node_id in order if node_id in failing
        ]


# ---------------------------------------------------------------------------
# 4. The endpoint
# ---------------------------------------------------------------------------


class TestTheTraceRidesThePreviewResponse:
    def test_a_successful_preview_carries_the_trace_for_the_selected_node(
        self, ema_graph, as_owner, service, feed
    ):
        graph, ids = ema_graph
        response = post_preview(as_owner, ids["ema"], graph)
        assert response.status_code == 200, response.text

        trace = response.json()["trace"]
        assert trace["node_id"] == ids["ema"]
        assert trace["status"] == "success"
        assert trace["duration_ms"] is not None
        assert trace["executions"][0]["inputs"], "the bound inputs are Requirement 24.6's first"
        assert trace["executions"][0]["output"]["type"] is not None
        assert trace["failures"] == []
        assert trace["summary"]
        # `signal_provenance` is answered, including when the answer is "nothing yet".
        assert set(trace["signal_provenance"]) == {
            "available",
            "records",
            "traces_seen",
            "message",
        }
        assert trace["signal_provenance"]["message"]

    def test_a_refused_run_carries_the_same_trace_a_success_would(
        self, slow_graph, as_owner, service, short_feed
    ):
        """422 ``PREVIEW_EXECUTION_FAILED`` - the run that produced nothing."""
        graph, ids = slow_graph
        response = post_preview(as_owner, ids["sma"], graph)
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "PREVIEW_EXECUTION_FAILED"

        trace = detail["trace"]
        assert trace["node_id"] == ids["sma"]
        # The keys are the same set the 200 path publishes: one payload builder, so a field
        # added later cannot appear on the success path only.
        assert set(trace) == {
            "node_id",
            "status",
            "recorded",
            "duration_ms",
            "executions",
            "failures",
            "blocking_failure",
            "conditions",
            "executed_nodes",
            "summary",
            "signal_provenance",
        }
        assert trace["summary"]

    def test_the_trace_costs_no_second_execution(self, ema_graph, as_owner, service, feed):
        """One run, one trace. A second execution would be two answers to one question."""
        graph, ids = ema_graph
        assert post_preview(as_owner, ids["ema"], graph).status_code == 200

        assert len(feed.calls) == 1

    def test_no_trace_route_exists_at_all(self):
        """There is deliberately no ``…/trace`` endpoint: an ``ExecutionTracer`` lives as
        long as the engine that ran, so a second endpoint would have to re-execute."""
        from backend_app.main import app

        paths = [route.path for route in app.routes if hasattr(route, "path")]
        assert [path for path in paths if path.endswith("/trace")] == []
        assert [path for path in paths if path.endswith("/traces")] == []


# ---------------------------------------------------------------------------
# 5. The controls
# ---------------------------------------------------------------------------


class TestNoControlIsWeakened:
    def test_an_unauthenticated_request_gets_no_trace(self, ema_graph, anonymous):
        graph, ids = ema_graph
        response = anonymous.post(
            preview_path(ids["ema"]), json={"blueprint": graph.to_dict()}
        )

        assert response.status_code in (401, 403)
        # The key, not the substring: this file's own strategy id contains the word, and
        # matching that would make the assertion pass for the wrong reason.
        assert "trace" not in keys_of(response.json())

    def test_another_tenants_strategy_is_404_and_not_403(
        self, ema_graph, as_intruder, service, feed
    ):
        """404, identically to a missing strategy, so existence does not leak."""
        graph, ids = ema_graph
        response = post_preview(as_intruder, ids["ema"], graph)

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "STRATEGY_NOT_FOUND"
        # Nothing ran, so there is nothing to trace.
        assert feed.calls == []
        assert "trace" not in keys_of(response.json())

    def test_ownership_is_resolved_before_anything_is_executed(
        self, ema_graph, as_intruder, service, feed
    ):
        graph, ids = ema_graph
        post_preview(as_intruder, ids["ema"], graph)

        # The double check: the service is asked, with the caller's own id, and its own
        # predicate matches on `id` *and* `user_id`.
        assert service.calls == [
            {"user_id": INTRUDER["id"], "strategy_id": STRATEGY_ID}
        ]

    def test_the_route_still_carries_its_rate_limit(self):
        source = inspect.getsource(SO)
        marker = '@router.post("/strategy-operations/strategies/{strategy_id}/nodes/{node_id}/preview")'
        assert marker in source
        following = source.split(marker, 1)[1]
        assert following.lstrip().startswith("@limiter.limit(")

    def test_no_credential_or_venue_key_reaches_the_trace(
        self, ema_graph, as_owner, service, feed
    ):
        """SB-06 / Requirement 12.1, over the whole nested payload rather than its top level."""
        graph, ids = ema_graph
        trace = post_preview(as_owner, ids["ema"], graph).json()["trace"]

        for key in keys_of(trace):
            words = key.lower().replace("-", "_").split("_")
            for forbidden in FORBIDDEN_KEY_WORDS:
                assert forbidden not in words and forbidden != key.lower(), (
                    f"trace payload carries key '{key}'"
                )

    def test_the_trace_names_no_venue_even_in_a_failure_message(
        self, slow_graph, as_owner, service, short_feed
    ):
        graph, ids = slow_graph
        detail = post_preview(as_owner, ids["sma"], graph).json()["detail"]

        rendered = str(detail["trace"]).lower()
        for venue in ("binance", "coinbase", "kraken", "bybit", "okx"):
            assert venue not in rendered


# ---------------------------------------------------------------------------
# 6. Migrations 004-004e are unapplied here
# ---------------------------------------------------------------------------


class TestTheTracePathReadsNoColumn:
    def test_an_unreadable_signal_trace_store_degrades_rather_than_500ing(
        self, ema_graph, as_owner, service, feed, monkeypatch
    ):
        """A diagnostic that could not be read must not turn a served preview into a 500."""
        import backend_app.backend.signal_trace_engine as STE

        async def explode(*args, **kwargs):
            raise RuntimeError("the store was never started")

        monkeypatch.setattr(STE.trace_engine, "get_recent_traces", explode)

        graph, ids = ema_graph
        response = post_preview(as_owner, ids["ema"], graph)

        assert response.status_code == 200, response.text
        provenance = response.json()["trace"]["signal_provenance"]
        assert provenance["available"] is False
        assert provenance["records"] == []
        assert provenance["message"]

    def test_the_honest_empty_answer_is_never_an_absent_key(self):
        payload = SO._signal_provenance_unavailable("nothing to report")

        assert payload == {
            "available": False,
            "records": [],
            "traces_seen": 0,
            "message": "nothing to report",
        }

    def test_the_trace_functions_read_no_relation_and_no_column(self):
        """Nothing on this path touches a table, so 004-004e being unapplied changes it not
        at all. Asserted over the source of the four functions rather than by prose."""
        for function in (
            SO._node_trace_payload,
            SO._trace_entry_payload,
            SO._condition_display,
            SO._trace_summary,
        ):
            source = inspect.getsource(function)
            for token in (".table(", ".select(", ".eq(", "supabase", "migration"):
                assert token not in source, f"{function.__name__} reaches for {token}"


# ---------------------------------------------------------------------------
# 7. No third trace store
# ---------------------------------------------------------------------------


class TestNothingHereRecordsAnything:
    def test_the_router_defines_no_recorder(self):
        """``design.md`` names two stores and reuses both. A third one in a router would be
        a store nobody could correlate with a run, and it would outlive the request."""
        source = inspect.getsource(SO)
        tree = ast.parse(source)

        defined_classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        for name in defined_classes:
            assert "Trace" not in name, f"router defines a trace type: {name}"

        # No module-level mutable store either.
        module_assignments = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        for name in module_assignments:
            assert "TRACE_STORE" not in name.upper()

    def test_the_projection_only_ever_reads_the_engine(self):
        """Every fact on the payload comes from a tracer or issue-log read."""
        source = inspect.getsource(SO._node_trace_payload)

        assert "engine.get_node_execution_trace(" in source
        assert "engine.get_execution_failures(" in source
        assert "engine.get_node_issues(" in source
        # Nothing is written back into the engine, and no clock is started.
        for token in ("start_node(", "complete_node(", "time.perf_counter", "time.time("):
            assert token not in source
