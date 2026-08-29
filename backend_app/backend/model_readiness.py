"""
backend_app/backend/model_readiness.py

The two Requirement 17 gates that stand between a trained model and a routed order:
the artifact **checksum** gate (17.6) and the feature-schema **drift** gate (17.7).

Spec: strategy-builder task 8.6. ``design.md`` -> "DAG runtime contract" (the
``AWAITING_MODEL`` row of the imperfect-reality table) and "Model artifact checksum
mismatch" / "Model/feature schema drift" in the failure-mode table.

WHY A MODULE, AND WHY IT RAISES NOTHING
---------------------------------------
Requirement 17.6 has **two** enforcers, not one: "THE DAG_Runtime SHALL mark that model
node as awaiting a model **and** THE Deployment_Service SHALL hold the deployment out of
the running state". The runtime wants a *verdict* it can put on a node
(``mark_node(state, node_id, AWAITING_MODEL)``); the Deployment_Service wants a
*refusal* it can turn into an HTTP status. One exception type cannot serve both without
the runtime catching exceptions on its hot path, so every function here returns a
verdict and **nothing here raises** a refusal.

``deployment_binding`` is the single place that turns a verdict into a
:class:`~backend_app.backend.deployment_binding.DeployRejected`, so the deploy
vocabulary stays in the one file task 8.2 put it in, and this module stays importable by
a runtime that must not depend on the deploy gate (or on FastAPI, or on a database
client). That is the same seam argument 8.2 made for putting the Requirement 13 gates in
``backend/`` rather than in the router, applied one layer in.

WHAT IS REUSED, VERBATIM
------------------------
* ``core.ml_safety.SafeModelLoader.compute_checksum`` - the SHA-256 routine. Reached
  through ``model_versioning``'s ``ArtifactStore.checksum``, which is where task 6.5 put
  it: ``LocalArtifactStore.checksum`` **is** ``SafeModelLoader.compute_checksum`` over
  the stored file, and ``SupabaseStorageArtifactStore.checksum`` is the same digest over
  the fetched bytes. There is no second checksum routine here, and no second idea of
  what "the checksum of what is stored" means.
* ``backend.feature_validator.FeatureValidator.check_model_compatibility`` - the
  comparison. It is the authority on whether a model and a feature frame agree, and it
  is called with the real recorded contract on one side and the real graph-declared
  columns on the other.
* ``dag_engine._feature_column_names`` - the naming authority. Its own docstring says it
  is "deterministic and derived only from the block id and the author's params, so the
  same node always names its columns the same way - which is what lets a trained model's
  recorded ``feature_schema`` still match the graph that produced it". This module is the
  reader that claim was written for; it does not re-derive a single column name.

WHY THE ARTIFACT IS **NOT** DESERIALIZED HERE
---------------------------------------------
``SafeModelLoader.load_model(expected_checksum=...)`` verifies the checksum *and then*
``joblib.load``s the file. Deserializing a pickle is arbitrary code execution and an
unbounded allocation; doing it inside a deploy request would put both on an
authenticated HTTP path, and ``MemoryMonitor``'s ceiling is enforced at *store* time
(task 6.5's ``max_artifact_bytes``), not at load time. So the gate is the digest alone:
it is the whole of what 17.6 asks about ("IF a model artifact's computed checksum
differs from its recorded checksum"), and it answers the question without loading
anything. ``load_model(expected_checksum=...)`` stays the *runtime's* loader, where the
model is going to be executed anyway and where ``TrainingIsolator``'s limits apply.

WHY THE DRIFT CHECK IS STATIC, AND WHAT THAT COSTS
--------------------------------------------------
Requirement 17.7 compares the recorded schema against "the feature output of the
version's **current** Canonical_Graph". Producing that output for real means running the
feature pipeline, which means a validated market-data window - a network fetch on the
deploy path whose failure modes ("the venue was slow") have nothing to do with drift.
So :func:`declared_feature_columns` walks the ML node's upstream feature closure in the
version's own ``CompiledPlan`` and asks each feature node what it *names* its columns,
through ``dag_engine._feature_column_names`` and the two composition rules
(``feat_concat``'s ``_unique_name`` suffixing, ``feat_select``'s projection). Every
FEATURE_ENGINEERING block in ``FEATURE_SPECS`` names its columns from ``block_id`` plus
params alone, so the walk is exact for a published graph.

What it costs, stated: the walk knows column **names**, not column **values**. A block
whose formula changed while its params and column name did not is drift this gate cannot
see - that is what ``dag_hash`` and Requirement 22.5's recompilation disposition are
for, and it is not claimed here. And a block the walk cannot name at all is
``FEATURE_COLUMNS_UNDETERMINED``: a refusal, not a pass, because a schema that cannot be
compared is not one that matched.

Order is compared but **not** refused on. ``FeatureValidator.validate_features``
projects the inference frame onto ``schema.expected_features`` before the model sees it
(``feature_df = feature_df[schema.expected_features]``), so a graph that produces the
same column names in a different order is normalised at inference rather than wrong. The
ordered lists are still reported, and an order-only difference is reported as a
*warning*, so the author sees it without a deploy being refused for something the
runtime fixes.

FAIL CLOSED
-----------
Every "cannot tell" is a refusal:

* no ``artifact_uri`` or no ``artifact_checksum`` on the row -> ``AWAITING_MODEL``
* the store cannot be reached, the object is missing, the digest raises -> ``AWAITING_MODEL``
* the recorded ``feature_schema`` carries no ``feature_names`` -> refused
* the plan's feature closure cannot be named -> refused
* the ML node the schema was recorded for is not an ML node of this plan -> refused
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("ModelReadiness")


# ══════════════════════════════════════════════════════════════════════════
# RUNTIME STATE LABELS - design.md's vocabulary, spelled once
# ══════════════════════════════════════════════════════════════════════════

#: ``design.md`` -> "DAG runtime contract": ``mark_node(state, node_id, AWAITING_MODEL)``.
#: Published here so task 8.4's ``execute_plan`` and this gate cannot disagree about the
#: label a checksum failure puts on a node. **Task 8.4 owns the state machine** - which
#: transitions exist, when they fire, and what ``state`` is - and should import these
#: rather than re-spelling them. Nothing in this module transitions anything.
AWAITING_MODEL = "AWAITING_MODEL"
NODE_READY = "READY"

#: What a verdict's ``code`` can be. Stable, because ``deployment_binding`` maps them to
#: HTTP statuses and a test asserts the mapping.
CODE_MODEL_READY = "MODEL_READY"
CODE_ARTIFACT_REFERENCE_MISSING = "ARTIFACT_REFERENCE_MISSING"
CODE_ARTIFACT_CHECKSUM_MISSING = "ARTIFACT_CHECKSUM_MISSING"
CODE_ARTIFACT_CHECKSUM_MISMATCH = "ARTIFACT_CHECKSUM_MISMATCH"
CODE_ARTIFACT_UNREADABLE = "ARTIFACT_UNREADABLE"

CODE_FEATURE_SCHEMA_MATCHES = "FEATURE_SCHEMA_MATCHES"
CODE_FEATURE_SCHEMA_DRIFT = "FEATURE_SCHEMA_DRIFT"
CODE_FEATURE_SCHEMA_NOT_RECORDED = "FEATURE_SCHEMA_NOT_RECORDED"
CODE_FEATURE_COLUMNS_UNDETERMINED = "FEATURE_COLUMNS_UNDETERMINED"
CODE_MODEL_NODE_NOT_IN_PLAN = "MODEL_NODE_NOT_IN_PLAN"
CODE_MODEL_BLOCK_CHANGED = "MODEL_BLOCK_CHANGED"

#: Reported on an order-only difference. Not a refusal - see the module docstring.
WARNING_FEATURE_ORDER_DIFFERS = "FEATURE_ORDER_DIFFERS"


# ══════════════════════════════════════════════════════════════════════════
# VERDICTS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ArtifactVerdict:
    """The 17.6 answer for one model version.

    ``ok`` is the runtime's ``model_ready(node, state)``; ``runtime_state`` is the label
    it puts on the node when ``ok`` is false; ``code``/``message``/``details`` are what
    the Deployment_Service reports.
    """

    ok: bool
    node_id: str
    model_version_id: Optional[str]
    code: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def runtime_state(self) -> str:
        return NODE_READY if self.ok else AWAITING_MODEL


@dataclass(frozen=True)
class FeatureSchemaVerdict:
    """The 17.7 answer for one model version.

    ``expected`` and ``actual`` are the **ordered** column lists Requirement 17.7 asks
    to be reported: ``expected`` is what the model was trained on, ``actual`` is what the
    version's current canonical graph declares it now produces. Either may be ``None``
    when that side could not be determined, which is itself a refusal.
    """

    ok: bool
    node_id: str
    model_version_id: Optional[str]
    code: str
    message: str
    expected: Optional[List[str]] = None
    actual: Optional[List[str]] = None
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: Tuple[Dict[str, Any], ...] = ()


# ══════════════════════════════════════════════════════════════════════════
# GATE 1 - THE ARTIFACT CHECKSUM (Requirement 17.6)
# ══════════════════════════════════════════════════════════════════════════


def _row_node_id(row: Mapping[str, Any]) -> str:
    return str(row.get("node_id") or "")


def _row_model_version_id(row: Mapping[str, Any]) -> Optional[str]:
    value = row.get("id")
    return str(value) if value else None


def verify_artifact_checksum(
    row: Mapping[str, Any], *, store: Any = None
) -> ArtifactVerdict:
    """Compare the artifact's **computed** checksum against the row's **recorded** one.

    Requirement 17.6's condition, verbatim: "IF a model artifact's computed checksum
    differs from its recorded checksum". Task 6.5 wrote that recorded value with
    ``SafeModelLoader.compute_checksum`` over the bytes *as actually stored* - read back
    after the write, not the bytes it meant to write - so recomputing it here through the
    same store method compares like with like.

    Never raises. A store that cannot be reached, an object that is not there, a digest
    that blows up: all of them are "this artifact's checksum could not be verified",
    which is not "it passed". The node holds :data:`AWAITING_MODEL` either way, so the
    runtime needs no ``except`` around this call and the deploy gets a classified
    refusal instead of a 500.
    """
    node_id = _row_node_id(row)
    model_version_id = _row_model_version_id(row)
    uri = str(row.get("artifact_uri") or "")
    recorded = str(row.get("artifact_checksum") or "")

    if not uri:
        return ArtifactVerdict(
            False,
            node_id,
            model_version_id,
            CODE_ARTIFACT_REFERENCE_MISSING,
            f"The active model version for node {node_id!r} records no artifact "
            f"reference, so there is no artifact whose checksum could be verified.",
            {"node_id": node_id, "model_version_id": model_version_id},
        )

    if not recorded:
        # 004d declares artifact_checksum NOT NULL, so an empty one means the row was
        # written by something other than task 6.5's writer. Refused rather than skipped:
        # "no recorded checksum" makes the comparison vacuous, and a vacuous comparison
        # that returns True is exactly the hole 17.6 exists to close.
        return ArtifactVerdict(
            False,
            node_id,
            model_version_id,
            CODE_ARTIFACT_CHECKSUM_MISSING,
            f"The active model version for node {node_id!r} records no artifact "
            f"checksum, so its artifact cannot be verified against anything.",
            {
                "node_id": node_id,
                "model_version_id": model_version_id,
                "artifact_uri": uri,
            },
        )

    try:
        target = store if store is not None else _current_store()
        computed = str(target.checksum(uri))
    except Exception as exc:  # noqa: BLE001 - fail closed, never propagate
        logger.warning(
            "Model artifact %s for node %s could not be checksummed, so its model "
            "version stays %s: %s",
            uri,
            node_id,
            AWAITING_MODEL,
            exc,
        )
        return ArtifactVerdict(
            False,
            node_id,
            model_version_id,
            CODE_ARTIFACT_UNREADABLE,
            f"The artifact for node {node_id!r} could not be read back and "
            f"checksummed, so it cannot be confirmed to be the artifact this model "
            f"version was recorded with. Detail: {exc}",
            {
                "node_id": node_id,
                "model_version_id": model_version_id,
                "failure": type(exc).__name__,
            },
        )

    if computed != recorded:
        logger.error(
            "Model artifact checksum mismatch for node %s (model version %s): recorded "
            "%s..., computed %s.... The node holds %s and the deployment is refused.",
            node_id,
            model_version_id,
            recorded[:16],
            computed[:16],
            AWAITING_MODEL,
        )
        return ArtifactVerdict(
            False,
            node_id,
            model_version_id,
            CODE_ARTIFACT_CHECKSUM_MISMATCH,
            f"The artifact for node {node_id!r} checksums to {computed[:16]}... but its "
            f"model version records {recorded[:16]}.... The stored artifact is not the "
            f"one this model was trained into, so it is never loaded.",
            {
                "node_id": node_id,
                "model_version_id": model_version_id,
                "recorded_checksum": recorded,
                "computed_checksum": computed,
            },
        )

    return ArtifactVerdict(
        True,
        node_id,
        model_version_id,
        CODE_MODEL_READY,
        f"The artifact for node {node_id!r} matches its recorded checksum.",
        {
            "node_id": node_id,
            "model_version_id": model_version_id,
            "recorded_checksum": recorded,
        },
    )


def _current_store() -> Any:
    from backend_app.backend.model_versioning import current_artifact_store

    return current_artifact_store()


def model_ready(row: Optional[Mapping[str, Any]], *, store: Any = None) -> bool:
    """``design.md``'s ``model_ready(node, state)``, for one ``model_versions`` row.

    The seam task 8.4's ``execute_plan`` calls: ``IF node.category = ML_DL AND NOT
    model_ready(node, state) THEN mark_node(state, node_id, AWAITING_MODEL)``. ``None``
    - no active model version for that node at all - is not ready, which is the
    "unloaded" half of that line.
    """
    if not row:
        return False
    return verify_artifact_checksum(row, store=store).ok


# ══════════════════════════════════════════════════════════════════════════
# GATE 2 - FEATURE SCHEMA DRIFT (Requirement 17.7)
# ══════════════════════════════════════════════════════════════════════════

#: Feature blocks whose output columns are the columns of the matrix arriving on their
#: ``matrix`` port, unchanged. ``feat_standardize`` scales in place; a scaler changes
#: values, never names (``FeatureEngine.compute_standardize`` returns a matrix built from
#: ``list(matrix.columns)``).
_MATRIX_PASSTHROUGH_BLOCKS: Tuple[str, ...] = ("feat_standardize",)

#: Feature blocks whose runtime returns ``(values, column_names)`` - it names its own
#: columns - mapped to the param the names come from and the stem they are prefixed with.
#: ``FeatureEngine.compute_time_features`` appends ``f"time_{part}"`` for each requested
#: part, in the requested order.
_SELF_NAMING_BLOCKS: Dict[str, Tuple[str, str]] = {
    "feat_time": ("parts", "time"),
}

#: Feature blocks producing one column per entry of a list param, and the param. The
#: *names* still come from ``dag_engine._feature_column_names``; this table only says how
#: many columns there will be, which is the one thing that function has to be told.
_COUNT_FROM_LIST_PARAM: Dict[str, str] = {
    "feat_lag": "lags",
    "feat_returns": "periods",
    "feat_momentum": "windows",
}

#: Feature blocks producing a fixed number of columns.
_FIXED_COUNT_BLOCKS: Dict[str, int] = {"feat_volume": 3}


def _feature_matrix_input_ports(descriptor: Any) -> List[str]:
    from backend_app.backend.strategy_service import _feature_matrix_ports

    return _feature_matrix_ports(descriptor)


def _list_param(params: Mapping[str, Any], key: str) -> Optional[List[Any]]:
    raw = params.get(key)
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return None


class _Undetermined(Exception):
    """The walk cannot name a node's columns. Carries the reason, never a guess."""

    def __init__(self, node_id: str, block_id: str, reason: str):
        super().__init__(reason)
        self.node_id = node_id
        self.block_id = block_id
        self.reason = reason


def _matrix_inputs(plan: Any, node_id: str, descriptor: Any) -> List[Tuple[str, str]]:
    """``(source_node, source_port)`` for every FEATURE_MATRIX edge into ``node_id``."""
    sources: List[Tuple[str, str]] = []
    for port in _feature_matrix_input_ports(descriptor):
        for edge in plan.inbound_edges(node_id, port):
            sources.append((edge.source, edge.source_port))
    return sources


def _columns_of(
    plan: Any,
    registry: Any,
    node_id: str,
    seen: Optional[set] = None,
) -> List[str]:
    """The feature columns node ``node_id`` publishes, named as the runtime names them.

    Recursive over the plan's inbound edges. Raises :class:`_Undetermined` rather than
    inventing a name for anything it cannot resolve.
    """
    from backend_app.backend.dag_engine import _feature_column_names
    from backend_app.backend.strategy_dag.feature_matrix import _unique_name

    seen = set(seen or ())
    if node_id in seen:
        # A plan is a DAG, so this is unreachable through a compiled plan; reaching it
        # means the stored plan is not a DAG, which is not something to loop on.
        raise _Undetermined(node_id, "", "the stored plan contains a cycle")
    seen.add(node_id)

    node = plan.node(node_id)
    if node is None:
        raise _Undetermined(node_id, "", "the plan declares no such node")

    block_id = str(node.block_id or "")
    params = dict(getattr(node, "params", None) or {})
    descriptor = registry.get(block_id) if registry is not None else None
    if descriptor is None:
        raise _Undetermined(
            node_id, block_id, "the registry publishes no descriptor for this block"
        )

    upstream = _matrix_inputs(plan, node_id, descriptor)

    if block_id == "feat_concat":
        if not upstream:
            raise _Undetermined(
                node_id, block_id, "no feature matrix is connected to it"
            )
        merged: List[str] = []
        for source_node, _port in upstream:
            for name in _columns_of(plan, registry, source_node, seen):
                merged.append(_unique_name(name, merged))
        return merged

    if block_id == "feat_select":
        wanted = _list_param(params, "columns")
        if wanted is None:
            raise _Undetermined(
                node_id, block_id, "its 'columns' param is not a list of column names"
            )
        return [str(name) for name in wanted]

    if block_id in _MATRIX_PASSTHROUGH_BLOCKS:
        if len(upstream) != 1:
            raise _Undetermined(
                node_id,
                block_id,
                f"it passes its input matrix through but has {len(upstream)} feature "
                f"matrix inputs",
            )
        return _columns_of(plan, registry, upstream[0][0], seen)

    self_naming = _SELF_NAMING_BLOCKS.get(block_id)
    if self_naming is not None:
        key, stem = self_naming
        parts = _list_param(params, key)
        if parts is None:
            raise _Undetermined(
                node_id, block_id, f"its {key!r} param is not a list"
            )
        return [f"{stem}_{part}" for part in parts]

    count_param = _COUNT_FROM_LIST_PARAM.get(block_id)
    if count_param is not None:
        entries = _list_param(params, count_param)
        if entries is None:
            raise _Undetermined(
                node_id, block_id, f"its {count_param!r} param is not a list"
            )
        return _feature_column_names(block_id, params, len(entries))

    fixed = _FIXED_COUNT_BLOCKS.get(block_id)
    if fixed is not None:
        return _feature_column_names(block_id, params, fixed)

    category = getattr(getattr(descriptor, "category", None), "value", None) or str(
        getattr(descriptor, "category", "")
    )
    if category != "FEATURE_ENGINEERING":
        raise _Undetermined(
            node_id,
            block_id,
            f"a {category or 'non-feature'} block is feeding a feature matrix port",
        )

    # Every remaining FEATURE_SPECS block publishes exactly one column, named from its
    # block id and the params in dag_engine._FEATURE_NAME_PARAMS.
    return _feature_column_names(block_id, params, 1)


def declared_feature_columns(
    plan: Any, ml_node_id: str, registry: Any = None
) -> Tuple[Optional[List[str]], Optional[str]]:
    """``(columns, undetermined_reason)`` for the features reaching ``ml_node_id``.

    The "actual" side of Requirement 17.7: what the version's current canonical graph
    declares it produces for this model node, in the order the runtime would assemble
    it. Exactly one of the two return values is ``None``.

    Multiple matrices on the ML node's feature ports are merged the way
    ``feature_matrix.concat_matrices`` merges them - in edge order, with duplicate names
    suffixed by ``_unique_name`` - because that is what
    ``strategy_service.run_feature_pipeline`` does before ``check_feature_schema``
    records the schema. Joining them any other way here would make this gate disagree
    with the run that produced the number it is comparing against.
    """
    from backend_app.backend.strategy_dag.feature_matrix import _unique_name

    if registry is None:
        from backend_app.backend.strategy_dag.registry import get_registry

        registry = get_registry()

    node = plan.node(ml_node_id)
    if node is None:
        return None, f"the plan declares no node {ml_node_id!r}"
    descriptor = registry.get(str(node.block_id or ""))
    if descriptor is None:
        return None, (
            f"the registry publishes no descriptor for {node.block_id!r}, so "
            f"{ml_node_id!r}'s feature ports are unknown"
        )

    sources = _matrix_inputs(plan, ml_node_id, descriptor)
    if not sources:
        return None, (
            f"no feature matrix is connected to {ml_node_id!r}, so the graph declares no "
            f"feature columns for it"
        )

    merged: List[str] = []
    try:
        for source_node, _port in sources:
            for name in _columns_of(plan, registry, source_node):
                merged.append(_unique_name(name, merged) if len(sources) > 1 else name)
    except _Undetermined as exc:
        return None, (
            f"node {exc.node_id!r}"
            + (f" (block {exc.block_id!r})" if exc.block_id else "")
            + f" does not declare its feature columns: {exc.reason}"
        )
    return merged, None


@dataclass(frozen=True)
class _RecordedContract:
    """The recorded ``feature_schema``, shaped like the model it was recorded for.

    ``FeatureValidator.check_model_compatibility`` reads exactly two attributes off the
    model it is given: ``n_features_in_`` and ``feature_names_in_`` - sklearn's own
    declaration of the input contract a fitted estimator accepts. Task 6.5 recorded that
    same contract as ``feature_schema.feature_count`` and ``feature_schema.feature_names``
    precisely so it could be compared without the artifact. Presenting it under sklearn's
    attribute names lets the real check run against it, rather than a second comparison
    being written here.
    """

    n_features_in_: int
    feature_names_in_: Tuple[str, ...]


def recorded_feature_columns(
    feature_schema: Any,
) -> Tuple[Optional[List[str]], Optional[int]]:
    """``(feature_names, feature_count)`` from a recorded ``feature_schema`` document.

    Tolerant about the container (JSONB comes back as a dict; a test may pass a mapping)
    and strict about the content: names that are not a non-empty list are ``None``, which
    the caller turns into a refusal.
    """
    if not isinstance(feature_schema, Mapping):
        return None, None
    raw = feature_schema.get("feature_names")
    names = (
        [str(name) for name in raw]
        if isinstance(raw, (list, tuple)) and raw
        else None
    )
    try:
        count = int(feature_schema.get("feature_count"))
    except (TypeError, ValueError):
        count = len(names) if names is not None else None
    return names, count


def compare_feature_schema(
    row: Mapping[str, Any], plan: Any, registry: Any = None
) -> FeatureSchemaVerdict:
    """Requirement 17.7 for one model version against one plan.

    Runs ``FeatureValidator.check_model_compatibility`` with the recorded contract as the
    model and the graph-declared columns as the frame, and reports **expected versus
    actual feature columns** whichever way it goes.

    Never raises: a drift is a verdict, and so is an inability to determine either side.
    """
    import pandas as pd

    from backend_app.backend.feature_validator import (
        FeatureValidator,
        ModelMismatchError,
    )

    node_id = _row_node_id(row)
    model_version_id = _row_model_version_id(row)
    expected, expected_count = recorded_feature_columns(row.get("feature_schema"))

    base: Dict[str, Any] = {"node_id": node_id, "model_version_id": model_version_id}

    if expected is None:
        return FeatureSchemaVerdict(
            False,
            node_id,
            model_version_id,
            CODE_FEATURE_SCHEMA_NOT_RECORDED,
            f"The active model version for node {node_id!r} records no feature column "
            f"names, so what it was trained on cannot be compared with what this "
            f"version's graph now produces.",
            None,
            None,
            dict(base),
        )

    # The node must still be a model node of this plan, and still the same block. A
    # schema recorded for a node the graph no longer has - or for a different model
    # block on the same id - is the starkest form of the drift 17.7 describes, and
    # comparing columns across it would be comparing two unrelated things.
    plan_ml_nodes = [str(nid) for nid in (getattr(plan, "ml_nodes", None) or ())]
    if node_id not in plan_ml_nodes:
        return FeatureSchemaVerdict(
            False,
            node_id,
            model_version_id,
            CODE_MODEL_NODE_NOT_IN_PLAN,
            f"This version's graph declares no model node {node_id!r} (its model nodes "
            f"are {plan_ml_nodes or 'none'}), so the model bound to that node was "
            f"trained for a graph this version no longer is.",
            list(expected),
            None,
            {**base, "plan_ml_nodes": plan_ml_nodes},
        )

    recorded_block = str(row.get("block_id") or "")
    plan_node = plan.node(node_id)
    plan_block = str(getattr(plan_node, "block_id", "") or "")
    if recorded_block and plan_block and recorded_block != plan_block:
        return FeatureSchemaVerdict(
            False,
            node_id,
            model_version_id,
            CODE_MODEL_BLOCK_CHANGED,
            f"Node {node_id!r} is a {plan_block!r} block in this version's graph but the "
            f"model bound to it was trained as {recorded_block!r}.",
            list(expected),
            None,
            {**base, "recorded_block_id": recorded_block, "plan_block_id": plan_block},
        )

    actual, undetermined = declared_feature_columns(plan, node_id, registry)
    if actual is None:
        return FeatureSchemaVerdict(
            False,
            node_id,
            model_version_id,
            CODE_FEATURE_COLUMNS_UNDETERMINED,
            f"The feature columns this version's graph produces for node {node_id!r} "
            f"could not be determined, so the model's recorded schema cannot be "
            f"confirmed to still match them. Detail: {undetermined}",
            list(expected),
            None,
            {**base, "reason": undetermined},
        )

    contract = _RecordedContract(
        n_features_in_=int(expected_count if expected_count is not None else len(expected)),
        feature_names_in_=tuple(expected),
    )
    frame = pd.DataFrame(columns=list(actual), dtype=float)

    try:
        FeatureValidator.check_model_compatibility(contract, frame)
    except ModelMismatchError as exc:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        return FeatureSchemaVerdict(
            False,
            node_id,
            model_version_id,
            CODE_FEATURE_SCHEMA_DRIFT,
            f"Node {node_id!r}'s model was trained on {len(expected)} feature column(s) "
            f"but this version's graph now produces {len(actual)}. "
            f"Detail: {exc}",
            list(expected),
            list(actual),
            {
                **base,
                "expected_feature_count": len(expected),
                "actual_feature_count": len(actual),
                "missing_feature_columns": missing,
                "extra_feature_columns": extra,
            },
        )

    warnings: List[Dict[str, Any]] = []
    if list(expected) != list(actual):
        warnings.append(
            {
                "code": WARNING_FEATURE_ORDER_DIFFERS,
                "message": (
                    f"Node {node_id!r}'s graph produces the same feature columns in a "
                    f"different order than the model recorded. The inference path "
                    f"projects the frame onto the recorded order before the model sees "
                    f"it, so this is normalised rather than wrong - but a graph edit "
                    f"that reorders features is worth knowing about."
                ),
                "node_id": node_id,
                "expected_feature_columns": list(expected),
                "actual_feature_columns": list(actual),
            }
        )

    return FeatureSchemaVerdict(
        True,
        node_id,
        model_version_id,
        CODE_FEATURE_SCHEMA_MATCHES,
        f"Node {node_id!r}'s recorded feature schema matches the {len(actual)} feature "
        f"column(s) this version's graph produces.",
        list(expected),
        list(actual),
        {
            **base,
            "expected_feature_count": len(expected),
            "actual_feature_count": len(actual),
        },
        tuple(warnings),
    )


# ══════════════════════════════════════════════════════════════════════════
# BOTH GATES, OVER A WHOLE VERSION
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ModelGateReport:
    """Every 17.6/17.7 verdict for one strategy version, and the first refusal.

    ``unbound_nodes`` is separate from the two verdict lists because "this ML node has no
    active model version at all" is Requirement 17.5's business (the version should not
    have reached ``READY``), and reporting it as a checksum failure would misname it.
    """

    version_id: str
    artifacts: Tuple[ArtifactVerdict, ...] = ()
    schemas: Tuple[FeatureSchemaVerdict, ...] = ()
    unbound_nodes: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.unbound_nodes and all(
            v.ok for v in (*self.artifacts, *self.schemas)
        )

    @property
    def warnings(self) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        for verdict in self.schemas:
            collected.extend(dict(w) for w in verdict.warnings)
        return collected

    def first_artifact_failure(self) -> Optional[ArtifactVerdict]:
        return next((v for v in self.artifacts if not v.ok), None)

    def first_schema_failure(self) -> Optional[FeatureSchemaVerdict]:
        return next((v for v in self.schemas if not v.ok), None)


def evaluate_model_gates(
    version_id: str,
    plan: Any,
    model_rows: Mapping[str, Mapping[str, Any]],
    *,
    registry: Any = None,
    store: Any = None,
) -> ModelGateReport:
    """Both gates for every ML node the plan declares.

    ``model_rows`` is ``{node_id: model_versions row}`` - task 6.5's
    ``active_model_versions`` output. The plan is the authority on *which* nodes must be
    checked, so a stray active row for a node the graph no longer has cannot make a
    version look bound (it is caught as ``MODEL_NODE_NOT_IN_PLAN`` if it is checked at
    all, and simply not consulted otherwise).

    Checksum before schema, per node: a corrupt artifact makes the recorded schema
    meaningless, so reporting drift against it would be reporting a derived fact about a
    file we have just established is not the file.
    """
    ml_nodes = [str(nid) for nid in (getattr(plan, "ml_nodes", None) or ())]
    artifacts: List[ArtifactVerdict] = []
    schemas: List[FeatureSchemaVerdict] = []
    unbound: List[str] = []

    for node_id in ml_nodes:
        row = model_rows.get(node_id)
        if not row:
            unbound.append(node_id)
            continue
        artifact = verify_artifact_checksum(row, store=store)
        artifacts.append(artifact)
        if not artifact.ok:
            continue
        schemas.append(compare_feature_schema(row, plan, registry))

    return ModelGateReport(
        version_id=str(version_id),
        artifacts=tuple(artifacts),
        schemas=tuple(schemas),
        unbound_nodes=tuple(unbound),
    )


__all__ = [
    "AWAITING_MODEL",
    "NODE_READY",
    "CODE_ARTIFACT_CHECKSUM_MISMATCH",
    "CODE_ARTIFACT_CHECKSUM_MISSING",
    "CODE_ARTIFACT_REFERENCE_MISSING",
    "CODE_ARTIFACT_UNREADABLE",
    "CODE_FEATURE_COLUMNS_UNDETERMINED",
    "CODE_FEATURE_SCHEMA_DRIFT",
    "CODE_FEATURE_SCHEMA_MATCHES",
    "CODE_FEATURE_SCHEMA_NOT_RECORDED",
    "CODE_MODEL_BLOCK_CHANGED",
    "CODE_MODEL_NODE_NOT_IN_PLAN",
    "CODE_MODEL_READY",
    "WARNING_FEATURE_ORDER_DIFFERS",
    "ArtifactVerdict",
    "FeatureSchemaVerdict",
    "ModelGateReport",
    "compare_feature_schema",
    "declared_feature_columns",
    "evaluate_model_gates",
    "model_ready",
    "recorded_feature_columns",
    "verify_artifact_checksum",
]
