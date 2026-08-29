"""
backend/strategy_compiler.py — Strategy Builder Compiler

PHASE G: Compiler-based architecture.

Visual Blocks → Block Graph → Validation → Execution Graph (IR) → Strategy Package

Builder must NEVER execute strategies directly.
All execution happens through the compiled execution graph.

SB-01: this module is THE compiler
----------------------------------
There used to be two. ``backend_app/routers/strategies.py`` carried its own
``DAGCompiler`` + ``CompiledDAG`` (10 validation steps, its own topological sort, its own
hash) and this module carried ``StrategyCompiler`` (a different rule set, a different
``NodeType`` enum). The two rule sets diverged, so a graph could compile on the save path
and be rejected on the clone path. This module is now the single entry point:

* :meth:`StrategyCompiler.compile_plan` takes a canonical
  :class:`~backend_app.backend.strategy_dag.schema.StrategyGraph` plus a registry,
  delegates **every** validation rule to ``strategy_dag/validator.py`` and returns a
  :class:`~backend_app.backend.strategy_dag.plan.CompiledPlan`.
* :meth:`StrategyCompiler.compile` dispatches: a canonical graph goes down that path, a
  legacy :class:`DAGConfig` goes down the pre-existing ``StrategyPackage`` path. The legacy
  path is kept only so the four current callers keep working until they are re-pointed;
  it is deleted with the router copy.

No validation rule is written here. The rules the router copy owned — orphan and
unreachable node rejection (validator stage 7), action-input provenance (stage 9) and
unknown node type rejection (stage 2, as unresolved ``block_id``) — are reached by calling
``validate()``, not by restating them. The recursive ``_has_cycle`` that could blow the
interpreter stack on a 200-node chain now delegates to the iterative
``validator.find_cycle``.

Two meanings of "dependencies", kept apart
------------------------------------------
``CompiledPlan.dependencies`` is a **predecessor map** (``node_id -> sorted upstream node
ids``). The legacy ``StrategyPackage.dependencies`` is a **resource bucket map**
(``indicators`` / ``ml_models`` / ``data_sources``). They are different things, so the
bucket form is produced by the explicitly named
:meth:`StrategyCompiler.extract_resource_dependencies` and read back through
``StrategyPackage.resource_dependencies``; ``StrategyPackage.dependencies`` survives only
as the wire-compatible alias its API response already publishes.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union
from uuid import uuid4

from backend_app.core.models.pydantic_models import DAGConfig, DAGNode, DAGEdge, NodeType

from backend_app.backend.strategy_dag import validator as _dag_validator
from backend_app.backend.strategy_dag.plan import (
    COMPILER_VERSION,
    CompiledPlan,
    PlanBuildError,
)
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    GraphParseError,
    StrategyGraph,
    compute_dag_hash,
    make_issue,
    parse_v2,
)
from backend_app.backend.strategy_dag.validator import (
    GraphLimits,
    ValidationReport,
    find_cycle,
    topological_order,
)

logger = logging.getLogger("StrategyCompiler")


#: Compile refusal reasons, for ``builder.compile.failures`` (task 9.1, Requirement 24.1).
#: A closed vocabulary of three, because a compile that produces no plan does so for
#: exactly three reasons and an author fixes each differently: the graph was invalid, the
#: payload was not a canonical graph at all, or a validated graph could not be assembled -
#: which is our bug, not the author's.
COMPILE_FAILURE_INVALID_GRAPH = "VALIDATION_ERROR"
COMPILE_FAILURE_NOT_CANONICAL = "NOT_CANONICAL_GRAPH"
COMPILE_FAILURE_ASSEMBLY = "COMPILER_ERROR"


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1)."""
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


__all__ = [
    "COMPILER_VERSION",
    "CompilerError",
    "ValidationError",
    "ExecutionGraph",
    "StrategyPackage",
    "StrategyCompiler",
    "get_compiler",
    "compile_graph",
    "LoadedPlan",
    "load_plan",
    "loose_execution_order",
    "plan_to_engine_graph",
    "ENGINE_NODE_TYPE",
]


class CompilerError(Exception):
    """Raised when compilation fails."""
    pass


class ValidationError(Exception):
    """Raised when validation fails.

    Carries the **real** structured :class:`ValidationReport` when the canonical path
    raised it, so a caller can return every error with its code, target node/edge/field and
    fix hint in one response rather than a flattened string (Requirement 3.5, 8.x).

    Backward compatible on purpose: the legacy path still raises
    ``ValidationError("some message")`` and every existing ``str(e)`` caller keeps working.
    ``report`` is ``None`` in that case, and ``errors`` is an empty list — never a
    half-populated report.
    """

    def __init__(
        self,
        message_or_report: Union[str, ValidationReport, None] = None,
        report: Optional[ValidationReport] = None,
    ):
        if isinstance(message_or_report, ValidationReport):
            report = message_or_report
            message = _report_message(report)
        else:
            message = "" if message_or_report is None else str(message_or_report)
            if report is not None and not message:
                message = _report_message(report)
        self.report: Optional[ValidationReport] = report
        super().__init__(message)

    @property
    def errors(self) -> List[Dict[str, Any]]:
        """The structured error list, or ``[]`` when this was a legacy string failure."""
        return list(self.report.errors) if self.report is not None else []

    @property
    def warnings(self) -> List[Dict[str, Any]]:
        return list(self.report.warnings) if self.report is not None else []

    def codes(self) -> List[str]:
        """Every error/warning code in the report. For tests, telemetry and 422 bodies."""
        return list(self.report.codes()) if self.report is not None else []

    def to_dict(self) -> Dict[str, Any]:
        """The structured error contract, or a minimal envelope for a legacy failure."""
        if self.report is not None:
            return self.report.to_dict()
        return {
            "valid": False,
            "dag_hash": None,
            "errors": [],
            "warnings": [],
            "summary": {},
            "message": str(self),
        }


@dataclass(frozen=True)
class _LooseEdge:
    """The two fields ``validator.find_cycle`` reads, lifted off a legacy edge dict.

    Legacy edges are untyped dicts with no ports. Rather than write a second cycle
    detector for that shape, the shape is adapted to the one detector.
    """

    id: str
    source: str
    target: str
    source_port: str = ""
    target_port: str = ""


def _report_message(report: ValidationReport) -> str:
    """A one-line human summary of a report. The structure stays on ``.report``."""
    count = len(report.errors)
    if not count:
        return "Strategy graph is not executable."
    first = report.errors[0]
    head = f"{first.get('code')}: {first.get('message')}"
    if count == 1:
        return head
    return f"{head} (+{count - 1} more error(s))"


@dataclass
class ExecutionGraph:
    """
    Intermediate Representation (IR) of the strategy.
    
    This is the compiled form that will be executed by the DAG engine.
    """
    id: str
    version: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    execution_order: List[str]  # Topological order
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/transmission."""
        return {
            "id": self.id,
            "version": self.version,
            "nodes": self.nodes,
            "edges": self.edges,
            "execution_order": self.execution_order,
            "metadata": self.metadata
        }


@dataclass
class StrategyPackage:
    """
    Complete strategy package ready for deployment.
    
    Contains:
    - Execution graph (IR)
    - Strategy metadata
    - Resource dependencies
    - Version information

    LEGACY artifact. New code compiles to
    :class:`~backend_app.backend.strategy_dag.plan.CompiledPlan`; this shape exists because
    ``deployment_manager``, ``optimization_engine``, ``backtest_runtime`` and
    ``POST /api/strategy-operations/strategies/compile`` still read it.

    ``dependencies`` here is the **resource bucket map** (``indicators`` / ``ml_models`` /
    ``data_sources``), NOT the predecessor map that ``CompiledPlan.dependencies`` carries.
    Read it through :attr:`resource_dependencies` in new code; the ``dependencies`` field
    name is kept only because it is already on the wire.
    """
    id: str
    strategy_id: str
    version: str
    execution_graph: ExecutionGraph
    metadata: Dict[str, Any] = field(default_factory=dict)
    dependencies: Dict[str, Any] = field(default_factory=dict)
    compiled_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def resource_dependencies(self) -> Dict[str, Any]:
        """The unambiguous name for the bucket map stored in :attr:`dependencies`."""
        return self.dependencies

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage/transmission."""
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "execution_graph": self.execution_graph.to_dict(),
            "metadata": self.metadata,
            "dependencies": self.dependencies,
            "compiled_at": self.compiled_at
        }


class StrategyCompiler:
    """
    Compiles visual strategy graphs into execution graphs.

    Canonical path (SB-01) — :meth:`compile_plan`
        ``StrategyGraph`` + registry -> ``validate()`` -> Kahn with a sorted ready set ->
        ``CompiledPlan``. Every rule lives in ``strategy_dag/validator.py``; an invalid
        graph raises ``ValidationError(report)`` and yields no plan.

    Legacy path — :meth:`compile_package`
        ``DAGConfig`` -> the pre-SB-01 rule set -> ``StrategyPackage``. Kept so the four
        current callers keep working; removed with the router copy.

    :meth:`compile` dispatches between the two on the type of what it is handed, so no
    caller has to change signature to be routed correctly.
    """
    
    def __init__(self):
        self._node_validators = {
            NodeType.INDICATOR: self._validate_indicator_node,
            NodeType.ML: self._validate_ml_node,
            NodeType.LOGIC: self._validate_logic_node,
            NodeType.ACTION: self._validate_action_node,
            NodeType.INPUT: self._validate_input_node,
        }

    # ------------------------------------------------------------------
    # The canonical compile path (SB-01)
    # ------------------------------------------------------------------

    def compile_plan(
        self,
        graph: Union[StrategyGraph, Mapping[str, Any]],
        registry: Any = None,
        *,
        available_bars: Optional[int] = None,
        feature_columns: Optional[int] = None,
        limits: Optional[GraphLimits] = None,
        report: Optional[ValidationReport] = None,
    ) -> CompiledPlan:
        """Compile a canonical graph into a :class:`CompiledPlan`.

        ``ALGORITHM compile(graph, registry)`` from ``design.md`` -> The single compiler.

        Parameters
        ----------
        graph
            A canonical version 2 :class:`StrategyGraph`, or a version 2 wire envelope,
            which is parsed by ``schema.parse_v2``. Parsing is ``schema``'s job, never
            this module's.
        registry
            A descriptor source. ``None`` resolves the assembled registry lazily.
        available_bars, feature_columns, limits
            Passed straight through to ``validate``; the caller that knows the data range
            supplies them so the warmup-feasibility warning can be evaluated.
        report
            An already-computed report for exactly this graph, for a caller that has just
            validated (the validate endpoint) and does not want the pipeline run twice. It
            is still checked for errors here, so a caller cannot smuggle an invalid graph
            past the gate.

        Preconditions
            ``graph`` parses as version 2; the registry assembles.

        Postconditions
            Either a ``CompiledPlan`` whose ``dag_hash`` equals
            ``compute_dag_hash(plan-source graph)``, whose ``execution_order`` holds every
            node exactly once with every edge's source before its target, and whose
            ``execution_levels`` place every node's predecessors in a strictly earlier
            level — or a raised :class:`ValidationError` carrying the complete report and
            **no plan at all**. Nothing is persisted and nothing is mutated on the failure
            path (Requirement 3.5, 3.6).

        Raises
            :class:`ValidationError` with ``.report`` set, for any invalid graph.
            :class:`CompilerError` when a *valid* graph still cannot be assembled into a
            plan — a compiler bug or a registry that changed underneath the validation,
            never a user error.
        """
        # Requirement 24.1. Measured from here rather than from the top of the method so
        # `builder.compile.duration_ms` is the compile, not the caller's parse of a wire
        # envelope; `_as_canonical_graph` refusals are counted as failures below.
        collector = _metrics()
        try:
            parsed = self._as_canonical_graph(graph)
        except CompilerError:
            if collector is not None:
                collector.record_builder_compile_failure(COMPILE_FAILURE_NOT_CANONICAL)
            raise
        _measured_from = time.perf_counter()

        resolved_report = (
            report
            if report is not None
            else _dag_validator.validate(
                parsed,
                registry,
                available_bars=available_bars,
                feature_columns=feature_columns,
                limits=limits,
            )
        )

        # All-or-nothing. An invalid graph never produces a partial plan (Requirement 3.5).
        if resolved_report.has_errors:
            logger.info(
                "Compilation refused: %d error(s) %s",
                len(resolved_report.errors),
                resolved_report.codes(),
            )
            if collector is not None:
                collector.record_builder_compile_failure(COMPILE_FAILURE_INVALID_GRAPH)
            raise ValidationError(resolved_report)

        # The graph that gets compiled is the SERVER's graph: ports, categories, edge
        # types and validation state as the registry declares them, never as the client
        # sent them (Requirement 6.13).
        canonical = resolved_report.canonical_graph or parsed

        order, levels = self._execution_order(canonical, resolved_report)

        try:
            plan = CompiledPlan.from_graph(
                canonical,
                order,
                levels,
                registry=registry,
                warmup_bars=resolved_report.warmup_bars,
            )
        except PlanBuildError as exc:
            # A validated graph that cannot be assembled is our bug, not the author's.
            if collector is not None:
                collector.record_builder_compile_failure(COMPILE_FAILURE_ASSEMBLY)
            raise CompilerError(f"Compilation failed after validation passed: {exc}") from exc

        # Postconditions from design.md, asserted rather than assumed: a plan that breaks
        # one of these is worse than no plan, because it would be persisted and executed.
        self._assert_plan_postconditions(plan, canonical)

        # Requirement 24.1, recorded only for a compile that produced a plan: the duration
        # of a refusal is not the duration of a compile, and mixing the two would make the
        # p95 in Requirement 25.1 a blend of work done and work declined. Node count comes
        # from the plan's own execution order, which holds every node exactly once.
        if collector is not None:
            collector.record_builder_compile(
                (time.perf_counter() - _measured_from) * 1000.0,
                len(plan.execution_order),
            )

        logger.info(
            "Compiled canonical graph %s: %d nodes, %d levels, warmup %d bars",
            plan.dag_hash,
            len(plan.execution_order),
            len(plan.execution_levels),
            plan.warmup_bars,
        )
        return plan

    @staticmethod
    def _as_canonical_graph(
        graph: Union[StrategyGraph, Mapping[str, Any]]
    ) -> StrategyGraph:
        """``graph`` as a :class:`StrategyGraph`, parsing a v2 envelope when handed one."""
        if isinstance(graph, StrategyGraph):
            return graph
        try:
            return parse_v2(graph)
        except GraphParseError as exc:
            # A payload that is not a canonical graph is a caller contract breach, and it
            # must not be silently coerced into an empty graph that then "validates".
            raise CompilerError(f"Not a canonical schema_version 2 graph: {exc}") from exc

    @staticmethod
    def _execution_order(
        graph: StrategyGraph, report: Optional[ValidationReport] = None
    ) -> tuple:
        """``(order, levels)`` for an already-validated graph.

        Kahn's algorithm with the ready set **sorted at every step**, which is what makes
        ``execution_order`` byte-identical across processes (Requirement 2.3) and therefore
        makes ``dag_hash`` stability and reproducible backtests possible. The single
        implementation lives in ``validator.topological_order``; the validator already ran
        it, so its result is reused when the report carries one rather than recomputed —
        one algorithm, one answer, no chance of the two disagreeing.

        Raises
            :class:`ValidationError` naming the cycle when no order exists. Reaching this
            means acyclicity passed and Kahn then failed, so the cycle is reported through
            the same structured contract rather than as a bare exception.
        """
        order = None if report is None else report.execution_order
        levels = None if report is None else report.execution_levels
        if order is None or levels is None:
            order, levels = topological_order(graph)

        if order is None or levels is None or len(order) != len(graph.nodes):
            cycle = find_cycle(graph.nodes, graph.edges)
            cycle_report = ValidationReport()
            cycle_report.add(
                make_issue(
                    _dag_validator.CODE_CYCLE,
                    _dag_validator.SEVERITY_ERROR,
                    "This strategy contains a loop: " + " -> ".join(cycle)
                    if cycle
                    else "No execution order exists for this strategy graph.",
                    node_id=cycle[0] if cycle else None,
                    expected="an acyclic graph",
                    actual=cycle,
                    fix_hint="Remove one connection on that loop; a strategy must flow one way.",
                )
            )
            raise ValidationError(cycle_report)

        return list(order), [list(level) for level in levels]

    @staticmethod
    def _assert_plan_postconditions(plan: CompiledPlan, graph: StrategyGraph) -> None:
        """The design's postconditions, checked before a plan can escape this method."""
        node_ids = {node.id for node in graph.nodes}
        if set(plan.execution_order) != node_ids or len(plan.execution_order) != len(node_ids):
            raise CompilerError(
                "execution_order must contain every node of the graph exactly once"
            )
        if plan.dag_hash != compute_dag_hash(graph):
            raise CompilerError("plan.dag_hash does not match the compiled graph")
        if not plan.data_nodes:
            raise CompilerError("a compiled plan must hold at least one DATA node")
        if not plan.action_nodes:
            raise CompilerError("a compiled plan must hold at least one ACTION node")

        # Requirement 12.5. An ACTION block carries no traded-asset param (12.8), so the
        # plan is the only place the market an order belongs to can come from. A plan that
        # escaped with an action whose symbol is unresolved would leave the execution layer
        # to guess an instrument, which is the one thing it must never do.
        unresolved = [
            action_id
            for action_id in plan.action_nodes
            if not (plan.action_symbols.get(action_id) or "").strip()
        ]
        if unresolved:
            raise CompilerError(
                "every ACTION node must have a traded symbol resolved from its upstream "
                f"DATA node (Requirement 12.5); unresolved: {sorted(unresolved)}"
            )
        stray = sorted(set(plan.action_symbols) - set(plan.action_nodes))
        if stray:
            raise CompilerError(
                f"action_symbols names non-action node(s) {stray}; the map is keyed by "
                "ACTION node id"
            )

        position = {node_id: index for index, node_id in enumerate(plan.execution_order)}
        for edge in graph.edges:
            if position[edge.source] >= position[edge.target]:
                raise CompilerError(
                    f"execution_order places '{edge.source}' after its downstream "
                    f"'{edge.target}'"
                )

        depth = {}
        for index, level in enumerate(plan.execution_levels):
            for node_id in level:
                depth[node_id] = index
        if len(depth) != len(node_ids):
            raise CompilerError("execution_levels must cover every node exactly once")
        for node_id, predecessors in plan.dependencies.items():
            for predecessor in predecessors:
                if depth[predecessor] >= depth[node_id]:
                    raise CompilerError(
                        f"execution_levels place '{predecessor}' no earlier than its "
                        f"downstream '{node_id}'"
                    )

    def extract_resource_dependencies(
        self, graph: Union[StrategyGraph, DAGConfig]
    ) -> Dict[str, List[str]]:
        """The resource buckets a strategy needs: indicators, ml models, data sources.

        Deliberately **not** called ``dependencies``: ``CompiledPlan.dependencies`` is a
        predecessor map, and one name for two meanings is how a caller ends up reading a
        node id where it expected an indicator name. This is the bucket form, and it is the
        only thing that produces it.

        Works on a canonical graph (buckets keyed off ``BlockCategory`` and ``block_id``)
        and on a legacy ``DAGConfig`` (buckets keyed off the old ``NodeType`` and the
        ``indicator`` / ``model_id`` / ``symbol`` scalar fields). Values are sorted, so the
        result is deterministic.
        """
        if isinstance(graph, StrategyGraph):
            indicators: Set[str] = set()
            models: Set[str] = set()
            sources: Set[str] = set()
            for node in graph.nodes:
                if node.category is BlockCategory.INDICATOR:
                    indicators.add(node.block_id)
                elif node.category is BlockCategory.ML_DL:
                    models.add(str(node.params.get("model_id") or node.block_id))
                elif node.category is BlockCategory.DATA:
                    symbol = node.params.get("symbol")
                    if isinstance(symbol, str) and symbol:
                        sources.add(symbol)
            return {
                "indicators": sorted(indicators),
                "ml_models": sorted(models),
                "data_sources": sorted(sources),
            }
        return self._extract_dependencies(graph)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def compile(
        self,
        dag_config: Union[DAGConfig, StrategyGraph, Mapping[str, Any], None] = None,
        strategy_id: Optional[str] = None,
        version: Optional[str] = None,
        metadata: Optional[Dict] = None,
        *,
        graph: Union[StrategyGraph, Mapping[str, Any], None] = None,
        registry: Any = None,
        available_bars: Optional[int] = None,
        feature_columns: Optional[int] = None,
        limits: Optional[GraphLimits] = None,
        report: Optional[ValidationReport] = None,
    ) -> Union[CompiledPlan, StrategyPackage]:
        """The one compiler entry point (Requirement 3.1). Dispatches on what it is given.

        * a canonical :class:`StrategyGraph`, or a ``schema_version: 2`` envelope, or
          anything passed as ``graph=`` -> :meth:`compile_plan`, returning a
          :class:`CompiledPlan`
        * a legacy :class:`DAGConfig` -> :meth:`compile_package`, returning a
          :class:`StrategyPackage`

        Both existing call shapes keep working unchanged:
        ``compile(dag_config=cfg, strategy_id=..., version=...)`` and
        ``compile(cfg, sid, ver)``. Task 2.4 removes the legacy branch once the six call
        sites are re-pointed.
        """
        subject = graph if graph is not None else dag_config
        if subject is None:
            raise CompilerError("compile() needs a strategy graph or a DAG config")

        if self._is_canonical(subject):
            return self.compile_plan(
                subject,
                registry,
                available_bars=available_bars,
                feature_columns=feature_columns,
                limits=limits,
                report=report,
            )

        return self.compile_package(
            subject,
            strategy_id or "",
            version or "",
            metadata,
        )

    @staticmethod
    def _is_canonical(subject: Any) -> bool:
        """True when ``subject`` is the canonical graph shape rather than a ``DAGConfig``.

        A ``StrategyGraph`` is canonical by type. A mapping is canonical when it declares
        ``schema_version`` 2 or higher, or when its nodes carry ``block_id`` — the field a
        version 1 / ``DAGConfig`` node never has. Everything else is legacy, so an
        unrecognised payload keeps its existing behaviour instead of being misrouted.
        """
        if isinstance(subject, StrategyGraph):
            return True
        if not isinstance(subject, Mapping):
            return False
        raw_version = subject.get("schema_version")
        if isinstance(raw_version, int) and not isinstance(raw_version, bool):
            return raw_version >= 2
        nodes = subject.get("nodes")
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes)):
            return any(
                isinstance(node, Mapping) and "block_id" in node for node in nodes
            )
        return False

    # ------------------------------------------------------------------
    # The legacy path, unchanged in behaviour
    # ------------------------------------------------------------------

    def compile_package(
        self,
        dag_config: DAGConfig,
        strategy_id: str,
        version: str,
        metadata: Optional[Dict] = None
    ) -> StrategyPackage:
        """
        Compile a visual strategy graph into a strategy package.
        
        LEGACY. Superseded by :meth:`compile_plan`; kept only until the callers listed in
        ``design.md`` -> Migration path for callers are re-pointed (task 2.4).
        
        Args:
            dag_config: DAG configuration from builder
            strategy_id: Strategy ID
            version: Strategy version
            metadata: Optional metadata
            
        Returns:
            StrategyPackage ready for deployment
            
        Raises:
            CompilerError: If compilation fails
            ValidationError: If validation fails
        """
        try:
            # Phase 1: Validate graph structure
            self._validate_graph_structure(dag_config)
            
            # Phase 2: Validate nodes
            self._validate_nodes(dag_config.nodes)
            
            # Phase 3: Validate edges
            self._validate_edges(dag_config.edges, dag_config.nodes)
            
            # Phase 4: Generate execution order
            execution_order = self._generate_execution_order(dag_config.nodes, dag_config.edges)
            
            # Phase 5: Build execution graph
            execution_graph = ExecutionGraph(
                id=str(uuid4()),
                version=version,
                nodes=dag_config.nodes,
                edges=dag_config.edges,
                execution_order=execution_order,
                metadata={
                    "symbols": dag_config.symbols,
                    "timeframe": dag_config.timeframe,
                    "node_count": len(dag_config.nodes),
                    "edge_count": len(dag_config.edges),
                    **(metadata or {})
                }
            )
            
            # Phase 6: Create strategy package
            package = StrategyPackage(
                id=str(uuid4()),
                strategy_id=strategy_id,
                version=version,
                execution_graph=execution_graph,
                metadata=metadata or {},
                dependencies=self._extract_dependencies(dag_config)
            )
            
            logger.info(f"Compiled strategy {strategy_id} v{version} with {len(dag_config.nodes)} nodes")
            
            return package
            
        except ValidationError as e:
            logger.error(f"Validation failed for strategy {strategy_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Compilation failed for strategy {strategy_id}: {e}")
            raise CompilerError(f"Compilation failed: {e}")
    
    def _validate_graph_structure(self, dag_config: DAGConfig) -> None:
        """Validate overall graph structure."""
        if not dag_config.nodes:
            raise ValidationError("No nodes in strategy graph")
        
        if not dag_config.edges:
            raise ValidationError("No edges in strategy graph")
        
        # Check for at least one input node
        input_nodes = [n for n in dag_config.nodes if n.get("type") == NodeType.INPUT]
        if not input_nodes:
            raise ValidationError("Strategy must have at least one input node")
        
        # Check for at least one action node
        action_nodes = [n for n in dag_config.nodes if n.get("type") == NodeType.ACTION]
        if not action_nodes:
            raise ValidationError("Strategy must have at least one action node")
    
    def _validate_nodes(self, nodes: List[Dict]) -> None:
        """Validate all nodes."""
        node_ids = set()
        
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                raise ValidationError("Node missing id")
            
            if node_id in node_ids:
                raise ValidationError(f"Duplicate node id: {node_id}")
            node_ids.add(node_id)
            
            node_type = node.get("type")
            if not node_type:
                raise ValidationError(f"Node {node_id} missing type")
            
            validator = self._node_validators.get(node_type)
            if validator:
                validator(node)
    
    def _validate_indicator_node(self, node: Dict) -> None:
        """Validate indicator node."""
        if not node.get("indicator"):
            raise ValidationError(f"Indicator node {node['id']} missing indicator field")
        
        params = node.get("params", {})
        if not isinstance(params, dict):
            raise ValidationError(f"Indicator node {node['id']} params must be a dict")
    
    def _validate_ml_node(self, node: Dict) -> None:
        """Validate ML node."""
        if not node.get("model_id"):
            raise ValidationError(f"ML node {node['id']} missing model_id")
        
        confidence_threshold = node.get("confidence_threshold", 0.7)
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValidationError(f"ML node {node['id']} confidence_threshold must be between 0 and 1")
    
    def _validate_logic_node(self, node: Dict) -> None:
        """Validate logic node."""
        if not node.get("operator"):
            raise ValidationError(f"Logic node {node['id']} missing operator")
    
    def _validate_action_node(self, node: Dict) -> None:
        """Validate action node."""
        if not node.get("action"):
            raise ValidationError(f"Action node {node['id']} missing action")
        
        valid_actions = ["buy", "sell", "hold", "close"]
        if node["action"] not in valid_actions:
            raise ValidationError(f"Action node {node['id']} has invalid action: {node['action']}")
    
    def _validate_input_node(self, node: Dict) -> None:
        """Validate input node."""
        if not node.get("symbol"):
            raise ValidationError(f"Input node {node['id']} missing symbol")
        
        if not node.get("timeframe"):
            raise ValidationError(f"Input node {node['id']} missing timeframe")
    
    def _validate_edges(self, edges: List[Dict], nodes: List[Dict]) -> None:
        """Validate all edges."""
        node_ids = {n.get("id") for n in nodes}
        
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            
            if not source or not target:
                raise ValidationError("Edge missing source or target")
            
            if source not in node_ids:
                raise ValidationError(f"Edge source {source} not found in nodes")
            
            if target not in node_ids:
                raise ValidationError(f"Edge target {target} not found in nodes")
        
        # Check for cycles — iteratively, and naming the loop that was found
        cycle = self._find_cycle(nodes, edges)
        if cycle:
            raise ValidationError(
                "Strategy graph contains cycles: " + " -> ".join(cycle)
            )
    
    def _find_cycle(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """One real cycle as an ordered node sequence, or ``[]`` for a DAG.

        Delegates to the **iterative** ``validator.find_cycle``. The recursive DFS this
        replaced blew the interpreter's stack on a long chain, and a 200-node strategy is
        allowed to be one long chain. There is one cycle detector in the codebase and this
        is not a second one.
        """
        node_ids = [str(node.get("id")) for node in nodes if node.get("id")]
        known = set(node_ids)
        edge_specs = []
        for index, edge in enumerate(edges):
            source = edge.get("source")
            target = edge.get("target")
            if source not in known or target not in known:
                continue  # unresolved endpoints are reported by _validate_edges
            edge_specs.append(
                _LooseEdge(
                    id=str(edge.get("id") or f"e_{index}"),
                    source=str(source),
                    target=str(target),
                )
            )
        return find_cycle(node_ids, edge_specs)

    def _has_cycle(self, nodes: List[Dict], edges: List[Dict]) -> bool:
        """True when the graph has a cycle. Thin view over :meth:`_find_cycle`."""
        return bool(self._find_cycle(nodes, edges))
    
    def _generate_execution_order(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """
        Generate topological execution order.

        Thin view over :func:`loose_execution_order`, which is the one loose-dict Kahn in
        the codebase. Kept as a method because the legacy path and its tests call it.
        """
        return loose_execution_order(nodes, edges)
    
    def _extract_dependencies(self, dag_config: DAGConfig) -> Dict[str, Any]:
        """The legacy resource **buckets**: indicators, ml models, data sources.

        Not a predecessor map. See :meth:`extract_resource_dependencies`, which is the
        public, unambiguously named entry point and the one new code should call.
        """
        dependencies = {
            "indicators": set(),
            "ml_models": set(),
            "data_sources": set()
        }
        
        for node in dag_config.nodes:
            node_type = node.get("type")
            
            if node_type == NodeType.INDICATOR:
                dependencies["indicators"].add(node.get("indicator"))
            elif node_type == NodeType.ML:
                dependencies["ml_models"].add(node.get("model_id"))
            elif node_type == NodeType.INPUT:
                dependencies["data_sources"].add(node.get("symbol"))
        
        # Convert sets to sorted lists: JSON-serializable and deterministic.
        return {
            "indicators": sorted(d for d in dependencies["indicators"] if d),
            "ml_models": sorted(d for d in dependencies["ml_models"] if d),
            "data_sources": sorted(d for d in dependencies["data_sources"] if d)
        }


# Singleton instance
_compiler = None

def get_compiler() -> StrategyCompiler:
    """Get singleton StrategyCompiler instance."""
    global _compiler
    if _compiler is None:
        _compiler = StrategyCompiler()
    return _compiler


def compile_graph(
    graph: Union[StrategyGraph, Mapping[str, Any]],
    registry: Any = None,
    *,
    available_bars: Optional[int] = None,
    feature_columns: Optional[int] = None,
    limits: Optional[GraphLimits] = None,
    report: Optional[ValidationReport] = None,
) -> CompiledPlan:
    """Module-level shorthand for the canonical path: one call, one plan.

    For callers that want the compiler without holding the singleton — the validate
    endpoint, ``dag_worker``, the backtester's recompile-on-hash-mismatch branch.
    """
    return get_compiler().compile_plan(
        graph,
        registry,
        available_bars=available_bars,
        feature_columns=feature_columns,
        limits=limits,
        report=report,
    )


# ---------------------------------------------------------------------------
# Loose-dict topological order (task 2.4)
#
# The router's DAGCompiler carried its own Kahn over untyped node/edge dicts. It is
# deleted; the one remaining consumer of that shape is the DAG-pruning utility behind
# POST /api/strategies/optimize, which operates on legacy payloads that were never
# canonical graphs. Rather than let a second sort survive in a router, the loose-dict
# ordering lives here, next to the canonical one, and there is exactly one of each.
# ---------------------------------------------------------------------------


def loose_execution_order(
    nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> List[str]:
    """Topological order of an untyped ``{"id": ...}`` / ``{"source", "target"}`` graph.

    Kahn with the ready set **sorted at every step**, so the order is byte-identical
    across processes (Requirement 2.3) rather than depending on the order the client
    happened to send its nodes in. Same algorithm as ``validator.topological_order``,
    applied to the legacy loose shape.

    Raises
        :class:`ValidationError` when no order exists, i.e. the graph has a cycle.
    """
    adjacency: Dict[str, List[str]] = {}
    in_degree: Dict[str, int] = {node["id"]: 0 for node in nodes}
    seen_pairs: Set[tuple] = set()

    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in in_degree or target not in in_degree:
            continue
        if (source, target) in seen_pairs:
            continue
        seen_pairs.add((source, target))
        adjacency.setdefault(source, []).append(target)
        in_degree[target] += 1

    ready = sorted(node_id for node_id, degree in in_degree.items() if degree == 0)
    execution_order: List[str] = []

    while ready:
        level = list(ready)
        next_ready: Set[str] = set()
        for node_id in level:
            execution_order.append(node_id)
            for neighbor in adjacency.get(node_id, ()):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_ready.add(neighbor)
        ready = sorted(next_ready)

    if len(execution_order) != len(nodes):
        raise ValidationError("Cannot generate execution order - graph may have cycles")

    return execution_order


# ---------------------------------------------------------------------------
# The plan load path (Requirement 22.3, 22.5; task 2.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedPlan:
    """The plan a version consumer will execute, and where it came from.

    ``reused`` is the whole point of this type. Requirement 22.3 says every version
    consumer must be served the *same* stored plan, and Requirement 22.5 says a consumer
    recompiles only when the stored plan's identity hash differs from the hash recomputed
    from the version's graph. Returning the provenance rather than just the plan means the
    decision is observable — a caller can log it, a metric can count it and a test can
    assert it, instead of everyone hoping the branch was taken.
    """

    plan: CompiledPlan
    graph: StrategyGraph
    reused: bool
    reason: str

    @property
    def dag_hash(self) -> str:
        """The identity hash, read as a field (SB-02)."""
        return self.plan.dag_hash


#: ``compiled_plan`` is the migration-004 column. ``execution_graph`` is where
#: ``StrategyService._insert_version_row`` puts the serialized plan while migration 004
#: part 1 is unapplied, so it is tried second. Anything that does not deserialize as a
#: plan is ignored and the graph is recompiled; a stale or foreign artifact is never
#: executed.
_PLAN_COLUMNS: Tuple[str, ...] = ("compiled_plan", "execution_graph")

#: The file whose absence puts a row into the legacy ``blueprint`` / ``execution_graph``
#: shape, named in the degradation warning below so an operator never has to guess.
#: ``strategy_service.CANONICAL_COLUMN_MIGRATION`` and
#: ``strategy_lifecycle.CANONICAL_LIFECYCLE_MIGRATION`` are the same file. It is spelled
#: here rather than imported because this module compiles graphs and must not import the
#: service or the lifecycle layer; that the three spellings agree is asserted by
#: ``tests/test_version_consumer_agreement.py`` rather than left to prose.
CANONICAL_COLUMN_MIGRATION = "backend_app/migrations/004_strategy_builder_canonical.sql"


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _decode_stored_plan(raw: Any) -> Optional[CompiledPlan]:
    """``raw`` as a :class:`CompiledPlan`, or ``None`` when it is not one.

    Deliberately forgiving about the *shape* and unforgiving about the *contents*: a
    column holding a legacy ``ExecutionGraph`` dict, a NULL, or a truncated blob yields
    ``None`` so the caller recompiles. It never yields a partially populated plan, because
    a plan that does not describe the graph is worse than no plan at all.
    """
    if raw is None or raw == "" or raw == {}:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            return CompiledPlan.from_json(raw)
        return CompiledPlan.from_dict(raw)
    except (PlanBuildError, TypeError, ValueError) as exc:
        logger.info("Stored plan could not be read back (%s); recompiling.", exc)
        return None


def load_plan(
    row: Any,
    registry: Any = None,
    *,
    available_bars: Optional[int] = None,
    feature_columns: Optional[int] = None,
) -> LoadedPlan:
    """The plan for a stored version, reusing the persisted one when it still fits.

    ``ALGORITHM`` (Requirement 22.5, ``design.md`` -> Migration path for callers):

    1. Load the row's graph through ``schema.load_graph``, which parses a version 2 row and
       migrates a version 1 row **at read time** without writing anything back.
    2. Read ``compiled_plan`` (then the legacy ``execution_graph`` fallback). If it
       deserializes **and** ``plan.matches_graph(graph)``, serve it unchanged — every
       consumer then executes byte-identical bytes (Requirement 22.3).
    3. Otherwise compile the graph. That covers three real cases with one branch: the
       column is absent because migration 004 part 1 has not been applied; the column is
       NULL because the row predates it; or the stored hash differs from the graph's, which
       is exactly the mismatch Requirement 22.5 says must force a recompile.

    Never recompiles unconditionally (that discards the artifact the requirement exists to
    share) and never serves a plan whose hash disagrees with the graph (that executes a
    strategy the plan does not describe).

    Parameters
        ``row`` — a ``strategy_versions`` row, a ``strategies`` row, or any mapping
        ``schema.extract_raw_graph`` accepts, including a bare ``{"nodes", "edges"}``
        payload.

    Raises
        :class:`ValidationError` when the row's graph does not validate and no usable plan
        was stored. A stored strategy that no longer compiles is reported, not executed.
        :class:`CompilerError` when the row carries no readable graph at all.
    """
    from backend_app.backend.strategy_dag import schema as _schema

    try:
        graph = _schema.load_graph(row)
    except _schema.GraphParseError as exc:
        raise CompilerError(f"Row carries no loadable strategy graph: {exc}") from exc

    if _row_value(row, "graph_json") is None and _row_value(row, "blueprint") is not None:
        # The migration-004-part-1-unapplied shape: the graph is in ``blueprint`` and the
        # plan is in ``execution_graph``, because ``blueprint`` is NOT NULL in the
        # pre-canonical schema and ``graph_json`` does not exist yet. Both consumers still
        # get the same artifact - that is the point of the two entries in
        # ``_PLAN_COLUMNS`` - so this degrades with a warning rather than refusing, but the
        # operator is told which file to apply and what the row is missing.
        logger.warning(
            "Version graph read from the legacy 'blueprint' column: 'graph_json' is "
            "absent, so %s (part 1) has not been applied. The version still loads and "
            "every consumer still gets the same plan, but dag_hash, validation_state and "
            "validation_report were never persisted for this row, so the deploy-time hash "
            "gate cannot run against it.",
            CANONICAL_COLUMN_MIGRATION,
        )

    for column in _PLAN_COLUMNS:
        stored = _decode_stored_plan(_row_value(row, column))
        if stored is None:
            continue
        if stored.matches_graph(graph):
            logger.debug(
                "Reusing the persisted plan from '%s' (dag_hash=%s)",
                column,
                stored.dag_hash,
            )
            return LoadedPlan(
                plan=stored, graph=graph, reused=True, reason=f"{column}_hash_match"
            )
        logger.warning(
            "Stored plan in '%s' has dag_hash %s but the version's graph hashes to %s; "
            "recompiling before execution (Requirement 22.5).",
            column,
            stored.dag_hash,
            compute_dag_hash(graph),
        )
        plan = compile_graph(
            graph,
            registry,
            available_bars=available_bars,
            feature_columns=feature_columns,
        )
        return LoadedPlan(
            plan=plan, graph=graph, reused=False, reason=f"{column}_hash_mismatch"
        )

    plan = compile_graph(
        graph,
        registry,
        available_bars=available_bars,
        feature_columns=feature_columns,
    )
    return LoadedPlan(plan=plan, graph=graph, reused=False, reason="no_stored_plan")


# ---------------------------------------------------------------------------
# Canonical plan -> legacy DAG engine payload (task 2.4)
# ---------------------------------------------------------------------------

#: ``BlockCategory`` -> the ``type`` key ``dag_engine.DAGEngine.executors`` dispatches on.
#: Total over the seven canonical categories, so no node can fall through to a default.
#: ``dag_engine`` is REUSED AS-IS in this phase, so the adaptation happens here rather
#: than by teaching the engine a second vocabulary.
ENGINE_NODE_TYPE: Dict[BlockCategory, str] = {
    BlockCategory.DATA: "market_data",
    BlockCategory.INDICATOR: "indicator",
    BlockCategory.MATH: "math",
    BlockCategory.LOGIC: "logic",
    BlockCategory.FEATURE_ENGINEERING: "feature",
    BlockCategory.ML_DL: "ml",
    BlockCategory.ACTION: "action",
}

#: Params the legacy executors read off the top level of a node dict rather than out of
#: ``params``. Lifted rather than duplicated blindly, so the engine keeps working without
#: the canonical node growing legacy fields.
_ENGINE_LIFTED_PARAMS: Tuple[str, ...] = (
    "model_id",
    "confidence_threshold",
    "operator",
    "action",
    "symbol",
    "timeframe",
)

#: Canonical INDICATOR ``block_id`` -> the name ``dag_engine.IndicatorExecutor`` branches
#: on. Only the names that genuinely differ are listed; everything else is the block id
#: verbatim. Before task 2.4 the worker was fed legacy loose dicts carrying the engine's
#: own vocabulary, so this translation used to happen in the caller and is exactly what the
#: canonical path lost.
_ENGINE_INDICATOR_NAME: Dict[str, str] = {
    # ``bb`` is the name the pre-task-5.4 engine branched on for Bollinger Bands. Since the
    # re-point, ``dag_engine.IndicatorExecutor`` resolves the indicator from the node's
    # canonical ``block_id`` and publishes all five declared bands as separate output ports,
    # so this key is only what a consumer still reading ``node["indicator"]`` sees. It is
    # kept because ``dag_event_loop.StatefulIndicatorExecutor`` is one such consumer.
    "bollinger_bands": "bb",
}

#: Canonical indicator param key -> the key the legacy executors read. ``IndicatorExecutor``
#: and ``dag_event_loop.StatefulIndicatorExecutor`` both read ``params["period"]`` and
#: ``params["std_dev"]``; ``INDICATOR_SPECS`` publishes ``window`` and ``num_std``. The
#: canonical keys are kept alongside the aliases, so the re-pointed ``IndicatorExecutor``
#: (task 5.4) reads the registry's own names while ``StatefulIndicatorExecutor`` keeps
#: reading the legacy ones.
_ENGINE_INDICATOR_PARAM_ALIAS: Tuple[Tuple[str, str], ...] = (
    ("window", "period"),
    ("num_std", "std_dev"),
)

#: Categories whose engine executor delegates to the kernel the descriptor publishes.
#: MATH and LOGIC are the two families whose semantics live entirely in a ``block_specs``
#: kernel, and the two the legacy engine could not express at all: ``math`` was a
#: pass-through that republished ``close``, and every ``logic`` node was evaluated as AND.
_ENGINE_KERNEL_CATEGORIES: Tuple[BlockCategory, ...] = (
    BlockCategory.MATH,
    BlockCategory.LOGIC,
)


def _engine_registry(registry: Any = None) -> Any:
    """The supplied registry, or the memoised default one.

    Resolved inside the call, never at import: assembling the registry reads
    ``exchange_executor.OrderType`` and would drag CCXT into every process that only wanted
    the plan shape. Same seam ``plan._resolve_registry`` and ``compile_plan`` use, so a
    caller that passes no registry gets the same descriptors the compiler validated against.
    """
    if registry is not None:
        return registry
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.get_registry()


def _engine_indicator_params(descriptor: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
    """``params`` plus the legacy aliases the indicator executors actually read.

    The alias value comes from ``descriptor.resolved_params``, so a param the author left
    blank falls back to the **descriptor's** default rather than to the engine's private
    one. Those two used to disagree (``ema`` defaults to 14 bars in ``INDICATOR_SPECS`` and
    the pre-task-5.4 ``IndicatorExecutor`` substituted 20), and the registry is the published
    contract.
    """
    engine_params = dict(params)
    resolved = descriptor.resolved_params(params) if descriptor is not None else dict(params)
    for canonical, legacy in _ENGINE_INDICATOR_PARAM_ALIAS:
        if legacy in engine_params:
            continue
        value = resolved.get(canonical)
        if value is not None:
            engine_params[legacy] = value
    return engine_params


def _engine_input_order(
    plan: CompiledPlan, node_id: str, descriptor: Any
) -> List[str]:
    """Upstream node ids for ``node_id``, in the order its descriptor declares its ports.

    ``DAGEngine.get_node_inputs`` walks the edge list and hands a multi-input executor its
    inputs positionally, so port order **is** operand order: it decides which side of a
    ``gt`` is the left-hand one and which of ``between``'s three inputs is the value. The
    plan's ``inbound`` map is keyed by ``target_port``, and its iteration order is the edge
    sort order (or, after a JSON round trip, alphabetical) - neither of which is the
    declared port order. So the order is taken from the descriptor and the JSON round trip
    can no longer change which operand is which.

    **Every** edge on a port contributes an operand, not just the first. A variadic port
    holds 2..N connections - ``add``, ``multiply``, ``min``, ``max``, ``and``, ``or`` and
    ``between`` all declare one - so emitting one source per port would hand ``add`` a single
    addend and ``and`` a single condition, computing a plausible answer from a subset of the
    author's operands with nothing raised anywhere. Within a port the edges keep
    ``plan.inbound``'s order, which is the graph's own total edge order, so operand order is
    identical across processes and across a JSON round trip.
    """
    inbound = plan.inbound.get(node_id) or {}
    ordered: List[str] = []
    seen_ports: Set[str] = set()
    declared = [port.name for port in getattr(descriptor, "inputs", ()) or ()]
    for port_name in declared:
        edges = inbound.get(port_name) or ()
        if not edges:  # an optional port the author left unconnected
            continue
        seen_ports.add(port_name)
        ordered.extend(edge.source for edge in edges)
    # A port the descriptor does not declare cannot happen for a validated graph (the
    # validator reports PORT_UNKNOWN), but a persisted plan is read back without being
    # re-validated. Appending the leftovers in a sorted, deterministic order is better than
    # dropping an edge the plan describes.
    for port_name in sorted(set(inbound) - seen_ports):
        ordered.extend(edge.source for edge in inbound[port_name] or ())
    return ordered


def plan_to_engine_graph(
    plan: CompiledPlan, registry: Any = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(nodes, edges)`` for :class:`~backend_app.backend.dag_engine.DAGEngine`.

    The nodes come out **in ``plan.execution_order``**, so a consumer that iterates the
    list executes in the compiler's deterministic topological order rather than in whatever
    order a payload happened to arrive in. That is the concrete content of "consume
    ``CompiledPlan.to_dict()`` instead of loose dicts": ordering, node identity and edge
    wiring all come from the compiled artifact.

    The edges are reconstructed from ``plan.inbound``, which is the compiler's own
    port-level wiring, so an edge cannot be present here that the plan does not describe -
    and, since ``inbound`` holds every edge per port rather than one, no edge the plan
    describes is missing either. Per target they are emitted in the target's **declared
    input-port order**, and within a port in the plan's edge order, because the engine hands a
    multi-input executor its inputs in edge order.

    FIDELITY (the whole point of this function)
    -------------------------------------------
    ``design.md`` marks ``dag_engine`` REUSED AS-IS for this phase, so the canonical
    vocabulary is translated into the keys the legacy executors actually read, here, rather
    than by teaching the engine a second vocabulary. What that means key by key:

    ===============  ==============================  ==================================
    Executor         key it reads                    where the canonical value lives
    ===============  ==============================  ==================================
    Indicator        ``indicator``                   ``block_id`` (``bollinger_bands``
                                                     -> ``bb``)
    Indicator        ``params["period"]``            ``params["window"]``
    Indicator        ``params["std_dev"]``           ``params["num_std"]``
    Logic            ``operator``                    ``block_id`` upper-cased
    Math / Logic     ``runtime_ref`` (kernel)        ``descriptor.runtime_ref``
    Action           ``action``                      ``metadata["side"]``
    ML               ``model_id``, ``confidence_..`` ``params`` (lifted)
    Market data      -                               reads ``close`` only
    ===============  ==============================  ==================================

    An ACTION node also carries ``side``, ``order_type``, ``intent`` and
    ``reduce_only_forced`` verbatim from the descriptor, because ``action`` collapses a
    six-order-type vocabulary onto ``buy``/``sell``/``hold`` and a consumer that can place a
    real order needs the unreduced form. It carries ``symbol`` - and ``timeframe`` where the
    plan resolved one - from ``plan.action_symbols``, so the engine node says which market
    the order belongs to rather than leaving a consumer to infer it from whichever feed
    happened to trigger the run (Requirement 12.5). It deliberately does **not** carry
    ACTION's
    ``runtime_ref``: that reference is ``CCXTExchangeExecutor.place_order``, and nothing
    reachable from a node dict may place an order - orders go through
    ``execution_guard`` / ``risk_engine`` / ``execution_engine``.

    ``side`` is ``None`` for the exit blocks (``action_close_position``,
    ``action_stop_market``, ``action_stop_limit``, ``action_take_profit_*``): their side is
    the inverse of the open position and is resolved at execution time. The legacy engine
    holds no position state, so those nodes are adapted as ``action="hold"`` - a zero
    contribution to the signed signal series rather than a guessed direction.

    Parameters
        ``registry`` - the descriptor source. ``None`` resolves the assembled registry
        lazily, the same seam ``compile_plan`` uses.

    Raises
        :class:`CompilerError` when the plan names a node the registry does not publish; the
        engine payload for such a node could only be a guess, and a guess here is a wrong
        strategy silently executing.
    """
    resolved_registry = _engine_registry(registry)

    nodes: List[Dict[str, Any]] = []
    for node_id in plan.execution_order:
        node = plan.node(node_id)
        if node is None:  # pragma: no cover - from_graph guarantees the index is total
            raise CompilerError(f"Plan execution_order names unknown node '{node_id}'")

        descriptor = resolved_registry.get(node.block_id)
        if descriptor is None:
            raise CompilerError(
                f"Node {node.id!r} references block {node.block_id!r}, which the registry "
                "does not publish; its runtime behaviour cannot be adapted for the engine"
            )
        metadata: Mapping[str, Any] = getattr(descriptor, "metadata", {}) or {}

        engine_node: Dict[str, Any] = {
            "id": node.id,
            "type": ENGINE_NODE_TYPE[node.category],
            "block_id": node.block_id,
            "category": node.category.value,
            # An indicator descriptor's block_id IS the indicator name, which is what
            # IndicatorExecutor reads.
            "indicator": _ENGINE_INDICATOR_NAME.get(node.block_id, node.block_id),
            "params": (
                _engine_indicator_params(descriptor, node.params)
                if node.category is BlockCategory.INDICATOR
                else dict(node.params)
            ),
        }
        for key in _ENGINE_LIFTED_PARAMS:
            if key in node.params:
                engine_node[key] = node.params[key]

        if node.category is BlockCategory.LOGIC:
            # The engine's legacy tokens (AND, OR, NOT, GT, LT, GTE, LTE, EQ) are exactly
            # the upper-cased canonical block ids, so nothing is invented here. The five
            # blocks the legacy branch chain never had (NEQ, BETWEEN, CROSS_ABOVE,
            # CROSS_BELOW, IF_THEN_ELSE, TO_SIGNAL) are served by the kernel below; the
            # operator is still carried, so the legacy fallback comparator is the one the
            # author picked rather than AND.
            engine_node["operator"] = node.block_id.upper()
        elif node.category is BlockCategory.MATH:
            engine_node["operator"] = metadata.get("operator")

        if node.category in _ENGINE_KERNEL_CATEGORIES:
            engine_node["runtime_ref"] = descriptor.runtime_ref
            engine_node["runtime_params"] = descriptor.runtime_kwargs(node.params)
            engine_node["input_order"] = _engine_input_order(plan, node.id, descriptor)

        if node.category is BlockCategory.ACTION:
            side = metadata.get("side")
            engine_node["side"] = side
            engine_node["order_type"] = metadata.get("order_type")
            engine_node["intent"] = metadata.get("intent")
            engine_node["reduce_only_forced"] = bool(metadata.get("reduce_only_forced"))
            engine_node["action"] = side if side in ("buy", "sell") else "hold"
            # Requirement 12.5: the market this action trades, resolved by the compiler
            # from the DATA node in its upstream closure. The key is the one the legacy
            # vocabulary already reserves (``_ENGINE_LIFTED_PARAMS``), which for a DATA node
            # is lifted out of ``params``; an ACTION node has no such param by design
            # (12.8), so this is where its value comes from. Omitted, never guessed, when
            # the plan does not carry one - a plan written by compiler 2.0.0.
            action_symbol = plan.action_symbol(node.id)
            if action_symbol:
                engine_node["symbol"] = action_symbol
            action_timeframe = plan.action_timeframe(node.id)
            if action_timeframe:
                engine_node["timeframe"] = action_timeframe

        nodes.append(engine_node)

    edges: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for node_id in plan.execution_order:
        node = plan.node(node_id)
        descriptor = (
            resolved_registry.get(node.block_id) if node is not None else None
        )
        inbound = plan.inbound.get(node_id) or {}
        declared = [port.name for port in getattr(descriptor, "inputs", ()) or ()]
        port_order = [name for name in declared if name in inbound]
        port_order += sorted(set(inbound) - set(port_order))
        for port_name in port_order:
            # Every edge on the port, in the plan's order: a variadic port carries 2..N and
            # emitting one would drop the author's other operands before the engine ever
            # saw them.
            for edge in inbound[port_name] or ():
                if edge.id in seen:
                    continue
                seen.add(edge.id)
                edges.append(
                    {
                        "id": edge.id,
                        "source": edge.source,
                        "source_port": edge.source_port,
                        "target": edge.target,
                        "target_port": edge.target_port,
                    }
                )

    return nodes, edges
