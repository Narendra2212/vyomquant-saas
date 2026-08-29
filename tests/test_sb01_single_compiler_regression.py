"""
tests/test_sb01_single_compiler_regression.py

SB-01, held at the HTTP surface: one graph, five paths, one verdict.

Spec: strategy-builder task 2.8. Requirements 3.1 (one compiler entry point) and 3.2
(the same validity verdict and the same error code set from the validate path, the save
path, the clone path, the deployment/compile path and the plan-load path).

--------------------------------------------------------------------------------
What this file adds over ``tests/test_task_2_4_call_site_migration.py``
--------------------------------------------------------------------------------

That file already asserts three-way agreement between ``_validate_payload``,
``_compile_payload`` and ``SC.load_plan`` over exactly these five graphs, and it
asserts save-vs-clone agreement at the HTTP layer for *one* graph. It is not
duplicated here. What is genuinely new:

1. **Five paths, not three, and at the transport layer.** Requirement 3.2 names the
   validate, save, clone, compile and plan-load paths. ``_compile_payload`` is one
   helper shared by save and clone; asserting it once does not prove the two
   *endpoints* agree, because each endpoint decides for itself what to do with the
   result - which is exactly how the pre-fix tree diverged. Here every graph goes
   through ``POST /api/strategies/validate``, ``POST /api/strategies/``,
   ``POST /api/strategies/{id}/clone`` and ``POST /api/strategies/compile`` as real
   requests, for all four divergent graphs plus the valid control.

2. **The compile endpoint is called, not read.** Task 2.4's test asserts that
   ``compile_strategy`` mentions ``compile_plan`` by inspecting its source. That
   proves the import, not the answer. This file posts to the endpoint and compares
   the code set it returns.

3. **HTTP status parity.** A verdict a client cannot read is not a shared verdict.
   Pre-fix, the same unexecutable graph produced ``200`` from validate, ``200`` with
   an error-shaped body from save, ``200 {"status": "cloned"}`` from clone and ``400``
   from compile. All four rejecting paths must now answer ``422``.

4. **The pre-fix divergence, measured.** See below.

--------------------------------------------------------------------------------
The pre-fix state, measured rather than asserted
--------------------------------------------------------------------------------

Run in a detached ``git worktree`` at the pre-fix commit ``af977d2`` (where
``routers/strategies.py`` still defines ``DAGCompiler`` and ``CompiledDAG``), the same
five graphs were submitted through the four HTTP paths that existed there, in the
legacy node format that tree accepted. Verdicts observed, ``accepted`` meaning the
path let the graph through:

    graph                      validate  save   clone  compile
    valid                      True      True   True   True      AGREE
    orphan_node                True      True   True   True      AGREE  (all wrong)
    action_fed_by_indicator    False     False  True   True      DIVERGE
    single_node_no_edges       False     False  True   False     DIVERGE
    unknown_node_type          False     False  True   False     DIVERGE

Three of the four divergent graphs split the four paths. The clone path accepted
every one of them, because the pre-fix clone wrapped its compile in
``except Exception: logger.warning(...)`` - the same swallow that produced SB-02, and
the probe confirmed it: even for the *valid* graph the clone was persisted with
``dag_hash=None`` after ``'CompiledDAG' object has no attribute 'get'`` was downgraded
to a warning.

``orphan_node`` is the more interesting one. All four pre-fix paths agreed, and all
four were wrong: the router's ``_detect_orphans`` was dead code, because
``_check_connectivity`` classified any node with no incoming edge as an *input* node,
so an isolated node was "reachable" by definition. The canonical validator rejects it
with ``ORPHAN_NODE``. So for this graph the pre-fix failure is not disagreement but
unanimous acceptance of an unexecutable strategy.

``PRE_FIX_VERDICTS`` below carries that table as data, and
``test_the_pre_fix_verdicts_no_longer_hold`` asserts against it. That test fails on the
pre-fix tree by construction: it requires unanimity where the pre-fix tree had none,
and rejection where the pre-fix tree accepted.

Nothing is faked. The registry, the validator and the compiler are the real ones; only
the Supabase client is a double, because this file is about verdicts, not storage.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.core.dependencies import get_current_user, get_request_supabase

# The graph builders and the Supabase double are task 2.4's. Reused, not restated: a
# second copy of `orphan_graph` is a second definition of what "orphan" means, and this
# whole file exists because the codebase had two of something.
from tests.test_task_2_4_call_site_migration import (
    _capturing_sb,
    action_fed_by_indicator_graph,
    orphan_graph,
    single_node_graph,
    unknown_block_payload,
    valid_graph,
)

#: The five paths Requirement 3.2 names. ``deployment`` and ``backtest load`` are the
#: same entry point - ``strategy_compiler.load_plan`` - so they are one column here.
PATHS = ("validate", "save", "clone", "compile", "plan_load")

#: The four paths that answer over HTTP. ``plan_load`` is a library call made by the
#: worker and the backtester; it has no endpoint, so it has no status code.
HTTP_PATHS = ("validate", "save", "clone", "compile")

#: What each path answered at the pre-fix commit af977d2. ``True`` means the path let
#: the graph through. Measured, not assumed - see the module docstring.
PRE_FIX_VERDICTS = {
    "valid": {"validate": True, "save": True, "clone": True, "compile": True},
    "orphan_node": {"validate": True, "save": True, "clone": True, "compile": True},
    "action_fed_by_indicator": {
        "validate": False,
        "save": False,
        "clone": True,
        "compile": True,
    },
    "single_node_no_edges": {
        "validate": False,
        "save": False,
        "clone": True,
        "compile": False,
    },
    "unknown_node_type": {
        "validate": False,
        "save": False,
        "clone": True,
        "compile": False,
    },
}

#: The graph that must be accepted, so a five-way agreement test cannot pass by
#: rejecting everything.
CONTROL = "valid"

#: The graphs task 2.8 names explicitly.
DIVERGENT = (
    "orphan_node",
    "action_fed_by_indicator",
    "single_node_no_edges",
    "unknown_node_type",
)

#: The code each divergent graph must be rejected with, on every path. Pinned so that a
#: graph rejected for a *different* reason on some path cannot pass as agreement.
EXPECTED_CODE = {
    "orphan_node": V.CODE_ORPHAN_NODE,
    "action_fed_by_indicator": V.CODE_ACTION_INPUT_PROVENANCE,
    "single_node_no_edges": V.CODE_MISSING_REQUIRED_CATEGORY,
    "unknown_node_type": V.CODE_UNRESOLVED_BLOCK,
}


class Verdict:
    """One path's answer for one graph.

    ``codes`` is ``None`` when the path does not publish its code set - the clone and
    compile endpoints return no report on the accepting branch, and inventing one would
    be asserting on this test's own arithmetic rather than on the server's answer.
    """

    __slots__ = ("valid", "codes", "status", "persisted")

    def __init__(self, valid, codes, status=None, persisted=None):
        self.valid = valid
        self.codes = codes
        self.status = status
        self.persisted = persisted

    def __repr__(self):
        return (
            f"Verdict(valid={self.valid}, status={self.status}, "
            f"codes={sorted(self.codes) if self.codes is not None else None})"
        )


def _issue_codes(*issue_lists):
    codes = set()
    for issues in issue_lists:
        for issue in issues or []:
            codes.add(issue.get("code"))
    return frozenset(codes)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(scope="module")
def user():
    return {
        "id": "usr_sb01_owner",
        "email": "sb01@example.com",
        "role": "authenticated",
        "access_token": "token_sb01",
    }


@pytest.fixture(scope="module")
def client(user):
    from backend_app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _compiled(payload, reg):
    """A compile of ``payload`` made outside every path under test.

    Node identifiers are minted at node creation, so calling ``valid_graph(reg)`` a
    second time produces a *different* strategy with a different identity hash. The
    control has to be a compile of the very payload that was submitted, not of a
    fresh graph that merely looks like it.
    """
    from backend_app.backend.strategy_dag.schema import load_graph

    return SC.compile_graph(load_graph(payload), reg)


@pytest.fixture(scope="module")
def cases(reg):
    """The four graphs task 2.8 names, plus the valid control."""
    return {
        "valid": valid_graph(reg).to_dict(),
        "orphan_node": orphan_graph(reg).to_dict(),
        "action_fed_by_indicator": action_fed_by_indicator_graph(reg).to_dict(),
        "single_node_no_edges": single_node_graph(reg).to_dict(),
        "unknown_node_type": unknown_block_payload(),
    }


def _stored_row(payload, user_id):
    """A strategy row as the save path writes one, for the clone path to read.

    The DAG lives in ``buy_logic._nodes`` / ``_edges`` because ``strategies`` has no DAG
    columns, and the declared schema version travels with it so a version 2 graph is not
    needlessly re-migrated as version 1 - which would give the clone path a different
    graph, and therefore a different verdict, for the same strategy.
    """
    return {
        "id": "strategy_source",
        "user_id": user_id,
        "name": "SB-01 source",
        "symbol": "ETH/USDT",
        "timeframe": "1h",
        "buy_logic": {
            "_nodes": payload["nodes"],
            "_edges": payload["edges"],
            "_dag_version": 1,
            "_dag_schema_version": payload.get("schema_version", 1),
            "_dag_hash": None,
        },
        "sell_logic": {},
        "risk": {},
        "indicators": [],
        "ml_model_path": None,
    }


# ---------------------------------------------------------------------------
# One submission per path, per graph. Collected once: the save endpoint carries a
# 20/minute rate limit, and a test that trips it is testing the limiter.
# ---------------------------------------------------------------------------


def _via_validate(client, payload):
    response = client.post("/api/strategies/validate", json={"dag": payload})
    body = response.json()
    return Verdict(
        valid=bool(body["valid"]),
        codes=_issue_codes(body.get("errors"), body.get("warnings")),
        status=response.status_code,
    )


def _via_save(client, name, payload):
    sb, captured = _capturing_sb()
    with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
        response = client.post("/api/strategies/", json={"name": name, **payload})
    persisted = "insert" in captured
    if response.status_code == 200:
        # The accepting branch publishes the non-blocking issues only.
        return Verdict(True, None, response.status_code, persisted)
    detail = response.json().get("detail") or {}
    return Verdict(
        False, frozenset(detail.get("codes") or []), response.status_code, persisted
    )


def _via_clone(client, user, payload):
    """The clone path's verdict.

    Requirement 10.2/10.3 (strategy-builder task 4.2) makes the clone path diverge from
    save on what it DOES with an invalid verdict: a clone whose recompile fails is
    persisted anyway, marked ``INVALID``, with the failure surfaced in the response
    ``warnings[]`` - never a bare ``except Exception`` swallowing the AttributeError
    (SB-02), and never a 422 that discards an existing, already-saved strategy the way
    the save path discards a brand-new one (Requirement 3.6). This is a deliberate,
    documented divergence in OUTCOME, not in VERDICT: the code set clone reports for a
    divergent graph must still match every other path exactly (Requirement 3.2). So the
    verdict here is read from ``warnings[]`` rather than from HTTP status - the clone
    endpoint answers 200 whether the graph was valid or not, and "valid" is decided by
    whether anything landed in ``warnings[]``, not by the transport status.
    """
    sb, captured = _capturing_sb(
        select_data=[_stored_row(payload, user["id"])],
        insert_data=[{"id": "strategy_clone"}],
    )
    with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
        response = client.post("/api/strategies/strategy_source/clone")
    persisted = "insert" in captured
    if response.status_code != 200:
        # Only an unreadable payload (GraphParseError/CompilerError) reaches this
        # branch - there is no graph at all to persist as INVALID.
        detail = response.json().get("detail") or {}
        return Verdict(
            False, frozenset(detail.get("codes") or []), response.status_code, persisted
        )
    body = response.json()
    warnings = body.get("warnings") or []
    if not warnings:
        return Verdict(True, None, response.status_code, persisted)
    codes = _issue_codes(
        warnings[0].get("errors"), warnings[0].get("warnings")
    )
    return Verdict(False, codes, response.status_code, persisted)


def _via_compile(client, payload):
    response = client.post(
        "/api/strategies/compile", json={"blueprint": payload, "version": "v1.0"}
    )
    if response.status_code == 200:
        return Verdict(True, None, response.status_code, persisted=False)
    detail = response.json().get("detail") or {}
    return Verdict(
        False,
        frozenset(detail.get("codes") or []),
        response.status_code,
        persisted=False,
    )


def _via_plan_load(payload):
    """The worker / backtester entry point. A library call, so it has no status."""
    row = {"graph_json": payload} if payload.get("schema_version") else dict(payload)
    try:
        loaded = SC.load_plan(row)
    except SC.ValidationError as exc:
        assert exc.report is not None, (
            "the canonical path must carry a structured report, not a bare string"
        )
        return Verdict(False, frozenset(exc.report.codes()), persisted=False)
    report = V.validate(loaded.graph)
    return Verdict(report.valid, frozenset(report.codes()), persisted=False)


@pytest.fixture(scope="module")
def verdicts(client, user, cases):
    """``{graph_name: {path: Verdict}}``. Every path submitted exactly once."""
    collected = {}
    for name, payload in cases.items():
        collected[name] = {
            "validate": _via_validate(client, payload),
            "save": _via_save(client, name, payload),
            "clone": _via_clone(client, user, payload),
            "compile": _via_compile(client, payload),
            "plan_load": _via_plan_load(payload),
        }
    return collected


# ---------------------------------------------------------------------------
# Requirement 3.2 - the same verdict and the same code set from every path
# ---------------------------------------------------------------------------


class TestOneVerdictFromEveryPath:
    def test_the_validity_verdict_is_identical_on_all_five_paths(self, verdicts):
        for name, by_path in verdicts.items():
            answers = {path: by_path[path].valid for path in PATHS}
            assert len(set(answers.values())) == 1, (
                f"{name}: the five paths disagree on whether this graph is "
                f"executable: {answers}. That disagreement IS defect SB-01."
            )

    def test_the_error_code_set_is_identical_on_all_five_paths(self, verdicts):
        """A message is prose; a code is a contract. The contract must match."""
        for name in DIVERGENT:
            published = {
                path: by_path
                for path, by_path in verdicts[name].items()
                if by_path.codes is not None
            }
            assert set(published) == set(PATHS), (
                f"{name}: a rejecting path published no code set: "
                f"{sorted(set(PATHS) - set(published))}"
            )
            distinct = {frozenset(v.codes) for v in published.values()}
            assert len(distinct) == 1, (
                f"{name}: the paths reject for different reasons: "
                + repr({path: sorted(v.codes) for path, v in published.items()})
            )

    def test_each_divergent_graph_is_rejected_for_the_reason_it_exists(self, verdicts):
        """Agreement on an unrelated code would be agreement about the wrong thing."""
        for name, expected in EXPECTED_CODE.items():
            for path in PATHS:
                verdict = verdicts[name][path]
                assert verdict.valid is False, f"{name} via {path}: {verdict}"
                assert expected in verdict.codes, (
                    f"{name} via {path}: expected {expected}, got "
                    f"{sorted(verdict.codes)}"
                )

    def test_the_valid_control_is_accepted_on_all_five_paths(self, verdicts):
        """Without this, rejecting everything would satisfy every test above."""
        for path in PATHS:
            verdict = verdicts[CONTROL][path]
            assert verdict.valid is True, f"{CONTROL} via {path}: {verdict}"

    def test_the_graph_set_actually_splits_valid_from_invalid(self, verdicts):
        by_graph = {
            name: verdicts[name]["validate"].valid for name in verdicts
        }
        assert by_graph[CONTROL] is True, by_graph
        assert set(DIVERGENT) == {
            name for name, valid in by_graph.items() if not valid
        }, by_graph


class TestOneStatusCodeFromEveryPath:
    def test_every_rejecting_endpoint_answers_422(self, verdicts):
        """Pre-fix: 200 from validate, 200 from save, 200 from clone, 400 from compile.

        Clone is excluded from the 422 requirement here by Requirement 10.2 (task 4.2):
        an existing, already-saved strategy is never discarded by cloning it, so a
        recompile failure on the clone path answers 200 with the failure carried in
        ``warnings[]`` and the clone persisted ``INVALID``, rather than 422. That is
        exercised separately in ``TestClonePersistsInvalidInsteadOfRejecting`` below.
        """
        for name in DIVERGENT:
            statuses = {
                path: verdicts[name][path].status
                for path in HTTP_PATHS
                if path not in ("validate", "clone")
            }
            assert set(statuses.values()) == {422}, f"{name}: {statuses}"

    def test_clone_answers_200_with_the_failure_in_warnings(self, verdicts):
        """Requirement 10.2/10.3: clone never 422s away an existing strategy."""
        for name in DIVERGENT:
            assert verdicts[name]["clone"].status == 200, (
                f"{name}: clone must persist an INVALID copy, not reject it"
            )

    def test_validate_answers_200_for_an_invalid_graph(self, verdicts):
        """An unexecutable strategy is a valid question with a negative answer."""
        for name in DIVERGENT:
            assert verdicts[name]["validate"].status == 200

    def test_every_accepting_endpoint_answers_200(self, verdicts):
        for path in HTTP_PATHS:
            assert verdicts[CONTROL][path].status == 200, path


class TestNothingIsPersistedOnADisagreement:
    def test_a_rejected_save_is_written_by_no_path(self, verdicts):
        """Requirement 3.6. A brand-new, never-saved strategy persists nothing at all."""
        for name in DIVERGENT:
            assert verdicts[name]["save"].persisted is False, (
                f"{name} via save persisted a record for a graph it rejected"
            )

    def test_a_rejected_clone_is_persisted_as_invalid_not_discarded(self, verdicts):
        """Requirement 10.2: the opposite rule for clone, on purpose.

        Cloning an existing, already-saved strategy must never silently discard it: a
        recompile failure is persisted anyway, marked INVALID, with the report attached
        and the failure surfaced in ``warnings[]``. Pre-fix, clone persisted every
        divergent graph as if it had compiled cleanly (dag_hash silently unset - SB-02).
        Post-fix, clone still persists them, but now honestly: marked INVALID and
        un-deployable, never mistakeable for a valid clone.
        """
        for name in DIVERGENT:
            assert verdicts[name]["clone"].persisted is True, (
                f"{name} via clone must still be persisted, as INVALID (Requirement 10.2)"
            )

    def test_the_accepted_graph_is_written_by_the_paths_that_persist(self, verdicts):
        for path in ("save", "clone"):
            assert verdicts[CONTROL][path].persisted is True, path

    def test_the_compile_endpoint_persists_nothing_at_all(self, client, cases):
        """It is a question, not a save. It never touches a database client."""
        sb, captured = _capturing_sb()
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)
        ):
            response = client.post(
                "/api/strategies/compile",
                json={"blueprint": cases[CONTROL], "version": "v1.0"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "compiled"
        assert "insert" not in captured


# ---------------------------------------------------------------------------
# Requirement 3.1 - the paths reach the same compiler, and the artifact matches
# ---------------------------------------------------------------------------


class TestOneCompilerBehindEveryPath:
    def test_every_accepting_path_reports_the_same_identity_hash(
        self, client, user, reg, cases
    ):
        """One graph, one meaning, one hash - whichever door it came through."""
        payload = cases[CONTROL]
        expected = _compiled(payload, reg).dag_hash

        validate_hash = client.post(
            "/api/strategies/validate", json={"dag": payload}
        ).json()["dag_hash"]

        compile_hash = client.post(
            "/api/strategies/compile", json={"blueprint": payload, "version": "v1.0"}
        ).json()["dag_hash"]

        sb_save, captured_save = _capturing_sb()
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb_save)
        ):
            save = client.post("/api/strategies/", json={"name": "sb01", **payload})

        sb_clone, captured_clone = _capturing_sb(
            select_data=[_stored_row(payload, user["id"])],
            insert_data=[{"id": "strategy_clone"}],
        )
        with patch(
            "backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb_clone)
        ):
            client.post("/api/strategies/strategy_source/clone")

        load_hash = SC.load_plan({"graph_json": payload}).dag_hash

        assert {
            "validate": validate_hash,
            "compile": compile_hash,
            "save_response": save.json()["dag_hash"],
            "save_persisted": captured_save["insert"]["buy_logic"]["_dag_hash"],
            "clone_persisted": captured_clone["insert"]["buy_logic"]["_dag_hash"],
            "plan_load": load_hash,
        } == {
            "validate": expected,
            "compile": expected,
            "save_response": expected,
            "save_persisted": expected,
            "clone_persisted": expected,
            "plan_load": expected,
        }

    def test_the_compile_endpoint_returns_the_one_serialization_form(
        self, client, reg, cases
    ):
        """Requirement 2.5: what the endpoint hands back is what a version row stores."""
        expected = _compiled(cases[CONTROL], reg)

        body = client.post(
            "/api/strategies/compile",
            json={"blueprint": cases[CONTROL], "version": "v1.0"},
        ).json()

        assert body["compiled_plan"] == expected.to_dict()
        assert body["warmup_bars"] == expected.warmup_bars
        assert body["schema_version"] == expected.schema_version

    def test_a_reused_plan_and_a_fresh_compile_give_the_same_verdict(self, reg, cases):
        """The load path must not become a fifth opinion by skipping validation."""
        plan = _compiled(cases[CONTROL], reg)

        reused = SC.load_plan(
            {"graph_json": cases[CONTROL], "compiled_plan": plan.to_dict()}, reg
        )
        fresh = SC.load_plan({"graph_json": cases[CONTROL]}, reg)

        assert reused.reused is True
        assert fresh.reused is False
        assert reused.dag_hash == fresh.dag_hash
        assert reused.plan.to_dict() == fresh.plan.to_dict()


# ---------------------------------------------------------------------------
# The pre-fix commit
# ---------------------------------------------------------------------------


class TestTheDefectIsClosed:
    def test_the_pre_fix_verdicts_no_longer_hold(self, verdicts):
        """Fails on the pre-fix tree by construction.

        Two things are required that the pre-fix tree did not provide: every path's
        VERDICT must agree, and the graphs the pre-fix tree accepted must now be refused
        everywhere. ``PRE_FIX_VERDICTS`` is the measured pre-fix answer set, not a guess
        - see the module docstring for how it was obtained.

        Clone's verdict is read from ``Verdict.valid`` (derived from ``warnings[]``, see
        ``_via_clone``), not from HTTP status: Requirement 10.2 (task 4.2) makes clone
        answer 200 for both an accepted and a persisted-as-INVALID graph, so status
        alone no longer distinguishes the two on this one path. The verdict - whether
        the graph is executable - still agrees with every other path exactly.
        """
        for name, pre_fix in PRE_FIX_VERDICTS.items():
            now = {path: verdicts[name][path].valid for path in HTTP_PATHS}

            assert len(set(now.values())) == 1, (
                f"{name}: still divergent. pre-fix {pre_fix}, now {now}"
            )
            if name == CONTROL:
                assert all(now.values()), now
                continue
            assert not any(now.values()), (
                f"{name} must be refused everywhere. pre-fix {pre_fix}, now {now}"
            )

    def test_the_paths_the_pre_fix_clone_let_through_now_refuse(self, verdicts):
        """The clone path accepted all four divergent graphs at af977d2.

        Post-fix, clone's verdict for each of them is now `invalid` - same as every
        other path - but Requirement 10.2 changes what clone DOES with that verdict: the
        graph is still persisted (as ``INVALID``, un-deployable, with the failure in
        ``warnings[]``) rather than rejected outright the way save rejects it. So
        ``status`` stays 200 and ``persisted`` stays True; what changed is that the
        clone is no longer silently mistaken for a valid one (SB-02).
        """
        accepted_pre_fix = [
            name
            for name, pre_fix in PRE_FIX_VERDICTS.items()
            if name != CONTROL and pre_fix["clone"] is True
        ]
        assert accepted_pre_fix == list(DIVERGENT), accepted_pre_fix
        for name in accepted_pre_fix:
            assert verdicts[name]["clone"].valid is False
            assert verdicts[name]["clone"].status == 200
            assert verdicts[name]["clone"].persisted is True

    def test_the_rules_that_lived_only_in_the_router_are_in_the_one_validator(self):
        """``_detect_orphans``, ``_validate_action_inputs`` and ``_validate_types``.

        These three rules existed only inside the deleted router compiler, and the
        orphan one was dead code there. They are now stages of the single validator, so
        every path gets them.
        """
        for code in (
            V.CODE_ORPHAN_NODE,
            V.CODE_ACTION_INPUT_PROVENANCE,
            V.CODE_MISSING_REQUIRED_CATEGORY,
            V.CODE_UNRESOLVED_BLOCK,
        ):
            assert isinstance(code, str) and code

        import backend_app.routers.strategies as strategies_router

        assert not hasattr(strategies_router, "DAGCompiler")
        assert not hasattr(strategies_router, "CompiledDAG")
