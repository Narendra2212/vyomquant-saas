"""The durable leg of the safety-control audit trail.

KILL-02 / KILL-03. WHY THIS MODULE EXISTS
-----------------------------------------
Before Phase 9 the only records of a platform halt were:

* a structured log line (``routers/admin.py::_log_admin_audit_event``);
* a best-effort ``RPUSH`` to the unbounded Redis LIST ``audit:admin_actions``, inside a
  ``try/except`` that only warns;
* a ``LPUSH`` to ``system:kill_switch_history`` carrying an ``EXPIRE`` applied **only in
  ``activate()``** (``core/global_safety.py:368``), so a reset entry inherits a
  partially-elapsed 7-day clock and can vanish within seconds of being written.

All three go through helpers that swallow every exception, so the platform could be
halted and leave **no record at all** -- and a Redis outage is itself a plausible reason
somebody is halting the platform, which is exactly when the trail disappears.

This module writes the same facts to ``public.safety_control_audit``
(``backend_app/migrations/028_safety_control_audit.sql``), which is append-only and has
no expiry. The Redis list and the log line are both retained; what changes is which one
is the system of record.

WHY THE WRITE IS BEST-EFFORT AND NEVER RAISES INTO THE CALLER
-------------------------------------------------------------
A safety control must not fail to engage because its audit row could not be written. If
the database is unreachable at the moment an operator halts trading, the correct outcome
is "halted, and loudly complain that the audit row is missing", not "refused to halt".

That is the opposite of the policy for *reading* kill-switch state, which fails closed --
and the asymmetry is deliberate: an unreadable state might mean trading is permitted when
it is not, whereas an unwritten audit row cannot make an unsafe order reach a venue.

The failure is reported at CRITICAL with the full record inline, so the log line remains a
recoverable copy. :func:`record_safety_transition` returns the audit id on success and
``None`` on failure, so a caller that wants to assert durability can.

THE WRITE IS SYNCHRONOUS SQLALCHEMY, RUN OFF THE EVENT LOOP
-----------------------------------------------------------
``SessionLocal`` is the repository's synchronous session factory and every other core
module uses it (``execution_engine``, ``consistency_checker``, ``readiness``). Calling it
directly inside a coroutine would block the loop for the duration of the insert, so it is
dispatched with ``asyncio.to_thread`` -- the same shape ``core/readiness.probe_database``
uses for its synchronous probe.

Regression: tests/test_kill03_authoritative_global_halt.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("SafetyAudit")

#: The control name recorded for the platform-wide kill switch. A constant rather than a
#: literal at each call site so the trail is queryable by an exact value
#: (`WHERE control = 'global_kill_switch'`) and a typo cannot silently create a second
#: control that no query finds.
CONTROL_GLOBAL_KILL_SWITCH = "global_kill_switch"

#: The two transitions. Mirrors `chk_safety_control_audit_action`, so a value this module
#: would be refused for is caught here with a readable message instead of as a 23514.
ACTION_ACTIVATE = "ACTIVATE"
ACTION_DEACTIVATE = "DEACTIVATE"
_ACTIONS = frozenset({ACTION_ACTIVATE, ACTION_DEACTIVATE})

#: APPLIED = the transition took effect. REFUSED = it was attempted and denied.
#: Recording REFUSED is the point of this column: an unauthorized or unverifiable reset
#: attempt on an emergency control is precisely the event an operator needs to see, and
#: nothing recorded it before.
OUTCOME_APPLIED = "APPLIED"
OUTCOME_REFUSED = "REFUSED"
_OUTCOMES = frozenset({OUTCOME_APPLIED, OUTCOME_REFUSED})

_INSERT_SQL = """
INSERT INTO public.safety_control_audit
       (audit_id, control, action, outcome, reason, operator_id, operator_email,
        triggered_by, authorization_source, detail)
VALUES (:audit_id, :control, :action, :outcome, :reason, :operator_id, :operator_email,
        :triggered_by, :authorization_source, CAST(:detail AS jsonb))
"""


def _coerce_operator_id(value: Any) -> Optional[str]:
    """``value`` as a uuid string, or ``None``.

    ``operator_id`` is a ``uuid`` column. A caller that has an operator identifier which
    is not a uuid (a Supabase ``sub`` claim is one, but a test double or a future identity
    provider may not be) must not take the whole audit row down with a 22P02. The id is
    dropped to NULL and the original is preserved in ``detail`` instead, so nothing is
    lost and the row still lands.
    """
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _record_sync(
    *,
    control: str,
    action: str,
    outcome: str,
    reason: str,
    operator_id: Any,
    operator_email: Optional[str],
    triggered_by: str,
    authorization_source: Optional[str],
    detail: Dict[str, Any],
) -> Optional[str]:
    """Insert one row and return its audit id. Runs on a worker thread."""
    from sqlalchemy import text

    from backend_app.core.database import SessionLocal

    audit_id = str(uuid.uuid4())
    coerced = _coerce_operator_id(operator_id)
    payload = dict(detail)
    if operator_id is not None and coerced is None:
        # Preserved rather than discarded: knowing WHICH non-uuid identifier acted is the
        # whole value of the row.
        payload["operator_id_raw"] = str(operator_id)

    session = SessionLocal()
    try:
        session.execute(
            text(_INSERT_SQL),
            {
                "audit_id": audit_id,
                "control": control,
                "action": action,
                "outcome": outcome,
                "reason": reason,
                "operator_id": coerced,
                "operator_email": operator_email,
                "triggered_by": triggered_by,
                "authorization_source": authorization_source,
                "detail": json.dumps(payload, default=str, sort_keys=True),
            },
        )
        session.commit()
        return audit_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def record_safety_transition(
    *,
    control: str,
    action: str,
    outcome: str,
    reason: str,
    triggered_by: str,
    operator_id: Any = None,
    operator_email: Optional[str] = None,
    authorization_source: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Record one safety-control transition durably. Returns the audit id, or ``None``.

    Never raises. See the module docstring for why a failed audit write must not prevent a
    safety control from engaging, and why that is the opposite of the fail-closed policy
    applied to *reading* control state.

    Args:
        control: the control being transitioned, e.g.
            :data:`CONTROL_GLOBAL_KILL_SWITCH`.
        action: :data:`ACTION_ACTIVATE` or :data:`ACTION_DEACTIVATE`.
        outcome: :data:`OUTCOME_APPLIED` or :data:`OUTCOME_REFUSED`.
        reason: the human-readable reason. Must not be blank -- the table's
            ``chk_safety_control_audit_reason_not_blank`` refuses one, and an emergency
            control with an empty reason is the gap, not an edge case.
        triggered_by: the component or actor label. For an automatic trigger this is the
            subsystem (``health_monitor``, ``reconciliation_engine``, ...); for an
            operator action it identifies the operator.
        operator_id: the authenticated operator's id, or ``None`` for an automatic
            trigger. Coerced to uuid; a non-uuid value is preserved in ``detail``.
        operator_email: the operator's email, for a human-readable trail.
        authorization_source: how the actor's authority was established -- e.g. the
            dependency that resolved the operator role. KILL-02 was that
            ``deactivate()`` accepted a free-text ``triggered_by`` nothing verified, so
            the trail could not tell an authorized reset from a claimed one.
        detail: any additional machine-readable facts. Serialised to ``jsonb``, not to a
            Python ``str(dict)`` repr -- ``global_safety.activate()`` writes its Redis
            metadata the latter way (``global_safety.py:382``) and the result needs
            ``ast.literal_eval`` to read back.
    """
    if action not in _ACTIONS:
        logger.error(
            "SAFETY AUDIT: refusing to record an unknown action %r (expected one of %s). "
            "Recording nothing rather than a row the table's CHECK would reject.",
            action, sorted(_ACTIONS),
        )
        return None
    if outcome not in _OUTCOMES:
        logger.error(
            "SAFETY AUDIT: refusing to record an unknown outcome %r (expected one of %s).",
            outcome, sorted(_OUTCOMES),
        )
        return None

    safe_reason = (reason or "").strip() or "(no reason supplied)"
    safe_trigger = (triggered_by or "").strip() or "unknown"

    try:
        audit_id = await asyncio.to_thread(
            _record_sync,
            control=control,
            action=action,
            outcome=outcome,
            reason=safe_reason,
            operator_id=operator_id,
            operator_email=operator_email,
            triggered_by=safe_trigger,
            authorization_source=authorization_source,
            detail=detail or {},
        )
    except Exception as exc:  # noqa: BLE001 - an audit failure must not block a halt
        # CRITICAL with the whole record inline, so the log line is a recoverable copy of
        # the row that did not land.
        logger.critical(
            "SAFETY AUDIT WRITE FAILED (%s: %s). The transition itself was NOT affected. "
            "Record, for recovery: control=%s action=%s outcome=%s triggered_by=%s "
            "operator_id=%s operator_email=%s authorization_source=%s reason=%r detail=%s",
            exc.__class__.__name__, exc,
            control, action, outcome, safe_trigger,
            operator_id, operator_email, authorization_source, safe_reason,
            json.dumps(detail or {}, default=str, sort_keys=True),
        )
        return None

    logger.info(
        "SAFETY AUDIT: %s %s %s recorded as %s (operator=%s, triggered_by=%s)",
        control, action, outcome, audit_id, operator_email or operator_id, safe_trigger,
    )
    return audit_id


__all__ = [
    "CONTROL_GLOBAL_KILL_SWITCH",
    "ACTION_ACTIVATE",
    "ACTION_DEACTIVATE",
    "OUTCOME_APPLIED",
    "OUTCOME_REFUSED",
    "record_safety_transition",
]
