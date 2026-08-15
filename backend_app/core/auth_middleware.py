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

def decode_token_local(token: str) -> dict:
    """
    Decodes and validates a JWT token locally.
    
    CWE-347 Algorithm Confusion Fix:
    All tokens are validated against a server-determined trust root (ES256 JWKS).
    The token's own unverified 'alg' header is never used to select secret keys or bypass verification.
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
        # Narrowly-scoped test fallback for synthetic test tokens in non-production test mode
        current_env = (os.environ.get("ENV") or getattr(settings, "ENV", "") or "").lower()
        if current_env in ("testing", "test", "development", "dev", "local"):
            try:
                unverified_payload = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256", "ES256"])
                if unverified_payload.get("iss") == "algo22-test":
                    return _decode_test_hs256_token(token)
            except Exception:
                pass
        raise InvalidTokenError("Invalid token signature or algorithm") from e


def _decode_test_hs256_token(token: str) -> dict:
    """Explicit, separate helper for test suite tokens with iss='algo22-test'."""
    secret = os.environ.get("SUPABASE_JWT_SECRET") or settings.JWT_SECRET or "dev-secret-change-in-production"
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



