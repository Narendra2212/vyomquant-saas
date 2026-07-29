# EQUITY CURVE RUNTIME VERIFICATION

## Overview
Verification of the Equity Curve analytics component rendering using actual backend data in the Premium Dashboard.

## Verification Targets
- **Backend Endpoint**: `GET /api/portfolio/equity-curve`
- **Frontend File**: `PremiumDashboard.jsx`
- **Component**: `AreaChart` (from `recharts`)
- **API Call Trace**: `endpoints.user.getEquityCurve(90)`

## Findings

### Backend Execution
- The endpoint `GET /api/portfolio/equity-curve` correctly queries the `equity_curve` table in QuestDB using parameterized queries via `telemetry.execute_query`.
- No hardcoded data exists on the backend.

### Frontend Integration
- **API Request Execution**: The request executes within the `useEffect` inside `PremiumDashboard.jsx`.
- **State Hydration**: The response hydrates the `equityCurve` state via `setEquityCurve`.
- **Rendering**: 
  - `AreaChart` is used.
  - Data mapping parses `v` as `equity` or `value` without falling back to random numbers.
- **Mock Overrides**: None. `demoMode` does not override the equity curve fetching logic.

## Verdict
**PASS** - Equity Curve is fully integrated with actual live database queries.
