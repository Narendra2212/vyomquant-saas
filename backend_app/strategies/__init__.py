"""
Strategies package for modular trading strategy implementations.
"""

from backend_app.strategies.base import BaseStrategy
from backend_app.strategies.registry import (STRATEGY_REGISTRY, get_strategy,
                                             register_strategy)
from backend_app.strategies.rsi_strategy import RSIStrategy

__all__ = [
    "BaseStrategy",
    "RSIStrategy",
    "STRATEGY_REGISTRY",
    "get_strategy",
    "register_strategy",
]
