"""
backend/portfolio_management.py — Portfolio Management System.

Comprehensive portfolio management with:
  - Capital allocation per strategy (dynamic & static)
  - Total exposure tracking (symbol-level & portfolio-level)
  - Cross-strategy conflict resolution
  - Real-time PnL aggregation
  - Margin usage tracking
  - Integration with risk engine
  - Position reconciliation

Architecture:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                     PORTFOLIO MANAGEMENT SYSTEM                      │
  │                                                                       │
  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
  │  │   Capital    │───▶│   Position   │───▶│     PnL      │          │
  │  │  Allocation  │    │   Tracking   │    │ Aggregation  │          │
  │  └──────────────┘    └──────────────┘    └──────────────┘          │
  │         │                  │                  │                   │
  │         ▼                  ▼                  ▼                   │
  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
  │  │   Strategy   │    │   Exposure   │    │    Risk      │          │
  │  │    Limits    │    │    Monitor   │    │   Engine     │          │
  │  └──────────────┘    └──────────────┘    └──────────────┘          │
  │         │                  │                  │                   │
  │         └──────────────────┴──────────────────┘                   │
  │                            │                                       │
  │                            ▼                                       │
  │              ┌─────────────────────────────┐                        │
  │              │   CONFLICT RESOLUTION       │                        │
  │              │                             │                        │
  │              │ • Long/Short conflicts      │                        │
  │              │ • Symbol exposure limits      │                        │
  │              │ • Strategy priority           │                        │
  │              │ • Order cancellation          │                        │
  │              └─────────────────────────────┘                        │
  │                                                                       │
  └─────────────────────────────────────────────────────────────────────┘
"""
import asyncio
import hashlib
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend_app.core.decimal_utils import to_decimal

logger = logging.getLogger("PortfolioManagement")


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

class ConflictResolution(Enum):
    """
    STEP 5.5: Multi-strategy conflict resolution modes.
    
    Resolution modes for handling strategy collisions (e.g., Strategy A BUY vs Strategy B SELL).
    
    DEFAULT: NET_POSITION
    """
    # STEP 5.5: New resolution modes
    NET_POSITION = "net_position"         # Take net of conflicting positions (DEFAULT)
    REJECT_CONFLICT = "reject_conflict"   # Reject all conflicting orders
    
    # Existing modes
    FIRST_WINS = "first_wins"           # First signal wins
    LAST_WINS = "last_wins"             # Last signal wins
    LARGER_WINS = "larger_wins"         # Larger position wins
    PRIORITY_WINS = "priority_wins"       # Higher priority strategy wins
    SUM_POSITIONS = "sum_positions"     # Sum conflicting positions
    BLOCK_ALL = "block_all"             # Block all conflicting orders


@dataclass
class StrategyAllocation:
    """Capital allocation configuration for a strategy."""
    strategy_id: str
    
    # Allocation parameters - DECIMAL for financial precision
    allocation_pct: Decimal = field(default_factory=lambda: Decimal("0.1"))           # 10% of portfolio
    max_position_pct: Decimal = field(default_factory=lambda: Decimal("0.05"))        # 5% per position
    max_positions: int = 10               # Max concurrent positions
    
    # Dynamic allocation
    use_dynamic_allocation: bool = False
    performance_based_scaling: bool = True
    
    # Risk parameters - DECIMAL for financial precision
    stop_loss_pct: Decimal = field(default_factory=lambda: Decimal("0.02"))
    take_profit_pct: Decimal = field(default_factory=lambda: Decimal("0.04"))
    max_drawdown_pct: Decimal = field(default_factory=lambda: Decimal("0.1"))
    
    # Current state - DECIMAL for financial precision
    allocated_capital: Decimal = field(default_factory=lambda: Decimal("0"))
    used_capital: Decimal = field(default_factory=lambda: Decimal("0"))
    available_capital: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # Performance tracking - DECIMAL for financial precision
    total_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    win_count: int = 0
    loss_count: int = 0
    
    @property
    def utilization_pct(self) -> Decimal:
        """Capital utilization as Decimal (e.g., 0.75 for 75%)."""
        if self.allocated_capital > 0:
            return (self.used_capital / self.allocated_capital).quantize(Decimal("0.0001"))
        return Decimal("0")
    
    @property
    def win_rate(self) -> Decimal:
        """Win rate as Decimal (e.g., 0.65 for 65%)."""
        total = self.win_count + self.loss_count
        if total > 0:
            return (Decimal(self.win_count) / Decimal(total)).quantize(Decimal("0.0001"))
        return Decimal("0")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "allocation_pct": self.allocation_pct,
            "allocated_capital": self.allocated_capital,
            "used_capital": self.used_capital,
            "available_capital": self.available_capital,
            "utilization_pct": self.utilization_pct,
            "max_position_pct": self.max_position_pct,
            "max_positions": self.max_positions,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
        }


@dataclass
class Position:
    """
    STEP 5.2: Trading position representation with Decimal precision.
    
    ALL financial fields use Decimal for financial accuracy:
    - entry_price: Decimal (never float)
    - quantity: Decimal (never float)
    - current_price: Decimal (never float)
    - unrealized_pnl: Decimal (never float)
    - realized_pnl: Decimal (never float)
    """
    id: str
    strategy_id: str
    symbol: str
    side: str  # "long" or "short"
    
    # Entry details - Decimal for financial precision
    entry_price: Decimal
    entry_time: datetime
    quantity: Decimal
    entry_order_id: Optional[str] = None
    
    # Current state - Decimal for financial precision
    current_price: Decimal = field(default_factory=lambda: Decimal('0'))
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal('0'))
    unrealized_pnl_pct: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Exit details - Decimal for financial precision
    exit_price: Optional[Decimal] = None
    exit_time: Optional[datetime] = None
    realized_pnl: Optional[Decimal] = None
    exit_order_id: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "open"
    execution_ids: List[str] = field(default_factory=list)  # Track all executions that updated this position
    
    @property
    def is_open(self) -> bool:
        return self.exit_time is None
    
    @property
    def market_value(self) -> Decimal:
        """Calculate market value using Decimal precision."""
        return self.quantity * self.current_price
    
    @property
    def cost_basis(self) -> Decimal:
        """Calculate cost basis using Decimal precision."""
        return self.quantity * self.entry_price
    
    def update_price(self, price: Decimal):
        """
        Update position with current market price.
        
        STEP 5.2: Always use Decimal, never float.
        """
        self.current_price = to_decimal(price)
        
        # Recalculate unrealized PnL with Decimal precision
        if self.side == "long":
            self.unrealized_pnl = (self.current_price - self.entry_price) * self.quantity
        else:  # short
            self.unrealized_pnl = (self.entry_price - self.current_price) * self.quantity
        
        # Recalculate unrealized PnL percentage
        if self.cost_basis > 0:
            self.unrealized_pnl_pct = self.unrealized_pnl / self.cost_basis
    
    def close(self, price: Decimal, time: datetime = None):
        """
        Close position at given price with Decimal precision.
        
        STEP 5.2: Calculates realized PnL using Decimal.
        """
        if time is None:
            time = datetime.now()
        
        self.exit_price = to_decimal(price)
        self.exit_time = time
        self.status = "closed"
        
        # Calculate realized PnL with Decimal precision
        if self.side == "long":
            self.realized_pnl = (self.exit_price - self.entry_price) * self.quantity
        else:  # short
            self.realized_pnl = (self.entry_price - self.exit_price) * self.quantity
        
        if self.side == "long":
            self.unrealized_pnl = (price - self.entry_price) * self.quantity
        else:  # short
            self.unrealized_pnl = (self.entry_price - price) * self.quantity
        
        if self.cost_basis > 0:
            self.unrealized_pnl_pct = self.unrealized_pnl / self.cost_basis
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "current_price": self.current_price,
            "quantity": self.quantity,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "realized_pnl": self.realized_pnl,
            "is_open": self.is_open,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
        }


@dataclass
class ExposureMetrics:
    """
    STEP 5.2: Portfolio exposure metrics with Decimal precision.
    """
    # Symbol-level exposure - Decimal for financial precision
    symbol_exposure: Dict[str, Decimal] = field(default_factory=dict)
    symbol_notional: Dict[str, Decimal] = field(default_factory=dict)
    
    # Strategy-level exposure - Decimal for financial precision
    strategy_exposure: Dict[str, Decimal] = field(default_factory=dict)
    
    # Portfolio-level exposure - Decimal for financial precision
    gross_exposure: Decimal = field(default_factory=lambda: Decimal('0'))  # Sum of absolute positions
    net_exposure: Decimal = field(default_factory=lambda: Decimal('0'))    # Net long/short
    long_exposure: Decimal = field(default_factory=lambda: Decimal('0'))
    short_exposure: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Leverage metrics
    gross_leverage: Decimal = field(default_factory=lambda: Decimal('0'))
    net_leverage: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Concentration
    max_symbol_concentration: Decimal = field(default_factory=lambda: Decimal('0'))
    max_strategy_concentration: Decimal = field(default_factory=lambda: Decimal('0'))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "long_exposure": self.long_exposure,
            "short_exposure": self.short_exposure,
            "gross_leverage": self.gross_leverage,
            "net_leverage": self.net_leverage,
            "max_symbol_concentration": self.max_symbol_concentration,
            "max_strategy_concentration": self.max_strategy_concentration,
            "symbol_exposure": self.symbol_exposure,
            "strategy_exposure": self.strategy_exposure,
        }


@dataclass
class MarginMetrics:
    """
    STEP 5.2: Margin usage metrics with Decimal precision.
    """
    # Margin details - Decimal for financial precision
    initial_margin_required: Decimal = field(default_factory=lambda: Decimal('0'))
    maintenance_margin_required: Decimal = field(default_factory=lambda: Decimal('0'))
    margin_used: Decimal = field(default_factory=lambda: Decimal('0'))
    margin_available: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Ratios
    margin_utilization_pct: Decimal = field(default_factory=lambda: Decimal('0'))
    margin_level: Decimal = field(default_factory=lambda: Decimal('0'))  # Equity / Used Margin
    
    # Limits
    margin_call_threshold: Decimal = field(default_factory=lambda: Decimal('1.25'))  # 125%
    liquidation_threshold: Decimal = field(default_factory=lambda: Decimal('1.1'))   # 110%
    
    # Status
    margin_call_warning: bool = False
    near_liquidation: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial_margin_required": str(self.initial_margin_required),
            "maintenance_margin_required": str(self.maintenance_margin_required),
            "margin_used": str(self.margin_used),
            "margin_available": str(self.margin_available),
            "margin_utilization_pct": str(self.margin_utilization_pct),
            "margin_level": str(self.margin_level),
            "margin_call_threshold": str(self.margin_call_threshold),
            "liquidation_threshold": str(self.liquidation_threshold),
            "margin_call_warning": self.margin_call_warning,
            "near_liquidation": self.near_liquidation,
        }


class MarginMonitor:
    """
    STEP 5.8: Margin + Liquidation Monitor
    
    Active monitoring system for margin health with automatic risk reduction.
    
    Thresholds:
    - WARNING: margin_level < 1.50 (150%) - Alert only
    - MARGIN CALL: margin_level < 1.25 (125%) - Warning issued
    - LIQUIDATION RISK: margin_level < 1.10 (110%) - Forced reduction triggered
    
    Automatic Actions:
    - At LIQUIDATION RISK: Automatically reduce largest position by 25%
    - Continues reducing until margin_level > 1.25 (125%)
    """
    
    # Threshold levels
    WARNING_THRESHOLD = Decimal('1.50')      # 150% - Alert
    MARGIN_CALL_THRESHOLD = Decimal('1.25')  # 125% - Warning
    LIQUIDATION_THRESHOLD = Decimal('1.10') # 110% - Forced reduction
    
    # Reduction settings
    REDUCTION_PERCENTAGE = Decimal('0.25')  # Reduce position by 25% per iteration
    MAX_REDUCTION_ITERATIONS = 5  # Maximum auto-reductions to prevent infinite loop
    
    def __init__(self, portfolio_manager: 'PortfolioManager'):
        self.pm = portfolio_manager
        self.reduction_count = 0
        self.last_margin_level: Optional[Decimal] = None
        self.margin_history: deque = deque(maxlen=1000)
        self.warning_issued = False
        self.margin_call_issued = False
    
    def check_margin_health(self, metrics: MarginMetrics) -> Dict[str, Any]:
        """
        STEP 5.8: Check margin health and take action if needed.
        
        Returns:
            Dict with status, action taken, and details
        """
        margin_level = metrics.margin_level
        self.last_margin_level = margin_level
        
        # Record history
        self.margin_history.append({
            "timestamp": datetime.now().isoformat(),
            "margin_level": str(margin_level),
            "margin_used": str(metrics.margin_used),
            "margin_available": str(metrics.margin_available),
        })
        
        result = {
            "status": "healthy",
            "margin_level": str(margin_level),
            "action": None,
            "positions_reduced": [],
        }
        
        # HEALTHY: margin_level >= 150%
        if margin_level >= self.WARNING_THRESHOLD:
            self.warning_issued = False
            self.margin_call_issued = False
            result["status"] = "healthy"
            return result
        
        # WARNING: 125% <= margin_level < 150%
        if margin_level >= self.MARGIN_CALL_THRESHOLD:
            if not self.warning_issued:
                logger.warning(
                    f"🟡 MARGIN WARNING: margin_level={margin_level:.2%} "
                    f"(threshold={self.WARNING_THRESHOLD:.2%})"
                )
                self.warning_issued = True
            result["status"] = "warning"
            return result
        
        # MARGIN CALL: 110% <= margin_level < 125%
        if margin_level >= self.LIQUIDATION_THRESHOLD:
            if not self.margin_call_issued:
                logger.error(
                    f"🔴 MARGIN CALL: margin_level={margin_level:.2%} "
                    f"(threshold={self.MARGIN_CALL_THRESHOLD:.2%})"
                )
                self.margin_call_issued = True
            result["status"] = "margin_call"
            return result
        
        # LIQUIDATION RISK: margin_level < 110%
        # 🚨 TRIGGER FORCED POSITION REDUCTION
        logger.critical(
            f"🚨 LIQUIDATION RISK: margin_level={margin_level:.2%} "
            f"(threshold={self.LIQUIDATION_THRESHOLD:.2%}). "
            f"INITIATING FORCED POSITION REDUCTION."
        )
        
        result["status"] = "liquidation_risk"
        reduced_positions = self._forced_position_reduction()
        result["positions_reduced"] = reduced_positions
        result["action"] = "forced_reduction"
        
        return result
    
    def _forced_position_reduction(self) -> List[Dict[str, Any]]:
        """
        STEP 5.8: Automatically reduce positions to improve margin level.
        
        Strategy:
        1. Find largest position by notional value
        2. Reduce by REDUCTION_PERCENTAGE (25%)
        3. Recalculate margin
        4. Repeat until margin_level > MARGIN_CALL_THRESHOLD (125%)
        5. Stop after MAX_REDUCTION_ITERATIONS
        
        Returns:
            List of reduced positions with details
        """
        reduced = []
        
        for iteration in range(self.MAX_REDUCTION_ITERATIONS):
            # Get current margin metrics
            metrics = self.pm.get_margin_metrics()
            margin_level = metrics.margin_level
            
            # Check if we're back to safe levels
            if margin_level >= self.MARGIN_CALL_THRESHOLD:
                logger.info(
                    f"✅ Margin level restored to {margin_level:.2%} after "
                    f"{iteration} reduction iterations"
                )
                break
            
            # Find largest position by absolute notional value
            largest_position = None
            largest_notional = Decimal('0')
            
            for position in self.pm.positions.values():
                if not position.is_open:
                    continue
                
                notional = abs(position.market_value)
                if notional > largest_notional:
                    largest_notional = notional
                    largest_position = position
            
            if not largest_position:
                logger.warning("No open positions to reduce")
                break
            
            # Calculate reduction size (25% of position)
            current_size = largest_position.quantity
            reduce_by = current_size * self.REDUCTION_PERCENTAGE
            new_size = current_size - reduce_by
            
            if new_size <= 0:
                # Close entire position
                logger.critical(
                    f"🚨 FORCED CLOSE: {largest_position.id} "
                    f"({largest_position.symbol} {largest_position.side}) "
                    f"size={current_size}, notional={largest_notional}"
                )
                
                try:
                    # Use current market price for close
                    close_price = self.pm.current_prices.get(
                        largest_position.symbol, 
                        largest_position.current_price
                    )
                    
                    closed_position = self.pm.close_position(
                        position_id=largest_position.id,
                        close_price=close_price,
                        reason="forced_liquidation"
                    )
                    
                    reduced.append({
                        "position_id": largest_position.id,
                        "symbol": largest_position.symbol,
                        "side": largest_position.side,
                        "action": "full_close",
                        "size_reduced": str(current_size),
                        "realized_pnl": str(closed_position.realized_pnl),
                    })
                    
                    self.reduction_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to force close position: {e}")
                    break
            else:
                # Partial reduction - close 25% of position
                logger.critical(
                    f"🚨 FORCED REDUCTION: {largest_position.id} "
                    f"({largest_position.symbol} {largest_position.side}) "
                    f"reducing by 25% ({reduce_by} units)"
                )
                
                # Note: This requires a partial close method
                # For now, we close the entire position to guarantee safety
                try:
                    close_price = self.pm.current_prices.get(
                        largest_position.symbol,
                        largest_position.current_price
                    )
                    
                    # Execute complete position closure for full risk isolation
                    closed_position = self.pm.close_position(
                        position_id=largest_position.id,
                        close_price=close_price,
                        reason="forced_reduction"
                    )
                    
                    reduced.append({
                        "position_id": largest_position.id,
                        "symbol": largest_position.symbol,
                        "side": largest_position.side,
                        "action": "full_close",
                        "size_reduced": str(current_size),
                        "realized_pnl": str(closed_position.realized_pnl),
                    })
                    
                    self.reduction_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to reduce position: {e}")
                    break
        
        return reduced
    
    def get_margin_status(self) -> Dict[str, Any]:
        """
        Get current margin status summary.
        
        Returns:
            Dict with margin status and history
        """
        metrics = self.pm.get_margin_metrics()
        
        return {
            "margin_level": str(metrics.margin_level),
            "margin_used": str(metrics.margin_used),
            "margin_available": str(metrics.margin_available),
            "margin_utilization_pct": str(metrics.margin_utilization_pct),
            "warning_threshold": str(self.WARNING_THRESHOLD),
            "margin_call_threshold": str(self.MARGIN_CALL_THRESHOLD),
            "liquidation_threshold": str(self.LIQUIDATION_THRESHOLD),
            "status": self._get_status_label(metrics.margin_level),
            "warning_issued": self.warning_issued,
            "margin_call_issued": self.margin_call_issued,
            "total_reductions": self.reduction_count,
            "history_count": len(self.margin_history),
        }
    
    def _get_status_label(self, margin_level: Decimal) -> str:
        """Get status label based on margin level."""
        if margin_level >= self.WARNING_THRESHOLD:
            return "healthy"
        elif margin_level >= self.MARGIN_CALL_THRESHOLD:
            return "warning"
        elif margin_level >= self.LIQUIDATION_THRESHOLD:
            return "margin_call"
        else:
            return "liquidation_risk"


class TradeHistoryLogger:
    """
    STEP 5.9: Trade History + Audit Log
    
    Comprehensive logging of all trade activity for:
    - Full audit trail compliance
    - Performance analysis and debugging
    - Strategy attribution
    - Regulatory reporting
    
    Stores for each trade:
    - entry price, exit price
    - size, pnl
    - timestamp
    - strategy_id, symbol, side
    - execution details
    """
    
    def __init__(self, max_history: int = 10000):
        self.trades: deque = deque(maxlen=max_history)
        self.trades_by_strategy: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.trades_by_symbol: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.daily_stats: Dict[str, Dict[str, Any]] = {}
    
    def log_trade(
        self,
        position_id: str,
        strategy_id: str,
        symbol: str,
        side: str,
        entry_price: Decimal,
        exit_price: Decimal,
        size: Decimal,
        realized_pnl: Decimal,
        entry_time: datetime,
        exit_time: datetime,
        close_reason: str = "manual",
        execution_metadata: Optional[Dict[str, Any]] = None
    ):
        """
        STEP 5.9: Log complete trade record.
        
        Args:
            position_id: Unique position identifier
            strategy_id: Strategy that executed the trade
            symbol: Trading symbol
            side: "long" or "short"
            entry_price: Entry price (Decimal)
            exit_price: Exit price (Decimal)
            size: Position size (Decimal)
            realized_pnl: Realized profit/loss (Decimal)
            entry_time: Position open timestamp
            exit_time: Position close timestamp
            close_reason: Reason for closing (manual, take_profit, stop_loss, forced_liquidation)
            execution_metadata: Additional execution details
        """
        trade_record = {
            "trade_id": f"{position_id}_{exit_time.timestamp()}",
            "position_id": position_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "entry_price": str(entry_price),
            "exit_price": str(exit_price),
            "size": str(size),
            "realized_pnl": str(realized_pnl),
            "entry_time": entry_time.isoformat() if entry_time else None,
            "exit_time": exit_time.isoformat() if exit_time else None,
            "duration_seconds": (exit_time - entry_time).total_seconds() if entry_time and exit_time else 0,
            "close_reason": close_reason,
            "pnl_percentage": str(realized_pnl / (entry_price * size) * 100) if entry_price and size else "0",
            "execution_metadata": execution_metadata or {},
            "logged_at": datetime.now().isoformat(),
        }
        
        # Store in main history
        self.trades.append(trade_record)
        
        # Store in strategy-specific history
        self.trades_by_strategy[strategy_id].append(trade_record)
        
        # Store in symbol-specific history
        self.trades_by_symbol[symbol].append(trade_record)
        
        # Update daily stats
        date_key = exit_time.strftime("%Y-%m-%d") if exit_time else datetime.now().strftime("%Y-%m-%d")
        if date_key not in self.daily_stats:
            self.daily_stats[date_key] = {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": Decimal('0'),
                "strategies": set(),
                "symbols": set(),
            }
        
        self.daily_stats[date_key]["total_trades"] += 1
        self.daily_stats[date_key]["strategies"].add(strategy_id)
        self.daily_stats[date_key]["symbols"].add(symbol)
        
        if realized_pnl > 0:
            self.daily_stats[date_key]["winning_trades"] += 1
        elif realized_pnl < 0:
            self.daily_stats[date_key]["losing_trades"] += 1
        
        self.daily_stats[date_key]["total_pnl"] += realized_pnl
        
        logger.info(
            f"TRADE_LOGGED: {symbol} {side} | Entry: {entry_price} → Exit: {exit_price} | "
            f"PnL: ${realized_pnl} | Strategy: {strategy_id} | Reason: {close_reason}"
        )
    
    def get_trade_history(
        self,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get filtered trade history.
        
        Args:
            strategy_id: Filter by strategy
            symbol: Filter by symbol
            start_time: Filter trades after this time
            end_time: Filter trades before this time
            limit: Maximum number of trades to return
        """
        # Get base collection
        if strategy_id:
            trades = list(self.trades_by_strategy.get(strategy_id, []))
        elif symbol:
            trades = list(self.trades_by_symbol.get(symbol, []))
        else:
            trades = list(self.trades)
        
        # Apply time filters
        if start_time:
            trades = [t for t in trades if t["exit_time"] and datetime.fromisoformat(t["exit_time"]) >= start_time]
        
        if end_time:
            trades = [t for t in trades if t["exit_time"] and datetime.fromisoformat(t["exit_time"]) <= end_time]
        
        # Sort by exit time descending (most recent first)
        trades.sort(key=lambda x: x["exit_time"] or "", reverse=True)
        
        return trades[:limit]
    
    def get_daily_summary(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get daily trading summary.
        
        Args:
            date: Date string in format "YYYY-MM-DD". Defaults to today.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        stats = self.daily_stats.get(date, {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": Decimal('0'),
            "strategies": set(),
            "symbols": set(),
        })
        
        total = stats["total_trades"]
        wins = stats["winning_trades"]
        
        return {
            "date": date,
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": stats["losing_trades"],
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": str(stats["total_pnl"]),
            "strategies": list(stats["strategies"]),
            "symbols": list(stats["symbols"]),
        }
    
    def get_strategy_performance(self, strategy_id: str) -> Dict[str, Any]:
        """
        Get performance metrics for a specific strategy.
        
        Args:
            strategy_id: Strategy identifier
        """
        trades = list(self.trades_by_strategy.get(strategy_id, []))
        
        if not trades:
            return {
                "strategy_id": strategy_id,
                "total_trades": 0,
                "total_pnl": "0",
                "win_rate": 0,
            }
        
        total_pnl = sum(Decimal(t["realized_pnl"]) for t in trades)
        wins = sum(1 for t in trades if Decimal(t["realized_pnl"]) > 0)
        
        return {
            "strategy_id": strategy_id,
            "total_trades": len(trades),
            "total_pnl": str(total_pnl),
            "win_rate": wins / len(trades),
            "avg_pnl": str(total_pnl / len(trades)),
            "symbols_traded": list(set(t["symbol"] for t in trades)),
        }


@dataclass
class PortfolioSnapshot:
    """
    STEP 5.10: Portfolio Snapshot for Recovery and Analytics
    
    Complete portfolio state captured at a point in time.
    Used for:
    - Disaster recovery
    - Analytics and backtesting
    - Debugging and auditing
    - State comparison over time
    """
    # Identification
    snapshot_id: str
    timestamp: datetime
    sequence_number: int
    
    # Capital State
    total_capital: Decimal
    available_capital: Decimal
    used_capital: Decimal
    
    # Position State
    positions: List[Dict[str, Any]]  # Serialized positions
    position_count: int
    open_position_count: int
    
    # PnL State
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    
    # Risk State
    margin_used: Decimal
    margin_available: Decimal
    margin_level: Decimal
    
    # Exposure State
    total_exposure: Decimal
    symbol_exposure: Dict[str, Decimal]
    strategy_exposure: Dict[str, Decimal]
    
    # Metadata
    trigger_reason: str  # scheduled, trade, price_update, manual
    checksum: str  # For integrity verification
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot to dictionary."""
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp.isoformat(),
            "sequence_number": self.sequence_number,
            "capital": {
                "total": str(self.total_capital),
                "available": str(self.available_capital),
                "used": str(self.used_capital),
            },
            "positions": {
                "count": self.position_count,
                "open_count": self.open_position_count,
                "details": self.positions,
            },
            "pnl": {
                "realized": str(self.realized_pnl),
                "unrealized": str(self.unrealized_pnl),
                "total": str(self.total_pnl),
            },
            "margin": {
                "used": str(self.margin_used),
                "available": str(self.margin_available),
                "level": str(self.margin_level),
            },
            "exposure": {
                "total": str(self.total_exposure),
                "by_symbol": {k: str(v) for k, v in self.symbol_exposure.items()},
                "by_strategy": {k: str(v) for k, v in self.strategy_exposure.items()},
            },
            "trigger_reason": self.trigger_reason,
            "checksum": self.checksum,
        }


class PortfolioSnapshotSystem:
    """
    STEP 5.10: Portfolio Snapshot System
    
    Manages periodic snapshots of portfolio state for:
    - Recovery from crashes
    - Analytics and debugging
    - Audit trails
    - State comparison
    
    Snapshots are triggered:
    - Periodically (every N seconds)
    - On trade execution
    - On price update
    - On manual request
    """
    
    def __init__(
        self,
        portfolio_manager: 'PortfolioManager',
        max_snapshots: int = 1000,
        auto_snapshot_interval: int = 300  # 5 minutes
    ):
        self.pm = portfolio_manager
        self.max_snapshots = max_snapshots
        self.auto_snapshot_interval = auto_snapshot_interval
        
        # Storage
        self.snapshots: deque = deque(maxlen=max_snapshots)
        self.sequence_number = 0
        
        # Last auto-snapshot time
        self.last_auto_snapshot: Optional[datetime] = None
    
    def create_snapshot(self, trigger_reason: str = "manual") -> PortfolioSnapshot:
        """
        Create a new portfolio snapshot.
        
        Args:
            trigger_reason: Why the snapshot was created
            
        Returns:
            PortfolioSnapshot with complete state
        """
        self.sequence_number += 1
        timestamp = datetime.now()
        
        # Get margin metrics
        margin_metrics = self.pm.get_margin_metrics()
        
        # Get exposure metrics
        exposure_metrics = self.pm.get_exposure_metrics()
        
        # Get PnL summary
        pnl_summary = self.pm.get_pnl_summary()
        
        # Serialize positions
        positions = []
        open_count = 0
        for pos in self.pm.positions.values():
            pos_dict = {
                "id": pos.id,
                "strategy_id": pos.strategy_id,
                "symbol": pos.symbol,
                "side": pos.side,
                "is_open": pos.is_open,
                "quantity": str(pos.quantity),
                "entry_price": str(pos.entry_price),
                "current_price": str(pos.current_price),
                "market_value": str(pos.market_value),
                "unrealized_pnl": str(pos.unrealized_pnl),
                "realized_pnl": str(pos.realized_pnl) if pos.realized_pnl else None,
            }
            positions.append(pos_dict)
            if pos.is_open:
                open_count += 1
        
        # Calculate checksum for integrity
        state_string = f"{timestamp}{self.sequence_number}{self.pm.total_capital}{open_count}"
        checksum = hashlib.sha256(state_string.encode()).hexdigest()[:16]
        
        snapshot = PortfolioSnapshot(
            snapshot_id=f"snap_{timestamp.strftime('%Y%m%d_%H%M%S')}_{self.sequence_number}",
            timestamp=timestamp,
            sequence_number=self.sequence_number,
            total_capital=self.pm.total_capital,
            available_capital=self.pm.available_capital,
            used_capital=self.pm.used_capital,
            positions=positions,
            position_count=len(positions),
            open_position_count=open_count,
            realized_pnl=pnl_summary.total_realized_pnl,
            unrealized_pnl=pnl_summary.unrealized_pnl,
            total_pnl=pnl_summary.total_pnl,
            margin_used=margin_metrics.margin_used,
            margin_available=margin_metrics.margin_available,
            margin_level=margin_metrics.margin_level,
            total_exposure=exposure_metrics.total_exposure,
            symbol_exposure=exposure_metrics.symbol_exposure,
            strategy_exposure=exposure_metrics.strategy_exposure,
            trigger_reason=trigger_reason,
            checksum=checksum,
        )
        
        # Store snapshot
        self.snapshots.append(snapshot)
        
        logger.info(
            f"📸 SNAPSHOT #{self.sequence_number} created | "
            f"Trigger: {trigger_reason} | "
            f"Positions: {open_count} open | "
            f"PnL: ${pnl_summary.total_pnl}"
        )
        
        return snapshot
    
    def check_auto_snapshot(self) -> Optional[PortfolioSnapshot]:
        """
        Check if auto-snapshot is due and create if needed.
        
        Returns:
            Snapshot if created, None otherwise
        """
        now = datetime.now()
        
        if self.last_auto_snapshot is None:
            self.last_auto_snapshot = now
            return self.create_snapshot(trigger_reason="scheduled")
        
        elapsed = (now - self.last_auto_snapshot).total_seconds()
        
        if elapsed >= self.auto_snapshot_interval:
            self.last_auto_snapshot = now
            return self.create_snapshot(trigger_reason="scheduled")
        
        return None
    
    def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        """Get the most recent snapshot."""
        if self.snapshots:
            return self.snapshots[-1]
        return None
    
    def get_snapshot_by_sequence(self, sequence_number: int) -> Optional[PortfolioSnapshot]:
        """Get snapshot by sequence number."""
        for snap in self.snapshots:
            if snap.sequence_number == sequence_number:
                return snap
        return None
    
    def get_snapshots_in_range(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> List[PortfolioSnapshot]:
        """
        Get snapshots within a time range.
        
        Args:
            start_time: Filter snapshots after this time
            end_time: Filter snapshots before this time
        """
        result = list(self.snapshots)
        
        if start_time:
            result = [s for s in result if s.timestamp >= start_time]
        
        if end_time:
            result = [s for s in result if s.timestamp <= end_time]
        
        return result
    
    def compare_snapshots(
        self,
        snapshot1: PortfolioSnapshot,
        snapshot2: PortfolioSnapshot
    ) -> Dict[str, Any]:
        """
        Compare two snapshots and return differences.
        
        Returns:
            Dict with changes between snapshots
        """
        return {
            "time_delta_seconds": (snapshot2.timestamp - snapshot1.timestamp).total_seconds(),
            "sequence_delta": snapshot2.sequence_number - snapshot1.sequence_number,
            "capital_changes": {
                "total": str(snapshot2.total_capital - snapshot1.total_capital),
                "available": str(snapshot2.available_capital - snapshot1.available_capital),
                "used": str(snapshot2.used_capital - snapshot1.used_capital),
            },
            "pnl_changes": {
                "realized": str(snapshot2.realized_pnl - snapshot1.realized_pnl),
                "unrealized": str(snapshot2.unrealized_pnl - snapshot1.unrealized_pnl),
                "total": str(snapshot2.total_pnl - snapshot1.total_pnl),
            },
            "position_changes": {
                "count_delta": snapshot2.position_count - snapshot1.position_count,
                "open_count_delta": snapshot2.open_position_count - snapshot1.open_position_count,
            },
            "margin_changes": {
                "level_delta": str(snapshot2.margin_level - snapshot1.margin_level),
            },
        }
    
    def get_recovery_state(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest snapshot for recovery purposes.
        
        Returns:
            Dict with recovery state or None if no snapshots
        """
        latest = self.get_latest_snapshot()
        if not latest:
            return None
        
        return {
            "can_recover": True,
            "snapshot": latest.to_dict(),
            "recovery_time": datetime.now().isoformat(),
            "data_freshness_seconds": (datetime.now() - latest.timestamp).total_seconds(),
        }
    
    def get_snapshot_summary(self) -> Dict[str, Any]:
        """Get summary of snapshot system status."""
        latest = self.get_latest_snapshot()
        
        return {
            "total_snapshots": len(self.snapshots),
            "max_snapshots": self.max_snapshots,
            "auto_interval_seconds": self.auto_snapshot_interval,
            "latest_snapshot": latest.to_dict() if latest else None,
            "latest_snapshot_age_seconds": (
                (datetime.now() - latest.timestamp).total_seconds()
                if latest else None
            ),
            "sequence_number": self.sequence_number,
        }


@dataclass
class PortfolioPnL:
    """
    STEP 5.7: Real-Time PnL Engine - Portfolio PnL summary with Decimal precision.
    
    Tracks three types of PnL:
    - Realized PnL: Profits/losses from closed positions
    - Unrealized PnL: Profits/losses from open positions
    - Total PnL: Sum of realized + unrealized
    
    Updated on:
    - Price updates (affects unrealized)
    - Trade executions (affects realized)
    """
    # Realized PnL - Decimal for financial precision
    daily_realized_pnl: Decimal = field(default_factory=lambda: Decimal('0'))
    total_realized_pnl: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Unrealized PnL - Decimal for financial precision
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal('0'))
    unrealized_pnl_pct: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Total PnL - Decimal for financial precision
    total_pnl: Decimal = field(default_factory=lambda: Decimal('0'))
    total_pnl_pct: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # Breakdown by strategy - Decimal for financial precision
    strategy_pnl: Dict[str, Decimal] = field(default_factory=dict)
    
    # Breakdown by symbol - Decimal for financial precision
    symbol_pnl: Dict[str, Decimal] = field(default_factory=dict)
    
    # Performance metrics - DECIMAL for consistency
    win_count: int = 0
    loss_count: int = 0
    win_rate: Decimal = field(default_factory=lambda: Decimal('0'))
    avg_win: Decimal = field(default_factory=lambda: Decimal('0'))
    avg_loss: Decimal = field(default_factory=lambda: Decimal('0'))
    profit_factor: Decimal = field(default_factory=lambda: Decimal('0'))
    
    # STEP 5.7: Real-time tracking metadata
    last_update_time: datetime = field(default_factory=datetime.now)
    last_price_update: Optional[datetime] = None
    last_trade_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_realized_pnl": str(self.daily_realized_pnl),
            "total_realized_pnl": str(self.total_realized_pnl),
            "unrealized_pnl": str(self.unrealized_pnl),
            "unrealized_pnl_pct": str(self.unrealized_pnl_pct),
            "total_pnl": str(self.total_pnl),
            "total_pnl_pct": str(self.total_pnl_pct),
            "strategy_pnl": {k: str(v) for k, v in self.strategy_pnl.items()},
            "symbol_pnl": {k: str(v) for k, v in self.symbol_pnl.items()},
            "last_update_time": self.last_update_time.isoformat() if self.last_update_time else None,
            "last_price_update": self.last_price_update.isoformat() if self.last_price_update else None,
            "last_trade_time": self.last_trade_time.isoformat() if self.last_trade_time else None,
        }


class RealTimePnLEngine:
    """
    STEP 5.7: Real-Time PnL Engine
    
    Responsibilities:
    1. Compute Realized PnL on position close
    2. Compute Unrealized PnL on price update
    3. Track Total PnL (Realized + Unrealized)
    4. Maintain PnL history for analytics
    
    Triggers:
    - Price Update: Recalculates unrealized PnL for all open positions
    - Trade Execution: Records realized PnL when position closes
    """
    
    def __init__(self):
        # PnL history tracking
        self.pnl_history: deque = deque(maxlen=10000)  # Rolling history
        self.daily_pnl_reset_time: datetime = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Position-level PnL tracking
        self.position_realized_pnl: Dict[str, Decimal] = {}  # position_id -> realized_pnl
        self.position_unrealized_pnl: Dict[str, Decimal] = {}  # position_id -> unrealized_pnl
        
        # Strategy-level aggregation
        self.strategy_realized_pnl: Dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
        self.strategy_unrealized_pnl: Dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
        
        # Symbol-level aggregation
        self.symbol_realized_pnl: Dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
        self.symbol_unrealized_pnl: Dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    
    def update_unrealized_pnl(
        self,
        position_id: str,
        strategy_id: str,
        symbol: str,
        unrealized_pnl: Decimal,
        timestamp: Optional[datetime] = None
    ):
        """
        STEP 5.7: Update unrealized PnL on price update.
        
        Args:
            position_id: Unique position identifier
            strategy_id: Strategy identifier
            symbol: Trading symbol
            unrealized_pnl: Current unrealized PnL value
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        # Update position-level tracking
        old_unrealized = self.position_unrealized_pnl.get(position_id, Decimal('0'))
        self.position_unrealized_pnl[position_id] = unrealized_pnl
        
        # Update strategy aggregation
        strategy_delta = unrealized_pnl - old_unrealized
        self.strategy_unrealized_pnl[strategy_id] += strategy_delta
        
        # Update symbol aggregation
        self.symbol_unrealized_pnl[symbol] += strategy_delta
        
        # Record history
        self.pnl_history.append({
            "timestamp": timestamp.isoformat(),
            "event": "price_update",
            "position_id": position_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "unrealized_pnl": str(unrealized_pnl),
            "unrealized_pnl_change": str(strategy_delta),
        })
    
    def record_realized_pnl(
        self,
        position_id: str,
        strategy_id: str,
        symbol: str,
        realized_pnl: Decimal,
        close_price: Decimal,
        timestamp: Optional[datetime] = None
    ):
        """
        STEP 5.7: Record realized PnL on position close.
        
        Args:
            position_id: Unique position identifier
            strategy_id: Strategy identifier
            symbol: Trading symbol
            realized_pnl: Realized PnL from position close
            close_price: Exit price
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        # Clear unrealized for this position (now realized)
        if position_id in self.position_unrealized_pnl:
            old_unrealized = self.position_unrealized_pnl[position_id]
            del self.position_unrealized_pnl[position_id]
            
            # Adjust strategy/symbol unrealized
            self.strategy_unrealized_pnl[strategy_id] -= old_unrealized
            self.symbol_unrealized_pnl[symbol] -= old_unrealized
        
        # Record realized PnL
        self.position_realized_pnl[position_id] = realized_pnl
        self.strategy_realized_pnl[strategy_id] += realized_pnl
        self.symbol_realized_pnl[symbol] += realized_pnl
        
        # Record history
        self.pnl_history.append({
            "timestamp": timestamp.isoformat(),
            "event": "trade_close",
            "position_id": position_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "realized_pnl": str(realized_pnl),
            "close_price": str(close_price),
        })
    
    def reset_daily_pnl(self):
        """Reset daily PnL counters at day boundary."""
        self.daily_pnl_reset_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    def get_current_pnl_state(self) -> Dict[str, Any]:
        """
        STEP 5.7: Get current PnL state across all levels.
        
        Returns:
            Dict with portfolio, strategy, and symbol level PnL
        """
        total_realized = sum(self.position_realized_pnl.values())
        total_unrealized = sum(self.position_unrealized_pnl.values())
        
        return {
            "portfolio": {
                "realized_pnl": str(total_realized),
                "unrealized_pnl": str(total_unrealized),
                "total_pnl": str(total_realized + total_unrealized),
            },
            "by_strategy": {
                k: {
                    "realized": str(self.strategy_realized_pnl[k]),
                    "unrealized": str(self.strategy_unrealized_pnl[k]),
                    "total": str(self.strategy_realized_pnl[k] + self.strategy_unrealized_pnl[k]),
                }
                for k in set(self.strategy_realized_pnl.keys()) | set(self.strategy_unrealized_pnl.keys())
            },
            "by_symbol": {
                k: {
                    "realized": str(self.symbol_realized_pnl[k]),
                    "unrealized": str(self.symbol_unrealized_pnl[k]),
                    "total": str(self.symbol_realized_pnl[k] + self.symbol_unrealized_pnl[k]),
                }
                for k in set(self.symbol_realized_pnl.keys()) | set(self.symbol_unrealized_pnl.keys())
            },
        }


@dataclass
class ExposureLimits:
    """
    STEP 5.6: Exposure limits configuration for risk control.
    
    Defines maximum exposure at three levels:
    1. Per-position: Maximum size for any single position
    2. Per-symbol: Maximum exposure for any symbol (across all strategies)
    3. Per-portfolio: Maximum total exposure (sum of all positions)
    
    All limits use Decimal for financial precision.
    """
    # Per-position limits (absolute values)
    max_position_size: Optional[Decimal] = None  # Max position quantity
    max_position_notional: Optional[Decimal] = None  # Max position value in USD
    
    # Per-symbol limits (aggregated across all strategies)
    max_symbol_exposure: Optional[Decimal] = None  # Max notional per symbol
    max_symbol_exposure_pct: Optional[Decimal] = None  # Max % of portfolio per symbol
    
    # Portfolio-wide limits
    max_total_exposure: Optional[Decimal] = None  # Max total notional
    max_total_exposure_pct: Optional[Decimal] = field(default_factory=lambda: Decimal('2.0'))  # 200% default (2x leverage)
    max_gross_leverage: Optional[Decimal] = None  # Max gross leverage ratio
    max_net_leverage: Optional[Decimal] = None  # Max net leverage ratio
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_position_size": str(self.max_position_size) if self.max_position_size else None,
            "max_position_notional": str(self.max_position_notional) if self.max_position_notional else None,
            "max_symbol_exposure": str(self.max_symbol_exposure) if self.max_symbol_exposure else None,
            "max_symbol_exposure_pct": str(self.max_symbol_exposure_pct) if self.max_symbol_exposure_pct else None,
            "max_total_exposure": str(self.max_total_exposure) if self.max_total_exposure else None,
            "max_total_exposure_pct": str(self.max_total_exposure_pct) if self.max_total_exposure_pct else None,
            "max_gross_leverage": str(self.max_gross_leverage) if self.max_gross_leverage else None,
            "max_net_leverage": str(self.max_net_leverage) if self.max_net_leverage else None,
        }


class ExposureExceeded(Exception):
    """
    STEP 5.6: Raised when a trade would exceed exposure limits.
    
    Contains details about which limit was exceeded and by how much.
    """
    def __init__(
        self,
        limit_type: str,
        limit_value: Decimal,
        current_value: Decimal,
        requested_value: Decimal,
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None
    ):
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.current_value = current_value
        self.requested_value = requested_value
        self.symbol = symbol
        self.strategy_id = strategy_id
        
        excess = requested_value - limit_value
        super().__init__(
            f"EXPOSURE LIMIT EXCEEDED: {limit_type} | "
            f"Limit: {limit_value}, Requested: {requested_value}, "
            f"Excess: {excess} | "
            f"Symbol: {symbol}, Strategy: {strategy_id}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# CONFLICT DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

class ConflictDetector:
    """
    STEP 5.5: Multi-strategy conflict detector and resolver.
    
    Handles strategy collisions with multiple resolution modes.
    
    DEFAULT: NET_POSITION - takes net of conflicting positions
    """
    
    def __init__(self, resolution_strategy: ConflictResolution = ConflictResolution.NET_POSITION):
        """
        Initialize conflict detector.
        
        STEP 5.5: Default is NET_POSITION for stable portfolio behavior.
        """
        self.resolution_strategy = resolution_strategy
        self.conflict_history: deque = deque(maxlen=1000)
    
    def detect_conflicts(
        self,
        new_position: Position,
        existing_positions: List[Position],
    ) -> List[Dict[str, Any]]:
        """
        Detect conflicts between new position and existing positions.
        
        Conflict Types:
        - Opposite side (long vs short on same symbol)
        - Exposure limit exceeded
        - Strategy priority conflict
        """
        conflicts = []
        
        for existing in existing_positions:
            if existing.symbol != new_position.symbol:
                continue
            
            if not existing.is_open:
                continue
            
            # Opposite side conflict
            if existing.side != new_position.side:
                conflicts.append({
                    "type": "opposite_side",
                    "symbol": new_position.symbol,
                    "existing_position": existing,
                    "new_position": new_position,
                    "existing_strategy": existing.strategy_id,
                    "new_strategy": new_position.strategy_id,
                    "severity": "high",
                })
            
            # Same side - check for concentration
            else:
                combined_size = existing.quantity + new_position.quantity
                if combined_size > existing.quantity * 1.5:  # 50% increase
                    conflicts.append({
                        "type": "concentration",
                        "symbol": new_position.symbol,
                        "existing_position": existing,
                        "new_position": new_position,
                        "existing_strategy": existing.strategy_id,
                        "new_strategy": new_position.strategy_id,
                        "severity": "medium",
                    })
        
        return conflicts
    
    def resolve_conflicts(
        self,
        conflicts: List[Dict],
        strategy_priorities: Dict[str, int],
    ) -> Dict[str, Any]:
        """
        Resolve detected conflicts using configured strategy.
        
        Returns resolution plan with actions to take.
        """
        if not conflicts:
            return {"action": "allow", "reason": "no_conflicts"}
        
        resolution_plan = {
            "action": "review",
            "conflicts": conflicts,
            "cancellations": [],
            "adjustments": [],
        }
        
        for conflict in conflicts:
            if conflict["type"] == "opposite_side":
                # STEP 5.5: NET_POSITION - Default resolution (take net of conflicting positions)
                if self.resolution_strategy == ConflictResolution.NET_POSITION:
                    # Calculate net position
                    existing_pos = conflict["existing_position"]
                    new_pos = conflict["new_position"]
                    
                    # Determine net: long - short
                    if existing_pos.side == "long":
                        net_size = existing_pos.quantity - new_pos.quantity
                    else:
                        net_size = new_pos.quantity - existing_pos.quantity
                    
                    if net_size > 0:
                        # Net is long - reduce or close existing short, keep as long
                        resolution_plan["action"] = "net_position"
                        resolution_plan["reason"] = f"net_long_{net_size}"
                        resolution_plan["adjustments"].append({
                            "position_id": existing_pos.id,
                            "action": "resize_to_net",
                            "net_size": str(net_size),
                            "net_side": "long" if existing_pos.side == "long" else "short",
                            "cancel_opposite": new_pos.id if net_size < existing_pos.quantity else None,
                        })
                    elif net_size < 0:
                        # Net is short
                        resolution_plan["action"] = "net_position"
                        resolution_plan["reason"] = f"net_short_{abs(net_size)}"
                        resolution_plan["adjustments"].append({
                            "position_id": existing_pos.id,
                            "action": "resize_to_net",
                            "net_size": str(abs(net_size)),
                            "net_side": "short" if existing_pos.side == "long" else "long",
                            "cancel_opposite": new_pos.id if abs(net_size) < existing_pos.quantity else None,
                        })
                    else:
                        # Net is zero - close both
                        resolution_plan["action"] = "close_both"
                        resolution_plan["reason"] = "net_zero"
                        resolution_plan["cancellations"].append({
                            "position_id": existing_pos.id,
                            "reason": "net_zero_cancel",
                        })
                    
                    return resolution_plan
                
                # STEP 5.5: REJECT_CONFLICT - Reject all conflicting orders
                elif self.resolution_strategy == ConflictResolution.REJECT_CONFLICT:
                    resolution_plan["action"] = "block"
                    resolution_plan["reason"] = "conflict_rejected"
                    return resolution_plan
                
                elif self.resolution_strategy == ConflictResolution.BLOCK_ALL:
                    resolution_plan["action"] = "block"
                    resolution_plan["reason"] = "opposite_side_conflict"
                    return resolution_plan
                
                elif self.resolution_strategy == ConflictResolution.PRIORITY_WINS:
                    # Compare priorities
                    existing_priority = strategy_priorities.get(
                        conflict["existing_strategy"], 0
                    )
                    new_priority = strategy_priorities.get(
                        conflict["new_strategy"], 0
                    )
                    
                    if new_priority > existing_priority:
                        # Cancel existing position
                        resolution_plan["cancellations"].append({
                            "position_id": conflict["existing_position"].id,
                            "reason": "lower_priority",
                            "symbol": conflict["symbol"],
                        })
                    elif new_priority < existing_priority:
                        # Block new position
                        resolution_plan["action"] = "block"
                        resolution_plan["reason"] = f"lower_priority_than_{conflict['existing_strategy']}"
                        return resolution_plan
                    else:
                        # Same priority - use larger position wins
                        if conflict["new_position"].quantity > conflict["existing_position"].quantity:
                            resolution_plan["cancellations"].append({
                                "position_id": conflict["existing_position"].id,
                                "reason": "smaller_position",
                            })
                        else:
                            resolution_plan["action"] = "block"
                            resolution_plan["reason"] = "smaller_position"
                            return resolution_plan
                
                elif self.resolution_strategy == ConflictResolution.FIRST_WINS:
                    resolution_plan["action"] = "block"
                    resolution_plan["reason"] = "existing_position_first"
                    return resolution_plan
                
                elif self.resolution_strategy == ConflictResolution.LAST_WINS:
                    resolution_plan["cancellations"].append({
                        "position_id": conflict["existing_position"].id,
                        "reason": "last_wins",
                    })
                
                elif self.resolution_strategy == ConflictResolution.SUM_POSITIONS:
                    # Allow both, but flag for review
                    resolution_plan["action"] = "allow"
                    resolution_plan["adjustments"].append({
                        "position_id": conflict["existing_position"].id,
                        "action": "increase_size",
                        "additional_quantity": conflict["new_position"].quantity,
                    })
        
        # Record conflict
        self.conflict_history.append({
            "timestamp": datetime.now().isoformat(),
            "conflicts": len(conflicts),
            "resolution": resolution_plan["action"],
        })
        
        return resolution_plan


# ═══════════════════════════════════════════════════════════════════════════
# PORTFOLIO MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class PortfolioManager:
    """
    Main portfolio management system.
    
    STEP 5.2: Full financial precision with Decimal.
    
    Coordinates capital allocation, position tracking, exposure monitoring,
    conflict resolution, and risk integration.
    """
    
    def __init__(
        self,
        total_capital: Decimal,
        risk_engine: Optional[Any] = None,
        conflict_resolution: ConflictResolution = ConflictResolution.NET_POSITION,
    ):
        """
        Initialize PortfolioManager.
        
        STEP 5.5: Default conflict resolution is NET_POSITION for stable portfolio behavior.
        """
        self.total_capital = to_decimal(total_capital)
        self.risk_engine = risk_engine
        
        # Strategy allocations
        self.allocations: Dict[str, StrategyAllocation] = {}
        self.strategy_priorities: Dict[str, int] = {}
        
        # Position tracking
        self.positions: Dict[str, Position] = {}
        self.open_positions: Dict[str, Set[str]] = defaultdict(set)  # symbol -> position_ids
        self.strategy_positions: Dict[str, Set[str]] = defaultdict(set)  # strategy -> position_ids
        
        # Conflict resolution
        self.conflict_detector = ConflictDetector(conflict_resolution)
        
        # STEP 5.6: Exposure control system
        self.exposure_limits = ExposureLimits()
        self._last_exposure_check: Optional[Dict[str, Any]] = None
        
        # STEP 5.7: Real-time PnL engine
        self.pnl_engine = RealTimePnLEngine()
        
        # STEP 5.8: Margin monitor for liquidation protection
        self.margin_monitor = MarginMonitor(self)
        
        # STEP 5.9: Trade history logger for audit trail
        self.trade_history = TradeHistoryLogger(max_history=10000)
        
        # STEP 5.10: Portfolio snapshot system for recovery
        self.snapshot_system = PortfolioSnapshotSystem(self, max_snapshots=1000)
        
        # Idempotency tracking - prevent double PnL from duplicate executions
        self._applied_executions: Set[str] = set()
        
        # Current prices - STEP 5.2: Decimal for financial precision
        self.current_prices: Dict[str, Decimal] = {}
        
        # Historical tracking
        self.equity_curve: deque = deque(maxlen=10000)
        self.daily_pnl_history: deque = deque(maxlen=365)
        
        # Async safety - use asyncio.Lock for async context
        self._lock = asyncio.Lock()
        
        # Callbacks
        self.position_callbacks: List[Callable[[Position, str], None]] = []  # position, event_type
        self.exposure_callbacks: List[Callable[[ExposureMetrics], None]] = []
        
        logger.info(f"PortfolioManager initialized with ${total_capital:,.2f} capital")
    
    async def register_strategy(
        self,
        strategy_id: str,
        allocation_pct: Decimal,
        priority: int = 0,
        max_position_pct: Decimal = None,
        max_positions: int = 10,
    ) -> StrategyAllocation:
        """Register a strategy with capital allocation."""
        # Convert to Decimal if string/float passed
        allocation_pct = to_decimal(allocation_pct)
        if max_position_pct is None:
            max_position_pct = Decimal("0.05")
        else:
            max_position_pct = to_decimal(max_position_pct)
        
        async with self._lock:
            allocation = StrategyAllocation(
                strategy_id=strategy_id,
                allocation_pct=allocation_pct,
                max_position_pct=max_position_pct,
                max_positions=max_positions,
                allocated_capital=(self.total_capital * allocation_pct).quantize(Decimal("0.01")),
                available_capital=(self.total_capital * allocation_pct).quantize(Decimal("0.01")),
            )
            
            self.allocations[strategy_id] = allocation
            self.strategy_priorities[strategy_id] = priority
            
            logger.info(
                f"Registered strategy {strategy_id}: "
                f"{float(allocation_pct):.1%} allocation = ${allocation.allocated_capital:,.2f}"
            )
            
            return allocation
    
    async def update_strategy_allocation(
        self,
        strategy_id: str,
        new_allocation_pct: Decimal,
    ) -> StrategyAllocation:
        """Dynamically update strategy allocation."""
        # Convert to Decimal if string/float passed
        new_allocation_pct = to_decimal(new_allocation_pct)
        
        async with self._lock:
            if strategy_id not in self.allocations:
                raise ValueError(f"Strategy not found: {strategy_id}")
            
            allocation = self.allocations[strategy_id]
            old_allocation = allocation.allocated_capital
            
            allocation.allocation_pct = new_allocation_pct
            allocation.allocated_capital = (self.total_capital * new_allocation_pct).quantize(Decimal("0.01"))
            
            # Adjust available capital
            if old_allocation > 0:
                used_pct = (allocation.used_capital / old_allocation).quantize(Decimal("0.0001"))
            else:
                used_pct = Decimal("0")
            allocation.used_capital = (allocation.allocated_capital * used_pct).quantize(Decimal("0.01"))
            allocation.available_capital = (allocation.allocated_capital - allocation.used_capital).quantize(Decimal("0.01"))
            
            logger.info(
                f"Updated allocation for {strategy_id}: "
                f"{old_allocation:,.2f} → {allocation.allocated_capital:,.2f}"
            )
            
            return allocation
    
    async def open_position(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        order_id: str = None,
        execution_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Tuple[Position, Dict[str, Any]]:
        """
        Open a new position with conflict detection, exposure limits, and idempotency.
        
        STEP 5.6: Checks exposure limits before opening position.
        Raises ExposureExceeded if limits would be breached.
        """
        async with self._lock:
            # Check strategy exists
            if strategy_id not in self.allocations:
                return None, {"error": f"Strategy not found: {strategy_id}"}
            
            allocation = self.allocations[strategy_id]
            
            # Check strategy limits
            if len(self.strategy_positions[strategy_id]) >= allocation.max_positions:
                return None, {"error": "max_positions_reached"}
            
            # Check capital availability
            required_capital = quantity * entry_price
            if required_capital > allocation.available_capital:
                return None, {"error": "insufficient_capital"}
            
            # Check position size limit
            max_position_value = allocation.allocated_capital * allocation.max_position_pct
            if required_capital > max_position_value:
                return None, {"error": "position_size_exceeded"}
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 5.6: EXPOSURE LIMIT CHECK
            # ═══════════════════════════════════════════════════════════════════
            exposure_check = self._check_exposure_limits(
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price
            )
            
            if not exposure_check["passes"]:
                limit_type = exposure_check["limit_type"]
                limit_value = exposure_check["limit_value"]
                requested = exposure_check["requested"]
                
                logger.error(
                    f"EXPOSURE LIMIT BLOCKED: {strategy_id} {side} {quantity} {symbol} @ {entry_price} | "
                    f"{limit_type} limit={limit_value}, requested={requested}"
                )
                
                return None, {
                    "error": "exposure_limit_exceeded",
                    "limit_type": limit_type,
                    "limit_value": str(limit_value),
                    "requested_value": str(requested),
                    "current_exposure": str(exposure_check.get("current", 0))
                }
            
            # ═══════════════════════════════════════════════════════════════════
            # IDEMPOTENCY CHECK: Prevent double PnL from duplicate executions
            # ═══════════════════════════════════════════════════════════════════
            execution_id = metadata.get('execution_id') if metadata else None
            if execution_id:
                if execution_id in self._applied_executions:
                    logger.warning(
                        f"PORTFOLIO_IDEMPOTENT_SKIP: execution_id={execution_id} "
                        f"already applied to portfolio",
                        extra={
                            "event": "PORTFOLIO_IDEMPOTENT_SKIP",
                            "execution_id": execution_id,
                            "strategy_id": strategy_id,
                            "symbol": symbol,
                            "action": "open_position",
                        }
                    )
                    return None, {"skipped": True, "reason": "execution_already_applied"}
                
                # Track execution_id to prevent future duplicates
                self._applied_executions.add(execution_id)
            
            # Create position with execution tracking
            position_id = f"{strategy_id}_{symbol}_{datetime.now().timestamp()}"
            position = Position(
                id=position_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                entry_time=datetime.now(),
                quantity=quantity,
                current_price=entry_price,
                metadata=metadata or {},
                execution_ids=[execution_id] if execution_id else [],  # Track in position history
            )
            
            # Detect conflicts
            existing_positions = [
                self.positions[pid]
                for pid in self.open_positions.get(symbol, set())
                if pid in self.positions
            ]
            
            conflicts = self.conflict_detector.detect_conflicts(
                position, existing_positions
            )
            
            # Resolve conflicts
            resolution = self.conflict_detector.resolve_conflicts(
                conflicts, self.strategy_priorities
            )
            
            # Handle resolution
            if resolution["action"] == "block":
                return None, {
                    "error": "conflict_blocked",
                    "reason": resolution["reason"],
                    "conflicts": conflicts,
                }
            
            # Cancel positions if needed
            for cancel in resolution.get("cancellations", []):
                await self.close_position(
                    cancel["position_id"],
                    entry_price,  # Use current price for now
                    reason=cancel["reason"],
                )
            
            # Store position
            self.positions[position_id] = position
            self.open_positions[symbol].add(position_id)
            self.strategy_positions[strategy_id].add(position_id)
            
            # Update allocation
            allocation.used_capital += required_capital
            allocation.available_capital -= required_capital
            
            logger.info(
                f"Opened position: {position_id} | "
                f"{strategy_id} {side} {quantity} {symbol} @ {entry_price}"
            )
            
            # STEP 5.4: Validate portfolio consistency after trade
            consistency_result = self.validate_consistency()
            if not consistency_result["is_valid"]:
                logger.error(
                    f"🚨 INCONSISTENCY DETECTED after opening position {position_id}: "
                    f"{consistency_result['errors']}"
                )
            
            # Notify callbacks
            for callback in self.position_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(position, "opened")
                    else:
                        callback(position, "opened")
                except Exception as e:
                    logger.error(f"Position callback error: {e}")
            
            result = {
                "resolution": resolution,
                "conflicts_detected": len(conflicts),
            }
            
            # Include consistency check in result
            result["consistency_check"] = consistency_result
            
            return position, result
    
    async def close_position(
        self,
        position_id: str,
        exit_price: Decimal,
        reason: str = "manual",
        execution_id: Optional[str] = None,
    ) -> Optional[Position]:
        """Close an existing position with idempotency check."""
        async with self._lock:
            if position_id not in self.positions:
                return None
            
            position = self.positions[position_id]
            
            if not position.is_open:
                return position
            
            # ═══════════════════════════════════════════════════════════════════
            # IDEMPOTENCY CHECK: Prevent double PnL from duplicate executions
            # ═══════════════════════════════════════════════════════════════════
            if execution_id:
                if execution_id in self._applied_executions:
                    logger.warning(
                        f"PORTFOLIO_IDEMPOTENT_SKIP: execution_id={execution_id} "
                        f"already applied to portfolio",
                        extra={
                            "event": "PORTFOLIO_IDEMPOTENT_SKIP",
                            "execution_id": execution_id,
                            "position_id": position_id,
                            "action": "close_position",
                        }
                    )
                    return position
                
                # Track execution_id to prevent future duplicates
                self._applied_executions.add(execution_id)
                # Add to position history
                position.execution_ids.append(execution_id)
            
            # Close position
            position.close(exit_price)
            
            # Update allocation
            if position.strategy_id in self.allocations:
                allocation = self.allocations[position.strategy_id]
                released_capital = position.quantity * position.entry_price
                allocation.used_capital -= released_capital
                allocation.available_capital += released_capital
                
                # Update PnL
                allocation.total_pnl += position.realized_pnl
                if position.realized_pnl > 0:
                    allocation.win_count += 1
                else:
                    allocation.loss_count += 1
            
            # Update tracking
            self.open_positions[position.symbol].discard(position_id)
            
            logger.info(
                f"Closed position: {position_id} | "
                f"PnL: ${position.realized_pnl:,.2f} | Reason: {reason}"
            )
            
            # STEP 5.7: Record realized PnL in engine
            self.pnl_engine.record_realized_pnl(
                position_id=position_id,
                strategy_id=position.strategy_id,
                symbol=position.symbol,
                realized_pnl=position.realized_pnl,
                close_price=position.exit_price,
                timestamp=position.exit_time
            )
            
            # STEP 5.9: Log trade for audit trail
            self.trade_history.log_trade(
                position_id=position_id,
                strategy_id=position.strategy_id,
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=position.exit_price,
                size=position.quantity,
                realized_pnl=position.realized_pnl,
                entry_time=position.entry_time,
                exit_time=position.exit_time,
                close_reason=reason,
            )
            
            # STEP 5.10: Create snapshot after trade for recovery
            self.snapshot_system.create_snapshot(trigger_reason="trade")
            
            # STEP 5.4: Validate portfolio consistency after trade
            consistency_result = self.validate_consistency()
            if not consistency_result["is_valid"]:
                logger.error(
                    f"🚨 INCONSISTENCY DETECTED after closing position {position_id}: "
                    f"{consistency_result['errors']}"
                )
            
            # Notify callbacks
            for callback in self.position_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(position, "closed")
                    else:
                        callback(position, "closed")
                except Exception as e:
                    logger.error(f"Position callback error: {e}")
            
            return position
    
    async def update_prices(self, prices: Dict[str, Decimal]):
        """
        Update market prices and recalculate PnL.
        
        STEP 5.7: Uses RealTimePnLEngine to track unrealized PnL.
        STEP 5.8: Triggers margin health check for liquidation protection.
        """
        async with self._lock:
            # Convert all prices to Decimal
            decimal_prices = {symbol: to_decimal(price) for symbol, price in prices.items()}
            self.current_prices.update(decimal_prices)
            
            timestamp = datetime.now()
            
            for position in self.positions.values():
                if position.is_open and position.symbol in decimal_prices:
                    # Update position price
                    position.update_price(decimal_prices[position.symbol])
                    
                    # STEP 5.7: Update unrealized PnL in engine
                    self.pnl_engine.update_unrealized_pnl(
                        position_id=position.id,
                        strategy_id=position.strategy_id,
                        symbol=position.symbol,
                        unrealized_pnl=position.unrealized_pnl,
                        timestamp=timestamp
                    )
            
            # STEP 5.8: Check margin health after price update
            margin_metrics = self.get_margin_metrics()
            margin_check = self.margin_monitor.check_margin_health(margin_metrics)
            
            if margin_check["status"] == "liquidation_risk":
                logger.critical(
                    f"🚨 LIQUIDATION PROTECTION ACTIVATED: "
                    f"{len(margin_check['positions_reduced'])} positions reduced"
                )
            
            # STEP 5.10: Auto-snapshot check for recovery
            self.snapshot_system.check_auto_snapshot()
    
    async def _check_exposure_limits(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal
    ) -> Dict[str, Any]:
        """
        STEP 5.6: Check exposure limits before opening a position.
        
        Validates:
        1. Per-position size limits (max_position_size, max_position_notional)
        2. Per-symbol exposure limits (max_symbol_exposure, max_symbol_exposure_pct)
        3. Portfolio-wide limits (max_total_exposure, max_total_exposure_pct, leverage)
        
        Returns:
            Dict with: passes (bool), limit_type, limit_value, requested, current
        """
        async with self._lock:
            limits = self.exposure_limits
            new_notional = quantity * entry_price
            
            # Calculate current exposures
            current_symbol_exposure = Decimal('0')
            for pos_id in self.open_positions.get(symbol, set()):
                position = self.positions.get(pos_id)
                if position and position.is_open:
                    current_symbol_exposure += abs(position.market_value)
            
            current_total_exposure = Decimal('0')
            for position in self.positions.values():
                if position.is_open:
                    current_total_exposure += abs(position.market_value)
            
            new_total_exposure = current_total_exposure + new_notional
            new_symbol_exposure = current_symbol_exposure + new_notional
            
            # CHECK 1: Per-position limits
            if limits.max_position_size and quantity > limits.max_position_size:
                return {
                    "passes": False,
                    "limit_type": "max_position_size",
                    "limit_value": limits.max_position_size,
                    "requested": quantity,
                    "current": current_symbol_exposure
                }
            
            if limits.max_position_notional and new_notional > limits.max_position_notional:
                return {
                    "passes": False,
                    "limit_type": "max_position_notional",
                    "limit_value": limits.max_position_notional,
                    "requested": new_notional,
                    "current": current_symbol_exposure
                }
            
            # CHECK 2: Per-symbol limits
            if limits.max_symbol_exposure and new_symbol_exposure > limits.max_symbol_exposure:
                return {
                    "passes": False,
                    "limit_type": "max_symbol_exposure",
                    "limit_value": limits.max_symbol_exposure,
                    "requested": new_symbol_exposure,
                    "current": current_symbol_exposure
                }
            
            if limits.max_symbol_exposure_pct:
                max_symbol_value = self.total_capital * (limits.max_symbol_exposure_pct / 100)
                if new_symbol_exposure > max_symbol_value:
                    return {
                        "passes": False,
                        "limit_type": "max_symbol_exposure_pct",
                        "limit_value": limits.max_symbol_exposure_pct,
                        "requested": new_symbol_exposure,
                        "current": current_symbol_exposure
                    }
            
            # CHECK 3: Portfolio-wide limits
            if limits.max_total_exposure and new_total_exposure > limits.max_total_exposure:
                return {
                    "passes": False,
                    "limit_type": "max_total_exposure",
                    "limit_value": limits.max_total_exposure,
                    "requested": new_total_exposure,
                    "current": current_total_exposure
                }
            
            if limits.max_total_exposure_pct:
                max_total_value = self.total_capital * (limits.max_total_exposure_pct / 100)
                if new_total_exposure > max_total_value:
                    return {
                        "passes": False,
                        "limit_type": "max_total_exposure_pct",
                        "limit_value": limits.max_total_exposure_pct,
                        "requested": new_total_exposure,
                        "current": current_total_exposure
                    }
            
            # CHECK 4: Leverage limits
            if limits.max_gross_leverage and self.total_capital > 0:
                new_gross_leverage = new_total_exposure / self.total_capital
                if new_gross_leverage > limits.max_gross_leverage:
                    return {
                        "passes": False,
                        "limit_type": "max_gross_leverage",
                        "limit_value": limits.max_gross_leverage,
                        "requested": new_gross_leverage,
                        "current": current_total_exposure / self.total_capital if self.total_capital > 0 else Decimal('0')
                    }
            
            # All checks passed
            return {
                "passes": True,
                "limit_type": None,
                "limit_value": None,
                "requested": new_notional,
                "current": current_symbol_exposure
            }
    
    async def set_exposure_limits(self, limits: ExposureLimits):
        """
        STEP 5.6: Set exposure limits for risk control.
        
        Args:
            limits: ExposureLimits instance with desired limits
        """
        async with self._lock:
            self.exposure_limits = limits
            logger.info(
                f"EXPOSURE_LIMITS_SET: position_size={limits.max_position_size}, "
                f"symbol_exposure={limits.max_symbol_exposure_pct}%, "
                f"total_exposure={limits.max_total_exposure_pct}%, "
                f"max_leverage={limits.max_gross_leverage}"
            )
    
    async def get_margin_metrics(self) -> MarginMetrics:
        """
        STEP 5.8: Calculate current margin metrics.
        
        Tracks:
        - margin_used: Total margin required for open positions
        - margin_available: Remaining margin capacity
        - margin_level: Equity / Used Margin (safety ratio)
        
        Returns:
            MarginMetrics with current margin status
        """
        async with self._lock:
            metrics = MarginMetrics()
            
            # Calculate total position value (used as proxy for margin required)
            total_position_value = Decimal('0')
            for position in self.positions.values():
                if position.is_open:
                    total_position_value += abs(position.market_value)
            
            # Calculate equity (total capital + unrealized PnL)
            unrealized_pnl = sum(
                p.unrealized_pnl for p in self.positions.values() if p.is_open
            )
            equity = self.total_capital + unrealized_pnl
            
            # Assume 10% margin requirement for simplicity (adjust as needed)
            margin_required_pct = Decimal('0.10')
            
            metrics.margin_used = total_position_value * margin_required_pct
            metrics.initial_margin_required = metrics.margin_used
            metrics.maintenance_margin_required = metrics.margin_used * Decimal('0.8')
            
            # Available margin
            metrics.margin_available = equity - metrics.margin_used
            
            # Margin utilization percentage
            if equity > 0:
                metrics.margin_utilization_pct = metrics.margin_used / equity
            
            # Margin level (safety ratio): Equity / Used Margin
            if metrics.margin_used > 0:
                metrics.margin_level = equity / metrics.margin_used
            else:
                metrics.margin_level = Decimal('999')  # Infinite if no margin used
            
            # Status flags
            metrics.margin_call_warning = metrics.margin_level < Decimal('1.25')
            metrics.near_liquidation = metrics.margin_level < Decimal('1.10')
            
            return metrics
    
    async def get_exposure_metrics(self) -> ExposureMetrics:
        """Calculate current exposure metrics."""
        async with self._lock:
            metrics = ExposureMetrics()
            
            
            # Calculate required margin
            total_notional = 0.0
            for position in self.positions.values():
                if position.is_open:
                    total_notional += position.market_value
            
            # Assume 10% initial margin, 5% maintenance
            metrics.initial_margin_required = total_notional * 0.1
            metrics.maintenance_margin_required = total_notional * 0.05
            
            # Current margin usage (simplified)
            metrics.margin_used = metrics.initial_margin_required
            metrics.margin_available = self.total_capital - metrics.margin_used
            
            if self.total_capital > 0:
                metrics.margin_utilization_pct = metrics.margin_used / self.total_capital
            
            # Margin level
            if metrics.margin_used > 0:
                metrics.margin_level = self.total_capital / metrics.margin_used
            
            # Warnings
            metrics.margin_call_warning = metrics.margin_level < metrics.margin_call_threshold
            metrics.near_liquidation = metrics.margin_level < metrics.liquidation_threshold
            
            return metrics
    
    async def get_pnl_summary(self) -> PortfolioPnL:
        """
        STEP 5.7: Calculate real-time PnL summary using PnL engine.
        
        Returns accurate real-time performance with:
        - Realized PnL: From closed positions
        - Unrealized PnL: From open positions (updated on price changes)
        - Total PnL: Sum of realized + unrealized
        """
        async with self._lock:
            summary = PortfolioPnL()
            
            # STEP 5.7: Get real-time PnL from engine
            pnl_state = self.pnl_engine.get_current_pnl_state()
            
            # Set portfolio-level PnL
            summary.total_realized_pnl = Decimal(pnl_state["portfolio"]["realized_pnl"])
            summary.unrealized_pnl = Decimal(pnl_state["portfolio"]["unrealized_pnl"])
            summary.total_pnl = Decimal(pnl_state["portfolio"]["total_pnl"])
            
            # Set strategy breakdown
            for strategy_id, pnl_data in pnl_state["by_strategy"].items():
                strategy_total = Decimal(pnl_data["realized"]) + Decimal(pnl_data["unrealized"])
                summary.strategy_pnl[strategy_id] = strategy_total
            
            # Set symbol breakdown
            for symbol, pnl_data in pnl_state["by_symbol"].items():
                symbol_total = Decimal(pnl_data["realized"]) + Decimal(pnl_data["unrealized"])
                summary.symbol_pnl[symbol] = symbol_total
            
            # Calculate percentages
            if self.total_capital > 0:
                summary.total_pnl_pct = summary.total_pnl / self.total_capital
                summary.unrealized_pnl_pct = summary.unrealized_pnl / self.total_capital
            
            # Win rate calculation
            for allocation in self.allocations.values():
                summary.win_count += allocation.win_count
                summary.loss_count += allocation.loss_count
            
            total_trades = summary.win_count + summary.loss_count
            if total_trades > 0:
                summary.win_rate = summary.win_count / total_trades
            
            # Update timestamps
            summary.last_update_time = datetime.now()
            summary.last_price_update = self.pnl_engine.daily_pnl_reset_time
            
            return summary
    
    async def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get complete portfolio summary."""
        async with self._lock:
            exposure_metrics = await self.get_exposure_metrics()
            margin_metrics = await self.get_margin_metrics()
            pnl_summary = await self.get_pnl_summary()
            return {
                "total_capital": self.total_capital,
                "strategies": len(self.allocations),
                "total_positions": len(self.positions),
                "open_positions": sum(1 for p in self.positions.values() if p.is_open),
                "allocations": {
                    sid: alloc.to_dict() for sid, alloc in self.allocations.items()
                },
                "exposure": exposure_metrics.to_dict(),
                "margin": margin_metrics.to_dict(),
                "pnl": pnl_summary.to_dict(),
                "conflicts_last_24h": len([
                    c for c in self.conflict_detector.conflict_history
                    if datetime.fromisoformat(c["timestamp"]) > datetime.now() - timedelta(hours=24)
                ]),
            }


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/api/portfolio", tags=["portfolio-management"])

# Global portfolio manager
_portfolio_manager: Optional[PortfolioManager] = None


def get_portfolio_manager() -> PortfolioManager:
    """Get or create portfolio manager singleton."""
    global _portfolio_manager
    if _portfolio_manager is None:
        _portfolio_manager = PortfolioManager(
            total_capital=Decimal("100000"),  # Default
        )
    return _portfolio_manager


class StrategyRegistrationRequest(BaseModel):
    """Request to register a strategy."""
    strategy_id: str
    allocation_pct: Decimal = Field(..., gt=0, le=1)
    priority: int = 0
    max_position_pct: Decimal = Field(default_factory=lambda: Decimal("0.05"))
    max_positions: int = 10


class OpenPositionRequest(BaseModel):
    """Request to open a position."""
    strategy_id: str
    symbol: str
    side: str = Field(..., pattern="^(long|short)$")
    quantity: Decimal = Field(..., gt=0)
    entry_price: Decimal = Field(..., gt=0)
    metadata: Optional[Dict] = None


class ClosePositionRequest(BaseModel):
    """Request to close a position."""
    exit_price: Decimal = Field(..., gt=0)
    reason: str = "manual"


class PriceUpdateRequest(BaseModel):
    """
    STEP 5.2: Request to update market prices with Decimal precision.
    """
    prices: Dict[str, Decimal]


@router.post("/initialize")
async def initialize_portfolio(total_capital: Decimal = Decimal('100000.0')):
    """
    Initialize portfolio with capital.
    
    STEP 5.2: Uses Decimal for financial precision.
    """
    global _portfolio_manager
    _portfolio_manager = PortfolioManager(total_capital=to_decimal(total_capital))
    
    return {
        "status": "initialized",
        "total_capital": str(_portfolio_manager.total_capital),  # Return as string for precision
    }


@router.post("/strategies/register")
async def register_strategy_endpoint(request: StrategyRegistrationRequest):
    """Register a strategy with capital allocation."""
    manager = get_portfolio_manager()
    
    allocation = manager.register_strategy(
        strategy_id=request.strategy_id,
        allocation_pct=request.allocation_pct,
        priority=request.priority,
        max_position_pct=request.max_position_pct,
        max_positions=request.max_positions,
    )
    
    return allocation.to_dict()


@router.post("/positions/open")
async def open_position_endpoint(request: OpenPositionRequest):
    """Open a new position with conflict detection."""
    manager = get_portfolio_manager()
    
    position, info = await manager.open_position(
        strategy_id=request.strategy_id,
        symbol=request.symbol,
        side=request.side,
        quantity=request.quantity,
        entry_price=request.entry_price,
        metadata=request.metadata,
    )
    
    if position is None:
        raise HTTPException(400, f"Failed to open position: {info.get('error')}")
    
    return {
        "position": position.to_dict(),
        "resolution": info.get("resolution"),
    }


@router.post("/positions/{position_id}/close")
async def close_position_endpoint(position_id: str, request: ClosePositionRequest):
    """Close an existing position."""
    manager = get_portfolio_manager()
    
    position = await manager.close_position(
        position_id=position_id,
        exit_price=request.exit_price,
        reason=request.reason,
    )
    
    if position is None:
        raise HTTPException(404, "Position not found")
    
    return {
        "position": position.to_dict(),
        "realized_pnl": position.realized_pnl,
    }


@router.post("/prices/update")
async def update_prices_endpoint(request: PriceUpdateRequest):
    """Update market prices."""
    manager = get_portfolio_manager()
    manager.update_prices(request.prices)
    
    return {"updated_symbols": len(request.prices)}


@router.get("/detailed-summary")
async def get_portfolio_detailed_summary():
    """Get complete portfolio summary from PortfolioManager (detailed engine view).
    
    NOTE: Use GET /api/portfolio/summary for the standard summary endpoint.
    This endpoint provides the full PortfolioManager state including positions,
    allocations, exposure, margin, and PnL breakdown.
    P1-04 FIX: Renamed from /summary to /detailed-summary to eliminate
    duplicate route registration with routers/portfolio.py.
    """
    manager = get_portfolio_manager()
    return manager.get_portfolio_summary()



@router.get("/exposure")
async def get_exposure_metrics():
    """Get exposure metrics."""
    manager = get_portfolio_manager()
    return manager.get_exposure_metrics().to_dict()


@router.get("/margin")
async def get_margin_metrics():
    """Get margin usage metrics."""
    manager = get_portfolio_manager()
    return manager.get_margin_metrics().to_dict()


@router.get("/margin/health")
async def check_margin_health():
    """
    STEP 5.8: Check margin health status.
    
    Returns current margin level and any warnings or actions taken.
    """
    manager = get_portfolio_manager()
    metrics = manager.get_margin_metrics()
    health = manager.margin_monitor.check_margin_health(metrics)
    return health


@router.get("/margin/status")
async def get_margin_status():
    """
    STEP 5.8: Get comprehensive margin status.
    
    Includes current metrics, thresholds, and historical data.
    """
    manager = get_portfolio_manager()
    return manager.margin_monitor.get_margin_status()


@router.get("/pnl")
async def get_pnl_summary():
    """Get PnL summary."""
    manager = get_portfolio_manager()
    return manager.get_pnl_summary().to_dict()


@router.get("/trades/history")
async def get_trade_history_endpoint(
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 100
):
    """
    STEP 5.9: Get trade history.
    
    Query parameters:
    - strategy_id: Filter by strategy
    - symbol: Filter by symbol
    - limit: Maximum number of trades (default 100)
    """
    manager = get_portfolio_manager()
    trades = manager.trade_history.get_trade_history(
        strategy_id=strategy_id,
        symbol=symbol,
        limit=limit
    )
    return {
        "trades": trades,
        "count": len(trades),
        "filters": {"strategy_id": strategy_id, "symbol": symbol}
    }


@router.get("/trades/daily")
async def get_daily_trades(date: Optional[str] = None):
    """
    STEP 5.9: Get daily trading summary.
    
    Query parameters:
    - date: Date in YYYY-MM-DD format (default today)
    """
    manager = get_portfolio_manager()
    return manager.trade_history.get_daily_summary(date)


@router.get("/trades/performance/{strategy_id}")
async def get_strategy_performance_endpoint(strategy_id: str):
    """
    STEP 5.9: Get performance metrics for a strategy.
    """
    manager = get_portfolio_manager()
    return manager.trade_history.get_strategy_performance(strategy_id)


@router.get("/positions")
async def list_positions(
    strategy_id: Optional[str] = None,
    symbol: Optional[str] = None,
    open_only: bool = True
):
    """List positions with optional filtering."""
    manager = get_portfolio_manager()
    
    positions = []
    for pos in manager.positions.values():
        if strategy_id and pos.strategy_id != strategy_id:
            continue
        if symbol and pos.symbol != symbol:
            continue
        if open_only and not pos.is_open:
            continue
        positions.append(pos.to_dict())
    
    return {
        "count": len(positions),
        "positions": positions,
    }


@router.get("/allocations")
async def get_strategy_allocations():
    """Get all strategy allocations."""
    manager = get_portfolio_manager()
    
    return {
        "allocations": [
            alloc.to_dict() for alloc in manager.allocations.values()
        ]
    }
