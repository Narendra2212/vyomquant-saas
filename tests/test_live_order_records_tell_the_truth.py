"""
tests/test_live_order_records_tell_the_truth.py

Found on the first real Binance-testnet order (order 7137381, BTC/USDC buy 0.0001):

SIG-DUP-01  An execution the engine skipped as a duplicate (``skipped_completed``: same
            5-minute bucket, side, qty and price as one already run) was recorded on its
            signal as FILLED with ``filled: 0`` - four phantom fills while the exchange held
            one OPEN, unfilled order and the bot's position stayed 0.
SIG-VENUE-01 The signal never carried the exchange's order id (always ``None``), so the
            live order could not be traced from Signal Trace.
REC-TZ-01   ``ReconciliationScheduler._needs_re_escalation`` subtracted a naive
            ``utcnow()`` from a ``timestamptz`` value and raised TypeError on every poll once
            a real mismatch existed (Sentry VYOMQUANT-API-PRODUCTION-S).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend_app.backend.master_executor import venue_order_id_of

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("result, expected", [
    ({"status": "accepted", "result": {"trade_result": {"order_id": 7137381}}}, "7137381"),
    ({"status": "completed", "result": {"trade_result": {"order_id": "abc"}}}, "abc"),
    ({"status": "failed", "result": {"error": "x"}}, None),
    ({"status": "skipped_completed"}, None),
    (None, None),
    ("not a dict", None),
])
def test_venue_order_id_of(result, expected):
    assert venue_order_id_of(result) == expected


def test_a_skipped_duplicate_is_never_recorded_as_filled():
    """The FILLED branch tests `== "completed"` only; any `skipped_*` maps to CANCELLED."""
    src = (REPO / "backend_app" / "backend" / "master_executor.py").read_text()
    block = src[src.index('if v_status == "completed":'):]
    block = block[:block.index("requested_qty = ")]
    filled_branch, rest = block.split('order_st = "FILLED"', 1)
    assert "skipped" not in filled_branch
    assert 'str(v_status).startswith("skipped")' in rest
    assert rest.index('startswith("skipped")') < rest.index('order_st = "CANCELLED"')


def test_the_signal_carries_the_venue_order_id():
    src = (REPO / "backend_app" / "backend" / "master_executor.py").read_text()
    assert "exchange_order_id=venue_order_id_of(v_result)" in src
    assert "exchange_order_id=None," not in src


@pytest.mark.parametrize("last", [
    datetime.now(timezone.utc) - timedelta(hours=2),               # timestamptz read back
    (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None),   # naive write
])
def test_re_escalation_handles_aware_and_naive_timestamps(last):
    from backend_app.core.reconciliation_scheduler import ReconciliationScheduler

    sched = ReconciliationScheduler.__new__(ReconciliationScheduler)
    mismatch = SimpleNamespace(escalation_count=1, last_escalated_at=last)
    assert sched._needs_re_escalation(mismatch) is True


def test_a_recent_escalation_is_not_repeated():
    from backend_app.core.reconciliation_scheduler import ReconciliationScheduler

    sched = ReconciliationScheduler.__new__(ReconciliationScheduler)
    mismatch = SimpleNamespace(escalation_count=1, last_escalated_at=datetime.now(timezone.utc))
    assert sched._needs_re_escalation(mismatch) is False
