"""REF-01 -- the pending -> completed/rejected payout settlement step.

THE DEFECT
    backend_app/routers/referral.py::create_payout_request writes a
    `referral_payouts` row with status='pending' and NO code path anywhere in this
    file ever transitioned it out of 'pending' -- grepped the whole file for
    "completed" before this fix and found nothing. A payout request could be
    created and then sit forever with no way to record that an operator had
    actually sent the money, or to refuse it and give the debited balance back.

THE FIX
    New PUT /api/referral/payouts/{payout_id}/settle, admin-gated
    (get_admin_user), under the service-role client (referral_payouts and
    referral_wallets both carry an owner-only RLS policy, so an admin's own JWT
    cannot read or write another user's row -- the SEC-08 pattern). 'completed'
    requires an external `reference` (the operator attests they actually performed
    the transfer) and records `processed_at`. 'rejected' re-credits the wallet
    balance this payout's own request had already debited, so the money is not
    left unaccounted for.

WHAT THIS FILE PROVES
    1. A non-admin is refused (403) before touching the database at all.
    2. A pending payout marked 'completed' with a reference transitions to
       'completed', records processed_at, and does NOT touch the wallet (the
       money already left the platform manually -- nothing to re-credit).
    3. 'completed' with no reference is refused (400) -- the attestation is
       mandatory, not optional.
    4. A pending payout marked 'rejected' transitions to 'rejected' AND re-credits
       the wallet by exactly the payout amount.
    5. An already-terminal payout (completed/rejected) cannot be settled again
       (409) -- settlement is a one-way, one-time transition.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_service_supabase
from backend_app.main import app

PAYOUT_ID = "33333333-3333-4333-8333-333333333333"
PAYOUT_USER_ID = "44444444-4444-4444-8444-444444444444"
PAYOUT_AMOUNT = 75.0
OPENING_WALLET_BALANCE = 120.0


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: str, state: dict):
        self._table = table
        self._state = state
        self._op = None
        self._payload = None
        self._filters = []

    def select(self, *a, **k):
        self._op = "select"
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def execute(self):
        self._state["calls"].append((self._table, self._op, dict(self._filters), self._payload))

        if self._table == "referral_payouts" and self._op == "select":
            return _Resp([dict(self._state["payout"])])

        if self._table == "referral_payouts" and self._op == "update":
            # Simulate the .eq("status", <expected current status>) optimistic guard.
            expected_status = dict(self._filters).get("status")
            if expected_status is not None and expected_status != self._state["payout"]["status"]:
                return _Resp([])
            self._state["payout"].update(self._payload)
            return _Resp([dict(self._state["payout"])])

        if self._table == "referral_wallets" and self._op == "select":
            return _Resp([{"approved_balance_usd": self._state["wallet_balance"]}])

        if self._table == "referral_wallets" and self._op == "update":
            if "approved_balance_usd" in self._payload:
                self._state["wallet_balance"] = float(self._payload["approved_balance_usd"])
                self._state["wallet_updated"] = True
            return _Resp([{"approved_balance_usd": self._state["wallet_balance"]}])

        return _Resp([])


class _Supabase:
    def __init__(self, state: dict):
        self._state = state

    def table(self, name):
        return _Query(name, self._state)


def _make_state(payout_status="pending"):
    return {
        "calls": [],
        "payout": {
            "id": PAYOUT_ID,
            "user_id": PAYOUT_USER_ID,
            "amount_usd": PAYOUT_AMOUNT,
            "status": payout_status,
            "payout_details": {},
        },
        "wallet_balance": OPENING_WALLET_BALANCE,
        "wallet_updated": False,
    }


@pytest.fixture
def admin_client(monkeypatch):
    state = _make_state()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "admin-1", "email": "admin@example.invalid",
        "app_metadata": {"role": "admin"},
    }
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }
    app.dependency_overrides[get_service_supabase] = lambda: _Supabase(state)
    try:
        yield TestClient(app, raise_server_exceptions=False), state
    finally:
        app.dependency_overrides.clear()


def test_a_non_admin_is_refused_before_touching_the_database():
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "plain-user", "email": "user@example.invalid", "app_metadata": {},
    }
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put(
            f"/api/referral/payouts/{PAYOUT_ID}/settle",
            json={"status": "completed", "reference": "TXN-1"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_completing_a_payout_records_reference_and_does_not_touch_the_wallet(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/referral/payouts/{PAYOUT_ID}/settle",
        json={"status": "completed", "reference": "UPI-TXN-998877"},
    )

    assert resp.status_code == 200, resp.text
    assert state["payout"]["status"] == "completed"
    assert state["payout"]["payout_details"]["settlement_reference"] == "UPI-TXN-998877"
    assert state["wallet_updated"] is False, (
        "completing a payout means the operator already sent the money manually -- "
        "there is nothing in the wallet to adjust"
    )


def test_completing_without_a_reference_is_refused(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/referral/payouts/{PAYOUT_ID}/settle",
        json={"status": "completed"},
    )

    assert resp.status_code == 400
    assert state["payout"]["status"] == "pending", "a refused settlement must not mutate the payout"


def test_rejecting_a_payout_recredits_the_wallet_by_the_payout_amount(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/referral/payouts/{PAYOUT_ID}/settle",
        json={"status": "rejected", "reason": "Invalid bank details"},
    )

    assert resp.status_code == 200, resp.text
    assert state["payout"]["status"] == "rejected"
    assert state["payout"]["payout_details"]["rejection_reason"] == "Invalid bank details"
    assert state["wallet_balance"] == pytest.approx(OPENING_WALLET_BALANCE + PAYOUT_AMOUNT), (
        "rejecting a payout must re-credit exactly the amount this payout's own "
        "request debited -- otherwise the money is simply gone"
    )


@pytest.mark.parametrize("terminal_status", ["completed", "rejected"])
def test_an_already_settled_payout_cannot_be_settled_again(monkeypatch, terminal_status):
    state = _make_state(payout_status=terminal_status)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }
    app.dependency_overrides[get_service_supabase] = lambda: _Supabase(state)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put(
            f"/api/referral/payouts/{PAYOUT_ID}/settle",
            json={"status": "completed", "reference": "TXN-2"},
        )
        assert resp.status_code == 409
        assert state["wallet_updated"] is False
    finally:
        app.dependency_overrides.clear()
