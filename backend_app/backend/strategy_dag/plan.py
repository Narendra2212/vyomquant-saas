"""
backend/strategy_dag/plan.py - the compiled execution artifact.

``CompiledPlan`` is what the compiler emits and what every consumer - the API response, the
persisted ``strategy_versions.compiled_plan`` column, ``dag_worker``, ``dag_event_loop``,
``dag_engine_parallel`` and the backtester - reads. It is data, not behaviour: no
validation, no topological sort and no compilation happens here. ``strategy_compiler.py``
remains the single compiler; this module owns the *shape* of its output and the one
warmup rule.

Exposes
-------
``CompiledPlan``        the execution artifact, with ``dag_hash`` as a readable FIELD and
                        exactly one serialization path, :meth:`CompiledPlan.to_dict`
``ModelRequirement``    one ML_DL node's data requirement, read from the registry
                        descriptor rather than restated
``compute_warmup``      bars of history that must be discarded, composed along the path
``compute_node_warmups`` the memoised per-node effective warmup map
``predecessor_map`` / ``inbound_map``  edge-derived views the plan and warmup rule need
``resolve_action_markets`` each ACTION node's traded symbol - and, where the closure gives
                        one answer, its timeframe - read off the DATA node in that action's
                        upstream closure (Requirement 12.5)
``COMPILER_VERSION``    stamped onto every plan

SB-02: ``dag_hash`` is a FIELD
------------------------------
The clone path used to call ``compiled.get("dag_hash")`` on a ``CompiledDAG`` *object* that
only had ``compute_hash()``. The resulting ``AttributeError`` was swallowed by a broad
``except Exception``, so every clone was persisted with no hash at all while the in-code
comment claimed the opposite. Three properties of this module make that state
unrepresentable:

1. ``dag_hash`` is a plain readable attribute. ``plan.dag_hash`` is the access pattern;
   there is no ``compute_hash()`` method and no recomputation step a caller can forget.
2. ``dag_hash`` is present in ``to_dict()``, which is the ONLY serialization path on the
   class. No second, ad-hoc dict is built anywhere.
3. A ``CompiledPlan`` cannot be constructed without a non-empty ``dag_hash``: the
   constructor raises :class:`PlanBuildError` instead of yielding a hashless plan.

The authoritative hash function is ``schema.compute_dag_hash``. This module calls it; it
does not define a second one.

An ACTION node's traded symbol is RESOLVED, never guessed (Requirement 12.5)
---------------------------------------------------------------------------
``block_specs`` forbids a traded-asset parameter on every ACTION descriptor, because a
saved strategy is exchange- and market-agnostic beyond its DATA nodes (SB-06). That
prohibition is only safe if something answers the question the parameter used to answer:
*which market does this action trade?* ``resolve_action_markets`` answers it from the
graph - the single DATA node in the action's upstream closure - and ``action_symbols``
carries the answer on the plan, per action, through ``to_dict`` and back.

Three properties, all of them financial-safety properties:

1. **Exactly one symbol, or no plan.** Zero DATA ancestors and two different upstream
   symbols are both :class:`PlanBuildError`s naming the action. There is no fallback
   literal, no "first DATA node wins" and no empty string: guessing which instrument to
   buy is the worst available failure mode.
2. **Per action, not per graph.** Two actions descending from two different feeds resolve
   to their own symbols. A graph-wide symbol list (which is what
   ``extract_resource_dependencies`` produces, and all any consumer had before) cannot say
   which of them an order belongs to.
3. **Not part of the identity hash.** ``schema.compute_dag_hash`` hashes the graph - nodes,
   params, wiring - and this map is *derived* from exactly that input, so it adds no
   discriminating power and would only risk the hash moving when the resolution rule
   changed rather than when the strategy did. See ``to_dict``'s note.

Warmup COMPOSES along a path
----------------------------
Each node's effective warmup is ``own_warmup + max(effective warmup of its predecessors)``.
An EMA(200) feeding a rolling-std(20) feeding a lag(3) needs 200 + 20 + 3 bars before its
last value is real. A bare ``max()`` over the nodes would answer 200 and ship
NaN-contaminated features into a model - a bug that looks like a working strategy in a
backtest and loses money live. Per-node results are memoised, so a diamond-shaped graph
evaluates a shared upstream subtree once rather than once per path.

Per-block own warmup comes from ``registry.BlockDescriptor.warmup(params)``, which applies
the block's declared ``warmup_fn`` to its resolved params. No warmup formula is restated
here.

Purity
------
Importing this module pulls in the standard library and ``strategy_dag.schema`` only. The
registry is reached through ``registry.get_registry()`` *inside* the functions that need
it, so the compiler/validator import path stays light: no FastAPI, no database handle, no
CCXT (Requirement 21.10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from backend_app.backend.strategy_dag.schema import (
    CURRENT_SCHEMA_VERSION,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
    compute_dag_hash,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from backend_app.backend.strategy_dag.registry import BlockDescriptor

#: Stamped onto every plan. Bumped when the compiled artifact's *meaning* changes, so a
#: persisted plan can be recognised as produced by an older compiler. 2.1.0 adds
#: ``action_symbols`` / ``action_timeframes``: a plan stamped 2.0.0 carries no resolved
#: traded symbol for its actions (Requirement 12.5).
COMPILER_VERSION = "2.1.0"

__all__ = [
    "COMPILER_VERSION",
    "PlanBuildError",
    "UnresolvedBlockError",
    "GraphCycleError",
    "ActionSymbolUnresolvedError",
    "AmbiguousActionSymbolError",
    "ModelRequirement",
    "CompiledPlan",
    "predecessor_map",
    "successor_map",
    "inbound_map",
    "compute_node_warmups",
    "compute_warmup",
    "plan_node_warmups",
    "resolve_action_markets",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PlanBuildError(ValueError):
    """Raised when a compiled plan cannot be built from what the caller supplied.

    A ``ValueError`` on purpose: every one of these is a caller contract breach, not an
    environmental failure, and none of them may be downgraded to a warning (SB-02).
    """

    code = "PLAN_BUILD_FAILED"


class UnresolvedBlockError(PlanBuildError):
    """A node's ``block_id`` is absent from the registry, so its warmup is unknowable."""

    code = "UNRESOLVED_BLOCK"

    def __init__(self, node_id: str, block_id: str):
        self.node_id = node_id
        self.block_id = block_id
        super().__init__(
            f"Node {node_id!r} references block {block_id!r}, which the registry does not "
            "publish; its warmup cannot be computed"
        )


class GraphCycleError(PlanBuildError):
    """The graph contains a cycle, so no node has a finite composed warmup."""

    code = "GRAPH_HAS_CYCLE"

    def __init__(self, node_ids: Sequence[str]):
        self.node_ids = tuple(node_ids)
        super().__init__(
            "Graph contains a cycle through: " + " -> ".join(self.node_ids)
        )


class ActionSymbolUnresolvedError(PlanBuildError):
    """An ACTION node's upstream closure holds no DATA node declaring a symbol.

    Unreachable for a validated graph - the validator's stage 7 rejects a node on no path,
    its stage 8 rejects a graph whose actions are not reachable from a DATA node, and its
    stage 3 rejects a DATA node with no ``symbol`` (Requirements 12.3, 12.4). Raised rather
    than defaulted anyway: an action whose market is unknown must stop the compile, because
    the alternative is an order against a guessed instrument.
    """

    code = "ACTION_SYMBOL_UNRESOLVED"

    def __init__(self, action_id: str, data_ancestors: Sequence[str] = ()):
        self.action_id = action_id
        self.data_ancestors = tuple(data_ancestors)
        detail = (
            "its upstream closure holds no Market Data block at all"
            if not self.data_ancestors
            else (
                "the Market Data blocks upstream of it ("
                + ", ".join(self.data_ancestors)
                + ") declare no symbol"
            )
        )
        super().__init__(
            f"Action {action_id!r} has no resolvable traded symbol: {detail}. An action "
            "carries no traded-asset parameter by design (Requirement 12.8), so its "
            "market comes from the Market Data block it descends from and cannot be "
            "defaulted (Requirement 12.5)."
        )


class AmbiguousActionSymbolError(PlanBuildError):
    """An ACTION node's upstream closure reaches more than one traded symbol.

    Unreachable for a validated graph: the validator's stage 9 already rejects it with
    ``MULTI_SYMBOL_ACTION_PATH``. Raised rather than resolved by picking one, including
    when a portfolio-allocation block is present in the closure - stage 9 exempts that
    case, and until per-action multi-symbol resolution is designed the compiler must refuse
    the graph instead of choosing an instrument on the author's behalf.
    """

    code = "AMBIGUOUS_ACTION_SYMBOL"

    def __init__(self, action_id: str, symbols: Sequence[str]):
        self.action_id = action_id
        self.symbols = tuple(symbols)
        super().__init__(
            f"Action {action_id!r} descends from more than one traded symbol "
            f"({', '.join(self.symbols)}); no single market can be resolved for it "
            "(Requirement 12.5). Split it into one action per symbol."
        )


# ---------------------------------------------------------------------------
# Edge-derived views
# ---------------------------------------------------------------------------


def _sorted_edges(graph: StrategyGraph) -> List[EdgeSpec]:
    """Every edge in one total order, so every derived view is deterministic."""
    return sorted(
        graph.edges,
        key=lambda edge: (edge.source, edge.source_port, edge.target, edge.target_port),
    )


def predecessor_map(graph: StrategyGraph) -> Dict[str, Tuple[str, ...]]:
    """``node_id`` -> its distinct upstream node ids, sorted.

    Every node of the graph is a key, including source nodes (empty tuple), so a caller
    never has to distinguish "no predecessors" from "unknown node".
    """
    collected: Dict[str, List[str]] = {node.id: [] for node in graph.nodes}
    for edge in _sorted_edges(graph):
        bucket = collected.setdefault(edge.target, [])
        if edge.source not in bucket:
            bucket.append(edge.source)
    return {node_id: tuple(sorted(sources)) for node_id, sources in collected.items()}


def successor_map(graph: StrategyGraph) -> Dict[str, Tuple[str, ...]]:
    """``node_id`` -> its distinct downstream node ids, sorted."""
    collected: Dict[str, List[str]] = {node.id: [] for node in graph.nodes}
    for edge in _sorted_edges(graph):
        bucket = collected.setdefault(edge.source, [])
        if edge.target not in bucket:
            bucket.append(edge.target)
    return {node_id: tuple(sorted(targets)) for node_id, targets in collected.items()}


def inbound_map(graph: StrategyGraph) -> Dict[str, Dict[str, Tuple[EdgeSpec, ...]]]:
    """``node_id`` -> ``target_port`` -> **every** edge feeding that port, in order.

    The value is a tuple, not a single edge, because a **variadic** input port legitimately
    holds 2..N connections: ``feat_concat``, ``and``, ``or``, ``add``, ``multiply``, ``min``,
    ``max`` and ``between`` all declare one. The validator's rule R7 rejects a second edge
    only on a *non*-variadic port, precisely so those blocks can be fed more than once, so
    "one edge per port" is not an invariant the graph guarantees and must not be one the plan
    assumes.

    This is ``design.md``'s STRUCTURE line - ``inbound: Map<String, List<EdgeSpec>>`` -
    implemented as typed. An earlier revision followed the inline comment in the same
    section instead ("target_port -> resolved edge") and stored one edge per port with
    last-write-wins. That silently discarded every operand but one on a variadic port: an
    ``add`` returned one addend and an ``and`` returned one condition, with no error
    anywhere. The typed STRUCTURE line is authoritative, and it is what correctness requires;
    the single-edge shape was the same silent-wrong-answer class as SB-05's dropped nodes.

    Postconditions
        Every node of ``graph`` is a key, so a caller never distinguishes "no inbound edges"
        from "unknown node". Within a port the edges are in ``_sorted_edges`` total order, so
        the operand order a consumer sees is a function of the graph alone - identical across
        processes and across a JSON round trip.
    """
    collected: Dict[str, Dict[str, List[EdgeSpec]]] = {node.id: {} for node in graph.nodes}
    for edge in _sorted_edges(graph):
        collected.setdefault(edge.target, {}).setdefault(edge.target_port, []).append(edge)
    return {
        node_id: {port: tuple(edges) for port, edges in ports.items()}
        for node_id, ports in collected.items()
    }


# ---------------------------------------------------------------------------
# Warmup composition
# ---------------------------------------------------------------------------


def _resolve_registry(registry: Any = None) -> Any:
    """The supplied registry, or the memoised default one.

    The default is fetched *inside* the call, never at module import: assembling the
    registry reads ``exchange_executor.OrderType`` and would drag CCXT into every process
    that only wanted the plan shape.
    """
    if registry is not None:
        return registry
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.get_registry()


def _own_warmup(node: NodeSpec, registry: Any) -> int:
    """One node's own warmup, from its registry descriptor.

    The formula belongs to the block, not to the compiler: ``BlockDescriptor.warmup``
    applies the declared ``warmup_fn`` to the node's params overlaid on the declared
    defaults. Nothing about EMA windows or lag depths is restated here.
    """
    descriptor: Optional["BlockDescriptor"] = registry.get(node.block_id)
    if descriptor is None:
        raise UnresolvedBlockError(node.id, node.block_id)
    try:
        own = descriptor.warmup(node.params)
    except PlanBuildError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure names the offending node
        raise PlanBuildError(
            f"Node {node.id!r} (block {node.block_id!r}) could not report its warmup: {exc}"
        ) from exc
    # A negative warmup is not a shorter history requirement, it is a broken declaration.
    return max(0, int(own))


def _effective_warmups(
    nodes: Mapping[str, NodeSpec],
    predecessors: Mapping[str, Sequence[str]],
    resolved_registry: Any,
) -> Dict[str, int]:
    """The warmup walk itself, over a node index and a predecessor map.

    Extracted verbatim from :func:`compute_node_warmups` (task 8.4) so the compiled plan
    can be asked the same question the graph is asked, through the *same* walk. The runtime
    readiness gate needs a per-node warmup and only ever holds a ``CompiledPlan``; giving it
    a second composition rule is how the engine and the compiler would come to disagree
    about which bar a node becomes trustworthy on - and Requirement 20.1 is decided by that
    number. See :func:`plan_node_warmups`.
    """
    memo: Dict[str, int] = {}
    path: List[str] = []          # the current walk, for a named cycle
    on_path: set = set()
    for start in sorted(nodes):
        if start in memo:
            continue
        # (node_id, expanded?) - the second visit finalises the node.
        stack: List[Tuple[str, bool]] = [(start, False)]
        while stack:
            node_id, expanded = stack.pop()
            if expanded:
                if node_id in on_path:
                    on_path.discard(node_id)
                    path.remove(node_id)
                if node_id in memo:
                    continue
                upstream = 0
                for pred in predecessors.get(node_id, ()):  # already finalised
                    if pred in memo and memo[pred] > upstream:
                        upstream = memo[pred]
                memo[node_id] = _own_warmup(nodes[node_id], resolved_registry) + upstream
                continue
            if node_id in memo:
                continue
            if node_id in on_path:
                raise GraphCycleError(path[path.index(node_id):] + [node_id])
            on_path.add(node_id)
            path.append(node_id)
            stack.append((node_id, True))
            for pred in predecessors.get(node_id, ()):
                if pred in memo:
                    continue
                if pred not in nodes:
                    # Structural validation resolves every edge endpoint before a plan is
                    # built; an unresolvable one contributes nothing rather than crashing.
                    continue
                stack.append((pred, False))
    return memo


def compute_node_warmups(
    graph: StrategyGraph, registry: Any = None
) -> Dict[str, int]:
    """Every node's *effective* warmup: ``own + max(upstream effective)``.

    Preconditions
        ``graph`` is acyclic (guaranteed by the validator's cycle stage running first) and
        every ``block_id`` resolves against ``registry``.

    Postconditions
        The returned map has one entry per node of ``graph``. A node with no predecessors
        maps to its own warmup. Every other node maps to its own warmup plus the largest
        effective warmup among its predecessors - the bars that must pass before *this*
        node's value is trustworthy.

    Loop invariants
        Evaluation is an explicit post-order walk, so when a node is finalised every one of
        its predecessors is already in ``memo``; once written, ``memo[x]`` is final, and
        each node's ``warmup_fn`` is invoked exactly once no matter how many paths run
        through it. That is what keeps a diamond-shaped graph linear instead of
        exponential.

    Raises
        :class:`UnresolvedBlockError` naming the node whose block the registry does not
        publish; :class:`GraphCycleError` naming the cycle, so a malformed graph fails
        loudly instead of recursing forever.
    """
    return _effective_warmups(
        graph.node_index(), predecessor_map(graph), _resolve_registry(registry)
    )


def plan_node_warmups(plan: "CompiledPlan", registry: Any = None) -> Dict[str, int]:
    """Every node's effective warmup, asked of a compiled plan rather than a graph.

    Same walk, same composition rule, same descriptors as :func:`compute_node_warmups` -
    only the two inputs come from the plan's own ``node_index`` and ``dependencies``
    (which *is* ``predecessor_map`` of the graph it was compiled from) instead of from a
    live ``StrategyGraph``. That matters because the runtime never has the graph: a
    deployment executes the persisted ``compiled_plan``, and the readiness gate has to
    decide "warming or ready" per node from it (Requirements 20.1, 20.10).

    Postconditions
        One entry per node of ``plan.node_index``. ``max(result[a] for a in
        plan.action_nodes) == plan.warmup_bars`` for a plan whose ``warmup_bars`` this
        compiler computed, because both are the same numbers reduced the same way.

    Raises
        :class:`UnresolvedBlockError` naming a node whose block the registry does not
        publish. A plan naming an unpublished block cannot be executed at all -
        ``plan_to_engine_graph`` refuses it first - so raising here is the same refusal one
        step earlier, never a substituted warmup.
    """
    return _effective_warmups(
        plan.node_index, plan.dependencies, _resolve_registry(registry)
    )


def compute_warmup(
    graph: StrategyGraph,
    registry: Any = None,
    *,
    node_ids: Optional[Iterable[str]] = None,
) -> int:
    """Bars of leading history that must be discarded before the strategy is trustworthy.

    The graph-level answer is the largest effective warmup over the terminal nodes: the
    ACTION nodes, because they are what the strategy exists to produce. ``node_ids``
    overrides that choice (the validator's warmup-feasibility stage asks about specific
    nodes).

    Postconditions
        ``bars >= 0``, and ``bars`` is the minimum leading bars to discard before any node
        on any action path produces a real value. Composition, not a bare maximum: see the
        module docstring.

    Extension beyond ``design.md``, which names ACTION nodes only: when the graph declares
    no ACTION node - a work-in-progress canvas, which the validator will reject on its own
    terms - the sink nodes are used instead of silently returning 0. An understated warmup
    is the failure mode this whole function exists to prevent.
    """
    warmups = compute_node_warmups(graph, registry)
    if not warmups:
        return 0

    if node_ids is not None:
        selected = [node_id for node_id in node_ids if node_id in warmups]
    else:
        selected = [
            node.id for node in graph.nodes if node.category is BlockCategory.ACTION
        ]
        if not selected:
            successors = successor_map(graph)
            selected = [
                node.id for node in graph.nodes if not successors.get(node.id)
            ]
        if not selected:  # pragma: no cover - only reachable for a cyclic graph
            selected = list(warmups)

    return max((warmups[node_id] for node_id in selected), default=0)


# ---------------------------------------------------------------------------
# Action market resolution (Requirement 12.5)
# ---------------------------------------------------------------------------


def _upstream_data_nodes(
    node_index: Mapping[str, NodeSpec],
    predecessors: Mapping[str, Sequence[str]],
    root: str,
) -> List[NodeSpec]:
    """The DATA nodes in ``root``'s upstream closure, in a deterministic order.

    Iterative, like every other walk in this module: a strategy may be 200 blocks in one
    chain (the published node limit is higher still), and a recursive walk would raise
    ``RecursionError`` on a graph the author is allowed to draw.

    Loop invariant
        ``seen`` holds exactly the nodes already popped, so each node is expanded once and
        the walk terminates on any input, cyclic or not. A cycle is the validator's stage 6
        to report; this function must not hang on one.
    """
    collected: List[NodeSpec] = []
    seen: set = set()
    stack: List[str] = [root]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        node = node_index.get(current)
        if node is not None and node.category is BlockCategory.DATA:
            collected.append(node)
        stack.extend(
            source for source in predecessors.get(current, ()) if source not in seen
        )
    collected.sort(key=lambda item: item.id)
    return collected


def resolve_action_markets(
    graph: StrategyGraph,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """``(action_symbols, action_timeframes)``: the market each ACTION node trades.

    Requirement 12.5. An ACTION descriptor carries no traded-asset parameter
    (Requirement 12.8), so the market an order belongs to is a property of the DATA node
    that action descends from, and this is the one place that reads it.

    Preconditions
        ``graph`` is the canonical, validated graph (the server's own, per
        Requirement 6.13). Node categories are read from the graph rather than re-derived
        from the registry, so no descriptor lookup and no registry assembly is needed.

    Postconditions
        ``action_symbols`` has one non-empty entry for **every** ACTION node of ``graph``,
        or the call raised. ``action_timeframes`` has an entry only for the actions whose
        upstream DATA nodes agree on exactly one timeframe; see below.

    Raises
        :class:`ActionSymbolUnresolvedError` naming the action whose closure declares no
        symbol, and :class:`AmbiguousActionSymbolError` naming the action whose closure
        declares two. Neither is downgraded to a default, and neither picks the first
        candidate.

    Why the timeframe map is allowed to be incomplete while the symbol map is not
    ------------------------------------------------------------------------------
    An order needs a market; it does not need a bar cadence. And the two are not equally
    available: ``ohlcv_feed`` declares ``symbol`` *and* ``timeframe``, but ``live_ticker``
    and ``orderbook_imbalance`` declare a symbol and no timeframe at all, because a ticker
    is not bar-aligned. Meanwhile one symbol at two timeframes - a 5m entry filtered by a
    1h trend - is a legitimate strategy that stage 9 permits, so a single timeframe simply
    does not exist for such an action. Absence therefore means "the closure gives no single
    answer", and a consumer must treat it as unknown rather than substitute one; erroring
    instead would reject strategies the platform is meant to support, and defaulting would
    be the ``"1m"`` equivalent of the ``"BTC/USDT"`` this whole requirement exists to
    delete.
    """
    node_index = graph.node_index()
    predecessors = predecessor_map(graph)

    symbols: Dict[str, str] = {}
    timeframes: Dict[str, str] = {}

    for action_id in sorted(
        node.id for node in graph.nodes if node.category is BlockCategory.ACTION
    ):
        found_symbols: List[str] = []
        found_timeframes: List[str] = []
        ancestors = _upstream_data_nodes(node_index, predecessors, action_id)
        for data_node in ancestors:
            symbol = data_node.params.get("symbol")
            if isinstance(symbol, str) and symbol.strip():
                value = symbol.strip()
                if value not in found_symbols:
                    found_symbols.append(value)
            timeframe = data_node.params.get("timeframe")
            if isinstance(timeframe, str) and timeframe.strip():
                frame = timeframe.strip()
                if frame not in found_timeframes:
                    found_timeframes.append(frame)

        if not found_symbols:
            raise ActionSymbolUnresolvedError(
                action_id, [node.id for node in ancestors]
            )
        if len(found_symbols) > 1:
            raise AmbiguousActionSymbolError(action_id, sorted(found_symbols))

        symbols[action_id] = found_symbols[0]
        if len(found_timeframes) == 1:
            timeframes[action_id] = found_timeframes[0]

    return symbols, timeframes


# ---------------------------------------------------------------------------
# Model requirements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRequirement:
    """What one ML_DL node needs before it can be trained or can predict.

    Every figure is read from the registry descriptor's ``metadata["model"]`` - which
    ``registry.descriptor_from_model_spec`` carries through verbatim from
    ``ml_models.ModelSpec`` - so the minimum-data gate and this plan quote the same numbers.
    """

    node_id: str
    block_id: str
    model_family: str = ""
    min_feature_columns: Optional[int] = None
    min_training_rows: Optional[int] = None
    sequence_length: Optional[int] = None
    params: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_node(
        cls, node: NodeSpec, descriptor: Optional["BlockDescriptor"] = None
    ) -> "ModelRequirement":
        """Build the requirement for ``node`` from its descriptor, when there is one."""
        model: Mapping[str, Any] = {}
        if descriptor is not None:
            raw = (descriptor.metadata or {}).get("model")
            if isinstance(raw, Mapping):
                model = raw
        return cls(
            node_id=node.id,
            block_id=node.block_id,
            model_family=str(model.get("model_family") or ""),
            min_feature_columns=_as_optional_int(model.get("min_feature_columns")),
            min_training_rows=_as_optional_int(model.get("min_training_rows")),
            sequence_length=_as_optional_int(model.get("sequence_length")),
            params=dict(node.params),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "block_id": self.block_id,
            "model_family": self.model_family,
            "min_feature_columns": self.min_feature_columns,
            "min_training_rows": self.min_training_rows,
            "sequence_length": self.sequence_length,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ModelRequirement":
        if not isinstance(data, Mapping):
            raise PlanBuildError(
                f"Model requirement must be an object, got {type(data).__name__}"
            )
        node_id = data.get("node_id")
        block_id = data.get("block_id")
        if not isinstance(node_id, str) or not node_id:
            raise PlanBuildError("Model requirement is missing a non-empty 'node_id'")
        if not isinstance(block_id, str) or not block_id:
            raise PlanBuildError("Model requirement is missing a non-empty 'block_id'")
        params = data.get("params") or {}
        if not isinstance(params, Mapping):
            raise PlanBuildError(
                f"Model requirement {node_id!r} field 'params' must be an object"
            )
        return cls(
            node_id=node_id,
            block_id=block_id,
            model_family=str(data.get("model_family") or ""),
            min_feature_columns=_as_optional_int(data.get("min_feature_columns")),
            min_training_rows=_as_optional_int(data.get("min_training_rows")),
            sequence_length=_as_optional_int(data.get("sequence_length")),
            params=dict(params),
        )


def _as_optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# CompiledPlan
# ---------------------------------------------------------------------------


def _as_id_tuple(value: Any, label: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise PlanBuildError(f"CompiledPlan field {label!r} must be a sequence of ids")
    return tuple(str(item) for item in value)


def _as_inbound_map(
    value: Any, label: str = "inbound"
) -> Dict[str, Dict[str, Tuple[EdgeSpec, ...]]]:
    """``{node_id: {target_port: (edge, ...)}}``, accepting the old single-edge shape too.

    Backward compatibility, deliberately narrow. A ``compiled_plan`` row persisted before
    this fix stores ``inbound[node][port]`` as ONE edge object; a row written after it stores
    a list. Both are read here, the single form becoming a one-element tuple, so an existing
    deployment keeps loading its stored artifact instead of failing on a shape change. A
    value that is neither an edge nor a sequence of edges is **rejected**, never skipped: an
    inbound entry that cannot be read is missing wiring, and dropping it silently is the
    defect this whole change removes.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PlanBuildError(f"CompiledPlan field {label!r} must be an object")

    resolved: Dict[str, Dict[str, Tuple[EdgeSpec, ...]]] = {}
    for node_id, ports in value.items():
        if ports is None:
            resolved[str(node_id)] = {}
            continue
        if not isinstance(ports, Mapping):
            raise PlanBuildError(
                f"CompiledPlan field {label!r} entry for {str(node_id)!r} must be an object "
                f"of target_port -> edges, got {type(ports).__name__}"
            )
        by_port: Dict[str, Tuple[EdgeSpec, ...]] = {}
        for port, raw in ports.items():
            by_port[str(port)] = _as_edge_tuple(raw, label, str(node_id), str(port))
        resolved[str(node_id)] = by_port
    return resolved


def _as_edge_tuple(
    raw: Any, label: str, node_id: str, port: str
) -> Tuple[EdgeSpec, ...]:
    """One port's edges as a tuple, from an ``EdgeSpec``, a mapping, or a sequence of either."""
    if raw is None:
        return ()
    if isinstance(raw, EdgeSpec):
        return (raw,)
    if isinstance(raw, Mapping):
        # The pre-fix persisted form: a single serialized edge object on the port.
        return (EdgeSpec.from_dict(raw),)
    if isinstance(raw, (str, bytes)):
        raise PlanBuildError(
            f"CompiledPlan field {label!r} entry {node_id!r}.{port!r} must be an edge or a "
            f"sequence of edges, got {type(raw).__name__}"
        )
    try:
        items = list(raw)
    except TypeError as exc:
        raise PlanBuildError(
            f"CompiledPlan field {label!r} entry {node_id!r}.{port!r} must be an edge or a "
            f"sequence of edges, got {type(raw).__name__}"
        ) from exc
    edges: List[EdgeSpec] = []
    for item in items:
        if isinstance(item, EdgeSpec):
            edges.append(item)
        elif isinstance(item, Mapping):
            edges.append(EdgeSpec.from_dict(item))
        else:
            raise PlanBuildError(
                f"CompiledPlan field {label!r} entry {node_id!r}.{port!r} holds a value that "
                f"is not an edge: {item!r}"
            )
    return tuple(edges)


def _as_text_map(value: Any, label: str) -> Dict[str, str]:
    """``{node_id: non-empty text}``, or a :class:`PlanBuildError`.

    An empty string is rejected rather than stored: a blank traded symbol on a plan is the
    same defect as a missing one, and it must not be able to reach a consumer that would
    then read it as "any market".
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PlanBuildError(f"CompiledPlan field {label!r} must be an object")
    resolved: Dict[str, str] = {}
    for node_id, item in value.items():
        if not isinstance(item, str) or not item.strip():
            raise PlanBuildError(
                f"CompiledPlan field {label!r} entry for {str(node_id)!r} must be a "
                f"non-empty string, got {item!r}"
            )
        resolved[str(node_id)] = item.strip()
    return resolved


@dataclass(frozen=True)
class CompiledPlan:
    """The compiled execution artifact: what the compiler emits and everyone else reads.

    Frozen on purpose. A compiled plan belongs to an immutable ``strategy_versions`` row;
    a caller that wants a different plan recompiles rather than edits, so a running
    deployment cannot have its plan changed underneath it.

    ``dag_hash`` is a readable field and it is never optional (SB-02, Requirement 2.4).
    ``to_dict`` is the only serialization path (Requirement 2.5); ``to_json`` delegates to
    it and nothing else in this class builds a dict.
    """

    dag_hash: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    compiler_version: str = COMPILER_VERSION
    execution_order: Tuple[str, ...] = ()
    execution_levels: Tuple[Tuple[str, ...], ...] = ()
    node_index: Mapping[str, NodeSpec] = field(default_factory=dict)
    #: ``node_id -> target_port -> every edge feeding that port``. A tuple per port, because
    #: a variadic port holds 2..N connections; see :func:`inbound_map`.
    inbound: Mapping[str, Mapping[str, Tuple[EdgeSpec, ...]]] = field(default_factory=dict)
    data_nodes: Tuple[str, ...] = ()
    action_nodes: Tuple[str, ...] = ()
    ml_nodes: Tuple[str, ...] = ()
    feature_pipeline: Tuple[str, ...] = ()
    warmup_bars: int = 0
    required_models: Tuple[ModelRequirement, ...] = ()
    dependencies: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    #: ``action_node_id -> the symbol that action trades``, resolved from the DATA node in
    #: its upstream closure (Requirement 12.5). Complete for every plan this compiler
    #: builds - the compiler asserts it as a postcondition - and empty for a plan read back
    #: from a row written by compiler 2.0.0, which did not resolve it.
    action_symbols: Mapping[str, str] = field(default_factory=dict)
    #: ``action_node_id -> the timeframe that action's market is read at``, present only
    #: where the upstream closure gives exactly one answer. Deliberately partial; see
    #: :func:`resolve_action_markets`.
    action_timeframes: Mapping[str, str] = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    def __post_init__(self) -> None:
        """Normalise the sequence and mapping fields, and refuse a hashless plan.

        A plan with no ``dag_hash`` is exactly the state SB-02 shipped for every clone, so
        it is rejected at construction rather than detected later by a database
        constraint - by which point a caller has already been told the clone succeeded.
        """
        if not isinstance(self.dag_hash, str) or not self.dag_hash.strip():
            raise PlanBuildError(
                "CompiledPlan requires a non-empty dag_hash; a plan without an identity "
                "hash can never be deployed (SB-02)"
            )

        set_field = object.__setattr__
        set_field(self, "dag_hash", self.dag_hash.strip())
        set_field(self, "schema_version", int(self.schema_version))
        set_field(self, "compiler_version", str(self.compiler_version))
        set_field(self, "execution_order", _as_id_tuple(self.execution_order, "execution_order"))
        set_field(
            self,
            "execution_levels",
            tuple(_as_id_tuple(level, "execution_levels") for level in self.execution_levels or ()),
        )
        set_field(self, "node_index", dict(self.node_index or {}))
        set_field(self, "inbound", _as_inbound_map(self.inbound))
        set_field(self, "data_nodes", _as_id_tuple(self.data_nodes, "data_nodes"))
        set_field(self, "action_nodes", _as_id_tuple(self.action_nodes, "action_nodes"))
        set_field(self, "ml_nodes", _as_id_tuple(self.ml_nodes, "ml_nodes"))
        set_field(
            self, "feature_pipeline", _as_id_tuple(self.feature_pipeline, "feature_pipeline")
        )
        set_field(self, "warmup_bars", max(0, int(self.warmup_bars)))
        set_field(self, "required_models", tuple(self.required_models or ()))
        set_field(
            self, "action_symbols", _as_text_map(self.action_symbols, "action_symbols")
        )
        set_field(
            self,
            "action_timeframes",
            _as_text_map(self.action_timeframes, "action_timeframes"),
        )
        set_field(
            self,
            "dependencies",
            {
                node_id: _as_id_tuple(sources, "dependencies")
                for node_id, sources in (self.dependencies or {}).items()
            },
        )

    @classmethod
    def from_graph(
        cls,
        graph: StrategyGraph,
        execution_order: Sequence[str],
        execution_levels: Optional[Sequence[Sequence[str]]] = None,
        *,
        registry: Any = None,
        compiler_version: str = COMPILER_VERSION,
        warmup_bars: Optional[int] = None,
    ) -> "CompiledPlan":
        """Assemble a plan for an already-ordered, already-validated graph.

        This is plan *construction*, not compilation: it runs no validation stage and no
        topological sort. ``execution_order`` is supplied by the compiler, which owns Kahn
        with its deterministic tie-break; every other field is derived from the graph so no
        caller has to remember to fill one in (that forgetting is how SB-02 happened).

        Preconditions
            ``graph`` passed validation; ``execution_order`` is a topological order of
            ``graph`` containing every node exactly once.

        Postconditions
            ``plan.dag_hash == compute_dag_hash(graph)`` and every node of the graph appears
            exactly once in ``plan.execution_order``.
        """
        node_index = graph.node_index()
        order = tuple(str(node_id) for node_id in execution_order)
        if len(order) != len(set(order)) or set(order) != set(node_index):
            raise PlanBuildError(
                "execution_order must contain every node of the graph exactly once: "
                f"got {len(order)} ids for {len(node_index)} nodes"
            )

        dependencies = predecessor_map(graph)

        if execution_levels is None:
            levels = _levels_from_order(order, dependencies)
        else:
            levels = tuple(
                tuple(str(node_id) for node_id in level) for level in execution_levels
            )

        def ids_in(category: BlockCategory) -> Tuple[str, ...]:
            return tuple(
                node_id
                for node_id in order
                if node_index[node_id].category is category
            )

        # Requirement 12.5: one symbol per ACTION node, read off the DATA node in its own
        # upstream closure. Raises rather than defaulting, so a plan cannot exist with an
        # action whose market is unknown.
        action_symbols, action_timeframes = resolve_action_markets(graph)

        resolved_registry = _resolve_registry(registry)
        ml_nodes = ids_in(BlockCategory.ML_DL)
        required_models = tuple(
            ModelRequirement.from_node(
                node_index[node_id], resolved_registry.get(node_index[node_id].block_id)
            )
            for node_id in ml_nodes
        )

        return cls(
            dag_hash=compute_dag_hash(graph),
            schema_version=graph.schema_version,
            compiler_version=compiler_version,
            execution_order=order,
            execution_levels=levels,
            node_index=node_index,
            inbound=inbound_map(graph),
            data_nodes=ids_in(BlockCategory.DATA),
            action_nodes=ids_in(BlockCategory.ACTION),
            ml_nodes=ml_nodes,
            feature_pipeline=ids_in(BlockCategory.FEATURE_ENGINEERING),
            warmup_bars=(
                compute_warmup(graph, resolved_registry)
                if warmup_bars is None
                else warmup_bars
            ),
            required_models=required_models,
            dependencies=dependencies,
            action_symbols=action_symbols,
            action_timeframes=action_timeframes,
        )

    # -- reads -------------------------------------------------------------
    def node(self, node_id: str) -> Optional[NodeSpec]:
        """The node with this id, or ``None``."""
        return self.node_index.get(node_id)

    def inbound_edges(self, node_id: str, target_port: str) -> Tuple[EdgeSpec, ...]:
        """**Every** edge feeding ``node_id``'s ``target_port``, in deterministic order.

        The accessor to reach for. A variadic port holds 2..N edges, so this is the only
        read that cannot lose an operand, and it answers a single-fed port as a one-element
        tuple rather than making the caller handle two shapes.
        """
        return tuple((self.inbound.get(node_id) or {}).get(target_port) or ())

    def inbound_edge(self, node_id: str, target_port: str) -> Optional[EdgeSpec]:
        """The **sole** edge feeding ``node_id``'s ``target_port``, or ``None`` when unfed.

        Deliberately strict: a port carrying more than one edge raises
        :class:`PlanBuildError` rather than returning one of them. Returning "an" edge from a
        multiply-fed port is exactly the defect this shape change removes - the caller would
        get a plausible answer computed from a subset of the author's operands, with nothing
        raised anywhere. A caller that may encounter a variadic port uses
        :meth:`inbound_edges`; this one stays available for the single-arity ports that are
        the majority, where it keeps the read honest by construction.
        """
        edges = self.inbound_edges(node_id, target_port)
        if not edges:
            return None
        if len(edges) > 1:
            raise PlanBuildError(
                f"Port {node_id!r}.{target_port!r} is fed by {len(edges)} edges "
                f"({', '.join(edge.id for edge in edges)}); inbound_edge returns a single "
                "edge and must not pick one of several. Use inbound_edges() for a variadic "
                "port."
            )
        return edges[0]

    def predecessors(self, node_id: str) -> Tuple[str, ...]:
        """The upstream node ids of ``node_id``, sorted."""
        return tuple(self.dependencies.get(node_id, ()))

    def action_symbol(self, action_id: str) -> Optional[str]:
        """The market ``action_id`` trades, or ``None`` when this plan does not say.

        ``None`` happens for one reason only: the plan was written by compiler 2.0.0, which
        resolved nothing. A caller that is about to place an order must treat ``None`` as
        "recompile before executing", never as "any market".
        """
        return self.action_symbols.get(action_id)

    def action_timeframe(self, action_id: str) -> Optional[str]:
        """The timeframe ``action_id``'s market is read at, or ``None``.

        ``None`` is normal, not an error: a ticker-fed action has no bar cadence and a
        multi-timeframe action has no single one. See :func:`resolve_action_markets`.
        """
        return self.action_timeframes.get(action_id)

    def matches_graph(self, graph: StrategyGraph) -> bool:
        """True when this plan was compiled from a graph with this identity.

        The backtester and the event loop use this to decide whether a persisted plan may
        be executed as-is or must be recompiled first.
        """
        return self.dag_hash == compute_dag_hash(graph)

    # -- serialization: exactly one path ----------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """The one serialization path (Requirement 2.5).

        JSON-safe, so it is simultaneously the API response body, the
        ``strategy_versions.compiled_plan`` column and the payload ``dag_worker`` receives.
        ``dag_hash`` is always present (SB-02).

        ``action_symbols`` is here because a persisted plan has to carry it: the row is what
        a deployment executes, and re-deriving the traded market at execution time would put
        the resolution rule in every consumer. It is deliberately **not** part of
        ``dag_hash``: that hash is computed by ``schema.compute_dag_hash`` over the graph's
        nodes, params and wiring, which is precisely the input this map is derived from, so
        including it could not distinguish two graphs the hash already separates and would
        instead make the identity of a *stored* strategy sensitive to a change in the
        compiler's resolution code. The hash tracks the strategy; this tracks the compile.
        """
        return {
            "dag_hash": self.dag_hash,
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "execution_order": list(self.execution_order),
            "execution_levels": [list(level) for level in self.execution_levels],
            "node_index": {
                node_id: node.to_dict() for node_id, node in self.node_index.items()
            },
            # A LIST per port, matching design.md's ``Map<String, List<EdgeSpec>>``, so a
            # variadic port's second and later operands survive persistence. ``from_dict``
            # still reads the pre-fix single-object form, so old rows keep loading.
            "inbound": {
                node_id: {
                    port: [edge.to_dict() for edge in edges or ()]
                    for port, edges in (ports or {}).items()
                }
                for node_id, ports in self.inbound.items()
            },
            "data_nodes": list(self.data_nodes),
            "action_nodes": list(self.action_nodes),
            "ml_nodes": list(self.ml_nodes),
            "feature_pipeline": list(self.feature_pipeline),
            "warmup_bars": self.warmup_bars,
            "required_models": [
                requirement.to_dict() for requirement in self.required_models
            ],
            "dependencies": {
                node_id: list(sources) for node_id, sources in self.dependencies.items()
            },
            "action_symbols": dict(self.action_symbols),
            "action_timeframes": dict(self.action_timeframes),
        }

    def to_json(self) -> str:
        """``to_dict`` rendered deterministically. Not a second serialization path."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Any) -> "CompiledPlan":
        """Read back a plan produced by :meth:`to_dict`.

        Deserialization, the inverse of the single serialization path: this is how
        ``dag_worker``, the event loop and the backtester load a persisted
        ``compiled_plan`` without recompiling.
        """
        if not isinstance(data, Mapping):
            raise PlanBuildError(
                f"CompiledPlan must be an object, got {type(data).__name__}"
            )

        raw_nodes = data.get("node_index") or {}
        if not isinstance(raw_nodes, Mapping):
            raise PlanBuildError("CompiledPlan field 'node_index' must be an object")
        raw_inbound = data.get("inbound") or {}
        if not isinstance(raw_inbound, Mapping):
            raise PlanBuildError("CompiledPlan field 'inbound' must be an object")
        raw_dependencies = data.get("dependencies") or {}
        if not isinstance(raw_dependencies, Mapping):
            raise PlanBuildError("CompiledPlan field 'dependencies' must be an object")

        return cls(
            dag_hash=str(data.get("dag_hash") or ""),
            schema_version=int(data.get("schema_version") or CURRENT_SCHEMA_VERSION),
            compiler_version=str(data.get("compiler_version") or COMPILER_VERSION),
            execution_order=_as_id_tuple(data.get("execution_order"), "execution_order"),
            execution_levels=tuple(
                _as_id_tuple(level, "execution_levels")
                for level in (data.get("execution_levels") or ())
            ),
            node_index={
                str(node_id): NodeSpec.from_dict(raw)
                for node_id, raw in raw_nodes.items()
            },
            # Accepts both the current list-per-port form and the single-edge-per-port form
            # written before the variadic fix, the latter becoming a one-element tuple.
            inbound=_as_inbound_map(raw_inbound),
            data_nodes=_as_id_tuple(data.get("data_nodes"), "data_nodes"),
            action_nodes=_as_id_tuple(data.get("action_nodes"), "action_nodes"),
            ml_nodes=_as_id_tuple(data.get("ml_nodes"), "ml_nodes"),
            feature_pipeline=_as_id_tuple(
                data.get("feature_pipeline"), "feature_pipeline"
            ),
            warmup_bars=int(data.get("warmup_bars") or 0),
            required_models=tuple(
                ModelRequirement.from_dict(raw)
                for raw in (data.get("required_models") or ())
            ),
            dependencies={
                str(node_id): _as_id_tuple(sources, "dependencies")
                for node_id, sources in raw_dependencies.items()
            },
            # Absent for a plan written by compiler 2.0.0. Read as absent rather than
            # rejected, so an existing deployment still loads its stored artifact; the
            # compiler's postcondition is what stops a NEW plan escaping unresolved.
            action_symbols=_as_text_map(data.get("action_symbols"), "action_symbols"),
            action_timeframes=_as_text_map(
                data.get("action_timeframes"), "action_timeframes"
            ),
        )

    @classmethod
    def from_json(cls, payload: str) -> "CompiledPlan":
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise PlanBuildError(f"CompiledPlan payload is not valid JSON: {exc}") from exc
        return cls.from_dict(decoded)


def _levels_from_order(
    order: Sequence[str], dependencies: Mapping[str, Sequence[str]]
) -> Tuple[Tuple[str, ...], ...]:
    """Bucket an existing topological order into parallel-executable levels.

    A convenience for a caller that has an order but no levels; the compiler emits levels
    directly from Kahn. Depth is the longest path to the node, so every node's predecessors
    sit in a strictly earlier level (Requirement 3.4), and each level is sorted, so the
    result is byte-identical across processes.
    """
    depth: Dict[str, int] = {}
    for node_id in order:
        upstream = [depth[pred] for pred in dependencies.get(node_id, ()) if pred in depth]
        depth[node_id] = (max(upstream) + 1) if upstream else 0
    buckets: Dict[int, List[str]] = {}
    for node_id, level in depth.items():
        buckets.setdefault(level, []).append(node_id)
    return tuple(tuple(sorted(buckets[level])) for level in sorted(buckets))
