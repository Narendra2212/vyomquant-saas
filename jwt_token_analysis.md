# JWT Token Analysis Report

**Date:** 2026-06-18 17:03:50 UTC
**User email:** narendra@aeroradynamics.in

This report analyzes the active user JWT token issued by the production Supabase authentication provider.

## 1. Token Claims Verification

- **Issuer (iss):** `https://YOUR_PROJECT_REF.supabase.co/auth/v1`
- **Audience (aud):** `authenticated`
- **Expiration Time (exp):** `1781805830` (2026-06-18 18:03:50 UTC)
- **User Role:** `authenticated`
- **Project Reference (extracted):** `wrkexcjqnidkdrayhlsi`
- **Project Reference Validation:** **MATCH** (Successfully validated against Supabase project reference `wrkexcjqnidkdrayhlsi`)

## 2. Decoded JWT Header

```json
{
  "alg": "ES256",
  "kid": "YOUR_SUPABASE_JWT_SECRET",
  "typ": "JWT"
}
```

## 3. Decoded JWT Payload

```json
{
  "aud": "authenticated",
  "exp": 1781805830,
  "sub": "52384fe1-c1dd-4540-89e4-5c36dd8a2bf8",
  "email": os.environ.get("TEST_USER_EMAIL", "your-test-email@example.com"),
  "phone": "",
  "app_metadata": {
  "provider": "email",
  "providers": [
    "email"
  ]
},
  "user_metadata": {
  "email": os.environ.get("TEST_USER_EMAIL", "your-test-email@example.com"),
  "email_verified": true,
  "phone_verified": false,
  "sub": "52384fe1-c1dd-4540-89e4-5c36dd8a2bf8"
},
  "role": "authenticated",
  "aal": "aal1",
  "amr": [{"method": "password", "timestamp": 1781802230}],
  "session_id": "a79812ec-9a88-4aa8-be8c-5cf74fca5dfb",
  "is_anonymous": false
}
```

## 4. Masked JWT Access Token

```
eyJhbGciOiJFUzI...[MASKED]...rcGekaNEoFr7tfw
```

---
> [!NOTE]
> The raw JWT token contains sensitive user authentication credentials and is masked to preserve security.
