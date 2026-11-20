"""RT-06 regression: only a `completed` execution result moves BotRunner exposure."""
from decimal import Decimal

import pytest

from backend_app.backend.master_executor import settle_exposure

PREV = Decimal("1.5")
QTY = Decimal("0.2")


def _res(status, size=None):
    r = {"status": status, "execution_id": "e"}
    if size is not None:
        r["result"] = {"trade_result": {"size": size}}
    return r


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        _res("blocked"),
        _res("failed"),
        _res("skipped_completed"),      # a dedupe replay is NOT a second fill
        _res("skipped_executing"),
        _res("skipped_failed"),
        _res("skipped_contention"),
        _res("skipped_ambiguous_submission"),
        _res("something_new_and_unknown"),
    ],
)
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_non_completed_results_leave_exposure_unchanged(result, side):
    filled, exposure = settle_exposure(side=side, qty=QTY, previous_exposure=PREV, result=result)
    assert filled == 0
    assert exposure == PREV


def test_completed_buy_adds_the_reported_fill_size():
    filled, exposure = settle_exposure(side="buy", qty=QTY, previous_exposure=PREV, result=_res("completed", "0.15"))
    assert filled == Decimal("0.15")
    assert exposure == Decimal("1.65000000")


def test_completed_sell_subtracts_and_never_goes_negative():
    _, exposure = settle_exposure(side="sell", qty=QTY, previous_exposure=Decimal("0.1"), result=_res("completed", "0.2"))
    assert exposure == Decimal("0")


def test_completed_without_a_reported_size_uses_the_requested_quantity():
    filled, exposure = settle_exposure(side="buy", qty=QTY, previous_exposure=PREV, result=_res("completed"))
    assert filled == QTY
    assert exposure == Decimal("1.70000000")


def test_completed_with_zero_reported_size_is_not_a_fill():
    filled, exposure = settle_exposure(side="buy", qty=QTY, previous_exposure=PREV, result=_res("completed", "0"))
    assert filled == 0 and exposure == PREV
