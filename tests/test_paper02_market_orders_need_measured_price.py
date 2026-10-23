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
    # PAPER-02: the measured-price seam is now `resolve_market_price`, which tries the
    # `mds:last_price` cache (`get_fresh_market_price`) first and then a bounded on-demand
    # venue quote. This assertion named the cache function directly; it names the seam the
    # order path actually calls, so it keeps testing "the price comes from a measurement"
    # rather than pinning which measurement.
    assert "resolve_market_price" in src
    assert "price=price" not in src.split('clean_type == "market"')[1].split("else")[0], (
        "the client's price must not reach the market branch"
    )


def test_the_measured_price_seam_still_consults_the_cache_first():
    """`resolve_market_price` must prefer the free, already-streamed price.

    Pinned because the ordering is what keeps the on-demand venue read rare, and because
    `tests/conftest.py`'s `fresh_paper_quote` fixture patches `get_fresh_market_price` and
    relies on that call short-circuiting the venue path for ~13 other suites.
    """
    src = inspect.getsource(pts.resolve_market_price)
    assert src.index("get_fresh_market_price") < src.index("fetch_venue_quote"), (
        "the cache must be consulted before the venue"
    )


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
