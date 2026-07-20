# API CLIENT RUNTIME TRACE

## Overview
Analysis of the `api_client` mapping and exact routing from frontend to backend.

## Call Chain Verification

1. **`endpoints.user.getPerformance(days)`**
   - **File**: `src/api/modules/user.js`
   - **Method**: `get('/api/analytics/performance', { params: { days } })`
   - **Trace**: Fully bound to `backend_app/routers/analytics.py` => `@router.get("/performance")`.

2. **`endpoints.user.getRecentTransactions(limit, days)`**
   - **File**: `src/api/modules/user.js`
   - **Method**: `get('/api/portfolio/recent-transactions', { params: { limit, days } })`
   - **Trace**: Fully bound to `backend_app/routers/portfolio.py` => `@router.get("/recent-transactions")`.

3. **`endpoints.user.getHeatmap(months)`**
   - **File**: `src/api/modules/user.js`
   - **Method**: `get('/api/portfolio/heatmap', { params: { months } })`
   - **Trace**: Fully bound to `backend_app/routers/portfolio.py` => `@router.get("/heatmap")`.

4. **`endpoints.user.getEquityCurve(days)`**
   - **File**: `src/api/modules/user.js`
   - **Method**: `get('/api/portfolio/equity-curve', { params: { days } })`
   - **Trace**: Fully bound to `backend_app/routers/portfolio.py` => `@router.get("/equity-curve")`.

## Verdict
**PASS** - API imports, module exports, and URL endpoints are symmetrically unified across the full stack.
