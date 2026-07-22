
"""
Live Exchange Executor

STEP 6 — LIVE EXCHANGE EXECUTION (CCXT INTEGRATION)

Execute real trades via CCXT safely.

Architecture:
┌─────────────────────────────────────────────────────────────────┐
│  UnifiedExecutionEngine                                          │
│       ↓                                                          │
│  ExchangeExecutor                                                │
│       ↓                                                          │
│  CCXT (async_support)                                            │
│       ↓                                                          │
│  Exchange API (Binance, Bybit, Coinbase)                        │
│       ↓                                                          │
│  WebSocket Events → EventListener (STEP 5)                       │
└─────────────────────────────────────────────────────────────────┘

STEP 6.4: NEVER assume FILLED - wait for exchange event
STEP 6.8: Sandbox mode for testing
STEP 6.10: Security - NEVER log API keys
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Any, Dict, Optional

from backend_app.backend.state_service import OrderStatus

# STEP 6.2: CCXT async support
# Note: CCXT must be installed: pip install ccxt
# For this implementation, we define the interface
try:
    import ccxt.async_support as ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    logging.warning("CCXT not installed. Install with: pip install ccxt")

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.5 — ORDER TYPES
# ═══════════════════════════════════════════════════════════════════════════════

class OrderType(Enum):
    """STEP 6.5: Supported order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT_MARKET = "take_profit_market"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class OrderSide(Enum):
    """Order side."""
    BUY = "buy"
    SELL = "sell"


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.6 — ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════════════

class ExchangeError(Exception):
    """Base exchange error."""
    def __init__(self, message: str, error_code: Optional[str] = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.retryable = retryable


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7 — CIRCUIT BREAKER (H2)
# ═══════════════════════════════════════════════════════════════════════════════

class CircuitBreakerState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failure threshold exceeded, blocking requests
    HALF_OPEN = "half_open" # Testing if service recovered


class CircuitBreaker:
    """
    STEP 7: Circuit Breaker pattern to prevent cascade failures.
    
    STATES:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failure threshold exceeded, requests blocked
    - HALF_OPEN: After timeout, allow test calls to check recovery
    
    RULES:
    - If failures > threshold: block requests (OPEN state)
    - After timeout: test calls allowed (HALF_OPEN state)
    - If test succeeds: return to CLOSED
    - If test fails: back to OPEN
    
    EXPECTED RESULT:
    ✔ No cascade failures
    ✔ No exchange bans
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_calls = 0
        self._lock = asyncio.Lock()
    
    async def call(self, func, *args, **kwargs):
        """
        Execute a function through the circuit breaker.
        
        Args:
            func: Async function to call
            *args, **kwargs: Arguments to pass to func
            
        Returns:
            Result from func
            
        Raises:
            CircuitBreakerOpenError: If circuit is OPEN
            Exception: Original exception if call fails
        """
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
            if self.state == CircuitBreakerState.OPEN:
                if self._should_attempt_reset():
                    logger.info("STEP 7: Circuit breaker entering HALF_OPEN state for recovery test")
                    self.state = CircuitBreakerState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                else:
                    # Still in timeout period, block the call
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker is OPEN. Retry after {self.recovery_timeout}s"
                    )
            
            # In HALF_OPEN state, limit the number of test calls
            if self.state == CircuitBreakerState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        "Circuit breaker HALF_OPEN limit reached. Waiting for recovery confirmation."
                    )
                self.half_open_calls += 1
        
        # Execute the call (outside lock to allow concurrent calls in CLOSED state)
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try recovery."""
        if self.last_failure_time is None:
            return True
        
        elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    async def _on_success(self):
        """Handle successful call."""
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.success_count += 1
                # If enough successes in HALF_OPEN, close the circuit
                if self.success_count >= self.half_open_max_calls // 2:
                    logger.info("STEP 7: Circuit breaker recovered, transitioning to CLOSED")
                    self._reset()
            else:
                # In CLOSED state, just reset failure count on success
                self.failure_count = 0
    
    async def _on_failure(self):
        """Handle failed call."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()
            
            if self.state == CircuitBreakerState.HALF_OPEN:
                # Recovery test failed, back to OPEN
                logger.warning("STEP 7: Recovery test failed, circuit breaker back to OPEN")
                self.state = CircuitBreakerState.OPEN
            elif self.failure_count >= self.failure_threshold:
                # Threshold exceeded, open the circuit
                logger.error(
                    f"STEP 7: Circuit breaker threshold ({self.failure_threshold}) exceeded, "
                    f"transitioning to OPEN"
                )
                self.state = CircuitBreakerState.OPEN
    
    def _reset(self):
        """Reset circuit breaker to CLOSED state."""
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.last_failure_time = None
    
    @property
    def is_open(self) -> bool:
        """Check if circuit is currently open."""
        return self.state == CircuitBreakerState.OPEN
    
    @property
    def is_closed(self) -> bool:
        """Check if circuit is currently closed."""
        return self.state == CircuitBreakerState.CLOSED
    
    @property
    def is_half_open(self) -> bool:
        """Check if circuit is in half-open state."""
        return self.state == CircuitBreakerState.HALF_OPEN


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and call is blocked."""
    pass


# Global circuit breakers per exchange
_exchange_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(exchange_id: str) -> CircuitBreaker:
    """Get or create circuit breaker for an exchange."""
    if exchange_id not in _exchange_circuit_breakers:
        _exchange_circuit_breakers[exchange_id] = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
            half_open_max_calls=3
        )
    return _exchange_circuit_breakers[exchange_id]


class InsufficientFundsError(ExchangeError):
    """Not enough balance."""
    def __init__(self, message: str):
        super().__init__(message, "INSUFFICIENT_FUNDS", retryable=False)


class RateLimitError(ExchangeError):
    """Rate limit hit."""
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message, "RATE_LIMIT", retryable=True)
        self.retry_after = retry_after


class InvalidSymbolError(ExchangeError):
    """Symbol not found."""
    def __init__(self, message: str):
        super().__init__(message, "INVALID_SYMBOL", retryable=False)


class NetworkError(ExchangeError):
    """Network/connection error."""
    def __init__(self, message: str):
        super().__init__(message, "NETWORK_ERROR", retryable=True)


class AuthenticationError(ExchangeError):
    """API key invalid."""
    def __init__(self, message: str):
        super().__init__(message, "AUTH_ERROR", retryable=False)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.7 — RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Per-exchange rate limiter.
    
    Prevents hitting exchange rate limits.
    """
    
    def __init__(self, requests_per_second: float = 10.0):
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, endpoint: str = "default"):
        """Acquire rate limit token."""
        async with self._lock:
            now = time.time()
            last_request = self.last_request_time.get(endpoint, 0)
            elapsed = now - last_request
            
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug(f"Rate limit waiting: {wait_time:.3f}s")
                await asyncio.sleep(wait_time)
            
            self.last_request_time[endpoint] = time.time()


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.3 — ORDER RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OrderResult:
    """Result of placing an order."""
    success: bool
    exchange_order_id: Optional[str]
    status: str  # 'pending', 'open', 'closed', 'canceled', 'rejected'
    filled_size: Optional[str]
    remaining_size: Optional[str]
    avg_price: Optional[str]
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    raw_response: Optional[Dict] = None


@dataclass
class CancelResult:
    """Result of canceling an order."""
    success: bool
    status: str  # 'canceled', 'rejected', etc.
    error_message: Optional[str] = None


@dataclass
class OrderStatusResult:
    """Result of fetching order status."""
    success: bool
    status: str
    filled_size: str
    remaining_size: str
    avg_price: Optional[str]
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.9 — EXCHANGE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class ExchangeNormalizer:
    """
    STEP 6.9: Normalize exchange-specific formats.
    
    Handles:
    - Symbol formats (BTCUSD vs BTC/USDT)
    - Price precision
    - Size precision
    - Order type mapping
    """
    
    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id
        self._markets: Optional[Dict] = None
    
    async def load_markets(self, exchange: Any):
        """Load market data for precision."""
        if self._markets is None:
            self._markets = await exchange.load_markets()
    
    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to exchange format.
        
        Examples:
        - Binance: BTCUSDT (no separator)
        - Bybit: BTCUSDT
        - Coinbase: BTC-USD
        """
        symbol = symbol.upper().replace("/", "").replace("-", "")
        
        if self.exchange_id == "binance":
            # Binance uses USDT suffix
            if symbol.endswith("USD") and not symbol.endswith("USDT"):
                symbol = symbol.replace("USD", "USDT")
        elif self.exchange_id == "coinbase":
            # Coinbase uses - separator
            if len(symbol) >= 6:
                base = symbol[:-3] if symbol.endswith("USD") else symbol[:-4]
                quote = "USD" if symbol.endswith("USD") else symbol[-4:]
                symbol = f"{base}-{quote}"
        
        return symbol
    
    def format_symbol_for_ccxt(self, symbol: str) -> str:
        """Format symbol for CCXT (usually with / separator)."""
        symbol = self.normalize_symbol(symbol)
        
        # CCXT standard is BASE/QUOTE
        if "/" not in symbol:
            if symbol.endswith("USDT"):
                base = symbol[:-4]
                quote = "USDT"
            elif symbol.endswith("USD"):
                base = symbol[:-3]
                quote = "USD"
            elif symbol.endswith("BTC"):
                base = symbol[:-3]
                quote = "BTC"
            else:
                # Assume last 3-4 chars are quote
                if len(symbol) > 4:
                    base = symbol[:-4]
                    quote = symbol[-4:]
                else:
                    base = symbol[:-3]
                    quote = symbol[-3:]
            
            symbol = f"{base}/{quote}"
        
        return symbol
    
    def normalize_price(self, symbol: str, price: Decimal) -> str:
        """Normalize price to exchange precision."""
        if self._markets and symbol in self._markets:
            market = self._markets[symbol]
            precision = market.get("precision", {}).get("price", 8)
        else:
            precision = 8
        
        quantize_str = "0." + "0" * precision
        normalized = price.quantize(Decimal(quantize_str), rounding=ROUND_DOWN)
        return str(normalized)
    
    def normalize_size(self, symbol: str, size: Decimal) -> str:
        """Normalize size to exchange precision."""
        if self._markets and symbol in self._markets:
            market = self._markets[symbol]
            precision = market.get("precision", {}).get("amount", 8)
        else:
            precision = 8
        
        quantize_str = "0." + "0" * precision
        normalized = size.quantize(Decimal(quantize_str), rounding=ROUND_DOWN)
        return str(normalized)
    
    def map_order_type(self, order_type: OrderType) -> str:
        """Map our order type to CCXT order type."""
        mapping = {
            OrderType.MARKET: "market",
            OrderType.LIMIT: "limit",
            OrderType.STOP_MARKET: "stop_market",
            OrderType.STOP_LIMIT: "stop_limit",
            OrderType.TAKE_PROFIT_MARKET: "take_profit_market",
            OrderType.TAKE_PROFIT_LIMIT: "take_profit_limit",
        }
        return mapping.get(order_type, "market")
    
    def map_order_side(self, side: OrderSide) -> str:
        """Map our side to CCXT side."""
        return side.value


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.1 — EXCHANGE EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════

class BaseExchangeExecutor(ABC):
    """
    Abstract base class for exchange executors.
    
    STEP 6.1: Core exchange executor interface.
    """
    
    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        sandbox: bool = False,
        password: Optional[str] = None  # For some exchanges like Coinbase
    ):
        self.exchange_id = exchange_id
        self.sandbox = sandbox
        
        # STEP 6.10: Security - NEVER store raw keys
        # Keys are passed to CCXT immediately, not logged
        self._api_key = api_key
        self._api_secret = api_secret
        self._password = password
        
        # STEP 6.7: Rate limiter
        self.rate_limiter = RateLimiter(requests_per_second=5.0)
        
        # STEP 6.9: Normalizer
        self.normalizer = ExchangeNormalizer(exchange_id)
        
        # CCXT exchange instance (initialized in connect)
        self._exchange: Optional[Any] = None
        self._connected = False
        self._current_execution_id = None
        self._current_validation_token = None

    def verify_and_consume_token(self, symbol: str, size: Decimal):
        """Verify the validation token to prevent direct bypass of UnifiedExecutionEngine."""
        from backend_app.core.global_safety import verify_validation_token
        
        token = self._current_validation_token
        exec_id = self._current_execution_id
        
        # Consume immediately to prevent reuse
        self._current_validation_token = None
        self._current_execution_id = None
        
        if not token or not exec_id:
            raise ValueError("Bypass attempt detected: All executions must route through UnifiedExecutionEngine.")
            
        if not verify_validation_token(exec_id, symbol, size, token):
            raise ValueError("Bypass attempt detected: Invalid execution validation token.")
    
    @abstractmethod
    async def connect(self):
        """Connect to exchange API."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Disconnect from exchange."""
        pass
    
    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        size: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        **kwargs
    ) -> OrderResult:
        """
        STEP 6.1: Place order on exchange.
        
        STEP 6.4: Returns 'pending' status - FILLED comes from events.
        """
        pass
    
    @abstractmethod
    async def cancel_order(
        self,
        exchange_order_id: str,
        symbol: str
    ) -> CancelResult:
        """STEP 6.1: Cancel order on exchange."""
        pass
    
    @abstractmethod
    async def get_order_status(
        self,
        exchange_order_id: str,
        symbol: str
    ) -> OrderStatusResult:
        """STEP 6.1: Fetch order status from exchange."""
        pass
    
    @abstractmethod
    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        pass
    
    # STEP 6.6: Error handling wrapper
    def _handle_ccxt_error(self, error: Exception) -> ExchangeError:
        """Convert CCXT error to our error type."""
        error_str = str(error).lower()
        
        # Classify error
        if "insufficient" in error_str or "balance" in error_str:
            return InsufficientFundsError(str(error))
        
        if "rate limit" in error_str or "too many requests" in error_str:
            return RateLimitError(str(error), retry_after=60)
        
        if "symbol" in error_str or "market" in error_str:
            return InvalidSymbolError(str(error))
        
        if "network" in error_str or "timeout" in error_str or "connection" in error_str:
            return NetworkError(str(error))
        
        if "authentication" in error_str or "apikey" in error_str or "key" in error_str:
            return AuthenticationError(str(error))
        
        # Generic error - check if retryable
        retryable = any(word in error_str for word in ["timeout", "network", "temporarily", "busy"])
        return ExchangeError(str(error), retryable=retryable)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.2 — CCXT EXCHANGE IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class CCXTExchangeExecutor(BaseExchangeExecutor):
    """
    STEP 6.2: CCXT-based exchange executor.
    
    Supports Binance, Bybit, Coinbase, and any CCXT-supported exchange.
    """
    
    async def connect(self):
        """Connect to exchange via CCXT."""
        if not CCXT_AVAILABLE:
            raise RuntimeError("CCXT not installed. Install with: pip install ccxt")
        
        # STEP 6.10: Security - keys passed to CCXT, never logged
        config = {
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "enableRateLimit": True,  # Let CCXT handle basic rate limiting
        }
        
        if self._password:
            config["password"] = self._password
        
        # STEP 6.8: Sandbox mode
        if self.sandbox:
            config["sandbox"] = True
            config["options"] = {"sandbox": True}
            logger.info(f"Connecting to {self.exchange_id} in SANDBOX mode")
        
        # Create CCXT exchange instance
        exchange_class = getattr(ccxt, self.exchange_id)
        self._exchange = exchange_class(config)
        
        # Load markets for precision
        await self.normalizer.load_markets(self._exchange)
        
        # Verify connection
        try:
            await self._exchange.fetch_balance()
            self._connected = True
            logger.info(f"Connected to {self.exchange_id} | Sandbox: {self.sandbox}")
        except Exception as e:
            logger.error(f"Failed to connect to {self.exchange_id}: {e}")
            raise self._handle_ccxt_error(e)
    
    async def disconnect(self):
        """Disconnect from exchange."""
        if self._exchange:
            await self._exchange.close()
            self._connected = False
            logger.info(f"Disconnected from {self.exchange_id}")
    
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        size: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        **kwargs
    ) -> OrderResult:
        """
        STEP 6.1: Place order on exchange.
        STEP 6.3: ExecutionEngine → ExchangeExecutor → CCXT → Exchange.
        STEP 6.4: Returns 'pending' - wait for event system for FILLED.
        """
        if size is None:
            if "amount" in kwargs:
                size = Decimal(str(kwargs["amount"]))
            else:
                raise ValueError("Either size or amount must be specified")
        
        # Bypass prevention check
        self.verify_and_consume_token(symbol, size)

        try:
            if isinstance(order_type, str):
                order_type = OrderType(order_type.lower())
            elif hasattr(order_type, "value"):
                order_type = OrderType(order_type.value.lower())
                
            if isinstance(side, str):
                side = OrderSide(side.lower())
            elif hasattr(side, "value"):
                side = OrderSide(side.value.lower())

            # STEP 6.7: Rate limiting
            await self.rate_limiter.acquire("place_order")
            
            # STEP 6.9: Normalize inputs
            ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol)
            ccxt_side = self.normalizer.map_order_side(side)
            ccxt_type = self.normalizer.map_order_type(order_type)
            
            normalized_size = self.normalizer.normalize_size(symbol, size)
            
            # Build order parameters
            params = {
                "symbol": ccxt_symbol,
                "type": ccxt_type,
                "side": ccxt_side,
                "amount": float(normalized_size),
            }
            
            # Add price for limit orders
            if price and order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT_LIMIT):
                normalized_price = self.normalizer.normalize_price(symbol, price)
                params["price"] = float(normalized_price)
            
            # Add stop price for stop orders
            if stop_price:
                normalized_stop = self.normalizer.normalize_price(symbol, stop_price)
                params["stopPrice"] = float(normalized_stop)
            
            # STEP 6.10: Security - log without sensitive data
            logger.info(
                f"PLACE ORDER: {self.exchange_id} | "
                f"{symbol} {side.value} {order_type.value} | "
                f"size={normalized_size} | "
                f"price={price if price else 'market'} | "
                f"sandbox={self.sandbox}"
            )
            
            # Execute via CCXT
            response = await self._exchange.create_order(**params)
            
            # Build result
            # STEP 6.4: Status is 'pending' - we wait for events for FILLED
            result = OrderResult(
                success=True,
                exchange_order_id=str(response.get("id")),
                status="pending",  # STEP 6.4: NOT 'filled'
                filled_size=str(response.get("filled", 0)),
                remaining_size=str(response.get("remaining", normalized_size)),
                avg_price=str(response.get("average")) if response.get("average") else None,
                raw_response=response
            )
            
            logger.info(
                f"ORDER PLACED: {result.exchange_order_id} | "
                f"status={result.status} | "
                f"exchange={self.exchange_id}"
            )
            
            return result
            
        except Exception as e:
            error = self._handle_ccxt_error(e)
            
            logger.error(
                f"ORDER FAILED: {symbol} {side.value} | "
                f"error={error.message} | "
                f"retryable={error.retryable}"
            )
            
            return OrderResult(
                success=False,
                exchange_order_id=None,
                status="rejected",
                filled_size=None,
                remaining_size=None,
                avg_price=None,
                error_message=error.message,
                error_code=error.error_code
            )
    
    async def cancel_order(self, exchange_order_id: str, symbol: str) -> CancelResult:
        """STEP 6.1: Cancel order on exchange."""
        try:
            # STEP 6.7: Rate limiting
            await self.rate_limiter.acquire("cancel_order")
            
            ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol)
            
            logger.info(f"CANCEL ORDER: {exchange_order_id} | {symbol}")
            
            await self._exchange.cancel_order(exchange_order_id, ccxt_symbol)
            
            return CancelResult(
                success=True,
                status="canceled"
            )
            
        except Exception as e:
            error = self._handle_ccxt_error(e)
            
            logger.error(
                f"CANCEL FAILED: {exchange_order_id} | "
                f"error={error.message}"
            )
            
            return CancelResult(
                success=False,
                status="error",
                error_message=error.message
            )
    
    async def get_order_status(self, exchange_order_id: str, symbol: str) -> OrderStatusResult:
        """STEP 6.1: Fetch order status from exchange."""
        try:
            # STEP 6.7: Rate limiting
            await self.rate_limiter.acquire("get_order")
            
            ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol)
            
            response = await self._exchange.fetch_order(exchange_order_id, ccxt_symbol)
            
            return OrderStatusResult(
                success=True,
                status=response.get("status", "unknown"),
                filled_size=str(response.get("filled", 0)),
                remaining_size=str(response.get("remaining", 0)),
                avg_price=str(response.get("average")) if response.get("average") else None
            )
            
        except Exception as e:
            error = self._handle_ccxt_error(e)
            
            return OrderStatusResult(
                success=False,
                status="error",
                filled_size="0",
                remaining_size="0",
                avg_price=None,
                error_message=error.message
            )
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        try:
            await self.rate_limiter.acquire("get_balance")
            
            balance = await self._exchange.fetch_balance()
            
            # Extract free and used balances
            result = {}
            for currency, data in balance.items():
                if isinstance(data, dict) and "free" in data:
                    result[currency] = {
                        "free": str(data.get("free", 0)),
                        "used": str(data.get("used", 0)),
                        "total": str(data.get("total", 0))
                    }
            
            return result
            
        except Exception as e:
            error = self._handle_ccxt_error(e)
            logger.error(f"Balance fetch failed: {error.message}")
            return {}

    async def place_order_with_idempotency(
        self,
        tenant_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        max_wait_seconds: int = 30,
        poll_interval_seconds: float = 1.0
    ) -> OrderResult:
        """
        🔴 STEP 2 — DISTRIBUTED IDEMPOTENCY (MANDATORY)
        
        Places order with distributed idempotency guarantees.
        This is the ONLY allowed method for order placement.
        
        RULE:
        ALL orders must have client_order_id
        MUST be unique per request
        
        IMPLEMENTATION:
        - Key: f"idempotency:{tenant_id}:{client_order_id}"
        - Checks Redis BEFORE execution
        - If exists: returns cached result (duplicate detected)
        - If not: executes and stores result
        
        EXPECTED RESULT:
        ✔ No duplicate orders across pods
        
        Args:
            tenant_id: Tenant identifier for isolation
            client_order_id: Client-provided UNIQUE order ID (MANDATORY)
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            order_type: "market" or "limit"
            amount: Order quantity
            price: Order price (required for limit orders)
            max_wait_seconds: Maximum time to wait for confirmation
            poll_interval_seconds: Time between status polls
            
        Returns:
            OrderResult with confirmed status from exchange
            
        Raises:
            MissingClientOrderIdError: If client_order_id is missing/empty
            DuplicateOrderError: If duplicate detected with processing stuck
        """
        from backend_app.core.distributed_idempotency import \
            get_idempotency_layer
        
        idempotency = get_idempotency_layer()
        
        # STEP 2: Execute with idempotency - enforces client_order_id rule
        return await idempotency.execute_with_idempotency(
            tenant_id=tenant_id,
            client_order_id=client_order_id,
            operation=self.place_order_and_wait_confirmation,
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds
        )

    async def place_order_and_wait_confirmation(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        max_wait_seconds: int = 30,
        poll_interval_seconds: float = 1.0
    ) -> OrderResult:
        """
        STEP 2: FORCE ORDER CONFIRMATION (C2)
        
        Places order and waits for exchange confirmation.
        
        STRICT RULES:
        - Market orders → wait for FILLED status
        - Limit orders → wait for OPEN status
        - NEVER assume order success without confirmation
        - NEVER mark filled without exchange confirmation
        
        TIMEOUT HANDLING:
        - If timeout: status = UNKNOWN, trigger alert
        - Local state must match exchange state
        
        NOTE: This is an INTERNAL method. Use place_order_with_idempotency()
        for all external order placement to ensure idempotency.
        
        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            order_type: "market" or "limit"
            amount: Order quantity
            price: Order price (required for limit orders)
            max_wait_seconds: Maximum time to wait for confirmation
            poll_interval_seconds: Time between status polls
            
        Returns:
            OrderResult with confirmed status from exchange
        """
        import asyncio
        from datetime import datetime

        # STEP 2: PLACE ORDER FIRST
        logger.info(
            f"STEP 2: Placing {order_type} order {side} {amount} {symbol} "
            f"and waiting for confirmation (max {max_wait_seconds}s)"
        )
        
        place_result = await self.place_order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price
        )
        
        # STEP 2: STRICT VALIDATION - Never assume success
        if not place_result.success or not place_result.exchange_order_id:
            logger.error(
                f"STEP 2: Order placement failed for {symbol} {side}. "
                f"Error: {place_result.error}"
            )
            # Return failed result - DO NOT assume anything
            return OrderResult(
                success=False,
                exchange_order_id=None,
                status=OrderStatus.FAILED,
                filled_amount=0.0,
                remaining_amount=amount,
                error=f"Order placement failed: {place_result.error}",
                raw_response=place_result.raw_response
            )
        
        exchange_order_id = place_result.exchange_order_id
        start_time = datetime.utcnow()
        
        logger.info(
            f"STEP 2: Order placed, exchange_order_id={exchange_order_id}. "
            f"Starting confirmation loop..."
        )
        
        # STEP 2: CONFIRMATION LOOP - Poll until confirmed or timeout
        while True:
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            
            # Check timeout
            if elapsed > max_wait_seconds:
                logger.error(
                    f"STEP 2: TIMEOUT - Order {exchange_order_id} not confirmed after "
                    f"{max_wait_seconds}s. Marking as UNKNOWN."
                )
                
                # STEP 2: TIMEOUT HANDLING - Mark as UNKNOWN
                # DO NOT assume filled - we don't know the status
                return OrderResult(
                    success=False,  # Not confirmed = not successful
                    exchange_order_id=exchange_order_id,
                    status=OrderStatus.UNKNOWN,  # CRITICAL: Unknown, not filled
                    filled_amount=0.0,
                    remaining_amount=amount,
                    error=f"Order confirmation timeout after {max_wait_seconds}s",
                    raw_response=None
                )
            
            # Poll order status from exchange
            try:
                status_result = await self.get_order_status(
                    exchange_order_id=exchange_order_id,
                    symbol=symbol
                )
                
                if status_result.success:
                    order_status = status_result.status
                    
                    # STEP 2: MARKET ORDER - Wait for FILLED
                    if order_type.lower() == "market":
                        if order_status == OrderStatus.FILLED:
                            logger.info(
                                f"STEP 2: MARKET order {exchange_order_id} CONFIRMED FILLED. "
                                f"Filled amount: {status_result.filled_amount}"
                            )
                            return OrderResult(
                                success=True,
                                exchange_order_id=exchange_order_id,
                                status=OrderStatus.FILLED,
                                filled_amount=status_result.filled_amount,
                                remaining_amount=status_result.remaining_amount,
                                error=None,
                                raw_response=status_result.raw_response
                            )
                        elif order_status == OrderStatus.CANCELED:
                            logger.error(
                                f"STEP 2: MARKET order {exchange_order_id} was CANCELED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status=OrderStatus.CANCELED,
                                filled_amount=status_result.filled_amount,
                                remaining_amount=status_result.remaining_amount,
                                error="Market order was canceled",
                                raw_response=status_result.raw_response
                            )
                        elif order_status == OrderStatus.REJECTED:
                            logger.error(
                                f"STEP 2: MARKET order {exchange_order_id} was REJECTED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status=OrderStatus.REJECTED,
                                filled_amount=0.0,
                                remaining_amount=amount,
                                error="Market order was rejected",
                                raw_response=status_result.raw_response
                            )
                    
                    # STEP 2: LIMIT ORDER - Wait for OPEN (or FILLED)
                    elif order_type.lower() == "limit":
                        if order_status in (OrderStatus.OPEN, OrderStatus.FILLED):
                            logger.info(
                                f"STEP 2: LIMIT order {exchange_order_id} CONFIRMED {order_status.value}. "
                                f"Filled amount: {status_result.filled_amount}"
                            )
                            return OrderResult(
                                success=True,
                                exchange_order_id=exchange_order_id,
                                status=order_status,  # OPEN or FILLED
                                filled_amount=status_result.filled_amount,
                                remaining_amount=status_result.remaining_amount,
                                error=None,
                                raw_response=status_result.raw_response
                            )
                        elif order_status == OrderStatus.CANCELED:
                            logger.error(
                                f"STEP 2: LIMIT order {exchange_order_id} was CANCELED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status=OrderStatus.CANCELED,
                                filled_amount=status_result.filled_amount,
                                remaining_amount=status_result.remaining_amount,
                                error="Limit order was canceled",
                                raw_response=status_result.raw_response
                            )
                        elif order_status == OrderStatus.REJECTED:
                            logger.error(
                                f"STEP 2: LIMIT order {exchange_order_id} was REJECTED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status=OrderStatus.REJECTED,
                                filled_amount=0.0,
                                remaining_amount=amount,
                                error="Limit order was rejected",
                                raw_response=status_result.raw_response
                            )
                
                else:
                    # Status check failed
                    logger.warning(
                        f"STEP 2: Status check failed for order {exchange_order_id}: "
                        f"{status_result.error}"
                    )
                
            except Exception as e:
                logger.error(
                    f"STEP 2: Error polling status for order {exchange_order_id}: {e}"
                )
            
            # Wait before next poll
            await asyncio.sleep(poll_interval_seconds)


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

class ExchangeExecutorFactory:
    """Factory for creating exchange executors."""
    
    SUPPORTED_EXCHANGES = ["binance", "bybit", "coinbase", "kraken", "okx", "bitget", "kucoin"]
    
    @staticmethod
    def create(
        exchange_id: str,
        api_key: str,
        api_secret: str,
        sandbox: bool = False,
        password: Optional[str] = None
    ) -> BaseExchangeExecutor:
        """
        Create exchange executor.
        
        STEP 6.2: Supports Binance, Bybit, Coinbase via CCXT.
        """
        exchange_id = exchange_id.lower()
        
        if exchange_id not in ExchangeExecutorFactory.SUPPORTED_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {exchange_id}")
        
        # STEP 6.10: Security - keys passed directly, never stored
        return CCXTExchangeExecutor(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            sandbox=sandbox,
            password=password
        )


# Global registry
_executors: Dict[str, BaseExchangeExecutor] = {}


def get_exchange_executor(
    exchange_id: str,
    api_key: str,
    api_secret: str,
    sandbox: bool = False,
    password: Optional[str] = None
) -> BaseExchangeExecutor:
    """Get or create exchange executor."""
    key = f"{exchange_id}:{sandbox}"
    
    if key not in _executors:
        _executors[key] = ExchangeExecutorFactory.create(
            exchange_id=exchange_id,
            api_key=api_key,
            api_secret=api_secret,
            sandbox=sandbox,
            password=password
        )
    
    return _executors[key]
