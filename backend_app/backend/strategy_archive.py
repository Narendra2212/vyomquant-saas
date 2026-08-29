"""
backend_app/backend/strategy_archive.py — the strategy archive (soft-delete) gate.

Trading-lifecycle-integration task 5.1. Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 20.2.

WHAT THIS MODULE IS FOR
-----------------------
``DELETE /api/strategies/{id}`` used to perform a real row deletion. Every dependent
table declares its parent reference as ``strategy_id UUID NOT NULL REFERENCES
strategies(id) ON DELETE CASCADE`` (``001_strategy_architecture.sql``), so one accepted
delete took the strategy's versions, its backtests, its deployments, its signals and
their ``signal_events`` with it — the financial and audit history Requirement 3 exists to
preserve.

This module is ``design.md``'s ``ALGORITHM archive_strategy(sb, user, strategy_id)``:
the gate, the write, the audit record, and the guard that keeps an archived strategy's
identifier out of the edit / rename / backtest / deploy paths (Requirement 3.3).

It performs no FastAPI work and imports no router: the same layering rule
``deployment_binding.py`` and ``strategy_lifecycle.py`` already follow, so the two
surfaces that call it (``routers/strategies.py``'s delete handler and
``StrategyService.deploy_version``) cannot disagree about what "archived" means.

WHAT IS REUSED, NOT RE-DERIVED
------------------------------
* :data:`~backend_app.backend.strategy_lifecycle.STOPPABLE_BINDING_STATES` — the same
  set (``DEPLOYING``, ``RUNNING``, ``PAUSED``) the kill switch already uses to decide
  which deployments it has something to stop. ``design.md``: "Reusing it here means
  'does this strategy have an active deployment' cannot answer differently in the
  archive gate than it does in the kill-switch path."
* :func:`~backend_app.backend.strategy_lifecycle.binding_state` and
  :func:`~backend_app.backend.strategy_lifecycle.binding_report` — the one place a
  ``strategy_deployments.status`` spelling is turned into one of Requirement 13.10's
  five states, and the one shape a deployment is reported in.
* :class:`~backend_app.backend.deployment_binding.DeployRejected` — :class:`ArchiveRejected`
  subclasses it for the same reason :class:`~backend_app.backend.strategy_lifecycle.
  LifecycleRejected` does: ``strategy_operations``'s deploy route already maps
  ``DeployRejected`` to ``(http_status, to_detail())``, so a refusal raised here reaches a
  client as the status the requirement asks for without a second mapping that could drift.
* :func:`~backend_app.backend.strategy_lifecycle.record_audit` and the
  ``StrategyAuditAction`` vocabulary — one audit path, not a second one.

MIGRATION 005a IS APPLIED BY HAND, SO IT MAY NOT BE APPLIED AT ALL
------------------------------------------------------------------
``backend_app/migrations/005a_strategy_archive.sql`` adds ``strategies.archived_at``.
``.github/workflows/03-deploy.yml`` has no migration step, so code reaches production
before the DDL does. That file's header states the contract this module implements, and
it is worth restating because the failure mode is unusually bad:

    Every code path that reads or writes ``archived_at`` must degrade with a WARNING
    NAMING ``005a_strategy_archive.sql``, not a 500, and NEVER report a strategy as
    archived when the archival write did not happen. "Reporting success there would be
    the worst possible degradation: the caller believes the strategy is gone from their
    list while it is still live, and — since task 5.1 replaces the hard delete — nothing
    else has removed it either."

So, concretely:

* **Writing** (:func:`archive_strategy`) refuses with ``503``
  ``STRATEGY_ARCHIVE_UNAVAILABLE``, naming the file, when the column is absent. It does
  **not** fall back to the hard delete it replaced, and it does not return
  ``{"status": "archived"}`` for a write that did not happen.
* **Reading** (:func:`is_archived`, :func:`assert_strategy_not_archived`) treats an
  absent column as "no strategy is archived" — a row that carries no ``archived_at`` key
  is active — because with the column absent nothing can have been archived, and failing
  an edit or a listing outright would take a working feature down to report a missing
  optional one. This is the same disposition the migration header prescribes for task
  5.2's ``include_archived`` filter.

ARCHIVING NEVER CASCADES, BY CONSTRUCTION
-----------------------------------------
:func:`archive_strategy` issues an ``UPDATE``. An ``UPDATE`` cascades nothing, so
Requirements 3.5 and 21.6 hold without a new constraint: no ``strategy_versions``,
``strategy_backtests``, ``strategy_deployments``, ``signals`` or ``signal_events`` row is
touched. Requirement 3.4 follows too — an archived strategy stays readable to its own
owner for history and audit, because its row and all its dependents are still there.
"""

from __future__ import annotations

import inspect
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from backend_app.backend.deployment_binding import DeployRejected
from backend_app.backend.strategy_lifecycle import (
    STOPPABLE_BINDING_STATES,
    binding_report,
    binding_state,
    is_active_binding_status,
    record_audit,
)
from backend_app.core.audit_trail import StrategyAuditAction

logger = logging.getLogger("StrategyArchive")


# ══════════════════════════════════════════════════════════════════════════
# VOCABULARY
# ══════════════════════════════════════════════════════════════════════════

#: Named in every degradation warning and in the 503 body, so an operator never has to
#: guess which file to apply.
STRATEGY_ARCHIVE_MIGRATION = "backend_app/migrations/005a_strategy_archive.sql"

#: The one column 005a adds.
ARCHIVED_AT_COLUMN = "archived_at"

#: ``{"status": ...}`` in :func:`archive_strategy`'s reply. ``design.md``'s two words,
#: published so a caller and a test spell them the same way.
STATUS_ARCHIVED = "archived"
STATUS_ALREADY_ARCHIVED = "already_archived"

#: The operations Requirement 3.3 names as refused on an archived strategy's identifier.
#: Passed to :func:`assert_strategy_not_archived` so the refusal says which one was asked
#: for. ``OPERATION_ARCHIVE`` is absent deliberately: re-archiving is idempotent
#: (Requirement 3.6), not refused.
OPERATION_EDIT = "edit"
OPERATION_RENAME = "rename"
OPERATION_BACKTEST = "backtest"
OPERATION_DEPLOY = "deploy"
ARCHIVED_REFUSED_OPERATIONS: Tuple[str, ...] = (
    OPERATION_EDIT,
    OPERATION_RENAME,
    OPERATION_BACKTEST,
    OPERATION_DEPLOY,
)

#: How long an "``archived_at`` is absent" verdict is trusted before it is re-probed, so
#: applying 005a to a running fleet takes effect without a redeploy. Same value and same
#: reason as ``deployment_binding.BINDING_COLUMN_RECHECK_SECONDS``.
ARCHIVE_COLUMN_RECHECK_SECONDS = 300.0

#: PostgreSQL's ``undefined_column`` and PostgREST's schema-cache equivalents.
#: ``42P01``/``PGRST205`` (a missing *table*) are deliberately absent: ``strategies`` is
#: part of the pre-existing schema and a missing table there is a real problem, not
#: something to degrade around.
_MISSING_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

_archive_column_supported: Optional[bool] = None
_archive_column_checked_at: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class ArchiveRejected(DeployRejected):
    """One classified archive refusal, carrying its own HTTP status.

    ``design.md``'s ``RAISE ArchiveRejected("STRATEGY_HAS_ACTIVE_DEPLOYMENTS", {...})``,
    generalised the way :class:`~backend_app.backend.deployment_binding.DeployRejected`
    already is: ``code`` is stable, ``details`` carries the named quantities the
    requirement asks for, and the status is decided here rather than by each router.

    ``409`` is the default because that is what ``design.md``'s status table puts every
    archive conflict at. The two exceptions set it explicitly: a strategy that is not the
    caller's is ``404`` (Requirement 20.2 — indistinguishable from one that does not
    exist), and an unapplied migration is ``503``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 409,
    ):
        super().__init__(code, message, details, http_status=http_status)


# ══════════════════════════════════════════════════════════════════════════
# 005a AVAILABILITY — detected, never assumed
# ══════════════════════════════════════════════════════════════════════════


def reset_archive_column_support() -> None:
    """Forget the cached 005a verdict. For tests, and for an operator who just applied it."""
    global _archive_column_supported, _archive_column_checked_at
    _archive_column_supported = None
    _archive_column_checked_at = 0.0


def archive_column_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False`` or ``None`` for "not yet determined"."""
    return _archive_column_supported


def _remember_archive_support(supported: bool) -> None:
    global _archive_column_supported, _archive_column_checked_at
    _archive_column_supported = supported
    _archive_column_checked_at = time.monotonic()


def remember_archive_column_absent() -> None:
    """Record that 005a is not applied, so the next archive refuses without re-probing.

    Public because the write path learns this the hard way: a process that cached a
    positive verdict and then meets ``42703`` at the ``UPDATE`` has newer information than
    the probe did. The negative verdict still expires after
    :data:`ARCHIVE_COLUMN_RECHECK_SECONDS`.
    """
    _remember_archive_support(False)


def _cached_archive_support() -> Optional[bool]:
    if _archive_column_supported is None:
        return None
    if _archive_column_supported:
        return True
    if time.monotonic() - _archive_column_checked_at >= ARCHIVE_COLUMN_RECHECK_SECONDS:
        return None
    return False


def is_missing_archived_at_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says ``strategies.archived_at`` does not exist.

    Narrow on purpose, and for the same reason ``deployment_binding``'s equivalent is:
    anything this returns ``False`` for is re-raised, because the one outcome worse than
    an archive that fails loudly is one that reports success without writing anything.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text or "42p01" in text:  # a missing TABLE, not a missing column
        return False
    if any(code in text for code in _MISSING_COLUMN_CODES):
        return True
    if ARCHIVED_AT_COLUMN not in text:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def warn_archive_column_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says exactly what did not happen."""
    logger.warning(
        "strategies.%s does not exist, so no strategy can be archived. Apply %s, then "
        "restart or wait %.0fs for the re-probe. Until then the archive request is "
        "REFUSED (503) rather than reported as done: the hard delete this endpoint used "
        "to perform is not a fallback (it would destroy the versions, backtests, "
        "deployments and signals Requirement 3.2 exists to preserve), and reporting a "
        "strategy as archived when the write did not happen would tell the caller their "
        "strategy is gone from their list while it is still live. Detail: %s",
        ARCHIVED_AT_COLUMN,
        STRATEGY_ARCHIVE_MIGRATION,
        ARCHIVE_COLUMN_RECHECK_SECONDS,
        detail,
    )


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


def _rows(result: Any) -> List[Dict[str, Any]]:
    data = getattr(result, "data", None) if result is not None else None
    if not data:
        return []
    try:
        return [dict(row) for row in data]
    except TypeError:  # a client whose response is not iterable tells us nothing
        return []


async def archive_column_supported(sb: Any) -> bool:
    """Whether ``strategies`` carries 005a's ``archived_at``.

    Read-only and cached: one ``SELECT archived_at ... LIMIT 1`` per process through the
    caller's own RLS-scoped client, so the probe sees what the write will see. An
    *indeterminate* answer resolves to ``True`` so the archival write is attempted and any
    real failure surfaces at the ``UPDATE`` — the same disposition
    ``deployment_binding.binding_columns_supported`` takes, and safe here because the
    ``UPDATE`` classifies the failure itself.
    """
    cached = _cached_archive_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        result = await _execute(
            sb.table("strategies").select(ARCHIVED_AT_COLUMN).limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_archived_at_error(exc):
            _remember_archive_support(False)
            warn_archive_column_absent(str(exc))
            return False
        logger.warning(
            "The strategies.%s probe was inconclusive (%s); attempting the archival "
            "write and letting a real error surface.",
            ARCHIVED_AT_COLUMN,
            exc,
        )
        return True

    error = getattr(result, "error", None)
    if error is not None and is_missing_archived_at_error(Exception(str(error))):
        _remember_archive_support(False)
        warn_archive_column_absent(str(error))
        return False

    _remember_archive_support(True)
    return True


# ══════════════════════════════════════════════════════════════════════════
# READING ARCHIVAL STATE (Requirements 3.3, 3.4)
# ══════════════════════════════════════════════════════════════════════════


def archived_at_of(row: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The row's ``archived_at`` as a string, or ``None`` for an active strategy.

    ``None`` is also the answer when the key is **absent**, which is what a row read
    before 005a is applied looks like. See the module docstring: with no column, nothing
    can have been archived.
    """
    if not isinstance(row, Mapping):
        return None
    value = row.get(ARCHIVED_AT_COLUMN)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_archived(row: Optional[Mapping[str, Any]]) -> bool:
    """Whether this strategy row is archived. ``archived_at IS NOT NULL``, and nothing else.

    Deliberately not derived from ``strategies.status``: ``status`` has an unfixed
    vocabulary and existing writers, and 005a's header records why archival is not
    expressed there.
    """
    return archived_at_of(row) is not None


def assert_strategy_not_archived(
    row: Optional[Mapping[str, Any]],
    *,
    operation: str,
    strategy_id: Optional[str] = None,
) -> None:
    """Requirement 3.3: refuse an edit, rename, backtest or deploy on an archived strategy.

    Raises
        :class:`ArchiveRejected` (``STRATEGY_ARCHIVED``, 409) naming the operation and the
        archival timestamp, so the client can say *why* the action is unavailable rather
        than only that it is.

    Refuses **only** on a row positively read as archived. A ``None`` row (unreadable, or
    read before 005a is applied) is not a refusal: ownership on every caller of this
    function is enforced by that caller's own ``user_id`` filter plus RLS, never by this
    guard, so failing open here withholds no tenant boundary — it only declines to
    invent an archival state it could not read.
    """
    stamp = archived_at_of(row)
    if stamp is None:
        return
    identifier = str(strategy_id or (row or {}).get("id") or "")
    raise ArchiveRejected(
        "STRATEGY_ARCHIVED",
        f"This strategy was archived at {stamp}, so it cannot be {operation}ed. "
        "An archived strategy stays readable for history and audit (Requirement 3.4); "
        "restore it, or duplicate it into a new strategy, to work on it again.",
        {
            "strategy_id": identifier,
            ARCHIVED_AT_COLUMN: stamp,
            "operation": operation,
            "refused_operations": list(ARCHIVED_REFUSED_OPERATIONS),
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READS THE GATE RESTS ON
# ══════════════════════════════════════════════════════════════════════════


async def load_owned_strategy(
    sb: Any, user_id: str, strategy_id: str
) -> Optional[Dict[str, Any]]:
    """One strategy row belonging to ``user_id``, or ``None``.

    ``select("*")`` rather than a named column list, and that is load-bearing: naming
    ``archived_at`` explicitly would make this read raise ``42703`` on every deployment
    where 005a is unapplied, turning a missing optional column into a broken endpoint.
    With ``*`` the key is simply absent, which :func:`is_archived` reads as "active".

    Two filters, the convention this codebase already established: the request-scoped
    client applies the ``strategies`` RLS policies, and ``user_id`` is on the filter as
    well, so the check does not rest on RLS alone.
    """
    if sb is None or not strategy_id:
        return None
    try:
        rows = _rows(
            await _execute(
                sb.table("strategies")
                .select("*")
                .eq("id", str(strategy_id))
                .eq("user_id", str(user_id))
                .execute()
            )
        )
    except Exception as exc:  # noqa: BLE001 - the caller decides what an unread row means
        logger.warning(
            "Strategy %s could not be read for user %s: %s", strategy_id, user_id, exc
        )
        return None
    return rows[0] if rows else None


async def load_blocking_deployments(
    sb: Any, *, user_id: str, strategy_id: str, strategy_row: Optional[Mapping[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Every deployment of this strategy that Requirement 3.1 blocks archival on.

    "Blocking" is :data:`STOPPABLE_BINDING_STATES` — ``DEPLOYING``, ``RUNNING``,
    ``PAUSED`` — evaluated through :func:`binding_state`, so the same status spellings the
    kill switch treats as live are the ones treated as live here. The filter is applied in
    Python and not as a server-side ``status = 'running'`` for the reason
    ``strategy_lifecycle._load_live_deployments`` records: the legacy column holds several
    spellings for the same state, and a server-side equality filter would silently miss a
    paused one.

    ``strategy_row`` is consulted as well, and this is not redundancy. The legacy deploy
    path (``routers/strategies.py::deploy_bot``, retained by ``design.md``) starts a bot
    and records it by setting ``strategies.status = 'running'`` **without** writing a
    ``strategy_deployments`` row. A strategy live only that way has no deployment row to
    find, and archiving it would hide a strategy whose bot is still trading. Its own
    status is therefore reported as a blocking pseudo-deployment, with
    ``deployment_id: None`` marking that it is the strategy's own legacy flag rather than
    a row in ``strategy_deployments``.

    Raises
        :class:`ArchiveRejected` (``STRATEGY_DEPLOYMENT_STATE_UNREADABLE``, 503) when the
        deployments read fails. Returning "none blocking" on a failed read would archive
        a strategy whose deployment state nothing could see, which is precisely the
        outcome Requirement 3.1 forbids.
    """
    blocking: List[Dict[str, Any]] = []

    if is_active_binding_status((strategy_row or {}).get("status")):
        blocking.append(
            binding_report(
                {
                    "id": None,
                    "strategy_id": strategy_id,
                    "status": (strategy_row or {}).get("status"),
                },
                extra={"source": "strategies.status"},
            )
        )

    try:
        rows = _rows(
            await _execute(
                sb.table("strategy_deployments")
                .select("*")
                .eq("user_id", str(user_id))
                .eq("strategy_id", str(strategy_id))
                .execute()
            )
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as a refusal, never as "none"
        logger.error(
            "Deployments for strategy %s could not be read, so archival cannot be "
            "gated on them: %s",
            strategy_id,
            exc,
        )
        raise ArchiveRejected(
            "STRATEGY_DEPLOYMENT_STATE_UNREADABLE",
            "This strategy's deployments could not be read, so the platform cannot "
            "confirm that none of them is still live. Requirement 3.1 refuses archival "
            "while an active deployment exists, so the request is refused rather than "
            "archiving a strategy whose deployment state is unknown. Try again.",
            {"strategy_id": str(strategy_id)},
            http_status=503,
        ) from exc

    for row in rows:
        if binding_state(row) in STOPPABLE_BINDING_STATES:
            blocking.append(binding_report(row, extra={"source": "strategy_deployments"}))
    return blocking


# ══════════════════════════════════════════════════════════════════════════
# THE GATE (design.md: ALGORITHM archive_strategy)
# ══════════════════════════════════════════════════════════════════════════


async def archive_strategy(
    sb: Any, user: Mapping[str, Any], strategy_id: str
) -> Dict[str, Any]:
    """Soft-delete one strategy. ``design.md``'s ``ALGORITHM archive_strategy``, verbatim.

    Order, and why: every gate is a **read**, and the single ``UPDATE`` is the only write,
    so a refusal at any point costs nothing and a crash before the ``UPDATE`` leaves the
    strategy exactly as it was. The audit record is written *after* the update, so nothing
    is audited that did not happen.

    Returns
        ``{"status": "archived", "strategy_id": ..., "archived_at": <timestamp>}`` — the
        archival instant, per the task's "return the archived timestamp".

        ``{"status": "already_archived", "strategy_id": ..., "archived_at": <original>}``
        when the strategy is already archived (Requirement 3.6): no further state change,
        no second audit record, no second quota release, and the *original* timestamp —
        not a fresh one — because re-archiving must leave existing archived data unchanged.

    Raises
        :class:`ArchiveRejected`:

        * ``STRATEGY_NOT_FOUND`` (404) when no such strategy belongs to this user. The
          same response a genuinely non-existent id gets, per Requirement 20.2.
        * ``STRATEGY_HAS_ACTIVE_DEPLOYMENTS`` (409) naming each blocking deployment by id
          and current state, per Requirement 3.1. Nothing is written.
        * ``STRATEGY_DEPLOYMENT_STATE_UNREADABLE`` (503) when the deployments read failed.
        * ``STRATEGY_ARCHIVE_UNAVAILABLE`` (503) when ``strategies.archived_at`` does not
          exist yet. See the module docstring: this is a refusal, never a hard delete and
          never a fabricated success.
    """
    user_id = str((user or {}).get("id") or (user or {}).get("user_id") or "")
    identifier = str(strategy_id or "")

    if sb is None:
        # DEV_MODE with no Supabase configured. The predecessor of this handler answered
        # ``{"status": "deleted"}`` here, which was a fabricated success; with a soft
        # delete that fabrication is exactly the one 005a's header forbids, because the
        # caller would drop the strategy from their list while it is still live.
        raise ArchiveRejected(
            "STRATEGY_ARCHIVE_UNAVAILABLE",
            "No database client is available, so this strategy cannot be archived. "
            "Nothing was changed.",
            {"strategy_id": identifier},
            http_status=503,
        )

    strategy = await load_owned_strategy(sb, user_id, identifier)
    if strategy is None:
        raise ArchiveRejected(
            "STRATEGY_NOT_FOUND",
            "Strategy not found.",
            {"strategy_id": identifier},
            http_status=404,
        )

    existing = archived_at_of(strategy)
    if existing is not None:
        # Requirement 3.6: idempotent. No write, no audit record, nothing to undo.
        logger.info(
            "Strategy %s is already archived (since %s); no further action taken.",
            identifier,
            existing,
        )
        return {
            "status": STATUS_ALREADY_ARCHIVED,
            "strategy_id": identifier,
            ARCHIVED_AT_COLUMN: existing,
        }

    if not await archive_column_supported(sb):
        raise ArchiveRejected(
            "STRATEGY_ARCHIVE_UNAVAILABLE",
            "This strategy cannot be archived yet: the archival column "
            f"strategies.{ARCHIVED_AT_COLUMN} does not exist in this database. Apply "
            f"{STRATEGY_ARCHIVE_MIGRATION}. Nothing was changed, and nothing was "
            "deleted — an archive that cannot be recorded is refused rather than "
            "performed as the row deletion it replaced.",
            {"strategy_id": identifier, "migration": STRATEGY_ARCHIVE_MIGRATION},
            http_status=503,
        )

    blocking = await load_blocking_deployments(
        sb, user_id=user_id, strategy_id=identifier, strategy_row=strategy
    )
    if blocking:
        raise ArchiveRejected(
            "STRATEGY_HAS_ACTIVE_DEPLOYMENTS",
            "This strategy has "
            f"{len(blocking)} active deployment(s), so it cannot be archived. Stop "
            "every one of them first; the strategy, its versions, its backtests, its "
            "deployments and its signals are unchanged.",
            {
                "strategy_id": identifier,
                "blocking_deployments": blocking,
                "blocking_states": list(STOPPABLE_BINDING_STATES),
            },
        )

    stamp = datetime.now(timezone.utc).isoformat()
    try:
        result = await _execute(
            sb.table("strategies")
            .update({ARCHIVED_AT_COLUMN: stamp})
            .eq("id", identifier)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
        if is_missing_archived_at_error(exc):
            # The probe said the column was there (or was inconclusive) and the write
            # says otherwise. The write is the newer information.
            remember_archive_column_absent()
            warn_archive_column_absent(str(exc))
            raise ArchiveRejected(
                "STRATEGY_ARCHIVE_UNAVAILABLE",
                "This strategy could not be archived: the archival column "
                f"strategies.{ARCHIVED_AT_COLUMN} does not exist in this database. "
                f"Apply {STRATEGY_ARCHIVE_MIGRATION}. Nothing was changed, and nothing "
                "was deleted.",
                {"strategy_id": identifier, "migration": STRATEGY_ARCHIVE_MIGRATION},
                http_status=503,
            ) from exc
        logger.error("Strategy %s could not be archived: %s", identifier, exc)
        raise ArchiveRejected(
            "STRATEGY_ARCHIVE_FAILED",
            "This strategy could not be archived. Nothing was changed. Try again.",
            {"strategy_id": identifier},
            http_status=503,
        ) from exc

    # The write raised nothing, so it happened. ``archived_at`` is read back off the
    # returned representation when the client sent one, and falls back to the value this
    # function wrote otherwise — PostgREST returns a representation only when asked to,
    # and an empty body is not evidence the update matched no row (the ownership-scoped
    # read above already established that it does).
    written = _rows(result)
    archived_at = archived_at_of(written[0]) if written else None
    if archived_at is None:
        archived_at = stamp
        if not written:
            logger.debug(
                "The archive update for strategy %s returned no representation; "
                "reporting the timestamp that was written.",
                identifier,
            )

    await record_audit(
        StrategyAuditAction.STRATEGY_ARCHIVED,
        actor_id=user_id or "unknown",
        resource_type="strategy",
        resource_id=identifier,
        reason="Strategy archived on request (soft delete, Requirement 3.2)",
        before=None,
        after=archived_at,
        strategy_id=identifier,
        metadata={
            "name": strategy.get("name"),
            "archived_at": archived_at,
            "cascaded": False,
        },
    )

    logger.info("Strategy %s archived at %s for user %s", identifier, archived_at, user_id)
    return {
        "status": STATUS_ARCHIVED,
        "strategy_id": identifier,
        ARCHIVED_AT_COLUMN: archived_at,
    }


__all__ = [
    "ARCHIVED_AT_COLUMN",
    "ARCHIVED_REFUSED_OPERATIONS",
    "ARCHIVE_COLUMN_RECHECK_SECONDS",
    "ArchiveRejected",
    "OPERATION_BACKTEST",
    "OPERATION_DEPLOY",
    "OPERATION_EDIT",
    "OPERATION_RENAME",
    "STATUS_ALREADY_ARCHIVED",
    "STATUS_ARCHIVED",
    "STRATEGY_ARCHIVE_MIGRATION",
    "archive_column_support_state",
    "archive_column_supported",
    "archive_strategy",
    "archived_at_of",
    "assert_strategy_not_archived",
    "is_archived",
    "is_missing_archived_at_error",
    "load_blocking_deployments",
    "load_owned_strategy",
    "remember_archive_column_absent",
    "reset_archive_column_support",
    "warn_archive_column_absent",
]
