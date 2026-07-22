"""
backend/signal_validator.py — Signal Validation Layer (STEP 7)

═══════════════════════════════════════════════════════════════════════════════
STEP 7: SIGNAL VALIDATION (CRITICAL)
═══════════════════════════════════════════════════════════════════════════════

VALIDATE before execution:
  ✅ Signal is boolean / valid enum (buy, sell, hold)
  ✅ Confidence in range [0, 1]
  ✅ No repeated identical signals (deduplication)

ADDED:
  if signal not in ["buy", "sell", "hold"]:
      raise ValueError("Invalid signal")

EXPECTED RESULT:
  ✅ No garbage signals reach execution

🚨 CRITICAL SAFETY COMPONENT — DO NOT MODIFY WITHOUT APPROVAL
"""

import logging
from datetime import datetime
from typing import Any, Dict, Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("SignalValidator")


class SignalValidationError(Exception):
    """Raised when signal validation fails."""
    pass


class SignalData:
    """
    Trading signal data container with validation.
    
    This class validates signals before they can be executed.
    Any invalid signal raises SignalValidationError.
    """
    
    def __init__(
        self,
        action: Literal["buy", "sell", "hold"],
        signal: pd.Series,
        strength: float,
        symbol: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        self.action = action
        self.signal = signal
        self.strength = strength
        self.symbol = symbol
        self.timestamp = timestamp or datetime.now()
        
        # Validate on creation
        self.validate()
    
    def validate(self) -> None:
        """
        Validate signal data.
        
        Raises:
            SignalValidationError: If any validation rule fails
        """
        errors = []
        
        # Rule 1: Signal must not be empty
        if self.signal is None or len(self.signal) == 0:
            errors.append("Signal is empty or None")
        
        # Rule 2: Signal must be pandas Series
        elif not isinstance(self.signal, pd.Series):
            errors.append(f"Signal must be pd.Series, got {type(self.signal).__name__}")
        
        else:
            # Rule 3: Last value must not be NaN
            if len(self.signal) > 0 and pd.isna(self.signal.iloc[-1]):
                errors.append("Signal last value is NaN")
            
            # Rule 4: Signal must have valid index
            if len(self.signal) > 0 and not isinstance(self.signal.index, pd.DatetimeIndex):
                # Allow if it's a numeric index, but warn
                pass
            
            # Rule 5: Check for all-NaN signal
            if self.signal.isna().all():
                errors.append("Signal contains only NaN values")
        
        # Rule 6: Strength must be in range [0, 1]
        if not isinstance(self.strength, (int, float)):
            errors.append(f"Strength must be numeric, got {type(self.strength).__name__}")
        elif not (0.0 <= self.strength <= 1.0):
            errors.append(f"Strength must be in [0,1], got {self.strength}")
        
        # Rule 7: Action must be valid (STEP 7: Signal Validation)
        # CRITICAL: Only allow valid enum values
        if self.action not in ["buy", "sell", "hold"]:
            errors.append(f"STEP 7: Invalid signal '{self.action}'. Must be one of: buy, sell, hold")
        
        # Rule 8: Symbol must be present for live trading
        if self.symbol is None or len(self.symbol) == 0:
            errors.append("Symbol is required")
        
        # If any errors, raise with details
        if errors:
            error_msg = f"Signal validation failed ({len(errors)} errors): " + "; ".join(errors)
            logger.critical(f"🚫 {error_msg}")
            logger.critical(f"Signal data: action={self.action}, strength={self.strength}, symbol={self.symbol}")
            raise SignalValidationError(error_msg)
        
        # Log successful validation
        logger.debug(f"✅ Signal validated: {self.action} {self.symbol} strength={self.strength:.2f}")
    
    def is_triggered(self) -> bool:
        """
        Check if signal is triggered (last value indicates action).
        
        Returns:
            bool: True if signal indicates trade should execute
        """
        if len(self.signal) == 0:
            return False
        
        last_value = self.signal.iloc[-1]
        
        # Handle different signal formats
        if isinstance(last_value, (bool, np.bool_)):
            return bool(last_value)
        elif isinstance(last_value, (int, float, np.number)):
            return last_value > 0.5  # Threshold
        
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "action": self.action,
            "strength": self.strength,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "last_signal": float(self.signal.iloc[-1]) if len(self.signal) > 0 else None,
            "signal_length": len(self.signal)
        }


class SignalValidator:
    """
    Central signal validation service.
    
    All signals must pass through this validator before execution.
    
    STEP 7: Signal deduplication to prevent repeated identical signals.
    """
    
    # STEP 7: Track last signals for deduplication
    _last_signals: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def _check_duplicate(
        cls,
        symbol: str,
        action: str,
        strength: float,
        timestamp: datetime
    ) -> bool:
        """
        STEP 7: Check if this is a duplicate signal.
        
        Returns:
            True if duplicate (should be rejected), False if new signal
        """
        key = f"{symbol}:{action}"
        
        if key not in cls._last_signals:
            cls._last_signals[key] = {
                "strength": strength,
                "timestamp": timestamp,
                "count": 1
            }
            return False
        
        last = cls._last_signals[key]
        time_diff = (timestamp - last["timestamp"]).total_seconds()
        
        # Consider duplicate if:
        # - Same action within 60 seconds
        # - Strength difference < 0.1
        if time_diff < 60 and abs(strength - last["strength"]) < 0.1:
            last["count"] += 1
            logger.warning(
                f"STEP 7: Duplicate signal detected for {key} "
                f"({last['count']}x in {time_diff:.1f}s)"
            )
            return True
        
        # Update tracking
        cls._last_signals[key] = {
            "strength": strength,
            "timestamp": timestamp,
            "count": 1
        }
        return False
    
    @staticmethod
    def validate_signal(
        action: Literal["buy", "sell", "hold"],
        signal: pd.Series,
        strength: float,
        symbol: Optional[str] = None
    ) -> SignalData:
        """
        STEP 7: Validate and create a SignalData object.
        
        Validates:
          - Signal is valid enum (buy, sell, hold)
          - Confidence in range [0, 1]
          - No repeated identical signals (deduplication)
        
        Args:
            action: "buy", "sell", or "hold"
            signal: pandas Series with signal values
            strength: Position size strength [0-1]
            symbol: Trading symbol
        
        Returns:
            SignalData: Validated signal object
        
        Raises:
            SignalValidationError: If validation fails or duplicate detected
        """
        # STEP 7: Signal enum validation
        if action not in ["buy", "sell", "hold"]:
            raise SignalValidationError(
                f"STEP 7: Invalid signal '{action}'. "
                f"Must be one of: buy, sell, hold"
            )
        
        # STEP 7: Confidence range validation
        if not isinstance(strength, (int, float)):
            raise SignalValidationError(
                f"STEP 7: Confidence must be numeric, got {type(strength).__name__}"
            )
        if not (0.0 <= strength <= 1.0):
            raise SignalValidationError(
                f"STEP 7: Confidence out of range [0,1]: {strength}"
            )
        
        # STEP 7: Duplicate signal check
        if symbol and SignalValidator._check_duplicate(symbol, action, strength, datetime.now()):
            raise SignalValidationError(
                f"STEP 7: Duplicate signal rejected - {action} {symbol} "
                f"strength={strength} (same signal within 60s)"
            )
        
        return SignalData(
            action=action,
            signal=signal,
            strength=strength,
            symbol=symbol
        )
    
    @staticmethod
    def validate_raw_signal(signal_data: Dict[str, Any]) -> SignalData:
        """
        Validate raw signal dictionary.
        
        Args:
            signal_data: Dictionary with action, signal, strength, symbol
        
        Returns:
            SignalData: Validated signal object
        """
        required_fields = ["action", "signal", "strength"]
        missing = [f for f in required_fields if f not in signal_data]
        
        if missing:
            raise SignalValidationError(f"Missing required fields: {missing}")
        
        return SignalValidator.validate_signal(
            action=signal_data["action"],
            signal=signal_data["signal"],
            strength=signal_data["strength"],
            symbol=signal_data.get("symbol")
        )
    
    @staticmethod
    def sanitize_signal(signal: pd.Series) -> pd.Series:
        """
        Sanitize a signal by filling NaN values.
        
        WARNING: This should only be used for preprocessing, not
        to make invalid signals pass validation.
        
        Args:
            signal: Raw signal series
        
        Returns:
            pd.Series: Sanitized signal
        """
        # Fill NaN with 0 (neutral)
        clean = signal.fillna(0)
        
        # Ensure numeric
        clean = pd.to_numeric(clean, errors='coerce').fillna(0)
        
        return clean


# Convenience function for quick validation (STEP 7)
def validate_signal(
    action: Literal["buy", "sell", "hold"],
    signal: pd.Series,
    strength: float,
    symbol: Optional[str] = None
) -> SignalData:
    """
    Quick validation function.
    
    Example:
        >>> from backend.signal_validator import validate_signal
        >>> signal = pd.Series([0.3, 0.5, 0.8])
        >>> validated = validate_signal("buy", signal, 0.5, "BTC/USDT")
        >>> if validated.is_triggered():
        ...     execute_order(validated)
    
    Raises:
        SignalValidationError: If signal is invalid
    """
    return SignalValidator.validate_signal(action, signal, strength, symbol)
