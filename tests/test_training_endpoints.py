"""
tests/test_training_endpoints.py

The HTTP surface of the training path: the save endpoint and the two job endpoints.

Spec: strategy-builder task 6.3 (`design.md` § API surface, § Training workflow).
Requirements 12.6, 14.10, 15.1, 15.7, 15.12, 15.13, 16.7, 21.1, 21.8.

WHAT IS ASSERTED HERE THAT THE SERVICE TESTS CANNOT
---------------------------------------------------
``tests/test_training_service_admission.py`` covers what reaches ``training_jobs``.
This file covers the parts that only exist at the HTTP boundary:

* the three paths ``design.md``'s API table names actually exist and are mounted;
* every one of them requires an authenticated user, and every one keeps a ``slowapi``
  limit (Requirements 21.1, 21.8);
* ``require_ml_training`` and ``check_ml_quota`` are still declared dependencies on
  ``POST /training/jobs`` - the caps from task 6.2 are additive to them, not a
  replacement (Requirement 16.7);
* the request models are closed to unknown fields, so a client that sends an
  ``exchange`` or an ``api_key`` gets a 422 rather than having it silently dropped
  (SB-06);
* the status-code mapping: 200 saved, 422 for a graph report and for a blocked
  admission, 409 for a job that is no longer cancellable, 404 for anything that is not
  this tenant's.

The doubles are the same two as the service tests, and for the same reasons: the
database (no local PostgreSQL, and ``004d_training_and_models.sql`` is unapplied) and
the candle feed. The app, the router, the dependencies, the compiler, the validator,
the gate and the caps are all real.
"""

import ast
import os
import pathlib
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_service as S
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.subscription_dependencies import check_ml_quota, require_ml_training

from tests.test_training_service_admission import (  # noqa: E402 - shared doubles
    PAID_TIER,
    STRATEGY_ID,
    USER_ID,
    FakeSupabase,
    bounded_bars,
    linear_graph,
    live_job,
    model_graph,
    owner,
)

ROUTER_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "backend_app"
    / "routers"
    / "strategy_operations.py"
)

SAVE_PATH = f"/api/strategy-operations/strategies/{STRATEGY_ID}/versions"
JOBS_PATH = "/api/strategy-operations/training/jobs"


@pytest.fixture(scope="module")
def reg():
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def default_hooks():
    from backend_app.backend.strategy_dag import validator as V

    V.install_default_stage_hooks()
    yield
    V.install_default_stage_hooks()


@pytest.fixture(autouse=True)
def forget_column_probe():
    S.reset_canonical_column_support()
    yield
    S.reset_canonical_column_support()


@pytest.fixture(autouse=True)
def deterministic_window(monkeypatch):
    """The one I/O boundary, replaced. Records what was asked for."""
    calls = []

    async def _fetch(symbol, timeframe, bars):
        calls.append({"symbol": symbol, "timeframe": timeframe, "bars": int(bars)})
        return bounded_bars(int(bars))

    monkeypatch.setattr(S, "fetch_training_bars", _fetch)
    return calls


@pytest.fixture(autouse=True)
def quiet_handoffs(monkeypatch):
    async def _enqueue(job_id, user_id):
        return True

    async def _publish(user_id, event, payload):
        return True

    monkeypatch.setattr(S, "enqueue_training_job", _enqueue)
    monkeypatch.setattr(S, "publish_training_event", _publish)


@pytest.fixture
def db():
    return FakeSupabase.seeded()


@pytest.fixture
def client(db):
    """A client for the real app, with the limiter suspended for the duration.

    The endpoints under test are limited to 30, 20 and 60 requests a minute. This file
    and the rest of the suite make more than that from one address, so leaving the
    limiter armed would make these assertions depend on how many other tests ran first
    - and a 429 says nothing about the training path either way. The limits themselves
    are NOT weakened: they stay exactly as declared in production code, they are
    restored after every test, and ``TestTheControlsStayInForce`` asserts each decorator
    is still on its endpoint.
    """
    from fastapi.testclient import TestClient

    from backend_app.main import app

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    app.dependency_overrides[get_current_user] = lambda: owner()
    app.dependency_overrides[get_request_supabase] = lambda: db
    try:
        with patch.object(
            S, "create_request_supabase_async", AsyncMock(return_value=db)
        ):
            yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


# ---------------------------------------------------------------------------
# 1. The three paths exist, on the paths the design names
# ---------------------------------------------------------------------------


class TestTheDesignedPathsExist:
    def test_all_three_are_mounted(self):
        from backend_app.main import app

        paths = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/strategy-operations/strategies/{strategy_id}/versions" in paths
        assert "/api/strategy-operations/training/jobs" in paths
        assert "/api/strategy-operations/training/jobs/{job_id}/cancel" in paths

    def test_each_one_is_a_post(self):
        """Each designed path is served by a POST.

        Asserted per PATH rather than per ROUTE on purpose. FastAPI mounts one route
        object per method, and task 6.6 deliberately mounts ``GET
        /api/strategy-operations/training/jobs`` - the truthful status listing - on the
        same path as this POST with a different method, which is the normal REST reading
        of "the collection". Iterating routes and demanding ``POST`` of every one of them
        would therefore fail on a sibling GET that is supposed to be there, while saying
        nothing extra about the POST this test exists to pin down. What must hold is that
        each of these three paths accepts a POST; it is not, and never was, that no other
        method may share the path.
        """
        from backend_app.main import app

        wanted = {
            "/api/strategy-operations/strategies/{strategy_id}/versions",
            "/api/strategy-operations/training/jobs",
            "/api/strategy-operations/training/jobs/{job_id}/cancel",
        }
        methods_by_path = {path: set() for path in wanted}
        for route in app.routes:
            path = getattr(route, "path", None)
            if path in wanted:
                methods_by_path[path] |= set(route.methods or ())

        for path, methods in methods_by_path.items():
            assert "POST" in methods, (path, sorted(methods))


# ---------------------------------------------------------------------------
# 2. The controls, unchanged (Requirements 21.1, 21.8, 16.7)
# ---------------------------------------------------------------------------


def _decorators_of(function_name):
    """The decorator source lines on ``function_name``, read from the router file.

    Read from source rather than from ``__wrapped__`` chains because that is what a
    reviewer changing this file would delete, and it is the change this test exists to
    catch.
    """
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return [ast.unparse(d) for d in node.decorator_list]
    raise AssertionError(f"{function_name} is not defined in {ROUTER_SOURCE}")


class TestTheControlsStayInForce:
    """No control is weakened. Requirement 21, and this spec's own rule."""

    @pytest.mark.parametrize(
        "function_name,route",
        [
            ("save_strategy_version", "/strategy-operations/strategies/{strategy_id}/versions"),
            ("create_training_job", "/strategy-operations/training/jobs"),
            ("cancel_training_job", "/strategy-operations/training/jobs/{job_id}/cancel"),
        ],
    )
    def test_every_endpoint_keeps_its_route_and_its_rate_limit(self, function_name, route):
        decorators = _decorators_of(function_name)
        assert any(route in d for d in decorators), decorators
        assert any(d.startswith("limiter.limit(") for d in decorators), (
            f"{function_name} lost its slowapi limit"
        )

    @pytest.mark.parametrize(
        "function_name",
        ["save_strategy_version", "create_training_job", "cancel_training_job"],
    )
    def test_every_endpoint_requires_an_authenticated_user(self, function_name):
        from backend_app.routers import strategy_operations

        import inspect as _inspect

        signature = _inspect.signature(getattr(strategy_operations, function_name))
        user = signature.parameters["user"]
        assert "get_current_user" in repr(user.default), user

    def test_the_job_endpoint_keeps_require_ml_training_and_check_ml_quota(self):
        """Requirement 16.7: task 6.2's caps are ADDITIVE to these, not a replacement."""
        from backend_app.routers import strategy_operations

        import inspect as _inspect

        signature = _inspect.signature(strategy_operations.create_training_job)
        defaults = repr([p.default for p in signature.parameters.values()])
        assert "require_ml_training" in defaults
        assert "check_ml_quota" in defaults
        # And the functions themselves are the platform's, not a local copy.
        assert require_ml_training.__module__ == "backend_app.core.subscription_dependencies"
        assert check_ml_quota.__module__ == "backend_app.core.subscription_dependencies"

    def test_the_save_endpoint_applies_the_same_two_controls_on_its_training_branch(self):
        """A dependency cannot be conditional, so the branch calls them itself.

        Gating every save behind ``require_ml_training`` would refuse a FREE user's
        indicator-only strategy, so the save endpoint cannot carry them as
        dependencies. It calls the same two functions inside the training branch
        instead, and this asserts that it is those functions and not a reimplementation.
        """
        import inspect as _inspect

        from backend_app.backend.strategy_service import StrategyService

        source = _inspect.getsource(StrategyService._assert_ml_training_entitled)
        assert "require_feature(Feature.ML_TRAINING.value" in source
        assert "require_quota(Resource.ML_TRAININGS.value" in source
        assert "from backend_app.core.subscription_dependencies import" in source


# ---------------------------------------------------------------------------
# 3. The request models are closed (SB-06)
# ---------------------------------------------------------------------------


class TestTheRequestModelsRefuseExchangeIdentity:
    """An ``exchange`` field is a 422, not a silently ignored key.

    A key that is quietly dropped is worse than one that is refused: a client author
    believes it is doing something, and the strategy trades a market nobody chose. That
    is defect SB-06, and ``extra="forbid"`` is what makes it impossible to express.
    """

    @pytest.mark.parametrize(
        "field", ["exchange", "exchange_id", "api_key", "secret", "exchange_account_id"]
    )
    def test_the_training_config_forbids_it(self, field):
        import pydantic

        from backend_app.routers.strategy_operations import TrainingConfigRequest

        with pytest.raises(pydantic.ValidationError):
            TrainingConfigRequest(**{field: "binance"})

    @pytest.mark.parametrize("field", ["exchange", "exchange_id", "api_key"])
    def test_the_save_request_forbids_it(self, field):
        import pydantic

        from backend_app.routers.strategy_operations import VersionSaveRequest

        with pytest.raises(pydantic.ValidationError):
            VersionSaveRequest(blueprint={"nodes": [], "edges": []}, **{field: "binance"})

    def test_the_endpoint_answers_422_rather_than_dropping_the_field(self, client, reg):
        graph, _model_id = model_graph(reg)
        response = client.post(
            SAVE_PATH,
            json={"blueprint": graph.to_dict(), "exchange": "binance"},
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# 4. The status-code mapping
# ---------------------------------------------------------------------------


class TestSaveResponseMapping:
    def test_a_graph_with_no_model_node_is_saved_ready_and_not_required(
        self, client, reg, db
    ):
        response = client.post(SAVE_PATH, json={"blueprint": linear_graph(reg).to_dict()})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["lifecycle_state"] == "READY"
        assert body["training"]["state"] == S.TRAINING_NOT_REQUIRED
        assert body["training"]["required"] is False
        assert db.jobs == []

    def test_an_admitted_graph_answers_queued_with_the_job_id(self, client, reg, db):
        graph, model_id = model_graph(reg)
        response = client.post(
            SAVE_PATH,
            json={"blueprint": graph.to_dict(), "training": {"epochs": 100}},
        )

        assert response.status_code == 200, response.text
        training = response.json()["training"]
        assert training["state"] == S.TRAINING_QUEUED
        assert training["job_id"] == db.jobs[0]["id"]
        assert db.jobs[0]["node_id"] == model_id
        assert db.jobs[0]["status"] == "QUEUED"

    def test_a_blocked_gate_is_a_200_save_with_a_blocked_training_half(
        self, client, reg, db
    ):
        """The save succeeded; only training is refused.

        Refusing the whole request would discard a compiled version and force the
        author to resubmit an identical graph. The explicit ``POST /training/jobs``
        endpoint, whose only purpose is to create a job, answers 422 for the same
        block - asserted below.
        """
        graph, _model_id = model_graph(reg, lags=(1, 2, 3))
        response = client.post(
            SAVE_PATH,
            json={"blueprint": graph.to_dict(), "training": {"epochs": 100}},
        )

        assert response.status_code == 200, response.text
        training = response.json()["training"]
        assert training["state"] == S.TRAINING_BLOCKED
        assert training["reason"] == S.REASON_ML_REQUIREMENTS
        assert training["job_id"] is None
        assert db.jobs == []

    def test_an_invalid_graph_is_422_with_the_full_report_and_persists_nothing(
        self, client, reg, db
    ):
        graph, _model_id = model_graph(reg)
        # Strip the DATA node's symbol: the save must be refused naming node and field.
        payload = graph.to_dict()
        for node in payload["nodes"]:
            if node["block_id"] == "ohlcv_feed":
                node["params"].pop("symbol", None)

        response = client.post(SAVE_PATH, json={"blueprint": payload})

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_VALIDATION_FAILED"
        assert detail["report"] is not None
        assert detail["codes"]
        assert db.versions == [], "a refused save persists no version"
        assert db.jobs == []

    def test_another_tenants_strategy_is_404(self, client, reg, db):
        db.tables["strategies"] = [{"id": STRATEGY_ID, "user_id": "someone_else"}]
        response = client.post(SAVE_PATH, json={"blueprint": linear_graph(reg).to_dict()})

        assert response.status_code == 404, response.text
        assert db.versions == []


class TestJobEndpointMapping:
    def _save(self, client, reg, **training):
        graph, model_id = model_graph(reg)
        response = client.post(
            SAVE_PATH,
            json={"blueprint": graph.to_dict(), "training": training or {"epochs": 100}},
        )
        assert response.status_code == 200, response.text
        return response.json(), model_id

    def test_a_repeat_request_answers_with_the_existing_job(self, client, reg, db):
        saved, model_id = self._save(client, reg)
        response = client.post(
            JOBS_PATH,
            json={"version_id": saved["version_id"], "node_id": model_id,
                  "training": {"epochs": 100}},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job_id"] == saved["training"]["job_id"]
        assert body["idempotent_nodes"] == [model_id]
        assert len(db.jobs) == 1

    def test_a_blocked_admission_is_422_and_says_no_job_was_created(self, client, reg, db):
        graph, model_id = model_graph(reg, lags=(1, 2, 3))
        saved = client.post(
            SAVE_PATH, json={"blueprint": graph.to_dict(), "training": {"epochs": 100}}
        ).json()

        response = client.post(
            JOBS_PATH,
            json={"version_id": saved["version_id"], "training": {"epochs": 100}},
        )

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "TRAINING_BLOCKED"
        assert detail["reason"] == S.REASON_ML_REQUIREMENTS
        assert detail["job_created"] is False
        assert set(detail["detail"]["required"]) >= {"columns", "rows"}
        assert set(detail["detail"]["available"]) >= {"columns", "rows"}
        assert db.jobs == []

    def test_an_unknown_version_is_404(self, client):
        response = client.post(
            JOBS_PATH, json={"version_id": "00000000-0000-0000-0000-000000000000"}
        )
        assert response.status_code == 404, response.text

    def test_an_unknown_node_is_404(self, client, reg):
        saved, _model_id = self._save(client, reg)
        response = client.post(
            JOBS_PATH, json={"version_id": saved["version_id"], "node_id": "n_nope"}
        )
        assert response.status_code == 404, response.text


class TestCancelEndpointMapping:
    def test_cancel_sets_the_flag_and_returns_200(self, client, db):
        row = live_job(db, status="RUNNING")
        response = client.post(f"{JOBS_PATH}/job-live/cancel")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cancel_requested"] is True
        assert body["status"] == "RUNNING"
        assert row["cancel_requested"] is True
        assert row["status"] == "RUNNING", "the worker owns the CANCELLED transition"

    def test_a_finished_job_is_409_naming_the_state(self, client, db):
        live_job(db, status="FAILED")
        response = client.post(f"{JOBS_PATH}/job-live/cancel")

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "TRAINING_JOB_NOT_CANCELLABLE"
        assert "FAILED" in detail["message"]

    def test_another_users_job_is_404(self, client, db):
        row = live_job(db, user_id="usr_someone_else")
        response = client.post(f"{JOBS_PATH}/job-live/cancel")

        assert response.status_code == 404, response.text
        assert row["cancel_requested"] is False

    def test_an_unknown_job_is_404(self, client, db):
        response = client.post(f"{JOBS_PATH}/nope/cancel")
        assert response.status_code == 404, response.text
