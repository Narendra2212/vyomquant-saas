"""FILL-01 — partial-fill accounting. Cumulative and incremental are not interchangeable.

ESTABLISHED SEMANTICS (not assumed)
-----------------------------------
ccxt 4.3.92's `Exchange.safe_order` derives the order-level figure by summing the
trade-level ones:

    filled = '0'
    for i in range(len(trades)):
        tradeAmount = self.safe_string(trades[i], 'amount')
        if parseFilled and (tradeAmount is not None):
            filled = Precise.string_add(filled, tradeAmount)

so `trade['amount']` is INCREMENTAL and `order['filled']` is CUMULATIVE. Demonstrated:
three trades of 1.0 / 1.0 / 0.5 parse to `filled == 2.5`, `average == 180.0`.
Evidence: `staging/probe_p11bc_fill_semantics.py` → `staging/p11bc_fill_semantics.txt`.

THE DEFECTS THESE TESTS PIN
---------------------------
1. The no-trades fallback synthesised a fill from the CUMULATIVE `filled` under the
   ORDER's id — so a second event re-booked everything, and dedup on trade id could not
   distinguish a new partial fill from a replay.
2. `trade.get("id", "unknown")` gave every id-less fill the literal identity `"unknown"`,
   collapsing distinct fills onto one row.
3. `price` defaulted to 0, booking a real quantity at a price of zero.
4. `filled_size = str(event.filled) if event.filled else "0"` read an ABSENT figure as a
   zero, erasing a previously recorded cumulative fill.
5. Nothing enforced monotonicity, so a stale out-of-order event could shrink a
   cumulative fill.

SCOPE: OFFLINE UNIT TESTS. No venue, no credential, no order, and the WebSocket listener
is **not started** — the two handler methods are called directly. These prove the
accounting logic. They are **not** exchange certification and do not establish anything
about how any venue actually reports fills; scenario 5b in
`tests/exchange_sandbox/test_sandbox_order_lifecycle.py` remains the test that would.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from backend_app.backend.exchange_websocket_listener import (ExchangeWebSocketListener,
                                                             OrderUpdateEvent)

ORDER_ID = "VENUE-ORDER-1"
TENANT = "11111111-1111-1111-1111-111111111111"


# ══════════════════════════════════════════════════════════════════════════════════════
#  FIXTURES — the handlers are exercised directly; nothing is started
# ══════════════════════════════════════════════════════════════════════════════════════

def _event(**over: Any) -> OrderUpdateEvent:
    """An `OrderUpdateEvent` with only the fields these handlers read."""
    base: dict = {
        "exchange_id": "binance", "tenant_id": TENANT, "symbol": "BTC/USDT",
        "order_id": ORDER_ID, "status": "open", "side": "buy",
        "filled": None, "remaining": None, "average_price": None,
    }
    base.update(over)
    ev = OrderUpdateEvent.__new__(OrderUpdateEvent)
    for k, v in base.items():
        setattr(ev, k, v)
    return ev


class _BookedFills:
    """Captures what `handle_fill` was asked to persist, without a database."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> "_BookedFills":
        import backend_app.backend.exchange_websocket_listener as mod

        booked = self

        class _Manager:
            def __init__(self, _session, tenant_id=None):
                self.tenant_id = tenant_id

            async def handle_fill(self, fill_data):
                booked.calls.append(dict(fill_data))

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        class _Pool:
            def get_session(self):
                return _Session()

        import backend_app.backend.transactional_execution_manager as tem
        import backend_app.core.database_pool as dbp
        monkeypatch.setattr(tem, "TransactionalExecutionManager", _Manager,
                            raising=False)
        monkeypatch.setattr(dbp, "db_pool", _Pool(), raising=False)
        monkeypatch.setattr(mod.ExchangeWebSocketListener, "_publish_fill_event",
                            lambda self, ev: _noop(), raising=False)
        return self


async def _noop():
    return None


@pytest.fixture
def listener() -> ExchangeWebSocketListener:
    """A listener instance that is NEVER started. Handlers are called directly."""
    obj = ExchangeWebSocketListener.__new__(ExchangeWebSocketListener)
    obj._running = False
    return obj


@pytest.fixture
def booked(monkeypatch) -> _BookedFills:
    return _BookedFills().install(monkeypatch)


def run(coro):
    return asyncio.run(coro)


def trade(tid: str | None, amount: float, price: float, ts: int) -> dict:
    t: dict = {"amount": amount, "price": price, "timestamp": ts, "fee": None}
    if tid is not None:
        t["id"] = tid
    return t


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 1 — two cumulative updates for one order
# ══════════════════════════════════════════════════════════════════════════════════════

def test_two_cumulative_updates_book_only_the_incremental_trades(listener, booked):
    """Each event carries per-trade detail; only the NEW trade is booked each time.

    This is the shape where the old code was already correct, and it is pinned so the
    fix did not break it: the cumulative `filled` on the order rises 1.0 -> 2.0 while
    exactly one fill row is written per event.
    """
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0, "trades": [trade("T1", 1.0, 100.0, 1)]},
    ))
    run(listener._handle_fill_event(
        _event(filled=Decimal("2.0")),
        {"id": ORDER_ID, "filled": 2.0, "trades": [trade("T2", 1.0, 200.0, 2)]},
    ))

    assert [c["fill_id"] for c in booked.calls] == ["T1", "T2"]
    # Incremental, NOT the cumulative 1.0 then 2.0.
    assert [c["quantity"] for c in booked.calls] == [1.0, 1.0]
    assert sum(c["quantity"] for c in booked.calls) == 2.0


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 2 — repeated delivery of the same update
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_replayed_event_reuses_the_same_trade_identity(listener, booked):
    """Idempotency. A redelivered event must present the SAME id to the persistence layer.

    The handler does not itself deduplicate — that is the persistence layer's job, keyed
    on `fill_id`. What this asserts is the precondition that makes dedup possible at all:
    the identity must be stable across replays. Under the old synthesised-trade path the
    identity was the order id, which was stable but **not distinct**, so dedup could not
    separate a replay from a new fill.
    """
    raw = {"id": ORDER_ID, "filled": 1.0, "trades": [trade("T1", 1.0, 100.0, 1)]}
    run(listener._handle_fill_event(_event(filled=Decimal("1.0")), raw))
    run(listener._handle_fill_event(_event(filled=Decimal("1.0")), raw))

    assert len(booked.calls) == 2, "the handler should forward both deliveries"
    assert booked.calls[0]["fill_id"] == booked.calls[1]["fill_id"] == "T1"
    assert booked.calls[0] == booked.calls[1], (
        "a replay produced a different payload, so the persistence layer could not "
        "recognise it as the same fill"
    )


def test_a_derived_identity_is_also_stable_across_replays(listener, booked):
    """Same property when the venue supplies no trade id."""
    raw = {"id": ORDER_ID, "filled": 1.0, "trades": [trade(None, 1.0, 100.0, 1)]}
    run(listener._handle_fill_event(_event(filled=Decimal("1.0")), raw))
    run(listener._handle_fill_event(_event(filled=Decimal("1.0")), raw))

    ids = [c["fill_id"] for c in booked.calls]
    assert len(ids) == 2
    assert ids[0] == ids[1]
    assert ids[0].startswith("derived-")
    assert ids[0] != "unknown"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 3 — multiple distinct partial fills
# ══════════════════════════════════════════════════════════════════════════════════════

def test_multiple_distinct_partial_fills_get_distinct_identities(listener, booked):
    """Three trades in one event -> three rows, three identities, incremental amounts."""
    run(listener._handle_fill_event(
        _event(filled=Decimal("2.5")),
        {"id": ORDER_ID, "filled": 2.5, "trades": [
            trade("T1", 1.0, 100.0, 1),
            trade("T2", 1.0, 200.0, 2),
            trade("T3", 0.5, 300.0, 3),
        ]},
    ))

    assert [c["fill_id"] for c in booked.calls] == ["T1", "T2", "T3"]
    assert [c["quantity"] for c in booked.calls] == [1.0, 1.0, 0.5]
    # Matches the cumulative figure ccxt would derive — the invariant that ties the two
    # conventions together.
    assert sum(c["quantity"] for c in booked.calls) == 2.5


def test_distinct_id_less_fills_do_not_collide_on_unknown(listener, booked):
    """The `trade.get("id", "unknown")` collision, pinned.

    Two genuinely different fills previously both booked under the literal `"unknown"`.
    """
    run(listener._handle_fill_event(
        _event(filled=Decimal("2.0")),
        {"id": ORDER_ID, "filled": 2.0, "trades": [
            trade(None, 1.0, 100.0, 1),
            trade(None, 1.0, 200.0, 2),
        ]},
    ))

    ids = [c["fill_id"] for c in booked.calls]
    assert len(ids) == 2
    assert ids[0] != ids[1], "two distinct fills collapsed onto one identity"
    assert "unknown" not in ids


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 4 — an event with missing trade details
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_cumulative_only_event_books_no_fill(listener, booked):
    """THE CORE FIX. No per-trade detail -> no fill row, and a loud warning.

    This is the shape that produced the double-booking: `trades` empty but
    `lastTradeTimestamp` set. The cumulative `filled` cannot be converted to a delta from
    the event alone, so nothing is booked. The order-level cumulative figure is still
    persisted by `_update_order_in_db`, which is where a cumulative number belongs.
    """
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0, "average": 100.0, "lastTradeTimestamp": 1},
    ))
    assert booked.calls == [], (
        "a cumulative-only event was booked as an incremental fill — the FILL-01 defect"
    )


def test_two_cumulative_only_events_never_double_book(listener, booked):
    """The money consequence, stated directly.

    Under the old code these two events booked 1.0 then 2.0 — a total of 3.0 for an
    order that had filled 2.0, and both rows carried the order id so neither could be
    deduplicated.
    """
    for total in (1.0, 2.0):
        run(listener._handle_fill_event(
            _event(filled=Decimal(str(total))),
            {"id": ORDER_ID, "filled": total, "average": 100.0,
             "lastTradeTimestamp": int(total)},
        ))
    assert booked.calls == []


def test_a_fill_without_a_price_is_refused(listener, booked):
    """`price` previously defaulted to 0. A fill that cannot be valued is not booked."""
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0,
         "trades": [{"id": "T1", "amount": 1.0, "timestamp": 1}]},
    ))
    assert booked.calls == []


def test_a_fill_without_an_amount_is_refused(listener, booked):
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0,
         "trades": [{"id": "T1", "price": 100.0, "timestamp": 1}]},
    ))
    assert booked.calls == []


@pytest.mark.parametrize("amount,price", [(0, 100.0), (1.0, 0), (-1.0, 100.0)])
def test_non_positive_quantities_or_prices_are_refused(listener, booked, amount, price):
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0,
         "trades": [trade("T1", amount, price, 1)]},
    ))
    assert booked.calls == []


def test_an_unparseable_quantity_is_refused_not_crashed(listener, booked):
    run(listener._handle_fill_event(
        _event(filled=Decimal("1.0")),
        {"id": ORDER_ID, "filled": 1.0,
         "trades": [trade("T1", "not-a-number", 100.0, 1)]},
    ))
    assert booked.calls == []


def test_a_good_fill_alongside_a_bad_one_is_still_booked(listener, booked):
    """One unusable trade must not discard the usable ones in the same event."""
    run(listener._handle_fill_event(
        _event(filled=Decimal("2.0")),
        {"id": ORDER_ID, "filled": 2.0, "trades": [
            {"id": "BAD", "amount": 1.0, "timestamp": 1},   # no price
            trade("GOOD", 1.0, 200.0, 2),
        ]},
    ))
    assert [c["fill_id"] for c in booked.calls] == ["GOOD"]


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 5 — out-of-order or decreasing cumulative quantities
# ══════════════════════════════════════════════════════════════════════════════════════

class _Order:
    def __init__(self, filled="0", remaining="0", avg="0"):
        self.order_id = ORDER_ID
        self.tenant_id = TENANT
        self.status = "open"
        self.filled_size = filled
        self.remaining_size = remaining
        self.avg_price = avg
        self.last_exchange_sync = None


def _install_db(monkeypatch, order: _Order):
    """A minimal async DB session yielding `order` from its query chain."""
    import contextlib

    import backend_app.backend.exchange_websocket_listener as mod

    class _Q:
        def filter(self, *_a, **_k):
            return self

        def first(self):
            return order

    class _DB:
        def query(self, *_a, **_k):
            return _Q()

        def commit(self):
            pass

    @contextlib.asynccontextmanager
    async def _sess():
        yield _DB()

    import backend_app.core.database_pool as dbp
    monkeypatch.setattr(dbp, "get_db_session", lambda: _sess(), raising=False)

    class _Redis:
        async def setex(self, *_a, **_k):
            return True

    monkeypatch.setattr(mod, "redis_manager", _Redis(), raising=False)
    return order


def test_a_decreasing_cumulative_figure_is_ignored(listener, monkeypatch):
    """A stale event reporting LESS than recorded must not shrink the fill."""
    order = _install_db(monkeypatch, _Order(filled="2.0", remaining="1.0", avg="150"))

    run(listener._update_order_in_db(
        _event(filled=Decimal("1.0"), remaining=Decimal("2.0"),
               average_price=Decimal("100"), status="open")
    ))

    assert order.filled_size == "2.0", "a stale event shrank the cumulative fill"
    assert order.remaining_size == "1.0"
    assert order.avg_price == "150"


def test_an_increasing_cumulative_figure_is_applied(listener, monkeypatch):
    order = _install_db(monkeypatch, _Order(filled="1.0", remaining="2.0", avg="100"))

    run(listener._update_order_in_db(
        _event(filled=Decimal("2.0"), remaining=Decimal("1.0"),
               average_price=Decimal("150"), status="open")
    ))

    assert order.filled_size == "2.0"
    assert order.remaining_size == "1.0"
    assert order.avg_price == "150"


def test_an_equal_cumulative_figure_is_applied_not_rejected(listener, monkeypatch):
    """A redelivery of the current state is not stale; it must not be treated as such."""
    order = _install_db(monkeypatch, _Order(filled="2.0", remaining="1.0", avg="150"))

    run(listener._update_order_in_db(
        _event(filled=Decimal("2.0"), remaining=Decimal("1.0"),
               average_price=Decimal("150"), status="closed")
    ))

    assert order.filled_size == "2.0"
    assert order.status == "closed"


def test_an_absent_filled_figure_does_not_erase_a_recorded_fill(listener, monkeypatch):
    """Absent is not zero. The old truthiness guard wrote "0" over a real fill."""
    order = _install_db(monkeypatch, _Order(filled="2.0", remaining="1.0", avg="150"))

    run(listener._update_order_in_db(
        _event(filled=None, remaining=None, average_price=None, status="open")
    ))

    assert order.filled_size == "2.0", "an event with no 'filled' erased the fill"
    assert order.avg_price == "150", "an event with no average erased the price"


def test_a_zero_filled_figure_is_honoured_when_nothing_is_recorded(listener, monkeypatch):
    """A genuine zero is still a measurement and must not be mistaken for absent."""
    order = _install_db(monkeypatch, _Order(filled="0", remaining="3.0", avg="0"))

    run(listener._update_order_in_db(
        _event(filled=Decimal("0"), remaining=Decimal("3.0"),
               average_price=None, status="open")
    ))

    assert order.filled_size == "0"
    assert order.remaining_size == "3.0"


def test_status_is_always_applied_even_when_quantities_are_stale(listener, monkeypatch):
    """A terminal status must land even if the event's quantities are behind.

    Otherwise an order cancelled at the venue would stay `open` locally purely because
    its cumulative figure had not advanced.
    """
    order = _install_db(monkeypatch, _Order(filled="2.0", remaining="1.0", avg="150"))

    run(listener._update_order_in_db(
        _event(filled=Decimal("1.0"), status="canceled")
    ))

    assert order.status == "canceled"
    assert order.filled_size == "2.0"


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 6 — restart / replay idempotency
# ══════════════════════════════════════════════════════════════════════════════════════

def test_replaying_a_whole_event_stream_is_idempotent(listener, booked):
    """Replaying the full stream after a restart yields the same identities and amounts.

    This is the property the persistence layer's `fill_id` dedup needs in order to work.
    What it does NOT establish is that the persistence layer actually deduplicates —
    `TransactionalExecutionManager.handle_fill` is substituted here, and its real
    uniqueness constraint is a separate concern owned by the ledger. Recorded rather
    than implied.
    """
    stream = [
        {"id": ORDER_ID, "filled": 1.0, "trades": [trade("T1", 1.0, 100.0, 1)]},
        {"id": ORDER_ID, "filled": 2.0, "trades": [trade("T2", 1.0, 200.0, 2)]},
        {"id": ORDER_ID, "filled": 2.5, "trades": [trade("T3", 0.5, 300.0, 3)]},
    ]

    for raw in stream:
        run(listener._handle_fill_event(_event(filled=Decimal(str(raw["filled"]))), raw))
    first_pass = list(booked.calls)

    booked.calls.clear()
    for raw in stream:
        run(listener._handle_fill_event(_event(filled=Decimal(str(raw["filled"]))), raw))
    second_pass = list(booked.calls)

    assert first_pass == second_pass
    assert [c["fill_id"] for c in first_pass] == ["T1", "T2", "T3"]
    assert sum(c["quantity"] for c in first_pass) == 2.5


def test_the_stable_identity_helper_prefers_the_venue_id():
    assert ExchangeWebSocketListener._stable_trade_id(
        ORDER_ID, {"id": "VENUE-T1", "amount": 1, "price": 2, "timestamp": 3}
    ) == "VENUE-T1"


@pytest.mark.parametrize("sentinel", [None, "", "unknown"])
def test_the_helper_derives_an_identity_when_the_venue_id_is_absent(sentinel):
    tid = ExchangeWebSocketListener._stable_trade_id(
        ORDER_ID, {"id": sentinel, "amount": 1, "price": 2, "timestamp": 3}
    )
    assert tid is not None and tid.startswith("derived-")


def test_the_helper_refuses_rather_than_inventing_an_identity():
    """No venue id and no invariants -> `None`, and the caller refuses to book."""
    assert ExchangeWebSocketListener._stable_trade_id(ORDER_ID, {"amount": 1}) is None
    assert ExchangeWebSocketListener._stable_trade_id(ORDER_ID, {}) is None


def test_the_order_id_is_never_used_as_a_trade_identity():
    """The specific prohibition from the brief, asserted directly."""
    derived = ExchangeWebSocketListener._stable_trade_id(
        ORDER_ID, {"amount": 1, "price": 2, "timestamp": 3}
    )
    assert derived != ORDER_ID
    assert ORDER_ID not in derived
