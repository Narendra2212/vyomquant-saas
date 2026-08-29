"""
backend/registry_snapshot_service.py - Block registry snapshot provenance

Spec: strategy-builder task 3.2. Requirement 4.16 ("THE Persistence_Layer SHALL retain a
Block_Registry snapshot for each registry version"); Requirement 9.1 is the other half and
is already satisfied by ``strategy_versions.registry_version`` (task 2.3).

WHY THIS EXISTS
---------------
A strategy version records ``registry_version`` - a deterministic content hash of the
assembled descriptor set, ``r_3d173598`` today. That hash is a label, not an explanation. If
a parameter range widens, a block is withdrawn or a model library disappears from the image,
the descriptor set that hash names is gone and the version becomes unexplainable: you cannot
say why it behaves as it does, because you cannot say what the author was offered when they
built it. This module records the descriptor set itself, keyed by that hash, so the label
stays resolvable.

WHERE THE WRITE LIVES, AND WHY NOT IN ``strategy_dag``
------------------------------------------------------
``strategy_dag.registry.build_registry()`` is where assembly happens, and it is pure and
synchronous with no database handle. ``tests/test_strategy_dag_architecture.py`` enforces
that purity: no module under ``strategy_dag/`` may reach FastAPI, a database client, the
execution path or a credential store, at import time or (for credentials) at all. Putting an
insert there would break that guard, and the guard is load-bearing - it is what keeps the
compiler importable by the worker and the backtester, and what makes it impossible for the
Builder to place an order by accident.

So the write lives here, in the service layer, one level above the pure core:

* ``strategy_service.StrategyService.create_version`` calls
  :func:`record_registry_snapshot` with the request-scoped client it already holds, right
  after the version row lands. That is the moment the assembled registry is *used* to
  produce a durable artifact, so it is the moment the artifact's provenance is worth
  recording.
* The registry endpoints (task 3.1, ``routers/strategy_operations.py``) can call the same
  function on the assembly they are about to serve. It is idempotent, so being called from
  both places costs one write per descriptor set, not one per call.

This module imports nothing from ``strategy_dag`` at import time; the registry is reached
lazily inside the functions that need it, so importing the service layer does not trigger
assembly.

FAILURE MODES, AND WHICH ONES ARE ALLOWED TO MATTER
---------------------------------------------------
Registry assembly and the save path are both on the critical path. Recording provenance is
not. The ordering of priorities is therefore explicit:

1. **Idempotent on ``registry_version``.** Two layers. In-process, a version already
   recorded is not written again, so a busy process issues one write per descriptor set, not
   one per request. In the database, the write is an upsert with
   ``ON CONFLICT DO NOTHING`` on the primary key, so restarts, concurrent requests and other
   processes converge on exactly one row.
2. **A snapshot failure never breaks a save or startup.** :func:`record_registry_snapshot`
   does not raise. Ever. A missing table, an unreachable database, a denied insert - all of
   them return an outcome and log.
3. **Nothing is swallowed silently.** Each failure class gets its own log line at the level
   that matches how actionable it is: a missing table is a WARNING naming
   :data:`SNAPSHOT_TABLE_MIGRATION`, a denied insert is a WARNING naming the policy that
   must be missing, and anything else is an ERROR with a traceback. A failed write is also
   *not* remembered as recorded, so the next save retries it rather than assuming success.
   Callers that want the exception itself use :func:`write_registry_snapshot`, which
   classifies the same way but re-raises what it cannot classify.
4. **The table may not exist.** ``backend_app/migrations/004b_block_registry_snapshots.sql``
   is applied by hand - ``.github/workflows/03-deploy.yml`` has no migration step - so code
   reaches production before the DDL does. This module handles that exactly the way
   ``strategy_service.canonical_columns_supported`` handles the missing part-1 columns:
   one cached read-only probe per process, a narrow error classification, a negative verdict
   that expires so a late migration is picked up without a redeploy, and a warning that
   names the file to apply. The mechanism is deliberately the same one rather than a second
   invention, so an operator who has learned one has learned both.

TENANT ISOLATION
----------------
A snapshot is platform-global: no ``user_id``, no ``strategy_id``, nothing tenant-derived in
the row or the payload. The descriptors written here are the same bytes already served to
every authenticated caller by the registry endpoint. The write still goes through the
caller's own RLS-scoped client - no service-role client, no new credential - so this module
cannot reach anything the caller could not already reach. See the migration's "ROW LEVEL
SECURITY" header for the access list and the residual risk it accepts.
"""

import inspect
import logging
import time
from enum import Enum
from typing import Any, Dict, FrozenSet, Optional, Set, Tuple

logger = logging.getLogger("RegistrySnapshotService")


# ══════════════════════════════════════════════════════════════════════════
#  Contract constants
# ══════════════════════════════════════════════════════════════════════════

#: The table from migration 004 part 2.
SNAPSHOT_TABLE = "block_registry_snapshots"

#: The column the snapshot is keyed on, and the ``ON CONFLICT`` target.
SNAPSHOT_KEY_COLUMN = "registry_version"

#: The payload column: ``BlockRegistry.to_dict()``.
SNAPSHOT_PAYLOAD_COLUMN = "descriptors"

#: Named in every degradation warning, so an operator is never left guessing which file to
#: apply. Part 1 is named separately by ``strategy_service.CANONICAL_COLUMN_MIGRATION``.
SNAPSHOT_TABLE_MIGRATION = "backend_app/migrations/004b_block_registry_snapshots.sql"

#: How long a "table is absent" verdict is trusted before it is re-probed, so applying the
#: migration to a running fleet takes effect without a redeploy.
SNAPSHOT_TABLE_RECHECK_SECONDS = 300.0

#: Error codes that mean "this relation does not exist": PostgreSQL's ``42P01`` and
#: PostgREST's ``PGRST205`` (table absent from the schema cache). ``PGRST204`` and ``42703``
#: are deliberately absent - those are a missing *column*, which on this table would mean
#: the table exists in the wrong shape, and masking that would hide a real problem.
_MISSING_TABLE_CODES: Tuple[str, ...] = ("42p01", "undefined_table", "pgrst205")

#: What a denied insert looks like. Distinguished from a generic failure because the fix is
#: specific and stating it saves an operator a bisect.
_PERMISSION_DENIED_CODES: Tuple[str, ...] = (
    "42501",
    "insufficient_privilege",
    "permission denied",
    "row-level security",
    "row level security",
)


class SnapshotOutcome(str, Enum):
    """What a snapshot attempt did. Returned rather than raised, so a caller on the save
    path can log or surface it without a try/except around a provenance record."""

    #: The row was sent to the database. ``ON CONFLICT DO NOTHING`` means "sent", not
    #: necessarily "inserted": a concurrent writer may have won. Either way the table now
    #: holds exactly one row for this ``registry_version``.
    WRITTEN = "written"

    #: This process already recorded this ``registry_version``; no query was issued.
    ALREADY_RECORDED = "already_recorded"

    #: The table does not exist yet. Migration 004 part 2 is unapplied. Warned, not raised.
    TABLE_ABSENT = "table_absent"

    #: The insert was refused by RLS or by a missing grant. Warned, not raised.
    PERMISSION_DENIED = "permission_denied"

    #: No database client was available, so there was nothing to write through.
    NO_CLIENT = "no_client"

    #: The descriptor source is not a registry: it publishes no ``registry_version`` or no
    #: serialisable payload. A caller-supplied descriptor source can legitimately look like
    #: this, so it is a debug-level non-event rather than a failure.
    UNSUPPORTED_SOURCE = "unsupported_source"

    #: Something else went wrong. Logged at ERROR with a traceback, and NOT remembered, so
    #: the next attempt retries.
    FAILED = "failed"


#: Outcomes that mean the snapshot is durable as far as this process can tell.
RECORDED_OUTCOMES: FrozenSet[SnapshotOutcome] = frozenset(
    {SnapshotOutcome.WRITTEN, SnapshotOutcome.ALREADY_RECORDED}
)


# ══════════════════════════════════════════════════════════════════════════
#  Process-local state
# ══════════════════════════════════════════════════════════════════════════

#: ``None`` means "not yet determined".
_table_supported: Optional[bool] = None
_table_checked_at: float = 0.0

#: Registry versions this process has already written. Bounded by the number of distinct
#: descriptor sets a process sees, which is one unless the registry is reset - so this is a
#: handful of short strings, not a leak.
_recorded_versions: Set[str] = set()


def reset_registry_snapshot_state() -> None:
    """Forget the cached table verdict and the recorded-version set.

    For tests, and for an operator who has just applied migration 004 part 2 and does not
    want to wait out :data:`SNAPSHOT_TABLE_RECHECK_SECONDS`.
    """
    global _table_supported, _table_checked_at
    _table_supported = None
    _table_checked_at = 0.0
    _recorded_versions.clear()


def snapshot_table_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False``, or ``None`` for "not yet determined"."""
    return _table_supported


def recorded_registry_versions() -> FrozenSet[str]:
    """The registry versions this process has recorded. For assertions and diagnostics."""
    return frozenset(_recorded_versions)


def _remember_table_support(supported: bool) -> None:
    global _table_supported, _table_checked_at
    _table_supported = supported
    _table_checked_at = time.monotonic()


def _cached_table_support() -> Optional[bool]:
    """The cached verdict, expiring a negative one so a late migration is picked up.

    A positive verdict is kept for the life of the process: migration 004 part 2 is additive
    and drops nothing, so a table that exists cannot stop existing.
    """
    if _table_supported is None:
        return None
    if _table_supported:
        return True
    if time.monotonic() - _table_checked_at >= SNAPSHOT_TABLE_RECHECK_SECONDS:
        return None
    return False


# ══════════════════════════════════════════════════════════════════════════
#  Error classification - narrow on purpose
# ══════════════════════════════════════════════════════════════════════════


def is_missing_snapshot_table_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says the snapshot table does not exist.

    Narrow by design. Anything this returns ``False`` for is either classified as a
    permission problem or reported as a genuine error, because a provenance gap that is
    logged as "migration not applied" when the real cause is something else is worse than no
    log at all.
    """
    text = str(exc).lower()
    if not text:
        return False
    if any(code in text for code in _MISSING_TABLE_CODES):
        return True
    if SNAPSHOT_TABLE not in text:
        return False
    return any(
        phrase in text
        for phrase in (
            "does not exist",
            "schema cache",
            "could not find",
            "unknown table",
            "no such table",
        )
    )


def is_snapshot_permission_error(exc: BaseException) -> bool:
    """True when ``exc`` says the write was refused by RLS or by a missing grant."""
    text = str(exc).lower()
    if not text:
        return False
    return any(code in text for code in _PERMISSION_DENIED_CODES)


def _warn_snapshot_table_absent(detail: str) -> None:
    logger.warning(
        "Table %s does not exist, so no block registry snapshot was recorded. "
        "Apply %s, then restart or wait %.0fs for the re-probe. Until then "
        "Requirement 4.16 is not met: strategy versions still record registry_version, "
        "but the descriptor set that hash names is not retained, so what an author was "
        "offered cannot be reconstructed. Saves and the registry endpoints are "
        "unaffected. Detail: %s",
        SNAPSHOT_TABLE,
        SNAPSHOT_TABLE_MIGRATION,
        SNAPSHOT_TABLE_RECHECK_SECONDS,
        detail,
    )


def _warn_snapshot_write_denied(detail: str) -> None:
    logger.warning(
        "Writing a block registry snapshot to %s was refused. The migration %s creates "
        "the brs_authenticated_append policy and grants INSERT to the authenticated role; "
        "if the table exists without them, an authenticated client cannot record "
        "provenance. Requirement 4.16 is not met until that is fixed. Saves and the "
        "registry endpoints are unaffected. Detail: %s",
        SNAPSHOT_TABLE,
        SNAPSHOT_TABLE_MIGRATION,
        detail,
    )


# ══════════════════════════════════════════════════════════════════════════
#  Table availability probe
# ══════════════════════════════════════════════════════════════════════════


async def snapshot_table_supported(sb: Any) -> bool:
    """Whether :data:`SNAPSHOT_TABLE` exists, cached per process.

    Read-only: one ``SELECT registry_version ... LIMIT 1`` per process, through the caller's
    own client so the probe sees what the write will see. Never writes and never raises. An
    indeterminate answer resolves to ``True`` so the write is attempted and a real failure
    surfaces at the insert rather than being pre-emptively downgraded to "migration not
    applied".
    """
    cached = _cached_table_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        query = sb.table(SNAPSHOT_TABLE).select(SNAPSHOT_KEY_COLUMN).limit(1).execute()
        result = await query if inspect.isawaitable(query) else query
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if is_missing_snapshot_table_error(exc):
            _remember_table_support(False)
            _warn_snapshot_table_absent(str(exc))
            return False
        # Transient or unrelated: decide nothing, cache nothing, let the write speak.
        logger.warning(
            "Probe of %s was inconclusive (%s); attempting the snapshot write and letting "
            "a real error surface.",
            SNAPSHOT_TABLE,
            exc,
        )
        return True

    # PostgREST clients that report errors on the response instead of raising.
    error = getattr(result, "error", None)
    if error is not None and is_missing_snapshot_table_error(Exception(str(error))):
        _remember_table_support(False)
        _warn_snapshot_table_absent(str(error))
        return False

    _remember_table_support(True)
    return True


# ══════════════════════════════════════════════════════════════════════════
#  The snapshot row
# ══════════════════════════════════════════════════════════════════════════


def _resolve_registry(registry: Any = None) -> Any:
    """``registry`` when given, otherwise the assembled backend registry.

    The import is lazy so that importing this module does not trigger registry assembly -
    assembly reads ``exchange_executor.OrderType`` and would drag CCXT into every process
    that merely imports the service layer.
    """
    if registry is not None:
        return registry
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.get_registry()


def snapshot_row(registry: Any) -> Optional[Dict[str, Any]]:
    """The row to write for ``registry``, or ``None`` when it is not a registry.

    Postconditions
        The returned row satisfies ``chk_brs_descriptors_shape``: ``descriptors`` is an
        object holding a ``blocks`` array whose ``registry_version`` equals the row key.
        ``created_at`` is omitted so the column DEFAULT decides, which keeps the recorded
        time on the database clock rather than the application's.
    """
    version = getattr(registry, "registry_version", None)
    to_dict = getattr(registry, "to_dict", None)
    if not isinstance(version, str) or not version or not callable(to_dict):
        return None

    payload = to_dict()
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        return None
    if payload.get(SNAPSHOT_KEY_COLUMN) != version:
        # chk_brs_descriptors_shape would reject this. Fail here, where the message can say
        # why, rather than as a 23514 from inside the insert.
        return None

    return {SNAPSHOT_KEY_COLUMN: version, SNAPSHOT_PAYLOAD_COLUMN: payload}


# ══════════════════════════════════════════════════════════════════════════
#  The write
# ══════════════════════════════════════════════════════════════════════════


async def write_registry_snapshot(sb: Any, registry: Any = None) -> SnapshotOutcome:
    """Record one snapshot, re-raising anything that cannot be classified.

    The strict form. Use :func:`record_registry_snapshot` on any path where a provenance
    record must not be able to fail the operation it is describing.

    Parameters
    ----------
    sb
        A database client - the caller's own RLS-scoped one. ``None`` yields
        :attr:`SnapshotOutcome.NO_CLIENT`.
    registry
        The assembled registry, or ``None`` to use the memoised backend one. A descriptor
        source that is not a registry yields :attr:`SnapshotOutcome.UNSUPPORTED_SOURCE`.

    Returns
        A :class:`SnapshotOutcome`. ``WRITTEN`` and ``ALREADY_RECORDED`` mean the table
        holds exactly one row for this ``registry_version``.

    Raises
        Whatever the client raised, when it is neither a missing table nor a refused
        insert. A genuine write failure is never converted into a success.
    """
    row = snapshot_row(_resolve_registry(registry))
    if row is None:
        logger.debug(
            "Descriptor source publishes no registry_version or no serialisable payload; "
            "no snapshot recorded."
        )
        return SnapshotOutcome.UNSUPPORTED_SOURCE

    version = row[SNAPSHOT_KEY_COLUMN]

    # Layer 1 of idempotency: this process already did it, so do not go near the database.
    if version in _recorded_versions:
        return SnapshotOutcome.ALREADY_RECORDED

    if sb is None:
        logger.warning(
            "No database client available, so block registry snapshot %s was not recorded.",
            version,
        )
        return SnapshotOutcome.NO_CLIENT

    if not await snapshot_table_supported(sb):
        return SnapshotOutcome.TABLE_ABSENT

    try:
        # Layer 2 of idempotency: ON CONFLICT DO NOTHING on the primary key, which is what
        # ignore_duplicates=True sends (Prefer: resolution=ignore-duplicates). Concurrent
        # requests, a restart and a second process all converge on one row.
        query = (
            sb.table(SNAPSHOT_TABLE)
            .upsert(row, on_conflict=SNAPSHOT_KEY_COLUMN, ignore_duplicates=True)
            .execute()
        )
        result = await query if inspect.isawaitable(query) else query
    except Exception as exc:  # noqa: BLE001 - classified, then re-raised if unrecognised
        if is_missing_snapshot_table_error(exc):
            _remember_table_support(False)
            _warn_snapshot_table_absent(str(exc))
            return SnapshotOutcome.TABLE_ABSENT
        if is_snapshot_permission_error(exc):
            _warn_snapshot_write_denied(str(exc))
            return SnapshotOutcome.PERMISSION_DENIED
        raise

    error = getattr(result, "error", None)
    if error is not None:
        as_exc = Exception(str(error))
        if is_missing_snapshot_table_error(as_exc):
            _remember_table_support(False)
            _warn_snapshot_table_absent(str(error))
            return SnapshotOutcome.TABLE_ABSENT
        if is_snapshot_permission_error(as_exc):
            _warn_snapshot_write_denied(str(error))
            return SnapshotOutcome.PERMISSION_DENIED
        raise as_exc

    _recorded_versions.add(version)
    logger.info(
        "Recorded block registry snapshot %s (%d blocks) in %s.",
        version,
        len(row[SNAPSHOT_PAYLOAD_COLUMN].get("blocks") or ()),
        SNAPSHOT_TABLE,
    )
    return SnapshotOutcome.WRITTEN


async def record_registry_snapshot(sb: Any, registry: Any = None) -> SnapshotOutcome:
    """Record one snapshot, best effort. Never raises.

    This is what the save path and the registry endpoints call. Provenance is worth
    recording; it is not worth failing a save for. An unclassifiable failure is logged at
    ERROR with a traceback and is *not* remembered as recorded, so the next attempt retries
    it.

    Postconditions
        Returns a :class:`SnapshotOutcome` and propagates no exception, for any ``sb``,
        any ``registry`` and any database state.
    """
    try:
        return await write_registry_snapshot(sb, registry)
    except Exception as exc:  # noqa: BLE001 - the whole point of this wrapper
        logger.error(
            "Failed to record a block registry snapshot in %s: %s. The operation that "
            "triggered this is unaffected; provenance for this registry version is "
            "missing and will be retried on the next attempt.",
            SNAPSHOT_TABLE,
            exc,
            exc_info=True,
        )
        return SnapshotOutcome.FAILED


__all__ = [
    "SNAPSHOT_TABLE",
    "SNAPSHOT_KEY_COLUMN",
    "SNAPSHOT_PAYLOAD_COLUMN",
    "SNAPSHOT_TABLE_MIGRATION",
    "SNAPSHOT_TABLE_RECHECK_SECONDS",
    "RECORDED_OUTCOMES",
    "SnapshotOutcome",
    "is_missing_snapshot_table_error",
    "is_snapshot_permission_error",
    "record_registry_snapshot",
    "recorded_registry_versions",
    "reset_registry_snapshot_state",
    "snapshot_row",
    "snapshot_table_support_state",
    "snapshot_table_supported",
    "write_registry_snapshot",
]
