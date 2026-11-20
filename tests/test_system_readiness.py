"""
tests/test_system_readiness.py — Production Readiness & Dependency Health Suite.

Verifies:
1. /api/system/readiness's exchange-certification figures reflect reality (CLAIM-02):
   zero registered venues are Level-5 certified, because none has ever had a sandbox or
   live order placed against it.
2. The certification matrix still reports every registered venue, so the figure's absence
   of Level-5 entries is a correction, not venues going missing.
"""

from backend_app.core.exchange_certification import get_exchange_certification_registry


def test_system_readiness_status():
    registry = get_exchange_certification_registry()
    matrix = registry.get_certification_matrix()

    # CLAIM-02: this used to assert >= 5 and True for each of these -- the exact
    # fabricated claim the finding is about. Corrected to the honest baseline: nothing is
    # production-certified without a real measurement, and none exists yet.
    assert matrix["total_certified_level_5"] == 0
    assert matrix["total_registered"] == 8
    assert registry.is_certified("binance") is False
    assert registry.is_certified("bybit") is False
    assert registry.is_certified("kraken") is False
    assert registry.is_certified("okx") is False
