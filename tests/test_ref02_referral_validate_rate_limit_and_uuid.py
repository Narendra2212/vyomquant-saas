"""tests/test_ref02_referral_validate_rate_limit_and_uuid.py

REF-02 (P3) -- two independent defects in ``POST /referral/validate``:

1. Its rate limiter was its own ``Limiter(key_func=get_remote_address)`` -- a SEPARATE
   instance from every other router's. Behind CloudFront/ALB, ``get_remote_address``
   reads the shared edge socket address, not the caller's own, so every caller landed in
   the same bucket; and a bare ``Limiter(...)`` with no ``storage_uri`` defaults to
   slowapi's in-memory store, which is per-pod, not shared across replicas.
2. Its response returned ``referrer_id`` -- the referrer's raw Supabase user UUID --
   which nothing in the frontend reads.

THE FIX
-------
``referral.py`` now imports the shared ``backend_app.core.rate_limit.limiter``, the same
instance every other router already uses (``trusted_client_address`` key, resolved
storage backend). ``ValidateReferralCodeResponse`` no longer has a ``referrer_id`` field
at all.

WHAT THIS FILE PROVES
----------------------
1. ``referral.limiter`` IS the shared ``core.rate_limit.limiter`` object, not a second
   instance;
2. a successful validation response contains ``valid``/``message`` and nothing else --
   no ``referrer_id`` key at all, not even null;
3. the response Pydantic model itself has no ``referrer_id`` field (so a future call
   site cannot resurrect the leak by constructing the model with the old keyword).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_request_supabase
from backend_app.core.rate_limit import limiter as shared_limiter
from backend_app.main import app
from backend_app.routers import referral as referral_router
from backend_app.routers.referral import ValidateReferralCodeResponse

client = TestClient(app, raise_server_exceptions=True)


def test_referral_router_uses_the_shared_rate_limiter_instance():
    assert referral_router.limiter is shared_limiter, (
        "referral.py must not construct its own separate Limiter -- a second instance "
        "has its own key_func and its own (likely unshared) storage, independent of "
        "every other router's"
    )


def test_the_response_model_has_no_referrer_id_field():
    assert "referrer_id" not in ValidateReferralCodeResponse.model_fields


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return _FakeResp(self._rows)


class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeServiceClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "referral_codes"
        return _FakeQuery(self._rows)


def test_a_valid_code_response_carries_no_referrer_id_key_at_all():
    app.dependency_overrides[get_request_supabase] = lambda: object()
    fake_client = _FakeServiceClient([{"user_id": "11111111-1111-4111-8111-111111111111", "is_active": True}])
    try:
        with patch.object(referral_router, "_get_service_client", return_value=fake_client):
            resp = client.post("/api/referral/validate", json={"referral_code": "ABCDE"})
    finally:
        app.dependency_overrides.pop(get_request_supabase, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is True
    assert "referrer_id" not in body
    assert set(body.keys()) == {"valid", "message"}
