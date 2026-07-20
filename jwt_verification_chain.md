# Phase 1: JWT Verification Chain Analysis

Based on the inspection of `core/auth_middleware.py`, `core/config.py`, `routers/auth.py`, and `main.py`:

* **Exact verification function:** `jwt.decode()` (from the PyJWT library, invoked in `verify_token()` inside `core/auth_middleware.py`).
* **Exact secret variable used:** `settings.SUPABASE_JWT_SECRET` (defined in `core/config.py` and mapped to `os.getenv("SUPABASE_JWT_SECRET")`). Note that `JWT_SECRET` exists in config but is *not* used for this validation.
* **Issuer validation:** **None**. The `issuer` parameter is not passed to `jwt.decode()`, meaning the issuer (`iss` claim) is completely ignored during validation.
* **Audience validation:** **"authenticated"**. The `audience="authenticated"` parameter is explicitly passed to `jwt.decode()`, which enforces that the token must contain this exact audience (`aud` claim).
* **Expiration validation:** **Yes (Automatic)**. The `jwt.decode()` function inherently validates the `exp` claim, and the middleware explicitly catches `jwt.ExpiredSignatureError`.
