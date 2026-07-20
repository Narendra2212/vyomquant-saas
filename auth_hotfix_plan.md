# AUTH_MIDDLEWARE_HOTFIX_PLAN

**Generated:** 2026-06-19T10:06:00+05:30  
**Mission:** Convert HS256 shared-secret → Supabase ES256 JWKS validation

---

## Root Cause (Confirmed)

| Component | Finding |
|-----------|---------|
| `core/auth_middleware.py` | `jwt.decode(..., algorithms=["HS256"])` rejects all ES256 tokens |
| `core/auth_middleware.py` | `settings.SUPABASE_JWT_SECRET` is a symmetric string — invalid for EC key verification |
| `core/websocket_auth.py` | Same HS256 bug — WebSocket auth also broken |
| Supabase JWKS | Confirmed ES256 / P-256 curve |

```python
# BROKEN — original code
payload = jwt.decode(
    token,
    settings.SUPABASE_JWT_SECRET,   # symmetric secret — wrong for EC
    algorithms=["HS256"],            # rejects ES256 tokens — wrong algorithm
    audience="authenticated"
)
```

---

## JWKS Endpoint

```
https://YOUR_PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json
```

**Live JWKS snapshot:**
```json
{
  "keys": [{
    "alg": "ES256",
    "crv": "P-256",
    "ext": true,
    "key_ops": ["verify"],
    "kid": "YOUR_SUPABASE_JWT_SECRET",
    "kty": "EC",
    "use": "sig",
    "x": "9DHfTR_JV0iPkqgwqYfV-Q2VSqAXq6tIFflNjgXBbns",
    "y": "BX3fBC1JZhsmN5mjP1eHQsMyKJ3ApUKTBpavJrWwoN0"
  }]
}
```

---

## Files to Modify

| File | Change |
|------|--------|
| `core/auth_middleware.py` | Replace HS256 with JWKS-based ES256 verification; add `PyJWKClient` |
| `core/websocket_auth.py` | Same fix in `_validate_token()` |
| `core/dependencies.py` | Update secondary decode from HS256 to ES256 JWKS |
| `requirements.txt` | Ensure `PyJWT>=2.4.0` with `cryptography` extra |

## Files NOT Modified (per constraints)

- Trading engines (`core/execution_engine.py`, `core/unified_execution_engine.py`)
- Strategy logic, portfolio logic
- Redis client
- Supabase schema / RLS
- Any route or worker file

---

## Implementation Design

### JWKS Cache
- Single `PyJWKClient` instance at module load — caches keys in memory
- `cache_keys=True` — avoids repeated network fetches
- Refresh on `kid` miss (PyJWT handles this automatically)

### Verification Flow
```
1. Decode JWT header → extract kid
2. PyJWKClient.get_signing_key_from_jwt(token) → resolves EC public key by kid
3. jwt.decode(token, signing_key.key, algorithms=["ES256"], audience="authenticated")
4. Verify exp, sub, aud claims
5. Return payload → downstream user extraction unchanged
```

### Error Handling
- `ExpiredSignatureError` → 401 "Token expired" (preserved)
- `InvalidAudienceError` → 401 "Invalid token" (preserved)
- `InvalidTokenError` → 401 "Invalid token" (preserved)
- JWKS fetch failure → 401 "Authentication failed" (preserved)

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| JWKS endpoint unreachable on boot | `PyJWKClient` with `lifespan_seconds` cache; fail-fast at decode time |
| Key rotation | `kid` lookup automatically picks correct key |
| Performance regression | Keys cached in-process; < 0.3s target preserved |

---

## Verification Plan

- Valid ES256 token → `200 OK`
- Expired ES256 token → `401 Unauthorized`
- Tampered/invalid ES256 token → `401 Unauthorized`
- Wrong audience token → `401 Unauthorized`
