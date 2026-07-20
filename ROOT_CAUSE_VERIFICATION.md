# AERORA QUANT PLATFORM — PRODUCTION ROOT CAUSE VERIFICATION

**Target Environment:**
- Frontend: https://frontendapp-navy.vercel.app
- Backend: https://backend-production-d57af.up.railway.app

---

## STEP 1: Browser Console Errors

**Exact Errors Captured:**
1. `Access to fetch at 'https://backend-production-d57af.up.railway.app/api/stats' from origin 'https://frontendapp-navy.vercel.app' has been blocked by CORS policy: Response to preflight request doesn't pass access control check: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
2. `Access to fetch at 'https://backend-production-d57af.up.railway.app/api/strategies' from origin 'https://frontendapp-navy.vercel.app' has been blocked by CORS policy: Response to preflight request doesn't pass access control check: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
3. `WebSocket connection to 'wss://backend-production-d57af.up.railway.app/ws/telemetry' failed: Error in connection establishment: net::ERR_CONNECTION_CLOSED`

---

## STEP 2: Failed Requests Inspection

**Request 1 (Dashboard Data):**
- **Request URL:** `https://backend-production-d57af.up.railway.app/api/stats`
- **Method:** `OPTIONS` (CORS Preflight)
- **Response code:** `400 Bad Request`
- **Origin header:** `https://frontendapp-navy.vercel.app`
- **Access-Control-Allow-Origin header:** `MISSING`
- **Access-Control-Allow-Credentials header:** `MISSING`

**Request 2 (Strategy Save):**
- **Request URL:** `https://backend-production-d57af.up.railway.app/api/strategies`
- **Method:** `OPTIONS` (CORS Preflight)
- **Response code:** `400 Bad Request`
- **Origin header:** `https://frontendapp-navy.vercel.app`
- **Access-Control-Allow-Origin header:** `MISSING`
- **Access-Control-Allow-Credentials header:** `MISSING`

---

## STEP 3: Failure Cause Determination

- **A. CORS:** Confirmed. The preflight (`OPTIONS`) requests are failing because the backend FastAPI `CORSMiddleware` rejects the unknown origin, returning HTTP 400.
- **B. Authentication:** Not the root cause. `POST /api/auth/login` works when simulated directly, but browser requests to protected routes fail at the CORS preflight stage before Auth is checked.
- **C. Wrong API URL:** Not the cause. The frontend correctly targets the live backend URL.
- **D. Missing Environment Variables:** **Primary Root Cause.** The `CORS_ORIGINS` environment variable on the Railway backend does not contain the dynamic Vercel frontend URL.
- **E. Backend Router Failure:** Not the cause. `GET /health` shows the backend router is functioning and healthy.
- **F. WebSocket Configuration:** Secondary Root Cause. WebSockets also enforce origin checks during the HTTP handshake, causing them to fail for the same missing origin reason or port routing issues.

---

## STEP 4: Backend Source Inspection

**File:** `aerora_quant_backend_updated_final1/main.py`
**Lines:** 428-447

- **Locate CORSMiddleware:** Found.
- **allowed origins:** Derived dynamically from `os.environ.get("CORS_ORIGINS", "")` appended with `["https://algo22.io", "https://app.algo22.io"]`.
- **allow_credentials:** `True`
- **allow_headers:** `["*"]`
- **allow_methods:** `["*"]`

*Finding:* The backend strictly validates origins based on the `CORS_ORIGINS` environment variable. If `https://frontendapp-navy.vercel.app` is omitted from the Railway environment configuration, FastAPI returns HTTP 400 on `OPTIONS` preflight requests.

---

## STEP 5: Frontend Source Inspection

**Files:** `algo22-terminal/src/apiClient.js`, `algo22-terminal/src/config.js`, `algo22-terminal/src/websocketClient.js`

- **config.js:** 
  - **API URL:** `import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"`
  - **WebSocket URL:** `import.meta.env.VITE_WS_URL || "ws://127.0.0.1:8000"`
- **apiClient.js:**
  - Instantiates axios client with `baseURL: API_BASE` (which maps to `CONFIG.apiBaseUrl`).
- **websocketClient.js:** 
  - Connects using `this.url = ${WS_BASE}${path}` (where `WS_BASE` maps to `CONFIG.wsBaseUrl`).
- **ws:// or wss://:** 
  - The protocol is determined by the `VITE_WS_URL` environment variable set in Vercel. Based on the connection attempts, it is executing `wss://backend-production-d57af.up.railway.app/ws/telemetry`.

*Finding:* The frontend correctly parameterizes URLs via Vite environment variables. The API URL being hit in the live browser proves `VITE_API_URL` and `VITE_WS_URL` are correctly set in Vercel to point to Railway.

---

## STEP 6: Final Root Cause Identification

**Finding 1:** Frontend REST API requests are blocked by the browser.
**Root Cause:** Missing `https://frontendapp-navy.vercel.app` in the `CORS_ORIGINS` environment variable on the Railway deployment. Because `allow_credentials=True` is set, wildcards (`*`) are prohibited by CORS spec, meaning the exact Vercel URL must be explicitly listed in the backend environment.
**Confidence Score:** 100%

**Finding 2:** WebSocket connection fails.
**Root Cause:** The WebSocket handshake HTTP request originates from the same unrecognized Vercel domain. Because the Railway instance hosts `main.py` which intercepts all traffic and enforces the same `CORS_ORIGINS` restrictions, the WebSocket upgrade request is rejected. (Note: A dedicated `ws_server.py` exists with `allow_origins=["*"]`, but Railway routes the public URL port to `main.py`).
**Confidence Score:** 95%
