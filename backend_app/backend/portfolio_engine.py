"""
Portfolio Engine

STEP 4.5 — PORTFOLIO + PnL CONSISTENCY

Aggregates positions into portfolio-level metrics.

Portfolio Aggregation:
┌─────────────────────────────────────────────────────────────────┐
│  Portfolio Metrics                                               │
│                                                                  │
│  total_equity                                                    │
│  ├─ cash_balance                                                 │
│  └─ positions_value                                              │
│      ├─ sum(position.size × current_price) for all open         │
│                                                                  │
│  total_pnl                                                       │
│  ├─ realized_pnl (from closed positions)                         │
│  └─ unrealized_pnl (from open positions)                         │
│                                                                  │
│  exposure_per_symbol                                             │
│  ├─ symbol: BTC → value = 50000 × 1.0 = $50,000                │
│  ├─ symbol: ETH → value = 3000 × 10.0 = $30,000                 │
│  └─ total_exposure = $80,000                                    │
│                                                                  │
│  exposure_per_strategy                                           │
│  ├─ strategy_1: $60,000                                         │
│  ├─ strategy_2: $20,000                                         │
│  └─ total = $80,000                                             │
│                                                                  │
│  risk_metrics                                                    │
│  ├─ concentration (largest position %)                          │
│  ├─ leverage_ratio                                              │
│  └─ margin_utilization                                            │
└─────────────────────────────────────────────────────────────────┘
"""

from decimal import Decimal
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import logging

from sqlalchemy.orm import Session

from backend_app.core.position_model import (
    PositionModel, PositionRepository, get_position_repository
)
from backend_app.backend.pnl_engine import PnLEngine, get_pnl_engine

logger = logging.getLogger(__name__)


@dataclass
class SymbolExposure:
    """Exposure breakdown for a symbol."""
    symbol: str
    total_size: str
    avg_entry_price: str
    current_price: str
    notional_value: str
    unrealized_pnl: str
    position_count: int


@dataclass
class StrategyExposure:
    """Exposure breakdown for a strategy."""
    strategy_id: str
    total_positions: int
    total_value: str
    unrealized_pnl: str
    realized_pnl: str
    symbols: List[str]


@dataclass
class PortfolioMetrics:
    """Complete portfolio metrics."""
    # Time
    timestamp: datetime
    
    # Equity
    cash_balance: str
    positions_value: str
    total_equity: str
    
    # PnL
    total_realized_pnl: str
    total_unrealized_pnl: str
    total_pnl: str
    
    # Exposure
    total_exposure: str
    exposure_by_symbol: Dict[str, SymbolExposure]
    exposure_by_strategy: Dict[str, StrategyExposure]
    
    # Risk
    concentration_pct: str  # Largest position as % of total
    leverage_ratio: str
    margin_utilization_pct: str
    
    # Counts
    open_positions_count: int
    total_symbols_traded: int


@dataclass
class PortfolioHealth:
    """Portfolio health check results."""
    status: str  # 'healthy', 'warning', 'critical'
    issues: List[Dict[str, Any]]
    recommendations: List[str]


class PortfolioEngine:
    """
    Portfolio aggregation engine.
    
    STEP 4.5: Aggregates positions into portfolio-level metrics.
    
    Features:
    - Real-time portfolio valuation
    - Exposure tracking by symbol and strategy
    - Risk metrics calculation
    - Portfolio health monitoring
    
    Usage:
        engine = PortfolioEngine(db_session)
        
        # Get portfolio snapshot
        metrics = engine.get_portfolio_metrics(tenant_id, current_prices)
        
        # Get exposure breakdown
        exposure = engine.get_exposure_by_symbol(tenant_id, current_prices)
    """
    
    PRECISION = Decimal('0.00000001')
    
    def __init__(self, db_session: Session):
        """Initialize portfolio engine."""
        self.db = db_session
        self.position_repo = get_position_repository(db_session)
        self.pnl_engine = get_pnl_engine(db_session)
    
    def get_portfolio_metrics(
        self,
        tenant_id: str,
        current_prices: Dict[str, Decimal],
        cash_balance: Optional[Decimal] = None
    ) -> PortfolioMetrics:
        """
        STEP 4.5: Get complete portfolio metrics.
        
        Args:
            tenant_id: Tenant UUID
            current_prices: Dict of symbol -> current_price
            cash_balance: Optional cash balance (if None, calculated from positions)
            
        Returns:
            PortfolioMetrics with all aggregated data
        """
        # Get all open positions
        open_positions = self.position_repo.list_open_positions(tenant_id)
        
        # Calculate positions value and PnL
        positions_value = Decimal('0')
        total_realized = Decimal('0')
        total_unrealized = Decimal('0')
        
        # Exposure aggregation
        exposure_by_symbol: Dict[str, Dict[str, Any]] = {}
        exposure_by_strategy: Dict[str, Dict[str, Any]] = {}
        
        # Track largest position for concentration
        largest_position_value = Decimal('0')
        
        for position in open_positions:
            symbol = position.symbol
            current_price = current_prices.get(symbol)
            
            if current_price is None:
                logger.warning(f"No price for {symbol}, skipping in portfolio metrics")
                continue
            
            size = Decimal(position.size)
            position_value = size * current_price
            positions_value += position_value
            
            # Track largest position
            if position_value > largest_position_value:
                largest_position_value = position_value
            
            # PnL
            unrealized = Decimal(position.unrealized_pnl)
            realized = Decimal(position.realized_pnl)
            total_unrealized += unrealized
            total_realized += realized
            
            # Symbol exposure
            if symbol not in exposure_by_symbol:
                exposure_by_symbol[symbol] = {
                    'symbol': symbol,
                    'total_size': Decimal('0'),
                    'avg_entry_price': Decimal('0'),
                    'current_price': current_price,
                    'notional_value': Decimal('0'),
                    'unrealized_pnl': Decimal('0'),
                    'position_count': 0
                }
            
            symbol_exp = exposure_by_symbol[symbol]
            symbol_exp['total_size'] += size
            symbol_exp['notional_value'] += position_value
            symbol_exp['unrealized_pnl'] += unrealized
            symbol_exp['position_count'] += 1
            
            # Strategy exposure
            strategy_id = position.strategy_id
            if strategy_id not in exposure_by_strategy:
                exposure_by_strategy[strategy_id] = {
                    'strategy_id': strategy_id,
                    'total_positions': 0,
                    'total_value': Decimal('0'),
                    'unrealized_pnl': Decimal('0'),
                    'realized_pnl': Decimal('0'),
                    'symbols': set()
                }
            
            strategy_exp = exposure_by_strategy[strategy_id]
            strategy_exp['total_positions'] += 1
            strategy_exp['total_value'] += position_value
            strategy_exp['unrealized_pnl'] += unrealized
            strategy_exp['realized_pnl'] += realized
            strategy_exp['symbols'].add(symbol)
        
        # Cash balance (placeholder - would come from exchange/bank)
        if cash_balance is None:
            cash_balance = Decimal('0')  # TODO: Get from exchange
        
        # Total equity
        total_equity = cash_balance + positions_value
        
        # Total PnL
        total_pnl = total_realized + total_unrealized
        
        # Total exposure
        total_exposure = positions_value
        
        # Concentration
        concentration_pct = (
            (largest_position_value / total_exposure * 100)
            if total_exposure > 0 else Decimal('0')
        )
        
        # Leverage ratio (simplified - positions / equity)
        leverage_ratio = (
            (total_exposure / total_equity)
            if total_equity > 0 else Decimal('0')
        )
        
        # Margin utilization (simplified)
        margin_utilization_pct = (
            (total_exposure / total_equity * 100)
            if total_equity > 0 else Decimal('0')
        )
        
        # Convert symbol exposure to dataclass
        symbol_exposures = {}
        for symbol, data in exposure_by_symbol.items():
            symbol_exposures[symbol] = SymbolExposure(
                symbol=data['symbol'],
                total_size=str(data['total_size']),
                avg_entry_price=str(data['avg_entry_price']),
                current_price=str(data['current_price']),
                notional_value=str(data['notional_value']),
                unrealized_pnl=str(data['unrealized_pnl']),
                position_count=data['position_count']
            )
        
        # Convert strategy exposure to dataclass
        strategy_exposures = {}
        for strategy_id, data in exposure_by_strategy.items():
            strategy_exposures[strategy_id] = StrategyExposure(
                strategy_id=data['strategy_id'],
                total_positions=data['total_positions'],
                total_value=str(data['total_value']),
                unrealized_pnl=str(data['unrealized_pnl']),
                realized_pnl=str(data['realized_pnl']),
                symbols=list(data['symbols'])
            )
        
        metrics = PortfolioMetrics(
            timestamp=datetime.utcnow(),
            cash_balance=str(cash_balance),
            positions_value=str(positions_value),
            total_equity=str(total_equity),
            total_realized_pnl=str(total_realized),
            total_unrealized_pnl=str(total_unrealized),
            total_pnl=str(total_pnl),
            total_exposure=str(total_exposure),
            exposure_by_symbol=symbol_exposures,
            exposure_by_strategy=strategy_exposures,
            concentration_pct=str(concentration_pct.quantize(self.PRECISION)),
            leverage_ratio=str(leverage_ratio.quantize(self.PRECISION)),
            margin_utilization_pct=str(margin_utilization_pct.quantize(self.PRECISION)),
            open_positions_count=len(open_positions),
            total_symbols_traded=len(symbol_exposures)
        )
        
        logger.info(
            f"PORTFOLIO METRICS: tenant={tenant_id} | "
            f"equity={metrics.total_equity} | "
            f"pnl={metrics.total_pnl} | "
            f"positions={metrics.open_positions_count} | "
            f"exposure={metrics.total_exposure}"
        )
        
        return metrics
    
    def get_exposure_by_symbol(
        self,
        tenant_id: str,
        current_prices: Dict[str, Decimal]
    ) -> List[SymbolExposure]:
        """
        Get exposure breakdown by symbol.
        
        Args:
            tenant_id: Tenant UUID
            current_prices: Dict of symbol -> current_price
            
        Returns:
            List of SymbolExposure for each traded symbol
        """
        open_positions = self.position_repo.list_open_positions(tenant_id)
        
        # Aggregate by symbol
        symbol_data: Dict[str, Dict[str, Any]] = {}
        
        for position in open_positions:
            symbol = position.symbol
            current_price = current_prices.get(symbol)
            
            if current_price is None:
                continue
            
            size = Decimal(position.size)
            notional = size * current_price
            
            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    'symbol': symbol,
                    'total_size': Decimal('0'),
                    'avg_entry_price': Decimal('0'),
                    'current_price': current_price,
                    'notional_value': Decimal('0'),
                    'unrealized_pnl': Decimal('0'),
                    'position_count': 0
                }
            
            data = symbol_data[symbol]
            data['total_size'] += size
            data['notional_value'] += notional
            data['unrealized_pnl'] += Decimal(position.unrealized_pnl)
            data['position_count'] += 1
        
        # Convert to dataclass
        exposures = []
        for symbol, data in symbol_data.items():
            exposures.append(SymbolExposure(
                symbol=data['symbol'],
                total_size=str(data['total_size']),
                avg_entry_price=str(data['avg_entry_price']),
                current_price=str(data['current_price']),
                notional_value=str(data['notional_value']),
                unrealized_pnl=str(data['unrealized_pnl']),
                position_count=data['position_count']
            ))
        
        # Sort by notional value descending
        exposures.sort(
            key=lambda x: Decimal(x.notional_value),
            reverse=True
        )
        
        return exposures
    
    def get_exposure_by_strategy(
        self,
        tenant_id: str,
        current_prices: Dict[str, Decimal]
    ) -> List[StrategyExposure]:
        """
        Get exposure breakdown by strategy.
        
        Args:
            tenant_id: Tenant UUID
            current_prices: Dict of symbol -> current_price
            
        Returns:
            List of StrategyExposure for each strategy
        """
        open_positions = self.position_repo.list_open_positions(tenant_id)
        
        # Aggregate by strategy
        strategy_data: Dict[str, Dict[str, Any]] = {}
        
        for position in open_positions:
            strategy_id = position.strategy_id
            symbol = position.symbol
            current_price = current_prices.get(symbol)
            
            if current_price is None:
                continue
            
            size = Decimal(position.size)
            notional = size * current_price
            
            if strategy_id not in strategy_data:
                strategy_data[strategy_id] = {
                    'strategy_id': strategy_id,
                    'total_positions': 0,
                    'total_value': Decimal('0'),
                    'unrealized_pnl': Decimal('0'),
                    'realized_pnl': Decimal('0'),
                    'symbols': set()
                }
            
            data = strategy_data[strategy_id]
            data['total_positions'] += 1
            data['total_value'] += notional
            data['unrealized_pnl'] += Decimal(position.unrealized_pnl)
            data['realized_pnl'] += Decimal(position.realized_pnl)
            data['symbols'].add(symbol)
        
        # Convert to dataclass
        exposures = []
        for strategy_id, data in strategy_data.items():
            exposures.append(StrategyExposure(
                strategy_id=data['strategy_id'],
                total_positions=data['total_positions'],
                total_value=str(data['total_value']),
                unrealized_pnl=str(data['unrealized_pnl']),
                realized_pnl=str(data['realized_pnl']),
                symbols=list(data['symbols'])
            ))
        
        # Sort by total value descending
        exposures.sort(
            key=lambda x: Decimal(x.total_value),
            reverse=True
        )
        
        return exposures
    
    def check_portfolio_health(
        self,
        tenant_id: str,
        metrics: Optional[PortfolioMetrics] = None,
        current_prices: Optional[Dict[str, Decimal]] = None
    ) -> PortfolioHealth:
        """
        Check portfolio health and identify issues.
        
        Returns:
            PortfolioHealth with status, issues, and recommendations
        """
        if metrics is None:
            if current_prices is None:
                raise ValueError("Either metrics or current_prices must be provided")
            metrics = self.get_portfolio_metrics(tenant_id, current_prices)
        
        issues = []
        recommendations = []
        
        # Check concentration
        concentration = Decimal(metrics.concentration_pct)
        if concentration > 50:
            issues.append({
                'type': 'high_concentration',
                'severity': 'warning',
                'message': f'High concentration: {concentration}% in single position',
                'value': str(concentration)
            })
            recommendations.append('Consider diversifying positions')
        
        # Check leverage
        leverage = Decimal(metrics.leverage_ratio)
        if leverage > 3:
            issues.append({
                'type': 'high_leverage',
                'severity': 'critical',
                'message': f'High leverage: {leverage}x',
                'value': str(leverage)
            })
            recommendations.append('Reduce position sizes to lower leverage')
        elif leverage > 2:
            issues.append({
                'type': 'elevated_leverage',
                'severity': 'warning',
                'message': f'Elevated leverage: {leverage}x',
                'value': str(leverage)
            })
        
        # Check margin utilization
        margin_util = Decimal(metrics.margin_utilization_pct)
        if margin_util > 80:
            issues.append({
                'type': 'high_margin_utilization',
                'severity': 'critical',
                'message': f'High margin utilization: {margin_util}%',
                'value': str(margin_util)
            })
            recommendations.append('Add margin or reduce positions immediately')
        
        # Check total PnL
        total_pnl = Decimal(metrics.total_pnl)
        total_equity = Decimal(metrics.total_equity)
        if total_equity > 0:
            pnl_pct = (total_pnl / total_equity) * 100
            if pnl_pct < -10:
                issues.append({
                    'type': 'significant_loss',
                    'severity': 'warning',
                    'message': f'Significant portfolio loss: {pnl_pct}%',
                    'value': str(pnl_pct)
                })
        
        # Determine status
        critical_count = sum(1 for i in issues if i['severity'] == 'critical')
        warning_count = sum(1 for i in issues if i['severity'] == 'warning')
        
        if critical_count > 0:
            status = 'critical'
        elif warning_count > 0:
            status = 'warning'
        else:
            status = 'healthy'
        
        return PortfolioHealth(
            status=status,
            issues=issues,
            recommendations=recommendations
        )
    
    def get_portfolio_equity_history(
        self,
        tenant_id: str,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get historical portfolio equity.
        
        In production, this would query historical snapshots.
        For now, returns placeholder.
        """
        # This would query a portfolio_snapshots table
        # For now, return empty structure
        return []


# Global instance
def get_portfolio_engine(db_session: Session) -> PortfolioEngine:
    """Get portfolio engine instance."""
    return PortfolioEngine(db_session)
