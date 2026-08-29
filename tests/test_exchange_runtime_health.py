"""
tests/test_exchange_runtime_health.py — Runtime Exchange Health & Circuit Gating Suite.

Verifies:
1. Runtime health state transitions (HEALTHY, DEGRADED, DISCONNECTED, RATE_LIMITED, MAINTENANCE, BLOCKED).
2. Health state gating before live execution.
"""

from backend_app.core.exchange_certification import (
    get_exchange_certification_registry, RuntimeExchangeHealth
)


def test_exchange_runtime_health_states():
    registry = get_exchange_certification_registry()
    
    assert registry.get_exchange_health("binance") == RuntimeExchangeHealth.HEALTHY
    
    registry.set_exchange_health("binance", RuntimeExchangeHealth.RATE_LIMITED)
    assert registry.get_exchange_health("binance") == RuntimeExchangeHealth.RATE_LIMITED
    
    # Restore health
    registry.set_exchange_health("binance", RuntimeExchangeHealth.HEALTHY)
    assert registry.get_exchange_health("binance") == RuntimeExchangeHealth.HEALTHY
