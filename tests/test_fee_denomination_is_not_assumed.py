"""A fee must be converted to quote currency, or reported as unconverted — never assumed.

WHAT THIS GUARDS
----------------
``FeeEngine.record_fee`` decided a fee's quote-currency cost with two branches:

    if fee_asset.upper() == base_asset.upper():
        fee_cost_in_quote = fee_amount * price
    else:
        # Fee paid in quote asset (e.g. USDT) or third asset
        fee_cost_in_quote = fee_amount

The comment on that ``else`` names the defect: it lumps together two cases that are not
alike.

* **Quote asset** — the fee is already in quote terms. 1:1 is correct.
* **Third asset** — the fee is in a currency nobody converted, and 1:1 is a category
  error. ``0.0005 BNB`` was recorded as ``$0.0005``.

A BNB-denominated fee on a BTC/USDT trade is the *common* case, not an exotic one: paying
fees in BNB is the Binance default and is incentivised with a 25% discount. At roughly
$600/BNB the fee was understated by about three orders of magnitude, and so was every
``net_pnl`` derived from it — a net PnL that silently equals the gross.

``record_fee`` has no market-data handle, so no rate is available for a third asset. This
module's surrounding convention is that an unmeasured figure is not replaced with a
plausible one, so the third-asset case now records the true amount and true denomination
and reports ``fee_pct`` / ``net_pnl`` as **absent**. "Not computed" is recoverable; a PnL
wrong by 1000x is not.

Second defect covered here: ``str(x) if x else None`` persisted a ``Decimal('0')`` as
NULL, because zero is falsy — making a break-even trade indistinguishable from one whose
PnL was never computed.
"""

import asyncio
from decimal import Decimal

import pytest

from backend_app.backend.fee_engine import FeeEngine, FeeType


class _FakeSession:
    """Captures the record instead of persisting it. No database involved."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def flush(self):
        pass

    def query(self, *a, **k):
        raise AssertionError("record_fee should not query during these tests")


@pytest.fixture
def engine(monkeypatch):
    eng = FeeEngine(_FakeSession())
    # Tenant lookup reads execution_records; irrelevant to the DENOMINATION property this
    # file tests, and would need a DB.
    #
    # FEE-01: this used to patch the lookup to `UUID(int=0)`, mirroring the fallback the
    # product itself used. That fallback is now a `TenantResolutionError`, so the double
    # returns a VALID tenant instead -- the point of this file is the fee math, and a
    # resolved tenant is the ordinary precondition for reaching it. The refusal behaviour
    # is covered by tests/test_fee01_tenant_is_validated_not_sentinel_postgres.py.
    import uuid as _uuid

    _tenant = _uuid.uuid4()
    monkeypatch.setattr(
        FeeEngine, "_get_tenant_id_from_execution", lambda self, _eid: _tenant
    )
    return eng


def _record(engine, **kw):
    args = dict(
        execution_id="exec_abc",
        order_id="order_1",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("1"),
        price=Decimal("60000"),
        fee_amount=Decimal("0.0005"),
        fee_asset="USDT",
        fee_type=FeeType.TAKER,
    )
    args.update(kw)
    asyncio.run(engine.record_fee(**args))
    return engine.db.added[-1]


# ─────────────────────────────────────────────────────────────────────────────
# The three denominations
# ─────────────────────────────────────────────────────────────────────────────

def test_a_quote_denominated_fee_is_taken_at_face_value(engine):
    """A USDT fee on BTC/USDT is already in quote terms."""
    rec = _record(engine, fee_asset="USDT", fee_amount=Decimal("30"),
                  gross_pnl=Decimal("100"))

    assert rec.net_pnl == str(Decimal("70.00000000"))


def test_a_base_denominated_fee_is_converted_by_the_trade_price(engine):
    """A BTC fee on BTC/USDT converts at the trade's own price."""
    rec = _record(engine, fee_asset="BTC", fee_amount=Decimal("0.0005"),
                  price=Decimal("60000"), gross_pnl=Decimal("100"))

    # 0.0005 BTC * 60000 = 30 USDT
    assert rec.net_pnl == str(Decimal("70.00000000"))


def test_a_third_asset_fee_is_not_silently_counted_as_quote_currency(engine):
    """THE DEFECT. A BNB fee on BTC/USDT must not be treated as 0.0005 USDT."""
    rec = _record(engine, fee_asset="BNB", fee_amount=Decimal("0.0005"),
                  gross_pnl=Decimal("100"))

    assert rec.net_pnl is None, (
        "a BNB fee was deducted from PnL as though it were quote currency; net_pnl "
        f"came back {rec.net_pnl!r}, which understates the fee by ~1000x"
    )
    assert rec.fee_pct is None, (
        "fee_pct was computed as a ratio of two different currencies"
    )


def test_a_third_asset_fee_still_records_its_true_amount_and_denomination(engine):
    """Refusing to convert must not mean losing the fee. The amount is a real
    measurement; only the conversion is missing."""
    rec = _record(engine, fee_asset="BNB", fee_amount=Decimal("0.0005"))

    assert rec.fee_amount == str(Decimal("0.0005"))
    assert rec.fee_asset == "BNB"


def test_the_third_asset_case_is_not_reachable_for_the_quote_asset(engine):
    """Guards against a fix that refuses too much: the quote case must still convert."""
    for quote_spelling in ("USDT", "usdt", "UsDt"):
        rec = _record(engine, fee_asset=quote_spelling, fee_amount=Decimal("10"),
                      gross_pnl=Decimal("50"))
        assert rec.net_pnl == str(Decimal("40.00000000")), (
            f"a fee spelled {quote_spelling!r} was not recognised as quote-denominated"
        )


def test_an_unseparated_symbol_still_resolves_its_quote_asset(engine):
    """``BTCUSDT`` must resolve the same way as ``BTC/USDT``, or every fee on an
    unseparated symbol falls into the third-asset branch."""
    rec = _record(engine, symbol="BTCUSDT", fee_asset="USDT",
                  fee_amount=Decimal("10"), gross_pnl=Decimal("50"))

    assert rec.net_pnl == str(Decimal("40.00000000"))


# ─────────────────────────────────────────────────────────────────────────────
# Zero is a measurement
# ─────────────────────────────────────────────────────────────────────────────

def test_a_gross_pnl_of_exactly_zero_is_not_stored_as_null(engine):
    """``str(x) if x else None`` on ``Decimal('0')`` stored NULL, which is
    indistinguishable from "never computed"."""
    rec = _record(engine, fee_asset="USDT", fee_amount=Decimal("0"),
                  gross_pnl=Decimal("0"))

    assert rec.gross_pnl is not None, "a break-even trade's gross PnL was stored as NULL"
    assert Decimal(rec.gross_pnl) == Decimal("0")


def test_a_net_pnl_of_exactly_zero_is_not_stored_as_null(engine):
    """A trade whose fee exactly consumed its gross profit."""
    rec = _record(engine, fee_asset="USDT", fee_amount=Decimal("100"),
                  gross_pnl=Decimal("100"))

    assert rec.net_pnl is not None, "a net PnL of exactly zero was stored as NULL"
    assert Decimal(rec.net_pnl) == Decimal("0")


def test_a_zero_fee_still_records_a_zero_percentage(engine):
    """A full rebate is a real fee schedule, not a missing measurement."""
    rec = _record(engine, fee_asset="USDT", fee_amount=Decimal("0"))

    assert rec.fee_pct is not None
    assert Decimal(rec.fee_pct) == Decimal("0")


# ─────────────────────────────────────────────────────────────────────────────
# The record must survive the unconverted case
# ─────────────────────────────────────────────────────────────────────────────

def test_a_third_asset_fee_does_not_raise_while_logging(engine):
    """``fee_pct`` is now None on this path, and the success log formats it as a
    percentage. An unguarded ``None * 100`` would take the whole fee record down."""
    rec = _record(engine, fee_asset="BNB", fee_amount=Decimal("0.0005"))
    assert rec is not None
