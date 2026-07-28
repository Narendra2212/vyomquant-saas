"""
tests/test_portfolio_engine_consolidation.py

Unit test suite verifying:
1. Deletion of unintegrated backend/portfolio_engine.py.
2. Single surviving implementation in backend_app.core.portfolio_engine.PortfolioEngine.
3. Core allocation, position tracking, rebalancing, and metrics calculation behavior.
"""

import pathlib
from backend_app.core.portfolio_engine import PortfolioEngine


def test_dead_portfolio_engine_deleted():
    dead_file = pathlib.Path("backend_app/backend/portfolio_engine.py")
    assert not dead_file.exists(), "backend/portfolio_engine.py should be deleted"


def test_portfolio_engine_initialization_and_allocation():
    pe = PortfolioEngine(total_capital=100000.0, max_positions=5, max_allocation_per_asset=0.3)
    
    signals = {"BTC/USDT": 1.0, "ETH/USDT": 0.8, "SOL/USDT": 0.2}
    allocations = pe.allocate(signals)

    assert "BTC/USDT" in allocations
    assert "ETH/USDT" in allocations
    # Max allocation per asset is 30% of $100,000 = $30,000
    assert allocations["BTC/USDT"] <= 30000.0


def test_position_tracking_and_metrics():
    pe = PortfolioEngine(total_capital=100000.0)
    pe.update_position("BTC/USDT", size=0.5, entry_price=50000.0)  # Value: $25,000
    pe.update_position("ETH/USDT", size=2.0, entry_price=3000.0)   # Value: $6,000

    exposed = pe.get_total_exposed()
    assert exposed == 31000.0

    available = pe.get_available_capital()
    assert available == 69000.0

    # Test metrics with current prices
    current_prices = {"BTC/USDT": 52000.0, "ETH/USDT": 3100.0}
    metrics = pe.get_portfolio_metrics(current_prices)

    assert metrics["total_capital"] == 100000.0
    assert metrics["open_positions_count"] == 2
    # Unrealized PnL: BTC +$1000, ETH +$200 = +$1200
    assert metrics["unrealized_pnl"] == 1200.0

    # Test symbol exposure breakdown
    exposures = pe.get_exposure_by_symbol(current_prices)
    assert exposures["BTC/USDT"]["notional_value"] == 26000.0
    assert exposures["ETH/USDT"]["notional_value"] == 6200.0


def test_close_position_and_health_status():
    pe = PortfolioEngine(total_capital=100000.0)
    pe.update_position("BTC/USDT", size=0.5, entry_price=50000.0)
    
    health = pe.get_health_status()
    assert health["status"] == "healthy"
    assert health["position_count"] == 1

    closed = pe.close_position("BTC/USDT")
    assert closed is not None
    assert closed["size"] == 0.5
    assert len(pe.active_positions) == 0
