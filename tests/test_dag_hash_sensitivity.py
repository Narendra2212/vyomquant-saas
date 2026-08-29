# -*- coding: utf-8 -*-
"""
tests/test_dag_hash_sensitivity.py - the identity hash is sensitive to every semantic change.

Spec: strategy-builder task 1.13. Requirements 2.2.

**Property 5: The identity hash changes under any semantic change.**
(`design.md` -> Correctness properties, 5:
`forall graphs g, forall semantic changes s (node set, block id, params, port wiring,
category, schema version): dag_hash(g) != dag_hash(s(g))`.)

Why this property is load-bearing
---------------------------------
From Phase 2 onward `dag_hash` is the value deployment, cloning and cache invalidation all
key on: a stored `compiled_plan` is trusted while its hash matches the graph, and
recompiled when it does not (Requirement 22.5). A hash that misses a semantic change is
therefore not cosmetic - a strategy can be materially edited and still be treated as the
already-deployed version, and a stale plan is executed against a graph that no longer
describes it.

The deleted `CompiledDAG.compute_hash` had exactly this defect. It hashed only node ids and
`source->target` strings, so it was blind to parameter changes and to port rewiring, and two
materially different strategies could share a hash. That function is reconstructed below as
:func:`pre_fix_dag_hash` and is *asserted to be blind* on the same inputs the real hash
distinguishes - so the sensitivity assertions in this file are demonstrated to discriminate
rather than merely asserted.

Shape of the file
-----------------
1. `TestProperty5...` - one property test per semantic mutation class, each generating
   random canonical graphs from real registry block ids and applying a random mutation of
   that class: node set (add / remove), `block_id`, params (change / add key / remove key),
   `category`, port wiring (`source_port`, `target_port`, endpoint, add edge, remove edge)
   and `schema_version`.
2. `TestNumericNormalisationBoundary` - the canonicaliser's normalisation is a *semantic*
   statement, not an accident: `21` and `21.0` are the same parameter value and must share a
   hash; `21` and `22` are different values and must not.
3. `TestTheComplementHolds` - the control. A presentation-only change (`ui`: position,
   label, collapsed) and a reordering of the `nodes` / `edges` lists do **not** move the
   hash. Without this, every sensitivity assertion above would be satisfied by a hash that
   changes on everything, and the property would be vacuous. (Task 1.12 owns layout
   invariance as a property in its own right - `Property 4` - this is its control here.)
4. `TestPreFixHashRegression` - the concrete defect the property generalises: two graphs
   differing only in a node's params, and two differing only in port wiring (same node ids,
   same source->target node pairs, different ports). `pre_fix_dag_hash` collides on both;
   `compute_dag_hash` must not.
5. `TestCollisionPressure` - over many generated graphs, two distinct canonical forms never
   share a hash.

No mocks. Block ids, categories, ports and parameter sets come from the assembled
`build_registry()`; the function under test is the real `compute_dag_hash`. Generated graphs
are *canonical in shape* but need not pass full validation - this file is about hashing, not
legality, and `compute_dag_hash` hashes what it is given by contract.
"""

import copy
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from backend_app.backend.strategy_dag.block_specs import ParamType
from backend_app.backend.strategy_dag.registry import (
    BlockDescriptor,
    build_registry,
)
from backend_app.backend.strategy_dag.schema import (
    CURRENT_SCHEMA_VERSION,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    Port,
    StrategyGraph,
    ValidationState,
    canonicalize,
    compute_dag_hash,
    new_edge_id,
    new_node_id,
)

# ---------------------------------------------------------------------------
# The real registry, assembled once. Generated graphs use block ids, categories, ports and
# parameter keys that actually exist, so a mutation is a mutation an author could make.
# ---------------------------------------------------------------------------

REGISTRY = build_registry()
DESCRIPTORS: Tuple[BlockDescriptor, ...] = REGISTRY.blocks()
DESCRIPTORS_BY_CATEGORY: Dict[BlockCategory, Tuple[BlockDescriptor, ...]] = {
    category: REGISTRY.in_category(category) for category in BlockCategory
}
BLOCK_IDS: Tuple[str, ...] = REGISTRY.block_ids()

#: Every real port name the registry publishes, used when a node's own descriptor declares
#: only one port on the side a wiring mutation needs to repoint.
ALL_INPUT_PORT_NAMES: Tuple[str, ...] = tuple(
    sorted({port.name for d in DESCRIPTORS for port in d.inputs})
)
ALL_OUTPUT_PORT_NAMES: Tuple[str, ...] = tuple(
    sorted({port.name for d in DESCRIPTORS for port in d.outputs})
)

#: Identifiers are minted once, at import, from the real minting functions, and then drawn
#: from as a pool. Generation stays reproducible (nothing is minted inside a strategy) while
#: the ids keep the `n_` + ULID shape the schema mints.
NODE_ID_POOL: Tuple[str, ...] = tuple(sorted(new_node_id() for _ in range(10)))
EDGE_ID_POOL: Tuple[str, ...] = tuple(sorted(new_edge_id() for _ in range(12)))

#: Schema versions a mutation can move to. Only the *value* matters here: `compute_dag_hash`
#: hashes the declared version, it does not parse the graph again.
OTHER_SCHEMA_VERSIONS: Tuple[int, ...] = (1, 3, 4, 99)


# ---------------------------------------------------------------------------
# The semantic form: what "a semantic change" means, taken from the design
# ---------------------------------------------------------------------------


def semantic_form(graph: StrategyGraph) -> str:
    """The identity-bearing projection of ``graph``, per `design.md` -> `dag_hash` definition.

    Schema version; per node (ordered by id) the id, block id, category and canonicalized
    params; edges as sorted port-addressed strings. ``ui`` and every envelope field
    (`name`, `metadata`, `validation_state`, edge ids, `edge.type`) are outside it.

    This is the *specification* of identity, independent of sha256. Property 5 says
    `compute_dag_hash` is injective with respect to it; the complement says it is constant
    on its fibres. Both directions below are stated against this function, so a test can
    never claim "semantic change" for a mutation that changed nothing that matters.
    """
    return json.dumps(
        {
            "schema_version": graph.schema_version,
            "nodes": [
                {
                    "id": node.id,
                    "block_id": node.block_id,
                    "category": getattr(node.category, "value", str(node.category)),
                    "params": canonicalize(node.params),
                }
                for node in sorted(graph.nodes, key=lambda item: item.id)
            ],
            "edges": sorted(
                f"{e.source}.{e.source_port}->{e.target}.{e.target_port}"
                for e in graph.edges
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def pre_fix_dag_hash(graph: StrategyGraph) -> str:
    """The deleted ``CompiledDAG.compute_hash``, reconstructed.

    It hashed the schema version, the node **ids** and `source->target` strings - no params,
    no categories, no block ids, no port identity. Kept here so the sensitivity assertions
    can be shown to discriminate: this function is asserted *blind* on the very inputs
    `compute_dag_hash` must distinguish. It is never used as an implementation.
    """
    payload = json.dumps(
        {
            "schema_version": graph.schema_version,
            "nodes": sorted(node.id for node in graph.nodes),
            "edges": sorted(f"{e.source}->{e.target}" for e in graph.edges),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assert_semantic_change_moves_the_hash(
    before: StrategyGraph, after: StrategyGraph, label: str
) -> None:
    """Property 5, as one assertion: a semantic difference implies a hash difference."""
    assert semantic_form(before) != semantic_form(after), (
        f"mutation '{label}' was declared semantic but changed nothing in the "
        "identity-bearing projection; the assertion below would be vacuous"
    )
    assert compute_dag_hash(before) != compute_dag_hash(after), (
        f"Property 5 violated by '{label}': the graph changed semantically but "
        f"dag_hash stayed {compute_dag_hash(before)}, so a materially different "
        "strategy shares an identity with the deployed one (Requirement 2.2)"
    )


def assert_presentation_change_keeps_the_hash(
    before: StrategyGraph, after: StrategyGraph, label: str
) -> None:
    """The complement: no semantic difference implies no hash difference."""
    assert semantic_form(before) == semantic_form(after), (
        f"'{label}' was declared presentation-only but altered the "
        "identity-bearing projection"
    )
    assert compute_dag_hash(before) == compute_dag_hash(after), (
        f"'{label}' is presentation-only, yet dag_hash moved from "
        f"{compute_dag_hash(before)} to {compute_dag_hash(after)}; identity must not "
        "depend on the canvas (Requirement 2.1)"
    )


# ---------------------------------------------------------------------------
# Generators over real descriptors
# ---------------------------------------------------------------------------

#: Realistic free-text parameter values, so a TEXT / SYMBOL / TIMEFRAME parameter carries
#: something an author would actually type rather than random unicode.
_TEXT_VALUES: Tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "close", "hl2")
_TIMEFRAME_VALUES: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")


def param_values(spec: Any) -> st.SearchStrategy[Any]:
    """Values one `ParamSpec` admits, constrained to that spec's own declaration."""
    if spec.options:
        options = list(spec.options)
        if spec.type is ParamType.MULTISELECT:
            return st.lists(st.sampled_from(options), min_size=1, max_size=3, unique=True)
        return st.sampled_from(options)
    if spec.type is ParamType.BOOLEAN:
        return st.booleans()
    if spec.type in (ParamType.INTEGER, ParamType.NUMBER):
        low = int(spec.min) if spec.min is not None else 1
        high = int(spec.max) if spec.max is not None else low + 200
        if high < low:
            high = low
        integers = st.integers(min_value=low, max_value=high)
        if spec.type is ParamType.INTEGER:
            return integers
        return st.one_of(integers, integers.map(float))
    if spec.type is ParamType.TIMEFRAME:
        return st.sampled_from(_TIMEFRAME_VALUES)
    if spec.type is ParamType.DATE:
        return st.sampled_from(("2023-01-01", "2024-06-30", "2025-02-14"))
    return st.sampled_from(_TEXT_VALUES)


def _build_param_maps(descriptor: BlockDescriptor) -> st.SearchStrategy[Dict[str, Any]]:
    """A parameter map for ``descriptor``: a subset of its real keys with admissible values.

    A subset rather than the full set, so the add-key and remove-key mutation classes both
    have room to operate on graphs the generator produced.
    """
    if not descriptor.params:
        return st.just({})
    return st.fixed_dictionaries(
        {},
        optional={spec.key: param_values(spec) for spec in descriptor.params},
    )


#: One parameter strategy per block, built once. Rebuilding it per draw dominated the cost
#: of generation, and the strategies are immutable, so they are shared.
PARAM_MAPS: Dict[str, st.SearchStrategy[Dict[str, Any]]] = {
    descriptor.block_id: _build_param_maps(descriptor) for descriptor in DESCRIPTORS
}

#: Presentation-only node state. Excluded from the hash by contract.
UI_DICTS: st.SearchStrategy[Dict[str, Any]] = st.fixed_dictionaries(
    {
        "position": st.fixed_dictionaries(
            {
                "x": st.integers(min_value=-2000, max_value=2000),
                "y": st.integers(min_value=-2000, max_value=2000),
            }
        ),
    },
    optional={
        "label": st.sampled_from(("Entry", "Fast EMA", "Slow EMA", "Risk gate")),
        "collapsed": st.booleans(),
    },
)


@st.composite
def node_specs(draw, node_id: str) -> NodeSpec:
    """One canonical node with ``node_id``, built from a real registry descriptor."""
    descriptor = draw(st.sampled_from(DESCRIPTORS))
    return NodeSpec(
        id=node_id,
        block_id=descriptor.block_id,
        category=descriptor.category,
        params=draw(PARAM_MAPS[descriptor.block_id]),
        inputs=list(descriptor.inputs),
        outputs=list(descriptor.outputs),
        ui=draw(UI_DICTS),
    )


def _port_name(ports: Sequence[Port], fallback: Tuple[str, ...]) -> st.SearchStrategy[str]:
    if ports:
        return st.sampled_from([port.name for port in ports])
    return st.sampled_from(list(fallback))


@st.composite
def canonical_graphs(
    draw,
    min_nodes: int = 1,
    max_nodes: int = 4,
    min_edges: int = 0,
    max_edges: int = 5,
) -> StrategyGraph:
    """A random canonical-shaped graph over real registry blocks.

    Edges are port-addressed with real port names and are unique by address. The graph is
    not required to be *legal* - it may be cyclic, may lack a DATA or ACTION node and may
    connect incompatible port types. `compute_dag_hash`'s precondition is structural
    (unique node ids, endpoints resolve), which is what this generator guarantees.
    """
    node_ids = draw(
        st.lists(
            st.sampled_from(NODE_ID_POOL),
            min_size=min_nodes,
            max_size=max_nodes,
            unique=True,
        )
    )
    nodes = [draw(node_specs(node_id)) for node_id in node_ids]

    wanted = draw(st.integers(min_value=min_edges, max_value=max_edges))
    addresses: List[Tuple[str, str, str, str]] = []
    seen = set()
    for _ in range(wanted * 3):
        if len(addresses) >= wanted:
            break
        source = draw(st.sampled_from(nodes))
        target = draw(st.sampled_from(nodes))
        source_port = draw(_port_name(source.outputs, ALL_OUTPUT_PORT_NAMES))
        target_port = draw(_port_name(target.inputs, ALL_INPUT_PORT_NAMES))
        address = (source.id, source_port, target.id, target_port)
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    assume(len(addresses) >= min_edges)

    edges = [
        EdgeSpec(
            id=EDGE_ID_POOL[index],
            source=address[0],
            source_port=address[1],
            target=address[2],
            target_port=address[3],
        )
        for index, address in enumerate(addresses)
    ]

    return StrategyGraph(
        schema_version=CURRENT_SCHEMA_VERSION,
        strategy_id="s_hash_sensitivity",
        version="v1",
        name=draw(st.sampled_from(("Momentum", "Mean reversion", "ML gate"))),
        nodes=nodes,
        edges=edges,
        metadata={},
        validation_state=ValidationState.UNVALIDATED,
    )


# ---------------------------------------------------------------------------
# Semantic mutations. Each returns (mutated graph, label), or None when the drawn graph
# offers no room for that class - the caller filters with `assume`.
# ---------------------------------------------------------------------------

Mutation = Optional[Tuple[StrategyGraph, str]]


def _clone(graph: StrategyGraph) -> StrategyGraph:
    return copy.deepcopy(graph)


def _bump(value: Any) -> Any:
    """A different value of the same kind - a change an author could make in the inspector.

    Deterministic rather than redrawn, so no mutation is filtered for accidentally
    reproducing the value it replaced.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.5
    if isinstance(value, str):
        return value + "_alt"
    if isinstance(value, list):
        return value + ["extra"]
    if isinstance(value, dict):
        return {**value, "extra": 1}
    if value is None:
        return 1
    return str(value) + "_alt"


def mutate_add_node(graph: StrategyGraph, data) -> Mutation:
    """Node set: add a node."""
    free = [node_id for node_id in NODE_ID_POOL if graph.node(node_id) is None]
    if not free:
        return None
    node = data.draw(node_specs(data.draw(st.sampled_from(free))))
    mutated = _clone(graph)
    mutated.nodes.append(node)
    return mutated, f"add node {node.id} ({node.block_id})"


def mutate_remove_node(graph: StrategyGraph, data) -> Mutation:
    """Node set: remove a node, and with it the edges that referenced it."""
    if not graph.nodes:
        return None
    victim = data.draw(st.sampled_from([node.id for node in graph.nodes]))
    mutated = _clone(graph)
    mutated.nodes = [node for node in mutated.nodes if node.id != victim]
    mutated.edges = [
        edge
        for edge in mutated.edges
        if edge.source != victim and edge.target != victim
    ]
    return mutated, f"remove node {victim}"


def mutate_block_id(graph: StrategyGraph, data) -> Mutation:
    """`block_id`: point a node at a different real block.

    Same category where the registry offers a sibling, so the mutation isolates `block_id`;
    otherwise any other published block id. The node's `category` field is left untouched
    either way, so exactly one hashed field moves.
    """
    if not graph.nodes:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.nodes) - 1))
    node = graph.nodes[index]
    siblings = [
        d.block_id
        for d in DESCRIPTORS_BY_CATEGORY.get(node.category, ())
        if d.block_id != node.block_id
    ] or [block_id for block_id in BLOCK_IDS if block_id != node.block_id]
    if not siblings:
        return None
    replacement = data.draw(st.sampled_from(sorted(siblings)))
    mutated = _clone(graph)
    mutated.nodes[index].block_id = replacement
    return mutated, f"block_id {node.block_id} -> {replacement} on {node.id}"


def mutate_param_value(graph: StrategyGraph, data) -> Mutation:
    """params: change the value of an existing parameter."""
    candidates = [i for i, node in enumerate(graph.nodes) if node.params]
    if not candidates:
        return None
    index = data.draw(st.sampled_from(candidates))
    node = graph.nodes[index]
    key = data.draw(st.sampled_from(sorted(node.params)))
    replacement = _bump(node.params[key])
    if canonicalize(replacement) == canonicalize(node.params[key]):
        return None
    mutated = _clone(graph)
    mutated.nodes[index].params[key] = replacement
    return mutated, f"param {key} {node.params[key]!r} -> {replacement!r} on {node.id}"


def mutate_param_add_key(graph: StrategyGraph, data) -> Mutation:
    """params: add a parameter key the node did not carry."""
    if not graph.nodes:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.nodes) - 1))
    node = graph.nodes[index]
    descriptor = REGISTRY.get(node.block_id)
    unset = sorted(
        spec.key
        for spec in (descriptor.params if descriptor else ())
        if spec.key not in node.params
    )
    key = data.draw(st.sampled_from(unset)) if unset else "annotation"
    if key in node.params:
        return None
    mutated = _clone(graph)
    mutated.nodes[index].params[key] = 7
    return mutated, f"add param {key} on {node.id}"


def mutate_param_remove_key(graph: StrategyGraph, data) -> Mutation:
    """params: remove a parameter key the node carried."""
    candidates = [i for i, node in enumerate(graph.nodes) if node.params]
    if not candidates:
        return None
    index = data.draw(st.sampled_from(candidates))
    node = graph.nodes[index]
    key = data.draw(st.sampled_from(sorted(node.params)))
    mutated = _clone(graph)
    del mutated.nodes[index].params[key]
    return mutated, f"remove param {key} on {node.id}"


def mutate_category(graph: StrategyGraph, data) -> Mutation:
    """`category`: reclassify a node."""
    if not graph.nodes:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.nodes) - 1))
    node = graph.nodes[index]
    others = [c for c in BlockCategory if c is not node.category]
    replacement = data.draw(st.sampled_from(others))
    mutated = _clone(graph)
    mutated.nodes[index].category = replacement
    return mutated, f"category {node.category.value} -> {replacement.value} on {node.id}"


def _repoint_port(graph: StrategyGraph, data, side: str) -> Mutation:
    if not graph.edges:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.edges) - 1))
    edge = graph.edges[index]
    if side == "source":
        owner = graph.node(edge.source)
        declared = [port.name for port in (owner.outputs if owner else ())]
        pool = declared or list(ALL_OUTPUT_PORT_NAMES)
        current = edge.source_port
    else:
        owner = graph.node(edge.target)
        declared = [port.name for port in (owner.inputs if owner else ())]
        pool = declared or list(ALL_INPUT_PORT_NAMES)
        current = edge.target_port
    alternatives = sorted({name for name in pool if name != current})
    if not alternatives:
        alternatives = sorted(
            {
                name
                for name in (
                    ALL_OUTPUT_PORT_NAMES if side == "source" else ALL_INPUT_PORT_NAMES
                )
                if name != current
            }
        )
    if not alternatives:
        return None
    replacement = data.draw(st.sampled_from(alternatives))
    mutated = _clone(graph)
    if side == "source":
        mutated.edges[index].source_port = replacement
    else:
        mutated.edges[index].target_port = replacement
    if semantic_form(mutated) == semantic_form(graph):
        # The rewired address collided with an edge the graph already carried, so the edge
        # multiset is unchanged. That is not a semantic change; nothing to assert.
        return None
    return mutated, f"{side}_port {current} -> {replacement} on edge {edge.id}"


def mutate_source_port(graph: StrategyGraph, data) -> Mutation:
    """Port wiring: repoint an edge's `source_port`, keeping both endpoints."""
    return _repoint_port(graph, data, "source")


def mutate_target_port(graph: StrategyGraph, data) -> Mutation:
    """Port wiring: repoint an edge's `target_port`, keeping both endpoints."""
    return _repoint_port(graph, data, "target")


def mutate_edge_endpoint(graph: StrategyGraph, data) -> Mutation:
    """Port wiring: repoint an edge onto a different node."""
    if not graph.edges or len(graph.nodes) < 2:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.edges) - 1))
    edge = graph.edges[index]
    side = data.draw(st.sampled_from(("source", "target")))
    current = edge.source if side == "source" else edge.target
    others = sorted({node.id for node in graph.nodes if node.id != current})
    if not others:
        return None
    replacement = data.draw(st.sampled_from(others))
    mutated = _clone(graph)
    setattr(mutated.edges[index], side, replacement)
    if semantic_form(mutated) == semantic_form(graph):
        return None
    return mutated, f"edge {edge.id} {side} {current} -> {replacement}"


def mutate_add_edge(graph: StrategyGraph, data) -> Mutation:
    """Port wiring: add a connection."""
    if not graph.nodes or len(graph.edges) >= len(EDGE_ID_POOL):
        return None
    source = data.draw(st.sampled_from([node.id for node in graph.nodes]))
    target = data.draw(st.sampled_from([node.id for node in graph.nodes]))
    source_node = graph.node(source)
    target_node = graph.node(target)
    source_port = data.draw(
        _port_name(source_node.outputs if source_node else (), ALL_OUTPUT_PORT_NAMES)
    )
    target_port = data.draw(
        _port_name(target_node.inputs if target_node else (), ALL_INPUT_PORT_NAMES)
    )
    mutated = _clone(graph)
    mutated.edges.append(
        EdgeSpec(
            id=EDGE_ID_POOL[len(graph.edges)],
            source=source,
            source_port=source_port,
            target=target,
            target_port=target_port,
        )
    )
    if semantic_form(mutated) == semantic_form(graph):
        # A duplicate address adds no connection the hash can see.
        return None
    return mutated, f"add edge {source}.{source_port}->{target}.{target_port}"


def mutate_remove_edge(graph: StrategyGraph, data) -> Mutation:
    """Port wiring: remove a connection."""
    if not graph.edges:
        return None
    index = data.draw(st.integers(min_value=0, max_value=len(graph.edges) - 1))
    mutated = _clone(graph)
    removed = mutated.edges.pop(index)
    if semantic_form(mutated) == semantic_form(graph):
        return None
    return mutated, f"remove edge {removed.address}"


def mutate_schema_version(graph: StrategyGraph, data) -> Mutation:
    """`schema_version`: declare a different schema."""
    replacement = data.draw(
        st.sampled_from([v for v in OTHER_SCHEMA_VERSIONS if v != graph.schema_version])
    )
    mutated = _clone(graph)
    mutated.schema_version = replacement
    return mutated, f"schema_version {graph.schema_version} -> {replacement}"


#: The mutation classes Requirement 2.2 names, one entry per class.
SEMANTIC_MUTATIONS: Tuple[Tuple[str, Callable[..., Mutation]], ...] = (
    ("node set: add", mutate_add_node),
    ("node set: remove", mutate_remove_node),
    ("block_id", mutate_block_id),
    ("params: change value", mutate_param_value),
    ("params: add key", mutate_param_add_key),
    ("params: remove key", mutate_param_remove_key),
    ("category", mutate_category),
    ("port wiring: source_port", mutate_source_port),
    ("port wiring: target_port", mutate_target_port),
    ("port wiring: endpoint", mutate_edge_endpoint),
    ("port wiring: add edge", mutate_add_edge),
    ("port wiring: remove edge", mutate_remove_edge),
    ("schema_version", mutate_schema_version),
)

#: Example counts are deliberately modest. Each example assembles a graph over the real
#: registry, deep-copies it and hashes both sides, and the mutation classes are checked
#: one per test rather than mixed, so coverage comes from the number of *tests* rather than
#: from a large budget in any one of them. The whole file runs in well under a minute.
PROPERTY_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# 1. Property 5, one test per mutation class
# ---------------------------------------------------------------------------


class TestProperty5SemanticSensitivity:
    """**Property 5: The identity hash changes under any semantic change.**

    Generator: random canonical graphs over real registry blocks, plus a random mutation
    drawn from the named class. Each test asserts both halves - that the mutation really
    changed the identity-bearing projection, and that `compute_dag_hash` moved with it.

    **Validates: Requirements 2.2**
    """

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(), data=st.data())
    def test_node_set_addition_changes_the_hash(self, graph, data):
        outcome = mutate_add_node(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_node_set_removal_changes_the_hash(self, graph, data):
        outcome = mutate_remove_node(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_block_id_change_changes_the_hash(self, graph, data):
        outcome = mutate_block_id(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_param_value_change_changes_the_hash(self, graph, data):
        outcome = mutate_param_value(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_param_key_addition_changes_the_hash(self, graph, data):
        outcome = mutate_param_add_key(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_param_key_removal_changes_the_hash(self, graph, data):
        outcome = mutate_param_remove_key(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_category_change_changes_the_hash(self, graph, data):
        outcome = mutate_category(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=2, min_edges=1), data=st.data())
    def test_source_port_rewiring_changes_the_hash(self, graph, data):
        outcome = mutate_source_port(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=2, min_edges=1), data=st.data())
    def test_target_port_rewiring_changes_the_hash(self, graph, data):
        outcome = mutate_target_port(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=2, min_edges=1), data=st.data())
    def test_edge_endpoint_rewiring_changes_the_hash(self, graph, data):
        outcome = mutate_edge_endpoint(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_edge_addition_changes_the_hash(self, graph, data):
        outcome = mutate_add_edge(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1, min_edges=1), data=st.data())
    def test_edge_removal_changes_the_hash(self, graph, data):
        outcome = mutate_remove_edge(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(), data=st.data())
    def test_schema_version_change_changes_the_hash(self, graph, data):
        outcome = mutate_schema_version(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], outcome[1])

    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(graph=canonical_graphs(min_nodes=2, min_edges=1), data=st.data())
    def test_any_mutation_class_drawn_at_random_changes_the_hash(self, graph, data):
        """The same property over the whole mutation set, mixed rather than per class."""
        label, mutator = data.draw(st.sampled_from(SEMANTIC_MUTATIONS))
        outcome = mutator(graph, data)
        assume(outcome is not None)
        assert_semantic_change_moves_the_hash(graph, outcome[0], f"{label}: {outcome[1]}")


# ---------------------------------------------------------------------------
# 2. The numeric normalisation boundary
# ---------------------------------------------------------------------------


def _single_node_graph(params: Dict[str, Any], block_id: str = "ema") -> StrategyGraph:
    """One node carrying ``params``, with a fixed id so only params can move the hash."""
    descriptor = REGISTRY.get(block_id) or DESCRIPTORS[0]
    return StrategyGraph(
        schema_version=CURRENT_SCHEMA_VERSION,
        nodes=[
            NodeSpec(
                id=NODE_ID_POOL[0],
                block_id=descriptor.block_id,
                category=descriptor.category,
                params=dict(params),
                inputs=list(descriptor.inputs),
                outputs=list(descriptor.outputs),
            )
        ],
    )


class TestNumericNormalisationBoundary:
    """Where "same value" ends and "different value" begins.

    The canonicaliser folds an integral float onto its integer, so `21` and `21.0` are the
    same parameter value and must share an identity. That is a deliberate semantic
    statement, and it is only safe while the *adjacent* case still separates: `21` and `22`
    must not share one. Both halves are asserted here, because a canonicaliser that
    over-normalised (rounding, say) would satisfy the first on its own.

    **Validates: Requirements 2.2**
    """

    @pytest.mark.parametrize(
        "left,right",
        [
            (21, 21.0),
            (21.0, 21),
            (0, 0.0),
            (-5, -5.0),
            ({"length": 21}, {"length": 21.0}),
            ({"weights": [1, 2.0]}, {"weights": [1.0, 2]}),
            ({"a": 1, "b": 2}, {"b": 2, "a": 1}),
        ],
    )
    def test_numerically_equal_params_share_a_hash(self, left, right):
        left_params = left if isinstance(left, dict) else {"length": left}
        right_params = right if isinstance(right, dict) else {"length": right}
        assert compute_dag_hash(_single_node_graph(left_params)) == compute_dag_hash(
            _single_node_graph(right_params)
        ), f"{left_params} and {right_params} are the same parameter value"

    @pytest.mark.parametrize(
        "left,right",
        [
            (21, 22),
            (21, 21.5),
            (21.0, 20.999),
            (0, 1),
            (-5, 5),
            (1, True),
            (0, False),
            (1, "1"),
            (21, None),
        ],
    )
    def test_numerically_different_params_do_not_share_a_hash(self, left, right):
        assert compute_dag_hash(_single_node_graph({"length": left})) != compute_dag_hash(
            _single_node_graph({"length": right})
        ), f"{left!r} and {right!r} are different parameter values"

    @settings(max_examples=30, deadline=None)
    @given(value=st.integers(min_value=-10_000, max_value=10_000))
    def test_integer_and_its_float_twin_agree_for_every_drawn_value(self, value):
        assert compute_dag_hash(_single_node_graph({"length": value})) == compute_dag_hash(
            _single_node_graph({"length": float(value)})
        )

    @settings(max_examples=30, deadline=None)
    @given(
        value=st.integers(min_value=-10_000, max_value=10_000),
        delta=st.integers(min_value=1, max_value=50),
    )
    def test_adjacent_integers_never_agree(self, value, delta):
        assert compute_dag_hash(_single_node_graph({"length": value})) != compute_dag_hash(
            _single_node_graph({"length": value + delta})
        )


# ---------------------------------------------------------------------------
# 3. The complement - without this the sensitivity assertions are vacuous
# ---------------------------------------------------------------------------


def relayout(graph: StrategyGraph, data) -> StrategyGraph:
    """Move every node on the canvas, relabel it and collapse it. Nothing semantic."""
    mutated = _clone(graph)
    for node in mutated.nodes:
        node.ui = {
            "position": {
                "x": data.draw(st.integers(min_value=-9999, max_value=9999)),
                "y": data.draw(st.integers(min_value=-9999, max_value=9999)),
            },
            "label": data.draw(st.sampled_from(("", "Renamed", "Step 2", "gate"))),
            "collapsed": data.draw(st.booleans()),
        }
    return mutated


class TestTheComplementHolds:
    """A hash that changed on *everything* would satisfy Property 5 and be useless.

    These are the control cases: presentation-only edits and list reorderings leave the
    hash where it was. Task 1.12 owns layout invariance (Property 4) as a property in its
    own right; here it is what proves the assertions above are not trivially true.

    **Validates: Requirements 2.1, 2.2**
    """

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_relayout_does_not_change_the_hash(self, graph, data):
        assert_presentation_change_keeps_the_hash(graph, relayout(graph, data), "relayout")

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=2, min_edges=2), data=st.data())
    def test_reordering_the_node_and_edge_lists_does_not_change_the_hash(self, graph, data):
        """The hash iterates a total order, so input order cannot matter."""
        mutated = _clone(graph)
        mutated.nodes = data.draw(st.permutations(mutated.nodes))
        mutated.edges = data.draw(st.permutations(mutated.edges))
        assume(
            [n.id for n in mutated.nodes] != [n.id for n in graph.nodes]
            or [e.id for e in mutated.edges] != [e.id for e in graph.edges]
        )
        assert_presentation_change_keeps_the_hash(graph, mutated, "reorder nodes/edges")

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1, min_edges=1), data=st.data())
    def test_relabelling_edge_ids_does_not_change_the_hash(self, graph, data):
        """Edges are hashed by port address, not by id: the same wiring is one strategy."""
        mutated = _clone(graph)
        for offset, edge in enumerate(mutated.edges):
            edge.id = EDGE_ID_POOL[-(offset + 1)]
        assume([e.id for e in mutated.edges] != [e.id for e in graph.edges])
        assert_presentation_change_keeps_the_hash(graph, mutated, "relabel edge ids")

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(), data=st.data())
    def test_envelope_metadata_does_not_change_the_hash(self, graph, data):
        """`name`, `metadata` and `validation_state` are not identity."""
        mutated = _clone(graph)
        mutated.name = data.draw(st.sampled_from(("", "Renamed strategy", "copy of X")))
        mutated.metadata = {"note": data.draw(st.sampled_from(("a", "b")))}
        mutated.validation_state = data.draw(st.sampled_from(list(ValidationState)))
        assert_presentation_change_keeps_the_hash(graph, mutated, "envelope metadata")

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(), data=st.data())
    def test_the_hash_is_stable_across_repeated_computation(self, graph, data):
        """Determinism within a process; task 2.6 owns the cross-process half."""
        first = compute_dag_hash(graph)
        assert first == compute_dag_hash(graph) == compute_dag_hash(_clone(graph))
        assert len(first) == 16
        int(first, 16)  # 16 lowercase hex chars, per the design's output contract


# ---------------------------------------------------------------------------
# 4. The concrete defect: the deleted CompiledDAG.compute_hash
# ---------------------------------------------------------------------------


def _two_node_graph(
    upstream_params: Dict[str, Any],
    target_port: str,
    upstream_block: str,
    downstream_block: str,
) -> StrategyGraph:
    """A fixed two-node, one-edge graph. Only the two named knobs vary between instances."""
    up = REGISTRY[upstream_block]
    down = REGISTRY[downstream_block]
    nodes = [
        NodeSpec(
            id=NODE_ID_POOL[0],
            block_id=up.block_id,
            category=up.category,
            params=dict(upstream_params),
            inputs=list(up.inputs),
            outputs=list(up.outputs),
            ui={"position": {"x": 0, "y": 0}},
        ),
        NodeSpec(
            id=NODE_ID_POOL[1],
            block_id=down.block_id,
            category=down.category,
            params={},
            inputs=list(down.inputs),
            outputs=list(down.outputs),
            ui={"position": {"x": 300, "y": 0}},
        ),
    ]
    edges = [
        EdgeSpec(
            id=EDGE_ID_POOL[0],
            source=NODE_ID_POOL[0],
            source_port=up.outputs[0].name,
            target=NODE_ID_POOL[1],
            target_port=target_port,
        )
    ]
    return StrategyGraph(schema_version=CURRENT_SCHEMA_VERSION, nodes=nodes, edges=edges)


#: A real block with two distinct input ports, so "same nodes, same source->target pair,
#: different port" is expressible without inventing a port. Read from the registry rather
#: than hardcoded, so a renamed port cannot silently make this case untestable.
MULTI_INPUT_BLOCK: BlockDescriptor = next(
    d for d in DESCRIPTORS if len({p.name for p in d.inputs}) >= 2
)
SINGLE_OUTPUT_SOURCE: BlockDescriptor = next(d for d in DESCRIPTORS if d.outputs)


class TestPreFixHashRegression:
    """The two collisions the deleted `CompiledDAG.compute_hash` actually produced.

    It hashed node ids and `source->target` strings only. So a parameter edit was invisible
    to it - EMA(21) and EMA(200) were "the same strategy" - and so was moving a connection
    from one input port to another, because the node pair was unchanged. Both are asserted
    below: `pre_fix_dag_hash` collides, `compute_dag_hash` does not. The property tests
    above generalise these two cases; these two pin the defect itself.

    **Validates: Requirements 2.2**
    """

    def test_graphs_differing_only_in_params_are_distinguished(self):
        target_port = MULTI_INPUT_BLOCK.inputs[0].name
        left = _two_node_graph(
            {"length": 21},
            target_port,
            SINGLE_OUTPUT_SOURCE.block_id,
            MULTI_INPUT_BLOCK.block_id,
        )
        right = _two_node_graph(
            {"length": 200},
            target_port,
            SINGLE_OUTPUT_SOURCE.block_id,
            MULTI_INPUT_BLOCK.block_id,
        )

        # The two graphs are identical in everything the pre-fix hash looked at.
        assert [n.id for n in left.nodes] == [n.id for n in right.nodes]
        assert [(e.source, e.target) for e in left.edges] == [
            (e.source, e.target) for e in right.edges
        ]

        assert pre_fix_dag_hash(left) == pre_fix_dag_hash(right), (
            "reconstruction check: the pre-fix hash must be blind here, otherwise this "
            "test is not exercising the defect"
        )
        assert compute_dag_hash(left) != compute_dag_hash(right), (
            "a parameter edit must move the identity hash; the pre-fix hash treated "
            "EMA(21) and EMA(200) as the same strategy (Requirement 2.2)"
        )

    def test_graphs_differing_only_in_port_wiring_are_distinguished(self):
        first_port, second_port = (
            MULTI_INPUT_BLOCK.inputs[0].name,
            MULTI_INPUT_BLOCK.inputs[1].name,
        )
        assert first_port != second_port
        left = _two_node_graph(
            {"length": 21},
            first_port,
            SINGLE_OUTPUT_SOURCE.block_id,
            MULTI_INPUT_BLOCK.block_id,
        )
        right = _two_node_graph(
            {"length": 21},
            second_port,
            SINGLE_OUTPUT_SOURCE.block_id,
            MULTI_INPUT_BLOCK.block_id,
        )

        # Same node ids, same node pair on the edge - only the port moved.
        assert [n.id for n in left.nodes] == [n.id for n in right.nodes]
        assert [(e.source, e.target) for e in left.edges] == [
            (e.source, e.target) for e in right.edges
        ]

        assert pre_fix_dag_hash(left) == pre_fix_dag_hash(right), (
            "reconstruction check: the pre-fix hash must be blind to port identity"
        )
        assert compute_dag_hash(left) != compute_dag_hash(right), (
            f"moving a connection from '{first_port}' to '{second_port}' changes what the "
            "strategy computes and must move the identity hash (Requirement 2.2)"
        )

    def test_graphs_differing_only_in_block_id_are_distinguished(self):
        """The third field the pre-fix hash dropped: which block a node even is."""
        alternatives = [
            d.block_id
            for d in DESCRIPTORS_BY_CATEGORY[SINGLE_OUTPUT_SOURCE.category]
            if d.block_id != SINGLE_OUTPUT_SOURCE.block_id and d.outputs
        ]
        assert alternatives, (
            f"no sibling of '{SINGLE_OUTPUT_SOURCE.block_id}' in "
            f"{SINGLE_OUTPUT_SOURCE.category.value} to swap in"
        )
        target_port = MULTI_INPUT_BLOCK.inputs[0].name
        left = _two_node_graph(
            {}, target_port, SINGLE_OUTPUT_SOURCE.block_id, MULTI_INPUT_BLOCK.block_id
        )
        right = _two_node_graph(
            {}, target_port, alternatives[0], MULTI_INPUT_BLOCK.block_id
        )
        assert semantic_form(left) != semantic_form(right)

        assert compute_dag_hash(left) != compute_dag_hash(right)

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_the_pre_fix_hash_is_blind_to_every_param_mutation(self, graph, data):
        """The defect, as a property: no parameter edit ever moved the pre-fix hash.

        This is what makes the param-sensitivity assertions above load-bearing rather than
        decorative - the same generator and the same mutation, checked against a hash that
        genuinely fails Property 5.
        """
        outcome = mutate_param_value(graph, data)
        assume(outcome is not None)
        mutated = outcome[0]
        assert compute_dag_hash(graph) != compute_dag_hash(mutated)
        assert pre_fix_dag_hash(graph) == pre_fix_dag_hash(mutated)

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=2, min_edges=1), data=st.data())
    def test_the_pre_fix_hash_is_blind_to_every_port_rewiring(self, graph, data):
        """The same demonstration for port identity."""
        outcome = data.draw(st.sampled_from((mutate_source_port, mutate_target_port)))(
            graph, data
        )
        assume(outcome is not None)
        mutated = outcome[0]
        assert compute_dag_hash(graph) != compute_dag_hash(mutated)
        assert pre_fix_dag_hash(graph) == pre_fix_dag_hash(mutated)

    @PROPERTY_SETTINGS
    @given(graph=canonical_graphs(min_nodes=1), data=st.data())
    def test_the_pre_fix_hash_is_blind_to_every_category_change(self, graph, data):
        outcome = mutate_category(graph, data)
        assume(outcome is not None)
        mutated = outcome[0]
        assert compute_dag_hash(graph) != compute_dag_hash(mutated)
        assert pre_fix_dag_hash(graph) == pre_fix_dag_hash(mutated)


# ---------------------------------------------------------------------------
# 5. Collision pressure
# ---------------------------------------------------------------------------


class TestCollisionPressure:
    """Distinct canonical forms do not share a hash; identical ones always do.

    Property 5 is an injectivity claim, and the per-class tests only probe it one mutation
    at a time. This puts many unrelated graphs in one bucket and asserts the mapping from
    canonical form to hash is a function *and* injective over what was generated.

    **Validates: Requirements 2.2**
    """

    @settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        graphs=st.lists(
            canonical_graphs(min_nodes=1, max_nodes=3, max_edges=3),
            min_size=2,
            max_size=14,
        )
    )
    def test_distinct_canonical_forms_never_share_a_hash(self, graphs):
        by_hash: Dict[str, str] = {}
        for graph in graphs:
            form = semantic_form(graph)
            digest = compute_dag_hash(graph)
            previous = by_hash.setdefault(digest, form)
            assert previous == form, (
                "two graphs with different canonical forms share the identity hash "
                f"{digest}:\n{previous}\nvs\n{form}"
            )

    @settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        graphs=st.lists(
            canonical_graphs(min_nodes=1, max_nodes=3, max_edges=3),
            min_size=2,
            max_size=10,
        )
    )
    def test_equal_canonical_forms_always_share_a_hash(self, graphs):
        by_form: Dict[str, str] = {}
        for graph in graphs:
            form = semantic_form(graph)
            digest = compute_dag_hash(graph)
            previous = by_form.setdefault(form, digest)
            assert previous == digest, (
                "two graphs with the same canonical form produced different hashes "
                f"({previous} vs {digest}); identity would not be reproducible"
            )
