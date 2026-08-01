# Billing System Discovery Report
**Phase 1: Complete Discovery - Dependency Graph Analysis**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Billing & Subscription System

---

## Executive Summary

The billing system is fragmented across multiple layers with inconsistent plan definitions, hardcoded values, and no centralized entitlement engine. The system uses a hybrid approach with Supabase as the source of truth for subscription state, but retains legacy SQLite models for some operations. Multiple conflicting plan naming conventions exist across the codebase.

---

## 1. Frontend Dependency Graph

### 1.1 Pages
- **`algo22-terminal/src/pages/Billing.jsx`**
  - Main billing page component
  - Hardcoded plans: `free`, `pro`, `enterprise`
  - Features: Current plan display, billing history, payment methods, upgrade modal
  - Currency toggle: USD/INR
  - API calls: `endpoints.billing.getPlan()`, `getInvoices()`, `getPaymentMethods()`, `createCheckout()`

### 1.2 Components
- **`algo22-terminal/src/components/landing/Pricing.jsx`**
  - Landing page pricing display
  - Hardcoded plans: `Free`, `Pro`, `Elite`
  - Monthly/annual pricing toggle
  - No API integration - purely presentational

- **`algo22-terminal/src/components/DashboardUpgrades.jsx`**
  - Dashboard visualization components (charts, metrics)
  - NOT billing-related - monitoring/analytics only

### 1.3 API Client
- **`algo22-terminal/src/api/modules/billing.js`**
  - Frontend API module for billing operations
  - Methods:
    - `getPlan()` - Fetch current billing plan
    - `getInvoices()` - Fetch invoice history
    - `getPaymentMethods()` - Fetch saved payment methods
    - `createCheckout(request)` - Create checkout session
  - Uses `get()` and `post()` helpers from `../../apiClient`

### 1.4 Missing Frontend Components
- No feature flag system
- No entitlement guards
- No usage/quota display components
- No currency detection logic
- No plan comparison features

---

## 2. Backend Dependency Graph

### 2.1 Routers
- **`backend_app/routers/billing.py`** (584 lines)
  - Main billing router
  - Endpoints:
    - `POST /api/billing/checkout` - Create Stripe/Razorpay checkout
    - `POST /api/billing/webhook/stripe` - Stripe webhook handler
    - `POST /api/billing/webhook/razorpay` - Razorpay webhook handler
    - `GET /api/billing/payment-methods` - List saved payment methods
    - `POST /api/billing/payment-methods` - Add payment method
    - `DELETE /api/billing/payment-methods/{method_id}` - Delete payment method
    - `POST /api/billing/portal` - Create Stripe billing portal session
  - Plan keys: `free`, `pro_999`, `elite_1999`, `ml_addon`
  - Pricing (in cents/paise):
    - `pro_999`: INR 99900, USD 1200
    - `elite_1999`: INR 199900, USD 2400
    - `ml_addon`: INR 19900, USD 300
  - Security fixes documented (F-09, F-19, F-20)
  - Referral commission integration present

- **`backend_app/routers/user.py`** (189 lines)
  - Contains billing plan endpoint: `GET /billing/plan`
  - Hardcoded plan mappings (lines 92-121):
    - Maps Supabase `subscription_tier` to frontend plan objects
    - `pro_999` → `pro` (Pro Tier)
    - `elite_1999` → `enterprise` (Enterprise Tier)
    - `free` → `free` (Free Tier)
  - Invoice endpoint: `GET /billing/invoices` (uses SQLite InvoiceModel)
  - Profile update whitelist prevents self-upgrade (USER-1 fix)

### 2.2 Core Dependencies
- **`backend_app/core/dependencies.py`** (535 lines)
  - Hardcoded deployment limits (lines 406-410):
    ```python
    DEPLOYMENT_LIMITS = {
        "free": 1,        # FIX N3: was 0
        "pro_999": 5,
        "elite_1999": float("inf"),
    }
    ```
  - Hardcoded ML build limits (lines 412-416):
    ```python
    ML_BUILD_LIMITS = {
        "free": 0,
        "pro_999": 0,
        "elite_1999": 2,
    }
    ```
  - `check_deployment_limit()` - Enforces bot deployment limits
  - `check_ml_build_limit()` - Enforces ML training limits
  - Profile caching with 60s TTL (PROFILE_CACHE_TTL)
  - `invalidate_profile_cache()` - Called after payment webhooks

- **`backend_app/core/tenant.py`** (276 lines)
  - **CRITICAL INCONSISTENCY**: Uses different plan enum:
    ```python
    class TenantPlan(Enum):
        FREE = "free"
        BASIC = "basic"
        PROFESSIONAL = "professional"
        ENTERPRISE = "enterprise"
    ```
  - Quota definitions per plan (lines 58-96)
  - TenantContext for multi-tenant isolation
  - TenantKeyBuilder for Redis key namespacing

- **`backend_app/core/hard_quota_enforcer.py`** (674 lines)
  - Service-layer quota enforcement
  - Enforces: DAG sessions, nodes, symbols, backtests, orders, positions, capital, storage
  - Uses Redis for tracking
  - Structured error handling via quota_errors.py

- **`backend_app/core/quota_errors.py`** (225 lines)
  - Structured quota violation errors
  - Error types: DAGQuotaExceededError, ExecutionQuotaExceededError, PortfolioQuotaExceededError, etc.

### 2.3 Models
- **`backend_app/core/models/billing.py`** (94 lines)
  - `SubscriptionModel` - SQLAlchemy model (DEPRECATED per comments)
  - `InvoiceModel` - SQLAlchemy model (DEPRECATED per comments)
  - `PaymentMethodModel` - SQLAlchemy model (STILL IN USE for payment methods display)
  - Security note: No hardcoded card metadata defaults (security fix)

### 2.4 Other Routers with Billing Dependencies
- **`backend_app/routers/strategies.py`** - Uses `check_deployment_limit`, `check_ml_build_limit`
- **`backend_app/routers/orders.py`** - Uses quota enforcement
- **`backend_app/routers/risk.py`** - Risk settings (not billing)

---

## 3. Database Dependency Graph

### 3.1 Supabase Tables (Primary)
- **`profiles` table**
  - Columns:
    - `subscription_tier` - Current plan (free, pro_999, elite_1999)
    - `deployed_bots` - Bot deployment counter (can drift)
    - `ml_strategies_built` - ML model counter
    - `ml_addons_purchased` - ML addon counter
    - `is_frozen` - Account freeze flag
    - `available_discounts` - Discount counter
  - Source of truth for billing state (per billing.py comments)

### 3.2 SQLite Tables (Legacy)
- **`subscriptions` table** - DEPRECATED (SubscriptionModel)
- **`invoices` table** - DEPRECATED (InvoiceModel) but still used by user.py endpoint
- **`payment_methods` table** - STILL IN USE (PaymentMethodModel)

### 3.3 Migrations Found
- **`migrations/add_support_tables.sql`** - Support tickets (not billing)
- **`migrations/add_notifications_table.sql`** - Notifications (not billing)
- **`migrations/referral_system_redesign.sql`** - Referral system (has commission integration)
- **Archived migrations** - Library, marketplace, analytics tables (not billing)

### 3.4 Missing Database Components
- No dedicated billing tables (plans, subscriptions, invoices, payments)
- No plan configuration table
- No usage tracking tables
- No audit trail for billing events

---

## 4. AWS/Infrastructure Dependency Graph

### 4.1 Environment Variables Required
- `STRIPE_SECRET_KEY` - Stripe API key
- `STRIPE_WEBHOOK_SECRET` - Stripe webhook verification
- `RAZORPAY_KEY_ID` - Razorpay key
- `RAZORPAY_KEY_SECRET` - Razorpay secret
- `RAZORPAY_WEBHOOK_SECRET` - Razorpay webhook verification
- `SUPABASE_URL` - Supabase connection
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase admin access
- `FRONTEND_URL` - Redirect URLs for checkout
- `DEV_MODE` - Development mode flag
- `PROFILE_CACHE_TTL` - Profile cache TTL (default 60s)

### 4.2 Infrastructure Components
- Redis - Profile caching, quota tracking
- Supabase - User profiles, billing state
- Stripe - Payment processing (USD)
- Razorpay - Payment processing (INR)
- No AWS-specific billing infrastructure found

### 4.3 Documentation Found
- `aws_report.md` - AWS validation results
- `docs/history/AWS_INFRASTRUCTURE_PLAN.md` - Historical AWS planning
- `docs/history/AWS_DEPLOYMENT_GUIDE.md` - Historical AWS deployment guide

---

## 5. Critical Issues Identified

### 5.1 Plan Definition Inconsistency (P0)
**Problem**: Three different plan naming conventions exist:

1. **Frontend (Billing.jsx)**: `free`, `pro`, `enterprise`
2. **Backend Billing (billing.py)**: `free`, `pro_999`, `elite_1999`, `ml_addon`
3. **Backend Tenant (tenant.py)**: `FREE`, `BASIC`, `PROFESSIONAL`, `ENTERPRISE`

**Impact**: Confusion, maintenance burden, potential bugs in plan mapping
**Risk**: High - Users could be assigned wrong plan or features

### 5.2 Hardcoded Values Everywhere (P0)
**Problem**: Plan definitions, pricing, and limits are hardcoded in multiple locations:

- Frontend: `Billing.jsx` lines 17-21, `Pricing.jsx` lines 5-32
- Backend: `billing.py` lines 124-128, `dependencies.py` lines 406-416
- Tenant: `tenant.py` lines 58-96

**Impact**: Cannot update plans without code deployment
**Risk**: High - Business agility blocked, error-prone updates

### 5.3 No Centralized Entitlement Engine (P0)
**Problem**: Feature access is checked via:
- Hardcoded limits in `dependencies.py`
- Direct Supabase queries in routers
- No unified entitlement checking system

**Impact**: Inconsistent enforcement, difficult to audit
**Risk**: High - Security vulnerabilities, quota bypass possible

### 5.4 Database Schema Issues (P1)
**Problem**:
- Billing state mixed with profile data
- No dedicated billing tables
- Legacy SQLite models partially deprecated but still in use
- Invoice data in SQLite instead of Supabase

**Impact**: Data integrity risks, migration complexity
**Risk**: Medium - Data loss, inconsistent state

### 5.5 Missing Currency Localization (P1)
**Problem**:
- No country detection
- No regional pricing beyond USD/INR
- Manual currency toggle in UI
- No automatic currency selection

**Impact**: Poor UX for international users
**Risk**: Low - UX issue, not functional

### 5.6 Frontend Lacks Feature Flags (P1)
**Problem**:
- No feature flag system
- No UI guards based on plan
- Plan features hardcoded in components
- No usage display

**Impact**: Cannot control feature access from backend
**Risk**: Medium - Frontend could show unavailable features

### 5.7 Invoice History Incomplete (P2)
**Problem**:
- Invoice endpoint uses deprecated SQLite InvoiceModel
- No real invoice data from payment providers
- No invoice PDF generation
- No payment history tracking

**Impact**: Users cannot see real payment history
**Risk**: Low - UX issue

### 5.8 Webhook Security (P1)
**Problem**:
- Webhook signature verification present
- No idempotency keys on webhook processing
- No retry logic for failed webhooks
- No webhook event logging

**Impact**: Duplicate processing possible on retries
**Risk**: Medium - Double-charging, duplicate entitlements

---

## 6. Billing Lifecycle Flow

### 6.1 Current Flow
1. User views plans → Frontend displays hardcoded plans
2. User clicks upgrade → `POST /api/billing/checkout`
3. Backend creates Stripe/Razorpay session → Returns checkout URL
4. User completes payment → Payment provider sends webhook
5. Webhook handler → Updates Supabase `subscription_tier`
6. Profile cache invalidated → New limits take effect
7. User can now access paid features → Checked via `check_deployment_limit()`

### 6.2 Issues in Flow
- Plan selection not validated against backend plan definitions
- No confirmation that payment succeeded before granting access
- No rollback if webhook fails
- No audit trail of billing events
- Referral commission processed in same transaction (could fail independently)

---

## 7. Architecture Assessment

### 7.1 Strengths
- Supabase as single source of truth for billing state
- Security fixes documented and applied
- Profile caching for performance
- Hard quota enforcement at service layer
- Webhook signature verification

### 7.2 Weaknesses
- No centralized entitlement engine
- Plan definitions scattered and inconsistent
- Hardcoded values prevent dynamic configuration
- Legacy SQLite models create confusion
- No database-driven plan configuration
- Missing currency localization
- No usage tracking or analytics
- Invoice system incomplete

### 7.3 Technical Debt
- Deprecated models still referenced
- Multiple plan naming conventions
- Hardcoded limits in multiple files
- Mixed database usage (Supabase + SQLite)
- No migration path for billing data

---

## 8. Recommendations for Redesign

### 8.1 Immediate (P0)
1. Standardize plan naming convention across all layers
2. Create centralized entitlement engine
3. Move all plan definitions to database
4. Remove hardcoded limits from code

### 8.2 Short-term (P1)
1. Implement proper invoice tracking in Supabase
2. Add webhook idempotency
3. Create usage tracking tables
4. Implement currency detection
5. Add feature flag system

### 8.3 Long-term (P2)
1. Complete migration from SQLite billing models
2. Implement regional pricing
3. Add billing analytics dashboard
4. Create audit trail for all billing events
5. Implement subscription management (pause, resume, cancel)

---

## 9. Next Steps

**Phase 2: Business Logic Audit**
- Trace complete billing lifecycle
- Identify all entitlement checks
- Map feature access to plans
- Document all billing-related database operations

**Phase 3: Frontend Audit**
- Inspect all billing UI components
- Identify hardcoded plan references
- Find missing feature flags
- Document upgrade flows

**Phase 4: Backend Audit**
- Inspect all billing services
- Find race conditions
- Identify security vulnerabilities
- Review webhook handlers

---

## Appendix A: File Inventory

### Frontend Files
- `algo22-terminal/src/pages/Billing.jsx`
- `algo22-terminal/src/components/landing/Pricing.jsx`
- `algo22-terminal/src/api/modules/billing.js`
- `algo22-terminal/src/App.jsx` (routing)

### Backend Files
- `backend_app/routers/billing.py`
- `backend_app/routers/user.py`
- `backend_app/core/dependencies.py`
- `backend_app/core/tenant.py`
- `backend_app/core/hard_quota_enforcer.py`
- `backend_app/core/quota_errors.py`
- `backend_app/core/models/billing.py`

### Database Files
- `migrations/referral_system_redesign.sql`
- `migrations/add_support_tables.sql`
- `migrations/add_notifications_table.sql`

### Documentation
- `aws_report.md`
- `docs/history/AWS_INFRASTRUCTURE_PLAN.md`
- `docs/history/AWS_DEPLOYMENT_GUIDE.md`

---

**End of Phase 1 Discovery Report**
