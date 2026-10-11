"""A fee ledger total must not add amounts denominated in different assets.

PHASE 10 / WORKSTREAM D / observation 1, at the AGGREGATE level.

WHAT WAS FOUND
--------------
`FeeEngine.record_fee` was fixed so that a third-asset fee (a BNB fee on a BTC/USDT trade)
records its true amount and asset and reports `fee_pct` / `net_pnl` as ABSENT rather than
pretending 0.0005 BNB is 0.0005 USDT. `FeeEngine.get_fee_summary` still made exactly that
mistake one level up::

    for fee in fees:
        fee_amount = Decimal(fee.fee_amount)      # denominated in fee.fee_asset
        totals[fee.fee_type] += fee_amount        # ...added regardless of asset
        total_volume += Decimal(fee.trade_volume) # quote currency
    total_fees   = sum(totals.values())
    avg_fee_rate = total_fees / total_volume * 100

So a 30 USDT taker fee and a 0.0005 BNB taker fee totalled `30.0005` — a number in no
currency at all — and `avg_fee_rate_pct` then divided that by a quote-denominated volume.
The row-level fix made each record honest; the summary re-introduced the same category
error in the figure an operator would actually read.

Second defect in the same function: `totals` was seeded with four of `FeeType`'s SIX
members (MAKER, TAKER, FUNDING, LIQUIDATION), so `totals[fee.fee_type] += ...` raised
`KeyError` on a WITHDRAWAL or DEPOSIT fee and took the whole summary down — a recorded fee
made the ledger unreadable.

WHAT THE FIX DOES
-----------------
Groups by `fee_asset`. When every fee shares one asset the totals are unchanged, which is
the ordinary case and the one `test_tenant02_fee_records_rls_postgres.py` pins. When more
than one asset is present, `total_fees` and `avg_fee_rate_pct` are reported as **absent**
and the per-asset breakdown carries the figures — the same convention `record_fee` already
follows for an unconvertible fee. `totals` now covers every `FeeType`.

Gated on AERORA_TEST_DATABASE_URL. No exchange is contacted.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="needs a real PostgreSQL instance (AERORA_TEST_DATABASE_URL).",
)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# `FeeRecordModel` carries FKs to execution_records and positions; both owning models must
# be in the registry before a mapper configures.
import backend_app.core.models.execution_record  # noqa: E402,F401
import backend_app.core.position_model  # noqa: E402,F401
from backend_app.backend.fee_engine import FeeEngine, FeeType  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db():
    url = TEST_DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url, future=True)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        # Everything this file writes is discarded.
        session.rollback()
        session.close()
        engine.dispose()


@pytest.fixture
def seeded(db):
    """A tenant and an execution record to hang fees off, inside the rolled-back tx."""
    tenant = uuid.uuid4()
    execution_id = f"exec_p10dsum_{uuid.uuid4().hex[:10]}"
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (:t, :e) "
             "ON CONFLICT (id) DO NOTHING"),
        {"t": str(tenant), "e": f"p10dsum-{str(tenant)[:8]}@example.invalid"},
    )
    db.execute(
        text("INSERT INTO public.profiles (id) VALUES (:t) ON CONFLICT (id) DO NOTHING"),
        {"t": str(tenant)},
    )
    db.execute(
        text("INSERT INTO public.execution_records "
             "(execution_id, tenant_id, strategy_id, symbol, side, size, status, "
             " created_at, updated_at) "
             "VALUES (:e, :t, 'p10d-summary', 'BTC/USDT', 'buy', '1', "
             "        'EXECUTING'::executionstatus, now(), now())"),
        {"e": execution_id, "t": str(tenant)},
    )
    db.flush()
    return tenant, execution_id


def _fee(db, execution_id, *, asset, amount, order_id, fee_type=FeeType.TAKER):
    return _run(FeeEngine(db).record_fee(
        execution_id=execution_id,
        order_id=order_id,
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("1"),
        price=Decimal("60000"),
        fee_amount=amount,
        fee_asset=asset,
        fee_type=fee_type,
        exchange_id="binance",
        auto_commit=False,          # flush only — the fixture rolls back
    ))


# ═════════════════════════════════════════════════════════════════════════════
#  1. Control — a single-asset ledger is unchanged
# ═════════════════════════════════════════════════════════════════════════════

def test_a_single_asset_ledger_totals_exactly_as_before(db, seeded):
    """Preserves the contract `test_tenant02_fee_records_rls_postgres.py` asserts.

    Two USDT fees, one asset, so the total IS expressible and must be the plain sum.
    """
    tenant, execution_id = seeded
    _fee(db, execution_id, asset="USDT", amount=Decimal("30"), order_id="o1")
    _fee(db, execution_id, asset="USDT", amount=Decimal("12.5"), order_id="o2")

    summary = FeeEngine(db).get_fee_summary(tenant)

    assert summary["count"] == 2
    assert Decimal(summary["total_fees"]) == Decimal("42.5"), (
        "a single-asset ledger must still total normally"
    )
    assert summary["avg_fee_rate_pct"] is not None, (
        "a single-asset ledger has a meaningful average rate"
    )
    assert summary["fee_assets"] == ["USDT"]


# ═════════════════════════════════════════════════════════════════════════════
#  2. THE DEFECT — a mixed-denomination total is not a number
# ═════════════════════════════════════════════════════════════════════════════

def test_fees_in_different_assets_are_not_added_together(db, seeded):
    """THE DEFECT. 30 USDT + 0.0005 BNB must not be reported as 30.0005."""
    tenant, execution_id = seeded
    _fee(db, execution_id, asset="USDT", amount=Decimal("30"), order_id="o1")
    _fee(db, execution_id, asset="BNB", amount=Decimal("0.0005"), order_id="o2")

    summary = FeeEngine(db).get_fee_summary(tenant)

    assert summary["count"] == 2, "both fees must still be counted"
    assert sorted(summary["fee_assets"]) == ["BNB", "USDT"]

    assert summary["total_fees"] is None, (
        f"total_fees came back {summary['total_fees']!r} for a ledger holding both USDT "
        f"and BNB fees. A sum across denominations is a number in no currency; it must be "
        f"reported as absent, exactly as record_fee reports fee_pct for the same reason."
    )
    assert summary["avg_fee_rate_pct"] is None, (
        "an average rate derived from a mixed-currency numerator is meaningless"
    )


def test_the_per_asset_breakdown_keeps_every_fee_visible(db, seeded):
    """Absent is not the same as lost. Each fee must still be readable, in its own unit.

    This is the reconciliation property: the ledger total may be inexpressible, but no fee
    may disappear from the report.
    """
    tenant, execution_id = seeded
    _fee(db, execution_id, asset="USDT", amount=Decimal("30"), order_id="o1")
    _fee(db, execution_id, asset="BNB", amount=Decimal("0.0005"), order_id="o2")
    _fee(db, execution_id, asset="BNB", amount=Decimal("0.0007"), order_id="o3")

    summary = FeeEngine(db).get_fee_summary(tenant)
    by_asset = summary["totals_by_asset"]

    assert Decimal(by_asset["USDT"]) == Decimal("30")
    assert Decimal(by_asset["BNB"]) == Decimal("0.0012"), (
        "fees sharing an asset must still be summed within that asset"
    )
    assert summary["count"] == 3


# ═════════════════════════════════════════════════════════════════════════════
#  3. THE DEFECT — a recorded fee must not make the ledger unreadable
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("fee_type", [FeeType.WITHDRAWAL, FeeType.DEPOSIT])
def test_every_fee_type_can_be_summarised(db, seeded, fee_type):
    """THE DEFECT. `totals` held four of six FeeType members, so these raised KeyError."""
    tenant, execution_id = seeded
    _fee(db, execution_id, asset="USDT", amount=Decimal("1.25"),
         order_id=f"o-{fee_type.value}", fee_type=fee_type)

    summary = FeeEngine(db).get_fee_summary(tenant)

    assert summary["count"] == 1
    assert Decimal(summary["by_type"][fee_type.value]) == Decimal("1.25"), (
        f"a {fee_type.value} fee was recorded but is missing from the summary's by_type "
        f"breakdown"
    )
    assert Decimal(summary["total_fees"]) == Decimal("1.25")


def test_by_type_covers_the_whole_fee_type_enum(db, seeded):
    """So a future FeeType member cannot silently fall out of the ledger again."""
    tenant, _execution_id = seeded
    summary = FeeEngine(db).get_fee_summary(tenant)

    assert set(summary["by_type"]) == {ft.value for ft in FeeType}, (
        "the summary's by_type keys no longer match FeeType; a fee of a missing type "
        "would be dropped from or crash the ledger report"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  4. An empty ledger is still answerable
# ═════════════════════════════════════════════════════════════════════════════

def test_an_empty_ledger_reports_zero_rather_than_absent(db, seeded):
    """No fees is a known quantity — zero — not an inexpressible one."""
    tenant, _execution_id = seeded
    summary = FeeEngine(db).get_fee_summary(tenant)

    assert summary["count"] == 0
    assert Decimal(summary["total_fees"]) == Decimal("0")
    assert summary["fee_assets"] == []


# ═════════════════════════════════════════════════════════════════════════════
#  5. Several fills on one execution in the same second
# ═════════════════════════════════════════════════════════════════════════════

def test_multiple_fees_on_one_execution_in_the_same_second_are_all_recorded(db, seeded):
    """THE DEFECT, found while writing the tests above.

    `fee_id` was `fee_{execution_id}_{int(utcnow().timestamp())}` — whole-second
    resolution, no fill component. Two fees against one execution inside the same second
    therefore shared a primary key, and the second died on `fee_records_pkey` with a
    UniqueViolation no caller catches: the fee was LOST and the transaction was left
    aborted, so any later write in it failed too.

    One execution has as many fees as it has fills, and a market order sweeping a liquid
    book fills several times in well under a second. No `sleep` here — recording three
    fees back to back is the whole point.
    """
    tenant, execution_id = seeded

    recorded = [
        _fee(db, execution_id, asset="USDT", amount=Decimal("1.00"), order_id="fill-1"),
        _fee(db, execution_id, asset="USDT", amount=Decimal("2.00"), order_id="fill-2"),
        _fee(db, execution_id, asset="USDT", amount=Decimal("3.00"), order_id="fill-3"),
    ]

    assert len({r.fee_id for r in recorded}) == 3, (
        f"three fees on one execution produced {len({r.fee_id for r in recorded})} "
        f"distinct fee_ids: {[r.fee_id for r in recorded]}"
    )

    rows = db.execute(
        text("SELECT fee_amount FROM public.fee_records WHERE execution_id = :e"),
        {"e": execution_id},
    ).fetchall()
    assert len(rows) == 3, f"expected three persisted fee rows, found {len(rows)}"

    # The reconciliation property: the ledger total equals the fees actually charged.
    summary = FeeEngine(db).get_fee_summary(tenant)
    assert summary["count"] == 3
    assert Decimal(summary["total_fees"]) == Decimal("6.00"), (
        "the summarised total does not equal the sum of the fees charged"
    )


def test_a_funding_fee_keeps_its_deterministic_idempotency_key(db, seeded):
    """The counterpart. `record_funding_fee` MUST still collide on a replay.

    There is exactly one funding fee per position per period, so for that call the primary
    key IS the idempotency key — the fix above must not have made it unique per call.
    """
    import inspect as _inspect

    from backend_app.backend.fee_engine import FeeEngine as _FE

    src = _inspect.getsource(_FE.record_funding_fee)
    assert 'fee_funding_{position_id}_{funding_period}' in src, (
        "record_funding_fee's fee_id is no longer the deterministic "
        "(position, period) pair, so a replayed funding fee would be double-counted "
        "instead of refused by the primary key"
    )
    assert "uuid4" not in src, (
        "record_funding_fee's id now carries a random component, which defeats its "
        "per-period idempotency"
    )
