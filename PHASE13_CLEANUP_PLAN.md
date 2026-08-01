# Phase 13: Cleanup Plan
**Delete All Unused Billing Code**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Cleanup of deprecated and unused billing code

---

## Executive Summary

Based on the audit findings from Phases 1-12, several billing code components are deprecated, unused, or need to be removed. This phase documents all code that should be cleaned up and provides a safe removal plan.

---

## Code to Remove

### 1. Deprecated SQLite Models

**File:** `backend_app/core/models/billing.py`

**Status:** DEPRECATED per comments (F-20)

**Models to Remove:**
- `SubscriptionModel` - No longer used, Supabase is source of truth
- `InvoiceModel` - No longer used, Supabase is source of truth
- `PaymentMethodModel` - Still used but should be migrated to Stripe/Razorpay

**Action:** 
- Remove `SubscriptionModel` class
- Remove `InvoiceModel` class
- Keep `PaymentMethodModel` temporarily until migration complete

**Migration Required:**
- Migrate any existing data to Supabase
- Update all references to use Supabase
- Remove SQLite tables from database

**Priority:** P1 - High
**Effort:** 4 hours

---

### 2. Hardcoded Plan Definitions

**Files to Update:**
- `backend_app/routers/billing.py` - PRICES dict (lines 124-128)
- `backend_app/core/dependencies.py` - DEPLOYMENT_LIMITS (lines 406-410)
- `backend_app/core/dependencies.py` - ML_BUILD_LIMITS (lines 412-416)
- `backend_app/core/tenant.py` - TenantPlan enum (lines 14-19)
- `backend_app/core/tenant.py` - TenantQuota.for_plan() (lines 58-96)

**Status:** Should be database-driven

**Action:**
- Remove hardcoded PRICES dict
- Remove hardcoded DEPLOYMENT_LIMITS dict
- Remove hardcoded ML_BUILD_LIMITS dict
- Update TenantPlan enum to match new plan structure
- Update TenantQuota to use database

**Migration Required:**
- Create plan service to fetch from database
- Update all references to use plan service
- Add caching for plan data

**Priority:** P1 - High
**Effort:** 8 hours

---

### 3. Deprecated Payment Method Endpoints

**File:** `backend_app/routers/billing.py`

**Endpoints to Deprecate:**
- `GET /api/billing/payment-methods` (lines 455-472)
- `POST /api/billing/payment-methods` (lines 475-531)
- `DELETE /api/billing/payment-methods/{method_id}` (lines 534-549)

**Status:** Should use Stripe/Razorpay customer API

**Action:**
- Add deprecation warnings to endpoints
- Document migration path
- Remove after frontend migration complete

**Migration Required:**
- Create Stripe/Razorpay customer API endpoints
- Update frontend to use new endpoints
- Remove old endpoints

**Priority:** P1 - High
**Effort:** 6 hours

---

### 4. Deprecated Invoice Endpoint

**File:** `backend_app/routers/user.py`

**Endpoint to Deprecate:**
- `GET /api/billing/invoices` (lines 124-136)

**Status:** Should use Supabase invoices table

**Action:**
- Add deprecation warning to endpoint
- Document migration path
- Remove after frontend migration complete

**Migration Required:**
- Create invoices table in Supabase
- Migrate invoice data from payment providers
- Create new invoice endpoint
- Update frontend to use new endpoint

**Priority:** P1 - High
**Effort:** 6 hours

---

### 5. Hardcoded Plan Names in Frontend

**Files to Update:**
- `algo22-terminal/src/pages/Billing.jsx` - Hardcoded plans
- `algo22-terminal/src/pages/Wizard.jsx` - Hardcoded plans
- `algo22-terminal/src/components/landing/Pricing.jsx` - Hardcoded plans
- `algo22-terminal/src/components/Sidebar.jsx` - Hardcoded tier label

**Status:** Should fetch from backend

**Action:**
- Remove hardcoded plan definitions
- Fetch plans from backend API
- Use backend plan service

**Migration Required:**
- Create plan API endpoint
- Update frontend to fetch plans
- Remove hardcoded values

**Priority:** P1 - High
**Effort:** 8 hours

---

### 6. Old Counter Column

**File:** Supabase profiles table

**Column to Remove:**
- `deployed_bots` - Never updated, relies on FleetManager

**Status:** Dead code

**Action:**
- Remove `deployed_bots` column from profiles table
- Update all references to use FleetManager
- Update dependencies.py to use FleetManager

**Migration Required:**
- Update check_deployment_limit to use FleetManager (already done)
- Remove column from database
- Update any remaining references

**Priority:** P0 - Critical
**Effort:** 2 hours

---

### 7. DEV_MODE Bypass Code

**File:** `backend_app/routers/billing.py`

**Lines to Remove:**
- Line 252: DEV_MODE check in Stripe webhook
- Line 407: DEV_MODE check in Razorpay webhook
- Lines 48, 54-58: DEV_MODE checks in key validation

**Status:** Security vulnerability

**Action:**
- Remove all DEV_MODE bypasses
- Remove DEV_MODE checks entirely

**Migration Required:**
- None (direct removal)

**Priority:** P0 - Critical
**Effort:** 1 hour

---

### 8. Unused Import Statements

**Files to Check:**
- All billing-related files

**Action:**
- Remove unused imports
- Clean up import statements

**Priority:** P2 - Low
**Effort:** 2 hours

---

### 9. Dead Code in Hard Quota Enforcer

**File:** `backend_app/core/hard_quota_enforcer.py`

**Status:** Mostly dead code, not integrated with billing

**Action:**
- Review if any methods are used
- Remove unused methods
- Keep if used for service-layer enforcement

**Priority:** P2 - Low
**Effort:** 4 hours

---

## Cleanup Timeline

### Week 1: Critical Cleanup (P0)
1. Remove DEV_MODE bypasses (1 hour)
2. Remove deployed_bots column (2 hours)

### Week 2: High Priority Cleanup (P1)
1. Remove deprecated SQLite models (4 hours)
2. Remove hardcoded plan definitions (8 hours)
3. Deprecate payment method endpoints (6 hours)
4. Deprecate invoice endpoint (6 hours)

### Week 3: Frontend Cleanup (P1)
1. Remove hardcoded plan names in frontend (8 hours)

### Week 4: Low Priority Cleanup (P2)
1. Remove unused imports (2 hours)
2. Review hard quota enforcer (4 hours)

---

## Safe Removal Process

### 1. Code Review

Before removing any code:
1. Search for all references to the code
2. Verify no active usage
3. Check test coverage
4. Document removal reason

### 2. Deprecation First

For endpoints and public APIs:
1. Add deprecation warning
2. Document migration path
3. Wait for frontend migration
4. Remove after grace period

### 3. Database Migration

For database changes:
1. Create migration script
2. Test migration in staging
3. Backup production database
4. Run migration during maintenance window
5. Verify migration success

### 4. Rollback Plan

Have rollback plan ready:
1. Git revert for code changes
2. Database restore for schema changes
3. Feature flags to disable new code

---

## Verification After Cleanup

### 1. Code Verification

- [ ] No references to removed code
- [ ] No import errors
- [ ] No runtime errors
- [ ] All tests pass

### 2. Database Verification

- [ ] No orphaned data
- [ ] No broken foreign keys
- [ ] No missing indexes
- [ ] No schema inconsistencies

### 3. API Verification

- [ ] All endpoints work
- [ ] No 404 errors
- [ ] No 500 errors
- [ ] Response times acceptable

### 4. Frontend Verification

- [ ] All pages load
- [ ] No console errors
- [ ] All features work
- [ ] No broken links

---

## Rollback Plan

If cleanup causes issues:

### Code Rollback
```bash
git revert <commit-hash>
```

### Database Rollback
```bash
pg_restore -d vyomquant backup_before_cleanup.sql
```

### Feature Flags
```bash
export USE_NEW_PLAN_SERVICE=false
export USE_NEW_PAYMENT_METHODS=false
```

---

## Next Steps

1. Review and approve this cleanup plan
2. Begin Week 1 critical cleanup
3. Proceed through cleanup timeline
4. Complete verification
5. Deploy to staging
6. Monitor for issues
7. Deploy to production

---

**End of Phase 13 Cleanup Plan**
