# FIRST_100_USERS_OPERATIONS_GUIDE

This guide dictates the daily operational tasks required to support the Private Beta cohort (User 1 to 100).

## Daily Routine
### 1. Moderation Queue Review (Morning & Evening)
- Admin logs into the portal.
- Navigates to the Marketplace Moderation tab (`/api/library/admin/pending`).
- Reviews pending strategies for spam, abusive language, or empty DAGs.
- Approves legitimate strategies to seed the public marketplace.

### 2. Error Triage (Continuous)
- Monitor Sentry dashboard for Unhandled Exceptions.
- Target Metric: 0 Backend HTTP 500s.
- Target Metric: 0 Frontend React Crahses.
- Immediately tag backend crashes related to `UnifiedExecutionEngine` as P0.

### 3. Rate Limit Monitoring (Weekly)
- Review `slowapi` 429 logs to determine if `10/minute` limits are excessively penalizing legitimate power users.
- Adjust limits in `main.py` if false positives exceed 2% of users.

### 4. Community Support
- Users may trigger edge-cases in `vectorbt` backtesting (e.g., invalid lookback windows, division by zero in custom indicators).
- Log these exact DAG configurations, reproduce them locally, and add validation rules to `validate_dag` to prevent them from crashing the backend.

## Success Metrics for Graduation to Public Beta
- **Uptime**: 99.9% API uptime over a 14-day window.
- **Safety**: 0 errant live trades executed.
- **Engagement**: > 25% of beta users publish at least one strategy to the library.
- **Performance**: P95 API Latency remains under 250ms (excluding backtest compute).
