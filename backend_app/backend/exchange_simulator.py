"""
backend/exchange_simulator.py — Paper Trading Exchange Simulator (STEP 8.10)

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE
STEP 8.10: Staging Environment - Safe Testing with Paper Trading

Purpose:
  - Simulates a real exchange for safe testing
  - Paper trading with virtual balance
  - Real-time market data simulation
  - Order matching engine (simplified)
  - No real money at risk

Features:
  - Virtual balance tracking (starting $100,000)
  - Simulated order fills
  - Realistic latency injection (configurable)
  - Market data generation
  - WebSocket streaming simulation
  - Position tracking
  - PnL calculation

Usage:
    from backend_app.backend.exchange_simulator import PaperTradingExchange
    
    exchange = PaperTradingExchange(
        initial_balance=100000.0,
        latency_ms=50,
        fill_probability=0.95
    )
    
    # Place order (paper trade)
    result = await exchange.place_order(
        symbol="BTC-USD",
        side="buy",
        quantity=1.5,
        order_type="market"
    )
    
    # Get balance
    balance = exchange.get_balance()
    
    # Get positions
    positions = exchange.get_positions()
"""

import asyncio
import json
import time
import random
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger("ExchangeSimulator")

try:
    from backend_app.backend.exchange_executor import ExchangeError
except ImportError:
    class ExchangeError(Exception):
        """Base exchange error."""
        def __init__(self, message: str, error_code: Optional[str] = None, retryable: bool = False):
            super().__init__(message)
            self.message = message
            self.error_code = error_code
            self.retryable = retryable


class FallbackDict(dict):
    def __getitem__(self, key):
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str):
            norm = key.replace("USDT", "USD")
            if super().__contains__(norm):
                return super().__getitem__(norm)
            norm2 = key.replace("USD", "USDT")
            if super().__contains__(norm2):
                return super().__getitem__(norm2)
        raise KeyError(key)
        
    def __contains__(self, key):
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            norm = key.replace("USDT", "USD")
            if super().__contains__(norm):
                return True
            norm2 = key.replace("USD", "USDT")
            if super().__contains__(norm2):
                return True
        return False
        
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        
    def __delitem__(self, key):
        if super().__contains__(key):
            super().__delitem__(key)
            return
        if isinstance(key, str):
            norm = key.replace("USDT", "USD")
            if super().__contains__(norm):
                super().__delitem__(norm)
                return
            norm2 = key.replace("USD", "USDT")
            if super().__contains__(norm2):
                super().__delitem__(norm2)
                return
        raise KeyError(key)
        
    def pop(self, key, default=None):
        try:
            val = self[key]
            del self[key]
            return val
        except KeyError:
            return default


class OrderStatus(Enum):
    """Order status for paper trading."""
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class PaperOrder:
    """Paper trading order."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str  # "buy" or "sell"
    quantity: float
    filled_quantity: float = 0.0
    price: float = 0.0
    order_type: str = "market"
    status: OrderStatus = OrderStatus.PENDING
    created_at: float = field(default_factory=time.time)
    filled_at: Optional[float] = None
    commission: float = 0.0
    stop_price: Optional[float] = None
    leverage: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "price": self.price,
            "order_type": self.order_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "filled_at": self.filled_at,
            "commission": self.commission,
            "stop_price": self.stop_price,
            "leverage": self.leverage,
        }


@dataclass
class Position:
    """Paper trading position."""
    symbol: str
    quantity: float
    avg_entry_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
        }


class PaperTradingExchange:
    """
    STEP 8.10: Paper Trading Exchange Simulator
    
    Simulates a real exchange for safe testing:
    - Virtual balance ($100,000 default)
    - Simulated order fills
    - Position tracking
    - PnL calculation
    - Realistic latency
    - WebSocket market data
    """
    
    def __init__(
        self,
        initial_balance: float = 100000.0,
        latency_ms: float = 50.0,
        fill_probability: float = 1.0,
        commission_rate: float = 0.001,  # 0.1%
        enable_websocket: bool = True,
        maintenance_mode: bool = False,
        rate_limit_triggered: bool = False,
        partial_fill_ratio: float = 1.0,
        fill_delay_seconds: float = 0.0,
        disconnect_ws: bool = False,
        slippage_overwrite: float = 0.0,
        liquidity_multiplier: float = 1.0,
        maintenance_margin_requirement_pct: float = 0.05,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.equity = initial_balance
        self.latency_ms = latency_ms
        self.fill_probability = fill_probability
        self.commission_rate = commission_rate
        self.enable_websocket = enable_websocket
        
        # Failure injections
        self.maintenance_mode = maintenance_mode
        self.rate_limit_triggered = rate_limit_triggered
        self.partial_fill_ratio = partial_fill_ratio
        self.fill_delay_seconds = fill_delay_seconds
        self.disconnect_ws = disconnect_ws
        
        # Stress configuration
        self.slippage_overwrite = slippage_overwrite
        self.liquidity_multiplier = liquidity_multiplier
        self.maintenance_margin_requirement_pct = maintenance_margin_requirement_pct
        
        # State
        self.orders: Dict[str, PaperOrder] = {}
        self.positions: Dict[str, Position] = FallbackDict()
        self.order_history: List[PaperOrder] = []
        self._order_counter = 0
        
        # Market data simulation
        self._prices: Dict[str, float] = FallbackDict({
            "BTC-USD": 45000.0,
            "ETH-USD": 3000.0,
            "SOL-USD": 100.0,
            "ADA-USD": 0.50,
        })
        self._price_volatility = 0.001  # 0.1% per tick
        
        # WebSocket simulation
        self._ws_subscribers: List[asyncio.Queue] = []
        self._ws_task: Optional[asyncio.Task] = None
        
        self._connected = False
        
        logger.info(
            f"STEP 8.10: Paper Trading Exchange initialized | "
            f"Balance: ${initial_balance:,.2f} | "
            f"Latency: {latency_ms}ms"
        )
    
    async def connect(self):
        """Connect to simulated exchange."""
        await self._simulate_latency()
        self._connected = True
        
        # Start market data simulation
        if self.enable_websocket:
            self._ws_task = asyncio.create_task(self._simulate_market_data())
        
        logger.info("STEP 8.10: Paper exchange connected")
    
    async def disconnect(self):
        """Disconnect from simulated exchange."""
        self._connected = False
        
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        logger.info("STEP 8.10: Paper exchange disconnected")
    
    async def _simulate_latency(self):
        """Simulate network latency."""
        if self.latency_ms > 0:
            await asyncio.sleep(self.latency_ms / 1000.0)
    
    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        self._order_counter += 1
        return f"PAPER-{self._order_counter}-{int(time.time() * 1000)}"
    
    def _get_price_from_store(self, symbol: str, default: float) -> float:
        if symbol in self._prices:
            return self._prices[symbol]
        norm = symbol.replace("USDT", "USD")
        if norm in self._prices:
            return self._prices[norm]
        norm2 = symbol.replace("USD", "USDT")
        if norm2 in self._prices:
            return self._prices[norm2]
        return default

    def _get_current_price(self, symbol: str) -> float:
        """Get current simulated price."""
        base_price = self._get_price_from_store(symbol, 100.0)
        # Add small random noise
        noise = random.gauss(0, base_price * self._price_volatility)
        return base_price + noise
    
    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
        leverage: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Place a paper trading order.
        """
        symbol = symbol.replace("/", "-").replace("USDT", "USD")
        if self.maintenance_mode:
            raise ExchangeError("Exchange is down for maintenance", error_code="503", retryable=False)
            
        if self.rate_limit_triggered:
            raise ExchangeError("Rate limit exceeded - too many requests", error_code="429", retryable=True)

        await self._simulate_latency()
        
        if not self._connected:
            raise RuntimeError("Exchange not connected")
        
        # Create order
        initial_status = OrderStatus.OPEN if order_type == "limit" else OrderStatus.PENDING
        order_id = self._generate_order_id()
        order = PaperOrder(
            order_id=order_id,
            client_order_id=client_order_id or f"client-{order_id}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            status=initial_status,
            stop_price=stop_price,
            leverage=leverage,
        )
        
        # Get current price
        current_price = self._get_current_price(symbol)
        order.price = price or current_price
        
        # Check fill probability
        if random.random() > self.fill_probability:
            order.status = OrderStatus.REJECTED
            self.orders[order_id] = order
            
            logger.warning(
                f"STEP 8.10: Order REJECTED (simulated) | "
                f"{order_id} | {symbol} | {side} | Qty: {quantity}"
            )
            
            return {
                "success": False,
                "order_id": order_id,
                "status": "rejected",
                "message": "Order rejected by simulated exchange",
            }
        
        # REDUCE-ONLY check
        if reduce_only:
            pos = self.positions.get(symbol)
            if not pos or pos.quantity <= 0:
                order.status = OrderStatus.REJECTED
                self.orders[order_id] = order
                return {
                    "success": False,
                    "order_id": order_id,
                    "status": "rejected",
                    "message": f"Reduce-only order rejected: no open position in {symbol}"
                }
            if side == "sell" and quantity > pos.quantity:
                quantity = pos.quantity
                order.quantity = quantity
                logger.info(f"Reduce-only order size clipped to match position size: {quantity}")
                if quantity <= 0:
                    order.status = OrderStatus.REJECTED
                    self.orders[order_id] = order
                    return {
                        "success": False,
                        "order_id": order_id,
                        "status": "rejected",
                        "message": "Reduce-only order clipped to zero"
                    }
        
        # Calculate commission and margin
        order_value = quantity * order.price
        commission = order_value * self.commission_rate
        order.commission = commission
        
        if leverage > 1.0:
            margin_required = order_value / leverage
        else:
            margin_required = order_value
        
        # Check balance for buys
        if side == "buy":
            total_cost = margin_required + commission
            if total_cost > self.balance:
                order.status = OrderStatus.REJECTED
                self.orders[order_id] = order
                
                logger.warning(
                    f"STEP 8.10: Order REJECTED (insufficient balance/margin) | "
                    f"{order_id} | Required: ${total_cost:,.2f} | "
                    f"Balance: ${self.balance:,.2f}"
                )
                
                return {
                    "success": False,
                    "order_id": order_id,
                    "status": "rejected",
                    "message": f"Insufficient balance/margin: ${self.balance:,.2f}",
                }
        
        # Apply fill delay if configured, scaled by liquidity_multiplier
        delay = self.fill_delay_seconds
        if getattr(self, "liquidity_multiplier", 1.0) > 1.0:
            delay = max(delay, 0.1) * getattr(self, "liquidity_multiplier", 1.0)
        if delay > 0:
            await asyncio.sleep(delay)
        
        # Store order
        self.orders[order_id] = order
        self.order_history.append(order)
        
        # Match order
        self._match_order_individually(order)
        
        # Update equity
        self._recalculate_equity()
        
        return {
            "success": True,
            "order_id": order_id,
            "client_order_id": order.client_order_id,
            "status": order.status.value,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "filled_quantity": order.filled_quantity,
            "price": order.price,
            "commission": order.commission,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    def _match_order_individually(self, order: PaperOrder):
        """Match/fill a single order against current price."""
        if order.status not in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]:
            return
            
        current_price = self._get_price_from_store(order.symbol, order.price)
        
        # 1. Stop order trigger checks
        if order.order_type in ["stop_market", "stop_limit"] and order.status == OrderStatus.PENDING:
            stop_triggered = False
            stop_price = order.stop_price or order.price
            if order.side == "buy" and current_price >= stop_price:
                stop_triggered = True
            elif order.side == "sell" and current_price <= stop_price:
                stop_triggered = True
                
            if stop_triggered:
                logger.info(f"Stop order TRIGGERED: {order.order_id} at {current_price}")
                if order.order_type == "stop_market":
                    order.order_type = "market"
                    order.status = OrderStatus.OPEN
                else:
                    order.order_type = "limit"
                    order.status = OrderStatus.OPEN
                    
        # 2. Match market/limit orders
        if order.status in [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED] or (order.order_type == "market" and order.status == OrderStatus.PENDING):
            should_fill = False
            fill_price = order.price
            
            if order.order_type == "market":
                should_fill = True
                fill_price = current_price
            elif order.order_type == "limit":
                if order.side == "buy" and current_price <= order.price:
                    should_fill = True
                elif order.side == "sell" and current_price >= order.price:
                    should_fill = True
            
            if should_fill:
                self._fill_order(order, fill_price)
                
    def _fill_order(self, order: PaperOrder, fill_price: float):
        """Simulate filling an order (full or partial)."""
        remaining_qty = order.quantity - order.filled_quantity
        if remaining_qty <= 0:
            return
            
        # Apply slippage overwrite for market orders
        if order.order_type == "market" and getattr(self, "slippage_overwrite", 0.0) > 0.0:
            slippage_pct = getattr(self, "slippage_overwrite", 0.0)
            if order.side == "buy":
                fill_price = fill_price * (1.0 + slippage_pct)
            else:
                fill_price = fill_price * (1.0 - slippage_pct)
            
        # Determine fill quantity based on partial_fill_ratio
        fill_qty = remaining_qty
        if self.partial_fill_ratio < 1.0:
            fill_qty = remaining_qty * self.partial_fill_ratio
            fill_qty = round(fill_qty, 4)
            if fill_qty <= 0.0001:
                fill_qty = remaining_qty  # Force fill if too small
                
        # Calculate order value and commission for this fill
        fill_value = fill_qty * fill_price
        commission = fill_value * self.commission_rate
        
        # Check balance for buys
        if order.side == "buy":
            margin_required = fill_value / order.leverage if order.leverage > 1.0 else fill_value
            total_cost = margin_required + commission
            if total_cost > self.balance:
                logger.warning(f"Insufficient balance to fill order {order.order_id}")
                order.status = OrderStatus.REJECTED
                return
            self.balance -= total_cost
        else:
            # For sells, we release/add to balance
            margin_released = fill_value / order.leverage if order.leverage > 1.0 else fill_value
            self.balance += (margin_released - commission)
            
        order.filled_quantity += fill_qty
        order.commission += commission
        order.price = fill_price  # Avg execution price
        
        # Update position
        self._update_position(order.symbol, order.side, fill_qty, fill_price)
        
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_at = time.time()
            logger.info(f"Order FILLED: {order.order_id} @ {fill_price} | Qty: {order.quantity}")
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
            logger.info(f"Order PARTIALLY FILLED: {order.order_id} @ {fill_price} | Filled: {order.filled_quantity}/{order.quantity}")
            
        self._recalculate_equity()

    def _update_position(self, symbol: str, side: str, quantity: float, price: float):
        """Update position after fill."""
        if symbol not in self.positions:
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=0.0,
                avg_entry_price=0.0,
            )
        
        position = self.positions[symbol]
        
        if side == "buy":
            # Increase position
            total_value = (position.quantity * position.avg_entry_price) + (quantity * price)
            position.quantity += quantity
            position.avg_entry_price = total_value / position.quantity if position.quantity > 0 else 0
        else:
            # Decrease/close position
            if quantity >= position.quantity:
                # Full close
                realized_pnl = position.quantity * (price - position.avg_entry_price)
                position.realized_pnl += realized_pnl
                position.quantity = 0.0
                position.avg_entry_price = 0.0
            else:
                # Partial close
                realized_pnl = quantity * (price - position.avg_entry_price)
                position.realized_pnl += realized_pnl
                position.quantity -= quantity
    
    def _recalculate_equity(self):
        """Recalculate total equity."""
        positions_value = 0.0
        total_maintenance_margin = 0.0
        
        for symbol, position in list(self.positions.items()):
            if position.quantity <= 0:
                continue
            current_price = self._get_price_from_store(symbol, 100.0)
            
            # Update position unrealized pnl
            position.unrealized_pnl = position.quantity * (current_price - position.avg_entry_price)
            positions_value += position.quantity * current_price
            
            # Maintenance margin is calculated as position value * MMR
            mmr = getattr(self, "maintenance_margin_requirement_pct", 0.05)
            total_maintenance_margin += position.quantity * current_price * mmr
        
        self.equity = self.balance + positions_value
        
        # Check if liquidation is needed
        if total_maintenance_margin > 0 and self.equity < total_maintenance_margin:
            self._liquidate_all_positions()

    def _liquidate_all_positions(self):
        """Force liquidate all open positions due to margin call."""
        logger.critical(
            f"⚡ LIQUIDATION TRIGGERED! Equity ${self.equity:,.2f} dropped below "
            f"maintenance margin requirement. Liquidating all positions."
        )
        
        # Cancel all pending/open orders
        for order_id, order in list(self.orders.items()):
            if order.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]:
                order.status = OrderStatus.REJECTED
                
        # Close all positions
        for symbol, position in list(self.positions.items()):
            if position.quantity > 0:
                current_price = self._get_price_from_store(symbol, 100.0)
                # Position value realized into balance
                value = position.quantity * current_price
                # Subtract 1% liquidation fee
                penalty = value * 0.01
                self.balance += (value - penalty)
                position.quantity = 0.0
                position.avg_entry_price = 0.0
                position.unrealized_pnl = 0.0
                
        # Final equity recalculation
        self.equity = self.balance

    def trigger_flash_crash(self, symbol: str, drop_pct: float):
        """Force direct price drop for simulated flash crash."""
        symbol = symbol.replace("/", "-").replace("USDT", "USD")
        current_price = self._get_price_from_store(symbol, 100.0)
        self._prices[symbol] = current_price * (1.0 - drop_pct)
        self._recalculate_equity()

    def trigger_gap_open(self, symbol: str, gap_pct: float):
        """Force price gap (open higher or lower)."""
        symbol = symbol.replace("/", "-").replace("USDT", "USD")
        current_price = self._get_price_from_store(symbol, 100.0)
        self._prices[symbol] = current_price * (1.0 + gap_pct)
        self._recalculate_equity()
    
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order."""
        if self.maintenance_mode:
            raise ExchangeError("Exchange is down for maintenance", error_code="503", retryable=False)
            
        if self.rate_limit_triggered:
            raise ExchangeError("Rate limit exceeded - too many requests", error_code="429", retryable=True)

        await self._simulate_latency()
        
        if order_id not in self.orders:
            return {"success": False, "message": "Order not found"}
        
        order = self.orders[order_id]
        
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            return {"success": False, "message": f"Order already {order.status.value}"}
        
        # Refund balance/margin for cancelled buy orders
        if order.side == "buy" and order.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]:
            remaining_qty = order.quantity - order.filled_quantity
            margin_required = (remaining_qty * order.price) / order.leverage if order.leverage > 1.0 else (remaining_qty * order.price)
            refund = margin_required + (remaining_qty * order.price * self.commission_rate)
            self.balance += refund
        
        order.status = OrderStatus.CANCELLED
        
        logger.info(f"STEP 8.10: Order CANCELLED | {order_id}")
        
        return {"success": True, "order_id": order_id, "status": "cancelled"}
    
    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order details."""
        if self.maintenance_mode:
            raise ExchangeError("Exchange is down for maintenance", error_code="503", retryable=False)
            
        if self.rate_limit_triggered:
            raise ExchangeError("Rate limit exceeded - too many requests", error_code="429", retryable=True)

        await self._simulate_latency()
        
        if order_id not in self.orders:
            return None
        
        return self.orders[order_id].to_dict()
    
    def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        self._recalculate_equity()
        
        return {
            "balance": self.balance,
            "equity": self.equity,
            "initial_balance": self.initial_balance,
            "pnl": self.equity - self.initial_balance,
            "pnl_pct": ((self.equity - self.initial_balance) / self.initial_balance) * 100,
        }
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all positions."""
        self._recalculate_equity()
        
        positions = []
        for symbol, position in self.positions.items():
            current_price = self._get_price_from_store(symbol, 100.0)
            
            # Calculate unrealized PnL
            if position.quantity > 0:
                position.unrealized_pnl = position.quantity * (current_price - position.avg_entry_price)
            
            positions.append(position.to_dict())
        
        return positions
    
    def get_order_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get order history."""
        history = [o.to_dict() for o in self.order_history[-limit:]]
        return list(reversed(history))
    
    async def _simulate_market_data(self):
        """Simulate real-time market data."""
        while self._connected:
            try:
                # Update prices
                for symbol in self._prices:
                    base_price = self._prices[symbol]
                    change = random.gauss(0, base_price * self._price_volatility)
                    self._prices[symbol] = max(0.01, base_price + change)
                
                # Match limit/stop orders
                self._match_orders()
                
                # Create tick
                tick = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "prices": {s: p for s, p in self._prices.items()},
                }
                
                # Broadcast to subscribers
                for queue in self._ws_subscribers:
                    try:
                        queue.put_nowait(tick)
                    except asyncio.QueueFull:
                        pass
                
                await asyncio.sleep(1)  # 1 second updates
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"STEP 8.10: Market data simulation error: {e}")
                await asyncio.sleep(1)
                
    def broadcast_ws_event(self, event: Dict[str, Any]):
        """Helper to broadcast custom events to all WS queues (e.g. for duplicate/out-of-order testing)."""
        for queue in self._ws_subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
    
    def _match_orders(self):
        """Match open limit/stop orders against current prices."""
        for order in list(self.orders.values()):
            self._match_order_individually(order)
    
    async def stream_market_data(self, symbols: List[str]):
        """Stream simulated market data (WebSocket simulation)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._ws_subscribers.append(queue)
        
        try:
            while self._connected:
                if self.disconnect_ws:
                    raise ConnectionError("Simulated WebSocket disconnect")
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=5.0)
                    
                    # Filter for requested symbols
                    filtered_tick = {
                        "timestamp": tick["timestamp"],
                        "prices": {s: p for s, p in tick["prices"].items() if s in symbols},
                    }
                    
                    yield filtered_tick
                    
                except asyncio.TimeoutError:
                    continue
                
        finally:
            self._ws_subscribers.remove(queue)
    
    def reset(self):
        """Reset paper trading account (staging only)."""
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.orders.clear()
        self.positions.clear()
        self.order_history.clear()
        
        logger.warning(
            f"STEP 8.10: Paper trading account RESET | "
            f"Balance restored to ${self.initial_balance:,.2f}"
        )


# Global singleton for staging
_paper_exchange: Optional[PaperTradingExchange] = None


def get_paper_exchange() -> PaperTradingExchange:
    """Get the global paper trading exchange singleton."""
    global _paper_exchange
    if _paper_exchange is None:
        _paper_exchange = PaperTradingExchange()
    return _paper_exchange


ExchangeSimulator = PaperTradingExchange
