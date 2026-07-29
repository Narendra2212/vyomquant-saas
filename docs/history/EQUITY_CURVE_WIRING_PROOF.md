# EQUITY CURVE WIRING PROOF

## Audit Findings
- The existing implementation `endpoints.user.getEquityCurve?.()` was already feeding the `<AreaChart />` directly if `demoMode` was off. 
- However, if the endpoint failed or returned empty data, it fell back to a `Math.random()` walk generator.

## Modifications Made
- Removed the `Math.random()` fallback for both `demoMode = true` and `demoMode = false` scenarios in `PremiumDashboard.jsx`.
- Modified `loadData` to strictly use the REST payload.

## Code Snippet (After)
```javascript
        // Load equity curve
        try {
          const curve = await endpoints.user.getEquityCurve(90);
          if (curve && curve.length > 0) {
            setEquityCurve(curve.map((d, i) => ({ d: i, v: d.value || d.equity || 0 })));
          } else {
            setEquityCurve([]);
          }
        } catch (e) {
          console.error("Failed to load equity curve", e);
        }
```

## Payload Trace
```json
// Example of actual payload from GET /api/portfolio/equity-curve
[]
// OR
[
  {"timestamp": "2023-10-01T00:00:00Z", "equity": 10500.50},
  {"timestamp": "2023-10-02T00:00:00Z", "equity": 10620.10}
]
```
