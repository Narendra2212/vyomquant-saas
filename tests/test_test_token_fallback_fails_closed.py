"""The HS256 ``iss="algo22-test"`` token path must be unreachable unless ENV says so.

WHAT THIS GUARDS
----------------
``decode_token_local`` verifies Supabase ES256 tokens against the JWKS endpoint. When that
fails it has a second branch for the test suite's own HS256 tokens, gated on the
environment. The gate used to be::

    current_env = (os.environ.get("ENV") or getattr(settings, "ENV", "") or "").lower()
    if current_env in ("testing", "test", "development", "dev", "local"):

``core/config.py`` declares ``ENV: str = os.getenv("ENV", "development")``. So on a
deployment that never set ``ENV`` — which is the default state, not an exotic one — this
resolved to ``"development"``, the gate opened, and the platform accepted tokens signed
with its own ``JWT_SECRET``.

Why that is total rather than partial:

* ``sub`` is the identity every router scopes by, so an arbitrary ``sub`` impersonates any
  account, including reading its portfolio and placing orders with its exchange keys.
* ``get_admin_user`` / ``get_operator_user`` read ``app_metadata.role``, which the forger
  also controls — so the same token reaches the global kill switch.
* The key is an ordinary application secret present in ``.env``, CI config and task
  definitions, unlike Supabase's ES256 private key which never leaves Supabase.

These tests assert the GATE, not the signature check: the signature check was always
correct (``verify_signature=True``), and the defect was that an attacker-mintable key was
accepted as a trust root at all.
"""

import importlib

import jwt
import pytest

VICTIM = "11111111-2222-3333-4444-555555555555"


def _forge(secret: str, **claims) -> str:
    """A syntactically valid test token: correct issuer, audience and signature."""
    payload = {
        "sub": VICTIM,
        "email": "attacker@example.invalid",
        "aud": "authenticated",
        "iss": "algo22-test",
        "exp": 9999999999,
        "app_metadata": {"role": "operator"},
        "aal": "aal2",
    }
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def auth(monkeypatch):
    """The auth module with the ES256/JWKS path forced to fail fast.

    The real ``PyJWKClient`` would perform an HTTP fetch against the Supabase JWKS
    endpoint. In a test that must run offline and deterministically that is both a hang
    (DNS resolution against the placeholder host) and the wrong subject: what is under
    test is the FALLBACK branch, which is only reached once the ES256 attempt has raised.
    Stubbing the client makes "ES256 did not verify this token" immediate and certain,
    which is exactly the production condition the fallback is written for.
    """
    module = importlib.import_module("backend_app.core.auth_middleware")

    def _no_jwks():
        raise RuntimeError("JWKS unavailable (stubbed for offline test)")

    monkeypatch.setattr(module, "_get_jwks_client", _no_jwks)
    return module


# ─────────────────────────────────────────────────────────────────────────────
# The gate itself
# ─────────────────────────────────────────────────────────────────────────────

def test_unset_env_does_not_enable_the_fallback(auth, monkeypatch):
    """THE DEFECT. An unset ENV must not be read as a development box."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert auth._test_token_fallback_allowed() is False, (
        "with ENV unset the HS256 test-token path is enabled, so any holder of "
        "JWT_SECRET can mint a token for any user"
    )


def test_blank_env_does_not_enable_the_fallback(auth, monkeypatch):
    """``ENV=""`` is a real deployment state (an empty value in a task definition)."""
    monkeypatch.setenv("ENV", "   ")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert auth._test_token_fallback_allowed() is False


@pytest.mark.parametrize("env", ["production", "prod", "staging", "development", "dev", "local"])
def test_non_test_environments_do_not_enable_the_fallback(auth, monkeypatch, env):
    """``development``/``dev``/``local`` are deliberately no longer admitted: they are
    what an unconfigured production deployment resolves to."""
    monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert auth._test_token_fallback_allowed() is False


@pytest.mark.parametrize("env", ["testing", "test", "TESTING", " test "])
def test_explicit_test_environments_still_enable_the_fallback(auth, monkeypatch, env):
    """The suite must keep working. ``tests/conftest.py`` sets ``ENV=testing``.

    Case and surrounding whitespace are accepted because a value typed into a CI form or
    a task definition routinely carries them, and refusing those would turn the fix into
    an outage of the test lane rather than a security improvement.
    """
    monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert auth._test_token_fallback_allowed() is True


# ─────────────────────────────────────────────────────────────────────────────
# End to end through decode_token_local, which is what the routers call
# ─────────────────────────────────────────────────────────────────────────────

def test_forged_token_is_rejected_when_env_is_unset(auth, monkeypatch):
    """The exploit, end to end: a correctly signed ``algo22-test`` token with an
    arbitrary subject and an operator role must not authenticate."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("JWT_SECRET", "a-leaked-application-secret-value")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-leaked-application-secret-value")

    forged = _forge("a-leaked-application-secret-value")

    with pytest.raises(jwt.exceptions.InvalidTokenError):
        auth.decode_token_local(forged)


def test_forged_token_cannot_claim_operator_when_env_is_unset(auth, monkeypatch):
    """Stated as the privilege-escalation consequence rather than as a decode result,
    so the test reads as the thing we actually care about."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("JWT_SECRET", "a-leaked-application-secret-value")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a-leaked-application-secret-value")

    forged = _forge("a-leaked-application-secret-value", app_metadata={"role": "operator"})

    accepted_role = None
    try:
        accepted_role = auth.decode_token_local(forged).get("app_metadata", {}).get("role")
    except Exception:
        pass

    assert accepted_role != "operator", (
        "a self-minted token was accepted carrying app_metadata.role='operator', which "
        "get_operator_user trusts for the global kill switch"
    )


def test_test_token_still_authenticates_under_env_testing(auth, monkeypatch):
    """The fix must not break the suite's own authentication path."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("JWT_SECRET", "the-test-secret-value-for-suite")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "the-test-secret-value-for-suite")

    token = _forge("the-test-secret-value-for-suite")
    payload = auth.decode_token_local(token)

    assert payload["sub"] == VICTIM


def test_wrong_signature_is_rejected_even_under_env_testing(auth, monkeypatch):
    """The signature check was never the defect and must stay strict: a token signed
    with the wrong key is refused even where the fallback is enabled."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.setenv("JWT_SECRET", "the-real-test-secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "the-real-test-secret")

    token = _forge("a-different-secret-entirely")

    with pytest.raises(jwt.exceptions.InvalidTokenError):
        auth.decode_token_local(token)


def test_unsigned_token_is_never_accepted(auth, monkeypatch):
    """``alg=none`` must not pass under any ENV. Guards the CWE-347 property."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.setenv("JWT_SECRET", "the-real-test-secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "the-real-test-secret")

    unsigned = jwt.encode(
        {"sub": VICTIM, "aud": "authenticated", "iss": "algo22-test", "exp": 9999999999},
        key="",
        algorithm="none",
    )

    with pytest.raises(Exception):
        auth.decode_token_local(unsigned)


def test_the_gate_does_not_consult_settings_env(auth):
    """The root cause, pinned: ``settings.ENV`` carries a ``"development"`` DEFAULT that is
    indistinguishable from a deliberate setting, so the gate must not read it.

    Checked against the parsed BODY rather than the source text, because the docstring
    explains the defect and therefore necessarily contains the word ``settings``. A
    substring scan would pass or fail on prose.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(auth._test_token_fallback_allowed)))
    func = tree.body[0]

    # Drop the docstring, then look for any `settings...` reference in what remains.
    body = func.body[1:] if ast.get_docstring(func) else func.body
    names = {
        node.id
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name)
    }

    assert "settings" not in names, (
        "the test-token gate reads settings.ENV, whose default is 'development'; an "
        "unconfigured production deployment would enable the fallback again"
    )
