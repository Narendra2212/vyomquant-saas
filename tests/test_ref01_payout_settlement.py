"""REF-01 -- the pending -> completed/rejected payout settlement step, now atomic.

THE DEFECT
    backend_app/routers/referral.py::create_payout_request writes a
    `referral_payouts` row with status='pending' and NO code path anywhere in this
    file ever transitioned it out of 'pending' -- grepped the whole file for
    "completed" before this fix and found nothing. A payout request could be
    created and then sit forever with no way to record that an operator had
    actually sent the money, or to refuse it and give the debited balance back.

THE FIX (ORIGINAL)
    New PUT /api/referral/payouts/{payout_id}/settle, admin-gated
    (get_admin_user), under the service-role client (referral_payouts and
    referral_wallets both carry an owner-only RLS policy, so an admin's own JWT
    cannot read or write another user's row -- the SEC-08 pattern). 'completed'
    requires an external `reference` (the operator attests they actually performed
    the transfer) and records `processed_at`. 'rejected' re-credits the wallet
    balance this payout's own request had already debited, so the money is not
    left unaccounted for.

THE REDESIGN (THIS FILE'S CURRENT SHAPE)
    The status flip and the rejection re-credit used to be two separate PostgREST
    statements issued from the handler. They are now ONE call to
    `public.settle_referral_payout` (`049_ref01_payout_atomic.sql`), a single
    `SECURITY DEFINER` Postgres function that performs both inside one transaction.
    This file's stub therefore no longer fakes individual `.table(...)` calls for
    the settlement step: it fakes the ONE `.rpc(...)` call, replicating the SQL
    function's own decision logic (terminal check, reference requirement, atomic
    re-credit) so the HANDLER's status-code mapping and request wiring are proven
    without a database. The database-level atomicity guarantee itself -- that a
    rejection's status flip and re-credit commit together or not at all -- is
    proven against a REAL PostgreSQL server in
    `tests/test_ref01_payout_atomic_postgres.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_service_supabase
from backend_app.main import app

PAYOUT_ID = "33333333-3333-4333-8333-333333333333"
PAYOUT_USER_ID = "44444444-4444-4444-8444-444444444444"
PAYOUT_AMOUNT = 75.0
OPENING_WALLET_BALANCE = 120.0


class _PGError(Exception):
    """Mimics `postgrest.exceptions.APIError`'s `.code` attribute, carrying the
    SQLSTATE a real `RAISE EXCEPTION ... USING ERRCODE = '...'` would surface."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _Resp:
    def __init__(self, data):
        self.data = data


class _Rpc:
    """Replicates `public.settle_referral_payout`'s own decision logic (049's SQL),
    operating on the same in-memory `state` dict every test shares -- the Python-side
    mirror of the real function's behaviour, not a re-implementation of its SQL.
    """

    def __init__(self, name: str, params: dict, state: dict):
        self._name = name
        self._params = params
        self._state = state

    def execute(self):
        self._state["rpc_calls"].append((self._name, dict(self._params)))
        if self._name != "settle_referral_payout":
            return _Resp(None)

        payout = self._state["payout"]
        status = self._params["p_status"]
        reference = self._params.get("p_reference")
        reason = self._params.get("p_reason")

        if status not in ("completed", "rejected"):
            raise _PGError("RF003", "invalid status")

        if payout["status"] not in ("pending", "processing"):
            raise _PGError(
                "RF004", f"payout {PAYOUT_ID} is already {payout['status']!r}"
            )

        if status == "completed":
            if not reference:
                raise _PGError("RF003", "a reference is required")
            details = dict(payout.get("payout_details") or {})
            details["settlement_reference"] = reference
            payout["status"] = "completed"
            payout["payout_details"] = details
        else:
            details = dict(payout.get("payout_details") or {})
            details["rejection_reason"] = reason or "Not specified"
            payout["status"] = "rejected"
            payout["payout_details"] = details
            # The atomic re-credit. `wallet_updated` records that this -- and only
            # this -- path moves the wallet, matching the real function's own
            # "completed does not touch the wallet" shape.
            self._state["wallet_balance"] += PAYOUT_AMOUNT
            self._state["wallet_updated"] = True

        return _Resp(dict(payout))


class _Supabase:
    def __init__(self, state: dict):
        self._state = state

    def rpc(self, name, params):
        return _Rpc(name, params, self._state)


def _make_state(payout_status="pending"):
    return {
        "rpc_calls": [],
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
    assert state["rpc_calls"] == [
        ("settle_referral_payout", {
            "p_payout_id": PAYOUT_ID, "p_status": "completed",
            "p_reference": "UPI-TXN-998877", "p_reason": None,
        })
    ], "the handler must issue exactly one atomic settlement RPC call"


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
