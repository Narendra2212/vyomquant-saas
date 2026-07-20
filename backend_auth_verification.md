# Backend Authentication Verification Report

**Date:** 2026-06-18
**Environment:** Production Backend

This report inspects the backend authentication middleware and dependencies to verify the expected JWT properties.

## 1. Expected Properties

- **Secret Variable:** `settings.SUPABASE_JWT_SECRET` (read from the `SUPABASE_JWT_SECRET` environment variable).
- **Signing Algorithm:** `HS256` (strictly specified as `algorithms=["HS256"]`).
- **Expected Issuer:** Not checked or validated in the middleware.
- **Expected Audience:** `authenticated` (strictly verified as `audience="authenticated"`).

## 2. Middleware Implementation Details

### FastAPI Dependency Auth Middleware (`core/auth_middleware.py`)
```python
def verify_token(credentials):
    token = credentials.credentials
    ...
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
        return payload
    ...
```

### Multi-Tenant Middleware (`core/tenant_middleware.py`)
```python
    def _decode_jwt(self, token: str) -> dict:
        """Decode JWT token using Supabase JWT secret."""
        import jwt
        from core.config import settings
        
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
```

## 3. Analysis

The backend expects and strictly enforces:
1. Symmetric signature verification using the `HS256` algorithm.
2. The `SUPABASE_JWT_SECRET` symmetric key.
3. An audience of `authenticated`.

Any token using an asymmetric algorithm (such as `ES256`) or not matching the audience will fail verification at the backend layer, returning a `401 Unauthorized` status.
