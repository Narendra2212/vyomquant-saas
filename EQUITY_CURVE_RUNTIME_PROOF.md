# EQUITY CURVE RUNTIME PROOF

## Data Flow Trace
1. **API Call**: `endpoints.user.getEquityCurve(90)` fetches data.
2. **State Update**: `setEquityCurve(curve.map(...))` stores the formatted data in `equityCurve` state variable.
3. **Component Injection**: `<AreaChart data={equityCurve}>` receives the data.

## Source Evidence
`algo22-terminal/src/pages/PremiumDashboard.jsx` Lines 54-63:
```javascript
        // Load equity curve
        try {
          const curve = await endpoints.user.getEquityCurve(90);
          if (curve && curve.length > 0) {
            setEquityCurve(curve.map((d, i) => ({ d: i, v: d.value || d.equity || 0 })));
          } else {
            setEquityCurve([]);
          }
        } catch (e) { ... }
```
`PremiumDashboard.jsx` Lines 185-186:
```jsx
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve}>
```

## Verification Result
- State updated: Yes.
- Chart receives data: Yes (`AreaChart` accepts `data={equityCurve}`).
*Note: The chart currently uses `<AreaChart>` directly inline rather than the `<EquityCurveChart>` component from `DashboardUpgrades.jsx`.*
