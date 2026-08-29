# -*- coding: utf-8 -*-
"""
backend/ml_training_policy.py - the minimum-data gate and the backend-enforced caps.

Two decisions live here, and nowhere else:

1. **Can this dataset train this model at all?** :func:`check_ml_data_requirements`
   answers it from *measured* dataset statistics and returns ``required`` and
   ``available`` for both dimensions, so the builder renders the exact numbers the
   requirements demand rather than "insufficient data" (Requirements 14.2-14.6, 14.9).
2. **How much of the shared training capacity may this user consume?**
   :func:`resolve_caps` builds an :class:`MLTrainingCaps` and :func:`enforce_caps`
   applies it, at job creation and again in the worker before the first epoch
   (Requirements 16.1-16.5).

Design: ``design.md`` -> ML/DL model registry -> "Minimum-data gate" and
"Resource caps (backend-enforced)". Spec task 6.2.

Measured, never estimated
-------------------------
Requirement 14.2 says the gate measures usable feature columns and usable rows *from
the fetched dataset* after removing warmup and the label horizon. A formula over the
requested bar count is not that measurement: it cannot see a gap the exchange did not
serve, a warmup that composed longer than the graph declared, or a feature column that
came out all-NaN and was dropped.

So :class:`DatasetStats` is built from a real
:class:`~backend_app.backend.ml_dataset.SupervisedDataset`, whose ``n_rows`` already has
the warmup prefix and the trailing label horizon removed *by construction* - the row
range that produced ``X`` is the row range that produced ``y``. Nothing here subtracts
anything from a bar count. :meth:`DatasetStats.from_mapping` **refuses** a mapping that
carries only a raw total and a warmup figure, because silently doing the subtraction is
exactly the estimate the requirement forbids.

Memory is the one figure that *is* estimated, and it says so: the design writes
``estimated <- estimate_memory_mb(request)``. A pre-flight cap on a training run's
footprint cannot be measured before the run exists; the authoritative bound is
``ml_safety.MemoryMonitor`` at runtime.

The cap intersection direction
------------------------------
``max_epochs = min(tier_cap, model_spec.max_safe_epochs)``.

Entitlements **widen within a ceiling they cannot lift**. A paid tier raises the limit up
to what the model spec declares safe and no further; the model spec never raises what a
tier allows either. Inverting this - ``max(...)``, or letting the tier win - would let a
subscription upgrade buy a training run the model registry says is unsafe, which is a
resource-exhaustion path dressed up as a feature. Every intersection in
:func:`resolve_caps` runs in that direction, and :func:`enforce_caps` never sees the
un-intersected tier figure.

Consolidation, not authorship
-----------------------------
Nothing here re-declares a figure another surface owns:

* model minimums, ``sequence_length``, ``max_safe_epochs``, ``can_train`` and the split
  geometry are read from ``ml_models.ModelSpec`` - directly, or through the registry
  descriptor's ``metadata["model"]``, which ``registry.descriptor_from_model_spec``
  carries through verbatim. :class:`ModelSpecView` is the adapter over those two shapes;
  it holds no numbers of its own.
* the platform feature-column floor is ``ml_models.PLATFORM_MIN_FEATURE_COLUMNS``.
* the platform feature-column ceiling is ``strategy_dag.validator.LIMITS``.
* wall-clock and memory ceilings are ``ml_safety.TrainingIsolator``'s live
  ``TrainingConfig``; the artifact-size ceiling is ``MemoryMonitor``'s ``MemoryConfig``.
  A model larger than the cache it must be loaded into can never be served.
* the plan is resolved with ``entitlement_engine.PlanMapper``, the ML-training
  entitlement with ``SubscriptionEngine.has_feature`` and the monthly training allowance
  with ``SubscriptionEngine.get_quota_limit``. There is no tier table here for any of
  those.
* per-user concurrency is ``tenant.TenantQuota.for_plan(...).max_backtest_parallel`` -
  the platform's existing per-tier compute-concurrency figure.
* the embargo floor is ``ml_dataset.required_embargo_bars``.

:data:`ML_TIER_CAPS` is the one table this module owns, and it covers only the four
dimensions no existing surface carries: epochs, rows, feature columns and the per-tier
share of duration/memory/artifact size. It is keyed on the existing
``tenant.TenantPlan``, not on a plan vocabulary invented here.

Caps are additive
-----------------
``core/subscription_dependencies.require_ml_training`` (feature gate) and
``check_ml_quota`` (monthly ``ml_trainings`` allowance) stay exactly where they are and
keep running as FastAPI dependencies on the training endpoints. Nothing in this module
replaces or relaxes either one (Requirement 16.7). What it adds is per-request and
per-resource bounds those two do not express. The resolved caps *record* the entitlement
flag and the monthly allowance on :class:`MLTrainingCaps` so a caps payload shows that
both layers were consulted, and so a FREE/BASIC plan's caps agree with the entitlement
layer instead of silently permitting what the dependency would refuse.

Import weight
-------------
``strategy_dag.validator`` imports this module at *its* import time to install stage 11
(see :func:`ml_readiness_stage`), and ``strategy_dag`` must stay importable by the
worker and the backtester without dragging in FastAPI, a database handle or an exchange
client - ``tests/test_strategy_dag_architecture.py`` measures that in a fresh
interpreter. So module scope here imports **stdlib, ``core.tenant`` (stdlib-only) and
``strategy_dag.schema`` (pure) and nothing else**. ``ml_models``, ``ml_dataset``,
``ml_safety``, ``entitlement_engine``, ``subscription_engine`` and ``validator`` are all
reached through in-function imports, the same lazy seam ``block_specs`` uses for
``exchange_executor``.

No credential store is reachable from here, lazily or otherwise.

The training tables are not applied
-----------------------------------
Concurrency caps need live counts from ``training_jobs``, and migration
``004d_training_and_models.sql`` has not been applied in this environment. A caller that
cannot supply counts gets the concurrency checks **skipped with a warning naming that
file** rather than a 500 - see :class:`JobCounts` and
:data:`JOB_COUNTS_UNAVAILABLE_WARNING`. Skipping is safe in the direction that matters:
it can defer a rejection to the worker's re-check (Requirement 16.4), and it can never
turn a rejection into an admission of an over-cap *request*, because the epoch, row,
column and memory caps are evaluated from the request alone.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from backend_app.core.tenant import TenantPlan, TenantQuota

from backend_app.backend.strategy_dag.schema import (
    SEVERITY_ERROR,
    BlockCategory,
    make_issue,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed
    from backend_app.backend.strategy_dag.validator import ValidationContext

logger = logging.getLogger("MLTrainingPolicy")

#: Bumped when a cap dimension or a gate code changes, so a stored admission record can
#: be told apart from one produced by a later policy.
POLICY_VERSION = "1.0.0"

__all__ = [
    "POLICY_VERSION",
    "MLTrainingPolicyError",
    "CapExceeded",
    "DatasetStatsError",
    # model families
    "FAMILY_TREE",
    "FAMILY_SEQUENCE",
    "FAMILY_AUTOENCODER",
    # gate
    "CODE_INSUFFICIENT_FEATURE_COLUMNS",
    "CODE_INSUFFICIENT_ROWS",
    "CODE_INSUFFICIENT_SEQUENCE_ROWS",
    "CODE_MODEL_NOT_TRAINABLE",
    "CODE_MODEL_SPEC_UNAVAILABLE",
    "ModelSpecView",
    "ValidationConfig",
    "DatasetStats",
    "GateVerdict",
    "check_ml_data_requirements",
    "format_thousands",
    # caps
    "CAP_MAX_EPOCHS",
    "CAP_MAX_ROWS",
    "CAP_MAX_FEATURES",
    "CAP_MAX_CONCURRENT_USER",
    "CAP_MAX_MEMORY",
    "TierTrainingCaps",
    "ML_TIER_CAPS",
    "MLTrainingCaps",
    "TrainingRequest",
    "JobCounts",
    "AdmissionDecision",
    "Admission",
    "resolve_caps",
    "enforce_caps",
    "estimate_memory_mb",
    "DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL",
    "JOB_COUNTS_UNAVAILABLE_WARNING",
    "TRAINING_TABLES_MIGRATION",
    # validation stage 11
    "STAGE_NUMBER",
    "ml_readiness_stage",
    "gate_issues_for_context",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MLTrainingPolicyError(Exception):
    """Base class for every refusal this module raises."""


class DatasetStatsError(MLTrainingPolicyError):
    """Dataset statistics were absent, unreadable, or an estimate posing as a measurement."""


class CapExceeded(MLTrainingPolicyError):
    """A training request exceeds one effective cap.

    Carries ``requested`` and ``allowed`` for the *one* cap that was exceeded, which is
    what Requirement 16.3 asks the response to state. :attr:`http_status` is 422, the
    status the design's failure-mode table names for this class.
    """

    http_status = 422

    def __init__(
        self,
        cap: str,
        requested: Any,
        allowed: Any,
        *,
        unit: str = "",
        block_id: str = "",
        plan: str = "",
        fix_hint: str = "",
    ) -> None:
        self.cap = str(cap)
        self.requested = requested
        self.allowed = allowed
        self.unit = str(unit or "")
        self.block_id = str(block_id or "")
        self.plan = str(plan or "")
        self.fix_hint = str(fix_hint or "")
        suffix = f" {self.unit}" if self.unit else ""
        super().__init__(
            f"{self.cap}: requested {requested}{suffix}, allowed {allowed}{suffix}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """The wire form: one cap, its requested value and its permitted value."""
        return {
            "error": "CapExceeded",
            "cap": self.cap,
            "requested": self.requested,
            "allowed": self.allowed,
            "unit": self.unit,
            "block_id": self.block_id,
            "plan": self.plan,
            "message": str(self),
            "fix_hint": self.fix_hint,
        }


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: ``ml_models.ModelFamily`` values, as strings. They arrive here as strings whichever
#: way the spec was read: ``ModelSpec.to_dict()`` publishes ``model_family.value`` and
#: ``ModelFamily`` is a ``str`` enum, so a comparison against these constants holds for
#: the enum member too.
FAMILY_TREE = "TREE"
FAMILY_SEQUENCE = "SEQUENCE"
FAMILY_AUTOENCODER = "AUTOENCODER"

# -- gate codes (design.md -> Minimum-data gate) ----------------------------
CODE_INSUFFICIENT_FEATURE_COLUMNS = "INSUFFICIENT_FEATURE_COLUMNS"
CODE_INSUFFICIENT_ROWS = "INSUFFICIENT_ROWS"
CODE_INSUFFICIENT_SEQUENCE_ROWS = "INSUFFICIENT_SEQUENCE_ROWS"
CODE_MODEL_NOT_TRAINABLE = "MODEL_NOT_TRAINABLE"
#: The node is an ML block but no model descriptor could be read for it. Reported as an
#: error, never skipped: an unknown model's data requirements cannot be asserted either
#: way, and "we could not check" must not read as "it is fine".
CODE_MODEL_SPEC_UNAVAILABLE = "MODEL_SPEC_UNAVAILABLE"

# -- cap names (design.md -> enforce_caps) ---------------------------------
CAP_MAX_EPOCHS = "MAX_EPOCHS"
CAP_MAX_ROWS = "MAX_ROWS"
CAP_MAX_FEATURES = "MAX_FEATURES"
CAP_MAX_CONCURRENT_USER = "MAX_CONCURRENT_USER"
CAP_MAX_MEMORY = "MAX_MEMORY"

#: The validation stage this module installs into ``strategy_dag.validator``.
STAGE_NUMBER = 11

#: Named in every degradation message, so an operator reads which file to apply.
TRAINING_TABLES_MIGRATION = "004d_training_and_models.sql"

JOB_COUNTS_UNAVAILABLE_WARNING = (
    "Concurrent-job caps were not evaluated: live job counts are unavailable "
    f"(apply migration {TRAINING_TABLES_MIGRATION} to create training_jobs). The "
    "per-request caps were still enforced, and the worker re-checks caps before the "
    "first epoch."
)


def format_thousands(value: Any) -> str:
    """``5000 -> '5,000'``. The design's message shape uses grouped thousands."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Read an integer, or ``default``. ``True``/``False`` are not integers here."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _enum_value(value: Any) -> Any:
    """The ``.value`` of an enum member, or the value itself."""
    return getattr(value, "value", value)


# ---------------------------------------------------------------------------
# Lazy readers for the surfaces that own the numbers
# ---------------------------------------------------------------------------
#
# Every one of these is an in-function import at its call site's cost. See the module
# docstring: strategy_dag imports this module at import time and must stay light.


def _ml_models() -> Any:
    """``backend.ml_models``, or ``None`` when it cannot be imported."""
    try:
        from backend_app.backend import ml_models

        return ml_models
    except Exception:  # noqa: BLE001 - an unimportable registry is a reported state
        logger.exception("ml_models could not be imported; model specs are unavailable")
        return None


def platform_min_feature_columns() -> Optional[int]:
    """``ml_models.PLATFORM_MIN_FEATURE_COLUMNS``, the >= 5 platform floor.

    ``None`` when ``ml_models`` is unreadable. That is not a hole in the gate: the same
    module asserts at import that every spec's ``min_feature_columns`` is at or above
    this floor, so a spec figure alone already satisfies it.
    """
    module = _ml_models()
    return None if module is None else _as_int(
        getattr(module, "PLATFORM_MIN_FEATURE_COLUMNS", None)
    )


def _platform_max_feature_columns() -> Optional[int]:
    """``strategy_dag.validator.LIMITS.max_feature_columns``, the graph capacity bound.

    Imported lazily *because* the validator imports this module at its import time to
    install stage 11. At module scope this would be a cycle; at call time the validator
    is fully initialised, since a caps resolution can only happen after it.
    """
    try:
        from backend_app.backend.strategy_dag.validator import LIMITS

        return _as_int(LIMITS.max_feature_columns)
    except Exception:  # noqa: BLE001
        logger.debug("Graph limits unavailable; feature-column ceiling left to the tier")
        return None


def _runtime_ceilings() -> Dict[str, Optional[int]]:
    """Wall-clock, memory and artifact-size ceilings from ``core.ml_safety``.

    Read from the *live* configuration of ``TrainingIsolator`` and ``MemoryMonitor``
    rather than from their dataclass defaults, so an operator who reconfigures isolation
    reconfigures the caps with it.
    """
    out: Dict[str, Optional[int]] = {
        "max_duration_seconds": None,
        "max_memory_mb": None,
        "max_model_size_mb": None,
    }
    try:
        from backend_app.core.ml_safety import MemoryMonitor, TrainingIsolator

        training = getattr(TrainingIsolator, "_config", None)
        if training is not None:
            out["max_duration_seconds"] = _as_int(
                getattr(training, "max_training_time_seconds", None)
            )
            out["max_memory_mb"] = _as_int(getattr(training, "max_memory_mb", None))
        memory = getattr(MemoryMonitor, "_config", None)
        if memory is not None:
            # A model that cannot fit the cache it must be loaded into can never be
            # served, so the cache bound is the artifact bound.
            out["max_model_size_mb"] = _as_int(
                getattr(memory, "max_model_cache_size_mb", None)
            )
    except Exception:  # noqa: BLE001
        logger.debug("ml_safety unavailable; runtime ceilings left to the tier")
    return out


def _required_embargo_bars(feature_lookback: int, label_horizon: int) -> int:
    """``ml_dataset.required_embargo_bars`` - lookback + horizon, from its owner.

    Raises rather than guessing. An embargo floor this module cannot read is an embargo
    floor it must not substitute a smaller number for: that would let a training row's
    label window reach into the validation split.
    """
    try:
        from backend_app.backend.ml_dataset import required_embargo_bars
    except Exception as exc:  # noqa: BLE001
        raise MLTrainingPolicyError(
            "Cannot compute the embargo floor: backend.ml_dataset is unavailable. "
            "The gate refuses rather than assume a smaller embargo."
        ) from exc
    return int(required_embargo_bars(int(feature_lookback), int(label_horizon)))


# ---------------------------------------------------------------------------
# ModelSpecView - one read-only shape over the two the spec arrives in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpecView:
    """The figures the gate and the caps need, normalised.

    A model spec reaches this module in one of four shapes and they must not be handled
    four ways:

    * an ``ml_models.ModelSpec`` (the training service holds one),
    * the ``metadata["model"]`` mapping on a registry ``BlockDescriptor`` (all the pure
      validator has - which is exactly how stage 10b reaches its leakage facts, and why
      stage 11 needs no ``ml_models`` import inside ``strategy_dag``),
    * a ``BlockDescriptor`` itself,
    * a ``block_id`` string, resolved against ``ml_models.MODEL_SPECS``.

    Every field is copied from the spec. This class declares no minimum, no ceiling and
    no default figure of its own.
    """

    block_id: str
    display_name: str
    model_family: str
    min_feature_columns: int
    min_training_rows: int
    sequence_length: Optional[int]
    recommended_epochs: int
    max_safe_epochs: int
    epoch_unit: str
    can_train: bool
    backend_available: bool
    val_fraction: float
    test_fraction: float
    embargo_bars: int
    metric: str

    @property
    def is_sequence(self) -> bool:
        return str(_enum_value(self.model_family)).upper() == FAMILY_SEQUENCE

    @property
    def reserved_fraction(self) -> float:
        return float(self.val_fraction) + float(self.test_fraction)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "display_name": self.display_name,
            "model_family": self.model_family,
            "min_feature_columns": self.min_feature_columns,
            "min_training_rows": self.min_training_rows,
            "sequence_length": self.sequence_length,
            "recommended_epochs": self.recommended_epochs,
            "max_safe_epochs": self.max_safe_epochs,
            "epoch_unit": self.epoch_unit,
            "can_train": self.can_train,
            "backend_available": self.backend_available,
            "val_fraction": self.val_fraction,
            "test_fraction": self.test_fraction,
            "embargo_bars": self.embargo_bars,
            "metric": self.metric,
        }

    # -- construction ----------------------------------------------------
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModelSpecView":
        """Adapt a ``ModelSpec.to_dict()`` / ``metadata["model"]`` mapping."""
        requirements = data.get("validation_requirements")
        if not isinstance(requirements, Mapping):
            requirements = {}
        family = str(_enum_value(data.get("model_family")) or "").upper()
        return cls(
            block_id=str(data.get("block_id") or ""),
            display_name=str(data.get("display_name") or data.get("block_id") or ""),
            model_family=family,
            min_feature_columns=_as_int(data.get("min_feature_columns"), 0) or 0,
            min_training_rows=_as_int(data.get("min_training_rows"), 0) or 0,
            sequence_length=_as_int(data.get("sequence_length")),
            recommended_epochs=_as_int(data.get("recommended_epochs"), 0) or 0,
            max_safe_epochs=_as_int(data.get("max_safe_epochs"), 0) or 0,
            epoch_unit=str(_enum_value(data.get("epoch_unit")) or "epochs"),
            can_train=bool(data.get("can_train", True)),
            backend_available=bool(data.get("backend_available", True)),
            val_fraction=_as_float(requirements.get("val_fraction"), 0.0) or 0.0,
            test_fraction=_as_float(requirements.get("test_fraction"), 0.0) or 0.0,
            embargo_bars=_as_int(requirements.get("embargo_bars"), 0) or 0,
            metric=str(requirements.get("metric") or ""),
        )

    @classmethod
    def from_spec(cls, spec: Any) -> "ModelSpecView":
        """Adapt an ``ml_models.ModelSpec`` object through its own ``to_dict()``."""
        to_dict = getattr(spec, "to_dict", None)
        if callable(to_dict):
            return cls.from_mapping(to_dict())
        requirements = getattr(spec, "validation_requirements", None)
        return cls.from_mapping(
            {
                "block_id": getattr(spec, "block_id", ""),
                "display_name": getattr(spec, "display_name", ""),
                "model_family": getattr(spec, "model_family", ""),
                "min_feature_columns": getattr(spec, "min_feature_columns", 0),
                "min_training_rows": getattr(spec, "min_training_rows", 0),
                "sequence_length": getattr(spec, "sequence_length", None),
                "recommended_epochs": getattr(spec, "recommended_epochs", 0),
                "max_safe_epochs": getattr(spec, "max_safe_epochs", 0),
                "epoch_unit": getattr(spec, "epoch_unit", "epochs"),
                "can_train": getattr(spec, "can_train", True),
                "backend_available": getattr(spec, "backend_available", True),
                "validation_requirements": {
                    "val_fraction": getattr(requirements, "val_fraction", 0.0),
                    "test_fraction": getattr(requirements, "test_fraction", 0.0),
                    "embargo_bars": getattr(requirements, "embargo_bars", 0),
                    "metric": getattr(requirements, "metric", ""),
                },
            }
        )

    @classmethod
    def for_block_id(cls, block_id: str) -> Optional["ModelSpecView"]:
        """Resolve ``block_id`` against ``ml_models.MODEL_SPECS``."""
        module = _ml_models()
        if module is None:
            return None
        getter = getattr(module, "get_model_spec", None)
        spec = getter(str(block_id)) if callable(getter) else None
        return None if spec is None else cls.from_spec(spec)

    @classmethod
    def coerce(cls, value: Any) -> Optional["ModelSpecView"]:
        """Best-effort adaptation of any of the four accepted shapes. ``None`` on failure."""
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls.for_block_id(value)
        # A registry BlockDescriptor: the spec rides on metadata["model"].
        metadata = getattr(value, "metadata", None)
        if isinstance(metadata, Mapping) and isinstance(metadata.get("model"), Mapping):
            return cls.from_mapping(metadata["model"])
        if isinstance(value, Mapping):
            if isinstance(value.get("model"), Mapping):
                return cls.from_mapping(value["model"])
            return cls.from_mapping(value)
        if hasattr(value, "min_training_rows") or hasattr(value, "max_safe_epochs"):
            return cls.from_spec(value)
        return None


# ---------------------------------------------------------------------------
# ValidationConfig - the split geometry the gate reserves for
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationConfig:
    """Fractions, embargo and label horizon the required-row count is computed against.

    ``embargo_bars`` is the *effective* embargo: the model spec's own floor raised to at
    least the longest feature lookback plus the label horizon when a lookback is known.
    :meth:`for_model` raises it and never lowers it, which is the rule
    ``ml_models.ValidationRequirements`` documents and defers to Phase 6.
    """

    val_fraction: float
    test_fraction: float
    embargo_bars: int
    label_horizon: int
    metric: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "val_fraction", float(self.val_fraction))
        object.__setattr__(self, "test_fraction", float(self.test_fraction))
        object.__setattr__(self, "embargo_bars", int(self.embargo_bars))
        object.__setattr__(self, "label_horizon", int(self.label_horizon))
        if self.embargo_bars < 0:
            raise MLTrainingPolicyError(f"embargo_bars must be >= 0: {self.embargo_bars}")
        if self.label_horizon < 1:
            raise MLTrainingPolicyError(
                f"label_horizon must be >= 1: {self.label_horizon}"
            )
        # The design's own precondition: reserved < 1.0. With reserved >= 1 the required
        # row count divides by zero or goes negative, and the gate would either crash or
        # admit everything. Refusing here is the only honest option.
        if not 0.0 <= self.reserved_fraction < 1.0:
            raise MLTrainingPolicyError(
                "val_fraction + test_fraction must leave a non-empty train split: "
                f"{self.val_fraction} + {self.test_fraction} = {self.reserved_fraction}"
            )

    @property
    def reserved_fraction(self) -> float:
        return self.val_fraction + self.test_fraction

    @property
    def train_fraction(self) -> float:
        return 1.0 - self.reserved_fraction

    def to_dict(self) -> Dict[str, Any]:
        return {
            "val_fraction": self.val_fraction,
            "test_fraction": self.test_fraction,
            "embargo_bars": self.embargo_bars,
            "label_horizon": self.label_horizon,
            "metric": self.metric,
        }

    @classmethod
    def for_model(
        cls,
        spec: ModelSpecView,
        *,
        label_horizon: int,
        feature_lookback: Optional[int] = None,
        val_fraction: Optional[float] = None,
        test_fraction: Optional[float] = None,
        embargo_bars: Optional[int] = None,
    ) -> "ValidationConfig":
        """The config for ``spec``, with the caller's overrides applied.

        Postconditions
            ``embargo_bars`` is at least the model's declared floor, and at least
            ``feature_lookback + label_horizon`` when a lookback was supplied. An
            explicit ``embargo_bars`` can raise it further but cannot lower it below
            either floor.
        """
        horizon = int(label_horizon)
        floor = int(spec.embargo_bars)
        if feature_lookback is not None:
            floor = max(floor, _required_embargo_bars(int(feature_lookback), horizon))
        effective = floor if embargo_bars is None else max(floor, int(embargo_bars))
        return cls(
            val_fraction=(
                spec.val_fraction if val_fraction is None else float(val_fraction)
            ),
            test_fraction=(
                spec.test_fraction if test_fraction is None else float(test_fraction)
            ),
            embargo_bars=effective,
            label_horizon=horizon,
            metric=spec.metric,
        )


# ---------------------------------------------------------------------------
# DatasetStats - measured, never estimated
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetStats:
    """What the *built* dataset actually holds.

    ``usable_rows`` and ``usable_feature_columns`` are counts of rows and columns that
    exist in an assembled ``(X, y)`` pair - warmup prefix already gone, trailing label
    horizon already gone. They are not a bar count minus a warmup figure.

    ``total_rows``, ``dropped_warmup_rows`` and ``dropped_trailing_rows`` are carried for
    provenance and for the job-status payload (Requirement 15.3). Nothing in the gate
    computes a usable figure from them.
    """

    usable_rows: int
    usable_feature_columns: int
    label_horizon: int
    dropped_warmup_rows: int = 0
    dropped_trailing_rows: int = 0
    total_rows: Optional[int] = None
    feature_names: Tuple[str, ...] = ()
    source: str = "measured"

    def __post_init__(self) -> None:
        for name in (
            "usable_rows",
            "usable_feature_columns",
            "label_horizon",
            "dropped_warmup_rows",
            "dropped_trailing_rows",
        ):
            object.__setattr__(self, name, int(getattr(self, name)))
        object.__setattr__(self, "feature_names", tuple(self.feature_names or ()))
        if self.usable_rows < 0 or self.usable_feature_columns < 0:
            raise DatasetStatsError(
                f"measured counts cannot be negative: rows={self.usable_rows}, "
                f"columns={self.usable_feature_columns}"
            )
        if self.label_horizon < 1:
            raise DatasetStatsError(
                f"label_horizon must be >= 1: {self.label_horizon}. A dataset with no "
                f"label horizon has no labels."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "usable_rows": self.usable_rows,
            "usable_feature_columns": self.usable_feature_columns,
            "label_horizon": self.label_horizon,
            "dropped_warmup_rows": self.dropped_warmup_rows,
            "dropped_trailing_rows": self.dropped_trailing_rows,
            "total_rows": self.total_rows,
            "feature_names": list(self.feature_names),
            "source": self.source,
        }

    # -- construction ----------------------------------------------------
    @classmethod
    def from_dataset(cls, dataset: Any) -> "DatasetStats":
        """Measure a ``ml_dataset.SupervisedDataset``.

        ``n_rows`` is the row count of ``X``, which ``build_supervised_dataset`` produced
        from one row range starting at the feature matrix's warmup offset and stopping
        ``horizon`` rows early. So the warmup trim and the label-horizon trim are already
        in the number, and subtracting them again here would double-count.
        """
        rows = _as_int(getattr(dataset, "n_rows", None))
        columns = _as_int(getattr(dataset, "n_columns", None))
        horizon = _as_int(getattr(dataset, "horizon", None))
        if rows is None or columns is None or horizon is None:
            raise DatasetStatsError(
                "A SupervisedDataset must expose n_rows, n_columns and horizon; got "
                f"{type(dataset).__name__}"
            )
        dropped_warmup = _as_int(getattr(dataset, "dropped_warmup_rows", 0), 0) or 0
        dropped_trailing = _as_int(getattr(dataset, "dropped_trailing_rows", 0), 0) or 0
        columns_seq = getattr(dataset, "columns", ()) or ()
        return cls(
            usable_rows=rows,
            usable_feature_columns=columns,
            label_horizon=horizon,
            dropped_warmup_rows=dropped_warmup,
            dropped_trailing_rows=dropped_trailing,
            total_rows=rows + dropped_warmup + dropped_trailing,
            feature_names=tuple(str(name) for name in columns_seq),
            source="supervised_dataset",
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DatasetStats":
        """Read a measured stats mapping, e.g. ``SupervisedDataset.stats()``.

        Accepts ``usable_rows``/``rows`` and ``usable_feature_columns``/``columns``, so
        ``dataset.stats()`` can be handed over unchanged.

        Raises
            :class:`DatasetStatsError` when a usable row or column count is absent -
            including the case where only ``total_rows`` and a warmup figure are present.
            Doing the subtraction here would produce exactly the estimate Requirement
            14.2 forbids, and it would be wrong whenever the fetched range had a gap or a
            feature column was dropped.
        """
        if not isinstance(data, Mapping):
            raise DatasetStatsError(
                f"dataset statistics must be a mapping, got {type(data).__name__}"
            )
        rows = _as_int(data.get("usable_rows"))
        if rows is None:
            rows = _as_int(data.get("rows"))
        columns = _as_int(data.get("usable_feature_columns"))
        if columns is None:
            columns = _as_int(data.get("columns"))
        if columns is None:
            columns = _as_int(data.get("feature_columns"))
        horizon = _as_int(data.get("label_horizon"))
        if horizon is None:
            horizon = _as_int(data.get("horizon"))
        if rows is None or columns is None:
            raise DatasetStatsError(
                "Dataset statistics must carry measured usable row and column counts "
                "('usable_rows'/'rows' and 'usable_feature_columns'/'columns'). "
                "Deriving them from a total bar count minus warmup is an estimate, and "
                "Requirement 14.2 requires a measurement of the built dataset."
            )
        if horizon is None:
            raise DatasetStatsError(
                "Dataset statistics must carry the label horizon "
                "('label_horizon'/'horizon'); the gate reserves for it."
            )
        names = data.get("feature_names") or ()
        if isinstance(names, (str, bytes)):
            names = ()
        return cls(
            usable_rows=rows,
            usable_feature_columns=columns,
            label_horizon=horizon,
            dropped_warmup_rows=_as_int(data.get("dropped_warmup_rows"), 0) or 0,
            dropped_trailing_rows=_as_int(data.get("dropped_trailing_rows"), 0) or 0,
            total_rows=_as_int(data.get("total_rows")),
            feature_names=tuple(str(name) for name in names),
            source=str(data.get("source") or "mapping"),
        )

    @classmethod
    def coerce(cls, value: Any) -> "DatasetStats":
        """A :class:`DatasetStats`, a stats mapping, or a ``SupervisedDataset``."""
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.from_mapping(value)
        if value is None:
            raise DatasetStatsError("no dataset statistics were supplied")
        return cls.from_dataset(value)


# ---------------------------------------------------------------------------
# The minimum-data gate (design.md -> Minimum-data gate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateVerdict:
    """The gate's answer, carrying required and available for both dimensions.

    ``required`` and ``available`` always hold ``columns`` and ``rows`` - even when the
    gate passed, and even when only one dimension fell short - because Requirement 14.9
    renders both quantities and a UI that has to ask a second time for the other half
    renders "insufficient data" instead.
    """

    ok: bool
    issues: Tuple[Dict[str, Any], ...]
    required: Mapping[str, Any]
    available: Mapping[str, Any]
    block_id: str = ""
    node_id: Optional[str] = None
    config: Optional[ValidationConfig] = None
    stats: Optional[DatasetStats] = None

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(str(issue.get("code")) for issue in self.issues)

    def message(self) -> str:
        """The mandated message shape, straight out of required / available.

        > Training cannot start. Required: 5 feature columns and 5,000 usable rows.
        > Available: 3 feature columns and 1,240 rows.
        """
        if self.ok:
            return ""
        return (
            "Training cannot start. Required: "
            f"{format_thousands(self.required.get('columns'))} feature columns and "
            f"{format_thousands(self.required.get('rows'))} usable rows. Available: "
            f"{format_thousands(self.available.get('columns'))} feature columns and "
            f"{format_thousands(self.available.get('rows'))} rows."
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "block_id": self.block_id,
            "node_id": self.node_id,
            "issues": [dict(issue) for issue in self.issues],
            "required": dict(self.required),
            "available": dict(self.available),
            "message": self.message(),
            "config": None if self.config is None else self.config.to_dict(),
            "stats": None if self.stats is None else self.stats.to_dict(),
        }


def required_row_count(spec: ModelSpecView, config: ValidationConfig) -> int:
    """Rows needed *before* splitting, so every split and window is non-empty.

    ``ceil((min_core + 2 * embargo) / (1 - reserved))`` where ``min_core`` is the model's
    minimum training rows, plus its ``sequence_length`` for the SEQUENCE family because
    every sequence sample consumes a whole window.

    The two embargoes are the train->val and val->test gaps: rows that exist, are paid
    for out of the fetched range, and are used by neither split.
    """
    min_core = int(spec.min_training_rows)
    if spec.is_sequence:
        min_core += int(spec.sequence_length or 0)
    numerator = min_core + 2 * int(config.embargo_bars)
    return int(math.ceil(numerator / config.train_fraction))


def required_train_rows(spec: ModelSpecView) -> int:
    """Rows a SEQUENCE model's *train split* must hold: window + minimum rows."""
    return int(spec.min_training_rows) + int(spec.sequence_length or 0)


def check_ml_data_requirements(
    plan: Any,
    dataset_stats: Any,
    model_spec: Any,
    validation_cfg: Any = None,
    *,
    node_id: Optional[str] = None,
    feature_lookback: Optional[int] = None,
) -> GateVerdict:
    """Decide whether ``dataset_stats`` can train ``model_spec``. Collect-all.

    Parameters
    ----------
    plan
        The compiled plan, or ``None``. Used only to attribute an issue to a node when
        ``node_id`` is not given (``plan.ml_nodes[0]``, as the design's pseudocode does)
        and to read ``warmup_bars`` as the feature lookback when ``feature_lookback`` is
        not given. **No usable figure is derived from it** - warmup is already out of the
        measured row count.
    dataset_stats
        A :class:`DatasetStats`, a measured stats mapping, or a ``SupervisedDataset``.
    model_spec
        Any shape :meth:`ModelSpecView.coerce` accepts.
    validation_cfg
        A :class:`ValidationConfig`, a mapping of overrides, or ``None`` to take the
        model's own ``validation_requirements``.

    Returns
    -------
    :class:`GateVerdict` with ``required`` and ``available`` for both dimensions.

    Preconditions
        ``dataset_stats`` was measured from real fetched data; ``reserved < 1.0``, which
        :class:`ValidationConfig` enforces at construction.

    Postconditions
        Every check that could run has run - a graph short on both columns and rows is
        told about both in one round, not one per retry. ``ok`` is true only when no
        issue was collected, and a caller that sees ``ok = False`` must create **no**
        ``training_jobs`` row (Requirements 14.3, 14.4). When ``ok`` is true every split
        is non-empty and every sequence sample has a complete window.
    """
    stats = DatasetStats.coerce(dataset_stats)
    spec = ModelSpecView.coerce(model_spec)

    resolved_node_id = node_id
    if resolved_node_id is None:
        ml_nodes = getattr(plan, "ml_nodes", None) or ()
        resolved_node_id = str(ml_nodes[0]) if ml_nodes else None

    if spec is None:
        # No descriptor, no assertion. Reported as an error so the caller blocks.
        block_id = model_spec if isinstance(model_spec, str) else ""
        issue = make_issue(
            CODE_MODEL_SPEC_UNAVAILABLE,
            SEVERITY_ERROR,
            "Training cannot start: no model descriptor is published for "
            f"{block_id or 'this model node'}, so its data requirements cannot be "
            "checked.",
            node_id=resolved_node_id,
            field_name="block_id",
            expected="a model block the registry publishes",
            actual=block_id or None,
            fix_hint=(
                "Replace the model block from the current palette. A model whose "
                "library is not importable in this image is not offered."
            ),
        )
        return GateVerdict(
            ok=False,
            issues=(issue,),
            required={"columns": None, "rows": None},
            available={
                "columns": stats.usable_feature_columns,
                "rows": stats.usable_rows,
            },
            block_id=str(block_id),
            node_id=resolved_node_id,
            stats=stats,
        )

    lookback = feature_lookback
    if lookback is None:
        lookback = _as_int(getattr(plan, "warmup_bars", None))

    if isinstance(validation_cfg, ValidationConfig):
        config = validation_cfg
    else:
        overrides: Mapping[str, Any] = (
            validation_cfg if isinstance(validation_cfg, Mapping) else {}
        )
        config = ValidationConfig.for_model(
            spec,
            label_horizon=_as_int(overrides.get("label_horizon"), stats.label_horizon)
            or stats.label_horizon,
            feature_lookback=lookback,
            val_fraction=_as_float(overrides.get("val_fraction")),
            test_fraction=_as_float(overrides.get("test_fraction")),
            embargo_bars=_as_int(overrides.get("embargo_bars")),
        )

    issues: List[Dict[str, Any]] = []
    display = spec.display_name or spec.block_id

    # -- 1 feature columns: platform floor AND model floor ------------------
    available_columns = stats.usable_feature_columns
    platform_floor = platform_min_feature_columns()
    required_columns = int(spec.min_feature_columns)
    if platform_floor is not None:
        required_columns = max(int(platform_floor), required_columns)

    if available_columns < required_columns:
        issues.append(
            make_issue(
                CODE_INSUFFICIENT_FEATURE_COLUMNS,
                SEVERITY_ERROR,
                f"Training cannot start. Required: {required_columns} feature columns. "
                f"Available: {available_columns}.",
                node_id=resolved_node_id,
                field_name="usable_feature_columns",
                expected=required_columns,
                actual=available_columns,
                fix_hint=(
                    f"Add {required_columns - available_columns} more feature column(s) "
                    f"to the pipeline feeding '{display}'."
                ),
            )
        )

    # -- 2 usable rows: measured, after warmup and label horizon ------------
    usable_rows = stats.usable_rows
    required_rows = required_row_count(spec, config)

    if usable_rows < required_rows:
        issues.append(
            make_issue(
                CODE_INSUFFICIENT_ROWS,
                SEVERITY_ERROR,
                f"Training cannot start. Required: {format_thousands(required_rows)} "
                f"usable rows. Available: {format_thousands(usable_rows)}.",
                node_id=resolved_node_id,
                field_name="usable_rows",
                expected=required_rows,
                actual=usable_rows,
                fix_hint=(
                    "Widen the history range, use a shorter timeframe, or reduce the "
                    "reserved validation and test fractions."
                    + (
                        f" A shorter sequence length than {spec.sequence_length} also "
                        f"lowers the requirement."
                        if spec.is_sequence
                        else ""
                    )
                ),
            )
        )

    # -- 3 a sequence model needs a full window after splitting -------------
    train_rows = int(math.floor(usable_rows * config.train_fraction))
    required_train = required_train_rows(spec) if spec.is_sequence else None
    if spec.is_sequence and train_rows < int(required_train or 0):
        issues.append(
            make_issue(
                CODE_INSUFFICIENT_SEQUENCE_ROWS,
                SEVERITY_ERROR,
                f"Sequence length {spec.sequence_length} leaves only "
                f"{format_thousands(train_rows)} training rows; "
                f"{format_thousands(required_train)} are needed "
                f"({spec.sequence_length} window + {format_thousands(spec.min_training_rows)} "
                f"minimum rows).",
                node_id=resolved_node_id,
                field_name="sequence_length",
                expected=required_train,
                actual=train_rows,
                fix_hint=(
                    f"Reduce the sequence length below {spec.sequence_length}, or widen "
                    f"the history range."
                ),
            )
        )

    # -- 4 the model must actually be runnable ------------------------------
    if not spec.can_train:
        issues.append(
            make_issue(
                CODE_MODEL_NOT_TRAINABLE,
                SEVERITY_ERROR,
                f"'{display}' cannot be trained in this build.",
                node_id=resolved_node_id,
                field_name="block_id",
                expected="a trainable model block",
                actual=spec.block_id,
                fix_hint="Choose a model block that supports training.",
            )
        )
    elif not spec.backend_available:
        issues.append(
            make_issue(
                CODE_MODEL_NOT_TRAINABLE,
                SEVERITY_ERROR,
                f"'{display}' cannot be trained: its library is not importable in this "
                f"image.",
                node_id=resolved_node_id,
                field_name="block_id",
                expected="a model whose library resolves in this image",
                actual=spec.block_id,
                fix_hint="Choose a different model block, or install the library.",
            )
        )

    required: Dict[str, Any] = {
        "columns": required_columns,
        "rows": required_rows,
    }
    available: Dict[str, Any] = {
        "columns": available_columns,
        "rows": usable_rows,
    }
    if spec.is_sequence:
        # Requirement 14.6: report the sequence length together with the available
        # training row count, not only the shortfall.
        required["train_rows"] = required_train
        required["sequence_length"] = spec.sequence_length
        available["train_rows"] = train_rows
        available["sequence_length"] = spec.sequence_length

    return GateVerdict(
        ok=not issues,
        issues=tuple(issues),
        required=required,
        available=available,
        block_id=spec.block_id,
        node_id=resolved_node_id,
        config=config,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Caps (design.md -> Resource caps, backend-enforced)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierTrainingCaps:
    """One plan's ML training allowance, before intersection.

    These are the only figures this module declares, and they cover only the dimensions
    no existing surface carries. Everything else on :class:`MLTrainingCaps` is read from
    ``ml_safety``, ``tenant.TenantQuota``, ``validator.LIMITS`` or the model spec.

    FREE and BASIC are zero on purpose. Neither plan grants ``ml_training`` in
    ``entitlement_engine.FeatureEntitlements`` nor a non-zero ``ml_trainings`` allowance
    in ``SubscriptionEngine``, so ``require_ml_training`` and ``check_ml_quota`` already
    refuse them. Zeroing the caps makes this layer *agree* with that instead of quietly
    permitting what the dependency above it rejects - the fail-closed direction.
    """

    max_epochs: int
    max_rows: int
    max_feature_columns: int
    max_duration_seconds: int
    max_memory_mb: int
    max_model_size_mb: int
    #: Floor for the per-user concurrency figure read from ``TenantQuota``.
    min_concurrent_jobs_per_user: int = 0


#: Per-plan allowances, keyed on the existing ``tenant.TenantPlan``.
ML_TIER_CAPS: Mapping[TenantPlan, TierTrainingCaps] = {
    TenantPlan.FREE: TierTrainingCaps(
        max_epochs=0,
        max_rows=0,
        max_feature_columns=0,
        max_duration_seconds=0,
        max_memory_mb=0,
        max_model_size_mb=0,
        min_concurrent_jobs_per_user=0,
    ),
    TenantPlan.BASIC: TierTrainingCaps(
        max_epochs=0,
        max_rows=0,
        max_feature_columns=0,
        max_duration_seconds=0,
        max_memory_mb=0,
        max_model_size_mb=0,
        min_concurrent_jobs_per_user=0,
    ),
    TenantPlan.PROFESSIONAL: TierTrainingCaps(
        max_epochs=300,
        max_rows=200_000,
        max_feature_columns=200,
        max_duration_seconds=1_800,
        max_memory_mb=2_048,
        max_model_size_mb=256,
        min_concurrent_jobs_per_user=1,
    ),
    TenantPlan.ENTERPRISE: TierTrainingCaps(
        max_epochs=3_000,
        max_rows=2_000_000,
        max_feature_columns=200,
        max_duration_seconds=3_600,
        max_memory_mb=4_096,
        max_model_size_mb=1_024,
        min_concurrent_jobs_per_user=2,
    ),
}

def _env_int(name: str, default: int) -> int:
    """An integer from the environment, or ``default``. A bad value is never fatal."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _as_int(raw.strip())
    if value is None or value < 0:
        logger.warning("%s=%r is not a non-negative integer; using %s", name, raw, default)
        return default
    return value


#: Platform-wide, not per tier: the shared training pool's width. Read from the
#: environment because it is a property of the deployment's worker fleet, not of a plan.
DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL = _env_int("ML_TRAINING_MAX_CONCURRENT_GLOBAL", 8)

#: Bytes per feature value in the training matrix. float64, which is what numpy hands a
#: model unless a caller downcasts.
_BYTES_PER_VALUE = 8
#: Peak-to-matrix ratio: the framework holds the matrix, a split view, the labels and its
#: own working buffers at once. A rounded, deliberately generous multiplier - this is an
#: estimate and the authoritative bound is ``ml_safety.MemoryMonitor`` at runtime.
_MEMORY_PEAK_FACTOR = 3
#: Interpreter, framework and model overhead a run pays before it sees a row.
_MEMORY_BASE_MB = 128


@dataclass(frozen=True)
class MLTrainingCaps:
    """The effective bounds for one user training one model block.

    Every figure is already intersected: nothing downstream needs to remember to apply a
    ceiling, and :func:`enforce_caps` never sees the un-intersected tier value.
    """

    max_epochs: int
    max_rows: int
    max_feature_columns: int
    max_concurrent_jobs_per_user: int
    max_concurrent_jobs_global: int
    max_duration_seconds: int
    max_memory_mb: int
    max_model_size_mb: int

    # -- provenance, so a caps payload shows what was consulted -------------
    plan: str = TenantPlan.FREE.value
    block_id: str = ""
    epoch_unit: str = "epochs"
    #: The tier's own epoch allowance, before the model ceiling.
    tier_max_epochs: int = 0
    #: The model spec's ``max_safe_epochs``, the ceiling a tier cannot lift.
    model_max_safe_epochs: int = 0
    #: ``FeatureEntitlements.is_feature_available(ML_TRAINING, plan)`` - recorded, not
    #: re-decided. ``require_ml_training`` still gates the endpoint (Requirement 16.7).
    ml_training_entitled: bool = False
    #: ``SubscriptionEngine.get_quota_limit(plan, 'ml_trainings')``; ``-1`` means
    #: unlimited, ``None`` means it could not be read. Enforced by ``check_ml_quota``.
    monthly_training_quota: Optional[int] = None
    warnings: Tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION

    @property
    def has_model_ceiling(self) -> bool:
        """True when these caps were resolved against a specific model spec.

        Caps without a model ceiling must not admit a job: Requirement 16.2 defines the
        effective epoch cap as the lesser of the tier's and the model's figures, and
        without the second one there is no such lesser value. :func:`enforce_caps`
        refuses them.
        """
        return bool(self.block_id) and self.model_max_safe_epochs > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_epochs": self.max_epochs,
            "max_rows": self.max_rows,
            "max_feature_columns": self.max_feature_columns,
            "max_concurrent_jobs_per_user": self.max_concurrent_jobs_per_user,
            "max_concurrent_jobs_global": self.max_concurrent_jobs_global,
            "max_duration_seconds": self.max_duration_seconds,
            "max_memory_mb": self.max_memory_mb,
            "max_model_size_mb": self.max_model_size_mb,
            "plan": self.plan,
            "block_id": self.block_id,
            "epoch_unit": self.epoch_unit,
            "tier_max_epochs": self.tier_max_epochs,
            "model_max_safe_epochs": self.model_max_safe_epochs,
            "ml_training_entitled": self.ml_training_entitled,
            "monthly_training_quota": self.monthly_training_quota,
            "warnings": list(self.warnings),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class TrainingRequest:
    """What a client asked for. Never trusted; measured against the caps.

    ``rows`` and ``feature_columns`` are the *measured* dataset figures the gate already
    accepted, not a client's claim about them - the job creation path passes
    ``DatasetStats`` through. ``epochs`` is the one field a client genuinely chooses.
    """

    block_id: str
    epochs: int
    rows: int
    feature_columns: int
    sequence_length: Optional[int] = None
    batch_size: Optional[int] = None
    model_family: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_id", str(self.block_id or ""))
        for name in ("epochs", "rows", "feature_columns"):
            object.__setattr__(self, name, int(getattr(self, name)))
        if self.sequence_length is not None:
            object.__setattr__(self, "sequence_length", int(self.sequence_length))
        if self.batch_size is not None:
            object.__setattr__(self, "batch_size", int(self.batch_size))
        object.__setattr__(
            self, "model_family", str(_enum_value(self.model_family) or "").upper()
        )
        if self.epochs < 0 or self.rows < 0 or self.feature_columns < 0:
            raise MLTrainingPolicyError(
                "TrainingRequest figures cannot be negative: "
                f"epochs={self.epochs}, rows={self.rows}, "
                f"feature_columns={self.feature_columns}"
            )

    @property
    def is_sequence(self) -> bool:
        return self.model_family == FAMILY_SEQUENCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "epochs": self.epochs,
            "rows": self.rows,
            "feature_columns": self.feature_columns,
            "sequence_length": self.sequence_length,
            "batch_size": self.batch_size,
            "model_family": self.model_family,
        }

    @classmethod
    def build(
        cls,
        model_spec: Any,
        stats: Any,
        *,
        epochs: Optional[int] = None,
        batch_size: Optional[int] = None,
    ) -> "TrainingRequest":
        """A request for ``model_spec`` over ``stats``, defaulting epochs to the spec's
        ``recommended_epochs`` when the caller named none."""
        spec = ModelSpecView.coerce(model_spec)
        if spec is None:
            raise MLTrainingPolicyError(
                "Cannot build a training request without a resolvable model spec"
            )
        measured = DatasetStats.coerce(stats)
        return cls(
            block_id=spec.block_id,
            epochs=int(spec.recommended_epochs if epochs is None else epochs),
            rows=measured.usable_rows,
            feature_columns=measured.usable_feature_columns,
            sequence_length=spec.sequence_length,
            batch_size=batch_size,
            model_family=spec.model_family,
        )


@dataclass(frozen=True)
class JobCounts:
    """Live counts from ``training_jobs``, or an honest statement that there are none.

    ``available = False`` is the degraded path: migration
    ``004d_training_and_models.sql`` has not been applied, or the query failed. The
    concurrency caps are then skipped with a warning rather than raising, and the
    worker's pre-first-epoch re-check (Requirement 16.4) is what closes the gap.
    """

    user_active: int = 0
    global_running: int = 0
    global_queued: int = 0
    available: bool = True
    unavailable_reason: str = ""

    def __post_init__(self) -> None:
        for name in ("user_active", "global_running", "global_queued"):
            object.__setattr__(self, name, int(getattr(self, name)))

    @classmethod
    def unavailable(cls, reason: str = "") -> "JobCounts":
        return cls(
            available=False,
            unavailable_reason=reason or JOB_COUNTS_UNAVAILABLE_WARNING,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_active": self.user_active,
            "global_running": self.global_running,
            "global_queued": self.global_queued,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


class AdmissionDecision(str, Enum):
    """What :func:`enforce_caps` concluded for a request that broke no cap."""

    ADMIT = "ADMIT"
    #: Global capacity is saturated. The job is *queued*, not rejected: Requirement 16.5
    #: says hold it and report a queue position estimate.
    DEFER = "DEFER"


@dataclass(frozen=True)
class Admission:
    """The admission verdict, plus what was measured on the way to it."""

    decision: AdmissionDecision
    queue_position: Optional[int] = None
    estimated_memory_mb: int = 0
    caps: Optional[MLTrainingCaps] = None
    warnings: Tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.decision is AdmissionDecision.ADMIT

    @property
    def deferred(self) -> bool:
        return self.decision is AdmissionDecision.DEFER

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "queue_position": self.queue_position,
            "estimated_memory_mb": self.estimated_memory_mb,
            "caps": None if self.caps is None else self.caps.to_dict(),
            "warnings": list(self.warnings),
        }


def _tenant_plan_of(value: Any) -> Tuple[TenantPlan, Tuple[str, ...]]:
    """Resolve any plan spelling onto ``TenantPlan``. Fails closed to FREE.

    The alias table is ``entitlement_engine.PlanMapper``, which itself normalises through
    ``SubscriptionEngine.migrate_plan_key`` - the platform's single source of truth for
    plan-name aliases. No alias is restated here.
    """
    warnings: List[str] = []
    if isinstance(value, TenantPlan):
        return value, ()
    name = str(_enum_value(value) or "").strip()
    if not name:
        return TenantPlan.FREE, (
            "No plan could be read for this user; caps resolved at the FREE tier.",
        )
    try:
        from backend_app.core.entitlement_engine import PlanMapper

        return PlanMapper.billing_to_tenant(name), ()
    except Exception:  # noqa: BLE001
        warnings.append(
            "The entitlement layer could not be consulted for plan resolution; the "
            "plan name was matched directly and caps fall back to FREE if it is "
            "unrecognised."
        )
    try:
        return TenantPlan(name.lower()), tuple(warnings)
    except ValueError:
        warnings.append(f"Unrecognised plan {name!r}; caps resolved at the FREE tier.")
        return TenantPlan.FREE, tuple(warnings)


def _plan_name_from_user(user: Any) -> Any:
    """The plan a user mapping declares, under any of the keys the platform uses."""
    if user is None:
        return None
    plan = getattr(user, "plan", None)
    if plan is not None:
        return plan
    if isinstance(user, Mapping):
        for key in ("plan", "subscription_plan", "plan_id", "tier", "billing_plan"):
            if user.get(key):
                return user[key]
    return None


def _intersect(tier_value: int, *ceilings: Optional[int]) -> int:
    """The tier's figure, lowered by every ceiling that applies. Never raised.

    This is the one direction the whole caps design turns on: an entitlement widens a
    limit *within* a ceiling it cannot lift. ``max`` here would let a tier upgrade buy an
    unsafe run.
    """
    effective = int(tier_value)
    for ceiling in ceilings:
        if ceiling is None:
            continue
        effective = min(effective, int(ceiling))
    return max(0, effective)


def estimate_memory_mb(request: TrainingRequest) -> int:
    """Estimate a run's peak footprint in MB.

    Explicitly an *estimate* - the design writes ``estimated <- estimate_memory_mb`` -
    and it is the only estimated figure in this module. A run's real footprint cannot be
    measured before the run exists; ``ml_safety.MemoryMonitor`` is the authoritative
    bound once it does.

    A SEQUENCE model's windowed view repeats each row ``sequence_length`` times, so the
    matrix it materialises is that much larger than the row count suggests. Treating a
    sequence model like a tree model here would under-count by two orders of magnitude
    at a 120-bar window, which is the case the cap exists for.
    """
    values = max(0, request.rows) * max(0, request.feature_columns)
    if request.is_sequence and request.sequence_length:
        values *= max(1, int(request.sequence_length))
    matrix_mb = (values * _BYTES_PER_VALUE) / (1024 * 1024)
    return int(math.ceil(matrix_mb * _MEMORY_PEAK_FACTOR)) + _MEMORY_BASE_MB


def resolve_caps(
    user: Any = None,
    model_spec: Any = None,
    *,
    plan: Any = None,
    tenant: Any = None,
    max_concurrent_jobs_global: Optional[int] = None,
) -> MLTrainingCaps:
    """The effective caps for ``user`` training ``model_spec``.

    Parameters
    ----------
    user
        A user mapping or object carrying a plan, as the API layer holds it.
    model_spec
        Any shape :meth:`ModelSpecView.coerce` accepts. Without it the epoch cap has no
        model ceiling and :func:`enforce_caps` will refuse the resulting caps.
    plan / tenant
        Explicit overrides. ``tenant`` may be a ``TenantContext``; its ``plan`` wins over
        anything read off ``user``.

    Postconditions
        ``max_epochs == min(tier_max_epochs, model_max_safe_epochs)`` whenever a model
        spec resolved - the intersection Requirement 16.2 specifies, in that direction.
        Every other dimension is likewise the tier figure lowered by every platform
        ceiling that applies, never raised by one.
    """
    warnings: List[str] = []

    plan_source: Any = plan
    if plan_source is None and tenant is not None:
        plan_source = getattr(tenant, "plan", None)
    if plan_source is None:
        plan_source = _plan_name_from_user(user)

    tenant_plan, plan_warnings = _tenant_plan_of(plan_source)
    warnings.extend(plan_warnings)

    tier = ML_TIER_CAPS.get(tenant_plan) or ML_TIER_CAPS[TenantPlan.FREE]
    spec = ModelSpecView.coerce(model_spec)

    # -- ceilings, each read from the surface that owns it ------------------
    runtime = _runtime_ceilings()
    platform_columns = _platform_max_feature_columns()

    quota = getattr(tenant, "quota", None)
    if quota is None:
        quota = TenantQuota.for_plan(tenant_plan)
    # The platform's existing per-tier compute-concurrency figure. ML training draws on
    # the same shared pool a parallel backtest does.
    tier_concurrency = max(
        int(tier.min_concurrent_jobs_per_user),
        _as_int(getattr(quota, "max_backtest_parallel", None), 0) or 0,
    )
    if tier.max_epochs <= 0:
        # A plan with no training allowance gets no concurrency either, whatever the
        # backtest figure says.
        tier_concurrency = 0

    # -- the intersection -------------------------------------------------
    model_ceiling = None if spec is None else int(spec.max_safe_epochs)
    max_epochs = _intersect(tier.max_epochs, model_ceiling)

    caps = MLTrainingCaps(
        max_epochs=max_epochs,
        max_rows=_intersect(tier.max_rows),
        max_feature_columns=_intersect(tier.max_feature_columns, platform_columns),
        max_concurrent_jobs_per_user=tier_concurrency,
        max_concurrent_jobs_global=int(
            DEFAULT_MAX_CONCURRENT_JOBS_GLOBAL
            if max_concurrent_jobs_global is None
            else max_concurrent_jobs_global
        ),
        max_duration_seconds=_intersect(
            tier.max_duration_seconds, runtime["max_duration_seconds"]
        ),
        max_memory_mb=_intersect(tier.max_memory_mb, runtime["max_memory_mb"]),
        max_model_size_mb=_intersect(
            tier.max_model_size_mb, runtime["max_model_size_mb"]
        ),
        plan=tenant_plan.value,
        block_id="" if spec is None else spec.block_id,
        epoch_unit="epochs" if spec is None else spec.epoch_unit,
        tier_max_epochs=int(tier.max_epochs),
        model_max_safe_epochs=0 if model_ceiling is None else model_ceiling,
        ml_training_entitled=_ml_training_entitled(tenant_plan, warnings),
        monthly_training_quota=_monthly_training_quota(tenant_plan, warnings),
        warnings=tuple(warnings),
    )
    return caps


def _ml_training_entitled(tenant_plan: TenantPlan, warnings: List[str]) -> bool:
    """Whether the plan grants ``ml_training``, read from the entitlement layer.

    Recorded on the caps, never used to admit or refuse here: ``require_ml_training``
    remains the gate on the endpoint (Requirement 16.7). What it buys is a caps payload
    that shows both layers were consulted and cannot silently disagree.
    """
    try:
        from backend_app.core.entitlement_engine import FeatureEntitlements, FeatureFlag

        return bool(
            FeatureEntitlements.is_feature_available(
                FeatureFlag.ML_TRAINING, tenant_plan
            )
        )
    except Exception:  # noqa: BLE001
        warnings.append(
            "The ML-training entitlement flag could not be read; require_ml_training "
            "still gates the endpoint."
        )
        return False


def _monthly_training_quota(
    tenant_plan: TenantPlan, warnings: List[str]
) -> Optional[int]:
    """The plan's monthly ``ml_trainings`` allowance, read from ``SubscriptionEngine``.

    Enforcement stays with ``check_ml_quota``; this is the figure, carried so a caps
    payload can state it.
    """
    try:
        from backend_app.core.entitlement_engine import PlanMapper
        from backend_app.core.subscription_engine import Resource, SubscriptionEngine

        billing = PlanMapper.tenant_to_billing(tenant_plan)
        return _as_int(
            SubscriptionEngine.get_quota_limit(billing, Resource.ML_TRAININGS.value)
        )
    except Exception:  # noqa: BLE001
        warnings.append(
            "The monthly ML-training allowance could not be read; check_ml_quota still "
            "gates the endpoint."
        )
        return None


def enforce_caps(
    request: Any,
    caps: MLTrainingCaps,
    user: Any = None,
    *,
    job_counts: Optional[JobCounts] = None,
) -> Admission:
    """Apply ``caps`` to ``request``. Raise on the first cap exceeded, else admit or defer.

    Evaluated inside the job creation path **and again inside the worker before the first
    epoch** (Requirement 16.4), so a request that bypasses the API still hits the wall.

    Raises
        :class:`CapExceeded` naming the one cap, its requested value and its permitted
        value (Requirement 16.3). :class:`MLTrainingPolicyError` when the caps were not
        resolved for this request's model, because then there is no model ceiling to
        intersect and the epoch cap would be the tier's alone.

    Returns
        :class:`Admission` with ``ADMIT``, or ``DEFER`` plus a queue position estimate
        when the global pool is saturated (Requirement 16.5). Deferring is not a
        rejection: the job is created and stays ``QUEUED``.

    Postconditions
        On ``ADMIT`` or ``DEFER``: ``request.epochs <= min(tier_cap, model.max_safe_epochs)``,
        ``request.rows <= caps.max_rows``, ``request.feature_columns <=
        caps.max_feature_columns`` and the estimated footprint is within
        ``caps.max_memory_mb``.
    """
    if not isinstance(request, TrainingRequest):
        if isinstance(request, Mapping):
            request = TrainingRequest(
                block_id=request.get("block_id", ""),
                epochs=_as_int(request.get("epochs"), 0) or 0,
                rows=_as_int(request.get("rows"), 0) or 0,
                feature_columns=_as_int(request.get("feature_columns"), 0) or 0,
                sequence_length=_as_int(request.get("sequence_length")),
                batch_size=_as_int(request.get("batch_size")),
                model_family=request.get("model_family", ""),
            )
        else:
            raise MLTrainingPolicyError(
                f"enforce_caps needs a TrainingRequest, got {type(request).__name__}"
            )

    if not caps.has_model_ceiling:
        raise MLTrainingPolicyError(
            "Caps were resolved without a model spec, so there is no max_safe_epochs to "
            "intersect the tier cap with. Resolve caps with resolve_caps(user, "
            "model_spec) before admitting a job (Requirement 16.2)."
        )
    if request.block_id and caps.block_id and request.block_id != caps.block_id:
        raise MLTrainingPolicyError(
            f"Caps were resolved for model {caps.block_id!r} but the request names "
            f"{request.block_id!r}. Admitting against another model's ceiling would "
            f"defeat the intersection."
        )

    warnings: List[str] = list(caps.warnings)

    if request.epochs > caps.max_epochs:
        raise CapExceeded(
            CAP_MAX_EPOCHS,
            request.epochs,
            caps.max_epochs,
            unit=caps.epoch_unit,
            block_id=caps.block_id,
            plan=caps.plan,
            fix_hint=(
                f"Reduce {caps.epoch_unit} to {caps.max_epochs} or fewer."
                if caps.max_epochs > 0
                else "This plan includes no ML training; upgrade to train models."
            ),
        )
    if request.rows > caps.max_rows:
        raise CapExceeded(
            CAP_MAX_ROWS,
            request.rows,
            caps.max_rows,
            unit="rows",
            block_id=caps.block_id,
            plan=caps.plan,
            fix_hint="Narrow the history range, or upgrade for a larger row allowance.",
        )
    if request.feature_columns > caps.max_feature_columns:
        raise CapExceeded(
            CAP_MAX_FEATURES,
            request.feature_columns,
            caps.max_feature_columns,
            unit="feature columns",
            block_id=caps.block_id,
            plan=caps.plan,
            fix_hint="Remove feature columns, or upgrade for a wider matrix.",
        )

    # -- concurrency: needs live counts from training_jobs ------------------
    counts = job_counts if job_counts is not None else JobCounts.unavailable()
    queue_position: Optional[int] = None
    if not counts.available:
        warnings.append(counts.unavailable_reason or JOB_COUNTS_UNAVAILABLE_WARNING)
    else:
        if counts.user_active >= caps.max_concurrent_jobs_per_user:
            raise CapExceeded(
                CAP_MAX_CONCURRENT_USER,
                counts.user_active + 1,
                caps.max_concurrent_jobs_per_user,
                unit="concurrent jobs",
                block_id=caps.block_id,
                plan=caps.plan,
                fix_hint=(
                    "Wait for a running training job to finish, or cancel one."
                    if caps.max_concurrent_jobs_per_user > 0
                    else "This plan includes no ML training; upgrade to train models."
                ),
            )
        if counts.global_running >= caps.max_concurrent_jobs_global:
            # Not a rejection. Requirement 16.5: hold it and estimate its position.
            return Admission(
                decision=AdmissionDecision.DEFER,
                queue_position=max(1, counts.global_queued + 1),
                estimated_memory_mb=estimate_memory_mb(request),
                caps=caps,
                warnings=tuple(warnings),
            )

    estimated = estimate_memory_mb(request)
    if estimated > caps.max_memory_mb:
        raise CapExceeded(
            CAP_MAX_MEMORY,
            estimated,
            caps.max_memory_mb,
            unit="MB",
            block_id=caps.block_id,
            plan=caps.plan,
            fix_hint=(
                "Reduce rows or feature columns"
                + (
                    ", or shorten the sequence length"
                    if request.is_sequence
                    else ""
                )
                + "."
            ),
        )

    return Admission(
        decision=AdmissionDecision.ADMIT,
        queue_position=queue_position,
        estimated_memory_mb=estimated,
        caps=caps,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Validation stage 11 - the gate, wired into the one rule engine
# ---------------------------------------------------------------------------


def _stats_by_node(
    raw: Any, ml_node_ids: Sequence[str]
) -> Dict[str, DatasetStats]:
    """Normalise the caller's dataset statistics into ``node_id -> DatasetStats``.

    Two shapes are accepted, because both are honest:

    * one stats mapping / dataset, applied to every ML node - the common case, where all
      model nodes read the same assembled feature matrix;
    * ``{node_id: stats}``, for a graph whose model nodes read different matrices.

    A per-node mapping that names a node the graph does not carry is an error, not a
    silent no-op: it means the caller measured a different graph.
    """
    if isinstance(raw, Mapping):
        known = set(ml_node_ids)
        # A per-node mapping is one whose keys are node ids. Distinguish it from a stats
        # mapping by looking for any known node id as a key - a stats mapping's keys are
        # 'rows', 'columns' and friends, never a node id.
        if known and known & set(map(str, raw.keys())):
            unknown = sorted(set(map(str, raw.keys())) - known)
            if unknown:
                raise DatasetStatsError(
                    "Dataset statistics were supplied for node(s) this graph does not "
                    f"carry: {unknown}. The statistics belong to a different graph."
                )
            return {
                str(node_id): DatasetStats.coerce(value)
                for node_id, value in raw.items()
            }

    shared = DatasetStats.coerce(raw)
    return {str(node_id): shared for node_id in ml_node_ids}


def gate_issues_for_context(context: "ValidationContext") -> List[Dict[str, Any]]:
    """Run the minimum-data gate over every ML node in ``context``.

    The model figures come from ``context.descriptors[node_id].metadata["model"]``, which
    ``registry.descriptor_from_model_spec`` carries through verbatim from
    ``ml_models.ModelSpec``. That is the same route stage 10b takes to its leakage facts,
    and it is why ``strategy_dag`` needs no ``ml_models`` import to run this stage - the
    figures were already in the descriptor the validator resolved.

    Loop invariants
        The ML node id list is computed **once**, sorted, before any node is examined,
        and the returned issues are sorted by ``(node_id, code)``. So permuting the
        graph's node or edge lists cannot change the verdict or its order - the same
        property stage 10b holds, for the same reason: a graph must not validate
        differently across two processes.
    """
    ml_node_ids = sorted(context.nodes_in_category(BlockCategory.ML_DL))
    if not ml_node_ids:
        return []

    stats_by_node = _stats_by_node(context.ml_dataset_stats, ml_node_ids)

    issues: List[Dict[str, Any]] = []
    for node_id in ml_node_ids:
        stats = stats_by_node.get(node_id)
        if stats is None:
            raise DatasetStatsError(
                f"No dataset statistics were supplied for model node {node_id!r}; "
                f"the ML readiness stage cannot be answered for it."
            )
        # The composed warmup at the model node is the longest feature lookback on a path
        # into it, which is the embargo floor's lookback half.
        lookback = _as_int(context.warmup_by_node.get(node_id))
        verdict = check_ml_data_requirements(
            None,
            stats,
            context.descriptors.get(node_id),
            node_id=node_id,
            feature_lookback=lookback,
        )
        issues.extend(verdict.issues)

    issues.sort(
        key=lambda issue: (
            str(issue.get("node_id") or ""),
            str(issue.get("code") or ""),
            str(issue.get("field") or ""),
        )
    )
    return issues


def ml_readiness_stage(context: "ValidationContext") -> List[Dict[str, Any]]:
    """The stage-11 hook. Skips cleanly when the caller supplied no dataset statistics.

    Stage 11 is the one stage that cannot be answered from a graph: a graph declares
    which model a node runs, not how many rows and columns the fetched data produced. So
    when ``context.ml_dataset_stats`` is absent this raises
    ``validator.StageSkipped``, and the report records the stage ``SKIPPED`` - the same
    distinction stage 10 draws between *implemented but could not run* and
    ``NOT_IMPLEMENTED``. A graph validated without dataset statistics must not read as
    ML-ready.

    A graph with no ML node at all is ``PASSED``, not skipped: there is nothing to check
    and the absence of statistics is not a gap (Requirement 14.10).
    """
    # Imported here, not at module scope: the validator imports *this* module at its own
    # import time to install this hook, so the reverse import can only be resolved at
    # call time - by which point the validator is fully initialised, since this function
    # can only run from validate().
    from backend_app.backend.strategy_dag.validator import StageSkipped

    if not context.nodes_in_category(BlockCategory.ML_DL):
        return []

    if context.ml_dataset_stats is None:
        raise StageSkipped(
            "ML readiness needs measured dataset statistics (usable rows and usable "
            "feature columns from the built dataset), which a graph does not carry. "
            "Pass validate(..., ml_dataset_stats=...) from the training path, which "
            "has the fetched dataset. This graph's ML nodes are NOT confirmed ready."
        )

    try:
        return gate_issues_for_context(context)
    except DatasetStatsError as exc:
        # Unreadable or mismatched statistics are a skip, not a pass: the stage is
        # implemented and could not run. Reporting PASSED here would be the exact
        # false-clean signal the seam docstring warns about.
        raise StageSkipped(f"ML readiness could not run: {exc}") from exc
