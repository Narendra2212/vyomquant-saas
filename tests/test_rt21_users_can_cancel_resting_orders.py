"""RT-21 -- a live user can cancel resting orders (single and all), in every platform mode.

THE DEFECT (two independent root causes)
    1. ``ExecutionGuard`` rejected the ``side='cancel'|'cancel_all'`` / ``size=0`` signal the
       routes build ("Invalid side", "size must be > 0"), even with ``reduce_only=True``.
    2. Behind the guard, ``_build_execution_engine`` raised 403 unconditionally, and
       ``ExecutionEngine`` has no ``cancel_order`` / ``cancel_all`` at all.

SCOPE OF THE FIX (and of this file)
    Cancel and cancel-all work, are idempotent, audited, tenant-scoped and available in
    ``safe`` mode, under a tenant halt and with the kill switch on. Offline: a scripted ccxt
    client stands in for the venue -- NOTHING here talks to an exchange, so "the venue shows
    the order cancelled" is NOT established (needs a sandbox: EXTERNAL). Close-all (flatten)
    is an order placement and is NOT part of this change.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import ccxt
import pytest
from fastapi.testclient import TestClient

from backend_app.backend.exchange_executor import CCXTExchangeExecutor
from backend_app.backend.execution_guard import ExecutionGuard, ValidationSeverity
from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from tests.test_trade04_live_order_gate_at_venue_boundary import _Normalizer, _RateLimiter

USER = str(uuid.uuid4())


# ── scripted venue ───────────────────────────────────────────────────────────


class ScriptedVenue:
    def __init__(self, *, open_orders=None, cancel_outcomes=None, list_error=None):
        self.open_orders = list(open_orders or [])
        self.cancel_outcomes = dict(cancel_outcomes or {})
        self.list_error = list_error
        self.cancel_calls = []
        self.created = []

    async def cancel_order(self, order_id, symbol=None):
        self.cancel_calls.append((order_id, symbol))
        out = self.cancel_outcomes.get(order_id, "ok")
        if isinstance(out, BaseException):
            raise out
        return {"id": order_id, "status": "canceled"}

    async def fetch_open_orders(self, symbol=None):
        if self.list_error:
            raise self.list_error
        return [o for o in self.open_orders if symbol in (None, o.get("symbol"))]

    async def create_order(self, *a, **k):          # must never be called by this feature
        self.created.append(a)
        raise AssertionError("order_control placed an order")


def make_executor(venue):
    ex = object.__new__(CCXTExchangeExecutor)
    ex._exchange = venue
    ex._connected = True
    ex.exchange_id = "binance"
    ex.sandbox = False
    ex.normalizer = _Normalizer()
    ex.rate_limiter = _RateLimiter()
    ex._api_key, ex._api_secret, ex._password = "k", "s", None
    return ex


def run(coro):
    return asyncio.run(coro)


# ── 1. the guard: control actions are valid, orders are not affected ──────────


def _guard_report(side, *, reduce_only, symbol="BTC/USDT"):
    g = ExecutionGuard(redis_client=None)
    return run(g.validate_trade(
        tenant_id="t1",
        signal={"symbol": symbol, "side": side, "size": "0", "price": "0"},
        portfolio_state={}, market_state={}, reduce_only=reduce_only,
    ))


@pytest.mark.parametrize("side", ["cancel", "cancel_all", "close_all"])
def test_guard_admits_a_control_action_that_is_declared_reduce_only(side):
    report = _guard_report(side, reduce_only=True)
    assert report.execution_allowed is True, report.blocked_reasons


def test_guard_still_refuses_the_same_signal_when_reduce_only_is_not_asserted():
    # A caller that does not claim reduce-only gets the order checks, which reject it.
    report = _guard_report("cancel", reduce_only=False)
    assert report.execution_allowed is False


def test_guard_rejects_a_control_action_with_no_symbol():
    report = _guard_report("cancel", reduce_only=True, symbol="")
    assert report.execution_allowed is False


def test_guard_still_rejects_an_order_with_zero_size_even_when_reduce_only():
    g = ExecutionGuard(redis_client=None)
    report = run(g.validate_trade(
        tenant_id="t1", signal={"symbol": "BTC/USDT", "side": "buy", "size": "0", "price": "1"},
        portfolio_state={}, market_state={}, reduce_only=True))
    assert report.execution_allowed is False, "the control bypass must not widen to orders"


def test_guard_admits_a_control_action_while_circuit_breakers_would_block(monkeypatch):
    g = ExecutionGuard(redis_client=None)

    async def tripped(*a, **k):
        from backend_app.backend.execution_guard import ValidationResult
        return ValidationResult("circuit_breakers", False, ValidationSeverity.BLOCK, "kill switch")

    monkeypatch.setattr(g, "_validate_circuit_breakers", tripped)
    report = run(g.validate_trade(
        tenant_id="t1", signal={"symbol": "BTC/USDT", "side": "cancel", "size": "0", "price": "0"},
        portfolio_state={}, market_state={}, reduce_only=True))
    assert report.execution_allowed is True


# ── 2. the executor boundary ──────────────────────────────────────────────────


def test_cancel_success():
    v = ScriptedVenue()
    r = run(make_executor(v).cancel_order("o1", "BTC/USDT"))
    assert (r.success, r.status) == (True, "canceled") and v.cancel_calls == [("o1", "BTC/USDT")]


def test_cancel_of_an_unknown_order_is_idempotent_success():
    v = ScriptedVenue(cancel_outcomes={"o1": ccxt.OrderNotFound("gone")})
    r = run(make_executor(v).cancel_order("o1", "BTC/USDT"))
    assert (r.success, r.status) == (True, "already_closed")


def test_cancel_lost_response_is_unknown_not_success():
    v = ScriptedVenue(cancel_outcomes={"o1": ccxt.RequestTimeout("timeout")})
    r = run(make_executor(v).cancel_order("o1", "BTC/USDT"))
    assert (r.success, r.status) == (False, "unknown")


def test_cancel_refused_by_venue_is_error():
    v = ScriptedVenue(cancel_outcomes={"o1": ccxt.PermissionDenied("nope")})
    r = run(make_executor(v).cancel_order("o1", "BTC/USDT"))
    assert (r.success, r.status) == (False, "error")


def test_cancel_all_reports_each_order_and_continues_after_a_failure():
    v = ScriptedVenue(
        open_orders=[{"id": "a", "symbol": "BTC/USDT"}, {"id": "b", "symbol": "BTC/USDT"},
                     {"id": "c", "symbol": "BTC/USDT"}, {"id": "d", "symbol": "BTC/USDT"}],
        cancel_outcomes={"b": ccxt.PermissionDenied("x"), "c": ccxt.OrderNotFound("g"),
                         "d": ccxt.NetworkError("n")})
    s = run(make_executor(v).cancel_all_open_orders("BTC/USDT"))
    assert s["cancelled"] == ["a"] and s["already_closed"] == ["c"]
    assert [f["order_id"] for f in s["failed"]] == ["b"]
    assert [f["order_id"] for f in s["unknown"]] == ["d"]
    assert s["success"] is False and len(v.cancel_calls) == 4
    assert v.created == []


def test_cancel_all_listing_failure_is_reported_not_swallowed():
    v = ScriptedVenue(list_error=ccxt.NetworkError("down"))
    s = run(make_executor(v).cancel_all_open_orders(None))
    assert s["success"] is False and s["listed"] is False and v.cancel_calls == []


def test_cancel_all_with_nothing_resting_is_a_success():
    s = run(make_executor(ScriptedVenue()).cancel_all_open_orders(None))
    assert s["success"] is True and s["cancelled_count"] == 0


# ── 3. the routes ─────────────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def delete(self, k):
        self.kv.pop(k, None)

    # the guard's audit/report writers
    async def setex(self, *a, **k):
        return True

    async def lpush(self, *a, **k):
        return 1

    async def ltrim(self, *a, **k):
        return True

    async def expire(self, *a, **k):
        return True


class FakeVault:
    def __init__(self):
        self.loaded = []

    def load_decrypted_keys(self, user_id, exchange_id):
        self.loaded.append((user_id, exchange_id))
        return {"api_key": "k", "secret_key": "s"}


@pytest.fixture
def api(monkeypatch):
    from backend_app.core.dependencies import get_vault

    monkeypatch.setenv("VYOMQUANT_MODE", "safe")       # the default: opening orders are blocked
    redis = FakeRedis()
    vault = FakeVault()
    venue = ScriptedVenue()
    executor = make_executor(venue)

    async def _get_client():
        return redis

    monkeypatch.setattr("backend_app.routers.orders.redis_manager.get_client", _get_client)
    monkeypatch.setattr("backend_app.backend.order_control.get_exchange_executor",
                        lambda *a, **k: executor)
    app.dependency_overrides[get_current_user] = lambda: {"id": USER, "email": "u@x.io"}
    app.dependency_overrides[get_vault] = lambda: vault
    try:
        yield SimpleNamespace(client=TestClient(app), venue=venue, redis=redis, vault=vault)
    finally:
        app.dependency_overrides.clear()


H = {"Authorization": "Bearer t"}


def test_cancel_route_cancels_in_safe_mode(api):
    r = api.client.post("/api/orders/cancel/o-1?exchange_id=binance",
                        json={"order_id": "o-1", "symbol": "BTC-USDT"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "canceled"
    assert api.venue.cancel_calls == [("o-1", "BTC/USDT")]
    assert api.vault.loaded == [(USER, "binance")], "credentials of the caller only"


def test_cancel_route_works_while_the_tenant_is_halted_and_the_kill_switch_is_on(api, monkeypatch):
    import backend_app.core.tenant_halt as th
    run(th.set_halt(USER, True))                    # RT-19 halt: new orders are refused
    try:
        assert th.mirror_is_halted(USER) is True
        import backend_app.core.global_safety as gs

        class _On:
            async def is_active(self):
                return True

        monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _On())
        r = api.client.post("/api/orders/cancel/o-1?exchange_id=binance",
                            json={"order_id": "o-1", "symbol": "BTC-USDT"}, headers=H)
        a = api.client.post("/api/orders/cancel-all?exchange_id=binance", json={}, headers=H)
    finally:
        run(th.set_halt(USER, False))
    assert r.status_code == 200, r.text
    assert a.status_code == 200, a.text


def test_cancel_route_reports_an_unconfirmed_cancel_as_an_error_not_success(api):
    api.venue.cancel_outcomes["o-1"] = ccxt.RequestTimeout("t")
    r = api.client.post("/api/orders/cancel/o-1?exchange_id=binance",
                        json={"order_id": "o-1", "symbol": "BTC-USDT"}, headers=H)
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "CANCEL_OUTCOME_UNKNOWN"


def test_cancel_route_replays_an_idempotent_retry_without_calling_the_venue_again(api):
    h = {**H, "Idempotency-Key": "k-1"}
    body = {"order_id": "o-1", "symbol": "BTC-USDT"}
    a = api.client.post("/api/orders/cancel/o-1?exchange_id=binance", json=body, headers=h)
    b = api.client.post("/api/orders/cancel/o-1?exchange_id=binance", json=body, headers=h)
    assert a.status_code == b.status_code == 200
    assert b.json()["replayed"] is True
    assert len(api.venue.cancel_calls) == 1


def test_cancel_route_failure_is_not_replayed(api):
    api.venue.cancel_outcomes["o-1"] = ccxt.RequestTimeout("t")
    h = {**H, "Idempotency-Key": "k-2"}
    body = {"order_id": "o-1", "symbol": "BTC-USDT"}
    assert api.client.post("/api/orders/cancel/o-1?exchange_id=binance", json=body, headers=h).status_code == 502
    api.venue.cancel_outcomes.clear()
    ok = api.client.post("/api/orders/cancel/o-1?exchange_id=binance", json=body, headers=h)
    assert ok.status_code == 200 and len(api.venue.cancel_calls) == 2


def test_cancel_all_route_cancels_every_resting_order(api):
    api.venue.open_orders = [{"id": "a", "symbol": "BTC/USDT"}, {"id": "b", "symbol": "ETH/USDT"}]
    r = api.client.post("/api/orders/cancel-all?exchange_id=binance", json={}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["cancelled"]["cancelled_count"] == 2
    assert sorted(c[0] for c in api.venue.cancel_calls) == ["a", "b"]


def test_cancel_all_route_symbol_filter(api):
    api.venue.open_orders = [{"id": "a", "symbol": "BTC/USDT"}, {"id": "b", "symbol": "ETH/USDT"}]
    r = api.client.post("/api/orders/cancel-all?exchange_id=binance",
                        json={"symbol": "BTC-USDT"}, headers=H)
    assert r.status_code == 200 and [c[0] for c in api.venue.cancel_calls] == ["a"]


def test_cancel_all_route_reports_partial_failure(api):
    api.venue.open_orders = [{"id": "a", "symbol": "BTC/USDT"}, {"id": "b", "symbol": "BTC/USDT"}]
    api.venue.cancel_outcomes["b"] = ccxt.PermissionDenied("x")
    r = api.client.post("/api/orders/cancel-all?exchange_id=binance", json={}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "partial"


def test_the_opening_path_is_still_blocked_and_no_order_is_ever_placed(api):
    from backend_app.routers.orders import _build_execution_engine
    with pytest.raises(Exception) as ei:
        run(_build_execution_engine(USER, None, "binance"))
    assert getattr(ei.value, "status_code", None) == 403
    api.client.post("/api/orders/cancel-all?exchange_id=binance", json={}, headers=H)
    assert api.venue.created == []
