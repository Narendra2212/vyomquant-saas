# -*- coding: utf-8 -*-
"""
backend/training_worker.py - the training worker: claim, run, and end truthfully.

``design.md`` -> Training workflow -> ``PROCEDURE run_training_job(job_id)``, and spec
task 6.4. Requirements 15.7, 15.8, 15.9, 15.10, 16.4, 16.6.

WHAT THIS MODULE OWNS
---------------------
The **control plane of one training run**, and nothing else:

1. :func:`claim_training_job` - the atomic ``QUEUED -> RUNNING`` transition, plus the
   first heartbeat.
2. :func:`recheck_caps` - the caps, re-evaluated **before the first epoch**
   (Requirement 16.4), against live global counts this process can actually measure.
3. :func:`run_training_job` - the epoch loop, under :class:`DeterministicEnforcer`'s
   seeded context, with :class:`MemoryMonitor` bounds and
   :class:`TrainingIsolator`-mediated fitting (Requirement 16.6).
4. The four terminal transitions, each of which sets ``updated_at`` itself:
   ``CANCELLED`` at an epoch boundary (15.7), ``FAILED`` +
   ``MAX_DURATION_EXCEEDED`` when the wall clock runs out (15.8), ``FAILED`` +
   ``WORKER_LOST`` when a heartbeat goes stale (15.9, :func:`reap_stale_jobs`), and
   ``FAILED`` + a **classified** reason for everything else (15.10).

WHAT THIS MODULE DELIBERATELY DOES NOT OWN
------------------------------------------
The fit itself and the artifact. ``model_versions`` rows, artifact bytes, checksums,
serialization modes, hyperparameter records and the ``READY`` transition are task
6.5's, and this module reaches them through **one named seam**:
:class:`TrainingBackend`, installed with :func:`register_training_backend`.

Until that seam is installed the worker still does every job it owns - it claims, it
re-checks the caps, it rebuilds the dataset, it observes cancellation - and then ends
the job ``FAILED`` with the classified reason ``TRAINER_UNAVAILABLE`` or
``MODEL_PERSISTENCE_UNAVAILABLE``. It does **not** write ``COMPLETED``, because the
design's own ordering puts ``set_status(job, COMPLETED)`` *after*
``insert_model_version`` and ``bind_model_to_version_node``: a job reported complete
with no model bound would be the silent-success shape Requirement 15.10 exists to
prevent. See "THE 6.5 SEAM" below.

REUSED, NOT REBUILT
-------------------
Nothing here re-implements a rule another surface owns:

* the gate, the caps and their intersection    -> ``ml_training_policy``
* the dataset, the splits and the embargo      -> ``ml_dataset``
* the window, its quality grading, the feature
  pipeline, the schema check, the fingerprint
  and the ``training_jobs`` vocabulary          -> ``strategy_service`` (task 6.3)
* the plan for a stored version                -> ``strategy_compiler.load_plan``
* determinism, memory bounds, isolation        -> ``core.ml_safety``
* the user's plan                              -> ``core.subscription_dependencies.get_user_plan``
* the realtime hand-off                        -> ``strategy_service.publish_training_event``

Every one of those is reached **through the module object** (``S.fetch_training_bars``,
not ``from ... import fetch_training_bars``), so the seams task 6.3 built stay the same
seams here and a test that replaces one replaces it for both.

THE AUTHORITATIVE QUEUE IS THE ROW
----------------------------------
``TRAINING_QUEUE_NAME`` in Redis is a wake-up hint and is treated as one: a popped
message is only a *suggestion* that a job id might be claimable, and
:meth:`TrainingWorker.process_iteration` falls back to scanning ``QUEUED`` rows when
Redis is empty or absent. The claim itself is a **conditional UPDATE against the
table** - ``SET status='RUNNING' ... WHERE id = :id AND status = 'QUEUED'`` - so two
workers that both saw the same hint cannot both run the job: PostgreSQL takes the row
lock, and the loser re-evaluates ``status = 'QUEUED'`` after the lock and matches no
rows. Nothing here trusts Redis for exclusion.

``updated_at`` IS WRITTEN BY HAND, EVERY TIME
---------------------------------------------
Migration ``004d_training_and_models.sql`` deliberately attaches **no**
``BEFORE UPDATE`` trigger to ``training_jobs`` - its header says so, and task 6.1's
report repeats it. So every status transition, every heartbeat and every epoch write in
this module sets ``updated_at`` explicitly. :func:`_transition` is the single writer for
status changes precisely so that column cannot be forgotten in one branch.

THE TABLES ARE NOT APPLIED
--------------------------
``004d_training_and_models.sql`` is unapplied in this environment and there is no local
PostgreSQL. Every function here that touches ``training_jobs`` or ``strategy_versions``
therefore degrades with a warning **naming that file** and returns an honest "nothing
happened" rather than raising: a worker that crashed on a missing relation would crash
on every poll. :data:`TRAINING_MIGRATION` is ``strategy_service``'s own constant, so
there is one spelling of that filename in the codebase.

metrics_history HOLDS SCALARS, AND THAT IS ENFORCED HERE
--------------------------------------------------------
004d's header states plainly that SQL cannot enforce the data-minimisation rule on
``training_jobs.metrics_history`` - "the enforcement itself belongs to the writer in
tasks 6.4 and 6.6". This module is that writer. :func:`sanitize_epoch_metrics` admits
only real scalars, drops anything else with a warning, and refuses the forbidden key
names outright, so a trainer that returned a prediction vector cannot put a user's
model output into a document that is served over the API and pushed over realtime
frames.

THE 6.5 SEAM
------------
::

    from backend_app.backend.training_worker import (
        TrainingBackend, register_training_backend,
    )

    register_training_backend(TrainingBackend(
        name="phase6-models",
        trainer=lambda ctx: MyEpochTrainer(ctx),   # per-epoch fitting
        binder=lambda ctx, model: {...},           # model_versions row + artifact
    ))

``trainer(ctx)`` returns an :class:`EpochTrainer`; the worker drives it and owns every
boundary decision. ``binder(ctx, model)`` persists the artifact, inserts the
``model_versions`` row, binds it to ``(version_id, node_id)`` and returns what it wrote.
Only when ``binder`` returns does this module write ``COMPLETED``.

A KNOWN, DELIBERATELY UNRELAXED STRICTNESS
------------------------------------------
``market_data_validation.OutlierDetector._z_score_filter`` REJECTS a window containing
any close beyond 3 sigma, and a real multi-thousand-bar market series is near-certain to
contain one. That component is REUSED AS-IS and its threshold is **not** relaxed from
here, for the same reason task 6.3 gave: relaxing a data-integrity rule from one caller
is worse than the strictness. The rejection arrives as
``strategy_service.TrainingBlocked(DATA_QUALITY, ...)`` and :func:`classify_failure`
maps it to the classified failure reason ``DATA_QUALITY`` carrying the validator's own
message - never a 500, never a generic string, and never a silently retried window.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

from backend_app.backend import strategy_service as S

logger = logging.getLogger("TrainingWorker")

#: Bumped when a transition, a failure reason or the seam contract changes, so a stored
#: job can be told apart from one a later worker produced.
TRAINING_WORKER_VERSION = "1.0.0"

__all__ = [
    "TRAINING_WORKER_VERSION",
    # vocabulary
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_CANCELLED",
    "FAILURE_REASONS",
    "FAILURE_MAX_DURATION_EXCEEDED",
    "FAILURE_WORKER_LOST",
    "FAILURE_CAP_EXCEEDED",
    "FAILURE_MEMORY_EXCEEDED",
    "FAILURE_DATA_QUALITY",
    "FAILURE_DATA_UNAVAILABLE",
    "FAILURE_DATA_SOURCE_UNRESOLVED",
    "FAILURE_FEATURES",
    "FAILURE_DATASET",
    "FAILURE_ML_REQUIREMENTS",
    "FAILURE_MODEL_UNPUBLISHED",
    "FAILURE_VERSION_UNAVAILABLE",
    "FAILURE_TRAINER_UNAVAILABLE",
    "FAILURE_MODEL_PERSISTENCE_UNAVAILABLE",
    "FAILURE_TRAINING_RUNTIME_ERROR",
    "WORKER_LOST_LIFECYCLE_STATE",
    "FAILED_LIFECYCLE_STATE",
    # errors
    "TrainingWorkerError",
    "MaxDurationExceeded",
    "MemoryBoundExceeded",
    "TrainerUnavailable",
    "ModelPersistenceUnavailable",
    "VersionUnavailable",
    "UnclassifiedFailureReason",
    # the 6.5 seam
    "EpochTrainer",
    "TrainingBackend",
    "register_training_backend",
    "current_training_backend",
    "reset_training_backend",
    # configuration
    "heartbeat_interval_seconds",
    "heartbeat_stale_seconds",
    "isolation_deadline_seconds",
    "reset_isolation_latch",
    "assert_memory_bounds",
    "run_isolated",
    "TrainingInputs",
    "next_queued_job_id",
    "pop_queue_hint",
    "new_worker_id",
    "set_version_lifecycle",
    "parse_timestamp",
    "elapsed_seconds",
    # units of work
    "assert_classified_reason",
    "classify_failure",
    "sanitize_epoch_metrics",
    "claim_training_job",
    "release_training_job",
    "write_heartbeat",
    "record_epoch",
    "recheck_caps",
    "reap_stale_jobs",
    "count_all_training_jobs",
    "resolve_job_user",
    "load_version_row",
    "rebuild_training_inputs",
    "TrainingContext",
    "TrainingRunResult",
    "run_training_job",
    "TrainingWorker",
    "run_worker",
]


# ══════════════════════════════════════════════════════════════════════════
#  1. VOCABULARY
#
#  Every status is ``strategy_service``'s (which is ``chk_tj_status``'s, verbatim) and
#  every failure reason is a member of a CLOSED set. Requirement 15.10 asks for a
#  classified reason and not a generic string, so :func:`assert_classified_reason`
#  makes an unclassified one unwritable rather than merely discouraged.
# ══════════════════════════════════════════════════════════════════════════

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

#: The table and the migration that creates it - ``strategy_service``'s constants, so
#: this module cannot disagree with task 6.3 about either spelling.
TRAINING_JOBS_TABLE = S.TRAINING_JOBS_TABLE
TRAINING_MIGRATION = S.TRAINING_MIGRATION
STRATEGY_VERSIONS_TABLE = "strategy_versions"

# -- the two reasons the requirements name by string ------------------------
#: Requirement 15.8, spelled exactly as the requirement spells it.
FAILURE_MAX_DURATION_EXCEEDED = "MAX_DURATION_EXCEEDED"
#: Requirement 15.9, likewise.
FAILURE_WORKER_LOST = "WORKER_LOST"

# -- the admission vocabulary, reused rather than restated ------------------
# A job can fail on the worker for the same measured reason the API would have
# blocked it for (the window degraded between admission and execution, the caps
# tightened, the feed went away). Reusing task 6.3's REASON_* constants means the
# builder renders one vocabulary, not two that drift.
FAILURE_CAP_EXCEEDED = S.REASON_CAP_EXCEEDED
FAILURE_DATA_QUALITY = S.REASON_DATA_QUALITY
FAILURE_DATA_UNAVAILABLE = S.REASON_DATA_UNAVAILABLE
FAILURE_DATA_SOURCE_UNRESOLVED = S.REASON_DATA_SOURCE
FAILURE_FEATURES = S.REASON_FEATURES
FAILURE_DATASET = S.REASON_DATASET
FAILURE_ML_REQUIREMENTS = S.REASON_ML_REQUIREMENTS
FAILURE_MODEL_UNPUBLISHED = S.REASON_MODEL_UNPUBLISHED

# -- reasons this module is the first to need -------------------------------
#: The memory bound ``MemoryMonitor`` enforces at runtime tripped. The pre-flight
#: estimate in ``ml_training_policy.estimate_memory_mb`` says it is an estimate; this
#: is the measurement.
FAILURE_MEMORY_EXCEEDED = "MEMORY_EXCEEDED"
#: The job's ``strategy_versions`` row could not be read, or carries no loadable graph,
#: so there is nothing to train. Distinct from a data problem.
FAILURE_VERSION_UNAVAILABLE = "VERSION_UNAVAILABLE"
#: No :class:`TrainingBackend` is installed, so no epoch could be fitted. Task 6.5.
FAILURE_TRAINER_UNAVAILABLE = "TRAINER_UNAVAILABLE"
#: Every epoch ran, but the artifact and the ``model_versions`` row could not be
#: written, so no model is bound and the run produced nothing deployable. Task 6.5.
FAILURE_MODEL_PERSISTENCE_UNAVAILABLE = "MODEL_PERSISTENCE_UNAVAILABLE"
#: The classified catch-all. Still a classification, not a message: the exception's
#: TYPE is recorded beside it, and the free-text detail never becomes the reason.
FAILURE_TRAINING_RUNTIME_ERROR = "TRAINING_RUNTIME_ERROR"

#: The closed set. ``failure_reason`` is written from this and from nothing else.
FAILURE_REASONS: frozenset = frozenset(
    {
        FAILURE_MAX_DURATION_EXCEEDED,
        FAILURE_WORKER_LOST,
        FAILURE_CAP_EXCEEDED,
        FAILURE_MEMORY_EXCEEDED,
        FAILURE_DATA_QUALITY,
        FAILURE_DATA_UNAVAILABLE,
        FAILURE_DATA_SOURCE_UNRESOLVED,
        FAILURE_FEATURES,
        FAILURE_DATASET,
        FAILURE_ML_REQUIREMENTS,
        FAILURE_MODEL_UNPUBLISHED,
        FAILURE_VERSION_UNAVAILABLE,
        FAILURE_TRAINER_UNAVAILABLE,
        FAILURE_MODEL_PERSISTENCE_UNAVAILABLE,
        FAILURE_TRAINING_RUNTIME_ERROR,
    }
)

#: Where a stale-heartbeat job leaves its version. Requirement 15.9 says "its
#: pre-training lifecycle state" and ``design.md``'s failure-mode table names that
#: state ``SAVED`` - "job marked FAILED with WORKER_LOST; version returns to SAVED".
#: Task 6.4's own text repeats ``SAVED``, so that is what is written.
WORKER_LOST_LIFECYCLE_STATE = "SAVED"

#: Where an in-worker failure or a cancellation leaves its version. ``design.md``'s
#: ``run_training_job`` CATCH block writes ``set_state(job.version, VALIDATED)``, and
#: that is a different sentence from the failure table's ``SAVED`` above. Both are
#: un-deployable pre-training states, so the difference is cosmetic; each is written
#: where its own source specifies it rather than one being silently preferred.
FAILED_LIFECYCLE_STATE = "VALIDATED"

#: Keys admitted into ``metrics_history``. See the module docstring: 004d records the
#: data-minimisation rule in a COMMENT and hands the enforcement to this writer.
EPOCH_METRIC_KEYS: Tuple[str, ...] = (
    "epoch",
    "loss",
    "val_loss",
    "metric",
    "val_metric",
    "learning_rate",
    "duration_seconds",
)

#: Names that must never reach ``metrics_history`` whatever their value looks like. A
#: single float under the key ``predictions`` is still a model output leaking into a
#: served document.
FORBIDDEN_METRIC_KEYS: frozenset = frozenset(
    {
        "predictions",
        "prediction",
        "y_pred",
        "y_true",
        "features",
        "feature_values",
        "X",
        "y",
        "probabilities",
        "proba",
        "logits",
        "embeddings",
        "residuals",
    }
)


# ══════════════════════════════════════════════════════════════════════════
#  2. ERRORS
# ══════════════════════════════════════════════════════════════════════════


class TrainingWorkerError(Exception):
    """Base class for the refusals this module raises on its own behalf."""


class MaxDurationExceeded(TrainingWorkerError):
    """The job outran its permitted wall clock (Requirement 15.8)."""

    def __init__(self, elapsed_seconds: float, allowed_seconds: int) -> None:
        self.elapsed_seconds = float(elapsed_seconds)
        self.allowed_seconds = int(allowed_seconds)
        super().__init__(
            f"elapsed {self.elapsed_seconds:.1f}s exceeds the permitted "
            f"{self.allowed_seconds}s"
        )


class MemoryBoundExceeded(TrainingWorkerError):
    """``MemoryMonitor`` refused the assembled tensor or the batch size."""


class TrainerUnavailable(TrainingWorkerError):
    """No :class:`TrainingBackend` could produce an :class:`EpochTrainer`. Task 6.5."""


class ModelPersistenceUnavailable(TrainingWorkerError):
    """Training finished but nothing could persist or bind the model. Task 6.5."""


class VersionUnavailable(TrainingWorkerError):
    """The job's strategy version could not be read, or carries no loadable graph."""


class UnclassifiedFailureReason(TrainingWorkerError):
    """A caller tried to write a ``failure_reason`` outside :data:`FAILURE_REASONS`.

    Raised rather than logged: this is the guard that makes "a classified reason, never
    a generic string" (Requirement 15.10) a property of the code instead of a
    convention a later edit can quietly break.
    """


def assert_classified_reason(reason: Any) -> str:
    """Return ``reason`` when it is a member of :data:`FAILURE_REASONS`, else raise."""
    text = str(reason or "")
    if text not in FAILURE_REASONS:
        raise UnclassifiedFailureReason(
            f"{text!r} is not a classified training failure reason. Requirement 15.10 "
            f"requires one of {sorted(FAILURE_REASONS)}; a free-text message is not a "
            f"classification."
        )
    return text


# ══════════════════════════════════════════════════════════════════════════
#  3. CONFIGURATION
#
#  Read from the environment at CALL time, not at import time, so an operator who
#  reconfigures a running fleet reconfigures the next poll rather than needing a
#  redeploy - and so a test can set one without reloading the module.
# ══════════════════════════════════════════════════════════════════════════

#: How often a running job writes ``last_heartbeat``.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15
#: How long a ``last_heartbeat`` may lag before :func:`reap_stale_jobs` calls the
#: worker lost. Must be a comfortable multiple of the interval: a threshold close to
#: the interval reaps healthy jobs on one slow write.
DEFAULT_HEARTBEAT_STALE_SECONDS = 90
#: Minimum ratio between the stale threshold and the heartbeat interval. Enforced, so
#: a misconfiguration cannot turn the liveness detector into a job killer.
MIN_STALE_TO_INTERVAL_RATIO = 3


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer; using %s.", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%s is below %s; using %s.", name, value, minimum, default)
        return default
    return value


def heartbeat_interval_seconds() -> int:
    """Seconds between heartbeat writes for a running job."""
    return _env_int(
        "TRAINING_HEARTBEAT_INTERVAL_SECONDS", DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    )


def heartbeat_stale_seconds() -> int:
    """Seconds after which a heartbeat is stale and the worker is presumed lost.

    Raised to :data:`MIN_STALE_TO_INTERVAL_RATIO` times the heartbeat interval when the
    environment asks for less. A detector that fires inside one heartbeat period does
    not detect worker loss, it manufactures it.
    """
    configured = _env_int(
        "TRAINING_HEARTBEAT_STALE_SECONDS", DEFAULT_HEARTBEAT_STALE_SECONDS
    )
    floor = MIN_STALE_TO_INTERVAL_RATIO * heartbeat_interval_seconds()
    if configured < floor:
        logger.warning(
            "The stale-heartbeat threshold (%ss) is less than %sx the heartbeat "
            "interval; raising it to %ss so a healthy job is not reaped.",
            configured,
            MIN_STALE_TO_INTERVAL_RATIO,
            floor,
        )
        return floor
    return configured


def isolation_deadline_seconds() -> Optional[int]:
    """``ml_safety.TrainingIsolator``'s live wall-clock ceiling, or ``None``.

    Read from the isolator's CONFIGURATION rather than from a constant here, exactly as
    ``ml_training_policy._runtime_ceilings`` reads it, so the figure the caps
    intersected and the figure this worker enforces are the same figure.
    """
    try:
        from backend_app.core.ml_safety import TrainingIsolator

        config = getattr(TrainingIsolator, "_config", None)
        value = getattr(config, "max_training_time_seconds", None)
        return None if value is None else int(value)
    except Exception:  # noqa: BLE001 - an unreadable ceiling is the caps' figure alone
        logger.debug("ml_safety is unavailable; the duration cap is the resolved one.")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  4. THE 6.5 SEAM
# ══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class EpochTrainer(Protocol):
    """One unit of fitting, driven by this module's loop.

    The contract is deliberately small, because everything interesting about a training
    run's *control* - cancellation, the wall clock, heartbeats, progress, the status
    row - is the worker's and must not be re-decided per model family.

    ``train_epoch(epoch)`` fits ONE epoch (or round, or iteration, or estimator - see
    ``ml_models.EpochUnit``; the unit is the model spec's, not this module's) and
    returns per-epoch **scalars**. ``evaluate(split)`` scores the fitted model on
    ``"train"``, ``"val"`` or ``"test"``. ``model`` is whatever the binder will persist;
    this module never inspects it.
    """

    def train_epoch(self, epoch: int) -> Mapping[str, Any]:  # pragma: no cover - protocol
        ...

    def evaluate(self, split: str) -> Mapping[str, Any]:  # pragma: no cover - protocol
        ...

    @property
    def model(self) -> Any:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class TrainingBackend:
    """The fit-and-persist half of a training run. Installed by task 6.5.

    ``trainer`` receives the fully built :class:`TrainingContext` and returns an
    :class:`EpochTrainer`. ``binder`` receives the same context plus the fitted model,
    persists the artifact, inserts the ``model_versions`` row, binds it to
    ``(version_id, node_id)`` and returns a mapping describing what it wrote. A
    ``binder`` of ``None`` means "this backend can fit but cannot persist", which the
    worker reports as ``MODEL_PERSISTENCE_UNAVAILABLE`` rather than as success.
    """

    trainer: Callable[["TrainingContext"], Any]
    binder: Optional[Callable[["TrainingContext", Any], Mapping[str, Any]]] = None
    name: str = ""

    def __post_init__(self) -> None:
        if not callable(self.trainer):
            raise TypeError("TrainingBackend.trainer must be callable")
        if self.binder is not None and not callable(self.binder):
            raise TypeError("TrainingBackend.binder must be callable or None")


_TRAINING_BACKEND: Optional[TrainingBackend] = None


def register_training_backend(backend: Optional[TrainingBackend]) -> None:
    """Install (or, with ``None``, remove) the fit-and-persist backend."""
    global _TRAINING_BACKEND
    if backend is not None and not isinstance(backend, TrainingBackend):
        raise TypeError(
            f"register_training_backend expects a TrainingBackend, got "
            f"{type(backend).__name__}"
        )
    _TRAINING_BACKEND = backend
    logger.info(
        "Training backend %s.",
        "removed" if backend is None else f"installed: {backend.name or 'unnamed'}",
    )


def current_training_backend() -> Optional[TrainingBackend]:
    """The installed backend, or ``None`` when task 6.5 has not landed one."""
    return _TRAINING_BACKEND


def reset_training_backend() -> None:
    """Forget the installed backend. For tests, and for a worker being reconfigured."""
    register_training_backend(None)


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.2. A metrics failure must never be what stops a terminal status from
    being written: an unrecorded sample is a dashboard gap, an unwritten terminal status is
    a job that looks like it is still running.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _builder_alerts() -> Any:
    """``backend/builder_alerts.py``, or ``None``. Lazy and guarded (task 9.2).

    The queue-depth alert. Separate from :func:`_metrics` because it is a separate failure -
    the alert module reaches the platform's dispatcher - and guarded for the same reason: a
    page about a deep queue must never be what stops a job from being admitted to it.
    """
    try:
        from backend_app.backend import builder_alerts

        return builder_alerts
    except Exception:  # noqa: BLE001 - an alert never breaks the act it observes
        return None


# ══════════════════════════════════════════════════════════════════════════
#  5. TIME
#
#  Two clocks, on purpose. ``_now()`` is wall-clock UTC and is what goes in a column,
#  because a stored timestamp has to be comparable across processes. Elapsed duration
#  is measured against the job's stored ``started_at`` rather than against a local
#  monotonic counter, so a job that was claimed, lost and re-claimed does not get a
#  fresh duration budget each time - which would make the cap in Requirement 15.8
#  unenforceable by simply crashing often enough.
# ══════════════════════════════════════════════════════════════════════════


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return (moment or _now()).isoformat()


def parse_timestamp(value: Any) -> Optional[datetime]:
    """A timezone-aware ``datetime`` from whatever PostgREST handed back, or ``None``.

    Accepts a ``datetime``, an ISO-8601 string with or without a ``Z``, and a numeric
    epoch. Returns ``None`` for anything unreadable rather than guessing "now": a
    heartbeat this function could not parse must not read as a fresh one.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        # PostgREST can return more than six fractional digits, which
        # ``fromisoformat`` rejects on older interpreters. Trim rather than fail.
        head, sep, tail = text.partition(".")
        if not sep:
            return None
        digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        offset = tail[len(digits) :].lstrip("0123456789")
        try:
            parsed = datetime.fromisoformat(f"{head}.{digits or '0'}{offset}")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def elapsed_seconds(job: Mapping[str, Any], *, now: Optional[datetime] = None) -> float:
    """How long this job has been running, from its stored ``started_at``.

    Falls back to ``created_at`` when ``started_at`` is unreadable, and to ``0.0`` when
    neither is. Zero is the safe direction here: it can only fail to trip the duration
    cap on a row whose timestamps are unreadable, and the heartbeat detector still
    bounds such a job.
    """
    started = parse_timestamp(job.get("started_at")) or parse_timestamp(
        job.get("created_at")
    )
    if started is None:
        return 0.0
    return max(0.0, ((now or _now()) - started).total_seconds())


# ══════════════════════════════════════════════════════════════════════════
#  6. CLASSIFICATION  (Requirement 15.10)
# ══════════════════════════════════════════════════════════════════════════


def classify_failure(exc: BaseException) -> Tuple[str, Dict[str, Any]]:
    """``(classified_reason, detail)`` for ``exc``.

    The reason is always a member of :data:`FAILURE_REASONS`. The exception's own text
    goes in ``detail["message"]`` and its type in ``detail["failure"]``, so an operator
    keeps the diagnosis without the *reason* column becoming a free-text field a UI has
    to pattern-match (Requirement 15.10).

    The order of the checks is the order of specificity. ``TrainingBlocked`` is
    consulted first and its own ``reason`` is trusted, because task 6.3 already
    classified it - which is how a window rejected by
    ``market_data_validation.OutlierDetector`` arrives here as ``DATA_QUALITY`` rather
    than as an unclassified runtime error.
    """
    detail: Dict[str, Any] = {
        "failure": type(exc).__name__,
        "message": str(exc)[:2000],
    }

    if isinstance(exc, S.TrainingBlocked):
        reason = str(getattr(exc, "reason", "") or "")
        detail.update(getattr(exc, "detail", None) or {})
        if reason in FAILURE_REASONS:
            return reason, detail
        # A block whose reason this module does not know is still a block, not a crash.
        detail["blocked_reason"] = reason
        return FAILURE_DATASET, detail

    if isinstance(exc, MaxDurationExceeded):
        detail["elapsed_seconds"] = round(exc.elapsed_seconds, 3)
        detail["allowed_seconds"] = exc.allowed_seconds
        return FAILURE_MAX_DURATION_EXCEEDED, detail

    if isinstance(exc, MemoryBoundExceeded) or isinstance(exc, MemoryError):
        return FAILURE_MEMORY_EXCEEDED, detail

    if isinstance(exc, TrainerUnavailable):
        detail["next_task"] = "6.5 (model versioning and artifact handling)"
        return FAILURE_TRAINER_UNAVAILABLE, detail

    if isinstance(exc, ModelPersistenceUnavailable):
        detail["next_task"] = "6.5 (model versioning and artifact handling)"
        return FAILURE_MODEL_PERSISTENCE_UNAVAILABLE, detail

    if isinstance(exc, VersionUnavailable):
        return FAILURE_VERSION_UNAVAILABLE, detail

    # ``ml_training_policy`` is imported lazily everywhere in this module; classifying
    # its two exception types is the one place that has to name them.
    try:
        from backend_app.backend.ml_training_policy import (
            CapExceeded,
            MLTrainingPolicyError,
        )
    except Exception:  # noqa: BLE001 - fall through to the classified catch-all
        CapExceeded = MLTrainingPolicyError = ()  # type: ignore[assignment]

    if CapExceeded and isinstance(exc, CapExceeded):
        detail.update(exc.to_dict())
        return FAILURE_CAP_EXCEEDED, detail
    if MLTrainingPolicyError and isinstance(exc, MLTrainingPolicyError):
        return FAILURE_CAP_EXCEEDED, detail

    try:
        from backend_app.backend.ml_dataset import MLDatasetError
    except Exception:  # noqa: BLE001
        MLDatasetError = ()  # type: ignore[assignment]
    if MLDatasetError and isinstance(exc, MLDatasetError):
        return FAILURE_DATASET, detail

    return FAILURE_TRAINING_RUNTIME_ERROR, detail


def sanitize_epoch_metrics(metrics: Any, *, epoch: Optional[int] = None) -> Dict[str, Any]:
    """Per-epoch **scalars**, and nothing else.

    004d's header: ``metrics_history`` holds per-epoch scalars only, never predictions
    and never feature values; "SQL CANNOT ENFORCE THIS ... the enforcement itself
    belongs to the writer in tasks 6.4 and 6.6". This is that enforcement, and it works
    two ways rather than one:

    * a key in :data:`FORBIDDEN_METRIC_KEYS` is dropped whatever its value looks like,
      because a single float named ``prediction`` is still model output; and
    * a value that is not a real scalar (``int`` / ``float`` / ``bool`` / ``None``) is
      dropped whatever its key is, because a vector under an innocent name is the same
      leak.

    Anything dropped is logged, so a trainer whose metrics are being trimmed is visible
    rather than silently truncated.
    """
    clean: Dict[str, Any] = {}
    dropped: List[str] = []

    for key, value in dict(metrics or {}).items():
        name = str(key)
        if name.lower() in FORBIDDEN_METRIC_KEYS:
            dropped.append(name)
            continue
        if value is None:
            clean[name] = None
            continue
        if isinstance(value, bool):
            clean[name] = bool(value)
            continue
        if isinstance(value, int):
            clean[name] = int(value)
            continue
        if isinstance(value, float):
            # NaN and infinity are not JSON scalars; PostgREST would reject the whole
            # write, taking the epoch's honest figures with it.
            clean[name] = float(value) if value == value and abs(value) != float("inf") else None
            continue
        # A numpy scalar answers ``.item()``; a numpy array answers it only when it
        # holds exactly one element, which is what makes this a scalar test and not a
        # coercion.
        item = getattr(value, "item", None)
        if callable(item) and getattr(value, "size", 1) == 1:
            try:
                return_value = item()
            except Exception:  # noqa: BLE001
                dropped.append(name)
                continue
            if isinstance(return_value, (int, float, bool)):
                clean[name] = sanitize_epoch_metrics({name: return_value}).get(name)
                continue
        dropped.append(name)

    if dropped:
        logger.warning(
            "Dropped %d non-scalar or forbidden metric key(s) from metrics_history: "
            "%s. That column holds per-epoch scalars only (see "
            "004d_training_and_models.sql).",
            len(dropped),
            ", ".join(sorted(dropped)),
        )
    if epoch is not None:
        clean["epoch"] = int(epoch)
    return clean


# ══════════════════════════════════════════════════════════════════════════
#  7. THE WRITES
#
#  One writer for status changes (:func:`_transition`), so ``updated_at`` cannot be
#  forgotten in a branch and a terminal state cannot be written without the
#  compare-and-set that stops two writers from disagreeing.
# ══════════════════════════════════════════════════════════════════════════


def _degraded(exc: BaseException, what: str) -> bool:
    """Log and swallow a missing-``training_jobs`` error. ``True`` when it was one.

    Anything else propagates, for the reason ``strategy_service`` gives for the same
    split: a write that fails loudly beats a job whose status silently stopped moving.
    """
    if S.is_missing_training_table_error(exc):
        logger.warning(
            "%s could not %s because %s does not exist. Apply %s. Detail: %s",
            "The training worker",
            what,
            TRAINING_JOBS_TABLE,
            TRAINING_MIGRATION,
            exc,
        )
        return True
    return False


async def _transition(
    sb: Any,
    job_id: str,
    payload: Mapping[str, Any],
    *,
    expect_status: Optional[str] = None,
    expect_worker_id: Optional[str] = None,
    expect_heartbeat: Any = None,
    match_heartbeat: bool = False,
    what: str = "update a training job",
) -> Optional[Dict[str, Any]]:
    """Write ``payload`` to one job row, conditionally, always moving ``updated_at``.

    ``expect_status`` / ``expect_worker_id`` / ``match_heartbeat`` are the
    compare-and-set. They are what makes the claim atomic and what stops the
    stale-heartbeat reaper from overwriting a job that heartbeated between the read and
    the write: PostgreSQL takes the row lock for the UPDATE and re-evaluates the WHERE
    clause afterwards, so exactly one of two racing writers matches a row.

    Returns the written row, or ``None`` when the condition matched nothing (which is
    an ordinary outcome here, not an error) or when the table is absent.
    """
    if sb is None:
        logger.warning(
            "No database client, so the training worker could not %s for job %s.",
            what,
            job_id,
        )
        return None

    body = dict(payload)
    # 004d attaches no BEFORE UPDATE trigger to this table. Every writer sets this.
    body["updated_at"] = _iso()

    try:
        query = sb.table(TRAINING_JOBS_TABLE).update(body).eq("id", str(job_id))
        if expect_status is not None:
            query = query.eq("status", expect_status)
        if expect_worker_id is not None:
            query = query.eq("worker_id", expect_worker_id)
        if match_heartbeat:
            query = query.eq("last_heartbeat", expect_heartbeat)
        result = await S._execute(query.execute())
    except Exception as exc:  # noqa: BLE001 - classified, never blanket-swallowed
        if _degraded(exc, what):
            return None
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if S.is_missing_training_table_error(Exception(error_text)):
            logger.warning(
                "%s does not exist (%s), so the worker could not %s. Apply %s.",
                TRAINING_JOBS_TABLE,
                error_text,
                what,
                TRAINING_MIGRATION,
            )
            return None
        raise RuntimeError(f"training job {what} failed: {error_text}")

    rows = (getattr(result, "data", None) or []) if result else []
    return dict(rows[0]) if rows else None


async def _read_job(sb: Any, job_id: str) -> Optional[Dict[str, Any]]:
    """One job row by id, or ``None``. Never raises on a missing table."""
    if sb is None:
        return None
    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("*")
            .eq("id", str(job_id))
            .limit(1)
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        if _degraded(exc, "read a training job"):
            return None
        raise
    if S.is_missing_training_table_error(Exception(S._result_error_text(result) or "")):
        return None
    rows = (getattr(result, "data", None) or []) if result else []
    return dict(rows[0]) if rows else None


def new_worker_id() -> str:
    """A worker identity that says which host and process is holding a job."""
    host = os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME") or "local"
    return f"tw-{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


async def claim_training_job(
    sb: Any, job_id: str, worker_id: str, *, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Claim ``job_id`` atomically and return the ``RUNNING`` row, or ``None``.

    ``design.md``: ``job <- claim(job_id)  // atomic; sets RUNNING + heartbeat``, and
    ``IF job IS NULL THEN RETURN`` for "already claimed or cancelled".

    The atomicity is the **conditional UPDATE**, not a read-then-write and not the Redis
    hint: ``SET status='RUNNING', worker_id=..., started_at=..., last_heartbeat=...
    WHERE id = :id AND status = 'QUEUED'``. Two workers that popped the same hint both
    issue that statement; PostgreSQL serialises them on the row lock and the second one
    re-evaluates ``status = 'QUEUED'`` against the already-updated row, matching nothing.
    A read-then-write would let both see ``QUEUED`` and both proceed.

    ``started_at`` is set here and **not** re-set on a later claim of the same row,
    because the duration cap in Requirement 15.8 measures from it: a job that crashed and
    was re-queued must not buy a fresh budget by crashing. It is written only when the
    column is still empty, which the read below establishes.

    Returns ``None`` - never raises - when the job is gone, is no longer ``QUEUED``, was
    claimed by somebody else, or when ``training_jobs`` does not exist yet.
    """
    moment = now or _now()
    existing = await _read_job(sb, job_id)
    if existing is None:
        logger.info("Training job %s could not be read; nothing was claimed.", job_id)
        return None
    if str(existing.get("status") or "") != STATUS_QUEUED:
        logger.info(
            "Training job %s is %s, not %s; it was already claimed or has finished.",
            job_id,
            existing.get("status"),
            STATUS_QUEUED,
        )
        return None

    payload: Dict[str, Any] = {
        "status": STATUS_RUNNING,
        "worker_id": str(worker_id),
        "last_heartbeat": _iso(moment),
    }
    if not existing.get("started_at"):
        payload["started_at"] = _iso(moment)

    claimed = await _transition(
        sb,
        job_id,
        payload,
        expect_status=STATUS_QUEUED,
        what="claim a training job",
    )
    if claimed is None:
        logger.info(
            "Training job %s was claimed by another worker between the read and the "
            "write; this worker is standing down.",
            job_id,
        )
        return None

    # The UPDATE's representation is authoritative for the columns it wrote, but a
    # client that returns a partial row would leave the config unreadable, so fall back
    # to a full read rather than training on a half-known job.
    if "config" not in claimed:
        refreshed = await _read_job(sb, job_id)
        if refreshed is not None:
            claimed = refreshed

    # Requirement 24.2's counts by status. RUNNING is recorded here because this is the
    # only place a job becomes RUNNING, and only after the conditional UPDATE matched - a
    # worker that lost the race counts nothing, so the tally is claims, not attempts.
    collector = _metrics()
    if collector is not None:
        collector.record_training_job_status(STATUS_RUNNING)

    logger.info(
        "Training job %s claimed by %s (epochs_total=%s).",
        job_id,
        worker_id,
        claimed.get("epochs_total"),
    )
    return claimed


async def release_training_job(
    sb: Any, job_id: str, worker_id: str, *, reason: str = ""
) -> Optional[Dict[str, Any]]:
    """Hand a claimed job back to the queue, unrun.

    Used by the global-concurrency path: Requirement 16.5 says a job that cannot run
    because the shared pool is saturated is **held in the queue**, not rejected. So the
    claim is reverted - ``RUNNING -> QUEUED``, worker identity and heartbeat cleared -
    rather than the job being failed. ``started_at`` is left in place on purpose: the
    duration budget belongs to the job, not to one attempt at it.
    """
    released = await _transition(
        sb,
        job_id,
        {
            "status": STATUS_QUEUED,
            "worker_id": None,
            "last_heartbeat": None,
        },
        expect_status=STATUS_RUNNING,
        expect_worker_id=worker_id,
        what="release a training job back to the queue",
    )
    if released is not None:
        logger.info(
            "Training job %s released back to %s%s.",
            job_id,
            STATUS_QUEUED,
            f" ({reason})" if reason else "",
        )
    return released


async def write_heartbeat(
    sb: Any, job_id: str, worker_id: str, *, now: Optional[datetime] = None
) -> bool:
    """Refresh ``last_heartbeat`` for a job this worker holds. Best effort.

    Scoped to ``status = 'RUNNING' AND worker_id = :me`` so a worker cannot heartbeat a
    job it no longer owns - which is what stops a process that was already reaped as
    ``WORKER_LOST`` from resurrecting the row by writing to it.

    Returns ``False`` rather than raising when the write did not land: a failed
    heartbeat is a liveness signal in its own right, and :func:`reap_stale_jobs` is the
    thing that acts on it.
    """
    try:
        written = await _transition(
            sb,
            job_id,
            {"last_heartbeat": _iso(now)},
            expect_status=STATUS_RUNNING,
            expect_worker_id=worker_id,
            what="write a heartbeat",
        )
    except Exception as exc:  # noqa: BLE001 - a heartbeat must not kill the run
        logger.warning("Heartbeat for training job %s failed: %s", job_id, exc)
        return False
    return written is not None


async def record_epoch(
    sb: Any,
    job: Mapping[str, Any],
    worker_id: str,
    *,
    epoch: int,
    epochs_total: int,
    metrics: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Persist one completed epoch: progress, the two losses, and the scalar history.

    **Progress is derived from completed epochs and from nothing else** (Requirement
    15.4). It is written after the epoch's fit returned, so the row never claims an
    epoch that is still running, and it is clamped into ``[0, 1]`` because
    ``chk_tj_progress`` refuses anything outside that - a job whose ``epochs_total``
    disagreed with its loop would otherwise take the whole write down.

    ``metrics_history`` is the sanitised history, so this write is also where 004d's
    data-minimisation rule is applied. The heartbeat rides along: an epoch that
    completed is the strongest liveness evidence there is, so a separate write would be
    redundant.
    """
    completed = max(0, int(epoch))
    total = max(1, int(epochs_total))
    payload: Dict[str, Any] = {
        "epoch_current": completed,
        "progress": min(1.0, max(0.0, completed / total)),
        "metrics_history": [dict(entry) for entry in history],
        "last_heartbeat": _iso(now),
    }
    if "loss" in metrics:
        payload["loss"] = metrics.get("loss")
    if "val_loss" in metrics:
        payload["val_loss"] = metrics.get("val_loss")

    return await _transition(
        sb,
        str(job.get("id") or ""),
        payload,
        expect_status=STATUS_RUNNING,
        expect_worker_id=worker_id,
        what="record an epoch",
    )


async def set_version_lifecycle(
    sb: Any,
    version_id: str,
    state: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Move one ``strategy_versions`` row's ``lifecycle_state``. Best effort.

    The vocabulary is validated against ``strategy_builder.LIFECYCLE_STATES`` - which is
    ``chk_lifecycle_state`` verbatim - before the write, so a typo is a local error
    rather than a 23514 from PostgreSQL. A row whose canonical columns are absent
    (migration 004 part 1 unapplied) degrades to a warning naming that condition,
    because a job's own terminal status is the load-bearing record and must not be lost
    to a failed secondary write.

    ``extra`` rides along in the same UPDATE and exists for one caller: task 8.3's
    lifecycle machine sets ``is_read_only = TRUE`` on the transition into an immutable
    state, and it has to be the *same* statement so a row cannot be left ``DEPLOYED`` and
    still writable. It may not carry ``lifecycle_state`` - that value is this function's
    to decide, and a caller that could override it after the vocabulary check would
    defeat the check.
    """
    if sb is None or not version_id:
        return False

    from backend_app.backend.strategy_builder import LIFECYCLE_STATES

    target = str(state).upper()
    if target not in LIFECYCLE_STATES:
        raise ValueError(
            f"lifecycle_state {state!r} is not one of {sorted(LIFECYCLE_STATES)} "
            f"(chk_lifecycle_state)"
        )

    payload: Dict[str, Any] = {"lifecycle_state": target}
    for key, value in dict(extra or {}).items():
        if str(key) == "lifecycle_state":
            raise ValueError(
                "set_version_lifecycle(extra=...) may not carry lifecycle_state; pass it "
                "as the state argument so it is checked against chk_lifecycle_state."
            )
        payload[str(key)] = value

    try:
        query = (
            sb.table(STRATEGY_VERSIONS_TABLE)
            .update(payload)
            .eq("id", str(version_id))
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        if S.is_missing_canonical_column_error(exc) or S.is_missing_training_table_error(
            exc
        ):
            logger.warning(
                "Strategy version %s could not be moved to %s because the canonical "
                "Strategy Builder columns are absent (%s). The training job's own "
                "status is recorded either way.",
                version_id,
                target,
                exc,
            )
            return False
        logger.warning(
            "Strategy version %s could not be moved to %s: %s",
            version_id,
            target,
            exc,
        )
        return False

    error_text = S._result_error_text(result)
    if error_text:
        logger.warning(
            "Strategy version %s could not be moved to %s: %s",
            version_id,
            target,
            error_text,
        )
        return False
    return bool(getattr(result, "data", None))


# ══════════════════════════════════════════════════════════════════════════
#  8. THE TERMINAL TRANSITIONS
# ══════════════════════════════════════════════════════════════════════════


async def _finish(
    sb: Any,
    job: Mapping[str, Any],
    *,
    status: str,
    worker_id: Optional[str],
    failure_reason: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
    event: str = "",
    detail: Optional[Mapping[str, Any]] = None,
    expect_status: str = STATUS_RUNNING,
    expect_heartbeat: Any = None,
    match_heartbeat: bool = False,
) -> Optional[Dict[str, Any]]:
    """Write one terminal status, move the version, and announce it. One code path.

    Every terminal state in this module comes through here, which is what makes four
    guarantees structural instead of repeated:

    1. ``updated_at`` moves (:func:`_transition` sets it; 004d has no trigger).
    2. ``FAILED`` carries a reason from :data:`FAILURE_REASONS` -
       :func:`assert_classified_reason` raises otherwise, so an unclassified reason
       cannot be written at all (Requirement 15.10). ``chk_tj_failed_has_reason`` is the
       database's half of the same rule.
    3. A non-``FAILED`` state carries **no** reason, so a cancelled job cannot look like
       a failed one.
    4. The write is a compare-and-set, so a job already reaped as ``WORKER_LOST`` is not
       silently overwritten by the process that lost it.
    """
    job_id = str(job.get("id") or "")
    payload: Dict[str, Any] = {"status": status, "completed_at": _iso()}

    if status == STATUS_FAILED:
        payload["failure_reason"] = assert_classified_reason(failure_reason)
    else:
        if failure_reason:
            raise ValueError(
                f"a {status} training job must not carry a failure reason "
                f"({failure_reason!r})"
            )
        payload["failure_reason"] = None

    if status == STATUS_COMPLETED:
        # Every epoch ran. Progress is 1 because the epochs completed, not because the
        # status says so.
        payload["progress"] = 1.0
    payload.update(dict(extra or {}))

    written = await _transition(
        sb,
        job_id,
        payload,
        expect_status=expect_status,
        expect_worker_id=worker_id,
        expect_heartbeat=expect_heartbeat,
        match_heartbeat=match_heartbeat,
        what=f"set status {status}",
    )
    if written is None:
        logger.warning(
            "Training job %s could not be moved to %s: no row matched the expected "
            "state (%s). Another writer got there first, or %s is absent.",
            job_id,
            status,
            expect_status,
            TRAINING_JOBS_TABLE,
        )
        return None

    if lifecycle_state:
        await set_version_lifecycle(sb, str(job.get("version_id") or ""), lifecycle_state)

    if event:
        await S.publish_training_event(
            str(job.get("user_id") or ""),
            event,
            {
                "job_id": job_id,
                "version_id": job.get("version_id"),
                "node_id": job.get("node_id"),
                "status": status,
                "failure_reason": payload.get("failure_reason"),
                "epoch_current": written.get("epoch_current"),
                "epochs_total": written.get("epochs_total"),
                "progress": written.get("progress"),
                **dict(detail or {}),
            },
        )

    # -- Requirement 24.2 ---------------------------------------------------
    # Recorded here and only here for a terminal status, because every terminal state in
    # this module comes through this function - which is what stops the tally from drifting
    # from the rows. Recorded *after* the write landed, so a status that could not be
    # written is not counted as one that was.
    #
    # The duration is `elapsed_seconds(job)`, the same figure the duration cap in
    # Requirement 15.8 is enforced against, read from the job's stored `started_at` rather
    # than measured again here. A job that was claimed, lost and re-claimed therefore
    # reports its whole life, which is the figure that matters.
    collector = _metrics()
    if collector is not None:
        # `elapsed_seconds` answers 0.0 for a row whose timestamps are all unreadable. That
        # is the right answer for the *duration cap* - zero can only fail to trip it, which
        # is the safe direction - but it is the wrong thing to put in a duration histogram,
        # where it would read as a job that finished instantly. So an unmeasurable duration
        # is recorded as absent, not as zero.
        elapsed = elapsed_seconds(job)
        collector.record_training_job_status(
            status,
            block_id=str(job.get("block_id") or "") or None,
            duration_seconds=elapsed if elapsed > 0 else None,
        )
        reason_written = payload.get("failure_reason")
        if reason_written == FAILURE_CAP_EXCEEDED:
            # Requirement 16.3 names one cap; `classify_failure` put it in the detail as
            # `CapExceeded.to_dict()`. Unnamed here rather than guessed at.
            collector.record_training_cap_rejection(
                str(dict(detail or {}).get("cap") or "unnamed")
            )
        elif reason_written in S.INSUFFICIENT_DATA_BLOCK_REASONS:
            # A job admitted with enough data can still find it gone by the time it runs -
            # the window degraded, the feed shortened. Same vocabulary as the API-side
            # block (`strategy_service.INSUFFICIENT_DATA_BLOCK_REASONS`), so the counter
            # answers one question rather than two half-questions. The two cannot double
            # count: a request blocked at admission never becomes a row to fail.
            collector.record_training_blocked_insufficient_data(str(reason_written))

    logger.info(
        "Training job %s -> %s%s.",
        job_id,
        status,
        f" ({payload['failure_reason']})" if payload.get("failure_reason") else "",
    )
    return written


async def reap_stale_jobs(
    sb: Any,
    *,
    stale_after_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Mark every ``RUNNING`` job whose heartbeat has gone stale ``FAILED``/``WORKER_LOST``.

    Requirement 15.9, and ``design.md``'s failure table: "heartbeat stale beyond
    threshold -> job marked ``FAILED`` with ``WORKER_LOST``; version returns to
    ``SAVED``; no partial model bound". The requirement attributes this to the
    Training_Service rather than to the worker, which is why it is a standalone sweep
    any process can run - the worker calls it on its own poll so a single-process
    deployment still detects loss, but nothing about it depends on being *the* worker.

    **Why the staleness test is in Python and not in the WHERE clause.** A server-side
    ``last_heartbeat < :cutoff`` would silently skip rows where ``last_heartbeat`` IS
    NULL, because in SQL ``NULL < anything`` is NULL and a NULL predicate does not
    match. A ``RUNNING`` job with no heartbeat at all is the *most* lost job there is,
    so the comparison is done here where a missing timestamp counts as stale.

    **Why the write is a compare-and-set on the heartbeat.** Between the read and the
    write the owning worker may heartbeat. The UPDATE therefore carries
    ``last_heartbeat = :the_value_we_judged`` alongside ``status = 'RUNNING'``, so a job
    that proved itself alive in that window matches nothing and is left running. A row
    whose heartbeat is NULL has no such token; it is reaped on status alone, which is
    correct for a job that never wrote one.

    Returns one entry per reaped job. Never raises on an absent table.
    """
    if sb is None:
        return []

    threshold = int(
        heartbeat_stale_seconds() if stale_after_seconds is None else stale_after_seconds
    )
    moment = now or _now()
    cutoff = moment - timedelta(seconds=threshold)

    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("*")
            .eq("status", STATUS_RUNNING)
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        if _degraded(exc, "scan for stale training jobs"):
            return []
        raise
    if S.is_missing_training_table_error(Exception(S._result_error_text(result) or "")):
        return []

    running = (getattr(result, "data", None) or []) if result else []
    reaped: List[Dict[str, Any]] = []

    for row in running:
        job = dict(row or {})
        job_id = str(job.get("id") or "")
        if not job_id:
            continue
        beat = parse_timestamp(job.get("last_heartbeat"))
        if beat is not None and beat > cutoff:
            continue

        written = await _finish(
            sb,
            job,
            status=STATUS_FAILED,
            worker_id=None,
            failure_reason=FAILURE_WORKER_LOST,
            lifecycle_state=WORKER_LOST_LIFECYCLE_STATE,
            event="training.failed",
            detail={
                "reason": FAILURE_WORKER_LOST,
                "last_heartbeat": job.get("last_heartbeat"),
                "stale_after_seconds": threshold,
                "worker_id": job.get("worker_id"),
            },
            expect_status=STATUS_RUNNING,
            expect_heartbeat=job.get("last_heartbeat"),
            match_heartbeat=beat is not None,
        )
        if written is None:
            # It heartbeated, or another sweeper got there first. Both mean "leave it".
            continue
        logger.warning(
            "Training job %s was held by worker %s whose heartbeat last arrived at %s, "
            "more than %ss ago; marked %s/%s and version %s returned to %s. No model "
            "was bound.",
            job_id,
            job.get("worker_id"),
            job.get("last_heartbeat"),
            threshold,
            STATUS_FAILED,
            FAILURE_WORKER_LOST,
            job.get("version_id"),
            WORKER_LOST_LIFECYCLE_STATE,
        )
        reaped.append(
            {
                "job_id": job_id,
                "version_id": job.get("version_id"),
                "worker_id": job.get("worker_id"),
                "last_heartbeat": job.get("last_heartbeat"),
                "failure_reason": FAILURE_WORKER_LOST,
            }
        )
        if limit is not None and len(reaped) >= int(limit):
            break

    return reaped


# ══════════════════════════════════════════════════════════════════════════
#  9. THE CAP RE-CHECK  (Requirement 16.4)
# ══════════════════════════════════════════════════════════════════════════


async def count_all_training_jobs(
    sb: Any, user_id: str, *, exclude_job_id: str = ""
) -> Any:
    """A :class:`ml_training_policy.JobCounts` with **genuinely global** figures.

    This is the half task 6.2 could not measure and said so: ``count_training_jobs`` runs
    on an RLS-scoped client and therefore reports the user's own counts as an explicit
    lower bound for the fleet. The worker runs with the service role, so here
    ``global_running`` and ``global_queued`` are what they claim to be, and Requirement
    16.5's global saturation check becomes real rather than deferred - which is exactly
    what task 6.2's report said belonged to 6.4's scheduler.

    ``exclude_job_id`` removes the job being admitted from the global running count. The
    worker has already claimed it, so counting it would let the cap refuse the very job
    it just started for every value of the limit.

    Degrades to :meth:`JobCounts.unavailable` naming ``004d_training_and_models.sql``.
    """
    from backend_app.backend.ml_training_policy import JobCounts

    if sb is None:
        return JobCounts.unavailable(
            "The training worker has no database client, so the concurrency caps were "
            "not evaluated for this job."
        )

    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("id,status,user_id")
            .in_("status", list(S.TRAINING_JOB_LIVE_STATES))
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        if S.is_missing_training_table_error(exc):
            logger.warning(
                "%s is absent, so the worker could not evaluate the concurrency caps "
                "for this job. Apply %s. Every per-request cap (epochs, rows, feature "
                "columns, memory) was still re-checked. Detail: %s",
                TRAINING_JOBS_TABLE,
                TRAINING_MIGRATION,
                exc,
            )
            return JobCounts.unavailable(
                f"{TRAINING_JOBS_TABLE} is absent; apply {TRAINING_MIGRATION}."
            )
        logger.warning("Live training job counts could not be read: %s", exc)
        return JobCounts.unavailable(f"Live training job counts could not be read ({exc}).")

    if S.is_missing_training_table_error(Exception(S._result_error_text(result) or "")):
        return JobCounts.unavailable(
            f"{TRAINING_JOBS_TABLE} is absent; apply {TRAINING_MIGRATION}."
        )

    rows = [dict(row or {}) for row in ((getattr(result, "data", None) or []) if result else [])]
    if exclude_job_id:
        rows = [row for row in rows if str(row.get("id") or "") != str(exclude_job_id)]

    counts = JobCounts(
        user_active=sum(1 for row in rows if str(row.get("user_id") or "") == str(user_id)),
        global_running=sum(1 for row in rows if row.get("status") == STATUS_RUNNING),
        global_queued=sum(1 for row in rows if row.get("status") == STATUS_QUEUED),
        available=True,
    )
    # Task 9.2's queue-depth alert, from the count that was just taken. This is the one
    # place in the codebase where the global `QUEUED` figure is real: the worker holds the
    # service role, so the count is the fleet's, not the caller's own lower bound (see this
    # function's docstring). Raised only on the `available=True` path - an unreadable queue
    # is not a deep one, and paging on a missing table would page on migration
    # `004d_training_and_models.sql` rather than on capacity. No second query, no await, and
    # the caps below are evaluated from `counts` exactly as before.
    alerts = _builder_alerts()
    if alerts is not None:
        try:
            alerts.notice_training_queue_depth(counts.global_queued)
        except Exception:  # noqa: BLE001 - the caps below this count are a control
            logger.debug("The training queue-depth alert was not raised.", exc_info=True)
    return counts


async def resolve_job_user(sb: Any, user_id: str) -> Dict[str, Any]:
    """The ``{"id", "plan"}`` mapping ``resolve_caps`` reads, for a job's owner.

    A worker holds no request and therefore no JWT, so the plan is read from the same
    place every entitlement check reads it:
    ``core.subscription_dependencies.get_user_plan``, which selects
    ``profiles.subscription_tier`` and normalises it through
    ``SubscriptionEngine.migrate_plan_key``. There is no second tier lookup here.

    An unreadable plan falls back to whatever that function falls back to - it is
    fail-closed in production by its own design - and the fallback is recorded in the
    returned mapping so the caps' warnings can say the plan was assumed.
    """
    from backend_app.core.subscription_dependencies import get_user_plan

    try:
        plan = await get_user_plan(str(user_id), sb)
        return {"id": str(user_id), "plan": plan, "plan_resolved": True}
    except Exception as exc:  # noqa: BLE001 - reported on the caps, not swallowed
        logger.warning(
            "The plan for user %s could not be read (%s); the caps will be resolved at "
            "the most restrictive tier.",
            user_id,
            exc,
        )
        return {"id": str(user_id), "plan": None, "plan_resolved": False}


async def recheck_caps(
    sb: Any,
    job: Mapping[str, Any],
    *,
    stats: Any,
    spec: Any,
    user: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Re-evaluate the effective caps for ``job`` **before its first epoch**.

    Requirement 16.4, in the one place the requirement puts it. This is not a repeat of
    the admission check for symmetry's sake; it closes three real windows the API check
    cannot:

    * the client may never have gone through the API at all;
    * the user's tier may have changed between queueing and running, in either
      direction; and
    * the concurrency figures task 6.2 had to report as a lower bound are measurable
      here (see :func:`count_all_training_jobs`), so global saturation is detected rather
      than assumed absent.

    The epoch count re-checked is the **stored** ``epochs_total`` and the row/column
    counts are the **rebuilt dataset's measured** figures, not the numbers the config
    recorded at admission: a window that returned fewer usable rows this time is checked
    as it actually is.

    Raises
        ``ml_training_policy.CapExceeded`` naming the one cap with its requested and
        permitted values (Requirement 16.3), which :func:`classify_failure` turns into a
        ``CAP_EXCEEDED`` failure reason.

    Returns
        the ``Admission``. ``DEFER`` is not a rejection: the caller releases the claim
        and the job stays ``QUEUED`` (Requirement 16.5).
    """
    from backend_app.backend.ml_training_policy import (
        TrainingRequest,
        enforce_caps,
        resolve_caps,
    )

    config = dict(job.get("config") or {})
    owner = dict(user or await resolve_job_user(sb, str(job.get("user_id") or "")))

    epochs = job.get("epochs_total")
    if epochs is None:
        epochs = config.get("epochs")

    request = TrainingRequest(
        block_id=str(job.get("block_id") or config.get("block_id") or ""),
        epochs=int(epochs or 0),
        rows=int(stats.usable_rows),
        feature_columns=int(stats.usable_feature_columns),
        sequence_length=config.get("sequence_length"),
        batch_size=config.get("batch_size"),
        model_family=config.get("model_family") or "",
    )
    caps = resolve_caps(owner, spec)
    counts = await count_all_training_jobs(
        sb, str(job.get("user_id") or ""), exclude_job_id=str(job.get("id") or "")
    )
    admission = enforce_caps(request, caps, owner, job_counts=counts)
    logger.info(
        "Caps re-checked for training job %s before its first epoch: %s "
        "(epochs %s/%s, rows %s/%s, feature columns %s/%s).",
        job.get("id"),
        admission.decision.value,
        request.epochs,
        caps.max_epochs,
        request.rows,
        caps.max_rows,
        request.feature_columns,
        caps.max_feature_columns,
    )
    return admission


# ══════════════════════════════════════════════════════════════════════════
#  10. REBUILDING THE INPUTS
#
#  Every step below is task 6.3's function, called through the module object so the
#  fetch seam, the quality rule, the feature pipeline and the fingerprint are literally
#  the same code the admission path ran. Nothing is recomputed by a second rule here.
# ══════════════════════════════════════════════════════════════════════════


async def load_version_row(sb: Any, version_id: str) -> Dict[str, Any]:
    """The ``strategy_versions`` row a job trains against.

    Raises :class:`VersionUnavailable` rather than returning ``None``, because a job
    whose version cannot be read has nothing to train and that is a classified failure
    (``VERSION_UNAVAILABLE``), not a degraded success.
    """
    if sb is None:
        raise VersionUnavailable(
            "No database client is available, so the strategy version could not be read."
        )
    try:
        query = (
            sb.table(STRATEGY_VERSIONS_TABLE)
            .select("*")
            .eq("id", str(version_id))
            .limit(1)
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        raise VersionUnavailable(
            f"Strategy version {version_id} could not be read: {exc}"
        ) from exc

    error_text = S._result_error_text(result)
    if error_text:
        raise VersionUnavailable(
            f"Strategy version {version_id} could not be read: {error_text}"
        )
    rows = (getattr(result, "data", None) or []) if result else []
    if not rows:
        raise VersionUnavailable(f"Strategy version {version_id} does not exist.")
    return dict(rows[0])


@dataclass
class TrainingInputs:
    """What a job needs before its first epoch, all measured from a real refetch."""

    plan: Any
    graph: Any
    spec: Any
    validation_cfg: Any
    matrix: Any
    feature_schema: Any
    dataset: Any
    splits: Any
    stats: Any
    quality: Any
    fingerprint: str
    fingerprint_matches: bool
    recorded_fingerprint: str


async def rebuild_training_inputs(
    job: Mapping[str, Any], version_row: Mapping[str, Any], *, registry: Any = None
) -> TrainingInputs:
    """Refetch the window and rebuild the dataset from ``job.config``.

    ``design.md``'s worker body, steps ``refetch`` / ``run_feature_pipeline`` /
    ``make_temporal_splits`` / ``build_supervised_dataset``, each delegated to the
    function task 6.3 already wrote. The plan comes from
    ``strategy_compiler.load_plan``, so the worker executes the *persisted* plan when its
    hash still matches the graph and recompiles only when it does not (Requirement 22.5)
    - the worker and the API therefore train and validate the same bytes.

    **The fingerprint is verified and reported, not enforced.** The design writes
    ``refetch(job.config, expect_fingerprint := job.dataset_fingerprint)``, and a strict
    equality there is unsatisfiable with the fetch the platform actually has:
    ``fetch_training_bars`` returns the *N most recent* bars, so any delay between
    queueing and running shifts the window and changes the fingerprint by construction.
    Failing on that would fail every real job. So the recorded fingerprint stays the
    provenance record it was written as, the observed one is computed and compared, and a
    mismatch is a logged warning carried on the result rather than a refusal. Making it
    an enforced precondition needs a by-timestamp fetch, which is a change to the feed
    contract and not to this worker.

    Raises
        :class:`VersionUnavailable` when the row carries no loadable graph, and
        ``strategy_service.TrainingBlocked`` (already classified) for a rejected window,
        an unusable feature schema or an unsplittable dataset.
    """
    from backend_app.backend.ml_training_policy import DatasetStats, ModelSpecView, ValidationConfig
    from backend_app.backend.strategy_compiler import (
        CompilerError,
        ValidationError,
        load_plan,
    )

    config = dict(job.get("config") or {})
    node_id = str(job.get("node_id") or "")
    block_id = str(job.get("block_id") or config.get("block_id") or "")

    try:
        loaded = load_plan(version_row, registry)
    except (CompilerError, ValidationError) as exc:
        raise VersionUnavailable(
            f"Strategy version {job.get('version_id')} carries no executable plan: {exc}"
        ) from exc

    plan = loaded.plan
    if node_id not in tuple(getattr(plan, "ml_nodes", ()) or ()):
        raise VersionUnavailable(
            f"Node {node_id!r} is not a model node of strategy version "
            f"{job.get('version_id')}, so this job has nothing to train."
        )

    spec = ModelSpecView.for_block_id(block_id)
    if spec is None:
        raise S.TrainingBlocked(
            S.REASON_MODEL_UNPUBLISHED,
            f"The registry publishes no model descriptor for {block_id!r}, so this job "
            f"cannot be trained.",
            {"node_id": node_id, "block_id": block_id},
        )

    label_horizon = int(config.get("label_horizon") or S.DEFAULT_LABEL_HORIZON)
    validation_cfg = ValidationConfig.for_model(
        spec,
        label_horizon=label_horizon,
        feature_lookback=int(getattr(plan, "warmup_bars", 0) or 0),
        val_fraction=config.get("val_fraction"),
        test_fraction=config.get("test_fraction"),
        embargo_bars=config.get("embargo_bars"),
    )

    # -- the window, through the one I/O seam ------------------------------
    market = S.resolve_training_data_source(plan)
    symbol = str(config.get("symbol") or market["symbol"])
    timeframe = str(config.get("timeframe") or market["timeframe"])
    bars = int((config.get("range") or {}).get("bars") or 0) or int(
        (config.get("range") or {}).get("requested_bars") or 0
    )
    if bars <= 0:
        raise S.TrainingBlocked(
            S.REASON_DATASET,
            "The recorded training configuration names no window size, so the job "
            "cannot be reproduced.",
            {"range": config.get("range")},
        )

    rows = await S.fetch_training_bars(symbol, timeframe, bars)
    frame = S.training_frame(rows, bars)
    frame, quality = await S.quality_check_training_frame(frame, symbol, timeframe)
    fingerprint = S.dataset_fingerprint(frame, symbol, timeframe)

    recorded = str(job.get("dataset_fingerprint") or "")
    matches = bool(recorded) and recorded == fingerprint
    if recorded and not matches:
        logger.warning(
            "Training job %s recorded dataset fingerprint %s but the refetched window "
            "fingerprints to %s. The feed returns the most recent bars, so a window "
            "that moved between queueing and running is expected; the recorded "
            "configuration remains the provenance record and training continues on the "
            "window that was actually read.",
            job.get("id"),
            recorded,
            fingerprint,
        )

    # -- features, schema, labels, splits ---------------------------------
    matrix = await S.run_feature_pipeline(plan, registry, frame, node_id)
    feature_schema, _validated = S.check_feature_schema(matrix, node_id, block_id)
    dataset = S.build_training_dataset(
        matrix,
        frame,
        label_horizon=validation_cfg.label_horizon,
        label_mode=str(config.get("label_mode") or S.DEFAULT_LABEL_MODE),
        label_threshold=float(
            config.get("label_threshold")
            if config.get("label_threshold") is not None
            else S.DEFAULT_LABEL_THRESHOLD
        ),
    )
    stats = DatasetStats.from_dataset(dataset)
    splits = S.split_training_dataset(
        dataset, validation_cfg, feature_lookback=int(getattr(plan, "warmup_bars", 0) or 0)
    )

    return TrainingInputs(
        plan=plan,
        graph=loaded.graph,
        spec=spec,
        validation_cfg=validation_cfg,
        matrix=matrix,
        feature_schema=feature_schema,
        dataset=dataset,
        splits=splits,
        stats=stats,
        quality=quality,
        fingerprint=fingerprint,
        fingerprint_matches=matches,
        recorded_fingerprint=recorded,
    )


# ══════════════════════════════════════════════════════════════════════════
#  11. ISOLATION, MEMORY AND DETERMINISM  (Requirement 16.6)
# ══════════════════════════════════════════════════════════════════════════


def assert_memory_bounds(dataset: Any, *, batch_size: Optional[int] = None) -> Dict[str, Any]:
    """Put the assembled tensor and the batch size past ``ml_safety.MemoryMonitor``.

    The pre-flight figure in ``ml_training_policy.estimate_memory_mb`` says of itself
    that it is an estimate; ``MemoryMonitor`` is the measurement, and this is where a run
    meets it. Both refusals are re-raised as :class:`MemoryBoundExceeded` so
    classification is unambiguous - ``validate_batch_size`` raises a bare ``ValueError``,
    which the classified catch-all would otherwise absorb as a generic runtime error.

    Returns what was measured, so a run's footprint is in the log rather than inferred.
    """
    from backend_app.core.ml_safety import MemoryMonitor

    features = getattr(dataset, "X", None)
    nbytes = int(getattr(features, "nbytes", 0) or 0)
    tensor_mb = nbytes / (1024 * 1024)

    try:
        MemoryMonitor.validate_tensor_size(tensor_mb)
    except MemoryError as exc:
        raise MemoryBoundExceeded(
            f"The training feature matrix needs {tensor_mb:.1f} MB, which exceeds the "
            f"platform tensor bound: {exc}"
        ) from exc

    if batch_size is not None:
        try:
            MemoryMonitor.validate_batch_size(int(batch_size))
        except ValueError as exc:
            raise MemoryBoundExceeded(
                f"Batch size {batch_size} exceeds the platform bound: {exc}"
            ) from exc

    measured = {
        "tensor_mb": round(tensor_mb, 3),
        "rows": int(getattr(dataset, "n_rows", 0) or 0),
        "columns": int(getattr(dataset, "n_columns", 0) or 0),
        "batch_size": None if batch_size is None else int(batch_size),
    }
    logger.info("Memory bounds accepted for this run: %s", measured)
    return measured


#: Latched once the isolator has proved it cannot run this process's fit. See
#: :func:`run_isolated`: retrying a ``spawn`` pool that will fail to pickle the same
#: closure on every epoch costs a process launch per epoch and buys nothing.
_ISOLATION_UNAVAILABLE = False


def reset_isolation_latch() -> None:
    """Forget that isolation was unavailable. For tests, and for a reconfigured worker."""
    global _ISOLATION_UNAVAILABLE
    _ISOLATION_UNAVAILABLE = False


def run_isolated(fn: Callable[[], Any]) -> Tuple[Any, bool]:
    """Run ``fn`` through ``ml_safety.TrainingIsolator``; fall back in-process.

    ``(result, isolated)``. The fallback is not a shortcut, it is the precedent this
    codebase already set: ``ml_models.XGBoostStrategyBlock.train_custom_strategy`` calls
    ``TrainingIsolator.run_isolated_training`` inside a ``try`` and continues in the
    current process on failure, logging it. The reason is concrete - the isolator uses a
    ``spawn`` multiprocessing pool, and a closure over a live model and a numpy view is
    not picklable - so an unconditional isolated call would make training impossible
    rather than safe.

    **Why isolation is applied per epoch and not per job.** Wrapping the whole run would
    put the epoch loop inside the child process, and the epoch boundary is exactly where
    Requirement 15.7 requires cancellation to be observed and Requirement 15.8 requires
    the wall clock to be checked. Those decisions need the parent's database client, so
    they stay in the parent and the *fit* is what crosses the boundary. The worker process
    is itself the coarse-grained isolation ``TrainingIsolator``'s docstring is about
    ("training must never interfere with execution runtime"): it is a separate entry
    point (:func:`run_worker`) and shares no state with the API or the execution runtime.

    **Why the failure latches.** ``ml_models`` retries the isolated call every time it
    trains, which is once per model. This loop calls it once per epoch, and the failure
    mode is deterministic - the same unpicklable closure fails identically every epoch -
    so retrying would pay a ``spawn`` process launch per epoch for a result already known.
    The first failure is logged in full and latches for the life of the process;
    :func:`reset_isolation_latch` clears it.
    """
    global _ISOLATION_UNAVAILABLE

    try:
        from backend_app.core.ml_safety import TrainingIsolator
    except Exception as exc:  # noqa: BLE001
        logger.warning("ml_safety is unavailable (%s); the fit runs in-process.", exc)
        return fn(), False

    config = getattr(TrainingIsolator, "_config", None)
    if not getattr(config, "enable_process_isolation", False):
        # The isolator itself says isolation is off. Honour its configuration rather
        # than second-guessing it.
        return fn(), False
    if _ISOLATION_UNAVAILABLE:
        return fn(), False

    try:
        return TrainingIsolator.run_isolated_training(fn), True
    except Exception as exc:  # noqa: BLE001 - the ml_models precedent, verbatim
        _ISOLATION_UNAVAILABLE = True
        logger.warning(
            "Isolated training failed (%s); continuing in the worker process, which is "
            "itself isolated from the API and execution runtimes. Further epochs in this "
            "process will not re-attempt process isolation.",
            exc,
        )
        return fn(), False


# ══════════════════════════════════════════════════════════════════════════
#  12. ONE RUN
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TrainingContext:
    """Everything the 6.5 seam needs, and everything the loop measured.

    Handed to ``TrainingBackend.trainer`` and to ``TrainingBackend.binder`` unchanged, so
    task 6.5 reads the dataset, the splits, the feature schema, the resolved caps and the
    job row from one object rather than re-deriving any of them.
    """

    job: Dict[str, Any]
    config: Dict[str, Any]
    worker_id: str
    inputs: TrainingInputs
    caps: Any
    admission: Any
    seed: int
    epochs_total: int
    batch_size: Optional[int]
    memory: Dict[str, Any] = field(default_factory=dict)
    epoch_metrics: List[Dict[str, Any]] = field(default_factory=list)
    #: ``{"train": {...}, "val": {...}, "test": {...}}``, set from :func:`_score_splits`
    #: immediately before the binder is called and never before the last epoch has run.
    #: Requirement 17.1 puts the three metric sets on the ``model_versions`` row, so the
    #: binder has to be able to read them; carrying them on the context is what lets it,
    #: without this module writing them to ``training_jobs`` and giving the platform two
    #: records of one measurement that can disagree. Empty on any path that did not
    #: complete every epoch, which is what makes "cancellation binds no model" and "a
    #: cancelled run records no metrics" the same structural fact.
    split_metrics: Dict[str, Any] = field(default_factory=dict)
    #: **The client this run is being written with.** The binder has to write to the same
    #: database this module is writing to, and ``run_training_job`` deliberately accepts
    #: an injected ``sb``, so re-deriving one from :func:`_worker_client` inside the binder
    #: could silently address a different store. Carried explicitly instead. In production
    #: it IS :func:`_worker_client`'s service-role singleton, which is what 004d's header
    #: says the ``model_versions`` UPDATE needs.
    sb: Any = None

    # -- convenience accessors the seam will want ------------------------
    @property
    def job_id(self) -> str:
        return str(self.job.get("id") or "")

    @property
    def user_id(self) -> str:
        return str(self.job.get("user_id") or "")

    @property
    def version_id(self) -> str:
        return str(self.job.get("version_id") or "")

    @property
    def node_id(self) -> str:
        return str(self.job.get("node_id") or "")

    @property
    def block_id(self) -> str:
        return str(self.job.get("block_id") or self.config.get("block_id") or "")

    @property
    def dataset(self) -> Any:
        return self.inputs.dataset

    @property
    def splits(self) -> Any:
        return self.inputs.splits

    @property
    def spec(self) -> Any:
        return self.inputs.spec

    @property
    def feature_schema(self) -> Any:
        return self.inputs.feature_schema


@dataclass(frozen=True)
class TrainingRunResult:
    """What one call to :func:`run_training_job` did, for a caller and for a test."""

    job_id: str
    status: str
    claimed: bool
    failure_reason: Optional[str] = None
    epochs_completed: int = 0
    epochs_total: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)
    model_version: Optional[Dict[str, Any]] = None

    @property
    def cancelled(self) -> bool:
        return self.status == STATUS_CANCELLED

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAILED

    @property
    def completed(self) -> bool:
        return self.status == STATUS_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "claimed": self.claimed,
            "failure_reason": self.failure_reason,
            "epochs_completed": self.epochs_completed,
            "epochs_total": self.epochs_total,
            "detail": dict(self.detail),
            "model_version": self.model_version,
        }


async def _cancellation_requested(sb: Any, job_id: str) -> Tuple[bool, Optional[str]]:
    """``(cancel_requested, status)`` read FRESH from the row.

    Read from the database at every epoch boundary and never from the claimed row's
    cached copy: the flag is set by another process (task 6.3's cancel endpoint) *after*
    this job started, so a cached value can only ever say "no".

    A read that fails answers ``(False, None)`` - a transient database blip must not
    cancel a healthy run - and the ``status`` is returned alongside so the caller can
    also notice that the job stopped being ``RUNNING`` underneath it, which is what
    happens when the stale-heartbeat reaper has already taken it.
    """
    row = await _read_job(sb, job_id)
    if row is None:
        return False, None
    return bool(row.get("cancel_requested")), str(row.get("status") or "")


async def run_training_job(
    job_id: str,
    *,
    sb: Any = None,
    worker_id: Optional[str] = None,
    registry: Any = None,
    now: Optional[datetime] = None,
) -> TrainingRunResult:
    """Claim ``job_id``, run it, and end it truthfully. The worker entry point.

    ``design.md`` -> ``PROCEDURE run_training_job(job_id)``, step for step, with the two
    strengthenings and one deviation noted below.

    ORDER, AND WHY IT IS THIS ORDER
    -------------------------------
    1. **Claim** (atomic; sets ``RUNNING`` + heartbeat). A job that cannot be claimed
       returns ``claimed=False`` and nothing is written - "already claimed or cancelled".
    2. **Rebuild the inputs** from the recorded configuration, through task 6.3's own
       functions.
    3. **Re-check the caps** (Requirement 16.4). ``DEFER`` releases the claim and leaves
       the job ``QUEUED`` (Requirement 16.5); ``CapExceeded`` fails it with
       ``CAP_EXCEEDED``.
    4. **Memory bounds** through ``MemoryMonitor``, then the whole loop inside
       ``DeterministicEnforcer.deterministic_context(seed)`` (Requirement 16.6). The seed
       is the one recorded in ``config``, so a rerun of this row is the same run.
    5. **The epoch loop.** At every boundary, in this order:
         a. ``cancel_requested`` -> ``CANCELLED``, **no model bound** (15.7). Checked
            before the clock so an author's explicit stop is recorded as a cancellation
            rather than as a duration failure - the row then says what the author asked
            for.
         b. the job is still ``RUNNING`` and still ours -> otherwise stand down without
            writing, because the reaper has already recorded ``WORKER_LOST`` and
            overwriting it would resurrect a job nobody is running.
         c. elapsed > permitted -> ``FAILED`` / ``MAX_DURATION_EXCEEDED`` (15.8).
         d. fit one epoch, isolated.
         e. the clock again. *This is a strengthening of the pseudocode*, which checks
            only at the top of the loop: a single long epoch would otherwise overrun the
            cap by any margin and still finish, and Requirement 15.8 is about elapsed
            duration, not about loop iterations.
         f. persist the epoch (progress from completed epochs only) and publish it.
    6. **Score the splits**, then hand the fitted model to the 6.5 seam. ``COMPLETED`` is
       written **only after the binder returns**, matching the pseudocode's ordering:
       ``persist_artifact`` -> ``insert_model_version`` -> ``bind`` -> ``set_status(job,
       COMPLETED)``. With no binder installed the run ends ``FAILED`` /
       ``MODEL_PERSISTENCE_UNAVAILABLE``, which is the honest report of a run that
       produced no bound model.
    7. **On any exception**, ``FAILED`` with :func:`classify_failure`'s classified reason
       and the version left un-deployable (15.10).

    Never raises for a training failure - that is what the status row is for. It will
    propagate a programming error (a bad lifecycle name, an unclassified reason), because
    those are defects in this module rather than outcomes of a run.
    """
    identity = worker_id or new_worker_id()
    if sb is None:
        sb = await _worker_client()

    job = await claim_training_job(sb, job_id, identity, now=now)
    if job is None:
        return TrainingRunResult(job_id=str(job_id), status="", claimed=False)

    config = dict(job.get("config") or {})
    epochs_total = int(job.get("epochs_total") or config.get("epochs") or 0)
    seed = int(
        config.get("seed") if config.get("seed") is not None else S.DEFAULT_TRAINING_SEED
    )
    batch_size = config.get("batch_size")
    batch_size = None if batch_size is None else int(batch_size)

    await S.publish_training_event(
        str(job.get("user_id") or ""),
        "training.started",
        {
            "job_id": str(job.get("id") or ""),
            "version_id": job.get("version_id"),
            "node_id": job.get("node_id"),
            "status": STATUS_RUNNING,
            "epochs_total": epochs_total,
            "worker_id": identity,
        },
    )

    try:
        return await _run_claimed_job(
            sb,
            job,
            identity,
            config=config,
            epochs_total=epochs_total,
            seed=seed,
            batch_size=batch_size,
            registry=registry,
        )
    except (UnclassifiedFailureReason, ValueError) as defect:
        # A defect in this module, not an outcome of the run. Still record the job as
        # failed - leaving it RUNNING forever would be worse - and then re-raise so the
        # defect is visible rather than absorbed into a status column.
        await _finish(
            sb,
            job,
            status=STATUS_FAILED,
            worker_id=identity,
            failure_reason=FAILURE_TRAINING_RUNTIME_ERROR,
            lifecycle_state=FAILED_LIFECYCLE_STATE,
            event="training.failed",
            detail={"failure": type(defect).__name__, "message": str(defect)[:2000]},
        )
        raise
    except BaseException as exc:  # noqa: BLE001 - every failure is classified, none escapes
        reason, detail = classify_failure(exc)
        logger.warning(
            "Training job %s failed: %s (%s)", job.get("id"), reason, detail.get("message")
        )
        await _finish(
            sb,
            job,
            status=STATUS_FAILED,
            worker_id=identity,
            failure_reason=reason,
            lifecycle_state=FAILED_LIFECYCLE_STATE,
            event="training.failed",
            detail={"reason": reason, **detail},
        )
        result = TrainingRunResult(
            job_id=str(job.get("id") or ""),
            status=STATUS_FAILED,
            claimed=True,
            failure_reason=reason,
            epochs_total=epochs_total,
            detail=detail,
        )
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            # The process is going away. The row is now honest; let the signal through.
            raise
        return result


async def _run_claimed_job(
    sb: Any,
    job: Dict[str, Any],
    identity: str,
    *,
    config: Dict[str, Any],
    epochs_total: int,
    seed: int,
    batch_size: Optional[int],
    registry: Any,
) -> TrainingRunResult:
    """The body of a claimed run. Raises; :func:`run_training_job` classifies."""
    job_id = str(job.get("id") or "")

    # ── 2. Rebuild the inputs, through task 6.3's own functions ──────────
    version_row = await load_version_row(sb, str(job.get("version_id") or ""))
    inputs = await rebuild_training_inputs(job, version_row, registry=registry)

    # ── 3. The caps, re-evaluated before the first epoch (16.4) ──────────
    admission = await recheck_caps(sb, job, stats=inputs.stats, spec=inputs.spec)
    if admission.deferred:
        # Requirement 16.5: hold it in the queue, do not reject it.
        await release_training_job(
            sb,
            job_id,
            identity,
            reason=f"global training capacity is saturated; queue position "
            f"{admission.queue_position}",
        )
        await S.publish_training_event(
            str(job.get("user_id") or ""),
            "training.queued",
            {
                "job_id": job_id,
                "status": STATUS_QUEUED,
                "queue_position": admission.queue_position,
                "deferred": True,
            },
        )
        return TrainingRunResult(
            job_id=job_id,
            status=STATUS_QUEUED,
            claimed=True,
            epochs_total=epochs_total,
            detail={
                "deferred": True,
                "queue_position": admission.queue_position,
                "warnings": list(admission.warnings),
            },
        )

    caps = admission.caps
    # The wall-clock cap: the caps' figure, already intersected with the isolator's live
    # ceiling by ``resolve_caps``. Read the isolator directly too and take the lower of
    # the two, so an operator who tightens isolation after this job was queued tightens
    # this job as well.
    permitted_seconds = int(getattr(caps, "max_duration_seconds", 0) or 0)
    isolator_ceiling = isolation_deadline_seconds()
    if isolator_ceiling is not None and isolator_ceiling > 0:
        permitted_seconds = min(permitted_seconds, isolator_ceiling) if permitted_seconds else isolator_ceiling

    # ── 4. Memory bounds, then the seeded context (16.6) ─────────────────
    memory = assert_memory_bounds(inputs.dataset, batch_size=batch_size)

    context = TrainingContext(
        job=dict(job),
        config=config,
        worker_id=identity,
        inputs=inputs,
        caps=caps,
        admission=admission,
        seed=seed,
        epochs_total=epochs_total,
        batch_size=batch_size,
        memory=memory,
        sb=sb,
    )

    backend = current_training_backend()
    if backend is None:
        raise TrainerUnavailable(
            "No training backend is installed, so no epoch could be fitted. Task 6.5 "
            "registers one with register_training_backend(); until then a claimed job is "
            "recorded as FAILED/TRAINER_UNAVAILABLE rather than reported as complete."
        )

    from backend_app.core.ml_safety import DeterministicEnforcer

    # Requirement 16.6's deterministic seeding, over the WHOLE run rather than around
    # one call: a trainer that drew from the global RNG between epochs would otherwise
    # make the run irreproducible from its recorded seed.
    with DeterministicEnforcer.deterministic_context(seed=seed):
        trainer = backend.trainer(context)
        if trainer is None:
            raise TrainerUnavailable(
                f"Training backend {backend.name or 'unnamed'} produced no trainer for "
                f"block {context.block_id!r}."
            )
        outcome = await _epoch_loop(
            sb,
            job,
            identity,
            context=context,
            trainer=trainer,
            epochs_total=epochs_total,
            permitted_seconds=permitted_seconds,
        )
        if outcome is not None:
            return outcome

        # ── 6. Score the splits, then hand over to the 6.5 seam ─────────
        metrics = _score_splits(trainer)
        # Handed to the binder on the context rather than as an argument, so the seam's
        # signature stays ``binder(ctx, model)``. Task 6.5's row records train, val and
        # test metrics (Requirement 17.1) and this is where they become reachable.
        context.split_metrics = metrics

    if backend.binder is None:
        raise ModelPersistenceUnavailable(
            f"Every one of the {epochs_total} epochs ran, but training backend "
            f"{backend.name or 'unnamed'} installs no binder, so no artifact was stored "
            f"and no model_versions row was written. Task 6.5 owns that step; the job is "
            f"recorded as FAILED rather than COMPLETED because a completed job with no "
            f"bound model is the silent-success shape Requirement 15.10 forbids."
        )

    bound = backend.binder(context, getattr(trainer, "model", None))
    if asyncio.iscoroutine(bound):
        bound = await bound
    if not bound:
        raise ModelPersistenceUnavailable(
            f"Training backend {backend.name or 'unnamed'} bound no model version for "
            f"job {job_id}, so nothing deployable was produced."
        )
    model_version = dict(bound)

    # ``COMPLETED`` only now: after the artifact, the row and the binding, exactly as the
    # design's ordering has it. The version's own transition to READY is task 6.5's, and
    # it belongs there because only the binder knows whether EVERY ml node is bound.
    await _finish(
        sb,
        job,
        status=STATUS_COMPLETED,
        worker_id=identity,
        event="training.completed",
        detail={
            "metrics": metrics,
            "model_version": model_version,
            "split_sizes": inputs.splits.sizes,
        },
    )
    return TrainingRunResult(
        job_id=job_id,
        status=STATUS_COMPLETED,
        claimed=True,
        epochs_completed=epochs_total,
        epochs_total=epochs_total,
        detail={"metrics": metrics, "memory": memory},
        model_version=model_version,
    )


def _score_splits(trainer: Any) -> Dict[str, Any]:
    """``{"train": {...}, "val": {...}, "test": {...}}`` from the trainer, sanitised.

    Scored here and carried on the result rather than written to ``training_jobs``: the
    three metric sets belong in ``model_versions`` (Requirement 17.1), which is task
    6.5's row, and duplicating them into the job would give the platform two records of
    the same measurement that can disagree.
    """
    scored: Dict[str, Any] = {}
    evaluate = getattr(trainer, "evaluate", None)
    if not callable(evaluate):
        return scored
    for split in ("train", "val", "test"):
        try:
            scored[split] = sanitize_epoch_metrics(evaluate(split))
        except Exception as exc:  # noqa: BLE001 - a missing score is not a failed run
            logger.warning("The %s split could not be scored: %s", split, exc)
            scored[split] = {}
    return scored


async def _epoch_loop(
    sb: Any,
    job: Dict[str, Any],
    identity: str,
    *,
    context: TrainingContext,
    trainer: Any,
    epochs_total: int,
    permitted_seconds: int,
) -> Optional[TrainingRunResult]:
    """Drive ``trainer`` for ``epochs_total`` epochs. ``None`` when every epoch ran.

    A non-``None`` return is a terminal outcome the loop reached on its own -
    cancellation, the duration cap, or losing the job to the reaper - and the caller
    returns it unchanged rather than continuing to the model-binding step. That is how
    "bind no model on cancellation" (Requirement 15.7) is structural: there is no path
    from a cancelled loop to the binder.
    """
    job_id = str(job.get("id") or "")
    history: List[Dict[str, Any]] = list(job.get("metrics_history") or [])
    last_heartbeat = time.monotonic()
    completed = 0

    for epoch in range(1, max(0, int(epochs_total)) + 1):
        # ── (a) cancellation, read fresh, before anything else ───────────
        cancel_requested, status_now = await _cancellation_requested(sb, job_id)
        if cancel_requested:
            written = await _finish(
                sb,
                job,
                status=STATUS_CANCELLED,
                worker_id=identity,
                lifecycle_state=FAILED_LIFECYCLE_STATE,
                event="training.cancelled",
                detail={
                    "epoch_current": completed,
                    "cancelled_at_epoch_boundary": epoch,
                },
            )
            logger.info(
                "Training job %s stopped at the boundary before epoch %d because "
                "cancellation was requested; %d of %d epochs had completed and NO model "
                "version was bound.",
                job_id,
                epoch,
                completed,
                epochs_total,
            )
            return TrainingRunResult(
                job_id=job_id,
                status=STATUS_CANCELLED if written is not None else STATUS_RUNNING,
                claimed=True,
                epochs_completed=completed,
                epochs_total=epochs_total,
                detail={"cancelled_at_epoch_boundary": epoch, "model_bound": False},
            )

        # ── (b) do we still hold this job? ──────────────────────────────
        if status_now is not None and status_now != STATUS_RUNNING:
            # The reaper has already recorded WORKER_LOST, or an operator intervened.
            # Writing anything now would resurrect a job nobody is running.
            logger.warning(
                "Training job %s is %s rather than %s, so worker %s is standing down "
                "without writing; %d of %d epochs had completed.",
                job_id,
                status_now,
                STATUS_RUNNING,
                identity,
                completed,
                epochs_total,
            )
            return TrainingRunResult(
                job_id=job_id,
                status=status_now,
                claimed=True,
                epochs_completed=completed,
                epochs_total=epochs_total,
                detail={"stood_down": True, "observed_status": status_now},
            )

        # ── (c) the wall clock, before the epoch ────────────────────────
        _assert_within_duration(job, permitted_seconds)

        # ── (d) one epoch, isolated ─────────────────────────────────────
        started = time.monotonic()
        raw, isolated = run_isolated(lambda e=epoch: trainer.train_epoch(e))
        duration = time.monotonic() - started
        completed = epoch

        metrics = sanitize_epoch_metrics(raw, epoch=epoch)
        metrics.setdefault("duration_seconds", round(duration, 4))
        history.append(metrics)
        context.epoch_metrics.append(dict(metrics))

        # ── (e) the wall clock again ────────────────────────────────────
        # A strengthening of the pseudocode, which checks only at the top of the loop.
        # Requirement 15.8 bounds elapsed DURATION; one epoch that runs for an hour past
        # the cap must not finish just because the loop had already been entered. The
        # epoch's own figures are persisted first, so a job stopped here still reports the
        # work it really did.
        await record_epoch(
            sb,
            job,
            identity,
            epoch=epoch,
            epochs_total=epochs_total,
            metrics=metrics,
            history=history,
        )
        # The honest ETA, from task 6.6's rule and not a second copy of it. Imported
        # locally because ``training_status`` reads this module's vocabulary at import
        # time, so a module-level import here would be a cycle. ``design.md``'s progress
        # frame carries ``eta_seconds``, and computing it from the same function the
        # status endpoint uses is what stops the socket and the endpoint disagreeing
        # about when an estimate is trustworthy.
        from backend_app.backend import training_status as TS

        eta = TS.reliable_eta(
            status=STATUS_RUNNING,
            epoch_current=epoch,
            epochs_total=epochs_total,
            durations=TS.epoch_durations(history),
        )
        await S.publish_training_event(
            str(job.get("user_id") or ""),
            "training.progress",
            {
                "job_id": job_id,
                "version_id": job.get("version_id"),
                "node_id": job.get("node_id"),
                "status": STATUS_RUNNING,
                "epoch": epoch,
                "epochs_total": epochs_total,
                # Derived from COMPLETED epochs, never from a timer (Requirement 15.4).
                "progress": min(1.0, epoch / max(1, epochs_total)),
                "loss": metrics.get("loss"),
                "val_loss": metrics.get("val_loss"),
                # NULL until three epochs have completed and their durations are stable
                # (Requirements 15.5, 15.6). Never a guess.
                "eta_seconds": eta["eta_seconds"],
                "eta_state": eta["eta_state"],
                "isolated": isolated,
            },
        )
        _assert_within_duration(job, permitted_seconds)

        # A heartbeat between epochs as well, so a model family whose single epoch is
        # long-running does not look lost while it is working.
        if time.monotonic() - last_heartbeat >= heartbeat_interval_seconds():
            await write_heartbeat(sb, job_id, identity)
            last_heartbeat = time.monotonic()

    return None


def _assert_within_duration(job: Mapping[str, Any], permitted_seconds: int) -> None:
    """Raise :class:`MaxDurationExceeded` when the job has outrun its permitted clock.

    A permitted value of zero or less means "no readable ceiling", and the check is
    skipped with that stated rather than silently treating it as "no time at all", which
    would fail every job the moment a ceiling became unreadable.
    """
    if permitted_seconds is None or int(permitted_seconds) <= 0:
        return
    elapsed = elapsed_seconds(job)
    if elapsed > float(permitted_seconds):
        raise MaxDurationExceeded(elapsed, int(permitted_seconds))


# ══════════════════════════════════════════════════════════════════════════
#  13. THE PROCESS
# ══════════════════════════════════════════════════════════════════════════


async def _worker_client() -> Any:
    """The worker's database client: the service role, because there is no JWT here.

    A worker holds no request and so no user token, which means
    ``dependencies.create_request_supabase_async`` (the RLS-scoped client every API path
    uses) has nothing to authenticate with. It therefore uses the existing service-role
    singleton, ``core.supabase_connection.get_supabase_connection``, and that has three
    consequences worth stating rather than leaving to be discovered:

    * **Tenant scoping stops being free and becomes explicit.** Every read and write in
      this module is keyed on the specific ``job.id`` it claimed, and the job carries its
      own ``user_id``; nothing here selects across users except
      :func:`count_all_training_jobs`, whose whole purpose is a fleet-wide count and
      which returns counts, never rows.
    * **It is what makes 004d's "no UPDATE policy on model_versions" workable** for task
      6.5, which the migration header calls out as needing the service role.
    * **It is why the worker must never be started inside the API process** - see
      :func:`run_worker`.

    ``None`` when Supabase is not configured, which every caller here degrades on.
    """
    try:
        from backend_app.core.supabase_connection import get_supabase_connection

        connection = get_supabase_connection()
        client = connection.get_client() if connection is not None else None
        if client is None:
            logger.warning(
                "The training worker has no Supabase service-role client, so no job can "
                "be claimed. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("The training worker could not obtain a database client: %s", exc)
        return None


async def next_queued_job_id(sb: Any, *, limit: int = 25) -> Optional[str]:
    """The oldest claimable ``QUEUED`` job id, read from the authoritative queue.

    The table, not Redis. ``idx_tj_user_status`` indexes ``(user_id, status)`` and
    ``idx_tj_created`` orders by ``created_at DESC``; this asks for the queued set and
    takes the OLDEST, so a job whose Redis hint was lost is picked up on the next poll
    instead of waiting forever. That is the property that makes
    ``enqueue_training_job``'s best-effort behaviour safe.

    Returns ``None`` when nothing is queued or the table is absent.
    """
    if sb is None:
        return None
    try:
        query = (
            sb.table(TRAINING_JOBS_TABLE)
            .select("id,created_at")
            .eq("status", STATUS_QUEUED)
            .limit(int(limit))
            .execute()
        )
        result = await S._execute(query)
    except Exception as exc:  # noqa: BLE001
        if _degraded(exc, "scan for queued training jobs"):
            return None
        raise
    if S.is_missing_training_table_error(Exception(S._result_error_text(result) or "")):
        return None

    rows = [dict(row or {}) for row in ((getattr(result, "data", None) or []) if result else [])]
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return str(rows[0].get("id") or "") or None


async def pop_queue_hint() -> Optional[str]:
    """A job id from the Redis wake-up queue, or ``None``. A HINT, never exclusion.

    ``TRAINING_QUEUE_NAME`` is task 6.3's constant. A popped id is not a claim: the claim
    is the conditional UPDATE in :func:`claim_training_job`, so a duplicated or stale hint
    costs one failed claim and nothing else. Never raises - a Redis that is down means the
    worker falls back to scanning the table, which is where the truth is.
    """
    try:
        from backend_app.backend.redis_manager import get_redis_manager

        manager = await get_redis_manager()
        if manager is None:
            return None
        item = await manager.queue_pop(S.TRAINING_QUEUE_NAME)
        if not item:
            return None
        return str(item.get("job_id") or "") or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("No training queue hint could be read (%s); scanning the table.", exc)
        return None


class TrainingWorker:
    """The polling loop around :func:`run_training_job`.

    Deliberately NOT a ``core.worker_base.WorkerBase`` subclass. ``WorkerBase`` gives a
    poll loop, a health heartbeat and a backpressure hook, and this worker needs none of
    those three: its liveness signal is the per-job ``training_jobs.last_heartbeat`` column
    that Requirement 15.9's detector reads (a worker-level health ping would not tell you
    whether a *job* is progressing), and its backpressure is the global concurrency cap in
    Requirement 16.5, applied per job by :func:`recheck_caps` rather than per worker. Adding
    a second heartbeat and a second throttle would give the platform two answers to each
    question. The lifecycle surface is kept identical - ``start()``, ``stop()``,
    ``process_iteration()`` - so it is operated the same way as the others.

    One job at a time, on purpose: concurrency belongs to the number of worker processes,
    which is what the global cap counts.
    """

    def __init__(
        self,
        *,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        reap_interval: float = 30.0,
        sb: Any = None,
        registry: Any = None,
    ) -> None:
        self.worker_id = worker_id or new_worker_id()
        self.poll_interval = float(poll_interval)
        self.reap_interval = float(reap_interval)
        self.registry = registry
        self.running = False
        self._sb = sb
        self._task: Optional[asyncio.Task] = None
        self._last_reap = 0.0
        self.jobs_run = 0
        self.jobs_reaped = 0

    async def client(self) -> Any:
        if self._sb is None:
            self._sb = await _worker_client()
        return self._sb

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop(), name=f"{self.worker_id}_loop")
        logger.info("Training worker %s started.", self.worker_id)

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        logger.info(
            "Training worker %s stopped after running %d job(s) and reaping %d.",
            self.worker_id,
            self.jobs_run,
            self.jobs_reaped,
        )

    async def _loop(self) -> None:
        while self.running:
            try:
                worked = await self.process_iteration()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a poll must never kill the loop
                logger.exception("Training worker %s poll failed: %s", self.worker_id, exc)
                worked = False
            if not worked:
                await asyncio.sleep(self.poll_interval)

    async def process_iteration(self) -> bool:
        """One poll: reap stale jobs when due, then run at most one job.

        Returns whether any work was done, so the caller can skip the sleep and drain a
        backlog rather than pacing it at one job per poll interval.
        """
        sb = await self.client()
        if sb is None:
            return False

        if time.monotonic() - self._last_reap >= self.reap_interval:
            self._last_reap = time.monotonic()
            reaped = await reap_stale_jobs(sb)
            self.jobs_reaped += len(reaped)

        # The hint first, because it is cheap and usually right; the table second,
        # because it is the truth.
        job_id = await pop_queue_hint()
        if job_id is None:
            job_id = await next_queued_job_id(sb)
        if job_id is None:
            return False

        result = await run_training_job(
            job_id, sb=sb, worker_id=self.worker_id, registry=self.registry
        )
        if result.claimed:
            self.jobs_run += 1
        return True


async def _main() -> None:  # pragma: no cover - process entry point
    import logging as _logging

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    _logging.basicConfig(
        level=_logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    worker = TrainingWorker()
    await worker.start()
    logger.info("TrainingWorker running - Ctrl-C to stop")
    try:
        while True:
            await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await worker.stop()


def run_worker() -> None:  # pragma: no cover - process entry point
    """Run the training worker as its own process.

    ``python -m backend_app.backend.training_worker``.

    **Deliberately not wired into ``main.py``'s startup.** Requirement 16.6 asks for
    process isolation, and ``TrainingIsolator``'s own docstring says training must never
    interfere with the execution runtime, saturate Redis or block orchestration. Starting
    this loop inside the API process would put a fitting job in the same interpreter as
    the request handlers and the execution runtime, which is the one thing the isolation
    requirement is about. It is a separate entry point, and it is meant to be deployed as
    a separate process.
    """
    asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover - process entry point
    run_worker()
