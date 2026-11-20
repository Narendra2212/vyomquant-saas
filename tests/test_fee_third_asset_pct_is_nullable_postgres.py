"""Phase 10, observation A: a third-asset fee is RECORDED, with fee_pct NULL, not lost.

THE DEFECT
----------
`fee_records.fee_pct` was `character varying NOT NULL`. `FeeEngine.record_fee` correctly
computes `fee_pct = None` for a fee denominated in a THIRD asset (e.g. a BNB fee on a
BTC/USDT trade -- the Binance default), because no conversion rate is available to express
it as a fraction of trade volume. Committing that record raised a NOT NULL violation, so
the fee -- real, charged by the venue, known exactly as amount + asset -- was LOST.

THE FIX
-------
`FeeRecordModel.fee_pct` is now `nullable=True` (the authoritative shape for fresh,
create_all()-managed databases) and migration `031_fee_records_fee_pct_nullable.sql` drops
the NOT NULL on already-provisioned databases. `fee_amount` and `fee_asset` STAY NOT NULL,
so the money actually charged is never lost; only the derived, non-computable percentage is
absent. NULL rather than 0 (which would falsely state a free trade) or a fabricated rate
(forbidden).

WHAT THIS PROVES
----------------
Drives the REAL `FeeEngine.record_fee` against real PostgreSQL:
  * a third-asset fee (BNB on BTC/USDT) records with fee_pct NULL, amount and asset exact;
  * a quote-asset fee (USDT on BTC/USDT) records with fee_pct COMPUTED, so the fix did not
    turn the percentage off for the cases that can have one;
  * net_pnl is absent for the third-asset fee (it cannot be netted without a rate) and
    present for the quote-asset fee;
  * repeated processing of the same execution produces an additional, DISTINCT fee row
    rather than mutating or duplicating one in place -- i.e. record_fee is append-only and
    its fee_id is unique per call (characterised, so a change is visible);
  * FEE-01 still holds: a fee for an execution with no record is REFUSED, not attributed to
    a sentinel tenant.

No live order and no venue: `record_fee` is pure accounting over a SQLAlchemy session.
Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL instance (AERORA_TEST_DATABASE_URL).",
)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend_app.backend.fee_engine import (  # noqa: E402
    FeeEngine,
    FeeType,
    TenantResolutionError,
)

# FeeRecordModel carries foreign keys to `positions.position_id` and
# `execution_records.execution_id`. SQLAlchemy resolves those at flush time against the
# SHARED Base.metadata, so the model modules that DECLARE those tables must be imported, or
# the ORM INSERT raises NoReferencedTableError (the real application imports all models
# together at startup). Imported for their registration side effect only.
import backend_app.core.position_model  # noqa: E402,F401
import backend_app.core.models.execution_record  # noqa: E402,F401


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def db():
    engine = create_engine(TEST_DATABASE_URL, future=True, poolclass=NullPool)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def seeded_execution(db):
    """A tenant + one execution_records row, so record_fee can resolve the tenant (FEE-01)."""
    tenant_id = str(uuid.uuid4())
    execution_id = f"exec_fee_{uuid.uuid4().hex[:12]}"
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"p10-fee-{tenant_id[:8]}@example.invalid"},
    )
    db.execute(
        text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"p10-fee-{tenant_id[:8]}@example.invalid"},
    )
    created = datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO public.execution_records "
            "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
            " created_at, updated_at) "
            "VALUES (:e, CAST(:t AS uuid), 'strat_fee', 'BTC/USDT', 'buy', '0.1', "
            "        '61234.50', CAST('EXECUTING' AS executionstatus), :c, :c)"
        ),
        {"e": execution_id, "t": tenant_id, "c": created},
    )
    db.commit()

    yield tenant_id, execution_id

    db.rollback()
    db.execute(text("DELETE FROM public.fee_records WHERE tenant_id = CAST(:t AS uuid)"),
               {"t": tenant_id})
    db.execute(
        text("DELETE FROM public.execution_records WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    db.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
               {"t": tenant_id})
    db.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"), {"t": tenant_id})
    db.commit()


def test_a_third_asset_fee_is_recorded_with_fee_pct_null(db, seeded_execution):
    """BNB fee on BTC/USDT: amount and asset exact, fee_pct NULL, net_pnl absent."""
    _tenant, execution_id = seeded_execution
    engine = FeeEngine(db)

    rec = _run(engine.record_fee(
        execution_id=execution_id,
        order_id="ord-third-asset",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.1"),
        price=Decimal("61234.50"),
        fee_amount=Decimal("0.0005"),
        fee_asset="BNB",                 # neither base (BTC) nor quote (USDT)
        fee_type=FeeType.TAKER,
        gross_pnl=Decimal("100"),
        auto_commit=True,
    ))

    assert rec.fee_amount == "0.0005", "the fee amount actually charged must be preserved"
    assert rec.fee_asset == "BNB", "the fee asset must be preserved"
    assert rec.fee_pct is None, (
        "a third-asset fee has no computable percentage; it must be NULL, not 0 or a guess"
    )
    assert rec.net_pnl is None, (
        "net_pnl cannot be computed from an unconverted third-asset fee and must be absent"
    )

    # It is readable back from the database with fee_pct NULL -- i.e. the commit succeeded,
    # which is the whole point: before the fix this raised a NOT NULL violation.
    row = db.execute(
        text("SELECT fee_amount, fee_asset, fee_pct FROM public.fee_records "
             "WHERE fee_id = :f"),
        {"f": rec.fee_id},
    ).fetchone()
    assert row is not None, "the third-asset fee record was not persisted"
    assert row[2] is None, "the persisted fee_pct must be NULL for a third-asset fee"


def test_a_quote_asset_fee_still_computes_a_percentage(db, seeded_execution):
    """Control: the fix did not turn percentages off where they ARE computable.

    A USDT fee on BTC/USDT is already quote-denominated, so fee_pct = fee / trade_volume is
    well-defined and must be recorded.
    """
    _tenant, execution_id = seeded_execution
    engine = FeeEngine(db)

    rec = _run(engine.record_fee(
        execution_id=execution_id,
        order_id="ord-quote-asset",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.1"),
        price=Decimal("61234.50"),
        fee_amount=Decimal("3.0617"),   # ~0.05% of 6123.45 notional
        fee_asset="USDT",               # the quote asset
        fee_type=FeeType.TAKER,
        gross_pnl=Decimal("100"),
        auto_commit=True,
    ))

    assert rec.fee_asset == "USDT"
    assert rec.fee_pct is not None, "a quote-asset fee has a computable percentage"
    assert Decimal(rec.fee_pct) > 0
    assert rec.net_pnl is not None, "net_pnl is computable when the fee is quote-denominated"


def test_repeated_processing_appends_a_distinct_row_not_a_duplicate_or_mutation(
    db, seeded_execution
):
    """record_fee is append-only; two calls for one execution make two DISTINCT rows.

    Characterised rather than asserted as idempotent, because record_fee's fee_id embeds a
    unix-second timestamp and is not an idempotency key -- so this records the ACTUAL
    behaviour (each call is a new row with its own fee_id) so that a future change toward
    real idempotency is a visible, deliberate contract change rather than a silent one.
    """
    _tenant, execution_id = seeded_execution
    engine = FeeEngine(db)

    kwargs = dict(
        order_id="ord-rep",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.1"),
        price=Decimal("61234.50"),
        fee_amount=Decimal("0.0005"),
        fee_asset="BNB",
        fee_type=FeeType.TAKER,
        auto_commit=True,
    )
    r1 = _run(engine.record_fee(execution_id=execution_id, **kwargs))
    # Force a distinct fee_id (it embeds int(timestamp)); a 2nd within the same second
    # would collide on the primary key, which is itself a known limitation, not this test's
    # subject -- so advance past the second boundary deterministically.
    import time
    time.sleep(1.1)
    r2 = _run(engine.record_fee(execution_id=execution_id, **kwargs))

    assert r1.fee_id != r2.fee_id, "two record_fee calls must not share a primary key"
    rows = db.execute(
        text("SELECT fee_id, fee_pct FROM public.fee_records WHERE execution_id = :e"),
        {"e": execution_id},
    ).fetchall()
    assert len(rows) == 2, f"expected two distinct fee rows, found {len(rows)}"
    assert all(r[1] is None for r in rows), "both third-asset rows must carry fee_pct NULL"


def test_a_fee_for_an_unknown_execution_is_refused_not_sentinel_attributed(db):
    """FEE-01 still holds alongside the fee_pct fix: no execution record -> refuse."""
    engine = FeeEngine(db)
    with pytest.raises(TenantResolutionError):
        _run(engine.record_fee(
            execution_id=f"exec_absent_{uuid.uuid4().hex[:8]}",
            order_id="ord-absent",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.1"),
            price=Decimal("61234.50"),
            fee_amount=Decimal("0.0005"),
            fee_asset="BNB",
            auto_commit=True,
        ))
