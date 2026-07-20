# Portfolio API Alignment Refactoring Plan

## Objective

Align the frontend React connection layer with the FastAPI backend schema while maintaining **zero impact** on GUI components. The portfolio module is currently synchronized, but this plan establishes the architectural pattern for future backend schema changes.

---

## Architecture Rules

### Presentation Layer (`App.jsx`, React Components): **NO CHANGES ALLOWED**

- HTML/JSX structure remains immutable
- CSS/Tailwind classes untouched
- React state variable names preserved
- Component props interfaces frozen

### Service Layer (`api.js`): **TARGET FOR UPDATES**

- All data transformation occurs here
- Adapter pattern implementation site
- Error handling escalation point
- Type definition synchronization

### Data Transformation Boundary

```
Backend Response → Service Layer (Transform) → UI State → React Components
     ↑                                                    ↓
     └─────────── Adapter Pattern ──────────────────────┘
```

---

## Current State Assessment

### Backend Schema (`routers/portfolio.py`)

| Endpoint | Method | Response Shape |
|----------|--------|----------------|
| `/api/portfolio/summary` | GET | `{total_value, unrealized_pnl, realized_pnl, roi_percentage}` |
| `/api/portfolio/equity-curve` | GET | `[{date, value}]` |
| `/api/portfolio/allocation` | GET | `[{asset, percentage, value}]` |
| `/api/portfolio/heatmap` | GET | `[{date, pnl}]` |
| `/recent-transactions` | GET | `[]` |

### Frontend State (`api.js` Type Definitions)

| Type | Definition | Status |
|------|------------|--------|
| `PortfolioSummary` | `{total_value, unrealized_pnl, realized_pnl, roi_percentage}` | ✅ ALIGNED |
| `EquityCurvePoint` | `{date, value}` | ✅ ALIGNED |
| `AllocationItem` | `{asset, percentage, value}` | ✅ ALIGNED |
| `HeatmapPoint` | `{date, pnl}` | ✅ ALIGNED |

**Current Status:** ✅ **FULLY SYNCHRONIZED** — No discrepancies detected.

---

## Detailed Implementation Phases

### Phase 1: Frontend TypeScript Definition Verification

**Action:** Audit and verify type definitions in `api.js` match backend Pydantic models.

**Verification Checklist:**
- [ ] `PortfolioSummary` fields match `/summary` response
- [ ] `EquityCurvePoint` fields match `/equity-curve` array items
- [ ] `AllocationItem` fields match `/allocation` array items
- [ ] `HeatmapPoint` fields match `/heatmap` array items

**Status:** ✅ **COMPLETE** — All types verified against `routers/portfolio.py` and `core/models.py`.

---

### Phase 2: Legacy UI TypeScript Definition Preservation

**Action:** Maintain backward-compatible exports for existing UI consumption patterns.

**Current Legacy Exports (api.js lines 790-805):**
```javascript
/** @deprecated Use specific API modules instead */
export const portfolio = portfolioApi;
```

**Preservation Strategy:**
- Legacy `api.get('/api/portfolio/...')` calls continue to work
- Service layer maintains identical response shapes
- UI components require zero modifications

**Status:** ✅ **COMPLETE** — Legacy patterns preserved via deprecated exports.

---

### Phase 3: Adapter Pattern Implementation (Conditional)

**Trigger Condition:** Execute only if backend schema diverges from current state.

**Implementation Template:**

```javascript
// Adapter function for schema migration
function adaptPortfolioSummary(backendResponse) {
  // Map new backend fields to legacy UI expectations
  return {
    total_value: backendResponse.portfolio_equity ?? backendResponse.total_value,
    unrealized_pnl: backendResponse.unrealized_pnl ?? 0,
    realized_pnl: backendResponse.realized_pnl ?? 0,
    roi_percentage: backendResponse.roi_pct ?? backendResponse.roi_percentage ?? 0
  };
}

// Integration in service layer
export const portfolioApi = {
  getSummary: () => 
    api.get('/api/portfolio/summary')
      .then(r => adaptPortfolioSummary(r.data)),
};
```

**Current Status:** ⏸️ **STANDBY** — No adapter needed; schemas are synchronized.

---

### Phase 4: Error Handling Escalation

**Action:** Implement transformation failure detection and graceful degradation.

**Error Handler Pattern:**
```javascript
function withTransformationGuard(apiCall, transformer, fallback) {
  return async (...args) => {
    try {
      const response = await apiCall(...args);
      return transformer(response);
    } catch (err) {
      console.error('[PortfolioAPI] Transformation failed:', err);
      // Return fallback data to prevent UI crash
      return fallback;
    }
  };
}
```

**Integration Points:**
- Service layer wraps all portfolio API calls
- Fallback data matches expected UI shape
- Logs errors for monitoring without breaking UI

**Status:** ✅ **IMPLEMENTED** — Axios interceptors handle auth errors; service layer returns safe fallbacks.

---

## Risk Mitigation

| Risk | Mitigation Strategy |
|------|---------------------|
| UI Breakage | Adapter pattern contains all transformations in service layer |
| Type Mismatches | JSDoc types mirror Pydantic models exactly |
| Silent Failures | Error escalation through axios interceptors + console logging |
| Backend Changes | Adapter functions can be activated without UI modifications |

---

## Verification Steps

1. **Build Verification:**
   ```bash
   cd algo22-terminal && npm run build
   ```
   Expected: ✅ Zero TypeScript/compilation errors

2. **Runtime Verification:**
   - Portfolio dashboard renders without console errors
   - API calls return 200 with expected data shapes
   - Mock data fallbacks display correctly when QuestDB empty

---

## Conclusion

**Current Assessment:** The portfolio API connection layer is **fully synchronized** with the FastAPI backend. No refactoring actions are required at this time.

**This plan serves as:**
1. Architectural documentation for the adapter pattern
2. Activation guide for future schema migrations
3. Verification checklist for ongoing maintenance

**Next Actions:**
- [ ] Archive this plan upon next backend schema change
- [ ] Activate Phase 3 if `portfolio.py` fields diverge from current definitions

---

*Generated: 2026-04-24*  
*Status: SYNCHRONIZED — No changes required*
