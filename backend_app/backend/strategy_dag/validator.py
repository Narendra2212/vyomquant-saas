"""
backend/strategy_dag/validator.py - the one rule engine over a canonical graph.

A graph goes in, a :class:`ValidationReport` comes out. Nothing is persisted, nothing is
fetched, nothing is executed. This is the module every mutating Builder request routes
through - validate, save, clone, compile, deploy - so a rule cannot be enforced on one
path and skipped on another (Requirement 6.12).

Exposes
-------
``is_edge_legal(graph, edge, registry)``   rules R1-R8 for one edge; ``None`` when legal
``find_cycle(nodes, edges)``               iterative DFS returning the true cycle path
``creates_cycle(graph, edge)``             would adding ``edge`` close a loop?
``topological_order(graph)``               deterministic Kahn order + parallel levels
``compose_warmup(graph, registry)``        per-node and total warmup, composed along paths
``validate(graph, ...)``                   the collect-all stage pipeline, stages 1-11
``ValidationReport``                       the structured report, with ``to_dict()``
``GraphLimits`` / ``LIMITS``               the capacity bounds, as data
``validate_no_lookahead(graph, ...)``      stage 10b: structural leakage detection
``register_leakage_stage`` / ``register_ml_readiness_stage``
                                           the named seams stages 10b and 11 are installed
                                           through; **both** are installed at import
                                           (10b in Phase 5, 11 in Phase 6), so
                                           ``pending_stages()`` is empty
``StageSkipped``                           raised by a seam that is implemented but was
                                           not given an input it needs

Consolidation, not authorship
-----------------------------
Every rule this module applies is *read* from a surface that already owns it:

* R4 type compatibility calls :func:`registry.compatible`, the same function whose matrix
  is published to the client. There is no second matrix here (Requirement 6.9).
* R3 port existence calls ``BlockDescriptor.input_port`` / ``output_port``.
* R5 category adjacency reads ``BlockDescriptor.allowed_successor_categories``.
* R6 terminality reads ``BlockDescriptor.is_terminal``.
* Stage 3 declarative parameter checks call :func:`block_specs.validate_block_params`, and
  cross-field checks call ``BlockDescriptor.cross_field_issues`` - this module owns no
  range checker and no ``ParamSpec``.
* Warmup per node calls ``BlockDescriptor.warmup(params)``.
* Stage 11 calls :func:`ml_training_policy.check_ml_data_requirements`, and the model
  figures it needs are read off ``BlockDescriptor.metadata["model"]`` - which
  ``registry.descriptor_from_model_spec`` carries through verbatim from
  ``ml_models.ModelSpec``. This module owns no minimum-row figure and no cap.
* Issues are built with :func:`schema.make_issue`, so a migration issue and a validation
  issue are the same shape and fold into one report untranslated.

Severity vocabulary (one, not two)
----------------------------------
``schema`` emits lower-case ``"error"`` / ``"warning"``; ``registry`` emits upper-case
``"ERROR"`` on its cross-field issues. The design's structured error contract shows
lower-case, so **lower-case is the canonical vocabulary** and every issue entering this
module is passed through :func:`normalise_severity` at the ingest point. No caller
downstream has to know two spellings.

Never trust the client
----------------------
Node port lists, node categories, edge port types, ``validation_state``, the identity hash
and the execution order are **recomputed** from the registry descriptors and returned on
``report.canonical_graph``. The legality rules read the descriptor, never the node's
declared ports, so a hand-crafted payload cannot widen its own contract (Requirement 6.13).
Where a submitted value disagreed with the recomputed one the report carries a warning
naming the override, so tampering is visible rather than merely ineffective.

Purity
------
Importing this module pulls in ``schema``, ``block_specs``, ``registry`` and - for stage 11
only - ``backend.ml_training_policy``: no FastAPI, no database handle, no CCXT.
``registry.get_registry()`` is called *inside* functions, so importing the validator never
triggers registry assembly (Requirement 21.10).

``ml_training_policy`` lives in ``backend/``, one level above this package, and is imported
**inside** :func:`install_default_stage_hooks` rather than at module scope. Two reasons,
both load-bearing: the reverse dependency (that module reads ``LIMITS`` from here) would be
a cycle at module scope, and that module keeps ``ml_models``, ``ml_safety``,
``entitlement_engine`` and ``subscription_engine`` behind its own lazy seams, so installing
stage 11 costs this package no new import weight.
``tests/test_strategy_dag_architecture.py`` measures both facts in a fresh interpreter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from backend_app.backend.strategy_dag import registry as _registry
from backend_app.backend.strategy_dag.block_specs import validate_block_params
from backend_app.backend.strategy_dag.schema import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    Port,
    PortType,
    StrategyGraph,
    SUPPORTED_SCHEMA_VERSIONS,
    ValidationState,
    compute_dag_hash,
    make_issue,
)
from backend_app.backend.strategy_dag.schema import (
    CODE_CATEGORY_REMAPPED,
    CODE_UNRESOLVED_BLOCK,
)
from backend_app.backend.strategy_dag.schema import (
    migration_issues as _migration_issues,
)

logger = logging.getLogger("StrategyDAG.Validator")


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None`` when it cannot be reached.

    Strategy-builder task 9.1, Requirement 24.1. Imported **inside** this function for the
    same reason ``ml_training_policy`` is: a module-scope import would add an edge from the
    pure core to ``backend/`` for every consumer of this package, and the Purity note above
    is a contract. ``backend/metrics.py`` itself imports only the standard library, so this
    costs no weight when it is called - but the guard stays, because instrumentation must
    never be what fails a validation.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None

#: Bumped when the *set* of rules or their codes changes, so a stored report can be told
#: apart from one produced by a later rule set.
VALIDATOR_VERSION = "1.0.0"

__all__ = [
    "VALIDATOR_VERSION",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "normalise_severity",
    "GraphLimits",
    "LIMITS",
    "LIMIT_LABELS",
    "capacity_issues",
    "measured_feature_columns",
    "ValidationReport",
    "ValidationContext",
    "is_edge_legal",
    "find_cycle",
    "creates_cycle",
    "cycle_path_with_edge",
    "topological_order",
    "compose_warmup",
    "validate",
    "validate_edge",
    "STAGE_LEAKAGE",
    "STAGE_ML_READINESS",
    "STAGE_NAMES",
    "STATUS_PASSED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_NOT_IMPLEMENTED",
    "StageSkipped",
    "register_leakage_stage",
    "register_ml_readiness_stage",
    "clear_stage_hooks",
    "install_default_stage_hooks",
    "pending_stages",
    # stage 10b: leakage
    "validate_no_lookahead",
    "leakage_stage",
    "CODE_LOOKAHEAD_SHIFT",
    "CODE_LEAKY_FEATURE_INTO_MODEL",
    "CODE_GLOBAL_STATISTIC_LEAK",
    "GLOBAL_STATISTIC_MODE",
    "REVIEW_DISCHARGED_BY_FLAG",
    # stage 11: ML readiness
    "ml_readiness_stage",
]


# ---------------------------------------------------------------------------
# Severity: one vocabulary
# ---------------------------------------------------------------------------

#: Spellings seen on the surfaces that feed this module, mapped onto the canonical two.
_SEVERITY_ALIASES: Dict[str, str] = {
    "error": SEVERITY_ERROR,
    "errors": SEVERITY_ERROR,
    "fatal": SEVERITY_ERROR,
    "critical": SEVERITY_ERROR,
    "warning": SEVERITY_WARNING,
    "warn": SEVERITY_WARNING,
}


def normalise_severity(value: Any) -> str:
    """Map any accepted severity spelling onto the canonical lower-case vocabulary.

    ``registry`` publishes ``"ERROR"`` on its cross-field issues and ``schema`` publishes
    ``"error"``; both mean the same thing and a report must not carry both spellings.

    Postconditions
        The result is exactly ``"error"`` or ``"warning"``. An unrecognised severity
        resolves to ``"error"``: a rule whose severity cannot be read must block rather
        than pass silently.
    """
    if isinstance(value, str):
        return _SEVERITY_ALIASES.get(value.strip().lower(), SEVERITY_ERROR)
    return SEVERITY_ERROR


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

# -- stage 1: structural ----------------------------------------------------
CODE_EMPTY_GRAPH = "EMPTY_GRAPH"
CODE_DUPLICATE_NODE_ID = "DUPLICATE_NODE_ID"
CODE_DUPLICATE_EDGE_ID = "DUPLICATE_EDGE_ID"
CODE_UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"

# -- capacity limits (design.md -> Performance; Requirement 25.4) -----------
CODE_NODE_LIMIT_EXCEEDED = "NODE_LIMIT_EXCEEDED"
CODE_EDGE_LIMIT_EXCEEDED = "EDGE_LIMIT_EXCEEDED"
CODE_ML_NODE_LIMIT_EXCEEDED = "ML_NODE_LIMIT_EXCEEDED"
CODE_FEATURE_NODE_LIMIT_EXCEEDED = "FEATURE_NODE_LIMIT_EXCEEDED"
CODE_FEATURE_COLUMN_LIMIT_EXCEEDED = "FEATURE_COLUMN_LIMIT_EXCEEDED"
CODE_PORT_FAN_IN_LIMIT_EXCEEDED = "PORT_FAN_IN_LIMIT_EXCEEDED"

# -- stage 3: parameters ----------------------------------------------------
CODE_PARAM_REQUIRED_MISSING = "PARAM_REQUIRED_MISSING"
CODE_PARAM_OUT_OF_RANGE = "PARAM_OUT_OF_RANGE"
CODE_PARAM_NOT_IN_OPTIONS = "PARAM_NOT_IN_OPTIONS"
CODE_PARAM_TYPE_INVALID = "PARAM_TYPE_INVALID"
CODE_PARAM_INVALID = "PARAM_INVALID"
CODE_PARAM_UNKNOWN = "PARAM_UNKNOWN"
#: Re-used from the registry so a cross-field problem has one code everywhere.
CODE_PARAM_CROSS_FIELD = _registry.CODE_CROSS_FIELD

# -- stage 4: edge legality, rules R1-R8 ------------------------------------
CODE_EDGE_ENDPOINT_UNKNOWN = "EDGE_ENDPOINT_UNKNOWN"      # R1
CODE_SELF_LOOP = "SELF_LOOP"                              # R2
CODE_UNKNOWN_SOURCE_PORT = "UNKNOWN_SOURCE_PORT"          # R3
CODE_UNKNOWN_TARGET_PORT = "UNKNOWN_TARGET_PORT"          # R3
CODE_TYPE_MISMATCH = "TYPE_MISMATCH"                      # R4
CODE_ILLEGAL_CATEGORY_FLOW = "ILLEGAL_CATEGORY_FLOW"      # R5
CODE_TERMINAL_HAS_NO_OUTPUT = "TERMINAL_HAS_NO_OUTPUT"    # R6
CODE_PORT_ALREADY_CONNECTED = "PORT_ALREADY_CONNECTED"    # R7
CODE_CYCLE = "CYCLE"                                      # R8

# -- stage 5 / 7 / 8 / 9 ----------------------------------------------------
CODE_REQUIRED_INPUT_MISSING = "REQUIRED_INPUT_MISSING"
CODE_ORPHAN_NODE = "ORPHAN_NODE"
CODE_MISSING_REQUIRED_CATEGORY = "MISSING_REQUIRED_CATEGORY"
CODE_ACTION_UNREACHABLE = "ACTION_UNREACHABLE_FROM_DATA"
CODE_ACTION_INPUT_PROVENANCE = "ACTION_INPUT_PROVENANCE"
CODE_MULTI_SYMBOL_ACTION_PATH = "MULTI_SYMBOL_ACTION_PATH"

# -- stage 10a: warmup ------------------------------------------------------
CODE_WARMUP_EXCEEDS_HISTORY = "WARMUP_EXCEEDS_HISTORY"

# -- stage 10b: leakage (design.md -> Data leakage protection, mechanism 1) --
#: A ``shift`` block reading forward in time.
CODE_LOOKAHEAD_SHIFT = "LOOKAHEAD_SHIFT"
#: A block whose output encodes a future bar, on a path into a model.
CODE_LEAKY_FEATURE_INTO_MODEL = "LEAKY_FEATURE_INTO_MODEL"
#: A statistic computed over the whole series, which sees the test split.
CODE_GLOBAL_STATISTIC_LEAK = "GLOBAL_STATISTIC_LEAK"

#: The ``mode`` value that makes a z-score or a normalisation read the whole series.
#: ``feature_engineering`` keeps this value *representable* precisely so this stage can
#: reject it rather than the parameter enum silently forbidding it - a rejected graph
#: teaches the author why, an absent option does not.
GLOBAL_STATISTIC_MODE = "global"

#: Capability flags by which a ``REVIEW_REQUIRED`` block declares its review already
#: discharged by construction.
#:
#: The distinction this encodes is the one mechanism 1 is actually about. Mechanism 1 is
#: *structural*: it rejects a block whose **output value at bar t encodes a bar after t**.
#: Ichimoku's ``chikou`` is exactly that and can never be made safe. ``feat_standardize``
#: is flagged ``REVIEW_REQUIRED`` for a different reason - a scaler fit over the full
#: series would leak validation and test statistics - and that is a *procedural* risk of
#: how the block is fitted, not a property of the values it emits. It is discharged by
#: mechanism 2 (chronological, embargoed splits) together with the block's own ``fit_on``
#: parameter, whose only representable value is ``train_split``.
#:
#: Treating the two identically would make ``feat_standardize`` unable to feed a model,
#: which is the only thing it exists to do - a false positive that would delete the
#: design's own recommended feature pipeline. Treating neither as an error would ship a
#: look-ahead feature into a model. So the rule errors by default and clears only on an
#: explicit declaration, which the author cannot add from the client.
REVIEW_DISCHARGED_BY_FLAG: FrozenSet[str] = frozenset({"fit_on_train_split"})

# -- server-side recomputation (Requirement 6.13) ---------------------------
CODE_PORT_CONTRACT_RECOMPUTED = "PORT_CONTRACT_RECOMPUTED"
CODE_EDGE_TYPE_RECOMPUTED = "EDGE_TYPE_RECOMPUTED"
CODE_VALIDATION_STATE_RECOMPUTED = "VALIDATION_STATE_RECOMPUTED"

#: Capability flag a block declares to say it allocates across symbols. No block declares
#: it yet; the flag is the seam Requirement 7.10 checks against so the rule does not have
#: to name a block id it would then have to keep in step.
PORTFOLIO_ALLOCATION_FLAG = "portfolio_allocation"

#: Output port types that carry a trade decision (Requirement 7.9).
TRADE_SIGNAL_TYPES: Tuple[PortType, ...] = (PortType.SIGNAL, PortType.TRADE_INTENT)


# ---------------------------------------------------------------------------
# Capacity limits, as data (design.md -> Performance; Requirement 25.4)
# ---------------------------------------------------------------------------


#: Human label per limit, so an error names the limit the way the design's table does.
LIMIT_LABELS: Mapping[str, str] = {
    "max_nodes": "Max nodes per strategy",
    "max_edges": "Max edges per strategy",
    "max_ml_nodes": "Max ML nodes per strategy",
    "max_feature_nodes": "Max FEATURE_ENGINEERING nodes per strategy",
    "max_feature_columns": "Max feature columns",
    "max_fan_in_per_variadic_port": "Max fan-in per variadic port",
}


@dataclass(frozen=True)
class GraphLimits:
    """The capacity bounds a graph must respect.

    Values are the design's table. They are data rather than literals inside the check so
    a caller (a test, or a tenant tier) can pass a narrower set without a second
    implementation of the rule.
    """

    max_nodes: int = 200
    max_edges: int = 400
    max_ml_nodes: int = 4
    max_feature_nodes: int = 40
    max_feature_columns: int = 200
    max_fan_in_per_variadic_port: int = 16

    def label(self, key: str) -> str:
        """The human label of one limit, for the message."""
        return LIMIT_LABELS.get(key, key)


#: The platform limits. Passed explicitly wherever they are applied.
LIMITS = GraphLimits()


def measured_feature_columns(stats: Optional[Any]) -> Optional[int]:
    """The widest measured feature-matrix width in ``stats``, or ``None`` (task 9.4).

    ``stats`` is whatever :func:`validate`'s ``ml_dataset_stats`` accepts: one
    ``SupervisedDataset``, one measured stats mapping (``dataset.stats()``), or
    ``{node_id: stats}`` for a graph with several model nodes. The widest of them is the
    binding one, because :data:`GraphLimits.max_feature_columns` bounds a single matrix and
    a graph is refused if *any* of its matrices exceeds it.

    Deliberately total and exception-free: an unreadable shape returns ``None``, which
    leaves the capacity check unable to run rather than inventing a count. A fabricated
    column count would either refuse a legal graph or pass an illegal one, and both are
    worse than the stage saying it had no measurement. Nothing is estimated here - a bar
    count minus a warmup is not a column count.
    """
    if stats is None:
        return None

    def _one(candidate: Any) -> Optional[int]:
        for attribute in ("usable_feature_columns", "n_columns"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            return int(value)
        if isinstance(candidate, Mapping):
            for key in ("usable_feature_columns", "columns", "n_columns"):
                value = candidate.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                return int(value)
        return None

    try:
        direct = _one(stats)
        if direct is not None:
            return direct
        if isinstance(stats, Mapping):
            widths = [
                width
                for width in (_one(value) for value in stats.values())
                if width is not None
            ]
            if widths:
                return max(widths)
    except Exception:  # noqa: BLE001 - a measurement that cannot be read is no measurement
        return None
    return None


def capacity_issues(
    graph: StrategyGraph,
    *,
    limits: Optional[GraphLimits] = None,
    descriptors: Optional[Mapping[str, Any]] = None,
    feature_columns: Optional[int] = None,
) -> Tuple[Dict[str, Any], ...]:
    """Every capacity bound ``graph`` exceeds, as structured issues (Requirement 25.4).

    The **one** implementation of the capacity rule. :func:`validate` applies it as part of
    stage 1, and a caller that wants the same refusal without paying for the other ten
    stages - a boundary sizing a payload before it compiles it, a runtime that has just
    measured a feature matrix - applies it directly and gets the *same* codes, the same
    messages and the same permitted values. A second copy of these six numbers is the
    defect this signature exists to prevent; :data:`LIMITS` stays the only table.

    ``descriptors`` may be omitted, in which case the ML and FEATURE_ENGINEERING counts
    fall back to each node's declared ``category``. That is what the wire form carries, so
    an omitted registry narrows the answer rather than skipping the check.

    Every issue names the limit (``field_name`` plus its human label in ``message``) and
    its permitted value (``expected``) beside the graph's own figure (``actual``).
    """
    resolved_limits = limits if limits is not None else LIMITS
    report = ValidationReport()
    _stage_limits(
        graph,
        report,
        resolved_limits,
        descriptors if descriptors is not None else {},
        feature_columns,
    )
    return tuple(report.issues)


# ---------------------------------------------------------------------------
# Stage identity
# ---------------------------------------------------------------------------

STAGE_STRUCTURAL = "structural"
STAGE_REGISTRY = "registry_resolution"
STAGE_PARAMETERS = "parameters"
STAGE_PORT_CONTRACTS = "port_contracts"
STAGE_REQUIRED_INPUTS = "required_inputs"
STAGE_ACYCLICITY = "acyclicity"
STAGE_REACHABILITY = "reachability"
STAGE_EXECUTION_PATH = "execution_path"
STAGE_ACTION_PROVENANCE = "action_provenance"
STAGE_WARMUP = "warmup"
#: The Phase 5 seam. Named here so the report can say the stage exists and has not run.
STAGE_LEAKAGE = "leakage"
#: The Phase 6 seam.
STAGE_ML_READINESS = "ml_readiness"

#: Stage number -> name, in the order the design's pipeline diagram runs them. Stage 10
#: covers warmup *and* leakage, which is why ``leakage`` shares its number.
STAGE_NAMES: Tuple[Tuple[int, str], ...] = (
    (1, STAGE_STRUCTURAL),
    (2, STAGE_REGISTRY),
    (3, STAGE_PARAMETERS),
    (4, STAGE_PORT_CONTRACTS),
    (5, STAGE_REQUIRED_INPUTS),
    (6, STAGE_ACYCLICITY),
    (7, STAGE_REACHABILITY),
    (8, STAGE_EXECUTION_PATH),
    (9, STAGE_ACTION_PROVENANCE),
    (10, STAGE_WARMUP),
    (10, STAGE_LEAKAGE),
    (11, STAGE_ML_READINESS),
)

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
#: The stage is implemented but could not run: an input it needs was not supplied, or an
#: earlier stage's failure makes its result meaningless.
STATUS_SKIPPED = "SKIPPED"
#: The stage is declared and has no implementation yet. It neither passes nor fails, and
#: it never contributes to ``valid``. See :func:`register_leakage_stage`.
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


# ---------------------------------------------------------------------------
# The two named seams (stages 10-leakage and 11)
# ---------------------------------------------------------------------------

#: What each seam will check, quoted in the report so a caller reading a report knows the
#: stage is pending rather than clean.
_SEAM_DESCRIPTIONS: Dict[str, str] = {
    STAGE_LEAKAGE: (
        "Structural leakage detection (design.md -> Data leakage protection, mechanism 1: "
        "validate_no_lookahead): negative 'shift', REVIEW_REQUIRED leakage_risk blocks on a "
        "path into an ML_DL node, and whole-series statistics. This stage LANDED in Phase 5 "
        "and is installed at import; seeing this text means it was explicitly uninstalled "
        "with clear_stage_hooks(). Restore it with install_default_stage_hooks()."
    ),
    STAGE_ML_READINESS: (
        "ML readiness (design.md -> Minimum-data gate, check_ml_data_requirements): feature "
        "columns, usable rows after warmup and label horizon, sequence-window sufficiency "
        "and model trainability. This stage LANDED in Phase 6 and is installed at import; "
        "seeing this text means it was explicitly uninstalled with clear_stage_hooks(). "
        "Restore it with install_default_stage_hooks(). Note that a graph validated without "
        "'ml_dataset_stats' reports this stage SKIPPED, not NOT_IMPLEMENTED - the stage "
        "exists and was not given the measured statistics it needs."
    ),
}

class StageSkipped(Exception):
    """A seam stage is implemented but was not given an input it needs.

    Raising this is how a hook says ``SKIPPED`` rather than ``PASSED``. The distinction is
    the whole point of the seam contract: "checked and clean" and "could not check" must
    not produce the same report, or a graph nobody measured reads as a graph that passed.

    ``NOT_IMPLEMENTED`` remains a *third* state, and it is not this one - it means no
    implementation is installed at all. Stage 11 is the case that needs all three: it is
    implemented (not NOT_IMPLEMENTED), and it can only run when the caller supplies
    measured dataset statistics a graph does not carry (so SKIPPED, not PASSED).

    The message is carried into ``report.stages[...]["detail"]`` verbatim, so it must say
    what was missing and what the reader should therefore not conclude.
    """


#: ``hook(context) -> Sequence[issue]``. Issues are folded into the report untranslated
#: apart from severity normalisation, so a hook uses ``schema.make_issue`` and nothing else.
#: A hook may raise :class:`StageSkipped` instead of returning, and the stage is then
#: recorded ``SKIPPED`` with the exception's message as its detail.
StageHook = Callable[["ValidationContext"], Sequence[Mapping[str, Any]]]

_STAGE_HOOKS: Dict[str, Optional[StageHook]] = {
    STAGE_LEAKAGE: None,
    STAGE_ML_READINESS: None,
}


def register_leakage_stage(hook: Optional[StageHook]) -> None:
    """Install the Phase 5 leakage stage.

    This is a seam, not a stub: until a hook is installed the pipeline reports the stage as
    ``NOT_IMPLEMENTED`` and lists it in ``report.pending_stages``. It is deliberately not
    given a body that returns no issues, because "no leakage found" and "leakage never
    looked for" must not be the same report - the second one would read as a clean graph
    and ship a look-ahead feature into a model.
    """
    _STAGE_HOOKS[STAGE_LEAKAGE] = hook


def register_ml_readiness_stage(hook: Optional[StageHook]) -> None:
    """Install the Phase 6 ML readiness stage. Same contract as the leakage seam.

    Installed at import by :func:`install_default_stage_hooks`, for the same reason stage
    10b is: a gate a caller has to remember to switch on is a gate some caller ships
    without. The hook decides for itself whether it *can* run - see :class:`StageSkipped`.
    """
    _STAGE_HOOKS[STAGE_ML_READINESS] = hook


def clear_stage_hooks() -> None:
    """Uninstall both seams. For tests that install one.

    This also removes the stage-10b leakage implementation installed at import, so a test
    that clears hooks and then expects leakage detection must call
    :func:`install_default_stage_hooks` to put it back.
    """
    for name in _STAGE_HOOKS:
        _STAGE_HOOKS[name] = None


def pending_stages() -> Tuple[str, ...]:
    """The declared stages that have no implementation installed, in pipeline order."""
    return tuple(name for name, hook in _STAGE_HOOKS.items() if hook is None)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Every problem found in one pass, plus the values recomputed server-side.

    ``valid`` is false when *any* error was collected; a report holding only warnings is
    valid (Requirement 8.5). ``dag_hash`` is populated only for a valid graph, matching the
    design's contract where an invalid graph reports ``"dag_hash": null``.
    """

    valid: bool = True
    dag_hash: Optional[str] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    stages: List[Dict[str, Any]] = field(default_factory=list)
    #: The graph with ports, categories, edge types and validation state recomputed from
    #: the registry. This - not the submitted graph - is what a caller should persist.
    canonical_graph: Optional[StrategyGraph] = None
    execution_order: Optional[List[str]] = None
    execution_levels: Optional[List[List[str]]] = None
    warmup_bars: Optional[int] = None
    warmup_by_node: Dict[str, int] = field(default_factory=dict)
    registry_version: Optional[str] = None
    validator_version: str = VALIDATOR_VERSION

    # -- accumulation -----------------------------------------------------
    def add(self, issue: Mapping[str, Any]) -> Dict[str, Any]:
        """Fold one structured issue in, routing it by its normalised severity."""
        entry = dict(issue)
        entry["severity"] = normalise_severity(entry.get("severity"))
        if entry["severity"] == SEVERITY_ERROR:
            self.errors.append(entry)
            self.valid = False
        else:
            self.warnings.append(entry)
        return entry

    def extend(self, issues: Iterable[Mapping[str, Any]]) -> None:
        for issue in issues or ():
            self.add(issue)

    def record_stage(
        self, number: int, name: str, status: str, detail: str = "", **extra: Any
    ) -> None:
        entry: Dict[str, Any] = {
            "stage": number,
            "name": name,
            "status": status,
            "detail": detail,
        }
        entry.update(extra)
        self.stages.append(entry)

    # -- views ------------------------------------------------------------
    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def issues(self) -> List[Dict[str, Any]]:
        """Errors then warnings, in collection order."""
        return list(self.errors) + list(self.warnings)

    def codes(self) -> List[str]:
        """Every code in the report, errors first. For tests and telemetry."""
        return [str(issue.get("code")) for issue in self.issues]

    @property
    def pending_stages(self) -> List[str]:
        """Declared stages that did not run because they have no implementation yet."""
        return [
            str(entry["name"])
            for entry in self.stages
            if entry.get("status") == STATUS_NOT_IMPLEMENTED
        ]

    @property
    def validation_state(self) -> ValidationState:
        return ValidationState.VALID if self.valid else ValidationState.INVALID

    def to_dict(self) -> Dict[str, Any]:
        """The wire form from ``design.md`` -> Structured error contract."""
        return {
            "valid": self.valid,
            "dag_hash": self.dag_hash,
            "validation_state": self.validation_state.value,
            "errors": [dict(issue) for issue in self.errors],
            "warnings": [dict(issue) for issue in self.warnings],
            "summary": dict(self.summary),
            "stages": [dict(entry) for entry in self.stages],
            "pending_stages": self.pending_stages,
            "execution_order": (
                None if self.execution_order is None else list(self.execution_order)
            ),
            "execution_levels": (
                None
                if self.execution_levels is None
                else [list(level) for level in self.execution_levels]
            ),
            "registry_version": self.registry_version,
            "validator_version": self.validator_version,
        }


@dataclass(frozen=True)
class ValidationContext:
    """Everything the pipeline derived, handed to a stage hook.

    A hook gets the derived indices rather than recomputing them, so an extension stage
    cannot disagree with the pipeline about who feeds whom.
    """

    graph: StrategyGraph
    registry: Any
    descriptors: Mapping[str, Any]          # node_id -> BlockDescriptor | None
    node_index: Mapping[str, NodeSpec]
    adjacency: Mapping[str, Tuple[str, ...]]         # node_id -> downstream node ids
    reverse_adjacency: Mapping[str, Tuple[str, ...]]  # node_id -> upstream node ids
    inbound: Mapping[str, Mapping[str, Tuple[EdgeSpec, ...]]]  # node -> port -> edges
    execution_order: Optional[Tuple[str, ...]]
    warmup_by_node: Mapping[str, int]
    warmup_bars: Optional[int]
    available_bars: Optional[int]
    feature_columns: Optional[int]
    limits: GraphLimits
    #: Measured dataset statistics for stage 11, or ``None``. Either one stats mapping /
    #: ``SupervisedDataset`` applied to every ML node, or ``{node_id: stats}``. ``None``
    #: is not "no problems found" - it makes stage 11 report ``SKIPPED``.
    ml_dataset_stats: Optional[Any] = None

    def nodes_in_category(self, category: BlockCategory) -> Tuple[str, ...]:
        """Node ids whose *descriptor* category is ``category`` (never the client's)."""
        return tuple(
            node_id
            for node_id, descriptor in self.descriptors.items()
            if _category_of(self.node_index[node_id], descriptor) is category
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _resolve_registry(registry: Any = None) -> Any:
    """Return the registry to read descriptors from, assembling the real one lazily.

    Assembly happens on first use inside a call, never at import: the validator must stay
    importable by processes that only parse graphs.
    """
    if registry is not None:
        return registry
    return _registry.get_registry()


def _descriptor(registry: Any, block_id: Any) -> Any:
    getter = getattr(registry, "get", None)
    if callable(getter):
        try:
            return getter(block_id)
        except Exception:  # noqa: BLE001 - a broken lookup is a reported state
            logger.exception("Registry lookup failed for block_id %r", block_id)
            return None
    return None


def _category_of(node: NodeSpec, descriptor: Any) -> BlockCategory:
    """The authoritative category: the descriptor's, falling back to the node's.

    The fallback exists only for a node whose block did not resolve; such a node always
    carries an ``UNRESOLVED_BLOCK`` error, so the fallback can never reach a valid graph.
    """
    category = getattr(descriptor, "category", None)
    if isinstance(category, BlockCategory):
        return category
    return node.category


def _port_label(node_id: str, port_name: str) -> str:
    return f"{node_id}.{port_name}"


def _declared_ports(descriptor: Any, kind: str) -> Tuple[Port, ...]:
    ports = getattr(descriptor, kind, ()) or ()
    return tuple(ports)


def _port_names(descriptor: Any, kind: str) -> List[str]:
    return [port.name for port in _declared_ports(descriptor, kind)]


def _build_adjacency(
    edges: Sequence[EdgeSpec], known: Optional[Set[str]] = None
) -> Dict[str, Tuple[str, ...]]:
    """``source -> downstream node ids``, de-duplicated and ordered deterministically.

    Endpoints outside ``known`` are dropped: an unresolved endpoint is reported by stage 1
    and must not make the traversal invent a node.
    """
    out: Dict[str, List[str]] = {}
    seen: Set[Tuple[str, str]] = set()
    for edge in edges:
        if known is not None and (edge.source not in known or edge.target not in known):
            continue
        key = (edge.source, edge.target)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(edge.source, []).append(edge.target)
    return {source: tuple(targets) for source, targets in out.items()}


def _build_inbound(
    edges: Sequence[EdgeSpec],
) -> Dict[str, Dict[str, Tuple[EdgeSpec, ...]]]:
    """``target -> target_port -> edges``. A variadic port legitimately holds several."""
    out: Dict[str, Dict[str, List[EdgeSpec]]] = {}
    for edge in edges:
        out.setdefault(edge.target, {}).setdefault(edge.target_port, []).append(edge)
    return {
        node_id: {port: tuple(items) for port, items in ports.items()}
        for node_id, ports in out.items()
    }


def _reachable_from(
    adjacency: Mapping[str, Tuple[str, ...]], roots: Iterable[str]
) -> Set[str]:
    """Every node reachable from ``roots``, iteratively. Roots are included."""
    seen: Set[str] = set()
    stack: List[str] = [root for root in roots]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(
            target for target in adjacency.get(current, ()) if target not in seen
        )
    return seen


# ---------------------------------------------------------------------------
# Cycle detection (design.md -> Cycle detection)
# ---------------------------------------------------------------------------


_WHITE, _GREY, _BLACK = 0, 1, 2


def find_cycle(
    nodes: Sequence[Any], edges: Sequence[EdgeSpec]
) -> List[str]:
    """Return one real cycle as an ordered node sequence, or ``[]`` for a DAG.

    ``nodes`` may be :class:`NodeSpec` objects or plain id strings.

    Preconditions
        Edge endpoints that are not in ``nodes`` are ignored rather than trusted; stage 1
        reports them.

    Postconditions
        A non-empty result is a genuine cycle: consecutive ids are joined by an edge and
        the first id equals the last, so the caller can print it as
        ``a -> b -> c -> a``. Empty exactly when the graph is acyclic.

    Loop invariants
        ``path`` always holds the current GREY chain from the search root to the tip, which
        is why the reported sequence is the true cycle and not an arbitrary visited set. A
        node coloured BLACK participates in no cycle reachable from it, so it is never
        re-entered.

    This is the **iterative** form the design mandates: the recursion in
    ``strategy_compiler._has_cycle`` blows the interpreter's stack on a long chain, and a
    200-node strategy is allowed to be one long chain.
    """
    node_ids: List[str] = [
        item if isinstance(item, str) else getattr(item, "id") for item in nodes
    ]
    known = set(node_ids)
    adjacency = _build_adjacency(edges, known)

    state: Dict[str, int] = {node_id: _WHITE for node_id in node_ids}

    for root in node_ids:
        if state.get(root, _WHITE) != _WHITE:
            continue

        state[root] = _GREY
        path: List[str] = [root]
        # Each frame is (node_id, iterator over that node's remaining successors). The
        # iterator is what makes the explicit stack equivalent to recursion: resuming a
        # frame continues where it left off instead of restarting the scan.
        stack: List[Tuple[str, Any]] = [(root, iter(adjacency.get(root, ())))]

        while stack:
            current, successors = stack[-1]
            descended = False
            for nxt in successors:
                colour = state.get(nxt, _WHITE)
                if colour == _GREY:
                    # nxt is on the current path: the cycle is that suffix, closed.
                    start = path.index(nxt)
                    return path[start:] + [nxt]
                if colour == _WHITE:
                    state[nxt] = _GREY
                    path.append(nxt)
                    stack.append((nxt, iter(adjacency.get(nxt, ()))))
                    descended = True
                    break
                # BLACK: fully explored and cycle-free, nothing to do.
            if not descended:
                stack.pop()
                path.pop()
                state[current] = _BLACK

    return []


def creates_cycle(graph: StrategyGraph, edge: EdgeSpec) -> bool:
    """Would adding ``edge`` to ``graph`` close a loop? (rule R8)

    Safe to call with an edge that is already in the graph - the reachability question is
    the same either way - so one function serves both connect-time and validate-time.
    """
    if edge.source == edge.target:
        return True
    known = {node.id for node in graph.nodes}
    adjacency = _build_adjacency(list(graph.edges) + [edge], known)
    return edge.source in _reachable_from(adjacency, (edge.target,))


def cycle_path_with_edge(graph: StrategyGraph, edge: EdgeSpec) -> List[str]:
    """The cycle ``edge`` would close, as an ordered node sequence."""
    edges = list(graph.edges)
    if all(existing.id != edge.id for existing in edges):
        edges.append(edge)
    return find_cycle(graph.nodes, edges)


# ---------------------------------------------------------------------------
# Deterministic topological order (shared with the compiler)
# ---------------------------------------------------------------------------


def topological_order(
    graph: StrategyGraph,
) -> Tuple[Optional[List[str]], Optional[List[List[str]]]]:
    """Kahn's algorithm with a sorted tie-break: ``(order, levels)``, or ``(None, None)``.

    Postconditions
        ``order`` holds every node exactly once and every node appears after all of its
        predecessors. ``levels[i]`` holds the nodes whose predecessors are all in earlier
        levels, which is what lets the parallel engine evaluate a level concurrently.
        ``(None, None)`` when the graph is cyclic - there is no order to return, and the
        caller reports the cycle instead of inventing one.

    Loop invariants
        Every id already in ``order`` has all of its predecessors in ``order``; ``ready``
        holds only nodes with zero unemitted predecessors. ``ready`` is sorted at every
        step, so the same graph always produces a byte-identical order - required for a
        stable ``dag_hash`` and for reproducible backtests.
    """
    node_ids = {node.id for node in graph.nodes}
    adjacency = _build_adjacency(graph.edges, node_ids)

    in_degree: Dict[str, int] = {node_id: 0 for node_id in node_ids}
    for source, targets in adjacency.items():
        for target in targets:
            in_degree[target] += 1

    ready = sorted(node_id for node_id, degree in in_degree.items() if degree == 0)
    order: List[str] = []
    levels: List[List[str]] = []

    while ready:
        level = list(ready)
        levels.append(level)
        next_ready: Set[str] = set()
        for node_id in level:
            order.append(node_id)
            for target in adjacency.get(node_id, ()):
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    next_ready.add(target)
        ready = sorted(next_ready)

    if len(order) != len(node_ids):
        return None, None
    return order, levels


# ---------------------------------------------------------------------------
# Warmup composition (design.md -> Warmup computation)
# ---------------------------------------------------------------------------


def compose_warmup(
    graph: StrategyGraph,
    registry: Any = None,
    *,
    order: Optional[Sequence[str]] = None,
    descriptors: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, int], int]:
    """Per-node and total warmup: ``(warmup_by_node, total)``.

    Warmup **composes** along a path rather than maxing: an EMA(200) feeding a
    rolling-std(20) feeding a lag(3) needs 200 + 20 + 3 bars before its last value is real.
    Taking only the max would ship NaN-contaminated features into a model.

    Preconditions
        ``graph`` is acyclic. Evaluation walks a topological order, so a cycle would make
        the result meaningless - the caller runs stage 6 first and skips this on a cycle.

    Postconditions
        ``total`` is the largest warmup on any ACTION node's path, or over every node when
        the graph declares no ACTION node yet.

    Loop invariants
        A node's entry is written only after every predecessor's entry, so an entry once
        written is final.

    The canonical composition for a :class:`CompiledPlan` is ``plan.compute_warmup``
    (task 1.10). This function is the validator's own read of the same rule, kept here so
    the stage-10 feasibility warning does not depend on a plan being built first; the
    compiler passes the plan's value through instead.
    """
    resolved = _resolve_registry(registry)
    node_index = graph.node_index()
    if descriptors is None:
        descriptors = {
            node.id: _descriptor(resolved, node.block_id) for node in graph.nodes
        }

    walk = list(order) if order is not None else (topological_order(graph)[0] or [])
    reverse = _build_adjacency(
        [
            EdgeSpec(
                id=edge.id,
                source=edge.target,
                source_port=edge.target_port,
                target=edge.source,
                target_port=edge.source_port,
            )
            for edge in graph.edges
        ],
        set(node_index),
    )

    warmup_by_node: Dict[str, int] = {}
    for node_id in walk:
        node = node_index.get(node_id)
        if node is None:  # pragma: no cover - walk comes from the graph itself
            continue
        descriptor = descriptors.get(node_id)
        own = 0
        warmup_fn = getattr(descriptor, "warmup", None)
        if callable(warmup_fn):
            try:
                own = int(warmup_fn(node.params))
            except Exception:  # noqa: BLE001 - a warmup that cannot be read is 0, reported
                logger.warning(
                    "warmup_fn failed for node %s (block %s); treating as 0",
                    node_id,
                    node.block_id,
                )
                own = 0
        upstream = max(
            (warmup_by_node.get(pred, 0) for pred in reverse.get(node_id, ())),
            default=0,
        )
        warmup_by_node[node_id] = own + upstream

    action_ids = [
        node.id
        for node in graph.nodes
        if _category_of(node, descriptors.get(node.id)) is BlockCategory.ACTION
    ]
    pool = action_ids or list(warmup_by_node)
    total = max((warmup_by_node.get(node_id, 0) for node_id in pool), default=0)
    return warmup_by_node, int(total)


# ---------------------------------------------------------------------------
# Edge legality: rules R1-R8 (design.md -> Legality rules)
# ---------------------------------------------------------------------------


def is_edge_legal(
    graph: StrategyGraph,
    edge: EdgeSpec,
    registry: Any = None,
    *,
    check_cycle: bool = True,
    inbound: Optional[Mapping[str, Mapping[str, Tuple[EdgeSpec, ...]]]] = None,
    descriptors: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Rules R1-R8 for one edge. ``None`` when the edge is legal, else one issue.

    Preconditions
        ``registry`` resolves, or is left ``None`` to use the assembled one. ``graph`` has
        unique node ids. ``edge`` need not be in ``graph``: the same function answers the
        connect-time question ("may I add this?") and the validate-time question ("is this
        one legal?").

    Postconditions
        Either ``None`` or exactly one issue carrying a machine code and an actionable
        message. ``graph`` is not mutated.

    Rule order deviates from the design's listing in one respect, deliberately: the
    block-level rejections R6 (terminal source) and R5 (category flow) are evaluated
    before the port-level R3/R4. An ACTION block declares no output ports at all, so R3
    would reject ``ACTION -> anything`` with "unknown port" when the real reason is "an
    action is terminal"; likewise a DATA block declares no inputs, so ``ML_DL -> DATA``
    reads better as an illegal category flow. Legality is unaffected - an edge is illegal
    if *any* rule rejects it, and only the message differs - and the forbidden flows stay
    consequences of R5/R6 rather than a special-cased table.

    ``check_cycle=False`` suppresses R8 only; the pipeline evaluates acyclicity once for
    the whole graph in stage 6 instead of once per edge, which is the same rule with one
    evaluation instead of E.
    """
    resolved = _resolve_registry(registry)

    def _descriptor_for(node: NodeSpec) -> Any:
        if descriptors is not None and node.id in descriptors:
            return descriptors[node.id]
        return _descriptor(resolved, node.block_id)

    # -- R1 endpoints resolve ------------------------------------------------
    src = graph.node(edge.source)
    dst = graph.node(edge.target)
    if src is None or dst is None:
        missing = [
            name
            for name, node in (("source", src), ("target", dst))
            if node is None
        ]
        named = ", ".join(
            f"{name}={getattr(edge, name)!r}" for name in missing
        )
        return make_issue(
            CODE_EDGE_ENDPOINT_UNKNOWN,
            SEVERITY_ERROR,
            f"Connection '{edge.id}' names a block that is not in this strategy ({named}).",
            edge_id=edge.id,
            expected="both endpoints present in the graph",
            actual={"source": edge.source, "target": edge.target},
            fix_hint="Delete the connection, or re-add the block it points at.",
        )

    # -- R2 no self loop -----------------------------------------------------
    if src.id == dst.id:
        return make_issue(
            CODE_SELF_LOOP,
            SEVERITY_ERROR,
            f"A block cannot feed itself: '{src.id}' is both ends of connection "
            f"'{edge.id}'.",
            node_id=src.id,
            edge_id=edge.id,
            expected="source and target to be different blocks",
            actual=src.id,
            fix_hint="Point one end of the connection at a different block.",
        )

    src_descriptor = _descriptor_for(src)
    dst_descriptor = _descriptor_for(dst)
    src_category = _category_of(src, src_descriptor)
    dst_category = _category_of(dst, dst_descriptor)

    # -- R6 terminal blocks have no outputs ----------------------------------
    if bool(getattr(src_descriptor, "is_terminal", False)):
        return make_issue(
            CODE_TERMINAL_HAS_NO_OUTPUT,
            SEVERITY_ERROR,
            f"'{src.block_id}' is terminal: an action block ends a path and cannot feed "
            f"another block.",
            node_id=src.id,
            edge_id=edge.id,
            expected="a non-terminal source block",
            actual=f"{src.block_id} ({src_category.value}, TERMINAL)",
            fix_hint="Take the connection from the block that feeds the action instead.",
        )

    # -- R5 category adjacency from the descriptors --------------------------
    allowed_successors = getattr(src_descriptor, "allowed_successor_categories", None)
    if allowed_successors is not None and dst_category not in allowed_successors:
        permitted = sorted(category.value for category in allowed_successors)
        return make_issue(
            CODE_ILLEGAL_CATEGORY_FLOW,
            SEVERITY_ERROR,
            f"{src_category.value} cannot feed {dst_category.value}: "
            f"'{src.block_id}' may only feed {permitted or ['nothing']}.",
            node_id=dst.id,
            edge_id=edge.id,
            field_name=_port_label(dst.id, edge.target_port),
            expected=permitted,
            actual=dst_category.value,
            fix_hint=(
                "Insert a block of a permitted category between the two, or connect to a "
                "different block."
            ),
        )

    # -- R3 ports exist and face the right way -------------------------------
    out_port = (
        src_descriptor.output_port(edge.source_port)
        if hasattr(src_descriptor, "output_port")
        else None
    )
    if out_port is None:
        known = _port_names(src_descriptor, "outputs")
        detail = (
            f"'{src.block_id}' publishes outputs {known}."
            if src_descriptor is not None
            else f"The block registry publishes no descriptor for '{src.block_id}'."
        )
        return make_issue(
            CODE_UNKNOWN_SOURCE_PORT,
            SEVERITY_ERROR,
            f"Unknown output port '{edge.source_port}' on '{src.id}'. {detail}",
            node_id=src.id,
            edge_id=edge.id,
            field_name=_port_label(src.id, edge.source_port),
            expected=known,
            actual=edge.source_port,
            fix_hint="Re-draw the connection from a port the block publishes.",
        )

    in_port = (
        dst_descriptor.input_port(edge.target_port)
        if hasattr(dst_descriptor, "input_port")
        else None
    )
    if in_port is None:
        known = _port_names(dst_descriptor, "inputs")
        detail = (
            f"'{dst.block_id}' accepts inputs {known}."
            if dst_descriptor is not None
            else f"The block registry publishes no descriptor for '{dst.block_id}'."
        )
        return make_issue(
            CODE_UNKNOWN_TARGET_PORT,
            SEVERITY_ERROR,
            f"Unknown input port '{edge.target_port}' on '{dst.id}'. {detail}",
            node_id=dst.id,
            edge_id=edge.id,
            field_name=_port_label(dst.id, edge.target_port),
            expected=known,
            actual=edge.target_port,
            fix_hint="Re-draw the connection into a port the block accepts.",
        )

    # -- R4 type compatibility, via the published matrix ---------------------
    if not _registry.compatible(out_port.type, in_port.type):
        return make_issue(
            CODE_TYPE_MISMATCH,
            SEVERITY_ERROR,
            f"{out_port.type.value} cannot feed {in_port.type.value}. Insert a converting "
            f"block or pick a different port.",
            node_id=dst.id,
            edge_id=edge.id,
            field_name=_port_label(dst.id, in_port.name),
            expected=sorted(
                target.value
                for target in _registry.COMPATIBILITY_MATRIX.get(out_port.type, ())
            ),
            actual=in_port.type.value,
            fix_hint=(
                f"'{src.id}.{out_port.name}' produces {out_port.type.value}; connect it to "
                f"a port that accepts that type."
            ),
        )

    # -- R7 single-arity input already occupied ------------------------------
    if not in_port.variadic:
        existing = (
            inbound.get(dst.id, {}).get(edge.target_port, ())
            if inbound is not None
            else tuple(
                candidate
                for candidate in graph.edges
                if candidate.target == dst.id
                and candidate.target_port == edge.target_port
            )
        )
        occupied = [
            candidate.id for candidate in existing if candidate.id != edge.id
        ]
        if occupied:
            return make_issue(
                CODE_PORT_ALREADY_CONNECTED,
                SEVERITY_ERROR,
                f"{_port_label(dst.id, edge.target_port)} accepts one connection and "
                f"already has one.",
                node_id=dst.id,
                edge_id=edge.id,
                field_name=_port_label(dst.id, edge.target_port),
                expected=1,
                actual=len(occupied) + 1,
                fix_hint=(
                    "Remove the existing connection first, or use a block whose input "
                    "accepts several sources."
                ),
            )

    # -- R8 adding the edge must not create a cycle --------------------------
    if check_cycle and creates_cycle(graph, edge):
        path = cycle_path_with_edge(graph, edge)
        return make_issue(
            CODE_CYCLE,
            SEVERITY_ERROR,
            "Connection would create a loop: " + " -> ".join(path),
            node_id=src.id,
            edge_id=edge.id,
            expected="an acyclic graph",
            actual=path,
            fix_hint="Remove one connection on that loop; a strategy must flow one way.",
        )

    return None


def validate_edge(
    graph: StrategyGraph, edge: EdgeSpec, registry: Any = None
) -> Optional[Dict[str, Any]]:
    """Connect-time gate: the full R1-R8 verdict for a candidate edge."""
    return is_edge_legal(graph, edge, registry, check_cycle=True)


# ---------------------------------------------------------------------------
# Stage 3 helpers: parameters, through the one declarative checker
# ---------------------------------------------------------------------------

#: Message fragments :func:`block_specs.validate_block_params` emits, mapped onto codes.
#: This module classifies the single checker's output; it does not re-check anything, so
#: there is exactly one range/option/type implementation in the codebase. The fragments
#: are covered by a test, so a reworded message surfaces as a failure rather than as
#: silently mis-coded telemetry.
_PARAM_CODE_FRAGMENTS: Tuple[Tuple[str, str], ...] = (
    ("is required and has no default", CODE_PARAM_REQUIRED_MISSING),
    ("expects one of", CODE_PARAM_NOT_IN_OPTIONS),
    ("expects at least", CODE_PARAM_OUT_OF_RANGE),
    ("expects at most", CODE_PARAM_OUT_OF_RANGE),
    ("expects a whole number", CODE_PARAM_TYPE_INVALID),
    ("expects a number", CODE_PARAM_TYPE_INVALID),
    ("expects a integer", CODE_PARAM_TYPE_INVALID),
    ("expects a boolean", CODE_PARAM_TYPE_INVALID),
    ("expects a multiselect", CODE_PARAM_TYPE_INVALID),
)


def _classify_param_message(message: str) -> str:
    lowered = message.lower()
    for fragment, code in _PARAM_CODE_FRAGMENTS:
        if fragment in lowered:
            return code
    return CODE_PARAM_INVALID


def _param_expectation(param: Any, code: str) -> Any:
    if code == CODE_PARAM_REQUIRED_MISSING:
        return "a value"
    if code == CODE_PARAM_NOT_IN_OPTIONS:
        return list(param.options or ())
    if code == CODE_PARAM_OUT_OF_RANGE:
        return {"min": param.min, "max": param.max}
    if code == CODE_PARAM_TYPE_INVALID:
        return param.type.value
    return None


def _param_fix_hint(param: Any) -> str:
    if param.example is not None:
        return f"Set '{param.key}' to {param.example!r}."
    if param.help:
        return param.help
    if param.options:
        return f"Pick one of {list(param.options)}."
    if param.min is not None or param.max is not None:
        return f"Choose a value between {param.min} and {param.max}."
    return f"Give '{param.key}' a value this block accepts."


def _param_issues(node: NodeSpec, descriptor: Any) -> List[Dict[str, Any]]:
    """Declarative and cross-field parameter problems for one node.

    Each declared parameter is checked by :func:`block_specs.validate_block_params` in
    isolation - the descriptor is narrowed to that one parameter with
    ``dataclasses.replace`` - so every message it returns is attributable to a field
    without parsing the message for a key. Cross-field rules come from
    ``BlockDescriptor.cross_field_issues``, which is the descriptor's own hook.
    """
    issues: List[Dict[str, Any]] = []
    params = dict(node.params or {})

    for param in getattr(descriptor, "params", ()) or ():
        probe = replace(descriptor, params=(param,), validate=None)
        for message in validate_block_params(probe, {param.key: params.get(param.key)}):
            code = _classify_param_message(message)
            issues.append(
                make_issue(
                    code,
                    SEVERITY_ERROR,
                    message,
                    node_id=node.id,
                    field_name=param.key,
                    expected=_param_expectation(param, code),
                    actual=params.get(param.key),
                    fix_hint=_param_fix_hint(param),
                )
            )

    declared = {param.key for param in getattr(descriptor, "params", ()) or ()}
    for key in sorted(set(params) - declared):
        # A warning, not an error: an undeclared key is never forwarded to the runtime, so
        # it cannot change behaviour - but it does mean the author believes something is
        # configured that is not, which is worth saying out loud.
        issues.append(
            make_issue(
                CODE_PARAM_UNKNOWN,
                SEVERITY_WARNING,
                f"'{node.block_id}' does not declare a parameter '{key}'; it is ignored.",
                node_id=node.id,
                field_name=key,
                expected=sorted(declared),
                actual=key,
                fix_hint=f"Remove '{key}' from this block's settings.",
            )
        )

    cross_field = getattr(descriptor, "cross_field_issues", None)
    if callable(cross_field):
        for raw in cross_field(params) or ():
            # The registry already normalises these onto the contract's key set and spells
            # its severity ``"ERROR"``; re-emitting through make_issue collapses the two
            # spellings onto one and drops the registry's extra ``block_id`` key, so every
            # entry in the report has exactly the contract's fields.
            issues.append(
                make_issue(
                    str(raw.get("code") or CODE_PARAM_CROSS_FIELD),
                    normalise_severity(raw.get("severity")),
                    str(raw.get("message") or "Parameters are inconsistent."),
                    node_id=node.id,
                    field_name=raw.get("field"),
                    expected=raw.get("expected"),
                    actual=raw.get("actual"),
                    fix_hint=(
                        str(raw.get("fix_hint"))
                        if raw.get("fix_hint")
                        else "Adjust the parameters this block's rule names so they agree."
                    ),
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Server-side recomputation (Requirement 6.13)
# ---------------------------------------------------------------------------


def _recompute_graph(
    graph: StrategyGraph,
    descriptors: Mapping[str, Any],
) -> Tuple[StrategyGraph, List[Dict[str, Any]]]:
    """Rebuild the graph with ports, categories and edge types read from the registry.

    Returns the canonical graph and one warning per field whose submitted value disagreed
    with the recomputed one. Nothing here *trusts* the submitted value: the rules already
    read the descriptor, so the warnings exist to make tampering visible, not to enforce.
    """
    overrides: List[Dict[str, Any]] = []
    nodes: List[NodeSpec] = []

    for node in graph.nodes:
        descriptor = descriptors.get(node.id)
        if descriptor is None:
            # Ports of an unresolved block are unknown, never invented. The node keeps
            # what it arrived with and carries an UNRESOLVED_BLOCK error.
            nodes.append(node)
            continue

        declared_inputs = list(_declared_ports(descriptor, "inputs"))
        declared_outputs = list(_declared_ports(descriptor, "outputs"))
        category = _category_of(node, descriptor)

        submitted = (
            [port.to_dict() for port in node.inputs],
            [port.to_dict() for port in node.outputs],
        )
        recomputed = (
            [port.to_dict() for port in declared_inputs],
            [port.to_dict() for port in declared_outputs],
        )
        if node.inputs or node.outputs:
            if submitted != recomputed:
                overrides.append(
                    make_issue(
                        CODE_PORT_CONTRACT_RECOMPUTED,
                        SEVERITY_WARNING,
                        f"The port list submitted for '{node.id}' did not match the "
                        f"registry contract for '{node.block_id}'; the registry contract "
                        f"was used.",
                        node_id=node.id,
                        field_name="inputs/outputs",
                        expected=recomputed,
                        actual=submitted,
                        fix_hint=(
                            "Refresh the block palette; the client is sending a stale or "
                            "hand-edited port list."
                        ),
                    )
                )
        if node.category is not category:
            overrides.append(
                make_issue(
                    CODE_CATEGORY_REMAPPED,
                    SEVERITY_WARNING,
                    f"Node '{node.id}' was submitted as {node.category.value} but "
                    f"'{node.block_id}' is a {category.value} block; the registry wins.",
                    node_id=node.id,
                    field_name="category",
                    expected=category.value,
                    actual=node.category.value,
                    fix_hint="Refresh the block palette so the client agrees with the registry.",
                )
            )

        nodes.append(
            NodeSpec(
                id=node.id,
                block_id=node.block_id,
                category=category,
                params=dict(node.params),
                inputs=declared_inputs,
                outputs=declared_outputs,
                ui=dict(node.ui),
            )
        )

    node_index = {node.id: node for node in nodes}
    edges: List[EdgeSpec] = []
    for edge in graph.edges:
        source = node_index.get(edge.source)
        descriptor = descriptors.get(edge.source) if source is not None else None
        port = (
            descriptor.output_port(edge.source_port)
            if hasattr(descriptor, "output_port")
            else None
        )
        recomputed_type = port.type if port is not None else None
        if (
            edge.type is not None
            and recomputed_type is not None
            and edge.type is not recomputed_type
        ):
            overrides.append(
                make_issue(
                    CODE_EDGE_TYPE_RECOMPUTED,
                    SEVERITY_WARNING,
                    f"Connection '{edge.id}' declared type {edge.type.value}; "
                    f"'{edge.source}.{edge.source_port}' produces "
                    f"{recomputed_type.value}. The port's type was used.",
                    edge_id=edge.id,
                    field_name="type",
                    expected=recomputed_type.value,
                    actual=edge.type.value,
                    fix_hint="Refresh the block palette; the client's port types are stale.",
                )
            )
        edges.append(
            EdgeSpec(
                id=edge.id,
                source=edge.source,
                source_port=edge.source_port,
                target=edge.target,
                target_port=edge.target_port,
                type=recomputed_type if recomputed_type is not None else edge.type,
            )
        )

    canonical = StrategyGraph(
        schema_version=graph.schema_version,
        strategy_id=graph.strategy_id,
        version=graph.version,
        name=graph.name,
        nodes=nodes,
        edges=edges,
        metadata=dict(graph.metadata),
        validation_state=graph.validation_state,
    )
    return canonical, overrides


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def validate(
    graph: StrategyGraph,
    registry: Any = None,
    *,
    available_bars: Optional[int] = None,
    feature_columns: Optional[int] = None,
    ml_dataset_stats: Optional[Any] = None,
    limits: Optional[GraphLimits] = None,
    include_migration_issues: bool = True,
    warmup_provider: Optional[Callable[[StrategyGraph, Any], int]] = None,
) -> ValidationReport:
    """Run every stage and return one report holding every problem found.

    Parameters
    ----------
    graph
        A canonical version 2 graph, already parsed. Parsing is ``schema``'s job.
    registry
        A descriptor source. ``None`` uses the assembled registry, resolved lazily.
    available_bars
        Bars the configured data range provides. Supplied by the caller that knows the
        range; when it is ``None`` the stage-10 feasibility warning cannot be evaluated and
        the stage is reported ``SKIPPED`` rather than passed.
    feature_columns
        Width of the assembled feature matrix, when known. Column count is a property of
        the produced matrix rather than of the graph, so the capacity check runs only when
        the caller supplies it; the authoritative check is the ML readiness gate.
    ml_dataset_stats
        Measured statistics of the built training dataset, for stage 11. Either one stats
        mapping (``SupervisedDataset.stats()``), one ``SupervisedDataset``, or
        ``{node_id: stats}`` when a graph's model nodes read different matrices. A graph
        carries which model a node runs but not how many rows and columns the fetched data
        produced, so stage 11 cannot be answered without this; when it is ``None`` the
        stage is reported ``SKIPPED`` and the graph is **not** confirmed ML-ready.
    limits
        Capacity bounds. Defaults to :data:`LIMITS`.
    include_migration_issues
        Fold the v1 -> v2 migration's issues into this report. They are already the same
        shape, so they are folded untranslated.
    warmup_provider
        ``fn(graph, registry) -> int``, for a caller that has already composed warmup (the
        compiler passes ``plan.warmup_bars``). Defaults to :func:`compose_warmup`.

    Preconditions
        ``graph`` parses as version 2. The registry assembles.

    Postconditions
        Every stage that could run has run - this is **collect-all**, never fail-fast - so
        the author fixes every problem in one round. ``report.valid`` is false exactly when
        at least one error was collected. ``report.canonical_graph`` carries the
        server-recomputed ports, categories, edge types and validation state, and
        ``report.dag_hash`` is set only for a valid graph. Nothing is mutated: ``graph`` is
        returned untouched.
    """
    # Requirement 24.1. `perf_counter` rather than `time.time`: this is an interval, and a
    # wall-clock correction mid-validation must not turn into a negative duration.
    _measured_from = time.perf_counter()
    resolved_limits = limits if limits is not None else LIMITS
    # Task 9.4: the column ceiling is a property of the produced matrix, so stage 1 can only
    # check it against a measured count. When the caller supplied measured dataset
    # statistics but no explicit ``feature_columns`` - which is what the training path
    # does - the count is *in* those statistics, and reading it here is what makes
    # Requirement 25.4's 200-column bound reachable on a real path instead of only when a
    # caller happens to pass the number twice.
    if feature_columns is None:
        feature_columns = measured_feature_columns(ml_dataset_stats)
    resolved_registry = _resolve_registry(registry)
    report = ValidationReport()
    report.registry_version = _registry_version_of(resolved_registry)

    node_index = graph.node_index()
    descriptors: Dict[str, Any] = {
        node.id: _descriptor(resolved_registry, node.block_id) for node in graph.nodes
    }
    known_ids = set(node_index)
    adjacency = _build_adjacency(graph.edges, known_ids)
    reverse_adjacency = _build_adjacency(
        [
            EdgeSpec(
                id=edge.id,
                source=edge.target,
                source_port=edge.target_port,
                target=edge.source,
                target_port=edge.source_port,
            )
            for edge in graph.edges
        ],
        known_ids,
    )
    inbound = _build_inbound(graph.edges)

    if include_migration_issues:
        migration = _migration_issues(graph)
        if migration:
            report.extend(migration)
            report.record_stage(
                0,
                "migration",
                STATUS_FAILED if any(
                    normalise_severity(issue.get("severity")) == SEVERITY_ERROR
                    for issue in migration
                ) else STATUS_PASSED,
                f"{len(migration)} issue(s) folded in from the v1 -> v2 migration.",
            )

    # -- stage 1: structural -------------------------------------------------
    before = len(report.errors)
    _stage_structural(graph, report, resolved_limits, descriptors, feature_columns)
    report.record_stage(
        1,
        STAGE_STRUCTURAL,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "ids unique, endpoints resolve, graph non-empty, capacity limits respected.",
    )

    # -- stage 2: registry resolution ---------------------------------------
    before = len(report.errors)
    for node in graph.nodes:
        if descriptors.get(node.id) is None:
            report.add(
                make_issue(
                    CODE_UNRESOLVED_BLOCK,
                    SEVERITY_ERROR,
                    f"Node '{node.id}' references block '{node.block_id}', which the "
                    f"block registry does not publish.",
                    node_id=node.id,
                    field_name="block_id",
                    expected="a block id the registry publishes",
                    actual=node.block_id,
                    fix_hint=(
                        "Replace the block from the current palette; this build does not "
                        "offer that block."
                    ),
                )
            )
    report.record_stage(
        2,
        STAGE_REGISTRY,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "every block_id resolves to a descriptor whose runtime the registry asserted at "
        "assembly.",
    )

    # -- stage 3: parameters -------------------------------------------------
    before = len(report.errors)
    for node in graph.nodes:
        descriptor = descriptors.get(node.id)
        if descriptor is None:
            continue  # already reported; its ParamSpecs are unknown, never guessed
        report.extend(_param_issues(node, descriptor))
    report.record_stage(
        3,
        STAGE_PARAMETERS,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "required, type, range, enum and cross-field rules, from the descriptors' own "
        "ParamSpecs.",
    )

    # -- stage 4: port contracts, R3 R4 R5 R6 R7 per edge --------------------
    before = len(report.errors)
    for edge in graph.edges:
        if edge.source not in known_ids or edge.target not in known_ids:
            continue  # R1 already reported it in stage 1
        issue = is_edge_legal(
            graph,
            edge,
            resolved_registry,
            check_cycle=False,       # R8 runs once for the whole graph, in stage 6
            inbound=inbound,
            descriptors=descriptors,
        )
        if issue is not None:
            report.add(issue)
    report.record_stage(
        4,
        STAGE_PORT_CONTRACTS,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "rules R3-R7 evaluated per connection; R8 is evaluated once in stage 6.",
    )

    # -- stage 5: required inputs bound --------------------------------------
    before = len(report.errors)
    for node in graph.nodes:
        descriptor = descriptors.get(node.id)
        if descriptor is None:
            continue
        for port in _declared_ports(descriptor, "inputs"):
            if not port.required:
                continue
            if inbound.get(node.id, {}).get(port.name):
                continue
            report.add(
                make_issue(
                    CODE_REQUIRED_INPUT_MISSING,
                    SEVERITY_ERROR,
                    f"'{node.block_id}' has nothing connected to its required "
                    f"'{port.name}' input.",
                    node_id=node.id,
                    field_name=port.name,
                    expected=f"one connection producing {port.type.value}",
                    actual=None,
                    fix_hint=(
                        f"Connect a block that outputs {port.type.value} to "
                        f"{_port_label(node.id, port.name)}."
                    ),
                )
            )
    report.record_stage(
        5,
        STAGE_REQUIRED_INPUTS,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "no required input port is left unfed.",
    )

    # -- stage 6: acyclicity -------------------------------------------------
    cycle = find_cycle(graph.nodes, graph.edges)
    if cycle:
        closing = _closing_edge(graph, cycle)
        report.add(
            make_issue(
                CODE_CYCLE,
                SEVERITY_ERROR,
                "This strategy contains a loop: " + " -> ".join(cycle),
                node_id=cycle[0],
                edge_id=None if closing is None else closing.id,
                expected="an acyclic graph",
                actual=cycle,
                fix_hint=(
                    "Remove one connection on that loop; a strategy must flow one way."
                ),
            )
        )
    report.record_stage(
        6,
        STAGE_ACYCLICITY,
        STATUS_FAILED if cycle else STATUS_PASSED,
        "iterative depth-first search over the whole graph.",
        cycle=list(cycle),
    )

    # -- execution order, recomputed server-side -----------------------------
    order: Optional[List[str]] = None
    levels: Optional[List[List[str]]] = None
    if not cycle:
        order, levels = topological_order(graph)
    report.execution_order = order
    report.execution_levels = levels

    data_ids = [
        node.id
        for node in graph.nodes
        if _category_of(node, descriptors.get(node.id)) is BlockCategory.DATA
    ]
    action_ids = [
        node.id
        for node in graph.nodes
        if _category_of(node, descriptors.get(node.id)) is BlockCategory.ACTION
    ]
    downstream_of_data = _reachable_from(adjacency, data_ids)
    upstream_of_action = _reachable_from(reverse_adjacency, action_ids)

    # -- stage 7: reachability ----------------------------------------------
    before = len(report.errors)
    for node in graph.nodes:
        if node.id in downstream_of_data or node.id in upstream_of_action:
            continue
        report.add(
            make_issue(
                CODE_ORPHAN_NODE,
                SEVERITY_ERROR,
                f"'{node.id}' is not on any path: nothing upstream reaches it from a "
                f"Market Data block and nothing downstream reaches an Action block.",
                node_id=node.id,
                expected="a path from a DATA block or a path to an ACTION block",
                actual="no path in either direction",
                fix_hint="Connect the block into the strategy, or delete it.",
            )
        )
    report.record_stage(
        7,
        STAGE_REACHABILITY,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "no node is disconnected from both the data source and the action sink.",
    )

    # -- stage 8: execution path --------------------------------------------
    before = len(report.errors)
    for category, present in (
        (BlockCategory.DATA, data_ids),
        (BlockCategory.ACTION, action_ids),
    ):
        if not present:
            report.add(
                make_issue(
                    CODE_MISSING_REQUIRED_CATEGORY,
                    SEVERITY_ERROR,
                    f"A strategy needs at least one {category.value} block and this one "
                    f"has none.",
                    field_name="category",
                    expected=category.value,
                    actual=0,
                    fix_hint=(
                        f"Drag a {category.value} block onto the canvas and connect it."
                    ),
                )
            )
    if data_ids and action_ids:
        reachable_actions = [
            action_id for action_id in action_ids if action_id in downstream_of_data
        ]
        if not reachable_actions:
            report.add(
                make_issue(
                    CODE_ACTION_UNREACHABLE,
                    SEVERITY_ERROR,
                    "No Action block is reachable from a Market Data block, so nothing "
                    "this strategy computes can ever place an order.",
                    expected="at least one ACTION node reachable from a DATA node",
                    actual=0,
                    fix_hint=(
                        "Connect the chain from your data block through to an action "
                        "block."
                    ),
                )
            )
    report.record_stage(
        8,
        STAGE_EXECUTION_PATH,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "at least one DATA node, at least one ACTION node, and an ACTION reachable from "
        "a DATA node.",
    )

    # -- stage 9: action provenance -----------------------------------------
    before = len(report.errors)
    _stage_action_provenance(
        graph, report, descriptors, inbound, reverse_adjacency, action_ids
    )
    report.record_stage(
        9,
        STAGE_ACTION_PROVENANCE,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        "an ACTION signal input is fed only by a LOGIC block or a trade-signal producer, "
        "and one action path trades one symbol.",
    )

    # -- stage 10a: warmup ---------------------------------------------------
    warmup_by_node: Dict[str, int] = {}
    warmup_bars: Optional[int] = None
    if cycle:
        report.record_stage(
            10,
            STAGE_WARMUP,
            STATUS_SKIPPED,
            "warmup composes along paths and is undefined on a cyclic graph; fix the loop "
            "reported by stage 6 first.",
        )
    else:
        if warmup_provider is not None:
            warmup_bars = int(warmup_provider(graph, resolved_registry))
        else:
            warmup_by_node, warmup_bars = compose_warmup(
                graph, resolved_registry, order=order, descriptors=descriptors
            )
        report.warmup_by_node = warmup_by_node
        report.warmup_bars = warmup_bars
        if available_bars is None:
            report.record_stage(
                10,
                STAGE_WARMUP,
                STATUS_SKIPPED,
                f"warmup composed to {warmup_bars} bars; feasibility was not checked "
                f"because the caller supplied no available bar count.",
                warmup_bars=warmup_bars,
            )
        elif warmup_bars > int(available_bars):
            # A warning, not an error: the graph is well-formed, the chosen range is too
            # short. The author can widen the range without touching the strategy.
            report.add(
                make_issue(
                    CODE_WARMUP_EXCEEDS_HISTORY,
                    SEVERITY_WARNING,
                    f"This strategy needs {warmup_bars} warmup bars. The selected range "
                    f"provides {int(available_bars)}.",
                    field_name="warmup_bars",
                    expected=warmup_bars,
                    actual=int(available_bars),
                    fix_hint=(
                        "Widen the history range, use a shorter lookback, or accept that "
                        "early bars are discarded."
                    ),
                )
            )
            report.record_stage(
                10,
                STAGE_WARMUP,
                STATUS_PASSED,
                f"warmup {warmup_bars} bars exceeds the {int(available_bars)} available; "
                f"reported as a warning, which does not block.",
                warmup_bars=warmup_bars,
            )
        else:
            report.record_stage(
                10,
                STAGE_WARMUP,
                STATUS_PASSED,
                f"warmup {warmup_bars} bars fits the {int(available_bars)} available.",
                warmup_bars=warmup_bars,
            )

    context = ValidationContext(
        graph=graph,
        registry=resolved_registry,
        descriptors=descriptors,
        node_index=node_index,
        adjacency=adjacency,
        reverse_adjacency=reverse_adjacency,
        inbound=inbound,
        execution_order=None if order is None else tuple(order),
        warmup_by_node=dict(warmup_by_node),
        warmup_bars=warmup_bars,
        available_bars=available_bars,
        feature_columns=feature_columns,
        limits=resolved_limits,
        ml_dataset_stats=ml_dataset_stats,
    )

    # -- stage 10b: leakage, and stage 11: ML readiness ----------------------
    _run_seam(report, 10, STAGE_LEAKAGE, context)
    _run_seam(report, 11, STAGE_ML_READINESS, context)

    # -- recompute what the client is not trusted with ----------------------
    canonical, overrides = _recompute_graph(graph, descriptors)
    report.extend(overrides)

    computed_state = ValidationState.VALID if not report.errors else ValidationState.INVALID
    # UNVALIDATED is the honest state for a graph that has not been validated yet, so it is
    # not an override worth reporting. A client *claiming* VALID or INVALID is.
    if (
        graph.validation_state is not ValidationState.UNVALIDATED
        and graph.validation_state is not computed_state
    ):
        report.add(
            make_issue(
                CODE_VALIDATION_STATE_RECOMPUTED,
                SEVERITY_WARNING,
                f"The submitted graph declared validation_state "
                f"{graph.validation_state.value}; this validation computed "
                f"{computed_state.value}.",
                field_name="validation_state",
                expected=computed_state.value,
                actual=graph.validation_state.value,
                fix_hint=(
                    "Validation state is a server-side verdict; the client's value is "
                    "advisory only."
                ),
            )
        )
        # The recomputed verdict may have flipped when that warning landed; recompute it
        # from the errors list, which is the only authority.
        computed_state = (
            ValidationState.VALID if not report.errors else ValidationState.INVALID
        )
    canonical.validation_state = computed_state

    report.canonical_graph = canonical
    report.dag_hash = compute_dag_hash(canonical) if not report.errors else None
    report.summary = {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "warmup_bars": warmup_bars,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "data_nodes": len(data_ids),
        "action_nodes": len(action_ids),
    }

    # -- Requirement 24.1 ----------------------------------------------------
    # Duration and error counts by code, read off the report this pass already built
    # rather than recounted. The recorder is total (see `metrics.never_fails`), and the
    # report is complete and unmodified by this point, so nothing here can change the
    # verdict a caller is about to act on.
    collector = _metrics()
    if collector is not None:
        collector.record_builder_validation(
            (time.perf_counter() - _measured_from) * 1000.0,
            [str(issue.get("code")) for issue in report.errors],
        )
    return report


# ---------------------------------------------------------------------------
# Stage bodies that are long enough to name
# ---------------------------------------------------------------------------


def _registry_version_of(registry: Any) -> Optional[str]:
    version = getattr(registry, "registry_version", None)
    if callable(version):  # pragma: no cover - module-level accessor form
        try:
            return str(version())
        except Exception:  # noqa: BLE001
            return None
    return None if version is None else str(version)


def _closing_edge(graph: StrategyGraph, cycle: Sequence[str]) -> Optional[EdgeSpec]:
    """The edge that closes ``cycle``: the last hop back to its first node."""
    if len(cycle) < 2:
        return None
    source, target = cycle[-2], cycle[-1]
    for edge in graph.edges:
        if edge.source == source and edge.target == target:
            return edge
    return None


def _stage_structural(
    graph: StrategyGraph,
    report: ValidationReport,
    limits: GraphLimits,
    descriptors: Mapping[str, Any],
    feature_columns: Optional[int],
) -> None:
    """Stage 1: ids, endpoints, emptiness, schema version and the capacity limits."""
    if graph.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        report.add(
            make_issue(
                CODE_UNSUPPORTED_SCHEMA_VERSION,
                SEVERITY_ERROR,
                f"Graph schema_version {graph.schema_version!r} is not one this build can "
                f"read.",
                field_name="schema_version",
                expected=list(SUPPORTED_SCHEMA_VERSIONS),
                actual=graph.schema_version,
                fix_hint="Re-save the strategy from a current client.",
            )
        )

    if not graph.nodes:
        report.add(
            make_issue(
                CODE_EMPTY_GRAPH,
                SEVERITY_ERROR,
                "This strategy has no blocks.",
                expected="at least one block",
                actual=0,
                fix_hint="Drag a Market Data block onto the canvas to begin.",
            )
        )

    seen_nodes: Set[str] = set()
    for node in graph.nodes:
        if node.id in seen_nodes:
            report.add(
                make_issue(
                    CODE_DUPLICATE_NODE_ID,
                    SEVERITY_ERROR,
                    f"Two blocks share the id '{node.id}'.",
                    node_id=node.id,
                    field_name="id",
                    expected="a unique block id",
                    actual=node.id,
                    fix_hint="Re-add one of the two blocks so it is minted a fresh id.",
                )
            )
        seen_nodes.add(node.id)

    seen_edges: Set[str] = set()
    for edge in graph.edges:
        if edge.id in seen_edges:
            report.add(
                make_issue(
                    CODE_DUPLICATE_EDGE_ID,
                    SEVERITY_ERROR,
                    f"Two connections share the id '{edge.id}'.",
                    edge_id=edge.id,
                    field_name="id",
                    expected="a unique connection id",
                    actual=edge.id,
                    fix_hint="Re-draw one of the two connections.",
                )
            )
        seen_edges.add(edge.id)

        # R1: ``seen_nodes`` is complete here, the node loop above having finished.
        if edge.source not in seen_nodes or edge.target not in seen_nodes:
            report.add(
                make_issue(
                    CODE_EDGE_ENDPOINT_UNKNOWN,
                    SEVERITY_ERROR,
                    f"Connection '{edge.id}' names a block that is not in this "
                    f"strategy (source={edge.source!r}, target={edge.target!r}).",
                    edge_id=edge.id,
                    expected="both endpoints present in the graph",
                    actual={"source": edge.source, "target": edge.target},
                    fix_hint="Delete the connection, or re-add the block it points at.",
                )
            )

    _stage_limits(graph, report, limits, descriptors, feature_columns)


def _stage_limits(
    graph: StrategyGraph,
    report: ValidationReport,
    limits: GraphLimits,
    descriptors: Mapping[str, Any],
    feature_columns: Optional[int],
) -> None:
    """The capacity bounds, each naming the limit and its permitted value (25.4)."""

    def _emit(code: str, key: str, actual: int, unit: str) -> None:
        permitted = getattr(limits, key)
        report.add(
            make_issue(
                code,
                SEVERITY_ERROR,
                f"{limits.label(key)} is {permitted} {unit}; this strategy has {actual}.",
                field_name=key,
                expected=permitted,
                actual=actual,
                fix_hint=(
                    f"Reduce to {permitted} {unit} or fewer, or split the strategy in two."
                ),
            )
        )

    if len(graph.nodes) > limits.max_nodes:
        _emit(CODE_NODE_LIMIT_EXCEEDED, "max_nodes", len(graph.nodes), "blocks")
    if len(graph.edges) > limits.max_edges:
        _emit(CODE_EDGE_LIMIT_EXCEEDED, "max_edges", len(graph.edges), "connections")

    ml_count = sum(
        1
        for node in graph.nodes
        if _category_of(node, descriptors.get(node.id)) is BlockCategory.ML_DL
    )
    if ml_count > limits.max_ml_nodes:
        _emit(CODE_ML_NODE_LIMIT_EXCEEDED, "max_ml_nodes", ml_count, "model blocks")

    fe_count = sum(
        1
        for node in graph.nodes
        if _category_of(node, descriptors.get(node.id))
        is BlockCategory.FEATURE_ENGINEERING
    )
    if fe_count > limits.max_feature_nodes:
        _emit(
            CODE_FEATURE_NODE_LIMIT_EXCEEDED,
            "max_feature_nodes",
            fe_count,
            "feature blocks",
        )

    if feature_columns is not None and int(feature_columns) > limits.max_feature_columns:
        # Column width is a property of the produced matrix, not of the graph, so this
        # runs only when a caller supplies the count. The ML readiness gate is where it
        # becomes unconditional.
        _emit(
            CODE_FEATURE_COLUMN_LIMIT_EXCEEDED,
            "max_feature_columns",
            int(feature_columns),
            "columns",
        )

    fan_in: Dict[Tuple[str, str], int] = {}
    for edge in graph.edges:
        key = (edge.target, edge.target_port)
        fan_in[key] = fan_in.get(key, 0) + 1
    for (node_id, port_name), count in sorted(fan_in.items()):
        if count <= limits.max_fan_in_per_variadic_port:
            continue
        permitted = limits.max_fan_in_per_variadic_port
        report.add(
            make_issue(
                CODE_PORT_FAN_IN_LIMIT_EXCEEDED,
                SEVERITY_ERROR,
                f"{limits.label('max_fan_in_per_variadic_port')} is {permitted}; "
                f"{_port_label(node_id, port_name)} has {count} connections.",
                node_id=node_id,
                field_name="max_fan_in_per_variadic_port",
                expected=permitted,
                actual=count,
                fix_hint=(
                    f"Merge some of those inputs through an intermediate block so no port "
                    f"takes more than {permitted}."
                ),
            )
        )


def _stage_action_provenance(
    graph: StrategyGraph,
    report: ValidationReport,
    descriptors: Mapping[str, Any],
    inbound: Mapping[str, Mapping[str, Tuple[EdgeSpec, ...]]],
    reverse_adjacency: Mapping[str, Tuple[str, ...]],
    action_ids: Sequence[str],
) -> None:
    """Stage 9: what may feed an action, and how many symbols one action may trade."""
    node_index = graph.node_index()

    for action_id in action_ids:
        for port_edges in inbound.get(action_id, {}).values():
            for edge in port_edges:
                source = node_index.get(edge.source)
                if source is None:
                    continue  # reported by stage 1
                source_descriptor = descriptors.get(source.id)
                source_category = _category_of(source, source_descriptor)
                out_port = (
                    source_descriptor.output_port(edge.source_port)
                    if hasattr(source_descriptor, "output_port")
                    else None
                )
                produces_signal = (
                    out_port is not None and out_port.type in TRADE_SIGNAL_TYPES
                )
                if source_category is BlockCategory.LOGIC or produces_signal:
                    continue
                report.add(
                    make_issue(
                        CODE_ACTION_INPUT_PROVENANCE,
                        SEVERITY_ERROR,
                        f"'{node_index[action_id].block_id}' is fed by "
                        f"'{source.block_id}', which is a {source_category.value} block "
                        f"and produces no trade signal. An order must be triggered by a "
                        f"decision, not by a raw value.",
                        node_id=source.id,
                        edge_id=edge.id,
                        field_name=edge.target_port,
                        expected="a LOGIC block, or a port producing SIGNAL / TRADE_INTENT",
                        actual=(
                            f"{source_category.value} / "
                            f"{None if out_port is None else out_port.type.value}"
                        ),
                        fix_hint=(
                            "Put a comparison or a Convert-to-signal block between the two."
                        ),
                    )
                )

        # Requirement 7.10: one action path trades one symbol unless something in the
        # closure allocates across symbols.
        closure = _reachable_from(reverse_adjacency, (action_id,))
        symbols: Dict[str, List[str]] = {}
        allocator = False
        for node_id in closure:
            node = node_index.get(node_id)
            if node is None:
                continue
            descriptor = descriptors.get(node_id)
            flags = getattr(descriptor, "capability_flags", ()) or ()
            if PORTFOLIO_ALLOCATION_FLAG in flags:
                allocator = True
            if _category_of(node, descriptor) is not BlockCategory.DATA:
                continue
            symbol = node.params.get("symbol")
            if isinstance(symbol, str) and symbol:
                symbols.setdefault(symbol, []).append(node_id)
        if len(symbols) > 1 and not allocator:
            named = sorted(symbols)
            report.add(
                make_issue(
                    CODE_MULTI_SYMBOL_ACTION_PATH,
                    SEVERITY_ERROR,
                    f"'{action_id}' descends from more than one traded symbol "
                    f"({', '.join(named)}) with no allocation block to decide between "
                    f"them.",
                    node_id=action_id,
                    field_name="symbol",
                    expected="one traded symbol per action path",
                    actual=named,
                    fix_hint=(
                        "Split this into one action per symbol, or route both through an "
                        "allocation block."
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Stage 10b: structural leakage detection
# (design.md -> Data leakage protection, mechanism 1: validate_no_lookahead)
# ---------------------------------------------------------------------------


def _reverse_adjacency_of(
    edges: Sequence[EdgeSpec], known: Optional[Set[str]] = None
) -> Dict[str, Tuple[str, ...]]:
    """``target -> upstream node ids``: the edge set walked backwards.

    Used to answer "which nodes reach a model?" in one traversal. The pipeline already
    derives this and passes it in; this exists so the rule is also callable standalone.
    """
    out: Dict[str, List[str]] = {}
    seen: Set[Tuple[str, str]] = set()
    for edge in edges:
        if known is not None and (edge.source not in known or edge.target not in known):
            continue
        key = (edge.target, edge.source)
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(edge.target, []).append(edge.source)
    return {target: tuple(sources) for target, sources in out.items()}


def _leakage_risk_name(descriptor: Any) -> str:
    """The descriptor's leakage classification as a plain upper-case name.

    ``indicators_backend`` publishes ``str`` risks and ``feature_engineering`` publishes a
    ``LeakageRisk`` enum; the registry normalises to its own enum, but this reads either so
    the rule cannot be defeated by which module a descriptor came from.
    """
    risk = getattr(descriptor, "leakage_risk", None)
    if risk is None:
        return "NONE"
    value = getattr(risk, "value", risk)
    return str(value).strip().upper()


def _leaky_output_ports(descriptor: Any) -> Tuple[str, ...]:
    """Output port names whose *own* value encodes a future bar.

    Read from the descriptor metadata ``indicators_backend`` populates from its per-port
    ``leakage_risk`` (``IndicatorSpec.leaky_outputs``). Per-port precision matters: Ichimoku
    publishes five outputs and only ``chikou`` reads forward, so flagging the whole block
    would refuse ``tenkan``/``kijun`` - legitimate, past-only lines - on any model path.
    """
    metadata = getattr(descriptor, "metadata", None) or {}
    raw = metadata.get("leaky_outputs") if isinstance(metadata, Mapping) else None
    if not raw:
        return ()
    return tuple(str(name) for name in raw)


def _review_is_discharged(descriptor: Any, node: NodeSpec) -> bool:
    """True when a ``REVIEW_REQUIRED`` block has structurally discharged its review.

    Both halves must hold: the descriptor declares a flag from
    :data:`REVIEW_DISCHARGED_BY_FLAG` (server-side data the client cannot supply), *and*
    the node's own parameters actually carry the safe value. A declaration alone is not
    enough - that would let a future edit widen ``fit_on`` and silently keep the pass.
    """
    flags = getattr(descriptor, "capability_flags", None) or frozenset()
    if not (REVIEW_DISCHARGED_BY_FLAG & frozenset(str(f) for f in flags)):
        return False
    if "fit_on_train_split" in {str(f) for f in flags}:
        return str(node.params.get("fit_on", "train_split")) == "train_split"
    return True


def _coerce_int(value: Any) -> Optional[int]:
    """``value`` as an int, or ``None`` when it is not a number.

    A non-numeric parameter is stage 3's problem (``PARAM_TYPE_INVALID``); this stage must
    not raise on it, and must not treat "unreadable" as "safe" either - it simply declines
    to judge, and the graph is already blocked by the earlier stage.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_no_lookahead(
    graph: StrategyGraph,
    registry: Any = None,
    *,
    descriptors: Optional[Mapping[str, Any]] = None,
    node_index: Optional[Mapping[str, NodeSpec]] = None,
    reverse_adjacency: Optional[Mapping[str, Tuple[str, ...]]] = None,
) -> List[Dict[str, Any]]:
    """Reject every structural path by which a future bar could reach a model.

    Three independent checks, matching ``design.md`` -> Data leakage protection:

    (a) **Negative shift.** A ``shift`` block with ``bars < 0`` reads forward in time.
        Reported wherever it appears, not only on a model path: ``shift`` is documented
        backward-only and its runtime (``block_specs.safe_math_apply``) raises on a
        negative value, so a graph carrying one is broken regardless of what consumes it.
        Requirement 18.2 asks for the model-path case; reporting the superset satisfies it
        and additionally names a graph that would fail at execution. Whether the node
        reaches a model is carried on the issue so a caller can tell the two apart.
    (b) **A future-encoding output on a path into a model.** Port-precise where the
        descriptor declares which outputs leak, block-wide otherwise. See
        :data:`REVIEW_DISCHARGED_BY_FLAG` for why a procedurally-risky block that has
        declared its review discharged is not caught here.
    (c) **Whole-series statistics.** ``mode = "global"`` on a z-score or a normalisation
        fits on data the model will not have at bar ``t``, including the test split.

    Preconditions
        ``graph`` is registry-resolved. A node whose block did not resolve carries an
        ``UNRESOLVED_BLOCK`` error from stage 2 and is skipped here rather than guessed at:
        an unknown block's leakage cannot be asserted either way.

    Postconditions
        Every returned issue is a full structured error-contract entry naming the node, and
        the result is sorted by ``(node_id, code, field)`` - so the same graph yields a
        byte-identical issue list regardless of node or edge ordering.

    Loop invariants
        ``ml_reachable`` is computed **once**, before any node is examined, and never
        mutated inside the loop. Every node is therefore classified against the same
        reachability set. Computing it per node would make the verdict depend on iteration
        order, and the same graph would validate differently across two processes.
    """
    resolved_registry = _resolve_registry(registry)
    nodes = list(graph.nodes)
    index: Mapping[str, NodeSpec] = (
        node_index if node_index is not None else {node.id: node for node in nodes}
    )
    resolved_descriptors: Mapping[str, Any] = (
        descriptors
        if descriptors is not None
        else {
            node.id: _descriptor(resolved_registry, node.block_id) for node in nodes
        }
    )
    reverse = (
        reverse_adjacency
        if reverse_adjacency is not None
        else _reverse_adjacency_of(graph.edges, set(index))
    )

    # ---- computed ONCE, before the loop (order-independence) --------------
    ml_node_ids = sorted(
        node_id
        for node_id, descriptor in resolved_descriptors.items()
        if descriptor is not None
        and _category_of(index[node_id], descriptor) is BlockCategory.ML_DL
    )
    ml_reachable: Set[str] = _reachable_from(reverse, ml_node_ids)

    issues: List[Dict[str, Any]] = []

    for node in nodes:
        descriptor = resolved_descriptors.get(node.id)
        if descriptor is None:
            continue  # stage 2 already reported it; its contract is unknown.

        category = _category_of(node, descriptor)
        block_id = str(node.block_id)
        metadata = getattr(descriptor, "metadata", None) or {}
        operator = (
            str(metadata.get("operator", "")).lower()
            if isinstance(metadata, Mapping)
            else ""
        )
        feeds_model = node.id in ml_reachable

        # -- (a) explicit forward shift ---------------------------------
        if block_id == "shift" or operator == "shift":
            bars = _coerce_int(node.params.get("bars"))
            if bars is not None and bars < 0:
                issues.append(
                    make_issue(
                        CODE_LOOKAHEAD_SHIFT,
                        SEVERITY_ERROR,
                        f"'{getattr(descriptor, 'display_name', block_id)}' shifts by "
                        f"{bars} bars, which reads future bars. Only positive (backward) "
                        f"shifts are allowed."
                        + (
                            " This node feeds a model."
                            if feeds_model
                            else ""
                        ),
                        node_id=node.id,
                        field_name="bars",
                        expected="bars >= 1 (backward only)",
                        actual=bars,
                        fix_hint=(
                            f"Use {abs(bars)} to read {abs(bars)} closed bars back. A "
                            f"forward shift is never available at trading time."
                        ),
                    )
                )

        # -- (c) whole-series statistics --------------------------------
        # Checked before (b) only for readability; the checks are independent and a node
        # can legitimately raise both.
        is_global_statistic_block = bool(
            metadata.get("global_statistic_block")
            if isinstance(metadata, Mapping)
            else False
        )
        if is_global_statistic_block or block_id in ("feat_zscore", "feat_normalize"):
            mode = str(node.params.get("mode", "") or "").strip().lower()
            if mode == GLOBAL_STATISTIC_MODE:
                issues.append(
                    make_issue(
                        CODE_GLOBAL_STATISTIC_LEAK,
                        SEVERITY_ERROR,
                        f"'{getattr(descriptor, 'display_name', block_id)}' computes its "
                        f"statistics over the whole series, which leaks validation and "
                        f"test information into every bar.",
                        node_id=node.id,
                        field_name="mode",
                        expected="a trailing window, e.g. 'rolling'",
                        actual=GLOBAL_STATISTIC_MODE,
                        fix_hint=(
                            "Set mode to 'rolling' so each bar uses only the bars before "
                            "it."
                        ),
                    )
                )

        # -- (b) an output that encodes the future, reaching a model -----
        if _leakage_risk_name(descriptor) != "REVIEW_REQUIRED":
            continue
        if category is BlockCategory.ML_DL:
            continue  # a model is not its own leak
        if _review_is_discharged(descriptor, node):
            continue

        leaky_ports = _leaky_output_ports(descriptor)
        display = getattr(descriptor, "display_name", block_id)

        if leaky_ports:
            # Port-precise: only a connection *leaving a leaky port* carries the leak.
            for edge in graph.edges:
                if edge.source != node.id or edge.source_port not in leaky_ports:
                    continue
                if edge.target not in ml_reachable:
                    continue
                issues.append(
                    make_issue(
                        CODE_LEAKY_FEATURE_INTO_MODEL,
                        SEVERITY_ERROR,
                        f"'{display}' output '{edge.source_port}' encodes a future bar, "
                        f"and this connection carries it into a model.",
                        node_id=node.id,
                        edge_id=edge.id,
                        field_name=edge.source_port,
                        expected="no future-encoding output on a path into a model",
                        actual=f"{node.id}.{edge.source_port} -> {edge.target}",
                        fix_hint=(
                            f"Disconnect '{edge.source_port}' from the model path. Use a "
                            f"past-only output of '{display}' instead."
                        ),
                    )
                )
        elif feeds_model:
            issues.append(
                make_issue(
                    CODE_LEAKY_FEATURE_INTO_MODEL,
                    SEVERITY_ERROR,
                    f"'{display}' encodes future information and cannot feed a model.",
                    node_id=node.id,
                    expected="no future-encoding block on a path into a model",
                    actual=f"{block_id} reaches a model node",
                    fix_hint=(
                        f"Remove '{display}' from the model path, or replace it with a "
                        f"block that reads only closed past bars."
                    ),
                )
            )

    issues.sort(
        key=lambda issue: (
            str(issue.get("node_id") or ""),
            str(issue.get("code") or ""),
            str(issue.get("field") or ""),
            str(issue.get("edge_id") or ""),
        )
    )
    return issues


def leakage_stage(context: ValidationContext) -> List[Dict[str, Any]]:
    """The stage-10b hook: :func:`validate_no_lookahead` over the pipeline's own indices.

    The derived indices are passed through rather than recomputed, so this stage cannot
    disagree with the rest of the pipeline about who feeds whom.
    """
    return validate_no_lookahead(
        context.graph,
        context.registry,
        descriptors=context.descriptors,
        node_index=context.node_index,
        reverse_adjacency=context.reverse_adjacency,
    )


def ml_readiness_stage(context: ValidationContext) -> List[Dict[str, Any]]:
    """The stage-11 hook: the minimum-data gate over the pipeline's own indices.

    Delegates to :func:`ml_training_policy.ml_readiness_stage`, imported lazily. The gate
    itself lives outside this package because it also runs on the job-creation path and in
    the worker, and because it reads the entitlement layer; what it needs *here* - the
    per-model minimums - travels on the registry descriptor's ``metadata["model"]``, so no
    ``ml_models`` import enters ``strategy_dag`` to make this stage work.

    Raises :class:`StageSkipped` when the caller supplied no measured dataset statistics.
    """
    from backend_app.backend.ml_training_policy import (
        ml_readiness_stage as _policy_stage,
    )

    return list(_policy_stage(context))


def install_default_stage_hooks() -> None:
    """Install the seam implementations that have landed.

    Stage 10b (leakage) landed in Phase 5 and stage 11 (ML readiness) in Phase 6. Both are
    installed at import, so every path through :func:`validate` runs both - a rule that had
    to be switched on would be a rule some path forgot. After this call
    :func:`pending_stages` is empty.

    Stage 11 still declines to *judge* a graph it has no dataset statistics for, and says
    so: it raises :class:`StageSkipped` and the report records ``SKIPPED``. Installed and
    skipped is not the same as not installed, and neither is the same as passed.

    Idempotent, and the way to restore the defaults after :func:`clear_stage_hooks`.
    """
    register_leakage_stage(leakage_stage)
    register_ml_readiness_stage(ml_readiness_stage)


def _run_seam(
    report: ValidationReport, number: int, name: str, context: ValidationContext
) -> None:
    """Run a seam stage if something is installed, else say it has not run.

    Three outcomes, deliberately distinct (see :class:`StageSkipped`):
    ``NOT_IMPLEMENTED`` (nothing installed), ``SKIPPED`` (installed, an input it needs was
    absent), ``PASSED``/``FAILED`` (it ran).
    """
    hook = _STAGE_HOOKS.get(name)
    if hook is None:
        report.record_stage(
            number,
            name,
            STATUS_NOT_IMPLEMENTED,
            _SEAM_DESCRIPTIONS.get(name, "Not implemented yet."),
        )
        return
    before = len(report.errors)
    try:
        issues = hook(context) or ()
    except StageSkipped as exc:
        report.record_stage(number, name, STATUS_SKIPPED, str(exc))
        return
    report.extend(issues)
    report.record_stage(
        number,
        name,
        STATUS_PASSED if len(report.errors) == before else STATUS_FAILED,
        f"'{name}' stage ran from its installed implementation.",
    )


# ---------------------------------------------------------------------------
# Install the stages that have landed.
#
# Stages 10b and 11 run on every path through validate() from the moment this module is
# imported. Neither is behind a flag or a caller opt-in: a leakage check or a training gate
# that some call site has to remember to switch on is one that some call site will ship
# without.
# ---------------------------------------------------------------------------

install_default_stage_hooks()
