# API CONSUMPTION TRACE

## Search Scope
- `algo22-terminal/src/api/**/*.js`
- `algo22-terminal/src/hooks/**/*.js`
- `algo22-terminal/src/pages/**/*.jsx`
- `algo22-terminal/src/components/**/*.jsx`

## Endpoints Trace

### 1. `GET /api/portfolio/equity-curve`
- **Status**: PARTIALLY USED
- **Calling File**: `algo22-terminal/src/App.jsx`
- **Calling Function**: `loadStats` (Line 2998)
- **Calling File**: `algo22-terminal/src/pages/PremiumDashboard.jsx` (implicitly via `endpoints.user.getEquityCurve()`)
- **Note**: Replaced with random data generator when the request fails or demo mode is active.

### 2. `GET /api/portfolio/recent-transactions`
- **Status**: UNUSED
- **Trace**: No matches found in the entire `src/` directory. `LivePositions` component accepts a prop but it's not hooked up to this endpoint.

### 3. `GET /api/analytics/performance`
- **Status**: UNUSED
- **Trace**: No matches found in the codebase. The `PerformanceMetrics` component in `DashboardUpgrades.jsx` requires this data structure, but it's completely un-wired.

### 4. `GET /api/portfolio/heatmap`
- **Status**: UNUSED
- **Trace**: No matches found in the codebase. `PnlHeatmap` in `PremiumDashboard.jsx` generates a random 28-day array instead of making a fetch call.

### 5. `/api/bots/*`
- **Status**: UNUSED
- **Trace**: The frontend does not use any `/api/bots` REST endpoints. It relies on `/api/strategies/*` instead for strategy manipulation.

### 6. `/ws/bots` and `/ws/signals`
- **Status**: UNUSED
- **Trace**: No connection logic references these dedicated endpoint paths. The `websocketClient.js` hardcodes connections to `/ws/telemetry`.
