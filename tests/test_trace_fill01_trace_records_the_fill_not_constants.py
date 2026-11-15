"""TRACE-FILL-01: Signal Trace's execution block reports the fill, not constants.

Found on 2026-11-15: every executed signal said fill price = decision price, fee = 0.1%,
slippage = 0.0005 and latency = 10 ms. A paper fill booked at 70.465215 (decision 70.43
plus the simulator's slippage) read as 70.44 in the trace.
"""

from __future__ import annotations

import inspect

import pytest

from backend_app.backend.master_executor import BotRunner, fill_report_of


def _result(**trade):
    return {"result": {"trade_result": trade}}


def test_a_paper_fill_reports_its_own_price_fee_and_slippage():
    price, fee, slip = fill_report_of(
        _result(executed_price="70.465215", fee="0.25", fee_currency=None, paper_ledger="canonical"),
        70.43, 3.544197, "SOL/USDT",
    )
    assert price == pytest.approx(70.465215)
    assert fee == pytest.approx(0.25)
    assert slip == pytest.approx((70.465215 - 70.43) / 70.43)


def test_a_live_fee_in_the_base_asset_is_not_reported():
    _, fee, _ = fill_report_of(
        _result(executed_price=64731.4, fee="1E-7", fee_currency=None), 64731.4, 0.0001, "BTC/USDC")
    assert fee is None
    _, fee, _ = fill_report_of(
        _result(executed_price=64731.4, fee="1E-7", fee_currency="BTC"), 64731.4, 0.0001, "BTC/USDC")
    assert fee is None


def test_a_live_fee_in_the_quote_currency_is_reported():
    _, fee, slip = fill_report_of(
        _result(executed_price=64731.4, fee="0.0647", fee_currency="USDC"), 64731.4, 0.0001, "BTC/USDC")
    assert fee == pytest.approx(0.0647) and slip == pytest.approx(0.0)


def test_nothing_filled_reports_nothing():
    assert fill_report_of(_result(executed_price=1, fee=1), 1, 0, "BTC/USDT") == (None, None, None)
    assert fill_report_of(None, 1, 1, "BTC/USDT") == (None, None, None)
    assert fill_report_of({"result": {}}, 1, 1, "BTC/USDT") == (None, None, None)


def test_the_bot_writes_the_report_and_no_constants():
    src = inspect.getsource(BotRunner)
    call = src.split("await sig_svc.update_order(", 1)[1][:1500]
    assert "average_price=fill_price," in call and "fees=fill_fee," in call
    assert "slippage=fill_slippage," in call and "latency_ms=None," in call
    assert "slippage=0.0005" not in call and "latency_ms=10.0" not in call
