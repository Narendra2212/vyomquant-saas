"""
backend/execution_environment.py - the three Execution_Environment values, and the guard
that stands in front of the live order path.

Requirement 13.1 fixes the value set to exactly ``BACKTEST``, ``PAPER`` and ``LIVE``, and
this module is the single place those three words are spelled. Everything that needs the
vocabulary - the ``chk_signals_environment`` check constraint, the Signal_Trace recorder's
``environment`` column (Requirement 23.1), the ``paper_sessions.environment`` value and the
live-path guard below - derives it from here, so the constraint and the code cannot drift.

The value set is *additive*: no existing ``mode`` or ``environment`` string anywhere in the
platform is renamed or removed by introducing it.

Exposes
-------
ExecutionEnvironment              the three values of Requirement 13.1
EXECUTION_ENVIRONMENTS            the three values as a tuple, in check-constraint order
parse_execution_environment(v)    exact-spelling resolution, ``None`` when unresolvable
ExecutionEnvironmentError         base of the two refusals below
UnresolvedExecutionEnvironment    Requirement 13.10: absent or out-of-set, never LIVE
ExecutionEnvironmentMismatch      Requirement 13.9: resolved, but not LIVE
assert_live_environment(env)      the live-path guard (Requirements 13.9, 13.10)

The guard, and why it looks like this
-------------------------------------
``assert_live_environment`` is called at exactly one place - the top of
``core/execution_engine.ExecutionEngine.execute_with_idempotency``, ahead of every
existing check and ahead of the ``exchange_executor``/CCXT call in
``_execute_trade_internal`` - so there is no live order path that reaches an exchange
without passing it.

* It **raises**; it does not return a verdict. A returned verdict can be dropped by a
  caller that forgets to read it, and the cost of dropping this one is a real order placed
  for a simulated intent. An exception cannot be dropped silently.
* It resolves the value by **exact spelling only**. ``"live "``, ``"live"`` and ``"Live"``
  are all unresolved, not LIVE. Trimming or case-folding here would mean the guard itself
  decides that a value it does not recognise is close enough to LIVE, which is precisely
  what Requirement 13.10 forbids.
* Nothing in it defaults to ``LIVE``. Absent and out-of-set both raise
  ``UnresolvedExecutionEnvironment`` (Requirement 13.10).
* The ``PAPER``/``BACKTEST`` refusal writes an audit record **before** raising
  (Requirement 13.9), so the rejection is on the record even though the exception is what
  stops the order. The write goes through ``core/audit_trail.StrategyAuditLogger``, which
  never raises and never fails the act it describes; an audit sink that could refuse would
  turn a safety refusal into an unhandled error.
* It touches no balance. It runs before any repository, session or exchange handle is
  opened, so a refused order leaves every Paper_Account and every real-money balance
  exactly as it found them (Requirement 13.9).

Module import pulls only the standard library. ``core/audit_trail`` is imported inside the
audit helper: it reaches Redis and the ORM, and this module is imported by
``core/execution_engine`` at module scope.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ExecutionEnvironment(str, Enum):
    """The three Execution_Environment values of Requirement 13.1.

    ``str`` mixin so a value round-trips through JSON and through a text column without a
    conversion at the call site, and so ``environment == "LIVE"`` in existing code that
    compares against a string keeps working.
    """

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


#: The three values as written in the database check constraints. A tuple, in the order the
#: constraints enumerate them, so the SQL and this module read the same way.
EXECUTION_ENVIRONMENTS: Tuple[ExecutionEnvironment, ...] = (
    ExecutionEnvironment.BACKTEST,
    ExecutionEnvironment.PAPER,
    ExecutionEnvironment.LIVE,
)

#: The audited act recorded when the live path refuses a non-LIVE order. Spelled here as a
#: string because ``StrategyAuditAction.EXECUTION_ENVIRONMENT_MISMATCH`` is added by task
#: 14.3; ``_mismatch_audit_action`` prefers the enum member the moment it exists, and the
#: member's value is this same string, so the recorded ``action`` is identical either way.
EXECUTION_ENVIRONMENT_MISMATCH_ACTION = "execution_environment_mismatch"


class ExecutionEnvironmentError(Exception):
    """Base of the two live-path refusals. Never raised directly."""


class UnresolvedExecutionEnvironment(ExecutionEnvironmentError):
    """Requirement 13.10: the order carries no Execution_Environment, or one out of set.

    Carries the offending value's ``repr`` in ``message`` and the value itself in
    ``received``, because "no environment" and ``"live "`` need different fixes and the
    caller cannot tell them apart from the type alone.
    """

    def __init__(self, received: Any) -> None:
        self.received = received
        self.code = "EXECUTION_ENVIRONMENT_UNRESOLVED"
        super().__init__(
            "Unresolved Execution_Environment: expected exactly one of "
            f"{[member.value for member in EXECUTION_ENVIRONMENTS]}, received {received!r}. "
            "The live order path refuses an order it cannot attribute to an environment and "
            "does not default it to LIVE."
        )


class ExecutionEnvironmentMismatch(ExecutionEnvironmentError):
    """Requirement 13.9: a resolved environment that is not ``LIVE`` reached the live path."""

    def __init__(self, received: ExecutionEnvironment) -> None:
        self.received = received
        self.code = "EXECUTION_ENVIRONMENT_MISMATCH"
        super().__init__(
            f"Execution_Environment mismatch: the live order path received a "
            f"{ExecutionEnvironment(received).value} order. No exchange call was issued and no "
            "balance changed."
        )


def parse_execution_environment(value: Any) -> Optional[ExecutionEnvironment]:
    """The ``ExecutionEnvironment`` this value *is*, or ``None`` if it is not one.

    Exact spelling only, and no default. An ``ExecutionEnvironment`` member passes through;
    a string passes only if it equals one of the three values character for character.
    Everything else - ``None``, ``""``, ``"live"``, ``"live "``, ``0``, ``True``, an object
    whose ``str()`` happens to read ``"LIVE"`` - resolves to ``None``.
    """
    if isinstance(value, ExecutionEnvironment):
        return value
    if isinstance(value, str):
        # `ExecutionEnvironment` has a `str` mixin, so a plain `str` subclass instance is
        # accepted here by value, not by identity. Lookup is by value and raises on a miss.
        try:
            return ExecutionEnvironment(str(value))
        except ValueError:
            return None
    return None


def _mismatch_audit_action() -> Any:
    """``StrategyAuditAction.EXECUTION_ENVIRONMENT_MISMATCH`` if it exists, else its value.

    Task 14.3 adds the member. Until it lands, the string is passed instead:
    ``StrategyAuditRecord.to_dict`` renders a non-member action with ``str()``, and the
    member's value is this same string, so the record an auditor reads does not change
    shape when 14.3 lands.
    """
    try:
        from backend_app.core.audit_trail import StrategyAuditAction
    except Exception as exc:  # noqa: BLE001 - an audit import must not fail the refusal
        logger.warning("Strategy audit action vocabulary unavailable: %s", exc)
        return EXECUTION_ENVIRONMENT_MISMATCH_ACTION
    return getattr(
        StrategyAuditAction,
        "EXECUTION_ENVIRONMENT_MISMATCH",
        EXECUTION_ENVIRONMENT_MISMATCH_ACTION,
    )


async def _record_mismatch(
    received: ExecutionEnvironment,
    *,
    actor_id: str,
    strategy_id: Optional[str],
    symbol: Optional[str],
    execution_id: Optional[str],
    audit_logger: Any,
) -> Any:
    """Write Requirement 13.9's Audit_Log entry. Returns the record, or ``None``.

    Never raises: a refused order must be refused whether or not the trail could be
    written, and a failed write is logged at warning level so the gap is visible.
    """
    try:
        if audit_logger is None:
            from backend_app.core.audit_trail import get_strategy_audit_logger

            audit_logger = get_strategy_audit_logger()

        metadata: Dict[str, Any] = {
            "received_environment": ExecutionEnvironment(received).value,
            "expected_environment": ExecutionEnvironment.LIVE.value,
            "guard": "assert_live_environment",
            "requirement": "13.9",
        }
        if symbol:
            metadata["symbol"] = symbol
        if execution_id:
            metadata["execution_id"] = execution_id

        return await audit_logger.log(
            _mismatch_audit_action(),
            actor_id=actor_id,
            resource_type="strategy",
            resource_id=str(strategy_id or "unknown"),
            reason=(
                "Live order path refused an order whose Execution_Environment is "
                f"{ExecutionEnvironment(received).value}; no exchange call was issued"
            ),
            before=ExecutionEnvironment(received).value,
            after=None,
            strategy_id=str(strategy_id) if strategy_id else None,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 - the refusal stands regardless
        logger.warning(
            "Execution_Environment mismatch (%s) was refused but not audited: %s",
            ExecutionEnvironment(received).value
            if isinstance(received, ExecutionEnvironment)
            else received,
            exc,
        )
        return None


async def assert_live_environment(
    env: Any,
    *,
    actor_id: str = "system",
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    execution_id: Optional[str] = None,
    audit_logger: Any = None,
) -> ExecutionEnvironment:
    """Refuse anything but ``LIVE``, before the live order path does anything at all.

    Returns ``ExecutionEnvironment.LIVE`` when the order may proceed, so a call site can
    bind the resolved value rather than re-parsing it.

    Raises
    ------
    UnresolvedExecutionEnvironment
        ``env`` is absent or outside the three values (Requirement 13.10). Nothing is
        assumed to be ``LIVE``.
    ExecutionEnvironmentMismatch
        ``env`` is ``PAPER`` or ``BACKTEST`` (Requirement 13.9). An Audit_Log entry is
        written first.

    ``actor_id``, ``strategy_id``, ``symbol`` and ``execution_id`` only identify the refused
    order in the audit record; none of them affects the verdict. ``audit_logger`` is the
    seam a test uses to observe the write without a Redis instance.
    """
    resolved = parse_execution_environment(env)

    if resolved is None:
        # Requirement 13.10. No audit action exists for a value that is not an
        # Execution_Environment, so the refusal is recorded in the log - which is the
        # sink `StrategyAuditLogger` itself writes first and unconditionally.
        logger.error(
            "Live order path refused an order with an unresolved Execution_Environment: "
            "received=%r strategy_id=%s symbol=%s. Not defaulted to LIVE (Requirement 13.10).",
            env,
            strategy_id,
            symbol,
        )
        raise UnresolvedExecutionEnvironment(env)

    if resolved is not ExecutionEnvironment.LIVE:
        # Requirement 13.9: audit, then refuse. The write precedes the raise so the trail
        # holds the rejection even if the caller turns the exception into a response and
        # nothing further runs.
        await _record_mismatch(
            resolved,
            actor_id=str(actor_id or "system"),
            strategy_id=strategy_id,
            symbol=symbol,
            execution_id=execution_id,
            audit_logger=audit_logger,
        )
        logger.error(
            "Live order path refused a %s order: strategy_id=%s symbol=%s. No exchange call "
            "was issued (Requirement 13.9).",
            resolved.value,
            strategy_id,
            symbol,
        )
        raise ExecutionEnvironmentMismatch(resolved)

    return ExecutionEnvironment.LIVE


__all__ = [
    "ExecutionEnvironment",
    "EXECUTION_ENVIRONMENTS",
    "EXECUTION_ENVIRONMENT_MISMATCH_ACTION",
    "ExecutionEnvironmentError",
    "UnresolvedExecutionEnvironment",
    "ExecutionEnvironmentMismatch",
    "parse_execution_environment",
    "assert_live_environment",
]
