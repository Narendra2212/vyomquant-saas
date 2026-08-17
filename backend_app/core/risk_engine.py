"""
Production-grade Risk Engine for Trading System
Controls capital risk, prevents overexposure, and enforces safety rules

FINANCIAL PRECISION: Uses Decimal for all financial calculations to prevent
precision loss and rounding errors in critical financial operations.
"""

from typing import Tuple
from decimal import Decimal, getcontext

import pandas as pd

# Set high precision for financial calculations
getcontext().prec = 28  # 28 decimal places for financial precision


class RiskEngine:
    """
    Risk management engine for trading operations.
    
    Enforces:
    - Position sizing based on risk per trade
    - Maximum drawdown limits
    - Daily loss limits
    - Peak equity tracking
    """
    
    def __init__(
        self,
        initial_capital: float,
        risk_per_trade: float = 0.01,   # 1% of equity per trade
        max_drawdown: float = 0.2,      # 20% max drawdown
        daily_loss_limit: float = 0.05, # 5% daily loss limit
        atr_multiplier: float = 2.0     # ATR stop distance multiplier
    ):
        """
        Initialize RiskEngine with capital and risk parameters.
        
        Args:
            initial_capital: Starting capital amount
            risk_per_trade: Fraction of equity to risk per trade (default: 0.01 = 1%)
            max_drawdown: Maximum allowed drawdown from peak (default: 0.2 = 20%)
            daily_loss_limit: Maximum daily loss as fraction of initial capital (default: 0.05 = 5%)
            atr_multiplier: Multiplier for ATR-based stop distance (default: 2.0)
        """
        # Convert to Decimal for financial precision
        self.initial_capital = Decimal(str(initial_capital))
        self.current_equity = Decimal(str(initial_capital))
        
        self.risk_per_trade = Decimal(str(risk_per_trade))
        self.max_drawdown = Decimal(str(max_drawdown))
        self.daily_loss_limit = Decimal(str(daily_loss_limit))
        self.atr_multiplier = Decimal(str(atr_multiplier))
        
        self.peak_equity = Decimal(str(initial_capital))
        self.daily_loss = Decimal('0.0')
    
    # ----------------------------------
    # CALCULATE ATR
    # ----------------------------------
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate Average True Range (ATR).
        
        Args:
            df: DataFrame with 'high', 'low', 'close' columns
            period: ATR calculation period (default: 14)
            
        Returns:
            Series of ATR values
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        
        return atr
    
    # ----------------------------------
    # POSITION SIZING (ATR BASED)
    # ----------------------------------
    def calculate_position_size(self, price: float, atr: float) -> float:
        """
        Calculate position size based on ATR (volatility-adjusted).
        
        Args:
            price: Current price of the asset
            atr: Current ATR value
            
        Returns:
            Position size (quantity) to trade (as float for API compatibility)
        """
        # Convert to Decimal for precision
        price_decimal = Decimal(str(price))
        atr_decimal = Decimal(str(atr))
        
        risk_amount = self.current_equity * self.risk_per_trade
        stop_distance = atr_decimal * self.atr_multiplier
        
        if stop_distance == 0:
            return 0.0
        
        position_size = risk_amount / stop_distance
        
        # Convert back to float for API compatibility
        return float(position_size)
    
    # ----------------------------------
    # UPDATE EQUITY AFTER TRADE
    # ----------------------------------
    def update_equity(self, pnl: float) -> None:
        """
        Update equity after a trade's PnL is realized.
        
        Args:
            pnl: Profit or loss from the trade (positive for profit, negative for loss)
        """
        self.current_equity += pnl
        
        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        
        # Update daily loss tracking
        if pnl < 0:
            self.daily_loss += abs(pnl)
    
    # ----------------------------------
    # CHECK DRAWDOWN
    # ----------------------------------
    def check_drawdown(self) -> Tuple[bool, str]:
        """
        Check if current drawdown exceeds maximum allowed.
        
        Returns:
            Tuple of (allowed, reason)
                - allowed: True if drawdown is within limits
                - reason: String explaining the check result
        """
        if self.peak_equity == 0:
            return True, "OK"
        
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        
        if drawdown >= self.max_drawdown:
            return False, f"Max drawdown exceeded: {drawdown:.2%} >= {self.max_drawdown:.2%}"
        
        return True, f"Drawdown OK: {drawdown:.2%}"
    
    # ----------------------------------
    # CHECK DAILY LOSS
    # ----------------------------------
    def check_daily_loss(self) -> Tuple[bool, str]:
        """
        Check if daily loss exceeds limit.
        
        Returns:
            Tuple of (allowed, reason)
                - allowed: True if daily loss is within limits
                - reason: String explaining the check result
        """
        if self.daily_loss >= self.initial_capital * self.daily_loss_limit:
            return False, f"Daily loss limit exceeded: {self.daily_loss:.2f} >= {self.initial_capital * self.daily_loss_limit:.2f}"
        
        return True, f"Daily loss OK: {self.daily_loss:.2f}"
    
    # ----------------------------------
    # GLOBAL CHECK
    # ----------------------------------
    def can_trade(self) -> bool:
        """
        Check if trading is allowed based on all risk parameters.
        
        Returns:
            True if trading is allowed, False otherwise
        """
        dd_ok, _ = self.check_drawdown()
        dl_ok, _ = self.check_daily_loss()
        
        return dd_ok and dl_ok
    
    # ----------------------------------
    # GET STATUS
    # ----------------------------------
    def get_status(self) -> dict:
        """
        Get current risk engine status.
        
        Returns:
            Dictionary with current risk metrics
        """
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity if self.peak_equity > 0 else 0
        
        return {
            "initial_capital": self.initial_capital,
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "daily_loss": self.daily_loss,
            "drawdown_pct": drawdown,
            "risk_per_trade": self.risk_per_trade,
            "max_drawdown": self.max_drawdown,
            "daily_loss_limit": self.daily_loss_limit,
            "atr_multiplier": self.atr_multiplier,
            "can_trade": self.can_trade()
        }
    
    # ----------------------------------
    # RESET DAILY LOSS
    # ----------------------------------
    def reset_daily_loss(self) -> None:
        """Reset daily loss counter (call at start of new trading day)"""
        self.daily_loss = 0.0
