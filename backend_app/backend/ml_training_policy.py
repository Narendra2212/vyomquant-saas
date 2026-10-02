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
from dataclasses import dataclass, field, replace
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
    SEVERITY_WARNING,
    BlockCategory,
    make_issue,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed
    from backend_app.backend.strategy_dag.validator import ValidationContext

logger = logging.getLogger("MLTrainingPolicy")

#: Bumped when a cap dimension or a gate code changes, so a stored admission record can
#: be told apart from one produced by a later policy.
#:
#: 1.0.0  the minimum-data gate (rows, feature columns, sequence window) + the caps.
#: 2.0.0  the data-sufficiency engine: task awareness, the classification /
#:        regression / time-series checks, computed split feasibility, and the
#:        three-outcome verdict (VALID / WARNING / BLOCKED). A MAJOR bump because new
#:        BLOCKING codes exist, so a decision recorded under 1.0.0 cannot be assumed to
#:        have been evaluated against them. Historical decisions are NOT re-evaluated;
#:        every job records the version it was admitted under.
POLICY_VERSION = "2.0.0"

__all__ = [
    "POLICY_VERSION",
    "MLTrainingPolicyError",
    "CapExceeded",
    "DatasetStatsError",
    # model families
    "FAMILY_TREE",
    "FAMILY_SEQUENCE",
    "FAMILY_AUTOENCODER",
    # tasks
    "TASK_CLASSIFICATION",
    "TASK_REGRESSION",
    "TASK_RECONSTRUCTION",
    "SUPERVISED_TASKS",
    "resolve_task",
    # gate
    "CODE_INSUFFICIENT_FEATURE_COLUMNS",
    "CODE_INSUFFICIENT_ROWS",
    "CODE_INSUFFICIENT_SEQUENCE_ROWS",
    "CODE_MODEL_NOT_TRAINABLE",
    "CODE_MODEL_SPEC_UNAVAILABLE",
    # data sufficiency - blocking
    "CODE_TASK_UNSUPPORTED",
    "CODE_SINGLE_CLASS_TARGET",
    "CODE_CLASS_ABSENT_FROM_TRAIN",
    "CODE_VALIDATION_SPLIT_SINGLE_CLASS",
    "CODE_CLASS_TOO_RARE",
    "CODE_TARGET_NO_VARIANCE",
    "CODE_LABELS_UNUSABLE",
    "CODE_FEATURES_NO_VARIANCE",
    "CODE_SPLIT_INFEASIBLE",
    "CODE_INDEX_NOT_CHRONOLOGICAL",
    # data sufficiency - warning
    "CODE_CLASS_IMBALANCE",
    "CODE_MINORITY_CLASS_SMALL",
    "CODE_CLASS_ABSENT_FROM_VALIDATION",
    "CODE_LIMITED_SAMPLES",
    "CODE_HIGH_DIMENSIONALITY",
    "CODE_CONSTANT_FEATURE_COLUMNS",
    "CODE_NEAR_CONSTANT_FEATURE_COLUMNS",
    "CODE_DUPLICATE_FEATURE_ROWS",
    "CODE_UNLABELLED_ROWS",
    "CODE_TARGET_OUTLIERS",
    "CODE_TARGET_LOW_CARDINALITY",
    "CODE_IRREGULAR_INTERVALS",
    "CODE_DUPLICATE_TIMESTAMPS",
    "CODE_TASK_SUPPORT_UNKNOWN",
    "CODE_SUFFICIENCY_NOT_MEASURED",
    "ModelSpecView",
    "ValidationConfig",
    "DatasetStats",
    "MeasurementsView",
    "SufficiencyThresholds",
    "DEFAULT_SUFFICIENCY_THRESHOLDS",
    "GateOutcome",
    "GateVerdict",
    "SplitPlan",
    "rows_for_feasible_split",
    "check_ml_data_requirements",
    "format_thousands",
    # caps
    "CAP_MAX_EPOCHS",
    "CAP_MAX_ROWS",
    "CAP_MAX_FEATURES",
    "CAP_MAX_CONCURRENT_USER",
    "CAP_MAX_MEMORY",
    "CAP_MAX_SEARCH_TRIALS",
    "CAP_MAX_SEARCH_SECONDS",
    "TierTrainingCaps",
    "ML_TIER_CAPS",
    # parameter-search governance
    "TierSearchCaps",
    "ML_TIER_SEARCH_CAPS",
    "SearchBudget",
    "resolve_search_budget",
    "enforce_search_budget",
    "DEFAULT_SEARCH_SECONDS_PER_TRIAL",
    "MIN_MEANINGFUL_SEARCH_TRIALS",
    "MLTrainingCaps",
    "TrainingRequest",
    "JobCounts",
    "AdmissionDecision",
    "Admission",
    "resolve_caps",
    "enforce_caps",
    "estimate_memory_mb",
    # adaptive budget
    "EarlyStoppingPolicy",
    "TrainingBudget",
    "resolve_budget",
    "cells_per_epoch",
    "estimate_seconds_per_epoch",
    "FAMILY_COST_FACTOR",
    "DEFAULT_EPOCH_THROUGHPUT_CELLS",
    "DEFAULT_MAX_CHECKPOINTS",
    "DURATION_PLANNING_HEADROOM",
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

#: ``ml_models.ModelTask`` values, as strings, for the same reason the families are:
#: they arrive here as strings whichever way the spec was read. They are also
#: ``ml_dataset.LabelMode``'s two values for the supervised pair, deliberately - the
#: label mode a run resolved IS its task, and one spelling means the gate cannot
#: disagree with the dataset builder about which it is.
TASK_CLASSIFICATION = "classification"
TASK_REGRESSION = "regression"
TASK_RECONSTRUCTION = "reconstruction"

SUPERVISED_TASKS: Tuple[str, ...] = (TASK_CLASSIFICATION, TASK_REGRESSION)


def _task_tuple(raw: Any) -> Tuple[str, ...]:
    """Normalise a supported-task collection to lowercase strings, order preserved."""
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)):
        value = str(_enum_value(raw) or "").lower()
        return (value,) if value else ()
    try:
        items = list(raw)
    except TypeError:
        return ()
    out: List[str] = []
    for item in items:
        value = str(_enum_value(item) or "").lower()
        if value and value not in out:
            out.append(value)
    return tuple(out)


# -- gate codes (design.md -> Minimum-data gate) ----------------------------
CODE_INSUFFICIENT_FEATURE_COLUMNS = "INSUFFICIENT_FEATURE_COLUMNS"
CODE_INSUFFICIENT_ROWS = "INSUFFICIENT_ROWS"
CODE_INSUFFICIENT_SEQUENCE_ROWS = "INSUFFICIENT_SEQUENCE_ROWS"
CODE_MODEL_NOT_TRAINABLE = "MODEL_NOT_TRAINABLE"
#: The node is an ML block but no model descriptor could be read for it. Reported as an
#: error, never skipped: an unknown model's data requirements cannot be asserted either
#: way, and "we could not check" must not read as "it is fine".
CODE_MODEL_SPEC_UNAVAILABLE = "MODEL_SPEC_UNAVAILABLE"

# -- data-sufficiency codes: BLOCKING ---------------------------------------
#
# Every one of these means "training this dataset with this model produces nothing
# usable", not "this looks risky". The risky ones are the WARNING codes below, and the
# separation is the whole point of the three-outcome design: a gate that blocked on risk
# would refuse legitimate work, and a gate that only warned about impossibility would
# silently train a model that cannot learn.
#
#: The label mode the run resolved is not one this block can fit. Fitting a classifier
#: on a continuous target produces a model that scores well and means nothing.
CODE_TASK_UNSUPPORTED = "TASK_UNSUPPORTED"
#: A classification target with fewer than two classes. There is nothing to discriminate.
CODE_SINGLE_CLASS_TARGET = "SINGLE_CLASS_TARGET"
#: A class that exists in the dataset has no rows at all in the train split, so the
#: model cannot learn it and will never predict it.
CODE_CLASS_ABSENT_FROM_TRAIN = "CLASS_ABSENT_FROM_TRAIN"
#: The validation split carries a single class, so no validation metric can discriminate
#: and validation-aware early stopping has nothing to monitor.
CODE_VALIDATION_SPLIT_SINGLE_CLASS = "VALIDATION_SPLIT_SINGLE_CLASS"
#: A class has so few rows that it cannot be represented in train, validation and test
#: at once. Distinct from the imbalance WARNING: this is a structural impossibility.
CODE_CLASS_TOO_RARE = "CLASS_TOO_RARE"
#: A regression target with no variance. Every label is the same number.
CODE_TARGET_NO_VARIANCE = "TARGET_NO_VARIANCE"
#: Every label is NaN or infinite, so there is nothing to fit.
CODE_LABELS_UNUSABLE = "LABELS_UNUSABLE"
#: Every feature column is constant, so the matrix carries no information.
CODE_FEATURES_NO_VARIANCE = "FEATURES_NO_VARIANCE"
#: The requested fractions cannot produce a non-empty train, validation and test split
#: over the measured rows once the embargo is carved out. Computed BEFORE admission,
#: with required-versus-available figures, rather than discovered later as a
#: `TemporalSplitError` after the gate already said yes.
CODE_SPLIT_INFEASIBLE = "SPLIT_INFEASIBLE"
#: The dataset's own index is not strictly increasing, so chronological integrity - and
#: therefore the embargo that depends on it - cannot be established.
CODE_INDEX_NOT_CHRONOLOGICAL = "INDEX_NOT_CHRONOLOGICAL"

# -- data-sufficiency codes: WARNING ----------------------------------------
#
# Technically trainable, with a meaningful concern. Training proceeds and the concern is
# stated in the author's own quantities.
CODE_CLASS_IMBALANCE = "CLASS_IMBALANCE"
CODE_MINORITY_CLASS_SMALL = "MINORITY_CLASS_SMALL"
CODE_CLASS_ABSENT_FROM_VALIDATION = "CLASS_ABSENT_FROM_VALIDATION"
CODE_LIMITED_SAMPLES = "LIMITED_SAMPLES"
CODE_HIGH_DIMENSIONALITY = "HIGH_DIMENSIONALITY"
CODE_CONSTANT_FEATURE_COLUMNS = "CONSTANT_FEATURE_COLUMNS"
CODE_NEAR_CONSTANT_FEATURE_COLUMNS = "NEAR_CONSTANT_FEATURE_COLUMNS"
CODE_DUPLICATE_FEATURE_ROWS = "DUPLICATE_FEATURE_ROWS"
CODE_UNLABELLED_ROWS = "UNLABELLED_ROWS"
CODE_TARGET_OUTLIERS = "TARGET_OUTLIERS"
CODE_TARGET_LOW_CARDINALITY = "TARGET_LOW_CARDINALITY"
CODE_IRREGULAR_INTERVALS = "IRREGULAR_INTERVALS"
CODE_DUPLICATE_TIMESTAMPS = "DUPLICATE_TIMESTAMPS"
CODE_TASK_SUPPORT_UNKNOWN = "TASK_SUPPORT_UNKNOWN"
CODE_SUFFICIENCY_NOT_MEASURED = "SUFFICIENCY_NOT_MEASURED"

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


# ---------------------------------------------------------------------------
# Sufficiency thresholds - the policy knobs, all configurable, all bounded
# ---------------------------------------------------------------------------
#
# WHY THESE ARE NOT ONE UNIVERSAL MINIMUM
# ---------------------------------------
# A single "datasets must have N rows" number is wrong in both directions at once: it
# refuses a 3,000-row tree model that would train perfectly and admits a 3,000-row
# transformer that cannot. So the ROW requirement stays derived from the model spec
# (`required_row_count`), and what lives here is only the SHAPE of the requirements that
# cannot be read off a spec: how rare a class may be before it is unlearnable, how
# imbalanced is worth warning about, how many samples per feature is thin.
#
# Every one is a ratio or a small structural count rather than an absolute row figure,
# which is what keeps them model-agnostic. Every one is overridable from the
# environment, and every override is clamped - see `_env_ratio` / `_env_count` - so a
# misconfiguration cannot produce a gate that admits everything or one that admits
# nothing.


def _env_ratio(name: str, default: float, *, low: float, high: float) -> float:
    """A ratio from the environment, clamped into ``[low, high]``.

    Clamped rather than validated-and-rejected: this module is imported by the
    validator, and an operator's typo in an environment variable must not make the
    strategy builder unimportable. An out-of-range value is logged and pulled to the
    nearest bound, which is the fail-safe direction for a threshold.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _as_float(raw.strip())
    if value is None:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    clamped = min(high, max(low, value))
    if clamped != value:
        logger.warning(
            "%s=%r is outside [%s, %s]; clamped to %s", name, raw, low, high, clamped
        )
    return clamped


def _env_count(name: str, default: int, *, low: int, high: int) -> int:
    """A count from the environment, clamped into ``[low, high]``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _as_int(raw.strip())
    if value is None:
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    clamped = min(high, max(low, value))
    if clamped != value:
        logger.warning(
            "%s=%r is outside [%s, %s]; clamped to %s", name, raw, low, high, clamped
        )
    return clamped


@dataclass(frozen=True)
class SufficiencyThresholds:
    """Where WARNING ends and BLOCKED begins, per concern.

    Separated from :class:`ml_dataset.MeasurementThresholds` on purpose: that type
    decides what a measurement MEANS (what counts as near-constant), this one decides
    what a measurement COSTS (whether it refuses a run). Measuring and deciding are
    different jobs and they belong to different layers.
    """

    # -- classification, structural (BLOCK) ------------------------------
    #: Rows a class needs in EVERY split to be represented at all. Two, not one: a
    #: single row cannot produce a metric with any stability, and a split holding one
    #: example of a class makes a validation score swing by its entire weight.
    min_class_rows_per_split: int = 2
    #: Classes a classification target needs. Two. One class is not a classification
    #: problem, and this is the one figure here that is genuinely universal.
    min_classes: int = 2

    # -- classification, statistical (WARN) ------------------------------
    #: ``majority / minority`` beyond this is reported. 10 is the point at which a
    #: naive majority-class predictor starts to look good on accuracy, which is when an
    #: author needs telling.
    warn_imbalance_ratio: float = 10.0
    #: A minority class below this many rows IN TRAIN is reported as likely
    #: unlearnable - distinct from the structural floor above, which is about being
    #: representable at all.
    warn_minority_train_rows: int = 50

    # -- regression (WARN) -----------------------------------------------
    #: Distinct target values below this ratio of rows is reported: a "continuous"
    #: target with a handful of levels is a classification problem wearing a disguise.
    warn_target_unique_ratio: float = 0.01
    #: Outlier rows beyond this share of the dataset is reported.
    warn_outlier_row_ratio: float = 0.01

    # -- shape and quality (WARN) ----------------------------------------
    #: Usable rows per feature column below this is reported as high-dimensional
    #: relative to the sample, which is the classic overfitting setup.
    warn_samples_per_feature: float = 10.0
    #: Rows below this multiple of the model's own required count is reported as
    #: limited - trainable, with a real generalisation concern.
    warn_row_headroom_ratio: float = 1.25
    #: Duplicate feature rows beyond this share is reported: the matrix carries less
    #: information than its row count claims.
    warn_duplicate_row_ratio: float = 0.10
    #: Unlabelled (NaN/inf label) rows beyond this share is reported.
    warn_unlabelled_row_ratio: float = 0.001
    #: Irregular bar intervals beyond this share is reported as a gappy window.
    warn_irregular_interval_ratio: float = 0.05

    @classmethod
    def from_env(cls) -> "SufficiencyThresholds":
        """Thresholds with every environment override applied and clamped.

        Resolved per call rather than once at import, so an operator can retune a
        threshold without a restart and a test can set one without reimporting the
        module. The cost is a handful of ``os.getenv`` calls per gate evaluation.
        """
        return cls(
            min_class_rows_per_split=_env_count(
                "ML_SUFFICIENCY_MIN_CLASS_ROWS_PER_SPLIT", 2, low=1, high=1_000
            ),
            min_classes=_env_count("ML_SUFFICIENCY_MIN_CLASSES", 2, low=2, high=1_000),
            warn_imbalance_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_IMBALANCE_RATIO", 10.0, low=1.0, high=1e6
            ),
            warn_minority_train_rows=_env_count(
                "ML_SUFFICIENCY_WARN_MINORITY_TRAIN_ROWS", 50, low=1, high=1_000_000
            ),
            warn_target_unique_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_TARGET_UNIQUE_RATIO", 0.01, low=0.0, high=1.0
            ),
            warn_outlier_row_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_OUTLIER_ROW_RATIO", 0.01, low=0.0, high=1.0
            ),
            warn_samples_per_feature=_env_ratio(
                "ML_SUFFICIENCY_WARN_SAMPLES_PER_FEATURE", 10.0, low=0.0, high=1e6
            ),
            warn_row_headroom_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_ROW_HEADROOM_RATIO", 1.25, low=1.0, high=100.0
            ),
            warn_duplicate_row_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_DUPLICATE_ROW_RATIO", 0.10, low=0.0, high=1.0
            ),
            warn_unlabelled_row_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_UNLABELLED_ROW_RATIO", 0.001, low=0.0, high=1.0
            ),
            warn_irregular_interval_ratio=_env_ratio(
                "ML_SUFFICIENCY_WARN_IRREGULAR_INTERVAL_RATIO", 0.05, low=0.0, high=1.0
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_class_rows_per_split": self.min_class_rows_per_split,
            "min_classes": self.min_classes,
            "warn_imbalance_ratio": self.warn_imbalance_ratio,
            "warn_minority_train_rows": self.warn_minority_train_rows,
            "warn_target_unique_ratio": self.warn_target_unique_ratio,
            "warn_outlier_row_ratio": self.warn_outlier_row_ratio,
            "warn_samples_per_feature": self.warn_samples_per_feature,
            "warn_row_headroom_ratio": self.warn_row_headroom_ratio,
            "warn_duplicate_row_ratio": self.warn_duplicate_row_ratio,
            "warn_unlabelled_row_ratio": self.warn_unlabelled_row_ratio,
            "warn_irregular_interval_ratio": self.warn_irregular_interval_ratio,
        }


#: The import-time defaults, for a caller that wants the shipped figures without reading
#: the environment. The gate itself calls :meth:`SufficiencyThresholds.from_env`.
DEFAULT_SUFFICIENCY_THRESHOLDS = SufficiencyThresholds()


# ---------------------------------------------------------------------------
# MeasurementsView - a read-only window onto ml_dataset's measurements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementsView:
    """The measured label/index/feature facts, read without importing ``ml_dataset``.

    ``ml_dataset.measure_dataset`` produces a `DatasetMeasurements`; this reads its
    ``to_dict()``. The indirection is not ceremony: ``strategy_dag.validator`` imports
    THIS module at its own import time to install the ML readiness stage, so a numpy
    import at this module's scope would be paid by the whole validator, and
    `tests/test_strategy_dag_architecture.py` measures exactly that in a fresh
    interpreter. The measuring stays where numpy already is; the deciding stays here.

    Every accessor returns ``None`` for a quantity the mapping does not carry, and the
    gate treats ``None`` as "not measured" rather than as zero. A dataset measured by an
    older build therefore loses the checks it has no data for, and keeps the rest -
    instead of being refused for facts nobody recorded.
    """

    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.data)

    def _int(self, key: str) -> Optional[int]:
        return _as_int(self.data.get(key))

    def _float(self, key: str) -> Optional[float]:
        return _as_float(self.data.get(key))

    @property
    def label_mode(self) -> str:
        return str(_enum_value(self.data.get("label_mode")) or "").lower()

    @property
    def rows(self) -> Optional[int]:
        return self._int("rows")

    @property
    def columns(self) -> Optional[int]:
        return self._int("columns")

    @property
    def class_counts(self) -> Dict[int, int]:
        """``{class: rows}``. Keys arrive as strings through JSON and come back as ints."""
        raw = self.data.get("class_counts")
        if not isinstance(raw, Mapping):
            return {}
        out: Dict[int, int] = {}
        for key, value in raw.items():
            label = _as_int(key)
            count = _as_int(value)
            if label is not None and count is not None:
                out[label] = count
        return out

    @property
    def n_classes(self) -> int:
        return len(self.class_counts)

    @property
    def minority_count(self) -> Optional[int]:
        counts = self.class_counts
        return min(counts.values()) if counts else None

    @property
    def imbalance_ratio(self) -> Optional[float]:
        counts = self.class_counts
        if not counts:
            return None
        minority = min(counts.values())
        if minority <= 0:
            return None
        return float(max(counts.values())) / float(minority)

    @property
    def label_nan_rows(self) -> Optional[int]:
        return self._int("label_nan_rows")

    @property
    def label_inf_rows(self) -> Optional[int]:
        return self._int("label_inf_rows")

    @property
    def target_variance(self) -> Optional[float]:
        return self._float("target_variance")

    @property
    def target_unique(self) -> Optional[int]:
        return self._int("target_unique")

    @property
    def target_unique_ratio(self) -> Optional[float]:
        return self._float("target_unique_ratio")

    @property
    def target_outlier_rows(self) -> Optional[int]:
        return self._int("target_outlier_rows")

    @property
    def index_strictly_increasing(self) -> Optional[bool]:
        value = self.data.get("index_strictly_increasing")
        return None if value is None else bool(value)

    @property
    def duplicate_timestamps(self) -> Optional[int]:
        return self._int("duplicate_timestamps")

    @property
    def non_monotonic_rows(self) -> Optional[int]:
        return self._int("non_monotonic_rows")

    @property
    def irregular_interval_rows(self) -> Optional[int]:
        return self._int("irregular_interval_rows")

    @property
    def constant_feature_columns(self) -> Tuple[str, ...]:
        raw = self.data.get("constant_feature_columns") or ()
        if isinstance(raw, (str, bytes)):
            return ()
        return tuple(str(name) for name in raw)

    @property
    def near_constant_feature_columns(self) -> Tuple[str, ...]:
        raw = self.data.get("near_constant_feature_columns") or ()
        if isinstance(raw, (str, bytes)):
            return ()
        return tuple(str(name) for name in raw)

    @property
    def duplicate_feature_rows(self) -> Optional[int]:
        return self._int("duplicate_feature_rows")

    @property
    def splits(self) -> Tuple[Mapping[str, Any], ...]:
        raw = self.data.get("splits") or ()
        if isinstance(raw, (str, bytes, Mapping)):
            return ()
        return tuple(item for item in raw if isinstance(item, Mapping))

    def split(self, name: str) -> Optional[Mapping[str, Any]]:
        for item in self.splits:
            if str(item.get("name") or "") == name:
                return item
        return None

    def split_class_counts(self, name: str) -> Dict[int, int]:
        item = self.split(name)
        if item is None:
            return {}
        raw = item.get("class_counts")
        if not isinstance(raw, Mapping):
            return {}
        out: Dict[int, int] = {}
        for key, value in raw.items():
            label = _as_int(key)
            count = _as_int(value)
            if label is not None and count is not None:
                out[label] = count
        return out

    def split_rows(self, name: str) -> Optional[int]:
        item = self.split(name)
        return None if item is None else _as_int(item.get("rows"))

    @classmethod
    def coerce(cls, value: Any) -> "MeasurementsView":
        """A view over a measurements mapping, a `DatasetMeasurements`, or nothing."""
        if value is None:
            return cls({})
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(dict(value))
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            try:
                payload = to_dict()
            except Exception:  # noqa: BLE001 - an unreadable measurement is "not measured"
                logger.debug("Dataset measurements could not be read", exc_info=True)
                return cls({})
            if isinstance(payload, Mapping):
                return cls(dict(payload))
        return cls({})


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
    #: ``ml_models.ModelSpec.supported_tasks``, as plain strings. Empty when the spec
    #: arrived from a descriptor produced before the field existed, which the gate
    #: reports as "task unknown" rather than assuming a task and asserting its
    #: requirements.
    supported_tasks: Tuple[str, ...] = ()
    #: The task assumed when the training configuration names no label mode.
    default_task: str = ""

    @property
    def is_sequence(self) -> bool:
        return str(_enum_value(self.model_family)).upper() == FAMILY_SEQUENCE

    @property
    def is_unsupervised(self) -> bool:
        """True when this block has no labels to balance and no target to check.

        An autoencoder reconstructs its input. Asserting class balance or target
        variance against it would refuse a perfectly trainable model for failing a
        requirement that does not apply to it.
        """
        return self.supported_tasks == (TASK_RECONSTRUCTION,)

    @property
    def reserved_fraction(self) -> float:
        return float(self.val_fraction) + float(self.test_fraction)

    def supports_task(self, task: Any) -> bool:
        """Whether this block can fit ``task``. Unknown support admits, and says so.

        An empty ``supported_tasks`` means the descriptor predates the field, not that
        the block supports nothing. Refusing on that would turn a schema gap into a
        blocked training run for every author on a stale registry snapshot, so the
        gate admits and records the gap as a warning instead.
        """
        if not self.supported_tasks:
            return True
        wanted = str(_enum_value(task) or "").lower()
        return wanted in self.supported_tasks

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
            "supported_tasks": list(self.supported_tasks),
            "default_task": self.default_task,
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
            supported_tasks=_task_tuple(data.get("supported_tasks")),
            default_task=str(_enum_value(data.get("default_task")) or "").lower(),
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
                "supported_tasks": getattr(spec, "supported_tasks", ()),
                "default_task": getattr(spec, "default_task", None),
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
    #: ``ml_dataset.DatasetMeasurements.to_dict()``, when the caller measured. The label,
    #: index and feature-variance facts the data-sufficiency engine decides on.
    #:
    #: Optional on purpose, and absent is a first-class state. The row and column counts
    #: above are what a caller can always produce; the measurements need the built ``y``
    #: and ``index``, which only the training path holds. A caller that supplies none
    #: gets the dimension checks it always got, plus one warning saying the
    #: task-specific checks did not run - never a silent pass that reads as "checked and
    #: fine".
    measurements: Optional[Mapping[str, Any]] = None

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
        if self.measurements is not None and not isinstance(self.measurements, Mapping):
            raise DatasetStatsError(
                "measurements must be a mapping (DatasetMeasurements.to_dict()), got "
                f"{type(self.measurements).__name__}"
            )
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
            "measurements": None if self.measurements is None else dict(self.measurements),
        }

    @property
    def measured(self) -> "MeasurementsView":
        """A view over the measurements, empty when none were supplied."""
        return MeasurementsView.coerce(self.measurements)

    def with_measurements(self, measurements: Any) -> "DatasetStats":
        """A copy carrying ``measurements``. Frozen type, so a copy rather than a set.

        Used by the training path, which builds the stats from the dataset and then
        measures it: attaching the facts here is what carries them through
        ``TrainingPreparation.stats_by_node()`` into validator stage 11, so the
        persisted validation report reflects the same checks the admission gate ran.
        """
        view = MeasurementsView.coerce(measurements)
        return replace(self, measurements=dict(view.data) if view.available else None)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_dataset(
        cls, dataset: Any, splits: Any = None, *, measure: bool = False
    ) -> "DatasetStats":
        """Measure a ``ml_dataset.SupervisedDataset``.

        ``n_rows`` is the row count of ``X``, which ``build_supervised_dataset`` produced
        from one row range starting at the feature matrix's warmup offset and stopping
        ``horizon`` rows early. So the warmup trim and the label-horizon trim are already
        in the number, and subtracting them again here would double-count.

        Args:
            dataset: the built ``(X, y)`` pair.
            splits: the temporal splits, when they exist. Only read when
                ``measure=True``, and then only to produce the per-split class and
                variance figures.
            measure: also run ``ml_dataset.measure_dataset`` and attach its facts, so
                the task-aware checks can run. **Defaults to False**, which keeps this
                constructor's existing cost and behaviour: it is called from paths that
                only need the dimensions, and measuring there would be work nobody reads.
                The training path passes ``True``.

        A measurement that fails is a logged warning and no measurements, never an
        exception: the dimension figures are correct either way, and refusing to build
        stats because an optional extra could not be computed would turn a reporting gap
        into a blocked training run.
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

        measurements: Optional[Dict[str, Any]] = None
        if measure:
            try:
                # In-function import, like every other reader in this module: ml_dataset
                # pulls numpy, and the validator imports this module at ITS import time.
                from backend_app.backend.ml_dataset import measure_dataset

                measurements = measure_dataset(dataset, splits).to_dict()
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                logger.warning(
                    "The dataset could not be measured (%s); the task-specific "
                    "sufficiency checks will be reported as not run.",
                    exc,
                )
                measurements = None

        return cls(
            usable_rows=rows,
            usable_feature_columns=columns,
            label_horizon=horizon,
            dropped_warmup_rows=dropped_warmup,
            dropped_trailing_rows=dropped_trailing,
            total_rows=rows + dropped_warmup + dropped_trailing,
            feature_names=tuple(str(name) for name in columns_seq),
            source="supervised_dataset",
            measurements=measurements,
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
            # Tolerant, deliberately: a payload produced before the measurements existed
            # carries none and must still load. The strictness above is about the figures
            # the gate CANNOT do without; this one it can report as unmeasured.
            measurements=(
                dict(data["measurements"])
                if isinstance(data.get("measurements"), Mapping)
                else None
            ),
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


class GateOutcome(str, Enum):
    """The three answers the data-sufficiency engine can give.

    WHY THREE AND NOT TWO
    ---------------------
    Two outcomes force every finding to be either fatal or invisible. A 40:1 class
    imbalance is not fatal - plenty of real strategies train on one - but an author who
    is not told about it will read a 97% accuracy as a result rather than as the
    majority-class baseline. A single-class target IS fatal, and warning about it would
    mean fitting a model that cannot discriminate and reporting it as trained.

    So: BLOCKED means "this produces nothing usable", WARNING means "this trains, and
    here is the concern in your own numbers", VALID means neither.
    """

    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SplitPlan:
    """The split sizes the requested fractions WOULD produce over ``rows``.

    Computed, not discovered. ``ml_dataset.make_temporal_splits`` refuses an infeasible
    geometry by raising, and that refusal stays - but it happens inside
    ``split_training_dataset``, which runs AFTER the gate has already said the dataset is
    sufficient. The author then sees a generic "the dataset cannot be split" where they
    should have seen "you need N rows for a 15/15 split with a 70-bar embargo and you
    have M".

    This reproduces that module's arithmetic exactly - ``test_start = n - floor(n *
    test_fraction)``, ``val_start = test_start - floor(n * val_fraction)``, embargo
    carved out of the end of train and of val - so the two cannot disagree about whether
    a geometry fits. It is the one place in this module that restates another module's
    formula, and it does so because the alternative is admitting a dataset that the next
    call will reject.
    """

    rows: int
    val_fraction: float
    test_fraction: float
    embargo_bars: int
    train: int
    val: int
    test: int

    @property
    def feasible(self) -> bool:
        return self.train > 0 and self.val > 0 and self.test > 0

    @property
    def reserved(self) -> int:
        """Rows consumed by the two embargo gaps - paid for, used by no split."""
        return 2 * int(self.embargo_bars)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "val_fraction": self.val_fraction,
            "test_fraction": self.test_fraction,
            "embargo_bars": self.embargo_bars,
            "train": self.train,
            "val": self.val,
            "test": self.test,
            "feasible": self.feasible,
            "embargo_rows_reserved": self.reserved,
        }

    @classmethod
    def compute(cls, rows: int, config: "ValidationConfig") -> "SplitPlan":
        """The geometry ``config`` produces over ``rows``, clamped at zero.

        Negative sizes are reported as zero rather than as negatives: a split cannot
        hold fewer than no rows, and a negative figure in a user-facing readiness panel
        reads as a bug rather than as "this does not fit".
        """
        total = max(0, int(rows))
        embargo = max(0, int(config.embargo_bars))
        test_start = total - int(math.floor(total * config.test_fraction))
        val_start = test_start - int(math.floor(total * config.val_fraction))
        # The embargo is carved out of the END of train and of val, exactly as
        # `make_temporal_splits` does it.
        train_rows = max(0, val_start - embargo)
        val_rows = max(0, (test_start - embargo) - val_start)
        test_rows = max(0, total - test_start)
        return cls(
            rows=total,
            val_fraction=float(config.val_fraction),
            test_fraction=float(config.test_fraction),
            embargo_bars=embargo,
            train=train_rows,
            val=val_rows,
            test=test_rows,
        )


def rows_for_feasible_split(config: "ValidationConfig", *, minimum_per_split: int = 1) -> int:
    """The fewest rows that give every split at least ``minimum_per_split`` rows.

    Each split binds for a different reason, and the embargo falls on two of the three:

    * ``test`` gets ``floor(n * test_fraction)`` rows and pays no embargo, so it needs
      ``n >= k / test_fraction``;
    * ``val`` gets ``floor(n * val_fraction)`` rows and the embargo is carved out of its
      tail, so it needs ``n >= (k + embargo) / val_fraction`` - this is usually the
      binding one, and the one a naive estimate misses;
    * ``train`` gets what is left minus an embargo, so it needs
      ``n >= (k + embargo) / train_fraction``.

    The algebra gives the right neighbourhood; the two ``floor`` calls make it wrong at
    the boundary by up to a row either way, so a short bounded search walks up from the
    estimate. A readiness panel that says "you need 4,117 rows" when 4,116 would do is a
    panel an author stops trusting, which is why this is exact rather than rounded up
    generously.

    Returns ``0`` when no row count can satisfy the geometry - a zero validation or test
    fraction, or a ``reserved_fraction`` so close to 1 that no split survives. Both are
    reported as "this geometry does not fit" rather than as a number.
    """
    floor_rows = max(1, int(minimum_per_split))
    embargo = max(0, int(config.embargo_bars))

    # A zero fraction cannot ever produce a non-empty split, whatever n is.
    # `make_temporal_splits` refuses these outright; there is no row count to report.
    if config.val_fraction <= 0.0 or config.test_fraction <= 0.0:
        return 0
    if config.train_fraction <= 0.0:
        return 0

    estimate = max(
        int(math.ceil(floor_rows / config.test_fraction)),
        int(math.ceil((floor_rows + embargo) / config.val_fraction)),
        int(math.ceil((floor_rows + embargo) / config.train_fraction)),
        3 * floor_rows,
    )
    # The floor() boundary can only ever cost a couple of rows, but search a generous
    # window so a pathological fraction cannot make this return a wrong "does not fit".
    candidate = max(3 * floor_rows, estimate - 16)
    ceiling = estimate + 256
    while candidate <= ceiling:
        plan = SplitPlan.compute(candidate, config)
        if plan.train >= floor_rows and plan.val >= floor_rows and plan.test >= floor_rows:
            return candidate
        candidate += 1
    return 0


@dataclass(frozen=True)
class GateVerdict:
    """The gate's answer, carrying required and available for both dimensions.

    ``required`` and ``available`` always hold ``columns`` and ``rows`` - even when the
    gate passed, and even when only one dimension fell short - because Requirement 14.9
    renders both quantities and a UI that has to ask a second time for the other half
    renders "insufficient data" instead.

    ``issues`` holds ONLY blocking findings and ``warnings`` holds only non-blocking
    ones. They are separate tuples rather than one list filtered by severity because
    every existing caller treats a non-empty ``issues`` as a refusal - the save path,
    the training path, validator stage 11 - and folding warnings into it would turn
    every advisory into a blocked training run.
    """

    ok: bool
    issues: Tuple[Dict[str, Any], ...]
    required: Mapping[str, Any]
    available: Mapping[str, Any]
    block_id: str = ""
    node_id: Optional[str] = None
    config: Optional[ValidationConfig] = None
    stats: Optional[DatasetStats] = None
    #: Non-blocking findings, in the same structured-issue shape as ``issues``.
    warnings: Tuple[Dict[str, Any], ...] = ()
    #: The task the run resolved to - the label mode intersected with the block's
    #: ``supported_tasks``. Empty when neither could be determined, which is itself
    #: reported as a warning rather than assumed.
    task: str = ""
    #: The split geometry the requested fractions produce over the measured rows.
    split_plan: Optional[SplitPlan] = None

    @property
    def codes(self) -> Tuple[str, ...]:
        return tuple(str(issue.get("code")) for issue in self.issues)

    @property
    def warning_codes(self) -> Tuple[str, ...]:
        return tuple(str(issue.get("code")) for issue in self.warnings)

    @property
    def outcome(self) -> GateOutcome:
        """BLOCKED on any issue, WARNING on any warning, else VALID."""
        if self.issues:
            return GateOutcome.BLOCKED
        if self.warnings:
            return GateOutcome.WARNING
        return GateOutcome.VALID

    def message(self) -> str:
        """The mandated message shape, straight out of required / available.

        > Training cannot start. Required: 5 feature columns and 5,000 usable rows.
        > Available: 3 feature columns and 1,240 rows.

        UNCHANGED for a dimension shortfall, because that sentence is Requirement 14.9's
        and existing callers render it. When the block is NOT a dimension shortfall - a
        single-class target, an infeasible split, an unsupported task - the row and
        column figures are both fine and quoting them would be actively misleading
        ("Required 2,000 rows, available 50,000" beside a refusal reads as a bug). In
        that case the issues' own messages are rendered instead, which already name the
        failing quantity.
        """
        if self.ok:
            return ""
        dimension_codes = {
            CODE_INSUFFICIENT_FEATURE_COLUMNS,
            CODE_INSUFFICIENT_ROWS,
            CODE_INSUFFICIENT_SEQUENCE_ROWS,
        }
        if dimension_codes.intersection(self.codes):
            return (
                "Training cannot start. Required: "
                f"{format_thousands(self.required.get('columns'))} feature columns and "
                f"{format_thousands(self.required.get('rows'))} usable rows. Available: "
                f"{format_thousands(self.available.get('columns'))} feature columns and "
                f"{format_thousands(self.available.get('rows'))} rows."
            )
        reasons = " ".join(
            str(issue.get("message") or "").strip() for issue in self.issues
        ).strip()
        return f"Training cannot start. {reasons}" if reasons else "Training cannot start."

    def warning_message(self) -> str:
        """The concerns, in one sentence each. Empty when there are none."""
        return " ".join(
            str(issue.get("message") or "").strip() for issue in self.warnings
        ).strip()

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
            "outcome": self.outcome.value,
            "warnings": [dict(issue) for issue in self.warnings],
            "warning_message": self.warning_message(),
            "task": self.task,
            "split_plan": None if self.split_plan is None else self.split_plan.to_dict(),
            "policy_version": POLICY_VERSION,
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


def resolve_task(
    spec: ModelSpecView,
    measured: "MeasurementsView",
    overrides: Optional[Mapping[str, Any]] = None,
) -> str:
    """The task a run resolves to, in precedence order.

    1. what the dataset ACTUALLY is - ``measurements["label_mode"]``, because the labels
       were built and measured and that is a fact rather than an intention;
    2. what the configuration asked for - ``label_mode`` in the overrides, for a gate
       evaluated before any dataset exists (validator stage 11);
    3. what the block assumes - ``spec.default_task``;
    4. nothing, which the caller reports rather than guessing around.

    An unsupervised block always resolves to its own single task: an autoencoder
    reconstructs its input whatever ``label_mode`` the configuration happens to carry,
    and letting a stray ``label_mode`` turn it into a "classification" run would then
    assert class balance against a model that has no classes.
    """
    if spec.is_unsupervised:
        return TASK_RECONSTRUCTION
    measured_mode = measured.label_mode
    if measured_mode:
        return measured_mode
    requested = str(_enum_value((overrides or {}).get("label_mode")) or "").lower()
    if requested:
        return requested
    return spec.default_task or ""


def _ratio(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    if numerator is None or not denominator:
        return None
    return float(numerator) / float(denominator)


def _classification_issues(
    spec: ModelSpecView,
    measured: "MeasurementsView",
    thresholds: SufficiencyThresholds,
    node_id: Optional[str],
) -> "Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """``(blocking, warning)`` findings for a CLASSIFICATION run.

    Every finding is collect-all: an author short on classes AND carrying an absent
    class in train is told both in one round, not one per retry.
    """
    blocking: List[Dict[str, Any]] = []
    warning: List[Dict[str, Any]] = []
    display = spec.display_name or spec.block_id

    counts = measured.class_counts
    if not counts:
        return blocking, warning

    # -- 1. there must be something to discriminate ----------------------
    if len(counts) < thresholds.min_classes:
        only = sorted(counts)
        blocking.append(
            make_issue(
                CODE_SINGLE_CLASS_TARGET,
                SEVERITY_ERROR,
                f"Every labelled row has the same target class ({only}), so there is "
                f"nothing for '{display}' to discriminate. A model fitted on this would "
                f"score perfectly and predict that one class forever.",
                node_id=node_id,
                field_name="label_threshold",
                expected=f"at least {thresholds.min_classes} classes",
                actual=f"{len(counts)} class",
                fix_hint=(
                    "Lower the classification threshold so price moves are split into "
                    "up/flat/down, use a longer label horizon, or widen the history "
                    "range to a period with more movement."
                ),
            )
        )

    # -- 2. a class too rare to be represented in all three splits -------
    #
    # Structural, not statistical: with three splits and a floor of
    # `min_class_rows_per_split` rows each, a class below that product cannot appear
    # everywhere it must appear however the rows fall.
    structural_floor = thresholds.min_class_rows_per_split * 3
    too_rare = sorted(cls for cls, count in counts.items() if count < structural_floor)
    if too_rare:
        blocking.append(
            make_issue(
                CODE_CLASS_TOO_RARE,
                SEVERITY_ERROR,
                f"Target class(es) {too_rare} have fewer than {structural_floor} rows in "
                f"the whole dataset, so they cannot be represented in the training, "
                f"validation and test splits at once. Class counts: "
                f"{ {cls: counts[cls] for cls in too_rare} }.",
                node_id=node_id,
                field_name="class_counts",
                expected=f"at least {structural_floor} rows per class",
                actual={str(cls): counts[cls] for cls in too_rare},
                fix_hint=(
                    "Widen the history range, or loosen the classification threshold so "
                    "the rare class is less rare."
                ),
            )
        )

    # -- 3. per-split representation, where splits were measured ---------
    train_counts = measured.split_class_counts("train")
    val_counts = measured.split_class_counts("val")

    if train_counts:
        absent = sorted(cls for cls in counts if train_counts.get(cls, 0) <= 0)
        if absent:
            blocking.append(
                make_issue(
                    CODE_CLASS_ABSENT_FROM_TRAIN,
                    SEVERITY_ERROR,
                    f"Target class(es) {absent} appear in the dataset but have no rows in "
                    f"the training split, so '{display}' cannot learn them and will never "
                    f"predict them. Training rows per class: {train_counts}.",
                    node_id=node_id,
                    field_name="split_class_counts",
                    expected="every class present in the training split",
                    actual={str(cls): train_counts.get(cls, 0) for cls in absent},
                    fix_hint=(
                        "Time-series splits are chronological and are never shuffled, so "
                        "a class confined to the end of the window lands only in test. "
                        "Widen the history range, or reduce the validation and test "
                        "fractions."
                    ),
                )
            )
        minority_train = min(train_counts.values()) if train_counts else None
        if (
            minority_train is not None
            and minority_train > 0
            and minority_train < thresholds.warn_minority_train_rows
        ):
            warning.append(
                make_issue(
                    CODE_MINORITY_CLASS_SMALL,
                    SEVERITY_WARNING,
                    f"The rarest target class has only {format_thousands(minority_train)} "
                    f"rows in the training split (fewer than "
                    f"{format_thousands(thresholds.warn_minority_train_rows)}). The model "
                    f"may learn it poorly and the metric for it will be unstable.",
                    node_id=node_id,
                    field_name="split_class_counts",
                    expected=f"at least {thresholds.warn_minority_train_rows} training rows per class",
                    actual=minority_train,
                    fix_hint=(
                        "Widen the history range, or loosen the classification threshold."
                    ),
                )
            )

    if val_counts:
        present = {cls for cls, count in val_counts.items() if count > 0}
        if len(present) < 2 and len(counts) >= 2:
            blocking.append(
                make_issue(
                    CODE_VALIDATION_SPLIT_SINGLE_CLASS,
                    SEVERITY_ERROR,
                    f"The validation split carries "
                    f"{'no classes' if not present else 'a single class ' + str(sorted(present))}, "
                    f"so no validation metric can discriminate and early stopping has "
                    f"nothing meaningful to monitor. Validation rows per class: "
                    f"{val_counts}.",
                    node_id=node_id,
                    field_name="split_class_counts",
                    expected="at least 2 classes in the validation split",
                    actual={str(cls): count for cls, count in val_counts.items()},
                    fix_hint=(
                        "Increase the validation fraction, widen the history range, or "
                        "loosen the classification threshold."
                    ),
                )
            )
        else:
            absent_val = sorted(cls for cls in counts if val_counts.get(cls, 0) <= 0)
            if absent_val:
                warning.append(
                    make_issue(
                        CODE_CLASS_ABSENT_FROM_VALIDATION,
                        SEVERITY_WARNING,
                        f"Target class(es) {absent_val} have no rows in the validation "
                        f"split, so the validation metric says nothing about how well the "
                        f"model handles them.",
                        node_id=node_id,
                        field_name="split_class_counts",
                        expected="every class present in the validation split",
                        actual={str(cls): val_counts.get(cls, 0) for cls in absent_val},
                        fix_hint=(
                            "Increase the validation fraction or widen the history range."
                        ),
                    )
                )

    # -- 4. imbalance, as a concern rather than a refusal ----------------
    ratio = measured.imbalance_ratio
    if ratio is not None and ratio > thresholds.warn_imbalance_ratio:
        warning.append(
            make_issue(
                CODE_CLASS_IMBALANCE,
                SEVERITY_WARNING,
                f"The target classes are imbalanced {ratio:.1f}:1 (majority to minority). "
                f"Accuracy will be dominated by the majority class, so judge this model "
                f"on '{spec.metric or 'a balanced metric'}' rather than on accuracy. "
                f"Class counts: {counts}.",
                node_id=node_id,
                field_name="class_counts",
                expected=f"an imbalance ratio at or below {thresholds.warn_imbalance_ratio:.0f}:1",
                actual=round(ratio, 2),
                fix_hint=(
                    "Loosen the classification threshold to even the classes out, or "
                    "widen the history range."
                ),
            )
        )
    return blocking, warning


def _regression_issues(
    spec: ModelSpecView,
    measured: "MeasurementsView",
    thresholds: SufficiencyThresholds,
    node_id: Optional[str],
) -> "Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """``(blocking, warning)`` findings for a REGRESSION run."""
    blocking: List[Dict[str, Any]] = []
    warning: List[Dict[str, Any]] = []
    display = spec.display_name or spec.block_id

    variance = measured.target_variance
    unique = measured.target_unique

    # -- 1. a target with no variance is not a target --------------------
    if variance is not None and variance <= 0.0:
        blocking.append(
            make_issue(
                CODE_TARGET_NO_VARIANCE,
                SEVERITY_ERROR,
                f"The regression target has zero variance - every labelled row carries "
                f"the same value - so '{display}' has nothing to predict.",
                node_id=node_id,
                field_name="target_variance",
                expected="a target with non-zero variance",
                actual=variance,
                fix_hint=(
                    "This usually means the price series did not move over the window. "
                    "Widen the history range or use a longer label horizon."
                ),
            )
        )
    elif unique is not None and unique < 2:
        blocking.append(
            make_issue(
                CODE_TARGET_NO_VARIANCE,
                SEVERITY_ERROR,
                f"The regression target holds {unique} distinct value(s), so there is "
                f"nothing for '{display}' to predict.",
                node_id=node_id,
                field_name="target_unique",
                expected="at least 2 distinct target values",
                actual=unique,
                fix_hint="Widen the history range or use a longer label horizon.",
            )
        )

    # -- 2. a "continuous" target with a handful of levels ---------------
    unique_ratio = measured.target_unique_ratio
    if (
        unique_ratio is not None
        and unique is not None
        and unique >= 2
        and unique_ratio <= thresholds.warn_target_unique_ratio
    ):
        warning.append(
            make_issue(
                CODE_TARGET_LOW_CARDINALITY,
                SEVERITY_WARNING,
                f"The regression target holds only {format_thousands(unique)} distinct "
                f"values across {format_thousands(measured.rows)} rows "
                f"({unique_ratio:.4f} of them). A target this discrete is usually a "
                f"classification problem, and a regression metric on it is hard to read.",
                node_id=node_id,
                field_name="target_unique",
                expected=f"a distinct-value ratio above {thresholds.warn_target_unique_ratio}",
                actual=round(unique_ratio, 6),
                fix_hint="Consider classification labelling instead.",
            )
        )

    # -- 3. outliers, reported and never removed -------------------------
    outliers = measured.target_outlier_rows
    outlier_ratio = _ratio(outliers, measured.rows)
    if (
        outliers
        and outlier_ratio is not None
        and outlier_ratio > thresholds.warn_outlier_row_ratio
    ):
        warning.append(
            make_issue(
                CODE_TARGET_OUTLIERS,
                SEVERITY_WARNING,
                f"{format_thousands(outliers)} of {format_thousands(measured.rows)} target "
                f"values lie beyond {_measurement_sigma(measured)} standard deviations "
                f"({outlier_ratio:.2%} of rows). Extreme forward returns will dominate a "
                f"squared-error objective.",
                node_id=node_id,
                field_name="target_outlier_rows",
                expected=f"at or below {thresholds.warn_outlier_row_ratio:.2%} of rows",
                actual=outliers,
                fix_hint=(
                    "No rows were removed. Consider a shorter label horizon, or a model "
                    "less sensitive to extreme targets."
                ),
            )
        )
    return blocking, warning


def _measurement_sigma(measured: "MeasurementsView") -> Any:
    """The sigma the outlier count was taken at, for an honest message."""
    thresholds = measured.data.get("thresholds")
    if isinstance(thresholds, Mapping):
        value = _as_float(thresholds.get("outlier_sigma"))
        if value is not None:
            return value
    return "the configured number of"


def _shape_and_quality_issues(
    spec: ModelSpecView,
    stats: DatasetStats,
    measured: "MeasurementsView",
    required_rows: int,
    thresholds: SufficiencyThresholds,
    node_id: Optional[str],
) -> "Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """``(blocking, warning)`` findings that apply to every task."""
    blocking: List[Dict[str, Any]] = []
    warning: List[Dict[str, Any]] = []
    rows = stats.usable_rows
    columns = stats.usable_feature_columns

    # -- 1. labels that are entirely unusable ----------------------------
    nan_rows = measured.label_nan_rows or 0
    inf_rows = measured.label_inf_rows or 0
    unlabelled = nan_rows + inf_rows
    if rows > 0 and unlabelled >= rows:
        blocking.append(
            make_issue(
                CODE_LABELS_UNUSABLE,
                SEVERITY_ERROR,
                f"All {format_thousands(rows)} labelled rows carry a non-finite target "
                f"({format_thousands(nan_rows)} NaN, {format_thousands(inf_rows)} "
                f"infinite), so there is nothing to fit.",
                node_id=node_id,
                field_name="label_nan_rows",
                expected="at least one finite target value",
                actual={"nan": nan_rows, "inf": inf_rows},
                fix_hint=(
                    "A non-finite forward return means the base price was zero or "
                    "missing. Check the market and timeframe on the DATA block."
                ),
            )
        )
    else:
        unlabelled_ratio = _ratio(unlabelled, rows)
        if (
            unlabelled
            and unlabelled_ratio is not None
            and unlabelled_ratio > thresholds.warn_unlabelled_row_ratio
        ):
            warning.append(
                make_issue(
                    CODE_UNLABELLED_ROWS,
                    SEVERITY_WARNING,
                    f"{format_thousands(unlabelled)} of {format_thousands(rows)} rows "
                    f"({unlabelled_ratio:.2%}) carry a non-finite target. For "
                    f"classification labelling these rows fall into the flat class rather "
                    f"than being dropped, which biases that class.",
                    node_id=node_id,
                    field_name="label_nan_rows",
                    expected=f"at or below {thresholds.warn_unlabelled_row_ratio:.2%} of rows",
                    actual=unlabelled,
                    fix_hint="Check the market and timeframe on the DATA block for gaps.",
                )
            )

    # -- 2. a feature matrix with no information -------------------------
    constant = measured.constant_feature_columns
    if constant and columns > 0 and len(constant) >= columns:
        blocking.append(
            make_issue(
                CODE_FEATURES_NO_VARIANCE,
                SEVERITY_ERROR,
                f"Every one of the {columns} feature column(s) is constant across the "
                f"whole dataset, so the matrix carries no information to learn from: "
                f"{list(constant)}.",
                node_id=node_id,
                field_name="constant_feature_columns",
                expected="at least one feature column that varies",
                actual=list(constant),
                fix_hint=(
                    "Check the indicator parameters feeding this model - a window longer "
                    "than the history range produces a flat column."
                ),
            )
        )
    elif constant:
        warning.append(
            make_issue(
                CODE_CONSTANT_FEATURE_COLUMNS,
                SEVERITY_WARNING,
                f"{len(constant)} of {columns} feature column(s) are constant and carry "
                f"no information: {list(constant)}. They still count against the feature "
                f"column cap.",
                node_id=node_id,
                field_name="constant_feature_columns",
                expected="feature columns that vary",
                actual=list(constant),
                fix_hint="Remove them, or check the indicator parameters that produce them.",
            )
        )

    near_constant = measured.near_constant_feature_columns
    if near_constant:
        warning.append(
            make_issue(
                CODE_NEAR_CONSTANT_FEATURE_COLUMNS,
                SEVERITY_WARNING,
                f"{len(near_constant)} feature column(s) take only a couple of distinct "
                f"values across the dataset: {list(near_constant)}. They carry very "
                f"little information relative to the cap they consume.",
                node_id=node_id,
                field_name="near_constant_feature_columns",
                expected="feature columns with meaningful variation",
                actual=list(near_constant),
                fix_hint="Consider removing them, or widening the history range.",
            )
        )

    # -- 3. samples per feature, the classic overfitting setup -----------
    if columns > 0 and rows > 0:
        per_feature = float(rows) / float(columns)
        if per_feature < thresholds.warn_samples_per_feature:
            warning.append(
                make_issue(
                    CODE_HIGH_DIMENSIONALITY,
                    SEVERITY_WARNING,
                    f"{format_thousands(rows)} usable rows across {columns} feature "
                    f"columns is {per_feature:.1f} samples per feature (below "
                    f"{thresholds.warn_samples_per_feature:.0f}). A model this wide "
                    f"relative to its sample tends to fit noise.",
                    node_id=node_id,
                    field_name="usable_feature_columns",
                    expected=f"at least {thresholds.warn_samples_per_feature:.0f} rows per feature column",
                    actual=round(per_feature, 2),
                    fix_hint=(
                        "Widen the history range, or reduce the number of feature columns "
                        "feeding this model."
                    ),
                )
            )

    # -- 4. rows barely over the model's own requirement -----------------
    if required_rows > 0 and rows >= required_rows:
        headroom = float(rows) / float(required_rows)
        if headroom < thresholds.warn_row_headroom_ratio:
            warning.append(
                make_issue(
                    CODE_LIMITED_SAMPLES,
                    SEVERITY_WARNING,
                    f"{format_thousands(rows)} usable rows is only {headroom:.2f}x this "
                    f"model's minimum of {format_thousands(required_rows)}. Training will "
                    f"run, and the model may generalise poorly.",
                    node_id=node_id,
                    field_name="usable_rows",
                    expected=(
                        f"at least {format_thousands(int(required_rows * thresholds.warn_row_headroom_ratio))} "
                        f"rows for comfortable headroom"
                    ),
                    actual=rows,
                    fix_hint="Widen the history range, or use a shorter timeframe.",
                )
            )

    # -- 5. duplicate rows: fewer distinct examples than the count claims
    duplicates = measured.duplicate_feature_rows
    duplicate_ratio = _ratio(duplicates, rows)
    if (
        duplicates
        and duplicate_ratio is not None
        and duplicate_ratio > thresholds.warn_duplicate_row_ratio
    ):
        warning.append(
            make_issue(
                CODE_DUPLICATE_FEATURE_ROWS,
                SEVERITY_WARNING,
                f"{format_thousands(duplicates)} of {format_thousands(rows)} feature rows "
                f"({duplicate_ratio:.1%}) are exact duplicates of another row, so the "
                f"matrix holds fewer distinct examples than its row count suggests.",
                node_id=node_id,
                field_name="duplicate_feature_rows",
                expected=f"at or below {thresholds.warn_duplicate_row_ratio:.0%} duplicate rows",
                actual=duplicates,
                fix_hint=(
                    "Usually a quantised or thinly traded market. Consider a longer "
                    "timeframe, or a more liquid market."
                ),
            )
        )
    return blocking, warning


def _time_series_issues(
    measured: "MeasurementsView",
    thresholds: SufficiencyThresholds,
    node_id: Optional[str],
) -> "Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
    """``(blocking, warning)`` findings about chronological integrity.

    Every training run on this platform is a time-series run - the labels are forward
    returns and the splits are chronological - so these apply to every task.
    """
    blocking: List[Dict[str, Any]] = []
    warning: List[Dict[str, Any]] = []

    increasing = measured.index_strictly_increasing
    duplicates = measured.duplicate_timestamps or 0
    backwards = measured.non_monotonic_rows or 0

    if increasing is False:
        # The embargo that prevents a training row's label window reaching into the
        # validation split is measured in BARS along this index. If the index does not
        # advance monotonically, the embargo does not mean what it says, and the leakage
        # protection the whole split design rests on is not in force.
        blocking.append(
            make_issue(
                CODE_INDEX_NOT_CHRONOLOGICAL,
                SEVERITY_ERROR,
                f"The dataset's timestamps are not strictly increasing "
                f"({format_thousands(duplicates)} duplicate, "
                f"{format_thousands(backwards)} out of order), so chronological order "
                f"cannot be established and the embargo between splits would not hold. "
                f"Training is refused rather than run with leakage protection that does "
                f"not apply.",
                node_id=node_id,
                field_name="index_strictly_increasing",
                expected="strictly increasing timestamps",
                actual={"duplicate": duplicates, "out_of_order": backwards},
                fix_hint=(
                    "The training window normally de-duplicates timestamps before a "
                    "dataset is built, so this points at the market data feed. Check the "
                    "market and timeframe on the DATA block."
                ),
            )
        )
    elif duplicates:
        warning.append(
            make_issue(
                CODE_DUPLICATE_TIMESTAMPS,
                SEVERITY_WARNING,
                f"{format_thousands(duplicates)} duplicate timestamp(s) were measured in "
                f"the dataset index.",
                node_id=node_id,
                field_name="duplicate_timestamps",
                expected="no duplicate timestamps",
                actual=duplicates,
                fix_hint="Check the market data feed for repeated bars.",
            )
        )

    irregular = measured.irregular_interval_rows
    irregular_ratio = _ratio(irregular, measured.rows)
    if (
        irregular
        and irregular_ratio is not None
        and irregular_ratio > thresholds.warn_irregular_interval_ratio
    ):
        warning.append(
            make_issue(
                CODE_IRREGULAR_INTERVALS,
                SEVERITY_WARNING,
                f"{format_thousands(irregular)} of {format_thousands(measured.rows)} bars "
                f"({irregular_ratio:.1%}) do not follow the window's usual interval, so "
                f"the history has gaps. A lookback window measured in bars then spans a "
                f"different amount of time in different places.",
                node_id=node_id,
                field_name="irregular_interval_rows",
                expected=f"at or below {thresholds.warn_irregular_interval_ratio:.0%} irregular intervals",
                actual=irregular,
                fix_hint="Choose a market and timeframe with continuous history.",
            )
        )
    return blocking, warning


def check_ml_data_requirements(
    plan: Any,
    dataset_stats: Any,
    model_spec: Any,
    validation_cfg: Any = None,
    *,
    node_id: Optional[str] = None,
    feature_lookback: Optional[int] = None,
    measurements: Any = None,
    thresholds: Optional[SufficiencyThresholds] = None,
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
    measurements
        ``ml_dataset.DatasetMeasurements`` (or its ``to_dict()``), which unlocks the
        task-specific checks: class balance and per-split class representation for
        classification, target variance and cardinality for regression, chronological
        integrity, feature variance, duplicate rows. Defaults to whatever
        ``dataset_stats`` carries on its ``measurements`` field, so the training path
        only has to attach them once.

        **Absent is a reported state, not a silent pass.** With no measurements the
        dimension checks run exactly as they always did and one WARNING records that the
        task-specific ones did not, because "we did not check" must never render as
        "checked and fine".
    thresholds
        Where WARNING ends and BLOCKED begins. Defaults to
        :meth:`SufficiencyThresholds.from_env`.

    Returns
    -------
    :class:`GateVerdict` with ``required`` and ``available`` for both dimensions,
    ``issues`` (blocking), ``warnings`` (advisory), ``outcome`` and ``split_plan``.

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
    limits = thresholds if thresholds is not None else SufficiencyThresholds.from_env()
    measured = (
        MeasurementsView.coerce(measurements)
        if measurements is not None
        else stats.measured
    )

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

    overrides: Mapping[str, Any] = (
        validation_cfg if isinstance(validation_cfg, Mapping) else {}
    )
    if isinstance(validation_cfg, ValidationConfig):
        config = validation_cfg
    else:
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
    warnings: List[Dict[str, Any]] = []
    display = spec.display_name or spec.block_id
    task = resolve_task(spec, measured, overrides)

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

    # ══════════════════════════════════════════════════════════════════
    #  5 - 9: THE DATA-SUFFICIENCY ENGINE
    #
    #  Checks 1-4 above answer "is there enough data, of the right shape, for this
    #  architecture". They cannot answer "is this data trainable for this TASK",
    #  because a row count says nothing about whether every class is represented or
    #  whether the target moves. These do.
    #
    #  All collect-all, like 1-4: an author with a single-class target AND an
    #  infeasible split is told both at once.
    # ══════════════════════════════════════════════════════════════════

    # -- 5 the split geometry, computed rather than discovered -------------
    plan_for_splits = SplitPlan.compute(usable_rows, config)
    if not plan_for_splits.feasible:
        needed = rows_for_feasible_split(
            config, minimum_per_split=max(1, limits.min_class_rows_per_split)
        )
        issues.append(
            make_issue(
                CODE_SPLIT_INFEASIBLE,
                SEVERITY_ERROR,
                f"A {config.val_fraction:.0%} validation / {config.test_fraction:.0%} test "
                f"split with a {format_thousands(config.embargo_bars)}-bar embargo cannot "
                f"be produced from {format_thousands(usable_rows)} usable rows: it would "
                f"leave train={plan_for_splits.train}, val={plan_for_splits.val}, "
                f"test={plan_for_splits.test}. "
                f"{format_thousands(needed)} rows are needed for that geometry.",
                node_id=resolved_node_id,
                field_name="val_fraction",
                expected=needed or "a feasible split geometry",
                actual={
                    "rows": usable_rows,
                    "train": plan_for_splits.train,
                    "val": plan_for_splits.val,
                    "test": plan_for_splits.test,
                    "embargo_bars": config.embargo_bars,
                },
                fix_hint=(
                    "Widen the history range, reduce the validation and test fractions, "
                    "or shorten the feature lookback so the embargo is smaller."
                ),
            )
        )

    # -- 6 the task must be one this block can fit -------------------------
    if not task:
        warnings.append(
            make_issue(
                CODE_TASK_SUPPORT_UNKNOWN,
                SEVERITY_WARNING,
                f"The training task for '{display}' could not be determined, so the "
                f"task-specific data checks (class balance for classification, target "
                f"variance for regression) were not applied.",
                node_id=resolved_node_id,
                field_name="label_mode",
                expected="classification, regression or reconstruction",
                actual=None,
                fix_hint="Name 'label_mode' on the training configuration.",
            )
        )
    elif not spec.supports_task(task):
        issues.append(
            make_issue(
                CODE_TASK_UNSUPPORTED,
                SEVERITY_ERROR,
                f"'{display}' cannot be trained for a {task} task; it supports "
                f"{list(spec.supported_tasks)}. Fitting it on this target would produce a "
                f"model whose output does not mean what the graph expects.",
                node_id=resolved_node_id,
                field_name="label_mode",
                expected=list(spec.supported_tasks),
                actual=task,
                fix_hint=(
                    f"Set 'label_mode' to one of {list(spec.supported_tasks)}, or choose "
                    f"a model block that supports {task}."
                ),
            )
        )

    # -- 7 the measurements, or an honest statement that there are none ----
    if not measured.available:
        warnings.append(
            make_issue(
                CODE_SUFFICIENCY_NOT_MEASURED,
                SEVERITY_WARNING,
                "The dataset's labels, index and feature variance were not measured, so "
                "class balance, target variance, chronological integrity and split "
                "representation were NOT checked. The row and feature-column "
                "requirements were.",
                node_id=resolved_node_id,
                field_name="measurements",
                expected="measured label, index and feature statistics",
                actual=None,
                fix_hint=(
                    "This is reported, not refused. The training path measures the "
                    "dataset; a graph validated without one cannot be."
                ),
            )
        )
    else:
        # -- 8 chronological integrity, for every task --------------------
        blocked, warned = _time_series_issues(measured, limits, resolved_node_id)
        issues.extend(blocked)
        warnings.extend(warned)

        # -- 9 shape and quality, for every task --------------------------
        blocked, warned = _shape_and_quality_issues(
            spec, stats, measured, required_rows, limits, resolved_node_id
        )
        issues.extend(blocked)
        warnings.extend(warned)

        # -- 10 the task's own requirements -------------------------------
        if task == TASK_CLASSIFICATION:
            blocked, warned = _classification_issues(
                spec, measured, limits, resolved_node_id
            )
            issues.extend(blocked)
            warnings.extend(warned)
        elif task == TASK_REGRESSION:
            blocked, warned = _regression_issues(spec, measured, limits, resolved_node_id)
            issues.extend(blocked)
            warnings.extend(warned)
        # RECONSTRUCTION has no labels to balance and no target to check: its target IS
        # its input, which checks 8 and 9 already covered. Asserting the supervised
        # requirements here would refuse a trainable autoencoder for failing a
        # requirement that does not apply to it.

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

    # Deterministic order, for the same reason stage 11 sorts its issues: two processes
    # evaluating one dataset must produce one verdict, in one order.
    issues.sort(key=lambda issue: (str(issue.get("code") or ""), str(issue.get("field") or "")))
    warnings.sort(
        key=lambda issue: (str(issue.get("code") or ""), str(issue.get("field") or ""))
    )

    return GateVerdict(
        ok=not issues,
        issues=tuple(issues),
        required=required,
        available=available,
        block_id=spec.block_id,
        node_id=resolved_node_id,
        config=config,
        stats=stats,
        warnings=tuple(warnings),
        task=task,
        split_plan=plan_for_splits,
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
    #: The model spec's ``recommended_epochs``, bounded by the tier. Carried so a
    #: refusal can state Requirement 21's full triple - requested, recommended,
    #: approved - in one message instead of leaving the author to guess what a
    #: reasonable number would have been.
    recommended_epochs: int = 0
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
            "recommended_epochs": self.recommended_epochs,
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
        # The spec's own recommendation, lowered by the effective ceiling. Never raised
        # above it: advising a number the account cannot run would be advice it cannot
        # take.
        recommended_epochs=(
            0 if spec is None else min(int(spec.recommended_epochs), max_epochs)
        ),
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


# ---------------------------------------------------------------------------
# The adaptive training budget
# ---------------------------------------------------------------------------
#
# WHAT THIS IS NOT
# ----------------
# It is not `MAX_EPOCHS = 100`. A single epoch ceiling is wrong for every model at once:
# a random forest counts estimators, a boosting model counts rounds, an LSTM counts
# passes over a windowed matrix, and 100 of each costs a different amount and buys a
# different amount. The caps above are the plan's HARD bounds; this is the budget WITHIN
# them, derived per run from what the run actually is.
#
# THE FLOOR IS AS LOAD-BEARING AS THE CEILING
# -------------------------------------------
# Every adaptive rule here can only move `max_epochs` DOWN from the cap, and every one
# of them is floored at `min_meaningful_epochs`. That floor exists because the failure
# mode of a resource-governance layer is not "it allowed too much" - it is "it quietly
# allowed so little that every model underfits and the platform looks broken". A budget
# that returns three epochs for a transformer has not protected anything; it has made
# the feature useless while appearing to work.
#
# PLATFORM LOAD DELIBERATELY DOES NOT SHRINK THE BUDGET
# -----------------------------------------------------
# Saturation is handled by ADMISSION - `enforce_caps` returns DEFER and the job waits
# (Requirement 16.5). It is recorded on the budget for observability and it changes no
# figure. Letting load reduce the epoch ceiling would make a model's quality depend on
# what time its author clicked train, and two runs of one configuration would not be
# comparable. Queueing is the honest way to shed load; silently training a worse model
# is not.
#
# EVERY ESTIMATE SAYS IT IS ONE
# -----------------------------
# `estimated_seconds_per_epoch` is a throughput model, not a measurement, and it is
# named so. It exists for one reason: the wall-clock cap is enforced at every epoch
# boundary, so a configuration that cannot finish inside it will FAIL at minute 30 with
# `MAX_DURATION_EXCEEDED` after burning the whole budget. Telling the author up front
# that 400 epochs will not fit is strictly better than letting them find out. When the
# estimate is wrong, the worker's own clock is still the authority.


#: Cells (rows x columns x window) a worker is assumed to push through per second, for
#: the duration estimate only. Deliberately conservative - an over-estimate of cost
#: lowers the suggested epoch count, and the floor stops that from becoming harmful,
#: whereas an under-estimate lets a run be admitted that cannot finish.
#:
#: Env-tunable because it is a property of the deployment's worker hardware, not of a
#: plan or a model, and because Requirement 19's adaptive policy is meant to correct it
#: from observed history.
DEFAULT_EPOCH_THROUGHPUT_CELLS = _env_int("ML_TRAINING_EPOCH_CELLS_PER_SECOND", 4_000_000)

#: Per-family multiplier on the estimate. One "epoch" is not one unit of work across
#: families: a boosting round touches every row once and fits one shallow tree, while a
#: sequence epoch runs forward and backward passes over a windowed matrix.
FAMILY_COST_FACTOR: Mapping[str, float] = {
    FAMILY_TREE: 1.0,
    FAMILY_SEQUENCE: 3.0,
    FAMILY_AUTOENCODER: 2.0,
}

#: Fraction of the wall-clock cap a budget plans to use. The remainder absorbs the fetch,
#: the feature pipeline, the split scoring and the artifact write, none of which are
#: epochs but all of which are inside the same clock.
DURATION_PLANNING_HEADROOM = 0.80

#: Persisted artifacts one training job may produce. One: the best model. `model_versions`
#: carries at most one ACTIVE row per (version, node) by unique index, so a job that
#: wrote several would be writing rows nothing can resolve - and every one of them would
#: consume a user's storage quota.
DEFAULT_MAX_CHECKPOINTS = _env_int("ML_TRAINING_MAX_CHECKPOINTS", 1)


@dataclass(frozen=True)
class EarlyStoppingPolicy:
    """How a run decides it has finished learning, independently of its epoch ceiling.

    THE EPOCH CAP IS NOT THE OVERFITTING CONTROL
    --------------------------------------------
    Stopping a run because it hit an epoch number says nothing about whether the model
    generalises - it is a resource control that happens to also end training. These
    figures are the training-QUALITY control: a run stops when the validation metric
    stops improving, keeps the best weights it saw rather than the last ones, and the
    epoch ceiling is the safety net underneath that, not the mechanism.

    UNDERFITTING IS PROTECTED EXPLICITLY
    ------------------------------------
    `warmup_epochs` is a floor on when stopping may first fire and `min_epochs` is a
    floor on how many epochs must complete regardless. Without them a noisy first few
    epochs would stop a model that had not begun to learn, which is the mirror failure
    of overfitting and far easier to cause accidentally.
    """

    #: The metric the stop decision reads. `val_loss` when the trainer reports one;
    #: a trainer that reports no validation metric gets `loss` and a warning, because
    #: early stopping on TRAINING loss cannot detect overfitting at all - it only
    #: detects convergence.
    monitor: str = "val_loss"
    #: `"min"` for a loss, `"max"` for a score. Derived from the monitored name.
    mode: str = "min"
    #: Epochs without improvement before stopping.
    patience: int = 10
    #: Improvement smaller than this does not count as improvement. Relative to the
    #: best value seen, not absolute, so it means the same thing at any loss scale.
    min_delta: float = 1e-4
    #: No stop decision is taken before this epoch.
    warmup_epochs: int = 5
    #: Epochs that must complete whatever the monitor says.
    min_epochs: int = 5
    #: Restore the best epoch's weights before the artifact is written.
    restore_best: bool = True
    #: Monitored value worse than ``best * divergence_factor`` is treated as divergence
    #: and stops immediately, past warmup. Catches a diverging learning rate in a few
    #: epochs instead of spending the whole budget on it.
    divergence_factor: float = 4.0
    #: True when `monitor` is a training metric because no validation one is available.
    monitors_training_loss: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "monitor": self.monitor,
            "mode": self.mode,
            "patience": self.patience,
            "min_delta": self.min_delta,
            "warmup_epochs": self.warmup_epochs,
            "min_epochs": self.min_epochs,
            "restore_best": self.restore_best,
            "divergence_factor": self.divergence_factor,
            "monitors_training_loss": self.monitors_training_loss,
        }

    @classmethod
    def for_run(
        cls,
        spec: ModelSpecView,
        max_epochs: int,
        *,
        validation_available: bool = True,
    ) -> "EarlyStoppingPolicy":
        """Patience and warmup scaled to the run, not fixed constants.

        A 2,000-round boosting model and a 30-epoch LSTM cannot share a patience of 10:
        ten rounds is noise on the first and a third of the budget on the second. Both
        figures are therefore fractions of the model's own recommended count, with small
        absolute floors so a tiny recommendation does not produce a patience of zero.
        """
        recommended = max(1, int(spec.recommended_epochs or 0) or max_epochs or 1)
        ceiling = max(1, int(max_epochs or recommended))

        warmup = max(3, int(math.ceil(recommended * 0.10)))
        patience = max(5, int(math.ceil(recommended * 0.15)))

        # Never let the quality controls exceed the budget they run inside: a patience
        # longer than the ceiling can never fire, and a warmup longer than the ceiling
        # disables stopping entirely - both are silently-dead controls.
        warmup = min(warmup, max(1, ceiling // 2))
        patience = min(patience, max(1, ceiling))

        return cls(
            monitor="val_loss" if validation_available else "loss",
            mode="min",
            patience=patience,
            min_delta=1e-4,
            warmup_epochs=warmup,
            min_epochs=warmup,
            restore_best=True,
            divergence_factor=4.0,
            monitors_training_loss=not validation_available,
        )


@dataclass(frozen=True)
class TrainingBudget:
    """What one run is allowed, and what it is advised, within the plan's caps.

    ``requested`` / ``recommended`` / ``max_epochs`` is the triple Requirement 21 asks
    to be stated: what the author asked for, what the system suggests, and the ceiling
    it will accept. They are reported rather than reconciled - `enforce_caps` refuses an
    over-ceiling request naming both numbers instead of silently clamping it, because an
    author who asked for 5,000 epochs and got 400 without being told would read the
    result as 5,000 epochs of training.
    """

    block_id: str
    epoch_unit: str

    requested_epochs: int
    recommended_epochs: int
    max_epochs: int
    min_meaningful_epochs: int

    max_wall_clock_seconds: int
    max_memory_mb: int
    max_model_size_mb: int
    max_checkpoints: int
    checkpoint_every_epochs: int
    concurrent_jobs_allowed: int

    early_stopping: EarlyStoppingPolicy

    # -- the estimate, labelled as one -----------------------------------
    cells_per_epoch: int = 0
    estimated_seconds_per_epoch: float = 0.0
    estimated_total_seconds: int = 0
    estimated_memory_mb: int = 0
    #: The epoch ceiling the wall-clock cap alone implies. Reported even when it is not
    #: the binding constraint, so the readiness panel can explain WHY a ceiling moved.
    max_epochs_by_duration: int = 0
    #: True when `recommended_epochs` is expected to finish inside the wall clock.
    fits_recommended: bool = True

    # -- provenance -------------------------------------------------------
    plan: str = ""
    tier_max_epochs: int = 0
    model_max_safe_epochs: int = 0
    #: Platform load at resolution time. Recorded, never applied - see the section
    #: comment above.
    global_running: Optional[int] = None
    global_queued: Optional[int] = None
    warnings: Tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION

    @property
    def clamped_by_duration(self) -> bool:
        return 0 < self.max_epochs_by_duration < self.max_epochs_before_duration

    @property
    def max_epochs_before_duration(self) -> int:
        """The ceiling from the caps alone, i.e. before the duration estimate."""
        return min(
            self.tier_max_epochs or self.max_epochs,
            self.model_max_safe_epochs or self.max_epochs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "epoch_unit": self.epoch_unit,
            "requested_epochs": self.requested_epochs,
            "recommended_epochs": self.recommended_epochs,
            "max_epochs": self.max_epochs,
            "min_meaningful_epochs": self.min_meaningful_epochs,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "max_memory_mb": self.max_memory_mb,
            "max_model_size_mb": self.max_model_size_mb,
            "max_checkpoints": self.max_checkpoints,
            "checkpoint_every_epochs": self.checkpoint_every_epochs,
            "concurrent_jobs_allowed": self.concurrent_jobs_allowed,
            "early_stopping": self.early_stopping.to_dict(),
            "cells_per_epoch": self.cells_per_epoch,
            "estimated_seconds_per_epoch": self.estimated_seconds_per_epoch,
            "estimated_total_seconds": self.estimated_total_seconds,
            "estimated_memory_mb": self.estimated_memory_mb,
            "max_epochs_by_duration": self.max_epochs_by_duration,
            "fits_recommended": self.fits_recommended,
            "plan": self.plan,
            "tier_max_epochs": self.tier_max_epochs,
            "model_max_safe_epochs": self.model_max_safe_epochs,
            "global_running": self.global_running,
            "global_queued": self.global_queued,
            "warnings": list(self.warnings),
            "policy_version": self.policy_version,
        }


def cells_per_epoch(request: "TrainingRequest") -> int:
    """Feature values one epoch touches. The complexity proxy the estimate is built on.

    A sequence model's windowed view repeats each row ``sequence_length`` times, which
    is the difference between a 60-bar LSTM and a tree model on the same matrix - two
    orders of magnitude, and the reason a single epoch ceiling cannot serve both.
    """
    cells = max(0, int(request.rows)) * max(0, int(request.feature_columns))
    if request.is_sequence and request.sequence_length:
        cells *= max(1, int(request.sequence_length))
    return int(cells)


def estimate_seconds_per_epoch(
    request: "TrainingRequest",
    spec: ModelSpecView,
    *,
    throughput_cells_per_second: Optional[int] = None,
    observed_seconds_per_epoch: Optional[float] = None,
) -> float:
    """Estimated wall-clock seconds for one epoch. **An estimate, never a measurement.**

    ``observed_seconds_per_epoch`` wins outright when supplied: a figure measured from
    this configuration's own history is better than any throughput model, and it is the
    seam Requirement 19's adaptive policy plugs into. It is clamped to a sane range by
    the caller rather than trusted raw.
    """
    if observed_seconds_per_epoch is not None and observed_seconds_per_epoch > 0:
        return float(observed_seconds_per_epoch)

    throughput = int(
        throughput_cells_per_second
        if throughput_cells_per_second is not None
        else DEFAULT_EPOCH_THROUGHPUT_CELLS
    )
    if throughput <= 0:
        throughput = 1
    factor = FAMILY_COST_FACTOR.get(
        str(_enum_value(spec.model_family)).upper(), 1.0
    )
    seconds = (float(cells_per_epoch(request)) / float(throughput)) * factor
    # A floor, because no epoch takes zero time: framework overhead, the data handoff
    # and the metric computation are real even on a tiny matrix. Without it a small
    # dataset would appear to support an unbounded epoch count.
    return max(0.01, seconds)


def resolve_budget(
    request: "TrainingRequest",
    caps: MLTrainingCaps,
    spec: ModelSpecView,
    *,
    job_counts: Optional[JobCounts] = None,
    validation_available: bool = True,
    observed_seconds_per_epoch: Optional[float] = None,
    throughput_cells_per_second: Optional[int] = None,
) -> TrainingBudget:
    """The budget for ``request`` within ``caps``. Adaptive, bounded, and floored.

    Order, and why it is this order:

    1. **The caps' ceiling** is the starting point. Nothing here raises it - the whole
       caps design turns on an entitlement widening a limit within a ceiling it cannot
       lift, and a budget that could exceed `caps.max_epochs` would defeat that.
    2. **The duration estimate** may lower it, because the wall-clock cap is enforced at
       every epoch boundary and a ceiling that cannot be reached inside it is a
       `MAX_DURATION_EXCEEDED` failure waiting to happen.
    3. **The floor** may raise it back. ``min_meaningful_epochs`` is the lesser of the
       model's own recommendation and the plan's ceiling, so the budget can never fall
       below what the spec calls a reasonable run unless the PLAN says so - a plan
       decision is legitimate, an adaptive one is not.

    Postconditions
        ``min_meaningful_epochs <= max_epochs <= caps.max_epochs`` whenever
        ``caps.max_epochs > 0``; ``max_epochs == 0`` exactly when the plan grants no
        training at all, which `enforce_caps` then refuses with the upgrade message.
    """
    warnings: List[str] = list(caps.warnings)
    cap_ceiling = max(0, int(caps.max_epochs))

    recommended = max(1, int(spec.recommended_epochs or 0))
    # The recommendation is itself bounded by the plan: suggesting 3,000 rounds to an
    # account whose ceiling is 300 would be advice it cannot take.
    recommended = min(recommended, cap_ceiling) if cap_ceiling else 0

    cells = cells_per_epoch(request)
    seconds_per_epoch = estimate_seconds_per_epoch(
        request,
        spec,
        throughput_cells_per_second=throughput_cells_per_second,
        observed_seconds_per_epoch=observed_seconds_per_epoch,
    )

    wall_clock = max(0, int(caps.max_duration_seconds))
    planning_seconds = wall_clock * DURATION_PLANNING_HEADROOM
    by_duration = (
        int(math.floor(planning_seconds / seconds_per_epoch)) if wall_clock > 0 else 0
    )

    # The floor. Never below the model's own recommendation, and never above the plan's
    # ceiling - so a tight wall clock cannot reduce a legitimate run to a token one.
    min_meaningful = min(recommended, cap_ceiling) if cap_ceiling else 0

    approved = cap_ceiling
    if cap_ceiling > 0 and wall_clock > 0 and by_duration < cap_ceiling:
        approved = max(min_meaningful, by_duration)
        if by_duration < min_meaningful:
            warnings.append(
                f"This configuration is estimated at {seconds_per_epoch:.2f}s per "
                f"{spec.epoch_unit[:-1] if spec.epoch_unit.endswith('s') else spec.epoch_unit}, "
                f"so the {wall_clock}s training time limit on this plan allows about "
                f"{by_duration} of them - fewer than the {min_meaningful} this model "
                f"normally needs. Training is still allowed up to {min_meaningful}, and "
                f"may stop on the time limit before finishing. Reduce the history range "
                f"or the number of feature columns, or upgrade for a longer limit."
            )

    fits_recommended = bool(
        wall_clock <= 0 or by_duration >= recommended or recommended == 0
    )
    estimated_total = int(math.ceil(seconds_per_epoch * max(0, approved)))

    checkpoints = max(1, int(DEFAULT_MAX_CHECKPOINTS))
    checkpoint_every = max(1, int(math.ceil(max(1, approved) / checkpoints)))

    counts = job_counts if job_counts is not None else None
    return TrainingBudget(
        block_id=spec.block_id,
        epoch_unit=spec.epoch_unit,
        requested_epochs=int(request.epochs),
        recommended_epochs=int(recommended),
        max_epochs=int(approved),
        min_meaningful_epochs=int(min_meaningful),
        max_wall_clock_seconds=wall_clock,
        max_memory_mb=int(caps.max_memory_mb),
        max_model_size_mb=int(caps.max_model_size_mb),
        max_checkpoints=checkpoints,
        checkpoint_every_epochs=checkpoint_every,
        concurrent_jobs_allowed=int(caps.max_concurrent_jobs_per_user),
        early_stopping=EarlyStoppingPolicy.for_run(
            spec, approved, validation_available=validation_available
        ),
        cells_per_epoch=cells,
        estimated_seconds_per_epoch=round(seconds_per_epoch, 4),
        estimated_total_seconds=estimated_total,
        estimated_memory_mb=estimate_memory_mb(request),
        max_epochs_by_duration=int(by_duration),
        fits_recommended=fits_recommended,
        plan=caps.plan,
        tier_max_epochs=int(caps.tier_max_epochs),
        model_max_safe_epochs=int(caps.model_max_safe_epochs),
        global_running=None if counts is None or not counts.available else counts.global_running,
        global_queued=None if counts is None or not counts.available else counts.global_queued,
        warnings=tuple(dict.fromkeys(warnings)),
    )


# ---------------------------------------------------------------------------
# Parameter-search (HPO) governance
# ---------------------------------------------------------------------------
#
# THE BYPASS THIS CLOSES
# ----------------------
# Every per-run cap in this module bounds ONE run. A parameter search is N runs, and
# until now the only bound on N was ``OptimizationRequest.n_iterations``'s
# ``le=1000`` - a Pydantic field validator, which is to say the client's own number with
# an upper bound nobody chose on purpose. So a caller entitled to a 1,800-second
# training job could spend 1,000 backtests in one request and consume three orders of
# magnitude more compute than any single-run cap permits, while every individual trial
# looked perfectly valid.
#
# WHAT IS NOT DUPLICATED HERE
# ---------------------------
# How many SEARCHES a month an account may run is already decided and already enforced:
# ``Resource.OPTIMIZATIONS`` in ``SubscriptionEngine`` (Free 0, Trader 25, Pro Quant 100,
# Business 400) metered by ``check_optimization_quota``. Nothing below re-decides that.
# What is missing - and what this adds - is a bound on how much compute ONE search may
# spend, which no existing table carries.
#
# WHY A SEPARATE TIER TABLE RATHER THAN REUSING ML_TIER_CAPS
# ---------------------------------------------------------
# Because the entitlements genuinely differ. ``ML_TIER_CAPS`` is zero for BASIC, because
# Trader includes no ML training - but Trader DOES include 25 optimizations a month.
# Deriving a search budget from the ML wall clock would give an entitled Trader account
# zero seconds of search, which is a refusal of something they paid for.


@dataclass(frozen=True)
class TierSearchCaps:
    """One plan's allowance for a SINGLE parameter search.

    FREE is zero because Free carries no ``optimization`` feature and no
    ``optimizations`` allowance, so ``require_optimization`` already refuses it. Zeroing
    here makes this layer agree with the gate above it rather than quietly permitting
    what that gate rejects - the same fail-closed direction :data:`ML_TIER_CAPS` takes.
    """

    #: Trials (backtests) one search may evaluate.
    max_trials: int
    #: Trials that may run at once. Searches are currently sequential, so this is a
    #: ceiling for a future concurrent implementation rather than a live throttle, and it
    #: is recorded on the budget so that implementation inherits a bound instead of
    #: choosing one.
    max_concurrent_trials: int
    #: Wall clock the WHOLE search may spend, in seconds. This is the figure that makes
    #: "10 trials x 2 hours" refusable: each trial is cheap, the aggregate is not.
    max_total_seconds: int


#: Per-plan single-search allowances, keyed on the existing ``tenant.TenantPlan``.
#:
#: The trial counts are deliberately NOT the monthly optimization allowances they sit
#: next to - those bound how many searches, these bound how big one search is.
ML_TIER_SEARCH_CAPS: Mapping[TenantPlan, TierSearchCaps] = {
    TenantPlan.FREE: TierSearchCaps(
        max_trials=0, max_concurrent_trials=0, max_total_seconds=0
    ),
    TenantPlan.BASIC: TierSearchCaps(
        max_trials=50, max_concurrent_trials=1, max_total_seconds=1_800
    ),
    TenantPlan.PROFESSIONAL: TierSearchCaps(
        max_trials=200, max_concurrent_trials=2, max_total_seconds=3_600
    ),
    TenantPlan.ENTERPRISE: TierSearchCaps(
        max_trials=500, max_concurrent_trials=4, max_total_seconds=10_800
    ),
}

#: Seconds one trial is assumed to cost, for the aggregate estimate only. A trial is a
#: full backtest over the configured window, so this is deployment- and
#: strategy-dependent and is env-tunable for that reason. Conservative on purpose: an
#: over-estimate lowers the permitted trial count, and the floor below stops that from
#: refusing an entitled account outright.
DEFAULT_SEARCH_SECONDS_PER_TRIAL = _env_ratio(
    "ML_SEARCH_SECONDS_PER_TRIAL", 8.0, low=0.01, high=3_600.0
)

#: Trials an entitled plan may always run, whatever the duration estimate says. The
#: same floor principle as :attr:`TrainingBudget.min_meaningful_epochs`: a search of one
#: trial is not a search, so an estimate that pessimistic must not silently reduce a
#: paid feature to a single backtest. It is reported as a warning instead.
MIN_MEANINGFUL_SEARCH_TRIALS = _env_count(
    "ML_SEARCH_MIN_TRIALS", 10, low=1, high=1_000
)

CAP_MAX_SEARCH_TRIALS = "MAX_SEARCH_TRIALS"
CAP_MAX_SEARCH_SECONDS = "MAX_SEARCH_SECONDS"


@dataclass(frozen=True)
class SearchBudget:
    """What one parameter search is allowed, and what it asked for."""

    plan: str
    requested_trials: int
    max_trials: int
    min_meaningful_trials: int
    max_concurrent_trials: int
    max_total_seconds: int
    estimated_seconds_per_trial: float
    estimated_total_seconds: int
    #: The trial ceiling the aggregate wall clock alone implies. Reported even when it
    #: is not binding, so a refusal can explain WHICH limit moved.
    max_trials_by_duration: int
    warnings: Tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION

    @property
    def entitled(self) -> bool:
        return self.max_trials > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan": self.plan,
            "requested_trials": self.requested_trials,
            "max_trials": self.max_trials,
            "min_meaningful_trials": self.min_meaningful_trials,
            "max_concurrent_trials": self.max_concurrent_trials,
            "max_total_seconds": self.max_total_seconds,
            "estimated_seconds_per_trial": self.estimated_seconds_per_trial,
            "estimated_total_seconds": self.estimated_total_seconds,
            "max_trials_by_duration": self.max_trials_by_duration,
            "warnings": list(self.warnings),
            "policy_version": self.policy_version,
        }


def resolve_search_budget(
    requested_trials: int,
    user: Any = None,
    *,
    plan: Any = None,
    tenant: Any = None,
    seconds_per_trial: Optional[float] = None,
) -> SearchBudget:
    """The budget for one parameter search. Narrows, floors, and never raises.

    ``requested_trials`` is what the client asked for - for a grid search that is the
    iteration cap, for a random or Bayesian search the trial count. Both are the same
    quantity as far as cost is concerned: the number of backtests that will run.

    Postconditions
        ``min_meaningful_trials <= max_trials <= tier.max_trials`` for an entitled
        plan; ``max_trials == 0`` exactly when the plan includes no optimization, which
        :func:`enforce_search_budget` then refuses with the upgrade message.
    """
    warnings: List[str] = []

    plan_source: Any = plan
    if plan_source is None and tenant is not None:
        plan_source = getattr(tenant, "plan", None)
    if plan_source is None:
        plan_source = _plan_name_from_user(user)
    tenant_plan, plan_warnings = _tenant_plan_of(plan_source)
    warnings.extend(plan_warnings)

    tier = ML_TIER_SEARCH_CAPS.get(tenant_plan) or ML_TIER_SEARCH_CAPS[TenantPlan.FREE]
    per_trial = float(
        seconds_per_trial
        if seconds_per_trial is not None and seconds_per_trial > 0
        else DEFAULT_SEARCH_SECONDS_PER_TRIAL
    )

    by_duration = (
        int(math.floor(tier.max_total_seconds / per_trial))
        if tier.max_total_seconds > 0 and per_trial > 0
        else 0
    )

    if tier.max_trials <= 0:
        approved = 0
        floor_trials = 0
    else:
        floor_trials = min(MIN_MEANINGFUL_SEARCH_TRIALS, tier.max_trials)
        approved = min(tier.max_trials, by_duration) if by_duration > 0 else tier.max_trials
        if approved < floor_trials:
            approved = floor_trials
            warnings.append(
                f"A trial is estimated at {per_trial:.1f}s, so the {tier.max_total_seconds}s "
                f"search time limit on this plan allows about {by_duration} of them - "
                f"fewer than the {floor_trials} a search needs to be worth running. Up to "
                f"{floor_trials} are allowed and the search may stop on the time limit "
                f"before finishing. Narrow the parameter space or the backtest window, or "
                f"upgrade for a longer limit."
            )

    return SearchBudget(
        plan=tenant_plan.value,
        requested_trials=max(0, int(requested_trials)),
        max_trials=int(approved),
        min_meaningful_trials=int(floor_trials),
        max_concurrent_trials=int(tier.max_concurrent_trials),
        max_total_seconds=int(tier.max_total_seconds),
        estimated_seconds_per_trial=round(per_trial, 3),
        estimated_total_seconds=int(math.ceil(per_trial * max(0, int(requested_trials)))),
        max_trials_by_duration=int(by_duration),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def enforce_search_budget(budget: SearchBudget) -> None:
    """Refuse a search that exceeds its budget. Raises or returns ``None``.

    Refuses rather than clamping, for the same reason the epoch cap does: a caller who
    asked for 1,000 trials and silently got 200 would read the best-of-200 result as a
    best-of-1,000 result, and act on a search that was four fifths smaller than they
    believe. The refusal names the requested value and the permitted one.

    Raises
        :class:`CapExceeded` with :data:`CAP_MAX_SEARCH_TRIALS`.
    """
    if budget.requested_trials > budget.max_trials:
        raise CapExceeded(
            CAP_MAX_SEARCH_TRIALS,
            budget.requested_trials,
            budget.max_trials,
            unit="trials",
            plan=budget.plan,
            fix_hint=(
                (
                    f"Reduce the search to {budget.max_trials} trials or fewer. The whole "
                    f"search is bounded to {budget.max_total_seconds}s of compute on this "
                    f"plan, at roughly {budget.estimated_seconds_per_trial:.1f}s per trial."
                )
                if budget.entitled
                else "This plan includes no parameter optimization; upgrade to run searches."
            ),
        )


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
        # Requirement 21's triple, in the refusal itself: what was asked for, what the
        # model normally needs, and what this plan permits. An author told only
        # "allowed 300" has to guess whether 300 is a lot or a little for this model;
        # told "recommended 200, allowed 300" they can act immediately.
        raise CapExceeded(
            CAP_MAX_EPOCHS,
            request.epochs,
            caps.max_epochs,
            unit=caps.epoch_unit,
            block_id=caps.block_id,
            plan=caps.plan,
            fix_hint=(
                (
                    f"Reduce {caps.epoch_unit} to {caps.max_epochs} or fewer. "
                    f"This model's recommended figure is {caps.recommended_epochs} "
                    f"{caps.epoch_unit}."
                )
                if caps.max_epochs > 0 and caps.recommended_epochs > 0
                else f"Reduce {caps.epoch_unit} to {caps.max_epochs} or fewer."
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
        # Blocking findings AND advisory ones. The validator's own report separates them
        # by the `severity` field every issue carries, which is how every other stage
        # already distinguishes an error from a warning - so a data-sufficiency concern
        # reaches the author's validation panel instead of only the training response.
        # `stats` carries the measurements when the training path attached them
        # (`DatasetStats.with_measurements`), so this stage runs the task-specific checks
        # on the save path too, and reports SUFFICIENCY_NOT_MEASURED when it cannot.
        issues.extend(verdict.issues)
        issues.extend(verdict.warnings)

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
