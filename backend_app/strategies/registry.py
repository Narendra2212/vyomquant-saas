"""
Strategy registry for managing available trading strategies.
Add new strategies here to make them available to the backtest engine.
"""

from typing import Dict, Type

from backend_app.strategies.base import BaseStrategy
from backend_app.strategies.rsi_strategy import RSIStrategy

# Registry of available strategies
# Key: strategy name (string)
# Value: strategy class (subclass of BaseStrategy)
STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "rsi": RSIStrategy,
}


def get_strategy(strategy_name: str) -> Type[BaseStrategy]:
    """
    Get strategy class by name.
    
    Args:
        strategy_name: Name of the strategy to retrieve
        
    Returns:
        Strategy class
        
    Raises:
        ValueError: If strategy name is not found in registry
    """
    strategy_class = STRATEGY_REGISTRY.get(strategy_name.lower())
    if strategy_class is None:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise ValueError(
            f"Strategy '{strategy_name}' not found. "
            f"Available strategies: {available}"
        )
    return strategy_class


def register_strategy(name: str, strategy_class: Type[BaseStrategy]) -> None:
    """
    Register a new strategy in the registry.
    
    Args:
        name: Name to register the strategy under
        strategy_class: Strategy class to register
    """
    STRATEGY_REGISTRY[name.lower()] = strategy_class
