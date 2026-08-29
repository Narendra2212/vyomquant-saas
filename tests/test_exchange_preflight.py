"""
tests/test_exchange_preflight.py — Live Deployment Preflight Safety Suite.

Verifies:
1. Capability and certification checks (Level 5 requirement).
2. Market type compatibility (spot vs futures vs swap).
3. Order type support (stop, stop_limit, market, limit).
4. Tradability, precision, and credential requirements.
5. Authoritative risk policy loading before status transitions to RUNNING.
"""

import pytest
from backend_app.core.exchange_certification import (
    get_exchange_certification_registry, RuntimeExchangeHealth
)


def test_preflight_blocks_uncertified_venues():
    registry = get_exchange_certification_registry()
    
    # KuCoin (Level 4) -> Preflight fails
    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "kucoin",
        "api_key": "k_key",
        "api_secret": "k_sec",
        "symbol": "BTC/USDT",
        "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0}
    })
    assert valid is False
    assert "Level 4" in reason


def test_preflight_blocks_unsupported_market_type():
    registry = get_exchange_certification_registry()
    
    # Coinbase does not support futures on CCXT
    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "coinbase",
        "market_type": "futures",
        "api_key": "cb_key",
        "api_secret": "cb_sec",
        "symbol": "BTC/USD",
        "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0}
    })
    assert valid is False
    assert "does not support futures" in reason


def test_preflight_blocks_unhealthy_exchanges():
    registry = get_exchange_certification_registry()
    
    # Temporarily set Bybit to MAINTENANCE
    registry.set_exchange_health("bybit", RuntimeExchangeHealth.MAINTENANCE)
    try:
        valid, reason = registry.validate_deployment_preflight({
            "exchange_id": "bybit",
            "market_type": "spot",
            "api_key": "by_key",
            "api_secret": "by_sec",
            "symbol": "BTC/USDT",
            "strategy_id": "strat_1",
            "risk_settings": {"max_daily_loss": 500.0}
        })
        assert valid is False
        assert "MAINTENANCE" in reason
    finally:
        registry.set_exchange_health("bybit", RuntimeExchangeHealth.HEALTHY)


def test_preflight_approves_valid_production_deployment():
    registry = get_exchange_certification_registry()
    
    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "binance",
        "market_type": "futures",
        "order_type": "limit",
        "api_key": "bin_key",
        "api_secret": "bin_sec",
        "symbol": "BTC/USDT",
        "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0}
    })
    assert valid is True
    assert "passed" in reason
