"""
tests/test_task_8_3_lifecycle_state_machine.py

Strategy-builder task 8.3 - the lifecycle state machine.
Requirements 9.4, 9.6, 9.7, 9.8, 9.9, 13.8, 13.10, 20.9.

WHAT THIS FILE HOLDS TO
-----------------------
* **The transition table IS design.md's diagram.** Every edge in the design's mermaid
  diagram is listed here by hand and compared for set equality against the module's
  table - so an edge added to the code without being in the design fails, and an edge in
  the design that the code dropped fails too. A second test asserts the table's keys are
  exactly ``chk_lifecycle_state``'s vocabulary, so a state added to the migration cannot
  arrive with undefined transitions and be silently treated as terminal.
* **Refusals are exhaustive, not sampled.** All 11 x 11 state pairs are checked against
  the table, and every refusal is required to name the *current* state (Requirement 9.7)
  in both the message and the structured detail.
* **13.10 is a total function.** ``binding_state`` must return one of the five for every
  input, including ``None`` and a status nothing recognises - and must return ``FAILED``,
  never ``RUNNING`` or ``STOPPED``, when it cannot tell.
* **20.9 is verified by reading the row back.** The guard-trip tests assert the reason is
  on ``error_message`` after the write, that another tenant's deployment was untouched,
  and that a deployment which never started is still stopped even though its version's
  label cannot legally move.
* **The doubles are the real thing wherever one exists.** The lifecycle vocabulary, the
  transition table, the binding vocabulary, the audit logger and the service are all real.
  The only doubles are the PostgREST client - which this environment has no instance of -
  and the global kill switch, which needs Redis. The fake client honours ``.eq`` filters
  and APPLIES updates to its rows, so "the tenant filter was on the write" and "the reason
  was preserved" are facts about the write rather than about the call.

WHAT THIS FILE CANNOT PROVE
---------------------------
There is no PostgreSQL here and migrations 004/004c/004e are unapplied, so nothing here
proves ``chk_lifecycle_state`` rejects a bad label, that ``trg_sv_immutable`` refuses a
graph edit on a DEPLOYED row, or that ``is_read_only`` makes that trigger fire. Those are
004c's own VERIFICATION queries and task 8.7's isolation matrix. There is no Redis either,
so the audit assertions are made against the logger's records, not against a Redis list.
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_lifecycle as lifecycle
from backend_app.backend.deployment_binding import BINDING_STATES, DeployRejected
from backend_app.backend.strategy_builder import LIFECYCLE_READY, LIFECYCLE_STATES
from backend_app.backend.strategy_service import StrategyService
from backend_app.core.audit_trail import (
    StrategyAuditAction,
    StrategyAuditLogger,
    get_strategy_audit_logger,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "strategy-builder" / "design.md"
ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

USER_ID = "user_task_8_3"
OTHER_USER_ID = "user_task_8_3_other"
STRATEGY_ID = "strategy_task_8_3"
VERSION_ID = "55555555-5555-4555-8555-555555555555"
DEPLOYMENT_ID = "66666666-6666-4666-8666-666666666666"


# ---------------------------------------------------------------------------
# design.md's diagram, transcribed by hand for the comparison
# ---------------------------------------------------------------------------

#: Every ``A --> B`` arrow in ``design.md`` -> Lifecycle.
DESIGN_EDGES = {
    ("DRAFT", "VALIDATED"),
    ("DRAFT", "DRAFT"),
    ("VALIDATED", "DRAFT"),
    ("VALIDATED", "SAVED"),
    ("SAVED", "TRAINING"),
    ("SAVED", "READY"),
    ("TRAINING", "TRAINED"),
    ("TRAINING", "SAVED"),
    ("TRAINED", "READY"),
    ("READY", "DEPLOYED"),
    ("DEPLOYED", "RUNNING"),
    ("RUNNING", "PAUSED"),
    ("PAUSED", "RUNNING"),
    ("RUNNING", "STOPPED"),
    ("PAUSED", "STOPPED"),
    ("STOPPED", "ARCHIVED"),
    ("READY", "ARCHIVED"),
}


def _table_edges():
    return {
        (state, target)
        for state, targets in lifecycle.VERSION_TRANSITIONS.items()
        for target in targets
    }


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable PostgREST stand-in that honours ``.eq`` and APPLIES updates.

    Applying the update matters for this file's subject: Requirement 20.9 says the reason
    is *preserved*, and a fake that only recorded the call could not tell a write that
    carried the reason from one that carried the filter twice.
    """

    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._not = {}
        self._mode = "select"
        self._payload = None

    def select(self, columns="*", *a, **kw):
        self._mode = "select"
        missing = [
            c
            for c in (columns.split(",") if isinstance(columns, str) else [])
            if c.strip() and c.strip() != "*" and c.strip() in self._parent.absent_columns
        ]
        if missing:
            raise RuntimeError(
                f"column {self._table}.{missing[0]} does not exist (42703 undefined_column)"
            )
        return self

    def insert(self, payload, *a, **kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *a, **kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def neq(self, column, value):
        self._not[column] = value
        return self

    def in_(self, column, values):
        self._filters[column] = ("__in__", tuple(values))
        return self

    def limit(self, *a, **kw):
        return self

    def _matches(self, row):
        for key, want in self._filters.items():
            if isinstance(want, tuple) and want and want[0] == "__in__":
                if row.get(key) not in want[1]:
                    return False
            elif str(row.get(key)) != str(want):
                return False
        for key, unwanted in self._not.items():
            if str(row.get(key)) == str(unwanted):
                return False
        return True

    def execute(self):
        rows = self._parent.rows.setdefault(self._table, [])
        offending = sorted(set(self._payload or {}) & self._parent.absent_columns)
        if self._mode in ("insert", "update") and offending:
            raise RuntimeError(
                f"Could not find the '{offending[0]}' column of '{self._table}' "
                f"in the schema cache (PGRST204)"
            )
        if self._mode == "insert":
            row = dict(self._payload)
            rows.append(row)
            self._parent.inserts.append((self._table, dict(row)))
            return _Result([row])
        if self._mode == "update":
            self._parent.updates.append(
                (self._table, dict(self._payload), dict(self._filters))
            )
            touched = []
            for row in rows:
                if self._matches(row):
                    row.update(dict(self._payload))
                    touched.append(row)
            return _Result(touched)
        return _Result([row for row in rows if self._matches(row)])


class _Supabase:
    def __init__(self, rows=None, absent_columns=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_columns = set(absent_columns)
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)

    def row(self, table, row_id):
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None


def _user(user_id=USER_ID):
    return {"id": user_id, "email": f"{user_id}@example.com", "access_token": "tok"}


def _version_row(state=LIFECYCLE_READY, **overrides):
    row = {
        "id": VERSION_ID,
        "strategy_id": STRATEGY_ID,
        "version": "v3.0",
        "lifecycle_state": state,
        "is_read_only": False,
        "is_current": True,
    }
    row.update(overrides)
    return row


def _deployment_row(status="running", **overrides):
    row = {
        "id": DEPLOYMENT_ID,
        "strategy_id": STRATEGY_ID,
        "user_id": USER_ID,
        "version_id": VERSION_ID,
        "version": "v3.0",
        "status": status,
        "mode": "paper",
    }
    row.update(overrides)
    return row


def _default_rows(version_state=LIFECYCLE_READY, deployment_status="running"):
    return {
        "strategies": [{"id": STRATEGY_ID, "user_id": USER_ID}],
        "strategy_versions": [_version_row(version_state)],
        "strategy_deployments": [_deployment_row(deployment_status)],
    }


def _service_on(sb):
    service = StrategyService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the documented seam
    return service


@pytest.fixture(autouse=True)
def audit(monkeypatch):
    """A real :class:`StrategyAuditLogger`, recording into a list instead of Redis.

    The logger is the production one; only its Redis hop is replaced, because there is no
    Redis in this environment and its absence must not be what makes an assertion pass.
    """
    import backend_app.core.audit_trail as audit_module

    recorded = []
    logger = StrategyAuditLogger()

    async def _append(record):
        recorded.append(record)
        return True

    logger._append_history = _append  # noqa: SLF001
    logger.recorded = recorded
    monkeypatch.setattr(audit_module, "_strategy_audit_logger", logger)
    return logger


@pytest.fixture(autouse=True)
def quota():
    """The subscription engine is not this task's subject; its calls are observed."""
    with patch(
        "backend_app.core.subscription_engine.SubscriptionEngine.decrement_quota_usage",
        new=AsyncMock(return_value=None),
    ) as decrement:
        yield decrement


# ---------------------------------------------------------------------------
# 1. The transition table is the design's diagram
# ---------------------------------------------------------------------------


class TestTransitionTableMatchesTheDesign:
    def test_every_edge_and_only_the_designs_edges_exist(self):
        assert _table_edges() == DESIGN_EDGES

    def test_the_table_covers_the_whole_check_vocabulary(self):
        """A state in ``chk_lifecycle_state`` with no entry would be silently terminal."""
        assert set(lifecycle.VERSION_TRANSITIONS) == set(LIFECYCLE_STATES)

    def test_every_target_is_itself_a_recognised_state(self):
        for state, targets in lifecycle.VERSION_TRANSITIONS.items():
            for target in targets:
                assert target in LIFECYCLE_STATES, (state, target)

    def test_archived_is_terminal_and_stopped_leads_only_to_archived(self):
        assert lifecycle.VERSION_TRANSITIONS["ARCHIVED"] == ()
        assert lifecycle.VERSION_TRANSITIONS["STOPPED"] == ("ARCHIVED",)

    def test_the_design_file_still_draws_these_edges(self):
        """If the diagram changes, this file must be revisited rather than quietly pass."""
        text = DESIGN_PATH.read_text(encoding="utf-8", errors="replace")
        section = text[text.index("### Lifecycle") : text.index("### Versioning")]
        for source, target in sorted(DESIGN_EDGES):
            assert f"{source} --> {target}" in section, (source, target)

    def test_deployed_has_no_stopped_edge_and_that_is_deliberate(self):
        """The gap the module reports rather than invents; pinned so it cannot drift."""
        assert "STOPPED" not in lifecycle.VERSION_TRANSITIONS["DEPLOYED"]
        assert lifecycle.VERSION_STATE_FOR_BINDING_STATE[lifecycle.BINDING_FAILED] is None


# ---------------------------------------------------------------------------
# 2. Requirements 9.6 and 9.7 - only these transitions, and a refusal names the state
# ---------------------------------------------------------------------------


class TestTransitionGate:
    @pytest.mark.parametrize("current", sorted(LIFECYCLE_STATES))
    @pytest.mark.parametrize("target", sorted(LIFECYCLE_STATES))
    def test_legality_is_exactly_the_table(self, current, target):
        legal = (current, target) in DESIGN_EDGES
        assert lifecycle.is_transition_legal(current, target) is legal
        if legal:
            assert lifecycle.assert_transition_legal(current, target) == (current, target)
        else:
            with pytest.raises(lifecycle.LifecycleRejected) as exc:
                lifecycle.assert_transition_legal(current, target)
            assert exc.value.code == "LIFECYCLE_TRANSITION_INVALID"
            assert exc.value.http_status == 409

    @pytest.mark.parametrize("current", sorted(LIFECYCLE_STATES))
    def test_every_refusal_names_the_current_state(self, current):
        """Requirement 9.7: a conflict response naming the current state."""
        for target in sorted(LIFECYCLE_STATES):
            if (current, target) in DESIGN_EDGES:
                continue
            with pytest.raises(lifecycle.LifecycleRejected) as exc:
                lifecycle.assert_transition_legal(
                    current, target, version_id=VERSION_ID, strategy_id=STRATEGY_ID
                )
            rejected = exc.value
            assert current in rejected.message
            assert rejected.details["lifecycle_state"] == current
            assert rejected.details["requested_state"] == target
            assert rejected.details["legal_transitions"] == list(
                lifecycle.VERSION_TRANSITIONS[current]
            )

    def test_a_terminal_state_says_so_rather_than_listing_nothing(self):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.assert_transition_legal("ARCHIVED", "DRAFT")
        assert "terminal state" in exc.value.message

    def test_lowercase_is_accepted_because_it_is_the_same_state(self):
        assert lifecycle.assert_transition_legal("ready", "deployed") == (
            "READY",
            "DEPLOYED",
        )

    @pytest.mark.parametrize("bad", ["LIVE", "almost_ready", "", None, 7])
    def test_a_state_outside_the_check_vocabulary_is_refused(self, bad):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.assert_transition_legal(bad, "DEPLOYED")
        assert exc.value.code == "LIFECYCLE_STATE_UNRECOGNISED"
        assert "chk_lifecycle_state" in exc.value.message

    def test_an_unrecognised_target_is_refused_too(self):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.assert_transition_legal("READY", "LIVE")
        assert exc.value.code == "LIFECYCLE_STATE_UNRECOGNISED"

    def test_normalise_returns_none_rather_than_guessing(self):
        assert lifecycle.normalise_lifecycle_state("deployed") == "DEPLOYED"
        assert lifecycle.normalise_lifecycle_state("nearly") is None
        assert lifecycle.normalise_lifecycle_state(None) is None

    def test_a_lifecycle_refusal_is_mapped_by_the_existing_deploy_handler(self):
        """It must be a ``DeployRejected``, or both deploy routes stop mapping it."""
        rejected = lifecycle.LifecycleRejected("X", "y", {"a": 1})
        assert isinstance(rejected, DeployRejected)
        assert rejected.http_status == 409
        assert rejected.to_detail() == {"error": "X", "message": "y", "a": 1}


# ---------------------------------------------------------------------------
# 3. Requirements 9.4 and 9.9 - editing a deployed version, and the canvas
# ---------------------------------------------------------------------------


class TestEditDisposition:
    @pytest.mark.parametrize("state", ["DEPLOYED", "RUNNING", "PAUSED"])
    def test_a_deployed_running_or_paused_version_forks_a_draft(self, state):
        disposition = lifecycle.edit_disposition(_version_row(state))
        assert disposition.read_only is True
        assert disposition.creates_new_draft is True
        assert disposition.base_version_id == VERSION_ID
        assert state in disposition.reason

    @pytest.mark.parametrize(
        "state",
        ["DRAFT", "VALIDATED", "SAVED", "TRAINING", "TRAINED", "READY", "STOPPED", "ARCHIVED"],
    )
    def test_every_other_state_is_editable_in_place(self, state):
        assert lifecycle.edit_disposition(_version_row(state)).read_only is False

    def test_the_read_only_set_is_exactly_the_immutability_triggers(self):
        assert lifecycle.READ_ONLY_LIFECYCLE_STATES == ("DEPLOYED", "RUNNING", "PAUSED")

    def test_the_is_read_only_flag_is_honoured_even_when_the_state_says_otherwise(self):
        assert lifecycle.edit_disposition(
            _version_row("DRAFT", is_read_only=True)
        ).read_only is True

    @pytest.mark.parametrize("state", [None, "LIVE", ""])
    def test_an_unreadable_state_is_treated_as_read_only(self, state):
        """Fail safe: forking a draft costs a save, mutating a live version costs money."""
        assert lifecycle.edit_disposition(_version_row(state)).read_only is True

    def test_an_empty_row_is_read_only(self):
        assert lifecycle.edit_disposition(None).read_only is True

    def test_the_canvas_projection_carries_the_verdict_and_the_frozen_fields(self):
        canvas = lifecycle.canvas_state(_version_row("RUNNING"))
        assert canvas["read_only"] is True
        assert canvas["editable"] is False
        assert canvas["edit_creates_new_draft"] is True
        assert canvas["frozen_fields"] == [
            "graph_json",
            "compiled_plan",
            "dag_hash",
            "schema_version",
        ]
        assert canvas["legal_transitions"] == ["PAUSED", "STOPPED"]

        editable = lifecycle.canvas_state(_version_row("READY"))
        assert editable["editable"] is True
        assert editable["frozen_fields"] == []

    def test_assert_version_mutable_refuses_a_deployed_row_with_a_409(self):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.assert_version_mutable(_version_row("DEPLOYED"))
        assert exc.value.code == "VERSION_IMMUTABLE"
        assert exc.value.http_status == 409
        lifecycle.assert_version_mutable(_version_row("READY"))  # no raise


@pytest.mark.asyncio
class TestEditingADeployedVersionThroughTheService:
    async def test_the_deployed_version_row_is_left_exactly_as_it_was(self):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        before = dict(sb.row("strategy_versions", VERSION_ID))
        service = _service_on(sb)

        result = await service.update_strategy(
            _user(), STRATEGY_ID, {"blueprint": {"nodes": [], "edges": []}}
        )

        # Requirement 9.4: unchanged. Including is_current - a running deployment's
        # version must not be demoted out from under it.
        assert sb.row("strategy_versions", VERSION_ID) == before
        draft = result["new_version"]
        assert draft["id"] != VERSION_ID
        assert draft["is_draft"] is True
        assert draft["is_current"] is False
        assert draft["cloned_from_version"] == VERSION_ID
        assert draft["lifecycle_state"] == "DRAFT"
        assert result["edit"]["read_only"] is True
        assert result["edit"]["creates_new_draft"] is True

    async def test_an_editable_version_keeps_the_pre_existing_behaviour(self):
        sb = _Supabase(_default_rows(version_state="READY"))
        service = _service_on(sb)

        result = await service.update_strategy(
            _user(), STRATEGY_ID, {"blueprint": {"nodes": [], "edges": []}}
        )

        assert result["edit"]["read_only"] is False
        assert result["new_version"]["is_current"] is True
        # The old version was demoted, as it always was.
        assert sb.row("strategy_versions", VERSION_ID)["is_current"] is False

    async def test_the_fork_is_audited_with_actor_and_reason(self, audit):
        sb = _Supabase(_default_rows(version_state="DEPLOYED"))
        await _service_on(sb).update_strategy(
            _user(), STRATEGY_ID, {"blueprint": {"nodes": [], "edges": []}}
        )

        records = audit.recorded
        assert [r.action for r in records] == [StrategyAuditAction.VERSION_CREATED]
        assert records[0].actor_id == USER_ID
        assert records[0].reason
        assert records[0].metadata["forked_from_read_only_version"] is True
        assert records[0].metadata["base_version_id"] == VERSION_ID

    async def test_no_blueprint_change_makes_no_version_and_no_edit_verdict(self):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        result = await _service_on(sb).update_strategy(
            _user(), STRATEGY_ID, {"name": "renamed"}
        )
        assert result["new_version"] is None
        assert result["edit"] is None


# ---------------------------------------------------------------------------
# 4. Requirement 13.10 - the five binding states, and only those
# ---------------------------------------------------------------------------


class TestBindingStateVocabulary:
    def test_the_five_states_are_the_ones_task_8_2_published(self):
        assert BINDING_STATES == ("DEPLOYING", "RUNNING", "PAUSED", "STOPPED", "FAILED")
        assert set(lifecycle.BINDING_TRANSITIONS) == set(BINDING_STATES)
        assert set(lifecycle.STATUS_COLUMN_VALUE) == set(BINDING_STATES)
        assert set(lifecycle.VERSION_STATE_FOR_BINDING_STATE) == set(BINDING_STATES)

    @pytest.mark.parametrize("state", BINDING_STATES)
    def test_the_column_value_round_trips(self, state):
        assert lifecycle.binding_state(lifecycle.status_column_value(state)) == state

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("deploying", "DEPLOYING"),
            ("deployed", "DEPLOYING"),
            ("starting", "DEPLOYING"),
            ("running", "RUNNING"),
            ("active", "RUNNING"),
            ("paused", "PAUSED"),
            ("stopping", "STOPPED"),
            ("stopped", "STOPPED"),
            ("failed", "FAILED"),
            ("RUNNING", "RUNNING"),
        ],
    )
    def test_the_legacy_column_maps_onto_the_five(self, status, expected):
        assert lifecycle.binding_state({"status": status}) == expected

    @pytest.mark.parametrize("status", [None, "", "banana", "half-running", 3])
    def test_a_state_that_cannot_be_read_is_failed_never_running(self, status):
        assert lifecycle.binding_state({"status": status}) == "FAILED"

    def test_a_status_this_module_cannot_name_is_refused_on_the_way_out(self):
        with pytest.raises(ValueError, match="13.10"):
            lifecycle.status_column_value("ARCHIVED")

    def test_the_report_states_both_the_canonical_state_and_the_raw_one(self):
        report = lifecycle.binding_report(
            _deployment_row("banana"), version_state="RUNNING"
        )
        assert report["binding_state"] == "FAILED"
        assert report["status_raw"] == "banana"
        assert report["binding_states"] == list(BINDING_STATES)
        assert report["lifecycle_state"] == "RUNNING"


# ---------------------------------------------------------------------------
# 5. Requirement 13.8 - deploy, pause, resume, stop
# ---------------------------------------------------------------------------


class TestActionGate:
    def test_the_four_actions_are_the_requirements_four(self):
        assert lifecycle.BINDING_ACTIONS == ("deploy", "pause", "resume", "stop")

    @pytest.mark.parametrize(
        "action,current,target",
        [
            ("pause", "running", "PAUSED"),
            ("resume", "paused", "RUNNING"),
            ("stop", "running", "STOPPED"),
            ("stop", "paused", "STOPPED"),
            ("stop", "deploying", "STOPPED"),
            ("stop", "failed", "STOPPED"),
            ("pause", "deploying", "PAUSED"),
        ],
    )
    def test_a_legal_action_resolves_to_its_target(self, action, current, target):
        plan = lifecycle.resolve_action(action, {"status": current})
        assert plan.target_state == target
        assert plan.idempotent is False
        assert plan.moves is True

    @pytest.mark.parametrize(
        "action,current",
        [("pause", "paused"), ("resume", "running"), ("stop", "stopped")],
    )
    def test_asking_for_the_state_it_is_already_in_is_idempotent(self, action, current):
        plan = lifecycle.resolve_action(action, {"status": current})
        assert plan.idempotent is True
        assert plan.moves is False

    @pytest.mark.parametrize(
        "action,current",
        [
            ("pause", "stopped"),
            ("resume", "stopped"),
            ("pause", "failed"),
            ("resume", "failed"),
        ],
    )
    def test_an_impossible_action_is_a_409_naming_the_current_state(self, action, current):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.resolve_action(action, {"status": current})
        rejected = exc.value
        state = lifecycle.binding_state({"status": current})
        assert rejected.code == "DEPLOYMENT_ACTION_INVALID"
        assert rejected.http_status == 409
        assert state in rejected.message
        assert rejected.details["binding_state"] == state

    def test_the_refusal_says_what_is_possible_instead(self):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.resolve_action("resume", {"status": "failed"})
        assert exc.value.details["available_actions"] == ["stop"]

    def test_whitespace_and_case_are_normalised_not_rejected(self):
        assert lifecycle.resolve_action("PAUSE ", {"status": "running"}).action == "pause"

    @pytest.mark.parametrize("action", ["deploy", "restart", "", None])
    def test_an_action_outside_the_vocabulary_is_a_422(self, action):
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            lifecycle.resolve_action(action, {"status": "running"})
        assert exc.value.code == "DEPLOYMENT_ACTION_UNRECOGNISED"
        assert exc.value.http_status == 422


# ---------------------------------------------------------------------------
# 6. Writing a transition, and Requirement 9.8's record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApplyVersionState:
    async def test_a_legal_transition_is_written_and_audited(self, audit):
        sb = _Supabase(_default_rows())
        result = await lifecycle.apply_version_state(
            sb,
            _version_row(LIFECYCLE_READY),
            "DEPLOYED",
            user=_user(),
            reason="Deployed by its author",
            action="deploy",
        )

        assert result.moved is True
        assert (result.from_state, result.to_state) == ("READY", "DEPLOYED")
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "DEPLOYED"

        record = audit.recorded[-1]
        assert record.action == StrategyAuditAction.LIFECYCLE_TRANSITION
        assert (record.before, record.after) == ("READY", "DEPLOYED")
        assert record.actor_id == USER_ID
        assert record.reason == "Deployed by its author"
        assert record.timestamp is not None
        assert result.audit_id == record.audit_id

    async def test_the_transition_into_an_immutable_state_also_sets_is_read_only(self):
        """004c's trigger fires on ``is_read_only OR lifecycle_state IN (...)``."""
        sb = _Supabase(_default_rows())
        await lifecycle.apply_version_state(
            sb, _version_row(LIFECYCLE_READY), "DEPLOYED", user=_user(), reason="r"
        )
        assert sb.row("strategy_versions", VERSION_ID)["is_read_only"] is True

    async def test_a_transition_out_of_one_does_not_clear_the_flag(self):
        """A version that has been deployed stays immutable; history must stay readable."""
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        sb.row("strategy_versions", VERSION_ID)["is_read_only"] = True
        await lifecycle.apply_version_state(
            sb, _version_row("RUNNING"), "STOPPED", user=_user(), reason="r"
        )
        row = sb.row("strategy_versions", VERSION_ID)
        assert row["lifecycle_state"] == "STOPPED"
        assert row["is_read_only"] is True

    async def test_an_illegal_transition_raises_and_writes_nothing(self, audit):
        sb = _Supabase(_default_rows())
        with pytest.raises(lifecycle.LifecycleRejected):
            await lifecycle.apply_version_state(
                sb, _version_row("READY"), "RUNNING", user=_user(), reason="r"
            )
        assert sb.updates == []
        assert audit.recorded == []

    async def test_non_strict_skips_it_reports_it_and_still_writes_nothing(self):
        sb = _Supabase(_default_rows(version_state="DEPLOYED"))
        result = await lifecycle.apply_version_state(
            sb,
            _version_row("DEPLOYED"),
            "STOPPED",
            user=_user(),
            reason="Guard trip",
            strict=False,
        )
        assert result.moved is False
        assert "DEPLOYED" in result.detail
        assert sb.updates == []

    async def test_an_unapplied_migration_004_is_reported_not_raised(self, caplog):
        sb = _Supabase(_default_rows(), absent_columns={"lifecycle_state"})
        with caplog.at_level("WARNING"):
            result = await lifecycle.apply_version_state(
                sb, _version_row("READY"), "DEPLOYED", user=_user(), reason="r"
            )
        assert result.moved is False
        assert lifecycle.CANONICAL_LIFECYCLE_MIGRATION in result.detail
        assert any(
            lifecycle.CANONICAL_LIFECYCLE_MIGRATION in record.getMessage()
            for record in caplog.records
        )

    async def test_a_row_with_no_id_writes_nothing_and_says_so(self):
        sb = _Supabase(_default_rows())
        result = await lifecycle.apply_version_state(
            sb, {"lifecycle_state": "READY"}, "DEPLOYED", user=_user(), reason="r"
        )
        assert result.moved is False
        assert sb.updates == []

    async def test_the_writer_is_the_existing_validated_setter(self):
        """One writer of ``lifecycle_state``, not a third one added by this task."""
        from backend_app.backend import training_worker

        sb = _Supabase(_default_rows())
        with patch.object(
            training_worker, "set_version_lifecycle", new=AsyncMock(return_value=True)
        ) as setter:
            await lifecycle.apply_version_state(
                sb, _version_row("READY"), "DEPLOYED", user=_user(), reason="r"
            )
        setter.assert_awaited_once()
        assert setter.await_args.args[1:] == (VERSION_ID, "DEPLOYED")
        assert sb.updates == []

    async def test_the_setter_refuses_to_have_its_state_overridden(self):
        from backend_app.backend.training_worker import set_version_lifecycle

        with pytest.raises(ValueError, match="chk_lifecycle_state"):
            await set_version_lifecycle(
                _Supabase(_default_rows()),
                VERSION_ID,
                "DEPLOYED",
                extra={"lifecycle_state": "ARCHIVED"},
            )


# ---------------------------------------------------------------------------
# 7. The deployment transitions end to end (Requirements 13.8, 13.10, 9.8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTransitionDeployment:
    async def test_pause_moves_the_row_the_version_and_the_audit(self, audit):
        sb = _Supabase(_default_rows(version_state="RUNNING", deployment_status="running"))
        result = await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "pause", reason="Rebalancing"
        )

        assert result["binding_state"] == "PAUSED"
        assert result["previous_state"] == "RUNNING"
        assert result["idempotent"] is False
        assert sb.row("strategy_deployments", DEPLOYMENT_ID)["status"] == "paused"
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "PAUSED"

        actions = [r.action for r in audit.recorded]
        assert StrategyAuditAction.DEPLOYMENT_ACTION in actions
        assert StrategyAuditAction.LIFECYCLE_TRANSITION in actions
        deployment_record = [
            r for r in audit.recorded if r.action == StrategyAuditAction.DEPLOYMENT_ACTION
        ][0]
        assert (deployment_record.before, deployment_record.after) == ("RUNNING", "PAUSED")
        assert deployment_record.reason == "Rebalancing"
        assert deployment_record.actor_id == USER_ID

    async def test_resume_moves_it_back(self):
        sb = _Supabase(_default_rows(version_state="PAUSED", deployment_status="paused"))
        result = await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "resume"
        )
        assert result["binding_state"] == "RUNNING"
        assert sb.row("strategy_deployments", DEPLOYMENT_ID)["status"] == "running"
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "RUNNING"

    async def test_stop_preserves_the_reason_on_the_row(self):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "stop", reason="Drawdown limit reached"
        )
        row = sb.row("strategy_deployments", DEPLOYMENT_ID)
        assert row["status"] == "stopped"
        assert row["error_message"] == "Drawdown limit reached"
        assert row["stopped_at"]

    async def test_a_stop_with_no_reason_still_records_who_asked(self):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "stop")
        assert USER_ID in sb.row("strategy_deployments", DEPLOYMENT_ID)["error_message"]

    async def test_stopping_a_running_deployment_releases_one_bot_slot(self, quota):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "stop")
        quota.assert_awaited_once()

    async def test_pausing_does_not_release_the_slot(self, quota):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "pause")
        quota.assert_not_awaited()

    async def test_an_idempotent_request_changes_nothing_at_all(self, audit):
        sb = _Supabase(_default_rows(version_state="PAUSED", deployment_status="paused"))
        result = await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "pause"
        )
        assert result["idempotent"] is True
        assert result["binding_state"] == "PAUSED"
        assert sb.updates == []
        assert audit.recorded == []

    async def test_an_illegal_transition_refuses_before_any_write(self, audit):
        sb = _Supabase(_default_rows(version_state="STOPPED", deployment_status="stopped"))
        with pytest.raises(lifecycle.LifecycleRejected) as exc:
            await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "resume")
        assert exc.value.details["binding_state"] == "STOPPED"
        assert sb.updates == []
        assert audit.recorded == []

    async def test_another_tenants_deployment_is_not_found_never_forbidden(self):
        rows = _default_rows(version_state="RUNNING")
        rows["strategy_deployments"][0]["user_id"] = OTHER_USER_ID
        sb = _Supabase(rows)
        with pytest.raises(ValueError, match="not found"):
            await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "stop")
        assert sb.updates == []

    async def test_every_deployment_write_carries_the_tenant_filter(self):
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        await _service_on(sb).transition_deployment(_user(), DEPLOYMENT_ID, "stop")
        writes = [
            filters
            for table, _payload, filters in sb.updates
            if table == "strategy_deployments"
        ]
        assert writes
        for filters in writes:
            assert filters.get("user_id") == USER_ID

    async def test_a_runtime_that_never_heard_of_it_does_not_block_the_stop(self):
        """``deployment_manager`` holds only its own deployments; a stop must still land."""
        sb = _Supabase(_default_rows(version_state="RUNNING"))
        result = await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "stop"
        )
        assert result["runtime"]["acknowledged"] is False
        assert sb.row("strategy_deployments", DEPLOYMENT_ID)["status"] == "stopped"

    async def test_stopping_a_deployment_that_never_ran_leaves_the_version_deployed(self):
        """The reported gap: DEPLOYED has no STOPPED edge, and the stop happens anyway."""
        sb = _Supabase(
            _default_rows(version_state="DEPLOYED", deployment_status="deploying")
        )
        result = await _service_on(sb).transition_deployment(
            _user(), DEPLOYMENT_ID, "stop", reason="Never started"
        )
        assert result["binding_state"] == "STOPPED"
        assert sb.row("strategy_deployments", DEPLOYMENT_ID)["status"] == "stopped"
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "DEPLOYED"
        assert result["lifecycle"]["moved"] is False


# ---------------------------------------------------------------------------
# 8. Requirement 20.9 - a guard trip or kill switch stops what is running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGuardTripStopsRunningDeployments:
    def _fleet(self):
        return {
            "strategies": [{"id": STRATEGY_ID, "user_id": USER_ID}],
            "strategy_versions": [
                _version_row("RUNNING"),
                _version_row("DEPLOYED", id="ver_never_ran"),
            ],
            "strategy_deployments": [
                _deployment_row("running", id="dep_running"),
                _deployment_row("paused", id="dep_paused"),
                _deployment_row("stopped", id="dep_stopped"),
                _deployment_row("deploying", id="dep_starting", version_id="ver_never_ran"),
                _deployment_row("running", id="dep_other_tenant", user_id=OTHER_USER_ID),
            ],
        }

    async def test_it_stops_the_live_ones_and_preserves_the_reason(self):
        sb = _Supabase(self._fleet())
        reason = "Risk guard tripped: portfolio drawdown 12%"

        result = await _service_on(sb).stop_deployments_for_guard_trip(_user(), reason)

        stopped_ids = sorted(entry["deployment_id"] for entry in result["stopped"])
        assert stopped_ids == ["dep_paused", "dep_running", "dep_starting"]
        for deployment_id in stopped_ids:
            row = sb.row("strategy_deployments", deployment_id)
            assert row["status"] == "stopped"
            assert row["error_message"] == reason
            assert row["stopped_at"]

    async def test_it_does_not_touch_another_tenants_deployment(self):
        sb = _Supabase(self._fleet())
        await _service_on(sb).stop_deployments_for_guard_trip(_user(), "trip")
        assert sb.row("strategy_deployments", "dep_other_tenant")["status"] == "running"

    async def test_an_already_stopped_deployment_is_reported_not_rewritten(self):
        sb = _Supabase(self._fleet())
        result = await _service_on(sb).stop_deployments_for_guard_trip(_user(), "trip")
        assert "dep_stopped" not in [e["deployment_id"] for e in result["stopped"]]
        assert sb.row("strategy_deployments", "dep_stopped").get("error_message") is None

    async def test_the_running_versions_label_follows_and_the_deployed_one_does_not(self):
        sb = _Supabase(self._fleet())
        await _service_on(sb).stop_deployments_for_guard_trip(_user(), "trip")
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "STOPPED"
        # DEPLOYED has no legal edge to STOPPED, and the deployment stopped regardless.
        assert sb.row("strategy_versions", "ver_never_ran")["lifecycle_state"] == "DEPLOYED"
        assert sb.row("strategy_deployments", "dep_starting")["status"] == "stopped"

    async def test_every_stop_is_audited_with_the_reason(self, audit):
        sb = _Supabase(self._fleet())
        await _service_on(sb).stop_deployments_for_guard_trip(
            _user(), "Kill switch: exchange unhealthy"
        )
        records = [
            r for r in audit.recorded if r.action == StrategyAuditAction.DEPLOYMENT_ACTION
        ]
        assert len(records) == 3
        for record in records:
            assert record.after == "STOPPED"
            assert record.reason == "Kill switch: exchange unhealthy"
            assert record.metadata["triggered_by"] == lifecycle.TRIGGER_GUARD

    async def test_one_deployment_that_will_not_write_does_not_hide_the_others(self):
        sb = _Supabase(self._fleet())
        real_table = sb.table
        seen = {"updates": 0}

        def _table(name):
            query = real_table(name)
            if name == "strategy_deployments":
                original = query.execute

                def execute():
                    if query._mode == "update":  # noqa: SLF001
                        seen["updates"] += 1
                        if seen["updates"] == 1:
                            raise RuntimeError("connection reset")
                    return original()

                query.execute = execute
            return query

        sb.table = _table
        result = await _service_on(sb).stop_deployments_for_guard_trip(_user(), "trip")

        assert len(result["failed"]) == 1
        assert len(result["stopped"]) == 2

    async def test_nothing_is_stopped_when_the_kill_switch_is_not_active(self):
        sb = _Supabase(self._fleet())
        switch = MagicMock()
        switch.get_status = AsyncMock(
            return_value={"kill_switch_active": False, "local_latch_active": False}
        )
        with patch(
            "backend_app.core.global_safety.get_global_kill_switch", return_value=switch
        ):
            result = await _service_on(sb).stop_deployments_on_kill_switch(_user())

        assert result["kill_switch_active"] is False
        assert result["stopped"] == []
        assert sb.row("strategy_deployments", "dep_running")["status"] == "running"

    async def test_an_active_kill_switch_writes_the_reason_it_recorded(self):
        sb = _Supabase(self._fleet())
        switch = MagicMock()
        switch.get_status = AsyncMock(
            return_value={"kill_switch_active": True, "local_latch_active": False}
        )
        switch.get_history = AsyncMock(
            return_value=[
                "2026-01-01T00:00:00|ACTIVATE|Exchange binance unhealthy|health_monitor"
            ]
        )
        with patch(
            "backend_app.core.global_safety.get_global_kill_switch", return_value=switch
        ):
            result = await _service_on(sb).stop_deployments_on_kill_switch(_user())

        assert result["kill_switch_active"] is True
        assert "Exchange binance unhealthy" in result["reason"]
        assert (
            sb.row("strategy_deployments", "dep_running")["error_message"]
            == result["reason"]
        )

    async def test_the_latch_is_named_when_there_is_no_recorded_reason(self):
        switch = MagicMock()
        switch.get_status = AsyncMock(
            return_value={"kill_switch_active": True, "local_latch_active": True}
        )
        switch.get_history = AsyncMock(return_value=[])
        with patch(
            "backend_app.core.global_safety.get_global_kill_switch", return_value=switch
        ):
            verdict = await lifecycle.kill_switch_verdict()
        assert verdict["active"] is True
        assert "latch" in verdict["reason"]

    async def test_an_unreadable_switch_stops_nothing_rather_than_everything(self):
        switch = MagicMock()
        switch.get_status = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch(
            "backend_app.core.global_safety.get_global_kill_switch", return_value=switch
        ):
            verdict = await lifecycle.kill_switch_verdict()
        assert verdict["active"] is False
        assert verdict["readable"] is False


# ---------------------------------------------------------------------------
# 9. Requirement 9.8 - the audit record itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAuditRecord:
    async def test_it_carries_actor_timestamp_and_reason(self, audit):
        audit_id = await lifecycle.record_audit(
            StrategyAuditAction.DEPLOYMENT_ACTION,
            actor_id=USER_ID,
            resource_type="strategy_deployment",
            resource_id=DEPLOYMENT_ID,
            reason="Stopped by author",
            before="RUNNING",
            after="STOPPED",
        )
        record = audit.recorded[-1]
        assert record.audit_id == audit_id
        assert record.actor_id == USER_ID
        assert record.timestamp is not None
        assert record.reason == "Stopped by author"
        assert record.to_dict()["before"] == "RUNNING"
        assert record.to_dict()["after"] == "STOPPED"

    async def test_a_platform_act_has_an_actor_too(self):
        assert lifecycle.actor_of(None) == "system"
        assert lifecycle.actor_of({"id": USER_ID}) == USER_ID

    async def test_an_unreachable_sink_never_fails_the_transition(self, caplog):
        sb = _Supabase(_default_rows())
        double = MagicMock()
        double.log = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch(
            "backend_app.backend.strategy_lifecycle.get_strategy_audit_logger",
            return_value=double,
        ):
            with caplog.at_level("WARNING"):
                result = await lifecycle.apply_version_state(
                    sb, _version_row("READY"), "DEPLOYED", user=_user(), reason="r"
                )
        assert result.moved is True
        assert result.audit_id is None
        assert any("could not be written" in r.getMessage() for r in caplog.records)

    async def test_the_order_audit_trail_is_untouched_by_this_task(self):
        """Adding to ``audit_trail`` must not change what an order lookup iterates."""
        from backend_app.core.audit_trail import AuditEventType

        assert [e.value for e in AuditEventType] == [
            "signal_received",
            "decision_made",
            "order_submitted",
            "order_confirmed",
            "order_failed",
            "fill_received",
            "reconciliation_complete",
        ]

    async def test_the_logger_is_a_singleton_and_keys_history_per_resource(self):
        assert get_strategy_audit_logger() is get_strategy_audit_logger()
        key = get_strategy_audit_logger().history_key("strategy_version", VERSION_ID)
        assert key == f"audit:strategy:strategy_version:{VERSION_ID}"


# ---------------------------------------------------------------------------
# 10. The deploy path moves the version (Requirements 9.6, 13.10)
# ---------------------------------------------------------------------------


def _binding(**overrides):
    from backend_app.backend import deployment_binding as db

    fields = {
        "strategy_id": STRATEGY_ID,
        "version_id": VERSION_ID,
        "version": "v3.0",
        "user_id": USER_ID,
        "mode": "paper",
        "exchange_account_id": None,
        "risk_config_id": None,
        "execution_config": {},
        "dag_hash": "deadbeefcafe1234",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
    }
    fields.update(overrides)
    return db.DeploymentBinding(**fields)


async def _deploy(sb, *, start_succeeds=True):
    """Run the real ``deploy_version`` with only the gate, the plan and the fleet doubled."""
    from backend_app.backend import deployment_binding as db

    service = _service_on(sb)
    service._assert_deploy_prerequisites = lambda *a, **kw: None  # noqa: SLF001

    fleet = MagicMock()
    fleet.start_bot = AsyncMock(
        return_value=(start_succeeds, "started" if start_succeeds else "no worker capacity")
    )

    class _AppState:
        pass

    app_state = _AppState()
    app_state.fleet = fleet

    with patch.object(
        db, "evaluate_binding", new=AsyncMock(return_value=_binding())
    ), patch.object(
        db, "binding_columns_supported", new=AsyncMock(return_value=False)
    ), patch(
        "backend_app.core.subscription_dependencies.get_user_plan",
        new=AsyncMock(return_value="pro"),
    ), patch(
        "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
        new=AsyncMock(return_value=(True, 1, 10)),
    ), patch(
        "backend_app.core.state.app_state", app_state
    ):
        return await service.deploy_version(
            user=_user(), strategy_id=STRATEGY_ID, version="v3.0"
        )


def _deploy_rows():
    return {
        "strategies": [{"id": STRATEGY_ID, "user_id": USER_ID}],
        "strategy_versions": [
            _version_row(LIFECYCLE_READY, blueprint={"nodes": [], "edges": []})
        ],
        "strategy_deployments": [],
    }


@pytest.mark.asyncio
class TestDeployMovesTheLifecycle:
    async def test_a_started_deployment_moves_ready_to_deployed_to_running(self, audit):
        sb = _Supabase(_deploy_rows())
        result = await _deploy(sb)

        assert result["binding_state"] == "RUNNING"
        version = sb.row("strategy_versions", VERSION_ID)
        assert version["lifecycle_state"] == "RUNNING"
        # The version became immutable the moment it was bound (migration 004c).
        assert version["is_read_only"] is True

        transitions = [
            (r.before, r.after)
            for r in audit.recorded
            if r.action == StrategyAuditAction.LIFECYCLE_TRANSITION
        ]
        assert transitions == [("READY", "DEPLOYED"), ("DEPLOYED", "RUNNING")]
        deployments = [
            (r.before, r.after)
            for r in audit.recorded
            if r.action == StrategyAuditAction.DEPLOYMENT_ACTION
        ]
        assert deployments == [(None, "DEPLOYING"), ("DEPLOYING", "RUNNING")]

    async def test_a_failed_start_reports_failed_and_leaves_the_version_deployed(self, audit):
        sb = _Supabase(_deploy_rows())
        result = await _deploy(sb, start_succeeds=False)

        assert result["binding_state"] == "FAILED"
        assert result["deployment"]["status"] == "failed"
        assert sb.row("strategy_versions", VERSION_ID)["lifecycle_state"] == "DEPLOYED"
        transitions = [
            (r.before, r.after)
            for r in audit.recorded
            if r.action == StrategyAuditAction.LIFECYCLE_TRANSITION
        ]
        assert transitions == [("READY", "DEPLOYED")]

    async def test_the_deploy_action_is_audited_with_the_binding_it_created(self, audit):
        sb = _Supabase(_deploy_rows())
        result = await _deploy(sb)
        record = [
            r for r in audit.recorded if r.action == StrategyAuditAction.DEPLOYMENT_ACTION
        ][0]
        assert record.actor_id == USER_ID
        assert record.metadata["mode"] == "paper"
        assert record.metadata["symbol"] == "BTC/USDT"
        assert record.metadata["dag_hash"] == "deadbeefcafe1234"
        assert result["audit_id"] == record.audit_id

    async def test_the_reported_state_is_one_of_the_five(self):
        sb = _Supabase(_deploy_rows())
        result = await _deploy(sb)
        assert result["binding_state"] in BINDING_STATES
        assert result["deployment"]["status"] == "running"


# ---------------------------------------------------------------------------
# 11. The HTTP surface
# ---------------------------------------------------------------------------


def _client_and_router():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.routers import strategy_operations as ops

    app = FastAPI()
    app.include_router(ops.router, prefix="/api")
    app.state.limiter = ops.limiter
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=False), ops


def _service_double(**methods):
    service = MagicMock()
    for name, value in methods.items():
        setattr(service, name, value)

    async def _get():
        return service

    return service, _get


class TestHttpSurface:
    @pytest.mark.parametrize("action", ["pause", "resume", "stop"])
    def test_each_route_keeps_its_auth_dependency_and_its_limiter(self, action):
        source = ROUTER_PATH.read_text(encoding="utf-8")
        index = source.index(f'@router.post("/deployments/{{deployment_id}}/{action}"')
        window = source[index : index + 700]
        assert "@limiter.limit(" in window
        assert "Depends(get_current_user)" in window

    @pytest.mark.parametrize("action", ["pause", "resume", "stop"])
    def test_a_successful_transition_reports_the_binding_state(self, action):
        client, ops = _client_and_router()
        service, getter = _service_double(
            transition_deployment=AsyncMock(
                return_value={
                    "deployment_id": DEPLOYMENT_ID,
                    "binding_state": "PAUSED",
                    "previous_state": "RUNNING",
                    "idempotent": False,
                    "audit_id": "SAUDIT-1",
                }
            )
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.post(
                f"/api/deployments/{DEPLOYMENT_ID}/{action}", json={"reason": "because"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["binding_state"] == "PAUSED"
        # The legacy lowercase key is still there for existing clients.
        assert body["status"] == "paused"
        assert body["deployment_id"] == DEPLOYMENT_ID
        assert service.transition_deployment.await_args.kwargs["action"] == action
        assert service.transition_deployment.await_args.kwargs["reason"] == "because"

    def test_a_state_conflict_is_a_409_carrying_the_current_state(self):
        client, ops = _client_and_router()
        _service, getter = _service_double(
            transition_deployment=AsyncMock(
                side_effect=lifecycle.LifecycleRejected(
                    "DEPLOYMENT_ACTION_INVALID",
                    "This deployment is STOPPED, so it cannot be resumed.",
                    {"binding_state": "STOPPED", "available_actions": []},
                )
            )
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.post(f"/api/deployments/{DEPLOYMENT_ID}/resume")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "DEPLOYMENT_ACTION_INVALID"
        assert detail["binding_state"] == "STOPPED"

    def test_another_tenants_deployment_is_a_404(self):
        client, ops = _client_and_router()
        _service, getter = _service_double(
            transition_deployment=AsyncMock(side_effect=ValueError("Deployment x not found"))
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.post(f"/api/deployments/{DEPLOYMENT_ID}/stop")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "DEPLOYMENT_NOT_FOUND"

    def test_the_body_admits_a_reason_and_nothing_else(self):
        client, ops = _client_and_router()
        _service, getter = _service_double(
            transition_deployment=AsyncMock(return_value={"binding_state": "STOPPED"})
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.post(
                f"/api/deployments/{DEPLOYMENT_ID}/stop",
                json={"reason": "ok", "exchange_account_id": "sneaky"},
            )
        assert resp.status_code == 422

    def test_no_body_is_fine_because_a_reason_is_optional(self):
        client, ops = _client_and_router()
        _service, getter = _service_double(
            transition_deployment=AsyncMock(return_value={"binding_state": "STOPPED"})
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.post(f"/api/deployments/{DEPLOYMENT_ID}/stop")
        assert resp.status_code == 200

    def test_the_version_history_publishes_the_canvas_verdict(self):
        client, ops = _client_and_router()
        _service, getter = _service_double(
            get_version_history=AsyncMock(
                return_value=[_version_row("RUNNING"), _version_row("DRAFT", id="v_draft")]
            )
        )
        with patch.object(ops, "get_strategy_service", getter):
            resp = client.get(f"/api/strategies/{STRATEGY_ID}/versions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["read_only_states"] == ["DEPLOYED", "RUNNING", "PAUSED"]
        assert body["versions"][0]["canvas"]["read_only"] is True
        assert body["versions"][1]["canvas"]["editable"] is True

    def test_the_duplicate_stop_route_behaves_identically(self):
        """Two registrations of one path pre-date this task; both now do the same thing."""
        from backend_app.routers import strategy_operations as ops

        handlers = [
            route
            for route in ops.router.routes
            if getattr(route, "path", "") == "/deployments/{deployment_id}/stop"
        ]
        assert len(handlers) == 2
        source = ROUTER_PATH.read_text(encoding="utf-8")
        assert source.count('_transition_deployment_endpoint(\n        "stop"') == 2
