"""ADM-01 -- POST /api/admin/reinitialize actually clears the halt, or refuses to.

THE DEFECT
    The handler sent an info alert and unconditionally returned
    ``{"status": "ACTIVE", "message": "Platform reinitialized. Bots can now be
    deployed."}`` -- there was no reference to `GlobalKillSwitch`,
    `get_global_kill_switch`, or any other platform state anywhere in the function. An
    operator calling this while the global kill switch (`POST /api/admin/kill-all`) was
    engaged would be told bots can deploy while every order path
    (`execution_engine.py`, `exchange_executor.place_order`) still refused new orders --
    the exact "halt is not a halt" defect KILL-03 fixed for `/kill-all` itself, now on
    its mirror-image endpoint.

THE FIX
    Route the call to `GlobalKillSwitch.deactivate()`, the same mechanism `/kill-all`
    engages via `activate()`. `deactivate()` enforces a stated reason, an authenticated
    operator with an authorization source, and that every currently-registered hold is
    named in `acknowledge_holds` -- a `KillSwitchResetRefused` surfaces as 409 with the
    refusal code rather than being swallowed into a false "reinitialized". The response
    reports the real, re-read status rather than a fixed string.

HOW THIS FILE MEASURES
    A controllable fake Redis stands in for `GlobalKillSwitch`'s backend, the same
    pattern `tests/test_trade02_kill_switch_fails_closed.py` and
    `tests/test_kill03_authoritative_global_halt.py` use. Every "it clears the halt"
    assertion is paired with a negative control that an unacknowledged hold is refused
    and the switch is demonstrably left engaged, not just that the HTTP call returns
    non-200.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import backend_app.backend.redis_manager as redis_manager_module
import backend_app.core.global_safety as global_safety
import backend_app.core.safety_audit as safety_audit
from backend_app.core.dependencies import get_current_user
from backend_app.core.global_safety import GlobalKillSwitch
from backend_app.main import app

OPERATOR = {
    "id": "op-adm01",
    "email": "operator@example.invalid",
    "aal": "aal2",
    "app_metadata": {"role": "operator"},
}
ADMIN_NOT_OPERATOR = {
    "id": "admin-adm01",
    "email": "admin@example.invalid",
    "aal": "aal2",
    "app_metadata": {"role": "admin"},
}


class FakeRedisDown(Exception):
    pass


class FakeRedis:
    """Minimal fake covering the string/list/set surface `GlobalKillSwitch` uses."""

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}
        self.lists: Dict[str, List[str]] = {}
        self.sets: Dict[str, set] = {}
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise FakeRedisDown("fake redis is down")

    async def get(self, key):
        self._guard()
        return self.store.get(key)

    async def set(self, key, value, nx: bool = False, ex=None):
        self._guard()
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self._guard()
        self.store[key] = value
        return True

    async def delete(self, key):
        self._guard()
        self.store.pop(key, None)
        return 1

    async def lpush(self, key, value):
        self._guard()
        self.lists.setdefault(key, []).insert(0, value)
        return True

    async def lrange(self, key, start, end):
        self._guard()
        items = self.lists.get(key, [])
        return items[start : (None if end == -1 else end + 1)]

    async def expire(self, key, seconds):
        self._guard()
        return True

    async def sadd(self, key, *values):
        self._guard()
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def srem(self, key, *values):
        self._guard()
        self.sets.setdefault(key, set()).difference_update(values)
        return len(values)

    async def smembers(self, key):
        self._guard()
        return set(self.sets.get(key, set()))


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(redis_manager_module, "redis_manager", fake)
    return fake


@pytest.fixture
def switch(backend: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> GlobalKillSwitch:
    """A fresh switch wired as the singleton `admin.py`'s handler resolves at call time."""
    sw = GlobalKillSwitch()
    monkeypatch.setattr(global_safety, "get_global_kill_switch", lambda: sw)
    return sw


@pytest.fixture(autouse=True)
def captured_audit(monkeypatch: pytest.MonkeyPatch) -> List[dict]:
    """Capture durable safety-transition writes instead of hitting a real database."""
    recorded: List[dict] = []

    async def _capture(**kwargs):
        recorded.append(kwargs)
        return "audit-stub-id"

    monkeypatch.setattr(safety_audit, "record_safety_transition", _capture)
    return recorded


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: OPERATOR
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _reinit(client: TestClient, **body_overrides):
    body = {"confirm_code": "REINITIALIZE", "reason": "scheduled maintenance complete"}
    body.update(body_overrides)
    return client.post("/api/admin/reinitialize", json=body)


class TestConfirmCodeAndRole:
    def test_wrong_confirm_code_is_rejected_before_touching_the_switch(self, client, switch):
        res = _reinit(client, confirm_code="WRONG")
        assert res.status_code == 400
        # Nothing should have been attempted against the switch at all.
        assert asyncio.run(switch.is_active()) is False

    def test_non_operator_role_is_rejected(self, switch):
        app.dependency_overrides[get_current_user] = lambda: ADMIN_NOT_OPERATOR
        try:
            c = TestClient(app)
            res = _reinit(c)
            assert res.status_code == 403
            assert "OPERATOR PERMISSION REQUIRED" in res.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestReinitializeActuallyClearsTheHalt:
    def test_clearing_an_engaged_switch_with_its_hold_acknowledged_succeeds(
        self, client, switch
    ):
        asyncio.run(
            switch.activate(
                reason="test halt",
                triggered_by="test_harness",
                operator_id="op-x",
                operator_email="op@example.invalid",
                authorization_source="fastapi:get_operator_user",
            )
        )
        assert asyncio.run(switch.is_active()) is True

        res = _reinit(client, acknowledge_holds=["test_harness"])
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "ACTIVE"
        assert body["kill_switch_active"] is False
        assert asyncio.run(switch.is_active()) is False

    def test_an_unacknowledged_hold_is_refused_and_the_switch_stays_engaged(
        self, client, switch
    ):
        """NEGATIVE CONTROL. Proves the response is not just a fixed "success"."""
        asyncio.run(
            switch.activate(
                reason="risk engine fault",
                triggered_by="risk_engine_hold",
                operator_id="op-x",
                operator_email="op@example.invalid",
                authorization_source="fastapi:get_operator_user",
            )
        )
        assert asyncio.run(switch.is_active()) is True

        res = _reinit(client, acknowledge_holds=[])

        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["code"] == "RESET_HOLDS_UNACKNOWLEDGED"
        # The switch must still be engaged: a refusal is not a silent partial clear.
        assert asyncio.run(switch.is_active()) is True

    def test_acknowledging_the_exact_hold_clears_it(self, client, switch):
        asyncio.run(
            switch.activate(
                reason="risk engine fault",
                triggered_by="risk_engine_hold",
                operator_id="op-x",
                operator_email="op@example.invalid",
                authorization_source="fastapi:get_operator_user",
            )
        )

        res = _reinit(client, acknowledge_holds=["risk_engine_hold"])

        assert res.status_code == 200, res.text
        assert res.json()["kill_switch_active"] is False
        assert asyncio.run(switch.get_active_holds()) == set()

    def test_old_unconditional_success_message_is_gone(self, client, switch):
        """Pins that the hardcoded no-op response text is actually gone from the route."""
        import inspect

        from backend_app.routers import admin as admin_router

        source = inspect.getsource(admin_router.reinitialize_platform)
        assert "Bots can now be deployed" not in source
        assert "get_global_kill_switch" in source

    def test_reason_is_required(self, client, switch):
        res = _reinit(client, reason="   ")
        assert res.status_code in (400, 422)

    def test_reinitialize_when_nothing_was_halted_is_a_clean_no_op_success(
        self, client, switch
    ):
        assert asyncio.run(switch.is_active()) is False
        res = _reinit(client)
        assert res.status_code == 200
        assert res.json()["kill_switch_active"] is False

    def test_audit_event_records_the_real_operator_and_reason(self, client, switch):
        with patch(
            "backend_app.routers.admin._log_admin_audit_event", AsyncMock()
        ) as mock_audit:
            res = _reinit(client, reason="verified recovery")
            assert res.status_code == 200
            mock_audit.assert_called_once()
            assert mock_audit.call_args.kwargs["action"] == "PLATFORM_REINITIALIZE"
            assert mock_audit.call_args.kwargs["reason"] == "verified recovery"
            assert mock_audit.call_args.kwargs["operator"] == OPERATOR
