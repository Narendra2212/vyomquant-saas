"""
Auth Middleware — Supabase ES256 JWKS Validation (HOTFIX)

Replaces HS256 shared-secret validation with proper Supabase JWKS-based
ES256 signature verification.

Changes:
- Uses PyJWKClient to resolve public key from Supabase JWKS endpoint
- Verifies ES256 signature with matching EC P-256 public key by kid
- Verifies audience="authenticated" and expiration
- Preserves all existing user extraction logic (sub, email, tenant_id)
- Preserves latency logging (< 0.3s target)
"""

import logging
import os
import threading
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from jwt import (ExpiredSignatureError, InvalidAudienceError,
                 InvalidTokenError, PyJWKClient)

from backend_app.core.config import settings

logger = logging.getLogger("AuthMiddleware")

security = HTTPBearer()

# ---------------------------------------------------------------------------
# JWKS Client — module-level singleton, thread-safe, keys cached in-process
# Dynamically constructed from SUPABASE_URL environment variable.
# ---------------------------------------------------------------------------

def _build_jwks_url() -> str:
    """
    Build the Supabase JWKS URL from SUPABASE_URL env var.
    e.g. https://abc123.supabase.co → https://abc123.supabase.co/auth/v1/.well-known/jwks.json
    Falls back to a placeholder so import does not crash if env var missing.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if supabase_url:
        return f"{supabase_url}/auth/v1/.well-known/jwks.json"
    # Fallback — JWKS validation will fail gracefully; HS256 path will still work.
    return "https://placeholder.supabase.co/auth/v1/.well-known/jwks.json"

_jwks_client: PyJWKClient | None = None
_jwks_lock = threading.Lock()


def _get_jwks_client() -> PyJWKClient:
    """
    Return the module-level PyJWKClient singleton.
    Lazily initialised and thread-safe. Keys are cached in-process.
    Constructed from SUPABASE_URL at first call (after env vars are loaded).
    cache_keys=True avoids repeated network fetches; PyJWT auto-refreshes
    on unknown kid.
    """
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                _jwks_client = PyJWKClient(
                    _build_jwks_url(),
                    cache_keys=True,
                    lifespan=3600,   # refresh JWKS every 1 hour max
                )
    return _jwks_client


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

#: The ONLY two ``ENV`` spellings that enable the HS256 test-token path below.
#:
#: Deliberately narrower than the sets used elsewhere in this codebase, and for the same
#: reason ``core/execution_engine.py``'s ``_SYNTHETIC_STRATEGY_ID_ENVS`` is narrower: what
#: this gate governs is whether a token the platform's OWN application secret can mint is
#: accepted as proof of identity. ``development``, ``dev`` and ``local`` were in this set
#: and are now gone — see :func:`_test_token_fallback_allowed`.
_TEST_TOKEN_ENVS = frozenset({"testing", "test"})


def _test_token_fallback_allowed() -> bool:
    """Whether the ``iss="algo22-test"`` HS256 token path is in force.

    FAILS CLOSED, AND THAT IS THE WHOLE FIX.

    The previous expression was::

        current_env = (os.environ.get("ENV") or getattr(settings, "ENV", "") or "").lower()
        if current_env in ("testing", "test", "development", "dev", "local"):

    ``settings.ENV`` is ``os.getenv("ENV", "development")`` (``core/config.py``), so when a
    deployment did not set ``ENV`` at all, this resolved to ``"development"`` — a member of
    that set. The fallback was therefore LIVE IN PRODUCTION on any deployment that had not
    explicitly set the variable, which was verified by execution during the pre-launch
    audit: with ``ENV`` and ``ENVIRONMENT`` unset, ``settings.ENV`` is ``'development'`` and
    a token signed with ``JWT_SECRET`` carrying ``iss="algo22-test"`` was accepted with an
    arbitrary ``sub``, an arbitrary ``app_metadata.role`` of ``"operator"`` and
    ``aal="aal2"``.

    That is total authentication collapse rather than a weakening, because:

    * ``sub`` is the user id every router scopes its queries by, so an arbitrary ``sub`` is
      impersonation of any account.
    * ``get_admin_user`` / ``get_operator_user`` trust ``app_metadata.role``, so the same
      token grants the global kill switch and user-status administration.
    * The signing key is ``JWT_SECRET`` / ``SUPABASE_JWT_SECRET`` — an ordinary application
      secret that lives in ``.env`` files, CI configuration and task definitions, and is
      handled far more widely than Supabase's ES256 private key, which never leaves
      Supabase. The fallback therefore demoted the trust root from a key nobody here holds
      to a key everybody here holds.

    Two changes, both narrowing:

    1. **Only the real environment variables are read.** ``settings.ENV`` is not consulted,
       because its default is indistinguishable from a deliberate setting, and conflating
       "nobody configured this" with "this is a development box" is precisely the defect.
       An unset or blank value returns ``False``.
    2. **Only ``testing`` and ``test`` are admitted.** ``development``, ``dev`` and
       ``local`` are removed. ``tests/conftest.py`` sets ``ENV=testing`` at import time, so
       the suite is unaffected; a developer running against real Supabase uses real
       Supabase tokens and never needed this path.

    Read from the environment on every call rather than cached at import, because
    individual tests ``monkeypatch.setenv`` it and a cached value would make the gate
    depend on module import order.

    Regression: tests/test_test_token_fallback_fails_closed.py
    """
    raw = os.environ.get("ENV") or os.environ.get("ENVIRONMENT")
    if raw is None or not raw.strip():
        # Unset is NOT a test environment. This single line is the production fix.
        return False
    return raw.strip().lower() in _TEST_TOKEN_ENVS


def decode_token_local(token: str) -> dict:
    """
    Decodes and validates a JWT token locally.
    
    CWE-347 Algorithm Confusion Fix:
    All tokens are validated against a server-determined trust root (ES256 JWKS).
    The token's own unverified 'alg' header is never used to select secret keys or bypass verification.

    The ``iss="algo22-test"`` HS256 branch below is gated on
    :func:`_test_token_fallback_allowed`, which fails closed on an unset ``ENV``.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
            options={"verify_exp": True},
        )
        return payload
    except (ExpiredSignatureError, InvalidAudienceError):
        raise
    except Exception as e:
        # SECURITY: No test fallback with verify_signature=False - prevents algorithm confusion attacks
        # Test tokens must be properly signed with the test secret
        if _test_token_fallback_allowed():
            # Narrowly-scoped test fallback for properly signed test tokens only in non-production
            try:
                unverified_payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["ES256", "HS256"])
                if unverified_payload.get("iss") == "algo22-test":
                    # Use test secret for test tokens - must be properly signed
                    test_secret = os.environ.get("SUPABASE_JWT_SECRET") or settings.JWT_SECRET
                    if not test_secret:
                        raise InvalidTokenError("Test mode requires a configured secret")
                    # Verify the token is actually signed with the test secret (no signature bypass)
                    return jwt.decode(
                        token,
                        test_secret,
                        algorithms=["HS256"],
                        audience="authenticated",
                        options={"verify_exp": True, "verify_signature": True},
                    )
            except (ExpiredSignatureError, InvalidAudienceError):
                raise
            except Exception:
                pass
        raise InvalidTokenError("Invalid token signature or algorithm") from e


def _decode_test_hs256_token(token: str) -> dict:
    """Explicit, separate helper for test suite tokens with iss='algo22-test'."""
    secret = os.environ.get("SUPABASE_JWT_SECRET") or settings.JWT_SECRET
    
    # SECURITY: Fail-closed if secret not available
    if not secret:
        raise InvalidTokenError("Test mode requires a configured secret")
    
    # SECURITY: Only allow in an explicitly-declared test environment.
    # Shares the one gate with decode_token_local so the two cannot drift apart — this
    # helper used to carry its own copy of the env list, including the `development`
    # default that made the gate ineffective in production.
    if not _test_token_fallback_allowed():
        raise InvalidTokenError("Test mode not allowed in production environment")
    
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="authenticated",
        options={"verify_exp": True},
    )


def verify_token(credentials):
    """
    Verify JWT using local dual-algorithm decoding (ES256/HS256).

    Supabase issues ES256-signed tokens for user sessions, while static keys and
    the validation test suite use HS256. This helper performs zero-latency local
    verification for both.

    Raises HTTPException 401 on any validation failure.
    """
    token = credentials.credentials

    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    start = time.time()

    try:
        payload = decode_token_local(token)

        elapsed = time.time() - start
        alg = jwt.get_unverified_header(token).get("alg", "unknown")
        if elapsed > 0.05:
            logger.warning(f"JWT validation slow: {elapsed:.3f}s")
        else:
            logger.debug(f"JWT VALIDATED {alg} ({elapsed:.3f}s)")

        return payload

    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Invalid token audience")
    except InvalidTokenError as e:
        logger.warning(f"JWT INVALID: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.warning(f"JWT FAILED: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")


# ---------------------------------------------------------------------------
# FastAPI dependency — user extraction (UNCHANGED from original)
# ---------------------------------------------------------------------------

async def get_current_user(credentials=Depends(security)):
    """Fast dependency for protected endpoints — user extraction preserved."""
    payload = verify_token(credentials)

    # Extract user info from JWT claims (unchanged)
    user_id = payload.get("sub")
    email = payload.get("email", "")
    tenant_id = (
        payload.get("tenant_id")
        or payload.get("app_metadata", {}).get("tenant_id")
        or user_id
    )

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token claims")

    return {
        "id": user_id,
        "email": email,
        "tenant_id": tenant_id,
        "payload": payload,
    }



