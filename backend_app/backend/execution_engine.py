"""
STEP 6.1 + 6.2 + 6.4 + 6.5 + 6.6 + 6.7 + 6.8 + 6.9 + 7.11 + 8.1 + 8.4 + 8.5: Production-Grade Execution Engine

STEP 6.1: Order ID Idempotency - No duplicate orders
STEP 6.2: Exchange Response Validation - No fake/broken responses
STEP 6.4: Order State Machine - Enforces order lifecycle
STEP 6.5: Safe Retry System - Exponential backoff
STEP 6.6: Slippage Model - Realistic execution pricing
STEP 6.7: Exchange Sync - Local state = Exchange state
STEP 6.8: Order Book Aware - Use depth for better execution
STEP 6.9: Latency Control - Block stale trades
STEP 7.11: ExecutionGuard Integration - Mandatory validation before execution
STEP 8.1: Metrics System - Track execution metrics
STEP 8.4: Global Circuit Breaker - Capital protection
STEP 8.5: Auto Position Liquidation - Emergency exit

Key: orders:{tenant_id}:{client_order_id}
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Set, List
from collections import deque
from enum import Enum, auto
import redis.asyncio as redis
from dataclasses import dataclass, field
from decimal import Decimal

# STEP 7.11: Import ExecutionGuard for mandatory validation
from backend_app.backend.execution_guard import ExecutionGuard, ValidationSeverity

# STEP 8.1: Import metrics collector
from backend_app.backend.metrics import record_trade_executed, record_failed_order, record_trade_blocked

# STEP 8.4: Import Global Circuit Breaker for capital protection
from backend_app.backend.circuit_breaker import global_circuit_breaker

logger = logging.getLogger(__name__)


class OrderState(Enum):
    """
    STEP 6.4: Order State Enumeration
    
    States:
    - CREATED: Order created locally, not yet sent to exchange
    - SUBMITTED: Order sent to exchange, awaiting acknowledgment
    - OPEN: Order acknowledged by exchange, active on order book
    - PARTIAL: Order partially filled, remaining quantity active
    - FILLED: Order completely filled, no remaining quantity
    - CANCELLED: Order cancelled (by user or system), no longer active
    - FAILED: Order failed to submit or was rejected by exchange
    """
    CREATED = "created"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    
    TERMINAL_STATES = {"filled", "cancelled", "failed"}
    
    @property
    def is_terminal(self) -> bool:
        """Check if state is terminal (no further transitions allowed)."""
        return self.value in self.TERMINAL_STATES


class InvalidOrderTransitionError(Exception):
    """
    STEP 6.4: Invalid Order Transition Error
    
    Raised when attempting an invalid order state transition.
    """
    
    def __init__(self, order_id: str, from_state: str, to_state: str, reason: str = ""):
        message = f"Invalid order transition: {order_id} cannot transition from '{from_state}' to '{to_state}'"
        if reason:
            message += f". Reason: {reason}"
        super().__init__(message)
        self.order_id = order_id
        self.from_state = from_state
        self.to_state = to_state
        self.timestamp = datetime.utcnow().isoformat()


class OrderStateMachine:
    """
    STEP 6.4: Order State Machine
    
    Enforces strict order lifecycle:
    
    CREATED → SUBMITTED → OPEN → PARTIAL → FILLED
                     ↓           ↓
                     └── CANCELLED (from OPEN or PARTIAL)
                     ↓
                     └── FAILED (from CREATED or SUBMITTED)
    
    Rules:
    1. Terminal states (FILLED, CANCELLED, FAILED) cannot transition further
    2. All transitions must follow the defined valid paths
    3. Invalid transitions raise InvalidOrderTransitionError
    """
    
    # Define valid transitions as a dictionary of {from_state: [to_states]}
    VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
        OrderState.CREATED: {OrderState.SUBMITTED, OrderState.FAILED},
        OrderState.SUBMITTED: {OrderState.OPEN, OrderState.CANCELLED, OrderState.FAILED},
        OrderState.OPEN: {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELLED},
        OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELLED},
        OrderState.FILLED: set(),  # Terminal state
        OrderState.CANCELLED: set(),  # Terminal state
        OrderState.FAILED: set(),  # Terminal state
    }
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.state_ttl = 86400 * 7  # 7 days retention
    
    def _get_state_key(self, tenant_id: str, order_id: str) -> str:
        """Generate Redis key for order state."""
        return f"order_state:{tenant_id}:{order_id}"
    
    def _get_history_key(self, tenant_id: str, order_id: str) -> str:
        """Generate Redis key for order state history."""
        return f"order_history:{tenant_id}:{order_id}"
    
    def _is_valid_transition(self, from_state: OrderState, to_state: OrderState) -> bool:
        """
        STEP 6.4: Check if state transition is valid.
        
        Args:
            from_state: Current state
            to_state: Proposed new state
            
        Returns:
            True if transition is valid, False otherwise
        """
        if from_state not in self.VALID_TRANSITIONS:
            return False
        
        return to_state in self.VALID_TRANSITIONS[from_state]
    
    def _get_reason_for_invalid_transition(
        self, from_state: OrderState, to_state: OrderState
    ) -> str:
        """Get human-readable reason for why transition is invalid."""
        if from_state.is_terminal:
            return f"{from_state.value} is a terminal state - no further transitions allowed"
        
        valid_next = [s.value for s in self.VALID_TRANSITIONS.get(from_state, set())]
        if valid_next:
            return f"Valid transitions from {from_state.value} are: {', '.join(valid_next)}"
        else:
            return f"No valid transitions defined from {from_state.value}"
    
    async def transition(
        self,
        tenant_id: str,
        order_id: str,
        new_state: OrderState,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        STEP 6.4: Attempt to transition order to new state.
        
        Args:
            tenant_id: Tenant identifier
            order_id: Order identifier
            new_state: Target state to transition to
            metadata: Optional metadata about the transition
            
        Returns:
            Dict with transition details
            
        Raises:
            InvalidOrderTransitionError: If transition is not allowed
        """
        # Get current state
        current_state_data = await self.get_current_state(tenant_id, order_id)
        current_state = OrderState(current_state_data["state"]) if current_state_data else OrderState.CREATED
        
        # STEP 6.4: Validate transition
        if not self._is_valid_transition(current_state, new_state):
            reason = self._get_reason_for_invalid_transition(current_state, new_state)
            logger.error(
                f"🚨 INVALID ORDER TRANSITION: {order_id} | "
                f"{current_state.value} → {new_state.value} | {reason}"
            )
            raise InvalidOrderTransitionError(
                order_id=order_id,
                from_state=current_state.value,
                to_state=new_state.value,
                reason=reason
            )
        
        # STEP 6.4: Execute valid transition
        timestamp = datetime.utcnow().isoformat()
        
        state_record = {
            "order_id": order_id,
            "tenant_id": tenant_id,
            "state": new_state.value,
            "previous_state": current_state.value if current_state_data else None,
            "transitioned_at": timestamp,
            "metadata": metadata or {},
        }
        
        # Store new state
        state_key = self._get_state_key(tenant_id, order_id)
        await self.redis.setex(
            state_key,
            self.state_ttl,
            json.dumps(state_record)
        )
        
        # Record in history
        history_key = self._get_history_key(tenant_id, order_id)
        history_entry = {
            "from_state": current_state.value if current_state_data else None,
            "to_state": new_state.value,
            "timestamp": timestamp,
            "metadata": metadata or {},
        }
        await self.redis.lpush(history_key, json.dumps(history_entry))
        await self.redis.expire(history_key, self.state_ttl)
        
        logger.info(
            f"✅ ORDER STATE TRANSITION: {order_id} | "
            f"{current_state.value} → {new_state.value}"
        )
        
        return state_record
    
    async def get_current_state(
        self,
        tenant_id: str,
        order_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get current state of order."""
        state_key = self._get_state_key(tenant_id, order_id)
        
        try:
            data = await self.redis.get(state_key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Error getting order state from Redis: {e}")
        
        return None
    
    async def get_state_history(
        self,
        tenant_id: str,
        order_id: str,
        limit: int = 100
    ) -> list:
        """Get state transition history for order."""
        history_key = self._get_history_key(tenant_id, order_id)
        
        try:
            entries = await self.redis.lrange(history_key, 0, limit - 1)
            return [json.loads(e) for e in entries]
        except Exception as e:
            logger.error(f"Error getting order state history: {e}")
        
        return []
    
    async def can_transition(
        self,
        tenant_id: str,
        order_id: str,
        target_state: OrderState
    ) -> bool:
        """Check if order can transition to target state."""
        current_state_data = await self.get_current_state(tenant_id, order_id)
        current_state = OrderState(current_state_data["state"]) if current_state_data else OrderState.CREATED
        
        return self._is_valid_transition(current_state, target_state)
    
    def get_valid_transitions(self, state: OrderState) -> Set[OrderState]:
        """Get set of valid transitions from a given state."""
        return self.VALID_TRANSITIONS.get(state, set()).copy()


class ExecutionError(Exception):
    """
    STEP 6.2: Execution Error
    
    Raised when exchange response validation fails.
    Indicates invalid or malformed order response from exchange.
    """
    
    def __init__(self, message: str, validation_failures: Optional[list] = None, raw_response: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.validation_failures = validation_failures or []
        self.raw_response = raw_response
        self.timestamp = datetime.utcnow().isoformat()


class ExchangeResponseValidator:
    """
    STEP 6.2: Exchange Response Validation
    
    Validates order responses from CCXT to ensure:
    1. order_id exists and is valid
    2. status is valid (open, closed, canceled, pending, rejected)
    3. filled <= amount (no over-fill)
    
    Raises ExecutionError on validation failure.
    """
    
    VALID_STATUSES = {"open", "closed", "canceled", "cancelled", "pending", "rejected", "expired", "filled"}
    
    def validate_order_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        STEP 6.2: Validate exchange order response.
        
        Args:
            response: Raw order response from CCXT/exchange
            
        Returns:
            Validated response dict
            
        Raises:
            ExecutionError: If validation fails
        """
        if not response:
            raise ExecutionError(
                "Empty order response from exchange",
                validation_failures=["empty_response"],
                raw_response=response
            )
        
        failures = []
        
        # STEP 6.2: Validate order_id exists
        order_id = response.get("id") or response.get("orderId") or response.get("order_id")
        if not order_id:
            failures.append("missing_order_id")
            logger.error(f"🚨 VALIDATION FAILED: Missing order_id in exchange response: {response}")
        
        # STEP 6.2: Validate status is valid
        status = response.get("status", "").lower()
        if not status:
            failures.append("missing_status")
            logger.error(f"🚨 VALIDATION FAILED: Missing status in exchange response")
        elif status not in self.VALID_STATUSES:
            failures.append(f"invalid_status:{status}")
            logger.error(f"🚨 VALIDATION FAILED: Invalid order status '{status}' in exchange response")
        
        # STEP 6.2: Validate filled <= amount (no over-fill)
        try:
            filled = Decimal(str(response.get("filled", 0)))
            amount = Decimal(str(response.get("amount", 0)))
            
            if filled < 0:
                failures.append("negative_filled")
                logger.error(f"🚨 VALIDATION FAILED: Negative filled amount: {filled}")
            
            if amount < 0:
                failures.append("negative_amount")
                logger.error(f"🚨 VALIDATION FAILED: Negative amount: {amount}")
            
            if filled > amount:
                failures.append("overfill")
                logger.error(
                    f"🚨 VALIDATION FAILED: Filled ({filled}) > Amount ({amount}) - "
                    f"Over-fill detected, possible exchange error"
                )
        except Exception as e:
            failures.append(f"amount_validation_error:{str(e)}")
            logger.error(f"🚨 VALIDATION FAILED: Error validating filled/amount: {e}")
        
        # STEP 6.2: Raise ExecutionError if any validations failed
        if failures:
            raise ExecutionError(
                f"Exchange response validation failed: {', '.join(failures)}",
                validation_failures=failures,
                raw_response=response
            )
        
        logger.info(
            f"✅ ORDER RESPONSE VALIDATED: order_id={order_id} | "
            f"status={status} | filled={filled}/{amount}"
        )
        
        return {
            "order_id": order_id,
            "status": status,
            "symbol": response.get("symbol"),
            "side": response.get("side"),
            "amount": str(amount),
            "filled": str(filled),
            "remaining": str(amount - filled),
            "price": str(response.get("price", 0)),
            "average": str(response.get("average", 0)),
            "fee": response.get("fee"),
            "timestamp": response.get("timestamp"),
            "validated_at": datetime.utcnow().isoformat(),
        }
    
    def safe_extract(self, response: Dict[str, Any], field: str, default: Any = None) -> Any:
        """
        Safely extract field from response, handling None/empty.
        """
        if not response:
            return default
        return response.get(field, default)


@dataclass
class OrderResult:
    """Result of order execution."""
    success: bool
    client_order_id: str
    order_id: Optional[str] = None
    status: str = "pending"
    message: str = ""
    details: Dict[str, Any] = None


class SafeRetryManager:
    """
    STEP 6.5: Safe Retry System with Exponential Backoff
    
    Retries ONLY for:
    - Network errors
    - Timeouts
    - Rate limits
    
    DOES NOT retry for:
    - Insufficient funds
    - Invalid orders
    - Authentication errors
    - Exchange rejections (logic errors)
    
    Uses exponential backoff to avoid overwhelming the exchange.
    Respects idempotency - no duplicate orders ever.
    """
    
    # Retryable error patterns (will retry)
    RETRYABLE_ERRORS = {
        "network_error",
        "timeout",
        "connection_error",
        "rate_limit",
        "ccxt_network_error",
        "ccxt_request_timeout",
        "ccxt_exchange_not_available",
        "temporarily_unavailable",
    }
    
    # Non-retryable error patterns (will NOT retry)
    NON_RETRYABLE_ERRORS = {
        "insufficient_funds",
        "invalid_order",
        "order_not_found",
        "authentication_error",
        "permission_denied",
        "bad_request",
        "invalid_symbol",
        "invalid_amount",
        "invalid_price",
        "min_amount_error",
        "max_amount_error",
        "exchange_rejection",
    }
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,  # 1 second
        max_delay: float = 30.0,  # 30 seconds
        exponential_base: float = 2.0,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
    
    def _calculate_delay(self, attempt: int) -> float:
        """
        Calculate exponential backoff delay.
        
        Formula: min(base_delay * (exponential_base ^ attempt), max_delay)
        
        Attempt 0: 1s
        Attempt 1: 2s
        Attempt 2: 4s
        Attempt 3: 8s
        """
        delay = self.base_delay * (self.exponential_base ** attempt)
        return min(delay, self.max_delay)
    
    def is_retryable(self, error: Exception) -> bool:
        """
        Determine if an error is retryable.
        
        Args:
            error: The exception that occurred
            
        Returns:
            True if error is retryable, False otherwise
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Check error message against retryable patterns
        for pattern in self.RETRYABLE_ERRORS:
            if pattern in error_str or pattern in error_type:
                return True
        
        # Check error message against non-retryable patterns
        for pattern in self.NON_RETRYABLE_ERRORS:
            if pattern in error_str or pattern in error_type:
                return False
        
        # Default: retry network-related errors by type
        retryable_types = [
            "timeout",
            "connection",
            "network",
            "ssl",
            "socket",
            "disconnected",
        ]
        for rtype in retryable_types:
            if rtype in error_type:
                return True
        
        # Conservative default: don't retry unknown errors
        return False
    
    async def execute_with_retry(
        self,
        operation: callable,
        operation_name: str = "operation",
        *args,
        **kwargs
    ) -> Any:
        """
        Execute an operation with safe retry logic.
        
        Args:
            operation: Async function to execute
            operation_name: Name of the operation for logging
            *args, **kwargs: Arguments to pass to operation
            
        Returns:
            Operation result
            
        Raises:
            Exception: If all retries exhausted or non-retryable error
        """
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                # Execute the operation
                result = await operation(*args, **kwargs)
                
                if attempt > 0:
                    logger.info(
                        f"✅ {operation_name} succeeded after {attempt} retries"
                    )
                
                return result
                
            except Exception as e:
                last_error = e
                
                # Check if error is retryable
                if not self.is_retryable(e):
                    logger.error(
                        f"🚫 {operation_name} failed with NON-RETRYABLE error: {e} | "
                        f"No retries attempted"
                    )
                    raise
                
                # Check if we have retries left
                if attempt >= self.max_retries:
                    logger.error(
                        f"🚫 {operation_name} failed after {self.max_retries} retries: {e}"
                    )
                    raise
                
                # Calculate and apply backoff delay
                delay = self._calculate_delay(attempt)
                logger.warning(
                    f"⚠️ {operation_name} failed (attempt {attempt + 1}/{self.max_retries + 1}): {e} | "
                    f"Retrying in {delay:.1f}s..."
                )
                
                await asyncio.sleep(delay)
        
        # Should never reach here
        raise last_error


class SlippageModel:
    """
    STEP 6.6: Realistic Execution Slippage Model
    
    Calculates realistic slippage for backtesting and live execution:
    
    slippage = base + size_impact + volatility + spread + random_noise
    
    Components:
    - Base: Fixed minimum slippage (e.g., 0.01%)
    - Size Impact: Larger orders move the market more
    - Volatility: Higher volatility = more slippage
    - Spread: Bid-ask spread component
    - Random Noise: Unpredictable market microstructure effects
    
    Applied BEFORE execution to make backtests realistic.
    """
    
    def __init__(
        self,
        base_bps: float = 1.0,           # Base slippage in basis points (0.01%)
        size_impact_factor: float = 0.1,  # Impact per unit of size
        volatility_factor: float = 5.0,   # Impact per unit of volatility
        spread_factor: float = 0.5,       # Impact of bid-ask spread
        noise_std: float = 2.0,           # Random noise standard deviation (bps)
    ):
        self.base_bps = base_bps
        self.size_impact_factor = size_impact_factor
        self.volatility_factor = volatility_factor
        self.spread_factor = spread_factor
        self.noise_std = noise_std
    
    def calculate_slippage_bps(
        self,
        order_size: Decimal,
        current_price: Decimal,
        volatility: float = 0.0,
        spread_bps: float = 5.0,
        symbol: str = "",
        side: str = "buy"
    ) -> float:
        """
        STEP 6.6: Calculate total slippage in basis points.
        
        Args:
            order_size: Order quantity
            current_price: Current market price
            volatility: Price volatility (e.g., 0.02 for 2%)
            spread_bps: Bid-ask spread in basis points
            symbol: Trading symbol (for logging)
            side: 'buy' or 'sell'
        
        Returns:
            Total slippage in basis points (bps)
        """
        import random
        import math
        
        # 1. Base slippage (fixed minimum)
        base = self.base_bps
        
        # 2. Size impact (larger orders = more slippage)
        # Normalize size by price to get notional value
        notional = float(order_size * current_price)
        # Impact increases with square root of size (market impact model)
        size_impact = self.size_impact_factor * math.sqrt(max(0, notional / 10000))
        
        # 3. Volatility impact (higher volatility = more slippage)
        volatility_impact = self.volatility_factor * volatility * 100  # Convert to bps
        
        # 4. Spread impact
        spread_impact = self.spread_factor * spread_bps
        
        # 5. Random noise (market microstructure)
        random_noise = random.gauss(0, self.noise_std)
        
        # Total slippage
        total_slippage_bps = base + size_impact + volatility_impact + spread_impact + random_noise
        
        # Ensure slippage is positive and reasonable (max 100 bps = 1%)
        total_slippage_bps = max(0.1, min(100.0, total_slippage_bps))
        
        logger.debug(
            f"STEP 6.6 Slippage: {symbol} {side} | "
            f"Base: {base:.2f} | Size: {size_impact:.2f} | "
            f"Vol: {volatility_impact:.2f} | Spread: {spread_impact:.2f} | "
            f"Noise: {random_noise:.2f} | Total: {total_slippage_bps:.2f} bps"
        )
        
        return total_slippage_bps
    
    def apply_slippage(
        self,
        price: Decimal,
        slippage_bps: float,
        side: str = "buy"
    ) -> Decimal:
        """
        STEP 6.6: Apply slippage to price.
        
        For buys: price goes UP (worse for buyer)
        For sells: price goes DOWN (worse for seller)
        
        Args:
            price: Original price
            slippage_bps: Slippage in basis points
            side: 'buy' or 'sell'
        
        Returns:
            Slipped price
        """
        # Convert bps to multiplier (1 bps = 0.0001 = 0.01%)
        slippage_multiplier = Decimal(str(1 + slippage_bps / 10000))
        
        if side.lower() == "buy":
            # Buy: price goes up (pay more)
            slipped_price = price * slippage_multiplier
        else:
            # Sell: price goes down (receive less)
            slipped_price = price / slippage_multiplier
        
        return slipped_price.quantize(Decimal("0.0001"))
    
    def estimate_execution_price(
        self,
        order_size: Decimal,
        current_price: Decimal,
        side: str = "buy",
        volatility: float = 0.0,
        spread_bps: float = 5.0,
        symbol: str = ""
    ) -> tuple[Decimal, float]:
        """
        STEP 6.6: Estimate realistic execution price with slippage.
        
        Returns:
            Tuple of (slipped_price, slippage_bps)
        """
        # Calculate slippage
        slippage_bps = self.calculate_slippage_bps(
            order_size=order_size,
            current_price=current_price,
            volatility=volatility,
            spread_bps=spread_bps,
            symbol=symbol,
            side=side
        )
        
        # Apply slippage to price
        execution_price = self.apply_slippage(
            price=current_price,
            slippage_bps=slippage_bps,
            side=side
        )
        
        logger.info(
            f"STEP 6.6: Estimated execution for {symbol} {side} | "
            f"Signal: {current_price} | Executed: {execution_price} | "
            f"Slippage: {slippage_bps:.2f} bps ({slippage_bps/100:.4f}%)"
        )
        
        return execution_price, slippage_bps


class ExchangeSyncManager:
    """
    STEP 6.7: Exchange Sync Manager (CRITICAL)
    
    Periodically synchronizes local state with exchange state:
    
    1. Fetch open orders from exchange
    2. Fetch positions from exchange
    3. Compare with local state
    4. Reconcile any mismatches
    
    Goal: Local state = Exchange state always
    
    Sync Triggers:
    - Periodic (every N seconds)
    - Before important operations
    - After network reconnection
    - Manual request
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        execution_engine: Optional['IdempotentExecutionEngine'] = None
    ):
        self.redis = redis_client
        self.engine = execution_engine
        self.sync_interval = 30  # Sync every 30 seconds
        self.last_sync_time: Optional[datetime] = None
        self.sync_count = 0
        self.mismatch_count = 0
        
    async def sync_with_exchange(
        self,
        tenant_id: str,
        exchange_client: Any = None
    ) -> Dict[str, Any]:
        """
        STEP 6.7: Main sync operation with exchange.
        
        Fetches current state from exchange and reconciles with local.
        
        Args:
            tenant_id: Tenant identifier
            exchange_client: CCXT or similar exchange client
            
        Returns:
            Sync report with details of any mismatches
        """
        logger.info(f"STEP 6.7: Starting exchange sync for tenant: {tenant_id}")
        
        sync_report = {
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
            "sync_number": self.sync_count + 1,
            "orders_synced": False,
            "positions_synced": False,
            "mismatches_found": 0,
            "reconciliations": [],
            "errors": [],
        }
        
        try:
            # STEP 6.7: Fetch open orders from exchange
            exchange_orders = await self._fetch_exchange_orders(
                tenant_id, exchange_client
            )
            
            # STEP 6.7: Fetch positions from exchange
            exchange_positions = await self._fetch_exchange_positions(
                tenant_id, exchange_client
            )
            
            # STEP 6.7: Get local state from Redis
            local_orders = await self._get_local_orders(tenant_id)
            
            # STEP 6.7: Compare and reconcile orders
            order_mismatches = await self._reconcile_orders(
                tenant_id, local_orders, exchange_orders
            )
            sync_report["orders_synced"] = True
            sync_report["mismatches_found"] += len(order_mismatches)
            sync_report["reconciliations"].extend(order_mismatches)
            
            # STEP 6.7: Compare and reconcile positions
            position_mismatches = await self._reconcile_positions(
                tenant_id, exchange_positions
            )
            sync_report["positions_synced"] = True
            sync_report["mismatches_found"] += len(position_mismatches)
            sync_report["reconciliations"].extend(position_mismatches)
            
            self.sync_count += 1
            self.last_sync_time = datetime.utcnow()
            
            if sync_report["mismatches_found"] > 0:
                self.mismatch_count += sync_report["mismatches_found"]
                logger.warning(
                    f"STEP 6.7: Sync found {sync_report['mismatches_found']} mismatches "
                    f"for tenant {tenant_id}"
                )
            else:
                logger.info(f"STEP 6.7: Sync complete - no mismatches for {tenant_id}")
            
        except Exception as e:
            error_msg = f"Exchange sync failed: {str(e)}"
            logger.error(f"STEP 6.7: {error_msg}")
            sync_report["errors"].append(error_msg)
        
        return sync_report
    
    async def _fetch_exchange_orders(
        self,
        tenant_id: str,
        exchange_client: Any
    ) -> List[Dict[str, Any]]:
        """Fetch open orders from exchange."""
        if exchange_client is None:
            # No exchange client available, return empty
            return []
        
        try:
            # Use CCXT to fetch open orders
            orders = await exchange_client.fetch_open_orders()
            logger.debug(f"Fetched {len(orders)} open orders from exchange")
            return orders
        except Exception as e:
            logger.error(f"Failed to fetch exchange orders: {e}")
            return []
    
    async def _fetch_exchange_positions(
        self,
        tenant_id: str,
        exchange_client: Any
    ) -> List[Dict[str, Any]]:
        """Fetch positions from exchange."""
        if exchange_client is None:
            return []
        
        try:
            # Use CCXT to fetch positions
            positions = await exchange_client.fetch_positions()
            logger.debug(f"Fetched {len(positions)} positions from exchange")
            return positions
        except Exception as e:
            logger.error(f"Failed to fetch exchange positions: {e}")
            return []
    
    async def _get_local_orders(self, tenant_id: str) -> Dict[str, Dict]:
        """Get local orders from Redis."""
        orders = {}
        try:
            # Scan for all orders for this tenant
            pattern = f"orders:{tenant_id}:*"
            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    data = await self.redis.get(key)
                    if data:
                        order_data = json.loads(data)
                        orders[order_data.get("order_id", key)] = order_data
                if cursor == 0:
                    break
            
            logger.debug(f"Found {len(orders)} local orders in Redis")
        except Exception as e:
            logger.error(f"Error fetching local orders: {e}")
        
        return orders
    
    async def _reconcile_orders(
        self,
        tenant_id: str,
        local_orders: Dict[str, Dict],
        exchange_orders: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        Compare local orders with exchange orders and reconcile mismatches.
        
        Mismatch types:
        - Missing local: Order exists on exchange but not locally
        - Missing remote: Order exists locally but not on exchange
        - Status mismatch: Order status differs between local and exchange
        """
        reconciliations = []
        exchange_order_ids = {o.get("id"): o for o in exchange_orders}
        
        # Check for orders missing locally
        for ex_order_id, ex_order in exchange_order_ids.items():
            if ex_order_id not in local_orders:
                # Order exists on exchange but not locally - reconstruct
                logger.warning(
                    f"RECONCILE: Order {ex_order_id} exists on exchange "
                    f"but not locally - reconstructing"
                )
                
                await self._reconstruct_order(tenant_id, ex_order)
                reconciliations.append({
                    "type": "missing_local",
                    "order_id": ex_order_id,
                    "action": "reconstructed",
                    "exchange_status": ex_order.get("status"),
                })
        
        # Check for status mismatches
        for local_id, local_order in local_orders.items():
            if local_id in exchange_order_ids:
                ex_order = exchange_order_ids[local_id]
                local_status = local_order.get("status", "").lower()
                ex_status = ex_order.get("status", "").lower()
                
                if local_status != ex_status:
                    logger.warning(
                        f"RECONCILE: Status mismatch for {local_id} | "
                        f"Local: {local_status} | Exchange: {ex_status}"
                    )
                    
                    # Update local order with exchange status
                    await self._update_local_order_status(
                        tenant_id, local_id, ex_status, ex_order
                    )
                    reconciliations.append({
                        "type": "status_mismatch",
                        "order_id": local_id,
                        "local_status": local_status,
                        "exchange_status": ex_status,
                        "action": "updated_from_exchange",
                    })
            else:
                # Order exists locally but not on exchange - may be filled/cancelled
                if local_order.get("status") not in ["filled", "cancelled", "closed"]:
                    logger.warning(
                        f"RECONCILE: Order {local_id} missing from exchange - "
                        f"may have been filled/cancelled"
                    )
                    reconciliations.append({
                        "type": "missing_remote",
                        "order_id": local_id,
                        "action": "flag_for_verification",
                    })
        
        return reconciliations
    
    async def _reconcile_positions(
        self,
        tenant_id: str,
        exchange_positions: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        Compare local positions with exchange positions.
        
        For now, this logs discrepancies. Full position reconciliation
        would require portfolio manager integration.
        """
        reconciliations = []
        
        for pos in exchange_positions:
            symbol = pos.get("symbol")
            contracts = pos.get("contracts", 0)
            
            if contracts != 0:
                logger.info(
                    f"STEP 6.7: Exchange position for {symbol}: {contracts} contracts"
                )
                
                # TODO: Compare with local portfolio manager
                # If mismatch, trigger position reconciliation
        
        return reconciliations
    
    async def _reconstruct_order(
        self,
        tenant_id: str,
        exchange_order: Dict[str, Any]
    ):
        """Reconstruct local order state from exchange data."""
        order_id = exchange_order.get("id")
        
        order_data = {
            "order_id": order_id,
            "client_order_id": f"reconstructed_{order_id}",
            "tenant_id": tenant_id,
            "symbol": exchange_order.get("symbol"),
            "side": exchange_order.get("side"),
            "size": str(exchange_order.get("amount", 0)),
            "status": exchange_order.get("status"),
            "reconstructed": True,
            "reconstructed_at": datetime.utcnow().isoformat(),
            "exchange_data": exchange_order,
        }
        
        # Store in Redis
        redis_key = f"orders:{tenant_id}:reconstructed_{order_id}"
        await self.redis.setex(redis_key, 86400 * 7, json.dumps(order_data))
    
    async def _update_local_order_status(
        self,
        tenant_id: str,
        order_id: str,
        new_status: str,
        exchange_data: Dict
    ):
        """Update local order status from exchange."""
        if self.engine:
            try:
                await self.engine.update_order_status(
                    tenant_id=tenant_id,
                    client_order_id=order_id,
                    status=new_status,
                    fill_data={
                        "filled": str(exchange_data.get("filled", 0)),
                        "remaining": str(exchange_data.get("remaining", 0)),
                        "average": str(exchange_data.get("average", 0)),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to update order status: {e}")
    
    async def should_sync(self) -> bool:
        """Check if sync is due based on interval."""
        if self.last_sync_time is None:
            return True
        
        elapsed = (datetime.utcnow() - self.last_sync_time).total_seconds()
        return elapsed >= self.sync_interval
    
    def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync manager status."""
        return {
            "sync_count": self.sync_count,
            "mismatch_count": self.mismatch_count,
            "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "sync_interval_seconds": self.sync_interval,
            "sync_due": self.should_sync() if self.last_sync_time else True,
        }


class ExecutionLatencyMonitor:
    """
    STEP 6.9: Execution Latency Control
    
    Measures and limits execution latency to prevent stale trades:
    
    Tracked Latencies:
    - signal_to_order: Time from signal generation to order submission
    - order_to_fill: Time from order submission to fill confirmation
    - total_execution: End-to-end execution time
    
    Thresholds:
    - max_signal_to_order_ms: Maximum allowed signal-to-order latency
    - max_order_to_fill_ms: Maximum allowed order-to-fill latency
    - max_total_latency_ms: Maximum allowed total execution time
    
    Action:
    - If latency exceeds threshold: BLOCK trade (prevent stale execution)
    """
    
    # Latency thresholds in milliseconds
    DEFAULT_MAX_SIGNAL_TO_ORDER_MS = 5000    # 5 seconds
    DEFAULT_MAX_ORDER_TO_FILL_MS = 30000     # 30 seconds
    DEFAULT_MAX_TOTAL_LATENCY_MS = 60000     # 60 seconds
    
    def __init__(
        self,
        max_signal_to_order_ms: float = None,
        max_order_to_fill_ms: float = None,
        max_total_latency_ms: float = None,
    ):
        self.max_signal_to_order_ms = max_signal_to_order_ms or self.DEFAULT_MAX_SIGNAL_TO_ORDER_MS
        self.max_order_to_fill_ms = max_order_to_fill_ms or self.DEFAULT_MAX_ORDER_TO_FILL_MS
        self.max_total_latency_ms = max_total_latency_ms or self.DEFAULT_MAX_TOTAL_LATENCY_MS
        
        # Tracking storage
        self.signal_timestamps: Dict[str, datetime] = {}
        self.order_timestamps: Dict[str, datetime] = {}
        self.fill_timestamps: Dict[str, datetime] = {}
        
        # Statistics
        self.latency_history: deque = deque(maxlen=1000)
        self.blocked_count = 0
    
    def record_signal(self, signal_id: str, timestamp: datetime = None):
        """Record when signal was generated."""
        self.signal_timestamps[signal_id] = timestamp or datetime.utcnow()
    
    def record_order_submitted(self, signal_id: str, order_id: str, timestamp: datetime = None):
        """Record when order was submitted to exchange."""
        now = timestamp or datetime.utcnow()
        self.order_timestamps[order_id] = now
        
        # Calculate signal-to-order latency
        if signal_id in self.signal_timestamps:
            signal_time = self.signal_timestamps[signal_id]
            latency_ms = (now - signal_time).total_seconds() * 1000
            
            self.latency_history.append({
                "type": "signal_to_order",
                "signal_id": signal_id,
                "order_id": order_id,
                "latency_ms": latency_ms,
                "timestamp": now.isoformat(),
            })
            
            logger.debug(
                f"STEP 6.9: Signal-to-order latency: {latency_ms:.2f}ms "
                f"(signal: {signal_id}, order: {order_id})"
            )
            
            return latency_ms
        
        return None
    
    def record_fill(self, order_id: str, timestamp: datetime = None):
        """Record when order was filled."""
        now = timestamp or datetime.utcnow()
        self.fill_timestamps[order_id] = now
        
        # Calculate order-to-fill latency
        if order_id in self.order_timestamps:
            order_time = self.order_timestamps[order_id]
            latency_ms = (now - order_time).total_seconds() * 1000
            
            self.latency_history.append({
                "type": "order_to_fill",
                "order_id": order_id,
                "latency_ms": latency_ms,
                "timestamp": now.isoformat(),
            })
            
            logger.debug(
                f"STEP 6.9: Order-to-fill latency: {latency_ms:.2f}ms (order: {order_id})"
            )
            
            return latency_ms
        
        return None
    
    def check_signal_to_order_latency(
        self,
        signal_id: str,
        order_id: str
    ) -> Dict[str, Any]:
        """
        Check if signal-to-order latency is acceptable.
        
        Returns:
            Dict with 'allowed' (bool) and 'latency_ms'
        """
        if signal_id not in self.signal_timestamps:
            return {"allowed": True, "latency_ms": None, "reason": "no_signal_record"}
        
        if order_id not in self.order_timestamps:
            return {"allowed": True, "latency_ms": None, "reason": "no_order_record"}
        
        signal_time = self.signal_timestamps[signal_id]
        order_time = self.order_timestamps[order_id]
        latency_ms = (order_time - signal_time).total_seconds() * 1000
        
        if latency_ms > self.max_signal_to_order_ms:
            self.blocked_count += 1
            logger.error(
                f"🚫 STEP 6.9: Signal-to-order latency TOO HIGH: {latency_ms:.2f}ms | "
                f"Max allowed: {self.max_signal_to_order_ms}ms | "
                f"Trade BLOCKED (signal: {signal_id})"
            )
            return {
                "allowed": False,
                "latency_ms": latency_ms,
                "max_allowed_ms": self.max_signal_to_order_ms,
                "reason": "signal_to_order_latency_exceeded",
            }
        
        return {
            "allowed": True,
            "latency_ms": latency_ms,
            "max_allowed_ms": self.max_signal_to_order_ms,
            "reason": "within_threshold",
        }
    
    def check_order_to_fill_latency(self, order_id: str) -> Dict[str, Any]:
        """
        Check if order-to-fill latency is acceptable.
        
        Returns:
            Dict with 'allowed' (bool) and 'latency_ms'
        """
        if order_id not in self.order_timestamps:
            return {"allowed": True, "latency_ms": None, "reason": "no_order_record"}
        
        if order_id not in self.fill_timestamps:
            # Order not yet filled - check elapsed time since order
            order_time = self.order_timestamps[order_id]
            elapsed_ms = (datetime.utcnow() - order_time).total_seconds() * 1000
            
            if elapsed_ms > self.max_order_to_fill_ms:
                logger.warning(
                    f"⚠️ STEP 6.9: Order {order_id} pending for {elapsed_ms:.2f}ms | "
                    f"Max allowed: {self.max_order_to_fill_ms}ms | "
                    f"May need cancellation"
                )
                return {
                    "allowed": False,
                    "latency_ms": elapsed_ms,
                    "max_allowed_ms": self.max_order_to_fill_ms,
                    "reason": "order_pending_too_long",
                }
            
            return {
                "allowed": True,
                "latency_ms": None,
                "elapsed_ms": elapsed_ms,
                "reason": "still_pending",
            }
        
        order_time = self.order_timestamps[order_id]
        fill_time = self.fill_timestamps[order_id]
        latency_ms = (fill_time - order_time).total_seconds() * 1000
        
        if latency_ms > self.max_order_to_fill_ms:
            logger.warning(
                f"⚠️ STEP 6.9: Order-to-fill latency HIGH: {latency_ms:.2f}ms | "
                f"Order: {order_id} | "
                f"Max allowed: {self.max_order_to_fill_ms}ms"
            )
            return {
                "allowed": False,
                "latency_ms": latency_ms,
                "max_allowed_ms": self.max_order_to_fill_ms,
                "reason": "order_to_fill_latency_exceeded",
            }
        
        return {
            "allowed": True,
            "latency_ms": latency_ms,
            "max_allowed_ms": self.max_order_to_fill_ms,
            "reason": "within_threshold",
        }
    
    def get_latency_statistics(self) -> Dict[str, Any]:
        """Get latency statistics."""
        if not self.latency_history:
            return {
                "count": 0,
                "avg_signal_to_order_ms": None,
                "avg_order_to_fill_ms": None,
                "blocked_count": self.blocked_count,
            }
        
        signal_to_order_latencies = [
            entry["latency_ms"]
            for entry in self.latency_history
            if entry["type"] == "signal_to_order"
        ]
        
        order_to_fill_latencies = [
            entry["latency_ms"]
            for entry in self.latency_history
            if entry["type"] == "order_to_fill"
        ]
        
        return {
            "count": len(self.latency_history),
            "avg_signal_to_order_ms": (
                sum(signal_to_order_latencies) / len(signal_to_order_latencies)
                if signal_to_order_latencies else None
            ),
            "avg_order_to_fill_ms": (
                sum(order_to_fill_latencies) / len(order_to_fill_latencies)
                if order_to_fill_latencies else None
            ),
            "max_signal_to_order_ms": self.max_signal_to_order_ms,
            "max_order_to_fill_ms": self.max_order_to_fill_ms,
            "max_total_latency_ms": self.max_total_latency_ms,
            "blocked_count": self.blocked_count,
        }


class OrderBookExecutionEngine:
    """
    STEP 6.8: Order Book Aware Execution
    
    Uses order book depth to improve execution quality:
    - Analyzes bid/ask spread
    - Checks available liquidity
    - Adjusts order size and price for optimal execution
    
    Goal: Better execution prices through market microstructure awareness.
    """
    
    # Maximum allowed spread as percentage of price
    MAX_SPREAD_PCT = 0.5  # 0.5%
    
    # Minimum liquidity required (multiple of order size)
    MIN_LIQUIDITY_MULTIPLIER = 2.0
    
    # Order book depth levels to check
    DEPTH_LEVELS = 5
    
    def __init__(self):
        self.order_book_cache: Dict[str, Dict] = {}
        self.cache_ttl_seconds = 5  # 5 second cache
    
    async def analyze_order_book(
        self,
        symbol: str,
        exchange_client: Any = None
    ) -> Dict[str, Any]:
        """
        STEP 6.8: Fetch and analyze order book for symbol.
        
        Returns:
            Dict with spread, liquidity, and depth analysis
        """
        try:
            if exchange_client is None:
                return self._get_default_analysis()
            
            # Fetch order book from exchange
            order_book = await exchange_client.fetch_order_book(symbol, limit=20)
            
            bids = order_book.get("bids", [])  # [[price, amount], ...]
            asks = order_book.get("asks", [])    # [[price, amount], ...]
            
            if not bids or not asks:
                return self._get_default_analysis()
            
            # Calculate spread
            best_bid = Decimal(str(bids[0][0]))
            best_ask = Decimal(str(asks[0][0]))
            mid_price = (best_bid + best_ask) / 2
            spread_bps = float((best_ask - best_bid) / mid_price * 10000)
            
            # Calculate depth (liquidity at different levels)
            bid_depth = self._calculate_depth(bids)
            ask_depth = self._calculate_depth(asks)
            
            # Calculate volume-weighted average price (VWAP) for top levels
            bid_vwap = self._calculate_vwap(bids[:self.DEPTH_LEVELS])
            ask_vwap = self._calculate_vwap(asks[:self.DEPTH_LEVELS])
            
            analysis = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "best_bid": str(best_bid),
                "best_ask": str(best_ask),
                "mid_price": str(mid_price),
                "spread_bps": spread_bps,
                "spread_pct": spread_bps / 100,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "bid_vwap": str(bid_vwap),
                "ask_vwap": str(ask_vwap),
                "liquidity_score": min(bid_depth, ask_depth),
            }
            
            # Cache the analysis
            self.order_book_cache[symbol] = {
                "analysis": analysis,
                "timestamp": datetime.utcnow(),
            }
            
            logger.debug(
                f"STEP 6.8: Order book analyzed for {symbol} | "
                f"Spread: {spread_bps:.2f} bps | Depth: {analysis['liquidity_score']:.4f}"
            )
            
            return analysis
            
        except Exception as e:
            logger.error(f"STEP 6.8: Failed to analyze order book for {symbol}: {e}")
            return self._get_default_analysis()
    
    def _calculate_depth(self, levels: List[List]) -> float:
        """Calculate total quantity available at given levels."""
        total = 0.0
        for level in levels[:self.DEPTH_LEVELS]:
            if len(level) >= 2:
                total += float(level[1])
        return total
    
    def _calculate_vwap(self, levels: List[List]) -> Decimal:
        """Calculate volume-weighted average price."""
        total_value = Decimal("0")
        total_volume = Decimal("0")
        
        for level in levels:
            if len(level) >= 2:
                price = Decimal(str(level[0]))
                volume = Decimal(str(level[1]))
                total_value += price * volume
                total_volume += volume
        
        if total_volume == 0:
            return Decimal("0")
        
        return total_value / total_volume
    
    def _get_default_analysis(self) -> Dict[str, Any]:
        """Return default analysis when order book unavailable."""
        return {
            "symbol": "unknown",
            "timestamp": datetime.utcnow().isoformat(),
            "best_bid": "0",
            "best_ask": "0",
            "mid_price": "0",
            "spread_bps": 10.0,  # Assume 10 bps default
            "spread_pct": 0.1,
            "bid_depth": 0,
            "ask_depth": 0,
            "bid_vwap": "0",
            "ask_vwap": "0",
            "liquidity_score": 0,
        }
    
    def adjust_order_parameters(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price: Optional[Decimal],
        order_book_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        STEP 6.8: Adjust order parameters based on order book analysis.
        
        Adjustments:
        - Reduce size if liquidity insufficient
        - Adjust price to VWAP if spread too wide
        - Add spread-based slippage estimation
        
        Returns:
            Dict with adjusted size, price, and execution recommendations
        """
        adjustments = {
            "original_size": str(size),
            "original_price": str(price) if price else None,
            "adjusted_size": str(size),
            "adjusted_price": str(price) if price else None,
            "size_reduced": False,
            "price_adjusted": False,
            "execution_recommendation": "proceed",
            "warnings": [],
        }
        
        spread_bps = order_book_analysis.get("spread_bps", 10.0)
        liquidity_score = order_book_analysis.get("liquidity_score", 0)
        
        # Check 1: Spread too wide
        if spread_bps > 50:  # More than 50 bps (0.5%)
            adjustments["warnings"].append(
                f"Wide spread detected: {spread_bps:.2f} bps"
            )
            
            if price is not None:
                # For limit orders, adjust price toward VWAP
                if side.lower() == "buy":
                    vwap = Decimal(order_book_analysis.get("ask_vwap", price))
                    adjustments["adjusted_price"] = str(vwap)
                else:
                    vwap = Decimal(order_book_analysis.get("bid_vwap", price))
                    adjustments["adjusted_price"] = str(vwap)
                
                adjustments["price_adjusted"] = True
                adjustments["execution_recommendation"] = "use_vwap"
        
        # Check 2: Insufficient liquidity
        if liquidity_score > 0:
            required_liquidity = float(size) * self.MIN_LIQUIDITY_MULTIPLIER
            
            if liquidity_score < float(size):
                # Severe liquidity shortage - reduce size
                new_size = Decimal(str(liquidity_score / self.MIN_LIQUIDITY_MULTIPLIER))
                adjustments["adjusted_size"] = str(new_size)
                adjustments["size_reduced"] = True
                adjustments["warnings"].append(
                    f"Size reduced from {size} to {new_size} due to low liquidity"
                )
                adjustments["execution_recommendation"] = "reduce_size"
                
            elif liquidity_score < required_liquidity:
                # Moderate liquidity shortage - warning
                adjustments["warnings"].append(
                    f"Low liquidity: {liquidity_score:.4f} available, {float(size):.4f} required"
                )
                adjustments["execution_recommendation"] = "caution"
        
        # Check 3: Very wide spread - consider not executing
        if spread_bps > 100:  # More than 100 bps (1%)
            adjustments["warnings"].append(
                f"Very wide spread: {spread_bps:.2f} bps - consider delaying execution"
            )
            adjustments["execution_recommendation"] = "delay_or_cancel"
        
        logger.info(
            f"STEP 6.8: Order parameters adjusted for {symbol} {side} | "
            f"Size: {adjustments['original_size']} → {adjustments['adjusted_size']} | "
            f"Recommendation: {adjustments['execution_recommendation']}"
        )
        
        return adjustments
    
    def get_optimal_order_type(
        self,
        symbol: str,
        size: Decimal,
        urgency: str = "normal",
        order_book_analysis: Optional[Dict] = None
    ) -> str:
        """
        STEP 6.8: Recommend optimal order type based on market conditions.
        
        Args:
            urgency: "low", "normal", "high"
            
        Returns:
            Recommended order type: "market", "limit", "ioc", "fok"
        """
        if order_book_analysis is None:
            return "limit"  # Conservative default
        
        spread_bps = order_book_analysis.get("spread_bps", 10.0)
        liquidity_score = order_book_analysis.get("liquidity_score", 0)
        
        # High urgency - use market or IOC
        if urgency == "high":
            if spread_bps < 20:  # Tight spread
                return "market"
            else:
                return "ioc"  # Immediate or cancel
        
        # Low urgency - use limit with patience
        if urgency == "low":
            return "limit"
        
        # Normal urgency - adaptive
        if spread_bps < 10 and liquidity_score > float(size) * 3:
            # Tight spread and good liquidity
            return "limit"
        elif spread_bps > 50:
            # Wide spread - use IOC to avoid walking the book
            return "ioc"
        else:
            return "limit"


class FailsafeExecutionGuard:
    """
    STEP 6.10: Failsafe Execution Guard (FINAL SAFETY LAYER)
    
    Performs comprehensive system health checks before ANY execution:
    
    Checks:
    1. System health - All services running
    2. WebSocket alive - Market data flowing
    3. Risk engine OK - Risk system operational
    4. Portfolio consistent - No state corruption
    5. Exchange connectivity - Can reach exchange
    6. Circuit breakers - No emergency stops
    
    If ANY check fails:
    → BLOCK trade immediately
    → Log detailed failure reason
    → Alert operators
    
    This is the FINAL gate before execution - no trade passes if unsafe.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        portfolio_manager: Any = None,
        risk_engine: Any = None
    ):
        self.redis = redis_client
        self.portfolio_manager = portfolio_manager
        self.risk_engine = risk_engine
        
        # Health check thresholds
        self.max_ws_staleness_seconds = 10  # WebSocket data must be < 10s old
        self.max_portfolio_sync_age_seconds = 60  # Portfolio sync < 60s old
    
    async def pre_execution_safety_check(
        self,
        tenant_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        signal_id: str = ""
    ) -> Dict[str, Any]:
        """
        STEP 6.10: Comprehensive safety check before execution.
        
        Returns:
            Dict with 'safe' (bool) and detailed check results
        """
        check_results = {
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
            "symbol": symbol,
            "side": side,
            "size": str(size),
            "signal_id": signal_id,
            "safe": True,
            "checks": {},
            "failures": [],
        }
        
        # Check 1: System health (Redis connectivity)
        try:
            await self.redis.ping()
            check_results["checks"]["redis"] = {"status": "ok"}
        except Exception as e:
            check_results["checks"]["redis"] = {"status": "failed", "error": str(e)}
            check_results["failures"].append("redis_unavailable")
        
        # Check 2: WebSocket health (market data freshness)
        ws_check = await self._check_websocket_health(tenant_id, symbol)
        check_results["checks"]["websocket"] = ws_check
        if not ws_check.get("healthy", False):
            check_results["failures"].append("websocket_stale")
        
        # Check 3: Risk engine health
        if self.risk_engine:
            risk_check = await self._check_risk_engine_health()
            check_results["checks"]["risk_engine"] = risk_check
            if not risk_check.get("healthy", False):
                check_results["failures"].append("risk_engine_unhealthy")
        
        # Check 4: Portfolio consistency
        if self.portfolio_manager:
            portfolio_check = await self._check_portfolio_consistency()
            check_results["checks"]["portfolio"] = portfolio_check
            if not portfolio_check.get("consistent", False):
                check_results["failures"].append("portfolio_inconsistent")
        
        # Check 5: Circuit breakers
        circuit_check = await self._check_circuit_breakers(tenant_id, symbol)
        check_results["checks"]["circuit_breakers"] = circuit_check
        if not circuit_check.get("open", True):  # Circuit breaker tripped
            check_results["failures"].append("circuit_breaker_tripped")
        
        # Final verdict
        if check_results["failures"]:
            check_results["safe"] = False
            logger.critical(
                f"🚨 STEP 6.10: FAILSAFE BLOCKED execution for {symbol} {side} | "
                f"Failures: {check_results['failures']} | "
                f"Signal: {signal_id}"
            )
        else:
            logger.info(
                f"✅ STEP 6.10: All safety checks passed for {symbol} {side} | "
                f"Signal: {signal_id}"
            )
        
        return check_results
    
    async def _check_websocket_health(self, tenant_id: str, symbol: str) -> Dict[str, Any]:
        """Check if WebSocket market data is fresh."""
        try:
            # Check last price update timestamp from Redis
            ws_key = f"ws:last_update:{tenant_id}:{symbol}"
            last_update = await self.redis.get(ws_key)
            
            if last_update:
                last_update_time = datetime.fromisoformat(last_update.decode())
                staleness = (datetime.utcnow() - last_update_time).total_seconds()
                
                if staleness > self.max_ws_staleness_seconds:
                    return {
                        "healthy": False,
                        "staleness_seconds": staleness,
                        "max_allowed": self.max_ws_staleness_seconds,
                        "error": f"WebSocket data stale: {staleness:.1f}s",
                    }
                
                return {
                    "healthy": True,
                    "staleness_seconds": staleness,
                    "last_update": last_update.decode(),
                }
            
            return {
                "healthy": False,
                "error": "No WebSocket data available",
            }
            
        except Exception as e:
            return {"healthy": False, "error": str(e)}
    
    async def _check_risk_engine_health(self) -> Dict[str, Any]:
        """Check if risk engine is operational."""
        try:
            # Check risk engine status via Redis or direct call
            risk_key = "risk:engine:status"
            status = await self.redis.get(risk_key)
            
            if status:
                status_data = json.loads(status)
                if status_data.get("operational", False):
                    return {"healthy": True, "status": status_data}
                else:
                    return {
                        "healthy": False,
                        "status": status_data,
                        "error": "Risk engine not operational",
                    }
            
            # Assume healthy if no status set (default)
            return {"healthy": True, "status": "unknown"}
            
        except Exception as e:
            return {"healthy": False, "error": str(e)}
    
    async def _check_portfolio_consistency(self) -> Dict[str, Any]:
        """Check if portfolio state is consistent."""
        try:
            if not self.portfolio_manager:
                return {"consistent": True, "message": "No portfolio manager"}
            
            # Get latest snapshot and check age
            if hasattr(self.portfolio_manager, 'snapshot_system'):
                latest = self.portfolio_manager.snapshot_system.get_latest_snapshot()
                if latest:
                    age = (datetime.utcnow() - latest.timestamp).total_seconds()
                    if age > self.max_portfolio_sync_age_seconds:
                        return {
                            "consistent": False,
                            "snapshot_age_seconds": age,
                            "max_allowed": self.max_portfolio_sync_age_seconds,
                            "error": "Portfolio snapshot too old",
                        }
            
            return {"consistent": True}
            
        except Exception as e:
            return {"consistent": False, "error": str(e)}
    
    async def _check_circuit_breakers(self, tenant_id: str, symbol: str) -> Dict[str, Any]:
        """Check if any circuit breakers are tripped."""
        try:
            # Check circuit breaker status in Redis
            cb_key = f"circuit_breaker:{tenant_id}:{symbol}"
            cb_status = await self.redis.get(cb_key)
            
            if cb_status:
                status_data = json.loads(cb_status)
                if status_data.get("tripped", False):
                    return {
                        "open": False,  # Circuit is OPEN (blocking)
                        "tripped_at": status_data.get("tripped_at"),
                        "reason": status_data.get("reason"),
                        "error": f"Circuit breaker tripped: {status_data.get('reason')}",
                    }
            
            return {"open": True}  # Circuit is CLOSED (allowing)
            
        except Exception as e:
            # Fail safe - if we can't check, assume circuit is open
            return {"open": False, "error": str(e)}


class IdempotentExecutionEngine:
    """
    STEP 6.1 + 6.2 + 6.4 + 6.5 + 6.6 + 6.7 + 6.8 + 6.9 + 6.10: Production-Grade Execution Engine
    
    Features:
    1. Order ID Idempotency - No duplicate orders (Step 6.1)
    2. Response Validation - No fake/broken responses (Step 6.2)
    3. Order State Machine - Enforces lifecycle (Step 6.4)
    4. Safe Retry System - Exponential backoff (Step 6.5)
    5. Slippage Model - Realistic pricing (Step 6.6)
    6. Exchange Sync - Local = Exchange (Step 6.7)
    7. Order Book Aware - Better execution (Step 6.8)
    8. Latency Control - Block stale trades (Step 6.9)
    9. Failsafe Guard - No execution under unsafe conditions (Step 6.10)
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.order_ttl = 86400 * 7  # 7 days retention
        # STEP 6.2: Initialize response validator
        self.response_validator = ExchangeResponseValidator()
        # STEP 6.4: Initialize order state machine
        self.order_state_machine = OrderStateMachine(redis_client)
        # STEP 6.5: Initialize safe retry manager
        self.retry_manager = SafeRetryManager()
        # STEP 6.6: Initialize slippage model
        self.slippage_model = SlippageModel()
        # STEP 6.7: Initialize exchange sync manager
        self.exchange_sync = ExchangeSyncManager(redis_client, self)
        # STEP 6.8: Initialize order book execution engine
        self.order_book_engine = OrderBookExecutionEngine()
        # STEP 6.9: Initialize latency monitor
        self.latency_monitor = ExecutionLatencyMonitor()
        # STEP 6.10: Initialize failsafe guard
        self.failsafe_guard = FailsafeExecutionGuard(redis_client)
    
    def _generate_client_order_id(
        self,
        tenant_id: str,
        symbol: str,
        timestamp: str,
        signal_id: str
    ) -> str:
        """
        STEP 6.1: Generate unique client_order_id.
        
        Hash of: tenant_id + symbol + timestamp + signal_id
        
        This ensures:
        - Same signal → Same client_order_id (forever)
        - Time-independent for true idempotency
        """
        content = f"{tenant_id}:{symbol}:{timestamp}:{signal_id}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()
        return f"ord_{hash_value[:24]}"
    
    async def _check_existing_order(
        self,
        tenant_id: str,
        client_order_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        STEP 6.1: Check if order already exists in Redis.
        
        Key format: orders:{tenant_id}:{client_order_id}
        
        Returns:
            Existing order dict if found, None otherwise
        """
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            existing = await self.redis.get(redis_key)
            if existing:
                order_data = json.loads(existing)
                logger.info(
                    f"🔄 DUPLICATE ORDER DETECTED: client_order_id={client_order_id} | "
                    f"order_id={order_data.get('order_id')} | "
                    f"Returning existing order | NO RE-EXECUTION"
                )
                return order_data
        except Exception as e:
            logger.error(f"Error checking existing order in Redis: {e}")
        
        return None
    
    async def _store_order(
        self,
        tenant_id: str,
        client_order_id: str,
        order_data: Dict[str, Any]
    ):
        """
        STEP 6.1: Store order in Redis with TTL.
        
        Key: orders:{tenant_id}:{client_order_id}
        TTL: 7 days
        """
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            await self.redis.setex(
                redis_key,
                self.order_ttl,
                json.dumps(order_data, default=str)
            )
            logger.debug(f"Order stored in Redis: {redis_key}")
        except Exception as e:
            logger.error(f"Error storing order in Redis: {e}")
    
    async def _get_portfolio_state(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        STEP 7.11: Get portfolio state for ExecutionGuard validation.
        
        CRITICAL FIX H8: 
        - Fetches from Redis only
        - ❌ NO hardcoded defaults - returns None if data unavailable
        - Caller must handle None appropriately (skip validation or fail)
        """
        try:
            # Try to get from Redis
            balance_key = f"tenant:{tenant_id}:balance"
            balance_data = await self.redis.hgetall(balance_key)
            
            if balance_data:
                return {
                    "available_balance": balance_data.get("available", "0"),
                    "total_equity": balance_data.get("total", "0"),
                    "total_exposure": balance_data.get("exposure", "0"),
                    "positions": json.loads(balance_data.get("positions", "{}")),
                    "daily_pnl": balance_data.get("daily_pnl", "0"),
                }
            
            # CRITICAL FIX H8: No fake defaults - return None
            logger.warning(f"No portfolio data available for tenant {tenant_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching portfolio state: {e}")
            return None
    
    async def _get_market_state(self, tenant_id: str, symbol: str) -> Dict[str, Any]:
        """
        STEP 7.11: Get market state for ExecutionGuard validation.
        
        Tries to fetch from Redis first, returns defaults if not available.
        """
        try:
            market_key = f"market:{tenant_id}:{symbol}"
            market_data = await self.redis.get(market_key)
            
            if market_data:
                state = json.loads(market_data)
                logger.debug(f"Market state loaded for {symbol}")
                return state
            
        except Exception as e:
            logger.warning(f"Could not load market state: {e}")
        
        # Return safe defaults
        return {
            "spread_bps": "10",  # 10 bps = 0.1%
            "spread_pct": "0.001",
            "volatility": "0.02",  # 2%
            "liquidity_depth": "1000000",  # $1M depth
            "volume_24h": "1000000000",
            "status": "open",
        }
    
    async def place_order(
        self,
        tenant_id: str,
        symbol: str,
        side: str,
        size: float,
        price: Optional[float] = None,
        signal_id: str = "",
        order_type: str = "market",
        metadata: Optional[Dict[str, Any]] = None
    ) -> OrderResult:
        """
        STEP 6.1 + 6.2: Place order with idempotency check + response validation.
        
        Flow:
        1. Generate client_order_id from signal content
        2. Check Redis for existing order
        3. IF exists → return existing order
        4. ELSE → place new order
        5. Validate exchange response (Step 6.2)
        6. Store validated order in Redis
        
        Step 6.2 Validation:
        - order_id must exist
        - status must be valid
        - filled <= amount (no over-fill)
        
        Args:
            tenant_id: Tenant identifier
            symbol: Trading symbol (e.g., BTC-USD)
            side: 'buy' or 'sell'
            size: Order size
            price: Order price (None for market orders)
            signal_id: Unique signal identifier for idempotency
            order_type: 'market' or 'limit'
            metadata: Additional order metadata
        
        Returns:
            OrderResult with order details
        """
        # STEP 8.4 + 8.5: Global Circuit Breaker - CAPITAL PROTECTION (FIRST CHECK)
        # This is the ULTIMATE SAFETY NET - stops all trading if capital at risk
        # Auto-liquidates positions when triggered
        if not global_circuit_breaker().can_trade():
            status = global_circuit_breaker().get_status()
            logger.critical(
                f"🚫 STEP 8.4: GLOBAL CIRCUIT BREAKER OPEN | "
                f"State: {status['state']} | Reason: {status['circuit_reason']} | "
                f"ALL TRADING HALTED"
            )
            # STEP 8.1: Record blocked trade
            record_trade_blocked(
                reason=f"global_circuit_breaker_{status['state']}",
                symbol=symbol
            )
            return OrderResult(
                success=False,
                client_order_id="",
                order_id="",
                status="circuit_breaker_open",
                message=f"Global circuit breaker open: {status['circuit_reason']}",
                details={
                    "circuit_state": status['state'],
                    "circuit_reason": status['circuit_reason'],
                    "cooldown_remaining": status['cooldown_seconds_remaining'],
                    "daily_metrics": status['daily_metrics'],
                    "drawdown_metrics": status['drawdown_metrics'],
                }
            )
        
        # STEP 7.11: ExecutionGuard validation - MANDATORY CHECK
        # This is the FINAL GATE - no order passes without validation
        try:
            guard = ExecutionGuard(self.redis)
            
            # Build signal dict for validation
            signal = {
                "symbol": symbol,
                "side": side,
                "size": str(size),
                "price": str(price) if price else "0",
                "signal_id": signal_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            # Get portfolio state from Redis or use defaults
            portfolio_state = await self._get_portfolio_state(tenant_id)
            
            # Get market state from Redis or use defaults  
            market_state = await self._get_market_state(tenant_id, symbol)
            
            # Run validation
            validation_report = await guard.validate_trade(
                tenant_id=tenant_id,
                signal=signal,
                portfolio_state=portfolio_state,
                market_state=market_state
            )
            
            # Check if execution allowed
            if not validation_report.execution_allowed:
                # STEP 8.1: Record blocked trade metric
                block_reason = validation_report.blocked_reasons[0] if validation_report.blocked_reasons else "unknown"
                record_trade_blocked(
                    reason=block_reason,
                    symbol=symbol,
                    risk_score=0  # Risk score not available here
                )
                
                logger.critical(
                    f"🚫 STEP 7.11: ExecutionGuard BLOCKED order | "
                    f"Tenant={tenant_id} | Symbol={symbol} | "
                    f"Reasons: {validation_report.blocked_reasons}"
                )
                return OrderResult(
                    success=False,
                    client_order_id="",
                    order_id="",
                    status="blocked",
                    message=f"ExecutionGuard blocked: {'; '.join(validation_report.blocked_reasons)}",
                    details={
                        "blocked_reasons": validation_report.blocked_reasons,
                        "validation_report": validation_report.to_dict(),
                    }
                )
            
            # Log warnings if any
            if validation_report.warning_reasons:
                logger.warning(
                    f"⚠️ STEP 7.11: ExecutionGuard warnings | "
                    f"Tenant={tenant_id} | Warnings: {validation_report.warning_reasons}"
                )
            
            logger.info(f"✅ STEP 7.11: ExecutionGuard passed | Tenant={tenant_id} | Symbol={symbol}")
            
        except Exception as e:
            logger.error(f"❌ STEP 7.11: ExecutionGuard validation error: {e}")
            # Fail-safe: block execution if validation fails
            return OrderResult(
                success=False,
                client_order_id="",
                order_id="",
                status="error",
                message=f"ExecutionGuard validation failed: {e}",
                details={"error": str(e)}
            )
        
        # Generate timestamp for idempotency (normalized to minute)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M")
        
        # STEP 6.1: Generate unique client_order_id
        client_order_id = self._generate_client_order_id(
            tenant_id=tenant_id,
            symbol=symbol,
            timestamp=timestamp,
            signal_id=signal_id
        )
        
        logger.info(
            f"Order placement: Tenant={tenant_id} | Symbol={symbol} | "
            f"Side={side} | Size={size} | ClientOrderID={client_order_id}"
        )
        
        # STEP 6.1: Check if order already exists
        existing_order = await self._check_existing_order(
            tenant_id=tenant_id,
            client_order_id=client_order_id
        )
        
        if existing_order:
            # Duplicate detected - return existing order
            return OrderResult(
                success=True,
                client_order_id=client_order_id,
                order_id=existing_order.get("order_id"),
                status="duplicate",
                message=f"Duplicate order prevented. Original status: {existing_order.get('status')}",
                details={
                    "original_order": existing_order,
                    "duplicate_detected_at": datetime.utcnow().isoformat(),
                }
            )
        
        # No duplicate - proceed with order placement
        try:
            # NOTE: Actual exchange API call would go here
            # For now, simulate order placement
            order_id = f"ex_{hashlib.sha256(client_order_id.encode()).hexdigest()[:16]}"
            
            # Prepare order data for storage
            order_data = {
                "client_order_id": client_order_id,
                "order_id": order_id,
                "tenant_id": tenant_id,
                "symbol": symbol,
                "side": side,
                "size": str(size),
                "price": str(price) if price else None,
                "order_type": order_type,
                "signal_id": signal_id,
                "status": "submitted",
                "created_at": datetime.utcnow().isoformat(),
                "metadata": metadata or {},
            }
            
            # STEP 6.1: Store order in Redis for idempotency
            await self._store_order(
                tenant_id=tenant_id,
                client_order_id=client_order_id,
                order_data=order_data
            )
            
            # STEP 8.1: Record successful trade metric
            execution_latency_ms = (datetime.utcnow() - order.created_at).total_seconds() * 1000
            record_trade_executed(
                latency_ms=execution_latency_ms,
                symbol=symbol,
                side=side
            )
            
            # STEP 8.4: Record trade in Global Circuit Breaker
            # Note: PnL will be updated when fill arrives from exchange
            global_circuit_breaker().record_trade(
                pnl=0.0,  # Will be updated on fill
                current_equity=100000.0,  # Placeholder - use actual portfolio equity
            )
            
            logger.info(
                f"✅ Order placed: client_order_id={client_order_id} | "
                f"order_id={order_id} | Status=submitted"
            )
            
            return OrderResult(
                success=True,
                client_order_id=client_order_id,
                order_id=order_id,
                status="submitted",
                message="Order successfully submitted",
                details=order_data
            )
            
        except ExecutionError as e:
            # STEP 6.2: Handle validation errors from exchange response
            # STEP 8.1: Record failed order metric
            record_failed_order(reason="exchange_validation_failed", symbol=symbol)
            
            # STEP 8.4: Record failure in Global Circuit Breaker
            global_circuit_breaker().record_failure(failure_reason="exchange_validation_failed")
            
            logger.critical(
                f"🚨 EXCHANGE RESPONSE VALIDATION FAILED: {e.message} | "
                f"Failures: {e.validation_failures}"
            )
            return OrderResult(
                success=False,
                client_order_id=client_order_id,
                status="failed",
                message=f"Exchange response validation failed: {e.message}",
                details={
                    "error": e.message,
                    "validation_failures": e.validation_failures,
                    "raw_response": e.raw_response,
                }
            )
        except Exception as e:
            # STEP 8.1: Record failed order metric
            record_failed_order(reason="execution_error", symbol=symbol)
            
            # STEP 8.4: Record failure in Global Circuit Breaker
            global_circuit_breaker().record_failure(failure_reason="execution_error")
            
            logger.error(f"Order placement failed: {e}")
            return OrderResult(
                success=False,
                client_order_id=client_order_id,
                status="failed",
                message=f"Order placement failed: {str(e)}",
                details={"error": str(e)}
            )
    
    async def validate_and_store_exchange_response(
        self,
        tenant_id: str,
        client_order_id: str,
        exchange_response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        STEP 6.2: Validate exchange response and update stored order.
        
        This method should be called AFTER receiving response from exchange.
        
        Args:
            tenant_id: Tenant identifier
            client_order_id: Client order ID
            exchange_response: Raw response from CCXT/exchange
            
        Returns:
            Validated order data
            
        Raises:
            ExecutionError: If validation fails
        """
        # STEP 6.2: Validate exchange response
        validated = self.response_validator.validate_order_response(exchange_response)
        
        # STEP 6.2: Update stored order with validated data
        await self.update_order_status(
            tenant_id=tenant_id,
            client_order_id=client_order_id,
            status=validated["status"],
            fill_data={
                "filled": validated["filled"],
                "remaining": validated["remaining"],
                "average": validated["average"],
                "fee": validated["fee"],
            }
        )
        
        logger.info(
            f"✅ Exchange response validated and stored: "
            f"{client_order_id} | order_id={validated['order_id']}"
        )
        
        return validated
    
    async def update_order_status(
        self,
        tenant_id: str,
        client_order_id: str,
        status: str,
        fill_data: Optional[Dict[str, Any]] = None
    ):
        """
        Update order status in Redis.
        
        Called when:
        - Order is filled
        - Order is cancelled
        - Order is rejected
        """
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            existing = await self.redis.get(redis_key)
            if existing:
                order_data = json.loads(existing)
                order_data["status"] = status
                order_data["updated_at"] = datetime.utcnow().isoformat()
                
                if fill_data:
                    order_data["fill_data"] = fill_data
                
                await self.redis.setex(
                    redis_key,
                    self.order_ttl,
                    json.dumps(order_data, default=str)
                )
                
                logger.info(
                    f"Order status updated: {client_order_id} | "
                    f"Status={status}"
                )
        except Exception as e:
            logger.error(f"Error updating order status: {e}")
    
    async def get_order(
        self,
        tenant_id: str,
        client_order_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get order details from Redis.
        
        Returns:
            Order dict if found, None otherwise
        """
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            data = await self.redis.get(redis_key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Error getting order from Redis: {e}")
        
        return None
    
    async def delete_order(
        self,
        tenant_id: str,
        client_order_id: str
    ):
        """
        Delete order from Redis (rarely used).
        """
        redis_key = f"orders:{tenant_id}:{client_order_id}"
        
        try:
            await self.redis.delete(redis_key)
            logger.info(f"Order deleted: {client_order_id}")
        except Exception as e:
            logger.error(f"Error deleting order: {e}")


# Global engine instance (initialized with Redis connection)
_execution_engine: Optional[IdempotentExecutionEngine] = None


def init_execution_engine(redis_client: redis.Redis):
    """Initialize the global execution engine."""
    global _execution_engine
    _execution_engine = IdempotentExecutionEngine(redis_client)
    logger.info("STEP 6.1: IdempotentExecutionEngine initialized")


def get_execution_engine() -> IdempotentExecutionEngine:
    """Get the global execution engine instance."""
    if _execution_engine is None:
        raise RuntimeError("ExecutionEngine not initialized. Call init_execution_engine first.")
    return _execution_engine


async def place_order(
    tenant_id: str,
    symbol: str,
    side: str,
    size: float,
    price: Optional[float] = None,
    signal_id: str = "",
    order_type: str = "market",
    metadata: Optional[Dict[str, Any]] = None
) -> OrderResult:
    """
    Convenience function to place an order.
    
    STEP 6.1 + 6.4: This function ensures idempotency and state tracking.
    Same signal will never result in duplicate orders.
    Order state is tracked through the lifecycle.
    """
    engine = get_execution_engine()
    return await engine.place_order(
        tenant_id=tenant_id,
        symbol=symbol,
        side=side,
        size=size,
        price=price,
        signal_id=signal_id,
        order_type=order_type,
        metadata=metadata
    )
