"""RECON-FILL-01 -- a resting order that fills is progress, not a reconciliation mismatch.

Production, 2026-11-20 (Binance Testnet): every limit order that rested and then filled
was reported by the watchdog's reconciliation loop as local `executing` vs venue `closed`.
Each one wrote a `reconciliation_mismatches` row and sent a critical "Order Stuck Alert".

The fix: `_is_contradiction` separates forward progress (the venue moved the order on)
from a real contradiction (fill went backwards, an unrecorded cancel/reject/expiry,
"completed" with nothing filled, an unknown status). Only a contradiction is recorded and
alerted. The order is updated from the venue either way.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                       ExecutionStatus)
from backend_app.core.models.reconciliation import ReconciliationMismatchModel

TENANT = uuid.UUID("22222222-2222-4222-8222-2222222222ab")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rf01.db'}", future=True, poolclass=NullPool)
    ExecutionRecordModel.__table__.create(bind=engine, checkfirst=True)
    ReconciliationMismatchModel.__table__.create(bind=engine, checkfirst=True)
    s = sessionmaker(bind=engine, future=True)()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _seed(session, filled="0"):
    row = ExecutionRecordModel(
        execution_id="exec_rf01", tenant_id=TENANT, strategy_id="strat-rf01",
        symbol="BTC/USDT", side="buy", size="1", price="90000",
        status=ExecutionStatus.EXECUTING, order_id="venue-1", filled_size=filled,
        remaining_size="1",
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


class _Exec:
    def __init__(self, status, filled):
        self.status, self.filled = status, filled

    async def get_order_status(self, exchange_order_id, symbol):
        return SimpleNamespace(success=True, status=self.status, filled_size=self.filled,
                               remaining_size="0", avg_price="90000", error_message=None)


def _watchdog(session, ex):
    w = OrderWatchdog(session, WatchdogConfig())
    w._get_executor_for_order = AsyncMock(return_value=ex)
    w._trigger_alert = AsyncMock()
    return w


@pytest.mark.asyncio
@pytest.mark.parametrize("venue_status,filled", [
    ("closed", "1"),            # the production case: a resting order filled
    ("filled", "1"),
    ("partially_filled", "0.4"),
])
async def test_forward_progress_writes_no_mismatch_and_no_alert(session, venue_status, filled):
    _seed(session)
    w = _watchdog(session, _Exec(venue_status, filled))

    await w._reconcile_open_orders()

    assert session.query(ReconciliationMismatchModel).count() == 0
    w._trigger_alert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("venue_status,local_fill,venue_fill", [
    ("canceled", "0", "0"),     # cancelled at the venue, not recorded here
    ("rejected", "0", "0"),
    ("expired", "0", "0"),
    ("executing", "0.5", "0.2"),  # fill went backwards
    ("closed", "0", "0"),       # "completed" with nothing filled
    ("mystery", "0", "0"),      # a status this file does not know
])
async def test_a_contradiction_is_still_recorded_and_alerted(
        session, venue_status, local_fill, venue_fill):
    _seed(session, filled=local_fill)
    w = _watchdog(session, _Exec(venue_status, venue_fill))

    await w._reconcile_open_orders()

    assert session.query(ReconciliationMismatchModel).count() == 1
    w._trigger_alert.assert_awaited_once()
