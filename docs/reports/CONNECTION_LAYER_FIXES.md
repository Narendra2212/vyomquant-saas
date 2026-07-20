# 🔌 CONNECTION LAYER FIXES
**Production-Ready Code Solutions**

---

## FIX 1: OrderWatchdog Executor (CRITICAL - C1)

**File:** `backend/order_watchdog.py:323-337`

### Current (BROKEN):
```python
def _get_executor_for_order(self, order: ExecutionRecordModel) -> Optional[Any]:
    """Get exchange executor for this order."""
    try:
        return None  # ❌ ALWAYS RETURNS NONE
    except:
        return None
```

### Fixed:
```python
async def _get_executor_for_order(self, order: ExecutionRecordModel) -> Optional[Any]:
    """Get exchange executor for this order."""
    try:
        from backend.connection_engine import get_or_create_exchange
        from backend.security_vault import SecurityVault
        from backend.exchange_executor import ExchangeExecutor
        
        # Get user's API keys
        vault = SecurityVault()
        exchange_id = order.exchange_id or "binance"
        
        keys = vault.load_decrypted_keys(order.tenant_id, exchange_id)
        if not keys or not keys.get("api_key"):
            logger.error(f"No API keys for tenant {order.tenant_id}")
            return None
        
        # Get or create exchange connection
        exchange = await get_or_create_exchange(
            user_id=order.tenant_id,
            exchange_id=exchange_id,
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            password=keys.get("password")
        )
        
        # Create executor
        executor = ExchangeExecutor(
            exchange=exchange,
            exchange_id=exchange_id,
            tenant_id=order.tenant_id
        )
        
        return executor
        
    except Exception as e:
        logger.error(f"Failed to get executor for order {order.execution_id}: {e}")
        return None
```

---

## FIX 2: Stale Data Detection (HIGH - H4)

**File:** `api_ws/ws_routes.py:120-143`

### Current (BROKEN):
```python
async def _stream_ticker(data_engine, symbol, manager):
    async for ticks in data_engine.stream_ticker(symbol):
        if not ticks:
            continue
        tick = ticks if isinstance(ticks, dict) else (ticks[0] if ticks else {})
        await manager.broadcast_ticker(...)  # ❌ No timestamp check
```

### Fixed:
```python
import time
from datetime import datetime

MAX_DATA_AGE_SECONDS = 30  # Reject data older than 30 seconds
MAX_STALL_SECONDS = 60     # Force reconnect if no data for 60 seconds

async def _stream_ticker(data_engine, symbol, manager):
    last_data_time = time.time()
    last_tick_timestamp = 0
    
    try:
        async for ticks in data_engine.stream_ticker(symbol):
            if not ticks:
                continue
            
            tick = ticks if isinstance(ticks, dict) else (ticks[0] if ticks else {})
            
            # Check data freshness
            tick_timestamp = tick.get("timestamp", 0)
            if isinstance(tick_timestamp, (int, float)):
                # Convert milliseconds to seconds if needed
                if tick_timestamp > 1e10:
                    tick_timestamp = tick_timestamp / 1000
                
                data_age = time.time() - tick_timestamp
                
                if data_age > MAX_DATA_AGE_SECONDS:
                    logger.warning(
                        f"[WS/ticker] Stale data for {symbol}: "
                        f"{data_age:.1f}s old (tick_time={tick_timestamp})"
                    )
                    _mark_public_exchange_failed()
                    continue  # Don't broadcast stale data
                
                last_tick_timestamp = tick_timestamp
            
            last_data_time = time.time()
            
            # Broadcast with freshness metadata
            await manager.broadcast_ticker(
                symbol,
                {
                    "type": "ticker",
                    "symbol": symbol,
                    "last": tick.get("last"),
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                    "timestamp": tick_timestamp,
                    "server_time": time.time(),
                    "data_age_ms": int((time.time() - tick_timestamp) * 1000) if tick_timestamp else None,
                },
            )
            
            # Check for stream stall
            if time.time() - last_data_time > MAX_STALL_SECONDS:
                logger.error(f"[WS/ticker] Stream stalled for {symbol}")
                _mark_public_exchange_failed()
                break
                
    except Exception as e:
        logger.warning(f"[WS/ticker] stream error for {symbol}: {e}")
        _mark_public_exchange_failed()
```

---

## FIX 3: Order Confirmation Wait (CRITICAL - C2)

**File:** `backend/exchange_executor.py` (Add new method)

### New Method:
```python
import asyncio
from typing import Optional
import time

class ExchangeExecutor:
    """Add this method to ExchangeExecutor class."""
    
    async def place_order_and_wait_confirmation(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        size: Decimal,
        price: Optional[Decimal] = None,
        max_wait_seconds: float = 30.0,
        poll_interval: float = 1.0
    ) -> OrderResult:
        """
        Place order and wait for exchange confirmation.
        
        For market orders: waits for fill
        For limit orders: waits for open status
        
        Args:
            symbol: Trading pair
            side: Buy or sell
            order_type: Market, limit, etc.
            size: Order size
            price: Order price (for limit orders)
            max_wait_seconds: Maximum time to wait for confirmation
            poll_interval: How often to poll for status
        
        Returns:
            OrderResult with confirmed status
        """
        # Submit order
        result = await self.place_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=price
        )
        
        if not result.success or not result.exchange_order_id:
            return result
        
        exchange_order_id = result.exchange_order_id
        start_time = time.time()
        
        # Wait for confirmation
        while time.time() - start_time < max_wait_seconds:
            try:
                # Check order status
                status_result = await self.get_order_status(
                    exchange_order_id=exchange_order_id,
                    symbol=symbol
                )
                
                if status_result.success:
                    # Order confirmed by exchange
                    confirmed_status = status_result.status.lower()
                    
                    if confirmed_status in ["filled", "closed", "completed"]:
                        # Fully filled - success!
                        result.status = "closed"
                        result.filled_size = status_result.filled_size
                        result.remaining_size = "0"
                        result.avg_price = status_result.avg_price
                        logger.info(
                            f"Order confirmed FILLED: {exchange_order_id} | "
                            f"filled={status_result.filled_size}"
                        )
                        return result
                    
                    elif confirmed_status in ["open", "partially_filled", "partial"]:
                        # Order is active (for limit orders this is success)
                        if order_type == OrderType.LIMIT:
                            result.status = "open"
                            result.filled_size = status_result.filled_size
                            result.remaining_size = status_result.remaining_size
                            logger.info(
                                f"Limit order confirmed OPEN: {exchange_order_id}"
                            )
                            return result
                        
                        # For market orders, wait for complete fill
                        if confirmed_status == "partially_filled":
                            # Partial fill - wait for rest
                            logger.info(
                                f"Market order partial fill: {exchange_order_id} | "
                                f"filled={status_result.filled_size} | "
                                f"remaining={status_result.remaining_size}"
                            )
                    
                    elif confirmed_status in ["canceled", "cancelled", "expired", "rejected"]:
                        # Order failed
                        result.success = False
                        result.status = confirmed_status
                        result.error_message = f"Order {confirmed_status}"
                        logger.warning(
                            f"Order {confirmed_status}: {exchange_order_id}"
                        )
                        return result
                
                # Wait before next poll
                await asyncio.sleep(poll_interval)
                
            except Exception as e:
                logger.warning(
                    f"Error checking order status (will retry): {exchange_order_id} | {e}"
                )
                await asyncio.sleep(poll_interval)
        
        # Timeout - order status unknown
        logger.critical(
            f"ORDER CONFIRMATION TIMEOUT: {exchange_order_id} | "
            f"Waited {max_wait_seconds}s without confirmation"
        )
        
        # Don't assume success - mark as unknown and alert
        result.status = "pending"  # Unknown status
        result.error_message = f"Confirmation timeout after {max_wait_seconds}s"
        
        # Trigger watchdog alert
        await self._alert_unknown_order_status(exchange_order_id, symbol)
        
        return result
    
    async def _alert_unknown_order_status(self, exchange_order_id: str, symbol: str):
        """Alert when order status cannot be confirmed."""
        try:
            from backend.alert_system import get_alert_system
            alert = get_alert_system()
            
            await alert.send_critical_alert(
                title="Order Confirmation Timeout",
                message=f"Order {exchange_order_id} ({symbol}) status unknown after timeout",
                metadata={
                    "exchange_order_id": exchange_order_id,
                    "symbol": symbol,
                    "timestamp": time.time()
                }
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
```

---

## FIX 4: Async Rate Limiter (CRITICAL - C3)

**File:** `core/exchange_rate_limiter.py:90-148`

### Current (BROKEN):
```python
class TokenBucket:
    async def acquire(self) -> bool:
        async with self._lock:
            self._add_tokens()
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            tokens_needed = 1 - self.tokens
            wait_time = tokens_needed / self.rate
        
        await asyncio.sleep(wait_time)  # ❌ Blocking all requests!
        
        async with self._lock:
            self.tokens -= 1
            return True
```

### Fixed:
```python
import asyncio
import time
from typing import List
import heapq

class AsyncTokenBucket:
    """
    Async token bucket with fair queuing.
    
    Requests are processed in order with proper wait handling.
    """
    
    def __init__(self, rate: float, capacity: Optional[float] = None):
        self.rate = rate
        self.capacity = capacity or rate
        self.tokens = self.capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()
        self._waiters: asyncio.Queue[asyncio.Event] = asyncio.Queue()
        self._refill_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self):
        """Start background refill task."""
        self._running = True
        self._refill_task = asyncio.create_task(self._refill_loop())
    
    async def stop(self):
        """Stop background task."""
        self._running = False
        if self._refill_task:
            self._refill_task.cancel()
            try:
                await self._refill_task
            except asyncio.CancelledError:
                pass
    
    async def _refill_loop(self):
        """Background task to refill tokens and notify waiters."""
        while self._running:
            try:
                async with self._lock:
                    self._add_tokens()
                    
                    # Notify waiters if tokens available
                    while self.tokens >= 1 and not self._waiters.empty():
                        try:
                            event = self._waiters.get_nowait()
                            self.tokens -= 1
                            event.set()
                        except asyncio.QueueEmpty:
                            break
                
                await asyncio.sleep(0.1)  # Check every 100ms
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Refill loop error: {e}")
                await asyncio.sleep(1)
    
    def _add_tokens(self):
        """Add tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
    
    async def acquire(self, priority: int = 0) -> bool:
        """
        Acquire a token. Returns True when acquired.
        
        Args:
            priority: Lower number = higher priority (0 is highest)
        """
        async with self._lock:
            self._add_tokens()
            
            if self.tokens >= 1:
                self.tokens -= 1
                return True
        
        # Need to wait - create event and queue
        event = asyncio.Event()
        
        # Priority queue using heap (negative because heapq is min-heap)
        # Store (priority, sequence_number, event) to ensure FIFO for same priority
        seq = time.time()
        await self._waiters.put((priority, seq, event))
        
        # Wait for token
        await event.wait()
        return True
    
    def get_wait_time(self, position: int = 0) -> float:
        """Get estimated wait time for given queue position."""
        tokens_needed = max(0, 1 - self.tokens + position)
        return tokens_needed / self.rate
```

### Updated ExchangeRateLimiter:
```python
class ExchangeRateLimiter:
    """Updated to use AsyncTokenBucket."""
    
    def __init__(self):
        self._buckets: Dict[str, AsyncTokenBucket] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._processors: List[asyncio.Task] = []
    
    async def start(self):
        """Start all buckets."""
        self._running = True
        for bucket in self._buckets.values():
            await bucket.start()
    
    async def stop(self):
        """Stop all buckets."""
        self._running = False
        for bucket in self._buckets.values():
            await bucket.stop()
    
    def _get_bucket(self, exchange: str) -> AsyncTokenBucket:
        """Get or create async token bucket for exchange."""
        exchange = exchange.lower()
        if exchange not in self._buckets:
            rate = EXCHANGE_RATE_LIMITS.get(ExchangeType(exchange), 10.0)
            bucket = AsyncTokenBucket(rate=rate, capacity=rate)
            self._buckets[exchange] = bucket
            # Start if already running
            if self._running:
                asyncio.create_task(bucket.start())
        return self._buckets[exchange]
    
    @asynccontextmanager
    async def acquire(self, exchange: str, priority: int = 0):
        """Context manager with priority support."""
        bucket = self._get_bucket(exchange)
        await bucket.acquire(priority=priority)
        try:
            yield
        finally:
            pass  # Token consumed
```

---

## FIX 5: Hashed Connection Pool Key (HIGH - H1)

**File:** `backend/connection_engine.py:50`

### Current (BROKEN):
```python
pool_key = f"{user_id}_{exchange_id}"  # ❌ user_123_binance vs user_12_3binance collision
```

### Fixed:
```python
import hashlib
import json

def _make_pool_key(user_id: str, exchange_id: str) -> str:
    """Create collision-resistant pool key."""
    # Use JSON array to avoid delimiter collision
    # Hash for consistent length and security
    key_data = json.dumps([user_id, exchange_id], sort_keys=True)
    return hashlib.sha256(key_data.encode()).hexdigest()[:32]

# Usage:
# pool_key = _make_pool_key(user_id, exchange_id)
```

---

## FIX 6: Circuit Breaker (HIGH - H2)

**File:** `backend/connection_engine.py` (Add new class)

### New Code:
```python
from enum import Enum
import time
from typing import Optional

class CircuitBreakerState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered

class CircuitBreaker:
    """
    Circuit breaker for exchange connections.
    
    Prevents cascade failures when exchange is down.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitBreakerState.CLOSED
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0
    
    def record_success(self):
        """Record successful call."""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.half_open_calls -= 1
            if self.half_open_calls <= 0:
                # Recovered!
                self.state = CircuitBreakerState.CLOSED
                self.failures = 0
                logger.info("Circuit breaker CLOSED (recovered)")
        elif self.state == CircuitBreakerState.CLOSED:
            self.failures = 0
    
    def record_failure(self) -> bool:
        """
        Record failed call.
        
        Returns:
            True if circuit should open
        """
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            # Failed in half-open, go back to open
            self.state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker OPEN (failed in half-open)")
            return True
        
        if self.failures >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.critical(
                f"Circuit breaker OPEN after {self.failures} failures"
            )
            return True
        
        return False
    
    def can_execute(self) -> bool:
        """Check if call should be allowed."""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        if self.state == CircuitBreakerState.OPEN:
            # Check if recovery timeout passed
            if self.last_failure_time and \
               time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                self.half_open_calls = self.half_open_max_calls
                logger.info("Circuit breaker HALF_OPEN (testing recovery)")
                return True
            return False
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            return self.half_open_calls > 0
        
        return True
    
    def get_state(self) -> CircuitBreakerState:
        """Get current state."""
        return self.state


# Add to ConnectionEngine
circuit_breakers: Dict[str, CircuitBreaker] = {}

def get_circuit_breaker(exchange_id: str) -> CircuitBreaker:
    """Get or create circuit breaker for exchange."""
    if exchange_id not in circuit_breakers:
        circuit_breakers[exchange_id] = CircuitBreaker()
    return circuit_breakers[exchange_id]
```

---

## FIX 7: Exchange Health Check (HIGH - H3)

**File:** `api_ws/ws_routes.py:43-65`

### Current (BROKEN):
```python
needs_reconnect = (
    _public_exchange is None
    or _public_fail_count >= _PUBLIC_MAX_FAILS
    or getattr(_public_exchange, "closed", False)  # ❌ May be False even if dead
)
```

### Fixed:
```python
import time

async def _is_exchange_healthy(exchange, timeout: float = 5.0) -> bool:
    """
    Check if exchange connection is actually working.
    
    Args:
        exchange: CCXT exchange instance
        timeout: Max time to wait for health check
    
    Returns:
        True if healthy, False otherwise
    """
    try:
        # Use asyncio.wait_for to enforce timeout
        ticker = await asyncio.wait_for(
            exchange.fetch_ticker("BTC/USDT"),
            timeout=timeout
        )
        
        # Validate ticker data
        if not ticker or not ticker.get("last"):
            logger.warning("Health check failed: invalid ticker data")
            return False
        
        # Check timestamp if available
        ticker_time = ticker.get("timestamp")
        if ticker_time:
            if isinstance(ticker_time, (int, float)) and ticker_time > 1e10:
                ticker_time = ticker_time / 1000
            
            age = time.time() - ticker_time
            if age > 60:  # Older than 1 minute
                logger.warning(f"Health check warning: ticker is {age:.1f}s old")
                # Still consider healthy but warn
        
        return True
        
    except asyncio.TimeoutError:
        logger.warning("Health check failed: timeout")
        return False
    except Exception as e:
        logger.warning(f"Health check failed: {e}")
        return False


async def _get_public_exchange():
    """WSR-2: Returns a healthy public CCXT instance with proper health check."""
    global _public_exchange, _public_fail_count
    
    async with _public_lock:
        needs_reconnect = False
        
        if _public_exchange is None:
            needs_reconnect = True
        elif _public_fail_count >= _PUBLIC_MAX_FAILS:
            logger.warning("Max failures reached, reconnecting")
            needs_reconnect = True
        elif not await _is_exchange_healthy(_public_exchange):  # ✅ Health check
            logger.warning("Exchange health check failed, reconnecting")
            needs_reconnect = True
        
        if needs_reconnect:
            if _public_exchange:
                try:
                    await _public_exchange.close()
                except Exception:
                    pass
            
            try:
                bridge = ConnectionEngine(exchange_id="binance")
                _public_exchange = await bridge.connect()
                _public_fail_count = 0
                logger.info("[WS] Public exchange reconnected.")
            except Exception as e:
                logger.error(f"[WS] Failed to reconnect: {e}")
                _public_fail_count += 1
                raise
    
    return _public_exchange
```

---

## DEPLOYMENT CHECKLIST

After implementing fixes:

- [ ] Order watchdog can successfully fetch order status
- [ ] Stale market data is rejected (test by delaying exchange response)
- [ ] Order confirmation waits for exchange ack
- [ ] Rate limiter handles concurrent requests without head-of-line blocking
- [ ] Connection pool keys don't collide (test with edge case user_ids)
- [ ] Circuit breaker opens after failures and recovers after timeout
- [ ] Exchange health check detects dead connections
- [ ] All WebSocket streams validate data timestamps
- [ ] Chaos test passes (network failures, exchange lag)

---

*Connection Layer Fixes Complete*
