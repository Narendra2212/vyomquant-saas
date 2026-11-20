"""
Production-grade Risk Engine for Trading System
Controls capital risk, prevents overexposure, and enforces safety rules

FINANCIAL PRECISION: Uses Decimal for all financial calculations to prevent
precision loss and rounding errors in critical financial operations.
"""

import logging
from typing import Optional, Tuple
from decimal import Decimal, InvalidOperation, getcontext

import pandas as pd

logger = logging.getLogger(__name__)

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
        atr_multiplier: float = 2.0,    # ATR stop distance multiplier
        max_position_notional_pct: Optional[float] = 1.0,
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

        #: Hard ceiling on one position's notional, as a fraction of current equity.
        #: Bounds `calculate_position_size`, whose risk-based formula is otherwise
        #: unbounded as volatility falls. Defaults to the whole account, so it removes
        #: only the impossible tail rather than tightening anyone's configured risk.
        self.max_position_notional_pct = (
            None if max_position_notional_pct is None
            else Decimal(str(max_position_notional_pct))
        )
    
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
        #
        # TRADE-11. A PRICE THAT CANNOT BOUND THE NOTIONAL SIZES NOTHING.
        #
        # `Decimal(str(price))` has no guard, so `None` arrives here as
        # `Decimal("None")` -> InvalidOperation, and the caller sees a crash rather than a
        # refusal. Worse, a price of 0 parsed fine and then SKIPPED the notional cap
        # below, because that cap is gated on `price_decimal > 0` -- so the one input that
        # makes the notional unknowable was also the one that removed the only bound on
        # ATR sizing, leaving `risk_amount / stop_distance` unbounded. Returning 0.0 means
        # "no position", which is the fail-closed answer: a size nobody can value is not
        # a size to take.
        #
        # `is_finite()` is checked too. `Decimal("NaN")` and `Decimal("Infinity")` both
        # parse, and every comparison against NaN is False -- so a NaN price would pass
        # `price_decimal > 0` as False (skipping the cap) while a NaN *size* would pass
        # `position_size > max_size` as False (also skipping the cap). Both directions
        # silently disable the bound.
        try:
            price_decimal = Decimal(str(price))
            atr_decimal = Decimal(str(atr))
        except (InvalidOperation, TypeError, ValueError):
            logger.error(
                "ATR sizing refused: price %r or atr %r is not a usable number, so the "
                "position notional cannot be bounded. Returning 0.", price, atr,
            )
            return 0.0

        if not price_decimal.is_finite() or not atr_decimal.is_finite():
            logger.error(
                "ATR sizing refused: price %r or atr %r is not finite. A non-finite "
                "value disables every comparison below rather than failing one. "
                "Returning 0.", price, atr,
            )
            return 0.0

        if price_decimal <= 0:
            logger.error(
                "ATR sizing refused: price is %r, so the position's notional cannot be "
                "computed and max_position_notional_pct cannot bound it. This used to "
                "fall through with the cap skipped, leaving risk-based sizing unbounded. "
                "Returning 0.", price,
            )
            return 0.0

        risk_amount = self.current_equity * self.risk_per_trade
        stop_distance = atr_decimal * self.atr_multiplier
        
        if stop_distance == 0:
            return 0.0
        
        position_size = risk_amount / stop_distance

        # ═══════════════════════════════════════════════════════════════════
        # NOTIONAL CAP — risk-based sizing is unbounded without one
        # ═══════════════════════════════════════════════════════════════════
        # `risk_amount / stop_distance` is dimensionally correct (risk dollars divided by
        # stop distance in dollars gives a quantity), but its magnitude is governed by the
        # asset's volatility, not by the account. As ATR falls the quantity rises without
        # limit, and the function shipped that number with no reference to the balance it
        # would be spent from.
        #
        # Worked example from the pre-launch audit: equity 10,000 with risk_per_trade 1%
        # gives a 100 risk budget; BTC at 50,000 with ATR 2 and multiplier 2 gives a stop
        # distance of 4, hence 25 BTC — 1.25M notional, 125x the account. The lower the
        # volatility, the larger the breach. A low-ATR regime is exactly when a strategy
        # is most likely to size up.
        #
        # The cap is expressed as a fraction of equity rather than an absolute figure so
        # it scales with the account, and it binds the NOTIONAL (quantity x price), which
        # is the quantity that has to be funded. `max_position_notional_pct` defaults to
        # 1.0 — a position may be at most the whole account — so this cannot make an
        # otherwise-valid size smaller than the balance could fund; it only removes the
        # unbounded tail. A caller wanting a tighter bound sets it on the constructor.
        # `price_decimal > 0` is no longer part of this condition: a non-positive or
        # non-finite price now returns 0.0 above rather than reaching here with the cap
        # disabled. That gate WAS the TRADE-11 defect in this file -- the one input which
        # made the notional unknowable was also the one that removed the bound.
        if self.max_position_notional_pct is not None:
            max_notional = self.current_equity * self.max_position_notional_pct
            max_size = max_notional / price_decimal
            if position_size > max_size:
                logger.warning(
                    "ATR sizing produced %s units (%s notional) against equity %s; "
                    "capped to %s units (%s notional) by max_position_notional_pct=%s.",
                    position_size,
                    position_size * price_decimal,
                    self.current_equity,
                    max_size,
                    max_notional,
                    self.max_position_notional_pct,
                )
                position_size = max_size

        # Convert back to float for API compatibility
        return float(position_size)
    
    # ----------------------------------
    # UPDATE EQUITY AFTER TRADE
    # ----------------------------------
    def update_equity(self, pnl) -> None:
        """
        Update equity after a trade's PnL is realized.

        Args:
            pnl: Profit or loss from the trade (positive for profit, negative for loss).
                Accepts ``Decimal``, ``float``, ``int`` or a numeric string; all are
                normalised to ``Decimal`` before touching an accumulator.

        THE BUG THIS FIXES. Every accumulator on this class is a ``Decimal``
        (``__init__`` converts them), but this method's declared parameter was ``float``
        and the body did ``self.current_equity += pnl``. ``Decimal + float`` raises
        ``TypeError`` in Python, so the ONLY calls that ever worked were the ones passing
        an ``int``. Reproduced by execution during the pre-launch audit::

            RiskEngine(initial_capital=10000).update_equity(-500.0)
            -> TypeError: unsupported operand type(s) for +=: 'decimal.Decimal' and 'float'

        That matters because ``daily_loss`` is what ``check_daily_loss`` compares against
        the daily loss limit, and ``can_trade`` gates on it. A kill switch that raises
        instead of accumulating is a kill switch that never fires: the exception
        propagates out of whatever was recording the loss, the loss is never added, and
        the limit is never reached no matter how much the account is down.

        Normalising here rather than demanding ``Decimal`` at every call site is the
        narrower change — the accumulators stay exact, and no caller has to be found and
        edited to avoid a crash.

        Regression: tests/test_risk_engine_accumulators_are_decimal.py
        """
        delta = pnl if isinstance(pnl, Decimal) else Decimal(str(pnl))

        self.current_equity += delta

        # Update peak equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        # Update daily loss tracking
        if delta < 0:
            self.daily_loss += abs(delta)
    
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
        """Reset daily loss counter (call at start of new trading day).

        ``Decimal('0')``, not ``0.0``. Assigning a float here replaced the Decimal
        accumulator with a float, so the FIRST loss recorded after the day rolled over
        raised ``TypeError: unsupported operand type(s) for +=: 'float' and
        'decimal.Decimal'`` — a kill switch that worked until midnight and then stopped.
        """
        self.daily_loss = Decimal("0")
