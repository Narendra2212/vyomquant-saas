# API CLIENT WIRING PROOF

## File Modified
`algo22-terminal/src/api/modules/user.js`

## Functions Added
- `getPerformance(days = 30)`
- `getRecentTransactions(limit = 50, days = 30)`
- `getHeatmap(months = 3)`
- `getEquityCurve(days = 90)`

## Code Snippet (After)
```javascript
  /**
   * Get performance metrics
   * @param {number} days
   */
  getPerformance: (days = 30) => get('/api/analytics/performance', { params: { days } }),

  /**
   * Get recent transactions
   * @param {number} limit
   * @param {number} days
   */
  getRecentTransactions: (limit = 50, days = 30) => get('/api/portfolio/recent-transactions', { params: { limit, days } }),

  /**
   * Get heatmap
   * @param {number} months
   */
  getHeatmap: (months = 3) => get('/api/portfolio/heatmap', { params: { months } }),

  /**
   * Get equity curve
   * @param {number} days
   */
  getEquityCurve: (days = 90) => get('/api/portfolio/equity-curve', { params: { days } }),
```

## Verification
Methods are exported as part of `userApi` and exposed via `endpoints.user`. Compile check passed.
