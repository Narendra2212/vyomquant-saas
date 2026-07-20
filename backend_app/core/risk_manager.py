"""
Risk Manager
Manages trading risk limits and position sizing with global guardrails.
"""

from datetime import datetime, date
from typing import Tuple, Optional


class RiskManager:
    """
    Global risk guardrails for trading system.
    
    Enforces BEFORE execution:
    - max_position_size = 10% equity per trade
    - max_daily_loss = 5% equity per day
    - max_open_trades = 5 concurrent positions
    """

    def __init__(self, initial_equity: float = 10000.0):
        # Per-trade risk
        self.max_risk_per_trade = 0.02
        self.max_drawdown = 0.1
        
        # Equity tracking
        self.initial_equity = initial_equity
        self.current_equity = initial_equity
        self.peak_equity = initial_equity
        
        # 🛡️ GLOBAL RISK GUARDRAILS
        self.max_position_size_pct = 0.10      # 10% of equity max per position
        self.max_daily_loss_pct = 0.05          # 5% max loss per day
        self.max_open_trades = 5                # Max 5 concurrent positions
        
        # Daily tracking
        self._current_date = date.today()
        self._daily_pnl = 0.0                   # Today's realized PnL
        self._daily_starting_equity = initial_equity
        
        # Open trade tracking
        self._open_trades_count = 0
        
    def position_size(self, price: float) -> float:
        """Calculate position size based on risk per trade."""
        return (self.current_equity * self.max_risk_per_trade) / price

    def update_equity(self, pnl: float):
        """Update equity with PnL and track daily performance."""
        self.current_equity += pnl
        self._daily_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.current_equity)

    def drawdown(self) -> float:
        """Calculate current drawdown from peak."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _reset_daily_if_needed(self):
        """Reset daily counters if date changed."""
        today = date.today()
        if today != self._current_date:
            self._current_date = today
            self._daily_pnl = 0.0
            self._daily_starting_equity = self.current_equity

    def daily_loss_pct(self) -> float:
        """Calculate today's loss as percentage of starting equity."""
        self._reset_daily_if_needed()
        if self._daily_starting_equity <= 0:
            return 0.0
        daily_loss = -min(0, self._daily_pnl)  # Only count losses
        return daily_loss / self._daily_starting_equity

    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed based on all risk limits.
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        self._reset_daily_if_needed()
        
        # Check 1: Max drawdown
        if self.drawdown() > self.max_drawdown:
            return False, "🛑 MAX DRAWDOWN HIT - Trading blocked"
        
        # Check 2: Max daily loss
        if self.daily_loss_pct() > self.max_daily_loss_pct:
            return False, f"🛑 MAX DAILY LOSS HIT ({self.daily_loss_pct()*100:.2f}%) - Trading blocked"
        
        return True, "✅ Risk checks passed"

    def can_open_position(
        self, 
        position_value: float, 
        open_trades_count: int = 0
    ) -> Tuple[bool, str]:
        """
        🛡️ GLOBAL GUARDRAIL: Check if position can be opened.
        
        Args:
            position_value: Dollar value of proposed position
            open_trades_count: Current number of open trades
            
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        self._reset_daily_if_needed()
        
        # Guardrail 1: Max position size (10% equity)
        max_position_value = self.current_equity * self.max_position_size_pct
        if position_value > max_position_value:
            return False, (
                f"🛡️ MAX POSITION SIZE EXCEEDED | "
                f"Position: ${position_value:.2f} | "
                f"Max allowed: ${max_position_value:.2f} (10% of ${self.current_equity:.2f})"
            )
        
        # Guardrail 2: Max daily loss (5%)
        allowed, msg = self.can_trade()
        if not allowed:
            return False, msg
        
        # Guardrail 3: Max open trades (5)
        if open_trades_count >= self.max_open_trades:
            return False, (
                f"🛡️ MAX OPEN TRADES EXCEEDED | "
                f"Current: {open_trades_count} | "
                f"Max allowed: {self.max_open_trades}"
            )
        
        return True, "✅ All guardrails passed - Trade allowed"

    def record_trade_open(self):
        """Record that a trade was opened."""
        self._open_trades_count += 1

    def record_trade_close(self):
        """Record that a trade was closed."""
        self._open_trades_count = max(0, self._open_trades_count - 1)

    def update_open_trades_count(self, count: int):
        """Update the count of open trades (sync with execution engine)."""
        self._open_trades_count = count

    def get_risk_status(self) -> dict:
        """Get current risk status for monitoring."""
        self._reset_daily_if_needed()
        return {
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown() * 100,
            "daily_pnl": self._daily_pnl,
            "daily_loss_pct": self.daily_loss_pct() * 100,
            "max_daily_loss_pct": self.max_daily_loss_pct * 100,
            "open_trades": self._open_trades_count,
            "max_open_trades": self.max_open_trades,
            "position_limit_pct": self.max_position_size_pct * 100,
        }
