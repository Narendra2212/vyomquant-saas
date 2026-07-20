# Phase 4: JWT Failure Root Cause

## Failure Determination

Based on the forensic analysis across the backend source code and the live production token:

1. **The Production Token** is signed using the **`ES256`** algorithm (ECDSA using P-256 and SHA-256).
2. **The Backend Middleware** (`core/auth_middleware.py`) explicitly restricts acceptable algorithms to **`HS256`** (HMAC with SHA-256).
3. **The Backend Middleware** is also passing a symmetric string secret (`settings.SUPABASE_JWT_SECRET`) to `jwt.decode()`.

## Mechanism of Failure

When the frontend sends the valid `ES256` token to the backend, the `jwt.decode` function in `verify_token` performs an algorithm check before validating the signature. Because `ES256` is not in the allowed `algorithms=["HS256"]` list, PyJWT immediately raises an `InvalidAlgorithmError`.

This exception is caught by the following block in `auth_middleware.py`:
```python
    except jwt.InvalidTokenError as e:
        print(f"❌ JWT INVALID: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")
```

Even if the algorithm restriction were removed, the validation would still fail because `ES256` requires an asymmetric public key (from Supabase's JWKS endpoint), while the middleware attempts to use the static symmetric `SUPABASE_JWT_SECRET`.

## Classification

**E = middleware bug**

The root cause is a fundamental design flaw in `auth_middleware.py` which is tightly coupled to symmetric `HS256` validation, making it incompatible with Supabase's modern asymmetric `ES256` JWTs.
