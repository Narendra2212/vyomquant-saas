"""PORT-01: the paper Portfolio/Dashboard reported 0.00 capital in use and 0.00 exposure.

``get_portfolio_overview(environment="paper")`` set ``used_balance`` to the account row's
``locked_balance`` - funds held by PENDING orders only - and ``total_exposure`` to that same
figure. A filled spot buy locks nothing, so production (2026-11-13) showed "Invested 0.00 /
Total exposure 0.00" while the same response listed a BTC position worth $120.91.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

USER = {"id": "u-1"}
ROW = {
    "total_equity": 99999.83, "available_balance": 99878.92, "locked_balance": 0.0,
    "realized_pnl": 0.0, "unrealized_pnl": -0.03, "initial_capital": 100000.0,
}
POSITION = {"symbol": "BTC-USDT", "side": "long", "size": "0.001757", "current_price": "68815.300455"}


class _Svc:
    def __init__(self, positions=None, boom=False):
        self.positions, self.boom = positions or [], boom

    def get_or_create_account(self, user_id):
        return dict(ROW)

    def get_trades(self, user_id):
        return []

    def get_positions(self, user_id):
        if self.boom:
            raise RuntimeError("positions read failed")
        return [dict(p) for p in self.positions]


def _overview(svc):
    with patch("backend_app.backend.paper_trading_service.get_paper_trading_service", return_value=svc):
        return asyncio.run(DashboardAggregationService().get_portfolio_overview(USER, environment="paper"))


def test_a_filled_spot_position_is_capital_in_use_and_exposure():
    o = _overview(_Svc([POSITION]))
    assert o["used_balance"] == 120.91  # 99999.83 - 99878.92
    assert o["total_exposure"] == round(0.001757 * 68815.300455, 2) == 120.91


def test_no_positions_is_zero_exposure_not_absent():
    o = _overview(_Svc([]))
    assert o["total_exposure"] == 0.0


def test_an_unreadable_mark_absents_exposure_rather_than_understating_it():
    o = _overview(_Svc([POSITION, {"size": "1", "current_price": None}]))
    assert o["total_exposure"] is None
    assert o["used_balance"] == 120.91


def test_a_failed_positions_read_absents_only_exposure():
    o = _overview(_Svc(boom=True))
    assert o["total_exposure"] is None
    assert o["total_equity"] == 99999.83 and o["used_balance"] == 120.91
