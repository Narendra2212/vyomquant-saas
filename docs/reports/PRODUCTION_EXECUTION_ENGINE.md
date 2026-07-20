# Production-Grade Execution Engine

**Date:** May 1, 2026

---

## Overview

Built a **production-grade execution engine** with comprehensive order management:

```
DAG Signal → Risk Check → Execution Engine → Order Book → Fill
                  ↓              ↓
             Retry Logic    Latency Sim
                  ↓              ↓
             Error Class    Slippage Model
```

**Features:**
- ✅ Full order lifecycle (created → pending → filled → partial → cancelled)
- ✅ Realistic slippage modeling
- ✅ Order book simulation for backtesting
- ✅ Intelligent retry logic with exponential backoff
- ✅ Exchange error classification and handling
- ✅ Network latency simulation
- ✅ DAG action node integration

---

## Architecture

### Execution Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ORDER LIFECYCLE                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │ CREATED │───▶│ PENDING │───▶│  OPEN   │───▶│ PARTIAL │         │
│  └─────────┘    └─────────┘    └─────────┘    └────┬────┘         │
│       │              │              │               │               │
│       │              │              │               ▼               │
│       │              │              │          ┌─────────┐         │
│       │              │              │          │ FILLED  │         │
│       │              │              │          └─────────┘         │
│       │              │              │                               │
│       │              │              ▼                               │
│       │              │         ┌─────────┐                         │
│       │              └────────▶│CANCELLED│                         │
│       │                        └─────────┘                         │
│       │                                                               │
│       ▼                                                               │
│  ┌─────────┐                                                         │
│  │ REJECTED│                                                         │
│  └─────────┘                                                         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Retry Logic Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     RETRY MECHANISM                                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Error Classification:                                                │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐          │
│  │  Retryable     │  │ Non-Retryable  │  │    Unknown     │          │
│  │                │  │                │  │                │          │
│  │ • Rate Limit   │  │ • Insufficient │  │                │          │
│  │ • Timeout      │  │   Funds        │  │ → Classify     │          │
│  │ • Server Error │  │ • Invalid      │  │   & Log        │          │
│  │ • Network Error│  │   Symbol       │  │                │          │
│  └────────┬───────┘  └────────────────┘  └────────────────┘          │
│           │                                                          │
│           ▼                                                          │
│  Exponential Backoff:                                                │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Attempt 1: 100ms + jitter                                 │    │
│  │  Attempt 2: 200ms + jitter                                 │    │
│  │  Attempt 3: 400ms + jitter                                 │    │
│  │  Attempt 4: 800ms + jitter                                 │    │
│  │  Attempt 5: 1600ms + jitter                                │    │
│  │                                                            │    │
│  │  Max Attempts: 5                                           │    │
│  │  Max Delay: 30 seconds                                     │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Order Book Simulation

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ORDER BOOK SIMULATION                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ASKS (Sell Orders)                                                  │
│  ┌────────────────────────────────────────┐                        │
│  │  Price    │  Size    │  Cumulative     │                        │
│  │  $45,100  │  2.5 BTC │  ←─ Best Ask   │                        │
│  │  $45,150  │  5.0 BTC │                │                        │
│  │  $45,200  │  8.0 BTC │                │                        │
│  │  $45,250  │ 12.0 BTC │                │                        │
│  │    ...    │   ...    │                │                        │
│  └────────────────────────────────────────┘                        │
│                         │                                            │
│                      Spread                                          │
│                         │                                            │
│  ┌────────────────────────────────────────┐                        │
│  │  Price    │  Size    │  Cumulative     │                        │
│  │  $45,090  │  3.0 BTC │  ←─ Best Bid   │                        │
│  │  $45,040  │  6.0 BTC │                │                        │
│  │  $44,990  │  9.0 BTC │                │                        │
│  │  $44,940  │ 15.0 BTC │                │                        │
│  │    ...    │   ...    │                │                        │
│  └────────────────────────────────────────┘                        │
│                                                                      │
│  BIDS (Buy Orders)                                                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘

Fill Logic:
  - Market Order: Walk book until quantity filled
  - Limit Order: Fill if price crosses, partial if insufficient liquidity
  - Stop Order: Trigger at stop price, then execute as market order
```

---

## Core Components

### 1. Order Data Class

```python
@dataclass
class Order:
    id: str                          # UUID
    symbol: str                      # BTCUSDT
    side: OrderSide                  # BUY / SELL
    type: OrderType                  # MARKET / LIMIT / STOP
    quantity: float
    price: Optional[float]
    stop_price: Optional[float]
    
    # Status tracking
    status: OrderStatus              # CREATED → PENDING → FILLED
    filled_quantity: float
    remaining_quantity: float
    avg_fill_price: float
    
    # Timestamps
    created_at: datetime
    submitted_at: Optional[datetime]
    filled_at: Optional[datetime]
    
    # Retry tracking
    attempt_count: int
    last_error: Optional[str]
    last_error_type: Optional[ExchangeErrorType]
    
    fills: List[Dict]               # Individual fill records
```

### 2. Slippage Model

```python
@dataclass
class SlippageModel:
    base_bps: float = 5.0           # 5 basis points base
    size_factor: float = 1.0         # Impact scales with size
    volatility_factor: float = 0.5  # Higher vol = more slippage
    spread_factor: float = 0.3      # % of spread to cross
    noise_std_bps: float = 2.0       # Random variation
    
    def calculate_slippage(
        self,
        order: Order,
        order_book: OrderBook,
        market_volatility: float
    ) -> float
```

**Formula:**
```
Slippage = Base + SizeImpact + VolImpact + SpreadImpact + Noise

Where:
  Base = mid_price × (base_bps / 10000)
  SizeImpact = mid_price × sqrt(size_ratio) / 100
  VolImpact = mid_price × (volatility × vol_factor / 100)
  SpreadImpact = spread × spread_factor
  Noise = random.gauss(0, noise_std_bps / 10000)
```

### 3. Latency Profile

```python
@dataclass
class LatencyProfile:
    network_ms_mean: float = 50.0    # Mean network RTT
    network_ms_std: float = 15.0     # Network variance
    exchange_ms_mean: float = 10.0   # Exchange processing
    queue_ms_per_order: float = 2.0  # Queue depth impact
    
    def get_total_latency_ms(self, queue_depth: int = 0) -> float
```

**Latency Components:**
- Network RTT (round-trip time)
- Exchange processing time
- Queue delay (when exchange is busy)

### 4. Retry Configuration

```python
@dataclass
class RetryConfig:
    max_attempts: int = 5
    base_delay_ms: float = 100.0
    max_delay_ms: float = 30000.0
    exponential_base: float = 2.0
    jitter_fraction: float = 0.2    # 20% jitter
    
    retryable_errors: Set[ExchangeErrorType] = {
        RATE_LIMIT, TIMEOUT, SERVER_ERROR, NETWORK_ERROR
    }
    
    def get_delay_ms(self, attempt: int) -> float
    # Returns: base_delay × (2^attempt) + jitter
```

### 5. Error Classification

```python
class ExchangeErrorType(Enum):
    # Retryable
    RATE_LIMIT = "rate_limit"           # 429 Too Many Requests
    TIMEOUT = "timeout"                 # Network timeout
    SERVER_ERROR = "server_error"       # 5xx errors
    NETWORK_ERROR = "network_error"     # Connection issues
    
    # Non-retryable
    INSUFFICIENT_FUNDS = "insufficient_funds"
    INVALID_SYMBOL = "invalid_symbol"
    INVALID_ORDER = "invalid_order"
    MARKET_CLOSED = "market_closed"
    ORDER_TOO_SMALL = "order_too_small"
    ORDER_TOO_LARGE = "order_too_large"
```

---

## API Endpoints

### Create Order

```http
POST /api/execution/orders
Content-Type: application/json

{
  "symbol": "BTCUSDT",
  "side": "buy",
  "type": "market",
  "quantity": 0.5,
  "price": null,
  "stop_price": null,
  "time_in_force": "gtc"
}

Response:
{
  "id": "uuid",
  "symbol": "BTCUSDT",
  "side": "buy",
  "type": "market",
  "status": "filled",
  "quantity": 0.5,
  "filled_quantity": 0.5,
  "remaining_quantity": 0,
  "avg_fill_price": 45120.50,
  "fills": [
    {
      "quantity": 0.3,
      "price": 45100.00,
      "timestamp": "2026-05-01T14:30:00"
    },
    {
      "quantity": 0.2,
      "price": 45151.25,
      "timestamp": "2026-05-01T14:30:00"
    }
  ]
}
```

### Get Order Status

```http
GET /api/execution/orders/{order_id}

Response:
{
  "id": "uuid",
  "symbol": "BTCUSDT",
  "status": "partial",
  "filled_quantity": 0.3,
  "remaining_quantity": 0.2,
  "fills": [...]
}
```

### Cancel Order

```http
DELETE /api/execution/orders/{order_id}

Response:
{
  "success": true,
  "order": {
    "id": "uuid",
    "status": "cancelled",
    "filled_quantity": 0.2,
    "remaining_quantity": 0.3
  }
}
```

### List Orders

```http
GET /api/execution/orders?active_only=true

Response:
{
  "count": 5,
  "orders": [
    { ... },
    { ... }
  ]
}
```

### Execution Statistics

```http
GET /api/execution/stats

Response:
{
  "total_orders": 1523,
  "filled_orders": 1389,
  "cancelled_orders": 89,
  "rejected_orders": 12,
  "total_fills": 2156,
  "total_volume": 1250.5,
  "avg_latency_ms": 45.2,
  "retry_attempts": 234,
  "failed_orders": 5
}
```

---

## Usage Examples

### Example 1: Basic Order Execution

```python
from backend.execution_engine_production import (
    ProductionExecutionEngine,
    OrderSide,
    OrderType,
    TimeInForce
)

# Create engine (backtest mode)
engine = ProductionExecutionEngine(mode="backtest")

# Create order
order = await engine.create_order(
    symbol="BTCUSDT",
    side=OrderSide.BUY,
    type=OrderType.MARKET,
    quantity=0.5,
    metadata={"signal_strength": 0.85}
)

# Submit with automatic retry
filled_order = await engine.submit_order(order.id)

print(f"Order filled: {filled_order.filled_quantity} @ {filled_order.avg_fill_price}")
print(f"Status: {filled_order.status.value}")
print(f"Latency: {engine.stats['avg_latency_ms']:.2f}ms")
```

### Example 2: Limit Order with Partial Fill

```python
# Create limit order
order = await engine.create_order(
    symbol="ETHUSDT",
    side=OrderSide.SELL,
    type=OrderType.LIMIT,
    quantity=10.0,
    price=3200.00,
    time_in_force=TimeInForce.GTC
)

# Submit
order = await engine.submit_order(order.id)

# Check for partial fill
if order.status == OrderStatus.PARTIAL:
    print(f"Partial fill: {order.filled_quantity}/{order.quantity}")
    print(f"Remaining: {order.remaining_quantity}")
    print(f"Avg price: {order.avg_fill_price}")
    
    # Wait for more fills or cancel
    await asyncio.sleep(60)
    order = engine.get_order(order.id)
    
    if order.remaining_quantity > 0:
        await engine.cancel_order(order.id)
```

### Example 3: Error Handling with Retry

```python
# Simulate exchange errors
async def flaky_exchange_client(**kwargs):
    if random.random() < 0.3:  # 30% failure rate
        raise Exception("500 Internal Server Error")
    return {"filled": kwargs["amount"], "price": 45000}

# Configure retry
from backend.execution_engine_production import RetryConfig

retry_config = RetryConfig(
    max_attempts=5,
    base_delay_ms=100,
    exponential_base=2.0
)

engine = ProductionExecutionEngine(
    mode="live",
    exchange_client=flaky_exchange_client,
    retry_config=retry_config
)

# This will retry on failure
order = await engine.create_order(...)

try:
    filled = await engine.submit_order(order.id)
    print(f"Success after {filled.attempt_count} attempts")
except Exception as e:
    print(f"Failed after {engine.retry_config.max_attempts} attempts: {e}")
```

### Example 4: DAG Action Node Integration

```python
from backend.execution_engine_production import DAGExecutionAdapter
from backend.dag_event_loop import Signal

# Create adapter
engine = ProductionExecutionEngine(mode="backtest")
adapter = DAGExecutionAdapter(engine)

# Execute DAG signal
signal = Signal(
    timestamp=datetime.now(),
    symbol="BTCUSDT",
    action="buy",
    strength=0.85,
    trigger_node="rsi_buy_logic"
)

result = await adapter.execute_signal(
    signal=signal,
    symbol="BTCUSDT",
    price=45000.00,  # Reference price
    quantity=0.5,
    metadata={"dag_node_id": "rsi_buy_logic"}
)

if result["success"]:
    print(f"Executed: {result['total_filled']} @ {result['avg_fill_price']}")
    print(f"Order ID: {result['order']['id']}")
else:
    print(f"Failed: {result['error']}")
```

### Example 5: Bulk Execution of Multiple Signals

```python
# Multiple signals from DAG
signals = [
    (Signal(action="buy", strength=0.9), "BTCUSDT", 45000, 0.5),
    (Signal(action="sell", strength=0.8), "ETHUSDT", 3200, 2.0),
    (Signal(action="buy", strength=0.7), "BNBUSDT", 580, 5.0),
]

# Execute all concurrently
results = await adapter.bulk_execute_signals(signals)

for (signal, symbol, _, _), result in zip(signals, results):
    status = "✅" if result["success"] else "❌"
    print(f"{status} {symbol} {signal.action}: {result.get('total_filled', 0)}")
```

### Example 6: Real-Time Order Monitoring

```python
# Add callbacks for real-time updates
def on_order_update(order):
    print(f"Order {order.id} status: {order.status.value}")

def on_fill(order, fill):
    print(f"Fill: {fill['quantity']} @ {fill['price']}")
    print(f"Total filled: {order.filled_quantity}/{order.quantity}")

engine.add_order_callback(on_order_update)
engine.add_fill_callback(on_fill)

# Execute order (callbacks fire on updates)
order = await engine.create_order(...)
await engine.submit_order(order.id)
```

---

## Slippage Models

### Model 1: Fixed Slippage

```python
fixed_model = SlippageModel(
    base_bps=10.0,      # Always 10 bps
    size_factor=0.0,    # No size impact
    volatility_factor=0.0,
    spread_factor=0.0,
)
# Result: ~0.1% slippage per trade
```

### Model 2: Volume-Weighted

```python
volume_model = SlippageModel(
    base_bps=5.0,
    size_factor=2.0,    # High size impact
    volatility_factor=0.5,
    spread_factor=0.3,
)
# Result: Large orders have more slippage
```

### Model 3: Volatility-Based

```python
vol_model = SlippageModel(
    base_bps=3.0,
    size_factor=1.0,
    volatility_factor=2.0,  # High vol impact
    spread_factor=0.5,
)
# Result: More slippage during volatile periods
```

---

## Backtest vs Live Mode

| Feature | Backtest | Live |
|---------|----------|------|
| Order Book | Simulated | Real |
| Latency | Simulated | Real |
| Fills | Walk book | Exchange API |
| Slippage | Model-based | Actual |
| Retries | Simulated delay | Real delay |
| Errors | Random injection | Actual |

---

## Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `backend/execution_engine_production.py` | ~800 | **NEW** - Production execution engine |
| `main.py` | +3 | Added execution router import & registration |

---

## Status: ✅ COMPLETE

Production-grade execution engine with:
- ✅ Full order lifecycle management
- ✅ Realistic slippage modeling
- ✅ Order book simulation
- ✅ Intelligent retry logic
- ✅ Exchange error classification
- ✅ Latency simulation
- ✅ DAG action node integration
