"""
tests/test_strategy_dag_validator.py

Unit tests for the validation rule engine,
`backend_app/backend/strategy_dag/validator.py`.

Spec: strategy-builder task 1.9 (`design.md` -> Validation pipeline, Port type system and
connection legality, Cycle detection, Structured error contract, Performance limits).
Requirements 6.2-6.8, 6.10-6.15, 7.4-7.10, 8.1-8.7, 25.4.

What these tests hold in place:

* **Every one of R1-R8 rejects and accepts.** A rule that only ever rejects is a rule
  nobody can satisfy; a rule that only ever accepts is not a rule.
* **`find_cycle` returns the true cycle**, not a visited set, and survives a graph deeper
  than the interpreter's recursion limit - the defect the iterative form exists to remove.
* **Collect-all.** One pass reports every defect, so an author fixes a strategy in one
  round rather than one save per problem.
* **The client is never trusted.** A hand-crafted payload that widens its own port
  contract, mislabels an edge type, mislabels a category or claims `VALID` gets the
  registry's contract and the server's verdict.
* **Forbidden flows are consequences, not special cases.** ACTION->DATA, ACTION->INDICATOR,
  ACTION->ML_DL and ML_DL->DATA are rejected by R5/R6, with no table of forbidden pairs
  anywhere in the validator.

Nothing is faked. The registry is the real assembled one; where a test needs a descriptor
the platform does not currently publish it derives it from the real descriptor set with
`dataclasses.replace`, so the rule is exercised against production data.
"""

import dataclasses

import pytest

from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    Port,
    PortType,
    StrategyGraph,
    ValidationState,
    compute_dag_hash,
)

#: The nine keys of the structured error contract (design.md -> Structured error contract).
CONTRACT_KEYS = {
    "code",
    "severity",
    "node_id",
    "edge_id",
    "field",
    "message",
    "expected",
    "actual",
    "fix_hint",
}


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def restore_default_stage_hooks():
    """Leave the stage hooks as they were found.

    Two tests below exercise the seam by calling `clear_stage_hooks()`, which also
    uninstalls the stage 10b leakage implementation that `validator.py` installs at import.
    The hooks are module-level process state, so without this every module collected after
    this one would validate graphs with leakage detection switched off - a leaky graph would
    read clean and earn a `dag_hash`, and nothing would say so. The two tests still clear
    the hooks themselves and still assert what they assert; this only puts the defaults back
    afterwards.
    """
    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


def make_node(reg: BlockRegistry, block_id: str, **params) -> NodeSpec:
    """A node carrying the descriptor's own category, as the client is required to."""
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
    """The smallest realistic strategy: data -> ema -> gt(vs constant) -> buy.

    Returns ``(graph, nodes_by_role)``.
    """
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


def spare(reg: BlockRegistry, graph: StrategyGraph, block_id: str, **params) -> NodeSpec:
    """Add an unconnected node to ``graph`` so an accepting case has a free input port."""
    node = make_node(reg, block_id, **params)
    graph.nodes.append(node)
    return node


def codes(report: V.ValidationReport):
    return set(report.codes())


# ---------------------------------------------------------------------------
# The happy path, so the rules below are known to be satisfiable
# ---------------------------------------------------------------------------


def test_a_well_formed_strategy_validates(reg):
    graph, _ = linear_strategy(reg)
    report = V.validate(graph, reg, available_bars=5000)

    assert report.valid, report.errors
    assert report.errors == []
    assert report.dag_hash == compute_dag_hash(report.canonical_graph)
    assert report.summary["node_count"] == 5
    assert report.summary["edge_count"] == 4
    assert report.summary["warmup_bars"] == report.warmup_bars > 0
    assert report.execution_order is not None
    assert len(report.execution_order) == 5


def test_execution_order_is_deterministic_and_topological(reg):
    graph, nodes = linear_strategy(reg)
    first = V.validate(graph, reg).execution_order

    shuffled = StrategyGraph(
        nodes=list(reversed(graph.nodes)),
        edges=list(reversed(graph.edges)),
    )
    second = V.validate(shuffled, reg).execution_order

    assert first == second
    order = first or []
    assert order.index(nodes["data"].id) < order.index(nodes["ema"].id)
    assert order.index(nodes["gt"].id) < order.index(nodes["action"].id)


# ---------------------------------------------------------------------------
# R1-R8: one rejecting and one accepting case each
# ---------------------------------------------------------------------------


def test_r1_endpoint_must_resolve(reg):
    graph, nodes = linear_strategy(reg)

    dangling = EdgeSpec.create("n_does_not_exist", "out", nodes["gt"].id, "left")
    issue = V.is_edge_legal(graph, dangling, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_EDGE_ENDPOINT_UNKNOWN
    assert issue["edge_id"] == dangling.id

    free = spare(reg, graph, "gt")
    legal = EdgeSpec.create(nodes["ema"].id, "value", free.id, "left")
    assert V.is_edge_legal(graph, legal, reg) is None


def test_r2_no_self_loop(reg):
    graph, nodes = linear_strategy(reg)
    add = make_node(reg, "add")
    graph.nodes.append(add)

    issue = V.is_edge_legal(graph, EdgeSpec.create(add.id, "out", add.id, "a"), reg)
    assert issue is not None
    assert issue["code"] == V.CODE_SELF_LOOP
    assert issue["node_id"] == add.id

    other = make_node(reg, "add")
    graph.nodes.append(other)
    assert (
        V.is_edge_legal(graph, EdgeSpec.create(add.id, "out", other.id, "a"), reg) is None
    )


def test_r3_ports_must_exist_on_the_descriptor(reg):
    graph, nodes = linear_strategy(reg)

    bad_source = EdgeSpec.create(nodes["ema"].id, "not_a_port", nodes["gt"].id, "right")
    issue = V.is_edge_legal(graph, bad_source, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_UNKNOWN_SOURCE_PORT
    assert "value" in issue["expected"]

    bad_target = EdgeSpec.create(nodes["ema"].id, "value", nodes["gt"].id, "middle")
    issue = V.is_edge_legal(graph, bad_target, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_UNKNOWN_TARGET_PORT
    assert set(issue["expected"]) == {"left", "right"}

    free = spare(reg, graph, "gt")
    good = EdgeSpec.create(nodes["ema"].id, "value", free.id, "right")
    assert V.is_edge_legal(graph, good, reg) is None


def test_r4_type_compatibility_uses_the_published_matrix(reg):
    graph, nodes = linear_strategy(reg)

    # OHLCV_FRAME may feed OHLCV_FRAME or PRICE_SERIES, never SCALAR_SERIES.
    mismatch = EdgeSpec.create(nodes["data"].id, "frame", nodes["gt"].id, "right")
    issue = V.is_edge_legal(graph, mismatch, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_TYPE_MISMATCH
    assert issue["actual"] == PortType.SCALAR_SERIES.value
    # The expectation is read from the registry's matrix, not restated by the validator.
    assert issue["expected"] == sorted(
        target.value
        for target in registry_module.COMPATIBILITY_MATRIX[PortType.OHLCV_FRAME]
    )

    free = spare(reg, graph, "sma", window=30, source="close")
    compatible = EdgeSpec.create(nodes["data"].id, "close", free.id, "series")
    assert V.is_edge_legal(graph, compatible, reg) is None


def test_r5_category_adjacency_comes_from_the_descriptor(reg):
    graph, nodes = linear_strategy(reg)

    # INDICATOR publishes no DATA successor, so this is illegal regardless of ports.
    backwards = EdgeSpec.create(nodes["ema"].id, "value", nodes["data"].id, "frame")
    issue = V.is_edge_legal(graph, backwards, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_ILLEGAL_CATEGORY_FLOW
    assert issue["actual"] == BlockCategory.DATA.value
    assert BlockCategory.DATA.value not in issue["expected"]

    free = spare(reg, graph, "gt")
    forwards = EdgeSpec.create(nodes["ema"].id, "value", free.id, "right")
    assert V.is_edge_legal(graph, forwards, reg) is None


def test_r6_terminal_blocks_have_no_outputs(reg):
    graph, nodes = linear_strategy(reg)

    issue = V.is_edge_legal(
        graph,
        EdgeSpec.create(nodes["action"].id, "out", nodes["ema"].id, "series"),
        reg,
    )
    assert issue is not None
    assert issue["code"] == V.CODE_TERMINAL_HAS_NO_OUTPUT
    assert issue["node_id"] == nodes["action"].id

    # A non-terminal source with the same shape of edge is fine.
    free = spare(reg, graph, "ema", window=9, source="close")
    assert (
        V.is_edge_legal(
            graph,
            EdgeSpec.create(nodes["data"].id, "close", free.id, "series"),
            reg,
        )
        is None
    )


def test_r7_single_arity_input_accepts_one_connection(reg):
    gt_one = make_node(reg, "gt")
    gt_two = make_node(reg, "gt")
    gate = make_node(reg, "and")
    graph = StrategyGraph(
        nodes=[gt_one, gt_two, gate],
        edges=[
            EdgeSpec.create(gt_one.id, "out", gate.id, "b"),
            EdgeSpec.create(gt_one.id, "out", gate.id, "a"),
        ],
    )

    # 'b' is not variadic and is already fed.
    issue = V.is_edge_legal(
        graph, EdgeSpec.create(gt_two.id, "out", gate.id, "b"), reg
    )
    assert issue is not None
    assert issue["code"] == V.CODE_PORT_ALREADY_CONNECTED
    assert issue["expected"] == 1

    # 'a' is variadic: a second source is exactly what it is for (Requirement 7.2).
    assert (
        V.is_edge_legal(graph, EdgeSpec.create(gt_two.id, "out", gate.id, "a"), reg)
        is None
    )


def test_r8_edge_that_would_close_a_loop_is_refused(reg):
    graph, nodes = linear_strategy(reg)
    add = make_node(reg, "add")
    shift = make_node(reg, "shift", bars=1)
    graph.nodes.extend([add, shift])
    graph.edges.extend(
        [
            EdgeSpec.create(nodes["ema"].id, "value", add.id, "a"),
            EdgeSpec.create(add.id, "out", shift.id, "a"),
        ]
    )

    closing = EdgeSpec.create(shift.id, "out", add.id, "b")
    issue = V.is_edge_legal(graph, closing, reg)
    assert issue is not None
    assert issue["code"] == V.CODE_CYCLE
    assert issue["actual"][0] == issue["actual"][-1]
    assert set(issue["actual"]) == {add.id, shift.id}

    # The same edge into a node that is not upstream is legal.
    other = make_node(reg, "add")
    graph.nodes.append(other)
    assert (
        V.is_edge_legal(graph, EdgeSpec.create(shift.id, "out", other.id, "a"), reg)
        is None
    )


# ---------------------------------------------------------------------------
# The forbidden flows are consequences of R5 / R6, not a table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_block, target_port",
    [
        ("ohlcv_feed", "frame"),   # ACTION -> DATA
        ("ema", "series"),         # ACTION -> INDICATOR
        ("xgboost", "features"),   # ACTION -> ML_DL
    ],
)
def test_action_cannot_feed_anything(reg, target_block, target_port):
    act = action_node(reg)
    target = make_node(reg, target_block)
    graph = StrategyGraph(nodes=[act, target], edges=[])

    issue = V.is_edge_legal(
        graph, EdgeSpec.create(act.id, "out", target.id, target_port), reg
    )
    assert issue is not None
    assert issue["code"] in {
        V.CODE_TERMINAL_HAS_NO_OUTPUT,
        V.CODE_ILLEGAL_CATEGORY_FLOW,
    }


def test_ml_cannot_feed_data(reg):
    model = make_node(reg, "xgboost")
    data = data_node(reg)
    graph = StrategyGraph(nodes=[model, data], edges=[])

    issue = V.is_edge_legal(
        graph, EdgeSpec.create(model.id, "prediction", data.id, "frame"), reg
    )
    assert issue is not None
    assert issue["code"] in {
        V.CODE_ILLEGAL_CATEGORY_FLOW,
        V.CODE_TERMINAL_HAS_NO_OUTPUT,
    }


# ---------------------------------------------------------------------------
# find_cycle: the true path, and no recursion limit
# ---------------------------------------------------------------------------


def _chain_edges(ids):
    return [
        EdgeSpec.create(ids[i], "out", ids[i + 1], "a") for i in range(len(ids) - 1)
    ]


def test_find_cycle_returns_the_true_cycle_not_a_visited_set():
    # A long acyclic lead-in, then a three-node cycle, then a dead-end branch. A visited
    # set would name the lead-in nodes too; the real cycle is only the three.
    lead_in = [f"lead_{i}" for i in range(6)]
    cycle_ids = ["c1", "c2", "c3"]
    branch = ["b1", "b2"]
    node_ids = lead_in + cycle_ids + branch

    edges = _chain_edges(lead_in)
    edges.append(EdgeSpec.create(lead_in[-1], "out", cycle_ids[0], "a"))
    edges.extend(_chain_edges(cycle_ids))
    edges.append(EdgeSpec.create(cycle_ids[-1], "out", cycle_ids[0], "a"))
    edges.append(EdgeSpec.create(cycle_ids[0], "out", branch[0], "a"))
    edges.extend(_chain_edges(branch))

    path = V.find_cycle(node_ids, edges)

    assert path, "a cycle exists and must be reported"
    assert path[0] == path[-1], "the reported path must be closed"
    assert set(path) == set(cycle_ids)
    assert not set(path) & set(lead_in + branch)
    # Consecutive ids really are joined by an edge: this is a path, not a set.
    hops = {(edge.source, edge.target) for edge in edges}
    assert all((path[i], path[i + 1]) in hops for i in range(len(path) - 1))


def test_find_cycle_is_empty_for_a_dag():
    node_ids = [f"n{i}" for i in range(20)]
    assert V.find_cycle(node_ids, _chain_edges(node_ids)) == []


def test_find_cycle_reports_a_self_loop():
    path = V.find_cycle(["solo"], [EdgeSpec.create("solo", "out", "solo", "a")])
    assert path == ["solo", "solo"]


def test_find_cycle_survives_a_graph_deeper_than_the_recursion_limit():
    # 5 000 nodes in one chain. A recursive depth-first search raises RecursionError here;
    # the design mandates the iterative form precisely because a 200-node strategy is
    # allowed to be one long chain and the compiler's old `_has_cycle` was recursive.
    node_ids = [f"deep_{i}" for i in range(5000)]
    edges = _chain_edges(node_ids)

    assert V.find_cycle(node_ids, edges) == []       # no RecursionError

    edges.append(EdgeSpec.create(node_ids[-1], "out", node_ids[0], "a"))
    path = V.find_cycle(node_ids, edges)
    assert path[0] == path[-1] == node_ids[0]
    assert len(path) == len(node_ids) + 1


def test_validate_reports_the_cycle_with_the_closing_edge(reg):
    graph, nodes = linear_strategy(reg)
    add = make_node(reg, "add")
    shift = make_node(reg, "shift", bars=1)
    graph.nodes.extend([add, shift])
    closing = EdgeSpec.create(shift.id, "out", add.id, "b")
    graph.edges.extend(
        [
            EdgeSpec.create(nodes["ema"].id, "value", add.id, "a"),
            EdgeSpec.create(add.id, "out", shift.id, "a"),
            closing,
        ]
    )

    report = V.validate(graph, reg)
    cycle_errors = [e for e in report.errors if e["code"] == V.CODE_CYCLE]
    assert len(cycle_errors) == 1
    assert cycle_errors[0]["edge_id"] == closing.id
    assert set(cycle_errors[0]["actual"]) == {add.id, shift.id}
    # No order can be reported for a cyclic graph, and none is invented.
    assert report.execution_order is None
    assert report.dag_hash is None
    warmup_stage = [s for s in report.stages if s["name"] == V.STAGE_WARMUP][0]
    assert warmup_stage["status"] == V.STATUS_SKIPPED


# ---------------------------------------------------------------------------
# Collect-all
# ---------------------------------------------------------------------------


def test_one_pass_reports_every_distinct_defect(reg):
    """Six unrelated defects, one validation, six reports (Requirement 8.1)."""
    unknown = NodeSpec.create("no_such_block", BlockCategory.INDICATOR)
    bad_param = make_node(reg, "ema", window=0)              # min is 2
    orphan = make_node(reg, "add")                            # connected to nothing
    data = data_node(reg)
    gt = make_node(reg, "gt")                                 # 'right' left unfed
    graph = StrategyGraph(
        nodes=[unknown, bad_param, orphan, data, gt],
        edges=[
            EdgeSpec.create(data.id, "close", bad_param.id, "series"),
            # OHLCV_FRAME cannot feed SCALAR_SERIES: a type mismatch.
            EdgeSpec.create(data.id, "frame", gt.id, "left"),
        ],
    )

    report = V.validate(graph, reg)
    found = codes(report)

    assert not report.valid
    assert V.CODE_UNRESOLVED_BLOCK in found          # stage 2
    assert V.CODE_PARAM_OUT_OF_RANGE in found       # stage 3
    assert V.CODE_TYPE_MISMATCH in found            # stage 4
    assert V.CODE_REQUIRED_INPUT_MISSING in found   # stage 5
    assert V.CODE_ORPHAN_NODE in found              # stage 7
    assert V.CODE_MISSING_REQUIRED_CATEGORY in found  # stage 8, no ACTION block

    # Every stage that could run did run: nothing short-circuited the pass.
    ran = {entry["name"] for entry in report.stages}
    assert {
        V.STAGE_STRUCTURAL,
        V.STAGE_REGISTRY,
        V.STAGE_PARAMETERS,
        V.STAGE_PORT_CONTRACTS,
        V.STAGE_REQUIRED_INPUTS,
        V.STAGE_ACYCLICITY,
        V.STAGE_REACHABILITY,
        V.STAGE_EXECUTION_PATH,
        V.STAGE_ACTION_PROVENANCE,
    } <= ran


def test_missing_category_error_names_the_category(reg):
    """Requirement 7.6: the error names the missing Block_Category."""
    gt = make_node(reg, "gt")
    const_a = make_node(reg, "constant", value=1.0)
    const_b = make_node(reg, "constant", value=2.0)
    graph = StrategyGraph(
        nodes=[gt, const_a, const_b],
        edges=[
            EdgeSpec.create(const_a.id, "value", gt.id, "left"),
            EdgeSpec.create(const_b.id, "value", gt.id, "right"),
        ],
    )

    report = V.validate(graph, reg)
    named = {
        issue["expected"]
        for issue in report.errors
        if issue["code"] == V.CODE_MISSING_REQUIRED_CATEGORY
    }
    assert named == {BlockCategory.DATA.value, BlockCategory.ACTION.value}


def test_branching_and_merging_are_accepted(reg):
    """Requirements 7.1-7.4: fan-out, fan-in and two ACTION nodes all validate."""
    data = data_node(reg)
    fast = make_node(reg, "ema", window=10, source="close")
    slow = make_node(reg, "ema", window=50, source="close")
    cross_up = make_node(reg, "cross_above")
    cross_down = make_node(reg, "cross_below")
    buy = action_node(reg)
    sell = make_node(
        reg, "action_sell_market", quantity_type="percent_of_equity", quantity=1.0
    )
    graph = StrategyGraph(
        nodes=[data, fast, slow, cross_up, cross_down, buy, sell],
        edges=[
            EdgeSpec.create(data.id, "close", fast.id, "series"),
            EdgeSpec.create(data.id, "close", slow.id, "series"),   # fan-out
            EdgeSpec.create(fast.id, "value", cross_up.id, "fast"),
            EdgeSpec.create(slow.id, "value", cross_up.id, "slow"),  # fan-in
            EdgeSpec.create(fast.id, "value", cross_down.id, "fast"),
            EdgeSpec.create(slow.id, "value", cross_down.id, "slow"),
            EdgeSpec.create(cross_up.id, "out", buy.id, "signal"),
            EdgeSpec.create(cross_down.id, "out", sell.id, "signal"),
        ],
    )

    report = V.validate(graph, reg)
    assert report.valid, report.errors
    assert report.summary["action_nodes"] == 2


# ---------------------------------------------------------------------------
# The structured error contract
# ---------------------------------------------------------------------------


def test_every_issue_carries_the_full_structured_contract(reg):
    unknown = NodeSpec.create("no_such_block", BlockCategory.INDICATOR)
    bad_param = make_node(reg, "ema", window=0, period=14)   # range + unknown key
    bad_options = data_node(reg)
    bad_options.params["market_type"] = "margin"
    gt = make_node(reg, "gt")
    graph = StrategyGraph(
        nodes=[unknown, bad_param, bad_options, gt],
        edges=[
            EdgeSpec.create(bad_options.id, "close", bad_param.id, "series"),
            EdgeSpec.create(bad_options.id, "frame", gt.id, "left"),
            EdgeSpec.create("n_missing", "out", gt.id, "right"),
        ],
    )

    report = V.validate(graph, reg, available_bars=1)
    assert report.errors, "this graph is meant to be broken"

    for issue in report.issues:
        assert set(issue) == CONTRACT_KEYS, issue
        assert issue["severity"] in {SEVERITY_ERROR, SEVERITY_WARNING}
        assert isinstance(issue["code"], str) and issue["code"]
        assert isinstance(issue["message"], str) and issue["message"].strip()
        assert isinstance(issue["fix_hint"], str) and issue["fix_hint"].strip()
        # Every entry points at something: a node, an edge or a field (Requirement 8.3).
        assert any(
            issue[key] is not None for key in ("node_id", "edge_id", "field")
        ), issue


def test_severity_vocabulary_is_one_vocabulary(reg):
    """`registry` says "ERROR", `schema` says "error"; a report says "error"."""
    assert V.normalise_severity("ERROR") == SEVERITY_ERROR
    assert V.normalise_severity("error") == SEVERITY_ERROR
    assert V.normalise_severity("WARNING") == SEVERITY_WARNING
    assert V.normalise_severity("warn") == SEVERITY_WARNING
    assert V.normalise_severity(None) == SEVERITY_ERROR      # fail closed
    assert registry_module.SEVERITY_ERROR.lower() == SEVERITY_ERROR

    graph, nodes = linear_strategy(reg)
    nodes["ema"].params["window"] = 0
    report = V.validate(graph, reg, available_bars=1)
    assert {issue["severity"] for issue in report.issues} <= {
        SEVERITY_ERROR,
        SEVERITY_WARNING,
    }


def test_only_warnings_still_means_valid(reg):
    """Requirement 8.5: errors block, warnings do not."""
    graph, nodes = linear_strategy(reg)
    nodes["ema"].params["period"] = 14          # undeclared key -> warning

    report = V.validate(graph, reg, available_bars=5000)
    assert report.warnings
    assert report.errors == []
    assert report.valid
    assert report.validation_state is ValidationState.VALID


# ---------------------------------------------------------------------------
# Stage 3: the one declarative parameter checker, classified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params, expected_code, expected_field",
    [
        ({"window": 0}, V.CODE_PARAM_OUT_OF_RANGE, "window"),
        ({"window": 10_000}, V.CODE_PARAM_OUT_OF_RANGE, "window"),
        ({"window": "twenty"}, V.CODE_PARAM_TYPE_INVALID, "window"),
        ({"source": "banana"}, V.CODE_PARAM_NOT_IN_OPTIONS, "source"),
        ({"period": 14}, V.CODE_PARAM_UNKNOWN, "period"),
    ],
)
def test_parameter_problems_are_coded_and_targeted(
    reg, params, expected_code, expected_field
):
    """The classification of `block_specs.validate_block_params` output must not drift."""
    node = make_node(reg, "ema", **params)
    graph = StrategyGraph(nodes=[node], edges=[])

    report = V.validate(graph, reg)
    matching = [issue for issue in report.issues if issue["code"] == expected_code]
    assert matching, report.codes()
    assert matching[0]["field"] == expected_field
    assert matching[0]["node_id"] == node.id
    assert matching[0]["expected"] is not None


def test_required_parameter_with_no_default_blocks_the_save(reg):
    """SB-06's structural fix: `symbol` has no default, so it cannot be silently invented."""
    node = make_node(reg, "ohlcv_feed", timeframe="15m", market_type="spot", mode="streaming")
    report = V.validate(StrategyGraph(nodes=[node], edges=[]), reg)

    missing = [
        issue for issue in report.errors if issue["code"] == V.CODE_PARAM_REQUIRED_MISSING
    ]
    assert [issue["field"] for issue in missing] == ["symbol"]
    assert not report.valid


# ---------------------------------------------------------------------------
# Stage 9: action provenance
# ---------------------------------------------------------------------------


def test_action_fed_by_a_logic_block_is_accepted(reg):
    graph, _ = linear_strategy(reg)
    report = V.validate(graph, reg, available_bars=5000)
    stage = [s for s in report.stages if s["name"] == V.STAGE_ACTION_PROVENANCE][0]
    assert stage["status"] == V.STATUS_PASSED


def test_action_fed_by_a_model_is_rejected_on_provenance(reg):
    """Requirement 7.9.

    No published descriptor lets a model feed an action today, so the case is built by
    widening the real `autoencoder` descriptor's successor set - the edge then passes R4
    and R5 and must still be refused by stage 9, which is the whole point of the stage.
    """
    widened = dataclasses.replace(
        reg["autoencoder"],
        allowed_successor_categories=frozenset(
            set(reg["autoencoder"].allowed_successor_categories) | {BlockCategory.ACTION}
        ),
    )
    permissive = BlockRegistry(
        [widened if d.block_id == "autoencoder" else d for d in reg.blocks()]
    )

    model = make_node(permissive, "autoencoder")
    act = action_node(permissive)
    edge = EdgeSpec.create(model.id, "is_anomaly", act.id, "signal")
    graph = StrategyGraph(nodes=[model, act], edges=[edge])

    # The edge itself is legal under R1-R8 once the descriptor permits the category.
    assert V.is_edge_legal(graph, edge, permissive) is None

    report = V.validate(graph, permissive)
    provenance = [
        issue
        for issue in report.errors
        if issue["code"] == V.CODE_ACTION_INPUT_PROVENANCE
    ]
    assert provenance, report.codes()
    assert provenance[0]["edge_id"] == edge.id
    assert provenance[0]["node_id"] == model.id


def test_one_action_path_may_not_trade_two_symbols(reg):
    """Requirement 7.10: the error names both symbols."""
    btc = data_node(reg, "BTC/USDT")
    eth = data_node(reg, "ETH/USDT")
    fast = make_node(reg, "ema", window=10, source="close")
    slow = make_node(reg, "ema", window=30, source="close")
    gt = make_node(reg, "gt")
    act = action_node(reg)
    graph = StrategyGraph(
        nodes=[btc, eth, fast, slow, gt, act],
        edges=[
            EdgeSpec.create(btc.id, "close", fast.id, "series"),
            EdgeSpec.create(eth.id, "close", slow.id, "series"),
            EdgeSpec.create(fast.id, "value", gt.id, "left"),
            EdgeSpec.create(slow.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", act.id, "signal"),
        ],
    )

    report = V.validate(graph, reg)
    multi = [
        issue
        for issue in report.errors
        if issue["code"] == V.CODE_MULTI_SYMBOL_ACTION_PATH
    ]
    assert multi, report.codes()
    assert multi[0]["actual"] == ["BTC/USDT", "ETH/USDT"]
    assert "BTC/USDT" in multi[0]["message"] and "ETH/USDT" in multi[0]["message"]


# ---------------------------------------------------------------------------
# Stage 10: warmup feasibility is a warning
# ---------------------------------------------------------------------------


def test_warmup_shortfall_is_a_warning_not_an_error(reg):
    """Requirement 8.6: the warning states the required and the available bar count."""
    graph, nodes = linear_strategy(reg)
    nodes["ema"].params["window"] = 200

    report = V.validate(graph, reg, available_bars=180)
    shortfall = [
        issue
        for issue in report.warnings
        if issue["code"] == V.CODE_WARMUP_EXCEEDS_HISTORY
    ]
    assert shortfall, report.codes()
    assert shortfall[0]["severity"] == SEVERITY_WARNING
    assert shortfall[0]["expected"] == report.warmup_bars > 180
    assert shortfall[0]["actual"] == 180
    assert V.CODE_WARMUP_EXCEEDS_HISTORY not in {e["code"] for e in report.errors}
    # A warning does not block: the graph is still valid and still gets a hash.
    assert report.valid
    assert report.dag_hash is not None


def test_warmup_composes_along_the_path(reg):
    """Composition, not max: chained lookbacks add up."""
    data = data_node(reg)
    first = make_node(reg, "ema", window=20, source="close")
    second = make_node(reg, "feat_rolling_mean", window=30)
    graph = StrategyGraph(
        nodes=[data, first, second],
        edges=[
            EdgeSpec.create(data.id, "close", first.id, "series"),
            EdgeSpec.create(first.id, "value", second.id, "series"),
        ],
    )
    # The chain is legal, so the composition is over a shape the validator accepts.
    assert V.is_edge_legal(graph, graph.edges[1], reg) is None

    per_node, total = V.compose_warmup(graph, reg)
    assert per_node[second.id] == per_node[first.id] + reg["feat_rolling_mean"].warmup(
        {"window": 30}
    )
    assert total == per_node[second.id]


def test_validator_warmup_agrees_with_the_plan_composition(reg):
    """Drift guard.

    `plan.compute_warmup` is the canonical composition for a `CompiledPlan`; the validator
    keeps its own read so the stage-10 warning does not require a plan to exist first. The
    two must agree, and `warmup_provider` is the seam a caller uses to pass the plan's
    figure straight through.
    """
    from backend_app.backend.strategy_dag import plan as plan_module

    graph, nodes = linear_strategy(reg)
    nodes["ema"].params["window"] = 120

    assert V.compose_warmup(graph, reg)[1] == plan_module.compute_warmup(graph, reg)
    injected = V.validate(
        graph,
        reg,
        warmup_provider=lambda g, r: plan_module.compute_warmup(g, r),
    )
    assert injected.warmup_bars == V.validate(graph, reg).warmup_bars


def test_warmup_feasibility_is_skipped_rather_than_faked_without_a_bar_count(reg):
    graph, _ = linear_strategy(reg)
    report = V.validate(graph, reg)                     # no available_bars
    stage = [s for s in report.stages if s["name"] == V.STAGE_WARMUP][0]
    assert stage["status"] == V.STATUS_SKIPPED
    assert stage["warmup_bars"] == report.warmup_bars
    assert report.valid


# ---------------------------------------------------------------------------
# Requirement 25.4: the capacity limits
# ---------------------------------------------------------------------------


def test_default_limits_match_the_design_table():
    assert V.LIMITS.max_nodes == 200
    assert V.LIMITS.max_edges == 400
    assert V.LIMITS.max_ml_nodes == 4
    assert V.LIMITS.max_feature_nodes == 40
    assert V.LIMITS.max_feature_columns == 200
    assert V.LIMITS.max_fan_in_per_variadic_port == 16


def test_node_and_edge_limits_name_the_limit_and_its_value(reg):
    graph, _ = linear_strategy(reg)
    limits = V.GraphLimits(max_nodes=3, max_edges=2)

    report = V.validate(graph, reg, limits=limits)
    by_code = {issue["code"]: issue for issue in report.errors}

    node_issue = by_code[V.CODE_NODE_LIMIT_EXCEEDED]
    assert node_issue["field"] == "max_nodes"
    assert node_issue["expected"] == 3
    assert node_issue["actual"] == 5
    assert V.LIMIT_LABELS["max_nodes"] in node_issue["message"]
    assert "3" in node_issue["message"]

    edge_issue = by_code[V.CODE_EDGE_LIMIT_EXCEEDED]
    assert edge_issue["field"] == "max_edges"
    assert edge_issue["expected"] == 2
    assert edge_issue["actual"] == 4
    assert V.LIMIT_LABELS["max_edges"] in edge_issue["message"]


def test_ml_and_feature_node_limits_name_the_limit(reg):
    models = [make_node(reg, "xgboost") for _ in range(2)]
    features = [make_node(reg, "feat_rolling_mean", window=20) for _ in range(2)]
    graph = StrategyGraph(nodes=models + features, edges=[])
    limits = V.GraphLimits(max_ml_nodes=1, max_feature_nodes=1)

    report = V.validate(graph, reg, limits=limits)
    by_code = {issue["code"]: issue for issue in report.errors}

    assert by_code[V.CODE_ML_NODE_LIMIT_EXCEEDED]["expected"] == 1
    assert by_code[V.CODE_ML_NODE_LIMIT_EXCEEDED]["actual"] == 2
    assert V.LIMIT_LABELS["max_ml_nodes"] in by_code[V.CODE_ML_NODE_LIMIT_EXCEEDED]["message"]

    fe = by_code[V.CODE_FEATURE_NODE_LIMIT_EXCEEDED]
    assert fe["expected"] == 1 and fe["actual"] == 2
    assert V.LIMIT_LABELS["max_feature_nodes"] in fe["message"]


def test_feature_column_limit_is_checked_when_the_count_is_supplied(reg):
    graph, _ = linear_strategy(reg)

    clean = V.validate(graph, reg, feature_columns=10)
    assert V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED not in codes(clean)

    report = V.validate(graph, reg, feature_columns=201)
    issue = [
        i for i in report.errors if i["code"] == V.CODE_FEATURE_COLUMN_LIMIT_EXCEEDED
    ][0]
    assert issue["field"] == "max_feature_columns"
    assert issue["expected"] == 200
    assert issue["actual"] == 201
    assert V.LIMIT_LABELS["max_feature_columns"] in issue["message"]


def test_variadic_fan_in_limit_names_the_port(reg):
    gate = make_node(reg, "and")
    sources = [make_node(reg, "gt") for _ in range(4)]
    graph = StrategyGraph(
        nodes=[gate] + sources,
        edges=[EdgeSpec.create(src.id, "out", gate.id, "a") for src in sources],
    )
    limits = V.GraphLimits(max_fan_in_per_variadic_port=3)

    report = V.validate(graph, reg, limits=limits)
    issue = [
        i for i in report.errors if i["code"] == V.CODE_PORT_FAN_IN_LIMIT_EXCEEDED
    ][0]
    assert issue["field"] == "max_fan_in_per_variadic_port"
    assert issue["expected"] == 3
    assert issue["actual"] == 4
    assert issue["node_id"] == gate.id
    assert f"{gate.id}.a" in issue["message"]


# ---------------------------------------------------------------------------
# Requirement 6.13: nothing the client sends is trusted
# ---------------------------------------------------------------------------


def test_server_recomputes_ports_categories_edge_types_state_and_hash(reg):
    graph, nodes = linear_strategy(reg)

    # A hand-crafted payload: the action block claims an output port it does not have and
    # an extra input, the edge claims the wrong type, the node claims the wrong category,
    # and the envelope claims it has already been validated.
    action = nodes["action"]
    action.outputs = [Port(name="out", type=PortType.SIGNAL)]
    action.inputs = [
        Port(name="signal", type=PortType.SIGNAL, required=True),
        Port(name="anything", type=PortType.OHLCV_FRAME),
    ]
    action.category = BlockCategory.LOGIC
    graph.edges[0].type = PortType.TRADE_INTENT
    graph.validation_state = ValidationState.VALID
    submitted_hash = compute_dag_hash(graph)

    report = V.validate(graph, reg, available_bars=5000)
    canonical = report.canonical_graph
    recomputed_action = canonical.node(action.id)
    descriptor = reg["action_buy_market"]

    # Ports come from the descriptor, so the widened contract is discarded.
    assert [p.to_dict() for p in recomputed_action.inputs] == [
        p.to_dict() for p in descriptor.inputs
    ]
    assert recomputed_action.outputs == []
    assert recomputed_action.category is BlockCategory.ACTION
    # Edge type comes from the source port.
    assert canonical.edges[0].type is PortType.PRICE_SERIES
    # The verdict and the hash are the server's.
    assert canonical.validation_state is ValidationState.VALID
    assert report.dag_hash == compute_dag_hash(canonical)
    assert report.dag_hash != submitted_hash

    # Each override is reported rather than applied silently.
    warned = {issue["code"] for issue in report.warnings}
    assert V.CODE_PORT_CONTRACT_RECOMPUTED in warned
    assert V.CODE_EDGE_TYPE_RECOMPUTED in warned
    assert "CATEGORY_REMAPPED" in warned

    # The submitted graph is not mutated: validation is a read.
    assert graph.node(action.id).category is BlockCategory.LOGIC
    assert graph.edges[0].type is PortType.TRADE_INTENT


def test_a_tampered_port_list_cannot_widen_the_contract(reg):
    """The security property: rules read the descriptor, never the node's own ports."""
    graph, nodes = linear_strategy(reg)
    action = nodes["action"]
    action.outputs = [Port(name="out", type=PortType.SIGNAL)]
    ema = nodes["ema"]
    ema.inputs = list(ema.inputs) + [Port(name="series", type=PortType.TRADE_INTENT)]

    smuggled = EdgeSpec.create(action.id, "out", ema.id, "series")
    graph.edges.append(smuggled)

    # Connect-time and validate-time agree, and both consult the registry.
    assert V.is_edge_legal(graph, smuggled, reg)["code"] == V.CODE_TERMINAL_HAS_NO_OUTPUT

    report = V.validate(graph, reg)
    assert not report.valid
    assert report.dag_hash is None
    assert V.CODE_TERMINAL_HAS_NO_OUTPUT in codes(report)


def test_client_claim_of_validity_is_contradicted(reg):
    """A client that says VALID about a broken graph is told otherwise."""
    unknown = NodeSpec.create("no_such_block", BlockCategory.INDICATOR)
    graph = StrategyGraph(nodes=[unknown], edges=[], validation_state=ValidationState.VALID)

    report = V.validate(graph, reg)
    assert not report.valid
    assert report.canonical_graph.validation_state is ValidationState.INVALID
    assert V.CODE_VALIDATION_STATE_RECOMPUTED in codes(report)


# ---------------------------------------------------------------------------
# The two seams: named, not stubbed
# ---------------------------------------------------------------------------


def test_leakage_and_ml_readiness_are_reported_as_not_implemented(reg):
    V.clear_stage_hooks()
    graph, _ = linear_strategy(reg)

    report = V.validate(graph, reg, available_bars=5000)
    pending = report.pending_stages

    assert V.STAGE_LEAKAGE in pending
    assert V.STAGE_ML_READINESS in pending
    # Not implemented is neither pass nor fail: the report says so rather than reading clean.
    for entry in report.stages:
        if entry["name"] in (V.STAGE_LEAKAGE, V.STAGE_ML_READINESS):
            assert entry["status"] == V.STATUS_NOT_IMPLEMENTED
            assert entry["detail"].strip()
    assert V.pending_stages() == (V.STAGE_LEAKAGE, V.STAGE_ML_READINESS)


def test_an_installed_seam_contributes_its_issues(reg):
    """The seam is a real extension point: an installed stage's errors block the graph."""
    from backend_app.backend.strategy_dag.schema import make_issue

    seen = {}

    def leakage_stage(context):
        seen["ml_nodes"] = context.nodes_in_category(BlockCategory.ML_DL)
        seen["warmup"] = context.warmup_bars
        return [
            make_issue(
                "LOOKAHEAD_SHIFT",
                SEVERITY_ERROR,
                "Negative shift reads future bars.",
                node_id=context.execution_order[0],
                fix_hint="Use a positive shift.",
            )
        ]

    graph, _ = linear_strategy(reg)
    try:
        V.register_leakage_stage(leakage_stage)
        report = V.validate(graph, reg, available_bars=5000)
    finally:
        V.clear_stage_hooks()

    assert "LOOKAHEAD_SHIFT" in codes(report)
    assert not report.valid
    assert V.STAGE_LEAKAGE not in report.pending_stages
    assert seen["warmup"] == report.warmup_bars
    assert seen["ml_nodes"] == ()

    # And it is gone again once uninstalled, rather than lingering as a half-state.
    assert V.STAGE_LEAKAGE in V.validate(graph, reg).pending_stages
