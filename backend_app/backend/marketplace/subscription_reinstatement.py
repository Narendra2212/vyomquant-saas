"""
backend_app/backend/marketplace/subscription_reinstatement.py - lifting an administrative hold.

Spec: marketplace-subscriptions-paper-trading, task 22 remediation. ``design.md`` ->
"``marketplace/subscription_reinstatement.py``". Requirements 5.1, 11.2, 11.6, 11.9, 11.12,
11.13, 11.14, **11.17**, 21.1, 22.10.

Exposes
-------
REINSTATEMENT_SUBSCRIPTION_SELECT  the explicit Subscription projection this path reads
REINSTATEMENT_SETTLEMENT_SELECT    the explicit Settlement_Record projection the probe reads
CAUSE_ADMIN_REINSTATEMENT          the ``library_subscription_transitions.cause`` this writes
MAX_REASON_CHARS                   the bound on the recorded reason
ReinstatementError                 the domain failure, carrying a catalogue *code string*
ReinstatementResult                the frozen value type :func:`reinstate` returns
validate_reason(...)               the reason rule, applied before any read
reinstate(...)                     the single entry point

WHY THIS MODULE EXISTS
----------------------
A ``SUSPENDED`` Subscription had no settlement-free route back to ``ACTIVE``, so a purchaser
suspended by an administrator had to **buy a fresh month** to recover access they had already
paid for.

* Requirement 11.2 PERMITS ``SUSPENDED -> ACTIVE``, and
  ``008_marketplace_settlement.sql`` seeds that edge.
* Requirement 11.6 requires a confirmed payment before every transition into ``ACTIVE``
  "from any of ``PENDING``, ``EXPIRED``, ``CANCELLED`` and ``PAYMENT_FAILED``" -
  ``SUSPENDED`` is absent from that list, so the edge existed with no stated payment rule.
* The implementation took the stricter reading: ``settlement_service`` was the only writer of
  ``status='active'``, and branch 3 of ``marketplace_subscription_guard()`` was keyed on
  ``NEW.status = 'active'`` alone. Every route into ``ACTIVE`` therefore required a NEW
  Settlement_Record, and ``settlement_service.ELIGIBLE_FOR_ACTIVATION`` carries ``suspended``
  precisely because a checkout was the only route. ``routers/billing.py::resume_subscription``
  acts on the platform plan, not ``library_subscriptions``, so it was not a route either.

THE REQUIREMENTS DECISION THIS MODULE IMPLEMENTS (Requirement 11.17)
-------------------------------------------------------------------
**A suspension is a hold, not a refund.** The period the purchaser paid for is still theirs, so
lifting the hold restores access WITHOUT a charge and WITHOUT moving the period. Concretely:

* ``period_start``, ``period_expiry`` and the retained ``started_at`` / ``expires_at`` mirrors
  are **not written**. The UPDATE payload is exactly ``{"status": "active"}`` - see
  :data:`_REINSTATEMENT_PAYLOAD_COLUMNS`. Reinstatement restores the REMAINING period, never a
  fresh one.
* An already-elapsed period therefore hands back **no** entitlement, and this module does not
  special-case it: ``entitlement_resolver.resolve`` compares ``now`` with ``period_expiry`` on
  every call irrespective of the stored status (Requirement 11.7), and the expiry sweep moves
  the row to ``EXPIRED`` on its next pass (Requirement 11.8). Refusing an elapsed reinstatement
  here would be a second, weaker copy of a rule the resolver already enforces exactly.
* No money moves. There is no amount, no currency arithmetic, no ``float``, no
  ``marketplace_settlements`` write and no fresh ``deployment_permissions`` grant on this path -
  the grant the settled payment wrote already carries the period expiry.

``SETTLEMENT_SERVICE`` IS STILL THE ONLY WRITER OF A **PAID** ACTIVATION
-----------------------------------------------------------------------
This is deliberately a **separate, explicitly administrative** writer, not a branch of
``settle``. ``settlement_service`` writes the Settlement_Record first and activates second
(Requirement 9.5); its invariant is "the money is recorded before any entitlement". Folding an
unpaid path into it would put a route through that function which writes no ledger row, and the
one sentence that makes it auditable - *every* activation it performs has a Settlement_Record
in the same call - would stop being true. Two writers with two different, stated preconditions
are honest; one writer with an internal exception is not.

WHAT MAKES THE DATABASE AGREE (Requirement 11.14 is not weakened)
-----------------------------------------------------------------
``backend_app/migrations/014_subscription_admin_reinstatement.sql`` replaces
``marketplace_subscription_guard()`` so its activation branch has two arms:

* the administrative reinstatement - reached only when
  ``marketplace_subscription_reinstatement_shape()`` says the write is ``suspended -> active``
  with **both period boundaries unchanged** and a stored expiry present - which still requires a
  non-reversal ``marketplace_settlements`` row settled at or before that expiry, i.e. **the
  payment that bought the period being resumed**; and
* 008's probe, verbatim, for every other source.

So a Subscription that never paid cannot be reinstated into access it never bought, and
``pending``, ``expired``, ``cancelled`` and ``payment_failed`` still demand a payment settled at
or after the current expiry. The migration's postflight proves both by CALLING the predicate.
The checks in this module are the fast gate; the database is the arbiter, and it fails closed.

THE ORDER OF THE STEPS, AND WHY THE AUDIT IS LAST
-------------------------------------------------
The shape ``submission_service.apply_admin_action`` already uses for an Admin_Reviewer action:

  1. the reason rule, **before any read** - a reinstatement with no recorded reason is refused
     and touches nothing (Requirement 11.12 requires the cause and the acting identity on the
     record, and a blank reason makes the record unreadable);
  2. read the Subscription (an explicit projection, never ``select("*")``);
  3. refuse anything that is not a ``suspended`` row with a stored period;
  4. refuse unless the period being resumed was paid for - the application half of the guard's
     arm 3a;
  5. the status UPDATE, guarded by ``.eq("status", "suspended")`` so a concurrent writer that
     already moved the row makes this a no-op the caller sees as a refusal, never a lost update;
  6. the ``library_subscription_transitions`` row - ``from_state='suspended'``,
     ``to_state='active'``, ``cause='admin_reinstatement'`` (Requirement 11.12);
  7. the Audit_Log entry, **last**, through ``record_or_raise``. If it cannot be written the
     status is reverted to ``suspended`` (an edge Requirement 11.2 permits) and
     ``MARKETPLACE_ACTION_NOT_RECORDED`` is raised, so a restored entitlement is never left
     persisted-and-unaudited.

The history row's ``cause`` is ``'admin_reinstatement'`` and **not** ``'settlement'``: a reader
must be able to tell a paid activation from an administrative one (Requirement 11.12), and the
two causes are the only thing that distinguishes them on a row where the period did not move.

NO FASTAPI, NO ERROR-CATALOGUE IMPORT
-------------------------------------
This module obeys the package's layering rule. ``errors.py`` imports FastAPI to register its
exception handler, so it is **not** imported here; the catalogue codes are named as string
constants, exactly as ``submission_service`` names its own, and
``tests/test_subscription_reinstatement_regression.py`` asserts they equal the catalogue's. The
route layer maps a :class:`ReinstatementError` onto the matching ``MarketplaceError`` by code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
    can_transition,
)

logger = logging.getLogger("MarketplaceSubscriptionReinstatement")

__all__ = [
    "REINSTATEMENT_SUBSCRIPTION_SELECT",
    "REINSTATEMENT_SETTLEMENT_SELECT",
    "SUBSCRIPTION_TABLE",
    "SETTLEMENT_TABLE",
    "TRANSITION_TABLE",
    "CAUSE_ADMIN_REINSTATEMENT",
    "MAX_REASON_CHARS",
    "MARKETPLACE_ACTION_NOT_RECORDED",
    "MARKETPLACE_PAYMENT_REQUIRED",
    "MARKETPLACE_READ_FAILED",
    "MARKETPLACE_REASON_REQUIRED",
    "NOT_FOUND",
    "ReinstatementError",
    "ReinstatementResult",
    "validate_reason",
    "reinstate",
]


# ══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: The three tables this module touches, named once.
SUBSCRIPTION_TABLE = "library_subscriptions"
SETTLEMENT_TABLE = "marketplace_settlements"
TRANSITION_TABLE = "library_subscription_transitions"

#: The explicit Subscription projection. No ``select("*")``: every column is named, and the set
#: is bound by the ``subscription_reinstatement`` entry of this package's ``COLUMN_CONTRACT``.
#:
#: ``status`` is both the source state and the optimistic guard on the UPDATE; ``period_start``
#: and ``period_expiry`` are the boundaries this path must NOT move and the anchor the settlement
#: probe compares against; ``user_id``, ``library_id`` and ``owner_id`` are the identity columns
#: the history row and the audit entry carry. No money column is read, because no figure on this
#: path is derived from one.
REINSTATEMENT_SUBSCRIPTION_SELECT = (
    "id,library_id,user_id,owner_id,status,period_start,period_expiry"
)

#: The Settlement_Record projection the payment probe reads. Four columns: the ones that decide
#: whether the period being resumed was bought. Deliberately **no** money column - this path
#: asks "was there a payment?", never "how much was it?", so no amount can leak into it.
REINSTATEMENT_SETTLEMENT_SELECT = "id,subscription_id,is_reversal,settled_at"

#: The ``library_subscription_transitions.cause`` this module writes, and the whole reason a
#: reader can tell an administrative reinstatement from a paid activation (Requirement 11.12).
#: ``settlement_service`` writes ``'settlement'`` and ``'refund'``; this is neither.
CAUSE_ADMIN_REINSTATEMENT = "admin_reinstatement"

#: The bound on the recorded reason, matching the admin-action reason bound of Requirement 5.5.
MAX_REASON_CHARS = 2000

#: The status spellings, resolved through the one enum -> column mapping rather than re-spelled.
_STATUS_SUSPENDED = STATUS_TEXT_FOR_STATE[SubscriptionState.SUSPENDED]
_STATUS_ACTIVE = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]

#: The one source state Requirement 11.17 authorises a settlement-free activation from. A
#: frozenset of one, not a bare string, so the intent is greppable and a future widening is a
#: visible edit at a named constant rather than an inline literal.
REINSTATABLE_FROM: FrozenSet[str] = frozenset({_STATUS_SUSPENDED})

#: The columns the UPDATE payload may contain. Exactly one. Requirement 11.17's "the period is
#: untouched" is enforced here by construction rather than trusted: :func:`_status_payload`
#: asserts against this set, so a future edit that adds ``period_expiry`` to the payload fails
#: inside this module rather than silently re-issuing a purchaser's month.
_REINSTATEMENT_PAYLOAD_COLUMNS: FrozenSet[str] = frozenset({"status"})

#: The audit action Requirement 11.12's Subscription_State transition record uses (design.md ->
#: "The Audit_Log actions"). Named, not imported: the audit facility is imported lazily.
_AUDIT_ACTION = "MARKETPLACE_SUBSCRIPTION_TRANSITIONED"

#: The catalogue codes this module raises, named as strings for the layering reason in the module
#: docstring. ``errors.py`` is the authority for their HTTP status and public sentence.
NOT_FOUND = "NOT_FOUND"
MARKETPLACE_PAYMENT_REQUIRED = "MARKETPLACE_PAYMENT_REQUIRED"
MARKETPLACE_REASON_REQUIRED = "MARKETPLACE_REJECTION_REASON_REQUIRED"
MARKETPLACE_ACTION_NOT_RECORDED = "MARKETPLACE_ACTION_NOT_RECORDED"
MARKETPLACE_READ_FAILED = "MARKETPLACE_READ_FAILED"

#: The HTTP status per code, from the catalogue's own table. Held here so a
#: :class:`ReinstatementError` is answerable without importing the FastAPI-bearing module, and
#: asserted equal to ``errors.HTTP_STATUS_FOR_CODE`` by this module's test.
_HTTP_STATUS_FOR_CODE: Mapping[str, int] = {
    NOT_FOUND: 404,
    MARKETPLACE_PAYMENT_REQUIRED: 409,
    MARKETPLACE_REASON_REQUIRED: 422,
    MARKETPLACE_ACTION_NOT_RECORDED: 500,
    MARKETPLACE_READ_FAILED: 503,
}


# ══════════════════════════════════════════════════════════════════════════
# THE OUTCOMES
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ReinstatementError(Exception):
    """A reinstatement failure carrying a catalogue *code string*.

    The same shape ``submission_service.SubmissionServiceError`` uses, for the same reason: the
    route layer maps ``code`` onto the matching ``MarketplaceError`` and the catalogue supplies
    the public sentence, so no internal wording reaches a client. ``details`` carries only the
    caller's own values (the current status on a refusal), never a database string or a path.
    """

    code: str
    message: str = ""
    http_status: int = 500
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - convenience for logs
        return f"{self.code}: {self.message}"


def _raise(code: str, message: str = "", **details: Any) -> "ReinstatementError":
    """Construct (and return, for ``raise _raise(...)``) the error for ``code``."""
    return ReinstatementError(
        code=code,
        message=message,
        http_status=_HTTP_STATUS_FOR_CODE.get(code, 500),
        details={key: value for key, value in details.items() if value is not None},
    )


@dataclass(frozen=True)
class ReinstatementResult:
    """What one :func:`reinstate` call did.

    Frozen, and it carries no money field at all: this path moves none. ``period_start`` and
    ``period_expiry`` are the **unchanged** stored boundaries, reported so a caller can show the
    purchaser the remaining period without reading the row again.
    """

    subscription_id: str
    from_status: str
    to_status: str
    actor_id: Optional[str]
    reason: str
    period_start: Optional[datetime]
    period_expiry: Optional[datetime]
    cause: str = CAUSE_ADMIN_REINSTATEMENT
    reinstated_at: Optional[datetime] = None


# ══════════════════════════════════════════════════════════════════════════
# THE REASON RULE (Requirement 11.12)
# ══════════════════════════════════════════════════════════════════════════


def validate_reason(reason: Any) -> str:
    """The trimmed reason, or a refusal. Applied BEFORE any read or write.

    Requirement 11.12 puts the cause and the acting identity on the record of every
    Subscription_State transition. A reinstatement whose reason is absent, whitespace-only or
    longer than :data:`MAX_REASON_CHARS` is refused outright rather than recorded as an
    administrative act nobody can account for. Whitespace is collapsed onto one line so a pasted
    multi-line note cannot break the audit line's shape.

    Raises:
        ReinstatementError: ``MARKETPLACE_REJECTION_REASON_REQUIRED`` (422).
    """
    text = "" if reason is None else str(reason)
    flattened = " ".join(text.split())
    if not flattened:
        raise _raise(
            MARKETPLACE_REASON_REQUIRED,
            "A reason is required to reinstate a suspended subscription.",
        )
    if len(flattened) > MAX_REASON_CHARS:
        raise _raise(
            MARKETPLACE_REASON_REQUIRED,
            "The reason is longer than the permitted maximum.",
        )
    return flattened


# ══════════════════════════════════════════════════════════════════════════
# THE ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════


async def reinstate(
    admin: Any,
    subscription_id: Any,
    reason: Any,
    *,
    supabase: Any,
    now: Optional[datetime] = None,
) -> ReinstatementResult:
    """Lift an administrative hold: ``suspended -> active``, unpaid, period unchanged (Req 11.17).

    Args:
        admin: the Admin_Reviewer identity **resolved server-side** by ``get_admin_user`` at the
            route. Only its ``id`` is read; no identifier from a body, query, path or header
            participates (Requirements 5.1, 21.1, 22.10).
        subscription_id: the ``library_subscriptions.id`` to reinstate.
        reason: the recorded reason. Required (Requirement 11.12).
        supabase: the injected Persistence_Layer handle (service-role client at the route).
        now: the instant to stamp the history row and the audit entry with, in UTC. Passed in so
            the record is deterministic under test. **No period is derived from it** - that is
            the whole point of this path.

    Returns:
        A :class:`ReinstatementResult` reporting the unchanged period.

    Raises:
        ReinstatementError: ``MARKETPLACE_REJECTION_REASON_REQUIRED`` (no recorded reason),
            ``NOT_FOUND`` (no such Subscription), ``MARKETPLACE_PAYMENT_REQUIRED`` (the row is
            not a ``suspended`` row with a paid, stored period - a checkout is the route),
            ``MARKETPLACE_ACTION_NOT_RECORDED`` (the audit could not be written, so the status
            was reverted) or ``MARKETPLACE_READ_FAILED`` (a read or write did not complete).
    """
    # ── Step 1: the reason rule, before anything is read (Requirement 11.12). ──
    recorded_reason = validate_reason(reason)

    actor_id = _as_text(_get(admin, "id"))
    subscription_key = _as_text(subscription_id) or ""
    instant = _coerce_instant(now)

    # ── Step 2: the Subscription, on an explicit projection. ──
    subscription = _read_subscription(supabase, subscription_key)
    if subscription is None:
        raise _raise(NOT_FOUND, "That subscription was not found.")

    from_status = _normalise_status(_get(subscription, "status"))
    period_start = _parse_instant(_get(subscription, "period_start"))
    period_expiry = _parse_instant(_get(subscription, "period_expiry"))

    # ── Step 3: only a suspended row with a stored period may be reinstated. ──
    #
    # Every other source needs a checkout, which is what MARKETPLACE_PAYMENT_REQUIRED says: "this
    # subscription cannot become active until a confirmed payment has been recorded for it".
    # ``can_transition`` is consulted as well so this module reads the state machine rather than
    # re-stating it - the edge is Requirement 11.2's, not this path's invention.
    if from_status not in REINSTATABLE_FROM:
        raise _raise(
            MARKETPLACE_PAYMENT_REQUIRED,
            "Only a suspended subscription can be reinstated without a payment.",
            current=from_status,
            rejected=_STATUS_ACTIVE,
        )
    if not can_transition(SubscriptionState.SUSPENDED, SubscriptionState.ACTIVE):
        # Unreachable while Requirement 11.2 permits the edge. Asserted rather than assumed: if
        # the transition table ever drops the pair, this path must stop writing, not diverge from
        # the state machine the way task 19.15's defect did.
        raise _raise(
            MARKETPLACE_PAYMENT_REQUIRED,
            "Only a suspended subscription can be reinstated without a payment.",
            current=from_status,
            rejected=_STATUS_ACTIVE,
        )
    if period_expiry is None:
        raise _raise(
            MARKETPLACE_PAYMENT_REQUIRED,
            "This subscription has no paid period to resume.",
            current=from_status,
        )

    # ── Step 4: the period being resumed must have been PAID for (Req 11.6, 11.14). ──
    if not _period_was_paid_for(supabase, subscription_key, period_expiry):
        raise _raise(
            MARKETPLACE_PAYMENT_REQUIRED,
            "No confirmed payment is recorded for the period this subscription would resume.",
            current=from_status,
        )

    # ── Step 5: the status UPDATE. One column. ──
    _apply_status(supabase, subscription_key, from_status=from_status)

    # ── Step 6: the history row (Requirement 11.12). ──
    _write_transition_row(
        supabase,
        subscription=subscription,
        actor_id=actor_id,
        reason=recorded_reason,
        instant=instant,
        period_expiry=period_expiry,
    )

    # ── Step 7: the audit, LAST, and required. ──
    try:
        await _record_reinstatement(
            actor_id=actor_id,
            subscription=subscription,
            reason=recorded_reason,
            instant=instant,
            period_start=period_start,
            period_expiry=period_expiry,
        )
    except Exception as exc:  # noqa: BLE001 - Req 11.12: never persisted-and-unaudited
        _revert_status(supabase, subscription_key)
        raise _raise(
            MARKETPLACE_ACTION_NOT_RECORDED,
            "The reinstatement could not be recorded and was rolled back.",
        ) from exc

    logger.info(
        "subscription %s reinstated by admin %s; period unchanged (expiry %s), no settlement "
        "written",
        subscription_key,
        actor_id,
        period_expiry.isoformat() if period_expiry else None,
    )
    return ReinstatementResult(
        subscription_id=subscription_key,
        from_status=from_status or "",
        to_status=_STATUS_ACTIVE,
        actor_id=actor_id,
        reason=recorded_reason,
        period_start=period_start,
        period_expiry=period_expiry,
        reinstated_at=instant,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE READS AND WRITES
# ══════════════════════════════════════════════════════════════════════════


def _read_subscription(supabase: Any, subscription_id: str) -> Optional[Mapping[str, Any]]:
    """The Subscription this action names, or ``None`` when there is no such row.

    Raises:
        ReinstatementError: ``MARKETPLACE_READ_FAILED`` - the read did not complete. NOT
            ``None``: a broken read reported as "not found" would tell an administrator
            something false about a purchaser's subscription.
    """
    try:
        response = (
            supabase.table(SUBSCRIPTION_TABLE)
            .select(REINSTATEMENT_SUBSCRIPTION_SELECT)
            .eq("id", subscription_id)
            .execute()
        )
        rows = _rows(response)
    except ReinstatementError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, never swallowed
        raise _raise(
            MARKETPLACE_READ_FAILED,
            f"the subscription read for {subscription_id} did not complete: {exc}",
        ) from exc
    return rows[0] if rows else None


def _period_was_paid_for(
    supabase: Any, subscription_id: str, period_expiry: datetime
) -> bool:
    """Whether a non-reversal Settlement_Record bought the period being resumed.

    The application half of the guard's arm 3a, and the same predicate: a
    ``marketplace_settlements`` row for THIS subscription, ``is_reversal = FALSE``, settled **at
    or before** the stored ``period_expiry``. A Subscription that never paid has none, so it
    cannot be reinstated into access it never bought (Requirements 11.6, 11.14).

    The comparison is done here rather than as a ``.lte`` filter because the stored value is a
    ``TIMESTAMPTZ`` string whose formatting varies by driver; parsing both sides to UTC instants
    and comparing instants is the same arithmetic ``entitlement_resolver`` does with
    ``period_expiry``, and it cannot be defeated by a text ordering difference.

    Raises:
        ReinstatementError: ``MARKETPLACE_READ_FAILED`` - the read did not complete. A failure is
            never read as "unpaid" and never as "paid": it is an outage, and the caller retries.
    """
    try:
        response = (
            supabase.table(SETTLEMENT_TABLE)
            .select(REINSTATEMENT_SETTLEMENT_SELECT)
            .eq("subscription_id", subscription_id)
            .eq("is_reversal", False)
            .execute()
        )
        rows = _rows(response)
    except ReinstatementError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, never swallowed
        raise _raise(
            MARKETPLACE_READ_FAILED,
            f"the settlement read for {subscription_id} did not complete: {exc}",
        ) from exc

    for row in rows:
        # ``.eq("is_reversal", False)`` is the server-side filter; this is the in-memory
        # re-scope, for the same reason ``entitlement_resolver._caller_subscription`` re-scopes
        # its embed: a client double that ignores the filter must not be able to make a refund
        # fund a reinstatement.
        if _is_true(_get(row, "is_reversal")):
            continue
        settled_at = _parse_instant(_get(row, "settled_at"))
        if settled_at is not None and settled_at <= period_expiry:
            return True
    return False


def _status_payload() -> Dict[str, Any]:
    """The UPDATE payload: ``{"status": "active"}`` and nothing else (Requirement 11.17).

    Built through a function with an assertion rather than written inline at the call site, so
    "the period is untouched" is a property of this module and not of one statement somebody
    might widen later. ``period_start``, ``period_expiry``, ``started_at`` and ``expires_at`` are
    absent, so the purchaser's paid period is resumed rather than re-issued.
    """
    payload: Dict[str, Any] = {"status": _STATUS_ACTIVE}
    assert set(payload) == _REINSTATEMENT_PAYLOAD_COLUMNS, (
        "the reinstatement UPDATE payload may contain the status and nothing else; a period "
        f"column here would move a purchaser's paid month: {sorted(payload)}"
    )
    return payload


def _apply_status(supabase: Any, subscription_id: str, *, from_status: Optional[str]) -> None:
    """Move the row to ``active``, guarded by the status that was read.

    ``.eq("status", from_status)`` is the optimistic lock PostgREST leaves us instead of
    ``SELECT … FOR UPDATE``, held exactly the way ``submission_service`` and
    ``settlement_service`` hold it: a concurrent writer that already moved the row makes this
    UPDATE match zero rows, which is reported as a refusal rather than read as success.

    Raises:
        ReinstatementError: ``MARKETPLACE_READ_FAILED`` when the statement did not complete, or
            ``MARKETPLACE_PAYMENT_REQUIRED`` when it matched no row because the row is no longer
            suspended.
    """
    try:
        response = (
            supabase.table(SUBSCRIPTION_TABLE)
            .update(_status_payload())
            .eq("id", subscription_id)
            .eq("status", from_status)
            .execute()
        )
        touched = _rows(response)
    except ReinstatementError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, never swallowed
        raise _raise(
            MARKETPLACE_READ_FAILED,
            f"the reinstatement of {subscription_id} did not complete: {exc}",
        ) from exc

    if not touched:
        raise _raise(
            MARKETPLACE_PAYMENT_REQUIRED,
            "Only a suspended subscription can be reinstated without a payment.",
            current=from_status,
            rejected=_STATUS_ACTIVE,
        )


def _write_transition_row(
    supabase: Any,
    *,
    subscription: Mapping[str, Any],
    actor_id: Optional[str],
    reason: str,
    instant: datetime,
    period_expiry: Optional[datetime],
) -> None:
    """Append one ``library_subscription_transitions`` row (Requirement 11.12).

    ``cause`` is :data:`CAUSE_ADMIN_REINSTATEMENT`, never ``'settlement'``: a reader must be able
    to tell a paid activation from an administrative one. Both expiry columns carry the **same**
    value, which is how the row itself says the period did not move.

    Raises:
        ReinstatementError: ``MARKETPLACE_READ_FAILED`` - the insert did not complete.
    """
    expiry_text = period_expiry.isoformat() if period_expiry else None
    payload: Dict[str, Any] = {
        "subscription_id": _as_text(_get(subscription, "id")),
        "user_id": _as_text(_get(subscription, "user_id")),
        "from_state": _STATUS_SUSPENDED,
        "to_state": _STATUS_ACTIVE,
        "cause": CAUSE_ADMIN_REINSTATEMENT,
        "actor_id": actor_id,
        "prior_period_expiry": expiry_text,
        "new_period_expiry": expiry_text,
        "transitioned_at": instant.isoformat(),
    }
    try:
        response = supabase.table(TRANSITION_TABLE).insert(payload).execute()
        _rows(response)
    except ReinstatementError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to the defined outcome, never swallowed
        raise _raise(
            MARKETPLACE_READ_FAILED,
            f"the transition history row for {payload['subscription_id']} did not complete: "
            f"{exc}",
        ) from exc


def _revert_status(supabase: Any, subscription_id: str) -> None:
    """Put the row back to ``suspended`` after an unwritable audit. Never raises.

    ``ACTIVE -> SUSPENDED`` is an edge Requirement 11.2 permits, so the compensation is
    expressible rather than a write the guard would refuse. The history row stays: it is
    append-only (``trg_lib_sub_transitions_append_only``) and it is the honest record that an
    attempt was made, while the reverted status is what a reader - and
    ``entitlement_resolver`` - sees. Best-effort, for the reason
    ``submission_service._compensate_admin_action`` gives: a failed revert is a reconciliation
    item, never a second exception masking the first.
    """
    try:
        supabase.table(SUBSCRIPTION_TABLE).update({"status": _STATUS_SUSPENDED}).eq(
            "id", subscription_id
        ).eq("status", _STATUS_ACTIVE).execute()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        logger.error(
            "the reinstatement of subscription %s could not be audited AND could not be "
            "reverted; it needs operator reconciliation",
            subscription_id,
        )


async def _record_reinstatement(
    *,
    actor_id: Optional[str],
    subscription: Mapping[str, Any],
    reason: str,
    instant: datetime,
    period_start: Optional[datetime],
    period_expiry: Optional[datetime],
) -> None:
    """Write the one Audit_Log entry, through ``record_or_raise`` (Requirement 11.12).

    ``record_or_raise``, not ``log``: Requirement 11.12 conditions the record on being written,
    so a storage failure must PROPAGATE for :func:`reinstate` to catch it and revert. The entry
    names the administrator (``actor_id``), the subscription (``resource_id``), the prior state
    (``before``) and the reason - the four things Requirement 11.12 asks for - plus the
    unchanged period, so the audit itself shows nothing was re-issued.

    The audit facility is imported lazily for the reason ``settlement_service`` gives: this
    module must stay importable and testable without pulling the audit stack, its Redis handle or
    an event loop behind it. It carries no payment credential, provider secret or card data.
    """
    from backend_app.core.audit_trail import (
        StrategyAuditAction,
        get_strategy_audit_logger,
    )

    action = getattr(StrategyAuditAction, _AUDIT_ACTION, None)
    if action is None:
        raise RuntimeError(f"no audit action {_AUDIT_ACTION}")

    audit_logger = get_strategy_audit_logger()
    writer = getattr(audit_logger, "record_or_raise", None)
    if not callable(writer):
        writer = getattr(audit_logger, "log", None)
    if not callable(writer):
        raise RuntimeError("audit logger unavailable")

    subscription_key = _as_text(_get(subscription, "id")) or "unknown"
    await writer(
        action,
        actor_id=actor_id or "unknown",
        resource_type="library_subscription",
        resource_id=subscription_key,
        reason=(
            f"administrative reinstatement of a suspended subscription, no payment taken and "
            f"the period unchanged: {reason}"
        ),
        before=_STATUS_SUSPENDED,
        after=_STATUS_ACTIVE,
        metadata={
            "subscription_id": subscription_key,
            "listing_id": _as_text(_get(subscription, "library_id")),
            "purchaser_id": _as_text(_get(subscription, "user_id")),
            "prior_state": _STATUS_SUSPENDED,
            "new_state": _STATUS_ACTIVE,
            "cause": CAUSE_ADMIN_REINSTATEMENT,
            "reason": reason,
            "period_start": period_start.isoformat() if period_start else None,
            "period_expiry": period_expiry.isoformat() if period_expiry else None,
            "period_moved": False,
            "settlement_written": False,
            "reinstated_at": instant.isoformat(),
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# ROW HELPERS - the same shapes settlement_service uses, for one reading of a row
# ══════════════════════════════════════════════════════════════════════════


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error."""
    if response is None:
        return []
    error = _get(response, "error")
    if error:
        raise _raise(MARKETPLACE_READ_FAILED, f"the persistence layer returned an error: {error}")
    data = _get(response, "data")
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    return []


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a mapping or as an attribute off an object. ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None``. Ids may be UUIDs or ints."""
    if value is None:
        return None
    return str(value)


def _normalise_status(value: Any) -> Optional[str]:
    """A ``library_subscriptions.status`` value as lowercase text, or ``None``."""
    if value is None:
        return None
    return str(value).strip().lower()


def _is_true(value: Any) -> bool:
    """Whether a stored boolean reads as true, tolerating the ``'true'`` text a driver may give."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "t", "1", "yes"}


def _coerce_instant(value: Optional[datetime]) -> datetime:
    """``value`` as a ``timezone.utc`` instant, defaulting to now. For timestamps, not periods."""
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_instant(value: Any) -> Optional[datetime]:
    """A stored ``TIMESTAMPTZ`` as a ``timezone.utc`` datetime, or ``None``.

    Accepts a ``datetime`` unchanged (normalising a naive one to UTC) and the ISO-8601 text
    PostgREST returns, including the ``Z`` suffix and a ``+00`` offset without minutes. An
    unparseable value is ``None``, which on this path means "no period" or "no settled_at" - both
    of which REFUSE the reinstatement, so a formatting surprise fails closed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S%z")
        except ValueError:
            logger.warning("could not read a stored timestamp on the reinstatement path")
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
