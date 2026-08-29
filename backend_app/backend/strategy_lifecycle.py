"""
backend_app/backend/strategy_lifecycle.py

The lifecycle state machine: which transitions exist, who may be edited, what a
deployment's state is called, and what is recorded when any of it moves.

Spec: strategy-builder task 8.3. ``design.md`` -> "Lifecycle" (the state diagram and its
three transition rules) and "Versioning and immutability" (``PROCEDURE
edit_deployed_strategy``). Requirements 9.4, 9.6, 9.7, 9.8, 9.9, 13.8, 13.10, 20.9.

WHY A SIBLING MODULE AND NOT MORE OF ``deployment_binding``
-----------------------------------------------------------
``deployment_binding`` answers one question - *may this version be bound to this account,
in this mode, right now?* - and answers it with reads only. This module answers a
different one: *given where something is, where may it go next, and what is written down
when it goes there?* It is consulted by the deploy path, by pause/resume/stop, by a guard
trip and by the editor, three of which have nothing to do with binding. Keeping it here
means the runtime and the routers reach the same machine, and it means a transition can
be tested without a TestClient and without a venue.

Nothing is duplicated. :class:`~backend_app.backend.deployment_binding.DeployRejected`
is the refusal base class (so a router that already maps it maps these too),
``BINDING_STATES`` is imported rather than re-spelled, ``LIFECYCLE_STATES`` remains
``chk_lifecycle_state`` verbatim in ``strategy_builder``, and the version write goes
through ``training_worker.set_version_lifecycle`` - the existing validated setter - not
through a third writer of the same column.

THE TWO MACHINES, AND WHY THEY ARE TWO
--------------------------------------
There are two state vocabularies in play and they are **not** the same machine:

* the **version** lifecycle - ``chk_lifecycle_state``'s eleven states, of which
  ``DRAFT..ARCHIVED`` describe an author's artifact; and
* the **binding** state - Requirement 13.10's five (``DEPLOYING``, ``RUNNING``,
  ``PAUSED``, ``STOPPED``, ``FAILED``), which describe one running deployment.

They overlap on three names and differ on two: the version vocabulary has no ``FAILED``,
and the binding vocabulary has no ``ARCHIVED``. Both are transcribed here **verbatim from
their sources**, and :data:`VERSION_STATE_FOR_BINDING_STATE` is the only place they are
related - deliberately partial, because a binding that ends ``FAILED`` has no version
state to move to. See "THE ONE GAP THIS TASK REPORTS RATHER THAN INVENTS".

THE ONE GAP THIS TASK REPORTS RATHER THAN INVENTS
-------------------------------------------------
``design.md``'s diagram contains no ``DEPLOYED -> STOPPED`` edge. Its transition rule is
explicit - "only forward transitions listed above are legal" - so a deployment whose
runtime start **fails** leaves the version at ``DEPLOYED`` while the binding is
``FAILED``: the version cannot legally reach ``STOPPED``, and therefore cannot reach
``ARCHIVED`` either, until it has actually run. That is transcribed as-is rather than
patched with an invented edge, because the alternative is a state machine in the code
that no longer matches the one in the design and no reader can tell which is authoritative.
The consequence is *reported* in three places instead: the transition is skipped with a
warning naming the missing edge, :func:`transition_report` says
``version_state_moved: false`` with the reason, and Requirement 20.9's "preserve the
recorded reason" is satisfied at the **binding** level, which is where 20.9 puts it
("move the affected running deployments to STOPPED"). A deployment is always stoppable;
it is only the version's own label that has nowhere to go.

REQUIREMENT 13.10 IS A REPORTING REQUIREMENT, SO NO COLUMN VOCABULARY IS REWRITTEN
----------------------------------------------------------------------------------
``strategy_deployments.status`` is a lowercase legacy string (migration 001: "deploying,
running, paused, stopped, failed") with an index, existing rows and several writers, and
no ``CHECK`` constraint. Requirement 13.10 says the service SHALL *report* one of the
five uppercase states - so this module maps between the two (:func:`binding_state`,
:func:`status_column_value`) and rewrites nothing in the database. A status string outside
the mapping is reported as ``FAILED``, never as ``RUNNING`` or ``STOPPED``: claiming a
deployment is running when the platform cannot tell would be a claim about real money,
and claiming it is stopped would be a claim it is not. ``FAILED`` is the state that makes
an operator look, and the raw value travels alongside it as ``status_raw``.

WHAT AN EDIT DOES (Requirements 9.4, 9.9)
-----------------------------------------
:func:`edit_disposition` is the whole of ``PROCEDURE edit_deployed_strategy``'s decision:
a version in ``DEPLOYED``, ``RUNNING`` or ``PAUSED`` is read-only, so an edit yields a
**new draft** and the existing row is left alone. Enforcement is not only procedural -
migration 004c's ``trg_sv_immutable`` rejects any change to ``graph_json``,
``compiled_plan``, ``dag_hash`` or ``schema_version`` on exactly those three states - and
:func:`canvas_state` is the same verdict projected for the client, so the canvas renders
read-only from the backend's answer rather than from a rule the frontend re-implements.

MIGRATION 004 PART 1 MAY BE UNAPPLIED
-------------------------------------
``lifecycle_state`` arrives with ``backend_app/migrations/004_strategy_builder_canonical.sql``
(part 1). Every write here goes through the existing setter, which classifies a missing
column and degrades to a warning; :func:`apply_version_state` adds the file name to that
warning and reports ``moved: false`` upward. A lifecycle transition that could not be
recorded is never reported as recorded - and never a 500.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from backend_app.backend.deployment_binding import BINDING_STATES, DeployRejected
from backend_app.backend.strategy_builder import (
    LIFECYCLE_DRAFT,
    LIFECYCLE_READY,
    LIFECYCLE_STATES,
    LIFECYCLE_TRAINING,
    LIFECYCLE_VALIDATED,
)
from backend_app.core.audit_trail import StrategyAuditAction, get_strategy_audit_logger

logger = logging.getLogger("StrategyLifecycle")


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.3's ``deployment.state_transitions``. Guarded because this module writes
    lifecycle state for deploy, pause, resume, stop and the kill switch: a metrics failure
    must not be what leaves a deployment running.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


# ══════════════════════════════════════════════════════════════════════════
# VERSION LIFECYCLE - design.md's diagram, transcribed edge by edge
# ══════════════════════════════════════════════════════════════════════════

LIFECYCLE_SAVED = "SAVED"
LIFECYCLE_TRAINED = "TRAINED"
LIFECYCLE_DEPLOYED = "DEPLOYED"
LIFECYCLE_RUNNING = "RUNNING"
LIFECYCLE_PAUSED = "PAUSED"
LIFECYCLE_STOPPED = "STOPPED"
LIFECYCLE_ARCHIVED = "ARCHIVED"

#: ``backend_app/migrations/004_strategy_builder_canonical.sql``, named in every
#: degradation warning so an operator never has to guess which file to apply.
CANONICAL_LIFECYCLE_MIGRATION = "backend_app/migrations/004_strategy_builder_canonical.sql"

#: ``design.md`` -> Lifecycle, one entry per state, one target per arrow. Nothing is
#: added and nothing is dropped: ``VERSION_TRANSITIONS`` keys are exactly
#: :data:`LIFECYCLE_STATES` (a test pins that), so a state added to
#: ``chk_lifecycle_state`` cannot arrive here with undefined transitions and be
#: silently treated as terminal.
#:
#: Two edges deserve their note:
#:
#: * ``DRAFT -> DRAFT`` ("edit") is a real self-edge in the diagram: editing a draft
#:   updates it in place, which is exactly what makes editing a *deployed* version
#:   different.
#: * ``VALIDATED -> DRAFT`` is the diagram's "edit (new draft, version untouched)". It is
#:   transcribed because it is drawn, but the edit path does not use it: an edit of a
#:   read-only version INSERTS a new ``DRAFT`` row (see :func:`edit_disposition`) rather
#:   than moving an existing one backwards.
VERSION_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    LIFECYCLE_DRAFT: (LIFECYCLE_VALIDATED, LIFECYCLE_DRAFT),
    LIFECYCLE_VALIDATED: (LIFECYCLE_DRAFT, LIFECYCLE_SAVED),
    LIFECYCLE_SAVED: (LIFECYCLE_TRAINING, LIFECYCLE_READY),
    LIFECYCLE_TRAINING: (LIFECYCLE_TRAINED, LIFECYCLE_SAVED),
    LIFECYCLE_TRAINED: (LIFECYCLE_READY,),
    LIFECYCLE_READY: (LIFECYCLE_DEPLOYED, LIFECYCLE_ARCHIVED),
    LIFECYCLE_DEPLOYED: (LIFECYCLE_RUNNING,),
    LIFECYCLE_RUNNING: (LIFECYCLE_PAUSED, LIFECYCLE_STOPPED),
    LIFECYCLE_PAUSED: (LIFECYCLE_RUNNING, LIFECYCLE_STOPPED),
    LIFECYCLE_STOPPED: (LIFECYCLE_ARCHIVED,),
    LIFECYCLE_ARCHIVED: (),
}

#: Requirement 9.4 and 9.9, and migration 004c's trigger condition, in one tuple. A
#: version in one of these states is never mutated in place and its canvas is read-only.
READ_ONLY_LIFECYCLE_STATES: Tuple[str, ...] = (
    LIFECYCLE_DEPLOYED,
    LIFECYCLE_RUNNING,
    LIFECYCLE_PAUSED,
)

#: The four columns migration 004c's ``trg_sv_immutable`` refuses to let an immutable
#: version change. Published so a caller can say *which* fields are frozen rather than
#: "this is read-only".
IMMUTABLE_VERSION_COLUMNS: Tuple[str, ...] = (
    "graph_json",
    "compiled_plan",
    "dag_hash",
    "schema_version",
)


# ══════════════════════════════════════════════════════════════════════════
# BINDING STATE - Requirement 13.10's five, and the legacy status column
# ══════════════════════════════════════════════════════════════════════════

BINDING_DEPLOYING = "DEPLOYING"
BINDING_RUNNING = "RUNNING"
BINDING_PAUSED = "PAUSED"
BINDING_STOPPED = "STOPPED"
BINDING_FAILED = "FAILED"

#: The binding-level machine. Requirement 13.8's four actions plus the two the runtime
#: makes on its own (a start that succeeds, and a start or a run that fails).
#: ``STOPPED`` is terminal - a stopped deployment is not restarted, a new one is created,
#: because execution references an immutable ``version_id`` and a "restart" that silently
#: reused a row would lose which plan ran when. ``FAILED -> STOPPED`` exists so a failed
#: deployment can be closed out and its quota released.
BINDING_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    BINDING_DEPLOYING: (BINDING_RUNNING, BINDING_PAUSED, BINDING_STOPPED, BINDING_FAILED),
    BINDING_RUNNING: (BINDING_PAUSED, BINDING_STOPPED, BINDING_FAILED),
    BINDING_PAUSED: (BINDING_RUNNING, BINDING_STOPPED, BINDING_FAILED),
    BINDING_STOPPED: (),
    BINDING_FAILED: (BINDING_STOPPED,),
}

ACTION_DEPLOY = "deploy"
ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_STOP = "stop"

#: Requirement 13.8's four transitions. ``deploy`` is listed for completeness and for the
#: audit vocabulary; it creates the binding rather than moving one, so it has no source
#: states and is not accepted by :func:`resolve_action`.
BINDING_ACTIONS: Tuple[str, ...] = (ACTION_DEPLOY, ACTION_PAUSE, ACTION_RESUME, ACTION_STOP)

#: What each action asks for. ``from_states`` is what may legally move; a current state
#: that already **is** the target is idempotent (see :func:`resolve_action`).
_ACTION_TARGET: Dict[str, str] = {
    ACTION_PAUSE: BINDING_PAUSED,
    ACTION_RESUME: BINDING_RUNNING,
    ACTION_STOP: BINDING_STOPPED,
}

#: The version state a binding state implies, where the version machine has a legal edge
#: for it. ``FAILED`` maps to nothing - ``chk_lifecycle_state`` has no ``FAILED`` member,
#: and inventing one would mean writing a value the database rejects with a 23514.
VERSION_STATE_FOR_BINDING_STATE: Dict[str, Optional[str]] = {
    BINDING_DEPLOYING: LIFECYCLE_DEPLOYED,
    BINDING_RUNNING: LIFECYCLE_RUNNING,
    BINDING_PAUSED: LIFECYCLE_PAUSED,
    BINDING_STOPPED: LIFECYCLE_STOPPED,
    BINDING_FAILED: None,
}

#: Canonical state -> the value written to ``strategy_deployments.status``. Lowercase,
#: exactly migration 001's documented vocabulary, so every existing reader of that column
#: (``stop_deployment``'s "running" check, ``idx_strategy_deployments_status``, the
#: dashboards) keeps working unchanged.
STATUS_COLUMN_VALUE: Dict[str, str] = {
    BINDING_DEPLOYING: "deploying",
    BINDING_RUNNING: "running",
    BINDING_PAUSED: "paused",
    BINDING_STOPPED: "stopped",
    BINDING_FAILED: "failed",
}

#: Every legacy ``status`` spelling this codebase writes or has written, mapped onto the
#: five. ``deployed`` and ``starting`` mean "bound, not yet confirmed running" - which is
#: what ``DEPLOYING`` is; ``stopping`` is reported as ``STOPPED`` because the stop was
#: already ordered and no intent may follow it.
_STATUS_TO_BINDING_STATE: Dict[str, str] = {
    "deploying": BINDING_DEPLOYING,
    "deployed": BINDING_DEPLOYING,
    "pending": BINDING_DEPLOYING,
    "queued": BINDING_DEPLOYING,
    "starting": BINDING_DEPLOYING,
    "running": BINDING_RUNNING,
    "active": BINDING_RUNNING,
    "restarting": BINDING_DEPLOYING,
    "paused": BINDING_PAUSED,
    "stopping": BINDING_STOPPED,
    "stopped": BINDING_STOPPED,
    "cancelled": BINDING_STOPPED,
    "canceled": BINDING_STOPPED,
    "completed": BINDING_STOPPED,
    "failed": BINDING_FAILED,
    "error": BINDING_FAILED,
    "crashed": BINDING_FAILED,
}

#: The states a guard trip or a kill switch has something to stop (Requirement 20.9).
STOPPABLE_BINDING_STATES: Tuple[str, ...] = (
    BINDING_DEPLOYING,
    BINDING_RUNNING,
    BINDING_PAUSED,
)


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class LifecycleRejected(DeployRejected):
    """One classified lifecycle refusal, carrying its own HTTP status.

    A subclass of :class:`~backend_app.backend.deployment_binding.DeployRejected` rather
    than a parallel exception type, for one concrete reason: both deploy routes already
    map ``DeployRejected`` to ``(http_status, to_detail())``, so every refusal in this
    module reaches a client as the status the requirement asks for without a second
    mapping that could drift. ``design.md``'s status table puts every lifecycle conflict
    at **409**, which is this class's default.

    Requirement 9.7 is why the message always names the current state: a refusal that
    only said "illegal transition" would leave the author with nothing to act on.
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
# VERSION LIFECYCLE: THE GATE (Requirements 9.6, 9.7)
# ══════════════════════════════════════════════════════════════════════════


def normalise_lifecycle_state(value: Any) -> Optional[str]:
    """``value`` as a member of ``chk_lifecycle_state``, or ``None``.

    Uppercased first, because ``READY`` and ``ready`` name the same state and the deploy
    gate already accepts either. A value outside the vocabulary returns ``None`` rather
    than raising: the caller decides whether an unrecognised state is a refusal (it
    usually is) and gets to say what it was.
    """
    if value is None:
        return None
    label = str(value).strip().upper()
    return label if label in LIFECYCLE_STATES else None


def legal_transitions(state: Any) -> Tuple[str, ...]:
    """Every state ``state`` may legally move to. ``()`` for terminal or unrecognised."""
    label = normalise_lifecycle_state(state)
    if label is None:
        return ()
    return VERSION_TRANSITIONS.get(label, ())


def is_transition_legal(current: Any, target: Any) -> bool:
    """Whether ``current -> target`` is an edge in ``design.md``'s diagram."""
    label = normalise_lifecycle_state(target)
    return label is not None and label in legal_transitions(current)


def assert_transition_legal(
    current: Any,
    target: Any,
    *,
    version_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    action: Optional[str] = None,
) -> Tuple[str, str]:
    """Requirements 9.6 and 9.7: only the diagram's transitions, or a 409 naming the state.

    Returns
        ``(current_label, target_label)`` normalised, when the transition is legal.

    Raises
        :class:`LifecycleRejected` (409). ``LIFECYCLE_STATE_UNRECOGNISED`` when either end
        is outside ``chk_lifecycle_state`` - which is a refusal and not a pass-through,
        because a write of that value would be a 23514 from PostgreSQL - and
        ``LIFECYCLE_TRANSITION_INVALID`` when both are real states but the edge is not
        drawn. Both carry ``lifecycle_state`` (the current one, by name, per 9.7),
        ``requested_state`` and ``legal_transitions``, so a client can show the author
        what *is* possible from where they are.
    """
    current_label = normalise_lifecycle_state(current)
    target_label = normalise_lifecycle_state(target)
    subject = _subject(strategy_id, version_id)

    if current_label is None or target_label is None:
        unknown = "current" if current_label is None else "requested"
        raise LifecycleRejected(
            "LIFECYCLE_STATE_UNRECOGNISED",
            f"Cannot move {subject} from {current!r} to {target!r}: the {unknown} state "
            f"is not one of {sorted(LIFECYCLE_STATES)} (chk_lifecycle_state).",
            {
                "lifecycle_state": current_label or (None if current is None else str(current)),
                "requested_state": target_label or (None if target is None else str(target)),
                "recognised_states": sorted(LIFECYCLE_STATES),
                "legal_transitions": list(legal_transitions(current)),
                "strategy_id": strategy_id,
                "version_id": version_id,
                "action": action,
            },
        )

    if target_label in VERSION_TRANSITIONS.get(current_label, ()):
        return current_label, target_label

    allowed = list(VERSION_TRANSITIONS.get(current_label, ()))
    raise LifecycleRejected(
        "LIFECYCLE_TRANSITION_INVALID",
        f"Cannot move {subject} from {current_label} to {target_label}: that transition "
        f"is not part of the strategy lifecycle. From {current_label} the legal "
        f"transitions are "
        f"{', '.join(allowed) if allowed else 'none - it is a terminal state'}.",
        {
            "lifecycle_state": current_label,
            "requested_state": target_label,
            "legal_transitions": allowed,
            "strategy_id": strategy_id,
            "version_id": version_id,
            "action": action,
        },
    )


def _subject(strategy_id: Optional[str], version_id: Optional[str]) -> str:
    if version_id and strategy_id:
        return f"version {version_id} of strategy {strategy_id}"
    if version_id:
        return f"version {version_id}"
    if strategy_id:
        return f"strategy {strategy_id}"
    return "this version"


# ══════════════════════════════════════════════════════════════════════════
# EDITING A DEPLOYED VERSION (Requirements 9.4, 9.9)
# ══════════════════════════════════════════════════════════════════════════


def is_read_only_state(state: Any) -> bool:
    """Whether a version in ``state`` is immutable (9.4) and renders read-only (9.9)."""
    return normalise_lifecycle_state(state) in READ_ONLY_LIFECYCLE_STATES


@dataclass(frozen=True)
class EditDisposition:
    """What an edit of one version does. ``design.md`` -> ``edit_deployed_strategy``."""

    lifecycle_state: Optional[str]
    #: ``True`` when the row must not be mutated: the edit becomes a new draft.
    read_only: bool
    #: The version the new draft is based on (``base := current.id``), or ``None``.
    base_version_id: Optional[str]
    #: Human-readable, and on the wire: the author is told why they got a draft.
    reason: str

    @property
    def creates_new_draft(self) -> bool:
        return self.read_only

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lifecycle_state": self.lifecycle_state,
            "read_only": self.read_only,
            "creates_new_draft": self.creates_new_draft,
            "base_version_id": self.base_version_id,
            "reason": self.reason,
        }


def edit_disposition(version_row: Optional[Mapping[str, Any]]) -> EditDisposition:
    """Whether editing this version mutates it or forks a new ``DRAFT``.

    Requirement 9.4: a ``DEPLOYED``, ``RUNNING`` or ``PAUSED`` version is never mutated -
    the edit produces a new draft and the existing record is left exactly as it is. A
    row that also carries ``is_read_only = TRUE`` is treated the same way even if its
    lifecycle state says otherwise, because that is the other half of migration 004c's
    trigger condition and the two must not disagree.

    A version whose ``lifecycle_state`` is absent (migration 004 part 1 unapplied) or
    unrecognised is treated as **read-only**. That is the safe direction: forking a draft
    from a version that was in fact running costs the author one extra save, whereas
    mutating a version that was in fact running changes what a live deployment is doing -
    which is the whole of Requirement 9's user story.
    """
    row = dict(version_row or {})
    state = normalise_lifecycle_state(row.get("lifecycle_state"))
    version_id = row.get("id")
    base_version_id = str(version_id) if version_id else None
    flagged = bool(row.get("is_read_only"))

    if state in READ_ONLY_LIFECYCLE_STATES:
        return EditDisposition(
            lifecycle_state=state,
            read_only=True,
            base_version_id=base_version_id,
            reason=(
                f"This version is {state}, so it cannot be changed. Your edit was saved "
                f"as a new draft based on it; validate and save that draft to create a "
                f"new version. The {state} version, and anything running from it, is "
                f"untouched."
            ),
        )

    if flagged:
        return EditDisposition(
            lifecycle_state=state,
            read_only=True,
            base_version_id=base_version_id,
            reason=(
                "This version is marked read-only, so it cannot be changed. Your edit "
                "was saved as a new draft based on it."
            ),
        )

    if state is None:
        raw = row.get("lifecycle_state")
        return EditDisposition(
            lifecycle_state=None,
            read_only=True,
            base_version_id=base_version_id,
            reason=(
                "This version records no recognised lifecycle state"
                + (f" ({raw!r})" if raw is not None else "")
                + ", so it is treated as read-only and your edit was saved as a new "
                "draft. A version that may be deployed must not be edited in place."
            ),
        )

    return EditDisposition(
        lifecycle_state=state,
        read_only=False,
        base_version_id=base_version_id,
        reason=f"This version is {state} and can still be edited in place.",
    )


def canvas_state(version_row: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Requirement 9.9's answer, decided by the backend and projected for the canvas.

    The frontend renders from this rather than re-deriving "is it deployed?" from a state
    string, so there is one rule and the canvas cannot be editable in a state the
    database's own trigger would reject a write from.
    """
    disposition = edit_disposition(version_row)
    return {
        "lifecycle_state": disposition.lifecycle_state,
        "read_only": disposition.read_only,
        "editable": not disposition.read_only,
        "edit_creates_new_draft": disposition.creates_new_draft,
        "frozen_fields": list(IMMUTABLE_VERSION_COLUMNS) if disposition.read_only else [],
        "read_only_states": list(READ_ONLY_LIFECYCLE_STATES),
        "legal_transitions": list(legal_transitions(disposition.lifecycle_state)),
        "reason": disposition.reason,
    }


def assert_version_mutable(
    version_row: Optional[Mapping[str, Any]],
    *,
    strategy_id: Optional[str] = None,
) -> None:
    """Refuse an in-place mutation of a read-only version (409).

    For callers that cannot fork a draft and must therefore refuse. A caller that *can*
    fork one uses :func:`edit_disposition` and returns the draft, which is what
    Requirement 9.4 asks for.
    """
    disposition = edit_disposition(version_row)
    if not disposition.read_only:
        return
    row = dict(version_row or {})
    raise LifecycleRejected(
        "VERSION_IMMUTABLE",
        f"Version {row.get('version') or row.get('id')} is "
        f"{disposition.lifecycle_state or 'in an unrecognised state'} and cannot be "
        f"modified in place. Edit it to create a new draft instead.",
        {
            "lifecycle_state": disposition.lifecycle_state,
            "read_only_states": list(READ_ONLY_LIFECYCLE_STATES),
            "frozen_fields": list(IMMUTABLE_VERSION_COLUMNS),
            "version_id": row.get("id"),
            "strategy_id": strategy_id,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# BINDING STATE (Requirements 13.8, 13.10)
# ══════════════════════════════════════════════════════════════════════════


def binding_state(row: Any) -> str:
    """One deployment's state as one of Requirement 13.10's five.

    Accepts a row (``{"status": ...}``) or a bare status string. An unrecognised status is
    reported as ``FAILED`` with a warning naming the raw value - see the module docstring
    for why that direction, and not ``RUNNING`` or ``STOPPED``.
    """
    raw = row.get("status") if isinstance(row, Mapping) else row
    if raw is None:
        logger.warning(
            "A deployment row records no status; reporting it as %s. Requirement 13.10 "
            "admits only %s, and a deployment whose state cannot be read must not be "
            "reported as running.",
            BINDING_FAILED,
            ", ".join(BINDING_STATES),
        )
        return BINDING_FAILED

    label = str(raw).strip()
    upper = label.upper()
    if upper in BINDING_STATES:
        return upper
    mapped = _STATUS_TO_BINDING_STATE.get(label.lower())
    if mapped is not None:
        return mapped
    logger.warning(
        "Deployment status %r is outside the known vocabulary; reporting it as %s "
        "rather than guessing whether it is running.",
        label,
        BINDING_FAILED,
    )
    return BINDING_FAILED


def is_active_binding_status(raw: Any) -> bool:
    """Whether ``raw`` is a **recognised** status spelling that means "still live".

    "Live" is :data:`STOPPABLE_BINDING_STATES` — the same set the kill switch has
    something to stop. Added for the strategy archive gate (trading-lifecycle-integration
    Requirement 3.1), which has to ask this question of ``strategies.status`` as well as
    of ``strategy_deployments.status``: the legacy deploy path records a running bot by
    setting ``strategies.status = 'running'`` and writes no deployment row.

    Deliberately **not** ``binding_state(raw) in STOPPABLE_BINDING_STATES``.
    :func:`binding_state` reports an unrecognised status as ``FAILED`` *and warns*, which
    is the right answer for a deployment row (a deployment whose state cannot be read must
    not be reported as running) but the wrong question here: ``strategies.status`` holds
    values from a different vocabulary altogether (``draft`` is its default), and every
    one of them would log a warning about a deployment that does not exist. An
    unrecognised value is therefore not active, and says nothing.
    """
    if raw is None:
        return False
    label = str(raw).strip()
    if not label:
        return False
    upper = label.upper()
    if upper in BINDING_STATES:
        return upper in STOPPABLE_BINDING_STATES
    mapped = _STATUS_TO_BINDING_STATE.get(label.lower())
    return mapped is not None and mapped in STOPPABLE_BINDING_STATES


def status_column_value(state: str) -> str:
    """The lowercase ``strategy_deployments.status`` value for a canonical state."""
    label = str(state).strip().upper()
    if label not in STATUS_COLUMN_VALUE:
        raise ValueError(
            f"{state!r} is not a deployment binding state; Requirement 13.10 admits "
            f"{', '.join(BINDING_STATES)}."
        )
    return STATUS_COLUMN_VALUE[label]


def is_binding_transition_legal(current: Any, target: Any) -> bool:
    """Whether one binding may move ``current -> target``."""
    label = str(target).strip().upper()
    source = str(current).strip().upper()
    return label in BINDING_TRANSITIONS.get(source, ())


@dataclass(frozen=True)
class ActionPlan:
    """What one of Requirement 13.8's actions does to one binding."""

    action: str
    current_state: str
    target_state: str
    #: ``True`` when the binding is already in the target state, so nothing moves.
    idempotent: bool

    @property
    def moves(self) -> bool:
        return not self.idempotent

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "current_state": self.current_state,
            "target_state": self.target_state,
            "idempotent": self.idempotent,
        }


def resolve_action(action: Any, current: Any) -> ActionPlan:
    """Requirement 13.8's gate: may this deployment be paused, resumed or stopped now?

    An action whose target the binding is **already in** is idempotent and succeeds
    without moving anything: a retried request after a dropped connection, or a
    double-clicked button, must not read as an error when the outcome the caller asked
    for is already the case. Anything else that is not an edge of
    :data:`BINDING_TRANSITIONS` is a **409** naming the current state (Requirement 9.7's
    rule, applied to the binding machine) and the actions that *are* available.

    Raises
        :class:`LifecycleRejected`: ``DEPLOYMENT_ACTION_UNRECOGNISED`` (422) for an action
        outside the vocabulary, ``DEPLOYMENT_ACTION_INVALID`` (409) for a legal action on
        a binding that cannot take it.
    """
    label = str(action or "").strip().lower()
    current_state = binding_state(current)

    if label not in _ACTION_TARGET:
        raise LifecycleRejected(
            "DEPLOYMENT_ACTION_UNRECOGNISED",
            f"{action!r} is not a deployment lifecycle action. Requirement 13.8 defines "
            f"{', '.join(BINDING_ACTIONS)}; deploy creates a binding rather than moving "
            f"one, so it is not requested here.",
            {
                "action": label or None,
                "binding_state": current_state,
                "supported_actions": [ACTION_PAUSE, ACTION_RESUME, ACTION_STOP],
            },
            http_status=422,
        )

    target = _ACTION_TARGET[label]
    if current_state == target:
        return ActionPlan(
            action=label, current_state=current_state, target_state=target, idempotent=True
        )

    if is_binding_transition_legal(current_state, target):
        return ActionPlan(
            action=label, current_state=current_state, target_state=target, idempotent=False
        )

    available = sorted(
        name
        for name, want in _ACTION_TARGET.items()
        if want == current_state or is_binding_transition_legal(current_state, want)
    )
    raise LifecycleRejected(
        "DEPLOYMENT_ACTION_INVALID",
        f"This deployment is {current_state}, so it cannot be {_past_tense(label)}. "
        + (
            f"From {current_state} the available action(s) are {', '.join(available)}."
            if available
            else f"A {current_state} deployment is final; deploy the version again to "
            "start a new one."
        ),
        {
            "action": label,
            "binding_state": current_state,
            "requested_state": target,
            "available_actions": available,
            "binding_states": list(BINDING_STATES),
        },
    )


def _past_tense(action: str) -> str:
    return {ACTION_PAUSE: "paused", ACTION_RESUME: "resumed", ACTION_STOP: "stopped"}.get(
        action, action
    )


def binding_report(
    row: Optional[Mapping[str, Any]],
    *,
    version_state: Any = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """One deployment as Requirement 13.10 reports it: the state, and how it was derived.

    ``status_raw`` travels alongside ``binding_state`` deliberately. The canonical state
    is what a client acts on; the raw column value is what an operator debugs with, and
    hiding it would make an unrecognised status indistinguishable from a real failure.
    """
    data = dict(row or {})
    state = binding_state(data)
    report: Dict[str, Any] = {
        "deployment_id": data.get("id"),
        "strategy_id": data.get("strategy_id"),
        "version_id": data.get("version_id"),
        "version": data.get("version"),
        "binding_state": state,
        "binding_states": list(BINDING_STATES),
        "status_raw": data.get("status"),
        "mode": data.get("mode"),
        "available_actions": sorted(
            name
            for name, want in _ACTION_TARGET.items()
            if want == state or is_binding_transition_legal(state, want)
        ),
        "reason": data.get("error_message"),
        "started_at": data.get("started_at"),
        "stopped_at": data.get("stopped_at"),
    }
    if version_state is not None:
        report["lifecycle_state"] = normalise_lifecycle_state(version_state)
    report.update(dict(extra or {}))
    return report


# ══════════════════════════════════════════════════════════════════════════
# THE AUDIT RECORD (Requirement 9.8)
# ══════════════════════════════════════════════════════════════════════════


async def record_audit(
    action: StrategyAuditAction,
    *,
    actor_id: str,
    resource_type: str,
    resource_id: Any,
    reason: str,
    before: Any = None,
    after: Any = None,
    strategy_id: Optional[str] = None,
    version_id: Optional[str] = None,
    deployment_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Record one audited act through ``core/audit_trail.py`` and return its audit id.

    Never raises. An audit write that fails is logged as a warning by the logger itself;
    a lifecycle transition that has already happened must not be reported as failed
    because a cache was unreachable, and a guard trip must not be prevented by one.
    """
    try:
        record = await get_strategy_audit_logger().log(
            action,
            actor_id=str(actor_id or "unknown"),
            resource_type=str(resource_type),
            resource_id=str(resource_id),
            reason=str(reason),
            before=None if before is None else str(before),
            after=None if after is None else str(after),
            strategy_id=strategy_id,
            version_id=version_id,
            deployment_id=deployment_id,
            metadata=dict(metadata or {}),
        )
        return record.audit_id
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning(
            "Audit record for %s on %s %s could not be written: %s",
            getattr(action, "value", action),
            resource_type,
            resource_id,
            exc,
        )
        return None


def actor_of(user: Optional[Mapping[str, Any]]) -> str:
    """The audited actor: the user id, or ``system`` for a platform-initiated act."""
    if not user:
        return "system"
    return str(user.get("id") or user.get("user_id") or "unknown")


# ══════════════════════════════════════════════════════════════════════════
# WRITING A VERSION TRANSITION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TransitionResult:
    """The outcome of one attempted version transition."""

    version_id: Optional[str]
    from_state: Optional[str]
    to_state: Optional[str]
    moved: bool
    reason: str
    detail: Optional[str] = None
    audit_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "moved": self.moved,
            "reason": self.reason,
            "detail": self.detail,
            "audit_id": self.audit_id,
        }


async def apply_version_state(
    sb: Any,
    version_row: Optional[Mapping[str, Any]],
    target: str,
    *,
    user: Optional[Mapping[str, Any]] = None,
    reason: str,
    action: Optional[str] = None,
    strategy_id: Optional[str] = None,
    strict: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TransitionResult:
    """Gate, write and audit one ``strategy_versions.lifecycle_state`` transition.

    Order: the gate first (a refusal costs no write), then the write, then the audit -
    so nothing is audited that did not happen and nothing happens unaudited.

    ``strict`` is the one knob, and it decides what an **illegal** transition does:

    * ``True`` (the default, and what a user-initiated transition uses): the refusal
      propagates as a :class:`LifecycleRejected` 409 naming the current state, which is
      Requirements 9.6 and 9.7.
    * ``False``: the transition is skipped, the reason is recorded on the result and a
      warning is logged. Used where the version's own label has nowhere legal to go but
      the *deployment* action must still complete - the ``DEPLOYED`` deployment that
      fails to start, described in the module docstring. Refusing the stop there would
      leave a deployment running because its version's label was awkward.

    A write that cannot land because migration 004 part 1 is unapplied is **never** an
    error: ``moved`` is ``False``, the file is named, and the caller reports it rather
    than 500-ing.
    """
    row = dict(version_row or {})
    version_id = str(row.get("id")) if row.get("id") else None
    current = normalise_lifecycle_state(row.get("lifecycle_state"))
    strategy = strategy_id or (str(row.get("strategy_id")) if row.get("strategy_id") else None)

    try:
        from_state, to_state = assert_transition_legal(
            row.get("lifecycle_state"),
            target,
            version_id=version_id,
            strategy_id=strategy,
            action=action,
        )
    except LifecycleRejected as exc:
        if strict:
            raise
        logger.warning(
            "Version %s stays at %s: %s (the deployment action itself is unaffected).",
            version_id,
            current,
            exc.message,
        )
        return TransitionResult(
            version_id=version_id,
            from_state=current,
            to_state=normalise_lifecycle_state(target),
            moved=False,
            reason=reason,
            detail=exc.message,
        )

    if not version_id:
        return TransitionResult(
            version_id=None,
            from_state=from_state,
            to_state=to_state,
            moved=False,
            reason=reason,
            detail="No version id was available, so no lifecycle state was written.",
        )

    # The existing validated setter. It checks the value against LIFECYCLE_STATES before
    # the write, classifies a missing canonical column and returns False rather than
    # raising, which is why this module writes no UPDATE of its own.
    from backend_app.backend.training_worker import set_version_lifecycle

    extra: Dict[str, Any] = {}
    if to_state in READ_ONLY_LIFECYCLE_STATES:
        # Migration 004c's trigger fires on `is_read_only OR lifecycle_state IN
        # (DEPLOYED, RUNNING, PAUSED)`, and 004c's own header records that nothing in
        # this repository ever set the column. Setting it here means the immutability
        # guarantee does not rest solely on the lifecycle half of that condition. OLD
        # still has is_read_only = FALSE at this UPDATE, so the trigger does not fire on
        # the statement that sets it, and it guards every later write.
        extra["is_read_only"] = True

    written = await set_version_lifecycle(sb, version_id, to_state, extra=extra or None)
    if not written:
        logger.warning(
            "Version %s could not be moved %s -> %s. If strategy_versions has no "
            "lifecycle_state column, apply %s; until then lifecycle transitions are not "
            "recorded and the immutability trigger has nothing to gate on. Reason for "
            "the transition: %s",
            version_id,
            from_state,
            to_state,
            CANONICAL_LIFECYCLE_MIGRATION,
            reason,
        )
        return TransitionResult(
            version_id=version_id,
            from_state=from_state,
            to_state=to_state,
            moved=False,
            reason=reason,
            detail=(
                "The lifecycle state could not be written. If this environment has not "
                f"applied {CANONICAL_LIFECYCLE_MIGRATION}, that is why."
            ),
        )

    audit_id = await record_audit(
        StrategyAuditAction.LIFECYCLE_TRANSITION,
        actor_id=actor_of(user),
        resource_type="strategy_version",
        resource_id=version_id,
        reason=reason,
        before=from_state,
        after=to_state,
        strategy_id=strategy,
        version_id=version_id,
        metadata={"action": action, **dict(metadata or {})},
    )
    # Requirement 24.3's `deployment.state_transitions`. Recorded here because every
    # lifecycle transition in the platform comes through this function, and recorded only
    # past the write - a transition the gate refused did not happen, and a write that could
    # not land because migration 004 part 1 is unapplied returned above. The labels are the
    # two states and the action, all closed vocabularies; no version, strategy, user or
    # tenant identifier appears, so the series count is bounded by the state machine.
    collector = _metrics()
    if collector is not None:
        collector.record_deployment_state_transition(from_state, to_state, action)

    logger.info(
        "Version %s moved %s -> %s (%s): %s",
        version_id,
        from_state,
        to_state,
        action or "transition",
        reason,
    )
    return TransitionResult(
        version_id=version_id,
        from_state=from_state,
        to_state=to_state,
        moved=True,
        reason=reason,
        audit_id=audit_id,
    )


# ══════════════════════════════════════════════════════════════════════════
# GUARD TRIP AND KILL SWITCH (Requirement 20.9)
# ══════════════════════════════════════════════════════════════════════════

#: Who tripped, for the audit record and for the reason string. Free-form, but these are
#: the ones this codebase produces.
TRIGGER_KILL_SWITCH = "global_kill_switch"
TRIGGER_GUARD = "guard_trip"
TRIGGER_USER = "user"


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


def _rows(result: Any) -> List[Dict[str, Any]]:
    return list((getattr(result, "data", None) or []) if result else [])


async def kill_switch_verdict() -> Dict[str, Any]:
    """What ``core/global_safety.py`` says right now, and why.

    Read, never derived: the kill switch owns its own state, including its fail-safe
    policy of treating an unreachable Redis as **active**. That policy is followed rather
    than second-guessed - if the platform considers execution blocked, a deployment this
    module still called ``RUNNING`` would be a false report - and the reason string says
    which it was, so an operator can tell a real trip from an outage.
    """
    from backend_app.core.global_safety import get_global_kill_switch

    switch = get_global_kill_switch()
    try:
        status = await switch.get_status()
    except Exception as exc:  # noqa: BLE001 - a safety read must not raise into a caller
        logger.warning("Kill switch status could not be read: %s", exc)
        return {"active": False, "readable": False, "reason": None, "detail": str(exc)}

    active = bool(status.get("kill_switch_active"))
    latched = bool(status.get("local_latch_active"))
    reason: Optional[str] = None
    if active:
        history: List[str] = []
        try:
            history = await switch.get_history(limit=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kill switch history could not be read: %s", exc)
        latest = history[0] if history else ""
        # `timestamp|ACTIVATE|reason|triggered_by`, GlobalKillSwitch's own format.
        parts = str(latest).split("|")
        recorded = parts[2].strip() if len(parts) >= 3 else ""
        if recorded:
            reason = f"Global kill switch active: {recorded}"
        elif latched:
            reason = (
                "Global kill switch active (local fail-safe latch; Redis unreachable, so "
                "the platform blocks execution until it recovers)."
            )
        else:
            reason = "Global kill switch active."
    return {
        "active": active,
        "readable": True,
        "reason": reason,
        "local_latch_active": latched,
    }


async def stop_deployments_for_reason(
    sb: Any,
    *,
    user_id: str,
    reason: str,
    triggered_by: str = TRIGGER_GUARD,
    strategy_id: Optional[str] = None,
    deployment_ids: Optional[List[str]] = None,
    user: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Requirement 20.9: move this user's live deployments to ``STOPPED``, reason preserved.

    The reason is written to ``strategy_deployments.error_message`` - a column migration
    001 already provides - alongside ``status = 'stopped'`` and ``stopped_at``. It is
    **preserved**, not summarised: whoever asks "why did this stop?" reads the same
    sentence the trip produced, and the audit record carries it a second time with the
    actor and the timestamp (Requirement 9.8).

    Scoping: every read and every write carries ``.eq("user_id", user_id)`` on top of the
    RLS-scoped client, so a trip against one tenant cannot stop another's deployments.
    A trip is not a reason to widen a filter.

    Order, and what a partial failure leaves: the row is stopped first and the version's
    label second, because the row is what the runtime and the quota read. A version whose
    label could not move (no legal edge from ``DEPLOYED``, or migration 004 part 1
    unapplied) is **reported**, not retried and not raised - the deployment is stopped
    either way, which is what 20.9 asks for.

    Returns
        ``{"reason", "triggered_by", "requested", "stopped": [...], "already_stopped":
        [...], "failed": [...], "versions": [...]}``. Every deployment this touched is
        named, so a caller can say what happened rather than how many.
    """
    stopped: List[Dict[str, Any]] = []
    already: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    versions: List[Dict[str, Any]] = []

    if sb is None or not user_id:
        return {
            "reason": reason,
            "triggered_by": triggered_by,
            "requested": list(deployment_ids or []),
            "stopped": stopped,
            "already_stopped": already,
            "failed": failed,
            "versions": versions,
            "detail": "No database client or no user; nothing was stopped.",
        }

    candidates = await _load_live_deployments(
        sb, user_id=user_id, strategy_id=strategy_id, deployment_ids=deployment_ids
    )
    for row in candidates:
        state = binding_state(row)
        deployment_id = str(row.get("id") or "")
        if state not in STOPPABLE_BINDING_STATES:
            already.append(binding_report(row))
            continue
        try:
            await _write_stop(sb, user_id=user_id, deployment_id=deployment_id, reason=reason)
        except Exception as exc:  # noqa: BLE001 - one failure must not hide the others
            logger.error(
                "Deployment %s could not be moved to STOPPED after %s: %s",
                deployment_id,
                triggered_by,
                exc,
            )
            failed.append({**binding_report(row), "error": str(exc)})
            continue

        stopped_row = {
            **row,
            "status": status_column_value(BINDING_STOPPED),
            "error_message": reason,
        }
        audit_id = await record_audit(
            StrategyAuditAction.DEPLOYMENT_ACTION,
            actor_id=actor_of(user) if user else triggered_by,
            resource_type="strategy_deployment",
            resource_id=deployment_id,
            reason=reason,
            before=state,
            after=BINDING_STOPPED,
            strategy_id=row.get("strategy_id"),
            version_id=row.get("version_id"),
            deployment_id=deployment_id,
            metadata={"action": ACTION_STOP, "triggered_by": triggered_by},
        )
        stopped.append(
            binding_report(stopped_row, extra={"audit_id": audit_id, "previous_state": state})
        )

        version_result = await _move_version_for_stop(
            sb,
            user_id=user_id,
            row=row,
            reason=reason,
            user=user,
            triggered_by=triggered_by,
        )
        if version_result is not None:
            versions.append(version_result.to_dict())

    logger.warning(
        "%s stopped %d deployment(s) for user %s (%d already stopped, %d failed). "
        "Reason preserved on each row: %s",
        triggered_by,
        len(stopped),
        user_id,
        len(already),
        len(failed),
        reason,
    )
    return {
        "reason": reason,
        "triggered_by": triggered_by,
        "requested": list(deployment_ids or []),
        "stopped": stopped,
        "already_stopped": already,
        "failed": failed,
        "versions": versions,
    }


async def stop_deployments_on_kill_switch(
    sb: Any,
    *,
    user_id: str,
    strategy_id: Optional[str] = None,
    user: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Requirement 20.9's kill-switch half: stop only if the switch actually says so.

    The reason comes from ``core/global_safety.py``'s own activation history, so what is
    written on the deployment is what was recorded when the switch was thrown, not a
    sentence invented here. If the switch is **not** active nothing is stopped and the
    result says why - a trip that did not happen must not stop anybody's deployments.
    """
    verdict = await kill_switch_verdict()
    if not verdict.get("active"):
        return {
            "kill_switch_active": False,
            "reason": None,
            "triggered_by": TRIGGER_KILL_SWITCH,
            "stopped": [],
            "already_stopped": [],
            "failed": [],
            "versions": [],
            "detail": "The global kill switch is not active; nothing was stopped.",
        }

    result = await stop_deployments_for_reason(
        sb,
        user_id=user_id,
        reason=str(verdict.get("reason") or "Global kill switch active."),
        triggered_by=TRIGGER_KILL_SWITCH,
        strategy_id=strategy_id,
        user=user,
    )
    result["kill_switch_active"] = True
    result["local_latch_active"] = verdict.get("local_latch_active")
    return result


async def _load_live_deployments(
    sb: Any,
    *,
    user_id: str,
    strategy_id: Optional[str] = None,
    deployment_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """This user's deployment rows, filtered in Python to the stoppable ones.

    The status filter is applied here rather than as a ``.in_()`` because the legacy
    column holds several spellings for the same state (``deployed``, ``starting``,
    ``active``) and :func:`binding_state` is the one place that vocabulary is decided. A
    server-side ``status = 'running'`` filter would silently miss a paused one.
    """
    wanted = {str(d) for d in (deployment_ids or []) if str(d)}
    rows: List[Dict[str, Any]] = []
    try:
        if wanted:
            for deployment_id in sorted(wanted):
                query = (
                    sb.table("strategy_deployments")
                    .select("*")
                    .eq("id", deployment_id)
                    .eq("user_id", user_id)
                    .execute()
                )
                rows.extend(_rows(await _execute(query)))
        else:
            builder = sb.table("strategy_deployments").select("*").eq("user_id", user_id)
            if strategy_id:
                builder = builder.eq("strategy_id", strategy_id)
            rows.extend(_rows(await _execute(builder.execute())))
    except Exception as exc:  # noqa: BLE001 - a read failure must not hide the trip
        logger.error(
            "Live deployments for user %s could not be read, so none could be stopped: %s",
            user_id,
            exc,
        )
        return []

    if wanted:
        return rows
    return [row for row in rows if binding_state(row) in STOPPABLE_BINDING_STATES]


async def _write_stop(sb: Any, *, user_id: str, deployment_id: str, reason: str) -> None:
    """The stop write. ``user_id`` is on the filter as well as on the client's RLS."""
    query = (
        sb.table("strategy_deployments")
        .update(
            {
                "status": status_column_value(BINDING_STOPPED),
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                # Requirement 20.9: the reason is preserved, on the row itself.
                "error_message": reason,
            }
        )
        .eq("id", deployment_id)
        .eq("user_id", user_id)
        .execute()
    )
    await _execute(query)


async def _move_version_for_stop(
    sb: Any,
    *,
    user_id: str,
    row: Mapping[str, Any],
    reason: str,
    user: Optional[Mapping[str, Any]],
    triggered_by: str,
) -> Optional[TransitionResult]:
    """Move the stopped deployment's version to ``STOPPED`` where the diagram allows it."""
    version_id = row.get("version_id")
    if not version_id:
        return None
    version_row = await load_version_row(sb, user_id=user_id, version_id=str(version_id))
    if version_row is None:
        return None
    return await apply_version_state(
        sb,
        version_row,
        LIFECYCLE_STOPPED,
        user=user,
        reason=reason,
        action=ACTION_STOP,
        strategy_id=row.get("strategy_id"),
        # Not strict: RUNNING and PAUSED have the edge, DEPLOYED does not, and a
        # deployment that never started must still be stoppable. See the module docstring.
        strict=False,
        metadata={"triggered_by": triggered_by, "deployment_id": row.get("id")},
    )


async def load_version_row(
    sb: Any, *, user_id: str, version_id: str
) -> Optional[Dict[str, Any]]:
    """One version row, or ``None``.

    Read through the caller's RLS-scoped client. ``strategy_versions`` carries no
    ``user_id`` column - tenancy is reached through ``strategies`` - so this read rests
    on RLS plus the deployment row that named the version, both of which were already
    scoped to ``user_id`` by the caller.
    """
    if sb is None or not version_id:
        return None
    try:
        query = sb.table("strategy_versions").select("*").eq("id", str(version_id)).execute()
        rows = _rows(await _execute(query))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Version %s could not be read: %s", version_id, exc)
        return None
    return rows[0] if rows else None


__all__ = [
    "ACTION_DEPLOY",
    "ACTION_PAUSE",
    "ACTION_RESUME",
    "ACTION_STOP",
    "ActionPlan",
    "BINDING_ACTIONS",
    "BINDING_DEPLOYING",
    "BINDING_FAILED",
    "BINDING_PAUSED",
    "BINDING_RUNNING",
    "BINDING_STOPPED",
    "BINDING_TRANSITIONS",
    "CANONICAL_LIFECYCLE_MIGRATION",
    "EditDisposition",
    "IMMUTABLE_VERSION_COLUMNS",
    "LIFECYCLE_ARCHIVED",
    "LIFECYCLE_DEPLOYED",
    "LIFECYCLE_PAUSED",
    "LIFECYCLE_RUNNING",
    "LIFECYCLE_SAVED",
    "LIFECYCLE_STOPPED",
    "LIFECYCLE_TRAINED",
    "LifecycleRejected",
    "READ_ONLY_LIFECYCLE_STATES",
    "STATUS_COLUMN_VALUE",
    "STOPPABLE_BINDING_STATES",
    "TRIGGER_GUARD",
    "TRIGGER_KILL_SWITCH",
    "TRIGGER_USER",
    "TransitionResult",
    "VERSION_STATE_FOR_BINDING_STATE",
    "VERSION_TRANSITIONS",
    "actor_of",
    "apply_version_state",
    "assert_transition_legal",
    "assert_version_mutable",
    "binding_report",
    "binding_state",
    "canvas_state",
    "edit_disposition",
    "is_active_binding_status",
    "is_binding_transition_legal",
    "is_read_only_state",
    "is_transition_legal",
    "kill_switch_verdict",
    "legal_transitions",
    "load_version_row",
    "normalise_lifecycle_state",
    "record_audit",
    "resolve_action",
    "status_column_value",
    "stop_deployments_for_reason",
    "stop_deployments_on_kill_switch",
]
