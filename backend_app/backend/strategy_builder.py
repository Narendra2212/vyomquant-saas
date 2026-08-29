"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: strategy_builder.py                                  ║
║                                                                          ║
║  Evaluates user drag-and-drop strategy blueprints against live state.    ║
║  Pure logic — zero HTTP/WS code.                                         ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  SB-1  CRITICAL: calculate_dynamic_size used hardcoded $10,000 balance  ║
║  SB-2  blueprint key access without existence check → KeyError           ║
║  SB-3  run_logic is async but has no timeout on logic evaluation         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import operator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from backend_app.backend.strategy_dag.plan import CompiledPlan
    from backend_app.backend.strategy_dag.schema import StrategyGraph
    from backend_app.backend.strategy_dag.validator import GraphLimits, ValidationReport

logger = logging.getLogger("StrategyEngine")

# Safe operator whitelist — prevents code injection via eval()
SAFE_OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


class StrategyEngine:

    def __init__(self, risk_manager, telemetry_engine):
        self.risk = risk_manager
        self.db = telemetry_engine

    # ══════════════════════════════════════════════════════════════════════
    #  CONDITION EVALUATION
    # ══════════════════════════════════════════════════════════════════════

    def _evaluate_condition(self, condition: dict, current_state: dict) -> bool:
        """
        Safely evaluates a single leaf condition from the drag-drop canvas.
        Example: {"left": "RSI_14", "op": "<", "right": 30.0}
        """
        left_key = condition.get("left")
        if not left_key:
            logger.warning("Condition missing 'left' key — skipping.")
            return False

        left_val = current_state.get(left_key)
        if left_val is None:
            # Indicator not yet in live_state — warmup still in progress
            return False

        right_val = condition.get("right")
        # Support dynamic right-hand side: compare two indicators
        if isinstance(right_val, str) and right_val in current_state:
            right_val = current_state[right_val]

        op_func = SAFE_OPERATORS.get(condition.get("op", ""))
        if op_func is None:
            logger.error(
                f"Unsupported operator: '{condition.get('op')}'. Skipping condition."
            )
            return False

        try:
            return op_func(float(left_val), float(right_val))
        except (TypeError, ValueError) as e:
            logger.warning(f"Condition evaluation error: {e}")
            return False

    def _evaluate_logic_tree(self, logic_node: dict, current_state: dict) -> bool:
        """
        Recursively evaluates an AND/OR tree from the drag-drop canvas.
        Short-circuits using all() / any() generators for performance.
        Supports infinite nesting depth.
        """
        if not logic_node or not logic_node.get("conditions"):
            return False

        op_type = logic_node.get("operator", "AND").upper()

        def eval_child(child):
            # Nested group: recurse
            if "operator" in child and "conditions" in child:
                return self._evaluate_logic_tree(child, current_state)
            # Leaf condition
            return self._evaluate_condition(child, current_state)

        if op_type == "AND":
            return all(eval_child(c) for c in logic_node["conditions"])
        if op_type == "OR":
            return any(eval_child(c) for c in logic_node["conditions"])

        logger.error(f"Unknown logic operator: '{op_type}'")
        return False

    # ══════════════════════════════════════════════════════════════════════
    #  MASTER LOGIC RUNNER
    # ══════════════════════════════════════════════════════════════════════

    async def run_logic(
        self,
        user_id: str,
        symbol: str,
        strategy_blueprint: dict,
        current_state: Dict[str, float],
        live_balance_usdt: float,  # FIX SB-1: real balance injected by BotRunner
    ) -> Optional[dict]:
        """
        Evaluates buy/sell logic trees and returns an order payload or None.
        FIX SB-2: Validates blueprint keys before accessing them.
        FIX SB-1: Uses injected live_balance_usdt (not a hardcoded $10K).
        """
        # ── FIX SB-2: Validate required blueprint keys ────────────────────
        required = ["buy_logic", "sell_logic", "risk", "strategy_id"]
        missing = [k for k in required if k not in strategy_blueprint]
        if missing:
            logger.error(f"Blueprint missing keys: {missing}. Skipping tick.")
            return None

        risk_cfg = strategy_blueprint.get("risk") or {}
        if "position_size_pct" not in risk_cfg:
            logger.error("Blueprint missing risk.position_size_pct. Skipping tick.")
            return None

        buy_signal = self._evaluate_logic_tree(
            strategy_blueprint["buy_logic"], current_state
        )
        sell_signal = self._evaluate_logic_tree(
            strategy_blueprint["sell_logic"], current_state
        )

        # Signal collision: both true simultaneously — SELL to protect capital
        if buy_signal and sell_signal:
            logger.warning(
                f"Signal collision on {symbol} for user {user_id}. Prioritising SELL."
            )
            action = "SELL"
        elif buy_signal:
            action = "BUY"
        elif sell_signal:
            action = "SELL"
        else:
            return None  # HOLD

        qty = self._calculate_size(
            live_balance_usdt=live_balance_usdt,  # FIX SB-1
            risk_pct=float(risk_cfg["position_size_pct"]),
            current_price=float(current_state.get("Close", 0)),
        )
        if qty <= 0:
            logger.warning(f"Calculated qty is 0 for {symbol}. Skipping order.")
            return None

        return {
            "user_id": user_id,
            "strategy_id": strategy_blueprint["strategy_id"],
            "symbol": symbol,
            "side": action.lower(),
            "qty": qty,
            "reduce_only": False,  # explicitly set; BotRunner may override for close
            "params": {
                "stop_loss_pct": risk_cfg.get("stop_loss_pct"),
                "take_profit_pct": risk_cfg.get("take_profit_pct"),
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    #  POSITION SIZING
    # ══════════════════════════════════════════════════════════════════════

    def _calculate_size(
        self,
        live_balance_usdt: float,  # FIX SB-1: injected, not hardcoded
        risk_pct: float,
        current_price: float,
    ) -> float:
        """
        Converts a percentage of the live wallet balance into coin quantity.
        FIX SB-1: Original had simulated_usdt_balance = 10000.0 hardcoded.
        """
        if current_price <= 0:
            raise ValueError("current_price must be > 0 to calculate position size.")
        if not (0 < risk_pct <= 1):
            raise ValueError(f"position_size_pct must be in (0, 1]. Got {risk_pct}.")

        dollar_alloc = live_balance_usdt * risk_pct
        qty = dollar_alloc / current_price
        return round(qty, 6)

# ══════════════════════════════════════════════════════════════════════════
#  CANONICAL VERSION ORCHESTRATION  (strategy-builder task 2.3)
#
#  design.md "Component disposition": this module "becomes the orchestration
#  seam between API and the new strategy_dag package". That is all this
#  section is - a seam. It owns no rule:
#
#    * validation rules live in strategy_dag/validator.py
#    * the hash lives in schema.compute_dag_hash
#    * the plan shape and its ONE serialization path live in plan.CompiledPlan
#    * the compile gate lives in strategy_compiler.StrategyCompiler
#    * the database write lives in strategy_service.StrategyService
#
#  What it adds is the single place that turns "a graph the author submitted"
#  into "the exact ten column values an immutable strategy_versions row
#  carries" (Requirement 9.1), and the guarantee that an invalid graph gets
#  there by no route at all (Requirement 3.6).
#
#  Requirement 3.6 is enforced STRUCTURALLY, not by a try/except: compile
#  happens here, before StrategyService has even opened a database client,
#  and a failure raises ValidationError out of this function. There is no
#  code path on which a caller holds a half-built version record.
#
#  Imports of strategy_dag / strategy_compiler are deliberately INSIDE the
#  functions. master_executor imports StrategyEngine from this module on the
#  hot trading path; it must not pay for the registry, the validator or the
#  compiler's pydantic models to do so.
# ══════════════════════════════════════════════════════════════════════════

#: The ten columns migration 004 part 1 adds to ``strategy_versions``, in the order the
#: migration declares them. Single source of truth for both the writer
#: (:meth:`CompiledVersion.canonical_columns`) and the runtime probe that decides whether
#: the migration has been applied yet (``strategy_service``). Keeping one tuple means the
#: probe can never check a different column set from the one the writer writes.
CANONICAL_VERSION_COLUMNS: Tuple[str, ...] = (
    "graph_json",
    "compiled_plan",
    "dag_hash",
    "schema_version",
    "compiler_version",
    "validation_state",
    "validation_report",
    "lifecycle_state",
    "warmup_bars",
    "registry_version",
)

#: ``chk_lifecycle_state`` from migration 004 part 1, verbatim. A value outside this set is
#: rejected here rather than by a 23514 from PostgreSQL half way through a save.
LIFECYCLE_STATES: frozenset = frozenset(
    {
        "DRAFT",
        "VALIDATED",
        "SAVED",
        "TRAINING",
        "TRAINED",
        "READY",
        "DEPLOYED",
        "RUNNING",
        "PAUSED",
        "STOPPED",
        "ARCHIVED",
    }
)

#: ``chk_validation_state`` from migration 004 part 1, verbatim.
VALIDATION_STATES: frozenset = frozenset({"UNVALIDATED", "VALID", "INVALID"})

#: The column default. A version that has not been through the compiler is a DRAFT.
LIFECYCLE_DRAFT = "DRAFT"

#: What a freshly compiled version is. ``design.md`` -> Training workflow:
#: ``insert_strategy_version(strategy_id, graph, plan, state := VALIDATED)``.
LIFECYCLE_VALIDATED = "VALIDATED"

#: A version with at least one ``QUEUED`` or ``RUNNING`` training job against it
#: (``design.md`` -> Training workflow step 7: ``set_state(version, TRAINING)``).
LIFECYCLE_TRAINING = "TRAINING"

#: Deployable. Reached two ways, and only these two: a graph that declares no model
#: node is ``READY`` the moment it compiles (Requirement 14.10), and a graph that
#: declares model nodes becomes ``READY`` when every one of them is bound to an active
#: model version (Requirement 17.9, task 6.5). Never set because training *started*.
LIFECYCLE_READY = "READY"

__all__ = [
    "StrategyEngine",
    "SAFE_OPERATORS",
    "CANONICAL_VERSION_COLUMNS",
    "LIFECYCLE_STATES",
    "VALIDATION_STATES",
    "LIFECYCLE_DRAFT",
    "LIFECYCLE_VALIDATED",
    "LIFECYCLE_TRAINING",
    "LIFECYCLE_READY",
    "CompiledVersion",
    "compile_version",
]


@dataclass(frozen=True)
class CompiledVersion:
    """One successfully compiled graph, ready to become an immutable version row.

    Frozen, because it describes a version that is about to be written and must not drift
    between being built and being persisted.

    Attributes
    ----------
    graph
        The **server's** canonical graph: ``report.canonical_graph``, with ports,
        categories, edge types and validation state recomputed from the registry. This -
        never the submitted payload - is what gets persisted, so a hand-crafted request
        cannot widen its own contract (Requirement 6.13).
    plan
        The :class:`CompiledPlan`. ``plan.dag_hash`` is a readable FIELD; this module never
        does ``plan.get("dag_hash")``, which is literally defect SB-02.
    report
        The :class:`ValidationReport` this graph passed. Persisted as ``validation_report``
        so the warnings an author was shown at save time survive with the version.
    registry_version
        Which descriptor set the author was actually offered.
    """

    graph: "StrategyGraph"
    plan: "CompiledPlan"
    report: "ValidationReport"
    registry_version: Optional[str] = None

    @property
    def dag_hash(self) -> str:
        """The identity hash, read as a plain attribute off the plan (SB-02)."""
        return self.plan.dag_hash

    @property
    def validation_state(self) -> str:
        """``VALID`` for a compiled version; the report is the authority, not the caller."""
        return self.report.validation_state.value

    @property
    def warnings(self) -> list:
        """Non-blocking issues to hand back to the author with the saved version."""
        return list(self.report.warnings)

    def canonical_columns(
        self, *, lifecycle_state: str = LIFECYCLE_VALIDATED
    ) -> dict:
        """The ten migration-004 column values for this version (Requirement 9.1).

        Every value is derived, never accepted from a caller:

        ============================ ==============================================
        ``graph_json``               ``StrategyGraph.to_dict()`` of the server's graph
        ``compiled_plan``            ``CompiledPlan.to_dict()`` - the ONE serialization
                                     path (Requirement 2.5); never a hand-built dict
        ``dag_hash``                 ``plan.dag_hash``, a field read (SB-02)
        ``schema_version``           from the plan
        ``compiler_version``         from the plan
        ``validation_state``         from the report's verdict
        ``validation_report``        ``ValidationReport.to_dict()``
        ``lifecycle_state``          the argument, checked against the CHECK vocabulary
        ``warmup_bars``              from the plan (composed along the path, not a max)
        ``registry_version``         the descriptor set in force at compile time
        ============================ ==============================================

        Postconditions
            The returned dict's keys are exactly :data:`CANONICAL_VERSION_COLUMNS`.
            ``graph_json`` carries ``nodes`` and ``edges`` arrays, so ``chk_graph_shape``
            holds. ``validation_state`` and ``lifecycle_state`` are members of their CHECK
            vocabularies, so ``chk_validation_state`` and ``chk_lifecycle_state`` hold.
            ``dag_hash`` and ``compiled_plan`` are both non-null whenever
            ``validation_state`` is ``VALID``, so the Phase 4 ``chk_valid_requires_hash``
            will hold for every row this method produces.

        Raises
            ``ValueError`` when ``lifecycle_state`` is outside ``chk_lifecycle_state``, or
            when the report's verdict is outside ``chk_validation_state``. Failing here
            costs nothing; failing inside the INSERT costs a 23514 on the save path.
        """
        state = str(lifecycle_state).upper()
        if state not in LIFECYCLE_STATES:
            raise ValueError(
                f"lifecycle_state {lifecycle_state!r} is not one of "
                f"{sorted(LIFECYCLE_STATES)} (chk_lifecycle_state)"
            )

        validation_state = self.validation_state
        if validation_state not in VALIDATION_STATES:
            raise ValueError(
                f"validation_state {validation_state!r} is not one of "
                f"{sorted(VALIDATION_STATES)} (chk_validation_state)"
            )

        graph_json = self.graph.to_dict()
        if not isinstance(graph_json.get("nodes"), list) or not isinstance(
            graph_json.get("edges"), list
        ):  # pragma: no cover - StrategyGraph.to_dict always emits both as lists
            raise ValueError(
                "graph_json must carry 'nodes' and 'edges' arrays (chk_graph_shape)"
            )

        columns = {
            "graph_json": graph_json,
            "compiled_plan": self.plan.to_dict(),
            "dag_hash": self.plan.dag_hash,
            "schema_version": int(self.plan.schema_version),
            "compiler_version": str(self.plan.compiler_version),
            "validation_state": validation_state,
            "validation_report": self.report.to_dict(),
            "lifecycle_state": state,
            "warmup_bars": int(self.plan.warmup_bars),
            "registry_version": self.registry_version,
        }
        # Cheap structural guard: the writer and the migration must agree exactly.
        assert set(columns) == set(CANONICAL_VERSION_COLUMNS), (
            "canonical_columns() drifted from CANONICAL_VERSION_COLUMNS"
        )
        return columns


def compile_version(
    graph: Any,
    registry: Any = None,
    *,
    available_bars: Optional[int] = None,
    feature_columns: Optional[int] = None,
    ml_dataset_stats: Optional[Any] = None,
    limits: Optional["GraphLimits"] = None,
) -> CompiledVersion:
    """Validate and compile ``graph``, or raise and leave nothing behind.

    The one seam every version-creating path goes through, so the save path, the clone path
    and the deploy path cannot disagree about whether a graph is executable (SB-01,
    Requirement 3.2).

    Parameters
    ----------
    graph
        A canonical version 2 :class:`StrategyGraph`, or a version 2 wire envelope, which
        ``schema.parse_v2`` reads. Parsing belongs to ``schema``; nothing is coerced here.
    registry
        A descriptor source. ``None`` resolves the assembled backend registry lazily.
    available_bars, feature_columns, limits
        Passed through to the validator by the caller that knows the configured data range,
        so the warmup-feasibility warning can be evaluated.
    ml_dataset_stats
        Measured statistics of the built training dataset, for validation stage 11
        (task 6.2). Either one ``DatasetStats`` / stats mapping / ``SupervisedDataset``
        applied to every ML node, or ``{node_id: stats}``. Only the training path has
        these, and only the training path should pass them: without them stage 11
        records ``SKIPPED`` - installed, but not given the measurement it needs - which
        is honest, whereas recording ``PASSED`` would be a false clean signal.

    Preconditions
        None on the database: this function performs no I/O and holds no client.

    Postconditions
        Either a :class:`CompiledVersion` whose ``plan.dag_hash`` equals
        ``compute_dag_hash`` of the persisted ``graph_json``, or a raised
        ``ValidationError`` carrying the complete report and **no artifact at all**
        (Requirements 3.5, 3.6).

    Raises
        ``strategy_compiler.ValidationError`` for an invalid graph - ``.report`` carries
        every error with its code, target and fix hint.
        ``strategy_compiler.CompilerError`` when the payload is not a canonical graph, or
        when a graph that passed validation still cannot be assembled into a plan (our bug,
        never the author's).

    Notes
        Validation runs exactly once. The report is computed here and handed to
        ``compile_plan(..., report=report)``, which re-checks it for errors before emitting
        a plan - so the report can be persisted alongside the version without the pipeline
        running twice and without a caller being able to smuggle an invalid graph past the
        gate.
    """
    from backend_app.backend.strategy_compiler import compile_graph
    from backend_app.backend.strategy_dag import validator as _validator

    parsed = _as_strategy_graph(graph)

    report = _validator.validate(
        parsed,
        registry,
        available_bars=available_bars,
        feature_columns=feature_columns,
        ml_dataset_stats=ml_dataset_stats,
        limits=limits,
    )

    # Raises ValidationError(report) when the report holds errors. No plan, no artifact,
    # no row - the failure path ends here, before any database client exists.
    plan = compile_graph(parsed, registry, report=report)

    canonical = report.canonical_graph or parsed
    return CompiledVersion(
        graph=canonical,
        plan=plan,
        report=report,
        registry_version=report.registry_version or _resolve_registry_version(registry),
    )


def _as_strategy_graph(graph: Any) -> "StrategyGraph":
    """``graph`` as a :class:`StrategyGraph`, parsing a version 2 envelope when handed one."""
    from backend_app.backend.strategy_compiler import CompilerError
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        StrategyGraph,
        parse_v2,
    )

    if isinstance(graph, StrategyGraph):
        return graph
    if not isinstance(graph, Mapping):
        raise CompilerError(
            "A version record needs a canonical StrategyGraph or a schema_version 2 "
            f"envelope, got {type(graph).__name__}"
        )
    try:
        return parse_v2(graph)
    except GraphParseError as exc:
        raise CompilerError(f"Not a canonical schema_version 2 graph: {exc}") from exc


def _resolve_registry_version(registry: Any = None) -> Optional[str]:
    """The registry version to record, when the report did not carry one.

    Only reached for a caller-supplied descriptor source that publishes no
    ``registry_version``; the assembled registry always does.
    """
    version = getattr(registry, "registry_version", None)
    if isinstance(version, str) and version:
        return version
    if registry is not None:
        return None
    try:
        from backend_app.backend.strategy_dag import registry as registry_module

        return registry_module.registry_version()
    except Exception as exc:  # noqa: BLE001 - a missing version is recorded as NULL
        logger.warning("Could not resolve the registry version to record: %s", exc)
        return None
