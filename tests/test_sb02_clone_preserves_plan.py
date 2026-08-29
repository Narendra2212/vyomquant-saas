"""
tests/test_sb02_clone_preserves_plan.py

The SB-02 regression test (strategy-builder task 4.4).

WHAT SB-02 WAS
--------------
``POST /api/strategies/{id}/clone`` used to recompile the cloned graph like this::

    compiled = DAGCompiler.compile(nodes, edges)          # returns a CompiledDAG OBJECT
    try:
        cloned_payload["dag_hash"] = compiled.get("dag_hash")        # objects have no .get
        cloned_payload["execution_order"] = compiled.get("execution_order")
    except Exception as e:
        logger.warning(...)                               # swallowed the AttributeError

``CompiledDAG`` exposed ``compute_hash()`` and ``.execution_order`` - it never had a
``.get``. So *every* clone raised ``AttributeError`` on the first line of the try block,
had it downgraded to a log warning by the broad ``except Exception``, and was persisted
with no identity hash and no execution order at all - while the comment directly above
claimed both were recomputed. The caller was told ``{"status": "cloned"}``.

WHY THE PERSISTED PAYLOAD IS WHAT THESE TESTS ASSERT ON
-------------------------------------------------------
The defect was invisible in the *response*: the endpoint returned 200 and echoed the row
the database handed back. It was only visible in what got **written**. So these tests
capture the payload actually passed to ``insert()`` rather than reading the mocked
response, because a mocked response echoes whatever the test told it to echo and would
have passed against the pre-fix code.

WHAT THIS FILE HOLDS IN PLACE (Requirements 10.1, 10.2, 10.3)
-------------------------------------------------------------
* 10.1  A clone whose graph compiles is persisted with a present ``dag_hash`` AND a
        present ``compiled_plan``, and the hash is the real hash of that graph rather
        than a placeholder.
* 10.2  A clone whose graph does NOT compile is persisted anyway, marked
        ``INVALID`` with the structured report attached, with ``dag_hash`` and
        ``compiled_plan`` explicitly null - and the failure returned in the response
        ``warnings[]``.
* 10.3  Clone-time compilation failure reaches the caller through a *typed* error path,
        not a bare ``except Exception``.

There is no third outcome: a clone is either fully compiled or explicitly INVALID
(design.md Correctness Property 7). The INVALID outcome is also asserted to be
un-deployable, which is what makes persisting it safe - it ties 10.2 to the Phase 4
deploy gate (Requirement 10.4) so "persisted but hashless" can never reach an exchange.

FAILS AGAINST THE PRE-FIX COMMIT
--------------------------------
``test_valid_clone_persists_dag_hash``, ``test_valid_clone_persists_compiled_plan`` and
``test_valid_clone_persists_the_real_graph_hash`` all assert on a value the pre-fix path
never wrote. ``test_compiled_plan_has_no_get_method`` and
``test_router_module_never_calls_get_dag_hash`` pin the *mechanism* rather than the
symptom, so the defect cannot return in a new location.

No infrastructure required: Supabase is mocked, so this file runs in the default lane.
"""

import ast
import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ── graph fixtures ─────────────────────────────────────────────────────────
# Built through the canonical schema and the real registry, never hand-written
# blobs: a hand-written blob only ever "passed" because the pre-fix router
# swallowed the failure, which is the defect itself.

def _build_valid_graph():
    """DATA -> RSI -> (RSI > 30) -> BUY. Compiles cleanly."""
    from backend_app.backend.strategy_dag import registry as registry_module
    from backend_app.backend.strategy_dag.schema import (
        EdgeSpec,
        NodeSpec,
        StrategyGraph,
    )

    reg = registry_module.get_registry()

    def node(block_id, **params):
        return NodeSpec.create(block_id, reg[block_id].category, params=params)

    data = node(
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="1h",
        market_type="spot",
        mode="streaming",
    )
    rsi = node("rsi", window=14, source="close")
    threshold = node("constant", value=30.0)
    gt = node("gt")
    buy = node("action_buy_market", quantity_type="percent_of_equity", quantity=0.25)

    return StrategyGraph(
        nodes=[data, rsi, threshold, gt, buy],
        edges=[
            EdgeSpec.create(data.id, "close", rsi.id, "series"),
            EdgeSpec.create(rsi.id, "value", gt.id, "left"),
            EdgeSpec.create(threshold.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", buy.id, "signal"),
        ],
    )


def _build_invalid_graph():
    """DATA -> RSI and nothing else: a readable graph with no ACTION node.

    Deliberately invalid at the *validation* stage, not the parse stage. A graph that
    cannot be parsed at all is a different outcome (422, refused outright, because there
    is nothing to persist as an invalid-but-real DAG); this one parses fine and then
    fails stage 8, which is the case Requirement 10.2 governs.
    """
    from backend_app.backend.strategy_dag import registry as registry_module
    from backend_app.backend.strategy_dag.schema import (
        EdgeSpec,
        NodeSpec,
        StrategyGraph,
    )

    reg = registry_module.get_registry()

    def node(block_id, **params):
        return NodeSpec.create(block_id, reg[block_id].category, params=params)

    data = node(
        "ohlcv_feed",
        symbol="ETH/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    rsi = node("rsi", window=14, source="close")

    return StrategyGraph(
        nodes=[data, rsi],
        edges=[EdgeSpec.create(data.id, "close", rsi.id, "series")],
    )


def _source_row(graph, strategy_id="sb02_source", owner="sb02_user"):
    """A stored ``strategies`` row carrying ``graph`` in the legacy ``buy_logic`` blob.

    ``strategies`` has no dedicated DAG columns, so the router has always stashed them
    inside ``buy_logic`` as ``_nodes`` / ``_edges`` / ``_dag_*``. ``_dag_schema_version``
    is set so the row is parsed as version 2 rather than needlessly re-migrated as
    version 1 - a version 1 re-migration would give the clone path a different verdict
    from the save path for the same graph, i.e. SB-01 by another route.
    """
    payload = graph.to_dict()
    return {
        "id": strategy_id,
        "user_id": owner,
        "name": "SB-02 Source",
        "symbol": "",
        "timeframe": "",
        "buy_logic": {
            "_nodes": payload["nodes"],
            "_edges": payload["edges"],
            "_dag_schema_version": payload["schema_version"],
        },
        "sell_logic": None,
        "risk": {"risk_per_trade": 0.02},
        "indicators": ["RSI"],
        "ml_model_path": None,
    }


# ── harness ────────────────────────────────────────────────────────────────

class _CloneHarness:
    """Captures the payload the clone endpoint actually writes."""

    def __init__(self, sb_client):
        self.sb = sb_client
        self.inserted_payload = None

    def set_source_row(self, row):
        self.sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[row])
        )

    @property
    def cloned_buy_logic(self):
        assert self.inserted_payload is not None, "clone endpoint wrote nothing"
        return self.inserted_payload["buy_logic"]


@pytest.fixture
def harness():
    with patch(
        "backend_app.routers.strategies._sb", new_callable=AsyncMock
    ) as mock_sb:
        sb_client = MagicMock()
        state = _CloneHarness(sb_client)

        def _insert(payload):
            # Record what would be persisted, then echo it back the way the database
            # would. The recorded payload - not the echo - is what the assertions read.
            state.inserted_payload = payload
            stored = dict(payload)
            stored["id"] = "sb02_clone"
            insert_obj = MagicMock()
            insert_obj.execute = AsyncMock(return_value=MagicMock(data=[stored]))
            return insert_obj

        sb_client.table.return_value.insert.side_effect = _insert
        mock_sb.return_value = sb_client
        yield state


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    return TestClient(app)


@pytest.fixture
def auth_headers():
    import time

    import jwt

    secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
    token = jwt.encode(
        {
            "sub": "sb02_user",
            "email": "sb02@example.com",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _clone(client, headers, strategy_id="sb02_source"):
    return client.post(f"/api/strategies/{strategy_id}/clone", headers=headers)


# ── Requirement 10.1: a compiling clone carries its compiled state ─────────

class TestValidCloneCarriesCompiledState:
    """Requirement 10.1 - the assertions the pre-fix path could not satisfy."""

    def test_valid_clone_persists_dag_hash(self, client, auth_headers, harness):
        """The line SB-02 broke. Pre-fix this key was never written at all."""
        harness.set_source_row(_source_row(_build_valid_graph()))

        response = _clone(client, auth_headers)
        assert response.status_code == 200

        dag_hash = harness.cloned_buy_logic.get("_dag_hash")
        assert dag_hash is not None, (
            "SB-02 regression: the clone was persisted with no dag_hash. "
            "This is exactly the state the pre-fix `compiled.get('dag_hash')` "
            "AttributeError left behind after being swallowed."
        )
        assert isinstance(dag_hash, str) and dag_hash.strip()

    def test_valid_clone_persists_compiled_plan(self, client, auth_headers, harness):
        """Requirement 10.1 names the Compiled_Plan alongside the Identity_Hash."""
        harness.set_source_row(_source_row(_build_valid_graph()))

        response = _clone(client, auth_headers)
        assert response.status_code == 200

        plan = harness.cloned_buy_logic.get("_compiled_plan")
        assert plan is not None, "clone persisted with no compiled_plan"
        assert isinstance(plan, dict)
        # to_dict() is the one serialization path, and dag_hash is always in it.
        assert plan.get("dag_hash") == harness.cloned_buy_logic["_dag_hash"]

    def test_valid_clone_persists_execution_order(self, client, auth_headers, harness):
        """The second value the swallowed AttributeError discarded."""
        harness.set_source_row(_source_row(_build_valid_graph()))

        assert _clone(client, auth_headers).status_code == 200

        order = harness.cloned_buy_logic.get("_execution_order")
        assert order is not None, "clone persisted with no execution_order"
        assert isinstance(order, list) and len(order) == 5

    def test_valid_clone_persists_the_real_graph_hash(
        self, client, auth_headers, harness
    ):
        """A present hash is not enough - it must be *this graph's* hash.

        Guards against a future "fix" that satisfies the not-null assertions with a
        placeholder or a stale value copied from the source row.
        """
        from backend_app.backend.strategy_dag.schema import compute_dag_hash

        graph = _build_valid_graph()
        harness.set_source_row(_source_row(graph))

        assert _clone(client, auth_headers).status_code == 200

        assert harness.cloned_buy_logic["_dag_hash"] == compute_dag_hash(graph)

    def test_valid_clone_reports_no_warnings(self, client, auth_headers, harness):
        harness.set_source_row(_source_row(_build_valid_graph()))

        response = _clone(client, auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cloned"
        assert body["warnings"] == []

    def test_valid_clone_carries_no_invalid_marker(
        self, client, auth_headers, harness
    ):
        """A compiling clone is not marked INVALID - the two outcomes are exclusive."""
        harness.set_source_row(_source_row(_build_valid_graph()))

        assert _clone(client, auth_headers).status_code == 200

        assert harness.cloned_buy_logic.get("_dag_validation_state") != "INVALID"


# ── Requirement 10.2 / 10.3: a failing clone is explicit, never degraded ───

class TestInvalidCloneIsExplicit:
    """Requirement 10.2 and 10.3 - persisted, marked, reported. No silent degradation."""

    def test_invalid_clone_is_still_persisted(self, client, auth_headers, harness):
        """A clone is ALWAYS persisted (10.2), unlike the save path which persists nothing.

        Cloning an existing, already-saved strategy must not silently discard it.
        """
        harness.set_source_row(_source_row(_build_invalid_graph()))

        response = _clone(client, auth_headers)
        assert response.status_code == 200
        assert harness.inserted_payload is not None

    def test_invalid_clone_is_marked_invalid(self, client, auth_headers, harness):
        harness.set_source_row(_source_row(_build_invalid_graph()))

        assert _clone(client, auth_headers).status_code == 200

        assert harness.cloned_buy_logic.get("_dag_validation_state") == "INVALID"

    def test_invalid_clone_has_explicitly_null_hash_and_plan(
        self, client, auth_headers, harness
    ):
        """Null, not merely absent.

        A reader must be able to tell "compiled and INVALID" from "never compiled".
        This is also what keeps the row consistent with the Phase 4 database constraint:
        the state is INVALID, so a null hash is legal; a VALID row with a null hash is
        what ``chk_valid_requires_hash`` forbids outright.
        """
        harness.set_source_row(_source_row(_build_invalid_graph()))

        assert _clone(client, auth_headers).status_code == 200

        buy_logic = harness.cloned_buy_logic
        assert "_dag_hash" in buy_logic and buy_logic["_dag_hash"] is None
        assert "_compiled_plan" in buy_logic and buy_logic["_compiled_plan"] is None

    def test_invalid_clone_attaches_the_structured_report(
        self, client, auth_headers, harness
    ):
        harness.set_source_row(_source_row(_build_invalid_graph()))

        assert _clone(client, auth_headers).status_code == 200

        report = harness.cloned_buy_logic.get("_dag_validation_report")
        assert isinstance(report, dict), "no validation report attached to the clone"
        assert "MISSING_REQUIRED_CATEGORY" in report.get("codes", [])

    def test_invalid_clone_returns_non_empty_warnings(
        self, client, auth_headers, harness
    ):
        """Requirement 10.2 - the failure is returned in the response warning collection."""
        harness.set_source_row(_source_row(_build_invalid_graph()))

        response = _clone(client, auth_headers)
        assert response.status_code == 200

        warnings = response.json()["warnings"]
        assert warnings, (
            "the recompile failed and the caller was told nothing - this is the "
            "silent-degradation shape SB-02 shipped"
        )
        assert "MISSING_REQUIRED_CATEGORY" in warnings[0].get("codes", [])

    def test_unreadable_source_is_refused_rather_than_cloned(
        self, client, auth_headers, harness
    ):
        """The one case that is NOT persisted, via a typed handler (10.3).

        A payload carrying no readable graph has nothing to persist as an
        invalid-but-real DAG, so it is refused. Distinguishing this from the
        INVALID case is only possible because the handlers are typed.
        """
        row = _source_row(_build_valid_graph())
        row["buy_logic"] = {"_nodes": [{"totally": "unparseable"}], "_edges": []}
        harness.set_source_row(row)

        response = _clone(client, auth_headers)
        assert response.status_code == 422
        assert harness.inserted_payload is None, "an unreadable graph was persisted"


# ── Correctness Property 7: there is no third outcome ─────────────────────

class TestCloneOutcomeIsExhaustive:
    """Every persisted clone is either fully compiled or explicitly INVALID."""

    @pytest.mark.parametrize("graph_builder", [_build_valid_graph, _build_invalid_graph])
    def test_persisted_clone_is_never_silently_degraded(
        self, client, auth_headers, harness, graph_builder
    ):
        harness.set_source_row(_source_row(graph_builder()))

        assert _clone(client, auth_headers).status_code == 200

        buy_logic = harness.cloned_buy_logic
        has_hash = buy_logic.get("_dag_hash") is not None
        has_plan = buy_logic.get("_compiled_plan") is not None
        marked_invalid = buy_logic.get("_dag_validation_state") == "INVALID"

        fully_compiled = has_hash and has_plan and not marked_invalid
        explicitly_invalid = marked_invalid and not has_hash and not has_plan

        assert fully_compiled or explicitly_invalid, (
            "clone landed in the SB-02 third state: neither a complete compiled "
            f"plan nor an explicit INVALID marker. hash={has_hash} "
            f"plan={has_plan} invalid={marked_invalid}"
        )

    def test_invalid_clone_cannot_be_deployed(self):
        """Persisting an INVALID clone is only safe because it cannot be deployed.

        Ties Requirement 10.2 to the Phase 4 deploy gate (Requirement 10.4): the
        hashless row SB-02 used to create silently is now both explicitly marked and
        structurally un-deployable, so it can never reach an exchange.
        """
        from backend_app.backend.strategy_service import (
            DeployPrerequisiteError,
            StrategyService,
        )

        service = StrategyService()
        invalid_version = {
            "id": "sb02_version",
            "strategy_id": "sb02_source",
            "version": "v1.0",
            "validation_state": "INVALID",
            "dag_hash": None,
            "compiled_plan": None,
        }

        with pytest.raises(DeployPrerequisiteError) as exc_info:
            service._assert_deploy_prerequisites(
                invalid_version, "sb02_source", "v1.0"
            )

        assert exc_info.value.missing_prerequisite


# ── mechanism guards: the defect cannot return in a new location ──────────

class TestSB02MechanismIsGone:
    """Pins the cause, not just the symptom."""

    def test_dag_hash_is_a_readable_field_not_a_method(self):
        """Requirement 2.4. ``plan.dag_hash`` is a value; there is nothing to call."""
        from backend_app.backend.strategy_builder import compile_version

        plan = compile_version(_build_valid_graph()).plan

        assert isinstance(plan.dag_hash, str) and plan.dag_hash.strip()
        assert not callable(plan.dag_hash)

    def test_compiled_plan_has_no_compute_hash_method(self):
        """The deleted ``CompiledDAG.compute_hash()`` has no successor.

        A recomputation step a caller can forget is how the hash went missing.
        """
        from backend_app.backend.strategy_dag.plan import CompiledPlan

        assert not hasattr(CompiledPlan, "compute_hash")

    def test_compiled_plan_has_no_get_method(self):
        """The literal pre-fix access pattern is impossible, not merely unused.

        ``compiled.get("dag_hash")`` raised ``AttributeError`` on a ``CompiledDAG``
        object and still would on a ``CompiledPlan`` - which is why this is asserted
        rather than trusted.
        """
        from backend_app.backend.strategy_builder import compile_version

        plan = compile_version(_build_valid_graph()).plan

        assert not hasattr(plan, "get")
        with pytest.raises(AttributeError):
            plan.get("dag_hash")

    def test_compiled_plan_cannot_be_built_without_a_hash(self):
        """A hashless plan is rejected at construction, not caught later by a constraint.

        By the time a database constraint fires, the caller has already been told the
        clone succeeded - which is precisely what SB-02 did.
        """
        from backend_app.backend.strategy_dag.plan import CompiledPlan, PlanBuildError

        with pytest.raises(PlanBuildError):
            CompiledPlan(dag_hash="")

    def test_router_never_reads_a_compiled_plan_with_dict_access(self):
        """The SB-02 shape is dict access applied to a compiled-plan OBJECT.

        Asserted over the parsed syntax tree, not the raw text, because the router's
        docstrings quote the pre-fix lines verbatim to explain SB-02. A text scan would
        match that documentation and force the explanation to be deleted to make the
        test pass - the wrong incentive entirely.

        Scoped by receiver, because ``.get("execution_order")`` is only a defect when the
        receiver is a plan. The router also calls it on ``dag_result``, the plain dict
        ``dag_engine.execute_dag`` returns, and on stored database rows - both legitimate
        mappings. Flagging those would make the guard noise, and a noisy guard gets
        deleted. What must never reappear is ``plan.get(...)`` / ``compiled.get(...)``.
        """
        import backend_app.routers.strategies as strategies_router

        tree = ast.parse(inspect.getsource(strategies_router))

        offenders = []
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ("dag_hash", "execution_order")
            ):
                continue
            receiver = ast.unparse(node.func.value).lower()
            if "plan" in receiver or receiver.startswith("compiled"):
                offenders.append(f"line {node.lineno}: {ast.unparse(node)}")

        assert not offenders, (
            "a compiled plan is being read with dict access - this is literally SB-02: "
            + "; ".join(offenders)
        )

    def test_clone_recompile_is_guarded_only_by_typed_handlers(self):
        """Requirement 10.3 - a typed error path, not a bare ``except Exception``.

        Locates the ``try`` that actually wraps the recompile and inspects its handlers.
        A bare ``except Exception`` there is what downgraded SB-02's ``AttributeError``
        to a log line; the design's error-handling rules call a swallowed exception on a
        persistence path a review-blocking defect. The broad handler guarding the
        database insert further down is a different, legitimate concern, and scoping by
        syntax tree keeps the two apart without relying on text offsets.
        """
        import backend_app.routers.strategies as strategies_router

        tree = ast.parse(inspect.getsource(strategies_router.clone_strategy))

        compile_tries = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_compile_payload"
                for stmt in node.body
                for inner in ast.walk(stmt)
            )
        ]

        assert len(compile_tries) == 1, (
            f"expected exactly one try block around the recompile, found "
            f"{len(compile_tries)}"
        )

        handled = {ast.unparse(h.type) for h in compile_tries[0].handlers if h.type}

        assert "CompileValidationError" in handled, (
            "the invalid-graph outcome (Requirement 10.2) is not handled by name"
        )
        assert any("GraphParseError" in h for h in handled), (
            "the unreadable-graph outcome is not handled by name"
        )
        assert not any(
            h in ("Exception", "BaseException") for h in handled
        ), (
            "a bare `except Exception` is back on the clone recompile path - this is "
            f"the mechanism that hid SB-02. Handlers found: {sorted(handled)}"
        )
        assert not any(h.type is None for h in compile_tries[0].handlers), (
            "a bare `except:` is guarding the clone recompile"
        )


# ── the original row is not collateral damage ─────────────────────────────

def test_clone_does_not_mutate_the_source_row(client, auth_headers, harness):
    """The recompiled DAG fields are written into a deep copy.

    The clone writes ``_dag_*`` keys into ``buy_logic``; without the deep copy it would
    stamp the *source* strategy's blob with the clone's compiled state.
    """
    row = _source_row(_build_valid_graph())
    harness.set_source_row(row)

    assert _clone(client, auth_headers).status_code == 200

    assert "_dag_hash" not in row["buy_logic"]
    assert "_compiled_plan" not in row["buy_logic"]
    assert harness.cloned_buy_logic is not row["buy_logic"]
