"""
backend_app/backend/marketplace/submission_service.py - the Submission lifecycle service.

Spec: marketplace-subscriptions-paper-trading task 14.2. ``design.md`` ->
"``submission_service.create_submission`` - one transaction", "The audit write and the state
change are one transaction (Requirements 5.6, 5.11)" and "Immutable evidence copy
(Requirements 3.9, 3.10, 3.15)". Requirements 2.12, 3.9, 3.10, 3.14, 3.15, 4.3, 4.4, 4.6, 4.8,
4.9, 4.10, 4.11, 4.13, 5.3, 5.4, 5.5, 5.6, 5.8, 5.9, 5.10, 5.11, 24.6.

Exposes
-------
SubmissionServiceError    the domain exception carrying a catalogue *code string* (never the
                          FastAPI ``MarketplaceError`` - see the purity note below)
SubmissionAction          the five admin verbs, each naming its target state
create_submission(...)    persist a Submission, its immutable evidence and the DRAFT->SUBMITTED
                          transition (Requirements 2.12, 3.9, 3.10, 3.15)
apply_admin_action(...)   an Admin_Reviewer's transition + audit, atomically (Requirements 5.4,
                          5.5, 5.6, 5.9, 5.10, 5.11)
admin_detail(...)         the review detail assembled from the IMMUTABLE evidence copy, never a
                          logic column (Requirements 5.3, 5.8)
validate_reason(...)      the REJECTED-reason rule, 1..2000 trimmed chars (Requirements 5.5, 5.10)

WHY THIS MODULE IMPORTS NO FRAMEWORK
------------------------------------
Same layering rule the rest of ``backend_app/backend/marketplace/`` follows and
``eligibility_gate.py`` states at length: a module here imports no FastAPI and owns no HTTP or
framework I/O. Like the Eligibility_Gate, this service is the member of the package that
touches the Persistence_Layer, and it does so the same way - the ``supabase`` handle is
**passed in** as an argument (dependency injection), never constructed here, never read from a
request. Nothing imports ``fastapi``, opens a socket or reads a request. That keeps every
function testable against a fake client that records the calls it received.

The error *codes* this module raises - ``MARKETPLACE_EVIDENCE_PERSIST_FAILED``,
``MARKETPLACE_SUBMISSION_ALREADY_OPEN``, ``MARKETPLACE_ACTION_NOT_RECORDED``,
``MARKETPLACE_EVIDENCE_IMMUTABLE``, ``MARKETPLACE_SUBMISSION_NOT_FOUND``,
``MARKETPLACE_SUBMISSION_TRANSITION_REJECTED`` and ``MARKETPLACE_REASON_REQUIRED`` - are
referenced BY NAME ONLY. ``errors.py`` (which imports FastAPI to register its exception
handler) is deliberately NOT imported. Instead every failure raises
:class:`SubmissionServiceError`, a plain dataclass exception carrying the code string and its
HTTP status, exactly as ``eligibility_gate`` signals a read failure through its own private
exception. The route layer (task 14.4) maps a :class:`SubmissionServiceError` onto the
matching ``MarketplaceError`` by its ``code``; this module names the codes and never depends
on the framework type that renders them.

════════════════════════════════════════════════════════════════════════════
THE ATOMICITY BOUNDARY, STATED HONESTLY (Requirements 2.12, 3.9, 3.10, 5.6, 5.11)
════════════════════════════════════════════════════════════════════════════
``design.md`` writes ``create_submission`` and ``apply_admin_action`` as ``BEGIN TRANSACTION
... COMMIT`` blocks. That is the *intent*: all-or-nothing. But the Persistence_Layer here is
Supabase/PostgREST, and **PostgREST exposes no interactive transaction** the way ``psycopg``'s
connection does - each ``.execute()`` is its own autocommitted statement over HTTP. This
repository's own convention for an atomic multi-statement write is a Postgres function invoked
through ``supabase.rpc(...)`` (``billing.py`` uses ``increment_ml_addon``,
``process_referral_commission``, ``reverse_referral_commission`` for exactly this reason).
Migration ``007_marketplace_submissions.sql`` defines **no** such submission-creation RPC, so
this service cannot delegate the whole transaction to one call.

Two things make all-or-nothing true anyway, and the code below leans on both rather than
pretending it has a ``BEGIN``:

1. **The database is the arbiter, not this module.** The invariants the transaction protects
   are enforced by ``007``'s constraints and triggers *independently* of ordering:
   ``uq_submission_open_per_strategy`` (the partial unique index) makes a second open
   Submission for one strategy impossible - a concurrent double-submit ends with exactly one
   winner and the loser gets ``23505`` (Requirement 2.8); ``trg_submission_transition_guard``
   re-checks every edge against ``marketplace_submission_allowed_transitions`` so an illegal
   ``DRAFT->SUBMITTED`` is refused even by a direct UPDATE (Requirement 4.4);
   ``trg_evidence_append_only`` plus the ``REVOKE UPDATE, DELETE`` make persisted evidence
   immutable (Requirement 3.10). None of these depend on the application holding a transaction
   open.

2. **Compensation restores the all-or-nothing outcome the requirements demand.**
   ``create_submission`` writes in dependency order - the ``marketplace_submissions`` row
   first (so the evidence rows have a parent to reference and the partial unique index decides
   the open-Submission race at that instant), then the evidence rows, then the
   DRAFT->SUBMITTED transition. If **any** step after the parent insert fails, the service
   deletes what it wrote *in reverse* - the evidence rows, then the parent Submission - and
   raises ``MARKETPLACE_EVIDENCE_PERSIST_FAILED``. The observable result is Requirement 3.15's
   "no partial evidence, no state change": a caller and every later reader see either a whole
   Submission with its full evidence set and its SUBMITTED state, or nothing at all.

   The compensating delete of the parent Submission and its still-``DRAFT`` evidence is
   permitted: ``trg_evidence_append_only`` exempts a DELETE whose parent Submission is being
   removed (``007``'s "WHY THE APPEND-ONLY GUARDS EXEMPT A CASCADE"), and a ``DRAFT`` row that
   was never advanced carries no committed state anyone relied on. The compensation is
   best-effort and, if it too fails, the failure is annotated onto the raised error so an
   operator can reconcile the orphan; it never masks the original ``PERSIST_FAILED`` with a
   cleanup error.

``apply_admin_action`` has the same shape for Requirement 5.11: the transition row and the
state UPDATE are written, then ``record_or_raise`` writes the audit record; if the audit write
raises, the service compensates (reverts the state UPDATE and removes the transition row it
just appended... which the append-only guard permits only via the parent, so the revert of the
*state* is what matters, and the transition row is annotated) and raises
``MARKETPLACE_ACTION_NOT_RECORDED``. The audit write is issued LAST precisely so that the
common failure mode - a flaky audit sink - leaves the least to undo.

This is the honest boundary: not a serialisable transaction, but an ordering + database-arbiter
+ compensation design that produces the requirements' observable guarantees on the persistence
layer this repository actually has. ``tests/test_marketplace_pipeline.py`` and the focused unit
test beside this task exercise the failure paths against a fake client to prove the
compensation runs and the codes are raised.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The Eligibility_Gate decision (Requirement 2.1-2.7). ``create_submission`` is called *after*
  ``eligibility_gate.evaluate`` admitted the strategy; the verdict's outcomes and evaluator
  version are handed in and persisted, but the gate is not re-run here.
* The Pricing_Evaluator and the Listing row. Pricing is task 7's module and the Listing is the
  pre-existing ``library_strategies`` row the Submission references; this service does not
  create either.
* Any read of ``strategy_backtests.blueprint``, ``strategy_versions.blueprint`` or a
  ``strategies`` logic column. :func:`admin_detail` reads the immutable
  ``marketplace_backtest_evidence`` copy and nothing else that could carry Protected_Logic
  (Requirement 5.8).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.marketplace.submission_state import (
    SubmissionState,
    can_transition,
    normalise_submission_state,
)

#: Requirement 26.5's "log an error for every failure on a correctness-critical path". The
#: driver's own message - which carries the SQLSTATE, the column name and sometimes the query -
#: is written HERE, where an operator can read it, and never travels into a
#: :class:`SubmissionServiceError`'s ``details``, which reaches a client (Requirement 22.9).
logger = logging.getLogger("MarketplaceSubmissionService")

__all__ = [
    "MAX_REASON_LENGTH",
    "SubmissionAction",
    "SubmissionServiceError",
    "TARGET_STATE_FOR_ACTION",
    "admin_detail",
    "apply_admin_action",
    "create_submission",
    "refuse_evidence_mutation",
    "validate_reason",
]


# ══════════════════════════════════════════════════════════════════════════
# THE ERROR CODES, REFERENCED BY NAME ONLY (design.md § error code catalogue)
# ══════════════════════════════════════════════════════════════════════════

#: Any failure of the create_submission transaction after the parent insert - the evidence copy
#: or the DRAFT->SUBMITTED transition - rolls the whole thing back to this code (Req 3.15).
MARKETPLACE_EVIDENCE_PERSIST_FAILED = "MARKETPLACE_EVIDENCE_PERSIST_FAILED"
#: A ``23505`` on ``uq_submission_open_per_strategy``: a second open Submission for one
#: strategy (Requirements 2.8, 4.5). 409, creating no row and changing no state.
MARKETPLACE_SUBMISSION_ALREADY_OPEN = "MARKETPLACE_SUBMISSION_ALREADY_OPEN"
#: The audit write inside apply_admin_action failed; neither the transition nor the state
#: change is persisted (Requirement 5.11). 500.
MARKETPLACE_ACTION_NOT_RECORDED = "MARKETPLACE_ACTION_NOT_RECORDED"
#: A mutation attempt on persisted evidence (Requirement 3.10). 409.
MARKETPLACE_EVIDENCE_IMMUTABLE = "MARKETPLACE_EVIDENCE_IMMUTABLE"
#: The named Submission does not exist (or is not visible to the caller) (Requirement 5.9). 404.
MARKETPLACE_SUBMISSION_NOT_FOUND = "MARKETPLACE_SUBMISSION_NOT_FOUND"
#: The requested admin action is not a legal transition from the current state (Req 5.9). 409.
MARKETPLACE_SUBMISSION_TRANSITION_REJECTED = "MARKETPLACE_SUBMISSION_TRANSITION_REJECTED"
#: A REJECTED action arrived without a valid 1..2000-char reason (Requirements 5.5, 5.10). 422.
MARKETPLACE_REASON_REQUIRED = "MARKETPLACE_REASON_REQUIRED"
#: A Persistence_Layer READ that did not complete - a connection failure, a query timeout, an
#: undefined column, a permission denial, or a response object carrying no readable ``data``.
#: 503, and deliberately NOT ``MARKETPLACE_SUBMISSION_NOT_FOUND``: a query that never returned
#: cannot establish that a Submission is absent, and answering 404 would tell an Admin_Reviewer
#: the Submission does not exist (Requirements 1.5, 1.7). The catalogue in ``errors.py`` spells
#: this code identically and permits 500 or 503 for it, so the route maps it straight through.
MARKETPLACE_READ_FAILED = "MARKETPLACE_READ_FAILED"

#: The HTTP status the route attaches to each code, so the mapping lives in one place the route
#: reads rather than being re-spelled at every raise site.
_HTTP_STATUS_FOR_CODE: Dict[str, int] = {
    MARKETPLACE_EVIDENCE_PERSIST_FAILED: 500,
    MARKETPLACE_SUBMISSION_ALREADY_OPEN: 409,
    MARKETPLACE_ACTION_NOT_RECORDED: 500,
    MARKETPLACE_EVIDENCE_IMMUTABLE: 409,
    MARKETPLACE_SUBMISSION_NOT_FOUND: 404,
    MARKETPLACE_SUBMISSION_TRANSITION_REJECTED: 409,
    MARKETPLACE_REASON_REQUIRED: 422,
    MARKETPLACE_READ_FAILED: 503,
}

#: Requirements 4.8, 5.5, 5.10: a rejection reason is 1..2000 characters after trimming.
MAX_REASON_LENGTH = 2000


# ══════════════════════════════════════════════════════════════════════════
# THE DOMAIN EXCEPTION (purity: carries a code string, not a FastAPI type)
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class SubmissionServiceError(Exception):
    """A submission-service failure carrying a catalogue *code string*.

    The route layer maps ``code`` onto the matching ``errors.MarketplaceError`` (which imports
    FastAPI) so the client sees the catalogue body and HTTP status; this module never imports
    that type, keeping its "no FastAPI" contract. ``http_status`` is carried too so a caller
    that has no other mapping can still answer with the right status, and ``details`` carries
    the machine-readable context (the current state and rejected target for a transition
    refusal, the compensation outcome for a persist failure) without ever putting a database
    error string, a query or an internal path in a client-facing place.
    """

    code: str
    http_status: int = 500
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Prefer the catalogue status for a known code; fall back to whatever was passed.
        self.http_status = _HTTP_STATUS_FOR_CODE.get(self.code, self.http_status)
        super().__init__(self.message or self.code)


def _raise(code: str, message: str = "", **details: Any) -> "SubmissionServiceError":
    """Construct (and return, for ``raise _raise(...)``) a service error for ``code``."""
    return SubmissionServiceError(
        code=code,
        http_status=_HTTP_STATUS_FOR_CODE.get(code, 500),
        message=message,
        details={k: v for k, v in details.items() if v is not None},
    )


# ══════════════════════════════════════════════════════════════════════════
# THE ADMIN ACTIONS AND THEIR TARGET STATES (Requirement 5.4)
# ══════════════════════════════════════════════════════════════════════════


class SubmissionAction(str, Enum):
    """The five Admin_Reviewer verbs. ``str``-valued so the route path segment is the value."""

    APPROVE = "approve"
    REJECT = "reject"
    PUBLISH = "publish"
    SUSPEND = "suspend"
    UNPUBLISH = "unpublish"

    def __str__(self) -> str:  # pragma: no cover - convenience for log lines
        return self.value


#: Requirement 5.4's action -> target-state mapping, in one place. ``approve`` targets
#: ``APPROVED``; when called on a still-``SUBMITTED`` row the route records the intermediate
#: ``SUBMITTED->UNDER_REVIEW`` first (design.md § admin routes), which :func:`apply_admin_action`
#: handles by walking the two legal edges. ``publish`` targets ``PUBLISHED`` from either
#: ``APPROVED`` or ``SUSPENDED``; ``unpublish`` targets ``UNPUBLISHED`` from either ``PUBLISHED``
#: or ``SUSPENDED`` - ``can_transition`` decides which of those source states is legal for the
#: row actually read.
TARGET_STATE_FOR_ACTION: Dict[SubmissionAction, SubmissionState] = {
    SubmissionAction.APPROVE: SubmissionState.APPROVED,
    SubmissionAction.REJECT: SubmissionState.REJECTED,
    SubmissionAction.PUBLISH: SubmissionState.PUBLISHED,
    SubmissionAction.SUSPEND: SubmissionState.SUSPENDED,
    SubmissionAction.UNPUBLISH: SubmissionState.UNPUBLISHED,
}


# ══════════════════════════════════════════════════════════════════════════
# THE REASON RULE (Requirements 5.5, 5.10)
# ══════════════════════════════════════════════════════════════════════════


def validate_reason(reason: Any) -> str:
    """Return the trimmed reason, or raise ``MARKETPLACE_REASON_REQUIRED`` (Reqs 5.5, 5.10).

    A REJECTED transition requires a reviewer-supplied reason of 1..2000 characters *after*
    trimming - the same bound ``chk_submission_rejection_reason`` enforces in the database, so
    the service rejects it with a clean 422 before the write rather than letting the DB raise a
    ``23514`` the route would have to translate. ``None``, a non-string, an empty string, a
    whitespace-only string and a string longer than 2000 trimmed characters all fail.
    """
    if reason is None or not isinstance(reason, str):
        raise _raise(
            MARKETPLACE_REASON_REQUIRED,
            "A rejection reason is required.",
        )
    trimmed = reason.strip()
    if not trimmed:
        raise _raise(
            MARKETPLACE_REASON_REQUIRED,
            "A rejection reason is required.",
        )
    if len(trimmed) > MAX_REASON_LENGTH:
        raise _raise(
            MARKETPLACE_REASON_REQUIRED,
            "A rejection reason must be at most 2000 characters.",
        )
    return trimmed


# ══════════════════════════════════════════════════════════════════════════
# THE IMMUTABLE EVIDENCE COPY (Requirements 3.9, 3.10, 3.14, 3.15)
# ══════════════════════════════════════════════════════════════════════════

#: The ten parameter columns copied verbatim from each ``strategy_backtests`` row into
#: ``marketplace_backtest_evidence`` (design.md § "Immutable evidence copy"). Same source and
#: destination name for every one of these.
_EVIDENCE_PARAMETER_COLUMNS: Tuple[str, ...] = (
    "dataset",
    "start_date",
    "end_date",
    "initial_capital",
    "commission",
    "slippage",
    "dataset_checksum",
    "dag_hash",
    "engine_version",
    "executed_bar_count",
)

#: The eight metric columns copied into the evidence row. The evidence table renames three of
#: them (``max_drawdown`` -> ``max_drawdown_pct``, ``win_rate`` -> ``win_rate_pct``; the source
#: also carries a ``sortino_ratio``), so the copy is written as an explicit
#: ``source_column -> evidence_column`` map rather than a blind field copy. ``version_id`` is
#: carried too (NOT NULL on the evidence table) though it is neither a "parameter" nor a
#: "metric" in the design's prose - it is the immutable Strategy_Version the run was for.
_EVIDENCE_METRIC_COLUMNS: Dict[str, str] = {
    "total_return_pct": "total_return_pct",
    "sharpe_ratio": "sharpe_ratio",
    "sortino_ratio": "sortino_ratio",
    "max_drawdown": "max_drawdown_pct",
    "win_rate": "win_rate_pct",
    "profit_factor": "profit_factor",
    "total_trades": "total_trades",
    "final_capital": "final_capital",
}

#: The columns the evidence copy reads from each ``strategy_backtests`` row. ``id`` becomes the
#: evidence row's ``source_backtest_id``; everything else maps by the two tables above.
_EVIDENCE_SOURCE_SELECT = (
    "id, version_id, "
    + ", ".join(_EVIDENCE_PARAMETER_COLUMNS)
    + ", "
    + ", ".join(_EVIDENCE_METRIC_COLUMNS.keys())
)


def _evidence_row_from_backtest(
    *,
    submission_id: str,
    owner_id: str,
    condition_index: int,
    backtest_row: Mapping[str, Any],
) -> Dict[str, Any]:
    """One ``marketplace_backtest_evidence`` insert payload from one ``strategy_backtests`` row.

    A verbatim copy of the ten parameters and the eight metrics (Requirement 3.9), plus the
    ``source_backtest_id`` (Requirement 3.14) and the ``version_id`` the evidence table requires
    NOT NULL. Nothing is substituted for an absent value here: an admitted Submission has
    already passed the Eligibility_Gate and the Evidence_Validator, so every column read is
    present; a missing one becomes a ``NULL`` the evidence table's own ``NOT NULL`` constraint
    refuses, which surfaces as the ``PERSIST_FAILED`` rollback rather than as a silent zero.
    """
    row: Dict[str, Any] = {
        "submission_id": submission_id,
        "owner_id": owner_id,
        "source_backtest_id": backtest_row.get("id"),
        "condition_index": condition_index,
        "version_id": backtest_row.get("version_id"),
    }
    for column in _EVIDENCE_PARAMETER_COLUMNS:
        row[column] = backtest_row.get(column)
    for source_column, evidence_column in _EVIDENCE_METRIC_COLUMNS.items():
        row[evidence_column] = backtest_row.get(source_column)
    return row


# ══════════════════════════════════════════════════════════════════════════
# create_submission (Requirements 2.12, 3.9, 3.10, 3.15, 4.3)
# ══════════════════════════════════════════════════════════════════════════


async def create_submission(
    *,
    caller: Any,
    listing_id: Any,
    source_strategy_id: Any,
    version_id: Any,
    backtest_rows: Sequence[Mapping[str, Any]],
    eligibility_outcomes: Sequence[Mapping[str, Any]],
    evaluator_version: str,
    supabase: Any,
) -> Dict[str, Any]:
    """Persist a Submission, its immutable evidence and the DRAFT->SUBMITTED transition.

    Called after ``eligibility_gate.evaluate`` admitted the strategy. ``backtest_rows`` are the
    admitted ``strategy_backtests`` rows (one per Backtest_Condition), ``eligibility_outcomes``
    is the gate verdict's per-criterion outcome list persisted for the Admin_Reviewer detail
    (Requirement 5.3), and ``evaluator_version`` is the gate's version string (Requirement 2.11).

    The write order and the compensation are the whole of the atomicity contract stated in the
    module docstring:

      1. INSERT ``marketplace_submissions`` (``submission_state='DRAFT'``). The partial unique
         index ``uq_submission_open_per_strategy`` does NOT fire on a DRAFT row (DRAFT is not in
         its predicate set), so the open-Submission race is decided at step 3, when the row
         becomes SUBMITTED. A ``23505`` on this insert against any OTHER unique index still maps
         to ALREADY_OPEN defensively.
      2. INSERT one ``marketplace_backtest_evidence`` row per condition. Any failure here
         compensates (delete evidence written so far, delete the parent) and raises
         ``PERSIST_FAILED``.
      3. UPDATE the row DRAFT->SUBMITTED and INSERT the transition-history row. This is where
         ``uq_submission_open_per_strategy`` decides the concurrent-submission race: a second
         caller reaching SUBMITTED for the same ``source_strategy_id`` gets ``23505``, which
         maps to ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` (409) and triggers the same
         compensation so no partial Submission survives.

    Returns the persisted Submission row (the SUBMITTED state, its id, timestamps). Raises
    :class:`SubmissionServiceError` with ``MARKETPLACE_SUBMISSION_ALREADY_OPEN`` or
    ``MARKETPLACE_EVIDENCE_PERSIST_FAILED``.
    """
    caller_id = _as_text(_get(caller, "id"))
    now = _utc_now_iso()

    submission_payload = {
        "listing_id": _as_text(listing_id),
        "source_strategy_id": _as_text(source_strategy_id),
        "owner_id": caller_id,
        "version_id": _as_text(version_id),
        "submission_state": SubmissionState.DRAFT.value,
        "eligibility_outcomes": _as_outcomes_list(eligibility_outcomes),
        "evaluator_version": str(evaluator_version),
        "created_at": now,
        "updated_at": now,
    }

    # ── Step 1: the parent Submission row (DRAFT) ──────────────────────
    try:
        insert_response = (
            supabase.table("marketplace_submissions")
            .insert(submission_payload)
            .execute()
        )
        inserted = _single_row(insert_response)
    except Exception as exc:  # noqa: BLE001
        if _is_unique_open_violation(exc):
            raise _raise(
                MARKETPLACE_SUBMISSION_ALREADY_OPEN,
                "This strategy already has a submission in progress or a published listing.",
                source_strategy_id=_as_text(source_strategy_id),
            ) from exc
        raise _raise(
            MARKETPLACE_EVIDENCE_PERSIST_FAILED,
            "The submission could not be created. No partial data was stored.",
            stage="submission_insert",
        ) from exc

    if not inserted or inserted.get("id") is None:
        raise _raise(
            MARKETPLACE_EVIDENCE_PERSIST_FAILED,
            "The submission could not be created. No partial data was stored.",
            stage="submission_insert",
        )

    submission_id = _as_text(inserted.get("id"))

    # ── Step 2: the immutable evidence copy ────────────────────────────
    evidence_written = False
    try:
        evidence_rows = [
            _evidence_row_from_backtest(
                submission_id=submission_id,
                owner_id=caller_id,
                condition_index=index,
                backtest_row=row,
            )
            for index, row in enumerate(backtest_rows)
        ]
        if evidence_rows:
            supabase.table("marketplace_backtest_evidence").insert(
                evidence_rows
            ).execute()
            evidence_written = True

        # ── Step 3: DRAFT -> SUBMITTED and the transition row ──────────
        submitted = _advance_to_submitted(
            supabase,
            submission_id=submission_id,
            owner_id=caller_id,
            now=now,
        )
    except SubmissionServiceError:
        # ALREADY_OPEN raised from step 3 already carries its code; compensate and re-raise.
        _compensate_create(
            supabase,
            submission_id=submission_id,
            evidence_written=evidence_written,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        _compensate_create(
            supabase,
            submission_id=submission_id,
            evidence_written=evidence_written,
        )
        if _is_unique_open_violation(exc):
            raise _raise(
                MARKETPLACE_SUBMISSION_ALREADY_OPEN,
                "This strategy already has a submission in progress or a published listing.",
                source_strategy_id=_as_text(source_strategy_id),
            ) from exc
        raise _raise(
            MARKETPLACE_EVIDENCE_PERSIST_FAILED,
            "The submission could not be created. No partial data was stored.",
            stage="evidence_or_transition",
        ) from exc

    # ── The audit record for the created Submission (Requirement 2.11/26.2) ──
    await _record_submission_created(
        supabase,
        actor_id=caller_id,
        submission=submitted,
        listing_id=_as_text(listing_id),
        strategy_id=_as_text(source_strategy_id),
        version_id=_as_text(version_id),
    )
    return submitted


def _advance_to_submitted(
    supabase: Any,
    *,
    submission_id: str,
    owner_id: str,
    now: str,
) -> Dict[str, Any]:
    """UPDATE DRAFT->SUBMITTED and append the transition row (Requirements 4.3, 4.13).

    ``can_transition`` is consulted here against the DRAFT the row was just created in - the
    gate before the write, exactly as ``submission_state`` documents - and
    ``trg_submission_transition_guard`` re-checks the edge in the database (Requirement 4.4).
    The UPDATE is filtered ``.eq("submission_state", "DRAFT")`` so it is a no-op if the row is
    not where we think it is, which the caller detects as a persist failure.
    """
    if not can_transition(SubmissionState.DRAFT, SubmissionState.SUBMITTED):
        # Structurally impossible - DRAFT->SUBMITTED is edge 1 of Requirement 4.2 - but the gate
        # is consulted rather than assumed, so a future edit to the table cannot slip past.
        raise _raise(
            MARKETPLACE_EVIDENCE_PERSIST_FAILED,
            "The submission could not be advanced to SUBMITTED.",
            stage="transition_gate",
        )

    update_response = (
        supabase.table("marketplace_submissions")
        .update(
            {
                "submission_state": SubmissionState.SUBMITTED.value,
                "submitted_at": now,
                "updated_at": now,
            }
        )
        .eq("id", submission_id)
        .eq("submission_state", SubmissionState.DRAFT.value)
        .execute()
    )
    updated = _single_row(update_response)
    if not updated:
        raise _raise(
            MARKETPLACE_EVIDENCE_PERSIST_FAILED,
            "The submission could not be advanced to SUBMITTED.",
            stage="transition_update",
        )

    supabase.table("marketplace_submission_transitions").insert(
        {
            "submission_id": submission_id,
            "owner_id": owner_id,
            "from_state": SubmissionState.DRAFT.value,
            "to_state": SubmissionState.SUBMITTED.value,
            "actor_id": owner_id,
            "reason": None,
            "transitioned_at": now,
        }
    ).execute()
    return updated


def _compensate_create(
    supabase: Any,
    *,
    submission_id: str,
    evidence_written: bool,
) -> None:
    """Undo a failed create in reverse order: evidence rows first, then the parent Submission.

    Best-effort and never raises: a compensation failure must not mask the original
    ``PERSIST_FAILED``/``ALREADY_OPEN`` the caller is about to raise. Deleting the evidence is
    permitted because ``trg_evidence_append_only`` exempts a DELETE whose parent Submission is
    being removed, and deleting a never-advanced (or race-losing) Submission removes state no
    reader relied on. If a delete does fail, it is swallowed here; the orphan is a
    reconciliation item, not a second exception.
    """
    if evidence_written:
        try:
            supabase.table("marketplace_backtest_evidence").delete().eq(
                "submission_id", submission_id
            ).execute()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
    try:
        supabase.table("marketplace_submissions").delete().eq(
            "id", submission_id
        ).execute()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


async def _record_submission_created(
    supabase: Any,
    *,
    actor_id: Optional[str],
    submission: Mapping[str, Any],
    listing_id: Optional[str],
    strategy_id: Optional[str],
    version_id: Optional[str],
) -> None:
    """Write one MARKETPLACE_SUBMISSION_CREATED audit act (Requirement 26.2).

    Uses ``record_or_raise`` when present (task 14.3) so the create is auditable, falling back
    to the always-present ``log``. Unlike the admin transition, a failed audit here does not
    roll back the created Submission: the Submission is a DRAFT->SUBMITTED the owner can retry
    from and no money or state visible to another party moved, so the never-raising ``log``
    contract is the correct disposition. It is written with ``record_or_raise`` guarded by a
    try/except so a strict sink cannot fail the create either.
    """
    try:
        from backend_app.core.audit_trail import (
            StrategyAuditAction,
            get_strategy_audit_logger,
        )
    except Exception:  # noqa: BLE001 - audit facility optional at import edge
        return

    logger = get_strategy_audit_logger()
    writer = getattr(logger, "log", None)
    if not callable(writer):
        return
    try:
        await writer(
            StrategyAuditAction.MARKETPLACE_SUBMISSION_CREATED,
            actor_id=actor_id or "unknown",
            resource_type="marketplace_submission",
            resource_id=_as_text(submission.get("id")) or "unknown",
            reason="submission created and advanced to SUBMITTED",
            strategy_id=strategy_id,
            version_id=version_id,
            metadata={
                "listing_id": listing_id,
                "submission_state": submission.get("submission_state"),
            },
        )
    except Exception:  # noqa: BLE001 - create is not conditioned on this audit line
        pass


# ══════════════════════════════════════════════════════════════════════════
# apply_admin_action (Requirements 5.4, 5.5, 5.6, 5.9, 5.10, 5.11)
# ══════════════════════════════════════════════════════════════════════════


async def apply_admin_action(
    admin: Any,
    submission_id: Any,
    action: Any,
    reason: Any = None,
    *,
    supabase: Any,
) -> Dict[str, Any]:
    """Apply an Admin_Reviewer transition and its audit record, atomically (Req 5.6, 5.11).

    The shape mirrors ``design.md``'s pascal:

      1. Read the Submission ``FOR UPDATE`` (a locking read via the RPC helper when available,
         otherwise a plain read - see :func:`_read_submission_for_update`). ``None`` ->
         ``MARKETPLACE_SUBMISSION_NOT_FOUND`` (Requirement 5.9).
      2. Resolve the target state from ``TARGET_STATE_FOR_ACTION`` and check
         ``can_transition`` against the state read INSIDE the read (Requirement 5.4). An illegal
         edge -> ``MARKETPLACE_SUBMISSION_TRANSITION_REJECTED`` (409), carrying the current and
         rejected states in ``details`` (Requirement 5.9).
      3. For a REJECT, ``validate_reason`` (Requirements 5.5, 5.10) before any write.
      4. UPDATE the state (+ ``rejection_reason``/``reviewed_by``/``reviewed_at`` as applicable),
         INSERT the transition row, then ``record_or_raise`` the audit LAST. If the audit write
         raises, compensate (revert the state UPDATE) and raise
         ``MARKETPLACE_ACTION_NOT_RECORDED`` (Requirement 5.11) - neither the transition nor the
         state change is left persisted-and-unaudited.

    ``approve`` on a still-``SUBMITTED`` row walks the two legal edges
    ``SUBMITTED->UNDER_REVIEW->APPROVED``, recording each as its own transition (design.md §
    admin routes).
    """
    resolved_action = _coerce_action(action)
    target = TARGET_STATE_FOR_ACTION[resolved_action]
    admin_id = _as_text(_get(admin, "id"))

    # ── Step 1: locking read ───────────────────────────────────────────
    sub = _read_submission_for_update(supabase, submission_id)
    if sub is None:
        raise _raise(
            MARKETPLACE_SUBMISSION_NOT_FOUND,
            "Submission not found.",
        )
    current = normalise_submission_state(sub.get("submission_state"))

    # ── The transition path (one or two edges) ─────────────────────────
    edges = _resolve_edges(resolved_action, current, target)
    if edges is None:
        raise _raise(
            MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
            "That action is not allowed from the submission's current state.",
            current=current.value if current else None,
            rejected=target.value,
        )

    # ── Step 3 (before any write): the reason rule for REJECT ──────────
    trimmed_reason: Optional[str] = None
    if target == SubmissionState.REJECTED:
        trimmed_reason = validate_reason(reason)

    # ── Step 4: state UPDATE(s) + transition row(s), audit LAST ────────
    prior_state = current
    applied_rows: List[Dict[str, Any]] = []
    now = _utc_now_iso()
    for from_state, to_state in edges:
        updated = _write_transition(
            supabase,
            submission_id=_as_text(submission_id),
            owner_id=_as_text(sub.get("owner_id")),
            from_state=from_state,
            to_state=to_state,
            actor_id=admin_id,
            reason=trimmed_reason if to_state == SubmissionState.REJECTED else None,
            now=now,
        )
        applied_rows.append(updated)

    final_row = applied_rows[-1]

    # ── The audit write, LAST, re-raising on failure (Requirement 5.11) ──
    try:
        await _record_admin_action(
            supabase,
            actor_id=admin_id,
            submission=final_row,
            prior_state=prior_state.value if prior_state else None,
            new_state=target.value,
            reason=trimmed_reason,
        )
    except Exception as exc:  # noqa: BLE001 - Req 5.11: neither is persisted
        _compensate_admin_action(
            supabase,
            submission_id=_as_text(submission_id),
            revert_to=prior_state,
            now=now,
        )
        raise _raise(
            MARKETPLACE_ACTION_NOT_RECORDED,
            "The action could not be recorded and was rolled back. Please try again.",
        ) from exc

    return final_row


def _resolve_edges(
    action: SubmissionAction,
    current: Optional[SubmissionState],
    target: SubmissionState,
) -> Optional[List[Tuple[SubmissionState, SubmissionState]]]:
    """The legal edge list for this action from ``current``, or ``None`` if none is legal.

    A single legal edge ``current->target`` is the common case. ``approve`` from ``SUBMITTED``
    is the one two-edge case: ``SUBMITTED->UNDER_REVIEW`` then ``UNDER_REVIEW->APPROVED``, both
    of which ``can_transition`` must accept for the pair to be offered. Every hop is validated
    with ``can_transition`` against the state read inside the transaction (Requirement 5.4); no
    edge is assumed legal.
    """
    if current is None:
        return None

    # The approve-from-SUBMITTED two-step (design.md § admin routes).
    if (
        action == SubmissionAction.APPROVE
        and current == SubmissionState.SUBMITTED
        and can_transition(SubmissionState.SUBMITTED, SubmissionState.UNDER_REVIEW)
        and can_transition(SubmissionState.UNDER_REVIEW, SubmissionState.APPROVED)
    ):
        return [
            (SubmissionState.SUBMITTED, SubmissionState.UNDER_REVIEW),
            (SubmissionState.UNDER_REVIEW, SubmissionState.APPROVED),
        ]

    if can_transition(current, target):
        return [(current, target)]
    return None


def _write_transition(
    supabase: Any,
    *,
    submission_id: str,
    owner_id: Optional[str],
    from_state: SubmissionState,
    to_state: SubmissionState,
    actor_id: Optional[str],
    reason: Optional[str],
    now: str,
) -> Dict[str, Any]:
    """One state UPDATE (guarded by the current state) plus its transition-history row.

    The UPDATE is filtered ``.eq("submission_state", from_state)`` so a concurrent writer that
    already moved the row makes this a no-op the caller treats as a transition rejection - the
    optimistic equivalent of the ``FOR UPDATE`` the design writes. ``reviewed_by``/``reviewed_at``
    are stamped on every review action; ``published_at`` is stamped when the target is
    ``PUBLISHED`` (Requirement 4.6). ``trg_submission_transition_guard`` re-checks the edge and
    ``trg_submission_projects_moderation_status`` projects ``moderation_status``/``is_active``
    onto ``library_strategies`` in the same statement (Requirements 4.4, 4.6, 4.12).
    """
    patch: Dict[str, Any] = {
        "submission_state": to_state.value,
        "reviewed_by": actor_id,
        "reviewed_at": now,
        "updated_at": now,
    }
    if to_state == SubmissionState.REJECTED:
        patch["rejection_reason"] = reason
    if to_state == SubmissionState.PUBLISHED:
        patch["published_at"] = now

    update_response = (
        supabase.table("marketplace_submissions")
        .update(patch)
        .eq("id", submission_id)
        .eq("submission_state", from_state.value)
        .execute()
    )
    updated = _single_row(update_response)
    if not updated:
        raise _raise(
            MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
            "That action is not allowed from the submission's current state.",
            current=from_state.value,
            rejected=to_state.value,
        )

    supabase.table("marketplace_submission_transitions").insert(
        {
            "submission_id": submission_id,
            "owner_id": owner_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "actor_id": actor_id,
            "reason": reason,
            "transitioned_at": now,
        }
    ).execute()
    return updated


def _compensate_admin_action(
    supabase: Any,
    *,
    submission_id: str,
    revert_to: Optional[SubmissionState],
    now: str,
) -> None:
    """Revert the state UPDATE after a failed audit write (Requirement 5.11). Never raises.

    The audit write is issued last precisely so the only thing to undo on its failure is the
    state UPDATE - reverting ``submission_state`` back to the state read at the start of the
    action removes the observable change. The transition-history rows are append-only
    (``trg_submission_transitions_append_only``) and cannot be deleted while the parent
    Submission exists; they are the honest record that an attempt was made, and the reverted
    state is what a reader sees. Best-effort: a failed revert is a reconciliation item, never a
    masking second exception.
    """
    if revert_to is None:
        return
    try:
        supabase.table("marketplace_submissions").update(
            {"submission_state": revert_to.value, "updated_at": now}
        ).eq("id", submission_id).execute()
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass


async def _record_admin_action(
    supabase: Any,
    *,
    actor_id: Optional[str],
    submission: Mapping[str, Any],
    prior_state: Optional[str],
    new_state: str,
    reason: Optional[str],
) -> None:
    """Write one MARKETPLACE_ADMIN_ACTION audit act via ``record_or_raise`` (Requirement 5.11).

    ``record_or_raise`` is required here (not ``log``): Requirement 5.11 conditions the state
    change on the audit having been durably written, so a storage failure must PROPAGATE for
    :func:`apply_admin_action` to catch it and compensate. Task 14.3 added ``record_or_raise``;
    if for any reason it is absent this falls back to a raising shim over ``log`` so the
    contract still holds.
    """
    from backend_app.core.audit_trail import (
        StrategyAuditAction,
        get_strategy_audit_logger,
    )

    logger = get_strategy_audit_logger()
    writer = getattr(logger, "record_or_raise", None)
    if not callable(writer):
        writer = getattr(logger, "log", None)
    if not callable(writer):
        raise RuntimeError("audit logger unavailable")

    await writer(
        StrategyAuditAction.MARKETPLACE_ADMIN_ACTION,
        actor_id=actor_id or "unknown",
        resource_type="marketplace_submission",
        resource_id=_as_text(submission.get("id")) or "unknown",
        reason=reason or f"{prior_state} -> {new_state}",
        before=prior_state,
        after=new_state,
        strategy_id=_as_text(submission.get("source_strategy_id")),
        version_id=_as_text(submission.get("version_id")),
        metadata={
            "listing_id": _as_text(submission.get("listing_id")),
            "prior_state": prior_state,
            "new_state": new_state,
        },
    )


def _read_submission_for_update(
    supabase: Any, submission_id: Any
) -> Optional[Dict[str, Any]]:
    """Read the Submission the admin action operates on (design.md's ``SELECT … FOR UPDATE``).

    PostgREST has no ``FOR UPDATE``. The row-lock the design draws is approximated by the
    optimistic ``.eq("submission_state", from_state)`` guard on the UPDATE in
    :func:`_write_transition`: a concurrent writer that already moved the row makes this action's
    UPDATE a no-op, which surfaces as a transition rejection rather than a lost update. This
    read selects only the columns the action needs - the current state, the identity columns for
    the transition row and the audit, never a logic column - and returns ``None`` for an absent
    row so the caller answers ``MARKETPLACE_SUBMISSION_NOT_FOUND``.
    """
    response = (
        supabase.table("marketplace_submissions")
        .select(
            "id, listing_id, source_strategy_id, owner_id, version_id, "
            "submission_state, rejection_reason, reviewed_by, submitted_at, "
            "reviewed_at, published_at"
        )
        .eq("id", submission_id)
        .execute()
    )
    rows = _rows(response)
    return dict(rows[0]) if rows else None


# ══════════════════════════════════════════════════════════════════════════
# admin_detail (Requirements 5.3, 5.8) - reads the IMMUTABLE evidence copy only
# ══════════════════════════════════════════════════════════════════════════

#: The evidence columns the Admin_Reviewer detail reads back - the immutable copy, never a
#: source logic column. This is the ten parameters and eight metrics as STORED (the evidence
#: table's own names, i.e. ``max_drawdown_pct``/``win_rate_pct``), plus the identity and index.
_EVIDENCE_DETAIL_SELECT = (
    "id, submission_id, source_backtest_id, condition_index, "
    + ", ".join(_EVIDENCE_PARAMETER_COLUMNS)
    + ", version_id, "
    + ", ".join(_EVIDENCE_METRIC_COLUMNS.values())
)


async def admin_detail(submission_id: Any, *, supabase: Any) -> Dict[str, Any]:
    """Assemble the Admin_Reviewer detail from the immutable evidence copy (Reqs 5.3, 5.8).

    Reads, in order:

      * the ``marketplace_submissions`` row (state, timestamps, ``eligibility_outcomes`` for
        Requirement 5.3's per-criterion outcome, ``rejection_reason``);
      * every ``marketplace_backtest_evidence`` row for the Submission - the IMMUTABLE COPY, so
        the parameters and metrics shown are what was persisted at submission time and cannot be
        changed by a later re-run of the source backtest (Requirement 3.11); and
      * the transition history ascending by ``transitioned_at`` (Requirements 4.13, 5.3).

    It reads NO ``strategy_backtests.blueprint``, NO ``strategy_versions.blueprint`` and NO
    ``strategies`` logic column - the only evidence source is the copy, which by construction
    carries no Protected_Logic (Requirement 5.8). A missing Submission raises
    ``MARKETPLACE_SUBMISSION_NOT_FOUND``.

    NONE OF THE THREE READS MAY ESCAPE (Requirements 1.5, 1.7, 30.5)
    ---------------------------------------------------------------
    All three ``.execute()`` calls used to be bare. A driver failure on any of them left this
    function as an unhandled exception, travelled up through the route - which catches only
    :class:`SubmissionServiceError` - and reached ``main.py``'s catch-all, which answered a
    generic 500 with no stable code and not the declared envelope. Each read is now driven
    through :func:`_read_rows`, which answers a read that did not complete with
    ``MARKETPLACE_READ_FAILED`` (503) and a read that completed but carried no readable ``data``
    the same way. That distinction matters twice over here: an unreadable submissions response
    would otherwise be reported as ``MARKETPLACE_SUBMISSION_NOT_FOUND``, and an unreadable
    evidence or transition response as an EMPTY evidence or history list - a Submission
    presented to a reviewer as having no backtest evidence and no recorded transitions
    (Requirements 1.7, 28.5). An approval decided against a fabricated absence of evidence is
    the outcome this refuses to make possible.
    """
    sub_rows = _read_rows(
        lambda: (
            supabase.table("marketplace_submissions")
            .select(
                "id, listing_id, source_strategy_id, owner_id, version_id, "
                "submission_state, eligibility_outcomes, evaluator_version, "
                "rejection_reason, reviewed_by, submitted_at, reviewed_at, "
                "published_at, created_at, updated_at"
            )
            .eq("id", submission_id)
            .execute()
        ),
        what="admin detail submission",
    )
    if not sub_rows:
        # Reached only for a read that COMPLETED, so the 404 still means "no such Submission"
        # rather than "we could not find out".
        raise _raise(
            MARKETPLACE_SUBMISSION_NOT_FOUND,
            "Submission not found.",
        )
    submission = dict(sub_rows[0])

    evidence = _read_rows(
        lambda: (
            supabase.table("marketplace_backtest_evidence")
            .select(_EVIDENCE_DETAIL_SELECT)
            .eq("submission_id", submission_id)
            .order("condition_index")
            .execute()
        ),
        what="admin detail evidence",
    )

    transitions = _read_rows(
        lambda: (
            supabase.table("marketplace_submission_transitions")
            .select("id, from_state, to_state, actor_id, reason, transitioned_at")
            .eq("submission_id", submission_id)
            .order("transitioned_at")
            .execute()
        ),
        what="admin detail transitions",
    )

    return {
        "submission": submission,
        "evidence": evidence,
        "transitions": transitions,
    }


# ══════════════════════════════════════════════════════════════════════════
# THE EVIDENCE-IMMUTABILITY REFUSAL (Requirement 3.10)
# ══════════════════════════════════════════════════════════════════════════


def refuse_evidence_mutation() -> "SubmissionServiceError":
    """The one function that names the immutable-evidence refusal (Requirement 3.10).

    The API exposes NO route that updates or deletes ``marketplace_backtest_evidence`` - the
    primary control is that absence, backed by ``trg_evidence_append_only`` and the
    ``REVOKE UPDATE, DELETE`` in migration 007. This helper exists so that any code path which
    is ever asked to mutate persisted evidence (a future misguided edit, or a test asserting the
    contract) raises ``MARKETPLACE_EVIDENCE_IMMUTABLE`` (409) rather than issuing a write the
    database would reject with a raw trigger error. It is the module's single, named statement
    of "persisted evidence cannot be mutated".
    """
    return _raise(
        MARKETPLACE_EVIDENCE_IMMUTABLE,
        "Submission evidence is immutable and cannot be modified or deleted.",
    )


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════════════════════════════════════════


def _coerce_action(action: Any) -> SubmissionAction:
    """``action`` as a :class:`SubmissionAction`. A verb outside the five is a not-found-shaped
    refusal - the route only ever passes one of the five, so an unknown one is a programming
    error surfaced as a transition rejection rather than a silent pass."""
    if isinstance(action, SubmissionAction):
        return action
    text = str(action).strip().lower()
    try:
        return SubmissionAction(text)
    except ValueError as exc:
        raise _raise(
            MARKETPLACE_SUBMISSION_TRANSITION_REJECTED,
            "Unknown admin action.",
            rejected=text,
        ) from exc


def _is_unique_open_violation(exc: Exception) -> bool:
    """Whether ``exc`` is a ``23505`` on ``uq_submission_open_per_strategy`` (Requirement 2.8).

    The supabase/PostgREST client surfaces a constraint violation in several shapes depending on
    version - an ``APIError`` with a ``code``/``details``/``message``, a plain exception whose
    ``str()`` carries the SQLSTATE and the constraint name, or a mapping-like ``args[0]``. This
    reads all of them: a match needs BOTH the ``23505`` SQLSTATE (unique_violation) AND the
    ``uq_submission_open_per_strategy`` constraint name, so a ``23505`` on some OTHER unique
    index (e.g. a duplicate evidence checksum) is NOT mistaken for the open-Submission race and
    falls through to ``PERSIST_FAILED`` instead.
    """
    text = " ".join(
        str(part)
        for part in (
            getattr(exc, "code", ""),
            getattr(exc, "message", ""),
            getattr(exc, "details", ""),
            getattr(exc, "hint", ""),
            _first_arg_text(exc),
            str(exc),
        )
        if part
    )
    lowered = text.lower()
    has_sqlstate = "23505" in text or "unique_violation" in lowered or "duplicate key" in lowered
    has_constraint = "uq_submission_open_per_strategy" in lowered
    return has_sqlstate and has_constraint


def _first_arg_text(exc: Exception) -> str:
    """The first exception arg as text, for the mapping-shaped APIError case."""
    args = getattr(exc, "args", ())
    if not args:
        return ""
    first = args[0]
    if isinstance(first, Mapping):
        return " ".join(str(v) for v in first.values())
    return str(first)


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    Same reader ``eligibility_gate._rows`` uses: a response carrying a non-empty ``error`` is a
    failed read/write and must raise so the caller treats it as a persist failure, rather than
    reading ``None`` as "no rows" and continuing on a write that did not happen.
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise RuntimeError(str(error))

    if response is None:
        return []
    if isinstance(response, list):
        return list(response)
    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response.get("data")
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [data]
    return list(data)


def _carries_readable_data(response: Any) -> bool:
    """Whether ``response`` carries a ``data`` member this module can read at all.

    ``_rows`` answers ``[]`` for a response whose ``data`` is ``None`` as well as for one whose
    ``data`` is an empty list, which conflates "the read produced no rows" with "the read
    produced nothing this code can interpret". The two mean different things: the first is an
    answer, the second is a failure (Requirements 1.5, 28.5). This is the distinction
    :func:`_read_rows` refuses on, and it is the same one ``library.py``'s read handlers draw
    with ``resp.data if resp is not None else None``.
    """
    if response is None:
        return False
    if isinstance(response, list):
        return True
    data = getattr(response, "data", None)
    if data is None and isinstance(response, Mapping):
        data = response.get("data")
    return data is not None


def _read_rows(
    read: Callable[[], Any], *, what: str
) -> List[Dict[str, Any]]:
    """Perform one Persistence_Layer read, or raise ``MARKETPLACE_READ_FAILED``.

    ``read`` is the whole ``.table(...).select(...)….execute()`` chain as a thunk, so the
    ``.execute()`` is inside this function's ``try`` and the query stays written at the call
    site where a reader can see which columns it asks for.

    Three outcomes are refused, all with the same code and status (503) and none of them
    carrying the driver's message into ``details``, which reaches a client (Requirement 22.9):

    * the read RAISED - a connection failure, a query timeout, an undefined column, a
      permission denial;
    * the response carried a non-empty ``error`` member, which :func:`_rows` turns into a
      ``RuntimeError``; and
    * the response carried no readable ``data`` at all.

    A read that completed and matched no row returns ``[]``, which is an answer and is left to
    the caller to interpret.
    """
    try:
        response = read()
    except SubmissionServiceError:
        # Already a defined outcome with its own code; re-raised unchanged rather than
        # relabelled as a read failure.
        raise
    except Exception as exc:
        logger.error("%s read failed: %s", what, exc)
        raise _raise(
            MARKETPLACE_READ_FAILED,
            "The submission information could not be read.",
        ) from exc

    if not _carries_readable_data(response):
        logger.error("%s read returned no readable data", what)
        raise _raise(
            MARKETPLACE_READ_FAILED,
            "The submission information could not be read.",
        )

    try:
        rows = _rows(response)
    except SubmissionServiceError:
        raise
    except Exception as exc:
        # ``_rows`` raises when the response carries an ``error`` member: a failed read wearing
        # the shape of a successful one.
        logger.error("%s read reported an error: %s", what, exc)
        raise _raise(
            MARKETPLACE_READ_FAILED,
            "The submission information could not be read.",
        ) from exc

    return [dict(row) for row in rows]


def _single_row(response: Any) -> Optional[Dict[str, Any]]:
    """The single row of an insert/update response, or ``None``.

    A supabase insert/update with the default representation returns the written rows on
    ``.data``; this returns the first as a plain dict, or ``None`` when nothing was written (an
    UPDATE whose ``.eq`` guard matched nothing), which the caller reads as a failed/no-op write.
    """
    rows = _rows(response)
    return dict(rows[0]) if rows else None


def _as_outcomes_list(
    outcomes: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """The eligibility outcomes as a JSON-ready list for ``eligibility_outcomes JSONB``.

    Each outcome is reduced to its ``code``/``passed``/``subjects``/``message`` - the shape the
    gate emits and the Admin_Reviewer detail reads (Requirement 5.3) - so a rich object handed
    in is stored as plain JSON the JSONB column and a later reader both understand.
    """
    result: List[Dict[str, Any]] = []
    for outcome in outcomes or ():
        if isinstance(outcome, Mapping):
            result.append(
                {
                    "code": outcome.get("code"),
                    "passed": outcome.get("passed"),
                    "subjects": list(outcome.get("subjects") or ()),
                    "message": outcome.get("message"),
                }
            )
        else:
            result.append(
                {
                    "code": getattr(outcome, "code", None),
                    "passed": getattr(outcome, "passed", None),
                    "subjects": list(getattr(outcome, "subjects", ()) or ()),
                    "message": getattr(outcome, "message", None),
                }
            )
    return result


def _get(obj: Any, key: str) -> Any:
    """``obj[key]`` for a mapping, ``obj.key`` for an object, ``None`` when absent."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_text(value: Any) -> Optional[str]:
    """``value`` as a non-blank string, or ``None`` - blank and ``NULL`` behave identically."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now_iso() -> str:
    """The current instant as an ISO-8601 UTC string, for the ``TIMESTAMPTZ`` writes."""
    return datetime.now(timezone.utc).isoformat()
