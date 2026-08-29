# -*- coding: utf-8 -*-
"""
tests/test_compiled_plan.py

Unit tests for `backend_app/backend/strategy_dag/plan.py`.

Spec: strategy-builder task 1.10, corrected by task 5.0 (`design.md` -> Canonical DAG model
/ The single compiler / Warmup computation). Requirements 2.4, 2.5, 3.3, 3.4, 3.8, 12.5.

Four defects this file exists to keep fixed:

* **SB-02.** The clone path called `compiled.get("dag_hash")` on an *object* that only had
  `compute_hash()`. The `AttributeError` was swallowed by a broad `except Exception`, so
  every clone was persisted with no hash while the in-code comment claimed the opposite.
  `TestDagHashIsAField` asserts `dag_hash` is a plain readable attribute, that it is present
  in `to_dict()`, and that a plan without one cannot be constructed at all.

* **Warmup composition (Requirement 3.8).** An EMA(200) feeding a rolling-std(20) feeding a
  lag(3) needs 223 bars, not 200. `TestWarmupComposition` asserts the composed answer is
  strictly greater than the bare `max()` of the individual warmups - the assertion that
  distinguishes a correct implementation from the one that ships NaN-contaminated features
  into a model.

* **SB-06 (Requirement 12.5).** An ACTION descriptor carries no traded-asset parameter, so
  the market an order belongs to is resolved from the DATA node in that action's upstream
  closure. `TestActionMarketResolution` holds `resolve_action_markets` to its stated
  postcondition - one non-empty symbol for *every* ACTION node or a raise, never a
  default - and pins `action_timeframes` as deliberately partial.
  `TestActionSymbolsAreNotPartOfIdentity` proves the map stays out of `dag_hash`, and
  `TestPlanWrittenByCompiler200` proves a plan persisted by compiler 2.0.0 still loads.

* **Variadic fan-in (task 5.0, Requirements 3.3, 3.4).** `inbound` was typed
  `node_id -> target_port -> ONE EdgeSpec` and built with last-write-wins, so a variadic port
  fed 2..N times kept one edge and every other operand vanished with nothing raised.
  `TestInboundHoldsEveryEdgeOnAPort` asserts the tuple shape, the deterministic within-port
  order, that `inbound_edge` **raises** rather than picking one of several, and that a row
  persisted in the old single-edge shape still deserializes.

Nothing is mocked. The synthetic-registry tests build real `BlockDescriptor` objects in a
real `BlockRegistry`, with a counting `warmup_fn` so memoisation is measured rather than
assumed; `TestRealRegistryWarmup` runs the same rule over the production registry.
"""

import inspect
import json
from collections import Counter
from typing import Any, Mapping

import pytest

from backend_app.backend.strategy_dag import plan as plan_module
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag.plan import (
    COMPILER_VERSION,
    ActionSymbolUnresolvedError,
    AmbiguousActionSymbolError,
    CompiledPlan,
    GraphCycleError,
    ModelRequirement,
    PlanBuildError,
    UnresolvedBlockError,
    compute_node_warmups,
    compute_warmup,
    inbound_map,
    predecessor_map,
    resolve_action_markets,
)
from backend_app.backend.strategy_dag.registry import BlockDescriptor, BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    CURRENT_SCHEMA_VERSION,
    BlockCategory,
    EdgeSpec,
    GraphParseError,
    NodeSpec,
    Port,
    PortType,
    StrategyGraph,
    compute_dag_hash,
)

# ---------------------------------------------------------------------------
# A real registry over real descriptors, with a counting warmup_fn
# ---------------------------------------------------------------------------


class WarmupSpy:
    """Builds real `BlockDescriptor`s whose declared warmup counts its own invocations.

    Not a mock of the registry: `BlockRegistry` and `BlockDescriptor.warmup` are the
    production classes and the production code path. Only the declared warmup figure is
    chosen by the test, which is exactly what a block owner declares in production.
    """

    def __init__(self) -> None:
        self.calls: Counter = Counter()

    def descriptor(
        self, block_id: str, category: BlockCategory, bars: int
    ) -> BlockDescriptor:
        def warmup_fn(params: Mapping[str, Any]) -> int:
            self.calls[block_id] += 1
            return int(params.get("bars", bars))

        return BlockDescriptor(
            block_id=block_id,
            display_name=block_id,
            category=category,
            description="",
            inputs=(Port(name="in", type=PortType.SCALAR_SERIES),),
            outputs=(Port(name="out", type=PortType.SCALAR_SERIES),),
            params=(),
            warmup_fn=warmup_fn,
            runtime_ref="tests.compiled_plan.noop",
            source_module="block_specs",
        )

    def registry(self, *specs) -> BlockRegistry:
        """`(block_id, category, bars)` triples -> a real assembled registry."""
        return BlockRegistry([self.descriptor(*spec) for spec in specs])


def node(node_id: str, block_id: str, category: BlockCategory, **params) -> NodeSpec:
    return NodeSpec(
        id=node_id, block_id=block_id, category=category, params=dict(params)
    )


def edge(source: str, target: str) -> EdgeSpec:
    return EdgeSpec(
        id=f"e_{source}_{target}",
        source=source,
        source_port="out",
        target=target,
        target_port="in",
    )


def graph_of(nodes, edges, **envelope) -> StrategyGraph:
    return StrategyGraph(
        schema_version=CURRENT_SCHEMA_VERSION,
        strategy_id=envelope.get("strategy_id", "s_test"),
        version=envelope.get("version", "v1"),
        name=envelope.get("name", "test graph"),
        nodes=list(nodes),
        edges=list(edges),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spy() -> WarmupSpy:
    return WarmupSpy()


@pytest.fixture
def chain(spy):
    """A -> B -> C -> D(ACTION), own warmups 200, 20, 3, 0."""
    registry = spy.registry(
        ("blk_a", BlockCategory.DATA, 200),
        ("blk_b", BlockCategory.INDICATOR, 20),
        ("blk_c", BlockCategory.FEATURE_ENGINEERING, 3),
        ("blk_d", BlockCategory.ACTION, 0),
    )
    graph = graph_of(
        [
            # A DATA node declares a symbol, always: the parameter is required with no
            # default on every published DATA descriptor (Requirements 12.3, 12.4), and it
            # is what the action's traded symbol is resolved from (12.5).
            node("n_a", "blk_a", BlockCategory.DATA, symbol="BTC/USDT", timeframe="1h"),
            node("n_b", "blk_b", BlockCategory.INDICATOR),
            node("n_c", "blk_c", BlockCategory.FEATURE_ENGINEERING),
            node("n_d", "blk_d", BlockCategory.ACTION),
        ],
        [edge("n_a", "n_b"), edge("n_b", "n_c"), edge("n_c", "n_d")],
    )
    return graph, registry


@pytest.fixture
def diamond(spy):
    """A -> B, A -> C, B -> D, C -> D. Own warmups 10, 5, 7, 1."""
    registry = spy.registry(
        ("blk_a", BlockCategory.DATA, 10),
        ("blk_b", BlockCategory.INDICATOR, 5),
        ("blk_c", BlockCategory.INDICATOR, 7),
        ("blk_d", BlockCategory.ACTION, 1),
    )
    graph = graph_of(
        [
            node("n_a", "blk_a", BlockCategory.DATA, symbol="ETH/USDT", timeframe="5m"),
            node("n_b", "blk_b", BlockCategory.INDICATOR),
            node("n_c", "blk_c", BlockCategory.INDICATOR),
            node("n_d", "blk_d", BlockCategory.ACTION),
        ],
        [
            edge("n_a", "n_b"),
            edge("n_a", "n_c"),
            edge("n_b", "n_d"),
            edge("n_c", "n_d"),
        ],
    )
    return graph, registry


@pytest.fixture
def chain_plan(chain):
    graph, registry = chain
    order = ["n_a", "n_b", "n_c", "n_d"]
    return CompiledPlan.from_graph(graph, order, registry=registry), graph


# ---------------------------------------------------------------------------
# 1. dag_hash is a FIELD - SB-02, Requirement 2.4
# ---------------------------------------------------------------------------


class TestDagHashIsAField:
    def test_dag_hash_is_a_readable_attribute(self, chain_plan):
        compiled, graph = chain_plan
        assert isinstance(compiled.dag_hash, str)
        assert compiled.dag_hash == compute_dag_hash(graph)
        assert len(compiled.dag_hash) == 16

    def test_dag_hash_is_not_a_method(self, chain_plan):
        compiled, _ = chain_plan
        assert not callable(compiled.dag_hash), (
            "dag_hash must be data, not a callable: a method is what let a caller forget "
            "to invoke it (SB-02)"
        )
        assert not hasattr(compiled, "compute_hash"), (
            "the deleted CompiledDAG.compute_hash must not come back; the hash is a field"
        )

    def test_sb02_regression_plain_attribute_access_never_raises(self, chain_plan):
        """The exact shape of the SB-02 bug: read the hash the way the clone path reads it.

        The old code did `compiled.get("dag_hash")` on an object, raised `AttributeError`,
        swallowed it, and persisted a clone with no hash. Here plain attribute access is
        the supported path and it yields a usable hash, so there is nothing to swallow.
        """
        compiled, graph = chain_plan

        recorded = None
        error = None
        try:
            recorded = compiled.dag_hash
        except AttributeError as exc:  # pragma: no cover - the regression under test
            error = exc

        assert error is None, f"reading plan.dag_hash raised AttributeError: {error}"
        assert recorded, "a clone would have been persisted with an empty dag_hash"
        assert recorded == compute_dag_hash(graph)

        # And the dict-style access that caused SB-02 is not quietly supported either: a
        # caller mixing object and dict access fails loudly instead of getting None.
        assert not hasattr(compiled, "get")

    def test_dag_hash_present_in_to_dict(self, chain_plan):
        compiled, graph = chain_plan
        payload = compiled.to_dict()
        assert payload["dag_hash"] == compute_dag_hash(graph)
        assert payload["dag_hash"] == compiled.dag_hash

    def test_plan_without_a_hash_cannot_be_constructed(self):
        """The silent-loss state is unrepresentable, not merely detected downstream."""
        for missing in ("", "   ", None):
            with pytest.raises(PlanBuildError):
                CompiledPlan(dag_hash=missing)

    def test_matches_graph_detects_a_stale_plan(self, chain_plan):
        compiled, graph = chain_plan
        assert compiled.matches_graph(graph) is True

        mutated = StrategyGraph.from_dict(graph.to_dict())
        mutated.nodes[1].params["window"] = 99
        assert compiled.matches_graph(mutated) is False


# ---------------------------------------------------------------------------
# 2. Exactly one serialization path - Requirement 2.5
# ---------------------------------------------------------------------------


class TestSingleSerializationPath:
    def test_to_dict_round_trips(self, chain_plan):
        compiled, _ = chain_plan
        assert CompiledPlan.from_dict(compiled.to_dict()) == compiled

    def test_to_json_round_trips_and_delegates(self, chain_plan):
        compiled, _ = chain_plan
        assert json.loads(compiled.to_json()) == compiled.to_dict()
        assert CompiledPlan.from_json(compiled.to_json()) == compiled

    def test_to_dict_is_json_serializable(self, chain_plan):
        compiled, _ = chain_plan
        # It is simultaneously the response body and the compiled_plan column.
        json.dumps(compiled.to_dict())

    def test_no_second_dict_building_path_exists(self):
        """Structural: exactly one method on the class builds the plan's dict."""
        builders = []
        for name, member in inspect.getmembers(CompiledPlan, inspect.isfunction):
            try:
                source = inspect.getsource(member)
            except (OSError, TypeError):
                continue  # dataclass-generated (__init__, __eq__): no source, no dict
            if '"dag_hash":' in source:
                builders.append(name)
        assert builders == ["to_dict"], (
            f"more than one serialization path found: {builders}; every consumer must read "
            "the same dict (Requirement 2.5)"
        )

    def test_to_dict_carries_every_declared_field(self, chain_plan):
        compiled, _ = chain_plan
        payload = compiled.to_dict()
        for name in (
            "dag_hash",
            "schema_version",
            "compiler_version",
            "execution_order",
            "execution_levels",
            "node_index",
            "inbound",
            "data_nodes",
            "action_nodes",
            "ml_nodes",
            "feature_pipeline",
            "warmup_bars",
            "required_models",
            "dependencies",
            # Requirement 12.5: a persisted plan carries the market each action trades, so
            # a deployment reads it off the row instead of re-deriving it.
            "action_symbols",
            "action_timeframes",
        ):
            assert name in payload, f"to_dict() dropped {name}"


# ---------------------------------------------------------------------------
# 3. Warmup COMPOSES along a path - Requirement 3.8
# ---------------------------------------------------------------------------


class TestWarmupComposition:
    def test_node_with_no_upstream_returns_its_own_warmup(self, chain, spy):
        graph, registry = chain
        warmups = compute_node_warmups(graph, registry)
        assert warmups["n_a"] == 200

    def test_chain_composes_as_the_sum_along_the_path(self, chain):
        graph, registry = chain
        warmups = compute_node_warmups(graph, registry)

        assert warmups["n_a"] == 200
        assert warmups["n_b"] == 220        # 20 + 200
        assert warmups["n_c"] == 223        # 3 + 220
        assert warmups["n_d"] == 223        # 0 + 223

    def test_composed_warmup_exceeds_the_bare_maximum(self, chain):
        """The whole point of task 1.10.

        A bare `max()` over the nodes answers 200 and ships 23 bars of NaN-contaminated
        feature values into whatever consumes them.
        """
        graph, registry = chain
        own = [200, 20, 3, 0]

        composed = compute_warmup(graph, registry)

        assert composed == sum(own)
        assert composed > max(own), (
            "warmup must compose along the path, not take a bare maximum "
            f"(composed {composed} vs max {max(own)})"
        )

    def test_compute_warmup_reports_the_action_path(self, chain):
        graph, registry = chain
        assert compute_warmup(graph, registry) == 223
        assert compute_warmup(graph, registry, node_ids=["n_b"]) == 220

    def test_diamond_composes_and_memoises(self, diamond, spy):
        """A shared upstream subtree is evaluated once, not once per path."""
        graph, registry = diamond

        warmups = compute_node_warmups(graph, registry)

        assert warmups["n_a"] == 10
        assert warmups["n_b"] == 15         # 5 + 10
        assert warmups["n_c"] == 17         # 7 + 10
        assert warmups["n_d"] == 18         # 1 + max(15, 17)

        assert dict(spy.calls) == {
            "blk_a": 1,
            "blk_b": 1,
            "blk_c": 1,
            "blk_d": 1,
        }, f"warmup_fn was not memoised per node: {dict(spy.calls)}"

    def test_independent_branches_take_the_max_of_the_branches_plus_own(self, spy):
        """Two disjoint upstream branches meeting at one node: max, then add own."""
        registry = spy.registry(
            ("blk_deep_1", BlockCategory.DATA, 100),
            ("blk_deep_2", BlockCategory.INDICATOR, 40),      # branch 1 total 140
            ("blk_shallow_1", BlockCategory.DATA, 30),
            ("blk_shallow_2", BlockCategory.INDICATOR, 5),    # branch 2 total 35
            ("blk_join", BlockCategory.ACTION, 7),
        )
        graph = graph_of(
            [
                node("n_d1", "blk_deep_1", BlockCategory.DATA),
                node("n_d2", "blk_deep_2", BlockCategory.INDICATOR),
                node("n_s1", "blk_shallow_1", BlockCategory.DATA),
                node("n_s2", "blk_shallow_2", BlockCategory.INDICATOR),
                node("n_join", "blk_join", BlockCategory.ACTION),
            ],
            [
                edge("n_d1", "n_d2"),
                edge("n_s1", "n_s2"),
                edge("n_d2", "n_join"),
                edge("n_s2", "n_join"),
            ],
        )

        warmups = compute_node_warmups(graph, registry)

        assert warmups["n_d2"] == 140
        assert warmups["n_s2"] == 35
        assert warmups["n_join"] == 147     # 7 + max(140, 35), not 7 + 140 + 35
        assert compute_warmup(graph, registry) == 147

    def test_a_single_node_graph_returns_its_own_warmup(self, spy):
        registry = spy.registry(("blk_only", BlockCategory.ACTION, 12))
        graph = graph_of([node("n_only", "blk_only", BlockCategory.ACTION)], [])
        assert compute_node_warmups(graph, registry) == {"n_only": 12}
        assert compute_warmup(graph, registry) == 12

    def test_params_reach_the_declared_warmup_fn(self, spy):
        """Own warmup comes from the block's declared function applied to node params."""
        registry = spy.registry(("blk_win", BlockCategory.ACTION, 0))
        graph = graph_of([node("n_win", "blk_win", BlockCategory.ACTION, bars=55)], [])
        assert compute_warmup(graph, registry) == 55

    def test_unresolved_block_is_named_not_silently_zero(self, spy):
        registry = spy.registry(("blk_known", BlockCategory.ACTION, 4))
        graph = graph_of([node("n_x", "blk_missing", BlockCategory.INDICATOR)], [])
        with pytest.raises(UnresolvedBlockError) as caught:
            compute_warmup(graph, registry)
        assert "n_x" in str(caught.value)
        assert "blk_missing" in str(caught.value)

    def test_a_cycle_is_reported_not_recursed_forever(self, spy):
        registry = spy.registry(
            ("blk_a", BlockCategory.INDICATOR, 1),
            ("blk_b", BlockCategory.INDICATOR, 1),
        )
        graph = graph_of(
            [
                node("n_a", "blk_a", BlockCategory.INDICATOR),
                node("n_b", "blk_b", BlockCategory.INDICATOR),
            ],
            [edge("n_a", "n_b"), edge("n_b", "n_a")],
        )
        with pytest.raises(GraphCycleError):
            compute_warmup(graph, registry)

    def test_a_long_chain_does_not_exhaust_the_stack(self, spy):
        """2000 nodes deep: the walk is iterative, so depth is not a recursion limit."""
        depth = 2000
        registry = spy.registry(*[(f"blk_{i}", BlockCategory.INDICATOR, 1) for i in range(depth)])
        nodes = [node(f"n_{i}", f"blk_{i}", BlockCategory.INDICATOR) for i in range(depth)]
        edges = [edge(f"n_{i}", f"n_{i + 1}") for i in range(depth - 1)]
        graph = graph_of(nodes, edges)

        warmups = compute_node_warmups(graph, registry)
        assert warmups[f"n_{depth - 1}"] == depth


# ---------------------------------------------------------------------------
# 4. The same rule over the production registry
# ---------------------------------------------------------------------------


class TestRealRegistryWarmup:
    """ohlcv_feed -> ema(200) -> feat_rolling_std(20) -> feat_lag(3) -> action_buy_market.

    The design's own worked example. Figures are read from the production descriptors, not
    restated here, so this test tracks the block owners rather than duplicating them.
    """

    @pytest.fixture
    def real_registry(self):
        return registry_module.get_registry()

    @pytest.fixture
    def real_graph(self):
        return graph_of(
            [
                node(
                    "n_data",
                    "ohlcv_feed",
                    BlockCategory.DATA,
                    symbol="BTC/USDT",
                    timeframe="1h",
                ),
                node("n_ema", "ema", BlockCategory.INDICATOR, window=200),
                node(
                    "n_std",
                    "feat_rolling_std",
                    BlockCategory.FEATURE_ENGINEERING,
                    window=20,
                ),
                node("n_lag", "feat_lag", BlockCategory.FEATURE_ENGINEERING, lags=[3]),
                node("n_buy", "action_buy_market", BlockCategory.ACTION),
            ],
            [
                edge("n_data", "n_ema"),
                edge("n_ema", "n_std"),
                edge("n_std", "n_lag"),
                edge("n_lag", "n_buy"),
            ],
        )

    def test_real_chain_composes(self, real_graph, real_registry):
        own = {
            node_spec.id: real_registry.get(node_spec.block_id).warmup(node_spec.params)
            for node_spec in real_graph.nodes
        }
        composed = compute_warmup(real_graph, real_registry)

        assert composed == sum(own.values())
        assert composed > max(own.values()), (
            "the production registry must compose too: "
            f"composed {composed}, largest single warmup {max(own.values())}"
        )

    def test_plan_over_the_real_registry(self, real_graph, real_registry):
        compiled = CompiledPlan.from_graph(
            real_graph,
            ["n_data", "n_ema", "n_std", "n_lag", "n_buy"],
            registry=real_registry,
        )
        assert compiled.dag_hash == compute_dag_hash(real_graph)
        assert compiled.warmup_bars == compute_warmup(real_graph, real_registry)
        assert compiled.data_nodes == ("n_data",)
        assert compiled.action_nodes == ("n_buy",)
        assert compiled.feature_pipeline == ("n_std", "n_lag")
        assert CompiledPlan.from_dict(compiled.to_dict()) == compiled


# ---------------------------------------------------------------------------
# 5. Plan construction from a graph
# ---------------------------------------------------------------------------


class TestPlanFromGraph:
    def test_derived_views_match_the_graph(self, chain_plan):
        compiled, graph = chain_plan

        assert compiled.execution_order == ("n_a", "n_b", "n_c", "n_d")
        assert compiled.execution_levels == (
            ("n_a",),
            ("n_b",),
            ("n_c",),
            ("n_d",),
        )
        assert compiled.schema_version == graph.schema_version
        assert compiled.compiler_version == COMPILER_VERSION
        assert compiled.warmup_bars == 223
        assert compiled.data_nodes == ("n_a",)
        assert compiled.action_nodes == ("n_d",)
        assert compiled.ml_nodes == ()
        assert compiled.feature_pipeline == ("n_c",)
        assert compiled.predecessors("n_c") == ("n_b",)
        assert compiled.node("n_b").block_id == "blk_b"
        assert compiled.inbound_edge("n_b", "in").source == "n_a"

    def test_incomplete_execution_order_is_rejected(self, chain):
        graph, registry = chain
        with pytest.raises(PlanBuildError):
            CompiledPlan.from_graph(graph, ["n_a", "n_b"], registry=registry)

    def test_diamond_levels_place_predecessors_earlier(self, diamond):
        graph, registry = diamond
        compiled = CompiledPlan.from_graph(
            graph, ["n_a", "n_b", "n_c", "n_d"], registry=registry
        )
        assert compiled.execution_levels == (("n_a",), ("n_b", "n_c"), ("n_d",))

    def test_plan_is_immutable(self, chain_plan):
        compiled, _ = chain_plan
        with pytest.raises(Exception):
            compiled.dag_hash = "0000000000000000"

    def test_edge_derived_views(self, diamond):
        graph, _ = diamond
        assert predecessor_map(graph) == {
            "n_a": (),
            "n_b": ("n_a",),
            "n_c": ("n_a",),
            "n_d": ("n_b", "n_c"),
        }
        assert set(inbound_map(graph)["n_d"]) == {"in"}


# ---------------------------------------------------------------------------
# 5b. inbound is target_port -> EVERY edge on that port - task 5.0
# ---------------------------------------------------------------------------


class TestInboundHoldsEveryEdgeOnAPort:
    """``inbound`` is ``node_id -> target_port -> Tuple[EdgeSpec, ...]``, per ``design.md``.

    It used to be one edge per port, built with last-write-wins. A **variadic** input port
    legitimately holds 2..N connections - the validator's rule R7 refuses a second edge only
    on a *non*-variadic port - so that shape silently discarded every operand but one:
    ``add`` returned one addend, ``and`` one condition, ``feat_concat`` one matrix. The
    ``diamond`` fixture wires ``n_b`` and ``n_c`` into the same ``n_d.in``, which is exactly
    the case that was being collapsed.
    """

    @pytest.fixture
    def diamond_plan(self, diamond):
        graph, registry = diamond
        return (
            CompiledPlan.from_graph(
                graph, ["n_a", "n_b", "n_c", "n_d"], registry=registry
            ),
            graph,
        )

    def test_inbound_map_keeps_both_edges_in_a_deterministic_order(self, diamond):
        graph, _ = diamond
        on_port = inbound_map(graph)["n_d"]["in"]
        assert isinstance(on_port, tuple)
        # ``_sorted_edges`` order: (source, source_port, target, target_port).
        assert [edge.source for edge in on_port] == ["n_b", "n_c"]

    def test_inbound_edges_returns_every_operand(self, diamond_plan):
        compiled, _ = diamond_plan
        assert [edge.source for edge in compiled.inbound_edges("n_d", "in")] == [
            "n_b",
            "n_c",
        ]
        # A single-fed port answers as a one-element tuple, so a caller handles one shape.
        assert [edge.source for edge in compiled.inbound_edges("n_b", "in")] == ["n_a"]
        # An unfed port and an unknown node are both the empty tuple, not a KeyError.
        assert compiled.inbound_edges("n_a", "in") == ()
        assert compiled.inbound_edges("no_such_node", "in") == ()

    def test_inbound_edge_refuses_to_pick_one_of_several(self, diamond_plan):
        """The decision recorded in task 5.0: the singular accessor raises, never guesses.

        Returning "an" edge from a multiply-fed port is the defect itself - the caller gets a
        plausible answer computed from a subset of the author's operands with nothing raised.
        """
        compiled, _ = diamond_plan
        with pytest.raises(PlanBuildError) as excinfo:
            compiled.inbound_edge("n_d", "in")
        message = str(excinfo.value)
        assert "n_d" in message and "in" in message
        assert "e_n_b_n_d" in message and "e_n_c_n_d" in message
        assert "inbound_edges" in message, "the error must name the accessor to use instead"

        # Single-arity ports keep the convenient singular read.
        assert compiled.inbound_edge("n_b", "in").source == "n_a"
        assert compiled.inbound_edge("n_a", "in") is None

    def test_to_dict_writes_a_list_per_port_and_round_trips(self, diamond_plan):
        compiled, _ = diamond_plan
        payload = compiled.to_dict()
        serialized = payload["inbound"]["n_d"]["in"]
        assert isinstance(serialized, list) and len(serialized) == 2, (
            "design.md types the field Map<String, List<EdgeSpec>>; a single object here is "
            "how the second operand was lost in persistence"
        )
        json.dumps(payload)  # still the compiled_plan column and the response body

        reloaded = CompiledPlan.from_dict(payload)
        assert reloaded == compiled
        assert [edge.source for edge in reloaded.inbound_edges("n_d", "in")] == [
            "n_b",
            "n_c",
        ]

    def test_a_row_persisted_in_the_old_single_edge_shape_still_loads(self, diamond_plan):
        """Backward compatibility: an existing ``compiled_plan`` row keeps deserializing.

        A row written before this fix stores ONE edge object per port. It is read as a
        one-element tuple - the only honest reading, since the operands the old compiler
        dropped were never in the bytes to begin with.
        """
        compiled, _ = diamond_plan
        payload = compiled.to_dict()
        payload["inbound"] = {
            node_id: {port: edges[-1] for port, edges in ports.items() if edges}
            for node_id, ports in payload["inbound"].items()
        }

        legacy = CompiledPlan.from_json(json.dumps(payload))
        assert [edge.source for edge in legacy.inbound_edges("n_d", "in")] == ["n_c"]
        assert legacy.inbound_edge("n_b", "in").source == "n_a"
        # And re-serializing it produces the current list shape, so the row heals on write.
        assert isinstance(legacy.to_dict()["inbound"]["n_d"]["in"], list)

    def test_an_unreadable_inbound_entry_is_rejected_rather_than_skipped(self, diamond_plan):
        """A port whose value cannot be read is missing wiring, and must not be dropped."""
        compiled, _ = diamond_plan
        for broken in ("not-an-edge", 17):
            payload = compiled.to_dict()
            payload["inbound"]["n_d"]["in"] = broken
            with pytest.raises(PlanBuildError):
                CompiledPlan.from_dict(payload)

        # A malformed edge *object* is refused by the schema's own parser, which is the
        # right owner of "what an edge is" - not silently dropped from the port either.
        payload = compiled.to_dict()
        payload["inbound"]["n_d"]["in"] = [{"id": "e", "source": "n_b"}]
        with pytest.raises(GraphParseError):
            CompiledPlan.from_dict(payload)

        payload = compiled.to_dict()
        payload["inbound"]["n_d"] = ["not", "a", "mapping"]
        with pytest.raises(PlanBuildError):
            CompiledPlan.from_dict(payload)


# ---------------------------------------------------------------------------
# 6. An ACTION node's market is RESOLVED, never guessed - Requirement 12.5
# ---------------------------------------------------------------------------


def data_node(node_id: str, block_id: str = "blk_data", **params) -> NodeSpec:
    """A DATA node. ``symbol`` / ``timeframe`` are passed explicitly by each test."""
    return node(node_id, block_id, BlockCategory.DATA, **params)


def market_registry(spy: WarmupSpy) -> BlockRegistry:
    """Two DATA blocks, one INDICATOR, two ACTION blocks - enough for any shape below."""
    return spy.registry(
        ("blk_data", BlockCategory.DATA, 0),
        ("blk_data_alt", BlockCategory.DATA, 0),
        ("blk_ind", BlockCategory.INDICATOR, 0),
        ("blk_action", BlockCategory.ACTION, 0),
        ("blk_action_alt", BlockCategory.ACTION, 0),
    )


class TestActionMarketResolution:
    """SB-06's replacement for the deleted `|| "BTC/USDT"` fallback.

    `block_specs` forbids a traded-asset param on every ACTION descriptor
    (Requirement 12.8), so `resolve_action_markets` is the only thing that can answer
    *which market does this order belong to?*. Its stated postcondition is one non-empty
    entry for **every** ACTION node or a raise - never a default - and these tests hold it
    to exactly that. `action_timeframes` is deliberately partial and is asserted as such.
    """

    def test_every_action_resolves_a_non_empty_symbol(self, chain_plan):
        compiled, graph = chain_plan
        symbols, timeframes = resolve_action_markets(graph)

        assert set(symbols) == set(compiled.action_nodes)
        assert all(value.strip() for value in symbols.values())
        assert symbols == {"n_d": "BTC/USDT"}
        assert timeframes == {"n_d": "1h"}
        # The plan publishes the same answer through its accessors.
        assert compiled.action_symbol("n_d") == "BTC/USDT"
        assert compiled.action_timeframe("n_d") == "1h"

    def test_resolution_is_per_action_not_per_graph(self, spy):
        """Two actions descending from two feeds get their own symbols.

        A graph-wide symbol list cannot say which of them an order belongs to; that was the
        whole reason `extract_resource_dependencies` could not be used here.
        """
        registry = market_registry(spy)
        graph = graph_of(
            [
                data_node("n_d1", symbol="BTC/USDT", timeframe="1h"),
                data_node("n_d2", "blk_data_alt", symbol="ETH/USDT", timeframe="5m"),
                node("n_a1", "blk_action", BlockCategory.ACTION),
                node("n_a2", "blk_action_alt", BlockCategory.ACTION),
            ],
            [edge("n_d1", "n_a1"), edge("n_d2", "n_a2")],
        )

        symbols, timeframes = resolve_action_markets(graph)
        assert symbols == {"n_a1": "BTC/USDT", "n_a2": "ETH/USDT"}
        assert timeframes == {"n_a1": "1h", "n_a2": "5m"}

        compiled = CompiledPlan.from_graph(
            graph, ["n_d1", "n_d2", "n_a1", "n_a2"], registry=registry
        )
        assert compiled.action_symbol("n_a1") == "BTC/USDT"
        assert compiled.action_symbol("n_a2") == "ETH/USDT"

    def test_action_with_no_data_node_upstream_raises(self, spy):
        """No DATA ancestor is a raise, not an empty string and not a literal default."""
        registry = market_registry(spy)
        graph = graph_of(
            [
                node("n_ind", "blk_ind", BlockCategory.INDICATOR),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_ind", "n_act")],
        )

        with pytest.raises(ActionSymbolUnresolvedError) as caught:
            resolve_action_markets(graph)
        assert caught.value.action_id == "n_act"
        assert "n_act" in str(caught.value)
        assert isinstance(caught.value, PlanBuildError)

        # And no plan can be built either: the compile stops rather than emitting an
        # action whose instrument is unknown.
        with pytest.raises(ActionSymbolUnresolvedError):
            CompiledPlan.from_graph(graph, ["n_ind", "n_act"], registry=registry)

    def test_data_node_that_declares_no_symbol_raises_and_names_it(self, spy):
        graph = graph_of(
            [
                data_node("n_data", timeframe="1h"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_data", "n_act")],
        )

        with pytest.raises(ActionSymbolUnresolvedError) as caught:
            resolve_action_markets(graph)
        assert caught.value.data_ancestors == ("n_data",)
        assert "n_data" in str(caught.value)

    def test_blank_symbol_is_not_a_symbol(self, spy):
        """`"   "` must fail exactly like an absent value, not resolve to whitespace."""
        graph = graph_of(
            [
                data_node("n_data", symbol="   ", timeframe="1h"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_data", "n_act")],
        )
        with pytest.raises(ActionSymbolUnresolvedError):
            resolve_action_markets(graph)

    def test_two_data_nodes_disagreeing_on_symbol_raise_and_name_both(self, spy):
        registry = market_registry(spy)
        graph = graph_of(
            [
                data_node("n_d1", symbol="BTC/USDT", timeframe="1h"),
                data_node("n_d2", "blk_data_alt", symbol="ETH/USDT", timeframe="1h"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_d1", "n_act"), edge("n_d2", "n_act")],
        )

        with pytest.raises(AmbiguousActionSymbolError) as caught:
            resolve_action_markets(graph)
        assert caught.value.action_id == "n_act"
        assert caught.value.symbols == ("BTC/USDT", "ETH/USDT")
        message = str(caught.value)
        assert "BTC/USDT" in message and "ETH/USDT" in message

        with pytest.raises(AmbiguousActionSymbolError):
            CompiledPlan.from_graph(graph, ["n_d1", "n_d2", "n_act"], registry=registry)

    def test_timeframe_is_present_when_the_closure_gives_one_answer(self, spy):
        """Two feeds, same symbol, same timeframe: unambiguous, so it is recorded."""
        graph = graph_of(
            [
                data_node("n_d1", symbol="BTC/USDT", timeframe="1h"),
                data_node("n_d2", "blk_data_alt", symbol="BTC/USDT", timeframe="1h"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_d1", "n_act"), edge("n_d2", "n_act")],
        )
        symbols, timeframes = resolve_action_markets(graph)
        assert symbols == {"n_act": "BTC/USDT"}
        assert timeframes == {"n_act": "1h"}

    def test_timeframe_is_absent_when_the_closure_gives_two_answers(self, spy):
        """A 5m entry filtered by a 1h trend: one symbol, no single cadence.

        Permitted by the validator, so this must resolve the symbol and simply omit the
        timeframe. Substituting one would be the `"15m"` equivalent of the `"BTC/USDT"`
        this requirement exists to delete.
        """
        graph = graph_of(
            [
                data_node("n_d1", symbol="BTC/USDT", timeframe="5m"),
                data_node("n_d2", "blk_data_alt", symbol="BTC/USDT", timeframe="1h"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_d1", "n_act"), edge("n_d2", "n_act")],
        )
        symbols, timeframes = resolve_action_markets(graph)
        assert symbols == {"n_act": "BTC/USDT"}
        assert "n_act" not in timeframes

    def test_timeframe_is_absent_for_a_feed_that_declares_none(self, spy):
        """`live_ticker` has a symbol and no timeframe: a ticker is not bar-aligned."""
        registry = market_registry(spy)
        graph = graph_of(
            [
                data_node("n_data", symbol="BTC/USDT"),
                node("n_act", "blk_action", BlockCategory.ACTION),
            ],
            [edge("n_data", "n_act")],
        )
        symbols, timeframes = resolve_action_markets(graph)
        assert symbols == {"n_act": "BTC/USDT"}
        assert timeframes == {}

        compiled = CompiledPlan.from_graph(graph, ["n_data", "n_act"], registry=registry)
        assert compiled.action_symbol("n_act") == "BTC/USDT"
        assert compiled.action_timeframe("n_act") is None

    def test_a_graph_with_no_action_node_resolves_nothing(self, spy):
        graph = graph_of([data_node("n_data", symbol="BTC/USDT")], [])
        assert resolve_action_markets(graph) == ({}, {})

    def test_a_blank_symbol_cannot_be_stored_on_a_plan(self, chain_plan):
        """A blank traded symbol on a plan is the same defect as a missing one."""
        compiled, _ = chain_plan
        for bad in ({"n_d": ""}, {"n_d": "   "}, {"n_d": None}, {"n_d": 7}):
            with pytest.raises(PlanBuildError):
                CompiledPlan(dag_hash=compiled.dag_hash, action_symbols=bad)
        with pytest.raises(PlanBuildError):
            CompiledPlan(dag_hash=compiled.dag_hash, action_symbols=["n_d"])


class TestActionSymbolsAreNotPartOfIdentity:
    """`action_symbols` is derived state: it rides on the plan, never in the hash.

    It is derived from precisely the input `compute_dag_hash` already hashes - nodes, params
    and wiring - so it adds no discriminating power, and including it would make the
    identity of a *stored* strategy move when the compiler's resolution code changed rather
    than when the strategy did.
    """

    def test_the_hash_function_takes_the_graph_alone(self):
        parameters = list(inspect.signature(compute_dag_hash).parameters)
        assert parameters == ["graph"], (
            "compute_dag_hash must hash the graph and nothing else; a plan-derived input "
            f"would make identity depend on the compiler: {parameters}"
        )
        source = inspect.getsource(compute_dag_hash)
        for derived in ("action_symbol", "action_timeframe"):
            assert derived not in source, f"{derived} leaked into the identity hash"

    def test_plan_hash_equals_the_graph_hash_although_symbols_are_resolved(
        self, chain_plan
    ):
        compiled, graph = chain_plan
        assert compiled.action_symbols, "the fixture must resolve a symbol to be a test"
        assert compiled.dag_hash == compute_dag_hash(graph)

    def test_dropping_action_symbols_does_not_move_the_hash(self, chain_plan):
        """The 2.0.0 -> 2.1.0 shape change must not have re-identified any strategy."""
        compiled, graph = chain_plan
        payload = compiled.to_dict()
        assert payload["action_symbols"] == {"n_d": "BTC/USDT"}

        payload.pop("action_symbols")
        payload.pop("action_timeframes")
        stripped = CompiledPlan.from_dict(payload)

        assert stripped.dag_hash == compiled.dag_hash == compute_dag_hash(graph)
        assert stripped.matches_graph(graph) is True

    def test_changing_the_symbol_changes_the_hash_through_the_graph(self, chain):
        """Identity still tracks the traded market - because the DATA node's param does."""
        graph, registry = chain
        order = ["n_a", "n_b", "n_c", "n_d"]
        before = CompiledPlan.from_graph(graph, order, registry=registry)

        moved = StrategyGraph.from_dict(graph.to_dict())
        moved.nodes[0].params["symbol"] = "ETH/USDT"
        after = CompiledPlan.from_graph(moved, order, registry=registry)

        assert after.action_symbols == {"n_d": "ETH/USDT"}
        assert after.dag_hash != before.dag_hash


class TestPlanWrittenByCompiler200(object):
    """A row written by compiler 2.0.0 still loads (Requirement 9.2's immutability).

    The compiler's postcondition is what stops a *new* plan escaping unresolved; an
    already-persisted artifact must not become unloadable, or an existing deployment breaks
    on upgrade.
    """

    def _legacy_payload(self, compiled: CompiledPlan) -> dict:
        payload = compiled.to_dict()
        payload["compiler_version"] = "2.0.0"
        payload.pop("action_symbols")
        payload.pop("action_timeframes")
        return payload

    def test_a_2_0_0_plan_loads_with_no_resolved_market(self, chain_plan):
        compiled, graph = chain_plan
        legacy = CompiledPlan.from_dict(self._legacy_payload(compiled))

        assert legacy.compiler_version == "2.0.0"
        assert legacy.action_symbols == {}
        assert legacy.action_timeframes == {}
        # None means "recompile before executing", never "any market".
        assert legacy.action_symbol("n_d") is None
        assert legacy.action_timeframe("n_d") is None
        # Everything else about the artifact is intact.
        assert legacy.execution_order == compiled.execution_order
        assert legacy.warmup_bars == compiled.warmup_bars
        assert legacy.dag_hash == compute_dag_hash(graph)

    def test_a_2_0_0_plan_round_trips_through_json(self, chain_plan):
        compiled, _ = chain_plan
        legacy = CompiledPlan.from_dict(self._legacy_payload(compiled))
        assert CompiledPlan.from_json(legacy.to_json()) == legacy
        # to_dict re-emits the keys, empty rather than absent, so every consumer of a
        # loaded plan reads one shape.
        assert legacy.to_dict()["action_symbols"] == {}

    def test_an_explicit_null_is_read_as_absent(self, chain_plan):
        compiled, _ = chain_plan
        payload = compiled.to_dict()
        payload["action_symbols"] = None
        payload["action_timeframes"] = None
        assert CompiledPlan.from_dict(payload).action_symbols == {}

    def test_a_malformed_map_is_rejected_rather_than_ignored(self, chain_plan):
        compiled, _ = chain_plan
        payload = compiled.to_dict()
        payload["action_symbols"] = {"n_d": ""}
        with pytest.raises(PlanBuildError):
            CompiledPlan.from_dict(payload)

    def test_the_current_compiler_version_is_stamped_on_a_new_plan(self, chain_plan):
        compiled, _ = chain_plan
        assert compiled.compiler_version == COMPILER_VERSION == "2.1.0"


# ---------------------------------------------------------------------------
# 7. Model requirements read the descriptor, never restate it
# ---------------------------------------------------------------------------


class TestModelRequirement:
    def test_requirement_is_read_from_the_production_descriptor(self):
        real_registry = registry_module.get_registry()
        descriptor = real_registry.get("lstm")
        if descriptor is None:  # the library is absent in this environment
            pytest.skip("lstm block is not published in this environment")

        spec = descriptor.metadata["model"]
        requirement = ModelRequirement.from_node(
            node("n_ml", "lstm", BlockCategory.ML_DL, epochs=5), descriptor
        )

        assert requirement.node_id == "n_ml"
        assert requirement.sequence_length == spec["sequence_length"]
        assert requirement.min_training_rows == spec["min_training_rows"]
        assert requirement.min_feature_columns == spec["min_feature_columns"]
        assert requirement.model_family == spec["model_family"]
        assert ModelRequirement.from_dict(requirement.to_dict()) == requirement

    def test_requirement_without_a_descriptor_still_names_the_node(self):
        requirement = ModelRequirement.from_node(
            node("n_ml", "unknown_model", BlockCategory.ML_DL), None
        )
        assert requirement.node_id == "n_ml"
        assert requirement.block_id == "unknown_model"
        assert requirement.sequence_length is None


# ---------------------------------------------------------------------------
# 8. Import purity - Requirement 21.10
# ---------------------------------------------------------------------------


def test_importing_plan_stays_light():
    """plan.py must not drag FastAPI, a DB handle or CCXT into the compiler path."""
    source = inspect.getsource(plan_module)
    for forbidden in ("import fastapi", "from fastapi", "import ccxt", "execution_engine"):
        assert forbidden not in source, f"plan.py must not reference {forbidden}"
    # The registry is reached inside functions, never at module import.
    header = source.split("# ---------------------------------------------------------------------------\n# Errors")[0]
    assert "strategy_dag import registry" not in header
