# Unified API Architecture

## Summary

Successfully consolidated `api.js` and `endpoints.js` into a single clean architecture with one source of truth.

---

## Final Folder Structure

```
src/
├── api/
│   ├── index.js              # Main exports (consolidated API)
│   └── modules/
│       ├── auth.js
│       ├── exchange.js
│       ├── market.js
│       ├── orders.js
│       ├── strategies.js
│       ├── portfolio.js
│       ├── risk.js
│       ├── billing.js
│       ├── user.js
│       ├── support.js
│       ├── leaderboard.js
│       └── notifications.js
├── apiClient.js            # HTTP client (unchanged)
└── [other components]
```

---

## Migration Guide

### Old Imports (Deprecated)

```javascript
// BEFORE - api.js
import { exchangeApi, marketApi } from './api';

// BEFORE - endpoints.js  
import endpoints from './endpoints';
import { orderEndpoints, marketEndpoints } from './endpoints';
```

### New Imports (Unified)

```javascript
// Option 1: Import consolidated API object
import { api } from './api';
const data = await api.strategies.backtest(payload);

// Option 2: Import specific modules
import { strategiesApi } from './api/modules/strategies';
const data = await strategiesApi.backtest(payload);

// Option 3: Import legacy compatibility export
import { endpoints } from './api';
const data = await endpoints.strategies.backtest(payload);

// Option 4: Import HTTP client directly
import { get, post, put, del } from './api';
```

---

## API Module Reference

| Module | Endpoints | Methods |
|--------|-----------|---------|
| `auth` | `/api/auth/*` | `signUp`, `signIn`, `signOut`, `getMe`, `register` |
| `exchange` | `/api/exchanges/*` | `getSupported`, `testConnection`, `saveKeys`, `list`, `delete` |
| `market` | `/api/market/*` | `getCandles`, `getOrderBook`, `getTicker`, `getFundingRate`, `getSymbols`, `haltStrategies`, `closeAllPositions` |
| `orders` | `/api/orders/*` | `execute`, `create`, `getHistory`, `getOpen`, `cancel`, `cancelAll`, `stopLoss`, `takeProfit` |
| `strategies` | `/api/strategies/*` | `list`, `getById`, `create`, `update`, `delete`, `deploy`, `start`, `stop`, `pause`, `backtest`, `deployDag`, `trainMl` |
| `portfolio` | `/api/portfolio/*` | `getSummary`, `getEquityCurve`, `getAllocation`, `getHeatmap`, `getRecentTransactions`, `getHistory`, `closeAllPositions` |
| `risk` | `/api/risk/*` | `getConfig`, `updateConfig`, `getStrategyLimits`, `updateStrategyLimit`, `getMarginHealth`, `getAccountHealth`, `killSwitch` |
| `billing` | `/api/billing/*` | `getPlan`, `getInvoices`, `getPaymentMethods`, `createCheckout` |
| `user` | `/api/user/*`, `/api/*` | `getProfile`, `updateProfile`, `getStats`, `getReferralStats`, `getLeaderboard`, `getSecurityLogs` |
| `support` | `/api/support/*` | `createTicket`, `getTickets` |
| `leaderboard` | `/api/leaderboard/*` | `getLeaderboard` |
| `notifications` | `/api/notifications/*` | `getSettings`, `updateSettings` |

---

## Backend Endpoint Verification

All endpoints have been verified against the backend FastAPI routers:

| Frontend Endpoint | Backend Route | Status |
|-------------------|---------------|--------|
| `/api/auth/signin` | `auth.py` | ✅ |
| `/api/auth/signup` | `auth.py` | ✅ |
| `/api/auth/me` | `auth.py` | ✅ |
| `/api/exchanges/supported` | `exchange.py` | ✅ |
| `/api/exchanges/test` | `exchange.py` | ✅ |
| `/api/exchanges/keys` | `exchange.py` | ✅ |
| `/api/market/candles/{symbol}/{tf}` | `market.py` | ✅ |
| `/api/market/orderbook/{symbol}` | `market.py` | ✅ |
| `/api/orders/execute` | `orders.py` | ✅ |
| `/api/orders/history` | `orders.py` | ✅ |
| `/api/strategies` | `strategies.py` | ✅ |
| `/api/strategies/{id}/deploy` | `strategies.py` | ✅ |
| `/api/strategies/backtest` | `strategies.py` | ✅ |
| `/api/portfolio/summary` | `portfolio.py` | ✅ |
| `/api/risk/settings` | `risk.py` | ✅ |
| `/api/billing/plan` | `billing.py` | ✅ |
| `/api/user/profile` | `user.py` | ✅ |
| `/api/leaderboard` | `user.py` | ✅ |
| `/api/stats` | `main.py` | ✅ |

---

## Files Modified

### Created
- `src/api/index.js` - Main unified export
- `src/api/modules/auth.js`
- `src/api/modules/exchange.js`
- `src/api/modules/market.js`
- `src/api/modules/orders.js`
- `src/api/modules/strategies.js`
- `src/api/modules/portfolio.js`
- `src/api/modules/risk.js`
- `src/api/modules/billing.js`
- `src/api/modules/user.js`
- `src/api/modules/support.js`
- `src/api/modules/leaderboard.js`
- `src/api/modules/notifications.js`

### Updated
- `src/App.jsx` - Changed import from `endpoints` to `api`
- `src/emergency-terminal.jsx` - Updated imports and API calls
- `src/SupportPage.jsx` - Updated imports and API calls

### Deprecated (to be removed)
- `src/api.js` - Functionality merged into new structure
- `src/endpoints.js` - Functionality merged into new structure

---

## Usage Examples

### Example 1: Backtest Strategy

```javascript
import { api } from './api';

const runBacktest = async () => {
  const payload = {
    strategies: ['rsi', 'macd'],
    symbols: ['BTCUSDT'],
    timeframe: '1h',
    initial_capital: 10000,
    trade_size_pct: 0.1,
    stop_loss_pct: 0.02,
    take_profit_pct: 0.04,
    ml_threshold: 0.75,
    params: {
      dag_nodes: nodes,
      dag_edges: edges,
    }
  };
  
  const result = await api.strategies.backtest(payload);
  console.log('Backtest result:', result);
};
```

### Example 2: Place Order

```javascript
import { api } from './api';

const placeOrder = async () => {
  const order = {
    symbol: 'BTC/USDT',
    side: 'buy',
    order_type: 'market',
    amount: 0.01,
    order_id: crypto.randomUUID()
  };
  
  const result = await api.orders.execute(order);
  console.log('Order placed:', result);
};
```

### Example 3: Get Portfolio

```javascript
import { api } from './api';

const loadPortfolio = async () => {
  const summary = await api.portfolio.getSummary();
  const equityCurve = await api.portfolio.getEquityCurve(90);
  const allocation = await api.portfolio.getAllocation();
  
  console.log('Portfolio:', { summary, equityCurve, allocation });
};
```

### Example 4: Direct HTTP Client

```javascript
import { get, post } from './api';

const customCall = async () => {
  const data = await get('/api/custom/endpoint', { params: { id: 123 } });
  const result = await post('/api/custom/endpoint', { foo: 'bar' });
};
```

---

## Benefits

1. **Single Source of Truth** - All API definitions in one place
2. **Modular Organization** - Each domain in its own file
3. **Type Safety** - JSDoc types for all methods
4. **Legacy Compatibility** - Old imports still work via compatibility exports
5. **Clean Imports** - Simple, consistent import patterns
6. **Maintainable** - Easy to add new endpoints or modify existing ones
7. **Backend Sync** - All endpoints verified against backend routers

---

## Next Steps

1. Test all refactored imports in development
2. Remove old `api.js` and `endpoints.js` files after verification
3. Update any remaining imports throughout the codebase
4. Consider adding TypeScript definitions for enhanced type safety

---

*Architecture refactored: Unified API layer with single source of truth*
