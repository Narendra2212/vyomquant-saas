"""IMPORT-03 -- PositionEngine._record_trade's import actually resolves, and persists.

THE DEFECT
    ``backend/position_engine.py::PositionEngine._record_trade`` has always done
    ``from backend_app.core.models.trade_history import TradeCreate, TradeHistoryModel``,
    and that module never existed. Every call raised ``ModuleNotFoundError``, caught by
    the method's own ``except Exception: logger.error(...)`` -- so no trade has ever been
    persisted through this path, silently, since the method was written.

THE FIX
    ``core/models/trade_history.py`` (new) gives this a real ``TradeHistoryModel`` (plain
    SQLAlchemy ORM, matching ``PositionModel``'s own conventions) and ``TradeCreate``
    (Pydantic). Migration 040 gives the table real, append-only, service-only persistence.
    A failure now also increments ``TRADE_HISTORY_RECORD_FAILURES_TOTAL`` so a persistent
    failure of this kind (a schema drift, a reintroduced broken import, a full disk) is
    observable rather than silently swallowed forever, as the original bug was.

WHAT THIS FILE PROVES (SQLite-backed; no network, no real exchange)
    * ``_record_trade`` actually persists a row, readable back with the fields the call
      site already builds, for full_close, position_flip_close and position_flip_open;
    * a write failure increments the new counter AND is still swallowed (the position
      update itself must never fail over this), not merely logged where nobody is counting.

NOT SHOWN, ON PURPOSE: this being called from a live or paper trade. ``PositionEngine``
has no caller anywhere in the application today (confirmed by ``grep -rn
"PositionEngine(" backend_app/`` finding only its own file) -- see RT-16's ledger entry
for the matching gap on its reconciliation side. This fix makes the dead path's import
correct and its persistence real and tested; it does not wire the path up.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend_app.backend.position_engine import PositionEngine
from backend_app.core.models.trade_history import TradeHistoryModel
from backend_app.core.position_model import PositionSide

#: See test_d5_watchdog_adjudication_repair.py's own TENANT comment: an all-digit hex UUID
#: gets NUMERIC affinity on SQLite and comes back broken; a letter-bearing hex form keeps
#: TEXT affinity.
TENANT = uuid.UUID("11111111-1111-4111-8111-1111111111ab")


@pytest.fixture
def sqlite_db(tmp_path):
    url = f"sqlite:///{tmp_path / 'import03.db'}"
    engine = create_engine(url, future=True, poolclass=NullPool)
    TradeHistoryModel.__table__.create(bind=engine, checkfirst=True)
    maker = sessionmaker(bind=engine, future=True)
    session = maker()
    try:
        yield session, maker
    finally:
        session.close()
        engine.dispose()


def _position(**overrides):
    defaults = dict(
        position_id="pos-1", tenant_id=TENANT, strategy_id="strat-1",
        symbol="BTC/USDT", side=PositionSide.LONG, avg_entry_price="50000",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
class TestRecordTradePersistsARealRow:
    async def test_a_full_close_is_persisted_and_readable_from_a_second_session(self, sqlite_db):
        session, maker = sqlite_db
        engine = PositionEngine(session)

        await engine._record_trade(
            position=_position(), exit_price=Decimal("51000"), exit_size=Decimal("0.5"),
            realized_pnl=Decimal("500"), close_reason="full_close",
        )

        other = maker()
        try:
            row = other.query(TradeHistoryModel).one()
            assert row.tenant_id == TENANT
            assert row.strategy_id == "strat-1"
            assert row.symbol == "BTC/USDT"
            assert row.side == "long"
            assert row.entry_price == "50000"
            assert row.exit_price == "51000"
            assert row.size == "0.5"
            assert row.realized_pnl == "500"
            assert row.close_reason == "full_close"
        finally:
            other.close()

    async def test_a_position_flip_records_both_the_close_and_the_open(self, sqlite_db):
        session, maker = sqlite_db
        engine = PositionEngine(session)

        await engine._record_trade(
            position=_position(position_id="pos-closed"), exit_price=Decimal("51000"),
            exit_size=Decimal("1"), realized_pnl=Decimal("1000"),
            close_reason="position_flip_close",
        )
        await engine._record_trade(
            position=_position(position_id="pos-opened", side=PositionSide.SHORT),
            exit_price=Decimal("51000"), exit_size=Decimal("0.3"),
            realized_pnl=Decimal("0"), close_reason="position_flip_open",
        )

        reasons = {r.close_reason for r in session.query(TradeHistoryModel).all()}
        assert reasons == {"position_flip_close", "position_flip_open"}


@pytest.mark.asyncio
class TestARecordFailureIsCountedNotJustLogged:
    async def test_a_write_failure_increments_the_counter_and_is_still_swallowed(self, sqlite_db, monkeypatch):
        session, maker = sqlite_db
        engine = PositionEngine(session)

        monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db gone")))

        from backend_app.core.metrics import TRADE_HISTORY_RECORD_FAILURES_TOTAL

        before = TRADE_HISTORY_RECORD_FAILURES_TOTAL.labels(
            tenant_id=str(TENANT), close_reason="full_close"
        )._value.get()

        # Must not raise -- a position update must never fail over an audit write.
        await engine._record_trade(
            position=_position(), exit_price=Decimal("51000"), exit_size=Decimal("1"),
            realized_pnl=Decimal("0"), close_reason="full_close",
        )

        after = TRADE_HISTORY_RECORD_FAILURES_TOTAL.labels(
            tenant_id=str(TENANT), close_reason="full_close"
        )._value.get()
        assert after == before + 1
