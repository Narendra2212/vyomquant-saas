"""AUTH-01 -- POST /api/auth/signout actually revokes the caller's session.

THE DEFECT
    ``signout`` built an ANON-keyed Supabase client (never authenticated as the caller
    -- the caller's own browser SDK performs their login, not this backend) and called
    its plain ``client.auth.sign_out()``. That method signs the CLIENT OBJECT'S OWN
    locally-tracked session out; this client never had one, so the call was a no-op --
    it returned 200 "Signed out successfully" while the caller's refresh token stayed
    valid indefinitely. The ``token`` parameter the endpoint accepted was never even
    read in the function body.

THE FIX
    Revoke the caller's session directly, by JWT, via Supabase's admin API
    (``client.auth.admin.sign_out(jwt=..., scope="global")`` against a SERVICE-ROLE
    client) -- a real server-to-server call naming WHOSE session to end, independent
    of whether this process ever held that session itself. The caller's own access
    token (``current_user["access_token"]``, already available from ``get_current_user``)
    is what gets revoked; the redundant, previously-unused ``token`` parameter is gone.

WHAT THIS FILE PROVES (a fake service-role client; no real Supabase)
    * the admin sign-out is called with the CALLER'S OWN access token, not a client-
      supplied one and not nothing;
    * ``scope="global"`` is used (every refresh token for the account, not just one);
    * no service-role client available is a clean 503, not a silent "success";
    * an admin API failure is a 400, not a false "Signed out successfully".
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.main import app

TEST_USER = {
    "id": "usr-auth01-test",
    "email": "trader@example.com",
    "access_token": "the-callers-real-access-token",
    "role": "authenticated",
}


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


class TestSignoutRevokesTheRealSession:
    def test_admin_sign_out_is_called_with_the_callers_own_token(self, client):
        fake_admin = MagicMock()
        fake_service_client = MagicMock()
        fake_service_client.auth.admin = fake_admin

        with patch(
            "backend_app.routers.auth.get_service_supabase",
            return_value=fake_service_client,
        ):
            res = client.post("/api/auth/signout")

        assert res.status_code == 200
        fake_admin.sign_out.assert_called_once_with(
            jwt=TEST_USER["access_token"], scope="global"
        )

    def test_no_service_role_client_is_a_clean_503_not_a_fake_success(self, client):
        with patch(
            "backend_app.routers.auth.get_service_supabase", return_value=None,
        ):
            res = client.post("/api/auth/signout")

        assert res.status_code == 503

    def test_an_admin_api_failure_is_a_400_not_a_false_success(self, client):
        fake_service_client = MagicMock()
        fake_service_client.auth.admin.sign_out.side_effect = RuntimeError("logout failed")

        with patch(
            "backend_app.routers.auth.get_service_supabase",
            return_value=fake_service_client,
        ):
            res = client.post("/api/auth/signout")

        assert res.status_code == 400

    def test_signout_requires_authentication(self, client):
        app.dependency_overrides.pop(get_current_user, None)
        res = client.post("/api/auth/signout")
        assert res.status_code in (401, 403)
