"""The referrer's balance must not be debited without a payout record to show for it.

PHASE 10 / WORKSTREAM D / observation 2, the part the column-name fix does not reach.

THE DEFECT
----------
``backend_app/routers/referral.py::create_payout_request`` performs, in order:

  1. debit  ``referral_wallets.approved_balance_usd``  (a CAS update guarded by ``.gte()``)
  2. insert ``referral_payouts``
  3. if the insert RETURNED NO ROWS, re-credit the wallet and answer 500

Step 3 is the compensating action for step 1, and it is reachable **only** through
``if not payout_resp.data or len(payout_resp.data) == 0``. An insert that *raises* never
produces a ``payout_resp`` at all: control jumps straight to the handler's
``except Exception`` clause, which logs and answers 500 **without re-crediting**.

So any raising failure of the payout insert -- a PostgREST error, an RLS denial, a CHECK or
FK violation, a timeout, a 5xx from Supabase -- leaves the customer's approved balance
reduced with **no payout row of any kind**. The money is simply gone from the balance, and
nothing reconciles it: there is no referral audit table and no settlement job.

This is independent of the ``payout_method``/``payment_method`` column fix. That fix removed
the *then-certain* cause of the raising insert; it did not make the debit recoverable when
the insert fails for any of the other reasons above.

WHAT THESE TESTS DO
-------------------
Drive the real handler through the real app with the dependencies overridden, and a stub
Supabase client whose ``referral_payouts`` insert raises. No database, no network, no
payment initiated, no money moved.

``test_a_returned_empty_insert_is_compensated`` is the NEGATIVE CONTROL: it exercises the
path that already works, so a failure of the test below cannot be blamed on the harness.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

USER_ID = "11111111-1111-4111-8111-111111111111"
OPENING_BALANCE = 500.0
PAYOUT_AMOUNT = 50.0


# ─────────────────────────────────────────────────────────────────────────────
#  A Supabase stub that records what it was asked to write
# ─────────────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """One fluent chain. Terminates in ``execute()``."""

    def __init__(self, table: str, log: dict):
        self._table = table
        self._log = log
        self._op = None
        self._payload = None

    # Every builder step is a no-op that keeps the chain going.
    def select(self, *a, **k):
        self._op = "select"
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
        self._op = "update"
        self._payload = payload
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def execute(self):
        self._log["calls"].append((self._table, self._op, self._payload))

        if self._table == "referral_wallets" and self._op == "select":
            return _Resp([{
                "user_id": USER_ID,
                "pending_balance_usd": 0,
                "approved_balance_usd": self._log["balance"],
                "paid_balance_usd": 0,
                "lifetime_earnings_usd": OPENING_BALANCE,
            }])

        if self._table == "referral_wallets" and self._op == "update":
            # The real CAS update returns the affected row. Record the new balance so the
            # test can see both the debit and any compensating re-credit.
            if "approved_balance_usd" in self._payload:
                self._log["balance"] = float(self._payload["approved_balance_usd"])
                self._log["balance_writes"].append(self._log["balance"])
            return _Resp([{"user_id": USER_ID,
                           "approved_balance_usd": self._log["balance"]}])

        if self._table == "referral_payouts" and self._op == "insert":
            mode = self._log["payout_insert"]
            if mode == "raise":
                # What PostgREST actually does on a rejected write: the client raises.
                raise RuntimeError(
                    "APIError: {'code': 'PGRST301', 'message': 'insert rejected'}"
                )
            if mode == "empty":
                return _Resp([])
            return _Resp([{"id": "payout-1"}])

        return _Resp([])


class _Supabase:
    def __init__(self, log: dict):
        self._log = log

    def table(self, name):
        return _Query(name, self._log)


@pytest.fixture
def harness(monkeypatch):
    """Overrides the app's auth + db dependencies and neutralises the Redis lock."""
    log = {
        "calls": [],
        "balance": OPENING_BALANCE,
        "balance_writes": [],
        "payout_insert": "ok",
    }

    # The handler takes a 30s Redis lock. Without Redis it already fails open
    # (lock_acquired = True), but it would first attempt a real connection, so the
    # manager is replaced outright to keep the test hermetic and fast.
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
    log["payout_insert"] = mode
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/api/referral/payouts",
        json={
            "amount_usd": PAYOUT_AMOUNT,
            "payment_method": "bank_transfer",
            "payment_details": {"account": "xxxx"},
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Control 1 — the happy path debits exactly once
# ─────────────────────────────────────────────────────────────────────────────
def test_a_successful_payout_debits_the_balance_once(harness):
    log = harness
    resp = _request_payout(log, "ok")

    assert resp.status_code == 200, resp.text
    assert log["balance"] == pytest.approx(OPENING_BALANCE - PAYOUT_AMOUNT), (
        "a successful payout did not debit the approved balance exactly once"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Control 2 — the path the handler ALREADY compensates
# ─────────────────────────────────────────────────────────────────────────────
def test_an_insert_that_returns_no_rows_is_compensated(harness):
    """NEGATIVE CONTROL for the test below.

    The handler's existing rollback covers this case, so it must pass. If it fails, the
    harness is wrong and the verdict of the next test means nothing.
    """
    log = harness
    resp = _request_payout(log, "empty")

    assert resp.status_code == 500
    assert log["balance"] == pytest.approx(OPENING_BALANCE), (
        "the handler's own rollback path did not restore the balance; the harness is "
        "not driving the handler correctly"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  THE DEFECT
# ─────────────────────────────────────────────────────────────────────────────
def test_an_insert_that_raises_is_also_compensated(harness):
    """THE ONE THAT MATTERS. A raising insert must not keep the debit.

    Only the failure MODE differs from the control above: same amount, same balance, same
    handler. If this fails while the control passes, the compensating re-credit is guarded
    by ``if not payout_resp.data`` and does not cover an exception.
    """
    log = harness
    resp = _request_payout(log, "raise")

    assert resp.status_code == 500, resp.text

    assert log["balance"] == pytest.approx(OPENING_BALANCE), (
        f"THE REFERRER'S MONEY IS GONE. approved_balance_usd went "
        f"{OPENING_BALANCE} -> {log['balance']} and NO referral_payouts row was created "
        f"(the insert raised). Balance writes seen: {log['balance_writes']}. The "
        f"compensating re-credit at routers/referral.py is reachable only via "
        f"`if not payout_resp.data`, so an insert that RAISES skips it and falls to "
        f"`except Exception`, which only logs."
    )


def test_no_payout_row_is_claimed_when_the_insert_failed(harness):
    """The response must not report success for a payout that was never recorded."""
    log = harness
    resp = _request_payout(log, "raise")

    assert resp.status_code >= 400, (
        "the handler reported success for a payout whose insert raised"
    )
    body = resp.json()
    assert body.get("status") != "success"
    assert "payout_id" not in body
