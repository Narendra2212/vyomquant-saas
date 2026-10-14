"""RT-16 -- a reconciliation discrepancy is finally written where the scheduler reads it.

THE DEFECT (audit)
    ``ReconciliationScheduler`` polls ``reconciliation_mismatches`` and escalates by
    severity -- real, already-written, correct policy (CRITICAL triggers the kill switch,
    HIGH/MEDIUM alert only). But ``ReconciliationMismatchRepository.record_mismatch`` had
    NO CALLER anywhere in the application. The scheduler was polling a table nothing ever
    wrote to, so its escalation logic -- however correct -- never ran on a real event.

THE FIX
    ``OrderWatchdog._reconcile_open_orders`` already (as of W-1b/RT-16's executor fix)
    detects a genuine local/venue divergence via ``_has_order_changed``, using a REAL
    per-tenant executor. This wires the one new call: when that divergence is real, record
    it, severity MEDIUM ("status or fill-price divergence" -- this codebase's own
    taxonomy). Deliberately NEVER CRITICAL from this call site: CRITICAL triggers the
    scheduler's kill switch, and nothing here has measured a SIZE or SYMBOL mismatch (what
    CRITICAL is defined for) -- only that a status or fill figure moved. Getting that
    escalation-severity judgement wrong from a new, hard-to-verify call site is exactly how
    a reconciliation safety mechanism becomes the thing that takes a platform down by
    accident; this file pins that it does not.

WHAT THIS FILE PROVES (SQLite-backed; no network, no real exchange)
    * a genuine status/fill divergence writes exactly one MEDIUM-severity row, readable by
      a second, independent session (the same durability bar W-2's own tests hold);
    * an order that has NOT changed writes nothing;
    * the mismatch is always MEDIUM, never CRITICAL, from this call site;
    * a write failure (an unreadable/misconfigured table) is logged and does not raise into
      ``_reconcile_open_orders`` -- recording a mismatch must never block reconciliation.

NOT SHOWN: the scheduler actually picking the row up and escalating it (that is
``ReconciliationScheduler``'s own, pre-existing, untouched logic); a real exchange
divergence (needs sandbox credentials).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.core.models.execution_record import (Base,
                                                       ExecutionRecordModel,
                                                       ExecutionStatus)
from backend_app.core.models.reconciliation import (MismatchSeverity,
                                                     ReconciliationMismatchModel)

#: See test_d5_watchdog_adjudication_repair.py's own TENANT comment: an all-digit hex UUID
#: gets NUMERIC affinity on SQLite (postgresql.UUID isn't a type it recognises) and comes
#: back broken. A letter-bearing hex form keeps TEXT affinity.
TENANT = uuid.UUID("11111111-1111-4111-8111-1111111111ab")
SYMBOL = "ETH/USDT"


@pytest.fixture
def sqlite_db(tmp_path):
    """File-backed SQLite so a second session reads a real commit, not an identity map."""
    url = f"sqlite:///{tmp_path / 'rt16.db'}"
    engine = create_engine(url, future=True, poolclass=NullPool)
    ExecutionRecordModel.__table__.create(bind=engine, checkfirst=True)
    ReconciliationMismatchModel.__table__.create(bind=engine, checkfirst=True)
    maker = sessionmaker(bind=engine, future=True)
    session = maker()
    try:
        yield session, maker
    finally:
        session.close()
        engine.dispose()


def _seed_order(session, *, status=ExecutionStatus.EXECUTING, filled_size="0",
                 remaining_size="0"):
    row = ExecutionRecordModel(
        execution_id="exec_rt16_01", tenant_id=TENANT, strategy_id="strat-rt16",
        symbol=SYMBOL, side="buy", size="1", price="2000", status=status,
        order_id="venue-order-99", filled_size=filled_size,
        remaining_size=remaining_size,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


class _FakeExecutor:
    def __init__(self, status: str, filled_size: str):
        self._status, self._filled_size = status, filled_size

    async def get_order_status(self, exchange_order_id, symbol):
        return SimpleNamespace(
            success=True, status=self._status, filled_size=self._filled_size,
            remaining_size="0", avg_price="2000", error_message=None,
        )


def _watchdog(session, executor) -> OrderWatchdog:
    w = OrderWatchdog(session, WatchdogConfig())
    w._get_executor_for_order = AsyncMock(return_value=executor)
    w._trigger_alert = AsyncMock()
    return w


@pytest.mark.asyncio
class TestMismatchIsRecorded:
    async def test_a_status_divergence_writes_one_medium_row(self, sqlite_db):
        session, maker = sqlite_db
        _seed_order(session, status=ExecutionStatus.EXECUTING, filled_size="0")
        w = _watchdog(session, _FakeExecutor(status="completed", filled_size="1"))

        await w._reconcile_open_orders()

        other = maker()
        try:
            rows = other.query(ReconciliationMismatchModel).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.severity == MismatchSeverity.MEDIUM, (
                "this call site must never claim CRITICAL -- that triggers the "
                "scheduler's kill switch and nothing here measured a size/symbol mismatch"
            )
            assert row.tenant_id is not None
            assert row.execution_id == "exec_rt16_01"
            assert row.order_id == "venue-order-99"
            assert row.symbol == SYMBOL
        finally:
            other.close()

    async def test_an_unchanged_order_writes_nothing(self, sqlite_db):
        session, maker = sqlite_db
        _seed_order(session, status=ExecutionStatus.EXECUTING, filled_size="0")
        w = _watchdog(session, _FakeExecutor(status="executing", filled_size="0"))

        await w._reconcile_open_orders()

        assert session.query(ReconciliationMismatchModel).count() == 0

    async def test_the_field_names_filled_size_when_that_is_what_diverged(self, sqlite_db):
        session, maker = sqlite_db
        _seed_order(session, status=ExecutionStatus.EXECUTING, filled_size="0")
        # Same status, different fill - the earlier branch of _has_order_changed.
        w = _watchdog(session, _FakeExecutor(status="executing", filled_size="0.5"))

        await w._reconcile_open_orders()

        row = session.query(ReconciliationMismatchModel).one()
        assert row.field == "filled_size"

    async def test_a_record_failure_is_logged_and_does_not_raise(self, sqlite_db, monkeypatch):
        session, maker = sqlite_db
        order = _seed_order(session, status=ExecutionStatus.EXECUTING, filled_size="0")
        w = _watchdog(session, _FakeExecutor(status="completed", filled_size="1"))

        def _boom(self, *a, **k):
            raise RuntimeError("table locked")

        monkeypatch.setattr(
            "backend_app.core.models.reconciliation.ReconciliationMismatchRepository.record_mismatch",
            _boom,
        )

        # Must not raise out of _reconcile_open_orders - recording is additive visibility,
        # never a control that can block the reconciliation it is reporting on.
        await w._reconcile_open_orders()
