"""PAPER-02: a manual paper market order never fills at a constant or a client price."""
import asyncio
import inspect

import pytest

from backend_app.backend import paper_trading_service as pts


def test_no_reference_price_table_remains():
    assert not hasattr(pts, "REFERENCE_PRICES")
    assert not hasattr(pts, "REFERENCE_PRICE_DEFAULT")


def test_default_provider_has_no_quote():
    assert asyncio.run(pts.get_fresh_market_price("BTC-USDT")) is None


def test_place_order_source_ignores_client_price_for_market():
    src = inspect.getsource(pts.PaperTradingService.place_order)
    assert 'clean_type == "market"' in src
    assert "get_fresh_market_price" in src


def _service_with_account(monkeypatch):
    svc = pts.PaperTradingService()
    monkeypatch.setattr(svc, "_client", lambda token=None: object())
    monkeypatch.setattr(
        svc, "_account_and_positions",
        lambda sb, uid: ({"id": "a1", "available_balance": "100000", "total_equity": "100000", "realized_pnl": "0", "initial_balance": "100000", "balance": "100000"}, []),
    )
    return svc


def test_market_order_refused_without_fresh_price(monkeypatch):
    from backend_app.backend.paper.paper_market_feed import PaperMarketDataUnavailable

    svc = _service_with_account(monkeypatch)
    with pytest.raises(PaperMarketDataUnavailable):
        # client price supplied must NOT rescue the order
        asyncio.run(svc.place_order("u1", "BTC-USDT", "buy", "market", 0.01, price=1.0))


def test_limit_order_requires_a_price(monkeypatch):
    svc = _service_with_account(monkeypatch)
    with pytest.raises(ValueError, match="positive price"):
        asyncio.run(svc.place_order("u1", "BTC-USDT", "buy", "limit", 0.01, price=None))
