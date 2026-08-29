"""
tests/test_task_8_2_deploy_preflight.py

``GET .../versions/{version}/deploy/preflight``, held in place.

Spec: trading-lifecycle-integration task 8.2. ``design.md`` -> API surface
(``GET /api/strategy-operations/strategies/{id}/versions/{version}/deploy/preflight``,
"Calls ``evaluate_binding_summary`` (collect-all, read-only)") and -> the preflight
response shape. Requirements 13.3, 13.4, 13.5, 13.6, with 22.2's rate-limit convention
and 20.2's non-existence answer.

WHAT THESE TESTS HOLD IN PLACE
------------------------------
* **The route actually resolves to this handler.** ``routers/strategies.py`` is mounted at
  ``/api/strategies`` *before* ``strategy_operations`` is mounted at ``/api``, and
  first-match-wins - this repository already carries nine (method, path) pairs both
  routers declare, where the first-mounted one wins. So the resolution is asserted against
  the **real** ``backend_app.main.app``, for both registered paths, rather than against a
  router assembled inside the test. A test that only checked the response body would pass
  just as happily while another module's handler served the path.

* **A non-deployable answer is 200.** The conditions carry the ``http_status`` the *write*
  path would use (404 for another tenant's account, 409 for a non-READY version, 503 for
  an unloaded universe). None of them becomes the preflight's status: a preflight is a 200
  answer *about* a deployment. Only a question this endpoint cannot answer at all - a
  version that is not this caller's, an archived strategy, an unparseable
  ``execution_config`` - is a non-200.

* **Read-only, because it is polled.** The fake PostgREST client records every insert and
  update; the preflight is required to have asked for none. The quota reservation the
  write path makes is asserted **not** to happen: reserving on a poll would spend a user's
  bot allowance by having a modal open (13.6 polls it every two seconds).

* **The shape is ``design.md``'s, produced by ``BindingSummary.to_dict()``.** Top level is
  exactly ``{deployable, conditions}``; a condition carries only
  ``{name, status, detail?, code?, message?, reason?}``. Neither ``http_status`` nor the
  ``DeploymentBinding`` the write path would create is serialised.

* **The preflight and the deploy cannot disagree.** A summary that says ``deployable`` is
  followed by a deploy that succeeds, on the same fake database; a version the write path
  refuses with ``DEPLOY_PREREQUISITE_NOT_MET`` is reported as a failed condition rather
  than omitted (Requirement 13.1 counts a hash-verified compiled plan among the mandatory
  validations, so 13.3 requires its status).

The doubles are the ones ``tests/test_task_8_2_deployment_binding.py`` already uses - the
same ``_Supabase``/``_Query`` fake, the same real ``AssetUniverse``, the same
``StrategyService`` with one seam - so this endpoint is exercised against the same
environment the write path is.
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import asset_universe as au
from backend_app.backend import deployment_binding as db
from backend_app.backend.strategy_service import DEPLOY_PREREQUISITE_CONDITION

from tests.test_task_8_2_deployment_binding import (  # the same doubles, not a second set
    ACCOUNT_ID,
    OTHER_ACCOUNT_ID,
    OTHER_USER_ID,
    RISK_ID,
    STRATEGY_ID,
    USER_ID,
    _default_rows,
    _deploy,
    _service,
    _Supabase,
    _universe,
    _user,
    _version,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"

VERSION = "v3.0"

#: Both registered paths. The first is the one ``design.md`` documents; the second is the
#: sibling of the existing ``POST /api/strategies/{id}/versions/{v}/deploy``.
PREFLIGHT_PATHS = (
    f"/api/strategy-operations/strategies/{STRATEGY_ID}/versions/{VERSION}/deploy/preflight",
    f"/api/strategies/{STRATEGY_ID}/versions/{VERSION}/deploy/preflight",
)


@pytest.fixture(autouse=True)
def _clean_module_state():
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()
    au._local_universe = _universe()  # noqa: SLF001 - reset_..._for_tests is the inverse
    yield
    db.reset_binding_column_support()
    db.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _sb(**kwargs):
    return _Supabase(rows=_default_rows(), **kwargs)


def _client(sb, *, user=None):
    """A TestClient over the real router, with the real service and a fake database.

    ``get_strategy_service`` is patched to return a ``StrategyService`` whose one seam is
    the Supabase client, so the handler runs the production ownership read, the production
    archive refusal and the production gates - only the rows are fake.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user
    from backend_app.routers import strategy_operations as ops

    service = _service(sb)

    async def _get_service():
        return service

    app = FastAPI()
    app.include_router(ops.router, prefix="/api")
    app.state.limiter = ops.limiter
    app.dependency_overrides[get_current_user] = lambda: (user or _user())
    client = TestClient(app, raise_server_exceptions=False)
    return client, patch.object(ops, "get_strategy_service", _get_service)


def _get(sb, *, params=None, user=None, path=PREFLIGHT_PATHS[0]):
    client, service_patch = _client(sb, user=user)
    quota = patch(
        "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
        AsyncMock(return_value=(True, 1, 10)),
    )
    with service_patch, quota as reserve_quota:
        response = client.get(path, params=params or {})
    # Requirement 13.6 polls this: a poll must not consume the caller's bot allowance.
    reserve_quota.assert_not_awaited()
    return response


def _statuses(body):
    return {c["name"]: c["status"] for c in body["conditions"]}


def _condition(body, name):
    return next(c for c in body["conditions"] if c["name"] == name)


def _passing_params():
    return {"mode": "paper", "exchange_account_id": ACCOUNT_ID, "risk_config_id": RISK_ID}


# ---------------------------------------------------------------------------
# 1. The route resolves to this handler, on the real app
# ---------------------------------------------------------------------------


class TestTheRouteIsReachableAndNotShadowed:
    """The hazard is real in this repository, so it is asserted, not reasoned about."""

    def _matched(self, method, path):
        from starlette.routing import Match

        from backend_app.main import app

        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "path_params": {},
            "headers": [],
            "root_path": "",
            "query_string": b"",
        }
        return [route for route in app.routes if route.matches(scope)[0] == Match.FULL]

    @pytest.mark.parametrize("path", PREFLIGHT_PATHS)
    def test_the_real_app_resolves_both_paths_to_the_preflight_handler(self, path):
        from backend_app.routers import strategy_operations as ops

        matched = self._matched("GET", path)

        assert matched, f"GET {path} resolves to no route at all"
        # First-match-wins, so the *first* match is the one that will serve it.
        assert matched[0].endpoint is ops.preflight_deploy_version, (
            f"GET {path} is served by {getattr(matched[0], 'name', matched[0])}, not by "
            "preflight_deploy_version"
        )

    @pytest.mark.parametrize("path", PREFLIGHT_PATHS)
    def test_the_path_is_registered_exactly_once_per_variant(self, path):
        """Two registrations of one path would make which handler answers a coin toss."""
        assert len(self._matched("GET", path)) == 1

    def test_the_deploy_post_it_belongs_to_still_resolves(self):
        """The new, longer GET path did not disturb the POST it hangs off."""
        from backend_app.routers import strategy_operations as ops

        matched = self._matched(
            "POST", f"/api/strategies/{STRATEGY_ID}/versions/{VERSION}/deploy"
        )
        assert matched
        assert matched[0].endpoint is ops.deploy_version

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_only_get_serves_the_preflight_path(self, method):
        assert self._matched(method, PREFLIGHT_PATHS[0]) == []

    def test_the_route_keeps_its_auth_dependency_and_its_limiter(self):
        """Requirement 22.2, and 22.4's authenticated-caller requirement."""
        source = ROUTER_PATH.read_text(encoding="utf-8")
        index = source.index("async def preflight_deploy_version(")
        window = source[index - 400 : index + 3000]
        assert "@limiter.limit(" in window
        assert "Depends(get_current_user)" in window

    def test_the_limit_matches_the_comparable_existing_get(self):
        """Requirement 22.2: the same action on the closest equivalent resource.

        ``GET /strategies/{strategy_id}`` is that endpoint, and it is read out of the
        router rather than restated here, so the two cannot drift apart.
        """
        source = ROUTER_PATH.read_text(encoding="utf-8")
        comparable = source.index('@router.get("/strategies/{strategy_id}")')
        comparable_limit = source[comparable : comparable + 200].split(
            "@limiter.limit(", 1
        )[1].split(")", 1)[0]

        preflight = source.index("async def preflight_deploy_version(")
        preflight_limit = source[preflight - 400 : preflight].rsplit(
            "@limiter.limit(", 1
        )[1].split(")", 1)[0]

        assert preflight_limit == comparable_limit


# ---------------------------------------------------------------------------
# 2. The response shape is design.md's, and nothing more
# ---------------------------------------------------------------------------


class TestTheResponseShape:
    def test_a_fully_passing_preflight_is_deployable(self):
        response = _get(_sb(), params=_passing_params())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deployable"] is True
        expected = {DEPLOY_PREREQUISITE_CONDITION, *db.BINDING_CONDITIONS}
        assert _statuses(body) == {name: db.CONDITION_PASSED for name in expected}

    def test_the_top_level_body_is_exactly_designs_two_keys(self):
        body = _get(_sb(), params=_passing_params()).json()

        assert set(body) == {"deployable", "conditions"}
        assert isinstance(body["deployable"], bool)

    def test_a_condition_carries_only_designs_keys_and_no_nulls(self):
        body = _get(
            _sb(), params={"exchange_account_id": OTHER_ACCOUNT_ID}
        ).json()

        for condition in body["conditions"]:
            assert {"name", "status"} <= set(condition)
            assert set(condition) <= {
                "name",
                "status",
                "detail",
                "code",
                "message",
                "reason",
            }
            assert condition["status"] in db.CONDITION_STATUSES
            # A UI that renders `detail` unconditionally must not print "None".
            assert None not in condition.values()

    def test_the_internal_http_status_is_not_serialised(self):
        """It is the status the *write* path would use, not this response's."""
        body = _get(_sb(), params={"exchange_account_id": OTHER_ACCOUNT_ID}).json()

        assert all("http_status" not in c for c in body["conditions"])

    def test_the_binding_is_not_serialised(self):
        """``BindingSummary.binding`` is deliberately absent from ``to_dict()``."""
        body = _get(_sb(), params=_passing_params()).json()

        assert "binding" not in body
        assert "exchange_account_id" not in body


# ---------------------------------------------------------------------------
# 3. A non-deployable answer is still 200 (13.3, 13.4)
# ---------------------------------------------------------------------------


class TestANonDeployableSummaryIsStillTwoHundred:
    def test_a_non_ready_version_is_a_reported_condition_not_a_409(self):
        sb = _Supabase(rows=_default_rows(version=_version(lifecycle_state="DRAFT")))

        response = _get(sb, params=_passing_params())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deployable"] is False
        condition = _condition(body, "version_ready")
        assert condition["status"] == db.CONDITION_FAILED
        assert condition["code"] == "VERSION_NOT_READY"
        assert condition["detail"]["outstanding_prerequisite"]

    def test_another_tenants_account_is_a_condition_not_a_404(self):
        """The write path answers 404 here; the preflight reports it and answers 200."""
        response = _get(_sb(), params={"exchange_account_id": OTHER_ACCOUNT_ID})

        assert response.status_code == 200, response.text
        body = response.json()
        condition = _condition(body, "exchange_account")
        assert condition["status"] == db.CONDITION_FAILED
        assert condition["code"] == "EXCHANGE_ACCOUNT_NOT_FOUND"
        assert body["deployable"] is False

    def test_every_failed_condition_is_reported_not_only_the_first(self):
        """Requirement 13.2 through the HTTP surface."""
        sb = _Supabase(rows=_default_rows(version=_version(lifecycle_state="DRAFT")))

        body = _get(sb, params={"mode": "wishful", "risk_config_id": "nope"}).json()

        statuses = _statuses(body)
        assert statuses["version_ready"] == db.CONDITION_FAILED
        assert statuses["deployment_mode"] == db.CONDITION_FAILED
        assert statuses["risk_config"] == db.CONDITION_FAILED

    def test_a_blocked_condition_is_pending_and_names_its_blocker(self):
        body = _get(_sb(), params={"exchange_account_id": OTHER_ACCOUNT_ID}).json()

        condition = _condition(body, "symbol_available")
        assert condition["status"] == db.CONDITION_PENDING
        assert condition["reason"] == "blocked by exchange_account"
        assert body["deployable"] is False

    def test_live_without_an_account_is_thirteen_six_reported_as_a_condition(self):
        body = _get(_sb(), params={"mode": "live"}).json()

        condition = _condition(body, "deployment_mode")
        assert condition["code"] == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
        assert body["deployable"] is False

    def test_an_unapplied_004e_reports_the_migration_rather_than_a_503(self):
        sb = _Supabase(rows=_default_rows(), absent_columns=db.BINDING_COLUMNS)

        response = _get(
            sb, params={"mode": "live", "exchange_account_id": ACCOUNT_ID}
        )

        assert response.status_code == 200, response.text
        condition = _condition(response.json(), "binding_storable")
        assert condition["status"] == db.CONDITION_FAILED
        assert condition["code"] == "BINDING_NOT_STORABLE"
        assert condition["detail"]["migration"] == db.DEPLOYMENT_BINDING_MIGRATION


# ---------------------------------------------------------------------------
# 4. Requirement 10.4's prerequisite gate is a condition, not an omission
# ---------------------------------------------------------------------------


class TestTheDeployPrerequisiteGateIsReported:
    @pytest.mark.parametrize(
        "overrides,missing",
        [
            ({"validation_state": "INVALID"}, "validation_state"),
            ({"dag_hash": None}, "dag_hash"),
            ({"compiled_plan": None}, "compiled_plan"),
        ],
    )
    def test_an_unmet_prerequisite_is_a_failed_condition(self, overrides, missing):
        sb = _Supabase(rows=_default_rows(version=_version(**overrides)))

        response = _get(sb, params=_passing_params())

        assert response.status_code == 200, response.text
        body = response.json()
        condition = _condition(body, DEPLOY_PREREQUISITE_CONDITION)
        assert condition["status"] == db.CONDITION_FAILED
        assert condition["code"] == "DEPLOY_PREREQUISITE_NOT_MET"
        assert condition["detail"]["missing_prerequisite"] == missing
        assert body["deployable"] is False

    def test_it_is_reported_first_the_order_the_write_path_gates_it(self):
        body = _get(_sb(), params=_passing_params()).json()

        assert body["conditions"][0]["name"] == DEPLOY_PREREQUISITE_CONDITION

    @pytest.mark.asyncio
    async def test_the_write_path_still_refuses_what_the_preflight_reports(self):
        """The two surfaces agree about the same version, by different conventions."""
        from backend_app.backend.strategy_service import DeployPrerequisiteError

        sb = _Supabase(rows=_default_rows(version=_version(dag_hash=None)))

        with pytest.raises(DeployPrerequisiteError) as excinfo:
            await _deploy(sb, payload=_passing_params())

        assert excinfo.value.missing_prerequisite == "dag_hash"
        assert sb.inserts == []


# ---------------------------------------------------------------------------
# 5. Read-only and side-effect-free, because it is polled (13.6)
# ---------------------------------------------------------------------------


class TestThePreflightWritesNothing:
    def test_no_insert_and_no_update_is_asked_for(self):
        sb = _sb()

        _get(sb, params=_passing_params())

        assert sb.inserts == []
        assert sb.updates == []

    def test_a_repeated_poll_returns_the_same_answer(self):
        """13.6 polls this every two seconds; the answer must be a function of state."""
        sb = _sb()

        first = _get(sb, params=_passing_params()).json()
        second = _get(sb, params=_passing_params()).json()

        assert first == second
        assert sb.inserts == []

    def test_a_condition_change_flips_deployable_on_the_next_poll(self):
        """13.6: a condition that had passed and then fails disables the button again."""
        sb = _sb()
        assert _get(sb, params=_passing_params()).json()["deployable"] is True

        # The account changes hands while the modal is open.
        for row in sb.rows["exchange_keys"]:
            if row["id"] == ACCOUNT_ID:
                row["user_id"] = OTHER_USER_ID

        body = _get(sb, params=_passing_params()).json()
        assert body["deployable"] is False
        assert _condition(body, "exchange_account")["code"] == "EXCHANGE_ACCOUNT_NOT_FOUND"

    def test_no_credential_is_read_and_none_travels_back(self):
        """Requirements 13.9/21.7: an account is a reference on this surface too."""
        with patch(
            "backend_app.backend.api_key_vault.APIKeyVault.load_decrypted_keys",
            side_effect=AssertionError("the preflight must not read keys"),
        ):
            response = _get(_sb(), params=_passing_params())

        assert response.status_code == 200, response.text
        text = response.text.lower()
        for secret in ("api_key", "api_secret", "passphrase", "private_key"):
            assert secret not in text


# ---------------------------------------------------------------------------
# 6. What the preflight refuses outright, and with which status
# ---------------------------------------------------------------------------


class TestTheRefusalsThatAreAboutTheRequest:
    def test_another_tenants_version_is_a_404(self):
        """Requirement 20.2: the same answer a non-existent id gets."""
        response = _get(_sb(), user=_user(OTHER_USER_ID), params=_passing_params())

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "VERSION_NOT_FOUND"

    def test_a_version_that_does_not_exist_is_the_same_404(self):
        client, service_patch = _client(_sb())
        with service_patch:
            response = client.get(
                f"/api/strategies/{STRATEGY_ID}/versions/v99.0/deploy/preflight"
            )

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "VERSION_NOT_FOUND"

    def test_an_archived_strategy_is_a_409(self):
        """Requirement 3.3, mapped by the deploy route's own refusal mapping."""
        rows = _default_rows()
        rows["strategies"][0]["archived_at"] = "2024-05-01T00:00:00+00:00"
        sb = _Supabase(rows=rows)

        response = _get(sb, params=_passing_params())

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_ARCHIVED"
        assert detail["strategy_id"] == STRATEGY_ID

    def test_an_unparseable_execution_config_is_a_422(self):
        response = _get(_sb(), params={"execution_config": "{not json"})

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "PREFLIGHT_REQUEST_INVALID"

    def test_a_non_object_execution_config_is_a_422(self):
        response = _get(_sb(), params={"execution_config": "[1, 2]"})

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "PREFLIGHT_REQUEST_INVALID"

    def test_an_unknown_execution_config_field_is_a_reported_condition(self):
        """A limit the deploy would refuse is named here, not discovered on submit."""
        response = _get(
            _sb(),
            params={**_passing_params(), "execution_config": '{"max_notional": 10}'},
        )

        assert response.status_code == 200, response.text
        condition = _condition(response.json(), "execution_config")
        assert condition["status"] == db.CONDITION_FAILED
        assert condition["code"] == "EXECUTION_CONFIG_UNKNOWN_FIELD"

    def test_a_known_execution_config_field_passes(self):
        response = _get(
            _sb(),
            params={
                **_passing_params(),
                "execution_config": '{"max_order_notional": 500}',
            },
        )

        body = response.json()
        assert _condition(body, "execution_config")["detail"]["fields"] == [
            "max_order_notional"
        ]
        assert body["deployable"] is True

    def test_an_unauthenticated_caller_gets_no_condition_data(self):
        """Requirement 22.4: no strategy data on an authorization failure."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend_app.routers import strategy_operations as ops

        app = FastAPI()
        app.include_router(ops.router, prefix="/api")
        app.state.limiter = ops.limiter
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(PREFLIGHT_PATHS[0])

        assert response.status_code in (401, 403)
        assert "conditions" not in response.text
        assert "deployable" not in response.text


# ---------------------------------------------------------------------------
# 7. The preflight and the deploy cannot disagree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheTwoSurfacesAgree:
    async def test_a_deployable_summary_is_followed_by_a_deploy_that_succeeds(self):
        sb = _sb()
        assert _get(sb, params=_passing_params()).json()["deployable"] is True

        result, _fleet = await _deploy(sb, payload=_passing_params())

        assert result.get("success") is True
        assert sb.inserted("strategy_deployments")

    async def test_the_preflight_reads_the_same_gates_the_write_path_does(self):
        """Every declared binding condition appears in the response, in order."""
        body = _get(_sb(), params=_passing_params()).json()

        names = [c["name"] for c in body["conditions"]]
        assert names == [DEPLOY_PREREQUISITE_CONDITION, *db.BINDING_CONDITIONS]

    async def test_both_registered_paths_answer_identically(self):
        sb = _sb()

        first = _get(sb, params=_passing_params(), path=PREFLIGHT_PATHS[0])
        second = _get(sb, params=_passing_params(), path=PREFLIGHT_PATHS[1])

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
