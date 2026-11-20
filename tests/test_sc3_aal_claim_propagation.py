"""SC-3: the `aal` claim must survive `get_current_user`, or MFA enforcement is impossible.

WHAT THIS GUARDS, AND WHY THE EXISTING SUITES DID NOT
-----------------------------------------------------
`require_aal2` is covered by `test_phase7b_auth_remediation.py`,
`test_phase12a_production_security_validation.py` and `tests/regression/capture_baseline.py`.
All three call it with a **hand-built user dict**, so they verify the gate's decision table
and nothing about where the claim comes from. `create_test_jwt` in the phase12a suite
compounds it by writing `aal` into `app_metadata` as well as the top level — real Supabase
tokens put it only at the top level
(https://supabase.com/docs/guides/auth/jwt-fields), so the fixture diverges from production
in exactly the field under test.

The consequence, before the fix this file pins: `get_current_user` assembled a six-key dict
that stopped at `app_metadata` and dropped `aal`. `require_aal2` then read
`app_metadata.get("aal")` (absent — wrong location for a real token) followed by
`user.get("aal", "aal1")` (absent — never copied), landed on the "aal1" default, and
answered 403 to every caller including one who had just passed a TOTP challenge.

So these tests assert through `get_current_user` itself, and then compose it with
`require_aal2` the way an endpoint would.
"""

import os
import time
import uuid

import jwt
import pytest
from fastapi import HTTPException

from backend_app.core.dependencies import get_current_user, require_aal2

TEST_SECRET = os.environ.get(
    "SUPABASE_JWT_SECRET", "super-secret-test-jwt-key-minimum-32-chars-long"
)


def _supabase_shaped_token(aal: str = "aal1", amr=None, **overrides) -> str:
    """A token shaped like a REAL Supabase access token.

    The point of this helper is what it does NOT do: it never writes `aal` into
    `app_metadata`. `app_metadata` carries provider data, as in production. A helper that
    put the claim in both places is what hid this defect.
    """
    payload = {
        "iss": "algo22-test",
        "sub": str(uuid.uuid4()),
        "email": "trader@vyomquant.io",
        "role": "authenticated",
        "aud": "authenticated",
        "aal": aal,
        "amr": amr if amr is not None else [{"method": "password", "timestamp": 1}],
        "app_metadata": {"provider": "email"},
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    payload.update(overrides)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


class _Credentials:
    """Minimal stand-in for HTTPAuthorizationCredentials."""

    def __init__(self, token: str):
        self.scheme = "Bearer"
        self.credentials = token


@pytest.fixture(autouse=True)
def _unfrozen_profile(monkeypatch):
    """Neutralise the account-freeze lookup.

    `get_current_user` calls `_get_cached_profile`, which needs Redis or Supabase and
    answers 503 when it cannot reach either. That check is not what these tests are about,
    and letting it run would make them a test of infrastructure availability.
    """

    async def _profile(_tenant_id, _token):
        return {"is_frozen": False}

    monkeypatch.setattr(
        "backend_app.core.dependencies._get_cached_profile", _profile
    )


async def _resolve(token: str) -> dict:
    return await get_current_user(credentials=_Credentials(token), supabase=None)


class TestAalClaimSurvivesTokenDecoding:
    """The regression proper: the claim must be on the dict `get_current_user` returns."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("aal", ["aal1", "aal2"])
    async def test_top_level_aal_is_propagated(self, aal):
        user = await _resolve(_supabase_shaped_token(aal=aal))
        assert user["aal"] == aal, (
            "get_current_user dropped the top-level `aal` claim; require_aal2 cannot "
            "distinguish an MFA session from a password-only one without it"
        )

    @pytest.mark.asyncio
    async def test_aal_is_not_sourced_from_app_metadata(self):
        """`app_metadata` must not be able to supply or override `aal`.

        `app_metadata` is server-controlled, so this is not a privilege-escalation hole the
        way `user_metadata` would be (see `get_admin_user`). It is asserted because the
        top-level claim is the authoritative one and a token that disagrees with itself
        must resolve to the claim Supabase actually signs for assurance level.
        """
        token = _supabase_shaped_token(
            aal="aal1", app_metadata={"provider": "email", "aal": "aal2"}
        )
        user = await _resolve(token)
        assert user["aal"] == "aal1"

    @pytest.mark.asyncio
    async def test_absent_aal_defaults_closed_to_aal1(self):
        payload_without_aal = _supabase_shaped_token()
        decoded = jwt.decode(
            payload_without_aal,
            TEST_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        decoded.pop("aal")
        token = jwt.encode(decoded, TEST_SECRET, algorithm="HS256")

        user = await _resolve(token)
        assert user["aal"] == "aal1", "a token with no assurance claim must fail closed"

    @pytest.mark.asyncio
    async def test_amr_is_propagated(self):
        amr = [
            {"method": "password", "timestamp": 1},
            {"method": "totp", "timestamp": 2},
        ]
        user = await _resolve(_supabase_shaped_token(aal="aal2", amr=amr))
        assert user["amr"] == amr


class TestRequireAal2ComposesWithGetCurrentUser:
    """The composition an endpoint would actually use: the dependency chain end to end.

    This is the assertion that would have failed before the fix. Every case here goes
    through `get_current_user` rather than constructing the dict by hand, which is the
    difference between testing the gate and testing the gate as it is deployed.
    """

    @pytest.mark.asyncio
    async def test_aal2_session_is_admitted(self):
        user = await _resolve(_supabase_shaped_token(aal="aal2"))
        admitted = await require_aal2(user)
        assert admitted["id"] == user["id"]

    @pytest.mark.asyncio
    async def test_aal1_session_is_refused_with_403(self):
        user = await _resolve(_supabase_shaped_token(aal="aal1"))
        with pytest.raises(HTTPException) as exc:
            await require_aal2(user)
        assert exc.value.status_code == 403
        assert "Two-factor authentication is required" in exc.value.detail

    @pytest.mark.asyncio
    async def test_the_refusal_is_not_universal(self):
        """Guards the specific failure mode: a gate that refuses EVERY caller.

        Before the propagation fix both of the above raised 403, so the suite could have
        looked healthy on the negative case alone. Asserting the two outcomes differ for
        the same code path is what pins "this gate discriminates" rather than merely
        "this gate rejects".
        """
        aal2_user = await _resolve(_supabase_shaped_token(aal="aal2"))
        aal1_user = await _resolve(_supabase_shaped_token(aal="aal1"))

        assert await require_aal2(aal2_user)

        with pytest.raises(HTTPException):
            await require_aal2(aal1_user)
