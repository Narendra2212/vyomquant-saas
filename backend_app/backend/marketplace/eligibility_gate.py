"""
backend_app/backend/marketplace/eligibility_gate.py - the publication Eligibility_Gate.

Spec: marketplace-subscriptions-paper-trading task 14.1. ``design.md`` ->
"``marketplace/eligibility_gate.py``". Requirements 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7,
2.9, 2.10, 2.11, 2.13.

Exposes
-------
CriterionOutcome         one criterion's verdict: code, passed, message, subjects
                         (re-exported from ``evidence_validator`` so the whole verdict is one
                         value type, whether the criterion is a gate criterion or an
                         evidence-distinctness one)
EligibilityVerdict       the result of :func:`evaluate`: admitted, outcomes, version_id,
                         unevaluable, evaluator_version
EVALUATOR_VERSION        the string persisted beside every evaluation (Requirement 2.11)
MP_CODES / MP_PUBLIC_MESSAGES   the gate's own criterion codes and their owner-facing
                         sentences (Requirement 2.10)
evaluate(caller, strategy_id, backtest_ids, supabase)   the gate itself (Requirement 2.1)

WHY THIS MODULE PERFORMS READS BUT IMPORTS NO FRAMEWORK
-------------------------------------------------------
The layering rule ``design.md`` states for this package - the one ``money.py``,
``submission_state.py``, ``evidence_validator.py``, ``pricing_evaluator.py`` and
``subscription_period.py`` already follow - is that a module here imports no FastAPI and owns
no HTTP or framework I/O. The Eligibility_Gate is the one member of the package that reads the
Persistence_Layer, and it does so without breaking that rule: the ``supabase`` handle is
**passed in** as an argument, exactly as ``design.md`` -> "Dependency injection" prescribes.
Nothing here imports ``fastapi``, constructs a client, reads a request, opens a socket or
reads the clock for anything but the audit timestamp. That keeps :func:`evaluate` testable
against a fake client that records the calls it received, and keeps the four-round-trip
contract (below) checkable by counting those calls.

The ``STRATEGY_SHARING`` / marketplace-publish entitlement check is deliberately **not** here.
It is the existing ``Depends(require_marketplace_publish)`` on the route (Requirement 2.9), so
a caller lacking it is refused with 403 by the framework *before* this function runs and
before any read is issued. Putting it here would both duplicate an existing control and issue
reads for a caller who must never reach them.

THE FIVE OWNER-SCOPED ROUND TRIPS (Requirements 2.2 - 2.7, and MKT-07)
----------------------------------------------------------------------
:func:`evaluate` issues exactly five reads, in this order, each scoped to the owner:

1. ``strategies``          - ``id, user_id, archived_at`` for ``strategy_id``, filtered
   ``.eq("user_id", caller.id)``. The row's existence and its owner are Criterion 2.2's two
   clauses, and its ``user_id`` is ALSO its tenant: this platform's tenancy model is
   tenant == user, so there is no third column to read. ``strategies`` has no ``tenant_id``
   column - the spec's ``tenant-boundary-audit.md`` records the table's tenant predicate as
   ``user_id``, and ``core/dependencies.py`` defaults a caller's ``tenant_id`` to their own
   ``user_id``. Selecting ``tenant_id`` here is what made every evaluation in production
   answer ``42703`` and therefore ``unevaluable`` (production-launch-hardening task 13.18).
2. ``strategy_versions``   - the versions of ``strategy_id``, filtered
   ``.eq("strategy_id", strategy_id)``, so ``MP_VERSION_EXISTS`` (a saved, non-draft version)
   and ``MP_VERSION_VALID`` (that version's canonical graph passes the Strategy_Builder's
   structural validation) can be decided (Criterion 2.3).
3. ``strategy_backtests``  - the twenty evidence columns for the referenced runs, filtered
   ``.in_("id", backtest_ids)`` **and** ``.eq("user_id", caller.id)``. The owner scope is what
   makes a foreign-owned reference indistinguishable from an absent one: a row belonging to
   another user simply is not returned, so ``EV_OWNERSHIP`` and ``MP_METRICS_COMPLETE`` treat
   it as missing rather than leaking that it exists (Requirements 2.4, 2.5, 2.6; 3.13).
4. the open-state probe over ``marketplace_submissions`` + ``library_strategies`` - is there
   already an open Submission for this strategy, or a ``PUBLISHED`` Listing for this
   ``source_strategy_id`` (Criterion 2.7)? One round trip: the two reads are issued together
   and a failure of either fails the probe.
5. ``execution_records`` - the strategy's **live** fills, filtered ``.eq("strategy_id", …)``
   and ``.eq("tenant_id", caller.id)``, so ``MP_LIVE_HISTORY`` can be decided (MKT-07; the
   owner brief's Task 15 D requirement 2, "at least 24 hours of verifiable live algorithmic
   trading history"). See :func:`_read_live_executions` for why this ledger and no other.

A failure of **any** of the five reads short-circuits the whole evaluation to
``unevaluable = True`` with no admit decision and the single code
``MARKETPLACE_ELIGIBILITY_UNEVALUABLE`` (Requirement 2.13). That outcome is distinct by code
from a criteria failure (``MARKETPLACE_ELIGIBILITY_FAILED``): a read that did not complete
means eligibility is *unknown*, which is a 503, not a 422 verdict of "not eligible". The
route maps the two to their catalogue statuses; this module only records which happened.

WHY NOTHING IS DERIVED FROM ``strategies.backtest_result``
----------------------------------------------------------
The strategy row's legacy ``backtest_result`` blob is never read and never consulted. Every
figure the gate needs - completion status, error text, the seven performance metrics, the bar
count, the window - comes from the ``strategy_backtests`` rows of read 3, which are the
Backtest_Evidence the owner explicitly referenced. Requirement 2.1 forbids trusting a value in
the request body; Requirement 2.5 and 2.6 name ``strategy_backtests`` columns specifically;
and admitting a strategy "on the strength of one ``strategies.backtest_result`` blob" is
exactly the defect task 14.10 removes from the old ``publish_strategy``. So this module derives
nothing from it, and the ``strategies`` read (read 1) deliberately does not even select it.

WHY EVERY CRITERION IS EVALUATED AND NONE SHORT-CIRCUITS
--------------------------------------------------------
Requirement 2.10 obliges the API to return, in one response, "every criterion of this
requirement that failed rather than only the first failure". So once the four reads have
completed, :func:`evaluate` appends an outcome for **every** criterion unconditionally - the
gate's own ``MP_*`` criteria, the ten ``EV_*`` criteria of
:func:`evidence_validator.validate`, ``MP_METRICS_COMPLETE`` and ``MP_SUBMISSION_OPEN`` - with
the ``passed`` flag carrying each verdict. Control flow never stops early on a failure; the
only early exit is the read failure above, which is a different thing (evaluation could not
happen) with a different code. ``admitted`` is then "every outcome passed", computed once at
the end.

The seven-metric completeness check (``MP_METRICS_COMPLETE``) lives here rather than in
``evidence_validator`` because it is an *eligibility* criterion of Requirement 2.6, evaluated
against the same rows the validator sees but answering a different question ("are the displayed
figures all present and finite") than the validator's distinctness rules. ``design.md`` keeps
the two apart for that reason, and the gate is the one place that reads both.

WHY THE AUDIT WRITE HAPPENS ON EVERY OUTCOME
--------------------------------------------
Requirement 2.11 requires *every* evaluation - admitted or rejected - recorded in the
Audit_Log with the strategy id, the evaluated Strategy_Version id, the acting identity, the
per-criterion outcomes, the evaluating server version and a timestamp. So :func:`evaluate`
writes one ``StrategyAuditAction.MARKETPLACE_ELIGIBILITY_EVALUATED`` record before it returns,
on the admitted path and on both rejection paths (criteria failure and unevaluable), carrying
all six facts. It uses the *existing* ``StrategyAuditLogger`` (task 14.3 adds
``record_or_raise`` and the remaining ``MARKETPLACE_*`` members; this module is written to use
``record_or_raise`` when it exists and to fall back to the always-present ``log`` until then -
see :func:`_write_audit`), so no second audit facility is introduced (Requirement 2.11,
``design.md`` -> "Audit records").

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The ``Submission`` insert, the evidence copy and the ``DRAFT -> SUBMITTED`` transition
  (Requirement 2.12). Those are ``submission_service.create_submission``'s transaction; the
  gate decides, the service persists.
* ``MarketplaceError`` and the HTTP status mapping. A :class:`EligibilityVerdict` becomes a
  ``MARKETPLACE_ELIGIBILITY_FAILED`` (422) or a ``MARKETPLACE_ELIGIBILITY_UNEVALUABLE`` (503)
  at the route, not here; importing ``errors.py`` (which imports FastAPI) would break this
  module's purity. The codes those errors carry are referenced by name only.
* The entitlement check (Requirement 2.9), which is the route's dependency, as above.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.marketplace import evidence_validator as _ev
from backend_app.backend.marketplace.evidence_validator import CriterionOutcome
from backend_app.backend.marketplace.submission_state import (
    OPEN_STATES,
    normalise_submission_state,
)

__all__ = [
    "EVALUATOR_VERSION",
    "METRIC_FIELDS",
    "MIN_LIVE_HISTORY_HOURS",
    "MP_CODES",
    "MP_EXECUTION_OK",
    "MP_LIVE_HISTORY",
    "MP_METRICS_COMPLETE",
    "MP_OWNERSHIP",
    "MP_PUBLIC_MESSAGES",
    "MP_SUBMISSION_OPEN",
    "MP_TENANT",
    "MP_VERSION_EXISTS",
    "MP_VERSION_VALID",
    "CriterionOutcome",
    "EligibilityVerdict",
    "evaluate",
]


# ══════════════════════════════════════════════════════════════════════════
# THE EVALUATOR VERSION (Requirement 2.11)
# ══════════════════════════════════════════════════════════════════════════

#: The evaluating-server version recorded on every audit record and returned on every verdict
#: (Requirement 2.11's "the evaluating server version"). Bumped whenever the *set of criteria*
#: or their meaning changes, so a stored evaluation names the rules it was decided under and an
#: Admin_Reviewer replaying an old audit record can tell which gate produced it. The delegated
#: ``EV_*`` rules carry their own versioning through ``evidence_validator.THRESHOLDS``; this
#: string versions the gate criteria this module owns.
#: 1.1.0 (MKT-07): ``MP_LIVE_HISTORY`` joined the criterion set and the backtest-count floor
#: moved from three to five. Both change what an admission *means*, so an evaluation stored
#: under 1.0.0 was decided under weaker rules and must not be read as though it were not.
EVALUATOR_VERSION = "eligibility-gate/1.1.0"


# ══════════════════════════════════════════════════════════════════════════
# THE LIVE-HISTORY THRESHOLD (MKT-07)
# ══════════════════════════════════════════════════════════════════════════

#: The owner brief's Task 15 D requirement 2: "at least 24 hours of verifiable live
#: algorithmic trading history". Expressed in hours because that is the unit the requirement
#: is written in, and named once so ``MP_LIVE_HISTORY`` and any later reader cannot drift.
MIN_LIVE_HISTORY_HOURS = 24

#: Rows read from each END of the strategy's live fill history when measuring the window.
#:
#: The criterion needs two instants - the earliest verified live fill and the latest - so read
#: 5 asks for a bounded window from each end (ascending, then descending) rather than for the
#: whole history. A single capped read would be wrong in a way that is easy to miss: a
#: high-frequency strategy with more fills than the cap inside its first hour would return a
#: slice spanning an hour and FAIL a strategy that has traded live for a month. Reading both
#: ends makes the measurement independent of how many fills sit between them.
#:
#: The window is 200 rather than 1 because ``filled_size`` is a ``character varying`` column,
#: so "a positive fill" cannot be expressed as a PostgREST filter and is applied here instead.
#: Taking one row from each end would let a single malformed or zero-size row at an end decide
#: the verdict; 200 means a false fail needs that many consecutive unusable rows at one end.
LIVE_HISTORY_END_WINDOW = 200


# ══════════════════════════════════════════════════════════════════════════
# THE GATE'S OWN CRITERION CODES (Requirements 2.2 - 2.7, 2.10)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 2.2 - the strategy row exists and its ``user_id`` is the caller.
MP_OWNERSHIP = "MP_OWNERSHIP"
#: Requirement 2.2 - the strategy's tenant matches the caller's tenant context.
MP_TENANT = "MP_TENANT"
#: Requirement 2.3 - at least one saved, non-draft Strategy_Version exists.
MP_VERSION_EXISTS = "MP_VERSION_EXISTS"
#: Requirement 2.3 - that Strategy_Version's canonical graph passes structural validation.
MP_VERSION_VALID = "MP_VERSION_VALID"
#: Requirement 2.4 - at least one referenced run completed and none failed.
MP_EXECUTION_OK = "MP_EXECUTION_OK"
#: Requirement 2.6 - every referenced run carries all seven metrics, non-null and finite.
MP_METRICS_COMPLETE = "MP_METRICS_COMPLETE"
#: Requirement 2.7 - no open Submission and no PUBLISHED Listing for this strategy.
MP_SUBMISSION_OPEN = "MP_SUBMISSION_OPEN"
#: MKT-07 - the strategy's verified live fills span at least :data:`MIN_LIVE_HISTORY_HOURS`.
MP_LIVE_HISTORY = "MP_LIVE_HISTORY"

#: The gate's eight codes, in the order :func:`evaluate` emits them.
MP_CODES: Tuple[str, ...] = (
    MP_OWNERSHIP,
    MP_TENANT,
    MP_VERSION_EXISTS,
    MP_VERSION_VALID,
    MP_EXECUTION_OK,
    MP_METRICS_COMPLETE,
    MP_SUBMISSION_OPEN,
    MP_LIVE_HISTORY,
)

#: One owner-actionable sentence per gate code, carrying no threshold digit, no internal
#: identifier, no column name, no table name and no query text (Requirement 2.10). Read-only,
#: so a call site cannot rewrite a public sentence in passing. ``errors.py`` remains the one
#: place the *HTTP body*'s sentence per error code is written; these are the gate's own
#: sentences for its criterion codes, held to the same deny-list by
#: ``tests/test_marketplace_error_surface.py``.
MP_PUBLIC_MESSAGES: Mapping[str, str] = MappingProxyType(
    {
        MP_OWNERSHIP: (
            "You can only publish a strategy that you own."
        ),
        MP_TENANT: (
            "This strategy belongs to a different workspace and cannot be published here."
        ),
        MP_VERSION_EXISTS: (
            "This strategy must have at least one saved version before it can be published. "
            "Save the strategy, then try again."
        ),
        MP_VERSION_VALID: (
            "The saved version of this strategy did not pass validation. "
            "Open it in the builder, resolve the reported problems, and save it again."
        ),
        MP_EXECUTION_OK: (
            "Every backtest you reference must be a run that finished successfully. "
            "Re-run any test that did not complete before publishing."
        ),
        MP_METRICS_COMPLETE: (
            "Every backtest you reference must have recorded its full set of performance "
            "results. Re-run any test that is missing them."
        ),
        MP_SUBMISSION_OPEN: (
            "This strategy already has a submission in progress or a published listing. "
            "Complete or withdraw it before submitting again."
        ),
        # Carries no digit, per Requirement 2.10 and the deny-list
        # `tests/test_marketplace_error_surface.py` holds these sentences to. "At least a
        # full day" is the threshold in words; the number lives in
        # :data:`MIN_LIVE_HISTORY_HOURS` and reaches the owner through the listing surface,
        # not through this sentence.
        MP_LIVE_HISTORY: (
            "This strategy must have traded live for at least a full day before it can be "
            "published. Deploy it to live trading and let it run; paper trading does not "
            "count towards this."
        ),
    }
)

#: Requirement 2.6's seven metrics, named once so ``MP_METRICS_COMPLETE`` and any later reader
#: of the evidence cannot drift apart on which figures "complete" means. Every one must be
#: present on every referenced run and read as a finite number.
METRIC_FIELDS: Tuple[str, ...] = (
    "total_return_pct",
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "total_trades",
    "final_capital",
)


# ══════════════════════════════════════════════════════════════════════════
# THE VERDICT VALUE TYPE
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EligibilityVerdict:
    """The result of one Eligibility_Gate evaluation.

    Frozen because a verdict is evidence of an evaluation: the audit record is written from it
    (Requirement 2.11) and the route renders it (Requirement 2.10), and neither may edit it on
    the way through.

    ``unevaluable`` is the third state Requirement 2.13 needs, kept separate from
    ``admitted``: when a read did not complete, ``admitted`` is ``False`` *and* ``unevaluable``
    is ``True``, and the route answers ``MARKETPLACE_ELIGIBILITY_UNEVALUABLE`` (503) rather
    than ``MARKETPLACE_ELIGIBILITY_FAILED`` (422). A criteria failure is ``admitted = False``
    with ``unevaluable = False``. Admission is ``admitted = True`` with ``unevaluable = False``
    and every outcome passed.
    """

    #: Whether every criterion passed. Always ``False`` when :attr:`unevaluable` is ``True``.
    admitted: bool
    #: Every criterion's outcome, in emission order. Empty only when :attr:`unevaluable`.
    outcomes: Tuple[CriterionOutcome, ...]
    #: The evaluated Strategy_Version identifier, or ``None`` when none could be resolved.
    version_id: Optional[str]
    #: The evaluating-server version (Requirement 2.11).
    evaluator_version: str = EVALUATOR_VERSION
    #: ``True`` when a required read did not complete, so eligibility is unknown (Req 2.13).
    unevaluable: bool = False

    @property
    def failed_outcomes(self) -> Tuple[CriterionOutcome, ...]:
        """The failing criteria alone, for the one response of Requirement 2.10."""
        return tuple(o for o in self.outcomes if not o.passed)


# ══════════════════════════════════════════════════════════════════════════
# THE GATE (Requirements 2.1 - 2.7, 2.10, 2.11, 2.13)
# ══════════════════════════════════════════════════════════════════════════


async def evaluate(
    caller: Any,
    strategy_id: Any,
    backtest_ids: Sequence[Any],
    supabase: Any,
) -> EligibilityVerdict:
    """Evaluate ``strategy_id`` for Marketplace publication on behalf of ``caller``.

    ``caller`` is the authenticated server-side identity - a mapping or object carrying at
    least an ``id`` and, where the platform is multi-tenant, a ``tenant_id`` (Requirement
    21.1's "derive the acting identity and tenant from the authenticated server-side
    session"). No identity is read from ``backtest_ids`` or from any request field; the caller
    is the only authority.

    ``backtest_ids`` are the Backtest_Evidence references the owner submitted. They are used
    only as a filter on the owner-scoped ``strategy_backtests`` read, so a reference to another
    user's run returns nothing and is treated as absent (Requirement 3.13).

    ``supabase`` is the injected Persistence_Layer handle. This function issues exactly four
    reads through it and performs no other I/O.

    Returns an :class:`EligibilityVerdict`. Writes one
    ``MARKETPLACE_ELIGIBILITY_EVALUATED`` audit record before returning, on every path
    (Requirement 2.11).
    """
    caller_id = _get(caller, "id")
    caller_tenant = _get(caller, "tenant_id")
    normalised_backtest_ids = _normalise_ids(backtest_ids)

    # ── The four owner-scoped reads. Any failure => unevaluable (Req 2.13) ──
    try:
        strategy_row = _read_strategy(supabase, strategy_id, caller_id)
        version_rows = _read_versions(supabase, strategy_id)
        backtest_rows = _read_backtests(supabase, normalised_backtest_ids, caller_id)
        open_submission_states, has_published_listing = _read_open_state(
            supabase, strategy_id
        )
        live_execution_rows = _read_live_executions(supabase, strategy_id, caller_id)
    except Exception as exc:  # noqa: BLE001 - a read that did not complete is unevaluable
        # No admit decision, no per-criterion outcomes: eligibility is *unknown*, which is
        # distinct by code from "not eligible" (Requirement 2.13). The audit record still
        # goes out, because Requirement 2.11 wants every evaluation recorded, including this
        # one - it names the code and the read that failed in its metadata.
        verdict = EligibilityVerdict(
            admitted=False,
            outcomes=(),
            version_id=None,
            unevaluable=True,
        )
        await _write_audit(
            supabase,
            caller_id=caller_id,
            strategy_id=strategy_id,
            version_id=None,
            verdict=verdict,
            unevaluable_reason=type(exc).__name__,
        )
        return verdict

    conditions = _ev.coerce_conditions(backtest_rows)
    # The seven display metrics of Requirement 2.6 are not part of the distinctness value
    # type, so MP_METRICS_COMPLETE reads them from the raw rows the reads returned rather than
    # from the coerced conditions.
    metric_rows = [row for row in backtest_rows if isinstance(row, Mapping)]
    evaluated_version_id = _evaluated_version_id(conditions, version_rows)

    outcomes: List[CriterionOutcome] = []

    # ── Requirement 2.2: existence, ownership, tenant ──────────────────
    strategy_exists = strategy_row is not None
    owner_matches = strategy_exists and _same_id(
        _get(strategy_row, "user_id"), caller_id
    )
    outcomes.append(_mp_outcome(MP_OWNERSHIP, strategy_exists and owner_matches))

    # A strategy that does not exist, or is not the caller's, also cannot have its tenant
    # confirmed - MP_TENANT fails alongside MP_OWNERSHIP rather than passing vacuously.
    # Under tenant == user the row's tenant comes from its owner column, not from a
    # ``tenant_id`` the table does not have (:func:`_strategy_tenant`), and the clause sits on
    # top of read 1's ``.eq("user_id", caller_id)`` filter as defence in depth rather than as
    # the only thing standing between two tenants.
    outcomes.append(
        _mp_outcome(
            MP_TENANT,
            strategy_exists
            and owner_matches
            and _tenant_matches(_strategy_tenant(strategy_row), caller_tenant),
        )
    )

    # ── Requirement 2.3: a saved, non-draft, structurally valid version ─
    saved_versions = [v for v in version_rows if not _is_draft(v)]
    outcomes.append(_mp_outcome(MP_VERSION_EXISTS, len(saved_versions) >= 1))
    outcomes.append(
        _mp_outcome(
            MP_VERSION_VALID,
            any(_version_graph_is_valid(v) for v in saved_versions),
        )
    )

    # ── Requirement 2.4: execution completed, none failed ──────────────
    outcomes.append(
        _mp_outcome(MP_EXECUTION_OK, _execution_ok(conditions))
    )

    # ── Requirement 3 (delegated): distinctness and quality ────────────
    # evidence_validator.validate emits ten EV_* outcomes, every one evaluated, none
    # short-circuiting. The gate reads them straight into its own list, so the one response
    # of Requirement 2.10 carries the MP_* and EV_* failures together.
    outcomes.extend(_ev.validate(conditions, caller_id, strategy_id))

    # ── Requirement 2.6: every referenced run carries all seven metrics ─
    outcomes.append(
        _mp_outcome(MP_METRICS_COMPLETE, _metrics_complete(metric_rows))
    )

    # ── Requirement 2.7: no open Submission, no PUBLISHED Listing ──────
    has_open_submission = any(
        normalise_submission_state(state) in OPEN_STATES
        for state in open_submission_states
    )
    outcomes.append(
        _mp_outcome(
            MP_SUBMISSION_OPEN,
            not has_open_submission and not has_published_listing,
        )
    )

    # ── MKT-07: 24 hours of verified live trading history ──────────────
    outcomes.append(
        _mp_outcome(MP_LIVE_HISTORY, _live_history_hours(live_execution_rows)
                    >= MIN_LIVE_HISTORY_HOURS)
    )

    admitted = all(o.passed for o in outcomes)
    verdict = EligibilityVerdict(
        admitted=admitted,
        outcomes=tuple(outcomes),
        version_id=evaluated_version_id,
        unevaluable=False,
    )
    await _write_audit(
        supabase,
        caller_id=caller_id,
        strategy_id=strategy_id,
        version_id=evaluated_version_id,
        verdict=verdict,
        unevaluable_reason=None,
    )
    return verdict


# ══════════════════════════════════════════════════════════════════════════
# THE FOUR READS (Requirements 2.2 - 2.7)
# ══════════════════════════════════════════════════════════════════════════


def _read_strategy(
    supabase: Any, strategy_id: Any, caller_id: Any
) -> Optional[Mapping[str, Any]]:
    """Read 1: the strategy row, owner-scoped. ``None`` when it is not the caller's.

    Selects only what Criterion 2.2 needs - ``id, user_id, archived_at`` - and never
    ``backtest_result``: the gate derives nothing from that blob (see the module docstring).
    Filtering ``.eq("user_id", caller_id)`` means a strategy that belongs to someone else
    returns no row, so it is indistinguishable from one that does not exist.

    There is no ``tenant_id`` in this projection and there must not be: ``public.strategies``
    has no such column, so naming it made this read raise ``42703`` and collapsed the whole
    evaluation to ``unevaluable`` (Requirement 2.13) for every caller. The row's tenant is read
    from the column that does exist - see :func:`_strategy_tenant`.
    """
    response = (
        supabase.table("strategies")
        .select("id, user_id, archived_at")
        .eq("id", strategy_id)
        .eq("user_id", caller_id)
        .execute()
    )
    rows = _rows(response)
    return rows[0] if rows else None


def _read_versions(supabase: Any, strategy_id: Any) -> List[Mapping[str, Any]]:
    """Read 2: the Strategy_Versions of ``strategy_id``, owner-scoped.

    Selects the identity, the version marker, the draft flag, the persisted validation state
    and the canonical graph, so ``MP_VERSION_EXISTS`` and ``MP_VERSION_VALID`` can both be
    decided from the returned rows without a further read.
    """
    # ``strategy_versions`` carries no ``user_id`` column of its own - a version belongs to
    # its parent strategy, and the owner scope is the ``strategies`` ownership already
    # confirmed by read 1 (plus RLS, which ties a version to the owner of its strategy). So
    # this read is scoped by ``strategy_id`` alone, exactly as ``design.md`` specifies; adding
    # a ``.eq("user_id", ...)`` here would reference a column that does not exist.
    response = (
        supabase.table("strategy_versions")
        .select(
            "id, strategy_id, version, is_draft, validation_state, "
            "blueprint, graph_json"
        )
        .eq("strategy_id", strategy_id)
        .execute()
    )
    return list(_rows(response))


def _read_backtests(
    supabase: Any, backtest_ids: Sequence[str], caller_id: Any
) -> List[Mapping[str, Any]]:
    """Read 3: the Backtest_Evidence rows, filtered to the references and the owner.

    Filtered ``.in_("id", backtest_ids)`` **and** ``.eq("user_id", caller_id)`` per Criterion
    2.4 and Requirement 3.13: a referenced run that belongs to another user is not returned,
    so ``EV_OWNERSHIP``, ``MP_EXECUTION_OK`` and ``MP_METRICS_COMPLETE`` all treat it as
    missing rather than leaking that it exists.

    An empty reference list is a real, decidable state - too few conditions - not a reason to
    skip the read, so the read is still issued and ``EV_COUNT`` reports the shortfall.
    """
    response = (
        supabase.table("strategy_backtests")
        .select(
            "id, user_id, strategy_id, version_id, status, completed_at, error_message, "
            "dataset, start_date, end_date, initial_capital, commission, slippage, "
            "dataset_checksum, dag_hash, total_trades, executed_bar_count, "
            "total_return_pct, sharpe_ratio, max_drawdown, win_rate, profit_factor, "
            "final_capital"
        )
        .in_("id", list(backtest_ids))
        .eq("user_id", caller_id)
        .execute()
    )
    return list(_rows(response))


def _read_open_state(
    supabase: Any, strategy_id: Any
) -> Tuple[List[Any], bool]:
    """Read 4: the open-state probe over ``marketplace_submissions`` + ``library_strategies``.

    Returns the states of any existing Submissions for this strategy and whether a
    ``library_strategies`` row for the same ``source_strategy_id`` is ``PUBLISHED``. Both are
    part of the one round trip Criterion 2.7 needs; a failure of either read propagates as the
    read failure the caller turns into ``MARKETPLACE_ELIGIBILITY_UNEVALUABLE``.
    """
    submissions = (
        supabase.table("marketplace_submissions")
        .select("id, submission_state")
        .eq("source_strategy_id", strategy_id)
        .execute()
    )
    submission_states = [_get(row, "submission_state") for row in _rows(submissions)]

    listings = (
        supabase.table("library_strategies")
        .select("id, moderation_status, is_active")
        .eq("source_strategy_id", strategy_id)
        .eq("moderation_status", "approved")
        .eq("is_active", True)
        .execute()
    )
    has_published_listing = len(_rows(listings)) > 0
    return submission_states, has_published_listing


#: The columns read 5 needs, and nothing more. No price, no size beyond the fill quantity, no
#: venue order id: the criterion is about *when* the strategy traded, so the projection carries
#: the three instants a fill can be stamped with, the two discriminators that make a row a
#: verified live fill, and the identity that lets two rows be told apart.
_LIVE_EXECUTION_PROJECTION = (
    "execution_id, strategy_id, status, exchange_id, filled_size, "
    "filled_at, submitted_at, created_at"
)

#: The one status that means the venue filled the order. `routers/orders.py`'s LIVE history
#: read uses the same value, so Trade History and this criterion agree on what a completed
#: live execution is rather than each deciding separately.
_LIVE_EXECUTION_STATUS = "COMPLETED"

#: The column the two end-windows are ORDERED by, which is deliberately not the column the
#: span is DATED by.
#:
#: Dating prefers `filled_at` (:func:`_live_history_hours`), but `filled_at` is nullable, and
#: ordering by a nullable column puts the nulls at one end: PostgreSQL defaults to NULLS LAST
#: ascending and NULLS FIRST descending. A strategy with more than
#: :data:`LIVE_HISTORY_END_WINDOW` fills whose `filled_at` was never stamped would therefore
#: have its *descending* window filled entirely with those rows, and the genuinely latest fill
#: would fall outside the slice — reporting a short span for a strategy that has traded live
#: for a month. A false FAIL, not a false pass, but a user-visible one: "I have traded live for
#: weeks and it says I have not."
#:
#: `created_at` is `NOT NULL` on this table (measured against production's column list), so
#: ordering by it has no null end to misplace. A record is created and then filled, so the
#: earliest-created and latest-created rows are the extremes of the history within the gap
#: between creation and fill — which is seconds, against a 24-hour threshold.
_LIVE_EXECUTION_ORDER_COLUMN = "created_at"


def _read_live_executions(
    supabase: Any, strategy_id: Any, caller_id: Any
) -> List[Mapping[str, Any]]:
    """Read 5: the strategy's verified **live** fills, from each end of its history (MKT-07).

    WHY ``execution_records`` AND NO OTHER LEDGER
    ---------------------------------------------
    The brief requires "verifiable live algorithmic trading history" from "actual persisted
    execution/runtime data", and forbids counting paper trading. Three candidate sources
    exist and only one answers all three clauses:

    * ``execution_records`` - the strategy-execution ledger. A row means this platform
      submitted that order; ``exchange_reconciliation.py`` is explicit that a venue order the
      platform cannot attribute is **reported, never written** here. Paper trading has its own
      store (``paper_sessions``) and, by ``test_paper01_paper_live_isolation``, no module under
      ``backend/paper`` reaches a venue at all - so a paper fill cannot appear in this table.
      Written exclusively through SQLAlchemy as the table owner, and since MKT-07's privilege
      change the ``authenticated`` role holds SELECT and nothing else, so **the account being
      judged cannot write its own evidence.**
    * ``strategy_deployments`` - would give deployment *runtime* directly, which is arguably
      the more natural reading of "24 hours". Rejected: ``authenticated`` holds
      INSERT/UPDATE/DELETE on it and the application's own writer uses the caller's client
      (``backend/strategy_service.py``), so the runtime figure is forgeable by the seller. A
      gate that reads forgeable evidence is not a gate.
    * ``strategies.backtest_result`` and the paper ledger - neither is live execution.

    WHAT IS READ
    ------------
    Two bounded end-windows (:data:`LIVE_HISTORY_END_WINDOW` rows each), ascending by the fill
    instant and then descending, scoped ``.eq("strategy_id", …)`` and
    ``.eq("tenant_id", caller_id)``. Two requests rather than one because the criterion needs
    the earliest and latest instants and a single capped read would measure only one end -
    see :data:`LIVE_HISTORY_END_WINDOW`.

    ``tenant_id`` is the owner scope here, not ``user_id``: the table declares both, the
    not-null one is ``tenant_id``, and it is what ``routers/orders.py``'s LIVE history read
    filters on. Under tenant == user the caller's id is their tenant, which is why
    ``caller_id`` is the value passed.

    The ``filled_size > 0`` clause is **not** sent as a filter. The column is
    ``character varying``, so a PostgREST comparison would be lexicographic - ``"0.5" > "0"``
    is true by luck, ``"10" > "9"`` is false - and a wrong filter here would silently drop real
    fills. It is applied in :func:`_live_history_hours` against a parsed number instead.

    Ordering is explicit for the same reason the cap exists: PostgREST's unordered default
    would make the returned slice, and therefore the verdict, arbitrary. It orders by
    :data:`_LIVE_EXECUTION_ORDER_COLUMN` rather than by the column the span is dated from —
    see that constant for why a nullable sort key would produce a false fail.
    """
    def _end(descending: bool) -> List[Mapping[str, Any]]:
        response = (
            supabase.table("execution_records")
            .select(_LIVE_EXECUTION_PROJECTION)
            .eq("strategy_id", strategy_id)
            .eq("tenant_id", caller_id)
            .eq("status", _LIVE_EXECUTION_STATUS)
            .not_.is_("exchange_id", "null")
            .order(_LIVE_EXECUTION_ORDER_COLUMN, desc=descending)
            .limit(LIVE_HISTORY_END_WINDOW)
            .execute()
        )
        return list(_rows(response))

    return _end(descending=False) + _end(descending=True)


# ══════════════════════════════════════════════════════════════════════════
# THE GATE CRITERIA
# ══════════════════════════════════════════════════════════════════════════


def _execution_ok(conditions: Sequence[_ev.BacktestCondition]) -> bool:
    """Criterion 2.4: at least one referenced run completed, and none failed.

    "Completed" is read strictly - status ``'completed'`` with a completion instant - and a
    failure is any run whose status is not ``'completed'`` or whose ``error_message`` carries
    text. An empty set fails: "at least one" is not satisfied by none. Nothing is inferred
    from ``strategies.backtest_result``; only the referenced runs are consulted.
    """
    if not conditions:
        return False
    completed_seen = False
    for condition in conditions:
        status = str(condition.status).strip().lower() if condition.status else ""
        errored = _is_present(condition.error_message)
        if status != "completed" or errored:
            return False
        completed_seen = True
    return completed_seen


def _metrics_complete(rows: Sequence[Mapping[str, Any]]) -> bool:
    """Criterion 2.6: every referenced run carries all seven metrics, non-null and finite.

    Read against the ``strategy_backtests`` rows themselves, never inferred: a ``NULL``, a
    non-numeric value, or a non-finite float (``NaN``/``inf``) fails the criterion for that
    run, and one failing run fails the set. An empty set fails, because "for every condition"
    over no conditions would vacuously pass and admit a strategy with no evidence at all.

    The seven metrics are read from the raw ``strategy_backtests`` row - the read selects them
    (``total_return_pct`` … ``final_capital``) - because they are the *display* figures of
    Requirement 2.6, not the distinctness inputs the :class:`_ev.BacktestCondition` value type
    models. Nothing is substituted for an absent figure (Requirement 3.11).
    """
    if not rows:
        return False
    return all(
        all(_is_finite_number(row.get(field_name)) for field_name in METRIC_FIELDS)
        for row in rows
    )


def _live_history_hours(rows: Sequence[Mapping[str, Any]]) -> float:
    """``MP_LIVE_HISTORY``: hours between the strategy's earliest and latest verified live fill.

    A row counts as a verified live fill when every clause holds, each read from the row and
    none inferred:

    * ``status`` is ``COMPLETED`` - the venue filled it. Re-checked here as well as in the
      filter, so a driver or test double that ignores a filter cannot widen the criterion.
    * ``exchange_id`` is present - a venue was involved. This is the clause that excludes
      anything not placed at an exchange.
    * ``filled_size`` parses to a number greater than zero - something actually traded. A
      ``NULL``, a blank, or unparseable text is not a fill and is dropped rather than read as
      zero or as "probably fine".
    * the row carries a readable instant: ``filled_at``, else ``submitted_at``, else
      ``created_at``, in that order. Same coalesce order as ``routers/orders.py``'s LIVE
      history read, so the two surfaces date the same fill identically.

    Returns ``0.0`` when fewer than two such rows exist. **Fewer than two fills is not 24
    hours of trading history**, whatever the deployment ran for: one fill demonstrates one
    order. Returning a float rather than a bool keeps the comparison against
    :data:`MIN_LIVE_HISTORY_HOURS` at the call site, where the threshold is visible.

    Duplicates across the two end-windows are harmless - a strategy with fewer fills than the
    window returns the same rows twice, and ``min``/``max`` over a duplicated set is the same
    pair of instants. Distinctness is enforced on ``execution_id`` so two readings of one fill
    cannot be counted as two fills.
    """
    instants: dict = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = _as_text(row.get("status"))
        if status is None or status.strip().upper() != _LIVE_EXECUTION_STATUS:
            continue
        if not _is_present(row.get("exchange_id")):
            continue
        filled = _as_float(row.get("filled_size"))
        if filled is None or filled <= 0:
            continue
        stamped = _as_instant(
            row.get("filled_at"), row.get("submitted_at"), row.get("created_at")
        )
        if stamped is None:
            continue
        key = _as_text(row.get("execution_id"))
        instants[key if key is not None else id(row)] = stamped

    if len(instants) < 2:
        return 0.0
    moments = sorted(instants.values())
    return (moments[-1] - moments[0]).total_seconds() / 3600.0


def _as_float(value: Any) -> Optional[float]:
    """``value`` as a finite float, or ``None`` when it is absent or unreadable.

    ``bool`` is rejected the same way :func:`_is_finite_number` rejects it: ``True`` is not a
    fill quantity. A non-finite result is ``None`` rather than a number, so ``NaN`` cannot pass
    a ``> 0`` test by accident - it would not, but relying on that is relying on a detail.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _as_instant(*candidates: Any) -> Optional[datetime]:
    """The first candidate that reads as a timezone-aware instant, or ``None``.

    A driver may hand back a ``datetime`` or an ISO-8601 string, and PostgREST's ``Z`` suffix
    is not accepted by :meth:`datetime.fromisoformat` before Python 3.11, so it is normalised.
    A naive datetime is read as UTC: every column in the projection is
    ``timestamp with time zone``, so a naive value means the driver dropped the offset rather
    than that the instant is local, and treating it as UTC keeps the subtraction well-defined
    instead of raising inside a criterion.
    """
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        text = str(value).strip()
        if not text:
            continue
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _version_graph_is_valid(version_row: Mapping[str, Any]) -> bool:
    """Criterion 2.3(b): the Strategy_Version's canonical graph passes structural validation.

    Two ways a version can be known valid, cheapest first:

    * its persisted ``validation_state`` is ``VALID`` - the Strategy_Builder recorded the
      verdict when the version was saved, and Requirement 2.3 names "the Strategy_Builder's
      existing structural validation"; or
    * failing a stored verdict, the canonical graph loads through the Strategy_Builder's own
      ``strategy_dag.schema.load_graph`` without raising, which is the same parse the builder
      applies. A graph that cannot be loaded cannot be valid.

    A version that carries neither a ``VALID`` state nor a loadable graph fails. The import of
    ``strategy_dag.schema`` is local so this module's import stays free of anything heavier
    than the standard library at module load - the schema module is pulled only when a version
    actually needs validating.
    """
    state = _get(version_row, "validation_state")
    if state is not None and str(state).strip().upper() == "VALID":
        return True

    try:
        from backend_app.backend.strategy_dag import schema as _schema

        _schema.load_graph(version_row)
        return True
    except Exception:  # noqa: BLE001 - an unparseable graph is simply not valid
        return False


# ══════════════════════════════════════════════════════════════════════════
# THE AUDIT WRITE (Requirement 2.11)
# ══════════════════════════════════════════════════════════════════════════


async def _write_audit(
    supabase: Any,
    *,
    caller_id: Any,
    strategy_id: Any,
    version_id: Optional[str],
    verdict: EligibilityVerdict,
    unevaluable_reason: Optional[str],
) -> None:
    """Record one ``MARKETPLACE_ELIGIBILITY_EVALUATED`` act on every evaluation.

    Uses the existing ``StrategyAuditLogger`` and its ``MARKETPLACE_ELIGIBILITY_EVALUATED``
    action, so retention matches existing records and no second audit facility is introduced
    (Requirement 2.11). Task 14.3 adds ``StrategyAuditLogger.record_or_raise`` (the never-swallow
    variant used inside the submission transaction) and the remaining ``MARKETPLACE_*`` action
    members; this function prefers ``record_or_raise`` when it is present and otherwise uses the
    always-present ``log`` method, so it is correct both before and after 14.3 lands.

    Requirement 2.11's six facts travel as: ``strategy_id`` and ``version_id`` on their own
    fields, ``actor_id`` as the acting identity, the per-criterion outcomes and the pass/fail
    per criterion in ``metadata['outcomes']``, ``EVALUATOR_VERSION`` in
    ``metadata['evaluator_version']``, and the ``timestamp`` the logger stamps. The reason line
    records the overall verdict in words for a human reading the trail.
    """
    from backend_app.core.audit_trail import (
        StrategyAuditAction,
        get_strategy_audit_logger,
    )

    logger = get_strategy_audit_logger()

    metadata = {
        "evaluator_version": verdict.evaluator_version,
        "admitted": verdict.admitted,
        "unevaluable": verdict.unevaluable,
        "outcomes": [
            {
                "code": o.code,
                "passed": o.passed,
                "subjects": list(o.subjects),
            }
            for o in verdict.outcomes
        ],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    if unevaluable_reason is not None:
        metadata["unevaluable_reason"] = unevaluable_reason

    if verdict.unevaluable:
        reason = "eligibility unevaluable: a required read did not complete"
    elif verdict.admitted:
        reason = "eligibility admitted: every criterion passed"
    else:
        failed = [o.code for o in verdict.failed_outcomes]
        reason = "eligibility rejected: " + ", ".join(failed)

    writer = getattr(logger, "record_or_raise", None)
    if not callable(writer):
        writer = logger.log

    await writer(
        StrategyAuditAction.MARKETPLACE_ELIGIBILITY_EVALUATED,
        actor_id=str(caller_id) if caller_id is not None else "unknown",
        resource_type="strategy",
        resource_id=str(strategy_id),
        reason=reason,
        strategy_id=str(strategy_id) if strategy_id is not None else None,
        version_id=str(version_id) if version_id is not None else None,
        metadata=metadata,
    )


# ══════════════════════════════════════════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════════════════════════════════════════


def _mp_outcome(code: str, passed: bool) -> CriterionOutcome:
    """One gate outcome, with its sentence taken from the one place gate sentences are written."""
    return CriterionOutcome(
        code=code,
        passed=bool(passed),
        message=MP_PUBLIC_MESSAGES[code],
        subjects=(),
    )


def _rows(response: Any) -> List[Mapping[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    A driver may return the rows on ``.data`` (the supabase-py convention this codebase uses),
    on a ``["data"]`` key, or - in a test double - as a bare list. A response carrying a
    non-empty ``error`` is a failed read and must raise, so :func:`evaluate` treats it as
    unevaluable rather than reading ``None`` as "no rows" and admitting on empty evidence.
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise _ReadFailed(str(error))

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


class _ReadFailed(Exception):
    """A Persistence_Layer read returned an error rather than rows (Requirement 2.13)."""


def _get(obj: Any, key: str) -> Any:
    """``obj[key]`` for a mapping, ``obj.key`` for an object, ``None`` when absent.

    ``caller`` and the driver rows may arrive as either a mapping or an attribute-carrying
    object depending on the call site and the test double, and the gate reads both the same
    way rather than assuming one shape.
    """
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalise_ids(backtest_ids: Sequence[Any]) -> List[str]:
    """The referenced identifiers as a de-duplicated list of strings, order preserved.

    Order is preserved and duplicates removed so the ``.in_`` filter is stable and a caller
    that repeats an id does not turn one run into two references. A ``None`` id is dropped: it
    can match no row.
    """
    seen: set = set()
    out: List[str] = []
    for raw in backtest_ids or ():
        if raw is None:
            continue
        text = str(raw)
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _same_id(left: Any, right: Any) -> bool:
    """Whether two identifiers denote the same principal, compared as case-folded text.

    A missing identifier on either side is never a match - an unreadable owner authorises
    nothing. Case-folded because a UUID may arrive as a ``uuid.UUID`` from one driver and its
    hex string from another, and hex is case-insensitive.
    """
    left_text = _as_text(left)
    right_text = _as_text(right)
    if left_text is None or right_text is None:
        return False
    return left_text.casefold() == right_text.casefold()


def _strategy_tenant(strategy_row: Optional[Mapping[str, Any]]) -> Any:
    """The tenant a ``strategies`` row belongs to, which is its ``user_id``.

    This platform's tenancy model is **tenant == user**. ``tenant-boundary-audit.md`` records
    ``strategies``' tenant predicate as ``user_id`` and notes the table has no ``tenant_id``
    column at all; ``core/dependencies.py`` builds every caller's ``tenant_id`` from the
    token's tenant claim *or*, failing one, from their own ``user_id``. So the owner column IS
    the tenant column, and reading it here is not a widening of Criterion 2.2 - it is the same
    clause, read off the column that exists.

    Named as its own function rather than inlined so there is one place the decision lives:
    adding a real ``tenant_id`` column later would change this function and nothing else.
    """
    return _get(strategy_row, "user_id")


def _tenant_matches(strategy_tenant: Any, caller_tenant: Any) -> bool:
    """Whether the strategy's tenant matches the caller's tenant context (Criterion 2.2).

    The three cases, and why each answers as it does:

    * **The caller carries no tenant context** - a direct or internal caller, since
      ``core/dependencies.py`` fills ``tenant_id`` for every HTTP one - then there is no tenant
      boundary asserted by the session, so there is nothing to violate and this passes. That
      holds whether or not the strategy carries a tenant, which is the one semantic this
      function changed in task 13.18: under tenant == user a strategy ALWAYS carries a tenant
      (its owner), so the old "exactly one side absent => False" branch would have refused
      every tenant-less caller unconditionally. Refusing is not the safe default here: read 1
      has already filtered ``.eq("user_id", caller_id)``, so the row in hand is the caller's
      own, and the only thing a refusal would protect against is the caller's own strategy.
    * **Both sides carry a tenant** - they must be equal, case-folded (a UUID arrives as text
      from one driver and as ``uuid.UUID`` from another, and hex is case-insensitive).
    * **The caller carries a tenant and the strategy does not** - no match. A caller inside a
      tenant must not publish a row whose tenant cannot be read; an unreadable owner authorises
      nothing. Reachable only for a row with no ``user_id``, which ``MP_OWNERSHIP`` has already
      failed, so this is defence in depth rather than the live branch.
    """
    caller_text = _as_text(caller_tenant)
    if caller_text is None:
        return True
    strategy_text = _as_text(strategy_tenant)
    if strategy_text is None:
        return False
    return strategy_text.casefold() == caller_text.casefold()


def _is_draft(version_row: Mapping[str, Any]) -> bool:
    """Whether a Strategy_Version row is a draft (Criterion 2.3's "saved immutable" version).

    A row with a truthy ``is_draft`` is a draft. Absent or falsy means a saved version. The
    truthiness read accepts the ``True``/``False``, ``1``/``0`` and ``'t'``/``'f'`` spellings a
    Postgres boolean column can arrive as.
    """
    return _as_bool(_get(version_row, "is_draft")) is True


def _evaluated_version_id(
    conditions: Sequence[_ev.BacktestCondition],
    version_rows: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    """The Strategy_Version this evaluation is *about*, for the audit record and the verdict.

    The evidence names one immutable version (Requirement 3.3, enforced by ``EV_ONE_VERSION``);
    that is the version the Submission would record, so it is the one reported here. When the
    conditions carry a single non-null ``version_id`` it is used; otherwise, when the strategy
    has exactly one saved version, that is reported; otherwise ``None`` - the evaluation is
    still audited (Requirement 2.11), it simply could not name a single version, which the
    ``EV_ONE_VERSION`` failure already explains.
    """
    version_ids = {c.version_id for c in conditions if c.version_id is not None}
    if len(version_ids) == 1:
        return next(iter(version_ids))
    saved = [v for v in version_rows if not _is_draft(v)]
    if len(saved) == 1:
        return _as_text(_get(saved[0], "id"))
    return None


def _is_finite_number(value: Any) -> bool:
    """Whether ``value`` is a present, finite number (Requirement 2.6).

    ``None`` is not; ``bool`` is not (``True`` is not a recorded metric); ``NaN`` and the
    infinities are not. An integer, a float, a ``Decimal`` or a numeric string that parses to a
    finite value is. Nothing is substituted for an absent or non-finite value.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    parsed = _ev._as_decimal(value)  # reuse the validator's one Decimal reader
    return parsed is not None and parsed.is_finite()


def _is_present(value: Any) -> bool:
    """Whether ``value`` is a recorded value rather than a ``NULL`` or a blank string."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _as_text(value: Any) -> Optional[str]:
    """``value`` as a non-blank string, or ``None`` - blank and ``NULL`` behave identically."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any) -> Optional[bool]:
    """``value`` as a boolean, accepting the spellings a Postgres boolean column arrives as.

    ``None`` for an unreadable value rather than a guess. A real boolean passes through; an
    integer ``0``/``1`` and the ``'t'``/``'f'``/``'true'``/``'false'`` text forms are mapped;
    anything else is ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in ("t", "true", "1", "yes", "y"):
        return True
    if text in ("f", "false", "0", "no", "n"):
        return False
    return None
