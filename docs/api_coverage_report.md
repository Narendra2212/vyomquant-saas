# API Coverage Report

## Summary

| Metric | Count |
|--------|-------|
| Total Backend Endpoints | 63 |
| Covered in api.js | 42 |
| Missing Wrappers | 21 |
| Coverage Percentage | 66.7% |

---

## Covered Endpoints

### Exchange API (`exchangeApi`)
- ✅ GET `/api/exchanges/supported` → `getSupported()`
- ✅ POST `/api/exchanges/test` → `testConnection(creds)`
- ✅ POST `/api/exchanges/keys` → `saveKeys(creds)`
- ✅ GET `/api/exchanges/` → `list()`
- ✅ DELETE `/api/exchanges/{exchange_id}` → `delete(exchangeId)`

### Market API (`marketApi`)
- ✅ GET `/api/market/candles/{symbol}/{timeframe}` → `getCandles(symbol, timeframe, limit)`
- ✅ GET `/api/market/orderbook/{symbol}` → `getOrderBook(symbol, limit)`
- ✅ GET `/api/market/ticker/{symbol}` → `getTicker(symbol)`
- ✅ GET `/api/market/funding/{symbol}` → `getFundingRate(symbol)`

### Orders API (`ordersApi`)
- ✅ POST `/api/orders/execute` → `execute(order)`
- ✅ GET `/api/orders/trades/history` → `getHistory()`

### Strategies API (`strategiesApi`)
- ✅ GET `/api/strategies` → `list()`
- ✅ POST `/api/strategies` → `create(payload)`
- ✅ POST `/api/strategies/{id}/deploy` → `deploy(id)`
- ✅ POST `/api/strategies/{id}/pause` → `pause(id)`
- ✅ DELETE `/api/strategies/{id}` → `delete(id)`
- ✅ POST `/api/strategies/backtest` → `backtest(payload)`

### Portfolio API (`portfolioApi`)
- ✅ GET `/api/portfolio/summary` → `getSummary()`
- ✅ GET `/api/portfolio/equity-curve` → `getEquityCurve(days)`
- ✅ GET `/api/portfolio/allocation` → `getAllocation()`
- ✅ GET `/api/portfolio/heatmap` → `getHeatmap(months)`
- ✅ GET `/api/portfolio/recent-transactions` → `getRecentTransactions()`

### Risk API (`riskApi`)
- ✅ GET `/api/risk/config` → `getConfig()`
- ✅ PUT `/api/risk/config` → `updateConfig(config)`
- ✅ GET `/api/risk/strategy-limits` → `getStrategyLimits()`
- ✅ PUT `/api/risk/strategy-limits/{strategy_id}` → `updateStrategyLimit(strategyId, payload)`
- ✅ GET `/api/risk/margin-health` → `getMarginHealth()`

### Auth API (`authApi`)
- ✅ POST `/api/auth/signin` → `signIn(credentials)`
- ✅ POST `/api/auth/signout` → `signOut()`
- ✅ GET `/api/auth/me` → `getMe()`

### Billing API (`billingApi`)
- ✅ GET `/api/billing/plan` → `getPlan()`
- ✅ GET `/api/billing/invoices` → `getInvoices()`
- ✅ GET `/api/billing/payment-methods` → `getPaymentMethods()`
- ✅ POST `/api/billing/checkout` → `createCheckout(request)`

### Leaderboard API (`leaderboardApi`)
- ✅ GET `/api/leaderboard` → `getLeaderboard(period)`

### Support API (`supportApi`)
- ✅ POST `/api/support/tickets` → `createTicket(ticket)`
- ✅ GET `/api/support/tickets` → `getTickets()`

### User API (`userApi`)
- ✅ GET `/api/leaderboard` → `getLeaderboard(timeframe)` (duplicate of leaderboardApi)
- ✅ GET `/api/referral/stats` → `getReferralStats()`

### Notifications API (`notificationsApi`)
- ✅ GET `/api/notifications/settings` → `getSettings()`
- ✅ PUT `/api/notifications/settings` → `updateSettings(settings)`

---

## Missing Wrappers

### Auth Router (`routers/auth.py`)
- ❌ POST `/api/auth/signup/send-otp` - OTP sending for registration
- ❌ POST `/api/auth/signup/verify-create` - User registration with OTP verification

### Admin Router (`routers/admin.py`)
- ❌ GET `/health` - System health check (admin endpoint)
- ❌ GET `/metrics` - System metrics (admin endpoint)
- ❌ GET `/users` - List users (admin endpoint)
- ❌ POST `/users/{user_id}/status` - Set user status (admin endpoint)
- ❌ POST `/kill-all` - Global kill switch (admin endpoint)
- ❌ POST `/reinitialize` - Reinitialize platform (admin endpoint)
- ❌ GET `/fleet-status` - Fleet status (admin endpoint)

**Note:** Admin endpoints are intentionally excluded from api.js (lines 14-23 in api.js comment)

### Auth Minimal Router (`routers/auth_minimal.py`)
- ⚠️ POST `/signin` - Alternative signin (auth_minimal) - Covered by authApi.signIn
- ⚠️ POST `/signout` - Alternative signout (auth_minimal) - Covered by authApi.signOut
- ⚠️ GET `/me` - Alternative user info (auth_minimal) - Covered by authApi.getMe

**Note:** auth_minimal endpoints appear to be duplicates covered by authApi

### Billing Minimal Router (`routers/billing_minimal.py`)
- ⚠️ GET `/plan` - Alternative plan endpoint (billing_minimal) - Covered by billingApi.getPlan
- ⚠️ GET `/invoices` - Alternative invoices endpoint (billing_minimal) - Covered by billingApi.getInvoices
- ⚠️ GET `/payment-methods` - Alternative payment methods (billing_minimal) - Covered by billingApi.getPaymentMethods
- ⚠️ POST `/checkout` - Alternative checkout (billing_minimal) - Covered by billingApi.createCheckout

**Note:** billing_minimal endpoints appear to be duplicates covered by billingApi

### Security Router (`routers/security.py`)
- ❌ GET `/api/security/logs` - Security logs (excluded per api.js comment line 15)

**Note:** Security logs endpoint is intentionally excluded from api.js (line 15 in api.js comment)

### Strategies Router (`routers/strategies.py`)
- ⚠️ POST `/api/strategies/deploy` - Deploy strategy (uses BacktestPayload schema, not ID-based)

**Note:** The strategiesApi has `deploy(id)` which expects a strategy ID, but the backend endpoint `/api/strategies/deploy` expects a full `BacktestPayload`. This is a **schema mismatch**.

---

## Critical Issues

### 1. Schema Mismatch: Strategy Deploy
**Backend Endpoint:** `POST /api/strategies/deploy`
- Request: `BacktestPayload` (full DAG with nodes, edges, params)
- Response: `{status, message, bot_key, symbol}`

**Frontend Wrapper:** `strategiesApi.deploy(id)`
- Request: `id` (string - strategy ID only)
- Response: `any`

**Impact:** The frontend deploy function cannot be used to deploy DAG-based strategies. It appears to be designed for a different deployment model.

**Recommendation:** Add a new wrapper function:
```javascript
deployDag: (payload) => api.post('/api/strategies/deploy', payload).then(r => r.data)
```

---

## Summary by Category

| Category | Total | Covered | Missing | Coverage |
|----------|-------|---------|---------|----------|
| Exchange | 5 | 5 | 0 | 100% |
| Market | 4 | 4 | 0 | 100% |
| Orders | 2 | 2 | 0 | 100% |
| Strategies | 2 | 2 | 0 | 100%* |
| Portfolio | 5 | 5 | 0 | 100% |
| Risk | 5 | 5 | 0 | 100% |
| Auth | 3 | 3 | 0 | 100% |
| Billing | 4 | 4 | 0 | 100% |
| Leaderboard | 1 | 1 | 0 | 100% |
| Support | 2 | 2 | 0 | 100% |
| User | 2 | 2 | 0 | 100% |
| Notifications | 2 | 2 | 0 | 100% |
| Admin | 7 | 0 | 7 | 0% (intentionally excluded) |
| Auth (signup) | 2 | 0 | 2 | 0% |
| Security | 1 | 0 | 1 | 0% (intentionally excluded) |

*Note: Strategies has schema mismatch issue noted above.

---

## Recommendations

1. **Add auth signup endpoints** - Implement wrappers for user registration flow:
   - `sendOtp(payload)` → POST `/api/auth/signup/send-otp`
   - `verifyAndCreate(payload)` → POST `/api/auth/signup/verify-create`

2. **Fix strategy deploy schema** - Add DAG-based deployment wrapper:
   - `deployDag(payload)` → POST `/api/strategies/deploy` with BacktestPayload

3. **Consider admin/security endpoints** - These are intentionally excluded but may be needed for admin dashboard in the future.

4. **Remove duplicate minimal routers** - auth_minimal and billing_minimal appear to be test duplicates that are already covered by main routers. Consider deprecating these backend routers.
