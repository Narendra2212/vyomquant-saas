"""
tests/test_trade13_live_fee_and_slippage_telemetry.py

TRADE-13. `GET /api/orders/history` (`routers/orders.py`) answers straight off the
`executions` QuestDB table, which `telemetry_engine.TelemetryEngine.log_execution` writes
and `master_executor.py`'s fire-and-forget `safe_log()` calls after a live fill. Before this
finding that call carried exactly `(timestamp, user_id, symbol, side, status, amount,
price)`, so `TradeHistory.jsx` had no `fee`, `slippage` or per-trade `pnl` to show for a
LIVE execution and rendered the not-available marker for all three, every time. This file
pins what changed and what deliberately did not:

  * `telemetry_fee_and_slippage` (`backend_app/backend/master_executor.py`) is the new, pure
    function that reads the fee and slippage figures `execution_engine._execute_trade_internal`
    already computes on the live branch (`trade_result['fee']`, `['fee_currency']`,
    `['executed_price']`) out of the SAME result `settle_exposure` already reads, and turns
    them into `(fee, fee_currency, slippage)` for the telemetry write. It calls nothing --
    no exchange, no repository, no state change -- so testing it needs no database, no
    event loop and no bot: it is a plain function of its arguments.
  * `TelemetryEngine.log_execution` (`backend_app/backend/telemetry_engine.py`) gained three
    new, OPTIONAL keyword arguments. A call that omits them (every call site that predates
    this change, and any test double that mocks the method with `*args, **kwargs`) must
    write the exact ILP line it always wrote -- this is the thing that makes the change
    additive rather than a rewrite of a hot, already-fire-and-forget write path.

What is NOT here: realized P&L. Computing it correctly for a position-closing trade needs
the entry price of the position being closed, and `PositionEngine`
(`backend_app/backend/position_engine.py`) has no caller anywhere on this live
order-completion path -- `master_executor.py` constructs no `PositionEngine` of its own, a
fact re-verified for this finding with
`grep -rn "PositionEngine(" backend_app/backend/master_executor.py` returning nothing. There
is therefore no cost basis to realize a live fill's P&L against today, so this finding does
not add one; `test_no_positionengine_caller_on_the_live_completion_path` below pins that
fact so a future caller added there is the thing that reopens the question, not a silent
assumption.
"""
import asyncio
import re
from decimal import Decimal

import pytest

from backend_app.backend.master_executor import telemetry_fee_and_slippage
from backend_app.backend.telemetry_engine import TelemetryEngine


# ═══════════════════════════════════════════════════════════════════════════════
# telemetry_fee_and_slippage — pure, no I/O
# ═══════════════════════════════════════════════════════════════════════════════

def _result(trade_result):
    """The shape `execute_with_idempotency` returns on a completed/filled order."""
    return {"status": "completed", "execution_id": "exec_x", "result": {"trade_result": trade_result}}


class TestTelemetryFeeAndSlippage:
    def test_reads_the_venues_own_fee_and_currency(self):
        fee, fee_currency, _slippage = telemetry_fee_and_slippage(
            result=_result({"fee": "1.25", "fee_currency": "USDT", "executed_price": 100.0}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("2"),
        )
        assert fee == pytest.approx(1.25)
        assert fee_currency == "USDT"

    def test_slippage_is_abs_fill_minus_decision_times_filled_qty(self):
        # Matches paper_simulator.slippage_amount's own definition:
        # |fill_price - reference| * quantity.
        _fee, _cur, slippage = telemetry_fee_and_slippage(
            result=_result({"fee": "0", "executed_price": 101.5}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("4"),
        )
        assert slippage == pytest.approx(abs(101.5 - 100.0) * 4)

    def test_slippage_is_zero_when_fill_matches_decision_price_exactly(self):
        # A genuine zero, not a fabricated one: the venue filled at the asked price.
        _fee, _cur, slippage = telemetry_fee_and_slippage(
            result=_result({"fee": "0", "executed_price": 100.0}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("3"),
        )
        assert slippage == 0.0

    def test_no_result_yields_all_none_not_zero(self):
        fee, fee_currency, slippage = telemetry_fee_and_slippage(
            result=None, decision_price=Decimal("100"), filled_qty=Decimal("1"),
        )
        assert (fee, fee_currency, slippage) == (None, None, None)

    def test_result_with_no_trade_result_yields_all_none(self):
        fee, fee_currency, slippage = telemetry_fee_and_slippage(
            result={"status": "skipped_contention", "result": None},
            decision_price=Decimal("100"),
            filled_qty=Decimal("1"),
        )
        assert (fee, fee_currency, slippage) == (None, None, None)

    def test_unreadable_fee_degrades_to_none_rather_than_raising(self):
        fee, _cur, _slip = telemetry_fee_and_slippage(
            result=_result({"fee": "not-a-number", "executed_price": 100.0}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("1"),
        )
        assert fee is None

    def test_unreadable_executed_price_yields_none_slippage_not_an_exception(self):
        _fee, _cur, slippage = telemetry_fee_and_slippage(
            result=_result({"fee": "0", "executed_price": "not-a-number"}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("1"),
        )
        assert slippage is None

    def test_zero_filled_qty_yields_none_slippage_not_a_zero(self):
        # filled_qty truthiness check: 0 must not silently zero the product out and read as
        # "no slippage" -- it must read as "not computable from nothing filled".
        _fee, _cur, slippage = telemetry_fee_and_slippage(
            result=_result({"fee": "0", "executed_price": 105.0}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("0"),
        )
        assert slippage is None

    def test_missing_fee_currency_is_none_not_empty_string(self):
        _fee, fee_currency, _slip = telemetry_fee_and_slippage(
            result=_result({"fee": "1.0", "executed_price": 100.0}),
            decision_price=Decimal("100.0"),
            filled_qty=Decimal("1"),
        )
        assert fee_currency is None


# ═══════════════════════════════════════════════════════════════════════════════
# TelemetryEngine.log_execution — ILP line shape, no network
# ═══════════════════════════════════════════════════════════════════════════════

class _CapturingResponse:
    status = 204

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return ""


class _CapturingSession:
    """Captures the exact `data=` payload `log_execution` POSTs, without any network I/O."""

    def __init__(self):
        self.lines = []

    def post(self, _url, data):
        self.lines.append(data)
        return _CapturingResponse()


def _engine_with_capturing_session():
    engine = TelemetryEngine.__new__(TelemetryEngine)  # bypass __init__'s real aiohttp setup
    engine.write_url = "http://questdb.test/write"
    session = _CapturingSession()
    engine._get_session = lambda: _async_return(session)
    return engine, session


async def _async_return(value):
    return value


class TestLogExecutionIsAdditive:
    """A call that omits fee/fee_currency/slippage writes the exact pre-TRADE-13 line."""

    def test_omitting_the_new_kwargs_writes_the_same_line_as_before(self):
        engine, session = _engine_with_capturing_session()
        asyncio.get_event_loop().run_until_complete(
            engine.log_execution(
                user_id="u1", symbol="BTC/USDT", side="buy", amount=0.5, price=64000.0,
            )
        )
        assert len(session.lines) == 1
        line = session.lines[0]
        assert line.startswith("executions,user_id=u1,symbol=BTC_USDT,side=buy,status=FILLED ")
        fields = line.split(" ")[1]
        assert fields == "amount=0.5,price=64000.0"
        assert "fee" not in fields
        assert "slippage" not in fields

    def test_fee_and_slippage_are_appended_as_fields_when_given(self):
        engine, session = _engine_with_capturing_session()
        asyncio.get_event_loop().run_until_complete(
            engine.log_execution(
                user_id="u1", symbol="BTC/USDT", side="buy", amount=0.5, price=64000.0,
                fee=1.23, fee_currency="USDT", slippage=4.56,
            )
        )
        line = session.lines[0]
        tags, fields, _ts = line.split(" ")
        assert ",fee_currency=USDT" in tags
        assert re.search(r",fee=1\.23(?:,|$)", fields)
        assert re.search(r",slippage=4\.56(?:,|$)", fields)

    def test_a_none_fee_writes_no_fee_field_at_all_not_a_fabricated_zero(self):
        engine, session = _engine_with_capturing_session()
        asyncio.get_event_loop().run_until_complete(
            engine.log_execution(
                user_id="u1", symbol="BTC/USDT", side="buy", amount=0.5, price=64000.0,
                fee=None, slippage=None,
            )
        )
        fields = session.lines[0].split(" ")[1]
        assert "fee=" not in fields
        assert "slippage=" not in fields

    def test_fee_currency_is_tag_escaped_not_quoted_as_a_field(self):
        # A string FIELD in ILP needs double quotes; a TAG is bare-escaped. fee_currency is
        # carried as a tag (like symbol/side), so a value with a special character must be
        # escaped the same way _ilp_escape_tag already escapes every other tag -- never
        # left bare in the fields half, where it would corrupt the line.
        engine, session = _engine_with_capturing_session()
        asyncio.get_event_loop().run_until_complete(
            engine.log_execution(
                user_id="u1", symbol="BTC/USDT", side="buy", amount=0.5, price=64000.0,
                fee=0.1, fee_currency="a,b",
            )
        )
        tags = session.lines[0].split(" ")[0]
        assert "fee_currency=a\\,b" in tags


# ═══════════════════════════════════════════════════════════════════════════════
# THE SCOPE LIMIT: no realized P&L, because nothing tracks a live cost basis
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_positionengine_caller_on_the_live_completion_path():
    """Re-verifies, for THIS finding, that `PositionEngine` has no caller in
    `master_executor.py` -- the reason TRADE-13 persists fee and slippage but does not
    attempt a per-trade realized P&L for live executions. A caller added here in the future
    is exactly the change that would reopen this question; this test fails loudly when it
    does, rather than letting the scope-out go stale.
    """
    import inspect

    from backend_app.backend import master_executor

    source = inspect.getsource(master_executor)
    assert "PositionEngine(" not in source
    assert "get_position_engine(" not in source
