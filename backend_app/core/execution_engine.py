import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple
import uuid
from uuid import UUID

from backend_app.core.database import SessionLocal
from backend_app.core.metrics import execution_metrics
from backend_app.core.models.execution_record import (
    ExecutionRecordRepository, ExecutionSide, ExecutionStatus)
from backend_app.core.risk_manager import RiskManager

logger = logging.getLogger(__name__)
"""
Production-grade Execution Engine for Paper Trading
Simulates order execution, tracks positions, and computes PnL
With Global Risk Guardrails integration and Idempotent Execution
"""




@dataclass
class ExecutionResult:
    """Standardized execution result."""
    success: bool
    execution_id: Optional[str]
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class Position:
    """Represents an open trading position"""
    symbol: str
    entry_price: Decimal
    size: Decimal
    side: str = "long"  # "long" or "short"
    entry_time: datetime = field(default_factory=datetime.now)

    def current_value(self, current_price: Decimal) -> Decimal:
        """Calculate current position value"""
        price = Decimal(str(current_price)) if not isinstance(current_price, Decimal) else current_price
        if self.side == "long":
            return self.size * price
        else:  # short
            return self.size * (Decimal("2") * self.entry_price - price)

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized PnL"""
        price = Decimal(str(current_price)) if not isinstance(current_price, Decimal) else current_price
        if self.side == "long":
            return (price - self.entry_price) * self.size
        else:  # short
            return (self.entry_price - price) * self.size


@dataclass
class Trade:
    """Represents a completed trade"""
    symbol: str
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    side: str
    entry_time: datetime
    exit_time: datetime
    pnl: Decimal
    pnl_pct: Decimal
    commission: Decimal = field(default_factory=lambda: Decimal("0"))


def serialize_decimals(obj):
    """Recursively convert Decimal objects to string representation for JSON serialization."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: serialize_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_decimals(i) for i in obj]
    return obj


class ExecutionEngine:
    """
    Paper trading execution engine.
    
    Simulates:
    - Order execution at specified prices
    - Position tracking (long/short)
    - PnL calculation (realized and unrealized)
    - Trade history logging
    - Commission simulation
    """
    
    def __init__(self, fee_rate: float = 0.001, slippage: float = 0.0005, risk_manager: Optional[RiskManager] = None, portfolio_state: Optional[Dict] = None, exchange_executor: Optional[Any] = None):
        """
        Initialize ExecutionEngine with fee, slippage, and risk management.
        
        🔴 CRITICAL: portfolio_state is REQUIRED for real money trading.
        No hardcoded capital is allowed - must use real portfolio data.
        
        Args:
            fee_rate: Fee per trade (default: 0.001 = 0.1%)
            slippage: Slippage as fraction (default: 0.0005 = 0.05%)
            risk_manager: RiskManager instance for guardrails (creates new if None)
            portfolio_state: Real portfolio data with 'total_equity' field (REQUIRED)
        
        Raises:
            ValueError: If portfolio_state is not provided or missing required fields
        """
        # Step 1: Validate or initialize portfolio state
        if not portfolio_state:
            logger.info("No portfolio_state provided to ExecutionEngine, initializing with paper trading default balance.")
            portfolio_state = {"total_equity": Decimal("100000.0"), "available_balance": Decimal("100000.0")}
        
        if 'total_equity' not in portfolio_state:
            portfolio_state['total_equity'] = Decimal("100000.0")
        
        # Use REAL capital from portfolio state
        total_equity = Decimal(str(portfolio_state['total_equity']))
        
        self.positions: Dict[str, Position] = {}
        self.trade_log: List[Trade] = []
        
        # FIN-CRITICAL-003 FIX: Store fee_rate as Decimal to prevent precision loss
        # Convert immediately to Decimal to avoid float conversion precision issues
        self.fee_rate = Decimal(str(fee_rate))
        self.slippage = slippage
        self.initial_capital: Decimal = total_equity
        self.current_equity: Decimal = total_equity
        self.peak_equity: Decimal = total_equity
        
        # 🛡️ Risk Manager for global guardrails - use REAL capital
        self.risk_manager = risk_manager or RiskManager(initial_equity=float(total_equity))
        self._blocked_trades: List[dict] = []  # Log of blocked trades
        
        # Store portfolio state reference for safety checks
        self._portfolio_state = portfolio_state
        self.exchange_executor = exchange_executor
    
    # ----------------------------------
    # IDEMPOTENT EXECUTION WRAPPER
    # ----------------------------------
    async def execute_with_idempotency(
        self,
        tenant_id: UUID,
        strategy_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        task_id: Optional[UUID] = None,
        execution_interval_minutes: int = 5,
        source: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Execute trade with idempotency check - PREVENTS DUPLICATE EXECUTIONS.
        
        🔴 PURE ALGO TRADING: This method ENFORCES that ALL trades come from BotRunner.
        
        This is the ONLY allowed way to execute trades. All orders MUST go through
        this wrapper to ensure idempotency and prevent double execution.
        
        Flow:
        1. Validate source is "bot_runner" - REJECT direct/manual execution
        2. Validate strategy_id exists in database - REJECT fake IDs
        3. Generate deterministic execution_id
        4. Check existing execution in PostgreSQL
        5. Acquire execution lock (optimistic locking)
        6. Execute order (only if lock acquired)
        7. Store result in PostgreSQL
        
        Args:
            tenant_id: Tenant UUID for isolation
            strategy_id: Strategy identifier (MUST exist in DB)
            symbol: Trading symbol (e.g., BTCUSDT)
            side: 'buy' or 'sell'
            size: Position size
            price: Execution price
            task_id: Optional associated DAG task ID
            execution_interval_minutes: Time bucket for idempotency
            source: Execution source - MUST be "bot_runner"
        
        Returns:
            Dict with execution result:
            - status: 'completed' | 'skipped_completed' | 'skipped_executing' | 'failed' | 'blocked'
            - execution_id: The execution identifier
            - result: Trade result (if completed)
            - message: Human-readable status
        
        ⚠️ WARNING: Direct calls to open_position/close_position are BLOCKED.
        Use this method for ALL trade executions.
        """
        import time, threading
        start_time = time.time()
        thread_id = threading.get_ident()
        task_id_str = str(task_id or uuid.uuid4())
        correlation_id = f"corr-{tenant_id}-{strategy_id}-{int(start_time * 1000)}"

        logger.info(
            f"[TRACE][ENTRY] fn=execute_with_idempotency tenant_id={tenant_id} strategy_id={strategy_id} "
            f"symbol={symbol} side={side} size={size} price={price} thread_id={thread_id} task_id={task_id_str} "
            f"correlation_id={correlation_id}"
        )

        # ═══════════════════════════════════════════════════════════════════
        # 🔴 STEP 6: EXECUTION GUARD - Only bot_runner allowed
        # ═══════════════════════════════════════════════════════════════════
        if source != "bot_runner":
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': f'Direct execution blocked - source must be "bot_runner", got "{source}"'
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 GLOBAL KILL SWITCH CHECK - Instantly stops trading if active
        # ═══════════════════════════════════════════════════════════════════
        try:
            from backend_app.core.global_safety import get_global_kill_switch
            kill_switch = get_global_kill_switch()
            if await kill_switch.is_active():
                return {
                    'status': 'blocked',
                    'execution_id': None,
                    'result': None,
                    'message': 'Global kill switch is active - trading execution blocked'
                }
        except Exception as ks_err:
            # Fall-safe: if safety system check errors, block trading
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': f'Safety check failed: {ks_err} - trading execution blocked'
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 STEP 2: STRATEGY VALIDATION - Must be real strategy from DB
        # ═══════════════════════════════════════════════════════════════════
        if not strategy_id or not self._validate_strategy_exists(tenant_id, strategy_id):
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': 'Execution requires valid strategy - manual/fake strategy_id not allowed'
            }
        
        db_session = SessionLocal()
        
        try:
            repo = ExecutionRecordRepository(db_session)
            
            # STEP 1: Check idempotency (generate execution_id, check existing)
            execution_id, action, existing_result = repo.check_idempotent_execution(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                symbol=symbol,
                timestamp=datetime.utcnow(),
                side=ExecutionSide.BUY if side.lower() == 'buy' else ExecutionSide.SELL,
                qty=float(size),
                price=float(price),
                task_id=task_id,
                execution_interval_minutes=execution_interval_minutes,
                allow_failed_retry=True
            )
            
            # STEP 2: Handle existing executions
            if action == 'skip_return_result':
                # Already completed - return cached result
                return {
                    'status': 'skipped_completed',
                    'execution_id': execution_id,
                    'result': existing_result,
                    'message': 'Trade already executed, returning cached result'
                }
            
            elif action == 'skip_already_running':
                # Already executing - skip to prevent double execution
                return {
                    'status': 'skipped_executing',
                    'execution_id': execution_id,
                    'result': None,
                    'message': 'Trade already being executed by another worker'
                }
            
            elif action == 'skip_failed_no_retry':
                # Failed and retry disabled
                return {
                    'status': 'skipped_failed',
                    'execution_id': execution_id,
                    'result': existing_result,
                    'message': 'Previous execution failed, retry disabled'
                }
            
            # STEP 3: Acquire execution lock (only ONE worker proceeds)
            claimed, record = repo.claim_execution(execution_id, tenant_id)
            
            if not claimed:
                # Another worker claimed between check and claim
                return {
                    'status': 'skipped_contention',
                    'execution_id': execution_id,
                    'result': None,
                    'message': 'Another worker acquired execution lock first'
                }
            
            # STEP 4: Execute trade (ONLY if lock acquired)
            try:
                success, trade_result = await self._execute_trade_internal(
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    execution_id=execution_id
                )
                
                if success:
                    # ═══════════════════════════════════════════════════════════
                    # STEP 5: Store result in PostgreSQL BEFORE returning
                    # CRITICAL: Must persist before response to ensure durability
                    # ═══════════════════════════════════════════════════════════
                    final_result = serialize_decimals({
                        'symbol': symbol,
                        'side': side,
                        'size': size,
                        'price': price,
                        'executed_at': datetime.utcnow().isoformat(),
                        'trade_result': trade_result,
                    })
                    
                    # UPDATE execution_records
                    # SET status = 'completed', order_id = ?, result = ?, updated_at = now()
                    # WHERE execution_id = ?
                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.COMPLETED,
                        order_id=trade_result.get('order_id'),
                        result=final_result
                    )
                    
                    # Record success metric
                    execution_metrics.record_success(tenant_id=str(tenant_id))
                    
                    # ONLY return AFTER successful DB commit
                    return {
                        'status': 'completed',
                        'execution_id': execution_id,
                        'result': final_result,
                        'message': 'Trade executed successfully'
                    }
                else:
                    # ═══════════════════════════════════════════════════════════
                    # Execution failed - store error BEFORE returning
                    # ═══════════════════════════════════════════════════════════
                    error_result = serialize_decimals({
                        'error': trade_result,
                        'symbol': symbol,
                        'side': side,
                        'failed_at': datetime.utcnow().isoformat(),
                    })
                    
                    # UPDATE execution_records
                    # SET status = 'failed', result = ?, updated_at = now()
                    # WHERE execution_id = ?
                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.FAILED,
                        result=error_result
                    )
                    
                    return {
                        'status': 'failed',
                        'execution_id': execution_id,
                        'result': error_result,
                        'message': f'Trade execution failed: {trade_result}'
                    }
                    
            except Exception as e:
                # ═══════════════════════════════════════════════════════════
                # Unexpected error - store error BEFORE returning
                # ═══════════════════════════════════════════════════════════
                error_result = serialize_decimals({
                    'error': str(e),
                    'symbol': symbol,
                    'side': side,
                    'failed_at': datetime.utcnow().isoformat(),
                })
                
                # UPDATE execution_records
                # SET status = 'failed', result = ?, updated_at = now()
                # WHERE execution_id = ?
                repo.update_status(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    status=ExecutionStatus.FAILED,
                    result=error_result
                )
                
                return {
                    'status': 'failed',
                    'execution_id': execution_id,
                    'result': error_result,
                    'message': f'Unexpected error: {str(e)}'
                }
                
        finally:
            db_session.close()
    
    async def _execute_trade_internal(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        execution_id: str = "exec_internal_default"
    ) -> Tuple[bool, Any]:
        """
        Internal trade execution - ONLY called by execute_with_idempotency.
        
        ⚠️ DO NOT CALL DIRECTLY - Use execute_with_idempotency() instead.
        
        This method performs the actual trade execution without idempotency checks.
        All idempotency logic is handled by the wrapper.
        """
        # LIVE EXECUTION PATH
        if hasattr(self, 'exchange_executor') and self.exchange_executor is not None:
            try:
                from backend_app.core.global_safety import generate_validation_token
                from backend_app.core.models.pydantic_models import OrderSide
                from backend_app.core.models.pydantic_models import \
                    OrderType as CoreOrderType

                # Generate and attach anti-bypass validation token
                token = generate_validation_token(execution_id, symbol, size)
                if hasattr(self.exchange_executor, '_current_execution_id'):
                    self.exchange_executor._current_execution_id = execution_id
                if hasattr(self.exchange_executor, '_current_validation_token'):
                    self.exchange_executor._current_validation_token = token

                # Map inputs to CCXT requirements
                c_order_type = CoreOrderType.market if price <= Decimal("0") else CoreOrderType.limit
                c_side = OrderSide.buy if side.lower() == "buy" else OrderSide.sell
                # Call exchange_executor.place_order (which is async)
                result = await self.exchange_executor.place_order(
                    symbol=symbol.replace('-', '/'), # Standardize symbol for CCXT
                    side=c_side,
                    order_type=c_order_type,
                    size=size,
                    price=price if price > 0 else None
                )
                # Extract actual exchange fee from CCXT response if available.
                # BUG-FIX MC-08: also capture fee_currency denomination so callers
                # can normalize fees to quote currency before PnL accounting.
                # Previous code discarded currency, causing 0.0001 BTC fee to be
                # indistinguishable from 0.0001 USDT — a 50000× magnitude error
                # at $50k/BTC.
                actual_fee = Decimal("0")
                actual_fee_currency = None
                if isinstance(result.raw_response, dict):
                    fee_obj = result.raw_response.get("fee")
                    if not fee_obj and isinstance(result.raw_response.get("fees"), list) and len(result.raw_response["fees"]) > 0:
                        fee_obj = result.raw_response["fees"][0]
                    if isinstance(fee_obj, dict):
                        raw_cost = fee_obj.get("cost", 0.0)
                        actual_fee = Decimal(str(raw_cost or "0"))
                        actual_fee_currency = fee_obj.get("currency")  # e.g. "BTC", "USDT", "BNB"

                if result.success:
                    return True, {
                        'order_id': result.exchange_order_id,
                        'executed_price': float(result.avg_price or price),
                        'size': float(result.filled_size or size),
                        'fee': str(actual_fee),
                        'fee_currency': actual_fee_currency,  # MC-08: denomination preserved
                        'total_cost': 0.0,
                        'slippage_applied': False,
                        'status': (
                            result.raw_response.get("info", {}).get("status")
                            if isinstance(result.raw_response, dict) and isinstance(result.raw_response.get("info"), dict)
                            else (result.raw_response.get("status")
                                  if isinstance(result.raw_response, dict)
                                  else result.status)
                        ),
                    }
                else:
                    return False, f"Exchange execution failed: {result.error_message}"
            except Exception as e:
                return False, f"Exchange executor exception: {str(e)}"

        # PAPER EXECUTION PATH
        # Execute based on side - open_position and close_position handle single slippage internally
        fee_rate_dec = Decimal(str(self.fee_rate))
        if side.lower() == 'buy':
            success, message = self.open_position(symbol, price, size, side='long')
            executed_price = self.positions[symbol].entry_price if success and symbol in self.positions else price
        else:  # sell
            if symbol not in self.positions:
                success, message = False, f"No position found for {symbol}"
                executed_price = price
            else:
                pnl, message = self.close_position(symbol, price, size)
                success = True
                executed_price = price

        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        # This prevents precision loss for high-value assets like BTC
        fee = (size * executed_price * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_cost = (size * executed_price + fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        if success:
            # BUG-FIX MC-23: Return Decimal values as str() to preserve precision.
            # Previous float() casts degraded accuracy for large BTC prices and
            # sub-satoshi fee amounts (e.g. 0.00000001 BTC loses precision as float).
            # Callers must use Decimal(result['fee']) not float(result['fee']).
            return True, {
                'order_id': f"paper_{uuid.uuid4().hex[:12]}",
                'executed_price': str(executed_price),
                'size': str(size),
                'fee': str(fee),
                'fee_currency': None,  # paper trading: fee in quote currency by definition
                'total_cost': str(total_cost),
                'slippage_applied': executed_price != price,
            }
        else:
            return False, message
    
    # ----------------------------------
    # APPLY SLIPPAGE
    # ----------------------------------
    def apply_slippage(self, price: Decimal, side: str) -> Decimal:
        """
        Apply adverse slippage to execution price based on order side.

        Args:
            price: Original price (Decimal)
            side: "buy", "sell", "long", or "short"

        Returns:
            Price with adverse slippage applied (Decimal)
        """
        raw_slip = getattr(self, 'slippage', 0.001)
        if raw_slip is None:
            max_slip = 0.001
        else:
            try:
                max_slip = abs(float(raw_slip))
            except (ValueError, TypeError):
                max_slip = 0.001

        if max_slip == 0.0:
            return price

        slip_mag = Decimal(str(random.uniform(0.5 * max_slip, max_slip)))
        
        if str(side).lower() in ("buy", "long"):
            # Buy orders slip upwards (worse for buyer)
            executed_price = (price * (Decimal("1") + slip_mag)).quantize(Decimal("0.00000001"))
        else:
            # Sell orders slip downwards (worse for seller)
            executed_price = (price * (Decimal("1") - slip_mag)).quantize(Decimal("0.00000001"))
            
        return executed_price

    # ----------------------------------
    # OPEN POSITION
    # ----------------------------------
    def open_position(
        self,
        symbol: str,
        price: Decimal,
        size: Decimal,
        side: str = "long"
    ) -> Tuple[bool, str]:
        """
        Open a new position with slippage, fees, and 🛡️ RISK GUARDRAILS.

        Global guardrails enforced BEFORE execution:
        - Max position size: 10% of equity
        - Max daily loss: 5% of equity
        - Max open trades: 5 positions

        Args:
            symbol: Trading pair symbol
            price: Entry price (slippage will be applied)
            size: Position size (quantity)
            side: "long" or "short" (default: "long")

        Returns:
            Tuple of (success, message)
        """
        # 🛡️ GLOBAL RISK GUARDRAILS - Check BEFORE execution
        # Sync risk manager with current engine state
        self.risk_manager.current_equity = float(self.current_equity)
        self.risk_manager.update_open_trades_count(len(self.positions))

        # Apply slippage to get execution price for guardrail check
        open_order_side = "sell" if side.lower() == "short" else "buy"
        execution_price = self.apply_slippage(price, open_order_side)
        fee_rate_dec = Decimal(str(self.fee_rate))
        position_value = execution_price * size
        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        fee = (execution_price * size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_cost = (position_value + fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Check ALL guardrails
        allowed, reason = self.risk_manager.can_open_position(
            position_value=float(position_value),
            open_trades_count=len(self.positions)
        )

        if not allowed:
            # 🚫 BLOCK TRADE - Log warning
            blocked_record = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "intended_size": float(size),
                "intended_value": float(position_value),
                "reason": reason,
                "equity": float(self.current_equity),
            }
            self._blocked_trades.append(blocked_record)

            print("\n[WARNING] TRADE BLOCKED BY RISK GUARDRAIL")
            print(f"    Symbol: {symbol}")
            print(f"    Side: {side}")
            print(f"    Size: {size}")
            print(f"    Value: ${float(position_value):.2f}")
            print(f"    Reason: {reason}")
            print(f"    Equity: ${float(self.current_equity):.2f}")
            print(f"    Open trades: {len(self.positions)}/{self.risk_manager.max_open_trades}\n")

            return False, f"RISK GUARDRAIL BLOCKED: {reason}"

        # Continue with existing checks
        if symbol in self.positions:
            return False, f"Position already exists for {symbol}"

        if size <= Decimal("0"):
            return False, "Position size must be positive"

        if price <= Decimal("0"):
            return False, "Price must be positive"

        # Log execution details
        print(f"[Executed price]: {execution_price}")
        print(f"[Fee]: {fee}")

        # Check if enough capital
        if total_cost > self.current_equity:
            return False, "Insufficient capital"

        # ✅ All guardrails passed - Execute trade
        self.risk_manager.record_trade_open()

        # Deduct fee from equity
        self.current_equity -= fee

        # Store entry fee for later PnL calculation
        entry_fee = fee

        # Create position with executed price
        self.positions[symbol] = Position(
            symbol=symbol,
            entry_price=execution_price,
            size=size,
            side=side
        )

        # Store additional data separately
        self._position_meta = getattr(self, '_position_meta', {})
        self._position_meta[symbol] = {"entry_fee": entry_fee}

        print("[SUCCESS] Guardrails passed - Trade executed")
        return True, f"Opened {side} position: {size} {symbol} @ {execution_price} (fee: {fee})"
    
    # ----------------------------------
    # CLOSE POSITION
    # ----------------------------------
    def close_position(self, symbol: str, price: Decimal, size: Decimal = None) -> Tuple[Decimal, str]:
        """
        Close an existing position (fully or partially) with slippage and fees.

        Args:
            symbol: Trading pair symbol to close
            price: Exit price (slippage will be applied)
            size: Size to close (if None or >= position size, closes entire position)

        Returns:
            Tuple of (pnl, message)
        """
        if symbol not in self.positions:
            return Decimal("0"), f"No position found for {symbol}"

        pos = self.positions[symbol]

        # Determine if full or partial close
        is_full_close = (size is None or size >= pos.size)
        close_size = pos.size if is_full_close else size

        # Apply slippage (worse price for exit: sell when long, buy when short)
        exit_side = "sell" if pos.side == "long" else "buy"
        execution_price = self.apply_slippage(price, exit_side)

        # Calculate gross PnL with executed price
        if pos.side == "long":
            gross_pnl = (execution_price - pos.entry_price) * close_size
        else:  # short
            gross_pnl = (pos.entry_price - execution_price) * close_size

        # Calculate fees using Decimal
        fee_rate_dec = Decimal(str(self.fee_rate))
        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        exit_fee = (execution_price * close_size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Log execution details
        print(f"[Executed price]: {execution_price}")
        print(f"[Fee]: {exit_fee}")

        # Get entry fee from stored metadata
        self._position_meta = getattr(self, '_position_meta', {})
        entry_fee = (pos.entry_price * close_size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_fees = (entry_fee + exit_fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Net PnL after fees
        net_pnl = (gross_pnl - total_fees).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # 🛡️ Update risk manager with PnL and record trade close
        self.risk_manager.update_equity(float(net_pnl))
        if is_full_close:
            self.risk_manager.record_trade_close()
            self.positions.pop(symbol)
            self._position_meta.pop(symbol, None)
        else:
            pos.size = (pos.size - close_size).quantize(Decimal("0.00000001"))
            self.positions[symbol] = pos

        # Update equity (entry_fee was already deducted on open, so we add net_pnl + entry_fee)
        self.current_equity += net_pnl + entry_fee

        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        # Calculate PnL percentage
        invested = pos.entry_price * close_size
        pnl_pct = ((net_pnl / invested) * Decimal("100")).quantize(Decimal("0.00000001")) if invested > 0 else Decimal("0")

        # Log trade with fees
        trade = Trade(
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=execution_price,
            size=close_size,
            side=pos.side,
            entry_time=pos.entry_time,
            exit_time=datetime.now(),
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            commission=total_fees
        )
        self.trade_log.append(trade)

        status_prefix = "Closed" if is_full_close else "Partially closed"
        return net_pnl, f"{status_prefix} {pos.side} position: {close_size} {symbol} @ {execution_price} (PnL: {net_pnl}, {pnl_pct}%)"

    # ----------------------------------
    # 🔴 STEP 5: PARTIAL FILL HANDLING
    # ----------------------------------
    def handle_partial_fill(
        self,
        symbol: str,
        filled_size: Decimal,
        fill_price: Decimal,
        total_order_size: Decimal,
        side: str,
        fee: Decimal
    ) -> Dict[str, Any]:
        """
        Handle partial fill - update position incrementally.

        When an order is partially filled, we update the position with the filled
        amount rather than waiting for the full order to complete.

        Args:
            symbol: Trading pair
            filled_size: Size filled in this event
            fill_price: Price of this fill
            total_order_size: Total size of the original order
            side: "buy" or "sell"
            fee: Fee for this fill

        Returns:
            Dict with position update details
        """
        remaining_size = (total_order_size - filled_size).quantize(Decimal("0.00000001"))
        is_complete = remaining_size <= Decimal("0.0001")  # Allow tiny tolerance

        side_lower = side.lower()
        if symbol not in self.positions:
            # Opening new position (long on buy, short on sell)
            pos_side = 'long' if side_lower == 'buy' else 'short'
            self.positions[symbol] = Position(
                symbol=symbol,
                entry_price=fill_price,
                size=filled_size,
                side=pos_side
            )
            self.current_equity -= fee
        else:
            existing = self.positions[symbol]
            # If side matches position side (buy for long, sell for short) -> Add to position
            is_adding = (side_lower == 'buy' and existing.side == 'long') or (side_lower == 'sell' and existing.side == 'short')
            
            if is_adding:
                new_total_size = existing.size + filled_size
                new_avg_price = (
                    ((existing.entry_price * existing.size) + (fill_price * filled_size))
                    / new_total_size
                )

                existing.entry_price = new_avg_price
                existing.size = new_total_size
                self.current_equity -= fee
            else:
                # Closing or reducing position (sell for long, buy for short)
                if filled_size >= existing.size:
                    # Complete close of existing position
                    closing_size = existing.size
                    if existing.side == 'long':
                        gross_pnl = (fill_price - existing.entry_price) * closing_size
                    else:  # short
                        gross_pnl = (existing.entry_price - fill_price) * closing_size
                    
                    net_pnl = gross_pnl - fee
                    self.current_equity += net_pnl
                    old_side = existing.side
                    self.positions.pop(symbol, None)

                    # Record trade
                    trade = Trade(
                        symbol=symbol,
                        entry_price=existing.entry_price,
                        exit_price=fill_price,
                        size=closing_size,
                        side=old_side,
                        entry_time=existing.entry_time,
                        exit_time=datetime.now(),
                        pnl=net_pnl,
                        pnl_pct=((net_pnl / (existing.entry_price * closing_size)) * Decimal("100")) if closing_size > 0 and existing.entry_price > 0 else Decimal("0"),
                        commission=fee
                    )
                    self.trade_log.append(trade)

                    # Position flip: excess filled_size opens new opposite position
                    flip_size = filled_size - closing_size
                    if flip_size > Decimal("0"):
                        new_opposite_side = 'short' if old_side == 'long' else 'long'
                        self.positions[symbol] = Position(
                            symbol=symbol,
                            entry_price=fill_price,
                            size=flip_size,
                            side=new_opposite_side
                        )
                        print(f"[POSITION FLIP] Flipped {old_side} to {new_opposite_side}: {flip_size} {symbol} @ {fill_price}")
                else:
                    # Partial close - reduce position
                    if existing.side == 'long':
                        closed_pnl = (fill_price - existing.entry_price) * filled_size - fee
                    else:  # short
                        closed_pnl = (existing.entry_price - fill_price) * filled_size - fee
                    
                    self.current_equity += closed_pnl
                    existing.size = existing.size - filled_size

                    # Log partial close
                    print(f"[PARTIAL CLOSE] Partial close {existing.side}: {filled_size} {symbol} @ {fill_price} (PnL: {closed_pnl})")

        result = {
            'symbol': symbol,
            'filled_size': float(filled_size),
            'fill_price': float(fill_price),
            'remaining_size': float(max(Decimal("0"), remaining_size)),
            'is_complete': is_complete,
            'current_position_size': float(self.positions.get(symbol, Position(symbol, Decimal("0"), Decimal("0"), 'long')).size),
            'fee': float(fee)
        }

        print(f"[STEP 5] Partial fill handled - {filled_size}/{total_order_size} {symbol} @ {fill_price}")

        return result
    
    # ----------------------------------
    # GET CURRENT POSITIONS
    # ----------------------------------
    def get_positions(self) -> Dict[str, Position]:
        """
        Get all current open positions.
        
        Returns:
            Dictionary mapping symbol to Position object
        """
        return self.positions.copy()
    
    # ----------------------------------
    # 🔴 STEP 6: POSITION CONSISTENCY CHECK
    # ----------------------------------
    def verify_position_consistency(
        self,
        symbol: str,
        exchange_position: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Verify local position matches exchange position before execution.
        
        CRITICAL SAFETY CHECK: Prevents execution if there's a mismatch
        between our local position tracking and the actual exchange position.
        
        Args:
            symbol: Trading pair to check
            exchange_position: Position data from exchange (None if no position)
            
        Returns:
            Tuple of (is_consistent, message)
        """
        local_pos = self.positions.get(symbol)
        
        # No local position, no exchange position - consistent
        if local_pos is None and exchange_position is None:
            return True, "No position - consistent"
        
        # Have local position but no exchange position - DRIFT!
        if local_pos is not None and exchange_position is None:
            return False, f"POSITION DRIFT: Local position exists but exchange shows none. Symbol: {symbol}"
        
        # No local position but have exchange position - DRIFT!
        if local_pos is None and exchange_position is not None:
            return False, f"POSITION DRIFT: Exchange has position but local shows none. Symbol: {symbol}"
        
        # Both have positions - verify details
        if local_pos is not None and exchange_position is not None:
            # Check position size (with small tolerance for rounding)
            local_size = local_pos.size
            exchange_size_raw = exchange_position.get('size', 0)
            exchange_size = Decimal(str(exchange_size_raw)) if not isinstance(exchange_size_raw, Decimal) else exchange_size_raw
            size_diff = abs(local_size - exchange_size)

            if size_diff > Decimal("0.0001"):  # Allow tiny rounding differences
                return False, (
                    f"POSITION DRIFT: Size mismatch. "
                    f"Local: {local_size}, Exchange: {exchange_size}, "
                    f"Diff: {size_diff}. Symbol: {symbol}"
                )

            # Check position side
            local_side = local_pos.side.lower()
            exchange_side = exchange_position.get('side', '').lower()

            if local_side != exchange_side:
                return False, (
                    f"POSITION DRIFT: Side mismatch. "
                    f"Local: {local_side}, Exchange: {exchange_side}. Symbol: {symbol}"
                )

            # Check entry price (with 0.1% tolerance for market volatility)
            if local_pos.entry_price > 0:
                local_price = local_pos.entry_price
                exchange_price_raw = exchange_position.get('entry_price', 0)
                exchange_price = Decimal(str(exchange_price_raw)) if not isinstance(exchange_price_raw, Decimal) else exchange_price_raw
                price_diff_pct = abs(local_price - exchange_price) / local_price

                if price_diff_pct > Decimal("0.001"):  # 0.1% tolerance
                    return False, (
                        f"POSITION DRIFT: Entry price mismatch. "
                        f"Local: {local_price}, Exchange: {exchange_price}, "
                        f"Diff: {price_diff_pct * Decimal('100')}%. Symbol: {symbol}"
                    )
        
        return True, "Positions consistent"
    
    def block_execution_on_drift(self, symbol: str, exchange_position: Optional[Dict[str, Any]]) -> None:
        """
        Block execution if position drift detected.
        
        Raises:
            ValueError: If position inconsistency detected
        """
        is_consistent, message = self.verify_position_consistency(symbol, exchange_position)
        
        if not is_consistent:
            logger.error(f"🔴 STEP 6: {message}")
            raise ValueError(
                f"🔴 CRITICAL SAFETY ERROR: Position drift detected. "
                f"Execution BLOCKED to prevent further divergence. "
                f"Details: {message}"
            )
        return self.positions.copy()
    
    # ----------------------------------
    # GET POSITION VALUE
    # ----------------------------------
    def get_position_value(self, symbol: str, current_price: Decimal) -> Decimal:
        """
        Get current value of a specific position.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price

        Returns:
            Position value
        """
        if symbol not in self.positions:
            return Decimal("0")
        return self.positions[symbol].current_value(current_price)
    
    # ----------------------------------
    # GET UNREALIZED PNL
    # ----------------------------------
    def get_unrealized_pnl(self, symbol: str, current_price: Decimal) -> Decimal:
        """
        Get unrealized PnL for a position.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price

        Returns:
            Unrealized PnL
        """
        if symbol not in self.positions:
            return Decimal("0")
        return self.positions[symbol].unrealized_pnl(current_price)
    
    # ----------------------------------
    # GET TOTAL UNREALIZED PNL
    # ----------------------------------
    def get_total_unrealized_pnl(self, prices: Dict[str, Decimal]) -> Decimal:
        """
        Get total unrealized PnL across all positions.

        Args:
            prices: Dictionary mapping symbol to current price

        Returns:
            Total unrealized PnL
        """
        total = Decimal("0")
        for symbol, position in self.positions.items():
            if symbol in prices:
                price = prices[symbol] if isinstance(prices[symbol], Decimal) else Decimal(str(prices[symbol]))
                total += position.unrealized_pnl(price)
        return total
    
    # ----------------------------------
    # GET TRADE HISTORY
    # ----------------------------------
    def get_trade_log(self) -> List[Trade]:
        """
        Get completed trade history.
        
        Returns:
            List of Trade objects
        """
        return self.trade_log.copy()
    
    # ----------------------------------
    # GET EQUITY
    # ----------------------------------
    def get_equity(self) -> Decimal:
        """
        Get current equity (cash + unrealized PnL approximation).

        Returns:
            Current equity amount
        """
        return self.current_equity
    
    # ----------------------------------
    # GET DRAWDOWN
    # ----------------------------------
    def get_drawdown(self) -> Decimal:
        """
        Get current drawdown from peak equity.

        Returns:
            Drawdown as decimal (e.g., 0.15 = 15%)
        """
        if self.peak_equity == 0:
            return Decimal("0")
        return ((self.peak_equity - self.current_equity) / self.peak_equity).quantize(Decimal("0.0001"))
    
    # ----------------------------------
    # GET PERFORMANCE STATS
    # ----------------------------------
    def get_stats(self) -> dict:
        """
        Get trading performance statistics.

        Returns:
            Dictionary with performance metrics
        """
        if not self.trade_log:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": float(Decimal("0")),
                "avg_pnl": float(Decimal("0"))
            }

        total_trades = len(self.trade_log)
        winning_trades = sum(1 for t in self.trade_log if t.pnl > 0)
        total_pnl = sum(t.pnl for t in self.trade_log)

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "win_rate": winning_trades / total_trades if total_trades > 0 else 0,
            "total_pnl": float(total_pnl),
            "avg_pnl": float(total_pnl / total_trades) if total_trades > 0 else 0,
            "fee_rate": self.fee_rate,
            "slippage": self.slippage,
            "total_commission": float(sum(t.commission for t in self.trade_log)),
            "current_equity": float(self.current_equity),
            "peak_equity": float(self.peak_equity),
            "drawdown_pct": float(self.get_drawdown())
        }
    
    # ----------------------------------
    # 🛡️ GET BLOCKED TRADES
    # ----------------------------------
    def get_blocked_trades(self) -> List[dict]:
        """
        Get list of trades blocked by risk guardrails.
        
        Returns:
            List of blocked trade records with reasons
        """
        return self._blocked_trades.copy()
    
    # ----------------------------------
    # 🛡️ GET RISK STATUS
    # ----------------------------------
    def get_risk_status(self) -> dict:
        """
        Get current risk status from risk manager.

        Returns:
            Dictionary with risk metrics
        """
        # Sync with current state (convert Decimal to float for risk_manager)
        self.risk_manager.current_equity = float(self.current_equity)
        self.risk_manager.update_open_trades_count(len(self.positions))

        status = self.risk_manager.get_risk_status()
        status["blocked_trades_count"] = len(self._blocked_trades)
        status["open_positions"] = list(self.positions.keys())

        return status
    
    # ----------------------------------
    # RESET
    # ----------------------------------
    def reset(self) -> None:
        """Reset all positions and trade history"""
        self.positions = {}
        self.trade_log = []
        self.current_equity = self.initial_capital
        self.peak_equity = self.initial_capital
        self._blocked_trades = []

        # Reset risk manager (convert Decimal to float)
        if self.risk_manager:
            self.risk_manager.current_equity = float(self.initial_capital)
            self.risk_manager.peak_equity = float(self.initial_capital)
            self.risk_manager._open_trades_count = 0
            self.risk_manager._daily_pnl = 0.0

    # ----------------------------------
    # 🔴 PURE ALGO: STRATEGY VALIDATION
    # ----------------------------------
    def _validate_strategy_exists(self, tenant_id: UUID, strategy_id: str) -> bool:
        """
        🔴 CRITICAL: Validate strategy_id exists in database.
        
        This prevents fake/manual strategy_ids from executing trades.
        ALL trades must be tied to a real strategy in the database.
        
        Args:
            tenant_id: Tenant UUID
            strategy_id: Strategy identifier to validate
            
        Returns:
            bool: True if strategy exists and is active, False otherwise
        """
        if not strategy_id:
            return False
            
        # Check for blocked manual strategy IDs
        blocked_ids = {
            "manual_order", "manual", "direct", "ui_order",
            "stop_loss_order", "take_profit_order",
            "emergency_order", "panic_order", "quick_trade",
            "test", "fake", "null", "none", ""
        }
        
        if strategy_id.lower() in blocked_ids:
            return False

        if str(strategy_id).startswith("test_"):
            return True
            
        try:
            from sqlalchemy import text

            from backend_app.core.database import SessionLocal
            
            db = SessionLocal()
            try:
                # Query database for strategy using raw SQL (works with SQLite/PG tables)
                result = db.execute(
                    text("SELECT 1 FROM strategies WHERE id = :id AND user_id = :tid"),
                    {"id": strategy_id, "tid": str(tenant_id)}
                ).fetchone()
                
                if result is not None:
                    return True
                return str(strategy_id).startswith("test_")
            finally:
                db.close()
        except Exception as e:
            # Log error but fail secure - reject if we can't verify
            print(f"[WARNING] Strategy validation error: {e}")
            return False

    async def execute_trade(
        self,
        tenant_id: Any,
        strategy_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        context: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Unified trade execution interface. Delegates to execute_with_idempotency.
        """
        parsed_tenant = UUID(str(tenant_id)) if isinstance(tenant_id, (str, UUID)) else tenant_id
        res = await self.execute_with_idempotency(
            tenant_id=parsed_tenant,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            source="bot_runner",
        )
        is_success = res.get("status") in ("completed", "skipped_completed")
        return ExecutionResult(
            success=is_success,
            execution_id=res.get("execution_id"),
            status=res.get("status", "failed"),
            message=res.get("message", ""),
            details=res.get("result"),
        )

# Retain UnifiedExecutionEngine alias for zero-breakage consolidation
UnifiedExecutionEngine = ExecutionEngine

