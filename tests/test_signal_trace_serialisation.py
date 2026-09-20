"""
tests/test_signal_trace_serialisation.py - production-launch-hardening task 1, CLUSTER D.

Requirements 1.29 / 2.29. `design.md` §Hypothesized Root Cause (cluster D), Property 10:
*"For any signal trace, `json.dumps(to_frontend_format())` SHALL succeed, and every stage's
`nodes` SHALL be dicts produced by one shared projection rather than by per-stage
expressions."* This is the regression test tasks 9.4 and 9.5 name.

WHAT THIS FILE IS
-----------------
**Bug condition exploration tests.** Every test in sections 1-3 is EXPECTED TO FAIL against
the current tree (`F`). The failure is the deliverable: it is the counterexample that proves
the defect exists, and the same assertion is what validates the fix in task 11.1. Nothing here
is a symptom patch and no assertion has been weakened to make a run green.

THE DEFECT, IN ONE SENTENCE
---------------------------
`SignalTraceRecord.to_frontend_format` projects its node traces to dicts in one of the three
stages that carry nodes, and puts the raw dataclass instances into the other two - so the
response cannot be serialised at all, and the two stages carry no readable detail even if it
could.

The three `nodes`-bearing stages, and what each one puts in the field:

| Stage | Line | `nodes` contains | JSON-serialisable |
|---|---|---|---|
| 1 `market_data` | `:313-317` | raw `DAGNodeTrace` instances | **no** |
| 2 `indicators` | `:318-322` | raw `DAGNodeTrace` instances | **no** |
| 3 `dag_nodes` | `:325-341` | dicts, from an inline 8-key projection | yes |

Stage 3's projection is the working one and the expected shape for the other two. Transcribed
from `:330-338`, it is::

    {"node_id": n.node_id,
     "type": n.node_type.value,
     "label": n.node_label,
     "inputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in n.inputs],
     "outputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in n.outputs],
     "execution_ms": n.execution_ms,
     "status": n.status.value,
     "error": n.error_message}

`str(io.value)` is what makes it safe: `NodeIO.value` is `Any`, and the fixtures below put a
`Decimal` and a `bool` through it deliberately, because a projection that forwarded the value
unchanged would serialise the `bool` and fail on the `Decimal`.

`EXPECTED_NODE_KEYS` below is that shape written out by hand rather than imported, and
`_stage_three_projection` recomputes it rather than calling the subject. A test that obtained
its expectation from the code under test would pass no matter what that code did.

THE ROOT CAUSE IS THAT THE PROJECTION HAS NO NAME
-------------------------------------------------
`design.md`: *"the `DAGNodeTrace -> dict` projection exists, but as an inline expression inside
one of three stage literals. It could not be reused, so two stages were written without it. The
fix is to name the projection; the three stages then cannot disagree."* Task 9.4 extracts it,
task 9.5 calls it from all three stages. This file asserts the outcome, not the mechanism: any
implementation where the three stages agree satisfies it.

THE THIRD DEFECT AT THE SAME SITE: THREE NODE TYPES REACH NO STAGE AT ALL  (section 3)
--------------------------------------------------------------------------------------
`NodeType` has nine members. Stage 3 excludes five of them by name
(`MARKET_DATA`, `INDICATOR`, `ML_MODEL`, `RISK`, `EXECUTION`, `:339-340`), and stages 1 and 2
take only the first two. So a node trace whose type is `ML_MODEL`, `RISK` or `EXECUTION` is
**silently dropped from every stage** - it appears in no `nodes` list anywhere in the response.

This is not covered by 1.29's wording and is recorded here as a new finding, which is what
`tasks.md` task 1 asks for (*"produces that proof or a new numbered defect"*). It is a real
condition, not a hypothetical: `trace_node_execution` takes `node_type` as a parameter and
accepts any member of the enum, and the three excluded stages downstream
(`ml_inference`, `risk_validation`, `execution`) project from `ml_trace`, `risk_trace` and
`execution_trace` - different objects, with different fields, that a node trace never reaches.
Section 3 parametrises over all nine members for exactly this reason: a single-type test
(`OPERATOR`, say) passes on `F` and sees none of it.

WHAT IS ALREADY MEASURED ABOUT REACH  (section 5)
-------------------------------------------------
Recorded so the disposition is not guessed at. `to_frontend_format` has exactly one caller in
`backend_app/` - `SignalTraceEngine.get_traces_for_frontend` (`:791-799`) - and that method has
**no caller at all**. The HTTP surface does not use either: `routers/signal_trace.py` reads the
engine record through `signal_service._dag_nodes_from_record` (`signal_service.py:8426`), a
**second, independent projection** of the same dataclass that covers every node type and
carries more fields (`cache_hit`, `retry_count`, `started_at`, `ended_at`, per-port `shape`).

Two consequences, both for task 9.4. The defect is presently **latent** rather than serving
broken responses - nothing calls the broken path today. And "one shared projection" has to
reckon with the fact that there are already two, and the one the API actually serves is the
more complete one. Section 5 asserts the caller graph so the claim is reproducible; it does not
decide the question.

HARNESS
-------
No harness. `SignalTraceRecord`, `DAGNodeTrace` and `NodeIO` are plain dataclasses, so every
fixture is constructed directly with fixed timestamps and a fixed `execution_ms` - never through
`DAGNodeTrace.complete()`, which reads the wall clock and would make the byte-for-byte
preservation assertion in section 4 non-deterministic. Same construction style as
`tests/test_task_13_2_signal_trace_detail.py`'s `engine_record`, which builds the same dataclass
for the router tests.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backend_app.backend.signal_trace_engine import (
    DAGNodeTrace,
    ExecutionTrace,
    MLInferenceTrace,
    NodeIO,
    NodeType,
    RiskValidationTrace,
    SignalTraceRecord,
    TraceStatus,
    ValidationResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend_app"

#: The eight keys stage 3 publishes per node, transcribed from `signal_trace_engine.py:330-338`.
#: Written out rather than derived from the subject: this is the contract, and a fix that
#: renamed or dropped one of them would be a frontend break, not an improvement.
EXPECTED_NODE_KEYS = ("node_id", "type", "label", "inputs", "outputs", "execution_ms", "status", "error")

#: Fixed instants, so `execution_ms` and the envelope timestamp are deterministic.
CREATED_AT = datetime(2024, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
NODE_START = datetime(2024, 5, 17, 12, 0, 1, tzinfo=timezone.utc)
NODE_END = datetime(2024, 5, 17, 12, 0, 1, 125_000, tzinfo=timezone.utc)

#: JSON's own scalar set. Anything else in the payload is a serialisation failure waiting to
#: happen, whichever object it is.
JSON_SCALARS = (str, int, float, bool, type(None))


def _node(node_type, node_id, label, *, status=ValidationResult.PASS, error=None):
    """One `DAGNodeTrace`, fully populated, with nothing read off the clock.

    `inputs` carries a `Decimal` and `outputs` a `bool` on purpose. `NodeIO.value` is typed
    `Any`, and stage 3's `str(io.value)` is the step that makes either safe - so a fix that
    forwarded the value unchanged would serialise the `bool` and fail on the `Decimal`, and
    these two fixtures are what would catch it.
    """
    return DAGNodeTrace(
        node_id=node_id,
        node_type=node_type,
        node_label=label,
        start_time=NODE_START,
        end_time=NODE_END,
        execution_ms=125.0,
        inputs=[NodeIO(key="close", value=Decimal("101.5"), dtype="float")],
        outputs=[NodeIO(key="passed", value=True, dtype="bool")],
        status=status,
        error_message=error,
    )


def _stage_three_projection(node):
    """`node` as stage 3 projects it - recomputed here, never read back from the subject.

    This is the expected shape for all three `nodes`-bearing stages. Task 9.4 extracts the
    source's own copy into a named function; this one stays a transcription, because a test
    that imported the projection it is asserting about could only prove the code agrees with
    itself.
    """
    return {
        "node_id": node.node_id,
        "type": node.node_type.value,
        "label": node.node_label,
        "inputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in node.inputs],
        "outputs": [{"key": io.key, "value": str(io.value), "dtype": io.dtype} for io in node.outputs],
        "execution_ms": node.execution_ms,
        "status": node.status.value,
        "error": node.error_message,
    }


def _non_serialisable_sites(payload):
    """Every path inside `payload` holding something `json.dumps` cannot write, and its type.

    Reported as paths rather than as the bare `TypeError`, because `json.dumps` names only the
    first offending object it reaches and stops. Two broken stages then look like one, and a
    fix that repaired `market_data` alone would appear to have worked until the next call.
    """
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str):
                    found.append((f"{path}.<key {key!r}>", type(key).__name__))
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif not isinstance(node, JSON_SCALARS):
            found.append((path, type(node).__name__))

    walk(payload, "$")
    return found


def _stage(payload, name):
    """The one stage named `name`, or a failure saying which stages there were."""
    matches = [stage for stage in payload["pipeline"] if stage.get("stage") == name]
    assert len(matches) == 1, (
        f"expected exactly one {name!r} stage, found {len(matches)}; "
        f"stages are {[stage.get('stage') for stage in payload['pipeline']]}"
    )
    return matches[0]


def _projected_nodes(payload):
    """Every entry in every stage's `nodes`, in pipeline order. Stages without the key skip."""
    return [node for stage in payload["pipeline"] for node in stage.get("nodes", [])]


#: The four node types stage 3 accepts. `OPERATOR` and `LOGIC` are the DAG's own; `ORDERBOOK`
#: and `TICKER` are market-shaped but not `MARKET_DATA`, so `:339-340`'s exclusion list lets
#: them through. Pinned as a set because section 4 asserts stage 3 byte for byte.
STAGE_THREE_NODES = (
    (NodeType.OPERATOR, "n_op", "Crossover"),
    (NodeType.LOGIC, "n_logic", "RSI > 70"),
    (NodeType.ORDERBOOK, "n_book", "Imbalance"),
    (NodeType.TICKER, "n_tick", "Last price"),
)


def _full_trace():
    """A trace with every stage populated: four node types, ML, risk and execution.

    `MARKET_DATA` and `INDICATOR` are present because they are the two defective stages;
    `OPERATOR` and `LOGIC` because stage 3 must keep working unchanged beside them.
    """
    record = SignalTraceRecord(
        trace_id="trc_cluster_d_1",
        signal_id="sig_cluster_d_1",
        strategy_id="stg_cluster_d",
        strategy_name="Cluster D probe",
        bot_id="bot_cluster_d",
        symbol="BTC/USDT",
        exchange="binance",
        created_at=CREATED_AT,
        started_at=CREATED_AT,
        status=TraceStatus.COMPLETED,
        final_decision="EXECUTE",
        total_latency_ms=412.5,
    )

    record.add_node_trace(_node(NodeType.MARKET_DATA, "n_market", "BTC/USDT 1h"))
    record.add_node_trace(_node(NodeType.INDICATOR, "n_rsi", "RSI(14)"))
    for node_type, node_id, label in STAGE_THREE_NODES:
        record.add_node_trace(_node(node_type, node_id, label))

    record.ml_trace = MLInferenceTrace(
        model_id="mdl_1",
        model_version="1.0.0",
        inference_start=NODE_START,
        inference_end=NODE_END,
        inference_ms=12.5,
        prediction="BUY",
        confidence=0.81,
    )
    record.risk_trace = RiskValidationTrace(
        checks=["position_limit"],
        exposure_pct=18.5,
        validation_start=NODE_START,
        validation_end=NODE_END,
        validation_ms=3.25,
        passed=True,
        blocked=False,
    )
    record.execution_trace = ExecutionTrace(
        signal_type="BUY",
        symbol="BTC/USDT",
        order_type="market",
        requested_size=Decimal("0.5"),
        exchange="binance",
        order_id="ord_1",
        status="filled",
        filled_size=Decimal("0.5"),
        filled_price=Decimal("101.5"),
        fill_percent=100.0,
        fees=Decimal("0.05"),
        slippage=Decimal("0.01"),
        exchange_latency_ms=41.0,
    )
    return record


def _single_node_trace(node_type):
    """A trace holding exactly one node trace, of `node_type`. Nothing else populated."""
    record = SignalTraceRecord(
        trace_id=f"trc_{node_type.value}",
        signal_id=f"sig_{node_type.value}",
        strategy_id="stg_cluster_d",
        strategy_name="Cluster D probe",
        bot_id="bot_cluster_d",
        symbol="BTC/USDT",
        exchange="binance",
        created_at=CREATED_AT,
        status=TraceStatus.RUNNING,
    )
    node = _node(node_type, f"n_{node_type.value}", f"a {node_type.value} node")
    record.add_node_trace(node)
    return record, node


# ══════════════════════════════════════════════════════════════════════════
# 1. THE RESPONSE CAN BE SERIALISED  (`signal_trace_engine.py:313-323`, Requirement 1.29)
# ══════════════════════════════════════════════════════════════════════════


def test_a_trace_formatted_for_the_frontend_can_be_serialised():
    """`json.dumps(trace.to_frontend_format())` succeeds. The whole clause, in one line.

    COUNTEREXAMPLE OBSERVED ON `F` (signal_trace_engine.py:315 and :320)::

        TypeError: Object of type DAGNodeTrace is not JSON serializable

        non-serialisable paths:
          $.pipeline[0].nodes[0]  ->  DAGNodeTrace      (stage "market_data", :315)
          $.pipeline[1].nodes[0]  ->  DAGNodeTrace      (stage "indicators",  :320)

    Two sites, one per stage, both holding the dataclass instance the list comprehension
    selected without projecting it. Everything else in the payload is already JSON-native:
    the envelope's `status` goes through `.value`, the ML and risk stages read scalars off
    their own traces, and the execution stage `str()`s its four `Decimal`s. Stages 1 and 2 are
    the only two places a raw object survives.

    The paths are reported rather than the bare `TypeError` because `json.dumps` stops at the
    first offending object it reaches. On `F` that is always `market_data`, so a fix applied to
    that stage alone would make this test pass while `indicators` stayed broken - and the next
    trace with an indicator node and no market-data node would fail in production instead of
    here.
    """
    payload = _full_trace().to_frontend_format()

    sites = _non_serialisable_sites(payload)
    assert sites == [], (
        "the frontend payload holds objects json.dumps cannot write: "
        + ", ".join(f"{path} -> {type_name}" for path, type_name in sites)
    )

    body = json.dumps(payload)
    assert json.loads(body)["id"] == "trc_cluster_d_1", "and it round-trips"


# ══════════════════════════════════════════════════════════════════════════
# 2. STAGES 1 AND 2 CARRY NODE DETAIL, PROJECTED AS STAGE 3 PROJECTS IT
#    (Requirement 1.29 / 2.29)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "stage_name, node_type, defect_line",
    [
        ("market_data", NodeType.MARKET_DATA, "signal_trace_engine.py:313-317"),
        ("indicators", NodeType.INDICATOR, "signal_trace_engine.py:318-322"),
    ],
)
def test_the_first_two_stages_project_their_nodes_the_way_the_third_does(stage_name, node_type, defect_line):
    """Same dataclass, same three stages, one shape. Stage 3's shape, because it is the one that works.

    COUNTEREXAMPLE OBSERVED ON `F` - `nodes[0]` in each of the two stages, `repr` shortened::

        market_data -> [DAGNodeTrace(node_id='n_market', node_type=<NodeType.MARKET_DATA: …>,
                                     node_label='BTC/USDT 1h', start_time=datetime(…), …)]
        indicators  -> [DAGNodeTrace(node_id='n_rsi', node_type=<NodeType.INDICATOR: …>,
                                     node_label='RSI(14)', start_time=datetime(…), …)]

    Not a dict, so: no `node_id` key, no `type`, no `label`, no `inputs`, no `outputs`, no
    `execution_ms`, no `status`, no `error`. The stage's own `status` field says `"completed"`
    in both cases, because `any(n.node_type == …)` is true - so the response reports two
    completed stages whose detail is unreadable.

    Both stages are parametrised rather than asserted once because they are two separate
    literals in the source. They were written independently, they fail independently, and
    `design.md`'s fix - one named projection called three times - is the only shape in which
    they cannot drift apart again.

    Asserted against `_stage_three_projection`, which is transcribed in this file rather than
    imported: the claim is that stages 1 and 2 match the CONTRACT stage 3 implements, not that
    all three call the same function. Either fix satisfies it.
    """
    trace = _full_trace()
    payload = trace.to_frontend_format()

    expected = [_stage_three_projection(node) for node in trace.node_traces if node.node_type is node_type]
    assert expected, "the fixture must contain a node of this type for the test to mean anything"

    stage = _stage(payload, stage_name)
    observed = stage["nodes"]

    assert observed and all(isinstance(node, dict) for node in observed), (
        f"{stage_name}: nodes holds {[type(node).__name__ for node in observed]} rather than "
        f"dicts ({defect_line}). Stage 3 at :325-341 projects the same dataclass correctly."
    )
    assert [sorted(node) for node in observed] == [sorted(EXPECTED_NODE_KEYS)] * len(expected), (
        f"{stage_name}: the projected keys differ from stage 3's eight"
    )
    assert observed == expected, (
        f"{stage_name}: node detail does not match the projection stage 3 already performs"
    )


def test_the_stage_status_flags_still_agree_with_the_node_detail():
    """A stage that reports `completed` has the detail to show for it, and one that reports
    `pending` has no nodes.

    COUNTEREXAMPLE OBSERVED ON `F`: `market_data` and `indicators` both report
    `status: "completed"` while their `nodes` cannot be read at all - the flag is computed from
    the same `node_traces` scan that fills the field, so it is right about presence and blind
    to shape. That disagreement is what makes the broken stages render as empty-but-complete on
    the frontend rather than as an error.

    Asserted as a relation rather than as two literals so it holds for any trace: `completed`
    if and only if there is node detail.
    """
    trace = _full_trace()
    payload = trace.to_frontend_format()

    for stage_name in ("market_data", "indicators", "dag_nodes"):
        stage = _stage(payload, stage_name)
        readable = [node for node in stage.get("nodes", []) if isinstance(node, dict) and node.get("node_id")]
        if stage["status"] == "completed":
            assert readable, (
                f"{stage_name} reports completed with no readable node detail: "
                f"{[type(node).__name__ for node in stage.get('nodes', [])]}"
            )
        else:
            assert not stage.get("nodes"), f"{stage_name} reports {stage['status']} but carries nodes"


# ══════════════════════════════════════════════════════════════════════════
# 3. EVERY NodeType, NOT JUST ONE  (new finding: three types reach no stage)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("node_type", list(NodeType), ids=lambda node_type: node_type.value)
def test_a_node_trace_of_any_type_is_projected_into_some_stage(node_type):
    """Whatever type a recorded node has, its detail reaches the response, projected once.

    `trace_node_execution` accepts any `NodeType` member, so all nine are reachable inputs.
    Parametrised over the whole enum because the three outcomes are different and a
    single-type test sees at most one of them.

    COUNTEREXAMPLE OBSERVED ON `F`, by member - 5 of 9 fail::

        market_data  -> $.pipeline[0].nodes[0] -> DAGNodeTrace        raw object   (stage 1)
        indicator    -> $.pipeline[1].nodes[0] -> DAGNodeTrace        raw object   (stage 2)
        ml_model     -> projected into NO stage: 0 node dicts in the whole payload
        risk         -> projected into NO stage: 0 node dicts in the whole payload
        execution    -> projected into NO stage: 0 node dicts in the whole payload
        operator     -> projected correctly into dag_nodes
        logic        -> projected correctly into dag_nodes
        orderbook    -> projected correctly into dag_nodes
        ticker       -> projected correctly into dag_nodes

    THE `ml_model` / `risk` / `execution` ROWS ARE A THIRD DEFECT AT THIS SITE, not covered by
    1.29's wording. Stage 3's filter excludes five types by name (`:339-340`) on the assumption
    that each has a stage of its own downstream - but `ml_inference`, `risk_validation` and
    `execution` project from `ml_trace`, `risk_trace` and `execution_trace`, which are
    different objects with different fields. A node trace of one of those three types is
    recorded, retained, and then silently dropped from every stage of the response. It is not
    a serialisation failure, so nothing reports it.

    Recorded here rather than acted on: the disposition belongs to task 9.4, which decides what
    the one shared projection covers. What is not in doubt is that a node the engine accepted
    must appear somewhere, because the alternative is an observability surface that omits part
    of what it observed without saying so.
    """
    trace, node = _single_node_trace(node_type)
    payload = trace.to_frontend_format()

    sites = _non_serialisable_sites(payload)
    assert sites == [], (
        f"{node_type.value}: the payload holds raw objects - "
        + ", ".join(f"{path} -> {type_name}" for path, type_name in sites)
    )

    projected = _projected_nodes(payload)
    assert projected == [_stage_three_projection(node)], (
        f"{node_type.value}: the one recorded node projects to {projected!r}, not to the single "
        f"stage-3-shaped dict it should. A node the engine accepted reaches no stage of the "
        f"response."
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. PRESERVATION - stage 3, and the envelope, unchanged
# ══════════════════════════════════════════════════════════════════════════


def test_preserved_stage_three_node_detail_is_unchanged():
    """Byte for byte. Stage 3 is the only stage that works today, and the fix must not touch it.

    Passes on `F` and must keep passing. The comparison is made three ways, because they fail
    for three different reasons: the exact JSON text (any change at all, including key order
    and float formatting), the parsed value (a change in content), and the stage envelope
    (`stage` and `status` beside `nodes`).

    `execution_ms` is `125.0` and both timestamps are fixed constants because
    `DAGNodeTrace.complete()` reads the wall clock - these fixtures never call it, so the
    serialised text is the same on every run and "byte for byte" is a claim a test can make.

    The assertion is deliberately strict. Task 9.4 extracts stage 3's inline expression into a
    named function, which is a refactor with an observable output; this is the observation that
    keeps it one.
    """
    trace = _full_trace()
    payload = trace.to_frontend_format()
    stage = _stage(payload, "dag_nodes")

    expected_nodes = [
        _stage_three_projection(node)
        for node in trace.node_traces
        if node.node_type not in (NodeType.MARKET_DATA, NodeType.INDICATOR, NodeType.ML_MODEL, NodeType.RISK, NodeType.EXECUTION)
    ]
    assert len(expected_nodes) == len(STAGE_THREE_NODES)

    assert stage["nodes"] == expected_nodes, "stage 3's node detail changed"
    assert json.dumps(stage["nodes"], sort_keys=True) == json.dumps(expected_nodes, sort_keys=True), (
        "stage 3's serialised node detail changed"
    )

    # The envelope around it, pinned with the nodes so a fix cannot move the field.
    assert sorted(stage) == ["nodes", "stage", "status"]
    assert stage["stage"] == "dag_nodes"
    # `:341` - `len(self.node_traces) > 2`, counting EVERY node trace and not just this
    # stage's. Pinned as observed rather than as it arguably should be: this file changes no
    # behaviour, and a fix that repaired the count would have to say so.
    assert stage["status"] == "completed"


def test_preserved_the_envelope_and_the_three_non_node_stages_are_unchanged():
    """Everything that already serialises keeps its exact shape and values.

    Passes on `F` and must keep passing. Pinned because the fix edits a literal that contains
    all seven stages, and the two broken ones sit between the working ones.
    """
    payload = _full_trace().to_frontend_format()

    assert {key: payload[key] for key in ("id", "signal_id", "strategy", "symbol", "status", "final_decision", "latency_ms")} == {
        "id": "trc_cluster_d_1",
        "signal_id": "sig_cluster_d_1",
        "strategy": "Cluster D probe",
        "symbol": "BTC/USDT",
        "status": "completed",
        "final_decision": "EXECUTE",
        "latency_ms": 412.5,
    }
    assert payload["timestamp"] == CREATED_AT.isoformat()
    assert payload["errors"] == []

    assert [stage["stage"] for stage in payload["pipeline"]] == [
        "market_data",
        "indicators",
        "dag_nodes",
        "ml_inference",
        "risk_validation",
        "execution",
    ]

    assert _stage(payload, "ml_inference") == {
        "stage": "ml_inference",
        "model": "mdl_1",
        "confidence": 0.81,
        "prediction": "BUY",
        "inference_ms": 12.5,
        "status": "completed",
    }
    assert _stage(payload, "risk_validation") == {
        "stage": "risk_validation",
        "passed": True,
        "blocked": False,
        "block_reason": None,
        "exposure_pct": 18.5,
        "validation_ms": 3.25,
        "status": "completed",
    }
    assert _stage(payload, "execution") == {
        "stage": "execution",
        "order_id": "ord_1",
        "filled_size": "0.5",
        "filled_price": "101.5",
        "fill_percent": 100.0,
        "fees": "0.05",
        "slippage": "0.01",
        "exchange_latency_ms": 41.0,
        "status": "filled",
    }


def test_preserved_a_trace_with_no_nodes_still_formats():
    """An empty trace is a real case - `start_trace` returns one before any node runs.

    Passes on `F`, because with no node traces there is nothing raw to serialise. It is the
    control for section 1: the failure there is caused by the nodes, not by the envelope.
    """
    record = SignalTraceRecord(
        trace_id="trc_empty",
        signal_id="sig_empty",
        strategy_id="stg_cluster_d",
        strategy_name="Cluster D probe",
        bot_id="bot_cluster_d",
        symbol="BTC/USDT",
        exchange="binance",
        created_at=CREATED_AT,
        status=TraceStatus.PENDING,
    )

    payload = record.to_frontend_format()
    assert _non_serialisable_sites(payload) == []
    json.dumps(payload)

    for stage_name in ("market_data", "indicators", "dag_nodes"):
        stage = _stage(payload, stage_name)
        assert stage["nodes"] == []
        assert stage["status"] == "pending"


# ══════════════════════════════════════════════════════════════════════════
# 5. MEASURED - who calls the broken path, and what the API serves instead
#
# These two tests PASS on `F` by design. They are measurements, not expectations: their job is
# to make task 9.4's evidence reproducible rather than leaving it in prose.
# ══════════════════════════════════════════════════════════════════════════


def _python_sources(root):
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def test_measured_the_broken_path_has_one_caller_and_that_caller_has_none():
    """MEASURED - every `.py` file under `backend_app/` searched for both names.

        to_frontend_format        -> defined at signal_trace_engine.py:329
                                     called at  signal_trace_engine.py:817  (1 call site)
        get_traces_for_frontend   -> defined at signal_trace_engine.py:810
                                     called nowhere in backend_app/        (0 call sites)

    The counts are the measurement and they are unchanged from `F`. The line numbers moved by
    +19 in task 9.4, which inserted the named `project_node_trace` above `SignalTraceRecord`;
    on `F` the three sites were `:301`, `:798` and `:791`. Nothing about the caller graph
    changed - the extraction added no call site and removed none.

    So the defect is **latent**: no route, task or subscriber reaches
    `to_frontend_format` today. That does not weaken 1.29 - both methods are public API on a
    public dataclass and a public engine, and the projection is wrong whenever it is called -
    but it does mean the fix is not restoring a currently-broken screen, and the disposition
    should be decided knowing that.

    Recorded rather than argued. This test asserts the counts it measured, so if a caller is
    added later the assertion fails and the note above stops being true silently.
    """
    sources = _python_sources(BACKEND_ROOT)
    assert len(sources) > 50, "the walk must have found the backend for this to mean anything"

    def call_sites(name):
        hits = []
        for path in sources:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if f"{name}(" in line and not line.lstrip().startswith(("def ", "async def ", "#")):
                    hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}")
        return hits

    assert call_sites("to_frontend_format") == [
        "backend_app/backend/signal_trace_engine.py:817",
    ]
    assert call_sites("get_traces_for_frontend") == []


def test_measured_a_second_complete_projection_of_the_same_dataclass_already_exists():
    """MEASURED ON `F` - what the HTTP surface actually serves, and how it differs.

        routers/signal_trace.py  ->  signal_service.load_signal_trace_record
                                 ->  signal_service._dag_nodes_from_record   (:8426)

    `_dag_nodes_from_record` is a second, independent `DAGNodeTrace -> dict` projection. It is
    the more complete of the two: it covers **every** node type (no exclusion list), and it
    carries `cache_hit`, `retry_count`, `started_at`, `ended_at` and a per-port `shape` that
    stage 3 drops. It also reads every field by name through `_pick`/`_text`/`_jsonable`, so a
    `Decimal` port value goes through the same bounded conversion the rest of that module uses.

    Two things follow for task 9.4, and neither is decided here. "Extract the projection into
    one named function" has to reckon with the fact that there are already two implementations
    of it, and the one the API serves is not the one being extracted. And the eight-key shape
    stage 3 publishes is a strict subset of the twelve-key shape the router already returns -
    so `tests/fixtures/signal_trace_node_projection.json` has to declare which of the two is
    the contract before either side can be pinned to it.
    """
    signal_service = (BACKEND_ROOT / "backend" / "signal_service.py").read_text(encoding="utf-8")

    assert "def _dag_nodes_from_record(" in signal_service
    assert "def _node_io(" in signal_service

    # Fields the second projection carries and stage 3 does not.
    for field in ("cache_hit", "retry_count", "started_at", "ended_at"):
        assert field in signal_service, f"{field} is part of the router's projection"
        assert field not in EXPECTED_NODE_KEYS, f"{field} is not part of stage 3's"

    # And it applies no node-type exclusion list: every recorded node is projected.
    projection = signal_service.split("def _dag_nodes_from_record(", 1)[1].split("\ndef ", 1)[0]
    for excluded in ("ML_MODEL", "RISK", "EXECUTION", "MARKET_DATA", "INDICATOR"):
        assert excluded not in projection, (
            f"the router's projection filters on {excluded}, so it is not type-complete after all"
        )

# ══════════════════════════════════════════════════════════════════════════
# 6. THE NAMED PROJECTION AND THE SHARED FIXTURE AGREE  (task 9.4)
#
# These tests PASS on the post-9.4 tree. Task 9.4 extracts stage 3's inline expression into
# `project_node_trace` and declares its output in `tests/fixtures/signal_trace_node_projection.json`
# - the one file both sides read: pytest here, vitest through `node:fs` in task 9.6.
#
# The fixture is a declaration, and a declaration nobody checks goes stale. Section 6 is that
# check: it compares the file against what the function actually returns for a constructed
# `DAGNodeTrace`, so the file cannot drift from the code while both keep passing.
#
# This is the one place in this file that imports the subject. Sections 1-4 deliberately do not -
# `_stage_three_projection` is a transcription, because a test that read its expectation out of the
# code under test would pass whatever that code did. Here the code IS one of the two things being
# compared, and the fixture is the independent statement of the contract.
# ══════════════════════════════════════════════════════════════════════════

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "signal_trace_node_projection.json"

#: The two `NodeIO.value` objects the fixture's worked example uses, written out as Python rather
#: than evaluated from the file. The fixture records them as `"Decimal('101.5')"` and `"True"` for
#: the frontend's benefit; a test must not `eval` a data file to get them back.
_EXAMPLE_PORT_VALUES = {"Decimal('101.5')": Decimal("101.5"), "True": True}

#: `json_type` names in the fixture, mapped to what `isinstance` should see. `bool` is excluded
#: from `number` on purpose: `True` is an `int` in Python and would otherwise satisfy it.
_JSON_TYPES = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _load_fixture():
    """The shared contract file, parsed. Its absence is a failure, not a skip."""
    assert FIXTURE_PATH.exists(), (
        f"{FIXTURE_PATH.relative_to(REPO_ROOT).as_posix()} is missing - it is the single copy of "
        f"this contract, read by pytest here and by vitest in task 9.6"
    )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _node_from_fixture_example(example):
    """The `DAGNodeTrace` the fixture says produced its worked output.

    Built field by field from `example["node_trace"]`, so the fixture - not this function - is
    what decides which node is being projected. Timestamps are the file-level constants and
    `execution_ms` is read from the fixture, because `DAGNodeTrace.complete()` would put the wall
    clock into the result.
    """
    described = example["node_trace"]
    return DAGNodeTrace(
        node_id=described["node_id"],
        node_type=NodeType(described["node_type"]),
        node_label=described["node_label"],
        start_time=NODE_START,
        end_time=NODE_END,
        execution_ms=described["execution_ms"],
        inputs=[
            NodeIO(key=port["key"], value=_EXAMPLE_PORT_VALUES[port["python_value"]], dtype=port["dtype"])
            for port in described["inputs"]
        ],
        outputs=[
            NodeIO(key=port["key"], value=_EXAMPLE_PORT_VALUES[port["python_value"]], dtype=port["dtype"])
            for port in described["outputs"]
        ],
        status=ValidationResult(described["status"]),
        error_message=described["error_message"],
    )


def test_the_projection_exists_as_a_named_module_level_function():
    """Task 9.4's deliverable, asserted as a fact about the module rather than about a response.

    `project_node_trace` is importable from `signal_trace_engine` and callable on a bare
    `DAGNodeTrace` - which is what makes it reusable by the two stages that were written without
    it. An inline expression inside a stage literal satisfies none of this, which is why the
    stages could disagree in the first place.
    """
    from backend_app.backend import signal_trace_engine

    projection = getattr(signal_trace_engine, "project_node_trace", None)
    assert callable(projection), (
        "signal_trace_engine.project_node_trace is missing: the DAGNodeTrace -> dict projection "
        "still has no name, so it cannot be shared between the three stages"
    )

    node = _node(NodeType.OPERATOR, "n_op", "Crossover")
    assert projection(node) == _stage_three_projection(node), (
        "the named projection does not produce stage 3's shape - 9.5's preservation clause "
        "(3.7) requires stage 3's node detail unchanged, byte for byte"
    )


def test_the_shared_fixture_matches_what_the_projection_actually_returns():
    """The fixture cannot go stale: its worked example is recomputed from the function every run.

    Three claims, each failing for its own reason:

      1. `key_order` is the projection's keys, in the projection's own insertion order. Order is
         part of the contract because section 4 compares stage 3's serialised text, and
         `json.dumps` writes keys in insertion order.
      2. The declared `json_type` of every key describes the value actually produced, with
         `nullable` honoured - so `error: null` is legal and `error: 0` is not.
      3. `example.output` equals `project_node_trace(example.node_trace)` exactly.

    Together they mean a change to the projection that nobody reflected in the fixture fails
    here, rather than in task 9.6's vitest run or, later, on a Signal Trace screen.
    """
    fixture = _load_fixture()
    from backend_app.backend.signal_trace_engine import project_node_trace

    node = _node_from_fixture_example(fixture["example"])
    produced = project_node_trace(node)

    # 1. Key order, as declared and as produced.
    assert list(produced) == fixture["key_order"], (
        f"the projection emits {list(produced)}, the fixture declares {fixture['key_order']}"
    )
    assert tuple(fixture["key_order"]) == EXPECTED_NODE_KEYS, (
        "the fixture's key order disagrees with this file's hand-transcribed contract"
    )

    # 2. Declared types describe the produced values.
    for key, declared in fixture["keys"].items():
        value = produced[key]
        if value is None:
            assert declared["nullable"], f"{key} came back null but the fixture declares it non-nullable"
            continue
        assert isinstance(value, _JSON_TYPES[declared["json_type"]]) and not (
            declared["json_type"] == "number" and isinstance(value, bool)
        ), f"{key} is {type(value).__name__}, the fixture declares {declared['json_type']}"

    # Port sub-shape: same two claims, one level down.
    port_contract = fixture["port"]
    for field_name in ("inputs", "outputs"):
        for port in produced[field_name]:
            assert list(port) == port_contract["key_order"], (
                f"{field_name} port emits {list(port)}, the fixture declares "
                f"{port_contract['key_order']}"
            )
            for key, declared in port_contract["keys"].items():
                assert isinstance(port[key], _JSON_TYPES[declared["json_type"]]), (
                    f"{field_name} port {key} is {type(port[key]).__name__}, the fixture declares "
                    f"{declared['json_type']}"
                )

    # 3. The worked example, value for value and then byte for byte.
    assert produced == fixture["example"]["output"], (
        "tests/fixtures/signal_trace_node_projection.json has gone stale: its worked example is "
        "not what project_node_trace returns for the node it describes"
    )
    assert json.dumps(produced) == json.dumps(fixture["example"]["output"]), (
        "the fixture's example serialises to different text than the projection does"
    )

    # And the enums the fixture publishes to the frontend are the enums the backend has.
    assert fixture["keys"]["type"]["enum"] == [member.value for member in NodeType]
    assert fixture["keys"]["status"]["enum"] == [member.value for member in ValidationResult]


def test_the_projection_serialises_every_node_type_and_both_awkward_port_values():
    """`json.dumps(project_node_trace(node))` succeeds for all nine `NodeType` members.

    The function is now the single thing three stages will call, so it is asserted on its own -
    not through a response, where stage 3's exclusion list hides six of the nine. `str(io.value)`
    is the step under test: the fixtures carry a `Decimal` input and a `bool` output, and a
    projection that forwarded either unchanged would serialise the `bool` and raise on the
    `Decimal`.

    Stage coverage for those six types is task 9.5's; that a node of any type CAN be projected
    is this task's.
    """
    from backend_app.backend.signal_trace_engine import project_node_trace

    for node_type in NodeType:
        node = _node(node_type, f"n_{node_type.value}", f"a {node_type.value} node")
        produced = project_node_trace(node)

        assert _non_serialisable_sites(produced) == [], (
            f"{node_type.value}: the projection emitted objects json.dumps cannot write"
        )
        assert json.loads(json.dumps(produced))["type"] == node_type.value
        assert produced["inputs"] == [{"key": "close", "value": "101.5", "dtype": "float"}], (
            f"{node_type.value}: the Decimal input port did not come through as a string"
        )
        assert produced["outputs"] == [{"key": "passed", "value": "True", "dtype": "bool"}], (
            f"{node_type.value}: the bool output port did not come through as a string"
        )

    failed = _node(NodeType.LOGIC, "n_bad", "RSI > 70", status=ValidationResult.FAIL, error="upstream gap")
    assert project_node_trace(failed)["status"] == "fail"
    assert project_node_trace(failed)["error"] == "upstream gap", "error_message maps to `error`"


def test_stage_three_now_routes_through_the_named_projection():
    """Stage 3 calls the function rather than carrying its own copy of the expression.

    Asserted against the source text, because the two are indistinguishable from the outside -
    which is the point of the refactor, and also the reason a behavioural test cannot tell whether
    it happened. If the inline literal came back, the three stages could drift apart again even
    with all of sections 1-5 green.
    """
    source = (BACKEND_ROOT / "backend" / "signal_trace_engine.py").read_text(encoding="utf-8")

    stage_three = source.split('"stage": "dag_nodes"', 1)[1].split('"stage": "ml_inference"', 1)[0]
    assert "project_node_trace(" in stage_three, "the dag_nodes stage no longer calls the projection"
    assert '"node_id": n.node_id' not in stage_three, (
        "the dag_nodes stage still holds an inline copy of the projection"
    )

    # The exclusion list is 9.5's to revisit, and is pinned here as observed so that 9.4 is
    # readable as a refactor with no behaviour change.
    assert "NodeType.MARKET_DATA, NodeType.INDICATOR, NodeType.ML_MODEL, NodeType.RISK, NodeType.EXECUTION" in stage_three
    assert 'if len(self.node_traces) > 2 else "pending"' in stage_three
