# SMOKE_TEST_PROCEDURE

Perform these steps chronologically in the production environment.

## 1. Registration
- **Action**: Sign up with `test_smoke@algo22.io`.
- **Expected Result**: Success, token saved to `sessionStorage`, routed to Dashboard.
- **Rollback Action if Failed**: Delay launch. Verify Supabase Auth configuration.

## 2. Login
- **Action**: Log out, then log back in.
- **Expected Result**: Dashboard loads successfully with `GET /api/me` returning 200.
- **Rollback Action if Failed**: Delay launch. Verify JWT signing secrets match.

## 3. Password Reset
- **Action**: Click "Forgot Password", enter email, click link in email.
- **Expected Result**: Renders `UpdatePasswordPage`, successfully updates, redirects.
- **Rollback Action if Failed**: Note as P1, can launch with manual reset support via Admin if necessary, but ideally fix.

## 4. Strategy Creation
- **Action**: Build a simple RSI DAG in the Builder. Click Save.
- **Expected Result**: `POST /api/strategies` returns 200, Strategy appears in Dashboard.
- **Rollback Action if Failed**: Delay launch. Check RLS policies on `strategies` table.

## 5. Backtest
- **Action**: Click "Run Backtest" on the saved strategy.
- **Expected Result**: `POST /api/strategies/backtest` returns metrics, equity curve renders.
- **Rollback Action if Failed**: Delay launch. Check VectorBT dependencies/Numba caching in Docker.

## 6. Paper Trading
- **Action**: Deploy strategy to Sandbox.
- **Expected Result**: Order is routed, PnL updates on Dashboard, no live Exchange API key is required.
- **Rollback Action if Failed**: Delay launch. Check `UnifiedExecutionEngine` state.

## 7. Marketplace Publish
- **Action**: Click "Publish to Library", fill details.
- **Expected Result**: `POST /api/library` returns 201. Status is `pending`.
- **Rollback Action if Failed**: Delay launch. Check `library_strategies` constraints.

## 8. Admin Moderation
- **Action**: Login as Admin. Approve the strategy.
- **Expected Result**: Strategy appears in public Browse `/api/library`.
- **Rollback Action if Failed**: Note as P1. Fix admin JWT claims.

## 9. Marketplace Clone
- **Action**: Login as a *different* user. Clone the strategy.
- **Expected Result**: Strategy is duplicated to the new user's builder with `source_library_id` set.
- **Rollback Action if Failed**: Delay launch. Verify clone RLS and transaction safety.

## 10. Ratings
- **Action**: Submit a 5-star rating on the cloned strategy.
- **Expected Result**: Rating accepted, `avg_rating` recalculates to 5.0.
- **Rollback Action if Failed**: Note as P2. Ratings can be temporarily disabled.

## 11. Kill Switch
- **Action**: In the backend shell, force `ExecutionFlags.LIVE_TRADING_ENABLED = False`. Submit a Sandbox Order.
- **Expected Result**: HTTP 403 / 400 SafetyViolationError. Order blocked.
- **Rollback Action if Failed**: **ABSOLUTE BLOCKER**. DO NOT LAUNCH.

## 12. Health Checks
- **Action**: Hit `/api/metrics` unauthenticated.
- **Expected Result**: Returns Prometheus formatted metrics.
- **Rollback Action if Failed**: Note as P2. Non-critical for users, critical for Ops.
