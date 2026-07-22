"""
Base strategy class for modular trading strategies.
All strategies must inherit from BaseStrategy and implement generate_signals.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

import pandas as pd


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""
    
    def __init__(self, params: Dict[str, Any] = None):
        """
        Initialize strategy with parameters.
        
        Args:
            params: Dictionary of strategy-specific parameters
        """
        self.params = params or {}
    
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Generate entry and exit signals from price data.
        
        Args:
            data: DataFrame containing OHLCV data with at least 'close' column
            
        Returns:
            Tuple of (entries, exits) as pandas Series with boolean values
        """
        raise NotImplementedError("Must implement generate_signals")
