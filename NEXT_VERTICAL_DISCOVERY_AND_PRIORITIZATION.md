# NEXT VERTICAL — READ-ONLY SYSTEM INVENTORY & PRIORITIZATION REPORT

**Classification:** ARCHITECTURAL DISCOVERY & VERTICAL SELECTION
**Date:** 2026-08-30
**Auditor / Principal Platform Architect:** Principal Platform & Security Audit Engineer (Antigravity)
**Current Baseline Status:** Phase 8E Complete (`PHASE_8E_LANDING_PAGE_PRODUCTION_READINESS_RELEASE_GATE.md` — **PASS**)

---

## 1. CURRENT SYSTEM INVENTORY & BASELINE

```text
Git Commit HEAD: f0e4fc6 feat: complete trading-lifecycle-integration spec
Recent Commits:
  - f0e4fc6: feat: complete trading-lifecycle-integration spec
  - af977d2: fix(telemetry): repair QuestDB schema bootstrap that silently created nothing
  - 7d8093c: chore(db): add auditable runner used to apply migration 007 to production
  - 1497d35: fix(db): add missing marketplace columns to library_strategies (PostgreSQL 42703)
  - e1424da: fix(frontend): remove dev-only telemetry shims and test artifact from production HTML
Working Tree: Clean to authorized Phase 8 Landing Page change set (Zero unauthorized changes)
```

---

## 2. COMPLETED PHASES STATUS (PHASE 1 THROUGH PHASE 8)

| Phase Area | Focus Surface | Status | Authoritative Report |
|:---|:---|:---:|:---|
| **Phase 1–4** | Quantitative Trading Dashboard & Metrics | **ACCEPTED / PROMOTED** | `DASHBOARD_FINAL_FORENSIC_ACCEPTANCE.md`, `DASHBOARD_PHASE_4_STAGING_RELEASE_REPORT.md` |
| **Phase 5** | Trader Profile, Preferences & Security | **ACCEPTED / PROMOTED** | `PROFILE_PHASE_5D_PRODUCTION_READINESS_REPORT.md`, `PROFILE_PHASE_5E_TRADER_UX_IMPLEMENTATION_REPORT.md` |
| **Phase 6** | Exchange Manager (CCXT.pro, API Keys, AES-256 Vault) | **ACCEPTED / PROMOTED** | `PHASE_6D_EXCHANGE_PRODUCTION_READINESS_REPORT.md` |
| **Phase 7** | Sign In / Sign Up / Supabase Auth / MFA / RBAC | **ACCEPTED / PROMOTED** | `PHASE_7D_AUTH_PRODUCTION_READINESS_REPORT.md` |
| **Phase 8** | Public Landing Page, SEO, Conversion & Release Gate | **ACCEPTED / RELEASE READY** | `PHASE_8E_LANDING_PAGE_PRODUCTION_READINESS_RELEASE_GATE.md` |

---

## 3. EXISTING AUTHENTICATED APPLICATION SURFACES INVENTORY

The VyomQuant platform comprises the following authenticated desktop/web terminal pages:

| Surface / Route | Primary File | Backend Router | Implementation Maturity |
|:---|:---|:---|:---:|
| **Dashboard** (`/app/dashboard`) | `Dashboard.jsx` (87.8 kB) | `dashboard.py` (8.5 kB) | **Production-Audited** (Phase 1–4) |
| **Profile & Settings** (`/app/profile`) | `Profile.jsx` (47.4 kB) | `user.py` (6.8 kB) | **Production-Audited** (Phase 5) |
| **Exchange Manager** (`/app/exchanges`) | `ExchangeManager.jsx` (39.4 kB) | `exchange.py` (28.0 kB) | **Production-Audited** (Phase 6) |
| **Strategy Builder** (`/app/builder`) | `StrategyBuilder.jsx` (121.4 kB) | `strategies.py` (126 kB), `strategy_operations.py` (277 kB), `dag_tasks.py` (21 kB) | **Heavily Built / Unfinished Audit** |
| **Strategies List** (`/app/strategies`) | `Strategies.jsx` (46.9 kB) | `strategies.py` (126 kB) | **Functional / Unfinished Audit** |
| **Strategy Detail** (`/app/strategies/:id`) | `StrategyDetail.jsx` (46.9 kB) | `strategies.py`, `signal_trace.py` (29.5 kB) | **Functional / Unfinished Audit** |
| **Backtester** (`/app/backtest`) | `Backtester.jsx` (36.7 kB) | `analytics.py` (3.6 kB), `market.py` (12.9 kB) | **Functional / Unfinished Audit** |
| **Paper / Live Trading** (`/app/trades`) | `TradeHistory.jsx` (12.4 kB) | `orders.py` (45.7 kB), `paper_trading.py` (8.6 kB) | **Money-Critical / Safety Freeze** |
| **Risk Controls** (`/app/risk`) | `RiskSettings.jsx` (18.0 kB) | `risk.py` (22.7 kB) | **Money-Critical / Functional** |
| **Marketplace** (`/marketplace`, `/app/marketplace`) | `StrategyMarketplace.jsx` (25.2 kB) | `library.py` (105.8 kB) | **Functional / Spec Completed** |
| **Billing & Plans** (`/app/billing`) | `Billing.jsx` (38.3 kB) | `billing.py` (69.9 kB), `referral.py` (28.7 kB) | **Functional / Partially Audited** |
| **Admin Waitlist** (`/admin/waitlist`) | `AdminDashboard.jsx` (16.9 kB) | `admin.py` (8.0 kB), Direct Supabase RLS | **Gated (Phase 8B)** |
| **AI Copilot** (Floating / Overlay) | `AICopilot.jsx` (6.6 kB) | `copilot.py` (14.6 kB) | **DORMANT / DEFERRED** |

---

## 4. FEATURE MATURITY MATRIX & STATUS

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🟢 AUDITED & RELEASE READY : Dashboard, Profile, Exchanges, Auth, Landing   │
│ 🟡 FUNCTIONAL / PENDING AUDIT : Strategy Builder, Backtester, Marketplace   │
│ 🔴 MONEY-CRITICAL / FROZEN   : Live Order Execution, Safety Circuit Breaker │
│ ⚪ DORMANT / DEFERRED        : AI Copilot                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

| Domain | Status | Codebase Artifacts | Key Capability & Readiness |
|:---|:---:|:---|:---|
| **1. Strategy Builder (DAG)** | 🟡 Advanced | `StrategyBuilder.jsx`, `useBuilderRealtime.js`, `strategies.py`, `dag_tasks.py` | Visual ReactFlow node graph, 15+ indicator nodes, AND/OR logic gates, ML training nodes, JSON serialization, preflight validation. |
| **2. Backtesting Engine** | 🟡 Functional | `Backtester.jsx`, `analytics.py`, `market.py` | VectorBT tick-level backtest engine, parameter sweeps, Sharpe/Sortino/Drawdown calculation, equity curves. |
| **3. Trading Lifecycle & Execution** | 🔴 Money-Critical | `orders.py`, `paper_trading.py`, `distributed_execution.py`, `health_websocket.py` | Paper trading simulator active; live execution under safety freeze; preflight order verification, WebSocket telemetry. |
| **4. Risk Management** | 🔴 Money-Critical | `risk.py`, `RiskSettings.jsx` | Max drawdown killswitch, position size ceiling, leverage limit, anomaly halt triggers. |
| **5. Marketplace & Library** | 🟡 Functional | `StrategyMarketplace.jsx`, `library.py`, `.kiro/specs/marketplace-subscriptions...` | Strategy discovery, cloning, template publication, creator attribution, rating/reviews. |
| **6. Billing & Entitlements** | 🟡 Functional | `billing.py`, `Billing.jsx`, `referral.py` | Stripe webhook processing, tier limits (bot count, backtests), affiliate referrals. |
| **7. Desktop Packaging** | 🟡 Built | `src-tauri/`, `DownloadPage.jsx` | Tauri 2.0 native packaging for Windows (.exe), macOS (.dmg), Linux (.AppImage). |
| **8. AI Copilot** | ⚪ Dormant | `AICopilot.jsx`, `copilot.py` | UI disabled; prompt injection and token usage deferred per `COPILOT_UI_DEFERRED_REPORT.md`. |

---

## 5. MONEY-CRITICAL & SECURITY-SENSITIVE SURFACE MAP

```
                        MONEY-CRITICAL SURFACES
┌─────────────────────────────────────────────────────────────────────────────┐
│  [Exchange API Keys / AES-256 Vault]  ──>  PHASE 6 AUDITED & SECURE ✅      │
│  [Authentication / MFA / Sessions]    ──>  PHASE 7 AUDITED & SECURE ✅      │
│  [Live Order Routing / Preflight]     ──>  FROZEN (Requires Phase 9/10 Gate)│
│  [Account Risk Circuit Breakers]      ──>  FROZEN (Requires Forensic Audit) │
│  [Stripe Billing & Paid Entitlements] ──>  Requires Verification Gate       │
│  [Strategy DAG Compilation Engine]    ──>  EXECUTION LOGIC FOUNDATION       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Critical Boundaries:
1. **Live Execution Safety Freeze:** Live order placement must remain disabled until Strategy Builder validation and Risk Guard enforcement are forensically audited and proven tamper-proof.
2. **Strategy Code / DAG Compilation Integrity:** If the visual DAG compiles to invalid parameters or reverses buy/sell signals, severe financial loss will occur. The node compiler is a **Tier-1 critical asset**.
3. **Billing Entitlements Engine:** Subscription limits (e.g. Free = 1 bot, Trader = 5 bots, Pro = 15 bots) must be strictly enforced server-side before execution is scheduled.

---

## 6. TEST COVERAGE ASSESSMENT & TECHNICAL DEBT

### Test Coverage Baseline:
- **Backend Python Suite (`pytest`):**
  - `tests/test_phase7b_auth_remediation.py` (16/16 PASS)
  - `tests/test_admin_auth.py` (PASS)
  - `tests/test_role_granularity_and_audit.py` (PASS)
  - `tests/test_mfa_security_lifecycle.py` (PASS)
  - Full Auth Suite: **33/33 PASS**
- **Frontend Vitest Suite (`npx vitest run`):**
  - **44 test files passed / 1 failed (1035 tests passed / 1 test failed)**
  - Single failure: `tests/unit/portfolio-rendering.test.jsx:147` (pre-existing minor string expectation mismatch in Portfolio empty state).
  - Realtime DAG tests: `builderRealtime.test.jsx` (69/69 PASS), `deployPreflight.test.jsx` (40/40 PASS), `strategyBuilder.trace.test.jsx` (13/13 PASS).

### Technical Debt / Operational Hygiene:
1. **Pydantic V2 / SQLAlchemy 2.0 Warnings:** 34 deprecation warnings on backend model validators (`@validator` -> `@field_validator`, `declarative_base()` import).
2. **Bundle Chunk Size:** Minified chunks `vendor-recharts` (554 kB) and `index` (423 kB) can benefit from code-splitting in a future performance optimization pass.
3. **Orphaned `pages/Landing.jsx`:** Marked deprecated in Phase 8B; pending clean deletion after final release promotion.

---

## 7. TOP CANDIDATE NEXT VERTICALS (RANKED ANALYSIS)

### Candidate 1: **Strategy Builder (Visual DAG Construction, Node Engine & Rule Validation)**
- **User / Business Value:** **MAXIMUM (10/10)** — This is the core differentiator and central value proposition of VyomQuant ("Build quantitative trading strategies without writing code").
- **Revenue Impact:** **CRITICAL (9/10)** — Users convert to paid tiers specifically to build, export, and run more DAG strategy systems.
- **Trading / Money Risk:** **HIGH (8/10)** — Invalid DAG serialization or malformed logic nodes will cause corrupt execution signals downstream.
- **Implementation Readiness:** **VERY HIGH (9/10)** — Full ReactFlow UI (`StrategyBuilder.jsx` 121 kB), backend compilation router (`strategies.py` 126 kB), and comprehensive spec (`.kiro/specs/strategy-builder`) are already written and testable.
- **Security Sensitivity:** **MEDIUM-HIGH (7/10)** — Requires strict tenant isolation on strategy storage and sanitization of indicator parameters.

### Candidate 2: **Backtesting Engine & Quantitative Analytics (VectorBT Integration)**
- **User / Business Value:** **HIGH (8.5/10)** — Essential research step; traders will not deploy strategies without backtesting them first.
- **Revenue Impact:** **HIGH (8/10)** — Drives pro subscriptions for tick data access and compute slots.
- **Trading / Money Risk:** **LOW-MEDIUM (4/10)** — Historical simulation only; zero direct capital risk.
- **Implementation Readiness:** **MEDIUM-HIGH (7.5/10)** — `Backtester.jsx` and backend VectorBT hooks exist. Dependent on Strategy Builder output format.

### Candidate 3: **Trading Lifecycle & Paper Trading Execution Engine**
- **User / Business Value:** **VERY HIGH (9/10)** — Forward-testing strategies against live market feeds.
- **Revenue Impact:** **HIGH (8.5/10)** — Required for daily platform engagement.
- **Trading / Money Risk:** **EXTREME (10/10)** — Direct interaction with order routers and simulated/live balances.
- **Implementation Readiness:** **MEDIUM (6/10)** — Spec `trading-lifecycle-integration` committed; requires Strategy Builder output as its input.

### Candidate 4: **Billing, Subscriptions & Paid Entitlements Gate**
- **User / Business Value:** **MEDIUM (6/10)** — Internal monetization engine.
- **Revenue Impact:** **MAXIMUM (10/10)** — Direct payment collection.
- **Trading / Money Risk:** **HIGH (8/10)** — Financial entitlements and payment webhooks.
- **Implementation Readiness:** **HIGH (8/10)** — Previously audited across early billing reports.

---

## 8. FORMAL RECOMMENDATION: THE NEXT VERTICAL

### **Recommended Next Vertical:**
## 🎯 **VERTICAL 9: STRATEGY BUILDER — VISUAL DAG ENGINE, NODE VALIDATION & COMPILATION INTEGRITY**

---

### Why It Must Come Next (Logical Product Sequence):

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Visitor Lands & Signs Up            ──> PHASE 8 (COMPLETE ✅)            │
│ 2. Authenticates & Configures Exchange ──> PHASE 6 & 7 (COMPLETE ✅)        │
│ 3. 🎯 BUILDS FIRST SYSTEMATIC DAG      ──> NEXT: PHASE 9 STRATEGY BUILDER   │
│ 4. Backtests Strategy with VectorBT    ──> PHASE 10 BACKTESTING             │
│ 5. Deploys to Paper/Live Trading       ──> PHASE 11 TRADING LIFECYCLE       │
│ 6. Upgrades Tier / Marketplace Share   ──> PHASE 12 BILLING & MARKETPLACE   │
└─────────────────────────────────────────────────────────────────────────────┘
```

1. **The Core Product Foundation:** The entire SaaS value proposition rests upon the Strategy Builder. A trader cannot backtest, paper trade, or deploy a bot until they can construct a valid, verified strategy.
2. **Eliminates Downstream Bottlenecks:** Both the Backtester (Phase 10) and Trading Lifecycle (Phase 11) depend on the exact JSON schema and execution contract produced by the Strategy Builder DAG. Auditing Strategy Builder first guarantees stable contracts for execution.
3. **Substantial Existing Codebase:** `StrategyBuilder.jsx` (121 kB) and `strategies.py` (126 kB) represent the largest un-audited surface in the repository. It is primed for a rigorous forensic audit.

---

## 9. PROPOSED PHASE SEQUENCE FOR STRATEGY BUILDER VERTICAL

Following the proven VYOMQUANT forensic audit methodology:

- **Phase 9A — Strategy Builder Forensic Audit (READ-ONLY):**
  - Audit ReactFlow node canvas, 15+ indicator nodes, connection rules, parameter validation.
  - Audit backend serialization, schema validation, node compilation, and tenant isolation in `strategies.py` & `dag_tasks.py`.
  - Classify all defects (P0/P1/P2/P3/INFO).
- **Phase 9B — Strategy Builder Surgical Remediation:**
  - Fix high-priority node validation errors, compilation edge cases, and tenant boundary leaks.
- **Phase 9C — Strategy Builder Adversarial Acceptance Audit (READ-ONLY):**
  - Inject circular dependency loops, malformed parameters, invalid types, cross-tenant strategy access attempts.
- **Phase 9D — Strategy Builder UX Polish & Trader Experience:**
  - Enhance node palette, canvas performance, error indicators, parameter sliders, and undo/redo states.
- **Phase 9E — Strategy Builder Production Readiness & Release Gate:**
  - Final regression test pass, schema validation, and release sign-off.

---

## 10. PROTECTED BOUNDARIES FOR NEXT VERTICAL

During discovery and until Phase 9A is authorized:
- **DO NOT MODIFY:**
  - JWT verification & Supabase Auth (`backend_app/routers/auth.py`, `core/auth_middleware.py`)
  - Exchange Manager credentials & AES-256 vault (`backend_app/routers/exchange.py`)
  - Live execution controls and safety circuit breakers (`backend_app/routers/orders.py`, `risk.py`)
  - Billing authorization (`backend_app/routers/billing.py`)
  - Landing page assets (`algo22-terminal/src/components/landing/*`)

---

## 11. DELIVERABLE LOCATION

The authoritative discovery report is saved to:
[`NEXT_VERTICAL_DISCOVERY_AND_PRIORITIZATION.md`](file:///C:/aerora_quant_backend_updated_final1/NEXT_VERTICAL_DISCOVERY_AND_PRIORITIZATION.md)

---

**CRITICAL STOP CONDITION MET:** System discovery and prioritization are complete. Zero source code was modified. Awaiting your explicit authorization to begin **Phase 9A — Strategy Builder Forensic Audit**.
