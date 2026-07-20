# Phase 3: JWT Claims Comparison

## Backend Verification Trace

The backend validation is implemented in `core/auth_middleware.py` within the `verify_token` function:

```python
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
```

### Expected vs. Actual Claims

| Property | Expected by Backend | Actual Token Claim (from Phase 2) | Match? |
| :--- | :--- | :--- | :--- |
| **Algorithm** | `HS256` (Hardcoded in middleware) | `ES256` | ❌ **MISMATCH** |
| **Audience** | `authenticated` | `authenticated` | ✅ **MATCH** |
| **Issuer** | *Not Validated* | `https://YOUR_PROJECT_REF.supabase.co/auth/v1` | ➖ *(Ignored)* |
| **Expiration** | Must be in the future | `1781805830` (Valid) | ✅ **MATCH** |
| **Secret/Key** | Symmetric `SUPABASE_JWT_SECRET` | Asymmetric ECDSA signature | ❌ **MISMATCH** |

### Findings
The backend middleware explicitly expects an `HS256` (HMAC with SHA-256) symmetric signature. However, the production Supabase project is issuing tokens signed with `ES256` (ECDSA using P-256 and SHA-256), which is an asymmetric algorithm requiring a public key (JWKS) to verify. 

Because the algorithm expected (`HS256`) does not match the token's algorithm (`ES256`), PyJWT immediately raises an `InvalidAlgorithmError` (a subclass of `InvalidTokenError`), triggering the `401 Unauthorized` response with "Invalid token".
