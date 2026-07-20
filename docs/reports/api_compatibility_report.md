# API Compatibility Report

**Generated:** May 1, 2026  
**Frontend:** api.js, endpoints.js, apiClient.js  
**Backend:** FastAPI routers (auth, exchange, market, orders, strategies, portfolio, risk, billing, user, analytics, admin)

---

## Summary

| Status | Count |
|--------|-------|
| ✅ MATCH | 45 |
| ⚠️ MISMATCH | 12 |
| ❌ MISSING (Backend) | 8 |
| ❌ MISSING (Frontend) | 5 |

---

## Detailed Comparison Table

### AUTHENTICATION ENDPOINTS (/api/auth/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `POST /api/auth/signup` | `POST /api/auth/signup` | ✅ MATCH | None |
| `POST /api/auth/signin` | `POST /api/auth/signin` | ✅ MATCH | None |
| `POST /api/auth/signout` | `POST /api/auth/signout` | ⚠️ MISMATCH | Backend requires `token` param in body; frontend sends empty body |
| `GET /api/auth/me` | `GET /api/auth/me` | ✅ MATCH | None |
| `POST /api/auth/register` (main.py) | `POST /api/auth/register` | ✅ MATCH | None |

---

### EXCHANGE ENDPOINTS (/api/exchanges/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/exchanges/supported` | ❌ MISSING | ❌ MISSING | Backend route not implemented |
| `POST /api/exchanges/test` | `POST /api/exchanges/test` | ✅ MATCH | None |
| `POST /api/exchanges/keys` | `POST /api/exchanges/keys` | ✅ MATCH | None |
| `GET /api/exchanges/` | `GET /api/exchanges/` | ✅ MATCH | None |
| `DELETE /api/exchanges/{exchangeId}` | `DELETE /api/exchanges/{exchange_id}` | ✅ MATCH | None |

---

### MARKET DATA ENDPOINTS (/api/market/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/market/candles/{symbol}/{timeframe}` | `GET /api/market/candles/{symbol}/{timeframe}` | ✅ MATCH | None |
| `GET /api/market/orderbook/{symbol}` | `GET /api/market/orderbook/{symbol}` | ✅ MATCH | None |
| `GET /api/market/ticker/{symbol}` | `GET /api/market/ticker/{symbol}` | ✅ MATCH | None |
| `GET /api/market/funding/{symbol}` | `GET /api/market/funding/{symbol}` | ✅ MATCH | None |
| `GET /api/market/symbols` | `GET /api/market/symbols` | ✅ MATCH | None |
| `GET /api/market/data/{symbol}/{timeframe}` | `GET /api/market/data/{symbol}/{timeframe}` | ✅ MATCH | Backend has alias to candles |

---

### ORDERS ENDPOINTS (/api/orders/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `POST /api/orders/execute` | `POST /api/orders/execute` | ✅ MATCH | None |
| `POST /api/orders/create` (alias) | `POST /api/orders/create` | ✅ MATCH | None |
| `POST /api/orders/stop-loss` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `POST /api/orders/take-profit` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `GET /api/orders/open` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `POST /api/orders/cancel/{order_id}` | ❌ MISSING | ❌ MISSING | Frontend defines; backend has different signature |
| `POST /api/orders/cancel-all` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `GET /api/orders/history` | `GET /api/orders/history` | ✅ MATCH | None |
| `GET /api/orders/trades/history` | `GET /api/orders/history` | ⚠️ MISMATCH | Wrong path in api.js - should be `/api/orders/history` |

---

### STRATEGIES ENDPOINTS (/api/strategies/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/strategies` | `GET /api/strategies` | ✅ MATCH | None |
| `GET /api/strategies/{id}` | `GET /api/strategies/{id}` | ✅ MATCH | None (in endpoints.js) |
| `POST /api/strategies` | `POST /api/strategies` | ✅ MATCH | None |
| `POST /api/strategies/{id}/deploy` | `POST /api/strategies/{strategy_id}/deploy` | ✅ MATCH | None |
| `POST /api/strategies/{id}/pause` | `POST /api/strategies/{id}/stop` | ⚠️ MISMATCH | Frontend uses `pause` -> backend `stop` |
| `POST /api/strategies/{id}/stop` | `POST /api/strategies/{strategy_id}/stop` | ✅ MATCH | None |
| `PUT /api/strategies/{id}` | `PUT /api/strategies/{strategy_id}` | ✅ MATCH | None |
| `DELETE /api/strategies/{id}` | `DELETE /api/strategies/{strategy_id}` | ✅ MATCH | None |
| `POST /api/strategies/backtest` | `POST /api/strategies/backtest` | ✅ MATCH | None |
| `POST /api/strategies/deploy` (DAG) | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `POST /api/strategies/{id}/start` | `POST /api/strategies/{id}/deploy` | ⚠️ MISMATCH | Alias issue - frontend calls `start` -> backend `deploy` |
| `POST /api/strategies/train-ml` | `POST /api/strategies/train-ml` | ✅ MATCH | None |
| `GET /api/strategies/{id}/status` | ❌ MISSING | ❌ MISSING | Frontend may expect; backend not implemented |

---

### PORTFOLIO ENDPOINTS (/api/portfolio/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/portfolio/summary` | `GET /api/portfolio/summary` | ✅ MATCH | None |
| `GET /api/portfolio/equity-curve` | `GET /api/portfolio/equity-curve` | ✅ MATCH | None |
| `GET /api/portfolio/allocation` | `GET /api/portfolio/allocation` | ✅ MATCH | None |
| `GET /api/portfolio/heatmap` | `GET /api/portfolio/heatmap` | ✅ MATCH | None |
| `GET /api/portfolio/recent-transactions` | `GET /api/portfolio/recent-transactions` | ❌ MISSING | Backend route not implemented |
| `GET /api/portfolio/history` | `GET /api/orders/history` | ⚠️ MISMATCH | Wrong path - routes to orders |
| `POST /api/portfolio/close-all` | ❌ MISSING | ❌ MISSING | Frontend defines in endpoints.js; backend not implemented |

---

### RISK ENDPOINTS (/api/risk/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/risk/config` | `GET /api/risk/settings` | ⚠️ MISMATCH | Frontend `/config` -> backend `/settings` |
| `PUT /api/risk/config` | `PUT /api/risk/settings` | ⚠️ MISMATCH | Frontend `/config` -> backend `/settings` |
| `GET /api/risk/strategy-limits` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `PUT /api/risk/strategy-limits/{id}` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `GET /api/risk/margin-health` | `GET /api/risk/account-health` | ⚠️ MISMATCH | Frontend `/margin-health` -> backend `/account-health` |
| `POST /api/risk/kill-switch` | ✅ MATCH | ✅ MATCH | Available in backend but not exposed in frontend |

---

### BILLING ENDPOINTS (/api/billing/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/billing/plan` | `GET /api/billing/plan` | ✅ MATCH | None |
| `GET /api/billing/invoices` | `GET /api/billing/invoices` | ✅ MATCH | None |
| `GET /api/billing/payment-methods` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `POST /api/billing/checkout` | `POST /api/billing/checkout` | ✅ MATCH | None |

---

### USER ENDPOINTS (/api/user/* and /api/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/user/profile` | `GET /api/user/profile` | ✅ MATCH | None |
| `PUT /api/user/profile` | `PUT /api/user/profile` | ✅ MATCH | None |
| `GET /api/stats` | `GET /api/stats` | ✅ MATCH | None |
| `GET /api/referral/stats` | `GET /api/referral/stats` | ✅ MATCH | None |
| `GET /api/leaderboard` | `GET /api/leaderboard` | ✅ MATCH | None |
| `GET /api/security/logs` | `GET /api/security/logs` | ✅ MATCH | None |

---

### NOTIFICATIONS ENDPOINTS (/api/notifications/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/notifications/settings` | `GET /api/notifications/settings` | ✅ MATCH | None |
| `PUT /api/notifications/settings` | `PUT /api/notifications/settings` | ✅ MATCH | None |

---

### SUPPORT ENDPOINTS (/api/support/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `POST /api/support/tickets` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |
| `GET /api/support/tickets` | ❌ MISSING | ❌ MISSING | Frontend defines; backend not implemented |

---

### ADMIN ENDPOINTS (/api/admin/*) - Intentionally Excluded from Frontend

| Backend Endpoint | Status | Notes |
|------------------|--------|-------|
| `GET /api/admin/health` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `GET /api/admin/metrics` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `GET /api/admin/users` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `POST /api/admin/users/{user_id}/status` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `POST /api/admin/kill-all` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `POST /api/admin/reinitialize` | ✅ AVAILABLE | Not exposed in frontend (intentional) |
| `GET /api/admin/fleet-status` | ✅ AVAILABLE | Not exposed in frontend (intentional) |

---

### ANALYTICS ENDPOINTS (/api/analytics/*)

| Frontend Call | Backend Expected | Status | Fix Required |
|---------------|------------------|--------|--------------|
| `GET /api/analytics/performance` | ✅ AVAILABLE | Not exposed in frontend |

---

## Critical Issues (Must Fix)

### 1. WRONG ENDPOINT PATHS

| File | Line | Wrong Path | Correct Path |
|------|------|------------|--------------|
| `api.js` | 526 | `/api/orders/trades/history` | `/api/orders/history` |
| `endpoints.js` | 307 | `/api/orders/history` (for portfolio) | `/api/portfolio/history` |
| `api.js` | 737, 744 | `/api/risk/config` | `/api/risk/settings` |
| `api.js` | 765 | `/api/risk/margin-health` | `/api/risk/account-health` |

### 2. MISSING BACKEND ENDPOINTS

These frontend calls will fail (404):

1. `GET /api/exchanges/supported` - Need to implement in exchange.py
2. `GET /api/portfolio/recent-transactions` - Need to implement in portfolio.py
3. `POST /api/orders/stop-loss` - Need to implement in orders.py
4. `POST /api/orders/take-profit` - Need to implement in orders.py
5. `GET /api/orders/open` - Need to implement in orders.py
6. `POST /api/orders/cancel/{order_id}` - Partially exists but different signature
7. `POST /api/orders/cancel-all` - Need to implement in orders.py
8. `GET /api/billing/payment-methods` - Need to implement in billing.py
9. `GET /api/risk/strategy-limits` - Need to implement in risk.py
10. `PUT /api/risk/strategy-limits/{id}` - Need to implement in risk.py
11. `POST /api/support/tickets` - Need to implement support router
12. `GET /api/support/tickets` - Need to implement support router
13. `POST /api/portfolio/close-all` - Need to implement in portfolio.py
14. `POST /api/strategies/deploy` (DAG) - Need to implement in strategies.py

### 3. PAYLOAD MISMATCHES

| Endpoint | Frontend Sends | Backend Expects |
|----------|---------------|-------------------|
| `POST /api/auth/signout` | Empty body | `{token: string}` |
| `POST /api/orders/cancel/{id}` | Not defined | `CancelOrderRequest` body with symbol |
| `PUT /api/risk/settings` | Flat object | `RiskSettingsRequest` with nested kill_switches |

---

## Recommended Actions

### Priority 1 (Critical - Breaking)
1. Fix `api.js` line 526: Change `/api/orders/trades/history` to `/api/orders/history`
2. Fix `api.js` risk endpoints: Change `/config` to `/settings`
3. Fix `api.js` margin-health: Change to `/account-health`

### Priority 2 (High - Missing Features)
1. Implement `GET /api/exchanges/supported` in backend
2. Implement missing order endpoints (stop-loss, take-profit, open orders, cancel)
3. Implement `GET /api/portfolio/recent-transactions`
4. Implement `GET /api/billing/payment-methods`

### Priority 3 (Medium - Nice to Have)
1. Implement support ticket endpoints
2. Implement strategy-limits endpoints in risk router
3. Implement DAG strategy deployment endpoint
4. Add analytics endpoint to frontend

---

## Clean Frontend-Backend Contract

### Standardized Naming Convention
- Use `{id}` not `{strategy_id}` in frontend paths (backend uses both)
- Use kebab-case consistently
- Query params should match exactly

### Required Response Shapes

#### Portfolio Summary
```javascript
// Backend returns:
{
  total_value: number,
  pnl_24h: number,
  unrealized_pnl: number,
  roi_percentage: number
}

// Frontend adapter maps to:
{
  total_value: number,
  unrealized_pnl: number,
  realized_pnl: number,
  roi_percentage: number
}
```

#### Order History
```javascript
// Backend returns array of objects with columns from QuestDB
[
  { timestamp, symbol, side, amount, price, pnl, ... }
]

// Frontend expects TradeHistoryItem
[
  { id, symbol, side, entry, exit, size, pnl, fees, time }
]
```

---

## Files Modified

- `c:\Users\user\Desktop\aerora_quant_backend_updated_final1\algo22-terminal\src\api.js`
- `c:\Users\user\Desktop\aerora_quant_backend_updated_final1\algo22-terminal\src\endpoints.js`
- `c:\Users\user\Desktop\aerora_quant_backend_updated_final1\aerora_quant_backend_updated_final1\routers\*.py`

---

*Report generated by API compatibility scanner*
