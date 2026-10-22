"""MKT-06 -- the admin-only creator-payout settlement endpoint, mirroring REF-01's
`settle_payout` test shape exactly (`tests/test_ref01_payout_settlement.py`).

THE GAP THIS CLOSES
    `routers/library.py::creator_analytics` has always been read-only: it reports a
    creator's lifetime earnings but nothing in this codebase ever marked any of it
    paid, and nothing created a payout. A prior pass's own comment ("no payout
    mechanism exists in this system") named the gap without giving it a ledger row.

THE FIX
    New `PUT /api/library/creator-payouts/{payout_id}/settle`, admin-gated
    (`get_admin_user`), under the service-role client -- `marketplace_creator_
    payouts` carries an owner-only RLS read policy, so an admin's own JWT cannot
    see or settle another creator's row. The status flip and (for a rejection)
    the claim-release are ONE call to `public.settle_creator_payout`
    (`051_mkt06_creator_payout_atomic.sql`). This file fakes that ONE `.rpc(...)`
    call, replicating the SQL function's own decision logic (terminal check,
    reference requirement) so the HANDLER's status-code mapping is proven without
    a database. The database-level atomicity -- that the status flip and the
    claim-release commit together or not at all -- is proven against a REAL
    PostgreSQL server in `tests/test_mkt06_creator_payout_atomic_postgres.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_service_supabase
from backend_app.main import app

PAYOUT_ID = "55555555-5555-4555-8555-555555555555"
PAYOUT_OWNER_ID = "66666666-6666-4666-8666-666666666666"
PAYOUT_AMOUNT_MINOR = 269900


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
    """Replicates `public.settle_creator_payout`'s own decision logic (051's SQL),
    operating on the same in-memory `state` dict every test shares."""

    def __init__(self, name: str, params: dict, state: dict):
        self._name = name
        self._params = params
        self._state = state

    def execute(self):
        self._state["rpc_calls"].append((self._name, dict(self._params)))
        if self._name != "settle_creator_payout":
            return _Resp(None)

        payout = self._state["payout"]
        status = self._params["p_status"]
        reference = self._params.get("p_reference")
        reason = self._params.get("p_reason")

        if status not in ("completed", "rejected"):
            raise _PGError("PY003", "invalid status")

        if payout["status"] != "pending":
            raise _PGError(
                "PY004", f"payout {PAYOUT_ID} is already {payout['status']!r}"
            )

        if status == "completed":
            if not reference:
                raise _PGError("PY003", "a reference is required")
            payout["status"] = "completed"
            payout["reference"] = reference
        else:
            payout["status"] = "rejected"
            payout["reason"] = reason or "Not specified"
            # The claim release. `claims_released` records that this -- and only
            # this -- path releases claims, matching the real function's own
            # "completed does not touch the claims table" shape.
            self._state["claims"] = []
            self._state["claims_released"] = True

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
            "owner_id": PAYOUT_OWNER_ID,
            "amount_minor": PAYOUT_AMOUNT_MINOR,
            "currency": "USD",
            "status": payout_status,
            "reference": None,
            "reason": None,
        },
        "claims": ["settlement-1"],
        "claims_released": False,
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
            f"/api/library/creator-payouts/{PAYOUT_ID}/settle",
            json={"status": "completed", "reference": "TXN-1"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_completing_a_payout_records_reference_and_does_not_release_claims(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/library/creator-payouts/{PAYOUT_ID}/settle",
        json={"status": "completed", "reference": "UPI-TXN-998877"},
    )

    assert resp.status_code == 200, resp.text
    assert state["payout"]["status"] == "completed"
    assert state["payout"]["reference"] == "UPI-TXN-998877"
    assert state["claims_released"] is False, (
        "completing a payout means the operator already sent the money manually -- "
        "the claimed settlements stay claimed"
    )
    assert state["claims"] == ["settlement-1"]
    assert state["rpc_calls"] == [
        ("settle_creator_payout", {
            "p_payout_id": PAYOUT_ID, "p_status": "completed",
            "p_reference": "UPI-TXN-998877", "p_reason": None,
        })
    ], "the handler must issue exactly one atomic settlement RPC call"


def test_completing_without_a_reference_is_refused(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/library/creator-payouts/{PAYOUT_ID}/settle",
        json={"status": "completed"},
    )

    assert resp.status_code == 400
    assert state["payout"]["status"] == "pending", "a refused settlement must not mutate the payout"


def test_rejecting_a_payout_releases_its_claims(admin_client):
    client, state = admin_client
    resp = client.put(
        f"/api/library/creator-payouts/{PAYOUT_ID}/settle",
        json={"status": "rejected", "reason": "Invalid bank details"},
    )

    assert resp.status_code == 200, resp.text
    assert state["payout"]["status"] == "rejected"
    assert state["payout"]["reason"] == "Invalid bank details"
    assert state["claims"] == [], (
        "rejecting a payout must release every settlement it claimed, so they are "
        "payable again in the next weekly sweep"
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
            f"/api/library/creator-payouts/{PAYOUT_ID}/settle",
            json={"status": "completed", "reference": "TXN-2"},
        )
        assert resp.status_code == 409
        assert state["claims_released"] is False
    finally:
        app.dependency_overrides.clear()


def test_settling_a_nonexistent_payout_is_404(admin_client):
    client, state = admin_client

    class _NotFoundRpc:
        def execute(self):
            raise _PGError("PY002", "no such payout")

    class _NotFoundSupabase:
        def rpc(self, name, params):
            return _NotFoundRpc()

    app.dependency_overrides[get_service_supabase] = lambda: _NotFoundSupabase()
    resp = client.put(
        "/api/library/creator-payouts/99999999-9999-4999-8999-999999999999/settle",
        json={"status": "completed", "reference": "TXN-GHOST"},
    )
    assert resp.status_code == 404
