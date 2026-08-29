# -*- coding: utf-8 -*-
"""
backend/training_status.py - truthful training status reporting.

``design.md`` -> Training workflow -> "Status surface (``GET /training/jobs/{id}``)" and
the Reliable-ETA rule, and spec task 6.6. Requirements 14.9, 15.2, 15.3, 15.4, 15.5,
15.6.

WHAT THIS MODULE OWNS
---------------------
The **read** half of a training job, and nothing else:

1. :func:`public_training_job` - the ONLY projection of a ``training_jobs`` row that is
   returned to a client. Requirement 15.3's whole list, in one document.
2. :func:`derive_progress` - the progress fraction, computed from **completed epochs**
   and nothing else (Requirement 15.4). Never from a timer, never from elapsed wall
   clock, never from a stored value this module did not check.
3. :func:`reliable_eta` - the honest ETA: absent until at least
   :data:`ETA_MIN_COMPLETED_EPOCHS` epochs have completed **and** their measured
   durations have a coefficient of variation below :data:`ETA_MAX_DURATION_CV`
   (Requirements 15.5, 15.6). When either condition fails the answer is ``None`` with a
   machine-readable reason, never a guess.
4. :func:`is_cancellable` - the cancellable flag, derived from the status vocabulary
   rather than stored, so it cannot disagree with what the cancel endpoint will do.
5. :func:`training_job_report` / :func:`list_training_job_reports` - the two
   ownership-checked reads behind ``GET /training/jobs/{job_id}`` and
   ``GET /training/jobs?version_id=``.

THIS MODULE WRITES NOTHING
--------------------------
Not one function here issues an INSERT or an UPDATE. That matters for a specific
reason: 004d attaches **no** ``BEFORE UPDATE`` trigger to ``training_jobs``, so every
writer in this codebase has to set ``updated_at`` itself, and the safest way for a
reporting surface to hold that invariant is to have no write to forget it on. A read
that repaired a row it thought was wrong would also be a read that changed what a
concurrent worker was about to write.

REUSED, NOT REBUILT
-------------------
* the status vocabulary and the live set   -> ``strategy_service`` (task 6.3)
* the failure vocabulary and the scalar
  rule for ``metrics_history``             -> ``training_worker`` (task 6.4)
* the model version projection             -> ``model_versioning`` (task 6.5)
* the request-scoped client seam           -> ``strategy_service.create_request_supabase_async``

``metrics_history`` GOES OUT THROUGH THE SAME SANITISER IT CAME IN THROUGH
-------------------------------------------------------------------------
004d's header states that ``training_jobs.metrics_history`` holds per-epoch scalars
only - never predictions, never feature values - that SQL cannot enforce it, and that
"the enforcement itself belongs to the writer in tasks 6.4 and 6.6". Task 6.4 is the
writer; this module is the **reader**, and it applies the identical rule on the way out
with the identical function, :func:`training_worker.sanitize_epoch_metrics`. Two
reasons, both load-bearing: a row written by an older worker, by a migration, or by
hand is not covered by the write-side check at all; and ``loss`` / ``val_loss`` are
separate columns that a lying writer could have filled with a vector or a NaN, which
would leak model output or break the JSON encoding of the whole response. Both go
through the sanitiser here.

NO ARTIFACT REFERENCE, EVER
---------------------------
A completed job's bound model is reported as
:func:`model_versioning.public_model_version`, which builds a new mapping from named
keys and therefore has no ``artifact_uri`` by construction. This module never reads that
column, never copies a ``model_versions`` row, and offers no way to reach the bytes -
that is the signed, ownership-checked exchange task 6.5 landed.

TENANT SCOPING IS DOUBLE, AND ABSENCE IS THE ANSWER FOR "NOT YOURS"
-------------------------------------------------------------------
Every read goes through the caller's own request-scoped client (so ``tj_owner_select``
applies) **and** carries an explicit ``.eq("user_id", ...)``, which is the pattern every
other Strategy Builder read uses. Another tenant's job is reported as **not found**, not
as forbidden, so the endpoint is not an existence oracle for job identifiers
(Requirement 21.4).

THE TABLES ARE NOT APPLIED
--------------------------
``004d_training_and_models.sql`` is unapplied in this environment and there is no local
PostgreSQL. Every path here degrades with a warning **naming that file** and answers
"no such job" / "no jobs" rather than raising, so a client in an unmigrated environment
gets an honest answer and never a 500.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend import strategy_service as S
from backend_app.backend import training_worker as W

logger = logging.getLogger("TrainingStatus")

#: Bumped when a reported field, the progress rule or the ETA rule changes, so a client
#: can tell which contract it is reading.
TRAINING_STATUS_VERSION = "1.0.0"

__all__ = [
    "TRAINING_STATUS_VERSION",
    # vocabulary
    "TRAINING_JOB_STATES",
    "TRAINING_JOB_LIVE_STATES",
    "ETA_MIN_COMPLETED_EPOCHS",
    "ETA_MAX_DURATION_CV",
    "ETA_AVAILABLE",
    "ETA_ABSENT_NOT_RUNNING",
    "ETA_ABSENT_UNKNOWN_TOTAL",
    "ETA_ABSENT_TOO_FEW_EPOCHS",
    "ETA_ABSENT_NO_DURATIONS",
    "ETA_ABSENT_UNSTABLE_DURATIONS",
    "ETA_STATES",
    # the pure rules
    "epoch_durations",
    "duration_coefficient_of_variation",
    "reliable_eta",
    "derive_progress",
    "is_cancellable",
    "scalar_metrics_history",
    "public_training_job",
    # the reads
    "TrainingJobNotFound",
    "load_owned_training_job",
    "training_job_report",
    "list_training_job_reports",
]


# ══════════════════════════════════════════════════════════════════════════
#  1. VOCABULARY - reused, never restated
# ══════════════════════════════════════════════════════════════════════════

#: ``chk_tj_status``, through task 6.3's constant. Requirement 15.2's five states.
TRAINING_JOB_STATES = S.TRAINING_JOB_STATES

#: The two states ``uq_tj_active_per_node`` scopes its uniqueness to. A job in one of
#: these is LIVE, which is exactly what makes it cancellable.
TRAINING_JOB_LIVE_STATES = S.TRAINING_JOB_LIVE_STATES

#: Requirement 15.5, verbatim: "WHILE fewer than three epochs have completed, THE
#: Training_Service SHALL report the estimated remaining time as absent."
ETA_MIN_COMPLETED_EPOCHS = 3

#: Requirement 15.6, verbatim: "WHILE measured epoch durations have a coefficient of
#: variation of 0.35 or greater, ... report the estimated remaining time as absent." The
#: comparison is therefore ``cv < ETA_MAX_DURATION_CV`` to publish, and a cv of exactly
#: 0.35 is absent.
ETA_MAX_DURATION_CV = 0.35

#: Why an ETA is what it is. A machine code rather than a sentence, because the builder
#: renders "estimating…" for every absent case and a client must not have to parse prose
#: to tell "not enough epochs yet" from "this job is not running".
ETA_AVAILABLE = "AVAILABLE"
ETA_ABSENT_NOT_RUNNING = "ABSENT_NOT_RUNNING"
ETA_ABSENT_UNKNOWN_TOTAL = "ABSENT_UNKNOWN_EPOCHS_TOTAL"
ETA_ABSENT_TOO_FEW_EPOCHS = "ABSENT_TOO_FEW_EPOCHS"
ETA_ABSENT_NO_DURATIONS = "ABSENT_NO_MEASURED_DURATIONS"
ETA_ABSENT_UNSTABLE_DURATIONS = "ABSENT_UNSTABLE_EPOCH_DURATIONS"

ETA_STATES: frozenset = frozenset(
    {
        ETA_AVAILABLE,
        ETA_ABSENT_NOT_RUNNING,
        ETA_ABSENT_UNKNOWN_TOTAL,
        ETA_ABSENT_TOO_FEW_EPOCHS,
        ETA_ABSENT_NO_DURATIONS,
        ETA_ABSENT_UNSTABLE_DURATIONS,
    }
)

#: The key the worker records a completed epoch's wall clock under. One spelling, shared
#: with ``training_worker.EPOCH_METRIC_KEYS``.
DURATION_KEY = "duration_seconds"


class TrainingJobNotFound(Exception):
    """No training job with that identifier belongs to this user.

    The same answer for "does not exist", "belongs to another tenant" and "the migration
    that creates the table has not been applied", because distinguishing them tells a
    caller something about a resource they do not own (Requirement 21.4).
    """


# ══════════════════════════════════════════════════════════════════════════
#  2. THE SCALAR RULES
# ══════════════════════════════════════════════════════════════════════════


def _int_or_none(value: Any) -> Optional[int]:
    """``value`` as an ``int``, or ``None`` when it is not a whole number at all."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _finite_float(value: Any) -> Optional[float]:
    """``value`` as a finite ``float``, or ``None``.

    NaN and infinity become ``None`` rather than travelling: neither is a JSON number,
    and a response that fails to encode reports nothing at all.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def scalar_metrics_history(history: Any) -> List[Dict[str, Any]]:
    """``metrics_history`` as per-epoch scalars, re-checked on the way out.

    Every entry goes through :func:`training_worker.sanitize_epoch_metrics` - the same
    function task 6.4 writes through - so a row written by an older worker, a migration
    or a hand-edit cannot serve a prediction vector or a feature value to a client. See
    the module docstring for why the read side applies the rule as well as the write
    side.
    """
    if not isinstance(history, (list, tuple)):
        if history is not None:
            logger.warning(
                "metrics_history was %s rather than a list; it is reported as empty "
                "rather than guessed at.",
                type(history).__name__,
            )
        return []
    entries: List[Dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, Mapping):
            logger.warning(
                "A metrics_history entry was %s rather than a mapping and was dropped.",
                type(entry).__name__,
            )
            continue
        entries.append(W.sanitize_epoch_metrics(entry))
    return entries


def epoch_durations(history: Any) -> Tuple[float, ...]:
    """The measured wall clock of each completed epoch, in order.

    Only the entries that actually carry a usable ``duration_seconds`` are returned. A
    missing, non-numeric, non-finite or negative duration is **not** substituted with a
    mean or a zero: the ETA rule counts *measured* durations (Requirement 15.6), so an
    unmeasured epoch has to be absent from the sample rather than imputed into it.

    ``0.0`` is kept. An epoch really can complete inside the clock's resolution, and
    dropping it would make a fast run look unmeasured.
    """
    durations: List[float] = []
    for entry in scalar_metrics_history(history):
        value = _finite_float(entry.get(DURATION_KEY))
        if value is None or value < 0.0:
            continue
        durations.append(value)
    return tuple(durations)


def duration_coefficient_of_variation(
    durations: Sequence[float],
) -> Optional[float]:
    """``stdev / mean`` over ``durations``, or ``None`` when it is not defined.

    ``None`` for fewer than two samples (no dispersion to measure) and for a mean of
    zero (the ratio is undefined, and reporting ``0.0`` would read as "perfectly
    stable" for a sample that says nothing).

    The **sample** standard deviation is used, not the population one. Epoch durations
    are a sample of the run's behaviour rather than its whole population, and the
    ``n - 1`` denominator is the larger of the two - so in the borderline case this rule
    withholds an estimate rather than publishing one. Requirement 15.6 does not name a
    denominator; withholding is the direction that cannot mislead.
    """
    values = [float(d) for d in durations]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean <= 0.0:
        return None
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) / mean


def reliable_eta(
    *,
    status: str,
    epoch_current: Optional[int],
    epochs_total: Optional[int],
    durations: Sequence[float],
) -> Dict[str, Any]:
    """The honest remaining-time estimate, or an absence with its reason.

    ``design.md``: "``eta_seconds`` is ``NULL`` until at least three epochs have
    completed and their durations have a coefficient of variation below 0.35. Otherwise
    the UI shows 'estimating…'."

    The five ways this answers absent, and why each one is not a guess:

    ==============================  ====================================================
    ``eta_state``                   what it means
    ==============================  ====================================================
    ``ABSENT_NOT_RUNNING``          the job is ``QUEUED`` or finished. A queued job's
                                    remaining time depends on a queue this function
                                    cannot see; a finished job has none.
    ``ABSENT_UNKNOWN_EPOCHS_TOTAL`` no positive ``epochs_total``, so there is no
                                    denominator and no count of remaining work.
    ``ABSENT_TOO_FEW_EPOCHS``       fewer than three epochs have completed
                                    (Requirement 15.5).
    ``ABSENT_NO_MEASURED_DURATIONS`` three epochs completed but fewer than three of them
                                    recorded a duration, so there is nothing to average.
    ``ABSENT_UNSTABLE_EPOCH_DURATIONS`` the coefficient of variation is 0.35 or greater
                                    (Requirement 15.6), or is undefined.
    ==============================  ====================================================

    ``eta_seconds`` is ``mean(duration) * epochs_remaining`` and nothing more: no
    smoothing, no decay, no allowance for the artifact write. It is published only in the
    regime the requirements declare it trustworthy in.
    """
    measured = tuple(float(d) for d in durations)
    completed = _int_or_none(epoch_current) or 0
    total = _int_or_none(epochs_total)
    cv = duration_coefficient_of_variation(measured)

    detail: Dict[str, Any] = {
        "completed_epochs": max(0, completed),
        "measured_epochs": len(measured),
        "required_epochs": ETA_MIN_COMPLETED_EPOCHS,
        "duration_cv": None if cv is None else round(cv, 6),
        "max_duration_cv": ETA_MAX_DURATION_CV,
        "mean_epoch_seconds": (
            round(sum(measured) / len(measured), 6) if measured else None
        ),
        "epochs_remaining": (
            None if total is None or total <= 0 else max(0, total - max(0, completed))
        ),
    }

    def answer(state: str, seconds: Optional[float] = None) -> Dict[str, Any]:
        return {"eta_seconds": seconds, "eta_state": state, "eta_detail": detail}

    if str(status or "").upper() != W.STATUS_RUNNING:
        return answer(ETA_ABSENT_NOT_RUNNING)
    if total is None or total <= 0:
        return answer(ETA_ABSENT_UNKNOWN_TOTAL)
    if completed < ETA_MIN_COMPLETED_EPOCHS:
        return answer(ETA_ABSENT_TOO_FEW_EPOCHS)
    if len(measured) < ETA_MIN_COMPLETED_EPOCHS:
        return answer(ETA_ABSENT_NO_DURATIONS)
    if cv is None or cv >= ETA_MAX_DURATION_CV:
        return answer(ETA_ABSENT_UNSTABLE_DURATIONS)

    remaining = max(0, total - completed)
    mean = sum(measured) / len(measured)
    return answer(ETA_AVAILABLE, round(mean * remaining, 3))


def derive_progress(
    epoch_current: Optional[int], epochs_total: Optional[int]
) -> Optional[float]:
    """``completed / total``, clamped to ``[0, 1]``. Requirement 15.4.

    Derived from **completed epochs only**. There is deliberately no elapsed-time term,
    no per-stage weighting and no "nearly there" floor: every one of those would move a
    progress bar for a job that had not finished an epoch, which is the fabricated
    progress the requirement forbids.

    ``None`` when ``epochs_total`` is absent or not positive. That is the honest answer
    for a row with no denominator - reporting ``0.0`` would claim no work had been done
    when some may have been, and ``chk_tj_progress``'s own range is ``[0, 1]``, which
    says nothing about an unknown total.
    """
    total = _int_or_none(epochs_total)
    if total is None or total <= 0:
        return None
    completed = max(0, _int_or_none(epoch_current) or 0)
    return min(1.0, max(0.0, completed / total))


def is_cancellable(status: Any) -> bool:
    """Whether a job in ``status`` can still be cancelled.

    Derived from the status vocabulary, never read from the row: a live job
    (``QUEUED`` or ``RUNNING`` - the two states ``uq_tj_active_per_node`` scopes to) is
    cancellable and a terminal one is not. Because this reads the same constant
    ``StrategyService.request_job_cancellation`` filters on, the flag cannot promise a
    cancellation the cancel endpoint would answer 409 to.
    """
    return str(status or "").upper() in TRAINING_JOB_LIVE_STATES


def _reported_failure_reason(row: Mapping[str, Any]) -> Optional[str]:
    """The stored ``failure_reason``, echoed, with anything unclassified flagged in the log.

    Not coerced and not dropped: the row is the record of what happened, and rewriting a
    reason this module does not recognise into one it does would be inventing the
    classification Requirement 15.10 asks to be *recorded*. An unrecognised value is
    logged instead, because it means something wrote outside
    :data:`training_worker.FAILURE_REASONS`.
    """
    reason = str(row.get("failure_reason") or "").strip()
    if not reason:
        return None
    if reason not in W.FAILURE_REASONS:
        logger.warning(
            "Training job %s records failure_reason %r, which is not one of the "
            "classified reasons in training_worker.FAILURE_REASONS. It is reported "
            "verbatim rather than rewritten.",
            row.get("id"),
            reason,
        )
    return reason


# ══════════════════════════════════════════════════════════════════════════
#  3. THE PROJECTION (Requirement 15.3)
# ══════════════════════════════════════════════════════════════════════════


def public_training_job(
    row: Mapping[str, Any], *, model_version: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """The ONLY projection of a ``training_jobs`` row that is returned to a client.

    Requirement 15.3's whole list in one document - status, dataset rows, usable rows,
    feature columns, feature names, split sizes, model, epochs total, current epoch,
    training loss, validation loss, progress, the cancellable flag and the failure
    reason - plus ``design.md``'s nullable ``eta_seconds``.

    Built from named keys rather than by copying the row, for the reason
    :func:`model_versioning.public_model_version` is: a projection that *removes* keys is
    one refactor away from not removing them. Three columns are therefore absent by
    construction rather than by filtering:

    * ``config`` - the recorded training configuration. It is the provenance record
      (Requirement 15.14) and it is not on Requirement 15.3's list. Task 6.5 already
      publishes a **whitelisted** projection of it as a model version's
      ``hyperparameters``, so there is one path that decides which of its keys a client
      may see and it is not this one.
    * ``worker_id`` and ``last_heartbeat`` - operational identity of the machine holding
      the job. A client has no use for it and it names internal hosts.

    ``model_version`` is task 6.5's :func:`public_model_version` projection, passed in by
    the caller that resolved it, or ``None``. ``None`` means "not reported here", which
    is why it travels beside an explicit ``model_bound`` flag: a job that completed with
    no readable model row must not look identical to one whose model the caller simply
    did not ask for.
    """
    status = str(row.get("status") or "").upper()
    if status and status not in TRAINING_JOB_STATES:
        logger.warning(
            "Training job %s records status %r, which is not one of %s (chk_tj_status). "
            "It is reported verbatim.",
            row.get("id"),
            status,
            sorted(TRAINING_JOB_STATES),
        )

    epoch_current = max(0, _int_or_none(row.get("epoch_current")) or 0)
    epochs_total = _int_or_none(row.get("epochs_total"))
    history = scalar_metrics_history(row.get("metrics_history"))
    durations = epoch_durations(row.get("metrics_history"))

    # ``loss`` and ``val_loss`` are separate DOUBLE PRECISION columns, so the write-side
    # scalar rule never saw them as metrics. They go through the same sanitiser here, so
    # a vector or a NaN in either cannot reach a client or break the encoding.
    losses = W.sanitize_epoch_metrics(
        {"loss": row.get("loss"), "val_loss": row.get("val_loss")}
    )

    progress = derive_progress(epoch_current, epochs_total)
    stored_progress = _finite_float(row.get("progress"))
    if (
        progress is not None
        and stored_progress is not None
        and abs(stored_progress - progress) > 1e-6
    ):
        # The derived figure is what is reported. The stored one is a cache of the same
        # arithmetic and a disagreement means a writer computed it differently, which is
        # worth an operator's attention but must not change the answer.
        logger.warning(
            "Training job %s stores progress %s but %d of %s completed epochs derive "
            "%s. The derived figure is reported (Requirement 15.4).",
            row.get("id"),
            stored_progress,
            epoch_current,
            epochs_total,
            progress,
        )

    eta = reliable_eta(
        status=status,
        epoch_current=epoch_current,
        epochs_total=epochs_total,
        durations=durations,
    )

    feature_names = row.get("feature_names")
    return {
        # -- identity -----------------------------------------------------
        "job_id": str(row.get("id") or ""),
        "strategy_id": str(row.get("strategy_id") or ""),
        "version_id": str(row.get("version_id") or ""),
        "node_id": str(row.get("node_id") or ""),
        # Requirement 15.3's "model block identifier". Reported under both names: the
        # design's status surface calls it ``model``, and every other Strategy Builder
        # document calls a block's identifier ``block_id``.
        "model": str(row.get("block_id") or ""),
        "block_id": str(row.get("block_id") or ""),
        # -- status (15.2) ------------------------------------------------
        "status": status,
        "failure_reason": _reported_failure_reason(row),
        "cancellable": is_cancellable(status),
        "cancel_requested": bool(row.get("cancel_requested")),
        # -- the dataset the run measured (15.3) --------------------------
        "dataset_rows": _int_or_none(row.get("dataset_rows")),
        "usable_rows": _int_or_none(row.get("usable_rows")),
        "feature_columns": _int_or_none(row.get("feature_columns")),
        "feature_names": (
            [str(name) for name in feature_names]
            if isinstance(feature_names, (list, tuple))
            else []
        ),
        "split_sizes": (
            dict(row.get("split_sizes"))
            if isinstance(row.get("split_sizes"), Mapping)
            else None
        ),
        "dataset_fingerprint": str(row.get("dataset_fingerprint") or "") or None,
        # -- the run (15.3, 15.4) -----------------------------------------
        "epochs_total": epochs_total,
        "epoch_current": epoch_current,
        "loss": losses.get("loss"),
        "val_loss": losses.get("val_loss"),
        "progress": progress,
        "metrics_history": history,
        # -- the ETA (15.5, 15.6) -----------------------------------------
        "eta_seconds": eta["eta_seconds"],
        "eta_state": eta["eta_state"],
        "eta_detail": eta["eta_detail"],
        # -- what came out of it ------------------------------------------
        "model_bound": model_version is not None,
        "model_version": None if model_version is None else dict(model_version),
        # -- timestamps ---------------------------------------------------
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "updated_at": row.get("updated_at"),
        "report_version": TRAINING_STATUS_VERSION,
    }


# ══════════════════════════════════════════════════════════════════════════
#  4. THE READS
# ══════════════════════════════════════════════════════════════════════════


async def _client_for(user: Mapping[str, Any], sb: Any = None) -> Any:
    """The caller's own RLS-scoped client, through the seam every other read uses.

    ``strategy_service.create_request_supabase_async`` rather than a direct import of
    ``core.dependencies``, so this path is the same one task 6.3's writes and task 6.5's
    reads go through - and the same one the tests substitute.
    """
    if sb is not None:
        return sb
    result = S.create_request_supabase_async(dict(user or {}).get("access_token"))
    if hasattr(result, "__await__"):
        return await result
    return result


def _degrade(exc: BaseException, what: str) -> bool:
    """Log and swallow a missing-``training_jobs`` error. ``True`` when it was one.

    Anything else propagates, for the reason ``strategy_service`` gives for the same
    split: a read that fails loudly beats one that reports an empty status surface for a
    job that is really running.
    """
    if S.is_missing_training_table_error(exc):
        logger.warning(
            "A training status read could not %s because %s does not exist. Apply %s. "
            "Detail: %s",
            what,
            S.TRAINING_JOBS_TABLE,
            S.TRAINING_MIGRATION,
            exc,
        )
        return True
    return False


async def load_owned_training_job(
    user: Mapping[str, Any], job_id: str, *, sb: Any = None
) -> Dict[str, Any]:
    """One ``training_jobs`` row this user owns, or :class:`TrainingJobNotFound`.

    Two filters, deliberately, exactly as ``strategy_service._load_owned_version`` and
    ``model_versioning.load_owned_model_version`` do it: the request-scoped client
    applies ``tj_owner_select`` (``user_id = auth.uid()``), and the query then carries an
    explicit ``.eq("user_id", ...)``. Another tenant's job is **not found**, which is the
    same answer a non-existent id gets, so existence does not leak (Requirement 21.4).
    """
    client = await _client_for(user, sb)
    user_id = str(dict(user or {}).get("id") or "")
    if client is None or not user_id or not str(job_id or ""):
        raise TrainingJobNotFound(f"Training job {job_id} not found")

    try:
        result = await S._execute(
            client.table(S.TRAINING_JOBS_TABLE)
            .select("*")
            .eq("id", str(job_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _degrade(exc, "read one training job"):
            raise TrainingJobNotFound(f"Training job {job_id} not found") from exc
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if _degrade(Exception(error_text), "read one training job"):
            raise TrainingJobNotFound(f"Training job {job_id} not found")
        raise RuntimeError(f"training job {job_id} could not be read: {error_text}")

    rows = (getattr(result, "data", None) or []) if result else []
    if not rows:
        raise TrainingJobNotFound(f"Training job {job_id} not found")
    row = dict(rows[0])
    if str(row.get("user_id") or "") != user_id:
        # Unreachable with the filter above and with RLS in force. Asserted anyway,
        # because this is the last line before another tenant's run would be described.
        raise TrainingJobNotFound(f"Training job {job_id} not found")
    return row


async def _owned_training_jobs(
    user: Mapping[str, Any], version_id: str, *, sb: Any = None
) -> List[Dict[str, Any]]:
    """Every ``training_jobs`` row this user owns for ``version_id``. Never raises.

    An unowned or unknown ``version_id`` yields an empty list rather than a refusal, for
    the same non-leaking reason :func:`load_owned_training_job` answers "not found": an
    error that distinguished "not yours" from "no jobs" would confirm that a version
    identifier exists.
    """
    client = await _client_for(user, sb)
    user_id = str(dict(user or {}).get("id") or "")
    if client is None or not user_id or not str(version_id or ""):
        return []

    try:
        result = await S._execute(
            client.table(S.TRAINING_JOBS_TABLE)
            .select("*")
            .eq("version_id", str(version_id))
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if _degrade(exc, "list the training jobs for a version"):
            return []
        raise

    error_text = S._result_error_text(result)
    if error_text:
        if _degrade(Exception(error_text), "list the training jobs for a version"):
            return []
        raise RuntimeError(
            f"training jobs for version {version_id} could not be read: {error_text}"
        )

    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    # Scoped again in Python. The filter above and RLS both already do this; a third
    # check costs nothing and this is the boundary where another tenant's run would be
    # described if either had been misconfigured.
    rows = [row for row in rows if str(row.get("user_id") or "") == user_id]
    # Ordered here rather than trusted from the server: PostgREST returns rows in no
    # guaranteed order without an ORDER BY, and a status list whose order changes between
    # polls makes a UI flicker. Newest first, ties broken by node so the order is total.
    rows.sort(
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("node_id") or "")),
        reverse=True,
    )
    return rows


async def _model_versions_by_job(
    user: Mapping[str, Any],
    jobs: Iterable[Mapping[str, Any]],
    *,
    sb: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """``{training_job_id: public_model_version(row)}`` for the completed jobs given.

    One read for the whole set rather than one per job, and only for jobs that reached
    ``COMPLETED`` - a job in any other status has no model by construction (task 6.4
    writes ``COMPLETED`` only after task 6.5's binder returned), so asking would be a
    query whose answer is known.

    Every projection is :func:`model_versioning.public_model_version`, so no
    ``artifact_uri`` can travel. An absent ``model_versions`` table, an unreadable row or
    any other failure yields an empty mapping and the report says ``model_bound: false``:
    an unreported model is an honest absence, an invented one is not.
    """
    completed = [
        job
        for job in jobs
        if str(job.get("status") or "").upper() == W.STATUS_COMPLETED and job.get("id")
    ]
    if not completed:
        return {}

    from backend_app.backend import model_versioning as MV

    client = await _client_for(user, sb)
    user_id = str(dict(user or {}).get("id") or "")
    if client is None or not user_id:
        return {}

    job_ids = sorted({str(job.get("id")) for job in completed})
    try:
        result = await S._execute(
            client.table(MV.MODEL_VERSIONS_TABLE)
            .select("*")
            .in_("training_job_id", job_ids)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if MV.is_missing_model_table_error(exc):
            logger.warning(
                "The model versions for %d completed training job(s) could not be "
                "reported because %s does not exist. Apply %s. Detail: %s",
                len(job_ids),
                MV.MODEL_VERSIONS_TABLE,
                MV.MODEL_MIGRATION,
                exc,
            )
            return {}
        logger.warning(
            "The model versions for %d completed training job(s) could not be read, so "
            "they are reported as unbound rather than guessed at: %s",
            len(job_ids),
            exc,
        )
        return {}

    error_text = S._result_error_text(result)
    if error_text:
        logger.warning(
            "The model versions for %d completed training job(s) could not be read, so "
            "they are reported as unbound: %s",
            len(job_ids),
            error_text,
        )
        return {}

    bound: Dict[str, Dict[str, Any]] = {}
    for row in getattr(result, "data", None) or []:
        if str(row.get("user_id") or "") != user_id:
            continue
        if not row.get("is_active"):
            # A superseded row keeps its own artifact and its own checksum, but the model
            # this job's node currently resolves to is the active one. A deactivated row
            # is not reported as the job's bound model.
            continue
        job_id = str(row.get("training_job_id") or "")
        if job_id:
            bound[job_id] = MV.public_model_version(row)
    return bound


async def training_job_report(
    user: Mapping[str, Any], job_id: str, *, sb: Any = None
) -> Dict[str, Any]:
    """The truthful status of one training job. ``GET /training/jobs/{job_id}``.

    Raises :class:`TrainingJobNotFound` when no such job belongs to this user, including
    when ``004d_training_and_models.sql`` has not been applied.
    """
    row = await load_owned_training_job(user, job_id, sb=sb)
    bound = await _model_versions_by_job(user, [row], sb=sb)
    return public_training_job(
        row, model_version=bound.get(str(row.get("id") or ""))
    )


async def list_training_job_reports(
    user: Mapping[str, Any], version_id: str, *, sb: Any = None
) -> Dict[str, Any]:
    """Every training job this user owns for one version. ``GET /training/jobs?version_id=``.

    An empty ``jobs`` list is the answer for a version with no jobs, for a version
    belonging to another tenant and for an unapplied migration - deliberately the same
    answer, because an empty list is what the builder already treats as "no job has been
    observed, so the training state is unknown", and distinguishing the three would tell
    a caller whether a version identifier they do not own exists.
    """
    rows = await _owned_training_jobs(user, version_id, sb=sb)
    bound = await _model_versions_by_job(user, rows, sb=sb)
    jobs = [
        public_training_job(row, model_version=bound.get(str(row.get("id") or "")))
        for row in rows
    ]
    return {
        "version_id": str(version_id or ""),
        "count": len(jobs),
        "jobs": jobs,
        "report_version": TRAINING_STATUS_VERSION,
    }
