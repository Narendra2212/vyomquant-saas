"""
strategy_last_signal.py — the producer for ``strategies.last_signal_at``.

vyomquant-ui-redesign task 12.3 (the follow-up recorded under BC-3), ``design.md`` §7.2,
Requirements 4.1, 19.1, 19.2. Migration ``015_strategy_last_signal_at.sql``.

WHAT THIS MODULE EXISTS FOR
---------------------------
BC-3 landed the *reader*: ``GET /api/strategies`` publishes ``last_signal_at`` on every
entry, off the strategy row, through the same key the dashboard strategy projection has
read all along (``dashboard_aggregation_service.get_strategies``, published there as
``last_signal_time``). No migration declared the column and nothing wrote it, so the field
reported ``null`` on every database, permanently. Task 12's preamble says no field's
not-available state may be its permanent outcome. This module is the missing half: it
writes the column at the point a signal is recorded.

Why the column is stored rather than derived as ``MAX(generated_at)``, and why this is not
a database trigger, is argued in full in ``015_strategy_last_signal_at.sql``'s header. The
short of it: ``public.signals`` is reachable only through PostgREST (there is no SQLAlchemy
model for it, so BC-4's grouped-aggregate route has no session to use), PostgREST's grouped
aggregates are off unless an operator enables them, a view over ``signals`` would bypass
that table's RLS on this PostgreSQL baseline, and a trigger would put a lock on
``public.strategies`` inside the signal INSERT's own transaction.

THE ONE CONSTRAINT EVERYTHING HERE IS SHAPED BY
-----------------------------------------------
**THE SIGNAL WRITE PATH MUST NOT BECOME ABLE TO FAIL.** A signal that was generated but
whose denormalised timestamp did not update is acceptable — the signal row is the record of
record, and ``last_signal_at`` is a convenience for one column of one table on one page.
The reverse is not acceptable at any price. So:

1. **Nothing here is awaited by the signal path.** :func:`schedule_last_signal_at` calls
   ``asyncio.create_task`` and returns immediately, so the update cannot add a single
   millisecond of latency to signal generation, let alone an exception. An awaited call —
   even one wrapped in ``try/except`` — would still let a slow or hanging PostgREST request
   delay the signal on its way to risk validation and order submission.
2. **The coroutine itself cannot raise.** :func:`record_last_signal_at` catches
   ``BaseException`` around every statement and returns a bool. A task that raised would
   only reach asyncio's exception handler, but "only reaches the log" is not a reason to
   leave an unhandled failure in a trading process.
3. **It runs after the signal row is confirmed persisted**, never before and never instead.
   The caller has a row back from the database at that point, so this update can only ever
   be late — never a substitute for the write that matters.
4. **It never reports a write it did not make.** Every function here returns ``False``
   rather than optimistically claiming success, and the absent-column case is logged with
   the migration filename so an operator knows what to apply.

WHAT IT WRITES, AND WHAT IT NEVER WRITES
----------------------------------------
One statement::

    UPDATE strategies SET last_signal_at = <generated_at>
     WHERE id = <strategy_id> AND user_id = <user_id>

``generated_at`` is the **signal row's own** ``generated_at``, passed in by the caller.
There is no clock read anywhere in this module (:func:`_instant` has no default and cannot
substitute "now"), so a strategy that has never signalled keeps ``NULL`` and no strategy
can be given a timestamp it did not earn (Requirement 19.2).

The ``user_id`` predicate is belt-and-braces beside RLS, matching
``strategy_archive.archive_strategy``: the row is addressed by primary key *and* owner, so
a mismatched pair updates zero rows rather than another tenant's strategy. Zero rows is a
legitimate outcome here and is reported as ``False``, not as an error.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not guard the write with ``last_signal_at < generated_at``, so under genuinely
concurrent generation for one strategy the column holds the last instant *written* rather
than the greatest. That is a real, bounded imprecision and it is chosen over the
alternative: PostgREST's spelling of "null OR less than" is an ``or_`` filter string, and
a mis-specified filter there fails *closed* — the UPDATE silently matches nothing and the
field stays ``null`` forever, which is the exact defect this module exists to fix. Signals
for one strategy are produced by one deployment worker, bar by bar, so the window requires
two near-simultaneous signals; and every value the column can hold is still a real instant
at which *that* strategy signalled. Nothing is fabricated and nothing is borrowed from
another strategy. Verification query 5 in the migration detects any drift and query 6
repairs it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime
from typing import Any, Optional, Set

logger = logging.getLogger("StrategyLastSignal")


# ══════════════════════════════════════════════════════════════════════════
# VOCABULARY
# ══════════════════════════════════════════════════════════════════════════

#: Named in the degradation warning, so an operator never has to guess which file to apply.
STRATEGY_LAST_SIGNAL_MIGRATION = "backend_app/migrations/015_strategy_last_signal_at.sql"

#: The one column 015 adds. The same name BC-3 publishes
#: (``routers.strategies.LAST_SIGNAL_AT_COLUMN``) and the same key the dashboard strategy
#: projection reads, so there is one spelling of "last signal" in the codebase.
LAST_SIGNAL_AT_COLUMN = "last_signal_at"

#: The table the column lives on.
STRATEGIES_TABLE = "strategies"

#: How long a "``last_signal_at`` is absent" verdict is trusted before it is re-probed, so
#: applying 015 to a running fleet takes effect without a redeploy. Same value and same
#: reason as ``strategy_archive.ARCHIVE_COLUMN_RECHECK_SECONDS``.
LAST_SIGNAL_COLUMN_RECHECK_SECONDS = 300.0

#: PostgreSQL's ``undefined_column`` and PostgREST's schema-cache equivalents. ``42P01`` /
#: ``PGRST205`` (a missing *table*) are deliberately absent: ``strategies`` is part of the
#: pre-existing schema and a missing table there is a real problem, not something to
#: degrade around. Copied from ``strategy_archive`` rather than shared, because that
#: module's tuple is tied to its own 503 contract and this one must not acquire it.
_MISSING_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

#: The exceptions that must NOT be swallowed even here. "Never raises" means never raises
#: *a failure* — it does not mean "refuses to be cancelled". A task that ate its own
#: ``CancelledError`` would hang an orderly shutdown, and swallowing ``SystemExit`` /
#: ``KeyboardInterrupt`` would make this module the reason a process would not stop. None of
#: the three can reach the signal path, because the signal path never awaits this task.
_MUST_PROPAGATE = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)

_column_absent: bool = False
_column_absent_at: float = 0.0

#: Strong references to in-flight fire-and-forget updates. ``asyncio`` keeps only a weak
#: reference to a task, so without this a scheduled update can be garbage-collected before
#: it runs — the write would then be silently dropped rather than merely late. Entries are
#: discarded by a done-callback, so this set holds only what is actually in flight.
_pending: Set["asyncio.Task[bool]"] = set()


# ══════════════════════════════════════════════════════════════════════════
# 015 AVAILABILITY — learned from the write, never probed on the signal path
# ══════════════════════════════════════════════════════════════════════════
#
# ``strategy_archive`` probes with a SELECT before its UPDATE because it has to REFUSE
# (503) when the column is missing. This module has nothing to refuse: the correct
# behaviour on a missing column is "write nothing, report null, say which file to apply",
# which is what a failed UPDATE already tells us. So there is no probe and no extra round
# trip — the verdict is learned from the one statement that was going to be issued anyway.


def reset_last_signal_column_support() -> None:
    """Forget the cached 015 verdict. For tests, and for an operator who just applied it."""
    global _column_absent, _column_absent_at
    _column_absent = False
    _column_absent_at = 0.0


def last_signal_column_is_absent() -> bool:
    """Whether a recent UPDATE proved ``strategies.last_signal_at`` does not exist.

    Expires after :data:`LAST_SIGNAL_COLUMN_RECHECK_SECONDS`, so applying 015 to a running
    fleet takes effect without a redeploy. A positive verdict is never cached — the column
    existing is the normal case and needs no memo.
    """
    if not _column_absent:
        return False
    if time.monotonic() - _column_absent_at >= LAST_SIGNAL_COLUMN_RECHECK_SECONDS:
        return False
    return True


def remember_last_signal_column_absent() -> None:
    """Record that 015 is not applied, so the next signal does not re-attempt the UPDATE."""
    global _column_absent, _column_absent_at
    _column_absent = True
    _column_absent_at = time.monotonic()


def is_missing_last_signal_at_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says ``strategies.last_signal_at`` is absent.

    Narrow on purpose, for the same reason ``strategy_archive``'s equivalent is: anything
    this returns ``False`` for is logged as a real failure rather than filed away as "015
    is unapplied", so a genuine outage cannot masquerade as a missing migration and stop
    this module from ever trying again.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text or "42p01" in text:  # a missing TABLE, not a missing column
        return False
    if any(code in text for code in _MISSING_COLUMN_CODES):
        return True
    if LAST_SIGNAL_AT_COLUMN not in text:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def warn_last_signal_column_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says exactly what did not happen."""
    logger.warning(
        "strategies.%s does not exist, so no signal instant can be recorded on the "
        "strategy row and GET /api/strategies will keep reporting last_signal_at as null "
        "for every strategy. Apply %s, then restart or wait %.0fs for the re-check. The "
        "signal itself was persisted and is unaffected — only this denormalised timestamp "
        "was skipped, and null is reported rather than a guessed instant (Requirement "
        "19.2). Detail: %s",
        LAST_SIGNAL_AT_COLUMN,
        STRATEGY_LAST_SIGNAL_MIGRATION,
        LAST_SIGNAL_COLUMN_RECHECK_SECONDS,
        detail,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE WRITE
# ══════════════════════════════════════════════════════════════════════════


def _instant(value: Any) -> Optional[str]:
    """A signal instant as an ISO-8601 string, or ``None``. Never a clock read.

    ``generated_at`` reaches this module as an ISO-8601 string on both signal paths
    (``Signal.generated_at`` and ``SignalService.create_signal``'s row both spell it that
    way), but a ``datetime`` is accepted and normalised so a caller cannot accidentally
    store ``str(dt)`` — which spells the separator as a space where every other timestamp
    on this table uses ``T``.

    This function has no default and cannot substitute "now": ``None`` in, ``None`` out,
    and an empty or whitespace-only string is ``None`` too. That is the single place a
    fabricated timestamp could enter ``strategies.last_signal_at``, so it is the place that
    is deliberately incapable of inventing one (Requirement 19.2).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _identifier(value: Any) -> Optional[str]:
    """A non-empty identifier as a string, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


async def record_last_signal_at(
    sb: Any,
    *,
    strategy_id: Any,
    user_id: Any,
    generated_at: Any,
) -> bool:
    """Set ``strategies.last_signal_at`` for one strategy. NEVER RAISES.

    Called only after the signal row is confirmed persisted, and only through
    :func:`schedule_last_signal_at`, so nothing on the awaited signal path depends on this
    returning at all — let alone returning successfully.

    Args:
        sb: The RLS-scoped PostgREST client the signal was written through, so this update
            runs under the same identity and the same row-level isolation. A caller with no
            client gets ``False``; this module builds none of its own, because a write that
            escaped the caller's RLS scope is the one outcome worse than a missing
            timestamp.
        strategy_id: The strategy the signal belongs to. The row is addressed by this and
            by ``user_id`` together.
        user_id: The signal's owner. Belt-and-braces beside RLS: a pair that does not match
            a row updates nothing, rather than another tenant's strategy.
        generated_at: The signal row's own ``generated_at``. Never defaulted — see
            :func:`_instant`.

    Returns:
        ``True`` only when the database confirmed a row was updated. ``False`` for every
        other outcome: no client, an incomplete argument, 015 unapplied, no matching row,
        or any failure at all. Never an optimistic ``True``.
    """
    try:
        if sb is None:
            return False

        strategy = _identifier(strategy_id)
        owner = _identifier(user_id)
        instant = _instant(generated_at)
        if not strategy or not owner or not instant:
            # Nothing is written from partial attribution. A missing strategy id would
            # address no row; a missing instant would invite a clock read, which is exactly
            # what Requirement 19.2 forbids.
            logger.debug(
                "[LAST_SIGNAL] Not recording: strategy_id=%r user_id=%r generated_at=%r "
                "is incomplete, and no part of it is substituted.",
                strategy_id,
                user_id,
                generated_at,
            )
            return False

        if last_signal_column_is_absent():
            # A recent UPDATE already proved 015 is unapplied. Re-attempting on every
            # signal would be a per-signal round trip that cannot succeed, and a repeat of
            # a warning an operator has already been given.
            return False

        try:
            result = await _execute(
                sb.table(STRATEGIES_TABLE)
                .update({LAST_SIGNAL_AT_COLUMN: instant})
                .eq("id", strategy)
                .eq("user_id", owner)
                .execute()
            )
        except _MUST_PROPAGATE:
            raise
        except BaseException as exc:  # noqa: BLE001 — classified, never propagated
            return _classify_failure(exc, strategy)

        error = getattr(result, "error", None)
        if error is not None:
            # PostgREST reports a failure as an error OBJECT on the response as often as it
            # raises, so both shapes reach the same classifier.
            return _classify_failure(Exception(str(error)), strategy)

        data = getattr(result, "data", None)
        if not data:
            # No row matched. Either the strategy was deleted between the signal write and
            # this update, or the signal's owner is not the strategy row's owner. Both are
            # facts to log, neither is an error, and in both cases the column is correctly
            # left as it was rather than written somewhere else.
            logger.debug(
                "[LAST_SIGNAL] No strategy row matched id=%s for this owner; %s left "
                "unchanged.",
                strategy,
                LAST_SIGNAL_AT_COLUMN,
            )
            return False

        logger.debug("[LAST_SIGNAL] strategies.%s=%s for %s", LAST_SIGNAL_AT_COLUMN, instant, strategy)
        return True
    except _MUST_PROPAGATE:
        raise
    except BaseException as exc:  # noqa: BLE001 — the outermost guarantee of "never raises"
        # Unreachable through the paths above, which each handle their own failures. Present
        # so that no future edit inside this function can make a scheduled update raise.
        logger.warning(
            "[LAST_SIGNAL] Unhandled failure recording %s for strategy %r (%s: %s). The "
            "signal itself was persisted and is unaffected.",
            LAST_SIGNAL_AT_COLUMN,
            strategy_id,
            type(exc).__name__,
            exc,
        )
        return False


def _classify_failure(exc: BaseException, strategy: str) -> bool:
    """File one UPDATE failure as "015 unapplied" or as a real failure. Always ``False``."""
    if is_missing_last_signal_at_error(exc):
        remember_last_signal_column_absent()
        warn_last_signal_column_absent(str(exc))
        return False
    logger.warning(
        "[LAST_SIGNAL] Could not record %s for strategy %s (%s: %s). The signal itself was "
        "persisted and is unaffected; this strategy keeps its previous value, which is "
        "null if it has not signalled before — never a guessed instant.",
        LAST_SIGNAL_AT_COLUMN,
        strategy,
        type(exc).__name__,
        exc,
    )
    return False


def schedule_last_signal_at(
    sb: Any,
    *,
    strategy_id: Any,
    user_id: Any,
    generated_at: Any,
) -> "Optional[asyncio.Task[bool]]":
    """Start :func:`record_last_signal_at` and return WITHOUT AWAITING IT. NEVER RAISES.

    This is the whole isolation guarantee, and it is one line of asyncio: the signal path
    calls this, gets a task object it ignores, and continues. No exception raised inside
    the update can reach the caller because the caller never awaits it, and no delay in
    the update can hold the signal up because the caller never waits for it. That is why
    the update is scheduled rather than awaited even though
    :func:`record_last_signal_at` is already incapable of raising: "cannot fail" is not
    enough on this path, it must also be incapable of being *slow*.

    Returns the task so a test can await it deterministically, and ``None`` when there is
    no running event loop to schedule on — a synchronous caller gets no write and no
    exception, rather than a ``RuntimeError`` on the signal path.
    """
    try:
        task = asyncio.get_running_loop().create_task(
            record_last_signal_at(
                sb,
                strategy_id=strategy_id,
                user_id=user_id,
                generated_at=generated_at,
            )
        )
    except _MUST_PROPAGATE:
        raise
    except BaseException as exc:  # noqa: BLE001 — scheduling must not fail the signal either
        logger.debug(
            "[LAST_SIGNAL] Could not schedule the %s update for strategy %r (%s: %s); the "
            "signal is unaffected.",
            LAST_SIGNAL_AT_COLUMN,
            strategy_id,
            type(exc).__name__,
            exc,
        )
        return None

    # asyncio holds only a weak reference to a task, so without this the update could be
    # collected before it ever runs.
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return task


async def drain_last_signal_at_writes() -> None:
    """Wait for every scheduled update to finish. For tests and for orderly shutdown.

    Never raises: :func:`record_last_signal_at` returns a bool instead of raising, and
    ``return_exceptions=True`` covers cancellation.
    """
    while _pending:
        await asyncio.gather(*tuple(_pending), return_exceptions=True)
