
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
STEP 6.11: UNKNOWN state reconciliation
"""
import asyncio
import hashlib
import json
import logging
import threading
import time
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

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


def _is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV", "development").lower() == "production"


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
# Per-exchange-id creation lock for double-checked initialisation.
# threading.Lock is used (not asyncio.Lock) because get_circuit_breaker is a
# plain sync function — it cannot await — matching the pattern in
# core/auth_middleware.py::_get_jwks_client().
_circuit_breaker_lock = threading.Lock()


def get_circuit_breaker(exchange_id: str) -> CircuitBreaker:
    """Get or create the circuit breaker for *exchange_id*.

    Uses double-checked locking so that:
    - Steady-state calls (breaker already exists) take no lock overhead.
    - Concurrent cold-start calls produce exactly one CircuitBreaker instance
      per exchange, preventing split failure-state across two objects.
    """
    if exchange_id not in _exchange_circuit_breakers:
        with _circuit_breaker_lock:
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
    Per-exchange, per-endpoint rate limiter.

    Prevents hitting exchange rate limits while allowing fully concurrent
    token acquisition across *different* endpoints on the same exchange.

    Design:
        Each endpoint key ("place_order", "cancel_order", …) owns an
        independent asyncio.Lock stored in ``_endpoint_locks``.  Sleeping
        to respect the rate limit for one endpoint therefore never blocks
        a coroutine that is trying to acquire a token for a different
        endpoint — they hold independent locks and share no critical
        section during the sleep phase.

        The sleep itself still occurs **inside** the per-endpoint lock so
        there is no TOCTOU race: only one coroutine per endpoint can be
        computing or sleeping at a time, guaranteeing ``last_request_time``
        is always updated atomically relative to the next waiter.
    """

    def __init__(self, requests_per_second: float = 10.0):
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time: Dict[str, float] = {}
        # Per-endpoint locks: sleeping on endpoint A never blocks endpoint B.
        # defaultdict is safe here because asyncio is single-threaded —
        # __getitem__ is synchronous and non-interruptible within the event loop.
        self._endpoint_locks: defaultdict = defaultdict(asyncio.Lock)

    async def acquire(self, endpoint: str = "default"):
        """
        Acquire a rate-limit token for *endpoint*.

        Waits the minimum required interval since the last call on this
        specific endpoint.  Calls to *different* endpoints proceed
        concurrently and are never blocked by each other's wait.
        """
        async with self._endpoint_locks[endpoint]:
            now = time.time()
            last_request = self.last_request_time.get(endpoint, 0.0)
            elapsed = now - last_request

            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug(f"Rate limit waiting [{endpoint}]: {wait_time:.3f}s")
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
    #: The venue's own identifier for the order, when the caller did not already know it.
    #:
    #: TRADE-07. `get_order_status` is CALLED with the venue order id, so it had no reason
    #: to return one. `find_order_by_client_order_id` is the opposite: it is called with
    #: the id WE assigned and its entire purpose is to discover the venue's. Recovery is
    #: forbidden from recording a fill without it, so it has to travel in the result.
    #:
    #: Additive with a default, so every existing construction of this dataclass is
    #: unaffected.
    venue_order_id: Optional[str] = None


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

    # ─────────────────────────────────────────────────────────────────────────
    # PRECISION
    # ─────────────────────────────────────────────────────────────────────────
    #
    # CCXT reports `market["precision"]["price"|"amount"]` under TWO conventions, and
    # which one applies is a property of the venue, not of this code:
    #
    #   DECIMAL_PLACES  -> an integer count, e.g. 2 meaning "two decimal places"
    #   TICK_SIZE       -> the step itself, e.g. 0.01 or 1e-05
    #
    # Binance, Kraken, Coinbase and Bitfinex are TICK_SIZE venues. The previous
    # implementation assumed DECIMAL_PLACES unconditionally and built its quantizer as
    # `"0." + "0" * precision`, so a TICK_SIZE value raised
    # `TypeError: can't multiply sequence by non-int of type 'float'` — reproduced by
    # execution during the pre-launch audit — on the LIVE order path, inside
    # `place_order`, after the idempotency claim had already been taken.
    #
    # The resolution rules are taken from `backend/paper/paper_simulator._decimal_places`,
    # which already solved this for the paper path, including its refusal to guess at the
    # ambiguous value 1 (one place under DECIMAL_PLACES, none under TICK_SIZE).

    @staticmethod
    def _decimal_places(value: Any) -> Optional[int]:
        """A CCXT precision figure as a count of decimal places, or ``None`` if unreadable.

        * ``0``                    -> 0 places, under either convention.
        * ``0 < v < 1``            -> a tick size; the count is its decimal exponent, so
                                      ``0.01`` is 2 and ``1e-08`` is 8.
        * ``v > 1`` and integral   -> already a count of places.
        * ``v == 1``               -> ambiguous, so ``None``. See the block comment: one
                                      convention means one place and the other means none,
                                      and the two differ in which orders the venue accepts.
        * anything else            -> ``None``.

        A ``float`` is read through ``Decimal(repr(value))``, not ``Decimal(value)``: CCXT
        delivers these as JSON numbers, and ``repr`` is the shortest text that round-trips,
        which makes ``0.01`` exactly one hundredth rather than the binary value beside it.
        """
        if value is None or isinstance(value, bool):
            return None
        try:
            dec = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
        except Exception:
            return None

        if not dec.is_finite() or dec < 0:
            return None
        if dec == 0:
            return 0
        if dec == 1:
            return None  # ambiguous — refuse rather than guess
        if dec < 1:
            return -dec.normalize().as_tuple().exponent
        if dec == dec.to_integral_value():
            return int(dec)
        return None

    def _market_for(self, symbol: str) -> Optional[Dict]:
        """The venue's market record for *symbol*, tolerating either spelling.

        ``self._markets`` is keyed by CCXT's unified symbol (``"BTC/USDT"``), but
        ``place_order`` passes the RAW symbol it was given (``"BTCUSDT"``). The lookup
        therefore missed on every single order and both normalizers fell through to their
        8-decimal-place default — silently, so the orders were accepted by this code and
        then rejected or re-rounded by the venue. Verified by execution during the
        pre-launch audit: the raw spelling produced ``0.12345678`` where the unified
        spelling produced the venue's real ``0.123``.
        """
        if not self._markets:
            return None
        if symbol in self._markets:
            return self._markets[symbol]
        unified = self.format_symbol_for_ccxt(symbol)
        return self._markets.get(unified)

    def _precision_places(self, symbol: str, field: str) -> Optional[int]:
        """Decimal places for ``price``/``amount`` on *symbol*, or ``None`` if unstated."""
        market = self._market_for(symbol)
        if not market:
            return None
        precision = (market.get("precision") or {}).get(field)
        return self._decimal_places(precision)
    
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
        """Format symbol for CCXT (standard BASE/QUOTE separator)."""
        clean = symbol.upper().replace("/", "").replace("-", "")
        
        if clean.endswith("USDT"):
            base = clean[:-4]
            quote = "USDT"
        elif clean.endswith("USDC"):
            base = clean[:-4]
            quote = "USDC"
        elif clean.endswith("USD"):
            base = clean[:-3]
            quote = "USD"
        elif clean.endswith("BTC"):
            base = clean[:-3]
            quote = "BTC"
        elif clean.endswith("ETH"):
            base = clean[:-3]
            quote = "ETH"
        else:
            base = clean[:-3] if len(clean) > 3 else clean
            quote = clean[-3:] if len(clean) > 3 else "USDT"
        
        return f"{base}/{quote}"
    
    def _quantize_down(self, value: Decimal, places: Optional[int]) -> Decimal:
        """Truncate *value* to *places* decimals, defaulting to 8 when unstated.

        ``ROUND_DOWN`` throughout, and that direction is deliberate: truncating a size can
        only ever make an order smaller than intended, whereas rounding up can exceed the
        available balance and can breach the position limit that was checked against the
        pre-rounding figure.
        """
        effective = 8 if places is None else places
        if effective <= 0:
            return value.quantize(Decimal("1"), rounding=ROUND_DOWN)
        return value.quantize(Decimal("0." + "0" * effective), rounding=ROUND_DOWN)

    def normalize_price(self, symbol: str, price: Decimal) -> str:
        """Normalize price to the venue's stated price precision, truncating."""
        return str(self._quantize_down(price, self._precision_places(symbol, "price")))

    def normalize_size(self, symbol: str, size: Decimal) -> str:
        """Normalize size to the venue's stated amount precision, truncating.

        Where the venue publishes an amount STEP (``precision.amount`` under the
        TICK_SIZE convention) the result is also snapped down to a whole multiple of that
        step. Truncating to N decimals is not the same as snapping to a step: with a step
        of ``0.003``, ``0.004`` truncates to ``0.004`` and is then rejected by the venue.
        """
        places = self._precision_places(symbol, "amount")
        normalized = self._quantize_down(size, places)

        step = self.amount_step(symbol)
        if step and step > 0:
            steps = (normalized / step).to_integral_value(rounding=ROUND_DOWN)
            normalized = (steps * step).quantize(normalized)

        return str(normalized)

    def amount_step(self, symbol: str) -> Optional[Decimal]:
        """The venue's amount step for *symbol*, when it publishes one as a tick size."""
        market = self._market_for(symbol)
        if not market:
            return None
        raw = (market.get("precision") or {}).get("amount")
        if raw is None or isinstance(raw, bool):
            return None
        try:
            dec = Decimal(repr(raw)) if isinstance(raw, float) else Decimal(str(raw))
        except Exception:
            return None
        # Only a sub-unit figure is a step; an integer is a count of decimal places.
        return dec if dec.is_finite() and 0 < dec < 1 else None

    def market_minimums(self, symbol: str) -> Dict[str, Optional[Decimal]]:
        """The venue's ``limits.amount.min`` and ``limits.cost.min`` for *symbol*.

        Returned as ``None`` where the venue does not state a figure, so a caller can tell
        "no minimum published" from "a minimum of zero" and does not substitute one.
        """
        market = self._market_for(symbol) or {}
        limits = market.get("limits") or {}

        def _read(section: str) -> Optional[Decimal]:
            raw = (limits.get(section) or {}).get("min")
            if raw is None or isinstance(raw, bool):
                return None
            try:
                return Decimal(repr(raw)) if isinstance(raw, float) else Decimal(str(raw))
            except Exception:
                return None

        return {"min_amount": _read("amount"), "min_cost": _read("cost")}
    
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
# SAFETY BLOCK — VENUES WITHHELD FROM ORDER ROUTING BY AN EXPLICIT DECISION
# ═══════════════════════════════════════════════════════════════════════════════
#
# WHY THIS LIVES HERE AND NOT IN THE CAPABILITY REGISTRY.
#
# `core/exchange_capability_registry.py` already carried a `SAFETY_BLOCKED` dict and
# filtered bitget out of `order_routing_allowlisted()`. That made the registry's REPORT
# correct and changed nothing about what could actually be executed: the registry reads
# `SUPPORTED_EXCHANGES` from this module, and nothing in this module ever read the
# registry. So the block was advisory. Three paths still built a bitget executor:
#
#   1. `ExchangeExecutorFactory.create("bitget", ...)` — bitget was in SUPPORTED_EXCHANGES
#   2. `get_exchange_executor(..., exchange_id="bitget")` — delegates to the factory
#   3. `CCXTExchangeExecutor(exchange_id="bitget", ...)` — direct construction, no check
#
# A safety block enforced only at the layer that reports it is not a safety block. The
# authoritative list therefore lives beside the code that constructs executors, and the
# registry imports it from here — which also matches the dependency direction that
# already existed (registry -> executor), so no import cycle is created.
#
# WHY BITGET.
#
# `ccxt.bitget.set_sandbox_mode` overrides the base implementation and does NOT call
# `super()`:
#
#     def set_sandbox_mode(self, enabled):
#         self.options['sandboxMode'] = enabled
#
# and bitget publishes no `urls['test']`. So arming sandbox mode changes neither the base
# URL nor any header, while `CCXTExchangeExecutor.connect` logs "Connecting to bitget in
# SANDBOX mode" and `place_order` logs `sandbox=True` on every order. The log says
# sandbox; the socket says production. A trader who ticked "Testnet / Sandbox Mode" would
# have placed real orders against a real balance.
#
# Measured, not inferred: staging/p10e_sandbox_probe.txt reports
# `url_changed=False, headers_added=[], options['sandboxMode']=True` for bitget, against
# binance/bybit (base URL swapped to `urls['test']`) and okx (header
# `x-simulated-trading`). Reproduction: tests/exchange_sandbox/
# test_sandbox_routing_contract.py::test_bitget_sandbox_flag_is_inert_and_orders_reach_production
#
# This is distinct from coinbase/kraken/kucoin, which have no sandbox and REFUSE loudly —
# a refusal cannot lose anyone's money. Bitget succeeded silently.
#
# Regression: tests/test_exch01_bitget_routing_is_blocked.py
SAFETY_BLOCKED_EXCHANGES: Dict[str, str] = {
    "bitget": (
        "EXCH-01: arming sandbox mode on bitget changes neither the base URL nor any "
        "header (ccxt.bitget.set_sandbox_mode does not call super(), and bitget "
        "publishes no urls['test']), so an order placed in 'sandbox mode' reaches the "
        "PRODUCTION bitget API with real credentials and a real balance while the "
        "executor logs 'SANDBOX mode'. Withheld from order routing until fail-closed "
        "routing verification is in place."
    ),
}


class ExchangeRoutingBlocked(ValueError):
    """An order was routed to a venue withheld by an explicit safety decision.

    Subclasses ``ValueError`` deliberately. ``ExchangeExecutorFactory.create`` already
    raised ``ValueError`` for an unsupported venue, so every existing caller's error
    handling continues to fail closed on this without modification — while new code can
    still tell "deliberately blocked for a money-safety defect" from "never supported",
    which are different facts that warrant different responses.
    """

    def __init__(self, exchange_id: str, reason: str) -> None:
        self.exchange_id = exchange_id
        self.reason = reason
        self.code = "EXCHANGE_ROUTING_BLOCKED"
        super().__init__(
            f"Exchange '{exchange_id}' is blocked from order routing. {reason}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# EXCH-01 — SANDBOX ENDPOINT VERIFICATION, FAIL CLOSED
# ═══════════════════════════════════════════════════════════════════════════════
#
# THE DEFECT THIS REPLACES.
#
# `connect()` established "sandbox" by SETTING A FLAG and then LOGGING IT:
#
#     if self.sandbox:
#         config["sandbox"] = True
#         config["options"] = {"sandbox": True}
#         logger.info(f"Connecting to {self.exchange_id} in SANDBOX mode")
#
# Nothing checked where the resulting client would actually send. `place_order` then
# logged `sandbox=True` on every order. For bitget that log was false: ccxt's bitget
# `set_sandbox_mode` sets only `options['sandboxMode']` and bitget publishes no
# `urls['test']`, so the client stayed on the production API. Measured:
# `staging/p10exch01_endpoints.txt` reports, for bitget, `urls['apiBackup'] set: False`,
# `headers added: []`, and an UNCHANGED base URL — while `options['sandboxMode']: True`.
#
# Critically, bitget would also pass the obvious "fix": checking that
# `set_sandbox_mode()` did not raise. It does not raise. **Absence of an exception is not
# evidence of routing.** This verifier therefore requires POSITIVE endpoint evidence.
#
# WHY NO NETWORK CALL IS NEEDED.
#
# ccxt's `Exchange.set_sandbox_mode` leaves the proof on the client:
#
#     if enabled:
#         if 'test' in self.urls:
#             self.urls['apiBackup'] = self.urls['api']   # the production endpoint
#             self.urls['api'] = self.urls['test']        # the sandbox endpoint
#         else:
#             raise NotSupported(...)
#
# So a client that genuinely swapped carries `apiBackup` (where it came FROM) alongside
# `api` and `test` (where it is going). Comparing those three proves both that the swap
# happened and that it landed on the venue's OWN DECLARED sandbox endpoint — strictly
# stronger than "the URL changed to something", and decidable entirely offline.
#
# VENUE-AWARE, BECAUSE THE VENUES GENUINELY DIFFER.
#
# Measured on ccxt 4.3.92 across the six approved venues plus bitget:
#
#     binance, bybit    URL_SWAP     apiBackup set, api == test
#     okx               HEADER       url UNCHANGED, header `x-simulated-trading` added
#     coinbase, kraken,
#     kucoin            UNSUPPORTED  constructor raises NotSupported; no sandbox exists
#     bitget            NONE         armed, changed nothing -> the EXCH-01 condition
#
# OKX is why a URL-only rule would be wrong: demo trading shares the production host and
# is selected by a header, so "URL unchanged" is the CORRECT outcome there. A generic
# "something must have changed" rule would be wrong in the other direction — it would
# accept any future venue that happens to mutate an unrelated attribute.
#
# So the mechanism is DECLARED per venue and verified against that declaration. A venue
# absent from the table is `UNKNOWN` and is REFUSED: a new venue must be measured and
# added deliberately, never sandbox-certified by a generic rule.
#
# Regression: tests/test_exch01_sandbox_endpoint_verification.py


class SandboxMechanism(str, Enum):
    """How a venue selects its sandbox. Declared per venue, never inferred."""

    #: `urls['api']` is replaced by `urls['test']`; `urls['apiBackup']` holds production.
    URL_SWAP = "url_swap"
    #: The host is unchanged and a named header selects demo trading (OKX).
    HEADER = "header"
    #: The venue has no sandbox on this ccxt. Requesting one must refuse, not proceed.
    UNSUPPORTED = "unsupported"


#: Per-venue sandbox mechanism and, for `HEADER`, the header that must be present.
#:
#: Derived by measurement (`staging/probe_exch01_sandbox_endpoints.py` ->
#: `staging/exch01_sandbox_endpoint_evidence.json`), not from ccxt's `has` map and not
#: from any capability registry — two of those registries were wrong about exactly this
#: (EXCH-04: coinbase and kucoin were declared `sandbox=True` and both raise).
#:
#: Deliberately scoped to the approved routing venues. Anything else is `UNKNOWN` and
#: refused, so widening this table is a conscious act with a measurement behind it.
SANDBOX_MECHANISMS: Dict[str, Tuple[SandboxMechanism, Optional[str]]] = {
    "binance": (SandboxMechanism.URL_SWAP, None),
    "bybit": (SandboxMechanism.URL_SWAP, None),
    "okx": (SandboxMechanism.HEADER, "x-simulated-trading"),
    "coinbase": (SandboxMechanism.UNSUPPORTED, None),
    "kraken": (SandboxMechanism.UNSUPPORTED, None),
    "kucoin": (SandboxMechanism.UNSUPPORTED, None),
}


class SandboxVerificationFailed(ExchangeError):
    """A sandbox connection could not be PROVEN to have left production.

    Raised, never returned. A returned verdict can be dropped by a caller, and dropping
    this one leaves a client that reports itself as sandbox while addressing the
    production API — which is the defect this class exists to prevent.

    Subclasses :class:`ExchangeError` so existing `except ExchangeError` handling on the
    connect path treats it as a terminal, non-retryable connection failure. It is not
    retryable: nothing about trying again changes which endpoint the venue publishes.
    """

    def __init__(self, exchange_id: str, reason: str) -> None:
        self.exchange_id = exchange_id
        self.reason = reason
        super().__init__(
            f"Sandbox routing for '{exchange_id}' could not be verified: {reason} "
            f"No connection was established.",
            error_code="SANDBOX_UNVERIFIED",
            retryable=False,
        )


@dataclass(frozen=True)
class SandboxVerification:
    """The outcome of verifying one client's endpoint configuration.

    ``verified`` is only ever ``True`` when positive endpoint evidence was found. There
    is no third "assumed" state: a connection is sandbox-verified or it is not.
    """

    exchange_id: str
    verified: bool
    mechanism: Optional[str]
    detail: str
    endpoint: Optional[str] = None
    production_endpoint: Optional[str] = None


def _endpoint_fingerprint(value: Any) -> str:
    """A stable, comparable rendering of a ccxt URL entry.

    `urls['api']` is a `str` for some venues and a nested `dict` of per-section hosts for
    others (binance has a dozen). Sorted JSON makes the comparison order-independent and
    total, so "did this change" cannot be answered differently by two dict orderings.
    """
    return json.dumps(value, sort_keys=True, default=str)


def _pristine_production_endpoint(exchange_id: str) -> Optional[str]:
    """The venue's PRODUCTION endpoint fingerprint, from a client with no sandbox armed.

    WHY THIS EXISTS. `verify_sandbox_endpoint` needs to prove an armed client is not
    addressing production, and until ccxt 4.5.90 it did that by comparing against
    `urls['apiBackup']`, which 4.3.92's `set_sandbox_mode` filled with the production
    endpoint before swapping. 4.5.90 leaves `apiBackup` equal to the TEST urls, so that
    comparison stopped meaning anything and started failing every legitimate sandbox
    connection. Asking a pristine client what production is does not depend on how ccxt
    chooses to use `apiBackup`, and it is what the check always meant.

    COSTS NOTHING AND LEAKS NOTHING. Measured on ccxt 4.5.90 + aiohttp 3.14.4: after
    `cls({})` both `session` and `tcp_connector` are still `None` and no
    `Unclosed client session` warning is emitted at shutdown, so there is no transport to
    close and no credential involved -- the config is empty. ccxt fills `urls` from class
    data in the constructor, so no network call is made either.

    Returns ``None`` rather than raising if the class cannot be built, so the caller
    decides what an unanswerable question means (it treats it as a refusal, because a
    sandbox that cannot be proven is not a sandbox).
    """
    try:
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            return None
        pristine = cls({})
        api = (getattr(pristine, "urls", None) or {}).get("api")
        if api is None:
            return None
        return _endpoint_fingerprint(api)
    except Exception:  # noqa: BLE001 - an unanswerable question is a refusal upstream
        logger.warning(
            "could not determine the production endpoint for %r from a pristine client; "
            "sandbox verification will refuse rather than guess", exchange_id,
        )
        return None


def verify_sandbox_endpoint(
    client: Any,
    exchange_id: str,
    *,
    production_endpoint: Any = None,
) -> SandboxVerification:
    """Prove that *client* addresses a sandbox endpoint, or raise. Offline.

    Reads only locally available client metadata — `urls` and `headers`. Makes no
    network call, so it cannot be defeated by a venue being temporarily reachable, and
    it costs nothing on the connect path.

    Args:
        production_endpoint: the venue's PRODUCTION endpoint, used by the URL_SWAP
            check to prove the armed client has actually left it. Defaults to
            reading it from a pristine client of the same id.

            THIS PARAMETER EXISTS BECAUSE ccxt 4.5.90 STOPPED RETAINING PRODUCTION
            ON THE ARMED CLIENT. Until then this function worked from `client`
            alone: 4.3.92's set_sandbox_mode saved production into
            `urls['apiBackup']` before swapping, so `api != apiBackup` proved the
            move. 4.5.90 leaves `apiBackup` EQUAL TO THE TEST urls -- measured,
            identical for binance, bybit and okx -- so production is no longer on
            the armed client and the question 'did it leave production' cannot be
            answered from it at all. The old check therefore refused every
            legitimate sandbox connection; it failed closed, which is the one thing
            that went right, but it broke testnet trading and DUP-01's harness.

            The default reaches for a pristine client. That alone would have broken
            this function's own design rule, which its test fake states plainly:
            'the verifier must work from `urls` and `headers` alone, and a fake that
            offered more would let a dependency on something else go unnoticed'.
            Reaching for global ccxt state makes a FakeClient's endpoints
            uncomparable against a real venue's, which silently stops 'a swap that
            lands back on production is refused' from being testable AT ALL -- the
            refusal branch would still exist and nothing would exercise it.
            Injecting it keeps that case exercisable with a fake, which is the only
            way the refusal stays honest.

    Raises:
        SandboxVerificationFailed: when the mechanism is unknown, unsupported, or the
            expected endpoint evidence is absent. Every failure path refuses; there is
            no branch that proceeds on incomplete evidence.
    """
    normalized = str(exchange_id or "").strip().lower()
    declared = SANDBOX_MECHANISMS.get(normalized)

    # UNKNOWN. A venue nobody measured must not be sandbox-certified by a generic rule.
    if declared is None:
        raise SandboxVerificationFailed(
            normalized,
            "no sandbox mechanism has been measured for this venue, so there is no "
            "declaration to verify the client against. Measure it with "
            "staging/probe_exch01_sandbox_endpoints.py and add it to "
            "SANDBOX_MECHANISMS before enabling sandbox mode.",
        )

    mechanism, header_name = declared

    # UNSUPPORTED. Refused explicitly rather than letting ccxt's raw NotSupported escape
    # from the constructor, which is both unclassified and reported as a connection fault.
    if mechanism is SandboxMechanism.UNSUPPORTED:
        raise SandboxVerificationFailed(
            normalized,
            "this venue publishes no sandbox endpoint on the installed ccxt, so a "
            "sandbox connection cannot exist. Requesting one is a configuration error, "
            "not a transient failure.",
        )

    urls = getattr(client, "urls", None) or {}
    api_fp = _endpoint_fingerprint(urls.get("api"))
    test_fp = _endpoint_fingerprint(urls.get("test")) if urls.get("test") is not None else None
    # Retained as evidence that set_sandbox_mode ran its swap branch at all. Its VALUE is
    # no longer trusted to be the production endpoint -- see the URL_SWAP block below.
    backup_fp = (
        _endpoint_fingerprint(urls.get("apiBackup")) if "apiBackup" in urls else None
    )

    if mechanism is SandboxMechanism.URL_SWAP:
        # Three conditions, all required. `apiBackup` present proves set_sandbox_mode ran
        # the swap branch; `api == test` proves it landed on the DECLARED sandbox
        # endpoint rather than merely somewhere else; and the third proves it is not on
        # production.
        #
        # THE THIRD CONDITION CHANGED ON ccxt 4.5.90 AND HAD TO, because the fact it used
        # to rest on stopped being true. It was `api != apiBackup`, which worked because
        # 4.3.92's set_sandbox_mode did:
        #
        #     self.urls['apiBackup'] = self.urls['api']   # save PRODUCTION
        #     self.urls['api']       = self.urls['test']  # swap in TEST
        #
        # so `apiBackup` WAS the production endpoint and differing from it meant "we
        # moved". On 4.5.90 `apiBackup` comes out equal to the TEST urls, so
        # `api == apiBackup` for binance and bybit and this check raised
        # SandboxVerificationFailed on a client that had correctly swapped. MEASURED:
        # apiBackup == test_url == armed api, all three identical, for binance, bybit and
        # okx.
        #
        # It failed CLOSED, which is the one thing that went right -- it refused to
        # connect rather than quietly addressing production -- but it refused every
        # legitimate sandbox connection, which broke testnet trading and DUP-01's
        # certification harness outright.
        #
        # So the condition now compares against the venue's REAL production endpoint,
        # taken from a pristine instance of the same class. That is what the check always
        # meant, it does not depend on how ccxt chooses to use `apiBackup`, and it is
        # strictly stronger: the old form only proved "different from whatever apiBackup
        # holds", while this proves "the declared sandbox endpoint is genuinely not the
        # production one". Constructing a pristine client is local and makes no network
        # call, the same way `probe_exch01_sandbox_endpoints` does it.
        if backup_fp is None:
            raise SandboxVerificationFailed(
                normalized,
                "urls['apiBackup'] is absent, so ccxt never performed the sandbox URL "
                "swap — the client is still addressing the production API.",
            )
        if test_fp is None:
            raise SandboxVerificationFailed(
                normalized,
                "the venue declares no urls['test'], so there is no sandbox endpoint to "
                "verify against.",
            )
        if api_fp != test_fp:
            raise SandboxVerificationFailed(
                normalized,
                "the active endpoint does not match the venue's declared sandbox "
                "endpoint, so where this client would send is ambiguous.",
            )
        production_fp = (
            _endpoint_fingerprint(production_endpoint)
            if production_endpoint is not None
            else _pristine_production_endpoint(normalized)
        )
        if production_fp is None:
            raise SandboxVerificationFailed(
                normalized,
                "the venue's production endpoint could not be determined from a pristine "
                "client, so it is impossible to prove this one was swapped away from it.",
            )
        if api_fp == production_fp:
            raise SandboxVerificationFailed(
                normalized,
                "the active endpoint is identical to the venue's production endpoint, so "
                "this client is addressing production despite sandbox mode.",
            )
        # The evidence NAMES ITS SOURCE. Two different facts can satisfy the third
        # condition -- a production endpoint handed in by the caller, or one read from a
        # pristine client -- and a reader of this record has to be able to tell which,
        # because an injected value is only as good as whoever injected it.
        production_source = (
            "a production endpoint supplied by the caller"
            if production_endpoint is not None
            else "the production endpoint of a pristine client of the same venue"
        )
        return SandboxVerification(
            exchange_id=normalized,
            verified=True,
            mechanism=mechanism.value,
            detail=(
                f"base URL replaced by the venue's declared urls['test'] (urls['apiBackup'] "
                f"is present, so ccxt ran its swap branch), and that endpoint differs from "
                f"{production_source}"
            ),
            endpoint=api_fp,
            production_endpoint=production_fp,
        )

    if mechanism is SandboxMechanism.HEADER:
        headers = getattr(client, "headers", None) or {}
        value = headers.get(header_name)
        if value is None:
            raise SandboxVerificationFailed(
                normalized,
                f"the {header_name!r} header selects this venue's demo environment and "
                f"it is not set, so the client is addressing live trading.",
            )
        # The header's VALUE matters, not merely its presence: OKX treats '1' as demo
        # and anything else as live, so a header set to '0' is a live connection wearing
        # a sandbox label.
        if str(value).strip() not in ("1", "true", "True"):
            raise SandboxVerificationFailed(
                normalized,
                f"the {header_name!r} header is present but set to {value!r}, which "
                f"does not select the demo environment.",
            )
        return SandboxVerification(
            exchange_id=normalized,
            verified=True,
            mechanism=mechanism.value,
            detail=(
                f"demo trading selected by the {header_name!r} header (this venue shares "
                f"its production host, so an unchanged base URL is expected)"
            ),
            endpoint=api_fp,
            production_endpoint=api_fp,
        )

    # Unreachable while SandboxMechanism has three members, and it still refuses rather
    # than falling through to a success return if a fourth is ever added.
    raise SandboxVerificationFailed(
        normalized,
        f"sandbox mechanism {mechanism!r} has no verification rule.",
    )


def assert_sandbox_supported(exchange_id: str) -> None:
    """Refuse a sandbox request a venue cannot satisfy, BEFORE a client is built.

    Checked ahead of construction for two reasons. It converts ccxt's raw `NotSupported`
    — which is raised from the constructor, outside `connect()`'s `try`, and so reaches
    callers unclassified — into this platform's own terminal error. And it avoids
    allocating a client, and an aiohttp session, that is about to be thrown away.
    """
    normalized = str(exchange_id or "").strip().lower()
    declared = SANDBOX_MECHANISMS.get(normalized)
    if declared is None:
        raise SandboxVerificationFailed(
            normalized,
            "no sandbox mechanism has been measured for this venue; sandbox mode is "
            "refused until one is added to SANDBOX_MECHANISMS.",
        )
    if declared[0] is SandboxMechanism.UNSUPPORTED:
        raise SandboxVerificationFailed(
            normalized,
            "this venue publishes no sandbox endpoint on the installed ccxt.",
        )


def assert_exchange_routable(exchange_id: str) -> None:
    """Refuse a venue withheld from order routing. Raises, never returns a verdict.

    Called from both :meth:`ExchangeExecutorFactory.create` and
    :meth:`BaseExchangeExecutor.__init__`, so the block holds whether an executor is
    built through the factory or constructed directly. The constructor is the one that
    closes the bypass: a factory-only check is one ``CCXTExchangeExecutor(...)`` call
    away from being irrelevant.

    It raises rather than returning a boolean for the same reason
    ``require_live_authorization`` and ``enforce_kill_switch`` do: a returned verdict can
    be dropped by a caller, and dropping this one places a real order on a venue that was
    withheld because its sandbox silently routes to production.
    """
    normalized = str(exchange_id or "").strip().lower()
    reason = SAFETY_BLOCKED_EXCHANGES.get(normalized)
    if reason is not None:
        logger.critical(
            "EXCH-01: refused to construct an executor for blocked venue %r", normalized
        )
        raise ExchangeRoutingBlocked(normalized, reason)


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
        # EXCH-01. The bypass-closing check, BEFORE any credential is stored.
        #
        # Placed in the base constructor rather than only in the factory because direct
        # construction was a real path, not a hypothetical one: `ExchangeExecutorFactory`
        # is a convenience, and `CCXTExchangeExecutor(exchange_id=..., ...)` is a public
        # class that the reconciliation and recovery code paths also instantiate. A check
        # that lives only in the factory is one direct call away from being irrelevant.
        #
        # First statement in the body, so a blocked venue cannot reach the lines that
        # retain the decrypted api key and secret on the instance.
        assert_exchange_routable(exchange_id)

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
        #: TRADE-04. The signed `LiveOrderAuthorization` for the order currently being
        #: placed. `place_order` refuses to reach a venue without one and clears it
        #: immediately, so a single authorisation cannot cover two orders. `None` is the
        #: safe default: an executor that nobody authorised cannot trade.
        self._current_live_authorization = None
        #: The execution id of the order currently being placed, set by
        #: `verify_and_consume_token` and forwarded to the venue as `clientOrderId`.
        self._consumed_execution_id = None
        #: EXCH-01. The outcome of `verify_sandbox_endpoint`, set by `connect()`.
        #:
        #: `None` until a connection is established, and that default is the safe one:
        #: `sandbox_verified` reads it and reports False, so an executor that has not
        #: connected can never be represented as sandbox-certified. `self.sandbox` is a
        #: REQUEST; this is the EVIDENCE, and conflating the two was the defect.
        self._sandbox_verification: Optional["SandboxVerification"] = None

    @property
    def sandbox_verified(self) -> bool:
        """True only when this client was PROVEN to address a sandbox endpoint.

        Deliberately not derived from `self.sandbox`, which records what the caller asked
        for. Anything reporting sandbox certification must read this, so an unverified or
        not-yet-connected client cannot be presented as sandbox-certified.
        """
        v = self._sandbox_verification
        return bool(v and v.verified)

    @property
    def sandbox_evidence(self) -> Optional["SandboxVerification"]:
        """The verification record, for reporting. `None` before `connect()`."""
        return self._sandbox_verification

    def _redact_credentials(self, text: str) -> str:
        """Strip this instance's own key/secret/password values out of *text*.

        STEP 6.10 says keys are "passed to CCXT, never logged" - true for every log
        call that names ``self._api_key`` / ``self._api_secret`` / ``self._password``
        directly. It does not cover a failure branch that logs ``str(exception)``: if
        CCXT, a network library, or the venue itself ever echoes the credential it was
        given back into an error message, that message reaches the log verbatim unless
        it is redacted first. Call this on any exception text before it is logged.
        """
        for value in (self._api_key, self._api_secret, self._password):
            if value:
                text = text.replace(value, "[REDACTED]")
        return text

    def verify_and_consume_token(self, symbol: str, size: Decimal):
        """Verify the validation token to prevent direct bypass of UnifiedExecutionEngine.

        The verified execution id is retained on ``self._consumed_execution_id`` for the
        duration of this one order so that ``place_order`` can forward it to the venue as
        a ``clientOrderId``. The TOKEN is still consumed and cannot be reused; only the
        id — which is not a secret, is deterministic, and is already written to
        ``execution_records`` — survives, and only because the venue needs it to reject a
        duplicate of this order.
        """
        from backend_app.core.global_safety import verify_validation_token
        
        token = self._current_validation_token
        exec_id = self._current_execution_id
        
        # Consume immediately to prevent reuse
        self._current_validation_token = None
        self._current_execution_id = None
        self._consumed_execution_id = None
        
        if not token or not exec_id:
            raise ValueError("Bypass attempt detected: All executions must route through UnifiedExecutionEngine.")
            
        if not verify_validation_token(exec_id, symbol, size, token):
            raise ValueError("Bypass attempt detected: Invalid execution validation token.")

        self._consumed_execution_id = exec_id
    
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
        """Convert CCXT error to our error type.

        STEP 6.10: *error* originates from CCXT / the venue / an underlying network
        library, none of which this code controls - if any of them ever echoes the
        credential it was given back into an exception message (as an authentication
        failure plausibly would), that message must not propagate into
        ``ExchangeError.message`` unredacted, since every caller logs it and it can
        reach an API response body. Redact this instance's own key/secret/password
        values before classifying or storing the message anywhere below.

        W-6: this used to classify PURELY by substring match on the message, in a fixed
        order that checked "insufficient"/"balance" before "network"/"timeout" -- so a
        transient network error from an operation literally named fetch_balance (a
        message like "Request timeout fetching balance") matched the funds check first
        and was returned as a non-retryable InsufficientFundsError instead of a
        retryable NetworkError, even though nothing about the actual failure had
        anything to do with available funds. Classifying by ccxt's own exception type
        first closes this: a type cannot be fooled by what operation the message
        happens to name. Ordered most-specific-first, since ccxt's hierarchy nests
        RateLimitExceeded/RequestTimeout/DDoSProtection under NetworkError, and
        InsufficientFunds/BadSymbol/InvalidOrder/AuthenticationError under
        ExchangeError. Falls through (never raises) when ``ccxt`` is not a real module
        with that hierarchy -- e.g. patched out in a test -- so the substring fallback
        below, unchanged in content but reordered for the same reason (network/timeout
        checked before insufficient/balance), still runs for a non-ccxt exception
        (asyncio.TimeoutError, an aiohttp/OS error) exactly as it always has.
        """
        redacted = self._redact_credentials(str(error))
        error_str = redacted.lower()

        if CCXT_AVAILABLE:
            try:
                if isinstance(error, ccxt.RateLimitExceeded):
                    return RateLimitError(redacted, retry_after=60)
                if isinstance(error, ccxt.NetworkError):
                    return NetworkError(redacted)
                if isinstance(error, ccxt.InsufficientFunds):
                    return InsufficientFundsError(redacted)
                if isinstance(error, (ccxt.BadSymbol, ccxt.InvalidOrder)):
                    return InvalidSymbolError(redacted)
                if isinstance(error, ccxt.AuthenticationError):
                    return AuthenticationError(redacted)
            except TypeError:
                pass  # `ccxt` here is not a real exception hierarchy (e.g. a test double).

        # Fallback for anything not a recognised ccxt type.
        if "network" in error_str or "timeout" in error_str or "connection" in error_str:
            return NetworkError(redacted)

        if "insufficient" in error_str or "balance" in error_str:
            return InsufficientFundsError(redacted)

        if "rate limit" in error_str or "too many requests" in error_str:
            return RateLimitError(redacted, retry_after=60)

        if "symbol" in error_str or "market" in error_str:
            return InvalidSymbolError(redacted)

        if "authentication" in error_str or "apikey" in error_str or "key" in error_str:
            return AuthenticationError(redacted)

        # Generic error - check if retryable
        retryable = any(word in error_str for word in ["timeout", "network", "temporarily", "busy"])
        return ExchangeError(redacted, retryable=retryable)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6.2 — CCXT EXCHANGE IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _outcome_is_ambiguous(exc: BaseException) -> bool:
    """RT-17: could a failure raised at/after the venue call mean the order was accepted?

    * ccxt ``ExchangeError`` family (InsufficientFunds, InvalidOrder, BadSymbol,
      AuthenticationError, ...): the venue ANSWERED and refused -> definite rejection.
    * ccxt ``OperationFailed`` family (NetworkError and subclasses, BadResponse): the outcome
      is unknown -> ambiguous.
    * Anything else (asyncio.TimeoutError, aiohttp/OS errors, cancellation of the awaiting
      task is not an Exception so never reaches here): the request may have been sent ->
      ambiguous.
    """
    if not CCXT_AVAILABLE:
        return True
    if isinstance(exc, ccxt.OperationFailed):
        return True
    if isinstance(exc, ccxt.ExchangeError):
        return False
    return True


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
        #
        # EXCH-01. `config["sandbox"]` alone is INERT: ccxt 4.3.92's constructor reads the
        # flag out of `self.options` (`safe_bool_2(self.options, 'sandbox', 'testnet')`),
        # so the `options` entry below is the one that arms anything. Both are kept —
        # removing the "redundant" options line would silently route every sandbox order
        # to production, and the top-level key is harmless.
        #
        # The log line that used to sit here announced SANDBOX mode before anything had
        # established it. It now runs only after `verify_sandbox_endpoint` has proven it.
        if self.sandbox:
            assert_sandbox_supported(self.exchange_id)
            config["sandbox"] = True
            config["options"] = {"sandbox": True}
        
        # Create CCXT exchange instance
        exchange_class = getattr(ccxt, self.exchange_id)
        if self.sandbox:
            # ccxt calls `set_sandbox_mode` from inside the constructor, so a venue with
            # no sandbox raises HERE — before the `try` below. Classified rather than
            # allowed to escape as a bare ccxt type.
            try:
                self._exchange = exchange_class(config)
            except SandboxVerificationFailed:
                raise
            except Exception as exc:  # noqa: BLE001
                raise SandboxVerificationFailed(
                    self.exchange_id,
                    f"the client could not be constructed in sandbox mode "
                    f"({type(exc).__name__}: {exc}).",
                ) from exc
        else:
            self._exchange = exchange_class(config)

        # ═══════════════════════════════════════════════════════════════════
        # EXCH-01: PROVE THE SANDBOX, DO NOT ANNOUNCE IT
        # ═══════════════════════════════════════════════════════════════════
        # Positive endpoint evidence, read locally, before this client is used for
        # anything. Offline, so it adds no latency and cannot be defeated by a venue
        # being momentarily reachable.
        #
        # On failure the client is CLOSED before the raise. A client that is constructed
        # and then abandoned leaks its aiohttp session and keeps a decrypted credential
        # alive in the process — the same `Unclosed client session` leak that
        # `connection_engine`'s CE-3 note documents, and the reason that fix nulls its
        # reference too.
        if self.sandbox:
            try:
                verification = verify_sandbox_endpoint(self._exchange, self.exchange_id)
            except SandboxVerificationFailed:
                await self._close_unverified_client()
                logger.critical(
                    "EXCH-01: refused a sandbox connection to %s that could not be "
                    "proven to have left production.", self.exchange_id,
                )
                raise
            self._sandbox_verification = verification
            logger.info(
                "Connecting to %s in VERIFIED SANDBOX mode via %s — %s",
                self.exchange_id, verification.mechanism, verification.detail,
            )
        else:
            self._sandbox_verification = SandboxVerification(
                exchange_id=str(self.exchange_id).lower(),
                verified=False,
                mechanism=None,
                detail="live mode was requested; no sandbox verification was performed",
            )
        
        # Load markets for precision
        await self.normalizer.load_markets(self._exchange)
        
        # Verify connection
        try:
            await self._exchange.fetch_balance()
            self._connected = True
            logger.info(f"Connected to {self.exchange_id} | Sandbox: {self.sandbox}")
        except Exception as e:
            # STEP 6.10: redact before logging - see _redact_credentials. The keys are
            # never logged directly, but the underlying library's exception text is not
            # under this code's control and may echo a credential it was given.
            classified = self._handle_ccxt_error(e)
            logger.error(f"Failed to connect to {self.exchange_id}: {classified.message}")
            raise classified
    
    async def _close_unverified_client(self) -> None:
        """Tear down a client that failed sandbox verification.

        Separate from `disconnect()` because that method is for a client that WAS
        connected: it flips `_connected` and logs a disconnect, neither of which is true
        here. This one closes the socket, drops the reference so the decrypted credential
        it holds is not reachable, and never raises — a failure to clean up must not
        replace the refusal that is propagating.
        """
        client = self._exchange
        self._exchange = None
        self._connected = False
        if client is None:
            return
        try:
            await client.close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the refusal
            logger.warning(
                "EXCH-01: failed to close the unverified %s client", self.exchange_id,
                exc_info=True,
            )

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

        # ═══════════════════════════════════════════════════════════════════
        # TRADE-04: THE LIVE-EXECUTION GATE, ON THE PATH THAT REACHES A VENUE
        # ═══════════════════════════════════════════════════════════════════
        # The token above answers "did this come through the gateway?". It cannot answer
        # "did the gateway establish that this account is permitted to trade real money?",
        # because it is computed from (execution_id, symbol, size) alone.
        #
        # Until this block existed, nothing on this path answered the second question. The
        # pre-launch audit believed `ExecutionFlags.LIVE_TRADING_ENABLED` did, and an AST
        # trace of `_inner_loop` -> `execute_with_idempotency` -> `_execute_trade_internal`
        # -> `place_order` found no reference to it in any of them. Live trading was held
        # off by configuration, a paid-tier entitlement and the absence of a connected
        # exchange account -- defence in depth with nothing behind it.
        #
        # `require_live_authorization` refuses unless the gateway presents a signed
        # authorisation covering the COMPLETE `REQUIRED_CHECKS` set for THIS order on THIS
        # venue. It raises rather than returning: a returned verdict can be dropped, and
        # dropping this one places a real order with customer money.
        #
        # Consumed here, like the token, so one authorisation cannot cover two orders.
        # Regression: tests/test_trade04_live_order_gate_at_venue_boundary.py
        from backend_app.core.live_execution_gate import require_live_authorization

        _authorization = getattr(self, "_current_live_authorization", None)
        self._current_live_authorization = None
        require_live_authorization(
            _authorization,
            symbol=symbol,
            side=getattr(side, "value", side),
            size=size,
            exchange_id=self.exchange_id,
        )

        # ═══════════════════════════════════════════════════════════════════
        # KILL-03 / KILL-04: RE-READ THE HALT STATE, HERE, AT THE BOUNDARY
        # ═══════════════════════════════════════════════════════════════════
        # KILL-04. The authorisation verified immediately above carries the check name
        # `"kill_switch"` in its signed set (minted at `execution_engine.py:1270`). That is
        # a signed assertion that the gateway DID consult the switch -- and it is true --
        # but it is an assertion about a check that ran EARLIER. The only genuine
        # `is_active()` read on this path is at `execution_engine.py:770`, and between
        # that line and `create_order` below the code takes an idempotency claim, runs the
        # advisory-lock capacity transaction, commits to the database and awaits the rate
        # limiter. A halt engaged anywhere in that window was not noticed, and the order
        # went to the venue.
        #
        # KILL-03. `enforce_kill_switch()` had ZERO call sites across 401 `backend_app`
        # source files -- measured, not estimated. It raised correctly and guarded nothing.
        # This is its call site, and it is deliberately the narrowest one: the last
        # statement before the irreversible write.
        #
        # WHY THIS SPECIFIC PLACE. `place_order` is the canonical venue boundary (the
        # four-boundary inventory pinned by `tests/test_paper01_paper_live_isolation.py`
        # names it), and this is the final await before `create_order`. Checking any
        # earlier reopens the window; there is nowhere later.
        #
        # IT RAISES, AND THAT IS THE POINT. `ExecutionBlocked` propagates out of
        # `place_order`; it is NOT converted into an `OrderResult(success=False)` by the
        # `try` below, because it is raised before that block is entered. A returned
        # verdict can be dropped by a caller, and dropping this one places a real order
        # during a platform halt -- the same reasoning `require_live_authorization` and
        # `assert_live_environment` already apply.
        #
        # FAIL-CLOSED COMES FOR FREE AND IS NOT INCIDENTAL. `is_active()` engages its
        # local latch and returns True on `RedisUnavailable` or any unexpected error
        # (KILL-01's fix), so an unreadable halt state refuses the order rather than
        # permitting it.
        #
        # SCOPE, STATED EXPLICITLY. This blocks NEW ORDERS ONLY -- which is this
        # platform's documented emergency policy, pinned by
        # `tests/test_trade02_kill_switch_fails_closed.py::
        # test_activation_refuses_new_orders_and_does_not_flatten`. `cancel_order` is a
        # different method and is NOT gated here, so an operator can still cancel resting
        # orders while the platform is halted. A position-reducing order, however, IS
        # blocked, because it is a new order. That is a consequence of the existing
        # policy, not of this change; changing it would be a product decision about what
        # a halt means, and it is recorded in staging/TRADING_SAFETY_AUDIT.md rather than
        # quietly carved out here.
        #
        # Regression: tests/test_kill03_authoritative_global_halt.py
        from backend_app.core.global_safety import enforce_kill_switch

        await enforce_kill_switch()

        # RT-17: set True immediately before the venue call. An exception BEFORE this is a
        # definite local refusal ("rejected"); one at or after it may have reached the venue.
        _submit_attempted = False

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

            # ═══════════════════════════════════════════════════════════════
            # VENUE MINIMUMS, CHECKED BEFORE THE ORDER IS SENT
            # ═══════════════════════════════════════════════════════════════
            # `limits.amount.min` and `limits.cost.min` were read from CCXT and carried
            # as far as `asset_universe`, and then compared against nothing. An order
            # below a venue minimum is rejected by the venue, but by then the idempotency
            # claim has been taken and the execution record says an attempt was made — so
            # the strategy's intent is consumed and the position never opens, silently.
            #
            # Truncation makes this reachable rather than theoretical: `normalize_size`
            # rounds DOWN, so a size just above the minimum can land just below it.
            #
            # Refused locally, with the figures named, so the trader is told which
            # minimum and by how much instead of receiving the venue's opaque rejection.
            size_for_checks = Decimal(normalized_size)
            minimums = self.normalizer.market_minimums(symbol)

            if size_for_checks <= 0:
                return OrderResult(
                    success=False,
                    exchange_order_id=None,
                    status="rejected",
                    filled_size=None,
                    remaining_size=None,
                    avg_price=None,
                    error_message=(
                        f"Order size {size} truncated to {normalized_size} at this "
                        f"venue's amount precision, which is not a tradable quantity."
                    ),
                    error_code="BELOW_PRECISION",
                )

            min_amount = minimums.get("min_amount")
            if min_amount is not None and size_for_checks < min_amount:
                return OrderResult(
                    success=False,
                    exchange_order_id=None,
                    status="rejected",
                    filled_size=None,
                    remaining_size=None,
                    avg_price=None,
                    error_message=(
                        f"Order size {size_for_checks} is below the {self.exchange_id} "
                        f"minimum order quantity of {min_amount} for {symbol}."
                    ),
                    error_code="BELOW_MIN_QUANTITY",
                )

            min_cost = minimums.get("min_cost")
            if min_cost is not None and price and price > 0:
                notional = size_for_checks * Decimal(str(price))
                if notional < min_cost:
                    return OrderResult(
                        success=False,
                        exchange_order_id=None,
                        status="rejected",
                        filled_size=None,
                        remaining_size=None,
                        avg_price=None,
                        error_message=(
                            f"Order notional {notional} is below the {self.exchange_id} "
                            f"minimum of {min_cost} for {symbol}."
                        ),
                        error_code="BELOW_MIN_NOTIONAL",
                    )

            # Build order parameters.
            #
            # POSITIONAL, NOT **kwargs. CCXT's signature is
            # `create_order(symbol, type, side, amount, price=None, params={})` — it has
            # no `stopPrice` parameter, so the previous `create_order(**params)` with a
            # `stopPrice` key raised TypeError for every stop and take-profit order.
            # Trigger prices belong in `params`, which is where every venue's unified
            # implementation reads them from.
            ccxt_price = None
            if price and order_type in (
                OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT_LIMIT
            ):
                ccxt_price = float(self.normalizer.normalize_price(symbol, price))

            extra_params: Dict[str, Any] = {}
            if stop_price:
                extra_params["stopPrice"] = float(
                    self.normalizer.normalize_price(symbol, stop_price)
                )

            # VENUE-SIDE IDEMPOTENCY.
            #
            # Without a client order id the venue cannot recognise a repeat, so a ccxt
            # internal retry, or a lost response followed by any retry, becomes a second
            # REAL order. The execution_id is already the deterministic idempotency key
            # this platform computes for the trade, so forwarding it makes the venue
            # enforce the same uniqueness the database does.
            if self._consumed_execution_id:
                extra_params["clientOrderId"] = self._consumed_execution_id

            params = {
                "symbol": ccxt_symbol,
                "type": ccxt_type,
                "side": ccxt_side,
                "amount": float(normalized_size),
                "price": ccxt_price,
                "params": extra_params,
            }
            
            # STEP 6.10: Security - log without sensitive data
            logger.info(
                f"PLACE ORDER: {self.exchange_id} | "
                f"{symbol} {side.value} {order_type.value} | "
                f"size={normalized_size} | "
                f"price={price if price else 'market'} | "
                f"sandbox={self.sandbox}"
            )
            
            # Execute via CCXT — positional, matching
            # create_order(symbol, type, side, amount, price, params)
            _submit_attempted = True
            response = await self._exchange.create_order(
                params["symbol"],
                params["type"],
                params["side"],
                params["amount"],
                params["price"],
                params["params"],
            )
            
            # Build result
            # STEP 6.4: Status is 'pending' - we wait for events for FILLED
            filled_val = response.get("filled") if response.get("filled") is not None else 0
            remaining_val = response.get("remaining") if response.get("remaining") is not None else normalized_size
            avg_price_val = response.get("average") if response.get("average") is not None else None

            raw_id = response.get("id")
            order_id_str = str(raw_id) if raw_id is not None else None

            if order_id_str is None:
                # RT-17: the venue answered without an order id. It may have accepted the order;
                # reporting that as a plain failure invites a duplicate. Ambiguous.
                logger.critical(
                    "ORDER SUBMISSION UNKNOWN: %s %s - the venue response carried no order id; "
                    "the order may be live. Do NOT resubmit; adjudicate by clientOrderId.",
                    symbol, side.value,
                )
                return OrderResult(
                    success=False,
                    exchange_order_id=None,
                    status="unknown",
                    filled_size=None,
                    remaining_size=None,
                    avg_price=None,
                    raw_response=response,
                    error_message="The exchange response carried no order id; the order may be live.",
                    error_code="SUBMISSION_UNKNOWN",
                )

            result = OrderResult(
                success=True if order_id_str else False,
                exchange_order_id=order_id_str,
                status="pending",  # STEP 6.4: NOT 'filled'
                filled_size=str(filled_val),
                remaining_size=str(remaining_val),
                avg_price=str(avg_price_val) if avg_price_val is not None else None,
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

            # RT-17: A LOST RESPONSE IS NOT A REJECTION.
            # Once the request may have left this process, a timeout / network error / bad
            # response says nothing about whether the venue accepted the order. Recording it
            # as "rejected" lets the strategy roll back, re-enter on the next tick (new price
            # => new execution id) and double the position. ccxt marks exactly this class as
            # `OperationFailed` (NetworkError, RequestTimeout, ExchangeNotAvailable,
            # DDoSProtection, RateLimitExceeded, BadResponse, ...); venue refusals are
            # `ExchangeError`s and stay "rejected". A non-ccxt failure after the send
            # (asyncio/aiohttp timeouts, resets) is equally ambiguous.
            if _submit_attempted and _outcome_is_ambiguous(e):
                logger.critical(
                    "ORDER SUBMISSION UNKNOWN: %s %s | %s | the request may have reached the "
                    "venue. NOT recorded as rejected; do NOT resubmit; adjudicate by "
                    "clientOrderId.", symbol, side.value, type(e).__name__,
                )
                return OrderResult(
                    success=False,
                    exchange_order_id=None,
                    status="unknown",
                    filled_size=None,
                    remaining_size=None,
                    avg_price=None,
                    error_message=error.message,
                    error_code="SUBMISSION_UNKNOWN",
                )

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
            # RT-21: a cancel is idempotent. An order the venue no longer knows about is
            # already not resting, which is what the caller asked for.
            if CCXT_AVAILABLE and isinstance(e, ccxt.OrderNotFound):
                logger.info("CANCEL: %s already closed at the venue (%s)", exchange_order_id, symbol)
                return CancelResult(success=True, status="already_closed")

            error = self._handle_ccxt_error(e)
            
            logger.error(
                f"CANCEL FAILED: {exchange_order_id} | "
                f"error={error.message}"
            )

            # RT-21: a lost response to a cancel does not say whether the order is still
            # resting. Reported as such so the caller re-reads open orders rather than
            # assuming either outcome.
            return CancelResult(
                success=False,
                status="unknown" if _outcome_is_ambiguous(e) else "error",
                error_message=error.message
            )

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """RECON-01: the one place that lists this account's resting orders.

        Extracted from :meth:`cancel_all_open_orders`'s own internal listing step so a
        caller that only wants to COMPARE orders (reconciliation) does not have to go
        through a method whose name and purpose is to cancel them. Same rate limit,
        same symbol normalisation, same error path.

        Raises the classified :class:`ExchangeError` on a venue/network failure rather
        than returning ``[]``: an empty list and "the venue could not be asked" are
        different facts, and a caller (e.g. a reconciliation pass) that treated them the
        same would read every currently-open order as having left the venue.
        """
        ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol) if symbol else None
        await self.rate_limiter.acquire("fetch_open_orders")
        try:
            return await self._exchange.fetch_open_orders(ccxt_symbol)
        except Exception as e:
            error = self._handle_ccxt_error(e)
            logger.error("FETCH_OPEN_ORDERS failed: %s", error.message)
            raise error

    async def cancel_all_open_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """RT-21: cancel every resting order (optionally for one symbol). Reduce-only by nature.

        Lists open orders, then cancels each through :meth:`cancel_order` so there is one
        venue-cancel call site and one error classification. Per-order outcomes are reported
        individually; one failure does not stop the rest.
        """
        ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol) if symbol else None
        try:
            await self.rate_limiter.acquire("fetch_open_orders")
            open_orders = await self._exchange.fetch_open_orders(ccxt_symbol)
        except Exception as e:
            error = self._handle_ccxt_error(e)
            logger.error("CANCEL-ALL: could not list open orders: %s", error.message)
            return {
                "success": False, "listed": False, "cancelled": [], "already_closed": [],
                "failed": [], "unknown": [], "cancelled_count": 0,
                "error": error.message,
            }

        cancelled, already_closed, failed, unknown = [], [], [], []
        for order in open_orders or []:
            oid = order.get("id")
            if oid is None:
                continue
            res = await self.cancel_order(str(oid), order.get("symbol") or ccxt_symbol)
            if res.success and res.status == "already_closed":
                already_closed.append(str(oid))
            elif res.success:
                cancelled.append(str(oid))
            elif res.status == "unknown":
                unknown.append({"order_id": str(oid), "error": res.error_message})
            else:
                failed.append({"order_id": str(oid), "error": res.error_message})
        return {
            "success": not failed and not unknown, "listed": True,
            "cancelled": cancelled, "already_closed": already_closed,
            "failed": failed, "unknown": unknown,
            "cancelled_count": len(cancelled),
        }
    
    async def get_order_status(self, exchange_order_id: str, symbol: str) -> OrderStatusResult:
        """STEP 6.1: Fetch order status from exchange."""
        try:
            # STEP 6.7: Rate limiting
            await self.rate_limiter.acquire("get_order")
            
            ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol)
            
            response = await self._exchange.fetch_order(exchange_order_id, ccxt_symbol)
            
            filled_val = response.get("filled") if response.get("filled") is not None else 0
            remaining_val = response.get("remaining") if response.get("remaining") is not None else 0
            avg_price_val = response.get("average") if response.get("average") is not None else None
            raw_status = response.get("status")
            status_str = str(raw_status).lower() if raw_status is not None else "unknown"

            return OrderStatusResult(
                success=True,
                status=status_str,
                filled_size=str(filled_val),
                remaining_size=str(remaining_val),
                avg_price=str(avg_price_val) if avg_price_val is not None else None
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

    async def find_order_by_client_order_id(
        self, client_order_id: str, symbol: str
    ) -> OrderStatusResult:
        """Ask the venue about an order by the id WE assigned it.

        TRADE-07. THE RECOVERY HANDLE THAT EXISTED AND WAS NEVER USED.
        -------------------------------------------------------------
        `place_order` already forwards `execution_id` to the venue as `clientOrderId`
        (see the VENUE-SIDE IDEMPOTENCY block above), and `execution_id` is
        deterministic -- `generate_execution_id` derives it from
        (tenant, strategy, symbol, time bucket, side, qty, price). So for every order this
        platform has ever sent there is a locally reconstructable identifier the venue
        also knows.

        Measured: nothing in the repository looked an order up by it. `get_order_status`
        can only `fetch_order(exchange_order_id, ...)` -- the venue's own id, which is
        exactly the thing an interrupted submission never received. That is why a row
        stuck in EXECUTING with `order_id IS NULL` was unrecoverable: the one identifier
        that could resolve it was being sent and then forgotten.

        THREE STRATEGIES, IN ORDER OF DIRECTNESS, because CCXT unifies this poorly:

        1. ``fetch_order(clientOrderId, symbol, {"clientOrderId": ...})``. Several venues
           accept it; the id positional is passed too because some implementations read
           it from there instead of from params.
        2. Scan ``fetch_open_orders(symbol)``. Catches a RESTING unfilled order, which is
           the most dangerous case to get wrong -- live exposure the platform does not
           know it has.
        3. Scan ``fetch_closed_orders(symbol)``. Catches an order that filled or was
           cancelled while the response was lost.

        A clean "not found" from all three is NOT an error: it returns
        ``success=True, status="not_found"``, because "the venue has no such order" is a
        definite and highly useful answer -- it converts an ambiguous row into a safely
        failable one. Only an inability to ASK is an error, and the caller must then keep
        the row unresolved rather than assume anything.

        The venue's own order id is returned in ``venue_order_id``, which was added to
        ``OrderStatusResult`` for this method: ``get_order_status`` is *called* with that
        id and so never needed to return one, whereas this method's entire purpose is to
        discover it. The caller needs it because recovery is forbidden from recording a
        fill without the venue's own identifier for it.
        """
        try:
            await self.rate_limiter.acquire("get_order")
            ccxt_symbol = self.normalizer.format_symbol_for_ccxt(symbol)
        except Exception as exc:  # noqa: BLE001
            return OrderStatusResult(
                success=False, status="error", filled_size="0", remaining_size="0",
                avg_price=None,
                error_message=f"could not prepare a venue lookup: {exc}",
            )

        def _as_status(order: dict) -> OrderStatusResult:
            filled_val = order.get("filled") if order.get("filled") is not None else 0
            remaining_val = (
                order.get("remaining") if order.get("remaining") is not None else 0
            )
            avg = order.get("average")
            raw_status = order.get("status")
            return OrderStatusResult(
                success=True,
                status=str(raw_status).lower() if raw_status is not None else "unknown",
                filled_size=str(filled_val),
                remaining_size=str(remaining_val),
                avg_price=str(avg) if avg is not None else None,
                error_message=None,
                # The venue's OWN id -- the thing the interrupted submission never
                # received, and the thing recovery must have before it is allowed to
                # record a fill at all.
                venue_order_id=(
                    str(order.get("id")) if order.get("id") is not None else None
                ),
            )

        def _matches(order: Any) -> bool:
            if not isinstance(order, dict):
                return False
            info = order.get("info") if isinstance(order.get("info"), dict) else {}
            candidates = {
                order.get("clientOrderId"),
                info.get("clientOrderId"),
                info.get("client_order_id"),
            }
            return str(client_order_id) in {
                str(c) for c in candidates if c is not None
            }

        reachable = False

        # 1. Direct lookup.
        try:
            order = await self._exchange.fetch_order(
                client_order_id, ccxt_symbol, {"clientOrderId": client_order_id}
            )
            reachable = True
            if isinstance(order, dict) and (_matches(order) or order.get("id")):
                return _as_status(order)
        except Exception as exc:  # noqa: BLE001
            # A venue that does not support the lookup and a venue that genuinely has no
            # such order both raise here, and they are indistinguishable at this layer.
            # So fall through to the scans rather than concluding anything.
            logger.info(
                "TRADE-07: direct clientOrderId lookup for %s was not conclusive (%s); "
                "falling back to order scans.",
                client_order_id, self._redact_credentials(str(exc)),
            )

        # 2/3. Resting and recently-closed orders.
        for fetcher, label in (
            (getattr(self._exchange, "fetch_open_orders", None), "open"),
            (getattr(self._exchange, "fetch_closed_orders", None), "closed"),
        ):
            if fetcher is None:
                continue
            try:
                orders = await fetcher(ccxt_symbol)
                reachable = True
                for candidate in orders or []:
                    if _matches(candidate):
                        logger.warning(
                            "TRADE-07: found %s order at the venue for clientOrderId %s "
                            "-- this execution DID reach the exchange.",
                            label, client_order_id,
                        )
                        return _as_status(candidate)
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "TRADE-07: %s-order scan for %s failed (%s).",
                    label, client_order_id, self._redact_credentials(str(exc)),
                )

        if reachable:
            # A DEFINITE negative: the venue answered and does not know this order, so
            # the submission did not take effect and the row is safe to fail.
            return OrderStatusResult(
                success=True, status="not_found", filled_size="0", remaining_size="0",
                avg_price=None,
            )

        # We could not ask at all. Say so; the caller keeps the row unresolved.
        return OrderStatusResult(
            success=False, status="error", filled_size="0", remaining_size="0",
            avg_price=None,
            error_message=(
                f"the venue could not be queried about clientOrderId {client_order_id}; "
                f"no lookup method succeeded, so the order's existence is unknown"
            ),
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
                f"Error: {place_result.error_message}"
            )
            # Return failed result - DO NOT assume anything
            return OrderResult(
                success=False,
                exchange_order_id=None,
                status="rejected",
                filled_size="0",
                remaining_size=str(amount),
                avg_price=None,
                error_message=f"Order placement failed: {place_result.error_message}",
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
                
                # STEP 2: TIMEOUT HANDLING - Mark as UNKNOWN with reconciliation
                # DO NOT assume filled - we don't know the status
                # Trigger reconciliation to determine actual state
                unknown_result = OrderResult(
                    success=False,  # Not confirmed = not successful
                    exchange_order_id=exchange_order_id,
                    status="unknown",  # CRITICAL: Unknown, not filled
                    filled_size="0",
                    remaining_size=str(amount),
                    avg_price=None,
                    error_message=f"Order confirmation timeout after {max_wait_seconds}s - reconciliation required",
                    raw_response=None
                )
                
                # CRITICAL: Trigger reconciliation for UNKNOWN state
                if _is_production():
                    logger.critical(
                        f"PRODUCTION ALERT: Order {exchange_order_id} in UNKNOWN state. "
                        f"Immediate reconciliation required. Manual intervention may be needed."
                    )
                    # In production, UNKNOWN state should trigger immediate alert
                    # and potentially halt trading for that user until resolved
                else:
                    logger.warning(
                        f"DEV MODE: Order {exchange_order_id} in UNKNOWN state. "
                        f"Reconciliation would be triggered in production."
                    )
                
                return unknown_result
            
            # Poll order status from exchange
            try:
                status_result = await self.get_order_status(
                    exchange_order_id=exchange_order_id,
                    symbol=symbol
                )
                
                if status_result.success:
                    order_status = (status_result.status or "").lower()
                    
                    # STEP 2: MARKET ORDER - Wait for FILLED
                    if order_type.lower() == "market":
                        if order_status in ("filled", "closed"):
                            logger.info(
                                f"STEP 2: MARKET order {exchange_order_id} CONFIRMED FILLED. "
                                f"Filled amount: {status_result.filled_size}"
                            )
                            return OrderResult(
                                success=True,
                                exchange_order_id=exchange_order_id,
                                status="closed",
                                filled_size=status_result.filled_size,
                                remaining_size=status_result.remaining_size,
                                avg_price=status_result.avg_price,
                                error_message=None,
                                raw_response=None
                            )
                        elif order_status in ("canceled", "cancelled", "expired"):
                            logger.error(
                                f"STEP 2: MARKET order {exchange_order_id} was CANCELED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status="canceled",
                                filled_size=status_result.filled_size,
                                remaining_size=status_result.remaining_size,
                                avg_price=status_result.avg_price,
                                error_message="Market order was canceled",
                                raw_response=None
                            )
                        elif order_status in ("rejected", "error"):
                            logger.error(
                                f"STEP 2: MARKET order {exchange_order_id} was REJECTED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status="rejected",
                                filled_size="0",
                                remaining_size=str(amount),
                                avg_price=None,
                                error_message="Market order was rejected",
                                raw_response=None
                            )
                    
                    # STEP 2: LIMIT ORDER - Wait for OPEN (or FILLED)
                    elif order_type.lower() == "limit":
                        if order_status in ("open", "filled", "closed"):
                            logger.info(
                                f"STEP 2: LIMIT order {exchange_order_id} CONFIRMED {order_status}. "
                                f"Filled amount: {status_result.filled_size}"
                            )
                            return OrderResult(
                                success=True,
                                exchange_order_id=exchange_order_id,
                                status=order_status,  # open or closed
                                filled_size=status_result.filled_size,
                                remaining_size=status_result.remaining_size,
                                avg_price=status_result.avg_price,
                                error_message=None,
                                raw_response=None
                            )
                        elif order_status in ("canceled", "cancelled", "expired"):
                            logger.error(
                                f"STEP 2: LIMIT order {exchange_order_id} was CANCELED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status="canceled",
                                filled_size=status_result.filled_size,
                                remaining_size=status_result.remaining_size,
                                avg_price=status_result.avg_price,
                                error_message="Limit order was canceled",
                                raw_response=None
                            )
                        elif order_status in ("rejected", "error"):
                            logger.error(
                                f"STEP 2: LIMIT order {exchange_order_id} was REJECTED by exchange"
                            )
                            return OrderResult(
                                success=False,
                                exchange_order_id=exchange_order_id,
                                status="rejected",
                                filled_size="0",
                                remaining_size=str(amount),
                                avg_price=None,
                                error_message="Limit order was rejected",
                                raw_response=None
                            )
                
                else:
                    # Status check failed
                    logger.warning(
                        f"STEP 2: Status check failed for order {exchange_order_id}: "
                        f"{status_result.error_message}"
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
    
    #: Venues this platform will route an order to.
    #:
    #: `bitget` was REMOVED here (EXCH-01) — see `SAFETY_BLOCKED_EXCHANGES` above for the
    #: measurement. It is withheld in two independent places rather than one: absent from
    #: this list, and refused by `assert_exchange_routable`. The list alone would be a
    #: silent "unsupported exchange" that loses the reason; the refusal alone would leave
    #: bitget advertised as routable by every consumer of this attribute — and there are
    #: several, including `asset_universe._universe_exchanges`, which decides which
    #: venues' markets a strategy author can even select.
    SUPPORTED_EXCHANGES = ["binance", "bybit", "coinbase", "kraken", "okx", "kucoin"]
    
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

        # EXCH-01. Checked BEFORE the allowlist test so a deliberately blocked venue
        # reports why it was blocked, instead of the generic "Unsupported exchange" that
        # removal from the list alone would produce. "Withheld for a money-safety defect"
        # and "never supported" warrant different responses from an operator.
        assert_exchange_routable(exchange_id)

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


# Global registry.
#
# ⚠️ THE KEY OF THIS DICT DECIDES WHOSE MONEY AN ORDER SPENDS.
#
# It used to be `f"{exchange_id}:{sandbox}"`, which contains nothing identifying the
# ACCOUNT whose decrypted credentials were passed in. `BotRunner._recover_live_state`
# calls this function with the keys it has just decrypted out of the vault for one
# specific user, so in a worker process that had already served one trader on binance,
# the next trader's call matched the existing key and was handed back the FIRST
# trader's executor — holding the first trader's api key and secret. Every order the
# second trader's strategy produced would have been signed with the first trader's
# credentials and executed against the first trader's exchange account and balance.
#
# Reproduced by execution in tests/test_exchange_executor_cache_is_per_tenant.py:
# two calls differing only in credentials returned the SAME object, and the second
# caller's executor reported the first caller's api key.
#
# `connection_engine.get_or_create_exchange` had this right all along — it keys on
# `sha256(json.dumps([user_id, exchange_id]))`. This is the same fix for the other pool.
_executors: Dict[str, BaseExchangeExecutor] = {}


def _executor_cache_key(
    user_id: str,
    exchange_id: str,
    sandbox: bool,
    api_key: str,
    api_secret: str,
    password: Optional[str],
) -> str:
    """The cache identity of an executor: WHOSE keys, for WHICH venue, in WHICH mode.

    The credential material is included as a salted digest, not stored, for two reasons:

    * **Key rotation.** A trader who rotates a compromised api key gets a FRESH executor
      rather than a cached one still holding the revoked secret. Without the credential
      component, the old executor would be served until the process restarted, so every
      order would keep being rejected by the venue with no way for the trader to fix it.
    * **Defence in depth.** If this derivation is ever changed and gets `user_id` wrong
      again, differing credentials still land on different keys, so the cache cannot
      serve one account's secret to another.

    The digest is over a length-prefixed encoding so that no combination of field values
    can be rearranged into another ((``"a"``, ``"bc"``) and (``"ab"``, ``"c"``) must not
    collide), and the credentials are hashed rather than concatenated into a readable key
    because cache keys reach logs and metrics labels.
    """
    parts = [
        str(user_id),
        str(exchange_id).lower(),
        "sandbox" if sandbox else "live",
        api_key or "",
        api_secret or "",
        password or "",
    ]
    canonical = "\x1f".join(f"{len(p)}:{p}" for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_exchange_executor(
    exchange_id: str,
    api_key: str,
    api_secret: str,
    sandbox: bool = False,
    password: Optional[str] = None,
    *,
    user_id: str,
) -> BaseExchangeExecutor:
    """Get or create the executor for ONE account's credentials on one venue.

    Args:
        exchange_id: The venue id, as accepted by :class:`ExchangeExecutorFactory`.
        api_key: The calling account's decrypted api key.
        api_secret: The calling account's decrypted secret.
        sandbox: Whether to trade the venue's testnet.
        password: The passphrase, for venues that require one (OKX, KuCoin, Coinbase).
        user_id: **Required, keyword-only.** The account the credentials belong to. It is
            keyword-only so it cannot be supplied positionally into one of the credential
            slots by mistake, and required with no default because a default would
            silently recreate the shared-cache defect for any caller that forgot it.

    Raises:
        ValueError: If ``user_id`` is empty. Failing closed is correct here: the
            alternative is an executor cached under an identity that does not
            distinguish accounts, which is the defect this parameter exists to prevent.
    """
    if not user_id or not str(user_id).strip():
        raise ValueError(
            "user_id is required to cache an exchange executor. Without it the cache "
            "cannot distinguish accounts and would serve one trader's API credentials "
            "to another."
        )

    key = _executor_cache_key(
        user_id=str(user_id).strip(),
        exchange_id=exchange_id,
        sandbox=sandbox,
        api_key=api_key,
        api_secret=api_secret,
        password=password,
    )

    cached = _executors.get(key)
    if cached is not None:
        # Belt and braces. The key already covers the credentials, so this can only fire
        # on a hash collision or a future regression in the derivation above — and the
        # consequence of being wrong is placing an order on somebody else's account, so
        # it is worth one string comparison per order to refuse rather than proceed.
        if cached._api_key == api_key and cached._api_secret == api_secret:
            return cached
        logger.critical(
            "Executor cache identity mismatch for exchange=%s user=%s. Refusing the "
            "cached instance and building a fresh one.",
            exchange_id,
            user_id,
        )

    executor = ExchangeExecutorFactory.create(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        sandbox=sandbox,
        password=password,
    )
    _executors[key] = executor
    return executor


def release_exchange_executor(
    user_id: str,
    exchange_id: str,
    sandbox: bool = False,
    api_key: str = "",
    api_secret: str = "",
    password: Optional[str] = None,
) -> bool:
    """Drop one account's cached executor, so a stopped bot does not pin its credentials.

    Returns ``True`` when an entry was removed. Without this, ``_executors`` grows for
    the lifetime of the worker and keeps decrypted api secrets reachable in memory after
    the bot that needed them has stopped.
    """
    key = _executor_cache_key(
        user_id=str(user_id).strip(),
        exchange_id=exchange_id,
        sandbox=sandbox,
        api_key=api_key,
        api_secret=api_secret,
        password=password,
    )
    return _executors.pop(key, None) is not None


# Alias for backward compatibility
ExchangeExecutor = CCXTExchangeExecutor

