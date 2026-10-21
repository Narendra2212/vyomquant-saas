"""The referrer's balance must not be debited without a payout record to show for it.

PHASE 10 / WORKSTREAM D / observation 2 -- SUPERSEDED BY REF-01's ATOMIC REDESIGN.

WHAT THIS FILE USED TO TEST
----------------------------
Before ``049_ref01_payout_atomic.sql``, ``create_payout_request`` issued the wallet
debit and the ``referral_payouts`` INSERT as two separate PostgREST statements, with a
best-effort compensating re-credit reachable only through
``if not payout_resp.data or len(payout_resp.data) == 0``. An insert that *raised*
(rather than returning empty) skipped that branch entirely, jumped to the handler's
``except Exception``, and left the wallet debited with no payout row and no
compensation. This file drove that exact gap with a stub whose insert raises.

WHY IT NO LONGER TESTS THAT GAP
--------------------------------
The handler no longer performs a separate debit-then-insert at all: both are now ONE
call to ``public.create_referral_payout_request``
(``backend_app/migrations/049_ref01_payout_atomic.sql``), a single
``SECURITY DEFINER`` Postgres function that debits the wallet and inserts the payout
row inside one transaction. If the insert fails for any reason, the debit the SAME
function body already issued rolls back with it -- there is no window in which the
money is out of the wallet and nowhere else, so there is nothing left for a
compensating re-credit to compensate, and ``routers/referral.py`` no longer has one.

The real atomicity guarantee is now a database property, exercised against a REAL
PostgreSQL server in ``tests/test_ref01_payout_atomic_postgres.py`` (which drives the
RPC directly and asserts, over an independent connection, that a rejected call leaves
both tables completely untouched). A stub that fakes individual ``.table(...)`` calls
cannot exercise "two statements in one transaction" at all, because there is only one
statement now: a single ``.rpc(...)`` call.

WHAT THIS FILE TESTS INSTEAD
------------------------------
That the HANDLER calls the RPC (not the old two-step table sequence) and, when that
RPC call raises for any reason, answers an error status and claims no payout id --
never reporting success for money that was never moved. This is the handler-level half
of the guarantee; the database-level half (the transaction itself) is
``test_ref01_payout_atomic_postgres.py``'s job.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

USER_ID = "11111111-1111-4111-8111-111111111111"
PAYOUT_AMOUNT = 50.0


class _Resp:
    def __init__(self, data):
        self.data = data


class _Rpc:
    """One ``.rpc(name, params)`` call. Terminates in ``execute()``."""

    def __init__(self, name: str, params: dict, log: dict):
        self._name = name
        self._params = params
        self._log = log

    def execute(self):
        self._log["rpc_calls"].append((self._name, dict(self._params)))
        mode = self._log["mode"]
        if self._name == "create_referral_payout_request":
            if mode == "raise":
                # What a failed RPC call actually surfaces: the client raises. The
                # SQLSTATE here is deliberately NOT one of RF001-RF004, so the handler
                # falls to its generic 500 branch -- the real-database tests already
                # cover each specific RFxxx classification.
                raise RuntimeError(
                    "APIError: {'code': '53300', 'message': 'too many connections'}"
                )
            if mode == "empty":
                return _Resp(None)
            return _Resp({
                "id": "payout-1",
                "status": "pending",
                "amount_usd": PAYOUT_AMOUNT,
                "payout_method": self._params.get("p_payout_method"),
            })
        return _Resp(None)


class _Query:
    """A ``.table(...)`` chain. Nothing in the redesigned handler reaches this for a
    payout request any more; present only so an unrelated call does not crash the stub.
    """

    def __init__(self, table: str, log: dict):
        self._table = table
        self._log = log

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def offset(self, *a, **k):
        return self

    def update(self, payload):
        return self

    def insert(self, payload):
        return self

    def execute(self):
        self._log["table_calls"].append(self._table)
        return _Resp([])


class _Supabase:
    def __init__(self, log: dict):
        self._log = log

    def table(self, name):
        return _Query(name, self._log)

    def rpc(self, name, params):
        return _Rpc(name, params, self._log)


@pytest.fixture
def harness(monkeypatch):
    """Overrides the app's auth + db dependencies and neutralises the Redis lock."""
    log = {"rpc_calls": [], "table_calls": [], "mode": "ok"}

    import backend_app.core.cache as cache_mod

    fake_redis = MagicMock()

    async def _set(*a, **k):
        return True

    async def _delete(*a, **k):
        return 1

    fake_redis.set = _set
    fake_redis.delete = _delete
    monkeypatch.setattr(cache_mod, "redis_manager", fake_redis, raising=True)

    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_ID, "email": "p10d@example.invalid", "role": "authenticated",
    }
    app.dependency_overrides[get_request_supabase] = lambda: _Supabase(log)
    try:
        yield log
    finally:
        app.dependency_overrides.clear()


def _request_payout(log, mode):
    log["mode"] = mode
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/api/referral/payouts",
        json={
            "amount_usd": PAYOUT_AMOUNT,
            "payment_method": "bank_transfer",
            "payment_details": {"account": "xxxx"},
        },
    )


def test_a_successful_payout_calls_the_atomic_rpc_exactly_once(harness):
    """The handler issues ONE rpc call -- not a debit statement plus an insert
    statement -- for the happy path."""
    log = harness
    resp = _request_payout(log, "ok")

    assert resp.status_code == 200, resp.text
    assert log["rpc_calls"] == [
        ("create_referral_payout_request", {
            "p_user_id": USER_ID,
            "p_amount_usd": PAYOUT_AMOUNT,
            "p_payout_method": "bank_transfer",
            "p_payout_details": {"account": "xxxx"},
        })
    ], "the handler no longer issues the two-statement debit-then-insert sequence"
    assert resp.json()["payout_id"] == "payout-1"


def test_an_rpc_call_that_raises_claims_no_success_and_touches_no_table(harness):
    """THE PROPERTY THAT REPLACES THE OLD COMPENSATION TEST.

    When the atomic RPC raises, the handler answers an error and never falls back to
    the old table-level statements -- there is no partial state for it to compensate,
    because the (now single) statement never committed in the first place.
    """
    log = harness
    resp = _request_payout(log, "raise")

    assert resp.status_code >= 400, resp.text
    body = resp.json()
    assert body.get("status") != "success"
    assert "payout_id" not in body
    assert log["table_calls"] == [], (
        "the handler fell back to a direct .table(...) write after the RPC failed; "
        "the redesign's whole point is that there is nothing left to compensate, so "
        "there should be no such fallback"
    )


def test_an_rpc_call_that_returns_no_row_is_also_reported_as_a_failure(harness):
    log = harness
    resp = _request_payout(log, "empty")

    assert resp.status_code >= 400, resp.text
    body = resp.json()
    assert body.get("status") != "success"
    assert "payout_id" not in body
