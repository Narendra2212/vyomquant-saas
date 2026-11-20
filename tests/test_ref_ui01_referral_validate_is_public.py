"""REF-UI-01: the signup form checks a typed referral code before any session exists.

`POST /api/referral/validate` therefore answers WITHOUT an Authorization header, and still
answers only valid/invalid (never the referrer's id), rate-limited per client (REF-02).
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.routers import referral as referral_router

client = TestClient(app)


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Svc:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "referral_codes"
        return _Q(self.rows)


def test_a_visitor_with_no_session_can_check_a_code():
    with patch.object(referral_router, "_get_service_client",
                      return_value=_Svc([{"user_id": "u-ref", "is_active": True}])):
        resp = client.post("/api/referral/validate", json={"referral_code": "abcde"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"valid": True, "message": "Valid referral code"}


def test_an_unknown_code_is_reported_invalid_without_detail():
    with patch.object(referral_router, "_get_service_client", return_value=_Svc([])):
        resp = client.post("/api/referral/validate", json={"referral_code": "NOPE1"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert "user_id" not in resp.text and "referrer" not in resp.text.lower().replace("referral", "")
