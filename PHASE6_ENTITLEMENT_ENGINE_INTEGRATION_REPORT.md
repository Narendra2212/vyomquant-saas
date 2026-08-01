# Phase 6: Entitlement Engine Integration Report
**Feature Gating - Verify Every Page Uses Entitlement Engine**

Generated: 2025-01-08
Project: VyomQuant SaaS
Scope: Integration of centralized entitlement engine

---

## Executive Summary

The centralized entitlement engine has been created (`core/entitlement_engine.py`) with FastAPI dependencies (`core/entitlement_dependencies.py`). However, integration with existing endpoints is minimal. Only 2 endpoints currently use entitlement checks (`check_deployment_limit` and `check_ml_build_limit`), both using the old hardcoded system. A comprehensive migration is required to integrate the new engine across all endpoints.

---

## Current Entitlement Check Usage

### Endpoints Using Old Entitlement System

#### 1. POST /api/strategies/{id}/deploy
**File:** `routers/strategies.py` (line 921)
**Current Check:** `Depends(check_deployment_limit)`
**Purpose:** Prevents over-deployment of bots
**Issues:**
- Uses hardcoded `DEPLOYMENT_LIMITS` dict
- Not integrated with new entitlement engine
- No feature flag check for live trading

**Migration Required:**
```python
# Old
_limit=Depends(check_deployment_limit)

# New
user: dict = Depends(require_feature(FeatureFlag.LIVE_TRADING))
_limit=Depends(require_quota("deployed_bots"))
```

#### 2. POST /api/strategies/train-ml
**File:** `routers/strategies.py` (line 1090)
**Current Check:** `Depends(check_ml_build_limit)`
**Purpose:** Prevents unpaid ML training
**Issues:**
- Uses hardcoded `ML_BUILD_LIMITS` dict
- Not integrated with new entitlement engine
- No feature flag check for ML training

**Migration Required:**
```python
# Old
_ml_check=Depends(check_ml_build_limit)

# New
user: dict = Depends(require_feature(FeatureFlag.ML_TRAINING))
_limit=Depends(require_quota("ml_models"))
```

### Endpoints Using Only Authentication

#### Strategies Router
- `GET /api/strategies` - List strategies (no feature check)
- `POST /api/strategies` - Create strategy (no feature check)
- `GET /api/strategies/{id}` - Get strategy (no feature check)
- `PUT /api/strategies/{id}` - Update strategy (no feature check)
- `DELETE /api/strategies/{id}` - Delete strategy (no feature check)
- `POST /api/strategies/{id}/stop` - Stop bot (no feature check)
- `POST /api/strategies/{id}/pause` - Pause bot (no feature check)
- `POST /api/strategies/{id}/resume` - Resume bot (no feature check)
- `POST /api/strategies/{id}/clone` - Clone strategy (no feature check)
- `POST /api/strategies/validate` - Validate strategy (no feature check)
- `POST /api/strategies/backtest` - Backtest (no feature check)
- `GET /api/strategies/backtest/{job_id}` - Get backtest status (no feature check)
- `POST /api/strategies/optimize` - Optimize strategy (no feature check)
- `POST /api/strategies/monte-carlo` - Monte Carlo simulation (no feature check)
- `POST /api/strategies/walk-forward` - Walk forward optimization (no feature check)

**Missing Entitlement Checks:**
- Backtesting should check `FeatureFlag.BACKTESTING`
- Advanced features (optimize, monte-carlo, walk-forward) should check `FeatureFlag.ADVANCED_ANALYTICS`

#### User Router
- `GET /api/user/profile` - Get profile (no feature check)
- `PUT /api/user/profile` - Update profile (no feature check)
- `GET /api/billing/plan` - Get billing plan (no feature check)
- `GET /api/billing/invoices` - Get invoices (no feature check)
- `GET /api/notifications/settings` - Get notification settings (no feature check)
- `PUT /api/notifications/settings` - Update notification settings (no feature check)
- `GET /api/security/logs` - Get security logs (no feature check)

**Missing Entitlement Checks:**
- None required (user profile is always available)

#### Support Router
- `GET /api/support/tickets` - Get tickets (no feature check)
- `POST /api/support/tickets` - Create ticket (no feature check)
- `GET /api/support/tickets/{id}` - Get ticket (no feature check)
- `POST /api/support/tickets/{id}/comments` - Add comment (no feature check)
- `PUT /api/support/tickets/{id}` - Update ticket (no feature check)

**Missing Entitlement Checks:**
- Priority support should check `FeatureFlag.PRIORITY_SUPPORT`

#### Signals Router
- `GET /api/signals` - List signals (no feature check)
- `GET /api/signals/{id}` - Get signal trace (no feature check)
- `POST /api/signals/{id}/replay` - Replay signal (no feature check)

**Missing Entitlement Checks:**
- None required (signals are core feature)

---

## Integration Plan

### Phase 6.1: Update Existing Entitlement Checks

**Priority:** P0 (Critical)
**Effort:** 2 hours

**Tasks:**
1. Update `POST /api/strategies/{id}/deploy` to use new entitlement engine
2. Update `POST /api/strategies/train-ml` to use new entitlement engine
3. Remove old `check_deployment_limit` and `check_ml_build_limit` dependencies
4. Update `core/dependencies.py` to deprecate old functions

**Implementation:**
```python
# In routers/strategies.py
from backend_app.core.entitlement_dependencies import (
    require_feature,
    require_quota,
    FeatureFlag,
)

@router.post("/{strategy_id}/deploy")
async def deploy_bot(
    strategy_id: str,
    body: Dict[str, Any],
    user: dict = Depends(require_feature(FeatureFlag.LIVE_TRADING)),
    _quota=Depends(require_quota("deployed_bots")),
    fleet=Depends(get_fleet),
    ws_mgr=Depends(get_ws_manager),
):
    # ... existing logic

@router.post("/train-ml")
async def train_ml_strategy(
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_feature(FeatureFlag.ML_TRAINING)),
    _quota=Depends(require_quota("ml_models")),
    vault=Depends(get_vault),
    ws_mgr=Depends(get_ws_manager),
):
    # ... existing logic
```

### Phase 6.2: Add Feature Checks to Advanced Features

**Priority:** P1 (High)
**Effort:** 4 hours

**Tasks:**
1. Add `FeatureFlag.BACKTESTING` check to backtest endpoint
2. Add `FeatureFlag.ADVANCED_ANALYTICS` check to optimize endpoint
3. Add `FeatureFlag.ADVANCED_ANALYTICS` check to monte-carlo endpoint
4. Add `FeatureFlag.ADVANCED_ANALYTICS` check to walk-forward endpoint

**Implementation:**
```python
@router.post("/strategies/backtest")
@limiter.limit("30/minute")
async def backtest(
    request: Request,
    payload: dict,
    user: dict = Depends(require_feature(FeatureFlag.BACKTESTING)),
):
    # ... existing logic

@router.post("/strategies/optimize")
async def optimize_strategy(
    payload: dict,
    user: dict = Depends(require_feature(FeatureFlag.ADVANCED_ANALYTICS)),
):
    # ... existing logic

@router.post("/strategies/monte-carlo")
async def monte_carlo_simulation(
    payload: dict,
    user: dict = Depends(require_feature(FeatureFlag.ADVANCED_ANALYTICS)),
):
    # ... existing logic

@router.post("/strategies/walk-forward")
async def walk_forward_optimization(
    payload: dict,
    user: dict = Depends(require_feature(FeatureFlag.ADVANCED_ANALYTICS)),
):
    # ... existing logic
```

### Phase 6.3: Add Support Tier Checks

**Priority:** P1 (High)
**Effort:** 1 hour

**Tasks:**
1. Add `FeatureFlag.PRIORITY_SUPPORT` check to support ticket creation
2. Add priority support badge to UI

**Implementation:**
```python
@router.post("/support/tickets")
async def create_ticket(
    body: CreateTicketRequest,
    user: dict = Depends(get_current_user),
    priority_check: EntitlementDecision = Depends(
        check_feature_optional(FeatureFlag.PRIORITY_SUPPORT)
    ),
):
    # ... existing logic
    # Use priority_check.is_allowed to determine if user gets priority support
```

### Phase 6.4: Add Entitlements API Endpoint

**Priority:** P1 (High)
**Effort:** 2 hours

**Tasks:**
1. Create `GET /api/user/entitlements` endpoint
2. Return all feature availability and quota status
3. Frontend can use this to conditionally show/hide features

**Implementation:**
```python
# In routers/user.py
from backend_app.core.entitlement_dependencies import get_entitlements

@router.get("/user/entitlements")
async def get_user_entitlements_endpoint(
    entitlements: dict = Depends(get_entitlements)
):
    return entitlements
```

### Phase 6.5: Update Billing Plan Endpoint

**Priority:** P1 (High)
**Effort:** 2 hours

**Tasks:**
1. Update `GET /api/billing/plan` to use new entitlement engine
2. Return feature availability instead of hardcoded plan info
3. Return quota status

**Implementation:**
```python
@router.get("/billing/plan")
async def get_billing_plan(
    user: dict = Depends(get_current_user),
    engine: EntitlementEngine = Depends(get_entitlement_engine),
):
    billing_plan = user.get("app_metadata", {}).get("subscription_tier", "free")
    entitlements = await engine.get_user_entitlements(user["id"], billing_plan)
    return entitlements
```

### Phase 6.6: Frontend Integration

**Priority:** P2 (Medium)
**Effort:** 8 hours

**Tasks:**
1. Create frontend entitlement API client
2. Update Billing.jsx to use entitlements API
3. Update Sidebar.jsx to show correct tier
4. Add feature flags to hide/show UI elements
5. Add upgrade prompts when features unavailable

**Implementation:**
```javascript
// In api/modules/entitlements.js
export async function getEntitlements() {
  return get("/user/entitlements");
}

// In components, use feature flags
{entitlements.features.live_trading.available ? (
  <DeployButton />
) : (
  <UpgradePrompt feature="Live Trading" />
)}
```

---

## Migration Checklist

### Backend
- [ ] Update `POST /api/strategies/{id}/deploy` to use new entitlement engine
- [ ] Update `POST /api/strategies/train-ml` to use new entitlement engine
- [ ] Add feature check to `POST /api/strategies/backtest`
- [ ] Add feature check to `POST /api/strategies/optimize`
- [ ] Add feature check to `POST /api/strategies/monte-carlo`
- [ ] Add feature check to `POST /api/strategies/walk-forward`
- [ ] Add optional feature check to `POST /api/support/tickets`
- [ ] Create `GET /api/user/entitlements` endpoint
- [ ] Update `GET /api/billing/plan` to use entitlement engine
- [ ] Deprecate old `check_deployment_limit` and `check_ml_build_limit`
- [ ] Remove hardcoded `DEPLOYMENT_LIMITS` and `ML_BUILD_LIMITS` dicts
- [ ] Add unit tests for entitlement engine
- [ ] Add integration tests for entitlement dependencies

### Frontend
- [ ] Create entitlement API client
- [ ] Update Billing.jsx to use entitlements API
- [ ] Update Sidebar.jsx to show correct tier
- [ ] Add feature flags to StrategyBuilder
- [ ] Add feature flags to Dashboard
- [ ] Add upgrade prompts to all feature-gated components
- [ ] Add usage display to Profile page
- [ ] Add usage display to Billing page
- [ ] Test all feature-gated components

---

## Testing Plan

### Unit Tests
- Test `PlanMapper.billing_to_tenant()` conversion
- Test `PlanMapper.tenant_to_billing()` conversion
- Test `FeatureEntitlements.is_feature_available()` for all features
- Test `FeatureEntitlements.get_required_plan()` for all features
- Test `EntitlementEngine.check_feature_entitlement()` with all plans
- Test `EntitlementEngine.check_quota_entitlement()` with all resources
- Test `EntitlementEngine.get_user_entitlements()` returns correct data

### Integration Tests
- Test `require_feature` dependency raises HTTPException correctly
- Test `check_feature_optional` dependency returns decision correctly
- Test `require_quota` dependency raises HTTPException correctly
- Test `get_entitlements` dependency returns correct data
- Test entitlement cache invalidation works correctly

### End-to-End Tests
- Test free user cannot deploy live bot
- Test free user cannot train ML model
- Test free user can backtest
- Test pro user can deploy live bot
- Test pro user cannot train ML model
- Test elite user can train ML model
- Test upgrade prompts show correctly
- Test usage display shows correct data

---

## Rollback Plan

If issues arise during migration:

1. **Immediate Rollback:** Revert endpoint changes to use old dependencies
2. **Partial Rollback:** Keep new engine but use old dependencies as fallback
3. **Feature Flag:** Add feature flag to enable/disable new engine
4. **Monitoring:** Add logging to track entitlement check failures

**Rollback Command:**
```bash
# Revert to old dependencies
git revert <commit-hash>
# Or use feature flag
export USE_NEW_ENTITLEMENT_ENGINE=false
```

---

## Next Steps

1. Review and approve this integration plan
2. Begin Phase 6.1 (Update existing entitlement checks)
3. Proceed through phases 6.2-6.6
4. Complete testing
5. Deploy to staging environment
6. Monitor for issues
7. Deploy to production

---

**End of Phase 6 Entitlement Engine Integration Report**
