"""
Production-grade Portfolio Engine for Trading System
Allocates capital across multiple symbols while enforcing risk limits

FINANCIAL PRECISION: Uses Decimal for all financial calculations to prevent
precision loss and rounding errors in critical financial operations.
"""

from typing import Dict, Optional, Tuple
from decimal import Decimal, getcontext

# Set high precision for financial calculations
getcontext().prec = 28  # 28 decimal places for financial precision


class PortfolioEngine:
    """
    Portfolio allocation engine for multi-asset trading.
    
    Enforces:
    - Maximum number of positions
    - Maximum allocation per asset
    - Signal-strength based weighting
    """
    
    def __init__(
        self,
        total_capital: float,
        max_positions: int = 5,
        max_allocation_per_asset: float = 0.3  # 30%
    ):
        """
        Initialize PortfolioEngine with capital and allocation constraints.
        
        Args:
            total_capital: Total capital available for allocation
            max_positions: Maximum number of positions to hold (default: 5)
            max_allocation_per_asset: Maximum fraction of capital per asset (default: 0.3 = 30%)
        """
        # Convert to Decimal for financial precision
        self.total_capital = Decimal(str(total_capital))
        self.max_positions = max_positions
        self.max_allocation_per_asset = Decimal(str(max_allocation_per_asset))
        
        self.current_allocations: Dict[str, Decimal] = {}
        self.active_positions: Dict[str, dict] = {}
    
    # ----------------------------------
    # ALLOCATE CAPITAL
    # ----------------------------------
    def allocate(self, signals: Dict[str, float]) -> Dict[str, float]:
        """
        Allocate capital across assets based on signal strength.
        
        Args:
            signals: Dictionary mapping symbol to signal strength (0.0 to 1.0+)
                     Example: {"BTCUSDT": 1.0, "ETHUSDT": 0.8, "SOLUSDT": 0.5}
        
        Returns:
            Dictionary mapping symbol to allocated capital amount (as float for API compatibility)
        """
        if not signals:
            return {}
        
        # Convert signals to Decimal for precision
        decimal_signals = {k: Decimal(str(v)) for k, v in signals.items()}
        
        # Sort by signal strength (descending)
        sorted_assets = sorted(
            decimal_signals.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Select top N positions
        selected = sorted_assets[:self.max_positions]
        
        # Calculate total signal strength
        total_signal = sum([s for _, s in selected])
        
        allocation = {}
        
        for symbol, strength in selected:
            # Weight proportional to signal strength
            weight = strength / total_signal if total_signal > 0 else Decimal('0')
            
            # Calculate raw capital allocation
            capital = weight * self.total_capital
            
            # Enforce max allocation per asset
            max_cap = self.total_capital * self.max_allocation_per_asset
            capital = min(capital, max_cap)
            
            allocation[symbol] = capital
        
        # Update current allocations
        self.current_allocations = allocation.copy()
        
        # Convert back to float for API compatibility
        return {k: float(v) for k, v in allocation.items()}
    
    # ----------------------------------
    # REBALANCE
    # ----------------------------------
    def rebalance(self, new_signals: Dict[str, float]) -> Dict[str, Tuple[float, float]]:
        """
        Rebalance portfolio based on new signals.
        
        Args:
            new_signals: Updated signal dictionary
        
        Returns:
            Dictionary mapping symbol to (target_allocation, current_allocation)
        """
        new_allocations = self.allocate(new_signals)
        
        rebalance_plan = {}
        
        # All symbols from new allocation
        for symbol, target in new_allocations.items():
            current = self.current_allocations.get(symbol, 0)
            rebalance_plan[symbol] = (target, current)
        
        # Symbols being removed
        for symbol, current in self.current_allocations.items():
            if symbol not in new_allocations:
                rebalance_plan[symbol] = (0, current)
        
        return rebalance_plan
    
    # ----------------------------------
    # UPDATE POSITION
    # ----------------------------------
    def update_position(self, symbol: str, size: float, entry_price: float) -> None:
        """
        Update tracked position for a symbol.
        
        Args:
            symbol: Trading pair symbol
            size: Position size (quantity)
            entry_price: Average entry price
        """
        self.active_positions[symbol] = {
            "size": size,
            "entry_price": entry_price,
            "value": size * entry_price
        }
    
    # ----------------------------------
    # CLOSE POSITION
    # ----------------------------------
    def close_position(self, symbol: str) -> Optional[dict]:
        """
        Close and remove a position.
        
        Args:
            symbol: Trading pair symbol to close
        
        Returns:
            Closed position data or None if not found
        """
        if symbol in self.active_positions:
            position = self.active_positions.pop(symbol)
            self.current_allocations.pop(symbol, None)
            return position
        return None
    
    # ----------------------------------
    # GET POSITION VALUE
    # ----------------------------------
    def get_position_value(self, symbol: str, current_price: float) -> float:
        """
        Calculate current value of a position.
        
        Args:
            symbol: Trading pair symbol
            current_price: Current market price
        
        Returns:
            Position value in base currency
        """
        position = self.active_positions.get(symbol)
        if position:
            return position["size"] * current_price
        return 0.0
    
    # ----------------------------------
    # GET TOTAL EXPOSED
    # ----------------------------------
    def get_total_exposed(self) -> Decimal:
        """
        Get total capital currently allocated to positions.
        
        Returns:
            Sum of all position values as Decimal
        """
        return sum(
            Decimal(str(pos.get("value", 0))) 
            for pos in self.active_positions.values()
        )
    
    # ----------------------------------
    # GET AVAILABLE CAPITAL
    # ----------------------------------
    def get_available_capital(self) -> Decimal:
        """
        Get remaining unallocated capital.
        
        Returns:
            Available capital amount as Decimal
        """
        return self.total_capital - self.get_total_exposed()
    
    # ----------------------------------
    # GET STATUS
    # ----------------------------------
    def get_status(self) -> dict:
        """
        Get current portfolio status.
        
        Returns:
            Dictionary with portfolio metrics
        """
        exposed = self.get_total_exposed()
        available = self.get_available_capital()
        
        return {
            "total_capital": self.total_capital,
            "exposed_capital": exposed,
            "available_capital": available,
            "exposure_pct": float(exposed / self.total_capital) if self.total_capital > 0 else 0.0,
            "position_count": len(self.active_positions),
            "max_positions": self.max_positions,
            "max_allocation_per_asset": self.max_allocation_per_asset,
            "positions": self.active_positions.copy(),
            "allocations": self.current_allocations.copy()
        }
    
    # ----------------------------------
    # CHECK ALLOCATION LIMIT
    # ----------------------------------
    def check_allocation_limit(self, symbol: str, proposed_capital: float) -> Tuple[bool, str]:
        """
        Check if proposed allocation exceeds limits.
        
        Args:
            symbol: Trading pair symbol
            proposed_capital: Proposed allocation amount
        
        Returns:
            Tuple of (allowed, reason)
        """
        # Check max positions
        if symbol not in self.active_positions and len(self.active_positions) >= self.max_positions:
            return False, f"Max positions reached: {self.max_positions}"
        
        # Check max allocation per asset
        max_allowed = self.total_capital * self.max_allocation_per_asset
        current = self.current_allocations.get(symbol, 0)
        new_total = current + proposed_capital
        
        if new_total > max_allowed:
            return False, f"Max allocation exceeded: {new_total:.2f} > {max_allowed:.2f}"
        
        return True, "Allocation OK"
    
    # ----------------------------------
    # RESET
    # ----------------------------------
    def reset(self) -> None:
        """Reset all allocations and positions"""
        self.current_allocations = {}
        self.active_positions = {}

    # ----------------------------------
    # METRICS & AGGREGATION HELPERS
    # ----------------------------------
    def get_portfolio_metrics(self, current_prices: Optional[Dict[str, float]] = None) -> dict:
        """
        Calculates aggregated portfolio metrics including valuation, exposure, and unrealized PnL.
        """
        prices = current_prices or {}
        total_positions_val = Decimal("0.0")
        total_unrealized_pnl = Decimal("0.0")

        for symbol, pos in self.active_positions.items():
            price = Decimal(str(prices.get(symbol, pos.get("entry_price", 0.0))))
            size = Decimal(str(pos.get("size", 0.0)))
            entry_price = Decimal(str(pos.get("entry_price", 0.0)))
            val = size * price
            pnl = (price - entry_price) * size
            total_positions_val += val
            total_unrealized_pnl += pnl

        available_cash = self.total_capital - self.get_total_exposed()
        total_equity = available_cash + total_positions_val

        return {
            "total_capital": self.total_capital,
            "total_equity": total_equity,
            "cash_balance": available_cash,
            "positions_value": total_positions_val,
            "unrealized_pnl": total_unrealized_pnl,
            "exposure_pct": float(total_positions_val / self.total_capital * 100) if self.total_capital > 0 else 0.0,
            "open_positions_count": len(self.active_positions),
        }

    def get_exposure_by_symbol(self, current_prices: Optional[Dict[str, float]] = None) -> Dict[str, dict]:
        """
        Calculates symbol-level exposure breakdown.
        """
        prices = current_prices or {}
        exposures = {}
        for symbol, pos in self.active_positions.items():
            price = Decimal(str(prices.get(symbol, pos.get("entry_price", 0.0))))
            size = Decimal(str(pos.get("size", 0.0)))
            entry_price = Decimal(str(pos.get("entry_price", 0.0)))
            notional = size * price
            exposures[symbol] = {
                "symbol": symbol,
                "size": float(size),
                "entry_price": float(entry_price),
                "current_price": float(price),
                "notional_value": float(notional),
                "unrealized_pnl": float((price - entry_price) * size),
            }
        return exposures

    def get_health_status(self) -> dict:
        """
        Evaluates portfolio health and constraint compliance.
        """
        exposed_pct = float(self.get_total_exposed() / self.total_capital) if self.total_capital > 0 else 0.0
        status = "healthy"
        issues = []
        if exposed_pct > 0.90:
            status = "warning"
            issues.append("Portfolio leverage/exposure > 90%")
        if len(self.active_positions) >= self.max_positions:
            issues.append(f"Max positions limit reached ({self.max_positions})")

        return {
            "status": status,
            "issues": issues,
            "position_count": len(self.active_positions),
            "max_positions": self.max_positions,
        }
