# BETA_OPERATIONS_DASHBOARD

These KPIs must be monitored daily by the operations team during the Private Beta phase.

## System Health Metrics
- **API Uptime**: Target `>99.9%`. Measured via Pingdom/UptimeRobot hitting `/api/metrics`.
- **HTTP 500 Rate**: Target `< 0.1%`. Measured via Sentry / Prometheus. Spikes indicate unhandled Python exceptions (often in DAG compilation).
- **HTTP 429 Rate**: Target `< 1%`. If higher, legitimate power-users are hitting `slowapi` rate limits and limits must be raised.
- **Average Response Time**: Target `< 200ms` for CRUD, `< 1500ms` for Backtests.
- **Crash-free Sessions**: Target `> 99%` (Frontend React unhandled exceptions).

## Business Metrics
- **Active Users / Day (DAU)**: Tracks engagement of the initial 100 users.
- **Backtest Success %**: Ratio of `200 OK` vs `400 Bad Request` on the backtest endpoint. Tracks how intuitive the DAG builder is.
- **Paper Trade Success %**: Tracks Sandbox stability.
- **Marketplace Publishes / Day**: Tracks content creation. Target: 2-5 per day during early beta.
- **Clone Success %**: Tracks community engagement (Users pulling others' strategies).
