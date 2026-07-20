# COMPONENT CONTRACT AUDIT

## 1. EquityCurveChart
**Prop Expected:** `data` (Array), `height` (Number), `showDrawdown` (Boolean) - Source: `DashboardUpgrades.jsx:179`
**Prop Supplied:** `N/A`
**Mismatch Detected:** The `PremiumDashboard.jsx` currently uses the standard Recharts `<AreaChart data={equityCurve}>` directly instead of the `<EquityCurveChart>` component. The wiring of the REST API to the chart's backing state (`equityCurve`) succeeds, but the component substitution itself was not mandated in the original 1C.4 directive.

## 2. PerformanceMetrics
**Prop Expected:** `metrics` (Object) - Source: `DashboardUpgrades.jsx`
**Prop Supplied:** `<PerformanceMetrics metrics={performanceMetrics} />`
**Mismatch Detected:** None. Precise matching.

## 3. LivePositions
**Prop Expected:** `positions` (Array) - Source: `DashboardUpgrades.jsx:502`
**Prop Supplied:** `<LivePositions positions={recentTransactions} />`
**Mismatch Detected:** None. The API payload maps the JSON key `transactions` into the `recentTransactions` state, which perfectly fulfills the `positions` Array contract.

## 4. PnlHeatmap
**Prop Expected:** `data` (Array) - Source: `PremiumDashboard.jsx:255`
**Prop Supplied:** `<PnlHeatmap data={heatmapData} />`
**Mismatch Detected:** None.

## Audit Summary
- Prop names correspond 1-to-1 perfectly for the injected components.
- The `EquityCurveChart` was left as the standard inline `<AreaChart>` and needs substitution if the custom enhanced component is strictly required.
