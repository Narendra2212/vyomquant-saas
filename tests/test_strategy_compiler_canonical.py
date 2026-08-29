"""
tests/test_strategy_compiler_canonical.py

Unit tests for the canonical compile path in
`backend_app/backend/strategy_compiler.py`.

Spec: strategy-builder task 2.1 (`design.md` -> The single compiler / Compile algorithm).
Requirements 2.3, 2.5, 3.1, 3.3, 3.4, 3.5, 3.8, 7.1, 7.2, 7.3.

What these tests hold in place:

* **One compiler, one verdict (SB-01).** A valid canonical graph compiles to a
  `CompiledPlan`; an invalid one raises `ValidationError` carrying the real structured
  report and produces no plan at all.
* **The rules that only existed in the router copy really run here now.** An orphan node,
  an ACTION fed straight from an INDICATOR, and an unknown node type are each rejected on
  this path.
* **Determinism (Requirement 2.3).** Compiling the same graph twice, and compiling it with
  the node and edge lists shuffled, yields an identical `dag_hash` AND an identical
  `execution_order`. `execution_order` is what the runtime replays and what a backtest has
  to reproduce, so a set-iteration-order dependency here is a silent reproducibility bug.
* **The topological contract (Requirements 3.3, 3.4).** Every node exactly once, every
  edge's source before its target, every node's predecessors in a strictly earlier level.
* **No recursion on depth.** A 5 000-node chain is cycle-checked without a `RecursionError`,
  which is the defect the recursive `_has_cycle` carried.

Nothing is faked. The registry is the real assembled one and the graphs are built from
blocks the platform actually publishes.
"""

import random
import sys

import pytest

from backend_app.backend.strategy_compiler import (
    CompilerError,
    StrategyCompiler,
    ValidationError,
    compile_graph,
    get_compiler,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.plan import COMPILER_VERSION, CompiledPlan
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
    compute_dag_hash,
)


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def compiler() -> StrategyCompiler:
    return StrategyCompiler()


def make_node(reg: BlockRegistry, block_id: str, **params) -> NodeSpec:
    descriptor = reg[block_id]
    return NodeSpec.create(block_id, descriptor.category, params=params)


def data_node(reg: BlockRegistry, symbol: str = "BTC/USDT") -> NodeSpec:
    return make_node(
        reg,
        "ohlcv_feed",
        symbol=symbol,
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )


def action_node(reg: BlockRegistry) -> NodeSpec:
    return make_node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )


def linear_strategy(reg: BlockRegistry):
    """data -> ema -> gt(vs constant) -> buy. Returns ``(graph, nodes_by_role)``."""
    data = data_node(reg)
    ema = make_node(reg, "ema", window=20, source="close")
    const = make_node(reg, "constant", value=30.0)
    gt = make_node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[data, ema, const, gt, act],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )
    return graph, {"data": data, "ema": ema, "const": const, "gt": gt, "action": act}


def branching_strategy(reg: BlockRegistry):
    """One data feed fanning into two indicators, merging into one decision, two ACTIONs.

    Requirements 7.1 (fan-out), 7.2 (fan-in) and 7.3 (more than one ACTION node) in one
    graph, so the compiler is exercised on a real topology rather than a chain.
    """
    data = data_node(reg)
    fast = make_node(reg, "ema", window=12, source="close")
    slow = make_node(reg, "ema", window=26, source="close")
    cross = make_node(reg, "gt")
    buy = action_node(reg)
    sell = make_node(
        reg, "action_sell_market", quantity_type="percent_of_equity", quantity=0.25
    )
    graph = StrategyGraph(
        nodes=[data, fast, slow, cross, buy, sell],
        edges=[
            EdgeSpec.create(data.id, "close", fast.id, "series"),
            EdgeSpec.create(data.id, "close", slow.id, "series"),
            EdgeSpec.create(fast.id, "value", cross.id, "left"),
            EdgeSpec.create(slow.id, "value", cross.id, "right"),
            EdgeSpec.create(cross.id, "out", buy.id, "signal"),
            EdgeSpec.create(cross.id, "out", sell.id, "signal"),
        ],
    )
    return graph, {"data": data, "cross": cross, "buy": buy, "sell": sell}


def shuffled(graph: StrategyGraph, seed: int = 7) -> StrategyGraph:
    """The same graph with its node and edge lists in a different order."""
    rng = random.Random(seed)
    nodes = list(graph.nodes)
    edges = list(graph.edges)
    rng.shuffle(nodes)
    rng.shuffle(edges)
    return StrategyGraph(
        schema_version=graph.schema_version,
        strategy_id=graph.strategy_id,
        version=graph.version,
        name=graph.name,
        nodes=nodes,
        edges=edges,
        metadata=dict(graph.metadata),
        validation_state=graph.validation_state,
    )


def assert_topological(plan: CompiledPlan, graph: StrategyGraph) -> None:
    """Requirement 3.3: every node once, every edge's source before its target."""
    node_ids = {node.id for node in graph.nodes}
    assert len(plan.execution_order) == len(node_ids)
    assert set(plan.execution_order) == node_ids
    position = {node_id: i for i, node_id in enumerate(plan.execution_order)}
    for edge in graph.edges:
        assert position[edge.source] < position[edge.target], (
            f"{edge.source} must be evaluated before {edge.target}"
        )


def assert_levels(plan: CompiledPlan, graph: StrategyGraph) -> None:
    """Requirement 3.4: every node's predecessors sit in a strictly earlier level."""
    depth = {}
    for index, level in enumerate(plan.execution_levels):
        for node_id in level:
            assert node_id not in depth, f"{node_id} appears in two levels"
            depth[node_id] = index
    assert set(depth) == {node.id for node in graph.nodes}
    for edge in graph.edges:
        assert depth[edge.source] < depth[edge.target], (
            f"{edge.source} must be in an earlier level than {edge.target}"
        )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_valid_graph_compiles_to_a_plan(reg, compiler):
    graph, nodes = linear_strategy(reg)

    plan = compiler.compile_plan(graph, reg)

    assert isinstance(plan, CompiledPlan)
    # Requirement 2.4: dag_hash is a readable FIELD, and it is the graph's identity.
    assert plan.dag_hash == compute_dag_hash(graph)
    assert plan.compiler_version == COMPILER_VERSION
    assert plan.schema_version == graph.schema_version
    assert plan.data_nodes == (nodes["data"].id,)
    assert plan.action_nodes == (nodes["action"].id,)
    # Requirement 3.8: warmup composes along the action path, so it is not zero.
    assert plan.warmup_bars > 0
    assert_topological(plan, graph)
    assert_levels(plan, graph)


def test_plan_serializes_through_one_path_and_round_trips(reg, compiler):
    """Requirement 2.5: exactly one serialization path, and it is lossless enough to reload."""
    graph, _ = linear_strategy(reg)
    plan = compiler.compile_plan(graph, reg)

    payload = plan.to_dict()
    assert payload["dag_hash"] == plan.dag_hash
    assert payload["execution_order"] == list(plan.execution_order)

    reloaded = CompiledPlan.from_dict(payload)
    assert reloaded.dag_hash == plan.dag_hash
    assert reloaded.execution_order == plan.execution_order
    assert reloaded.execution_levels == plan.execution_levels
    assert reloaded.matches_graph(graph)


def test_branching_and_merging_topologies_compile(reg, compiler):
    """Requirements 7.1, 7.2, 7.3: fan-out, fan-in and more than one ACTION node."""
    graph, nodes = branching_strategy(reg)

    plan = compiler.compile_plan(graph, reg)

    assert len(plan.action_nodes) == 2
    assert set(plan.action_nodes) == {nodes["buy"].id, nodes["sell"].id}
    # Fan-in: the comparison has two distinct upstream nodes.
    assert len(plan.predecessors(nodes["cross"].id)) == 2
    assert_topological(plan, graph)
    assert_levels(plan, graph)


def test_compile_dispatches_a_canonical_graph_to_the_plan_path(reg, compiler):
    """Requirement 3.1: one entry point. ``compile`` routes a canonical graph itself."""
    graph, _ = linear_strategy(reg)

    from_compile = compiler.compile(graph, registry=reg)
    from_wire = compiler.compile(graph.to_dict(), registry=reg)
    from_helper = compile_graph(graph, reg)

    assert isinstance(from_compile, CompiledPlan)
    assert isinstance(from_wire, CompiledPlan)
    assert from_compile.dag_hash == from_wire.dag_hash == from_helper.dag_hash
    assert from_compile.execution_order == from_wire.execution_order


def test_an_already_computed_report_is_reused_not_recomputed(reg, compiler):
    """The validate endpoint validates once; compiling must not run the pipeline again."""
    graph, _ = linear_strategy(reg)
    report = V.validate(graph, reg)

    plan = compiler.compile_plan(graph, reg, report=report)

    assert plan.dag_hash == report.dag_hash
    assert list(plan.execution_order) == report.execution_order


# ---------------------------------------------------------------------------
# Determinism (Requirement 2.3)
# ---------------------------------------------------------------------------


def test_compiling_twice_yields_an_identical_hash_and_order(reg, compiler):
    graph, _ = linear_strategy(reg)

    first = compiler.compile_plan(graph, reg)
    second = compiler.compile_plan(graph, reg)

    assert first.dag_hash == second.dag_hash
    assert first.execution_order == second.execution_order
    assert first.execution_levels == second.execution_levels
    assert first.to_json() == second.to_json()


def test_shuffling_the_node_and_edge_lists_changes_nothing(reg, compiler):
    """Presentation order is not identity, and it is not execution order either."""
    graph, _ = branching_strategy(reg)

    baseline = compiler.compile_plan(graph, reg)
    for seed in (1, 2, 3, 11, 101):
        rearranged = shuffled(graph, seed)
        plan = compiler.compile_plan(rearranged, reg)
        assert plan.dag_hash == baseline.dag_hash, f"hash drifted for seed {seed}"
        assert plan.execution_order == baseline.execution_order, (
            f"execution_order drifted for seed {seed}"
        )
        assert plan.execution_levels == baseline.execution_levels


def test_every_level_is_sorted_so_the_order_is_byte_identical_across_processes(reg, compiler):
    """The ready set is sorted at every Kahn step; that is the whole determinism argument."""
    graph, _ = branching_strategy(reg)
    plan = compiler.compile_plan(graph, reg)

    for level in plan.execution_levels:
        assert list(level) == sorted(level)
    # The order is the concatenation of the levels, so it is fully determined by them.
    flattened = [node_id for level in plan.execution_levels for node_id in level]
    assert flattened == list(plan.execution_order)


# ---------------------------------------------------------------------------
# All-or-nothing: an invalid graph yields no plan (Requirement 3.5)
# ---------------------------------------------------------------------------


def test_an_invalid_graph_raises_with_the_real_report_and_no_plan(reg, compiler):
    graph, _ = linear_strategy(reg)
    graph.nodes.append(make_node(reg, "ema", window=0))    # min window is 2

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    error = raised.value
    assert error.report is not None, "the report must be carried, not flattened to a string"
    assert error.report.valid is False
    assert error.errors, "the structured error list must be populated"
    assert V.CODE_PARAM_OUT_OF_RANGE in error.codes()
    # Every error carries the structured contract's targeting fields.
    first = error.errors[0]
    assert {"code", "severity", "message", "fix_hint"} <= set(first)
    # No plan escaped, and the report says so.
    assert error.report.dag_hash is None
    assert error.to_dict()["valid"] is False
    assert str(error), "the exception still has a human-readable message"


def test_an_orphan_node_is_rejected(reg, compiler):
    """Absorbed from the router copy: validator stage 7, reached through ``validate()``."""
    graph, _ = linear_strategy(reg)
    orphan = make_node(reg, "constant", value=1.0)          # wired to nothing at all
    graph.nodes.append(orphan)

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    codes = raised.value.codes()
    assert V.CODE_ORPHAN_NODE in codes, codes
    orphan_errors = [
        e for e in raised.value.errors if e["code"] == V.CODE_ORPHAN_NODE
    ]
    assert orphan_errors[0]["node_id"] == orphan.id


def test_an_action_fed_straight_from_an_indicator_is_rejected(reg, compiler):
    """Absorbed from the router copy: an order must be triggered by a decision."""
    graph, nodes = linear_strategy(reg)
    # Bypass the comparison: wire the EMA directly into the buy block's signal port.
    graph.edges = [
        edge for edge in graph.edges if edge.target != nodes["action"].id
    ]
    graph.edges.append(
        EdgeSpec.create(nodes["ema"].id, "value", nodes["action"].id, "signal")
    )

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    codes = set(raised.value.codes())
    # The provenance rule is the one that names the defect; the port type system rejects
    # the same edge independently, which is why both codes are acceptable evidence.
    assert codes & {
        V.CODE_ACTION_INPUT_PROVENANCE,
        V.CODE_TYPE_MISMATCH,
        V.CODE_ILLEGAL_CATEGORY_FLOW,
    }, codes


def test_an_unknown_node_type_is_rejected(reg, compiler):
    """Absorbed from the router copy: an unresolvable block is refused, never guessed at."""
    graph, _ = linear_strategy(reg)
    unknown = NodeSpec.create("no_such_block_anywhere", BlockCategory.INDICATOR)
    graph.nodes.append(unknown)

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    codes = raised.value.codes()
    assert V.CODE_UNRESOLVED_BLOCK in codes, codes


def test_a_graph_with_no_action_block_is_rejected(reg, compiler):
    """Requirement 7.6: the error names the missing category, and no plan is emitted."""
    graph, nodes = linear_strategy(reg)
    graph.nodes = [node for node in graph.nodes if node.id != nodes["action"].id]
    graph.edges = [
        edge for edge in graph.edges if edge.target != nodes["action"].id
    ]

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    assert V.CODE_MISSING_REQUIRED_CATEGORY in raised.value.codes()


def test_a_payload_that_is_not_a_canonical_graph_is_refused(compiler):
    """A caller contract breach is a CompilerError, not an empty graph that "validates"."""
    with pytest.raises(CompilerError):
        compiler.compile_plan({"schema_version": 2, "nodes": "not-a-list"})
    with pytest.raises(CompilerError):
        compiler.compile()


# ---------------------------------------------------------------------------
# Cycles, iteratively (no RecursionError)
# ---------------------------------------------------------------------------


def test_a_cyclic_graph_raises_rather_than_emitting_a_plan(reg, compiler):
    graph, nodes = linear_strategy(reg)
    add = make_node(reg, "add")
    shift = make_node(reg, "shift", bars=1)
    graph.nodes.extend([add, shift])
    graph.edges.extend(
        [
            EdgeSpec.create(nodes["ema"].id, "value", add.id, "a"),
            EdgeSpec.create(add.id, "out", shift.id, "a"),
            EdgeSpec.create(shift.id, "out", add.id, "b"),    # closes the loop
        ]
    )

    with pytest.raises(ValidationError) as raised:
        compiler.compile_plan(graph, reg)

    assert V.CODE_CYCLE in raised.value.codes()
    assert raised.value.report.execution_order is None


def test_cycle_detection_survives_a_chain_deeper_than_the_recursion_limit(compiler):
    """The recursive ``_has_cycle`` raised ``RecursionError`` here; the iterative one does not."""
    depth = 5000
    assert depth > sys.getrecursionlimit()
    nodes = [{"id": f"deep_{i}", "type": "math"} for i in range(depth)]
    edges = [
        {"id": f"e_{i}", "source": f"deep_{i}", "target": f"deep_{i + 1}"}
        for i in range(depth - 1)
    ]

    assert compiler._has_cycle(nodes, edges) is False        # no RecursionError

    edges.append({"id": "closing", "source": f"deep_{depth - 1}", "target": "deep_0"})
    path = compiler._find_cycle(nodes, edges)
    assert path and path[0] == path[-1] == "deep_0"
    assert len(path) == depth + 1


def test_the_legacy_execution_order_is_deterministic_too(compiler):
    """The legacy path shares the sorted-ready-set tie-break, so it cannot drift either."""
    nodes = [{"id": f"n{i}", "type": "math"} for i in range(8)]
    edges = [
        {"source": "n0", "target": "n2"},
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n2", "target": "n4"},
        {"source": "n3", "target": "n5"},
        {"source": "n4", "target": "n5"},
    ]

    baseline = compiler._generate_execution_order(nodes, edges)
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        shuffled_nodes = list(nodes)
        shuffled_edges = list(edges)
        rng.shuffle(shuffled_nodes)
        rng.shuffle(shuffled_edges)
        assert compiler._generate_execution_order(shuffled_nodes, shuffled_edges) == baseline

    position = {node_id: i for i, node_id in enumerate(baseline)}
    for edge in edges:
        assert position[edge["source"]] < position[edge["target"]]


# ---------------------------------------------------------------------------
# The two meanings of "dependencies", kept apart
# ---------------------------------------------------------------------------


def test_plan_dependencies_is_the_predecessor_map(reg, compiler):
    graph, nodes = linear_strategy(reg)
    plan = compiler.compile_plan(graph, reg)

    assert plan.dependencies[nodes["data"].id] == ()
    assert plan.dependencies[nodes["ema"].id] == (nodes["data"].id,)
    assert set(plan.dependencies[nodes["gt"].id]) == {nodes["ema"].id, nodes["const"].id}
    # Every key is a node id and every value holds node ids only: never a block name.
    node_ids = {node.id for node in graph.nodes}
    assert set(plan.dependencies) == node_ids
    for sources in plan.dependencies.values():
        assert set(sources) <= node_ids


def test_resource_dependencies_is_the_bucket_map_under_its_own_name(reg, compiler):
    graph, _ = linear_strategy(reg)

    buckets = compiler.extract_resource_dependencies(graph)

    assert set(buckets) == {"indicators", "ml_models", "data_sources"}
    assert buckets["indicators"] == ["ema"]
    assert buckets["data_sources"] == ["BTC/USDT"]
    assert buckets["ml_models"] == []
    # Sorted, therefore deterministic across processes.
    for values in buckets.values():
        assert values == sorted(values)


# ---------------------------------------------------------------------------
# Backward compatibility for the callers that have not been re-pointed yet
# ---------------------------------------------------------------------------


def test_the_legacy_dag_config_call_shape_still_returns_a_strategy_package():
    """`POST /strategy-operations/strategies/compile` calls exactly this, by keyword."""
    from backend_app.core.models.pydantic_models import DAGConfig

    dag_config = DAGConfig(
        nodes=[
            {"id": "in1", "type": "input", "symbol": "BTC/USDT", "timeframe": "15m"},
            {"id": "ind1", "type": "indicator", "indicator": "rsi", "params": {"period": 14}},
            {"id": "log1", "type": "logic", "operator": "gt"},
            {"id": "act1", "type": "action", "action": "buy"},
        ],
        edges=[
            {"source": "in1", "target": "ind1"},
            {"source": "ind1", "target": "log1"},
            {"source": "log1", "target": "act1"},
        ],
        symbols=["BTC/USDT"],
        timeframe="15m",
    )

    package = get_compiler().compile(
        dag_config=dag_config, strategy_id="s-1", version="v1.0"
    )

    assert package.strategy_id == "s-1"
    assert package.execution_graph.execution_order == ["in1", "ind1", "log1", "act1"]
    # The legacy field keeps its bucket meaning, now readable under an unambiguous name.
    assert package.dependencies == package.resource_dependencies
    assert package.resource_dependencies["indicators"] == ["rsi"]
    assert package.resource_dependencies["data_sources"] == ["BTC/USDT"]


def test_a_legacy_validation_failure_still_raises_a_plain_string_error():
    """`str(e)` is what the existing 422 handler formats, and it must keep working."""
    from backend_app.core.models.pydantic_models import DAGConfig

    dag_config = DAGConfig(
        nodes=[{"id": "in1", "type": "input", "symbol": "BTC/USDT", "timeframe": "15m"}],
        edges=[],
        symbols=["BTC/USDT"],
        timeframe="15m",
    )

    with pytest.raises(ValidationError) as raised:
        get_compiler().compile(dag_config=dag_config, strategy_id="s-2", version="v1.0")

    assert str(raised.value)
    assert raised.value.report is None      # never a half-populated report
    assert raised.value.errors == []
