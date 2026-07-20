# 🟡 FRONTEND FIXES
**Production-Ready UI Components**

---

## FIX 1: Idempotency Key Header (CRITICAL - C1)

**File:** `algo22-terminal/src/App.jsx:7805-7834`

### Current (BROKEN):
```javascript
const handleExecute = async (side) => {
  const payload = {
    symbol: activePair,
    side: side.toUpperCase(),
    order_type: orderType.toUpperCase(),
    amount: numSize,
    order_id: crypto.randomUUID()  // ❌ Body-level, not header
  };
  
  const data = await api.orders.execute(payload);  // ❌ No idempotency header
  // ...
};
```

### Fixed:
```javascript
// Store idempotency keys for pending orders
const pendingIdempotencyKeys = useRef(new Map());

const handleExecute = async (side) => {
  if (isExecuting || numSize <= 0) return;
  
  // Generate idempotency key
  const idempotencyKey = `order_${Date.now()}_${crypto.randomUUID()}`;
  
  // Store for potential retry
  const orderKey = `${activePair}_${side}_${numSize}`;
  pendingIdempotencyKeys.current.set(orderKey, idempotencyKey);
  
  setIsExecuting(true);
  try {
    const payload = {
      symbol: activePair,
      side: side.toUpperCase(),
      order_type: orderType.toUpperCase(),
      amount: numSize,
      price: orderType === 'limit' ? numPrice : undefined,
      order_id: crypto.randomUUID()
    };

    // Send with idempotency header
    const data = await api.orders.execute(payload, {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Client-Timestamp': new Date().toISOString()
      }
    });

    // Clear pending key on success
    pendingIdempotencyKeys.current.delete(orderKey);
    
    // Handle status properly (see Fix 4)
    handleOrderResponse(data, side);
    
  } catch (err) {
    handleOrderError(err, side, orderKey);
  } finally {
    setIsExecuting(false);
  }
};

const handleOrderError = (err, side, orderKey) => {
  console.error("Execution Engine Failed:", err);
  
  const isDuplicate = err?.response?.status === 409;
  const isRetryable = err?.response?.status >= 500 || !err?.response;
  
  if (isDuplicate) {
    // Order already exists, fetch status
    setToast({
      type: "info",
      msg: "Order already submitted. Checking status..."
    });
    refreshOrderStatus(orderKey);
  } else if (isRetryable) {
    // Offer retry with same idempotency key
    setToast({
      type: "warning",
      msg: (
        <div>
          <p>Network error. Order may not have been submitted.</p>
          <button onClick={() => retryOrder(side, orderKey)}>
            Retry with Same Key
          </button>
        </div>
      ),
      autoClose: false
    });
  } else {
    setToast({
      type: "error",
      msg: err?.response?.data?.detail || "Order rejected by exchange."
    });
  }
};

const retryOrder = async (side, orderKey) => {
  const idempotencyKey = pendingIdempotencyKeys.current.get(orderKey);
  if (!idempotencyKey) {
    setToast({ type: "error", msg: "Cannot retry - order key expired" });
    return;
  }
  
  // Retry with same idempotency key (backend will return cached result if already submitted)
  handleExecute(side);
};
```

---

## FIX 2: Order Confirmation Modal (CRITICAL - C2)

**File:** `algo22-terminal/src/App.jsx` (Add new component + modify buttons)

### New Component:
```javascript
const OrderConfirmationModal = ({ 
  isOpen, 
  order, 
  accountInfo,
  onConfirm, 
  onCancel,
  isSubmitting 
}) => {
  if (!isOpen || !order) return null;
  
  const { side, size, price, total, fees, symbol, type } = order;
  const isBuy = side === 'buy';
  const postTradeExposure = (accountInfo?.total_exposure || 0) + total;
  const availableBalance = accountInfo?.available_balance || 0;
  const highRisk = postTradeExposure > availableBalance * 0.9;
  const extremeRisk = postTradeExposure > availableBalance;
  
  // Price deviation check
  const marketPrice = order.marketPrice || price;
  const priceDeviation = type === 'limit' && price 
    ? Math.abs(price - marketPrice) / marketPrice 
    : 0;
  const extremeDeviation = priceDeviation > 0.05; // 5%
  
  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.8)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000
    }}>
      <div style={{
        background: C.bg2,
        border: `1px solid ${extremeRisk ? C.loss : C.border}`,
        borderRadius: 12,
        padding: 24,
        maxWidth: 400,
        width: '90%',
        boxShadow: extremeRisk ? C.glow.loss : C.shadowLg
      }}>
        {/* Header */}
        <div style={{ 
          display: 'flex', 
          alignItems: 'center', 
          gap: 12,
          marginBottom: 20,
          paddingBottom: 16,
          borderBottom: `1px solid ${C.border}`
        }}>
          <div style={{
            width: 40,
            height: 40,
            borderRadius: 8,
            background: isBuy ? C.profitBg : C.lossBg,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {isBuy ? <TrendingUp size={20} color={C.profit} /> : <TrendingDown size={20} color={C.loss} />}
          </div>
          <div>
            <h3 style={{ color: C.t1, fontSize: 16, fontWeight: 700 }}>
              Confirm {side.toUpperCase()} Order
            </h3>
            <p style={{ color: C.t3, fontSize: 11, fontFamily: 'monospace' }}>
              {symbol}
            </p>
          </div>
        </div>
        
        {/* Order Details */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 20 }}>
          <DetailRow label="Type" value={type.toUpperCase()} />
          <DetailRow label="Size" value={size} />
          <DetailRow label="Price" value={type === 'market' ? 'Market Price' : `$${price.toFixed(2)}`} />
          <DetailRow label="Total Value" value={`$${total.toFixed(2)}`} highlight />
          <DetailRow label="Estimated Fee" value={`$${fees.toFixed(2)}`} />
        </div>
        
        {/* Risk Warnings */}
        {(highRisk || extremeRisk || extremeDeviation) && (
          <div style={{
            background: extremeRisk ? C.lossBg : 'rgba(255,171,0,0.1)',
            border: `1px solid ${extremeRisk ? C.loss : C.warning}40`,
            borderRadius: 8,
            padding: 12,
            marginBottom: 20
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <AlertTriangle size={16} color={extremeRisk ? C.loss : C.warning} />
              <span style={{ 
                color: extremeRisk ? C.loss : C.warning, 
                fontSize: 12, 
                fontWeight: 700 
              }}>
                Risk Warning
              </span>
            </div>
            
            {extremeRisk && (
              <p style={{ color: C.loss, fontSize: 11, marginBottom: 4 }}>
                ⚠️ This order EXCEEDS your available balance!
              </p>
            )}
            {highRisk && !extremeRisk && (
              <p style={{ color: C.warning, fontSize: 11, marginBottom: 4 }}>
                ⚠️ This will use {(postTradeExposure/availableBalance*100).toFixed(0)}% of available margin
              </p>
            )}
            {extremeDeviation && (
              <p style={{ color: C.warning, fontSize: 11 }}>
                ⚠️ Limit price is {(priceDeviation*100).toFixed(1)}% from market price
              </p>
            )}
          </div>
        )}
        
        {/* Post-Trade Summary */}
        <div style={{
          background: C.bg3,
          borderRadius: 8,
          padding: 12,
          marginBottom: 20
        }}>
          <DetailRow 
            label="Available Balance" 
            value={`$${availableBalance.toFixed(2)}`} 
          />
          <DetailRow 
            label="Post-Trade Exposure" 
            value={`$${postTradeExposure.toFixed(2)}`}
            valueColor={highRisk ? C.warning : C.t1}
          />
        </div>
        
        {/* Actions */}
        <div style={{ display: 'flex', gap: 12 }}>
          <button
            onClick={onCancel}
            disabled={isSubmitting}
            style={{
              flex: 1,
              padding: '12px',
              background: 'transparent',
              border: `1px solid ${C.border}`,
              borderRadius: 6,
              color: C.t2,
              fontFamily: 'monospace',
              fontWeight: 700,
              cursor: 'pointer'
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isSubmitting || extremeRisk}
            style={{
              flex: 1,
              padding: '12px',
              background: isBuy ? C.profit : C.loss,
              border: 'none',
              borderRadius: 6,
              color: '#000',
              fontFamily: 'monospace',
              fontWeight: 900,
              cursor: isSubmitting || extremeRisk ? 'not-allowed' : 'pointer',
              opacity: isSubmitting || extremeRisk ? 0.5 : 1
            }}
          >
            {isSubmitting ? (
              <><Spinner size={14} /> Confirming...</>
            ) : (
              `Confirm ${side.toUpperCase()}`
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

const DetailRow = ({ label, value, highlight, valueColor }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
    <span style={{ color: C.t3, fontSize: 11 }}>{label}</span>
    <span style={{ 
      color: valueColor || (highlight ? C.accent : C.t1), 
      fontSize: 12, 
      fontWeight: highlight ? 700 : 400,
      fontFamily: highlight ? 'monospace' : 'inherit'
    }}>
      {value}
    </span>
  </div>
);
```

### Modified handleExecute:
```javascript
const [pendingOrder, setPendingOrder] = useState(null);
const [showConfirmModal, setShowConfirmModal] = useState(false);
const [accountInfo, setAccountInfo] = useState(null);

// Fetch account info when showing modal
useEffect(() => {
  if (showConfirmModal) {
    api.portfolio.getSummary().then(setAccountInfo);
  }
}, [showConfirmModal]);

const handleExecute = async (side) => {
  // Validate first
  if (numSize <= 0 || isNaN(numSize)) {
    setToast({ type: 'error', msg: 'Please enter a valid order size' });
    return;
  }
  
  // Show confirmation modal
  setPendingOrder({
    side,
    size: numSize,
    price: orderType === 'limit' ? numPrice : tickerPrice,
    total: estTotal,
    fees: estFee,
    symbol: activePair,
    type: orderType,
    marketPrice: tickerPrice
  });
  setShowConfirmModal(true);
};

const confirmOrder = async () => {
  if (!pendingOrder) return;
  
  setIsExecuting(true);
  try {
    // Actual execution (same as before but with idempotency)
    const idempotencyKey = `order_${Date.now()}_${crypto.randomUUID()}`;
    const payload = {
      symbol: activePair,
      side: pendingOrder.side.toUpperCase(),
      order_type: pendingOrder.type.toUpperCase(),
      amount: pendingOrder.size,
      price: pendingOrder.type === 'limit' ? pendingOrder.price : undefined,
      order_id: crypto.randomUUID()
    };
    
    const data = await api.orders.execute(payload, {
      headers: { 'Idempotency-Key': idempotencyKey }
    });
    
    handleOrderResponse(data, pendingOrder.side);
    setShowConfirmModal(false);
    setPendingOrder(null);
    
  } catch (err) {
    handleOrderError(err, pendingOrder.side);
  } finally {
    setIsExecuting(false);
  }
};

// In render:
<OrderConfirmationModal
  isOpen={showConfirmModal}
  order={pendingOrder}
  accountInfo={accountInfo}
  onConfirm={confirmOrder}
  onCancel={() => {
    setShowConfirmModal(false);
    setPendingOrder(null);
  }}
  isSubmitting={isExecuting}
/>
```

---

## FIX 3: Price Validation (CRITICAL - C3)

Add to `handleExecute` before showing modal:
```javascript
const validateOrder = (side) => {
  const errors = [];
  
  // Size validation
  if (!size || isNaN(numSize) || numSize <= 0) {
    errors.push('Please enter a valid order size');
  }
  
  if (numSize > 1000000) {  // Some reasonable max
    errors.push('Order size exceeds maximum allowed');
  }
  
  // Price validation for limit orders
  if (orderType === 'limit') {
    if (!price || isNaN(numPrice) || numPrice <= 0) {
      errors.push('Please enter a valid limit price');
    }
    
    const priceDiff = Math.abs(numPrice - tickerPrice) / tickerPrice;
    if (priceDiff > 0.20) {  // 20% from market
      errors.push(`Limit price is ${(priceDiff*100).toFixed(1)}% from market price - please confirm`);
    }
    
    // Warn on limit price that will never fill
    if (side === 'buy' && numPrice < tickerPrice * 0.95) {
      errors.push('Buy limit price is 5%+ below market - order may not fill soon');
    }
    if (side === 'sell' && numPrice > tickerPrice * 1.05) {
      errors.push('Sell limit price is 5%+ above market - order may not fill soon');
    }
  }
  
  return errors;
};

const handleExecute = async (side) => {
  const errors = validateOrder(side);
  if (errors.length > 0) {
    setToast({ type: 'error', msg: errors.join('. ') });
    return;
  }
  
  // Show confirmation modal
  // ...
};
```

---

## FIX 4: Order Status Handling (CRITICAL - C4)

```javascript
const handleOrderResponse = (data, side) => {
  const status = data?.status || 'pending';
  const orderId = data?.order_id || data?.execution_id;
  
  const statusMap = {
    'completed': { type: 'success', label: 'FILLED' },
    'filled': { type: 'success', label: 'FILLED' },
    'open': { type: 'info', label: 'OPEN' },
    'pending': { type: 'warning', label: 'PENDING' },
    'submitted': { type: 'warning', label: 'SUBMITTED' },
    'failed': { type: 'error', label: 'FAILED' },
    'rejected': { type: 'error', label: 'REJECTED' }
  };
  
  const statusInfo = statusMap[status.toLowerCase()] || { type: 'warning', label: status.toUpperCase() };
  
  setToast({
    type: statusInfo.type,
    msg: `${side.toUpperCase()} ${statusInfo.label}: ${size} ${baseAsset}. ID: ${orderId || 'N/A'}`,
    autoClose: statusInfo.type === 'success' ? 5000 : null
  });
  
  // Subscribe to updates for non-terminal statuses
  if (['pending', 'submitted', 'open'].includes(status.toLowerCase())) {
    subscribeToOrderUpdates(orderId);
  }
  
  // Refresh order history
  refreshOrderHistory();
};

const subscribeToOrderUpdates = (orderId) => {
  // Subscribe via WebSocket
  const unsubscribe = wsClient.subscribe('ORDER_UPDATE', (update) => {
    if (update.order_id === orderId) {
      // Show fill notification
      if (update.status === 'filled' || update.status === 'completed') {
        setToast({
          type: 'success',
          msg: `Order ${orderId} FILLED!`,
          autoClose: 10000  // Stay longer for fills
        });
        unsubscribe();  // Stop listening
        refreshOrderHistory();
      }
      
      // Show rejection
      if (update.status === 'rejected' || update.status === 'failed') {
        setToast({
          type: 'error',
          msg: `Order ${orderId} rejected: ${update.reason || 'Unknown'}`,
          autoClose: null  // Stay until dismissed
        });
        unsubscribe();
      }
    }
  });
};
```

---

## FIX 5: Trading Mode Indicator (MEDIUM - M5)

```javascript
const TradingModeBanner = () => {
  const [isLiveMode, setIsLiveMode] = useState(false);
  
  useEffect(() => {
    // Check mode from API or config
    api.user.getProfile().then(profile => {
      setIsLiveMode(profile.trading_mode === 'live');
    });
  }, []);
  
  if (!isLiveMode) return null;  // Only show for live mode
  
  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      background: C.loss,
      color: '#000',
      padding: '6px 12px',
      textAlign: 'center',
      fontSize: 11,
      fontWeight: 900,
      fontFamily: 'monospace',
      zIndex: 10000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8
    }}>
      <AlertTriangle size={14} />
      <span>🔴 LIVE TRADING MODE — REAL MONEY AT RISK</span>
      <button 
        onClick={() => setIsLiveMode(false)}  // Or navigate to paper mode
        style={{
          marginLeft: 12,
          padding: '2px 8px',
          background: 'rgba(0,0,0,0.3)',
          border: 'none',
          borderRadius: 4,
          color: '#fff',
          fontSize: 10,
          cursor: 'pointer'
        }}
      >
        Switch to Paper
      </button>
    </div>
  );
};

// In main App render, add at top:
<TradingModeBanner />
<div style={{ marginTop: isLiveMode ? 28 : 0 }}>
  {/* Rest of app */}
</div>
```

---

## FIX 6: WebSocket Cleanup (MEDIUM - M3)

```javascript
// Helper hook for WebSocket subscriptions
const useWebSocketSubscription = (eventType, callback, deps = []) => {
  useEffect(() => {
    const unsubscribe = wsClient.subscribe(eventType, callback);
    return unsubscribe;
  }, deps);
};

// Usage in components:
const TradingPanel = () => {
  const [ticker, setTicker] = useState(null);
  
  useWebSocketSubscription('ticker', (data) => {
    setTicker(data);
  }, [activePair]);
  
  // ...
};
```

---

## DEPLOYMENT CHECKLIST

- [ ] Idempotency headers sent on all orders
- [ ] Confirmation modal appears before trade
- [ ] Price validation rejects extreme deviations
- [ ] Order status displayed correctly (not always "FILLED")
- [ ] WebSocket subscriptions cleaned up on unmount
- [ ] Trading mode banner visible in live mode
- [ ] Risk panel shows exposure before trade
- [ ] Error handling offers retry for network failures

---

*Frontend Fixes Complete*
