"""
tests/test_exchange_certification.py — CCXT Exchange Certification Registry Test Suite.

CLAIM-02 (P2): this suite used to pin the registry's own fabrication as "correct" --
asserting Binance/Bybit/Coinbase/Kraken/OKX were "Verified Level 5 production exchanges"
and that preflight would *pass* a live Binance futures deployment, when nothing in this
codebase has ever placed a single sandbox or live order against any of these venues
(staging/EXCHANGE_SANDBOX_CERTIFICATION.md, Phase 10, 2026-10-10: "SANDBOX ORDERS
PLACED: 0"). This file now pins the corrected, honest baseline instead:

1. No registered exchange is certified for live trading (Level 5) -- none has ever been
   measured past "ccxt declares this market metadata" (Level 1).
2. Preflight therefore blocks every registered exchange, for every deployment, citing its
   actual (Level 1) status -- not the market-type/order-type/credential checks that used
   to be reachable only because the certification check was fabricated as already passed.
3. The market-type/order-type branch logic inside `validate_deployment_preflight` is still
   exercised directly, against a synthetic Level-5 capability record, so that logic keeps
   real coverage without the registry's singleton data pretending to supply it.
4. No registered exchange claims margin/leverage/futures/swap support, because this
   platform has no such order path anywhere (GUARD-01/RT-21's own finding) -- independent
   of what any venue itself offers.
"""

from uuid import uuid4

import pytest

from backend_app.core.exchange_certification import (
    CertificationLevel, ExchangeCapabilities, ExchangeCertificationRegistry,
    get_exchange_certification_registry
)

REGISTERED_EXCHANGES = ("binance", "bybit", "coinbase", "kraken", "okx", "kucoin", "gateio", "bitfinex")


def test_no_registered_exchange_is_production_certified():
    """CLAIM-02's core regression: zero sandbox/live orders have ever been placed, so
    nothing here may claim Level 5, however many venues the registry lists.
    """
    registry = get_exchange_certification_registry()
    for exchange_id in REGISTERED_EXCHANGES:
        assert registry.is_certified(exchange_id) is False, (
            f"{exchange_id} is reported production-certified with no measured evidence"
        )
    assert registry.is_certified("unknown_dex") is False


def test_every_registered_exchange_is_capped_at_level_1():
    """The ceiling without a real authenticated or sandbox-order measurement is Level 1
    ("market metadata verified", read from ccxt's own declared `has` map) -- Level 2
    requires a real account call and Level 3 a real sandbox order, neither of which this
    platform has ever performed for any venue.
    """
    registry = get_exchange_certification_registry()
    for exchange_id in REGISTERED_EXCHANGES:
        assert registry.get_certification_level(exchange_id) == CertificationLevel.LEVEL_1_MARKET_METADATA_VERIFIED, (
            f"{exchange_id} claims a certification level beyond what was ever measured"
        )
    assert registry.get_certification_level("non_existent") == CertificationLevel.LEVEL_0_METADATA_ONLY


def test_no_registered_exchange_claims_margin_leverage_futures_or_swap():
    """GUARD-01/RT-21: this platform has no margin, leverage, futures, or swap order path
    anywhere in its execution pipeline. A capability registry claiming any venue supports
    them would advertise a feature that, if ever acted on, routes through code that does
    not exist.
    """
    registry = get_exchange_certification_registry()
    for exchange_id in REGISTERED_EXCHANGES:
        caps = registry.get_capabilities(exchange_id)
        assert caps.margin is False, f"{exchange_id} claims margin support"
        assert caps.leverage is False, f"{exchange_id} claims leverage support"
        assert caps.futures is False, f"{exchange_id} claims futures support"
        assert caps.swap is False, f"{exchange_id} claims swap support"


def test_preflight_blocks_every_registered_exchange_on_certification_alone():
    """Every live-deployment preflight call is refused today, citing the venue's real
    (Level 1) status -- not because of market type, order type, or credentials, which
    is exactly what made the old Level-5 fabrication dangerous: it let those unrelated
    checks look like the thing actually gating live trading.
    """
    registry = get_exchange_certification_registry()
    for exchange_id in REGISTERED_EXCHANGES:
        valid, reason = registry.validate_deployment_preflight({
            "exchange_id": exchange_id,
            "api_key": "k_key",
            "api_secret": "k_sec",
            "symbol": "BTC/USDT",
            "strategy_id": "strat_1",
            "risk_settings": {"max_daily_loss": 500.0},
        })
        assert valid is False
        assert "not certified for live trading" in reason
        assert "Level 1" in reason


def test_preflight_market_type_and_order_type_logic_still_works():
    """The branch logic for market_type/order_type/credentials/symbol/strategy checks is
    real code worth keeping covered, even though no registered exchange can reach it
    today. Exercised here against a synthetic Level-5 record built just for this test --
    not the live singleton, so this can never again be mistaken for "Coinbase is Level 5".
    """
    registry = ExchangeCertificationRegistry()
    registry._registry["test_venue_no_futures"] = ExchangeCapabilities(
        exchange_id="test_venue_no_futures",
        display_name="Test Venue (no futures)",
        certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
        futures=False,
    )
    registry._registry["test_venue_full"] = ExchangeCapabilities(
        exchange_id="test_venue_full",
        display_name="Test Venue (full)",
        certification_level=CertificationLevel.LEVEL_5_PRODUCTION_READY,
        futures=True,
        stop_market=True,
    )
    from backend_app.core.exchange_certification import RuntimeExchangeHealth
    registry._health_status["test_venue_no_futures"] = RuntimeExchangeHealth.HEALTHY
    registry._health_status["test_venue_full"] = RuntimeExchangeHealth.HEALTHY

    valid_no_fut, reason_no_fut = registry.validate_deployment_preflight({
        "exchange_id": "test_venue_no_futures",
        "market_type": "futures",
        "api_key": "k", "api_secret": "s",
        "symbol": "BTC/USDT", "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0},
    })
    assert valid_no_fut is False
    assert "does not support futures trading" in reason_no_fut

    valid_full, reason_full = registry.validate_deployment_preflight({
        "exchange_id": "test_venue_full",
        "market_type": "futures",
        "api_key": "k", "api_secret": "s",
        "symbol": "BTC/USDT", "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0},
    })
    assert valid_full is True
    assert "passed" in reason_full

    # Missing credentials is still caught, independent of market type.
    valid_no_creds, reason_no_creds = registry.validate_deployment_preflight({
        "exchange_id": "test_venue_full",
        "symbol": "BTC/USDT", "strategy_id": "strat_1",
        "risk_settings": {"max_daily_loss": 500.0},
    })
    assert valid_no_creds is False
    assert "credentials" in reason_no_creds.lower()


def test_last_verified_at_is_not_fabricated_as_now():
    """CLAIM-02: this field used to default to `datetime.now()` on every construction,
    so simply reading a capability record claimed it had "just been verified". Nothing is
    continuously re-verified, so the honest default is unknown (None).
    """
    caps = ExchangeCapabilities(
        exchange_id="x", display_name="X",
        certification_level=CertificationLevel.LEVEL_1_MARKET_METADATA_VERIFIED,
    )
    assert caps.last_verified_at is None

    registry = get_exchange_certification_registry()
    for exchange_id in REGISTERED_EXCHANGES:
        assert registry.get_capabilities(exchange_id).last_verified_at is None
