"""RT-16 — a streamed fill is EVIDENCE. The watchdog remains the only adjudicator.

WHY THIS FILE EXISTS, AND WHY IT HAD TO COME BEFORE THE CHAIN WAS WIRED
----------------------------------------------------------------------
`OrderEventHandler._handle_filled` is the far end of RT-16's private fill stream. It was
written, it resolved the right row (TRADE-12 fixed that), and nothing fed it. Wiring the
feeder without reading it first would have been catastrophic, for a reason that was in the
code and not in the finding:

**IT READ KEYS THE PAYLOAD DOES NOT HAVE.** It took
``data.get('filled_size', data.get('z', 0))``. The feeder,
``exchange_websocket_listener._publish_order_event``, sets ``data=event.raw_data`` -- the
**ccxt-normalised order dict**, whose cumulative-fill key is ``filled``. That module's own
line 355 reads ``order_data.get("filled")``, so the two halves of the same pipeline
disagreed on the key name. Neither ``filled_size`` nor Binance's raw ``z`` appears in a
ccxt order, so the expression resolved to ``0`` on **every** ccxt-sourced fill, and the
handler then:

* wrote ``filled_size = "0"`` over whatever real fill was recorded,
* wrote ``avg_price = "0"`` over the price it had been booked at,
* set ``status = COMPLETED``, which is **terminal** -- ``OrderWatchdog._find_open_orders``,
  ``_find_stale_orders`` and ``ExchangeReconciliation._get_active_orders`` all select only
  PENDING/EXECUTING, so the row left every reconciliation population,
* and computed ``fill_delta = 0``, so no position was credited either.

A row recorded as fully complete, with nothing filled, at a price of zero, and invisible
to every process that might have noticed. **Strictly worse than the honest gap RT-16
described.** ``_handle_partial`` carried the same defect plus one of its own: with the
fill resolving to ``0`` it wrote ``remaining_size = total_size`` -- the full order -- back
onto a partially filled order.

THE SECOND REASON, WHICH IS THE RECORDED DECISION
-------------------------------------------------
Even with the keys fixed, this handler must not set status. ``core/models/
execution_record.py`` states it: *"OrderWatchdog is the only owner of adjudication, and
introducing a second one would be worse than the defect."* And the feeder's own
``_update_order_in_db`` already records the venue's figures **with** the FILL-01 guards
(absent is not zero; a cumulative fill never moves backwards), while this handler ran on
the same event with none of them -- so the second write undid the first.

So: **the stream observes, the watchdog decides.** The latency win survives, because the
watchdog now adjudicates from evidence already in the row rather than asking the venue.

NO NETWORK, NO VENUE, NO REAL DATABASE. The execution row is a plain object and the
session is a recording double.
"""
from __future__ import annotations

import inspect
from datetime import datetime
from decimal import Decimal

import pytest

from backend_app.backend import event_router as er
from backend_app.backend.event_listener import EventType, ExchangeEvent
from backend_app.backend.event_router import OrderEventHandler
from backend_app.core.models.execution_record import ExecutionStatus


class _Row:
    """An execution record with only the fields these handlers touch."""

    def __init__(self, **kw):
        self.execution_id = kw.get("execution_id", "exec-1")
        self.tenant_id = kw.get("tenant_id", "tenant-1")
        self.size = kw.get("size", "1.0")
        self.filled_size = kw.get("filled_size")
        self.remaining_size = kw.get("remaining_size")
        self.avg_price = kw.get("avg_price")
        self.status = kw.get("status", ExecutionStatus.EXECUTING)
        self.filled_at = kw.get("filled_at")
        self.last_exchange_sync = kw.get("last_exchange_sync")
        self.exchange_status = kw.get("exchange_status")


class _Session:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _handler():
    h = OrderEventHandler.__new__(OrderEventHandler)
    h.db = _Session()
    return h


def _event(data, event_type=EventType.ORDER_FILLED):
    return ExchangeEvent(
        event_id="evt-1",
        event_type=event_type,
        exchange_id="binance",
        tenant_id="tenant-1",
        order_id="V-1",
        symbol="BTC/USDT",
        data=data,
        exchange_timestamp=datetime.utcnow(),
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  1 — THE PAYLOAD SHAPE. The defect that made wiring the chain destructive.
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_ccxt_order_payload_is_read_correctly():
    """THE headline. This is the shape RT-16's feeder actually publishes.

    `_publish_order_event` sets `data=event.raw_data`, the ccxt order dict. Its
    cumulative-fill key is `filled`. Before this fix the handler looked for
    `filled_size` and `z`, found neither, and recorded a zero.
    """
    row = _Row(filled_size="0.2", avg_price="40000")
    handler = _handler()

    await handler._handle_filled(
        _event({"filled": 0.5, "average": 42000.0, "price": 41000.0, "status": "closed"}),
        row,
    )

    assert row.filled_size == "0.5", (
        "the ccxt `filled` key was not read -- this is the defect that would have "
        "overwritten a real fill with zero"
    )
    # `average` wins over `price`: for a cumulative fill the average IS the booking price,
    # while `price` is the order's limit and coincides only by accident.
    assert row.avg_price == "42000.0"
    assert row.remaining_size == "0.5"
    assert row.exchange_status == "closed"
    assert handler.db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,expected_fill",
    [
        ({"filled": "0.5"}, "0.5"),                 # ccxt
        ({"filled_size": "0.5"}, "0.5"),            # this platform's own producers
        ({"z": "0.5"}, "0.5"),                      # raw Binance user-data stream
    ],
)
async def test_all_three_producer_shapes_are_understood(payload, expected_fill):
    """Three producers reach this handler and they do not agree on key names.

    Reading one shape and silently zeroing the others is the defect; this pins all three.
    """
    row = _Row()
    await _handler()._handle_filled(_event(payload), row)
    assert row.filled_size == expected_fill


# ══════════════════════════════════════════════════════════════════════════════════════
#  2 — ABSENT IS NOT ZERO
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_an_unreported_fill_leaves_the_recorded_one_alone():
    """The destructive case, in isolation.

    An event that says nothing about the cumulative fill must not be read as "zero
    filled". The row already holds a real figure and it has to survive.
    """
    row = _Row(filled_size="0.8", avg_price="40000", remaining_size="0.2")

    await _handler()._handle_filled(_event({"symbol": "BTC/USDT", "status": "open"}), row)

    assert row.filled_size == "0.8", "a silent event erased a recorded fill"
    assert row.avg_price == "40000", "a silent event erased the recorded price"
    assert row.remaining_size == "0.2"
    # The sync time still moves: "the venue answered, at this time" is itself evidence.
    assert row.last_exchange_sync is not None


@pytest.mark.asyncio
async def test_a_reported_zero_is_recorded_as_a_real_statement():
    """The other half of the distinction, and it must not be collapsed.

    `filled: 0` is the venue saying "nothing has filled yet" -- a fact worth recording --
    whereas an absent key is the venue not saying. A truthiness check would treat both as
    nothing, which is how the two cases got conflated in the first place.
    """
    row = _Row(filled_size=None)
    await _handler()._handle_filled(_event({"filled": 0, "status": "open"}), row)
    assert row.filled_size == "0"


@pytest.mark.asyncio
async def test_an_unparseable_figure_is_treated_as_unreported():
    """Junk is not zero either. It must leave the recorded figure standing."""
    row = _Row(filled_size="0.3")
    await _handler()._handle_filled(_event({"filled": "not-a-number"}), row)
    assert row.filled_size == "0.3"


# ══════════════════════════════════════════════════════════════════════════════════════
#  3 — CUMULATIVE FILLS DO NOT SHRINK
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_stale_out_of_order_event_cannot_move_a_fill_backwards():
    """`filled` is cumulative, so a smaller figure is a late or reordered event.

    The feeder's `_update_order_in_db` already enforces this (FILL-01). This handler ran
    on the same event with no such guard, so the unguarded write undid the guarded one.
    """
    row = _Row(filled_size="0.9", avg_price="42000")

    await _handler()._handle_filled(_event({"filled": "0.4", "average": "1"}), row)

    assert row.filled_size == "0.9", "a stale event moved the cumulative fill backwards"
    assert row.avg_price == "42000", "a stale event rewrote the price"


@pytest.mark.asyncio
async def test_an_equal_fill_is_accepted_so_a_redelivery_refreshes_evidence():
    """Equal is not stale. A venue redelivering the same total is still a fresh answer."""
    row = _Row(filled_size="0.5", avg_price="40000")
    await _handler()._handle_filled(_event({"filled": "0.5", "average": "41000"}), row)
    assert row.filled_size == "0.5"
    assert row.avg_price == "41000"


# ══════════════════════════════════════════════════════════════════════════════════════
#  4 — THE RECORDED DECISION: THIS IS NOT AN ADJUDICATOR
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_confirmed_fill_does_not_change_the_execution_status():
    """Option (a), asserted on the row.

    A venue-confirmed full fill is the strongest possible evidence, and it STILL does not
    transition the execution here. COMPLETED is terminal -- it drops the row out of every
    watchdog and reconciliation population -- so a second owner setting it races the
    watchdog on real money.
    """
    row = _Row(status=ExecutionStatus.EXECUTING, size="1.0")

    await _handler()._handle_filled(
        _event({"filled": "1.0", "average": "42000", "status": "closed"}), row
    )

    assert row.status is ExecutionStatus.EXECUTING, (
        "the fill stream adjudicated the execution; OrderWatchdog is the only owner"
    )
    assert row.filled_at is None, "the stream stamped a terminal timestamp"
    # The evidence IS recorded -- that is the point of the stream.
    assert row.filled_size == "1.0"
    assert row.last_exchange_sync is not None


@pytest.mark.asyncio
async def test_a_partial_fill_does_not_change_the_status_either():
    row = _Row(status=ExecutionStatus.EXECUTING, size="1.0")
    await _handler()._handle_partial(
        _event({"filled": "0.4", "average": "42000"}, EventType.ORDER_PARTIAL), row
    )
    assert row.status is ExecutionStatus.EXECUTING
    assert row.filled_size == "0.4"
    assert row.remaining_size == "0.6"


@pytest.mark.asyncio
async def test_a_partial_fill_does_not_write_back_the_full_order_as_remaining():
    """`_handle_partial`'s own extra defect.

    With the fill resolving to 0 it computed `remaining = total - 0` and wrote the FULL
    order size back onto a partially filled order.
    """
    row = _Row(size="2.0", filled_size="0.5", remaining_size="1.5")

    await _handler()._handle_partial(
        _event({"symbol": "BTC/USDT"}, EventType.ORDER_PARTIAL), row
    )

    assert row.remaining_size == "1.5", (
        "an event reporting no fill rewrote remaining_size as the whole order"
    )


def test_the_module_does_not_import_the_fill_transitions():
    """Structural, and it is the guard against this being undone by a helpful edit.

    `transition_to_filled` / `transition_to_partial_fill` are what made this module a
    second adjudicator. Re-importing them is the first step of reintroducing that, so the
    absence is asserted rather than left to a comment.
    """
    source = inspect.getsource(er)
    import_block = source.split("logger = logging.getLogger")[0]
    code_lines = [
        line for line in import_block.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    assert "transition_to_filled" not in code, (
        "event_router imports transition_to_filled again -- that function is how this "
        "module becomes a second owner of adjudication"
    )
    assert "transition_to_partial_fill" not in code
    # The two that remain are deliberate: _handle_cancelled / _handle_rejected are
    # unchanged, so removing these would be a different change.
    assert "transition_to_cancelled" in code
    assert "transition_to_failed" in code


def test_no_fill_handler_assigns_a_terminal_status():
    """Reads the handlers' own source, so a future edit cannot quietly re-add it."""
    for method in (OrderEventHandler._handle_filled,
                   OrderEventHandler._handle_partial,
                   OrderEventHandler._record_venue_fill_evidence):
        source = inspect.getsource(method)
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        # Strip the docstring, which legitimately discusses the old behaviour.
        doc = method.__doc__ or ""
        code = code.replace(doc, "")
        assert "execution.status =" not in code, (
            f"{method.__name__} assigns execution.status; the fill stream must observe, "
            f"not adjudicate"
        )
        assert "ExecutionStatus.COMPLETED" not in code, (
            f"{method.__name__} references COMPLETED, which is terminal"
        )


# ══════════════════════════════════════════════════════════════════════════════════════
#  5 — THE HELPERS, directly
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "data,expected",
    [
        ({"filled": "1.5"}, Decimal("1.5")),
        ({"filled_size": "2"}, Decimal("2")),
        ({"z": "3"}, Decimal("3")),
        ({"filled": 0}, Decimal("0")),
        ({}, None),
        ({"filled": None}, None),
        ({"filled": "junk"}, None),
        # Precedence: ccxt's key wins, because it is the feeder RT-16 wires.
        ({"filled": "1", "filled_size": "9", "z": "9"}, Decimal("1")),
    ],
)
def test_cumulative_fill_extraction(data, expected):
    assert er._venue_cumulative_fill(data) == expected


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"average": "42000"}, Decimal("42000")),
        ({"price": "41000"}, Decimal("41000")),
        ({"L": "43000"}, Decimal("43000")),
        # `average` beats `price`: for a cumulative fill the average is the booking price,
        # while `price` is the order's limit and matches only by coincidence.
        ({"average": "42000", "price": "41000"}, Decimal("42000")),
        ({}, None),
    ],
)
def test_fill_price_extraction(data, expected):
    assert er._venue_fill_price(data) == expected
