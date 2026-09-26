"""
tests/test_strategy_status_publish.py

production-launch-hardening task 8.5 - the backend half of the STRATEGY_STATUS contract.
Requirements 1.24, 2.24.

THE DEFECT THIS REGRESSION TEST HOLDS DOWN
-------------------------------------------
Nothing in the tree published a ``STRATEGY_STATUS`` update before this task. The only
occurrence was a docstring at ``api_ws/ws_routes.py``'s ``broadcast_dashboard_update``,
describing a call no service made - the frontend's status contract (already spelled
``STRATEGY_STATUS`` at ``websocketClient.js:1202-1205``) was structurally satisfied and
operationally dead. This file asserts the other half is now true: a start and a stop each
publish **exactly one** frame whose ``type`` is the string the frontend subscribes to, over
``app_state.ws.broadcast_user`` - never wrapped as ``broadcast_dashboard_update`` would wrap
it, which is the specific wrong-envelope failure mode
``tests/fixtures/strategy_status_frame.json`` (task 8.4, read directly below rather than
hand-typed a second time) exists to rule out.

WHAT IS DOUBLED, AND WHY
-------------------------
``app_state.ws.broadcast_user`` is stubbed to CAPTURE calls rather than open a socket - this
environment has no live websocket peer, and the assertion is about what
``strategy_service.py`` calls, not about a network hop. The PostgREST client, the fleet and
``get_strategy`` are doubled the same way
``tests/test_task_8_2_deployment_binding.py::TestLegacyStrategyDeploy`` already doubles them
for the same two methods - a minimal ``_Supabase`` that honours ``.eq`` and applies updates
(``tests/test_task_8_3_lifecycle_state_machine.py``'s shape), ``get_strategy`` replaced with
an ``AsyncMock`` returning a version already past the deploy-prerequisite gate, and
``backend_app.core.state.app_state`` patched with a bare object carrying only ``.fleet`` and
``.ws`` - deliberately NOT a ``MagicMock``, so this file is also proof the guard added to
``_publish_strategy_status`` works against the same shape of double the failed-deploy branch
below already needs.

THE FAILED-DEPLOY JUDGEMENT CALL
---------------------------------
The task's phrasing ("one start ... each publish exactly one frame") is read here as
covering BOTH terminal outcomes of a start attempt, not only the successful one: a strategy
that fails to come up is exactly the state Live Trading exists to show a trader, and a
deploy attempt whose failure is invisible to the frontend is a second, quieter version of
1.24's own defect. ``test_a_failed_start_also_publishes_exactly_one_frame`` asserts this
explicitly and separately from the two the task names outright, so a reviewer who reads the
task the narrower way can see exactly what is being asserted beyond it.
"""

import os
import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.strategy_service import StrategyService

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "strategy_status_frame.json"

USER_ID = "user_task_8_5"
STRATEGY_ID = "strategy_task_8_5"
VERSION_ID = "77777777-7777-4777-8777-777777777777"
DEPLOYMENT_ID = "88888888-8888-4888-8888-888888888888"


# ---------------------------------------------------------------------------
# The fixture, read once - not hand-typed a second time.
# ---------------------------------------------------------------------------


def _fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = _fixture()


# ---------------------------------------------------------------------------
# A minimal PostgREST double - honours .eq, APPLIES updates, records inserts.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._mode = "select"
        self._payload = None

    def select(self, *_a, **_kw):
        self._mode = "select"
        return self

    def insert(self, payload, *_a, **_kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *_a, **_kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *_a, **_kw):
        return self

    def _matches(self, row):
        return all(str(row.get(k)) == str(v) for k, v in self._filters.items())

    def execute(self):
        rows = self._parent.rows.setdefault(self._table, [])
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", DEPLOYMENT_ID)
            rows.append(row)
            self._parent.inserts.append((self._table, dict(row)))
            return _Result([row])
        if self._mode == "update":
            touched = []
            for row in rows:
                if self._matches(row):
                    row.update(dict(self._payload))
                    touched.append(row)
            self._parent.updates.append(
                (self._table, dict(self._payload), dict(self._filters))
            )
            return _Result(touched)
        return _Result([row for row in rows if self._matches(row)])


class _Supabase:
    def __init__(self, rows=None):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)

    def row(self, table, row_id):
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None


# ---------------------------------------------------------------------------
# app_state - a bare object with only .fleet and .ws, not a MagicMock.
#
# This is deliberately NOT unittest.mock.MagicMock: a MagicMock auto-vivifies `.ws` even
# when nothing set it, which is exactly the shape of double that made the guard in
# `_publish_strategy_status` necessary in the first place (see its own docstring, and
# `tests/test_task_8_2_deployment_binding.py::TestLegacyStrategyDeploy`, re-run unmodified
# in this task's verification pass). Using a plain object with the two attributes actually
# set keeps this file's own doubles honest about what strategy_service.py is really given
# in production - a real AppState with a real `.ws`.
# ---------------------------------------------------------------------------


class _AppState:
    pass


def _user():
    return {"id": USER_ID, "email": f"{USER_ID}@example.com", "access_token": "tok"}


def _version_data():
    """A version already past the deploy-prerequisite gate (Requirement 10.4)."""
    return {
        "id": VERSION_ID,
        "version": "v1.0",
        "validation_state": "VALID",
        "dag_hash": "deadbeefcafe1234",
        "compiled_plan": {"nodes": []},
        "blueprint": {"nodes": []},
    }


def _service_with(sb, ws_mock, *, fleet_result=(True, "started")):
    """A ``StrategyService`` whose ``get_strategy`` and PostgREST client are doubled.

    ``_get_supabase`` is stubbed directly rather than left to call
    ``create_request_supabase_async`` for real - that function opens a live network
    connection, which this environment does not have (and must not depend on: the
    assertion here is about ``strategy_service.py``'s own publish call, not about
    reaching a real Supabase project).
    """
    service = StrategyService()
    service.get_strategy = AsyncMock(
        return_value={
            "strategy": {"id": STRATEGY_ID, "symbol": "BTC/USDT", "pair": "BTC/USDT"},
            "version": _version_data(),
        }
    )
    service._get_supabase = AsyncMock(return_value=sb)

    fleet = MagicMock()
    fleet.start_bot = AsyncMock(return_value=fleet_result)
    fleet.stop_bot = AsyncMock(return_value=(True, "stopped"))

    app_state = _AppState()
    app_state.fleet = fleet
    app_state.ws = ws_mock

    return service, app_state


def _deployed_row(*, environment="paper"):
    return {
        "id": DEPLOYMENT_ID,
        "strategy_id": STRATEGY_ID,
        "user_id": USER_ID,
        "environment": environment,
        "exchange_id": "binance",
        "status": "running",
    }


# ---------------------------------------------------------------------------
# The fixture's own contract, checked against what this file assumes about it.
# ---------------------------------------------------------------------------


def test_the_fixture_declares_the_upper_case_type_the_frontend_subscribes_to():
    assert FIXTURE["keys"]["type"]["const"] == "STRATEGY_STATUS"
    assert FIXTURE["example"]["type"] == "STRATEGY_STATUS"


# ---------------------------------------------------------------------------
# 8.5's own regression test: one start, one stop, each exactly one frame.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_start_publishes_exactly_one_strategy_status_frame():
    sb = _Supabase()
    ws = MagicMock()
    ws.broadcast_user = AsyncMock(return_value=None)
    service, app_state = _service_with(sb, ws)

    with patch("backend_app.core.state.app_state", app_state):
        await service.deploy_strategy(user=_user(), strategy_id=STRATEGY_ID, environment="paper")

    assert ws.broadcast_user.await_count == 1
    (user_id, frame), _kwargs = ws.broadcast_user.await_args
    assert user_id == USER_ID
    assert frame["type"] == FIXTURE["example"]["type"] == "STRATEGY_STATUS"
    assert frame["status"] == "running"
    assert frame["strategy_id"] == STRATEGY_ID


@pytest.mark.asyncio
async def test_a_failed_start_also_publishes_exactly_one_frame():
    """The judgement call: a start attempt's failure is still a transition worth a push."""
    sb = _Supabase()
    ws = MagicMock()
    ws.broadcast_user = AsyncMock(return_value=None)
    service, app_state = _service_with(
        sb, ws, fleet_result=(False, "no worker capacity")
    )

    with patch("backend_app.core.state.app_state", app_state):
        await service.deploy_strategy(user=_user(), strategy_id=STRATEGY_ID, environment="paper")

    assert ws.broadcast_user.await_count == 1
    (_user_id, frame), _kwargs = ws.broadcast_user.await_args
    assert frame["type"] == "STRATEGY_STATUS"
    assert frame["status"] == "failed"
    assert frame["error"] == "no worker capacity"


@pytest.mark.asyncio
async def test_a_stop_publishes_exactly_one_strategy_status_frame():
    sb = _Supabase({"strategy_deployments": [_deployed_row(environment="live")]})
    ws = MagicMock()
    ws.broadcast_user = AsyncMock(return_value=None)
    service, app_state = _service_with(sb, ws)
    service._get_supabase = AsyncMock(return_value=sb)

    with patch("backend_app.core.state.app_state", app_state):
        result = await service.stop_deployment(user=_user(), deployment_id=DEPLOYMENT_ID)

    assert result is True
    assert ws.broadcast_user.await_count == 1
    (user_id, frame), _kwargs = ws.broadcast_user.await_args
    assert user_id == USER_ID
    assert frame["type"] == "STRATEGY_STATUS"
    assert frame["status"] == "stopped"
    assert frame["strategy_id"] == STRATEGY_ID
    assert frame["environment"] == "live"

    # The write this publish follows actually committed - not a push racing ahead of it.
    assert sb.row("strategy_deployments", DEPLOYMENT_ID)["status"] == "stopped"


# ---------------------------------------------------------------------------
# The guard: app_state.ws absent must not turn a status push into a failed request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_start_does_not_crash_when_app_state_has_no_ws_attribute_at_all():
    """The exact shape of double several existing deploy_strategy callers already use.

    `tests/test_task_8_2_deployment_binding.py::TestLegacyStrategyDeploy` and
    `tests/test_task_4_3_deploy_prerequisite_gate.py` patch `app_state` with a bare
    `MagicMock` and set only `.fleet`. Reproduced here with an object that has NO `.ws`
    attribute at all (the strictest case `hasattr` alone is meant to catch) to assert the
    deploy still completes and returns its normal result.
    """
    sb = _Supabase()
    service = StrategyService()
    service.get_strategy = AsyncMock(
        return_value={
            "strategy": {"id": STRATEGY_ID, "symbol": "BTC/USDT", "pair": "BTC/USDT"},
            "version": _version_data(),
        }
    )
    service._get_supabase = AsyncMock(return_value=sb)
    fleet = MagicMock()
    fleet.start_bot = AsyncMock(return_value=(True, "started"))

    class _NoWsAppState:
        pass

    app_state = _NoWsAppState()
    app_state.fleet = fleet
    assert not hasattr(app_state, "ws")

    with patch("backend_app.core.state.app_state", app_state):
        result = await service.deploy_strategy(
            user=_user(), strategy_id=STRATEGY_ID, environment="paper"
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_a_start_does_not_crash_when_app_state_is_a_bare_magicmock():
    """`app_state.ws` auto-vivifies as a non-awaitable `MagicMock` attribute here - the
    shape that made the plain `hasattr` guard alone insufficient. Must not raise.
    """
    sb = _Supabase()
    service = StrategyService()
    service.get_strategy = AsyncMock(
        return_value={
            "strategy": {"id": STRATEGY_ID, "symbol": "BTC/USDT", "pair": "BTC/USDT"},
            "version": _version_data(),
        }
    )
    service._get_supabase = AsyncMock(return_value=sb)

    with patch("backend_app.core.state.app_state") as mock_app_state:
        fleet = MagicMock()
        fleet.start_bot = AsyncMock(return_value=(True, "started"))
        mock_app_state.fleet = fleet

        result = await service.deploy_strategy(
            user=_user(), strategy_id=STRATEGY_ID, environment="paper"
        )

    assert result["success"] is True
