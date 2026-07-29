# PERFORMANCE METRICS WIRING PROOF

## File Modified
`algo22-terminal/src/pages/PremiumDashboard.jsx`

## Functions Modified
- `loadData()` inside the `useEffect` hook.
- Added `<PerformanceMetrics />` element to the render tree.

## Code Snippet (After)
```javascript
        // Load performance metrics
        try {
          const perfData = await endpoints.user.getPerformance(30);
          setPerformanceMetrics(perfData);
          if (perfData) {
            setStats({
              totalEquity: perfData.total_pnl,
              totalPnl: perfData.total_pnl,
              dailyPnl: perfData.avg_pnl,
              weeklyPnl: perfData.avg_pnl * 7,
              winRate: perfData.win_rate,
              sharpeRatio: perfData.sharpe_ratio,
              activeBots: perfData.total_trades,
              drawdown: 0
            });
          }
        } catch (e) {
          console.error("Failed to load performance metrics", e);
        }
```
And in the render function:
```jsx
        {/* PERFORMANCE METRICS */}
        {performanceMetrics && (
          <PerformanceMetrics metrics={performanceMetrics} />
        )}
```

## Payload Trace
```json
{
  "period_days": 30,
  "total_trades": 12,
  "winning_trades": 8,
  "win_rate": 66.67,
  "total_pnl": 1250.50,
  "avg_pnl": 104.20,
  "sharpe_ratio": 2.1
}
```
