"""LIVE-FEE-01: a live fill's commission is read from the venue and stated in quote.

Found on 2026-11-15: every live testnet fill showed no fee. Orders the venue first ACCEPTED
are filled by the watchdog, which never read a fee, so ``trade_result`` kept the
submission's ``fee: "0"`` placeholder with no currency; and Binance charges the asset
received (1E-7 BTC on a 0.0001 BTC buy), which no reader converted.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend_app.backend import exchange_executor as ex
from backend_app.backend.order_watchdog import _record_venue_fee


def test_venue_fee_of_sums_one_currency_and_refuses_a_mix():
    assert ex.venue_fee_of({"fee": {"cost": 1e-7, "currency": "btc"}}) == ("1E-7", "BTC")
    trades = [{"fee": {"cost": "0.01", "currency": "USDC"}}, {"fee": {"cost": "0.02", "currency": "USDC"}}]
    assert ex.venue_fee_of(*trades) == ("0.03", "USDC")
    assert ex.venue_fee_of({"fees": [{"cost": 1, "currency": "BNB"}, {"cost": 1, "currency": "BTC"}]}) == (None, None)
    assert ex.venue_fee_of({"fee": None}, {}) == (None, None)
    assert ex.venue_fee_of({"fee": {"cost": 1, "currency": None}}) == (None, None)


def _executor(order, trades=None, has_trades=True):
    calls = []

    class _Exchange:
        has = {"fetchOrderTrades": has_trades}

        async def fetch_order(self, oid, symbol):
            return order

        async def fetch_order_trades(self, oid, symbol):
            calls.append(oid)
            return trades or []

    executor = ex.ExchangeExecutor.__new__(ex.ExchangeExecutor)
    executor._exchange = _Exchange()
    executor.rate_limiter = SimpleNamespace(acquire=lambda *_a: asyncio.sleep(0))
    executor.normalizer = SimpleNamespace(format_symbol_for_ccxt=lambda s: s)

    async def _connected():
        return None

    executor._ensure_connected = _connected
    return executor, calls


def test_a_filled_orders_fee_is_read_from_its_trades():
    order = {"status": "closed", "filled": 0.0001, "remaining": 0, "average": 64731.4, "fee": None}
    executor, calls = _executor(order, [{"fee": {"cost": 1e-7, "currency": "BTC"}}])
    result = asyncio.run(executor.get_order_status("7304857", "BTC/USDC"))
    assert (result.fee, result.fee_currency) == ("1E-7", "BTC") and calls == ["7304857"]


def test_an_open_order_does_not_read_trades():
    order = {"status": "open", "filled": 0, "remaining": 1, "average": None}
    executor, calls = _executor(order)
    result = asyncio.run(executor.get_order_status("1", "BTC/USDC"))
    assert result.fee is None and calls == []


def test_the_status_stands_when_the_fee_read_fails():
    order = {"status": "closed", "filled": 1, "remaining": 0, "average": 1.0}
    executor, _ = _executor(order)

    async def _boom(*_a):
        raise RuntimeError("rate limited")

    executor._exchange.fetch_order_trades = _boom
    result = asyncio.run(executor.get_order_status("1", "BTC/USDC"))
    assert result.success and result.status == "closed" and result.fee is None


def _order(trade):
    return SimpleNamespace(result={"symbol": "BTC/USDC", "trade_result": dict(trade)})


def test_the_watchdog_replaces_the_accepted_placeholder_with_the_venue_fee():
    order = _order({"fee": "0", "fee_currency": None, "fill_state": "accepted", "size": 0.0})
    status = SimpleNamespace(fee="1E-7", fee_currency="BTC", avg_price="64731.4", filled_size="0.0001")
    _record_venue_fee(order, status)
    trade = order.result["trade_result"]
    assert trade["fee"] == "1E-7" and trade["fee_currency"] == "BTC" and trade["fee_source"] == "venue"
    assert trade["fill_state"] == "filled" and trade["size"] == "0.0001"
    assert trade["executed_price"] == "64731.4"


def test_with_no_venue_fee_the_placeholder_is_cleared_not_kept_as_zero():
    order = _order({"fee": "0", "fee_currency": None, "fill_state": "accepted"})
    _record_venue_fee(order, SimpleNamespace(fee=None, fee_currency=None, avg_price=None, filled_size="1"))
    assert order.result["trade_result"]["fee"] is None


def test_a_real_fee_recorded_at_submission_is_not_overwritten_by_nothing():
    order = _order({"fee": "0.06", "fee_currency": "USDC", "fill_state": "filled"})
    _record_venue_fee(order, SimpleNamespace(fee=None, fee_currency=None, avg_price=None, filled_size="1"))
    assert order.result["trade_result"]["fee"] == "0.06"
