"""PAPER-01: a BotRunner paper fill reaches the CANONICAL paper ledger, against real PostgreSQL.

THE DEFECT
==========
The platform had two paper engines sharing one word. The canonical ledger
(``paper_accounts`` / ``paper_orders`` / ``paper_fills`` / ``paper_positions`` /
``paper_balance_events`` / ``paper_trades``) is what ``GET /api/paper/account``,
``/positions``, ``/orders``, ``/trades`` and ``/summary`` serve. ``BotRunner``'s paper mode
simulated fills inside ``ExecutionEngine`` against a synthetic ``{"total_equity": 10000.0}``
and wrote **nothing** -- ``master_executor.py`` had no import edge to
``backend_app/backend/paper/`` at all. A trader who deployed a strategy in paper mode got
real simulated executions their paper account did not show and that did not survive a
restart.

WHY THESE TESTS USE REAL POSTGRESQL AND NOT ``FakeSupabase``
------------------------------------------------------------
``tests/test_paper_repository.FakeSupabase`` is the established double and it reimplements
the eight unique indexes 009 declares. But PAPER-01 is a *persistence* finding: the claim
is that a fill reaches real tables and survives, under the real constraints. A double
cannot establish that, because the database is the thing under test. So every test here
runs through ``tests/paper_pg_client.PostgresSupabase`` -- a PostgREST-shaped client over
psycopg2 -- against the live instance, which means:

* ``uq_paper_order_idem_default`` is the real partial index with real NULL semantics, which
  is the whole subtlety migration 013 exists for;
* ``chk_paper_balances_non_negative`` and ``chk_paper_order_fill_bound`` are real CHECKs;
* ``trg_paper_order_transition_guard`` and the six ``*_append_only`` triggers really fire.

THE ORACLE IS THE DATABASE, NOT THE RETURN VALUE
------------------------------------------------
Every assertion reads rows back over an **independent psycopg2 connection** that does not
go through the shim, and :func:`ledger_identity` recomputes
``cash + position_market_value`` in this file -- including the SHORT convention spelled out
by hand -- rather than calling ``paper_accounting.total_equity``. An oracle that called the
implementation would agree with it by construction and prove nothing.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
Paper/live isolation has its own file
(``tests/test_paper01_paper_live_isolation.py``): no live adapter constructed, no credential
decrypted, no ``create_order``/``cancel_order`` reachable.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
import textwrap
import uuid
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)
if not DSN.startswith("postgres"):
    pytest.skip("PAPER-01 requires PostgreSQL", allow_module_level=True)

from tests.paper_pg_client import (  # noqa: E402
    PostgresSupabase, paper_tables_present, purge_paper_user)

from backend_app.backend.execution_environment import ExecutionEnvironment  # noqa: E402
from backend_app.backend.paper import bot_paper_ledger as bpl  # noqa: E402
from backend_app.backend.paper.bot_paper_ledger import (  # noqa: E402
    BotPaperLedger, BotPaperLedgerUnavailable, BotPaperSellExceedsPosition)
from backend_app.backend.paper_trading_service import PaperTradingService  # noqa: E402
from backend_app.core.execution_engine import ExecutionEngine  # noqa: E402

#: ``PaperTradingService._clean_symbol`` maps ``BTC/USDT`` -> ``BTC-USDT``, so the bot's
#: symbol and the ledger's spelling differ by design. Both are named here so a test never
#: has to guess which one a row carries.
BOT_SYMBOL = "BTC/USDT"
LEDGER_SYMBOL = "BTC-USDT"

#: ``PaperTradingService.__init__`` defaults, restated so the expected arithmetic below is
#: derived from stated figures rather than from whatever the service happens to hold.
CAPITAL = Decimal("100000")
FEE_RATE = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.0005")
MONEY = Decimal("0.01")          # MONEY_EXPONENT = 2
PRICE_Q = Decimal("0.00000001")  # PRICE_PRECISION = 8


# ══════════════════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module", autouse=True)
def _require_ledger():
    try:
        if not paper_tables_present(DSN):
            pytest.skip("migration 009 is not applied to this database")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.fixture
def user() -> str:
    """A fresh synthetic owner per test, purged afterwards."""
    uid = str(uuid.uuid4())
    yield uid
    purge_paper_user(DSN, uid)


@pytest.fixture
def pg():
    client = PostgresSupabase(DSN)
    yield client
    client.close()


@pytest.fixture
def service(pg) -> PaperTradingService:
    return PaperTradingService(supabase=pg)


@pytest.fixture
def account(service, user) -> Dict[str, Any]:
    return service.get_or_create_account(user)


@pytest.fixture
def ledger(service, pg, user) -> BotPaperLedger:
    return BotPaperLedger(user, service=service, supabase=pg)


@pytest.fixture
def engine(ledger) -> ExecutionEngine:
    """An ``ExecutionEngine`` built exactly as ``BotRunner``'s paper branch builds one."""
    return ExecutionEngine(
        fee_rate=float(FEE_RATE),
        slippage=float(SLIPPAGE_RATE),
        portfolio_state={"total_equity": 10000.0},
        exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=ledger,
    )


# ══════════════════════════════════════════════════════════════════════════
#  THE ORACLE - an independent connection and hand-written arithmetic
# ══════════════════════════════════════════════════════════════════════════


def rows(table: str, user_id: str) -> List[Dict[str, Any]]:
    """Every row of ``table`` for ``user_id``, over a connection the shim never touches."""
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM public."{table}" WHERE user_id = %s ORDER BY created_at',
                (user_id,),
            )
            return [dict(record) for record in cur.fetchall()]
    finally:
        conn.close()


def dec(value: Any) -> Decimal:
    return Decimal("0") if value is None else Decimal(str(value))


def account_row(user_id: str) -> Dict[str, Any]:
    found = rows("paper_accounts", user_id)
    assert len(found) == 1, f"expected exactly one default paper account, got {len(found)}"
    return found[0]


def position_market_value(user_id: str, prices: Dict[str, Decimal] = None) -> Decimal:
    """The open positions' market value from the stored rows, QUANTIZED PER POSITION.

    Two parts of this are deliberate and were both corrected after measurement:

    * **The SHORT convention is written out here rather than imported.** An oracle that
      called ``paper_accounting.position_value`` would agree with the implementation by
      construction and prove nothing.
    * **Each position is quantized to Minor_Units BEFORE the sum**, which is what
      ``paper_accounting.position_market_value`` does and the reason it gives:
      sum-then-quantize would let a rounding remainder from an untouched position leak
      into the cash movement. The first version of this oracle summed unquantized and
      disagreed with the ledger by 0.005 on a 0.005-size position -- the ledger was right.

    ``prices`` overrides the stored ``current_price`` per ledger symbol, for revaluing the
    book at a price that has not been written yet.
    """
    prices = prices or {}
    total = Decimal("0")
    for row in rows("paper_positions", user_id):
        if row.get("closed_at") is not None:
            continue
        size = dec(row["size"])
        if size <= 0:
            continue
        symbol = str(row["symbol"])
        entry = dec(row["entry_price"])
        if symbol in prices:
            price = prices[symbol]
        elif row.get("current_price") is None:
            price = entry
        else:
            price = dec(row["current_price"])
        if str(row["side"]).strip().upper() == "LONG":
            value = size * price
        else:
            value = size * (Decimal("2") * entry - price)
        total += value.quantize(MONEY, rounding=ROUND_HALF_EVEN)
    return total


def ledger_identity(user_id: str):
    """``(cash, position_market_value, equity)`` recomputed from the stored rows."""
    acct = account_row(user_id)
    cash = dec(acct["available_balance"]) + dec(acct["locked_balance"])
    return cash, position_market_value(user_id), dec(acct["total_equity"])


def assert_identity(user_id: str, note: str = "") -> None:
    """Requirement 18.3's identity, ZERO tolerance, from the database."""
    cash, market_value, equity = ledger_identity(user_id)
    assert cash + market_value == equity, (
        f"the equity identity does not hold in the ledger{' after ' + note if note else ''}: "
        f"cash {cash} + position_market_value {market_value} = {cash + market_value}, "
        f"but paper_accounts.total_equity is {equity}"
    )


def expected_buy(price: Decimal, size: Decimal):
    """``(fill_price, fee, notional)`` for a market buy, from the stated rates.

    The canonical model is adverse slippage by side and a fee on the filled notional:
    ``fill = price * (1 + slippage_rate)`` for a buy, ``fee = money(size * fill * fee_rate)``.
    Written out so the assertions below compare against arithmetic a reader can check,
    not against whatever the service returned.
    """
    fill = (price * (Decimal("1") + SLIPPAGE_RATE)).quantize(PRICE_Q)
    fee = (size * fill * FEE_RATE).quantize(MONEY)
    notional = (size * fill).quantize(MONEY)
    return fill, fee, notional


def expected_sell(price: Decimal, size: Decimal):
    fill = (price * (Decimal("1") - SLIPPAGE_RATE)).quantize(PRICE_Q)
    fee = (size * fill * FEE_RATE).quantize(MONEY)
    notional = (size * fill).quantize(MONEY)
    return fill, fee, notional


def fill_once(engine: ExecutionEngine, side: str, size: str, price: str,
              execution_id: str):
    """One tick through the engine's real paper path."""
    return asyncio.run(
        engine._execute_trade_internal(
            symbol=BOT_SYMBOL, side=side, size=Decimal(size),
            price=Decimal(price), execution_id=execution_id,
        )
    )


# ══════════════════════════════════════════════════════════════════════════
#  1. BUY
# ══════════════════════════════════════════════════════════════════════════


def test_a_full_market_buy_persists_order_fill_and_position(engine, account, user):
    """THE HEADLINE: the fill a bot produces is now readable from the paper account."""
    ok, detail = fill_once(engine, "buy", "0.01", "50000", "exec_buy_full_1")

    assert ok is True, f"the paper fill was refused: {detail}"
    assert detail["paper_ledger"] == "canonical"

    orders = rows("paper_orders", user)
    fills = rows("paper_fills", user)
    positions = rows("paper_positions", user)

    assert len(orders) == 1, f"expected one paper_orders row, got {len(orders)}"
    assert len(fills) == 1, f"expected one paper_fills row, got {len(fills)}"
    assert len(positions) == 1, f"expected one paper_positions row, got {len(positions)}"

    assert orders[0]["order_state"] == "FILLED"
    assert orders[0]["legacy_status"] == "FILLED"
    assert orders[0]["symbol"] == LEDGER_SYMBOL
    assert orders[0]["side"] == "buy"
    assert dec(orders[0]["filled_quantity"]) == Decimal("0.01")
    assert positions[0]["side"] == "LONG"
    assert dec(positions[0]["size"]) == Decimal("0.01")
    assert_identity(user, "a full market buy")


def test_the_buy_fill_price_and_fee_are_the_canonical_ones(engine, account, user):
    """Exact figures, derived from the stated rates rather than read off the result."""
    fill_price, fee, notional = expected_buy(Decimal("50000"), Decimal("0.01"))

    ok, detail = fill_once(engine, "buy", "0.01", "50000", "exec_buy_fee_1")
    assert ok is True, detail

    stored_fill = rows("paper_fills", user)[0]
    assert dec(stored_fill["price"]) == fill_price, (
        f"stored fill price {stored_fill['price']} != expected {fill_price}"
    )
    # ``fee_minor`` is exact integer Minor_Units, so the comparison is in integers.
    assert int(stored_fill["fee_minor"]) == int((fee / MONEY).to_integral_value()), (
        f"stored fee_minor {stored_fill['fee_minor']} != expected {fee} "
        f"({int((fee / MONEY).to_integral_value())} minor units)"
    )
    assert Decimal(str(detail["fee"])) == fee


def test_equity_falls_by_exactly_the_fee_on_a_buy(engine, account, user):
    """Requirement 18.7, read from the database: a fill costs equity exactly its fees."""
    before = dec(account_row(user)["total_equity"])
    assert before == CAPITAL

    _fill_price, fee, _notional = expected_buy(Decimal("50000"), Decimal("0.01"))
    ok, detail = fill_once(engine, "buy", "0.01", "50000", "exec_buy_equity_1")
    assert ok is True, detail

    after = dec(account_row(user)["total_equity"])
    assert before - after == fee, (
        f"equity moved by {before - after}, but the recorded fee is {fee}. Requirement "
        f"18.7 makes those the same number."
    )
    assert_identity(user, "a fee-bearing buy")


def test_a_second_buy_adds_to_the_position_at_a_weighted_average(engine, account, user):
    """The system's 'partial buy': adding to an open long.

    A market order on this path fills in full, so a *partially filled* order is a
    resting-limit-order state a bot cannot reach -- see the module's companion note in
    PAPER_TRADING_CERTIFICATION.md. What a bot can do is add to a position, and the
    convention is weighted average (``paper_accounting.WEIGHTED_AVERAGE``, the only
    cost basis implemented).
    """
    first_fill, _fee1, _n1 = expected_buy(Decimal("50000"), Decimal("0.01"))
    second_fill, _fee2, _n2 = expected_buy(Decimal("60000"), Decimal("0.01"))

    assert fill_once(engine, "buy", "0.01", "50000", "exec_wavg_1")[0] is True
    assert fill_once(engine, "buy", "0.01", "60000", "exec_wavg_2")[0] is True

    positions = [r for r in rows("paper_positions", user) if dec(r["size"]) > 0]
    assert len(positions) == 1, f"expected one open position, got {len(positions)}"
    assert dec(positions[0]["size"]) == Decimal("0.02")

    expected_entry = (
        ((Decimal("0.01") * first_fill) + (Decimal("0.01") * second_fill))
        / Decimal("0.02")
    ).quantize(PRICE_Q)
    assert dec(positions[0]["entry_price"]) == expected_entry, (
        f"entry {positions[0]['entry_price']} is not the weighted average "
        f"{expected_entry}"
    )
    assert len(rows("paper_fills", user)) == 2
    assert_identity(user, "two buys")


def test_a_buy_beyond_available_cash_is_refused_and_moves_no_money(engine, account, user):
    """Insufficient cash. The oracle is the balance, not the message."""
    before = account_row(user)

    ok, detail = fill_once(engine, "buy", "100", "50000", "exec_buy_unfundable_1")

    assert ok is False, f"an unfundable buy was reported as filled: {detail}"
    after = account_row(user)
    assert dec(after["available_balance"]) == dec(before["available_balance"])
    assert dec(after["total_equity"]) == dec(before["total_equity"])
    assert rows("paper_fills", user) == [], "an unfundable buy produced a fill row"
    assert [r for r in rows("paper_positions", user) if dec(r["size"]) > 0] == []
    assert_identity(user, "a refused buy")


# ══════════════════════════════════════════════════════════════════════════
#  2. SELL
# ══════════════════════════════════════════════════════════════════════════


def test_a_full_position_close_writes_a_trade_and_realises_pnl(engine, account, user):
    """Requirement 18.10: a ``paper_trades`` row exists only when size reaches zero."""
    buy_fill, buy_fee, _ = expected_buy(Decimal("50000"), Decimal("0.01"))
    sell_fill, sell_fee, _ = expected_sell(Decimal("51000"), Decimal("0.01"))

    assert fill_once(engine, "buy", "0.01", "50000", "exec_close_1")[0] is True
    assert fill_once(engine, "sell", "0.01", "51000", "exec_close_2")[0] is True

    trades = rows("paper_trades", user)
    assert len(trades) == 1, f"a full close wrote {len(trades)} paper_trades rows"
    trade = trades[0]
    assert trade["side"] == "LONG"
    assert dec(trade["quantity"]) == Decimal("0.01")
    assert dec(trade["entry_price"]) == buy_fill
    assert dec(trade["exit_price"]) == sell_fill

    # Realized PnL is closed quantity at recorded fill prices, FEES EXCLUDED -- the fee is
    # its own column (`paper_trades.fee_minor`), which is the convention
    # `paper_accounting._realized_on_close` states and declines to net. Quantized to
    # Minor_Units like every other money figure: the raw product here is 9.495 and the
    # ledger stores 9.50 (ROUND_HALF_EVEN).
    expected_pnl = ((sell_fill - buy_fill) * Decimal("0.01")).quantize(
        MONEY, rounding=ROUND_HALF_EVEN
    )
    assert dec(trade["realized_pnl"]) == expected_pnl, (
        f"realized_pnl {trade['realized_pnl']} != money((exit {sell_fill} - entry "
        f"{buy_fill}) * 0.01) = {expected_pnl}"
    )
    assert dec(account_row(user)["realized_pnl"]) == expected_pnl

    closed = [r for r in rows("paper_positions", user) if r["closed_at"] is not None]
    assert len(closed) == 1, "the closed position row was deleted rather than kept at zero"
    assert dec(closed[0]["size"]) == Decimal("0")
    assert_identity(user, "a full close")


def test_each_fill_costs_equity_exactly_its_own_fee(engine, account, user):
    """Requirement 18.7, stated the way the implementation actually guarantees it.

    THE IDENTITY THIS TEST DOES *NOT* ASSERT, AND WHY
    ------------------------------------------------
    ``equity == initial_capital + realized_pnl - fees`` is **not** an identity in this
    engine, and asserting it would have been wrong. Measured on a 0.01 BTC round trip
    50000 -> 51000: the ledger stores ``total_equity = 100008.48`` while
    ``100000 + 9.50 - 0.50 - 0.51 = 100008.49``.

    The 0.01 is not drift. ``apply_fill`` moves cash by the change in **quantized
    position value** (``old_value = money(0.01 * 50974.50) = 509.74``, since 509.745
    rounds half-to-even down), while ``realized_pnl`` is quantized independently
    (``money(9.495) = 9.50``, rounding up). Two figures, each correctly quantized once,
    which do not have to reconcile to the same hundredth -- and ``paper_accounting`` never
    claims they do, because ``realized_pnl`` is an accumulator and takes no part in the
    cash arithmetic.

    What Requirement 18.7 does guarantee, and what is asserted here, is per fill:
    **equity falls by exactly that fill's fee, valuing the position at that fill's price
    on both sides.** A price move between fills changes equity legitimately -- that is
    unrealized PnL, not a fee.
    """
    _buy_fill, buy_fee, _ = expected_buy(Decimal("50000"), Decimal("0.01"))
    sell_fill, sell_fee, _ = expected_sell(Decimal("51000"), Decimal("0.01"))

    # ── the buy: valued at its own fill price, equity falls by exactly the fee ──
    assert fill_once(engine, "buy", "0.01", "50000", "exec_rt_1")[0] is True
    after_buy = dec(account_row(user)["total_equity"])
    assert CAPITAL - after_buy == buy_fee, (
        f"the buy moved equity by {CAPITAL - after_buy}, fee was {buy_fee}"
    )

    # ── revalue the open position at the price the sell will fill at ──
    cash_before_sell = (
        dec(account_row(user)["available_balance"])
        + dec(account_row(user)["locked_balance"])
    )
    equity_at_sell_price = cash_before_sell + position_market_value(
        user, {LEDGER_SYMBOL: sell_fill}
    )

    # ── the sell: equity falls from THAT figure by exactly the sell fee ──
    assert fill_once(engine, "sell", "0.01", "51000", "exec_rt_2")[0] is True
    after_sell = dec(account_row(user)["total_equity"])
    assert equity_at_sell_price - after_sell == sell_fee, (
        f"at the sell price the book was worth {equity_at_sell_price} and after the fill "
        f"it is {after_sell}, a move of {equity_at_sell_price - after_sell}; the recorded "
        f"fee is {sell_fee}. Requirement 18.7 makes those the same number."
    )


def test_a_partial_sell_reduces_the_position_and_writes_no_trade(engine, account, user):
    """A partial close moves realized PnL without closing the round trip (Req 18.10)."""
    assert fill_once(engine, "buy", "0.02", "50000", "exec_partial_1")[0] is True
    assert fill_once(engine, "sell", "0.01", "51000", "exec_partial_2")[0] is True

    open_positions = [r for r in rows("paper_positions", user) if dec(r["size"]) > 0]
    assert len(open_positions) == 1
    assert dec(open_positions[0]["size"]) == Decimal("0.01")
    assert rows("paper_trades", user) == [], (
        "a partial sell wrote a paper_trades row; that set is closed round trips only"
    )
    assert dec(account_row(user)["realized_pnl"]) > 0, (
        "a profitable partial close moved no realized PnL"
    )
    assert_identity(user, "a partial sell")


def test_the_sell_fee_is_recorded_on_its_own_fill(engine, account, user):
    _buy_fill, _buy_fee, _ = expected_buy(Decimal("50000"), Decimal("0.01"))
    sell_fill, sell_fee, _ = expected_sell(Decimal("51000"), Decimal("0.01"))

    assert fill_once(engine, "buy", "0.01", "50000", "exec_sellfee_1")[0] is True
    assert fill_once(engine, "sell", "0.01", "51000", "exec_sellfee_2")[0] is True

    fills = rows("paper_fills", user)
    assert len(fills) == 2
    sell_row = fills[-1]
    assert dec(sell_row["price"]) == sell_fill
    assert int(sell_row["fee_minor"]) == int((sell_fee / MONEY).to_integral_value())


def test_a_sell_with_no_position_is_refused_and_opens_no_short(engine, account, user):
    """Insufficient position. THE PRE-FIX SEMANTICS, PRESERVED DELIBERATELY.

    Measured (``staging/probe_paper01_sell_semantics.py``): the canonical path ACCEPTS
    this and opens ``side=SHORT``. The pre-fix BotRunner paper branch answered
    ``"No position found for {symbol}"``, and its buy branch calls
    ``open_position(side='long')``, so the bot path is long-only by construction.
    ``bot_paper_ledger.REDUCE_ONLY_SELLS`` keeps it that way, because handing every paper
    bot the ability to short as a side effect of a persistence fix would be a material
    behaviour change nobody reviewed.
    """
    ok, detail = fill_once(engine, "sell", "0.01", "50000", "exec_naked_sell_1")

    assert ok is False, f"a naked sell was filled: {detail}"
    assert "No position found" in str(detail)
    assert rows("paper_fills", user) == []
    assert rows("paper_positions", user) == [], "a refused sell created a position row"
    assert dec(account_row(user)["total_equity"]) == CAPITAL
    assert_identity(user, "a refused naked sell")


def test_a_sell_larger_than_the_long_is_refused_rather_than_reversed(engine, account, user):
    """The reversal-through-zero case, also refused for a bot.

    Measured: the canonical path closes the long, writes a ``paper_trades`` row and opens
    a short for the remainder. Correct for the canonical engine; not what a long-only bot
    asked for.
    """
    assert fill_once(engine, "buy", "0.01", "50000", "exec_rev_1")[0] is True
    before_equity = dec(account_row(user)["total_equity"])

    ok, detail = fill_once(engine, "sell", "0.03", "51000", "exec_rev_2")

    assert ok is False, f"an oversized sell was filled: {detail}"
    assert "exceeds the open long" in str(detail)
    shorts = [r for r in rows("paper_positions", user)
              if str(r["side"]).upper() == "SHORT"]
    assert shorts == [], f"a refused oversized sell opened a short: {shorts}"
    assert dec(account_row(user)["total_equity"]) == before_equity
    assert len(rows("paper_fills", user)) == 1, "the refused sell produced a fill"
    assert_identity(user, "a refused oversized sell")


# ══════════════════════════════════════════════════════════════════════════
#  3. LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════


def test_multiple_fills_accumulate_one_position_and_many_fill_rows(engine, account, user):
    for index, price in enumerate(("50000", "50100", "50200", "50300")):
        ok, detail = fill_once(engine, "buy", "0.005", price, f"exec_multi_{index}")
        assert ok is True, f"fill {index} was refused: {detail}"
        assert_identity(user, f"fill {index}")

    assert len(rows("paper_fills", user)) == 4
    assert len(rows("paper_orders", user)) == 4
    open_positions = [r for r in rows("paper_positions", user) if dec(r["size"]) > 0]
    assert len(open_positions) == 1, (
        f"four buys produced {len(open_positions)} open positions; "
        f"uq_paper_position_open permits one per (account, symbol)"
    )
    assert dec(open_positions[0]["size"]) == Decimal("0.02")


def test_a_duplicate_execution_id_is_one_order_and_one_money_movement(
    engine, account, user
):
    """The idempotency guarantee, arbitrated by ``uq_paper_order_idem_default``."""
    ok1, _ = fill_once(engine, "buy", "0.01", "50000", "exec_dupe_same")
    assert ok1 is True
    after_first = dec(account_row(user)["total_equity"])

    ok2, detail2 = fill_once(engine, "buy", "0.01", "50000", "exec_dupe_same")

    assert ok2 is True, f"a replayed tick was refused outright: {detail2}"
    assert len(rows("paper_orders", user)) == 1, "a replayed tick placed a second order"
    assert len(rows("paper_fills", user)) == 1, "a replayed tick produced a second fill"
    assert dec(account_row(user)["total_equity"]) == after_first, (
        "a replayed tick moved the money twice"
    )
    assert_identity(user, "a replayed tick")


def test_two_different_ticks_are_two_orders(engine, account, user):
    """POSITIVE CONTROL for the test above.

    Without this, the idempotency assertion would also pass if the ledger had simply
    stopped placing orders after the first one.
    """
    assert fill_once(engine, "buy", "0.01", "50000", "exec_distinct_a")[0] is True
    assert fill_once(engine, "buy", "0.01", "50000", "exec_distinct_b")[0] is True

    assert len(rows("paper_orders", user)) == 2
    assert len(rows("paper_fills", user)) == 2
    keys = {r["idempotency_key"] for r in rows("paper_orders", user)}
    assert keys == {"botrunner:exec_distinct_a", "botrunner:exec_distinct_b"}


def test_a_duplicate_signal_in_one_time_bucket_is_one_order(engine, account, user):
    """A duplicate SIGNAL, as the gateway would present it.

    ``execute_with_idempotency`` derives ``execution_id`` from
    ``(tenant, strategy, symbol, side, time bucket)``, so two signals inside one bucket
    arrive with the SAME execution id. That is the shape this asserts.
    """
    shared = "exec_bucket_2026_10_08T10_05"
    for _ in range(5):
        ok, detail = fill_once(engine, "buy", "0.01", "50000", shared)
        assert ok is True, detail

    assert len(rows("paper_orders", user)) == 1
    assert len(rows("paper_fills", user)) == 1
    assert_identity(user, "five duplicate signals")


def test_state_survives_a_new_engine_instance(service, pg, account, user):
    """Restart after a persisted order: a fresh engine reads the stored book.

    This is what the pre-fix path could not do at all -- its figures lived on the engine
    instance and were lost with the process.
    """
    first = ExecutionEngine(
        fee_rate=float(FEE_RATE), slippage=float(SLIPPAGE_RATE),
        portfolio_state={"total_equity": 10000.0}, exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=BotPaperLedger(user, service=service, supabase=pg),
    )
    assert fill_once(first, "buy", "0.01", "50000", "exec_restart_1")[0] is True
    equity_before = dec(account_row(user)["total_equity"])

    # A brand-new process would build a brand-new engine on the synthetic 10000 again.
    second = ExecutionEngine(
        fee_rate=float(FEE_RATE), slippage=float(SLIPPAGE_RATE),
        portfolio_state={"total_equity": 10000.0}, exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=BotPaperLedger(user, service=service, supabase=pg),
    )
    assert second.current_equity == Decimal("10000"), (
        "precondition: a fresh engine starts on its synthetic balance"
    )

    # One fill later it is reconciled onto the PERSISTED book, not the synthetic one.
    assert fill_once(second, "sell", "0.01", "51000", "exec_restart_2")[0] is True

    equity_after = dec(account_row(user)["total_equity"])
    assert second.current_equity == equity_after, (
        f"the restarted engine reports {second.current_equity} while the ledger holds "
        f"{equity_after}"
    )
    assert equity_after > equity_before - Decimal("5"), (
        "the restart lost the position: a profitable close should not reduce equity by "
        "more than the fees"
    )
    assert len(rows("paper_fills", user)) == 2
    assert_identity(user, "a restart")


def test_a_worker_retry_after_an_apparent_failure_does_not_double_fill(
    engine, account, user
):
    """WORK-01's redelivery, on the paper path.

    The command worker can redeliver a tick whose acknowledgement was lost. The fill
    already landed, so the retry must find it rather than apply it again.
    """
    ok, _ = fill_once(engine, "buy", "0.01", "50000", "exec_worker_retry")
    assert ok is True
    equity = dec(account_row(user)["total_equity"])
    fills = len(rows("paper_fills", user))

    for _ in range(3):
        retried, detail = fill_once(engine, "buy", "0.01", "50000", "exec_worker_retry")
        assert retried is True, detail

    assert len(rows("paper_fills", user)) == fills
    assert dec(account_row(user)["total_equity"]) == equity
    assert_identity(user, "three worker retries")


def test_a_stale_execution_id_reuses_its_recorded_order(engine, account, user):
    """A tick replayed long after the fact resolves to the order already recorded."""
    assert fill_once(engine, "buy", "0.01", "50000", "exec_stale_1")[0] is True
    recorded = rows("paper_orders", user)[0]["id"]

    # Other ticks happen in between, so the stale replay is not simply the latest order.
    assert fill_once(engine, "buy", "0.01", "50100", "exec_stale_2")[0] is True
    assert fill_once(engine, "buy", "0.01", "50200", "exec_stale_3")[0] is True
    equity = dec(account_row(user)["total_equity"])

    ok, detail = fill_once(engine, "buy", "0.01", "50000", "exec_stale_1")
    assert ok is True, detail
    assert detail["order_id"] == str(recorded), (
        f"the stale replay resolved to {detail['order_id']}, not the recorded order "
        f"{recorded}"
    )
    assert len(rows("paper_orders", user)) == 3
    assert dec(account_row(user)["total_equity"]) == equity
    assert_identity(user, "a stale replay")


def test_close_all_flattens_every_position_and_conserves_the_identity(
    engine, account, user
):
    """A close-all sweep: every open long reduced to zero, identity intact throughout."""
    for index, (symbol, size, price) in enumerate(
        (("BTC/USDT", "0.01", "50000"), ("ETH/USDT", "0.5", "3000"),
         ("SOL/USDT", "10", "150"))
    ):
        ok, detail = asyncio.run(
            engine._execute_trade_internal(
                symbol=symbol, side="buy", size=Decimal(size),
                price=Decimal(price), execution_id=f"exec_closeall_open_{index}",
            )
        )
        assert ok is True, f"{symbol} buy refused: {detail}"

    open_positions = [r for r in rows("paper_positions", user) if dec(r["size"]) > 0]
    assert len(open_positions) == 3, f"expected three open positions, got {open_positions}"
    assert_identity(user, "three open positions")

    for index, (symbol, size, price) in enumerate(
        (("BTC/USDT", "0.01", "50500"), ("ETH/USDT", "0.5", "3030"),
         ("SOL/USDT", "10", "151"))
    ):
        ok, detail = asyncio.run(
            engine._execute_trade_internal(
                symbol=symbol, side="sell", size=Decimal(size),
                price=Decimal(price), execution_id=f"exec_closeall_close_{index}",
            )
        )
        assert ok is True, f"{symbol} close refused: {detail}"
        assert_identity(user, f"close {symbol}")

    still_open = [r for r in rows("paper_positions", user) if dec(r["size"]) > 0]
    assert still_open == [], f"close-all left open positions: {still_open}"
    assert len(rows("paper_trades", user)) == 3, "each full close owes a paper_trades row"
    cash, market_value, equity = ledger_identity(user)
    assert market_value == Decimal("0")
    assert cash == equity


def test_concurrent_paper_executions_keep_one_order_each_and_a_consistent_ledger(
    service, pg, account, user
):
    """Real concurrency over the ledger, with the database as the arbiter.

    Six coroutines, three distinct ticks, each submitted twice. The oracle is the row
    count and the identity: three orders, three fills, and the equity identity holding.

    ``paper_accounts.version`` is the optimistic predicate, so a loser surfaces as a 409
    ``PAPER_CONCURRENCY_CONFLICT`` rather than a silently overwritten balance -- a refusal
    is an acceptable outcome here, a corrupted balance is not. That is why this test
    asserts on the LEDGER's consistency and on "no duplicate order per key", not on all
    six calls succeeding.
    """
    async def drive():
        engines = [
            ExecutionEngine(
                fee_rate=float(FEE_RATE), slippage=float(SLIPPAGE_RATE),
                portfolio_state={"total_equity": 10000.0}, exchange_executor=None,
                execution_environment=ExecutionEnvironment.PAPER,
                paper_ledger=BotPaperLedger(
                    user, service=service, supabase=PostgresSupabase(DSN)
                ),
            )
            for _ in range(6)
        ]
        ticks = ["exec_conc_a", "exec_conc_b", "exec_conc_c"] * 2
        return await asyncio.gather(
            *[
                eng._execute_trade_internal(
                    symbol=BOT_SYMBOL, side="buy", size=Decimal("0.001"),
                    price=Decimal("50000"), execution_id=tick,
                )
                for eng, tick in zip(engines, ticks)
            ],
            return_exceptions=True,
        )

    outcomes = asyncio.run(drive())

    orders = rows("paper_orders", user)
    keys = [r["idempotency_key"] for r in orders]
    assert len(keys) == len(set(keys)), (
        f"a key was recorded twice despite uq_paper_order_idem_default: {keys}"
    )
    assert set(keys) <= {
        "botrunner:exec_conc_a", "botrunner:exec_conc_b", "botrunner:exec_conc_c"
    }, f"unexpected keys: {keys}"
    assert len(orders) >= 1, f"no order survived the race; outcomes={outcomes}"
    assert_identity(user, "six concurrent executions")

    # Every fill that exists belongs to an order that exists, and no order over-filled.
    for order in orders:
        assert dec(order["filled_quantity"]) <= dec(order["quantity"]), (
            f"order {order['id']} over-filled: {order['filled_quantity']} of "
            f"{order['quantity']}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  4. THE CONTROLS - these make the suite above mean something
# ══════════════════════════════════════════════════════════════════════════


def test_without_a_ledger_the_engine_still_persists_nothing(account, user):
    """NEGATIVE CONTROL. Reproduces the DEFECT, so the suite above cannot pass vacuously.

    An engine with no ledger is the pre-fix engine: it simulates in process, reports a
    ``paper_``-prefixed fill, and writes no row. If this ever starts persisting, the tests
    above are no longer measuring the seam.
    """
    bare = ExecutionEngine(
        fee_rate=float(FEE_RATE), slippage=float(SLIPPAGE_RATE),
        portfolio_state={"total_equity": 10000.0}, exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
    )
    assert bare.paper_ledger is None

    ok, detail = fill_once(bare, "buy", "0.01", "50000", "exec_bare_1")

    assert ok is True, f"the in-process paper branch stopped simulating: {detail}"
    assert str(detail["order_id"]).startswith("paper_"), detail
    assert "paper_ledger" not in detail
    assert rows("paper_orders", user) == [], (
        "an engine with NO ledger wrote to paper_orders; the negative control is broken "
        "and the rest of this file proves nothing about the seam"
    )
    assert rows("paper_fills", user) == []


def test_a_ledger_outage_refuses_the_fill_rather_than_simulating(service, account, user):
    """FAIL CLOSED. A fill that cannot be recorded must not be reported as filled."""
    class _Down:
        def __init__(self):
            self.calls = 0

        async def record_fill(self, **kwargs):
            self.calls += 1
            raise BotPaperLedgerUnavailable("simulated ledger outage")

    down = _Down()
    engine = ExecutionEngine(
        fee_rate=float(FEE_RATE), slippage=float(SLIPPAGE_RATE),
        portfolio_state={"total_equity": 10000.0}, exchange_executor=None,
        execution_environment=ExecutionEnvironment.PAPER,
        paper_ledger=down,
    )

    ok, detail = fill_once(engine, "buy", "0.01", "50000", "exec_outage_1")

    assert down.calls == 1, "the engine did not consult the ledger at all"
    assert ok is False, (
        f"a fill the ledger could not record was reported as filled: {detail}. That is "
        f"the PAPER-01 defect reintroduced as a fallback."
    )
    assert "did not record" in str(detail)
    assert rows("paper_fills", user) == []
    assert dec(account_row(user)["total_equity"]) == CAPITAL


def test_the_idempotency_key_is_deterministic_and_within_the_column_bound():
    """``chk_paper_order_idem_len`` admits 1..128 characters."""
    assert bpl.idempotency_key_for("exec_abc") == "botrunner:exec_abc"
    assert bpl.idempotency_key_for("exec_abc") == bpl.idempotency_key_for("exec_abc")

    long_key = bpl.idempotency_key_for("x" * 500)
    assert 1 <= len(long_key) <= bpl.MAX_IDEMPOTENCY_KEY
    assert long_key.startswith(bpl.IDEMPOTENCY_PREFIX)

    with pytest.raises(bpl.BotPaperLedgerError):
        bpl.idempotency_key_for("")


def test_the_engine_paper_branch_consults_the_ledger():
    """AST, not substring: this region's comments mention the ledger repeatedly."""
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(ExecutionEngine._execute_trade_internal))
    )
    node = tree.body[0]
    statements = node.body[1:] if ast.get_docstring(node) else node.body
    attributes = {
        call.func.attr
        for statement in statements
        for call in ast.walk(statement)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }
    assert "record_fill" in attributes, (
        "the engine's paper branch never calls record_fill, so a bot's fill reaches no "
        "ledger and PAPER-01 is open"
    )
    assert "_reconcile_from_paper_ledger" in attributes, (
        "the engine does not reconcile from the ledger, so it keeps a second independent "
        "answer to what the cash is"
    )


def test_botrunner_installs_a_ledger_on_the_paper_branch_and_not_on_the_live_one():
    """AST over ``_recover_live_state``: the paper construction passes one, the live does not."""
    from backend_app.backend.master_executor import BotRunner

    source = textwrap.dedent(inspect.getsource(BotRunner._recover_live_state))
    tree = ast.parse(source)

    constructions = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "ExecutionEngine"
    ]
    assert len(constructions) == 2, (
        f"_recover_live_state builds {len(constructions)} ExecutionEngines; this test "
        f"knows about the paper one and the live one"
    )

    def environment_of(call):
        for keyword in call.keywords:
            if keyword.arg == "execution_environment":
                return ast.dump(keyword.value)
        return ""

    def has_ledger(call):
        return any(keyword.arg == "paper_ledger" for keyword in call.keywords)

    paper = [c for c in constructions if "PAPER" in environment_of(c)]
    live = [c for c in constructions if "LIVE" in environment_of(c)]
    assert len(paper) == 1 and len(live) == 1, (
        "could not tell the paper and live engine constructions apart"
    )
    assert has_ledger(paper[0]), (
        "BotRunner's PAPER engine is built without a paper_ledger, so its fills reach no "
        "ledger"
    )
    assert not has_ledger(live[0]), (
        "BotRunner's LIVE engine was handed a paper_ledger; a live order must never be "
        "written to the paper books"
    )


def test_reduce_only_is_declared_and_on():
    """The policy that preserves the pre-fix long-only semantics is explicit, not implied."""
    assert bpl.REDUCE_ONLY_SELLS is True, (
        "REDUCE_ONLY_SELLS is off, so a paper bot can now open and reverse into shorts - "
        "a behaviour change the pre-fix engine refused. If that is intended, it needs a "
        "decision recorded, not a flipped default."
    )
