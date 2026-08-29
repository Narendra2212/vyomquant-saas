# VYOMQUANT — PHASE 6A FORENSIC AUDIT REPORT: EXCHANGE MANAGER & VAULT
**Surface Audited**: Exchange Management & API Key Vault (`ExchangeManager.jsx`, `backend_app/routers/exchange.py`, `backend_app/backend/api_key_vault.py`, `backend_app/core/models/pydantic_models.py`, `algo22-terminal/src/api/modules/exchange.js`)  
**Audit Date**: 2026-08-26  
**Final Verdict**: `AUDIT COMPLETE — REMEDIATION REQUIRED`

---

## 1. Executive Summary

A comprehensive, zero-code-change forensic audit was conducted across the entire **Exchange Management & API Key Vault** vertical of the VyomQuant SaaS trading platform. 

The Exchange Manager is the foundational prerequisite for all live automated algorithmic trading. It is responsible for:
1. Managing institutional and retail crypto exchange API credentials across 100+ CCXT-supported venues.
2. Encrypting and decrypting API keys/secrets via AES-256 MultiFernet Vault.
3. Enforcing strict multi-tenant isolation so no trader can ever access or leak another trader's exchange credentials.
4. Performing pre-flight connectivity, clock synchronization, permission testing, and wallet balance checks.
5. Providing exchange health metrics and capability registries to downstream live trading bots and risk monitors.

### Summary of Audit Findings:
- **P0 Findings**: **1** (Critical Production Blocker — `TypeError` in `store_keys` keyword argument mismatch preventing all key saves)
- **P1 Findings**: **2** (High Severity — Missing `uid`/`label` in `ExchangeKeysRequest` model; Unhandled `Promise.all` rejection & unmount memory leaks in UI)
- **P2 Findings**: **2** (Medium Severity — Duplicate route shadowing on `/schema/{exchange_id}`; Hardcoded `bot_count: 0` in `list_exchanges`)
- **P3 Findings**: **1** (Low Severity — Raw backend error details displayed in toasts)
- **INFO Findings**: **1** (Architectural Strength — MultiFernet AES-256 encryption and zero plaintext secret leakage in API responses)

---

## 2. Selected Surface and Justification

### Why ExchangeManager is the Correct Next Surface:
1. **Critical Architectural Dependency**: Following the Dashboard (Phase 1–4) and Profile (Phase 5), the Exchange Vault is the foundational operational bridge between the SaaS platform and external financial markets. Live trading, portfolio balances, and risk gates cannot function without authenticated exchange connections.
2. **High Security & Financial Gravity**: The Exchange Vault directly stores private cryptographic keys, API secrets, and passphrases capable of authorizing live orders and asset movements. Any vulnerability in key storage, serialization, or tenant scoping represents a critical P0 financial and data security risk.
3. **Multi-Tenant Isolation Integrity**: Every exchange connection must be strictly bound to the authenticated user's JWT `user_id`. Verifying that IDOR vulnerabilities cannot cross tenant boundaries is essential before live strategy deployment.

---

## 3. Architecture & Data Flow

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                    EXCHANGES VAULT                                     │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  1. Frontend (ExchangeManager.jsx)                                                     │
│     ├── GET /api/exchanges/supported  (List supported CCXT venues)                     │
│     ├── GET /api/exchanges/           (List user's connected exchanges)                │
│     ├── GET /api/exchanges/schema/:id (Fetch dynamic credential form schema)           │
│     ├── POST /api/exchanges/test      (Test connection with plaintext keys in RAM)     │
│     ├── POST /api/exchanges/keys      (Encrypt & store credentials in Vault)           │
│     └── DELETE /api/exchanges/:id     (Safely disconnect venue with active bot check)  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  2. API Router (backend_app/routers/exchange.py)                                       │
│     ├── JWT Authentication (get_current_user Dependency)                               │
│     ├── Rate Limiting (60/min via slowapi)                                             │
│     └── Notification Dispatches (exchange_connected, exchange_disconnected)            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  3. Security & Storage Layer                                                           │
│     ├── APIKeyVault (backend_app/backend/api_key_vault.py)                             │
│     │   └── MultiFernet (AES-256 CBC with HMAC-SHA256, master key rotation)            │
│     ├── ConnectionEngine (backend_app/backend/connection_engine.py)                    │
│     │   └── CCXT.pro async instance pooling, rate limiting, and balance fetching       │
│     └── Supabase Database                                                              │
│         └── table: exchange_keys (user_id, exchange_id, encrypted_api_key, etc.)       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. API Contract Matrix

| Endpoint | Method | Auth Required | Purpose | Status |
| :--- | :--- | :--- | :--- | :--- |
| `/api/exchanges/supported` | `GET` | Yes (`get_current_user`) | CCXT venue list with spot/futures/margin caps | **PASS** |
| `/api/exchanges/schema/{id}` | `GET` | Yes / Optional | Credential form fields & validation rules | **P2.1 Duplicate Shadowing** |
| `/api/exchanges/test` | `POST` | Yes (`get_current_user`) | Ephemeral key validation & balance check | **P1.1 Missing UID** |
| `/api/exchanges/keys` | `POST` | Yes (`get_current_user`) | Encrypts & stores credentials in Supabase | **P0.1 TypeError Blocker** |
| `/api/exchanges/` | `GET` | Yes (`get_current_user`) | Lists user's connected exchanges (masked keys) | **P2.2 Hardcoded bot_count** |
| `/api/exchanges/{id}` | `DELETE` | Yes (`get_current_user`) | Disconnects venue & evicts connection pool | **PASS (Active bot safe)** |
| `/api/exchanges/test-stored` | `POST` | Yes (`get_current_user`) | Tests existing stored encrypted credentials | **PASS** |
| `/api/exchanges/connections/{id}/reconnect` | `POST` | Yes (`get_current_user`) | Re-establishes connection socket | **PASS** |

---

## 5. Database & RLS Matrix

### Table: `exchange_keys`
- **Columns**: `user_id` (UUID/Text, PK), `exchange_id` (Text, PK), `encrypted_api_key` (Text), `encrypted_secret_key` (Text), `encrypted_password` (Text, Nullable), `created_at` (Timestamp), `updated_at` (Timestamp).
- **Multi-Tenancy**: All queries (`select`, `upsert`, `delete`) in `api_key_vault.py` and `exchange.py` explicitly filter by `eq("user_id", user["id"])`.
- **Secret Redaction**: Raw keys are never stored in the database. Decryption only occurs ephemerally in server RAM when executing live trade calls.

---

## 6. Findings Matrix

### [P0.1] Keyword Argument Mismatch in `vault.store_exchange_keys` Call
- **Severity**: **P0 (Critical / Production Blocker)**
- **File**: `backend_app/routers/exchange.py:171-178`
- **Exact Defect**: `store_keys` endpoint calls `vault.store_exchange_keys(user_id=user["id"], exchange_id=body.exchange_id, raw_api_key=body.api_key, raw_secret_key=body.secret_key, raw_password=body.password, label=getattr(body, "label", None))`
  However, `APIKeyVault.store_exchange_keys` in `backend_app/backend/api_key_vault.py` is defined as:
  `def store_exchange_keys(self, user_id: str, exchange_id: str, raw_api_key: str, raw_secret: str, raw_password: Optional[str] = None) -> bool`.
  `raw_secret_key` and `label` are unrecognized keyword arguments.
- **Expected Behavior**: Exchange API keys are encrypted and vaulted cleanly to Supabase.
- **Actual Behavior**: Runtime `TypeError: APIKeyVault.store_exchange_keys() got an unexpected keyword argument 'raw_secret_key'`. The exception is caught and re-raised as HTTP 500: `"Failed to store keys in vault: ..."`, completely preventing any user from storing exchange credentials.
- **Reproduction Scenario**: Call `POST /api/exchanges/keys` with valid credentials.
- **Evidence**: Verified via Python inspect signature test against `backend_app/backend/api_key_vault.py`.
- **Recommended Remediation**: Align keyword arguments to `raw_secret=body.secret_key` and update `APIKeyVault.store_exchange_keys` to accept optional `raw_secret_key` and `label`.

---

### [P1.1] Missing `uid` and `label` in `ExchangeKeysRequest` Schema
- **Severity**: **P1 (High Severity / Multi-Exchange Auth Incompatibility)**
- **File**: `backend_app/core/models/pydantic_models.py:42-46`
- **Exact Defect**: `ExchangeKeysRequest` model only declares `exchange_id: str`, `api_key: str`, `secret_key: str`, `password: Optional[str] = None`. It lacks `uid: Optional[str] = None` and `label: Optional[str] = None`.
- **Expected Behavior**: Exchanges requiring Account/User ID (e.g., Gate.io, KuCoin, OKX, Bitget) receive the `uid` parameter from frontend credential form for authentication and signature generation.
- **Actual Behavior**: `uid` submitted by `ExchangeManager.jsx` is stripped by Pydantic model instantiation. Calls to `POST /api/exchanges/test` and `POST /api/exchanges/keys` for UID-dependent venues fail or connect with missing subaccount scoping.
- **Reproduction Scenario**: Select Gate.io, input API Key, Secret, and User ID (`uid`), click "Test Connection".
- **Evidence**: Model dump of `ExchangeKeysRequest(exchange_id='gateio', api_key='k', secret_key='s', uid='123')` yields `{'exchange_id': 'gateio', 'api_key': 'k', 'secret_key': 's', 'password': None}` with `uid` stripped.
- **Recommended Remediation**: Add `uid: Optional[str] = None` and `label: Optional[str] = None` to `ExchangeKeysRequest` schema.

---

### [P1.2] Missing `Promise.allSettled` and `AbortController` in `ExchangeManager.jsx`
- **Severity**: **P1 (High Severity / Component Crash & State Corruption)**
- **File**: `algo22-terminal/src/pages/ExchangeManager.jsx:66-81`
- **Exact Defect**: `useEffect` mounts with `Promise.all([loadSupportedExchanges(), loadConnectedExchanges()])` without an `AbortController` or `isMounted` guard.
- **Expected Behavior**: Parallel asynchronous fetches execute resiliently via `Promise.allSettled`; if one fails (or user navigates away), ongoing HTTP requests are aborted without throwing unhandled React unmount warnings.
- **Actual Behavior**: If either endpoint fails (e.g., transient network glitch or slow CCXT metadata loading), `Promise.all` fails immediately, leaving the UI in an unrecoverable loading or partial state. Rapid unmounting triggers React memory leak warnings.
- **Recommended Remediation**: Use `Promise.allSettled` and attach `AbortController` to all API calls in `ExchangeManager.jsx`.

---

### [P2.1] Duplicate Route Handler Shadowing on `GET /api/exchanges/schema/{exchange_id}`
- **Severity**: **P2 (Medium Severity / Schema Conflict)**
- **File**: `backend_app/routers/exchange.py:332 & 617`
- **Exact Defect**: Route `/schema/{exchange_id}` is declared twice: first at line 332 (`get_exchange_auth_schema`) and again at line 617 (`get_connection_schema`).
- **Expected Behavior**: Single canonical, certified schema endpoint returning consistent field definitions.
- **Actual Behavior**: The first handler intercepts all requests to `/api/exchanges/schema/{exchange_id}`, returning dynamic CCXT inspection schema rather than the certified connection schema registry (`exchange_connection_schema.py`).
- **Recommended Remediation**: Unify the schema endpoints and remove duplicate route decorators.

---

### [P2.2] Hardcoded `bot_count: 0` in `list_exchanges`
- **Severity**: **P2 (Medium Severity / Telemetry Inaccuracy)**
- **File**: `backend_app/routers/exchange.py:243-244`
- **Exact Defect**: `list_exchanges` returns static `"bot_count": 0, "strategy_count": 0` without querying the `strategies` table.
- **Expected Behavior**: Returns actual count of active strategies and bots deployed on the specific exchange.
- **Actual Behavior**: The "Active Bots" counter on connected exchange cards and top statistics bar always displays `0` even when live bots are running on the venue.
- **Recommended Remediation**: Aggregate deployed bot count from `strategies` table or return dynamic count.

---

### [P3.1] Raw Backend Exception Message Exposure in Frontend Toasts
- **Severity**: **P3 (Low Severity / Information Leakage)**
- **File**: `algo22-terminal/src/pages/ExchangeManager.jsx:114-119, 146-151`
- **Exact Defect**: Toast messages display `err?.response?.data?.detail` directly without sanitizing internal database or CCXT stack details.
- **Expected Behavior**: Friendly, sanitized trader-facing error messages.
- **Actual Behavior**: Raw error strings (such as database connection timeout or CCXT network stack traces) can be rendered in toast banners.
- **Recommended Remediation**: Sanitize error strings to ensure clear, clean trader-facing messages.

---

### [INFO.1] Multi-Fernet Encryption and Zero Secret Exposure Invariant
- **Severity**: **INFO (Architectural Strength)**
- **File**: `backend_app/backend/api_key_vault.py` & `backend_app/routers/exchange.py`
- **Observation**: Keys are encrypted via AES-256 MultiFernet before DB storage. Responses from `GET /api/exchanges` strictly return masked strings (`BIN••••••••••••••••••••••••7890`), preserving tenant privacy and secret protection across all endpoints.

---

## 7. Adversarial Scenario Matrix

| Scenario | System Behavior | Forensic Assessment |
| :--- | :--- | :--- |
| **Missing / Expired JWT** | `Depends(get_current_user)` returns HTTP 401 Unauthorized | **PASS** |
| **Cross-Tenant Key Access** | `exchange_keys` queries enforce `eq("user_id", user["id"])` | **PASS (Strict Tenant Isolation)** |
| **Plaintext Key Exposure in API** | `list_exchanges` returns `masked_key` with SHA-256 masking | **PASS (Zero Secret Leaks)** |
| **Disconnect with Active Bots** | `delete_connection` checks `strategies.status == "deployed"` and rejects with HTTP 400 | **PASS (Safety Lock Active)** |
| **Submitting Malformed JSON / Types** | Handled via FastAPI Pydantic schema validation (HTTP 422) | **PASS** |
| **Submitting Valid Keys on Save** | Fails with HTTP 500 due to `store_exchange_keys` signature mismatch | **FAIL (P0.1 Defect)** |
| **Submitting UID for Gate.io / KuCoin** | `uid` stripped by Pydantic model instantiation | **FAIL (P1.1 Defect)** |
| **Rapid Page Unmount in UI** | Unhandled state updates on unmounted component | **FAIL (P1.2 Defect)** |

---

## 8. Protected Boundary Verification

The frozen production surfaces remain completely untouched:
- `algo22-terminal/src/pages/AdminDashboard.jsx`: **0 diff**
- `algo22-terminal/src/components/admin/AdminDashboard.jsx`: **0 diff**
- `backend_app/routers/admin.py`: **0 diff**
- `backend_app/routers/copilot.py`: **0 diff**

---

## 9. Recommended Phase 6B Remediation Plan

1. **Fix P0.1**: Align `vault.store_exchange_keys` invocation in `backend_app/routers/exchange.py` to match `APIKeyVault.store_exchange_keys` signature (`raw_secret=body.secret_key`).
2. **Fix P1.1**: Update `ExchangeKeysRequest` Pydantic model to include `uid: Optional[str] = None` and `label: Optional[str] = None`, and propagate `uid` into `ConnectionEngine`.
3. **Fix P1.2**: Refactor `ExchangeManager.jsx` data fetching to use `Promise.allSettled`, `AbortController`, and `isMounted` lifecycle guards.
4. **Fix P2.1 & P2.2**: Remove duplicate `/schema/{exchange_id}` route registration and aggregate active bot count from `strategies` table in `list_exchanges`.
5. **Add Comprehensive Unit Tests**: Create `algo22-terminal/tests/unit/exchange_manager_phase6.test.jsx` and `tests/test_exchange_vault_contract.py`.

---

## 10. Final Verdict

**`AUDIT COMPLETE — REMEDIATION REQUIRED`**
