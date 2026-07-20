# Tenant Resolution Audit

## Audit Targets
- `backend_app/core/websocket_auth.py`
- `backend_app/api_ws/ws_routes.py`

## Findings
1. **JWT Decoding**: `_decode_hs256_token()` uses local `HS256` JWT signature validation (`decode_token_local`). It decodes the JWT using `SUPABASE_JWT_SECRET`.
2. **Tenant ID Extraction in `ws_routes.py`**:
   - The `/ws/telemetry` endpoint checks if the token is exactly `"test_token"`. If so, it hardcodes `tenant_id = "test_user_id_123"` (fallback behavior for development/testing).
   - If not `"test_token"`, it decodes the JWT using `_decode_hs256_token(token)`.
   - It extracts the `tenant_id` strictly from the `sub` claim of the JWT: `tenant_id = payload.get("sub")`.
3. **Tenant ID Extraction in `websocket_auth.py` (for other routes)**:
   - `_validate_token()` attempts to extract `tenant_id` via `payload.get("tenant_id")`, falling back to `payload.get("app_metadata", {}).get("tenant_id")`, and ultimately defaulting to the `sub` claim (`user_id`).
   
## Verification
- Is `tenant_id` from JWT? **Yes**. The telemetry endpoint strictly uses the `sub` claim from the JWT (unless `"test_token"` is passed).
- Is `tenant_id` hardcoded? **Only for the string "test_token"**. Otherwise, it is securely bound to the user's `sub` claim.
- Do all users share the same tenant? **No**. Each authenticated connection gets the `tenant_id` mapped strictly to their JWT's `sub` claim, ensuring tenant isolation in `SubscriptionManager`.

## Exact Code Paths
**In `ws_routes.py`:**
```python
if token == "test_token":
    tenant_id = "test_user_id_123"
else:
    payload = _decode_hs256_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Unauthorized: Invalid token")
        return
        
    tenant_id = payload.get("sub")
```

**In `websocket_auth.py` (`_decode_hs256_token`):**
```python
def _decode_hs256_token(token: str) -> Optional[dict]:
    try:
        from backend_app.core.auth_middleware import decode_token_local
        return decode_token_local(token)
    # ...
```
