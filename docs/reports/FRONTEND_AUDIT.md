# 🟡 STEP 3 — FRONTEND AUDIT REPORT
**Algorithmic Trading Platform — UI/UX Assessment**

**Audit Date:** May 3, 2026  
**Auditor:** Senior System Architect  
**Scope:** All frontend React components, API integration, WebSocket handling

---

## EXECUTIVE SUMMARY

### 🔴 CRITICAL: 4
### ⚠️ HIGH: 6
### ⚡ MEDIUM: 5
### ℹ️ LOW: 3

**Overall Status:** ⚠️ **REQUIRES HARDENING BEFORE PRODUCTION**

The frontend has **good foundational architecture** but has **critical UX safety gaps** that could lead to user errors with financial impact.

---

## 1. API INTEGRATION AUDIT

### 🔴 C1: No Idempotency Key in Order Execution (CRITICAL)
**File:** `algo22-terminal/src/App.jsx:7805-7834`  
**Issue:** Order execution does NOT send `Idempotency-Key` header, risking duplicate orders on retry.

```javascript
const handleExecute = async (side) => {
  const payload = {
    symbol: activePair,
    side: side.toUpperCase(),
    order_type: orderType.toUpperCase(),
    amount: numSize,
    order_id: crypto.randomUUID()  // ❌ This is body-level, not header idempotency
  };
  
  // ❌ NO Idempotency-Key header!
  const data = await api.orders.execute(payload);
  // ...
};
```

**Backend expects:**
```javascript
// routers/orders.py expects header
idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
```

**Impact:** Network timeout → retry → duplicate order = double execution.  
**Financial Risk:** CRITICAL - User could place same order twice, doubling position.  
**Fix:**
```javascript
const handleExecute = async (side) => {
  const idempotencyKey = `order_${Date.now()}_${crypto.randomUUID()}`;
  
  const payload = { /* ... */ };
  
  // Send with idempotency header
  const data = await api.orders.execute(payload, {
    headers: {
      'Idempotency-Key': idempotencyKey
    }
  });
};
```

---

### 🔴 C2: No Order Confirmation Modal (CRITICAL UX)
**File:** `algo22-terminal/src/App.jsx:7944-7954`  
**Issue:** Clicking BUY/SELL executes immediately with NO confirmation dialog.

```javascript
<button 
  onClick={() => handleExecute("buy")}  // ❌ Immediate execution!
  disabled={isExecuting || numSize <= 0}
  style={{ background: C.green, /* ... */ }}
>
  BUY
</button>
```

**Impact:** Accidental click = immediate trade execution.  
**Financial Risk:** HIGH - Misclick can cost thousands.  
**Fix:** Add confirmation modal:
```javascript
const [pendingOrder, setPendingOrder] = useState(null);

const handleExecute = async (side) => {
  // Show confirmation instead of immediate execution
  setPendingOrder({ side, size: numSize, price: tickerPrice, total: estTotal });
};

// In render:
{pendingOrder && (
  <OrderConfirmModal
    order={pendingOrder}
    onConfirm={() => {
      executeOrder(pendingOrder.side);
      setPendingOrder(null);
    }}
    onCancel={() => setPendingOrder(null)}
  />
)}
```

---

### 🔴 C3: Missing Price Validation (CRITICAL)
**File:** `algo22-terminal/src/App.jsx:7805-7834`  
**Issue:** No validation that order price is within reasonable bounds of market price.

**Impact:** User can accidentally place limit order 50% away from market price.  
**Financial Risk:** HIGH - Order fills at unexpected price or doesn't fill.  
**Fix:**
```javascript
const handleExecute = async (side) => {
  // Validate price is within acceptable range
  if (orderType === 'limit' && numPrice > 0) {
    const priceDiff = Math.abs(numPrice - tickerPrice) / tickerPrice;
    if (priceDiff > 0.05) {  // 5% deviation
      setToast({
        type: "warning",
        msg: `Price ${numPrice} is ${(priceDiff*100).toFixed(1)}% from market. Confirm?`
      });
      // Require explicit confirmation for large deviations
      return;
    }
  }
  // ...
};
```

---

### 🔴 C4: Assumes Order Filled Without Confirmation (CRITICAL)
**File:** `algo22-terminal/src/App.jsx:7821-7824`  
**Issue:** Toast message claims "FILLED" when API response doesn't confirm fill status.

```javascript
setToast({
  type: "success",
  msg: `${side.toUpperCase()} FILLED: ${size} ${baseAsset}. ID: ${data?.order_id || 'CCXT-SYNC'}`  // ❌ Assumes filled!
});
```

**Impact:** User thinks order filled when it might be pending/rejected.  
**Financial Risk:** HIGH - User acts on false assumption of position.  
**Fix:**
```javascript
// Check actual status from response
const status = data?.status || 'pending';
const isFilled = status === 'completed' || status === 'filled';

setToast({
  type: isFilled ? "success" : "warning",
  msg: isFilled 
    ? `${side.toUpperCase()} FILLED: ${size} ${baseAsset}. ID: ${data?.order_id}`
    : `${side.toUpperCase()} ${status.toUpperCase()}: ${size} ${baseAsset}. ID: ${data?.order_id}. Monitoring...`
});

// Subscribe to fill updates via WebSocket
if (!isFilled && data?.order_id) {
  subscribeToOrderUpdates(data.order_id);
}
```

---

### ⚠️ H1: Missing Loading State on Order Button (HIGH)
**File:** `algo22-terminal/src/App.jsx:7944-7954`  
**Issue:** Button only disables during execution, no visual loading indicator.

```javascript
<button 
  onClick={() => handleExecute("buy")}
  disabled={isExecuting || numSize <= 0}  // ❌ No spinner/progress
>
  BUY
</button>
```

**Impact:** User doesn't know if click registered, may click multiple times.  
**Financial Risk:** MEDIUM - Duplicate clicks could queue multiple orders.  
**Fix:**
```javascript
<button 
  onClick={() => handleExecute("buy")}
  disabled={isExecuting || numSize <= 0}
>
  {isExecuting ? (
    <><Spinner size={14} /> Submitting...</>
  ) : (
    "BUY"
  )}
</button>
```

---

### ⚠️ H2: No Risk Exposure Display Before Trade (HIGH UX)
**File:** `algo22-terminal/src/App.jsx:7795-7834`  
**Issue:** No display of total exposure, available balance, or risk before confirming trade.

**Impact:** User doesn't know if they have sufficient funds or are over-leveraged.  
**Financial Risk:** HIGH - Can exceed account limits without warning.  
**Fix:** Add risk panel:
```javascript
const TradePanel = () => {
  const [accountInfo, setAccountInfo] = useState(null);
  
  useEffect(() => {
    api.portfolio.getSummary().then(setAccountInfo);
  }, []);
  
  return (
    <div>
      {/* ... existing fields ... */}
      
      {/* Risk Display */}
      <RiskPanel>
        <Row label="Available Balance" value={accountInfo?.available_balance} />
        <Row label="This Trade Value" value={estTotal} />
        <Row label="Post-Trade Exposure" value={accountInfo?.total_exposure + estTotal} />
        <WarningBanner 
          show={accountInfo?.total_exposure + estTotal > accountInfo?.available_balance * 0.9}
          message="This trade will use 90%+ of available margin"
        />
      </RiskPanel>
    </div>
  );
};
```

---

### ⚠️ H3: No Error Retry for Failed Orders (HIGH)
**File:** `algo22-terminal/src/App.jsx:7825-7830`  
**Issue:** Network errors show toast but don't offer retry mechanism.

```javascript
catch (err) {
  console.error("Execution Engine Failed:", err);
  setToast({
    type: "error",
    msg: err?.response?.data?.detail || "REJECTED: Failed to route order to exchange."
  });  // ❌ Just shows error, no retry option
}
```

**Impact:** Transient failures require manual re-entry of order.  
**Financial Risk:** MEDIUM - Delay in execution = price movement.  
**Fix:**
```javascript
catch (err) {
  const isRetryable = !err?.response?.status || err.response.status >= 500;
  
  setToast({
    type: "error",
    msg: (
      <div>
        <p>Order failed: {err?.response?.data?.detail || "Network error"}</p>
        {isRetryable && (
          <button onClick={() => handleExecute(side)}>
            Retry Order
          </button>
        )}
      </div>
    )
  });
}
```

---

### ⚠️ H4: WebSocket Reconnection Doesn't Refresh Data (HIGH)
**File:** `algo22-terminal/src/websocketClient.js:79-102`  
**Issue:** After reconnection, queued messages sent but no full data refresh.

```javascript
handleOpen() {
  // ...
  this.flushMessageQueue();  // ❌ Only sends queued messages
  // No data refresh after reconnect!
}
```

**Impact:** UI shows stale data after WebSocket reconnect.  
**Financial Risk:** HIGH - Trading on stale prices after reconnect.  
**Fix:**
```javascript
handleOpen() {
  // ...
  this.flushMessageQueue();
  
  // Trigger data refresh
  this.subscriptions.forEach((callbacks, eventType) => {
    if (eventType === 'ticker' || eventType === 'orderbook') {
      // Request fresh snapshot
      this.send({ action: 'refresh', type: eventType });
    }
  });
}
```

---

### ⚠️ H5: API Client Doesn't Validate Token Expiry (HIGH)
**File:** `algo22-terminal/src/apiClient.js` (error handling)  
**Issue:** 401 errors are categorized but don't trigger automatic token refresh or re-login.

```javascript
if (this.status === 401 || this.status === 403) {
  this.category = 'AUTH_ERROR';  // ❌ No auto-refresh or redirect
}
```

**Impact:** Session expires → API calls fail silently or with confusing errors.  
**Financial Risk:** MEDIUM - Can't place/cancel orders during active session.  
**Fix:**
```javascript
// In ApiError handling
if (this.category === 'AUTH_ERROR') {
  // Clear token and redirect to login
  localStorage.removeItem('token');
  window.location.href = '/login?expired=true';
}
```

---

### ⚠️ H6: No PnL Update Subscription (HIGH)
**File:** `algo22-terminal/src/App.jsx`  
**Issue:** No WebSocket subscription to PnL updates for real-time position tracking.

**Impact:** Position PnL is stale, user makes decisions on outdated data.  
**Financial Risk:** HIGH - Can't react to rapid losses.  
**Fix:**
```javascript
useEffect(() => {
  // Subscribe to PnL updates
  const unsubscribe = wsClient.subscribePnLUpdates((update) => {
    setPositions(prev => prev.map(pos => 
      pos.id === update.position_id 
        ? { ...pos, unrealized_pnl: update.unrealized_pnl }
        : pos
    ));
    
    // Alert on large losses
    if (update.unrealized_pnl < -1000) {
      setToast({ type: 'warning', msg: `Position ${update.symbol} down $${Math.abs(update.unrealized_pnl)}` });
    }
  });
  
  return unsubscribe;
}, []);
```

---

## 2. UI LOGIC BUGS

### ⚡ M1: Risk Settings Save on Every Slider Move (MEDIUM)
**File:** `algo22-terminal/src/App.jsx:6284-6301`  
**Issue:** Debounced but still triggers many API calls during slider adjustment.

```javascript
const handleSliderChange = (key, value) => {
  clearTimeout(sliderDebounceRef.current);
  sliderDebounceRef.current = setTimeout(() => {
    saveRiskConfig({ ... });  // ❌ Called even for small adjustments
  }, 500);
};
```

**Fix:** Add "Save" button instead of auto-save, or increase debounce to 2s.

---

### ⚡ M2: Order Size Parse Float Without Validation (MEDIUM)
**File:** `algo22-terminal/src/App.jsx:7800`  
**Issue:** NaN handling could allow invalid orders.

```javascript
const numSize = parseFloat(size) || 0;  // ❌ 0 is valid but might not be intended
```

**Fix:**
```javascript
const numSize = parseFloat(size);
if (isNaN(numSize) || numSize <= 0) {
  setToast({ type: 'error', msg: 'Please enter a valid order size' });
  return;
}
```

---

### ⚡ M3: WebSocket Subscriptions Not Cleaned Up (MEDIUM)
**File:** `algo22-terminal/src/App.jsx` (multiple places)  
**Issue:** Component unmount doesn't unsubscribe from WebSocket events.

```javascript
useEffect(() => {
  wsClient.subscribe('ticker', handleTicker);  // ❌ No cleanup!
}, []);
```

**Fix:**
```javascript
useEffect(() => {
  const unsubscribe = wsClient.subscribe('ticker', handleTicker);
  return unsubscribe;  // Cleanup on unmount
}, []);
```

---

### ⚡ M4: Toast Messages Auto-Close Without User Action (MEDIUM)
**File:** `algo22-terminal/src/App.jsx:7838-7841`  
**Issue:** Success/error toasts disappear automatically, user might miss critical info.

**Fix:** Require manual dismissal for critical messages:
```javascript
{toast && (
  <Toast 
    {...toast} 
    dismissible={true}
    autoClose={toast.type === 'success' ? 5000 : null}  // Errors stay until dismissed
  />
)}
```

---

### ⚡ M5: No Visual Distinction Between Paper and Live Trading (MEDIUM)
**File:** `algo22-terminal/src/App.jsx`  
**Issue:** UI looks identical regardless of trading mode (paper vs live).

**Financial Risk:** User might think they're in paper mode when actually live.  
**Fix:** Add prominent mode indicator:
```javascript
<div style={{ 
  position: 'fixed', 
  top: 0, 
  background: isLiveMode ? '#ff0000' : '#00ff00',
  color: '#000',
  padding: '4px 12px',
  fontWeight: 'bold'
}}>
  {isLiveMode ? '🔴 LIVE TRADING - REAL MONEY' : '🟢 PAPER TRADING - SIMULATED'}
</div>
```

---

## 3. UX AUDIT (CRITICAL FOR TRADING SAFETY)

### Dangerous UX Patterns Found:

| Pattern | Location | Risk | Severity |
|---------|----------|------|----------|
| **One-click execution** | App.jsx:7944 | Accidental trades | 🔴 Critical |
| **No price confirmation** | App.jsx:7805 | Extreme price execution | 🔴 Critical |
| **No exposure preview** | Trading panel | Over-leverage | ⚠️ High |
| **No order status tracking** | App.jsx:7821 | False fill assumptions | 🔴 Critical |
| **Identical UI for paper/live** | Global | Mode confusion | ⚡ Medium |

### Required UX Safety Features:

1. **Order Confirmation Modal** (C2)
   - Show order summary: side, size, price, total, fees
   - Require explicit confirmation
   - Highlight unusual values (large size, extreme price)

2. **Risk Preview Panel** (H2)
   - Show available balance
   - Show post-trade exposure
   - Warn at 80% margin usage
   - Block at 95% margin usage

3. **Mode Indicator** (M5)
   - Always-visible paper/live indicator
   - Different color themes per mode
   - Confirmation when switching to live

4. **Order Status Tracking** (C4)
   - Don't assume "filled"
   - Show pending/open/filled status
   - Subscribe to fill updates
   - Alert on rejections

---

## 4. REAL-TIME INTEGRATION AUDIT

### WebSocket Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  WebSocketClient                                            │
│  ├── connect() → Authenticate → Subscribe                     │
│  ├── handleMessage() → Route to callbacks                   │
│  ├── startHeartbeat() → Ping/Pong every 30s                │
│  └── scheduleReconnect() → Exponential backoff             │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  UI Components                                              │
│  ├── subscribe() on mount                                   │
│  ├── update state on message                                │
│  └── ❌ Missing: cleanup on unmount                        │
└─────────────────────────────────────────────────────────────┘
```

### Data Freshness Issues

| Data Type | Source | Update Mechanism | Stale Risk |
|-----------|--------|------------------|------------|
| Ticker | WebSocket | Real-time | Low (heartbeat) |
| Orderbook | WebSocket | Real-time | Low (heartbeat) |
| Positions | WebSocket | Push updates | ⚠️ No fallback poll |
| PnL | ❌ Not subscribed | ❌ None | 🔴 Always stale |
| Orders | ❌ Not subscribed | ❌ None | 🔴 Status unknown |

### Stale Data Scenarios

**Scenario 1: WebSocket Disconnect During Trade**
```
1. User places order
2. WebSocket disconnects (network blip)
3. Order fills on exchange
4. WebSocket reconnects
5. ❌ No fill notification sent (missed during disconnect)
6. UI shows order as "pending"
7. User thinks order failed, places again
8. Double position!
```

**Fix:**
```javascript
// On WebSocket reconnect, refresh all pending orders
useEffect(() => {
  if (wsStatus === 'connected') {
    refreshPendingOrders();  // Poll API for status
  }
}, [wsStatus]);
```

---

## COMPLIANCE CHECKLIST

| Requirement | Status | Fix |
|-------------|--------|-----|
| Order confirmation dialog | ❌ Missing | C2 |
| Idempotency key usage | ❌ Missing | C1 |
| Price validation | ❌ Missing | C3 |
| Order status tracking | ❌ Missing | C4 |
| Risk exposure preview | ❌ Missing | H2 |
| Paper/live mode indicator | ❌ Missing | M5 |
| WebSocket cleanup | ⚠️ Partial | M3 |
| Error retry mechanism | ❌ Missing | H3 |
| Auto-reconnect | ✅ Present | Good |
| Heartbeat/ping-pong | ✅ Present | Good |

---

## REMEDIATION PLAN

### Phase 1: Critical Safety (DO NOT TRADE WITHOUT)

1. **C2 - Order Confirmation Modal** (4 hours)
2. **C1 - Idempotency Headers** (2 hours)
3. **C3 - Price Validation** (2 hours)
4. **C4 - Order Status Handling** (3 hours)

### Phase 2: Risk Visibility (1 Week)

5. **H2 - Risk Preview Panel** (4 hours)
6. **H6 - PnL Subscription** (3 hours)
7. **M5 - Mode Indicator** (2 hours)

### Phase 3: Reliability (2 Weeks)

8. **H3 - Error Retry** (3 hours)
9. **H4 - Reconnect Refresh** (2 hours)
10. **M3 - Subscription Cleanup** (2 hours)

---

## FILES AUDITED

| File | Lines | Critical Issues |
|------|-------|-----------------|
| `App.jsx` | 8158 | C1, C2, C3, C4, H1, H2, H6 |
| `apiClient.js` | 1401 | H5 |
| `websocketClient.js` | 424 | H4, M3 |
| `api/modules/orders.js` | 114 | ✅ Good |
| `api/typed-client.ts` | 445 | ✅ Good |

---

## FINAL VERDICT

### ✅ Strengths
1. **API Client** - Well-structured with error handling, retry, caching
2. **Type Safety** - Full TypeScript definitions
3. **WebSocket Client** - Proper reconnection, heartbeat
4. **Error Boundaries** - App won't crash on component errors
5. **Design System** - Consistent colors, components

### ❌ Critical Weaknesses
1. **No Order Confirmation** - One-click execution is dangerous
2. **No Idempotency** - Risk duplicate orders
3. **Assumes Filled** - Wrong status display
4. **No Risk Preview** - Can't see exposure before trade
5. **Stale PnL** - No real-time position updates

### 🎯 Verdict

**DO NOT ENABLE LIVE TRADING** until:
- [ ] Order confirmation modal implemented (C2)
- [ ] Idempotency keys sent (C1)
- [ ] Order status properly tracked (C4)
- [ ] Risk exposure visible before trade (H2)
- [ ] Paper/live mode clearly distinguished (M5)

**Current state is appropriate for:**
- ✅ Internal testing
- ✅ Paper trading
- ✅ UI development
- ❌ Live trading with real money

---

*Frontend Audit Complete*  
*Next Recommended Step: Security Audit (API keys, auth, encryption)*
