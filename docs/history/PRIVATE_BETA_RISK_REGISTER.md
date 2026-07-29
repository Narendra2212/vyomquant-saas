# PRIVATE_BETA_RISK_REGISTER

## RISK-01: Lack of API Rate Limiting
- **Classification**: HIGH
- **Impact**: Malicious users could execute DoS attacks by spamming the `/api/library/<id>/clone` or backtest engine endpoints, starving system resources.
- **Likelihood**: Medium
- **Mitigation**: Implement `slowapi` or an API Gateway rate-limiting policy prior to expanding beyond 100 trusted users.

## RISK-02: Missing Centralized Telemetry
- **Classification**: MEDIUM
- **Impact**: Without Sentry or Datadog, diagnosing production crashes requires manually SSH-ing into instances to read `stdout` logs.
- **Likelihood**: High (Bugs will happen in Beta)
- **Mitigation**: Integrate `sentry-sdk` into the FastAPI lifespan events and configure the React frontend with a Sentry DSN before onboarding users.

## RISK-03: Missing Password Reset UI Flow
- **Classification**: MEDIUM
- **Impact**: Users who forget their passwords will be permanently locked out until manual admin intervention.
- **Likelihood**: High
- **Mitigation**: Verify or build the "Forgot Password" UI component that hooks into Supabase's `resetPasswordForEmail` endpoint.

## RISK-04: N+1 Database Queries in Marketplace
- **Classification**: LOW
- **Impact**: Browsing the marketplace will slow down as the database grows, increasing DB CPU utilization.
- **Likelihood**: High
- **Mitigation**: Can be safely ignored for Beta (N < 1000 items). Schedule for resolution in Marketplace V2 (denormalization or Redis cache).

## RISK-05: Missing Strategy Cover Images
- **Classification**: LOW
- **Impact**: Strategies only show generic SVGs or UI placeholders, reducing visual appeal.
- **Likelihood**: High (Guaranteed)
- **Mitigation**: Safe to launch without. Deploy Storage APIs in a fast-follow update post-launch.
