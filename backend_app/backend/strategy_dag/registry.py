"""
backend/strategy_dag/registry.py - the backend-authoritative block registry.

One registry, in the backend, assembled from the engines' real capabilities. It is the
single source of truth for what a block *is*, and it is the mechanism that closes SB-03
(a permanently empty FEATURE_ENGINEERING palette) and SB-04 (``wma`` / ``hma`` /
``catboost`` / ``autoencoder`` runnable but unselectable) at the same time.

Exposes
-------
``ParamSpec``              re-exported from :mod:`block_specs`; there is exactly ONE
                          parameter contract in this codebase, and every family is
                          adapted onto it
``BlockDescriptor``        one registry entry, field-for-field with ``design.md`` ->
                          Descriptor contract
``BlockRegistry``          the assembled catalogue, with port lookup helpers
``build_registry()``       assemble and assert; raises rather than advertising a block
                          the platform cannot run or a category the palette would render
                          empty
``get_registry()``         the memoised registry
``get(block_id)``          module-level descriptor lookup, ``None`` when unknown. This is
                          the seam ``schema._discover_default_resolver`` probes for
``registry_version()``     deterministic hash of the assembled descriptor set
``COMPATIBILITY_MATRIX``   the port-type compatibility rules, published to the client so
                          connect-time checks use the identical rule the backend enforces
``port_type_vocabulary()`` the ``PortType`` vocabulary as served

Consolidation, not authorship
-----------------------------
Nothing here declares a block. Descriptors come from the modules that own the
implementations, and this module only adapts them onto one contract:

* INDICATOR             ``indicators_backend.INDICATOR_SPECS`` (33)
* FEATURE_ENGINEERING   ``feature_engineering.FEATURE_SPECS`` (15)
* ML_DL                 ``ml_models.MODEL_SPECS`` (8, gated on the library importing)
* DATA / MATH / LOGIC   ``block_specs.DATA_SPECS`` / ``MATH_SPECS`` / ``LOGIC_SPECS``
* ACTION                ``block_specs.action_specs()``, generated from the real
                        ``exchange_executor.OrderType``

Restating any of those lists here would recreate exactly the drift mechanism that caused
SB-03 and SB-04, so the adapters read attributes and never literals.

Structural guards (do not soften these into warnings)
-----------------------------------------------------
* **Every advertised ``runtime_ref`` resolves to a callable**, through the resolver owned
  by the module that owns the implementation. A descriptor that cannot be run fails
  assembly naming the block and the reference (Requirements 4.7, 4.8).
* **Every one of the seven ``BlockCategory`` values holds at least one descriptor.** An
  empty category fails assembly naming the category, so an empty palette section is a
  startup failure and a failing test rather than a silently blank panel (Requirement 4.9).
  This is the guard that makes SB-03 unrepresentable.
* **A model block whose library is absent is omitted, never advertised.** Availability is
  read from ``ModelSpec.backend_available``, which ``ml_models`` measures with a real
  import probe (Requirements 4.5, 4.6).

Purity
------
Importing this module pulls in the four descriptor sources and NumPy - no FastAPI, no
database handle, no CCXT. ``exchange_executor`` is reached only when ACTION descriptors are
generated, inside ``build_registry()``, preserving the lazy seam ``block_specs`` already
established. Assembly is therefore explicit: importing the registry costs nothing, and the
compiler/validator import path stays light (Requirement 21.10).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from backend_app.backend import feature_engineering as _features
from backend_app.backend import indicators_backend as _indicators
from backend_app.backend import ml_models as _models
from backend_app.backend.strategy_dag import block_specs as _blocks
from backend_app.backend.strategy_dag.block_specs import (
    BlockSpec,
    ExecutionSemantics,
    LeakageRisk,
    ParamSpec,
    ParamType,
    Serialization,
)
from backend_app.backend.strategy_dag.schema import (
    BlockCategory,
    Port,
    PortType,
    canonicalize,
)

logger = logging.getLogger("StrategyDAG.Registry")

#: Bumped when the *shape* of a descriptor changes, independently of its content.
REGISTRY_SCHEMA_VERSION = "1.0.0"

#: The one parameter contract. ``block_specs.ParamSpec`` already implements the design's
#: ``ParamSpec`` field-for-field (and enforces Requirement 5.4 in ``__post_init__``), so it
#: is re-exported rather than duplicated: a second, competing ``ParamSpec`` would be the
#: same class of defect as a second palette list.
ParamSpec = ParamSpec

__all__ = [
    "ParamSpec",
    "ParamType",
    "ExecutionSemantics",
    "LeakageRisk",
    "Serialization",
    "BlockDescriptor",
    "BlockRegistry",
    "DescriptorSources",
    "RegistryAssemblyError",
    "UnrunnableBlockError",
    "EmptyCategoryError",
    "DuplicateBlockError",
    "COMPATIBILITY_MATRIX",
    "CATEGORY_ORDER",
    "build_registry",
    "default_sources",
    "get_registry",
    "reset_registry",
    "get",
    "registry_version",
    "compatibility_matrix",
    "port_type_vocabulary",
    "compatible",
]


# ---------------------------------------------------------------------------
# Published vocabularies
# ---------------------------------------------------------------------------

#: Palette order and display names, from ``design.md`` -> Registry response shape. Every
#: ``BlockCategory`` member appears exactly once; ``_assert_category_order_is_total``
#: enforces that at import, so a new category cannot be added to the schema without being
#: given a palette section.
CATEGORY_ORDER: Tuple[Tuple[BlockCategory, str], ...] = (
    (BlockCategory.DATA, "Market Data"),
    (BlockCategory.INDICATOR, "Indicators"),
    (BlockCategory.MATH, "Math"),
    (BlockCategory.LOGIC, "Logic"),
    (BlockCategory.FEATURE_ENGINEERING, "Feature Engineering"),
    (BlockCategory.ML_DL, "ML / DL Models"),
    (BlockCategory.ACTION, "Actions"),
)

#: Which target port types each source port type may feed (rule R4). Published to the
#: client so a connect-time check and the backend validator apply the identical rule -
#: one rule, two enforcement points, no divergence (Requirement 6.9).
#:
#: ``TRADE_INTENT`` maps to the empty set: it is the payload an ACTION block hands to the
#: execution layer, and no block accepts it as a graph input. The design's response
#: example omits the key entirely; it is listed here so the matrix is total over
#: ``PortType`` and a client can look up any type without special-casing.
COMPATIBILITY_MATRIX: Dict[PortType, Tuple[PortType, ...]] = {
    PortType.OHLCV_FRAME: (PortType.OHLCV_FRAME, PortType.PRICE_SERIES),
    PortType.PRICE_SERIES: (PortType.PRICE_SERIES, PortType.SCALAR_SERIES),
    PortType.SCALAR_SERIES: (PortType.SCALAR_SERIES,),
    PortType.BOOLEAN_SERIES: (PortType.BOOLEAN_SERIES, PortType.SIGNAL),
    PortType.FEATURE_MATRIX: (PortType.FEATURE_MATRIX,),
    PortType.PREDICTION: (PortType.PREDICTION, PortType.SCALAR_SERIES),
    PortType.SIGNAL: (PortType.SIGNAL, PortType.TRADE_INTENT),
    PortType.TRADE_INTENT: (),
    PortType.SCALAR: (PortType.SCALAR, PortType.SCALAR_SERIES),
}


def _assert_vocabularies_are_total() -> None:
    """Fail import when a published vocabulary does not cover every canonical value.

    Both checks are over authored data, so they either always pass or always fail - the
    same class of problem as a syntax error, and they should surface at import.
    """
    ordered = [category for category, _ in CATEGORY_ORDER]
    missing_categories = [c.value for c in BlockCategory if c not in ordered]
    if missing_categories:
        raise RuntimeError(
            f"CATEGORY_ORDER does not cover BlockCategory members {missing_categories}; "
            "every category needs a palette section"
        )
    if len(ordered) != len(set(ordered)):
        raise RuntimeError("CATEGORY_ORDER lists a category twice")

    missing_types = [t.value for t in PortType if t not in COMPATIBILITY_MATRIX]
    if missing_types:
        raise RuntimeError(
            f"COMPATIBILITY_MATRIX declares no rule for port types {missing_types}; "
            "the matrix must be total over PortType"
        )
    for source, targets in COMPATIBILITY_MATRIX.items():
        for target in targets:
            if not isinstance(target, PortType):  # pragma: no cover - authored data
                raise RuntimeError(
                    f"COMPATIBILITY_MATRIX[{source}] names a non-PortType target {target!r}"
                )


_assert_vocabularies_are_total()


def compatible(source: Any, target: Any) -> bool:
    """True when a ``source``-typed output may feed a ``target``-typed input (rule R4)."""
    try:
        source_type = source if isinstance(source, PortType) else PortType(source)
        target_type = target if isinstance(target, PortType) else PortType(target)
    except ValueError:
        return False
    return target_type in COMPATIBILITY_MATRIX.get(source_type, ())


def port_type_vocabulary() -> List[str]:
    """The ``PortType`` vocabulary, in declaration order, as served to the client."""
    return [port_type.value for port_type in PortType]


def compatibility_matrix() -> Dict[str, List[str]]:
    """:data:`COMPATIBILITY_MATRIX` in wire form."""
    return {
        source.value: [target.value for target in targets]
        for source, targets in COMPATIBILITY_MATRIX.items()
    }


# ---------------------------------------------------------------------------
# Assembly failures - each one names the offender
# ---------------------------------------------------------------------------


class RegistryAssemblyError(RuntimeError):
    """Base class for a failure that must stop startup rather than degrade the palette."""

    code = "REGISTRY_ASSEMBLY_FAILED"


class UnrunnableBlockError(RegistryAssemblyError):
    """A descriptor's ``runtime_ref`` does not resolve to a callable (Requirement 4.8)."""

    code = "BLOCK_RUNTIME_UNRESOLVED"

    def __init__(self, block_id: str, runtime_ref: str, reason: Any = None):
        self.block_id = block_id
        self.runtime_ref = runtime_ref
        self.reason = reason
        detail = f": {reason}" if reason else ""
        super().__init__(
            f"Block '{block_id}' advertises runtime_ref '{runtime_ref}', which does not "
            f"resolve to a callable{detail}. The registry refuses to advertise a block it "
            "cannot run."
        )


class EmptyCategoryError(RegistryAssemblyError):
    """A ``BlockCategory`` holds zero descriptors (Requirement 4.9, the SB-03 guard)."""

    code = "REGISTRY_CATEGORY_EMPTY"

    def __init__(self, categories: Sequence[BlockCategory]):
        self.categories = tuple(categories)
        named = ", ".join(category.value for category in self.categories)
        super().__init__(
            f"Block category {named} holds zero descriptors, so its palette section would "
            "render empty (SB-03). Startup fails rather than serving an empty category."
        )


class DuplicateBlockError(RegistryAssemblyError):
    """Two descriptor sources published the same ``block_id``."""

    code = "REGISTRY_DUPLICATE_BLOCK"

    def __init__(self, block_id: str, first_source: str, second_source: str):
        self.block_id = block_id
        super().__init__(
            f"block_id '{block_id}' is published by both '{first_source}' and "
            f"'{second_source}'; one family would shadow the other's block"
        )


# ---------------------------------------------------------------------------
# Runtime reference resolution - each family resolves through its owner
# ---------------------------------------------------------------------------

#: ``source_module`` -> the resolver owned by that module. The registry never resolves a
#: reference itself: if a module changes how its implementations are addressed, the change
#: lands in one place and the registry keeps working.
_RUNTIME_RESOLVERS: Dict[str, Callable[[str], Callable[..., Any]]] = {
    "indicators_backend": _indicators.resolve_indicator_runtime_ref,
    "feature_engineering": _features.resolve_feature_runtime,
    "ml_models": _models.resolve_model_runtime,
    "block_specs": _blocks.resolve_block_runtime,
}


def _resolve_runtime(source_module: str, runtime_ref: str) -> Callable[..., Any]:
    resolver = _RUNTIME_RESOLVERS.get(source_module)
    if resolver is None:  # pragma: no cover - adapters set this field themselves
        raise UnrunnableBlockError(
            runtime_ref, runtime_ref, f"unknown descriptor source '{source_module}'"
        )
    return resolver(runtime_ref)


# ---------------------------------------------------------------------------
# Adapters onto the one contract
# ---------------------------------------------------------------------------

#: Wrapped around the free-form ``validate`` hooks so every cross-field problem reaches
#: the validator in the structured shape Requirement 5.8 asks for.
CODE_CROSS_FIELD = "PARAM_CROSS_FIELD_INVALID"
SEVERITY_ERROR = "ERROR"


def _adapt_port(raw: Any, block_id: str, kind: str) -> Port:
    """Adapt any owner's port declaration onto the canonical :class:`schema.Port`.

    ``IndicatorSpec``, ``FeatureSpec`` and ``ModelSpec`` each declare their own frozen
    port dataclass with the same field names and plain-string types, so one adapter covers
    every source without any of them importing this module.
    """
    if isinstance(raw, Port):
        return raw
    name = getattr(raw, "name", None) or getattr(raw, "port", None)
    if not isinstance(name, str) or not name:
        raise RegistryAssemblyError(
            f"Block '{block_id}' declares an unnamed {kind} port"
        )
    raw_type = getattr(raw, "type", None)
    try:
        port_type = PortType(getattr(raw_type, "value", raw_type))
    except ValueError as exc:
        raise RegistryAssemblyError(
            f"Block '{block_id}' {kind} port '{name}' declares port type {raw_type!r}, "
            f"which is not a canonical PortType"
        ) from exc
    return Port(
        name=name,
        type=port_type,
        required=bool(getattr(raw, "required", False)),
        variadic=bool(getattr(raw, "variadic", False)),
        description=str(getattr(raw, "description", "") or ""),
    )


def _adapt_param(raw: Any, block_id: str) -> ParamSpec:
    """Adapt any owner's parameter declaration onto the canonical :class:`ParamSpec`."""
    if isinstance(raw, ParamSpec):
        return raw
    raw_type = getattr(raw, "type", None)
    try:
        param_type = ParamType(getattr(raw_type, "value", raw_type))
    except ValueError as exc:
        raise RegistryAssemblyError(
            f"Block '{block_id}' param '{getattr(raw, 'key', '?')}' declares type "
            f"{raw_type!r}, which is not a canonical ParamType"
        ) from exc
    options = getattr(raw, "options", None)
    try:
        return ParamSpec(
            key=getattr(raw, "key"),
            label=getattr(raw, "label", "") or getattr(raw, "key"),
            type=param_type,
            required=bool(getattr(raw, "required", False)),
            default=getattr(raw, "default", None),
            min=getattr(raw, "min", None),
            max=getattr(raw, "max", None),
            step=getattr(raw, "step", None),
            options=tuple(options) if options else None,
            unit=getattr(raw, "unit", None),
            example=getattr(raw, "example", None),
            help=str(getattr(raw, "help", "") or ""),
            depends_on=tuple(getattr(raw, "depends_on", ()) or ()),
            affects_warmup=bool(getattr(raw, "affects_warmup", False)),
            forward_to_runtime=bool(getattr(raw, "forward_to_runtime", True)),
            options_source=getattr(raw, "options_source", None),
        )
    except ValueError as exc:
        # ParamSpec.__post_init__ rejects a forbidden key (SB-06) or a
        # behaviour-changing param published with a default (Requirement 5.4).
        raise RegistryAssemblyError(
            f"Block '{block_id}' publishes an unacceptable parameter: {exc}"
        ) from exc


def _as_issue(raw: Any, block_id: str) -> Dict[str, Any]:
    """Normalise one cross-field problem onto the structured error contract."""
    if isinstance(raw, Mapping):
        issue = dict(raw)
    else:
        issue = {"message": str(raw)}
    issue.setdefault("code", CODE_CROSS_FIELD)
    issue.setdefault("severity", SEVERITY_ERROR)
    issue.setdefault("field", None)
    issue.setdefault("expected", None)
    issue.setdefault("actual", None)
    issue.setdefault("fix_hint", "")
    issue["block_id"] = block_id
    return issue


def _normalise_hook(
    hook: Optional[Callable[[Mapping[str, Any]], Sequence[Any]]], block_id: str
) -> Optional[Callable[[Mapping[str, Any]], List[Dict[str, Any]]]]:
    """Wrap an owner's ``validate(params)`` hook so it always yields structured issues.

    ``indicators_backend`` hooks already return issue dicts; ``block_specs`` and
    ``feature_engineering`` hooks return plain strings. Both shapes are real, and the
    registry is the place that reconciles them - not every caller.
    """
    if hook is None:
        return None

    def _validate(params: Mapping[str, Any]) -> List[Dict[str, Any]]:
        return [_as_issue(item, block_id) for item in (hook(dict(params)) or ())]

    _validate.__name__ = f"validate_{block_id}"
    _validate.__doc__ = (
        f"Cross-field parameter rules for '{block_id}' that ranges cannot express."
    )
    return _validate


# ---------------------------------------------------------------------------
# BlockDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockDescriptor:
    """One registry entry: everything a client, the validator and the runtime need.

    Field-for-field with ``design.md`` -> Descriptor contract, plus three fields the
    implementation needs and the design's prose already assumes:

    ``streaming_runtime_ref``
        The stream counterpart of a block whose primary reference is the historical or
        snapshot call. Asserted callable alongside ``runtime_ref``.
    ``validate``
        The optional per-block ``validate(params) -> List[Issue]`` hook the design's
        Descriptor contract names for cross-field rules ranges cannot express (MACD
        ``fast < slow``). Normalised to structured issues.
    ``metadata``
        Non-contractual extras the compiler, executor and training gate read: an ACTION
        block's ``order_type`` / ``side``, a MATH block's ``operator``, an indicator's
        leaky output ports, a model's minimum-data and cap figures. Carried through from
        the owning descriptor rather than restated.
    """

    block_id: str
    display_name: str
    category: BlockCategory
    description: str
    inputs: Tuple[Port, ...]
    outputs: Tuple[Port, ...]
    params: Tuple[ParamSpec, ...]
    warmup_fn: Callable[[Mapping[str, Any]], int]
    runtime_ref: str
    allowed_predecessor_categories: FrozenSet[BlockCategory] = frozenset()
    allowed_successor_categories: FrozenSet[BlockCategory] = frozenset()
    execution_semantics: ExecutionSemantics = ExecutionSemantics.SERIES_MAP
    serialization: Serialization = Serialization.NONE
    leakage_risk: LeakageRisk = LeakageRisk.NONE
    version: str = "1.0.0"
    capability_flags: FrozenSet[str] = frozenset()
    streaming_runtime_ref: Optional[str] = None
    validate: Optional[Callable[[Mapping[str, Any]], List[Dict[str, Any]]]] = None
    #: Which module owns the implementation, and therefore which resolver runs.
    source_module: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- derived views ----------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.execution_semantics is ExecutionSemantics.TERMINAL

    def input_port(self, name: str) -> Optional[Port]:
        """The named input port, or ``None`` when this block declares none such."""
        return next((p for p in self.inputs if p.name == name), None)

    def output_port(self, name: str) -> Optional[Port]:
        """The named output port, or ``None`` when this block declares none such."""
        return next((p for p in self.outputs if p.name == name), None)

    def param(self, key: str) -> Optional[ParamSpec]:
        return next((p for p in self.params if p.key == key), None)

    def required_params(self) -> Tuple[ParamSpec, ...]:
        return tuple(p for p in self.params if p.required)

    def defaults(self) -> Dict[str, Any]:
        """The parameter map a freshly dropped node starts with.

        Keys whose declared default is ``None`` are present and empty on purpose: the
        inspector renders them blank and blocking until the author sets them
        (Requirement 5.4).
        """
        return {p.key: p.default for p in self.params}

    def resolved_params(self, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Declared defaults overlaid with the supplied non-empty values."""
        resolved = self.defaults()
        if params:
            resolved.update({k: v for k, v in params.items() if v is not None})
        return resolved

    def runtime_kwargs(self, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """The subset of params the runtime callable actually accepts.

        Same rule as :meth:`block_specs.BlockSpec.runtime_kwargs`, stated once here so the
        plan -> engine adaptation does not restate the ``forward_to_runtime`` contract:
        a param the descriptor marks as authoring-only (``source``, ``mode``) is never
        handed to a kernel, and a param the author left blank falls back to the declared
        default rather than to whatever the kernel's own signature happens to say.
        """
        supplied = dict(params or {})
        resolved: Dict[str, Any] = {}
        for spec in self.params:
            if not getattr(spec, "forward_to_runtime", True):
                continue
            if supplied.get(spec.key) is not None:
                resolved[spec.key] = supplied[spec.key]
            elif spec.default is not None:
                resolved[spec.key] = spec.default
        return resolved

    def runtime_refs(self) -> Tuple[str, ...]:
        """Every runtime reference this descriptor advertises."""
        refs = [self.runtime_ref]
        if self.streaming_runtime_ref:
            refs.append(self.streaming_runtime_ref)
        return tuple(refs)

    def resolve_runtime(self) -> Callable[..., Any]:
        """The callable behind ``runtime_ref``, through the owning module's resolver."""
        return _resolve_runtime(self.source_module, self.runtime_ref)

    def assert_runnable(self) -> None:
        """Raise :class:`UnrunnableBlockError` naming this block when it cannot run."""
        for ref in self.runtime_refs():
            try:
                target = _resolve_runtime(self.source_module, ref)
            except UnrunnableBlockError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure names the offender
                raise UnrunnableBlockError(self.block_id, ref, exc) from exc
            if not callable(target):  # pragma: no cover - resolvers already check
                raise UnrunnableBlockError(self.block_id, ref, "not callable")

    def warmup(self, params: Optional[Mapping[str, Any]] = None) -> int:
        """Bars of history this block consumes before its output is trustworthy."""
        return int(self.warmup_fn(self.resolved_params(params)))

    def cross_field_issues(
        self, params: Optional[Mapping[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Run the per-block ``validate(params)`` hook over defaults-resolved params.

        Empty list when the block declares no hook, or when the supplied parameters
        satisfy its cross-field rules.
        """
        if self.validate is None:
            return []
        return list(self.validate(self.resolved_params(params)))

    def to_dict(self) -> Dict[str, Any]:
        """Wire form. Callables are deliberately absent: they are not serialisable and a
        client has no use for them. ``runtime_ref`` names them instead."""
        return {
            "block_id": self.block_id,
            "display_name": self.display_name,
            "category": self.category.value,
            "description": self.description,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params": [p.to_dict() for p in self.params],
            "allowed_predecessor_categories": sorted(
                c.value for c in self.allowed_predecessor_categories
            ),
            "allowed_successor_categories": sorted(
                c.value for c in self.allowed_successor_categories
            ),
            "execution_semantics": self.execution_semantics.value,
            "runtime_ref": self.runtime_ref,
            "streaming_runtime_ref": self.streaming_runtime_ref,
            "serialization": self.serialization.value,
            "leakage_risk": self.leakage_risk.value,
            "version": self.version,
            "capability_flags": sorted(self.capability_flags),
            "has_cross_field_validation": self.validate is not None,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# Family adapters
# ---------------------------------------------------------------------------


def _coerce_categories(raw: Iterable[Any]) -> FrozenSet[BlockCategory]:
    resolved = set()
    for item in raw or ():
        if isinstance(item, BlockCategory):
            resolved.add(item)
        else:
            resolved.add(BlockCategory(str(getattr(item, "value", item))))
    return frozenset(resolved)


def descriptor_from_indicator_spec(spec: Any) -> BlockDescriptor:
    """Adapt one ``indicators_backend.IndicatorSpec``.

    Category adjacency is not declared by an ``IndicatorSpec`` and is deliberately not
    invented here: it is derived from the assembled registry's port types in
    :func:`_derive_adjacency`, so it cannot drift from what the type system permits.
    Per-output leakage (Ichimoku's ``chikou``) is carried in ``metadata`` for the leakage
    validator; the block-level risk is the strongest risk any output carries.
    """
    return BlockDescriptor(
        block_id=spec.block_id,
        display_name=spec.display_name,
        category=BlockCategory.INDICATOR,
        description=spec.description,
        inputs=tuple(_adapt_port(p, spec.block_id, "input") for p in spec.inputs),
        outputs=tuple(_adapt_port(p, spec.block_id, "output") for p in spec.outputs),
        params=tuple(_adapt_param(p, spec.block_id) for p in spec.params),
        warmup_fn=spec.warmup_fn,
        runtime_ref=spec.runtime_ref,
        execution_semantics=ExecutionSemantics(spec.execution_semantics),
        serialization=Serialization.NONE,
        leakage_risk=LeakageRisk(spec.leakage_risk),
        version=spec.version,
        validate=_normalise_hook(spec.validate, spec.block_id),
        source_module="indicators_backend",
        metadata={
            "leaky_outputs": list(spec.leaky_outputs),
            "multi_output": len(spec.outputs) > 1,
        },
    )


def descriptor_from_feature_spec(spec: Any) -> BlockDescriptor:
    """Adapt one ``feature_engineering.FeatureSpec`` - the SB-03 category.

    The cross-field hook lives in ``feature_engineering.FEATURE_PARAM_VALIDATORS`` keyed
    by block id rather than on the descriptor, so it is looked up there rather than
    restated.
    """
    return BlockDescriptor(
        block_id=spec.block_id,
        display_name=spec.display_name,
        category=BlockCategory(spec.category),
        description=spec.description,
        inputs=tuple(_adapt_port(p, spec.block_id, "input") for p in spec.inputs),
        outputs=tuple(_adapt_port(p, spec.block_id, "output") for p in spec.outputs),
        params=tuple(_adapt_param(p, spec.block_id) for p in spec.params),
        warmup_fn=spec.warmup_fn,
        runtime_ref=spec.runtime_ref,
        allowed_predecessor_categories=_coerce_categories(
            spec.allowed_predecessor_categories
        ),
        allowed_successor_categories=_coerce_categories(spec.allowed_successor_categories),
        execution_semantics=ExecutionSemantics(spec.execution_semantics.value),
        serialization=Serialization(spec.serialization),
        leakage_risk=LeakageRisk(spec.leakage_risk.value),
        version=spec.version,
        capability_flags=frozenset(spec.capability_flags),
        validate=_normalise_hook(
            _features.FEATURE_PARAM_VALIDATORS.get(spec.block_id), spec.block_id
        ),
        source_module="feature_engineering",
        metadata={
            "global_statistic_block": spec.block_id in _features.GLOBAL_STATISTIC_BLOCKS,
        },
    )


def _model_warmup_fn(sequence_length: Optional[int]) -> Callable[[Mapping[str, Any]], int]:
    """A model's own warmup is the window it consumes: its sequence length, or none.

    Derived from the spec rather than declared, because ``ModelSpec`` owns
    ``sequence_length`` and a second figure here could disagree with it.
    """
    bars = int(sequence_length or 0)

    def warmup_fn(_params: Mapping[str, Any]) -> int:
        return bars

    return warmup_fn


def descriptor_from_model_spec(spec: Any) -> BlockDescriptor:
    """Adapt one available ``ml_models.ModelSpec``.

    Availability is *not* re-decided here - the caller omits unavailable specs, which is
    what keeps an unimportable library out of the palette instead of advertising a block
    that fails at train time (Requirements 4.5, 4.6). The minimum-data and cap figures the
    training gate needs (Requirement 14.1) are carried through ``metadata`` verbatim from
    the spec.
    """
    return BlockDescriptor(
        block_id=spec.block_id,
        display_name=spec.display_name,
        category=BlockCategory.ML_DL,
        description=spec.description,
        inputs=tuple(_adapt_port(p, spec.block_id, "input") for p in spec.inputs),
        outputs=tuple(_adapt_port(p, spec.block_id, "output") for p in spec.outputs),
        params=tuple(_adapt_param(p, spec.block_id) for p in spec.hyperparameters),
        warmup_fn=_model_warmup_fn(spec.sequence_length),
        runtime_ref=spec.runtime_ref,
        execution_semantics=ExecutionSemantics.STATEFUL,
        serialization=Serialization(spec.serialization.value),
        leakage_risk=LeakageRisk.NONE,
        version=spec.version,
        capability_flags=frozenset(
            {"needs_model"}
            | ({"trainable"} if spec.can_train else set())
            | ({"predicts"} if spec.can_predict else set())
        ),
        source_module="ml_models",
        metadata={"model": spec.to_dict()},
    )


def descriptor_from_block_spec(spec: BlockSpec) -> BlockDescriptor:
    """Adapt one DATA, MATH, LOGIC or ACTION ``block_specs.BlockSpec``.

    These four families already declare the descriptor contract, so the adaptation is a
    field copy: no port, param, warmup or adjacency decision is remade here.
    """
    return BlockDescriptor(
        block_id=spec.block_id,
        display_name=spec.display_name,
        category=spec.category,
        description=spec.description,
        inputs=tuple(spec.inputs),
        outputs=tuple(spec.outputs),
        params=tuple(spec.params),
        warmup_fn=spec.warmup_fn,
        runtime_ref=spec.runtime_ref,
        allowed_predecessor_categories=_coerce_categories(
            spec.allowed_predecessor_categories
        ),
        allowed_successor_categories=_coerce_categories(spec.allowed_successor_categories),
        execution_semantics=spec.execution_semantics,
        serialization=spec.serialization,
        leakage_risk=spec.leakage_risk,
        version=spec.version,
        capability_flags=frozenset(spec.capability_flags),
        streaming_runtime_ref=spec.streaming_runtime_ref,
        validate=_normalise_hook(spec.validate, spec.block_id),
        source_module="block_specs",
        metadata=dict(spec.metadata),
    )


# ---------------------------------------------------------------------------
# Category adjacency, derived from the port types
# ---------------------------------------------------------------------------


def _derive_adjacency(descriptors: Sequence[BlockDescriptor]) -> List[BlockDescriptor]:
    """Fill in the adjacency sets no owner declared, from the port types themselves.

    ``IndicatorSpec`` and ``ModelSpec`` declare ports but not category adjacency, and
    hand-writing that adjacency here is precisely the kind of parallel list that drifts.
    So it is *computed*: category ``D`` is an allowed successor of block ``B`` when some
    block of ``D`` has an input port that ``B``'s output ports may legally feed under
    :data:`COMPATIBILITY_MATRIX`, and ``D``'s own declared predecessor set (where it has
    one) admits ``B``'s category. Predecessors are derived symmetrically.

    Declared sets always win: ``block_specs`` and ``feature_engineering`` curate theirs
    deliberately, and an ACTION block's empty successor set is a structural fact
    (TERMINAL), not a gap.

    Postconditions
        Every non-terminal descriptor whose owner declared no successor set carries the
        derived one; a TERMINAL descriptor's successor set stays empty; a descriptor with
        no input ports keeps an empty predecessor set.
    """
    declared_pred = {d.block_id: d.allowed_predecessor_categories for d in descriptors}
    declared_succ = {d.block_id: d.allowed_successor_categories for d in descriptors}

    def _feeds(source: BlockDescriptor, target: BlockDescriptor) -> bool:
        return any(
            compatible(out_port.type, in_port.type)
            for out_port in source.outputs
            for in_port in target.inputs
        )

    resolved: List[BlockDescriptor] = []
    for descriptor in descriptors:
        successors = descriptor.allowed_successor_categories
        predecessors = descriptor.allowed_predecessor_categories

        if not successors and not descriptor.is_terminal and descriptor.outputs:
            successors = frozenset(
                other.category
                for other in descriptors
                if other.inputs
                and _feeds(descriptor, other)
                and (
                    not declared_pred[other.block_id]
                    or descriptor.category in declared_pred[other.block_id]
                )
            )

        if not predecessors and descriptor.inputs:
            predecessors = frozenset(
                other.category
                for other in descriptors
                if other.outputs
                and not other.is_terminal
                and _feeds(other, descriptor)
                and (
                    not declared_succ[other.block_id]
                    or descriptor.category in declared_succ[other.block_id]
                )
            )

        if successors is descriptor.allowed_successor_categories and (
            predecessors is descriptor.allowed_predecessor_categories
        ):
            resolved.append(descriptor)
        else:
            resolved.append(
                replace(
                    descriptor,
                    allowed_successor_categories=successors,
                    allowed_predecessor_categories=predecessors,
                )
            )
    return resolved


# ---------------------------------------------------------------------------
# Descriptor sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DescriptorSources:
    """The five descriptor sources :func:`build_registry` assembles.

    Kept as a value so a test can assemble from a deliberately broken or emptied source
    and observe the guard fire. Production always uses :func:`default_sources`.
    """

    indicators: Tuple[Any, ...] = ()
    features: Tuple[Any, ...] = ()
    #: ``ModelSpec`` objects *before* availability filtering; unavailable ones are omitted
    #: by :func:`build_registry`, never advertised.
    models: Tuple[Any, ...] = ()
    #: DATA + MATH + LOGIC descriptors.
    static_blocks: Tuple[BlockSpec, ...] = ()
    #: ACTION descriptors, generated from ``exchange_executor.OrderType``.
    actions: Tuple[BlockSpec, ...] = ()


def default_sources(order_types: Optional[Sequence[Any]] = None) -> DescriptorSources:
    """The real descriptor sources, read from the modules that own them.

    ``block_specs.action_specs`` reads ``exchange_executor.OrderType`` lazily, so the CCXT
    import happens here - at assembly - and not when this module is imported.
    """
    return DescriptorSources(
        indicators=tuple(_indicators.INDICATOR_SPECS),
        features=tuple(_features.FEATURE_SPECS),
        models=tuple(_models.MODEL_SPECS.values()),
        static_blocks=_blocks.DATA_SPECS + _blocks.MATH_SPECS + _blocks.LOGIC_SPECS,
        actions=_blocks.action_specs(order_types),
    )


# ---------------------------------------------------------------------------
# BlockRegistry
# ---------------------------------------------------------------------------


def _compute_registry_version(descriptors: Sequence[BlockDescriptor]) -> str:
    """Deterministic hash of the assembled descriptor set plus the published vocabulary.

    Preconditions
        Descriptors are fully assembled (adjacency resolved).

    Postconditions
        Stable across processes and platforms and independent of mapping order: blocks are
        hashed in ``block_id`` order and every mapping is key-sorted and numerically
        normalised by ``schema.canonicalize`` before serialisation. Changes whenever any
        descriptor changes, whenever a block appears or disappears (so an unavailable model
        library moves the version), and whenever the published vocabulary changes.
    """
    payload = {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "blocks": [
            descriptor.to_dict()
            for descriptor in sorted(descriptors, key=lambda d: d.block_id)
        ],
        "categories": [
            {"id": category.value, "display_name": name, "order": index}
            for index, (category, name) in enumerate(CATEGORY_ORDER, start=1)
        ],
        "port_types": port_type_vocabulary(),
        "compatibility_matrix": compatibility_matrix(),
    }
    encoded = json.dumps(
        canonicalize(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return "r_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:8]


class BlockRegistry:
    """The assembled catalogue: ``block_id`` -> :class:`BlockDescriptor`.

    Read-only after construction. ``get`` returns ``None`` for an unknown id rather than
    raising, because "is this block published?" is a question every caller asks and an
    exception is the wrong answer to it - it is also the contract
    ``schema._discover_default_resolver`` probes for.
    """

    def __init__(self, descriptors: Sequence[BlockDescriptor]):
        by_id: Dict[str, BlockDescriptor] = {}
        for descriptor in descriptors:
            existing = by_id.get(descriptor.block_id)
            if existing is not None:
                raise DuplicateBlockError(
                    descriptor.block_id,
                    existing.source_module,
                    descriptor.source_module,
                )
            by_id[descriptor.block_id] = descriptor

        ordered: List[BlockDescriptor] = []
        by_category: Dict[BlockCategory, Tuple[BlockDescriptor, ...]] = {}
        for category, _display in CATEGORY_ORDER:
            members = tuple(d for d in descriptors if d.category is category)
            by_category[category] = members
            ordered.extend(members)

        self._by_id = by_id
        self._ordered: Tuple[BlockDescriptor, ...] = tuple(ordered)
        self._by_category = by_category
        self._version = _compute_registry_version(self._ordered)

    # -- lookup -----------------------------------------------------------
    def get(self, block_id: Any) -> Optional[BlockDescriptor]:
        """The descriptor for ``block_id``, or ``None`` when the registry has none.

        Tried verbatim and then lower-cased: registry ids are lower-case snake_case while
        schema version 1 stored operator tokens upper-cased (``"AND"``).
        """
        if not isinstance(block_id, str):
            return None
        descriptor = self._by_id.get(block_id)
        if descriptor is not None:
            return descriptor
        return self._by_id.get(block_id.strip().lower())

    def __getitem__(self, block_id: str) -> BlockDescriptor:
        descriptor = self.get(block_id)
        if descriptor is None:
            raise KeyError(block_id)
        return descriptor

    def __contains__(self, block_id: Any) -> bool:
        return self.get(block_id) is not None

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[BlockDescriptor]:
        return iter(self._ordered)

    def block_ids(self) -> Tuple[str, ...]:
        """Every published block id, in palette order."""
        return tuple(d.block_id for d in self._ordered)

    def blocks(self) -> Tuple[BlockDescriptor, ...]:
        """Every descriptor, in palette order (category order, then declaration order)."""
        return self._ordered

    def in_category(self, category: Any) -> Tuple[BlockDescriptor, ...]:
        """Every descriptor in ``category``, in declaration order."""
        resolved = (
            category
            if isinstance(category, BlockCategory)
            else BlockCategory(str(getattr(category, "value", category)))
        )
        return self._by_category.get(resolved, ())

    def counts_by_category(self) -> Dict[str, int]:
        """How many descriptors each category holds. The SB-03 canary, as data."""
        return {
            category.value: len(self._by_category.get(category, ()))
            for category, _ in CATEGORY_ORDER
        }

    # -- port helpers -----------------------------------------------------
    def input_port(self, block_id: str, port_name: str) -> Optional[Port]:
        """The named input port of ``block_id``, or ``None`` (rule R3)."""
        descriptor = self.get(block_id)
        return None if descriptor is None else descriptor.input_port(port_name)

    def output_port(self, block_id: str, port_name: str) -> Optional[Port]:
        """The named output port of ``block_id``, or ``None`` (rule R3)."""
        descriptor = self.get(block_id)
        return None if descriptor is None else descriptor.output_port(port_name)

    def ports_compatible(self, source_type: Any, target_type: Any) -> bool:
        """Rule R4, against the same matrix the client is served."""
        return compatible(source_type, target_type)

    # -- published contract ----------------------------------------------
    @property
    def registry_version(self) -> str:
        """Deterministic hash of this assembled descriptor set."""
        return self._version

    @property
    def port_types(self) -> List[str]:
        """The ``PortType`` vocabulary, as served."""
        return port_type_vocabulary()

    @property
    def compatibility_matrix(self) -> Dict[str, List[str]]:
        """The port-type compatibility rules, as served (Requirement 6.9)."""
        return compatibility_matrix()

    def categories(self) -> List[Dict[str, Any]]:
        """The ordered palette sections."""
        return [
            {"id": category.value, "display_name": name, "order": index}
            for index, (category, name) in enumerate(CATEGORY_ORDER, start=1)
        ]

    def to_dict(self) -> Dict[str, Any]:
        """The registry response shape from ``design.md`` -> Registry response shape."""
        return {
            "registry_version": self._version,
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "port_types": self.port_types,
            "categories": self.categories(),
            "blocks": [descriptor.to_dict() for descriptor in self._ordered],
            "compatibility_matrix": self.compatibility_matrix,
        }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_registry(sources: Optional[DescriptorSources] = None) -> BlockRegistry:
    """Assemble the registry from the engines' real capabilities.

    Preconditions
        The engine modules import successfully.

    Postconditions
        Every published descriptor's ``runtime_ref`` (and streaming counterpart) resolves
        to a callable; no ``BlockCategory`` is empty; a model block whose library is not
        importable is absent rather than advertised; ``registry_version`` is the hash of
        the assembled descriptor set.

    Loop invariants
        After each registration the registry holds only descriptors whose runtime is
        present, so an advertised block is always a runnable block.

    Raises
        :class:`UnrunnableBlockError` naming the block and the reference it could not
        resolve; :class:`EmptyCategoryError` naming the empty category (the structural
        guard against SB-03 recurring); :class:`DuplicateBlockError` naming the colliding
        id. All three stop startup: serving a palette that lies is worse than not serving.
    """
    resolved = sources if sources is not None else default_sources()

    descriptors: List[BlockDescriptor] = []

    # DATA, MATH, LOGIC - declared in block_specs, executed by the category executors.
    for spec in resolved.static_blocks:
        descriptors.append(descriptor_from_block_spec(spec))

    # INDICATOR - derived from indicators_backend descriptors, never a parallel list.
    for spec in resolved.indicators:
        descriptors.append(descriptor_from_indicator_spec(spec))

    # FEATURE_ENGINEERING - closes SB-03.
    for spec in resolved.features:
        descriptors.append(descriptor_from_feature_spec(spec))

    # ML_DL - gated on the library actually importing (SB-04's other half).
    omitted_models: List[str] = []
    for spec in resolved.models:
        if not getattr(spec, "backend_available", False):
            omitted_models.append(spec.block_id)
            continue
        descriptors.append(descriptor_from_model_spec(spec))

    # ACTION - order types read from exchange_executor.OrderType, never invented.
    for spec in resolved.actions:
        descriptors.append(descriptor_from_block_spec(spec))

    # Never advertise what we cannot run.
    for descriptor in descriptors:
        descriptor.assert_runnable()

    descriptors = _derive_adjacency(descriptors)

    empty = [
        category
        for category, _ in CATEGORY_ORDER
        if not any(d.category is category for d in descriptors)
    ]
    if empty:
        raise EmptyCategoryError(empty)

    registry = BlockRegistry(descriptors)

    if omitted_models:
        logger.info(
            "Model blocks omitted from the registry (library not importable): %s",
            ", ".join(sorted(omitted_models)),
        )
    logger.info(
        "Block registry assembled: %s blocks, version %s, %s",
        len(registry),
        registry.registry_version,
        registry.counts_by_category(),
    )
    return registry


# ---------------------------------------------------------------------------
# Module-level surface
# ---------------------------------------------------------------------------

_REGISTRY: Optional[BlockRegistry] = None


def get_registry() -> BlockRegistry:
    """The memoised registry, assembling it on first use.

    Assembly is deliberately *not* performed at import: it reads
    ``exchange_executor.OrderType`` and would drag CCXT into every process that only needs
    the schema. A caller that wants assembly to happen at startup calls this from its
    startup hook, which is where a :class:`RegistryAssemblyError` should surface.
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_registry()
    return _REGISTRY


def reset_registry() -> None:
    """Forget the memoised registry. For tests that change a descriptor source."""
    global _REGISTRY
    _REGISTRY = None


def get(block_id: Any) -> Optional[BlockDescriptor]:
    """The descriptor for ``block_id``, or ``None`` when the registry publishes none.

    The module-level seam ``schema._discover_default_resolver`` probes for, so the v1 ->
    v2 migration resolves ports against the real registry without importing it eagerly.
    """
    return get_registry().get(block_id)


def registry_version() -> str:
    """The assembled registry's deterministic version hash.

    A function rather than a module constant on purpose: a constant would force assembly
    at import time, which is exactly the CCXT-dragging import the package's purity
    contract forbids.
    """
    return get_registry().registry_version


def input_port(block_id: str, port_name: str) -> Optional[Port]:
    """Module-level port lookup helper (rule R3)."""
    return get_registry().input_port(block_id, port_name)


def output_port(block_id: str, port_name: str) -> Optional[Port]:
    """Module-level port lookup helper (rule R3)."""
    return get_registry().output_port(block_id, port_name)
