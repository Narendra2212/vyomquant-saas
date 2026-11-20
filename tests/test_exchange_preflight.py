"""
tests/test_exchange_preflight.py — Live Deployment Preflight Safety Suite.

Verifies:
1. Certification checks block every registered venue today (CLAIM-02: none has ever had
   a sandbox or live order placed against it -- see exchange_certification.py's module
   docstring and staging/EXCHANGE_SANDBOX_CERTIFICATION.md).
2. Market type compatibility (spot vs futures vs swap), order type support, health, and
   credential checks -- exercised against a synthetic Level-5 record, since no registered
   venue can reach those branches today (certification is checked first and always fails).
3. Tradability, precision, and credential requirements.
"""

import pytest
from backend_app.core.exchange_certification import (
    CertificationLevel, ExchangeCapabilities, ExchangeCertificationRegistry,
    RuntimeExchangeHealth, get_exchange_certification_registry
)


def _certified_registry() -> ExchangeCertificationRegistry:
    """A registry seeded with one synthetic Level-5 venue, for exercising the
    market-type/order-type/health/credential branches of validate_deployment_preflight
    without implying any real, registered exchange holds that status.
    """
    registry = ExchangeCertificationRegistry()
    registry._registry["synthetic_l5"] = ExchangeCapabilities(
        exchange_id="synthetic_l5",
        display_name="Synthetic Level 5",
        certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
        futures=True,
        stop_market=True,
        stop_limit=True,
    )
    registry._health_status["synthetic_l5"] = RuntimeExchangeHealth.HEALTHY
    return registry


def test_preflight_blocks_uncertified_venues():
    registry = get_exchange_certification_registry()

    # KuCoin is registered but capped at Level 1 (CLAIM-02) -> preflight fails.
    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "kucoin",
        "api_key": "k_key",
        "api_secret": "k_sec",
        "symbol": "BTC/USDT",
        "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0}
    })
    assert valid is False
    assert "Level 1" in reason


def test_preflight_blocks_unsupported_market_type():
    registry = _certified_registry()
    registry._registry["synthetic_l5"].futures = False

    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "synthetic_l5",
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
    registry = _certified_registry()
    registry.set_exchange_health("synthetic_l5", RuntimeExchangeHealth.MAINTENANCE)

    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "synthetic_l5",
        "market_type": "spot",
        "api_key": "by_key",
        "api_secret": "by_sec",
        "symbol": "BTC/USDT",
        "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0}
    })
    assert valid is False
    assert "MAINTENANCE" in reason


def test_preflight_approves_valid_production_deployment():
    """A Level-5-certified, healthy venue with full credentials still passes preflight --
    this is the branch logic CLAIM-02's correction left untouched; it is simply
    unreachable by any venue registered today.
    """
    registry = _certified_registry()

    valid, reason = registry.validate_deployment_preflight({
        "exchange_id": "synthetic_l5",
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


def test_no_registered_exchange_reaches_level_5_today():
    """NEGATIVE CONTROL for the two tests above: confirms this suite's synthetic venue is
    doing real work, by proving the real singleton has nothing at Level 5 to test against.
    """
    registry = get_exchange_certification_registry()
    for exchange_id in ("binance", "bybit", "coinbase", "kraken", "okx"):
        assert registry.is_certified(exchange_id) is False
