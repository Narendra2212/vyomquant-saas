"""BT-05 / BT-06 -- a backtest says what dates it covered, and a trade's return is sane.

BT-05: a 2022-2026 request on 15-minute bars simulated only ~3.4 months (the pair listed in
Dec 2023 and the fetch stops at the bar cap) and nothing said so.
BT-06: ``return_pct`` divided a trade's P&L by the entry PRICE, so a 60 loss on a 1,000
position read -9,567%.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from backend_app.backend.backtest_runtime import _data_coverage  # noqa: E402

TF = 15 * 60 * 1000
START = 1703750400000  # 2023-12-28T08:00:00Z


def _bars(n):
    return [[START + i * TF, 1, 1, 1, 1, 1] for i in range(n)]


def test_a_cap_truncated_run_is_flagged():
    until = START + 400 * 24 * 3600 * 1000
    c = _data_coverage(_bars(10_000), "2023-12-28T08:00:00+00:00", "2025-02-01T00:00:00+00:00", TF, 10_000)
    assert c["truncated_by_cap"] is True
    assert c["first_bar"] == "2023-12-28T08:00:00Z"
    assert c["bars"] == 10_000


def test_a_run_that_reached_its_end_is_not_flagged():
    c = _data_coverage(_bars(600), "2023-12-28T08:00:00+00:00", "2023-12-30T00:00:00+00:00", TF, 10_000)
    assert c["truncated_by_cap"] is False
    assert c["starts_after_request"] is False


def test_data_that_starts_after_the_request_is_flagged():
    c = _data_coverage(_bars(300), "2022-10-18T00:00:00+00:00", "2024-01-02T00:00:00+00:00", TF, 10_000)
    assert c["starts_after_request"] is True


def test_unreadable_dates_do_not_raise():
    c = _data_coverage(_bars(60), "", "", TF, 10_000)
    assert c["requested_start"] is None and c["truncated_by_cap"] is False


def test_trade_return_is_pnl_over_the_position_not_over_the_price():
    import inspect

    from backend_app.backend import backtesting_engine

    src = inspect.getsource(backtesting_engine)
    assert "trade['PnL'] / trade['Entry Price']) * 100" not in src
    assert "abs(trade['Size']) * trade['Entry Price']" in src
