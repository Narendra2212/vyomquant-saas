# PHASE 12B — PRODUCTION BASELINE & INVENTORY REPORT
**Platform**: VYOMQUANT — Quantitative Trading Platform  
**Audit Stage**: Phase 12B Production Smoke, Failure & Recovery Gate  
**Execution Date**: 2026-08-31  
**Lead Auditor**: Principal Security & Reliability Engineer  
**Authentication Architecture**: **PASSWORD + EMAIL OTP**  

---

## 1. Commit & Repository Inventory

* **Repository Head Commit**: `b8933d7`
* **Branch**: `main`
* **Tracking Branch**: `origin/main` (Up to date)
* **Working Tree State**:
  - Frontend: `AuthPage.jsx`, `App.jsx`, `supabase.js`, `auth_otp.test.jsx`
  - Backend: `test_phase12a_production_security_validation.py`, `test_evidence_distinctness.py`
  - Baseline Snapshots: `tests/regression/baseline/`

---

## 2. Component & Deployment Architecture

| Component | Target Infrastructure | Deployment Mode | Security Gating |
| :--- | :--- | :--- | :--- |
| **Frontend SPA** | AWS CloudFront + S3 | Static Single-Page App (Vite) | Anon Supabase Key only; zero secrets bundled. |
| **Backend API** | AWS ECS Fargate | FastAPI Async ASGI Container | JWT JWKS verification, RBAC, AAL2, Rate-limiting. |
| **WebSocket Streamer** | AWS ECS Fargate / ALB | ASGI WebSocket Server | Token validation, tenant isolation, ping/pong watchdog. |
| **Auth & AuthZ** | Supabase Auth (Native) | Multi-tenant Identity Provider | Password Auth $\rightarrow$ Email OTP $\rightarrow$ Session JWT. |
| **Database & RLS** | PostgreSQL 15 / Supabase PostgREST | Relational Storage + RLS | Per-request JWT RLS isolation (`auth.uid()`). |
| **Cache & Idempotency** | Redis / AWS ElastiCache | In-Memory Cluster | Atomic Lua check-and-set idempotency locks. |
| **Risk Engine** | In-Process Micro-Engine | In-Memory Python / Decimal | Upstream pre-trade filter (Drawdown, Daily Loss, Kill Switch). |
| **Execution Engine** | Distributed Engine | Async Trade Dispatcher | Strict Paper / Live isolation; CCXT exchange driver. |
| **Exchange Testnet** | Sandbox / Testnet Providers | CCXT Sandboxes (Binance, Kraken) | Mock/Sandbox endpoints; NO live money credentials. |

---

## 3. Environment Variables Audit (Names Only)

### Frontend (Client-Safe Vite Environment)
* `VITE_API_URL`
* `VITE_WS_URL`
* `VITE_SUPABASE_URL`
* `VITE_SUPABASE_ANON_KEY`

### Backend (Server-Side Only)
* `ENV` / `ENVIRONMENT`
* `DEV_MODE`
* `SUPABASE_URL`
* `SUPABASE_ANON_KEY`
* `SUPABASE_SERVICE_ROLE_KEY`
* `SUPABASE_JWT_SECRET`
* `JWT_SECRET`
* `DATABASE_URL`
* `REDIS_URL`
* `REDIS_CLUSTER_NODES`
* `DEFAULT_EXCHANGE`
* `PROFILE_CACHE_TTL`
* `SUPABASE_POOL_MAX_KEEPALIVE`
* `SUPABASE_POOL_MAX_CONNECTIONS`
* `STRIPE_SECRET_KEY`
* `STRIPE_WEBHOOK_SECRET`
* `RAZORPAY_KEY_ID`
* `RAZORPAY_KEY_SECRET`
* `CREDENTIAL_VAULT_KEY`

---

## 4. WebSocket Routing Matrix

* `/ws/telemetry` — Real-time telemetry, strategy monitoring, risk events.
* `/ws/user/{user_id}` — Private user stream (fills, orders, balances) with strict `sub == user_id` cross-check.
* `/ws/dashboard` — Real-time dashboard updates (strategy status, signal trace, notifications).
* `/ws/strategy/{strategy_id}` — Strategy-specific execution and deployment metrics with ownership validation.
* `/ws/signal-trace` — Signal lifecycle tracking.
* `/ws/ticker/{symbol}` — Public ticker stream with active CCXT health checking and stale data threshold.
* `/ws/orderbook/{symbol}` — Public order book depth stream.
* `/ws/candles/{symbol}/{timeframe}` — Public OHLCV candle stream.

---

## 5. Security & Isolation Invariant Assertions

```text
1. Password Verified alone != Application Access
2. OTP Verified without Password != Application Access
3. Wrong Password + Valid OTP != Application Access
4. Valid Password + Invalid/Expired OTP != Application Access
5. Valid Password + Valid OTP == Authenticated Application Access
6. Unauthenticated REST Request == HTTP 401
7. Unauthenticated WebSocket == Code 4001 Closed
8. Cross-Tenant WebSocket Attempt == Code 4001 Closed
9. Cross-Tenant Strategy Subscription == Code CHANNEL_FORBIDDEN
10. Paper Trade == 0 Live CCXT Calls
11. Risk Breach / Kill Switch == 0 Exchange Orders
12. Duplicate Idempotency Key == 1 Logical Order
```
