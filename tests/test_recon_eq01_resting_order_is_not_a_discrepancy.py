"""RECON-EQ-01: a resting order is not a reconciliation discrepancy.

Production 2026-11-20: the watchdog compared the platform's `EXECUTING` with the venue's
`open` as raw strings, so one resting Testnet limit order wrote a reconciliation_mismatches
row (and an alert) every minute -- 21 in 21 minutes. Statuses are now compared by meaning
and quantities as numbers; a real change still registers.
"""

from types import SimpleNamespace

import pytest

from backend_app.backend.order_watchdog import OrderWatchdog
from backend_app.core.models.execution_record import ExecutionStatus


def _order(status=ExecutionStatus.EXECUTING, filled="0.0", remaining="0.0001"):
    return SimpleNamespace(status=status, filled_size=filled, remaining_size=remaining)


def _result(status="open", filled="0.0", remaining="0.0001"):
    return SimpleNamespace(status=status, filled_size=filled, remaining_size=remaining)


changed = OrderWatchdog._has_order_changed


@pytest.mark.parametrize("venue", ["open", "OPEN", "new", "NEW"])
def test_a_resting_order_matches_executing(venue):
    assert changed(None, _order(), _result(status=venue)) is False


def test_zero_spelt_differently_is_the_same_fill():
    assert changed(None, _order(filled="0.0"), _result(filled="0")) is False


@pytest.mark.parametrize(
    "result",
    [
        _result(filled="0.00005", remaining="0.00005", status="partially_filled"),
        _result(status="filled", filled="0.0001", remaining="0"),
        _result(status="canceled"),
        _result(status="expired"),
    ],
)
def test_a_real_change_still_registers(result):
    assert changed(None, _order(), result) is True


def test_a_completed_local_order_matches_a_filled_venue_order():
    assert changed(
        None,
        _order(status=ExecutionStatus.COMPLETED, filled="0.0001", remaining="0"),
        _result(status="filled", filled="0.0001", remaining="0.0"),
    ) is False
