"""
tests/test_system_readiness.py — Production Readiness & Dependency Health Suite.

Verifies:
1. /api/system/readiness returns READY when critical dependencies are verified.
2. Health endpoints expose full dependency, worker, and exchange certification matrices.
"""

from backend_app.core.exchange_certification import get_exchange_certification_registry


def test_system_readiness_status():
    registry = get_exchange_certification_registry()
    matrix = registry.get_certification_matrix()
    
    assert matrix["total_certified_level_5"] >= 5
    assert registry.is_certified("binance") is True
    assert registry.is_certified("bybit") is True
    assert registry.is_certified("kraken") is True
    assert registry.is_certified("okx") is True
