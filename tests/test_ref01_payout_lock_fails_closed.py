"""REF-01 -- a Redis lock-acquisition ERROR now fails CLOSED, not open.

THE DEFECT
    backend_app/routers/referral.py::create_payout_request caught any exception from
    the Redis lock-acquisition call and set `lock_acquired = True` -- fail OPEN.
    core/cache/redis_manager.py's own `set()` is deliberately fail-soft (it swallows
    every exception itself and returns False), so this except clause could never
    actually fire through the real manager, making the fail-open branch dead code
    today -- but a landmine: RED-01's own remaining_action proposes making
    redis_manager raise on error for exactly this class of safety-gate caller, and if
    that ever lands, this dead branch would silently come alive and reintroduce the
    double-spend race it exists to prevent.

THE FIX
    The except clause now sets `lock_acquired = False` -- fail closed. The wallet's
    own atomic conditional UPDATE (`.gte("approved_balance_usd", ...)`) remains the
    real correctness backstop against a double-spend regardless of this lock; failing
    closed here only costs a "please wait" on a genuine Redis error, never a double-
    spend.

WHAT THIS FILE PROVES
    A lock-acquisition call that RAISES (not the normal "key already held" case,
    which returns None/False without raising) results in the SAME refusal as a held
    lock -- 409, "please wait" -- and, critically, NO debit of the wallet balance:
    the request never reaches the debit step at all.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

USER_ID = "22222222-2222-4222-8222-222222222222"
OPENING_BALANCE = 500.0
PAYOUT_AMOUNT = 50.0


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: str, log: dict):
        self._table = table
        self._log = log
        self._op = None
        self._payload = None

    def select(self, *a, **k):
        self._op = "select"
        return self

    def eq(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def execute(self):
        self._log["calls"].append((self._table, self._op))
        if self._table == "referral_wallets" and self._op == "select":
            return _Resp([{
                "user_id": USER_ID,
                "approved_balance_usd": self._log["balance"],
            }])
        if self._table == "referral_wallets" and self._op == "update":
            self._log["balance_debited"] = True
            return _Resp([{"user_id": USER_ID, "approved_balance_usd": self._log["balance"]}])
        if self._table == "referral_payouts" and self._op == "insert":
            return _Resp([{"id": "payout-1"}])
        return _Resp([])


class _Supabase:
    def __init__(self, log: dict):
        self._log = log

    def table(self, name):
        return _Query(name, self._log)


@pytest.fixture
def harness(monkeypatch):
    log = {"calls": [], "balance": OPENING_BALANCE, "balance_debited": False}

    import backend_app.core.cache as cache_mod

    fake_redis = MagicMock()

    async def _set_raises(*a, **k):
        raise ConnectionError("Redis is unreachable")

    async def _delete(*a, **k):
        return 1

    fake_redis.set = _set_raises
    fake_redis.delete = _delete
    monkeypatch.setattr(cache_mod, "redis_manager", fake_redis, raising=True)

    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_ID, "email": "ref01@example.invalid", "role": "authenticated",
    }
    app.dependency_overrides[get_request_supabase] = lambda: _Supabase(log)
    try:
        yield log
    finally:
        app.dependency_overrides.clear()


def test_a_lock_acquisition_error_refuses_the_payout_rather_than_proceeding(harness):
    log = harness
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/referral/payouts",
        json={
            "amount_usd": PAYOUT_AMOUNT,
            "payment_method": "bank_transfer",
            "payment_details": {"account": "xxxx"},
        },
    )

    assert resp.status_code == 409, resp.text
    assert "wait" in resp.text.lower()


def test_a_lock_acquisition_error_never_reaches_the_debit_step(harness):
    log = harness
    client = TestClient(app, raise_server_exceptions=False)

    client.post(
        "/api/referral/payouts",
        json={
            "amount_usd": PAYOUT_AMOUNT,
            "payment_method": "bank_transfer",
            "payment_details": {"account": "xxxx"},
        },
    )

    assert log["balance_debited"] is False, (
        "a Redis lock-acquisition ERROR must refuse the request before ever touching "
        "the wallet -- fail open here would let a request through with no lock held "
        "at all"
    )
    assert log["balance"] == pytest.approx(OPENING_BALANCE)
