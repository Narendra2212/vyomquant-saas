"""
PnL Calculation Engine

STEP 4.4 — PORTFOLIO + PnL CONSISTENCY

Calculates realized and unrealized PnL with financial precision.

PnL Formulas:
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  UNREALIZED PnL (open positions):                               │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Long:  (current_price - avg_entry_price) × size             ││
│  │ Short: (avg_entry_price - current_price) × size             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  REALIZED PnL (closed positions):                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Long:  (exit_price - avg_entry_price) × exit_size           ││
│  │ Short: (avg_entry_price - exit_price) × exit_size           ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  TOTAL PnL = unrealized_pnl + realized_pnl                       │
│                                                                  │
│  PnL PERCENTAGE:                                                │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ (pnl / (avg_entry_price × size)) × 100                      ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend_app.core.position_model import (PositionCalculator, PositionModel,
                                             PositionSide, PositionStatus,
                                             get_position_repository)

logger = logging.getLogger(__name__)


@dataclass
class PnLBreakdown:
    """Detailed PnL breakdown."""
    # Identifiers
    position_id: str
    symbol: str
    side: str
    
    # Position data
    size: str
    avg_entry_price: str
    current_price: str
    
    # PnL values
    unrealized_pnl: str
    unrealized_pnl_pct: str
    realized_pnl: str
    total_pnl: str
    total_pnl_pct: str
    
    # Market value
    position_value: str
    cost_basis: str


@dataclass
class PortfolioPnLSummary:
    """Portfolio-wide PnL summary."""
    # Time range
    start_time: datetime
    end_time: datetime
    
    # Overall PnL
    total_unrealized_pnl: str
    total_realized_pnl: str
    total_pnl: str
    
    # By symbol
    pnl_by_symbol: Dict[str, Dict[str, str]]
    
    # By strategy
    pnl_by_strategy: Dict[str, Dict[str, str]]
    
    # Position breakdown
    open_positions_count: int
    closed_positions_count: int
    
    # Best/worst performers
    best_performer: Optional[Dict[str, str]]
    worst_performer: Optional[Dict[str, str]]


class PnLEngine:
    """
    PnL Calculation Engine.
    
    STEP 4.4: Calculates realized and unrealized PnL.
    
    Features:
    - Decimal precision for all calculations
    - Real-time PnL updates
    - Historical PnL tracking
    - Performance analytics
    
    Usage:
        engine = PnLEngine(db_session)
        
        # Calculate PnL for position
        pnl = engine.calculate_position_pnl(position, current_price)
        
        # Get portfolio summary
        summary = engine.get_portfolio_pnl_summary(tenant_id)
    """
    
    PRECISION = Decimal('0.00000001')
    
    def __init__(self, db_session: Session):
        """Initialize PnL engine."""
        self.db = db_session
        self.repo = get_position_repository(db_session)
        self.calculator = PositionCalculator()
    
    def calculate_position_pnl(
        self,
        position: PositionModel,
        current_market_price: Decimal
    ) -> PnLBreakdown:
        """
        Calculate complete PnL breakdown for a position.
        
        Args:
            position: Position model
            current_market_price: Current market price
            
        Returns:
            PnLBreakdown with all calculated values
        """
        size = Decimal(position.size)
        avg_entry = Decimal(position.avg_entry_price)
        realized = Decimal(position.realized_pnl)
        
        # Calculate unrealized PnL
        unrealized = self.calculator.calculate_unrealized_pnl(
            size=size,
            avg_entry_price=avg_entry,
            current_price=current_market_price,
            side=PositionSide(position.side)
        )
        
        # Calculate total PnL
        total = self.calculator.calculate_total_pnl(unrealized, realized)
        
        # Calculate cost basis and position value
        cost_basis = avg_entry * size
        position_value = current_market_price * size
        
        # Calculate percentages
        if cost_basis > 0:
            unrealized_pct = (unrealized / cost_basis) * 100
            total_pct = (total / cost_basis) * 100
        else:
            unrealized_pct = Decimal('0')
            total_pct = Decimal('0')
        
        return PnLBreakdown(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side.value,
            size=str(size),
            avg_entry_price=str(avg_entry),
            current_price=str(current_market_price),
            unrealized_pnl=str(unrealized),
            unrealized_pnl_pct=str(unrealized_pct.quantize(self.PRECISION)),
            realized_pnl=str(realized),
            total_pnl=str(total),
            total_pnl_pct=str(total_pct.quantize(self.PRECISION)),
            position_value=str(position_value),
            cost_basis=str(cost_basis)
        )
    
    def calculate_realized_pnl_from_fill(
        self,
        avg_entry_price: Decimal,
        exit_price: Decimal,
        exit_size: Decimal,
        side: PositionSide
    ) -> Decimal:
        """
        Calculate realized PnL from a closing fill.
        
        Args:
            avg_entry_price: Average entry price of position
            exit_price: Price at which position was closed
            exit_size: Size of the closing fill
            side: Position side (LONG or SHORT)
            
        Returns:
            Realized PnL value
        """
        return self.calculator.calculate_realized_pnl(
            exit_size=exit_size,
            exit_price=exit_price,
            avg_entry_price=avg_entry_price,
            side=side
        )
    
    def get_portfolio_pnl_summary(
        self,
        tenant_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> PortfolioPnLSummary:
        """
        Get complete portfolio PnL summary.
        
        Args:
            tenant_id: Tenant UUID
            start_time: Optional start time for range
            end_time: Optional end time for range
            
        Returns:
            PortfolioPnLSummary with all PnL data
        """
        if end_time is None:
            end_time = datetime.utcnow()
        if start_time is None:
            start_time = end_time - timedelta(days=30)  # Default 30 days
        
        # Get all positions
        open_positions = self.repo.list_open_positions(tenant_id)
        all_positions, _ = self.repo.list_all_positions(tenant_id, limit=10000)
        
        # Calculate totals
        total_unrealized = Decimal('0')
        total_realized = Decimal('0')
        
        # By symbol
        pnl_by_symbol: Dict[str, Dict[str, Decimal]] = {}
        
        # By strategy
        pnl_by_strategy: Dict[str, Dict[str, Decimal]] = {}
        
        # Track best/worst performers
        best_pnl = Decimal('-999999999')
        worst_pnl = Decimal('999999999')
        best_performer = None
        worst_performer = None
        
        for position in all_positions:
            symbol = position.symbol
            strategy = position.strategy_id
            
            unrealized = Decimal(position.unrealized_pnl)
            realized = Decimal(position.realized_pnl)
            total = unrealized + realized
            
            # Update totals
            total_unrealized += unrealized
            total_realized += realized
            
            # Aggregate by symbol
            if symbol not in pnl_by_symbol:
                pnl_by_symbol[symbol] = {
                    'unrealized': Decimal('0'),
                    'realized': Decimal('0'),
                    'total': Decimal('0')
                }
            pnl_by_symbol[symbol]['unrealized'] += unrealized
            pnl_by_symbol[symbol]['realized'] += realized
            pnl_by_symbol[symbol]['total'] += total
            
            # Aggregate by strategy
            if strategy not in pnl_by_strategy:
                pnl_by_strategy[strategy] = {
                    'unrealized': Decimal('0'),
                    'realized': Decimal('0'),
                    'total': Decimal('0')
                }
            pnl_by_strategy[strategy]['unrealized'] += unrealized
            pnl_by_strategy[strategy]['realized'] += realized
            pnl_by_strategy[strategy]['total'] += total
            
            # Track best/worst
            if total > best_pnl:
                best_pnl = total
                best_performer = {
                    'position_id': position.position_id,
                    'symbol': symbol,
                    'pnl': str(total)
                }
            
            if total < worst_pnl:
                worst_pnl = total
                worst_performer = {
                    'position_id': position.position_id,
                    'symbol': symbol,
                    'pnl': str(total)
                }
        
        # Convert Decimal to string for output
        pnl_by_symbol_str = {
            k: {kk: str(vv) for kk, vv in v.items()}
            for k, v in pnl_by_symbol.items()
        }
        
        pnl_by_strategy_str = {
            k: {kk: str(vv) for kk, vv in v.items()}
            for k, v in pnl_by_strategy.items()
        }
        
        closed_count = len([p for p in all_positions if p.status == PositionStatus.CLOSED])
        
        return PortfolioPnLSummary(
            start_time=start_time,
            end_time=end_time,
            total_unrealized_pnl=str(total_unrealized),
            total_realized_pnl=str(total_realized),
            total_pnl=str(total_unrealized + total_realized),
            pnl_by_symbol=pnl_by_symbol_str,
            pnl_by_strategy=pnl_by_strategy_str,
            open_positions_count=len(open_positions),
            closed_positions_count=closed_count,
            best_performer=best_performer,
            worst_performer=worst_performer
        )
    
    def get_daily_pnl_report(
        self,
        tenant_id: str,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get daily PnL report for the last N days.
        
        Args:
            tenant_id: Tenant UUID
            days: Number of days to report
            
        Returns:
            List of daily PnL records
        """
        # Query closed positions in date range
        end_date = datetime.utcnow()
        end_date - timedelta(days=days)
        
        # This would typically query a trades/closed_positions table
        # For now, return placeholder structure
        daily_records = []
        
        for i in range(days):
            date = end_date - timedelta(days=i)
            
            # In production, this would aggregate actual trades
            daily_records.append({
                'date': date.strftime('%Y-%m-%d'),
                'realized_pnl': '0.0',  # Placeholder
                'unrealized_pnl': '0.0',  # Placeholder
                'total_pnl': '0.0',  # Placeholder
                'trade_count': 0  # Placeholder
            })
        
        return daily_records
    
    def calculate_sharpe_ratio(
        self,
        daily_returns: List[Decimal],
        risk_free_rate: Decimal = Decimal('0.0')
    ) -> Decimal:
        """
        Calculate Sharpe ratio from daily returns.
        
        Formula: (mean(return) - risk_free_rate) / std_dev(return)
        """
        if len(daily_returns) < 2:
            return Decimal('0')
        
        # Calculate mean
        mean_return = sum(daily_returns) / len(daily_returns)
        
        # Calculate standard deviation
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
        std_dev = variance.sqrt()
        
        if std_dev == 0:
            return Decimal('0')
        
        sharpe = (mean_return - risk_free_rate) / std_dev
        
        return sharpe.quantize(self.PRECISION, rounding=ROUND_HALF_UP)
    
    def calculate_max_drawdown(
        self,
        equity_curve: List[Decimal]
    ) -> Dict[str, Decimal]:
        """
        Calculate maximum drawdown from equity curve.
        
        Returns:
            Dict with max_drawdown_pct, peak, trough
        """
        if not equity_curve:
            return {
                'max_drawdown_pct': Decimal('0'),
                'peak': Decimal('0'),
                'trough': Decimal('0')
            }
        
        peak = equity_curve[0]
        max_drawdown = Decimal('0')
        max_peak = peak
        max_trough = peak
        
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            
            drawdown = (peak - equity) / peak if peak > 0 else Decimal('0')
            
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_peak = peak
                max_trough = equity
        
        return {
            'max_drawdown_pct': str((max_drawdown * 100).quantize(self.PRECISION)),
            'peak': str(max_peak),
            'trough': str(max_trough)
        }


# Global instance
def get_pnl_engine(db_session: Session) -> PnLEngine:
    """Get PnL engine instance."""
    return PnLEngine(db_session)
