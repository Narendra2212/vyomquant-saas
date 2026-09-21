# PHASE 12D: LIVE STAGING BASELINE & INVENTORY REPORT
**Platform**: VYOMQUANT — Quantitative Trading Platform  
**Audit Stage**: Phase 12D Live Staging Infrastructure Validation & Final Gate  
**Execution Date**: 2026-08-31  
**Lead Auditor**: Principal Security & Reliability Engineer  
**Authentication Model**: **PASSWORD + EMAIL OTP**  

---

## 1. Commit & Repository Inventory

* **Repository Head Commit**: `b8933d7`
* **Branch**: `main`
* **Tracking Branch**: `origin/main` (Up to date)
* **Working Tree State**:
  - Frontend: `AuthPage.jsx`, `App.jsx`, `supabase.js`, `auth_otp.test.jsx`
  - Backend: `test_evidence_distinctness.py`, `test_phase12a_production_security_validation.py`
  - Regression Baselines: `tests/regression/baseline/`

---

## 2. Environment Variables & Services Audit (Status Only — No Secrets)

| Configuration Variable | Status | Description |
| :--- | :--- | :--- |
| `SUPABASE_URL` | **CONFIGURED** | Supabase project endpoint |
| `DATABASE_URL` | **CONFIGURED** | PostgreSQL relational database endpoint |
| `REDIS_URL` | **CONFIGURED** | Redis cache & distributed lock manager |
| `SUPABASE_ANON_KEY` | **NOT CONFIGURED** | Client-safe public Supabase key |
| `SUPABASE_SERVICE_ROLE_KEY` | **NOT CONFIGURED** | Server-side administrative key |
| `SUPABASE_JWT_SECRET` | **NOT CONFIGURED** | Server-side JWT decoding secret |
| `JWT_SECRET` | **NOT CONFIGURED** | Server-side JWT signature key |
| `CREDENTIAL_VAULT_KEY` | **NOT CONFIGURED** | In-flight exchange secret encryption key |
| `DEFAULT_EXCHANGE` | **NOT CONFIGURED** | Default CCXT exchange connector (defaults to sandbox) |
| `SMTP_PASSWORD` / SES | **NOT CONFIGURED** | Live third-party email OTP delivery provider |
| `BINANCE_TESTNET_KEY` | **NOT CONFIGURED** | Sandbox exchange API credentials |

---

## 3. Security Boundary Guarantees (Frozen Architecture)

```text
1. Password Verified alone != Application Access
2. OTP Verified without Password != Application Access
3. Wrong Password + Valid OTP != Application Access
4. Valid Password + Invalid/Expired OTP != Application Access
5. Valid Password + Valid OTP == Authenticated Application Access
6. Missing / Invalid / Expired JWT == HTTP 401
7. User Metadata Role Elevation == HTTP 403
8. Unauthenticated WebSocket == Code 4001 Closed
9. Cross-Tenant WebSocket Attempt == Code 4001 Closed
10. Cross-Tenant Strategy Subscription == Code CHANNEL_FORBIDDEN
11. Paper Trade == 0 Live CCXT Calls
12. Risk Breach / Kill Switch == 0 Exchange Orders
13. Duplicate Idempotency Key == 1 Logical Order
```
