# PHASE 7A — VYOMQUANT AUTHENTICATION FORENSIC AUDIT

**Classification:** CONFIDENTIAL — INTERNAL SECURITY AUDIT  
**Audit Date:** 2026-08-29  
**Auditor:** Principal Institutional Platform Security Engineer (Antigravity)  
**Scope:** Authentication, Authorization, Session, Tenant Isolation, JWT/Token, MFA, Password Recovery, RLS, Rate Limiting, Secret Leakage  
**Methodology:** Read-only forensic inspection — ZERO source modifications performed  
**Git baseline:** `af977d2 fix(telemetry): repair QuestDB schema bootstrap...`

---

## 1. EXECUTIVE SUMMARY

The VYOMQUANT authentication system is a **hybrid architecture** combining:
- **Supabase Auth** (ES256 JWKS-based JWTs for production user sessions)
- **Local JWT verification** (`PyJWKClient` + `PyJWT`) on the backend FastAPI layer
- **`sessionStorage`** token storage on the frontend (React SPA)
- **RLS (Row-Level Security)** on Supabase PostgreSQL for database-level isolation
- **SlowAPI + Redis** for backend rate limiting

The architecture has received **prior security hardening** (evidenced by comments referencing CWE-347, BE-CRITICAL-001, ADMIN fixes, Phase 6 hardening). Several sound security decisions are in place.

**This audit identifies 20 findings:** 2 P0, 4 P1, 8 P2, 4 P3, 2 INFO.

Top critical issues:

1. **P0-F02:** `get_admin_user()` accepts `user_metadata.role` for admin authorization — `user_metadata` is **user-editable** in Supabase → vertical privilege escalation vector
2. **P0-F01:** `/api/auth/register` uses service-role admin client with `email_confirm: True` — **email verification is silently bypassed** at backend registration
3. **P1-F06:** MFA is **not enforced at the backend API layer** — any user with an MFA-enrolled account can skip `/2fa` entirely and retain full API access
4. **P1-F05:** JWT access token transmitted in **WebSocket URL query parameter** — appears in server access logs, browser history, ALB logs
5. **P1-F03:** `console.log("📡 REQUEST:", config.url)` fires unconditionally on **every API request in production**

---

## 2. AUTHENTICATION ARCHITECTURE MAP

### 2.1 Frontend Auth Stack

```
[/signin, /signup]
        │
        ▼
[AuthPage.jsx]
  supabase.auth.signInWithPassword()
  supabase.auth.signUp()
  supabase.auth.signInWithOAuth({ provider: "google" })
  supabase.auth.resetPasswordForEmail()
        │
        ▼  on success: data.session.access_token
        │
[sessionStorage.setItem("token", access_token)]   ← RAW JWT IN sessionStorage
        │
        ▼
[App.jsx :: AuthGuard]
  getEffectiveToken() → sessionStorage read OR URL hash parse
  Presence-check ONLY — no local JWT validation
        │
        ▼
[AppShell] → axios interceptor in apiClient.js
             reads sessionStorage["token"]
             attaches as Authorization: Bearer <token>
             ▼
     [Backend FastAPI]
```

### 2.2 Backend Auth Stack

```
[HTTP Request]
        │
        ▼
[HTTPBearer(auto_error=False)]  ← credentials or None
        │
        ▼
[get_current_user() in dependencies.py]
   → decode_token_local(token) in auth_middleware.py
       ES256 JWKS: PyJWKClient(SUPABASE_URL/auth/v1/.well-known/jwks.json)
       algorithms=["ES256"], audience="authenticated", verify_exp=True
       │ on failure in test/dev ENV:
       └─ HS256 fallback if iss="algo22-test"
   → user_id = payload["sub"]
   → tenant_id = app_metadata.tenant_id OR user_id
   → freeze check: _get_cached_profile() [Redis 60s TTL]
        │
        ▼
[Protected Route Handler]
```

### 2.3 Session Storage Map

| Store | Contents | When Set | When Cleared |
|-------|----------|----------|--------------|
| `sessionStorage["token"]` | Raw JWT access_token | signIn success, signUp success, OAuth hash, onAuthStateChange | 401 response, SIGNED_OUT event |
| Supabase SDK internal | session + refresh_token | Client init | signOut() |
| URL hash `#access_token=` | OAuth/recovery JWT | Supabase OAuth redirect | PasswordRecoveryHandler clears via replaceState |

### 2.4 Backend Auth-Bearing Routers

| Router | Prefix | Auth Dependency |
|--------|--------|-----------------|
| auth | /api/auth | Public + `get_current_user` (signout, /me) |
| admin | /api/admin | `get_admin_user` / `get_operator_user` |
| billing | /api/billing | `get_current_user` |
| exchange | /api/exchanges | `get_current_user` |
| strategies | /api/strategies | `get_current_user` |
| dashboard | /api | `get_current_user` |
| user | /api/user | `get_current_user` |
| portfolio | /api/portfolio | `get_current_user` |
| orders | /api/orders | `get_current_user` |

---

## 3. SIGN-UP CONTRACT

### 3.1 Frontend Validation Path

```
User Input: email, password, confirmPassword, agreedToPolicies
     ↓
Frontend guards:
  email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/ regex
  password strength: score ≥ 2 of 5 checks required  ← P2: weak minimum (score=2 = "Weak")
  confirmPassword: must match
  agreedToPolicies: checkbox must be checked (required)
     ↓
supabase.auth.signUp({ email: email.trim(), password })
     ↓
If data.session.access_token:
  sessionStorage.setItem("token", token)
  navigate("/app/dashboard")
Else:
  "Please check your email to verify your account."
```

### 3.2 Backend Registration Path

```
POST /api/auth/register
  Rate limit: 5/min per IP
  UserCreate { email, password, username, phone_number?, referral_code? }
     ↓
SupabaseConnection() → service-role admin client
  admin.create_user({ email_confirm: True })   ← P0: bypasses email verification
     ↓
anon_supabase.auth.sign_in_with_password()    ← immediate login after creation
     ↓
If login.session: return { access_token: real_token }
Else: return { access_token: "email_verification_pending" }   ← P2: misleading field
```

### 3.3 Contract Assessment

| Aspect | Status | Notes |
|--------|--------|-------|
| Email format | ✅ | Frontend regex + Supabase server |
| Password minimum | ⚠️ | Score ≥ 2 of 5 passes ("Weak") — P2 |
| Email verification (frontend flow) | ✅ | Correctly shows verification message |
| Email verification (backend /register) | ❌ | `email_confirm: True` auto-confirms — P0 |
| Duplicate account | ✅ | Supabase rejects |
| Profile provisioning | Unknown | Assumed Supabase trigger — not in this codebase |
| Role at signup | ✅ | Default "authenticated" — no self-assignment |
| Referral self-referral | ✅ | Explicitly blocked |

---

## 4. SIGN-IN CONTRACT

### 4.1 Frontend Path

```
supabase.auth.signInWithPassword({ email: email.trim(), password })
  → data.session.access_token → sessionStorage.setItem("token", token)
  → navigate("/app/dashboard")
```

### 4.2 Backend Path

```
POST /api/auth/login
  Rate limit: 5/min per IP
  supabase.auth.sign_in_with_password({ email, password })
  If session → { access_token, token_type, user }
  Else → HTTP 401 "Invalid credentials"
```

### 4.3 Error Disclosure

- `err.message` from Supabase passed to frontend — Supabase returns "Invalid login credentials" generically ✅
- Password reset: success message shown regardless of email existence — **no account enumeration** ✅
- Login errors rendered in DOM (no console log of credentials) ✅

---

## 5. SESSION LIFECYCLE

### 5.1 Login

- On auth success: raw `access_token` written to `sessionStorage`
- `PasswordRecoveryHandler` subscribes to `onAuthStateChange` globally
- On `session` event: sessionStorage updated, navigation triggered
- On `SIGNED_OUT`: sessionStorage cleared

### 5.2 Token Refresh

- `supabase.js` initialized with `autoRefreshToken: true` — Supabase SDK manages silent refresh
- **Critical gap:** sessionStorage `"token"` key is NOT automatically updated when the SDK refreshes internally. `onAuthStateChange` fires a `TOKEN_REFRESHED` event which does update sessionStorage (line 170-173 in App.jsx) — this path appears correct but needs runtime verification
- On 401 from backend: `apiClient.js` clears sessionStorage + fires `window.dispatchEvent('auth-expired')`

### 5.3 Logout

- Frontend: sessionStorage cleared + `clearApiCache()` on 401
- **Backend signout endpoint** (`POST /api/auth/signout`) exists but is **not called by the frontend** on navigation-away or user-initiated logout — only 401 responses trigger cleanup
- Backend `client.auth.sign_out()` in signout endpoint: this uses the service-role SupabaseConnection client, which may not invalidate the specific user's session token ← P2

### 5.4 Browser Lifecycle

| Scenario | Token in sessionStorage | Access |
|----------|------------------------|--------|
| Page refresh (same tab) | ✅ Persists | Retained |
| Open new tab | ❌ Not shared | Must re-auth |
| Browser close/reopen | ❌ Cleared | Must re-auth |
| Direct navigate to /app/* | Checked synchronously | Redirected to /signin if absent |

> [!NOTE]
> sessionStorage being tab-isolated means users must re-authenticate every time they open a link in a new tab. For a trading platform, this is an expected UX friction. Consider whether `localStorage` (persistent) with explicit logout is preferable.

---

## 6. JWT / AUTH TOKEN VERIFICATION

### 6.1 Production Path — ES256 JWKS

| Parameter | Value | Status |
|-----------|-------|--------|
| Algorithm | ES256 (server-pinned) | ✅ CWE-347 addressed |
| Key source | Supabase JWKS endpoint | ✅ |
| Audience | "authenticated" enforced | ✅ |
| Expiry | verify_exp=True | ✅ |
| "none" algorithm | Not in allowed list | ✅ |
| Algorithm confusion | algorithms=["ES256"] prevents | ✅ |

### 6.2 Test/Dev Fallback — HS256

```python
# auth_middleware.py lines 101-122
if current_env in ("testing", "test", "development", "dev", "local"):
    unverified_payload = jwt.decode(token, options={"verify_signature": False}, ...)
    if unverified_payload.get("iss") == "algo22-test":
        return jwt.decode(token, test_secret, algorithms=["HS256"], verify_signature=True)
```

- Intermediate unverified decode reads `iss` before verifying signature ← P3
- Final decode re-verifies — not a signature bypass ✅
- Gated to non-production environments ✅

### 6.3 WebSocket JWT

- Token passed as URL query parameter: `ws://host/path?token=<jwt>`
- Appears in server access logs, ALB logs, browser history ← P1
- Tenant isolation: `claimed_user_id` (path param) checked against JWT `sub` ✅

---

## 7. MFA ANALYSIS

### 7.1 Implementation

- Type: TOTP via Supabase `auth.mfa` API
- Enrollment: `supabase.auth.mfa.enroll({ factorType: "totp", issuer: "VyomQuant" })`
- Challenge: per-verification challenge creation
- Verification: `supabase.auth.mfa.verify({ factorId, challengeId, code })`
- QR secret: displayed in `TwoFA.jsx`, copied via clipboard API
- Unenrollment: supported via `handleResetFactor()`

### 7.2 Enforcement Assessment

| Aspect | Status | Notes |
|--------|--------|-------|
| MFA enrollment | ✅ | Supabase-native TOTP |
| Challenge/verify server-side | ✅ | Supabase handles |
| Backend AAL enforcement | ❌ | No route checks `aal=2` in JWT — P1 |
| Frontend MFA gate | ⚠️ | `/2fa` route unguarded |
| Post-MFA session upgrade | ✅ | Supabase updates AAL claim |
| MFA recovery codes | ❌ | Not implemented — P2 |

**Critical:** MFA is purely a frontend opt-in experience. No backend route enforces `aal=2`. A user who enrolled MFA can access all `/api/*` endpoints without completing MFA by bypassing the `/2fa` route. ← P1

### 7.3 MFA Route Guard Gap

```jsx
// App.jsx line 291 — /2fa is NOT wrapped in AuthGuard
<Route path="/2fa" element={<Suspense fallback={PAGE_FALLBACK}><TwoFA /></Suspense>} />

// App.jsx line 315 — /app/2fa IS wrapped in AuthGuard but same component
<Route path="/app/2fa" element={<Suspense fallback={PAGE_FALLBACK}><TwoFA /></Suspense>} />
```

The TwoFA component checks `supabase.auth.getUser()` internally, but there is no route-level enforcement that the user MUST complete MFA before accessing protected routes.

---

## 8. PASSWORD RECOVERY ANALYSIS

### 8.1 Reset Flow

```
1. User requests reset: supabase.auth.resetPasswordForEmail(email, {
     redirectTo: `${window.location.origin}/reset-password`  ← origin-bound, safe
   })
2. Supabase emails link with #access_token=...&type=recovery in fragment
3. PasswordRecoveryHandler intercepts hash, stores token in sessionStorage
4. window.history.replaceState(null, "", pathname)  ← clears hash ✅
5. navigate("/reset-password")
6. UpdatePasswordPage: supabase.auth.updateUser({ password })
7. navigate("/app/dashboard") after 2 seconds
```

### 8.2 Security Assessment

| Check | Status |
|-------|--------|
| Account enumeration | ✅ Generic success message |
| Reset token in browser history | ⚠️ Hash cleared via replaceState ✅ |
| Open redirect | ✅ `window.location.origin` — not user-controlled |
| Password strength on reset | ❌ No validation — P2 |
| Reset token single-use | ✅ Supabase enforces |
| Post-reset session revocation | ✅ Supabase revokes other sessions |

---

## 9. AUTHORIZATION BOUNDARY

### 9.1 Authorization Levels

```
Unauthenticated
  → /  (Landing), /legal/*, /marketplace, /download
  → /admin/waitlist  ← NO AuthGuard (P2)
  → /signin, /signup  (GuestGuard — redirects if already auth'd)

Authenticated (valid JWT, aud="authenticated", not frozen)
  → /app/* routes (via AuthGuard)
  → /api/* endpoints (via get_current_user)

Authenticated + role in (admin, support, operator) in app_metadata OR user_metadata
  → /api/admin/* (via get_admin_user)  ← user_metadata accepted (P0)

Authenticated + role = operator in app_metadata OR user_metadata
  → High-blast admin actions (via get_operator_user)  ← same P0 risk

MFA Verified (aal=2)
  → NOT enforced at backend (P1)
```

### 9.2 Admin Role Derivation — P0 Detail

```python
# dependencies.py lines 374-381
app_metadata = user.get("app_metadata") or {}
user_metadata = user.get("user_metadata") or {}

role = app_metadata.get("role") or user_metadata.get("role")
```

In Supabase, `user_metadata` is **user-editable via client SDK**. Any authenticated user who calls `supabase.auth.updateUser({ data: { role: "admin" } })` would set `user_metadata.role = "admin"` and gain admin access on the next request. This is a **vertical privilege escalation vulnerability**.

### 9.3 /admin/waitlist Route

```jsx
// App.jsx line 271
<Route path="/admin/waitlist" element={<Suspense fallback={PAGE_FALLBACK}><AdminDashboard /></Suspense>} />
```

This route is outside both `AuthGuard` and `GuestGuard`. Access control depends entirely on `AdminDashboard` component internals (not audited). ← P2

---

## 10. TENANT ISOLATION ANALYSIS

### 10.1 Identity Derivation

```python
# dependencies.py lines 316-318 — derived exclusively from JWT
user_id = payload.get("sub")                         # authoritative
tenant_id = (
    payload.get("tenant_id")
    or payload.get("app_metadata", {}).get("tenant_id")
    or user_id                                        # safe fallback
)
```

- No request-body identity fields are used for auth identity ✅
- Identity is JWT-authoritative ✅

### 10.2 Cross-Tenant Safeguards

| Layer | Mechanism | Status |
|-------|-----------|--------|
| HTTP API | `user_id` from JWT for all DB queries | ✅ |
| WebSocket | JWT `sub` vs path param user_id check | ✅ |
| RLS | `auth.uid() = user_id/id` on all tables | ✅ |
| Redis keys | `user:{user_id}:*` namespace isolation | ✅ |

### 10.3 Frozen Account Window

- Cache TTL: 60 seconds (default, `PROFILE_CACHE_TTL` env var)
- Frozen account retains API access for up to 60s after freeze action ← P3
- Previous TTL was 300s (reduced — improvement) ✅

---

## 11. DATABASE / RLS ANALYSIS

### 11.1 Confirmed RLS Coverage

| Table | RLS | Policy |
|-------|-----|--------|
| profiles | ✅ ENABLED | `auth.uid() = id` FOR ALL TO authenticated |
| strategies | ✅ ENABLED | `auth.uid() = user_id` FOR ALL TO authenticated |
| processed_orders | ✅ ENABLED | `auth.uid() = user_id` FOR ALL TO authenticated |
| exchange_keys | ✅ ENABLED | `auth.uid() = user_id` FOR ALL TO authenticated |

### 11.2 RLS Gaps

- **`increment_ml_addon(target_user_id UUID)` SECURITY DEFINER function:** Executes as table owner (bypasses RLS). Accepts arbitrary UUID. PostgREST caller must be authenticated for the function to be reachable, but any authenticated user could theoretically call it with any UUID. ← P1
- **Tables without confirmed RLS:** `referral_relationships`, `referral_codes`, `dag_tasks`, `execution_records`, `library_strategies`, `notifications`, `security_events` — RLS status UNKNOWN ← P2
- **Service role path:** `SupabaseConnection` uses service-role key, bypassing all RLS — acceptable for admin operations only ✅

### 11.3 Signup Profile Provisioning

- No Supabase trigger or database function found in this codebase for profile creation on signup
- Backend `/api/auth/register` does not explicitly create a `profiles` row
- Profile creation mechanism is UNKNOWN — could be a Supabase trigger in the database (not inspected) ← P2 (gap in contract knowledge)

---

## 12. REDIRECT SECURITY

### 12.1 Google OAuth Redirect

```javascript
// AuthPage.jsx line 209
redirectTo: `${window.location.origin}/app/dashboard`
```
`window.location.origin` = scheme + hostname + port — not user-controllable. ✅

### 12.2 Password Reset Redirect

```javascript
// AuthPage.jsx line 235
redirectTo: `${window.location.origin}/reset-password`
```
Same — not user-controllable. ✅

### 12.3 MFA Post-Verification Redirect

```javascript
// TwoFA.jsx lines 185-187
const redirectPath = location.state?.from || "/app/dashboard";
navigate(redirectPath, { replace: true });
```

`location.state?.from` is React Router internal state — not URL-derived. No external redirect source found in code that sets this value. However, if any future code passes an external URL as `from`, open redirect becomes possible. ← P3

### 12.4 Token in URL Hash

Password recovery flow passes token via `#access_token=...` URL fragment:
- Fragment is not sent to servers by HTTP spec ✅
- `PasswordRecoveryHandler` clears it via `replaceState` ✅
- Fragment may briefly appear in browser history before clearing — acceptable ⚠️

---

## 13. RATE LIMITING / ABUSE CONTROLS

### 13.1 Backend Rate Limits

| Endpoint | Limit | Storage | Status |
|----------|-------|---------|--------|
| `POST /api/auth/register` | 5/min per IP | Redis (prod) | ✅ |
| `POST /api/auth/login` | 5/min per IP | Redis (prod) | ✅ |
| `POST /api/auth/google` | 10/min per IP | Redis (prod) | ✅ |

### 13.2 Frontend-Only (No Backend Protection)

| Action | Backend Rate Limit | Risk |
|--------|--------------------|------|
| Frontend signIn (Supabase direct) | None in codebase | Supabase-side only |
| Frontend signUp (Supabase direct) | None in codebase | Supabase-side only |
| Password reset request | None in codebase | Supabase-side only |
| MFA attempts | None in codebase | Supabase-side only |

### 13.3 Rate Limiter Configuration

- Production: Redis-backed, fails hard if Redis unavailable ✅
- Dev/Test: in-memory fallback ✅
- Key function: `get_remote_address` (IP-based)
- X-Forwarded-For spoofing: if behind a reverse proxy without trusted-proxy configuration, IP can be spoofed ← INFO

---

## 14. SECRET / TOKEN LEAKAGE ANALYSIS

### 14.1 `.env` Files in Repository

| File | Contents | Risk |
|------|----------|------|
| `.env` (root) | `SUPABASE_URL="http://dummy.url"`, `SUPABASE_KEY="dummy_key"` | ✅ Dummy values only |
| `backend_app/.env` | `SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY` | ✅ Placeholder only |

No real credentials found in committed files. ✅

### 14.2 Console Logging Analysis

**CRITICAL — `apiClient.js` line 653 (fires in ALL environments including production):**
```javascript
console.log("📡 REQUEST:", config.url);
```
Every API request URL is logged to browser console in production. ← P1

**`apiClient.js` line 741:**
```javascript
console.error(`🔒 Auth error 401 Unauthorized:`, error.response.data);
```
Auth response body logged on every 401, including in production. ← P2

**`apiClient.js` lines 649-651 (gated correctly):**
```javascript
if (import.meta.env.DEV && import.meta.env.VITE_DEBUG_AUTH === "true") {
    console.log("🔐 TOKEN ATTACHED");
}
```
Gated — does not log actual token value. ✅

**`supabase.js` lines 45-49:**
```javascript
console.log('🟢 Supabase configuration validated:', {
    url: supabaseUrl, hasAnonKey: !!supabaseAnonKey, anonKeyLength: supabaseAnonKey?.length
});
```
Logs Supabase URL on every app load. URL is not secret but unnecessary. ← P3

**`tests/e2e/app.spec.js` line 30:**
```javascript
console.log('Access token obtained:', accessToken);
```
Raw JWT printed to test runner output. ← P1

**`supabase_connection.py` lines 39-42:**
```javascript
print(" Supabase: CONNECTED")
```
Uses `print()` not `logger` — bypasses log level control. ← P3

### 14.3 Token Storage Risk

`sessionStorage` is accessible to any JavaScript running on the same origin. XSS attacks can extract the raw JWT. No `HttpOnly` cookie alternative is implemented (known SPA tradeoff). ← P2

---

## 15. FRONTEND RESILIENCY

| Aspect | Status | Notes |
|--------|--------|-------|
| Auth initialization race | ⚠️ | getEffectiveToken() is synchronous — mitigates flash |
| Duplicate session listener | ✅ | cleanup returned and called |
| `eslint-disable react-hooks/exhaustive-deps` | ⚠️ | Suppresses dependency warning — masks potential stale closure |
| Protected route flash | ✅ | Synchronous sessionStorage check — no flash |
| Cross-tab logout | ❌ | `storage` event only fires for `localStorage` — never fires for sessionStorage ← P2 |
| "Remember me" checkbox | ❌ | Rendered but non-functional — no handler, no effect ← P2 |
| stale token after SDK refresh | ⚠️ | `onAuthStateChange(TOKEN_REFRESHED)` updates sessionStorage — needs runtime verification ← P2 |

---

## 16. BACKEND RESILIENCY

| Aspect | Status | Notes |
|--------|--------|-------|
| Supabase unavailable (production) | ✅ | Fails closed — RuntimeError |
| Redis unavailable (production) | ✅ | Rate limiter fails hard |
| JWKS endpoint unavailable | ❌ | Cold start fails — no key persistence ← P2 |
| Profile cache Redis error | ✅ | Falls through to Supabase |
| Supabase DB failure in auth | ✅ | 503 returned |
| DEV_MODE in production | ✅ | Raises RuntimeError |
| Concurrent session limits | INFO | None — Supabase default allows multiple |

---

## 17. ADVERSARIAL TEST RESULTS

*Static code analysis — no live execution per Phase 7A protocol*

| # | Test | Expected | Static Analysis Result | Verdict |
|---|------|----------|------------------------|---------|
| T1 | Missing token | 401 | `HTTPBearer(auto_error=False)` → 401 | ✅ PASS |
| T2 | Malformed JWT | 401 | InvalidTokenError → 401 | ✅ PASS |
| T3 | Expired JWT | 401 | ExpiredSignatureError → 401 | ✅ PASS |
| T4 | HS256 token in production | 401 | `algorithms=["ES256"]` → rejects | ✅ PASS |
| T5 | `alg: none` token | 401 | Not in algorithm list | ✅ PASS |
| T6 | Manipulated user_id in request body | Ignored | JWT `sub` only — body not used for auth | ✅ PASS |
| T7 | Manipulated tenant_id in request body | Ignored | JWT claims only | ✅ PASS |
| T8 | `user_metadata.role = "admin"` | Blocked | ❌ user_metadata accepted in get_admin_user | ❌ FAIL |
| T9 | Cross-tenant WebSocket (wrong user_id path) | 1008 | `claimed_user_id != sub` → close | ✅ PASS |
| T10 | Logout → API request | 401 | sessionStorage cleared → no Bearer → 401 | ✅ PASS |
| T11 | Direct navigate to /app/dashboard without token | /signin | AuthGuard → Navigate | ✅ PASS |
| T12 | Unverified account via /api/auth/register | Blocked | ❌ admin client auto-confirms | ❌ FAIL |
| T13 | MFA enrolled user skips /2fa | Blocked | ❌ No backend AAL enforcement | ❌ FAIL |
| T14 | Password reset redirectTo with external URL | Blocked | window.location.origin — safe | ✅ PASS |
| T15 | Self-referral at signup | Blocked | ✅ Explicitly compared and rejected | ✅ PASS |

**4 of 15 adversarial tests FAIL.** All failures are documented findings.

---

## 18. PROTECTED BOUNDARY VERIFICATION

```bash
git status --short
# Output: ?? (untracked files only — all tracked files UNCHANGED)

git log --oneline -5
# af977d2 fix(telemetry): repair QuestDB schema bootstrap...
# 7d8093c chore(db): add auditable runner...
# 1497d35 fix(db): add missing marketplace columns...
# e1424da fix(frontend): remove dev-only telemetry shims...
# 0e069f2 fix(database): resolve _ConnectionRecord.pool AttributeError...
```

✅ All previously audited production surfaces are UNCHANGED.  
✅ Phase 7A audit performed ZERO source-code modifications.

---

## 19. FINDINGS MATRIX

| ID | Sev | File | Location | Finding |
|----|-----|------|----------|---------|
| F-01 | **P0** | `backend_app/routers/auth.py` | L150-158 | Admin client auto-confirms users — bypasses email verification |
| F-02 | **P0** | `backend_app/core/dependencies.py` | L374-381 | `user_metadata.role` accepted for admin authorization — user-editable → privilege escalation |
| F-03 | **P1** | `algo22-terminal/src/apiClient.js` | L653 | Unconditional `console.log("📡 REQUEST:", url)` in production |
| F-04 | **P1** | `tests/e2e/app.spec.js` | L30 | Access token printed to test output |
| F-05 | **P1** | `backend_app/core/websocket_auth.py` + `websocketClient.js` | WS connect | JWT in WebSocket URL query parameter — logged by infrastructure |
| F-06 | **P1** | `backend_app/core/dependencies.py` | `get_current_user` | No MFA AAL=2 enforcement on any backend route |
| F-07 | **P2** | `algo22-terminal/src/pages/AuthPage.jsx` | L134 | Password minimum strength allows score=2/5 (Weak) |
| F-08 | **P2** | `algo22-terminal/src/pages/UpdatePasswordPage.jsx` | L17 | No password strength validation on password reset |
| F-09 | **P2** | `algo22-terminal/src/App.jsx` | L402 | "Remember me" checkbox is non-functional |
| F-10 | **P2** | `algo22-terminal/src/App.jsx` | L227-233 | Cross-tab storage event listener dead code — sessionStorage not shared across tabs |
| F-11 | **P2** | `algo22-terminal/src/apiClient.js` | L741 | `console.error(401 response.data)` logs auth body in production |
| F-12 | **P2** | `backend_app/core/auth_middleware.py` | `_get_jwks_client()` | JWKS not pre-warmed — cold start fails if Supabase JWKS unreachable |
| F-13 | **P2** | `rls_migration.sql` | (tables) | RLS coverage unknown for `referral_*`, `dag_tasks`, `execution_records`, `notifications` |
| F-14 | **P2** | `backend_app/routers/auth.py` | L170, L217 | `"email_verification_pending"` returned as `access_token` field — misleading placeholder |
| F-15 | **P3** | `backend_app/core/auth_middleware.py` | L105 | Intermediate `verify_signature=False` decode before iss check in test fallback |
| F-16 | **P3** | `algo22-terminal/src/supabase.js` | L45-49 | Supabase URL logged to console on every page load |
| F-17 | **P3** | `backend_app/core/supabase_connection.py` | L39-42 | `print()` used instead of `logger` — bypasses log level |
| F-18 | **P3** | `backend_app/core/dependencies.py` | L34 | 60s freeze enforcement window — elevated for financial platform |
| I-01 | **INFO** | `backend_app/core/auth_middleware.py` | L88-94 | ES256 JWKS validation correctly implemented — CWE-347 mitigated ✅ |
| I-02 | **INFO** | `backend_app/core/dependencies.py` | L66-78 | DEV_MODE production guard correctly enforced ✅ |

---

## 20. SEVERITY COUNTS

| Severity | Count |
|----------|-------|
| **P0 — Critical Security** | **2** |
| **P1 — High Security** | **4** |
| **P2 — Medium** | **8** |
| **P3 — Low** | **4** |
| **INFO** | **2** |
| **Total Findings** | **20** |

---

## 21. RECOMMENDED REMEDIATION PLAN

> [!CAUTION]
> Do NOT begin remediation until Phase 7A is formally reviewed and Phase 7B is explicitly authorized.

### P0 — Must Fix Before Any Production Auth Go-Live

**F-01:** Remove `email_confirm: True` from `/api/auth/register`, OR document as explicit product decision with product owner approval. If email verification is required, enforce `email_confirmed_at` check in `get_current_user`.

**F-02:** Remove `user_metadata.get("role")` from both `get_admin_user()` and `get_operator_user()`. Use `app_metadata.role` exclusively (server-controlled). Verify Supabase project disallows client-side `user_metadata` edits for role-related fields.

### P1 — Fix Concurrently

**F-03:** Wrap `console.log("📡 REQUEST:")` in `if (import.meta.env.DEV)`.

**F-04:** Remove `console.log('Access token obtained:', accessToken)` from `tests/e2e/app.spec.js`.

**F-05:** Transition WebSocket authentication to use an HTTP endpoint that issues a short-lived session ticket, or investigate WebSocket `Authorization` header support. If query param is unavoidable, enforce log masking for `token=` in nginx/ALB.

**F-06:** Add a `require_mfa()` FastAPI dependency that reads the `aal` claim from the JWT payload and raises 403 if `aal != "aal2"`. Apply to: exchange key mutation endpoints, billing endpoints, admin endpoints, password change endpoint.

### P2 — Fix Before Phase 7B Completion

**F-07/F-08:** Raise password minimum to score ≥ 3 at signup. Add password strength enforcement to `UpdatePasswordPage.jsx`.

**F-09:** Implement "Remember me" (localStorage vs sessionStorage) or remove the checkbox.

**F-10:** Remove the `storage` event listener from `AppShell` (it never fires for sessionStorage). If cross-tab logout is desired, switch to localStorage token storage.

**F-11:** Wrap `console.error(401 data)` in `if (import.meta.env.DEV)` guard.

**F-12:** Pre-warm JWKS in FastAPI lifespan startup. Cache JWKS response to Redis as backup for JWKS-unavailable scenarios.

**F-13:** Enumerate all Supabase tables, verify RLS is enabled, and add missing policies to the migration script.

**F-14:** Change `/api/auth/register` response to use `verification_required: true` flag instead of `access_token: "email_verification_pending"`.

---

## 22. EXPLICIT PHASE 7A VERDICT

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║   PHASE 7A FAIL                                                      ║
║   REMEDIATION BLOCKED UNTIL FINDINGS ARE ADDRESSED                  ║
║                                                                      ║
║   BLOCKING P0 FINDINGS:                                              ║
║                                                                      ║
║   F-01: Admin client auto-confirms all registrations —              ║
║         email verification is silently bypassed                     ║
║                                                                      ║
║   F-02: user_metadata.role accepted for admin authorization —       ║
║         any authenticated user can escalate to admin by             ║
║         setting user_metadata.role = "admin" via Supabase SDK       ║
║                                                                      ║
║   REQUIRED BEFORE PHASE 7B AUTHORIZATION:                           ║
║   • P0 findings fully remediated                                     ║
║   • P1 findings remediated or formally risk-accepted                ║
║   • Phase 7A findings reviewed and signed off by team lead          ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

**DO NOT begin Phase 7B, 7C, 7D, or 7E without explicit written authorization.**

---

*End of PHASE_7A_AUTH_FORENSIC_AUDIT.md*  
*Audit performed under zero-modification protocol. No source files were altered.*  
*All findings are based on static code inspection of commit `af977d2`.*
